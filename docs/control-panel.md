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
