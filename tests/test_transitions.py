"""Transitions: blend formulas, the scene model, and the controller driving the
compositor (with a fake compositor that records what it is told)."""

import asyncio

import pytest

from aethra.blend import BLACK, BLEND_MODES, TRANSITION_TYPES, WHITE, Entry, blend_rgb, composite, transition_entries
from aethra.clock import now
from aethra.deck import DeckState
from aethra.picture import CompositorPicture
from aethra.scene import Scene, Transition
from aethra.show import Mode, ShowController

from fakes import FakeDeck, FakeStage, quiet_log

# ---------------------------------------------------------------- blend modes

RED, GREEN, GRAY = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.5, 0.5, 0.5)


@pytest.mark.parametrize(
    "mode, base, src, expected",
    [
        ("normal", GRAY, RED, RED),
        ("multiply", GRAY, RED, (0.5, 0.0, 0.0)),
        ("screen", GRAY, RED, (1.0, 0.5, 0.5)),
        ("darken", GRAY, RED, (0.5, 0.0, 0.0)),
        ("lighten", GRAY, RED, (1.0, 0.5, 0.5)),
        ("difference", GRAY, RED, (0.5, 0.5, 0.5)),
        ("exclusion", GRAY, RED, (0.5, 0.5, 0.5)),
        ("lineardodge", GRAY, RED, (1.0, 0.5, 0.5)),
        ("linearburn", GRAY, RED, (0.5, 0.0, 0.0)),
        ("subtract", GRAY, RED, (0.0, 0.5, 0.5)),
        ("overlay", GRAY, RED, (1.0, 0.0, 0.0)),
        ("hardlight", GRAY, RED, (1.0, 0.0, 0.0)),
        ("divide", GRAY, (1.0, 1.0, 0.5), (0.5, 0.5, 1.0)),
        ("colordodge", GRAY, (0.5, 0.0, 1.0), (1.0, 0.5, 1.0)),
        ("colorburn", GRAY, (0.5, 0.0, 1.0), (0.0, 0.0, 0.5)),
        ("hardmix", GRAY, (0.6, 0.4, 0.5), (1.0, 0.0, 1.0)),
        ("luminosity", RED, WHITE, WHITE),
        ("color", WHITE, RED, WHITE),
        ("darkercolor", GRAY, GREEN, GRAY),
        ("lightercolor", GRAY, GREEN, GREEN),
    ],
)
def test_blend_formulas(mode, base, src, expected):
    assert blend_rgb(mode, base, src) == pytest.approx(expected, abs=1e-6)


def test_every_blend_mode_is_a_transition_and_stays_in_range():
    assert set(BLEND_MODES) - {"normal"} <= set(TRANSITION_TYPES)
    for mode in BLEND_MODES:
        for b in (BLACK, WHITE, GRAY, RED, (0.2, 0.7, 0.9)):
            for s in (BLACK, WHITE, GRAY, GREEN, (0.9, 0.3, 0.1)):
                out = blend_rgb(mode, b, s)
                assert all(-1e-9 <= c <= 1 + 1e-9 for c in out), (mode, b, s, out)


@pytest.mark.parametrize("kind", TRANSITION_TYPES)
def test_every_transition_starts_with_the_old_picture_and_ends_with_the_new(kind):
    layers = {"X": (0.8, 0.3, 0.1), "Y": (0.1, 0.6, 0.9)}
    start = composite(layers["X"], transition_entries(kind, "Y", 0.0), layers)
    end = composite(layers["X"], transition_entries(kind, "Y", 1.0), layers)
    if kind != "cut":
        assert start == pytest.approx(layers["X"])
    assert end == pytest.approx(layers["Y"])


