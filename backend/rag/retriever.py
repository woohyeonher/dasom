"""
Query interface for both RAG collections.

Public API:
    retrieve_psychology(query, n_results=3) -> list[dict]
    retrieve_examples(query, n_results=3)   -> list[dict]

Each function returns a list of dicts with keys:
    text     – the retrieved chunk text
    source   – provenance string  "<source_dir>/<filename>"
    distance – cosine distance (lower = more similar)

The collections must already exist on disk.
Run scripts/build_vectorstore.py first if they don't.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so that `backend.*` resolves correctly
# both when this module is imported by FastAPI and when run directly.
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import chromadb

from backend.rag.embeddings import CHROMA_DB_DIR, embed_query

_REDDIT_COLLECTION = "reddit_examples"
_PSYCH_COLLECTION = "psychology_kb"

# Lazy singleton — opened on first retrieval call, reused for the process lifetime.
# Not initialized at import time so that importing this module never fails even
# if the vector store hasn't been built yet.
_client: chromadb.PersistentClient | None = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        if not CHROMA_DB_DIR.exists():
            raise RuntimeError(
                f"ChromaDB directory not found at {CHROMA_DB_DIR}. "
                "Run:  backend/.venv/Scripts/python.exe scripts/build_vectorstore.py"
            )
        _client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    return _client


def _retrieve(collection_name: str, query: str, n_results: int) -> list[dict]:
    client = _get_client()
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        raise RuntimeError(
            f"Collection '{collection_name}' not found in {CHROMA_DB_DIR}. "
            "Run:  backend/.venv/Scripts/python.exe scripts/build_vectorstore.py"
        )

    # Cap n_results to the actual collection size to avoid a ChromaDB error
    # when the collection has fewer documents than requested.
    actual_n = min(n_results, collection.count())
    if actual_n == 0:
        return []

    results = collection.query(
        query_embeddings=[embed_query(query)],
        n_results=actual_n,
        include=["documents", "metadatas", "distances"],
    )

    return [
        {
            "text": doc,
            "source": f"{meta['source']}/{meta['filename']}",
            "distance": dist,
        }
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]


def retrieve_psychology(query: str, n_results: int = 3) -> list[dict]:
    """
    Retrieve the top n chunks from the psychology_kb collection.

    Best used for: grounding mediator synthesis, emotion-agent context,
    NVC/attachment/Gottman framework lookups.
    """
    return _retrieve(_PSYCH_COLLECTION, query, n_results)


def retrieve_examples(query: str, n_results: int = 3) -> list[dict]:
    """
    Retrieve the top n chunks from the reddit_examples collection.

    Best used for: few-shot examples for emotion agents and persona agents,
    grounding conflict narratives in realistic relationship language.
    """
    return _retrieve(_REDDIT_COLLECTION, query, n_results)


# ── manual test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    TEST_CASES = [
        # (function, label, query)
        (retrieve_psychology, "psychology_kb", "Four Horsemen contempt stonewalling relationship conflict"),
        (retrieve_psychology, "psychology_kb", "nonviolent communication feelings needs empathy"),
        (retrieve_psychology, "psychology_kb", "anxious avoidant attachment style adult"),
        (retrieve_examples,   "reddit_examples", "partner never listens during arguments"),
        (retrieve_examples,   "reddit_examples", "feeling unappreciated and taken for granted"),
    ]

    for fn, label, query in TEST_CASES:
        print(f"\n{'─' * 64}")
        print(f"[{label}]  query: '{query}'")
        print("─" * 64)
        hits = fn(query, n_results=3)
        for i, hit in enumerate(hits, 1):
            print(f"  {i}. distance={hit['distance']:.4f}  source={hit['source']}")
            print(f"     {hit['text'][:120].strip()}...")
