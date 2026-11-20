"""Read-only smoke for deterministic Code Editor semantic argument binding.

Run from the Main Computer repo root:
    python -m tools.mcel_semantic_argument_resolver_smoke

Requires the previously supplied spectral-code-tripwire patch. This validates
its existing Code Editor resolver; it does not claim the planned application-
independent constraint matcher has been implemented.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_knowledge.workspace_plan import plan_workspace

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def refuse(fn, *, reason: str) -> None:
    try:
        fn()
    except ValueError:
        return
    raise AssertionError('Expected refusal: ' + reason)


def _require_smoke_dependencies():
    """Report missing smoke prerequisites before importing the resolver.

    This is a presentation-only audit; no production route hook is required.
    """
    module_path = ROOT / 'main_computer/mcel_knowledge/spectral_code_override.py'
    policy_path = ROOT / 'mcel_apps/code-editor/code-override.json'
    absent = [str(path.relative_to(ROOT)) for path in (module_path, policy_path) if not path.is_file()]
    if absent:
        raise RuntimeError(
            'SMOKE_PREREQUISITE_MISSING: ' + ', '.join(absent)
            + '; install the dependency-complete semantic argument smoke bundle'
        )
    from main_computer.mcel_knowledge.spectral_code_override import resolve_code_arguments
    return resolve_code_arguments


def run() -> dict:
    resolve_code_arguments = _require_smoke_dependencies()
    ir = compile_semantic_app('code-editor', ROOT)
    plan = plan_workspace(ir)
    policy = json.loads((ROOT / 'mcel_apps/code-editor/code-override.json').read_text(encoding='utf-8'))
    immutable = copy.deepcopy((ir, plan, policy))
    baseline = resolve_code_arguments(ir, plan, policy)
    groups = baseline['groups']
    checks = []

    def check(name, fn):
        fn()
        checks.append({'case': name, 'outcome': 'PASS'})

    def baseline_coverage():
        require(baseline['profile'] == 'code' and baseline['grouping'] == 'inferred', 'Wrong override mode')
        require({k: len(v) for k, v in groups.items()} == {'execution': 2, 'preview': 2, 'session': 7}, 'Unexpected group sizes')
        aid = next(r for r in ir['surfaces'][0]['interfaceIntent']['regions'] if r['id'] == baseline['region'])
        expected = sorted(m['ref'] for m in aid['members'])
        actual = [i for g in groups.values() for i in g]
        require(len(actual) == len(set(actual)) and sorted(actual) == expected, 'Duplicate or missing arguments')
        require(len(ir['intents']) == 39 and len(ir['capabilities']) == 13 and len(ir['effects']) == 36, 'Unexpected IR contract')

    check('unique, complete role binding from real compiled IR', baseline_coverage)

    def reordered_input():
        other = copy.deepcopy(ir)
        for key in ('intents', 'capabilities', 'effects', 'states'):
            other[key].reverse()
        other['surfaces'][0]['nodes'].reverse()
        other['surfaces'][0]['interfaceIntent']['regions'].reverse()
        for region in other['surfaces'][0]['interfaceIntent']['regions']:
            region['members'].reverse()
        changed_policy = copy.deepcopy(policy)
        changed_policy['operationRules'].reverse()
        require(resolve_code_arguments(other, plan, changed_policy)['groups'] == groups, 'Declaration order changed role binding')

    check('declaration-order invariance', reordered_input)

    def renamed_ids():
        other = copy.deepcopy(ir)
        region = next(r for r in other['surfaces'][0]['interfaceIntent']['regions'] if r['id'] == baseline['region'])
        ids = [m['ref'] for m in region['members']]
        rename = {old: 'surface-node:smoke.aider.' + str(i) for i, old in enumerate(ids)}
        inverse = {new: old for old, new in rename.items()}
        for member in region['members']:
            member['ref'] = rename[member['ref']]
        for node in other['surfaces'][0]['nodes']:
            if node['id'] in rename:
                node['id'] = rename[node['id']]
        actual = resolve_code_arguments(other, plan, policy)['groups']
        require({k: sorted(inverse[item] for item in vals) for k, vals in actual.items()} == groups,
                'Incidental node-ID rename changed argument assignment')

    check('identity-renaming equivariance', renamed_ids)

    def irrelevant_neighbor():
        other = copy.deepcopy(ir)
        regions = other['surfaces'][0]['interfaceIntent']['regions']
        nav = next(r for r in regions if r['purpose'] == 'navigation')
        candidate = copy.deepcopy(next(n for n in other['surfaces'][0]['nodes'] if n['id'] == nav['members'][0]['ref']))
        candidate['id'] = 'surface-node:smoke.unrelated-navigation'
        nav['members'].append({'ref': candidate['id']})
        other['surfaces'][0]['nodes'].append(candidate)
        require(resolve_code_arguments(other, plan, policy)['groups'] == groups,
                'Unrelated navigation element altered Aider bindings')

    check('irrelevant-neighbor immunity', irrelevant_neighbor)

    def changed_operation_meaning():
        other = copy.deepcopy(ir)
        runner = next(n for n in other['surfaces'][0]['nodes'] if n['id'].endswith('.aider-run-form'))
        runner['intent'] = {'ref': 'intent:read-component-doc'}
        refuse(lambda: resolve_code_arguments(other, plan, policy), reason='changed execution operation')

    check('reject changed operation semantics', changed_operation_meaning)

    def wrong_risk():
        other = copy.deepcopy(ir)
        intent = next(i for i in other['intents'] if i['id'] == 'intent:run-aider')
        intent['risk'] = 'external-read'
        refuse(lambda: resolve_code_arguments(other, plan, policy), reason='reclassified consequential effect')

    check('reject changed operational risk', wrong_risk)

    def conflicting_rules():
        other_policy = copy.deepcopy(policy)
        other_policy['operationRules'].append({'group': 'preview', 'operation': 'run', 'risk': 'consequential', 'nodeKind': 'form'})
        refuse(lambda: resolve_code_arguments(ir, plan, other_policy), reason='same control matches two roles')

    check('reject ambiguous policy assignment', conflicting_rules)

    def missing_editor():
        other = copy.deepcopy(ir)
        dregion = next(r for r in other['surfaces'][0]['interfaceIntent']['regions'] if r['id'] == plan['documentWorkbench']['document'])
        document_node_ids = {m['ref'] for m in dregion['members']}
        for node in other['surfaces'][0]['nodes']:
            if node['id'] in document_node_ids and node['nodeKind'] == 'document-editor':
                node['nodeKind'] = 'control'
        refuse(lambda: resolve_code_arguments(other, plan, policy), reason='missing primary document editor')

    check('reject missing required anchor', missing_editor)

    def missing_required_operation():
        other = copy.deepcopy(ir)
        aid = next(r for r in other['surfaces'][0]['interfaceIntent']['regions'] if r['id'] == baseline['region'])
        aid['members'] = [m for m in aid['members'] if not m['ref'].endswith('.aider-run-form')]
        refuse(lambda: resolve_code_arguments(other, plan, policy), reason='missing execution operation')

    check('reject missing required argument group', missing_required_operation)

    def invalid_precedence():
        other_policy = copy.deepcopy(policy)
        del other_policy['sharedResultPrecedence']
        refuse(lambda: resolve_code_arguments(ir, plan, other_policy), reason='ambiguous result ownership without precedence')

    check('refuse undefined shared-result precedence', invalid_precedence)

    def unrelated_apps_do_not_match():
        for name in ('file-manager', 'task-manager'):
            other = compile_semantic_app(name, ROOT)
            other_plan = plan_workspace(other)
            refuse(lambda: resolve_code_arguments(other, other_plan, policy),
                   reason='code profile mistakenly matched ' + name)

    check('refuse unrelated application profiles', unrelated_apps_do_not_match)

    def require_policy_for_current_implementation():
        refuse(lambda: resolve_code_arguments(ir, plan, {}),
               reason='existing resolver has no generic policy-free matching')

    check('document current dependency on named override policy', require_policy_for_current_implementation)

    def source_unchanged():
        require((ir, plan, policy) == immutable, 'Resolver mutated source inputs')

    check('no original IR/plan/policy mutation', source_unchanged)
    return {
        'schema': 'mcel.semantic-argument-resolver-smoke.v1',
        'status': 'PASS_EXISTING_CODE_RESOLVER',
        'scope': 'current named code override; not a universal graph-matching implementation',
        'remainingGap': 'role constraints still depend on application-owned code-override.json',
        'app': 'code-editor',
        'cases': checks,
        'argumentCount': sum(map(len, groups.values())),
        'groups': {k: len(v) for k, v in groups.items()},
        'authorizesEffects': False,
        'writesFiles': False,
    }


def main() -> int:
    try:
        report = run()
    except Exception as exc:
        print('MCEL_SEMANTIC_ARGUMENT_SMOKE FAIL ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1
    print('MCEL_SEMANTIC_ARGUMENT_SMOKE PASS cases=' + str(len(report['cases']))
          + ' arguments=' + str(report['argumentCount']))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