def test_transition_midpoints():
    layers = {"X": RED, "Y": GREEN}
    assert composite(RED, transition_entries("fade", "Y", 0.5), layers) == pytest.approx((0.5, 0.5, 0.0))
    assert composite(RED, transition_entries("fadeblack", "Y", 0.5), layers) == pytest.approx(BLACK)
    assert composite(RED, transition_entries("fadewhite", "Y", 0.5), layers) == pytest.approx(WHITE)
    # Halfway through a blend-mode transition the picture is exactly mode(X, Y).
    assert composite(RED, transition_entries("screen", "Y", 0.5), layers) == pytest.approx((1.0, 1.0, 0.0))
    assert composite(RED, transition_entries("multiply", "Y", 0.5), layers) == pytest.approx(BLACK)
    # To black: a plain fade, whatever the type.
    assert composite(RED, transition_entries("fadewhite", None, 0.5), layers) == pytest.approx((0.5, 0.0, 0.0))


# ---------------------------------------------------------------- scene


def test_scene_collapses_finished_transitions():
    scene = Scene(base="F")
    scene.add(Transition("A", "fade", 10.0, 1.0))
    assert scene.target == "A" and scene.layers(10.5) == {"F", "A"}
    scene.add(Transition("B", "multiply", 10.6, 1.0))
    assert scene.layers(10.7) == {"F", "A", "B"}
    # A finished: F is gone; B still running over A.
    assert scene.layers(11.2) == {"A", "B"} and scene.base == "A"
    assert scene.layers(11.7) == {"B"} and scene.transitions == []
    entries = scene.entries(11.7)
    assert entries == [Entry("B", "normal", 1.0)]


def test_scene_newest_finished_transition_covers_everything():
    scene = Scene(base="F")
    scene.add(Transition("A", "fade", 0.0, 5.0))
    scene.add(Transition(None, "fade", 0.0, 1.0))  # to black, quicker
    assert scene.layers(2.0) == set()
    assert scene.base is None
    scene.add(Transition("B", "cut", 3.0, 0.0))
    assert scene.base == "B" and scene.transitions == []


# ---------------------------------------------------------------- controller with the compositor


class FakeCompositor(FakeStage):
    def __init__(self) -> None:
        super().__init__(has_backdrop=False)
        self.sent: list[dict] = []

    def send(self, command: dict) -> None:
        self.sent.append(command)

    def transitions(self) -> list[tuple]:
        return [(c["to"], c["type"], c["duration"]) for c in self.sent if c["name"] == "transition"]

    def shows(self) -> list:
        return [c["layer"] for c in self.sent if c["name"] == "show"]


def build(make_config, data=None):
    cfg = make_config(data or {})
    decks = {"F": FakeDeck("F", "filler"), "A": FakeDeck("A", "cue"), "B": FakeDeck("B", "cue")}
    stage = FakeCompositor()
    ctl = ShowController(cfg, quiet_log(), decks, stage, picture=CompositorPicture(stage))
    for d in decks.values():
        d.listener = ctl
    return ctl, decks, stage


async def started(ctl, decks):
    await ctl.start()
    decks["F"].finish_load()
    await asyncio.sleep(0.01)


def finish_preload(decks, duration=200.0):
    for name in ("A", "B"):
        if decks[name].state == DeckState.LOADING:
            decks[name].finish_load(duration=duration)


def run(coro):
    return asyncio.run(coro)


def test_background_fades_in_from_black_at_start(make_config):
    async def scenario():
        ctl, decks, stage = build(make_config, {"transitions": {"xfade_out_duration": 1.0}})
        await started(ctl, decks)
        assert stage.transitions() == [("F", "fade", 0.5)]
        assert ctl.mode == Mode.FILLER and ctl.visible == "F"
        await ctl.shutdown()

    run(scenario())


def test_crossfade_into_a_cue_keeps_the_filler_playing_until_it_is_over(make_config):
    async def scenario():
        ctl, decks, stage = build(make_config, {"transitions": {"xfade_in_duration": 0.2, "xfade_in_type": "screen"}})
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        assert ctl.mode == Mode.CUE and ctl.visible == "A"
        assert stage.transitions()[-1] == ("A", "screen", 0.2)
        assert decks["F"].calls[-1] != ("pause",)  # still on stage, still playing
        assert ctl.picture.busy("F")
        await asyncio.sleep(0.3)
        assert decks["F"].calls[-1] == ("pause",)
        assert not ctl.picture.busy("F")
        await ctl.shutdown()

    run(scenario())


