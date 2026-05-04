"""
backend/agents/persona_agent.py
Persona Agent — argues one person's perspective in the simulation arena.

Public API
──────────
PersonaAgent(session_code, person_label, other_label, structured_output, tone)
    Initialises the agent for one person. Call once before simulation begins.

    person_label / other_label: "A" or "B"
    structured_output: StructuredOutput fields as a plain dict
    tone: "warm" | "neutral" | "direct"

PersonaAgent.generate_turn(opponent_message=None) → AsyncGenerator[str, None]
    Yield str tokens for one simulation turn.
    Pass opponent_message=None for the very first turn (no opponent has spoken yet).
    Pass the opponent's last full response for all subsequent turns.

    Side effects (via broadcast_simulation):
        simulation_token — each text token as it is generated
        simulation_turn  — the complete turn text, emitted after generation finishes
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.models.router import call_gemini_pro
from backend.rag.retriever import retrieve_examples
from backend.session.manager import broadcast_simulation
from backend.utils.prompts import PERSONA_AGENT_PROMPT

# Synthetic trigger sent as the first "user" message to prompt the persona's opening
_SIM_OPEN_TRIGGER = (
    "[The mediation simulation is beginning. "
    "Speak directly to the other person. "
    "Express your perspective on what happened — start the conversation.]"
)

# RAG examples section appended to the system prompt (grounding language in real human expression)
_RAG_SECTION_HEADER = """

═══ REAL EXAMPLES — HOW PEOPLE IN CONFLICT ACTUALLY SPEAK ═══
These are excerpts from real accounts of relationship conflict. \
Use them to calibrate the emotional register and authenticity of your language. \
Do not copy them — let them inform how you sound:

