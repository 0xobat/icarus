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
  getAddress,
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

// ── AllowlistGuard ABI ──

const GUARD_ABI = [
  {
    type: 'constructor',
    inputs: [
      { name: '_owner', type: 'address' },
      { name: '_initialAllowlist', type: 'address[]' },
    ],
  },
  {
    type: 'function',
    name: 'owner',
    inputs: [],
    outputs: [{ name: '', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    name: 'allowlisted',
    inputs: [{ name: '', type: 'address' }],
    outputs: [{ name: '', type: 'bool' }],
    stateMutability: 'view',
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

// ── Compiled bytecode (solc 0.8.34, no optimization) ──
// Regenerate with: npx solc --bin contracts/AllowlistGuard.sol --output-dir /tmp/solc-out
const GUARD_BYTECODE = '0x608060405234801561000f575f5ffd5b5060405161117b38038061117b8339818101604052810190610031919061037e565b5f73ffffffffffffffffffffffffffffffffffffffff168273ffffffffffffffffffffffffffffffffffffffff1603610096576040517fd92e233d00000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b815f5f6101000a81548173ffffffffffffffffffffffffffffffffffffffff021916908373ffffffffffffffffffffffffffffffffffffffff1602179055505f5f90505b81518110156101bb576001805f8484815181106100fa576100f96103d8565b5b602002602001015173ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff1681526020019081526020015f205f6101000a81548160ff021916908315150217905550818181518110610164576101636103d8565b5b602002602001015173ffffffffffffffffffffffffffffffffffffffff167fa226db3f664042183ee0281230bba26cbf7b5057e50aee7f25a175ff45ce4d7f60405160405180910390a280806001019150506100da565b505050610405565b5f604051905090565b5f5ffd5b5f5ffd5b5f73ffffffffffffffffffffffffffffffffffffffff82169050919050565b5f6101fd826101d4565b9050919050565b61020d816101f3565b8114610217575f5ffd5b50565b5f8151905061022881610204565b92915050565b5f5ffd5b5f601f19601f8301169050919050565b7f4e487b71000000000000000000000000000000000000000000000000000000005f52604160045260245ffd5b61027882610232565b810181811067ffffffffffffffff8211171561029757610296610242565b5b80604052505050565b5f6102a96101c3565b90506102b5828261026f565b919050565b5f67ffffffffffffffff8211156102d4576102d3610242565b5b602082029050602081019050919050565b5f5ffd5b5f6102fb6102f6846102ba565b6102a0565b9050808382526020820190506020840283018581111561031e5761031d6102e5565b5b835b818110156103475780610333888261021a565b845260208401935050602081019050610320565b5050509392505050565b5f82601f8301126103655761036461022e565b5b81516103758482602086016102e9565b91505092915050565b5f5f60408385031215610394576103936101cc565b5b5f6103a18582860161021a565b925050602083015167ffffffffffffffff8111156103c2576103c16101d0565b5b6103ce85828601610351565b9150509250929050565b7f4e487b71000000000000000000000000000000000000000000000000000000005f52603260045260245ffd5b610d69806104125f395ff3fe608060405234801561000f575f5ffd5b5060043610610086575f3560e01c806375f0bb521161005957806375f0bb521461010e5780638da5cb5b1461012a5780639327136814610148578063f2fde38b1461016457610086565b806303f45d411461008a5780633628731c146100ba57806338eada1c146100d65780634ba79dfe146100f2575b5f5ffd5b6100a4600480360381019061009f9190610838565b610180565b6040516100b1919061087d565b60405180910390f35b6100d460048036038101906100cf91906108f7565b61019d565b005b6100f060048036038101906100eb9190610838565b610329565b005b61010c60048036038101906101079190610838565b610448565b005b61012860048036038101906101239190610b1e565b610567565b005b6101326105ff565b60405161013f9190610c52565b60405180910390f35b610162600480360381019061015d9190610cc8565b610623565b005b61017e60048036038101906101799190610838565b610627565b005b6001602052805f5260405f205f915054906101000a900460ff1681565b5f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff163373ffffffffffffffffffffffffffffffffffffffff1614610222576040517f30cd747100000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b5f5f90505b82829050811015610324576001805f85858581811061024957610248610d06565b5b905060200201602081019061025e9190610838565b73ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff1681526020019081526020015f205f6101000a81548160ff0219169083151502179055508282828181106102c0576102bf610d06565b5b90506020020160208101906102d59190610838565b73ffffffffffffffffffffffffffffffffffffffff167fa226db3f664042183ee0281230bba26cbf7b5057e50aee7f25a175ff45ce4d7f60405160405180910390a28080600101915050610227565b505050565b5f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff163373ffffffffffffffffffffffffffffffffffffffff16146103ae576040517f30cd747100000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b6001805f8373ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff1681526020019081526020015f205f6101000a81548160ff0219169083151502179055508073ffffffffffffffffffffffffffffffffffffffff167fa226db3f664042183ee0281230bba26cbf7b5057e50aee7f25a175ff45ce4d7f60405160405180910390a250565b5f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff163373ffffffffffffffffffffffffffffffffffffffff16146104cd576040517f30cd747100000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b5f60015f8373ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff1681526020019081526020015f205f6101000a81548160ff0219169083151502179055508073ffffffffffffffffffffffffffffffffffffffff167f24a12366c02e13fe4a9e03d86a8952e85bb74a456c16e4a18b6d8295700b74bb60405160405180910390a250565b60015f8c73ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff1681526020019081526020015f205f9054906101000a900460ff166105f2578a6040517f6f7a52750000000000000000000000000000000000000000000000000000000081526004016105e99190610c52565b60405180910390fd5b5050505050505050505050565b5f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1681565b5050565b5f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff163373ffffffffffffffffffffffffffffffffffffffff16146106ac576040517f30cd747100000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b5f73ffffffffffffffffffffffffffffffffffffffff168173ffffffffffffffffffffffffffffffffffffffff1603610711576040517fd92e233d00000000000000000000000000000000000000000000000000000000815260040160405180910390fd5b8073ffffffffffffffffffffffffffffffffffffffff165f5f9054906101000a900473ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff167f8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e060405160405180910390a3805f5f6101000a81548173ffffffffffffffffffffffffffffffffffffffff021916908373ffffffffffffffffffffffffffffffffffffffff16021790555050565b5f604051905090565b5f5ffd5b5f5ffd5b5f73ffffffffffffffffffffffffffffffffffffffff82169050919050565b5f610807826107de565b9050919050565b610817816107fd565b8114610821575f5ffd5b50565b5f813590506108328161080e565b92915050565b5f6020828403121561084d5761084c6107d6565b5b5f61085a84828501610824565b91505092915050565b5f8115159050919050565b61087781610863565b82525050565b5f6020820190506108905f83018461086e565b92915050565b5f5ffd5b5f5ffd5b5f5ffd5b5f5f83601f8401126108b7576108b6610896565b5b8235905067ffffffffffffffff8111156108d4576108d361089a565b5b6020830191508360208202830111156108f0576108ef61089e565b5b9250929050565b5f5f6020838503121561090d5761090c6107d6565b5b5f83013567ffffffffffffffff81111561092a576109296107da565b5b610936858286016108a2565b92509250509250929050565b5f819050919050565b61095481610942565b811461095e575f5ffd5b50565b5f8135905061096f8161094b565b92915050565b5f5ffd5b5f601f19601f8301169050919050565b7f4e487b71000000000000000000000000000000000000000000000000000000005f52604160045260245ffd5b6109bf82610979565b810181811067ffffffffffffffff821117156109de576109dd610989565b5b80604052505050565b5f6109f06107cd565b90506109fc82826109b6565b919050565b5f67ffffffffffffffff821115610a1b57610a1a610989565b5b610a2482610979565b9050602081019050919050565b828183375f83830152505050565b5f610a51610a4c84610a01565b6109e7565b905082815260208101848484011115610a6d57610a6c610975565b5b610a78848285610a31565b509392505050565b5f82601f830112610a9457610a93610896565b5b8135610aa4848260208601610a3f565b91505092915050565b5f60ff82169050919050565b610ac281610aad565b8114610acc575f5ffd5b50565b5f81359050610add81610ab9565b92915050565b5f610aed826107de565b9050919050565b610afd81610ae3565b8114610b07575f5ffd5b50565b5f81359050610b1881610af4565b92915050565b5f5f5f5f5f5f5f5f5f5f5f6101608c8e031215610b3e57610b3d6107d6565b5b5f610b4b8e828f01610824565b9b50506020610b5c8e828f01610961565b9a505060408c013567ffffffffffffffff811115610b7d57610b7c6107da565b5b610b898e828f01610a80565b9950506060610b9a8e828f01610acf565b9850506080610bab8e828f01610961565b97505060a0610bbc8e828f01610961565b96505060c0610bcd8e828f01610961565b95505060e0610bde8e828f01610824565b945050610100610bf08e828f01610b0a565b9350506101208c013567ffffffffffffffff811115610c1257610c116107da565b5b610c1e8e828f01610a80565b925050610140610c308e828f01610824565b9150509295989b509295989b9093969950565b610c4c816107fd565b82525050565b5f602082019050610c655f830184610c43565b92915050565b5f819050919050565b610c7d81610c6b565b8114610c87575f5ffd5b50565b5f81359050610c9881610c74565b92915050565b610ca781610863565b8114610cb1575f5ffd5b50565b5f81359050610cc281610c9e565b92915050565b5f5f60408385031215610cde57610cdd6107d6565b5b5f610ceb85828601610c8a565b9250506020610cfc85828601610cb4565b9150509250929050565b7f4e487b71000000000000000000000000000000000000000000000000000000005f52603260045260245ffdfea264697066735822122066ce5c51c78126ce3e8b709135b3032e215ed500c64adfbfe5f585d79e5b748a64736f6c63430008220033' as `0x${string}`;

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

/**
 * Reads on-chain guard state and verifies it matches the expected allowlist.
 * Checks: owner matches Safe, every BASE_ALLOWLIST address is allowlisted on-chain.
 *
 * @param guardAddress - Deployed AllowlistGuard contract address
 * @param expectedOwner - Expected owner (Safe address)
 * @param rpcUrl - Base mainnet RPC URL
 * @returns Object with `ok` boolean and list of `errors`
 */
export async function verifyGuardMatchesAllowlist(
  guardAddress: Address,
  expectedOwner: Address,
  rpcUrl: string,
): Promise<{ ok: boolean; errors: string[] }> {
  const publicClient = createPublicClient({ chain: base, transport: http(rpcUrl) });
  const errors: string[] = [];

  // Verify owner
  const onChainOwner = await publicClient.readContract({
    address: guardAddress,
    abi: GUARD_ABI,
    functionName: 'owner',
  });

  if (getAddress(onChainOwner) !== getAddress(expectedOwner)) {
    errors.push(`Owner mismatch: on-chain=${onChainOwner}, expected=${expectedOwner}`);
  }

  // Verify each allowlisted address
  for (const addr of BASE_ALLOWLIST) {
    const isAllowlisted = await publicClient.readContract({
      address: guardAddress,
      abi: GUARD_ABI,
      functionName: 'allowlisted',
      args: [addr],
    });

    if (!isAllowlisted) {
      errors.push(`Address not allowlisted on-chain: ${addr}`);
    }
  }

  return { ok: errors.length === 0, errors };
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
