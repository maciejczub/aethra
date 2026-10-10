"""What is on stage and how it changes, independent of the display engine.

The show controller says *what* should be on stage - a deck, or black - and
how to get there (a transition type and a duration). Two implementations:

* :class:`CompositorPicture` - the compositor engine: every deck is a layer of
  one GPU-composed window, so all transition types work as designed (true
  cross-fades and blend modes). The controller keeps a mirror of the
  compositor's scene to know which decks are still part of the picture.
* :class:`WindowPicture` - the window engine: every deck is an mpv window.
  A window is either on top or not, so a transition between two decks dips
  through black (or white for ``fadewhite``): the old deck fades out with an
  overlay, the new one is raised and fades in. Half the time each.

Both answer :meth:`Picture.busy` - whether a deck may still be seen - which the
controller checks before it reloads or pauses a deck.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from aethra.clock import now, sleep_until
from aethra.scene import Scene, Transition
from aethra.stage import BACKDROP, Stage

if TYPE_CHECKING:
    from aethra.deck import Deck

BLACK_TINT = "000000"
WHITE_TINT = "FFFFFF"


def _done_future() -> asyncio.Future[None]:
    fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    fut.set_result(None)
    return fut


class Picture(Protocol):
    composited: bool

    @property
    def visible(self) -> str | None:
        """The deck on stage once running transitions have finished (None: black)."""
        ...

    def busy(self, name: str) -> bool: ...
    def cut(self, target: str | None) -> None: ...
    def transition(self, target: str | None, kind: str, duration: float, start: float | None = None) -> asyncio.Future[None]: ...
    def finish(self) -> None: ...
    def set_blackout(self, on: bool) -> None: ...
    def refresh(self) -> None: ...
    def resync(self) -> None: ...
    def deck_restarted(self, name: str) -> None: ...
    async def detach(self) -> None: ...


class CompositorPicture:
    """Transitions are drawn by the compositor process."""

    composited = True

    def __init__(self, stage: Any) -> None:
        self.stage = stage  # CompositorStage
        self.scene = Scene()
        self.blackout = False

    @property
    def visible(self) -> str | None:
        return self.scene.target

    def busy(self, name: str) -> bool:
        return name in self.scene.layers(now())

    def cut(self, target: str | None) -> None:
        self.scene.cut(target)
        self.stage.send({"name": "show", "layer": target})

    def transition(self, target: str | None, kind: str, duration: float, start: float | None = None) -> asyncio.Future[None]:
        t = now()
        start = t if start is None else start
        if kind == "cut" or duration <= 0:
            self.cut(target)
            return _done_future()
        self.scene.add(Transition(target, kind, start, duration))
        self.stage.send({"name": "transition", "to": target, "type": kind, "duration": duration, "start": start})
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(max(0.0, start + duration - t), lambda: fut.done() or fut.set_result(None))
        return fut

    def finish(self) -> None:
        if self.scene.transitions:
            self.cut(self.scene.target)

    def set_blackout(self, on: bool) -> None:
        self.blackout = on
        self.stage.send({"name": "blackout", "on": on})

    def refresh(self) -> None:
        self.stage.apply([])  # the compositor's window above the backdrop

    def resync(self) -> None:
        """Send the whole picture again: a restarted compositor knows nothing.
        Running transitions go on from where they are (same start time)."""
        self.scene.collapse(now())
        self.stage.send({"name": "show", "layer": self.scene.base})
        for tr in self.scene.transitions:
            self.stage.send({"name": "transition", "to": tr.target, "type": tr.kind, "duration": tr.duration, "start": tr.start})
        self.stage.send({"name": "blackout", "on": self.blackout})
        self.refresh()

    def deck_restarted(self, name: str) -> None:
        pass

    async def detach(self) -> None:
        pass  # the compositor keeps showing the picture on its own


class WindowPicture:
    """Transitions with mpv windows: z-order plus black (or white) overlays."""

    composited = False

    def __init__(self, stage: Stage, decks: dict[str, Deck], background: str, cue_decks: list[str]) -> None:
        self.stage = stage
        self.decks = decks
        self.background = background
        self.cue_decks = cue_decks
        self.blackout = False
        self._target: str | None = None
        self._shown: str | None = None  # the deck on top right now (the old one in a dip's first half)
        self._task: asyncio.Task[None] | None = None
        self._blackout_osd: dict[str, bool] = {}

    @property
    def visible(self) -> str | None:
        return self._target

    def busy(self, name: str) -> bool:
        return name in (self._target, self._shown)

    # -- stacking ----------------------------------------------------------------

    def _stack_order(self) -> list[str]:
        top = self._shown
        backdrop_on_top = (self.blackout or top is None) and self.stage.has_backdrop
        order: list[str] = [BACKDROP] if backdrop_on_top else []
        if top is not None:
            order.append(top)
        if self.background != top:
            order.append(self.background)
        if not backdrop_on_top:
            order.append(BACKDROP)
        order.extend(name for name in self.cue_decks if name != top)
        return order

    def refresh(self) -> None:
        self.stage.apply(self._stack_order())
        if not self.stage.has_backdrop:
            # No backdrop window: the blackout is an overlay in the deck on top.
            for deck in self.decks.values():
                want = self.blackout and deck.name == self._shown
                if self._blackout_osd.get(deck.name, False) != want and deck.up:
                    deck.set_blackout(want)
                    self._blackout_osd[deck.name] = want

    def deck_restarted(self, name: str) -> None:
        """A restarted deck has no overlays."""
        self._blackout_osd.pop(name, None)

    # -- changes -----------------------------------------------------------------

    def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def cut(self, target: str | None) -> None:
        self._cancel()
        self._target = self._shown = target
        deck = self.decks.get(target) if target is not None else None
        if deck is not None and deck.black > 0:
            deck.set_black(0.0)
        self.refresh()

    def transition(self, target: str | None, kind: str, duration: float, start: float | None = None) -> asyncio.Future[None]:
        """The returned future resolves when the transition is over - also when
        a newer one took its place."""
        if kind == "cut" or duration <= 0:
            self.cut(target)
            return _done_future()
        self._cancel()
        old, self._target = self._shown, target
        # Through white only between two pictures; to black is always a fade to black.
        tint = WHITE_TINT if kind == "fadewhite" and target is not None else BLACK_TINT
        task = asyncio.create_task(self._dip(old, target, tint, duration, start))
        self._task = task
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        task.add_done_callback(lambda _t: fut.done() or fut.set_result(None))
        return fut

    async def _dip(self, old: str | None, new: str | None, tint: str, duration: float, start: float | None) -> None:
        if start is not None:
            await sleep_until(start)
        old_deck = self.decks.get(old) if old is not None else None
        new_deck = self.decks.get(new) if new is not None else None
        if old != new and old_deck is not None and old_deck.up:
            # To black takes the whole time; between two decks, half of it.
            out = duration / 2 if new is not None else duration
            # asyncio.wait: a fade that is cancelled (deck died) does not stop the dip.
            await asyncio.wait({old_deck.fade(1.0, out, color=tint)})
        self._shown = new
        if new_deck is None:
            self.refresh()
            return
        if old != new:
            # From black (nothing on stage before) the new deck rises from black.
            new_deck.set_black(1.0, color=tint if old_deck is not None else BLACK_TINT)
        self.refresh()
        into = duration / 2 if old is not None and old != new else duration
        await asyncio.wait({new_deck.fade(0.0, into)})

    def finish(self) -> None:
        self.cut(self._target)

    def set_blackout(self, on: bool) -> None:
        self.blackout = on
        self.refresh()

    def resync(self) -> None:
        self.refresh()

    async def detach(self) -> None:
        # The backdrop dies with the controller: make sure nothing that should
        # not be seen can surface (hidden decks, or everything in a blackout).
        dark = [d for d in self.decks.values() if d.up and (d.name != self._shown or self.blackout)]
        await asyncio.gather(*(d.conceal() for d in dark), return_exceptions=True)
