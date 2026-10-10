"""``aethra rehearse``: play the whole set through a running Aethra, without REAPER.

Sends ``/aethra/cue <id>`` over OSC for every cue in order, waits for the clip
to end plus a gap, and repeats the set as many times as asked. It is the
unattended version of "the full set twice in a row" (docs/testing.md, test 1):
start the show in one console, the rehearsal in another, and read
``aethra report`` afterwards.
"""

from __future__ import annotations

import socket
import time

from aethra.config import Config
from aethra.media import verify_file
from aethra.osc import build_message


def clip_durations(mpv: str, cfg: Config) -> dict[str, float]:
    durations: dict[str, float] = {}
    for cue in cfg.cues:
        report = verify_file(mpv, cue.file, quick=True)
        if report.duration:
            durations[cue.id] = report.duration
    return durations


def rehearse(cfg: Config, mpv: str, loops: int, gap: float, host: str, max_clip: float | None) -> int:
    if not cfg.osc.enabled:
        print("rehearse needs osc.enabled: true in the show file (it triggers the cues over OSC)")
        return 2
    print("measuring clip lengths ...", flush=True)
    durations = clip_durations(mpv, cfg)
    missing = [c.id for c in cfg.cues if c.id not in durations]
    if missing:
        print(f"skipping unplayable cues: {', '.join(missing)}")
    playable = [c for c in cfg.cues if c.id in durations]
    total = sum(min(durations[c.id], max_clip or 1e9) + gap for c in playable) * loops
    print(f"{len(playable)} cues x {loops} = about {total / 60:.1f} min. Ctrl+C stops the rehearsal (not the show).")
    address = (host, cfg.osc.port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            for loop in range(1, loops + 1):
                for cue in playable:
                    length = min(durations[cue.id], max_clip or 1e9)
                    sock.sendto(build_message(f"{cfg.osc.prefix}/cue", cue.id), address)
                    print(f"{time.strftime('%H:%M:%S')}  pass {loop}/{loops}  cue {cue.id:<10} {length:7.1f} s", flush=True)
                    time.sleep(length + gap)
        except KeyboardInterrupt:
            print("rehearsal stopped")
            return 1
    print("done - now run: aethra report")
    return 0
