"""The control panel's back end: a small HTTP server on 127.0.0.1.

The page (``static/``) talks to it with JSON requests that carry a per-session
token, so other web pages open in a browser on the same computer cannot use
it. Everything long-running (checks, benchmarks, rehearsals, the Windows
preparation script) runs as a *job*: a child process whose output the page
polls line by line. The show itself is started as the normal launcher in its
own console window, exactly as the desktop shortcut starts it; the panel
watches it through the status file the controller writes and controls it
over OSC on 127.0.0.1.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aethra import __version__
from aethra.blend import BASIC_TRANSITIONS, BLEND_MODES, TRANSITION_TYPES
from aethra.config import COMMANDS, DEFAULT_KEYS, ConfigError, parse_config
from aethra.platform import IS_WINDOWS, runtime_dir

STATIC = Path(__file__).resolve().parent / "static"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # the folder that holds "aethra"
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mxf", ".png", ".jpg", ".jpeg", ".bmp", ".gif"}
MAX_JOB_LINES = 20000


def install_root() -> Path:
    """The Aethra folder: the bundle root (with Aethra.cmd) or the checkout."""
    return PACKAGE_ROOT.parent


def python_console() -> str:
    """python.exe even when the panel itself runs under pythonw.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return str(exe)


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# --------------------------------------------------------------------------- YAML


def load_yaml(path: Path) -> Any:
    import yaml

    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


HEADER = (
    "# Aethra show configuration, saved by the control panel (aethra gui).\n"
    "# Reference: docs/configuration.md. Keys left out use their defaults.\n"
    "# The previous version of this file is kept as {name}.bak.\n\n"
)
SECTION_ORDER = (
    "show", "stage", "mpv", "filler", "intro", "outro", "transitions", "sync", "midi", "osc", "keyboard", "logging",
    "test_mode", "cues",
)


class _Dumper:
    """PyYAML with readable output: blocks for sections, flow style for cues."""

    @staticmethod
    def dump(data: dict[str, Any]) -> str:
        import yaml

        class Dumper(yaml.SafeDumper):
            pass

        def str_presenter(dumper: Any, value: str) -> Any:
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"' if _needs_quotes(value) else None)

        Dumper.add_representer(str, str_presenter)
        parts = []
        ordered = [k for k in SECTION_ORDER if k in data] + [k for k in data if k not in SECTION_ORDER]
        for key in ordered:
            value = data[key]
            if key == "cues" and isinstance(value, list):
                lines = ["cues:"]
                for cue in value:
                    body = yaml.dump(cue, Dumper=Dumper, default_flow_style=True, sort_keys=False, width=100000, allow_unicode=True).strip()
                    lines.append(f"  - {body}")
                parts.append("\n".join(lines) + "\n")
            else:
                parts.append(yaml.dump({key: value}, Dumper=Dumper, default_flow_style=False, sort_keys=False, allow_unicode=True))
        return "\n".join(parts)


def _needs_quotes(value: str) -> bool:
    # Ids such as "01" must stay text; colours start with "#".
    return value == "" or value[:1] in "#&*!|>%@`'\"{[" or value.strip() != value or value.lower() in (
        "yes", "no", "true", "false", "on", "off", "null", "~"
    ) or _looks_numeric(value) or ":" in value


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def dump_yaml(data: dict[str, Any], name: str) -> str:
    return HEADER.format(name=name) + _Dumper.dump(data)


# --------------------------------------------------------------------------- jobs


