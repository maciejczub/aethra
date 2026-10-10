import asyncio
import random
import socket
import struct

import pytest

from aethra.osc import OscError, build_bundle, build_message, parse_packet, start_osc_server


def test_roundtrip_types():
    data = build_message("/aethra/cue", 3, 1.5, "intro", True, False, None, b"\x01\x02\x03")
    assert len(data) % 4 == 0
    (msg,) = parse_packet(data)
    assert msg.address == "/aethra/cue"
    assert msg.args[0] == 3
    assert msg.args[1] == pytest.approx(1.5)
    assert msg.args[2:] == ("intro", True, False, None, b"\x01\x02\x03")


@pytest.mark.parametrize("address", ["/a", "/ab", "/abc", "/abcd", "/aethra/go"])
def test_string_padding(address):
    (msg,) = parse_packet(build_message(address, "x" * len(address)))
    assert msg.address == address
    assert msg.args == ("x" * len(address),)


def test_message_without_type_tags():
    data = b"/play\x00\x00\x00"
    (msg,) = parse_packet(data)
    assert msg.address == "/play" and msg.args == ()


def test_reaper_style_float():
    data = b"/play\x00\x00\x00,f\x00\x00" + struct.pack(">f", 1.0)
    (msg,) = parse_packet(data)
    assert msg.args == (1.0,)


def test_bundle_flattening():
    inner = build_bundle(build_message("/b", 2))
    packet = build_bundle(build_message("/a", 1), inner)
    assert [(m.address, m.args) for m in parse_packet(packet)] == [("/a", (1,)), ("/b", (2,))]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"/a",  # not a multiple of 4
        b"noslash\x00",
        b"/a\x00\x00,i\x00\x00\x00\x00",  # truncated int
        b"/a\x00\x00,s\x00\x00abcd",  # unterminated string
        b"/a\x00\x00,z\x00\x00",  # unknown tag
        b"#bundle\x00" + b"\x00" * 8 + struct.pack(">i", 100),  # element too large
        b"/a\x00\x00,b\x00\x00" + struct.pack(">i", -4),  # negative blob
    ],
)
def test_malformed_packets(data):
    with pytest.raises(OscError):
        parse_packet(data)


def test_fuzz_never_raises_anything_else():
    rng = random.Random(1234)
    seeds = [build_message("/aethra/cue", "x", 1, 2.0), build_bundle(build_message("/a", 1))]
    for _ in range(3000):
        base = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 6)):
            pos = rng.randrange(len(base))
            base[pos] = rng.randrange(256)
        if rng.random() < 0.3:
            base = base[: rng.randrange(1, len(base) + 1)]
        try:
            parse_packet(bytes(base))
        except OscError:
            pass


def test_udp_server_receives():
    async def scenario():
        received = []
        transport = await start_osc_server("127.0.0.1", 0, lambda m, a, t: received.append((m, t)))
        port = transport.get_extra_info("sockname")[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(build_message("/aethra/go"), ("127.0.0.1", port))
            s.sendto(b"garbage", ("127.0.0.1", port))
            s.sendto(build_message("/aethra/cue", "02"), ("127.0.0.1", port))
        for _ in range(100):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.01)
        transport.close()
        return received

    received = asyncio.run(scenario())
    assert [m.address for m, _ in received] == ["/aethra/go", "/aethra/cue"]
    assert received[1][0].args == ("02",)
