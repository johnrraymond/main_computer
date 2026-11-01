"""Credential-free, durable progress journal for real Pinky laboratory runs.

Stage starts are flushed *before* potentially blocking calls. An incomplete
stage at failure means an operation was still in progress, not a proven cause.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import time

_TRACE: ContextVar[Path | None] = ContextVar('slh_lab_stage_trace', default=None)
SCHEMA = 'main-computer.slh-pinky-lab-stage.v1'


def enable(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _TRACE.set(path)


def disable(token):
    _TRACE.reset(token)


def record(stage: str, state: str, **fields) -> None:
    path = _TRACE.get()
    if path is None:
        return
    # The caller supplies only names, loopback ports, PIDs, and timing; no
    # generic request payloads, tokens, signed messages, or raw exceptions.
    row = {'schema': SCHEMA, 'timestamp': datetime.now(timezone.utc).isoformat(),
           'stage': stage, 'state': state, **fields}
    try:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush()
    except OSError:
        # Diagnostics must never change laboratory transaction semantics.
        pass


@contextmanager
def stage(name: str, **fields):
    start = time.perf_counter()
    record(name, 'begin', **fields)
    try:
        yield
    except BaseException as exc:
        record(name, 'error', elapsed_ms=round((time.perf_counter()-start)*1000, 1),
               error_type=type(exc).__name__,
               error_classification=('handshake_response_timeout'
                   if 'timed out while waiting for handshake response' in str(exc).lower()
                   else 'other'))
        raise
    else:
        record(name, 'ok', elapsed_ms=round((time.perf_counter()-start)*1000, 1))


def summarize(path: Path) -> dict:
    """Identify the innermost failed stage or last unfinished stage."""
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
                if line.strip()]
    except (OSError, ValueError):
        return {'events': 0, 'last_event': None, 'last_failure': None,
                'unfinished_stage': None}
    open_stages = []
    for row in rows:
        name, state = row.get('stage'), row.get('state')
        if state == 'begin':
            open_stages.append(name)
        elif state in ('error', 'ok'):
            if name in open_stages:
                index = len(open_stages)-1-open_stages[::-1].index(name)
                del open_stages[index]
    failures = [r for r in rows if r.get('state') == 'error']
    return {'events': len(rows), 'last_event': rows[-1] if rows else None,
            'last_failure': failures[0] if failures else None,
            'unfinished_stage': open_stages[-1] if open_stages else None}
