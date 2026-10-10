"use strict";
/* Aethra control panel. Plain JavaScript, no build step, nothing from the internet. */

// ---------------------------------------------------------------- api

const api = {
  async get(path) {
    const r = await fetch(path, { headers: { "X-Aethra-Token": window.AETHRA_TOKEN } });
    return r.json();
  },
  async post(path, body = {}) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Aethra-Token": window.AETHRA_TOKEN },
      body: JSON.stringify(body),
    });
    return r.json();
  },
};

// ---------------------------------------------------------------- state

const S = {
  meta: {},
  path: "",
  data: null,        // the show file being edited (plain object)
  savedJson: "",     // what is on disk
  text: "",
  validation: null,
  status: { running: false },
  media: { files: [] },
  devices: null,
  page: "live",
};

const clone = (x) => JSON.parse(JSON.stringify(x));
const dirty = () => S.data !== null && JSON.stringify(S.data) !== S.savedJson;

function getPath(obj, path) {
  let cur = obj;
  for (const k of path) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[k];
  }
  return cur;
}

function setPath(obj, path, value) {
  let cur = obj;
  for (let i = 0; i < path.length - 1; i++) {
    if (cur[path[i]] == null || typeof cur[path[i]] !== "object") cur[path[i]] = {};
    cur = cur[path[i]];
  }
  const last = path[path.length - 1];
  if (value === undefined) delete cur[last];
  else cur[last] = value;
  // Empty sections disappear from the file again.
  for (let i = path.length - 2; i >= 0; i--) {
    const parent = getPath(obj, path.slice(0, i));
    const key = path[i];
    if (parent && parent[key] && typeof parent[key] === "object" && !Array.isArray(parent[key]) && Object.keys(parent[key]).length === 0
        && !["intro", "outro"].includes(key)) delete parent[key];
  }
}

// ---------------------------------------------------------------- small UI helpers

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function toast(text, kind = "") {
  const t = h("div", { class: "toast " + kind }, text);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3500);
}

function modal(content, onClose) {
  const m = document.getElementById("modal");
  const body = document.getElementById("modal-body");
  body.innerHTML = "";
  body.append(content);
  m.classList.remove("hidden");
  const close = () => { m.classList.add("hidden"); if (onClose) onClose(); };
  m.onclick = (e) => { if (e.target === m) close(); };
  return close;
}

function fmtTime(sec) {
  if (sec == null || isNaN(sec)) return "–";
  sec = Math.max(0, sec);
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
const fmtSize = (b) => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : (b / 1e3).toFixed(0) + " kB";
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const noteName = (n) => (n == null || n === "" || isNaN(n)) ? "" : NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1); // C4 = 60

// ---------------------------------------------------------------- schema of the show file

const ENGINES = ["compositor", "windows"];
const TRANSITIONS = () => S.meta.transition_types || ["cut", "fade", "fadeblack", "fadewhite"];

const SECTIONS = {
  show: { title: "Show", fields: [
    { k: "name", t: "text", label: "Name", help: "Shown in the console and logs." },
    { k: "media_dir", t: "text", label: "Media folder", def: ".", help: "Folder with the clips, relative to the show file." },
  ] },
  stage: { title: "Stage output", fields: [
    { k: "engine", t: "select", opts: ENGINES, def: "compositor", label: "Display engine", help: "compositor: one window, all transitions on the GPU. windows: one mpv window per deck, transitions dip through black." },
    { k: "screen", t: "monitor", label: "Stage screen", nullable: true, help: "Leave empty: the first screen that is not the main display." },
    { k: "screen_name", t: "text", label: "Screen name", nullable: true, help: "Instead of the index, e.g. \\\\.\\DISPLAY2." },
    { k: "width", t: "int", def: 1920, min: 16, label: "Width" },
    { k: "height", t: "int", def: 1080, min: 16, label: "Height" },
    { k: "fps", t: "float", def: 25, min: 1, max: 240, label: "Frame rate", help: "Master frame rate of the clips." },
  ] },
  mpv: { title: "Players (mpv)", fields: [
    { k: "path", t: "text", def: "mpv", label: "mpv executable", help: "mpv/mpv.exe in the bundle." },
    { k: "libmpv", t: "text", nullable: true, label: "libmpv", help: "Compositor engine. Default: libmpv-2.dll next to mpv.exe." },
    { k: "hwdec", t: "select", opts: ["auto-safe", "auto", "auto-copy", "d3d11va", "d3d11va-copy", "dxva2", "dxva2-copy", "nvdec", "nvdec-copy", "no"], def: "auto-safe", label: "Hardware decoding", help: "Choose with the codec benchmark." },
    { k: "vo", t: "select", opts: ["", "gpu-next", "gpu", "direct3d"], nullable: true, label: "Video output", help: "Window engine only; empty = mpv's default." },
    { k: "priority", t: "select", opts: ["normal", "above_normal", "high"], def: "above_normal", label: "Process priority" },
    { k: "startup_timeout", t: "float", def: 15, min: 1, label: "Startup timeout (s)" },
    { k: "extra_args", t: "list", label: "Extra mpv options", help: "One per line, e.g. --d3d11-flip=no" },
  ] },
  transitions: { title: "Transitions", fields: [
    { k: "xfade_in_duration", t: "float", def: 0, min: 0, max: 30, step: 0.1, label: "In: duration (s)", help: "0 = a cut: the first frame is on stage at the trigger." },
    { k: "xfade_in_type", t: "transition", def: "fade", label: "In: type" },
    { k: "xfade_out_duration", t: "float", def: 1, min: 0, max: 30, step: 0.1, label: "Out: duration (s)", help: "Starts this long before the clip ends." },
    { k: "xfade_out_type", t: "transition", def: "fade", label: "Out: type" },
  ] },
  sync: { title: "Synchronisation", fields: [
    { k: "latency_compensation", t: "float", def: 0, min: -5, max: 5, step: 0.001, label: "Latency compensation (s)", help: "The picture is shown this much earlier. Measure it (testing, test 5)." },
    { k: "retrigger_guard", t: "float", def: 1, min: 0, label: "Retrigger guard (s)" },
    { k: "resume_after_crash", t: "bool", def: true, label: "Rejoin after a crash" },
    { k: "stall_timeout", t: "float", def: 2, min: 0.5, label: "Stall timeout (s)" },
  ] },
  "sync.correction": { title: "Drift correction", fields: [
    { k: "enabled", t: "bool", def: true, label: "Enabled" },
    { k: "threshold", t: "float", def: 0.04, step: 0.001, label: "Threshold (s)" },
    { k: "target", t: "float", def: 0.01, step: 0.001, label: "Target (s)" },
    { k: "max_speed_change", t: "float", def: 0.01, step: 0.001, label: "Max speed change" },
    { k: "correction_time", t: "float", def: 4, label: "Correction time (s)" },
    { k: "hard_seek", t: "float", def: 1, nullable: true, label: "Hard seek above (s)", help: "Empty = never seek." },
  ] },
  "sync.mtc": { title: "MIDI Time Code", fields: [
    { k: "enabled", t: "bool", def: false, label: "Use MTC" },
    { k: "on_stop", t: "select", opts: ["continue", "filler"], def: "continue", label: "When MTC stops" },
    { k: "timeout", t: "float", def: 0.5, label: "Timeout (s)" },
  ] },
  midi: { title: "MIDI", fields: [
    { k: "enabled", t: "bool", def: true, label: "Enabled" },
    { k: "port", t: "midiport", label: "Input port", help: "Part of the input name, or * for the first input." },
    { k: "channel", t: "channel", def: "any", label: "Channel" },
    { k: "backend", t: "select", opts: ["auto", "winmm", "rtmidi"], def: "auto", label: "Backend" },
    { k: "reconnect_interval", t: "float", def: 2, label: "Reconnect check (s)" },
  ] },
  osc: { title: "OSC", fields: [
    { k: "enabled", t: "bool", def: false, label: "Enabled", help: "The control panel controls the running show over OSC: keep it on." },
    { k: "bind", t: "text", def: "0.0.0.0", label: "Listen address" },
    { k: "port", t: "int", def: 9000, min: 1, max: 65535, label: "UDP port" },
    { k: "prefix", t: "text", def: "/aethra", label: "Address prefix" },
    { k: "require_playing", t: "bool", def: false, label: "Only while REAPER plays" },
  ] },
  keyboard: { title: "Keyboard", fields: [
    { k: "global_hotkeys", t: "bool", def: true, label: "Global hotkeys" },
    { k: "console", t: "bool", def: true, label: "Keys in the console" },
    { k: "stage_window", t: "bool", def: true, label: "Keys in the stage window" },
  ] },
  logging: { title: "Logging", fields: [
    { k: "dir", t: "text", def: "logs", label: "Log folder" },
    { k: "mpv_logs", t: "bool", def: true, label: "Player logs" },
    { k: "status_interval", t: "float", def: 1, label: "Status refresh (s)" },
  ] },
};
const BACKGROUND_FIELDS = [
  { k: "mode", t: "select", opts: ["color", "image", "loop"], def: "color", label: "Content" },
  { k: "file", t: "mediafile", nullable: true, label: "File", help: "For image/loop. If it is missing or broken, the colour is used." },
  { k: "color", t: "color", def: "#000000", label: "Colour" },
];

