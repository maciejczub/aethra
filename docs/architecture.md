# Architecture

Aethra plays one pre-rendered clip per song on a dedicated Windows laptop, in
sync with a backing track played by REAPER on another computer. Between songs a
filler (colour, still image or loop) is on stage; an optional intro opens the
show and an optional outro closes it. Songs come on stage and leave it with
transitions: cuts, cross-fades, fades through black or white, or blend modes.
The design goal is that the stage output never shows anything else: no
desktop, no dialog, no frozen picture that cannot be recovered.

```
 REAPER computer                         show laptop (Windows)
 ┌──────────────┐  MIDI note / OSC   ┌──────────────────────────────────────────┐
 │ backing track│ ─────────────────▶ │ Aethra controller (Python, asyncio)      │
 │ cue notes    │  MTC (optional)    │   MIDI (WinMM) · OSC (UDP) · hotkeys     │
 └──────────────┘ ─────────────────▶ │   show state machine · drift correction  │
     rtpMIDI over Ethernet or        │   event log · state file · backdrop      │
     USB MIDI interfaces             │        │ JSON IPC (named pipes)          │
                                     │ ┌──────┴───────────────────────────────┐ │
                                     │ │ compositor (separate process)        │ │
                                     │ │  layer F   layer A   layer B  libmpv │ │
                                     │ │  intro/    cue deck  cue deck        │ │
                                     │ │  filler/outro                        │ │
                                     │ │  └── OpenGL: transitions, blend ──┐  │ │
                                     │ └───────────────────────────────────┼──┘ │
                                     └─────────────────────────────────────┼────┘
                                                                           ▼ stage output
```

## Two display engines

The controller decides *what* is on stage; an engine puts it there.

* **Compositor** (`stage.engine: compositor`, the default). One process,
  `python -m aethra.compositor`, owns one borderless topmost window on the
  stage monitor. Every deck is a *layer*: a libmpv player rendering into its
  own OpenGL texture. Each frame the compositor draws the layers that are on
  stage into the window with a small GLSL shader, so a transition can show two
  pictures at once: cross-fades and 25 blend modes (multiply, screen, overlay,
  difference, hue, ...).
* **Window engine** (`stage.engine: windows`, also the automatic fallback).
  Every deck is an mpv process with its own full-screen window, and the
  controller stacks the windows. A window is either on top or not, so every
  transition between two pictures dips through black (white for `fadewhite`):
  the old picture fades out with an overlay, the new one is raised and fades
  in, half the time each.

If the compositor cannot start (no `libmpv-2.dll`, no OpenGL 3.3, the glfw
package missing), the error is logged and the show runs with the window
engine; `aethra selftest --windows` checks the compositor beforehand.

The controller talks to both through the same interface (`picture.py`):
*cut to X*, *transition to X with type T over D seconds*, *blackout*, and *is
deck X still part of the picture?* The last question matters: a deck that is
fading out must not be reloaded or paused.

## Components

| Module | Role |
|---|---|
| `show.py` | The state machine: triggers, preloading, transitions and their timing, failure handling, state file. |
| `picture.py` | What is on stage: `CompositorPicture` (commands to the compositor) and `WindowPicture` (window stacking and overlays). |
| `deck.py` | One player (compositor layer or mpv process): load/play/seek, state from IPC events, health. |
| `compositor.py` | The compositor process: GLFW window, libmpv layers, render loop, control server. |
| `scene.py`, `blend.py` | The picture model shared by controller and compositor; transitions and blend formulas (GLSL and a Python reference for the tests). |
| `libmpv.py`, `gl.py` | ctypes bindings: libmpv's render API and the few OpenGL functions the compositor uses. |
| `stage.py` | The black backdrop window and window stacking (Win32), the compositor process supervisor. |
| `ipc.py` | Asynchronous mpv JSON IPC client (overlapped named pipes on Windows, Unix sockets elsewhere); also used for the compositor's control channel. |
| `sync.py` | Drift correction against the trigger clock or MTC. |
| `mtc.py` | MIDI Time Code decoder. |
| `midi.py` | MIDI input: WinMM (ctypes, Windows) or python-rtmidi; reconnects automatically. |
| `osc.py` | OSC 1.0 parser and UDP server. |
| `keyboard.py`, `keys.py` | Global hotkeys (Windows), console keys, key names. |
| `config.py` | Strictly validated YAML/JSON configuration. |
| `eventlog.py`, `report.py` | JSON Lines event log and the post-show report. |
| `media.py`, `bench.py` | Pre-show decode check and codec benchmark. |
| `platform.py`, `winapi.py` | Process setup (error mode, timer resolution, priority, keep-awake), Win32 declarations. |

