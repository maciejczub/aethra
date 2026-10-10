"""The projector as the second (extended) screen, the operator's panel on the main one."""

import asyncio
import json
import os
import time

import pytest

from aethra.config import StageConfig
from aethra.keyboard import PanelFocus, types_text
from aethra.keys import parse_key_spec
from aethra.osc import OscMessage
from aethra.platform import runtime_dir
from aethra.stage import Monitor, stage_monitor

from fakes import FakeDeck, FakeStage, quiet_log

LAPTOP = Monitor(0, r"\\.\DISPLAY1", 0, 0, 1920, 1200, True)
PROJECTOR = Monitor(1, r"\\.\DISPLAY2", 1920, 0, 1920, 1080, False)


def test_automatic_stage_screen_never_takes_the_main_display():
    assert stage_monitor(StageConfig(), [LAPTOP, PROJECTOR]) == PROJECTOR
    assert stage_monitor(StageConfig(), [PROJECTOR.__class__(0, "x", 0, 0, 1, 1, False), LAPTOP]).primary is False
    # No projector yet: wait (None) instead of covering the operator's screen.
    assert stage_monitor(StageConfig(), [LAPTOP]) is None
    # Playing on the main display is possible, but only on purpose.
    assert stage_monitor(StageConfig(screen=0), [LAPTOP]) == LAPTOP
    assert stage_monitor(StageConfig(screen_name=r"\\.\DISPLAY2"), [LAPTOP]) is None


def test_keys_that_type():
    assert types_text(parse_key_spec("space")) and types_text(parse_key_spec("b")) and types_text(parse_key_spec("shift+b"))
    assert not types_text(parse_key_spec("ctrl+t")) and not types_text(parse_key_spec("alt+f5")) and not types_text(parse_key_spec("win+b"))


class FakeHotkeys:
    def __init__(self):
        self.calls = []

    def suspend_typing_keys(self):
        self.calls.append("suspend")

    def resume_typing_keys(self):
        self.calls.append("resume")


def test_panel_focus_is_a_lease():
    async def scenario():
        hk = FakeHotkeys()
        focus = PanelFocus(hk, quiet_log())
        focus.LEASE = 0.2
        focus.update(True)
        focus.update(True)  # renewals do not repeat the suspension
        assert hk.calls == ["suspend"]
        await asyncio.sleep(0.1)
        focus.update(True)
        await asyncio.sleep(0.15)
        assert hk.calls == ["suspend"]  # renewed in time
        await asyncio.sleep(0.15)
        assert hk.calls == ["suspend", "resume"]  # the panel went silent: keys back
        focus.update(True)
        focus.update(False)
        assert hk.calls == ["suspend", "resume", "suspend", "resume"]

    asyncio.run(scenario())


def test_panel_focus_only_from_this_computer(make_config):
    from aethra.show import ShowController

    cfg = make_config({"osc": {"enabled": True}})
    decks = {"F": FakeDeck("F", "filler"), "A": FakeDeck("A", "cue"), "B": FakeDeck("B", "cue")}
    ctl = ShowController(cfg, quiet_log(), decks, FakeStage())
    seen = []
    ctl.on_panel_focus = seen.append
    ctl.on_osc(OscMessage("/aethra/panel", (1,)), ("127.0.0.1", 5000), time.perf_counter())
    ctl.on_osc(OscMessage("/aethra/panel", (0,)), ("::1", 5000), time.perf_counter())
    ctl.on_osc(OscMessage("/aethra/panel", (1,)), ("192.168.1.20", 5000), time.perf_counter())
    assert seen == [True, False]


@pytest.fixture
def panel(tmp_path):
    from aethra.gui.server import Panel

    (tmp_path / "show.yaml").write_text("midi: {enabled: false}\nosc: {enabled: true, port: 9555}\ncues: [{id: '01', file: a.mp4}]\n")
    p = Panel(tmp_path / "show.yaml")
    p.instance = f"test-{os.getpid()}"
    yield p
    for suffix in ("status.json", "stop-request"):
        (runtime_dir() / f"{p.instance}-{suffix}").unlink(missing_ok=True)


def running_show(panel, mode="cue"):
    path = runtime_dir() / f"{panel.instance}-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "updated": time.time(), "mode": mode}))


def test_panel_keeps_test_pictures_off_the_stage_during_the_show(panel):
    running_show(panel)
    with pytest.raises(ValueError, match="stage screen"):
        panel.start_job("bench", {"files": ["a.mp4"]})
    with pytest.raises(ValueError, match="stage screen"):
        panel.start_job("selftest", {"windows": True})


def test_stop_while_waiting_for_the_projector(panel):
    running_show(panel, mode="waiting for the stage screen")
    assert panel.stop_show(panel.read_config()["data"]) == {"sent": "stop request"}
    assert (runtime_dir() / f"{panel.instance}-stop-request").is_file()