// ---------------------------------------------------------------- form fields

function fieldControl(f, value, onChange) {
  const def = f.def;
  let input;
  const emit = (v) => onChange(v);
  switch (f.t) {
    case "bool":
      input = h("input", { type: "checkbox" });
      input.checked = value === undefined ? !!def : !!value;
      input.onchange = () => emit(input.checked);
      return input;
    case "int": case "float":
      input = h("input", { type: "number", step: f.step || (f.t === "int" ? 1 : "any"), min: f.min, max: f.max, placeholder: def !== undefined ? String(def) : (f.nullable ? "–" : "") });
      if (value !== undefined && value !== null) input.value = value;
      input.oninput = () => {
        if (input.value === "") return emit(f.nullable && value === null ? null : undefined);
        const n = f.t === "int" ? parseInt(input.value, 10) : parseFloat(input.value);
        if (!isNaN(n)) emit(n);
      };
      return input;
    case "select": case "transition": {
      const opts = f.t === "transition" ? TRANSITIONS() : f.opts;
      input = h("select");
      input.append(h("option", { value: "" }, def !== undefined ? `default (${def})` : "—"));
      for (const o of opts) if (o !== "") input.append(h("option", { value: o }, f.t === "transition" ? transitionLabel(o) : o));
      input.value = value == null ? "" : String(value);
      input.onchange = () => emit(input.value === "" ? undefined : input.value);
      return input;
    }
    case "color": {
      const v = value || def || "#000000";
      const picker = h("input", { type: "color", value: v });
      const text = h("input", { type: "text", value: value || "", placeholder: def, style: { maxWidth: "110px" } });
      picker.oninput = () => { text.value = picker.value; emit(picker.value); };
      text.oninput = () => { if (/^#[0-9a-fA-F]{6}$/.test(text.value)) { picker.value = text.value; emit(text.value); } else if (!text.value) emit(undefined); };
      return h("span", { class: "row" }, picker, text);
    }
    case "list": {
      input = h("textarea", { rows: 3, placeholder: "one per line" });
      input.value = (value || []).join("\n");
      input.oninput = () => { const items = input.value.split("\n").map((s) => s.trim()).filter(Boolean); emit(items.length ? items : undefined); };
      return input;
    }
    case "channel": {
      input = h("select");
      input.append(h("option", { value: "" }, "any (default)"));
      for (let c = 1; c <= 16; c++) input.append(h("option", { value: String(c) }, String(c)));
      input.value = value == null || value === "any" ? "" : String(value);
      input.onchange = () => emit(input.value === "" ? undefined : parseInt(input.value, 10));
      return input;
    }
    case "monitor": {
      input = h("select");
      input.append(h("option", { value: "" }, "automatic (first screen that is not the main display)"));
      for (const m of (S.devices && S.devices.monitors) || []) input.append(h("option", { value: String(m.index) }, `#${m.index} ${m.name} ${m.width}×${m.height}${m.primary ? " (main)" : ""}`));
      if (value != null && ![...input.options].some((o) => o.value === String(value))) input.append(h("option", { value: String(value) }, `#${value} (not connected)`));
      input.value = value == null ? "" : String(value);
      input.onchange = () => emit(input.value === "" ? undefined : parseInt(input.value, 10));
      return input;
    }
    case "midiport": {
      input = h("input", { type: "text", value: value || "", list: "midi-ports", placeholder: "* = first input" });
      input.oninput = () => emit(input.value || undefined);
      const dl = h("datalist", { id: "midi-ports" }, ((S.devices && S.devices.midi) || []).map((n) => h("option", { value: n })));
      return h("span", { class: "row", style: { flex: 1 } }, input, dl);
    }
    case "mediafile": {
      input = h("input", { type: "text", value: value || "", list: "media-files", placeholder: "—" });
      input.oninput = () => emit(input.value || (f.nullable ? undefined : ""));
      return h("span", { class: "row", style: { flex: 1 } }, input, mediaDatalist());
    }
    default:
      input = h("input", { type: "text", value: value == null ? "" : value, placeholder: def !== undefined ? String(def) : "" });
      input.oninput = () => emit(input.value === "" ? undefined : input.value);
      return input;
  }
}

function mediaDatalist() {
  return h("datalist", { id: "media-files" }, S.media.files.map((f) => h("option", { value: f.name })));
}

function fieldRow(f, base, opts = {}) {
  const path = [...base, f.k];
  const value = getPath(S.data, path);
  const row = h("div", { class: "field" + (value !== undefined ? " set" : "") });
  const ctl = fieldControl(f, value, (v) => {
    setPath(S.data, path, v);
    row.classList.toggle("set", getPath(S.data, path) !== undefined);
    changed(opts.rerender);
  });
  const reset = h("button", { class: "ghost reset", title: "Use the default", onclick: () => { setPath(S.data, path, undefined); changed(true); } }, "default");
  row.append(h("label", {}, f.label || f.k), h("div", { class: "ctl" }, ctl, reset));
  if (f.help) row.append(h("div", { class: "help" }, f.help));
  return row;
}

function sectionCard(key, extra) {
  const sec = SECTIONS[key];
  const base = key.split(".");
  return h("div", { class: "card" }, h("h2", {}, sec.title), sec.fields.map((f) => fieldRow(f, base)), extra || null);
}

// ---------------------------------------------------------------- validation and saving

let validateTimer = null;
function changed(rerender = false) {
  updateTopbar();
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validate, 350);
  if (rerender) render();
}

async function validate() {
  if (!S.data) return;
  S.validation = await api.post("/api/validate", { data: S.data });
  updateTopbar();
}

function updateTopbar() {
  const v = S.validation;
  const badge = document.getElementById("validity");
  const box = document.getElementById("problems");
  box.innerHTML = "";
  if (!S.data) { badge.className = "badge bad"; badge.textContent = "no show file"; }
  else if (!v) { badge.className = "badge"; badge.textContent = "checking…"; }
  else if (!v.ok) {
    badge.className = "badge bad"; badge.textContent = `✕ ${v.problems.length} problem${v.problems.length > 1 ? "s" : ""}`;
    box.className = ""; box.append(h("b", {}, "The show file has problems (Aethra would not start):"), h("ul", {}, v.problems.map((p) => h("li", {}, p))));
  } else if (v.warnings.length) {
    badge.className = "badge warn"; badge.textContent = `⚠ ${v.warnings.length} warning${v.warnings.length > 1 ? "s" : ""}`;
    box.className = "warn"; box.append(h("b", {}, "Warnings (the show starts anyway):"), h("ul", {}, v.warnings.map((p) => h("li", {}, p))));
  } else { badge.className = "badge ok"; badge.textContent = "✓ valid"; }
  if (!box.childNodes.length) box.className = "hidden";
  const d = dirty();
  document.getElementById("btn-save").disabled = !d;
  document.getElementById("btn-revert").disabled = !d;
  document.title = (d ? "● " : "") + "Aethra – " + (S.path.split(/[\\/]/).pop() || "");
}

async function save(force = false) {
  if (!S.data) return;
  const r = await api.post("/api/save", { data: S.data, force });
  if (r.error) return toast(r.error, "bad");
  S.validation = r;
  if (!r.saved) {
    const close = modal(h("div", {}, h("h2", {}, "The show file has problems"),
      h("ul", {}, r.problems.map((p) => h("li", {}, p))),
      h("p", { class: "muted" }, "Aethra will not start with this file. Save anyway (e.g. to finish later)?"),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { onclick: () => close() }, "Cancel"),
        h("button", { class: "danger", onclick: () => { close(); save(true); } }, "Save anyway"))));
    updateTopbar();
    return;
  }
  S.savedJson = JSON.stringify(S.data);
  S.text = r.text;
  toast("Saved " + S.path.split(/[\\/]/).pop() + " (previous version: .bak)", "ok");
  updateTopbar();
}

