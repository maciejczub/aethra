"""Manual control from the keyboard: the safety net when triggers fail.

Three paths deliver the same commands:

* global hotkeys (Windows, ``RegisterHotKey``) - work no matter which window
  has focus; the bound keys are reserved for Aethra while it runs;
* the operator console (the terminal Aethra runs in);
* key bindings inside the stage windows (mpv ``keybind``), set up by the decks.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

from aethra.eventlog import EventLog
from aethra.keys import ANSI_SEQUENCES, KeySpec, key_from_char, key_from_msvcrt, parse_key_spec

Post = Callable[[str, str], None]  # (command, source)


def bindings_from(keys: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """canonical key spec -> command"""
    table: dict[str, str] = {}
    for command, specs in keys.items():
        for spec in specs:
            table[parse_key_spec(spec).canonical] = command
    return table


class GlobalHotkeys:
    def __init__(self, keys: dict[str, tuple[str, ...]], post: Post, log: EventLog) -> None:
        self.post = post
        self.log = log
        self.specs: list[tuple[KeySpec, str]] = []
        for command, specs in keys.items():
            for spec in specs:
                self.specs.append((parse_key_spec(spec), command))
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered: list[str] = []
        self.failed: list[str] = []

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        if self.registered:
            self.log.info("hotkeys", f"global hotkeys: {', '.join(self.registered)}")
        if self.failed:
            self.log.warn("hotkeys", f"could not register (used by another program?): {', '.join(self.failed)}")

    def _run(self) -> None:
        import ctypes

        from aethra import winapi as w

        msg = w.MSG()
        w.PeekMessageW(ctypes.byref(msg), None, 0, 0, w.PM_NOREMOVE)  # create the message queue
        self._thread_id = w.GetCurrentThreadId()
        ids: dict[int, tuple[KeySpec, str]] = {}
        for i, (spec, command) in enumerate(self.specs, start=1):
            if w.RegisterHotKey(None, i, spec.win_modifiers | w.MOD_NOREPEAT, spec.vk):
                ids[i] = (spec, command)
                self.registered.append(f"{spec.canonical}={command}")
            else:
                self.failed.append(spec.canonical)
        self._ready.set()
        try:
            while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == w.WM_HOTKEY and msg.wParam in ids:
                    spec, command = ids[msg.wParam]
                    self.post(command, f"hotkey {spec.canonical}")
        finally:
            for i in ids:
                w.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if sys.platform != "win32" or not self._thread_id:
            return
        from aethra import winapi as w

        w.PostThreadMessageW(self._thread_id, w.WM_QUIT, 0, 0)


class ConsoleKeys:
    def __init__(self, keys: dict[str, tuple[str, ...]], post: Post, log: EventLog) -> None:
        self.table = bindings_from(keys)
        self.post = post
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._restore: Callable[[], None] | None = None

    def start(self) -> bool:
        try:
            interactive = sys.stdin is not None and sys.stdin.isatty()
        except (OSError, ValueError):
            interactive = False
        if not interactive:
            return False
        target = self._run_windows if sys.platform == "win32" else self._run_posix
        self._thread = threading.Thread(target=target, name="console-keys", daemon=True)
        self._thread.start()
        return True

    def _emit(self, spec: KeySpec | None) -> None:
        if spec is None:
            return
        if spec.canonical == "ctrl+c" and spec.canonical not in self.table:
            # On Windows Ctrl+C arrives as a key (see winapi.ConsoleInputMode).
            self.post("quit", "Ctrl+C")
            return
        command = self.table.get(spec.canonical)
        if command is not None:
            self.post(command, f"console {spec.canonical}")

    def _run_windows(self) -> None:
        import msvcrt

        while not self._stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.01)
                continue
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                self._emit(key_from_msvcrt(ch, msvcrt.getwch()))
            else:
                self._emit(key_from_msvcrt(None, ch))

    def _run_posix(self) -> None:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        try:
            saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)  # keeps ISIG: Ctrl+C still reaches the controller
        except termios.error:
            return
        self._restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                data = os.read(fd, 64).decode("utf-8", "ignore")
                i = 0
                while i < len(data):
                    if data[i] == "\x1b" and i + 1 < len(data):
                        for seq, name in ANSI_SEQUENCES.items():
                            if data.startswith(seq, i):
                                self._emit(KeySpec(name, frozenset()))
                                i += len(seq)
                                break
                        else:
                            self._emit(key_from_char(data[i]))
                            i += 1
                        continue
                    self._emit(key_from_char(data[i]))
                    i += 1
        finally:
            self.restore()

    def restore(self) -> None:
        if self._restore is not None:
            try:
                self._restore()
            except Exception:
                pass
            self._restore = None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(0.5)
        self.restore()
