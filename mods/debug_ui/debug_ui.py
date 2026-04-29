"""
mods/debug_ui/debug_ui.py — Headless UI debugging.

Launch GUI applications inside the sandbox's virtual display,
take screenshots, and interact via mouse/keyboard — all without
a physical monitor.

The live frame server starts automatically when the display is
created — the user can watch at http://localhost:9222 or with
python viewer.py.  No commands needed to manage the stream.

Intercepted shell syntax:
    debug_ui -start "python app.py"
    debug_ui -screenshot
    debug_ui -click 640 400
    debug_ui -double-click 640 400
    debug_ui -right-click 640 400
    debug_ui -type "hello world"
    debug_ui -key Return
    debug_ui -key ctrl+s
    debug_ui -scroll up
    debug_ui -scroll down
    debug_ui -drag 100 200 300 400
    debug_ui -close
"""

from __future__ import annotations

import re
import threading
import time
from functools import wraps

from mods._shared import extract_quoted as _extract_quoted

from config import DISPLAY_RESOLUTION, DISPLAY_NUMBER, UI_SETTLE_DELAY
from engine.media import MediaAttachment
from engine.mod_api import ModResult, log_action
from engine.sandbox import run_command, read_file, is_docker

_SCREENSHOT_PATH      = "/tmp/_debug_ui_screenshot.png"
_WORKSPACE_LATEST     = "/workspace/.agent/screenshots/latest.png"
_WORKSPACE_SCREENSHOTS = "/workspace/.agent/screenshots"

# How long to wait for an app to render its first frame after launch.
_STARTUP_DELAY = 3.0

# How many times to retry capturing if the screen looks blank.
_MAX_CAPTURE_RETRIES = 8
_RETRY_INTERVAL = 2.0

_CACHE_LOCK = threading.Lock()
_CACHED_FRAME: bytes | None = None
_LAST_CAPTURE_TIME = 0.0
_CAPTURE_BUSY = False


