import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'

const source=readFileSync(new URL('../../main_computer/web/applications/scripts/worker.js',import.meta.url),'utf8')
const functionBody=source.split('    function buildWorkerNetworkRegistrationPayload(',2)[1]
  ?.split('    function workerNetworkRegistrationReadyForSelected(',1)[0]
assert.ok(functionBody,'the production Worker registration payload builder must exist')
const builder='function buildWorkerNetworkRegistrationPayload('+functionBody
const HUB='https://testnet-hub.greatlibrary.io'
const WALLET='0x'+'a'.repeat(40)
const OTHER='0x'+'b'.repeat(40)
const KEY='msk-issued-test-only'

function createPayload(active, explicit=null, staleSessionId='msk-never-hydrated'){
  const session={selected_network:'testnet',profile:{hub_url:HUB,chain_id:'42424241'},
    requested_ring:'3',active_multisession_key_id:staleSessionId}
  const context={
    workerNetworkSession:session, WORKER_DEFAULT_RING:'3',
    workerNetworkKey:key=>key,
    workerNetworkProfile:()=>session.profile,
    workerLowerAddress:text=>String(text||'').toLowerCase(),
    workerNetworkWalletAddress:()=>WALLET,
    buildWorkerOfferRegistrationPayload:()=>({hub_url:HUB,worker:{models:['test'],capabilities:{}}}),
    workerActiveMultisessionKey:()=>active,
    workerMultisessionKeyMatchesWallet:(key,wallet)=>key.walletAddress.toLowerCase()===wallet.toLowerCase(),
    workerMultisessionKeyMatchesSelectedHub:key=>key.hubUrl===HUB,
  }
  vm.createContext(context)
  vm.runInContext(builder,context)
  return context.buildWorkerNetworkRegistrationPayload({walletAddress:WALLET,
    ...(explicit===null?{}:{activeMultisessionKey:explicit})})
}

test('newly issued, wallet-and-Hub matched key id travels with offer registration',()=>{
  const p=createPayload({id:KEY,walletAddress:WALLET,hubUrl:HUB,status:'active'})
  assert.equal(p.active_multisession_key_id,KEY)
  assert.equal(p.multisession_key_id,KEY)
})
test('redacted key does not leak local reference or stale session id; private server must resolve it',()=>{
  const p=createPayload({id:'',localRef:'server-side-msk|secret',walletAddress:WALLET,hubUrl:HUB,status:'active'})
  assert.equal(Object.hasOwn(p,'active_multisession_key_id'),false)
  assert.equal(Object.hasOwn(p,'multisession_key_id'),false)
})
test('no active key never manufactures a key id from a stale network session',()=>{
  assert.equal(Object.hasOwn(createPayload(null),'active_multisession_key_id'),false)
})
test('wrong wallet, foreign Hub, or inactive key cannot be sent as offer bearer',()=>{
  for(const key of [
    {id:KEY,walletAddress:OTHER,hubUrl:HUB,status:'active'},
    {id:KEY,walletAddress:WALLET,hubUrl:'https://mainnet-hub.greatlibrary.io',status:'active'},
    {id:KEY,walletAddress:WALLET,hubUrl:HUB,status:'revoked'},
  ])assert.equal(Object.hasOwn(createPayload(key),'active_multisession_key_id'),false)
})
test('explicit work-now key must pass the same wallet, Hub and active checks',()=>{
  const foreign={id:KEY,walletAddress:OTHER,hubUrl:HUB,status:'active'}
  assert.equal(Object.hasOwn(createPayload(null,foreign),'active_multisession_key_id'),false)
  const valid={id:KEY,walletAddress:WALLET,hubUrl:HUB,status:'active'}
  assert.equal(createPayload(null,valid).active_multisession_key_id,KEY)
})
