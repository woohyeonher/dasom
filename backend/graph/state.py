"""
backend/graph/state.py
Shared state schema for the Dasom LangGraph graph.

The DasomState TypedDict is the single object that flows through every node
and edge in the graph. LangGraph reads the `Annotated` reducer hints to decide
how to merge partial updates returned by nodes:

    Annotated[list[dict], operator.add]  →  new items are APPENDED (not replaced)
    Plain scalar fields                  →  new value REPLACES the old one

Public API
──────────
DasomState         — TypedDict: the full graph state schema
StructuredOutputDict — TypedDict: typed shape of one person's 5W1H output
initial_state(session_code, user_id_a, user_id_b) → DasomState
    Factory: returns a minimal DasomState with sensible empty defaults.

Stage constants
───────────────
STAGE_INTAKE      — emotion agents are gathering information from one or both users
STAGE_SIMULATION  — simulation loop is running (personas arguing, mediator observing)
STAGE_COMPLETE    — synthesis delivered, SSE stream closed
"""

import operator
from typing import Annotated, TypedDict


# ── Stage constants ────────────────────────────────────────────────────────────

STAGE_INTAKE: str = "intake"
STAGE_SIMULATION: str = "simulation"
STAGE_COMPLETE: str = "complete"


# ── Structured output shape ────────────────────────────────────────────────────

class StructuredOutputDict(TypedDict, total=False):
    """
    Typed shape of one person's emotion-agent intake output.
    Matches the fields of StructuredOutput in backend/session/manager.py.
    All fields are optional (total=False) because they fill in progressively.
    """
    who: str | None
    when: str | None
    where: str | None
    what: str | None
    why: str | None
    how: str | None
    emotional_labels: list[str]
    needs: list[str]
    field_confidence: dict[str, str]   # field name → confidence level string
    overall_confidence: str            # "Low" | "Low-Medium" | "Medium" | "Medium-High" | "High"


# ── Main state schema ──────────────────────────────────────────────────────────

class DasomState(TypedDict, total=False):
    """
    Full shared state for the Dasom LangGraph graph.

    Fields with Annotated[list, operator.add] reducers are APPEND-ONLY:
    when a node returns {"history_a": [new_msg]}, LangGraph concatenates
    rather than replacing. All other fields are overwritten on update.

    `total=False` means all fields are optional at any point in the graph —
    nodes only need to return the fields they actually update.
    """

    # ── Session identity ───────────────────────────────────────────────────
    session_code: str          # 6-character session code
    user_id_a: str             # session user ID for Person A
    user_id_b: str             # session user ID for Person B

    # ── Tone preferences ───────────────────────────────────────────────────
    tone_a: str                # "warm" | "neutral" | "direct"
    tone_b: str

    # ── Structured outputs from emotion-agent intake ───────────────────────
    structured_output_a: StructuredOutputDict
    structured_output_b: StructuredOutputDict

    # ── Conversation histories (append-only) ──────────────────────────────
    # Each message: {"role": "user"|"assistant", "content": str}
    history_a: Annotated[list[dict], operator.add]
    history_b: Annotated[list[dict], operator.add]

    # ── Readiness flags ────────────────────────────────────────────────────
    # True once the emotion agent reaches Medium-High or High confidence
    ready_a: bool
    ready_b: bool

    # ── Simulation transcript (append-only) ───────────────────────────────
    # Each turn: {"speaker": "A"|"B", "text": str}
    simulation_transcript: Annotated[list[dict], operator.add]

    # ── Mediator synthesis — one version per person ────────────────────────
    # Both contain the full 6-section document; section 6 ("A Message to Each
    # Person") is framed specifically for each person's tone preference.
    synthesis_a: str
    synthesis_b: str

    # ── Graph control ──────────────────────────────────────────────────────
    stage: str                 # STAGE_INTAKE | STAGE_SIMULATION | STAGE_COMPLETE


# ── Factory function ──────────────────────────────────────────────────────────

