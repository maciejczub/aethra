# Configuring REAPER for Aethra

This guide covers the REAPER side of an Aethra show: the cable between the two
computers, the track that sends the cues, where to put the cue notes, MIDI Time
Code, OSC, and what to check on show day. The Aethra side is described in
[configuration.md](configuration.md) and [operation.md](operation.md).

> Menu and option names were checked against REAPER 7.82 (October 2026) and the
> REAPER User Guide 7.81. In REAPER 7.19 and older, *Preferences > Audio >
> MIDI Inputs* and *MIDI Outputs* are a single page called *MIDI Devices*. On
> macOS, *Options > Preferences* is *REAPER > Settings*. Where something could
> not be verified, the text says **verify on your system**.

**In short**

1. Connect the two computers with an Ethernet cable and rtpMIDI (or two USB
   MIDI interfaces and a MIDI cable).
2. Enable that port as a MIDI output in REAPER.
3. On a track called `VIDEO CUES`, put one short note on MIDI channel 16 at the
   start of every song, routed to that port. The note number selects the cue
   (`midi_note` in `show.yaml`).
4. Optionally add a timecode generator item that sends 25 fps MTC to the same
   port. The xjadeo fallback needs it.
5. Measure the latency once and enter it in Aethra.

MIDI notes are the recommended trigger. OSC from REAPER works, but it is less
precise and has more pitfalls (section 7).

## 1. Overview

```
 REAPER computer  192.168.77.1                        show laptop  192.168.77.2
┌────────────────────────────────────┐               ┌──────────────────────────────────┐
│ backing tracks ─▶ audio interface ─┼─▶ PA          │                                  │
│                                    │               │                                  │
│ VIDEO CUES track (notes, ch. 16) ─┐│               │                                  │
│ MTC track (generator item) ───────┤│   Ethernet    │                                  │
│                     rtpMIDI ◀─────┘│ direct cable  │                                  │
│                     AETHRA-REAPER ═╪═══════════════╪═▶ AETHRA-STAGE ─▶ Aethra ─▶ mpv ─┼─▶ projector
│ OSC control surface (optional) ────┼─── UDP 9000 ──┼─────────────────────┘            │
└────────────────────────────────────┘               └──────────────────────────────────┘

 alternative to Ethernet + rtpMIDI:
 USB MIDI interface ── 5-pin MIDI cable ──▶ USB MIDI interface
```

`AETHRA-REAPER` and `AETHRA-STAGE` are the rtpMIDI session names used in this
guide (2.3); each is the MIDI port name on its computer.

| Message | Sent by | Aethra uses it for | Needed |
|---|---|---|---|
| Note-on, channel 16 | `VIDEO CUES` track | starting a cue (`midi_note`) | yes (recommended trigger) |
| Program Change | `VIDEO CUES` track | starting a cue (`midi_program`) | alternative to notes |
| Command notes | `VIDEO CUES` track | `go`, `next`, `prev`, `black`, `filler`, `test` (`midi.command_notes`) | optional |
| MTC (quarter-frame and full-frame) | `MTC` track | drift reference (`sync.mtc`); the [xjadeo fallback](xjadeo-fallback.md) | recommended |
| OSC `/play`, `/lastmarker/name` | REAPER OSC control surface | secondary trigger (`osc:` per cue) | optional |

## 2. The connection

Never use Wi-Fi for the show connection.

### 2.1 Choose the transport

| | rtpMIDI over Ethernet (recommended) | USB MIDI interfaces + 5-pin MIDI cable |
|---|---|---|
| Cable length | up to 100 m (Ethernet) | up to 15 m (MIDI 1.0) |
| Extra hardware | none (a USB Ethernet adapter if the laptop has no port) | one USB MIDI interface per computer |
| Software | rtpMIDI on both computers | the interfaces' drivers |
| OSC over the same cable | yes | no |
| Typical faults | addresses, firewall, session not connected | USB power saving, loose connector |

### 2.2 Direct Ethernet cable: fixed addresses

1. Connect the two Ethernet ports directly. A normal patch cable is fine,
   because current network ports detect the cable type themselves.
2. On each computer, run `ncpa.cpl`. Right-click the adapter used for the
   cable, then choose *Properties > Internet Protocol Version 4 (TCP/IPv4) >
   Properties > Use the following IP address*:

   | | REAPER computer | Show laptop |
   |---|---|---|
   | IP address | `192.168.77.1` | `192.168.77.2` |
   | Subnet mask | `255.255.255.0` (/24) | `255.255.255.0` (/24) |
   | Default gateway, DNS | empty | empty |

   Or run this in an administrator Command Prompt, using the adapter name shown
   in `ncpa.cpl` (for example `Ethernet` or `Ethernet 2`):

   ```bat
   netsh interface ipv4 set address name="Ethernet" static 192.168.77.2 255.255.255.0
   ```

