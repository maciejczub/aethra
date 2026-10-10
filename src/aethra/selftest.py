"""``aethra selftest``: measures the mechanisms Aethra relies on, on this machine.

Meant for the show laptop (the Windows-specific parts cannot be tested in CI):
IPC round trips over the named pipe, the playback rate of a real mpv against
the system clock, timer accuracy, MIDI and screen enumeration, global hotkey
registration, and - with ``--windows`` - the backdrop window and z-order
control on the stage screen (this briefly shows a test picture there).
"""

from __future__ import annotations

import asyncio
import dataclasses
import platform as pyplatform
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from aethra.clock import now, sleep_until
from aethra.config import Config, parse_config
from aethra.ipc import MpvError, MpvIpc
from aethra.mpvproc import MpvProcess, build_deck_args, mpv_version, supported_options
from aethra.platform import IS_WINDOWS, ProcessSetup, ipc_address


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL | INFO
    detail: str


def _pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


async def _timer_accuracy() -> Check:
    errors = []
    for _ in range(50):
        target = now() + 0.02
        await sleep_until(target)
        errors.append((now() - target) * 1000)
    med, worst = statistics.median(errors), max(errors)
    status = "PASS" if worst < 4 else "WARN"
    return Check("timer accuracy", status, f"scheduled wake-ups late by median {med:.2f} ms, max {worst:.2f} ms")


async def _mpv_checks(mpv: str, cfg: Config, show_window: bool) -> list[Check]:
    checks: list[Check] = []
    address = ipc_address("selftest", "X")
    if not IS_WINDOWS:
        Path(address).unlink(missing_ok=True)
    args = build_deck_args(mpv, cfg, "selftest", address, None, supported_options(mpv), None if show_window else "null").args
    t_spawn = now()
    proc = MpvProcess.spawn(args, cfg.mpv.priority)
    ipc = MpvIpc(address, name="selftest")
    try:
        await ipc.connect(timeout=cfg.mpv.startup_timeout)
        checks.append(Check("mpv start", "PASS", f"IPC connected {(now() - t_spawn) * 1000:.0f} ms after launch ({'named pipe' if IS_WINDOWS else 'unix socket'})"))

        latencies = []
        for _ in range(300):
            t = now()
            await ipc.get("pid")
            latencies.append((now() - t) * 1000)
        med, p99 = statistics.median(latencies), _pct(latencies, 0.99)
        checks.append(Check("IPC round trip", "PASS" if p99 < 5 else "WARN", f"median {med:.2f} ms, 99th percentile {p99:.2f} ms"))

        events: list[tuple[float, float]] = []
        restarted = asyncio.Event()

        def on_event(msg: dict) -> None:
            if msg.get("event") == "playback-restart":
                restarted.set()
            elif msg.get("event") == "property-change" and msg.get("name") == "time-pos" and isinstance(msg.get("data"), (int, float)):
                events.append((now(), float(msg["data"])))

        ipc.on_event = on_event
        await ipc.observe("time-pos")
        s = cfg.stage
        source = f"av://lavfi:testsrc2=s={s.width}x{s.height}:r={s.fps:g}"
        await ipc.command("set_property", "pause", True)
        t_load = now()
        await ipc.command_named("loadfile", url=source, flags="replace")
        await asyncio.wait_for(restarted.wait(), 10)
        checks.append(Check("load to first frame", "INFO", f"{(now() - t_load) * 1000:.0f} ms for a generated test picture (paused)"))
        events.clear()
        t0 = now()
        await ipc.command("set_property", "pause", False)
        await asyncio.sleep(6.0)
        await ipc.command("set_property", "pause", True)
        usable = [(t, p) for t, p in events if t - t0 > 0.5]
        if len(usable) > 10:
            (ta, pa), (tb, pb) = usable[0], usable[-1]
            rate = (pb - pa) / (tb - ta)
            fps = len(usable) / (tb - ta)
            ok = abs(rate - 1.0) < 0.002
            checks.append(Check(
                "playback rate", "PASS" if ok else "FAIL",
                f"mpv advanced {rate:.4f} s per second of system time ({fps:.1f} position updates/s)"
                + ("" if ok else " - the drift correction will have to work constantly; investigate"),
            ))
        else:
            checks.append(Check("playback rate", "FAIL", "too few position updates from mpv"))
        drops = {p: await ipc.get_or(p) for p in ("frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count")}
        checks.append(Check("dropped frames", "PASS" if not any(drops.values()) else "WARN", ", ".join(f"{k} {v}" for k, v in drops.items())))
        if show_window:
            checks.append(Check("video output", "INFO", f"vo {await ipc.get_or('current-vo')}, display {await ipc.get_or('display-fps')} Hz, hwdec {await ipc.get_or('hwdec-current')}"))
            if IS_WINDOWS:
                checks.extend(await _zorder_check(ipc, proc, cfg))
    except (MpvError, asyncio.TimeoutError) as exc:
        checks.append(Check("mpv", "FAIL", str(exc) or type(exc).__name__))
    finally:
        try:
            await ipc.command("quit", timeout=1.0)
        except MpvError:
            pass
        await ipc.close()
        await asyncio.sleep(0.3)
        if proc.alive():
            proc.kill()
    return checks