async function loadConfig(cfg) {
  S.path = cfg.path;
  S.text = cfg.text || "";
  document.getElementById("file-name").textContent = cfg.path.split(/[\\/]/).pop();
  document.getElementById("file-path").textContent = cfg.path;
  if (cfg.error) toast(cfg.error, "bad");
  S.data = cfg.data && typeof cfg.data === "object" ? cfg.data : null;
  S.savedJson = S.data ? JSON.stringify(S.data) : "";
  S.validation = null;
  updateTopbar();
  if (S.data) { validate(); refreshMedia(); }
  render();
}

// ---------------------------------------------------------------- pages

const PAGES = [
  { group: "Show" },
  { id: "live", ico: "◉", title: "Live" },
  { id: "playlist", ico: "☰", title: "Playlist" },
  { id: "transitions", ico: "◐", title: "Transitions" },
  { id: "backgrounds", ico: "▣", title: "Intro · filler · outro" },
  { group: "Setup" },
  { id: "stage", ico: "▭", title: "Stage & players" },
  { id: "inputs", ico: "⎍", title: "MIDI · OSC · keys" },
  { id: "sync", ico: "⟲", title: "Sync & logging" },
  { id: "media", ico: "🎞", title: "Media" },
  { group: "Tools" },
  { id: "checks", ico: "✓", title: "Checks & rehearsal" },
  { id: "codec", ico: "⚙", title: "Codec choice" },
  { id: "windows", ico: "⊞", title: "Windows setup" },
  { id: "logs", ico: "≡", title: "Logs & reports" },
  { id: "yaml", ico: "{}", title: "Show file" },
];

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const p of PAGES) {
    if (p.group) { nav.append(h("div", { class: "group" }, p.group)); continue; }
    const a = h("a", { class: S.page === p.id ? "active" : "", onclick: () => go(p.id) }, h("span", { class: "ico" }, p.ico), p.title);
    if (p.id === "live" && S.status.running) a.append(h("span", { class: "dot", style: { background: "var(--live)" } }));
    nav.append(a);
  }
}

function go(id) {
  S.page = id;
  location.hash = id;
  render();
}

function render() {
  renderNav();
  const page = document.getElementById("page");
  const scroll = page.scrollTop;
  page.innerHTML = "";
  if (!S.data && !["yaml", "windows", "logs", "checks", "codec"].includes(S.page)) {
    page.append(noConfigPage());
    return;
  }
  const fn = { live: pageLive, playlist: pagePlaylist, transitions: pageTransitions, backgrounds: pageBackgrounds, stage: pageStage,
    inputs: pageInputs, sync: pageSync, media: pageMedia, checks: pageChecks, codec: pageCodec, windows: pageWindows, logs: pageLogs, yaml: pageYaml }[S.page];
  page.append(fn());
  page.scrollTop = scroll;
}

function noConfigPage() {
  return h("div", { class: "card" }, h("h1", {}, "No show file"),
    h("p", { class: "lead" }, `${S.path} does not exist or cannot be read.`),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: async () => loadConfig(await api.post("/api/open", { path: S.path, create: true })) }, "Create it from the example"),
      h("button", { onclick: openDialog }, "Open another file…")));
}

// ---- live

let liveEventsName = null;
function pageLive() {
  const st = S.status;
  const cues = (S.data && S.data.cues) || [];
  const running = !!st.running;
  const live = st.live;
  const wrap = h("div", {});
  wrap.append(h("div", { class: "row" }, h("h1", {}, "Live"), running ? h("span", { class: "badge live" }, "● ON AIR") : h("span", { class: "badge" }, "stopped"),
    h("span", { class: "spacer" }),
    running ? h("button", { class: "danger", onclick: stopShow }, "Stop the show") : null,
    !running ? h("label", { class: "row" }, h("input", { type: "checkbox", id: "opt-test" }), "test overlay") : null,
    !running ? h("label", { class: "row" }, h("input", { type: "checkbox", id: "opt-fresh" }), "fresh start") : null,
    !running ? h("button", { class: "primary big", onclick: startShow }, "▶ Start the show") : null));
  wrap.append(h("p", { class: "lead" }, running
    ? `Running: ${st.show} (${st.engine} engine). The operator console window shows the same status; keys and MIDI work as usual.`
    : "Start the show here or with the desktop shortcut. Save your changes first: the show reads the file when it starts."));

  const pct = live && live.duration ? Math.min(100, 100 * (live.position || 0) / live.duration) : 0;
  const onair = h("div", { class: "onair" },
    h("div", { class: "mode" }, running ? (st.mode || "") + (st.blackout ? " · BLACKOUT" : "") : "not running"),
    h("div", { class: "title" }, live ? `${live.cue}${live.title ? " – " + live.title : ""}` : running ? (st.mode || "").toUpperCase() : "—"),
    live ? h("div", { class: "row muted" }, `deck ${live.deck}`, live.drift_ms != null ? h("span", { class: "pill cyan" }, `drift ${live.drift_ms > 0 ? "+" : ""}${live.drift_ms} ms`) : null,
      live.leaving ? h("span", { class: "pill warn" }, "leaving") : null) : null,
    h("div", { class: "progress" }, h("div", { style: { width: pct + "%" } })),
    live ? h("div", { class: "row muted" }, fmtTime(live.position), h("span", { class: "spacer" }), "−" + fmtTime((live.duration || 0) - (live.position || 0)), " / ", fmtTime(live.duration)) : null,
    h("div", { class: "kv", style: { marginTop: "14px" } },
      h("div", {}, "Next"), h("div", {}, !running ? (cues[0] ? `${cues[0].id} (first song)` : "—") : st.next ? `${st.next} ${st.next_ready ? "✓ ready" : "loading…"}` : "end of the list"),
      h("div", {}, "MIDI"), h("div", {}, st.midi || "–"),
      h("div", {}, "OSC"), h("div", {}, st.osc || "off"),
      st.mtc ? h("div", {}, "MTC") : null, st.mtc ? h("div", {}, `${st.mtc.status} ${st.mtc.timecode || ""}`) : null,
      h("div", {}, "Players"), h("div", {}, st.decks ? Object.entries(st.decks).map(([k, v]) => h("span", { class: "pill " + (v === "down" ? "bad" : v === "playing" ? "ok" : ""), style: { marginRight: "6px" } }, `${k} ${v}`)) : "–")));

  const cmd = (name, args) => async () => { const r = await api.post("/api/show/command", { data: S.data, name, args }); if (r.error) toast(r.error, "bad"); else setTimeout(pollStatus, 150); };
  const controls = h("div", { class: "card" }, h("h2", {}, "Control"),
    h("div", { class: "controls" },
      h("button", { class: "go", disabled: !running, onclick: cmd("go") }, "GO ▶ " + (st.next || "")),
      h("button", { disabled: !running, onclick: cmd("prev") }, "◀ Prev"),
      h("button", { disabled: !running, onclick: cmd("next") }, "Next ▶"),
      h("button", { class: "black" + (st.blackout ? " on" : ""), disabled: !running, onclick: cmd("black", [st.blackout ? 0 : 1]) }, st.blackout ? "Blackout OFF" : "Blackout"),
      h("button", { disabled: !running, onclick: cmd("filler") }, "Filler"),
      h("button", { disabled: !running || !S.data.intro, onclick: cmd("intro") }, "Intro"),
      h("button", { disabled: !running || !S.data.outro, onclick: cmd("outro") }, "Outro"),
      h("button", { class: st.test_mode ? "on" : "", disabled: !running, onclick: cmd("test", [st.test_mode ? 0 : 1]) }, "Test overlay")),
    (S.data.osc || {}).enabled ? null : h("p", { class: "muted" }, "⚠ OSC is off in the show file: the panel cannot control the running show. Turn it on in MIDI · OSC · keys."));

  const setlist = h("div", { class: "card" }, h("h2", {}, "Set list"), h("div", { class: "setlist" },
    cues.map((c, i) => h("div", { class: "item" + (live && live.cue === c.id && !live.ended ? " live" : st.next === c.id ? " next" : ""),
      title: running ? "Click: select as next · double-click: start now" : "",
      onclick: running ? cmd("select", [i + 1]) : null, ondblclick: running ? cmd("cue", [String(c.id)]) : null },
      h("span", { class: "num" }, i + 1), h("b", {}, c.id), h("span", {}, c.title || ""), h("span", { class: "spacer" }),
      c.midi_note != null ? h("span", { class: "pill" }, `note ${c.midi_note}`) : null))));

  const events = h("div", { class: "card wide" }, h("div", { class: "row" }, h("h2", {}, "Event log"), h("span", { class: "spacer" }), h("small", { id: "ev-name" })), h("div", { class: "events", id: "live-events" }));
  wrap.append(h("div", { class: "stage" }, h("div", {}, onair, h("div", { style: { height: "16px" } }), controls), setlist), h("div", { style: { height: "16px" } }), events);
  loadLiveEvents();
  return wrap;
}

