"""
Vertex AI model router for Dasom.

Public API
──────────
call_gemini_flash(messages, system_prompt)   async generator → str tokens
call_gemini_pro(messages, system_prompt)     async generator → str tokens
call_llama_scout(messages, system_prompt)    async generator → str tokens
summarize_reasoning(reasoning_text)          async → str  (non-streaming)

`messages` is a list of dicts with keys "role" ("user" | "assistant")
and "content" (str).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import json
import time

import google.auth
import google.auth.transport.requests
from collections.abc import AsyncGenerator

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Imported here so router.py is the only place that calls LLMs;
# prompts.py is the only place that defines prompt text.
from backend.utils.prompts import REASONING_SUMMARIZER_PROMPT  # noqa: E402

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

_GCP_PROJECT = os.environ["GCP_PROJECT_ID"]
_GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
_FLASH_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
_PRO_MODEL = os.environ.get("GEMINI_PRO_MODEL", "gemini-2.5-pro")
_LLAMA_MODEL = os.environ.get("LLAMA_SCOUT_MODEL", "llama-4-scout-17b-16e-instruct-maas")
_LLAMA_REGION = os.environ.get("LLAMA_REGION", "us-east5")

# ── Clients ────────────────────────────────────────────────────────────────────

# Gemini models — google.genai SDK in Vertex AI mode (same SDK used for embeddings)
_client = genai.Client(vertexai=True, project=_GCP_PROJECT, location=_GCP_LOCATION)

# Llama Scout MaaS: OpenAI-compatible endpoint in us-east5, v1 API path.
# Path uses "openapi" (not "openai"); model name in the request body must
# include the "meta/" publisher prefix.
# Auth via Application Default Credentials (cached for ~55 min).
_LLAMA_URL = (
    f"https://{_LLAMA_REGION}-aiplatform.googleapis.com/v1"
    f"/projects/{_GCP_PROJECT}/locations/{_LLAMA_REGION}"
    "/endpoints/openapi/chat/completions"
)
# Ensure publisher prefix is present regardless of how the env var was set.
_LLAMA_API_MODEL = (
    _LLAMA_MODEL if _LLAMA_MODEL.startswith("meta/") else f"meta/{_LLAMA_MODEL}"
)
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_vertex_token() -> str:
    """Return a cached bearer token via Application Default Credentials.

    Works locally (user credentials via `gcloud auth application-default login`)
    and on Cloud Run (attached service account via the GCE metadata server).
    """
    if _token_cache["token"] and time.time() < float(_token_cache["expires_at"]) - 60:
        return str(_token_cache["token"])
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    token = credentials.token
    expires_at = (
        credentials.expiry.timestamp()
        if credentials.expiry
        else time.time() + 3300
    )
    _token_cache["token"] = token
    _token_cache["expires_at"] = expires_at
    return token

# ── Message conversion ─────────────────────────────────────────────────────────


def _to_contents(messages: list[dict]) -> list[types.Content]:
    result = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        result.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )
    return result


# ── Internal streaming helper (shared by all three models) ────────────────────


async def _stream_buffered(
    model: str, messages: list[dict], system_prompt: str, max_retries: int = 5
) -> list[str]:
    """
    Single call to a Gemini model. Buffers all tokens and retries on 429.
    Returns a list of token strings.
    """
    import asyncio as _asyncio
    from google.genai.errors import ClientError

    _BACKOFF = [60, 60, 90, 120, 120]

    for attempt in range(max_retries + 1):
        try:
            contents = _to_contents(messages)
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
            )
            stream = await _client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            )
            tokens: list[str] = []
            async for chunk in stream:
                if chunk.text:
                    tokens.append(chunk.text)
            return tokens
        except ClientError as exc:
            if "429" in str(exc) and attempt < max_retries:
                wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                print(
                    f"[Gemini {model} 429] retry {attempt + 1}/{max_retries} in {wait}s",
                    flush=True,
                )
                await _asyncio.sleep(wait)
            else:
                raise
    return []  # unreachable


# ── Public streaming functions ─────────────────────────────────────────────────


async def call_gemini_flash(
    messages: list[dict], system_prompt: str
) -> AsyncGenerator[str, None]:
    """Stream tokens from Gemini 2.5 Flash (emotion agents). Retries on 429."""
    for token in await _stream_buffered(_FLASH_MODEL, messages, system_prompt):
        yield token


async def _call_gemini_flash_thinking_once(
    messages: list[dict], system_prompt: str
) -> list[dict]:
    """Single (non-retried) call to Gemini Flash Thinking. Returns all chunks as a list."""
    contents = _to_contents(messages)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=1.0,
        thinking_config=types.ThinkingConfig(include_thoughts=True),
    )
    stream = await _client.aio.models.generate_content_stream(
        model=_FLASH_MODEL,
        contents=contents,
        config=config,
    )
    collected: list[dict] = []
    async for chunk in stream:
        if not chunk.candidates:
            continue
        try:
            parts = chunk.candidates[0].content.parts or []
        except AttributeError:
            continue
        for part in parts:
            if not part.text:
                continue
            if getattr(part, "thought", False):
                collected.append({"type": "thinking", "content": part.text})
            else:
                collected.append({"type": "text", "content": part.text})
    return collected


async def call_gemini_flash_thinking(
    messages: list[dict], system_prompt: str, max_retries: int = 5
) -> AsyncGenerator[dict, None]:
    """
    Stream tokens from Gemini 2.5 Flash with thinking enabled.
    Yields dicts: {"type": "thinking"|"text", "content": str}

    Thinking parts (type="thinking") carry the model's internal reasoning.
    Text parts (type="text") carry the visible response.
    If no thinking tokens are produced, only text dicts are yielded.

    Buffers all chunks before yielding so that 429 rate-limit retries are safe
    (no partial output is emitted before a confirmed successful response).
    """
    import asyncio as _asyncio
    from google.genai.errors import ClientError

    # Back-off: wait longer than typical per-minute window before retrying 429.
    _BACKOFF = [30, 60, 90, 120, 120]

    for attempt in range(max_retries + 1):
        try:
            collected = await _call_gemini_flash_thinking_once(messages, system_prompt)
            for item in collected:
                yield item
            return
        except ClientError as exc:
            if "429" in str(exc) and attempt < max_retries:
                wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                print(
                    f"[Gemini Flash 429] retry {attempt + 1}/{max_retries} in {wait}s",
                    flush=True,
                )
                await _asyncio.sleep(wait)
            else:
                raise


async def call_gemini_pro(
    messages: list[dict], system_prompt: str
) -> AsyncGenerator[str, None]:
    """Stream tokens from Gemini 2.5 Pro (persona agents). Retries on 429."""
    for token in await _stream_buffered(_PRO_MODEL, messages, system_prompt):
        yield token


async def call_llama_scout(
    messages: list[dict], system_prompt: str, max_retries: int = 8
) -> AsyncGenerator[str, None]:
    """
    Stream tokens from Llama 4 Scout via the Vertex AI OpenAI-compatible endpoint
    in us-east5 (v1/endpoints/openai/chat/completions, SSE response format).

    Retries up to max_retries times on HTTP 429 with exponential back-off
    (10s, 20s, 40s, 80s) before raising.
    """
    import asyncio as _asyncio

    payload = {
        "model": _LLAMA_API_MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": True,
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    # Back-off schedule: 15s, 30s, 60s, 60s, 60s, 60s, 60s, 60s (~7 min total)
    _BACKOFF = [15, 30, 60, 60, 60, 60, 60, 60]

    for attempt in range(max_retries + 1):
        token = _get_vertex_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as http:
                async with http.stream("POST", _LLAMA_URL, json=payload, headers=headers) as resp:
                    if resp.status_code == 429:
                        body = await resp.aread()
                        if attempt < max_retries:
                            wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                            await _asyncio.sleep(wait)
                            continue
                        raise httpx.HTTPStatusError(
                            f"HTTP 429 (rate limit) after {max_retries} retries: "
                            f"{body.decode('utf-8', errors='replace')}",
                            request=resp.request,
                            response=resp,
                        )
                    if not resp.is_success:
                        body = await resp.aread()
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}: {body.decode('utf-8', errors='replace')}",
                            request=resp.request,
                            response=resp,
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                            content = chunk["choices"][0]["delta"].get("content") or ""
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    return  # successful completion
        except httpx.HTTPStatusError:
            raise
        except httpx.TransportError:
            if attempt < max_retries:
                wait = 10 * (2 ** attempt)
                await _asyncio.sleep(wait)
                continue
            raise


# ── Reasoning summarizer ───────────────────────────────────────────────────────


async def summarize_reasoning(reasoning_text: str) -> str:
    """
    Return a 1-2 sentence empathetic summary of raw agent reasoning.
    Used to populate the ReasoningPanel shown to the user.
    """
    messages = [{"role": "user", "content": reasoning_text}]
    result = ""
    async for token in call_gemini_flash(messages, REASONING_SUMMARIZER_PROMPT):
        result += token
    return result.strip()


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _SYSTEM = "You are a helpful assistant. Be concise."
    _MESSAGES = [{"role": "user", "content": "Reply in one sentence only: what is 2 + 2?"}]

    async def _test_model(label: str, gen) -> str:
        print(f"\n── {label} {'─' * (54 - len(label))}")
        print("  response: ", end="", flush=True)
        full = ""
        async for token in gen:
            print(token, end="", flush=True)
            full += token
        print()
        assert full.strip(), f"{label} returned empty response"
        print(f"  ok  non-empty streaming response ({len(full)} chars)")
        return full

    async def main() -> None:
        # Gemini Flash
        await _test_model(
            "Gemini 2.5 Flash",
            call_gemini_flash(_MESSAGES, _SYSTEM),
        )

        # Gemini Pro
        await _test_model(
            "Gemini 2.5 Pro",
            call_gemini_pro(
                [{"role": "user", "content": "Reply in one sentence: what is the capital of France?"}],
                _SYSTEM,
            ),
        )

        # Llama Scout — also print raw HTTP details on failure to diagnose endpoint vs. access issues
        print("\n── Llama 4 Scout MaaS ────────────────────────────────────────")
        print(f"  endpoint: {_LLAMA_URL}")
        print(f"  model:    {_LLAMA_MODEL}")
        try:
            await _test_model(
                "Llama 4 Scout MaaS",
                call_llama_scout(
                    [{"role": "user", "content": "Reply in one sentence: what color is the sky?"}],
                    _SYSTEM,
                ),
            )
        except httpx.HTTPStatusError as exc:
            print(f"\n  ERROR: {exc}")
            raise

        # summarize_reasoning
        print("\n── summarize_reasoning ──────────────────────────────────────")
        sample_reasoning = (
            "The user reports feeling dismissed after their partner walked away mid-conversation. "
            "Core need appears to be acknowledgment and being heard. "
            "There is likely an anxious attachment pattern at play. "
            "The partner may have stonewalled due to flooding — a self-protective response, not contempt."
        )
        summary = await summarize_reasoning(sample_reasoning)
        print(f"  summary: {summary}")
        assert summary.strip(), "summarize_reasoning returned empty string"
        print(f"  ok  summary returned ({len(summary)} chars)")

        print("\n✓ All three models confirmed.")

    asyncio.run(main())
