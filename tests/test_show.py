import asyncio

import pytest

from aethra.clock import now
from aethra.deck import DeckState
from aethra.osc import OscMessage
from aethra.show import Mode, ShowController, StateStore

from fakes import FakeDeck, FakeStage, quiet_log

FAST = {"transitions": {"fade_out": 0.02, "fade_in": 0.02}}


def build(make_config, data=None, stage=None, state_store=None, mtc=None):
    merged = dict(FAST)
    merged.update(data or {})
    cfg = make_config(merged)
    decks = {"F": FakeDeck("F", "filler"), "A": FakeDeck("A", "cue"), "B": FakeDeck("B", "cue")}
    ctl = ShowController(cfg, quiet_log(), decks, stage or FakeStage(), mtc, state_store)
    for d in decks.values():
        d.listener = ctl
    return ctl, decks


async def started(ctl, decks):
    await ctl.start()
    decks["F"].finish_load()  # filler ready -> shown
    await asyncio.sleep(0.05)
    return ctl


async def preload_ready(decks):
    """Finish whatever cue deck is loading (the preload)."""
    for name in ("A", "B"):
        d = decks[name]
        if d.state == DeckState.LOADING:
            d.finish_load()


def run(coro):
    return asyncio.run(coro)


def test_startup_shows_filler_and_preloads_first_cue(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        assert ctl.mode == Mode.FILLER
        assert ctl.visible == "F"
        assert ctl.stage.top == "F"
        assert decks["A"].loads()[-1] == ("load", "01", 0.0, True)
        await ctl.shutdown()

    run(scenario())


def test_trigger_preloaded_cue_goes_live_immediately(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 36, 100]), t0)  # channel 16, note 36
        assert ctl.mode == Mode.CUE
        assert ctl.live.deck is decks["A"] and ctl.live.cue.id == "01"
        assert ("play",) in decks["A"].calls
        assert ctl.stage.top == "A"
        assert now() - t0 < 0.01
        # The filler is paused while hidden, and the next cue is preloaded on B.
        assert decks["F"].calls[-1] == ("pause",)
        assert decks["B"].loads()[-1] == ("load", "02", 0.0, True)
        # Fallback order: filler right under the live deck, then the backdrop.
        assert ctl.stage.orders[-1][:3] == ["A", "F", "backdrop"]
        await ctl.shutdown()

    run(scenario())


def test_wrong_channel_and_note_off_are_ignored(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x90, 36, 100]), now())  # channel 1
        ctl.on_midi(bytes([0x9F, 36, 0]), now())  # velocity 0 = note off
        assert ctl.mode == Mode.FILLER
        await ctl.shutdown()

    run(scenario())


def test_retrigger_guard(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 36, 100]), t0)
        plays = decks["A"].calls.count(("play",))
        ctl.on_midi(bytes([0x9F, 36, 100]), t0 + 0.2)
        assert decks["A"].calls.count(("play",)) == plays
        assert ctl.live.deck is decks["A"]
        await ctl.shutdown()

    run(scenario())


def test_negative_offset_delays_start(make_config):
    cues = [{"id": "01", "file": "one.mp4", "midi_note": 36, "offset": -0.15}]
    async def scenario():
        ctl, decks = build(make_config, {"cues": cues})
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 36, 100]), t0)
        assert ctl.mode == Mode.FILLER and ctl.pending is not None
        await asyncio.sleep(0.25)
        assert ctl.mode == Mode.CUE
        went = ctl.live.went_live_at - t0
        assert 0.149 <= went < 0.17
        await ctl.shutdown()

    run(scenario())


def test_positive_offset_preloads_into_the_clip(make_config):
    cues = [{"id": "01", "file": "one.mp4", "midi_note": 36, "offset": 2.0}]
    async def scenario():
        ctl, decks = build(make_config, {"cues": cues, "sync": {"latency_compensation": 0.1}})
        await started(ctl, decks)
        assert decks["A"].loads()[-1] == ("load", "01", 2.1, True)
        await ctl.shutdown()

    run(scenario())


