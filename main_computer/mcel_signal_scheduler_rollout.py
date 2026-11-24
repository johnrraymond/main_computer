"""Patch 5: bounded default-certified MCEL signal scheduling policy.

Only SLH, semantic Task Manager, and the promoted Code Editor may start in certified mode. The shared
runtime retains full incumbent rendering for unsupported/uncertain consumers,
per-page immediate rollback, and fault-triggered fallback. Other apps cannot
opt into default signals via this module.

Emergency rollback (restart/reload the application page after changing it):
    MCEL_SIGNAL_SCHEDULER_DISABLE=1
Per-application rollback:
    MCEL_SIGNAL_SCHEDULER_SLH=incumbent
    MCEL_SIGNAL_SCHEDULER_TASK_MANAGER=incumbent
    MCEL_SIGNAL_SCHEDULER_CODE_EDITOR=incumbent
The precise 'certified' override re-enables a previously disabled app unless
the emergency kill switch is active. Unknown policy values fail closed.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

APP_ENV = {
    "slh": "MCEL_SIGNAL_SCHEDULER_SLH",
    "semantic-task-manager": "MCEL_SIGNAL_SCHEDULER_TASK_MANAGER",
    "code-editor": "MCEL_SIGNAL_SCHEDULER_CODE_EDITOR",
}
KILL_SWITCH = "MCEL_SIGNAL_SCHEDULER_DISABLE"


def start_certified(app_id: str, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    if app_id not in APP_ENV:
        return False
    # An invalid global flag is not silently treated as an authorization.
    if env.get(KILL_SWITCH) not in (None, "0"):
        return False
    value = env.get(APP_ENV[app_id])
    return value is None or value == "certified"
