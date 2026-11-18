#!/usr/bin/env python3
"""NanoJev system acceptance: frozen pipeline + *separate* production binding.

Run from main_computer repo root (Windows):
  & $py -m tools.nanojev_system_acceptance --device cuda \
    --output runtime/reports/nanojev-system-acceptance-0001.json

The default performs real read-only JSONL process integration of the frozen
research candidate, reboot/restart and invalid-input tests, historical regression,
new-domain coverage, provenance, and fail-closed decisions. It never claims
FULL SYSTEM PASS merely because the research adapter works: to certify actual
application wiring, pass --production-command-json with an exact JSON array of
argv for the real installed process implementing the SAME JSONL wire protocol.
The contract is documented in tools.nanojev_system_contract_service.

A missing production binding, zero novel-language coverage, any false confident
acceptance, source drift, or operational fault blocks release and returns nonzero.
This is not an additional feature/accuracy bakeoff; no training or selection.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from tools import nanojev_sealed_overlap_external_generalization_bakeoff as ext
from tools import nanojev_sealed_overlap_negative_consistency_bakeoff as neg
from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_system_contract_service as service

SCHEMA = 'nanojev.system-acceptance.v1'
STATUS_PASS = 'PASS_FULL_SYSTEM_BOUNDED_INTEGRATION'
STATUS_BLOCK = 'BLOCKED_NOT_CERTIFIED'
REPORT_DEFAULT = Path('runtime/reports/nanojev-system-acceptance-0001.json')
EXTERNAL_DEFAULT = Path('runtime/reports/sealed-overlap-external-generalization-0001.json')
NEGATIVE_DEFAULT = Path('runtime/reports/sealed-overlap-negative-consistency-0001.json')


def cases_from_existing_frozen_fixtures() -> list[dict]:
    """Rebuild historical and fresh authored cases without changing fixtures."""
    base = binding.load_fixture(binding.FIXTURE)
    st = stress.load_fixture(stress.FIXTURE, base)
    iface = interface.load_fixture(interface.FIXTURE, base, st)
    rf = repair.load_fixture(repair.FIXTURE, base, iface, st)
    bf = blind.load_fixture(blind.FIXTURE, base, st, iface, rf)
    sf = scope.load_fixture(scope.FIXTURE, base, st, iface, rf, bf)
    nf = neg.load_fixture(neg.FIXTURE, base, st, iface, rf, bf, sf)
    ef = ext.load_fixture(ext.FIXTURE, base, (st, iface, rf, bf, sf, nf))
    data = stress.derive_data(base, st, seed=261117, train_atoms=216, selection_groups=16, final_groups=24)
    historical = scope.make_holdout(data, base, st, iface, rf, bf, sf, seed=261117, groups=24)
    fresh = ext.make_holdout(data, base, st, iface, rf, bf, sf, nf, ef, seed=261117, groups=24)
    if len(historical) != 600 or len(fresh) != 442:
        raise ValueError('SYSTEM_CASE_COUNT_DRIFT')
    cases = []
    for cohort, rows in [('historical', historical), ('fresh', fresh)]:
        for row in rows:
            truth = row.get('truth_atoms')
            policy = row.get('policy', row.get('target'))
            if policy not in {'supported', 'abstain'}:
                raise ValueError('SYSTEM_CASE_POLICY_INVALID')
            cases.append({'id': row['id'], 'cohort': cohort, 'family': row.get('family', row['kind']),
                          'kind': row['kind'], 'text': row['text'], 'gold': truth,
                          'policy': policy,
                          'external_source': row.get('source_url')})
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('SYSTEM_DUPLICATE_CASE_IDS')
    return cases



def load_expert_cases(path: Path, known: list[dict]) -> list[dict]:
    """Independently supplied, untrained full-slot gold; authorship is attested.

    The loader checks provenance and content separation but cannot itself
    authenticate the reviewer's identity or legal expertise.
    """
    obj = json.loads(path.read_text(encoding='utf8'))
    att = obj.get('attestation', {})
    if (obj.get('schema') != 'nanojev.system-expert-corpus.v1'
        or not isinstance(att, dict)
        or att.get('independently_authored') is not True
        or not isinstance(att.get('reviewer'), str) or len(att['reviewer'].strip()) < 3
        or not isinstance(att.get('origin'), str) or len(att['origin'].strip()) < 3):
        raise ValueError('EXPERT_CORPUS_PROVENANCE_OR_SCHEMA_INVALID')
    cases = obj.get('cases')
    if not isinstance(cases, list) or len(cases) < 24:
        raise ValueError('EXPERT_CORPUS_NEEDS_AT_LEAST_24_CASES')
    previous = {c['text'].strip() for c in known}
    identifiers = set()
    texts = set()
    out = []
    for row in cases:
        if not isinstance(row, dict) or set(row) != {'id', 'text', 'expected_relations'}:
            raise ValueError('EXPERT_CORPUS_CASE_SHAPE_INVALID')
        identifier, candidate, expected = row['id'], row['text'], row['expected_relations']
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 120
            or identifier in identifiers or not isinstance(candidate, str)
            or not candidate.strip() or candidate.strip() in previous or candidate in texts):
            raise ValueError('EXPERT_CORPUS_DUPLICATE_OR_KNOWN_TEXT')
        identifiers.add(identifier)
        texts.add(candidate)
        if expected is not None:
            if (not isinstance(expected, list) or not expected
                or any(not isinstance(e, dict) or set(e) != set(binding.FIELDS)
                       or any(not isinstance(e[k], str) or not e[k] for k in binding.FIELDS[:-1])
                       or type(e['polarity']) is not int or e['polarity'] not in {-1, 1}
                       for e in expected)):
                raise ValueError('EXPERT_CORPUS_GOLD_INVALID')
            gold = [tuple(e[k] for k in binding.FIELDS) for e in expected]
            policy = 'supported'
        else:
            gold = None
            policy = 'abstain'
        out.append({'id':'expert:'+identifier, 'cohort':'expert', 'family':'expert',
                    'kind':'expert_authored', 'text':candidate, 'gold':gold,
                    'policy':policy, 'external_source':att['origin']})
    if sum(x['policy']=='supported' for x in out) < 12 or sum(x['policy']=='abstain' for x in out) < 12:
        raise ValueError('EXPERT_CORPUS_NEEDS_BALANCED_SUPPORTED_AND_ABSTAIN')
    return out


def argv_for_candidate(device: str, prior: Path, external: Path):
    return [sys.executable, '-m', 'tools.nanojev_system_contract_service',
            '--device', device, '--batch-input', '--prior-report', str(prior), '--external-report', str(external)]


def make_request(case: dict):
    return {'request_id': case['id'], 'operation': 'extract_contract', 'text': case['text']}


def fault_requests():
    return [
        {'request_id': 'fault:unknown-op', 'operation': 'does_not_exist', 'text': 'x'},
        {'request_id': 'fault:empty', 'operation': 'extract_contract', 'text': ''},
        {'request_id': 'fault:missing', 'operation': 'extract_contract'},
        {'request_id': 'fault:extra', 'operation': 'extract_contract', 'text': 'x', 'gold': 'forbidden'},
        {'request_id': 'fault:nontext', 'operation': 'extract_contract', 'text': {'a': 1}},
        {'request_id': 'fault:multiline', 'operation': 'extract_contract', 'text': 'A contract\nB contract'},
        {'request_id': 'fault:nul', 'operation': 'extract_contract', 'text': 'abc\u0000def'},
        {'request_id': 'fault:huge', 'operation': 'extract_contract', 'text': 'z' * 12001},
    ]


def invoke_jsonl(argv: list[str], requests: list[dict], *, timeout: int, cwd: Path) -> dict:
    """Launch a real OS process, capture stdout/stderr/exit; no shell expansion."""
    payload = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in requests)
    start = time.monotonic()
    # Windows subprocess pipes may inherit a legacy code page rather than UTF-8.
    # All requests and source hashes must refer to the exact same Unicode text.
    # Force Python children (research and application adapters) to decode stdin
    # and encode stdout as UTF-8; retain strict decoding on the parent as well.
    child_env = os.environ.copy()
    child_env['PYTHONIOENCODING'] = 'utf-8:strict'
    child_env['PYTHONUTF8'] = '1'
    try:
        p = subprocess.run(argv, input=payload, encoding='utf-8', errors='strict',
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=str(cwd), env=child_env, timeout=timeout, shell=False, check=False)
        output = p.stdout.splitlines()
        parsed = []
        for line in output:
            try:
                parsed.append(json.loads(line))
            except ValueError:
                parsed.append({'invalid_stdout_line': line[:1000]})
        return {'argv': argv, 'elapsed_s': round(time.monotonic() - start, 3),
                'exit_code': p.returncode, 'responses': parsed, 'response_count': len(parsed),
                'stderr_tail': p.stderr[-3000:]}
    except subprocess.TimeoutExpired as exc:
        return {'argv': argv, 'elapsed_s': round(time.monotonic() - start, 3),
                'exit_code': None, 'responses': [], 'response_count': 0,
                'stderr_tail': str(exc), 'timeout': True}
    except (OSError, ValueError, UnicodeError) as exc:
        return {'argv': argv, 'elapsed_s': round(time.monotonic() - start, 3),
                'exit_code': None, 'responses': [], 'response_count': 0,
                'stderr_tail': f'{type(exc).__name__}: {exc}', 'startup_failure': True}


def score(cases: list[dict], result: dict) -> dict:
    responses = result['responses']
    problems = []
    if result.get('exit_code') != 0:
        problems.append('PROCESS_EXIT_NOT_ZERO')
    if len(responses) != len(cases):
        problems.append('PROTOCOL_RESPONSE_COUNT')
    stats = defaultdict(Counter)
    known_base = binding.load_fixture(binding.FIXTURE)
    errors = []
    for idx, case in enumerate(cases):
        group = case['cohort'] + '/' + case['family']
        totals = stats[group]
        totals['total'] += 1
        if case['policy'] == 'supported':
            totals['supported_total'] += 1
        else:
            totals['abstain_required_total'] += 1
        if idx >= len(responses) or not isinstance(responses[idx], dict):
            errors.append({'id': case['id'], 'fault': 'MISSING_RESPONSE'})
            continue
        response = responses[idx]
        if response.get('request_id') != case['id'] or response.get('schema') != service.SCHEMA:
            errors.append({'id': case['id'], 'fault': 'PROTOCOL_ID_OR_SCHEMA'})
            continue
        if response.get('source_sha256') != sha256(case['text'].strip().encode('utf8')).hexdigest():
            errors.append({'id': case['id'], 'fault': 'INPUT_PROVENANCE_MISMATCH'})
            continue
        decision = response.get('decision')
        if decision not in {'accepted', 'abstain'}:
            errors.append({'id': case['id'], 'fault': 'UNEXPECTED_ERROR', 'reasons': response.get('reasons')})
            continue
        totals[decision] += 1
        if decision == 'abstain':
            if response.get('relations') or response.get('contradiction') is not None:
                errors.append({'id': case['id'], 'fault': 'ABSTENTION_EMITTED_SEMANTICS'})
            continue
        relations = response.get('relations')
        if not isinstance(relations, list) or not relations:
            errors.append({'id': case['id'], 'fault': 'ACCEPTED_WITHOUT_RELATIONS'})
            continue
        if case['policy'] == 'abstain':
            totals['unsafe_accepted'] += 1
            errors.append({'id': case['id'], 'fault': 'UNSUPPORTED_ACCEPTANCE'})
            continue
        received = [tuple(atom.get(field) for field in binding.FIELDS) for atom in relations if isinstance(atom, dict)]
        expected = [tuple(atom) for atom in case['gold']]
        graph_matches = len(received) == len(relations) and sorted(received) == sorted(expected)
        if not graph_matches:
            totals['false_confident_wrong'] += 1
            errors.append({'id': case['id'], 'fault': 'WRONG_ACCEPTED_GRAPH', 'expected': expected, 'received': received})
        else:
            totals['correct_accepted'] += 1
        # Every accepted result must contain an internally consistent contradiction decision.
        if graph_matches and not isinstance(response.get('contradiction'), bool):
            errors.append({'id': case['id'], 'fault': 'CONTRADICTION_FIELD_MISSING'})
        elif graph_matches:
            try:
                expected_contradiction = repair.contradiction(expected, known_base)
            except ValueError:
                errors.append({'id': case['id'], 'fault': 'CONTRADICTION_GOLD_REPRESENTATION_UNSUPPORTED'})
            else:
                if response['contradiction'] != expected_contradiction:
                    errors.append({'id': case['id'], 'fault': 'CONTRADICTION_DECISION_WRONG'})
    if errors:
        problems.append('CASE_FAILURES')
    return {'passed_protocol': not problems, 'problems': sorted(set(problems)),
            'by_family': {k: dict(v) for k, v in sorted(stats.items())},
            'errors': errors[:60], 'errors_total': len(errors)}


def verify_faults(requests: list[dict], result: dict):
    failures = []
    if result.get('exit_code') != 0 or len(result.get('responses', [])) != len(requests):
        failures.append('FAULT_PROCESS_OR_COUNT')
    for req, res in zip(requests, result.get('responses', [])):
        want = 'abstain' if req['request_id'] == 'fault:multiline' else 'error'
        if res.get('request_id') != req['request_id'] or res.get('decision') != want or res.get('relations'):
            failures.append('FAULT_NOT_ISOLATED:' + req['request_id'])
    return failures


def run(args: argparse.Namespace, *, invoke: Callable = invoke_jsonl, fixtures: Callable = cases_from_existing_frozen_fixtures) -> dict:
    if args.output.exists():
        raise FileExistsError('SYSTEM_REPORT_EXISTS_REFUSED')
    repo = Path.cwd().resolve()
    started = datetime.now(timezone.utc).isoformat()
    report = {'schema': SCHEMA, 'status': STATUS_BLOCK, 'started_at': started,
              'mode': 'READ_ONLY_NO_SEAL_NO_PRODUCTION_MUTATION',
              'scope': {'candidate_adapter': 'tools.nanojev_system_contract_service',
                        'production_integration': 'NOT_BOUND' if not args.production_command_json else 'EXPLICITLY_BOUND',
                        'external_human_gold': 'NOT_AVAILABLE',
                        'qwen_weights_modified': False}, 'checks': {}, 'blockers': []}
    try:
        pins = ext.fingerprint_preflight()
        prior_paths = {'base_fixture_sha256': binding.FIXTURE, 'stress_fixture_sha256': stress.FIXTURE,
                       'interface_fixture_sha256': interface.FIXTURE, 'repair_fixture_sha256': repair.FIXTURE,
                       'blind_fixture_sha256': blind.FIXTURE, 'scope_fixture_sha256': scope.FIXTURE,
                       'new_fixture_sha256': neg.FIXTURE}
        ext.validate_prior(args.prior_report, prior_paths)
        e = json.loads(args.external_report.read_text(encoding='utf8'))
        if (e.get('schema') != ext.SCHEMA or e.get('status') != ext.STATUS
            or e.get('source_pins', {}).get('module_sha256') != pins
            or e.get('source_pins', {}).get('prior_report_sha256') != sha256(args.prior_report.read_bytes()).hexdigest()
            or e.get('source_pins', {}).get('fixture_sha256') != ext.FROZEN_HOLDOUT_SHA256
            or e.get('historical_canary', {}).get('matches_prior_216_of_216') is not True):
            raise ValueError('PRIOR_EXTERNAL_CUDA_EVIDENCE_INVALID')
        roots = [*{Path(p).resolve() for p in prior_paths.values()}, ext.FIXTURE.resolve(),
                 args.prior_report.resolve(), args.external_report.resolve(),
                 *[Path(mod.__file__).resolve() for mod in ext.MODULES],
                 Path(service.__file__).resolve(), Path(__file__).resolve()]
        if getattr(args, 'live_nanojev_url', None):
            # Pin the actual Main Computer router/provider and service sources
            # alongside the research candidate. A live check must not mutate them.
            from tools import nanojev_live_application_probe as live
            live.validate_local_url(args.live_nanojev_url)
            for rel in ('tools/nanojev_live_application_probe.py',
                        'main_computer/router.py', 'main_computer/config.py',
                        'main_computer/providers/nanojev.py',
                        'docker/nanojev/entrypoint.py',
                        'docker/nanojev/clef_service.py'):
                path = (repo / rel).resolve()
                if not path.is_file():
                    raise RuntimeError('NATIVE_APPLICATION_SOURCE_MISSING: ' + rel)
                roots.append(path)
        before = {str(p): sha256(p.read_bytes()).hexdigest() for p in roots}
        report['source_pins'] = {'file_sha256': before, 'frozen_modules': pins,
                                 'candidate_service_sha256': sha256(Path(service.__file__).read_bytes()).hexdigest()}
        report['checks']['frozen_preflight'] = 'PASS'
        cases = fixtures()
        report['cases'] = {'total': len(cases),
                           'historical': sum(c['cohort'] == 'historical' for c in cases),
                           'fresh': sum(c['cohort'] == 'fresh' for c in cases)}
        if len(cases) != 1042:
            raise ValueError('SYSTEM_CASES_NOT_EXHAUSTIVE')
        cmd = argv_for_candidate(args.device, args.prior_report, args.external_report)
        run_one = invoke(cmd, [make_request(c) for c in cases], timeout=args.timeout, cwd=repo)
        report['checks']['candidate_process'] = {'exit_code': run_one['exit_code'],
                          'response_count': run_one['response_count'], 'elapsed_s': run_one['elapsed_s'],
                          'stderr_tail': run_one.get('stderr_tail', '')}
        report['checks']['candidate_graphs'] = score(cases, run_one)
        if report['checks']['candidate_graphs']['problems']:
            report['blockers'].extend(report['checks']['candidate_graphs']['problems'])
        canaries = [c for c in cases if c['cohort'] == 'historical' and c['policy'] == 'supported']
        novel = [c for c in cases if c['cohort'] == 'fresh' and c['policy'] == 'supported' and c['family'] != 'known_control']
        def count_accept(rows):
            ids = {c['id'] for c in rows}
            return sum(r.get('decision') == 'accepted' and r.get('request_id') in ids for r in run_one['responses'])
        baseline_retention = count_accept(canaries)
        novel_coverage = count_accept(novel)
        report['capability'] = {'historical_supported_accepted': baseline_retention, 'historical_supported_total': len(canaries),
                                'novel_supported_accepted': novel_coverage, 'novel_supported_total': len(novel)}
        if baseline_retention != 216:
            report['blockers'].append('HISTORICAL_REGRESSION')
        if novel_coverage == 0:
            report['blockers'].append('ZERO_NOVEL_LANGUAGE_COVERAGE')
        # Process restart: same selected inputs after a complete OS-process exit.
        repeat = [c for c in cases if c['cohort'] == 'fresh' and c['kind'] in ('anchor_positive', 'anchor_negative', 'passive_voice', 'unknown_asset')][:8]
        second = invoke(cmd, [make_request(c) for c in repeat], timeout=args.timeout, cwd=repo)
        first_map = {r.get('request_id'): r for r in run_one['responses']}
        stable = (second['exit_code'] == 0 and second['response_count'] == len(repeat)
                  and all(second['responses'][j] == first_map.get(c['id']) for j, c in enumerate(repeat)))
        report['checks']['restart_repeatability'] = {'pass': stable, 'cases': len(repeat),
                                                  'exit_code': second['exit_code'], 'elapsed_s': second['elapsed_s']}
        if not stable:
            report['blockers'].append('RESTART_NONDETERMINISTIC')
        faults = fault_requests()
        fault_result = invoke(cmd, faults, timeout=args.timeout, cwd=repo)
        fault_failures = verify_faults(faults, fault_result)
        report['checks']['fault_isolation'] = {'pass': not fault_failures, 'failures': fault_failures,
                                               'exit_code': fault_result['exit_code']}
        report['blockers'].extend(fault_failures)
        after = {str(p): sha256(p.read_bytes()).hexdigest() for p in roots}
        report['checks']['source_immutability'] = {'pass': before == after}
        if before != after:
            report['blockers'].append('FROZEN_SOURCES_MUTATED')
        if args.production_command_json:
            actual_cmd = json.loads(args.production_command_json)
            if (not isinstance(actual_cmd, list) or not actual_cmd
                or not all(isinstance(x, str) and x for x in actual_cmd)):
                raise ValueError('PRODUCTION_COMMAND_JSON_MUST_BE_ARGV_ARRAY')
            if actual_cmd[:3] == cmd[:3] or 'nanojev_system_contract_service' in ' '.join(actual_cmd):
                raise ValueError('PRODUCTION_ENTRYPOINT_MUST_NOT_BE_RESEARCH_ADAPTER')
            actual = invoke(actual_cmd, [make_request(c) for c in cases], timeout=args.timeout, cwd=repo)
            report['checks']['production_process'] = {'exit_code': actual['exit_code'], 'response_count': actual['response_count'],
                                                       'stderr_tail': actual.get('stderr_tail', '')}
            report['checks']['production_graphs'] = score(cases, actual)
            if report['checks']['production_graphs']['problems']:
                report['blockers'].append('PRODUCTION_INTERFACE_FAILED')
            if actual['exit_code'] != 0:
                report['blockers'].append('PRODUCTION_PROCESS_FAILED')
            actual_repeat = invoke(actual_cmd, [make_request(c) for c in repeat], timeout=args.timeout, cwd=repo)
            original_map = {r.get('request_id'): r for r in actual['responses']}
            prod_stable = (actual_repeat['exit_code'] == 0 and actual_repeat['response_count'] == len(repeat)
                           and all(actual_repeat['responses'][j] == original_map.get(c['id'])
                                   for j, c in enumerate(repeat)))
            report['checks']['production_restart_repeatability'] = {'pass': prod_stable,
                        'exit_code': actual_repeat['exit_code']}
            if not prod_stable:
                report['blockers'].append('PRODUCTION_RESTART_NONDETERMINISTIC')
            prod_faults = invoke(actual_cmd, faults, timeout=args.timeout, cwd=repo)
            prod_fault_errors = verify_faults(faults, prod_faults)
            report['checks']['production_fault_isolation'] = {'pass': not prod_fault_errors,
                          'failures': prod_fault_errors}
            if prod_fault_errors:
                report['blockers'].append('PRODUCTION_FAULT_ISOLATION_FAILED')
        else:
            report['blockers'].append('REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED')
        # The installed application is a native CLEF inference service, not the
        # contract-graph research JSONL process. Exercise its actual HTTP provider
        # separately and never count its success as semantic extraction coverage.
        live_url = getattr(args, 'live_nanojev_url', None)
        if live_url:
            from tools import nanojev_live_application_probe as live
            try:
                native = live.run_probe(base_url=live_url,
                                        timeout_s=getattr(args, 'live_nanojev_timeout', 120))
                report['checks']['live_nanojev_application'] = native
                report['scope']['live_nanojev_application'] = native['status']
                if native['contract_extraction_production_bound'] is False:
                    if 'REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED' in report['blockers']:
                        report['blockers'].remove('REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED')
                        report['blockers'].append('CONTRACT_EXTRACTION_ENTRYPOINT_NOT_CONNECTED_CLEF_ONLY')
                    report['scope']['production_integration'] = 'NATIVE_CLEF_BOUND_CONTRACT_EXTRACTOR_UNBOUND'
            except Exception as exc:
                report['checks']['live_nanojev_application'] = {
                    'status': 'FAILED', 'error_type': type(exc).__name__,
                    'error': str(exc)[:500],
                    'contract_extraction_production_bound': False}
                report['blockers'].append('LIVE_NANOJEV_APPLICATION_PROBE_FAILED')
        if args.expert_corpus:
            expert = load_expert_cases(args.expert_corpus, cases)
            expert_hash = sha256(args.expert_corpus.read_bytes()).hexdigest()
            report['scope']['external_human_gold'] = 'EXTERNALLY_ATTESTED_NOT_AUTHENTICATED'
            report['checks']['expert_corpus'] = {'cases': len(expert), 'sha256': expert_hash}
            c = invoke(cmd, [make_request(x) for x in expert], timeout=args.timeout, cwd=repo)
            assessment = score(expert, c)
            report['checks']['expert_candidate'] = assessment
            if assessment['problems']:
                report['blockers'].append('EXPERT_CANDIDATE_FAILURES')
            supported = [x for x in expert if x['policy'] == 'supported']
            accepted = sum(r.get('decision') == 'accepted' for r in c['responses'])
            coverage = sum(r.get('decision') == 'accepted' and r.get('request_id') in {x['id'] for x in supported}
                           for r in c['responses']) / len(supported)
            report['checks']['expert_candidate']['supported_coverage'] = coverage
            if coverage < .5:
                report['blockers'].append('EXPERT_CANDIDATE_LOW_COVERAGE')
            if args.production_command_json:
                prod = invoke(actual_cmd, [make_request(x) for x in expert], timeout=args.timeout, cwd=repo)
                prod_score = score(expert, prod)
                report['checks']['expert_production'] = prod_score
                if prod_score['problems']:
                    report['blockers'].append('EXPERT_PRODUCTION_FAILURES')
                production_covered = sum(r.get('decision') == 'accepted' and r.get('request_id') in {x['id'] for x in supported}
                                         for r in prod['responses']) / len(supported)
                report['checks']['expert_production']['supported_coverage'] = production_covered
                if production_covered < .5:
                    report['blockers'].append('EXPERT_PRODUCTION_LOW_COVERAGE')
        else:
            report['blockers'].append('INDEPENDENT_EXPERT_GOLD_NOT_PROVIDED')
    except Exception as exc:
        report['blockers'].append('SELF_TEST_EXCEPTION:' + type(exc).__name__)
        report['error'] = str(exc)
    report['blockers'] = sorted(set(report['blockers']))
    report['status'] = STATUS_PASS if not report['blockers'] else STATUS_BLOCK
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write('\n')
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='cuda')
    p.add_argument('--output', type=Path, default=REPORT_DEFAULT)
    p.add_argument('--prior-report', type=Path, default=NEGATIVE_DEFAULT)
    p.add_argument('--external-report', type=Path, default=EXTERNAL_DEFAULT)
    p.add_argument('--timeout', type=int, default=1200)
    p.add_argument('--production-command-json', default=None,
                   help='JSON argv array for an ACTUAL application JSONL entrypoint; omit to report unbound')
    p.add_argument('--live-nanojev-url', default=None,
                   help='Probe actual local Main Computer NanoJev CLEF service (e.g. http://127.0.0.1:9765)')
    p.add_argument('--live-nanojev-timeout', type=float, default=120,
                   help='Timeout per native CLEF request; application remains running')
    p.add_argument('--expert-corpus', type=Path, default=None,
                   help='Independent reviewed corpus JSON (24+ cases, balanced, fully annotated)')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    result = run(args)
    print(json.dumps({'status': result['status'], 'report': str(args.output), 'blockers': result['blockers'],
                      'capability': result.get('capability'),
                      'candidate_protocol': result.get('checks', {}).get('candidate_graphs', {}).get('passed_protocol'),
                      'restart': result.get('checks', {}).get('restart_repeatability', {}).get('pass'),
                      'faults': result.get('checks', {}).get('fault_isolation', {}).get('pass'),
                      'live_nanojev_application': result.get('checks', {}).get('live_nanojev_application', {}).get('status')}, sort_keys=True))
    return 0 if result['status'] == STATUS_PASS else 2


if __name__ == '__main__':
    raise SystemExit(main())
