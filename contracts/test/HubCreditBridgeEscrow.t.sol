// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/HubCreditBridgeEscrow.sol";

contract EscrowActor {
    constructor() payable {}

    receive() external payable {}

    function depositFor(HubCreditBridgeEscrow escrow, address account, uint256 amountUnits, bytes32 depositId, string calldata memo)
        external
        payable
        returns (bool)
    {
        return escrow.depositFor{value: msg.value}(account, amountUnits, depositId, memo);
    }

    function rectifySpend(HubCreditBridgeEscrow escrow, address account, uint256 amountUnits, bytes32 rectificationId, string calldata memo)
        external
        returns (bool)
    {
        return escrow.rectifySpend(account, amountUnits, rectificationId, memo);
    }

    function completeDeposit(HubCreditBridgeEscrow escrow, bytes32 depositId) external returns (bool) {
        return escrow.completeDeposit(depositId);
    }

    function releaseWithdrawal(
        HubCreditBridgeEscrow escrow,
        address account,
        address payable recipient,
        uint256 amountUnits,
        bytes32 withdrawalId,
        string calldata memo
    ) external returns (bool) {
        return escrow.releaseWithdrawal(account, recipient, amountUnits, withdrawalId, memo);
    }

    function setBridgeController(HubCreditBridgeEscrow escrow, address newBridgeController) external {
        escrow.setBridgeController(newBridgeController);
    }

    function setPaused(HubCreditBridgeEscrow escrow, bool nextPaused) external {
        escrow.setPaused(nextPaused);
    }

    function transferOwnership(HubCreditBridgeEscrow escrow, address newOwner) external {
        escrow.transferOwnership(newOwner);
    }

    function proposeAuthorizeBridgeController(HubCreditBridgeEscrow escrow, address controller) external returns (uint256) {
        return escrow.proposeAuthorizeBridgeController(controller);
    }

    function proposeRetireBridgeController(HubCreditBridgeEscrow escrow, address controller) external returns (uint256) {
        return escrow.proposeRetireBridgeController(controller);
    }

    function proposeSetActionSecondsRequired(HubCreditBridgeEscrow escrow, bytes32 action, uint8 secondsRequired)
        external
        returns (uint256)
    {
        return escrow.proposeSetActionSecondsRequired(action, secondsRequired);
    }

    function secondOfficerProposal(HubCreditBridgeEscrow escrow, uint256 proposalId) external returns (bool) {
        return escrow.secondOfficerProposal(proposalId);
    }

    function sendRawEth(HubCreditBridgeEscrow escrow) external payable {
        (bool sent, ) = address(escrow).call{value: msg.value}("");
        require(sent, "raw send failed");
    }
}

