"""The few OpenGL functions the compositor uses, loaded through GLFW.

No PyOpenGL: one small table of prototypes resolved with
``glfw.get_proc_address`` once a context is current (which also covers the
OpenGL 1.1 functions that wglGetProcAddress does not return on Windows).
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

FUNCTYPE = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE

GL_FALSE = 0
GL_TRUE = 1
GL_TRIANGLES = 0x0004
GL_TEXTURE_2D = 0x0DE1
GL_UNSIGNED_BYTE = 0x1401
GL_RGB = 0x1907
GL_RGBA = 0x1908
GL_RGBA8 = 0x8058
GL_NEAREST = 0x2600
GL_LINEAR = 0x2601
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_CLAMP_TO_EDGE = 0x812F
GL_TEXTURE0 = 0x84C0
GL_COLOR_BUFFER_BIT = 0x4000
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_FRAMEBUFFER_COMPLETE = 0x8CD5
GL_FRAGMENT_SHADER = 0x8B30
GL_VERTEX_SHADER = 0x8B31
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_INFO_LOG_LENGTH = 0x8B84
GL_PACK_ALIGNMENT = 0x0D05
GL_UNPACK_ALIGNMENT = 0x0CF5
GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_BLEND = 0x0BE2
GL_SCISSOR_TEST = 0x0C11
GL_DEPTH_TEST = 0x0B71
GL_STENCIL_TEST = 0x0B90
GL_CULL_FACE = 0x0B44
GL_DITHER = 0x0BD0
GL_FRAMEBUFFER_SRGB = 0x8DB9
GL_PIXEL_PACK_BUFFER = 0x88EB
GL_PIXEL_UNPACK_BUFFER = 0x88EC
GL_ARRAY_BUFFER = 0x8892
GL_SYNC_GPU_COMMANDS_COMPLETE = 0x9117
GL_TIMEOUT_IGNORED = 0xFFFFFFFFFFFFFFFF

u, i, f, p = ctypes.c_uint, ctypes.c_int, ctypes.c_float, ctypes.c_void_p
_PROTOTYPES: dict[str, tuple[object, tuple[object, ...]]] = {
    "glGetString": (ctypes.c_char_p, (u,)),
    "glGetError": (u, ()),
    "glViewport": (None, (i, i, i, i)),
    "glClearColor": (None, (f, f, f, f)),
    "glClear": (None, (u,)),
    "glDisable": (None, (u,)),
    "glBindBuffer": (None, (u, u)),
    "glColorMask": (None, (ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte)),
    "glGenTextures": (None, (i, ctypes.POINTER(u))),
    "glDeleteTextures": (None, (i, ctypes.POINTER(u))),
    "glBindTexture": (None, (u, u)),
    "glTexImage2D": (None, (u, i, i, i, i, i, u, u, p)),
    "glTexParameteri": (None, (u, u, i)),
    "glActiveTexture": (None, (u,)),
    "glGenFramebuffers": (None, (i, ctypes.POINTER(u))),
    "glDeleteFramebuffers": (None, (i, ctypes.POINTER(u))),
    "glBindFramebuffer": (None, (u, u)),
    "glFramebufferTexture2D": (None, (u, u, u, u, i)),
    "glCheckFramebufferStatus": (u, (u,)),
    "glCreateShader": (u, (u,)),
    "glShaderSource": (None, (u, i, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(i))),
    "glCompileShader": (None, (u,)),
    "glGetShaderiv": (None, (u, u, ctypes.POINTER(i))),
    "glGetShaderInfoLog": (None, (u, i, ctypes.POINTER(i), ctypes.c_char_p)),
    "glDeleteShader": (None, (u,)),
    "glCreateProgram": (u, ()),
    "glAttachShader": (None, (u, u)),
    "glLinkProgram": (None, (u,)),
    "glGetProgramiv": (None, (u, u, ctypes.POINTER(i))),
    "glGetProgramInfoLog": (None, (u, i, ctypes.POINTER(i), ctypes.c_char_p)),
    "glUseProgram": (None, (u,)),
    "glGetUniformLocation": (i, (u, ctypes.c_char_p)),
    "glUniform1i": (None, (i, i)),
    "glUniform1f": (None, (i, f)),
    "glUniform3f": (None, (i, f, f, f)),
    "glGenVertexArrays": (None, (i, ctypes.POINTER(u))),
    "glBindVertexArray": (None, (u,)),
    "glDrawArrays": (None, (u, i, i)),
    "glReadPixels": (None, (i, i, i, i, u, u, p)),
    "glPixelStorei": (None, (u, i)),
    "glFinish": (None, ()),
    "glFlush": (None, ()),
    "glFenceSync": (p, (u, u)),
    "glWaitSync": (None, (p, u, ctypes.c_uint64)),
    "glDeleteSync": (None, (p,)),
}


class GLError(RuntimeError):
    pass


class GL:
    """OpenGL entry points as attributes: ``gl.glClear(...)``."""

    def __init__(self, get_proc_address: Callable[[str], int]) -> None:
        for name, (restype, argtypes) in _PROTOTYPES.items():
            address = get_proc_address(name)
            if not address:
                raise GLError(f"OpenGL function {name} is not available")
            setattr(self, name, FUNCTYPE(restype, *argtypes)(address))

    def info(self) -> str:
        def s(which: int) -> str:
            raw = self.glGetString(which)  # type: ignore[attr-defined]
            return raw.decode(errors="replace") if raw else "?"

        return f"{s(GL_RENDERER)} / {s(GL_VERSION)} / {s(GL_VENDOR)}"

    # -- helpers -------------------------------------------------------------

    def reset_state(self) -> None:
        """Back to OpenGL's standard defaults.

        libmpv expects them before every render call (and does not promise to
        leave them perfectly behind), and several players share this context:
        anything one of them, or the compositor, leaves bound can spoil the next
        one's frame.
        """
        self.glUseProgram(0)  # type: ignore[attr-defined]
        self.glBindVertexArray(0)  # type: ignore[attr-defined]
        for unit in range(4):
            self.glActiveTexture(GL_TEXTURE0 + unit)  # type: ignore[attr-defined]
            self.glBindTexture(GL_TEXTURE_2D, 0)  # type: ignore[attr-defined]
        self.glActiveTexture(GL_TEXTURE0)  # type: ignore[attr-defined]
        self.glBindFramebuffer(GL_FRAMEBUFFER, 0)  # type: ignore[attr-defined]
        for target in (GL_PIXEL_PACK_BUFFER, GL_PIXEL_UNPACK_BUFFER, GL_ARRAY_BUFFER):
            self.glBindBuffer(target, 0)  # type: ignore[attr-defined]
        for cap in (GL_BLEND, GL_SCISSOR_TEST, GL_DEPTH_TEST, GL_STENCIL_TEST, GL_CULL_FACE, GL_FRAMEBUFFER_SRGB):
            self.glDisable(cap)  # type: ignore[attr-defined]
        self.glColorMask(1, 1, 1, 1)  # type: ignore[attr-defined]
        self.glPixelStorei(GL_PACK_ALIGNMENT, 4)  # type: ignore[attr-defined]
        self.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)  # type: ignore[attr-defined]

    def texture(self, w: int, h: int) -> int:
        tex = ctypes.c_uint()
        self.glGenTextures(1, ctypes.byref(tex))  # type: ignore[attr-defined]
        self.glBindTexture(GL_TEXTURE_2D, tex.value)  # type: ignore[attr-defined]
        self.glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)  # type: ignore[attr-defined]
        for pname, value in (
            (GL_TEXTURE_MIN_FILTER, GL_LINEAR), (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
            (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE), (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE),
        ):
            self.glTexParameteri(GL_TEXTURE_2D, pname, value)  # type: ignore[attr-defined]
        self.glBindTexture(GL_TEXTURE_2D, 0)  # type: ignore[attr-defined]
        return tex.value

    def framebuffer(self, texture: int) -> int:
        fbo = ctypes.c_uint()
        self.glGenFramebuffers(1, ctypes.byref(fbo))  # type: ignore[attr-defined]
        self.glBindFramebuffer(GL_FRAMEBUFFER, fbo.value)  # type: ignore[attr-defined]
        self.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0)  # type: ignore[attr-defined]
        status = self.glCheckFramebufferStatus(GL_FRAMEBUFFER)  # type: ignore[attr-defined]
        self.glBindFramebuffer(GL_FRAMEBUFFER, 0)  # type: ignore[attr-defined]
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise GLError(f"framebuffer incomplete (0x{status:x})")
        return fbo.value

    def delete_texture(self, tex: int) -> None:
        t = ctypes.c_uint(tex)
        self.glDeleteTextures(1, ctypes.byref(t))  # type: ignore[attr-defined]

    def delete_framebuffer(self, fbo: int) -> None:
        b = ctypes.c_uint(fbo)
        self.glDeleteFramebuffers(1, ctypes.byref(b))  # type: ignore[attr-defined]

    def _shader(self, kind: int, source: str) -> int:
        shader = self.glCreateShader(kind)  # type: ignore[attr-defined]
        src = ctypes.c_char_p(source.encode())
        self.glShaderSource(shader, 1, ctypes.byref(src), None)  # type: ignore[attr-defined]
        self.glCompileShader(shader)  # type: ignore[attr-defined]
        ok = ctypes.c_int()
        self.glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(ok))  # type: ignore[attr-defined]
        if not ok.value:
            log = ctypes.create_string_buffer(8192)
            self.glGetShaderInfoLog(shader, 8192, None, log)  # type: ignore[attr-defined]
            raise GLError("shader compilation failed: " + log.value.decode(errors="replace"))
        return shader

    def program(self, vertex: str, fragment: str) -> int:
        vs = self._shader(GL_VERTEX_SHADER, vertex)
        fs = self._shader(GL_FRAGMENT_SHADER, fragment)
        prog = self.glCreateProgram()  # type: ignore[attr-defined]
        self.glAttachShader(prog, vs)  # type: ignore[attr-defined]
        self.glAttachShader(prog, fs)  # type: ignore[attr-defined]
        self.glLinkProgram(prog)  # type: ignore[attr-defined]
        ok = ctypes.c_int()
        self.glGetProgramiv(prog, GL_LINK_STATUS, ctypes.byref(ok))  # type: ignore[attr-defined]
        if not ok.value:
            log = ctypes.create_string_buffer(8192)
            self.glGetProgramInfoLog(prog, 8192, None, log)  # type: ignore[attr-defined]
            raise GLError("shader link failed: " + log.value.decode(errors="replace"))
        self.glDeleteShader(vs)  # type: ignore[attr-defined]
        self.glDeleteShader(fs)  # type: ignore[attr-defined]
        return prog

    def read_rgb(self, fbo: int, w: int, h: int) -> bytes:
        """Pixels of a framebuffer, top row first (for screenshots and tests)."""
        buf = ctypes.create_string_buffer(w * h * 3)
        self.glBindFramebuffer(GL_FRAMEBUFFER, fbo)  # type: ignore[attr-defined]
        self.glPixelStorei(GL_PACK_ALIGNMENT, 1)  # type: ignore[attr-defined]
        self.glReadPixels(0, 0, w, h, GL_RGB, GL_UNSIGNED_BYTE, buf)  # type: ignore[attr-defined]
        self.glBindFramebuffer(GL_FRAMEBUFFER, 0)  # type: ignore[attr-defined]
        rows = [buf.raw[y * w * 3 : (y + 1) * w * 3] for y in range(h)]
        return b"".join(reversed(rows))