Runtime dependencies: Python ≥ 3.11, PyYAML; for the compositor also the
glfw package (with its `glfw3.dll`) and libmpv. The Windows bundle contains all
of them. Nothing needs the internet at run time.

## Decks

Three players run for the whole show:

* **F** is the background deck: it plays the intro, the filler or the outro
  (loop, still image, or a solid colour generated by libavfilter:
  `av://lavfi:color=...`). While a cue plays, F loads - out of sight - what
  comes after it (the filler, or the outro after the last cue), so the end of
  a song never waits for a file to load. F only plays while it is part of the
  picture.
* **A** and **B** take turns: while one plays the current cue, the other has
  the next cue loaded and **paused on its first frame** (or on the frame that
  belongs to the trigger when `offset` is positive).

A trigger therefore only sends `set pause no` to the waiting deck and starts
its in-transition: the command leaves the controller well under a millisecond
after the MIDI message arrived. With a cut (the default for songs) the first
frame is on screen at the next vertical refresh.

Every deck is driven over mpv's JSON IPC, also as a compositor layer: libmpv
runs the same IPC server (`input-ipc-server`), so the deck code is the same for
both engines; only creating and killing a player differs.

## Transitions

Every cue has an in-transition (`xfade_in_*`) and an out-transition
(`xfade_out_*`), from the global `transitions` section or the cue's overrides.

* **In** - starts at the moment the cue goes live (the trigger, or `t0 - p0`
  for a negative offset). Whatever was on stage - intro, filler, outro or the
  previous song - is the other picture. The clip runs in sync from its first
  frame; the transition only decides how much of it is seen.
