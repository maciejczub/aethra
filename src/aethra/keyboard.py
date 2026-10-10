"""Manual control from the keyboard: the safety net when triggers fail.

Three paths deliver the same commands:

* global hotkeys (Windows, ``RegisterHotKey``) - work no matter which window
  has focus; the bound keys are reserved for Aethra while it runs - except
  while the control panel has the focus: then the keys that type (no Ctrl, Alt
  or Win) are released for typing, and the panel handles them itself;
* the operator console (the terminal Aethra runs in);
* key bindings inside the stage windows (mpv ``keybind``), set up by the decks.
"""

from __future__ import annotations

import asyncio
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


def types_text(spec: KeySpec) -> bool:
    """A key that produces text or moves a cursor (no Ctrl, Alt or Win)."""
    return not spec.mods & {"ctrl", "alt", "win"}


class GlobalHotkeys:
    WM_SUSPEND = 0x8000 + 1  # WM_APP + 1
    WM_RESUME = 0x8000 + 2

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
        self.suspended = False

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
        released: set[int] = set()
        try:
            while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == w.WM_HOTKEY and msg.wParam in ids:
                    spec, command = ids[msg.wParam]
                    self.post(command, f"hotkey {spec.canonical}")
                elif msg.message == self.WM_SUSPEND:
                    # Hotkeys can only be released by the thread that holds them.
                    for i, (spec, _command) in ids.items():
                        if types_text(spec) and i not in released:
                            w.UnregisterHotKey(None, i)
                            released.add(i)
                elif msg.message == self.WM_RESUME:
                    lost = []
                    for i in sorted(released):
                        spec, _command = ids[i]
                        if not w.RegisterHotKey(None, i, spec.win_modifiers | w.MOD_NOREPEAT, spec.vk):
                            lost.append(spec.canonical)
                    released.clear()
                    if lost:
                        self.log.warn("hotkeys", f"could not take these keys back (used by another program now?): {', '.join(lost)}")
        finally:
            for i in ids:
                if i not in released:
                    w.UnregisterHotKey(None, i)

    def _post(self, message: int) -> None:
        if sys.platform != "win32" or not self._thread_id:
            return
        from aethra import winapi as w

        w.PostThreadMessageW(self._thread_id, message, 0, 0)

    def suspend_typing_keys(self) -> None:
        """Let the keys that type reach the focused program (the control panel)."""
        self.suspended = True
        self._post(self.WM_SUSPEND)

    def resume_typing_keys(self) -> None:
        self.suspended = False
        self._post(self.WM_RESUME)

    def stop(self) -> None:
        if sys.platform != "win32" or not self._thread_id:
            return
        from aethra import winapi as w

        self._post(w.WM_QUIT)


class PanelFocus:
    """While the control panel has the keyboard focus, the keys that type are
    released from the global hotkeys, so that typing in the panel never starts
    a cue or blacks out the stage; the panel handles those keys itself outside
    its input fields. The panel renews this every second: if it stops (closed,
    crashed), the keys come back after ``LEASE`` seconds."""

    LEASE = 2.5

    def __init__(self, hotkeys: GlobalHotkeys, log: EventLog) -> None:
        self.hotkeys = hotkeys
        self.log = log
        self.active = False
        self._expiry: asyncio.TimerHandle | None = None

    def update(self, focused: bool) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        if focused:
            if not self.active:
                self.active = True
                self.hotkeys.suspend_typing_keys()
                self.log.debug("hotkeys", "control panel focused: keys that type are free for typing")
            self._expiry = asyncio.get_running_loop().call_later(self.LEASE, self.update, False)
        elif self.active:
            self.active = False
            self.hotkeys.resume_typing_keys()
            self.log.debug("hotkeys", "control panel not focused: all hotkeys are global again")


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
