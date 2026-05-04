"""
Fetches psychology content from gottman.com, cnvc.org, simplypsychology.org,
and positivepsychology.com into data/psychology/, then chunks, embeds, and
loads into the 'psychology_kb' ChromaDB collection.

Note: apa.org was the original target but is blocked by Incapsula bot
protection; positivepsychology.com is used instead for the apa_conflict/
directory, covering equivalent conflict-resolution content.

Fetch step is idempotent: if a source directory already has .txt files,
scraping is skipped for that source. Delete the directory to re-fetch.

Run from the project root:
    backend/.venv/Scripts/python.exe scripts/load_psychology.py
"""

import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to path so backend.rag can be imported.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import requests
from bs4 import BeautifulSoup

from backend.rag.embeddings import (
    chunk_documents,
    embed_documents,
    embed_query,
    load_collection,
    CHROMA_DB_DIR,
)

PSYCHOLOGY_DATA_DIR = ROOT / "data" / "psychology"
COLLECTION_NAME = "psychology_kb"

REQUEST_DELAY = 1.2   # seconds between requests — be polite
MIN_TEXT_LENGTH = 300  # discard pages shorter than this

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── target URLs (all verified reachable) ───────────────────────────────────────
SOURCES: dict[str, list[str]] = {
    "gottman_articles": [
        "https://www.gottman.com/blog/the-four-horsemen-recognizing-criticism-contempt-defensiveness-and-stonewalling/",
        "https://www.gottman.com/blog/the-four-horsemen-the-antidotes/",
        "https://www.gottman.com/blog/repair-attempts/",
        "https://www.gottman.com/blog/managing-vs-resolving-conflict/",
        "https://www.gottman.com/blog/the-positive-perspective/",
        "https://www.gottman.com/blog/the-myth-of-being-too-needy-in-relationships/",
    ],
    "nvc_framework": [
        "https://www.cnvc.org/learn/what-is-nvc",
        "https://www.cnvc.org/learn/research",
        "https://www.cnvc.org/news/lesson-1-of-5-powerful-nvc-micro-lessons-2026-relationship-reset-series",
        "https://www.cnvc.org/news/lesson-2-of-5-powerful-nvc-micro-lessons-2026-relationship-reset-series",
        "https://www.cnvc.org/news/lesson-3-of-5-powerful-nvc-micro-lessons-2026-relationship-reset-series",
        "https://www.cnvc.org/news/lesson-4-of-5-powerful-nvc-micro-lessons-2026-relationship-reset-series",
        "https://www.cnvc.org/news/lesson-5-of-5-powerful-nvc-micro-lessons-2026-relationship-reset-series",
    ],
    "attachment_theory": [
        "https://www.simplypsychology.org/attachment.html",
        "https://www.simplypsychology.org/attachment-theory.html",
        "https://www.simplypsychology.org/bowlby.html",
        "https://www.simplypsychology.org/adult-attachment.html",
        "https://www.simplypsychology.org/mary-ainsworth.html",
        "https://www.simplypsychology.org/conflict.html",
        "https://www.simplypsychology.org/love.html",
    ],
    # apa.org is behind Incapsula bot protection — replaced with
    # positivepsychology.com, which covers equivalent conflict-resolution content.
    "apa_conflict": [
        "https://positivepsychology.com/active-listening/",
        "https://positivepsychology.com/emotion-regulation/",
        "https://positivepsychology.com/empathy/",
        "https://positivepsychology.com/attachment-theory/",
        "https://positivepsychology.com/assertive-communication/",
        "https://positivepsychology.com/defense-mechanisms/",
    ],
}

# ── HTML extraction ─────────────────────────────────────────────────────────────

# Selectors tried in order; first match above MIN_TEXT_LENGTH wins.
CONTENT_SELECTORS = [
    "article",
    "main",
    '[class*="entry-content"]',
    '[class*="post-content"]',
    '[class*="article-content"]',
    '[class*="field-body"]',
    '[class*="field--body"]',
    '[class*="content-body"]',
    '[class*="page-content"]',
    ".content",
    "#content",
    "#post-content",
    ".container",
]

NOISE_TAGS = [
    "script", "style", "noscript", "header", "footer",
    "nav", "aside", "form", "button", "iframe", "figure",
]


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()

    text = ""
    for selector in CONTENT_SELECTORS:
        element = soup.select_one(selector)
        if element:
            candidate = element.get_text(separator=" ", strip=True)
            if len(candidate) >= MIN_TEXT_LENGTH:
                text = candidate
                break

    if not text:
        body = soup.find("body")
        if body:
            text = body.get_text(separator=" ", strip=True)

    return re.sub(r"\s{2,}", " ", text).strip()