@dataclass
class Job:
    id: str
    kind: str
    title: str
    args: list[str]
    started: float = field(default_factory=time.time)
    lines: list[str] = field(default_factory=list)
    returncode: int | None = None
    proc: subprocess.Popen[bytes] | None = None
    ended: float | None = None

    def info(self, since: int = 0) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "started": self.started, "ended": self.ended,
            "running": self.returncode is None, "returncode": self.returncode,
            "lines": self.lines[since:], "next": len(self.lines),
        }


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self._n = 0

    def start(self, kind: str, title: str, args: list[str], cwd: Path) -> Job:
        with self.lock:
            self._n += 1
            job = Job(f"j{self._n}", kind, title, args)
            self.jobs[job.id] = job
        kwargs: dict[str, Any] = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "env": child_env()}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        job.lines.append("$ " + " ".join(_quote(a) for a in args))
        try:
            job.proc = subprocess.Popen(args, **kwargs)
        except OSError as exc:
            job.lines.append(f"cannot start: {exc}")
            job.returncode = -1
            job.ended = time.time()
            return job
        threading.Thread(target=self._read, args=(job,), daemon=True).start()
        return job

    def _read(self, job: Job) -> None:
        assert job.proc is not None and job.proc.stdout is not None
        for raw in iter(job.proc.stdout.readline, b""):
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            # Progress lines rewritten with "\r" show their last state.
            text = text.split("\r")[-1]
            job.lines.append(text)
            if len(job.lines) > MAX_JOB_LINES:
                del job.lines[1:1000]
        job.returncode = job.proc.wait()
        job.ended = time.time()
        job.lines.append(f"[finished, exit code {job.returncode}]")

    def stop(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.proc is None or job.returncode is not None:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(job.proc.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(job.proc.pid, 15)
        except (OSError, ProcessLookupError):
            job.proc.kill()

    def stop_all(self) -> None:
        for job_id in list(self.jobs):
            self.stop(job_id)


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


# --------------------------------------------------------------------------- the panel


class Panel:
    def __init__(self, config: Path) -> None:
        self.config = config.resolve()
        self.token = secrets.token_urlsafe(24)
        self.jobs = Jobs()
        self.probe_pool = ThreadPoolExecutor(max_workers=2)
        self.probes: dict[str, dict[str, Any]] = {}  # file path -> result (or {"pending": True})
        self.last_seen = time.time()
        self.instance = "main"

    # -- configuration --------------------------------------------------------

    def read_config(self) -> dict[str, Any]:
        if not self.config.is_file():
            return {"path": str(self.config), "exists": False, "data": None, "text": ""}
        try:
            text = self.config.read_text(encoding="utf-8-sig")
            data = load_yaml(self.config)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return {"path": str(self.config), "exists": True, "data": None, "text": "", "error": f"cannot read: {exc}"}
        return {"path": str(self.config), "exists": True, "data": data, "text": text, "mtime": self.config.stat().st_mtime}

    def validate(self, data: Any) -> dict[str, Any]:
        try:
            cfg = parse_config(data, self.config)
        except ConfigError as exc:
            return {"ok": False, "problems": exc.problems, "warnings": []}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "problems": [f"{type(exc).__name__}: {exc}"], "warnings": []}
        return {"ok": True, "problems": [], "warnings": list(cfg.warnings), "cues": len(cfg.cues)}

    def save(self, data: Any, force: bool = False) -> dict[str, Any]:
        result = self.validate(data)
        if not result["ok"] and not force:
            return dict(result, saved=False)
        text = dump_yaml(data, self.config.name) if self.config.suffix.lower() != ".json" else json.dumps(data, indent=2)
        if self.config.is_file():
            shutil.copy2(self.config, self.config.with_name(self.config.name + ".bak"))
        tmp = self.config.with_name(self.config.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.config)
        return dict(result, saved=True, text=text, mtime=self.config.stat().st_mtime)

    def media_dir(self, data: Any) -> Path:
        show = (data or {}).get("show") or {}
        raw = show.get("media_dir", ".") if isinstance(show, dict) else "."
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else self.config.parent / p

    def media(self, data: Any) -> dict[str, Any]:
        folder = self.media_dir(data)
        files = []
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES:
                    st = path.stat()
                    rel = path.relative_to(folder).as_posix()
                    probe = self.probes.get(str(path))
                    if probe is not None and probe.get("mtime") != st.st_mtime:
                        probe = None
                    files.append({"name": rel, "size": st.st_size, "mtime": st.st_mtime, "probe": probe})
        return {"folder": str(folder), "exists": folder.is_dir(), "files": files}

    def probe(self, data: Any, names: list[str]) -> None:
        from aethra.mpvproc import resolve_mpv

        mpv_raw = str(((data or {}).get("mpv") or {}).get("path", "mpv"))
        if any(sep in mpv_raw for sep in "/\\"):
            p = Path(mpv_raw)
            mpv_raw = str(p if p.is_absolute() else self.config.parent / p)
        mpv = resolve_mpv(mpv_raw)
        folder = self.media_dir(data)
        for name in names:
            path = folder / name
            if not path.is_file():
                continue
            key = str(path)
            mtime = path.stat().st_mtime
            if self.probes.get(key, {}).get("mtime") == mtime:
                continue
            self.probes[key] = {"pending": True, "mtime": mtime}
            if mpv is None:
                self.probes[key] = {"mtime": mtime, "ok": False, "problems": ["mpv not found (mpv.path)"]}
                continue
            self.probe_pool.submit(self._probe_one, mpv, path, mtime)

    def _probe_one(self, mpv: str, path: Path, mtime: float) -> None:
        from aethra.media import verify_file

        try:
            r = verify_file(mpv, path, quick=True, timeout=120)
            self.probes[str(path)] = {
                "mtime": mtime, "ok": r.ok, "duration": r.duration, "fps": r.fps, "codec": r.codec,
                "width": r.width, "height": r.height, "problems": r.problems[:3],
            }
        except Exception as exc:  # noqa: BLE001
            self.probes[str(path)] = {"mtime": mtime, "ok": False, "problems": [str(exc)]}

    # -- devices ---------------------------------------------------------------

    def devices(self, backend: str = "auto") -> dict[str, Any]:
        from aethra.midi import MidiError, make_backend
        from aethra.stage import list_monitors

        midi: list[str] = []
        midi_error = None
        try:
            midi = make_backend(backend).list_inputs()
        except MidiError as exc:
            midi_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            midi_error = f"{type(exc).__name__}: {exc}"
        monitors = [
            {"index": m.index, "name": m.name, "left": m.left, "top": m.top, "width": m.width, "height": m.height, "primary": m.primary}
            for m in list_monitors()
        ]
        return {"midi": midi, "midi_error": midi_error, "monitors": monitors, "windows": IS_WINDOWS}

    def learn_note(self, data: Any, seconds: float = 10.0) -> dict[str, Any]:
        """Wait for one note-on (or program change) on the configured MIDI input."""
        from aethra.midi import MidiError, make_backend, match_port

        if self.show_status().get("running"):
            return {"error": "the show is running and holds the MIDI input; stop it first"}
        midi = (data or {}).get("midi") or {}
        try:
            backend = make_backend(str(midi.get("backend", "auto")))
            names = backend.list_inputs()
            index = match_port(names, str(midi.get("port") or "*"))
            if index is None:
                return {"error": f"MIDI input {midi.get('port')!r} not found ({', '.join(names) or 'no inputs'})"}
            got: dict[str, Any] = {}
            done = threading.Event()

            def on_message(raw: bytes, _t: float) -> None:
                if len(raw) >= 3 and raw[0] & 0xF0 == 0x90 and raw[2] > 0:
                    got.update(kind="note", value=raw[1], channel=(raw[0] & 0x0F) + 1)
                    done.set()
                elif len(raw) >= 2 and raw[0] & 0xF0 == 0xC0:
                    got.update(kind="program", value=raw[1], channel=(raw[0] & 0x0F) + 1)
                    done.set()

            port = backend.open(index, names[index], on_message)
            try:
                done.wait(seconds)
            finally:
                port.close()
            return got or {"error": f"nothing received in {seconds:.0f} s"}
        except MidiError as exc:
            return {"error": str(exc)}

    # -- the show --------------------------------------------------------------

    def show_status(self) -> dict[str, Any]:
        status_file = runtime_dir() / f"{self.instance}-status.json"
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"running": False}
        age = time.time() - float(data.get("updated", 0))
        alive = _pid_alive(data.get("pid"))
        data["running"] = alive and age < 10
        data["age"] = round(age, 1)
        return data

    def start_show(self, test_mode: bool, fresh: bool) -> dict[str, Any]:
        if self.show_status().get("running"):
            return {"error": "the show is already running"}
        extra = (["--test-mode"] if test_mode else []) + (["--fresh"] if fresh else [])
        launcher = install_root() / "Aethra.cmd"
        if IS_WINDOWS and launcher.is_file():
            # The normal launcher (restart loop) in its own console window.
            cmd = f'start "Aethra" /D "{install_root()}" cmd /c ""{launcher}" "{self.config}" {" ".join(extra)}"'
            subprocess.Popen(cmd, shell=True, cwd=str(install_root()), env=child_env(),
                             creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            args = [python_console(), "-X", "utf8", "-m", "aethra", "run", str(self.config), *extra]
            kwargs: dict[str, Any] = {"cwd": str(self.config.parent), "env": child_env()}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(args, **kwargs)
        return {"started": True}

    def osc(self, data: Any, address: str, *args: Any) -> dict[str, Any]:
        from aethra.osc import build_message

        osc = (data or {}).get("osc") or {}
        if not osc.get("enabled"):
            return {"error": "OSC is off in the show file (osc.enabled): the panel controls the show over OSC"}
        prefix = str(osc.get("prefix", "/aethra"))
        port = int(osc.get("port", 9000))
        payload = build_message(prefix + address, *args)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, ("127.0.0.1", port))
        return {"sent": prefix + address}

    def stop_show(self, data: Any) -> dict[str, Any]:
        first = self.osc(data, "/quit")
        if "error" in first:
            return first
        time.sleep(0.2)
        return self.osc(data, "/quit")

    # -- logs ------------------------------------------------------------------

    def logs_dir(self, data: Any) -> Path:
        raw = ((data or {}).get("logging") or {}).get("dir", "logs")
        p = Path(str(raw))
        return p if p.is_absolute() else self.config.parent / p

    def logs(self, data: Any) -> list[dict[str, Any]]:
        folder = self.logs_dir(data)
        if not folder.is_dir():
            return []
        out = []
        for path in sorted(folder.glob("aethra-*.jsonl"), reverse=True)[:200]:
            st = path.stat()
            out.append({"name": path.name, "size": st.st_size, "mtime": st.st_mtime})
        return out

    def log_events(self, data: Any, name: str | None, limit: int = 400, level: str = "info") -> dict[str, Any]:
        folder = self.logs_dir(data)
        files = sorted(folder.glob("aethra-*.jsonl")) if folder.is_dir() else []
        path = (folder / name) if name else (files[-1] if files else None)
        if path is None or not path.is_file() or path.parent.resolve() != folder.resolve():
            return {"name": None, "events": []}
        levels = {"debug": 0, "info": 1, "warn": 2, "warning": 2, "error": 3}
        floor = levels.get(level, 1)
        events = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if levels.get(str(rec.get("level")), 1) >= floor:
                    events.append({"wall": rec.get("wall"), "event": rec.get("event"), "level": rec.get("level"), "msg": rec.get("msg")})
        return {"name": path.name, "events": events[-limit:]}

    def report(self, data: Any, name: str) -> str:
        from aethra.report import build_report, read_log

        folder = self.logs_dir(data)
        path = folder / name
        if not path.is_file() or path.parent.resolve() != folder.resolve():
            return "log not found"
        return build_report(read_log(path))

    # -- jobs ------------------------------------------------------------------

    def start_job(self, kind: str, options: dict[str, Any]) -> Job:
        py = [python_console(), "-X", "utf8", "-m", "aethra"]
        config = str(self.config)
        cwd = self.config.parent
        if kind == "check":
            return self.jobs.start(kind, "Check configuration and devices", [*py, "check", config], cwd)
        if kind == "verify":
            args = [*py, "verify", config] + (["--quick"] if options.get("quick") else [])
            return self.jobs.start(kind, "Verify media files", args, cwd)
        if kind == "devices":
            return self.jobs.start(kind, "Devices", [*py, "devices"], cwd)
        if kind == "selftest":
            args = [*py, "selftest", config] + (["--windows"] if options.get("windows") else [])
            return self.jobs.start(kind, "Self-test", args, cwd)
        if kind == "monitor":
            return self.jobs.start(kind, "MIDI / OSC monitor", [*py, "monitor", config], cwd)
        if kind == "rehearse":
            args = [*py, "rehearse", config, "--loops", str(int(options.get("loops", 1))), "--gap", str(float(options.get("gap", 5)))]
            if options.get("max_clip"):
                args += ["--max-clip", str(float(options["max_clip"]))]
            return self.jobs.start(kind, "Rehearsal", args, cwd)
        if kind == "bench":
            files = [str(f) for f in options.get("files") or []]
            if not files:
                raise ValueError("no files selected")
            args = [*py, "bench", *files, "--config", config, "--seconds", str(float(options.get("seconds", 30)))]
            for mode in options.get("hwdec") or []:
                args += ["--hwdec", str(mode)]
            return self.jobs.start(kind, "Codec benchmark", args, cwd)
        if kind == "make_clips":
            script = install_root() / "tools" / "make_test_clips.py"
            what = str(options.get("what", "codecs"))
            if not script.is_file():
                raise ValueError("tools/make_test_clips.py is not part of this installation")
            args = [python_console(), "-X", "utf8", str(script), what, "--out", str(options.get("out") or "test-clips")]
            if options.get("ffmpeg"):
                args += ["--ffmpeg", str(options["ffmpeg"])]
            if options.get("seconds") and what != "demo":
                args += ["--seconds", str(float(options["seconds"]))]
            return self.jobs.start(kind, "Make test clips", args, cwd)
        if kind == "windows":
            script = _windows_script()
            if script is None:
                raise ValueError("prepare-stage-laptop.ps1 not found")
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            mode = options.get("mode", "check")
            if mode == "apply":
                args.append("-Apply")
            elif mode == "revert":
                args.append("-Revert")
            if mode != "revert":
                if options.get("scope") in ("User", "Machine"):
                    args += ["-Scope", str(options["scope"])]
                for flag, name in (("battery", "-IncludeBattery"), ("high_performance", "-HighPerformancePlan"), ("fast_startup", "-DisableFastStartup")):
                    if options.get(flag):
                        args.append(name)
                if options.get("osc_port") is not None:
                    args += ["-OscPort", str(int(options["osc_port"]))]
            if options.get("elevated"):
                # An administrator prompt opens its own window; its output is not captured here.
                inner = " ".join(_quote(a) for a in args[1:])
                args = ["powershell", "-NoProfile", "-Command",
                        f"Start-Process powershell -Verb RunAs -ArgumentList '{inner} ; Read-Host \"Done - press Enter\"'"]
            return self.jobs.start(kind, "Windows preparation" + (" (apply)" if mode == "apply" else " (revert)" if mode == "revert" else " (check)"), args, install_root())
        if kind == "shortcuts":
            script = _script("install-shortcuts.ps1")
            if script is None:
                raise ValueError("install-shortcuts.ps1 not found")
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Config", self.config.name]
            if options.get("autostart"):
                args += ["-Autostart", "-DelaySeconds", str(int(options.get("delay", 20)))]
            if options.get("remove_autostart"):
                args.append("-RemoveAutostart")
            return self.jobs.start(kind, "Shortcuts", args, install_root())
        raise ValueError(f"unknown job {kind!r}")

    # -- static facts for the page ---------------------------------------------

    def meta(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "windows": IS_WINDOWS,
            "install_root": str(install_root()),
            "commands": [c for c in COMMANDS],
            "default_keys": {k: list(v) for k, v in DEFAULT_KEYS.items()},
            "transition_types": list(TRANSITION_TYPES),
            "basic_transitions": list(BASIC_TRANSITIONS),
            "blend_modes": [m for m in BLEND_MODES if m != "normal"],
            "windows_script": str(_windows_script() or ""),
            "test_clips": str(self.config.parent / "test-clips"),
        }


