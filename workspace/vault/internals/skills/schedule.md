---
name:        schedule
description: Schedule tasks to run in the future — once, on a repeating interval, or on a cron expression
tags:        schedule, later, recurring, cron, interval, remind, future, task, automation
tier:        global
status:      active
created_at:  2026-04-28
author:      user
uses:        0
---

# Schedule — future and recurring tasks

Use `schedule` to queue any prompt for future execution. The scheduler picks
up JSON files from `scheduled/` every 60 seconds and posts due tasks to the
agent queue. The task runs exactly as if the user typed the prompt.

---

## Commands

### Create a one-time task

```bash
# At a specific datetime (ISO 8601, UTC)
schedule -add "check if the server is still running" -type once -value "2026-05-01T10:00:00Z"

# Relative offset from now: s=seconds, m=minutes, h=hours, d=days
schedule -add "remind me to commit" -type once -value 2h
schedule -add "run the weekly report" -type once -value 1d
```

### Create a repeating interval task

```bash
schedule -add "ping health endpoint" -type interval -value 30m
schedule -add "rotate logs" -type interval -value 12h
schedule -add "weekly backup" -type interval -value 7d
```

### Create a cron task

Five fields: `MIN HOUR DOM MON DOW` (DOW: 0=Sun, 1=Mon … 6=Sat)

```bash
schedule -add "daily standup summary" -type cron -value "0 9 * * 1-5"   # Mon–Fri 9am
schedule -add "monthly report" -type cron -value "0 8 1 * *"             # 1st of month 8am
schedule -add "every 15 minutes" -type cron -value "*/15 * * * *"
```

### Termination options

By default tasks run indefinitely (interval/cron) or stay until next_run is reached (once).
Use `-stop` to control what happens after the task fires:

```bash
# Remove the task once its dispatched job reaches complete or failed
schedule -add "one-time migration" -type once -value 30m -stop after_completion

# Remove the task after a given date
schedule -add "daily digest" -type interval -value 24h -stop on_date -until "2026-12-31"
```

| `-stop` value      | Behaviour |
|---|---|
| `never`            | Default. Keeps firing forever (interval/cron) |
| `after_completion` | Deleted once the dispatched task reaches complete/failed |
| `on_date`          | Deleted once `-until` date has passed (requires `-until`) |

### List all scheduled tasks

```bash
schedule -list
```

### Show full task detail

```bash
schedule -show 2026-05-01_check-if-the-server
```

### Remove a task

```bash
schedule -remove 2026-05-01_check-if-the-server
```

---

## How it works

Each `schedule -add` writes a JSON file to `scheduled/<task_id>.json`.
The background scheduler process reads these every 60 seconds, computes
whether the task is due, and if so POSTs the prompt to the agent HTTP queue.

The task_id is auto-generated from the date and a slug of your prompt.

---

## Examples

```bash
# Run a health check in 10 minutes, delete after it completes
schedule -add "check disk usage and alert if over 80%" -type once -value 10m -stop after_completion

# Every Monday at 9am, generate a weekly summary
schedule -add "summarise what was done this week from memory and write it to workspace/reports/" \
  -type cron -value "0 9 * * 1"

# Check every hour until end of year, then stop
schedule -add "monitor the deploy and report any errors" \
  -type interval -value 1h -stop on_date -until "2026-12-31"

# See what's queued
schedule -list
```
[[overview]]

[[skills-and-mods]]
