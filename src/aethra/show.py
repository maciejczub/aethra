"""The show controller: decides what is on stage.

Decks: ``F`` (the background: intro, filler or outro) and two cue decks, ``A``
and ``B``. While a cue plays on one cue deck, the other holds the next cue
paused on its first frame. A trigger then only needs to unpause that deck and
put it on stage (:mod:`aethra.picture`), with the cue's in-transition. The
cue's out-transition starts ``xfade_out_duration`` before the clip ends, so the
background (or black) has taken over when the last frame is reached. A deck
that is still part of the picture (fading out) is never reloaded: it is
retired when its transition is over.

Timing model: a trigger at host time ``t0`` means "clip position
``clip_at_t0`` belongs to ``t0``", where ``clip_at_t0 = cue.offset +
sync.latency_compensation``. A deck paused at clip position ``p`` therefore
goes live at ``t0 + (p - clip_at_t0)``. A deck that was not preloaded (an
out-of-order cue, a crashed deck) is loaded paused a little *ahead* of the
expected position and released exactly when real time catches up, so even a
cold start is in sync from its first visible frame.

All methods run on the asyncio loop thread; inputs from other threads are
marshalled with ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import enum
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aethra.clock import WallClock, now, sleep_until
from aethra.config import Config, Cue, FillerConfig
from aethra.deck import Deck, DeckState, Load
from aethra.eventlog import EventLog
from aethra.keys import KeySpecError, parse_key_spec
from aethra.mtc import MtcDecoder, format_seconds
from aethra.osc import OscMessage
from aethra.picture import Picture, WindowPicture
from aethra.stage import Stage
from aethra.sync import DriftCorrector, Seek, SetSpeed

STAT_PROPS = ("frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count")
MAX_LOAD_FAILURES = 3


class Mode(str, enum.Enum):
    STARTING = "starting"
    INTRO = "intro"
    FILLER = "filler"
    OUTRO = "outro"
    CUE = "cue"
    HOLD = "hold"
    BLACK = "black"


BACKGROUND_KINDS = ("intro", "filler", "outro")
BACKGROUND_MODES = (Mode.INTRO, Mode.FILLER, Mode.OUTRO)


@dataclass(eq=False)
class Live:
    cue: Cue
    deck: Deck
    t0: float
    clip_at_t0: float
    source: str
    went_live_at: float
    corrector: DriftCorrector | None = None
    ended: bool = False
    load: Load | None = None
    leaving: bool = False  # its out-transition has started
    out_task: asyncio.Task[None] | None = None


@dataclass(eq=False)
class PendingStart:
    cue: Cue
    deck: Deck
    t0: float
    clip_at_t0: float
    source: str
    load: Load | None
    position: float
    resume: bool = False
    attempt: int = 0
    task: asyncio.Task[None] | None = None


@dataclass
class InputStatus:
    midi: str = "off"
    osc: str = "off"
    keys: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class StateStore:
    """Small JSON file that lets a restarted controller pick up where it was."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, max_age: float = 6 * 3600) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or time.time() - float(data.get("saved_at", 0)) > max_age:
            return None
        return data

    def save(self, data: dict[str, Any]) -> None:
        data = dict(data, saved_at=time.time())
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


