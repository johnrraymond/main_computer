#!/usr/bin/env python3
"""Bounded state/accounting graph verification for ANY validated v3 escrow IR.

Checks state, one deadline phase, unallocated ETH, per-role pending credits,
and cumulative withdrawals. Conditional recovery uses one unavailable role.
Does not certify English translation, deployed code, transaction inclusion or
external recipient acceptance. No external effects or Solidity execution.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterator

from tools import nanojev_contract_v3_language as lang

MAX_NODES = 12000


@dataclass(frozen=True)
class Node:
    state: str
    phase: str
    unallocated: int
    pending: tuple[int, int, int]
    withdrawn: tuple[int, int, int]

    def summary(self) -> dict:
        return {'state': self.state, 'phase': self.phase, 'unallocated_wei': self.unallocated,
                'pending_wei': dict(zip(lang.ROLES, self.pending)),
                'withdrawn_wei': dict(zip(lang.ROLES, self.withdrawn))}


def initial(spec: dict) -> Node:
    return Node('funded', lang.PHASES[0], spec['amount_wei'], (0, 0, 0), (0, 0, 0))


def _enabled(rule: dict, node: Node) -> bool:
    return rule['from'] == node.state and (rule['when'] == 'always' or rule['when'] == node.phase)


def transition(node: Node, rule: dict) -> Node:
    unallocated, pending = lang.apply_credits(node.unallocated, node.pending, rule['credits'])
    return Node(rule['to'], node.phase, unallocated, pending, node.withdrawn)


def successors(spec: dict, node: Node, unavailable: str | None = None,
               include_withdrawals: bool = True) -> Iterator[tuple[str, Node]]:
    if node.phase == 'before_deadline':
        yield 'wait_until_deadline', Node(node.state, 'at_or_after_deadline',
                                         node.unallocated, node.pending, node.withdrawn)
    for rule in spec['rules']:
        if rule['actor'] != unavailable and _enabled(rule, node):
            try:
                yield rule['name'], transition(node, rule)
            except lang.V3Error:
                pass
    if include_withdrawals:
        for i, role in enumerate(lang.ROLES):
            if role == unavailable or node.pending[i] <= 0:
                continue
            pending, withdrawn = list(node.pending), list(node.withdrawn)
            withdrawn[i] += pending[i]
            pending[i] = 0
            yield 'withdraw:' + role, Node(node.state, node.phase, node.unallocated,
                                           tuple(pending), tuple(withdrawn))


def recovery(spec: dict, node: Node, terminal: frozenset[str],
             unavailable: str | None) -> list[str] | None:
    """Existential authorized path to a fully allocated terminal state.

    We omit withdrawals from recovery search: withdrawing credits never reduces
    outstanding unallocated escrow or advances the agreement in this language.
    """
    if node.state in terminal and node.unallocated == 0:
        return []
    q = deque([(node, [])]); seen = {node}
    while q:
        cur, path = q.popleft()
        for action, next_node in successors(spec, cur, unavailable, False):
            future = path + [action]
            if next_node.state in terminal and next_node.unallocated == 0:
                return future
            if next_node not in seen:
                q.append((next_node, future)); seen.add(next_node)
    return None


def verify(raw: Any) -> dict:
    spec = lang.validate(raw)
    terminal = lang.terminals(spec)
    start = initial(spec)
    paths = {start: []}; queue = deque([start])
    accounting_violations: list[dict] = []
    divergent: list[dict] = []; equivalent: list[dict] = []
    exercised: set[str] = set()
    deposit = spec['amount_wei']
    while queue:
        node = queue.popleft()
        path = paths[node]
        if node.unallocated + sum(node.pending) + sum(node.withdrawn) != deposit:
            accounting_violations.append({'reason': 'ESCROW_CONSERVATION', 'node': node.summary(), 'trace': path})
        if node.state in terminal and node.unallocated != 0:
            accounting_violations.append({'reason': 'TERMINAL_RESIDUAL_ESCROW',
                                          'node': node.summary(), 'trace': path})
        enabled = [r for r in spec['rules'] if _enabled(r, node)]
        outcomes = []
        for rule in enabled:
            exercised.add(rule['name'])
            try:
                nxt = transition(node, rule)
                outcomes.append((rule, nxt))
            except lang.V3Error as exc:
                accounting_violations.append({'reason': 'INVALID_CREDIT', 'action': rule['name'],
                                              'detail': exc.reason, 'node': node.summary(), 'trace': path})
        for i, (a, an) in enumerate(outcomes):
            for b, bn in outcomes[i + 1:]:
                witness = {'state': node.summary(), 'trace': path,
                           'actions': sorted((a['name'], b['name']))}
                if (an.state, an.unallocated, an.pending) != (bn.state, bn.unallocated, bn.pending):
                    divergent.append({**witness, 'effect_a': an.summary(), 'effect_b': bn.summary()})
                else:
                    equivalent.append(witness)
        for action, nxt in successors(spec, node):
            if nxt not in paths:
                if len(paths) >= MAX_NODES:
                    raise lang.V3Error('graph', 'Graph bound exceeded; cannot authorize emission')
                paths[nxt] = path + [action]; queue.append(nxt)
    unused = sorted(set(r['name'] for r in spec['rules']) - exercised)
    nonterminal = [(node, trace) for node, trace in paths.items() if node.state not in terminal]
    unavailable_traps = []; unconditional = []; witnesses = []
    for node, trace in nonterminal:
        for absent in (None, *lang.ROLES):
            solution = recovery(spec, node, terminal, absent)
            if solution is None:
                event = {'node': node.summary(), 'trace': trace,
                         'unavailable_role': absent}
                if absent is None:
                    unconditional.append(event)
                else:
                    unavailable_traps.append(event)
            else:
                witnesses.append({'state': node.state, 'phase': node.phase,
                                  'unallocated_wei': node.unallocated,
                                  'unavailable_role': absent, 'recovery_actions': solution})
    blockers = []
    if accounting_violations:
        blockers.append('INVALID_OR_NONCONSERVING_CREDIT_PATH')
    if unused:
        blockers.append('UNREACHABLE_OR_UNEXECUTABLE_ACTION')
    if divergent:
        blockers.append('DIVERGENT_SIMULTANEOUS_ACTIONS_REQUIRES_INTENT_REVIEW')
    if unconditional:
        blockers.append('NO_SETTLEMENT_PATH')
    if unavailable_traps:
        blockers.append('SINGLE_UNAVAILABLE_ROLE_TRAP')
    return {
        'schema': 'nanojev.contract.v3.graph_verifier.v1',
        'status': 'PASS_CONDITIONAL' if not blockers else 'BLOCKED_BY_V3_GRAPH_AUDIT',
        'emission_authorized_by_graph_audit': not bool(blockers),
        'source': 'VALIDATED_V3_IR_NOT_NATURAL_LANGUAGE',
        'model': {'amount_wei': deposit, 'deadline_seconds': spec['deadline_seconds'],
                  'roles': list(lang.ROLES), 'terminal_states': sorted(terminal),
                  'reachable_nodes': len(paths), 'unsettled_nodes': len(nonterminal),
                  'accounting_includes_withdrawal_states': True,
                  'credit_rule_count': sum(bool(r['credits']) for r in spec['rules']),
                  'partial_nonterminal_rules': [r['name'] for r in spec['rules']
                                                if r['to'] not in terminal and r['credits']]},
        'conservation': {'status': 'PASS_IR' if not accounting_violations else 'FAIL_IR',
                         'formula': 'deposit = unallocated + sum(pending) + sum(withdrawn)',
                         'violations': accounting_violations},
        'authorization': {'status': 'PASS_IR',
                          'role_guards': [{'action': r['name'], 'role': r['actor']}
                                          for r in spec['rules']],
                          'solidity_enforcement': 'NOT_PROVED_BY_IR'},
        'conflicts': {'divergent': divergent, 'equivalent': equivalent},
        'liveness': {'status': 'PASS_CONDITIONAL' if not unconditional and not unavailable_traps
                     else 'FAIL_TRAPPED_ESCROW_RISK',
                     'policy': 'EXISTS_FULL_ALLOCATION_PATH_AFTER_ANY_SINGLE_ROLE_STOPS_CALLING',
                     'single_role_traps': unavailable_traps,
                     'unconditional_traps': unconditional,
                     'checked_scenarios': len(nonterminal) * len(lang.ROLES),
                     'recovery_witnesses': witnesses},
        'unexecutable_actions': unused,
        'unverified': ['ENGLISH_TO_IR_REQUIREMENT_COMPLETENESS',
                       'ONCHAIN_IMPLEMENTATION_EQUIVALENCE', 'WITHDRAWAL_DESTINATION_ACCEPTANCE',
                       'AUTONOMOUS_EXECUTION', 'TRANSACTION_INCLUSION_FAIRNESS',
                       'MULTI_ACTOR_UNAVAILABILITY', 'TIMESTAMP_ADVERSARIAL_MANIPULATION',
                       'EXTERNAL_ETH_FORCED_TO_CONTRACT'],
        'blockers': blockers
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    opts = parser.parse_args(argv)
    result: dict
    try:
        if opts.spec.resolve() == opts.output.resolve() or opts.output.exists():
            raise lang.V3Error('preflight', 'Output must be fresh and distinct from input')
        result = verify(json.loads(opts.spec.read_text(encoding='utf-8')))
    except (lang.V3Error, ValueError, OSError) as exc:
        result = {'schema': 'nanojev.contract.v3.graph_verifier.v1',
                  'status': 'BLOCKED_INVALID_INPUT',
                  'emission_authorized_by_graph_audit': False,
                  'blockers': [str(exc)[:500]]}
    if not opts.output.exists():
        opts.output.parent.mkdir(parents=True, exist_ok=True)
        opts.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'blockers': result['blockers'],
                      'report': str(opts.output)}, separators=(',', ':')))
    return 0 if result['status'] == 'PASS_CONDITIONAL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
