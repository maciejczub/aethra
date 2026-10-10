#!/usr/bin/env python3
"""Make the logo files from the designer's master, gfx/aethra-logo.pdf.

The PDF has four pages: square and horizontal logo, each on dark (navy) and on
light (white) background. This script writes, into gfx/:

* aethra-logo-{square,horizontal}-{dark,light}.svg   as in the PDF, with background
* aethra-logo-horizontal-on-{dark,light}.svg         transparent, cropped (README, panel)
* aethra-logo-square-on-{dark,light}.svg             transparent, cropped
* aethra-mark.svg                                     the sign alone, transparent
* aethra-icon.svg, aethra-icon-<n>.png, aethra.ico   the sign on a navy tile (app icon)
* aethra-logo-horizontal-{dark,light}.png            for places without SVG

and copies what the control panel uses into src/aethra/gui/static/.

Needs pdftocairo (poppler-utils) and rsvg-convert (librsvg2-bin); the release
build container (docker/build.compose.yaml) has both:

    docker compose -f docker/build.compose.yaml run --rm build python tools/make_logo_assets.py
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GFX = ROOT / "gfx"
STATIC = ROOT / "src" / "aethra" / "gui" / "static"
PAGES = {1: ("square", "dark"), 2: ("horizontal", "dark"), 3: ("square", "light"), 4: ("horizontal", "light")}
NAVY = "#06142f"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

PATH_RE = re.compile(r'<path[^>]*?fill="rgb\(([\d.]+)%, ([\d.]+)%, ([\d.]+)%\)"[^>]*? d="([^"]*)"')


def hex_color(r: str, g: str, b: str) -> str:
    return "#" + "".join(f"{round(float(v) * 2.55):02x}" for v in (r, g, b))


def bbox(paths: list[tuple[str, str]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for _color, d in paths:
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)]
        xs += numbers[0::2]
        ys += numbers[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def svg(paths: list[tuple[str, str]], view: tuple[float, float, float, float], background: str | None = None, rx: float = 0) -> str:
    x, y, w, h = view
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:.2f} {y:.2f} {w:.2f} {h:.2f}" width="{w:.0f}" height="{h:.0f}">',
        "<title>Aethra</title>",
    ]
    if background:
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rx:.2f}" fill="{background}"/>')
    out += [f'<path fill="{color}" d="{d}"/>' for color, d in paths]
    out.append("</svg>")
    return "\n".join(out) + "\n"


def padded(box: tuple[float, float, float, float], pad: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad


def render(svg_path: Path, png: Path, width: int, height: int | None = None) -> None:
    args = ["rsvg-convert", "-w", str(width)] + (["-h", str(height)] if height else []) + ["-o", str(png), str(svg_path)]
    subprocess.run(args, check=True)


def write_ico(pngs: list[Path], target: Path) -> None:
    """A Windows icon with PNG images inside (Vista and later)."""
    blobs = [p.read_bytes() for p in pngs]
    sizes = [struct.unpack(">II", b[16:24]) for b in blobs]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for (w, h), blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    target.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    for tool in ("pdftocairo", "rsvg-convert"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is needed (see the docstring)")
    master = GFX / "aethra-logo.pdf"
    with tempfile.TemporaryDirectory() as tmp:
        pages: dict[tuple[str, str], tuple[list[tuple[str, str]], tuple[float, float]]] = {}
        for page, key in PAGES.items():
            raw_path = Path(tmp) / f"p{page}.svg"
            subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page), str(master), str(raw_path)], check=True)
            raw = raw_path.read_text(encoding="utf-8")
            w, h = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', raw).groups())  # type: ignore[union-attr]
            paths = [(hex_color(r, g, b), d) for r, g, b, d in PATH_RE.findall(raw)]
            pages[key] = (paths, (w, h))

    written = []

    def put(name: str, text: str) -> Path:
        path = GFX / name
        path.write_text(text, encoding="utf-8")
        written.append(name)
        return path

    for (shape, theme), (paths, (w, h)) in pages.items():
        if theme == "dark":
            background, art = paths[0][0], paths[1:]  # the first path is the navy page
        else:
            background, art = "#ffffff", paths  # the light pages are plain white paper
        put(f"aethra-logo-{shape}-{theme}.svg", svg(art, (0, 0, w, h), background))
        box = bbox(art)
        pad = 0.06 * (box[3] - box[1])
        put(f"aethra-logo-{shape}-on-{theme}.svg", svg(art, padded(box, pad)))

    # The sign alone: the three bars (purple, purple, green) of the square logo.
    sign = pages[("square", "dark")][0][1:4]
    sx0, sy0, sx1, sy1 = bbox(sign)
    put("aethra-mark.svg", svg(sign, padded((sx0, sy0, sx1, sy1), 0)))
    # App icon: the sign centred on a navy tile with rounded corners.
    side = max(sx1 - sx0, sy1 - sy0) * 1.36
    cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    icon = put("aethra-icon.svg", svg(sign, (cx - side / 2, cy - side / 2, side, side), NAVY, rx=side * 0.18))

    pngs = []
    for n in ICON_SIZES + (512,):
        png = GFX / f"aethra-icon-{n}.png"
        render(icon, png, n, n)
        if n in ICON_SIZES:
            pngs.append(png)
    write_ico(pngs, GFX / "aethra.ico")
    for p in pngs:
        if p.name != "aethra-icon-256.png":
            p.unlink()  # inside the .ico; keep 256 and 512 as plain images
    for theme in ("dark", "light"):
        render(GFX / f"aethra-logo-horizontal-{theme}.svg", GFX / f"aethra-logo-horizontal-{theme}.png", 1200)

    STATIC.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GFX / "aethra-logo-horizontal-on-dark.svg", STATIC / "logo.svg")
    shutil.copy2(GFX / "aethra-icon.svg", STATIC / "icon.svg")
    print("written:", ", ".join(written + ["aethra.ico", "aethra-icon-256.png", "aethra-icon-512.png", "aethra-logo-horizontal-*.png"]))
    print("panel:   static/logo.svg, static/icon.svg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
