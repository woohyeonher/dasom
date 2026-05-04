"""
In-memory session store and SSE fan-out for Dasom.

Public API
──────────
create_session()                        → session code (str)
get_session(code)                       → Session | None
join_session(code, user_id)             → UserState
get_user_state(code, user_id)           → UserState | None
set_tone(code, user_id, tone)
append_history(code, user_id, message)
update_structured_output(code, user_id, **fields)
both_ready(code)                        → bool
mark_intake_complete(code, user_id)

SSE (async)
───────────
register_intake_sse(code, user_id)      → asyncio.Queue
unregister_intake_sse(code, user_id, q)
register_simulation_sse(code)           → asyncio.Queue
unregister_simulation_sse(code, q)
broadcast_intake(code, user_id, msg)    async
broadcast_simulation(code, msg)         async
"""

from __future__ import annotations

import asyncio
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_TONES = {"warm", "neutral", "direct"}

CONFIDENCE_LEVELS = ["Low", "Low-Medium", "Medium", "Medium-High", "High"]
CONFIDENCE_READY = {"Medium-High", "High"}

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6

# ── State dataclasses ─────────────────────────────────────────────────────────


@dataclass
class StructuredOutput:
    """5W1H facts + emotional analysis produced by the Emotion Agent."""
    who: str | None = None
    when: str | None = None
    where: str | None = None
    what: str | None = None
    why: str | None = None
    how: str | None = None
    emotional_labels: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)
    # Per-field confidence populated by the emotion agent
    field_confidence: dict[str, str] = field(default_factory=dict)
    overall_confidence: str = "Low"


@dataclass
class UserState:
    user_id: str
    tone: str | None = None          # "warm" | "neutral" | "direct"
    history: list[dict] = field(default_factory=list)
    structured_output: StructuredOutput = field(default_factory=StructuredOutput)
    ready: bool = False              # True once overall_confidence in CONFIDENCE_READY
    intake_complete: bool = False    # True only when [INTAKE_COMPLETE] marker confirmed


@dataclass
class Session:
    code: str
    created_at: datetime
    users: dict[str, UserState] = field(default_factory=dict)
    # Per-user intake SSE queues (user_id → [queue, ...])
    intake_queues: dict[str, list[asyncio.Queue]] = field(default_factory=dict)
    # Simulation SSE queues broadcast to every registered connection
    simulation_queues: list[asyncio.Queue] = field(default_factory=list)
    # Simulation transcript: list of {"speaker": "A"|"B", "text": str}
    simulation_transcript: list[dict] = field(default_factory=list)
    # Full mediator synthesis text (produced after simulation ends)
    synthesis: str = ""
    # Guards against starting the simulation more than once (set by mark_simulation_started)
    simulation_started: bool = False


# ── In-memory store ───────────────────────────────────────────────────────────

_sessions: dict[str, Session] = {}


# ── Session lifecycle ─────────────────────────────────────────────────────────


def _generate_code() -> str:
    while True:
        code = "".join(random.choices(_CODE_CHARS, k=_CODE_LENGTH))
        if code not in _sessions:
            return code


def create_session() -> str:
    """Create a new session and return its 6-character code."""
    code = _generate_code()
    _sessions[code] = Session(code=code, created_at=datetime.now(timezone.utc))
    return code


def get_session(code: str) -> Session | None:
    return _sessions.get(code)


def join_session(code: str, user_id: str) -> UserState:
    """
    Add user_id to an existing session.  Idempotent — safe to call again
    if the user reconnects; returns the existing UserState if present.
    """
    session = _sessions.get(code)
    if session is None:
        raise KeyError(f"Session '{code}' not found")
    if user_id not in session.users:
        session.users[user_id] = UserState(user_id=user_id)
    return session.users[user_id]


def get_user_state(code: str, user_id: str) -> UserState | None:
    session = _sessions.get(code)
    if session is None:
        return None
    return session.users.get(user_id)


# ── User state mutations ──────────────────────────────────────────────────────


def set_tone(code: str, user_id: str, tone: str) -> None:
    if tone not in VALID_TONES:
        raise ValueError(f"tone must be one of {VALID_TONES}")
    user = _require_user(code, user_id)
    user.tone = tone


def append_history(code: str, user_id: str, message: dict) -> None:
    """Append one message dict (with 'role' and 'content' keys) to history."""
    user = _require_user(code, user_id)
    user.history.append(message)


def update_structured_output(code: str, user_id: str, **kwargs: Any) -> None:
    """
    Update any fields on the user's StructuredOutput.  After each update,
    the ready flag is recomputed from overall_confidence.

    Accepted kwargs: who, when, where, what, why, how, emotional_labels,
                     needs, field_confidence, overall_confidence
    """
    user = _require_user(code, user_id)
    out = user.structured_output
    for key, value in kwargs.items():
        if not hasattr(out, key):
            raise ValueError(f"StructuredOutput has no field '{key}'")
        setattr(out, key, value)
    user.ready = out.overall_confidence in CONFIDENCE_READY


def mark_intake_complete(code: str, user_id: str) -> None:
    """Mark a user's intake as fully complete (called only when [INTAKE_COMPLETE] fires)."""
    user = _require_user(code, user_id)
    user.intake_complete = True


