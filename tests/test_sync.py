import pytest

from aethra.config import CorrectionConfig
from aethra.mtc import MtcSnapshot
from aethra.sync import BASELINE_SAMPLES, SETTLE_TIME, DriftCorrector, Seek, SetSpeed

STEP = 0.04
BASELINE_DONE = SETTLE_TIME + BASELINE_SAMPLES * STEP


def running(pos):
    return MtcSnapshot("running", pos, None, 25.0)


def stopped(pos=None):
    return MtcSnapshot("stopped", pos, None, 25.0)


def simulate(c, seconds, video_rate=1.0, mtc_rate=1.0, video_offset=0.0, start=0.0, mtc_offset=None, mtc=True):
    """Video advances at video_rate*speed, MTC at mtc_rate. Returns (actions, video, reference)."""
    actions = []
    t = start
    video = video_offset
    ref = 0.0 if mtc_offset is None else mtc_offset
    while t < start + seconds:
        snap = running(ref) if mtc else None
        action = c.update(t, video, snap)
        if action is not None:
            actions.append((t, action))
            if isinstance(action, Seek):
                video = action.position
                c.seek_done(t)
        t += STEP
        video += STEP * video_rate * c.speed
        ref += STEP * mtc_rate
    return actions, video, ref


def corrector(use_mtc=True, **kw):
    cfg = CorrectionConfig(**kw)
    c = DriftCorrector(cfg, 25.0, use_mtc=use_mtc)
    c.start(0.0, 0.0, 0.0, 0.0 if use_mtc else None)
    return c


def test_no_correction_when_in_sync():
    c = corrector()
    actions, _, _ = simulate(c, 30.0)
    assert actions == []
    assert c.last_drift == pytest.approx(0.0, abs=1e-6)
    assert c.reference == "mtc"


def test_small_constant_bias_is_absorbed():
    c = corrector()
    actions, _, _ = simulate(c, 20.0, video_offset=0.035)  # mpv reports frames slightly early
    assert actions == []
    assert c.baseline == pytest.approx(0.035)


def test_large_initial_error_is_not_absorbed():
    c = corrector()
    actions, video, ref = simulate(c, 60.0, video_offset=0.1)
    assert c.baseline == pytest.approx(0.04)  # clamped to one frame
    assert isinstance(actions[0][1], SetSpeed) and actions[0][1].speed < 1.0
    assert actions[-1][1].speed == 1.0
    assert abs(video - ref - c.baseline) <= 0.015


def test_gentle_correction_of_a_fast_clock():
    c = corrector(threshold=0.04, target=0.01, max_speed_change=0.01, correction_time=4.0)
    actions, video, ref = simulate(c, 120.0, video_rate=1.005)  # 0.5 % fast
    speeds = [a.speed for _, a in actions if isinstance(a, SetSpeed)]
    assert speeds and all(0.99 <= s <= 1.0 for s in speeds)
    assert not any(isinstance(a, Seek) for _, a in actions)
    assert abs(video - ref) < 0.08
    assert c.max_abs_drift < 0.08


def test_clock_reference_without_mtc():
    c = corrector(use_mtc=False, max_speed_change=0.15, correction_time=0.5)
    # A player running 9 % slow (seen with mpv --vo=null on WSL2).
    actions, video, ref = simulate(c, 20.0, video_rate=0.91, mtc=False)
    assert c.reference == "clock"
    assert any(isinstance(a, SetSpeed) and a.speed > 1.05 for _, a in actions)
    t_end = 20.0
    assert abs(video - t_end) < 0.12


def test_hard_seek_after_persistent_jump():
    c = corrector(hard_seek=1.0)
    simulate(c, 2.0)  # establish the baseline
    actions, video, ref = simulate(c, 5.0, start=2.0, video_offset=2.0 + 5.0, mtc_offset=2.0)
    seeks = [a for _, a in actions if isinstance(a, Seek)]
    assert len(seeks) == 1
    assert abs(video - ref) < 0.15


def test_hard_seek_disabled():
    c = corrector(hard_seek=None)
    simulate(c, 2.0)
    actions, _, _ = simulate(c, 5.0, start=2.0, video_offset=7.0, mtc_offset=2.0)
    assert not any(isinstance(a, Seek) for _, a in actions)


def test_mtc_stop_continues_on_clock_without_jump():
    c = corrector()
    simulate(c, 5.0)
    assert c.reference == "mtc"
    e1 = c.expected(5.0, running(5.0))
    # MTC stops: the expectation continues on the laptop clock from the last MTC estimate.
    e2 = c.expected(6.0, stopped(5.0))
    assert c.reference == "clock"
    assert e2 == pytest.approx(e1 + 1.0)


def test_mtc_relocation_is_chased():
    c = corrector(hard_seek=1.0)
    simulate(c, 3.0)
    # REAPER jumps 30 s ahead: MTC-expected position jumps, the video follows with a seek.
    actions, video, ref = simulate(c, 3.0, start=3.0, video_offset=3.0, mtc_offset=33.0)
    assert any(isinstance(a, Seek) for _, a in actions)
    assert video == pytest.approx(ref, abs=0.15)


def test_mtc_appearing_late_continues_the_clock():
    c = DriftCorrector(CorrectionConfig(), 25.0, use_mtc=True)
    c.start(0.0, 0.0, 0.0, None)
    assert c.expected(1.0, None) == pytest.approx(1.0)
    assert c.expected(2.0, running(500.0)) == pytest.approx(2.0)  # anchored, no jump
    assert c.expected(3.0, running(501.0)) == pytest.approx(3.0)


def test_explicit_anchor():
    c = DriftCorrector(CorrectionConfig(), 25.0, use_mtc=True)
    c.start(0.0, 0.0, 2.0, 100.0)  # clip position 2.0 belongs to MTC 100.0
    assert c.expected(0.0, running(103.5)) == pytest.approx(5.5)


def test_trigger_time_matters_not_live_time():
    # A cold start goes live later than the trigger; the clock reference is the trigger.
    c = DriftCorrector(CorrectionConfig(), 25.0)
    c.start(t_live=10.4, t0=10.0, clip_at_t0=1.0)
    assert c.expected(11.0) == pytest.approx(2.0)
