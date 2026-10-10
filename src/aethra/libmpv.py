"""Minimal ctypes binding to libmpv: just what the compositor needs.

Each compositor layer is one libmpv core rendering into an OpenGL framebuffer
through the render API. Playback is controlled by the show controller over the
layer's own JSON IPC server (``input-ipc-server`` works in libmpv too), so this
binding only creates, configures, renders and destroys cores.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from collections.abc import Callable
from pathlib import Path

MPV_RENDER_PARAM_INVALID = 0
MPV_RENDER_PARAM_API_TYPE = 1
MPV_RENDER_PARAM_OPENGL_INIT_PARAMS = 2
MPV_RENDER_PARAM_OPENGL_FBO = 3
MPV_RENDER_PARAM_FLIP_Y = 4
MPV_RENDER_PARAM_ADVANCED_CONTROL = 10
MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME = 12
MPV_RENDER_UPDATE_FRAME = 1 << 0

MPV_EVENT_NONE = 0
MPV_EVENT_SHUTDOWN = 1

# Options a layer cannot work without; any other option this libmpv rejects
# (an older or newer version than the mpv executable) is skipped and reported.
REQUIRED_OPTIONS = frozenset({"vo", "input-ipc-server"})


class LibMpvError(RuntimeError):
    pass


class mpv_event(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_int),
        ("error", ctypes.c_int),
        ("reply_userdata", ctypes.c_uint64),
        ("data", ctypes.c_void_p),
    ]


class mpv_render_param(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("data", ctypes.c_void_p)]


GET_PROC_ADDRESS = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
UPDATE_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class mpv_opengl_init_params(ctypes.Structure):
    # The third field (extra_exts) exists in older libmpv versions only; it is
    # always NULL, so the struct also works with libraries that do not read it.
    _fields_ = [
        ("get_proc_address", GET_PROC_ADDRESS),
        ("get_proc_address_ctx", ctypes.c_void_p),
        ("extra_exts", ctypes.c_char_p),
    ]


class mpv_opengl_fbo(ctypes.Structure):
    _fields_ = [("fbo", ctypes.c_int), ("w", ctypes.c_int), ("h", ctypes.c_int), ("internal_format", ctypes.c_int)]


def _candidates(explicit: str | None) -> list[str]:
    names: list[str] = []
    if explicit:
        names.append(explicit)
    env = os.environ.get("AETHRA_LIBMPV")
    if env:
        names.append(env)
    if sys.platform == "win32":
        names += ["libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"]
    elif sys.platform == "darwin":
        names += ["libmpv.2.dylib", "libmpv.dylib"]
    else:
        names += ["libmpv.so.2", "libmpv.so.1", "libmpv.so"]
    found = ctypes.util.find_library("mpv")
    if found:
        names.append(found)
    return names


def load(explicit: str | None = None) -> "LibMpv":
    errors = []
    for name in _candidates(explicit):
        try:
            if sys.platform == "win32" and os.path.isabs(name):
                # Let the DLL find its neighbours (mpv's own dependencies).
                os.add_dll_directory(str(Path(name).parent))
            return LibMpv(ctypes.CDLL(name))
        except (OSError, AttributeError) as exc:
            errors.append(f"{name}: {exc}")
    raise LibMpvError("libmpv not found (" + "; ".join(errors) + ")")


class LibMpv:
    def __init__(self, dll: ctypes.CDLL) -> None:
        self.dll = dll

        def proto(name: str, restype: object, *argtypes: object):  # type: ignore[no-untyped-def]
            func = getattr(dll, name)
            func.restype = restype
            func.argtypes = argtypes
            return func

        vp, cp = ctypes.c_void_p, ctypes.c_char_p
        self.client_api_version = proto("mpv_client_api_version", ctypes.c_ulong)
        self.error_string = proto("mpv_error_string", cp, ctypes.c_int)
        self.create = proto("mpv_create", vp)
        self.initialize = proto("mpv_initialize", ctypes.c_int, vp)
        self.terminate_destroy = proto("mpv_terminate_destroy", None, vp)
        self.set_option_string = proto("mpv_set_option_string", ctypes.c_int, vp, cp, cp)
        self.command_string = proto("mpv_command_string", ctypes.c_int, vp, cp)
        self.wait_event = proto("mpv_wait_event", ctypes.POINTER(mpv_event), vp, ctypes.c_double)
        self.render_context_create = proto("mpv_render_context_create", ctypes.c_int, ctypes.POINTER(vp), vp, ctypes.POINTER(mpv_render_param))
        self.render_context_set_update_callback = proto("mpv_render_context_set_update_callback", None, vp, UPDATE_CALLBACK, vp)
        self.render_context_update = proto("mpv_render_context_update", ctypes.c_uint64, vp)
        self.render_context_render = proto("mpv_render_context_render", ctypes.c_int, vp, ctypes.POINTER(mpv_render_param))
        self.render_context_report_swap = proto("mpv_render_context_report_swap", None, vp)
        self.render_context_free = proto("mpv_render_context_free", None, vp)

    @property
    def version(self) -> str:
        v = self.client_api_version()
        return f"{v >> 16}.{v & 0xFFFF}"

    def check(self, code: int, what: str) -> None:
        if code < 0:
            raise LibMpvError(f"{what}: {self.error_string(code).decode(errors='replace')}")


def _params(items: list[tuple[int, object]]) -> ctypes.Array:  # type: ignore[type-arg]
    arr = (mpv_render_param * (len(items) + 1))()
    for i, (kind, data) in enumerate(items):
        arr[i].type = kind
        arr[i].data = ctypes.cast(data, ctypes.c_void_p) if data is not None else None
    arr[len(items)].type = MPV_RENDER_PARAM_INVALID
    arr[len(items)].data = None
    return arr


class Core:
    """One libmpv core with an OpenGL render context (one compositor layer)."""

    def __init__(
        self,
        lib: LibMpv,
        options: dict[str, str],
        get_proc_address: Callable[[bytes], int],
        on_update: Callable[[], None],
    ) -> None:
        self.lib = lib
        self.handle = lib.create()
        if not self.handle:
            raise LibMpvError("mpv_create failed")
        self.render_ctx = ctypes.c_void_p()
        self.alive = True
        self.dropped: list[str] = []
        try:
            for name, value in options.items():
                code = lib.set_option_string(self.handle, name.encode(), str(value).encode())
                if code < 0 and name not in REQUIRED_OPTIONS:
                    self.dropped.append(f"{name} ({lib.error_string(code).decode(errors='replace')})")
                    continue
                lib.check(code, f"option {name}")
            lib.check(lib.initialize(self.handle), "mpv_initialize")
            # Keep the callbacks referenced: libmpv calls them from its threads.
            self._get_proc = GET_PROC_ADDRESS(lambda _ctx, name: get_proc_address(name) or 0)
            self._on_update = UPDATE_CALLBACK(lambda _ctx: on_update())
            init = mpv_opengl_init_params(self._get_proc, None, None)
            api = ctypes.c_char_p(b"opengl")
            advanced = ctypes.c_int(1)
            self._init_keepalive = (init, api, advanced)
            params = _params([
                (MPV_RENDER_PARAM_API_TYPE, api),
                (MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, ctypes.pointer(init)),
                (MPV_RENDER_PARAM_ADVANCED_CONTROL, ctypes.pointer(advanced)),
            ])
            lib.check(lib.render_context_create(ctypes.byref(self.render_ctx), self.handle, params), "render context")
            lib.render_context_set_update_callback(self.render_ctx, self._on_update, None)
        except Exception:
            self.destroy()
            raise
        self._fbo = mpv_opengl_fbo(0, 0, 0, 0)
        # Rendered upside down: the texture then has OpenGL's orientation
        # (first row at the bottom), like everything else the compositor draws.
        self._flip = ctypes.c_int(1)
        self._block = ctypes.c_int(0)
        self._render_params = _params([
            (MPV_RENDER_PARAM_OPENGL_FBO, ctypes.pointer(self._fbo)),
            (MPV_RENDER_PARAM_FLIP_Y, ctypes.pointer(self._flip)),
            (MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, ctypes.pointer(self._block)),
        ])

    def update(self) -> bool:
        """True if a new frame must be rendered (call after every update callback)."""
        return bool(self.lib.render_context_update(self.render_ctx) & MPV_RENDER_UPDATE_FRAME)

    def render(self, fbo: int, w: int, h: int) -> None:
        self._fbo.fbo, self._fbo.w, self._fbo.h = fbo, w, h
        self.lib.render_context_render(self.render_ctx, self._render_params)

    def report_swap(self) -> None:
        self.lib.render_context_report_swap(self.render_ctx)

    def drain_events(self) -> bool:
        """Empty the core's event queue; False once the core has shut down."""
        while True:
            event = self.lib.wait_event(self.handle, 0.0).contents
            if event.event_id == MPV_EVENT_NONE:
                return True
            if event.event_id == MPV_EVENT_SHUTDOWN:
                self.alive = False
                return False

    def destroy(self) -> None:
        # The render context must go first (on the render thread, GL current),
        # then the core; the other order can deadlock or crash.
        if self.render_ctx:
            self.lib.render_context_free(self.render_ctx)
            self.render_ctx = ctypes.c_void_p()
        if self.handle:
            self.lib.terminate_destroy(self.handle)
            self.handle = None
        self.alive = False
