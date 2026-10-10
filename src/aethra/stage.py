"""Window stacking on the stage output, and the compositor process.

With the compositor engine (the default) the stage shows one window: the
compositor's, which draws every deck as a layer (:class:`CompositorStage`
starts it, watches it and restarts it). With the window engine every deck is
an mpv process with its own window, stacked as described below.

On Windows the controller owns a black, topmost "backdrop" window that covers
the stage monitor for the whole session, so the desktop can never show - even
if every mpv process dies. Deck windows are stacked with ``SetWindowPos``:

* a window becomes topmost the first time it is raised to the very top, in a
  single call, so it can never flash above the current picture;
* after that, the order within the topmost band is arranged with
  insert-after calls: visible deck, filler deck (instant fallback if the live
  deck dies), backdrop, then the hidden decks;
* every call uses ``SWP_ASYNCWINDOWPOS`` so a hung mpv cannot block the
  controller; the order is re-verified periodically and repaired if needed.

Elsewhere (development on Linux) the generic stage just toggles mpv's
``ontop`` property, and blackout falls back to an OSD overlay.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from aethra.config import StageConfig
from aethra.eventlog import EventLog
from aethra.ipc import MpvDisconnected, MpvError, MpvIpc
from aethra.platform import IS_WINDOWS

if TYPE_CHECKING:
    from aethra.deck import Deck

BACKDROP = "backdrop"


@dataclass(frozen=True)
class Monitor:
    index: int
    name: str
    left: int
    top: int
    width: int
    height: int
    primary: bool

    def __str__(self) -> str:
        flag = " (primary)" if self.primary else ""
        return f"#{self.index} {self.name} {self.width}x{self.height} at {self.left},{self.top}{flag}"


def list_monitors() -> list[Monitor]:
    if not IS_WINDOWS:
        return []
    from aethra import winapi

    result = []
    for i, (name, r, primary) in enumerate(winapi.monitors()):
        result.append(Monitor(i, name, r.left, r.top, r.right - r.left, r.bottom - r.top, primary))
    return result


def choose_monitor(cfg: StageConfig, monitors: list[Monitor]) -> Monitor | None:
    if not monitors:
        return None
    if cfg.screen_name:
        for m in monitors:
            if m.name.lower() == cfg.screen_name.lower():
                return m
        return None
    if cfg.screen is not None:
        return monitors[cfg.screen] if cfg.screen < len(monitors) else None
    non_primary = [m for m in monitors if not m.primary]
    return non_primary[0] if non_primary else monitors[0]


def stage_monitor(cfg: StageConfig, monitors: list[Monitor]) -> Monitor | None:
    """The screen the show plays on.

    Configured (``stage.screen`` / ``stage.screen_name``): that screen. Automatic:
    the first screen that is not the main display - and None while there is no
    such screen, so the show never covers the operator's main display (the
    console and the control panel) because the projector was not detected yet.
    """
    if cfg.screen_name or cfg.screen is not None:
        return choose_monitor(cfg, monitors)
    non_primary = [m for m in monitors if not m.primary]
    return non_primary[0] if non_primary else None


class Stage(Protocol):
    has_backdrop: bool

    def start(self) -> None: ...
    def register(self, deck: "Deck") -> None: ...
    def apply(self, order: list[str]) -> None: ...
    def maintain(self) -> None: ...
    def close(self) -> None: ...
    async def quit(self) -> None: ...


class NullStage:
    """No window control (tests, headless runs)."""

    has_backdrop = False

    def __init__(self) -> None:
        self.orders: list[list[str]] = []

    def start(self) -> None:
        pass

    def register(self, deck: "Deck") -> None:
        pass

    def apply(self, order: list[str]) -> None:
        self.orders.append(list(order))

    def maintain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def quit(self) -> None:
        pass


class OntopStage:
    """Portable fallback: the visible deck gets mpv's ``ontop``, the others lose it."""

    has_backdrop = False

    def __init__(self) -> None:
        self.decks: dict[str, Deck] = {}
        self._top: str | None = None

    def start(self) -> None:
        pass

    def register(self, deck: "Deck") -> None:
        self.decks[deck.name] = deck
        if deck.ipc is not None:
            deck.ipc.send("set_property", "ontop", deck.name == self._top)

    def apply(self, order: list[str]) -> None:
        top = next((name for name in order if name != BACKDROP), None)
        self._top = top
        if top in self.decks and self.decks[top].ipc is not None:
            self.decks[top].ipc.send("set_property", "ontop", True)
        for name, deck in self.decks.items():
            if name != top and deck.ipc is not None:
                deck.ipc.send("set_property", "ontop", False)

    def maintain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def quit(self) -> None:
        pass


