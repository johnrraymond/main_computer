"""Durable LMS SEH operation receipts and in-process exclusive execution.

The journal records admission and outcomes, but does not make historical errors
into a permanent service-wide lock. Remote mutation safety, including retry
and reconciliation, remains with the existing SEH harness and its receipts.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone

from .storage import secure_machine_state_dir, secure_private_state_file

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{15,127}\Z')


class ManagementJournalError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


class ManagementJournal:
    def __init__(self, root: Path, network: str):
        self.path = Path(root) / 'seh-operations' / network
        self.lock = threading.RLock()
        self._active_id = None
        self._worker = None
        self._latest_issue = None

    def _entry_path(self, operation_id):
        if type(operation_id) is not str or not _ID.fullmatch(operation_id):
            raise ManagementJournalError('SEH_OPERATION_ID_REQUIRED', 400)
        return self.path / (operation_id + '.json')

    def _read(self, operation_id):
        path = self._entry_path(operation_id)
        if path.is_symlink():
            raise ManagementJournalError('SEH_OPERATION_RECEIPT_UNSAFE')
        if not path.exists():
            return None
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            raise ManagementJournalError('SEH_OPERATION_RECEIPT_UNREADABLE') from None
        if (type(obj) is not dict or
                obj.get('schema') != 'main-computer.seh-management-operation.v1' or
                obj.get('operation_id') != operation_id or
                # RECOVERY_REQUIRED is accepted only to migrate old receipts.
                obj.get('phase') not in ('ACCEPTED', 'RUNNING', 'RECORDED',
                                        'RECOVERY_REQUIRED', 'CANCELED') or
                not isinstance(obj.get('digest'), str) or
                not re.fullmatch('[0-9a-f]{64}', obj['digest'])):
            raise ManagementJournalError('SEH_OPERATION_RECEIPT_INVALID')
        return obj

    def _write(self, record):
        secure_machine_state_dir(self.path)
        dest = self._entry_path(record['operation_id'])
        if dest.is_symlink():
            raise ManagementJournalError('SEH_OPERATION_RECEIPT_UNSAFE')
        fd, temporary = tempfile.mkstemp(prefix='.seh-operation-', dir=self.path)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream, sort_keys=True, separators=(',', ':'))
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            secure_private_state_file(Path(temporary))
            os.replace(temporary, dest)
            if os.name != 'nt':
                handle = os.open(self.path, os.O_RDONLY)
                try:
                    os.fsync(handle)
                finally:
                    os.close(handle)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _digest(details):
        return hashlib.sha256(json.dumps(details, sort_keys=True,
                              separators=(',', ':')).encode('utf-8')).hexdigest()

    @staticmethod
    def _public_row(row):
        return {k: v for k, v in row.items() if k in
                ('operation_id', 'phase', 'action', 'principal', 'accepted_at',
                 'stage', 'result', 'output')} | {'cancelable': row['phase'] == 'ACCEPTED'}

    def _finish_interrupted_unlocked(self, row, *, code='LMS_RESULT_UNCONFIRMED'):
        """Close a dead/legacy executor receipt, never repeat its remote action.

        This records *what LMS knows*, not a claim about Coolify. The harness
        alone can decide whether a subsequent explicit mutation is safe.
        """
        if row['phase'] not in ('ACCEPTED', 'RUNNING', 'RECOVERY_REQUIRED'):
            return row
        old = row.get('result')
        old = old if type(old) is dict else {}
        row['previous_stage'] = row.get('stage')
        row['phase'] = 'RECORDED'
        row['stage'] = 'EXECUTION_RESULT_UNCONFIRMED'
        row['result'] = {'status': 'INCOMPLETE',
                         'error_code': old.get('error_code') or code,
                         'remote_mutation': 'UNKNOWN'}
        self._write(row)
        return row

    def _all_receipts_unlocked(self):
        if not self.path.exists():
            return []
        paths = []
        for item in self.path.iterdir():
            if item.suffix != '.json':
                continue
            paths.append(item)
            if len(paths) > 2048:
                raise ManagementJournalError('SEH_OPERATION_REGISTRY_TOO_LARGE', 503)
        return [self._read(item.stem) for item in paths]

    def _pending_unlocked(self):
        """Only a live worker is busy. Never lock the LMS on historical receipts."""
        if self._active_id:
            row = self._read(self._active_id)
            if (row and row['phase'] in ('ACCEPTED', 'RUNNING') and
                    self._worker is not None and self._worker.is_alive()):
                return row
            if row is not None:
                self._finish_interrupted_unlocked(row)
            self._active_id = None
            self._worker = None
        rows = self._all_receipts_unlocked()
        for row in rows:
            self._finish_interrupted_unlocked(row)
        # A recent uncertain outcome should remain discoverable without
        # becoming a global lock or a modal that monopolizes the workspace.
        latest = max(rows, key=lambda r: (r.get('accepted_at', ''), r['operation_id']),
                     default=None)
        self._latest_issue = (latest if latest and latest['phase'] == 'RECORDED'
                              and latest.get('result', {}).get('status') == 'INCOMPLETE'
                              else None)
        return None

    def update_stage(self, operation_id: str, stage: str):
        if stage not in ('PREFLIGHT_EXECUTING', 'MUTATION_EXECUTING',
                         'ACTIVATION_PREFLIGHT', 'ACTIVATION_EXECUTING'):
            raise ManagementJournalError('SEH_OPERATION_STAGE_INVALID', 400)
        with self.lock:
            row = self._read(operation_id)
            if row is not None and row['phase'] == 'RUNNING':
                row['stage'] = stage
                self._write(row)

    def append_output(self, operation_id: str, stream: str, value: str):
        """Persist bounded, already-redacted harness output during execution.

        Each entry has a stable sequence so polling and reconnecting do not
        duplicate output. This journal does not accept untrusted HTTP content.
        """
        if stream not in ('stdout', 'stderr') or not isinstance(value, str):
            raise ManagementJournalError('SEH_OPERATION_OUTPUT_INVALID', 400)
        with self.lock:
            row = self._read(operation_id)
            if row is None or row['phase'] not in ('ACCEPTED', 'RUNNING'):
                return
            row['output_sequence'] = row.get('output_sequence', 0) + 1
            entry = {'seq': row['output_sequence'], 'stream': stream,
                     'text': value[:256]}
            lines = row.get('output', [])
            lines.append(entry)
            # Bound both receipt size and transmitted status payloads. Older
            # entries are discarded, never unboundedly accumulated.
            row['output'] = lines[-64:]
            self._write(row)

    def status(self, operation_id):
        with self.lock:
            self._pending_unlocked()
            row = self._read(operation_id)
            if row is None:
                raise ManagementJournalError('SEH_OPERATION_NOT_FOUND', 404)
            return {k: v for k, v in row.items() if k in
                    ('operation_id', 'phase', 'action', 'principal', 'accepted_at',
                     'result', 'stage', 'output')}

    def snapshot(self):
        with self.lock:
            row = self._pending_unlocked()
            if row is not None:
                return {'schema': 'main-computer.seh-management-operation-snapshot.v1',
                        'state': 'BUSY', 'busy': True, 'operation': self._public_row(row)}
            return {'schema': 'main-computer.seh-management-operation-snapshot.v1',
                    'state': 'IDLE', 'busy': False, 'operation': None,
                    'last_operation': (self._public_row(self._latest_issue)
                                       if self._latest_issue else None)}

    def ensure_idle(self):
        with self.lock:
            if self._pending_unlocked() is not None:
                raise ManagementJournalError('SEH_MANAGEMENT_BUSY', 409)

    def submit(self, operation_id, details: dict, execute, *, validate=None):
        """Durably admit, then execute outside the request and journal lock."""
        digest = self._digest(details)
        with self.lock:
            active = self._pending_unlocked()
            row = self._read(operation_id)
            if row is not None:
                if row['digest'] != digest:
                    raise ManagementJournalError('SEH_OPERATION_ID_CONFLICT')
                return {'status': (row.get('result', {}).get('status') or row['phase']),
                        'operation_id': operation_id, 'replayed_receipt': True,
                        'cancelable': row['phase'] == 'ACCEPTED'}
            if active is not None:
                raise ManagementJournalError('SEH_MANAGEMENT_BUSY', 409)
            if validate is not None:
                validate()
            row = {'schema': 'main-computer.seh-management-operation.v1',
                   'operation_id': operation_id, 'action': details['action'],
                   'principal': details['principal'], 'digest': digest,
                   'phase': 'ACCEPTED', 'stage': 'ACCEPTED_WAITING_FOR_EXECUTOR',
                   'accepted_at': datetime.now(timezone.utc).isoformat(),
                   'output': [], 'output_sequence': 0}
            self._write(row)
            self._active_id = operation_id
            worker = threading.Thread(target=self._perform, args=(operation_id, execute),
                                      name='lms-seh-' + operation_id[:32], daemon=True)
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._finish_interrupted_unlocked(row, code='SEH_OPERATION_EXECUTOR_UNAVAILABLE')
                self._active_id = None
                self._worker = None
                raise ManagementJournalError('SEH_OPERATION_EXECUTOR_UNAVAILABLE', 503) from None
            return {'status': 'ACCEPTED', 'operation_id': operation_id, 'cancelable': True}

    def _perform(self, operation_id, execute):
        with self.lock:
            row = self._read(operation_id)
            if row['phase'] == 'CANCELED':
                self._active_id = None
                return
            if row['phase'] != 'ACCEPTED':
                self._active_id = None
                return
            row['phase'] = 'RUNNING'
            row['stage'] = 'HARNESS_EXECUTING'
            self._write(row)
        try:
            result = execute()
            if type(result) is not dict:
                raise ManagementJournalError('SEH_OPERATION_INVALID_RESULT', 502)
            # INCOMPLETE is a recorded harness result, not an LMS busy state.
            safe_result = result
            stage = 'HARNESS_RETURNED'
        except Exception as exc:
            # The outcome may be uncertain. Preserve the safe exception type,
            # never its message (which may contain Coolify credentials).
            safe_result = {'status': 'INCOMPLETE',
                           'error_code': 'HARNESS_EXECUTION_UNCONFIRMED',
                           'error_type': type(exc).__name__,
                           'remote_mutation': 'UNKNOWN'}
            stage = 'HARNESS_RESULT_UNCONFIRMED'
        with self.lock:
            try:
                row = self._read(operation_id)
                row['phase'] = 'RECORDED'
                row['stage'] = stage
                row['result'] = safe_result
                self._write(row)
            finally:
                self._active_id = None
                self._worker = None

    def cancel(self, operation_id):
        with self.lock:
            row = self._read(operation_id)
            if row is None:
                raise ManagementJournalError('SEH_OPERATION_NOT_FOUND', 404)
            if row['phase'] != 'ACCEPTED' or self._active_id != operation_id:
                raise ManagementJournalError('SEH_CANCEL_UNSAFE', 409)
            row['phase'] = 'CANCELED'
            row['stage'] = 'CANCELED_BEFORE_EXECUTION'
            row['result'] = {'status': 'CANCELED'}
            self._write(row)
            return self._public_row(row)