def test_cold_start_loads_ahead_and_releases_on_time(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)  # A holds cue 01
        t0 = now()
        ctl.on_midi(bytes([0x9F, 38, 100]), t0)  # cue 03: not preloaded
        b = decks["B"]
        load = b.loads()[-1]
        assert load[1] == "03" and load[2] > 0.1  # loaded ahead of the trigger
        position = b.load_.start
        await asyncio.sleep(0.05)
        b.finish_load()  # ready before real time reaches `position`
        assert ctl.mode == Mode.FILLER  # waits for the right moment
        await asyncio.sleep(position + 0.05)
        assert ctl.mode == Mode.CUE and ctl.live.deck is b
        went = ctl.live.went_live_at - t0
        assert went == pytest.approx(position, abs=0.01)
        await ctl.shutdown()

    run(scenario())


def test_late_cold_start_reseeks(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 38, 100]), t0)
        b = decks["B"]
        await asyncio.sleep(0.6)  # the load takes longer than the lead it was given
        b.finish_load()
        assert b.calls[-1][0] == "seek"
        target = b.calls[-1][1]
        assert target > 0.6
        b.seeked()
        await asyncio.sleep(max(0.0, t0 + target - now()) + 0.05)
        assert ctl.mode == Mode.CUE and ctl.live.deck is b
        await ctl.shutdown()

    run(scenario())


def test_end_of_clip_fades_to_filler(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        decks["A"].reach_eof()
        await asyncio.sleep(0.1)
        assert ctl.mode == Mode.FILLER
        assert ctl.visible == "F" and ctl.stage.top == "F"
        assert ("fade", 1.0, 0.02) in decks["A"].calls
        assert ("unload",) in decks["A"].calls
        assert decks["F"].calls[-2:] == [("play",), ("fade", 0.0, 0.02)]
        await ctl.shutdown()

    run(scenario())


def test_error_during_playback_brings_in_the_filler(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        decks["A"].playback_error()
        await asyncio.sleep(0.1)
        assert ctl.mode == Mode.FILLER and ctl.visible == "F"
        assert ("fade", 1.0, 0.02) not in decks["A"].calls  # nothing to fade: the deck is black already
        assert decks["F"].calls[-2:] == [("play",), ("fade", 0.0, 0.02)]
        assert ("unload",) in decks["A"].calls
        await ctl.shutdown()

    run(scenario())


def test_on_end_hold_and_black(make_config):
    cues = [
        {"id": "01", "file": "one.mp4", "midi_note": 36, "on_end": "hold"},
        {"id": "02", "file": "two.mp4", "midi_note": 37, "on_end": "black"},
    ]
    async def scenario():
        ctl, decks = build(make_config, {"cues": cues})
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        decks["A"].reach_eof()
        await asyncio.sleep(0.05)
        assert ctl.mode == Mode.HOLD and ctl.stage.top == "A"
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 37, 100]), now())
        assert ctl.live.deck is decks["B"]
        decks["B"].reach_eof()
        await asyncio.sleep(0.05)
        assert ctl.mode == Mode.BLACK
        assert ("fade", 1.0, 0.02) in decks["B"].calls
        await ctl.shutdown()

    run(scenario())


def test_trigger_during_fade_out_wins(make_config):
    async def scenario():
        ctl, decks = build(make_config, {"transitions": {"fade_out": 0.5, "fade_in": 0.5}})
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        await preload_ready(decks)
        decks["A"].reach_eof()
        await asyncio.sleep(0.05)  # fading out
        ctl.on_midi(bytes([0x9F, 37, 100]), now())
        assert ctl.mode == Mode.CUE and ctl.live.deck is decks["B"]
        await asyncio.sleep(0.6)
        assert ctl.mode == Mode.CUE and ctl.visible == "B"  # the cancelled fade did not come back
        await ctl.shutdown()

    run(scenario())


