import test from 'node:test'
import assert from 'node:assert/strict'
import {classifyWalletConnectFailure, safeWalletConnectState} from './mcel_wallet_connect_diagnostics.mjs'

test('wallet failures are classified without propagating raw exception text',()=>{
  for(const [message,code] of [
    ['Worker Connect Wallet never became handler-bound and enabled: secret', 'CONNECT_CONTROL_NOT_READY'],
    ['Worker Connect Wallet click could not be dispatched: secret','CONNECT_CLICK_FAILED'],
    ['Worker Connect Wallet click produced no connection event or busy state: secret','CONNECT_NO_ACTIVITY'],
    ['Worker emitted connect.failed after [no approvals]: Connect failed: secret','WORKER_CONNECT_FAILED'],
    ['MetaMask approval not recognized or not enabled: secret','METAMASK_APPROVAL_UNRECOGNIZED'],
    ['MetaMask side-panel approval is still pending: secret','METAMASK_SIDEPANEL_UNAVAILABLE'],
    ['Worker is awaiting a MetaMask approval, but no accessible approval page was found','METAMASK_PROMPT_MISSING'],
    ['MetaMask approval inspection stalled','METAMASK_INSPECTION_STALLED'],
    ['Worker did not advance after connect.wallet.addChain.done','NETWORK_ADD_TRANSITION_STALLED'],
    ['Worker RPC proof did not finish','RPC_PROOF_TIMEOUT'],
    ['Worker wallet connection timed out','CONNECT_TIMEOUT'],
    ['Worker session does not match the selected marketplace Hub','SESSION_MISMATCH'],
    ['unrecognized exception wallet mnemonic secret','CONNECT_UNCLASSIFIED'],
  ]){
    assert.equal(classifyWalletConnectFailure(message),code)
    assert.ok(!classifyWalletConnectFailure(message).includes('secret'))
  }
})

test('wallet control evidence is categorical and strips browser-supplied values',()=>{
  assert.deepEqual(safeWalletConnectState({panelVisible:true,buttonPresent:true,
    handlerBound:true,disabled:false,busy:false,buttonState:'connected',
    hubChoice:'testnet',unexpected:'a seed phrase'}),{
      panelVisible:true,buttonPresent:true,handlerBound:true,disabled:false,busy:false,
      buttonState:'connected',hubChoice:'testnet'
    })
  assert.equal(safeWalletConnectState({hubChoice:'https://user:secret@example.com',
    buttonState:'mnemonic secret'}).hubChoice,'unverified')
  assert.equal(safeWalletConnectState({buttonState:'mnemonic secret'}).buttonState,'other')
})
