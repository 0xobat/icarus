/**
 * Deployment script for AllowlistGuard contract.
 *
 * Usage:
 *   npx ts-node scripts/deploy-guard.ts
 *
 * Required env vars:
 *   WALLET_PRIVATE_KEY   — Deployer EOA private key
 *   ALCHEMY_BASE_HTTP_URL — Base mainnet RPC URL
 *   SAFE_ADDRESS          — Safe address (guard owner will be set to this)
 *
 * The script:
 *   1. Deploys AllowlistGuard with permitted Base contracts
 *   2. Logs the deployed address
 *   3. Outputs the setGuard() calldata for attaching to the Safe
 */

import {
  createWalletClient,
  createPublicClient,
  http,
  type Address,
  encodeFunctionData,
} from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { base } from 'viem/chains';

// ── Permitted contracts on Base ──────────────────

const BASE_ALLOWLIST: Address[] = [
  '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5', // Aave V3 Pool
  '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43', // Aerodrome Router
  '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', // USDC
  '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA', // USDbC
  '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb', // DAI
];

// ── AllowlistGuard ABI (subset needed for deployment + setGuard) ──

const GUARD_ABI = [
  {
    type: 'constructor',
    inputs: [
      { name: '_owner', type: 'address' },
      { name: '_initialAllowlist', type: 'address[]' },
    ],
  },
] as const;

const SAFE_ABI = [
  {
    type: 'function',
    name: 'setGuard',
    inputs: [{ name: 'guard', type: 'address' }],
    outputs: [],
    stateMutability: 'nonpayable',
  },
] as const;

// ── Compiled bytecode placeholder ──
// Replace with actual compiled bytecode from: solc --bin contracts/AllowlistGuard.sol
const GUARD_BYTECODE = '0x__REPLACE_WITH_COMPILED_BYTECODE__' as `0x${string}`;

async function main() {
  const privateKey = process.env.WALLET_PRIVATE_KEY;
  const rpcUrl = process.env.ALCHEMY_BASE_HTTP_URL;
  const safeAddress = process.env.SAFE_ADDRESS as Address | undefined;

  if (!privateKey || !rpcUrl || !safeAddress) {
    console.error('Missing required env vars: WALLET_PRIVATE_KEY, ALCHEMY_BASE_HTTP_URL, SAFE_ADDRESS');
    process.exit(1);
  }

  const account = privateKeyToAccount(privateKey as `0x${string}`);
  const publicClient = createPublicClient({ chain: base, transport: http(rpcUrl) });
  const walletClient = createWalletClient({
    account,
    chain: base,
    transport: http(rpcUrl),
  });

  console.log('Deploying AllowlistGuard...');
  console.log(`  Owner (Safe): ${safeAddress}`);
  console.log(`  Allowlist: ${BASE_ALLOWLIST.length} addresses`);

  // Deploy the guard contract
  const hash = await walletClient.deployContract({
    abi: GUARD_ABI,
    bytecode: GUARD_BYTECODE,
    args: [safeAddress, BASE_ALLOWLIST],
  });

  console.log(`  TX hash: ${hash}`);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });

  if (!receipt.contractAddress) {
    console.error('Deployment failed — no contract address in receipt');
    process.exit(1);
  }

  console.log(`  Guard deployed at: ${receipt.contractAddress}`);

  // Generate setGuard calldata for the Safe
  const setGuardData = encodeFunctionData({
    abi: SAFE_ABI,
    functionName: 'setGuard',
    args: [receipt.contractAddress],
  });

  console.log('\nTo attach guard to Safe, execute this transaction from the Safe:');
  console.log(`  to: ${safeAddress}`);
  console.log(`  data: ${setGuardData}`);
  console.log(`  value: 0`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