def both_ready(code: str) -> bool:
    """
    True when exactly two users have joined and BOTH have completed intake
    (intake_complete=True, set only when [INTAKE_COMPLETE] confirmed).

    Intentionally does NOT use user.ready — that flag reflects confidence level
    and can become True mid-intake, which would prematurely start the simulation.
    """
    session = _sessions.get(code)
    if session is None:
        print(f"BOTH READY CHECK session={code}: session not found -> False", flush=True)
        return False
    users = session.users
    statuses = {uid: u.intake_complete for uid, u in users.items()}
    result = len(users) == 2 and all(statuses.values())
    print(
        f"BOTH READY CHECK session={code}: users={list(statuses.keys())} "
        f"intake_complete={statuses} -> {result}",
        flush=True,
    )
    return result


# ── Simulation transcript + synthesis ─────────────────────────────────────────


def append_simulation_turn(code: str, speaker: str, text: str) -> None:
    """Append one simulation turn to the session's persistent transcript."""
    _require_session(code).simulation_transcript.append(
        {"speaker": speaker, "text": text}
    )


def get_simulation_transcript(code: str) -> list[dict]:
    """Return a copy of the simulation transcript."""
    session = _sessions.get(code)
    return list(session.simulation_transcript) if session else []


def store_synthesis(code: str, text: str) -> None:
    """Store the final mediator synthesis for retrieval by both users."""
    _require_session(code).synthesis = text


def get_synthesis(code: str) -> str:
    """Return the stored synthesis text, or '' if not yet produced."""
    session = _sessions.get(code)
    return session.synthesis if session else ""


def mark_simulation_started(code: str) -> bool:
    """
    Atomically transition simulation_started from False → True.
    Returns True if this call made the transition (caller should start the simulation).
    Returns False if already started (caller should skip — another client beat them to it).
    Safe in asyncio's single-threaded event loop: no actual race condition possible.
    """
    session = _sessions.get(code)
    if session is None or session.simulation_started:
        return False
    session.simulation_started = True
    return True


# ── SSE fan-out ───────────────────────────────────────────────────────────────


def register_intake_sse(code: str, user_id: str) -> asyncio.Queue:
    """Return a new queue that will receive messages for this user's intake stream."""
    session = _require_session(code)
    q: asyncio.Queue = asyncio.Queue()
    session.intake_queues.setdefault(user_id, []).append(q)
    return q


def unregister_intake_sse(code: str, user_id: str, queue: asyncio.Queue) -> None:
    session = _sessions.get(code)
    if session is None:
        return
    queues = session.intake_queues.get(user_id, [])
    try:
        queues.remove(queue)
    except ValueError:
        pass


def register_simulation_sse(code: str) -> asyncio.Queue:
    """Return a new queue that will receive all simulation broadcast messages."""
    session = _require_session(code)
    q: asyncio.Queue = asyncio.Queue()
    session.simulation_queues.append(q)
    return q


def unregister_simulation_sse(code: str, queue: asyncio.Queue) -> None:
    session = _sessions.get(code)
    if session is None:
        return
    try:
        session.simulation_queues.remove(queue)
    except ValueError:
        pass


async def broadcast_intake(code: str, user_id: str, message: Any) -> None:
    """Put message into every intake queue registered for user_id."""
    session = _sessions.get(code)
    if session is None:
        return
    for q in list(session.intake_queues.get(user_id, [])):
        await q.put(message)


async def broadcast_simulation(code: str, message: Any) -> None:
    """Put message into every simulation queue registered for the session."""
    session = _sessions.get(code)
    if session is None:
        return
    for q in list(session.simulation_queues):
        await q.put(message)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _require_session(code: str) -> Session:
    session = _sessions.get(code)
    if session is None:
        raise KeyError(f"Session '{code}' not found")
    return session


