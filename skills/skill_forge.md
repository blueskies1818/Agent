---
description: Create and register new skills when a needed capability doesn't exist yet.
---

# skill_forge — Create and register new skills

Use this skill when you need a capability that doesn't exist yet.
You install the tool in the sandbox, write a skill file to the workspace,
then register it so it's available in all future sessions.

---

## When to use this

- You need a tool that isn't covered by any existing skill
- You've written a reusable script or wrapper and want to document it as a skill
- You want to check what skills you or past sessions have created

---

## Full workflow

### 1. Install the tool (if needed)

```bash
apt-get install -y ffmpeg
# or
pip install some-library
# or write a script to /usr/local/bin/my_tool
```

### 2. Verify it works

```bash
ffmpeg -version
```

### 3. Write the skill file to the workspace

The file **must** have a frontmatter block with `keywords:` and a `#` heading.

```bash
printf '---\nkeywords: ffmpeg, video, convert, encode, transcode, mp4, resize\n---\n# FFmpeg — Video processing and conversion\n\n## Install\n\n```bash\napt-get install -y ffmpeg\n```\n\n## Usage\n\n```bash\nffmpeg -i input.mp4 output.avi\n```\n' > /workspace/ffmpeg.md
```

### 4. Register it (moves the file — workspace copy is deleted)

```
skill_forge -register ffmpeg.md ffmpeg
```

The file is **moved** from `/workspace/ffmpeg.md` to `skills/ffmpeg.md`.
The workspace copy is deleted on success.

---

## Commands

```
skill_forge -register <file> <name>   Register a skill from workspace (file deleted after)
skill_forge -list                     List all skills — agent-created are marked [agent]
skill_forge -remove <name>            Remove an agent-created skill (built-ins protected)
skill_forge -audit                    Show only skills you have created
```

---

## Skill file format

```markdown
---
keywords: comma, separated, terms, that, match, user, requests
---
# ToolName — Short description

## Install
how to install it in the sandbox

## Usage
command examples
```

### Rules
- `keywords:` is required — without it the file will be rejected
- Must have a `# heading` line
- No `<action>`, `<think>`, `<plan>` tags — they will be stripped
- Max 10 KB

---

## What gets protected

Built-in skills (`read`, `write`, `edit`, `delete`, `memory`, `web_search`,
`debug_ui`, `skill_forge`) cannot be overwritten or removed via this mod.
