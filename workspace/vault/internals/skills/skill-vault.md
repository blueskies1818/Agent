---
name:        vault
description: Navigate and reindex the Obsidian vault — understand its structure, browse buckets, and maintain ChromaDB embeddings
tags:        vault, bucket, knowledge, obsidian, navigate, browse, reindex, index
tier:        global
status:      active
created_at:  2026-04-21
author:      user
uses:        0
---

# Vault — Knowledge Navigation

The vault is the agent's Obsidian-backed knowledge base. It lives at
`workspace/vault/` and is organised into named **buckets** — topic folders
that each have their own ChromaDB collection for semantic search.

You do not write content through the `vault` command. You read files directly
with `cat` / the `read` skill, and you search semantically with `memory -vault`.

---

## Structure

```
workspace/vault/
├── index.json                  ← bucket registry (source of truth)
└── internals/                  ← bucket: "internals"
    ├── architecture.md
    ├── engine.md
    ├── ...
    └── skills/                 ← bucket: "skills"
        ├── skill-read.md
        ├── skill-edit.md
        └── ...
```

`index.json` maps bucket names to folder paths and holds metadata:

```json
{
  "buckets": {
    "internals": { "path": "internals", "content_count": 18, "description": "..." },
    "skills":    { "path": "internals/skills", "content_count": 11, "description": "..." }
  }
}
```

---

## Navigating the vault

```bash
# See all buckets
cat workspace/vault/index.json

# List files in a bucket (shows path from index)
vault -contents internals
vault -contents skills

# Read a doc directly
cat workspace/vault/internals/architecture.md
cat workspace/vault/internals/skills/skill-read.md
```

Or use the `read` skill for any `.md` file path shown by `-contents`.

---

## Searching

**Don't use vault for search.** Use `memory` instead — it searches vault buckets
along with conversation history, preferences, and RAG embeddings all in one call:

```bash
memory -vault skills "how do I write a file"
memory -vault internals "context window eviction"
memory -vault * "async patterns"        # searches every bucket
memory -query "what do I know about X"  # searches everything including vault
```

---

## Commands

### List all buckets
```bash
vault -list
```
Shows each bucket with its doc count, folder path, and description from `index.json`.

### List docs in a bucket
```bash
vault -contents skills
vault -contents internals
```

### Reindex

Reindex a single bucket:
```bash
vault -reindex skills
vault -reindex internals
```

Reindex **all** buckets at once (omit bucket name):
```bash
vault -reindex
```

Re-embeds every `.md` file in the bucket(s) from disk into ChromaDB. Use this after:
- Manually editing files in Obsidian
- Moving files between bucket folders
- Updating `path` in `index.json`

Do **not** reindex automatically on every change — it's expensive (one embedding
call per file) and should be visible in the plan log as an intentional step.

---

## Adding new content

To add a new knowledge doc to the vault:

1. Write the `.md` file into the correct bucket folder (e.g. `workspace/vault/internals/skills/`)
2. Update `index.json` to increment `content_count` for that bucket and update `updated_at`
3. Run `vault -reindex <bucket>` to embed the new file

For adding a new skill specifically, see the `create_skill` skill.
