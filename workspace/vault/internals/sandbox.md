# Docker Sandbox

The sandbox is an isolated Ubuntu 24.04 container that receives commands via `docker exec`. The agent Python code runs on the **host** — only shell execution happens inside the container. This means the agent can install packages, manage services, and run GUI applications with root access, while the host filesystem remains untouched except for the bind-mounted workspace.

---

## Container setup

### Build

```bash
docker build --no-cache -t agent-sandbox .
```

The image is built automatically by `start.sh` when `SANDBOX=docker` is set and the image doesn't exist yet. If `dockerfile` changes, `start.sh` detects the change via a hash check and rebuilds.

### What's installed

| Category | Packages |
|----------|---------|
| Core tools | `bash`, `coreutils`, `findutils`, `grep`, `sed`, `gawk`, `file`, `tree`, `jq`, `zip`, `unzip`, `curl`, `wget`, `git` |
| Languages | `python3`, `python3-pip`, `python3-venv`, `nodejs`, `npm` |
| GUI stack | `xvfb`, `xdotool`, `imagemagick`, `x11-utils` |
| Browser deps | `dbus`, `dbus-x11`, `libgtk-3-0`, `libdbus-glib-1-2` |
| Browser | Firefox (from Mozilla's official APT repo — not snap) |

APT lists are preserved so the agent can install additional packages at runtime with `apt-get install`.

### `docker-compose.yml`

The Compose file provides an alternative to running the container via `start.sh` directly:

```yaml
services:
  sandbox:
    image: agent-sandbox
    container_name: agent-sandbox
    restart: unless-stopped
    mem_limit: 2g
    shm_size: 1g          # needed for Firefox/Chromium rendering
    pids_limit: 512
    cap_drop: [ALL]
    cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, FSETID, SETGID, SETUID, KILL, NET_BIND_SERVICE]
    security_opt: [no-new-privileges:true]
    network_mode: bridge
    volumes:
      - ${PROJECT:-./workspace}:/workspace
```

```bash
docker compose up -d      # start
docker compose down       # stop and remove
```

---

## Bind mount layout

| Host path | Container path | Controlled by |
|-----------|---------------|---------------|
| `$PROJECT` (if set) | `/workspace` | `PROJECT` env var |
| `./workspace/` (default) | `/workspace` | Default fallback |

Everything under `/workspace` is visible from both sides and changes sync immediately. Files the agent writes inside the container at `/workspace/` appear on the host and vice versa.

**The agent cannot see any other host path.** If `start.sh` detects the mount has changed (e.g. `PROJECT` was updated), it stops and recreates the container with the new mount.

---

## Security model

### Capability set

The container drops all Linux capabilities then adds back only the minimum required:

| Capability | Why kept |
|-----------|---------|
| `CHOWN` | File ownership changes |
| `DAC_OVERRIDE` | Read/write files regardless of owner |
| `FOWNER` | `chmod`, `chown` on owned files |
| `FSETID` | Set-UID/GID bits |
| `SETGID` / `SETUID` | User switching (e.g. running services) |
| `KILL` | Process management |
| `NET_BIND_SERVICE` | Bind to ports below 1024 |

Dangerous capabilities (`SYS_ADMIN`, `SYS_PTRACE`, `NET_RAW`, etc.) are permanently dropped.

### Resource limits

| Limit | Value |
|-------|-------|
| Memory | 1 GB (via `docker run`) / 2 GB (via Compose) |
| Shared memory | 1 GB (`shm_size` — needed for browsers) |
| CPU | 1.0 core |
| PIDs | 512 |
| Privilege escalation | Blocked (`no-new-privileges:true`) |

### Root inside the container

The agent runs as root inside the container, which allows `apt-get install`, `systemctl`, `chmod 777`, dev server startup, and similar privileged operations. This is intentional and safe because:
- The only host path exposed is the workspace bind mount
- Dangerous kernel capabilities are dropped
- `no-new-privileges` prevents escalation via setuid binaries

---

## `engine/sandbox.py` — Full API

`engine/sandbox.py` is the single import point for all sandbox interaction. It is transparent in both modes — the same call works whether `SANDBOX_MODE` is `"local"` or `"docker"`.

### `run_command(command, timeout=None) → str`

The primary entry point for all shell execution.

```python
from engine.sandbox import run_command

output = run_command("ls -la /workspace")
output = run_command("pip install fastapi", timeout=120)
```

- Routes to `subprocess.run(cwd=SANDBOX_ROOT)` in local mode
- Routes to `docker exec -w /workspace agent-sandbox /bin/bash -c "<command>"` in Docker mode
- Returns combined `stdout + stderr`
- Non-zero exit codes append `\n[exit code: N]` to the output
- Errors are returned as `[ERROR] ...` strings — never raised
- Timeout defaults to `SHELL_TIMEOUT` (30 s); override per-call

### File transfer

| Function | Direction | Docker implementation | Local implementation |
|----------|-----------|----------------------|---------------------|
| `pull_file(container_path, host_path)` | sandbox → host | `docker cp container:path host_path` | `shutil.copy2` within host fs |
| `push_file(host_path, container_path)` | host → sandbox | `docker cp host_path container:path` | `shutil.copy2` within host fs |
| `read_file(container_path) → bytes\|None` | sandbox → memory | `docker exec cat <path>` (binary) | `path.read_bytes()` |

In local mode, "container paths" like `/workspace/foo.txt` are resolved relative to `SANDBOX_ROOT` by stripping the `DOCKER_WORKDIR` prefix.

### Status queries

| Function | Returns |
|----------|---------|
| `is_docker()` | `True` if `SANDBOX_MODE == "docker"` |
| `container_running()` | `True` if the Docker container responds to `docker inspect` |
| `get_project_display()` | Human-readable label for the current workspace |
| `ensure_sandbox()` | Creates `SANDBOX_ROOT` (local) or starts the container (Docker); call once at startup |

### Container lifecycle

`ensure_sandbox()` handles the full lifecycle:
1. Ensures the host workspace directory exists
2. Checks if the container is already running
3. Verifies the running container has the correct bind mount
4. If the mount has changed, stops and recreates the container
5. Starts a fresh container if none is running

The container is started with `docker run -d ... tail -f /dev/null` — it sits idle and receives work via `docker exec`. There is no entrypoint that starts a process; the container is purely a filesystem and process-namespace boundary.

---

## Virtual display (Xvfb)

The `debug_ui` mod uses Xvfb (X virtual framebuffer) to run GUI applications headlessly inside the container. Xvfb provides a display that applications can render to without a physical monitor.

### Configuration

| Config variable | Default | Description |
|----------------|---------|-------------|
| `DISPLAY_RESOLUTION` | `"1280x800x24"` | Width × Height × colour depth |
| `DISPLAY_NUMBER` | `":99"` | X display identifier |
| `UI_SETTLE_DELAY` | `1.5` | Seconds to wait after a UI action before screenshotting |

### Start sequence (managed by `debug_ui`)

```bash
# 1. Start dbus (required by GTK/Firefox)
dbus-daemon --session --fork --address=unix:path=/tmp/dbus-session

# 2. Start Xvfb
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &

# 3. Launch application (via nohup + setsid to survive docker exec exit)
nohup setsid env DISPLAY=:99 DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-session \
  firefox > /tmp/_debug_ui_app.log 2>&1 &
```

### Screenshot pipeline

`debug_ui` uses ImageMagick `import` to capture the virtual display:

```bash
DISPLAY=:99 import -window root png:/tmp/_debug_ui_screenshot.png.tmp
convert /tmp/_debug_ui_screenshot.png.tmp -depth 8 -type TrueColor png:/tmp/_debug_ui_screenshot.png
```

An atomic `.tmp` → final rename prevents the frame server from serving a partial write.

The capture function is throttled to 2 FPS max via a time check — capturing more frequently would exhaust the container's CPU under the MJPEG stream load. The most recent frame is cached in RAM and served from cache when the throttle is active.

**Blank detection:** a solid-colour 1280×800 PNG compresses to < 15 KB. Real UI content with text and colours produces 50 KB+. The mod uses this heuristic to detect blank screens and retry capture up to 8 times (16 seconds total) after app startup.

---

## Live frame server

`engine/frame_server.py` is a lightweight HTTP server that streams the latest captured frame from any registered source. It is completely generic — `debug_ui` is just one possible source.

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Status HTML page with an embedded MJPEG stream |
| `GET /frame` | Latest PNG snapshot (single frame) |
| `GET /stream` | MJPEG stream at ~15 FPS |

The server binds to `0.0.0.0:FRAME_SERVER_PORT` (default 9222) so it is reachable from the host even though the display lives in the container.

### Lifecycle

The server auto-starts when `register_source()` is called and auto-stops when `unregister_source()` is called. It runs in a daemon thread — it dies with the main process.

```python
from engine.frame_server import register_source, unregister_source, is_serving

register_source(my_capture_fn)   # starts server on first call
is_serving()                     # True
unregister_source()              # stops server
```

`debug_ui` registers its `_capture` function when `_ensure_display()` runs and unregisters it when `-close` is called.

### Viewer app

```bash
python mods/debug_ui/viewer.py              # default: localhost:9222 @ 15 FPS
python mods/debug_ui/viewer.py --fps 30
python mods/debug_ui/viewer.py --host 192.168.1.10 --port 9222
```

The viewer is a standalone Tkinter + Pillow app. It polls `GET /frame` at the requested interval, scales the image to fit its window, and displays an FPS counter. It has no knowledge of `debug_ui` specifically — it just connects to the frame server port.

Requirements: `pip install pillow` (Tkinter is included with most Python distributions).

---

## Container restart behaviour

| Event | What persists | What resets |
|-------|--------------|-------------|
| Agent restart (container stays up) | All `/workspace` files, installed packages, running services | Nothing — container is unchanged |
| `docker compose restart` | `/workspace` files (bind mount) | Installed packages, running processes, Xvfb display |
| `docker compose down && up` | `/workspace` files (bind mount) | Everything except the workspace |
| Container recreation (mount change) | `/workspace` files | Same as down+up |

The bind mount is the persistence boundary. Files in `/workspace` survive all restart scenarios. Everything else (installed packages, background processes, display sessions) is ephemeral and must be re-established after a container restart.

**Practical implication:** if the agent installs a package with `apt-get install` and the container is restarted, it will need to install it again. For packages needed on every run, add them to the `dockerfile` and rebuild the image.


[[overview]]