def _requires_display(func):
    """Decorator — ensures the virtual display is running before the action."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        err = _ensure_display()
        if err:
            return ModResult(text=err)
        return func(*args, **kwargs)
    return wrapper


def _get_exit_code(output: str) -> int:
    """Extract exit code from run_command output."""
    match = re.search(r'\[exit code: (\d+)\]', output)
    return int(match.group(1)) if match else 0


def handle(args: list[str], raw: str) -> ModResult:
    """Dispatch to the appropriate UI action."""
    if not is_docker():
        return ModResult(
            text="[ERROR] debug_ui requires Docker sandbox mode.\n"
                 "Launch with: SANDBOX=docker ./start.sh\n"
                 "The virtual display runs inside the Docker container."
        )

    if not args:
        return ModResult(text=_usage())

    flag = args[0].lower().lstrip("-")

    if flag == "start":
        cmd = _extract_quoted(args[1:], raw, "-start")
        if not cmd:
            return ModResult(text="[ERROR] debug_ui -start requires a command.\n" + _usage())
        return _start(cmd)

    elif flag == "screenshot":
        return _screenshot()

    elif flag == "click" and len(args) >= 3:
        return _click(args[1], args[2])

    elif flag in ("double-click", "doubleclick", "dclick") and len(args) >= 3:
        return _double_click(args[1], args[2])

    elif flag in ("right-click", "rightclick", "rclick") and len(args) >= 3:
        return _right_click(args[1], args[2])

    elif flag == "type":
        text = _extract_quoted(args[1:], raw, "-type")
        if not text:
            return ModResult(text="[ERROR] debug_ui -type requires text.\n" + _usage())
        return _type_text(text)

    elif flag == "key" and len(args) >= 2:
        return _press_key(args[1])

    elif flag == "scroll" and len(args) >= 2:
        return _scroll(args[1].lower())

    elif flag == "drag" and len(args) >= 5:
        return _drag(args[1], args[2], args[3], args[4])

    elif flag == "close":
        return _close()

    else:
        return ModResult(text=f"[ERROR] Unknown debug_ui command: '{flag}'\n" + _usage())


# ── Display lifecycle ─────────────────────────────────────────────────────────

def _ensure_display() -> str | None:
    """
    Start Xvfb, dbus, and the frame server if not already running.
    Returns error text or None.
    """
    check = run_command(f"xdpyinfo -display {DISPLAY_NUMBER} >/dev/null 2>&1 && echo UP || echo DOWN")

    if "UP" not in check:
        # Display not running — start dbus then Xvfb
        run_command("dbus-daemon --session --fork --address=unix:path=/tmp/dbus-session 2>/dev/null || true")
        run_command(
            f"nohup Xvfb {DISPLAY_NUMBER} -screen 0 {DISPLAY_RESOLUTION} -ac +extension GLX +render -noreset "
            f"> /tmp/xvfb.log 2>&1 &"
        )

        # Poll until the display is ready (up to 5 seconds)
        for _ in range(10):
            time.sleep(0.5)
            if "UP" in run_command(f"xdpyinfo -display {DISPLAY_NUMBER} >/dev/null 2>&1 && echo UP || echo DOWN"):
                break
        else:
            xvfb_log = run_command("cat /tmp/xvfb.log 2>/dev/null || echo '(no log)'")
            return f"[ERROR] Failed to start virtual display.\n{xvfb_log}"

    # Always ensure frame server is registered — whether we just created
    # the display or it was already running from a previous command.
    from engine.frame_server import is_serving, register_source
    if not is_serving():
        register_source(_capture)

    return None


def _capture() -> bytes | None:
    """
    Take a screenshot, but throttle shell commands to max 2 FPS to prevent
    resource exhaustion (the 'death spiral'). Serve from memory otherwise.
    """
    global _CACHED_FRAME, _LAST_CAPTURE_TIME, _CAPTURE_BUSY
    
    now = time.time()
    # If the frame server asks for a frame, but we just took one less than 0.5s ago,
    # OR if another thread is currently running the capture command, return the RAM cache.
    if _CAPTURE_BUSY or (now - _LAST_CAPTURE_TIME < 0.5):
        return _CACHED_FRAME
        
    with _CACHE_LOCK:
        if _CAPTURE_BUSY: 
            return _CACHED_FRAME
        _CAPTURE_BUSY = True
        
    try:
        # Atomic file creation using a .tmp file
        run_command(
            f"rm -f {_SCREENSHOT_PATH}.tmp && "
            f"DISPLAY={DISPLAY_NUMBER} import -window root png:{_SCREENSHOT_PATH}.tmp && "
            f"convert {_SCREENSHOT_PATH}.tmp -resize 960x600\\> -quality 85 -depth 8 png:{_SCREENSHOT_PATH}"
        )
        new_frame = read_file(_SCREENSHOT_PATH)
        
        if new_frame: # Only update cache if the read was successful
            _CACHED_FRAME = new_frame
            _LAST_CAPTURE_TIME = time.time()
            
        return _CACHED_FRAME
    finally:
        _CAPTURE_BUSY = False


def _persist_screenshot(img: bytes, app_desc: str = "") -> None:
    """Save the latest screenshot to workspace for cross-turn persistence."""
    try:
        run_command(f"mkdir -p {_WORKSPACE_SCREENSHOTS}")
        run_command(f"cp {_SCREENSHOT_PATH} {_WORKSPACE_LATEST} 2>/dev/null || true")
        from engine.mod_api import save_fact
        desc = f": {app_desc}" if app_desc else ""
        save_fact(
            f"debug_ui GUI session active{desc}. "
            f"Latest screenshot saved at {_WORKSPACE_LATEST}. "
            "Use debug_ui -screenshot to see the current state."
        )
    except Exception:
        pass


def _to_attachments(imgs: list[bytes]) -> list[MediaAttachment]:
    """Convert a list of raw PNG bytes to MediaAttachment objects."""
    return [
        MediaAttachment(type="image", data=img, mime_type="image/png")
        for img in imgs
        if img
    ]


def _is_blank(img_bytes: bytes) -> bool:
    """
    Check if a screenshot is blank/black.

    A solid-color 1280×800 PNG compresses to under 15KB.
    Real UI content with text, buttons, and colors produces 50KB+.
    """
    return len(img_bytes) < 15_000


def _capture_after_action() -> tuple[str, list[bytes]]:
    """Capture after a click/type/key — short settle, warns if blank."""
    time.sleep(UI_SETTLE_DELAY)
    img = _capture()
    if not img:
        return "screenshot failed — display may not be running", []
    _persist_screenshot(img)
    if _is_blank(img):
        return (
            "screenshot captured but screen appears blank — "
            "the app may have crashed or closed. "
            "Try debug_ui -screenshot to check, or re-launch the app.",
            [img],
        )
    return "screenshot captured", [img]


def _capture_with_retry() -> tuple[str, list[bytes]]:
    """
    Capture with retries — used after app startup and explicit screenshots.

    Total max wait: _MAX_CAPTURE_RETRIES × _RETRY_INTERVAL = 16 seconds.
    """
    img = None
    for attempt in range(1, _MAX_CAPTURE_RETRIES + 1):
        img = _capture()
        if img and not _is_blank(img):
            _persist_screenshot(img)
            return f"screenshot captured (attempt {attempt})", [img]
        if attempt < _MAX_CAPTURE_RETRIES:
            print(f"  [debug_ui] screen blank, retrying ({attempt}/{_MAX_CAPTURE_RETRIES})...",
                  flush=True)
            time.sleep(_RETRY_INTERVAL)

    if img:
        _persist_screenshot(img)
        return (
            f"screenshot captured but screen appears blank after "
            f"{_MAX_CAPTURE_RETRIES} attempts ({_MAX_CAPTURE_RETRIES * _RETRY_INTERVAL:.0f}s). "
            f"The app may still be loading — try debug_ui -screenshot again in a moment.",
            [img],
        )
    return "screenshot failed — display may not be running", []


# ── Commands ──────────────────────────────────────────────────────────────────

def _start(command: str) -> ModResult:
    """Launch an application on the virtual display."""
    err = _ensure_display()
    if err:
        return ModResult(text=err)

    # Use nohup + setsid so the process survives after docker exec's
    # bash shell exits.
    # DBUS_SESSION_BUS_ADDRESS is set for GTK apps (Firefox, etc.)
    run_command(
        f"nohup setsid env DISPLAY={DISPLAY_NUMBER} "
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-session "
        f"MOZ_DISABLE_CONTENT_SANDBOX=1 "
        f"{command} > /tmp/_debug_ui_app.log 2>&1 &"
    )
    log_action(f"launched: {command}", source="debug_ui")
    try:
        from engine.mod_api import save_fact
        save_fact(f"debug_ui GUI session started: `{command}` launched on virtual display.")
    except Exception:
        pass

    time.sleep(_STARTUP_DELAY)

    status, images = _capture_with_retry()

    # If still blank, include the app's stderr for debugging
    if not images or (images and _is_blank(images[0])):
        app_log = run_command("tail -20 /tmp/_debug_ui_app.log 2>/dev/null || echo '(no log)'")
        return ModResult(
            text=f"Application launched: {command}\n{status}\n\n"
                 f"App log (may indicate why screen is blank):\n{app_log}",
            attachments=_to_attachments(images),
        )

    return ModResult(
        text=f"Application launched: {command}\n{status}",
        attachments=_to_attachments(images),
    )


@_requires_display
def _screenshot() -> ModResult:
    """Capture the current screen without any interaction."""
    status, images = _capture_with_retry()
    log_action("took screenshot", source="debug_ui")
    return ModResult(text=status, attachments=_to_attachments(images))


@_requires_display
def _click(x: str, y: str) -> ModResult:
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool mousemove {x} {y} click 1")
    log_action(f"clicked at ({x}, {y})", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"clicked at ({x}, {y}) — {status}", attachments=_to_attachments(images))


@_requires_display
def _double_click(x: str, y: str) -> ModResult:
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool mousemove {x} {y} click --repeat 2 1")
    log_action(f"double-clicked at ({x}, {y})", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"double-clicked at ({x}, {y}) — {status}", attachments=_to_attachments(images))


@_requires_display
def _right_click(x: str, y: str) -> ModResult:
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool mousemove {x} {y} click 3")
    log_action(f"right-clicked at ({x}, {y})", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"right-clicked at ({x}, {y}) — {status}", attachments=_to_attachments(images))


@_requires_display
def _type_text(text: str) -> ModResult:
    safe = text.replace("'", "'\\''")
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool type --delay 50 '{safe}'")
    log_action(f"typed: \"{text}\"", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"typed \"{text}\" — {status}", attachments=_to_attachments(images))


@_requires_display
def _press_key(key: str) -> ModResult:
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool key {key}")
    log_action(f"pressed key: {key}", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"pressed {key} — {status}", attachments=_to_attachments(images))


@_requires_display
def _scroll(direction: str) -> ModResult:
    button = "4" if direction == "up" else "5"
    run_command(f"DISPLAY={DISPLAY_NUMBER} xdotool click --repeat 3 {button}")
    log_action(f"scrolled {direction}", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"scrolled {direction} — {status}", attachments=_to_attachments(images))


@_requires_display
def _drag(x1: str, y1: str, x2: str, y2: str) -> ModResult:
    run_command(
        f"DISPLAY={DISPLAY_NUMBER} xdotool mousemove {x1} {y1} "
        f"mousedown 1 mousemove {x2} {y2} mouseup 1"
    )
    log_action(f"dragged from ({x1},{y1}) to ({x2},{y2})", source="debug_ui")
    status, images = _capture_after_action()
    return ModResult(text=f"dragged ({x1},{y1})→({x2},{y2}) — {status}", attachments=_to_attachments(images))


def _close() -> ModResult:
    """Kill all applications, stop frame server, stop Xvfb."""
    from engine.frame_server import unregister_source

    unregister_source()

    run_command(f"pkill -f 'DISPLAY={DISPLAY_NUMBER}' 2>/dev/null || true")
    run_command(f"pkill -f 'Xvfb {DISPLAY_NUMBER}' 2>/dev/null || true")

    log_action("closed debug_ui session", source="debug_ui")
    return ModResult(text="Display and applications closed.")


def _usage() -> str:
    return """Usage:
  debug_ui -start "python app.py"     Launch app, return screenshot
  debug_ui -screenshot                Fresh screenshot
  debug_ui -click 640 400             Left click at (x, y)
  debug_ui -double-click 640 400      Double click
  debug_ui -right-click 640 400       Right click
  debug_ui -type "hello world"        Type text at current focus
  debug_ui -key Return                Press a key (Return, Tab, Escape, ctrl+s)
  debug_ui -scroll up                 Scroll up/down
  debug_ui -drag 100 200 300 400      Drag from (x1,y1) to (x2,y2)
  debug_ui -close                     Kill app and stop display

Live view is automatic — open http://localhost:9222 or run: python viewer.py"""