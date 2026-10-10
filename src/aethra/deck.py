"""A deck is one player: a layer of the compositor (the default), or an mpv
process with its own full-screen window on the stage output (window engine).

The show uses three decks: ``F`` plays the background (intro, filler, outro),
``A`` and ``B`` take turns playing cues - while one is live, the other holds
the next cue loaded and paused on its first frame, so a trigger only has to
unpause it and put it on stage. Either way the deck is driven over mpv's JSON
IPC; it tracks mpv's state from IPC events and reports changes to its
listener (the show controller); all methods return immediately.
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from aethra.clock import now
from aethra.config import Config, Cue
from aethra.eventlog import EventLog
from aethra.ipc import MpvDisconnected, MpvError, MpvIpc
from aethra.keys import parse_key_spec
from aethra.mpvproc import MpvProcess, build_deck_args, build_layer_options
from aethra.platform import IS_WINDOWS, ipc_address

OVERLAY_FADE = 10
OVERLAY_BLACKOUT = 11
OVERLAY_INFO = 20
FADE_STEP = 0.02
TEST_STATUS = (
    "${filename}\n"
    "${playback-time/full}  frame ${estimated-frame-number}  speed ${speed}\n"
    "dropped vo ${frame-drop-count}  dec ${decoder-frame-drop-count}  late ${vo-delayed-frame-count}"
)


class DeckState(str, enum.Enum):
    DOWN = "down"
    STARTING = "starting"
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    PLAYING = "playing"
    ENDED = "ended"
    FAILED = "failed"


@dataclass(eq=False)
class Load:
    token: int
    media: str
    cue: Cue | None
    start: float
    paused: bool
    loop: bool
    requested_at: float
    done: asyncio.Future[bool]
    entry_id: int | None = None
    ready_at: float | None = None
    error: str | None = None

    @property
    def load_time(self) -> float | None:
        return None if self.ready_at is None else self.ready_at - self.requested_at


class DeckListener(Protocol):
    def deck_ready(self, deck: "Deck", load: Load) -> None: ...
    def deck_failed(self, deck: "Deck", load: Load, reason: str) -> None: ...
    def deck_eof(self, deck: "Deck", load: Load) -> None: ...
    def deck_down(self, deck: "Deck", reason: str) -> None: ...
    def deck_time(self, deck: "Deck", position: float, t: float) -> None: ...
    def deck_seeked(self, deck: "Deck") -> None: ...
    def deck_key(self, deck: "Deck", command: str) -> None: ...
    def deck_reconfig(self, deck: "Deck") -> None: ...


@dataclass
class DeckContext:
    config: Config
    log: EventLog
    mpv_path: str
    supported_options: frozenset[str] | None
    instance: str = "main"
    log_dir: Path | None = None
    session: str = ""
    vo_override: str | None = None
    keybinds: dict[str, str] = field(default_factory=dict)  # mpv key name -> command
    compositor: Any = None  # CompositorStage: decks are its layers


class Deck:
    def __init__(self, name: str, role: str, ctx: DeckContext, listener: DeckListener) -> None:
        self.name = name
        self.role = role  # "filler" or "cue"
        self.ctx = ctx
        self.listener = listener
        self.state = DeckState.DOWN
        self.ipc: MpvIpc | None = None
        self.proc: MpvProcess | None = None
        self.hwnd: int | None = None
        self.adopted = False
        self.load_: Load | None = None
        self.time_pos: float | None = None
        self.time_pos_t = 0.0
        self.paused = True
        self.duration: float | None = None
        self.speed = 1.0
        self.ping_failures = 0
        self.restarts = 0
        self.black = 0.0
        self.tint = "000000"  # colour of the fade overlay (window engine)
        self._token = 0
        self._current_entry: int | None = None
        self._fade_task: asyncio.Task[None] | None = None
        self._test_mode = False

    # -- helpers ---------------------------------------------------------------

    @property
    def log(self) -> EventLog:
        return self.ctx.log

    @property
    def address(self) -> str:
        return ipc_address(self.ctx.instance, self.name)

    @property
    def up(self) -> bool:
        return self.ipc is not None and self.ipc.connected and self.state not in (DeckState.DOWN, DeckState.STARTING)

    @property
    def cue(self) -> Cue | None:
        return self.load_.cue if self.load_ is not None else None

    def __repr__(self) -> str:
        return f"<Deck {self.name} {self.state.value}>"

    def _new_ipc(self) -> MpvIpc:
        return MpvIpc(
            self.address,
            name=f"deck {self.name}",
            on_event=self._on_event,
            on_disconnect=self._on_disconnect,
            on_error=self._on_ipc_error,
        )

    # -- lifecycle ---------------------------------------------------------------

    async def adopt(self, timeout: float = 0.3) -> dict[str, Any] | None:
        """Connect to an mpv left running by a previous controller instance.

        Returns what that mpv reports about itself, or None if nothing answered.
        """
        ipc = self._new_ipc()
        try:
            await ipc.connect(timeout=timeout)
        except MpvDisconnected:
            return None
        try:
            info = {
                "pid": await ipc.get_or("pid"),
                "path": await ipc.get_or("path"),
                "pause": await ipc.get_or("pause"),
                "eof": await ipc.get_or("eof-reached"),
                "time_pos": await ipc.get_or("time-pos"),
                "user": await ipc.get_or("user-data/aethra", {}),
                "entry": await ipc.get_or("playlist/0/id"),
            }
        except MpvError:
            await ipc.close()
            return None
        self.ipc = ipc
        if self.ctx.compositor is not None:
            self.proc = self.ctx.compositor.layer_process(self.name)
        else:
            self.proc = MpvProcess(pid=info["pid"]) if isinstance(info["pid"], int) else None
        self._current_entry = info["entry"] if isinstance(info["entry"], int) else None
        self.adopted = True
        await self._setup()
        self.state = DeckState.IDLE
        self.log.info("deck_adopted", f"deck {self.name}: reconnected to running mpv (pid {info['pid']})", deck=self.name, **{k: v for k, v in info.items() if k != "user"})
        return info

    async def spawn(self) -> bool:
        self.state = DeckState.STARTING
        cfg = self.ctx.config
        old = self.proc
        if old is not None and old.alive():
            # A killed process can take a moment to go; its pipe name must be free.
            old.kill()
            deadline = now() + 2.0
            while old.alive() and now() < deadline:
                await asyncio.sleep(0.05)
            old.reap(0.1)
        log_file = None
        if cfg.logging.mpv_logs and self.ctx.log_dir is not None:
            log_file = self.ctx.log_dir / f"mpv-{self.ctx.session}-{self.name}.log"
        if not IS_WINDOWS:
            Path(self.address).unlink(missing_ok=True)
        if self.ctx.compositor is not None:
            if not await self._create_layer(log_file):
                return False
        else:
            deck_args = build_deck_args(
                self.ctx.mpv_path, cfg, self.name, self.address, log_file, self.ctx.supported_options, self.ctx.vo_override
            )
            if deck_args.dropped and self.restarts == 0:
                self.log.debug("mpv_options", f"deck {self.name}: options not supported by this mpv: {', '.join(deck_args.dropped)}")
            try:
                self.proc = MpvProcess.spawn(deck_args.args, cfg.mpv.priority)
            except OSError as exc:
                self.state = DeckState.DOWN
                self.log.error("deck_spawn_failed", f"deck {self.name}: cannot start mpv: {exc}", deck=self.name)
                return False
        self.adopted = False
        if IS_WINDOWS and not self.proc.in_job and self.restarts == 0:
            self.log.warn("deck_job", f"deck {self.name}: could not put mpv into the crash job; a crash may freeze the picture until the watchdog acts")
        ipc = self._new_ipc()
        try:
            await ipc.connect(timeout=cfg.mpv.startup_timeout)
            self.ipc = ipc
            await self._setup()
        except MpvError as exc:
            self.log.error("deck_spawn_failed", f"deck {self.name}: mpv did not come up: {exc}", deck=self.name)
            await ipc.close()
            self.ipc = None
            self.proc.kill()
            self.state = DeckState.DOWN
            return False
        self.state = DeckState.IDLE
        what = "compositor layer" if self.ctx.compositor is not None else "mpv"
        self.log.info("deck_up", f"deck {self.name}: {what} running (pid {self.proc.pid})", deck=self.name, pid=self.proc.pid)
        return True

    async def _create_layer(self, log_file: Path | None) -> bool:
        layer = build_layer_options(self.ctx.config, self.name, self.address, log_file, self.ctx.supported_options)
        if layer.dropped and self.restarts == 0:
            self.log.debug("mpv_options", f"deck {self.name}: options not supported by this mpv: {', '.join(layer.dropped)}")
        try:
            self.proc = await self.ctx.compositor.create_layer(self.name, layer.options)
        except Exception as exc:  # CompositorError: the compositor is down or refused
            self.state = DeckState.DOWN
            self.log.error("deck_spawn_failed", f"deck {self.name}: cannot create the compositor layer: {exc}", deck=self.name)
            return False
        return True

    async def _setup(self) -> None:
        ipc = self.ipc
        assert ipc is not None
        for prop in ("time-pos", "eof-reached", "pause", "duration"):
            await ipc.observe(prop)
        await ipc.command("set_property", "user-data/aethra", {"deck": self.name, "role": self.role})
        for key, command in self.ctx.keybinds.items():
            ipc.send("keybind", key, f"script-message aethra-key {command}")
        self.hwnd = await self._find_window()
        self.set_test_mode(self._test_mode)

    async def _find_window(self) -> int | None:
        if not IS_WINDOWS or self.ipc is None or self.ctx.compositor is not None:
            return None
        from aethra import winapi

        for _ in range(50):
            wid = await self.ipc.get_or("window-id")
            if isinstance(wid, int) and wid:
                return wid
            if self.proc is not None and self.proc.pid:
                found = [h for h in winapi.windows_of_process(self.proc.pid) if winapi.class_name(h) == "mpv"]
                if found:
                    return found[0]
            await asyncio.sleep(0.05)
        self.log.warn("deck_window", f"deck {self.name}: window handle not found; z-order control limited")
        return None

    async def quit(self, timeout: float = 2.0) -> None:
        self._cancel_fade()
        if self.ipc is not None and self.ipc.connected:
            self.ipc._closing = True
            try:
                await self.ipc.command("quit", timeout=1.0)
            except MpvError:
                pass
            await self.ipc.close()
        if self.proc is not None:
            deadline = now() + timeout
            while self.proc.alive() and now() < deadline:
                await asyncio.sleep(0.05)
            if self.proc.alive():
                self.proc.kill()
            self.proc.reap(0.5)
        self.state = DeckState.DOWN
        self.ipc = None

    async def detach(self) -> None:
        """Close our connection but leave mpv running (controller restarts)."""
        if self.ipc is not None:
            await self.ipc.close()
        self.ipc = None
        self.state = DeckState.DOWN

    def kill(self, reason: str) -> None:
        """Force the process down; the disconnect then triggers ``deck_down``."""
        self.log.warn("deck_kill", f"deck {self.name}: killing mpv ({reason})", deck=self.name)
        if self.proc is not None:
            self.proc.kill()
        if self.ipc is not None and self.ipc.connected:
            self.ipc._lost()
        elif self.state != DeckState.DOWN:
            self._went_down(reason)

    def _on_disconnect(self) -> None:
        self._went_down("connection to mpv lost")

    def _went_down(self, reason: str) -> None:
        if self.state == DeckState.DOWN:
            return
        self._cancel_fade()
        load = self.load_
        self.state = DeckState.DOWN
        self.ipc = None
        self.hwnd = None
        self.time_pos = None
        if load is not None and not load.done.done():
            load.done.set_result(False)
        if self.proc is not None and self.proc.alive():
            self.proc.kill()
        self.listener.deck_down(self, reason)

    def _on_ipc_error(self, what: str, exc: BaseException) -> None:
        if isinstance(exc, MpvDisconnected):
            return
        self.log.warn("deck_ipc", f"deck {self.name}: {what}: {exc}", deck=self.name)

    async def ping(self, timeout: float = 1.0) -> bool:
        if self.ipc is None or not self.ipc.connected:
            return False
        try:
            await self.ipc.request(["get_property", "pid"], timeout)
        except MpvError:
            self.ping_failures += 1
            return False
        self.ping_failures = 0
        return True

    # -- playback ----------------------------------------------------------------

    def load(self, media: str, *, cue: Cue | None = None, start: float = 0.0, paused: bool = True, loop: bool = False) -> Load:
        loop_ = asyncio.get_running_loop()
        self._token += 1
        old = self.load_
        if old is not None and not old.done.done():
            old.done.set_result(False)
        load = Load(self._token, media, cue, max(0.0, start), paused, loop, now(), loop_.create_future())
        self.load_ = load
        if self.ipc is None or not self.ipc.connected:
            load.error = "deck is down"
            load.done.set_result(False)
            return load
        self.state = DeckState.LOADING
        self.time_pos = None
        self._set_speed_now(1.0)
        self.ipc.send("set_property", "pause", paused)
        self.paused = paused
        options = {"start": f"{load.start:.6f}", "loop-file": "inf" if loop else "no"}

        def on_reply(data: Any) -> None:
            if isinstance(data, dict) and isinstance(data.get("playlist_entry_id"), int):
                load.entry_id = data["playlist_entry_id"]

        fut = self.ipc.request({"name": "loadfile", "url": media, "flags": "replace", "options": options}, on_reply=on_reply)
        fut.add_done_callback(lambda f: self._load_command_done(load, f))
        user = {"deck": self.name, "role": self.role, "cue": cue.id if cue else None, "media": media}
        self.ipc.send("set_property", "user-data/aethra", user)
        return load

    def _load_command_done(self, load: Load, fut: asyncio.Future[Any]) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is None:
            return
        if self.load_ is load and self.state == DeckState.LOADING:
            self._fail(load, f"loadfile failed: {exc}")

    def _fail(self, load: Load, reason: str) -> None:
        load.error = reason
        self.state = DeckState.FAILED
        if not load.done.done():
            load.done.set_result(False)
        self.listener.deck_failed(self, load, reason)

    def assume(self, media: str, cue: Cue | None, playing: bool, start: float = 0.0, ended: bool = False) -> Load:
        """Adopt whatever an adopted mpv is already showing as our current load."""
        self._token += 1
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        fut.set_result(True)
        load = Load(self._token, media, cue, start, not playing, False, now(), fut, entry_id=self._current_entry)
        load.ready_at = load.requested_at
        self.load_ = load
        self.state = DeckState.ENDED if ended else DeckState.PLAYING if playing else DeckState.READY
        self.paused = not playing
        return load

    async def conceal(self) -> None:
        """Move the picture out of the window (it shows black), so the deck stays
        dark even without the controller's backdrop. Used before detaching."""
        if self.ipc is not None and self.ipc.connected:
            try:
                await self.ipc.request(["set_property", "video-pan-x", 4.0], 0.5)
            except MpvError:
                pass

    def reveal(self) -> None:
        if self.ipc is not None:
            self.ipc.send("set_property", "video-pan-x", 0.0)

    def play(self) -> None:
        if self.ipc is None:
            return
        self.ipc.send("set_property", "pause", False)
        self.paused = False
        if self.state in (DeckState.READY, DeckState.ENDED):
            self.state = DeckState.PLAYING

    def pause(self) -> None:
        if self.ipc is None:
            return
        self.ipc.send("set_property", "pause", True)
        self.paused = True

    def unload(self) -> None:
        old = self.load_
        self.load_ = None
        if old is not None and not old.done.done():
            old.done.set_result(False)
        self._cancel_fade()
        if self.ipc is None:
            return
        self.ipc.send("stop")
        self._apply_black(0.0)
        self._set_speed_now(1.0)
        self.time_pos = None
        self.state = DeckState.IDLE

    def seek(self, position: float) -> None:
        if self.ipc is not None:
            self.ipc.send("seek", f"{max(0.0, position):.6f}", "absolute+exact")

    def set_speed(self, speed: float) -> None:
        self._set_speed_now(speed)

    def _set_speed_now(self, speed: float) -> None:
        if self.ipc is None or speed == self.speed:
            return
        self.speed = speed
        self.ipc.send("set_property", "speed", speed)

    # -- visuals -----------------------------------------------------------------

    def _black_ass(self, alpha: float, color: str = "000000") -> str:
        a = max(0, min(255, round((1.0 - alpha) * 255)))
        w, h = self.ctx.config.stage.width, self.ctx.config.stage.height
        return (
            "{\\an7\\pos(0,0)\\bord0\\shad0\\blur0\\1c&H%s&\\1a&H%02X&\\p1}m %d %d l %d %d %d %d %d %d{\\p0}"
            % (color, a, -w, -h, 2 * w, -h, 2 * w, 2 * h, -w, 2 * h)
        )

    def _overlay(self, overlay_id: int, data: str | None, z: int) -> None:
        if self.ipc is None:
            return
        cfg = self.ctx.config.stage
        if data is None:
            self.ipc.send_named("osd-overlay", id=overlay_id, format="none", data="")
        else:
            self.ipc.send_named("osd-overlay", id=overlay_id, format="ass-events", data=data, res_x=cfg.width, res_y=cfg.height, z=z)

    def _apply_black(self, alpha: float) -> None:
        alpha = max(0.0, min(1.0, alpha))
        self.black = alpha
        self._overlay(OVERLAY_FADE, self._black_ass(alpha, self.tint) if alpha > 0.0005 else None, 10)

    def set_black(self, alpha: float, color: str | None = None) -> None:
        """Cover the picture with black (or ``color``, ``RRGGBB``) at this strength."""
        self._cancel_fade()
        if color is not None:
            self.tint = color
        self._apply_black(alpha)

    def fade(self, end: float, duration: float, start: float | None = None, color: str | None = None) -> asyncio.Task[None]:
        self._cancel_fade()
        if color is not None:
            self.tint = color
        task = asyncio.create_task(self._fade(self.black if start is None else start, end, duration))
        self._fade_task = task
        return task

    async def _fade(self, a: float, b: float, duration: float) -> None:
        if duration > 0:
            t0 = now()
            while True:
                p = (now() - t0) / duration
                if p >= 1.0:
                    break
                eased = p * p * (3 - 2 * p)  # smoothstep
                self._apply_black(a + (b - a) * eased)
                await asyncio.sleep(FADE_STEP)
        self._apply_black(b)

    def _cancel_fade(self) -> None:
        if self._fade_task is not None and not self._fade_task.done():
            self._fade_task.cancel()
        self._fade_task = None

    def set_blackout(self, on: bool) -> None:
        self._overlay(OVERLAY_BLACKOUT, self._black_ass(1.0) if on else None, 15)

    def set_test_mode(self, enabled: bool) -> None:
        self._test_mode = enabled
        if self.ipc is None:
            return
        if enabled:
            self.ipc.send("set_property", "osd-status-msg", f"[{self.name}] " + TEST_STATUS)
            self.ipc.send("set_property", "osd-level", 3)
        else:
            self.ipc.send("set_property", "osd-level", 0)
            self._overlay(OVERLAY_INFO, None, 20)

    def set_info(self, text: str | None) -> None:
        if not self._test_mode:
            return
        if text is None:
            self._overlay(OVERLAY_INFO, None, 20)
            return
        body = "\\N".join(_ass_escape(line) for line in text.splitlines())
        self._overlay(OVERLAY_INFO, "{\\an1\\fs30\\bord2\\1c&HFFFFFF&\\3c&H000000&}" + body, 20)

    # -- events ------------------------------------------------------------------

    def _on_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        load = self.load_
        if event == "property-change":
            name = msg.get("name")
            data = msg.get("data")
            if name == "time-pos":
                if isinstance(data, (int, float)):
                    t = now()
                    self.time_pos = float(data)
                    self.time_pos_t = t
                    self.listener.deck_time(self, float(data), t)
            elif name == "eof-reached":
                if data is True and load is not None and self.state == DeckState.PLAYING:
                    self.state = DeckState.ENDED
                    self.listener.deck_eof(self, load)
            elif name == "pause":
                self.paused = bool(data)
            elif name == "duration":
                self.duration = data if isinstance(data, (int, float)) else None
        elif event == "start-file":
            self._current_entry = msg.get("playlist_entry_id")
            if load is not None and load.entry_id is None and self.state == DeckState.LOADING:
                load.entry_id = self._current_entry  # very old mpv: no id in the reply
        elif event == "end-file":
            if load is None or msg.get("playlist_entry_id") != load.entry_id:
                return
            reason = msg.get("reason")
            if reason == "error":
                self._fail(load, str(msg.get("file_error") or "playback error"))
            elif reason == "eof":
                if self.state == DeckState.PLAYING:
                    self.state = DeckState.ENDED
                    self.listener.deck_eof(self, load)
                elif self.state == DeckState.LOADING:
                    self._fail(load, "file ended while loading")
        elif event == "playback-restart":
            if (
                load is not None
                and self.state == DeckState.LOADING
                and load.entry_id is not None
                and self._current_entry == load.entry_id
            ):
                load.ready_at = now()
                self.state = DeckState.READY if load.paused else DeckState.PLAYING
                if not load.done.done():
                    load.done.set_result(True)
                self.listener.deck_ready(self, load)
            elif self.state in (DeckState.READY, DeckState.PLAYING, DeckState.ENDED):
                self.listener.deck_seeked(self)
        elif event == "client-message":
            args = msg.get("args") or []
            if len(args) >= 2 and args[0] == "aethra-key":
                self.listener.deck_key(self, str(args[1]))
        elif event == "video-reconfig":
            # mpv may reposition its window here, and it does so with
            # HWND_NOTOPMOST: let the controller check the stacking.
            self.listener.deck_reconfig(self)


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def keybinds_for(config: Config) -> dict[str, str]:
    """mpv key name -> command, for bindings inside the stage windows."""
    binds: dict[str, str] = {}
    if not config.keyboard.stage_window:
        return binds
    for command, specs in config.keyboard.keys.items():
        for spec in specs:
            binds[parse_key_spec(spec).mpv_name] = command
    return binds
