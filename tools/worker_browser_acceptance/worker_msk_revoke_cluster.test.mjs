import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve, dirname } from 'node:path'
import vm from 'node:vm'

// Execute the production revocation handler, without a live wallet or Hub.
const dir = dirname(fileURLToPath(import.meta.url))
const worker = readFileSync(resolve(dir, '../../main_computer/web/applications/scripts/worker.js'),'utf8')
const begin = worker.indexOf('    async function revokeWorkerMultisessionKey(event) {')
const end = worker.indexOf('    function workerNetworkKey(', begin)
assert.ok(begin>=0 && end>begin)
const source = worker.slice(begin,end).trim()

async function run({keyCluster='',hubCluster='',hubVerified=true,hubUrl='https://testnet-hub.greatlibrary.io'}={}) {
  const key={id:'msk_approvedkey',status:'active',walletAddress:'0x'+'ab'.repeat(20),hubUrl,clusterId:keyCluster}
  let signed=null, posted=null
  const status={textContent:''}
  const context={
    window:{MainComputerWalletLibrary:{revokeMultiSessionKeySignature:async args=>{
      signed=args;return {signature:'0xmock',message:{action:'revoke_multisession_key',
        key_id:args.keyId,cluster_id:args.requestContext.cluster_id}}
    }}},
    workerBridgeState:{wallet:{connected:true,address:'0x'+'ab'.repeat(20)},multisessionKeys:[key]},
    workerNetworkSession:{connection_status:'connected',connected_hub_url:hubUrl,
      hub_status:hubVerified?{reachable:true,protocol:'stable-hub-identity-v1',status:{cluster_id:hubCluster}}:null},
    workerSaveStatus:status,
    loadWorkerBridgeState:()=>{},
    workerActiveMultisessionKey:()=>key,
    workerWalletValidAddress:value=>/^0x[0-9a-f]{40}$/.test(value),
    workerBuildMultisessionRequestContext:()=>({wallet_address:key.walletAddress,chain_id:'0x28757b1'}),
    workerSelectedHubUrl:()=>hubUrl,
    workerNormalizeHubUrl:value=>String(value).replace(/\/+$/,''),
    workerMultisessionKeyMatchesSelectedHub:k=>k.hubUrl===hubUrl,
    workerNowIso:()=>new Date().toISOString(),
    workerPostJson:async(_url,payload)=>{posted=payload;return {revoked:true,hub_revoke:{ok:true},keys:[]}},
    workerMergeLoadedMultisessionKeys:()=>{},
    saveWorkerBridgeState:()=>{}, renderWorkerBridgeReadiness:()=>{},
    console:{info:()=>{},warn:()=>{}},
    workerMultisessionInFlight:false,
    workerErrorText:e=>String(e?.message||e),
  }
  const handler=vm.runInNewContext(`(${source})`,context)
  await handler({preventDefault(){},stopPropagation(){}})
  return {signed,posted,status:status.textContent}
}

test('preexisting cached key signs against backend-verified Hub cluster',async()=>{
  const result=await run({keyCluster:'',hubCluster:'testnet-cluster'})
  assert.equal(result.signed?.requestContext.cluster_id,'testnet-cluster')
  assert.equal(result.posted?.signed_request.message.cluster_id,'testnet-cluster')
})
test('issued key signs original exact Hub cluster when status is unavailable',async()=>{
  const result=await run({keyCluster:'testnet-cluster',hubVerified:false})
  assert.equal(result.signed?.requestContext.cluster_id,'testnet-cluster')
})
test('mismatch is refused before wallet signature',async()=>{
  const result=await run({keyCluster:'wrong-cluster',hubCluster:'testnet-cluster'})
  assert.equal(result.signed,null)
  assert.equal(result.posted,null)
  assert.match(result.status,/differs from the verified Hub cluster/)
})
test('missing cluster is refused before wallet signature',async()=>{
  const result=await run({keyCluster:'',hubVerified:false})
  assert.equal(result.signed,null)
  assert.equal(result.posted,null)
  assert.match(result.status,/no verified cluster ID/)
})
