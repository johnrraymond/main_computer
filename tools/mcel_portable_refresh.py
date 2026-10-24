#!/usr/bin/env python3
"""Refresh the existing source-bound, live 19-intent portable certificate.

This is orchestration only. The existing Chromium producer, external authority,
certificate verifier and canonical truth audit retain their independent roles.
An old certificate is never re-labelled or silently accepted as fresh proof.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from main_computer.mcel_portable_certification import (  # noqa: E402
    DEFAULT_CERTIFICATE, verify_certificate,
)

PROOF_ATTEMPT = Path('runtime/reports/mcel-portable/mcel-portable-last-attempt.json')
REFRESH_REPORT = Path('runtime/reports/mcel-portable/mcel-portable-refresh-result.json')
REQUIRED_APPS = {'calculator', 'code-editor'}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _execute(repo: Path, argv: list[str], timeout: float) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, cwd=repo, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'blocked', 'reason': str(exc)}
    return {'status': 'pass' if result.returncode == 0 else 'refused',
            'exitCode': result.returncode,
            'stdoutTail': result.stdout[-1200:],
            'stderrTail': result.stderr[-1200:]}


def refresh(repo: Path, *, model: str, ollama_url: str = 'http://127.0.0.1:11434',
            external_timeout_s: float = 90, timeout_s: float = 900,
            max_age_hours: float = 168,
            execute: Callable[[Path, list[str], float], dict[str, Any]] = _execute,
            verify: Callable[..., dict[str, Any]] = verify_certificate) -> dict[str, Any]:
    """Regenerate real evidence and atomically replace the certificate iff admitted.

    Refusing a new run preserves the previous file but returns a failing result;
    the existing truth audit independently rejects stale saved certificates.
    """
    repo = repo.resolve()
    parsed = urlsplit(ollama_url)
    if (not model.strip() or parsed.scheme != 'http' or
            parsed.hostname not in ('127.0.0.1', 'localhost') or
            parsed.username is not None or parsed.password is not None):
        raise ValueError('Explicit local model and loopback Ollama URL are required')
    if not all(math.isfinite(value) and value > 0 for value in
               (external_timeout_s, timeout_s, max_age_hours)):
        raise ValueError('Timeout and maximum evidence age must be positive')

    destination = repo / DEFAULT_CERTIFICATE
    destination.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {'schema': 'mcel.portable-refresh-result.v1',
                              'status': 'refused', 'certificateUpdated': False,
                              'eligibleAppIds': [], 'stages': {}}
    with tempfile.TemporaryDirectory(prefix='.portable-refresh-', dir=destination.parent) as temp:
        staging = Path(temp)
        proof = staging / 'proof.json'
        candidate = staging / 'certificate.json'
        cmd = [sys.executable, 'tools/mcel_portable_intent_probe.py',
               '--repo-root', str(repo), '--authorize-external', '--model', model,
               '--ollama-url', ollama_url,
               '--external-timeout-s', str(external_timeout_s), '--output', str(proof)]
        result['stages']['liveProbe'] = execute(repo, cmd, timeout_s)
        observed = _read_json(proof)
        if observed is not None:
            # Preserve the attempted proof for troubleshooting, never for admission.
            shutil.copyfile(proof, repo / PROOF_ATTEMPT)
        if (result['stages']['liveProbe'].get('status') != 'pass' or
                observed is None or observed.get('passed') is not True or
                observed.get('fullSemanticRuntimeProven') is not True):
            result['reason'] = 'live-19-intent-proof-not-passing'
        else:
            cmd = [sys.executable, 'tools/mcel_portable_certify.py',
                   '--repo-root', str(repo), '--proof', str(proof),
                   '--output', str(candidate), '--max-age-hours', str(max_age_hours)]
            result['stages']['certificateProducer'] = execute(repo, cmd, timeout_s)
            document = _read_json(candidate)
            if document is None or result['stages']['certificateProducer'].get('status') != 'pass':
                result['reason'] = 'certificate-producer-refused'
            else:
                verdict = verify(document, repo, max_age_hours=max_age_hours)
                result['stages']['independentVerification'] = verdict
                if (verdict.get('passed') is True and verdict.get('status') == 'pass'
                        and set(verdict.get('eligibleAppIds') or []) == REQUIRED_APPS):
                    os.replace(candidate, destination)
                    result.update(status='pass', certificateUpdated=True,
                                  eligibleAppIds=sorted(REQUIRED_APPS),
                                  reason='fresh-independent-portable-certificate-admitted')
                else:
                    result['reason'] = 'independent-certificate-verification-refused'
    path = repo / REFRESH_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=REPO)
    parser.add_argument('--authorize-external', action='store_true',
                        help='Explicitly authorize local Ollama and Mathics proof operations')
    parser.add_argument('--model', required=True, help='Exact installed local Ollama model')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    parser.add_argument('--external-timeout-s', type=float, default=90)
    parser.add_argument('--timeout-seconds', type=float, default=900)
    parser.add_argument('--max-age-hours', type=float, default=168)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if not args.authorize_external:
        parser.error('--authorize-external required for live, external-provider proof')
    try:
        report = refresh(args.repo_root, model=args.model,
                         ollama_url=args.ollama_url,
                         external_timeout_s=args.external_timeout_s,
                         timeout_s=args.timeout_seconds,
                         max_age_hours=args.max_age_hours)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print('portable refresh:', report['status'], '-', report.get('reason'))
        print('certificate updated:', report['certificateUpdated'])
        print('report:', REFRESH_REPORT)
    return 0 if report['status'] == 'pass' else 4


if __name__ == '__main__':
    sys.exit(main())
