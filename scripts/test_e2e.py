"""
scripts/test_e2e.py

Full end-to-end test for the Dasom API — no browser, no internal imports.
Drives the complete flow through HTTP endpoints against a live backend.

Steps
-----
1  GET  /health
2  POST /session/create
3  POST /session/join/{code}         (User B)
4  User A (Alex)  — SSE intake + 3 messages
5  both_ready guard — confirm simulation does NOT start with only A ready
6  User B (Jordan) — SSE intake + 3 messages
7  Connect both clients to /stream/simulation, wait for 'done' on both
8  GET  /result/{code}/a  and  /result/{code}/b

Usage
-----
    python scripts/test_e2e.py
    python scripts/test_e2e.py --base http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

# -- Config --------------------------------------------------------------------

DEFAULT_BASE  = "http://localhost:8000"
TIMEOUT_HTTP  = 360.0   # max seconds to wait for one intake-turn POST (allows 2× 90s 429 retries)
TIMEOUT_OPEN  = 360.0   # max seconds to wait for opening-message turn_complete (allows 429 retries)
TIMEOUT_DRAIN =   1.5   # settle window after POST before draining SSE queue
TIMEOUT_GUARD =   8.0   # seconds to verify simulation does NOT fire prematurely
TIMEOUT_SIM   = 600.0   # max seconds for entire simulation to complete

# After the 3 substantive messages the agent delivers a final reflection and
# waits for the user to confirm before outputting [INTAKE_COMPLETE].
# This generic confirmation triggers that closing turn.
CONFIRMATION_MSG = (
    "Yes, that's exactly right. You've understood everything correctly. "
    "That is a perfect summary of what I shared."
)

# -- Scenario messages ---------------------------------------------------------

ALEX_MESSAGES = [
    (
        "Last Saturday, October 12th, Jordan was supposed to be home by 7pm. "
        "I had spent the entire afternoon cooking — I made Jordan's favorite, pasta carbonara "
        "from scratch, bought fresh ingredients and everything. I had the table set by 6:45. "
        "By 7:30 I started texting. No response. By 8:30 I called twice. Voicemail. Jordan "
        "finally walked through the door at 9:47pm, smelling like a bar, saying it was 'a work "
        "thing that ran late.' No apology, no explanation, just walked in and turned on the TV. "
        "I didn't yell. I just went quiet and started cleaning up the cold food. Jordan asked "
        "what was wrong and I said 'nothing' because I didn't trust myself to speak. We went "
        "to bed without saying a word to each other."
    ),
    (
        "In the moment I felt humiliated. I had put so much effort into that evening and it "
        "felt like Jordan didn't even register that it existed. But underneath that I felt "
        "scared — really scared. This wasn't the first time. In the past three months Jordan "
        "has missed our Tuesday dinners four times, cancelled our weekend trip to Vermont last "
        "minute because of work, and has been coming home past 9pm almost every weekday. I keep "
        "telling myself it's a busy season but I'm starting to wonder if I'm being naive. I feel "
        "lonely in this relationship. I feel like I'm sharing an apartment with someone who used "
        "to love me. I'm angry but mostly I'm grieving something I can't name yet."
    ),
    (
        "I need Jordan to understand that it's not about the pasta or even about Saturday night "
        "specifically. It's about a pattern I've been watching build for months and being too "
        "afraid to name it. I need to feel like Jordan chooses me sometimes — not just when it's "
        "convenient. I need a simple text when plans change. I need our Tuesday dinners to be "
        "protected. But more than any of that I need an honest conversation about where we are "
        "because I would rather have a hard conversation now than wake up in a year and realize "
        "we became strangers. I love Jordan. That's why this hurts so much."
    ),
]

JORDAN_MESSAGES = [
    (
        "Last Saturday was supposed to be a normal evening. But at 4:30pm my manager called "
        "and told me our biggest client, Mercer & Associates, had flagged a critical error in "
        "the quarterly report we submitted. If we didn't fix it and resubmit by Monday morning "
        "we risked losing the account — that's a $2.3 million contract. I had no choice but to "
        "stay. I texted my work group chat but in the chaos I completely forgot to text Alex. "
        "By the time I looked at my phone it was 8pm and I had three missed calls. I finished "
        "at 9:30, stopped for one drink with my colleague Sarah to decompress, and came home. "
        "When I walked in Alex was completely shut down — wouldn't look at me, one word answers. "
        "I tried to explain but got a wall of silence. I gave up and went to bed because I had "
        "nothing left."
    ),
    (
        "I felt like I couldn't win. I had just spent five hours saving a $2.3 million account "
        "for my company, I was exhausted and stressed and carrying a lot of guilt about forgetting "
        "to text, and instead of any understanding I walked into punishment. I know I should have "
        "texted. I know that. But I was in crisis mode and I'm human and I made a mistake. The "
        "silence from Alex felt like a verdict — like I had been tried and convicted without a "
        "chance to speak. I felt invisible in a different way. Like my stress, my workload, my "
        "efforts to build a future for us financially just don't count. I've been killing myself "
        "at work for months and I come home to silence and cold shoulders and I don't know how "
        "much longer I can keep going like this."
    ),
    (
        "I need Alex to trust me when I say work emergencies are real. I'm not choosing work "
        "over Alex — I'm trying to build something stable for both of us. I need some grace when "
        "I make a mistake under pressure. But I also want to be honest — I know I've been absent. "
        "I know Tuesday dinners have slipped. I know Vermont was a real loss and I feel terrible "
        "about it. I miss us too. I miss who we were before this promotion consumed everything. "
        "I think I've been avoiding talking about how stretched I am because I don't want to seem "
        "weak. I need us to find a way to talk about this that doesn't turn into a trial. I want "
        "to fix this. I'm just terrified I've already broken something I can't name."
    ),
]

# -- Result tracker ------------------------------------------------------------

_results: list[tuple[bool, str]] = []


def _step(label: str, passed: bool, note: str = "") -> bool:
    _results.append((passed, label))
    tag   = "PASS" if passed else "FAIL"
    extra = f"  ({note})" if note else ""
    print(f"  [{tag}] {label}{extra}", flush=True)
    return passed


# -- SSE helpers ---------------------------------------------------------------

async def _sse_reader(url: str, q: asyncio.Queue) -> None:
    """
    Background task — streams SSE from url, puts parsed event dicts into q.
    Stops when a 'done' event is received or the connection closes.
    On network/HTTP error puts {"type": "_error", "detail": str} into q.
    """
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("GET", url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:].strip())
                    except json.JSONDecodeError:
                        continue
                    await q.put(event)
                    if event.get("type") == "done":
                        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await q.put({"type": "_error", "detail": str(exc)})


async def _wait_for(q: asyncio.Queue, event_type: str, timeout: float) -> dict | None:
    """
    Consume events from q until one with matching type is found.
    Discards non-matching events. Returns the matching event or None on timeout.
    '_error' events immediately return None.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            event = await asyncio.wait_for(q.get(), timeout=min(remaining, 2.0))
        except asyncio.TimeoutError:
            continue
        if event.get("type") == "_error":
            print(f"  [SSE error] {event.get('detail', '')}", flush=True)
            return None
        if event.get("type") == event_type:
            return event


