#!/usr/bin/env python3
"""
mods/debug_ui/viewer.py — Live view into the agent's virtual display.

Shows a window that continuously polls the frame server for screenshots.
Watch the AI interact with GUI applications in real time.

Usage:
    python mods/debug_ui/viewer.py
    python mods/debug_ui/viewer.py --fps 30
    python mods/debug_ui/viewer.py --fps 5

Or just open http://localhost:9222 in any browser.

Requirements:
    pip install pillow
"""

import argparse
import io
import sys
import time
import tkinter as tk
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Pillow is required: pip install pillow")
    sys.exit(1)


class Viewer:
    def __init__(self, host: str = "localhost", port: int = 9222, fps: int = 15):
        self._url = f"http://{host}:{port}/frame"
        self._interval = max(16, 1000 // fps)
        self._running = True

        self._root = tk.Tk()
        self._root.title(f"Agent Live View — {host}:{port}")
        self._root.configure(bg="#111")
        self._root.geometry("1300x850")
        self._root.protocol("WM_DELETE_WINDOW", self._quit)

        self._status_var = tk.StringVar(value="  Waiting for frame server...")
        self._status = tk.Label(
            self._root,
            textvariable=self._status_var,
            bg="#1a1a1a", fg="#888",
            font=("monospace", 9),
            anchor="w",
            padx=8, pady=3,
        )
        self._status.pack(side="bottom", fill="x")

        self._canvas = tk.Label(self._root, bg="#111")
        self._canvas.pack(expand=True, fill="both")

        self._photo = None
        self._frame_count = 0
        self._last_time = time.time()
        self._actual_fps = 0.0

        self._root.after(100, self._poll)

    def run(self):
        self._root.mainloop()

    def _poll(self):
        if not self._running:
            return

        try:
            req = Request(self._url)
            with urlopen(req, timeout=2) as resp:
                data = resp.read()

            img = Image.open(io.BytesIO(data))

            win_w = max(self._root.winfo_width() - 4, 640)
            win_h = max(self._root.winfo_height() - 30, 400)
            img.thumbnail((win_w, win_h), Image.LANCZOS)

            self._photo = ImageTk.PhotoImage(img)
            self._canvas.configure(image=self._photo)

            # FPS counter
            self._frame_count += 1
            now = time.time()
            elapsed = now - self._last_time
            if elapsed >= 1.0:
                self._actual_fps = self._frame_count / elapsed
                self._frame_count = 0
                self._last_time = now

            self._status_var.set(
                f"  Connected  |  {img.size[0]}×{img.size[1]}  |  {self._actual_fps:.1f} FPS"
            )

        except (URLError, OSError):
            self._status_var.set(
                "  Waiting for frame server...  "
                "(tell the AI to use debug_ui, or open http://localhost:9222)"
            )

        except Exception as e:
            self._status_var.set(f"  Error: {e}")

        self._root.after(self._interval, self._poll)

    def _quit(self):
        self._running = False
        self._root.destroy()


def main():
    parser = argparse.ArgumentParser(
        description="Live view into the agent's virtual display."
    )
    parser.add_argument("--host", default="localhost",
                        help="Frame server host (default: localhost)")
    parser.add_argument("--port", type=int, default=9222,
                        help="Frame server port (default: 9222)")
    parser.add_argument("--fps", type=int, default=15,
                        help="Target frames per second (default: 15)")
    args = parser.parse_args()

    print(f"Agent Live View — http://{args.host}:{args.port}/frame @ {args.fps} FPS")
    print(f"The frame server starts automatically when the AI opens a display.")
    print()

    viewer = Viewer(host=args.host, port=args.port, fps=args.fps)
    viewer.run()


if __name__ == "__main__":
    main()