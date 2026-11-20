"""Generate bounded, IR-derived Forge behavior tests for generic v3 contracts.

These tests check Solidity against IR semantics, NOT against independently
human-annotated English. A separate pinned adversarial Forge suite exercises
the 12-ETH progressive prototype/beta/final fixture.
"""
from __future__ import annotations
from collections import deque

from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_verifier as verifier
from tools import nanojev_contract_v3_reference as reference
from tools import nanojev_contract_v3_solidity as solidity


def witnesses(spec: dict) -> dict[str, list[str]]:
    start = verifier.initial(spec)
    found = {}
    queue = deque([start]); paths = {start: []}
    while queue:
        cur = queue.popleft()
        for r in spec['rules']:
            if verifier._enabled(r, cur) and r['name'] not in found:
                found[r['name']] = paths[cur]
        for action, nxt in verifier.successors(spec, cur, include_withdrawals=False):
            if nxt not in paths:
                paths[nxt] = paths[cur] + [action]
                queue.append(nxt)
    if len(found) != len(spec['rules']):
        raise lang.V3Error('oracle', 'IR contains an unreachable action')
    return found


def tests(raw: dict, name: str) -> tuple[str, int]:
    spec = lang.validate(raw)
    if not verifier.verify(spec)['emission_authorized_by_graph_audit']:
        raise lang.V3Error('oracle', 'Cannot generate Forge tests for blocked IR')
    _, state_ids = solidity.state_ids(spec)
    known = witnesses(spec)
    rules = {x['name']: x for x in spec['rules']}
    functions: list[str] = []
    def add(nm: str, code: list[str], params: str = ''):
        functions.append('    function '+nm+'('+params+') public {\n'+'\n'.join(code)+'\n    }')
    def call(name_: str) -> list[str]:
        r = rules[name_]
        prefix = [f'        vm.prank({r["actor"].upper()});'] if r['actor'] != 'payer' else []
        return prefix + [f'        c.{name_}();']
    def setup(path: list[str]):
        lines = [f'        {name} c = funded();']
        for action in path:
            if action == 'wait_until_deadline':
                lines += ['        vm.warp(c.fundedAt() + DEADLINE);']
            else:
                lines += call(action)
        return lines
    add('testUnfundedCannotWithdraw', [f'        {name} c = fresh();',
        '        (bool ok,) = address(c).call(abi.encodeWithSignature("withdraw()"));',
        '        require(!ok, "unfunded withdrawal");'])
    add('testOnlyPayerCanFund', [f'        {name} c = fresh();',
        '        vm.deal(STRANGER, AMOUNT);', '        vm.prank(STRANGER);',
        '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "outsider funding");'])
    add('testDoubleFundingForbidden', [f'        {name} c = funded();',
        '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "double funding");'])
    add('testFuzzWrongDepositRejected', [f'        {name} c = fresh();',
        '        uint256 wrong = uint256(seed) % AMOUNT;',
        '        (bool ok,) = address(c).call{value: wrong}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "accepted wrong amount");'], 'uint96 seed')
    for r in spec['rules']:
        nm = r['name']; suffix = ''.join(x.capitalize() for x in nm.split('_'))
        path = known[nm]
        model = reference.ReferenceEscrow(spec)
        model.fund('payer', spec['amount_wei'])
        for action in path + [nm]:
            if action == 'wait_until_deadline':
                model.tick_to_deadline()
            else:
                model.execute(action, rules[action]['actor'])
        state, phase, unallocated, outstanding, withdrawn = model.snapshot()
        code = setup(path) + call(nm) + [
            f'        require(uint256(c.state()) == {state_ids[state]}, "state");',
            f'        require(c.unallocated() == {unallocated}, "unallocated");',
            f'        require(c.totalPending() == {sum(outstanding)}, "pending total");',
            f'        require(c.totalWithdrawn() == {sum(withdrawn)}, "withdrawn total");',
            f'        require(c.complete() == {str(state in model.sinks).lower()}, "completion");',
            '        require(c.escrowConserved(), "conservation");']
        for i, role in enumerate(lang.ROLES):
            code += [f'        require(c.pendingWithdrawals({role.upper()}) == {outstanding[i]}, "credit {role}");']
        add('testSemantics'+suffix, code)
        bad = setup(path) + ['        vm.prank(STRANGER);',
                             f'        (bool ok,) = address(c).call(abi.encodeWithSignature("{nm}()"));',
                             '        require(!ok, "unauthorized action");',
                             '        require(c.escrowConserved(), "conservation after refusal");']
        add('testUnauthorized'+suffix, bad)
    header = f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface VmV3 {{
    function deal(address who, uint256 amount) external;
    function prank(address who) external;
    function warp(uint256 timestamp) external;
}}
contract {name}GeneratedTest {{
    VmV3 internal constant vm = VmV3(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {spec['amount_wei']};
    uint256 internal constant DEADLINE = {spec['deadline_seconds']};
    address payable internal PAYER = payable(address(this));
    address payable internal constant WORKER = payable(address(0xBEEF));
    address payable internal constant REVIEWER = payable(address(0xCAFE));
    address payable internal constant STRANGER = payable(address(0xD00D));
    function setUp() public {{ vm.deal(address(this), AMOUNT * 4); }}
    function fresh() internal returns ({name} c) {{ return new {name}(WORKER, REVIEWER); }}
    function funded() internal returns ({name} c) {{
        c = fresh(); c.fund{{value: AMOUNT}}();
    }}
'''
    # Payee role PAYER needs reference to the test contract. PAYER is an internal
    # getter-like immutable expression, available from tests through address(this).
    return header + '\n'.join(functions) + '\n    receive() external payable {}\n}\n', len(functions)
