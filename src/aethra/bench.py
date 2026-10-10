"""Codec benchmark for the show laptop (``aethra bench``).

Plays each file full screen on the stage output exactly the way a deck does,
then reports dropped/late frames, the decoder actually used and CPU load. Run
it on the target laptop with every codec candidate; the right codec is the one
with zero drops and the lowest CPU.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

from aethra.clock import now
from aethra.config import Config
from aethra.ipc import MpvError, MpvIpc
from aethra.mpvproc import MpvProcess, build_deck_args, supported_options
from aethra.platform import IS_WINDOWS, ipc_address, process_cpu_seconds

COUNTERS = ("frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count")


@dataclass
class BenchResult:
    path: Path
    hwdec: str
    seconds: float = 0.0
    hwdec_current: str | None = None
    codec: str | None = None
    size: str | None = None
    fps: float | None = None
    display_fps: float | None = None
    cpu_percent: float | None = None
    counters: dict[str, int | None] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @property
    def dropped(self) -> int:
        return sum(v or 0 for v in self.counters.values())


async def bench_file(mpv: str, cfg: Config, path: Path, hwdec: str, seconds: float, vo: str | None = None) -> BenchResult:
    result = BenchResult(path, hwdec)
    cfg = dataclasses.replace(cfg, mpv=dataclasses.replace(cfg.mpv, hwdec=hwdec))
    address = ipc_address("bench", "X")
    if not IS_WINDOWS:
        Path(address).unlink(missing_ok=True)
    args = build_deck_args(mpv, cfg, "bench", address, None, supported_options(mpv), vo).args
    proc = MpvProcess.spawn(args, cfg.mpv.priority)
    ipc = MpvIpc(address, name="bench")
    try:
        await ipc.connect(timeout=cfg.mpv.startup_timeout)
        restarted = asyncio.Event()
        ipc.on_event = lambda msg: restarted.set() if msg.get("event") == "playback-restart" else None
        await ipc.command_named("loadfile", url=str(path), flags="replace", options={"loop-file": "inf"})
        try:
            await asyncio.wait_for(restarted.wait(), 20.0)
        except asyncio.TimeoutError:
            result.error = "file did not start"
            return result
        await asyncio.sleep(1.0)  # let the decoder settle before measuring
        base = {name: await ipc.get_or(name) for name in COUNTERS}
        cpu0 = process_cpu_seconds(proc.pid) if proc.pid else None
        t0 = now()
        await asyncio.sleep(seconds)
        t1 = now()
        cpu1 = process_cpu_seconds(proc.pid) if proc.pid else None
        for name in COUNTERS:
            value = await ipc.get_or(name)
            start = base.get(name)
            result.counters[name] = (value - start) if isinstance(value, int) and isinstance(start, int) else value
        result.seconds = t1 - t0
        result.hwdec_current = await ipc.get_or("hwdec-current")
        result.codec = await ipc.get_or("video-codec")
        w, h = await ipc.get_or("width"), await ipc.get_or("height")
        result.size = f"{w}x{h}" if w and h else None
        result.fps = await ipc.get_or("container-fps")
        result.display_fps = await ipc.get_or("display-fps")
        if cpu0 is not None and cpu1 is not None and result.seconds > 0:
            result.cpu_percent = 100.0 * (cpu1 - cpu0) / result.seconds / (os.cpu_count() or 1)
    except MpvError as exc:
        result.error = str(exc)
    finally:
        try:
            await ipc.command("quit", timeout=1.0)
        except MpvError:
            pass
        await ipc.close()
        await asyncio.sleep(0.3)
        if proc.alive():
            proc.kill()
        proc.reap(1.0)
    return result


def format_results(results: list[BenchResult]) -> str:
    lines = [
        f"{'file':<34} {'hwdec':<10} {'in use':<12} {'codec':<10} {'size':<10} {'CPU %':>6} {'drop':>5} {'late':>5}  verdict",
    ]
    for r in results:
        if r.error:
            lines.append(f"{r.path.name[:34]:<34} {r.hwdec:<10} ERROR: {r.error}")
            continue
        c = r.counters
        drop = (c.get("frame-drop-count") or 0) + (c.get("decoder-frame-drop-count") or 0)
        late = c.get("vo-delayed-frame-count") or 0
        cpu = f"{r.cpu_percent:6.1f}" if r.cpu_percent is not None else "   n/a"
        verdict = "OK" if drop == 0 and late == 0 else "DROPS"
        lines.append(
            f"{r.path.name[:34]:<34} {r.hwdec:<10} {str(r.hwdec_current)[:12]:<12} {str(r.codec)[:10]:<10} "
            f"{str(r.size):<10} {cpu} {drop:>5} {late:>5}  {verdict}"
        )
    if results and results[0].display_fps:
        lines.append(f"display refresh rate reported by mpv: {results[0].display_fps:g} Hz")
    return "\n".join(lines)
