"""Finite-state, symbolic-deadline, single-unavailable-role liveness oracle.

This is a diagnostic policy evaluator, NOT Solidity formal verification.
It tests existential settlement reachable with one role refusing to call,
assuming (a) other authorized callers eventually act, (b) time advances,
(c) all payout recipients accept ETH and (d) miners do not manipulate
boundary timestamps. Separately enumerate remaining external-transfer risks.
The oracle uses contract-language rules; it does not read generated Solidity.
"""
from __future__ import annotations

from collections import deque
from tools import nanojev_contract_state_machine as machine

PHASES = ('before_deadline', 'at_or_after_deadline')
ROLES = machine.ROLES


def enabled(when: str, phase: str) -> bool:
    return (when == 'always' or
            (when == 'before_deadline' and phase == PHASES[0]) or
            (when == 'at_or_after_deadline' and phase == PHASES[1]))


def successors(spec: dict, node: tuple[str, str], blocked: str | None):
    state, phase = node
    if phase == PHASES[0]:
        yield ((state, PHASES[1]), 'wait_until_deadline', False)
    for r in spec['rules']:
        if (r['from'] == state and r['actor'] != blocked and
                enabled(r['when'], phase)):
            yield ((r['to'], phase), r['name'], bool(r['payments']))


def reachable(spec: dict):
    start = ('funded', PHASES[0])
    paths = {start: []}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for new, action, settled in successors(spec, node, None):
            if settled or new in paths:
                continue
            paths[new] = paths[node] + [action]
            queue.append(new)
    return paths


def can_settle(spec: dict, start: tuple[str, str], blocked: str | None):
    visited = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for new, _, settled in successors(spec, node, blocked):
            if settled:
                return True
            if new not in visited:
                visited.add(new)
                queue.append(new)
    return False


def evaluate(raw: dict) -> dict:
    """Enumerate every reachable live state and both time phases as appropriate.

    Every nonterminal reachable node must permit eventual full settlement even
    if any ONE of the three roles never calls another function. Waiting until
    deadline is allowed but deadlines never automatically trigger a call.
    """
    spec = machine.validate(raw)
    paths = reachable(spec)
    strong = []
    single = []
    for (state, phase), actions in sorted(paths.items()):
        witness = {'state': state, 'phase': phase, 'witness_actions': actions}
        if not can_settle(spec, (state, phase), None):
            strong.append({**witness, 'issue': 'NO_SETTLEMENT_EVEN_WITH_ALL_CALLERS'})
        for blocked in ROLES:
            if not can_settle(spec, (state, phase), blocked):
                single.append({**witness, 'unavailable_role': blocked,
                               'issue': 'SETTLEMENT_DEPENDS_ON_UNAVAILABLE_CALLER'})
    payees = sorted({p['to'] for r in spec['rules'] for p in r['payments']})
    return {
        'policy': 'EVERY_REACHABLE_FUNDED_STATE_SETTLES_DESPITE_ANY_ONE_NONCALLING_ROLE',
        'state_model': 'ACYCLIC_FUNDED_ETH; TWO_SYMBOLIC_TIME_PHASES',
        'status': 'PASS_CONDITIONAL' if not strong and not single else 'FAIL_TRAPPED_FUNDS_RISK',
        'checked_reachable_nodes': len(paths),
        'checked_role_unavailability_scenarios': len(paths) * len(ROLES),
        'unconditional_traps': strong,
        'single_role_traps': single,
        'conditional_assumptions': [
            'other authorized roles eventually invoke available transactions',
            'block time eventually reaches and remains after the deadline',
            'all payout recipients accept ETH transfers',
            'no blockchain censorship, network outage or validator time manipulation',
            'funding occurs once and no external ETH is forcibly sent to the contract',
        ],
        'receiver_refusal_residual_risk': {
            'status': 'NOT_PROVED_SAFE',
            'possible_rejecting_roles': payees,
            'reason': 'Generated contracts use atomic ETH push transfers; a rejecting recipient can revert settlement.',
        },
    }


def recovery_path(raw: dict, start: tuple[str, str], blocked: str):
    """Shortest action trace to a payout with role blocked; None if trapped."""
    spec = machine.validate(raw)
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        for nxt, action, payout in successors(spec, node, blocked):
            if payout:
                return path + [action]
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [action]))
    return None


def forge_recovery_tests(raw: dict, name: str) -> tuple[str, int]:
    """Append oracle-derived recovery witnesses to an existing Foundry test class.

    Uses independently specified canonical GOLD; never generated Solidity.
    These traces test existential recovery with one caller silent. The
    adversary may not force other actors or recipients to refuse ETH.
    """
    spec = machine.validate(raw)
    base = machine.tests(spec, name)
    paths = reachable(spec)
    rules = {r['name']: r for r in spec['rules']}
    functions = []

    def emit_action(action):
        if action == 'wait_until_deadline':
            return ['        vm.warp(c.fundedAt() + DEADLINE);']
        r = rules[action]
        addr = {'payer':'address(this)', 'worker':'WORKER', 'reviewer':'REVIEWER'}[r['actor']]
        return [f'        vm.prank({addr});', f'        c.{action}();']

    for node, arrival in sorted(paths.items()):
        state, phase = node
        for blocked in ROLES:
            remedy = recovery_path(spec, node, blocked)
            if remedy is None:
                raise ValueError(f'No recovery path for {state}/{phase}/{blocked}')
            for action in remedy:
                if action != 'wait_until_deadline' and rules[action]['actor'] == blocked:
                    raise AssertionError('Recovery trace includes unavailable caller')
            nameparts = [state, phase, blocked]
            fn = 'testRecovery' + ''.join(x.title().replace('_','') for x in nameparts)
            lines = [f'    function {fn}() public {{',
                     f'        {name} c = funded(false);']
            for action in arrival + remedy:
                lines.extend(emit_action(action))
            lines += [
                '        require(address(c).balance == 0, "funds remained locked");',
                '    }',
            ]
            functions.append('\n'.join(lines))
    if not base.rstrip().endswith('}'):
        raise AssertionError('Unexpected Foundry test class tail')
    return base.rstrip()[:-1] + '\n' + '\n'.join(functions) + '\n}\n', len(functions)
