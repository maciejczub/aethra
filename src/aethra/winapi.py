"""ctypes declarations for the few Win32 APIs Aethra uses (Windows only).

All prototypes are declared explicitly (argtypes/restype) so that 64-bit
handles and pointer-sized parameters are never truncated.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - imported only on Windows
    raise ImportError("aethra.winapi is Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
DWORD_PTR = ctypes.c_size_t
HANDLE = wintypes.HANDLE

# --------------------------------------------------------------------------
# Constants

HWND_TOP = 0
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SWP_ASYNCWINDOWPOS = 0x4000

GW_HWNDNEXT = 2

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_DISPLAYCHANGE = 0x007E
WM_HOTKEY = 0x0312
WM_APP = 0x8000

MA_NOACTIVATE = 3
SW_SHOWNOACTIVATE = 4
BLACK_BRUSH = 4
MONITORINFOF_PRIMARY = 0x00000001
MOD_NOREPEAT = 0x4000
PM_NOREMOVE = 0x0000

SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
SEM_NOOPENFILEERRORBOX = 0x8000

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

NORMAL_PRIORITY_CLASS = 0x00000020
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
HIGH_PRIORITY_CLASS = 0x00000080

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

SWP_SHOWWINDOW_FLAG = SWP_SHOWWINDOW
GWL_EXSTYLE = -20

STD_INPUT_HANDLE = -10
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080

JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_BASIC_LIMIT_INFORMATION_CLASS = 2

PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4
PROCESS_POWER_THROTTLING_INFORMATION_CLASS = 4  # ProcessPowerThrottling

MIDIERR_STILLPLAYING = 65

# --------------------------------------------------------------------------
# Structures


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwSupport", wintypes.DWORD),
    ]


class MIDIHDR(ctypes.Structure):
    pass


MIDIHDR._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", DWORD_PTR),
    ("dwFlags", wintypes.DWORD),
    ("lpNext", ctypes.POINTER(MIDIHDR)),
    ("reserved", DWORD_PTR),
    ("dwOffset", wintypes.DWORD),
    ("dwReserved", DWORD_PTR * 8),
]

MIDIINPROC = ctypes.WINFUNCTYPE(None, HANDLE, wintypes.UINT, DWORD_PTR, DWORD_PTR, DWORD_PTR)

CALLBACK_FUNCTION = 0x00030000
MIM_OPEN = 0x3C1
MIM_CLOSE = 0x3C2
MIM_DATA = 0x3C3
MIM_LONGDATA = 0x3C4
MIM_ERROR = 0x3C5
MIM_LONGERROR = 0x3C6
MIM_MOREDATA = 0x3CC
MMSYSERR_NOERROR = 0

# --------------------------------------------------------------------------
# Prototypes


def _proto(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object) -> ctypes._CFuncPtr:  # type: ignore[name-defined]
    func = getattr(dll, name)
    func.restype = restype
    func.argtypes = argtypes
    return func


SetWindowPos = _proto(user32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
GetWindowThreadProcessId = _proto(user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
EnumWindows = _proto(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, LPARAM)
IsWindow = _proto(user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
IsWindowVisible = _proto(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
GetClassNameW = _proto(user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
GetTopWindow = _proto(user32, "GetTopWindow", wintypes.HWND, wintypes.HWND)
GetWindow = _proto(user32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
GetWindowRect = _proto(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(RECT))
EnumDisplayMonitors = _proto(user32, "EnumDisplayMonitors", wintypes.BOOL, wintypes.HDC, ctypes.POINTER(RECT), MONITORENUMPROC, LPARAM)
GetMonitorInfoW = _proto(user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
RegisterClassExW = _proto(user32, "RegisterClassExW", wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
UnregisterClassW = _proto(user32, "UnregisterClassW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.HINSTANCE)
CreateWindowExW = _proto(
    user32, "CreateWindowExW", wintypes.HWND,
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
DestroyWindow = _proto(user32, "DestroyWindow", wintypes.BOOL, wintypes.HWND)
ShowWindow = _proto(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
ShowWindowAsync = _proto(user32, "ShowWindowAsync", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
IsIconic = _proto(user32, "IsIconic", wintypes.BOOL, wintypes.HWND)
DefWindowProcW = _proto(user32, "DefWindowProcW", LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
GetMessageW = _proto(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
PeekMessageW = _proto(user32, "PeekMessageW", wintypes.BOOL, ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
TranslateMessage = _proto(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(MSG))
DispatchMessageW = _proto(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(MSG))
PostThreadMessageW = _proto(user32, "PostThreadMessageW", wintypes.BOOL, wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM)
PostMessageW = _proto(user32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
PostQuitMessage = _proto(user32, "PostQuitMessage", None, ctypes.c_int)
SetCursor = _proto(user32, "SetCursor", wintypes.HANDLE, wintypes.HANDLE)
RegisterHotKey = _proto(user32, "RegisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
UnregisterHotKey = _proto(user32, "UnregisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
GetStockObject = _proto(gdi32, "GetStockObject", wintypes.HGDIOBJ, ctypes.c_int)
GetModuleHandleW = _proto(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
GetCurrentThreadId = _proto(kernel32, "GetCurrentThreadId", wintypes.DWORD)
GetCurrentProcess = _proto(kernel32, "GetCurrentProcess", HANDLE)
SetPriorityClass = _proto(kernel32, "SetPriorityClass", wintypes.BOOL, HANDLE, wintypes.DWORD)
SetErrorMode = _proto(kernel32, "SetErrorMode", wintypes.UINT, wintypes.UINT)
SetThreadExecutionState = _proto(kernel32, "SetThreadExecutionState", wintypes.DWORD, wintypes.DWORD)
OpenProcess = _proto(kernel32, "OpenProcess", HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
GetStdHandle = _proto(kernel32, "GetStdHandle", HANDLE, wintypes.DWORD)
GetConsoleMode = _proto(kernel32, "GetConsoleMode", wintypes.BOOL, HANDLE, ctypes.POINTER(wintypes.DWORD))
SetConsoleMode = _proto(kernel32, "SetConsoleMode", wintypes.BOOL, HANDLE, wintypes.DWORD)
CreateJobObjectW = _proto(kernel32, "CreateJobObjectW", HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
SetInformationJobObject = _proto(kernel32, "SetInformationJobObject", wintypes.BOOL, HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
AssignProcessToJobObject = _proto(kernel32, "AssignProcessToJobObject", wintypes.BOOL, HANDLE, HANDLE)
GetExitCodeProcess = _proto(kernel32, "GetExitCodeProcess", wintypes.BOOL, HANDLE, ctypes.POINTER(wintypes.DWORD))
CloseHandle = _proto(kernel32, "CloseHandle", wintypes.BOOL, HANDLE)
timeBeginPeriod = _proto(winmm, "timeBeginPeriod", wintypes.UINT, wintypes.UINT)
timeEndPeriod = _proto(winmm, "timeEndPeriod", wintypes.UINT, wintypes.UINT)

midiInGetNumDevs = _proto(winmm, "midiInGetNumDevs", wintypes.UINT)
midiInGetDevCapsW = _proto(winmm, "midiInGetDevCapsW", wintypes.UINT, UINT_PTR, ctypes.POINTER(MIDIINCAPSW), wintypes.UINT)
midiInOpen = _proto(winmm, "midiInOpen", wintypes.UINT, ctypes.POINTER(HANDLE), wintypes.UINT, DWORD_PTR, DWORD_PTR, wintypes.DWORD)
midiInStart = _proto(winmm, "midiInStart", wintypes.UINT, HANDLE)
midiInStop = _proto(winmm, "midiInStop", wintypes.UINT, HANDLE)
midiInReset = _proto(winmm, "midiInReset", wintypes.UINT, HANDLE)
midiInClose = _proto(winmm, "midiInClose", wintypes.UINT, HANDLE)
midiInPrepareHeader = _proto(winmm, "midiInPrepareHeader", wintypes.UINT, HANDLE, ctypes.POINTER(MIDIHDR), wintypes.UINT)
midiInUnprepareHeader = _proto(winmm, "midiInUnprepareHeader", wintypes.UINT, HANDLE, ctypes.POINTER(MIDIHDR), wintypes.UINT)
midiInAddBuffer = _proto(winmm, "midiInAddBuffer", wintypes.UINT, HANDLE, ctypes.POINTER(MIDIHDR), wintypes.UINT)


def _optional(dll: ctypes.WinDLL, name: str, restype: object, *argtypes: object):  # type: ignore[no-untyped-def]
    try:
        return _proto(dll, name, restype, *argtypes)
    except AttributeError:
        return None


SetProcessDpiAwarenessContext = _optional(user32, "SetProcessDpiAwarenessContext", wintypes.BOOL, HANDLE)
SetProcessInformation = _optional(kernel32, "SetProcessInformation", wintypes.BOOL, HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
GetWindowLongPtrW = _optional(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int)
SetProcessDPIAware = _optional(user32, "SetProcessDPIAware", wintypes.BOOL)


# --------------------------------------------------------------------------
# Helpers


def set_dpi_awareness() -> None:
    """Physical pixels everywhere, so monitor rectangles match mpv's."""
    if SetProcessDpiAwarenessContext is not None:
        if SetProcessDpiAwarenessContext(HANDLE(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return
    try:
        shcore = ctypes.WinDLL("shcore")
        if shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (OSError, AttributeError):
        pass
    if SetProcessDPIAware is not None:
        SetProcessDPIAware()


def disable_power_throttling() -> bool:
    """Opt out of Windows 11 timer-resolution and EcoQoS throttling.

    Windows 11 stops honouring timeBeginPeriod for a process whose windows are
    all covered - which is always true for the controller (its backdrop sits
    under the video). Without this, timed waits fall back to ~15.6 ms ticks.
    """
    if SetProcessInformation is None:
        return False
    state = PROCESS_POWER_THROTTLING_STATE()
    state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION
    state.ControlMask = PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION | PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    state.StateMask = 0
    return bool(SetProcessInformation(GetCurrentProcess(), PROCESS_POWER_THROTTLING_INFORMATION_CLASS, ctypes.byref(state), ctypes.sizeof(state)))


def is_topmost(hwnd: int) -> bool:
    if GetWindowLongPtrW is None:
        return True
    return bool(GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


_deck_job: int | None = None


def assign_to_deck_job(process_handle: int) -> bool:
    """Put an mpv process into a job whose processes die on an unhandled
    exception instead of going through Windows Error Reporting (mpv resets the
    error mode it inherits, so a crash would otherwise leave a frozen picture
    while WER runs). The job does not kill its processes when Aethra exits."""
    global _deck_job
    if _deck_job is None:
        job = CreateJobObjectW(None, None)
        if not job:
            return False
        info = JOBOBJECT_BASIC_LIMIT_INFORMATION()
        info.LimitFlags = JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if not SetInformationJobObject(job, JOB_OBJECT_BASIC_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)):
            CloseHandle(job)
            return False
        _deck_job = job
    return bool(AssignProcessToJobObject(_deck_job, process_handle))


class ConsoleInputMode:
    """Console input mode for the show: no QuickEdit (a mouse click in the
    console would freeze every write to it - and the controller with it), and
    Ctrl+C delivered as a key instead of a signal (cmd.exe would otherwise
    remember it and stop the restart loop with "Terminate batch job (Y/N)?")."""

    def __init__(self) -> None:
        self._handle: int | None = None
        self._saved: int | None = None

    def apply(self) -> bool:
        handle = GetStdHandle(STD_INPUT_HANDLE & 0xFFFFFFFF)
        mode = wintypes.DWORD()
        if not handle or not GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new = (mode.value | ENABLE_EXTENDED_FLAGS) & ~(ENABLE_QUICK_EDIT_MODE | ENABLE_PROCESSED_INPUT)
        if not SetConsoleMode(handle, new):
            return False
        self._handle, self._saved = handle, mode.value
        return True

    def restore(self) -> None:
        if self._handle is not None and self._saved is not None:
            SetConsoleMode(self._handle, self._saved)
            self._handle = None


def monitors() -> list[tuple[str, RECT, bool]]:
    """Monitors in EnumDisplayMonitors order: (device name, rect, primary)."""
    found: list[tuple[str, RECT, bool]] = []

    def callback(hmon: int, _hdc: int, _rect: object, _data: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append((info.szDevice, RECT(r.left, r.top, r.right, r.bottom), bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return found


def windows_of_process(pid: int) -> list[int]:
    found: list[int] = []

    def callback(hwnd: int, _data: int) -> bool:
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    EnumWindows(WNDENUMPROC(callback), 0)
    return found


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def process_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        CloseHandle(handle)


def z_order(hwnds: set[int]) -> list[int]:
    """The given windows ordered top to bottom (as Windows currently stacks them)."""
    result: list[int] = []
    hwnd = GetTopWindow(None)
    guard = 0
    while hwnd and guard < 100000:
        if hwnd in hwnds:
            result.append(hwnd)
            if len(result) == len(hwnds):
                break
        hwnd = GetWindow(hwnd, GW_HWNDNEXT)
        guard += 1
    return result
