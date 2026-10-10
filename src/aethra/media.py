"""Pre-show media checks: does every file open and decode cleanly?"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from aethra.platform import IS_WINDOWS

INFO_TAG = "AETHRA_INFO"
_INFO_FORMAT = f"{INFO_TAG}|${{=duration:}}|${{container-fps:}}|${{video-format:}}|${{width:}}|${{height:}}"
_ERROR_RE = re.compile(r"error|corrupt|invalid|missing|failed|concealing", re.IGNORECASE)


@dataclass
class MediaReport:
    path: Path
    ok: bool
    duration: float | None = None
    fps: float | None = None
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    decode_seconds: float = 0.0
    problems: list[str] = field(default_factory=list)

    def summary(self, expected_fps: float | None = None, expected_size: tuple[int, int] | None = None) -> str:
        parts = []
        if self.duration is not None:
            parts.append(f"{self.duration:8.2f} s")
        if self.width and self.height:
            parts.append(f"{self.width}x{self.height}")
        if self.fps:
            parts.append(f"{self.fps:g} fps")
        if self.codec:
            parts.append(self.codec)
        return "  ".join(parts)

    def warnings(self, expected_fps: float, expected_size: tuple[int, int]) -> list[str]:
        out = []
        if self.fps and abs(self.fps - expected_fps) > 0.01:
            out.append(f"frame rate {self.fps:g} differs from the show's {expected_fps:g} fps")
        if self.width and self.height and (self.width, self.height) != expected_size:
            out.append(f"size {self.width}x{self.height} differs from the stage's {expected_size[0]}x{expected_size[1]}")
        return out


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def verify_file(mpv: str, path: Path, quick: bool = False, timeout: float = 3600.0) -> MediaReport:
    """Decode a file with mpv (no output) and collect errors.

    ``quick`` decodes only the first and last seconds; the full check decodes
    every frame as fast as the CPU allows.
    """
    report = MediaReport(path, ok=False)
    if not path.is_file():
        report.problems.append("file not found")
        return report
    base = [
        mpv, "--no-config", "--vo=null", "--ao=null", "--untimed", "--hwdec=no",
        "--sub=no", "--audio=no", "--term-status-msg=",
        "--msg-level=all=warn,cplayer=info,term-msg=info", "--term-playing-msg=" + _INFO_FORMAT,
    ]
    runs = [base + ["--length=2", str(path)], base + ["--start=-3", str(path)]] if quick else [base + [str(path)]]
    kwargs: dict = {"capture_output": True, "text": True, "errors": "replace", "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    started = time.perf_counter()
    for args in runs:
        try:
            result = subprocess.run(args, **kwargs)
        except subprocess.TimeoutExpired:
            report.problems.append("decoding timed out")
            return report
        except OSError as exc:
            report.problems.append(f"cannot run mpv: {exc}")
            return report
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            line = line.strip()
            if line.startswith(INFO_TAG):
                fields = line.split("|")
                if len(fields) >= 6 and report.duration is None:
                    report.duration = _float(fields[1])
                    report.fps = _float(fields[2])
                    report.codec = fields[3] or None
                    report.width = _int(fields[4])
                    report.height = _int(fields[5])
            elif line.startswith("[") and _ERROR_RE.search(line) and line not in report.problems:
                report.problems.append(line)
        if result.returncode != 0:
            report.problems.append(f"mpv exit code {result.returncode}")
    report.decode_seconds = time.perf_counter() - started
    if report.duration is None:
        report.problems.append("no video stream found")
    report.ok = not report.problems
    return report
