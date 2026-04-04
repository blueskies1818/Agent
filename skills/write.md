---
description: Create new files and directory structures.
keywords: write, create, make, new file, generate, save, output, produce, touch, scaffold
---

# Write — create new files and directories

Use shell commands to create files and directory structures.

## Commands

### Write a file (overwrites if exists)
```
cat > path/to/file.txt << 'EOF'
line one
line two
EOF
```

### Append to a file
```
cat >> path/to/file.txt << 'EOF'
additional content
EOF
```

### Create a directory (including parents)
```
mkdir -p path/to/nested/dir
```

### Write a file with printf (good for single lines)
```
printf 'Hello, world!\n' > path/to/file.txt
```

### Write a Python / shell script and make it executable
```
cat > script.py << 'EOF'
#!/usr/bin/env python3
print("hello")
EOF
chmod +x script.py
```

### Copy an existing file as a template
```
cp source.txt destination.txt
```

## Notes
- Always use `'EOF'` (quoted) in heredocs to prevent variable expansion unless you specifically need it
- Check available disk space with `df -h .` before writing large files
- After writing, verify with `cat path/to/file.txt` to confirm content is correct