def test_cut_into_a_cue_by_default(make_config):
    async def scenario():
        ctl, decks, stage = build(make_config)
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        assert stage.shows()[-1] == "A"
        assert ("pause",) in decks["F"].calls  # off stage at once
        await ctl.shutdown()

    run(scenario())


def test_out_transition_starts_before_the_clip_ends(make_config):
    async def scenario():
        ctl, decks, stage = build(make_config, {"transitions": {"xfade_out_duration": 0.5, "xfade_out_type": "multiply"}})
        await started(ctl, decks)
        finish_preload(decks, duration=10.0)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        live = ctl.live
        ctl.deck_time(decks["A"], 8.0, now())  # 2 s before the end: too early to schedule
        assert live.out_task is None
        ctl.deck_time(decks["A"], 9.4, now())  # the transition starts at 9.5 s
        assert live.out_task is not None
        await asyncio.sleep(0.05)
        assert not live.leaving
        await asyncio.sleep(0.1)
        assert live.leaving and stage.transitions()[-1] == ("F", "multiply", 0.5)
        assert ("play",) == decks["F"].calls[-1]
        assert ctl.mode == Mode.CUE  # the clip is still playing underneath
        decks["A"].reach_eof()  # the end of the clip changes nothing
        await asyncio.sleep(0.5)
        assert ctl.mode == Mode.FILLER and ctl.live is None
        assert ("unload",) in decks["A"].calls
        await ctl.shutdown()

    run(scenario())


def test_cue_overrides_the_out_transition(make_config):
    cues = [
        {"id": "01", "file": "one.mp4", "midi_note": 36, "xfade_out_duration": 0.2, "xfade_out_type": "fadewhite"},
        {"id": "02", "file": "two.mp4", "midi_note": 37},
    ]

    async def scenario():
        ctl, decks, stage = build(make_config, {"cues": cues})
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        ctl.command("filler", "test")  # the operator takes the song off: its own out-transition
        await asyncio.sleep(0.01)
        assert stage.transitions()[-1] == ("F", "fadewhite", 0.2)
        await asyncio.sleep(0.3)
        assert ctl.mode == Mode.FILLER
        await ctl.shutdown()

    run(scenario())


def test_cue_to_cue_crossfade_retires_the_previous_deck_afterwards(make_config):
    cues = [
        {"id": "01", "file": "one.mp4", "midi_note": 36},
        {"id": "02", "file": "two.mp4", "midi_note": 37, "xfade_in_duration": 0.3, "xfade_in_type": "difference"},
        {"id": "03", "file": "three.mp4", "midi_note": 38},
    ]

    async def scenario():
        ctl, decks, stage = build(make_config, {"cues": cues})
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        finish_preload(decks)  # 02 on B
        ctl.on_midi(bytes([0x9F, 37, 100]), now())
        assert ctl.live.deck is decks["B"]
        assert stage.transitions()[-1] == ("B", "difference", 0.3)
        assert decks["A"].calls[-1] == ("play",)  # still on stage, untouched
        assert decks["A"].state == DeckState.PLAYING
        await asyncio.sleep(0.4)
        played = decks["A"].calls.index(("play",))
        assert ("unload",) in decks["A"].calls[played:]
        assert decks["A"].loads()[-1][1] == "03"  # and the next cue is preloaded there
        await ctl.shutdown()

    run(scenario())


def test_trigger_during_a_cue_crossfade_takes_the_leaving_deck(make_config):
    cues = [
        {"id": "01", "file": "one.mp4", "midi_note": 36},
        {"id": "02", "file": "two.mp4", "midi_note": 37, "xfade_in_duration": 5.0},
        {"id": "03", "file": "three.mp4", "midi_note": 38},
    ]

    async def scenario():
        ctl, decks, stage = build(make_config, {"cues": cues})
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 37, 100]), now())  # 5 s cross-fade A -> B
        ctl.on_midi(bytes([0x9F, 38, 100]), now())  # 03 at once: A must be freed
        assert stage.shows()[-1] == "B"  # the cross-fade was completed at once
        assert decks["A"].loads()[-1][1] == "03"
        assert ctl.pending is not None and ctl.pending.deck is decks["A"]
        await ctl.shutdown()

    run(scenario())


