"""What the compositor shows: a base layer plus the transitions running over it.

The compositor process draws a :class:`Scene`; the show controller keeps a
mirror of it to know which decks are still part of the picture (a deck that
is fading out must not be reloaded). Both sides use the same clock
(``time.perf_counter`` is system-wide), so they agree on when a transition
has finished.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aethra.blend import Entry, ease, transition_entries


@dataclass
class Transition:
    target: str | None  # a layer, or None for black
    kind: str
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + max(0.0, self.duration)

    def progress(self, t: float) -> float:
        if self.duration <= 0:
            return 1.0 if t >= self.start else 0.0
        return ease((t - self.start) / self.duration)


@dataclass
class Scene:
    base: str | None = None  # on stage when no transition runs (None: black)
    transitions: list[Transition] = field(default_factory=list)
    frozen: float | None = None  # tests: pin the progress of running transitions

    @property
    def target(self) -> str | None:
        """What will be on stage once the running transitions have finished."""
        return self.transitions[-1].target if self.transitions else self.base

    def cut(self, target: str | None) -> None:
        self.base = target
        self.transitions.clear()

    def add(self, transition: Transition) -> None:
        if transition.kind == "cut" or transition.duration <= 0:
            self.cut(transition.target)
        else:
            self.transitions.append(transition)

    def progress(self, tr: Transition, t: float) -> float:
        return tr.progress(t) if self.frozen is None else self.frozen

    def collapse(self, t: float) -> list[Transition]:
        """Drop what finished transitions have covered; returns those transitions.

        A finished transition shows its target at full strength, so the base
        and everything that ran below it no longer matter.
        """
        for i in range(len(self.transitions) - 1, -1, -1):
            if self.progress(self.transitions[i], t) >= 1.0:
                finished = self.transitions[: i + 1]
                self.base = self.transitions[i].target
                self.transitions = self.transitions[i + 1 :]
                return finished
        return []

    def entries(self, t: float) -> list[Entry]:
        """The draw list at time ``t`` (call :meth:`collapse` first)."""
        out = [Entry(self.base, "normal", 1.0)] if self.base is not None else []
        for tr in self.transitions:
            out.extend(transition_entries(tr.kind, tr.target, self.progress(tr, t)))
        return out

    def layers(self, t: float) -> set[str]:
        """Layers that are part of the picture now or once their transition starts."""
        self.collapse(t)
        names = {self.base, *(tr.target for tr in self.transitions)}
        names.discard(None)
        return names  # type: ignore[return-value]

    @property
    def settles_at(self) -> float | None:
        """When the last running transition ends (None: nothing runs)."""
        return max((tr.end for tr in self.transitions), default=None)
