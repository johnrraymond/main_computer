// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../contracts/AlphaBetaLockout.sol";

contract AlphaBetaActor {
    function createProposal(
        AlphaBetaLockout lockout,
        address alphaA,
        address alphaB,
        address betaA,
        address betaB,
        bytes32 payloadHash,
        string calldata memo,
        uint256 expiresBlock
    ) external returns (uint256) {
        return lockout.createProposal(alphaA, alphaB, betaA, betaB, payloadHash, memo, expiresBlock);
    }

    function answerProposal(AlphaBetaLockout lockout, uint256 proposalId, AlphaBetaLockout.Answer answer) external {
        lockout.answerProposal(proposalId, answer);
    }
}

contract ForceSend {
    constructor() payable {}

    function push(address target) external {
        selfdestruct(payable(target));
    }
}

contract AlphaBetaLockoutTest {
    AlphaBetaActor private alphaA = new AlphaBetaActor();
    AlphaBetaActor private alphaB = new AlphaBetaActor();
    AlphaBetaActor private betaA = new AlphaBetaActor();
    AlphaBetaActor private betaB = new AlphaBetaActor();
    AlphaBetaActor private outsider = new AlphaBetaActor();

    function testConstructorRejectsDuplicateCouncilMembers() public {
        address[4] memory council = [address(alphaA), address(alphaA), address(betaA), address(betaB)];
        try new AlphaBetaLockout(council) {
            revert("expected duplicate rejection");
        } catch {}
    }

    function testConstructorRejectsZeroCouncilMember() public {
        address[4] memory council = [address(alphaA), address(0), address(betaA), address(betaB)];
        try new AlphaBetaLockout(council) {
            revert("expected zero rejection");
        } catch {}
    }

    function testTwoAlphaMembersCanCreateProposal() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        AlphaBetaLockout.Proposal memory proposal = lockout.getProposal(proposalId);
        assertEq(uint256(proposal.harmonicState), uint256(AlphaBetaLockout.HarmonicState.BETA_PENDING));
        assertEq(proposal.alphaA, address(alphaA));
        assertEq(proposal.alphaB, address(alphaB));
    }

    function testNonCouncilMemberCannotCreateProposal() public {
        AlphaBetaLockout lockout = fresh();
        try outsider.createProposal(
            lockout,
            address(outsider),
            address(alphaB),
            address(betaA),
            address(betaB),
            payloadHash(),
            "bad requester",
            block.number + 100
        ) {
            revert("expected non-council rejection");
        } catch {}
    }

    function testAlphaAndBetaCompartmentsCannotOverlap() public {
        AlphaBetaLockout lockout = fresh();
        try alphaA.createProposal(
            lockout,
            address(alphaA),
            address(alphaB),
            address(alphaB),
            address(betaB),
            payloadHash(),
            "overlap",
            block.number + 100
        ) {
            revert("expected overlap rejection");
        } catch {}
    }

    function testBetaYesYesResolvesAllow() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        betaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        assertEq(uint256(lockout.harmonicStateOf(proposalId)), uint256(AlphaBetaLockout.HarmonicState.ALLOW));
    }

    function testBetaNoNoResolvesHoldAgainst() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO);
        betaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO);
        assertEq(uint256(lockout.harmonicStateOf(proposalId)), uint256(AlphaBetaLockout.HarmonicState.HOLD_AGAINST));
    }

    function testBetaYesNoResolvesPhaseChange() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        betaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO);
        assertEq(uint256(lockout.harmonicStateOf(proposalId)), uint256(AlphaBetaLockout.HarmonicState.PHASE_CHANGE));
    }

    function testBetaNoYesResolvesPhaseChange() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO);
        betaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        assertEq(uint256(lockout.harmonicStateOf(proposalId)), uint256(AlphaBetaLockout.HarmonicState.PHASE_CHANGE));
    }

    function testAlphaMemberCannotAnswerOwnProposal() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        try alphaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES) {
            revert("expected alpha answer rejection");
        } catch {}
    }

    function testBetaMemberCannotAnswerTwice() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        try betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO) {
            revert("expected duplicate beta answer rejection");
        } catch {}
    }

    function testNonBetaCouncilMemberCannotAnswer() public {
        AlphaBetaLockout lockout = fresh();
        uint256 proposalId = create(lockout);
        try alphaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.NO) {
            revert("expected non-beta rejection");
        } catch {}
    }

    function testProposalWithZeroPayloadHashIsRejected() public {
        AlphaBetaLockout lockout = fresh();
        try alphaA.createProposal(
            lockout,
            address(alphaA),
            address(alphaB),
            address(betaA),
            address(betaB),
            bytes32(0),
            "zero payload",
            block.number + 100
        ) {
            revert("expected zero payload rejection");
        } catch {}
    }

    function testAllowDoesNotTransferFunds() public {
        AlphaBetaLockout lockout = fresh();
        ForceSend forceSend = new ForceSend{value: 1 ether}();
        forceSend.push(address(lockout));
        uint256 beforeBalance = address(lockout).balance;
        uint256 proposalId = create(lockout);
        betaA.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        betaB.answerProposal(lockout, proposalId, AlphaBetaLockout.Answer.YES);
        assertEq(uint256(lockout.harmonicStateOf(proposalId)), uint256(AlphaBetaLockout.HarmonicState.ALLOW));
        assertEq(address(lockout).balance, beforeBalance);
    }

    function testContractHasNoReserveDrainFunction() public {
        AlphaBetaLockout lockout = fresh();
        (bool ok,) = address(lockout).call(abi.encodeWithSignature("drainReserve()"));
        assertTrue(!ok);
    }

    function fresh() private returns (AlphaBetaLockout) {
        address[4] memory council = [address(alphaA), address(alphaB), address(betaA), address(betaB)];
        return new AlphaBetaLockout(council);
    }

    function create(AlphaBetaLockout lockout) private returns (uint256) {
        return alphaA.createProposal(
            lockout,
            address(alphaA),
            address(alphaB),
            address(betaA),
            address(betaB),
            payloadHash(),
            "locked payload",
            block.number + 100
        );
    }

    function payloadHash() private pure returns (bytes32) {
        return keccak256("main-computer-governance-payload");
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
}
