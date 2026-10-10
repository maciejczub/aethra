"""Key names shared by the three keyboard paths.

A key spec such as ``"space"``, ``"b"``, ``"ctrl+shift+q"`` or ``"f5"`` is
translated to a Windows virtual-key code (global hotkeys), an mpv key name
(bindings in the stage window) and console input sequences.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

MODIFIERS = ("ctrl", "alt", "shift", "win")
_MOD_ALIASES = {"control": "ctrl", "meta": "win", "super": "win", "cmd": "win"}

_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "pgdown": "pagedown",
    "ins": "insert",
    "del": "delete",
    "bs": "backspace",
}

# canonical name -> (Windows VK code, mpv key name)
_NAMED: dict[str, tuple[int, str]] = {
    "space": (0x20, "SPACE"),
    "enter": (0x0D, "ENTER"),
    "escape": (0x1B, "ESC"),
    "tab": (0x09, "TAB"),
    "backspace": (0x08, "BS"),
    "left": (0x25, "LEFT"),
    "up": (0x26, "UP"),
    "right": (0x27, "RIGHT"),
    "down": (0x28, "DOWN"),
    "pageup": (0x21, "PGUP"),
    "pagedown": (0x22, "PGDWN"),
    "end": (0x23, "END"),
    "home": (0x24, "HOME"),
    "insert": (0x2D, "INS"),
    "delete": (0x2E, "DEL"),
}
for _i in range(1, 25):
    _NAMED[f"f{_i}"] = (0x6F + _i, f"F{_i}")
for _i in range(10):
    _NAMED[f"numpad{_i}"] = (0x60 + _i, f"KP{_i}")
for _c in string.ascii_lowercase:
    _NAMED[_c] = (ord(_c.upper()), _c)
for _c in string.digits:
    _NAMED[_c] = (ord(_c), _c)

WIN_MODIFIER_BITS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}


class KeySpecError(ValueError):
    pass


@dataclass(frozen=True)
class KeySpec:
    key: str
    mods: frozenset[str]

    @property
    def canonical(self) -> str:
        return "+".join([m for m in MODIFIERS if m in self.mods] + [self.key])

    @property
    def vk(self) -> int:
        return _NAMED[self.key][0]

    @property
    def win_modifiers(self) -> int:
        bits = 0
        for mod in self.mods:
            bits |= WIN_MODIFIER_BITS[mod]
        return bits

    @property
    def mpv_name(self) -> str:
        name = _NAMED[self.key][1]
        mods = set(self.mods)
        if "shift" in mods and len(self.key) == 1 and self.key.isalpha():
            # mpv reports Shift+b as "B".
            mods.discard("shift")
            name = name.upper()
        prefix = "".join(
            {"ctrl": "Ctrl+", "alt": "Alt+", "shift": "Shift+", "win": "Meta+"}[m] for m in MODIFIERS if m in mods
        )
        return prefix + name

    def __str__(self) -> str:
        return self.canonical


def parse_key_spec(text: str) -> KeySpec:
    parts = [p.strip().lower() for p in text.split("+")]
    if not text.strip() or any(not p for p in parts):
        raise KeySpecError(f"invalid key {text!r}")
    *mod_parts, key = parts
    mods = set()
    for mod in mod_parts:
        mod = _MOD_ALIASES.get(mod, mod)
        if mod not in MODIFIERS:
            raise KeySpecError(f"unknown modifier {mod!r} in {text!r} (use {', '.join(MODIFIERS)})")
        mods.add(mod)
    key = _KEY_ALIASES.get(key, key)
    if key not in _NAMED:
        raise KeySpecError(f"unknown key {key!r} in {text!r}")
    return KeySpec(key, frozenset(mods))


# --------------------------------------------------------------------------
# Console input decoding

# Windows console (msvcrt.getwch): special keys arrive as a prefix + code.
_MSVCRT_SPECIAL = {
    72: "up", 80: "down", 75: "left", 77: "right",
    71: "home", 79: "end", 73: "pageup", 81: "pagedown", 82: "insert", 83: "delete",
    59: "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5", 64: "f6", 65: "f7", 66: "f8", 67: "f9", 68: "f10",
    133: "f11", 134: "f12",
}

# ANSI terminals (POSIX dev machines).
ANSI_SEQUENCES = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1b[H": "home", "\x1b[F": "end", "\x1b[1~": "home", "\x1b[4~": "end",
    "\x1b[5~": "pageup", "\x1b[6~": "pagedown", "\x1b[2~": "insert", "\x1b[3~": "delete",
    "\x1bOP": "f1", "\x1bOQ": "f2", "\x1bOR": "f3", "\x1bOS": "f4",
    "\x1b[15~": "f5", "\x1b[17~": "f6", "\x1b[18~": "f7", "\x1b[19~": "f8",
    "\x1b[20~": "f9", "\x1b[21~": "f10", "\x1b[23~": "f11", "\x1b[24~": "f12",
}


def key_from_msvcrt(prefix: str | None, ch: str) -> KeySpec | None:
    if prefix is not None:
        name = _MSVCRT_SPECIAL.get(ord(ch))
        return KeySpec(name, frozenset()) if name else None
    return key_from_char(ch)


def key_from_char(ch: str) -> KeySpec | None:
    """Decode one plain character (both consoles)."""
    simple = {" ": "space", "\r": "enter", "\n": "enter", "\x1b": "escape", "\t": "tab", "\x08": "backspace", "\x7f": "backspace"}
    if ch in simple:
        return KeySpec(simple[ch], frozenset())
    code = ord(ch)
    if 1 <= code <= 26:
        return KeySpec(chr(ord("a") + code - 1), frozenset({"ctrl"}))
    if ch.isascii() and ch.isalpha():
        if ch.isupper():
            return KeySpec(ch.lower(), frozenset({"shift"}))
        return KeySpec(ch, frozenset())
    if ch.isascii() and ch.isdigit():
        return KeySpec(ch, frozenset())
    return None
