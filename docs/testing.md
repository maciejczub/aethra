# Testing

Three layers:

1. **Automated tests** (every change): unit tests plus integration tests that
   drive a real mpv and the compositor. They run on Linux in a container.
2. **Show-laptop validation** (once per laptop / mpv / Windows update): the
   Windows-specific parts that cannot run in CI.
3. **Rehearsal tests** (before a tour or a new set): the five tests from the
   project brief, with the real REAPER rig.

Write down the results (date, laptop, mpv version, findings). `aethra report`
turns the event log of each run into a table you can paste.

## 1. Automated tests

```bash
docker compose -f docker/compose.yaml run --rm test                       # everything
docker compose -f docker/compose.yaml run --rm test python -m pytest -m mpv -v   # only the real-mpv tests
docker compose -f docker/compose.yaml run --rm test python -m pytest -m compositor -v   # only the compositor
```

The container runs every command on a virtual X display (Xvfb) with Mesa's
software OpenGL, so the compositor runs there as it does on Windows. Without
Docker: `pip install -e .[dev] glfw` and `python -m pytest` (the mpv tests are
skipped unless `mpv` and `ffmpeg` are on PATH, the compositor tests unless
libmpv, glfw and a display are available).

What the integration tests cover with a real mpv (`--vo=null`, window engine):
preloaded trigger latency and sync, end of clip → filler, cold start released
in sync, broken file never reaching the stage, a killed mpv being replaced and
the cue rejoined in sync, a controller restart continuing the running cue, OSD
fades and blackout.

With the compositor: every transition type and blend mode at several points of
its progress, pixel by pixel against the Python reference of the formulas;
layers upright, cuts, blackout, stacked transitions, a player quitting; a show
cross-fading into a song and out of it before the clip ends; a killed
compositor being restarted with its layers and the filler coming back.

## 2. Show-laptop validation (Windows)

Prepare: `aethra check show.yaml` must say OK, and the laptop is set up as in
[windows-setup.md](windows-setup.md). Generate test material on any computer
with ffmpeg:

```bash
python tools/make_test_clips.py demo --out media          # clips for the example show
python tools/make_test_clips.py codecs --out test-clips    # codec candidates
python tools/make_test_clips.py sync --out test-clips      # latency/sync kit
```

Start with the automatic measurements:

```bat
.\Aethra.cmd selftest --windows
```

It reports the IPC round trip over the named pipe, the timer accuracy, whether
mpv's playback rate matches the system clock (`playback rate` must be 1.000),
dropped frames, the video output and refresh rate, global hotkey registration,
whether the window stacking (backdrop/deck) can be controlled, and - with the
compositor engine - whether the compositor starts (OpenGL renderer, libmpv)
and plays a test picture at full frame rate without hitches. Any `FAIL` must be
understood before going further (a failing compositor means the show would run
with the window engine).

In Task Manager the compositor is the `python.exe` whose command line contains
`aethra.compositor`; the controller is the other one. With the window engine the
players are `mpv.exe` processes.

| # | Check | How | Expected |
|---|---|---|---|
| V1 | Stage output | `aethra devices`; start the show | Video on the stage screen only; console on the laptop panel; no cursor over the stage. |
| V2 | Deck switching | Test mode on (`Ctrl+T`), trigger cues back to back (`Space`), 30 times | No black or desktop flash between cues; film it at 120/240 fps if in doubt. |
| V3 | Backdrop | Kill the compositor (window engine: all `mpv.exe`) in Task Manager | Black (not the desktop) within a moment, then the filler returns. |
| V4 | Crash during a cue | Kill the compositor (window engine: the playing `mpv.exe`) | Compositor: about a second of black, then the song comes back in sync. Window engine: cut to filler, the song comes back in sync within ~1 s. No error dialog. |
| V5 | Hang | `Suspend` the compositor (window engine: the playing mpv) in Resource Monitor / Process Explorer | The frozen picture is replaced within ~3 s (compositor restarted, or filler after `stall_timeout`), song rejoined. |
| V6 | Controller crash | Close the Python process in Task Manager (not the window) | `Aethra.cmd` restarts it, the picture does not change, the log shows `cue_restored`. |
| V7 | Global keys | Focus another window (e.g. Notepad), press `B`, `B`, `F`, `Space` | Blackout on/off, filler, GO work regardless of focus. |
| V8 | Alt+F4 on stage | Click the stage window, press Alt+F4 | Compositor: nothing happens. Window engine: the deck restarts; picture recovers (filler or the song). |
| V9 | Display hot-plug | Unplug and replug the projector cable during a song | Note what Windows does with the windows; after replug the stage must show video or the backdrop, never the desktop. Repeat with the projector powered off/on. |
| V10 | Notifications | Trigger a toast (e.g. plug a USB stick) | Nothing on the stage output. |
| V11 | MIDI port | `aethra monitor`, play the REAPER cue track | Notes on channel 16 and, if used, `MTC running`. |
| V12 | Show desktop | Press `Win+D`, then `Win+M` | The stage windows come back within about half a second (log: `stage windows were minimised`). |
| V13 | Transitions | Test mode on; give a few cues different `xfade_in_type`/`xfade_out_type`; trigger them in and out of the filler and back to back | Every transition runs smoothly at the display rate; the out-transition is over when the clip's last frame (timecode in the clip) is reached; `dropped vo 0` in the overlay. |

