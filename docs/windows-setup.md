# Windows setup for the show laptop

Checklist for the Windows laptop that runs Aethra. Two rules:

1. **Nothing but our video ever appears on the stage output**: no notification, dialog, overlay,
   taskbar, desktop or mouse cursor.
2. **The laptop never sleeps, dims, throttles or restarts during a show.** Aethra needs no internet.

How to use this document:

- It covers Windows 11 (23H2 and later) and Windows 10 22H2. Paths are given for Windows 11, with
  Windows 10 noted where it differs. Windows 10 reached end of support in October 2025, so prefer
  Windows 11 for a new show laptop.
- Microsoft moves settings between releases. If a path does not match, search for the setting name in
  Settings. **Verify** marks items that could not be confirmed for every build or every laptop: check
  them on the target machine.
- **[script]** = applied (or checked) by
  [`scripts/windows/prepare-stage-laptop.ps1`](../scripts/windows/prepare-stage-laptop.ps1), see
  [section 13](#13-the-preparation-script). Run the script first, then do the manual items.
- **Admin** = needs an elevated PowerShell (right-click > *Run as administrator*). **Pro** = a Group
  Policy setting documented for Pro, Enterprise and Education; Home may ignore it.
- `HKCU` values belong to the show user's account, `HKLM` values to the whole machine. Values are
  `REG_DWORD` unless noted. Most `HKCU` changes apply after signing out and in again. Values marked
  *(community)* are not documented by Microsoft; they are what the matching Settings switch writes,
  according to widely used community tutorials (ElevenForum/TenForums).
- Do sections 0 to 12 once per laptop and again after every Windows feature update. Do
  [section 14](#14-pre-show-5-minute-checklist) before every show.

## 0. Before you start

- **Install everything first, then freeze.** Windows updates, the GPU driver, MIDI interface drivers,
  rtpMIDI, mpv and Aethra. Then apply this checklist and rehearse as in [testing.md](testing.md): the
  full set twice in a row, pulling the MIDI/network cable mid-song, a missing file, restarting Aethra
  mid-set. After that, no updates until the run of shows is over ([4.5](#45-windows-update)).
- **A dedicated local account** for shows (no Microsoft account sign-in) keeps OneDrive, backup and
  account prompts away. Optional.
- **Keep the media in a plain local folder** (for example `C:\AethraMedia`), not in a folder synced by
  OneDrive: OneDrive can turn files into online-only placeholders, and those cannot be opened offline.
- **Smart App Control** (Windows 11; Windows Security > App & browser control) blocks apps it cannot
  verify, which can include unsigned mpv builds. Check its state and confirm that Aethra and mpv start
  without any prompt. Files that came from a download or a zip can trigger the SmartScreen "Windows
  protected your PC" dialog: run `Get-ChildItem -Recurse <folder> | Unblock-File` on them once.
- **Hardware tip**: an HDMI EDID emulator between the laptop and the projector or LED processor keeps
  Windows from seeing the display disappear when the far end is power-cycled or switches inputs.

## 1. Display

| Setting | Where | Command / registry / notes |
|---|---|---|
| **Extend**, not Duplicate | `Win+P` > *Extend*, or Settings > System > Display > the menu near the top > *Extend these displays* (Win10: *Multiple displays* menu) | `DisplaySwitch.exe /extend` is undocumented and on Windows 11 22H2+ may only open the *Project* flyout: don't rely on it. |
| **Laptop panel = main display** | Settings > System > Display > select the laptop panel > *Multiple displays* > *Make this my main display* | The taskbar clock and tray, the sign-in and lock screens, and dialogs without an owner window open on the main display, so stray windows land on the operator's screen. |
| Stage output **1920x1080** | Settings > System > Display > select the stage display > *Display resolution* | Or the projector's native resolution if Aethra's files are rendered for it. |
| Stage output **50 Hz** (or 25 or 100 Hz) | Win11: Settings > System > Display > select the stage display > *Advanced display* > *Choose a refresh rate*. Win10: Settings > System > Display > *Advanced display settings* > *Choose display* > *Refresh rate* | Windows lists only the modes the display reports. 1080p50 is a standard HDMI (CTA-861) mode, but check that the projector or LED processor offers it. Fallback: *Display adapter properties* > *List All Modes*. *Dynamic refresh rate* applies to laptop panels only. |
| Stage display **scale 100 %** | Settings > System > Display > select the stage display > *Scale & layout* > *Scale* (Win10: *Change the size of text, apps, and other items*) | - |
| **HDR off** on the stage display | Win11: Settings > System > Display > select the display > *HDR* > *Use HDR* off, *Auto HDR* off. Win10: Settings > System > Display > *Windows HD Color settings* > *Use HDR* off | `Win+Alt+B` toggles HDR (a Game Bar shortcut). |
| **Night light off** | Settings > System > Display > *Night light* off and *Schedule night light* off | - |
| **Automatic brightness off** | Win11: Settings > System > Display > *Brightness* > *Change brightness based on content* = *Off* (Windows 11 22H2+ on laptops whose manufacturer enabled it), and *Change brightness automatically when lighting changes* off. Win10: *Change brightness automatically when lighting changes* | These change the built-in panel, i.e. the operator's view, not the stage output. Ambient-light power setting: `powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO ADAPTBRIGHT 0` then `powercfg /setactive SCHEME_CURRENT`. |
| **Taskbar only on the main display** [script] | Win11: Settings > Personalization > Taskbar > *Taskbar behaviors* > *Show my taskbar on all displays* off. Win10: Settings > Personalization > Taskbar > *Multiple displays* > *Show taskbar on all displays* off | `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced` `MMTaskbarEnabled` = 0 *(community)*. A full-screen window covers the taskbar, but only while it is there. |
| **"Show desktop" corner off** (Win11) | Settings > Personalization > Taskbar > *Taskbar behaviors* > *Select the far corner of the taskbar to show the desktop* off | Show desktop (also `Win+D`) minimizes every window, the stage window included. |
| **Window placement after a display drop-out** (Win11) | Settings > System > Display > *Multiple displays*: *Remember window locations based on monitor connection* on; *Minimize windows when a monitor is disconnected*: decide by test | Both are on by default. With minimizing off, windows of a lost display are stacked on the main display (the video would cover the console). Test: unplug the stage cable during playback, plug it back, check that the video returns to the stage. |
| **Desktop background: solid black** | Settings > Personalization > *Background* > *Personalize your background* = *Solid color*, black | Recent updates can make Windows spotlight the default wallpaper. If the player ever exits, the stage shows black, not a photo. Optional: hide desktop icons (right-click the desktop > *View* > *Show desktop icons*). |

Why 50 Hz: at 25 fps each frame is shown for exactly two refreshes. At 60 Hz frames alternate between
two and three refreshes (2.4 on average), which shows as judder on movement.

**GPU vendor power saving** (check in the vendor control panel; menus differ between driver versions):

- Intel: *Display Power Saving Technology* (DPST) changes the built-in panel's brightness and contrast
  to save power. Intel Graphics Command Center: *System* > *Power* > *On Battery* > *Display Power
  Savings* off; the older Intel Graphics Control Panel calls it *Intel DPST* and also has a *Graphics
  Power Plan* (set *Maximum Performance*). Panel Self Refresh: turn it off if the vendor panel exposes
  it (verify on the target machine).
- NVIDIA: NVIDIA Control Panel > *Manage 3D settings* > *Power management mode* = *Prefer maximum
  performance* (verify; it affects GPU clocks, not the image).
- AMD: AMD Software: Adrenalin Edition > *Display* > *Vari-Bright* off (a laptop-panel brightness
  feature).

These features mainly concern the laptop panel, but setting them keeps the operator's view and the GPU
behaviour predictable. GPU scheduling and the per-app GPU choice are in [section 12](#12-graphics).

## 2. Power

Values are stored **per power plan**: choose the plan first, then set the values. After any
`powercfg /setacvalueindex` on the active plan, run `powercfg /setactive SCHEME_CURRENT`: changes take
effect only when the plan is activated again. Standard users may change plan values by default.

| Setting | Where | Command / notes |
|---|---|---|
| **Power plan / power mode** | Modern Standby laptops (`powercfg /a` lists *Standby (S0 Low Power Idle)*) allow only *Balanced* or plans derived from it: set Settings > System > Power & battery > *Power mode* = *Best performance* for *Plugged in* (Win10: slider in the battery flyout). Other laptops: Control Panel > Power Options > *High performance* (possibly under *Show additional plans*). | `powercfg /list`, `powercfg /setactive SCHEME_MIN` (High performance, if listed) [script: `-HighPerformancePlan`]. Windows applies power throttling unless the mode is *Best performance*. *Ultimate Performance* was introduced for workstations and, per Microsoft, is not offered on battery-powered systems: skip it on a laptop. |
| **Plugged in** | Use the laptop's own charger. | See also *On battery too* below. |
| **Screen off, sleep, hibernate: Never** [script] | Win11: Settings > System > Power & battery > *Screen, sleep, & hibernate timeouts* (older builds: *Screen and sleep*) > everything *Never*. Win10: Settings > System > *Power & sleep* | `powercfg /change monitor-timeout-ac 0`, `standby-timeout-ac 0`, `hibernate-timeout-ac 0`, `disk-timeout-ac 0` (minutes; 0 = never). Recent builds ship with 5-minute defaults. |
| **Dim display: never** [script] | Control Panel > Power Options > *Change plan settings* > *Change advanced power settings* > *Display* > *Dim display after* | `powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEODIM 0`. Built-in panel only. |
| **Lid close: Do nothing** [script] | Control Panel > Power Options > *Choose what closing the lid does*; recent Win11: Settings > System > Power & battery > *Lid & power button controls* (label varies) | `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0` (0 do nothing, 1 sleep, 2 hibernate, 3 shut down). Programs cannot block lid or power-button sleep, so this setting is the protection. Still keep the lid open: on many laptops a closed lid switches the panel off and Windows moves the console to the remaining display (verify). |
| **Power and sleep buttons: Do nothing** [script] | Control Panel > Power Options > *Choose what the power buttons do* | `... SUB_BUTTONS PBUTTONACTION 0`, `... SUB_BUTTONS SBUTTONACTION 0`. Holding the power button for several seconds still forces the laptop off. |
| **USB selective suspend: Disabled** [script] | Advanced power settings > *USB settings* > *USB selective suspend setting* (can be hidden on Modern Standby laptops; the script sets it anyway) | `powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0`. Microsoft recommends keeping it on for battery life; for a show, USB MIDI reliability wins. Also Device Manager > *USB Root Hub* and the MIDI interface > *Power Management* > untick *Allow the computer to turn off this device to save power* (the tab is missing on some systems). |
| **PCI Express Link State Power Management: Off** [script] | Advanced power settings > *PCI Express* > *Link State Power Management* | `powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0` |
| **Programs may keep the display and system on** [script] | Hidden settings, default *Yes* | `ALLOWDISPLAY` (SUB_VIDEO) and `SYSTEMREQUIRED` (SUB_SLEEP) = 1, so mpv's "display required" requests are honoured. |
| **Energy saver / Battery saver off** | Win11 24H2+: Settings > System > Power & battery > *Energy saver*: *Always use energy saver* off, *Turn energy saver on when battery level is at* as low as possible. Older Win11: *Battery saver*. Win10: Settings > System > *Battery* > *Turn battery saver on automatically if my battery falls below* | Energy saver dims the panel by 30 % and locks the power mode. |
| **Presence sensing off** (laptops with a presence sensor) | Settings > System > Power & battery > *Screen and sleep* / *Screen, sleep, & hibernate timeouts*: *Automatically turn off my screen when I leave* off; under *More options* no lock-on-leave; adaptive dimming off if offered (verify labels) | Privacy switch: Settings > Privacy & security > *Presence sensing*. |
| **Fast Startup off** (optional) [script: `-DisableFastStartup`] | Control Panel > Power Options > *Choose what the power buttons do* > *Change settings that are currently unavailable* > untick *Turn on fast startup (recommended)* | Admin: `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power` `HiberbootEnabled` = 0, or `powercfg /hibernate off` (also removes hibernation). With Fast Startup, *Shut down* hibernates the kernel session including the loaded drivers and the next power-on resumes them; without it, every power-on initialises USB MIDI, network and GPU drivers from scratch. *Restart* always does a full boot. Microsoft's general advice is to keep it on; on a show machine, predictable boots win. |
| **On battery too** (recommended) [script: `-IncludeBattery`] | Same pages, *On battery* columns | The `-dc` / `/setdcvalueindex` variants of the commands above. If the power cable is pulled mid-show, the laptop keeps going instead of dimming or sleeping a few minutes later. |

Aethra itself asks Windows to keep the display and the system awake for the whole session
(`SetThreadExecutionState`). mpv's `--stop-screensaver=yes` adds its own request during active
playback only; when paused, idle or at the end of a file it releases it. The timeouts above must be
*Never* either way: if Aethra is not running (or was closed), nothing else keeps the screen on. To see who holds the
display awake during a test: `powercfg /requests` (from an elevated prompt if it refuses).

## 3. Lock screen, screen saver and sign-in

| Setting | Where | Notes |
|---|---|---|
| **Screen saver: (None)** [script checks] | Settings > Personalization > *Lock screen* > *Screen saver* (Win10: *Screen saver settings*) > *(None)* | Programs' "keep the display on" requests do not stop the screen saver, and it runs on every display. The script reports a configured one (`HKCU\Control Panel\Desktop` `SCRNSAVE.EXE`). |
| **No sign-in after being away** | Settings > Accounts > *Sign-in options* > *If you've been away, when should Windows require you to sign in again?* = *Never* (verify; it can be greyed out when Windows Hello is set up) | Locking replaces the desktop on every display, the stage included. |
| **Dynamic lock off** | Settings > Accounts > *Sign-in options* > *Dynamic lock*: untick | It locks the PC when a paired phone walks away. |
| **Lock screen without tips** [script] | Settings > Personalization > *Lock screen* > *Personalize your lock screen* = *Picture*; *Get fun facts, tips, tricks, and more on your lock screen* off | `HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager` `RotatingLockScreenOverlayEnabled` = 0, `SubscribedContent-338387Enabled` = 0 *(community)*. |

## 4. Notifications and interruptions

### 4.1 Notifications and Do Not Disturb

| Setting | Where | Command / notes |
|---|---|---|
| **All notifications off** [script] | Win11: Settings > System > *Notifications* > *Notifications* off. Win10: Settings > System > *Notifications & actions* > *Get notifications from apps and other senders* off | `HKCU\Software\Microsoft\Windows\CurrentVersion\PushNotifications` `ToastEnabled` = 0 *(community; sign out to apply)*. Pro: *Turn off toast notifications* (User Configuration > Administrative Templates > Start Menu and Taskbar > Notifications); Microsoft notes that it does not cover Windows system features. |
| **Do Not Disturb on** (Win11 22H2+) | Settings > System > *Notifications* > *Do not disturb* on, and under *Turn on do not disturb automatically*: *During these times* (cover setup to end of show), *When duplicating your display*, *When playing a game*, *When using an app in full-screen mode*, *For the first hour after a Windows feature update* | Whether the full-screen rule fires for a full-screen window on a secondary display is not documented: rely on the time rule or switch DND on by hand. *Focus* sessions (Settings > System > Focus) are a separate feature. No documented registry value. |
| **Focus assist** (Win10, Win11 21H2) | Settings > System > *Focus assist* > *Alarms only*; automatic rules *During these times*, *When I'm duplicating my display*, *When I'm playing a game*, *When I'm using an app in full screen mode* | - |

Notifications that remain (critical security alerts, for example) are shown on the main display, the
laptop panel (verify on the target machine).

### 4.2 Tips, suggestions and ads

All per user. Registry values are under
`HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager` unless another key is given;
all are *(community)*. Microsoft's own policies for these (*Turn off Microsoft consumer experiences*,
*Do not show Windows tips*) work only on Enterprise and Education.

| Setting (turn off) | Where | Registry |
|---|---|---|
| *Get tips and suggestions when using Windows* [script] | Win11: Settings > System > Notifications > *Additional settings*. Win10: Notifications & actions > *Get tips, tricks, and suggestions as you use Windows* | `SubscribedContent-338389Enabled` = 0 |
| *Show the Windows welcome experience after updates ...* [script] | Same place | `SubscribedContent-310093Enabled` = 0 |
| *Suggest ways to get the most out of Windows and finish setting up this device* [script] | Same place (Win10: *Suggest ways I can finish setting up my device ...*) | `HKCU\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement` `ScoobeSystemSettingEnabled` = 0 |
| *Show me suggested content in the Settings app* [script] | Settings > Privacy & security > *Recommendations & offers* (older builds: *General*). Win10: Settings > Privacy > General | `SubscribedContent-338393Enabled`, `SubscribedContent-353694Enabled`, `SubscribedContent-353696Enabled` = 0 |
| Start: *Show recommendations for tips, shortcuts, new apps, and more*, *Show account-related notifications* (Win11) | Settings > Personalization > *Start* | `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced` `Start_IrisRecommendations` = 0, `Start_AccountNotifications` = 0 |
| *Show suggestions occasionally in Start* (Win10) [script] | Settings > Personalization > Start | `SubscribedContent-338388Enabled` = 0 |
| *Show sync provider notifications* (OneDrive ads in File Explorer) [script] | File Explorer > *Options* > *View* | `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced` `ShowSyncProviderNotifications` = 0 |
| AutoPlay [script] | Win11: Settings > Bluetooth & devices > *AutoPlay* > *Use AutoPlay for all media and devices* off. Win10: Settings > Devices > AutoPlay | `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers` `DisableAutoplay` = 1 |

### 4.3 Widgets, Copilot, startup and background apps

- **Widgets** (Win11): Settings > Personalization > Taskbar > *Widgets* off. Pro: policy *Allow widgets*
  = Disabled (`HKLM\SOFTWARE\Policies\Microsoft\Dsh` `AllowNewsAndInterests` = 0). The script does
  not touch `TaskbarDa`: since 2024 the User Choice Protection Driver blocks writes to it from
  `reg.exe` and PowerShell.
- **News and interests** (Win10): right-click the taskbar > *News and interests* > *Turn off*. Pro:
  `HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Feeds` `EnableFeeds` = 0.
- **Copilot**: since late 2024 a Store app (*Microsoft Copilot*). Remove it in Settings > Apps >
  Installed apps, or `Get-AppxPackage -Name Microsoft.Copilot | Remove-AppxPackage`. The old
  *Turn off Windows Copilot* policy is deprecated and does not affect the app. If the keyboard has a
  Copilot key: Settings > Personalization > Text input > *Customize Copilot key on keyboard*.
- **Startup apps**: Settings > Apps > *Startup* (or Task Manager > *Startup apps*): turn off everything
  Aethra does not need: OneDrive, Teams, Edge, Spotify, chat apps, and the laptop vendor's update and
  support tools, which show their own pop-ups. In OneDrive's settings turn off starting with Windows;
  in Edge (`edge://settings/system`) turn off *Startup boost* and running in the background after
  Edge is closed (verify the labels in your versions).
- **Background apps**: Win11 per app: Settings > Apps > Installed apps > (app) > *Advanced options* >
  *Background app permissions* > *Let this app run in background* = *Never*. Win10: Settings >
  Privacy > *Background apps*. Pro: policy *Let Windows apps run in the background* = *Force Deny*
  (`HKLM\SOFTWARE\Policies\Microsoft\Windows\AppPrivacy` `LetAppsRunInBackground` = 2). This covers
  Store apps only; Aethra and mpv are desktop programs and are not affected.

### 4.4 Windows Security (Microsoft Defender)

- **Keep real-time protection on.** Don't disable Defender permanently.
- **Notifications**: Windows Security > *Settings* > *Manage notifications*: turn off the informational
  and recent-activity notifications and the firewall notifications. Critical alerts (threat found)
  are always shown, so scan new media before copying it to the laptop. Pro (Admin):
  *Hide non-critical notifications* (`HKLM\SOFTWARE\Policies\Microsoft\Windows Defender Security
  Center\Notifications` `DisableEnhancedNotifications` = 1); Tamper Protection may override it (verify).
- **Scheduled scan**: Microsoft documents the default schedule as a daily quick scan at 02:00 that
  runs only when the machine is idle, and a late show without keyboard input can look idle. Move it
  to a time when no show runs (Admin): `Set-MpPreference -ScanScheduleQuickScanTime 13:00:00`, and
  check with
  `Get-MpPreference | Select-Object ScanScheduleDay, ScanScheduleQuickScanTime, ScanOnlyIfIdleEnabled`.
  Defender also scans during Automatic Maintenance ([section 11](#11-maintenance-and-background-work)).
- **Media folder exclusion** (optional): Windows Security > Virus & threat protection > *Manage
  settings* > *Exclusions* > *Add or remove exclusions* > *Folder*, or (Admin)
  `Add-MpPreference -ExclusionPath 'C:\AethraMedia'`. Avoids scanning large clips when they are
  opened; the price is that files there are never scanned on access.

### 4.5 Windows Update

- **Update, test, then pause.** Win11: Settings > Windows Update > *Pause updates* > *Pick a date*
  (at most 35 days ahead). Win10: Settings > Update & Security > Windows Update > *Pause updates for
  7 days*, or *Advanced options* > *Pause updates* (pick a date). When the pause ends Windows installs
  pending updates before it lets you pause again, so check the end date before every run of shows.
- **Active hours**: Win11: Settings > Windows Update > Advanced options > *Active hours* > *Manually*.
  Win10: Windows Update > *Change active hours*. Cover setup to end of show; the maximum span is
  18 hours.
- **Advanced options**: *Receive updates for other Microsoft products* off, *Download updates over
  metered connections* off, *Notify me when a restart is required to finish updating* off (verify the
  label). Win11 main page: *Get the latest updates as soon as they're available* off.
- **Metered Ethernet** (defence in depth for the times the laptop is online): Win11: Settings > Network
  & internet > Ethernet > *Metered connection* on. Win10: Settings > Network & Internet > Ethernet >
  (network) > *Set as metered connection*.
- **Pro policies** (Admin, Computer Configuration > Administrative Templates > Windows Components >
  Windows Update): *Turn off auto-restart for updates during active hours*; *Display options for update
  notifications* (Win11 22H2+: *2* = no notifications at all, including restart warnings); *Do not
  include drivers with Windows Updates* (`HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate`
  `ExcludeWUDriversInQualityUpdate` = 1) so a working GPU driver is not replaced.
- **Device Installation Settings**: `sysdm.cpl` > *Hardware* > *Device Installation Settings* > *No*
  stops automatic download of manufacturers' apps and icons (not drivers).
- Use pause and policies; don't disable the Windows Update service.

## 5. Error dialogs

| Setting | Registry | Notes |
|---|---|---|
| **WER: no crash or hang dialogs** [script] | `HKCU\Software\Microsoft\Windows\Windows Error Reporting` `DontShowUI` = 1. Admin: the same value under `HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting` [script] | Removes the "*program* has stopped working" and "not responding" dialogs. Pro: *Prevent display of the user interface for critical errors* (Windows Components > Windows Error Reporting). Leave `Disabled` at 0: crashes are still recorded (Event Viewer > Windows Logs > Application; Reliability Monitor, `perfmon /rel`). |
| System "hard error" boxes (optional, Admin) | `HKLM\SYSTEM\CurrentControlSet\Control\Windows` `ErrorMode` = 2 | Logs such errors instead of showing them. Documented in an archived Microsoft article for unattended servers, without naming Windows 10/11: test before relying on it. |

Aethra itself calls this at start-up:

```c
SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)
```

A child process normally inherits that error mode, but mpv sets its own at start-up
(`SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX`, see `osdep/main-fn-win.c` in mpv's source),
which drops `SEM_NOGPFAULTERRORBOX`: a crashing mpv would go through Windows Error Reporting, with its
frozen picture still on stage while WER collects data. Aethra therefore starts every mpv in a Windows
*job object* with `JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION`: a crashing player ends at once,
without WER, and the filler deck below it is on stage immediately. (The job does not kill the players
when Aethra itself exits.) No crash dump is written; Aethra's event log and the per-deck mpv logs are
the record of a crash.

The WER settings above are still recommended as defence in depth: they also cover programs Aethra did
not start (tray tools, updaters, driver helpers) and the "not responding" dialog, which the
`SetErrorMode` flags do not address.

Windows 11 *digital signage mode*, announced by Microsoft in November 2025 to keep error screens off
public displays, has not been evaluated for Aethra.

## 6. Accessibility shortcuts

Each of these opens a dialog or changes the whole screen when triggered by accident. Turning off the
shortcut keeps the feature itself available. The script clears bit `0x4` (`*_HOTKEYACTIVE`, documented
in the Win32 structures) of each `Flags` string (`REG_SZ`) under `HKCU\Control Panel\Accessibility`.

| Shortcut | Feature | Turn the shortcut off | Registry [script] |
|---|---|---|---|
| Shift five times | Sticky Keys | Win11: Settings > Accessibility > Keyboard > *Sticky keys* > *Keyboard shortcut for Sticky keys* off. Win10: Settings > Ease of Access > Keyboard > *Allow the shortcut key to start Sticky Keys* off | `StickyKeys` `Flags` 510 -> 506 |
| Right Shift for 8 s | Filter Keys | Win11: ... > *Filter keys* > *Keyboard shortcut for Filter keys* off. Win10: *Allow the shortcut key to start Filter Keys* off | `Keyboard Response` `Flags` 126 -> 122 |
| Num Lock for 5 s | Toggle Keys | Win11: ... > *Toggle keys* > its keyboard shortcut off. Win10: *Allow the shortcut key to start Toggle Keys* off | `ToggleKeys` `Flags` 62 -> 58 |
| Left Alt + left Shift + Print Screen | High contrast | Script (or Control Panel > Ease of Access Center) | `HighContrast` `Flags` 126 -> 122 |
| Left Alt + left Shift + Num Lock | Mouse Keys | Script (or Control Panel > Ease of Access Center) | `MouseKeys` `Flags` 62 -> 58 |
| Ctrl + Win + Enter | Narrator (opens a window and speaks) | Win11: Settings > Accessibility > Narrator > *Keyboard shortcut for Narrator* off. Win10: *Allow the shortcut key to start Narrator* off | `HKCU\Software\Microsoft\Narrator\NoRoam` `WinEnterLaunchEnabled` = 0 *(community)* |
| Win + Ctrl + C | Color filters (affect every display) | Off by default: check Settings > Accessibility > *Color filters* > *Keyboard shortcut for color filters* (Win10: *Allow the shortcut key to toggle filter on or off*) | - |
| Win + Plus | Magnifier | No setting disables it. `Win+Esc` closes Magnifier | - |
| Win + Ctrl + O | On-screen keyboard | No setting disables it | - |

Values shown are the Windows defaults; the script clears only the shortcut bit and keeps any other bits.
Touch keyboard (touch-screen laptops): Win11 Settings > Time & language > Typing > *Touch keyboard* >
*Show the touch keyboard* = *Never* (verify).

## 7. Game Bar, overlays and flyouts

| Item | Where | Registry / notes |
|---|---|---|
| **Game Bar controller button off** | Settings > Gaming > *Xbox Game Bar* > *Open Game Bar using a button on a controller* off (older Win10: the main *Xbox Game Bar* switch) | - |
| **Captures / Game DVR off** [script] | Win11: Settings > Gaming > *Captures*: background recording off (verify) | `HKCU\System\GameConfigStore` `GameDVR_Enabled` = 0, `HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR` `AppCaptureEnabled` = 0 *(community)*. Pro: *Enables or disables Windows Game Recording and Broadcasting* = Disabled (`HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR` `AllowGameDVR` = 0). |
| **Win+G, Win+Alt+B** | Open Game Bar over the active window; toggle HDR | They stay active while Game Bar is installed. Uninstalling it (`Microsoft.XboxGamingOverlay`) is possible but test what `Win+G` does afterwards (verify). The Pro policy in [section 8](#8-input) disables Windows-key shortcuts altogether. |
| **Volume, brightness and media flyouts** | Hardware keys | Check during rehearsal that they appear on the laptop panel (verify). Recent Win11 builds (KB5065789 and later) let you move the flyout position (Settings > System > Notifications; verify). Don't touch volume, brightness or media keys during the show. Aethra starts mpv with `--media-controls=no`, so mpv does not register with SystemMediaTransportControls and never shows up in the media flyout. The option exists since mpv 0.39; older builds reject it, so keep mpv current. |
| **System sounds off** | Settings > System > Sound > *More sound settings* > *Sounds* > *Sound Scheme* = *No Sounds* | HDMI audio can become the default output; check the default playback device too. |

## 8. Input

- **Mouse cursor**: Aethra hides the cursor over the stage window; still park the mouse on the laptop
  screen before the show. Keep *Show location of pointer when I press the CTRL key* off (Control Panel >
  Mouse > *Pointer Options*; off by default).
- **Touchpad** (Settings > Bluetooth & devices > *Touchpad*; Win10: Settings > Devices > Touchpad): a
  three-finger swipe down shows the desktop, i.e. minimizes the stage window. Set the three- and
  four-finger *Swipes* to *Nothing* (verify; precision touchpads only), consider turning off *Tap with a
  single finger to single-click*, or turn the touchpad off and use a wired mouse.
- **Print Screen** [script]: by default it opens the screen-capture overlay, which dims every display,
  the stage included. Win11: Settings > Bluetooth & devices > *Keyboard* (older builds: Accessibility >
  Keyboard) > *Use the Print screen key to open screen capture* off. Win10: Ease of Access > Keyboard >
  *Use the PrtScn button to open screen snipping* off. `HKCU\Control Panel\Keyboard`
  `PrintScreenKeyForSnippingEnabled` = 0 *(community)*. `Win+Shift+S` still opens it.
- **Peek** [script]: hovering a taskbar thumbnail turns other windows transparent. Run
  `SystemPropertiesPerformance.exe` > *Visual Effects* > untick *Enable Peek*.
  `HKCU\Software\Microsoft\Windows\DWM` `EnableAeroPeek` = 0 *(community)*.
- **Title bar window shake** (Win11) off: Settings > System > *Multitasking* (off by default).
- **Shortcuts that disturb the stage**: `Win+D`, `Win+M`, `Win+Home`, `Win+Down` (minimize),
  `Win+P` (projection mode), `Win+Tab`, `Win+G`, `Win+Alt+B` (HDR), `Win+Ctrl+Shift+B` (resets the
  display; the screens flash black). Keep to Aethra's keys during the show. Pro: *Turn off
  Windows Key hotkeys* (User Configuration > Administrative Templates > Windows Components > File
  Explorer; `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer` `NoWinKeys` = 1, sign
  out to apply). It also disables `Win+P`: change the display mode in Settings instead.

## 9. Network

The REAPER computer and the show laptop are linked by one Ethernet cable. Example addresses:
REAPER `192.168.77.1/24`, show laptop `192.168.77.2/24`, no gateway, no DNS. The REAPER side is
described in [reaper-setup.md](reaper-setup.md).

| Setting | Where | Command / notes |
|---|---|---|
| **Wi-Fi and Bluetooth off** | Settings > Network & internet > *Airplane mode* on, then check that *Wi-Fi* and *Bluetooth* on the same page are still off (Windows remembers radios switched on during airplane mode) | Airplane mode turns off wireless only; wired Ethernet keeps working (verify on the target machine). Permanently (Admin): `Disable-NetAdapter -Name "Wi-Fi" -Confirm:$false` (name from `Get-NetAdapter`). Use a wired mouse and keyboard. |
| **Static IPv4 on Ethernet** | Win11: Settings > Network & internet > Ethernet > *IP assignment* > *Edit* > *Manual* > IPv4 on: IP `192.168.77.2`, subnet mask `255.255.255.0`, gateway and DNS empty. Win10: Settings > Network & Internet > Ethernet > (network) > *IP settings* > *Edit* (subnet prefix length `24`) | If Settings refuses to save without a gateway, use `ncpa.cpl` > adapter > *Properties* > *IPv4*, or PowerShell (below). |
| **Network category** | A link without a gateway is an *Unidentified network*, which is Public by default | `Set-NetConnectionProfile -InterfaceAlias Ethernet -NetworkCategory Private` does not survive reconnects. Pro: `secpol.msc` > *Network List Manager Policies* > *Unidentified Networks* > *Location type* = *Private*. Simplest: firewall rules with `-Profile Any` (the script does this), so the category does not matter. |
| **Firewall: OSC** [script] | - | Inbound UDP 9000 from the local subnet (below). |
| **Firewall: rtpMIDI** | - | Each rtpMIDI session uses two consecutive UDP ports: the configured one (5004 by default) and the next (5005). |
| **Firewall prompts off** [script] | Control Panel > Windows Defender Firewall > *Change notification settings* > untick *Notify me when Windows Defender Firewall blocks a new app* (all profiles) | `Set-NetFirewallProfile -Profile Domain,Private,Public -NotifyOnListen False`. Programs without an allow rule are then blocked silently, so create the rules first. |
| **Ethernet adapter power saving off** | Device Manager > Network adapters > (Ethernet) > *Power Management*: untick *Allow the computer to turn off this device to save power*; *Advanced*: *Energy Efficient Ethernet* / *Green Ethernet* = *Disabled* (names depend on the driver; verify) | - |

PowerShell equivalents (Admin; check the adapter name with `Get-NetAdapter` first):

```powershell
# Static address, no gateway (removes DHCP / APIPA addresses on that adapter first)
Set-NetIPInterface -InterfaceAlias "Ethernet" -Dhcp Disabled
Get-NetRoute -InterfaceAlias "Ethernet" -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false
Get-NetIPAddress -InterfaceAlias "Ethernet" -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.77.2 -PrefixLength 24

# Firewall: OSC (same rule as the script) and rtpMIDI, from the local subnet only, any profile
New-NetFirewallRule -Name "Aethra-OSC-UDP-9000-In" -DisplayName "Aethra OSC (UDP 9000 in)" `
    -Group "Aethra" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 9000 `
    -RemoteAddress LocalSubnet -Profile Any
New-NetFirewallRule -Name "Aethra-rtpMIDI-UDP-In" -DisplayName "rtpMIDI (UDP 5004-5005 in)" `
    -Group "Aethra" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5004-5005 `
    -RemoteAddress LocalSubnet -Profile Any

# Firewall prompts off
Set-NetFirewallProfile -Profile Domain,Private,Public -NotifyOnListen False

# Undo the rules
Remove-NetFirewallRule -Group "Aethra"
```

**Check for old block rules.** If anyone ever answered or cancelled a "Windows Defender Firewall has
blocked some features of this app" prompt for Python, Aethra or mpv, Windows created inbound *block*
rules (TCP and UDP) for that program, and block rules win over allow rules. List and delete them:

```powershell
Get-NetFirewallApplicationFilter | Where-Object { $_.Program -match 'python|aethra|mpv' } |
    Get-NetFirewallRule | Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }
```

**rtpMIDI** (Tobias Erichsen; the site lists Windows 7 to 10, so verify on Windows 11): it relies on
Apple Bonjour for discovery. On a direct cable, add the peer by hand instead: in rtpMIDI's *Directory*,
press *+* and enter the REAPER computer's address and port (`192.168.77.1`, `5004`). If you do rely on
Bonjour, also allow mDNS (UDP 5353). Don't connect the laptop to the venue Wi-Fi.

## 10. Clock

- Being offline is fine: Windows Time just retries its synchronisation later.
- The wall clock only matters for comparing Aethra's event log with REAPER or recordings afterwards.
  Before going offline, sync both computers (Win11: Settings > Time & language > Date & time > *Sync
  now*; or `w32tm /resync` as Admin) and use the same time zone.

## 11. Maintenance and background work

- **Automatic Maintenance** runs when the machine is idle and on AC power, and a show without
  keyboard input can look idle. Control Panel > System and Security > Security and Maintenance >
  *Maintenance* > *Change maintenance settings*: set the daily time outside show hours (for example
  10:00) and untick the option that lets scheduled maintenance wake the computer. Pro: *Automatic
  Maintenance Activation Boundary* (Windows Components > Maintenance Scheduler). The often-quoted
  `MaintenanceDisabled` registry value is not documented by Microsoft; don't use it.
- **Delivery Optimization**: Win11: Settings > Windows Update > Advanced options > *Delivery
  Optimization* > *Allow downloads from other devices* off (Win10: Settings > Update & Security >
  Delivery Optimization). Pro: `HKLM\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization`
  `DODownloadMode` = 99 (*Simple*, meant for offline machines) or 0 (*HTTP only*); value 100 is
  deprecated.
- **Search indexing**: keep the media folder out of indexed locations (Control Panel > *Indexing
  Options* > *Modify*).
- **OneDrive / Storage Sense**: see [section 0](#0-before-you-start); keep media out of OneDrive.

## 12. Graphics

- **Hardware-accelerated GPU scheduling**: test both ways with the real set and keep the setting with
  zero dropped frames; there is no general answer. Win11: Settings > System > Display > *Graphics* >
  *Advanced graphics settings* (older builds: *Change default graphics settings*) >
  *Hardware-accelerated GPU scheduling*. Win10 2004+: Settings > System > Display > *Graphics
  settings*. Needs a restart. Registry (Admin, *community*):
  `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers` `HwSchMode` = 2 on, 1 off. The switch
  appears only with a supporting GPU and driver.
- **GPU used by mpv** (laptops with two GPUs): Settings > System > Display > *Graphics* > add `mpv.exe`
  (*Add desktop app*) > *Options* > *High performance* (or *Power saving*). The HDMI port may be wired to
  either GPU, so test both and keep the one without dropped frames. Registry *(community)*:
  `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, value name = full path of `mpv.exe`,
  `REG_SZ` data `GpuPreference=2;` (2 high performance, 1 power saving). Applies at the next start of
  mpv.
- **Variable refresh rate** and **Optimizations for windowed games** (Settings > System > Display >
  Graphics) target games; their effect on mpv is untested. Leave the defaults unless a test shows a
  difference.

## 13. The preparation script

[`scripts/windows/prepare-stage-laptop.ps1`](../scripts/windows/prepare-stage-laptop.ps1) applies the
items marked **[script]**: per-user registry values, power plan values and, from an elevated prompt,
the machine-wide items (WER for all users, firewall prompts, OSC firewall rule, optionally Fast
Startup). It works offline, uses only tools that ship with Windows and runs on Windows PowerShell 5.1.

```powershell
cd <repo>\scripts\windows
Unblock-File .\prepare-stage-laptop.ps1     # if the file came from a download or a zip

# 1. Dry run (default): every setting with its current value and what would change. Nothing is changed.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1 -IncludeBattery

# 2. Apply. From "Run as administrator" to include the admin items. Then sign out and in again.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1 -Apply -IncludeBattery

# 3. Undo everything the script changed (all saved backups, newest first).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1 -Revert
```

| Parameter | Effect |
|---|---|
| *(none)* | Dry run. Keep the printed *Before* column: it records the original values. |
| `-Apply` | Change the settings. The previous value of every changed item is saved to `%LOCALAPPDATA%\Aethra\windows-setup\backup-*.json`. |
| `-Revert` | Restore all saved backups, newest first; `-BackupFile <path>` restores one file. |
| `-IncludeBattery` | Also set the on-battery values. Recommended. |
| `-HighPerformancePlan` | Activate the built-in *High performance* plan (if the laptop has it) and write the values there. |
| `-DisableFastStartup` | Admin. Turn Fast Startup off. |
| `-OscPort <n>` | Admin. UDP port of the OSC firewall rule (default 9000; `0` = no rule). |
| `-Scope User` / `-Scope Machine` | Only the items that need no admin rights / only the admin items. |

Per-user values go to the account that runs the script, so run it as the show account. If that account
is not an administrator, run `-Scope User` as the show account and `-Scope Machine` from an elevated
prompt. Everything else in this document is manual.

## 14. Pre-show 5-minute checklist

1. **Power**: charger plugged in and charging; battery charged in case the cable gets pulled.
2. **Network**: airplane mode on, Wi-Fi and Bluetooth off; the Ethernet cable to the REAPER computer
   is connected and Windows shows the Ethernet network.
3. **Interruptions**: Do Not Disturb on (Win10: Focus assist *Alarms only*); Windows Update shows the
   pause running past the show and no pending restart; all other apps closed, nothing unexpected in the
   notification area.
4. **Displays**: `Win+P` is *Extend*; the laptop panel is the main display; the stage output is
   1920x1080 at 50 Hz, scale 100 %, HDR off (Settings > System > Display).
5. **Input**: lid open; mouse cursor parked on the laptop screen; hands off `Win`-key shortcuts.
6. **Aethra**: run `aethra check show.yaml` and fix anything it reports; start Aethra; send one test
   trigger from REAPER (MIDI or OSC), watch the first frames on the stage, then return to the filler or
   black.

## Sources

Microsoft documentation was checked in October 2026. Community and vendor sources were used only
where Microsoft documents a setting in the UI but not its registry value (marked *(community)*), or not
at all.

- Displays: [Multiple monitors](https://support.microsoft.com/en-us/windows/how-to-use-multiple-monitors-in-windows-329c6962-5a4d-b481-7baa-bec9671f728a),
  [Keyboard shortcuts](https://support.microsoft.com/en-us/windows/keyboard-shortcuts-in-windows-dcc61a57-8ff0-cffe-9796-cb9706c75eec),
  [Refresh rate](https://support.microsoft.com/en-us/windows/hardware/display-graphics/change-the-refresh-rate-on-your-monitor-in-windows),
  [Resolution and scale](https://support.microsoft.com/en-us/windows/hardware/display-graphics/change-your-screen-resolution-and-layout-in-windows),
  [HDR](https://support.microsoft.com/en-us/windows/hardware/display-graphics/hdr-settings-in-windows),
  [Brightness, night light, content-adaptive brightness](https://support.microsoft.com/en-us/windows/change-display-brightness-and-color-in-windows-3f67a2f2-5c65-ceca-778b-5858fc007041),
  [KB5027303 (CABC)](https://support.microsoft.com/en-us/help/5027303),
  [Taskbar](https://support.microsoft.com/en-us/windows/experience/personalization/customize-the-taskbar-in-windows),
  [Primary monitor behaviour](https://learn.microsoft.com/en-us/windows/win32/gdi/positioning-objects-on-multiple-display-monitors),
  [Window locations on reconnect (Insider blog)](https://blogs.windows.com/windows-insider/2021/06/28/announcing-the-first-insider-preview-for-windows-11/),
  [DisplaySwitch on 22H2 (Q&A)](https://learn.microsoft.com/en-us/answers/questions/1036148/displayswitchexe-behavior-on-windows-11-22h2.html),
  [Background](https://support.microsoft.com/en-us/windows/experience/personalization/change-the-desktop-background-in-windows),
  [Windows spotlight](https://learn.microsoft.com/en-us/windows/configuration/windows-spotlight/),
  [Intel DPST](https://www.intel.com/content/www/us/en/support/articles/000039392/graphics.html),
  [AMD Vari-Bright](https://www.amd.com/en/resources/support-articles/faqs/DH3-005.html).
- Graphics: [GPU scheduling](https://devblogs.microsoft.com/directx/hardware-accelerated-gpu-scheduling/),
  [Graphics settings page](https://devblogs.microsoft.com/directx/navigating-the-redesigned-graphics-settings-page/),
  [Optimizations for windowed games](https://support.microsoft.com/en-us/windows/hardware/display-graphics/optimizations-for-windowed-games-in-windows-11).
- Power: [powercfg](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options),
  [Power policy settings](https://learn.microsoft.com/en-us/windows/win32/power/power-policy-settings),
  [PowerWriteACValueIndex](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powerwriteacvalueindex),
  [Lid switch close action](https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/power-button-and-lid-settings-lid-switch-close-action),
  [PCI Express ASPM](https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/pci-express-settings-link-state-power-management),
  [USB selective suspend](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/usb-selective-suspend),
  [Modern Standby](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/modern-standby),
  [Power mode](https://support.microsoft.com/en-us/windows/change-the-power-mode-for-your-windows-pc-c2aff038-22c9-f46d-5ca0-78696fdf2de8),
  [Power and sleep settings](https://support.microsoft.com/en-us/windows/how-to-adjust-power-and-sleep-settings-26f623b5-4fcc-4194-863d-b824e5ea7679),
  [Energy saver](https://learn.microsoft.com/en-us/windows-hardware/design/component-guidelines/energy-saver),
  [Fast Startup](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/fast-startup-causes-system-hibernation-shutdown-fail),
  [HiberbootEnabled](https://learn.microsoft.com/en-us/windows/configuration/unified-write-filter/hibernate-once-resume-many-horm),
  [Ultimate Performance (Insider blog)](https://blogs.windows.com/windows-insider/2018/02/14/announcing-windows-10-insider-preview-build-17101-fast-build-17604-skip-ahead/),
  [SetThreadExecutionState](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate),
  [Presence sensing](https://support.microsoft.com/en-US/Windows/privacy/managing-presence-sensing-settings-in-windows-11),
  [Dynamic lock](https://support.microsoft.com/en-us/windows/lock-windows-automatically-d0a5f536-74ac-0859-820a-4140dac9fcaf),
  [mpv manual](https://mpv.io/manual/stable/).
- Notifications and updates: [Notifications and Do Not Disturb](https://support.microsoft.com/en-us/windows/notifications-and-do-not-disturb-in-windows-feeca47f-0baf-5680-16f0-8801db1a8466),
  [Focus](https://support.microsoft.com/windows/how-to-use-focus-in-windows-11-cbcc9ddb-8164-43fa-8919-b9a2af072382),
  [Toast policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-wpn),
  [Experience policies](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-experience),
  [Widgets policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-newsandinterests),
  [Feeds policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-feeds),
  [UCPD and TaskbarDa](https://oofhours.com/2025/05/02/what-is-windows-11s-new-ucpd-feature/),
  [Copilot](https://learn.microsoft.com/en-us/windows/client-management/manage-windows-copilot),
  [Windows Security policies](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-windowsdefendersecuritycenter),
  [Defender scan schedule](https://learn.microsoft.com/en-us/defender-endpoint/schedule-antivirus-scans-powershell),
  [Tamper protection](https://learn.microsoft.com/en-us/defender-endpoint/tamper-protection-overview),
  [Defender notifications](https://learn.microsoft.com/en-us/defender-endpoint/configure-notifications-microsoft-defender-antivirus),
  [Pause updates](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/pause-updates-in-windows),
  [Active hours](https://support.microsoft.com/en-us/windows/keep-your-pc-up-to-date-with-active-hours-de79813c-7919-5fed-080f-0871c7bd9bde),
  [Update policies](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-update),
  [Smart App Control](https://support.microsoft.com/en-us/topic/what-is-smart-app-control-285ea03d-fa88-4d56-882e-6698afdb7003),
  [OneDrive Files On-Demand](https://support.microsoft.com/en-us/onedrive/save-disk-space-with-onedrive-files-on-demand-for-windows).
- Error dialogs: [WER settings](https://learn.microsoft.com/en-us/windows/win32/wer/wer-settings),
  [WER policies](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-errorreporting),
  [Using WER](https://learn.microsoft.com/en-us/windows/win32/wer/using-wer),
  [DontShowUI and the "stopped working" dialog](https://learn.microsoft.com/en-us/archive/blogs/alejacma/how-to-disable-the-pop-up-that-windows-shows-when-an-app-crashes),
  [SetErrorMode](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-seterrormode),
  [Process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags),
  [Python subprocess](https://docs.python.org/3/library/subprocess.html),
  [Hard error popup mode](https://learn.microsoft.com/en-us/troubleshoot/windows/win32/hard-error-popup-mode),
  [Digital signage mode announcement](https://blogs.windows.com/windowsexperience/2025/11/18/preparing-for-whats-next-windows-security-and-resiliency-innovations-help-organizations-mitigate-risks-recover-faster-and-prepare-for-the-era-of-ai/).
- Accessibility and input: [Accessibility shortcuts](https://support.microsoft.com/en-us/windows/windows-keyboard-shortcuts-for-accessibility-021bcb62-45c8-e4ef-1e4f-41b8c1fc87fd),
  [STICKYKEYS](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-stickykeys),
  [FILTERKEYS](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-filterkeys),
  [TOGGLEKEYS](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-togglekeys),
  [HIGHCONTRAST](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-highcontrastw),
  [MOUSEKEYS](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mousekeys),
  [Sticky Keys 510/506](https://learn.microsoft.com/en-us/archive/blogs/embedded/stickykeys),
  [Narrator settings](https://support.microsoft.com/en-us/accessibility/windows/narrator/chapter-7-customizing-narrator),
  [Narrator shortcut registry (Winaero)](https://winaero.com/disable-narrator-keyboard-shortcut-windows-10/),
  [Game DVR policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-applicationmanagement),
  [Touchpad gestures](https://support.microsoft.com/en-us/windows/touchpad-gestures-for-windows-a9d28305-4818-a5df-4e2b-e5590f850741),
  [NoWinKeys policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-windowsexplorer),
  [Print Screen and screen capture (Q&A)](https://learn.microsoft.com/en-us/answers/questions/5775212/missing-use-the-print-screen-key-to-open-snipping),
  [Show desktop corner (Winaero)](https://winaero.com/enable-show-desktop-on-taskbar),
  [mpv releases](https://github.com/mpv-player/mpv/releases).
- Network and maintenance: [Network settings and airplane mode](https://support.microsoft.com/en-us/windows/essential-network-settings-and-tasks-in-windows-f21a9bbc-c582-55cd-35e0-73431160a1b9),
  [New-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/new-netipaddress),
  [Set-NetIPInterface](https://learn.microsoft.com/en-us/powershell/module/nettcpip/set-netipinterface),
  [Set-NetConnectionProfile](https://learn.microsoft.com/en-us/powershell/module/netconnection/set-netconnectionprofile),
  [Network List Manager policies](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-R2-and-2012/jj966256%28v=ws.11%29),
  [Firewall rules and prompts](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules),
  [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule),
  [Set-NetFirewallProfile](https://learn.microsoft.com/en-us/powershell/module/netsecurity/set-netfirewallprofile),
  [Disable-NetAdapter](https://learn.microsoft.com/en-us/powershell/module/netadapter/disable-netadapter),
  [rtpMIDI tutorial](https://www.tobias-erichsen.de/software/rtpmidi/rtpmidi-tutorial.html),
  [rtpMIDI](https://www.tobias-erichsen.de/software/rtpmidi.html),
  [Windows Time tools](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings),
  [Automatic Maintenance](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-maintenence),
  [Delivery Optimization](https://learn.microsoft.com/en-us/windows/deployment/do/waas-delivery-optimization-reference),
  [Background apps policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-privacy).
