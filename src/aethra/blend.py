"""Blend modes and transitions of the compositor.

A transition from what is on stage (X) to a layer (Y) is described, for each
progress value ``p`` in 0..1, as a short list of *entries* drawn over X:

* ``fade``       Y over X with opacity p (a classic cross-fade);
* ``fadeblack``  X fades to black, then Y fades in from black;
* ``fadewhite``  the same through white;
* a blend mode   (``multiply``, ``screen``, ...): during the first half Y is
                 blended onto X with that mode, rising to full strength; during
                 the second half the blend gives way to Y alone. Halfway through
                 the picture is exactly ``mode(X, Y)``.

The formulas follow the W3C Compositing and Blending specification (the same
model as the familiar image-editor blend modes). :func:`blend_rgb` is a Python
reference used by the tests; :data:`FRAGMENT_SHADER` is what the GPU runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BLEND_MODES = (
    "normal", "multiply", "screen", "overlay", "softlight", "hardlight", "darken", "lighten",
    "colordodge", "colorburn", "lineardodge", "linearburn", "difference", "exclusion", "subtract",
    "divide", "vividlight", "linearlight", "pinlight", "hardmix", "hue", "saturation", "color",
    "luminosity", "darkercolor", "lightercolor",
)
MODE_ID = {name: i for i, name in enumerate(BLEND_MODES)}
BASIC_TRANSITIONS = ("cut", "fade", "fadeblack", "fadewhite")
TRANSITION_TYPES = BASIC_TRANSITIONS + tuple(m for m in BLEND_MODES if m != "normal")
BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)


@dataclass(frozen=True)
class Entry:
    layer: str | None  # None: a solid colour
    mode: str
    opacity: float
    color: tuple[float, float, float] = BLACK


def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def transition_entries(kind: str, target: str | None, p: float) -> list[Entry]:
    """Entries drawn over the current picture at progress ``p`` (already eased)."""
    p = min(1.0, max(0.0, p))
    if target is None:  # to black
        return [Entry(None, "normal", p, BLACK)]
    if kind == "cut" or p >= 1.0:
        return [Entry(target, "normal", 1.0)]
    if kind == "fade":
        return [Entry(target, "normal", p)]
    if kind in ("fadeblack", "fadewhite"):
        tint = BLACK if kind == "fadeblack" else WHITE
        if p < 0.5:
            return [Entry(None, "normal", 2 * p, tint)]
        return [Entry(target, "normal", 1.0), Entry(None, "normal", 2 - 2 * p, tint)]
    if kind in MODE_ID:
        if p < 0.5:
            return [Entry(target, kind, 2 * p)]
        return [Entry(target, kind, 1.0), Entry(target, "normal", 2 * p - 1)]
    raise ValueError(f"unknown transition {kind!r}")


# --------------------------------------------------------------------------
# Python reference (per channel, 0..1)


def _lum(c: tuple[float, float, float]) -> float:
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def _clip_color(c: tuple[float, float, float]) -> tuple[float, float, float]:
    lum = _lum(c)
    lo, hi = min(c), max(c)
    if lo < 0:
        c = tuple(lum + (x - lum) * lum / (lum - lo) for x in c)  # type: ignore[assignment]
    if hi > 1:
        c = tuple(lum + (x - lum) * (1 - lum) / (hi - lum) for x in c)  # type: ignore[assignment]
    return c


def _set_lum(c: tuple[float, float, float], lum: float) -> tuple[float, float, float]:
    d = lum - _lum(c)
    return _clip_color(tuple(x + d for x in c))  # type: ignore[arg-type]


def _sat(c: tuple[float, float, float]) -> float:
    return max(c) - min(c)


def _set_sat(c: tuple[float, float, float], s: float) -> tuple[float, float, float]:
    hi, lo = max(c), min(c)
    if hi <= lo:
        return (0.0, 0.0, 0.0)
    return tuple((x - lo) * s / (hi - lo) for x in c)  # type: ignore[return-value]


def _dodge(b: float, s: float) -> float:
    if b <= 0:
        return 0.0
    if s >= 1:
        return 1.0
    return min(1.0, b / (1 - s))


def _burn(b: float, s: float) -> float:
    if b >= 1:
        return 1.0
    if s <= 0:
        return 0.0
    return 1 - min(1.0, (1 - b) / s)


def _channel(mode: str, b: float, s: float) -> float:
    if mode == "normal":
        return s
    if mode == "multiply":
        return b * s
    if mode == "screen":
        return b + s - b * s
    if mode == "overlay":
        return _channel("hardlight", s, b)
    if mode == "softlight":
        if s <= 0.5:
            return b - (1 - 2 * s) * b * (1 - b)
        d = ((16 * b - 12) * b + 4) * b if b <= 0.25 else math.sqrt(b)
        return b + (2 * s - 1) * (d - b)
    if mode == "hardlight":
        return b * 2 * s if s <= 0.5 else _channel("screen", b, 2 * s - 1)
    if mode == "darken":
        return min(b, s)
    if mode == "lighten":
        return max(b, s)
    if mode == "colordodge":
        return _dodge(b, s)
    if mode == "colorburn":
        return _burn(b, s)
    if mode == "lineardodge":
        return min(1.0, b + s)
    if mode == "linearburn":
        return max(0.0, b + s - 1)
    if mode == "difference":
        return abs(b - s)
    if mode == "exclusion":
        return b + s - 2 * b * s
    if mode == "subtract":
        return max(0.0, b - s)
    if mode == "divide":
        return 1.0 if s <= 0 else min(1.0, b / s)
    if mode == "vividlight":
        return _burn(b, 2 * s) if s <= 0.5 else _dodge(b, 2 * s - 1)
    if mode == "linearlight":
        return min(1.0, max(0.0, b + 2 * s - 1))
    if mode == "pinlight":
        return min(b, 2 * s) if s <= 0.5 else max(b, 2 * s - 1)
    if mode == "hardmix":
        return 0.0 if _channel("vividlight", b, s) < 0.5 else 1.0
    raise ValueError(mode)


def blend_rgb(mode: str, b: tuple[float, float, float], s: tuple[float, float, float]) -> tuple[float, float, float]:
    if mode == "hue":
        return _set_lum(_set_sat(s, _sat(b)), _lum(b))
    if mode == "saturation":
        return _set_lum(_set_sat(b, _sat(s)), _lum(b))
    if mode == "color":
        return _set_lum(s, _lum(b))
    if mode == "luminosity":
        return _set_lum(b, _lum(s))
    if mode == "darkercolor":
        return s if _lum(s) < _lum(b) else b
    if mode == "lightercolor":
        return s if _lum(s) > _lum(b) else b
    return tuple(_channel(mode, x, y) for x, y in zip(b, s))  # type: ignore[return-value]


def composite(base: tuple[float, float, float], entries: list[Entry], layers: dict[str, tuple[float, float, float]]) -> tuple[float, float, float]:
    """What the GPU computes for one pixel (reference for tests)."""
    acc = base
    for e in entries:
        src = e.color if e.layer is None else layers[e.layer]
        r = blend_rgb(e.mode, acc, src)
        acc = tuple(a + (x - a) * e.opacity for a, x in zip(acc, r))  # type: ignore[assignment]
    return acc


# --------------------------------------------------------------------------
# GPU

VERTEX_SHADER = """
#version 330 core
out vec2 uv;
void main() {
    // One triangle that covers the screen; no vertex buffers needed.
    vec2 pos = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    uv = pos;
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
out vec4 frag;
uniform sampler2D dst_tex;
uniform sampler2D src_tex;
uniform int mode;
uniform float opacity;
uniform int src_is_color;
uniform vec3 src_color;
uniform int copy_only;

float lum(vec3 c) { return dot(c, vec3(0.3, 0.59, 0.11)); }
vec3 clip_color(vec3 c) {
    float l = lum(c); float lo = min(c.r, min(c.g, c.b)); float hi = max(c.r, max(c.g, c.b));
    if (lo < 0.0) c = l + (c - l) * l / (l - lo);
    if (hi > 1.0) c = l + (c - l) * (1.0 - l) / (hi - l);
    return c;
}
vec3 set_lum(vec3 c, float l) { return clip_color(c + (l - lum(c))); }
float sat(vec3 c) { return max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b)); }
vec3 set_sat(vec3 c, float s) {
    float hi = max(c.r, max(c.g, c.b)); float lo = min(c.r, min(c.g, c.b));
    if (hi <= lo) return vec3(0.0);
    return (c - lo) * s / (hi - lo);
}
float dodge(float b, float s) { return b <= 0.0 ? 0.0 : (s >= 1.0 ? 1.0 : min(1.0, b / (1.0 - s))); }
float burn(float b, float s) { return b >= 1.0 ? 1.0 : (s <= 0.0 ? 0.0 : 1.0 - min(1.0, (1.0 - b) / s)); }
float softlight(float b, float s) {
    if (s <= 0.5) return b - (1.0 - 2.0 * s) * b * (1.0 - b);
    float d = b <= 0.25 ? ((16.0 * b - 12.0) * b + 4.0) * b : sqrt(b);
    return b + (2.0 * s - 1.0) * (d - b);
}
float hardlight(float b, float s) { return s <= 0.5 ? b * 2.0 * s : b + (2.0 * s - 1.0) - b * (2.0 * s - 1.0); }
float vivid(float b, float s) { return s <= 0.5 ? burn(b, 2.0 * s) : dodge(b, 2.0 * s - 1.0); }

