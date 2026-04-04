---
description: Make precise targeted edits to existing files without rewriting them.
---

# Edit — modify existing files in place

Use shell commands to make precise edits without rewriting entire files.

## Commands

### Replace one occurrence of a string (sed)
```
sed -i 's/old text/new text/' path/to/file.txt
```

### Replace ALL occurrences in a file
```
sed -i 's/old text/new text/g' path/to/file.txt
```

### Replace on a specific line number
```
sed -i '5s/.*/replacement line content/' path/to/file.txt
```

### Delete a specific line
```
sed -i '10d' path/to/file.txt
```

### Delete lines matching a pattern
```
sed -i '/pattern to delete/d' path/to/file.txt
```

### Insert a line after a match
```
sed -i '/match this/a\new line goes here' path/to/file.txt
```

### Insert a line before a match
```
sed -i '/match this/i\new line goes here' path/to/file.txt
```

### Multi-file find and replace
```
grep -rl "old text" path/to/dir/ | xargs sed -i 's/old text/new text/g'
```

## Verification workflow
Always verify edits landed correctly:
```
grep -n "new text" path/to/file.txt
```
or
```
sed -n '3,8p' path/to/file.txt
```

## Notes
- On macOS, `sed -i` requires an empty string argument: `sed -i '' 's/old/new/' file`
- For complex edits (multiple lines, indentation), prefer rewriting the full file using the write skill
- Always read the file first (use the read skill) to know exact content before editing
