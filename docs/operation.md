# Running a show

## Before the show

1. Laptop prepared as in [windows-setup.md](windows-setup.md): plugged in,
   Do Not Disturb on, Wi-Fi off, Ethernet to the REAPER computer up.
2. Stage output connected and showing the desktop extension (not a mirror).
3. In the Aethra folder:

   ```bat
   .\Aethra.cmd check
   .\Aethra.cmd verify --quick
   ```

   `check` must end with `OK`: configuration, every media file, the MIDI port
   and the stage screen. `verify` decodes the start and end of every clip
   (without `--quick`: every frame — do that once after copying new media).
4. Start the show: double-click the **Aethra** shortcut (or `Aethra.cmd`).
   The stage shows black, then the intro (or, without one, the filler) fades
   in; the first cue is already preloaded. The console shows:

   ```
   20:41:07.112    show_ready     show 'Tour 2026': 14 cues, next 01 'Opening'
   FILLER | next 01 ready | MIDI ok | OSC udp/9000
   ```

5. In REAPER, play a few seconds of the first song (or use `aethra monitor`
   before starting the show) to see the trigger arrive, then go back to the
   filler with `F`.

## The console

The last line is the status, refreshed every second:

```
CUE 03 00:02:14:07/00:04:51:00 [B] | next 04 ready | MIDI ok | MTC running 00:31:02:11 | drift +3ms/mtc
```

| Part | Meaning |
|---|---|
| `INTRO` / `FILLER` / `OUTRO` / `CUE id pos/duration [deck]` / `HOLD` / `BLACK` | What is on stage. |
| `next 04 ready` | The cue `Space` (GO) would start, and whether it is preloaded. `FAILED` = its file cannot be played. |
| `MIDI ok` / `waiting` / `lost` | The MIDI input. `waiting`: the port is not there (cable, rtpMIDI session). |
| `MTC running/stopped/none` | Only with `sync.mtc.enabled`. |
| `drift +3ms/clock` | Measured drift of the playing clip, and the reference (laptop clock or MTC). |
| `DOWN A` | A video player is being restarted. |
| `BLACKOUT` | Blackout is on. |

Above it, every event is printed with its time; the same events go to
`logs/aethra-<date>-<time>.jsonl`.

## Keys

The keys work whatever window has the focus (global hotkeys). While Aethra
runs they are reserved for it: typing a space or a `b` in another program will
not work.

| Key | Action |
|---|---|
| `Space` | **GO**: start the selected next cue now (when a trigger did not come). |
| `→` / `↓` | Select the next cue (without starting it) — e.g. a song was dropped. |
| `←` / `↑` | Select the previous cue. |
| `B` or `Esc` | **Blackout** on/off. Cues keep running underneath and stay in sync. |
| `F` | The filler now (a song on stage leaves with its out-transition). |
| `Ctrl+T` | Test overlay on/off (timecode, frame number, drift). |
| `Ctrl+Shift+Q` twice | Quit (also `Ctrl+C` twice in the console). |

All keys can be changed in `show.yaml` (`keyboard.keys`).

## During the show

Normally nothing: the intro plays until the first trigger, every song is
started by its trigger (with its in-transition), the out-transition to the
filler starts shortly before the clip ends (after the last song: to the outro,
if configured), and the next clip is already waiting.
The intro and outro can also be called at any time through command notes, OSC
(`/aethra/intro`, `/aethra/outro`) or keys bound in `keyboard.keys`.

| Situation | What to do |
|---|---|
| A song started but the video did not | `Space` (GO). The video starts from the beginning *now*; if the trigger came late, accept the offset or skip the video for this song (`F`). |
| The band skips a song | Nothing if REAPER sends that song's trigger (the right cue starts). Without triggers: `→` to select the right cue, then `Space`. |
| Something wrong on screen | `B` (blackout) at once; sort it out; `B` again. |
| Wrong cue on stage | `F` (filler) or `B`. |
| `MIDI waiting`/`lost` | Check the cable/session; triggers work again as soon as the port is back. Use `Space` meanwhile. |
| A video player crashed (`DOWN`) | Nothing: the filler covered it and the song is rejoined automatically. |
| The whole picture went black for a second | The compositor was restarted (logged as `compositor_down`); the filler comes back and a running song rejoins by itself. |
| The console window was closed | The picture keeps running (the players are left alone on purpose). Start the Aethra shortcut again: it takes over the running song. |

## After the show

```bat
.\Aethra.cmd report
```

Prints every cue with its trigger source, start latency, dropped frames and
measured drift, and every problem. Keep the `logs` folder.

## Quitting

`Ctrl+Shift+Q` twice (or `Ctrl+C` twice in the console). The video players are
closed and the stage goes to the desktop — do this after the stage output is
off or switched away.

Closing the console window does **not** stop the picture: so that an accidental
click cannot black out a show, the video players are left running. To get rid of
them, start Aethra again and quit with the keys.
