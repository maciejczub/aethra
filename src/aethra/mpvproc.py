"""Locating, probing and launching mpv."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from aethra.config import Config
from aethra.platform import IS_WINDOWS

_OPTION_RE = re.compile(r"^\s*--([A-Za-z0-9][A-Za-z0-9-]*)")


def resolve_mpv(path: str) -> str | None:
    """Full path of the mpv executable, or None if it cannot be found."""
    if any(sep in path for sep in ("/", "\\")):
        candidate = Path(path)
    else:
        found = shutil.which(path)
        if found is None:
            return None
        candidate = Path(found)
    if IS_WINDOWS and candidate.suffix.lower() == ".com":
        # mpv.com is a console wrapper around mpv.exe; always run mpv.exe.
        exe = candidate.with_suffix(".exe")
        if exe.is_file():
            candidate = exe
    return str(candidate) if candidate.is_file() else None


def _run(args: list[str], timeout: float = 15.0) -> str | None:
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout, "errors": "replace"}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(args, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


@lru_cache(maxsize=8)
def mpv_version(mpv: str) -> str | None:
    out = _run([mpv, "--no-config", "--version"])
    if not out:
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return first.split(" Copyright")[0].strip() or None


@lru_cache(maxsize=8)
def supported_options(mpv: str) -> frozenset[str] | None:
    out = _run([mpv, "--no-config", "--list-options"])
    if not out:
        return None
    names = {m.group(1) for line in out.splitlines() if (m := _OPTION_RE.match(line))}
    return frozenset(names) if len(names) > 50 else None


# Options added in recent mpv releases. mpv refuses to start on an unknown
# option, so these are only passed when ``--list-options`` confirmed them.
RECENT_OPTIONS = frozenset({
    "background-color", "media-controls", "focus-on", "input-builtin-bindings", "auto-window-resize",
    "load-select", "load-commands", "load-positioning", "load-context-menu", "osd-on-seek",
})


@dataclass
class DeckArgs:
    args: list[str]
    dropped: list[str]


def build_deck_args(
    mpv: str,
    cfg: Config,
    deck: str,
    ipc_address: str,
    log_file: Path | None,
    supported: frozenset[str] | None,
    vo_override: str | None = None,
) -> DeckArgs:
    stage = cfg.stage
    opts: list[tuple[str, str]] = [
        ("idle", "yes"),
        ("force-window", "immediate"),
        ("keep-open", "always"),
        ("fullscreen", "yes"),
        ("border", "no"),
        ("title", f"Aethra {deck}"),
        ("osc", "no"),
        ("osd-level", "0"),
        ("osd-bar", "no"),
        ("osd-on-seek", "no"),
        ("osd-font-size", "34"),
        ("osd-border-size", "2"),
        ("osd-align-x", "left"),
        ("osd-align-y", "top"),
        ("osd-fractions", "yes"),
        ("input-default-bindings", "no"),
        ("input-builtin-bindings", "no"),
        ("input-terminal", "no"),
        ("input-media-keys", "no"),
        ("input-vo-keyboard", "yes"),
        ("terminal", "no"),
        ("cursor-autohide", "always"),
        ("stop-screensaver", "yes"),
        ("audio", "no"),
        ("sub-auto", "no"),
        ("audio-file-auto", "no"),
        ("hwdec", cfg.mpv.hwdec),
        ("hr-seek", "yes"),
        ("image-display-duration", "inf"),
        ("background-color", "#000000"),
        ("window-dragging", "no"),
        ("taskbar-progress", "no"),
        ("media-controls", "no"),
        ("snap-window", "no"),
        ("auto-window-resize", "no"),
        ("focus-on", "never"),
        ("ytdl", "no"),
        ("load-scripts", "no"),
        ("load-stats-overlay", "no"),
        ("load-osd-console", "no"),
        ("load-auto-profiles", "no"),
        ("load-select", "no"),
        ("load-commands", "no"),
        ("load-positioning", "no"),
        ("load-context-menu", "no"),
        ("resume-playback", "no"),
        ("save-position-on-quit", "no"),
        ("input-ipc-server", ipc_address),
    ]
    if stage.screen_name:
        opts += [("screen-name", stage.screen_name), ("fs-screen-name", stage.screen_name)]
    elif stage.screen is not None:
        opts += [("screen", str(stage.screen)), ("fs-screen", str(stage.screen))]
    vo = vo_override or cfg.mpv.vo
    if vo:
        opts.append(("vo", vo))
    if log_file is not None:
        opts.append(("log-file", str(log_file)))

    args = [mpv, "--no-config"]
    dropped: list[str] = []
    for name, value in opts:
        if (name not in supported) if supported is not None else (name in RECENT_OPTIONS):
            dropped.append(name)
            continue
        args.append(f"--{name}={value}")
    args.extend(cfg.mpv.extra_args)
    return DeckArgs(args, dropped)


class MpvProcess:
    """An mpv process we started (``popen``) or adopted after a restart (``pid``)."""

    def __init__(self, popen: subprocess.Popen[bytes] | None = None, pid: int | None = None) -> None:
        self.popen = popen
        self.pid = popen.pid if popen is not None else pid
        self.in_job = False

    @classmethod
    def spawn(cls, args: list[str], priority: str = "above_normal") -> "MpvProcess":
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if IS_WINDOWS:
            flags = subprocess.CREATE_NO_WINDOW
            flags |= {
                "normal": subprocess.NORMAL_PRIORITY_CLASS,
                "above_normal": subprocess.ABOVE_NORMAL_PRIORITY_CLASS,
                "high": subprocess.HIGH_PRIORITY_CLASS,
            }.get(priority, subprocess.ABOVE_NORMAL_PRIORITY_CLASS)
            kwargs["creationflags"] = flags
        else:
            # Own session: Ctrl+C in the operator's terminal must not reach mpv.
            kwargs["start_new_session"] = True
        proc = cls(subprocess.Popen(args, **kwargs))
        if IS_WINDOWS:
            from aethra import winapi

            handle = getattr(proc.popen, "_handle", None)
            if handle is not None:
                proc.in_job = winapi.assign_to_deck_job(int(handle))
        return proc

    def alive(self) -> bool:
        if self.popen is not None:
            return self.popen.poll() is None
        if self.pid is None:
            return False
        return _pid_alive(self.pid)

    def kill(self) -> None:
        try:
            if self.popen is not None:
                self.popen.kill()
            elif self.pid is not None:
                os.kill(self.pid, signal.SIGTERM if IS_WINDOWS else signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def reap(self, timeout: float = 0.0) -> int | None:
        if self.popen is None:
            return None
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        from aethra import winapi as w

        return w.process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
