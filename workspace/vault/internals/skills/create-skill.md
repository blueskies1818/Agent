---
name:        create_skill
description: Create a new skill — install the CLI tool, write the skill card in two places, and update the vault index
tags:        create skill, new skill, install tool, apt, pip, add capability, register skill
tier:        global
status:      active
created_at:  2026-04-28
author:      user
uses:        0
---

# Creating Skills

Use this when you need a capability that doesn't exist yet, or when you've written
a reusable script and want to document it so future sessions can find it.

---

## Overview

A skill lives in two places:
1. **`skills/<name>.md`** — runtime location; the SkillRetriever indexes this for RAG.
2. **`workspace/vault/internals/skills/<name>.md`** — Obsidian vault copy; human-readable reference.

Obsidian auto-discovers `.md` files — no `.obsidian/` config changes are needed.
You only need to update one JSON file: `workspace/vault/index.json`.

---

## Step 1 — Install the CLI tool (if needed)

```bash
# Debian/Ubuntu package
apt-get install -y <tool>

# Python package
pip install <package>

# Manual binary — write a script to /usr/local/bin/ and make it executable
printf '#!/bin/bash\nexec /path/to/real/binary "$@"\n' > /usr/local/bin/<tool>
chmod +x /usr/local/bin/<tool>
```

Verify it's on PATH before proceeding:

```bash
which <tool>
<tool> --version
```

If `which` returns nothing, the install failed or the PATH doesn't include the install dir.
Common fix: `export PATH="$PATH:/usr/local/bin"` or check `echo $PATH`.

---

## Step 2 — Write the runtime skill card

Write to **`skills/<name>.md`** (relative to the project root, alongside `mods/`, `engine/`, etc.):

```bash
printf '---\nname:        <name>\ndescription: <one-line description>\ntags:        <comma, separated, keywords>\ntier:        global\nstatus:      active\ncreated_at:  <YYYY-MM-DD>\nauthor:      agent\nuses:        0\n---\n\n# <Name>\n\n## Install\n\napt-get install -y <tool>\n\n## Usage\n\n<tool> [flags] <input>\n\n## Examples\n\n```bash\n<tool> -flag value\n```\n' > skills/<name>.md
```

Frontmatter rules:
- `name:` and `description:` are required — used for semantic search
- `tags:` optional but improves retrieval
- Must have a `# Heading` line
- No `<action>`, `<think>`, or `<plan>` tags — they will be stripped on load

---

## Step 3 — Write the vault copy

Write the same content to **`workspace/vault/internals/skills/<name>.md`**:

```bash
cp skills/<name>.md workspace/vault/internals/skills/<name>.md
```

Or write it directly with `printf` to that path if you prefer one step.

---

## Step 4 — Update the vault index

Edit **`workspace/vault/index.json`** to increment `skills.content_count` by 1
and update `updated_at` to today's date.

Read the file first, then use the `edit` skill to change exactly those two fields.
Do not rewrite the whole file — only patch the two values.

Example of the fields to update:
```json
"updated_at": "2026-04-28T00:00:00",
...
"skills": {
  "content_count": 12
}
```

---

## Step 5 — Verify

```bash
# Confirm both files exist
ls skills/<name>.md
ls workspace/vault/internals/skills/<name>.md

# Confirm the tool works
<tool> --version
```

The SkillRetriever bootstraps from `skills/` on first use — the new skill will be
searchable by RAG immediately in the next session. To make it searchable right now
without restarting, run `vault reindex_bucket skills` if the vault mod is available.

---

## Skill frontmatter reference

```markdown
---
name:        ffmpeg
description: Encode, convert, and compress video and audio files
tags:        video, audio, ffmpeg, compress, convert, encode
tier:        global          # global | project
status:      active          # active | pending | archived
created_at:  2026-04-28
author:      agent           # agent | user
uses:        0
---
```


[[internals/skills-and-mods]]
