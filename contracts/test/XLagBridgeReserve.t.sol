// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/XLagBridgeReserve.sol";

contract XLagActor {
    receive() external payable {}

    function proposePayout(XLagBridgeReserve reserve, address recipient, uint256 amountWei, string calldata memo, uint64 expiresBlock)
        external
        returns (uint256)
    {
        return reserve.proposePayout(recipient, amountWei, memo, expiresBlock);
    }

    function secondPayout(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.secondPayout(proposalId);
    }

    function belayPayout(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.belayPayout(proposalId);
    }

    function contestProposal(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.contestProposal(proposalId);
    }

    function executePayout(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.executePayout(proposalId);
    }

    function proposeOfficeReset(XLagBridgeReserve reserve, uint8 targetOffice, address newAddress, string calldata reason, uint64 expiresBlock)
        external
        returns (uint256)
    {
        return reserve.proposeOfficeReset(targetOffice, newAddress, reason, expiresBlock);
    }

    function approveOfficeReset(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.approveOfficeReset(proposalId);
    }

    function executeOfficeReset(XLagBridgeReserve reserve, uint256 proposalId) external {
        reserve.executeOfficeReset(proposalId);
    }

    function finalizeWalletSmokeTest(XLagBridgeReserve reserve, bytes32 smokeId, string calldata memo) external returns (uint256) {
        return reserve.finalizeWalletSmokeTest(smokeId, memo);
    }

    function frobByAnyUser(XLagBridgeReserve reserve, bytes32 frobId, string calldata memo) external returns (uint256) {
        return reserve.frobByAnyUser(frobId, memo);
    }
}

contract ForceSend {
    constructor() payable {}

    function push(address target) external {
        selfdestruct(payable(target));
    }
}