async def _zorder_check(ipc: MpvIpc, proc: MpvProcess, cfg: Config) -> list[Check]:
    from aethra import winapi
    from aethra.eventlog import EventLog
    from aethra.stage import Win32Stage

    class _OneDeck:
        name = "X"
        up = True
        hwnd: int | None = None

    deck = _OneDeck()
    wid = await ipc.get_or("window-id")
    deck.hwnd = wid if isinstance(wid, int) and wid else None
    if deck.hwnd is None and proc.pid:
        found = [h for h in winapi.windows_of_process(proc.pid) if winapi.class_name(h) == "mpv"]
        deck.hwnd = found[0] if found else None
    if deck.hwnd is None:
        return [Check("window handle", "FAIL", "mpv's window was not found")]
    stage = Win32Stage(cfg.stage, EventLog(None, None))
    stage.start()
    try:
        stage.register(deck)  # type: ignore[arg-type]
        results = []
        for order in (["X", "backdrop"], ["backdrop", "X"], ["X", "backdrop"]):
            stage.apply(order)
            await asyncio.sleep(0.4)
            wanted = [h for h in (stage._hwnd_of(n) for n in order) if h]
            actual = winapi.z_order(set(wanted))
            results.append(actual == wanted)
        ok = all(results)
        return [Check("z-order control", "PASS" if ok else "FAIL", "deck/backdrop raised and lowered 3 times" + ("" if ok else f": {results}"))]
    finally:
        stage.close()


def _hotkey_check() -> Check:
    if not IS_WINDOWS:
        return Check("global hotkeys", "INFO", "Windows only")
    from aethra import winapi as w

    vk_f24 = 0x87
    mods = 0x0001 | 0x0002 | 0x0004  # alt+ctrl+shift
    if w.RegisterHotKey(None, 0xBEEF, mods | w.MOD_NOREPEAT, vk_f24):
        w.UnregisterHotKey(None, 0xBEEF)
        return Check("global hotkeys", "PASS", "RegisterHotKey works")
    return Check("global hotkeys", "FAIL", "RegisterHotKey failed")


def _midi_check(cfg: Config) -> Check:
    from aethra.midi import make_backend, match_port

    try:
        names = make_backend(cfg.midi.backend).list_inputs()
    except Exception as exc:
        return Check("MIDI inputs", "WARN", str(exc))
    detail = ", ".join(names) or "none"
    if cfg.midi.enabled and cfg.midi.port:
        index = match_port(names, cfg.midi.port)
        if index is None:
            return Check("MIDI inputs", "WARN", f"{detail} (configured {cfg.midi.port!r} not found)")
        return Check("MIDI inputs", "PASS", f"{detail} -> using {names[index]!r}")
    return Check("MIDI inputs", "INFO", detail)


def _screen_check(cfg: Config) -> Check:
    from aethra.stage import choose_monitor, list_monitors

    monitors = list_monitors()
    if not monitors:
        return Check("screens", "INFO", "Windows only")
    chosen = choose_monitor(cfg.stage, monitors)
    detail = "; ".join(str(m) for m in monitors)
    if chosen is None:
        return Check("screens", "FAIL", f"{detail} - the configured stage screen is not there")
    status = "PASS" if (chosen.width, chosen.height) == (cfg.stage.width, cfg.stage.height) else "WARN"
    return Check("screens", status, f"stage = {chosen}; all: {detail}")


async def run_selftest(cfg: Config | None, mpv: str, show_window: bool) -> list[Check]:
    if cfg is None:
        cfg = parse_config({"midi": {"enabled": False}, "cues": [{"id": "x", "file": "x"}]}, Path.cwd() / "selftest.yaml")
    checks = [Check("system", "INFO", f"{pyplatform.platform()}, Python {sys.version.split()[0]}, {mpv_version(mpv)}")]
    checks.append(_screen_check(cfg))
    checks.append(_midi_check(cfg))
    checks.append(_hotkey_check())
    checks.append(await _timer_accuracy())
    if show_window and cfg.stage.screen is None and IS_WINDOWS:
        from aethra.stage import choose_monitor, list_monitors

        chosen = choose_monitor(cfg.stage, list_monitors())
        if chosen is not None:
            cfg = dataclasses.replace(cfg, stage=dataclasses.replace(cfg.stage, screen=chosen.index))
    checks.extend(await _mpv_checks(mpv, cfg, show_window))
    return checks


def main(cfg: Config | None, mpv: str, show_window: bool) -> int:
    setup = ProcessSetup(priority="high", console=False)  # keep Ctrl+C working here
    setup.apply()
    try:
        checks = asyncio.run(run_selftest(cfg, mpv, show_window))
    finally:
        setup.revert()
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status:<4}] {c.name:<{width}}  {c.detail}")
    failed = [c for c in checks if c.status == "FAIL"]
    print(f"\n{'FAILED: ' + ', '.join(c.name for c in failed) if failed else 'no failures'}")
    return 1 if failed else 0
