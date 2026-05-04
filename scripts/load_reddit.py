"""
Reads all .txt files from data/reddit/ and loads them into the
'reddit_examples' ChromaDB collection using Vertex AI embeddings.

Each .txt file is one Reddit post body (no title, no metadata).
Subforums expected: relationship_advice/, aitah/

Run from the project root:
    backend/.venv/Scripts/python.exe scripts/load_reddit.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to path so backend.rag can be imported.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backend.rag.embeddings import (
    chunk_documents,
    embed_documents,
    embed_query,
    load_collection,
    CHROMA_DB_DIR,
)

REDDIT_DATA_DIR = ROOT / "data" / "reddit"
COLLECTION_NAME = "reddit_examples"
SUBFORUMS = ["relationship_advice", "aitah"]


def read_posts() -> list[dict]:
    """Return list of {text, source, filename} dicts from all subforum dirs."""
    documents = []
    for subforum in SUBFORUMS:
        subforum_dir = REDDIT_DATA_DIR / subforum
        if not subforum_dir.exists():
            print(f"  [warn] {subforum_dir} not found — skipping")
            continue
        txt_files = sorted(subforum_dir.glob("*.txt"))
        if not txt_files:
            print(f"  [warn] no .txt files in {subforum_dir} — skipping")
            continue
        for path in txt_files:
            text = path.read_text(encoding="utf-8-sig").strip()
            if text:
                documents.append(
                    {"text": text, "source": subforum, "filename": path.name}
                )
        print(f"  read {len(txt_files)} files from {subforum}/")
    return documents


def load_reddit():
    print("=== load_reddit.py ===\n")

    # 1. Read
    print("Reading source files...")
    documents = read_posts()
    if not documents:
        print("No documents found. Add .txt files to data/reddit/ subfolders.")
        sys.exit(1)
    print(f"  total posts: {len(documents)}\n")

    # 2. Chunk
    print("Chunking...")
    texts, metadatas = chunk_documents(documents)
    print(f"  total chunks: {len(texts)}\n")

    # 3. Embed
    print("Generating embeddings via Vertex AI (text-embedding-004)...")
    embeddings = embed_documents(texts)
    print(f"  generated {len(embeddings)} embeddings\n")

    # 4. Load into ChromaDB
    print(f"Loading into ChromaDB collection '{COLLECTION_NAME}'...")
    collection = load_collection(COLLECTION_NAME, texts, metadatas, embeddings)
    print()

    # 5. Smoke test
    print("Smoke test — querying for 'partner forgot anniversary'...")
    query_vec = embed_query("partner forgot anniversary")
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=3,
        include=["documents", "metadatas", "distances"],
    )
    for i, (doc, meta, dist) in enumerate(
        zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ),
        start=1,
    ):
        print(f"  [{i}] {meta['source']}/{meta['filename']}  distance={dist:.4f}")
        print(f"       {doc[:120].strip()}...")

    print(f"\nDone. ChromaDB persisted at: {CHROMA_DB_DIR}")


if __name__ == "__main__":
    load_reddit()
