"""Generic Solidity-based adversarial tests for governed progressive escrows.

Executable behavior comparisons use hand-specified trace expectations drawn
from the governance spec, plus authorization, timing and denial tests. These
are not independent external contract specifications or security certification.
"""
from __future__ import annotations
from tools import nanojev_contract_v3_governance_language as lang
from tools import nanojev_contract_v3_governance_verifier as verifier


def tests(raw,name='NanoJevGoverned01'):
    s=lang.validate(raw)
    audit=verifier.verify(s)
    if not audit['emission_authorized_by_graph_audit']:
        raise ValueError('Cannot generate Forge tests for refused governed IR')
    policies=s['policy'];milestones=s['milestones']
    funcs=[]
    def add(label,*lines):
        funcs.append('    function test'+label+'() public {\n'+'\n'.join('        '+line for line in lines)+'\n    }')
    def funded():return [f'{name} c = fundedEscrow();']
    def dispute():
        return [*funded(),'vm.prank(WORKER);','c.submit();','c.reject();',
                'vm.prank(WORKER);','c.fileDispute();']
    def nominated():
        return [*dispute(),'c.nominateArbitrator(ARBITRATOR);',
                'vm.prank(WORKER);','c.confirmArbitrator(ARBITRATOR);']
    add('ExactFunding',*funded(),
        'require(c.unallocated() == AMOUNT, "funded amount");',
        'require(c.escrowConserved(), "conservation");')
    add('NoDoubleFunding',*funded(),
        '(bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        'require(!ok, "double funding accepted");')
    add('NoOutsiderFunding',f'{name} c = fresh();',
        'vm.deal(STRANGER, AMOUNT);','vm.prank(STRANGER);',
        '(bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        'require(!ok, "outsider funded");')
    add('NoWrongFunding',f'{name} c = fresh();',
        'vm.deal(address(this), AMOUNT);',
        '(bool ok,) = address(c).call{value: AMOUNT - 1}(abi.encodeWithSignature("fund()"));',
        'require(!ok, "wrong funding accepted");')
    add('CannotApproveBeforeSubmission',*funded(),
        '(bool ok,) = address(c).call(abi.encodeWithSignature("approve()"));',
        'require(!ok, "approval without submission");')
    add('WorkerOnlySubmission',*funded(),
        '(bool ok,) = address(c).call(abi.encodeWithSignature("submit()"));',
        'require(!ok, "payer submitted");',
        'vm.prank(WORKER);','c.submit();',
        'require(uint256(c.status()) == 2, "not submitted");')
    add('NoWorkerApproval',*funded(),'vm.prank(WORKER);','c.submit();',
        'vm.prank(WORKER);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("approve()"));',
        'require(!ok, "worker approved");')
    add('ThreeStageProgressiveOrGeneric',*funded(),
        'uint256 expectedWorker = 0;',
        'for (uint256 i = 0; i < c.MILESTONES(); ++i) {',
        '    vm.prank(WORKER);','    c.submit();',
        '    uint256 portion = c.milestoneAmount(i);',
        '    c.approve();','    expectedWorker += portion;',
        '    require(c.pendingWithdrawals(WORKER) == expectedWorker, "credits mismatch");',
        '    require(c.unallocated() == AMOUNT - expectedWorker, "remaining escrow");',
        '    require(c.escrowConserved(), "lost escrow");',
        '}',
        'require(c.complete() && uint256(c.status()) == 5, "not completed");')
    add('IndependentWithdrawalAfterMilestone',*funded(),
        'vm.prank(WORKER);','c.submit();','c.approve();',
        'uint256 credit = c.pendingWithdrawals(WORKER);',
        'vm.prank(WORKER);','c.withdrawTo(payable(RECEIVER));',
        'require(c.totalWithdrawn() == credit, "withdrawn tally");',
        'require(c.pendingWithdrawals(WORKER) == 0, "credit still pending");',
        'require(c.escrowConserved(), "withdrawal conservation");')
    add('CannotWithdrawAnotherBeneficiary',*funded(),
        'vm.prank(WORKER);','c.submit();','c.approve();',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("withdraw()"));',
        'require(!ok, "payer withdrew worker balance");')
    add('PrematureMissedDeadlineRefundDenied',*funded(),
        '(bool ok,) = address(c).call(abi.encodeWithSignature("resolveMissedDeadline()"));',
        'require(!ok, "early refund");')
    add('MissedDeadlineRefundIsPermissionless',*funded(),
        'vm.warp(c.dueAt() + c.extensionDecision(0) + 1);',
        'vm.prank(STRANGER);','c.resolveMissedDeadline();',
        'require(c.pendingWithdrawals(address(this)) == AMOUNT, "wrong refund");',
        'require(c.complete() && c.unallocated() == 0, "not closed");')
    add('SubmissionAfterDeadlineDenied',*funded(),
        'vm.warp(c.dueAt() + 1);','vm.prank(WORKER);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("submit()"));',
        'require(!ok, "late work submitted");')
    add('ExplicitRejectionAndDispute',*dispute(),
        'require(uint256(c.status()) == 4, "not disputed");',
        'require(c.unallocated() == AMOUNT, "premature allocation");')
    add('NoUnapprovedArbitration',*dispute(),
        'vm.prank(ARBITRATOR);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("awardWorker()"));',
        'require(!ok, "unappointed arbitrator ruled");')
    add('MutualAppointmentAndWorkerAward',*nominated(),
        'require(c.arbitrator() == ARBITRATOR, "not jointly appointed");',
        'vm.prank(ARBITRATOR);','c.awardWorker();',
        'require(c.pendingWithdrawals(WORKER) == c.milestoneAmount(0), "wrong award");',
        'require(c.escrowConserved(), "arb award conservation");')
    add('MutualAppointmentAndPayerAward',*nominated(),
        'vm.prank(ARBITRATOR);','c.awardPayer();',
        'require(c.pendingWithdrawals(address(this)) == AMOUNT, "wrong ruling refund");',
        'require(c.complete() && c.unallocated() == 0, "not settled");')
    add('OutsiderCannotNominate',*dispute(),
        'vm.prank(STRANGER);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("nominateArbitrator(address)", ARBITRATOR));',
        'require(!ok, "outsider nominated");')
    add('WrongNomineeCannotBeConfirmed',*dispute(),
        'c.nominateArbitrator(ARBITRATOR);',
        'vm.prank(WORKER);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("confirmArbitrator(address)", STRANGER));',
        'require(!ok, "wrong nominee confirmed");')
    add('CannotDoubleNominate',*dispute(),
        'c.nominateArbitrator(ARBITRATOR);',
        '(bool ok,) = address(c).call(abi.encodeWithSignature("nominateArbitrator(address)", STRANGER));',
        'require(!ok, "second nomination");')
    add('PayerCannotRuleAsArbitrator',*nominated(),
        '(bool ok,) = address(c).call(abi.encodeWithSignature("awardWorker()"));',
        'require(!ok, "payer ruled");')
    add('UnappointedTimeoutPermissionless',*dispute(),
        'vm.warp(c.disputedAt() + c.APPOINTMENT_SECONDS() + 1);',
        'vm.prank(STRANGER);','c.resolveUnappointed();',
        f'require(c.pendingWithdrawals({"WORKER" if policies["on_appointment_failure"]=="pay_worker" else "address(this)"}) == '
        f'{"c.milestoneAmount(0)" if policies["on_appointment_failure"]=="pay_worker" else "AMOUNT"}, "appointment fallback");')
    add('AppointedArbitratorSilentTimeout',*nominated(),
        'vm.warp(c.disputedAt() + c.RULING_SECONDS() + 1);',
        'vm.prank(STRANGER);','c.resolveArbitratorSilence();',
        f'require(c.pendingWithdrawals({"WORKER" if policies["on_arbitrator_silence"]=="pay_worker" else "address(this)"}) == '
        f'{"c.milestoneAmount(0)" if policies["on_arbitrator_silence"]=="pay_worker" else "AMOUNT"}, "arbitrator fallback");')
    add('NoEarlyArbitratorTimeout',*nominated(),
        '(bool ok,) = address(c).call(abi.encodeWithSignature("resolveArbitratorSilence()"));',
        'require(!ok, "premature fallback");')
    add('RejectedNoDisputeTimesOut',*funded(),
        'vm.prank(WORKER);','c.submit();','c.reject();',
        'vm.warp(c.rejectedAt() + c.FILING_SECONDS() + 1);',
        'vm.prank(STRANGER);','c.resolveUndisputed();',
        'require(c.pendingWithdrawals(address(this)) == AMOUNT, "missing rejection refund");')
    add('PayerSilentReviewResolves',*funded(),
        'vm.prank(WORKER);','c.submit();',
        'vm.warp(c.submittedAt() + c.REVIEW_SECONDS() + 1);',
        'vm.prank(STRANGER);','c.resolveReviewSilence();',
        f'require(c.pendingWithdrawals({"WORKER" if policies["on_review_silence"]=="pay_worker" else "address(this)"}) == '
        f'{"c.milestoneAmount(0)" if policies["on_review_silence"]=="pay_worker" else "AMOUNT"}, "review silence fallback");')
    if milestones[0]['max_extensions']:
        add('BoundedExtensionAfterMiss',*funded(),
            'uint256 originalDue = c.dueAt();','vm.warp(originalDue);',
            'c.grantExtension();',
            'require(c.dueAt() == originalDue + c.extensionDuration(0), "wrong extension");',
            'vm.prank(WORKER);','c.submit();',
            'c.approve();',
            'require(c.pendingWithdrawals(WORKER) == c.milestoneAmount(0), "extended completion");')
        if milestones[0]['max_extensions']==1:
            add('ExtensionLimitEnforced',*funded(),
                'vm.warp(c.dueAt());','c.grantExtension();',
                'vm.warp(c.dueAt());',
                '(bool ok,) = address(c).call(abi.encodeWithSignature("grantExtension()"));',
                'require(!ok, "extra extension");')
    add('RefundAfterEarnedCreditPreservesCredit',*funded(),
        'vm.prank(WORKER);','c.submit();','c.approve();',
        'uint256 earned = c.milestoneAmount(0);',
        'if (c.MILESTONES() > 1) {',
        '    vm.warp(c.dueAt() + c.extensionDecision(1) + 1);',
        '    c.resolveMissedDeadline();',
        '    require(c.pendingWithdrawals(WORKER) == earned, "earned credit changed");',
        '    require(c.pendingWithdrawals(address(this)) == AMOUNT - earned, "residual refund");',
        '    require(c.escrowConserved(), "conservation broken");',
        '}')
    add('WithdrawalAccounting',*funded(),
        'vm.prank(WORKER);','c.submit();','c.approve();',
        'vm.prank(WORKER);','c.withdrawTo(payable(RECEIVER));',
        'require(c.totalPending() + c.totalWithdrawn() + c.unallocated() == AMOUNT, "sum mismatch");')
    funcs.append('''    function testFuzzWithdrawOrder(uint8 selector) public {
        NanoJevGoverned01 c = fundedEscrow();
        vm.prank(WORKER);
        c.submit();
        c.approve();
        if (selector % 2 == 0) {
            vm.prank(WORKER);
            c.withdrawTo(payable(RECEIVER));
        }
        if (c.MILESTONES() > 1) {
            vm.warp(c.dueAt() + c.extensionDecision(1) + 1);
            c.resolveMissedDeadline();
        }
        if (selector % 2 != 0) {
            vm.prank(WORKER);
            c.withdrawTo(payable(RECEIVER));
        }
        require(c.totalPending() + c.totalWithdrawn() + c.unallocated() == AMOUNT, "fuzz conservation");
    }''')
    header=f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface Vm {{
    function prank(address) external;
    function deal(address,uint256) external;
    function warp(uint256) external;
}}
contract {name}GeneratedTest {{
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address private constant WORKER = address(0xBEEF);
    address private constant ARBITRATOR = address(0xA11CE);
    address private constant STRANGER = address(0xCAFE);
    address private constant RECEIVER = address(0xF00D);
    uint256 private constant AMOUNT = {s['amount_wei']};
    function fresh() internal returns ({name} c) {{
        c = new {name}(payable(WORKER));
    }}
    function fundedEscrow() internal returns ({name} c) {{
        c = fresh();
        vm.deal(address(this), AMOUNT * 4);
        c.fund{{value: AMOUNT}}();
    }}
'''
    return header+'\n'.join(funcs)+'\n    receive() external payable {}\n}\n',len(funcs)