"""


def _build_system_prompt(
    person_label: str,
    other_label: str,
    structured_output: dict[str, Any],
    tone: str,
) -> str:
    """Build the full system prompt: base persona prompt + RAG few-shot examples."""
    base = PERSONA_AGENT_PROMPT(person_label, other_label, structured_output, tone)

    # Retrieve few-shot examples grounded in the conflict topic
    conflict_query = structured_output.get("what") or "relationship conflict argument"
    try:
        examples = retrieve_examples(conflict_query, n_results=3)
    except RuntimeError:
        examples = []

    if examples:
        snippets = []
        for hit in examples:
            snippet = hit["text"][:400].replace("\n", " ").strip()
            snippets.append(f"  • {snippet}")
        base += _RAG_SECTION_HEADER + "\n".join(snippets)

    return base


class PersonaAgent:
    """
    Stateful agent representing one person's perspective in the simulation.

    Maintains its own conversation history (opponent turns as "user",
    own turns as "assistant"). History grows across calls to generate_turn.
    """

    def __init__(
        self,
        session_code: str,
        person_label: str,
        other_label: str,
        structured_output: dict[str, Any],
        tone: str,
    ) -> None:
        self.session_code = session_code
        self.label = person_label
        self._system_prompt = _build_system_prompt(
            person_label, other_label, structured_output, tone
        )
        self._history: list[dict] = []

    async def generate_turn(
        self, opponent_message: str | None = None
    ) -> AsyncGenerator[str, None]:
        """
        Generate one simulation turn.

        opponent_message=None  → first turn; persona speaks first with no prior exchange.
        opponent_message=<str> → persona responds to the opponent's last message.

        Yields str tokens to the caller.
        Broadcasts simulation_token and simulation_turn SSE events.
        """
        trigger = _SIM_OPEN_TRIGGER if opponent_message is None else opponent_message
        self._history.append({"role": "user", "content": trigger})

        text_chunks: list[str] = []
        async for token in call_gemini_pro(self._history, self._system_prompt):
            text_chunks.append(token)
            yield token
            await broadcast_simulation(self.session_code, {
                "type": "simulation_token",
                "data": {"speaker": self.label, "text": token},
            })

        full_response = "".join(text_chunks)
        self._history.append({"role": "assistant", "content": full_response})

        await broadcast_simulation(self.session_code, {
            "type": "simulation_turn",
            "data": {"speaker": self.label, "text": full_response},
        })

    @property
    def turn_count(self) -> int:
        """Number of complete turns this persona has taken."""
        return sum(1 for m in self._history if m["role"] == "assistant")

    def last_response(self) -> str:
        """The most recent complete response this persona produced."""
        for msg in reversed(self._history):
            if msg["role"] == "assistant":
                return msg["content"]
        return ""


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Two-person conflict: late nights / communication pattern
    _OUTPUT_A: dict[str, Any] = {
        "who": "the speaker and their partner",
        "when": "last Saturday night; ongoing for 6 months",
        "where": "at home",
        "what": "partner repeatedly came home late without texting",
        "why": "feels deprioritised in the relationship",
        "how": "calmly asked why no text; partner got defensive; speaker walked away",
        "emotional_labels": ["invisible", "dismissed", "exhausted"],
        "needs": ["communication", "to feel like a priority", "to be seen"],
        "field_confidence": {
            "who": "High", "when": "High", "where": "High",
            "what": "High", "why": "Medium-High", "how": "High",
            "emotional_labels": "High", "needs": "High",
        },
        "overall_confidence": "High",
    }

    _OUTPUT_B: dict[str, Any] = {
        "who": "the speaker and their partner",
        "when": "last Saturday; ongoing work pressure for months",
        "where": "at home / at work",
        "what": "partner interrogates every late arrival and calls it controlling",
        "why": "work has been genuinely demanding; feels unappreciated for providing",
        "how": "came home late after a hard week; immediately confronted; defended himself",
        "emotional_labels": ["overwhelmed", "pressured", "misunderstood", "unappreciated"],
        "needs": ["space to decompress", "trust", "appreciation for working hard"],
        "field_confidence": {
            "who": "High", "when": "High", "where": "Medium-High",
            "what": "High", "why": "High", "how": "High",
            "emotional_labels": "High", "needs": "High",
        },
        "overall_confidence": "High",
    }

    _NUM_TURNS = 3  # turns per persona

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
            register_simulation_sse,
            unregister_simulation_sse,
        )

        print("── session setup ───────────────────────────────────────────")
        code = create_session()
        q = register_simulation_sse(code)
        _ok(f"session {code} created, simulation SSE queue registered")

        # Instantiate both persona agents
        persona_a = PersonaAgent(code, "A", "B", _OUTPUT_A, "warm")
        persona_b = PersonaAgent(code, "B", "A", _OUTPUT_B, "direct")
        _ok("PersonaAgent A (warm) and B (direct) instantiated")

        # Verify system prompts were built
        assert "Person A" in persona_a._system_prompt
        assert "Person B" in persona_b._system_prompt
        _ok("system prompts contain correct person labels")

        # ── Simulation: 3 turns per persona ───────────────────────────────
        print(f"\n── simulation — {_NUM_TURNS} turns per persona ──────────────────────")

        last_a = None
        last_b = None

        for round_num in range(1, _NUM_TURNS + 1):
            # ── Person A's turn ────────────────────────────────────────────
            print(f"\n  [Round {round_num} — A speaks]")
            a_tokens: list[str] = []
            async for token in persona_a.generate_turn(last_b):
                a_tokens.append(token)
            last_a = "".join(a_tokens)

            a_events = _drain(q)
            a_sim_tokens = [e for e in a_events if e.get("type") == "simulation_token"]
            a_sim_turns = [e for e in a_events if e.get("type") == "simulation_turn"]

            print(f"  A ({len(last_a)} chars): {last_a[:120].strip()}...")
            assert a_tokens, f"round {round_num} A: no tokens yielded"
            assert a_sim_tokens, f"round {round_num} A: no simulation_token events"
            assert len(a_sim_turns) == 1, (
                f"round {round_num} A: expected 1 simulation_turn, got {len(a_sim_turns)}"
            )
            assert a_sim_turns[0]["data"]["speaker"] == "A"
            assert a_sim_turns[0]["data"]["text"] == last_a
            _ok(
                f"round {round_num} A: {len(a_sim_tokens)} token events + "
                f"1 simulation_turn event (speaker=A)"
            )

            # ── Person B's turn ────────────────────────────────────────────
            print(f"\n  [Round {round_num} — B responds]")
            b_tokens: list[str] = []
            async for token in persona_b.generate_turn(last_a):
                b_tokens.append(token)
            last_b = "".join(b_tokens)

            b_events = _drain(q)
            b_sim_tokens = [e for e in b_events if e.get("type") == "simulation_token"]
            b_sim_turns = [e for e in b_events if e.get("type") == "simulation_turn"]

            print(f"  B ({len(last_b)} chars): {last_b[:120].strip()}...")
            assert b_tokens, f"round {round_num} B: no tokens yielded"
            assert b_sim_tokens, f"round {round_num} B: no simulation_token events"
            assert len(b_sim_turns) == 1, (
                f"round {round_num} B: expected 1 simulation_turn, got {len(b_sim_turns)}"
            )
            assert b_sim_turns[0]["data"]["speaker"] == "B"
            assert b_sim_turns[0]["data"]["text"] == last_b
            _ok(
                f"round {round_num} B: {len(b_sim_tokens)} token events + "
                f"1 simulation_turn event (speaker=B)"
            )

        # ── Final state checks ─────────────────────────────────────────────
        print("\n── final agent state ───────────────────────────────────────")
        assert persona_a.turn_count == _NUM_TURNS, (
            f"A: expected {_NUM_TURNS} turns, got {persona_a.turn_count}"
        )
        assert persona_b.turn_count == _NUM_TURNS, (
            f"B: expected {_NUM_TURNS} turns, got {persona_b.turn_count}"
        )
        _ok(f"both personas completed {_NUM_TURNS} turns each")

        a_history_len = len(persona_a._history)
        b_history_len = len(persona_b._history)
        expected = _NUM_TURNS * 2
        assert a_history_len == expected, (
            f"A history: expected {expected} messages, got {a_history_len}"
        )
        assert b_history_len == expected, (
            f"B history: expected {expected} messages, got {b_history_len}"
        )
        _ok(
            f"A history: {a_history_len} messages | "
            f"B history: {b_history_len} messages"
        )

        assert persona_a.last_response() == last_a
        assert persona_b.last_response() == last_b
        _ok("last_response() returns correct final message for each persona")

        unregister_simulation_sse(code, q)
        print("\n✓ Persona agent test complete.")

    asyncio.run(_run_test())
