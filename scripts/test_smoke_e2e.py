"""
End-to-end smoke test: bypass intake, inject Medium-High structured outputs,
run simulation and finalize nodes directly, verify synthesis produced.
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.graph.dasom_graph import node_finalize, node_run_simulation
from backend.graph.state import STAGE_COMPLETE, initial_state
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

_OUTPUT_A = {
    "who": "the speaker and their partner",
    "when": "last Saturday night; pattern for 6 months",
    "where": "at home",
    "what": "partner repeatedly came home late without texting",
    "why": "feels deprioritised and invisible in the relationship",
    "how": "asked calmly why no text; partner became defensive; speaker walked away",
    "emotional_labels": ["invisible", "dismissed", "exhausted", "hurt"],
    "needs": ["communication", "to feel like a priority", "to be seen"],
    "field_confidence": {
        "who": "High", "when": "High", "where": "High",
        "what": "High", "why": "High", "how": "High",
        "emotional_labels": "High", "needs": "High",
    },
    "overall_confidence": "Medium-High",
}

_OUTPUT_B = {
    "who": "the speaker and their partner",
    "when": "last Saturday; ongoing work pressure for months",
    "where": "home and workplace",
    "what": "partner interrogates every late arrival; feels controlled",
    "why": "work is genuinely demanding; feels unappreciated for providing",
    "how": "came home after a hard week; immediately confronted; got defensive",
    "emotional_labels": ["overwhelmed", "pressured", "misunderstood", "unappreciated"],
    "needs": ["space to decompress", "trust", "appreciation"],
    "field_confidence": {
        "who": "High", "when": "High", "where": "High",
        "what": "High", "why": "High", "how": "High",
        "emotional_labels": "High", "needs": "High",
    },
    "overall_confidence": "Medium-High",
}


def _ok(label: str) -> None:
    print(f"  ok  {label}")


async def main() -> None:
    print("── session setup ───────────────────────────────────────────")
    code = create_session()
    join_session(code, "user_a")
    join_session(code, "user_b")
    set_tone(code, "user_a", "warm")
    set_tone(code, "user_b", "direct")
    update_structured_output(code, "user_a", **_OUTPUT_A)
    update_structured_output(code, "user_b", **_OUTPUT_B)

    sa = get_user_state(code, "user_a")
    sb = get_user_state(code, "user_b")
    assert sa.ready, "user_a should be ready at Medium-High"
    assert sb.ready, "user_b should be ready at Medium-High"
    _ok(f"session {code}: both users injected at Medium-High confidence and marked ready")

    # Register SSE queue so broadcast_simulation has somewhere to put events
    q = register_simulation_sse(code)

    # Build DasomState with injected values
    state = initial_state(code, "user_a", "user_b")
    state["ready_a"] = True
    state["ready_b"] = True
    state["tone_a"] = "warm"
    state["tone_b"] = "direct"
    state["structured_output_a"] = _OUTPUT_A
    state["structured_output_b"] = _OUTPUT_B
    _ok("DasomState initialised with injected structured outputs")

    # ── Run simulation node ────────────────────────────────────────────
    print("\n── node_run_simulation ─────────────────────────────────────")
    print("  (running — this makes real LLM calls; mediator will intervene)")
    sim_update = await node_run_simulation(state)
    state.update(sim_update)

    transcript = get_simulation_transcript(code)
    assert transcript, "simulation transcript should be non-empty"
    a_turns = [t for t in transcript if t["speaker"] == "A"]
    b_turns = [t for t in transcript if t["speaker"] == "B"]
    print(f"  transcript: {len(transcript)} turns ({len(a_turns)} A, {len(b_turns)} B)")
    _ok("simulation transcript stored in session manager")

    assert state.get("stage") == "simulation", f"stage should be 'simulation', got {state.get('stage')!r}"
    _ok("state.stage = 'simulation' after node_run_simulation")

    raw_synthesis = get_synthesis(code)
    assert raw_synthesis, "synthesis should be stored after simulation"
    _ok(f"synthesis stored in session manager ({len(raw_synthesis)} chars)")

    # ── Run finalize node ──────────────────────────────────────────────
    print("\n── node_finalize ───────────────────────────────────────────")
    final_update = await node_finalize(state)
    state.update(final_update)

    assert state.get("stage") == STAGE_COMPLETE, (
        f"stage should be 'complete', got {state.get('stage')!r}"
    )
    _ok("state.stage = 'complete' after node_finalize")

    synthesis_a = state.get("synthesis_a", "")
    synthesis_b = state.get("synthesis_b", "")
    assert synthesis_a, "synthesis_a should be non-empty"
    assert synthesis_b, "synthesis_b should be non-empty"
    assert synthesis_a == synthesis_b == raw_synthesis
    _ok("synthesis_a and synthesis_b populated from session synthesis")

    # ── SSE event counts ───────────────────────────────────────────────
    print("\n── SSE events ──────────────────────────────────────────────")
    events: list[dict] = []
    while not q.empty():
        events.append(q.get_nowait())

    event_types: dict[str, int] = {}
    for e in events:
        event_types[e.get("type", "?")] = event_types.get(e.get("type", "?"), 0) + 1
    for etype, count in sorted(event_types.items()):
        print(f"  {etype:<25} {count}")
    assert events, "SSE queue should have received events"
    _ok(f"{len(events)} total SSE events broadcast")

    assert any(e.get("type") == "done" for e in events), "done event should be present"
    assert events[-1].get("type") == "done", "done should be the last event"
    _ok("'done' event present and is the final event")

    # ── Synthesis preview ──────────────────────────────────────────────
    print("\n── synthesis preview ───────────────────────────────────────")
    print(f"  Person A (warm)   | first 200 chars:")
    print(f"    {synthesis_a[:200].strip()}")
    print()
    print(f"  Person B (direct) | first 200 chars:")
    print(f"    {synthesis_b[:200].strip()}")

    unregister_simulation_sse(code, q)
    print("\n✓ End-to-end smoke test passed.")


asyncio.run(main())