3. Mark the cable network as *Private*. Windows treats an unidentified network
   as *Public*, where the firewall is stricter. In an administrator PowerShell:

   ```powershell
   Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private
   ```

4. Use a subnet that no other network on either computer uses, for example the
   venue Wi-Fi on the REAPER computer.
5. Test from the laptop with `ping 192.168.77.1`. If ping fails but rtpMIDI
   connects (2.3), the firewall is only blocking ping, which is fine.

### 2.3 rtpMIDI sessions

1. Install [rtpMIDI](https://www.tobias-erichsen.de/software/rtpmidi.html) by
   Tobias Erichsen on both computers. The current download is version 1.1.14.
   The site lists Windows 7 to 10, so test it on Windows 11 (**verify on your
   system**). The installer checks for Apple's Bonjour and suggests installing
   it if it is missing. Bonjour only finds peers on the local network and needs
   no internet. rtpMIDI runs as a Windows service, so its ports exist from boot
   even when the rtpMIDI window is closed.
2. On each computer, open rtpMIDI and click **+** under *My Sessions*. Set the
   fields *before* ticking *Enabled*. The port can only be changed while the
   session is disabled.

   | | REAPER computer | Show laptop |
   |---|---|---|
   | Local name (this is the MIDI port name) | `AETHRA-REAPER` | `AETHRA-STAGE` |
   | Bonjour name | leave the default (the computer name) | leave the default |
   | Port | `5004` | `5004` |

   Then tick *Enabled*. A session uses its port and the next one (UDP 5004 and
   5005).
3. On the show laptop, click **+** under *Directory* (*Add Remote Peer*):
   Name `REAPER`, Host/Port `192.168.77.1:5004`. Select it and click
   *Connect*. Each computer now lists the other under *Participants*, with a
   latency (normally 0 ms on a direct cable).
   - According to the rtpMIDI tutorial, the side that made the connection
     re-establishes it after a reboot. Starting the connection from the laptop
     covers a laptop restart in the middle of a set. Check reconnection after
     unplugging the cable and after a reboot in rehearsal ([testing.md](testing.md),
     tests 2 and 4).
4. *Who may connect to me*: *Anyone* is fine on a private cable. If you choose
   *Only computers in my directory*, add the other computer to the directory on
   both sides.
5. Leave *Live routings* empty.

If the computers do not see each other, check the cable, the addresses and the
Windows firewall on both sides. rtpMIDI must be allowed on the Private profile
(UDP 5004–5005); **verify on your system** whether the installer added that
rule.

If the REAPER computer is a Mac, use the network MIDI built into macOS (*Audio
MIDI Setup*, MIDI Network Setup) on that side. rtpMIDI is compatible with it.

### 2.4 REAPER: enable the port as an output

1. Go to *Options > Preferences > Audio > MIDI Outputs*. The session appears as
   `AETHRA-REAPER`. If it is missing (for example, the session was created
   after REAPER started), reopen the page or click *Reset all MIDI devices*.
   Both rescan for new devices (how well depends on the driver).
2. Right-click the port and choose *Enable output*. Then double-click it to
   open *Configure MIDI Output*:

   | Option | Setting | Why |
   |---|---|---|
   | Enable output to this device | on | |
   | Send clock to this device | off | Sends MIDI beat clock and Song Position Pointer, not MTC. Aethra ignores them. |
   | Do not send reset messages | off (default) | REAPER's resets (all-notes-off and similar) are harmless to Aethra. |
   | Open device in low latency/low precision mode | **off** | The default timestamped high-precision output is what you want. |
   | Do not warn if unable to open device | off | REAPER should tell you if the port fails. |
   | Offset output to this device by | `0.00` ms | Delays everything sent to this port. Compensate in Aethra instead (section 5). Whether it accepts negative values: **verify on your system**. |

3. Do not enable the session as a MIDI *input* in REAPER. Nothing needs to come
   back, and leaving it off rules out MIDI feedback loops.
4. Optional, for a backup REAPER computer running the same project: give the
   port the same *Device ID* (right-click menu) on both computers. Track
   routings refer to the device ID, and REAPER allows changing it for exactly
   this purpose.

### 2.5 The laptop: leave the port to Aethra

- Aethra opens the MIDI input whose name contains `midi.port` (not case
  sensitive): `midi.port: "AETHRA-STAGE"`, as in the example configuration.
  `.\Aethra.cmd devices` lists the exact names.
- With the classic Windows MIDI stack (WinMM), a port can usually be opened by
  only one program at a time. This is typical of USB class-compliant
  interfaces. rtpMIDI's ports come from Tobias Erichsen's virtualMIDI driver,
  which its author describes as multi-client capable, but do not rely on it.
  On the laptop, nothing but Aethra should open the port: close MIDI monitors,
  DAWs and xjadeo. `aethra monitor` opens the port too, so stop it before
  starting the show.
- **Windows MIDI Services**, the new MIDI stack in Windows 11, has been rolled
  out in phases since February 2026. It makes every port multi-client and can
  give ports new-style names. Microsoft listed "Dynamic ports (loopMIDI, …,
  rtpMIDI, …) are not always visible" as a known issue, and a fix started
  rolling out on 30 April 2026. Install Windows updates well before a tour (not
  on show day). After every update, check that `.\Aethra.cmd devices` still lists
  the port under the same name. `.\Aethra.cmd check`, part of the pre-show routine
  in [operation.md](operation.md), reports a missing port.