contract HubCreditBridgeEscrowTest {
    uint256 private constant CREDIT = 1 ether;

    EscrowActor private requester = new EscrowActor{value: 200 ether}();
    EscrowActor private bridge = new EscrowActor();
    EscrowActor private outsider = new EscrowActor();

    function testDepositRectifyAndReleaseWithdrawal() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        bytes32 depositId = keccak256("deposit-100");
        bool depositApplied = requester.depositFor{value: 100 * CREDIT}(
            escrow,
            address(requester),
            100 * CREDIT,
            depositId,
            "deposit 100"
        );

        assertTrue(depositApplied);
        assertEq(escrow.depositedUnits(address(requester)), 100 * CREDIT);
        assertEq(escrow.withdrawableUnits(address(requester)), 100 * CREDIT);

        bool firstRectification = bridge.rectifySpend(
            escrow,
            address(requester),
            5 * CREDIT,
            keccak256("rectify-5"),
            "rectify first 5"
        );

        assertTrue(firstRectification);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), 5 * CREDIT);
        assertEq(escrow.withdrawableUnits(address(requester)), 95 * CREDIT);

        bool dustRectification = bridge.rectifySpend(
            escrow,
            address(requester),
            CREDIT / 2,
            keccak256("rectify-0.5"),
            "rectify dust 0.5"
        );

        assertTrue(dustRectification);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), (5 * CREDIT) + (CREDIT / 2));
        assertEq(escrow.withdrawableUnits(address(requester)), (94 * CREDIT) + (CREDIT / 2));

        uint256 beforeRequester = address(requester).balance;
        bool released = bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            (94 * CREDIT) + (CREDIT / 2),
            keccak256("withdraw-94.5"),
            "release unused escrow"
        );

        assertTrue(released);
        assertEq(address(requester).balance, beforeRequester + ((94 * CREDIT) + (CREDIT / 2)));
        assertEq(escrow.withdrawnUnits(address(requester)), (94 * CREDIT) + (CREDIT / 2));
        assertEq(escrow.withdrawableUnits(address(requester)), 0);
    }

    function testDepositRecordStartsIncompleteWithPayer() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        bytes32 depositId = keccak256("deposit-record");
        requester.depositFor{value: 3 * CREDIT}(
            escrow,
            address(requester),
            3 * CREDIT,
            depositId,
            "record deposit"
        );

        (
            bool exists,
            bool completed,
            address account,
            address payer,
            uint256 amountUnits
        ) = escrow.depositRecord(depositId);

        assertTrue(exists);
        assertFalse(completed);
        assertEq(account, address(requester));
        assertEq(payer, address(requester));
        assertEq(amountUnits, 3 * CREDIT);
        assertEq(escrow.completedDepositUnits(address(requester)), 0);
    }

    function testCompleteDepositMarksCompleteAndIncrementsAggregate() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 depositId = keccak256("complete-deposit");
        requester.depositFor{value: 7 * CREDIT}(
            escrow,
            address(requester),
            7 * CREDIT,
            depositId,
            "complete deposit"
        );

        bool applied = bridge.completeDeposit(escrow, depositId);

        (
            bool exists,
            bool completed,
            address account,
            address payer,
            uint256 amountUnits
        ) = escrow.depositRecord(depositId);

        assertTrue(applied);
        assertTrue(exists);
        assertTrue(completed);
        assertEq(account, address(requester));
        assertEq(payer, address(requester));
        assertEq(amountUnits, 7 * CREDIT);
        assertEq(escrow.completedDepositUnits(address(requester)), 7 * CREDIT);
    }

    function testDuplicateCompleteDepositDoesNotDoubleCount() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 depositId = keccak256("duplicate-complete-deposit");
        requester.depositFor{value: 11 * CREDIT}(
            escrow,
            address(requester),
            11 * CREDIT,
            depositId,
            "complete deposit once"
        );

        bool first = bridge.completeDeposit(escrow, depositId);
        bool duplicate = bridge.completeDeposit(escrow, depositId);

        assertTrue(first);
        assertFalse(duplicate);
        assertEq(escrow.completedDepositUnits(address(requester)), 11 * CREDIT);
    }

    function testNonBridgeCannotCompleteDeposit() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 depositId = keccak256("non-bridge-complete-deposit");
        requester.depositFor{value: 13 * CREDIT}(
            escrow,
            address(requester),
            13 * CREDIT,
            depositId,
            "non bridge complete"
        );

        try outsider.completeDeposit(escrow, depositId) {
            revert("expected bridge-only complete rejection");
        } catch {}

        (
            bool exists,
            bool completed,
            address account,
            address payer,
            uint256 amountUnits
        ) = escrow.depositRecord(depositId);

        assertTrue(exists);
        assertFalse(completed);
        assertEq(account, address(requester));
        assertEq(payer, address(requester));
        assertEq(amountUnits, 13 * CREDIT);
        assertEq(escrow.completedDepositUnits(address(requester)), 0);
    }

    function testUnknownDepositCannotBeCompleted() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        try bridge.completeDeposit(escrow, keccak256("missing-deposit")) {
            revert("expected unknown deposit rejection");
        } catch {}
    }

    function testDuplicateRectificationIdDoesNotDoubleCount() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 rectificationId = keccak256("same-rectification");
        bool first = bridge.rectifySpend(escrow, address(requester), 5 * CREDIT, rectificationId, "first");
        bool duplicate = bridge.rectifySpend(escrow, address(requester), 5 * CREDIT, rectificationId, "retry");

        assertTrue(first);
        assertFalse(duplicate);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), 5 * CREDIT);
        assertEq(escrow.withdrawableUnits(address(requester)), 95 * CREDIT);
    }

    function testDuplicateWithdrawalIdDoesNotDoublePay() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();
        bridge.rectifySpend(escrow, address(requester), 5 * CREDIT, keccak256("rectify-before-withdraw"), "rectify");

        bytes32 withdrawalId = keccak256("same-withdrawal");
        uint256 beforeRequester = address(requester).balance;

        bool first = bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            95 * CREDIT,
            withdrawalId,
            "first"
        );
        bool duplicate = bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            95 * CREDIT,
            withdrawalId,
            "retry"
        );

        assertTrue(first);
        assertFalse(duplicate);
        assertEq(address(requester).balance, beforeRequester + (95 * CREDIT));
        assertEq(escrow.withdrawnUnits(address(requester)), 95 * CREDIT);
    }

    function testNonBridgeCannotRectifyOrReleaseWithdrawal() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        try outsider.rectifySpend(escrow, address(requester), 1 * CREDIT, keccak256("bad-rectify"), "bad") {
            revert("expected bridge-only rectify rejection");
        } catch {}

        try outsider.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            1 * CREDIT,
            keccak256("bad-withdraw"),
            "bad"
        ) {
            revert("expected bridge-only withdrawal rejection");
        } catch {}

        assertEq(escrow.rectifiedSpentUnits(address(requester)), 0);
        assertEq(escrow.withdrawnUnits(address(requester)), 0);
        assertEq(escrow.withdrawableUnits(address(requester)), 100 * CREDIT);
    }

    function testConflictingIdempotencyIdsAreRejected() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 rectificationId = keccak256("rectification-conflict");
        bridge.rectifySpend(escrow, address(requester), 5 * CREDIT, rectificationId, "first");

        try bridge.rectifySpend(escrow, address(requester), 6 * CREDIT, rectificationId, "conflict") {
            revert("expected rectification amount mismatch");
        } catch {}

        bytes32 withdrawalId = keccak256("withdrawal-conflict");
        bridge.releaseWithdrawal(escrow, address(requester), payable(address(requester)), 95 * CREDIT, withdrawalId, "first");

        try bridge.releaseWithdrawal(escrow, address(requester), payable(address(outsider)), 95 * CREDIT, withdrawalId, "conflict") {
            revert("expected withdrawal recipient mismatch");
        } catch {}
    }

    function testOnlyOwnerCanChangeBridgeController() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        try outsider.setBridgeController(escrow, address(outsider)) {
            revert("expected owner rejection");
        } catch {}

        escrow.setBridgeController(address(outsider));
        assertEq(escrow.bridgeController(), address(outsider));
    }

    function testPauseBlocksWritesAndUnpauseResumesOperations() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        bytes32 depositWhilePausedId = keccak256("deposit-while-paused");
        bytes32 completeWhilePausedId = keccak256("complete-while-paused");
        requester.depositFor{value: 2 * CREDIT}(
            escrow,
            address(requester),
            2 * CREDIT,
            completeWhilePausedId,
            "deposit before pause"
        );

        escrow.setPaused(true);
        assertTrue(escrow.paused());

        try requester.depositFor{value: 1 * CREDIT}(
            escrow,
            address(requester),
            1 * CREDIT,
            depositWhilePausedId,
            "paused deposit"
        ) {
            revert("expected paused deposit rejection");
        } catch {}

        try bridge.completeDeposit(escrow, completeWhilePausedId) {
            revert("expected paused complete rejection");
        } catch {}

        try bridge.rectifySpend(escrow, address(requester), 1 * CREDIT, keccak256("paused-rectify"), "paused rectify") {
            revert("expected paused rectify rejection");
        } catch {}

        try bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            1 * CREDIT,
            keccak256("paused-withdraw"),
            "paused withdraw"
        ) {
            revert("expected paused withdrawal rejection");
        } catch {}

        assertEq(escrow.depositedUnits(address(requester)), 102 * CREDIT);
        assertEq(escrow.completedDepositUnits(address(requester)), 0);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), 0);
        assertEq(escrow.withdrawnUnits(address(requester)), 0);

        escrow.setPaused(false);
        assertFalse(escrow.paused());

        bytes32 resumedDepositId = keccak256("resumed-deposit");
        bool resumedDeposit = requester.depositFor{value: 3 * CREDIT}(
            escrow,
            address(requester),
            3 * CREDIT,
            resumedDepositId,
            "resumed deposit"
        );
        bool completed = bridge.completeDeposit(escrow, completeWhilePausedId);
        bool rectified = bridge.rectifySpend(
            escrow,
            address(requester),
            1 * CREDIT,
            keccak256("resumed-rectify"),
            "resumed rectify"
        );
        bool released = bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            1 * CREDIT,
            keccak256("resumed-withdraw"),
            "resumed withdraw"
        );

        assertTrue(resumedDeposit);
        assertTrue(completed);
        assertTrue(rectified);
        assertTrue(released);
        assertEq(escrow.completedDepositUnits(address(requester)), 2 * CREDIT);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), 1 * CREDIT);
        assertEq(escrow.withdrawnUnits(address(requester)), 1 * CREDIT);
    }

    function testOnlyOwnerCanTransferOwnershipAndNewOwnerReceivesOwnerPowers() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        assertEq(escrow.owner(), address(this));

        try outsider.transferOwnership(escrow, address(outsider)) {
            revert("expected owner-only transfer rejection");
        } catch {}
        assertEq(escrow.owner(), address(this));

        try escrow.transferOwnership(address(0)) {
            revert("expected zero-owner rejection");
        } catch {}
        assertEq(escrow.owner(), address(this));

        escrow.transferOwnership(address(outsider));
        assertEq(escrow.owner(), address(outsider));

        try escrow.setPaused(true) {
            revert("expected old owner pause rejection");
        } catch {}

        try escrow.setBridgeController(address(outsider)) {
            revert("expected old owner bridge-controller rejection");
        } catch {}

        try escrow.transferOwnership(address(this)) {
            revert("expected old owner transfer rejection");
        } catch {}

        outsider.setPaused(escrow, true);
        assertTrue(escrow.paused());

        outsider.setPaused(escrow, false);
        assertFalse(escrow.paused());

        outsider.setBridgeController(escrow, address(outsider));
        assertEq(escrow.bridgeController(), address(outsider));

        outsider.transferOwnership(escrow, address(this));
        assertEq(escrow.owner(), address(this));
    }

    function testBridgeControllerRotationRevokesOldBridgeAndAuthorizesNewBridge() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        try escrow.setBridgeController(address(0)) {
            revert("expected zero bridge rejection");
        } catch {}
        assertEq(escrow.bridgeController(), address(bridge));

        escrow.setBridgeController(address(outsider));
        assertEq(escrow.bridgeController(), address(outsider));

        bytes32 depositId = keccak256("rotated-controller-deposit");
        requester.depositFor{value: 4 * CREDIT}(
            escrow,
            address(requester),
            4 * CREDIT,
            depositId,
            "rotation deposit"
        );

        try bridge.completeDeposit(escrow, depositId) {
            revert("expected old bridge complete rejection");
        } catch {}

        try bridge.rectifySpend(escrow, address(requester), 1 * CREDIT, keccak256("old-bridge-rectify"), "old bridge") {
            revert("expected old bridge rectify rejection");
        } catch {}

        try bridge.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            1 * CREDIT,
            keccak256("old-bridge-withdraw"),
            "old bridge"
        ) {
            revert("expected old bridge withdrawal rejection");
        } catch {}

        bool completed = outsider.completeDeposit(escrow, depositId);
        bool rectified = outsider.rectifySpend(
            escrow,
            address(requester),
            1 * CREDIT,
            keccak256("new-bridge-rectify"),
            "new bridge"
        );
        bool released = outsider.releaseWithdrawal(
            escrow,
            address(requester),
            payable(address(requester)),
            1 * CREDIT,
            keccak256("new-bridge-withdraw"),
            "new bridge"
        );

        assertTrue(completed);
        assertTrue(rectified);
        assertTrue(released);
        assertEq(escrow.completedDepositUnits(address(requester)), 4 * CREDIT);
        assertEq(escrow.rectifiedSpentUnits(address(requester)), 1 * CREDIT);
        assertEq(escrow.withdrawnUnits(address(requester)), 1 * CREDIT);
    }

    function testHubAdminCannotUseOwnerOnlyControls() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        try bridge.setBridgeController(escrow, address(outsider)) {
            revert("expected hub admin bridge-controller rejection");
        } catch {}

        try bridge.setPaused(escrow, true) {
            revert("expected hub admin pause rejection");
        } catch {}

        try bridge.transferOwnership(escrow, address(bridge)) {
            revert("expected hub admin ownership-transfer rejection");
        } catch {}

        assertEq(escrow.owner(), address(this));
        assertEq(escrow.bridgeController(), address(bridge));
        assertFalse(escrow.paused());
    }


    function testOfficerCanAuthorizeAdditionalBridgeControllerWithoutSecondingAtBootstrap() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        uint256 proposalId = escrow.proposeAuthorizeBridgeController(address(outsider));

        assertEq(proposalId, 1);
        assertTrue(escrow.authorizedBridgeControllers(address(bridge)));
        assertTrue(escrow.authorizedBridgeControllers(address(outsider)));
        assertEq(escrow.authorizedBridgeControllerCount(), 2);

        bool rectified = outsider.rectifySpend(
            escrow,
            address(requester),
            1 * CREDIT,
            keccak256("second-bridge-rectify"),
            "second bridge"
        );
        assertTrue(rectified);
    }

    function testOfficerCanRaiseAuthorizeThresholdAndRequireSecondOfficer() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        uint256 thresholdProposalId = escrow.proposeSetActionSecondsRequired(
            escrow.ACTION_AUTHORIZE_BRIDGE_CONTROLLER(),
            1
        );

        assertEq(thresholdProposalId, 1);
        assertEq(escrow.actionSecondsRequired(escrow.ACTION_AUTHORIZE_BRIDGE_CONTROLLER()), 1);

        uint256 proposalId = bridge.proposeAuthorizeBridgeController(escrow, address(requester));
        assertFalse(escrow.authorizedBridgeControllers(address(requester)));

        bool executed = outsider.secondOfficerProposal(escrow, proposalId);

        assertTrue(executed);
        assertTrue(escrow.authorizedBridgeControllers(address(requester)));
        assertEq(escrow.authorizedBridgeControllerCount(), 2);
    }

    function testOfficerRetiresOldBridgeControllerAfterReplacement() public {
        HubCreditBridgeEscrow escrow = fundedEscrow();

        escrow.proposeAuthorizeBridgeController(address(outsider));
        assertTrue(escrow.authorizedBridgeControllers(address(bridge)));
        assertTrue(escrow.authorizedBridgeControllers(address(outsider)));

        uint256 proposalId = escrow.proposeRetireBridgeController(address(bridge));

        assertEq(proposalId, 2);
        assertFalse(escrow.authorizedBridgeControllers(address(bridge)));
        assertTrue(escrow.authorizedBridgeControllers(address(outsider)));
        assertEq(escrow.authorizedBridgeControllerCount(), 1);
        assertEq(escrow.bridgeController(), address(0));

        bytes32 depositId = keccak256("retired-old-complete");
        requester.depositFor{value: 2 * CREDIT}(
            escrow,
            address(requester),
            2 * CREDIT,
            depositId,
            "retire old deposit"
        );

        try bridge.completeDeposit(escrow, depositId) {
            revert("expected retired bridge complete rejection");
        } catch {}

        bool completed = outsider.completeDeposit(escrow, depositId);
        assertTrue(completed);
    }

    function testConstructorRejectsZeroBridgeAndInitializesAuthorities() public {
        address[4] memory officeAddresses = [
            address(this),
            address(bridge),
            address(requester),
            address(outsider)
        ];

        try new HubCreditBridgeEscrow(address(0), officeAddresses) returns (HubCreditBridgeEscrow rejected) {
            assertTrue(address(rejected) != address(0));
            revert("expected zero bridge constructor rejection");
        } catch {}

        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        assertEq(escrow.owner(), address(this));
        assertEq(escrow.bridgeController(), address(bridge));
        assertTrue(escrow.authorizedBridgeControllers(address(bridge)));
        assertEq(escrow.authorizedBridgeControllerCount(), 1);
        assertEq(escrow.actionSecondsRequired(escrow.ACTION_AUTHORIZE_BRIDGE_CONTROLLER()), 0);
        assertEq(escrow.actionSecondsRequired(escrow.ACTION_RETIRE_BRIDGE_CONTROLLER()), 0);
        assertEq(escrow.actionSecondsRequired(escrow.ACTION_SET_ACTION_SECONDS_REQUIRED()), 0);
        assertFalse(escrow.paused());
    }

    function testRawEthReceiveRevertsAndDepositForIsRequired() public {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));

        try requester.sendRawEth{value: 1 * CREDIT}(escrow) {
            revert("expected raw eth rejection");
        } catch {}

        assertEq(address(escrow).balance, 0);

        bytes32 depositId = keccak256("receive-requires-deposit-for");
        bool deposited = requester.depositFor{value: 1 * CREDIT}(
            escrow,
            address(requester),
            1 * CREDIT,
            depositId,
            "deposit via depositFor"
        );

        assertTrue(deposited);
        assertEq(address(escrow).balance, 1 * CREDIT);
        assertEq(escrow.depositedUnits(address(requester)), 1 * CREDIT);
    }


    function newEscrow(address controller) private returns (HubCreditBridgeEscrow) {
        address[4] memory officeAddresses = [
            address(this),
            address(bridge),
            address(requester),
            address(outsider)
        ];
        return new HubCreditBridgeEscrow(controller, officeAddresses);
    }

    function fundedEscrow() private returns (HubCreditBridgeEscrow) {
        HubCreditBridgeEscrow escrow = newEscrow(address(bridge));
        requester.depositFor{value: 100 * CREDIT}(
            escrow,
            address(requester),
            100 * CREDIT,
            keccak256(abi.encodePacked("deposit", address(escrow))),
            "fund requester"
        );
        return escrow;
    }

    function assertEq(uint256 left, uint256 right) private pure {
        require(left == right, "uint mismatch");
    }

    function assertEq(address left, address right) private pure {
        require(left == right, "address mismatch");
    }

    function assertTrue(bool value) private pure {
        require(value, "assert true failed");
    }

    function assertFalse(bool value) private pure {
        require(!value, "assert false failed");
    }
}
