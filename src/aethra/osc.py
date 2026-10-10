"""Minimal, defensive OSC 1.0 implementation (UDP only).

Parsing is strict about framing (sizes, padding) and lenient about content:
unknown type tags make the message unparseable, but never raise anything other
than :class:`OscError`, so one malformed datagram cannot disturb the show.
Bundles are flattened and their time tags ignored (messages run immediately).
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aethra.clock import now


class OscError(ValueError):
    pass


@dataclass(frozen=True)
class OscMessage:
    address: str
    args: tuple[Any, ...] = ()


_BUNDLE_TAG = b"#bundle\x00"
_MAX_DEPTH = 8


def _read_string(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise OscError("unterminated string")
    text = data[pos:end].decode("utf-8", "replace")
    new_pos = pos + ((end - pos) // 4 + 1) * 4
    if new_pos > len(data):
        raise OscError("string padding runs past the end")
    return text, new_pos


def _read_blob(data: bytes, pos: int) -> tuple[bytes, int]:
    if pos + 4 > len(data):
        raise OscError("truncated blob size")
    (size,) = struct.unpack_from(">i", data, pos)
    pos += 4
    if size < 0 or pos + size > len(data):
        raise OscError("bad blob size")
    blob = data[pos : pos + size]
    pos += (size + 3) & ~3
    if pos > len(data):
        raise OscError("blob padding runs past the end")
    return blob, pos


_FIXED = {"i": (">i", 4), "f": (">f", 4), "h": (">q", 8), "t": (">Q", 8), "d": (">d", 8), "c": (">i", 4), "r": (">I", 4)}


def parse_message(data: bytes) -> OscMessage:
    address, pos = _read_string(data, 0)
    if not address.startswith("/"):
        raise OscError(f"bad address {address!r}")
    if pos >= len(data):
        return OscMessage(address)  # no type tag string (OSC 1.0 allows it)
    tags, pos = _read_string(data, pos)
    if not tags.startswith(","):
        raise OscError("missing type tag string")
    args: list[Any] = []
    for tag in tags[1:]:
        if tag in _FIXED:
            fmt, size = _FIXED[tag]
            if pos + size > len(data):
                raise OscError(f"truncated argument {tag!r}")
            (value,) = struct.unpack_from(fmt, data, pos)
            pos += size
            if tag == "c":
                value = chr(value) if 0 <= value < 0x110000 else ""
            args.append(value)
        elif tag in "sS":
            value, pos = _read_string(data, pos)
            args.append(value)
        elif tag == "b":
            value, pos = _read_blob(data, pos)
            args.append(value)
        elif tag == "m":
            if pos + 4 > len(data):
                raise OscError("truncated MIDI argument")
            args.append(bytes(data[pos : pos + 4]))
            pos += 4
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag in "NI":
            args.append(None)
        elif tag in "[]":
            continue  # arrays are flattened
        else:
            raise OscError(f"unsupported type tag {tag!r}")
    return OscMessage(address, tuple(args))


def parse_packet(data: bytes, _depth: int = 0) -> list[OscMessage]:
    if _depth > _MAX_DEPTH:
        raise OscError("bundles nested too deeply")
    if len(data) < 4 or len(data) % 4:
        raise OscError("packet size is not a multiple of 4")
    if data.startswith(_BUNDLE_TAG):
        if len(data) < 16:
            raise OscError("truncated bundle")
        pos = 16  # tag + 8-byte time tag (ignored)
        messages: list[OscMessage] = []
        while pos < len(data):
            if pos + 4 > len(data):
                raise OscError("truncated bundle element size")
            (size,) = struct.unpack_from(">i", data, pos)
            pos += 4
            if size <= 0 or size % 4 or pos + size > len(data):
                raise OscError("bad bundle element size")
            messages.extend(parse_packet(data[pos : pos + size], _depth + 1))
            pos += size
        return messages
    return [parse_message(data)]


def _pad(raw: bytes) -> bytes:
    return raw + b"\x00" * (4 - len(raw) % 4)


def build_message(address: str, *args: Any) -> bytes:
    """Encode a message (used by tests and the ``aethra send`` helper)."""
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "T" if arg else "F"
        elif arg is None:
            tags += "N"
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags += "s"
            payload += _pad(arg.encode("utf-8"))
        elif isinstance(arg, (bytes, bytearray)):
            tags += "b"
            payload += struct.pack(">i", len(arg)) + bytes(arg) + b"\x00" * (-len(arg) % 4)
        else:
            raise TypeError(f"cannot encode {type(arg).__name__} as OSC")
    return _pad(address.encode("utf-8")) + _pad(tags.encode("ascii")) + payload


def build_bundle(*elements: bytes) -> bytes:
    out = _BUNDLE_TAG + struct.pack(">Q", 1)
    for element in elements:
        out += struct.pack(">i", len(element)) + element
    return out


# --------------------------------------------------------------------------
# UDP server

OscHandler = Callable[[OscMessage, tuple[str, int], float], None]
ErrorHandler = Callable[[str, tuple[str, int]], None]


class OscProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_message: OscHandler, on_error: ErrorHandler | None = None) -> None:
        self.on_message = on_message
        self.on_error = on_error
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        t = now()
        try:
            messages = parse_packet(data)
        except OscError as exc:
            if self.on_error:
                self.on_error(f"malformed OSC packet ({exc})", addr)
            return
        for message in messages:
            self.on_message(message, addr, t)

    def error_received(self, exc: Exception) -> None:  # e.g. ICMP port unreachable on Windows
        if self.on_error:
            self.on_error(f"socket error: {exc}", ("", 0))


async def start_osc_server(bind: str, port: int, on_message: OscHandler, on_error: ErrorHandler | None = None) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: OscProtocol(on_message, on_error), local_addr=(bind, port)
    )
    return transport  # type: ignore[return-value]