async function loadLiveEvents() {
  const box = document.getElementById("live-events");
  if (!box) return;
  const r = await api.post("/api/log", { data: S.data, limit: 200 });
  if (!document.getElementById("live-events")) return;
  liveEventsName = r.name;
  document.getElementById("ev-name").textContent = r.name || "";
  renderEvents(box, r.events || [], true);
}

function renderEvents(box, events, newestFirst) {
  box.innerHTML = "";
  const list = newestFirst ? [...events].reverse() : events;
  for (const e of list) box.append(h("div", { class: "ev " + (e.level || "") }, h("span", { class: "muted" }, (e.wall || "").slice(11, 23)), h("b", {}, e.event), h("span", {}, e.msg)));
  if (!events.length) box.append(h("div", { class: "muted" }, "no events"));
}

async function startShow() {
  if (dirty() && !confirm("There are unsaved changes. The show starts with the file on disk. Start anyway?")) return;
  const r = await api.post("/api/show/start", { test_mode: document.getElementById("opt-test").checked, fresh: document.getElementById("opt-fresh").checked });
  if (r.error) toast(r.error, "bad"); else toast("Starting the show…", "ok");
  setTimeout(pollStatus, 1500);
}

async function stopShow() {
  if (!confirm("Stop the show? The players close and the stage goes to the desktop.")) return;
  const r = await api.post("/api/show/stop", { data: S.data });
  if (r.error) toast(r.error, "bad"); else toast("Stopping…");
}

// ---- playlist

let dragIndex = null;
function pagePlaylist() {
  const cues = S.data.cues || (S.data.cues = []);
  const probes = Object.fromEntries(S.media.files.map((f) => [f.name, f.probe]));
  const t = S.data.transitions || {};
  const total = cues.reduce((sum, c) => sum + ((probes[c.file] || {}).duration || 0), 0);
  const tbody = h("tbody");
  cues.forEach((c, i) => {
    const probe = probes[c.file];
    const fileOk = S.media.files.some((f) => f.name === c.file);
    const upd = (k, rerender = false) => (v) => { if (v === undefined || v === "") delete c[k]; else c[k] = v; changed(rerender); };
    const num = (k, step = "any") => { const el = h("input", { type: "number", step, value: c[k] ?? "", placeholder: "–" }); el.oninput = () => upd(k)(el.value === "" ? undefined : Number(el.value)); return el; };
    const text = (k, ph = "") => { const el = h("input", { type: "text", value: c[k] ?? "", placeholder: ph }); el.oninput = () => upd(k)(el.value); return el; };
    const noteIn = num("midi_note", 1);
    noteIn.style.minWidth = "64px";
    const noteLbl = h("span", { class: "note-name" }, noteName(c.midi_note));
    noteIn.addEventListener("input", () => { noteLbl.textContent = noteName(Number(noteIn.value)); });
    const onEnd = h("select", {}, ["filler", "hold", "black"].map((o) => h("option", { value: o }, o)));
    onEnd.value = c.on_end || "filler";
    onEnd.onchange = () => upd("on_end")(onEnd.value === "filler" ? undefined : onEnd.value);
    const fileIn = h("input", { type: "text", value: c.file || "", list: "media-files", class: fileOk ? "" : "changed", title: fileOk ? "" : "not in the media folder" });
    fileIn.oninput = () => upd("file")(fileIn.value);
    const xf = ["xfade_in_duration", "xfade_in_type", "xfade_out_duration", "xfade_out_type"].filter((k) => c[k] !== undefined);
    const row = h("tr", { class: "cue-row", draggable: "true" },
      h("td", { class: "handle", title: "drag to reorder" }, "⠿"),
      h("td", { class: "muted" }, i + 1),
      h("td", { style: { width: "80px" } }, text("id")),
      h("td", {}, text("title", "title")),
      h("td", { style: { minWidth: "200px" } }, fileIn,
        probe ? h("div", { class: "tiny muted" }, probe.pending ? "probing…" : probe.ok ? `${fmtTime(probe.duration)} · ${probe.width}×${probe.height} · ${probe.fps || "?"} fps · ${probe.codec || ""}` : h("span", { style: { color: "var(--bad)" } }, (probe.problems || []).join("; "))) : null),
      h("td", { style: { width: "120px" } }, h("div", { class: "row", style: { flexWrap: "nowrap" } }, noteIn, noteLbl,
        h("button", { class: "ghost", title: "Learn: play the note now", onclick: () => learn(c) }, "🎹"))),
      h("td", { style: { width: "80px" } }, num("offset", 0.01)),
      h("td", { style: { width: "90px" } }, onEnd),
      h("td", {}, h("button", { class: "ghost", title: "Transitions of this song", onclick: () => cueTransitions(c) }, xf.length ? h("span", { class: "pill" }, transitionSummary(c, t)) : h("span", { class: "muted tiny" }, "global"))),
      h("td", { style: { whiteSpace: "nowrap" } },
        h("button", { class: "ghost", title: "More: OSC trigger, program change, MTC", onclick: () => cueMore(c) }, "⋯"),
        h("button", { class: "ghost", title: "Duplicate", onclick: () => { cues.splice(i + 1, 0, { ...clone(c), id: nextId(cues) }); changed(true); } }, "⧉"),
        h("button", { class: "ghost", title: "Delete", onclick: () => { if (confirm(`Delete cue ${c.id}?`)) { cues.splice(i, 1); changed(true); } } }, "🗑")));
    row.addEventListener("dragstart", (e) => { dragIndex = i; e.dataTransfer.effectAllowed = "move"; });
    row.addEventListener("dragover", (e) => { e.preventDefault(); row.classList.add("drag-over"); });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      if (dragIndex === null || dragIndex === i) return;
      const [moved] = cues.splice(dragIndex, 1);
      cues.splice(i, 0, moved);
      dragIndex = null;
      changed(true);
    });
    tbody.append(row);
  });
  const unused = S.media.files.filter((f) => !cues.some((c) => c.file === f.name) && f.name !== ((S.data.filler || {}).file));
  return h("div", {},
    h("div", { class: "row" }, h("h1", {}, "Playlist"), h("span", { class: "spacer" }),
      h("span", { class: "muted" }, `${cues.length} songs · ${fmtTime(total)} of video`),
      h("button", { onclick: () => { probeAll(); toast("Reading clip lengths…"); } }, "Read clip lengths"),
      h("button", { class: "primary", onclick: () => { cues.push({ id: nextId(cues), file: "" }); changed(true); } }, "+ Add song")),
    h("p", { class: "lead" }, "One row per song, in set order (GO and next/previous follow it). Drag rows to reorder. MIDI note numbers use C4 = 60 (REAPER's default)."),
    h("div", { class: "card wide", style: { overflowX: "auto" } },
      h("table", {}, h("thead", {}, h("tr", {}, ["", "#", "ID", "Title", "File", "MIDI note", "Offset (s)", "At the end", "Transitions", ""].map((x) => h("th", {}, x)))), tbody),
      mediaDatalist()),
    unused.length ? h("div", { class: "card wide", style: { marginTop: "16px" } }, h("h2", {}, "Clips in the media folder not in the playlist"),
      h("div", { class: "row" }, unused.map((f) => h("button", { onclick: () => { cues.push({ id: nextId(cues), title: f.name.replace(/\.[^.]+$/, ""), file: f.name }); changed(true); } }, "+ " + f.name)))) : null);
}

