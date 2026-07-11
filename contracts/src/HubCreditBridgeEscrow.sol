// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title HubCreditBridgeEscrow
/// @notice Native-asset escrow for Compute Credits controlled by a bridge ledger.
/// @dev The contract does not know request-level AI charges. Users deposit once,
/// the bridge rectifies aggregate internal spend when needed, and the bridge
/// releases reconciled unused escrow back to the user. This avoids one public
/// chain transaction per AI request while still preventing over-withdrawal.
contract HubCreditBridgeEscrow {
    uint8 public constant OFFICER_COUNT = 4;
    uint8 public constant MAX_SECONDS_REQUIRED = OFFICER_COUNT - 1;

    bytes32 public constant ACTION_AUTHORIZE_BRIDGE_CONTROLLER =
        keccak256("HubCreditBridgeEscrow.action.authorizeBridgeController");
    bytes32 public constant ACTION_RETIRE_BRIDGE_CONTROLLER =
        keccak256("HubCreditBridgeEscrow.action.retireBridgeController");
    bytes32 public constant ACTION_SET_ACTION_SECONDS_REQUIRED =
        keccak256("HubCreditBridgeEscrow.action.setActionSecondsRequired");

    struct AccountEscrow {
        uint256 depositedUnits;
        uint256 rectifiedSpentUnits;
        uint256 withdrawnUnits;
    }

    struct ActionRecord {
        bool exists;
        address account;
        address actor;
        uint256 amountUnits;
    }

    struct DepositRecord {
        bool exists;
        bool completed;
        address account;
        address payer;
        uint256 amountUnits;
    }

    struct OfficerProposal {
        bytes32 action;
        address account;
        bytes32 value;
        uint8 requestedSecondsRequired;
        address proposer;
        uint8 secondsRequired;
        uint8 secondsReceived;
        bool executed;
    }

    event CreditDeposited(
        bytes32 indexed depositId,
        address indexed account,
        address indexed payer,
        uint256 amountUnits,
        string memo
    );
    event CreditDepositCompleted(
        bytes32 indexed depositId,
        address indexed account,
        uint256 amountUnits,
        uint256 cumulativeCompletedUnits,
        address indexed completer
    );
    event SpendRectified(
        bytes32 indexed rectificationId,
        address indexed account,
        uint256 amountUnits,
        uint256 cumulativeRectifiedUnits,
        string memo
    );
    event WithdrawalReleased(
        bytes32 indexed withdrawalId,
        address indexed account,
        address indexed recipient,
        uint256 amountUnits,
        string memo
    );
    event BridgeControllerUpdated(address indexed oldController, address indexed newController);
    event BridgeControllerAuthorized(address indexed controller, address indexed officer);
    event BridgeControllerRetired(address indexed controller, address indexed officer);
    event Paused(bool paused);
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);
    event OfficerProposalCreated(
        uint256 indexed proposalId,
        bytes32 indexed action,
        address indexed account,
        bytes32 value,
        address proposer,
        uint8 secondsRequired
    );
    event OfficerProposalSeconded(uint256 indexed proposalId, address indexed officer, uint8 secondsReceived);
    event OfficerProposalExecuted(uint256 indexed proposalId, bytes32 indexed action);
    event ActionSecondsRequiredUpdated(bytes32 indexed action, uint8 oldSecondsRequired, uint8 newSecondsRequired);

    address public owner;

    /// @notice Legacy primary bridge controller pointer retained for existing read paths.
    /// @dev Bridge authorization is now governed by authorizedBridgeControllers.
    address public bridgeController;

    bool public paused;
    address[4] public officers;
    uint256 public nextOfficerProposalId = 1;
    uint256 public authorizedBridgeControllerCount;

    mapping(address => bool) public authorizedBridgeControllers;
    mapping(address => bool) public isOfficer;
    mapping(bytes32 => uint8) public actionSecondsRequired;

    mapping(address => AccountEscrow) private _accounts;
    mapping(bytes32 => DepositRecord) private _deposits;
    mapping(address => uint256) private _completedDepositUnits;
    mapping(bytes32 => ActionRecord) private _rectifications;
    mapping(bytes32 => ActionRecord) private _withdrawals;
    mapping(uint256 => OfficerProposal) private _officerProposals;
    mapping(uint256 => mapping(address => bool)) public officerProposalApprovals;

    bool private _locked;

    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    modifier onlyOfficer() {
        require(isOfficer[msg.sender], "only officer");
        _;
    }

    modifier onlyBridge() {
        require(authorizedBridgeControllers[msg.sender], "only bridge");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "paused");
        _;
    }

    modifier nonReentrant() {
        require(!_locked, "reentrant");
        _locked = true;
        _;
        _locked = false;
    }

    constructor(address bridgeController_, address[4] memory officers_) {
        require(bridgeController_ != address(0), "zero bridge");
        owner = msg.sender;
        _installOfficers(officers_);
        bridgeController = bridgeController_;
        authorizedBridgeControllers[bridgeController_] = true;
        authorizedBridgeControllerCount = 1;
        emit OwnershipTransferred(address(0), msg.sender);
        emit BridgeControllerUpdated(address(0), bridgeController_);
        emit BridgeControllerAuthorized(bridgeController_, msg.sender);
    }

    function getAccount(address account) external view returns (AccountEscrow memory) {
        return _accounts[account];
    }

    function depositedUnits(address account) external view returns (uint256) {
        return _accounts[account].depositedUnits;
    }

    function rectifiedSpentUnits(address account) external view returns (uint256) {
        return _accounts[account].rectifiedSpentUnits;
    }

    function withdrawnUnits(address account) external view returns (uint256) {
        return _accounts[account].withdrawnUnits;
    }

    function completedDepositUnits(address account) external view returns (uint256) {
        return _completedDepositUnits[account];
    }

    function withdrawableUnits(address account) public view returns (uint256) {
        AccountEscrow memory escrow = _accounts[account];
        uint256 used = escrow.rectifiedSpentUnits + escrow.withdrawnUnits;
        if (used >= escrow.depositedUnits) {
            return 0;
        }
        return escrow.depositedUnits - used;
    }

    function depositRecord(bytes32 depositId)
        external
        view
        returns (
            bool exists,
            bool completed,
            address account,
            address payer,
            uint256 amountUnits
        )
    {
        DepositRecord memory deposit = _deposits[depositId];
        return (
            deposit.exists,
            deposit.completed,
            deposit.account,
            deposit.payer,
            deposit.amountUnits
        );
    }

    function rectificationRecord(bytes32 rectificationId) external view returns (ActionRecord memory) {
        return _rectifications[rectificationId];
    }

    function withdrawalRecord(bytes32 withdrawalId) external view returns (ActionRecord memory) {
        return _withdrawals[withdrawalId];
    }

    function officerProposal(uint256 proposalId)
        external
        view
        returns (
            bytes32 action,
            address account,
            bytes32 value,
            address proposer,
            uint8 secondsRequired,
            uint8 secondsReceived,
            bool executed
        )
    {
        OfficerProposal memory proposal = _officerProposals[proposalId];
        return (
            proposal.action,
            proposal.account,
            proposal.value,
            proposal.proposer,
            proposal.secondsRequired,
            proposal.secondsReceived,
            proposal.executed
        );
    }

    function officerProposalApprovalCount(uint256 proposalId) external view returns (uint8) {
        OfficerProposal memory proposal = _officerProposals[proposalId];
        if (proposal.proposer == address(0)) {
            return 0;
        }
        return uint8(1 + proposal.secondsReceived);
    }

    function isKnownOfficerAction(bytes32 action) public pure returns (bool) {
        return action == ACTION_AUTHORIZE_BRIDGE_CONTROLLER
            || action == ACTION_RETIRE_BRIDGE_CONTROLLER
            || action == ACTION_SET_ACTION_SECONDS_REQUIRED;
    }

    /// @notice Deposit native escrow for a user/account.
    /// @dev In the current dev-chain smoke, amountUnits is denominated in native
    /// base units. Hub accounting may display those units as Compute Credit atoms.
    function depositFor(address account, uint256 amountUnits, bytes32 depositId, string calldata memo)
        external
        payable
        whenNotPaused
        returns (bool applied)
    {
        require(account != address(0), "zero account");
        require(amountUnits > 0, "zero amount");
        require(depositId != bytes32(0), "zero deposit id");
        require(msg.value == amountUnits, "value mismatch");

        DepositRecord memory prior = _deposits[depositId];
        if (prior.exists) {
            revert("duplicate deposit id");
        }

        _deposits[depositId] = DepositRecord({
            exists: true,
            completed: false,
            account: account,
            payer: msg.sender,
            amountUnits: amountUnits
        });
        _accounts[account].depositedUnits += amountUnits;

        emit CreditDeposited(depositId, account, msg.sender, amountUnits, memo);
        return true;
    }

    /// @notice Mark a previously recorded funding deposit as completed by the bridge.
    /// @dev This Hub transaction entrypoint keeps the same ABI; only the signer allowlist changed.
    function completeDeposit(bytes32 depositId)
        external
        onlyBridge
        whenNotPaused
        returns (bool applied)
    {
        DepositRecord storage deposit = _deposits[depositId];

        require(deposit.exists, "unknown deposit");

        if (deposit.completed) {
            return false;
        }

        deposit.completed = true;
        _completedDepositUnits[deposit.account] += deposit.amountUnits;

        emit CreditDepositCompleted(
            depositId,
            deposit.account,
            deposit.amountUnits,
            _completedDepositUnits[deposit.account],
            msg.sender
        );

        return true;
    }

    /// @notice Rectify aggregate internal spend onto the escrow contract.
    /// @dev Duplicate rectification ids with the same account/amount are no-ops.
    function rectifySpend(address account, uint256 amountUnits, bytes32 rectificationId, string calldata memo)
        external
        onlyBridge
        whenNotPaused
        returns (bool applied)
    {
        require(account != address(0), "zero account");
        require(amountUnits > 0, "zero amount");
        require(rectificationId != bytes32(0), "zero rectification id");

        ActionRecord memory prior = _rectifications[rectificationId];
        if (prior.exists) {
            require(prior.account == account, "rectification id account mismatch");
            require(prior.amountUnits == amountUnits, "rectification id amount mismatch");
            return false;
        }

        require(amountUnits <= withdrawableUnits(account), "insufficient escrow");

        AccountEscrow storage escrow = _accounts[account];
        escrow.rectifiedSpentUnits += amountUnits;

        _rectifications[rectificationId] = ActionRecord({
            exists: true,
            account: account,
            actor: msg.sender,
            amountUnits: amountUnits
        });

        emit SpendRectified(rectificationId, account, amountUnits, escrow.rectifiedSpentUnits, memo);
        return true;
    }

    /// @notice Release reconciled unused escrow back to a recipient.
    /// @dev Duplicate withdrawal ids with the same account/recipient/amount are no-ops.
    function releaseWithdrawal(
        address account,
        address payable recipient,
        uint256 amountUnits,
        bytes32 withdrawalId,
        string calldata memo
    ) external onlyBridge whenNotPaused nonReentrant returns (bool applied) {
        require(account != address(0), "zero account");
        require(recipient != address(0), "zero recipient");
        require(amountUnits > 0, "zero amount");
        require(withdrawalId != bytes32(0), "zero withdrawal id");

        ActionRecord memory prior = _withdrawals[withdrawalId];
        if (prior.exists) {
            require(prior.account == account, "withdrawal id account mismatch");
            require(prior.actor == recipient, "withdrawal id recipient mismatch");
            require(prior.amountUnits == amountUnits, "withdrawal id amount mismatch");
            return false;
        }

        require(amountUnits <= withdrawableUnits(account), "insufficient escrow");

        _accounts[account].withdrawnUnits += amountUnits;
        _withdrawals[withdrawalId] = ActionRecord({
            exists: true,
            account: account,
            actor: recipient,
            amountUnits: amountUnits
        });

        (bool sent, ) = recipient.call{value: amountUnits}("");
        require(sent, "withdrawal transfer failed");

        emit WithdrawalReleased(withdrawalId, account, recipient, amountUnits, memo);
        return true;
    }

    /// @notice Legacy owner-only hard switch retained for dev bootstrap and older scripts.
    /// @dev The officer-governed rotation path should use proposeAuthorizeBridgeController/
    /// proposeRetireBridgeController. This method preserves the old single-controller
    /// behavior by retiring the current primary bridgeController and authorizing the new one.
    function setBridgeController(address newBridgeController) external onlyOwner {
        require(newBridgeController != address(0), "zero bridge");
        address oldBridgeController = bridgeController;
        if (oldBridgeController != address(0) && authorizedBridgeControllers[oldBridgeController]) {
            authorizedBridgeControllers[oldBridgeController] = false;
            authorizedBridgeControllerCount -= 1;
            emit BridgeControllerRetired(oldBridgeController, msg.sender);
        }
        bridgeController = newBridgeController;
        if (!authorizedBridgeControllers[newBridgeController]) {
            authorizedBridgeControllers[newBridgeController] = true;
            authorizedBridgeControllerCount += 1;
            emit BridgeControllerAuthorized(newBridgeController, msg.sender);
        }
        emit BridgeControllerUpdated(oldBridgeController, newBridgeController);
    }

    function proposeAuthorizeBridgeController(address controller) external onlyOfficer returns (uint256 proposalId) {
        require(controller != address(0), "zero bridge");
        proposalId = _createOfficerProposal(ACTION_AUTHORIZE_BRIDGE_CONTROLLER, controller, bytes32(0));
        _executeOfficerProposalIfReady(proposalId);
    }

    function proposeRetireBridgeController(address controller) external onlyOfficer returns (uint256 proposalId) {
        require(controller != address(0), "zero bridge");
        proposalId = _createOfficerProposal(ACTION_RETIRE_BRIDGE_CONTROLLER, controller, bytes32(0));
        _executeOfficerProposalIfReady(proposalId);
    }

    function proposeSetActionSecondsRequired(bytes32 action, uint8 secondsRequired)
        external
        onlyOfficer
        returns (uint256 proposalId)
    {
        require(isKnownOfficerAction(action), "unknown action");
        require(secondsRequired <= MAX_SECONDS_REQUIRED, "seconds too high");
        proposalId = _createOfficerProposal(
            ACTION_SET_ACTION_SECONDS_REQUIRED,
            address(0),
            action
        );
        _officerProposals[proposalId].requestedSecondsRequired = secondsRequired;
        _executeOfficerProposalIfReady(proposalId);
    }

    function secondOfficerProposal(uint256 proposalId) external onlyOfficer returns (bool executed) {
        OfficerProposal storage proposal = _officerProposals[proposalId];
        require(proposal.proposer != address(0), "unknown proposal");
        require(!proposal.executed, "proposal executed");
        require(!officerProposalApprovals[proposalId][msg.sender], "already approved");
        officerProposalApprovals[proposalId][msg.sender] = true;
        proposal.secondsReceived += 1;
        emit OfficerProposalSeconded(proposalId, msg.sender, proposal.secondsReceived);
        return _executeOfficerProposalIfReady(proposalId);
    }

    function setPaused(bool nextPaused) external onlyOwner {
        paused = nextPaused;
        emit Paused(nextPaused);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero owner");
        address oldOwner = owner;
        owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }

    function _installOfficers(address[4] memory officers_) private {
        for (uint256 index = 0; index < OFFICER_COUNT; index += 1) {
            address officer = officers_[index];
            require(officer != address(0), "zero officer");
            require(!isOfficer[officer], "duplicate officer");
            officers[index] = officer;
            isOfficer[officer] = true;
        }
    }

    function _createOfficerProposal(bytes32 action, address account, bytes32 value) private returns (uint256 proposalId) {
        require(isKnownOfficerAction(action), "unknown action");
        proposalId = nextOfficerProposalId;
        nextOfficerProposalId += 1;

        uint8 secondsRequired = actionSecondsRequired[action];
        OfficerProposal storage proposal = _officerProposals[proposalId];
        proposal.action = action;
        proposal.account = account;
        proposal.value = value;
        proposal.proposer = msg.sender;
        proposal.secondsRequired = secondsRequired;
        officerProposalApprovals[proposalId][msg.sender] = true;

        emit OfficerProposalCreated(proposalId, action, account, value, msg.sender, secondsRequired);
    }

    function _executeOfficerProposalIfReady(uint256 proposalId) private returns (bool executed) {
        OfficerProposal storage proposal = _officerProposals[proposalId];
        if (proposal.executed) {
            return true;
        }
        if (proposal.secondsReceived < proposal.secondsRequired) {
            return false;
        }

        proposal.executed = true;
        if (proposal.action == ACTION_AUTHORIZE_BRIDGE_CONTROLLER) {
            _authorizeBridgeController(proposal.account, proposal.proposer);
        } else if (proposal.action == ACTION_RETIRE_BRIDGE_CONTROLLER) {
            _retireBridgeController(proposal.account, proposal.proposer);
        } else if (proposal.action == ACTION_SET_ACTION_SECONDS_REQUIRED) {
            _setActionSecondsRequired(proposal.value, proposal.requestedSecondsRequired);
        } else {
            revert("unknown action");
        }

        emit OfficerProposalExecuted(proposalId, proposal.action);
        return true;
    }

    function _authorizeBridgeController(address controller, address officer) private {
        require(controller != address(0), "zero bridge");
        if (!authorizedBridgeControllers[controller]) {
            authorizedBridgeControllers[controller] = true;
            authorizedBridgeControllerCount += 1;
            emit BridgeControllerAuthorized(controller, officer);
        }
    }

    function _retireBridgeController(address controller, address officer) private {
        require(controller != address(0), "zero bridge");
        if (!authorizedBridgeControllers[controller]) {
            return;
        }
        require(authorizedBridgeControllerCount > 1, "last bridge");
        authorizedBridgeControllers[controller] = false;
        authorizedBridgeControllerCount -= 1;
        if (bridgeController == controller) {
            address oldBridgeController = bridgeController;
            bridgeController = address(0);
            emit BridgeControllerUpdated(oldBridgeController, address(0));
        }
        emit BridgeControllerRetired(controller, officer);
    }

    function _setActionSecondsRequired(bytes32 action, uint8 secondsRequired) private {
        require(isKnownOfficerAction(action), "unknown action");
        require(secondsRequired <= MAX_SECONDS_REQUIRED, "seconds too high");
        uint8 oldSecondsRequired = actionSecondsRequired[action];
        actionSecondsRequired[action] = secondsRequired;
        emit ActionSecondsRequiredUpdated(action, oldSecondsRequired, secondsRequired);
    }

    receive() external payable {
        revert("use depositFor");
    }
}
