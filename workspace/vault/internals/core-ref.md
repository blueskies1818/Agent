# Core Reference

Always-available operational reference. These conventions apply on every task.

## Shell conventions

Write file content with printf (NOT heredoc — heredoc breaks the XML parser):
```
printf 'line one\nline two\n' > file.txt
printf 'multi\nline\ncontent\n' > script.py
```

For files with special characters or quotes, use $'...' syntax:
```
printf $'line with \'quotes\'\n' > file.txt
```

After writing always verify: `cat filename`
`ls -la` confirms a file exists but NOT that its content was written correctly.

Chain actions — do not stop between steps unless waiting for results.

## File operations

| Operation | Command |
|-----------|---------|
| Read      | `cat filename` |
| Write     | `printf '...' > filename` |
| Append    | `printf '...' >> filename` |
| Edit line | `sed -i 's/old/new/' filename` |
| Delete    | `rm filename` |
| List dir  | `ls -la path/` |
| Find file | `find . -name "pattern"` |
| Search content | `grep -r "term" path/` |

## System

- Shell: bash
- Working directory: set by sandbox config (check with `pwd`)
- Sandbox mode: local subprocess or docker exec (transparent — behave the same either way)

## Mod commands

The following commands are intercepted by the system BEFORE reaching the shell.
They are NOT filesystem binaries — `which`, `command -v`, and `find` will never locate them.

| Command | Purpose |
|---------|---------|
| `memory` | Query / read / write persistent memory |
| `search_web` | Web search and URL fetch |
| `debug_ui` | Headless GUI interaction |
| `schedule` | Create / list / cancel scheduled tasks |
| `vault` | Bucketed knowledge base |
| `passwd` | Session-scoped credential cache |
| `run_shell` | Direct sandbox shell |

Use them exactly as documented. Never wrap in `which`, `command -v`, or `find`.
If a mod command stops working, escalate — do NOT attempt to find its binary path.

**Each mod command MUST be the only command in its shell action.**
Never chain with `&&`, `;`, `||`, or `|`. One action per mod call:
```
✓  <action type="shell"><command>debug_ui -screenshot</command></action>
✗  <action type="shell"><command>ls -la && debug_ui -screenshot</command></action>
```

For GUI browser navigation, standard keyboard shortcuts work without needing to
locate UI elements visually: focus address bar with ctrl+l, type URL, press Return.

## Memory

Write a fact:
```xml
<action type="memory"><op>write</op><content>fact to store</content></action>
```

Read memory:
```xml
<action type="memory"><op>read</op></action>
```

## Load a skill

```xml
<action type="skill"><n>skill_name</n></action>
```

Skills provide detailed instructions for specific tools and workflows. Load before using.

## Done

```xml
<action type="done"/>
```

Emit when the task is fully complete and verified. Write a plain-text summary first, then emit done.


[[overview]]
