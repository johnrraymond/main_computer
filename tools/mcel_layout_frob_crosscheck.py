"""MCEL layout frob obligations: verify contract integrity and publish an honest plan.

This is NOT the layout engine, browser runner, or operational-effect harness.
It expands the machine-readable crosscheck for future runner adapters and
executes only the three explicitly implemented static checks. Everything else
remains NOT_IMPLEMENTED, and audit mode refuses certification accordingly.

Usage:
  python tools/mcel_layout_frob_crosscheck.py --mode plan
  python tools/mcel_layout_frob_crosscheck.py --mode audit
  python tools/mcel_layout_frob_crosscheck.py --mode audit --prior-probe runtime/reports/task-manager-semantic-layout/report.json
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required to read the MCEL frob design contract.') from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = PROJECT_ROOT / 'mcel_designs' / 'task-manager' / 'frob_crosscheck.yaml'
DEFAULT_DESIGN = PROJECT_ROOT / 'mcel_designs' / 'task-manager' / 'design.yaml'
DEFAULT_OUTPUT = PROJECT_ROOT / 'runtime' / 'reports' / 'mcel-layout-frob' / 'crosscheck.json'
SCHEMA = 'main-computer.mcel.layout-frob-crosscheck.v1'
STATIC_PROBES = {'static.contract_fields', 'static.refs_resolve', 'static.design_coverage', 'static.hierarchy_integrity'}
EXTERNAL_PROBES = {'semantic.compiler','catalog.presentation', 'planner.capabilities', 'planner.candidates', 'planner.hints', 'planner.objectives', 'browser.geometry', 'browser.performance', 'runtime.observations', 'runtime.operations', 'runtime.effect_boundary'}
VERDICTS = {'PASS', 'FAIL', 'UNVERIFIED', 'NOT_IMPLEMENTED', 'BLOCKED', 'NOT_APPLICABLE'}
AXES = {'machine', 'engine', 'viewport', 'font_scale', 'access_mode'}


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'{path}: must be a YAML mapping')
    return data


def get_path(data: Any, dotted: str) -> Any:
    here = data
    for part in dotted.split('.'):
        if isinstance(here, dict):
            if part not in here:
                raise KeyError(f'{dotted!r} missing member {part!r}')
            here = here[part]
        elif isinstance(here, list) and part.isdecimal():
            here = here[int(part)]
        else:
            raise KeyError(f'{dotted!r}: cannot traverse {part!r}')
    return here


def resolve_target(design: dict, contract: dict, dotted: str) -> Any:
    try:
        return get_path(design, dotted)
    except (KeyError, IndexError):
        return get_path(contract, dotted)


def member_id(value: Any, index: Any) -> str:
    if isinstance(index, str):
        return index
    if isinstance(value, dict) and isinstance(value.get('id'), str):
        return value['id']
    if isinstance(value, (list, tuple)):
        return '--'.join(str(x) for x in value)
    return str(index)


def expand(test: dict, subject: Any) -> list[dict]:
    kind = test.get('foreach')
    if kind == 'mapping_keys':
        if not isinstance(subject, dict):
            raise ValueError(f'{test["id"]}: mapping_keys needs a map')
        seq = [(k, value) for k, value in subject.items()]
    elif kind == 'sequence_members':
        if not isinstance(subject, list):
            raise ValueError(f'{test["id"]}: sequence_members needs a list')
        seq = list(enumerate(subject))
    elif kind == 'hierarchy_nodes':
        if not isinstance(subject, dict):
            raise ValueError(f'{test["id"]}: hierarchy_nodes needs a mapping')
        nodes = []
        def visit(value: Any, path: str = 'hierarchy') -> None:
            if isinstance(value, dict):
                if isinstance(value.get('id'), str):
                    nodes.append((value['id'], value))
                for k,v in value.items():
                    visit(v,path+'.'+str(k))
            elif isinstance(value,list):
                for n,v in enumerate(value):
                    visit(v,path+'.'+str(n))
        visit(subject)
        seq = nodes
    elif kind is None:
        seq = [(None, subject)]
    else:
        raise ValueError(f'{test["id"]}: unknown foreach {kind}')
    if not seq:
        raise ValueError(f'{test["id"]}: zero test instances')
    expanded = []
    for index, value in seq:
        suffix = member_id(value, index) if index is not None else None
        instance_id = test['id'] + ('::' + suffix if suffix else '')
        expanded.append({'id': instance_id, 'test_id': test['id'], 'member': suffix,
                         'value': value, 'target': test['target']})
    return expanded


def validate(contract: dict, design: dict) -> list[dict]:
    if contract.get('schema') != SCHEMA:
        raise ValueError('unsupported frob crosscheck schema')
    if design.get('schema') != 'main-computer.mcel.application-design.v1':
        raise ValueError('unsupported Task Manager design schema')
    if contract.get('design_source') != 'mcel_designs/task-manager/design.yaml':
        raise ValueError('contract design_source does not identify the Task Manager YAML')
    probes = contract.get('probes')
    tests = contract.get('tests')
    if not isinstance(probes, dict) or not isinstance(tests, list) or not tests:
        raise ValueError('probes and nonempty tests are mandatory')
    for probe_id, info in probes.items():
        if not isinstance(info, dict) or not isinstance(info.get('implemented'), bool):
            raise ValueError(f'invalid probe registration {probe_id}')
        if info['implemented'] and probe_id not in STATIC_PROBES | EXTERNAL_PROBES:
            raise ValueError(f'unrecognized implemented runner {probe_id}: fail closed')
        if info['implemented'] and not info.get('runner'):
            raise ValueError(f'{probe_id}: implemented probe must name a runner')
        if not info.get('evidence'):
            raise ValueError(f'{probe_id}: evidence contract is missing')
    machine_ids = {m['id'] for m in contract['layout_machines']}
    engine_ids = {e['id'] for e in contract['planning_engines']}
    if len(machine_ids) != len(contract['layout_machines']) or len(engine_ids) != len(contract['planning_engines']):
        raise ValueError('duplicate machine or engine id')
    pairs = set()
    allowed_pair_status = {'available','needs_adapter','subtree_only','not_implemented','unassessed','unsupported'}
    for pair in contract['compatibility']:
        key = (pair['machine'], pair['engine'])
        if pair['machine'] not in machine_ids or pair['engine'] not in engine_ids or key in pairs:
            raise ValueError(f'invalid/duplicate machine-engine pairing {key}')
        pairs.add(key)
        if pair.get('state') not in allowed_pair_status:
            raise ValueError(f'unknown compatibility state {key}')
    missing_pairs = {(m,e) for m in machine_ids for e in engine_ids} - pairs
    if missing_pairs:
        raise ValueError(f'unaccounted machine-engine combinations: {sorted(missing_pairs)}')
    fixture_ids = set(contract['fixture_catalog'])
    if set(contract['sweep_policy']['fixtures']) - fixture_ids:
        raise ValueError('unknown sweep fixture')
    seen_ids = set()
    all_instances = []
    foreach_scopes = set()
    for test in tests:
        test_id = test.get('id')
        if not isinstance(test_id, str) or test_id in seen_ids:
            raise ValueError(f'blank or duplicate test id {test_id}')
        seen_ids.add(test_id)
        if test.get('probe') not in probes:
            raise ValueError(f'{test_id}: missing registered probe {test.get("probe")}')
        if not test.get('assertion') or not isinstance(test['assertion'], dict):
            raise ValueError(f'{test_id}: assertion object required')
        if not isinstance(test.get('description'), str) or not test['description'].strip():
            raise ValueError(f'{test_id}: test has no purpose/description')
        if not isinstance(test.get('required'), bool):
            raise ValueError(f'{test_id}: required must be boolean')
        if set(test.get('fixtures', [])) - fixture_ids:
            raise ValueError(f'{test_id}: undeclared data fixtures')
        if set(test.get('axes', [])) - AXES:
            raise ValueError(f'{test_id}: undeclared sweep axes')
        try:
            subject = resolve_target(design, contract, test['target'])
        except (KeyError, IndexError) as exc:
            raise ValueError(f'{test_id}: reference not found: {test["target"]}: {exc}') from exc
        if test.get('foreach'):
            foreach_scopes.add(test['target'])
        all_instances.extend({'test': test, **inst} for inst in expand(test, subject))
    full = contract['coverage_rules']['full']
    missing = sorted(set(full) - foreach_scopes)
    if missing:
        raise ValueError(f'coverage hole: collections have no per-member crosschecks: {missing}')
    expanded_ids = [i['id'] for i in all_instances]
    all_test_ids = {test['id'] for test in tests}
    for negative in contract.get('negative_controls', []):
        if not negative.get('mutation') or not negative.get('must') or not negative.get('linked_tests'):
            raise ValueError('negative control requires mutation, expected refusal, and linked test IDs')
        unknown = set(negative['linked_tests']) - all_test_ids
        if unknown:
            raise ValueError(f'negative control {negative.get("id")}: unknown tests {unknown}')
    if len(expanded_ids) != len(set(expanded_ids)):
        raise ValueError('duplicate expanded instance IDs: cannot track independent evidence')
    for strength in ('none', 'suggest', 'prefer', 'constrain', 'require'):
        if strength not in contract['hint_language']['authorities']:
            raise ValueError(f'hint ladder missing {strength}')
    return all_instances


def static_result(item: dict, design: dict, contract: dict) -> dict:
    test = item['test']
    assertion = test['assertion']
    value = item['value']
    probe = test['probe']
    evidence = {'design_pointer': test['target'], 'member': item['member']}
    if probe == 'static.contract_fields':
        missing = [field for field in assertion['required_fields']
                   if not isinstance(value, dict) or field not in value]
        evidence['missing_fields'] = missing
        return {'status': 'FAIL' if missing else 'PASS', 'evidence': evidence,
                'reason': f'missing required fields: {missing}' if missing else 'Declared structural fields present; runtime behavior NOT proven.'}
    if probe == 'static.refs_resolve':
        namespaces = assertion['namespace']
        known = set().union(*(set(get_path(design, name)) for name in namespaces))
        if assertion['field'] == 'relation_endpoints':
            targets = [value[0], value[2]] if isinstance(value, list) and len(value) == 3 else ['invalid relationship triple']
        else:
            targets = [value.get(assertion['field']) if isinstance(value, dict) else None]
        missing = [v for v in targets if v not in known]
        evidence['referenced_entity'] = targets
        evidence['missing_refs'] = missing
        return {'status': 'FAIL' if missing else 'PASS', 'evidence': evidence,
                'reason': f'unknown reference(s): {missing}' if missing else 'Referenced type exists; runtime binding NOT proven.'}
    if probe == 'static.hierarchy_integrity':
        missing = [field for field in assertion['required_fields'] if not isinstance(value, dict) or field not in value]
        evidence['hierarchy_id'] = value.get('id') if isinstance(value, dict) else None
        return {'status':'FAIL' if missing else 'PASS', 'evidence':evidence,
                'reason':f'missing hierarchy fields: {missing}' if missing else 'Declared nested semantic node exists; DOM representation NOT proven.'}
    if probe == 'static.design_coverage':
        covered = set(t['target'] for t in contract['tests'] if t.get('foreach'))
        missing = sorted(set(contract['coverage_rules']['full']) - covered)
        evidence['expanded_instances'] = len(contract['tests'])
        evidence['coverage_paths'] = sorted(covered)
        return {'status': 'FAIL' if missing else 'PASS', 'evidence': evidence,
                'reason': f'coverage paths missing {missing}' if missing else 'All declared mandatory collections have member-level obligations; their behavior remains unverified.'}
    raise ValueError('internal static probe dispatch error')


def audit(design: dict, contract: dict, *, mode: str,
          prior_probe: dict | None = None, frob_report: dict | None = None,
          frob_path: Path | None = None) -> dict:
    instances = validate(contract, design)
    fingerprint = hashlib.sha256(json.dumps(design, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if frob_report is not None:
        if frob_report.get('schema') != 'main-computer.mcel.layout-frob-execution.v1':
            raise ValueError('Unrecognized Frob execution evidence')
        expected_sha = hashlib.sha256(json.dumps(design,sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()
        if frob_report.get('provenance',{}).get('design_sha256') != expected_sha:
            raise ValueError('Frob execution was not from the current design; refuse stale evidence')
        if frob_report.get('provenance',{}).get('contract_sha256') != hashlib.sha256(json.dumps(contract,sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest():
            raise ValueError('Frob execution was not from the current crosscheck contract')
        if not frob_path or not frob_path.is_file():
            raise ValueError('Frob browser evidence requires an existing report path')
        for scenario in frob_report.get('scenarios', []):
            obs = scenario.get('observed')
            if obs is None:
                continue
            filename=obs.get('screenshot')
            recorded_hash=obs.get('screenshot_sha256')
            if not filename or not isinstance(filename,str) or Path(filename).name != filename:
                raise ValueError('Missing or unsafe Frob screenshot reference')
            file=frob_path.parent/filename
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=recorded_hash:
                raise ValueError('Frob screenshot missing or fingerprint changed: '+filename)
            if scenario.get('status') == 'PASS' and obs.get('witness',{}).get('status')!='pass':
                raise ValueError('Frob PASS lacks successful MCEL browser witness')
    results = []
    for item in instances:
        test = item['test']
        probe_id = test['probe']
        probe = contract['probes'][probe_id]
        if mode == 'audit' and probe['implemented'] and probe_id in STATIC_PROBES:
            outcome = static_result(item, design, contract)
        elif mode == 'audit' and probe_id in EXTERNAL_PROBES and probe['implemented']:
            witness = (frob_report or {}).get('evidence_index',{}).get(item['test_id'])
            if witness is not None and witness.get('status') in ('PASS','FAIL'):
                outcome={'status':witness['status'],'reason':witness['reason'],
                         'evidence':{'frob_scenarios':witness.get('scenario_ids',[]),
                                     'design_sha256':frob_report['provenance']['design_sha256'],
                                     'report_scope':'bounded browser/fixture experiment'}}
            else:
                outcome={'status':'UNVERIFIED','reason':'Adapter exists; this obligation lacks adequate independent evidence', 'evidence':None}
        else:
            outcome = {'status': 'NOT_IMPLEMENTED' if not probe['implemented'] else 'UNVERIFIED',
                       'reason': 'Runner not implemented.' if not probe['implemented'] else 'Static check awaits execution.',
                       'evidence': None}
        results.append({'id': item['id'], 'test_id': item['test_id'], 'group': test['group'],
                        'probe': probe_id, 'target': test['target'], 'member': item['member'],
                        'required': test['required'], 'assertion': test['assertion'],
                        'fixtures': test.get('fixtures', []), 'axes': test.get('axes', []),
                        'critical': test.get('critical', False), **outcome})
    counts = Counter(result['status'] for result in results)
    group_summary = {}
    for result in results:
        group_summary.setdefault(result['group'], Counter())[result['status']] += 1
    all_pass = bool(results) and all(r['status'] == 'PASS' or (not r['required'] and r['status'] == 'NOT_APPLICABLE') for r in results)
    pending = Counter(r['probe'] for r in results if r['status'] in ('NOT_IMPLEMENTED','UNVERIFIED','BLOCKED'))
    res = {
        'schema': 'main-computer.mcel.layout-frob-crosscheck-report.v1',
        'scope': 'design-contract only; static evidence never certifies layout or functionality',
        'design_fingerprint_sha256': fingerprint,
        'contract_test_count': len(contract['tests']),
        'expanded_obligation_count': len(results),
        'status': 'PASS' if all_pass else 'FAIL' if counts['FAIL'] else 'INCOMPLETE',
        'counts': dict(sorted(counts.items())),
        'groups': {k:dict(sorted(v.items())) for k,v in sorted(group_summary.items())},
        'incomplete_is_not_success': True,
        'machine_engine_compatibility': contract['compatibility'],
        'pending_runner_counts': dict(sorted(pending.items(), key=lambda x:(-x[1],x[0]))),
        'sweep_policy': contract['sweep_policy'],
        'negative_controls': contract['negative_controls'],
        'run_provenance_contract': contract['run_provenance'],
        'results': results,
    }
    if prior_probe is not None:
        res['historical_probe_diagnostics'] = compare_prior_probe(prior_probe)
    return res


def parse_prior_console(text: str) -> dict:
    """Parse the old probe's printed summary without executing arbitrary code."""
    for line in text.splitlines():
        if line.startswith('canonical_browser:'):
            value = ast.literal_eval(line.split(':',1)[1].strip())
            if not isinstance(value, dict):
                raise ValueError('canonical_browser stdout summary must be a map')
            return value
    raise ValueError('prior stdout is missing canonical_browser summary')


