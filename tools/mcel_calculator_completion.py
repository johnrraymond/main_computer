#!/usr/bin/env python3
"""Fail-closed Calculator completion check using existing MCEL proof authorities.

This certifies an explicitly scoped host-bound application: the existing
acceptance runner, fresh generated-adapter Chromium/IR proof, generic layout
inference and Lab classifications, and a fresh deterministic AI tool receipt.
It does NOT convert host-bound proof into the package-host/global truth gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_calculator_ai_tool import execute_calculator_ai_tool

SCHEMA = 'mcel.calculator.host-bound-completion.v1'
REQUIRED_CONTRACTS = (
    'calculator.acceptance.full-semantic-runtime',
    'calculator.acceptance.layout-inference',
    'calculator.acceptance.no-hidden-mutation',
)
SCOPE = 'host-bound-runtime-layout-and-deterministic-ai-tool'
EXPRESSION = '3 m + 40 cm'


def _is_sha256_digest(value: Any, *, prefixed: bool = True) -> bool:
    """Validate a producer's SHA-256 representation, not just its prefix.

    The repository provenance authority returns raw lowercase hex; MCEL's
    semantic, package, projection, and AI authorities return ``sha256:`` hex.
    Neither representation is silently rewritten for proof comparison.
    """
    if not isinstance(value, str):
        return False
    if prefixed:
        if not value.startswith('sha256:'):
            return False
        value = value[len('sha256:'):]
    return len(value) == 64 and all(char in '0123456789abcdef' for char in value)

def _receipt_digest_valid(receipt: Mapping[str, Any]) -> bool:
    evaluation = receipt.get('evaluation')
    if not isinstance(evaluation, dict):
        return False
    for key in ('coreSha256', 'runnerSha256', 'sourceBindingFingerprint'):
        value = receipt.get(key)
        if not isinstance(value, str) or len(value) != 71 or not value.startswith('sha256:'):
            return False
    canonical = json.dumps({
        'action': 'calculator.evaluate',
        'expression': EXPRESSION,
        'evaluation': evaluation,
        'semanticFingerprint': receipt.get('semanticFingerprint'),
        'sourceBindingFingerprint': receipt.get('sourceBindingFingerprint'),
        'coreSha256': receipt.get('coreSha256'),
        'runnerSha256': receipt.get('runnerSha256'),
    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    expected = 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return receipt.get('receiptSha256') == expected


def assess_completion(proof: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Pure evaluation: missing, skipped, stale or narrower evidence fails closed."""
    contracts = proof.get('acceptanceContracts') or {}
    contract_results = {}
    for contract_id in REQUIRED_CONTRACTS:
        contract = contracts.get(contract_id) or {}
        summary = contract.get('summary') or {}
        status = contract.get('status')
        passed = (status == 'pass' and contract.get('executed') is True
                  and contract.get('enforceable') is True
                  and bool(contract.get('bindingId'))
                  and int(summary.get('passed') or 0) > 0
                  and all(int(summary.get(k) or 0) == 0 for k in ('skipped','failed','errors','xfailed','xpassed')))
        contract_results[contract_id] = {
            'status': 'pass' if passed else 'fail',
            'bindingId': contract.get('bindingId') or '',
            'passedTests': int(summary.get('passed') or 0),
            'skippedTests': int(summary.get('skipped') or 0),
        }
    coverage = proof.get('intentCoverage') or {}
    provenance_checks = {
        'semanticFingerprintsValid': (
            _is_sha256_digest(proof.get('semanticFingerprint'))
            and _is_sha256_digest(receipt.get('semanticFingerprint'))),
        'semanticFingerprintsMatch': (
            proof.get('semanticFingerprint') == receipt.get('semanticFingerprint')),
        # build_repository_provenance() returns a bare SHA-256 hexdigest.
        'repositoryFingerprintValid': _is_sha256_digest(
            proof.get('repositoryFingerprint'), prefixed=False),
        # Package and runtime projection producers return sha256:<64 hex>.
        'packageFingerprintValid': _is_sha256_digest(proof.get('packageFingerprint')),
        'runtimeProjectionFingerprintValid': _is_sha256_digest(
            proof.get('runtimeProjectionFingerprint')),
        'sourceBindingFingerprintValid': _is_sha256_digest(
            receipt.get('sourceBindingFingerprint')),
        'receiptFingerprintValid': _is_sha256_digest(receipt.get('receiptSha256')),
    }
    provenance_ok = all(provenance_checks.values())
    runtime_ok = (proof.get('passed') is True and proof.get('appId') == 'calculator'
                  and proof.get('truthStatus') == 'host-bound-ir-native-authoritative'
                  and proof.get('fullSemanticRuntimeProven') is False
                  and proof.get('pendingAcceptanceContracts') == []
                  and coverage.get('passed') is True
                  and coverage.get('declaredIntentCount') == coverage.get('coveredIntentCount') == 11
                  and (proof.get('evidence') or {}).get('browserObservation',{}).get('fresh') is True
                  and (proof.get('evidence') or {}).get('effects',{}).get('status') == 'closed')
    evaluation = receipt.get('evaluation') or {}
    ai_ok = (receipt.get('schema') == 'mcel.calculator.ai-tool-receipt.v1'
             and receipt.get('ok') is True and receipt.get('action') == 'calculator.evaluate'
             and receipt.get('intentId') == 'intent:calculator.evaluate-expression'
             and receipt.get('executionLane') == 'local-arithmetic'
             and receipt.get('effectStatus') == 'none'
             and evaluation.get('ok') is True
             and evaluation.get('displayValue') == '3.4 m'
             and _receipt_digest_valid(receipt))
    checks = {
        'acceptanceContracts': all(item['status'] == 'pass' for item in contract_results.values()),
        'freshHostBoundProof': runtime_ok,
        'semanticAndSourceProvenance': provenance_ok,
        'deterministicAiCalculator': ai_ok,
    }
    passed = all(checks.values())
    return {
        'schema': SCHEMA, 'appId': 'calculator',
        'passed': passed, 'status': 'pass' if passed else 'fail',
        'completionScope': SCOPE, 'hostBoundApplicationComplete': passed,
        'fullSemanticRuntimeProven': False,
        'packageHostTruthGateProven': False,
        'liveOllamaModelInvoked': False,
        'checks': checks, 'contracts': contract_results,
        'provenanceChecks': provenance_checks,
        'semanticFingerprint': proof.get('semanticFingerprint'),
        'sourceBindingFingerprint': receipt.get('sourceBindingFingerprint'),
        'repositoryFingerprint': proof.get('repositoryFingerprint'),
        'aiReceiptSha256': receipt.get('receiptSha256'),
    }


