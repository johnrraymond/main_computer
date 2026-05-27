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
}

contract HubCreditBridgeEscrowTest {
    uint256 private constant CREDIT = 1 ether;

    EscrowActor private requester = new EscrowActor{value: 200 ether}();
    EscrowActor private bridge = new EscrowActor();
    EscrowActor private outsider = new EscrowActor();

    function testDepositRectifyAndReleaseWithdrawal() public {
        HubCreditBridgeEscrow escrow = new HubCreditBridgeEscrow(address(bridge));

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
        HubCreditBridgeEscrow escrow = new HubCreditBridgeEscrow(address(bridge));

        try outsider.setBridgeController(escrow, address(outsider)) {
            revert("expected owner rejection");
        } catch {}

        escrow.setBridgeController(address(outsider));
        assertEq(escrow.bridgeController(), address(outsider));
    }

    function fundedEscrow() private returns (HubCreditBridgeEscrow) {
        HubCreditBridgeEscrow escrow = new HubCreditBridgeEscrow(address(bridge));
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
