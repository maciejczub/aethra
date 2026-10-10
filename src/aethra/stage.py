"""Window stacking on the stage output.

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
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aethra.config import StageConfig
from aethra.eventlog import EventLog
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


class Stage(Protocol):
    has_backdrop: bool

    def start(self) -> None: ...
    def register(self, deck: "Deck") -> None: ...
    def apply(self, order: list[str]) -> None: ...
    def maintain(self) -> None: ...
    def close(self) -> None: ...


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


class Win32Stage:
    has_backdrop = True

    def __init__(self, cfg: StageConfig, log: EventLog, on_display_change: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self.log = log
        self.on_display_change = on_display_change
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

        found = choose_monitor(self.cfg, list_monitors())
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