### 2.6 Alternative: USB MIDI interfaces and a MIDI cable

1. Connect MIDI OUT of the REAPER computer's interface to MIDI IN of the
   laptop's interface (at most 15 m).
2. In REAPER, enable the interface's output as in 2.4.
3. On the laptop, set `midi.port` to a distinctive part of the interface's
   input name (`.\Aethra.cmd devices`).
4. Turn off USB power saving on both computers (see
   [windows-setup.md](windows-setup.md)).
5. Bandwidth is no issue. A note-on takes about 1 ms on a 5-pin MIDI cable, and
   25 fps MTC uses about 6 % of the cable's capacity.

## 3. The VIDEO CUES track

1. *Track: Insert new track* (Ctrl+T). Name it `VIDEO CUES` and give it a
   distinctive colour (right-click > *Track color*).
2. Open the track's routing window (*ROUTE* / I/O button):
   - *MIDI Hardware Output*: `AETHRA-REAPER` (or the USB interface).
   - Channel: **Send to channel 16**. *Send to original channels* also works
     if every note is on channel 16. Forcing channel 16 guarantees it, so a
     note drawn on the wrong channel still arrives on 16.
3. Record input: none. Never record-arm this track. An armed track with input
   monitoring passes a MIDI keyboard or controller straight through to Aethra.
4. In `show.yaml`, set `midi.channel: 16`. Notes on other channels, for example
   from another track routed to the same port by mistake, are then ignored.
5. Lock the cue items when the show is programmed (4.1, step 7).

REAPER settings that matter on the show computer:

| Where | Setting | Value | Why |
|---|---|---|---|
| MIDI editor > *Options* | *Chase MIDI note-on/CC/PC/pitch in project playback* | **off** | When on, starting or seeking playback inside a note re-sends its note-on, and the last Program Change before the play position is re-sent. Since REAPER 6.76 this one global option also covers CC/PC/pitch chasing. If REAPER also plays instrument parts, check them with it off. |
| MIDI editor > *Options > Note preview* | *Preview notes when inserting or editing* | off | Clicking or editing a cue note would send it to Aethra. |
| *Preferences > Audio* | *Close audio device when stopped and application is inactive* | **off** | REAPER closes its MIDI devices together with the audio device. |
| *Preferences > Audio* | *Close audio device when stopped and active (less responsive)* | off | Same reason. |
| *Preferences > Audio > Scrub/Jog* | *Only play selected tracks when scrubbing/jogging* | on | Keep `VIDEO CUES` unselected, so scrubbing never sends cue notes. |
| *Preferences > Media* | *Set media items offline when application is not active* | off (optional) | Avoids media coming back online at the moment you press play. |
| *Project Settings* (Alt+Enter) > *Video* | *Frame rate* | `25` | Timecode display and MTC (section 6). |
| *Transport* | *Repeat* | off during the show | A loop that contains a cue note fires it on every pass. |

## 4. Where to put the triggers

### 4.1 One short note per song

1. Use one MIDI item per song on `VIDEO CUES`, holding exactly one note. Select
   the track, place the edit cursor at the song start and choose *Insert > New
   MIDI item*. Double-click the item to edit it. One item per song means a song
   moved in the arrangement takes its cue with it.
2. **Position:** put the note exactly at the moment that belongs to the clip
   position `offset`. That is usually the song's first downbeat, or wherever the
   clip's first frame must appear (4.3). Turn snapping on (*Options: Toggle
   snapping*). Use the musical grid for downbeats, or set the grid to *Frame* in
   *Snap/Grid Settings* when the clip's zero point is not on a beat.