function nextId(cues) {
  let n = cues.length + 1;
  const ids = new Set(cues.map((c) => String(c.id)));
  while (ids.has(String(n).padStart(2, "0"))) n++;
  return String(n).padStart(2, "0");
}

function transitionSummary(c, t) {
  const inT = c.xfade_in_type ?? t.xfade_in_type ?? "fade", inD = c.xfade_in_duration ?? t.xfade_in_duration ?? 0;
  const outT = c.xfade_out_type ?? t.xfade_out_type ?? "fade", outD = c.xfade_out_duration ?? t.xfade_out_duration ?? 1;
  return `${inD > 0 ? inT + " " + inD + "s" : "cut"} → ${outD > 0 ? outT + " " + outD + "s" : "cut"}`;
}

function cueTransitions(c) {
  const box = h("div", {}, h("h2", {}, `Transitions of ${c.id}${c.title ? " – " + c.title : ""}`),
    h("p", { class: "muted" }, "Empty fields use the global settings (Transitions page)."));
  for (const f of SECTIONS.transitions.fields) {
    const row = h("div", { class: "field" });
    row.append(h("label", {}, f.label), h("div", { class: "ctl" }, fieldControl({ ...f, def: getPath(S.data, ["transitions", f.k]) ?? f.def }, c[f.k], (v) => { if (v === undefined) delete c[f.k]; else c[f.k] = v; changed(); })));
    box.append(row);
  }
  box.append(h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "spacer" }), h("button", { class: "primary", onclick: () => close() }, "Done")));
  const close = modal(box, () => render());
}

function cueMore(c) {
  const osc = typeof c.osc === "object" && c.osc ? c.osc : { address: c.osc || "" };
  const addr = h("input", { type: "text", value: osc.address || "", placeholder: "/song/1" });
  const arg = h("input", { type: "text", value: osc.arg ?? "", placeholder: "optional" });
  const prog = h("input", { type: "number", min: 0, max: 127, value: c.midi_program ?? "" });
  const mtc = h("input", { type: "text", value: c.mtc_start || "", placeholder: "HH:MM:SS:FF" });
  const apply = () => {
    if (!addr.value) delete c.osc; else c.osc = arg.value === "" ? addr.value : { address: addr.value, arg: isNaN(Number(arg.value)) ? arg.value : Number(arg.value) };
    if (prog.value === "") delete c.midi_program; else c.midi_program = Number(prog.value);
    if (!mtc.value) delete c.mtc_start; else c.mtc_start = mtc.value;
    changed(true);
  };
  const close = modal(h("div", {}, h("h2", {}, `More triggers for ${c.id}`),
    h("div", { class: "field" }, h("label", {}, "OSC address"), addr, h("div", { class: "help" }, "An extra OSC message that starts this cue.")),
    h("div", { class: "field" }, h("label", {}, "OSC argument"), arg),
    h("div", { class: "field" }, h("label", {}, "MIDI program change"), prog),
    h("div", { class: "field" }, h("label", {}, "MTC start"), mtc, h("div", { class: "help" }, "REAPER timecode of the trigger (with MTC)." )),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "spacer" }), h("button", { onclick: () => close() }, "Cancel"), h("button", { class: "primary", onclick: () => { apply(); close(); } }, "Apply"))));
}

async function learn(cue) {
  const close = modal(h("div", {}, h("h2", {}, `Learn the note of ${cue.id}`), h("p", {}, "Play the trigger note now (from REAPER or the keyboard)… waiting 10 s.")));
  const r = await api.post("/api/learn", { data: S.data, seconds: 10 });
  close();
  if (r.error) return toast(r.error, "bad");
  if (r.kind === "note") { cue.midi_note = r.value; toast(`Note ${r.value} (${noteName(r.value)}) on channel ${r.channel}`, "ok"); }
  else { cue.midi_program = r.value; toast(`Program change ${r.value} on channel ${r.channel}`, "ok"); }
  changed(true);
}

// ---- transitions

function transitionLabel(type) {
  const names = { cut: "cut", fade: "fade (cross-fade)", fadeblack: "fade through black", fadewhite: "fade through white" };
  return names[type] || "blend: " + type;
}

const CSS_BLEND = { multiply: "multiply", screen: "screen", overlay: "overlay", softlight: "soft-light", hardlight: "hard-light", darken: "darken", lighten: "lighten",
  colordodge: "color-dodge", colorburn: "color-burn", difference: "difference", exclusion: "exclusion", hue: "hue", saturation: "saturation", color: "color", luminosity: "luminosity" };

