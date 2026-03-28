"""
engine/frame_server.py — Generic live frame server.

A lightweight HTTP server that serves the latest frame from any registered
capture source.  Not specific to any mod — any code that produces images
can register a capture function.

Endpoints:
    /frame   — latest PNG snapshot
    /stream  — MJPEG stream
    /        — status page with embedded stream

Usage:
    from engine.frame_server import register_source, unregister_source

    # Any mod can register its capture function
    register_source(my_capture_fn)     # auto-starts server on first registration
    unregister_source()                # auto-stops server when no sources remain

The viewer (viewer.py) or any browser just hits http://localhost:9222
"""

from __future__ import annotations

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable

from config import FRAME_SERVER_PORT


_server: HTTPServer | None = None
_thread: threading.Thread | None = None
_capture_fn: Callable[[], bytes | None] | None = None
_lock = threading.Lock()

# ── Server ────────────────────────────────────────────────────────────────────

class _ReusableServer(HTTPServer):
    allow_reuse_address = True
    allow_reuse_port = True


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/frame":
            self._serve_frame()
        elif self.path == "/stream":
            self._serve_mjpeg()
        elif self.path == "/":
            self._serve_status()
        else:
            self.send_error(404)

    def _serve_frame(self):
        img = _capture_fn() if _capture_fn else None
        if not img:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"No capture source registered")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(img)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(img)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            while _capture_fn is not None:
                img = _capture_fn()
                if img:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/png\r\n")
                    self.wfile.write(f"Content-Length: {len(img)}\r\n".encode())
                    self.wfile.write(b"\r\n")
                    self.wfile.write(img)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(0.066)  # ~15 FPS server-side
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _serve_status(self):
        html = (
            "<!DOCTYPE html><html><head><title>Agent Live View</title></head>"
            "<body style='margin:0;background:#111;display:flex;justify-content:center;"
            "align-items:center;height:100vh'>"
            "<img src='/stream' style='max-width:100%;max-height:100%'>"
            "</body></html>"
        )
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def register_source(capture_fn: Callable[[], bytes | None]) -> None:
    """
    Register a frame capture function and auto-start the server.

    The capture function should return PNG bytes or None.
    Only one source at a time — calling again replaces the previous one.
    """
    global _capture_fn
    _capture_fn = capture_fn
    _ensure_server()


def unregister_source() -> None:
    """Unregister the capture function and stop the server."""
    global _capture_fn
    _capture_fn = None
    _stop_server()


def is_serving() -> bool:
    """Check if the frame server is running."""
    return _server is not None


# ── Internals ─────────────────────────────────────────────────────────────────

def _ensure_server() -> None:
    global _server, _thread

    with _lock:
        if _server is not None:
            return
        try:
            _server = _ReusableServer(("0.0.0.0", FRAME_SERVER_PORT), _Handler)
            _server.timeout = 1
            _thread = threading.Thread(target=_server.serve_forever, daemon=True)
            _thread.start()
            print(f"[frame_server] Live view at http://localhost:{FRAME_SERVER_PORT}", flush=True)
        except OSError as e:
            print(f"[frame_server] Could not start: {e}", flush=True)
            _server = None


def _stop_server() -> None:
    global _server, _thread

    with _lock:
        if _server is None:
            return
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None
        _thread = None