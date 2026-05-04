"""
scripts/test_observer_parser.py
Unit tests for _parse_observer_json — no API calls, runs instantly.

Covers every known Llama 4 Scout response format pattern:
  - Clean JSON (ideal)
  - JSON with leading/trailing whitespace or newlines
  - Markdown fences (```json ... ``` and ``` ... ```)
  - <think>...</think> reasoning wrapper
  - <|thinking|>...</|thinking|> wrapper
  - Prose before the JSON object
  - Python-style booleans (True/False)
  - Single-quoted JSON
  - intervene=true as keyword (no braces at all)
  - Completely garbled output → safe default

Run: backend\\.venv\\Scripts\\python.exe scripts\\test_observer_parser.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.agents.mediator_agent import _parse_observer_json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = [
    # (label, raw_input, expected_intervene, reason_contains)
    (
        "clean JSON intervene=false",
        '{"intervene": false, "reason": "Still waiting for Person B to express core needs."}',
        False,
        "Person B",
    ),
    (
        "clean JSON intervene=true",
        '{"intervene": true, "reason": "Both sides have expressed their core needs; circular now."}',
        True,
        "circular",
    ),
    (
        "leading/trailing whitespace",
        '  \n\n{"intervene": false, "reason": "Too early — only 2 turns each."}\n\n  ',
        False,
        "Too early",
    ),
    (
        "markdown fence ```json",
        '```json\n{"intervene": true, "reason": "Breakthrough moment — B softened."}\n```',
        True,
        "Breakthrough",
    ),
    (
        "markdown fence ``` no lang",
        '```\n{"intervene": false, "reason": "Conversation is still productive."}\n```',
        False,
        "productive",
    ),
    (
        "<think> block before JSON",
        '<think>\nLet me analyze the transcript carefully.\nBoth sides have spoken 3 times.\n</think>\n{"intervene": true, "reason": "Core tension is now visible."}',
        True,
        "Core tension",
    ),
    (
        "<|thinking|> block before JSON",
        '<|thinking|>\nAnalyzing...\n</|thinking|>\n{"intervene": false, "reason": "B has not finished expressing."}',
        False,
        "B has not",
    ),
    (
        "prose before JSON",
        'After careful analysis of the transcript:\n\n{"intervene": false, "reason": "Only 1 turn per person so far."}',
        False,
        "1 turn",
    ),
    (
        "Python True boolean",
        "{'intervene': True, 'reason': 'Circular — repeating same points.'}",
        True,
        "Circular",
    ),
    (
        "Python False boolean",
        "{'intervene': False, 'reason': 'Still early in the conversation.'}",
        False,
        "early",
    ),
    (
        "no braces — keyword scan true",
        'I believe we should intervene. "intervene": true\n"reason": "The exchange is escalating."',
        True,
        "",
    ),
    (
        "no braces — keyword scan false",
        'Not yet. "intervene": false "reason": "Still one side unheard."',
        False,
        "",
    ),
    (
        "extra text after closing brace",
        '{"intervene": true, "reason": "Stonewalling detected."} Hope that helps!',
        True,
        "Stonewalling",
    ),
    (
        "intervene:true no space",
        '{"intervene":true,"reason":"Escalation into contempt."}',
        True,
        "Escalation",
    ),
    (
        "intervene:false no space",
        '{"intervene":false,"reason":"Waiting for A to name their need."}',
        False,
        "Waiting",
    ),
    (
        "streaming artifact: missing opening {\" (intervene=false)",
        'intervene": false, "reason": "Fewer than 3 turns per person have passed."}',
        False,
        "Fewer than 3",
    ),
    (
        "streaming artifact: missing opening {\" (intervene=true)",
        'intervene": true, "reason": "Both sides circular after 3 turns each."}',
        True,
        "circular",
    ),
    (
        "completely garbled → safe default",
        "I'm sorry I cannot output JSON right now. Please try again.",
        False,
        "(unparseable",
    ),
    (
        "empty string → safe default",
        "",
        False,
        "(unparseable",
    ),
    (
        "markdown fence then prose after",
        '```json\n{"intervene": false, "reason": "Let it breathe more."}\n```\n\nThis is my assessment.',
        False,
        "breathe",
    ),
    (
        "<reasoning> block",
        '<reasoning>Need to check if 3 turns per person passed.</reasoning>\n{"intervene": true, "reason": "Three turns each — intervening now."}',
        True,
        "Three turns",
    ),
]


def run_tests() -> None:
    passed = 0
    failed = 0

    for label, raw, expected_intervene, reason_contains in CASES:
        result = _parse_observer_json(raw)
        ok_intervene = result["intervene"] == expected_intervene
        ok_reason = reason_contains.lower() in result["reason"].lower() if reason_contains else True

        if ok_intervene and ok_reason:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label}")
            if not ok_intervene:
                print(f"        intervene: expected={expected_intervene!r}  got={result['intervene']!r}")
            if not ok_reason:
                print(f"        reason should contain {reason_contains!r}")
                print(f"        reason was: {result['reason']!r}")
            failed += 1

    print(f"\n{'─'*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(CASES)} cases")
    if failed:
        sys.exit(1)
    else:
        print("All parser tests PASS.")


if __name__ == "__main__":
    run_tests()
