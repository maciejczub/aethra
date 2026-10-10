# Release build environment for Aethra: makes the Windows bundle and the
# installer (NSIS) on Linux. Usage: see docker/build.compose.yaml.
FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends nsis libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
