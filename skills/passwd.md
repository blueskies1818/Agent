# passwd — Session-scoped credential manager

Use this skill when you need to log into services, pass API tokens, or supply
passwords to commands.  Credentials are stored in RAM only — never written to
disk, logs, memory, or embeddings.

---

## The <<NAME>> placeholder

Use `<<CREDENTIAL_NAME>>` anywhere in a shell command or debug_ui action.
The framework substitutes the real value before execution.
You only ever write the placeholder — the value is invisible to you.

```bash
# API call with token
curl -H "Authorization: Bearer <<GITHUB_TOKEN>>" https://api.github.com/user

# GUI login — type password into focused field
debug_ui -type <<GMAIL_PASSWORD>>

# Any shell command
ssh-keygen -p -f ~/.ssh/id_rsa -N <<SSH_PASSPHRASE>>
```

If a placeholder is unknown (not in cache), the command runs with the literal
text `<<NAME>>` so the error is visible rather than silently wrong.

---

## Commands

```
passwd -set <NAME> <value>   Store a credential in the session cache
passwd -load                 Load all credentials from the .passwd file
passwd -list                 Show what names are cached (values never shown)
passwd -clear <NAME>         Remove one credential
passwd -clear-all            Wipe the entire cache
```

---

## Typical workflow

### One-off credential
```
passwd -set GITHUB_TOKEN ghp_xxxxxxxxxxxx
curl -H "Authorization: Bearer <<GITHUB_TOKEN>>" https://api.github.com/user
```

### Load from file (for repeated sessions)
The user maintains a `.passwd` file at the project root (gitignored).
```
passwd -load
passwd -list    ← confirm what loaded
```

### GUI login
```
passwd -set GMAIL_PASSWORD mypassword
debug_ui -start "firefox https://mail.google.com"
debug_ui -click 640 400     ← click the password field
debug_ui -type <<GMAIL_PASSWORD>>
debug_ui -key Return
```

---

## Security properties

- Values exist only in RAM — cleared automatically when the session ends
- Values are scrubbed from all command output before you see it
- Values are never pushed to the context window, logs, or memory
- `passwd -list` shows names only — values are never revealed
- The `.passwd` file is gitignored and never read by you directly
