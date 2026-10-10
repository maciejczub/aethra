"""Show configuration: one YAML (or JSON) file, validated strictly at startup.

Every key is checked: unknown keys are errors, so a typo is caught by
``aethra check`` the day before the show rather than ignored silently.
Missing media files are only *warnings* - the show must still start and play
every cue that is available.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aethra.keys import KeySpecError, parse_key_spec

COMMANDS = ("go", "next", "prev", "black", "filler", "test", "quit")
ON_END = ("filler", "hold", "black")
FILLER_MODES = ("color", "image", "loop")
MTC_ON_STOP = ("continue", "filler")

DEFAULT_KEYS: dict[str, tuple[str, ...]] = {
    "go": ("space",),
    "next": ("right", "down"),
    "prev": ("left", "up"),
    "black": ("b", "escape"),
    "filler": ("f",),
    "test": ("ctrl+t",),
    "quit": ("ctrl+shift+q",),
}


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid configuration:\n  " + "\n  ".join(problems))


@dataclass(frozen=True)
class StageConfig:
    screen: int | None = None
    screen_name: str | None = None
    width: int = 1920
    height: int = 1080
    fps: float = 25.0


@dataclass(frozen=True)
class MpvConfig:
    path: str = "mpv"
    hwdec: str = "auto-safe"
    vo: str | None = None
    priority: str = "above_normal"
    extra_args: tuple[str, ...] = ()
    startup_timeout: float = 15.0


@dataclass(frozen=True)
class FillerConfig:
    mode: str = "color"
    file: Path | None = None
    color: str = "#000000"


@dataclass(frozen=True)
class TransitionConfig:
    fade_out: float = 0.5
    fade_in: float = 0.5


@dataclass(frozen=True)
class CorrectionConfig:
    enabled: bool = True
    threshold: float = 0.040
    target: float = 0.010
    max_speed_change: float = 0.01
    correction_time: float = 4.0
    hard_seek: float | None = 1.0


@dataclass(frozen=True)
class MtcConfig:
    enabled: bool = False
    on_stop: str = "continue"
    timeout: float = 0.5


@dataclass(frozen=True)
class SyncConfig:
    latency_compensation: float = 0.0
    retrigger_guard: float = 1.0
    resume_after_crash: bool = True
    stall_timeout: float = 2.0
    correction: CorrectionConfig = field(default_factory=CorrectionConfig)
    mtc: MtcConfig = field(default_factory=MtcConfig)


@dataclass(frozen=True)
class MidiConfig:
    enabled: bool = True
    port: str = ""
    channel: int = 0
    backend: str = "auto"
    command_notes: dict[str, int] = field(default_factory=dict)
    reconnect_interval: float = 2.0


@dataclass(frozen=True)
class OscConfig:
    enabled: bool = False
    bind: str = "0.0.0.0"
    port: int = 9000
    prefix: str = "/aethra"
    require_playing: bool = False


@dataclass(frozen=True)
class KeyboardConfig:
    global_hotkeys: bool = True
    console: bool = True
    stage_window: bool = True
    keys: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_KEYS))


@dataclass(frozen=True)
class LoggingConfig:
    dir: Path = Path("logs")
    mpv_logs: bool = True
    status_interval: float = 1.0


@dataclass(frozen=True)
class OscTrigger:
    address: str
    arg: str | int | float | None = None

    def matches(self, address: str, args: tuple[Any, ...]) -> bool:
        if address != self.address:
            return False
        if self.arg is None:
            return True
        if not args:
            return False
        value = args[0]
        if isinstance(self.arg, str):
            return isinstance(value, str) and value == self.arg
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return abs(float(value) - float(self.arg)) < 1e-6
        return False


@dataclass(frozen=True)
class Cue:
    index: int
    id: str
    title: str
    file: Path
    midi_note: int | None = None
    midi_program: int | None = None
    osc: OscTrigger | None = None
    offset: float = 0.0
    on_end: str = "filler"
    fade_in: float = 0.0
    mtc_start: float | None = None

    @property
    def label(self) -> str:
        return f"{self.id} {self.title!r}" if self.title else self.id


@dataclass(frozen=True)
class Config:
    path: Path
    base_dir: Path
    name: str
    stage: StageConfig
    mpv: MpvConfig
    filler: FillerConfig
    transitions: TransitionConfig
    sync: SyncConfig
    midi: MidiConfig
    osc: OscConfig
    keyboard: KeyboardConfig
    logging: LoggingConfig
    test_mode: bool
    cues: tuple[Cue, ...]
    warnings: tuple[str, ...] = ()

    def cue_by_id(self, cue_id: str) -> Cue | None:
        for cue in self.cues:
            if cue.id == cue_id:
                return cue
        return None


# --------------------------------------------------------------------------
# Parsing helpers

_MISSING = object()
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TIMECODE_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;.](\d{2})$")


class _Section:
    """Reads one mapping, remembers which keys were used, reports the rest."""

    def __init__(self, data: Any, path: str, problems: list[str]) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            problems.append(f"{path}: expected a mapping, got {type(data).__name__}")
            data = {}
        self.data = data
        self.path = path
        self.problems = problems
        self.used: set[str] = set()

    def _where(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def raw(self, key: str, default: Any = None) -> Any:
        self.used.add(key)
        return self.data.get(key, default)

    def get(
        self,
        key: str,
        kind: type | tuple[type, ...],
        default: Any,
        *,
        choices: tuple[Any, ...] | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        nullable: bool = False,
    ) -> Any:
        self.used.add(key)
        value = self.data.get(key, _MISSING)
        if value is _MISSING:
            return default
        where = self._where(key)
        if value is None:
            if nullable:
                return None
            self.problems.append(f"{where}: must not be empty")
            return default
        kinds = kind if isinstance(kind, tuple) else (kind,)
        if float in kinds and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if bool not in kinds and isinstance(value, bool):
            self.problems.append(f"{where}: expected {_kind_name(kinds)}, got a boolean")
            return default
        if not isinstance(value, kinds):
            self.problems.append(f"{where}: expected {_kind_name(kinds)}, got {type(value).__name__}")
            return default
        if choices is not None and value not in choices:
            self.problems.append(f"{where}: must be one of {', '.join(map(str, choices))} (got {value!r})")
            return default
        if minimum is not None and value < minimum:
            self.problems.append(f"{where}: must be >= {minimum} (got {value})")
            return default
        if maximum is not None and value > maximum:
            self.problems.append(f"{where}: must be <= {maximum} (got {value})")
            return default
        return value

    def section(self, key: str) -> "_Section":
        self.used.add(key)
        return _Section(self.data.get(key), self._where(key), self.problems)

    def finish(self) -> None:
        for key in self.data:
            if key not in self.used:
                self.problems.append(f"{self._where(str(key))}: unknown key")


def _kind_name(kinds: tuple[type, ...]) -> str:
    names = {bool: "true/false", int: "an integer", float: "a number", str: "text", list: "a list", dict: "a mapping"}
    return " or ".join(names.get(k, k.__name__) for k in kinds)


def parse_timecode(text: str, fps: float) -> float:
    """``HH:MM:SS:FF`` (non-drop) to seconds."""
    m = _TIMECODE_RE.match(text.strip())
    if not m:
        raise ValueError(f"not a timecode (HH:MM:SS:FF): {text!r}")
    h, mi, s, f = (int(x) for x in m.groups())
    if mi > 59 or s > 59 or f >= fps:
        raise ValueError(f"timecode out of range: {text!r}")
    return h * 3600 + mi * 60 + s + f / fps


def _resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


# --------------------------------------------------------------------------
# Loading


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError([f"cannot read {path}: {exc}"]) from exc
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError([f"{path.name}: invalid JSON: {exc}"]) from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - PyYAML is a dependency
            raise ConfigError(["PyYAML is not installed; install it or use a .json config"]) from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError([f"{path.name}: invalid YAML: {exc}"]) from exc
    return parse_config(data, path)


def parse_config(data: Any, path: Path) -> Config:
    problems: list[str] = []
    warnings: list[str] = []
    root = _Section(data, "", problems)

    show = root.section("show")
    name = show.get("name", str, path.stem)
    base_dir = path.parent
    media_dir_raw = show.get("media_dir", str, ".")
    media_dir = _resolve(base_dir, media_dir_raw)
    show.finish()

    stage = _parse_stage(root.section("stage"))
    mpv = _parse_mpv(root.section("mpv"), base_dir)
    filler = _parse_filler(root.section("filler"), media_dir, warnings)
    transitions = _parse_transitions(root.section("transitions"))
    sync = _parse_sync(root.section("sync"))
    midi = _parse_midi(root.section("midi"))
    osc = _parse_osc(root.section("osc"))
    keyboard = _parse_keyboard(root.section("keyboard"))
    logging_cfg = _parse_logging(root.section("logging"), base_dir)
    test_mode = root.get("test_mode", bool, False)
    cues = _parse_cues(root.raw("cues"), media_dir, stage.fps, midi, problems, warnings)
    root.finish()

    if problems:
        raise ConfigError(problems)
    return Config(
        path=path,
        base_dir=base_dir,
        name=name,
        stage=stage,
        mpv=mpv,
        filler=filler,
        transitions=transitions,
        sync=sync,
        midi=midi,
        osc=osc,
        keyboard=keyboard,
        logging=logging_cfg,
        test_mode=test_mode,
        cues=tuple(cues),
        warnings=tuple(warnings),
    )


def _parse_stage(s: _Section) -> StageConfig:
    screen = s.get("screen", int, None, minimum=0, maximum=32, nullable=True)
    screen_name = s.get("screen_name", str, None, nullable=True)
    cfg = StageConfig(
        screen=screen,
        screen_name=screen_name or None,
        width=s.get("width", int, 1920, minimum=16),
        height=s.get("height", int, 1080, minimum=16),
        fps=s.get("fps", float, 25.0, minimum=1.0, maximum=240.0),
    )
    s.finish()
    return cfg


def _parse_mpv(s: _Section, base_dir: Path) -> MpvConfig:
    path = s.get("path", str, "mpv")
    # A bare program name is looked up on PATH; anything with a separator is a
    # path relative to the config file.
    if any(sep in path for sep in ("/", "\\")):
        path = str(_resolve(base_dir, path))
    extra = s.get("extra_args", list, [])
    if not all(isinstance(a, str) for a in extra):
        s.problems.append("mpv.extra_args: must be a list of strings")
        extra = []
    vo = s.get("vo", str, None, nullable=True)
    cfg = MpvConfig(
        path=path,
        hwdec=s.get("hwdec", str, "auto-safe"),
        vo=vo or None,
        priority=s.get("priority", str, "above_normal", choices=("normal", "above_normal", "high")),
        extra_args=tuple(extra),
        startup_timeout=s.get("startup_timeout", float, 15.0, minimum=1.0),
    )
    s.finish()
    return cfg


def _parse_filler(s: _Section, media_dir: Path, warnings: list[str]) -> FillerConfig:
    mode = s.get("mode", str, "color", choices=FILLER_MODES)
    color = s.get("color", str, "#000000")
    if not _COLOR_RE.match(color):
        s.problems.append(f"filler.color: expected #RRGGBB, got {color!r}")
        color = "#000000"
    file_raw = s.get("file", str, None, nullable=True)
    file = _resolve(media_dir, file_raw) if file_raw else None
    if mode in ("image", "loop"):
        if file is None:
            s.problems.append(f"filler.file: required when filler.mode is {mode!r}")
        elif not file.is_file():
            warnings.append(f"filler file not found: {file} (the filler colour will be used)")
    s.finish()
    return FillerConfig(mode=mode, file=file, color=color)


def _parse_transitions(s: _Section) -> TransitionConfig:
    cfg = TransitionConfig(
        fade_out=s.get("fade_out", float, 0.5, minimum=0.0, maximum=10.0),
        fade_in=s.get("fade_in", float, 0.5, minimum=0.0, maximum=10.0),
    )
    s.finish()
    return cfg


def _parse_sync(s: _Section) -> SyncConfig:
    c = s.section("correction")
    correction = CorrectionConfig(
        enabled=c.get("enabled", bool, True),
        threshold=c.get("threshold", float, 0.040, minimum=0.001),
        target=c.get("target", float, 0.010, minimum=0.0),
        max_speed_change=c.get("max_speed_change", float, 0.01, minimum=0.0, maximum=0.5),
        correction_time=c.get("correction_time", float, 4.0, minimum=0.1),
        hard_seek=c.get("hard_seek", float, 1.0, minimum=0.1, nullable=True),
    )
    c.finish()
    if correction.target >= correction.threshold:
        s.problems.append("sync.correction.target must be smaller than sync.correction.threshold")
    m = s.section("mtc")
    mtc = MtcConfig(
        enabled=m.get("enabled", bool, False),
        on_stop=m.get("on_stop", str, "continue", choices=MTC_ON_STOP),
        timeout=m.get("timeout", float, 0.5, minimum=0.1),
    )
    m.finish()
    cfg = SyncConfig(
        latency_compensation=s.get("latency_compensation", float, 0.0, minimum=-5.0, maximum=5.0),
        retrigger_guard=s.get("retrigger_guard", float, 1.0, minimum=0.0),
        resume_after_crash=s.get("resume_after_crash", bool, True),
        stall_timeout=s.get("stall_timeout", float, 2.0, minimum=0.5),
        correction=correction,
        mtc=mtc,
    )
    s.finish()
    return cfg


def _parse_midi(s: _Section) -> MidiConfig:
    enabled = s.get("enabled", bool, True)
    port = s.get("port", str, "")
    if enabled and not port:
        s.problems.append('midi.port: required when MIDI is enabled (a part of the input port name, or "*" for the first port)')
    channel_raw = s.raw("channel", 0)
    if channel_raw in ("any", None):
        channel = 0
    elif isinstance(channel_raw, int) and not isinstance(channel_raw, bool) and 0 <= channel_raw <= 16:
        channel = channel_raw
    else:
        s.problems.append(f'midi.channel: must be 1-16 or "any" (got {channel_raw!r})')
        channel = 0
    notes_raw = s.get("command_notes", dict, {})
    command_notes: dict[str, int] = {}
    for command, note in notes_raw.items():
        if command not in COMMANDS or command == "quit":
            s.problems.append(f"midi.command_notes.{command}: unknown command (use {', '.join(c for c in COMMANDS if c != 'quit')})")
        elif not isinstance(note, int) or isinstance(note, bool) or not 0 <= note <= 127:
            s.problems.append(f"midi.command_notes.{command}: must be a note number 0-127")
        else:
            command_notes[command] = note
    cfg = MidiConfig(
        enabled=enabled,
        port=port,
        channel=channel,
        backend=s.get("backend", str, "auto", choices=("auto", "winmm", "rtmidi")),
        command_notes=command_notes,
        reconnect_interval=s.get("reconnect_interval", float, 2.0, minimum=0.2),
    )
    s.finish()
    return cfg


def _parse_osc(s: _Section) -> OscConfig:
    prefix = s.get("prefix", str, "/aethra")
    if not prefix.startswith("/") or prefix.endswith("/"):
        s.problems.append(f"osc.prefix: must start with '/' and not end with '/' (got {prefix!r})")
    cfg = OscConfig(
        enabled=s.get("enabled", bool, False),
        bind=s.get("bind", str, "0.0.0.0"),
        port=s.get("port", int, 9000, minimum=1, maximum=65535),
        prefix=prefix,
        require_playing=s.get("require_playing", bool, False),
    )
    s.finish()
    return cfg


def _parse_keyboard(s: _Section) -> KeyboardConfig:
    keys = dict(DEFAULT_KEYS)
    raw = s.get("keys", dict, {})
    for command, spec in raw.items():
        if command not in COMMANDS:
            s.problems.append(f"keyboard.keys.{command}: unknown command (use {', '.join(COMMANDS)})")
            continue
        specs = [spec] if isinstance(spec, str) else spec
        if specs is None:
            specs = []
        if not isinstance(specs, list) or not all(isinstance(x, str) for x in specs):
            s.problems.append(f"keyboard.keys.{command}: expected a key name or a list of key names")
            continue
        for item in specs:
            try:
                parse_key_spec(item)
            except KeySpecError as exc:
                s.problems.append(f"keyboard.keys.{command}: {exc}")
        keys[command] = tuple(specs)
    seen: dict[str, str] = {}
    for command, specs in keys.items():
        for item in specs:
            try:
                norm = parse_key_spec(item).canonical
            except KeySpecError:
                continue
            if norm in seen and seen[norm] != command:
                s.problems.append(f"keyboard.keys: {item!r} is bound to both {seen[norm]!r} and {command!r}")
            seen[norm] = command
    cfg = KeyboardConfig(
        global_hotkeys=s.get("global_hotkeys", bool, True),
        console=s.get("console", bool, True),
        stage_window=s.get("stage_window", bool, True),
        keys=keys,
    )
    s.finish()
    return cfg


def _parse_logging(s: _Section, base_dir: Path) -> LoggingConfig:
    cfg = LoggingConfig(
        dir=_resolve(base_dir, s.get("dir", str, "logs")),
        mpv_logs=s.get("mpv_logs", bool, True),
        status_interval=s.get("status_interval", float, 1.0, minimum=0.1),
    )
    s.finish()
    return cfg


def _parse_cues(
    raw: Any,
    media_dir: Path,
    fps: float,
    midi: MidiConfig,
    problems: list[str],
    warnings: list[str],
) -> list[Cue]:
    if raw is None:
        problems.append("cues: at least one cue is required")
        return []
    if not isinstance(raw, list):
        problems.append("cues: expected a list")
        return []
    cues: list[Cue] = []
    ids: dict[str, int] = {}
    notes: dict[int, str] = {}
    programs: dict[int, str] = {}
    osc_triggers: dict[tuple[str, Any], str] = {}
    command_notes = {note: command for command, note in midi.command_notes.items()}
    for i, item in enumerate(raw):
        s = _Section(item, f"cues[{i}]", problems)
        cue_id = s.raw("id")
        if isinstance(cue_id, int) and not isinstance(cue_id, bool):
            cue_id = str(cue_id)
        if not isinstance(cue_id, str) or not cue_id.strip():
            problems.append(f"cues[{i}].id: required (text)")
            cue_id = f"#{i + 1}"
        cue_id = cue_id.strip()
        if any(ch.isspace() for ch in cue_id):
            problems.append(f"cues[{i}].id: must not contain spaces ({cue_id!r})")
        if cue_id in ids:
            problems.append(f"cues[{i}].id: duplicate id {cue_id!r} (also cues[{ids[cue_id]}])")
        ids[cue_id] = i
        where = f"cues[{i}] ({cue_id})"

        file_raw = s.get("file", str, None)
        if not file_raw:
            problems.append(f"{where}.file: required")
            file = media_dir / "missing"
        else:
            file = _resolve(media_dir, file_raw)
            if not file.is_file():
                warnings.append(f"cue {cue_id}: media file not found: {file}")

        note = s.get("midi_note", int, None, minimum=0, maximum=127, nullable=True)
        if note is not None:
            if note in notes:
                problems.append(f"{where}.midi_note: note {note} is already used by cue {notes[note]}")
            elif note in command_notes:
                problems.append(f"{where}.midi_note: note {note} is already midi.command_notes.{command_notes[note]}")
            notes[note] = cue_id
        program = s.get("midi_program", int, None, minimum=0, maximum=127, nullable=True)
        if program is not None:
            if program in programs:
                problems.append(f"{where}.midi_program: program {program} is already used by cue {programs[program]}")
            programs[program] = cue_id

        osc_trigger = None
        osc_raw = s.raw("osc")
        if isinstance(osc_raw, str):
            osc_trigger = OscTrigger(osc_raw)
        elif isinstance(osc_raw, dict):
            o = _Section(osc_raw, f"{where}.osc", problems)
            address = o.get("address", str, "")
            arg = o.raw("arg")
            o.finish()
            if arg is not None and (isinstance(arg, bool) or not isinstance(arg, (str, int, float))):
                problems.append(f"{where}.osc.arg: must be text or a number")
                arg = None
            osc_trigger = OscTrigger(address, arg)
        elif osc_raw is not None:
            problems.append(f"{where}.osc: expected an address or {{address, arg}}")
        if osc_trigger is not None:
            if not osc_trigger.address.startswith("/"):
                problems.append(f"{where}.osc: address must start with '/'")
            key = (osc_trigger.address, osc_trigger.arg)
            if key in osc_triggers:
                problems.append(f"{where}.osc: same trigger as cue {osc_triggers[key]}")
            osc_triggers[key] = cue_id

        mtc_start = None
        mtc_raw = s.raw("mtc_start")
        if mtc_raw is not None:
            try:
                mtc_start = parse_timecode(str(mtc_raw), fps)
            except ValueError as exc:
                problems.append(f"{where}.mtc_start: {exc}")

        cues.append(
            Cue(
                index=i,
                id=cue_id,
                title=s.get("title", str, ""),
                file=file,
                midi_note=note,
                midi_program=program,
                osc=osc_trigger,
                offset=s.get("offset", float, 0.0, minimum=-60.0, maximum=3600.0),
                on_end=s.get("on_end", str, "filler", choices=ON_END),
                fade_in=s.get("fade_in", float, 0.0, minimum=0.0, maximum=10.0),
                mtc_start=mtc_start,
            )
        )
        s.finish()
    if not cues:
        problems.append("cues: at least one cue is required")
    return cues
