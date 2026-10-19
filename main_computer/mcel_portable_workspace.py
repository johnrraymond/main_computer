"""Explicit, isolated capabilities for independently generated MCEL packages.

No existing application host is imported. File mutations are limited to an
operator-supplied workspace and require fresh SHA-256 or a prepared review handle.
This is a single-file reviewed transaction scope, not a general multi-file commit.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

_SHA = re.compile(r"^[a-f0-9]{64}$", re.I)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PortableCapabilityRefusal(ValueError):
    pass


class PortableWorkspaceBroker:
    def __init__(self, root: Path, *, external: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] | None = None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise PortableCapabilityRefusal("workspace-root-not-directory")
        self._lock = threading.RLock()
        self._reviews: dict[str, dict[str, Any]] = {}
        self._external = dict(external or {})
        self.effects: list[dict[str, Any]] = []

    def _path(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw or '\x00' in raw or '\\' in raw or ':' in raw:
            raise PortableCapabilityRefusal("invalid-project-path")
        components = raw.split('/')
        if any(item in ('', '.', '..') for item in components):
            raise PortableCapabilityRefusal("project-path-traversal")
        path = self.root.joinpath(*components)
        # Reject symlinks at every level, including nonexistent targets with a symlink parent.
        current = self.root
        for item in components:
            current = current / item
            if current.is_symlink():
                raise PortableCapabilityRefusal("workspace-symlink-denied")
        if not path.resolve().is_relative_to(self.root):
            raise PortableCapabilityRefusal("outside-authorized-workspace")
        return path

    def _existing(self, relative: Any) -> tuple[Path, bytes]:
        path = self._path(relative)
        if not path.is_file():
            raise PortableCapabilityRefusal("source-file-not-found")
        data = path.read_bytes()
        try:
            data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise PortableCapabilityRefusal("non-utf8-source") from exc
        if len(data) > 2_000_000:
            raise PortableCapabilityRefusal("source-file-too-large")
        return path, data

    def request(self, intent: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return self._request(intent, payload)
        except (PortableCapabilityRefusal, ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def _request(self, intent: str, p: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(p, Mapping):
            raise PortableCapabilityRefusal("invalid-capability-payload")
        with self._lock:
            if intent == 'inspectWorkspace':
                files = []
                for path in sorted(self.root.rglob('*')):
                    if path.is_file() and not path.is_symlink() and not any(v.is_symlink() for v in path.relative_to(self.root).parents if v != Path('.')):
                        relative = path.relative_to(self.root).as_posix()
                        if len(files) >= 200:
                            break
                        files.append({'path': relative, 'name': path.name, 'kind': 'file', 'bytes': path.stat().st_size})
                return {'ok':True,'files':files}
            if intent == 'openFile':
                path, data = self._existing(p.get('path'))
                return {'ok':True,'path':path.relative_to(self.root).as_posix(),'content':data.decode('utf-8'),'sourceHash':_digest(data)}
            if intent == 'saveFile':
                if p.get('explicit_save') is not True or p.get('stale_source_checked') is not True:
                    raise PortableCapabilityRefusal("explicit-save-required")
                path, before = self._existing(p.get('path'))
                expected = p.get('expected_before_sha256')
                if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _digest(before) != expected.lower():
                    raise PortableCapabilityRefusal("stale-source-refused")
                replacement = p.get('replacement_text')
                if not isinstance(replacement,str) or len(replacement.encode('utf-8')) > 2_000_000:
                    raise PortableCapabilityRefusal("invalid-save-text")
                after = replacement.encode('utf-8')
                if after == before:
                    raise PortableCapabilityRefusal("unchanged-source-not-a-mutation")
                # Safe workspace path with an exclusive temporary file and atomic replace.
                temp = path.with_name('.mcel-'+secrets.token_hex(12)+'.tmp')
                try:
                    with temp.open('xb') as handle:
                        handle.write(after)
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                record={'intent':intent,'path':path.relative_to(self.root).as_posix(), 'beforeSha256':_digest(before),'afterSha256':_digest(after)}
                self.effects.append(record)
                return {'ok':True,'path':record['path'],'sourceHash':record['afterSha256'],'changed':True,'effect':record}
            if intent == 'previewAiderPlan':
                if p.get('preview_only') is not True or p.get('mutation_allowed') is not False:
                    raise PortableCapabilityRefusal("read-only-preview-required")
                changes = p.get('changes')
                if not isinstance(changes,list) or len(changes) != 1:
                    raise PortableCapabilityRefusal("single-file-review-scope-required")
                change=changes[0]
                if not isinstance(change,Mapping) or change.get('operation') != 'modify':
                    raise PortableCapabilityRefusal("only-reviewed-modify-supported")
                path,before=self._existing(change.get('path'))
                expected=change.get('expected_before_sha256')
                if not isinstance(expected,str) or _digest(before) != expected.lower():
                    raise PortableCapabilityRefusal("stale-preview-source")
                replacement=change.get('replacement_text')
                if not isinstance(replacement,str) or len(replacement.encode('utf-8')) > 2_000_000:
                    raise PortableCapabilityRefusal("invalid-patch-replacement")
                handle=secrets.token_hex(16)
                self._reviews[handle]={'path':path.relative_to(self.root).as_posix(),
                                       'before':_digest(before),'after':replacement}
                return {'ok':True,'handle':handle,'state':'prepared','mutationAllowed':False,
                        'changedFiles':[change['path']],
                        'transaction':{'changes':[dict(change)],'project_root':'.'}}
            if intent == 'applyReviewedPatch':
                if p.get('reviewed') is not True or (p.get('approved') is not True and p.get('confirmed') is not True):
                    raise PortableCapabilityRefusal("reviewed-approval-required")
                handle=p.get('handle')
                if not isinstance(handle,str) or handle not in self._reviews:
                    raise PortableCapabilityRefusal("unknown-or-consumed-review-handle")
                review=self._reviews.pop(handle)  # consume before checking, replay denied
                path,before=self._existing(review['path'])
                if _digest(before) != review['before']:
                    raise PortableCapabilityRefusal("stale-reviewed-source")
                after=review['after'].encode('utf-8')
                temp=path.with_name('.mcel-'+secrets.token_hex(12)+'.tmp')
                try:
                    with temp.open('xb') as stream:
                        stream.write(after)
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                record={'intent':intent,'path':review['path'],'beforeSha256':_digest(before),'afterSha256':_digest(after),'handle':handle}
                self.effects.append(record)
                return {'ok':True,'handle':handle,'state':'applied','mutationAllowed':True,
                        'changedFiles':[review['path']],'effect':record}
            if intent in {'askModelForExpression','askModelForGraphExpression',
                          'askModelForMathicsExpression','evaluateMathics','askResultQuestion'}:
                provider=self._external.get(intent)
                if provider is None:
                    raise PortableCapabilityRefusal("external-domain-provider-unavailable:"+intent)
                result=provider(p)
                if not isinstance(result,Mapping) or not isinstance(result.get('text'),str) or not result['text'].strip() or not result.get('provider') or not result.get('evidence'):
                    raise PortableCapabilityRefusal("external-provider-evidence-invalid")
                return {'ok':True,'text':result['text'],'provider':result['provider'],'evidence':result['evidence']}
            raise PortableCapabilityRefusal("capability-not-granted:"+str(intent))
