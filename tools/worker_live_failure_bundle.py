"""Package one MCEL live Worker failure, including the screenshots from THAT attempt.

This never reads wallet profiles, browser storage, process environment, raw request
bodies, or Node stderr. The archive remains local; screenshots are sensitive.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
from zipfile import ZIP_DEFLATED, ZipFile

GATES = frozenset(('wallet-msk', 'offer-registration', 'work-cycle', 'requester-enforcement'))
CAPTURE_NAME = re.compile(
    r'live-(wallet-msk|offer-registration|work-cycle|requester-enforcement)-'
    r'(?:failure|moment-[a-z0-9-]+)-[A-Za-z0-9_-]{6,}/'
    r'(?:desktop|worker|metamask-(?:cdp-)?(?:home|notification|popup|sidepanel)-[1-9][0-9]?)\.png'
)
MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 192 * 1024 * 1024
MAX_SCREENSHOTS = 100
SENSITIVE_FIELD = re.compile(r'(?:mnemonic|password|passphrase|seedphrase|seed_phrase|'
    r'private.?key|secret.?key|authorization.?header|bearer.?token|raw.?signature|'
    r'raw.?response|raw.?request)', re.IGNORECASE)
WALLET_ADDRESS = re.compile(r'0x[a-fA-F0-9]{40}\b')
LONG_HEX = re.compile(r'0x[a-fA-F0-9]{64,}\b')


def _sanitized_json(value, depth: int = 0):
    """Defense-in-depth for accidentally introduced evidence fields."""
    if depth > 16:
        return '[depth-limit]'
    if isinstance(value, dict):
        return {str(k)[:100]: ('[redacted]' if SENSITIVE_FIELD.search(str(k))
                else _sanitized_json(v, depth + 1)) for k, v in list(value.items())[:512]}
    if isinstance(value, list):
        return [_sanitized_json(item, depth + 1) for item in value[:1024]]
    if isinstance(value, str):
        value = LONG_HEX.sub('[hex-redacted]',value)
        value = WALLET_ADDRESS.sub('[address-redacted]',value)
        return value[:2048] if len(value) <= 2048 else value[:2048]+'[truncated]'
    if value is None or isinstance(value,(bool,int,float)):
        return value
    return '[unrecognized]'


def _safe_token(value: object, default: str = 'unknown') -> str:
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) else default


def _images_from_evidence(evidence: object, gate: str, evidence_dir: Path):
    """Yield ONLY images named in the current evidence; never glob older runs."""
    if not isinstance(evidence, dict):
        return
    groups = [evidence.get('failureScreenshots')]
    for key in ('mskMoments', 'offerMoments'):
        moments = evidence.get(key)
        if isinstance(moments, list):
            groups.extend(moments[:32])
    seen = set()
    count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        captures = group.get('captures')
        if not isinstance(captures, list):
            continue
        for item in captures[:30]:
            if not isinstance(item, dict) or item.get('status') != 'saved':
                continue
            value = item.get('file')
            if not isinstance(value, str) or not CAPTURE_NAME.fullmatch(value):
                continue
            if not value.startswith('live-' + gate + '-') or value in seen:
                continue
            seen.add(value)
            count += 1
            if count > MAX_SCREENSHOTS:
                return
            candidate = evidence_dir / value
            # Deny links and directory traversal even if evidence was tampered.
            if candidate.is_symlink() or candidate.parent.is_symlink():
                continue
            if candidate.resolve().parent != candidate.parent.resolve():
                continue
            yield value, candidate


def build_failure_bundle(*, gate: str, evidence_path: Path, report_path: Path,
                         evidence: dict | None, reason: str, process: str,
                         destination_dir: Path | None = None) -> dict:
    """Make a unique ZIP even when Node failed before producing its JSON proof.

    Never changes PASS/FAIL, never modifies application state, and never sends
    data over the network. Report path and screenshot names are locally scoped.
    """
    if gate not in GATES:
        raise ValueError('Unrecognized gate')
    evidence_path = Path(evidence_path)
    report_path = Path(report_path)
    root = Path(destination_dir) if destination_dir else evidence_path.parent / 'failure-bundles'
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bundle = root / f'live-{gate}-failure-evidence-{stamp}-{secrets.token_hex(4)}.zip'
    manifest = {'schema': 'mcel.worker.failure-bundle.v1', 'gate': gate,
                'createdUtc': datetime.now(timezone.utc).isoformat(),
                'stage': _safe_token(evidence.get('stage') if isinstance(evidence, dict) else None),
                'reason': _safe_token(reason), 'process': _safe_token(process),
                'status': 'UNVERIFIED', 'screenshots': [], 'files': [], 'missing': [],
                'policy': {'expectedBaseline': 'unknown', 'restorationVerified': 'unknown'}}
    if isinstance(evidence, dict):
        offer = evidence.get('offerRegistration')
        if isinstance(offer, dict):
            mode = offer.get('availabilityModeBaseline')
            restored = offer.get('availabilityRestored')
            manifest['policy'] = {
                'expectedBaseline': mode if mode in ('totally_idle', 'ai_idle') else 'unknown',
                'restorationVerified': 'yes' if restored is True else 'no' if restored is False else 'unknown',
            }
    total = 0
    with ZipFile(bundle, mode='x', compression=ZIP_DEFLATED, compresslevel=6) as zipfile:
        def add(name: str, content: bytes, *, category: str):
            nonlocal total
            if total + len(content) > MAX_TOTAL_BYTES:
                manifest['missing'].append({'file': name, 'reason': 'size-cap'})
                return False
            zipfile.writestr(name, content)
            total += len(content)
            manifest['files'].append({'path': name, 'category': category,
                                      'bytes': len(content),
                                      'sha256': hashlib.sha256(content).hexdigest()})
            return True

        # Do not bundle an unvalidated evidence file. The runner has parsed it
        # already, and only sanitized instrumentation is permitted here.
        if isinstance(evidence, dict):
            add('evidence/live-gate-evidence.json',
                (json.dumps(_sanitized_json(evidence), indent=2, sort_keys=True) + '\n').encode('utf-8'),
                category='structured-evidence')
        else:
            manifest['missing'].append({'file': 'evidence/live-gate-evidence.json',
                                        'reason': 'node-evidence-missing'})
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if isinstance(report, dict) and report.get('schema') == 'mcel.worker.live-functional.v1':
                add('evidence/live-run-report.json',
                    (json.dumps(_sanitized_json(report), indent=2, sort_keys=True) + '\n').encode('utf-8'),
                    category='structured-evidence')
        except (OSError, ValueError):
            manifest['missing'].append({'file': 'evidence/live-run-report.json',
                                        'reason': 'report-missing-or-unreadable'})

        for relative, candidate in _images_from_evidence(evidence, gate, evidence_path.parent):
            archive_name = 'screenshots/' + relative
            try:
                size = candidate.stat().st_size
                if not candidate.is_file() or size < 64 or size > MAX_SCREENSHOT_BYTES:
                    raise ValueError('unavailable-or-oversized')
                content = candidate.read_bytes()
                # Validate PNG signature; never archive arbitrary data renamed PNG.
                if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('not-png')
                if add(archive_name, content, category='screenshot'):
                    manifest['screenshots'].append(archive_name)
            except (OSError, ValueError):
                manifest['missing'].append({'file': archive_name, 'reason': 'unavailable'})

        timeline = {
            'stage': manifest['stage'],
            'policyTransition': evidence.get('offerRegistration', {}).get('policyTransition')
              if isinstance(evidence, dict) and isinstance(evidence.get('offerRegistration'), dict) else None,
            'mskEvents': evidence.get('mskSigning', {}).get('events', [])[-32:]
              if isinstance(evidence, dict) and isinstance(evidence.get('mskSigning'), dict) else [],
            'offerMoments': [item.get('phase') for item in evidence.get('offerMoments', [])[:32]
              if isinstance(item, dict) and isinstance(item.get('phase'), str)]
              if isinstance(evidence, dict) and isinstance(evidence.get('offerMoments'), list) else [],
        }
        add('evidence/failure-timeline.json',
            (json.dumps(_sanitized_json(timeline), indent=2, sort_keys=True) + '\n').encode('utf-8'),
            category='event-timeline')
        add('README.txt', (
            'MCEL Worker live acceptance failure evidence (local only).\n'
            'This archive is NOT proof of a passing test.\n'
            'Screenshots may include wallet/account information. Review before sharing.\n'
            'No wallet secret files, browser profiles, environment dumps, raw HTTP bodies,\n'
            'Node stderr, or Playwright trace/storage-state dumps are included.\n'
            'See manifest.json for hashes and any missing screenshots.\n'
        ).encode('utf-8'), category='instructions')
        zipfile.writestr('manifest.json', json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return {'path': str(bundle), 'screenshots': len(manifest['screenshots']),
            'missing': len(manifest['missing']), 'files': len(manifest['files']) + 1,
            'manifest': manifest}


def main(argv: list[str] | None = None) -> int:
    """Package the latest already-recorded failure, without rerunning any test."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate', required=True, choices=sorted(GATES))
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    base = Path(__file__).resolve().parents[1] / 'runtime/reports/mcel-worker-live-functional'
    detail = args.evidence or base / f'live-{args.gate}-evidence.json'
    report_path = args.report or detail.parent / 'report.json'
    try:
        evidence = json.loads(detail.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        parser.error('No readable live gate evidence; run the gate to capture it first')
    if not isinstance(evidence, dict) or evidence.get('gate') != args.gate:
        parser.error('Evidence does not match requested gate')
    if evidence.get('ok') is True:
        parser.error('Latest evidence is successful; failure ZIP not needed')
    result = build_failure_bundle(gate=args.gate, evidence_path=detail,
        report_path=report_path, evidence=evidence,
        reason='live-operation-failed', process='existing-evidence')
    print('WORKER_LIVE_FAILURE_BUNDLE status=saved', 'path='+result['path'],
          'files='+str(result['files']), 'screenshots='+str(result['screenshots']),
          'missing='+str(result['missing']), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
