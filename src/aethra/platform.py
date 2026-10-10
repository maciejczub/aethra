"""Process-level setup and OS integration that is not part of the show logic."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO

IS_WINDOWS = sys.platform == "win32"


class ProcessSetup:
    """Applies (and on exit reverts) process-wide settings for a show session."""

    def __init__(self, priority: str = "high", console: bool = True) -> None:
        self.priority = priority
        self.console = console
        self.notes: list[str] = []
        self._timer = False
        self._console_mode = None

    def apply(self) -> None:
        if not IS_WINDOWS:
            return
        from aethra import winapi as w

        # No "program has stopped working" or "insert disk" dialogs - ever.
        # The error mode is inherited by the mpv processes we start.
        w.SetErrorMode(w.SEM_FAILCRITICALERRORS | w.SEM_NOGPFAULTERRORBOX | w.SEM_NOOPENFILEERRORBOX)
        # 1 ms timer resolution for accurate scheduling of delayed cue starts,
        # also while our windows are covered (Windows 11 throttles that otherwise).
        if w.timeBeginPeriod(1) == 0:
            self._timer = True
        if not w.disable_power_throttling():
            self.notes.append("power throttling opt-out not available (Windows 10?)")
        if self.console:
            mode = w.ConsoleInputMode()
            if mode.apply():
                self._console_mode = mode
        # Keep the system and the displays awake for the whole session.
        w.SetThreadExecutionState(w.ES_CONTINUOUS | w.ES_SYSTEM_REQUIRED | w.ES_DISPLAY_REQUIRED)
        w.set_dpi_awareness()
        cls = {"normal": w.NORMAL_PRIORITY_CLASS, "above_normal": w.ABOVE_NORMAL_PRIORITY_CLASS, "high": w.HIGH_PRIORITY_CLASS}
        if not w.SetPriorityClass(w.GetCurrentProcess(), cls.get(self.priority, w.HIGH_PRIORITY_CLASS)):
            self.notes.append("could not raise process priority")

    def revert(self) -> None:
        if not IS_WINDOWS:
            return
        from aethra import winapi as w

        if self._timer:
            w.timeEndPeriod(1)
            self._timer = False
        if self._console_mode is not None:
            self._console_mode.restore()
            self._console_mode = None
        w.SetThreadExecutionState(w.ES_CONTINUOUS)


class SingleInstance:
    """Advisory lock file: a second controller on the same machine refuses to start."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


_console_handler = None  # keep the ctypes callback alive


def on_console_close(callback: Callable[[], None], grace: float = 4.0) -> None:
    """Windows: run ``callback`` when the console window is closed (or on logoff/shutdown).

    Windows terminates the process ~5 s after the handler is entered, so the
    handler waits ``grace`` seconds to give the show a chance to shut down
    cleanly (stopping the mpv decks instead of leaving them orphaned).
    """
    global _console_handler
    if not IS_WINDOWS:
        return
    import ctypes
    import time
    from ctypes import wintypes

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(event: int) -> bool:
        if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            try:
                callback()
            except Exception:
                pass
            time.sleep(grace)
            return True
        return False  # Ctrl+C / Ctrl+Break: Python's own handling

    _console_handler = handler_type(handler)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetConsoleCtrlHandler.argtypes = (handler_type, wintypes.BOOL)
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel32.SetConsoleCtrlHandler(_console_handler, True)


def process_cpu_seconds(pid: int) -> float | None:
    """Total user+kernel CPU time of a process (for codec benchmarks)."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        from aethra import winapi as w

        handle = w.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
            k32.GetProcessTimes.restype = wintypes.BOOL
            if not k32.GetProcessTimes(handle, *(ctypes.byref(f) for f in ft)):
                return None

            def seconds(f: wintypes.FILETIME) -> float:
                return ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7

            return seconds(ft[2]) + seconds(ft[3])
        finally:
            w.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        return (int(fields[11]) + int(fields[12])) / ticks
    except (OSError, ValueError, IndexError):
        return None


def runtime_dir() -> Path:
    """Where lock files and POSIX IPC sockets live."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
        return Path(base) / "Aethra"
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / "aethra"
    import tempfile

    return Path(tempfile.gettempdir()) / f"aethra-{os.getuid()}"


def ipc_address(instance: str, deck: str) -> str:
    if IS_WINDOWS:
        return rf"\\.\pipe\aethra-{instance}-{deck}"
    path = runtime_dir() / f"{instance}-{deck}.sock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(path)
