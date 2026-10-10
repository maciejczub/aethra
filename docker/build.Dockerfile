# Release build environment for Aethra: makes the Windows bundle and the
# installer (NSIS) on Linux, and the logo files (tools/make_logo_assets.py). Usage: see docker/build.compose.yaml.
FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends nsis libarchive-tools poppler-utils librsvg2-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
