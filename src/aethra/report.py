"""Post-show report from an event log (``aethra report logs/aethra-....jsonl``)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROBLEM_EVENTS = (
    "cue_failed", "preload_failed", "start_late", "start_skipped", "deck_down", "deck_kill", "stall",
    "cue_lost", "midi_lost", "midi_missing", "sync_seek", "filler_failed", "no_deck", "internal_error",
    "display_change", "mpv_missing", "stage_monitor",
)


@dataclass
class CueRun:
    wall: str
    cue: str
    deck: str
    source: str
    trigger_to_play_ms: float | None
    late_ms: float | None
    resume: bool
    stats: dict[str, Any] = field(default_factory=dict)
    ended: bool = False


def read_log(path: Path) -> list[dict[str, Any]]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # a torn last line after a crash
    return records


def build_report(records: list[dict[str, Any]]) -> str:
    if not records:
        return "empty log"
    out: list[str] = []
    starts = [r for r in records if r.get("event") == "start"]
    first, last = records[0], records[-1]
    duration = float(last.get("t", 0)) - float(first.get("t", 0))
    out.append(f"Session {first.get('wall', '?')[:19]} - {last.get('wall', '?')[11:19]} ({duration / 60:.1f} min)")
    for s in starts:
        out.append(f"  {s.get('msg', '')}")
    if len(starts) > 1:
        out.append(f"  ! the controller started {len(starts)} times (restarts after crashes?)")

    runs: list[CueRun] = []
    open_runs: dict[str, CueRun] = {}
    triggers = 0
    ignored = 0
    speed_changes = 0
    for r in records:
        ev = r.get("event")
        if ev == "trigger":
            triggers += 1
        elif ev == "trigger_ignored":
            ignored += 1
        elif ev == "sync_speed":
            speed_changes += 1
        elif ev == "cue_live":
            run = CueRun(
                wall=str(r.get("wall", ""))[11:23],
                cue=str(r.get("cue")),
                deck=str(r.get("deck")),
                source=str(r.get("source")),
                trigger_to_play_ms=r.get("trigger_to_play_ms"),
                late_ms=r.get("late_ms"),
                resume=bool(r.get("resume")),
            )
            runs.append(run)
            open_runs[run.cue] = run
        elif ev == "cue_end" and r.get("cue") in open_runs:
            open_runs[r["cue"]].ended = True
        elif ev == "cue_stats" and r.get("cue") in open_runs:
            open_runs.pop(r["cue"]).stats = {k: v for k, v in r.items() if k not in ("t", "wall", "event", "level", "msg", "cue")}

    out.append("")
    out.append(f"Cues played: {len(runs)}   triggers: {triggers}   ignored (duplicates): {ignored}   speed corrections: {speed_changes}")
    out.append(f"  {'time':<12} {'cue':<8} {'deck':<4} {'source':<22} {'t->play':>8} {'late':>6} {'played':>7} {'drop':>5} {'late fr':>7} {'max drift':>9}")
    for run in runs:
        st = run.stats
        drops = (st.get("frame_drop_count") or 0) + (st.get("decoder_frame_drop_count") or 0)
        delayed = st.get("vo_delayed_frame_count")
        played = st.get("played_s")
        drift = st.get("max_drift_ms")
        flag = "  RESUMED" if run.resume else ""
        out.append(
            f"  {run.wall:<12} {run.cue:<8} {run.deck:<4} {run.source[:22]:<22} "
            f"{_ms(run.trigger_to_play_ms):>8} {_ms(run.late_ms):>6} {(f'{played:.0f}s' if played else '-'):>7} "
            f"{drops if st else '-':>5} {delayed if delayed is not None else '-':>7} {(f'{drift:.0f}ms' if drift is not None else '-'):>9}{flag}"
        )

    problems = [r for r in records if r.get("event") in PROBLEM_EVENTS or r.get("level") == "error"]
    out.append("")
    if problems:
        counts = Counter(r.get("event") for r in problems)
        out.append("Problems: " + ", ".join(f"{k} x{v}" for k, v in counts.most_common()))
        for r in problems[:60]:
            out.append(f"  {str(r.get('wall', ''))[11:23]} {r.get('event'):<15} {r.get('msg', '')}")
        if len(problems) > 60:
            out.append(f"  ... {len(problems) - 60} more")
    else:
        out.append("Problems: none")
    return "\n".join(out)


def _ms(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1f}"
