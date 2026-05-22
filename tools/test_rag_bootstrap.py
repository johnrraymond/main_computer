from __future__ import annotations

"""Convenience wrapper for the backend-only RAG bootstrap harness.

Run from the repository root:

    python tools/test_rag_bootstrap.py --prompt "Inspect the Docker executor RAG flow" --no-model

This wrapper keeps the operator-facing test command stable while the reusable
implementation lives in ``main_computer.rag_harness`` for later API/frontend use.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.rag_harness import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
