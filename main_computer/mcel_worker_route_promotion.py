"""Local-only, reversible MCEL Worker canonical-route selection.

The route switch changes only the Worker *application surface*. It does not
change any marketplace backend, wallet, credential, spending rule, or job.
The original native Worker SPA is retained as the rollback target.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RELATIVE_STATE = Path("runtime/state/mcel_worker_canonical_route.json")
SCHEMA = "mcel.worker.canonical-route.v1"


def canonical_worker_mode(root: Path = ROOT) -> str:
    """Default to the explicitly promoted MCEL route; refuse corrupt overrides."""
    try:
        data = json.loads((root / RELATIVE_STATE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "promoted"   # Release default; rollback requires explicit action.
    except (OSError, ValueError, TypeError):
        return "legacy"     # Invalid rollback state must never select MCEL.
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA or
            data.get("mode") not in {"promoted", "legacy"}):
        return "legacy"
    return data["mode"]


def set_canonical_worker_mode(mode: str, root: Path = ROOT) -> Path:
    """Atomic local file switch, observed by the next Worker HTTP request."""
    if mode not in {"promoted", "legacy"}:
        raise ValueError("Invalid canonical Worker route mode")
    target = root / RELATIVE_STATE
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"schema": SCHEMA, "mode": mode}, sort_keys=True) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         prefix=".mcel-worker-route-", suffix=".tmp",
                                         dir=target.parent, delete=False) as fp:
            temporary = Path(fp.name)
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
