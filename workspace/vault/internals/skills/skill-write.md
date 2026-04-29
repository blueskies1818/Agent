---
name:        write
description: Create new files and directory structures
tags:        write, create, make, new file, generate, save, output, produce, touch, scaffold
tier:        global
status:      active
created_at:  2026-04-01
author:      user
uses:        0
---

# Write — create new files and directories

Use shell commands to create files and directory structures.

## Commands

### Write a file with printf (preferred — avoids heredoc/XML parser issues)
```
printf 'line one\nline two\n' > path/to/file.txt
```

### Append to a file
```
printf 'additional content\n' >> path/to/file.txt
```

### Create a directory (including parents)
```
mkdir -p path/to/nested/dir
```

### Write a Python / shell script and make it executable
```
printf '#!/usr/bin/env python3\nprint("hello")\n' > script.py
chmod +x script.py
```

### Copy an existing file as a template
```
cp source.txt destination.txt
```

## Notes
- Use `printf` for all file writes — heredoc breaks the XML parser
- After writing always verify with: `cat filename`
- Check available disk space with `df -h .` before writing large files


[[overview]]


---

## Connections (skill back-link)
- [[internals/skills-and-mods]]