3. **The note:** channel 16, any velocity above 0 (for example 100), short
   (a 1/16 note or about 100 ms). Do not use zero-length notes or overlapping
   notes. Start the item at the note, so that *Item navigation: Move cursor to
   start of items* lands on the trigger (4.5).
4. **Number:** the note number is the cue's `midi_note` (4.2).
5. **Item properties (F2):** untick *Loop source*. New MIDI items normally have
   it on, and then lengthening the item repeats the note. Set the take name to
   the cue id, title and note, for example `03 Ballad n38`.
6. **Colour:** give the cue items the track colour (*Item and take colors*) so
   that a stray one stands out.
7. **Lock:** lock each item (*Item properties: Toggle lock*), or use REAPER's
   locking: *Locking settings…* (Shift+L), tick *Items (full)*, and toggle
   locking with L. With ripple editing, lock the backing tracks the same way,
   or an edit could move the audio without its cue. *Preferences > Editing
   Behavior* has options for how ripple edits treat locked items.
8. **Optional marker:** add a project marker at each cue note named after the
   cue id (`03`). It helps navigation, the Region/Marker Manager then lists every
   cue position (4.5), and OSC triggers need it (section 7).
9. **Pre-roll:** start playback a moment before the note, for example from a
   marker one bar before the song. The note is then played, not chased.
10. **Copies:** Ctrl+drag copies are independent unless *Pool MIDI source data
    when pasting or duplicating media items* (*Preferences > Media > MIDI*) is
    on. With pooled copies, changing one note changes all of them.
11. **Timebase:** give the cue items the same *Item timebase* as the backing
    track items, so that a tempo edit moves both or neither. After any tempo
    edit, read `mtc_start` again (4.5).

```
 timeline   ··count-in··│ bar 1 of the song ══════════════════════════
 VIDEO CUES             ▮  note 38, channel 16, 1/16 long  (item "03 Ballad n38")
 MTC        ════════════════════════════════════════════════  (one item, whole show)
 Aethra                 ▶  frame 0 of 03-ballad.mp4 on stage   (offset 0)
```

### 4.2 Note numbers, not note names

- In the MIDI editor, select the note and press Ctrl+F2 (*Event properties…*).
  The *Note* field shows the number and the name, for example `38 D2`. Put the
  **number** in `midi_note`.
- Names depend on an octave convention. By default REAPER labels middle C (note
  60) as C4. *Preferences > Media > MIDI > MIDI octave name display offset*
  changes that (−1 makes it C3). Other programs and devices use other
  conventions. Numbers are unambiguous, which is why Aethra takes numbers:
  `midi_note: 38`.
- `aethra monitor` prints every received note with its number and channel.

### 4.3 The offset: which clip frame belongs to the note

`offset` is the clip position, in seconds, that coincides with the trigger.
Aethra shows clip position `offset + sync.latency_compensation` at the moment
the note arrives ([architecture.md](architecture.md), timing model).

| Situation | Put the note | `offset` |
|---|---|---|
| The clip's first frame belongs on bar 1 of the song | on bar 1 | `0` |
| The clip has a 2 s intro that must be seen before bar 1 | 2 s before bar 1 | `0` |
| Same clip, but the note stays on bar 1 | on bar 1 | `2.0`. The clip starts 2 s in, so its first 2 s are never shown. |
| The note is at the start of a 2 s count-in; the clip belongs on bar 1 | 2 s before bar 1 | `-2.0`. Aethra waits 2 s, then starts the clip. |

Prefer `offset: 0` with the note where the clip starts. Use a positive offset
to skip a clip's pre-roll, and a negative one when the note cannot move.

### 4.4 Program Changes and command notes

- **Program Change** (`midi_program: 0–127`): insert it in the MIDI editor's
  *Bank/Program Select* lane, or with *Insert bank/program select event…*.
  Aethra uses the raw value (0–127), while some programs display 1–128, so
  check the number with `aethra monitor`. Chasing must be off (section 3);
  otherwise every start of playback inside a song re-sends that song's Program
  Change and restarts its clip. Notes are simpler and recommended.
- **Command notes** (`midi.command_notes`) use the same channel and must not
  collide with cue notes. In the REAPER timeline, prefer `filler` (for example
  to force the filler at an exact point at the end of a song). `black`
  *toggles* blackout like the B key, so its effect depends on the current state.
  Do not put it on the timeline.

### 4.5 Reading the trigger timecode (`mtc_start`)

`mtc_start` is the REAPER timecode of a cue's trigger note. Aethra uses it to
anchor MTC, and the xjadeo timeline tool uses it to place the clips.

