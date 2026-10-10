"""Test doubles for the show controller."""

from __future__ import annotations

import asyncio

from aethra.clock import now
from aethra.deck import DeckState, Load
from aethra.eventlog import EventLog


class FakeDeck:
    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role
        self.state = DeckState.DOWN
        self.listener = None
        self.load_: Load | None = None
        self.duration: float | None = None
        self.time_pos: float | None = None
        self.time_pos_t = 0.0
        self.paused = True
        self.black = 0.0
        self.speed = 1.0
        self.hwnd = None
        self.ipc = None
        self.calls: list[tuple] = []
        self.ping_failures = 0
        self.restarts = 0
        self.spawn_ok = True
        self.blackout_osd = False
        self.adopt_info = None
        self.adopted = False
        self._token = 0

    def __repr__(self) -> str:
        return f"<FakeDeck {self.name} {self.state.value}>"

    @property
    def up(self) -> bool:
        return self.state not in (DeckState.DOWN, DeckState.STARTING)

    @property
    def cue(self):
        return self.load_.cue if self.load_ else None

    async def adopt(self, timeout: float = 0.3):
        if self.adopt_info is not None:
            self.state = DeckState.IDLE
            self.adopted = True
        return self.adopt_info

    async def spawn(self) -> bool:
        self.calls.append(("spawn",))
        self.state = DeckState.IDLE if self.spawn_ok else DeckState.DOWN
        return self.spawn_ok

    def load(self, media, *, cue=None, start=0.0, paused=True, loop=False) -> Load:
        self._token += 1
        old = self.load_
        if old is not None and not old.done.done():
            old.done.set_result(False)
        fut = asyncio.get_running_loop().create_future()
        load = Load(self._token, media, cue, max(0.0, start), paused, loop, now(), fut)
        self.load_ = load
        self.state = DeckState.LOADING
        self.paused = paused
        self.calls.append(("load", cue.id if cue else media, round(load.start, 3), paused))
        return load

    def assume(self, media, cue, playing, start=0.0, ended=False) -> Load:
        load = self.load(media, cue=cue, start=start, paused=not playing)
        load.done.set_result(True)
        self.state = DeckState.ENDED if ended else DeckState.PLAYING if playing else DeckState.READY
        self.adopted = True
        return load

    async def conceal(self) -> None:
        self.calls.append(("conceal",))

    def reveal(self) -> None:
        self.calls.append(("reveal",))

    # -- test helpers -------------------------------------------------------
    def finish_load(self, ok: bool = True, duration: float | None = 200.0) -> None:
        load = self.load_
        assert load is not None and self.state == DeckState.LOADING
        if ok:
            load.ready_at = now()
            self.duration = duration
            self.state = DeckState.READY if load.paused else DeckState.PLAYING
            load.done.set_result(True)
            self.listener.deck_ready(self, load)
        else:
            self.state = DeckState.FAILED
            load.done.set_result(False)
            self.listener.deck_failed(self, load, "broken file")

    def playback_error(self, reason: str = "decoder error") -> None:
        load = self.load_
        self.state = DeckState.FAILED
        self.listener.deck_failed(self, load, reason)

    def reach_eof(self) -> None:
        self.state = DeckState.ENDED
        self.listener.deck_eof(self, self.load_)

    def go_down(self, reason: str = "crash") -> None:
        self.state = DeckState.DOWN
        self.listener.deck_down(self, reason)

    def seeked(self) -> None:
        self.listener.deck_seeked(self)

    # -- Deck API -----------------------------------------------------------
    def play(self) -> None:
        self.calls.append(("play",))
        self.paused = False
        if self.state in (DeckState.READY, DeckState.ENDED):
            self.state = DeckState.PLAYING

    def pause(self) -> None:
        self.calls.append(("pause",))
        self.paused = True

    def unload(self) -> None:
        self.calls.append(("unload",))
        old = self.load_
        self.load_ = None
        if old is not None and not old.done.done():
            old.done.set_result(False)
        self.black = 0.0
        self.speed = 1.0
        if self.up:
            self.state = DeckState.IDLE

    def seek(self, position: float) -> None:
        self.calls.append(("seek", round(position, 3)))

    def set_speed(self, speed: float) -> None:
        self.calls.append(("speed", speed))
        self.speed = speed

    def set_black(self, alpha: float) -> None:
        self.black = alpha

    def fade(self, end: float, duration: float, start=None):
        self.calls.append(("fade", end, duration))

        async def run():
            await asyncio.sleep(duration)
            self.black = end

        return asyncio.ensure_future(run())

    def set_blackout(self, on: bool) -> None:
        self.blackout_osd = on

    def set_test_mode(self, enabled: bool) -> None:
        pass

    def set_info(self, text) -> None:
        pass

    async def quit(self) -> None:
        self.state = DeckState.DOWN

    async def detach(self) -> None:
        self.state = DeckState.DOWN

    def kill(self, reason: str) -> None:
        self.go_down(reason)

    async def ping(self, timeout: float = 1.0) -> bool:
        return self.up

    def loads(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "load"]


class FakeStage:
    def __init__(self, has_backdrop: bool = True) -> None:
        self.has_backdrop = has_backdrop
        self.orders: list[list[str]] = []

    def start(self) -> None:
        pass

    def register(self, deck) -> None:
        pass

    def apply(self, order) -> None:
        self.orders.append(list(order))

    @property
    def top(self) -> str | None:
        return self.orders[-1][0] if self.orders else None

    def maintain(self) -> None:
        pass

    def close(self) -> None:
        pass


def quiet_log() -> EventLog:
    return EventLog(None, None)