def test_background_switch_goes_through_black(make_config, tmp_path):
    (tmp_path / "intro.mp4").write_bytes(b"x")

    async def scenario():
        ctl, decks, stage = build(
            make_config,
            {"intro": {"mode": "loop", "file": "intro.mp4"}, "transitions": {"xfade_out_duration": 0.2}},
        )
        await started(ctl, decks)
        assert ctl.mode == Mode.INTRO
        ctl.command("filler", "test")
        await asyncio.sleep(0.15)  # to black first, then the background deck reloads
        assert stage.transitions()[-1] == (None, "fade", 0.1)
        assert decks["F"].loads()[-1][1].startswith("av://lavfi:color")
        decks["F"].finish_load()
        await asyncio.sleep(0.2)
        assert stage.transitions()[-1] == ("F", "fade", 0.1)
        assert ctl.mode == Mode.FILLER
        await ctl.shutdown()

    run(scenario())


def test_on_end_black_fades_to_black_before_the_end(make_config):
    cues = [{"id": "01", "file": "one.mp4", "midi_note": 36, "on_end": "black", "xfade_out_duration": 0.3}]

    async def scenario():
        ctl, decks, stage = build(make_config, {"cues": cues})
        await started(ctl, decks)
        finish_preload(decks, duration=10.0)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        ctl.deck_time(decks["A"], 9.7, now())
        await asyncio.sleep(0.02)
        assert stage.transitions()[-1] == (None, "fade", 0.3)
        decks["A"].reach_eof()
        assert ctl.mode == Mode.BLACK
        await ctl.shutdown()

    run(scenario())


def test_live_deck_lost_with_every_deck_resumes_when_one_is_back(make_config):
    async def scenario():
        ctl, decks, stage = build(make_config)
        await started(ctl, decks)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        # The compositor died: every layer goes down at once.
        for name in ("A", "B", "F"):
            decks[name].go_down()
        assert ctl.live is None and ctl._lost is not None
        assert stage.shows()[-1] is None  # black (the background is down too)
        await asyncio.sleep(0.7)  # respawns
        assert any(c[0] == "load" and c[1] == "01" and c[3] for c in decks["A"].calls[-3:] + decks["B"].calls[-3:])
        assert ctl.pending is not None and ctl.pending.resume
        await ctl.shutdown()

    run(scenario())


def test_window_engine_fadewhite_dips_through_white(make_config):
    """The window engine cannot cross-fade: types become a dip through black,
    or white for fadewhite."""

    async def scenario():
        cfg = make_config({"transitions": {"xfade_in_duration": 0.2, "xfade_in_type": "fadewhite"}})
        decks = {"F": FakeDeck("F", "filler"), "A": FakeDeck("A", "cue"), "B": FakeDeck("B", "cue")}
        ctl = ShowController(cfg, quiet_log(), decks, FakeStage())
        for d in decks.values():
            d.listener = ctl
        await started(ctl, decks)
        await asyncio.sleep(0.6)
        finish_preload(decks)
        ctl.on_midi(bytes([0x9F, 36, 100]), now())
        await asyncio.sleep(0.01)
        assert decks["F"].calls[-1] == ("fade", 1.0, 0.1) and decks["F"].tint == "FFFFFF"
        assert ctl.stage.top == "F"  # the old picture stays on top until it is white
        await asyncio.sleep(0.15)
        assert ctl.stage.top == "A" and decks["A"].tint == "FFFFFF"
        assert decks["A"].calls[-1] == ("fade", 0.0, 0.1)
        await ctl.shutdown()

    run(scenario())
