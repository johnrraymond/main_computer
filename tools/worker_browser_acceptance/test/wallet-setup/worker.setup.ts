import { defineWalletSetup } from '@synthetixio/synpress'
import { MetaMask } from '@synthetixio/synpress/playwright'
import { readFileSync } from 'node:fs'

// The Python launcher creates this disposable wallet using ethers and stores
// it outside source control. No mnemonic or password input is required.
const walletFile = process.env.MC_WORKER_TEST_WALLET_FILE
if (!walletFile) throw new Error('Run through tools/worker_browser_acceptance.py')
const wallet = JSON.parse(readFileSync(walletFile, 'utf8')) as {
  version: number; address: string; mnemonic: string; password: string
}
if (wallet.version !== 1 || !wallet.mnemonic || !wallet.password || !wallet.address) {
  throw new Error('Disposable test wallet is missing or invalid')
}
const password = wallet.password

export default defineWalletSetup(password, async (context, walletPage) => {
  const metamask = new MetaMask(context, walletPage, password)
  await metamask.importWallet(wallet.mnemonic.trim())

  // Test wallet only. Actual RPC requests still use Cloudflare and Besu.
  await metamask.addNetwork({
    name: 'Main Computer Testnet',
    rpcUrl: 'https://testnet-rpc.greatlibrary.io',
    chainId: 42424241,
    symbol: 'MCXLAG',
  })
  await metamask.switchNetwork('Main Computer Testnet')
})
