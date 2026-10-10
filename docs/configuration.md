# Configuration reference

One file per show: `show.yaml` (YAML) or `show.json` (JSON). Start from
[`examples/show.example.yaml`](../examples/show.example.yaml), which is
commented line by line. Validate with `aethra check show.yaml`: unknown keys and
wrong types are errors (Aethra refuses to start), missing media files are
warnings (the show starts; those cues stay on the filler).

Relative paths are relative to the configuration file; media paths are
relative to `show.media_dir`. Durations are in seconds.

## `show`

| Key | Default | |
|---|---|---|
| `name` | file name | Shown in the console and logs. |
| `media_dir` | `.` | Folder with the clips. |

## `stage`

| Key | Default | |
|---|---|---|
| `screen` | first screen that is not the main display | Index from `aethra devices`. |
| `screen_name` | – | Windows device name instead of the index, e.g. `\\.\DISPLAY2`. |
| `width`, `height` | `1920`, `1080` | Stage resolution (overlays, filler colour, backdrop fallback). |
| `fps` | `25` | Master frame rate (timecode display, "one frame" thresholds). |

## `mpv`

| Key | Default | |
|---|---|---|
| `path` | `mpv` | `mpv/mpv.exe` in the bundle; a bare name is searched on PATH. |
| `hwdec` | `auto-safe` | Decoder; choose with `aethra bench` ([codec-selection.md](codec-selection.md)). |
| `vo` | mpv default | Video output, e.g. `gpu-next` or `gpu`. |
| `priority` | `above_normal` | Windows priority of the players: `normal`, `above_normal`, `high`. |
| `extra_args` | `[]` | Extra mpv options, e.g. `["--d3d11-flip=no"]`. |
| `startup_timeout` | `15` | Seconds to wait for a player to come up. |

Aethra starts mpv with `--no-config` and a fixed set of options (no on-screen
controller, no default key bindings, no audio, no subtitles, keep-open,
full screen on the stage screen, cursor hidden); options the installed mpv does
not know are left out automatically.

## `filler`

| Key | Default | |
|---|---|---|
| `mode` | `color` | `color`, `image` (still) or `loop` (video loop). |
| `file` | – | For `image`/`loop`. If missing or broken, `color` is used. |
| `color` | `#000000` | `#RRGGBB`. |

## `transitions`

| Key | Default | |
|---|---|---|
| `fade_out` | `0.5` | End of a clip (or `F`) → black. |
| `fade_in` | `0.5` | Black → filler. |

Cues start with a cut unless the cue has `fade_in`.

## `sync`

| Key | Default | |
|---|---|---|
| `latency_compensation` | `0.0` | Seconds the picture is shown *earlier* to make up for MIDI and display latency; measure it ([testing.md](testing.md), test 5). |
| `retrigger_guard` | `1.0` | The same cue triggered again within this time is ignored (duplicate notes). |
| `resume_after_crash` | `true` | Rejoin a cue at its expected position after a player or controller crash. |
| `stall_timeout` | `2.0` | No new frame for this long while playing = stalled player (it is replaced). |

### `sync.correction`

Keeps a playing clip locked to its trigger. The reference is the laptop clock
(time since the trigger), or MTC when `sync.mtc.enabled` and MTC is running.

| Key | Default | |
|---|---|---|
| `enabled` | `true` | |
| `threshold` | `0.040` | Start correcting above this drift (1 frame). |
| `target` | `0.010` | Stop correcting below this drift. |
| `max_speed_change` | `0.01` | Largest speed change (1 % is invisible). |
| `correction_time` | `4.0` | How fast the drift is removed (speed change = drift / correction_time). |
| `hard_seek` | `1.0` | Drift (s) that is fixed with one exact seek instead; `null` = never seek. |

### `sync.mtc`

| Key | Default | |
|---|---|---|
| `enabled` | `false` | Use MIDI Time Code from the MIDI input as the reference. |
| `on_stop` | `continue` | When MTC stops during a cue: `continue` (laptop clock) or `filler`. |
| `timeout` | `0.5` | No MTC for this long = MTC lost. |

## `midi`

| Key | Default | |
|---|---|---|
| `enabled` | `true` | |
| `port` | *(required)* | Part of the input name (case-insensitive), or `*` for the first input. |
| `channel` | `any` | `1`–`16` or `any`. |
| `backend` | `auto` | `winmm` (Windows), `rtmidi` (needs python-rtmidi). |
| `command_notes` | `{}` | Notes on the same channel for commands: `go`, `next`, `prev`, `black`, `filler`, `test`. |
| `reconnect_interval` | `2.0` | How often the port list is checked. |

## `osc`

| Key | Default | |
|---|---|---|
| `enabled` | `false` | |
| `bind`, `port` | `0.0.0.0`, `9000` | UDP address to listen on. |
| `prefix` | `/aethra` | Built-in addresses below. |
| `require_playing` | `false` | Per-cue OSC triggers only while REAPER reports `/play 1`. |

Built-in addresses: `/aethra/cue <id or 1-based number>`, `/aethra/go`,
`/aethra/next`, `/aethra/prev`, `/aethra/filler`, `/aethra/black [1|0]`
(no argument toggles), `/aethra/test [1|0]`. A float/int argument below 0.5 on
`go`/`next`/`prev`/`filler` is ignored (button release from control surfaces).

## `keyboard`

| Key | Default | |
|---|---|---|
| `global_hotkeys` | `true` | Windows: keys work regardless of focus. |
| `console` | `true` | Keys typed in the console. |
| `stage_window` | `true` | Keys in the stage windows. |
| `keys` | see [operation.md](operation.md) | `command: key` or `command: [key, key]`. |

Key names: `a`–`z`, `0`–`9`, `f1`–`f24`, `space`, `enter`, `escape`, `tab`,
`backspace`, `left`, `right`, `up`, `down`, `pageup`, `pagedown`, `home`,
`end`, `insert`, `delete`, `numpad0`–`numpad9`; modifiers `ctrl+`, `alt+`,
`shift+`, `win+`. Commands: `go`, `next`, `prev`, `black`, `filler`, `test`,
`quit` (needs two presses within 3 s).

## `logging`

| Key | Default | |
|---|---|---|
| `dir` | `logs` | Event logs and mpv logs. |
| `mpv_logs` | `true` | One verbose log per player per session. |
| `status_interval` | `1.0` | Console status refresh. |

## `test_mode`

`false` by default. `true` (or `aethra run --test-mode`, or `Ctrl+T`) shows an
overlay on the stage: clip name, time, frame number, dropped frames, the last
trigger, MTC and drift.

## `cues`

A list in set order (`next`/`prev`/GO follow it).

| Key | Default | |
|---|---|---|
| `id` | *(required)* | Unique, no spaces; used in logs and `/aethra/cue`. |
| `title` | `""` | |
| `file` | *(required)* | Relative to `show.media_dir`. |
| `midi_note` | – | Note-on (velocity > 0) on `midi.channel` that triggers the cue. |
| `midi_program` | – | Or a program change. |
| `osc` | – | Extra OSC trigger: `"/address"` or `{address: "/addr", arg: "value"}`. |
| `offset` | `0.0` | Clip position (s) that coincides with the trigger: `2.0` = the clip has 2 s before the trigger point; `-1.0` = the trigger comes 1 s before the clip starts. |
| `on_end` | `filler` | At the end of the clip: `filler`, `hold` (keep the last frame), `black`. |
| `fade_in` | `0.0` | Fade in from black instead of a cut. |
| `mtc_start` | – | REAPER timecode of the trigger (`HH:MM:SS:FF`); without it the MTC position at the trigger is used. |
