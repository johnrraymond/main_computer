// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract XLagBridgeReserve {
    uint8 public constant OFFICE_COUNT = 4;
    uint8 public constant CAPTAIN = 0;
    uint8 public constant FIRST_OFFICER = 1;
    uint8 public constant SECOND_OFFICER = 2;
    uint8 public constant THIRD_OFFICER = 3;

    enum ProposalKind {
        NONE,
        PAYOUT,
        OFFICE_RESET
    }

    enum ProposalState {
        NONE,
        PENDING,
        BELAYED,
        CONTESTED,
        EXECUTED,
        EXPIRED,
        CANCELLED
    }

    struct PayoutProposal {
        uint256 id;
        ProposalKind kind;
        address captain;
        address recipient;
        uint256 amountWei;
        string memo;
        uint256 createdBlock;
        uint256 executableBlock;
        uint256 expiresBlock;
        address secondedBy;
        address belayedBy;
        address contestedBy;
        ProposalState state;
    }

    struct OfficeResetProposal {
        uint256 id;
        ProposalKind kind;
        uint8 targetOffice;
        address oldAddress;
        address newAddress;
        string reason;
        uint256 createdBlock;
        uint256 executableBlock;
        uint256 expiresBlock;
        uint8 approvalsBitmap;
        uint8 approvalCount;
        address contestedBy;
        ProposalState state;
    }

    event OfficeResetProposed(
        uint256 indexed id,
        uint8 indexed targetOffice,
        address indexed oldAddress,
        address newAddress,
        address proposer,
        string reason,
        uint64 expiresBlock
    );
    event OfficeResetApproved(uint256 indexed id, uint8 indexed office, address indexed approver, uint8 approvalCount);
    event OfficeResetExecuted(uint256 indexed id, uint8 indexed targetOffice, address indexed oldAddress, address newAddress);
    event PayoutProposed(uint256 indexed id, address indexed captain, address indexed recipient, uint256 amountWei, string memo, uint64 expiresBlock);
    event PayoutSeconded(uint256 indexed id, uint8 indexed office, address indexed secondedBy);
    event PayoutBelayed(uint256 indexed id, address indexed belayedBy);
    event ProposalContested(uint256 indexed id, address indexed contestedBy);
    event PayoutExecuted(uint256 indexed id, address indexed recipient, uint256 amountWei);
    event ProposalExpired(uint256 indexed id);
    event NativeReceived(address indexed sender, uint256 amountWei);
    event WalletSmokeFinalized(uint256 indexed nonce, address indexed finalizer, uint8 indexed office, bytes32 smokeId, string memo);
    event AnyUserFrobbed(uint256 indexed nonce, address indexed frobber, bytes32 frobId, string memo);

    address[OFFICE_COUNT] private _offices;
    mapping(address => uint8) public officeIndexPlusOne;
    mapping(uint256 => ProposalKind) public proposalKinds;
    mapping(uint256 => PayoutProposal) private _payouts;
    mapping(uint256 => OfficeResetProposal) private _resets;

    uint256 public maxPayoutWei;
    uint64 public payoutDelayBlocks;
    uint64 public resetDelayBlocks;
    uint256 public nextProposalId = 1;

    uint256 public walletSmokeNonce;
    address public lastWalletSmokeFinalizer;
    uint8 public lastWalletSmokeOffice;
    bytes32 public lastWalletSmokeId;
    string public lastWalletSmokeMemo;
    uint256 public lastWalletSmokeBlock;

    uint256 public frobNonce;
    address public lastFrobber;
    bytes32 public lastFrobId;
    string public lastFrobMemo;
    uint256 public lastFrobBlock;

    constructor(address[OFFICE_COUNT] memory initialOffices, uint256 maxPayoutWei_, uint64 payoutDelayBlocks_, uint64 resetDelayBlocks_) {
        require(maxPayoutWei_ > 0, "max payout zero");
        maxPayoutWei = maxPayoutWei_;
        payoutDelayBlocks = payoutDelayBlocks_;
        resetDelayBlocks = resetDelayBlocks_;
        for (uint8 i = 0; i < OFFICE_COUNT; i++) {
            address office = initialOffices[i];
            require(office != address(0), "zero office");
            require(officeIndexPlusOne[office] == 0, "duplicate office");
            _offices[i] = office;
            officeIndexPlusOne[office] = i + 1;
        }
    }

    receive() external payable {
        emit NativeReceived(msg.sender, msg.value);
    }

    function finalizeWalletSmokeTest(bytes32 smokeId, string calldata memo) external returns (uint256 nonce) {
        uint8 office = _requireOffice(msg.sender);
        require(smokeId != bytes32(0), "zero smoke id");

        walletSmokeNonce += 1;
        nonce = walletSmokeNonce;
        lastWalletSmokeFinalizer = msg.sender;
        lastWalletSmokeOffice = office;
        lastWalletSmokeId = smokeId;
        lastWalletSmokeMemo = memo;
        lastWalletSmokeBlock = block.number;

        emit WalletSmokeFinalized(nonce, msg.sender, office, smokeId, memo);
    }

    // Harmless dev-chain/browser-wallet frob. It records caller metadata only;
    // it never touches payout proposals, office roles, or reserve funds.
    function frobByAnyUser(bytes32 frobId, string calldata memo) external returns (uint256 nonce) {
        require(frobId != bytes32(0), "zero frob id");

        frobNonce += 1;
        nonce = frobNonce;
        lastFrobber = msg.sender;
        lastFrobId = frobId;
        lastFrobMemo = memo;
        lastFrobBlock = block.number;

        emit AnyUserFrobbed(nonce, msg.sender, frobId, memo);
    }

    function proposePayout(address recipient, uint256 amountWei, string calldata memo, uint64 expiresBlock) external returns (uint256 id) {
        require(msg.sender == _offices[CAPTAIN], "only captain");
        require(recipient != address(0), "zero recipient");
        require(amountWei > 0, "zero amount");
        require(amountWei <= maxPayoutWei, "amount exceeds max");
        require(expiresBlock > block.number, "invalid expiry");

        id = nextProposalId++;
        proposalKinds[id] = ProposalKind.PAYOUT;
        _payouts[id] = PayoutProposal({
            id: id,
            kind: ProposalKind.PAYOUT,
            captain: msg.sender,
            recipient: recipient,
            amountWei: amountWei,
            memo: memo,
            createdBlock: block.number,
            executableBlock: block.number + payoutDelayBlocks,
            expiresBlock: expiresBlock,
            secondedBy: address(0),
            belayedBy: address(0),
            contestedBy: address(0),
            state: ProposalState.PENDING
        });
        emit PayoutProposed(id, msg.sender, recipient, amountWei, memo, expiresBlock);
    }

    function secondPayout(uint256 proposalId) external {
        PayoutProposal storage proposal = _requirePayout(proposalId);
        _expirePayoutIfNeeded(proposalId, proposal);
        require(proposal.state == ProposalState.PENDING, "not pending");
        uint8 office = _requireOffice(msg.sender);
        require(office == SECOND_OFFICER || office == THIRD_OFFICER, "only beta second");
        require(proposal.secondedBy == address(0), "already seconded");
        proposal.secondedBy = msg.sender;
        emit PayoutSeconded(proposalId, office, msg.sender);
    }

    function belayPayout(uint256 proposalId) external {
        PayoutProposal storage proposal = _requirePayout(proposalId);
        _expirePayoutIfNeeded(proposalId, proposal);
        require(proposal.state == ProposalState.PENDING, "not pending");
        require(msg.sender == _offices[FIRST_OFFICER], "only first officer");
        proposal.belayedBy = msg.sender;
        proposal.state = ProposalState.BELAYED;
        emit PayoutBelayed(proposalId, msg.sender);
    }

    function contestProposal(uint256 proposalId) external {
        _requireOffice(msg.sender);
        ProposalKind kind = proposalKinds[proposalId];
        require(kind != ProposalKind.NONE, "unknown proposal");
        if (kind == ProposalKind.PAYOUT) {
            PayoutProposal storage payout = _payouts[proposalId];
            require(payout.state != ProposalState.EXECUTED, "already executed");
            payout.contestedBy = msg.sender;
            payout.state = ProposalState.CONTESTED;
        } else {
            OfficeResetProposal storage reset = _resets[proposalId];
            require(reset.state != ProposalState.EXECUTED, "already executed");
            reset.contestedBy = msg.sender;
            reset.state = ProposalState.CONTESTED;
        }
        emit ProposalContested(proposalId, msg.sender);
    }

    function executePayout(uint256 proposalId) external {
        PayoutProposal storage proposal = _requirePayout(proposalId);
        _expirePayoutIfNeeded(proposalId, proposal);
        require(proposal.state == ProposalState.PENDING, "not pending");
        require(block.number >= proposal.executableBlock, "delay active");
        require(proposal.secondedBy == _offices[SECOND_OFFICER] || proposal.secondedBy == _offices[THIRD_OFFICER], "missing beta second");
        require(proposal.belayedBy == address(0), "belayed");
        require(proposal.contestedBy == address(0), "contested");
        require(address(this).balance >= proposal.amountWei, "insufficient balance");

        proposal.state = ProposalState.EXECUTED;
        (bool ok,) = payable(proposal.recipient).call{value: proposal.amountWei}("");
        require(ok, "native transfer failed");
        emit PayoutExecuted(proposalId, proposal.recipient, proposal.amountWei);
    }

    function proposeOfficeReset(uint8 targetOffice, address newAddress, string calldata reason, uint64 expiresBlock) external returns (uint256 id) {
        uint8 proposerOffice = _requireOffice(msg.sender);
        require(targetOffice < OFFICE_COUNT, "bad office");
        require(newAddress != address(0), "zero new office");
        require(officeIndexPlusOne[newAddress] == 0, "new office duplicate");
        require(expiresBlock > block.number, "invalid expiry");

        id = nextProposalId++;
        uint8 approvalBit = uint8(1) << proposerOffice;
        proposalKinds[id] = ProposalKind.OFFICE_RESET;
        _resets[id] = OfficeResetProposal({
            id: id,
            kind: ProposalKind.OFFICE_RESET,
            targetOffice: targetOffice,
            oldAddress: _offices[targetOffice],
            newAddress: newAddress,
            reason: reason,
            createdBlock: block.number,
            executableBlock: block.number + resetDelayBlocks,
            expiresBlock: expiresBlock,
            approvalsBitmap: approvalBit,
            approvalCount: 1,
            contestedBy: address(0),
            state: ProposalState.PENDING
        });
        emit OfficeResetProposed(id, targetOffice, _offices[targetOffice], newAddress, msg.sender, reason, expiresBlock);
        emit OfficeResetApproved(id, proposerOffice, msg.sender, 1);
    }

    function approveOfficeReset(uint256 proposalId) external {
        OfficeResetProposal storage proposal = _requireReset(proposalId);
        _expireResetIfNeeded(proposalId, proposal);
        require(proposal.state == ProposalState.PENDING, "not pending");
        uint8 office = _requireOffice(msg.sender);
        uint8 bit = uint8(1) << office;
        if (proposal.approvalsBitmap & bit == 0) {
            proposal.approvalsBitmap |= bit;
            proposal.approvalCount += 1;
            emit OfficeResetApproved(proposalId, office, msg.sender, proposal.approvalCount);
        }
    }

    function executeOfficeReset(uint256 proposalId) external {
        OfficeResetProposal storage proposal = _requireReset(proposalId);
        _expireResetIfNeeded(proposalId, proposal);
        require(proposal.state == ProposalState.PENDING, "not pending");
        require(proposal.approvalCount >= 3, "insufficient approvals");
        require(block.number >= proposal.executableBlock, "delay active");
        require(proposal.contestedBy == address(0), "contested");
        require(proposal.newAddress != address(0), "zero new office");
        require(officeIndexPlusOne[proposal.newAddress] == 0, "new office duplicate");

        address oldAddress = _offices[proposal.targetOffice];
        officeIndexPlusOne[oldAddress] = 0;
        _offices[proposal.targetOffice] = proposal.newAddress;
        officeIndexPlusOne[proposal.newAddress] = proposal.targetOffice + 1;
        proposal.oldAddress = oldAddress;
        proposal.state = ProposalState.EXECUTED;
        emit OfficeResetExecuted(proposalId, proposal.targetOffice, oldAddress, proposal.newAddress);
    }

    function getOffice(uint8 office) external view returns (address) {
        require(office < OFFICE_COUNT, "bad office");
        return _offices[office];
    }

    function getOffices() external view returns (address[OFFICE_COUNT] memory) {
        return _offices;
    }

    function isOffice(address account) public view returns (bool) {
        return officeIndexPlusOne[account] != 0;
    }

    function officeOf(address account) external view returns (bool found, uint8 office) {
        uint8 value = officeIndexPlusOne[account];
        if (value == 0) {
            return (false, 0);
        }
        return (true, value - 1);
    }

    function getPayoutProposal(uint256 proposalId) external view returns (PayoutProposal memory) {
        require(proposalKinds[proposalId] == ProposalKind.PAYOUT, "not payout");
        return _payouts[proposalId];
    }

    function getOfficeResetProposal(uint256 proposalId) external view returns (OfficeResetProposal memory) {
        require(proposalKinds[proposalId] == ProposalKind.OFFICE_RESET, "not reset");
        return _resets[proposalId];
    }

    function proposalState(uint256 proposalId) public view returns (ProposalState) {
        ProposalKind kind = proposalKinds[proposalId];
        if (kind == ProposalKind.PAYOUT) {
            PayoutProposal storage payout = _payouts[proposalId];
            if (payout.state == ProposalState.PENDING && block.number > payout.expiresBlock) {
                return ProposalState.EXPIRED;
            }
            return payout.state;
        }
        if (kind == ProposalKind.OFFICE_RESET) {
            OfficeResetProposal storage reset = _resets[proposalId];
            if (reset.state == ProposalState.PENDING && block.number > reset.expiresBlock) {
                return ProposalState.EXPIRED;
            }
            return reset.state;
        }
        return ProposalState.NONE;
    }

    function _requirePayout(uint256 proposalId) private view returns (PayoutProposal storage proposal) {
        require(proposalKinds[proposalId] == ProposalKind.PAYOUT, "not payout");
        return _payouts[proposalId];
    }

    function _requireReset(uint256 proposalId) private view returns (OfficeResetProposal storage proposal) {
        require(proposalKinds[proposalId] == ProposalKind.OFFICE_RESET, "not reset");
        return _resets[proposalId];
    }

    function _requireOffice(address account) private view returns (uint8) {
        uint8 value = officeIndexPlusOne[account];
        require(value != 0, "only office");
        return value - 1;
    }

    function _expirePayoutIfNeeded(uint256 proposalId, PayoutProposal storage proposal) private {
        if (proposal.state == ProposalState.PENDING && block.number > proposal.expiresBlock) {
            proposal.state = ProposalState.EXPIRED;
            emit ProposalExpired(proposalId);
        }
    }

    function _expireResetIfNeeded(uint256 proposalId, OfficeResetProposal storage proposal) private {
        if (proposal.state == ProposalState.PENDING && block.number > proposal.expiresBlock) {
            proposal.state = ProposalState.EXPIRED;
            emit ProposalExpired(proposalId);
        }
    }
}