def test_blackout_puts_backdrop_on_top(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.command("black", "test")
        assert ctl.blackout and ctl.stage.top == "backdrop"
        ctl.on_midi(bytes([0x9F, 36, 100]), now())  # cues still run underneath
        assert ctl.mode == Mode.CUE and ctl.stage.top == "backdrop"
        assert ctl.stage.orders[-1][:3] == ["backdrop", "A", "F"]
        ctl.command("black", "test")
        assert ctl.stage.top == "A"
        await ctl.shutdown()

    run(scenario())


def test_blackout_without_backdrop_uses_osd(make_config):
    async def scenario():
        ctl, decks = build(make_config, stage=FakeStage(has_backdrop=False))
        await started(ctl, decks)
        ctl.set_blackout(True, "test")
        assert decks["F"].blackout_osd
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        assert decks["A"].blackout_osd and not decks["F"].blackout_osd
        ctl.set_blackout(False, "test")
        assert not decks["A"].blackout_osd
        await ctl.shutdown()

    run(scenario())


def test_go_next_prev(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        ctl.command("next", "test")
        assert ctl.selected == 1
        assert decks["B"].loads()[-1][1] == "02"  # the preload follows the selection (A keeps 01)
        ctl.command("prev", "test")
        ctl.command("prev", "test")
        assert ctl.selected == 0
        await preload_ready(decks)
        ctl.command("go", "test")
        assert ctl.live is not None and ctl.live.cue.id == "01"
        assert ctl.selected == 1
        await ctl.shutdown()

    run(scenario())


def test_first_cue_is_preloaded_after_the_last(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 38, 100]), now())  # last cue (cold start on B)
        decks["B"].finish_load()
        await asyncio.sleep(0.6)
        assert ctl.live.cue.id == "03" and ctl.selected_cue is None
        assert decks["A"].load_ is not None and decks["A"].load_.cue.id == "01"
        ctl.command("go", "test")
        assert ctl.live.cue.id == "03"  # GO does not wrap around
        await ctl.shutdown()

    run(scenario())


def test_missing_file_keeps_filler(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        decks["A"].finish_load(ok=False)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        # Not preloaded any more -> cold start, which fails as well.
        deck = ctl.pending.deck
        deck.finish_load(ok=False)
        assert ctl.mode == Mode.FILLER and ctl.visible == "F"
        assert ctl.pending is None
        await ctl.shutdown()

    run(scenario())


def test_live_deck_crash_cuts_to_filler_and_resumes(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 36, 100]), t0)
        await asyncio.sleep(0.3)
        decks["A"].go_down("crash")
        assert ctl.visible == "F" and ctl.stage.top == "F"
        assert ("play",) in decks["F"].calls[-2:]
        # Resume on B at the expected position (ahead of now).
        b = decks["B"]
        assert ctl.pending is not None and ctl.pending.deck is b and ctl.pending.resume
        position = b.load_.start
        assert position > 0.3
        b.finish_load()
        await asyncio.sleep(max(0.0, t0 + position - now()) + 0.05)
        assert ctl.mode == Mode.CUE and ctl.live.deck is b and ctl.live.cue.id == "01"
        assert ctl.live.went_live_at - t0 == pytest.approx(position, abs=0.01)
        await asyncio.sleep(0.6)  # respawn of A
        assert decks["A"].up
        await ctl.shutdown()

    run(scenario())


def test_filler_failure_falls_back_to_colour(make_config, tmp_path):
    (tmp_path / "loop.mp4").write_bytes(b"x")
    async def scenario():
        ctl, decks = build(make_config, {"filler": {"mode": "loop", "file": "loop.mp4", "color": "#102030"}})
        await ctl.start()
        f = decks["F"]
        assert f.loads()[-1][1].endswith("loop.mp4")
        f.finish_load(ok=False)
        assert f.loads()[-1][1].startswith("av://lavfi:color=c=0x102030")
        f.finish_load()
        await asyncio.sleep(0.05)
        assert ctl.visible == "F"
        await ctl.shutdown()

    run(scenario())


