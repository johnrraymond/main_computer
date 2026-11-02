"""Read-only Windows process-producer provenance probe for P3.6 acceptance.

Run: python -m tools.mcel_p36_windows_process_probe
No mutating calls; no external HTTP or production service changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from main_computer.task_manager import TaskManagerService

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    current = os.getpid()
    snapshot = TaskManagerService(ROOT).snapshot(
        query=str(current), limit=100, include_all=True,
        include_connections=False, process_diagnostics=True,
    )
    process = next((row for row in snapshot['processes']
                    if row.get('pid') == current and row.get('created_at')), None)
    result = {
        'osProcessIdentityConfirmed': bool(process),
        'currentPid': current,
        'observedGeneration': process.get('created_at') if process else None,
        'producer': snapshot.get('_mcel_task_process_producer'),
        'processRowsReturned': len(snapshot['processes']),
    }
    print(json.dumps(result, indent=2))
    return 0 if process else 1


if __name__ == '__main__':
    raise SystemExit(main())
