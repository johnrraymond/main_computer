// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/XLagBridgeReserve.sol";

contract DeployXLagBridgeReserve {
    function deploy(address[4] memory offices, uint256 maxPayoutWei, uint64 payoutDelayBlocks, uint64 resetDelayBlocks)
        external
        returns (XLagBridgeReserve)
    {
        return new XLagBridgeReserve(offices, maxPayoutWei, payoutDelayBlocks, resetDelayBlocks);
    }
}
