"""
backend/agents/emotion_agent.py
Emotion Agent — private intake companion for one user.

Public API
──────────
start_intake(session_code, user_id)              async → None
    Sends the opening welcome message to start the intake conversation.
    Call once after the user sets their tone, before any process_turn calls.

process_turn(session_code, user_id, user_message) async → bool
    Process one user message turn.
    Returns True when intake is complete (confidence >= Medium-High + marker).

SSE events emitted (via broadcast_intake → session manager fan-out):
    token           — one text chunk from the agent response
    reasoning       — 1-2 sentence summary of the agent's thinking
    analysis_update — updated 5W1H / confidence state after each turn
    done            — intake complete; emitted before returning True
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.models.router import (
    call_gemini_flash,
    call_gemini_flash_thinking,
    summarize_reasoning,
)
from backend.rag.retriever import retrieve_examples, retrieve_psychology
from backend.session.manager import (
    append_history,
    broadcast_intake,
    get_user_state,
    mark_intake_complete,
    update_structured_output,
)
from backend.utils.prompts import EMOTION_AGENT_PROMPT

# ── Analysis system prompt ─────────────────────────────────────────────────────

_ANALYSIS_SYSTEM = """\
You are analyzing an intake conversation between a person sharing their relationship conflict \
and an AI companion.

Based on the full conversation transcript, extract what has been clearly confirmed and understood.

Respond with ONLY a valid JSON object — no markdown, no explanation, no preamble:
{
  "who": "string or null",
  "when": "string or null",
  "where": "string or null",
  "what": "string or null",
  "why": "string or null",
  "how": "string or null",
  "emotional_labels": ["list", "of", "emotion", "words"],
  "needs": ["list", "of", "underlying", "needs"],
  "field_confidence": {
    "who": "Low|Low-Medium|Medium|Medium-High|High",
    "when": "Low|Low-Medium|Medium|Medium-High|High",
    "where": "Low|Low-Medium|Medium|Medium-High|High",
    "what": "Low|Low-Medium|Medium|Medium-High|High",
    "why": "Low|Low-Medium|Medium|Medium-High|High",
    "how": "Low|Low-Medium|Medium|Medium-High|High",
    "emotional_labels": "Low|Low-Medium|Medium|Medium-High|High",
    "needs": "Low|Low-Medium|Medium|Medium-High|High"
  },
  "overall_confidence": "Low|Low-Medium|Medium|Medium-High|High"
}

Confidence scale:
  Low          — dimension absent or extremely vague
  Low-Medium   — mentioned but unclear or not yet confirmed by the user
  Medium       — reasonable picture; notable gaps or uncertainties remain
  Medium-High  — solid, confirmed information; only minor details unclear
  High         — complete, clear, well-confirmed across all dimensions

Set overall_confidence to Medium-High or High ONLY when:
  - Most of the 5W1H dimensions have confirmed, specific information (not vague)
  - At least 2 specific emotions have been expressed and acknowledged
  - At least 1 core underlying need has been identified
  - The user has confirmed the agent's reflection at least once

