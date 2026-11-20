"""Generic progressive-escrow v3 Solidity 0.8.24 source emitter.

Emission requires a PASS from the bounded v3 accounting-state graph. This is an
experimental compiler and generated contracts are NOT audited for deployment.
"""
from __future__ import annotations
import re

from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_verifier as graph


def state_ids(spec: dict) -> tuple[dict[str, str], dict[str, int]]:
    states = sorted({'funded'} | {x for r in spec['rules'] for x in (r['from'], r['to'])})
    ordered = ['unfunded', 'funded'] + [x for x in states if x != 'funded']
    names = {x: ''.join(segment.capitalize() for segment in x.split('_')) for x in ordered}
    if len(set(names.values())) != len(names) or any(not value for value in names.values()):
        raise lang.V3Error('solidity_emit', 'Solidity enum-name collision')
    return names, {name: i for i, name in enumerate(ordered)}


def source(raw: dict, name: str) -> str:
    spec = lang.validate(raw)
    if not re.fullmatch(r'NanoJevProgressive[0-9]{2}', name):
        raise lang.V3Error('solidity_emit', 'Unsafe class name')
    audit = graph.verify(spec)
    if not audit['emission_authorized_by_graph_audit']:
        raise lang.V3Error('solidity_emit', 'V3 graph audit blocked: ' + ', '.join(audit['blockers']))
    caps, _ = state_ids(spec)
    terminal = lang.terminals(spec)
    methods = []
    for r in spec['rules']:
        code = [f'        require(msg.sender == {r["actor"]}, "not authorized");',
                '        require(!withdrawalLock && !complete, "locked/complete");',
                f'        require(state == State.{caps[r["from"]]}, "wrong state");']
        if r['when'] == 'before_deadline':
            code.append('        require(block.timestamp < fundedAt + DEADLINE_SECONDS, "deadline elapsed");')
        elif r['when'] == 'at_or_after_deadline':
            code.append('        require(block.timestamp >= fundedAt + DEADLINE_SECONDS, "too early");')
        code.append(f'        state = State.{caps[r["to"]]};')
        for i, c in enumerate(r['credits']):
            amt = 'unallocated' if c['amount_wei'] == 'remaining' else str(c['amount_wei'])
            code += [f'        uint256 credit{i} = {amt};',
                     f'        require(credit{i} > 0 && credit{i} <= unallocated, "insufficient escrow");',
                     f'        unallocated -= credit{i};',
                     f'        pendingWithdrawals[{c["to"]}] += credit{i};',
                     f'        totalPending += credit{i};',
                     f'        emit CreditAssigned({c["to"]}, credit{i});']
        if r['to'] in terminal:
            code += ['        require(unallocated == 0, "terminal residual");',
                     '        complete = true;']
        code.append('        assert(unallocated + totalPending + totalWithdrawn == AMOUNT_WEI);')
        methods.append('    function '+r['name']+'() external {\n'+'\n'.join(code)+'\n    }')
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Experimental generated progressive escrow. UNAUDITED. DO NOT DEPLOY.
contract {name} {{
    enum State {{ {', '.join(caps.values())} }}
    State public state;
    address payable public immutable payer;
    address payable public immutable worker;
    address payable public immutable reviewer;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant DEADLINE_SECONDS = {spec['deadline_seconds']};
    uint256 public fundedAt;
    uint256 public unallocated;
    mapping(address => uint256) public pendingWithdrawals;
    uint256 public totalPending;
    uint256 public totalWithdrawn;
    bool public complete;
    bool private withdrawalLock;
    event CreditAssigned(address indexed beneficiary, uint256 amount);
    event CreditWithdrawn(address indexed beneficiary, address indexed receiver, uint256 amount);

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
        unallocated = msg.value;
        state = State.Funded;
        assert(unallocated + totalPending + totalWithdrawn == AMOUNT_WEI);
    }}
    /// @dev Incoming ordinary ETH is not another funding operation.
    receive() external payable {{ revert("fund only"); }}
    fallback() external payable {{ revert("unknown call"); }}
    function escrowConserved() external view returns (bool) {{
        if (state == State.Unfunded) return unallocated == 0 && totalPending == 0 && totalWithdrawn == 0;
        return unallocated + totalPending + totalWithdrawn == AMOUNT_WEI;
    }}
    /// @notice Beneficiary chooses destination; no other payee's withdrawal can be blocked.
    function withdrawTo(address payable destination) public {{
        require(!withdrawalLock, "withdrawal reentrant");
        require(destination != address(0), "zero destination");
        uint256 amount = pendingWithdrawals[msg.sender];
        require(amount > 0, "nothing owed");
        withdrawalLock = true;
        pendingWithdrawals[msg.sender] = 0;
        totalPending -= amount;
        totalWithdrawn += amount;
        (bool ok,) = destination.call{{value: amount}}("");
        require(ok, "withdrawal failed");
        assert(unallocated + totalPending + totalWithdrawn == AMOUNT_WEI);
        withdrawalLock = false;
        emit CreditWithdrawn(msg.sender, destination, amount);
    }}
    function withdraw() external {{ withdrawTo(payable(msg.sender)); }}
{chr(10).join(methods)}
}}
'''