function previewBox(type, duration) {
  const a = h("div", { class: "a", style: { background: "linear-gradient(135deg,#ff5f6d,#ffc371)" } });
  const b = h("div", { class: "b", style: { background: "radial-gradient(circle at 30% 30%,#22d3ee,#1e3a8a 70%)", opacity: 0 } });
  const tint = h("div", { class: "b", style: { opacity: 0 } });
  const box = h("div", { class: "preview" }, a, b, tint);
  const dur = Math.max(0.6, duration || 1) * 1000;
  let start = performance.now();
  function frame(now) {
    if (!box.isConnected) return;
    let p = ((now - start) % (dur + 1400)) / dur;
    p = Math.min(1, p);
    const e = p * p * (3 - 2 * p);
    b.style.mixBlendMode = "normal"; tint.style.opacity = 0; b.style.opacity = 0;
    if (type === "cut") b.style.opacity = e > 0.5 ? 1 : 0;
    else if (type === "fade") b.style.opacity = e;
    else if (type === "fadeblack" || type === "fadewhite") {
      tint.style.background = type === "fadeblack" ? "#000" : "#fff";
      if (e < 0.5) tint.style.opacity = 2 * e; else { b.style.opacity = 1; tint.style.opacity = 2 - 2 * e; }
    } else {
      // Blend modes: CSS has most of them; the rest preview as a plain fade.
      b.style.mixBlendMode = CSS_BLEND[type] || "normal";
      b.style.opacity = e < 0.5 ? 2 * e : 1;
      if (e >= 0.5) { tint.style.background = b.style.background; tint.style.opacity = 2 * e - 1; }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return box;
}

function pageTransitions() {
  const t = S.data.transitions || {};
  const inType = t.xfade_in_type || "fade", outType = t.xfade_out_type || "fade";
  const types = TRANSITIONS();
  return h("div", {},
    h("h1", {}, "Transitions"),
    h("p", { class: "lead" }, "How a song comes on stage (in) and leaves it (out), whatever the other picture is: intro, filler, outro or the previous song. The out-transition starts before the clip ends, so the filler has taken over at its last frame. Every song can override these settings (Playlist → Transitions)."),
    h("div", { class: "cards" },
      h("div", { class: "card" }, h("h2", {}, "Global settings"), SECTIONS.transitions.fields.map((f) => fieldRow(f, ["transitions"], { rerender: f.t === "transition" })),
        h("p", { class: "muted" }, "With the window engine every type except cut dips through black (fadewhite: through white).")),
      h("div", { class: "card" }, h("h2", {}, "Preview"), h("div", { class: "grid2" },
        h("div", {}, h("small", {}, "In: " + transitionLabel(inType)), previewBox(inType, t.xfade_in_duration || 1)),
        h("div", {}, h("small", {}, "Out: " + transitionLabel(outType)), previewBox(outType, t.xfade_out_duration ?? 1))),
        h("p", { class: "muted" }, "An approximation in the browser; the compositor uses the exact formulas.")),
      h("div", { class: "card wide" }, h("h2", {}, "All types"), h("div", { class: "cards", style: { gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))" } },
        types.map((ty) => h("div", {}, previewBox(ty, 1.2), h("small", {}, transitionLabel(ty))))))));
}

// ---- backgrounds

function pageBackgrounds() {
  const card = (key, title, desc, optional) => {
    const on = S.data[key] !== undefined && S.data[key] !== null;
    const c = h("div", { class: "card" }, h("div", { class: "row" }, h("h2", {}, title), h("span", { class: "spacer" }),
      optional ? h("label", { class: "row" }, h("input", { type: "checkbox", checked: on, onchange: (e) => { if (e.target.checked) S.data[key] = { mode: "color", color: "#000000" }; else delete S.data[key]; changed(true); } }), "use") : null),
      h("p", { class: "muted" }, desc));
    if (on || !optional) {
      if (!S.data[key]) S.data[key] = {};
      c.append(...BACKGROUND_FIELDS.map((f) => fieldRow(f, [key])));
    }
    return c;
  };
  return h("div", {}, h("h1", {}, "Intro · filler · outro"),
    h("p", { class: "lead" }, "Backgrounds: a colour, a still image or a video loop. The intro plays from the start until the first song, the filler between songs, the outro after the last song. Intro and outro can also be called any time (commands intro/outro)."),
    h("div", { class: "cards" },
      card("intro", "Intro", "Before the first song (the first song is preloaded meanwhile).", true),
      card("filler", "Filler", "Between songs.", false),
      card("outro", "Outro", "Automatically after the last song.", true)), mediaDatalist());
}

// ---- stage, inputs, sync

function pageStage() {
  return h("div", {}, h("h1", {}, "Stage & players"),
    h("p", { class: "lead" }, "Where the picture goes and how it is played."),
    h("div", { class: "cards" }, sectionCard("stage", devicesButton()), sectionCard("mpv")));
}

function devicesButton() {
  const ds = S.devices;
  return h("div", { class: "row", style: { marginTop: "10px" } },
    h("button", { onclick: async () => { S.devices = await api.post("/api/devices", { backend: (S.data.midi || {}).backend || "auto" }); render(); } }, "Refresh screens and MIDI inputs"),
    ds ? h("small", {}, `${ds.monitors.length} screens, ${ds.midi.length} MIDI inputs`) : null);
}

function pageInputs() {
  const keys = (S.data.keyboard || {}).keys || {};
  const notes = (S.data.midi || {}).command_notes || {};
  const commands = (S.meta.commands || []).filter((c) => c !== "quit");
  const keyRows = (S.meta.commands || []).map((cmd) => {
    const def = (S.meta.default_keys || {})[cmd] || [];
    const val = keys[cmd];
    const input = h("input", { type: "text", value: val ? [].concat(val).join(", ") : "", placeholder: def.join(", ") || "—" });
    input.oninput = () => {
      const items = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      setPath(S.data, ["keyboard", "keys", cmd], input.value === "" ? undefined : items);
      changed();
    };
    return h("tr", {}, h("td", {}, h("b", {}, cmd)), h("td", {}, input));
  });
  const noteRows = commands.map((cmd) => {
    const input = h("input", { type: "number", min: 0, max: 127, value: notes[cmd] ?? "", placeholder: "—" });
    const lbl = h("span", { class: "note-name" }, noteName(notes[cmd]));
    input.oninput = () => { setPath(S.data, ["midi", "command_notes", cmd], input.value === "" ? undefined : Number(input.value)); lbl.textContent = noteName(Number(input.value)); changed(); };
    return h("tr", {}, h("td", {}, h("b", {}, cmd)), h("td", {}, h("div", { class: "row" }, input, lbl)));
  });
  return h("div", {}, h("h1", {}, "MIDI · OSC · keys"),
    h("p", { class: "lead" }, "Triggers from REAPER and the operator's keys. Per-song notes are on the Playlist page."),
    h("div", { class: "cards" },
      sectionCard("midi", devicesButton()),
      h("div", { class: "card" }, h("h2", {}, "Command notes"), h("p", { class: "muted" }, "Notes on the MIDI channel that run commands (e.g. 121 = filler)."), h("table", {}, h("tbody", {}, noteRows))),
      sectionCard("osc", h("p", { class: "muted" }, "Built-in addresses: /aethra/cue <id>, /go, /next, /prev, /filler, /intro, /outro, /black [1|0], /test [1|0].")),
      h("div", { class: "card" }, h("h2", {}, "Keys"), SECTIONS.keyboard.fields.map((f) => fieldRow(f, ["keyboard"])),
        h("p", { class: "muted" }, "Comma-separated, e.g. space, ctrl+g, f5. Empty = the default shown."), h("table", {}, h("tbody", {}, keyRows)))));
}

function pageSync() {
  const tm = h("input", { type: "checkbox", checked: !!S.data.test_mode, onchange: (e) => { if (e.target.checked) S.data.test_mode = true; else delete S.data.test_mode; changed(); } });
  return h("div", {}, h("h1", {}, "Sync & logging"),
    h("p", { class: "lead" }, "Keeping the picture locked to REAPER, and what is written down for the analysis after the show."),
    h("div", { class: "cards" }, sectionCard("sync"), sectionCard("sync.correction"), sectionCard("sync.mtc"),
      sectionCard("logging", h("div", { class: "field" }, h("label", {}, "Test overlay at start"), h("div", { class: "ctl" }, tm), h("div", { class: "help" }, "Timecode, frame number and drift on the stage (also Ctrl+T during the show).")))));
}

// ---- media

async function refreshMedia() {
  if (!S.data) return;
  S.media = await api.post("/api/media", { data: S.data });
  if (["media", "playlist", "codec"].includes(S.page)) render();
}

async function probeAll() {
  S.media = await api.post("/api/probe", { data: S.data, files: S.media.files.map((f) => f.name) });
  render();
  const poll = setInterval(async () => {
    S.media = await api.post("/api/media", { data: S.data });
    if (["media", "playlist"].includes(S.page)) render();
    if (!S.media.files.some((f) => f.probe && f.probe.pending)) clearInterval(poll);
  }, 1200);
}

function pageMedia() {
  const fps = (S.data.stage || {}).fps || 25, w = (S.data.stage || {}).width || 1920, hgt = (S.data.stage || {}).height || 1080;
  const used = new Set([...(S.data.cues || []).map((c) => c.file), ...["filler", "intro", "outro"].map((k) => (S.data[k] || {}).file)]);
  return h("div", {}, h("div", { class: "row" }, h("h1", {}, "Media"), h("span", { class: "spacer" }),
      h("button", { onclick: refreshMedia }, "Refresh"), h("button", { class: "primary", onclick: probeAll }, "Read all clips")),
    h("p", { class: "lead" }, `Folder: ${S.media.folder || "?"}${S.media.exists === false ? " (does not exist)" : ""}. "Read all clips" opens the start and end of every file (like aethra verify --quick). For a full decode of every frame use Checks → Verify media.`),
    h("div", { class: "card wide" }, h("table", {}, h("thead", {}, h("tr", {}, ["File", "Size", "Length", "Picture", "Codec", "Status", "Used"].map((x) => h("th", {}, x)))),
      h("tbody", {}, S.media.files.map((f) => {
        const p = f.probe || {};
        const issues = [];
        if (p.ok && p.fps && Math.abs(p.fps - fps) > 0.01) issues.push(`${p.fps} fps ≠ ${fps}`);
        if (p.ok && p.width && (p.width !== w || p.height !== hgt)) issues.push(`${p.width}×${p.height} ≠ stage`);
        return h("tr", {}, h("td", {}, f.name), h("td", {}, fmtSize(f.size)), h("td", {}, p.duration ? fmtTime(p.duration) : "–"),
          h("td", {}, p.width ? `${p.width}×${p.height} ${p.fps || "?"} fps` : "–"), h("td", {}, p.codec || "–"),
          h("td", {}, p.pending ? h("span", { class: "pill" }, "reading…") : !f.probe ? h("span", { class: "muted" }, "–") : p.ok ? h("span", { class: issues.length ? "pill warn" : "pill ok" }, issues.length ? issues.join(", ") : "OK") : h("span", { class: "pill bad" }, (p.problems || ["error"]).join("; "))),
          h("td", {}, used.has(f.name) ? "✓" : ""));
      })))));
}

// ---- jobs (checks, codec, windows)

function jobConsole(kind, label, optionsFn, opts = {}) {
  const out = h("div", { class: "console" + (opts.small ? "" : ""), style: opts.height ? { height: opts.height } : {} }, h("span", { class: "muted" }, "Output appears here."));
  const runBtn = h("button", { class: opts.primary === false ? "" : "primary" }, label);
  const stopBtn = h("button", { class: "danger hidden" }, "Stop");
  let current = null;
  runBtn.onclick = async () => {
    if (opts.needsSave && dirty() && !confirm("There are unsaved changes; the tool reads the file on disk. Run anyway?")) return;
    if (opts.confirm && !confirm(opts.confirm)) return;
    const options = optionsFn ? optionsFn() : {};
    if (options === null) return;
    const r = await api.post("/api/jobs", { kind, options });
    if (r.error) { toast(r.error, "bad"); return; }
    current = r.id;
    out.innerHTML = "";
    runBtn.disabled = true; stopBtn.classList.remove("hidden");
    let since = 0;
    const poll = async () => {
      const j = await api.get(`/api/jobs/${current}?since=${since}`);
      for (const line of j.lines || []) {
        const cls = /^\$ /.test(line) ? "l-cmd" : /\bFAIL|error|problem|✗|\bfailed/i.test(line) ? "l-fail" : /\bWARN|warning/i.test(line) ? "l-warn" : /\bPASS\b|\bOK\b|no failures/.test(line) ? "l-pass" : "";
        out.append(h("div", { class: cls }, line));
      }
      since = j.next || since;
      if ((j.lines || []).length) out.scrollTop = out.scrollHeight;
      if (j.running) setTimeout(poll, 400);
      else { runBtn.disabled = false; stopBtn.classList.add("hidden"); if (opts.onDone) opts.onDone(j); }
    };
    poll();
  };
  stopBtn.onclick = () => current && api.post("/api/jobs/stop", { id: current });
  return { el: h("div", {}, h("div", { class: "row", style: { marginBottom: "10px" } }, runBtn, stopBtn, opts.extra || null), out), out };
}

function pageChecks() {
  const quick = h("input", { type: "checkbox", checked: true });
  const winsel = h("input", { type: "checkbox", checked: true });
  const loops = h("input", { type: "number", value: 2, min: 1, style: { width: "70px" } });
  const gap = h("input", { type: "number", value: 5, min: 0, step: 0.5, style: { width: "70px" } });
  const maxClip = h("input", { type: "number", placeholder: "whole clip", min: 1, style: { width: "110px" } });
  const tabs = [
    { id: "check", title: "Check", desc: "Configuration, every media file, the MIDI port and the stage screen. Must end with OK before a show.", job: jobConsole("check", "Run check", null, { needsSave: true }) },
    { id: "verify", title: "Verify media", desc: "Decodes the clips to find damaged files. Quick: start and end of every clip; full: every frame (do it once after copying new media).", job: jobConsole("verify", "Verify", () => ({ quick: quick.checked }), { needsSave: true, extra: h("label", { class: "row" }, quick, "quick") }) },
    { id: "selftest", title: "Self-test", desc: "Measures this computer: IPC round trip, timer accuracy, playback rate, hotkeys; with stage windows: window stacking and the compositor (shows test pictures on the stage screen).", job: jobConsole("selftest", "Run self-test", () => ({ windows: winsel.checked }), { extra: h("label", { class: "row" }, winsel, "test on the stage screen") }) },
    { id: "rehearse", title: "Rehearsal", desc: "Plays the whole set over OSC without REAPER (test 1: the set twice without a restart). Start the show first (Live page) with OSC enabled.", job: jobConsole("rehearse", "Run rehearsal", () => ({ loops: Number(loops.value), gap: Number(gap.value), max_clip: maxClip.value ? Number(maxClip.value) : null }),
      { extra: h("span", { class: "row" }, "passes", loops, "gap (s)", gap, "max per clip (s)", maxClip) }) },
    { id: "monitor", title: "MIDI / OSC monitor", desc: "Shows incoming MIDI notes, MTC and OSC messages (no video). Stop the show first: it holds the MIDI input.", job: jobConsole("monitor", "Start monitor", null, { height: "460px" }) },
    { id: "devices", title: "Devices", desc: "MIDI inputs and screens as Aethra sees them.", job: jobConsole("devices", "List devices", null) },
  ];
  return tabbedJobs("Checks & rehearsal", "The tools from docs/testing.md. They run in the background; their output appears below.", tabs);
}

let activeTab = {};
function tabbedJobs(title, lead, tabs, after) {
  const key = title;
  const cur = activeTab[key] || tabs[0].id;
  const body = h("div", {});
  const bar = h("div", { class: "tabs" });
  const show = (id) => {
    activeTab[key] = id;
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
    body.innerHTML = "";
    const t = tabs.find((x) => x.id === id);
    body.append(h("p", { class: "muted" }, t.desc), t.job ? t.job.el : t.el);
  };
  for (const t of tabs) bar.append(h("button", { "data-id": t.id, onclick: () => show(t.id) }, t.title));
  const wrap = h("div", {}, h("h1", {}, title), h("p", { class: "lead" }, lead), h("div", { class: "card wide" }, bar, body), after || null);
  show(cur);
  return wrap;
}

function pageCodec() {
  const files = [...S.media.files.map((f) => ({ path: S.media.folder + "/" + f.name, name: f.name }))];
  const list = h("div", { style: { maxHeight: "220px", overflow: "auto" } });
  const chosen = new Set();
  const addFile = (path, name) => {
    const cb = h("input", { type: "checkbox", onchange: (e) => e.target.checked ? chosen.add(path) : chosen.delete(path) });
    list.append(h("label", { class: "row", style: { padding: "3px 0" } }, cb, name));
  };
  files.forEach((f) => addFile(f.path, f.name));
  const modes = { "auto-safe": true, no: true, "auto-copy": false, d3d11va: false, nvdec: false, dxva2: false };
  const modeBoxes = Object.entries(modes).map(([m, on]) => { const cb = h("input", { type: "checkbox", checked: on, "data-mode": m }); return h("label", { class: "row" }, cb, m); });
  const seconds = h("input", { type: "number", value: 30, min: 5, style: { width: "80px" } });
  const addOther = h("button", { onclick: () => browse((p) => addFile(p, p), [".mp4", ".mov", ".mkv", ".avi"]) }, "Add files…");
  const bench = jobConsole("bench", "Run benchmark", () => {
    if (!chosen.size) { toast("Select at least one file", "bad"); return null; }
    return { files: [...chosen], seconds: Number(seconds.value), hwdec: modeBoxes.map((l) => l.querySelector("input")).filter((x) => x.checked).map((x) => x.dataset.mode) };
  }, { needsSave: true, height: "300px" });
  const what = h("select", {}, ["codecs", "sync", "demo"].map((x) => h("option", {}, x)));
  const ffmpeg = h("input", { type: "text", placeholder: "ffmpeg on PATH" });
  const clipSecs = h("input", { type: "number", value: 60, style: { width: "80px" } });
  const make = jobConsole("make_clips", "Generate", () => ({ what: what.value, ffmpeg: ffmpeg.value || null, seconds: Number(clipSecs.value), out: what.value === "demo" ? "media" : "test-clips" }), { primary: false, height: "180px" });
  return h("div", {}, h("h1", {}, "Codec choice"),
    h("p", { class: "lead" }, "Plays each file full screen on the stage output like a real deck and counts dropped frames and CPU load, with hardware and software decoding. The right codec has zero dropped frames and the lowest CPU load (docs/codec-selection.md). Use the stage display at its show refresh rate (50 Hz for 25 fps)."),
    h("div", { class: "cards" },
      h("div", { class: "card" }, h("h2", {}, "Files"), list, h("div", { class: "row", style: { marginTop: "8px" } }, addOther)),
      h("div", { class: "card" }, h("h2", {}, "Decoders and length"), h("div", { class: "row" }, modeBoxes), h("div", { class: "row", style: { marginTop: "10px" } }, "seconds per run", seconds),
        h("p", { class: "muted" }, "Result: put the winner into Stage & players → Hardware decoding. With the compositor engine check the player logs too (docs/codec-selection.md).")),
      h("div", { class: "card wide" }, h("h2", {}, "Benchmark"), bench.el),
      h("div", { class: "card wide" }, h("h2", {}, "Test clips (needs ffmpeg)"), h("div", { class: "row" }, "kind", what, "ffmpeg", ffmpeg, "seconds", clipSecs),
        h("p", { class: "muted" }, "codecs: H.264/HEVC/HAP/MJPEG candidates; sync: the latency/sync kit; demo: clips for the example show. Written to test-clips (demo: media)."), make.el)));
}

function pageWindows() {
  const battery = h("input", { type: "checkbox", checked: true });
  const hp = h("input", { type: "checkbox" });
  const fast = h("input", { type: "checkbox" });
  const scope = h("select", {}, ["All", "User", "Machine"].map((x) => h("option", {}, x)));
  const osc = h("input", { type: "number", value: ((S.data || {}).osc || {}).port || 9000, style: { width: "90px" } });
  const opts = (mode, elevated = false) => () => ({ mode, scope: scope.value, battery: battery.checked, high_performance: hp.checked, fast_startup: fast.checked, osc_port: Number(osc.value), elevated });
  const check = jobConsole("windows", "Check settings (dry run)", opts("check"), { height: "460px" });
  const apply = jobConsole("windows", "Apply", opts("apply"), { primary: false, confirm: "Change the Windows settings listed by the check? Previous values are saved and can be reverted.", height: "320px" });
  const elevated = jobConsole("windows", "Apply as administrator…", opts("apply", true), { primary: false, confirm: "An administrator prompt opens in its own window. Continue?", height: "120px" });
  const revert = jobConsole("windows", "Revert all changes", () => ({ mode: "revert" }), { primary: false, confirm: "Restore the values saved by earlier Apply runs?", height: "200px" });
  const autostart = h("input", { type: "checkbox" });
  const delay = h("input", { type: "number", value: 20, style: { width: "70px" } });
  const shortcuts = jobConsole("shortcuts", "Create shortcuts", () => ({ autostart: autostart.checked, delay: Number(delay.value) }), { primary: false, height: "120px" });
  const removeAuto = jobConsole("shortcuts", "Remove autostart", () => ({ remove_autostart: true }), { primary: false, height: "80px" });
  if (!S.meta.windows) return h("div", {}, h("h1", {}, "Windows setup"), h("p", { class: "lead" }, "This page needs Windows."));
  return h("div", {}, h("h1", {}, "Windows setup"),
    h("p", { class: "lead" }, "The show laptop must never show a notification, dialog or desktop on the stage. This runs scripts/prepare-stage-laptop.ps1: the check lists every setting with its current and wanted value; Apply changes them (reversibly). Many more manual steps are in docs/windows-setup.md."),
    h("div", { class: "card wide" }, h("h2", {}, "Options"), h("div", { class: "row" },
      h("label", { class: "row" }, battery, "also on battery"), h("label", { class: "row" }, hp, "High performance plan"),
      h("label", { class: "row" }, fast, "turn Fast Startup off (admin)"), "scope", scope, "OSC firewall port", osc)),
    h("div", { style: { height: "16px" } }),
    h("div", { class: "cards" },
      h("div", { class: "card wide" }, h("h2", {}, "1. Check"), check.el),
      h("div", { class: "card" }, h("h2", {}, "2. Apply"), h("p", { class: "muted" }, "Per-user settings and power plan (no admin needed). Machine-wide items are skipped unless this panel runs as administrator."), apply.el),
      h("div", { class: "card" }, h("h2", {}, "Administrator items"), h("p", { class: "muted" }, "Error-report dialogs for all users, firewall prompts and the OSC rule, Fast Startup."), elevated.el,
        h("h2", { style: { marginTop: "16px" } }, "Undo"), revert.el),
      h("div", { class: "card wide" }, h("h2", {}, "Shortcuts and autostart"), h("div", { class: "row" }, h("label", { class: "row" }, autostart, "start the show after logging in"), "delay (s)", delay),
        h("div", { class: "grid2" }, shortcuts.el, removeAuto.el))));
}

async function pageLogsLoad(box, list) {
  const logs = await api.post("/api/logs", { data: S.data });
  list.innerHTML = "";
  for (const l of logs) list.append(h("div", { class: "entry row", onclick: () => showLog(box, l.name) }, h("b", {}, l.name.replace("aethra-", "").replace(".jsonl", "")), h("span", { class: "spacer" }), h("small", {}, fmtSize(l.size))));
  if (!logs.length) list.append(h("div", { class: "muted" }, "no logs yet"));
}

async function showLog(box, name) {
  box.innerHTML = "";
  const rep = await api.post("/api/report", { data: S.data, name });
  const ev = await api.post("/api/log", { data: S.data, name, limit: 2000, level: "info" });
  const events = h("div", { class: "events", style: { maxHeight: "420px" } });
  renderEvents(events, ev.events || [], false);
  box.append(h("h2", {}, name), h("h2", { style: { marginTop: "6px" } }, "Report"), h("pre", { class: "console", style: { height: "auto", maxHeight: "380px" } }, rep.text || rep.error), h("h2", { style: { marginTop: "16px" } }, "Events"), events);
}

function pageLogs() {
  const box = h("div", { class: "card" }, h("p", { class: "muted" }, "Pick a log on the left."));
  const list = h("div", { class: "browser" });
  if (S.data) pageLogsLoad(box, list);
  return h("div", {}, h("h1", {}, "Logs & reports"),
    h("p", { class: "lead" }, "One event log per session with microsecond timestamps. The report lists every cue with its trigger, start latency, dropped frames and drift, and every problem."),
    h("div", { style: { display: "grid", gridTemplateColumns: "280px 1fr", gap: "16px" } }, h("div", { class: "card" }, h("h2", {}, "Sessions"), list), box));
}

function pageYaml() {
  return h("div", {}, h("div", { class: "row" }, h("h1", {}, "Show file"), h("span", { class: "spacer" }), h("button", { onclick: openDialog }, "Open another file…"),
      h("button", { onclick: async () => loadConfig(await api.get("/api/config")) }, "Reload from disk")),
    h("p", { class: "lead" }, "The file as it is on disk. The panel writes a tidy version of it when saving (comments are not kept; the previous version is saved as .bak)."),
    h("div", { class: "card wide" }, h("pre", { class: "console", style: { height: "calc(100vh - 260px)" } }, S.text || "(empty)")));
}

// ---------------------------------------------------------------- file dialogs

async function browse(onPick, suffixes, start) {
  let path = start || S.meta.install_root;
  const listBox = h("div", { class: "browser", style: { maxHeight: "50vh", overflow: "auto" } });
  const title = h("div", { class: "mono muted", style: { margin: "6px 0" } });
  const close = modal(h("div", {}, h("h2", {}, "Choose a file"), title, listBox, h("div", { class: "row", style: { marginTop: "10px" } }, h("span", { class: "spacer" }), h("button", { onclick: () => close() }, "Cancel"))));
  const load = async (p) => {
    const r = await api.post("/api/browse", { path: p, suffixes });
    path = r.path; title.textContent = r.path;
    listBox.innerHTML = "";
    listBox.append(h("div", { class: "entry", onclick: () => load(r.parent) }, "⬆ .."));
    for (const e of r.entries) listBox.append(h("div", { class: "entry", onclick: () => e.dir ? load(e.path) : (close(), onPick(e.path)) }, (e.dir ? "📁 " : "📄 ") + e.name));
  };
  load(path);
}

function openDialog() {
  if (dirty() && !confirm("Discard unsaved changes?")) return;
  browse(async (p) => loadConfig(await api.post("/api/open", { path: p })), [".yaml", ".yml", ".json"], S.path);
}

// ---------------------------------------------------------------- status polling and start-up

async function pollStatus() {
  const st = await api.get("/api/status");
  const before = JSON.stringify(S.status);
  S.status = st;
  const side = document.getElementById("side-status");
  side.innerHTML = "";
  side.append(st.running ? h("span", { class: "badge live" }, "● ON AIR") : h("span", { class: "badge" }, "show stopped"));
  if (st.running && st.live) side.append(h("div", { style: { marginTop: "6px" } }, `${st.live.cue} ${fmtTime(st.live.position)}`));
  if (S.page === "live" && JSON.stringify(st) !== before) {
    // Keep focus and scroll: re-render only when something changed.
    render();
  } else if (JSON.stringify(st.running) !== JSON.stringify(JSON.parse(before || "{}").running)) renderNav();
}

async function init() {
  S.meta = await api.get("/api/meta");
  document.getElementById("version").textContent = "control panel · " + S.meta.version;
  document.getElementById("btn-save").onclick = () => save();
  document.getElementById("btn-revert").onclick = () => { if (confirm("Discard all unsaved changes?")) { S.data = JSON.parse(S.savedJson); changed(true); } };
  document.getElementById("btn-open").onclick = openDialog;
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });
  window.addEventListener("beforeunload", (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } });
  const hash = location.hash.slice(1);
  if (PAGES.some((p) => p.id === hash)) S.page = hash;
  api.post("/api/devices", {}).then((d) => { S.devices = d; if (["stage", "inputs"].includes(S.page)) render(); });
  await loadConfig(await api.get("/api/config"));
  pollStatus();
  setInterval(pollStatus, 1000);
  setInterval(() => { if (S.page === "live") loadLiveEvents(); }, 2000);
}

init();
