"""Wires everything together for ``aethra run``."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import platform as pyplatform
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from aethra import __version__
from aethra.clock import WallClock
from aethra.config import Config
from aethra.deck import Deck, DeckContext, keybinds_for
from aethra.eventlog import Console, EventLog
from aethra.keyboard import ConsoleKeys, GlobalHotkeys
from aethra.midi import MidiInput
from aethra.mpvproc import mpv_version, resolve_mpv, supported_options
from aethra.mtc import MtcDecoder
from aethra.osc import start_osc_server
from aethra.picture import CompositorPicture, Picture
from aethra.platform import IS_WINDOWS, ProcessSetup, SingleInstance, ipc_address, on_console_close, runtime_dir
from aethra.show import ShowController, StateStore
from aethra.stage import (
    CompositorError,
    CompositorStage,
    NullStage,
    OntopStage,
    Stage,
    Win32Stage,
    choose_monitor,
    list_monitors,
)

EXIT_OK = 0
EXIT_CRASH = 1
EXIT_CONFIG = 2
EXIT_RUNNING = 3


@dataclass
class RunOptions:
    instance: str = "main"
    fresh: bool = False
    vo: str | None = None
    headless: bool = False
    verbose: bool = False
    no_hotkeys: bool = False
    exit_after: float | None = None  # tests: stop cleanly after this many seconds


def run(cfg: Config, opts: RunOptions) -> int:
    setup = ProcessSetup(priority="high")
    setup.apply()
    lock = SingleInstance(runtime_dir() / f"{opts.instance}.lock")
    if not lock.acquire():
        print(f"Aethra ({opts.instance}) is already running on this computer.", file=sys.stderr)
        setup.revert()
        return EXIT_RUNNING
    try:
        return asyncio.run(run_show(cfg, opts))
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        lock.release()
        setup.revert()


async def run_show(cfg: Config, opts: RunOptions) -> int:
    loop = asyncio.get_running_loop()
    console = Console(verbose=opts.verbose)
    wall = WallClock()
    log = EventLog(cfg.logging.dir, console, wall)

    def on_loop_error(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        log.error("internal_error", f"{context.get('message', 'error')}: {exc!r}" if exc else str(context.get("message")))

    loop.set_exception_handler(on_loop_error)
    log.info(
        "start",
        f"Aethra {__version__}: show {cfg.name!r} ({cfg.path})",
        version=__version__, python=sys.version.split()[0], os=pyplatform.platform(), log_file=str(log.path),
    )
    for warning in cfg.warnings:
        log.warn("config", warning)

    mpv = resolve_mpv(cfg.mpv.path)
    if mpv is None:
        log.error("mpv_missing", f"mpv not found at {cfg.mpv.path!r}; set mpv.path in the config")
        log.close()
        return EXIT_CONFIG
    log.info("mpv", f"{mpv_version(mpv) or 'mpv (unknown version)'} at {mpv}")
    supported = supported_options(mpv)

    stage: Stage
    compositor: CompositorStage | None = None
    rect = (0, 0, cfg.stage.width, cfg.stage.height)
    if opts.headless:
        stage = NullStage()
    elif IS_WINDOWS:
        monitors = list_monitors()
        for m in monitors:
            log.info("monitor", str(m))
        chosen = choose_monitor(cfg.stage, monitors)
        if chosen is None:
            # An explicitly configured stage screen is missing (projector not
            # connected yet): wait for it rather than covering the operator's screen.
            log.error("monitor", "the configured stage screen is not connected; waiting for it (Ctrl+C to give up)")
            while chosen is None:
                await asyncio.sleep(2.0)
                chosen = choose_monitor(cfg.stage, list_monitors())
            log.info("monitor", f"stage screen connected: {chosen}")
        elif len(monitors) == 1:
            log.warn("monitor", "only one screen is connected: the video uses it (the console will be covered)")
        if cfg.stage.screen is None and not cfg.stage.screen_name:
            # Tell mpv explicitly which screen we picked, so mpv and the backdrop agree.
            cfg = dataclasses.replace(cfg, stage=dataclasses.replace(cfg.stage, screen=chosen.index))
            log.info("monitor", f"stage output: {chosen}")
        rect = (chosen.left, chosen.top, chosen.width, chosen.height)
        stage = Win32Stage(cfg.stage, log)
    else:
        stage = OntopStage()
    if not opts.headless and cfg.stage.engine == "compositor":
        compositor = await _start_compositor(cfg, opts, log, mpv, rect)
        if compositor is not None:
            stage = compositor
    log.info("engine", "compositor: one window, decks are layers" if compositor is not None else "window engine: one mpv window per deck")

    ctx = DeckContext(
        config=cfg,
        log=log,
        mpv_path=mpv,
        supported_options=supported,
        instance=opts.instance,
        log_dir=cfg.logging.dir,
        session=log.session,
        vo_override=opts.vo,
        keybinds=keybinds_for(cfg),
        compositor=compositor,
    )
    mtc = MtcDecoder(cfg.sync.mtc.timeout) if cfg.midi.enabled else None
    # Decks and controller refer to each other: create the decks first, then
    # make the controller their listener.
    decks = {name: Deck(name, role, ctx, listener=None) for name, role in (("F", "filler"), ("A", "cue"), ("B", "cue"))}  # type: ignore[arg-type]
    picture: Picture | None = CompositorPicture(compositor) if compositor is not None else None
    controller = ShowController(
        cfg, log, decks, stage, mtc, StateStore(runtime_dir() / f"{opts.instance}-state.json"), wall, picture=picture
    )
    for deck in decks.values():
        deck.listener = controller
    windows = compositor.backdrop if compositor is not None else stage
    if isinstance(windows, Win32Stage):
        windows.on_display_change = lambda: _display_changed(windows, controller, log)
    if compositor is not None:
        compositor.on_key = controller.stage_key
        compositor.on_restart = controller.compositor_restarted

    def post(command: str, source: str) -> None:
        loop.call_soon_threadsafe(controller.command, command, source)

    # Closing the console window must not black out the show: the controller
    # exits but leaves the video players running, and a restarted Aethra takes
    # them over. Quitting for real is the quit key (or Ctrl+C) pressed twice.
    detach = False

    def console_closed() -> None:
        nonlocal detach
        detach = True
        log.warn("console_closed", "console closed: leaving the video players running")
        controller.stop_event.set()

    if IS_WINDOWS:
        # Normally Ctrl+C arrives as a key (console input mode); these handlers
        # cover consoles where that mode could not be set.
        signal.signal(signal.SIGINT, lambda *_: post("quit", "Ctrl+C"))
        signal.signal(signal.SIGBREAK, lambda *_: post("quit", "Ctrl+Break"))
        on_console_close(lambda: loop.call_soon_threadsafe(console_closed))
    else:
        loop.add_signal_handler(signal.SIGINT, controller.command, "quit", "Ctrl+C")
        loop.add_signal_handler(signal.SIGTERM, controller.stop_event.set)
        loop.add_signal_handler(signal.SIGHUP, console_closed)

    await controller.start(fresh=opts.fresh)
    status_file = runtime_dir() / f"{opts.instance}-status.json"
    engine = "compositor" if compositor is not None else "windows"

    def on_status(text: str) -> None:
        console.status(text)
        # For the control panel (aethra gui): what is on stage, once a second.
        data = dict(controller.status_dict(), pid=os.getpid(), updated=time.time(), engine=engine, config=str(cfg.path))
        tmp = status_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, status_file)
        except OSError:
            pass

    controller.on_status = on_status
    if opts.exit_after is not None:
        loop.call_later(opts.exit_after, controller.stop_event.set)

    midi: MidiInput | None = None
    if cfg.midi.enabled:
        midi = MidiInput(cfg.midi, log, controller.on_midi, on_status=lambda s: setattr(controller.inputs, "midi", s))
        await midi.start()

    osc_transport = None
    if cfg.osc.enabled:
        def osc_error(text: str, addr: tuple[str, int]) -> None:
            log.debug("osc_error", f"{text} from {addr[0]}")

        try:
            osc_transport = await start_osc_server(cfg.osc.bind, cfg.osc.port, controller.on_osc, osc_error)
            controller.inputs.osc = f"udp/{cfg.osc.port}"
            log.info("osc", f"listening for OSC on {cfg.osc.bind}:{cfg.osc.port}")
        except OSError as exc:
            controller.inputs.osc = "error"
            log.error("osc", f"cannot listen on UDP {cfg.osc.bind}:{cfg.osc.port}: {exc}")

    hotkeys = None
    if IS_WINDOWS and cfg.keyboard.global_hotkeys and not opts.no_hotkeys:
        hotkeys = GlobalHotkeys(cfg.keyboard.keys, post, log)
        hotkeys.start()
    # The console reader also runs with keyboard.console off: it turns Ctrl+C
    # into the quit command.
    console_keys: ConsoleKeys | None = ConsoleKeys(cfg.keyboard.keys if cfg.keyboard.console else {}, post, log)
    if not console_keys.start():
        console_keys = None

    exit_code = EXIT_OK
    try:
        await controller.stop_event.wait()
    except asyncio.CancelledError:
        exit_code = EXIT_CRASH
    finally:
        console.clear_status()
        log.info("stop", "shutting down")
        if console_keys is not None:
            console_keys.stop()
        if hotkeys is not None:
            hotkeys.stop()
        if midi is not None:
            await midi.stop()
        if osc_transport is not None:
            osc_transport.close()
        await controller.shutdown(quit_decks=not detach)
        status_file.unlink(missing_ok=True)
        log.info("stopped", "video players left running" if detach else "bye")
        log.close()
        console.close()
    return exit_code


async def _start_compositor(
    cfg: Config, opts: RunOptions, log: EventLog, mpv: str, rect: tuple[int, int, int, int]
) -> CompositorStage | None:
    """Start (or reconnect to) the compositor; None if it cannot run here, in
    which case the show runs with the window engine."""
    libmpv = cfg.mpv.libmpv
    if libmpv is None:
        # The bundle keeps libmpv next to mpv.exe.
        for name in ("libmpv-2.dll", "mpv-2.dll") if IS_WINDOWS else ():
            candidate = Path(mpv).parent / name
            if candidate.is_file():
                libmpv = str(candidate)
                break
    log_file = None
    if cfg.logging.dir is not None:
        try:
            cfg.logging.dir.mkdir(parents=True, exist_ok=True)
            log_file = cfg.logging.dir / f"compositor-{log.session}.log"
        except OSError:
            log_file = None
    compositor = CompositorStage(
        cfg.stage, log, ipc_address(opts.instance, "compositor"), rect,
        libmpv=libmpv, log_file=log_file, backdrop=Win32Stage(cfg.stage, log) if IS_WINDOWS else None,
        startup_timeout=cfg.mpv.startup_timeout + 5.0, priority=cfg.mpv.priority,
    )
    try:
        await compositor.open()
    except CompositorError as exc:
        log.error("compositor", f"{exc}; using the window engine instead (transitions dip through black)")
        return None
    return compositor


def _display_changed(stage: Win32Stage, controller: ShowController, log: EventLog) -> None:
    log.warn("display_change", "display configuration changed")
    loop = asyncio.get_running_loop()

    def settle(attempt: int) -> None:
        # Windows sends several notifications while it rearranges; act once it has settled.
        try:
            if stage.reposition():
                log.info("monitor", f"windows placed on the stage output: {stage.monitor}")
                controller.picture.refresh()
                return
            if attempt == 0:
                log.error("monitor", "stage screen not found after the display change; waiting for it")
        except Exception as exc:
            log.error("display_change", f"could not reposition the windows: {exc!r}")
        loop.call_later(2.0, settle, attempt + 1)

    handle = getattr(stage, "_settle_handle", None)
    if handle is not None:
        handle.cancel()
    stage._settle_handle = loop.call_later(1.5, settle, 0)  # type: ignore[attr-defined]
