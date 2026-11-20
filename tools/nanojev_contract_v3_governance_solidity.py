"""Solidity 0.8.24 emitter for bounded v3 governed milestone escrow.

Requires a PASS from the independent v3 governance IR graph gate. Calls use
permissionless deterministic default outcomes on timeout. No actual deployment.
"""
from __future__ import annotations
import re
from tools import nanojev_contract_v3_governance_language as lang
from tools import nanojev_contract_v3_governance_verifier as graph
from tools import nanojev_contract_v3_language as v3


def _selector(name,attr,spec):
    branches=[]
    for i,m in enumerate(spec['milestones']):
        branches.append(f'        if (index == {i}) return {m[attr]};')
    return (f'    function {name}(uint256 index) public pure returns (uint256) {{\n'
            +'\n'.join(branches)+'\n        revert("invalid milestone");\n    }\n')


def _outcome(policy):
    if policy=='pay_worker':return '        _award();'
    if policy=='refund_payer':return '        _refund();'
    raise v3.V3Error('solidity_emit','Refusing unknown timeout policy')


def source(raw,name='NanoJevGoverned01'):
    spec=lang.validate(raw)
    if not re.fullmatch(r'NanoJevGoverned[0-9]{2}',name):
        raise v3.V3Error('solidity_emit','Invalid Solidity contract name')
    audit=graph.verify(spec)
    if not audit['emission_authorized_by_graph_audit']:
        raise v3.V3Error('solidity_emit','Governance graph refused: '+str(audit['blockers']))
    p=spec['policy']
    selectors='\n'.join([_selector('milestoneAmount','amount_wei',spec),
                         _selector('milestoneDeadline','deadline_seconds',spec),
                         _selector('extensionDuration','extension_seconds',spec),
                         _selector('extensionDecision','extension_decision_seconds',spec),
                         _selector('extensionLimit','max_extensions',spec)])
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Experimental v3 governed progressive escrow. UNAUDITED. DO NOT DEPLOY.
contract {name} {{
    enum Status {{ Unfunded, Active, Submitted, Rejected, Disputed, Completed, Refunded }}
    Status public status;
    address payable public immutable payer;
    address payable public immutable worker;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant MILESTONES = {len(spec['milestones'])};
    uint256 public constant REVIEW_SECONDS = {p['review_seconds']};
    uint256 public constant FILING_SECONDS = {p['dispute_filing_seconds']};
    uint256 public constant APPOINTMENT_SECONDS = {p['appointment_seconds']};
    uint256 public constant RULING_SECONDS = {p['ruling_seconds']};
    uint256 public fundedAt;
    uint256 public currentMilestone;
    uint256 public dueAt;
    uint256 public extensionsGranted;
    uint256 public submittedAt;
    uint256 public rejectedAt;
    uint256 public disputedAt;
    address public nominatedArbitrator;
    address public arbitrator;
    uint256 public unallocated;
    mapping(address => uint256) public pendingWithdrawals;
    uint256 public totalPending;
    uint256 public totalWithdrawn;
    bool public complete;
    bool private withdrawalLock;
    event CreditAssigned(address indexed beneficiary, uint256 amount);
    event CreditWithdrawn(address indexed beneficiary, address indexed destination, uint256 amount);
    event MilestoneAdvanced(uint256 milestoneIndex);
    event ArbitratorNominated(address indexed candidate);
    event ArbitratorConfirmed(address indexed candidate);
    modifier notWithdrawing() {{ require(!withdrawalLock, "withdrawal callback"); _; }}
    constructor(address payable worker_) {{
        require(worker_ != address(0) && worker_ != msg.sender, "invalid worker");
        payer = payable(msg.sender);
        worker = worker_;
    }}
{selectors}
    function _conserve() internal view {{
        assert(unallocated + totalPending + totalWithdrawn == AMOUNT_WEI);
    }}
    function escrowConserved() external view returns (bool) {{
        if (status == Status.Unfunded)
            return unallocated == 0 && totalPending == 0 && totalWithdrawn == 0;
        return unallocated + totalPending + totalWithdrawn == AMOUNT_WEI;
    }}
    function fund() external payable notWithdrawing {{
        require(msg.sender == payer && status == Status.Unfunded, "payer/unfunded only");
        require(msg.value == AMOUNT_WEI, "wrong amount");
        fundedAt = block.timestamp;
        unallocated = msg.value;
        status = Status.Active;
        dueAt = block.timestamp + milestoneDeadline(0);
        _conserve();
    }}
    receive() external payable {{ revert("fund only"); }}
    fallback() external payable {{ revert("unknown action"); }}
    function _credit(address beneficiary, uint256 amount) internal {{
        require(amount > 0 && amount <= unallocated, "invalid credit");
        unallocated -= amount;
        pendingWithdrawals[beneficiary] += amount;
        totalPending += amount;
        emit CreditAssigned(beneficiary, amount);
    }}
    function _refund() internal {{
        _credit(payer, unallocated);
        status = Status.Refunded;
        complete = true;
        _conserve();
    }}
    function _award() internal {{
        _credit(worker, milestoneAmount(currentMilestone));
        if (currentMilestone + 1 == MILESTONES) {{
            require(unallocated == 0, "terminal residual");
            status = Status.Completed;
            complete = true;
        }} else {{
            currentMilestone += 1;
            extensionsGranted = 0;
            submittedAt = 0;
            rejectedAt = 0;
            disputedAt = 0;
            nominatedArbitrator = address(0);
            arbitrator = address(0);
            dueAt = block.timestamp + milestoneDeadline(currentMilestone);
            status = Status.Active;
            emit MilestoneAdvanced(currentMilestone);
        }}
        _conserve();
    }}
    function submit() external notWithdrawing {{
        require(status == Status.Active && msg.sender == worker, "active/worker only");
        require(block.timestamp <= dueAt, "deadline passed");
        status = Status.Submitted;
        submittedAt = block.timestamp;
    }}
    /// @notice Optional extension only in the bounded post-deadline decision window.
    function grantExtension() external notWithdrawing {{
        require(msg.sender == payer && status == Status.Active, "payer/active only");
        require(extensionsGranted < extensionLimit(currentMilestone), "extension limit");
        require(block.timestamp >= dueAt &&
                block.timestamp <= dueAt + extensionDecision(currentMilestone), "decision window");
        extensionsGranted += 1;
        dueAt += extensionDuration(currentMilestone);
    }}
    function resolveMissedDeadline() external notWithdrawing {{
        require(status == Status.Active, "not active");
        require(block.timestamp > dueAt + extensionDecision(currentMilestone), "decision open");
{_outcome(p['on_missed_submission'])}
    }}
    function approve() external notWithdrawing {{
        require(status == Status.Submitted && msg.sender == payer, "payer/submitted only");
        require(block.timestamp <= submittedAt + REVIEW_SECONDS, "review expired");
        _award();
    }}
    function reject() external notWithdrawing {{
        require(status == Status.Submitted && msg.sender == payer, "payer/submitted only");
        require(block.timestamp <= submittedAt + REVIEW_SECONDS, "review expired");
        rejectedAt = block.timestamp;
        status = Status.Rejected;
    }}
    function resolveReviewSilence() external notWithdrawing {{
        require(status == Status.Submitted && block.timestamp > submittedAt + REVIEW_SECONDS,
                "review still open");
{_outcome(p['on_review_silence'])}
    }}
    function fileDispute() external notWithdrawing {{
        require(status == Status.Rejected && msg.sender == worker, "worker/rejected only");
        require(block.timestamp <= rejectedAt + FILING_SECONDS, "filing expired");
        disputedAt = block.timestamp;
        nominatedArbitrator = address(0);
        arbitrator = address(0);
        status = Status.Disputed;
    }}
    function resolveUndisputed() external notWithdrawing {{
        require(status == Status.Rejected && block.timestamp > rejectedAt + FILING_SECONDS,
                "filing still open");
{_outcome(p['on_undisputed_rejection'])}
    }}
    function nominateArbitrator(address candidate) external notWithdrawing {{
        require(status == Status.Disputed && msg.sender == payer, "payer/disputed only");
        require(block.timestamp <= disputedAt + APPOINTMENT_SECONDS, "appointment expired");
        require(nominatedArbitrator == address(0), "already nominated");
        require(candidate != address(0) && candidate != payer && candidate != worker &&
                candidate != address(this), "invalid nominee");
        nominatedArbitrator = candidate;
        emit ArbitratorNominated(candidate);
    }}
    function confirmArbitrator(address candidate) external notWithdrawing {{
        require(status == Status.Disputed && msg.sender == worker, "worker/disputed only");
        require(block.timestamp <= disputedAt + APPOINTMENT_SECONDS, "appointment expired");
        require(nominatedArbitrator != address(0) && candidate == nominatedArbitrator,
                "candidate mismatch");
        require(arbitrator == address(0), "already confirmed");
        arbitrator = candidate;
        emit ArbitratorConfirmed(candidate);
    }}
    function awardWorker() external notWithdrawing {{
        require(status == Status.Disputed && arbitrator != address(0) &&
                msg.sender == arbitrator, "appointed arbitrator only");
        require(block.timestamp <= disputedAt + RULING_SECONDS, "ruling expired");
        _award();
    }}
    function awardPayer() external notWithdrawing {{
        require(status == Status.Disputed && arbitrator != address(0) &&
                msg.sender == arbitrator, "appointed arbitrator only");
        require(block.timestamp <= disputedAt + RULING_SECONDS, "ruling expired");
        _refund();
    }}
    function resolveUnappointed() external notWithdrawing {{
        require(status == Status.Disputed && arbitrator == address(0), "appointed/already resolved");
        require(block.timestamp > disputedAt + APPOINTMENT_SECONDS, "appointment open");
{_outcome(p['on_appointment_failure'])}
    }}
    function resolveArbitratorSilence() external notWithdrawing {{
        require(status == Status.Disputed && arbitrator != address(0), "not appointed");
        require(block.timestamp > disputedAt + RULING_SECONDS, "ruling open");
{_outcome(p['on_arbitrator_silence'])}
    }}
    /// @notice Independent pull withdrawals; recipient failure reverts only their call.
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
        require(ok, "receiver rejected");
        _conserve();
        withdrawalLock = false;
        emit CreditWithdrawn(msg.sender, destination, amount);
    }}
    function withdraw() external {{ withdrawTo(payable(msg.sender)); }}
}}
'''