class ShowController:
    def __init__(
        self,
        cfg: Config,
        log: EventLog,
        decks: dict[str, Deck],
        stage: Stage,
        mtc: MtcDecoder | None = None,
        state_store: StateStore | None = None,
        wall: WallClock | None = None,
        picture: Picture | None = None,
    ) -> None:
        self.cfg = cfg
        self.log = log
        self.decks = decks
        self.filler_deck = decks["F"]
        self.cue_decks = [decks["A"], decks["B"]]
        self.stage = stage
        self.picture: Picture = picture or WindowPicture(stage, decks, "F", ["A", "B"])
        self.mtc = mtc
        self.state_store = state_store
        self.wall = wall or WallClock()
        self.mode = Mode.STARTING
        self.live: Live | None = None
        self.pending: PendingStart | None = None
        self.blackout = False
        self.test_mode = cfg.test_mode
        self.selected = 0
        self.reaper_playing: bool | None = None
        self.inputs = InputStatus()
        self.stopping = False
        self.stop_event = asyncio.Event()
        self.on_status: Callable[[str], None] | None = None
        self.on_panel_focus: Callable[[bool], None] | None = None
        self._transition: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._respawning: set[str] = set()
        self._load_lead = 0.35
        self._load_failures: dict[str, int] = {}
        self._quit_armed_at: float | None = None
        self._mtc_was_running = False
        self._last_trigger: tuple[str, str, float] | None = None
        # Cues that left the stage but whose deck may still be seen (fading out).
        self._retiring: list[Live] = []
        # A cue whose deck died while no other deck could take over (cue, t0, clip_at_t0).
        self._lost: tuple[Cue, float, float] | None = None
        self._stage_keys: dict[str, str] = {}
        if cfg.keyboard.stage_window:
            for command, specs in cfg.keyboard.keys.items():
                for spec in specs:
                    try:
                        self._stage_keys[parse_key_spec(spec).canonical] = command
                    except KeySpecError:
                        pass
        # What the background deck F holds (or is loading): intro, filler or outro.
        self.background_kind = "filler"
        self._notes = {c.midi_note: c for c in cfg.cues if c.midi_note is not None}
        self._programs = {c.midi_program: c for c in cfg.cues if c.midi_program is not None}
        self._command_notes = {note: cmd for cmd, note in cfg.midi.command_notes.items()}

    # ------------------------------------------------------------------ helpers

    @property
    def cues(self) -> tuple[Cue, ...]:
        return self.cfg.cues

    @property
    def visible(self) -> str | None:
        """The deck on stage (or coming on stage); None: black."""
        return self.picture.visible

    @property
    def selected_cue(self) -> Cue | None:
        return self.cues[self.selected] if 0 <= self.selected < len(self.cues) else None

    def _spawn_task(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.log.error("internal_error", f"task {task.get_name()} failed: {exc!r}")

    def _clip_at_trigger(self, cue: Cue) -> float:
        return cue.offset + self.cfg.sync.latency_compensation

    def _background_config(self, kind: str) -> FillerConfig:
        return {"intro": self.cfg.intro, "outro": self.cfg.outro}.get(kind) or self.cfg.filler

    def _background_media(self, kind: str, force_color: bool = False) -> tuple[str, bool]:
        f = self._background_config(kind)
        if not force_color and f.mode in ("loop", "image") and f.file is not None and f.file.is_file():
            return str(f.file), f.mode == "loop"
        s = self.cfg.stage
        color = f.color.lstrip("#")
        return f"av://lavfi:color=c=0x{color}:s={s.width}x{s.height}:r={s.fps:g}", False

    def _has_background(self, kind: str) -> bool:
        return kind == "filler" or (kind == "intro" and self.cfg.intro is not None) or (kind == "outro" and self.cfg.outro is not None)

    @staticmethod
    def _mode_for(kind: str) -> Mode:
        return {"intro": Mode.INTRO, "outro": Mode.OUTRO}.get(kind, Mode.FILLER)

    def _is_last(self, cue: Cue) -> bool:
        return bool(self.cues) and cue is self.cues[-1]

    def _after(self, cue: Cue) -> str:
        """The background that follows a cue: the outro after the last song."""
        return "outro" if self._is_last(cue) and self.cfg.outro is not None else "filler"

    def _wanted_background(self, saved_mode: str | None = None) -> str:
        if self.live is not None:
            return self._after(self.live.cue)
        if saved_mode in BACKGROUND_KINDS and self._has_background(saved_mode):
            return saved_mode
        return "intro" if self.cfg.intro is not None else "filler"

    # ------------------------------------------------------------------ startup

    async def start(self, fresh: bool = False) -> None:
        state = None if fresh or self.state_store is None else self.state_store.load()

        # 1. Reconnect to players left running by a previous controller.
        all_decks = [self.filler_deck, *self.cue_decks]
        infos: dict[str, dict[str, Any] | None] = {}
        adopted = await asyncio.gather(*(deck.adopt() for deck in all_decks))
        for deck, info in zip(all_decks, adopted):
            if info is not None and fresh:
                await deck.quit()
                info = None
            infos[deck.name] = info
        if state:
            sel = state.get("selected")
            if isinstance(sel, int) and 0 <= sel <= len(self.cues):
                self.selected = sel
            self.blackout = bool(state.get("blackout", False))
        continuing = self._continue_live(state, infos)

        # 2. Only now the backdrop appears, at its place below a deck that is
        #    still playing: a restarted controller does not interrupt the picture.
        for deck in all_decks:
            if deck.up:
                self.stage.register(deck)
        self.stage.start()
        self.picture.set_blackout(self.blackout)
        for deck in all_decks:
            if deck.up and deck.adopted:
                deck.reveal()

        # 3. Start the players that are missing.
        missing = [deck for deck in all_decks if not deck.up]
        await asyncio.gather(*(deck.spawn() for deck in missing))
        for deck in missing:
            if deck.up:
                self.stage.register(deck)
            else:
                self._schedule_respawn(deck)

        for deck in self.cue_decks:
            if deck.up and (self.live is None or self.live.deck is not deck):
                deck.unload()
        self._restore_background(infos.get(self.filler_deck.name), (state or {}).get("mode"))
        if not continuing:
            # Fresh start: the intro if there is one (the first cue is preloaded
            # meanwhile); after a restart: whatever background was on stage.
            self.mode = self._mode_for(self.background_kind)
            background = self.filler_deck
            if background.state == DeckState.PLAYING and background.adopted:
                self._cut_to_background()  # it was on stage already: no new fade
            elif background.state in (DeckState.PLAYING, DeckState.READY):
                self._fade_in_background()
            self._resume_after_restart(state)
        self.picture.refresh()
        self._preload_next()
        if self.test_mode:
            self.set_test_mode(True)
        self._spawn_task(self._watchdog(), "watchdog")
        self._spawn_task(self._status_loop(), "status")
        self.log.info("show_ready", f"show {self.cfg.name!r}: {len(self.cues)} cues, next {self._cue_text(self.selected_cue)}")
        self._save_state()

    def _saved_live(self, state: dict[str, Any] | None) -> tuple[Cue, Deck, float, float, bool] | None:
        if not state or not isinstance(state.get("live"), dict):
            return None
        live = state["live"]
        cue = self.cfg.cue_by_id(str(live.get("cue")))
        deck = self.decks.get(str(live.get("deck")))
        if cue is None or deck is None or deck not in self.cue_decks:
            return None
        try:
            clip_at_t0 = float(live["clip_at_t0"])
            t0 = self._restore_time(live, state)
        except (KeyError, TypeError, ValueError):
            return None
        if t0 is None:
            return None
        return cue, deck, t0, clip_at_t0, bool(live.get("ended", False))

    @staticmethod
    def _restore_time(live: dict[str, Any], state: dict[str, Any]) -> float | None:
        """The trigger time of a cue saved by a previous controller, on our clock.

        perf_counter is system-wide and monotonic until the next reboot, so it
        survives a controller restart unaffected by clock adjustments; the wall
        clock is only the fallback (after a reboot the counter starts again).
        """
        t0_mono, saved_mono = live.get("t0_mono"), state.get("saved_mono")
        if isinstance(t0_mono, (int, float)) and isinstance(saved_mono, (int, float)) and now() >= saved_mono >= t0_mono:
            return float(t0_mono)
        elapsed = time.time() - float(live["t0_wall"])
        return now() - elapsed if elapsed >= 0 else None

    def _continue_live(self, state: dict[str, Any] | None, infos: dict[str, dict[str, Any] | None]) -> bool:
        """A deck kept playing (or holding its last frame) while the controller
        was away: adopt it as live."""
        saved = self._saved_live(state)
        if saved is None:
            return False
        cue, deck, t0, clip_at_t0, ended = saved
        info = infos.get(deck.name)
        if not info or info.get("path") != str(cue.file):
            return False
        saved_mode = str((state or {}).get("mode"))
        if ended or info.get("eof"):
            if not (info.get("eof") and saved_mode in (Mode.HOLD.value, Mode.BLACK.value)):
                return False
            load = deck.assume(str(cue.file), cue, playing=False, ended=True)
            self.live = Live(cue, deck, t0, clip_at_t0, "restored", now(), None, ended=True, load=load)
            self.mode = Mode(saved_mode)
        elif info.get("pause"):
            return False
        else:
            load = deck.assume(str(cue.file), cue, playing=True)
            self.live = Live(cue, deck, t0, clip_at_t0, "restored", now(), self._new_corrector(cue, t0, clip_at_t0), load=load)
            self.mode = Mode.CUE
        self.picture.cut(None if self.mode == Mode.BLACK else deck.name)
        self.log.info("cue_restored", f"cue {cue.label} is still on stage (deck {deck.name}, {self.mode.value}); continuing", cue=cue.id)
        return True

    def _resume_after_restart(self, state: dict[str, Any] | None) -> None:
        """The previous controller died mid-cue and the deck is gone: rejoin the cue."""
        saved = self._saved_live(state)
        if saved is None or not self.cfg.sync.resume_after_crash:
            return
        cue, _deck, t0, clip_at_t0, ended = saved
        if ended:
            return
        free = self._free_cue_deck()
        if free is None:
            return
        self.log.info("cue_resume", f"rejoining cue {cue.label} at its expected position after a restart", cue=cue.id)
        self._cold_start(free, cue, t0, clip_at_t0, "resume", resume=True)

    def _restore_background(self, info: dict[str, Any] | None, saved_mode: str | None) -> None:
        deck = self.filler_deck
        wanted = self._wanted_background(saved_mode)
        if not deck.up:
            self.background_kind = wanted
            return
        if info and not info.get("eof"):
            for kind in (wanted, *BACKGROUND_KINDS):
                if self._has_background(kind) and info.get("path") == self._background_media(kind)[0]:
                    deck.assume(info["path"], None, playing=not info.get("pause"))
                    self.background_kind = kind
                    return
        self._load_background(wanted)

    def _load_background(self, kind: str, force_color: bool = False, paused: bool | None = None) -> Load | None:
        deck = self.filler_deck
        self.background_kind = kind
        if not deck.up:
            return None
        media, loop = self._background_media(kind, force_color)
        if paused is None:
            # The background only plays while it is on stage.
            paused = not self.picture.busy(deck.name) and not (self.live is None and self.mode in (Mode.STARTING, *BACKGROUND_MODES))
        return deck.load(media, start=0.0, paused=paused, loop=loop)

    async def _background_ready(self, timeout: float = 5.0) -> bool:
        """Wait until the background deck has its current load ready (it may be
        replaced by a fallback colour on the way)."""
        deck = self.filler_deck
        deadline = now() + timeout
        while now() < deadline:
            if deck.up and deck.state in (DeckState.PLAYING, DeckState.READY):
                return True
            load = deck.load_
            if not deck.up or load is None:
                return False
            if not load.done.done():
                await asyncio.wait({load.done}, timeout=max(0.0, deadline - now()))
            elif deck.load_ is load:
                return deck.state in (DeckState.PLAYING, DeckState.READY)
            else:
                await asyncio.sleep(0.01)  # a fallback load replaced it
        return False

    # ------------------------------------------------------------------ triggers

    def trigger(self, cue: Cue, t0: float, source: str) -> None:
        guard = self.cfg.sync.retrigger_guard
        for current in (self.pending, self.live):
            if current is not None and current.cue is cue and 0 <= t0 - current.t0 < guard:
                self.log.info("trigger_ignored", f"{source}: {cue.id} again after {(t0 - current.t0) * 1000:.0f} ms (retrigger guard)", cue=cue.id, source=source)
                return
        self.log.info("trigger", f"{source} -> cue {cue.label}", t=t0, cue=cue.id, source=source)
        self._last_trigger = (cue.id, source, t0)
        self._cancel_pending("superseded")
        # A running fade to the filler is not cancelled here but when the new
        # cue actually goes live: if it cannot start, the filler still arrives.
        self.selected = min(cue.index + 1, len(self.cues))
        clip_at_t0 = self._clip_at_trigger(cue)
        deck = self._preloaded_deck(cue, clip_at_t0)
        if deck is not None:
            assert deck.load_ is not None
            self.pending = PendingStart(cue, deck, t0, clip_at_t0, source, deck.load_, deck.load_.start)
            if deck.state == DeckState.READY:
                self._arm(self.pending)
            # else: still loading - deck_ready() arms it
        else:
            deck = self._free_cue_deck(steal=True)
            if deck is None:
                self.log.error("no_deck", f"no cue deck is available for {cue.label}; cue skipped", cue=cue.id)
                return
            self._cold_start(deck, cue, t0, clip_at_t0, source)
        self._save_state()

    def _preloaded_deck(self, cue: Cue, clip_at_t0: float) -> Deck | None:
        start = max(0.0, clip_at_t0)
        for deck in self.cue_decks:
            if self.live is not None and self.live.deck is deck:
                continue
            load = deck.load_
            if (
                load is not None
                and load.cue is cue
                and deck.state in (DeckState.READY, DeckState.LOADING)
                and abs(load.start - start) < 1e-3
            ):
                return deck
        return None

    def _free_cue_deck(self, steal: bool = False) -> Deck | None:
        """A cue deck that is neither live, nor starting, nor part of the picture.

        ``steal``: when both cue decks are on stage (a cross-fade between two
        cues), end the transition now to free the deck that is leaving.
        """
        def candidates() -> list[Deck]:
            return [
                d
                for d in self.cue_decks
                if d.up
                and (self.live is None or self.live.deck is not d)
                and (self.pending is None or self.pending.deck is not d)
                and not self.picture.busy(d.name)
            ]

        found = candidates()
        if not found and steal and self._retiring:
            self.log.info("transition_cut", "both cue decks are on stage: ending the running transition now")
            self.picture.finish()
            self._retire_unused()
            found = candidates()
        if not found:
            return None
        # Prefer an empty deck over one that holds a preload.
        rank = {DeckState.IDLE: 0, DeckState.FAILED: 0, DeckState.ENDED: 1, DeckState.READY: 2, DeckState.LOADING: 3}
        found.sort(key=lambda d: rank.get(d.state, 4))
        return found[0]

    def _cold_start(self, deck: Deck, cue: Cue, t0: float, clip_at_t0: float, source: str, resume: bool = False) -> None:
        target = clip_at_t0 + (now() - t0) + self._load_lead
        target = max(target, clip_at_t0, 0.0)
        load = deck.load(str(cue.file), cue=cue, start=target, paused=True)
        self.pending = PendingStart(cue, deck, t0, clip_at_t0, source, load, target, resume=resume)
        self.log.info(
            "cold_start",
            f"cue {cue.id} was not preloaded; loading on deck {deck.name} at {target:.3f} s",
            cue=cue.id, deck=deck.name, position=round(target, 3), resume=resume,
        )

    def _arm(self, pending: PendingStart) -> None:
        if self.pending is not pending:
            return
        duration = pending.deck.duration
        if duration is not None and pending.position >= duration - 0.5:
            self.pending = None
            self.log.warn("start_skipped", f"cue {pending.cue.id}: expected position {pending.position:.1f} s is past the end of the clip", cue=pending.cue.id)
            pending.deck.unload()
            self._preload_next()
            return
        frame = 1.0 / self.cfg.stage.fps
        go_time = pending.t0 + (pending.position - pending.clip_at_t0)
        late = now() - go_time
        if late <= frame:
            if go_time > now():
                pending.task = self._spawn_task(self._go_live_at(pending, go_time), f"go-{pending.cue.id}")
            else:
                self._go_live(pending)
            return
        if pending.attempt < 2:
            pending.attempt += 1
            pending.position = pending.clip_at_t0 + (now() - pending.t0) + self._load_lead * (1 + pending.attempt)
            self.log.info(
                "start_reseek",
                f"cue {pending.cue.id}: deck ready {late * 1000:.0f} ms too late; re-seeking to {pending.position:.3f} s",
                cue=pending.cue.id,
            )
            pending.deck.seek(pending.position)
            return
        self.log.warn("start_late", f"cue {pending.cue.id} starts {late * 1000:.0f} ms late", cue=pending.cue.id, late_ms=round(late * 1000, 1))
        self._go_live(pending, late)

    async def _go_live_at(self, pending: PendingStart, go_time: float) -> None:
        await sleep_until(go_time)
        self._go_live(pending)

    def _go_live(self, pending: PendingStart, late: float = 0.0) -> None:
        if self.pending is not pending:
            return
        self.pending = None
        deck, cue = pending.deck, pending.cue
        if deck.load_ is not pending.load or deck.state != DeckState.READY:
            self.log.error("start_failed", f"cue {cue.id}: deck {deck.name} is not ready ({deck.state.value})", cue=cue.id)
            return
        self._cancel_transition()
        deck.play()
        t_live = now()
        # A cue that rejoins after a crash comes back at once.
        kind, duration = ("cut", 0.0) if pending.resume else (cue.transitions.xfade_in_type, cue.transitions.xfade_in_duration)
        done = self.picture.transition(deck.name, kind, duration, start=t_live)
        old_live = self.live
        self.live = Live(
            cue, deck, pending.t0, pending.clip_at_t0, pending.source, t_live,
            self._new_corrector(cue, pending.t0, pending.clip_at_t0), load=pending.load,
        )
        self.mode = Mode.CUE
        self._lost = None
        self.log.info(
            "cue_live",
            f"cue {cue.label} on deck {deck.name} ({(t_live - pending.t0) * 1000:.1f} ms after trigger)"
            + (f", {kind} {duration:g} s" if duration > 0 and kind != "cut" else ""),
            t=t_live, cue=cue.id, deck=deck.name, source=pending.source,
            trigger_to_play_ms=round((t_live - pending.t0) * 1000, 2), position=round(pending.position, 3),
            late_ms=round(late * 1000, 1), resume=pending.resume, transition=kind, transition_s=duration,
        )
        if old_live is not None and old_live.deck is not deck and old_live not in self._retiring:
            self._retiring.append(old_live)
        # What the transition still shows (the previous cue, the background) is
        # released when it is over; whatever is free already, right now.
        done.add_done_callback(lambda _f: self._settle())
        self._settle()
        self._save_state()

    def _new_corrector(self, cue: Cue, t0: float, clip_at_t0: float) -> DriftCorrector | None:
        if not self.cfg.sync.correction.enabled:
            return None
        use_mtc = self.mtc is not None and self.cfg.sync.mtc.enabled
        corrector = DriftCorrector(self.cfg.sync.correction, self.cfg.stage.fps, use_mtc=use_mtc)
        anchor = cue.mtc_start
        if anchor is None and use_mtc:
            snap = self.mtc.snapshot(t0)  # type: ignore[union-attr]
            anchor = snap.position if snap.running else None
        corrector.start(now(), t0, clip_at_t0, anchor)
        return corrector

    def _retire_unused(self) -> None:
        """Unload the decks of cues that left the stage, once they cannot be seen."""
        for live in list(self._retiring):
            if self.live is live:
                self._retiring.remove(live)
            elif not self.picture.busy(live.deck.name):
                self._retiring.remove(live)
                still_loaded = live.load is None or live.deck.load_ is live.load
                if still_loaded and (self.pending is None or self.pending.deck is not live.deck):
                    self._retire(live)

    def _settle(self) -> None:
        """Free what the picture no longer shows: retire the decks of cues that
        went off stage, pause the background off stage and prepare the one that
        follows the live cue, preload the next cue."""
        if self.stopping:
            return
        self._retire_unused()
        background = self.filler_deck
        transition_running = self._transition is not None and not self._transition.done()
        if not self.picture.busy(background.name) and not transition_running and background.up:
            if not background.paused and background.state in (DeckState.PLAYING, DeckState.READY):
                background.pause()
            if self.live is not None:
                # Prepare, out of sight, the background that comes after this cue.
                following = self._after(self.live.cue)
                if following != self.background_kind or background.state in (DeckState.FAILED, DeckState.IDLE):
                    self._load_background(following, paused=True)
        self._preload_next()

    def _retire(self, live: Live) -> None:
        deck = live.deck
        if deck.up and deck.ipc is not None:
            # mpv answers in order: these replies describe the clip before "stop".
            futures = [deck.ipc.request(["get_property", p], 1.0) for p in STAT_PROPS]
            self._spawn_task(self._log_cue_stats(live, futures), f"stats-{live.cue.id}")
        deck.unload()

    async def _log_cue_stats(self, live: Live, futures: list[asyncio.Future[Any]]) -> None:
        results = await asyncio.gather(*futures, return_exceptions=True)
        stats = {p.replace("-", "_"): (r if not isinstance(r, BaseException) else None) for p, r in zip(STAT_PROPS, results)}
        c = live.corrector
        if c is not None:
            stats.update(
                max_drift_ms=round(c.max_abs_drift * 1000, 1),
                speed_corrections=c.corrections,
                hard_seeks=c.seeks,
            )
        played = now() - live.went_live_at
        self.log.info("cue_stats", f"cue {live.cue.id}: played {played:.1f} s, {stats}", cue=live.cue.id, played_s=round(played, 3), **stats)

    def _preload_next(self) -> None:
        # Past the end of the list GO does nothing, but keep the first cue ready
        # anyway: an encore or a second pass then starts instantly as well.
        cue = self.selected_cue or (self.cues[0] if self.cues else None)
        if cue is None or self._load_failures.get(cue.id, 0) >= MAX_LOAD_FAILURES:
            return
        start = max(0.0, self._clip_at_trigger(cue))
        for deck in self.cue_decks:
            load = deck.load_
            if (
                load is not None
                and load.cue is cue
                and deck.state in (DeckState.LOADING, DeckState.READY)
                and (self.live is None or self.live.deck is not deck)
                and abs(load.start - start) < 1e-3
            ):
                return
        deck = self._free_cue_deck()
        if deck is None:
            return
        self.log.debug("preload", f"preloading cue {cue.id} on deck {deck.name}", cue=cue.id, deck=deck.name)
        deck.load(str(cue.file), cue=cue, start=start, paused=True)

    def _cancel_pending(self, reason: str) -> None:
        pending, self.pending = self.pending, None
        if pending is None:
            return
        if pending.task is not None:
            pending.task.cancel()
        self.log.info("start_cancelled", f"pending start of cue {pending.cue.id} cancelled ({reason})", cue=pending.cue.id)

    def _cancel_transition(self) -> None:
        if self._transition is not None and not self._transition.done():
            self._transition.cancel()
        self._transition = None

    # ------------------------------------------------------------------ endings, filler, blackout

    def _schedule_out(self, live: Live, position: float, t: float) -> None:
        """Start the cue's out-transition so that it is over when the clip ends."""
        cue = live.cue
        out = cue.transitions.xfade_out_duration
        duration = live.deck.duration
        if cue.on_end == "hold" or out <= 0 or not duration:
            return
        until = (duration - out) - position  # clip seconds before the transition starts
        if until > 1.0:
            return
        start = t + max(0.0, until) / max(live.deck.speed, 0.5)
        live.out_task = self._spawn_task(self._out_at(live, start), f"out-{cue.id}")

    async def _out_at(self, live: Live, start: float) -> None:
        await sleep_until(start)
        if self.live is live and not live.leaving and not live.ended:
            self._end_cue(live, "end of clip", start=start)

    def _end_cue(self, live: Live, reason: str, start: float | None = None) -> None:
        """The cue leaves the stage the way it is configured to (``on_end``)."""
        if live.leaving or self.live is not live:
            return
        if live.cue.on_end == "filler":
            self.to_background(self._after(live.cue), reason, start=start)
        elif live.cue.on_end == "black":
            live.leaving = True
            self._cancel_transition()
            t = live.cue.transitions
            done = self.picture.transition(None, t.xfade_out_type, t.xfade_out_duration, start=start)
            done.add_done_callback(lambda _f: self._settle())
            self.log.info("cue_out", f"cue {live.cue.id}: to black ({reason})", cue=live.cue.id)

    def _cue_ended(self, live: Live) -> None:
        live.ended = True
        self.log.info("cue_end", f"cue {live.cue.id} reached its end ({live.cue.on_end})", cue=live.cue.id)
        if live.cue.on_end == "hold":
            self.mode = Mode.HOLD
        else:
            # Without an out-transition running yet (none configured, or the
            # clip ended early), the cue leaves now.
            self._end_cue(live, "end of clip")
            if live.cue.on_end == "black":
                self.mode = Mode.BLACK
        self._save_state()

    def to_filler(self, reason: str) -> None:
        self.to_background("filler", reason)

    def to_background(self, kind: str, reason: str, start: float | None = None) -> None:
        """Take whatever is on stage off and bring the intro, filler or outro on."""
        if (
            self.live is None
            and self.visible == self.filler_deck.name
            and self.mode == self._mode_for(kind)
            and self.background_kind == kind
        ):
            return
        self._cancel_transition()
        live = self.live
        if live is not None:
            live.leaving = True
        self._transition = self._spawn_task(self._background_transition(live, kind, reason, start), f"to-{kind}")

    async def _background_transition(self, live: Live | None, kind: str, reason: str, start: float | None) -> None:
        background = self.filler_deck
        if live is not None:
            # Out of a cue: the cue's out-transition.
            kind_t, duration = live.cue.transitions.xfade_out_type, live.cue.transitions.xfade_out_duration
        else:
            # Between intro, filler and outro: through black (one background deck).
            kind_t, duration = "fade", self.cfg.transitions.xfade_out_duration
        if self.background_kind != kind or background.state not in (DeckState.PLAYING, DeckState.READY):
            # The background deck must load other content first: through black,
            # half of the time out, half in.
            half = duration / 2
            to_black = self.picture.transition(None, "fade", half, start)
            if self.picture.busy(background.name):
                await to_black  # it is still on stage: reload it only once it is dark
            self._load_background(kind, paused=False)
            await self._background_ready()
            await to_black
            kind_t, duration, start = "fade", half, None
        shown = background.up and background.state in (DeckState.PLAYING, DeckState.READY)
        if shown:
            background.play()
            await self.picture.transition(background.name, kind_t, duration, start)
        if self.live is live:
            self.live = None
        self.mode = self._mode_for(kind)
        if live is not None and live not in self._retiring:
            self._retiring.append(live)
        self.log.info(kind, f"{kind} on stage ({reason})" if shown else f"black on stage, {kind} unavailable ({reason})")
        self._transition = None
        self._settle()
        self._save_state()

    def _cut_to_background(self) -> bool:
        """The background on stage at once (a deck died); black if it is not ready."""
        deck = self.filler_deck
        ok = deck.up and deck.state in (DeckState.PLAYING, DeckState.READY)
        if ok:
            deck.play()
        self.picture.cut(deck.name if ok else None)
        return ok

    def _fade_in_background(self) -> bool:
        """From black to the background (start of the show, background ready again)."""
        deck = self.filler_deck
        if not (deck.up and deck.state in (DeckState.PLAYING, DeckState.READY)):
            return False
        deck.play()
        self.picture.transition(deck.name, "fade", self.cfg.transitions.xfade_out_duration / 2)
        return True

    def set_blackout(self, on: bool | None, source: str) -> None:
        new = (not self.blackout) if on is None else bool(on)
        if new == self.blackout:
            return
        self.blackout = new
        self.picture.set_blackout(new)
        self.log.info("blackout", f"blackout {'ON' if new else 'OFF'} ({source})", on=new, source=source)
        self._save_state()

    def set_test_mode(self, enabled: bool) -> None:
        self.test_mode = enabled
        for deck in self.decks.values():
            deck.set_test_mode(enabled)
        self.log.info("test_mode", f"test overlay {'on' if enabled else 'off'}")

    def select(self, index: int, source: str) -> None:
        index = max(0, min(index, len(self.cues)))
        if index == self.selected:
            return
        self.selected = index
        self.log.info("select", f"next cue: {self._cue_text(self.selected_cue)} ({source})", index=index)
        self._preload_next()
        self._save_state()

    # ------------------------------------------------------------------ commands and inputs

    def command(self, name: str, source: str, arg: Any = None) -> None:
        if name == "go":
            cue = self.selected_cue
            if cue is None:
                self.log.warn("command", f"go ({source}): no more cues")
                return
            self.log.info("command", f"go ({source})")
            self.trigger(cue, now(), f"go ({source})")
        elif name == "next":
            self.select(self.selected + 1, source)
        elif name == "prev":
            self.select(self.selected - 1, source)
        elif name == "black":
            self.set_blackout(arg, source)
        elif name in BACKGROUND_KINDS:
            if not self._has_background(name):
                self.log.warn("command", f"{name} ({source}): no {name} is configured")
                return
            self.log.info("command", f"{name} ({source})")
            self._cancel_pending(f"{name} requested")
            self.to_background(name, f"operator, {source}")
        elif name == "test":
            self.set_test_mode(not self.test_mode if arg is None else bool(arg))
        elif name == "quit":
            t = now()
            if self._quit_armed_at is not None and t - self._quit_armed_at < 3.0:
                self.log.info("quit", f"quit requested ({source})")
                self.stop_event.set()
            else:
                self._quit_armed_at = t
                self.log.warn("quit", "press quit again within 3 seconds to stop the show")
        else:
            self.log.warn("command", f"unknown command {name!r} from {source}")

    def on_midi(self, data: bytes, t: float) -> None:
        if not data:
            return
        status = data[0]
        if status == 0xF1:
            if self.mtc is not None and len(data) >= 2:
                self.mtc.feed_quarter_frame(data[1], t)
            return
        if status == 0xF0:
            if self.mtc is not None:
                self.mtc.feed_full_frame(bytes(data), t)
            return
        if status >= 0xF0:
            return
        kind, channel = status & 0xF0, (status & 0x0F) + 1
        if self.cfg.midi.channel and channel != self.cfg.midi.channel:
            return
        if kind == 0x90 and len(data) >= 3 and data[2] > 0:
            note = data[1]
            if note in self._command_notes:
                self.command(self._command_notes[note], f"midi note {note}")
                return
            cue = self._notes.get(note)
            if cue is not None:
                self.trigger(cue, t, f"midi note {note}")
            else:
                self.log.debug("midi_unmapped", f"note {note} on channel {channel} is not mapped")
        elif kind == 0xC0 and len(data) >= 2:
            cue = self._programs.get(data[1])
            if cue is not None:
                self.trigger(cue, t, f"midi program {data[1]}")

    def on_osc(self, message: OscMessage, addr: tuple[str, int], t: float) -> None:
        address, args = message.address, message.args
        if address == "/play" and args and isinstance(args[0], (int, float)):
            self.reaper_playing = float(args[0]) >= 0.5
        elif address == "/stop" and args and isinstance(args[0], (int, float)) and float(args[0]) >= 0.5:
            self.reaper_playing = False
        prefix = self.cfg.osc.prefix + "/"
        if address.startswith(prefix):
            self._osc_command(address[len(prefix):], args, addr, t)
            return
        for cue in self.cues:
            if cue.osc is not None and cue.osc.matches(address, args):
                if self.cfg.osc.require_playing and self.reaper_playing is not True:
                    self.log.info("trigger_ignored", f"osc {address}: REAPER is not playing", cue=cue.id)
                    return
                self.trigger(cue, t, f"osc {address}")
                return

    def _osc_command(self, name: str, args: tuple[Any, ...], addr: tuple[str, int], t: float) -> None:
        source = f"osc {addr[0]}"
        if name == "cue":
            cue = self._cue_from_arg(args[0] if args else None)
            if cue is None:
                self.log.warn("osc", f"/cue: unknown cue {args[0] if args else '(missing)'}")
            else:
                self.trigger(cue, t, f"osc /cue {cue.id}")
        elif name in ("go", "next", "prev", "filler", "intro", "outro", "test"):
            if args and isinstance(args[0], (int, float)) and not isinstance(args[0], bool) and float(args[0]) < 0.5 and name != "test":
                return  # button release from control surfaces
            self.command(name, source, bool(args[0]) if name == "test" and args else None)
        elif name == "black":
            on = None
            if args and isinstance(args[0], (int, float, bool)):
                on = float(args[0]) >= 0.5
            self.command("black", source, on)
        elif name == "select" and args and isinstance(args[0], (int, float)) and not isinstance(args[0], bool):
            self.select(int(args[0]) - 1, source)  # 1-based, like /cue
        elif name == "panel" and addr[0] in ("127.0.0.1", "::1"):
            # The control panel on this computer has (1) or lost (0) the keyboard.
            if self.on_panel_focus is not None:
                self.on_panel_focus(bool(args) and isinstance(args[0], (int, float)) and float(args[0]) >= 0.5)
        elif name == "quit" and addr[0] in ("127.0.0.1", "::1"):
            # The control panel on this computer; like the key, it needs two
            # messages within 3 s.
            self.command("quit", source)
        else:
            self.log.warn("osc", f"unknown address {self.cfg.osc.prefix}/{name}")

    def _cue_from_arg(self, value: Any) -> Cue | None:
        if value is None:
            return None
        if isinstance(value, str):
            return self.cfg.cue_by_id(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            by_id = self.cfg.cue_by_id(str(int(value)))
            if by_id is not None:
                return by_id
            index = int(value) - 1
            return self.cues[index] if 0 <= index < len(self.cues) else None
        return None

    # ------------------------------------------------------------------ deck events

    def deck_ready(self, deck: Deck, load: Load) -> None:
        if load.load_time is not None and load.cue is not None:
            self._load_lead = max(0.15, min(2.0, 0.7 * self._load_lead + 0.3 * (1.5 * load.load_time + 0.05)))
        if load.cue is not None:
            self._load_failures.pop(load.cue.id, None)
        self.log.debug("deck_ready", f"deck {deck.name}: {load.cue.id if load.cue else self.background_kind} ready in {(load.load_time or 0) * 1000:.0f} ms")
        if deck is self.filler_deck:
            if self._transition is not None and not self._transition.done():
                return  # the running transition shows it
            if self.live is None and self.mode in (Mode.STARTING, *BACKGROUND_MODES) and self.visible != deck.name:
                self.mode = self._mode_for(self.background_kind)
                self._fade_in_background()
            elif not self.picture.busy(deck.name):
                deck.pause()
            return
        if self.pending is not None and self.pending.load is load:
            self._arm(self.pending)

    def deck_seeked(self, deck: Deck) -> None:
        if self.pending is not None and self.pending.deck is deck and self.pending.attempt > 0 and deck.state == DeckState.READY:
            self._arm(self.pending)
        elif self.live is not None and self.live.deck is deck and self.live.corrector is not None:
            self.live.corrector.seek_done(now())

    def deck_failed(self, deck: Deck, load: Load, reason: str) -> None:
        if deck is self.filler_deck:
            kind = self.background_kind
            media, _ = self._background_media(kind)
            if load.media == media and not media.startswith("av://"):
                self.log.error(f"{kind}_failed", f"{kind} file failed ({reason}); using the {kind} colour")
                self._load_background(kind, force_color=True, paused=load.paused)
            else:
                self.log.error(f"{kind}_failed", f"{kind} failed ({reason})")
            return
        cue = load.cue
        cue_id = cue.id if cue else "?"
        live = self.live
        if live is not None and live.deck is deck and live.cue is cue:
            # The playing clip broke off (decoder/file error): the deck shows
            # black now, so bring the filler in from black.
            live.ended = True
            following = self._after(live.cue)
            self.log.error("cue_error", f"cue {cue_id} stopped with an error ({reason}); {following}", cue=cue_id, reason=reason)
            self.to_background(following, "playback error")
            return
        failures = self._load_failures.get(cue_id, 0) + 1
        self._load_failures[cue_id] = failures
        if self.pending is not None and self.pending.load is load:
            self.pending = None
            self.log.error("cue_failed", f"cue {cue_id} cannot play: {reason} - staying on the current picture", cue=cue_id, reason=reason)
        else:
            self.log.error("preload_failed", f"cue {cue_id} failed to load on deck {deck.name}: {reason}", cue=cue_id, reason=reason)
        deck.unload()
        if failures < MAX_LOAD_FAILURES:
            self._spawn_task(self._retry_preload(3.0), f"retry-{cue_id}")

    async def _retry_preload(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._preload_next()

    def deck_eof(self, deck: Deck, load: Load) -> None:
        live = self.live
        if live is not None and live.deck is deck and not live.ended:
            self._cue_ended(live)

    def _resume_lost(self, deck: Deck) -> bool:
        """A cue deck is back after every deck went down (compositor restart):
        rejoin the cue that was lost."""
        lost, self._lost = self._lost, None
        if lost is None or self.live is not None or self.pending is not None or not self.cfg.sync.resume_after_crash:
            return False
        cue, t0, clip_at_t0 = lost
        self._cold_start(deck, cue, t0, clip_at_t0, "resume", resume=True)
        return True

    def deck_down(self, deck: Deck, reason: str) -> None:
        self.log.error("deck_down", f"deck {deck.name}: {reason}", deck=deck.name)
        if self.stopping:
            return
        live = self.live
        if live is not None and live.deck is deck:
            self.live = None
            self._cancel_transition()
            shown = self._cut_to_background()
            self.mode = self._mode_for(self.background_kind)
            expected = self._expected_position(live, now())
            duration = deck.duration
            remaining = (duration - expected) if duration else None
            self.log.error("cue_lost", f"cue {live.cue.id} lost at {expected:.2f} s; {self.background_kind if shown else 'black'} on stage", cue=live.cue.id)
            if self.cfg.sync.resume_after_crash and (remaining is None or remaining > 2.0) and not live.leaving:
                t0 = now() - (expected - live.clip_at_t0)
                other = self._free_cue_deck()
                if other is not None:
                    self._cold_start(other, live.cue, t0, live.clip_at_t0, "resume", resume=True)
                else:
                    self._lost = (live.cue, t0, live.clip_at_t0)
        elif self.pending is not None and self.pending.deck is deck:
            pending = self.pending
            self.pending = None
            other = self._free_cue_deck()
            if other is not None:
                self._cold_start(other, pending.cue, pending.t0, pending.clip_at_t0, pending.source, pending.resume)
            else:
                self._lost = (pending.cue, pending.t0, pending.clip_at_t0)
        if deck is self.filler_deck and self.visible == deck.name:
            self.picture.cut(None)
        self.picture.refresh()
        self._schedule_respawn(deck)
        self._save_state()

    def deck_time(self, deck: Deck, position: float, t: float) -> None:
        live = self.live
        if live is None or live.deck is not deck or live.ended:
            return
        if not live.leaving and live.out_task is None:
            self._schedule_out(live, position, t)
        if live.corrector is None:
            return
        corrector = live.corrector
        mtc = self.mtc.snapshot(t) if self.mtc is not None else None
        action = corrector.update(t, position, mtc)
        if isinstance(action, SetSpeed):
            previous = deck.speed
            deck.set_speed(action.speed)
            # Start and end of a correction are worth a line on the console; the steps in between are not.
            level = "info" if previous == 1.0 or action.speed == 1.0 else "debug"
            self.log.event(
                "sync_speed", level,
                f"drift {action.drift * 1000:+.1f} ms ({corrector.reference}) -> speed {action.speed:.4f}",
                drift_ms=round(action.drift * 1000, 2), speed=action.speed, reference=corrector.reference,
            )
        elif isinstance(action, Seek):
            duration = deck.duration
            if action.position < 0 or (duration is not None and action.position > duration - 0.5):
                self.log.warn("sync_seek", f"drift {action.drift:+.2f} s, but {action.position:.2f} s is outside the clip; correction stopped for this cue")
                live.corrector = None
                deck.set_speed(1.0)
                return
            deck.set_speed(1.0)
            deck.seek(action.position)
            self.log.warn(
                "sync_seek",
                f"drift {action.drift * 1000:+.0f} ms ({corrector.reference}) -> seek to {action.position:.3f} s",
                drift_ms=round(action.drift * 1000, 1), reference=corrector.reference,
            )

    def deck_key(self, deck: Deck, command: str) -> None:
        self.command(command, f"stage key ({deck.name})")

    def stage_key(self, key: str) -> None:
        """A key pressed in the compositor's window."""
        command = self._stage_keys.get(key)
        if command is not None:
            self.command(command, "stage key")

    def compositor_restarted(self) -> None:
        """The compositor was restarted: it shows black until told otherwise.
        (Its decks went down with it and come back on their own.)"""
        self.picture.resync()

    def deck_reconfig(self, deck: Deck) -> None:
        # mpv may have re-stacked its window (HWND_NOTOPMOST) while reconfiguring.
        if not self.stopping:
            asyncio.get_running_loop().call_later(0.05, self._maintain_stage)

    def _maintain_stage(self) -> None:
        try:
            self.stage.maintain()
        except Exception as exc:
            self.log.warn("stage", f"z-order check failed: {exc!r}")

    def _expected_position(self, live: Live, t: float) -> float:
        if live.corrector is not None:
            return live.corrector.expected(t, self.mtc.snapshot(t) if self.mtc is not None else None)
        return live.clip_at_t0 + (t - live.t0)

    # ------------------------------------------------------------------ supervision

    def _schedule_respawn(self, deck: Deck) -> None:
        if deck.name in self._respawning or self.stopping:
            return
        self._respawning.add(deck.name)
        self._spawn_task(self._respawn(deck), f"respawn-{deck.name}")

    async def _respawn(self, deck: Deck) -> None:
        delay = 0.5
        try:
            while not self.stopping:
                await asyncio.sleep(delay)
                deck.restarts += 1
                if await deck.spawn():
                    self.stage.register(deck)
                    deck.set_test_mode(self.test_mode)
                    self.picture.deck_restarted(deck.name)
                    self.picture.refresh()
                    if deck is self.filler_deck:
                        self._load_background(self._wanted_background(self.mode.value))
                    elif not self._resume_lost(deck):
                        self._preload_next()
                    self.log.info("deck_recovered", f"deck {deck.name} is back", deck=deck.name, restarts=deck.restarts)
                    return
                delay = min(delay * 2, 10.0)
        finally:
            self._respawning.discard(deck.name)

    async def _watchdog(self) -> None:
        tick = 0
        while not self.stopping:
            await asyncio.sleep(0.25)
            tick += 1
            t = now()
            self._check_stall(t)
            self._check_mtc_stop(t)
            if tick % 4 == 0:
                for deck in self.decks.values():
                    if deck.up:
                        self._spawn_task(self._ping(deck), f"ping-{deck.name}")
            self._maintain_stage()
            if self.test_mode:
                self._update_test_overlay(t)

    async def _ping(self, deck: Deck) -> None:
        if await deck.ping(1.0):
            return
        if deck.up and deck.ping_failures >= 3:
            if self.live is not None and self.live.deck is deck:
                self._cut_to_background()
            deck.kill("not responding")

    def _check_stall(self, t: float) -> None:
        live = self.live
        if live is None or live.ended or self._transition is not None and not self._transition.done():
            return
        deck = live.deck
        if deck.state != DeckState.PLAYING or deck.paused or not deck.duration:
            return  # (no duration: a still image, whose position never moves)
        last = max(deck.time_pos_t, live.went_live_at)
        if t - last > self.cfg.sync.stall_timeout:
            self.log.error("stall", f"deck {deck.name}: playback stalled for {t - last:.1f} s", deck=deck.name, cue=live.cue.id)
            self._cut_to_background()
            deck.kill("playback stalled")

    def _check_mtc_stop(self, t: float) -> None:
        if self.mtc is None or not self.cfg.sync.mtc.enabled:
            return
        running = self.mtc.snapshot(t).running
        if running != self._mtc_was_running:
            self.log.info("mtc", "MTC running" if running else "MTC stopped or lost")
            if not running and self.cfg.sync.mtc.on_stop == "filler" and self.live is not None and not self.live.ended:
                self.to_background(self._after(self.live.cue), "MTC stopped")
        self._mtc_was_running = running

    # ------------------------------------------------------------------ status

    def _cue_text(self, cue: Cue | None) -> str:
        return cue.label if cue is not None else "(end of list)"

    def status_text(self, t: float | None = None) -> str:
        t = now() if t is None else t
        parts: list[str] = []
        live = self.live
        if self.mode == Mode.CUE and live is not None:
            pos = live.deck.time_pos
            dur = live.deck.duration
            when = f" {format_seconds(pos or 0.0, self.cfg.stage.fps)}" + (f"/{format_seconds(dur, self.cfg.stage.fps)}" if dur else "")
            parts.append(f"CUE {live.cue.id}{when} [{live.deck.name}]")
        elif self.mode in (Mode.HOLD, Mode.BLACK) and live is not None:
            parts.append(f"{self.mode.value.upper()} after {live.cue.id}")
        else:
            parts.append(self.mode.value.upper())
        nxt = self.selected_cue
        if nxt is not None:
            ready = "?"
            for deck in self.cue_decks:
                if deck.load_ is not None and deck.load_.cue is nxt and (self.live is None or self.live.deck is not deck):
                    ready = {"ready": "ready", "loading": "loading", "failed": "FAILED"}.get(deck.state.value, deck.state.value)
            if self._load_failures.get(nxt.id, 0) >= MAX_LOAD_FAILURES:
                ready = "FAILED"
            parts.append(f"next {nxt.id} {ready}")
        else:
            parts.append("next: end")
        parts.append(f"MIDI {self.inputs.midi}")
        if self.cfg.osc.enabled:
            parts.append(f"OSC {self.inputs.osc}")
        if self.mtc is not None and self.cfg.sync.mtc.enabled:
            snap = self.mtc.snapshot(t)
            text = f"MTC {snap.status}"
            if snap.timecode:
                text += f" {snap.timecode}"
            parts.append(text)
        if live is not None and live.corrector is not None and live.corrector.last_drift is not None:
            parts.append(f"drift {live.corrector.last_drift * 1000:+.0f}ms/{live.corrector.reference}")
        down = [d.name for d in self.decks.values() if not d.up]
        if down:
            parts.append(f"DOWN {','.join(down)}")
        if self.blackout:
            parts.append("BLACKOUT")
        return " | ".join(parts)

    def status_dict(self, t: float | None = None) -> dict[str, Any]:
        """Machine-readable status (the GUI reads it from the status file)."""
        t = now() if t is None else t
        live = self.live
        nxt = self.selected_cue
        data: dict[str, Any] = {
            "show": self.cfg.name,
            "mode": self.mode.value,
            "text": self.status_text(t),
            "blackout": self.blackout,
            "test_mode": self.test_mode,
            "visible": self.visible,
            "background": self.background_kind,
            "selected": self.selected,
            "next": nxt.id if nxt else None,
            "next_ready": any(
                d.load_ is not None and d.load_.cue is nxt and d.state == DeckState.READY for d in self.cue_decks
            ) if nxt else False,
            "midi": self.inputs.midi,
            "osc": self.inputs.osc if self.cfg.osc.enabled else None,
            "decks": {d.name: d.state.value for d in self.decks.values()},
            "live": None,
        }
        if live is not None:
            drift = live.corrector.last_drift if live.corrector is not None else None
            data["live"] = {
                "cue": live.cue.id,
                "title": live.cue.title,
                "deck": live.deck.name,
                "position": live.deck.time_pos,
                "duration": live.deck.duration,
                "ended": live.ended,
                "leaving": live.leaving,
                "drift_ms": None if drift is None else round(drift * 1000, 1),
            }
        if self.mtc is not None and self.cfg.sync.mtc.enabled:
            snap = self.mtc.snapshot(t)
            data["mtc"] = {"status": snap.status, "timecode": snap.timecode}
        return data

    async def _status_loop(self) -> None:
        while not self.stopping:
            if self.on_status is not None:
                try:
                    self.on_status(self.status_text())
                except Exception:
                    pass
            await asyncio.sleep(self.cfg.logging.status_interval)

    def _update_test_overlay(self, t: float) -> None:
        lines = []
        if self._last_trigger is not None:
            cue_id, source, tt = self._last_trigger
            lines.append(f"last trigger {cue_id} from {source} at {self.wall.hms(tt)}")
        if self.mtc is not None:
            snap = self.mtc.snapshot(t)
            lines.append(f"MTC {snap.status} {snap.timecode or '--:--:--:--'}")
        live = self.live
        if live is not None and live.corrector is not None:
            drift = live.corrector.last_drift
            lines.append(f"drift {'n/a' if drift is None else f'{drift * 1000:+.1f} ms'} ({live.corrector.reference})  speed {live.deck.speed:.4f}")
        text = "\n".join(lines) if lines else None
        if self.visible is not None and self.visible in self.decks:
            self.decks[self.visible].set_info(text)

    def _save_state(self) -> None:
        if self.state_store is None:
            return
        live = self.live
        data: dict[str, Any] = {
            "show": self.cfg.name,
            "saved_mono": now(),
            "selected": self.selected,
            "mode": self.mode.value,
            "blackout": self.blackout,
            "live": None,
        }
        if live is not None:
            data["live"] = {
                "cue": live.cue.id,
                "deck": live.deck.name,
                "t0_wall": self.wall.wall(live.t0),
                "t0_mono": live.t0,
                "clip_at_t0": live.clip_at_t0,
                "ended": live.ended,
            }
        self.state_store.save(data)

    # ------------------------------------------------------------------ shutdown

    async def shutdown(self, quit_decks: bool = True) -> None:
        self.stopping = True
        self._cancel_transition()
        if self.pending is not None and self.pending.task is not None:
            self.pending.task.cancel()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if quit_decks:
            live = self.live
            if live is not None and live.deck.up and live.deck.ipc is not None:
                futures = [live.deck.ipc.request(["get_property", p], 1.0) for p in STAT_PROPS]
                await self._log_cue_stats(live, futures)
            self.live = None
            self._save_state()
            await asyncio.gather(*(deck.quit() for deck in self.decks.values()), return_exceptions=True)
            try:
                await self.stage.quit()
            except Exception as exc:
                self.log.warn("stage", f"stopping the stage failed: {exc!r}")
        else:
            await self.picture.detach()
            await asyncio.gather(*(deck.detach() for deck in self.decks.values()), return_exceptions=True)
        try:
            self.stage.close()
        except Exception:
            pass
