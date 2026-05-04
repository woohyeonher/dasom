"""
backend/graph/dasom_graph.py
LangGraph graph definition for the full Dasom session lifecycle.

Graph topology
──────────────
                    ┌──────────┐
            ┌──────►│ intake_a │◄───────┐
            │       └────┬─────┘        │
  [START]───┤            ▼              │
            │       ┌────────────────┐  │
            │       │ check_readiness │──┤ (neither ready)
            │       └────┬───────────┘  │
            │  (both     │              │
            │   ready)   ▼              │
            │       ┌──────────┐        │
            └──────►│ intake_b │────────┘
                    └────┬─────┘
                         │ (both ready → run_simulation)
                         ▼
                  ┌──────────────┐
                  │ run_simulation│
                  └──────┬───────┘
                         ▼
                   ┌──────────┐
                   │ finalize  │
                   └─────┬────┘
                         ▼
                       [END]

Nodes
─────
intake_a         — syncs Person A's emotion-agent state into the graph after each turn
intake_b         — syncs Person B's emotion-agent state into the graph after each turn
check_readiness  — reads the latest readiness flags from the session manager
run_simulation   — executes the full PersonaAgent A↔B loop with Mediator observer
finalize         — stores per-person synthesis into state; marks stage=complete

Routing
───────
route_after_readiness:
    ready_a and ready_b → "run_simulation"
    not ready_a, not ready_b → ["intake_a", "intake_b"]  (parallel loop-back)
    not ready_a only → "intake_a"
    not ready_b only → "intake_b"

Exported symbols
────────────────
dasom_graph      — compiled StateGraph (with MemorySaver checkpointer)
build_graph()    — factory to compile a fresh graph (optional custom checkpointer)
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.agents.mediator_agent import run_simulation as _run_simulation
from backend.graph.state import (
    STAGE_COMPLETE,
    STAGE_INTAKE,
    STAGE_SIMULATION,
    DasomState,
)
from backend.session.manager import (
    get_simulation_transcript,
    get_synthesis,
    get_user_state,
)


# ── Node functions ─────────────────────────────────────────────────────────────
#
# Each node reads from the session manager (single source of truth for live
# agent data) and returns a partial DasomState to update the graph's copy.
#
# The emotion-agent turns themselves are driven by FastAPI's POST /intake/message
# endpoint calling emotion_agent.process_turn() directly.  These nodes sync that
# progress into the graph state after each turn so LangGraph can route correctly.


async def node_intake_a(state: DasomState) -> dict:
    """
    Sync Person A's emotion-agent progress from the session manager into the
    LangGraph state.  Called once per round-trip through the intake loop.
    """
    user_state = get_user_state(state["session_code"], state.get("user_id_a", ""))
    if user_state is None:
        return {"ready_a": False, "stage": STAGE_INTAKE}

    return {
        "structured_output_a": dataclasses.asdict(user_state.structured_output),
        "ready_a": user_state.ready,
        "tone_a": user_state.tone or "neutral",
        "stage": STAGE_INTAKE,
    }


async def node_intake_b(state: DasomState) -> dict:
    """
    Sync Person B's emotion-agent progress from the session manager into the
    LangGraph state.  Called once per round-trip through the intake loop.
    """
    user_state = get_user_state(state["session_code"], state.get("user_id_b", ""))
    if user_state is None:
        return {"ready_b": False, "stage": STAGE_INTAKE}

    return {
        "structured_output_b": dataclasses.asdict(user_state.structured_output),
        "ready_b": user_state.ready,
        "tone_b": user_state.tone or "neutral",
        "stage": STAGE_INTAKE,
    }


async def node_check_readiness(state: DasomState) -> dict:
    """
    Re-read both users' readiness flags from the session manager after the
    intake nodes have synced, ensuring the routing decision sees fresh data.
    """
    code = state["session_code"]
    sa = get_user_state(code, state.get("user_id_a", ""))
    sb = get_user_state(code, state.get("user_id_b", ""))

    return {
        "ready_a": sa.ready if sa else False,
        "ready_b": sb.ready if sb else False,
    }


async def node_run_simulation(state: DasomState) -> dict:
    """
    Execute the full simulation lifecycle:
      - PersonaAgent A and PersonaAgent B argue in alternating turns
      - Mediator observes after every turn and decides when to intervene
      - Mediator runs synthesis after intervention
      - Session manager stores transcript and synthesis

    After completion, syncs both into the LangGraph state.
    """
    code = state["session_code"]
    await _run_simulation(code, state["user_id_a"], state["user_id_b"])

    transcript = get_simulation_transcript(code)
    return {
        "simulation_transcript": transcript,
        "stage": STAGE_SIMULATION,
    }


async def node_finalize(state: DasomState) -> dict:
    """
    Read the synthesis produced by the mediator, store a copy for each person
    (both currently receive the full 6-section document — the frontend renders
    the relevant "To Person X" subsection prominently), and mark the session done.
    """
    synthesis = get_synthesis(state["session_code"])
    return {
        "synthesis_a": synthesis,
        "synthesis_b": synthesis,
        "stage": STAGE_COMPLETE,
    }


# ── Routing ────────────────────────────────────────────────────────────────────


def route_after_readiness(state: DasomState) -> str | list[str]:
    """
    Decide where to go after check_readiness.

    Both ready     → proceed to simulation
    Only A not ready → loop back to intake_a
    Only B not ready → loop back to intake_b
    Neither ready  → loop back to both intake nodes in parallel
    """
    ready_a = state.get("ready_a", False)
    ready_b = state.get("ready_b", False)

    if ready_a and ready_b:
        return "run_simulation"

    not_ready: list[str] = []
    if not ready_a:
        not_ready.append("intake_a")
    if not ready_b:
        not_ready.append("intake_b")

    return not_ready if len(not_ready) > 1 else not_ready[0]


# ── Graph construction ─────────────────────────────────────────────────────────


def build_graph(checkpointer=None):
    """
    Build and compile the Dasom StateGraph.

    checkpointer: optional LangGraph checkpointer (e.g. MemorySaver) for
                  human-in-the-loop persistence across HTTP requests.
                  Pass None for tests or one-shot execution.
    """
    builder = StateGraph(DasomState)

    # Register nodes
    builder.add_node("intake_a", node_intake_a)
    builder.add_node("intake_b", node_intake_b)
    builder.add_node("check_readiness", node_check_readiness)
    builder.add_node("run_simulation", node_run_simulation)
    builder.add_node("finalize", node_finalize)

    # START → both intake nodes in parallel (fan-out)
    builder.add_edge(START, "intake_a")
    builder.add_edge(START, "intake_b")

    # Both intake nodes → check_readiness (LangGraph joins after both complete)
    builder.add_edge("intake_a", "check_readiness")
    builder.add_edge("intake_b", "check_readiness")

    # Conditional: route based on readiness state
    builder.add_conditional_edges("check_readiness", route_after_readiness)

    # Linear: simulation complete → finalize → END
    builder.add_edge("run_simulation", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


# ── Module-level compiled graph ────────────────────────────────────────────────

# Exported for use by FastAPI endpoints.  Uses MemorySaver so graph state
# persists across multiple HTTP requests within a single Python process
# (matching the in-memory-only constraint from CLAUDE.md).
dasom_graph = build_graph(checkpointer=MemorySaver())


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def _ok(label: str) -> None:
        print(f"  ok  {label}")

    print("── compiling Dasom graph ────────────────────────────────────")
    # Compile without checkpointer for the structure test
    graph = build_graph()
    _ok("build_graph() compiled without errors")

    # ── Node inventory ─────────────────────────────────────────────────
    print("\n── node list ────────────────────────────────────────────────")
    all_nodes = list(graph.nodes)
    for n in all_nodes:
        print(f"  {n}")

    user_nodes = [n for n in all_nodes if not n.startswith("__")]
    expected_nodes = {"intake_a", "intake_b", "check_readiness", "run_simulation", "finalize"}
    actual_nodes = set(user_nodes)

    assert actual_nodes == expected_nodes, (
        f"node mismatch\n  expected: {expected_nodes}\n  got:      {actual_nodes}"
    )
    _ok(f"all 5 expected nodes present: {sorted(actual_nodes)}")

    # ── Edge verification via graph schema ─────────────────────────────
    print("\n── edge structure ───────────────────────────────────────────")

    # LangGraph exposes compiled edge data via graph.builder or the raw graph
    # object.  We verify structure by inspecting what the builder recorded.
    builder_graph = graph.builder

    # Verify START fans out to both intake nodes
    start_edges = [
        (src, dst)
        for (src, dst) in builder_graph.edges
        if src == START
    ]
    start_targets = {dst for (_, dst) in start_edges}
    assert "intake_a" in start_targets, "START must edge to intake_a"
    assert "intake_b" in start_targets, "START must edge to intake_b"
    _ok(f"START fans out to: {sorted(start_targets)}")

    # Verify both intake nodes converge on check_readiness
    for intake_node in ("intake_a", "intake_b"):
        outgoing = [
            (src, dst)
            for (src, dst) in builder_graph.edges
            if src == intake_node
        ]
        targets = {dst for (_, dst) in outgoing}
        assert "check_readiness" in targets, (
            f"{intake_node} must have edge to check_readiness"
        )
    _ok("intake_a and intake_b both edge to check_readiness (join)")

    # Verify run_simulation → finalize → END linear chain
    sim_targets = {
        dst for (src, dst) in builder_graph.edges if src == "run_simulation"
    }
    assert "finalize" in sim_targets, "run_simulation must edge to finalize"
    _ok("run_simulation → finalize")

    final_targets = {dst for (src, dst) in builder_graph.edges if src == "finalize"}
    assert END in final_targets, "finalize must edge to END"
    _ok("finalize → END")

    # Verify check_readiness has a conditional edge
    cond_edge_sources = {src for src, _, _ in builder_graph.branches.get("check_readiness", {}).values()
                         } if hasattr(builder_graph, "branches") else set()
    # Presence check: conditional edges are stored differently; just verify
    # that route_after_readiness is the registered routing function
    cond_edges = builder_graph.branches.get("check_readiness", {})
    assert cond_edges, "check_readiness must have a conditional edge"
    _ok("check_readiness has conditional edge (confidence gate router)")

    # ── Routing function logic ─────────────────────────────────────────
    print("\n── routing function unit tests ──────────────────────────────")

    def _route(a: bool, b: bool) -> str | list[str]:
        return route_after_readiness({"ready_a": a, "ready_b": b})

    result = _route(True, True)
    assert result == "run_simulation", f"expected 'run_simulation', got {result!r}"
    _ok("both ready → 'run_simulation'")

    result = _route(False, False)
    assert isinstance(result, list) and set(result) == {"intake_a", "intake_b"}, (
        f"expected ['intake_a', 'intake_b'], got {result!r}"
    )
    _ok("neither ready → ['intake_a', 'intake_b']")

    result = _route(False, True)
    assert result == "intake_a", f"expected 'intake_a', got {result!r}"
    _ok("only A not ready → 'intake_a'")

    result = _route(True, False)
    assert result == "intake_b", f"expected 'intake_b', got {result!r}"
    _ok("only B not ready → 'intake_b'")

    # ── Module-level dasom_graph ───────────────────────────────────────
    print("\n── module-level dasom_graph (with MemorySaver) ──────────────")
    assert dasom_graph is not None
    assert set(n for n in dasom_graph.nodes if not n.startswith("__")) == expected_nodes
    _ok("dasom_graph compiled with MemorySaver checkpointer")

    print("\n✓ Graph structure verified.")