Be conservative. A single exchange rarely reaches Medium-High.
For any dimension with no information, use null for the value and "Low" for field confidence.\
"""

# ── RAG helpers ────────────────────────────────────────────────────────────────

_RAG_HEADER = (
    "\n\n[Context for your response — use to inform understanding, do not quote directly:\n"
)
_RAG_FOOTER = "]"

_VALID_CONFIDENCE = {"Low", "Low-Medium", "Medium", "Medium-High", "High"}
_VALID_FIELDS = {
    "who", "when", "where", "what", "why", "how", "emotional_labels", "needs"
}

# Synthetic trigger stored in history when agent speaks first
_START_TRIGGER = "[session started]"


def _format_rag(user_message: str) -> str:
    """
    Retrieve RAG context relevant to the user's message.
    Returns a formatted string to append to the current user turn,
    or "" if retrieval fails or returns nothing useful.
    """
    try:
        examples = retrieve_examples(user_message, n_results=2)
        psych = retrieve_psychology(user_message, n_results=2)
    except RuntimeError:
        return ""

    lines: list[str] = []
    if examples:
        lines.append("Similar relationship situations for context:")
        for hit in examples:
            snippet = hit["text"][:300].replace("\n", " ").strip()
            lines.append(f"  - {snippet}")
    if psych:
        lines.append("Relevant psychological framework:")
        for hit in psych:
            snippet = hit["text"][:300].replace("\n", " ").strip()
            lines.append(f"  - {snippet}")

    return (_RAG_HEADER + "\n".join(lines) + _RAG_FOOTER) if lines else ""


# ── Message builder ────────────────────────────────────────────────────────────


def _build_messages(prior_history: list[dict], current_user_text: str) -> list[dict]:
    """
    Build the LLM messages list from stored history plus the current (possibly
    RAG-enriched) user turn. RAG enrichment is not stored in session history.
    """
    messages = [{"role": m["role"], "content": m["content"]} for m in prior_history]
    messages.append({"role": "user", "content": current_user_text})
    return messages


# ── Conversation analyzer ──────────────────────────────────────────────────────


def _parse_analysis(raw: str) -> dict[str, Any]:
    """Parse and validate the JSON analysis response. Returns safe defaults on error."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1:] if first_nl != -1 else cleaned[3:]
        last_fence = cleaned.rfind("```")
        cleaned = cleaned[:last_fence] if last_fence != -1 else cleaned

    try:
        data = json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError):
        return {"overall_confidence": "Low"}

    result: dict[str, Any] = {}
    for dim in ("who", "when", "where", "what", "why", "how"):
        v = data.get(dim)
        result[dim] = str(v).strip() if v is not None else None

    result["emotional_labels"] = [
        str(e) for e in data.get("emotional_labels", []) if isinstance(e, str)
    ]
    result["needs"] = [str(n) for n in data.get("needs", []) if isinstance(n, str)]

    raw_fc = data.get("field_confidence") or {}
    result["field_confidence"] = {
        k: v
        for k, v in raw_fc.items()
        if k in _VALID_FIELDS and v in _VALID_CONFIDENCE
    }

    overall = data.get("overall_confidence", "Low")
    result["overall_confidence"] = overall if overall in _VALID_CONFIDENCE else "Low"
    return result


async def _analyze_conversation(history: list[dict]) -> dict[str, Any]:
    """
    Non-streaming Gemini Flash call to extract structured 5W1H + confidence from
    the full conversation so far. Returns kwargs compatible with update_structured_output().
    """
    if not history:
        return {"overall_confidence": "Low"}

    lines = []
    for msg in history:
        if msg["content"] == _START_TRIGGER:
            continue
        role = "User" if msg["role"] == "user" else "Agent"
        lines.append(f"[{role}]: {msg['content']}")

    if not lines:
        return {"overall_confidence": "Low"}

    messages = [{"role": "user", "content": "\n".join(lines)}]
    raw = ""
    async for token in call_gemini_flash(messages, _ANALYSIS_SYSTEM):
        raw += token
    return _parse_analysis(raw)


# ── Streaming helper ───────────────────────────────────────────────────────────


async def _run_agent_turn(
    session_code: str,
    user_id: str,
    messages: list[dict],
    system_prompt: str,
) -> tuple[str, str]:
    """
    Call Gemini Flash with thinking, stream text tokens as SSE events,
    collect thinking tokens for reasoning summarization.

    Returns (full_text_response, full_thinking_text).
    """
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []

    try:
        async for part in call_gemini_flash_thinking(messages, system_prompt):
            if part["type"] == "thinking":
                thinking_chunks.append(part["content"])
            else:
                text_chunks.append(part["content"])
                await broadcast_intake(session_code, user_id, {
                    "type": "token",
                    "data": {"text": part["content"]},
                })
    finally:
        # Always signal turn completion so the frontend cursor stops,
        # even if streaming was interrupted or thinking was empty.
        await broadcast_intake(session_code, user_id, {
            "type": "turn_complete",
            "data": {},
        })

    return "".join(text_chunks), "".join(thinking_chunks)


# ── Public API ─────────────────────────────────────────────────────────────────


async def start_intake(session_code: str, user_id: str) -> None:
    """
    Send the opening agent message to begin the intake conversation.
    Must be called once after the user's tone is set, before any process_turn calls.

    Emits: token events (the opening message), reasoning event (if thinking produced).
    Does not emit analysis_update — no user content to analyze yet.
    """
    user_state = get_user_state(session_code, user_id)
    if user_state is None:
        raise KeyError(f"User '{user_id}' not in session '{session_code}'")

    tone = user_state.tone or "neutral"
    system_prompt = EMOTION_AGENT_PROMPT(tone)
    messages = [{"role": "user", "content": _START_TRIGGER}]

    full_response, full_thinking = await _run_agent_turn(
        session_code, user_id, messages, system_prompt
    )

    # Store trigger + opening in history so subsequent calls have proper context
    append_history(session_code, user_id, {"role": "user", "content": _START_TRIGGER})
    append_history(session_code, user_id, {"role": "assistant", "content": full_response})

    if full_thinking.strip():
        summary = await summarize_reasoning(full_thinking)
        await broadcast_intake(session_code, user_id, {
            "type": "reasoning",
            "data": {"summary": summary},
        })


