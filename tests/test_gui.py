"""The control panel's back end (no browser needed)."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import yaml

from aethra.config import load_config
from aethra.gui.server import Handler, Panel, dump_yaml

SHOW = {
    "show": {"name": "Panel test", "media_dir": "media"},
    "stage": {"engine": "compositor", "fps": 25},
    "filler": {"mode": "color", "color": "#102030"},
    "intro": {"mode": "color", "color": "#000000"},
    "transitions": {"xfade_out_duration": 1.5, "xfade_out_type": "screen"},
    "midi": {"enabled": True, "port": "*", "channel": 16, "command_notes": {"filler": 121}},
    "osc": {"enabled": True, "port": 9123},
    "keyboard": {"keys": {"go": ["space", "ctrl+g"]}},
    "cues": [
        {"id": "01", "title": "Opening: part 1", "file": "01.mp4", "midi_note": 36, "xfade_in_type": "multiply", "xfade_in_duration": 2.0},
        {"id": "2", "title": "yes", "file": "02 song.mp4", "midi_note": 37, "offset": -1.5, "on_end": "black"},
    ],
}


def make_show(tmp_path):
    (tmp_path / "media").mkdir()
    for name in ("01.mp4", "02 song.mp4", "filler.mov", "notes.txt"):
        (tmp_path / "media" / name).write_bytes(b"x")
    path = tmp_path / "show.yaml"
    path.write_text(yaml.safe_dump(SHOW), encoding="utf-8")
    return path


def test_save_round_trip_keeps_types(tmp_path):
    path = make_show(tmp_path)
    panel = Panel(path)
    read = panel.read_config()
    assert read["data"] == SHOW
    result = panel.save(read["data"])
    assert result["saved"] and result["ok"]
    assert (tmp_path / "show.yaml.bak").is_file()
    # Ids stay text, colours stay colours, "yes" stays a word.
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == SHOW
    cfg = load_config(path)
    assert [c.id for c in cfg.cues] == ["01", "2"]
    assert cfg.cues[0].transitions.xfade_in_type == "multiply"
    assert '"01"' in result["text"] and "cues:\n  - {" in result["text"]


def test_dump_order_and_header():
    text = dump_yaml({"cues": [{"id": "01", "file": "a.mp4"}], "show": {"name": "x"}}, "show.yaml")
    assert text.startswith("# Aethra show configuration")
    assert text.index("show:") < text.index("cues:")


def test_invalid_file_is_not_saved_unless_forced(tmp_path):
    path = make_show(tmp_path)
    panel = Panel(path)
    bad = json.loads(json.dumps(SHOW))
    bad["transitions"]["xfade_in_type"] = "dissolve"
    result = panel.save(bad)
    assert not result["saved"] and any("xfade_in_type" in p for p in result["problems"])
    assert yaml.safe_load(path.read_text()) == SHOW
    assert panel.save(bad, force=True)["saved"]


def test_media_listing(tmp_path):
    path = make_show(tmp_path)
    panel = Panel(path)
    media = panel.media(SHOW)
    assert [f["name"] for f in media["files"]] == ["01.mp4", "02 song.mp4", "filler.mov"]
    assert panel.validate(SHOW)["ok"]


def test_jobs_run_and_stream_output(tmp_path):
    path = make_show(tmp_path)
    panel = Panel(path)
    job = panel.start_job("check", {})
    deadline = time.time() + 30
    while job.returncode is None and time.time() < deadline:
        time.sleep(0.1)
    text = "\n".join(job.lines)
    assert job.returncode is not None
    assert "Panel test" in text or "show" in text.lower()
    assert job.lines[-1].startswith("[finished")


def test_http_requires_the_token(tmp_path):
    path = make_show(tmp_path)
    panel = Panel(path)
    Handler.panel = panel
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert panel.token in page and "__TOKEN__" not in page
        try:
            urllib.request.urlopen(base + "/api/config")
            raise AssertionError("no token accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        req = urllib.request.Request(base + "/api/validate", data=json.dumps({"data": SHOW}).encode(),
                                     headers={"X-Aethra-Token": panel.token, "Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        assert urllib.request.urlopen(base + "/static/app.js").status == 200
        try:
            urllib.request.urlopen(base + "/static/../server.py")
            raise AssertionError("path traversal")
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404)
    finally:
        httpd.shutdown()


def test_show_status_without_a_show(tmp_path):
    assert Panel(make_show(tmp_path)).show_status()["running"] in (False, True)
