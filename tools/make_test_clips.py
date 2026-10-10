#!/usr/bin/env python3
"""Generate test clips with ffmpeg.

    python tools/make_test_clips.py codecs --out test-clips --seconds 60
        One test pattern encoded with every codec candidate (H.264, H.264
        all-intra, HEVC, HAP, HAP Q, MJPEG) for "aethra bench" on the laptop.

    python tools/make_test_clips.py sync --out test-clips --seconds 120
        sync-flash.mp4 (one white frame at every full second, big timecode and
        frame number) and sync-click.wav (a 10 ms beep at every full second).
        Put the WAV in REAPER at the trigger note, film screen and speakers
        together and compare: see docs/testing.md.

    python tools/make_test_clips.py demo --out media
        Clips for examples/show.example.yaml, to try the show without real media.

Every pattern shows timecode, frame number and a bar that moves a fixed step
per frame: a dropped or repeated frame is visible as a jump or a stall of the
bar, and the number tells which frame is on screen when filmed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

CODECS: dict[str, tuple[str, list[str]]] = {
    "h264": (".mp4", ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-profile:v", "high", "-g", "50", "-bf", "2", "-movflags", "+faststart"]),
    "h264-intra": (".mp4", ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-g", "1", "-bf", "0", "-movflags", "+faststart"]),
    "hevc": (".mp4", ["-c:v", "libx265", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-x265-params", "keyint=50:log-level=error", "-movflags", "+faststart"]),
    "hap": (".mov", ["-c:v", "hap", "-format", "hap"]),
    "hapq": (".mov", ["-c:v", "hap", "-format", "hap_q"]),
    "mjpeg": (".avi", ["-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj420p"]),
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


def find_font(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None  # let ffmpeg/fontconfig pick one


def font_opt(font: str | None) -> str:
    if not font:
        return ""
    escaped = font.replace("\\", "/").replace(":", "\\:")
    return f"fontfile='{escaped}':"


def drawtext_escape(text: str) -> str:
    """Escape text for a drawtext option inside a filtergraph."""
    for ch in ("\\", "'", ":", ",", ";", "[", "]", "%"):
        text = text.replace(ch, "\\" + ch)
    return text


def pattern(width: int, height: int, fps: int, seconds: float, label: str, font: str | None,
            background: str = "0x101418", flash: str = "corner") -> str:
    f = font_opt(font)
    bar_w = max(8, width // 40)
    step = max(1, (width - bar_w) // (fps * 2))  # crosses the screen in ~2 s
    flash_box = (
        "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill"
        if flash == "full"
        else f"drawbox=x=iw-{height // 4}-40:y=40:w={height // 4}:h={height // 4}:color=white:t=fill"
    )
    return ";".join([
        f"color=c={background}:s={width}x{height}:r={fps}:d={seconds}[bg]",
        f"color=c=white:s={bar_w}x{height // 10}:r={fps}[bar]",
        f"[bg][bar]overlay=x='mod(n*{step}\\,W-w)':y=H-h-{height // 18}:shortest=1[b1]",
        f"[b1]{flash_box}:enable='lt(mod(n\\,{fps})\\,1)'[b2]",
        f"[b2]drawtext={f}timecode='00\\:00\\:00\\:00':rate={fps}:fontsize={height // 7}:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-tw)/2:y=h/2-th-20[b3]",
        f"[b3]drawtext={f}text='frame %{{frame_num}}':fontsize={height // 12}:fontcolor=0xFFD040:"
        f"borderw=3:bordercolor=black:x=(w-tw)/2:y=h/2+30[b4]",
        f"[b4]drawtext={f}text='{drawtext_escape(label)}':fontsize={height // 22}:fontcolor=0xC0C0C0:x={width // 32}:y={height // 18}",
    ])


def run_ffmpeg(ffmpeg: str, args: list[str]) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("  ffmpeg failed: " + (result.stderr.strip().splitlines() or ["?"])[-1])
        return False
    return True


def cmd_codecs(a: argparse.Namespace, ffmpeg: str, font: str | None) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in a.codec or list(CODECS):
        ext, enc = CODECS[name]
        target = out / f"test-{name}-{a.height}p{a.fps}{ext}"
        print(f"{target.name} ...", flush=True)
        graph = pattern(a.width, a.height, a.fps, a.seconds, f"{name}  {a.width}x{a.height} {a.fps} fps", font)
        if run_ffmpeg(ffmpeg, ["-f", "lavfi", "-i", graph, "-r", str(a.fps), *enc, str(target)]):
            print(f"  {target.stat().st_size / 1e6:.1f} MB")


def cmd_sync(a: argparse.Namespace, ffmpeg: str, font: str | None) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    video = out / f"sync-flash-{a.fps}.mp4"
    audio = out / "sync-click.wav"
    print(f"{video.name} ...", flush=True)
    graph = pattern(a.width, a.height, a.fps, a.seconds, "SYNC: white frame on every full second", font, flash="full")
    run_ffmpeg(ffmpeg, ["-f", "lavfi", "-i", graph, "-r", str(a.fps), *CODECS["h264"][1], str(video)])
    print(f"{audio.name} ...", flush=True)
    beep = "if(lt(mod(t\\,1)\\,0.010)\\,0.8*sin(2*PI*1000*t)\\,0)"
    run_ffmpeg(ffmpeg, ["-f", "lavfi", "-i", f"aevalsrc={beep}:s=48000:d={a.seconds}", "-c:a", "pcm_s16le", str(audio)])
    print("Place sync-click.wav in REAPER exactly at the trigger note of a cue that plays sync-flash; see docs/testing.md.")


def cmd_demo(a: argparse.Namespace, ffmpeg: str, font: str | None) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    clips = [
        ("01-opening.mp4", "01 Opening", "0x402020", 30),
        ("02-second.mp4", "02 Second song", "0x204020", 30),
        ("03-ballad.mp4", "03 Ballad", "0x202048", 30),
        ("filler-loop.mp4", "FILLER LOOP", "0x181818", 10),
    ]
    for name, label, color, seconds in clips:
        target = out / name
        print(f"{target.name} ...", flush=True)
        graph = pattern(a.width, a.height, a.fps, seconds, label, font, background=color)
        run_ffmpeg(ffmpeg, ["-f", "lavfi", "-i", graph, "-r", str(a.fps), *CODECS["h264"][1], str(target)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("codecs", "sync", "demo"))
    parser.add_argument("--out", default="test-clips")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--codec", action="append", choices=sorted(CODECS), help="only these codecs (repeatable)")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--font", default=None, help="TTF font for the overlays")
    a = parser.parse_args()
    ffmpeg = a.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found (use --ffmpeg)", file=sys.stderr)
        return 2
    font = find_font(a.font)
    {"codecs": cmd_codecs, "sync": cmd_sync, "demo": cmd_demo}[a.what](a, ffmpeg, font)
    return 0


if __name__ == "__main__":
    sys.exit(main())
