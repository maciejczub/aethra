#!/usr/bin/env python3
"""Build a self-contained Aethra folder for the Windows show laptop.

The result needs no installation and no internet on the laptop: copy the
folder (or unzip the archive), put the clips into ``media``, edit
``show.yaml`` and start ``Aethra.cmd``.

    python tools/build_windows_bundle.py --mpv-dir C:/tools/mpv --libmpv C:/tools/mpv-dev/libmpv-2.dll
    python tools/build_windows_bundle.py --mpv-archive mpv-x86_64-20260101-git-abc.7z \
                                         --libmpv mpv-dev-x86_64-20260101-git-abc.7z
    python tools/build_windows_bundle.py            # without mpv (add it later to mpv/)
    python tools/build_windows_bundle.py --download-mpv --installer   # the release: Aethra-<version>-setup.exe

--download-mpv takes the pinned mpv build (mpv.exe and libmpv-2.dll of the
same date, checked against SHA-256 sums). --installer also makes the Windows
installer with NSIS (makensis; docker/build.compose.yaml provides it).

The compositor engine (the default) needs libmpv-2.dll next to mpv.exe: take
it from the "mpv-dev" archive of the same mpv build. Without it the show runs
with the window engine (no cross-fades).

Layout of dist/Aethra:

    Aethra.cmd            start the show (restarts it if it ever crashes)
    show.yaml             the show (created from the example if missing)
    python/               Windows embeddable Python + PyYAML + glfw
    src/aethra/           the application
    mpv/                  mpv.exe and its files, libmpv-2.dll
    media/  logs/         clips and logs
    scripts/              shortcuts/autostart, laptop preparation
    docs/

Downloads (once, at build time) are verified against pinned SHA-256 sums and
cached in ``dist/cache``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHON_VERSION = "3.14.8"
PYTHON_TAG = "314"
PYTHON_SHA256 = {
    "amd64": "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310",
    "arm64": "155be84ccb57c6331cf0e39001c78a1dfac3be62f403f3ff5f2e29b80dda7ebe",
}
PYYAML_WHEELS = {
    "amd64": (
        "https://files.pythonhosted.org/packages/23/20/bb6982b26a40bb43951265ba29d4c246ef0ff59c9fdcdf0ed04e0687de4d/pyyaml-6.0.3-cp314-cp314-win_amd64.whl",
        "4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac",
    ),
    "arm64": (
        None,  # resolved from PyPI at build time (pinned by hash below)
        "93dda82c9c22deb0a405ea4dc5f2d0cda384168e466364dec6255b293923b2f3",
    ),
}


# GLFW (window and OpenGL context of the compositor); the wheel carries glfw3.dll.
# There is no Windows-on-ARM wheel: such a bundle runs with the window engine.
GLFW_WHEELS = {
    "amd64": (
        "https://files.pythonhosted.org/packages/e0/6c/4ca5f3ab85a8d7f612ce857208726e9b834e54e559751e3d4f98cc2f7509/glfw-2.10.2-py2.py3-none-win_amd64.whl",
        "15fd0666cd8f1b0ecb535c3abb712b8ddda053bf8d16de2353b1c6ced0e65402",
    ),
}


# mpv for Windows (shinchiro builds, GitHub releases): player and libmpv of the same build.
MPV_BUILD = "20261010"
MPV_ARCHIVES = {
    "mpv": ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261010/mpv-x86_64-20261010-git-b2c255c13e.7z",
            "70a568c046a10f0b365319c4ec5382d8fb37f509615944b7e9932b66e5ccfb25"),
    "dev": ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261010/mpv-dev-x86_64-20261010-git-b2c255c13e.7z",
            "394490ed6354c2294b87c63dfa3663b23524b5551aace9cbe35c4085682b14fa"),
}

THIRD_PARTY = """Aethra is MIT-licensed (LICENSE.txt). This package also contains:

* Python {python} (embeddable distribution) - PSF License Agreement.
  https://www.python.org/
* PyYAML 6.0.3 - MIT License. https://pyyaml.org/
* pyGLFW 2.10.2 with GLFW 3.4 (glfw3.dll) - MIT and zlib/libpng licenses.
  https://github.com/FlorianRhiem/pyGLFW, https://www.glfw.org/
