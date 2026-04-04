---
description: Launch and interact with GUI apps on a headless virtual display (Docker only).
keywords: ui, gui, window, screen, display, interface, button, click, debug ui, debug_ui, launch app, open app, graphical, visual, screenshot, headless, xvfb, interact with
---

# Debug UI — launch and interact with GUI applications

Use shell commands to run graphical applications on a headless virtual display.
You can see the screen via screenshots and interact with mouse clicks, typing,
and keyboard shortcuts. Every command automatically returns a screenshot.

A live view is available to the user automatically at http://localhost:9222
whenever the display is active — no commands needed to manage it.

## Requirements
- Docker sandbox mode (`SANDBOX=docker`)

## Commands

### Launch an application
```
debug_ui -start "python app.py"
```

### Take a screenshot (no interaction)
```
debug_ui -screenshot
```

### Click at coordinates
```
debug_ui -click 640 400
debug_ui -double-click 640 400
debug_ui -right-click 640 400
```

### Type text at the current focus
```
debug_ui -type "hello world"
```

### Press a key or key combination
```
debug_ui -key Return
debug_ui -key Tab
debug_ui -key Escape
debug_ui -key ctrl+s
debug_ui -key alt+F4
```

### Scroll
```
debug_ui -scroll up
debug_ui -scroll down
```

### Drag from one point to another
```
debug_ui -drag 100 200 300 400
```

### Close the application and display
```
debug_ui -close
```

## Workflow

1. Start the app — you'll get a screenshot of the initial window
2. Look at the screenshot to identify UI elements and their positions
3. Use click, type, key commands to interact — each one returns a new screenshot
4. Read the updated screenshot to verify what happened
5. Continue until the task is done, then close

## Coordinate system
- Origin (0, 0) is the top-left corner of the screen
- Default resolution is 1280×800
- Estimate coordinates from the screenshot — approximate positions work fine

## Tips
- Every command returns a screenshot — you never need to request one separately
- After typing, press Return or Tab if the UI needs it to proceed
- If a dialog appears, read the screenshot and click the appropriate button
- Standard keyboard shortcuts work (ctrl+s, ctrl+z, etc.)
- If the screen looks blank, the app may still be loading — try -screenshot again
- Close the session when done to free resources