1. Set the ruler and the transport to *Hours:Minutes:Seconds:Frames* (right-click
   the ruler or the time display). The project frame rate must be 25 (section 6).
2. Select a cue item and run *Item navigation: Move cursor to start of items*,
   or click the note position with snapping on. Read the time display.
3. With a marker on every cue note, *View > Region/Marker Manager* lists all
   positions at once.
4. Write the value as `mtc_start: "00:12:30:00"`.

Any edit that moves a song changes its timecode. Update `mtc_start` and rebuild
the xjadeo fallback video ([xjadeo-fallback.md](xjadeo-fallback.md)).

## 5. Calibrating latency and per-cue offsets

**Global latency.** Rehearsal test 5 in [testing.md](testing.md) gives the full
procedure. The REAPER part:

1. On any computer with ffmpeg, run `python tools/make_test_clips.py sync --out
   test-clips`. You get `sync-flash-25.mp4` (a white frame every second) and
   `sync-click.wav` (a short beep every second).
2. Add a cue for the flash clip in `show.yaml` (for example `id: sync`) with a
   free note number, and put that note on `VIDEO CUES` at a bar line.
3. Put `sync-click.wav` on an audio track, starting exactly at the same position
   (snap).
4. Play it. Film the screen and a loudspeaker together with a phone in slow
   motion (120 or 240 fps).
5. Count the slow-motion frames from the click to the white frame. A positive
   result means the picture is late. Set `sync.latency_compensation` to the
   delay in seconds (for example `0.060`) and repeat until the difference is
   under one frame.

- Film close to a loudspeaker. Sound travels about 14 m in one frame (40 ms), so
  in a large room you may want the picture to match the middle of the audience
  rather than the stage.
- The result includes everything between REAPER and the screen. Measure again
  after changing REAPER's audio interface or buffer size, the projector or LED
  processor, or its picture mode.

**Per-cue offsets.** Clips can be cut a frame or two early or late. Turn on test
mode (Ctrl+T); the overlay shows the clip's timecode and frame number. Film it
together with REAPER's *View > Big clock* in H:M:S:F, or watch a visual hit
against an audio hit. If a clip is consistently *n* frames late, increase its
`offset` by *n* × 0.04 s. If it is early, decrease it.

**Keeping the first frames.** `latency_compensation` starts every clip that far
in, so its first one or two frames are skipped. That is invisible if clips start
from black. If those frames matter, keep `latency_compensation` at 0 and have
REAPER send the cues earlier instead: give `VIDEO CUES` a negative *Media
playback offset* (track routing window) equal to the measured delay. Check with
the sync kit that REAPER applies it to the MIDI hardware output (**verify on
your system**).

## 6. MIDI Time Code

MTC is optional for Aethra. With `sync.mtc.enabled: true` it becomes the
reference for drift correction, and Aethra follows relocations (useful in
rehearsals with loops and jumps). The xjadeo fallback needs it, so keep the MTC
track in every show project. You can then switch to the fallback without
touching REAPER. MTC goes to the same port as the cue notes.

### 6.1 Project settings for 25 fps

1. *Project Settings* (Alt+Enter) > *Video* > *Frame rate*: `25`.
2. *Project start time*: leave it at 0. If it was changed, reset it: right-click
   the ruler, *Change start time/measure…*, *Reset project start time*. Another
   start time works only if REAPER, the generator, `mtc_start` and the xjadeo
   timeline all use it consistently.
3. Set the ruler and the transport to *Hours:Minutes:Seconds:Frames*.

### 6.2 The timecode generator item

1. Insert a track named `MTC` and select it. Move the edit cursor to the start of
   the project (*Go to start of project*, Home).
2. *Insert > SMPTE LTC/MTC Timecode Generator*. This puts a timecode generator
   item on the selected track. The exact menu wording varies slightly between
   REAPER versions.
3. *Item Properties* (F2): *Position* 0, and a *Length* that reaches past the end
   of the show by a few minutes. MTC is only sent while the play cursor is over
   the item. Set its *Item timebase* to *Time* (or run *Item properties: Set item
   timebase to time*), so that later tempo edits can never move or stretch the
   timecode.
4. Double-click the item (REAPER 7.23 and later), or right-click > *Source
   properties*, to open *SMPTE Generator Properties*:
   - **Send MIDI (MTC)** (not *Send audio (LTC)*)
   - *Frame rate*: **25**
   - *Start time*: `00:00:00:00`, the timecode the ruler shows at the item's start
   - *User value 1–4* are LTC user bits and do not matter here.