async def process_turn(session_code: str, user_id: str, user_message: str) -> bool:
    """
    Process one user message turn in the emotion agent intake conversation.

    Steps:
      1. Appends user message to session history (clean, no RAG).
      2. Retrieves RAG context and injects into the current turn only.
      3. Calls Gemini 2.5 Flash with thinking enabled.
      4. Streams text tokens as SSE 'token' events.
      5. Summarizes thinking tokens → emits 'reasoning' event.
      6. Analyzes full conversation → updates structured output → emits 'analysis_update'.
      7. If [INTAKE_COMPLETE] found AND confidence >= Medium-High: emits 'done', returns True.

    Returns:
        True  — intake complete; 'done' event has been emitted
        False — intake ongoing; continue accepting user messages
    """
    user_state = get_user_state(session_code, user_id)
    if user_state is None:
        raise KeyError(f"User '{user_id}' not in session '{session_code}'")

    tone = user_state.tone or "neutral"
    system_prompt = EMOTION_AGENT_PROMPT(tone)

    # Store clean user message in history
    append_history(session_code, user_id, {"role": "user", "content": user_message})

    # Build messages: prior history + RAG-enriched current user turn
    prior_history = user_state.history[:-1]
    rag_context = _format_rag(user_message)
    enriched = user_message + rag_context if rag_context else user_message
    messages = _build_messages(prior_history, enriched)

    # Stream agent response
    full_response, full_thinking = await _run_agent_turn(
        session_code, user_id, messages, system_prompt
    )

    # Store assistant response in history
    append_history(session_code, user_id, {"role": "assistant", "content": full_response})

    # Emit reasoning summary if thinking tokens were produced
    if full_thinking.strip():
        summary = await summarize_reasoning(full_thinking)
        await broadcast_intake(session_code, user_id, {
            "type": "reasoning",
            "data": {"summary": summary},
        })

    # Analyze full conversation and update session state
    analysis = await _analyze_conversation(user_state.history)
    update_structured_output(session_code, user_id, **analysis)

    # Emit analysis_update with the full structured picture
    await broadcast_intake(session_code, user_id, {
        "type": "analysis_update",
        "data": {
            "who": analysis.get("who"),
            "when": analysis.get("when"),
            "where": analysis.get("where"),
            "what": analysis.get("what"),
            "why": analysis.get("why"),
            "how": analysis.get("how"),
            "emotional_labels": analysis.get("emotional_labels", []),
            "needs": analysis.get("needs", []),
            "field_confidence": analysis.get("field_confidence", {}),
            "overall_confidence": analysis.get("overall_confidence", "Low"),
        },
    })

    # Hard block: only complete if BOTH the marker is present AND confidence is sufficient
    confidence = analysis.get("overall_confidence", "Low")
    is_complete = (
        "[INTAKE_COMPLETE]" in full_response
        and confidence in {"Medium-High", "High"}
    )

    if is_complete:
        mark_intake_complete(session_code, user_id)
        await broadcast_intake(session_code, user_id, {"type": "done", "data": {}})

    return is_complete


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Three-turn simulated intake conversation with rich emotional content.
    # Designed to give the emotion agent enough material to analyze across
    # all 5W1H dimensions and identify emotions + needs.
    _TURNS = [
        (
            "We had a huge fight last Saturday night at home. My partner came home really late "
            "from work again without texting me, and I had dinner waiting. I felt completely "
            "invisible and dismissed. This pattern has been going on for about six months now "
            "and I'm exhausted from it."
        ),
        (
            "Yes, you got that exactly right. We've been together for three years. I need him "
            "to communicate with me — even a quick text would make such a difference. It's like "
            "I'm not a priority anymore. The relationship used to feel equal but now it feels "
            "one-sided."
        ),
        (
            "The fight started when I asked calmly why he didn't text, and he got really "
            "defensive and accused me of being controlling. I just walked away because I "
            "couldn't take it anymore. We've barely spoken since. At the core I just need "
            "to feel like I matter to him. I need him to see that I'm here waiting and that "
            "it hurts when he disappears."
        ),
    ]

    def _ok(label: str) -> None:
        print(f"  ok  {label}")

    def _drain(queue: asyncio.Queue) -> list[dict]:
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events

    async def _run_test() -> None:
        from backend.session.manager import (
            create_session,
            join_session,
            register_intake_sse,
            set_tone,
            unregister_intake_sse,
        )

        print("── session setup ───────────────────────────────────────────")
        code = create_session()
        join_session(code, "tester")
        set_tone(code, "tester", "warm")
        q = register_intake_sse(code, "tester")
        _ok(f"session {code} created, user joined with tone=warm, SSE queue registered")

        # ── Opening message ────────────────────────────────────────────────
        print("\n── start_intake (opening message) ──────────────────────────")
        await start_intake(code, "tester")
        events = _drain(q)
        token_events = [e for e in events if e.get("type") == "token"]
        reasoning_events = [e for e in events if e.get("type") == "reasoning"]
        opening_text = "".join(e["data"]["text"] for e in token_events)
        print(f"  opening ({len(opening_text)} chars): {opening_text[:120].strip()}...")
        assert token_events, "opening message should emit at least one token event"
        _ok(f"start_intake emitted {len(token_events)} token events")
        if reasoning_events:
            summary = reasoning_events[0]["data"]["summary"]
            print(f"  reasoning: {summary}")
            _ok("reasoning event emitted for opening")
        else:
            print("  (no thinking tokens in opening — normal)")

        user_state = get_user_state(code, "tester")
        assert len(user_state.history) == 2, (
            f"history should have trigger + opening, got {len(user_state.history)}"
        )
        _ok("opening message stored in session history")

        # ── Three user turns ───────────────────────────────────────────────
        for i, msg in enumerate(_TURNS, 1):
            print(f"\n── turn {i} ──────────────────────────────────────────────")
            print(f"  user: {msg[:80].strip()}...")

            done = await process_turn(code, "tester", msg)
            events = _drain(q)

            token_evts = [e for e in events if e.get("type") == "token"]
            reasoning_evts = [e for e in events if e.get("type") == "reasoning"]
            analysis_evts = [e for e in events if e.get("type") == "analysis_update"]
            done_evts = [e for e in events if e.get("type") == "done"]

            agent_reply = "".join(e["data"]["text"] for e in token_evts)
            print(f"  agent ({len(agent_reply)} chars): {agent_reply[:120].strip()}...")

            assert token_evts, f"turn {i}: expected token events"
            _ok(f"turn {i}: {len(token_evts)} token events emitted")

            if reasoning_evts:
                print(f"  reasoning: {reasoning_evts[0]['data']['summary']}")
                _ok(f"turn {i}: reasoning event emitted")

            assert analysis_evts, f"turn {i}: expected analysis_update event"
            analysis = analysis_evts[0]["data"]
            confidence = analysis.get("overall_confidence", "?")
            print(f"  confidence: {confidence}")
            print(f"  emotions: {analysis.get('emotional_labels', [])}")
            print(f"  needs:    {analysis.get('needs', [])}")
            _ok(f"turn {i}: analysis_update emitted (confidence={confidence})")

            # Verify session state was updated
            state = get_user_state(code, "tester")
            assert state.structured_output.overall_confidence == confidence
            _ok(f"turn {i}: session structured_output updated")

            if done:
                assert done_evts, "done=True but no 'done' event emitted"
                assert confidence in {"Medium-High", "High"}, (
                    f"intake completed at {confidence} — should be >= Medium-High"
                )
                print(f"  [INTAKE_COMPLETE] detected at turn {i}")
                _ok(f"turn {i}: intake complete — done event emitted, confidence gate passed")
                break
            else:
                assert not done_evts, "done=False but 'done' event was emitted"
                print(f"  intake ongoing (confidence={confidence})")

        # ── Final history check ────────────────────────────────────────────
        print("\n── final session state ─────────────────────────────────────")
        state = get_user_state(code, "tester")
        print(f"  history length: {len(state.history)} messages")
        print(f"  ready flag:     {state.ready}")
        print(f"  confidence:     {state.structured_output.overall_confidence}")
        print(f"  who:   {state.structured_output.who}")
        print(f"  when:  {state.structured_output.when}")
        print(f"  what:  {state.structured_output.what}")
        print(f"  emotions: {state.structured_output.emotional_labels}")
        print(f"  needs:    {state.structured_output.needs}")
        assert len(state.history) >= 4, "expected at least 4 messages in history"
        _ok("session history has expected depth")

        unregister_intake_sse(code, "tester", q)

        print("\n✓ Emotion agent test complete.")

    asyncio.run(_run_test())
