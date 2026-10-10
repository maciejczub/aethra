#!/usr/bin/env python3
"""Render the whole show as ONE video on the REAPER timeline, for the xjadeo fallback.

xjadeo shows the frame of a single file that matches the incoming MTC. This
tool places every cue of a show file where REAPER triggers it and fills the
gaps with the show's filler (or black), so that xjadeo following REAPER's MTC
shows what Aethra would have shown. See docs/xjadeo-fallback.md.

    python tools/make_xjadeo_timeline.py show.yaml --out show-timeline.avi
    python tools/make_xjadeo_timeline.py show.yaml --out t.avi --start 01:00:00:00 --black-gaps

Every cue needs ``mtc_start`` (the REAPER timecode of its trigger). Placement
mirrors Aethra: clip position ``offset`` sits at ``mtc_start``; a clip runs to
its end or until the next cue starts. The video's first frame is the timecode
given by ``--start`` (default 00:00:00:00), so give xjadeo the matching offset.
Output: MJPEG (every frame a keyframe, so xjadeo can jump anywhere instantly).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aethra.config import ConfigError, load_config, parse_timecode  # noqa: E402
from aethra.mtc import format_seconds  # noqa: E402


@dataclass
class Segment:
    start: int  # timeline frame (relative to --start)
    frames: int
    source: str  # "clip" | "gap"
    path: Path | None = None
    clip_in: int = 0  # first clip frame
    label: str = ""


def probe_frames(ffprobe: str, path: Path, fps: float) -> int:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(float(out) * fps)


def plan(cfg, ffprobe: str, start_tc: float, tail: float = 0.0) -> list[Segment]:
    fps = cfg.stage.fps
    missing = [c.id for c in cfg.cues if c.mtc_start is None]
    if missing:
        raise SystemExit(f"these cues have no mtc_start (needed to place them on the timeline): {', '.join(missing)}")
    cues = sorted(cfg.cues, key=lambda c: c.mtc_start)
    placed: list[Segment] = []
    for cue in cues:
        if not cue.file.is_file():
            raise SystemExit(f"cue {cue.id}: file not found: {cue.file}")
        total = probe_frames(ffprobe, cue.file, fps)
        trigger = round((cue.mtc_start - start_tc) * fps)
        offset = round(cue.offset * fps)
        first_visible = trigger + max(0, -offset)  # negative offset: the clip starts after the trigger
        clip_in = max(0, offset)
        frames = total - clip_in
        if trigger < 0:
            raise SystemExit(f"cue {cue.id} starts before --start")
        placed.append(Segment(first_visible, frames, "clip", cue.file, clip_in, cue.id))
    # A clip ends where the next cue's trigger is (like Aethra cutting to the next cue).
    for current, nxt in zip(placed, placed[1:]):
        current.frames = max(0, min(current.frames, nxt.start - current.start))
    timeline: list[Segment] = []
    position = 0
    for seg in placed:
        if seg.start > position:
            timeline.append(Segment(position, seg.start - position, "gap", label="filler"))
        if seg.frames > 0:
            timeline.append(seg)
        position = seg.start + seg.frames
    if tail > 0:
        timeline.append(Segment(position, round(tail * fps), "gap", label="filler"))
    return timeline


def build_command(cfg, timeline: list[Segment], ffmpeg: str, out: Path, black_gaps: bool, quality: int) -> list[str]:
    fps = cfg.stage.fps
    w, h = cfg.stage.width, cfg.stage.height
    filler = cfg.filler
    use_filler_file = not black_gaps and filler.mode in ("loop", "image") and filler.file is not None and filler.file.is_file()
    color = "black" if black_gaps else "0x" + filler.color.lstrip("#")
    args = [ffmpeg, "-hide_banner", "-v", "error", "-stats", "-y"]
    filters = []
    labels = []
    for i, seg in enumerate(timeline):
        norm = f"fps={fps:g},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuvj420p"
        if seg.source == "clip":
            args += ["-i", str(seg.path)]
            filters.append(f"[{i}:v]{norm},trim=start_frame={seg.clip_in}:end_frame={seg.clip_in + seg.frames},setpts=PTS-STARTPTS[v{i}]")
        elif use_filler_file:
            if filler.mode == "loop":
                args += ["-stream_loop", "-1", "-i", str(filler.file)]
            else:
                args += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(filler.file)]
            filters.append(f"[{i}:v]{norm},trim=end_frame={seg.frames},setpts=PTS-STARTPTS[v{i}]")
        else:
            args += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps:g}:d={seg.frames / fps + 1:.3f}"]
            filters.append(f"[{i}:v]{norm},trim=end_frame={seg.frames},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
    filters.append(f"{''.join(labels)}concat=n={len(timeline)}:v=1:a=0[out]")
    args += [
        "-filter_complex", ";".join(filters), "-map", "[out]",
        "-r", f"{fps:g}", "-c:v", "mjpeg", "-q:v", str(quality), "-pix_fmt", "yuvj420p", "-an", str(out),
    ]
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config")
    parser.add_argument("--out", default="show-timeline.avi")
    parser.add_argument("--start", default="00:00:00:00", help="timecode of the video's first frame")
    parser.add_argument("--black-gaps", action="store_true", help="black between cues instead of the filler")
    parser.add_argument("--tail", type=float, default=30.0, help="seconds of filler after the last cue")
    parser.add_argument("--quality", type=int, default=3, help="MJPEG quality, 2 (best) to 31")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe") if args.ffmpeg is None else str(Path(args.ffmpeg).with_name(Path(args.ffmpeg).name.replace("ffmpeg", "ffprobe")))
    if not ffmpeg or not ffprobe:
        print("ffmpeg/ffprobe not found", file=sys.stderr)
        return 2
    fps = cfg.stage.fps
    start_tc = parse_timecode(args.start, fps)
    timeline = plan(cfg, ffprobe, start_tc, args.tail)
    print(f"timeline starts at {args.start} ({len(timeline)} segments):")
    for seg in timeline:
        tc = format_seconds(start_tc + seg.start / fps, fps)
        what = f"cue {seg.label} from clip frame {seg.clip_in}" if seg.source == "clip" else "filler/black"
        print(f"  {tc}  {seg.frames / fps:8.2f} s  {what}")
    end = timeline[-1].start + timeline[-1].frames if timeline else 0
    print(f"ends at {format_seconds(start_tc + end / fps, fps)}")
    if args.dry_run:
        return 0
    subprocess.run(build_command(cfg, timeline, ffmpeg, Path(args.out), args.black_gaps, args.quality), check=True)
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
