"""Contract Language v3, phase 1: bounded progressive native-ETH escrow IR.

This is a new, versioned language; v2 modules and semantics are not modified.
No oracles, appointment, arbitration, multi-deadlines, extension or deployment.
"""
from __future__ import annotations

import re
from typing import Any

LANG = 'nanojev.contract.state_machine.v3'
ROLES = ('payer', 'worker', 'reviewer')
PHASES = ('before_deadline', 'at_or_after_deadline')
WHEN = ('always', *PHASES)
IDENT = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
ROOT = {'language': LANG, 'asset': 'native_eth', 'settlement_mode': 'pull',
        'payer': 'deployer', 'worker': 'constructor_worker',
        'reviewer': 'constructor_reviewer'}
RESERVED = {'fund', 'withdraw', 'withdrawto', 'withdraw_to', 'state', 'payer',
            'worker', 'reviewer', 'constructor', 'receive', 'fallback', 'fundedat',
            'unallocated', 'totalpending', 'totalwithdrawn', 'complete',
            'pendingwithdrawals', 'creditassigned', 'creditwithdrawn', 'amount_wei',
            'deadline_seconds', 'escrowconserved'}


class V3Error(ValueError):
    def __init__(self, stage: str, reason: str):
        super().__init__(f'{stage}: {reason}')
        self.stage, self.reason = stage, reason


def validate(raw: Any) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(ROOT) | {'amount_wei', 'deadline_seconds', 'rules'}:
        raise V3Error('schema', 'Exact v3 escrow fields required')
    if any(raw.get(k) != v for k, v in ROOT.items()):
        raise V3Error('schema', 'Wrong v3 language, pull mode or constructor roles')
    deposit = raw['amount_wei']
    deadline = raw['deadline_seconds']
    if type(deposit) is not int or not 0 < deposit < 2**128:
        raise V3Error('schema', 'Deposit must be an integer 1..2^128-1 wei')
    if type(deadline) is not int or not 0 < deadline <= 365 * 86400:
        raise V3Error('schema', 'Deadline must be between 1 and 365 days')
    rules = raw['rules']
    if not isinstance(rules, list) or not 2 <= len(rules) <= 24:
        raise V3Error('schema', 'Need 2..24 explicit action rules')
    names, selectors, normalized = set(), set(), []
    states = {'funded'}
    for original in rules:
        if not isinstance(original, dict) or set(original) != {'name', 'actor', 'from', 'to', 'when', 'credits'}:
            raise V3Error('schema', 'Each rule needs name, actor, from, to, when, credits')
        action, src, dst = original['name'], original['from'], original['to']
        if not all(isinstance(x, str) and IDENT.fullmatch(x) for x in (action, src, dst)):
            raise V3Error('schema', 'Invalid rule/state identifier')
        solidity_name = action.replace('_', '').lower()
        if action in names or solidity_name in selectors or action in RESERVED or solidity_name in RESERVED:
            raise V3Error('schema', 'Duplicate or reserved action name')
        if src == dst or dst in {'funded', 'unfunded'} or src == 'unfunded':
            raise V3Error('schema', 'Only progress to another state; cannot reenter funded')
        if original['actor'] not in ROLES or original['when'] not in WHEN:
            raise V3Error('schema', 'Invalid action authority or deadline guard')
        amounts = original['credits']
        if not isinstance(amounts, list) or len(amounts) > len(ROLES):
            raise V3Error('schema', 'Invalid credit list')
        recipients, alloc, remaining_seen = set(), [], False
        for index, item in enumerate(amounts):
            if not isinstance(item, dict) or set(item) != {'to', 'amount_wei'}:
                raise V3Error('schema', 'Each credit needs to and amount_wei')
            payee, amount = item['to'], item['amount_wei']
            if payee not in ROLES or payee in recipients:
                raise V3Error('schema', 'Duplicate/invalid recipient')
            recipients.add(payee)
            if amount == 'remaining':
                if remaining_seen or index != len(amounts)-1:
                    raise V3Error('schema', 'Only final credit may use remaining')
                remaining_seen = True
            elif type(amount) is not int or not 0 < amount <= deposit:
                raise V3Error('schema', 'Credit must be positive integer wei or final remaining')
            alloc.append({'to': payee, 'amount_wei': amount})
        names.add(action)
        selectors.add(solidity_name)
        states.update((src, dst))
        normalized.append({'name': action, 'actor': original['actor'], 'from': src,
                           'to': dst, 'when': original['when'], 'credits': alloc})
    if len(states) > 12:
        raise V3Error('schema', 'At most 12 states in v3 phase 1')
    edges = {s: [] for s in states}
    for r in normalized:
        edges[r['from']].append(r)
    reached, active = set(), set()
    def visit(state: str) -> None:
        if state in active:
            raise V3Error('schema', 'Cycles unsupported in v3 phase 1')
        if state in reached:
            return
        active.add(state)
        for rule in edges[state]:
            visit(rule['to'])
        active.remove(state)
        reached.add(state)
    visit('funded')
    if reached != states:
        raise V3Error('schema', 'Unreachable state declared')
    if not any(not edges[s] for s in states):
        raise V3Error('schema', 'No terminal state')
    return {**ROOT, 'amount_wei': deposit, 'deadline_seconds': deadline,
            'rules': sorted(normalized, key=lambda r: r['name'])}


def terminals(spec: dict) -> frozenset[str]:
    all_states = {'funded'} | {x for rule in spec['rules'] for x in (rule['from'], rule['to'])}
    from_states = {rule['from'] for rule in spec['rules']}
    return frozenset(all_states - from_states)


def apply_credits(remaining: int, outstanding: tuple[int, int, int],
                  credits: list[dict]) -> tuple[int, tuple[int, int, int]]:
    """Pure accounting primitive used by the v3 IR graph, not by Solidity tests."""
    pending = list(outstanding)
    unallocated = remaining
    for credit in credits:
        wei = unallocated if credit['amount_wei'] == 'remaining' else credit['amount_wei']
        if type(wei) is not int or wei <= 0 or wei > unallocated:
            raise V3Error('accounting', 'Insufficient unallocated escrow or zero credit')
        unallocated -= wei
        pending[ROLES.index(credit['to'])] += wei
    return unallocated, tuple(pending)