float channel(int m, float b, float s) {
    if (m == 1) return b * s;
    if (m == 2) return b + s - b * s;
    if (m == 3) return hardlight(s, b);
    if (m == 4) return softlight(b, s);
    if (m == 5) return hardlight(b, s);
    if (m == 6) return min(b, s);
    if (m == 7) return max(b, s);
    if (m == 8) return dodge(b, s);
    if (m == 9) return burn(b, s);
    if (m == 10) return min(1.0, b + s);
    if (m == 11) return max(0.0, b + s - 1.0);
    if (m == 12) return abs(b - s);
    if (m == 13) return b + s - 2.0 * b * s;
    if (m == 14) return max(0.0, b - s);
    if (m == 15) return s <= 0.0 ? 1.0 : min(1.0, b / s);
    if (m == 16) return vivid(b, s);
    if (m == 17) return clamp(b + 2.0 * s - 1.0, 0.0, 1.0);
    if (m == 18) return s <= 0.5 ? min(b, 2.0 * s) : max(b, 2.0 * s - 1.0);
    if (m == 19) return vivid(b, s) < 0.5 ? 0.0 : 1.0;
    return s;
}

vec3 blend(int m, vec3 b, vec3 s) {
    if (m == 20) return set_lum(set_sat(s, sat(b)), lum(b));
    if (m == 21) return set_lum(set_sat(b, sat(s)), lum(b));
    if (m == 22) return set_lum(s, lum(b));
    if (m == 23) return set_lum(b, lum(s));
    if (m == 24) return lum(s) < lum(b) ? s : b;
    if (m == 25) return lum(s) > lum(b) ? s : b;
    if (m == 0) return s;
    return vec3(channel(m, b.r, s.r), channel(m, b.g, s.g), channel(m, b.b, s.b));
}

void main() {
    vec3 s = src_is_color == 1 ? src_color : texture(src_tex, uv).rgb;
    if (copy_only == 1) { frag = vec4(s, 1.0); return; }
    vec3 b = texture(dst_tex, uv).rgb;
    frag = vec4(mix(b, clamp(blend(mode, b, s), 0.0, 1.0), opacity), 1.0);
}
"""
