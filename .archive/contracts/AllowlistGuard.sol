// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AllowlistGuard
 * @notice Safe Guard module that enforces a contract allowlist at the wallet level.
 *         Only transactions targeting allowlisted addresses are permitted.
 *         Even if the application layer is compromised, the Safe cannot interact
 *         with non-allowlisted contracts on-chain.
 *
 * @dev Implements the Safe Guard interface (ITransactionGuard).
 *      - Owner can add/remove addresses from the allowlist.
 *      - checkTransaction() reverts if `to` is not allowlisted.
 *      - ETH transfers to EOAs can be allowed by adding the EOA to the allowlist.
 *
 * Permitted contracts for Base mainnet:
 *   - Aave V3 Pool:      0xA238Dd80C259a72e81d7e4664a9801593F98d1c5
 *   - Aerodrome Router:   0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43
 *   - USDC:               0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
 *   - USDbC:              0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA
 *   - DAI:                0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb
 */

/// @notice Minimal interface for Safe's Guard hook.
interface ITransactionGuard {
    function checkTransaction(
        address to,
        uint256 value,
        bytes memory data,
        uint8 operation,
        uint256 safeTxGas,
        uint256 baseGas,
        uint256 gasPrice,
        address gasToken,
        address payable refundReceiver,
        bytes memory signatures,
        address msgSender
    ) external;

    function checkAfterExecution(bytes32 txHash, bool success) external;
}

contract AllowlistGuard is ITransactionGuard {
    address public owner;
    mapping(address => bool) public allowlisted;

    event AddressAdded(address indexed addr);
    event AddressRemoved(address indexed addr);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotAllowlisted(address target);
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address _owner, address[] memory _initialAllowlist) {
        if (_owner == address(0)) revert ZeroAddress();
        owner = _owner;
        for (uint256 i = 0; i < _initialAllowlist.length; i++) {
            allowlisted[_initialAllowlist[i]] = true;
            emit AddressAdded(_initialAllowlist[i]);
        }
    }

    /// @notice Called by the Safe before every transaction. Reverts if `to` is not allowlisted.
    function checkTransaction(
        address to,
        uint256,
        bytes memory,
        uint8,
        uint256,
        uint256,
        uint256,
        address,
        address payable,
        bytes memory,
        address
    ) external view override {
        if (!allowlisted[to]) revert NotAllowlisted(to);
    }

    /// @notice Called by the Safe after transaction execution. No-op for this guard.
    function checkAfterExecution(bytes32, bool) external override {
        // intentionally empty
    }

    /// @notice Add an address to the allowlist.
    function addAddress(address addr) external onlyOwner {
        allowlisted[addr] = true;
        emit AddressAdded(addr);
    }

    /// @notice Remove an address from the allowlist.
    function removeAddress(address addr) external onlyOwner {
        allowlisted[addr] = false;
        emit AddressRemoved(addr);
    }

    /// @notice Batch add multiple addresses.
    function addAddresses(address[] calldata addrs) external onlyOwner {
        for (uint256 i = 0; i < addrs.length; i++) {
            allowlisted[addrs[i]] = true;
            emit AddressAdded(addrs[i]);
        }
    }

    /// @notice Transfer ownership of the guard contract.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