def test_filler_unavailable_means_black(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        decks["F"].spawn_ok = False
        await ctl.start()
        assert ctl.visible is None and ctl.stage.top == "backdrop"
        await ctl.shutdown()

    run(scenario())


def test_osc_commands_and_cues(make_config):
    cues = [
        {"id": "intro", "file": "one.mp4", "osc": {"address": "/lastmarker/name", "arg": "V01"}},
        {"id": "two", "file": "two.mp4"},
    ]
    async def scenario():
        ctl, decks = build(make_config, {"cues": cues, "osc": {"enabled": True, "require_playing": True}})
        await started(ctl, decks)
        await preload_ready(decks)
        addr = ("10.0.0.1", 9000)
        ctl.on_osc(OscMessage("/lastmarker/name", ("V01",)), addr, now())
        assert ctl.mode == Mode.FILLER  # REAPER not playing
        ctl.on_osc(OscMessage("/play", (1.0,)), addr, now())
        ctl.on_osc(OscMessage("/lastmarker/name", ("V01",)), addr, now())
        assert ctl.live.cue.id == "intro"
        ctl.on_osc(OscMessage("/aethra/black", (1,)), addr, now())
        assert ctl.blackout
        ctl.on_osc(OscMessage("/aethra/black", (0,)), addr, now())
        assert not ctl.blackout
        await preload_ready(decks)
        ctl.on_osc(OscMessage("/aethra/cue", (2,)), addr, now())  # 1-based number
        assert ctl.live.cue.id == "two"
        await ctl.shutdown()

    run(scenario())


def test_command_notes(make_config):
    async def scenario():
        ctl, decks = build(make_config, {"midi": {"port": "*", "channel": 16, "command_notes": {"black": 120, "go": 121}}})
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 120, 1]), now())
        assert ctl.blackout
        ctl.on_midi(bytes([0x9F, 121, 1]), now())
        assert ctl.live.cue.id == "01"
        await ctl.shutdown()

    run(scenario())


def test_quit_needs_confirmation(make_config):
    async def scenario():
        ctl, decks = build(make_config)
        await started(ctl, decks)
        ctl.command("quit", "test")
        assert not ctl.stop_event.is_set()
        ctl.command("quit", "test")
        assert ctl.stop_event.is_set()
        await ctl.shutdown()

    run(scenario())


def test_state_restore_continues_playing_deck(make_config, tmp_path):
    store = StateStore(tmp_path / "state.json")

    async def first_run():
        ctl, decks = build(make_config, state_store=store)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        await ctl.shutdown(quit_decks=False)  # controller "crashes", decks keep running

    async def second_run():
        ctl, decks = build(make_config, state_store=store)
        decks["A"].adopt_info = {"path": str(ctl.cfg.cues[0].file), "pause": False, "eof": False}
        decks["F"].adopt_info = None
        await ctl.start()
        assert ctl.mode == Mode.CUE and ctl.live.deck is decks["A"] and ctl.live.cue.id == "01"
        assert ctl.selected == 1
        assert ctl.stage.top == "A"
        await ctl.shutdown()

    run(first_run())
    run(second_run())


def test_state_restore_resumes_on_fresh_decks(make_config, tmp_path):
    store = StateStore(tmp_path / "state.json")

    async def first_run():
        ctl, decks = build(make_config, state_store=store)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 37 - 1, 100]), now())
        await asyncio.sleep(0.2)
        await ctl.shutdown(quit_decks=False)

    async def second_run():
        ctl, decks = build(make_config, state_store=store)
        await ctl.start()
        assert ctl.pending is not None and ctl.pending.resume and ctl.pending.cue.id == "01"
        assert ctl.pending.position > 0.2
        await ctl.shutdown()

    run(first_run())
    run(second_run())


def test_drift_correction_adjusts_speed(make_config):
    async def scenario():
        ctl, decks = build(make_config, {"sync": {"correction": {"max_speed_change": 0.05}}})
        await started(ctl, decks)
        await preload_ready(decks)
        t0 = now()
        ctl.on_midi(bytes([0x9F, 36, 100]), t0)
        live = ctl.live
        assert live.corrector is not None and live.corrector.reference == "clock"
        # Feed positions of a video that runs 10 % slow against the trigger clock.
        t = live.went_live_at
        for _ in range(150):
            t += 0.04
            ctl.deck_time(decks["A"], (t - t0) * 0.9, t)
        speeds = [c[1] for c in decks["A"].calls if c[0] == "speed"]
        assert speeds and max(speeds) > 1.0
        await ctl.shutdown()

    run(scenario())


def test_correction_disabled(make_config):
    async def scenario():
        ctl, decks = build(make_config, {"sync": {"correction": {"enabled": False}}})
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        assert ctl.live.corrector is None
        await ctl.shutdown()

    run(scenario())


