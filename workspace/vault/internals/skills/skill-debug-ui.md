---
name:        debug_ui
description: Launch and interact with GUI apps on a headless virtual display (Docker only)
tags:        ui, gui, window, screen, display, interface, button, click, debug ui, launch app, graphical, visual, screenshot, headless, xvfb
tier:        global
status:      active
created_at:  2026-04-01
author:      user
uses:        0
---

# Debug UI — launch and interact with GUI applications

Use `debug_ui` shell commands to run graphical applications on a headless virtual display.
Every command returns a screenshot. A live view is available at http://localhost:9222.

## IMPORTANT — invocation format

`debug_ui` commands are intercepted shell commands. Always invoke them via a shell action:

```xml
<action type="shell"><command>debug_ui -start "firefox"</command></action>
<action type="shell"><command>debug_ui -screenshot</command></action>
<action type="shell"><command>debug_ui -click 640 400</command></action>
```

There is NO `<action type="debug_ui">`. It does NOT exist as a filesystem binary —
`which debug_ui`, `command -v debug_ui`, and `find / -name debug_ui` will always fail.
Never try to locate it as a file. Just use it directly in a shell action.

## Session behavior — READ FIRST

- **Sessions do NOT persist between agent turns.** Always begin with `-start`.
- **Never verify if a session is open before acting.** Just start fresh.
- **After `-start`, navigate immediately.** Do NOT take a screenshot to "confirm browser loaded" first — just send `ctrl+l` and type the URL.
- **Never escalate because you "can't confirm browser state."** If the display state is unclear, start fresh and navigate.
- `-start` returns a screenshot automatically — that's your confirmation the app launched.
- The golden path is always: start → `ctrl+l` → type URL → Return → screenshot.
- Each `debug_ui` call must be its **own shell action** — never chain with `&&`, `;`, or `|`.

## Requirements
- Docker sandbox mode (`SANDBOX=docker`)

## Commands

### Launch an application
```xml
<action type="shell"><command>debug_ui -start "firefox"</command></action>
<action type="shell"><command>debug_ui -start "python app.py"</command></action>
```

### Take a screenshot
```xml
<action type="shell"><command>debug_ui -screenshot</command></action>
```
Use this to see the current display state at any point. To "wait and watch", take a
screenshot — there is no `-wait` command.

### Navigate the browser address bar
```xml
<action type="shell"><command>debug_ui -key ctrl+l</command></action>
<action type="shell"><command>debug_ui -type "https://example.com"</command></action>
<action type="shell"><command>debug_ui -key Return</command></action>
```

### Click at coordinates
```xml
<action type="shell"><command>debug_ui -click 640 400</command></action>
<action type="shell"><command>debug_ui -double-click 640 400</command></action>
<action type="shell"><command>debug_ui -right-click 640 400</command></action>
```

### Type text at the current focus
```xml
<action type="shell"><command>debug_ui -type "hello world"</command></action>
```

### Press a key or key combination
```xml
<action type="shell"><command>debug_ui -key Return</command></action>
<action type="shell"><command>debug_ui -key Tab</command></action>
<action type="shell"><command>debug_ui -key Escape</command></action>
<action type="shell"><command>debug_ui -key ctrl+s</command></action>
<action type="shell"><command>debug_ui -key alt+F4</command></action>
```

### Scroll
```xml
<action type="shell"><command>debug_ui -scroll up</command></action>
<action type="shell"><command>debug_ui -scroll down</command></action>
```

### Drag from one point to another
```xml
<action type="shell"><command>debug_ui -drag 100 200 300 400</command></action>
```

### Close the application and display
```xml
<action type="shell"><command>debug_ui -close</command></action>
```

## Workflow

1. Launch the app — you get a screenshot of the initial window
2. Read the screenshot to identify UI elements and their coordinates
3. Use click / type / key actions — each returns a new screenshot
4. Read the updated screenshot to verify what changed
5. Continue until done, then close

## Navigating a browser to a URL

Start → navigate immediately — no intermediate verify step:

```xml
<action type="shell"><command>debug_ui -start "firefox"</command></action>
<action type="shell"><command>debug_ui -key ctrl+l</command></action>
<action type="shell"><command>debug_ui -type "https://openai.com"</command></action>
<action type="shell"><command>debug_ui -key Return</command></action>
<action type="shell"><command>debug_ui -screenshot</command></action>
```

`ctrl+l` focuses the address bar regardless of what's on screen. You do NOT need to verify
the browser loaded before sending the key — just send it and type the URL.

## Coordinate system
- Origin (0, 0) is the top-left corner
- Default resolution: 1280×800
- Estimate from the screenshot — approximate positions work fine

## Tips
- There is no `-wait` command. To wait/observe: use `-screenshot`
- After typing, press Return or Tab if the UI needs it
- If a dialog appears, read the screenshot and click the appropriate button
- If the screen looks blank, the app may still be loading — retry `-screenshot`
- Close the session when done to free resources

[[overview]]


---

## Connections (skill back-link)
- [[internals/skills-and-mods]]
