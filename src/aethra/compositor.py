"""The stage compositor: one window on the stage output, composed on the GPU.

Started by the show controller as a separate process::

    python -m aethra.compositor --control <address> --rect X,Y,W,H --size W,H

Every *layer* is a libmpv player that renders into its own texture (render
API, OpenGL). Each layer has an OpenGL context of its own, sharing textures
with the compositor's: libmpv expects to find the context as it left it, and
several players taking turns in one context spoil each other's frames (seen
as layers that stay black). Each layer runs mpv's JSON IPC server, so the controller drives
playback exactly as it drives a stand-alone mpv. The compositor is told what
is on stage and how to get from one picture to the next (a cut, a fade, a fade
through black or white, or a blend mode) over its *control* channel, which
speaks the same JSON IPC protocol as mpv; it animates transitions frame by
frame in its render loop.

The process deliberately outlives its controller: if the controller crashes,
the picture goes on, and a restarted controller reconnects to the control
channel and to the layers' IPC servers.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import queue
import struct
import sys
import threading
import time
import traceback
import zlib
from dataclasses import dataclass
from typing import Any, TextIO

from aethra import libmpv
from aethra.blend import BLACK, FRAGMENT_SHADER, MODE_ID, TRANSITION_TYPES, VERTEX_SHADER, Entry
from aethra.gl import (
    GL,
    GL_COLOR_BUFFER_BIT,
    GL_FRAMEBUFFER,
    GL_SYNC_GPU_COMMANDS_COMPLETE,
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    GL_TIMEOUT_IGNORED,
    GL_TRIANGLES,
)
from aethra.scene import Scene, Transition

PROTOCOL = 1
EXIT_ADDRESS_IN_USE = 3
EXIT_STARTUP = 4


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    raw = b"".join(b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)


class Log:
    def __init__(self, path: str | None) -> None:
        self.fh: TextIO | None = None
        if path:
            try:
                self.fh = open(path, "a", encoding="utf-8", buffering=1)
            except OSError:
                self.fh = None

    def __call__(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {text}\n"
        try:
            (self.fh or sys.stderr).write(line)
        except (OSError, ValueError, AttributeError):
            pass


@dataclass
class Layer:
    name: str
    core: libmpv.Core
    window: Any  # hidden GLFW window: holds the layer's own OpenGL context
    gl: GL  # entry points for that context
    tex: int  # shared with the compositor's context
    fbo: int  # belongs to the layer's context (framebuffers are not shared)
    dirty: bool = True
    frames: int = 0
    fence: Any = None  # GPU fence after the latest frame; waited for before the texture is read


@dataclass
class Fader:
    """The blackout overlay: a level that moves from ``a`` to ``b``."""

    a: float = 0.0
    b: float = 0.0
    start: float = 0.0
    duration: float = 0.0

    def level(self, t: float) -> float:
        if self.duration <= 0 or t >= self.start + self.duration:
            return self.b
        p = max(0.0, (t - self.start) / self.duration)
        return self.a + (self.b - self.a) * p


class ControlServer:
    """mpv-style JSON IPC (requests with ``request_id``, replies, events) over a
    named pipe (Windows) or a Unix socket; runs in its own thread."""

    def __init__(self, address: str, inbox: queue.SimpleQueue, log: Log) -> None:  # type: ignore[type-arg]
        self.address = address
        self.inbox = inbox
        self.log = log
        self.loop: asyncio.AbstractEventLoop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()  # type: ignore[attr-defined]
        self.writers: set[asyncio.StreamWriter] = set()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="control", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.ready.wait(10.0)
        if self.error is not None:
            raise self.error

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - handed to the main thread
            self.error = exc
            self.ready.set()
            return
        self.ready.set()
        self.loop.run_forever()

    async def _serve(self) -> None:
        if sys.platform == "win32":
            def factory() -> asyncio.StreamReaderProtocol:
                return asyncio.StreamReaderProtocol(asyncio.StreamReader(limit=1 << 20), self._client)

            # The first pipe instance is created with FILE_FLAG_FIRST_PIPE_INSTANCE:
            # it fails if another compositor already serves this address.
            await self.loop.start_serving_pipe(factory, self.address)  # type: ignore[attr-defined]
        else:
            if os.path.exists(self.address):
                # A stale socket file is removed; a live one means another compositor.
                try:
                    _reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.address), 1.0)
                except (ConnectionRefusedError, FileNotFoundError, asyncio.TimeoutError):
                    os.unlink(self.address)
                else:
                    writer.close()
                    raise OSError(f"{self.address} is in use")
            await asyncio.start_unix_server(self._client, path=self.address)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self.inbox.put((msg, writer))
        except (OSError, ConnectionError):
            pass
        finally:
            self.writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    def send(self, writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()

        def write() -> None:
            if writer in self.writers:
                try:
                    writer.write(data)
                except Exception:
                    self.writers.discard(writer)

        self.loop.call_soon_threadsafe(write)

    def broadcast(self, msg: dict[str, Any]) -> None:
        for writer in list(self.writers):
            self.send(writer, msg)


class Compositor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.width, self.height = args.size
        self.log = Log(args.log)
        self.inbox: queue.SimpleQueue = queue.SimpleQueue()  # type: ignore[type-arg]
        self.control = ControlServer(args.control, self.inbox, self.log)
        self.layers: dict[str, Layer] = {}
        self.scene = Scene()
        self.blackout = Fader()
        self.frames = 0
        self.fps = 0.0
        self.slow_frames = 0
        self.quit = False
        self.hwnd: int | None = None
        self.gl: GL
        self.gl_info = "?"
        self.lib: libmpv.LibMpv | None = None
        self._keys: dict[int, str] = {}
        self._current: Any = None  # the window whose context is current

    # -- setup ----------------------------------------------------------------

    def _context_hints(self) -> None:
        glfw = self.glfw
        glfw.default_window_hints()
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        glfw.window_hint(glfw.VISIBLE, False)

    def _use(self, window: Any) -> None:
        if self._current is not window:
            self.glfw.make_context_current(window)
            self._current = window

    def _window(self) -> None:
        import glfw

        self.glfw = glfw
        if not glfw.init():
            raise RuntimeError("glfw.init failed")
        self._context_hints()  # hidden: shown after the first black frame
        glfw.window_hint(glfw.DECORATED, False)
        glfw.window_hint(glfw.RESIZABLE, False)
        glfw.window_hint(glfw.FOCUSED, False)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, False)
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        glfw.window_hint(glfw.FLOATING, not self.args.no_topmost)
        x, y, w, h = self.args.rect
        self.window = glfw.create_window(w, h, self.args.title, None, None)
        if not self.window:
            raise RuntimeError("cannot create the stage window (OpenGL 3.3 is required)")
        glfw.set_window_pos(self.window, x, y)
        self._use(self.window)
        glfw.swap_interval(1)
        glfw.set_input_mode(self.window, glfw.CURSOR, glfw.CURSOR_HIDDEN)
        glfw.set_key_callback(self.window, self._on_key)
        # Nobody closes the stage window but the controller (Alt+F4 does nothing).
        glfw.set_window_close_callback(self.window, lambda win: glfw.set_window_should_close(win, False))
        if sys.platform == "win32":
            self.hwnd = glfw.get_win32_window(self.window)
            self._win32_styles()

        def proc(name: str) -> int:
            return glfw.get_proc_address(name) or 0  # for the current context

        self._proc = proc
        self.gl = GL(proc)
        self.get_proc = lambda raw: proc(raw.decode())

    def _win32_styles(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get = user32.GetWindowLongPtrW
        get.argtypes, get.restype = (wintypes.HWND, ctypes.c_int), ctypes.c_ssize_t
        set_ = user32.SetWindowLongPtrW
        set_.argtypes, set_.restype = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t), ctypes.c_ssize_t
        gwl_exstyle, ws_ex_toolwindow, ws_ex_noactivate, ws_ex_appwindow = -20, 0x80, 0x08000000, 0x40000
        # No taskbar button, no Alt+Tab entry, never takes the focus.
        ex = get(self.hwnd, gwl_exstyle)
        set_(self.hwnd, gwl_exstyle, (ex | ws_ex_toolwindow | ws_ex_noactivate) & ~ws_ex_appwindow)

    def _gl_objects(self) -> None:
        gl = self.gl
        self.program = gl.program(VERTEX_SHADER, FRAGMENT_SHADER)
        vao = ctypes.c_uint()
        gl.glGenVertexArrays(1, ctypes.byref(vao))  # type: ignore[attr-defined]
        self.vao = vao.value
        self.acc: list[tuple[int, int]] = []
        for _ in range(2):
            tex = gl.texture(self.width, self.height)
            self.acc.append((tex, gl.framebuffer(tex)))
        self.u = {
            name: gl.glGetUniformLocation(self.program, name.encode())  # type: ignore[attr-defined]
            for name in ("dst_tex", "src_tex", "mode", "opacity", "src_is_color", "src_color", "copy_only")
        }
        self.last_acc = 0

    # -- layers -----------------------------------------------------------------

    def create_layer(self, name: str, options: dict[str, str]) -> dict[str, Any]:
        old = self.layers.get(name)
        if old is not None:
            self._drop_layer(old)
        assert self.lib is not None
        opts = dict(options)
        opts.setdefault("vo", "libmpv")
        opts.setdefault("video-timing-offset", "0")
        glfw = self.glfw
        t = time.perf_counter()
        self._context_hints()
        window = glfw.create_window(16, 16, f"Aethra layer {name}", None, self.window)
        if not window:
            raise RuntimeError(f"cannot create an OpenGL context for layer {name}")
        holder: list[Layer] = []

        def updated() -> None:  # called from a libmpv thread
            if holder:
                holder[0].dirty = True

        self._use(window)
        gl: GL | None = None
        tex = fbo = 0
        try:
            gl = GL(self._proc)
            tex = gl.texture(self.width, self.height)
            fbo = gl.framebuffer(tex)
            self._clear(fbo, gl)
            core = libmpv.Core(self.lib, opts, self.get_proc, updated)
        except Exception:
            if gl is not None:
                if fbo:
                    gl.delete_framebuffer(fbo)
                if tex:
                    gl.delete_texture(tex)
            self._use(self.window)
            glfw.destroy_window(window)
            raise
        self._use(self.window)
        layer = Layer(name, core, window, gl, tex, fbo)
        holder.append(layer)
        self.layers[name] = layer
        skipped = f"; options skipped: {', '.join(core.dropped)}" if core.dropped else ""
        self.log(f"layer {name} created in {(time.perf_counter() - t) * 1000:.0f} ms{skipped}")
        return {"dropped": core.dropped}

    def _drop_layer(self, layer: Layer) -> None:
        self.layers.pop(layer.name, None)
        self._use(layer.window)
        try:
            layer.core.destroy()  # the render context goes with the layer's context current
        finally:
            if layer.fence:
                layer.gl.glDeleteSync(layer.fence)  # type: ignore[attr-defined]
            layer.gl.delete_framebuffer(layer.fbo)
            layer.gl.delete_texture(layer.tex)
            self._use(self.window)
            self.glfw.destroy_window(layer.window)
        self.log(f"layer {layer.name} removed")

    def _clear(self, fbo: int, gl: GL | None = None) -> None:
        gl = gl or self.gl
        gl.glBindFramebuffer(GL_FRAMEBUFFER, fbo)  # type: ignore[attr-defined]
        gl.glViewport(0, 0, self.width, self.height)  # type: ignore[attr-defined]
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)  # type: ignore[attr-defined]
        gl.glClear(GL_COLOR_BUFFER_BIT)  # type: ignore[attr-defined]
        gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)  # type: ignore[attr-defined]

    def _update_layers(self) -> None:
        for layer in list(self.layers.values()):
            if not layer.core.drain_events():
                # The layer's player was told to quit (or gave up): free it.
                self._drop_layer(layer)
                self.control.broadcast({"event": "layer_down", "layer": layer.name})
                continue
            if layer.dirty:
                layer.dirty = False
                # update() may run GL work for the player (frame buffers): its context.
                self._use(layer.window)
                if layer.core.update():
                    layer.core.render(layer.fbo, self.width, self.height)
                    if layer.fence:
                        layer.gl.glDeleteSync(layer.fence)  # type: ignore[attr-defined]
                    layer.fence = layer.gl.glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)  # type: ignore[attr-defined]
                    layer.gl.glFlush()  # type: ignore[attr-defined]
                    layer.frames += 1
        self._use(self.window)

    # -- composition --------------------------------------------------------------

    def _draw(self, target_fbo: int, w: int, h: int, dst_tex: int, entry: Entry | None, src_tex: int = 0) -> None:
        gl, u = self.gl, self.u
        gl.glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)  # type: ignore[attr-defined]
        gl.glViewport(0, 0, w, h)  # type: ignore[attr-defined]
        gl.glActiveTexture(GL_TEXTURE0)  # type: ignore[attr-defined]
        gl.glBindTexture(GL_TEXTURE_2D, dst_tex)  # type: ignore[attr-defined]
        gl.glActiveTexture(GL_TEXTURE0 + 1)  # type: ignore[attr-defined]
        gl.glBindTexture(GL_TEXTURE_2D, src_tex)  # type: ignore[attr-defined]
        gl.glUniform1i(u["dst_tex"], 0)  # type: ignore[attr-defined]
        gl.glUniform1i(u["src_tex"], 1)  # type: ignore[attr-defined]
        if entry is None:  # plain copy of src_tex
            gl.glUniform1i(u["copy_only"], 1)  # type: ignore[attr-defined]
            gl.glUniform1i(u["src_is_color"], 0)  # type: ignore[attr-defined]
        else:
            gl.glUniform1i(u["copy_only"], 0)  # type: ignore[attr-defined]
            gl.glUniform1i(u["mode"], MODE_ID.get(entry.mode, 0))  # type: ignore[attr-defined]
            gl.glUniform1f(u["opacity"], float(entry.opacity))  # type: ignore[attr-defined]
            gl.glUniform1i(u["src_is_color"], 1 if entry.layer is None else 0)  # type: ignore[attr-defined]
            gl.glUniform3f(u["src_color"], *entry.color)  # type: ignore[attr-defined]
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)  # type: ignore[attr-defined]

    def _composite(self, t: float) -> None:
        gl = self.gl
        gl.reset_state()
        for layer in self.layers.values():
            if layer.fence:
                # The GPU finishes the layer's frame before the compositor reads it.
                gl.glWaitSync(layer.fence, 0, GL_TIMEOUT_IGNORED)  # type: ignore[attr-defined]
                gl.glDeleteSync(layer.fence)  # type: ignore[attr-defined]
                layer.fence = None
        self.scene.collapse(t)
        entries = self.scene.entries(t)
        black = self.blackout.level(t)
        if black > 0:
            entries.append(Entry(None, "normal", black, BLACK))
        gl.glUseProgram(self.program)  # type: ignore[attr-defined]
        gl.glBindVertexArray(self.vao)  # type: ignore[attr-defined]
        cur = 0
        self._clear(self.acc[cur][1])
        for entry in entries:
            if entry.layer is not None and entry.layer not in self.layers:
                continue  # a layer that is gone shows nothing
            src_tex = self.layers[entry.layer].tex if entry.layer is not None else 0
            nxt = 1 - cur
            self._draw(self.acc[nxt][1], self.width, self.height, self.acc[cur][0], entry, src_tex)
            cur = nxt
        self.last_acc = cur
        fw, fh = self.glfw.get_framebuffer_size(self.window)
        self._draw(0, fw, fh, 0, None, self.acc[cur][0])

    # -- commands -------------------------------------------------------------------

    def _status(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "pid": os.getpid(),
            "hwnd": self.hwnd,
            "size": [self.width, self.height],
            "layers": sorted(self.layers),
            "base": self.scene.base,
            "target": self.scene.target,
            "transitions": [[tr.target, tr.kind] for tr in self.scene.transitions],
            "blackout": self.blackout.b > 0,
            "frames": self.frames,
            "fps": round(self.fps, 2),
            "slow_frames": self.slow_frames,
            "gl": self.gl_info,
            "libmpv": self.lib.version if self.lib else None,
            "layer_frames": {n: layer.frames for n, layer in self.layers.items()},
        }

    def _handle(self, name: str, args: dict[str, Any]) -> Any:
        t = time.perf_counter()
        if name in ("hello", "status"):
            return self._status()
        if name == "ping":
            return {"frames": self.frames}
        if name == "create_layer":
            options = {str(k): str(v) for k, v in (args.get("options") or {}).items()}
            return self.create_layer(str(args["layer"]), options)
        if name == "destroy_layer":
            layer = self.layers.get(str(args.get("layer")))
            if layer is not None:
                self._drop_layer(layer)
            return None
        if name == "show":
            self.scene.cut(_layer_arg(args.get("layer")))
            return None
        if name == "transition":
            kind = str(args.get("type", "fade"))
            if kind not in TRANSITION_TYPES:
                raise ValueError(f"unknown transition type {kind!r}")
            start = args.get("start")
            start = t if not isinstance(start, (int, float)) else min(float(start), t + 10.0)
            self.scene.add(Transition(_layer_arg(args.get("to")), kind, start, float(args.get("duration", 0.0))))
            return None
        if name == "blackout":
            level = self.blackout.level(t)
            self.blackout = Fader(level, 1.0 if args.get("on") else 0.0, t, float(args.get("duration", 0.0)))
            return None
        if name == "freeze":
            value = args.get("progress")
            self.scene.frozen = None if value is None else float(value)
            return None
        if name == "screenshot":
            source = self.layers.get(str(args["layer"])) if args.get("layer") else None
            if source is not None:
                self._use(source.window)
                try:
                    rgb = source.gl.read_rgb(source.fbo, self.width, self.height)
                finally:
                    self._use(self.window)
            else:
                self.gl.reset_state()
                rgb = self.gl.read_rgb(self.acc[self.last_acc][1], self.width, self.height)
            path = args.get("path")
            if path:
                write_png(str(path), self.width, self.height, rgb)
            x, y = args.get("x"), args.get("y")
            if x is not None and y is not None:
                i = (int(y) * self.width + int(x)) * 3
                return {"pixel": list(rgb[i : i + 3])}
            return None
        if name == "quit":
            self.quit = True
            return None
        raise ValueError(f"unknown command {name!r}")

    def _process_commands(self) -> None:
        while True:
            try:
                msg, writer = self.inbox.get_nowait()
            except queue.Empty:
                return
            command = msg.get("command")
            if isinstance(command, dict):
                name, args = str(command.get("name")), command
            elif isinstance(command, list) and command:
                name, args = str(command[0]), {}
            else:
                name, args = "", {}
            reply: dict[str, Any] = {"request_id": msg.get("request_id")}
            try:
                reply["data"] = self._handle(name, args)
                reply["error"] = "success"
            except Exception as exc:  # noqa: BLE001 - every error goes back to the caller
                reply["error"] = f"{type(exc).__name__}: {exc}"
                self.log(f"command {name} failed: {exc!r}")
            if msg.get("request_id") is not None:
                self.control.send(writer, reply)

    def _on_key(self, _win: object, key: int, _scancode: int, action: int, mods: int) -> None:
        glfw = self.glfw
        if action != glfw.PRESS:
            return
        if not self._keys:
            names = {
                glfw.KEY_SPACE: "space", glfw.KEY_ESCAPE: "escape", glfw.KEY_ENTER: "enter", glfw.KEY_TAB: "tab",
                glfw.KEY_BACKSPACE: "backspace", glfw.KEY_LEFT: "left", glfw.KEY_RIGHT: "right", glfw.KEY_UP: "up",
                glfw.KEY_DOWN: "down", glfw.KEY_PAGE_UP: "pageup", glfw.KEY_PAGE_DOWN: "pagedown",
                glfw.KEY_HOME: "home", glfw.KEY_END: "end", glfw.KEY_INSERT: "insert", glfw.KEY_DELETE: "delete",
            }
            names.update({getattr(glfw, f"KEY_{c}"): c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})
            names.update({getattr(glfw, f"KEY_F{n}"): f"f{n}" for n in range(1, 25)})
            names.update({getattr(glfw, f"KEY_KP_{n}"): f"numpad{n}" for n in range(10)})
            self._keys.update(names)
        name = self._keys.get(key)
        if name is None:
            return
        mod_names = ((glfw.MOD_CONTROL, "ctrl+"), (glfw.MOD_ALT, "alt+"), (glfw.MOD_SHIFT, "shift+"), (glfw.MOD_SUPER, "win+"))
        prefix = "".join(text for bit, text in mod_names if mods & bit)
        self.control.broadcast({"event": "key", "key": prefix + name})

    # -- main loop --------------------------------------------------------------------

    def run(self) -> int:
        self.log(f"compositor starting (pid {os.getpid()}, control {self.args.control}, rect {self.args.rect}, size {self.args.size})")
        if sys.platform == "win32":
            try:
                from aethra import winapi

                winapi.disable_power_throttling()
                winapi.timeBeginPeriod(1)
            except Exception:
                pass
        try:
            self.control.start()
        except OSError as exc:
            self.log(f"control address unavailable ({exc}); another compositor is running")
            return EXIT_ADDRESS_IN_USE
        try:
            self._window()
            self.gl_info = self.gl.info()
            self._gl_objects()
            self.lib = libmpv.load(self.args.libmpv)
        except Exception as exc:
            self.log(f"startup failed: {exc!r}")
            return EXIT_STARTUP
        self.log(f"OpenGL: {self.gl_info}; libmpv API {self.lib.version}")
        # One black frame before the window becomes visible: never a flash of garbage.
        self._composite(time.perf_counter())
        self.glfw.swap_buffers(self.window)
        self.glfw.show_window(self.window)
        last = time.perf_counter()
        window_start, window_frames = last, 0
        next_stats, slow_reported = last + 60.0, 0
        try:
            while not self.quit:
                self.glfw.poll_events()
                self._process_commands()
                self._update_layers()
                self._composite(time.perf_counter())
                self.glfw.swap_buffers(self.window)
                for layer in self.layers.values():
                    layer.core.report_swap()
                self.frames += 1
                window_frames += 1
                t = time.perf_counter()
                if t - last > 0.1:
                    self.slow_frames += 1
                if t - window_start >= 1.0:
                    self.fps = window_frames / (t - window_start)
                    window_start, window_frames = t, 0
                if t >= next_stats:
                    # For the analysis after the show: the display rate and any hitches.
                    frames = ", ".join(f"{n} {layer.frames}" for n, layer in sorted(self.layers.items()))
                    self.log(f"stats: {self.fps:.1f} fps, {self.slow_frames - slow_reported} frames over 100 ms in the last minute; layer frames: {frames}")
                    next_stats, slow_reported = t + 60.0, self.slow_frames
                # Without vsync (window covered, virtual display) do not spin.
                if t - last < 0.008:
                    time.sleep(0.008 - (t - last))
                last = time.perf_counter()
        except Exception:
            self.log("render loop failed:\n" + traceback.format_exc())
            raise
        finally:
            for layer in list(self.layers.values()):
                try:
                    self._drop_layer(layer)
                except Exception:
                    pass
            self.glfw.terminate()
            self.log("compositor stopped")
        return 0


def _layer_arg(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    def pair(text: str) -> tuple[int, int]:
        a, b = (int(v) for v in text.split(","))
        return a, b

    def rect(text: str) -> tuple[int, int, int, int]:
        x, y, w, h = (int(v) for v in text.split(","))
        return x, y, w, h

    parser = argparse.ArgumentParser(prog="python -m aethra.compositor", description="Aethra stage compositor")
    parser.add_argument("--control", required=True, help="control pipe or socket")
    parser.add_argument("--rect", type=rect, required=True, help="window position and size: X,Y,W,H")
    parser.add_argument("--size", type=pair, required=True, help="composition size: W,H")
    parser.add_argument("--libmpv", default=None, help="libmpv library (default: search)")
    parser.add_argument("--log", default=None, help="log file")
    parser.add_argument("--title", default="Aethra stage")
    parser.add_argument("--no-topmost", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return Compositor(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
