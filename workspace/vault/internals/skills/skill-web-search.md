---
name:        web_search
description: Search the internet or fetch a URL for current information
tags:        search, google, look up, look online, find online, web search, search the web, latest, current, recent news, how to, documentation
tier:        global
status:      active
created_at:  2026-04-01
author:      user
uses:        0
---

# Web Search — search the internet for current information

Use shell commands to search the web, fetch pages, and get relevant text excerpts.
Results are scored for relevance and returned as focused chunks ready to answer questions.

## Commands

### Search the web (default: 3 sources)
```
search_web -query "Python 3.13 new features"
```

### Search with more sources (for deeper research)
```
search_web -query "FastAPI vs Django comparison" -sources 5
```

### Fetch and read a specific URL
```
search_web -url "https://docs.python.org/3/library/asyncio.html"
```

## When to use
- User asks about something you don't know or aren't sure about
- User asks for current or recent information (news, releases, prices)
- User needs documentation, tutorials, or reference material
- You need to verify a fact before acting on it
- User explicitly asks you to search, look up, or find something

## What you get back
The search returns relevant text excerpts from the top web pages:
```
============================================================
  tokens : 2,140 (~estimated)
  sources: 3
  query  : 'Python 3.13 new features'
============================================================

## Source 1: https://docs.python.org/3/whatsnew/3.13.html
(relevant text chunks)

## Source 2: https://realpython.com/python313-new-features/
(relevant text chunks)
```

## Tips
- Write queries like you would type into Google — short and specific
- Use 3 sources for quick lookups, 5 for deeper research
- After getting results, summarise the answer — don't dump raw text at the user
- If results are insufficient, rephrase the query and try again
- Use `-url` when the user gives you a specific link to read
- Cite which source(s) your answer came from when relevant


[[overview]]


---

## Connections (skill back-link)
- [[internals/skills-and-mods]]
