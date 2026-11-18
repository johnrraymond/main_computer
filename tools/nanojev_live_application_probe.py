#!/usr/bin/env python3
"""Bounded native NanoJev/CLEF application probe, not contract-graph certification.

Exercises the *installed Main Computer provider* against the actual managed
NanoJev HTTP application. CLEF's evaluate operation is distinct from the
research adapter's extract_contract JSONL protocol. They must never be equated.

Only HTTP(S) loopback is permitted; no Stable Hub, marketplace, or credit paths.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
import ipaddress
import math
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from typing import Any

DEFAULT_URL = 'http://127.0.0.1:9765'
EXPECTED_OPERATIONS = ('nanojev.clef.evaluate.v1', 'nanojev.clef.pairwise-batch.v1')


def validate_local_url(value: str) -> str:
    """Prevent accidental probes of production networks or nonlocal services."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError('NANOJEV_LIVE_URL_INVALID')
    parsed = urlsplit(value)
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('NANOJEV_LIVE_URL_INVALID') from exc
    local = host == 'localhost'
    if host and not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    if (not local or parsed.scheme != 'http' or not port
            or parsed.path not in ('', '/') or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('NANOJEV_LIVE_LOOPBACK_ONLY')
    return value.rstrip('/')


def _fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    files = ('main_computer/router.py', 'main_computer/config.py',
             'main_computer/providers/nanojev.py',
             'docker/nanojev/clef_service.py',
             'docker/nanojev/entrypoint.py')
    return {file: sha256((root / file).read_bytes()).hexdigest()
            for file in files if (root / file).is_file()}


def _comparison(result: dict[str, Any]) -> tuple[bool, tuple[float, float]]:
    """Check one real two-outcome answer, not its accuracy against assumed gold."""
    try:
        row = result['result']['states'][0]['answers']['receipt']
        values = row['probabilities']
        if (row['type'] != 'boolean' or not isinstance(row['value'], bool)
                or set(values) != {'false', 'true'}):
            raise ValueError('NATIVE_CLEF_BOOLEAN_RESULT_MALFORMED')
        p0, p1 = float(values['false']), float(values['true'])
        if not (math.isfinite(p0) and math.isfinite(p1) and 0 <= p0 <= 1
                and 0 <= p1 <= 1 and abs(p0 + p1 - 1) <= 1e-5):
            raise ValueError('NATIVE_CLEF_PROBABILITIES_INVALID')
        return row['value'], (p0, p1)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('NATIVE_CLEF_BOOLEAN_RESULT_MALFORMED') from exc


def run_probe(*, base_url: str = DEFAULT_URL, timeout_s: float = 120,
              provider_factory=None) -> dict[str, Any]:
    """Probe service discovery, exact checkpoint execution, and stable repeated inference.

    A passing result means the real CLEF service was reached through the
    Main Computer NanoJev provider and executed a native CLEF request.
    It does *not* imply that the research contract extractor is production-bound.
    """
    url = validate_local_url(base_url)
    if not 0 < timeout_s <= 900:
        raise ValueError('NANOJEV_LIVE_TIMEOUT_INVALID')
    from main_computer.providers.nanojev import (CLEF_EVALUATE, CLEF_MODEL,
                                                  NanoJevClefProvider,
                                                  NanoJevProviderError)
    if provider_factory is None:
        # Route through MainComputer.build -> MainComputer.solve, not merely
        # a side-car REST client. Do not modify global environment variables.
        from main_computer.config import MainComputerConfig
        from main_computer.router import MainComputer
        configured = replace(MainComputerConfig.from_env(), provider='nanojev',
                             model=CLEF_MODEL, nanojev_base_url=url,
                             nanojev_timeout_s=timeout_s)
        computer = MainComputer.build(configured)
        provider = computer.provider
        native_solve = computer.solve
        route = 'main_computer.router.MainComputer.build/solve -> NanoJevClefProvider'
    else:
        provider = provider_factory(model=CLEF_MODEL, base_url=url, timeout_s=timeout_s)
        native_solve = provider.solve
        route = 'test-provider-injection'
    if not isinstance(provider, NanoJevClefProvider):
        raise RuntimeError('REAL_MAIN_COMPUTER_NANOJEV_ROUTING_NOT_ACTIVE')
    start = monotonic()
    health = provider.health()
    catalog = provider.discover_models()
    chosen = next((entry for entry in catalog['models'] if entry['id'] == CLEF_MODEL), None)
    if chosen is None or not set(EXPECTED_OPERATIONS).issubset(set(chosen.get('operations', []))):
        raise RuntimeError('NATIVE_CLEF_OPERATIONS_UNAVAILABLE')
    pin = chosen['checkpoint']['release_manifest_sha256']
    if health.get('release_manifest_sha256') != pin:
        raise RuntimeError('NATIVE_CLEF_HEALTH_CHECKPOINT_MISMATCH')

    sample = {'states': [{'id': 'native-acceptance-01',
                          'state': 'The receipt has been issued.',
                          'questions': {'receipt': {
                              'type': 'boolean',
                              'instructions': 'Has the receipt been issued?',
                              'criteria': {'false': 'The receipt has not been issued.',
                                           'true': 'The receipt has been issued.'}}}}]}
    first = native_solve(capability=CLEF_EVALUATE, payload=sample)
    second = native_solve(capability=CLEF_EVALUATE, payload=sample)
    a = _comparison(first)
    b = _comparison(second)
    if first.get('schema') != 'main-computer.nanojev-solution.v1':
        raise RuntimeError('NATIVE_CLEF_APPLICATION_SOLUTION_SCHEMA_INVALID')
    if any(solution.get('model', {}).get('release_manifest_sha256') != pin
           or solution.get('capability') != CLEF_EVALUATE for solution in (first, second)):
        raise RuntimeError('NATIVE_CLEF_EXECUTION_IDENTITY_MISMATCH')
    if a[0] != b[0] or any(abs(x-y) > 1e-5 for x, y in zip(a[1], b[1])):
        raise RuntimeError('NATIVE_CLEF_REPEATABILITY_FAILED')
    # Negative boundary: the live CLEF provider must reject the research-only
    # extract_contract operation; never send contract text as native CLEF work.
    if 'extract_contract' in chosen.get('operations', []):
        raise RuntimeError('NATIVE_CLEF_CONTRACT_CAPABILITY_CHANGED_REVIEW_REQUIRED')
    try:
        native_solve(capability='extract_contract', payload={'text': 'test'})
    except NanoJevProviderError:
        unsupported_rejected = True
    else:
        unsupported_rejected = False
    if not unsupported_rejected:
        raise RuntimeError('NATIVE_CLEF_UNSUPPORTED_CONTRACT_OPERATION_ADMITTED')
    last = provider.health()
    if last.get('release_manifest_sha256') != pin:
        raise RuntimeError('NATIVE_CLEF_CHECKPOINT_CHANGED_DURING_TEST')
    return {
        'status': 'PASS_NATIVE_CLEF_APPLICATION_BOUND',
        'base_url': url, 'application_entrypoint': route,
        'backend': 'docker/nanojev/clef_service.py:/api/infer',
        'model': CLEF_MODEL,
        'operation': CLEF_EVALUATE,
        'checkpoint_sha256': pin,
        'release_name': str(first['model']['release_name']),
        'requests_executed': 2,
        'native_response_shape_valid': True,
        'repeatability_same_live_process': True,
        'service_restart_tested': False,
        'unsupported_contract_operation_refused': True,
        'contract_extraction_production_bound': False,
        'native_answer_is_not_independently_gold_scored': True,
        'source_sha256': _fingerprints(),
        'elapsed_s': round(monotonic() - start, 3),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=DEFAULT_URL)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, default=None,
                        help='Optional create-only native application probe report')
    args = parser.parse_args(argv)
    if args.output is not None and args.output.exists():
        raise FileExistsError('NATIVE_APPLICATION_PROBE_REPORT_EXISTS_REFUSED')
    try:
        result = run_probe(base_url=args.base_url, timeout_s=args.timeout)
        ok = True
    except Exception as exc:
        result = {'status': 'FAILED', 'error_type': type(exc).__name__,
                  'error': str(exc)[:500], 'contract_extraction_production_bound': False}
        ok = False
    result['schema'] = 'nanojev.native-application-probe.v1'
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write('\n')
    print(json.dumps({'status': result['status'], 'report': str(args.output) if args.output else None,
                      'contract_extraction_production_bound': False}, sort_keys=True))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