5. Track routing: *MIDI Hardware Output* `AETHRA-REAPER`, *Send to original
   channels*. MTC has no channel; REAPER does not rewrite system messages.
6. Lock the item and do not mute the track. You do not need *Send clock to this
   device* for MTC from the generator.
7. Check on the laptop while the show is not running: run `.\Aethra.cmd monitor`
   and play in REAPER. Once a second it prints a line like `MTC running
   00:01:23:04 (25 fps)`, which must match REAPER's time display.

### 6.3 When REAPER sends MTC

| REAPER is… | MTC on the cable | Consequence |
|---|---|---|
| playing over the generator item | continuous quarter-frame messages (100 per second at 25 fps) | Aethra and xjadeo follow. |
| seeking while playing (ruler click, marker jump, loop wrap) | a full-frame message, then quarter frames again | Aethra corrects with one exact seek; xjadeo jumps. |
| stopped | nothing | Aethra: `sync.mtc.on_stop`; xjadeo holds the last frame. |
| locating while stopped | nothing | Nobody notices until playback starts. |
| playing past the end of the item | nothing | As stopped. Make the item long enough. |

### 6.4 MTC while stopped (optional): vVIMTC

The item-based generator is silent while REAPER is stopped. *vVIMTC Generator*
is a free, unofficial JSFX posted on the Cockos forum (thread 43606, "Blechi",
2009). It can send MTC while the transport is stopped or paused. Its modes are
*Quarter Frame MTC on STOP/PAUSE*, *Full Frame MTC on STOP/PAUSE* (meant to be
used together with REAPER's generator), *Quarter Frame MTC always*, and
*Fullframe MTC + Quarterframe MTC* (the default, a standalone generator).

- Use it only if you want xjadeo to follow locates while stopped. Add it in
  *Full Frame MTC on STOP/PAUSE* mode on a separate track routed to the same
  port, next to the generator item.
- Set its *MTC Framerate* slider to **25**; the default is 30.
- According to its own notes, its track must be record-armed in a
  monitoring-only record mode, must not be muted, and stops working when other
  tracks are soloed. Arm it with a MIDI input that receives nothing, not *All
  MIDI inputs*.
- It dates from 2009, so test it with your REAPER version. Also check with test
  mode that Aethra behaves as expected while REAPER is stopped. Do not use
  *Quarter Frame MTC always*, which looks like running MTC while the transport
  stands still.

### 6.5 What does not send MTC

- *Send clock to this device* (MIDI output configuration) sends MIDI beat clock
  and Song Position Pointer.
- *External Timecode Synchronization* (right-click the Play button) makes REAPER
  *follow* incoming timecode; it does not send any.
- To send MTC, use the generator item (or a JSFX such as vVIMTC).

### 6.6 In Aethra

```yaml
sync:
  mtc:
    enabled: true
cues:
  - id: "03"
    file: 03-ballad.mp4
    midi_note: 38
    mtc_start: "00:12:30:00"   # REAPER timecode of the note (4.5)
```

## 7. OSC (secondary option)

### 7.1 What REAPER can and cannot send

- REAPER's OSC output is **control-surface feedback**, defined by a pattern file
  (`Default.ReaperOSC`). It covers the transport state (`/play`, `/stop`), the
  position (`/time`, `/beat/str`, `/frames/str`) and the last marker or region
  passed (`/lastmarker/name`, `/lastmarker/number/str`, `/lastregion/name`, …).
  REAPER cannot send an arbitrary message at a timeline position.
- **Timing.** Feedback is produced on REAPER's user-interface thread. The REAPER
  SDK says control surfaces are run "30x/sec or so", and *Preferences >
  Control/OSC/web* has a *Control surface display update frequency* setting
  (15 Hz by default, according to the REAPER changelog). Expect a marker
  message tens of milliseconds after the play cursor crosses the marker, varying
  from pass to pass (one to two frames at 25 fps). Measure it with the sync kit
  if you use it. MIDI notes, in contrast, are scheduled with the audio and sent
  as timestamped MIDI output, precise to about a millisecond.
- **Pitfalls.** `/lastmarker/*` follows the current position, including the edit
  cursor while REAPER is stopped (**verify on your system** with `aethra
  monitor`).
  - Clicking near a marker, or jumping to one while stopped, sends that
    marker's name.
  - Seeking *during playback* into a song sends that song's marker. Aethra
    would then start the clip from its beginning in the middle of the song.

OSC marker triggers are therefore acceptable for loose material (ambient
loops, looks between songs), not for tight sync.

### 7.2 Setup

REAPER:

1. *Preferences > Control/OSC/web > Add*. Set *Control surface mode* to
   *OSC (Open Sound Control)*.
2. *Device name*: `Aethra`. *Pattern config*: a trimmed copy (7.3) or `Default`.
3. *Mode*: *Device IP/port [send only]*. *Device IP*: `192.168.77.2`. *Device
   port*: `9000`.
4. Leave *Outgoing max packet size* (1024) and *Wait between packets* (10 ms) at
   their defaults. REAPER suggests raising them if packets get lost.

Aethra (`show.yaml`):

```yaml
osc:
  enabled: true
  port: 9000
  require_playing: true      # per-cue OSC triggers only while REAPER reports /play 1
cues:
  - id: "03"
    file: 03-ballad.mp4
    osc: {address: "/lastmarker/name", arg: "03"}   # marker "03" at the cue position; quote the name
```

- `scripts/windows/prepare-stage-laptop.ps1` creates the inbound firewall rule
  for UDP 9000 on the laptop.
- Check with `.\Aethra.cmd monitor` (show not running). It prints each message
  with the sender's address, for example `/lastmarker/name ['03']` from
  `192.168.77.1`.

### 7.3 Trim the pattern file

The default configuration sends a lot. `Default.ReaperOSC` itself warns that
`TIME`, `BEAT`, `SAMPLES`, `FRAMES`, `VU`, `FX_PARAM`, `LAST_MARKER` and
`LAST_REGION` messages can flood a device.

1. Click *(open config directory)* next to *Pattern config*. Copy
   `Default.ReaperOSC` to `Aethra.ReaperOSC`.
2. In the copy, put `#` in front of every pattern line except these three. Keep
   the `DEVICE_…` and `REAPER_…` settings at the top as they are.

   ```
   PLAY t/play
   STOP t/stop
   LAST_MARKER_NAME s/lastmarker/name
   ```

3. Select `Aethra` under *Pattern config*.

### 7.4 Making marker triggers safe enough

- Set `osc.require_playing: true`. It only gates per-cue `osc:` triggers; the
  built-in `/aethra/cue` is not gated, so do not map REAPER feedback to it.
- Start Aethra before REAPER starts playing. Aethra learns the transport state
  from the `/play` messages.
- Give markers unique names equal to the cue ids, and lock markers (*Locking
  settings… > Markers*).
- During the show, do not seek during playback and do not change the OSC
  surface settings.
- `sync.retrigger_guard` ignores the same cue triggered again within one
  second, but nothing later.

### 7.5 Sending your own OSC at exact positions

There is no reliable built-in way to do this:

- REAPER's OSC output is limited to the feedback in the pattern file.
- `OscLocalMessageToHost` in ReaScript only sends a message *into* REAPER.
- REAPER's scripting API has no function for sending OSC (UDP) to another
  computer.
- SWS *Marker Actions* (markers named `!<action id>`, run when the play cursor
  crosses them) run on the UI thread, so they have the same timing limits as
  above, and they still could not send UDP.

If the laptop must receive OSC rather than MIDI, Cockos' **OSCII-bot** (v0.6,
2017; listed for Windows XP–10, so **verify on your system**) can convert the
cue notes to OSC. REAPER sends the notes to a loopback port on the REAPER
computer (for example [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)),
and an OSCII-bot script turns them into OSC. The following is an untested
sketch based on the syntax in OSCII-bot's `sample_script.txt`. It sends
`/cue/<note>`, which a cue matches with `osc: "/cue/38"`:

