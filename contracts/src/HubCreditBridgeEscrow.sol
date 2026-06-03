// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title HubCreditBridgeEscrow
/// @notice Native-asset escrow for Compute Credits controlled by a bridge ledger.
/// @dev The contract does not know request-level AI charges. Users deposit once,
/// the bridge rectifies aggregate internal spend when needed, and the bridge
/// releases reconciled unused escrow back to the user. This avoids one public
/// chain transaction per AI request while still preventing over-withdrawal.
contract HubCreditBridgeEscrow {
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
    event Paused(bool paused);
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);

    address public owner;
    address public bridgeController;
    bool public paused;

    mapping(address => AccountEscrow) private _accounts;
    mapping(bytes32 => DepositRecord) private _deposits;
    mapping(address => uint256) private _completedDepositUnits;
    mapping(bytes32 => ActionRecord) private _rectifications;
    mapping(bytes32 => ActionRecord) private _withdrawals;

    bool private _locked;

    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    modifier onlyBridge() {
        require(msg.sender == bridgeController, "only bridge");
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

    constructor(address bridgeController_) {
        require(bridgeController_ != address(0), "zero bridge");
        owner = msg.sender;
        bridgeController = bridgeController_;
        emit OwnershipTransferred(address(0), msg.sender);
        emit BridgeControllerUpdated(address(0), bridgeController_);
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
    /// @dev This is the on-chain idempotency point for Hub wallet-funding credit.
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

    function setBridgeController(address newBridgeController) external onlyOwner {
        require(newBridgeController != address(0), "zero bridge");
        address oldBridgeController = bridgeController;
        bridgeController = newBridgeController;
        emit BridgeControllerUpdated(oldBridgeController, newBridgeController);
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

    receive() external payable {
        revert("use depositFor");
    }
}