def _run_fresh_host_bound_authority(*, repo: Path, app_id: str, headed: bool) -> Mapping[str, Any]:
    """Bound execution of the existing CLI authority; no alternate proof implementation."""
    cmd = [sys.executable, str(repo / 'tools/mcel_phase1_host_bound_prove.py'),
           '--repo-root', str(repo), '--app', app_id, '--check', '--json']
    if headed:
        cmd.append('--headed')
    completed = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=300, check=False)
    if completed.returncode:
        raise RuntimeError('Fresh host-bound authority failed: ' +
                           (completed.stderr or completed.stdout).strip()[-2000:])
    try:
        report = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError('Host-bound authority did not return JSON.') from exc
    if not isinstance(report, dict) or report.get('passed') is not True:
        raise RuntimeError('Host-bound authority did not provide a passing proof.')
    return report


def run_completion(repo: Path, *, headed: bool=False,
                   prove: Callable[..., Mapping[str, Any]]=_run_fresh_host_bound_authority,
                   evaluate: Callable[..., Mapping[str, Any]]=execute_calculator_ai_tool) -> dict[str, Any]:
    root = repo.resolve()
    try:
        proof = prove(repo=root, app_id='calculator', headed=headed)
        receipt = evaluate({'action':'calculator.evaluate','expression':EXPRESSION}, repo_root=root)
        return assess_completion(proof, receipt)
    except (Exception) as exc:
        return {'schema':SCHEMA, 'appId':'calculator', 'passed':False, 'status':'fail',
                'completionScope':SCOPE, 'hostBoundApplicationComplete':False,
                'fullSemanticRuntimeProven':False, 'packageHostTruthGateProven':False,
                'error':f'{type(exc).__name__}: {exc}'}


def main(argv: Sequence[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    result=run_completion(args.repo_root,headed=args.headed)
    if args.json:
        print(json.dumps(result,sort_keys=True,indent=2,ensure_ascii=False))
    else:
        print(f"MCEL Calculator host-bound completion: {result['status']}")
        for key, passed in (result.get('checks') or {}).items():
            print(f"  {key}: {'pass' if passed else 'fail'}")
        if result.get('error'):
            print(result['error'])
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
