// Regression: the native MCEL Worker embeds wallet.js WITHOUT wallet.html's
// global ethers UMD loader. Connect Wallet imports ethers as ESM instead.
// Run with ordinary node --test. The import URL is replaced in-memory by a
// local ESM fixture, so no CDN or browser wallet is contacted.
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {webcrypto} from 'node:crypto'

const source = readFileSync(new URL('../../main_computer/web/applications/scripts/wallet.js', import.meta.url), 'utf8')
const workerSource = readFileSync(new URL('../../main_computer/web/applications/scripts/worker.js', import.meta.url), 'utf8')
const address = '0x1234567890abcdef1234567890abcdef12345678'
const pinnedModuleUrl = 'https://cdn.jsdelivr.net/npm/ethers@6.13.4/+esm'
const fakeEsmUrl = 'data:text/javascript,' + encodeURIComponent(`
  export class BrowserProvider {
    constructor(provider, network) { this.provider=provider; this.network=network; }
    async getSigner() { return this.provider.signer; }
    async getNetwork() { return {chainId:BigInt(this.provider.chainId)}; }
  }
`)

function loadWallet({globalEthers=false,chainId='0x28757b1'}={}) {
  const calls = {signed:[],network:chainId}
  const signer = {
    async getAddress() { return address },
    async signMessage(text) { calls.signed.push(JSON.parse(text)); return '0xmock-test-signature' }
  }
  const injected = {chainId, signer}
  const window = {
    ethereum:injected, crypto:webcrypto,
    location:{origin:'http://127.0.0.1:8765'},
    ...(globalEthers?{ethers:{BrowserProvider:class {
      constructor(provider) {this.provider=provider}
      async getSigner(){return this.provider.signer}
      async getNetwork(){return {chainId:BigInt(this.provider.chainId)}}
    }}}:{})
  }
  const assignment = `const WALLET_WORKER_ETHERS_ESM_URL = "${pinnedModuleUrl}";`
  assert.ok(source.includes(assignment))
  const testSource = source.replace(assignment,
    `const WALLET_WORKER_ETHERS_ESM_URL = ${JSON.stringify(fakeEsmUrl)};`)
  // The classic wallet.js script is evaluated with the exact mocked loader
  // substitution. All signing preconditions and message construction run.
  new Function('window','TextEncoder','Date','console',testSource)(window,TextEncoder,Date,console)
  return {calls,window}
}
const requestContext = {wallet_address:address,chain_id:'0x28757b1'}

test('native MCEL Worker shares the same pinned ethers module as the MSK signer',()=>{
  assert.ok(workerSource.includes(`"${pinnedModuleUrl}"`))
  assert.match(source,/walletMultisessionBrowserProvider\(\)/)
})

test('MSK signs on Testnet without global window.ethers (native MCEL route)',async()=>{
  const {window,calls} = loadWallet()
  assert.equal(window.ethers,undefined)
  const signed=await window.MainComputerWalletLibrary.requestMultiSessionKeySignature({
    requestContext,origin:'http://127.0.0.1:8765'
  })
  assert.equal(calls.signed.length,1)
  assert.equal(calls.signed[0].chain_id,'0x28757b1')
  assert.equal(signed.message.chain_id,'0x28757b1')
  assert.equal(signed.message.wallet_address,address)
  assert.equal(signed.signature,'0xmock-test-signature')
  await window.MainComputerWalletLibrary.requestMultiSessionKeySignature({
    requestContext,origin:'http://127.0.0.1:8765'
  })
  assert.equal(calls.signed.length,2,'both requests reach signing through the ESM loader')
})

test('standalone Wallet continues using its existing global ethers loader',async()=>{
  const {window,calls} = loadWallet({globalEthers:true})
  const signed=await window.MainComputerWalletLibrary.requestMultiSessionKeySignature({requestContext})
  assert.equal(signed.message.chain_id,'0x28757b1')
  assert.equal(calls.signed.length,1)
})

test('signing refuses wrong network before opening a wallet signature',async()=>{
  const {window,calls} = loadWallet({chainId:'0x28757b2'})
  await assert.rejects(window.MainComputerWalletLibrary.requestMultiSessionKeySignature({requestContext}),
    /Wallet is on 0x28757b2; Worker requires 0x28757b1/)
  assert.equal(calls.signed.length,0)
})

test('signing refuses mismatched wallet account before signing',async()=>{
  const {window,calls} = loadWallet()
  await assert.rejects(window.MainComputerWalletLibrary.requestMultiSessionKeySignature({
    requestContext:{...requestContext,wallet_address:'0xabcdefabcdefabcdefabcdefabcdefabcdefabcd'}
  }), /does not match Worker request/)
  assert.equal(calls.signed.length,0)
})
