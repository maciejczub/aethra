"""Event log (JSON Lines, one file per session) and the operator console.

Every line in the log carries ``t`` (seconds on the session's high-resolution
monotonic clock), ``wall`` (local ISO time with microseconds) and ``event``.
The file is flushed after every line so that nothing is lost if the process
dies; ``tools/analyze_log.py`` turns it into a post-show report.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from aethra.clock import WallClock, now

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


class Console:
    """Prints log lines above a single status line that is redrawn in place.

    All writing happens on a separate thread: a console can block its writers
    (a selection in a Windows console, a paused terminal), and that must never
    stall the show. If the writer falls behind, lines are dropped and counted.
    """

    QUEUE_SIZE = 2000

    def __init__(self, stream: TextIO | None = None, verbose: bool = False) -> None:
        self.stream = stream or sys.stdout
        self.verbose = verbose
        try:
            self.interactive = bool(self.stream.isatty())
        except (AttributeError, OSError, ValueError):
            self.interactive = False
        self._status = ""
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=self.QUEUE_SIZE)
        self._dropped = 0
        self._thread = threading.Thread(target=self._writer, name="console", daemon=True)
        self._thread.start()

    def line(self, text: str) -> None:
        self._put("line", text)

    def status(self, text: str) -> None:
        if self.interactive:
            self._put("status", text)

    def clear_status(self) -> None:
        self._put("clear", "")

    def close(self, timeout: float = 2.0) -> None:
        """Write out what is queued (used at exit)."""
        try:
            self._queue.put(("stop", ""), timeout=timeout)
        except queue.Full:
            return
        self._thread.join(timeout)

    def _put(self, kind: str, text: str) -> None:
        try:
            self._queue.put_nowait((kind, text))
        except queue.Full:
            self._dropped += 1

    def _writer(self) -> None:
        while True:
            kind, text = self._queue.get()
            if kind == "stop":
                return
            try:
                if self._dropped:
                    dropped, self._dropped = self._dropped, 0
                    self._write_line(f"... {dropped} console lines dropped (see the log file)")
                if kind == "line":
                    self._write_line(text)
                elif kind == "status":
                    self._write_status(text)
                elif kind == "clear":
                    self._erase_status()
                    self._status = ""
                self.stream.flush()
            except (OSError, ValueError, UnicodeError):
                pass  # a broken console must never take the show down

    def _erase_status(self) -> None:
        if self.interactive and self._status:
            self.stream.write("\r" + " " * _width(self._status) + "\r")

    def _write_line(self, text: str) -> None:
        self._erase_status()
        self.stream.write(text + "\n")
        if self.interactive and self._status:
            self.stream.write(self._status)

    def _write_status(self, text: str) -> None:
        # A status line that wraps cannot be redrawn with "\r": keep it to one line.
        columns = shutil.get_terminal_size((120, 24)).columns
        text = text[: max(20, columns - 1)]
        pad = max(0, _width(self._status) - _width(text))
        self.stream.write("\r" + text + " " * pad)
        self._status = text


def _width(text: str) -> int:
    return len(text)


class EventLog:
    def __init__(
        self,
        directory: Path | None,
        console: Console | None = None,
        wall: WallClock | None = None,
        session: str | None = None,
    ) -> None:
        self.wall = wall or WallClock()
        self.console = console
        self.t0 = self.wall.mono0
        self.session = session or _session_name(self.wall)
        self.path: Path | None = None
        self._file: TextIO | None = None
        self._lock = threading.Lock()
        if directory is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self.path = directory / f"aethra-{self.session}.jsonl"
                self._file = open(self.path, "a", encoding="utf-8", newline="\n")
            except OSError as exc:
                self._file = None
                if console:
                    console.line(f"!! cannot open event log in {directory}: {exc} (logging to console only)")

    def rel(self, t: float) -> float:
        return t - self.t0

    def event(self, kind: str, level: str = "info", msg: str | None = None, t: float | None = None, **fields: Any) -> None:
        if t is None:
            t = now()
        record: dict[str, Any] = {
            "t": round(t - self.t0, 6),
            "wall": self.wall.iso(t),
            "event": kind,
            "level": level,
        }
        if msg is not None:
            record["msg"] = msg
        record.update(fields)
        self._write(record)
        if self.console is not None:
            if LEVELS.get(level, 20) >= LEVELS["info"] or self.console.verbose:
                self.console.line(self._format(t, kind, level, msg, fields))

    def debug(self, kind: str, msg: str | None = None, **fields: Any) -> None:
        self.event(kind, "debug", msg, **fields)

    def info(self, kind: str, msg: str | None = None, **fields: Any) -> None:
        self.event(kind, "info", msg, **fields)

    def warn(self, kind: str, msg: str | None = None, **fields: Any) -> None:
        self.event(kind, "warn", msg, **fields)

    def error(self, kind: str, msg: str | None = None, **fields: Any) -> None:
        self.event(kind, "error", msg, **fields)

    def _write(self, record: dict[str, Any]) -> None:
        if self._file is None:
            return
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            line = json.dumps({k: str(v) for k, v in record.items()}, ensure_ascii=False)
        with self._lock:
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except (OSError, ValueError):
                pass

    def _format(self, t: float, kind: str, level: str, msg: str | None, fields: dict[str, Any]) -> str:
        mark = {"warn": "! ", "error": "!!", "debug": " ."}.get(level, "  ")
        text = msg if msg is not None else " ".join(f"{k}={v}" for k, v in fields.items())
        return f"{self.wall.hms(t)} {mark} {kind:<14} {text}"

    def fsync(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                except (OSError, ValueError):
                    pass

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None


def _session_name(wall: WallClock) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(wall.wall0).strftime("%Y%m%d-%H%M%S")
