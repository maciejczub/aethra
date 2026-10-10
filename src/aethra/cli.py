"""Command line: ``aethra run|check|verify|devices|monitor|bench|selftest|rehearse|report|send``."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import glob
import socket
import sys
from pathlib import Path

from aethra import __version__
from aethra.config import Config, ConfigError, load_config

DEFAULT_CONFIG = "show.yaml"


def _load(path: str) -> Config | None:
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"Configuration error in {path}:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- run


def cmd_run(args: argparse.Namespace) -> int:
    from aethra.app import EXIT_CONFIG, RunOptions, run

    cfg = _load(args.config)
    if cfg is None:
        return EXIT_CONFIG
    if args.test_mode:
        cfg = dataclasses.replace(cfg, test_mode=True)
    opts = RunOptions(
        instance=args.instance,
        fresh=args.fresh,
        vo=args.vo,
        headless=args.headless,
        verbose=args.verbose,
        no_hotkeys=args.no_hotkeys,
        exit_after=args.exit_after,
    )
    return run(cfg, opts)


# --------------------------------------------------------------------------- check


def _trigger_text(cue) -> str:  # type: ignore[no-untyped-def]
    parts = []
    if cue.midi_note is not None:
        parts.append(f"note {cue.midi_note}")
    if cue.midi_program is not None:
        parts.append(f"prog {cue.midi_program}")
    if cue.osc is not None:
        parts.append(f"osc {cue.osc.address}" + (f" {cue.osc.arg!r}" if cue.osc.arg is not None else ""))
    return ", ".join(parts) or "(manual only)"


def cmd_check(args: argparse.Namespace) -> int:
    from aethra.mpvproc import mpv_version, resolve_mpv
    from aethra.stage import choose_monitor, list_monitors

    cfg = _load(args.config)
    if cfg is None:
        return 2
    problems: list[str] = list(cfg.warnings)
    print(f"Aethra {__version__}: {cfg.path}")
    print(f"Show: {cfg.name} ({len(cfg.cues)} cues)")

    mpv = resolve_mpv(cfg.mpv.path)
    if mpv is None:
        problems.append(f"mpv not found ({cfg.mpv.path})")
        print(f"mpv:    NOT FOUND ({cfg.mpv.path})")
    else:
        print(f"mpv:    {mpv_version(mpv) or '?'} ({mpv})")

    monitors = list_monitors()
    if monitors:
        chosen = choose_monitor(cfg.stage, monitors)
        for m in monitors:
            print(f"screen: {m}{'   <- stage output' if m == chosen else ''}")
        if chosen is None:
            problems.append("the configured stage screen is not connected")
    else:
        where = cfg.stage.screen_name or (f"#{cfg.stage.screen}" if cfg.stage.screen is not None else "default")
        print(f"stage:  screen {where} (monitor list available on Windows only)")

    if cfg.midi.enabled:
        line = f"MIDI:   input matching {cfg.midi.port!r}, channel {cfg.midi.channel or 'any'}"
        try:
            from aethra.midi import make_backend, match_port

            names = make_backend(cfg.midi.backend).list_inputs()
            index = match_port(names, cfg.midi.port)
            if index is None:
                problems.append(f"MIDI input {cfg.midi.port!r} not found (available: {', '.join(names) or 'none'})")
                line += " -> NOT FOUND"
            else:
                line += f" -> {names[index]!r}"
        except Exception as exc:
            line += f" -> cannot list ports ({exc})"
        print(line)
        if cfg.midi.command_notes:
            print("        command notes: " + ", ".join(f"{k}={v}" for k, v in cfg.midi.command_notes.items()))
    else:
        print("MIDI:   disabled")
    print(f"OSC:    {'udp ' + cfg.osc.bind + ':' + str(cfg.osc.port) if cfg.osc.enabled else 'disabled'}")
    print(f"MTC:    {'drift correction on' if cfg.sync.mtc.enabled else 'off'}; latency compensation {cfg.sync.latency_compensation * 1000:+.0f} ms")
    filler = cfg.filler
    if filler.mode == "color":
        print(f"Filler: colour {filler.color}")
    else:
        state = "ok" if filler.file and filler.file.is_file() else "MISSING (colour fallback)"
        print(f"Filler: {filler.mode} {filler.file} [{state}]")

    print()
    print(f"  {'#':>2}  {'id':<10} {'title':<24} {'trigger':<22} {'offset':>8}  {'end':<6} file")
    for cue in cfg.cues:
        exists = cue.file.is_file()
        print(
            f"  {cue.index + 1:>2}  {cue.id:<10} {cue.title[:24]:<24} {_trigger_text(cue)[:22]:<22} "
            f"{cue.offset:+8.3f}  {cue.on_end:<6} {cue.file.name}{'' if exists else '  [MISSING]'}"
        )
    keys = ", ".join(f"{cmd}: {'/'.join(specs)}" for cmd, specs in cfg.keyboard.keys.items() if specs)
    print(f"\nKeys:   {keys}")
    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK")
    return 0


# --------------------------------------------------------------------------- verify


def cmd_verify(args: argparse.Namespace) -> int:
    from aethra.media import verify_file
    from aethra.mpvproc import resolve_mpv

    cfg = _load(args.config)
    if cfg is None:
        return 2
    mpv = resolve_mpv(cfg.mpv.path)
    if mpv is None:
        print(f"mpv not found ({cfg.mpv.path})", file=sys.stderr)
        return 2
    files: list[tuple[str, Path]] = [(cue.id, cue.file) for cue in cfg.cues]
    if cfg.filler.file is not None and cfg.filler.mode != "color":
        files.append(("filler", cfg.filler.file))
    bad = 0
    mode = "quick check (start and end)" if args.quick else "full decode"
    print(f"Verifying {len(files)} files with {mpv} - {mode}\n")
    for label, path in files:
        report = verify_file(mpv, path, quick=args.quick)
        status = "OK  " if report.ok else "FAIL"
        print(f"[{status}] {label:<10} {path.name:<36} {report.summary()}  ({report.decode_seconds:.1f} s)")
        for problem in report.problems[:8]:
            print(f"         ! {problem}")
        for warning in report.warnings(cfg.stage.fps, (cfg.stage.width, cfg.stage.height)):
            print(f"         ~ {warning}")
        bad += 0 if report.ok else 1
    print(f"\n{len(files) - bad} OK, {bad} failed")
    return 1 if bad else 0


# --------------------------------------------------------------------------- devices


def cmd_devices(args: argparse.Namespace) -> int:
    from aethra.midi import MidiError, make_backend
    from aethra.stage import list_monitors

    try:
        backend = make_backend(args.backend)
        names = backend.list_inputs()
        print(f"MIDI inputs ({backend.name}):")
        for i, name in enumerate(names):
            print(f"  {i}: {name}")
        if not names:
            print("  (none)")
    except MidiError as exc:
        print(f"MIDI: {exc}")
    monitors = list_monitors()
    if monitors:
        print("Screens (index = stage.screen):")
        for m in monitors:
            print(f"  {m}")
    return 0


# --------------------------------------------------------------------------- monitor


def cmd_monitor(args: argparse.Namespace) -> int:
    return asyncio.run(_monitor(args))


async def _monitor(args: argparse.Namespace) -> int:
    from aethra.clock import WallClock
    from aethra.config import MidiConfig
    from aethra.eventlog import Console, EventLog
    from aethra.midi import MidiInput, describe_message
    from aethra.mtc import MtcDecoder
    from aethra.osc import start_osc_server

    cfg = _load(args.config) if Path(args.config).is_file() else None
    midi_cfg = cfg.midi if cfg is not None else MidiConfig(enabled=True, port=args.midi_port or "*")
    if args.midi_port:
        midi_cfg = dataclasses.replace(midi_cfg, port=args.midi_port, enabled=True)
    osc_port = args.osc_port or (cfg.osc.port if cfg is not None else 9000)
    wall = WallClock()
    log = EventLog(None, Console(), wall)
    mtc = MtcDecoder()
    last_mtc_print = 0.0

    def on_midi(data: bytes, t: float) -> None:
        nonlocal last_mtc_print
        if data and data[0] == 0xF1:
            mtc.feed_quarter_frame(data[1], t)
            if t - last_mtc_print >= 1.0:
                snap = mtc.snapshot(t)
                print(f"{wall.hms(t)}  MTC {snap.status} {snap.timecode} ({snap.fps or '?'} fps)", flush=True)
                last_mtc_print = t
            return
        if data and data[0] == 0xF0:
            mtc.feed_full_frame(bytes(data), t)
        if data and data[0] in (0xF8, 0xFE):
            return
        print(f"{wall.hms(t)}  MIDI {describe_message(data)}", flush=True)

    midi = MidiInput(midi_cfg, log, on_midi)
    await midi.start()

    def on_osc(message, addr, t):  # type: ignore[no-untyped-def]
        print(f"{wall.hms(t)}  OSC  {addr[0]} {message.address} {list(message.args)}", flush=True)

    transport = None
    try:
        transport = await start_osc_server("0.0.0.0", osc_port, on_osc, lambda text, addr: print(f"OSC error: {text} from {addr[0]}", flush=True))
        print(f"Listening: MIDI {midi_cfg.port!r}, OSC udp/{osc_port}. Ctrl+C to stop.", flush=True)
    except OSError as exc:
        print(f"OSC: cannot listen on {osc_port}: {exc}", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await midi.stop()
        if transport is not None:
            transport.close()
    return 0


# --------------------------------------------------------------------------- bench


def cmd_bench(args: argparse.Namespace) -> int:
    from aethra.bench import bench_file, format_results
    from aethra.mpvproc import resolve_mpv

    cfg = _load(args.config) if args.config else None
    if cfg is None:
        from aethra.config import parse_config

        cfg = parse_config({"midi": {"enabled": False}, "cues": [{"id": "x", "file": "x"}]}, Path.cwd() / "bench.yaml")
    if args.screen is not None:
        cfg = dataclasses.replace(cfg, stage=dataclasses.replace(cfg.stage, screen=args.screen, screen_name=None))
    mpv = resolve_mpv(args.mpv or cfg.mpv.path)
    if mpv is None:
        print("mpv not found", file=sys.stderr)
        return 2
    hwdecs = args.hwdec or ["auto-safe", "no"]
    files: list[str] = []
    for pattern in args.files:  # cmd.exe does not expand wildcards
        matches = sorted(glob.glob(pattern)) if any(ch in pattern for ch in "*?[") else [pattern]
        files.extend(matches)
    if not files:
        print("no files to test", file=sys.stderr)
        return 2

    async def go() -> list:
        results = []
        for file in files:
            for hwdec in hwdecs:
                print(f"playing {file} with hwdec={hwdec} for {args.seconds:g} s ...", flush=True)
                results.append(await bench_file(mpv, cfg, Path(file), hwdec, args.seconds, args.vo))
        return results

    results = asyncio.run(go())
    print()
    print(format_results(results))
    return 0 if all(r.error is None and r.dropped == 0 for r in results) else 1


# --------------------------------------------------------------------------- send


def _parse_osc_arg(text: str) -> object:
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def cmd_send(args: argparse.Namespace) -> int:
    from aethra.osc import build_message

    payload = build_message(args.address, *(_parse_osc_arg(a) for a in args.args))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, (args.host, args.port))
    print(f"sent {args.address} {args.args} to {args.host}:{args.port}")
    return 0


# --------------------------------------------------------------------------- selftest


def cmd_selftest(args: argparse.Namespace) -> int:
    from aethra.mpvproc import resolve_mpv
    from aethra.selftest import main as selftest_main

    cfg = None
    if args.config and Path(args.config).is_file():
        cfg = _load(args.config)
        if cfg is None:
            return 2
    mpv = resolve_mpv(args.mpv or (cfg.mpv.path if cfg else "mpv"))
    if mpv is None:
        print("mpv not found (use --mpv or a show file with mpv.path)", file=sys.stderr)
        return 2
    return selftest_main(cfg, mpv, show_window=args.windows)


# --------------------------------------------------------------------------- rehearse


def cmd_rehearse(args: argparse.Namespace) -> int:
    from aethra.mpvproc import resolve_mpv
    from aethra.rehearse import rehearse

    cfg = _load(args.config)
    if cfg is None:
        return 2
    mpv = resolve_mpv(cfg.mpv.path)
    if mpv is None:
        print(f"mpv not found ({cfg.mpv.path})", file=sys.stderr)
        return 2
    return rehearse(cfg, mpv, args.loops, args.gap, args.host, args.max_clip)


# --------------------------------------------------------------------------- report


def cmd_report(args: argparse.Namespace) -> int:
    from aethra.report import build_report, read_log

    path = Path(args.log)
    if path.is_dir():
        logs = sorted(path.glob("aethra-*.jsonl"))
        if not logs:
            print(f"no event logs in {path}", file=sys.stderr)
            return 2
        path = logs[-1]
    print(f"{path}\n")
    print(build_report(read_log(path)))
    return 0


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aethra", description="Reliability-first stage video player.")
    parser.add_argument("--version", action="version", version=f"aethra {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="run the show")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--test-mode", action="store_true", help="show the timecode/frame overlay")
    p.add_argument("--fresh", action="store_true", help="do not reconnect to decks or state left by a previous run")
    p.add_argument("--instance", default="main", help=argparse.SUPPRESS)
    p.add_argument("--vo", default=None, help="override mpv's video output (e.g. null for headless tests)")
    p.add_argument("--headless", action="store_true", help="no window management (tests)")
    p.add_argument("--no-hotkeys", action="store_true", help="do not register global hotkeys")
    p.add_argument("--exit-after", type=float, default=None, help=argparse.SUPPRESS)  # automated tests
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="validate the configuration, media files and devices")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("verify", help="decode every media file to find damaged files")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--quick", action="store_true", help="only the first and last seconds of each file")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("devices", help="list MIDI inputs and screens")
    p.add_argument("--backend", default="auto", choices=("auto", "winmm", "rtmidi"))
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("monitor", help="print incoming MIDI, MTC and OSC (no video)")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--midi-port", default=None, help='input name (part of it), "*" = first port')
    p.add_argument("--osc-port", type=int, default=None)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("bench", help="codec test: play files full screen and count dropped frames")
    p.add_argument("files", nargs="+")
    p.add_argument("--config", default=None, help="take stage and mpv settings from this show file")
    p.add_argument("--hwdec", action="append", help="hwdec mode(s) to test (default: auto-safe and no)")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--screen", type=int, default=None)
    p.add_argument("--mpv", default=None)
    p.add_argument("--vo", default=None)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("selftest", help="measure IPC, timing, playback rate, hotkeys and window control on this machine")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--windows", action="store_true", help="also test real windows on the stage screen (shows a test picture there)")
    p.add_argument("--mpv", default=None)
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("rehearse", help="trigger every cue in order over OSC (unattended run of the set)")
    p.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--loops", type=int, default=2, help="how many times to play the set (default 2)")
    p.add_argument("--gap", type=float, default=5.0, help="seconds of filler between cues")
    p.add_argument("--max-clip", type=float, default=None, help="cut every cue after this many seconds (quick runs)")
    p.add_argument("--host", default="127.0.0.1")
    p.set_defaults(func=cmd_rehearse)

    p = sub.add_parser("report", help="summarise an event log after the show")
    p.add_argument("log", nargs="?", default="logs", help="log file, or a folder (the newest log is used)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("send", help="send one OSC message (testing)")
    p.add_argument("address")
    p.add_argument("args", nargs="*")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.set_defaults(func=cmd_send)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    commands = {"run", "check", "verify", "devices", "monitor", "bench", "selftest", "rehearse", "report", "send"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        argv.insert(0, "run")  # "aethra show.yaml" == "aethra run show.yaml"
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return int(args.func(args))