def compare_console_report(console: dict, report: dict) -> dict:
    browser = report.get('canonicalBrowser') or {}
    mismatches = {}
    for width, summary in console.items():
        if not isinstance(summary, dict):
            raise ValueError('non-dictionary browser stdout viewport')
        rec = browser.get(width) or {}
        for field in ('columns','priorityUpperLeft','viewportOverflow'):
            if field in summary and summary[field] != rec.get(field):
                mismatches[f'{width}.{field}'] = {
                    'console': summary[field], 'report': rec.get(field)}
    return {'status': 'FAIL' if mismatches else 'PASS', 'mismatches': mismatches,
            'scope': 'Only comparable when stdout and report originate from the same execution.'}


def compare_prior_probe(probe: dict) -> dict:
    """Inspect prior layout-only evidence; do not relabel it as a fresh Frob run."""
    browser = probe.get('canonicalBrowser') or {}
    mobile = browser.get('390') or {}
    automatic = probe.get('inferredWorkspace') or {}
    inferred = {v: (data.get('attentionFirst') if isinstance(data, dict) else None)
                for v,data in automatic.items()}
    columns = mobile.get('columns')
    return {
      'scope': 'Historical layout-only probe; not an MCEL Frob certification.',
      'prior_report_schema': probe.get('schema'),
      'mobile_390_one_column': {'status': 'PASS' if columns == 1 else 'FAIL' if isinstance(columns, int) else 'UNVERIFIED',
                                'expected_columns': 1, 'observed_columns': columns,
                                'note': 'The earlier layout-only PASS did not include a one-column mobile requirement.'},
      'automatic_priority_first': {'status': 'PASS' if inferred and all(v is True for v in inferred.values())
                                    else 'FAIL' if any(v is False for v in inferred.values()) else 'UNVERIFIED',
                                    'observed': inferred,
                                    'note': 'A baseline inference diagnostic, not a mandatory no-hint placement rule.'},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument('--design', type=Path, default=DEFAULT_DESIGN)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--prior-probe', type=Path)
    parser.add_argument('--frob-report', type=Path,help='Fresh Frob Lab report; accept evidence only for fully matched obligations')
    parser.add_argument('--prior-console', type=Path, help='Captured stdout from the SAME prior probe execution')
    parser.add_argument('--mode', choices=['plan','audit'], default='audit')
    args = parser.parse_args(argv)
    try:
        contract = load_yaml(args.contract)
        design = load_yaml(args.design)
        prior = json.loads(args.prior_probe.read_text(encoding='utf-8')) if args.prior_probe else None
        executed = json.loads(args.frob_report.read_text(encoding='utf-8')) if args.frob_report else None
        report = audit(design, contract, mode=args.mode, prior_probe=prior, frob_report=executed, frob_path=args.frob_report)
        if args.prior_console:
            if prior is None:
                raise ValueError('--prior-console requires --prior-probe from the SAME run')
            console = parse_prior_console(args.prior_console.read_text(encoding='utf-8'))
            report['console_report_comparison'] = compare_console_report(console,prior)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f'MCEL_LAYOUT_FROB_CROSSCHECK_REFUSED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print('MCEL_LAYOUT_FROB_CROSSCHECK_' + report['status'])
    print(f"tests={report['contract_test_count']} expanded={report['expanded_obligation_count']} counts={report['counts']}")
    if prior is not None:
        diag = report['historical_probe_diagnostics']
        print('prior_mobile_390:', diag['mobile_390_one_column'])
        print('prior_auto_priority:', diag['automatic_priority_first'])
    if args.prior_console:
        print('console_vs_report:', report['console_report_comparison'])
    print('report:', args.output)
    # Nonzero for unproven or failed acceptance in audit mode; 'plan' validates only.
    return 0 if (args.mode == 'plan' or report['status'] == 'PASS') and (not args.prior_console or report['console_report_comparison']['status'] == 'PASS') else 1


if __name__ == '__main__':
    sys.exit(main())
