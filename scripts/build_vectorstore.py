"""
Builds both ChromaDB collections in sequence:
  1. reddit_examples  — via scripts/load_reddit.py
  2. psychology_kb    — via scripts/load_psychology.py

Exits 0 on full success, 1 if either loader fails.

Run from the project root:
    backend/.venv/Scripts/python.exe scripts/build_vectorstore.py
"""

import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"

# Use the same interpreter that launched this script — automatically picks up
# the active venv without needing a hardcoded path.
PYTHON = sys.executable

STAGES = [
    ("reddit_examples", "load_reddit.py"),
    ("psychology_kb",   "load_psychology.py"),
]

_W = 62  # banner width


def banner(text: str, char: str = "─") -> None:
    print(f"\n{char * _W}", flush=True)
    print(f"  {text}", flush=True)
    print(f"{char * _W}\n", flush=True)


def run_loader(script_name: str) -> int:
    """Run a loader script as a subprocess and return its exit code."""
    result = subprocess.run(
        [PYTHON, str(SCRIPTS / script_name)],
        cwd=str(ROOT),
    )
    return result.returncode


def main() -> None:
    banner("build_vectorstore.py — building all ChromaDB collections", "=")

    failed: list[str] = []

    for i, (collection, script) in enumerate(STAGES, start=1):
        banner(f"STAGE {i} of {len(STAGES)} — {collection}  ({script})")
        rc = run_loader(script)
        if rc != 0:
            print(f"\n[FAIL] {script} exited with code {rc}", flush=True)
            failed.append(script)
        else:
            print(f"\n[OK]   {script} completed successfully", flush=True)

    banner("Summary", "=")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        print()
        sys.exit(1)
    else:
        print(f"  All {len(STAGES)} collections built successfully.")
        print()


if __name__ == "__main__":
    main()
