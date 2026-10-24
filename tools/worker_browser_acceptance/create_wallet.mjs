// The only authority for disposable Chromium-test wallet creation.
// Never log a mnemonic, private key or wallet password.
import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { Wallet } from 'ethers'

const dest = process.env.MC_WORKER_TEST_WALLET_FILE
if (!dest || !resolve(dest).endsWith('wallet.json')) {
  throw new Error('MC_WORKER_TEST_WALLET_FILE must point to the dedicated wallet.json')
}
const file = resolve(dest)
const rotate = process.argv.includes('--rotate')

// Synpress currently resolves ethers v5, whose wallet recovery method is
// fromMnemonic(). Accept v6's fromPhrase() as well, so a stale local install
// cannot break validation of a previously generated disposable wallet.
function addressFromMnemonic(phrase) {
  if (typeof Wallet.fromMnemonic === 'function') return Wallet.fromMnemonic(phrase).address
  if (typeof Wallet.fromPhrase === 'function') return Wallet.fromPhrase(phrase).address
  throw new Error('Installed ethers does not support mnemonic wallet recovery')
}
if (!rotate && existsSync(file)) {
  const saved = JSON.parse(readFileSync(file, 'utf8'))
  if (saved.version !== 1 || !saved.mnemonic || !saved.password ||
      addressFromMnemonic(saved.mnemonic).toLowerCase() !== saved.address?.toLowerCase()) {
    throw new Error('Existing test wallet is invalid; refusing to replace it automatically')
  }
  console.log('Reusing disposable test wallet:', saved.address)
  process.exit(0)
}
mkdirSync(dirname(file), { recursive: true, mode: 0o700 })
const wallet = Wallet.createRandom() // ethers uses a cryptographically secure random source
if (!wallet.mnemonic?.phrase) throw new Error('Wallet generation did not provide a mnemonic')
const secret = {
  version: 1,
  address: wallet.address,
  mnemonic: wallet.mnemonic.phrase,
  password: `Test@${randomBytes(24).toString('base64url')}`,
}
const temporary = `${file}.${process.pid}.${randomBytes(8).toString('hex')}.tmp`
try {
  writeFileSync(temporary, `${JSON.stringify(secret)}\n`, { flag: 'wx', mode: 0o600 })
  renameSync(temporary, file)
} finally {
  if (existsSync(temporary)) unlinkSync(temporary)
}
console.log('Generated disposable test wallet:', wallet.address)
