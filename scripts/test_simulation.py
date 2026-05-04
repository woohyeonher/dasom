"""
scripts/test_simulation.py
End-to-end simulation test.

Flow:
  1. Create session, run minimal intake for both users to reach ready=True
  2. Both "tabs" open simulation SSE stream concurrently (fan-out test)
  3. Monitor all 7 simulation event types across both tabs
  4. Verify mediator intervention, synthesis streaming, and done event
  5. Verify GET /result/{code}/{user_id} returns the synthesis

Run: backend\\.venv\\Scripts\\python.exe scripts\\test_simulation.py
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time

import requests

BASE = "http://localhost:8000"

# Rich single messages that push both users to Medium-High on first turn
MSG_A = (
    "We had a huge fight last Saturday night at home after dinner. My partner Alex "
    "came home two hours late from work again without texting me. I had cooked dinner "
    "and waited over an hour. When I asked why they hadn't texted, Alex got defensive "
    "and said I was being controlling. I felt completely invisible and disrespected. "
    "This has been a pattern for six months — late nights, no texts, defensiveness "
    "whenever I bring it up. I am exhausted and feel like I am not a priority anymore. "
    "We have been together three years and it used to feel equal. I need Alex to "
    "communicate — even a five-second text would make a difference. I need to feel "
    "like I matter enough to warrant a message. The relationship feels one-sided now."
)

MSG_B = (
    "My partner Sam and I had a serious argument last Friday evening at our apartment. "
    "Sam had been messaging all week about planning dinner together. When Friday came I "
    "stayed late at the office to finish a critical project deadline — I thought Sam "
    "understood how important work has been lately. I got home around 9pm and Sam was "
    "upset I hadn't called ahead. I felt blindsided because I thought my work stress "
    "was something Sam knew about and supported. The argument got intense and Sam said "
    "I never show up. That felt deeply unfair. We have been together two years. I need "
    "Sam to trust that working late is not a rejection. I feel unappreciated for the "
    "stability I provide. My efforts go unseen while my absences get magnified. "
    "I need to feel accepted, not constantly evaluated."
)


# ── SSE reader thread ────────────────────────────────────────────────────────────

def sse_reader_thread(url: str, event_queue: queue.Queue, label: str) -> None:
    try:
        with requests.get(url, stream=True, timeout=None) as resp:
            if resp.status_code != 200:
                event_queue.put({"type": "_http_error", "status": resp.status_code, "label": label})
                return
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or raw_line.startswith(":"):
                    continue
                if raw_line.startswith("data:"):
                    try:
                        payload = json.loads(raw_line[5:].strip())
                    except Exception:
                        payload = {"type": "_raw", "data": {"text": raw_line}}
                    payload["_from"] = label
                    event_queue.put(payload)
                    if payload.get("type") == "done":
                        return
    except Exception as exc:
        event_queue.put({"type": "_reader_error", "data": {"error": str(exc)}, "_from": label})


def drain_until(q: queue.Queue, target: str, total_timeout: float = 1200.0) -> list[dict]:
    collected = []
    deadline = time.time() + total_timeout
    while time.time() < deadline:
        try:
            evt = q.get(timeout=5.0)
            collected.append(evt)
            if evt.get("type") == target:
                break
        except queue.Empty:
            continue
    return collected


# ── Intake helper (minimal — just enough to set ready=True) ──────────────────────

def quick_intake(code: str, user_id: str, message: str, label: str) -> bool:
    """Open SSE, wait for opening, send one rich message, confirm ready=True."""
    intake_q: queue.Queue = queue.Queue()
    sse_url = f"{BASE}/stream/intake/{code}/{user_id}"
    t = threading.Thread(target=sse_reader_thread, args=(sse_url, intake_q, label), daemon=True)
    t.start()

    # Wait for reasoning event (end of opening message)
    opening = drain_until(intake_q, "reasoning", total_timeout=90)
    tok = "".join(e.get("data", {}).get("text", "") for e in opening if e.get("type") == "token")
    print(f"[{label}] Opening ({len(tok)} chars): {tok[:120].strip()}...", flush=True)

    # Send the rich intake message (process_turn calls Gemini Flash + analysis — allow 240s)
    r = requests.post(
        f"{BASE}/intake/message",
        json={"session_code": code, "user_id": user_id, "message": message},
        timeout=240,
    )
    if r.status_code != 200:
        print(f"[{label}] ERROR sending message: {r.status_code}", flush=True)
        return False
    post = r.json()

    # Wait for analysis_update
    turn_evts = drain_until(intake_q, "analysis_update", total_timeout=120)
    ana = next((e for e in turn_evts if e.get("type") == "analysis_update"), None)
    confidence = ana["data"].get("overall_confidence", "Low") if ana else "Low"
    ready = post.get("ready", False)
    print(f"[{label}] After turn 1: confidence={confidence}, ready={ready}", flush=True)

    # If not yet ready, send a confirming follow-up message
    if not ready:
        confirm = (
            "Yes, you have understood it correctly. That reflection is accurate — "
            "that is exactly how I feel and what I need. I confirm everything you said."
        )
        r2 = requests.post(
            f"{BASE}/intake/message",
            json={"session_code": code, "user_id": user_id, "message": confirm},
            timeout=240,
        )
        if r2.status_code == 200:
            post2 = r2.json()
            t2_evts = drain_until(intake_q, "analysis_update", total_timeout=120)
            ana2 = next((e for e in t2_evts if e.get("type") == "analysis_update"), None)
            confidence = ana2["data"].get("overall_confidence", confidence) if ana2 else confidence
            ready = post2.get("ready", False)
            print(f"[{label}] After turn 2: confidence={confidence}, ready={ready}", flush=True)

    t.join(timeout=2)
    return ready


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Create session
    r = requests.post(f"{BASE}/session/create")
    code = r.json()["session_code"]
    r2 = requests.post(f"{BASE}/session/join/{code}")
    print(f"Session: {code}  User B joined: {r2.json()['user_id']}")

    # ── Phase 1: Quick intake for both users ────────────────────────────────────
    print(f"\n── PHASE 1: Intake (getting both users to ready=True) ──────────")
    ready_results: dict = {}

    def intake_thread(user_id, msg, label):
        try:
            ready_results[label] = quick_intake(code, user_id, msg, label)
        except Exception as exc:
            print(f"[{label}] EXCEPTION: {exc}", flush=True)
            ready_results[label] = False

    ta = threading.Thread(target=intake_thread, args=("a", MSG_A, "INTAKE-A"))
    tb = threading.Thread(target=intake_thread, args=("b", MSG_B, "INTAKE-B"))
    ta.start(); tb.start()
    ta.join(); tb.join()

    print(f"\nIntake results: A={ready_results.get('INTAKE-A')}, B={ready_results.get('INTAKE-B')}")
    if not all(ready_results.values()):
        print("ERROR: Not all users reached ready=True. Aborting simulation test.")
        return

    # ── Phase 2: Simulation fan-out ─────────────────────────────────────────────
    print(f"\n── PHASE 2: Simulation (both tabs connecting to SSE fan-out) ──")
    print(f"(This will take 10–25 min — Gemini 2.5 Pro x2 + Llama Scout x N + synthesis)\n")

    sim_url = f"{BASE}/stream/simulation/{code}"
    shared_queue: queue.Queue = queue.Queue()

    # Open two SSE connections (simulating two browser tabs)
    t_tab_a = threading.Thread(
        target=sse_reader_thread, args=(sim_url, shared_queue, "TAB-A"), daemon=True
    )
    t_tab_b = threading.Thread(
        target=sse_reader_thread, args=(sim_url, shared_queue, "TAB-B"), daemon=True
    )
    t_tab_a.start()
    t_tab_b.start()
    print("Both tabs connected to simulation SSE stream.")

    # Collect all events until done
    all_events: list[dict] = []
    deadline = time.time() + 2400  # 40 min safety ceiling
    done_count = 0

    while time.time() < deadline and done_count < 2:
        try:
            evt = shared_queue.get(timeout=10.0)
            all_events.append(evt)
            t = evt.get("type", "")
            src = evt.get("_from", "?")

            if t == "simulation_turn":
                sp = evt.get("data", {}).get("speaker", "?")
                txt = evt.get("data", {}).get("text", "")
                print(f"[{src}] simulation_turn [{sp}] ({len(txt)} chars): {txt[:100].strip()}...", flush=True)

            elif t == "reasoning":
                summary = evt.get("data", {}).get("summary", "")
                print(f"[{src}] reasoning: {summary[:120]}", flush=True)

            elif t == "mediator_intervention":
                reason = evt.get("data", {}).get("reason", "")
                print(f"\n[{src}] *** MEDIATOR INTERVENES *** reason: {reason}", flush=True)

            elif t == "synthesis_token":
                pass  # too noisy to print individual tokens

            elif t == "synthesis_complete":
                text = evt.get("data", {}).get("text", "")
                print(f"\n[{src}] synthesis_complete ({len(text)} chars)", flush=True)
                # Print all 6 section headers to verify
                for line in text.splitlines():
                    if line.startswith("## "):
                        print(f"  section: {line}", flush=True)

            elif t == "done":
                done_count += 1
                print(f"\n[{src}] DONE event received ({done_count}/2)", flush=True)

            elif t in ("_http_error", "_reader_error"):
                print(f"[{src}] ERROR: {evt}", flush=True)

        except queue.Empty:
            continue

    t_tab_a.join(timeout=3)
    t_tab_b.join(timeout=3)

    # ── Phase 3: Result endpoint ─────────────────────────────────────────────────
    print(f"\n── PHASE 3: Result endpoint ────────────────────────────────────")
    for uid in ("a", "b"):
        r = requests.get(f"{BASE}/result/{code}/{uid}", timeout=30)
        if r.status_code == 200:
            data = r.json()
            synth = data.get("synthesis", "")
            print(f"GET /result/{code}/{uid}: 200  synthesis={len(synth)} chars")
        else:
            print(f"GET /result/{code}/{uid}: {r.status_code} ERROR: {r.text[:200]}")

    # ── Summary ─────────────────────────────────────────────────────────────────
    print(f"\n── EVENT SUMMARY ───────────────────────────────────────────────")
    for tab in ("TAB-A", "TAB-B"):
        tab_evts = [e for e in all_events if e.get("_from") == tab]
        by_type: dict[str, int] = {}
        for e in tab_evts:
            by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
        print(f"  {tab}: {dict(sorted(by_type.items()))}")

    print(f"\nTotal events: {len(all_events)} across both tabs")
    print(f"Done events received: {done_count}/2")
    if done_count == 2:
        print("PASS: both tabs received done event -> both would navigate to /result")
    else:
        print("FAIL: not all tabs received done event")


if __name__ == "__main__":
    main()
