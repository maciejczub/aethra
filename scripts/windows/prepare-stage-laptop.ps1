#Requires -Version 5.1
<#
.SYNOPSIS
    Applies a conservative, reversible subset of docs/windows-setup.md to the
    Aethra show laptop. DRY RUN BY DEFAULT.

.DESCRIPTION
    Without -Apply nothing is changed: the script prints every setting it knows,
    the current value, the target value and what it would do.

    What it can change:
      * Per-user registry values (HKCU): toast notifications, tips and
        suggestions, Windows Error Reporting dialogs, accessibility and
        Narrator shortcut keys, Print Screen screen-capture overlay, Game DVR,
        taskbar on all displays, Peek, AutoPlay. It also reports (but does not
        change) a configured screen saver.
      * Power plan values: display / dim / sleep / hibernate / disk timeouts,
        lid, power button and sleep button actions, USB selective suspend,
        PCI Express Link State Power Management, and whether programs may keep
        the display and system awake. Only the plugged-in (AC) values, unless
        -IncludeBattery is given. They are written to the active plan, or to
        "High performance" with -HighPerformancePlan.
      * Machine-wide items that need an elevated prompt ("admin"; skipped with a
        note when not elevated): Windows Error Reporting dialogs for all users,
        Windows Firewall "blocked app" prompts, an inbound firewall rule for OSC
        (UDP), and Fast Startup (only with -DisableFastStartup).

    What it never does: download anything, restart Windows or Explorer, touch
    Microsoft Defender protection, stop Windows Update, or change the display
    layout. Everything else is a manual step in docs/windows-setup.md.

    PER-USER ITEMS are written for the account that runs the script. Run it as
    the account that will run the show. If that account is not an administrator,
    run "-Scope User" as the show account and "-Scope Machine" from an elevated
    prompt, so that the per-user part does not land in the admin's profile.

    Most per-user changes take effect after signing out and in again. Power plan
    and firewall changes take effect immediately.

    REVERTING: every -Apply run stores the previous value of each item it
    changes in a JSON file under %LOCALAPPDATA%\Aethra\windows-setup (the path is
    printed). "-Revert" restores all such files, newest first, and renames each
    fully restored file to "reverted-<name>". "-Revert -BackupFile <path>"
    restores a single file. If a backup file is lost, use the "Before" column
    that every run prints to restore values by hand.

.PARAMETER Apply
    Change the settings. Without it the script only reports (dry run).

.PARAMETER Revert
    Restore the values saved by earlier -Apply runs (all backups, newest first).

.PARAMETER BackupFile
    With -Revert: restore only this backup file.

.PARAMETER Scope
    All (default), User (items that do not need admin: per-user registry values
    and power plan values) or Machine (only the items that need admin).

.PARAMETER IncludeBattery
    Also set the on-battery (DC) power values, so that a pulled power cable does
    not dim or switch off the display, put the laptop to sleep, or act on the
    lid or power button. Recommended for shows. Note: with this switch a closed
    laptop on battery keeps running (and draining) until you shut it down.

.PARAMETER HighPerformancePlan
    Write the power values to the built-in "High performance" plan and activate
    it. Many Modern Standby laptops only offer "Balanced"; the script then keeps
    the active plan and tells you so.

.PARAMETER DisableFastStartup
    Admin. Turn Fast Startup off (HiberbootEnabled = 0) so that every boot fully
    initialises drivers and devices.

.PARAMETER OscPort
    Admin. UDP port for the inbound OSC firewall rule (default 9000). Must match
    the OSC port in the Aethra configuration. 0 skips the rule.

.PARAMETER BackupDir
    Folder for backup files. Default: %LOCALAPPDATA%\Aethra\windows-setup

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1
    Dry run: shows what would change. Nothing is modified.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1 -Apply -IncludeBattery
    Applies everything. Run it from "Run as administrator" to include admin items.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\prepare-stage-laptop.ps1 -Revert
    Restores the values saved by earlier -Apply runs.

.NOTES
    Written for and tested with Windows PowerShell 5.1 on Windows 10/11 (it
    should also run in PowerShell 7, untested). Uses only built-in tools: the
    registry provider, powercfg.exe and the NetSecurity module that ships with
    Windows. Works offline. Keep this file ASCII-only.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Revert,
    [string]$BackupFile,
    [ValidateSet('All', 'User', 'Machine')]
    [string]$Scope = 'All',
    [switch]$IncludeBattery,
    [switch]$HighPerformancePlan,
    [switch]$DisableFastStartup,
    [ValidateRange(0, 65535)]
    [int]$OscPort = 9000,
    [string]$BackupDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($Apply -and $Revert) { throw 'Use either -Apply or -Revert, not both.' }
if ($BackupFile -and -not $Revert) { throw '-BackupFile is only used together with -Revert.' }
if (-not $BackupDir) { $BackupDir = Join-Path $env:LOCALAPPDATA 'Aethra\windows-setup' }

$GuidPattern = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
$HighPerformanceGuid = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'   # alias SCHEME_MIN

# =============================================================================
# General helpers
# =============================================================================

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal -ArgumentList $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WindowsSummary {
    $cv = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $build = 0
    [void][int]::TryParse([string]$cv.CurrentBuild, [ref]$build)
    # ProductName still says "Windows 10" on Windows 11; the build number does not lie.
    $family = 'Windows 10'
    if ($build -ge 22000) { $family = 'Windows 11' }
    $release = ''
    foreach ($name in @('DisplayVersion', 'ReleaseId')) {
        if ($cv.PSObject.Properties[$name]) { $release = [string]$cv.$name; break }
    }
    $edition = ''
    if ($cv.PSObject.Properties['EditionID']) { $edition = [string]$cv.EditionID }
    return ('{0} {1} (build {2}, edition {3})' -f $family, $release, $build, $edition)
}

# =============================================================================
# Registry helpers
# =============================================================================

