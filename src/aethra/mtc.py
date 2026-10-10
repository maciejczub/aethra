"""MIDI Time Code decoder.

Quarter-frame messages (``F1 0nnn dddd``) arrive four per frame; eight of them
(pieces 0-7) carry one full timecode. That timecode is the frame during which
piece 0 was sent, so piece *k* of a sequence encoding time *T* was sent at
``T + k/4`` frames. Once a sequence completes, each of its eight arrival times
yields one (arrival time, MTC position) pair; the median offset of the last two
sequences gives a jitter-resistant estimate of the MTC position at any host
time. Full-frame SysEx (``F0 7F dev 01 01 hh mm ss ff F7``) is a locate: it
sets a static position.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

RATE_FPS = {0: 24.0, 1: 25.0, 2: 30000 / 1001, 3: 30.0}
NOMINAL_FPS = {0: 24, 1: 25, 2: 30, 3: 30}
DROP_FRAME = 2


@dataclass(frozen=True)
class Timecode:
    hours: int
    minutes: int
    seconds: int
    frames: int
    rate: int  # MTC rate code: 0=24, 1=25, 2=29.97 drop-frame, 3=30

    @property
    def fps(self) -> float:
        return RATE_FPS[self.rate]

    def to_seconds(self) -> float:
        if self.rate == DROP_FRAME:
            total_minutes = 60 * self.hours + self.minutes
            frame_number = (3600 * self.hours + 60 * self.minutes + self.seconds) * 30 + self.frames
            frame_number -= 2 * (total_minutes - total_minutes // 10)
            return frame_number * 1001 / 30000
        return self.hours * 3600 + self.minutes * 60 + self.seconds + self.frames / NOMINAL_FPS[self.rate]

    def __str__(self) -> str:
        sep = ";" if self.rate == DROP_FRAME else ":"
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}{sep}{self.frames:02d}"


def format_seconds(seconds: float, fps: float = 25.0) -> str:
    """Seconds -> ``HH:MM:SS:FF`` (non-drop), for display only."""
    sign = "-" if seconds < 0 else ""
    total = int(round(abs(seconds) * fps))
    nominal = int(round(fps))
    frames = total % nominal
    secs = total // nominal
    return f"{sign}{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}:{frames:02d}"


@dataclass(frozen=True)
class MtcSnapshot:
    status: str  # "running" | "stopped" | "none"
    position: float | None
    timecode: str | None
    fps: float | None

    @property
    def running(self) -> bool:
        return self.status == "running"


class MtcDecoder:
    def __init__(self, timeout: float = 0.5) -> None:
        self.timeout = timeout
        self._pieces: list[int | None] = [None] * 8
        self._times: list[float] = [0.0] * 8
        self._expected = 0
        self._offsets: deque[float] = deque(maxlen=16)
        self._last_tc: Timecode | None = None
        self._last_seq_t: float | None = None
        self._last_msg_t: float | None = None
        self._last_qf_t: float | None = None
        self._confirmed = False
        self._static_position: float | None = None
        self.sequences = 0
        self.discontinuities = 0

    # -- input ---------------------------------------------------------------

    def feed_quarter_frame(self, data: int, t: float) -> None:
        piece = (data >> 4) & 0x07
        value = data & 0x0F
        self._last_msg_t = t
        self._last_qf_t = t
        if piece == 0:
            self._pieces = [None] * 8
            self._expected = 0
        if piece != self._expected:
            # Lost message or reverse playback: wait for the next piece 0.
            self._expected = -1
            return
        self._pieces[piece] = value
        self._times[piece] = t
        self._expected = piece + 1
        if piece == 7:
            self._expected = -1
            self._complete_sequence()

    def feed_full_frame(self, sysex: bytes, t: float) -> bool:
        """Feed a SysEx message; returns True if it was an MTC full frame."""
        if len(sysex) != 10 or sysex[0] != 0xF0 or sysex[1] != 0x7F or sysex[3:5] != b"\x01\x01" or sysex[9] != 0xF7:
            return False
        hh, mm, ss, ff = sysex[5], sysex[6], sysex[7], sysex[8]
        tc = Timecode(hh & 0x1F, mm & 0x3F, ss & 0x3F, ff & 0x1F, (hh >> 5) & 0x03)
        self._last_msg_t = t
        self._last_tc = tc
        self._static_position = tc.to_seconds()
        self._offsets.clear()
        self._confirmed = False
        self._last_seq_t = None
        return True

    def _complete_sequence(self) -> None:
        p = self._pieces
        if any(v is None for v in p):
            return
        frames = p[0] | ((p[1] & 0x01) << 4)
        seconds = p[2] | ((p[3] & 0x03) << 4)
        minutes = p[4] | ((p[5] & 0x03) << 4)
        hours = p[6] | ((p[7] & 0x01) << 4)
        rate = (p[7] >> 1) & 0x03
        tc = Timecode(hours, minutes, seconds, frames, rate)
        position = tc.to_seconds()
        frame = 1.0 / tc.fps
        seq_t = self._times[0]

        continuous = False
        if self._last_tc is not None and self._last_seq_t is not None and self._last_tc.rate == rate:
            expected = self._last_tc.to_seconds() + 2 * frame
            elapsed = seq_t - self._last_seq_t
            continuous = abs(position - expected) < frame / 2 and abs(elapsed - 2 * frame) < frame
        if not continuous:
            if self._confirmed or self._offsets:
                self.discontinuities += 1
            self._offsets.clear()
        self._confirmed = continuous
        for k in range(8):
            self._offsets.append(position + k * frame / 4 - self._times[k])
        self._last_tc = tc
        self._last_seq_t = seq_t
        self._static_position = position + 7 * frame / 4
        self.sequences += 1

    # -- output --------------------------------------------------------------

    def snapshot(self, t: float) -> MtcSnapshot:
        if self._last_msg_t is None or t - self._last_msg_t > self.timeout:
            return MtcSnapshot("none", self._static_position, self._tc_text(), self._fps())
        fps = self._fps()
        stale_after = 4.0 / fps if fps else self.timeout
        if self._confirmed and self._offsets and self._last_qf_t is not None and t - self._last_qf_t <= stale_after:
            position = t + statistics.median(self._offsets)
            return MtcSnapshot("running", position, format_seconds(position, fps or 25.0), fps)
        return MtcSnapshot("stopped", self._static_position, self._tc_text(), fps)

    def position(self, t: float) -> float | None:
        snap = self.snapshot(t)
        return snap.position if snap.running else None

    def _fps(self) -> float | None:
        return self._last_tc.fps if self._last_tc else None

    def _tc_text(self) -> str | None:
        if self._static_position is None or self._last_tc is None:
            return None
        return format_seconds(self._static_position, self._last_tc.fps)


# --------------------------------------------------------------------------
# Encoding (tests, simulators)


def quarter_frame_bytes(tc: Timecode) -> list[int]:
    """The eight quarter-frame data bytes (without the F1 status) for ``tc``."""
    nibbles = [
        tc.frames & 0x0F,
        (tc.frames >> 4) & 0x01,
        tc.seconds & 0x0F,
        (tc.seconds >> 4) & 0x03,
        tc.minutes & 0x0F,
        (tc.minutes >> 4) & 0x03,
        tc.hours & 0x0F,
        ((tc.hours >> 4) & 0x01) | (tc.rate << 1),
    ]
    return [(piece << 4) | nibble for piece, nibble in enumerate(nibbles)]


def frames_to_timecode(frame_number: int, rate: int = 1) -> Timecode:
    """Non-drop frame count -> Timecode (rates 0, 1, 3)."""
    fps = NOMINAL_FPS[rate]
    frames = frame_number % fps
    secs = frame_number // fps
    return Timecode(secs // 3600, secs // 60 % 60, secs % 60, frames, rate)


def full_frame_sysex(tc: Timecode, device: int = 0x7F) -> bytes:
    return bytes([0xF0, 0x7F, device, 0x01, 0x01, (tc.rate << 5) | tc.hours, tc.minutes, tc.seconds, tc.frames, 0xF7])
