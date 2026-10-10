"""The compositor process with real libmpv layers, and the show on that engine.

Needs libmpv, the glfw package and a display. The test container provides all
three (a virtual X display with Mesa's software OpenGL):

    docker compose -f docker/compose.yaml run --rm test python -m pytest -m compositor -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys

import pytest

from aethra.blend import TRANSITION_TYPES, composite, transition_entries
from aethra.clock import now
from aethra.config import StageConfig, parse_config
from aethra.deck import Deck, DeckContext, DeckState, keybinds_for
from aethra.eventlog import EventLog
from aethra.ipc import MpvIpc
from aethra.mpvproc import resolve_mpv, supported_options
from aethra.picture import CompositorPicture
from aethra.platform import ipc_address
from aethra.show import Mode, ShowController, StateStore
from aethra.stage import CompositorStage


def _missing() -> str | None:
    try:
        import glfw  # noqa: F401

        from aethra import libmpv

        libmpv.load()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "no display"
    if shutil.which("ffmpeg") is None:
        return "no ffmpeg"
    return None


MISSING = _missing()
pytestmark = [
    pytest.mark.compositor,
    pytest.mark.skipif(MISSING is not None, reason=f"needs libmpv, glfw, ffmpeg and a display: {MISSING}"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX test harness"),
]

W, H = 64, 36
TOLERANCE = 4 / 255  # 8-bit rounding in every pass


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")

    def make(name: str, graph: str, seconds: float) -> None:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, "-t", str(seconds),
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25", str(d / f"{name}.mp4")],
            check=True,
        )

    make("split", f"color=c=red:s={W}x{H // 2}:r=25[a];color=c=blue:s={W}x{H // 2}:r=25[b];[a][b]vstack[out0]", 2)
    make("red", f"color=c=0xE03020:s={W}x{H}:r=25", 3)
    make("green", f"color=c=0x30C060:s={W}x{H}:r=25", 3)
    return d


# As in production (mpvproc.build_layer_options): mpv's single-pass renderer.
LAYER_OPTIONS = {
    "idle": "yes", "keep-open": "always", "hwdec": "no", "audio": "no", "terminal": "no", "force-window": "yes",
    "gpu-dumb-mode": "yes",
}


async def wait_for(predicate, timeout=5.0, step=0.02):
    deadline = now() + timeout
    while now() < deadline:
        if await predicate() if asyncio.iscoroutinefunction(predicate) else predicate():
            return True
        await asyncio.sleep(step)
    return False


class Rig:
    """A compositor with two layers showing solid colours."""

    def __init__(self, tmp_path, instance: str) -> None:
        self.tmp_path = tmp_path
        self.instance = instance
        self.stage = CompositorStage(
            StageConfig(width=W, height=H), EventLog(None, None), ipc_address(instance, "compositor"), (0, 0, W, H),
            log_file=tmp_path / "compositor.log", topmost=False,
        )
        self.layers: dict[str, MpvIpc] = {}

    async def __aenter__(self) -> "Rig":
        await self.stage.open()
        return self

    async def __aexit__(self, *exc) -> None:
        for ipc in self.layers.values():
            await ipc.close()
        await self.stage.quit()

    async def layer(self, name: str, url: str) -> MpvIpc:
        address = ipc_address(self.instance, name)
        await self.stage.create_layer(name, dict(LAYER_OPTIONS, **{"input-ipc-server": address}))
        ipc = MpvIpc(address, name)
        await ipc.connect(timeout=5)
        await ipc.request({"name": "loadfile", "url": url, "flags": "replace", "options": {"pause": "yes"}})
        self.layers[name] = ipc

        async def loaded() -> bool:
            return await ipc.get_or("time-pos") is not None

        assert await wait_for(loaded)
        await asyncio.sleep(0.1)  # the first frame is rendered into the layer
        return ipc

    async def pixel(self, x: int = W // 2, y: int = H // 2) -> tuple[float, float, float]:
        await asyncio.sleep(0.06)  # a few frames: the command is drawn
        data = await self.stage.request({"name": "screenshot", "x": x, "y": y})
        return tuple(c / 255 for c in data["pixel"])  # type: ignore[return-value]

    async def send(self, **command) -> None:
        await self.stage.request(command)


def run(coro):
    return asyncio.run(coro)


def test_layers_are_upright_and_cut(clips, tmp_path):
    async def scenario():
        async with Rig(tmp_path, "ct-up") as rig:
            await rig.layer("S", str(clips / "split.mp4"))
            assert await rig.pixel() == (0, 0, 0)  # nothing on stage yet
            await rig.send(name="show", layer="S")
            top, bottom = await rig.pixel(y=3), await rig.pixel(y=H - 4)
            assert top[0] > 0.9 and top[2] < 0.1, top  # red on top
            assert bottom[2] > 0.9 and bottom[0] < 0.1, bottom
            await rig.send(name="blackout", on=True)
            assert await rig.pixel() == (0, 0, 0)
            await rig.send(name="blackout", on=False)
            assert (await rig.pixel(y=3))[0] > 0.9

    run(scenario())


def test_every_transition_type_matches_the_reference(clips, tmp_path):
    async def scenario():
        async with Rig(tmp_path, "ct-modes") as rig:
            await rig.layer("X", str(clips / "red.mp4"))
            await rig.layer("Y", str(clips / "green.mp4"))
            await rig.send(name="show", layer="X")
            x = await rig.pixel()
            await rig.send(name="show", layer="Y")
            y = await rig.pixel()
            failures = []
            for kind in TRANSITION_TYPES:
                for p in (0.25, 0.5, 0.75):
                    await rig.send(name="show", layer="X")
                    await rig.send(name="freeze", progress=p)
                    await rig.send(name="transition", to="Y", type=kind, duration=60.0)
                    got = await rig.pixel()
                    want = composite(x, transition_entries(kind, "Y", p), {"Y": y})
                    if any(abs(a - b) > TOLERANCE for a, b in zip(got, want)):
                        failures.append(f"{kind} p={p}: got {tuple(round(v * 255) for v in got)}, want {tuple(round(v * 255) for v in want)}")
                    await rig.send(name="freeze", progress=None)
            assert not failures, "\n".join(failures)

    run(scenario())


def test_transition_runs_on_time_and_stacks(clips, tmp_path):
    async def scenario():
        async with Rig(tmp_path, "ct-time") as rig:
            await rig.layer("X", str(clips / "red.mp4"))
            await rig.layer("Y", str(clips / "green.mp4"))
            await rig.send(name="show", layer="X")
            await rig.send(name="transition", to="Y", type="fade", duration=0.6, start=now())
            status = await rig.stage.request({"name": "status"})
            assert status["target"] == "Y" and status["base"] == "X"
            await rig.send(name="transition", to="X", type="screen", duration=0.3)  # on top of the running one
            await asyncio.sleep(0.45)
            status = await rig.stage.request({"name": "status"})
            assert status["base"] == "X" and status["transitions"] == []  # the newer one finished: X alone

    run(scenario())


def test_quitting_a_layer_reports_it(clips, tmp_path):
    async def scenario():
        async with Rig(tmp_path, "ct-quit") as rig:
            ipc = await rig.layer("X", str(clips / "red.mp4"))
            assert rig.stage.layers == {"X"}
            ipc.send("quit")
            assert await wait_for(lambda: not rig.stage.layers)

    run(scenario())


# ---------------------------------------------------------------- the show on the compositor engine

MPV = resolve_mpv("mpv")


def compositor_show(clips, tmp_path, instance, **extra):
    data = {
        "show": {"name": "compositor", "media_dir": str(clips)},
        "mpv": {"path": MPV or "mpv", "hwdec": "no", "startup_timeout": 10},
        "stage": {"width": W, "height": H},
        "filler": {"mode": "color", "color": "#2040A0"},
        "transitions": {"xfade_in_duration": 0.6, "xfade_in_type": "fade", "xfade_out_duration": 1.0, "xfade_out_type": "fade"},
        "sync": {"correction": {"max_speed_change": 0.15, "correction_time": 0.5, "threshold": 0.03, "target": 0.01}},
        "midi": {"enabled": True, "port": "*", "channel": 16},
        "logging": {"dir": str(tmp_path / "logs")},
        "cues": [
            {"id": "01", "file": "red.mp4", "midi_note": 36},
            {"id": "02", "file": "green.mp4", "midi_note": 37, "xfade_in_type": "multiply"},
        ],
    }
    data.update(extra)
    cfg = parse_config(data, tmp_path / "show.yaml")
    log = EventLog(cfg.logging.dir, None)
    stage = CompositorStage(
        cfg.stage, log, ipc_address(instance, "compositor"), (0, 0, W, H),
        log_file=tmp_path / "compositor.log", topmost=False,
    )
    ctx = DeckContext(
        config=cfg, log=log, mpv_path=MPV or "mpv", supported_options=supported_options(MPV) if MPV else None,
        instance=instance, log_dir=cfg.logging.dir, session="ct", keybinds=keybinds_for(cfg), compositor=stage,
    )
    decks = {n: Deck(n, r, ctx, listener=None) for n, r in (("F", "filler"), ("A", "cue"), ("B", "cue"))}
    ctl = ShowController(cfg, log, decks, stage, None, StateStore(tmp_path / f"{instance}-state.json"), picture=CompositorPicture(stage))
    for d in decks.values():
        d.listener = ctl
    stage.on_restart = ctl.compositor_restarted
    return ctl, decks, stage


async def picture(stage) -> tuple[int, int, int]:
    data = await stage.request({"name": "screenshot", "x": W // 2, "y": H // 2})
    return tuple(data["pixel"])  # type: ignore[return-value]


async def diagnose(stage) -> str:
    status = await stage.request({"name": "status"})
    layers = {}
    for name in status["layers"]:
        layers[name] = (await stage.request({"name": "screenshot", "layer": name, "x": W // 2, "y": H // 2}))["pixel"]
    return f"status {status} layer pixels {layers}"


def close(a, b, tol=12) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.skipif(MPV is None, reason="needs mpv")
def test_show_crossfades_in_and_out_with_the_compositor(clips, tmp_path):
    async def scenario():
        ctl, decks, stage = compositor_show(clips, tmp_path, "ct-show")
        await stage.open()
        try:
            await ctl.start(fresh=True)
            filler = (0x20, 0x40, 0xA0)
            assert await wait_for(lambda: decks["A"].state == DeckState.READY)
            assert await wait_for(lambda: ctl.mode == Mode.FILLER and decks["F"].state == DeckState.PLAYING)
            await asyncio.sleep(0.7)  # the start-up fade from black is over
            assert close(await picture(stage), filler), await diagnose(stage)
            ctl.on_midi(bytes([0x9F, 36, 100]), now())
            assert ctl.mode == Mode.CUE and ctl.picture.busy("F")
            await asyncio.sleep(0.3)
            mid = await picture(stage)
            assert not close(mid, filler) and not close(mid, (0xE0, 0x30, 0x20)), mid  # in between
            await asyncio.sleep(0.5)
            assert close(await picture(stage), (0xE0, 0x30, 0x20)), await diagnose(stage)
            assert decks["F"].paused  # off stage: paused
            # The out-transition starts 1 s before the 3 s clip ends.
            live = ctl.live
            assert await wait_for(lambda: live.leaving, timeout=3.0)
            assert decks["A"].state == DeckState.PLAYING and not live.ended
            assert await wait_for(lambda: ctl.mode == Mode.FILLER, timeout=3.0)
            await asyncio.sleep(0.2)
            assert close(await picture(stage), filler), await diagnose(stage)
            assert decks["A"].state == DeckState.IDLE or decks["A"].cue is not None and decks["A"].cue.id != "01"
        finally:
            await ctl.shutdown()

    run(scenario())


@pytest.mark.skipif(MPV is None, reason="needs mpv")
def test_compositor_crash_is_recovered(clips, tmp_path):
    async def scenario():
        ctl, decks, stage = compositor_show(clips, tmp_path, "ct-crash")
        await stage.open()
        try:
            await ctl.start(fresh=True)  # also starts the compositor watchdog
            assert await wait_for(lambda: ctl.mode == Mode.FILLER and decks["F"].state == DeckState.PLAYING)
            pid = stage.pid
            os.kill(pid, 9)
            assert await wait_for(lambda: stage.restarts == 1 and stage.pid != pid, timeout=10.0)
            assert await wait_for(lambda: all(d.up for d in decks.values()), timeout=10.0)
            assert await wait_for(lambda: decks["F"].state == DeckState.PLAYING and ctl.visible == "F", timeout=5.0)
            await asyncio.sleep(0.8)
            assert close(await picture(stage), (0x20, 0x40, 0xA0)), await diagnose(stage)
        finally:
            await ctl.shutdown()

    run(scenario())
