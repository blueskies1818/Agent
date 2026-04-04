---
description: Remove files and directories safely.
---

# Delete — remove files and directories safely

Use shell commands to remove files and directories.

## Commands

### Delete a single file
```
rm path/to/file.txt
```

### Delete multiple files
```
rm path/to/file1.txt path/to/file2.txt
```

### Delete files matching a pattern
```
rm path/to/dir/*.log
```

### Delete an empty directory
```
rmdir path/to/empty_dir/
```

### Delete a directory and all its contents (DESTRUCTIVE)
```
rm -rf path/to/dir/
```

### Preview what would be deleted (dry run with find)
```
find path/to/dir/ -name "*.tmp" -type f
```
Then delete after confirming:
```
find path/to/dir/ -name "*.tmp" -type f -delete
```

### Move to trash instead of hard delete (safer)
```
mv path/to/file.txt path/to/file.txt.bak
```

## Safety rules
1. **Never run `rm -rf` without first listing what will be deleted**
2. Use the read skill to inspect directories before bulk deletion
3. When uncertain, rename/backup instead of deleting (`mv file file.bak`)
4. There is no recycle bin — deletions are permanent

## Verification
After deleting, confirm with:
```
ls path/to/dir/
```
or check a file no longer exists:
```
test -f path/to/file.txt && echo "still exists" || echo "deleted"
```