class Win32Stage:
    has_backdrop = True

    def __init__(
        self,
        cfg: StageConfig,
        log: EventLog,
        on_display_change: Callable[[], None] | None = None,
        auto_screen: bool = False,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.on_display_change = on_display_change
        # Automatic stage screen: after a display change it is again "the screen
        # that is not the main display", whatever number Windows gives it now.
        self.auto_screen = auto_screen
        self.decks: dict[str, Deck] = {}
        self.monitor: Monitor | None = None
        self._order: list[str] = []
        self._generation = 0
        self._backdrop_shown = False
        self._last_repair = 0.0
        self._hwnd: int | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._closing = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wndproc = None  # keep the ctypes callback alive

    # -- backdrop window ---------------------------------------------------------

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.monitor = choose_monitor(self.cfg, list_monitors())
        if self.monitor is None:
            self.log.error("stage_monitor", "configured stage screen not found; the backdrop covers the primary screen")
        thread = threading.Thread(target=self._run, name="stage-backdrop", daemon=True)
        thread.start()
        if not self._ready.wait(5.0):
            self.log.error("stage_backdrop", "backdrop window did not start")

    def _rect(self) -> tuple[int, int, int, int]:
        m = self.monitor
        if m is None:
            return (0, 0, self.cfg.width, self.cfg.height)
        return (m.left, m.top, m.width, m.height)

    def _run(self) -> None:
        import ctypes

        from aethra import winapi as w

        def wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            try:
                if msg == w.WM_SETCURSOR:
                    w.SetCursor(None)
                    return 1
                if msg == w.WM_MOUSEACTIVATE:
                    return w.MA_NOACTIVATE
                if msg == w.WM_DISPLAYCHANGE and self._loop is not None and self.on_display_change is not None:
                    self._loop.call_soon_threadsafe(self.on_display_change)
                if msg == w.WM_CLOSE and not self._closing:
                    return 0  # nobody closes the backdrop but us
                if msg == w.WM_DESTROY:
                    w.PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return w.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = w.WNDPROC(wndproc)
        self._thread_id = w.GetCurrentThreadId()
        hinst = w.GetModuleHandleW(None)
        wc = w.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(w.WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hbrBackground = w.GetStockObject(w.BLACK_BRUSH)
        wc.lpszClassName = "AethraBackdrop"
        w.RegisterClassExW(ctypes.byref(wc))
        x, y, cx, cy = self._rect()
        # Created hidden: the first apply() shows it at its place in the order,
        # so a restarted controller never covers a deck that is already playing.
        hwnd = w.CreateWindowExW(
            w.WS_EX_TOPMOST | w.WS_EX_TOOLWINDOW | w.WS_EX_NOACTIVATE,
            "AethraBackdrop", "Aethra backdrop", w.WS_POPUP,
            x, y, cx, cy, None, None, hinst, None,
        )
        if not hwnd:
            self.log.error("stage_backdrop", f"CreateWindowEx failed (error {ctypes.get_last_error()})")
            self._ready.set()
            return
        self._hwnd = hwnd
        self._ready.set()
        msg = w.MSG()
        while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w.TranslateMessage(ctypes.byref(msg))
            w.DispatchMessageW(ctypes.byref(msg))
        w.UnregisterClassW("AethraBackdrop", hinst)

    def reposition(self) -> bool:
        """After a display change: find the stage monitor again and bring every
        window back onto it (Windows moves windows off a disconnected monitor,
        and does not move them back). Returns False while the monitor is missing.
        """
        from aethra import winapi as w

        monitors = list_monitors()
        if self.auto_screen:
            found = stage_monitor(StageConfig(), monitors)
        else:
            found = choose_monitor(self.cfg, monitors)
        if found is None:
            return False  # keep everything where it is until the monitor is back
        self.monitor = found
        x, y, cx, cy = self._rect()
        flags = w.SWP_NOZORDER | w.SWP_NOACTIVATE | w.SWP_ASYNCWINDOWPOS
        handles = [self._hwnd] + [d.hwnd for d in self.decks.values() if d.up and d.hwnd]
        for hwnd in handles:
            if not hwnd:
                continue
            r = w.RECT()
            if w.GetWindowRect(hwnd, ctypes.byref(r)) and (r.left, r.top, r.right - r.left, r.bottom - r.top) == (x, y, cx, cy):
                continue
            w.SetWindowPos(hwnd, None, x, y, cx, cy, flags)
        return True

    def close(self) -> None:
        from aethra import winapi as w

        self._closing = True
        if self._hwnd is not None:
            w.PostMessageW(self._hwnd, w.WM_CLOSE, 0, 0)
        self._hwnd = None

    async def quit(self) -> None:
        pass

    # -- stacking ----------------------------------------------------------------

    def register(self, deck: "Deck") -> None:
        self.decks[deck.name] = deck
        if self._order:
            self.apply(self._order)

    def _hwnd_of(self, name: str) -> int | None:
        if name == BACKDROP:
            return self._hwnd
        deck = self.decks.get(name)
        return deck.hwnd if deck is not None and deck.up else None

    def _hwnds(self, order: list[str]) -> list[int]:
        return [h for h in (self._hwnd_of(name) for name in order) if h]

    def _flags(self, hwnd: int) -> int:
        from aethra import winapi as w

        flags = w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE | w.SWP_NOOWNERZORDER | w.SWP_ASYNCWINDOWPOS
        if hwnd == self._hwnd and not self._backdrop_shown:
            self._backdrop_shown = True
            flags |= w.SWP_SHOWWINDOW
        return flags

    def apply(self, order: list[str]) -> None:
        """Raise the first window, then (once it is on top) arrange the rest.

        The calls go to windows owned by different threads, so their order of
        execution is not guaranteed: arranging the lower windows before the new
        top is in place could briefly put the backdrop on top (a black flash).
        """
        from aethra import winapi as w

        self._order = list(order)
        self._generation += 1
        hwnds = self._hwnds(order)
        if not hwnds:
            return
        w.SetWindowPos(hwnds[0], w.HWND_TOPMOST, 0, 0, 0, 0, self._flags(hwnds[0]))
        if self._loop is not None:
            self._loop.call_later(0.005, self._arrange_rest, self._generation, 0)
        else:
            self._arrange_rest(self._generation, 0)

    def _arrange_rest(self, generation: int, attempt: int) -> None:
        from aethra import winapi as w

        if generation != self._generation:
            return  # a newer order was applied meanwhile
        hwnds = self._hwnds(self._order)
        if not hwnds:
            return
        actual = w.z_order(set(hwnds))
        if actual and actual[0] != hwnds[0] and attempt < 40 and self._loop is not None:
            self._loop.call_later(0.005, self._arrange_rest, generation, attempt + 1)
            return
        # Inserting a window directly below a topmost window keeps it in the
        # topmost band, below the window above it: nothing can jump over the top.
        prev = hwnds[0]
        for hwnd in hwnds[1:]:
            w.SetWindowPos(hwnd, prev, 0, 0, 0, 0, self._flags(hwnd))
            prev = hwnd
        if self._loop is not None:
            self._loop.call_later(0.08, self._verify, generation)

    def _in_order(self) -> bool:
        from aethra import winapi as w

        hwnds = self._hwnds(self._order)
        if not hwnds:
            return True
        if not w.is_topmost(hwnds[0]):
            return False
        return w.z_order(set(hwnds)) == hwnds

    def _verify(self, generation: int) -> None:
        if generation == self._generation and not self._in_order():
            self._repair("stacking order not as intended")

    def _repair(self, why: str) -> None:
        from aethra.clock import now

        t = now()
        if t - self._last_repair > 5.0:
            self.log.warn("stage", f"{why}; restoring it")
        self._last_repair = t
        self.apply(self._order)

    def maintain(self) -> None:
        from aethra import winapi as w

        if not self._order:
            return
        # "Show desktop" (Win+D) or "minimize all" (Win+M) minimise our windows
        # too: restore them without activating them.
        restored = False
        for hwnd in [self._hwnd] + [d.hwnd for d in self.decks.values() if d.up]:
            if hwnd and w.IsIconic(hwnd):
                w.ShowWindowAsync(hwnd, w.SW_SHOWNOACTIVATE)
                restored = True
        if restored:
            self._repair("stage windows were minimised (Win+D / Win+M?)")
        elif not self._in_order():
            self._repair("stacking order changed")


# --------------------------------------------------------------------------
# Compositor engine


class CompositorError(RuntimeError):
    pass


class LayerProcess:
    """Stands in for the mpv process of a deck that is a compositor layer."""

    in_job = True

    def __init__(self, stage: "CompositorStage", name: str) -> None:
        self.stage = stage
        self.name = name

    @property
    def pid(self) -> int | None:
        return self.stage.pid

    def alive(self) -> bool:
        return self.name in self.stage.layers

    def kill(self) -> None:
        self.stage.destroy_layer(self.name)

    def reap(self, timeout: float = 0.0) -> int | None:
        return None


class _CompositorWindow:
    """The compositor's window, stacked by :class:`Win32Stage` like a deck's."""

    name = "compositor"

    def __init__(self) -> None:
        self.hwnd: int | None = None

    @property
    def up(self) -> bool:
        return self.hwnd is not None


class CompositorStage:
    """Starts the compositor process, talks to it, and restarts it if it dies
    or stops answering. On Windows the backdrop window stays below the
    compositor's window, so a compositor restart shows black, never the desktop.
    """

    PING_INTERVAL = 0.5
    PING_TIMEOUT = 1.0
    PING_FAILURES = 3  # this many unanswered pings in a row = hung

    def __init__(
        self,
        cfg: StageConfig,
        log: EventLog,
        address: str,
        rect: tuple[int, int, int, int],
        *,
        python: str = sys.executable,
        libmpv: str | None = None,
        log_file: Path | None = None,
        backdrop: Win32Stage | None = None,
        startup_timeout: float = 20.0,
        priority: str = "above_normal",
        topmost: bool = True,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.address = address
        self.rect = rect
        self.python = python
        self.libmpv = libmpv
        self.log_file = log_file
        self.backdrop = backdrop
        self.startup_timeout = startup_timeout
        self.priority = priority
        self.topmost = topmost
        self.ipc: MpvIpc | None = None
        self.proc: Any = None  # MpvProcess
        self.pid: int | None = None
        self.info: dict[str, Any] = {}
        self.layers: set[str] = set()
        self.restarts = 0
        self.on_key: Callable[[str], None] | None = None
        self.on_restart: Callable[[], None] | None = None
        self._window = _CompositorWindow()
        self._ready: asyncio.Event | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def has_backdrop(self) -> bool:
        return self.backdrop is not None

    @property
    def size(self) -> tuple[int, int]:
        return self.cfg.width, self.cfg.height

    def _ready_event(self) -> asyncio.Event:
        if self._ready is None:
            self._ready = asyncio.Event()
        return self._ready

    # -- process ---------------------------------------------------------------

    async def open(self) -> None:
        """Reconnect to the compositor a previous controller left running, or start one."""
        if await self._connect(0.3):
            self.log.info("compositor", f"reconnected to the running compositor (pid {self.pid}, layers {', '.join(sorted(self.layers)) or 'none'})")
        else:
            await self._spawn()
        self._ready_event().set()

    async def _connect(self, timeout: float, hello_timeout: float = 2.0) -> bool:
        ipc = MpvIpc(self.address, name="compositor", on_event=self._on_event, on_error=self._on_ipc_error)
        try:
            await ipc.connect(timeout=timeout)
        except MpvDisconnected:
            return False
        try:
            info = await ipc.request({"name": "hello"}, hello_timeout)
        except MpvError:
            await ipc.close()
            return False
        if not isinstance(info, dict):
            await ipc.close()
            return False
        if info.get("protocol") != 1 or tuple(info.get("size") or ()) != self.size:
            # Another version, or another stage size: replace it.
            self.log.warn("compositor", f"the running compositor does not match this show ({info.get('size')}); restarting it")
            ipc.send_named("quit")
            await ipc.close()
            await self._wait_gone(info.get("pid"), 3.0)
            return False
        self.ipc = ipc
        self.info = info
        self.pid = info.get("pid") if isinstance(info.get("pid"), int) else None
        self.layers = {str(n) for n in info.get("layers") or ()}
        hwnd = info.get("hwnd")
        self._window.hwnd = hwnd if isinstance(hwnd, int) and hwnd else None
        return True

    async def _wait_gone(self, pid: Any, timeout: float) -> None:
        from aethra.clock import now
        from aethra.mpvproc import MpvProcess

        if not isinstance(pid, int):
            return
        proc = MpvProcess(pid=pid)
        deadline = now() + timeout
        while proc.alive() and now() < deadline:
            await asyncio.sleep(0.05)
        if proc.alive():
            proc.kill()

    async def _spawn(self) -> None:
        from aethra.clock import now
        from aethra.mpvproc import MpvProcess

        x, y, w, h = self.rect
        args = [
            self.python, "-X", "utf8", "-m", "aethra.compositor",
            "--control", self.address, "--rect", f"{x},{y},{w},{h}", "--size", f"{self.cfg.width},{self.cfg.height}",
        ]
        if self.libmpv:
            args += ["--libmpv", self.libmpv]
        if self.log_file is not None:
            args += ["--log", str(self.log_file)]
        if not self.topmost:
            args.append("--no-topmost")
        env = dict(os.environ)
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if not IS_WINDOWS:
            Path(self.address).unlink(missing_ok=True)
        t0 = now()
        try:
            self.proc = MpvProcess.spawn(args, self.priority, output=self.log_file, env=env)
        except OSError as exc:
            raise CompositorError(f"cannot start the compositor: {exc}") from exc
        deadline = t0 + self.startup_timeout
        while True:
            if await self._connect(0.5, hello_timeout=max(1.0, deadline - now())):
                break
            if not self.proc.alive():
                code = self.proc.reap(0.1)
                raise CompositorError(f"the compositor exited during startup (code {code}){self._log_tail()}")
            if now() > deadline:
                self.proc.kill()
                raise CompositorError(f"the compositor did not start within {self.startup_timeout:.0f} s{self._log_tail()}")
        self.log.info(
            "compositor",
            f"compositor running (pid {self.pid}, {(now() - t0) * 1000:.0f} ms): {self.info.get('gl')}, libmpv API {self.info.get('libmpv')}",
            pid=self.pid,
        )

    def _log_tail(self) -> str:
        if self.log_file is None:
            return ""
        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return (": " + " | ".join(lines[-3:])) if lines else ""

    async def _restart(self, reason: str) -> None:
        from aethra.mpvproc import MpvProcess

        self.log.error("compositor_down", f"compositor: {reason}; restarting it")
        self._ready_event().clear()
        ipc, self.ipc = self.ipc, None
        if ipc is not None:
            await ipc.close()
        proc = self.proc if self.proc is not None else (MpvProcess(pid=self.pid) if self.pid else None)
        if proc is not None and proc.alive():
            proc.kill()
            await self._wait_gone(self.pid, 2.0)
        self.layers.clear()
        self._window.hwnd = None
        self.pid = None
        delay = 0.5
        while not self._closing:
            try:
                await self._spawn()
                break
            except CompositorError as exc:
                self.log.error("compositor_down", str(exc))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
        if self._closing:
            return
        self.restarts += 1
        self._ready_event().set()
        self.apply([])
        if self.on_restart is not None:
            self.on_restart()

    async def _watch(self) -> None:
        failures = 0
        while not self._closing:
            await asyncio.sleep(self.PING_INTERVAL)
            ipc = self.ipc
            if ipc is None or not ipc.connected:
                await self._restart("connection lost (the compositor stopped)")
                failures = 0
                continue
            try:
                await ipc.request({"name": "ping"}, self.PING_TIMEOUT)
                failures = 0
            except MpvError:
                failures += 1
                if failures >= self.PING_FAILURES:
                    await self._restart(f"no answer for {failures * self.PING_INTERVAL + self.PING_TIMEOUT:.1f} s (hung)")
                    failures = 0

    def _on_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "layer_down":
            self.layers.discard(str(msg.get("layer")))
        elif event == "key" and self.on_key is not None:
            self.on_key(str(msg.get("key")))

    def _on_ipc_error(self, what: str, exc: BaseException) -> None:
        if not isinstance(exc, MpvDisconnected):
            self.log.warn("compositor", f"{what}: {exc}")

    # -- layers ------------------------------------------------------------------

    async def create_layer(self, name: str, options: dict[str, str]) -> LayerProcess:
        try:
            await asyncio.wait_for(self._ready_event().wait(), timeout=self.startup_timeout + 5.0)
        except asyncio.TimeoutError as exc:
            raise CompositorError("the compositor is not running") from exc
        ipc = self.ipc
        if ipc is None:
            raise CompositorError("the compositor is not running")
        try:
            reply = await ipc.request({"name": "create_layer", "layer": name, "options": options}, 10.0)
        except MpvError as exc:
            raise CompositorError(f"layer {name}: {exc}") from exc
        self.layers.add(name)
        dropped = (reply or {}).get("dropped") if isinstance(reply, dict) else None
        if dropped:
            self.log.debug("mpv_options", f"layer {name}: options this libmpv does not take: {', '.join(dropped)}")
        return LayerProcess(self, name)

    def layer_process(self, name: str) -> LayerProcess:
        return LayerProcess(self, name)

    def destroy_layer(self, name: str) -> None:
        self.layers.discard(name)
        self.send({"name": "destroy_layer", "layer": name})

    def send(self, command: dict[str, Any]) -> None:
        """Fire and forget; a lost command is repaired by the restart logic."""
        if self.ipc is not None and self.ipc.connected:
            self.ipc._detach(self.ipc.request(command))

    async def request(self, command: dict[str, Any], timeout: float = 2.0) -> Any:
        if self.ipc is None:
            raise MpvDisconnected("compositor: not connected")
        return await self.ipc.request(command, timeout)

    # -- Stage protocol ------------------------------------------------------------

    def start(self) -> None:
        if self.backdrop is not None:
            self.backdrop.start()
            self.backdrop.register(self._window)  # type: ignore[arg-type]
        self.apply([])
        self._heartbeat = asyncio.get_running_loop().create_task(self._watch(), name="compositor-watch")

    def register(self, deck: "Deck") -> None:
        pass  # layers have no windows

    def apply(self, order: list[str]) -> None:
        if self.backdrop is not None:
            self.backdrop.apply([self._window.name, BACKDROP])

    def maintain(self) -> None:
        if self.backdrop is not None:
            self.backdrop.maintain()

    def close(self) -> None:
        """Stop watching; the compositor keeps running (see :meth:`quit`)."""
        self._closing = True
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        if self.ipc is not None:
            ipc, self.ipc = self.ipc, None
            asyncio.get_running_loop().create_task(ipc.close())
        if self.backdrop is not None:
            self.backdrop.close()

    async def quit(self) -> None:
        """End of the show: the compositor exits (after the decks have quit)."""
        self._closing = True
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        ipc, self.ipc = self.ipc, None
        if ipc is not None and ipc.connected:
            try:
                await ipc.request({"name": "quit"}, 2.0)
            except MpvError:
                pass
            await ipc.close()
        if self.proc is not None:
            from aethra.clock import now

            deadline = now() + 3.0
            while self.proc.alive() and now() < deadline:
                await asyncio.sleep(0.05)
            if self.proc.alive():
                self.proc.kill()
            self.proc.reap(0.5)
        else:
            await self._wait_gone(self.pid, 3.0)
