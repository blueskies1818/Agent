"""
mods/web_search/web_search_tool.py
----------------------------------
Web search + HTML parsing engine.  Fetches pages, cleans HTML, scores
chunks by relevance, and returns focused context.

This file is an internal dependency of the web_search mod.  It can also
be run standalone from the command line:

    python -m mods.web_search.web_search_tool "your search query"

Dependencies:
    pip install requests beautifulsoup4 duckduckgo-search

Optional (for semantic similarity scoring):
    pip install sentence-transformers scikit-learn
"""

import re
import time
import argparse
import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_NUM_SOURCES   = 3
DEFAULT_TOP_CHUNKS    = 3
CHUNK_WORD_SIZE       = 300
MAX_CHARS_PER_SOURCE  = 4000
REQUEST_DELAY         = 1.0
REQUEST_TIMEOUT       = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

NOISE_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "form", "noscript", "iframe", "svg",
    "button", "input", "select", "textarea", "label",
]


# ---------------------------------------------------------------------------
# Step 1: Search → list of URLs
# ---------------------------------------------------------------------------

def search_urls(query: str, num_results: int = DEFAULT_NUM_SOURCES) -> list[str]:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
            return [r["href"] for r in results if "href" in r]
    except ImportError:
        print("[warn] duckduckgo-search not installed. Run: pip install duckduckgo-search")
        return []
    except Exception as e:
        print(f"[warn] Search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Step 2: Fetch a URL → raw HTML
# ---------------------------------------------------------------------------

def fetch_html_requests(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type:
            return None
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException:
        return None


def fetch_html_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                html = page.content()
            except PWTimeout:
                html = page.content()
            finally:
                browser.close()
            return html
    except ImportError:
        return None
    except Exception:
        return None


def fetch_html(url: str) -> str | None:
    html = fetch_html_requests(url)

    if html and len(html) > 500:
        return html

    reason = "short response" if html else "failed request"
    print(f"[retry]  requests got {reason} for {url.split('/')[2]}, trying Playwright...")
    pw_html = fetch_html_playwright(url)

    if pw_html and len(pw_html) > 500:
        return pw_html

    if html:
        return html
    if pw_html:
        return pw_html

    print(f"[warn]   Could not fetch {url}")
    return None


# ---------------------------------------------------------------------------
# Step 3: Parse HTML → clean plain text
# ---------------------------------------------------------------------------

def parse_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(NOISE_TAGS):
        tag.decompose()

    main_content = (
        soup.find("article")            or
        soup.find("main")               or
        soup.find(id="content")         or
        soup.find(id="main-content")    or
        soup.find(class_="post-body")   or
        soup.find(class_="entry-content") or
        soup.find(class_="article-body") or
        soup.body                       or
        soup
    )

    raw_text = main_content.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    lines = [l for l in lines if len(l) > 30]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 4: Chunk the text
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_WORD_SIZE) -> list[str]:
    words = text.split()
    if not words:
        return []

    overlap = chunk_size // 5
    step    = chunk_size - overlap
    chunks  = []

    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Step 5: Score chunks
# ---------------------------------------------------------------------------

def score_chunks_simple(chunks: list[str], query: str) -> list[tuple[float, str]]:
    stopwords = {
        "a", "an", "the", "is", "are", "was", "were", "in", "on",
        "at", "to", "of", "for", "and", "or", "but", "with", "how",
        "what", "when", "where", "who", "why", "do", "does", "did",
        "it", "its", "this", "that", "i", "my", "me", "we", "our",
    }
    query_words = {
        w.lower() for w in re.findall(r"\w+", query)
        if w.lower() not in stopwords
    }

    scored = []
    for chunk in chunks:
        chunk_words = {w.lower() for w in re.findall(r"\w+", chunk)}
        hits  = len(query_words & chunk_words)
        score = hits / max(len(query_words), 1)
        scored.append((score, chunk))

    return sorted(scored, reverse=True)


def score_chunks_semantic(chunks: list[str], query: str) -> list[tuple[float, str]]:
    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        print("[warn] sentence-transformers or scikit-learn not installed, "
              "falling back to keyword scoring.")
        return score_chunks_simple(chunks, query)

    model      = SentenceTransformer("all-MiniLM-L6-v2")
    query_vec  = model.encode([query])
    chunk_vecs = model.encode(chunks)
    scores     = cosine_similarity(query_vec, chunk_vecs)[0]

    return sorted(zip(scores.tolist(), chunks), reverse=True)


def get_relevant_chunks(
    text:      str,
    query:     str,
    top_k:     int  = DEFAULT_TOP_CHUNKS,
    semantic:  bool = False,
) -> list[str]:
    chunks = chunk_text(text)
    if not chunks:
        return []

    if semantic:
        scored = score_chunks_semantic(chunks, query)
    else:
        scored = score_chunks_simple(chunks, query)

    relevant = [chunk for score, chunk in scored[:top_k] if score > 0]
    return relevant or [scored[0][1]]


# ---------------------------------------------------------------------------
# Step 6: Put it all together
# ---------------------------------------------------------------------------

def scrape_url(url: str, query: str, semantic: bool = False) -> str | None:
    html = fetch_html(url)
    if not html:
        return None

    text = parse_html(html)
    if len(text) < 200:
        return None

    chunks   = get_relevant_chunks(text, query, semantic=semantic)
    combined = "\n\n---\n\n".join(chunks)

    return combined[:MAX_CHARS_PER_SOURCE]


def web_search(
    query:       str,
    num_sources: int  = DEFAULT_NUM_SOURCES,
    semantic:    bool = False,
) -> str:
    print(f"[search] Query: {query!r}")
    urls = search_urls(query, num_results=num_sources)

    if not urls:
        return "No search results found."

    sections = []
    for i, url in enumerate(urls, 1):
        print(f"[fetch]  ({i}/{len(urls)}) {url}")
        excerpt = scrape_url(url, query, semantic=semantic)

        if excerpt:
            sections.append(f"## Source {i}: {url}\n\n{excerpt}")
        else:
            print(f"[skip]   No usable content at {url}")

        if i < len(urls):
            time.sleep(REQUEST_DELAY)

    if not sections:
        return "Could not extract content from any search results."

    body = ("\n\n" + "=" * 60 + "\n\n").join(sections)

    try:
        import tiktoken
        enc         = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(body))
        method      = "exact"
    except ImportError:
        token_count = len(body) // 4
        method      = "~estimated"

    divider = "=" * 60
    header  = (
        f"{divider}\n"
        f"  tokens : {token_count:,} ({method})\n"
        f"  sources: {len(sections)}\n"
        f"  query  : {query!r}\n"
        f"{divider}\n\n"
    )

    return header + body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search the web and return relevant text chunks."
    )
    parser.add_argument("query",          help="Search query")
    parser.add_argument("--sources",      type=int, default=DEFAULT_NUM_SOURCES,
                        help=f"Number of pages to fetch (default: {DEFAULT_NUM_SOURCES})")
    parser.add_argument("--semantic",     action="store_true",
                        help="Use semantic similarity scoring (requires sentence-transformers)")
    args = parser.parse_args()

    print(web_search(args.query, num_sources=args.sources, semantic=args.semantic))