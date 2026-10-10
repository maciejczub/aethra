"""End-to-end tests against a real mpv (``--vo=null``, no display needed).

Run them in the test container:  docker compose -f docker/compose.yaml run --rm test
They are skipped when mpv or ffmpeg is not installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import statistics
import subprocess
import sys

import pytest

from aethra.clock import now
from aethra.config import parse_config
from aethra.deck import Deck, DeckContext, DeckState, keybinds_for
from aethra.eventlog import EventLog
from aethra.mpvproc import mpv_version, resolve_mpv, supported_options
from aethra.show import Mode, ShowController, StateStore
from aethra.stage import NullStage

MPV = resolve_mpv("mpv")
FFMPEG = shutil.which("ffmpeg")

pytestmark = [
    pytest.mark.mpv,
    pytest.mark.skipif(MPV is None or FFMPEG is None, reason="needs mpv and ffmpeg"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX test harness"),
]


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    for name, seconds, color in (("one", 3, "red"), ("two", 3, "green"), ("three", 6, "blue"), ("filler", 2, "gray")):
        subprocess.run(
            [
                FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                "-i", f"color=c={color}:s=640x360:r=25:d={seconds}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25",
                str(d / f"{name}.mp4"),
            ],
            check=True,
        )
    (d / "broken.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(4096))
    return d


CUES = [
    {"id": "01", "file": "one.mp4", "midi_note": 36},
    {"id": "02", "file": "two.mp4", "midi_note": 37},
    {"id": "03", "file": "three.mp4", "midi_note": 38},
    {"id": "bad", "file": "broken.mp4", "midi_note": 39},
]


def show_config(media, tmp_path, **extra):
    data = {
        "show": {"name": "integration", "media_dir": str(media)},
        "mpv": {"path": MPV, "hwdec": "no", "startup_timeout": 10},
        "stage": {"width": 640, "height": 360},
        "transitions": {"fade_out": 0.1, "fade_in": 0.1},
        # mpv with --vo=null runs a few percent slow inside containers on WSL2
        # (timer overshoot); the clock correction has to make up for it, which
        # is exactly what these tests exercise. Real shows use 1 % at most.
        "sync": {"correction": {"max_speed_change": 0.15, "correction_time": 0.5, "threshold": 0.03, "target": 0.01}},
        "midi": {"enabled": True, "port": "*", "channel": 16},
        "logging": {"dir": str(tmp_path / "logs")},
        "cues": CUES,
    }
    data.update(extra)
    return parse_config(data, tmp_path / "show.yaml")


def make_controller(cfg, tmp_path, instance):
    log = EventLog(cfg.logging.dir, None)
    ctx = DeckContext(
        config=cfg, log=log, mpv_path=MPV, supported_options=supported_options(MPV),
        instance=instance, log_dir=cfg.logging.dir, session="it", vo_override="null", keybinds=keybinds_for(cfg),
    )
    decks = {n: Deck(n, r, ctx, listener=None) for n, r in (("F", "filler"), ("A", "cue"), ("B", "cue"))}
    ctl = ShowController(cfg, log, decks, NullStage(), None, StateStore(tmp_path / f"{instance}-state.json"))
    for d in decks.values():
        d.listener = ctl
    return ctl, decks, log


async def wait_for(predicate, timeout=5.0, step=0.01):
    deadline = now() + timeout
    while now() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


def note(n):
    return bytes([0x9F, n, 100])


def test_mpv_version_and_options():
    assert mpv_version(MPV).startswith("mpv")
    options = supported_options(MPV)
    assert options and "input-ipc-server" in options and "keep-open" in options


def test_preloaded_trigger_is_instant_and_stays_in_sync(media, tmp_path):
    async def scenario():
        cues = [CUES[2], CUES[0], CUES[1]]  # the 6 s clip first, so it is the preloaded one
        cfg = show_config(media, tmp_path, cues=cues)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-sync")
        samples = []
        orig = ctl.deck_time

        def record(deck, position, t):
            if ctl.live is not None and ctl.live.deck is deck:
                samples.append((t, position))
            orig(deck, position, t)

        ctl.deck_time = record
        try:
            await ctl.start(fresh=True)
            assert await wait_for(lambda: decks["A"].state == DeckState.READY)
            t0 = now()
            ctl.on_midi(note(38), t0)
            assert ctl.mode == Mode.CUE and ctl.live.deck is decks["A"]
            assert ctl.live.went_live_at - t0 < 0.005  # unpause sent within 5 ms
            await asyncio.sleep(5.0)
            corrector = ctl.live.corrector
            baseline = corrector.baseline or 0.0
            # After the correction settled the clip stays within ~1.5 frames of
            # the trigger clock. (Against a player that is 9 % slow, a purely
            # proportional correction keeps a steady error of about
            # rate error x correction_time = 45 ms; real clock errors are ppm.)
            errors = [pos - (t - t0) - baseline for t, pos in samples if t - t0 > 3.0]
            assert len(errors) > 20
            assert abs(statistics.median(errors)) < 0.06, statistics.median(errors)
            # Either mpv kept time by itself, or the correction had to step in.
            assert corrector.corrections >= 1 or abs(statistics.median(errors)) < 0.02
        finally:
            await ctl.shutdown()

    asyncio.run(scenario())


def test_end_of_clip_goes_to_filler_and_next_is_preloaded(media, tmp_path):
    async def scenario():
        cfg = show_config(media, tmp_path)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-end")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: decks["F"].state == DeckState.PLAYING and ctl.visible == "F")
        assert await wait_for(lambda: decks["A"].state == DeckState.READY)
        ctl.on_midi(note(36), now())
        assert await wait_for(lambda: decks["B"].state == DeckState.READY and decks["B"].cue.id == "02")
        assert decks["F"].paused
        assert await wait_for(lambda: ctl.mode == Mode.FILLER, timeout=6.0)
        assert ctl.visible == "F" and not decks["F"].paused
        assert decks["A"].state == DeckState.IDLE
        # Next trigger goes straight to the preloaded deck B.
        ctl.on_midi(note(37), now())
        assert ctl.live.deck is decks["B"] and ctl.live.cue.id == "02"
        await ctl.shutdown()

    asyncio.run(scenario())


def test_cold_start_is_released_in_sync(media, tmp_path):
    async def scenario():
        cfg = show_config(media, tmp_path)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-cold")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: decks["A"].state == DeckState.READY)
        t0 = now()
        ctl.on_midi(note(38), t0)  # cue 03 is not preloaded
        assert ctl.pending is not None
        assert await wait_for(lambda: ctl.mode == Mode.CUE, timeout=5.0)
        position = ctl.live.deck.load_.start
        assert ctl.live.went_live_at - t0 == pytest.approx(position, abs=0.02)
        await asyncio.sleep(2.5)
        deck = ctl.live.deck
        expected = deck.time_pos_t - t0
        assert deck.time_pos == pytest.approx(expected, abs=0.08)
        await ctl.shutdown()

    asyncio.run(scenario())


def test_broken_file_never_reaches_the_stage(media, tmp_path):
    async def scenario():
        cfg = show_config(media, tmp_path)
        ctl, decks, log = make_controller(cfg, tmp_path, "it-bad")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: ctl.visible == "F")
        ctl.on_midi(note(39), now())
        assert await wait_for(lambda: ctl.pending is None, timeout=5.0)
        assert ctl.mode == Mode.FILLER and ctl.visible == "F"
        # The show goes on.
        assert await wait_for(lambda: any(d.state == DeckState.READY for d in (decks["A"], decks["B"])))
        ctl.command("prev", "test")
        await asyncio.sleep(0.3)
        await ctl.shutdown()
        text = log.path.read_text()
        assert "cue_failed" in text

    asyncio.run(scenario())


def test_killed_live_deck_is_replaced_and_rejoins_the_cue(media, tmp_path):
    async def scenario():
        cfg = show_config(media, tmp_path)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-crash")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: decks["A"].state == DeckState.READY)
        t0 = now()
        ctl.on_midi(note(38), t0)  # 6 s clip, cold start on B
        assert await wait_for(lambda: ctl.mode == Mode.CUE, timeout=5.0)
        live_deck = ctl.live.deck
        await asyncio.sleep(1.0)
        live_deck.proc.kill()  # simulated mpv crash
        assert await wait_for(lambda: ctl.visible == "F", timeout=2.0)
        assert await wait_for(lambda: ctl.mode == Mode.CUE and ctl.live is not None, timeout=5.0)
        resumed = ctl.live.deck
        assert resumed is not live_deck and ctl.live.cue.id == "03"
        await asyncio.sleep(1.5)
        assert resumed.time_pos == pytest.approx(resumed.time_pos_t - t0, abs=0.1)
        assert await wait_for(lambda: live_deck.up, timeout=10.0)  # respawned
        await ctl.shutdown()

    asyncio.run(scenario())


def test_controller_restart_keeps_the_running_cue(media, tmp_path):
    async def first():
        cfg = show_config(media, tmp_path)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-restart")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: decks["A"].state == DeckState.READY)
        ctl.on_midi(note(36), now())
        await asyncio.sleep(0.3)
        await ctl.shutdown(quit_decks=False)  # the controller goes away, mpv keeps playing
        return decks["A"].proc.pid if decks["A"].proc else None

    async def second(pid):
        cfg = show_config(media, tmp_path)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-restart")
        await ctl.start()
        assert ctl.mode == Mode.CUE and ctl.live.deck is decks["A"] and ctl.live.cue.id == "01"
        assert decks["A"].adopted and decks["A"].proc.pid == pid
        assert await wait_for(lambda: ctl.mode == Mode.FILLER, timeout=6.0)  # its end is still handled
        await ctl.shutdown()

    pid = asyncio.run(first())
    asyncio.run(second(pid))


def test_blackout_and_fades_do_not_break_playback(media, tmp_path):
    async def scenario():
        cfg = show_config(media, tmp_path, test_mode=True)
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-osd")
        await ctl.start(fresh=True)
        assert await wait_for(lambda: decks["A"].state == DeckState.READY)
        ctl.on_midi(note(36), now())
        for _ in range(4):
            ctl.command("black", "test")
            await asyncio.sleep(0.05)
        ctl.command("filler", "test")
        assert await wait_for(lambda: ctl.mode == Mode.FILLER, timeout=2.0)
        assert all(d.up for d in decks.values())
        await ctl.shutdown()

    asyncio.run(scenario())


def test_verify_finds_damaged_files(media):
    from aethra.media import verify_file

    good = verify_file(MPV, media / "one.mp4")
    assert good.ok, good.problems
    assert good.duration == pytest.approx(3.0, abs=0.05)
    assert good.fps == 25 and (good.width, good.height) == (640, 360)
    assert good.warnings(25.0, (1920, 1080))  # size differs from a 1080p stage
    quick = verify_file(MPV, media / "three.mp4", quick=True)
    assert quick.ok, quick.problems
    bad = verify_file(MPV, media / "broken.mp4")
    assert not bad.ok and bad.problems
    missing = verify_file(MPV, media / "nope.mp4")
    assert missing.problems == ["file not found"]


def test_bench_reports_counters(media, tmp_path):
    from aethra.bench import bench_file, format_results

    cfg = show_config(media, tmp_path)
    result = asyncio.run(bench_file(MPV, cfg, media / "three.mp4", "no", 1.5, vo="null"))
    assert result.error is None
    assert result.codec and result.size == "640x360"
    assert result.counters["frame-drop-count"] == 0
    assert "three.mp4" in format_results([result])


def test_intro_filler_outro_sequence(media, tmp_path):
    async def scenario():
        cfg = show_config(
            media, tmp_path,
            intro={"mode": "color", "color": "#102030"},
            outro={"mode": "color", "color": "#302010"},
            cues=[CUES[0], CUES[1]],
        )
        ctl, decks, _ = make_controller(cfg, tmp_path, "it-intro")
        f = decks["F"]
        try:
            await ctl.start(fresh=True)
            assert await wait_for(lambda: ctl.mode == Mode.INTRO and ctl.visible == "F" and f.state == DeckState.PLAYING)
            assert "0x102030" in f.load_.media
            assert await wait_for(lambda: decks["A"].state == DeckState.READY and decks["A"].cue.id == "01")
            ctl.on_midi(note(36), now())
            assert ctl.mode == Mode.CUE
            assert await wait_for(lambda: ctl.background_kind == "filler" and f.state == DeckState.READY)
            assert await wait_for(lambda: ctl.mode == Mode.FILLER, timeout=6.0)
            assert await wait_for(lambda: any(d.state == DeckState.READY and d.cue and d.cue.id == "02" for d in decks.values()))
            ctl.on_midi(note(37), now())  # the last song
            assert await wait_for(lambda: ctl.background_kind == "outro" and f.state == DeckState.READY)
            assert "0x302010" in f.load_.media
            assert await wait_for(lambda: ctl.mode == Mode.OUTRO and ctl.visible == "F", timeout=6.0)
        finally:
            await ctl.shutdown()

    asyncio.run(scenario())
