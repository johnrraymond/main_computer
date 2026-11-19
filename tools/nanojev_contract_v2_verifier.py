#!/usr/bin/env python3
"""Generic, bounded Contract Language v2 graph-completeness verifier.

Checks *the validated IR*, not arbitrary English or generated Solidity.
Exhaustive over the current v2 ceiling: <=8 states, <=6 acyclic actions,
three constructor-bound roles and the two symbolic funded-time phases.

A PASS is a conditional, existential-recovery result, not a formal proof of
Solidity correctness, truthful off-chain evidence, or eventual execution.
No model, keys, network, Docker or deployment required.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
from typing import Any

from tools import nanojev_contract_pull_payments as v2
from tools.nanojev_contract_solidity_smoke import SmokeFailure

PHASES = ('before_deadline', 'at_or_after_deadline')
ROLES = ('payer', 'worker', 'reviewer')
Node = tuple[str, str]


def _enabled(guard: str, phase: str) -> bool:
    return guard == 'always' or guard == phase


def _payout(amount: int, items: list[dict]) -> dict[str, int]:
    """Exact v2 Solidity allocation: floor all but final, last receives remainder."""
    if not items:
        return {}
    credits: dict[str, int] = {}
    remainder = amount
    for idx, item in enumerate(items):
        wei = amount * item['bps'] // 10000 if idx != len(items) - 1 else remainder
        credits[item['to']] = wei
        remainder -= wei
    return credits


def _effect(rule: dict, amount: int) -> tuple:
    return (rule['to'], tuple(sorted(_payout(amount, rule['payments']).items())))


def _successors(spec: dict, node: Node, blocked_role: str | None = None):
    state, phase = node
    if phase == PHASES[0]:
        yield (state, PHASES[1]), 'wait_until_deadline', None
    for rule in spec['rules']:
        if rule['from'] == state and rule['actor'] != blocked_role and _enabled(rule['when'], phase):
            yield (rule['to'], phase), rule['name'], rule


def _reachable(spec: dict) -> dict[Node, list[str]]:
    start = ('funded', PHASES[0])
    paths = {start: []}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for dest, label, _ in _successors(spec, node):
            if dest not in paths:
                paths[dest] = paths[node] + [label]
                queue.append(dest)
    return paths


def _recovery(spec: dict, start: Node, blocked_role: str | None) -> list[str] | None:
    """Find a shortest terminal credited-settlement witness, if any."""
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        node, trace = queue.popleft()
        for dest, label, rule in _successors(spec, node, blocked_role):
            path = trace + [label]
            if rule is not None and rule['payments']:
                return path
            if dest not in seen:
                seen.add(dest)
                queue.append((dest, path))
    return None


def verify(raw: Any) -> dict:
    """Fail closed on invalid v2; analyze arbitrary *validated* v2 programs.

    Choice ambiguity is reported independently from liveness.  In v2, an
    atomic state update prevents double settlement, but competing transitions
    may still have different economic outcomes depending on transaction order.
    Unresolved divergent same-state/same-phase choices require intent review.
    """
    spec = v2.validate(raw)
    paths = _reachable(spec)
    all_states = sorted({'funded'} | {s for r in spec['rules'] for s in (r['from'], r['to'])})
    terminal = sorted({r['to'] for r in spec['rules'] if r['payments']})
    amount = spec['amount_wei']
    credits = []
    for rule in spec['rules']:
        if not rule['payments']:
            continue
        allocation = _payout(amount, rule['payments'])
        credits.append({'action': rule['name'], 'source': rule['from'],
                        'terminal_state': rule['to'], 'credits_wei': allocation,
                        'credited_total_wei': sum(allocation.values()),
                        'conserves_escrow': sum(allocation.values()) == amount,
                        'positive_credits': all(v > 0 for v in allocation.values())})
    account_ok = bool(credits) and all(
        x['conserves_escrow'] and x['positive_credits'] for x in credits
    )
    guards = []
    for rule in spec['rules']:
        guards.append({'action': rule['name'], 'actor': rule['actor'],
                       'unauthorized_roles_rejected_by_ir': [role for role in ROLES if role != rule['actor']],
                       'from': rule['from'], 'to': rule['to'], 'when': rule['when']})
    # Distinct constructor-role addresses are required by the v2 compiler;
    # this IR analysis does not prove Solidity correctly enforces the checks.
    authorization_ok = all(rule['actor'] in ROLES for rule in spec['rules'])
    unsettled = []
    recovered = []
    single_traps = []
    unconditional_traps = []
    races = []
    equivalent_routes = []
    for node, arrival in sorted(paths.items()):
        state, phase = node
        # Terminal states have credits and no more IR moves. They are excluded
        # from future-liveness obligations, while retained in the full graph.
        if state in terminal:
            continue
        context = {'state': state, 'phase': phase, 'witness_actions': arrival}
        settled = _recovery(spec, node, None)
        if settled is None:
            unconditional_traps.append({**context, 'issue': 'NO_SETTLEMENT_WITH_ALL_ROLES'})
        else:
            recovered.append({**context, 'unavailable_role': None, 'recovery_actions': settled})
        for blocked in ROLES:
            witness = _recovery(spec, node, blocked)
            if witness is None:
                single_traps.append({**context, 'unavailable_role': blocked,
                                     'issue': 'SETTLEMENT_DEPENDS_ON_UNAVAILABLE_CALLER'})
            else:
                recovered.append({**context, 'unavailable_role': blocked, 'recovery_actions': witness})
        enabled = [r for r in spec['rules'] if r['from'] == state and _enabled(r['when'], phase)]
        # Multiple authorized actions are *not* automatically a double spend:
        # Solidity serializes by state.  But if they lead to different states
        # or different credit allocations, the user must approve the race.
        for i, a in enumerate(enabled):
            for b in enabled[i + 1:]:
                pair = {'state': state, 'phase': phase,
                        'actions': sorted([a['name'], b['name']]),
                        'actors': sorted([a['actor'], b['actor']]),
                        'witness_actions': arrival}
                if _effect(a, amount) == _effect(b, amount):
                    equivalent_routes.append({**pair, 'classification': 'EQUIVALENT_EFFECT_FIRST_CALL_WINS'})
                else:
                    races.append({**pair, 'classification': 'DIVERGENT_EFFECT_FIRST_CALL_WINS',
                                  'outcomes': {a['name']: {'to': a['to'], 'credits_wei': _payout(amount, a['payments'])},
                                               b['name']: {'to': b['to'], 'credits_wei': _payout(amount, b['payments'])}}})
        unsettled.append({'state': state, 'phase': phase, 'witness_actions': arrival,
                          'enabled_actions': sorted(r['name'] for r in enabled)})
    status = ('PASS_CONDITIONAL' if account_ok and authorization_ok and not unconditional_traps
              and not single_traps and not races else 'BLOCKED_BY_V2_GRAPH_AUDIT')
    blockers = []
    if not account_ok:
        blockers.append('ESCROW_CONSERVATION_OR_ZERO_CREDIT')
    if not authorization_ok:
        blockers.append('UNAUTHORIZED_ACTION')
    if unconditional_traps:
        blockers.append('NO_SETTLEMENT_PATH')
    if single_traps:
        blockers.append('SINGLE_UNAVAILABLE_ROLE_TRAP')
    if races:
        blockers.append('DIVERGENT_SAME_PHASE_TRANSITION_RACE_REQUIRES_INTENT_REVIEW')
    return {
        'schema': 'nanojev.contract.v2.graph_verifier.v1',
        'source': 'VALIDATED_CONTRACT_LANGUAGE_V2_NOT_ENGLISH',
        'status': status, 'emission_authorized_by_graph_audit': status == 'PASS_CONDITIONAL',
        'bounded_model': {'states': len(all_states), 'rules': len(spec['rules']),
                          'deadline_phases': list(PHASES), 'role_names': list(ROLES),
                          'start': ['funded', 'before_deadline'],
                          'terminal_states': terminal, 'funding_is_built_in': True},
        'graph': {'reachable_nodes': [
            {'state': state, 'phase': phase, 'witness_actions': path}
            for (state, phase), path in sorted(paths.items())],
            'unsettled_nodes': unsettled, 'reachable_count': len(paths),
            'unsettled_count': len(unsettled)},
        'authorization': {'status': 'PASS_IR' if authorization_ok else 'FAIL_IR',
                          'actions': guards,
                          'constructor_roles_distinct': 'REQUIRED_BY_V2_CODEGEN_NOT_ONCHAIN_VERIFIED'},
        'conservation': {'status': 'PASS_IR' if account_ok else 'FAIL_IR',
                         'deposit_wei': amount, 'terminal_settlements': credits,
                         'progressive_partial_credits_supported': False,
                         'withdrawal_credit_balance': 'TESTED_SEPARATELY_NOT_PROVED_BY_GRAPH'},
        'conflicts': {'status': 'REQUIRES_INTENT_REVIEW' if races else 'NO_DIVERGENT_RACE',
                      'divergent': races, 'equivalent_effects': equivalent_routes,
                      'note': 'First valid transaction wins; equivalent effects can still have different method names.'},
        'liveness': {'status': 'PASS_CONDITIONAL' if not single_traps and not unconditional_traps
                     else 'FAIL_TRAPPED_FUNDS_RISK',
                     'policy': 'EXISTS_SETTLEMENT_PATH_AFTER_ANY_SINGLE_ROLE_STOPS_CALLING',
                     'unconditional_traps': unconditional_traps, 'single_role_traps': single_traps,
                     'recovery_witnesses': recovered,
                     'checked_reachable_unsettled_nodes': len(unsettled),
                     'checked_single_role_scenarios': len(unsettled) * len(ROLES)},
        'unverified': ['SOURCE_REQUIREMENT_COMPLETENESS', 'SOLIDITY_IMPLEMENTATION_EQUIVALENCE',
                       'AUTONOMOUS_EXECUTION', 'TRANSACTION_FAIRNESS',
                       'UNBOUNDED_TIME_OR_MULTI_ACTOR_FAILURE', 'WITHDRAWAL_SUCCESS'],
        'assumptions': ['The accepted Contract Language v2 IR is the intended agreement',
                        'At most one role stops calling after each analyzed reachable state',
                        'Other authorized roles eventually exercise an available settlement path',
                        'Time monotonically advances past the single deadline',
                        'Transactions are included and their caller identity is authentic',
                        'Settlement credits are ledger operations; withdrawal can fail independently'],
        'blockers': blockers,
    }


def verify_prior_report(payload: dict) -> dict:
    """Re-evaluate all v2 candidates in a recorded live pull smoke, without LLM.

    Replay is not independent source gold or an attestation that input came from
    an actual model run.  Each candidate is revalidated from its own JSON.
    """
    if (not isinstance(payload, dict) or
            payload.get('schema') != 'nanojev.contract-pull-smoke.v1' or
            not isinstance(payload.get('cases'), list) or not payload['cases']):
        raise SmokeFailure('preflight', 'Expected nonempty pull-smoke report with cases')
    cases = []
    seen = set()
    mismatches = []
    for original in payload['cases']:
        if not isinstance(original, dict) or not isinstance(original.get('id'), str):
            raise SmokeFailure('preflight', 'Invalid case in pull report')
        ident = original['id']
        if not ident or ident in seen:
            raise SmokeFailure('preflight', 'Missing/duplicate case ID')
        seen.add(ident)
        raw = original.get('llm_output')
        if not isinstance(raw, str):
            raise SmokeFailure('preflight', 'Case has no recorded live LLM response: ' + ident)
        try:
            answer = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SmokeFailure('preflight', 'Unparseable LLM response: ' + ident) from exc
        if not isinstance(answer, dict):
            raise SmokeFailure('preflight', 'LLM response is not an object: ' + ident)
        action = original.get('policy_action')
        if answer.get('status') == 'needs_clarification':
            if action != 'REFUSE_UNDERSPECIFIED':
                mismatches.append({'case': ident, 'error': 'Recorded refusal has incompatible policy action'})
            cases.append({'id': ident, 'action': action, 'graph_audit': 'NOT_APPLICABLE_REFUSED'})
            continue
        if answer.get('status') != 'contract':
            raise SmokeFailure('preflight', 'Unknown LLM response status: ' + ident)
        audited = verify(answer.get('contract'))
        expected_gate = {'BLOCK_UNSAFE_CONTRACT': False, 'EMIT_CREDITED_SETTLEMENT': True}
        if action not in expected_gate:
            mismatches.append({'case': ident, 'error': 'Unknown/missing recorded policy action'})
        elif audited['emission_authorized_by_graph_audit'] != expected_gate[action]:
            mismatches.append({'case': ident, 'error': 'Generic graph audit disagrees with recorded policy',
                               'blockers': audited['blockers']})
        cases.append({'id': ident, 'action': action, 'graph_audit': audited})
    return {'schema': 'nanojev.contract.v2.graph_verifier_replay.v1',
            'status': ('PASS_REPLAY_IR_GRAPH_AUDIT_NOT_SEMANTIC_CERTIFIED'
                       if not mismatches else 'BLOCKED_REPLAY_GATE_MISMATCH'),
            'source': 'RECORDED_LLM_OUTPUT_REVALIDATED_NO_LLM_NO_FORGE',
            'source_model': payload.get('model'),
            'gold_independence': 'NOT_ESTABLISHED',
            'case_count': len(cases), 'audited_contracts': sum('graph_audit' in x and isinstance(x['graph_audit'], dict)
                                                            for x in cases),
            'refusals': sum(x['graph_audit'] == 'NOT_APPLICABLE_REFUSED' for x in cases
                            if isinstance(x['graph_audit'], str)),
            'cases': cases, 'blockers': mismatches}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--spec', type=Path,
                       help='JSON object containing one full v2 Contract Language program')
    group.add_argument('--from-report', type=Path,
                       help='Replay all recorded v2 LLM candidates in a prior pull smoke (no model/Forge)')
    p.add_argument('--output', type=Path, required=True)
    opts = p.parse_args(argv)
    try:
        source = opts.spec if opts.spec is not None else opts.from_report
        if source.resolve() == opts.output.resolve():
            raise SmokeFailure('preflight', 'Refusing to overwrite the input file')
        if opts.output.exists():
            raise SmokeFailure('preflight', 'Output already exists; choose a new path')
        obj = json.loads(source.read_text(encoding='utf-8'))
        result = verify(obj) if opts.spec is not None else verify_prior_report(obj)
    except (SmokeFailure, ValueError, TypeError, OSError) as exc:
        result = {'schema': 'nanojev.contract.v2.graph_verifier.v1',
                  'status': 'BLOCKED_INVALID_INPUT',
                  'emission_authorized_by_graph_audit': False,
                  'blockers': [getattr(exc, 'reason', str(exc))[:500]]}
    if not opts.output.exists():
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if isinstance(result.get('cases'), list):
        audits = [c['graph_audit'] for c in result['cases']
                  if isinstance(c.get('graph_audit'), dict)]
        overview = {'audited_contracts': len(audits),
                    'refusals': result['refusals'],
                    'blocked_contracts': sum(not a['emission_authorized_by_graph_audit'] for a in audits),
                    'single_role_traps': sum(len(a['liveness']['single_role_traps']) for a in audits),
                    'divergent_conflicts': sum(len(a['conflicts']['divergent']) for a in audits)}
    else:
        overview = {
            'unsettled_nodes': result.get('graph', {}).get('unsettled_count'),
            'single_role_traps': len(result.get('liveness', {}).get('single_role_traps', [])),
            'divergent_conflicts': len(result.get('conflicts', {}).get('divergent', []))}
    print(json.dumps({'status': result['status'], 'blockers': result['blockers'],
                      **overview, 'report': str(opts.output)}, separators=(',', ':')))
    return 0 if result['status'] in ('PASS_CONDITIONAL',
                                   'PASS_REPLAY_IR_GRAPH_AUDIT_NOT_SEMANTIC_CERTIFIED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
