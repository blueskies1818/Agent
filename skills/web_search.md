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

## Examples

User asks "what's new in the latest Python release":
```
search_web -query "Python latest release new features 2025"
```

User asks about a library:
```
search_web -query "polars dataframe library getting started"
```

User shares a link:
```
search_web -url "https://example.com/article"
```