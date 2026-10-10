import pytest

from aethra.mtc import (
    MtcDecoder,
    Timecode,
    format_seconds,
    frames_to_timecode,
    full_frame_sysex,
    quarter_frame_bytes,
)


def feed_stream(dec, start_frame, seconds, t_start, fps=25, rate=1, jitter=None, skip=None):
    """Feed quarter frames as a 25 fps sender would; returns the end time."""
    frame = 1.0 / fps
    t = t_start
    f = start_frame
    n = 0
    end = t_start + seconds
    while t < end:
        tc = frames_to_timecode(f, rate)
        for k, data in enumerate(quarter_frame_bytes(tc)):
            if skip and n in skip:
                n += 1
                continue
            dt = jitter[n % len(jitter)] if jitter else 0.0
            dec.feed_quarter_frame(data, t + k * frame / 4 + dt)
            n += 1
        t += 2 * frame
        f += 2
    return t


def test_quarter_frame_roundtrip():
    tc = Timecode(1, 2, 3, 4, 1)
    pieces = quarter_frame_bytes(tc)
    assert [p >> 4 for p in pieces] == list(range(8))
    dec = MtcDecoder()
    for k, p in enumerate(pieces):
        dec.feed_quarter_frame(p, 10.0 + k * 0.01)
    snap = dec.snapshot(10.08)
    assert snap.status == "stopped"  # one sequence is not enough to call it running
    assert snap.fps == 25


def test_running_position_is_accurate():
    dec = MtcDecoder()
    start = 25 * 60  # 00:01:00:00
    t_end = feed_stream(dec, start, 2.0, t_start=100.0)
    snap = dec.snapshot(t_end)
    assert snap.running
    # MTC position at host time t is 60 s + (t - 100)
    assert snap.position == pytest.approx(60.0 + (t_end - 100.0), abs=0.002)
    # Extrapolation between messages
    assert dec.position(t_end + 0.05) == pytest.approx(60.0 + (t_end + 0.05 - 100.0), abs=0.002)


def test_jitter_is_filtered():
    dec = MtcDecoder()
    jitter = [0.0, 0.003, -0.002, 0.004, 0.0, -0.003, 0.002, 0.001]
    t_end = feed_stream(dec, 0, 3.0, t_start=5.0, jitter=jitter)
    assert dec.snapshot(t_end).position == pytest.approx(t_end - 5.0, abs=0.003)


def test_stops_and_times_out():
    dec = MtcDecoder(timeout=0.5)
    t_end = feed_stream(dec, 0, 1.0, t_start=0.0)
    assert dec.snapshot(t_end).running
    assert dec.snapshot(t_end + 0.3).status == "stopped"  # no quarter frames for > 4 frames
    assert dec.snapshot(t_end + 1.0).status == "none"


def test_jump_is_detected():
    dec = MtcDecoder()
    t_jump = feed_stream(dec, 0, 1.0, t_start=0.0)
    t = feed_stream(dec, 25 * 600, 1.0, t_start=t_jump)  # relocated to 00:10:00:00
    snap = dec.snapshot(t)
    assert snap.running
    assert snap.position == pytest.approx(600.0 + (t - t_jump), abs=0.003)
    assert dec.discontinuities == 1


def test_lost_message_resyncs():
    dec = MtcDecoder()
    t = feed_stream(dec, 0, 2.0, t_start=0.0, skip={20, 21})
    assert dec.snapshot(t).running
    assert dec.snapshot(t).position == pytest.approx(t, abs=0.005)


def test_reverse_playback_is_not_running():
    dec = MtcDecoder()
    tc = frames_to_timecode(100)
    pieces = quarter_frame_bytes(tc)
    t = 0.0
    for _ in range(10):
        for p in reversed(pieces):
            dec.feed_quarter_frame(p, t)
            t += 0.01
    assert not dec.snapshot(t).running


def test_full_frame_locate():
    dec = MtcDecoder()
    tc = Timecode(0, 5, 0, 12, 1)
    assert dec.feed_full_frame(full_frame_sysex(tc), 1.0)
    snap = dec.snapshot(1.1)
    assert snap.status == "stopped"
    assert snap.position == pytest.approx(300.48)
    assert snap.timecode == "00:05:00:12"
    assert not dec.feed_full_frame(b"\xf0\x7e\x00\x06\x01\xf7", 1.0)


def test_drop_frame_seconds():
    # 00:10:00;00 at 29.97 DF is exactly 17982 frames = 600.0 s real time
    assert Timecode(0, 10, 0, 0, 2).to_seconds() == pytest.approx(17982 * 1001 / 30000)
    assert Timecode(0, 1, 0, 2, 2).to_seconds() == pytest.approx(1800 * 1001 / 30000)


def test_format_seconds():
    assert format_seconds(62.2, 25) == "00:01:02:05"
    assert format_seconds(-0.04, 25) == "-00:00:00:01"
