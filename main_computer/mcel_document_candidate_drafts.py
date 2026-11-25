"""Isolated backend draft storage for the opt-in native Document Editor.
No path in the incumbent draft namespace is ever written by this module.
"""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from http import HTTPStatus

_LOCK = threading.RLock()
ROOT_NAME = 'mcel_document_candidate_drafts'


def draft_path(handler, requested):
    incumbent, relative = handler._docs_draft_path(requested)
    root = (handler.server.debug_root / 'runtime' / ROOT_NAME).resolve()
    legacy_root = handler._docs_drafts_root()
    if root == legacy_root or root.is_relative_to(legacy_root) or legacy_root.is_relative_to(root):
        raise ValueError('Candidate draft storage overlaps incumbent storage')
    target = (root / incumbent.relative_to(legacy_root)).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Candidate draft escapes storage root')
    return target, relative


def read_draft(path):
    if not path.exists():
        return {'exists': False, 'token': ''}
    data = path.read_bytes()
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError('Invalid candidate draft')
    return {**payload, 'exists': True, 'token': hashlib.sha256(data).hexdigest()}


def handle_candidate_draft(handler, *, write=False):
    try:
        body = handler._read_json()
        if not isinstance(body.get('path'), str):
            raise ValueError('Explicit document path required')
        path, relative = draft_path(handler, body['path'])
        with _LOCK:
            previous = read_draft(path)
            if not write:
                result = previous
            else:
                if not isinstance(body.get('expected_token'), str) or body['expected_token'] != previous['token']:
                    handler._send_json({'ok': False, 'error': 'CANDIDATE_DRAFT_CONFLICT'}, status=HTTPStatus.CONFLICT)
                    return
                html = body.get('html')
                if not isinstance(html, str) or len(html.encode('utf-8')) > 5*1024*1024:
                    raise ValueError('Invalid or oversized draft HTML')
                if not isinstance(body.get('layout'), dict):
                    raise ValueError('Draft layout required')
                payload = {'format': 1, 'path': relative, 'html': html, 'layout': body['layout'],
                           'revision': body.get('revision'), 'updated_at': datetime.now(timezone.utc).isoformat()}
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='.draft-', dir=path.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                        json.dump(payload, stream, ensure_ascii=False)
                    os.replace(name, path)
                finally:
                    if os.path.exists(name): os.unlink(name)
                result = read_draft(path)
        handler._send_json({**result, 'ok': True, 'path': relative, 'root': 'runtime/'+ROOT_NAME})
    except Exception as exc:
        handler._send_json({'ok': False, 'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