contract XLagBridgeReserveTest {
    XLagActor private o0 = new XLagActor();
    XLagActor private o1 = new XLagActor();
    XLagActor private o2 = new XLagActor();
    XLagActor private o3 = new XLagActor();
    XLagActor private outsider = new XLagActor();
    XLagActor private recipient = new XLagActor();
    XLagActor private replacement = new XLagActor();

    function testConstructorRejectsZeroOfficeAddress() public {
        address[4] memory offices = [address(o0), address(0), address(o2), address(o3)];
        try new XLagBridgeReserve(offices, 1 ether, 0, 0) {
            revert("expected zero rejection");
        } catch {}
    }

    function testConstructorRejectsDuplicateOfficeAddress() public {
        address[4] memory offices = [address(o0), address(o0), address(o2), address(o3)];
        try new XLagBridgeReserve(offices, 1 ether, 0, 0) {
            revert("expected duplicate rejection");
        } catch {}
    }

    function testCaptainCanProposePayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = propose(reserve, 1 wei);
        XLagBridgeReserve.PayoutProposal memory proposal = reserve.getPayoutProposal(id);
        assertEq(proposal.captain, address(o0));
        assertEq(uint256(proposal.state), uint256(XLagBridgeReserve.ProposalState.PENDING));
    }

    function testNonCaptainCannotProposePayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try o1.proposePayout(reserve, address(recipient), 1 wei, "bad", uint64(block.number + 100)) {
            revert("expected captain rejection");
        } catch {}
    }

    function testO2CanSecondPayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = propose(reserve, 1 wei);
        o2.secondPayout(reserve, id);
        XLagBridgeReserve.PayoutProposal memory proposal = reserve.getPayoutProposal(id);
        assertEq(proposal.secondedBy, address(o2));
    }

    function testO3CanSecondPayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = propose(reserve, 1 wei);
        o3.secondPayout(reserve, id);
        XLagBridgeReserve.PayoutProposal memory proposal = reserve.getPayoutProposal(id);
        assertEq(proposal.secondedBy, address(o3));
    }

    function testO1CanBelayPayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = propose(reserve, 1 wei);
        o1.belayPayout(reserve, id);
        assertEq(uint256(reserve.proposalState(id)), uint256(XLagBridgeReserve.ProposalState.BELAYED));
    }

    function testBelayedPayoutCannotExecute() public {
        XLagBridgeReserve reserve = fundedFresh(0, 0, 1 ether);
        uint256 id = propose(reserve, 1 wei);
        o2.secondPayout(reserve, id);
        o1.belayPayout(reserve, id);
        try outsider.executePayout(reserve, id) {
            revert("expected belay rejection");
        } catch {}
    }

    function testAnyOfficeCanContestPayout() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = propose(reserve, 1 wei);
        o3.contestProposal(reserve, id);
        assertEq(uint256(reserve.proposalState(id)), uint256(XLagBridgeReserve.ProposalState.CONTESTED));
    }

    function testContestedPayoutCannotExecute() public {
        XLagBridgeReserve reserve = fundedFresh(0, 0, 1 ether);
        uint256 id = propose(reserve, 1 wei);
        o2.secondPayout(reserve, id);
        o3.contestProposal(reserve, id);
        try outsider.executePayout(reserve, id) {
            revert("expected contest rejection");
        } catch {}
    }

    function testPayoutCannotExecuteWithoutBetaSecond() public {
        XLagBridgeReserve reserve = fundedFresh(0, 0, 1 ether);
        uint256 id = propose(reserve, 1 wei);
        try outsider.executePayout(reserve, id) {
            revert("expected second rejection");
        } catch {}
    }

    function testPayoutCannotExecuteBeforeDelay() public {
        XLagBridgeReserve reserve = fundedFresh(100, 0, 1 ether);
        uint256 id = propose(reserve, 1 wei);
        o2.secondPayout(reserve, id);
        try outsider.executePayout(reserve, id) {
            revert("expected delay rejection");
        } catch {}
    }

    function testPayoutExecutesAfterCaptainIntentBetaSecondDelayNoBlocks() public {
        XLagBridgeReserve reserve = fundedFresh(0, 0, 1 ether);
        uint256 id = propose(reserve, 100 wei);
        o2.secondPayout(reserve, id);
        uint256 beforeBalance = address(recipient).balance;
        outsider.executePayout(reserve, id);
        assertEq(uint256(reserve.proposalState(id)), uint256(XLagBridgeReserve.ProposalState.EXECUTED));
        assertEq(address(recipient).balance, beforeBalance + 100 wei);
    }

    function testPayoutAmountCannotExceedMaxPayoutWei() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try o0.proposePayout(reserve, address(recipient), 2 ether, "too much", uint64(block.number + 100)) {
            revert("expected max rejection");
        } catch {}
    }

    function testAnyOfficeCanProposeOfficeReset() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        XLagBridgeReserve.OfficeResetProposal memory proposal = reserve.getOfficeResetProposal(id);
        assertEq(proposal.approvalCount, 1);
        assertEq(proposal.newAddress, address(replacement));
    }

    function testResetRequiresThreeDistinctOfficeApprovals() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        try outsider.executeOfficeReset(reserve, id) {
            revert("expected approval rejection");
        } catch {}
    }

    function testDuplicateApprovalDoesNotIncreaseCount() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o1.approveOfficeReset(reserve, id);
        XLagBridgeReserve.OfficeResetProposal memory proposal = reserve.getOfficeResetProposal(id);
        assertEq(proposal.approvalCount, 2);
    }

    function testResetCannotExecuteBeforeDelay() public {
        XLagBridgeReserve reserve = fresh(0, 100);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o2.approveOfficeReset(reserve, id);
        try outsider.executeOfficeReset(reserve, id) {
            revert("expected delay rejection");
        } catch {}
    }

    function testResetCannotExecuteIfContested() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o2.approveOfficeReset(reserve, id);
        o3.contestProposal(reserve, id);
        try outsider.executeOfficeReset(reserve, id) {
            revert("expected contest rejection");
        } catch {}
    }

    function testResetUpdatesTargetOfficeAddressAfterThreeApprovalsAndDelay() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o2.approveOfficeReset(reserve, id);
        outsider.executeOfficeReset(reserve, id);
        assertEq(reserve.getOffice(0), address(replacement));
    }

    function testResetRejectsDuplicateNewAddress() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try o0.proposeOfficeReset(reserve, 0, address(o1), "duplicate", uint64(block.number + 100)) {
            revert("expected duplicate new address rejection");
        } catch {}
    }

    function testOldAddressNoLongerCountsAsOfficeAfterReset() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o2.approveOfficeReset(reserve, id);
        outsider.executeOfficeReset(reserve, id);
        assertTrue(!reserve.isOffice(address(o0)));
    }

    function testNewAddressCountsAsOfficeAfterReset() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        uint256 id = proposeReset(reserve, address(replacement));
        o1.approveOfficeReset(reserve, id);
        o2.approveOfficeReset(reserve, id);
        outsider.executeOfficeReset(reserve, id);
        assertTrue(reserve.isOffice(address(replacement)));
    }

    function testOfficeCanFinalizeWalletSmokeTestWithSingleSignature() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        bytes32 smokeId = keccak256(bytes("unit wallet smoke"));
        uint256 nonce = o0.finalizeWalletSmokeTest(reserve, smokeId, "single signer smoke");

        assertEq(nonce, 1);
        assertEq(reserve.walletSmokeNonce(), 1);
        assertEq(reserve.lastWalletSmokeFinalizer(), address(o0));
        assertEq(uint256(reserve.lastWalletSmokeOffice()), uint256(0));
        assertEq(reserve.lastWalletSmokeId(), smokeId);
        require(
            keccak256(bytes(reserve.lastWalletSmokeMemo())) == keccak256(bytes("single signer smoke")),
            "memo mismatch"
        );
        assertEq(reserve.lastWalletSmokeBlock(), block.number);
    }

    function testNonOfficeCannotFinalizeWalletSmokeTest() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try outsider.finalizeWalletSmokeTest(reserve, keccak256(bytes("outsider smoke")), "bad smoke") {
            revert("expected office rejection");
        } catch {}
    }

    function testWalletSmokeRejectsZeroSmokeId() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try o0.finalizeWalletSmokeTest(reserve, bytes32(0), "bad smoke") {
            revert("expected zero smoke id rejection");
        } catch {}
    }

    function testAnyUserCanFrobWithoutOfficeRole() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        bytes32 frobId = keccak256(bytes("unit any-user frob"));
        uint256 nonce = outsider.frobByAnyUser(reserve, frobId, "permissionless browser frob");

        assertEq(nonce, 1);
        assertEq(reserve.frobNonce(), 1);
        assertEq(reserve.lastFrobber(), address(outsider));
        assertEq(reserve.lastFrobId(), frobId);
        require(
            keccak256(bytes(reserve.lastFrobMemo())) == keccak256(bytes("permissionless browser frob")),
            "frob memo mismatch"
        );
        assertEq(reserve.lastFrobBlock(), block.number);
    }

    function testAnyUserFrobRejectsZeroFrobId() public {
        XLagBridgeReserve reserve = fresh(0, 0);
        try outsider.frobByAnyUser(reserve, bytes32(0), "bad frob") {
            revert("expected zero frob id rejection");
        } catch {}
    }

    function fresh(uint64 payoutDelayBlocks, uint64 resetDelayBlocks) private returns (XLagBridgeReserve) {
        address[4] memory offices = [address(o0), address(o1), address(o2), address(o3)];
        return new XLagBridgeReserve(offices, 1 ether, payoutDelayBlocks, resetDelayBlocks);
    }

    function fundedFresh(uint64 payoutDelayBlocks, uint64 resetDelayBlocks, uint256 amount) private returns (XLagBridgeReserve) {
        XLagBridgeReserve reserve = fresh(payoutDelayBlocks, resetDelayBlocks);
        ForceSend forceSend = new ForceSend{value: amount}();
        forceSend.push(address(reserve));
        return reserve;
    }

    function propose(XLagBridgeReserve reserve, uint256 amountWei) private returns (uint256) {
        return o0.proposePayout(reserve, address(recipient), amountWei, "payout", uint64(block.number + 100));
    }

    function proposeReset(XLagBridgeReserve reserve, address newAddress) private returns (uint256) {
        return o0.proposeOfficeReset(reserve, 0, newAddress, "rotate captain", uint64(block.number + 100));
    }

    function assertEq(address left, address right) private pure {
        require(left == right, "address mismatch");
    }

    function assertEq(uint256 left, uint256 right) private pure {
        require(left == right, "uint mismatch");
    }

    function assertEq(bytes32 left, bytes32 right) private pure {
        require(left == right, "bytes32 mismatch");
    }

    function assertTrue(bool value) private pure {
        require(value, "assert true failed");
    }
}