def _script(name: str) -> Path | None:
    for candidate in (install_root() / "scripts" / name, install_root() / "scripts" / "windows" / name):
        if candidate.is_file():
            return candidate
    return None


def _windows_script() -> Path | None:
    return _script("prepare-stage-laptop.ps1")


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return False
    if IS_WINDOWS:
        from aethra import winapi

        return winapi.process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    panel: Panel
    server_version = "AethraPanel/" + __version__

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode(), "application/json; charset=utf-8")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Aethra-Token", ""), self.panel.token)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.panel.token)
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if url.path.startswith("/static/"):
            target = (STATIC / url.path[len("/static/"):]).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
            return
        if not url.path.startswith("/api/") or not self._authorised():
            self._send(403, b"forbidden", "text/plain")
            return
        self.panel.last_seen = time.time()
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            self._json(self.route_get(url.path, q))
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not url.path.startswith("/api/") or not self._authorised():
            self._send(403, b"forbidden", "text/plain")
            return
        self.panel.last_seen = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(self.route_post(url.path, body))
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def route_get(self, path: str, q: dict[str, str]) -> Any:
        p = self.panel
        if path == "/api/meta":
            return p.meta()
        if path == "/api/config":
            return p.read_config()
        if path == "/api/status":
            return p.show_status()
        if path == "/api/jobs":
            return [j.info(len(j.lines)) for j in p.jobs.jobs.values()]
        if path.startswith("/api/jobs/"):
            job = p.jobs.jobs.get(path.rsplit("/", 1)[1])
            return job.info(int(q.get("since", 0))) if job else {"error": "no such job"}
        raise ValueError(f"unknown {path}")

    def route_post(self, path: str, body: dict[str, Any]) -> Any:
        p = self.panel
        data = body.get("data")
        if path == "/api/validate":
            return p.validate(data)
        if path == "/api/save":
            return p.save(data, bool(body.get("force")))
        if path == "/api/open":
            target = Path(str(body["path"])).expanduser()
            if not target.is_absolute():
                target = install_root() / target
            if body.get("create") and not target.exists():
                example = _script_example()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(example, encoding="utf-8")
            p.config = target.resolve()
            return p.read_config()
        if path == "/api/media":
            return p.media(data)
        if path == "/api/probe":
            p.probe(data, list(body.get("files") or []))
            return p.media(data)
        if path == "/api/devices":
            return p.devices(str(body.get("backend", "auto")))
        if path == "/api/learn":
            return p.learn_note(data, float(body.get("seconds", 10)))
        if path == "/api/show/start":
            return p.start_show(bool(body.get("test_mode")), bool(body.get("fresh")))
        if path == "/api/show/stop":
            return p.stop_show(data)
        if path == "/api/show/command":
            args = body.get("args") or []
            return p.osc(data, "/" + str(body["name"]).lstrip("/"), *args)
        if path == "/api/logs":
            return p.logs(data)
        if path == "/api/log":
            return p.log_events(data, body.get("name"), int(body.get("limit", 400)), str(body.get("level", "info")))
        if path == "/api/report":
            return {"text": p.report(data, str(body["name"]))}
        if path == "/api/jobs":
            job = p.start_job(str(body["kind"]), dict(body.get("options") or {}))
            return job.info()
        if path == "/api/jobs/stop":
            p.jobs.stop(str(body["id"]))
            return {"ok": True}
        if path == "/api/browse":
            return _browse(Path(str(body.get("path") or install_root())), body.get("suffixes"))
        raise ValueError(f"unknown {path}")