```
// aethra-cues.txt (OSCII-bot script): note-on on channel 16 -> /cue/<note number>
// The last @output value is the pause after each packet: 0 instead of the default 10 ms.
@input cues MIDI "loopMIDI Port"
@output aethra OSC "192.168.77.2:9000" 1024 0

@midimsg
msg1 == 0x9F && msg3 > 0 ? (     // 0x9F = note-on, channel 16
  fmt0 = msg2;
  oscsend(aethra, "t/cue/%.0f", 0);
);
```

This adds two programs and a virtual port to the chain. Use it only when MIDI to
the laptop is not possible, and measure it with the sync kit.

## 8. Troubleshooting

Run `.\Aethra.cmd monitor` on the laptop (show not running) and play the cue
track. It prints every note, Program Change and OSC message, and the MTC status
once a second.

| Symptom | Check / fix |
|---|---|
| `monitor` shows nothing | In rtpMIDI, both sessions are *Enabled* and each computer lists the other under *Participants*. REAPER *MIDI Outputs*: the port is enabled. Track routing: *MIDI Hardware Output* is the port, and the track and item are not muted. `midi.port` matches a name in `.\Aethra.cmd devices`. No other program on the laptop holds the port. |
| The port is missing in REAPER | The session was created or renamed after REAPER started. Reopen *Preferences > Audio > MIDI Outputs* or click *Reset all MIDI devices*, then pick the port again in the track routing. |
| The port is missing on the laptop | Disable and re-enable the rtpMIDI session, or reboot. On Windows 11 with Windows MIDI Services, install the updates (dynamic ports such as rtpMIDI were a known issue, fixed from April 2026) and check whether the port name changed. |
| Notes arrive but nothing starts | The channel in `monitor` does not match `midi.channel`, or the note number does not match `midi_note` (octave naming, 4.2). |
| A cue fires twice or repeatedly | *Loop source* on a lengthened item; overlapping items, or several takes or lanes playing at once (for example after loop or punch recording on the cue track); *Repeat* on with a loop over the note; two tracks or sends feeding the same port; a record-armed track echoing a controller. `retrigger_guard` hides repeats within 1 s only. |
| A cue fires when playback starts mid-song | *Chase MIDI note-on/CC/PC/pitch* is on and the play position is inside a cue note or after a Program Change. Turn chase off; keep notes short. |
| A cue fires while editing | MIDI editor note preview, clicking the piano-roll keys, *Scrub preview MIDI* in the MIDI editor, or scrubbing over the cue track. Edit the cue track only when Aethra is not running the show, or mute the track meanwhile. |
| A cue fires on a click in the timeline during playback | Seeking into a note with chase on; with OSC, seeking past a marker (7.1). Untick the relevant boxes under *Preferences > Audio > Seeking* > *Seek playback when clicked* so that a stray click cannot move playback. |
| Nothing is sent while REAPER is in the background | *Close audio device when stopped and application is inactive* is on. Turn it off. |
| Cues late or uneven | OSC marker triggers (use MIDI); *Open device in low latency/low precision mode* turned on; REAPER's audio buffer or interface changed since the measurement (measure again). |
| `MTC none` while REAPER plays | Generator item missing, muted, or too short; *Send MIDI (MTC)* not selected; MTC track not routed to the port. |
| MTC differs from REAPER's display | Generator frame rate not 25, project frame rate not 25, item not at position 0, or the project start time changed. |
| OSC: nothing arrives | Mode *Device IP/port [send only]*; device IP and port; laptop firewall (UDP 9000); `osc.enabled: true`. |
| OSC cue fires while stopped | `osc.require_playing: true`; start Aethra before playing. |

