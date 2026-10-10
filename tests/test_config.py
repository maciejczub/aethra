from pathlib import Path

import pytest

from aethra.config import ConfigError, load_config, parse_config, parse_timecode

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "show.example.yaml"


def test_example_config_is_valid():
    cfg = load_config(EXAMPLE)
    assert cfg.name == "Example show"
    assert [c.id for c in cfg.cues] == ["01", "02", "03"]
    assert cfg.midi.channel == 16
    assert cfg.midi.command_notes == {"black": 120, "filler": 121}
    assert cfg.cues[2].fade_in == 1.0
    # Media files of the example do not exist: warnings, not errors.
    assert any("not found" in w for w in cfg.warnings)


def test_defaults(make_config):
    cfg = make_config()
    assert cfg.stage.fps == 25.0
    assert cfg.filler.mode == "color"
    assert cfg.sync.mtc.enabled is False
    assert cfg.sync.correction.enabled is True
    assert cfg.keyboard.keys["go"] == ("space",)
    assert cfg.warnings == ()
    assert cfg.cues[0].on_end == "filler"


def test_unknown_keys_are_errors(make_config):
    with pytest.raises(ConfigError) as err:
        make_config({"stage": {"screeen": 1}, "fillr": {}})
    text = "\n".join(err.value.problems)
    assert "stage.screeen: unknown key" in text
    assert "fillr: unknown key" in text


def test_type_errors(make_config):
    with pytest.raises(ConfigError) as err:
        make_config({"stage": {"fps": "fast", "width": True}})
    text = "\n".join(err.value.problems)
    assert "stage.fps" in text and "stage.width" in text


def test_duplicate_ids_and_notes(make_config):
    cues = [
        {"id": "a", "file": "a.mp4", "midi_note": 40},
        {"id": "a", "file": "b.mp4", "midi_note": 40},
    ]
    with pytest.raises(ConfigError) as err:
        make_config(cues=cues)
    text = "\n".join(err.value.problems)
    assert "duplicate id" in text
    assert "note 40 is already used" in text


def test_note_collides_with_command_note(make_config):
    with pytest.raises(ConfigError) as err:
        make_config(
            {"midi": {"port": "*", "command_notes": {"black": 36}}},
        )
    assert "command_notes.black" in "\n".join(err.value.problems)


def test_missing_media_is_a_warning(make_config):
    cfg = make_config(create_files=False)
    assert len(cfg.warnings) == 3


def test_midi_port_required(make_config):
    with pytest.raises(ConfigError):
        make_config({"midi": {"enabled": True}})
    cfg = make_config({"midi": {"enabled": False}})
    assert cfg.midi.enabled is False


def test_channel_any(make_config):
    assert make_config({"midi": {"port": "*", "channel": "any"}}).midi.channel == 0
    with pytest.raises(ConfigError):
        make_config({"midi": {"port": "*", "channel": 17}})


def test_osc_trigger_forms(make_config):
    cues = [
        {"id": "a", "file": "a.mp4", "osc": "/song/a"},
        {"id": "b", "file": "b.mp4", "osc": {"address": "/lastmarker/name", "arg": "V02"}},
        {"id": "c", "file": "c.mp4", "osc": {"address": "/song", "arg": 3}},
    ]
    cfg = make_config(cues=cues)
    a, b, c = cfg.cues
    assert a.osc.matches("/song/a", ())
    assert b.osc.matches("/lastmarker/name", ("V02",))
    assert not b.osc.matches("/lastmarker/name", ("V03",))
    assert c.osc.matches("/song", (3,)) and c.osc.matches("/song", (3.0,))
    assert not c.osc.matches("/song", ("3",))


def test_timecodes():
    assert parse_timecode("00:01:02:05", 25) == pytest.approx(62.2)
    assert parse_timecode("01:00:00:00", 25) == 3600
    with pytest.raises(ValueError):
        parse_timecode("00:00:00:25", 25)
    with pytest.raises(ValueError):
        parse_timecode("1:2:3", 25)


def test_mtc_start(make_config):
    cues = [{"id": "a", "file": "a.mp4", "mtc_start": "00:00:10:00"}]
    assert make_config(cues=cues).cues[0].mtc_start == 10.0


def test_key_bindings(make_config):
    cfg = make_config({"keyboard": {"keys": {"go": "f5", "black": ["b", "ctrl+b"]}}})
    assert cfg.keyboard.keys["go"] == ("f5",)
    with pytest.raises(ConfigError) as err:
        make_config({"keyboard": {"keys": {"go": "hyper+x"}}})
    assert "unknown modifier" in "\n".join(err.value.problems)
    with pytest.raises(ConfigError) as err:
        make_config({"keyboard": {"keys": {"go": "b"}}})  # b is also "black"
    assert "bound to both" in "\n".join(err.value.problems)


def test_filler_validation(make_config, tmp_path):
    with pytest.raises(ConfigError):
        make_config({"filler": {"mode": "loop"}})
    with pytest.raises(ConfigError):
        make_config({"filler": {"color": "red"}})
    cfg = make_config({"filler": {"mode": "loop", "file": "nope.mp4"}})
    assert any("filler file not found" in w for w in cfg.warnings)


def test_correction_target_below_threshold(make_config):
    with pytest.raises(ConfigError):
        make_config({"sync": {"correction": {"threshold": 0.01, "target": 0.02}}})
    cfg = make_config({"sync": {"correction": {"enabled": False}, "mtc": {"enabled": True, "on_stop": "filler"}}})
    assert cfg.sync.correction.enabled is False
    assert cfg.sync.mtc.on_stop == "filler"


def test_paths_relative_to_config(make_config, tmp_path):
    cfg = make_config({"show": {"media_dir": "."}, "mpv": {"path": "bin/mpv.exe"}})
    assert cfg.cues[0].file == tmp_path / "one.mp4"
    assert Path(cfg.mpv.path) == tmp_path / "bin" / "mpv.exe"
    assert cfg.mpv.path != "mpv"


def test_json_config(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    path = tmp_path / "show.json"
    path.write_text('{"midi": {"enabled": false}, "cues": [{"id": "a", "file": "a.mp4"}]}')
    cfg = load_config(path)
    assert cfg.cues[0].file == tmp_path / "a.mp4"


def test_not_a_mapping():
    with pytest.raises(ConfigError):
        parse_config(["x"], Path("/tmp/show.yaml"))


def test_deck_args_without_option_list(make_config, tmp_path):
    from aethra.mpvproc import RECENT_OPTIONS, build_deck_args

    cfg = make_config()
    unknown = build_deck_args("mpv", cfg, "A", "/tmp/x.sock", None, None)
    assert not any(a.split("=")[0].lstrip("-") in RECENT_OPTIONS for a in unknown.args)
    known = build_deck_args("mpv", cfg, "A", "/tmp/x.sock", None, frozenset({"idle", "media-controls"}))
    assert "--media-controls=no" in known.args and "--idle=yes" in known.args
    assert "keep-open" in known.dropped