function Get-RegState {
    # Reads one registry value. Never changes anything.
    param([string]$Path, [string]$Name)
    $state = [pscustomobject]@{ KeyExists = $false; Exists = $false; Kind = $null; Data = $null }
    if (Test-Path -LiteralPath $Path) {
        $state.KeyExists = $true
        $key = Get-Item -LiteralPath $Path
        if (@($key.GetValueNames()) -contains $Name) {
            $state.Exists = $true
            $state.Kind = $key.GetValueKind($Name).ToString()
            $state.Data = $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        }
    }
    return $state
}

function Set-RegValue {
    param([string]$Path, [string]$Name, [string]$Kind, $Data)
    if (-not (Test-Path -LiteralPath $Path)) {
        # Create the key only when it is missing: "New-Item -Force" on an
        # EXISTING registry key would delete all values in it.
        New-Item -Path $Path -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $Path -Name $Name -PropertyType $Kind -Value $Data -Force | Out-Null
}

function Remove-RegValue {
    param([string]$Path, [string]$Name, [bool]$RemoveKeyIfEmpty)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $key = Get-Item -LiteralPath $Path
    if (@($key.GetValueNames()) -contains $Name) {
        Remove-ItemProperty -LiteralPath $Path -Name $Name
    }
    if ($RemoveKeyIfEmpty) {
        $key = Get-Item -LiteralPath $Path
        if ($key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0) {
            Remove-Item -LiteralPath $Path
        }
    }
}

# =============================================================================
# Power plan helpers (powercfg.exe)
# powercfg output is localized, so only GUIDs and 0x... numbers are parsed.
# By default the power plans' security descriptors let members of Users read
# and write plan values (see "powercfg /getsecuritydescriptor <plan GUID>"),
# so these items do not need an elevated prompt.
# =============================================================================

function Invoke-PowerCfg {
    param([string[]]$Arguments)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & powercfg.exe @Arguments 2>&1 | ForEach-Object { [string]$_ }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    return [pscustomobject]@{ ExitCode = $code; Text = (@($lines) -join "`n") }
}

function Get-PowerSchemes {
    # Returns a hashtable: plan GUID (lower case) -> plan name.
    $result = @{}
    $r = Invoke-PowerCfg -Arguments @('/list')
    if ($r.ExitCode -ne 0) { return $result }
    foreach ($m in [regex]::Matches($r.Text, "($GuidPattern)\s+\(([^)]*)\)")) {
        $result[$m.Groups[1].Value.ToLowerInvariant()] = $m.Groups[2].Value
    }
    return $result
}

function Get-ActiveSchemeGuid {
    $r = Invoke-PowerCfg -Arguments @('/getactivescheme')
    $m = [regex]::Match($r.Text, $GuidPattern)
    if ($r.ExitCode -eq 0 -and $m.Success) { return $m.Value.ToLowerInvariant() }
    return $null
}

function Get-PowerValue {
    # Returns the current AC and DC index of one setting in one plan, or $null
    # if the setting does not exist on this PC. "/query" hides settings marked
    # as hidden (lid and button actions on some machines), so "/qh" (query
    # including hidden settings; not in the powercfg reference, but used in
    # Microsoft's power slider OEM documentation) is the fallback.
    param([string]$Scheme, [string]$SubGroup, [string]$Setting)
    foreach ($option in @('/query', '/qh')) {
        $r = Invoke-PowerCfg -Arguments @($option, $Scheme, $SubGroup)
        if ($r.ExitCode -ne 0) { continue }
        $start = $r.Text.IndexOf($Setting, [StringComparison]::OrdinalIgnoreCase)
        if ($start -lt 0) { continue }
        $block = $r.Text.Substring($start + $Setting.Length)
        $next = [regex]::Match($block, $GuidPattern)
        if ($next.Success) { $block = $block.Substring(0, $next.Index) }
        # The last two hex numbers of a setting block are the AC and DC values.
        $hex = [regex]::Matches($block, '0x([0-9a-fA-F]{1,8})')
        if ($hex.Count -lt 2) { continue }
        return [pscustomobject]@{
            AC = [Convert]::ToInt64($hex[$hex.Count - 2].Groups[1].Value, 16)
            DC = [Convert]::ToInt64($hex[$hex.Count - 1].Groups[1].Value, 16)
        }
    }
    return $null
}

function Set-PowerValue {
    param([string]$Scheme, [string]$SubGroup, [string]$Setting, [string]$Line, [long]$Value)
    $option = '/setacvalueindex'
    if ($Line -eq 'DC') { $option = '/setdcvalueindex' }
    $r = Invoke-PowerCfg -Arguments @($option, $Scheme, $SubGroup, $Setting, [string]$Value)
    if ($r.ExitCode -ne 0) { throw ('powercfg {0} failed: {1}' -f $option, $r.Text.Trim()) }
}

function Set-ActiveScheme {
    # Also used to re-apply the active plan after its values were changed.
    param([string]$Scheme)
    $r = Invoke-PowerCfg -Arguments @('/setactive', $Scheme)
    if ($r.ExitCode -ne 0) { throw ('powercfg /setactive failed: {0}' -f $r.Text.Trim()) }
}

function Format-SchemeName {
    param([string]$Guid)
    if (-not $Guid) { return '(unknown)' }
    if ($Schemes.ContainsKey($Guid)) { return ('{0} [{1}]' -f $Schemes[$Guid], $Guid.Substring(0, 8)) }
    return $Guid
}

function Format-PowerValue {
    param($Item, [long]$Value)
    if ($null -ne $Item.Labels) {
        if ($Value -ge 0 -and $Value -le 255 -and $Item.Labels.ContainsKey([int]$Value)) {
            return ('{0} = {1}' -f $Value, $Item.Labels[[int]$Value])
        }
        return [string]$Value
    }
    if ($Value -eq 0) { return '0 = never' }
    return ('{0} s' -f $Value)
}

# =============================================================================
# Item list
# =============================================================================

$Items = New-Object System.Collections.Generic.List[object]

function New-ItemRecord {
    # One uniform record per setting, so that every field always exists.
    param([string]$Kind, [string]$Area, [string]$Name, [string]$ScopeLabel, [bool]$NeedsAdmin)
    return [pscustomobject]@{
        Kind = $Kind; Area = $Area; Name = $Name; ScopeLabel = $ScopeLabel; NeedsAdmin = $NeedsAdmin
        Path = $null; ValueName = $null; Type = $null; Value = $null; ClearBits = 0
        SubGroup = $null; Setting = $null; Line = $null; Labels = $null
        RuleName = $null; DisplayName = $null; Port = 0; Profile = $null; Hint = $null
    }
}

function Add-RegItem {
    # Sets a registry value to a fixed value, or (with -ClearBits) clears bits
    # in a numeric REG_SZ "Flags" value and keeps the user's other bits.
    param(
        [string]$Area, [string]$Name, [string]$Path, [string]$ValueName,
        [string]$Type = 'DWord', $Value = $null, [int]$ClearBits = 0
    )
    $machine = $Path -like 'HKLM:*'
    $label = 'User'
    if ($machine) { $label = 'Machine' }
    $item = New-ItemRecord -Kind 'Registry' -Area $Area -Name $Name -ScopeLabel $label -NeedsAdmin $machine
    $item.Path = $Path; $item.ValueName = $ValueName; $item.Type = $Type
    $item.Value = $Value; $item.ClearBits = $ClearBits
    $Items.Add($item)
}

function Add-RegCheck {
    # Report-only item: OK when the value is absent or empty, otherwise the
    # script asks for a manual change (it never writes this value).
    param([string]$Area, [string]$Name, [string]$Path, [string]$ValueName, [string]$Hint)
    $item = New-ItemRecord -Kind 'RegCheck' -Area $Area -Name $Name -ScopeLabel 'User' -NeedsAdmin $false
    $item.Path = $Path; $item.ValueName = $ValueName; $item.Hint = $Hint
    $Items.Add($item)
}

function Add-PowerItem {
    # One item per power line: AC always, DC only with -IncludeBattery.
    param([string]$Area, [string]$Name, [string]$SubGroup, [string]$Setting, [long]$Value, [hashtable]$Labels = $null)
    $lines = @('AC')
    if ($IncludeBattery) { $lines += 'DC' }
    foreach ($line in $lines) {
        $item = New-ItemRecord -Kind 'Power' -Area $Area -Name $Name -ScopeLabel ('Plan ' + $line) -NeedsAdmin $false
        $item.SubGroup = $SubGroup; $item.Setting = $Setting; $item.Line = $line
        $item.Value = $Value; $item.Labels = $Labels
        $Items.Add($item)
    }
}

# Value labels for enumerated power settings (indexes as shown by powercfg).
$ActionLabels = @{ 0 = 'do nothing'; 1 = 'sleep'; 2 = 'hibernate'; 3 = 'shut down'; 4 = 'turn off display' }
$OnOffLabels = @{ 0 = 'disabled'; 1 = 'enabled' }
$AspmLabels = @{ 0 = 'off'; 1 = 'moderate savings'; 2 = 'maximum savings' }
$YesNoLabels = @{ 0 = 'no'; 1 = 'yes' }

$CDM = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
$Accessibility = 'HKCU:\Control Panel\Accessibility'

function Build-ItemList {
    # ---------------------------------------------------------------- Power plan
    if ($HighPerformancePlan -and $Schemes.ContainsKey($HighPerformanceGuid)) {
        # Make "High performance" the active plan (done after its values are written).
        $item = New-ItemRecord -Kind 'PowerScheme' -Area 'Power' -Name 'Active power plan' -ScopeLabel 'Machine' -NeedsAdmin $false
        $item.Value = $HighPerformanceGuid
        $Items.Add($item)
    }
    # "Turn off display after": never. (SUB_VIDEO / VIDEOIDLE, seconds, 0 = never)
    Add-PowerItem 'Power' 'Turn off display after' '7516b95f-f776-4464-8c53-06167f40cc99' '3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e' 0
    # "Dim display after": never. Laptop panel dimming. (SUB_VIDEO / VIDEODIM)
    Add-PowerItem 'Power' 'Dim display after' '7516b95f-f776-4464-8c53-06167f40cc99' '17aaa29b-8b43-4b94-aafe-35f64daaf1ee' 0
    # "Sleep after": never. (SUB_SLEEP / STANDBYIDLE)
    Add-PowerItem 'Power' 'Sleep after' '238c9fa8-0aad-41ed-83f4-97be242c8f20' '29f6c1db-86da-48c5-9fdb-f2b67b1f44da' 0
    # "Hibernate after": never. (SUB_SLEEP / HIBERNATEIDLE) On Modern Standby
    # laptops 0 on battery means "adaptive hibernate" after the PC has been in
    # standby; with sleep set to never the PC does not get there.
    Add-PowerItem 'Power' 'Hibernate after' '238c9fa8-0aad-41ed-83f4-97be242c8f20' '9d7815a6-7ee4-497e-8888-515a05f02364' 0
    # "Turn off hard disk after": never, so a spinning media disk never has to
    # spin up before the next clip. (SUB_DISK / DISKIDLE)
    Add-PowerItem 'Power' 'Turn off hard disk after' '0012ee47-9041-4b5d-9b77-535fba8b1442' '6738e2c4-e8a5-4a42-b16a-e040e769756e' 0
    # Lid close action: do nothing, so closing or bumping the lid does not stop
    # the show. (SUB_BUTTONS / LIDACTION)
    Add-PowerItem 'Power' 'Lid close action' '4f971e89-eebd-4455-a8de-9e59040e7347' '5ca83367-6e45-459f-a27b-476b1d01c936' 0 $ActionLabels
    # Power button action: do nothing (a 4+ second hold still forces power off).
    # (SUB_BUTTONS / PBUTTONACTION)
    Add-PowerItem 'Power' 'Power button action' '4f971e89-eebd-4455-a8de-9e59040e7347' '7648efa3-dd9c-4e3e-b566-50f929386280' 0 $ActionLabels
    # Sleep button action (Fn sleep keys): do nothing. (SUB_BUTTONS / SBUTTONACTION)
    Add-PowerItem 'Power' 'Sleep button action' '4f971e89-eebd-4455-a8de-9e59040e7347' '96996bc0-ad50-47ec-923b-6f41874dd9eb' 0 $ActionLabels
    # USB selective suspend: disabled, so idle USB MIDI interfaces are not
    # suspended between songs. (USB settings, no alias)
    Add-PowerItem 'Power' 'USB selective suspend' '2a737441-1930-4402-8d77-b2bebba308a3' '48e6b7a6-50f5-4782-a5d4-53bb8f07e226' 0 $OnOffLabels
    # PCI Express Link State Power Management: off (no ASPM link power saving
    # on GPU, NVMe and network links). (SUB_PCIEXPRESS / ASPM)
    Add-PowerItem 'Power' 'PCIe link state power mgmt' '501a4d13-42af-4429-9fd1-a8218c268e20' 'ee12f906-d277-404b-b6da-e5fa1a576df5' 0 $AspmLabels
    # Honour programs' "display required" requests (mpv keeps the display on
    # while playing). Default yes; a policy or OEM tool may have turned it off.
    # (SUB_VIDEO / ALLOWDISPLAY)
    Add-PowerItem 'Power' 'Allow display-on requests' '7516b95f-f776-4464-8c53-06167f40cc99' 'a9ceb8da-cd46-44fb-a98b-02af69de4623' 1 $YesNoLabels
    # Honour programs' "system required" requests (no idle sleep while mpv
    # plays). Default yes. (SUB_SLEEP / SYSTEMREQUIRED)
    Add-PowerItem 'Power' 'Allow system-on requests' '238c9fa8-0aad-41ed-83f4-97be242c8f20' 'a4b195f5-8225-47d8-8012-9d41369786e2' 1 $YesNoLabels

    # ------------------------------------------------------------- Notifications
    # Microsoft documents these switches in Settings, not their registry values;
    # the values below are the ones the Settings switches write, as documented
    # by long-standing community tutorials (see docs/windows-setup.md).
    # Master switch for toast notifications (banners) of apps and Windows for
    # this user: Settings > System > Notifications > Notifications = Off.
    Add-RegItem 'Notifications' 'Toast notifications' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications' 'ToastEnabled' 'DWord' 0
    # "Get tips and suggestions when using Windows" = Off.
    Add-RegItem 'Notifications' 'Tips and suggestions' $CDM 'SubscribedContent-338389Enabled' 'DWord' 0
    # "Show the Windows welcome experience after updates ..." = Off.
    Add-RegItem 'Notifications' 'Welcome experience' $CDM 'SubscribedContent-310093Enabled' 'DWord' 0
    # "Suggest ways to get the most out of Windows and finish setting up this
    # device" = Off (full-screen "Let's finish setting up" prompts).
    Add-RegItem 'Notifications' 'Finish-setup prompts' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement' 'ScoobeSystemSettingEnabled' 'DWord' 0
    # "Show me suggested content in the Settings app" = Off (three values).
    Add-RegItem 'Notifications' 'Settings suggestions (1/3)' $CDM 'SubscribedContent-338393Enabled' 'DWord' 0
    Add-RegItem 'Notifications' 'Settings suggestions (2/3)' $CDM 'SubscribedContent-353694Enabled' 'DWord' 0
    Add-RegItem 'Notifications' 'Settings suggestions (3/3)' $CDM 'SubscribedContent-353696Enabled' 'DWord' 0
    # Windows 10 "Show suggestions occasionally in Start" = Off.
    Add-RegItem 'Notifications' 'Start suggestions (Win10)' $CDM 'SubscribedContent-338388Enabled' 'DWord' 0
    # Lock screen "Get fun facts, tips, tricks, and more" = Off (two values).
    Add-RegItem 'Notifications' 'Lock screen tips (1/2)' $CDM 'RotatingLockScreenOverlayEnabled' 'DWord' 0
    Add-RegItem 'Notifications' 'Lock screen tips (2/2)' $CDM 'SubscribedContent-338387Enabled' 'DWord' 0
    # File Explorer "Show sync provider notifications" = Off (OneDrive ads).
    Add-RegItem 'Notifications' 'Explorer sync provider ads' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' 'ShowSyncProviderNotifications' 'DWord' 0
    # AutoPlay off: no "choose what happens" prompt when a USB stick is plugged in.
    Add-RegItem 'Notifications' 'AutoPlay' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers' 'DisableAutoplay' 'DWord' 1

    # ------------------------------------------------------------- Error dialogs
    # Windows Error Reporting: never show crash/hang UI for this user's programs.
    Add-RegItem 'Error dialogs' 'WER crash dialogs (user)' 'HKCU:\Software\Microsoft\Windows\Windows Error Reporting' 'DontShowUI' 'DWord' 1
    # Same for all users and system processes (admin).
    Add-RegItem 'Error dialogs' 'WER crash dialogs (machine)' 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting' 'DontShowUI' 'DWord' 1

    # ------------------------------------------------------------- Accessibility
    # Clear bit 0x4 (xxx_HOTKEYACTIVE) of each "Flags" string: the keyboard
    # shortcut can no longer switch the feature on or open its dialog. The
    # feature itself stays available in Settings. Applies after sign-in.
    # Sticky Keys: press Shift five times.
    Add-RegItem 'Accessibility' 'Sticky Keys shortcut' "$Accessibility\StickyKeys" 'Flags' 'String' -ClearBits 4
    # Filter Keys: hold right Shift for 8 seconds.
    Add-RegItem 'Accessibility' 'Filter Keys shortcut' "$Accessibility\Keyboard Response" 'Flags' 'String' -ClearBits 4
    # Toggle Keys: hold Num Lock for 5 seconds.
    Add-RegItem 'Accessibility' 'Toggle Keys shortcut' "$Accessibility\ToggleKeys" 'Flags' 'String' -ClearBits 4
    # High contrast: left Alt + left Shift + Print Screen.
    Add-RegItem 'Accessibility' 'High contrast shortcut' "$Accessibility\HighContrast" 'Flags' 'String' -ClearBits 4
    # Mouse Keys: left Alt + left Shift + Num Lock.
    Add-RegItem 'Accessibility' 'Mouse Keys shortcut' "$Accessibility\MouseKeys" 'Flags' 'String' -ClearBits 4
    # Narrator: Ctrl + Win + Enter no longer starts the screen reader (which
    # opens a window and speaks over the default audio output). Same as
    # Settings > Accessibility > Narrator > "Keyboard shortcut for Narrator" = Off.
    Add-RegItem 'Accessibility' 'Narrator shortcut' 'HKCU:\Software\Microsoft\Narrator\NoRoam' 'WinEnterLaunchEnabled' 'DWord' 0

    # --------------------------------------------------------------------- Input
    # Print Screen opens the screen-capture overlay, which dims every display,
    # the stage output included. Off = Print Screen copies to the clipboard.
    Add-RegItem 'Input' 'PrtScn opens screen capture' 'HKCU:\Control Panel\Keyboard' 'PrintScreenKeyForSnippingEnabled' 'DWord' 0

    # ---------------------------------------------------------- Gaming overlays
    # Game DVR / background recording off.
    Add-RegItem 'Gaming' 'Game DVR' 'HKCU:\System\GameConfigStore' 'GameDVR_Enabled' 'DWord' 0
    # Game Bar captures off.
    Add-RegItem 'Gaming' 'Game Bar captures' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' 'AppCaptureEnabled' 'DWord' 0

    # ----------------------------------------------------------- Desktop, taskbar
    # "Show my taskbar on all displays" = Off: no taskbar on the stage output.
    Add-RegItem 'Desktop' 'Taskbar on all displays' 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' 'MMTaskbarEnabled' 'DWord' 0
    # Peek off: hovering a taskbar thumbnail must not turn other windows
    # (the stage window) transparent.
    Add-RegItem 'Desktop' 'Peek' 'HKCU:\Software\Microsoft\Windows\DWM' 'EnableAeroPeek' 'DWord' 0
    # Screen saver: report only. A configured screen saver is stored as
    # SCRNSAVE.EXE; "(None)" removes it. Set it in the screen saver dialog.
    Add-RegCheck 'Desktop' 'Screen saver' 'HKCU:\Control Panel\Desktop' 'SCRNSAVE.EXE' 'set Screen saver to (None), see docs section 3'

    # --------------------------------------------------- Machine-wide (admin only)
    if ($DisableFastStartup) {
        # Fast Startup off: shutdown no longer hibernates the kernel session,
        # so every boot initialises drivers from scratch.
        Add-RegItem 'Power' 'Fast Startup' 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' 'HiberbootEnabled' 'DWord' 0
    }
    # Windows Firewall: do not show "Windows Defender Firewall has blocked some
    # features of this app" prompts; blocked apps are blocked silently.
    foreach ($fwProfile in @('Domain', 'Private', 'Public')) {
        $item = New-ItemRecord -Kind 'FirewallNotify' -Area 'Network' -Name ('Firewall prompts (' + $fwProfile + ')') -ScopeLabel 'Machine' -NeedsAdmin $true
        $item.Profile = $fwProfile
        $Items.Add($item)
    }
    # Inbound allow rule for OSC triggers from the REAPER machine (local subnet only).
    if ($OscPort -gt 0) {
        $item = New-ItemRecord -Kind 'FirewallRule' -Area 'Network' -Name ('OSC firewall rule UDP ' + $OscPort) -ScopeLabel 'Machine' -NeedsAdmin $true
        $item.RuleName = 'Aethra-OSC-UDP-' + $OscPort + '-In'
        $item.DisplayName = 'Aethra OSC (UDP ' + $OscPort + ' in)'
        $item.Port = $OscPort
        $Items.Add($item)
    }
}

# =============================================================================
# Reading, applying and restoring items
# =============================================================================

function Get-ItemStatus {
    # Current state of one item. Never changes anything.
    param($Item)
    $st = [pscustomobject]@{
        Available = $true; Compliant = $false; Current = ''; Target = ''
        TargetData = $null; Original = $null; Detail = ''
    }
    switch ($Item.Kind) {
        'Registry' {
            $reg = Get-RegState -Path $Item.Path -Name $Item.ValueName
            $st.Original = $reg
            $st.Current = '(not set)'
            if ($reg.Exists) { $st.Current = [string]$reg.Data }
            if ($Item.ClearBits -ne 0) {
                $flags = 0
                if (-not $reg.Exists -or $reg.Kind -ne 'String' -or -not [int]::TryParse([string]$reg.Data, [ref]$flags)) {
                    $st.Available = $false
                    $st.Detail = 'Flags value missing or not numeric'
                } else {
                    $newFlags = $flags -band (-bnot $Item.ClearBits)
                    $st.TargetData = [string]$newFlags
                    $st.Target = [string]$newFlags
                    $st.Compliant = ($newFlags -eq $flags)
                }
            } else {
                $st.TargetData = $Item.Value
                $st.Target = [string]$Item.Value
                if ($reg.Exists -and @('DWord', 'String') -notcontains $reg.Kind) {
                    $st.Available = $false
                    $st.Detail = 'unexpected value type ' + $reg.Kind
                } else {
                    $st.Compliant = $reg.Exists -and ($reg.Kind -eq $Item.Type) -and ([string]$reg.Data -eq [string]$Item.Value)
                }
            }
        }
        'RegCheck' {
            $reg = Get-RegState -Path $Item.Path -Name $Item.ValueName
            $st.Original = $reg
            $st.Current = '(not set)'
            if ($reg.Exists) { $st.Current = [string]$reg.Data }
            $st.Target = '(not set)'
            $st.Compliant = (-not $reg.Exists) -or ([string]$reg.Data -eq '')
        }
        'Power' {
            $st.Target = Format-PowerValue -Item $Item -Value $Item.Value
            $st.TargetData = $Item.Value
            $pv = $null
            if ($TargetScheme) { $pv = Get-PowerValue -Scheme $TargetScheme -SubGroup $Item.SubGroup -Setting $Item.Setting }
            if ($null -eq $pv) {
                $st.Available = $false
                $st.Current = '-'
                $st.Detail = 'setting not present on this PC'
            } else {
                $value = $pv.AC
                if ($Item.Line -eq 'DC') { $value = $pv.DC }
                $st.Original = $value
                $st.Current = Format-PowerValue -Item $Item -Value $value
                $st.Compliant = ($value -eq $Item.Value)
            }
        }
        'PowerScheme' {
            $st.Original = $ActiveScheme
            $st.Current = Format-SchemeName -Guid $ActiveScheme
            $st.Target = Format-SchemeName -Guid $Item.Value
            $st.TargetData = $Item.Value
            $st.Compliant = ($ActiveScheme -eq $Item.Value)
        }
        'FirewallRule' {
            $st.Target = ('allow UDP {0} in from local subnet' -f $Item.Port)
            try {
                $rule = Get-NetFirewallRule -Name $Item.RuleName -ErrorAction SilentlyContinue
                if ($null -eq $rule) {
                    $st.Current = 'no rule'
                } elseif ([string]$rule.Enabled -ne 'True') {
                    $st.Current = 'rule disabled'
                    $st.Available = $false
                    $st.Detail = 'rule exists but is disabled; enable it in wf.msc'
                } else {
                    $st.Current = 'rule present'
                    $st.Compliant = $true
                }
            } catch {
                $st.Current = '(cannot read)'
                if ($IsElevated) { $st.Available = $false; $st.Detail = $_.Exception.Message }
            }
        }
        'FirewallNotify' {
            $st.Target = 'False'
            $st.TargetData = 'False'
            try {
                $fwp = Get-NetFirewallProfile -Name $Item.Profile -PolicyStore PersistentStore
                $st.Original = [string]$fwp.NotifyOnListen
                $st.Current = $st.Original
                $st.Compliant = ($st.Original -eq 'False')
            } catch {
                $st.Current = '(cannot read)'
                if ($IsElevated) { $st.Available = $false; $st.Detail = $_.Exception.Message }
            }
        }
        default { throw ('Unknown item kind: ' + $Item.Kind) }
    }
    return $st
}

function New-BackupEntry {
    # Everything needed to undo one change, with a uniform set of fields.
    param($Item, $Status)
    $entry = [pscustomobject]@{
        Kind = $Item.Kind; Name = $Item.Name; ScopeLabel = $Item.ScopeLabel; NeedsAdmin = $Item.NeedsAdmin
        Path = $Item.Path; ValueName = $Item.ValueName
        Existed = $null; OriginalKind = $null; OriginalData = $null; KeyCreated = $null
        Scheme = $null; SubGroup = $Item.SubGroup; Setting = $Item.Setting; Line = $Item.Line
        OriginalValue = $null; Labels = $null
        RuleName = $Item.RuleName; Profile = $Item.Profile
    }
    switch ($Item.Kind) {
        'Registry' {
            $entry.Existed = $Status.Original.Exists
            $entry.OriginalKind = $Status.Original.Kind
            $entry.OriginalData = $Status.Original.Data
            $entry.KeyCreated = -not $Status.Original.KeyExists
        }
        'Power' {
            $entry.Scheme = $TargetScheme
            $entry.OriginalValue = $Status.Original
        }
        'PowerScheme' { $entry.Scheme = $Status.Original }
        'FirewallNotify' { $entry.OriginalData = $Status.Original }
    }
    return $entry
}

function Save-BackupEntry {
    # Written before each change, so a crash half-way still leaves a usable backup.
    param($Entry)
    if ($null -eq $script:BackupPath) {
        if (-not (Test-Path -LiteralPath $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $script:BackupPath = Join-Path $BackupDir ('backup-{0}.json' -f $stamp)
        $n = 1
        while (Test-Path -LiteralPath $script:BackupPath) {
            # Never overwrite an earlier backup (two runs within one second).
            $script:BackupPath = Join-Path $BackupDir ('backup-{0}-{1}.json' -f $stamp, $n)
            $n++
        }
    }
    $script:BackupEntries.Add($Entry)
    $doc = [pscustomobject]@{
        Tool = 'prepare-stage-laptop.ps1'
        Created = (Get-Date -Format 's')
        Computer = $env:COMPUTERNAME
        User = $UserName
        UserSid = $UserSid
        Entries = $script:BackupEntries.ToArray()
    }
    ConvertTo-Json -InputObject $doc -Depth 6 | Set-Content -LiteralPath $script:BackupPath -Encoding UTF8
}

function Set-ItemTarget {
    param($Item, $Status)
    switch ($Item.Kind) {
        'Registry' {
            Set-RegValue -Path $Item.Path -Name $Item.ValueName -Kind $Item.Type -Data $Status.TargetData
        }
        'Power' {
            Set-PowerValue -Scheme $TargetScheme -SubGroup $Item.SubGroup -Setting $Item.Setting -Line $Item.Line -Value $Item.Value
        }
        'PowerScheme' {
            # Activated after the loop, once all values of the plan are written.
        }
        'FirewallRule' {
            $ruleArgs = @{
                Name = $Item.RuleName
                DisplayName = $Item.DisplayName
                Description = 'Aethra: OSC triggers from the REAPER computer. Created by prepare-stage-laptop.ps1.'
                Group = 'Aethra'
                Direction = 'Inbound'
                Action = 'Allow'
                Protocol = 'UDP'
                LocalPort = [string]$Item.Port
                RemoteAddress = 'LocalSubnet'
                Profile = 'Any'
                Enabled = 'True'
            }
            New-NetFirewallRule @ruleArgs | Out-Null
        }
        'FirewallNotify' {
            Set-NetFirewallProfile -Name $Item.Profile -PolicyStore PersistentStore -NotifyOnListen False
        }
    }
}

function Restore-BackupEntry {
    # Puts one saved value back. Returns a short description of what was restored.
    param($Entry)
    switch ($Entry.Kind) {
        'Registry' {
            if ($Entry.Existed) {
                $data = [string]$Entry.OriginalData
                if ($Entry.OriginalKind -eq 'DWord') { $data = [int]$Entry.OriginalData }
                Set-RegValue -Path $Entry.Path -Name $Entry.ValueName -Kind $Entry.OriginalKind -Data $data
                return [string]$data
            }
            Remove-RegValue -Path $Entry.Path -Name $Entry.ValueName -RemoveKeyIfEmpty ([bool]$Entry.KeyCreated)
            return '(removed, was not set)'
        }
        'Power' {
            Set-PowerValue -Scheme $Entry.Scheme -SubGroup $Entry.SubGroup -Setting $Entry.Setting -Line $Entry.Line -Value ([long]$Entry.OriginalValue)
            $script:RevertTouchedPower = $true
            return [string]$Entry.OriginalValue
        }
        'PowerScheme' {
            Set-ActiveScheme -Scheme $Entry.Scheme
            $script:RevertTouchedPower = $true
            return $Entry.Scheme
        }
        'FirewallRule' {
            $rule = Get-NetFirewallRule -Name $Entry.RuleName -ErrorAction SilentlyContinue
            if ($null -ne $rule) { Remove-NetFirewallRule -Name $Entry.RuleName }
            return '(rule removed)'
        }
        'FirewallNotify' {
            $value = [string]$Entry.OriginalData
            if (@('True', 'False', 'NotConfigured') -notcontains $value) { $value = 'NotConfigured' }
            Set-NetFirewallProfile -Name $Entry.Profile -PolicyStore PersistentStore -NotifyOnListen $value
            return $value
        }
        default { throw ('Unknown backup entry kind: ' + $Entry.Kind) }
    }
}

function Invoke-Revert {
    $files = @()
    if ($BackupFile) {
        $files = @(Get-Item -LiteralPath $BackupFile)
    } elseif (Test-Path -LiteralPath $BackupDir) {
        # Newest first: undo later runs before earlier ones.
        $files = @(Get-ChildItem -LiteralPath $BackupDir -Filter 'backup-*.json' |
            Sort-Object -Property LastWriteTimeUtc, Name -Descending)
    }
    if ($files.Count -eq 0) {
        Write-Host ('No backup files found in {0}. Nothing to revert.' -f $BackupDir)
        return
    }

    $rows = New-Object System.Collections.Generic.List[object]
    $incompleteFiles = @()
    foreach ($file in $files) {
        $doc = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
        $entries = @($doc.Entries)
        [array]::Reverse($entries)   # undo in the opposite order of the changes
        $complete = $true
        foreach ($entry in $entries) {
            $restored = ''
            $outcome = ''
            if ($entry.NeedsAdmin -and -not $IsElevated) {
                $outcome = 'SKIPPED: needs admin'
                $complete = $false
            } elseif ($entry.Kind -eq 'Registry' -and $entry.Path -like 'HKCU:*' -and $doc.UserSid -ne $UserSid) {
                $outcome = ('SKIPPED: backup belongs to {0}' -f $doc.User)
                $complete = $false
            } else {
                try {
                    $restored = Restore-BackupEntry -Entry $entry
                    $outcome = 'RESTORED'
                } catch {
                    $outcome = 'FAILED: ' + $_.Exception.Message
                    $complete = $false
                }
            }
            $scopeText = $entry.ScopeLabel
            $rows.Add([pscustomobject]@{
                Backup = $file.Name; Setting = $entry.Name; Scope = $scopeText
                RestoredTo = $restored; Result = $outcome
            })
        }
        if ($complete) {
            Rename-Item -LiteralPath $file.FullName -NewName ('reverted-' + $file.Name)
        } else {
            $incompleteFiles += $file.FullName
        }
    }

    if ($script:RevertTouchedPower) {
        # Re-apply the (possibly restored) active plan so its values take effect.
        $active = Get-ActiveSchemeGuid
        if ($active) { Set-ActiveScheme -Scheme $active }
    }

    $rows | Format-Table -Property Backup, Setting, Scope, RestoredTo, Result -AutoSize -Wrap | Out-Host
    if ($incompleteFiles.Count -gt 0) {
        Write-Host 'Some entries were not restored. These backup files were kept; fix the cause'
        Write-Host '(for example run from an elevated prompt) and run -Revert again:'
        foreach ($f in $incompleteFiles) { Write-Host ('  ' + $f) }
    } else {
        Write-Host 'All backups restored. Sign out and in again for per-user settings to take effect.'
    }
}

function Initialize-PowerContext {
    # Finds the active plan and the plan whose values are read and written.
    $script:Schemes = Get-PowerSchemes
    $script:ActiveScheme = Get-ActiveSchemeGuid
    $script:TargetScheme = $script:ActiveScheme
    if ($HighPerformancePlan) {
        if ($script:Schemes.ContainsKey($HighPerformanceGuid)) {
            $script:TargetScheme = $HighPerformanceGuid
        } else {
            Write-Warning ('The "High performance" plan is not available on this PC (typical for Modern Standby ' +
                'laptops). Using the active plan; set Settings > System > Power & battery > Power mode to ' +
                '"Best performance" by hand.')
        }
    }
    Write-Host ('Power   : active plan {0}; values are read from / written to {1}' -f (Format-SchemeName -Guid $script:ActiveScheme), (Format-SchemeName -Guid $script:TargetScheme))
    if (-not $IncludeBattery) {
        Write-Host '          (plugged-in values only; add -IncludeBattery for the on-battery values)'
    }
    Write-Host ''
}

function Invoke-Prepare {
    # Reports every item and, with -Apply, changes the ones that differ.
    $results = New-Object System.Collections.Generic.List[object]
    $powerChanged = $false
    $counts = @{ Ok = 0; Change = 0; Admin = 0; NA = 0; Failed = 0; Manual = 0 }

    foreach ($item in $Items) {
        if ($Scope -eq 'User' -and $item.NeedsAdmin) { continue }
        if ($Scope -eq 'Machine' -and -not $item.NeedsAdmin) { continue }

        $status = Get-ItemStatus -Item $item
        $result = ''
        if (-not $status.Available) {
            $result = 'N/A: ' + $status.Detail
            $counts.NA++
        } elseif ($status.Compliant) {
            $result = 'OK (already set)'
            $counts.Ok++
        } elseif ($item.Kind -eq 'RegCheck') {
            $result = 'MANUAL: ' + $item.Hint
            $counts.Manual++
        } elseif ($item.NeedsAdmin -and -not $IsElevated) {
            $result = 'SKIPPED: needs admin'
            $counts.Admin++
        } elseif (-not $Apply) {
            $result = 'WOULD CHANGE'
            $counts.Change++
        } else {
            try {
                Save-BackupEntry -Entry (New-BackupEntry -Item $item -Status $status)
                Set-ItemTarget -Item $item -Status $status
                if ($item.Kind -eq 'Power' -or $item.Kind -eq 'PowerScheme') { $powerChanged = $true }
                $result = 'CHANGED'
                if ($item.Kind -ne 'PowerScheme') {
                    $check = Get-ItemStatus -Item $item
                    if (-not $check.Compliant) { $result = 'FAILED: value did not change' }
                }
            } catch {
                $result = 'FAILED: ' + $_.Exception.Message
            }
            if ($result -eq 'CHANGED') { $counts.Change++ } else { $counts.Failed++ }
        }

        $adminText = 'no'
        if ($item.NeedsAdmin) { $adminText = 'yes' }
        $results.Add([pscustomobject]@{
            Area = $item.Area; Setting = $item.Name; Scope = $item.ScopeLabel; Admin = $adminText
            Before = $status.Current; Target = $status.Target; Result = $result
        })
    }

    if ($Apply -and $powerChanged -and $TargetScheme) {
        # Activating the plan (again) makes the new plan values take effect now.
        try {
            Set-ActiveScheme -Scheme $TargetScheme
        } catch {
            Write-Warning ('Could not activate the power plan: {0}' -f $_.Exception.Message)
            $counts.Failed++
        }
    }

    $results | Format-Table -Property Area, Setting, Scope, Admin, Before, Target, Result -AutoSize -Wrap | Out-Host

    if ($Apply) {
        Write-Host ('Summary: {0} changed, {1} already set, {2} skipped (need admin), {3} not applicable, {4} failed.' -f
            $counts.Change, $counts.Ok, $counts.Admin, $counts.NA, $counts.Failed)
        if ($script:BackupPath) {
            Write-Host ('Previous values saved to: {0}' -f $script:BackupPath)
            Write-Host 'Undo with: prepare-stage-laptop.ps1 -Revert'
        }
        if ($counts.Change -gt 0) {
            Write-Host 'Sign out and in again (or restart) so that all per-user settings take effect.'
        }
    } else {
        Write-Host ('Summary: {0} would change, {1} already set, {2} skipped (need admin), {3} not applicable.' -f
            $counts.Change, $counts.Ok, $counts.Admin, $counts.NA)
        Write-Host 'Nothing was changed. Run again with -Apply to apply. The "Before" column lists the current'
        Write-Host 'values; keep this output if you may want to restore them by hand.'
    }
    if ($counts.Manual -gt 0) {
        Write-Host ('{0} item(s) marked MANUAL are only checked by this script; change them by hand.' -f $counts.Manual)
    }
    if ($counts.Admin -gt 0) {
        Write-Host 'Items marked "needs admin" run only from an elevated PowerShell ("Run as administrator").'
    }
    Write-Host 'Display layout, Do Not Disturb, Windows Update, GPU vendor settings, network addresses and the'
    Write-Host 'other manual steps are listed in docs/windows-setup.md.'
}

# =============================================================================
# Main
# =============================================================================

$IsElevated = Test-IsElevated
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$UserName = $currentIdentity.Name
$UserSid = $currentIdentity.User.Value
$Schemes = @{}
$ActiveScheme = $null
$TargetScheme = $null
$script:BackupPath = $null
$script:BackupEntries = New-Object System.Collections.Generic.List[object]
$script:RevertTouchedPower = $false

$mode = 'DRY RUN - nothing will be changed'
if ($Apply) { $mode = 'APPLY' }
if ($Revert) { $mode = 'REVERT' }

Write-Host ''
Write-Host ('Aethra stage laptop preparation - {0}' -f $mode)
Write-Host ('Windows : {0}' -f (Get-WindowsSummary))
Write-Host ('User    : {0}   Elevated: {1}' -f $UserName, $IsElevated)

if ($Revert) {
    Write-Host ('Backups : {0}' -f $BackupDir)
    Write-Host ''
    Invoke-Revert
    return
}

Initialize-PowerContext
Build-ItemList
Invoke-Prepare
