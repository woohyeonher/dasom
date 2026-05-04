"""
backend/main.py
FastAPI application for Dasom — AI relationship conflict mediator.

Endpoints
─────────
GET  /health
POST /session/create
POST /session/join/{code}
GET  /stream/intake/{session_code}/{user_id}     SSE
POST /intake/message
GET  /stream/simulation/{session_code}           SSE
GET  /result/{session_code}/{user_id}
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.agents.emotion_agent import process_turn, start_intake
from backend.agents.mediator_agent import run_simulation
from backend.session.manager import (
    both_ready,
    create_session,
    get_session,
    get_synthesis,
    get_user_state,
    join_session,
    mark_simulation_started,
    register_intake_sse,
    register_simulation_sse,
    unregister_intake_sse,
    unregister_simulation_sse,
)

# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(title="Dasom API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Shared SSE event converter ─────────────────────────────────────────────────

def _to_sse(event: dict) -> dict:
    """
    Convert internal event dict to sse-starlette format.
    Sends as an unnamed SSE event so the browser EventSource onmessage
    handler receives it. The type is carried inside the JSON body so the
    frontend can dispatch on it: { type: "token", data: { text: "..." } }
    """
    return {
        "data": json.dumps({"type": event["type"], "data": event["data"]}, ensure_ascii=False),
    }


# ── Request models ─────────────────────────────────────────────────────────────

class IntakeMessageRequest(BaseModel):
    session_code: str
    user_id: str
    message: str


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/session/create")
async def session_create():
    """
    Create a new session and return its 6-character code.
    Auto-registers the creator as user 'a'.
    Share the code (or the join link) with your partner.
    """
    code = create_session()
    join_session(code, "a")
    return {"session_code": code}


@app.post("/session/join/{code}")
async def session_join(code: str):
    """
    Join an existing session as user 'b'.
    Returns 404 if the session code does not exist.
    Returns 409 if user 'b' is already registered (session is full).
    """
    session = get_session(code)
    if session is None:
        raise HTTPException(404, f"Session '{code}' not found")
    if "b" in session.users:
        raise HTTPException(409, "session is already full")
    join_session(code, "b")
    return {"session_code": code, "user_id": "b"}


@app.get("/stream/intake/{session_code}/{user_id}")
async def stream_intake(session_code: str, user_id: str):
    """
    SSE stream for the emotion-agent intake conversation.

    Connects to the user's intake queue.  On first connection (empty history)
    the emotion agent's opening message is triggered immediately as a background
    task — the queue is registered before the task starts so no tokens are lost.

    Event types yielded: token | reasoning | analysis_update | done
    """
    user_state = get_user_state(session_code, user_id)
    if user_state is None:
        raise HTTPException(404, "session or user not found")

    async def _gen():
        q = register_intake_sse(session_code, user_id)
        try:
            # Queue is now registered. Schedule the opening message only on
            # first connection (empty history = agent hasn't spoken yet).
            if not user_state.history:
                asyncio.create_task(start_intake(session_code, user_id))

            while True:
                event: dict = await q.get()
                yield _to_sse(event)
                if event.get("type") == "done":
                    break
        except asyncio.CancelledError:
            pass
        finally:
            unregister_intake_sse(session_code, user_id, q)

    return EventSourceResponse(_gen())


@app.post("/intake/message")
async def intake_message(body: IntakeMessageRequest):
    """
    Submit one user message to the emotion agent.

    Triggers emotion_agent.process_turn(), which streams token / reasoning /
    analysis_update events back to the user's open SSE stream.

    Returns:
        done  — True if intake is complete (confidence gate passed + [INTAKE_COMPLETE])
        ready — True if the user's overall_confidence is >= Medium-High
    """
    user_state = get_user_state(body.session_code, body.user_id)
    if user_state is None:
        raise HTTPException(404, "session or user not found")
    try:
        done = await process_turn(body.session_code, body.user_id, body.message)
    except KeyError as exc:
        raise HTTPException(404, str(exc))

    # Re-read readiness after the turn (process_turn may have updated it)
    user_state = get_user_state(body.session_code, body.user_id)
    return {"done": done, "ready": user_state.ready if user_state else False}


@app.get("/stream/simulation/{session_code}")
async def stream_simulation(session_code: str):
    """
    SSE stream for the simulation arena — fan-out to all connected clients.

    Both Person A and Person B connect here and see the same stream in real time.
    The simulation is started exactly once: the first client to connect (after
    both users are ready) triggers run_simulation as a background task.

    Event types yielded:
        simulation_token | simulation_turn | reasoning |
        mediator_intervention | synthesis_token | synthesis_complete | done
    """
    session = get_session(session_code)
    if session is None:
        raise HTTPException(404, "session not found")

    async def _gen():
        q = register_simulation_sse(session_code)
        try:
            # Yield a 'connected' event immediately so the response body starts
            # flowing before any API calls begin. Without this, sse-starlette /
            # uvicorn closes idle chunked responses within seconds.
            yield _to_sse({"type": "connected", "data": {}})

            # Queue registered. Start simulation once, only when both are ready.
            print(f"BOTH READY CHECK: {both_ready(session_code)} session={session_code}", flush=True)
            if both_ready(session_code) and mark_simulation_started(session_code):
                user_ids = list(session.users.keys())
                asyncio.create_task(
                    run_simulation(session_code, user_ids[0], user_ids[1])
                )

            while True:
                event: dict = await q.get()
                yield _to_sse(event)
                if event.get("type") == "done":
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"ERROR in simulation SSE session={session_code}: {type(exc).__name__}: {exc}", flush=True)
            raise
        finally:
            unregister_simulation_sse(session_code, q)

    return EventSourceResponse(_gen())


@app.get("/result/{session_code}/{user_id}")
async def result(session_code: str, user_id: str):
    """
    Return the final mediator synthesis for a user.

    Both users receive the full 6-section document; the frontend is responsible
    for highlighting the 'To Person X' subsection relevant to each viewer.
    Returns 404 if synthesis is not yet available (simulation still running).
    """
    if get_user_state(session_code, user_id) is None:
        raise HTTPException(404, "session or user not found")
    synthesis = get_synthesis(session_code)
    if not synthesis:
        raise HTTPException(404, "synthesis not yet available — simulation may still be running")
    return {"synthesis": synthesis, "user_id": user_id, "session_code": session_code}