V2 and V9 are the two items that depend most on the particular laptop, GPU
driver and Windows build — do not skip them.

The frame counter in mpv's test overlay can be one frame behind the picture
(it is drawn from the position mpv reports). For measurements, use clips with
burned-in numbers (`tools/make_test_clips.py`) and read those.

## 3. Rehearsal tests (from the project brief)

Run with the real REAPER project, the final media and the final cabling. Keep
test mode off except where noted, so you test what the audience sees.

### Test 1 — the full set twice without a restart

Play the whole set two times in a row without restarting Aethra or the laptop.

Pass: every cue started by its trigger, `aethra report` shows no problems, no
dropped frames (`drop` = 0) and `max drift` below one frame (40 ms) for every cue.

Without REAPER (for example as an overnight soak test of the laptop) enable OSC
in the show file, start the show, and in a second console run
`.\Aethra.cmd rehearse --loops 2`: it triggers every cue in order, waits for each
clip to end plus a gap, and repeats the set.

### Test 2 — unplug and replug MIDI / network during a song

During a song: pull the MIDI cable (or the Ethernet cable for rtpMIDI), wait
10 s, plug it back. Then let the next cue trigger.

Pass: the running clip continues unaffected; the log shows `midi_lost` /
`midi_open` (USB) or nothing (rtpMIDI keeps its port); the next trigger works.
With MTC enabled the status shows `MTC none` while unplugged and the clip runs
on the laptop clock, then `MTC running` again.

### Test 3 — missing or damaged file

Rename one clip, and replace another with a damaged copy (truncate a copy of a
clip to half its size). `aethra check` / `aethra verify` must report both. Then
run the set.

Pass: when those cues are triggered the stage stays on the filler (or the
previous cue) — no error message, no frozen frame; the next cues play normally.

### Test 4 — restart in the middle of the set

During a song: (a) close the Aethra console window, wait 10 s and start Aethra
again from the shortcut; (b) end the Python process in Task Manager (the
launcher restarts it); (c) end *every* `mpv.exe` and the Python process at
once (simulates a full crash), then start Aethra from the shortcut.

Pass: (a) and (b) the picture is not interrupted (the console window only
detaches from the running players; the restarted controller takes them over —
log `cue_restored`); (c) black, then the filler, then the song is rejoined at
its expected position (log `cue_resume`). For (c) end both Aethra `python.exe`
processes (controller and compositor) — with the window engine every `mpv.exe`
and the Python process. In every case the next trigger works
and the selected next cue is still correct. (A deliberate quit with
`Ctrl+Shift+Q` is a clean stop: the players close and the song is not
rejoined on the next start.)

### Test 5 — latency and drift measurement

*Latency trigger → first frame.* Use the sync kit:

1. Add `test-clips/sync-flash-25.mp4` as a cue (e.g. `id: sync`, a free note).
2. In REAPER put `sync-click.wav` on a track starting exactly at that cue's
   trigger note.
3. Play it and film the screen *and* listen to the speakers with a phone in
   slow motion (120 or 240 fps). Every full second the screen shows a white frame
   and the speakers click.
4. Count the slow-motion frames between the click (audio waveform in a video
   editor) and the white frame. Positive = picture late.
5. Set `sync.latency_compensation` to the measured delay in seconds (e.g.
   `0.060`) and repeat until the difference is below one frame.

The measured delay includes MIDI transport, the projector/LED processor and the
display; the controller's own share is logged as `trigger_to_play_ms` (normally
< 1 ms for a preloaded cue).

*Drift at the end of the longest song.* Turn test mode on (`aethra run
--test-mode`): the overlay shows the clip timecode and frame number. At the end
of the longest song, film the overlay together with REAPER's timecode display
(or put a click at the last full second, as above). `aethra report` also lists
`max drift` per cue as measured by the controller. Pass: under one frame.

## After every show

```bash
aethra report logs          # newest log
```

Keep the `logs` folder: the event log has microsecond timestamps of every
trigger, start, correction and failure, and the per-deck mpv logs.
