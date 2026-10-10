import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aethra.config import parse_config  # noqa: E402


@pytest.fixture
def make_config(tmp_path):
    """Build a Config from a dict; media files listed in cues are created."""

    def factory(data=None, create_files=True, **overrides):
        data = dict(data or {})
        data.setdefault("midi", {"enabled": True, "port": "*", "channel": 16})
        data.setdefault(
            "cues",
            [
                {"id": "01", "title": "One", "file": "one.mp4", "midi_note": 36},
                {"id": "02", "title": "Two", "file": "two.mp4", "midi_note": 37},
                {"id": "03", "title": "Three", "file": "three.mp4", "midi_note": 38},
            ],
        )
        data.update(overrides)
        if create_files:
            for cue in data.get("cues", []):
                if isinstance(cue, dict) and isinstance(cue.get("file"), str):
                    (tmp_path / cue["file"]).write_bytes(b"x")
        return parse_config(data, tmp_path / "show.yaml")

    return factory