* mpv and libmpv (mpv/), Windows build {mpv_build} by shinchiro, with FFmpeg and
  further libraries linked in - GPL version 2 or later (see mpv/doc and the
  build's documentation). Sources: https://github.com/mpv-player/mpv,
  https://github.com/shinchiro/mpv-winbuild-cmake (build scripts and the
  versions of every library), https://ffmpeg.org/
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected: str | None) -> Path:
    if not (dest.is_file() and (expected is None or sha256(dest) == expected)):
        print(f"downloading {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as out:
            shutil.copyfileobj(response, out)
        tmp.replace(dest)
    if expected is not None:
        actual = sha256(dest)
        if actual != expected:
            dest.unlink()
            raise SystemExit(f"checksum mismatch for {dest.name}: {actual} != {expected}")
    return dest


def pypi_wheel_url(filename: str) -> str:
    import json

    with urllib.request.urlopen("https://pypi.org/pypi/PyYAML/6.0.3/json", timeout=60) as response:
        data = json.load(response)
    for item in data["urls"]:
        if item["filename"] == filename:
            return item["url"]
    raise SystemExit(f"{filename} not found on PyPI")


def extract(archive: Path, dest: Path) -> None:
    """Unpack a .zip, or a .7z with 7-Zip or bsdtar (Windows 10+ ships it as tar.exe)."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    seven = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
    if seven is not None:
        subprocess.run([seven, "x", "-y", f"-o{dest}", str(archive)], check=True, stdout=subprocess.DEVNULL)
        return
    tar = shutil.which("bsdtar") or (shutil.which("tar") if sys.platform == "win32" else None)
    if tar is None:
        raise SystemExit("extracting .7z needs 7-Zip (7z) or bsdtar; or extract it yourself and pass the folder/file")
    subprocess.run([tar, "-xf", str(archive), "-C", str(dest)], check=True)


def write_crlf(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def build(args: argparse.Namespace) -> Path:
    arch = args.arch
    out = Path(args.out).resolve()
    cache = out.parent / "cache"
    # Rebuilding in place keeps the show: show.yaml, the clips and the logs.
    kept = out.parent / (out.name + ".kept")
    saved = None
    if out.exists():
        keep = out / "show.yaml"
        saved = keep.read_bytes() if keep.is_file() and not args.reset_config else None
        if kept.exists():
            raise SystemExit(f"{kept} exists (left by an interrupted build): move its media/logs back first")
        kept.mkdir()
        for name in ("media", "logs"):
            if (out / name).is_dir():
                shutil.move(str(out / name), kept / name)
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1. Python (embeddable distribution)
    py_zip = download(
        f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-{arch}.zip",
        cache / f"python-{PYTHON_VERSION}-embed-{arch}.zip",
        PYTHON_SHA256[arch],
    )
    py_dir = out / "python"
    with zipfile.ZipFile(py_zip) as z:
        z.extractall(py_dir)
    # The ._pth file is the only search path an embedded Python uses
    # (PYTHONPATH is ignored): add the application and site-packages.
    (py_dir / f"python{PYTHON_TAG}._pth").write_text(
        f"python{PYTHON_TAG}.zip\n.\n..\\src\nLib\\site-packages\n", encoding="ascii"
    )

    # 2. PyYAML
    url, digest = PYYAML_WHEELS[arch]
    wheel_name = f"pyyaml-6.0.3-cp{PYTHON_TAG}-cp{PYTHON_TAG}-win_{arch}.whl"
    wheel = download(url or pypi_wheel_url(wheel_name), cache / wheel_name, digest)
    site = py_dir / "Lib" / "site-packages"
    site.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as z:
        z.extractall(site)

    # 2b. GLFW
    if arch in GLFW_WHEELS:
        url, digest = GLFW_WHEELS[arch]
        glfw_wheel = download(url, cache / url.rsplit("/", 1)[1], digest)
        with zipfile.ZipFile(glfw_wheel) as z:
            z.extractall(site)
    else:
        print(f"note: no glfw for {arch}: the show will run with the window engine")

    # 3. The application
    shutil.copytree(ROOT / "src" / "aethra", out / "src" / "aethra", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 4. mpv
    mpv_dir = out / "mpv"
    if args.download_mpv:
        if arch != "amd64":
            raise SystemExit("--download-mpv: only amd64 builds are pinned")
        url, digest = MPV_ARCHIVES["mpv"]
        args.mpv_archive = str(download(url, cache / url.rsplit("/", 1)[1], digest))
        url, digest = MPV_ARCHIVES["dev"]
        args.libmpv = str(download(url, cache / url.rsplit("/", 1)[1], digest))
    if args.mpv_dir:
        src = Path(args.mpv_dir)
        if not (src / "mpv.exe").is_file():
            raise SystemExit(f"{src} does not contain mpv.exe")
        shutil.copytree(src, mpv_dir)
    elif args.mpv_archive:
        archive = Path(args.mpv_archive)
        extract(archive, mpv_dir)
        nested = [p for p in mpv_dir.rglob("mpv.exe")]
        if not nested:
            raise SystemExit("no mpv.exe in the archive")
        if nested[0].parent != mpv_dir:  # archive with a top-level folder
            inner = nested[0].parent
            for item in inner.iterdir():
                shutil.move(str(item), mpv_dir / item.name)
    else:
        mpv_dir.mkdir()
        (mpv_dir / "PUT-MPV-HERE.txt").write_text(
            "Copy mpv.exe (and the files that come with it) into this folder.\n"
            "Windows builds: see https://mpv.io/installation/ (e.g. the shinchiro builds).\n"
        )

    # 4b. libmpv, next to mpv.exe (Aethra looks for it there)
    if args.libmpv:
        source = Path(args.libmpv)
        if source.suffix.lower() == ".dll":
            dll = source
        else:
            unpacked = cache / ("unpacked-" + source.stem)
            if unpacked.exists():
                shutil.rmtree(unpacked)
            extract(source, unpacked)
            found = list(unpacked.rglob("libmpv-2.dll"))
            if not found:
                raise SystemExit(f"no libmpv-2.dll in {source}")
            dll = found[0]
        mpv_dir.mkdir(exist_ok=True)
        shutil.copy2(dll, mpv_dir / "libmpv-2.dll")
    else:
        print("note: no --libmpv: the show will run with the window engine (no cross-fades)")

    # 5. Scripts, config, docs
    write_crlf(out / "Aethra.cmd", (ROOT / "scripts" / "windows" / "Aethra.cmd").read_text())
    scripts = out / "scripts"
    scripts.mkdir()
    for ps1 in (ROOT / "scripts" / "windows").glob("*.ps1"):
        write_crlf(scripts / ps1.name, ps1.read_text())
    if saved is not None:
        (out / "show.yaml").write_bytes(saved)
    else:
        write_crlf(out / "show.yaml", (ROOT / "examples" / "show.example.yaml").read_text())
    write_crlf(out / "show.example.yaml", (ROOT / "examples" / "show.example.yaml").read_text())
    for name in ("media", "logs"):
        if (kept / name).is_dir():
            shutil.move(str(kept / name), out / name)
        else:
            (out / name).mkdir()
    if kept.exists():
        kept.rmdir()
    docs = out / "docs"
    shutil.copytree(ROOT / "docs", docs, ignore=shutil.ignore_patterns("*.tmp"))
    shutil.copy2(ROOT / "README.md", out / "README.md")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    match = re.search(r'__version__ = "([^"]+)"', (ROOT / "src" / "aethra" / "__init__.py").read_text())
    version = match.group(1) if match else "unknown"
    (out / "VERSION.txt").write_text(f"Aethra {version}\nPython {PYTHON_VERSION} ({arch})\n")
    write_crlf(out / "THIRD-PARTY.txt", THIRD_PARTY.format(python=PYTHON_VERSION, mpv_build=MPV_BUILD if args.download_mpv else "(as supplied)"))

    if args.zip:
        archive = out.parent / f"Aethra-{version}-win-{arch}.zip"
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(out.rglob("*")):
                z.write(path, Path("Aethra") / path.relative_to(out))
        print(f"archive: {archive}")
    if args.installer:
        makensis = shutil.which("makensis")
        if makensis is None:
            raise SystemExit("--installer needs NSIS (makensis): docker compose -f docker/build.compose.yaml run --rm build")
        setup = out.parent / f"Aethra-{version}-setup.exe"
        subprocess.run([makensis, "-V2", f"-DVERSION={version}", f"-DSOURCE={out}", f"-DOUTFILE={setup}",
                        str(ROOT / "tools" / "installer" / "aethra.nsi")], check=True)
        print(f"installer: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")
    print(f"bundle:  {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "dist" / "Aethra"))
    parser.add_argument("--arch", choices=sorted(PYTHON_SHA256), default="amd64")
    parser.add_argument("--mpv-dir", help="folder that contains mpv.exe")
    parser.add_argument("--mpv-archive", help="mpv Windows build archive (.7z needs 7-Zip or bsdtar, or .zip)")
    parser.add_argument("--libmpv", help="libmpv-2.dll, or the mpv-dev archive of the same mpv build")
    parser.add_argument("--download-mpv", action="store_true", help=f"use the pinned mpv build {MPV_BUILD} (mpv and libmpv)")
    parser.add_argument("--installer", action="store_true", help="also build dist/Aethra-<version>-setup.exe (needs makensis)")
    parser.add_argument("--zip", action="store_true", help="also create dist/Aethra-<version>-win-<arch>.zip")
    parser.add_argument("--reset-config", action="store_true", help="replace an existing show.yaml with the example")
    build(parser.parse_args())
    return 0


if __name__ == "__main__":
    sys.exit(main())
