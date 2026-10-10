import asyncio
import json
import sys

import pytest

from aethra.ipc import MpvCommandError, MpvDisconnected, MpvIpc, MpvTimeout

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake server uses a Unix socket")


class FakeMpv:
    """A tiny stand-in for mpv's IPC server."""

    def __init__(self, path):
        self.path = path
        self.server = None
        self.writers = []
        self.received = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=str(self.path))

    async def _client(self, reader, writer):
        self.writers.append(writer)
        held = []
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line)
            self.received.append(msg)
            cmd = msg["command"]
            rid = msg.get("request_id", 0)
            name = cmd["name"] if isinstance(cmd, dict) else cmd[0]
            if name == "slow":
                continue  # never answer
            if name == "hold":
                held.append(rid)  # answered later, in reverse order, by "release"
                continue
            if name == "release":
                for hid in reversed(held):
                    writer.write(json.dumps({"request_id": hid, "error": "success", "data": hid}).encode() + b"\n")
                held.clear()
            if name == "loadfile":
                # Reply and the following event arrive in one chunk.
                writer.write(
                    (json.dumps({"request_id": rid, "error": "success", "data": {"playlist_entry_id": 7}}) + "\n"
                     + json.dumps({"event": "start-file", "playlist_entry_id": 7}) + "\n").encode()
                )
                await writer.drain()
                continue
            if name == "bad":
                reply = {"request_id": rid, "error": "invalid parameter"}
            elif name == "get_property" and cmd[1] == "pid":
                reply = {"request_id": rid, "error": "success", "data": 4242}
            elif name == "emit":
                writer.write(json.dumps({"event": "client-message", "args": ["aethra-key", "go"]}).encode() + b"\n")
                reply = {"request_id": rid, "error": "success", "data": None}
            elif name == "bye":
                writer.close()
                return
            else:
                reply = {"request_id": rid, "error": "success", "data": None}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()

    async def stop(self):
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()


def run(coro):
    return asyncio.run(coro)


def test_request_reply_and_events(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "mpv.sock")
        await fake.start()
        events = []
        ipc = MpvIpc(str(fake.path), on_event=events.append)
        await ipc.connect(timeout=2)
        assert await ipc.get("pid") == 4242
        await ipc.command("emit")
        await asyncio.sleep(0.05)
        await ipc.close()
        await fake.stop()
        return events

    events = run(scenario())
    assert events == [{"event": "client-message", "args": ["aethra-key", "go"]}]


def test_out_of_order_replies_are_matched(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "mpv.sock")
        await fake.start()
        ipc = MpvIpc(str(fake.path))
        await ipc.connect(timeout=2)
        first = ipc.request(["hold"])
        second = ipc.request(["hold"])
        await ipc.command("release")
        results = await asyncio.gather(first, second)
        await ipc.close()
        await fake.stop()
        return results

    first, second = run(scenario())
    assert first < second  # data echoes each request id


def test_on_reply_runs_before_following_event(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "mpv.sock")
        await fake.start()
        order = []
        ipc = MpvIpc(str(fake.path), on_event=lambda m: order.append(("event", m["event"])))
        await ipc.connect(timeout=2)
        fut = ipc.request({"name": "loadfile", "url": "x"}, on_reply=lambda data: order.append(("reply", data["playlist_entry_id"])))
        await fut
        await asyncio.sleep(0.05)
        await ipc.close()
        await fake.stop()
        return order

    assert run(scenario()) == [("reply", 7), ("event", "start-file")]


def test_errors_and_timeouts(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "mpv.sock")
        await fake.start()
        errors = []
        ipc = MpvIpc(str(fake.path), on_error=lambda what, exc: errors.append(exc))
        await ipc.connect(timeout=2)
        with pytest.raises(MpvCommandError):
            await ipc.command("bad")
        with pytest.raises(MpvTimeout):
            await ipc.command("slow", timeout=0.1)
        ipc.send("bad")  # fire and forget: reported through on_error
        await asyncio.sleep(0.05)
        await ipc.close()
        await fake.stop()
        return errors

    errors = run(scenario())
    assert len(errors) == 1 and isinstance(errors[0], MpvCommandError)


def test_disconnect_fails_pending_and_notifies_once(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "mpv.sock")
        await fake.start()
        lost = []
        ipc = MpvIpc(str(fake.path), on_disconnect=lambda: lost.append(1))
        await ipc.connect(timeout=2)
        pending = ipc.request(["slow"], timeout=5)
        ipc.send("bye")
        with pytest.raises(MpvDisconnected):
            await pending
        await asyncio.sleep(0.05)
        assert not ipc.connected
        with pytest.raises(MpvDisconnected):
            await ipc.command("anything")
        await fake.stop()
        return lost

    assert run(scenario()) == [1]


def test_connect_waits_for_server(tmp_path):
    async def scenario():
        fake = FakeMpv(tmp_path / "late.sock")

        async def late_start():
            await asyncio.sleep(0.2)
            await fake.start()

        starter = asyncio.create_task(late_start())
        ipc = MpvIpc(str(fake.path))
        await ipc.connect(timeout=3)
        await starter
        ok = await ipc.get("pid")
        await ipc.close()
        await fake.stop()
        return ok

    assert run(scenario()) == 4242


def test_connect_gives_up(tmp_path):
    async def scenario():
        ipc = MpvIpc(str(tmp_path / "nobody.sock"))
        with pytest.raises(MpvDisconnected):
            await ipc.connect(timeout=0.2)

    run(scenario())
