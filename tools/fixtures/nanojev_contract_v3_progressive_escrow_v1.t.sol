// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/NanoJevProgressive01.sol";

interface VmProgressive {
    function deal(address who, uint256 amount) external;
    function prank(address who) external;
    function warp(uint256 timestamp) external;
}
contract RejectProgressiveCredit {
    receive() external payable { revert("reject transfer"); }
}
contract ReenterProgressiveCredit {
    NanoJevProgressive01 public escrow;
    bool public attempted;
    bool public blocked;
    function bind(NanoJevProgressive01 c) external { escrow = c; }
    function claim() external { escrow.withdrawTo(payable(address(this))); }
    receive() external payable {
        attempted = true;
        (bool ok,) = address(escrow).call(abi.encodeWithSignature("withdraw()"));
        blocked = !ok;
    }
}
contract NanoJevProgressive01SmokeTest {
    VmProgressive internal constant vm = VmProgressive(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant DEPOSIT = 12 ether;
    uint256 internal constant DEADLINE = 14 days;
    address payable internal constant WORKER = payable(address(0xBEEF));
    address payable internal constant REVIEWER = payable(address(0xCAFE));
    address payable internal constant OUTSIDER = payable(address(0xD00D));

    function setUp() public { vm.deal(address(this), 100 ether); }
    function fresh() internal returns (NanoJevProgressive01 c) {
        c = new NanoJevProgressive01(WORKER, REVIEWER);
    }
    function funded() internal returns (NanoJevProgressive01 c) {
        c = fresh();
        c.fund{value: DEPOSIT}();
    }
    function assertLedger(NanoJevProgressive01 c) internal view {
        require(c.escrowConserved(), "conservation flag");
        require(c.unallocated() + c.totalPending() + c.totalWithdrawn() == DEPOSIT, "not conserved");
        require(address(c).balance >= c.unallocated() + c.totalPending(), "insufficient ETH custody");
    }
    function testFundingStartsUnallocated() public {
        NanoJevProgressive01 c = funded();
        require(c.unallocated() == DEPOSIT, "unallocated");
        require(c.totalPending() == 0 && c.totalWithdrawn() == 0, "already allocated");
        require(!c.complete(), "completed too early");
        assertLedger(c);
    }
    function testThreeProgressiveAllocations() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        require(c.unallocated() == 9 ether && c.pendingWithdrawals(WORKER) == 3 ether, "prototype");
        require(!c.complete(), "closed after prototype");
        assertLedger(c);
        c.approve_beta();
        require(c.unallocated() == 5 ether && c.pendingWithdrawals(WORKER) == 7 ether, "beta");
        assertLedger(c);
        c.approve_final();
        require(c.unallocated() == 0 && c.pendingWithdrawals(WORKER) == DEPOSIT, "final");
        require(c.complete(), "not closed");
        assertLedger(c);
    }
    function testWithdrawalDoesNotAdvanceMilestone() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        uint256 prior = OUTSIDER.balance;
        vm.prank(WORKER);
        c.withdrawTo(OUTSIDER);
        require(OUTSIDER.balance == prior + 3 ether, "withdrawn wrong amount");
        require(c.unallocated() == 9 ether && c.totalWithdrawn() == 3 ether, "ledger");
        require(c.totalPending() == 0 && !c.complete(), "accidental finalization");
        c.approve_beta();
        require(c.pendingWithdrawals(WORKER) == 4 ether, "second credit missing");
        assertLedger(c);
    }
    function testRefundRemainingAfterPrototype() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank(WORKER);
        c.refund_prototype_worker();
        require(c.complete() && c.unallocated() == 0, "not settled");
        require(c.pendingWithdrawals(address(this)) == 9 ether, "wrong payer refund");
        require(c.pendingWithdrawals(WORKER) == 3 ether, "earned funds confiscated");
        assertLedger(c);
    }
    function testRefundRemainingAfterBeta() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        c.approve_beta();
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank(WORKER);
        c.refund_beta_worker();
        require(c.pendingWithdrawals(address(this)) == 5 ether, "wrong refund");
        require(c.pendingWithdrawals(WORKER) == 7 ether, "earned credits changed");
        require(c.complete() && c.unallocated() == 0, "unresolved balance");
        assertLedger(c);
    }
    function testPayerCanRefundWithoutWorker() public {
        NanoJevProgressive01 c = funded();
        vm.warp(c.fundedAt() + DEADLINE);
        c.refund_funded_payer();
        require(c.pendingWithdrawals(address(this)) == DEPOSIT, "refund missed");
        assertLedger(c);
    }
    function testWorkerCanRefundWithoutPayer() public {
        NanoJevProgressive01 c = funded();
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank(WORKER);
        c.refund_funded_worker();
        require(c.pendingWithdrawals(address(this)) == DEPOSIT, "refund missed");
        assertLedger(c);
    }
    function testNoDoubleAllocation() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        (bool ok,) = address(c).call(abi.encodeWithSignature("approve_prototype()"));
        require(!ok, "double allocation succeeded");
        require(c.pendingWithdrawals(WORKER) == 3 ether, "double credit");
        assertLedger(c);
    }
    function testNoPostCompletionAllocation() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype(); c.approve_beta(); c.approve_final();
        (bool ok,) = address(c).call(abi.encodeWithSignature("refund_beta_payer()"));
        require(!ok, "refund after completion");
        assertLedger(c);
    }
    function testAuthorizationCannotBeBypassed() public {
        NanoJevProgressive01 c = funded();
        vm.prank(WORKER);
        (bool ok,) = address(c).call(abi.encodeWithSignature("approve_prototype()"));
        require(!ok, "worker approved own work");
        vm.prank(OUTSIDER);
        (ok,) = address(c).call(abi.encodeWithSignature("approve_prototype()"));
        require(!ok, "outsider approved");
        c.approve_prototype();
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank(OUTSIDER);
        (ok,) = address(c).call(abi.encodeWithSignature("refund_prototype_payer()"));
        require(!ok, "outsider refunded");
        assertLedger(c);
    }
    function testWrongTimeGuards() public {
        NanoJevProgressive01 c = funded();
        (bool ok,) = address(c).call(abi.encodeWithSignature("refund_funded_payer()"));
        require(!ok, "early refund");
        vm.warp(c.fundedAt() + DEADLINE);
        (ok,) = address(c).call(abi.encodeWithSignature("approve_prototype()"));
        require(!ok, "late prototype approval");
        assertLedger(c);
    }
    function testCannotFundTwiceOrWrongAmount() public {
        NanoJevProgressive01 c = fresh();
        (bool ok,) = address(c).call{value: 11 ether}(abi.encodeWithSignature("fund()"));
        require(!ok, "wrong deposit");
        c.fund{value: DEPOSIT}();
        (ok,) = address(c).call{value: DEPOSIT}(abi.encodeWithSignature("fund()"));
        require(!ok, "duplicate deposit");
        assertLedger(c);
    }
    function testUnfundedAndUnauthorizedFunding() public {
        NanoJevProgressive01 c = fresh();
        (bool ok,) = address(c).call(abi.encodeWithSignature("approve_prototype()"));
        require(!ok, "unfunded action");
        vm.deal(OUTSIDER, DEPOSIT);
        vm.prank(OUTSIDER);
        (ok,) = address(c).call{value: DEPOSIT}(abi.encodeWithSignature("fund()"));
        require(!ok, "unauthorized funding");
    }
    function testRejectDirectEtherTransfer() public {
        NanoJevProgressive01 c = funded();
        (bool ok,) = address(c).call{value: 1 ether}("");
        require(!ok, "unexpected direct ETH accepted");
        assertLedger(c);
    }
    function testZeroCreditCannotWithdraw() public {
        NanoJevProgressive01 c = funded();
        vm.prank(WORKER);
        (bool ok,) = address(c).call(abi.encodeWithSignature("withdraw()"));
        require(!ok, "zero credit withdrawn");
        assertLedger(c);
    }
    function testRejectingDestinationDoesNotConsumeCredit() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        RejectProgressiveCredit rejector = new RejectProgressiveCredit();
        vm.prank(WORKER);
        (bool ok,) = address(c).call(abi.encodeWithSignature("withdrawTo(address)", address(rejector)));
        require(!ok, "rejecting receiver accepted transfer");
        require(c.pendingWithdrawals(WORKER) == 3 ether && c.totalWithdrawn() == 0, "lost funds");
        // Withdrawal failure cannot block later obligations or another payee.
        c.approve_beta();
        vm.warp(c.fundedAt() + DEADLINE);
        c.refund_beta_payer();
        require(c.pendingWithdrawals(WORKER) == 7 ether, "worker credit was lost");
        require(c.pendingWithdrawals(address(this)) == 5 ether, "payer refund was blocked");
        assertLedger(c);
    }
    function testOtherRoleCannotWithdrawWorkerCredit() public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        vm.prank(OUTSIDER);
        (bool ok,) = address(c).call(abi.encodeWithSignature("withdrawTo(address)", address(OUTSIDER)));
        require(!ok, "stole worker funds");
        require(c.pendingWithdrawals(WORKER) == 3 ether, "credit stolen");
        assertLedger(c);
    }
    function testReentrancyCannotDoubleWithdraw() public {
        ReenterProgressiveCredit receiver = new ReenterProgressiveCredit();
        NanoJevProgressive01 c = new NanoJevProgressive01(payable(address(receiver)), REVIEWER);
        receiver.bind(c);
        c.fund{value: DEPOSIT}();
        c.approve_prototype();
        receiver.claim();
        require(receiver.attempted() && receiver.blocked(), "reentrancy not rejected");
        require(address(receiver).balance == 3 ether, "double payment");
        require(c.totalWithdrawn() == 3 ether && c.pendingWithdrawals(address(receiver)) == 0, "ledger");
        assertLedger(c);
    }
    function testFuzzWithdrawalOrderAndConservation(uint64 seed) public {
        NanoJevProgressive01 c = funded();
        c.approve_prototype();
        if (seed % 2 == 0) {
            vm.prank(WORKER);
            c.withdrawTo(OUTSIDER);
        }
        c.approve_beta();
        if (seed % 3 == 0 && c.pendingWithdrawals(WORKER) > 0) {
            vm.prank(WORKER);
            c.withdrawTo(OUTSIDER);
        }
        if (seed % 5 == 0) {
            c.approve_final();
        } else {
            vm.warp(c.fundedAt() + DEADLINE);
            c.refund_beta_payer();
        }
        assertLedger(c);
        require(c.unallocated() == 0 && c.complete(), "funds not settled");
        if (c.pendingWithdrawals(WORKER) > 0) {
            vm.prank(WORKER);
            c.withdrawTo(OUTSIDER);
        }
        if (c.pendingWithdrawals(address(this)) > 0) c.withdrawTo(payable(address(this)));
        assertLedger(c);
        require(c.totalPending() == 0 && c.totalWithdrawn() == DEPOSIT, "withdrawal final mismatch");
    }
    receive() external payable {}
}
