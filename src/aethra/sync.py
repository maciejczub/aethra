"""Keeps a playing clip locked to its trigger.

After a cue goes live, the clip position that *should* be on screen is known:

* laptop clock: ``expected = clip_at_t0 + (now - t0)``, where ``t0`` is the
  trigger's arrival time;
* MTC (when enabled and running): ``expected = clip_at_anchor + (mtc_now -
  mtc_anchor)``, anchored at the trigger (or at the cue's ``mtc_start``).

When MTC appears, disappears or comes back, the new reference is anchored so
that the expectation continues smoothly; a real jump in MTC (REAPER relocated)
therefore shows up as drift and is chased with one exact seek.

``drift = video_position - expected`` is median-filtered over ~2 s. Up to one
frame of constant bias measured right after the start (mpv reports a frame's
position slightly before it is shown) is treated as the zero point. Above
``threshold`` the playback speed is nudged by at most ``max_speed_change``
until the drift is back under ``target``; a drift beyond ``hard_seek`` that
persists for half a second is fixed with one exact seek instead.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

from aethra.config import CorrectionConfig
from aethra.mtc import MtcSnapshot

SETTLE_TIME = 0.5  # ignore samples right after start/seek
BASELINE_SAMPLES = 12
WINDOW = 2.0
HARD_SEEK_PERSIST = 0.5
SEEK_LEAD = 0.08  # seek a little ahead: the seek itself takes time


@dataclass(frozen=True)
class SetSpeed:
    speed: float
    drift: float


@dataclass(frozen=True)
class Seek:
    position: float
    drift: float


Action = SetSpeed | Seek


class DriftCorrector:
    def __init__(self, cfg: CorrectionConfig, fps: float = 25.0, use_mtc: bool = False) -> None:
        self.cfg = cfg
        self.frame = 1.0 / fps
        self.use_mtc = use_mtc
        self._clock_anchor = (0.0, 0.0)  # (host time, clip position)
        self._mtc_anchor: tuple[float, float] | None = None  # (mtc position, clip position)
        self._samples: deque[tuple[float, float]] = deque()
        self._baseline_samples: list[float] = []
        self.baseline: float | None = None
        self._settle_until = 0.0
        self._speed = 1.0
        self._correcting = False
        self.reference = "clock"
        self.last_drift: float | None = None
        self.max_abs_drift = 0.0
        self.corrections = 0
        self.seeks = 0

    @property
    def speed(self) -> float:
        return self._speed

    def start(self, t_live: float, t0: float, clip_at_t0: float, mtc_at_t0: float | None = None) -> None:
        """The cue went live at ``t_live``; ``clip_at_t0`` belongs to trigger time ``t0``."""
        self._clock_anchor = (t0, clip_at_t0)
        self._mtc_anchor = (mtc_at_t0, clip_at_t0) if mtc_at_t0 is not None else None
        self.reference = "clock"
        self._reset(t_live)

    def _reset(self, t: float) -> None:
        self._samples.clear()
        self._settle_until = t + SETTLE_TIME
        self._correcting = False

    def expected(self, t: float, mtc: MtcSnapshot | None = None) -> float:
        """Where the clip should be at host time ``t`` (updates the anchors)."""
        if self.use_mtc and mtc is not None and mtc.running and mtc.position is not None:
            if self._mtc_anchor is None:
                self._mtc_anchor = (mtc.position, self._clock_expected(t))
            mtc_pos, clip = self._mtc_anchor
            expected = clip + (mtc.position - mtc_pos)
            self._clock_anchor = (t, expected)  # the clock continues from here if MTC stops
            self.reference = "mtc"
            return expected
        # No running MTC: the laptop clock continues from the last estimate. The
        # MTC anchor is kept, so when MTC returns the clip follows REAPER's
        # position again (a relocation becomes drift and is chased).
        self.reference = "clock"
        return self._clock_expected(t)

    def _clock_expected(self, t: float) -> float:
        t0, clip = self._clock_anchor
        return clip + (t - t0)

    def update(self, t: float, video_position: float, mtc: MtcSnapshot | None = None) -> Action | None:
        reference_before = self.reference
        expected = self.expected(t, mtc)
        if self.reference != reference_before:
            self._samples.clear()
        raw = video_position - expected
        if t < self._settle_until:
            return None
        if self.baseline is None:
            self._baseline_samples.append(raw)
            if len(self._baseline_samples) >= BASELINE_SAMPLES:
                median = statistics.median(self._baseline_samples)
                self.baseline = max(-self.frame, min(self.frame, median))
            return None
        drift = raw - self.baseline
        self._samples.append((t, drift))
        while self._samples and self._samples[0][0] < t - WINDOW:
            self._samples.popleft()
        if len(self._samples) < 5:
            return None
        median = statistics.median(d for _, d in self._samples)
        self.last_drift = median
        self.max_abs_drift = max(self.max_abs_drift, abs(median))

        cfg = self.cfg
        if cfg.hard_seek is not None and abs(median) >= cfg.hard_seek:
            recent = [d for ts, d in self._samples if ts >= t - HARD_SEEK_PERSIST]
            persisted = self._samples[0][0] <= t - HARD_SEEK_PERSIST
            if persisted and recent and all(abs(d) >= cfg.hard_seek for d in recent):
                self.seeks += 1
                self._reset(t)
                self._speed = 1.0
                return Seek(expected + SEEK_LEAD, median)
            return None

        if not self._correcting and abs(median) >= cfg.threshold:
            self._correcting = True
            self.corrections += 1
        if self._correcting:
            if abs(median) <= cfg.target:
                self._correcting = False
                return self._set_speed(1.0, median)
            adjust = max(-cfg.max_speed_change, min(cfg.max_speed_change, median / cfg.correction_time))
            return self._set_speed(1.0 - adjust, median)
        return None

    def stop(self) -> SetSpeed | None:
        """Reference lost for good (e.g. correction disabled at runtime): back to 1.0."""
        self._samples.clear()
        self._correcting = False
        return self._set_speed(1.0, 0.0)

    def _set_speed(self, speed: float, drift: float) -> SetSpeed | None:
        speed = round(speed, 5)
        # Avoid flooding mpv with tiny changes; returning to exactly 1.0 always goes out.
        if speed == self._speed or (speed != 1.0 and abs(speed - self._speed) < 0.0002):
            return None
        self._speed = speed
        return SetSpeed(speed, drift)

    def seek_done(self, t: float) -> None:
        self._reset(t)
