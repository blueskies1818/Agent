# Read — view files and directory contents

Use shell commands to read files and explore the filesystem.

## Commands

### View a file
```
cat path/to/file.txt
```

### View with line numbers (helpful for editing)
```
cat -n path/to/file.txt
```

### View a specific range of lines
```
sed -n '10,30p' path/to/file.txt
```

### List directory contents
```
ls -la path/to/dir/
```

### Recursive directory tree
```
find path/to/dir -type f | sort
```

### Search inside files
```
grep -rn "search term" path/to/dir/
```

### Read large files page by page (first N lines)
```
head -n 50 path/to/file.txt
```
```
tail -n 50 path/to/file.txt
```

## Notes
- All paths are relative to your sandbox root unless they start with `/`
- Binary files (images, compiled code) will produce unreadable output — use `file` to check type first
- Use `wc -l path/to/file` to check file size before reading large files