async def _drain(q: asyncio.Queue, settle: float = TIMEOUT_DRAIN) -> list[dict]:
    """Wait `settle` seconds then drain everything currently in the queue."""
    await asyncio.sleep(settle)
    events: list[dict] = []
    while True:
        try:
            events.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


# -- Intake helper -------------------------------------------------------------

async def run_intake(
    client: httpx.AsyncClient,
    base: str,
    code: str,
    user_id: str,
    messages: list[str],
    label: str,
) -> bool:
    """
    Run a complete intake conversation for one user via the HTTP API.
    Opens an SSE stream (which triggers the opening message), then sends
    each message via POST /intake/message.  Returns True iff intake completed.
    """
    bar = "-" * max(0, 60 - len(label))
    print(f"\n-- {label} intake {bar}", flush=True)

    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_sse_reader(f"{base}/stream/intake/{code}/{user_id}", q))

    # Wait for the agent's opening message (signals SSE is live and start_intake ran)
    print(f"  waiting for opening message (up to {TIMEOUT_OPEN:.0f}s)…", flush=True)
    opening = await _wait_for(q, "turn_complete", TIMEOUT_OPEN)
    if not _step(f"{label}: opening message (turn_complete received)", opening is not None):
        task.cancel()
        return False

    intake_done  = False
    final_conf   = "Low"
    final_ready  = False

    # After the 3 substantive messages the emotion agent delivers a final reflection
    # and waits for confirmation before emitting [INTAKE_COMPLETE].  We append up
    # to MAX_CONFIRMATIONS generic confirmation messages so the loop always drives
    # the agent to completion without hardcoding the exact turn count.
    MAX_CONFIRMATIONS = 3
    all_messages = list(messages)

    i = 0
    confirmations_sent = 0
    sse_done_in_loop   = False   # True if 'done' SSE event arrives during the main loop
    while i < len(all_messages):
        msg = all_messages[i]
        i += 1
        turn_label = f"turn {i}"
        if i > len(messages):
            turn_label = f"confirmation {confirmations_sent}"

        print(f"  {turn_label} — sending ({len(msg)} chars)…", flush=True)

        # Retry once on connection errors (stale keep-alive after long 429-retry turn).
        resp = None
        for _attempt in range(2):
            try:
                resp = await client.post(
                    f"{base}/intake/message",
                    json={"session_code": code, "user_id": user_id, "message": msg},
                    timeout=TIMEOUT_HTTP,
                )
                break
            except Exception as exc:
                exc_desc = f"{type(exc).__name__}: {exc!r}"
                if _attempt == 0:
                    print(f"  [retry] {turn_label} POST failed ({exc_desc}), retrying…", flush=True)
                    await asyncio.sleep(1.0)
                else:
                    _step(f"{label} {turn_label}: POST /intake/message", False, exc_desc)
                    task.cancel()
                    return False

        if not _step(
            f"{label} {turn_label}: POST /intake/message → 200",
            resp.status_code == 200,
            f"got HTTP {resp.status_code}",
        ):
            task.cancel()
            return False

        body        = resp.json()
        intake_done = body.get("done", False)
        final_ready = body.get("ready", False)

        # Drain SSE events that arrived during this turn's processing
        events = await _drain(q)
        analysis = next(
            (e["data"] for e in reversed(events) if e.get("type") == "analysis_update"),
            {},
        )
        if analysis:
            final_conf = analysis.get("overall_confidence", final_conf)

        _step(
            f"{label} {turn_label}: complete",
            True,
            f"done={intake_done}  ready={final_ready}  confidence={final_conf}",
        )

        if intake_done:
            # Check now — 'done' SSE event may have arrived during this turn's drain.
            sse_done_in_loop = any(e.get("type") == "done" for e in events)
            break

        # Substantive messages exhausted and not yet done: the agent is waiting
        # for the user to confirm its final reflection.  Append a confirmation.
        if i >= len(all_messages) and confirmations_sent < MAX_CONFIRMATIONS:
            confirmations_sent += 1
            all_messages.append(CONFIRMATION_MSG)

    if not _step(f"{label}: intake completed (server returned done=True)", intake_done):
        task.cancel()
        return False

    _step(
        f"{label}: confidence reached Medium-High or High",
        final_conf in {"Medium-High", "High"},
        f"got '{final_conf}'",
    )

    _step(f"{label}: ready=True in HTTP response", final_ready)

    # Confirm SSE 'done' event arrived.  It may have been drained already inside
    # the main loop (sse_done_in_loop) or arrive in the settling window now.
    leftover = await _drain(q, settle=2.0)
    sse_done = sse_done_in_loop or any(e.get("type") == "done" for e in leftover)
    if not sse_done:
        evt = await _wait_for(q, "done", timeout=10.0)
        sse_done = evt is not None
    _step(f"{label}: SSE 'done' event received", sse_done)

    if not task.done():
        task.cancel()
    return True


