"""
scripts/test_observer_frequency.py
Verifies that _observe() is called on turns 2, 4, 6, 8 and skipped on 1, 3, 5, 7.
No API calls — patches _observe and persona generation with stubs.

Run: backend\\.venv\\Scripts\\python.exe scripts\\test_observer_frequency.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main() -> None:
    from backend.session.manager import (
        create_session,
        join_session,
        register_simulation_sse,
        set_tone,
        unregister_simulation_sse,
        update_structured_output,
    )
    import backend.agents.mediator_agent as med

    # ── Session setup ──────────────────────────────────────────────────────
    code = create_session()
    join_session(code, "ua")
    join_session(code, "ub")
    set_tone(code, "ua", "neutral")
    set_tone(code, "ub", "neutral")

    _OUT = {
        "who": "us", "when": "Saturday", "where": "home",
        "what": "argument", "why": "unmet needs", "how": "it escalated",
        "emotional_labels": ["hurt"], "needs": ["respect"],
        "field_confidence": {k: "High" for k in ("who","when","where","what","why","how","emotional_labels","needs")},
        "overall_confidence": "High",
    }
    update_structured_output(code, "ua", **_OUT)
    update_structured_output(code, "ub", **_OUT)

    q = register_simulation_sse(code)

    # ── Stubs ──────────────────────────────────────────────────────────────
    observe_calls: list[int] = []   # turn_count value when _observe was called
    turn_tracker = {"count": 0}

    async def fake_observe(transcript):
        # turn_count has already been incremented to the current even value
        observe_calls.append(turn_tracker["count"])
        return {"intervene": False, "reason": "still watching"}

    async def fake_generate_turn(self, prev):
        turn_tracker["count"] += 1
        yield f"turn{turn_tracker['count']}"

    # Run 4 rounds (8 turns) so we can check turns 1–8
    with (
        patch.object(med, "_observe", side_effect=fake_observe),
        patch("backend.agents.persona_agent.PersonaAgent.generate_turn", fake_generate_turn),
    ):
        await med.run_simulation(code, "ua", "ub", max_rounds=4)

    unregister_simulation_sse(code, q)

    # ── Assertions ────────────────────────────────────────────────────────
    print(f"Observer called {len(observe_calls)} times, at turns: {observe_calls}")

    expected_calls = [2, 4, 6, 8]
    expected_skips = [1, 3, 5, 7]

    assert observe_calls == expected_calls, (
        f"Expected observer at turns {expected_calls}, got {observe_calls}"
    )
    print(f"  PASS  observer called at even turns: {expected_calls}")

    for skip in expected_skips:
        assert skip not in observe_calls, f"Observer should have been skipped at turn {skip}"
    print(f"  PASS  observer skipped at odd turns: {expected_skips}")

    assert len(observe_calls) == 4, f"Expected 4 observer calls for 4 rounds, got {len(observe_calls)}"
    print(f"  PASS  exactly 4 observer calls for 4 rounds (halved from 8)")

    print("\nAll frequency tests PASS.")


if __name__ == "__main__":
    asyncio.run(main())
