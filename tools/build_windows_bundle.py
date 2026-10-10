#!/usr/bin/env python3
"""Build a self-contained Aethra folder for the Windows show laptop.

The result needs no installation and no internet on the laptop: copy the
folder (or unzip the archive), put the clips into ``media``, edit
``show.yaml`` and start ``Aethra.cmd``.

    python tools/build_windows_bundle.py --mpv-dir C:/tools/mpv
    python tools/build_windows_bundle.py --mpv-archive mpv-x86_64-20260101-git-abc.7z
    python tools/build_windows_bundle.py            # without mpv (add it later to mpv/)

Layout of dist/Aethra:

    Aethra.cmd            start the show (restarts it if it ever crashes)
    show.yaml             the show (created from the example if missing)
    python/               Windows embeddable Python + PyYAML
    src/aethra/           the application
    mpv/                  mpv.exe and its files
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


def write_crlf(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def build(args: argparse.Namespace) -> Path:
    arch = args.arch
    out = Path(args.out).resolve()
    cache = out.parent / "cache"
    if out.exists():
        keep = out / "show.yaml"
        saved = keep.read_bytes() if keep.is_file() and not args.reset_config else None
        shutil.rmtree(out)
    else:
        saved = None
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

    # 3. The application
    shutil.copytree(ROOT / "src" / "aethra", out / "src" / "aethra", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 4. mpv
    mpv_dir = out / "mpv"
    if args.mpv_dir:
        src = Path(args.mpv_dir)
        if not (src / "mpv.exe").is_file():
            raise SystemExit(f"{src} does not contain mpv.exe")
        shutil.copytree(src, mpv_dir)
    elif args.mpv_archive:
        archive = Path(args.mpv_archive)
        mpv_dir.mkdir()
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(mpv_dir)
        else:
            seven = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
            if seven is None:
                raise SystemExit("extracting .7z needs 7-Zip (7z) on PATH; or extract it yourself and use --mpv-dir")
            subprocess.run([seven, "x", "-y", f"-o{mpv_dir}", str(archive)], check=True, stdout=subprocess.DEVNULL)
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
    (out / "media").mkdir()
    (out / "logs").mkdir()
    docs = out / "docs"
    shutil.copytree(ROOT / "docs", docs, ignore=shutil.ignore_patterns("*.tmp"))
    shutil.copy2(ROOT / "README.md", out / "README.md")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    match = re.search(r'__version__ = "([^"]+)"', (ROOT / "src" / "aethra" / "__init__.py").read_text())
    version = match.group(1) if match else "unknown"
    (out / "VERSION.txt").write_text(f"Aethra {version}\nPython {PYTHON_VERSION} ({arch})\n")

    if args.zip:
        archive = out.parent / f"Aethra-{version}-win-{arch}.zip"
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(out.rglob("*")):
                z.write(path, Path("Aethra") / path.relative_to(out))
        print(f"archive: {archive}")
    print(f"bundle:  {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "dist" / "Aethra"))
    parser.add_argument("--arch", choices=sorted(PYTHON_SHA256), default="amd64")
    parser.add_argument("--mpv-dir", help="folder that contains mpv.exe")
    parser.add_argument("--mpv-archive", help="mpv Windows build archive (.7z needs 7-Zip, or .zip)")
    parser.add_argument("--zip", action="store_true", help="also create dist/Aethra-<version>-win-<arch>.zip")
    parser.add_argument("--reset-config", action="store_true", help="replace an existing show.yaml with the example")
    build(parser.parse_args())
    return 0


if __name__ == "__main__":
    sys.exit(main())
