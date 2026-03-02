/**
 * EXEC-006: Lido encode module.
 *
 * Lightweight protocol encoder for Lido staking operations.
 * ABI definitions + encode functions only. TransactionBuilder handles execution.
 */

import { type Address, type Hex, encodeFunctionData, parseAbi } from 'viem';

// ── ABIs ──────────────────────────────────────────

export const LIDO_STETH_ABI = parseAbi([
  'function submit(address _referral) payable returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function approve(address _spender, uint256 _amount) returns (bool)',
  'function allowance(address _owner, address _spender) view returns (uint256)',
]);

export const WSTETH_ABI = parseAbi([
  'function wrap(uint256 _stETHAmount) returns (uint256)',
  'function unwrap(uint256 _wstETHAmount) returns (uint256)',
  'function balanceOf(address _account) view returns (uint256)',
  'function getWstETHByStETH(uint256 _stETHAmount) view returns (uint256)',
  'function getStETHByWstETH(uint256 _wstETHAmount) view returns (uint256)',
]);

// ── Addresses ──────────────────────────────────────

/** Lido stETH on Sepolia. */
export const STETH_ADDRESS: Address =
  (process.env.LIDO_STETH_ADDRESS as Address | undefined)
  ?? '0x3e3FE7dBc6B4C189E7128855dD526361c49b40Af';

/** Wrapped stETH on Sepolia. */
export const WSTETH_ADDRESS: Address =
  (process.env.LIDO_WSTETH_ADDRESS as Address | undefined)
  ?? '0xB82381A3fBD3FaFA77B3a7bE693342618240067b';

// ── Encoders ──────────────────────────────────────

export function encodeStake(referral?: Address): Hex {
  return encodeFunctionData({
    abi: LIDO_STETH_ABI,
    functionName: 'submit',
    args: [referral ?? '0x0000000000000000000000000000000000000000' as Address],
  });
}

export function encodeWrap(stethAmount: bigint): Hex {
  return encodeFunctionData({
    abi: WSTETH_ABI,
    functionName: 'wrap',
    args: [stethAmount],
  });
}

export function encodeUnwrap(wstethAmount: bigint): Hex {
  return encodeFunctionData({
    abi: WSTETH_ABI,
    functionName: 'unwrap',
    args: [wstethAmount],
  });
}
