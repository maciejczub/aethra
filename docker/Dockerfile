# Linux test environment for Aethra: real mpv + ffmpeg, used to run the
# integration tests (mpv with --vo=null, no display needed). The show itself
# runs on Windows; this image only exists to exercise the code on a real mpv.
FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends mpv ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "pytest>=8" "pyyaml>=6"

WORKDIR /src
