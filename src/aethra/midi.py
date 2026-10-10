"""MIDI input with automatic reconnection.

Backends:

* ``winmm`` (Windows, default): the classic Windows MIDI API through ctypes -
  no compiled dependency, works with USB interfaces, rtpMIDI and loopMIDI
  ports.
* ``rtmidi`` (any OS, optional): python-rtmidi, used for development on
  Linux/macOS.

Each message is timestamped with :func:`aethra.clock.now` the moment the
backend delivers it (on the backend's thread) and then handed to the asyncio
loop. A supervisor checks the port list every few seconds: a vanished port is
closed, a reappearing one is reopened, so unplugging and replugging a cable
in the middle of a show needs no operator action.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable
from typing import Protocol

from aethra.clock import now
from aethra.config import MidiConfig
from aethra.eventlog import EventLog

MessageHandler = Callable[[bytes, float], None]


class MidiError(Exception):
    pass


class MidiPort(Protocol):
    name: str

    def close(self) -> None: ...


class MidiBackend(Protocol):
    name: str

    def list_inputs(self) -> list[str]: ...
    def open(self, index: int, name: str, callback: MessageHandler) -> MidiPort: ...


def short_message_length(status: int) -> int:
    if status < 0x80:
        return 0
    if status < 0xF0:
        return 2 if (status & 0xF0) in (0xC0, 0xD0) else 3
    return {0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1}.get(status, 1)


# --------------------------------------------------------------------------
# WinMM (Windows)


# Ports whose handle could not be closed cleanly stay referenced for the rest
# of the process: WinMM may still call their callback, which must not be freed.
_UNCLOSED_PORTS: list[object] = []


class _WinMMPort:
    SYSEX_BUFFERS = 4
    SYSEX_SIZE = 1024

    def __init__(self, index: int, name: str, callback: MessageHandler) -> None:
        import ctypes

        from aethra import winapi as w

        self.name = name
        self._w = w
        self._ctypes = ctypes
        self._callback = callback
        self._handle = w.HANDLE()
        self._closing = False
        self._opened = False
        self._headers: list[object] = []
        self._buffers: list[object] = []
        self._requeue: list[int] = []
        self._requeue_lock = threading.Lock()
        self._requeue_event = threading.Event()
        self._requeue_thread: threading.Thread | None = None
        self._proc = w.MIDIINPROC(self._on_message)  # keep a reference: called from a WinMM thread
        result = w.midiInOpen(ctypes.byref(self._handle), index, ctypes.cast(self._proc, ctypes.c_void_p).value, 0, w.CALLBACK_FUNCTION)
        if result != w.MMSYSERR_NOERROR:
            raise MidiError(f"midiInOpen({name!r}) failed with error {result}")
        self._opened = True
        try:
            try:
                for _ in range(self.SYSEX_BUFFERS):
                    self._add_sysex_buffer()
            except MidiError:
                pass  # SysEx (MTC full frames) is optional
            # midiInAddBuffer must not be called from inside the callback.
            self._requeue_thread = threading.Thread(target=self._requeue_loop, name=f"midi-sysex-{index}", daemon=True)
            self._requeue_thread.start()
            if w.midiInStart(self._handle) != w.MMSYSERR_NOERROR:
                raise MidiError(f"midiInStart({name!r}) failed")
        except BaseException:
            self.close()
            raise

    def _add_sysex_buffer(self) -> None:
        ctypes, w = self._ctypes, self._w
        buf = ctypes.create_string_buffer(self.SYSEX_SIZE)
        hdr = w.MIDIHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = self.SYSEX_SIZE
        hdr.dwFlags = 0
        if w.midiInPrepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr)) != w.MMSYSERR_NOERROR:
            raise MidiError("midiInPrepareHeader failed")
        self._headers.append(hdr)
        self._buffers.append(buf)
        if w.midiInAddBuffer(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr)) != w.MMSYSERR_NOERROR:
            raise MidiError("midiInAddBuffer failed")

    def _on_message(self, handle: int, msg: int, instance: int, param1: int, param2: int) -> None:
        try:
            w = self._w
            if msg in (w.MIM_DATA, w.MIM_MOREDATA):
                t = now()
                status = param1 & 0xFF
                length = short_message_length(status)
                if length:
                    data = bytes([(param1 >> (8 * i)) & 0xFF for i in range(length)])
                    self._callback(data, t)
            elif msg in (w.MIM_LONGDATA, w.MIM_LONGERROR):
                if msg == w.MIM_LONGDATA:
                    t = now()
                    hdr = self._ctypes.cast(param1, self._ctypes.POINTER(w.MIDIHDR)).contents
                    if hdr.dwBytesRecorded and not self._closing:
                        self._callback(self._ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded), t)
                # Hand the buffer back (also after an error, or SysEx stops for good).
                if not self._closing:
                    with self._requeue_lock:
                        self._requeue.append(param1)
                    self._requeue_event.set()
        except Exception:
            pass  # never let an exception escape into the WinMM thread

    def _requeue_loop(self) -> None:
        ctypes, w = self._ctypes, self._w
        while not self._closing:
            self._requeue_event.wait(0.5)
            self._requeue_event.clear()
            with self._requeue_lock:
                pending, self._requeue = self._requeue, []
            for address in pending:
                with self._requeue_lock:
                    if self._closing:
                        return
                    hdr = ctypes.cast(address, ctypes.POINTER(w.MIDIHDR))
                    w.midiInAddBuffer(self._handle, hdr, ctypes.sizeof(w.MIDIHDR))

    def close(self) -> None:
        if not self._opened or self._closing:
            return
        w, ctypes = self._w, self._ctypes
        # Stop the requeue thread first, so no buffer is added after the reset.
        with self._requeue_lock:
            self._closing = True
        self._requeue_event.set()
        if self._requeue_thread is not None and self._requeue_thread is not threading.current_thread():
            self._requeue_thread.join(2.0)
        w.midiInStop(self._handle)
        w.midiInReset(self._handle)  # returns every SysEx buffer to us
        clean = True
        for hdr in self._headers:
            result = w.midiInUnprepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if result == w.MIDIERR_STILLPLAYING:
                w.midiInReset(self._handle)
                result = w.midiInUnprepareHeader(self._handle, ctypes.byref(hdr), ctypes.sizeof(hdr))
            clean = clean and result == w.MMSYSERR_NOERROR
        if w.midiInClose(self._handle) != w.MMSYSERR_NOERROR or not clean:
            _UNCLOSED_PORTS.append(self)
        self._opened = False


class WinMMBackend:
    name = "winmm"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise MidiError("the winmm backend is Windows-only")
        from aethra import winapi

        self._w = winapi

    def list_inputs(self) -> list[str]:
        import ctypes

        w = self._w
        names = []
        for i in range(w.midiInGetNumDevs()):
            caps = w.MIDIINCAPSW()
            if w.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == w.MMSYSERR_NOERROR:
                names.append(caps.szPname)
            else:
                names.append(f"<input {i}>")
        return names

    def open(self, index: int, name: str, callback: MessageHandler) -> MidiPort:
        return _WinMMPort(index, name, callback)


# --------------------------------------------------------------------------
# python-rtmidi (optional)


class _RtMidiPort:
    def __init__(self, midi_in: object, name: str) -> None:
        self.name = name
        self._in = midi_in

    def close(self) -> None:
        try:
            self._in.cancel_callback()  # type: ignore[attr-defined]
            self._in.close_port()  # type: ignore[attr-defined]
        except Exception:
            pass


class RtMidiBackend:
    name = "rtmidi"

    def __init__(self) -> None:
        try:
            import rtmidi
        except ImportError as exc:
            raise MidiError("python-rtmidi is not installed (pip install python-rtmidi)") from exc
        self._rtmidi = rtmidi
        self._probe = rtmidi.MidiIn()

    def list_inputs(self) -> list[str]:
        return list(self._probe.get_ports())

    def open(self, index: int, name: str, callback: MessageHandler) -> MidiPort:
        midi_in = self._rtmidi.MidiIn()
        midi_in.ignore_types(sysex=False, timing=True, active_sense=True)

        def on_message(event: tuple[list[int], float], _data: object = None) -> None:
            t = now()
            try:
                callback(bytes(event[0]), t)
            except Exception:
                pass

        midi_in.open_port(index)
        midi_in.set_callback(on_message)
        return _RtMidiPort(midi_in, name)


def make_backend(kind: str) -> MidiBackend:
    if kind == "auto":
        kind = "winmm" if sys.platform == "win32" else "rtmidi"
    if kind == "winmm":
        return WinMMBackend()
    if kind == "rtmidi":
        return RtMidiBackend()
    raise MidiError(f"unknown MIDI backend {kind!r}")


def match_port(names: list[str], wanted: str) -> int | None:
    if wanted == "*":
        return 0 if names else None
    for i, name in enumerate(names):
        if name == wanted:
            return i
    lowered = wanted.lower()
    for i, name in enumerate(names):
        if lowered in name.lower():
            return i
    return None


# --------------------------------------------------------------------------
# Supervisor


class MidiInput:
    def __init__(
        self,
        cfg: MidiConfig,
        log: EventLog,
        on_message: MessageHandler,
        on_status: Callable[[str], None] | None = None,
        backend: MidiBackend | None = None,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.on_message = on_message
        self.on_status = on_status
        self.backend = backend
        self.port: MidiPort | None = None
        self.messages = 0
        self._open_names: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None

    def _deliver(self, data: bytes, t: float) -> None:
        # Backend thread -> asyncio loop. ``t`` was taken on arrival.
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._dispatch, data, t)

    def _dispatch(self, data: bytes, t: float) -> None:
        self.messages += 1
        self.on_message(data, t)

    def _status(self, text: str) -> None:
        if self.on_status is not None:
            self.on_status(text)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.backend is None:
            try:
                self.backend = make_backend(self.cfg.backend)
            except MidiError as exc:
                self.log.error("midi", f"MIDI unavailable: {exc}")
                self._status("unavailable")
                return
        self._task = asyncio.create_task(self._supervise(), name="midi-supervisor")

    async def _supervise(self) -> None:
        assert self.backend is not None
        announced_missing = False
        while True:
            try:
                names = await asyncio.to_thread(self.backend.list_inputs)
            except Exception as exc:
                self.log.warn("midi", f"cannot list MIDI inputs: {exc}")
                names = []
            if self.port is not None and self.port.name not in names:
                self.log.error("midi_lost", f"MIDI input {self.port.name!r} disappeared")
                await self._close_port()
            elif self.port is not None and names != self._open_names:
                # Devices were added or removed (possibly ours, unplugged and
                # replugged between two checks): reopen to get a live handle.
                self.log.info("midi_reopen", "MIDI device list changed; reopening the input")
                await self._close_port()
            if self.port is None:
                index = match_port(names, self.cfg.port)
                if index is None:
                    if not announced_missing:
                        self.log.warn("midi_missing", f"MIDI input matching {self.cfg.port!r} not found (available: {', '.join(names) or 'none'}); waiting for it")
                        announced_missing = True
                    self._status("waiting")
                else:
                    try:
                        self.port = await asyncio.to_thread(self.backend.open, index, names[index], self._deliver)
                    except Exception as exc:
                        self.log.error("midi", f"cannot open MIDI input {names[index]!r}: {exc}")
                        self._status("error")
                    else:
                        announced_missing = False
                        self._open_names = list(names)
                        self.log.info("midi_open", f"MIDI input {names[index]!r} open (channel {self.cfg.channel or 'any'})")
                        self._status("ok")
            await asyncio.sleep(self.cfg.reconnect_interval)

    async def _close_port(self) -> None:
        port, self.port = self.port, None
        if port is not None:
            try:
                await asyncio.wait_for(asyncio.to_thread(port.close), 5.0)
            except Exception:
                pass
        self._status("lost")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_port()


def describe_message(data: bytes) -> str:
    """Human-readable form for ``aethra monitor``."""
    if not data:
        return "(empty)"
    status = data[0]
    if status == 0xF1 and len(data) > 1:
        return f"MTC quarter frame piece {data[1] >> 4} value {data[1] & 0x0F}"
    if status == 0xF0:
        return "SysEx " + data.hex(" ")
    if status >= 0xF0:
        return {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop", 0xFE: "active sensing", 0xFF: "reset"}.get(status, f"system {status:02X}")
    kind, channel = status & 0xF0, (status & 0x0F) + 1
    if kind == 0x90 and len(data) >= 3:
        return f"ch {channel} note-{'on' if data[2] else 'off'} {data[1]} vel {data[2]}"
    if kind == 0x80 and len(data) >= 3:
        return f"ch {channel} note-off {data[1]}"
    if kind == 0xC0 and len(data) >= 2:
        return f"ch {channel} program {data[1]}"
    if kind == 0xB0 and len(data) >= 3:
        return f"ch {channel} cc {data[1]} = {data[2]}"
    return f"ch {channel} " + data.hex(" ")
