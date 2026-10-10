import pytest

from aethra.keys import KeySpecError, key_from_char, key_from_msvcrt, parse_key_spec


def test_parse_and_canonical():
    spec = parse_key_spec("Shift+Ctrl+Q")
    assert spec.canonical == "ctrl+shift+q"
    assert spec.vk == ord("Q")
    assert spec.win_modifiers == 0x0002 | 0x0004
    assert parse_key_spec("esc").canonical == "escape"
    assert parse_key_spec("F5").vk == 0x74
    assert parse_key_spec("space").vk == 0x20


def test_mpv_names():
    assert parse_key_spec("space").mpv_name == "SPACE"
    assert parse_key_spec("b").mpv_name == "b"
    assert parse_key_spec("shift+b").mpv_name == "B"
    assert parse_key_spec("ctrl+shift+q").mpv_name == "Ctrl+Q"
    assert parse_key_spec("ctrl+t").mpv_name == "Ctrl+t"
    assert parse_key_spec("right").mpv_name == "RIGHT"
    assert parse_key_spec("escape").mpv_name == "ESC"


@pytest.mark.parametrize("bad", ["", "ctrl+", "hyper+x", "nokey", "ctrl++"])
def test_invalid(bad):
    with pytest.raises(KeySpecError):
        parse_key_spec(bad)


def test_console_decoding():
    assert key_from_char(" ").canonical == "space"
    assert key_from_char("b").canonical == "b"
    assert key_from_char("B").canonical == "shift+b"
    assert key_from_char("\x14").canonical == "ctrl+t"
    assert key_from_char("\x1b").canonical == "escape"
    assert key_from_msvcrt("\xe0", "M").canonical == "right"
    assert key_from_msvcrt("\x00", ";").canonical == "f1"
    assert key_from_msvcrt(None, "f").canonical == "f"


def test_ctrl_c_in_console_means_quit():
    from aethra.eventlog import EventLog
    from aethra.keyboard import ConsoleKeys

    posted = []
    keys = ConsoleKeys({"go": ("space",)}, lambda cmd, src: posted.append(cmd), EventLog(None, None))
    keys._emit(key_from_char("\x03"))
    keys._emit(key_from_char(" "))
    assert posted == ["quit", "go"]
