"""
mcp_servers/schedule_tools.py — Scheduler tools.

Wraps the schedule mod. Agent commands intercepted:
    schedule -add "prompt" -type once|interval|cron -value "..."
    schedule -list
    schedule -remove <task_id>
    schedule -show   <task_id>
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def schedule(args: str = "") -> str:
        """Create, list, and remove scheduled tasks.

        Args syntax:
          -add "prompt" -type once     -value "2026-05-01T10:00:00Z"
          -add "prompt" -type once     -value 2h        (relative: now + 2h)
          -add "prompt" -type interval -value 12h
          -add "prompt" -type cron     -value "0 9 * * 1"
          -add "..."    -type once     -value 1d  -stop after_completion
          -add "..."    -type cron     -value "0 0 * * *" -stop on_date -until "2026-12-31"
          -list
          -remove <task_id>
          -show   <task_id>
        """
        from mods.schedule.schedule import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []
        return handle(parsed, f"schedule {args}")
