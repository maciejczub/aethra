# Fallback: xjadeo following MTC

[xjadeo](https://xjadeo.sourceforge.net/) (by Robin Gareus / x42, GPL) is a
video player that shows the frame of **one** video file that matches an incoming
timecode. It is Aethra's prepared emergency alternative: it runs on the show
laptop and follows the MIDI Time Code (MTC) that REAPER already sends. If Aethra
cannot be made to work during a show, the operator quits it and starts xjadeo,
and REAPER keeps driving the picture.

- **What it covers:** a failure of Aethra or of its video players on the laptop
  (a software fault, a broken installation).
- **What it does not cover:** MIDI not arriving (cable, rtpMIDI session). Then
  xjadeo has no timecode to follow either; use Aethra's manual keys
  ([operation.md](operation.md)).

Prepare it before the tour and test it in rehearsal. An untested fallback is not
a fallback.

## 1. How it works, and what follows from it

xjadeo shows *file frame = incoming MTC frame + offset*. It knows nothing about
songs, cues or fillers. The fallback therefore needs **one long video, the
timeline video**, laid out exactly like the REAPER project:

```
REAPER timecode  00:00:00:00          00:03:10:00                00:07:42:00
                 │ walk-on, talk      │ song 1                   │ song 2 ...
MTC (25 fps) ────┴────────────────────┴──────────────────────────┴──────────────▶
timeline video   │ filler / black     │ 01-opening.mp4           │ 02-second.mp4 ...
                 frame 0              frame 4750                 frame 11550
```

- Every clip sits where Aethra would show it: clip position `offset` at the
  song's trigger timecode (`mtc_start`). Filler or black fills the gaps.
- The video starts at the timecode of REAPER's project start (00:00:00:00) and
  runs well past the end of the show.
- Any change to the set, a clip, or a song's position in REAPER means building
  the timeline video again.
- Use an intra-frame codec, MJPEG in AVI, so that every jump in REAPER shows the
  exact frame at once (4.3).
- MTC must keep running. REAPER sends it only while playing over the timecode
  generator item. When MTC stops, xjadeo holds the last frame.

## 2. What to prepare

1. REAPER sends 25 fps MTC to the cue port, from a generator item that covers
   the whole show ([reaper-setup.md](reaper-setup.md), section 6). Keep it in
   every show project, so that switching to the fallback needs no change in
   REAPER.
2. Every cue in `show.yaml` has `mtc_start`, the REAPER timecode of its trigger
   note ([reaper-setup.md](reaper-setup.md), 4.5).
3. xjadeo is installed on the show laptop (section 3).
4. The timeline video is built, copied to the laptop's internal disk, and
   checked (section 4).
5. You know the PortMidi number of the MIDI port (section 5).
6. The launcher is edited and tested, and has a desktop shortcut
   (sections 7 and 9).

## 3. Install xjadeo

| | |
|---|---|
| Current version | 0.8.15, November 2025 (binaries built with FFmpeg 7.1.2) |
| Download | <https://xjadeo.sourceforge.net/download.html>: "Windows 64bit", file `xjadeo_installer_w64_v0_8_15.exe` (SourceForge). All releases: <https://sf.net/projects/xjadeo/files/xjadeo/> |
| Source, issues | <https://github.com/x42/xjadeo> |
| Installs to | `C:\Program Files\xjadeo\` (`xjadeo.exe`, plus `xjremote.bat`); Start menu folder *xjadeo* |
| Manual | <https://xjadeo.sourceforge.net/xjadeo.html> (the man page; `xjadeo.exe -h` prints the options) |

1. Run the installer (it needs administrator rights). Install while the laptop is
   still online; nothing is downloaded at run time.
2. JACK is not needed. The download page says JACK must be installed "to
   properly use xjadeo", but that concerns JACK transport sync. The Windows build
   loads JACK only if it is present, and MTC through PortMidi works without it.
3. Check the build in a Command Prompt. This prints the version and the
   supported MIDI drivers, which must include `portmidi`:

   ```bat
   "C:\Program Files\xjadeo\xjadeo.exe" -V
   ```

## 4. Make the timeline video

### 4.1 With `tools/make_xjadeo_timeline.py` (recommended)

The tool reads `show.yaml` and builds the video with ffmpeg. Run it on any
computer with a development checkout of Aethra (Python ≥ 3.11 with PyYAML) and
`ffmpeg`/`ffprobe` on the PATH:

```bash
python tools/make_xjadeo_timeline.py show.yaml --out show-timeline.avi --dry-run    # print the plan only
python tools/make_xjadeo_timeline.py show.yaml --out show-timeline.avi --tail 600   # build it
```

- It places every cue the way Aethra plays it: clip position `offset` at the
  cue's `mtc_start`. A clip runs to its end, or until the next cue's trigger.
- Gaps get the show's filler (`filler.mode`/`file`/`color`), or black with
  `--black-gaps`.
- `--tail` adds filler after the last cue. The default is 30 s; make it longer
  than REAPER can ever run past the last song.
- Output: MJPEG in AVI at `stage.fps` and the stage size. `--quality` sets the
  MJPEG quality, from 2 (best) to 31 (default 3).
- `--start` sets the timecode of the video's first frame (default
  `00:00:00:00`). Leave it there and keep REAPER's project start time at 0;
  otherwise xjadeo needs a matching offset (section 6).
- Compare the `--dry-run` plan, which lists each segment with its timecode,
  against the markers in REAPER's Region/Marker Manager.

### 4.2 By hand

If you lay the clips out yourself, in REAPER or a video editor, render the whole
timeline from 00:00:00:00 to well past the end at 25 fps, then transcode it to
MJPEG:

```bash
ffmpeg -i show-timeline-master.mov -an \
       -vf "fps=25,scale=1920:1080:flags=lanczos,setsar=1" \
       -c:v mjpeg -q:v 3 -pix_fmt yuvj420p show-timeline.avi
```

- `-q:v` sets the quality, from 2 (best) to 31; 3 to 5 is good.
- `-pix_fmt yuvj420p` gives full-range 4:2:0 JPEG frames.
- `-an` drops the audio, which xjadeo does not play anyway.
- REAPER can render the timeline itself. Put the clips on a video track at their
  cue positions, then *File > Render*: format *Video (FFmpeg/libav encoder)*,
  container AVI, video codec MJPEG, audio codec none, 1920×1080, 25 fps, bounds
  *Entire project*. MJPEG is in REAPER's codec list, but the FFmpeg bundled with
  REAPER is a minimal build, so **verify on your system** that it renders. If
  it does not, install the full FFmpeg libraries for REAPER, or render any
  high-quality codec and transcode with the command above.

Check the result:

```bash
ffprobe -v error -select_streams v:0 \
        -show_entries stream=codec_name,width,height,r_frame_rate,nb_frames \
        -of default=nw=1 show-timeline.avi
```

You should see `codec_name=mjpeg` and `r_frame_rate=25/1`.

### 4.3 Why MJPEG

- Every MJPEG frame is a keyframe. When the first 500 frames of a file are all
  keyframes, xjadeo skips indexing and seeks directly ("direct seek mode"). The
  file is ready almost at once after loading, and every jump shows the right
  frame immediately.
- Any other file is indexed first. The screen shows "Indexing. Please wait.",
  and each jump then decodes forward from the previous keyframe. Files whose
  keyframes are too far apart are refused with "Index Error. File is not
  suitable." (`-K` raises the limit, which defaults to 100 frames). The xjadeo
  manual recommends AVI/MJPEG.
- Other all-intra formats should also work with the FFmpeg in xjadeo's Windows
  build, but **verify on your system** before relying on one.
- MJPEG files are large: very roughly 1–10 GB per 10 minutes at 1080p with
  `-q:v 3`, depending on the content. A 720p timeline (`scale=1280:720`) or
  `-q:v 5` is much smaller, and xjadeo scales it to full screen. Files over 4 GB
  are supported since xjadeo 0.8.3. Keep the file on the laptop's internal SSD.

## 5. Find the MIDI port number

On Windows, xjadeo reads MIDI through PortMidi (the Windows MME interface) and
identifies the input by a **number**, not by name.

1. Make sure Aethra and `aethra monitor` are not running. They hold the port.
2. In a Command Prompt, run:

   ```bat
   "C:\Program Files\xjadeo\xjadeo.exe" -v -d portmidi -m -1
   ```

3. The console lists the PortMidi devices. For example (names and numbers differ
   on every machine):

   ```
   0: MMSystem, Microsoft MIDI Mapper (output)
   1: MMSystem, AETHRA-STAGE (input) (*)
   2: MMSystem, Microsoft GS Wavetable Synth (output)
   3: MMSystem, AETHRA-STAGE (output)
   ```

   Take the number of the `(input)` line with your port name; here it is `1`.
   `(*)` marks the PortMidi default, which `-m -1` would use: the first input,
   unless another one was chosen with PortMidi's `pmdefaults` tool.
4. Close xjadeo (Esc).

- The number can change when MIDI devices are added or removed (USB
  interfaces, other virtual ports) and after Windows MIDI updates
  ([reaper-setup.md](reaper-setup.md), 2.5). Keep the laptop's MIDI setup fixed
  and check the number again after any change.
- Always give `-d portmidi`. JACK MIDI comes first in xjadeo's driver list, so
  without it xjadeo may try JACK.
- The xjadeo menu item *Sync > MTC (PortMidi)* always connects to the default
  (`-1`) port. Prefer the launcher's explicit number.

## 6. Command-line options used here

| Option | Meaning |
|---|---|
| `-d portmidi` | MIDI driver: PortMidi. |
| `-m <n>` | Sync to MTC from PortMidi input `n` (`-1`: the default / first input). |
| `-o <frames>` | Offset: frames added to the incoming timecode. Positive values show later frames, which fixes a late picture. It also accepts `HH:MM:SS:FF`, but give negative values as plain frames (for example `-90000` = −1 hour at 25 fps). |
| `-S` | No splash (otherwise xjadeo shows its logo animation at start). |
| `-i <n>` | On-screen display: `0` off, `1` frame number, `2` sync timecode, `3` both. |
| `-R` | Remote control on standard input. The launcher uses it to place the window. |
| `-s` | Start full screen. It fills the display the window opens on, usually the *main* display (section 7). |
| `-a` | Keep the window on top. |
| `-b` | Fill the screen without letterboxing (the default keeps the aspect ratio). |
| `-M <n>` | MTC conversion: `0` (default) uses the MTC frame rate. That is right for 25 fps MTC and a 25 fps video. |
| `-K <n>` | Keyframe distance limit for non-intra files (default 100). |
| `-x <n>` | Video output (default: automatic, OpenGL); `-x -1` lists the modes. |
| `-v`, `-V`, `-h` | Verbose, version and drivers, help. |

## 7. The launcher

On Windows, xjadeo has no command-line option to choose a display (`-X` exists
only in Linux builds with Xinerama), and `-s` fills the display the window opens
on, normally the laptop panel. The launcher therefore starts xjadeo in
remote-control mode (`-R`) and sends it commands on standard input:

1. shrink the window and move it onto the stage display,
2. wait two seconds, then switch to full screen (xjadeo fills the display that
   holds the window),
3. hide the mouse pointer,
4. lock down the keys that could ruin the picture.

The last step matters because in Aethra **Esc means blackout, but in xjadeo Esc
quits** and shows the desktop. `set override 385` disables quitting with Esc,
Ctrl+Q and the menu (1), the *Sync* menu (128), and opening or closing files
with Ctrl+O and Ctrl+W (256). Alt+F4 on the video still quits, on purpose.

Save this as `xjadeo-fallback.cmd` next to the timeline video, edit the settings,
and put a shortcut to it on the desktop:

```bat
@echo off
rem ======================================================================
rem  xjadeo-fallback.cmd - EMERGENCY video for an Aethra show.
rem  Shows the frame of ONE timeline video that matches the MIDI Time Code
rem  from REAPER. Quit Aethra first. Quit this: Alt+F4 on the video.
rem  See docs/xjadeo-fallback.md in the Aethra repository.
rem ======================================================================
setlocal EnableExtensions

rem ---- settings --------------------------------------------------------
rem xjadeo.exe (default folder of the 64-bit installer)
set "XJADEO=C:\Program Files\xjadeo\xjadeo.exe"
rem The timeline video (tools\make_xjadeo_timeline.py)
set "VIDEO=C:\Show\fallback\show-timeline.avi"
rem PortMidi number of the MIDI input that carries MTC. List them with:
rem   "C:\Program Files\xjadeo\xjadeo.exe" -v -d portmidi -m -1
set "MIDI_IN=1"
rem A point well inside the stage display, in Windows desktop coordinates.
rem Stage display right of the main display: X = main display width + 100.
set "STAGE_X=2020"
set "STAGE_Y=100"
rem Frames added to the incoming timecode; +1 shows one frame later.
set "OFFSET=0"
rem ----------------------------------------------------------------------

if not exist "%XJADEO%" goto :no_xjadeo
if not exist "%VIDEO%" goto :no_video
tasklist /fi "imagename eq mpv.exe" | find /i "mpv.exe" >nul && echo WARNING: mpv.exe is running - quit Aethra first, it holds the MIDI port.

title xjadeo fallback - keep this window open
echo xjadeo: "%VIDEO%"
echo MIDI input %MIDI_IN%, offset %OFFSET% frames. Quit: Alt+F4 on the video.

rem The echo lines below are xjadeo remote-control commands (-R). Keep
rem paths out of the bracketed block: a ")" in a path would break it.
rem Numeric arguments on purpose: cmd may append a space to each line.
(
echo window size 640x360
echo window position %STAGE_X%x%STAGE_Y%
ping -n 3 127.0.0.1 >nul
echo window fullscreen 1
echo window mouse toggle
echo set override 385
) | "%XJADEO%" -R -S -i 0 -d portmidi -m %MIDI_IN% -o %OFFSET% "%VIDEO%"

echo xjadeo has stopped.
pause
exit /b 0

:no_xjadeo
echo xjadeo not found: "%XJADEO%"
pause
exit /b 1

:no_video
echo Timeline video not found: "%VIDEO%"
pause
exit /b 1
```

Notes:

- The remote-control commands come from xjadeo's own command set (`window size`,
  `window position`, `window fullscreen`, `window mouse`, `set override`). The
  installer's `xjremote.bat` shows that `-R` is meant to be used on Windows.
  Still, **verify on your system** that the window lands on the stage display
  in rehearsal.
- **Display scaling.** xjadeo is not DPI-aware. If the main display uses scaling
  above 100 %, Windows may give xjadeo scaled coordinates. A point well inside
  the stage display (main width + 100) still works; an exact edge coordinate may
  not. Set the stage display itself to 100 % scaling.
- **Manual placement**, if the window stays on the laptop panel: click the
  xjadeo window, press **Win+Shift+→** (Windows moves the active window to the
  next display), then **F** for full screen and **M** to hide the pointer.
- The launcher's console window must stay open; minimise it if it is in the
  way. xjadeo attaches to this console, so closing the window, or pressing
  Ctrl+C in it, ends xjadeo too.

## 8. Keys and mouse

Click the xjadeo window first; the keys only work while it has the focus.

| Key | Action |
|---|---|
| `F` | Full screen on/off (on the display that holds the window) |
| `M` | Mouse pointer on/off over the window |
| `=` / `-` | Offset +1 / −1 frame (live fine-tuning; note the value for the launcher) |
| `+` / `_` | Offset +1 / −1 second |
| `\` | Offset back to zero |
| `S` | Show/hide the sync-source timecode (`MTC hh:mm:ss:ff`) |
| `V` | Cycle the video timecode / frame number display |
| `I`, `G` | Show/hide file time info, file geometry |
| `O` | Cycle the offset display |
| `Shift+C` | Clear all on-screen text |
| `L` | Letterbox on/off |
| `Esc`, `Ctrl+Q` | **Quit** (blocked by the launcher) |
| `Ctrl+O`, `Ctrl+W` | Open / close a file (blocked by the launcher; closing leaves black with a grey cross) |
| `Alt+F4` | Close the window and quit (still works) |
| `Space`, `Backspace` | JACK transport only; no effect with MTC |
| Right-click | Menu: *File*, *Sync*, *Display*, *OSD*, *Offset*, *Transport* |

When xjadeo has no frame to show, it draws **a thin grey diagonal cross on
black**. That happens when the timecode is outside the video, when no file is
loaded, and when a frame cannot be decoded.

## 9. Testing in rehearsal

Do this with the real REAPER project and the real timeline video, ideally once
per tour and after every rebuild of the video.

1. Start the launcher with `-i 3` instead of `-i 0` (on-screen frame number and
   MTC timecode), or press `S` and `V`.
2. **Play from the start.** The OSD timecode matches REAPER's time display, and
   the frame number is timecode × 25 (plus `OFFSET`).
3. **Song starts.** Play into each song: its clip starts at the cue note, as in
   Aethra.
4. **Jump during playback.** Click the ruler, go to markers, loop a section. The
   picture follows within a frame or two (REAPER sends a full-frame message on
   seeks).
5. **Stop.** The last frame stays on screen. **Locate while stopped:** nothing
   happens, because REAPER's generator is silent when stopped, unless vVIMTC
   sends full frames ([reaper-setup.md](reaper-setup.md), 6.4). **Play again:**
   the picture jumps to the new position within about 0.1 s (xjadeo needs eight
   quarter frames, two frames' worth, to read the time).
6. **Past the end.** Play past the last song: filler or black until the end of
   the tail, then the grey cross. Make the tail long enough for every real
   situation.
7. **Latency.** Build a test timeline from a show file containing only the sync
   cue (`tools/make_test_clips.py sync`, `sync-flash-25.mp4`, with `mtc_start` at
   a position where REAPER plays `sync-click.wav`). Film the screen and a
   loudspeaker in slow motion as in [testing.md](testing.md), test 5. If the
   picture is late, set `OFFSET` to the delay in frames (for example 2). The
   value is xjadeo's own and differs from Aethra's `latency_compensation`.
8. **The switch.** Rehearse section 10 once and time it.

## 10. Switching to the fallback during a show

1. If possible, switch between songs, and ask lighting or video to black out the
   projector or LED wall (shutter, AV mute, processor blackout) for the switch.
   The Windows desktop can be visible for a few seconds, so set the stage
   display's desktop background to solid black ([windows-setup.md](windows-setup.md)).
2. Quit Aethra: `Ctrl+Shift+Q` twice ([operation.md](operation.md)). If it does
   not respond, close the Aethra console window, then end every `mpv.exe` and
   Aethra's `python.exe` in Task Manager (*Details* tab). Aethra's launcher
   restarts a crashed controller, so close its window first.
3. Start the xjadeo fallback shortcut. After about three seconds the video is
   full screen on the stage display. While REAPER is stopped it shows the frame
   at the last timecode (the first frame of the video, if nothing has been
   received yet).
4. Move the mouse pointer to the laptop panel. Click the xjadeo window only if
   you need its keys.
5. From now on REAPER drives the picture. xjadeo has no blackout and no filler
   logic, so a blackout has to be done on the projector or LED processor.

Never run Aethra and xjadeo at the same time. Both need the MIDI port, and both
want to be on top on the stage display.

## 11. Limits compared with Aethra

| | Aethra | xjadeo fallback |
|---|---|---|
| Media | one file per song | one timeline video; every change to the set or to REAPER positions means rebuilding it |
| Between songs | filler logic (colour, image, loop, fades) | whatever is baked into the video |
| Start of a song | MIDI note, OSC or the GO key | follows MTC only, no triggers |
| REAPER stops | the clip continues (with MTC: or goes to the filler, `sync.mtc.on_stop`) | frozen frame |
| MTC lost | continues on the laptop clock | frozen frame |
| Locate while stopped | not needed | not followed (only with vVIMTC full-frame messages) |
| Timecode outside the video | — | black with a grey cross |
| Manual control | GO, next/previous, blackout, filler | offset keys only |
| Failures | crashed players replaced, controller restarted, black backdrop | none; Alt+F4 or a crash shows the desktop |
| MIDI port | by name, reconnects | by number, opened once at start |
| Log | event log, post-show report | none |
| Codec | chosen by benchmark (often hardware H.264/HEVC) | MJPEG, large files |

## 12. Troubleshooting

| Symptom | Check / fix |
|---|---|
| Black window with `[right-click]` | No file loaded: check `VIDEO` in the launcher. |
| "Indexing. Please wait." | The file is not all-intra. Wait, or transcode it to MJPEG (4.2). |
| "Index Error. File is not suitable." | Keyframes too far apart, or a damaged file. Transcode to MJPEG. |
| The picture does not move while REAPER plays | Wrong `MIDI_IN` (an output, or another port); Aethra or `aethra monitor` still holds the port; `-d portmidi` missing; REAPER sends no MTC. Quit xjadeo and check MTC with `.\Aethra.cmd monitor`. |
| The picture moves but shows the wrong place | The video was built for another REAPER layout or `--start`; REAPER's project start time or the generator's *Start time* was changed; `OFFSET`. |
| Grey cross on black | The timecode is before the start or after the end of the video, or the frame cannot be decoded. |
| The picture is one to three frames late | Increase `OFFSET` (keys `=` / `-` to find the value). |
| The window does not reach the stage display | `STAGE_X`/`STAGE_Y`, display scaling (section 7); use manual placement. |
| Tearing, or a black window that never shows video | List the outputs with `xjadeo.exe -x -1` and try another with `-x <n>`; update the GPU driver. |
| xjadeo's logo appears at start | `-S` missing. |
| Esc closed xjadeo | It was started without the launcher (`set override 385`). |

## References

- xjadeo website, download page and manual (man page of 0.8.15):
  <https://xjadeo.sourceforge.net/>
- xjadeo source code, including the Windows remote-control and PortMidi code:
  <https://github.com/x42/xjadeo>
- MTC with REAPER, loopMIDI and xjadeo on Windows (the issue that led to PortMidi
  default-device support in 0.8.8): <https://github.com/x42/xjadeo/issues/26>
- REAPER timecode generator: REAPER User Guide 7.81, section 23.7,
  <https://www.reaper.fm/userguide.php>
