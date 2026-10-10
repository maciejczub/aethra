# Choosing the codec

The codec is chosen by measurement on the show laptop itself: the right one
plays with **zero dropped frames** and the **lowest CPU load**, with margin.
Decoding performance depends on the GPU, its driver and the Windows build, so
results from another computer do not count.

## Candidates

| Codec | Container | Decoding in mpv | Notes |
|---|---|---|---|
| H.264 (8-bit 4:2:0, High profile) | .mp4 | Hardware (`d3d11va`) on practically every GPU | Small files. Long GOP: seeking (cold starts, crash recovery) decodes from the previous keyframe; keep the GOP ≤ 2 s. |
| H.264 all-intra | .mp4 | Hardware | Every frame a keyframe: instant exact seeks, larger files. |
| HEVC (8-bit) | .mp4 | Hardware on most GPUs from ~2016 on | Smaller files than H.264 at the same quality; check hardware support. |
| HAP / HAP Q | .mov | Software (FFmpeg's HAP decoder: Snappy + DXT on the CPU) | Very light decoding, very large files (fast SSD needed). mpv does not upload DXT textures to the GPU, so the CPU does the work. |
| MJPEG | .avi/.mov | Software | All-intra; also the format for the xjadeo fallback ([xjadeo-fallback.md](xjadeo-fallback.md)). |

The master stays 25 fps, 1920×1080 (or the stage resolution), progressive,
square pixels. Encode every clip of the set the same way.

## Procedure

1. On any computer with ffmpeg, generate the candidates (60 s each):

   ```bash
   python tools/make_test_clips.py codecs --out test-clips --seconds 60
   ```

   Each clip shows timecode, frame number and a bar that moves a fixed step per
   frame, so a dropped or repeated frame is visible.

2. On the show laptop, with the stage display connected and set up as for the
   show (resolution, 50 Hz), power plugged in:

   ```bat
   .\Aethra.cmd bench test-clips\*.mp4 test-clips\*.mov test-clips\*.avi --config show.yaml --seconds 60
   ```

   Every file is played full screen on the stage output like a real deck, once
   with hardware decoding (`auto-safe`) and once in software (`no`); add
   `--hwdec d3d11va` to test a specific mode. (From a development checkout
   without the bundle: `python -m aethra bench ...`.)

3. Read the table:

   ```
   file                               hwdec      in use       codec      size        CPU %  drop  late  verdict
   test-h264-1080p25.mp4              auto-safe  d3d11va      h264       1920x1080     2.1     0     0  OK
   test-h264-1080p25.mp4              no         no           h264       1920x1080    11.8     0     0  OK
   test-hap-1080p25.mov               auto-safe  no           hap        1920x1080     6.0     0     0  OK
   ...
   display refresh rate reported by mpv: 50 Hz
   ```

   * `in use` is the decoder mpv really used — `no` means software decoding even
     if hardware was requested.
   * `drop` = frames dropped by the decoder or the video output; `late` =
     frames mpv judged delayed by external causes. Both must be 0.
   * `CPU %` is the mpv process's share of the whole CPU.

4. Repeat the winner with your real, longest, busiest clip, and once while the
   laptop is doing something else (e.g. copying files), to see the margin.

5. Put the result in `show.yaml` (`mpv.hwdec`) and in your notes.

### With the compositor engine

The compositor's players render through OpenGL, which narrows mpv's choice of
hardware decoders: `nvdec` on NVIDIA (no copy), `dxva2` through the
`WGL_NV_DX_interop` extension on most Intel and AMD drivers; `d3d11va` without
copying needs Direct3D and is not available there. `aethra bench` measures
mpv's own window, so after choosing the codec check what the layers use: the
players' logs (`logs\mpv-*-A.log`, line `Using hardware decoding (...)`), or
the test overlay. If a layer falls back to software decoding and the CPU load
is too high, try `hwdec: auto-copy` (or `d3d11va-copy`): the GPU decodes and
the frames are copied back, which works with any rendering API.

## Encoding the set

H.264 example (25 fps, 2 s GOP, high quality, fast start):

```bash
ffmpeg -i master.mov -r 25 -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p \
       -profile:v high -g 50 -bf 2 -movflags +faststart -an song.mp4
```

HAP:

```bash
ffmpeg -i master.mov -r 25 -c:v hap -format hap -an song.mov
```

Audio is not needed in the clips (the audio comes from REAPER); `-an` keeps
files smaller. After encoding run `aethra verify show.yaml` to decode every
file once.

## Display refresh rate

Set the stage output to 50 Hz (or 100 Hz): every 25 fps frame is then shown
for exactly two refreshes. At 60 Hz frames alternate between 2 and 3 refreshes,
which looks like judder on slow pans. `aethra bench` prints the refresh rate mpv
detected.
