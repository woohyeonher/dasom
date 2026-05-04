"""
scripts/test_observer.py
Diagnostic + functional test for the Llama Scout mediator observer.

Phase 1 — Raw response inspection:
    Calls call_llama_scout directly with MEDIATOR_OBSERVER_PROMPT and
    a sample transcript for round 2, 4, and 6 turns, then prints the
    exact raw string before any parsing so we can see what the model
    actually returns.

Phase 2 — Intervention decision test:
    Using the same sample transcript grown to 8 turns (4 per side),
    confirm that _parse_observer_json succeeds and that the observer
    eventually returns intervene=True (conditions 1 and 5 satisfied).

Run: backend\\.venv\\Scripts\\python.exe scripts\\test_observer.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.agents.mediator_agent import _format_transcript_numbered, _parse_observer_json
from backend.models.router import call_llama_scout
from backend.utils.prompts import MEDIATOR_OBSERVER_PROMPT

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Sample transcript that mimics a real simulation ───────────────────────────

_ALL_TURNS = [
    {"speaker": "A", "text": (
        "I just don't understand why a five-second text is too much to ask. "
        "I had dinner on the table. I waited an hour. You didn't think once to pick up your phone? "
        "That tells me everything about where I fall on your list of priorities."
    )},
    {"speaker": "B", "text": (
        "I was finishing a critical deadline — my entire team was depending on me. "
        "I didn't text because I was heads-down trying to get us to the finish line. "
        "I'm sorry the dinner got cold, but you're making this about you when it was about work."
    )},
    {"speaker": "A", "text": (
        "It's always about work. That's exactly my point. "
        "And when I try to bring it up you call me controlling. "
        "I'm not controlling — I'm asking to be considered. That's a basic thing. "
        "We've been together three years and I feel invisible."
    )},
    {"speaker": "B", "text": (
        "You keep saying invisible but you were not invisible to me — I was thinking about you. "
        "I just couldn't stop in the middle of something critical to send a text. "
        "And now every time I work late I'm going to hear about this one night? "
        "That feels like I can never win no matter what I do."
    )},
    {"speaker": "A", "text": (
        "You say you were thinking about me but your actions said otherwise. "
        "This isn't about one night — this is a six-month pattern. "
        "Every time I bring it up you find a way to make me the problem. "
        "I just need to know I'm a priority. Is that so unreasonable?"
    )},
    {"speaker": "B", "text": (
        "Of course you're a priority. But you can't be my only priority. "
        "I have a career, I have responsibilities — and when those responsibilities are urgent, "
        "I have to handle them. I need you to trust that stepping away for a deadline "
        "is not the same as not caring about you."
    )},
    {"speaker": "A", "text": (
        "I hear you saying I matter, but I don't feel it. "
        "And that gap — between what you say and what I experience — "
        "that is what's killing us. I don't want to fight. "
        "I want to feel safe in this relationship again."
    )},
    {"speaker": "B", "text": (
        "I don't want to fight either. I love you. I'm not going anywhere. "
        "But I need you to understand that when I'm under pressure I go quiet — "
        "that's not abandonment, that's how I cope. "
        "Maybe we need to figure out a way to signal that to each other."
    )},
]


async def _raw_observe(transcript: list[dict]) -> str:
    """Call Llama Scout and return the raw unprocessed string."""
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
    return raw


async def main() -> None:
    print("=" * 70)
    print("PHASE 1 — Raw Llama Scout observer response inspection")
    print("=" * 70)

    for i, (label, n_turns) in enumerate([("Round 1 (2 turns)", 2), ("Round 2 (4 turns)", 4), ("Round 3 (6 turns)", 6)]):
        if i > 0:
            print(f"  (waiting 15s between calls to avoid rate limit...)", flush=True)
            await asyncio.sleep(15)
        transcript = _ALL_TURNS[:n_turns]
        print(f"\n── {label} {'─' * (60 - len(label))}")
        print(f"  Transcript turns: {n_turns}")
        print("  Calling Llama Scout...", flush=True)
        raw = await _raw_observe(transcript)
        print(f"\n  RAW RESPONSE ({len(raw)} chars):")
        print("  " + repr(raw))
        print(f"\n  VISIBLE:")
        for line in raw.splitlines():
            print(f"    | {line}")
        parsed = _parse_observer_json(raw)
        print(f"\n  PARSED: intervene={parsed['intervene']!r}  reason={parsed['reason']!r}")

    print("\n" + "=" * 70)
    print("PHASE 2 — Observer intervention decision test (8 turns / 4 per side)")
    print("=" * 70)

    transcript_full = _ALL_TURNS[:8]
    print(f"\n(waiting 15s before final call...)", flush=True)
    await asyncio.sleep(15)
    print(f"Calling observer with full {len(transcript_full)}-turn transcript...")
    raw = await _raw_observe(transcript_full)
    print(f"\nRAW ({len(raw)} chars):")
    print(repr(raw))
    parsed = _parse_observer_json(raw)
    print(f"\nPARSED: intervene={parsed['intervene']!r}  reason={parsed['reason']!r}")

    if parsed["intervene"]:
        print("\nPASS: observer returns intervene=True on a mature 8-turn transcript.")
    else:
        print("\nWARNING: observer did not intervene even at 8 turns. Reason may need inspection.")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