def test_restore_time_survives_wall_clock_jumps():
    t = now()
    live = {"t0_wall": 4_000_000_000.0, "t0_mono": t - 5.0}  # wall clock went back since the save
    assert ShowController._restore_time(live, {"saved_mono": t - 1.0}) == pytest.approx(t - 5.0)
    # After a reboot the monotonic counter restarted: fall back to the wall clock.
    import time as _time

    live = {"t0_wall": _time.time() - 3.0, "t0_mono": t + 100.0}
    assert ShowController._restore_time(live, {"saved_mono": t + 101.0}) == pytest.approx(now() - 3.0, abs=0.05)
    # A trigger "in the future" on both clocks is rejected.
    assert ShowController._restore_time({"t0_wall": _time.time() + 60}, {}) is None


def test_failed_trigger_during_fade_out_still_brings_the_filler(make_config):
    async def scenario():
        ctl, decks = build(make_config, {"transitions": {"fade_out": 0.3, "fade_in": 0.05}})
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        decks["A"].reach_eof()  # fading out to the filler...
        await asyncio.sleep(0.05)
        ctl.on_midi(bytes([0x9F, 38, 100]), now())  # ...a trigger arrives, cold start on B
        decks["B"].finish_load(ok=False)  # and it cannot play
        await asyncio.sleep(0.5)
        assert ctl.mode == Mode.FILLER and ctl.visible == "F" and ctl.stage.top == "F"
        await ctl.shutdown()

    run(scenario())


def test_restart_keeps_the_running_cue_on_top_from_the_first_order(make_config, tmp_path):
    store = StateStore(tmp_path / "state.json")

    async def first_run():
        ctl, decks = build(make_config, state_store=store)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        await ctl.shutdown(quit_decks=False)
        # Detaching darkens every deck that is not on stage.
        assert ("conceal",) in decks["B"].calls and ("conceal",) in decks["F"].calls
        assert ("conceal",) not in decks["A"].calls

    async def second_run():
        ctl, decks = build(make_config, state_store=store)
        decks["A"].adopt_info = {"path": str(ctl.cfg.cues[0].file), "pause": False, "eof": False}
        decks["B"].adopt_info = {"path": None, "pause": True, "eof": False}
        await ctl.start()
        assert ctl.stage.orders[0][0] == "A"  # never the backdrop first
        assert ("reveal",) in decks["B"].calls
        await ctl.shutdown()

    run(first_run())
    run(second_run())


def test_restart_during_hold_keeps_the_last_frame(make_config, tmp_path):
    store = StateStore(tmp_path / "state.json")
    cues = [{"id": "01", "file": "one.mp4", "midi_note": 36, "on_end": "hold"}]

    async def first_run():
        ctl, decks = build(make_config, {"cues": cues}, state_store=store)
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        decks["A"].reach_eof()
        assert ctl.mode == Mode.HOLD
        await ctl.shutdown(quit_decks=False)

    async def second_run():
        ctl, decks = build(make_config, {"cues": cues}, state_store=store)
        decks["A"].adopt_info = {"path": str(ctl.cfg.cues[0].file), "pause": True, "eof": True}
        await ctl.start()
        assert ctl.mode == Mode.HOLD and ctl.visible == "A" and ctl.live.ended
        assert ctl.pending is None  # an ended cue is not rejoined
        await ctl.shutdown()

    run(first_run())
    run(second_run())


def test_mtc_stop_does_not_override_hold(make_config):
    async def scenario():
        from aethra.mtc import MtcSnapshot

        cues = [{"id": "01", "file": "one.mp4", "midi_note": 36, "on_end": "hold"}]
        ctl, decks = build(make_config, {"cues": cues, "sync": {"mtc": {"enabled": True, "on_stop": "filler"}}})

        class Mtc:
            running = True

            def snapshot(self, t):
                return MtcSnapshot("running" if self.running else "stopped", 0.0, None, 25.0)

            def position(self, t):
                return 0.0

        mtc = Mtc()
        ctl.mtc = mtc
        await started(ctl, decks)
        await preload_ready(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        ctl._check_mtc_stop(now())
        decks["A"].reach_eof()
        mtc.running = False
        ctl._check_mtc_stop(now())
        await asyncio.sleep(0.1)
        assert ctl.mode == Mode.HOLD
        await ctl.shutdown()

    run(scenario())
