"""Bounded, deterministic finite-state ETH language lowerer; smoke only, not audited.

No contract-family template dispatch: each guarded transition is compiled from its
explicit actor, source state, destination state, deadline guard, and payout list.
"""
from __future__ import annotations
import re
from collections import deque
from typing import Any
from tools.nanojev_contract_solidity_smoke import SmokeFailure

LANG = 'nanojev.contract.state_machine.v1'
ROLES = ('payer', 'worker', 'reviewer')
FIXED = {'language': LANG, 'asset': 'native_eth', 'payer': 'deployer',
         'worker': 'constructor_worker', 'reviewer': 'constructor_reviewer'}
WHEN = ('always', 'before_deadline', 'at_or_after_deadline')
IDENT = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')


def validate(obj: Any) -> dict:
    if not isinstance(obj, dict) or set(obj) != set(FIXED) | {'amount_wei', 'deadline_seconds', 'rules'}:
        raise SmokeFailure('contract_validation', 'Invalid state-machine field set')
    if any(obj[k] != v for k, v in FIXED.items()):
        raise SmokeFailure('contract_validation', 'Contract roles or language changed')
    if type(obj['amount_wei']) is not int or not 0 < obj['amount_wei'] < 2**128:
        raise SmokeFailure('contract_validation', 'Invalid positive ETH amount')
    if type(obj['deadline_seconds']) is not int or not 0 < obj['deadline_seconds'] <= 365 * 86400:
        raise SmokeFailure('contract_validation', 'Deadline outside 1..365 days')
    rules = obj['rules']
    if not isinstance(rules, list) or not 2 <= len(rules) <= 6:
        raise SmokeFailure('contract_validation', 'Must have 2..6 transitions')
    names, normalized, states = set(), [], {'funded'}
    for r in rules:
        if not isinstance(r, dict) or set(r) != {'name', 'actor', 'from', 'to', 'when', 'payments'}:
            raise SmokeFailure('contract_validation', 'Bad transition keys')
        action, src, dst = r['name'], r['from'], r['to']
        if (not all(isinstance(x, str) and IDENT.fullmatch(x) for x in (action, src, dst))
                or action in names or action in {'fund', 'state', 'payer', 'worker', 'reviewer',
                                                   'constructor', 'receive', 'fallback', 'fundedat'}
                or src == dst or dst == 'funded'):
            raise SmokeFailure('contract_validation', 'Unsafe, duplicate or invalid identifier')
        if r['actor'] not in ROLES or r['when'] not in WHEN:
            raise SmokeFailure('contract_validation', 'Unsupported actor or time guard')
        names.add(action)
        states.update((src, dst))
        payments = r['payments']
        if not isinstance(payments, list) or len(payments) > 3:
            raise SmokeFailure('contract_validation', 'Invalid payout list')
        to_seen, payout, sum_bps = set(), [], 0
        for item in payments:
            if (not isinstance(item, dict) or set(item) != {'to', 'bps'} or
                item['to'] not in ROLES or item['to'] in to_seen or
                type(item['bps']) is not int or not 1 <= item['bps'] <= 10000):
                raise SmokeFailure('contract_validation', 'Duplicate or invalid payout')
            to_seen.add(item['to'])
            sum_bps += item['bps']
            payout.append(dict(item))
        if payout and sum_bps != 10000:
            raise SmokeFailure('contract_validation', 'Payout sum must equal 10000 bps')
        normalized.append({'name': action, 'actor': r['actor'], 'from': src,
                           'to': dst, 'when': r['when'],
                           'payments': sorted(payout, key=lambda x: x['to'])})
    if len(states) > 8:
        raise SmokeFailure('contract_validation', 'Too many states')
    edges = {s: [] for s in states}
    for r in normalized:
        edges[r['from']].append(r)
    visited, active = set(), set()
    def walk(s):
        if s in active:
            raise SmokeFailure('contract_validation', 'Cycles are not representable')
        if s in visited:
            return
        active.add(s)
        for r in edges[s]:
            walk(r['to'])
        active.remove(s)
        visited.add(s)
    walk('funded')
    if visited != states:
        raise SmokeFailure('contract_validation', 'Unreachable states')
    for s in states:
        arriving = [r for r in normalized if r['to'] == s]
        if s != 'funded' and not edges[s] and (not arriving or any(not r['payments'] for r in arriving)):
            raise SmokeFailure('contract_validation', 'Terminal states must settle full balance')
        if edges[s] and any(r['payments'] for r in arriving):
            raise SmokeFailure('contract_validation', 'Transitions after settlement forbidden')
    if not any(r['payments'] for r in normalized):
        raise SmokeFailure('contract_validation', 'No settlement route')
    return {**FIXED, 'amount_wei': obj['amount_wei'],
            'deadline_seconds': obj['deadline_seconds'], 'rules': sorted(normalized, key=lambda r:r['name'])}


