// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/HubCreditSale.sol";

contract HubCreditSaleBuyer {
    function purchase(HubCreditSale sale, address account, uint256 credits, string calldata memo) external payable returns (bytes32) {
        return sale.purchase{value: msg.value}(account, credits, memo);
    }

    function setWeiPerCredit(HubCreditSale sale, uint256 price) external {
        sale.setWeiPerCredit(price);
    }

    receive() external payable {}
}

contract HubCreditSaleTest {
    HubCreditSaleBuyer private treasury = new HubCreditSaleBuyer();
    HubCreditSaleBuyer private buyer = new HubCreditSaleBuyer();

    function testQuoteUsesConfiguredPrice() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        assertEq(sale.quote(125), 1.25 ether);
    }

    function testPurchaseForwardsPaymentAndRecordsNonce() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        uint256 beforeTreasury = address(treasury).balance;

        bytes32 purchaseId = buyer.purchase{value: 0.02 ether}(sale, address(buyer), 2, "unit purchase");

        assertTrue(purchaseId != bytes32(0));
        assertEq(address(treasury).balance, beforeTreasury + 0.02 ether);
        assertEq(sale.nextPurchaseNonce(), 2);
    }

    function testPurchaseRefundsOverpayment() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        uint256 beforeBuyer = address(buyer).balance;

        buyer.purchase{value: 0.03 ether}(sale, address(buyer), 1, "refund test");

        assertEq(address(treasury).balance, 0.01 ether);
        assertEq(address(buyer).balance, beforeBuyer + 0.02 ether);
    }

    function testPurchaseRejectsUnderpayment() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        try buyer.purchase{value: 0.009 ether}(sale, address(buyer), 1, "underpaid") {
            revert("expected underpayment rejection");
        } catch {}
    }

    function testOwnerCanPausePurchases() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        sale.setPaused(true);
        try buyer.purchase{value: 0.01 ether}(sale, address(buyer), 1, "paused") {
            revert("expected pause rejection");
        } catch {}
    }

    function testNonOwnerCannotChangePrice() public {
        HubCreditSale sale = new HubCreditSale(payable(address(treasury)), 0.01 ether);
        try buyer.setWeiPerCredit(sale, 0.02 ether) {
            revert("expected external owner rejection");
        } catch {}
    }

    function assertEq(uint256 left, uint256 right) private pure {
        require(left == right, "uint mismatch");
    }

    function assertTrue(bool value) private pure {
        require(value, "assert true failed");
    }
}
