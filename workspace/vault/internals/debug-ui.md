# Debug UI Mod

`debug_ui` lets the agent launch and interact with GUI applications inside the Docker sandbox's virtual display (Xvfb). It provides mouse, keyboard, and screenshot control without a physical monitor. Every interaction command automatically captures a screenshot so the agent can see the result of each action before issuing the next one.

**Requires Docker mode.** The virtual display runs inside the container. `debug_ui` returns `[ERROR]` immediately if `SANDBOX_MODE != "docker"`.

---

## When to use it

- Testing a web app in a real browser (Firefox) without a real monitor
- Interacting with a desktop GUI application (file managers, editors, terminals)
- Filling forms, clicking buttons, typing text, navigating menus
- Verifying that a UI renders correctly as part of an automated workflow
- Debugging rendering issues by comparing sequential screenshots

---

## All commands

### `-start` — launch an application

```bash
debug_ui -start "python app.py"
debug_ui -start "firefox"
debug_ui -start "gedit /workspace/notes.txt"
```

Starts Xvfb and dbus if not already running, launches the command via `nohup setsid`, waits 3 seconds, then attempts to capture a screenshot. Returns the screenshot and a status message.

If the screen is blank after launch, retries capture up to 8 times (2 seconds apart, 16 seconds total). If still blank after all retries, returns the app's last 20 lines of stderr (`/tmp/_debug_ui_app.log`) to help diagnose startup failures.

### `-screenshot` — capture current screen

```bash
debug_ui -screenshot
```

Returns a fresh screenshot without any interaction. Uses the same retry logic as `-start` — retries up to 8 times if the screen appears blank.

### `-click` — left click

```bash
debug_ui -click 640 400
```

Moves the mouse to `(x, y)` and sends a left button click. Waits `UI_SETTLE_DELAY` seconds (default 1.5) then captures a screenshot.

### `-double-click` — double click

```bash
debug_ui -double-click 640 400
```

Two rapid left button clicks at `(x, y)`. Useful for opening files in file managers or activating list items.

### `-right-click` — right click

```bash
debug_ui -right-click 640 400
```

Right button click at `(x, y)`. Opens context menus in most applications.

### `-type` — type text

```bash
debug_ui -type "hello world"
debug_ui -type "search query"
```

Types text at the current keyboard focus using `xdotool type --delay 50`. The 50 ms inter-character delay prevents dropped characters in applications with slow input handlers. Waits then screenshots.

To type into a specific field, click it first to set focus, then type.

### `-key` — press a key or chord

```bash
debug_ui -key Return
debug_ui -key Tab
debug_ui -key Escape
debug_ui -key ctrl+s
debug_ui -key ctrl+a
debug_ui -key super+d
```

Sends a key or key combination using `xdotool key`. Key names follow X11 conventions. Waits then screenshots.

### `-scroll` — scroll the mouse wheel

```bash
debug_ui -scroll up
debug_ui -scroll down
```

Sends 3 scroll wheel ticks in the specified direction. Useful for navigating long pages or lists. Waits then screenshots.

### `-drag` — click and drag

```bash
debug_ui -drag 100 200 300 400
```

Presses the left button at `(x1, y1)`, moves to `(x2, y2)`, releases. Useful for sliders, dragging windows, and selecting text ranges. Waits then screenshots.

### `-close` — close the display

```bash
debug_ui -close
```

Kills all processes running on the virtual display, stops Xvfb, and unregisters the frame server capture source (which auto-stops the frame server). Returns a confirmation message with no screenshot.

---

## Screenshot behaviour

Every interaction command (`-click`, `-double-click`, `-right-click`, `-type`, `-key`, `-scroll`, `-drag`) follows the same post-action pattern:

1. **Settle** — `time.sleep(UI_SETTLE_DELAY)` (default 1.5 seconds) to let the UI update
2. **Capture** — take a screenshot via ImageMagick `import`
3. **Blank check** — if the PNG is < 15 KB (solid colour heuristic), warn that the app may have crashed
4. **Return** — `ModResult(text="...", attachments=[MediaAttachment(...)])` with the PNG bytes

### Screenshot size

Before being returned, screenshots are downscaled to **960×600** (only if larger — never upscaled) and compressed with `-quality 85`. This keeps each image under ~150 KB, compared to the 1+ MB uncompressed PNGs that 1280×800 captures produce. At 4 chars ≈ 1 token, a 150 KB image is roughly 37K tokens vs 250K+ for the raw capture.

### Image context policy

The screenshot bytes are passed through `engine/media.py` as a `MediaAttachment` and serialized into a provider-specific image block. The engine enforces a **max-1-image** rule across all actor turns: `strip_all_but_last_image()` removes all earlier screenshots from `state["messages"]` before each LLM call, keeping only the most recent one. Planner and replanner strip all images entirely — they do not need visual state.

