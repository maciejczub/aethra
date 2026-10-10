"""Asynchronous client for mpv's JSON IPC.

Windows: mpv listens on a named pipe. Reading and writing a pipe at the same
time needs overlapped I/O (a blocking ReadFile would also block WriteFile on the
same handle), so the connection goes through asyncio's Proactor loop
(``create_pipe_connection``), which uses overlapped I/O. POSIX: a Unix socket.

Commands are written immediately and synchronously by :meth:`MpvIpc.request`
(the critical path never waits for anything); the returned future resolves to
the reply's ``data``. Every request has a timeout, so a hung mpv can never
block the controller.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from aethra.clock import now

STREAM_LIMIT = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 2.0


class MpvError(Exception):
    pass


class MpvDisconnected(MpvError):
    pass


class MpvTimeout(MpvError):
    pass


class MpvCommandError(MpvError):
    def __init__(self, command: Any, error: str) -> None:
        self.command = command
        self.error = error
        super().__init__(f"{_short(command)}: {error}")


def _short(command: Any) -> str:
    if isinstance(command, dict):
        return str(command.get("name", "?"))
    if isinstance(command, list) and command:
        head = " ".join(str(c) for c in command[:2])
        return head[:80]
    return str(command)[:80]


async def open_ipc_stream(address: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        create_pipe = getattr(loop, "create_pipe_connection", None)
        if create_pipe is None:
            raise RuntimeError("named pipes need asyncio's ProactorEventLoop (the Windows default)")
        reader = asyncio.StreamReader(limit=STREAM_LIMIT, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        transport, _ = await create_pipe(lambda: protocol, address)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer
    return await asyncio.open_unix_connection(address, limit=STREAM_LIMIT)


class MpvIpc:
    def __init__(
        self,
        address: str,
        name: str = "mpv",
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.address = address
        self.name = name
        self.on_event = on_event
        self.on_disconnect = on_disconnect
        self.on_error = on_error
        self.connected = False
        self.last_rx = 0.0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, tuple[asyncio.Future[Any], Any, asyncio.TimerHandle, Callable[[Any], None] | None]] = {}
        self._next_id = 1
        self._next_observe_id = 1
        self._closing = False

    # -- connection ----------------------------------------------------------

    async def connect(self, timeout: float = 5.0, retry_interval: float = 0.05) -> None:
        deadline = now() + timeout
        while True:
            try:
                self._reader, self._writer = await asyncio.wait_for(open_ipc_stream(self.address), timeout=2.0)
                break
            except (OSError, asyncio.TimeoutError) as exc:
                if now() >= deadline:
                    raise MpvDisconnected(f"cannot connect to {self.address}: {exc}") from exc
                await asyncio.sleep(retry_interval)
        self.connected = True
        self._closing = False
        self.last_rx = now()
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"ipc-{self.name}")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                self.last_rx = now()
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "event" in msg:
                    if self.on_event is not None:
                        try:
                            self.on_event(msg)
                        except Exception as exc:  # a handler bug must not kill the connection
                            if self.on_error:
                                self.on_error("event handler failed", exc)
                    continue
                pending = self._pending.pop(msg.get("request_id"), None)  # type: ignore[arg-type]
                if pending is None:
                    continue
                fut, command, timer, on_reply = pending
                timer.cancel()
                if fut.done():
                    continue
                error = msg.get("error", "success")
                if error == "success":
                    if on_reply is not None:
                        # Synchronous: runs before any later event is dispatched.
                        try:
                            on_reply(msg.get("data"))
                        except Exception as exc:
                            if self.on_error:
                                self.on_error("reply handler failed", exc)
                    fut.set_result(msg.get("data"))
                else:
                    fut.set_exception(MpvCommandError(command, str(error)))
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            self._lost()

    def _lost(self) -> None:
        was_connected = self.connected
        self.connected = False
        pending, self._pending = self._pending, {}
        for fut, _command, timer, _on_reply in pending.values():
            timer.cancel()
            if not fut.done():
                fut.set_exception(MpvDisconnected(f"{self.name}: connection closed"))
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        if was_connected and not self._closing and self.on_disconnect is not None:
            self.on_disconnect()

    async def close(self) -> None:
        self._closing = True
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._lost()

    # -- requests ------------------------------------------------------------

    def request(
        self,
        command: list[Any] | dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT,
        on_reply: Callable[[Any], None] | None = None,
    ) -> asyncio.Future[Any]:
        """Write ``command`` now; the future resolves to the reply data.

        ``on_reply`` is called synchronously with the data of a successful
        reply, before any event that mpv sent after the reply is dispatched.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        if not self.connected or self._writer is None:
            fut.set_exception(MpvDisconnected(f"{self.name}: not connected"))
            return fut
        rid = self._next_id
        self._next_id += 1
        payload = json.dumps({"command": command, "request_id": rid}, ensure_ascii=False, separators=(",", ":"))
        try:
            self._writer.write(payload.encode("utf-8", "surrogatepass") + b"\n")
        except Exception as exc:
            fut.set_exception(MpvDisconnected(f"{self.name}: write failed: {exc}"))
            self._lost()
            return fut
        timer = loop.call_later(timeout, self._expire, rid)
        self._pending[rid] = (fut, command, timer, on_reply)
        return fut

    def _expire(self, rid: int) -> None:
        pending = self._pending.pop(rid, None)
        if pending is None:
            return
        fut, command, _timer, _on_reply = pending
        if not fut.done():
            fut.set_exception(MpvTimeout(f"{self.name}: no reply to {_short(command)}"))

    async def command(self, *args: Any, timeout: float = DEFAULT_TIMEOUT) -> Any:
        return await self.request(list(args), timeout)

    async def command_named(self, name: str, timeout: float = DEFAULT_TIMEOUT, **kwargs: Any) -> Any:
        return await self.request({"name": name, **kwargs}, timeout)

    def send(self, *args: Any, timeout: float = DEFAULT_TIMEOUT) -> asyncio.Future[Any]:
        """Fire and forget (failures are reported through ``on_error``)."""
        return self._detach(self.request(list(args), timeout))

    def send_named(self, name: str, timeout: float = DEFAULT_TIMEOUT, **kwargs: Any) -> asyncio.Future[Any]:
        return self._detach(self.request({"name": name, **kwargs}, timeout))

    def _detach(self, fut: asyncio.Future[Any]) -> asyncio.Future[Any]:
        def done(f: asyncio.Future[Any]) -> None:
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None and self.on_error is not None:
                self.on_error("command failed", exc)

        fut.add_done_callback(done)
        return fut

    async def get(self, prop: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
        return await self.request(["get_property", prop], timeout)

    async def get_or(self, prop: str, default: Any = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        try:
            return await self.request(["get_property", prop], timeout)
        except MpvCommandError:
            return default

    def set(self, prop: str, value: Any) -> asyncio.Future[Any]:
        return self.send("set_property", prop, value)

    async def observe(self, prop: str) -> int:
        oid = self._next_observe_id
        self._next_observe_id += 1
        await self.request(["observe_property", oid, prop])
        return oid
