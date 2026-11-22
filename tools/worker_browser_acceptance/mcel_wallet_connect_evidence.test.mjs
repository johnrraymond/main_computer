import test from 'node:test'
import assert from 'node:assert/strict'
import {safeWalletLifecycleEvent,safeWalletProgress,safeWalletDiagnosticSnapshot,
  safeWalletDiagnosticTimeline,safeWalletExtensionEvidence,safeWalletBackendSession} from './mcel_wallet_connect_evidence.mjs'

test('early lifecycle events retain categories, never raw details',()=>{
  assert.equal(safeWalletLifecycleEvent('connect.wallet.networkPreflight.start'),'connect.wallet.networkPreflight.start')
  assert.equal(safeWalletLifecycleEvent('provider.hydrate.connected'),'provider.hydrate.connected')
  assert.equal(safeWalletLifecycleEvent('provider.hydrate.seed phrase'),null)
  assert.equal(safeWalletLifecycleEvent('connect.secret1234'),null)
})

test('progress allows only exact bounded status, event, and approval categories',()=>{
  assert.equal(safeWalletProgress('worker.connect.control-ready'),'worker.connect.control-ready')
  assert.equal(safeWalletProgress('worker.status.account-requested'),'worker.status.account-requested')
  assert.equal(safeWalletProgress('worker.connect.ethers.requestAccounts.start'),'worker.connect.ethers.requestAccounts.start')
  assert.equal(safeWalletProgress('metamask.account-connect'),'metamask.account-connect')
  assert.equal(safeWalletProgress('metamask.phrase: secret'),null)
  assert.equal(safeWalletProgress('worker.evil mnemonic or passphrase'),null)
})

test('snapshots and transitions strip raw data, preserve when control changed',()=>{
  const raw={kind:'mutation',elapsedMs:312,state:{button:'requesting',disabled:true,busy:true,
    panelVisible:true,handlerBound:true,contextVerified:true,hubChoice:'testnet',
    status:'network-update-requested',seed:'never-record-this'}}
  const timeline=safeWalletDiagnosticTimeline([
    {kind:'start',elapsedMs:0,state:{button:'idle',disabled:false}},raw,
    {kind:'event',elapsedMs:340,value:'provider.hydrate.connected'},
    {kind:'event',elapsedMs:500,value:'address 0x123 private'},
    {kind:'milestone',elapsedMs:550,value:'worker.status.account-requested'},
    {kind:'mutation',elapsedMs:800,state:{button:'mnemonic secret',status:'mnemonic secret'}},
  ])
  assert.equal(timeline.length,5)
  assert.equal(timeline[1].state.button,'requesting')
  assert.equal(timeline[1].state.disabled,true)
  assert.equal(timeline[3].value,'worker.status.account-requested')
  assert.equal(timeline[4].state.button,'other')
  assert.doesNotMatch(JSON.stringify(timeline),/seed|never-record|0x123|mnemonic secret|private/)
  assert.equal(safeWalletDiagnosticSnapshot({hubChoice:'http://credentials',status:'raw-credentials'}).hubChoice,'unverified')
})

test('MetaMask targets and backend session are sanitized independently',()=>{
  const extension=safeWalletExtensionEvidence({pages:[{kind:'notification',networkAdd:true,secret:'seed'}],
    cdpExtensionTargets:[{kind:'sidepanel',type:'page',url:'chrome-extension://secret/path'}]})
  assert.deepEqual(extension,{pages:1,pageStates:[{kind:'notification',networkAdd:true,networkSwitch:false,
    accountConnect:false,originMatched:false,rpcMatched:false}],
    targets:[{kind:'sidepanel',type:'page'}]})
  assert.doesNotMatch(JSON.stringify(extension),/seed|chrome-extension/)
  assert.deepEqual(safeWalletBackendSession({httpOk:true,selectedNetwork:'testnet',status:'connected',ring:'3',secret:'x'}),
    {httpOk:true,selectedNetwork:'testnet',status:'connected',ring:'3'})
})