# -- Main test orchestration ---------------------------------------------------

async def run_all(base: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}")
    print(f"  Dasom end-to-end test  ·  {base}")
    print(f"{bar}\n")

    async with httpx.AsyncClient(timeout=30.0) as client:

        # -- Step 1: health check -------------------------------------------
        print("-- 1 · Health check ---------------------------------------------------------")
        resp = await client.get(f"{base}/health")
        if not _step("GET /health → 200", resp.status_code == 200, resp.text):
            print("  Backend not reachable — ensure uvicorn is running and retry.", flush=True)
            return

        # -- Step 2: create session -----------------------------------------
        print("\n-- 2 · Create session -------------------------------------------------------")
        resp = await client.post(f"{base}/session/create")
        _step("POST /session/create → 200", resp.status_code == 200)
        code: str = resp.json().get("session_code", "")
        _step(f"session_code received: {code!r}", len(code) == 6)

        # -- Step 3: User B joins -------------------------------------------
        print("\n-- 3 · User B joins ---------------------------------------------------------")
        resp = await client.post(f"{base}/session/join/{code}")
        _step("POST /session/join/{code} → 200", resp.status_code == 200,
              f"got {resp.status_code}")
        join = resp.json()
        _step(
            "User B assigned user_id='b', joined same session as User A",
            join.get("user_id") == "b" and join.get("session_code") == code,
            str(join),
        )
        # Duplicate join attempt must return 409
        resp409 = await client.post(f"{base}/session/join/{code}")
        _step(
            "Second join attempt returns 409 (session full)",
            resp409.status_code == 409,
            f"got {resp409.status_code}",
        )

        # -- Step 4: Alex (User A) completes intake -------------------------
        alex_ok = await run_intake(client, base, code, "a", ALEX_MESSAGES, "Alex (User A)")

        # -- Step 5: both_ready guard ---------------------------------------
        #  Connect User A's simulation SSE client NOW (before Jordan completes).
        #  The simulation must NOT start — both_ready() should return False.
        print("\n-- 5 · both_ready guard (only A complete) -----------------------------------")
        q_a: asyncio.Queue = asyncio.Queue()
        sim_url = f"{base}/stream/simulation/{code}"
        task_a  = asyncio.create_task(_sse_reader(sim_url, q_a))

        print(
            f"  User A connected to simulation SSE — "
            f"waiting {TIMEOUT_GUARD:.0f}s to confirm no premature start…",
            flush=True,
        )

        # Give the SSE connection a moment to register, then listen for any event.
        # A properly guarded both_ready() returns False here → queue stays empty.
        # 'connected' keep-alive events are expected and should be ignored.
        await asyncio.sleep(0.5)  # let the SSE connection establish
        guard_deadline = asyncio.get_event_loop().time() + TIMEOUT_GUARD
        early_sim   = None
        guard_error = None
        while True:
            remaining = guard_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                ev = await asyncio.wait_for(q_a.get(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                continue
            if ev.get("type") == "_error":
                guard_error = ev
                break
            if ev.get("type") == "connected":
                continue  # expected keep-alive — not a premature simulation start
            # Any other real event means simulation fired prematurely
            early_sim = ev
            break

        if guard_error is not None:
            _step(
                "Simulation guard: SSE connection stable",
                False,
                f"transport error: {guard_error.get('detail', '')}",
            )
        elif early_sim is None:
            _step("Simulation did NOT start with only User A ready (both_ready guard)", True)
        else:
            _step(
                "Simulation did NOT start with only User A ready (both_ready guard)",
                False,
                f"premature simulation event: {early_sim}",
            )

        # -- Step 6: Jordan (User B) completes intake -----------------------
        jordan_ok = await run_intake(client, base, code, "b", JORDAN_MESSAGES, "Jordan (User B)")

        # -- Step 7: both clients on simulation SSE; wait for done ----------
        print("\n-- 7 · Simulation -----------------------------------------------------------")

        # Skip expensive 600s wait if we already know both_ready() will be False.
        if not alex_ok or not jordan_ok:
            _step(
                "Simulation skipped: both users must complete intake first",
                False,
                f"alex_ok={alex_ok} jordan_ok={jordan_ok}",
            )
            for t in (task_a,):
                if not t.done():
                    t.cancel()
            print("\n-- 8 · Results (skipped — simulation did not run) -------------------")
            _step("GET /result/{code}/a → 200", False, "simulation did not run")
            _step("GET /result/{code}/b → 200", False, "simulation did not run")
            return

        # If user A's guard-phase SSE connection died, reconnect it BEFORE user B
        # connects so both queues are registered when the simulation starts.
        if task_a.done():
            q_a = asyncio.Queue()
            task_a = asyncio.create_task(_sse_reader(sim_url, q_a))
            print("  User A SSE reconnected (guard-phase connection dropped)", flush=True)
            await asyncio.sleep(0.3)  # let the reconnect register

        # Now connect User B — this connection triggers both_ready() → True → simulation starts.
        q_b: asyncio.Queue = asyncio.Queue()
        task_b = asyncio.create_task(_sse_reader(sim_url, q_b))
        _step(
            "User A SSE registered, User B SSE now connected",
            True,
        )

        print(f"  Waiting up to {TIMEOUT_SIM:.0f}s for simulation to complete…", flush=True)

        # Both queues will receive every broadcast event including 'done'
        done_a, done_b = await asyncio.gather(
            _wait_for(q_a, "done", TIMEOUT_SIM),
            _wait_for(q_b, "done", TIMEOUT_SIM),
        )
        _step("User A simulation SSE: 'done' event received", done_a is not None)
        _step("User B simulation SSE: 'done' event received", done_b is not None)

        # Count what event types each client received
        leftovers_a = await _drain(q_a, settle=0.5)
        leftovers_b = await _drain(q_b, settle=0.5)

        def _event_counts(events: list[dict]) -> str:
            counts: dict[str, int] = {}
            for e in events:
                t = e.get("type", "?")
                counts[t] = counts.get(t, 0) + 1
            return "  ".join(f"{k}:{v}" for k, v in sorted(counts.items()))

        # Print event type summaries (informational)
        print(f"  User A events received: {_event_counts(leftovers_a)}", flush=True)
        print(f"  User B events received: {_event_counts(leftovers_b)}", flush=True)

        # -- Step 8: fetch results ------------------------------------------
        print("\n-- 8 · Results --------------------------------------------------------------")
        resp_a = await client.get(f"{base}/result/{code}/a", timeout=30.0)
        _step(
            "GET /result/{code}/a → 200",
            resp_a.status_code == 200,
            f"got {resp_a.status_code}",
        )

        resp_b = await client.get(f"{base}/result/{code}/b", timeout=30.0)
        _step(
            "GET /result/{code}/b → 200",
            resp_b.status_code == 200,
            f"got {resp_b.status_code}",
        )

        if resp_a.status_code == 200 and resp_b.status_code == 200:
            syn_a = resp_a.json().get("synthesis", "")
            syn_b = resp_b.json().get("synthesis", "")

            _step("User A synthesis non-empty", bool(syn_a.strip()),
                  f"{len(syn_a)} chars")
            _step("User B synthesis non-empty", bool(syn_b.strip()),
                  f"{len(syn_b)} chars")
            _step("Both users receive identical synthesis", syn_a == syn_b)

            # Check that synthesis contains the 6 expected sections
            SECTIONS = [
                "What Happened",
                "What Each Person Felt",
                "Where You Actually Agree",
                "The Core Tension",
                "A Path Forward",
                "Message to",  # covers "A Message to Each Person", "Message to Alex", etc.
            ]
            lower = syn_a.lower()
            found = [s for s in SECTIONS if s.lower() in lower]
            _step(
                f"Synthesis contains all 6 expected sections",
                len(found) >= 6,
                f"found {len(found)}/6: {found}",
            )

            # Print a preview
            print(f"\n  Synthesis preview (first 300 chars):")
            print(f"  {syn_a[:300].strip()!r}")

        # -- Cleanup --------------------------------------------------------
        for t in (task_a, task_b):
            if not t.done():
                t.cancel()


# -- Entry point ---------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Dasom end-to-end test")
    ap.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    args = ap.parse_args()

    t0 = time.time()
    try:
        asyncio.run(run_all(args.base))
    except KeyboardInterrupt:
        print("\n  (interrupted)", flush=True)

    elapsed = time.time() - t0
    bar = "=" * 72
    passed = sum(1 for ok, _ in _results if ok)
    failed = sum(1 for ok, _ in _results if not ok)
    total  = passed + failed

    print(f"\n{bar}")
    print(f"  {passed}/{total} passed · {failed} failed · {elapsed:.1f}s")
    if failed:
        print("  Failed:")
        for ok, label in _results:
            if not ok:
                print(f"    x  {label}")
    print(bar)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