def _script_example() -> str:
    for candidate in (install_root() / "examples" / "show.example.yaml", install_root() / "show.example.yaml"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return 'show:\n  name: "New show"\n  media_dir: media\nmidi:\n  enabled: false\ncues:\n  - {id: "01", file: 01.mp4}\n'


def _browse(folder: Path, suffixes: Any) -> dict[str, Any]:
    folder = folder.expanduser().resolve()
    if folder.is_file():
        folder = folder.parent
    wanted = {str(s).lower() for s in suffixes} if suffixes else None
    entries = []
    try:
        for item in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            if item.is_dir() or wanted is None or item.suffix.lower() in wanted:
                entries.append({"name": item.name, "dir": item.is_dir(), "path": str(item)})
    except OSError as exc:
        return {"path": str(folder), "parent": str(folder.parent), "entries": [], "error": str(exc)}
    return {"path": str(folder), "parent": str(folder.parent), "entries": entries}


# --------------------------------------------------------------------------- start


def _edge() -> str | None:
    if not IS_WINDOWS:
        return shutil.which("microsoft-edge") or shutil.which("chromium") or shutil.which("google-chrome")
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return str(exe)
    return None


def serve(config: Path, port: int = 0, open_window: bool = True) -> int:
    panel = Panel(config)
    Handler.panel = panel
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _say(f"Aethra control panel: {url}")
    window = None
    if open_window:
        edge = _edge()
        if edge:
            profile = runtime_dir() / "panel-browser"
            profile.mkdir(parents=True, exist_ok=True)
            # App mode: a window of its own without tabs or address bar.
            window = subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                                       "--no-default-browser-check", "--window-size=1440,920"])
        else:
            import webbrowser

            webbrowser.open(url)
    try:
        while True:
            time.sleep(1.0)
            if window is not None and window.poll() is not None and time.time() - panel.last_seen > 5:
                break  # the panel's window was closed
            if window is None and time.time() - panel.last_seen > 6 * 3600:
                break
    except KeyboardInterrupt:
        pass
    finally:
        panel.jobs.stop_all()
        httpd.shutdown()
    return 0


def _say(text: str) -> None:
    if sys.stdout is not None:  # pythonw has no console
        try:
            print(text, flush=True)
        except OSError:
            pass
