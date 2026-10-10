# Control panel

`Aethra panel` on the desktop (or `.\Aethra.cmd gui`) opens the control panel:
a window for everything around the show — editing the show file, the checks and
measurements from [testing.md](testing.md), the codec choice, the Windows
preparation, starting and watching the show, and the logs after it.

It is a local web page in its own Microsoft Edge window (app mode, no address
bar). A small server inside Aethra serves it on `127.0.0.1` only; every request
carries a random per-session token, so no other page or computer can use it.
Nothing is loaded from the internet. Without Edge it opens in the default
browser; `aethra gui --no-window` only prints the address.

## Pages

| Page | What it does |
|---|---|
| Live | Start/stop the show, what is on stage (song, position, drift, players), GO / previous / next / blackout / filler / intro / outro / test overlay, the set list (click: select next, double-click: start), the event log. |
| Playlist | The cues: drag to reorder, add from the media folder, MIDI note with *learn* (play the note), offset, end behaviour, per-song transitions, OSC/program/MTC triggers; clip lengths. |
| Transitions | Global in/out transitions with an animated preview of every type. |
| Intro · filler · outro | The three backgrounds. |
| Stage & players, MIDI · OSC · keys, Sync & logging | Every other setting of the show file, with its default and an explanation. |
| Media | The clips with length, size, frame rate and codec; frame rate or size that differs from the stage is flagged. |
| Checks & rehearsal | `check`, `verify`, `selftest`, `rehearse`, the MIDI/OSC monitor, `devices` — with their output live. |
| Codec choice | `bench` on selected files and decoders; generating test clips. |
| Windows setup | `scripts/prepare-stage-laptop.ps1`: check (dry run), apply, administrator items, revert; desktop shortcuts and autostart. |
| Logs & reports | Every session's report and events. |
| Show file | The file as it is on disk; open another file. |

## The panel and the show on one laptop

The intended set-up: the projector is the laptop's **second, extended
screen** (`Win+P` > *Extend*, the laptop panel is the main display — see
[windows-setup.md](windows-setup.md), section 1), the show plays on the
projector, and the panel stays on the laptop's own screen, where it can be used
during the show.

* **Which screen gets the video.** With `stage.screen` left automatic it is the
  screen that is not the main display. If the projector is not connected (or
  not detected) when the show starts, the show **waits for it** — the Live page
  says so — instead of covering the laptop screen; *Stop* ends the waiting.
  When the projector is unplugged and plugged in again, the video goes back to
  the screen that is not the main display, whatever number Windows gives it.
  Only an explicit choice of the main display plays the video there (rehearsing
  without a projector).
* **Typing in the panel is safe.** The show's keys (Space = GO, arrows, `B`,
  `F`, `Esc` …) are global hotkeys: in any other program they run the show
  command instead of typing. While the panel has the focus, the keys that type
  (without Ctrl, Alt or Win) are released for it: typing in a field never
  touches the show, and outside the fields the panel itself runs the same keys
  (Space = GO). Keys with Ctrl/Alt (test overlay, quit) stay global. If the
  panel closes or hangs, the keys are global again after 2.5 s.
* **Nothing from the panel lands on the stage.** The stage windows never take
  the focus and hide the mouse pointer, so a pointer that wanders onto the
  projector is not seen. Tools that would put test pictures on the stage
  screen (codec benchmark, self-test on the stage screen) refuse to run during
  the show; tools that load the processor (full verify, test clips, reading
  all clips) ask first. The transition previews stand still during the show.
* The show's console window starts minimised when the panel starts the show
  (the panel keeps the focus; the console shows the same status).
* The show runs at a higher priority than the panel and the browser window, so
  the panel cannot slow it down.

## Good to know

* Changes are checked as you type (the badge at the top) and written with
  **Save** (`Ctrl+S`). A file with problems is only saved on request. The
  panel writes a tidy version of the file: comments are not kept, the
  previous version stays as `show.yaml.bak`.
* The show reads the file when it starts: save first, then start.
* The show runs in its usual console window with the usual restart loop; the
  panel only watches it (through a status file the show writes every second)
  and controls it **over OSC on 127.0.0.1** — keep `osc.enabled: true`. Closing
  the panel does not affect the show. Stopping from the panel is the same as
  pressing the quit key twice.
* MIDI *learn* and the MIDI monitor need the MIDI input, which the running show
  holds: stop the show first.
