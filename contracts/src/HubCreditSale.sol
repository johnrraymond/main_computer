// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title HubCreditSale
/// @notice Purchase-intent receipt contract for Main Computer Compute Credits.
/// @dev This contract intentionally does not mint an ERC-20 token. It accepts
/// native payment, forwards the required payment to treasury, refunds any
/// overpayment, and emits a machine-readable receipt that the hub backend can
/// index into its internal service-credit ledger.
contract HubCreditSale {
    event CreditPurchased(
        bytes32 indexed purchaseId,
        address indexed account,
        address indexed payer,
        uint256 creditsGranted,
        uint256 amountPaidWei,
        string memo
    );
    event PriceUpdated(uint256 oldWeiPerCredit, uint256 newWeiPerCredit);
    event Paused(bool paused);
    event TreasuryUpdated(address indexed oldTreasury, address indexed newTreasury);
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);

    address public owner;
    address payable public treasury;
    uint256 public weiPerCredit;
    bool public paused;
    uint256 public nextPurchaseNonce = 1;

    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    constructor(address payable treasury_, uint256 weiPerCredit_) {
        require(treasury_ != address(0), "zero treasury");
        require(weiPerCredit_ > 0, "zero price");
        owner = msg.sender;
        treasury = treasury_;
        weiPerCredit = weiPerCredit_;
        emit OwnershipTransferred(address(0), msg.sender);
        emit TreasuryUpdated(address(0), treasury_);
        emit PriceUpdated(0, weiPerCredit_);
    }

    function quote(uint256 credits) public view returns (uint256 amountWei) {
        require(credits > 0, "zero credits");
        return credits * weiPerCredit;
    }

    function purchase(address account, uint256 credits, string calldata memo) external payable returns (bytes32 purchaseId) {
        require(!paused, "paused");
        require(account != address(0), "zero account");
        uint256 amountWei = quote(credits);
        require(msg.value >= amountWei, "underpaid");

        uint256 nonce = nextPurchaseNonce;
        nextPurchaseNonce = nonce + 1;
        purchaseId = keccak256(abi.encodePacked(block.chainid, address(this), nonce, account, msg.sender, credits, amountWei, memo));

        emit CreditPurchased(purchaseId, account, msg.sender, credits, amountWei, memo);

        (bool sentTreasury, ) = treasury.call{value: amountWei}("");
        require(sentTreasury, "treasury transfer failed");

        uint256 refund = msg.value - amountWei;
        if (refund > 0) {
            (bool sentRefund, ) = payable(msg.sender).call{value: refund}("");
            require(sentRefund, "refund failed");
        }
    }

    function setWeiPerCredit(uint256 newWeiPerCredit) external onlyOwner {
        require(newWeiPerCredit > 0, "zero price");
        uint256 oldWeiPerCredit = weiPerCredit;
        weiPerCredit = newWeiPerCredit;
        emit PriceUpdated(oldWeiPerCredit, newWeiPerCredit);
    }

    function setPaused(bool nextPaused) external onlyOwner {
        paused = nextPaused;
        emit Paused(nextPaused);
    }

    function setTreasury(address payable newTreasury) external onlyOwner {
        require(newTreasury != address(0), "zero treasury");
        address oldTreasury = treasury;
        treasury = newTreasury;
        emit TreasuryUpdated(oldTreasury, newTreasury);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero owner");
        address oldOwner = owner;
        owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }

    receive() external payable {
        revert("use purchase");
    }
}