## 9. Show-day checklist (REAPER side)

1. The cable is plugged in; the addresses are 192.168.77.1 and .2; rtpMIDI on
   both computers lists the other one as a participant.
2. The show project is loaded and the project frame rate is 25.
3. The MTC item covers the whole set and is routed to the port.
4. `VIDEO CUES` is not muted and not armed, outputs to the port on channel 16,
   and its items are locked; locking is on (L).
5. Chase is off, *Repeat* is off, and no MIDI editor is open for the cue track.
6. *Close audio device when stopped and application is inactive* is off. The
   REAPER computer does not sleep, update or show notifications.
7. On the laptop, before starting the show: `.\Aethra.cmd check` says OK, and
   `.\Aethra.cmd monitor` shows the first song's note on channel 16 and `MTC
   running` while REAPER plays its first seconds. Stop `monitor`, start the
   show, and play the first trigger once ([operation.md](operation.md)).
8. If anything was edited since the last rehearsal: are the `mtc_start` values
   and the xjadeo fallback video still correct?

## References

- REAPER User Guide 7.81 (MIDI output configuration, track routing, timecode
  generator, preferences): <https://www.reaper.fm/userguide.php>
- REAPER changelog (MIDI output, chase, timecode and OSC changes):
  <https://www.reaper.fm/whatsnew.txt>
- REAPER OSC overview and the `Default.ReaperOSC` file shipped with REAPER 7.82:
  <https://www.cockos.com/reaper/sdk/osc/osc.php>
- REAPER SDK, `reaper_plugin.h` (control surface `Run()` rate):
  <https://github.com/justinfrankel/reaper-sdk>
- rtpMIDI and its tutorial: <https://www.tobias-erichsen.de/software/rtpmidi.html>
- Windows MIDI Services: <https://blogs.windows.com/windowsexperience/2026/02/17/making-music-with-midi-just-got-a-real-boost-in-windows-11/>
  and the known issues: <https://devblogs.microsoft.com/windows-music-dev/windows-midi-services-rollout-known-issues-and-workarounds/>
- SWS Marker Actions: <https://www.sws-extension.org/markeractions.php>
- OSCII-bot: <https://www.cockos.com/oscii-bot/>
