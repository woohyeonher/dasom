"""
backend/agents/mediator_agent.py
Mediator Agent + simulation loop orchestrator.

Public API
──────────
run_simulation(session_code, user_id_a, user_id_b, max_rounds=10)  async → None
    Full simulation lifecycle:
      1. Initialise PersonaAgent A and B from session state.
      2. Run alternating turns (A first).
      3. After every turn: call the mediator observer (Llama Scout) to decide
         whether to intervene.  Observer reason broadcast as 'reasoning' event.
      4. On intervention (or max_rounds reached): run mediator synthesis.
      5. Stream synthesis tokens as 'synthesis_token' events.
      6. Store synthesis in session state.
      7. Broadcast 'done' to close the simulation SSE stream.

SSE events broadcast (via broadcast_simulation fan-out):
    simulation_token     — one text token from a persona turn (emitted by PersonaAgent)
    simulation_turn      — complete persona turn (emitted by PersonaAgent)
    reasoning            — mediator's observation reason after each turn
    mediator_intervention— mediator has decided to end the simulation
    synthesis_token      — one text token from the synthesis
    synthesis_complete   — full assembled synthesis text
    done                 — simulation stream is finished
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.agents.persona_agent import PersonaAgent
from backend.models.router import call_llama_scout
from backend.session.manager import (
    append_simulation_turn,
    broadcast_simulation,
    get_user_state,
    store_synthesis,
)
from backend.utils.prompts import MEDIATOR_OBSERVER_PROMPT, MEDIATOR_SYNTHESIS_PROMPT

# ── Transcript formatter ───────────────────────────────────────────────────────


def _format_transcript_numbered(transcript: list[dict]) -> str:
    """Format the simulation transcript with turn numbers for the mediator observer."""
    if not transcript:
        return "(no turns yet)"
    lines = []
    for i, turn in enumerate(transcript, 1):
        speaker = turn["speaker"]
        text = turn["text"].strip()
        lines.append(f"Turn {i} (Person {speaker}):\n{text}")
    return "\n\n".join(lines)


# ── Observer (Llama Scout JSON decision) ──────────────────────────────────────


def _parse_observer_json(raw: str) -> dict:
    """
    Extract the JSON object from Llama Scout's observer response.
    Returns {"intervene": bool, "reason": str} or a safe default on parse failure.

    Handles all known Llama response patterns in order:
      1. Strip <think>/<|thinking|>/<reasoning> blocks and similar XML-style tags.
      2. Strip markdown fences (```json ... ``` or ``` ... ```).
      3. JSON-parse the first { ... } block found.
      4. Normalize Python-style booleans (True/False) and retry JSON parse.
      5. Keyword scan for "intervene": true/false anywhere in the text.
      6. Safe default: intervene=False.
    """
    import re

    cleaned = raw.strip()

    # Strategy 0: recover streaming artifact where the opening `{"` is dropped
    # (Llama Scout sometimes loses its first token — seen as `intervene": ...}`)
    # The missing prefix is `{"` (2 chars), so prepend both to restore valid JSON.
    if cleaned and not cleaned.startswith("{") and "intervene" in cleaned[:20].lower():
        cleaned = '{"' + cleaned

    # Strategy 0a: strip <think>...</think> and similar reasoning wrappers
    cleaned = re.sub(r"<\|?thinking\|?>.*?</\|?thinking\|?>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<reasoning>.*?</reasoning>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()

    # Strategy 0b: strip markdown fences if present
    fence_match = re.match(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    elif cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1:] if first_nl != -1 else cleaned[3:]
        last_fence = cleaned.rfind("```")
        cleaned = cleaned[:last_fence].strip() if last_fence != -1 else cleaned

    # Strategy 1: find and parse the first { ... } block
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start: end + 1]
        try:
            data = json.loads(candidate)
            return {
                "intervene": bool(data.get("intervene", False)),
                "reason": str(data.get("reason", "")).strip(),
            }
        except (json.JSONDecodeError, ValueError):
            # Strategy 1b: normalize Python booleans then retry
            normalized = candidate.replace(": True", ": true").replace(":True", ":true")
            normalized = normalized.replace(": False", ": false").replace(":False", ":false")
            normalized = normalized.replace(": None", ": null").replace(":None", ":null")
            # Replace single quotes around keys/values with double quotes
            normalized = re.sub(r"'([^']*)'", r'"\1"', normalized)
            try:
                data = json.loads(normalized)
                return {
                    "intervene": bool(data.get("intervene", False)),
                    "reason": str(data.get("reason", "")).strip(),
                }
            except (json.JSONDecodeError, ValueError):
                pass

    # Strategy 2: keyword scan (handles prose-wrapped responses)
    lower = raw.lower()
    intervene_true = (
        '"intervene": true' in lower or '"intervene":true' in lower
        or "'intervene': true" in lower or "intervene=true" in lower
    )
    intervene_false = (
        '"intervene": false' in lower or '"intervene":false' in lower
        or "'intervene': false" in lower or "intervene=false" in lower
    )

    def _extract_reason(text: str) -> str:
        lower_t = text.lower()
        for key in ('"reason"', "'reason'", "reason:"):
            idx = lower_t.find(key)
            if idx != -1:
                snippet = text[idx:]
                colon = snippet.find(":")
                if colon != -1:
                    after = snippet[colon + 1:].strip().strip("\"'").split('"')[0].split("'")[0]
                    if after:
                        return after.strip()
        return ""

    if intervene_true:
        reason = _extract_reason(raw)
        return {"intervene": True, "reason": reason or "Mediator decided to intervene."}

    if intervene_false:
        reason = _extract_reason(raw)
        return {"intervene": False, "reason": reason or "Mediator is still observing."}

    return {"intervene": False, "reason": f"(unparseable — raw: {raw[:120]!r})"}


_observe_call_count = 0


async def _observe(transcript: list[dict]) -> dict:
    """
    Ask Llama Scout whether to intervene in the simulation.
    Returns {"intervene": bool, "reason": str}.
    """
    global _observe_call_count
    _observe_call_count += 1
    call_num = _observe_call_count

    formatted = _format_transcript_numbered(transcript)
    messages = [
        {
            "role": "user",
            "content": (
                "Here is the simulation transcript so far. "
                "Decide whether to intervene.\n\n"
                + formatted
            ),
        }
    ]
    raw = ""
    async for token in call_llama_scout(messages, MEDIATOR_OBSERVER_PROMPT):
        raw += token

    # Print raw response for first 3 calls so format issues are visible in logs
    if call_num <= 3:
        print(
            f"[observer call {call_num}] raw ({len(raw)} chars): {raw!r}",
            flush=True,
        )

    return _parse_observer_json(raw)


# ── Synthesis (Llama Scout streaming 6-section document) ──────────────────────


async def _synthesize(
    session_code: str,
    output_a: dict,
    output_b: dict,
    tone_a: str,
    tone_b: str,
    transcript: list[dict],
) -> str:
    """
    Call Llama Scout with MEDIATOR_SYNTHESIS_PROMPT to produce the full 6-section synthesis.
    Streams each token as a 'synthesis_token' SSE event.
    Returns the complete synthesis text.
    """
    system = MEDIATOR_SYNTHESIS_PROMPT(output_a, output_b, tone_a, tone_b, transcript)
    messages = [{"role": "user", "content": "Produce the mediation synthesis now."}]

    chunks: list[str] = []
    async for token in call_llama_scout(messages, system):
        chunks.append(token)
        await broadcast_simulation(session_code, {
            "type": "synthesis_token",
            "data": {"text": token},
        })

    full_text = "".join(chunks).strip()
    await broadcast_simulation(session_code, {
        "type": "synthesis_complete",
        "data": {"text": full_text},
    })
    return full_text


# ── Public orchestrator ────────────────────────────────────────────────────────


async def run_simulation(
    session_code: str,
    user_id_a: str,
    user_id_b: str,
    max_rounds: int = 10,
) -> None:
    """
    Run the full simulation lifecycle for a session.

    user_id_a / user_id_b: session user IDs for the two participants.
    Person A = user_id_a; Person B = user_id_b.
    max_rounds: safety ceiling (mediator normally ends it sooner).
    """
    print(f"SIMULATION STARTED session={session_code} a={user_id_a} b={user_id_b}", flush=True)
    try:
        await _run_simulation_inner(session_code, user_id_a, user_id_b, max_rounds)
    except Exception as e:
        import traceback
        print(f"SIMULATION ERROR session={session_code}: {type(e).__name__}: {e}", flush=True)
        print(traceback.format_exc(), flush=True)


async def _run_simulation_inner(
    session_code: str,
    user_id_a: str,
    user_id_b: str,
    max_rounds: int,
) -> None:
    # Load state
    state_a = get_user_state(session_code, user_id_a)
    state_b = get_user_state(session_code, user_id_b)
    if state_a is None or state_b is None:
        raise KeyError(
            f"Both users must be in session '{session_code}' before running simulation"
        )

    output_a = dataclasses.asdict(state_a.structured_output)
    output_b = dataclasses.asdict(state_b.structured_output)
    tone_a = state_a.tone or "neutral"
    tone_b = state_b.tone or "neutral"

    # Initialise persona agents
    persona_a = PersonaAgent(session_code, "A", "B", output_a, tone_a)
    persona_b = PersonaAgent(session_code, "B", "A", output_b, tone_b)

    transcript: list[dict] = []
    last_b_response: str | None = None
    intervened = False
    turn_count = 0  # increments after each individual speaker turn (A or B)

    for round_num in range(1, max_rounds + 1):

        # ── Person A's turn ────────────────────────────────────────────────
        a_tokens: list[str] = []
        async for token in persona_a.generate_turn(last_b_response):
            a_tokens.append(token)
        a_response = "".join(a_tokens)
        transcript.append({"speaker": "A", "text": a_response})
        append_simulation_turn(session_code, "A", a_response)
        turn_count += 1  # turn_count is now odd — skip observer

        # ── Person B's turn ────────────────────────────────────────────────
        b_tokens: list[str] = []
        async for token in persona_b.generate_turn(a_response):
            b_tokens.append(token)
        b_response = "".join(b_tokens)
        last_b_response = b_response
        transcript.append({"speaker": "B", "text": b_response})
        append_simulation_turn(session_code, "B", b_response)
        turn_count += 1  # turn_count is now even — call observer

        # Mediator observes every 2 turns (after each complete A+B exchange)
        obs = await _observe(transcript)
        await broadcast_simulation(session_code, {
            "type": "reasoning",
            "data": {"summary": obs["reason"]},
        })
        if obs["intervene"]:
            await broadcast_simulation(session_code, {
                "type": "mediator_intervention",
                "data": {"reason": obs["reason"]},
            })
            intervened = True
            break

    if not intervened:
        # Safety: max_rounds reached without mediator intervention
        await broadcast_simulation(session_code, {
            "type": "mediator_intervention",
            "data": {"reason": "Maximum rounds reached — proceeding to synthesis."},
        })

    # ── Mediator synthesis ─────────────────────────────────────────────────
    synthesis_text = await _synthesize(
        session_code, output_a, output_b, tone_a, tone_b, transcript
    )
    store_synthesis(session_code, synthesis_text)

    # Close the simulation SSE stream
    await broadcast_simulation(session_code, {"type": "done", "data": {}})


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Dummy structured outputs for two users with opposing perspectives
    _OUTPUT_A = {
        "who": "the speaker and their partner",
        "when": "last Saturday night; pattern ongoing 6 months",
        "where": "at home",
        "what": "partner came home late without texting repeatedly",
        "why": "feels deprioritised and invisible in the relationship",
        "how": "calmly asked why no text; partner got defensive; speaker walked away",
        "emotional_labels": ["invisible", "dismissed", "exhausted", "hurt"],
        "needs": ["communication", "to feel like a priority", "to be seen"],
        "field_confidence": {
            "who": "High", "when": "High", "where": "High",
            "what": "High", "why": "High", "how": "High",
            "emotional_labels": "High", "needs": "High",
        },
        "overall_confidence": "High",
    }

    _OUTPUT_B = {
        "who": "the speaker and their partner",
        "when": "last Saturday; ongoing work pressure for months",
        "where": "home and workplace",
        "what": "partner interrogates every late arrival and makes them feel controlled",
        "why": "work has been genuinely demanding; feels unappreciated for providing",
        "how": "came home after a hard week; immediately confronted; got defensive",
        "emotional_labels": ["overwhelmed", "pressured", "misunderstood", "unappreciated"],
        "needs": ["space to decompress", "trust", "appreciation"],
        "field_confidence": {
            "who": "High", "when": "High", "where": "High",
            "what": "High", "why": "High", "how": "High",
            "emotional_labels": "High", "needs": "High",
        },
        "overall_confidence": "High",
    }

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
            get_simulation_transcript,
            get_synthesis,
            get_user_state,
            join_session,
            register_simulation_sse,
            set_tone,
            unregister_simulation_sse,
            update_structured_output,
        )
        from dataclasses import fields

        print("── session setup ───────────────────────────────────────────")
        code = create_session()
        join_session(code, "user_a")
        join_session(code, "user_b")
        set_tone(code, "user_a", "warm")
        set_tone(code, "user_b", "direct")

        # Load structured outputs into session state
        update_structured_output(code, "user_a", **_OUTPUT_A)
        update_structured_output(code, "user_b", **_OUTPUT_B)

        q = register_simulation_sse(code)
        _ok(f"session {code} ready — both users joined and structured outputs loaded")

        # ── Run simulation ─────────────────────────────────────────────────
        print("\n── simulation run ──────────────────────────────────────────")
        print("  (running — mediator will intervene after >= 3 rounds per persona)")
        print("  (max_rounds=8 safety ceiling)")

        await run_simulation(code, "user_a", "user_b", max_rounds=8)

        # ── Collect and inspect all events ────────────────────────────────
        events = _drain(q)
        print(f"\n── event stream ({len(events)} total events) ─────────────────────")

        sim_turns = [e for e in events if e.get("type") == "simulation_turn"]
        sim_tokens = [e for e in events if e.get("type") == "simulation_token"]
        reasoning_evts = [e for e in events if e.get("type") == "reasoning"]
        intervention_evts = [e for e in events if e.get("type") == "mediator_intervention"]
        synth_tokens = [e for e in events if e.get("type") == "synthesis_token"]
        synth_complete = [e for e in events if e.get("type") == "synthesis_complete"]
        done_evts = [e for e in events if e.get("type") == "done"]

        print(f"  simulation_turn:      {len(sim_turns)}")
        print(f"  simulation_token:     {len(sim_tokens)}")
        print(f"  reasoning:            {len(reasoning_evts)}")
        print(f"  mediator_intervention:{len(intervention_evts)}")
        print(f"  synthesis_token:      {len(synth_tokens)}")
        print(f"  synthesis_complete:   {len(synth_complete)}")
        print(f"  done:                 {len(done_evts)}")

        # ── Verify simulation turns ────────────────────────────────────────
        assert sim_turns, "expected simulation_turn events"
        a_turns = [e for e in sim_turns if e["data"]["speaker"] == "A"]
        b_turns = [e for e in sim_turns if e["data"]["speaker"] == "B"]
        print(f"\n  A turns: {len(a_turns)} | B turns: {len(b_turns)}")
        _ok(f"simulation produced {len(a_turns)} A turns and {len(b_turns)} B turns")

        # Print abbreviated conversation
        print("\n  ── conversation excerpt ──────────────────────────────────")
        for turn in sim_turns:
            sp = turn["data"]["speaker"]
            txt = turn["data"]["text"][:100].strip()
            print(f"  [{sp}]: {txt}...")

        # ── Verify mediator events ─────────────────────────────────────────
        assert reasoning_evts, "expected reasoning events from mediator observer"
        _ok(f"{len(reasoning_evts)} mediator reasoning events emitted")

        if reasoning_evts:
            print(f"\n  last mediator reason: {reasoning_evts[-1]['data']['summary']}")

        assert intervention_evts, "expected mediator_intervention event"
        intervention_reason = intervention_evts[0]["data"]["reason"]
        print(f"  intervention reason: {intervention_reason}")
        _ok("mediator intervention event emitted")

        # ── Verify synthesis ───────────────────────────────────────────────
        assert synth_complete, "expected synthesis_complete event"
        synthesis_from_event = synth_complete[0]["data"]["text"]
        print(f"\n  synthesis ({len(synthesis_from_event)} chars)")

        # Verify all 6 sections are present (check content, not markdown prefix)
        required_sections = [
            "WHAT HAPPENED",
            "WHAT EACH PERSON FELT",
            "WHERE YOU ACTUALLY AGREE",
            "THE CORE TENSION",
            "A PATH FORWARD",
            "A MESSAGE TO EACH PERSON",
        ]
        upper_synthesis = synthesis_from_event.upper()
        missing = [s for s in required_sections if s not in upper_synthesis]
        if missing:
            print(f"  WARNING: missing sections: {missing}")
        else:
            _ok("all 6 synthesis sections present")

        # Print first section
        first_section_end = synthesis_from_event.find("\n## ", 20)
        first_section = synthesis_from_event[:first_section_end if first_section_end > 0 else 400]
        print(f"\n  synthesis excerpt:\n  {first_section[:300].strip()}...")

        # ── Verify session state ───────────────────────────────────────────
        print("\n── session state after simulation ──────────────────────────")
        transcript = get_simulation_transcript(code)
        stored_synthesis = get_synthesis(code)

        assert transcript, "simulation transcript should be stored"
        assert len(transcript) == len(sim_turns), (
            f"transcript length {len(transcript)} != sim_turn events {len(sim_turns)}"
        )
        _ok(f"simulation transcript stored ({len(transcript)} turns)")

        assert stored_synthesis, "synthesis should be stored in session"
        assert stored_synthesis == synthesis_from_event
        _ok(f"synthesis stored in session ({len(stored_synthesis)} chars)")

        # ── Verify done event ──────────────────────────────────────────────
        assert done_evts, "expected 'done' event to close stream"
        assert events[-1]["type"] == "done", "done must be the last event"
        _ok("'done' event emitted as final event")

        unregister_simulation_sse(code, q)
        print("\n✓ Simulation loop + mediator test complete.")

    asyncio.run(_run_test())
