"""
SSE streaming helpers for Dasom FastAPI endpoints.

Public API
──────────
emit(queue, event_type, data)            async — push event onto a queue
intake_stream(session_code, user_id)     async generator → SSE dicts
simulation_stream(session_code)          async generator → SSE dicts

SSE dict format (consumed by sse-starlette EventSourceResponse):
    {"event": "<event_type>", "data": "<json-string>"}

Event types
───────────
  token           — text token streamed from an agent
  reasoning       — summarised reasoning chunk for the ReasoningPanel
  analysis_update — updated 5W1H / confidence fields for the AnalysisPanel
  simulation_turn — one persona agent turn in the simulation arena
  done            — stream complete; generators exit after yielding this

Internal queue payload format (what agents put onto queues):
    {"type": "<event_type>", "data": <any json-serialisable value>}
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.session.manager import (
    register_intake_sse,
    register_simulation_sse,
    unregister_intake_sse,
    unregister_simulation_sse,
)

# ── Internal helpers ──────────────────────────────────────────────────────────


def _to_sse(event: dict) -> dict:
    """
    Convert an internal event dict to the sse-starlette EventSourceResponse format.

    Input:  {"type": "token", "data": {"text": "hello"}}
    Output: {"event": "token", "data": '{"text": "hello"}'}
    """
    return {
        "event": event["type"],
        "data": json.dumps(event["data"], ensure_ascii=False),
    }


# ── Public API ────────────────────────────────────────────────────────────────


async def emit(queue: asyncio.Queue, event_type: str, data: Any) -> None:
    """
    Push a structured event onto a queue.

    Called by agents to send tokens, reasoning summaries, and analysis updates
    to a specific user's intake queue, or to the simulation broadcast queue.
    """
    await queue.put({"type": event_type, "data": data})


async def intake_stream(
    session_code: str, user_id: str
) -> AsyncGenerator[dict, None]:
    """
    Register an intake SSE queue for the user and yield events until "done".

    Pass the returned generator directly to sse-starlette's EventSourceResponse.
    The queue is automatically unregistered when the generator exits (including
    on client disconnect, which triggers a GeneratorExit).
    """
    queue = register_intake_sse(session_code, user_id)
    try:
        while True:
            event: dict = await queue.get()
            yield _to_sse(event)
            if event.get("type") == "done":
                break
    finally:
        unregister_intake_sse(session_code, user_id, queue)


async def simulation_stream(session_code: str) -> AsyncGenerator[dict, None]:
    """
    Register a simulation SSE queue and yield broadcast events until "done".

    Multiple clients (Person A and Person B) each get their own queue via this
    function; broadcast_simulation() in manager.py fans out to all of them.
    Pass the returned generator directly to sse-starlette's EventSourceResponse.
    """
    queue = register_simulation_sse(session_code)
    try:
        while True:
            event: dict = await queue.get()
            yield _to_sse(event)
            if event.get("type") == "done":
                break
    finally:
        unregister_simulation_sse(session_code, queue)


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from backend.session.manager import (
        broadcast_intake,
        broadcast_simulation,
        create_session,
        join_session,
    )

    def _ok(label: str) -> None:
        print(f"  ok  {label}")

    async def _run_tests() -> None:

        # ── emit + _to_sse ────────────────────────────────────────────────────
        print("── emit helper ─────────────────────────────────────────────")

        q: asyncio.Queue = asyncio.Queue()
        await emit(q, "token", {"text": "hello"})
        raw = q.get_nowait()
        assert raw == {"type": "token", "data": {"text": "hello"}}, f"unexpected: {raw}"
        _ok("emit pushes correct internal dict structure")

        sse = _to_sse(raw)
        assert sse == {"event": "token", "data": '{"text": "hello"}'}
        _ok('_to_sse maps to {"event": ..., "data": json-string}')

        # Round-trip: json.loads(sse["data"]) recovers original data
        assert json.loads(sse["data"]) == {"text": "hello"}
        _ok("SSE data field is valid JSON string")

        # ── intake_stream ─────────────────────────────────────────────────────
        print("\n── intake_stream ───────────────────────────────────────────")

        code = create_session()
        join_session(code, "user_a")
        intake_events: list[dict] = []

        async def _send_intake() -> None:
            await asyncio.sleep(0.05)   # let consumer register its queue first
            await broadcast_intake(code, "user_a", {"type": "token",           "data": {"text": "Hello"}})
            await broadcast_intake(code, "user_a", {"type": "reasoning",       "data": {"summary": "There is pain here."}})
            await broadcast_intake(code, "user_a", {"type": "analysis_update", "data": {"who": "partner", "overall_confidence": "Low"}})
            await broadcast_intake(code, "user_a", {"type": "done",            "data": {}})

        async def _recv_intake() -> None:
            async for evt in intake_stream(code, "user_a"):
                intake_events.append(evt)

        await asyncio.gather(_send_intake(), _recv_intake())

        assert len(intake_events) == 4, f"expected 4 events, got {len(intake_events)}"
        assert intake_events[0] == {"event": "token", "data": json.dumps({"text": "Hello"})}
        _ok("first event: token with correct payload")

        assert intake_events[1]["event"] == "reasoning"
        assert json.loads(intake_events[1]["data"])["summary"] == "There is pain here."
        _ok("second event: reasoning with correct payload")

        assert intake_events[2]["event"] == "analysis_update"
        assert json.loads(intake_events[2]["data"])["overall_confidence"] == "Low"
        _ok("third event: analysis_update with correct payload")

        assert intake_events[3] == {"event": "done", "data": "{}"}
        _ok("fourth event: done — stream terminated correctly")

        # ── simulation_stream ─────────────────────────────────────────────────
        print("\n── simulation_stream ───────────────────────────────────────")

        sim_events: list[dict] = []

        async def _send_sim() -> None:
            await asyncio.sleep(0.05)
            await broadcast_simulation(code, {"type": "simulation_turn", "data": {"speaker": "A", "text": "I felt ignored."}})
            await broadcast_simulation(code, {"type": "simulation_turn", "data": {"speaker": "B", "text": "I didn't realise."}})
            await broadcast_simulation(code, {"type": "done", "data": {}})

        async def _recv_sim() -> None:
            async for evt in simulation_stream(code):
                sim_events.append(evt)

        await asyncio.gather(_send_sim(), _recv_sim())

        assert len(sim_events) == 3, f"expected 3 events, got {len(sim_events)}"
        assert sim_events[0]["event"] == "simulation_turn"
        assert json.loads(sim_events[0]["data"])["speaker"] == "A"
        _ok("simulation_turn A received correctly")

        assert sim_events[1]["event"] == "simulation_turn"
        assert json.loads(sim_events[1]["data"])["speaker"] == "B"
        _ok("simulation_turn B received correctly")

        assert sim_events[2]["event"] == "done"
        _ok("simulation_stream terminates on done event")

        # ── fan-out (two clients on same simulation) ──────────────────────────
        print("\n── fan-out — two clients, same session ─────────────────────")

        client_a: list[dict] = []
        client_b: list[dict] = []

        async def _send_fanout() -> None:
            await asyncio.sleep(0.05)
            await broadcast_simulation(code, {"type": "simulation_turn", "data": {"speaker": "A", "text": "hi"}})
            await broadcast_simulation(code, {"type": "done", "data": {}})

        async def _client(buf: list) -> None:
            async for evt in simulation_stream(code):
                buf.append(evt)

        await asyncio.gather(_send_fanout(), _client(client_a), _client(client_b))

        assert len(client_a) == 2 and len(client_b) == 2
        assert client_a == client_b
        _ok("both clients received identical events (fan-out confirmed)")

        print("\n✓ All streaming tests passed.")

    asyncio.run(_run_tests())
