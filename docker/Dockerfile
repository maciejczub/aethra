# Linux test environment for Aethra: real mpv + ffmpeg, used to run the
# integration tests. Stand-alone mpv runs with --vo=null; the compositor
# (libmpv + OpenGL) runs on a virtual X display (Xvfb, Mesa software
# rendering). The show itself runs on Windows; this image only exists to
# exercise the code on a real mpv.
FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        mpv libmpv2 ffmpeg \
        xvfb xauth libgl1 libglx-mesa0 libgl1-mesa-dri \
        libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6 libxext6 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "pytest>=8" "pyyaml>=6" "glfw>=2.7"

WORKDIR /src
# Every command runs with a virtual display (the compositor needs one).
ENTRYPOINT ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"]
