// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract AlphaBetaLockout {
    uint256 public constant COUNCIL_SIZE = 4;
    uint256 public constant ALPHA_SIZE = 2;
    uint256 public constant BETA_SIZE = 2;

    enum Answer {
        NONE,
        YES,
        NO
    }

    enum HarmonicState {
        NONE,
        BETA_PENDING,
        ALLOW,
        HOLD_AGAINST,
        SPLIT,
        PHASE_CHANGE,
        EXPIRED,
        CANCELLED
    }

    struct Proposal {
        uint256 proposalId;
        address alphaA;
        address alphaB;
        address betaA;
        address betaB;
        bytes32 payloadHash;
        string memo;
        uint256 createdBlock;
        uint256 expiresBlock;
        Answer betaAnswerA;
        Answer betaAnswerB;
        HarmonicState harmonicState;
        bool executed;
    }

    event ProposalCreated(
        uint256 indexed proposalId,
        address indexed alphaA,
        address indexed alphaB,
        address betaA,
        address betaB,
        bytes32 payloadHash,
        string memo,
        uint256 expiresBlock
    );
    event ProposalAnswered(uint256 indexed proposalId, address indexed betaMember, Answer answer);
    event HarmonicResolved(uint256 indexed proposalId, HarmonicState harmonicState);
    event PhaseChange(uint256 indexed proposalId);

    address[COUNCIL_SIZE] private _council;
    mapping(address => bool) private _isCouncilMember;
    mapping(uint256 => Proposal) private _proposals;
    uint256 private _nextProposalId = 1;

    constructor(address[COUNCIL_SIZE] memory councilMembers) {
        for (uint256 i = 0; i < COUNCIL_SIZE; i++) {
            address member = councilMembers[i];
            require(member != address(0), "zero council member");
            require(!_isCouncilMember[member], "duplicate council member");
            _isCouncilMember[member] = true;
            _council[i] = member;
        }
    }

    function createProposal(
        address alphaA,
        address alphaB,
        address betaA,
        address betaB,
        bytes32 payloadHash,
        string calldata memo,
        uint256 expiresBlock
    ) external returns (uint256 proposalId) {
        require(isCouncilMember(msg.sender), "only council");
        require(isCouncilMember(alphaA) && isCouncilMember(alphaB), "alpha not council");
        require(isCouncilMember(betaA) && isCouncilMember(betaB), "beta not council");
        require(alphaA != alphaB, "alpha duplicate");
        require(betaA != betaB, "beta duplicate");
        require(alphaA != betaA && alphaA != betaB && alphaB != betaA && alphaB != betaB, "compartment overlap");
        require(msg.sender == alphaA || msg.sender == alphaB, "caller not alpha");
        require(payloadHash != bytes32(0), "zero payload hash");
        require(expiresBlock == 0 || expiresBlock > block.number, "expired at creation");

        proposalId = _nextProposalId++;
        _proposals[proposalId] = Proposal({
            proposalId: proposalId,
            alphaA: alphaA,
            alphaB: alphaB,
            betaA: betaA,
            betaB: betaB,
            payloadHash: payloadHash,
            memo: memo,
            createdBlock: block.number,
            expiresBlock: expiresBlock,
            betaAnswerA: Answer.NONE,
            betaAnswerB: Answer.NONE,
            harmonicState: HarmonicState.BETA_PENDING,
            executed: false
        });

        emit ProposalCreated(proposalId, alphaA, alphaB, betaA, betaB, payloadHash, memo, expiresBlock);
    }

    function answerProposal(uint256 proposalId, Answer answer) external {
        Proposal storage proposal = _proposals[proposalId];
        require(proposal.harmonicState != HarmonicState.NONE, "unknown proposal");
        require(isActiveProposal(proposalId), "proposal not active");
        require(answer == Answer.YES || answer == Answer.NO, "invalid answer");
        require(msg.sender == proposal.betaA || msg.sender == proposal.betaB, "only beta");
        require(msg.sender != proposal.alphaA && msg.sender != proposal.alphaB, "alpha locked out");

        if (msg.sender == proposal.betaA) {
            require(proposal.betaAnswerA == Answer.NONE, "beta already answered");
            proposal.betaAnswerA = answer;
        } else {
            require(proposal.betaAnswerB == Answer.NONE, "beta already answered");
            proposal.betaAnswerB = answer;
        }
        emit ProposalAnswered(proposalId, msg.sender, answer);

        if (proposal.betaAnswerA != Answer.NONE && proposal.betaAnswerB != Answer.NONE) {
            _resolve(proposalId, proposal);
        }
    }

    function getProposal(uint256 proposalId) external view returns (Proposal memory) {
        Proposal memory proposal = _proposals[proposalId];
        require(proposal.harmonicState != HarmonicState.NONE, "unknown proposal");
        return proposal;
    }

    function harmonicStateOf(uint256 proposalId) public view returns (HarmonicState) {
        Proposal storage proposal = _proposals[proposalId];
        require(proposal.harmonicState != HarmonicState.NONE, "unknown proposal");
        if (
            proposal.harmonicState == HarmonicState.BETA_PENDING
                && proposal.expiresBlock != 0
                && block.number > proposal.expiresBlock
        ) {
            return HarmonicState.EXPIRED;
        }
        return proposal.harmonicState;
    }

    function isCouncilMember(address member) public view returns (bool) {
        return _isCouncilMember[member];
    }

    function isActiveProposal(uint256 proposalId) public view returns (bool) {
        Proposal storage proposal = _proposals[proposalId];
        if (proposal.harmonicState == HarmonicState.NONE) {
            return false;
        }
        return harmonicStateOf(proposalId) == HarmonicState.BETA_PENDING;
    }

    function councilMember(uint256 index) external view returns (address) {
        require(index < COUNCIL_SIZE, "index out of bounds");
        return _council[index];
    }

    function _resolve(uint256 proposalId, Proposal storage proposal) private {
        Answer a = proposal.betaAnswerA;
        Answer b = proposal.betaAnswerB;
        if (a == Answer.YES && b == Answer.YES) {
            proposal.harmonicState = HarmonicState.ALLOW;
            emit HarmonicResolved(proposalId, HarmonicState.ALLOW);
            return;
        }
        if (a == Answer.NO && b == Answer.NO) {
            proposal.harmonicState = HarmonicState.HOLD_AGAINST;
            emit HarmonicResolved(proposalId, HarmonicState.HOLD_AGAINST);
            return;
        }
        proposal.harmonicState = HarmonicState.SPLIT;
        emit HarmonicResolved(proposalId, HarmonicState.SPLIT);
        proposal.harmonicState = HarmonicState.PHASE_CHANGE;
        emit PhaseChange(proposalId);
    }
}