* **Out** - starts `xfade_out_duration` seconds before the clip ends (computed
  from the deck's position reports, scheduled with ~1 ms accuracy), so the
  background - or black for `on_end: black` - has fully taken over when the
  last frame is reached. The clip keeps playing underneath. Operator commands
  (`filler`, `intro`, `outro`) and playback errors use the same transition,
  starting at once.
* Between two backgrounds (e.g. the operator calls the outro while the filler
  is on stage) the single background deck has to load other content, so that
  change goes through black, half of `transitions.xfade_out_duration` each way.

A transition can start while another one is running (a song triggered during
the previous song's out-transition): the compositor stacks them, and the deck
that leaves is released only when it can no longer be seen. If both cue decks
are still on stage when a third, not preloaded, cue is triggered, the running
transition is completed at once to free a deck.

### Blend-mode transitions

A transition from picture X to picture Y with a blend mode *M* (formulas from
the W3C Compositing and Blending specification) runs in two halves: during the
first, `M(X, Y)` is mixed over X with rising strength; during the second, Y
alone is mixed over that result. Halfway through, the picture is exactly
`M(X, Y)`; at the end it is Y. `fadeblack`/`fadewhite` dip through a colour
the same way. All progress values are eased (smoothstep).

## The compositor

```
render loop (one thread, vsync):
  window events → control commands → for each layer with a new frame:
      make the layer's GL context current, render the frame into its texture,
      fence → composite: base layer, then each running transition's entries,
      then the blackout overlay, ping-ponging between two frame buffers
      → draw to the window → swap → tell every layer when the frame was shown
```

* **Layers.** Each layer has its own OpenGL context that shares textures with
  the window's context (libmpv expects to find its context as it left it; GPU
  fences order the work between contexts). Layers use mpv's single-pass
  renderer (`gpu-dumb-mode`): clips are made at the stage size, so mpv's
  multi-pass path would only cost GPU time - and in libmpv 0.40 it sometimes
  rendered a layer black for good.
* **Control channel.** The compositor serves mpv's JSON IPC protocol on its own
  pipe (`\\.\pipe\aethra-main-compositor`): `create_layer`, `show`,
  `transition` (with the start time on the shared `perf_counter` clock),
  `blackout`, `status`, `screenshot`, `ping`, `quit`. It reports `layer_down`
  and keys pressed in its window.
* **The controller mirrors the scene** (`scene.py`) - base layer plus running
  transitions - to know which decks are still part of the picture, and sends
  the whole scene again after a compositor restart.
* **Supervision.** The controller pings the compositor twice a second; no
  answer for ~2.5 s (a hung render loop or driver) or a closed connection means
  it is killed and restarted, and so are its layers. The backdrop window under
  it keeps the stage black meanwhile (about one second on the test machine),
  then the background comes back and a cue that was playing rejoins at its
  expected position.

## Window stacking

On Windows the controller owns a black, topmost **backdrop** window that
covers the stage monitor for the whole session. With the compositor engine the
order is simply: compositor window, backdrop. With the window engine the decks'
windows are kept in this order (top to bottom):

```
visible deck  ─ what the audience sees
filler deck F ─ instantly visible if the visible cue deck dies
backdrop      ─ black, instantly visible if the filler deck dies too
hidden decks  ─ the preloaded next cue, never visible by accident
```

During a blackout (window engine) the backdrop is moved to the top; the
compositor draws its blackout itself. Rules that keep the stacking safe:

* a window becomes topmost only by being raised to the very top in one call,
  or by being inserted directly below a topmost window, so nothing can ever
  jump above the current picture;
* the windows belong to different threads, so the new top window is raised
  first and the rest is arranged only once it is really on top (otherwise the
  backdrop could briefly come out on top: a black flash);
* all calls use `SWP_ASYNCWINDOWPOS`, so a hung player cannot block the
  controller;
* the order (and the topmost flag of the top window) is verified 80 ms after
  every change and four times a second, and repaired if anything changed it:
  mpv itself re-stacks its window with `HWND_NOTOPMOST` when it reconfigures
  the video, Windows minimises every window on Win+D/Win+M;
* the backdrop is created hidden and appears directly at its place in the
  order, so a restarted controller does not cover a picture that is still on
  stage.

On Linux (development) the window engine toggles mpv's `ontop` property and
blackout uses an opaque OSD overlay; the compositor works on X11 as it does on
Windows.

## Timing model

A trigger arriving at host time `t0` means: *clip position `p0 = offset +
latency_compensation` belongs to `t0`*. All timestamps come from
`time.perf_counter` (QueryPerformanceCounter on Windows, shared by all
processes), taken on the MIDI thread the moment the message is delivered.

* **Preloaded cue** – the deck waits at `max(0, p0)`. If `p0 ≥ 0` it is
  released immediately; if `p0 < 0` (the note comes before the clip should
  start) it is released at `t0 - p0`, scheduled with ~1 ms accuracy.
* **Cold start** (a cue that was not preloaded: out-of-order trigger, crash
  recovery, restart) – the deck is loaded paused at `p0 + (now - t0) + lead`,
  i.e. slightly *ahead* of where the clip will be when loading finishes, and is
  released exactly when real time catches up. The `lead` adapts to measured load
  times; if a load is slower than expected the deck re-seeks (up to twice)
  before giving up and starting late. The picture stays on the filler or the
  previous cue until the new cue is ready, and the first visible frame is in
  sync. A cue that rejoins after a crash comes back with a cut.
* **While playing** – the corrector compares mpv's `time-pos` with the expected
  position. The reference is the laptop clock since `t0`, or MTC when enabled
  and running (anchored at the trigger, or at the cue's `mtc_start`). The
  median drift over ~2 s is corrected by nudging playback speed by at most
  `max_speed_change` (1 % by default, invisible) until it is under `target`. A
  drift above `hard_seek` that persists for half a second (REAPER relocated, a
  stall) is fixed with one exact seek. Up to one frame of constant bias measured
  right after the start is treated as zero (mpv reports a frame's position a
  little before it is displayed).

Without MTC the laptop's crystal and the audio interface's clock still differ
by a few tens of ppm: about 20 ms over a ten-minute song, under one frame at 25
fps. MTC makes long clips and relocations safe.

## Failure handling

| Failure | What the audience sees | What Aethra does |
|---|---|---|
| Media file missing or unreadable | The filler (or the previous cue) stays | Logs `cue_failed`, retries the preload later; `aethra check`/`verify` find it before the show. |
| Decoder error in the middle of a clip | The clip's out-transition to the filler (from black, or from its last frame) | Same path as a normal end of clip. |
| A player crashes or hangs during a cue (mpv process, or a layer that stops answering for 3 s or stalls for `stall_timeout`) | Instant cut to the filler | The player is killed and restarted; the cue rejoins at its expected position on the other cue deck, in sync. Window engine: the players run in a Windows job that ends a crashing process at once (mpv resets the inherited error mode, so a crash would otherwise go through Windows Error Reporting with a frozen picture). |
| The compositor crashes or hangs | Black (the backdrop) for about a second, then the filler; a running cue rejoins | The compositor runs in the same crash job; it is restarted, the scene is sent again, the decks are recreated. |
| Filler deck dies | Black | Restarted and reloaded; a filler file that fails falls back to the filler colour. |
| Controller crashes | Nothing changes: the picture goes on | `Aethra.cmd` restarts it; it reconnects to the compositor (or the mpv decks), reads the state file and continues the running cue (also a held last frame or a black end). Closing the console window does the same on purpose: the players stay. |
| A click into the console window, or Ctrl+C | Nothing | QuickEdit is turned off and all console output goes through its own thread, so the console can never block the controller. Ctrl+C is read as a key (quit needs two presses), so `cmd.exe` never stops the restart loop. |
| Controller restarted after the players died too | Black, then filler | The running cue is rejoined at its expected position (wall clock). |
| MIDI cable/network unplugged | Nothing (the clip runs on its own clock) | The input is reopened when the port reappears; rtpMIDI sessions reconnect by themselves. |
| Stage display disconnected/reconnected | Depends on Windows | When the stage monitor is back, the backdrop and the stage windows are moved onto it again; an explicitly configured stage screen that is missing at start is waited for. See docs/testing.md for what to verify. |
| Win+D / Win+M on the laptop | The stage windows come back within ~0.25 s | Minimised stage windows are restored without activation. |
| Windows error dialogs | None | `SetErrorMode` (inherited by the players) suppresses crash and critical-error boxes; also disable WER UI (docs/windows-setup.md). |

The operator can always force the picture with the keyboard: `B`/`Esc`
(blackout), `F` (filler), `Space` (GO: start the selected next cue).

## Restart and state

The controller writes a small state file (`%LOCALAPPDATA%\Aethra\main-state.json`)
on every change: selected next cue, blackout, and the live cue with its trigger
time. The compositor and its layers (or the mpv decks) are left running when
the controller dies; on start it first tries to reconnect to
`\\.\pipe\aethra-main-compositor` and `\\.\pipe\aethra-main-F|A|B`.
`aethra run --fresh` ignores both and starts clean.

## Why separate processes

The architecture decision (see the project brief) is mpv controlled over IPC.
Separate processes isolate failures: a crash in a decoder or a graphics driver
cannot take the controller with it, and the controller - which also owns the
backdrop - turns it into a short black instead of a desktop on stage. The
compositor puts the three players into one process to compose them on the
GPU; the window engine keeps one process per deck and remains available as
the simpler alternative.