def fetch_url(url: str, session: requests.Session) -> str | None:
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        text = extract_text(resp.text)
        if len(text) < MIN_TEXT_LENGTH:
            print(f"    [warn] content too short ({len(text)} chars) — {url}")
            return None
        return text
    except Exception as exc:
        print(f"    [warn] fetch failed — {url} — {exc}")
        return None


# ── fetch step ─────────────────────────────────────────────────────────────────

def fetch_source(source_name: str, urls: list[str], session: requests.Session) -> int:
    out_dir = PSYCHOLOGY_DATA_DIR / source_name
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = list(out_dir.glob("*.txt"))
    if existing:
        print(f"  {source_name}/  already has {len(existing)} file(s) — skipping fetch")
        return len(existing)

    saved = 0
    for i, url in enumerate(urls, start=1):
        print(f"  [{i}/{len(urls)}] {url}")
        text = fetch_url(url, session)
        if text:
            slug = re.sub(r"[^\w\-]", "_", url.split("//", 1)[-1])[:80]
            filename = out_dir / f"{i:02d}_{slug}.txt"
            filename.write_text(text, encoding="utf-8")
            print(f"    saved {len(text)} chars → {filename.name}")
            saved += 1
        time.sleep(REQUEST_DELAY)

    return saved


def fetch_all() -> int:
    session = requests.Session()
    session.headers.update(HEADERS)
    total = 0
    for source_name, urls in SOURCES.items():
        print(f"\nFetching {source_name} ({len(urls)} URLs)...")
        total += fetch_source(source_name, urls, session)
    return total


# ── read step ──────────────────────────────────────────────────────────────────

def read_all_files() -> list[dict]:
    documents = []
    for source_dir in sorted(PSYCHOLOGY_DATA_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        txt_files = sorted(source_dir.glob("*.txt"))
        for path in txt_files:
            text = path.read_text(encoding="utf-8-sig").strip()
            if text:
                documents.append(
                    {"text": text, "source": source_dir.name, "filename": path.name}
                )
        print(f"  read {len(txt_files)} files from {source_dir.name}/")
    return documents


# ── main ───────────────────────────────────────────────────────────────────────

def load_psychology():
    print("=== load_psychology.py ===\n")

    # 1. Fetch
    print("── Step 1: Fetch ──────────────────────────────────────────")
    total_files = fetch_all()
    print(f"\nTotal files on disk: {total_files}")

    # 2. Read
    print("\n── Step 2: Read from disk ─────────────────────────────────")
    documents = read_all_files()
    if not documents:
        print("No documents found under data/psychology/. Aborting.")
        sys.exit(1)
    print(f"  total documents: {len(documents)}")

    # 3. Chunk
    print("\n── Step 3: Chunk ──────────────────────────────────────────")
    texts, metadatas = chunk_documents(documents)
    print(f"  total chunks: {len(texts)}")

    # 4. Embed
    print("\n── Step 4: Embed ──────────────────────────────────────────")
    print("  embedding via Vertex AI text-embedding-004...")
    embeddings = embed_documents(texts)
    print(f"  generated {len(embeddings)} embeddings")

    # 5. Load into ChromaDB
    print("\n── Step 5: Load into ChromaDB ─────────────────────────────")
    collection = load_collection(COLLECTION_NAME, texts, metadatas, embeddings)

    # 6. Smoke test
    print("\n── Step 6: Smoke test ─────────────────────────────────────")
    smoke_queries = [
        "Four Horsemen criticism contempt defensiveness stonewalling",
        "nonviolent communication feelings and needs",
        "anxious avoidant attachment style relationship",
    ]
    for query in smoke_queries:
        print(f"\n  query: '{query}'")
        results = collection.query(
            query_embeddings=[embed_query(query)],
            n_results=2,
            include=["documents", "metadatas", "distances"],
        )
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            print(f"    [{meta['source']}/{meta['filename']}  d={dist:.4f}]")
            print(f"    {doc[:100].strip()}...")

    print(f"\nDone. ChromaDB persisted at: {CHROMA_DB_DIR}")


if __name__ == "__main__":
    load_psychology()
