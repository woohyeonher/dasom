"""
Shared chunking, embedding, and ChromaDB ingestion logic for the Dasom RAG pipeline.

Imported by:
  scripts/load_reddit.py    → populates 'reddit_examples' collection
  scripts/load_psychology.py → populates 'psychology_kb' collection
  backend/rag/retriever.py  → reads from both collections at query time
"""

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from google import genai
from google.genai import types as genai_types
import chromadb

# ── env & GCP setup ────────────────────────────────────────────────────────────
# Three parents up: backend/rag/embeddings.py → backend/rag/ → backend/ → root
_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_ROOT / ".env")

_GCP_PROJECT = os.environ["GCP_PROJECT_ID"]
_GCP_LOCATION = os.environ["GCP_LOCATION"]

# Single genai client for the lifetime of the process.
_genai_client = genai.Client(
    vertexai=True, project=_GCP_PROJECT, location=_GCP_LOCATION
)

# ── constants ──────────────────────────────────────────────────────────────────
CHROMA_DB_DIR: Path = _ROOT / "backend" / "chroma_db"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

EMBED_MODEL = "text-embedding-004"
# 50 chunks × ~200 tokens/chunk ≈ 10K tokens — comfortably under the 20K limit.
_EMBED_BATCH = 50


# ── public API ─────────────────────────────────────────────────────────────────

def chunk_documents(documents: list[dict]) -> tuple[list[str], list[dict]]:
    """
    Split a list of document dicts into fixed-size text chunks.

    Each dict must have keys: 'text', 'source', 'filename'.
    Returns parallel (texts, metadatas) lists ready for embed_documents().
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    texts: list[str] = []
    metadatas: list[dict] = []
    for doc in documents:
        for chunk in splitter.split_text(doc["text"]):
            texts.append(chunk)
            metadatas.append({"source": doc["source"], "filename": doc["filename"]})
    return texts, metadatas


def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of document strings using Vertex AI text-embedding-004.

    Uses RETRIEVAL_DOCUMENT task type. Batches internally to stay under the
    Vertex AI 20K token-per-call limit.
    """
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        batch = texts[start : start + _EMBED_BATCH]
        response = _genai_client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        embeddings.extend([e.values for e in response.embeddings])
    return embeddings


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string using Vertex AI text-embedding-004.

    Uses RETRIEVAL_QUERY task type, which is asymmetric to RETRIEVAL_DOCUMENT
    and yields better retrieval performance.
    """
    response = _genai_client.models.embed_content(
        model=EMBED_MODEL,
        contents=[query],
        config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return response.embeddings[0].values


def load_collection(
    collection_name: str,
    texts: list[str],
    metadatas: list[dict],
    embeddings: list[list[float]],
) -> chromadb.Collection:
    """
    Drop-and-recreate a ChromaDB collection and insert all chunks.

    Always does a fresh load — call this after embed_documents().
    Returns the live collection object for immediate querying (e.g. smoke tests).
    """
    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))

    try:
        client.delete_collection(collection_name)
        print(f"  deleted existing '{collection_name}' (fresh load)")
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    collection.add(
        ids=[str(uuid.uuid4()) for _ in texts],
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    print(f"  inserted {len(texts)} chunks into '{collection_name}'")
    return collection