### Capture throttle

The internal `_capture()` function is throttled to **one command per 0.5 seconds**. If the frame server requests a frame while a capture is in progress or within the throttle window, it serves the last cached frame from RAM. This prevents the MJPEG stream from exhausting container CPU even at 15 FPS.

---

## Live frame server

When `debug_ui` starts a display, it automatically registers its capture function with `engine/frame_server.py`. The frame server starts on first registration.

Open in a browser:

```
http://localhost:9222          # status page with embedded MJPEG stream
http://localhost:9222/frame    # single latest PNG
http://localhost:9222/stream   # raw MJPEG stream
```

Or run the standalone viewer app:

```bash
python mods/debug_ui/viewer.py              # default: localhost:9222 @ 15 FPS
python mods/debug_ui/viewer.py --fps 5     # slower polling
python mods/debug_ui/viewer.py --fps 30    # faster polling
python mods/debug_ui/viewer.py --host 192.168.1.10  # remote host
```

The viewer is a Tkinter window that shows a live feed scaled to fit, with a status bar showing resolution and actual FPS. It polls `GET /frame` independently of the stream.

**Requirements:** `pip install pillow` (Tkinter ships with most Python distributions).

The frame server stops automatically when `debug_ui -close` is called (via `unregister_source()`).

---

## `engine/frame_server.py` — Generic live frame server

The frame server is completely generic. It knows nothing about `debug_ui` specifically. Any mod can register a capture function:

```python
from engine.frame_server import register_source, unregister_source, is_serving

def my_capture() -> bytes | None:
    """Return PNG bytes, or None if no frame is available."""
    ...

register_source(my_capture)    # auto-starts the HTTP server
is_serving()                   # → True
unregister_source()            # auto-stops the server
```

Only one capture source is active at a time. Calling `register_source` again replaces the previous source. The server runs in a daemon thread on `0.0.0.0:FRAME_SERVER_PORT` (default 9222).

### Endpoints

| Endpoint | Response |
|----------|---------|
| `GET /` | HTML page with embedded `<img src='/stream'>` — open in any browser |
| `GET /frame` | `image/png` — latest single frame; `Cache-Control: no-cache` |
| `GET /stream` | `multipart/x-mixed-replace` MJPEG at ~15 FPS server-side |

The server uses `allow_reuse_address = True` so it can restart on the same port without waiting for `TIME_WAIT`.

---

## `mods/debug_ui/viewer.py` — Standalone viewer

The viewer is designed to run on the **host** while the agent operates inside Docker. It connects to the frame server over the network.

Key details:
- Scales frames to fit the window using `PIL.Image.thumbnail` with `LANCZOS` resampling
- Shows `Connected | WxH | N.N FPS` in the status bar when receiving frames
- Shows `Waiting for frame server...` when the server is not reachable — no crash, just polling
- `--fps` controls the **poll interval** on the client side; the server always generates frames at ~15 FPS
- Closing the window gracefully stops the polling loop

---

## Common workflows

### Open a URL in Firefox and interact

```bash
debug_ui -start "firefox"
# wait for screenshot showing Firefox has loaded
debug_ui -click 640 45          # click the address bar (usually near top centre)
debug_ui -key ctrl+a            # select all existing text
debug_ui -type "https://example.com"
debug_ui -key Return
# wait for page load screenshot
debug_ui -click 640 400         # interact with page content
```

### Test a Python GUI app

```bash
debug_ui -start "python /workspace/myapp.py"
# review screenshot — if blank, check app log in the output
debug_ui -click 200 300         # click a button
debug_ui -type "test input"
debug_ui -key Return
debug_ui -screenshot            # explicit screenshot to verify state
debug_ui -close
```

### Fill a web form

```bash
debug_ui -start "firefox"
debug_ui -type "http://localhost:8080"
debug_ui -key Return
debug_ui -click 400 300         # click username field
debug_ui -type "admin"
debug_ui -key Tab               # move to password field
debug_ui -type "<<ADMIN_PASSWORD>>"   # credential placeholder
debug_ui -key Return
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `[ERROR] debug_ui requires Docker sandbox mode` | `SANDBOX_MODE` is not `"docker"` | Launch with `SANDBOX=docker ./start.sh` |
| Blank screenshot after `-start` | App still loading, crashed, or missing deps | Check app stderr in the output; install missing packages |
| App log empty or missing | App launched but exited immediately | The command likely failed; check for typos in the launch command |
| Frame server not reachable in browser | Port 9222 not exposed or wrong host | Check `FRAME_SERVER_PORT` config; use `--host` flag if connecting remotely |
| `xdotool` not found | Container rebuild needed | Run `docker build --no-cache -t agent-sandbox .` |


[[overview]]
