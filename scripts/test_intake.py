"""
scripts/test_intake.py
End-to-end intake test — two concurrent users, each running in their own thread.

Architecture:
  - SSE reader thread per user: GET /stream/intake/.., puts events into a queue
  - Main thread per user: POST messages, drain queue for each turn's events
  - Both users run concurrently via threading

Run: backend\\.venv\\Scripts\\python.exe scripts\\test_intake.py
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time

import requests

BASE = "http://localhost:8000"

TURNS_A = [
    (
        "We had a huge fight last Saturday night at home after dinner. My partner Alex "
        "came home two hours late from work again without texting me. I cooked dinner and "
        "waited over an hour. When I asked why they hadn't texted, Alex got defensive and "
        "said I was being controlling. I felt completely invisible and disrespected. "
        "This has been a pattern for six months — late nights, no texts, defensiveness "
        "whenever I bring it up. I am exhausted and feel like I am not a priority."
    ),
    (
        "Yes, that is exactly right. We have been together for three years. I need Alex to "
        "communicate with me — even a five-second text would make a difference. It is not "
        "about the lateness, it is about feeling like I matter enough to warrant a message. "
        "The relationship used to feel equal but now it feels one-sided. I am always "
        "waiting and Alex is always somewhere else mentally."
    ),
    (
        "The fight escalated when I tried to stay calm but Alex accused me of nagging, so "
        "I walked away to avoid a screaming match. We have barely spoken since Saturday. "
        "At the core I need to feel valued and seen. When Alex disappears without a word "
        "it triggers my fear of abandonment from a past relationship. I am not trying to "
        "control Alex, I am trying to feel safe and like we are a team."
    ),
    (
        "You have captured it really well. I feel hurt, scared, and lonely — the anger is "
        "just what shows when I am scared. My deepest need is for Alex to see me as a "
        "partner who deserves basic communication and consideration. I want to feel like "
        "we are a team. Yes, your reflection is accurate — that is exactly how it feels."
    ),
]

TURNS_B = [
    (
        "My partner Sam and I had a serious argument last Friday evening at our apartment. "
        "Sam had been messaging all week about planning dinner together. When Friday came "
        "I stayed late at the office to finish a critical project deadline — I thought Sam "
        "understood how important this period has been. I got home around 9pm and Sam was "
        "upset I hadn't called ahead. I felt blindsided. The argument got intense and Sam "
        "said I never show up. That felt deeply unfair given everything I do for us."
    ),
    (
        "Yes, you understood correctly. We have been together for two years. I need Sam to "
        "trust that when I am working late it is not a rejection of our relationship. I "
        "feel like no matter how hard I try to balance work and us, it is never enough. "
        "I don't feel appreciated for the stability and future I provide. My efforts go "
        "unseen while my absences are used against me."
    ),
    (
        "The hardest part is I do love Sam and want to be present. But I feel trapped: "
        "focus on work and I fail as a partner, drop work and I fail our future. I need "
        "Sam to acknowledge what I contribute and give me more flexibility at crunch times. "
        "I am also hurt that Sam jumped straight to accusations instead of asking what "
        "happened. Underneath everything I need to feel accepted, not constantly evaluated."
    ),
    (
        "That reflection is accurate. I feel overwhelmed, misunderstood, and unappreciated. "
        "My core need is to feel that my love is recognized even when I cannot be physically "
        "present. I need Sam to understand that my absence on Friday was sacrifice, not "
        "indifference. Yes, you have summarized it correctly. I need us to find a way to "
        "talk that does not turn into a trial where I am always the defendant."
    ),
]


def _tokens(events: list[dict]) -> str:
    return "".join(e.get("data", {}).get("text", "") for e in events if e.get("type") == "token")


def _reasoning(events: list[dict]) -> str | None:
    evt = next((e for e in events if e.get("type") == "reasoning"), None)
    return evt["data"].get("summary") if evt else None


def _analysis(events: list[dict]) -> dict | None:
    evt = next((e for e in events if e.get("type") == "analysis_update"), None)
    return evt["data"] if evt else None


def _has_done(events: list[dict]) -> bool:
    return any(e.get("type") == "done" for e in events)


def sse_reader_thread(url: str, event_queue: queue.Queue) -> None:
    """Runs in a daemon thread; reads SSE stream and puts parsed dicts into queue."""
    try:
        with requests.get(url, stream=True, timeout=None) as resp:
            if resp.status_code != 200:
                event_queue.put({"type": "_http_error", "status": resp.status_code})
                return
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or raw_line.startswith(":"):
                    continue
                if raw_line.startswith("data:"):
                    try:
                        payload = json.loads(raw_line[5:].strip())
                    except Exception:
                        payload = {"type": "_raw", "data": {"text": raw_line}}
                    event_queue.put(payload)
                    if payload.get("type") == "done":
                        return
    except Exception as exc:
        event_queue.put({"type": "_reader_error", "data": {"error": str(exc)}})


def drain_until(event_queue: queue.Queue, target: str, per_get_timeout: float = 5.0, total_timeout: float = 120.0) -> list[dict]:
    """Collect events from queue until target type arrives or total_timeout exceeded."""
    collected: list[dict] = []
    deadline = time.time() + total_timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        try:
            evt = event_queue.get(timeout=min(per_get_timeout, remaining))
            collected.append(evt)
            if evt.get("type") == target:
                break
        except queue.Empty:
            break
    return collected


def drain_bonus(event_queue: queue.Queue, wait: float = 0.5) -> list[dict]:
    """Drain any events that arrive within `wait` seconds after the main drain."""
    collected = []
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            collected.append(event_queue.get_nowait())
        except queue.Empty:
            time.sleep(0.05)
    return collected


def run_user(label: str, code: str, user_id: str, turns: list[str], results: dict) -> None:
    """Runs as a thread. Writes final ready status into results[label]."""
    sse_url = f"{BASE}/stream/intake/{code}/{user_id}"
    event_queue: queue.Queue = queue.Queue()

    t = threading.Thread(target=sse_reader_thread, args=(sse_url, event_queue), daemon=True)
    t.start()

    print(f"\n{'='*60}", flush=True)
    print(f"  {label}  user_id={user_id}", flush=True)
    print(f"{'='*60}", flush=True)

    # Opening message
    print(f"[{label}] Waiting for opening message + reasoning...", flush=True)
    opening = drain_until(event_queue, "reasoning", total_timeout=90)
    errors = [e for e in opening if "error" in e.get("type", "")]
    if errors:
        print(f"[{label}] FATAL ERROR in opening: {errors}", flush=True)
        results[label] = False
        return

    tok = _tokens(opening)
    rsn = _reasoning(opening)
    print(f"[{label}] CHAT PANEL opening ({len(tok)} chars):", flush=True)
    print(f"  > {tok[:220].strip()}", flush=True)
    if rsn:
        print(f"[{label}] REASONING PANEL: {rsn[:160]}", flush=True)
    else:
        print(f"[{label}] REASONING PANEL: (none in opening)", flush=True)

    # Turns
    current_confidence = "Low"
    intake_complete = False

    for i, msg in enumerate(turns, 1):
        print(f"\n[{label}] ── Turn {i} ──────────────────────────────────", flush=True)
        print(f"  User: {msg[:100]}...", flush=True)

        r = requests.post(
            f"{BASE}/intake/message",
            json={"session_code": code, "user_id": user_id, "message": msg},
            timeout=60,
        )
        if r.status_code != 200:
            print(f"[{label}] ERROR {r.status_code}: {r.text[:200]}", flush=True)
            continue
        post = r.json()

        # Wait for analysis_update, then drain any bonus events
        turn_evts = drain_until(event_queue, "analysis_update", total_timeout=120)
        turn_evts += drain_bonus(event_queue, wait=0.5)

        tok = _tokens(turn_evts)
        rsn = _reasoning(turn_evts)
        ana = _analysis(turn_evts)
        done_flag = _has_done(turn_evts) or post.get("done", False)

        # CHAT PANEL
        if tok:
            print(f"[{label}] CHAT PANEL ({len(tok)} chars):", flush=True)
            print(f"  > {tok[:220].strip()}", flush=True)
        else:
            print(f"[{label}] CHAT PANEL: WARNING — no tokens", flush=True)

        # REASONING PANEL
        if rsn:
            print(f"[{label}] REASONING PANEL: {rsn[:160]}", flush=True)
        else:
            print(f"[{label}] REASONING PANEL: WARNING — no reasoning event", flush=True)

        # ANALYSIS PANEL
        if ana:
            current_confidence = ana.get("overall_confidence", "Low")
            print(f"[{label}] ANALYSIS PANEL:", flush=True)
            print(f"  confidence badge : {current_confidence}", flush=True)
            print(f"  who    : {ana.get('who')}", flush=True)
            print(f"  when   : {ana.get('when')}", flush=True)
            print(f"  where  : {ana.get('where')}", flush=True)
            print(f"  what   : {ana.get('what')}", flush=True)
            print(f"  why    : {ana.get('why')}", flush=True)
            print(f"  how    : {ana.get('how')}", flush=True)
            print(f"  emotions : {ana.get('emotional_labels', [])}", flush=True)
            print(f"  needs    : {ana.get('needs', [])}", flush=True)
        else:
            print(f"[{label}] ANALYSIS PANEL: WARNING — no analysis_update", flush=True)

        ready = post.get("ready", False)
        print(f"[{label}] server → ready={ready}, done={post.get('done', False)}, confidence={current_confidence}", flush=True)
        if not ready and current_confidence not in {"Medium-High", "High"}:
            print(f"[{label}] HARD BLOCK: confidence below Medium-High, cannot proceed", flush=True)

        if done_flag:
            print(f"\n[{label}] INTAKE COMPLETE at turn {i} — confidence={current_confidence}", flush=True)
            intake_complete = True
            break

    ready_final = intake_complete or current_confidence in {"Medium-High", "High"}
    print(f"\n[{label}] Final confidence={current_confidence}, ready={ready_final}", flush=True)
    results[label] = ready_final


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    r = requests.post(f"{BASE}/session/create")
    code = r.json()["session_code"]
    r2 = requests.post(f"{BASE}/session/join/{code}")
    print(f"Session: {code}")
    print(f"User B joined: {r2.json()}")
    print(f"\nRunning both users concurrently (3–5 min — calling Gemini 2.5 Flash)...\n")

    results: dict = {}
    ta = threading.Thread(target=run_user, args=("TAB-A", code, "a", TURNS_A, results))
    tb = threading.Thread(target=run_user, args=("TAB-B", code, "b", TURNS_B, results))
    ta.start()
    tb.start()
    ta.join()
    tb.join()

    print("\n" + "=" * 60)
    print("  SUBSTEP 3 RESULTS")
    print("=" * 60)
    for label in ["TAB-A", "TAB-B"]:
        status = results.get(label)
        print(f"  {label}: {'READY' if status else 'NOT READY' if status is False else 'NO RESULT'}")


if __name__ == "__main__":
    main()