def _require_user(code: str, user_id: str) -> UserState:
    session = _require_session(code)
    user = session.users.get(user_id)
    if user is None:
        raise KeyError(f"User '{user_id}' not in session '{code}'")
    return user


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio as _asyncio
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def _assert(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(f"FAIL: {label}")
        print(f"  ok  {label}")

    print("── session creation ──────────────────────────────────────────")

    code = create_session()
    _assert(len(code) == _CODE_LENGTH, f"code is {_CODE_LENGTH} chars")
    _assert(all(c in _CODE_CHARS for c in code), "code is alphanumeric uppercase")
    _assert(get_session(code) is not None, "get_session returns Session")
    _assert(get_session("XXXXXX") is None, "unknown code returns None")

    # Second session gets a different code
    code2 = create_session()
    _assert(code != code2, "two sessions get distinct codes")

    print("\n── join & idempotency ────────────────────────────────────────")

    ua = join_session(code, "user_a")
    ub = join_session(code, "user_b")
    _assert(ua.user_id == "user_a", "user_a stored")
    _assert(ub.user_id == "user_b", "user_b stored")
    _assert(join_session(code, "user_a") is ua, "join is idempotent")

    print("\n── tone ──────────────────────────────────────────────────────")

    set_tone(code, "user_a", "warm")
    set_tone(code, "user_b", "direct")
    _assert(get_user_state(code, "user_a").tone == "warm", "user_a tone=warm")
    _assert(get_user_state(code, "user_b").tone == "direct", "user_b tone=direct")

    try:
        set_tone(code, "user_a", "aggressive")
        raise AssertionError("FAIL: invalid tone should raise")
    except ValueError:
        print("  ok  invalid tone raises ValueError")

    print("\n── conversation history ──────────────────────────────────────")

    append_history(code, "user_a", {"role": "user", "content": "He never listens."})
    append_history(code, "user_a", {"role": "assistant", "content": "That sounds frustrating."})
    _assert(len(get_user_state(code, "user_a").history) == 2, "2 messages in history")
    _assert(get_user_state(code, "user_b").history == [], "user_b history still empty")

    print("\n── structured output & readiness ─────────────────────────────")

    update_structured_output(code, "user_a",
        who="my partner",
        what="dismissed my feelings",
        overall_confidence="Low",
    )
    out = get_user_state(code, "user_a").structured_output
    _assert(out.who == "my partner", "who stored")
    _assert(out.what == "dismissed my feelings", "what stored")
    _assert(out.overall_confidence == "Low", "confidence=Low")
    _assert(get_user_state(code, "user_a").ready is False, "not ready at Low")

    update_structured_output(code, "user_a", overall_confidence="Medium-High")
    _assert(get_user_state(code, "user_a").ready is True, "ready at Medium-High")

    update_structured_output(code, "user_a",
        emotional_labels=["frustrated", "hurt"],
        needs=["acknowledgment", "connection"],
        field_confidence={"who": "High", "what": "Medium-High"},
    )
    out = get_user_state(code, "user_a").structured_output
    _assert(out.emotional_labels == ["frustrated", "hurt"], "emotional_labels stored")
    _assert(out.needs == ["acknowledgment", "connection"], "needs stored")
    _assert(out.field_confidence["who"] == "High", "field_confidence stored")

    try:
        update_structured_output(code, "user_a", nonexistent_field="oops")
        raise AssertionError("FAIL: unknown field should raise")
    except ValueError:
        print("  ok  unknown field raises ValueError")

    print("\n── both_ready ────────────────────────────────────────────────")

    _assert(both_ready(code) is False, "not both ready — neither intake_complete")
    mark_intake_complete(code, "user_a")
    _assert(both_ready(code) is False, "not both ready — only user_a complete")
    mark_intake_complete(code, "user_b")
    _assert(both_ready(code) is True, "both ready when both intake_complete=True")
    _assert(both_ready("XXXXXX") is False, "unknown session → False")

    print("\n── SSE queue registration ────────────────────────────────────")

    q_sim = register_simulation_sse(code)
    _assert(q_sim in get_session(code).simulation_queues, "simulation queue registered")
    unregister_simulation_sse(code, q_sim)
    _assert(q_sim not in get_session(code).simulation_queues, "simulation queue unregistered")
    unregister_simulation_sse(code, q_sim)   # idempotent — must not raise

    q_in = register_intake_sse(code, "user_a")
    _assert(q_in in get_session(code).intake_queues["user_a"], "intake queue registered")
    unregister_intake_sse(code, "user_a", q_in)
    _assert(q_in not in get_session(code).intake_queues.get("user_a", []), "intake queue unregistered")

    print("\n── SSE broadcast (async) ─────────────────────────────────────")

    async def _test_broadcast() -> None:
        q1 = register_simulation_sse(code)
        q2 = register_simulation_sse(code)
        await broadcast_simulation(code, {"type": "turn", "speaker": "A", "text": "hello"})
        m1 = await _asyncio.wait_for(q1.get(), timeout=1.0)
        m2 = await _asyncio.wait_for(q2.get(), timeout=1.0)
        _assert(m1 == m2, "both queues received identical message")
        _assert(m1["speaker"] == "A", "message content preserved")
        unregister_simulation_sse(code, q1)
        unregister_simulation_sse(code, q2)

        qi = register_intake_sse(code, "user_a")
        await broadcast_intake(code, "user_a", {"type": "token", "text": "..."})
        mi = await _asyncio.wait_for(qi.get(), timeout=1.0)
        _assert(mi["type"] == "token", "intake broadcast delivered")
        unregister_intake_sse(code, "user_a", qi)

    _asyncio.run(_test_broadcast())

    print("\n── error cases ───────────────────────────────────────────────")

    try:
        join_session("XXXXXX", "user")
        raise AssertionError("FAIL: should raise on unknown session")
    except KeyError:
        print("  ok  join unknown session raises KeyError")

    try:
        set_tone("XXXXXX", "user", "warm")
        raise AssertionError("FAIL: should raise on unknown session")
    except KeyError:
        print("  ok  set_tone unknown session raises KeyError")

    try:
        append_history(code, "user_nobody", {"role": "user", "content": "hi"})
        raise AssertionError("FAIL: should raise on unknown user")
    except KeyError:
        print("  ok  append_history unknown user raises KeyError")

    print("\n✓ All tests passed.")
