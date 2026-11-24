"""Opt-in, content-free timings for MCEL's *disposable* hosted Code Editor save.

This module never authorizes writes, modifies a transaction decision, or logs
source text / secrets. It traces only runtime/reports/mcel-hosted-signal-*/
reference.js or candidate.js and writes bounded evidence to runtime/reports.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import uuid

_ACTIVE = ContextVar('mcel_disposable_save_observer', default=None)
_PATTERN = re.compile(r'^runtime/reports/mcel-hosted-signal-[^/]+/(reference|candidate)\.js$')
_STAGES = ('mcel-project-edit-stage-', 'mcel-project-edit-dryrun-')


def is_disposable_save_path(value: object) -> bool:
    return isinstance(value, str) and bool(_PATTERN.fullmatch(value.replace('\\', '/')))


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _stack_for_thread(ident: int) -> list[dict[str, object]]:
    frame = sys._current_frames().get(ident)
    frames = []
    while frame is not None and len(frames) < 18:
        file = Path(frame.f_code.co_filename).name
        # File basenames, function identifiers and lines only. No arguments,
        # source content, local variables or absolute workstation paths.
        if file in {'mcel_project_edit_transaction.py', 'viewport_routes_editor.py',
                    'subprocess.py', 'shutil.py', 'pathlib.py', 'ntpath.py',
                    'threading.py', 'zipfile.py', 'os.py', 'tempfile.py', 'io.py'}:
            frames.append({'file': file, 'function': frame.f_code.co_name,
                           'line': frame.f_lineno})
        frame = frame.f_back
    return frames


def _stage_folders() -> list[dict[str, object]]:
    try:
        base = Path(tempfile.gettempdir())
        result = []
        for child in base.iterdir():
            if any(child.name.startswith(prefix) for prefix in _STAGES):
                try:
                    stat = child.stat()
                    result.append({'kind': next(x for x in _STAGES if child.name.startswith(x)),
                                   'mtimeAgeSeconds': round(max(0, time.time()-stat.st_mtime), 1)})
                except OSError:
                    continue
                if len(result) == 40:
                    break
        return result
    except OSError:
        return []


class _Recorder:
    def __init__(self, workspace: Path, role: str):
        self.id = uuid.uuid4().hex[:16]
        key=hashlib.sha256(str(Path(workspace).resolve()).encode('utf8')).hexdigest()[:20]
        # Reports must NEVER be inside the repository, otherwise their creation
        # and ongoing append can perturb project manifests and staging.
        self.report_root = Path(tempfile.gettempdir())/'mcel-hosted-save-stall-diagnostics'/key/'server' 
        self.report_root.mkdir(parents=True, exist_ok=True)
        self.path = self.report_root / (self.id + '.jsonl')
        self.role = role
        self.thread_id = threading.get_ident()
        self.start = time.monotonic()
        self.stage = 'save.request_accepted'
        self.stage_started = self.start
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.events = 0
        self.write('request_enter', {'stage': self.stage, 'role': role,
                                     'serverPid': os.getpid(), 'session': self.id})
        self.thread = threading.Thread(target=self._watch, name='mcel-save-stall-watch', daemon=True)
        self.thread.start()

    def write(self, event: str, extra: dict | None = None) -> None:
        body = {'schema': 'mcel.save-stall-telemetry.v1', 'utc': _utc(),
                'session': self.id, 'event': event, 'stage': self.stage,
                'elapsedSeconds': round(time.monotonic()-self.start, 3)}
        if extra: body.update(extra)
        # Append each record atomically under an in-process lock. The observer
        # never stores request payloads, local paths, tokens or response text.
        with self._lock:
            with self.path.open('a', encoding='utf8') as handle:
                handle.write(json.dumps(body, separators=(',', ':'), sort_keys=True)+'\n')
            self.events += 1
        if event in ('stage_enter', 'stage_exit', 'request_complete', 'request_error'):
            print('MCEL_SAVE_STALL_STAGE',json.dumps(body, sort_keys=True),flush=True)

    def begin(self, stage: str) -> None:
        self.stage = stage
        self.stage_started = time.monotonic()
        self.write('stage_enter')

    def end(self, stage: str, status: str) -> None:
        self.write('stage_exit', {'status': status,
                                  'stageElapsedSeconds': round(time.monotonic()-self.stage_started,3)})
        self.stage = 'between_stages'
        self.stage_started = time.monotonic()

    def _watch(self) -> None:
        # Samples run in an independent thread even while the request thread is
        # in a blocking filesystem call or waiting for subprocess completion.
        while not self._stop.wait(5.0):
            self.write('still_active', {
                'stageElapsedSeconds': round(time.monotonic()-self.stage_started, 2),
                'requestThreadStack': _stack_for_thread(self.thread_id),
                'temporaryStageDirectories': _stage_folders(),
            })

    def close(self, status: str) -> None:
        self.write('request_complete' if status == 'completed' else 'request_error', {'status': status})
        self._stop.set()
        self.thread.join(timeout=0.25)


@contextmanager
def observe_save(workspace: Path, path: object):
    """Only the exact hosted disposable fixture may activate telemetry."""
    if not is_disposable_save_path(path):
        yield
        return
    role = 'incumbent' if str(path).replace('\\','/').endswith('/reference.js') else 'signals'
    recorder = _Recorder(Path(workspace).resolve(), role)
    token = _ACTIVE.set(recorder)
    try:
        yield
    except BaseException as exc:
        recorder.write('request_exception', {'errorClass': type(exc).__name__})
        recorder.close('failed')
        raise
    else:
        recorder.close('completed')
    finally:
        _ACTIVE.reset(token)


@contextmanager
def stage(name: str):
    """Zero logging for ordinary production requests; no transaction changes."""
    recorder = _ACTIVE.get()
    if recorder is None:
        yield
        return
    recorder.begin(name)
    try:
        yield
    except BaseException:
        recorder.end(name, 'error')
        raise
    else:
        recorder.end(name, 'completed')
