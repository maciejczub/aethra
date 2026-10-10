import json

from aethra.report import build_report, read_log


def test_report_from_log(tmp_path):
    records = [
        {"t": 0.0, "wall": "2026-10-09T20:00:00.000000+02:00", "event": "start", "level": "info", "msg": "Aethra 0.1.0: show 'x'"},
        {"t": 10.0, "wall": "2026-10-09T20:00:10.000000+02:00", "event": "trigger", "level": "info", "cue": "01"},
        {"t": 10.001, "wall": "2026-10-09T20:00:10.001000+02:00", "event": "cue_live", "level": "info", "cue": "01", "deck": "A",
         "source": "midi note 36", "trigger_to_play_ms": 0.4, "late_ms": 0.0, "resume": False},
        {"t": 200.0, "wall": "2026-10-09T20:03:20.000000+02:00", "event": "cue_end", "level": "info", "cue": "01"},
        {"t": 200.6, "wall": "2026-10-09T20:03:20.600000+02:00", "event": "cue_stats", "level": "info", "cue": "01",
         "played_s": 190.0, "frame_drop_count": 0, "decoder_frame_drop_count": 1, "vo_delayed_frame_count": 2, "max_drift_ms": 12.5},
        {"t": 210.0, "wall": "2026-10-09T20:03:30.000000+02:00", "event": "deck_down", "level": "error", "msg": "deck B: crash"},
    ]
    path = tmp_path / "aethra-x.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n{torn line")
    text = build_report(read_log(path))
    assert "Cues played: 1" in text
    assert "01" in text and "midi note 36" in text
    assert "deck_down x1" in text