def state_ids(spec):
    states = sorted({'funded'} | {x for r in spec['rules'] for x in (r['from'], r['to'])})
    order = ['unfunded', 'funded'] + [s for s in states if s != 'funded']
    caps = {s: ''.join(p.capitalize() for p in s.split('_')) for s in order}
    if len(set(caps.values())) != len(order):
        raise SmokeFailure('solidity_emit', 'State normalization collision')
    return caps, {s:i for i,s in enumerate(order)}


def source(spec, name):
    spec = validate(spec)
    if not re.fullmatch(r'NanoJevComposition[0-9]{2}', name):
        raise SmokeFailure('solidity_emit', 'Unsafe contract class')
    caps, _ = state_ids(spec)
    methods = []
    for r in spec['rules']:
        lines = [f'        require(msg.sender == {r["actor"]}, "not authorized");',
                 f'        require(state == State.{caps[r["from"]]}, "wrong state");']
        if r['when'] == 'before_deadline':
            lines.append('        require(block.timestamp < fundedAt + DEADLINE_SECONDS, "deadline expired");')
        elif r['when'] == 'at_or_after_deadline':
            lines.append('        require(block.timestamp >= fundedAt + DEADLINE_SECONDS, "too early");')
        lines.append(f'        state = State.{caps[r["to"]]};')
        payments = r['payments']
        for i, p in enumerate(payments):
            amount = (f'(AMOUNT_WEI * {p["bps"]}) / 10000' if i < len(payments)-1 else
                      'AMOUNT_WEI' if i == 0 else
                      'AMOUNT_WEI - (' + ' + '.join('share'+str(j) for j in range(i)) + ')')
            lines.append(f'        uint256 share{i} = {amount};')
        for i, p in enumerate(payments):
            lines.extend((f'        (bool ok{i},) = {p["to"]}.call{{value: share{i}}}("");',
                          f'        require(ok{i}, "transfer failed");'))
        methods.append('    function '+r['name']+'() external {\n'+'\n'.join(lines)+'\n    }')
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Diagnostic state machine; unaudited, do not deploy.
contract {name} {{
    enum State {{ {', '.join(caps.values())} }}
    State public state;
    address payable public immutable payer;
    address payable public immutable worker;
    address payable public immutable reviewer;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant DEADLINE_SECONDS = {spec['deadline_seconds']};
    uint256 public fundedAt;
    constructor(address payable worker_, address payable reviewer_) {{
        require(worker_ != address(0) && reviewer_ != address(0), "zero actor");
        require(worker_ != reviewer_ && worker_ != msg.sender && reviewer_ != msg.sender, "duplicate actor");
        payer = payable(msg.sender);
        worker = worker_;
        reviewer = reviewer_;
    }}
    function fund() external payable {{
        require(msg.sender == payer, "payer only");
        require(state == State.Unfunded, "already funded");
        require(msg.value == AMOUNT_WEI, "wrong amount");
        fundedAt = block.timestamp;
        state = State.Funded;
    }}
{chr(10).join(methods)}
}}
'''


def route(spec, target):
    q = deque([('funded', [])])
    while q:
        state, path = q.popleft()
        if state == target:
            return path
        for r in spec['rules']:
            if r['from'] == state:
                q.append((r['to'], path+[r]))
    raise SmokeFailure('solidity_emit', 'Unreachable gold scenario')


def tests(gold, name):
    """Generate behavioral tests from pinned GOLD, never from inferred text or Solidity."""
    spec = validate(gold)
    _, stateid = state_ids(spec)
    methods = []
    def add(testname, lines, param=''):
        if testname.startswith('testFail'):
            raise SmokeFailure('solidity_emit', 'Deprecated Foundry testFail name')
        methods.append('    function '+testname+'('+param+') public {\n'+'\n'.join(lines)+'\n    }')
    def clock(r, wrong=False):
        when = r['when']
        if when == 'always':
            return []
        after = (when == 'at_or_after_deadline') != wrong
        return ['        vm.warp(c.fundedAt() + DEADLINE'+(');' if after else ' - 1);')]
    def invoke(r, *, bad_actor=False, expect_revert=False):
        caller = 'STRANGER' if bad_actor else ('address(this)' if r['actor']=='payer' else 'address(c.worker())' if r['actor']=='worker' else 'REVIEWER')
        lines = [f'        vm.prank({caller});']
        if expect_revert:
            lines.extend((f'        (bool ok,) = address(c).call(abi.encodeWithSignature("{r["name"]}()"));',
                          f'        require(!ok, "unexpected {r["name"]} success");'))
        else:
            lines.append(f'        c.{r["name"]}();')
        return lines
    def prepare(r, reject_worker=False):
        lines = [f'        {name} c = funded({str(reject_worker).lower()});']
        for previous in route(spec, r['from']):
            lines.extend(clock(previous))
            lines.extend(invoke(previous))
        return lines
    add('testNoUnauthorizedFunding', [f'        {name} c = fresh(false);', '        vm.deal(STRANGER, AMOUNT);',
         '        vm.prank(STRANGER);',
         '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
         '        require(!ok, "outsider funded");'])
    add('testWrongAmountRejected', [f'        {name} c = fresh(false);',
         '        (bool ok,) = address(c).call{value: AMOUNT - 1}(abi.encodeWithSignature("fund()"));',
         '        require(!ok, "wrong amount accepted");'])
    add('testDuplicateDepositRejected', [f'        {name} c = funded(false);',
         '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
         '        require(!ok, "second deposit allowed");'])
    add('testFuzzWrongAmount', [f'        {name} c = fresh(false);',
         '        uint256 value = uint256(seed) % AMOUNT;',
         '        (bool ok,) = address(c).call{value: value}(abi.encodeWithSignature("fund()"));',
         '        require(!ok, "incorrect deposit accepted");'], 'uint96 seed')
    add('testDuplicateConstructorRoleRejected', [f'        try new {name}(WORKER, WORKER) returns ({name} c) {{',
         '            require(address(c) != address(0), "unexpected zero address");',
         '            revert("accepted duplicate roles");', '        } catch {}'])
    for r in spec['rules']:
        suffix = ''.join(x.capitalize() for x in r['name'].split('_'))
        add('testUnfundedRejects'+suffix, [f'        {name} c = fresh(false);'] + invoke(r, expect_revert=True))
        add('testUnauthorized'+suffix, prepare(r)+clock(r)+invoke(r, bad_actor=True, expect_revert=True))
        if r['when'] != 'always':
            add('testBoundaryRejects'+suffix, prepare(r)+clock(r, wrong=True)+invoke(r, expect_revert=True))
        happy = prepare(r)+clock(r)
        payees = {p['to'] for p in r['payments']}
        for role in payees:
            happy.append(f'        uint256 prior{role} = {"address(this)" if role == "payer" else "address(c.worker())" if role == "worker" else "REVIEWER"}.balance;')
        happy += invoke(r)
        happy += [f'        require(uint256(c.state()) == {stateid[r["to"]]}, "wrong oracle state");']
        running = 0
        for i, p in enumerate(r['payments']):
            expected = (f'(AMOUNT * {p["bps"]}) / 10000' if i < len(r['payments'])-1 else
                        'AMOUNT' if i == 0 else f'AMOUNT - (AMOUNT * {running}) / 10000')
            happy.append(f'        require({"address(this)" if p["to"] == "payer" else "address(c.worker())" if p["to"] == "worker" else "REVIEWER"}.balance - prior{p["to"]} == {expected}, "wrong oracle payout");')
            running += p['bps']
        happy.append('        require(address(c).balance == '+('0' if r['payments'] else 'AMOUNT')+', "unexpected retained balance");')
        add('testOracleHappy'+suffix, happy)
        if r['payments']:
            add('testSettlementIrreversible'+suffix,
                prepare(r)+clock(r)+invoke(r)+invoke(r, expect_revert=True))
            if 'worker' in payees and r['actor'] != 'worker':
                reject = prepare(r, reject_worker=True)+clock(r)+[
                    '        uint256 initialBalance = address(c).balance;']+invoke(r, expect_revert=True)+[
                    f'        require(uint256(c.state()) == {stateid[r["from"]]}, "settlement state not rolled back");',
                    '        require(address(c).balance == initialBalance, "partial payment escaped");']
                add('testAtomicTransferRejection'+suffix, reject)
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface Vm{name} {{
    function deal(address who, uint256 amount) external;
    function prank(address who) external;
    function warp(uint256 timestamp) external;
}}
contract RejectETH{name} {{ receive() external payable {{ revert("reject"); }} }}
contract {name}SmokeTest {{
    Vm{name} internal constant vm = Vm{name}(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {spec['amount_wei']};
    uint256 internal constant DEADLINE = {spec['deadline_seconds']};
    address payable internal constant WORKER = payable(address(0xBEEF));
    address payable internal constant REVIEWER = payable(address(0xCAFE));
    address internal constant STRANGER = address(0xBAD);
    receive() external payable {{}}
    function fresh(bool rejectingWorker) internal returns ({name} c) {{
        vm.deal(address(this), AMOUNT * 4);
        if (rejectingWorker) {{
            RejectETH{name} rejector = new RejectETH{name}();
            c = new {name}(payable(address(rejector)), REVIEWER);
        }} else {{
            c = new {name}(WORKER, REVIEWER);
        }}
    }}
    function funded(bool rejectingWorker) internal returns ({name} c) {{
        c = fresh(rejectingWorker);
        c.fund{{value: AMOUNT}}();
    }}
{chr(10).join(methods)}
}}
'''