def initial_state(
    session_code: str,
    user_id_a: str,
    user_id_b: str,
) -> DasomState:
    """
    Return a minimal DasomState for a fresh session.
    Tone and structured outputs are set by later graph nodes (after the user
    sets their preference and completes intake).
    """
    return DasomState(
        session_code=session_code,
        user_id_a=user_id_a,
        user_id_b=user_id_b,
        tone_a="neutral",
        tone_b="neutral",
        structured_output_a=StructuredOutputDict(),
        structured_output_b=StructuredOutputDict(),
        history_a=[],
        history_b=[],
        ready_a=False,
        ready_b=False,
        simulation_transcript=[],
        synthesis_a="",
        synthesis_b="",
        stage=STAGE_INTAKE,
    )


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def _ok(label: str) -> None:
        print(f"  ok  {label}")

    print("── DasomState schema ───────────────────────────────────────")

    # Factory creates a valid initial state
    state = initial_state("ABC123", "user_a", "user_b")

    assert state["session_code"] == "ABC123"
    assert state["user_id_a"] == "user_a"
    assert state["user_id_b"] == "user_b"
    _ok("initial_state() sets session identity fields")

    assert state["stage"] == STAGE_INTAKE
    _ok(f"initial stage is STAGE_INTAKE ({STAGE_INTAKE!r})")

    assert state["tone_a"] == "neutral"
    assert state["tone_b"] == "neutral"
    _ok("default tones are 'neutral'")

    assert state["history_a"] == []
    assert state["history_b"] == []
    assert state["simulation_transcript"] == []
    _ok("list fields initialise to []")

    assert state["ready_a"] is False
    assert state["ready_b"] is False
    _ok("readiness flags initialise to False")

    assert state["synthesis_a"] == ""
    assert state["synthesis_b"] == ""
    _ok("synthesis fields initialise to ''")

    # Verify Annotated reducer hints are preserved.
    # Use get_type_hints(..., include_extras=True) — the only reliable way to
    # recover full Annotated[...] types from a TypedDict (plain __annotations__
    # returns string literals when from __future__ import annotations is active).
    from typing import get_type_hints
    hints = get_type_hints(DasomState, include_extras=True)
    for field in ("history_a", "history_b", "simulation_transcript"):
        assert hasattr(hints[field], "__metadata__"), (
            f"field '{field}' should have Annotated metadata (reducer)"
        )
        reducer = hints[field].__metadata__[0]
        assert reducer is operator.add, (
            f"reducer for '{field}' should be operator.add"
        )
    _ok("history_a, history_b, simulation_transcript carry operator.add reducer")

    # Simulate what LangGraph does when a node returns a partial update:
    # reducer = operator.add → new items are appended, not replaced
    msg1 = {"role": "user", "content": "I felt dismissed."}
    msg2 = {"role": "assistant", "content": "That sounds really hard."}
    merged = operator.add(state["history_a"], [msg1])
    merged = operator.add(merged, [msg2])
    assert merged == [msg1, msg2], "operator.add appends correctly"
    _ok("operator.add reducer appends messages — existing list preserved")

    # Stage transitions
    for stage_val in (STAGE_INTAKE, STAGE_SIMULATION, STAGE_COMPLETE):
        updated = {**state, "stage": stage_val}
        assert updated["stage"] == stage_val
    _ok("stage can be updated to any defined constant")

    # StructuredOutputDict accepts all expected keys
    so: StructuredOutputDict = StructuredOutputDict(
        who="my partner",
        when="last Saturday",
        where="at home",
        what="came home late without texting",
        why="feels deprioritised",
        how="asked calmly, partner got defensive",
        emotional_labels=["dismissed", "invisible"],
        needs=["communication", "to feel like a priority"],
        field_confidence={"who": "High", "what": "High"},
        overall_confidence="High",
    )
    assert so["overall_confidence"] == "High"
    _ok("StructuredOutputDict accepts all 5W1H + emotional + confidence fields")

    print("\n── DasomState field inventory ──────────────────────────────")
    for field, annotation in hints.items():
        print(f"  {field:<26} {str(annotation)[:60]}")

    print("\n✓ State schema verified.")
