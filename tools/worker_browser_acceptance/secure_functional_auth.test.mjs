import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {createHmac} from 'node:crypto'
import {proofSignature,authenticateContext,ORIGIN} from './secure_functional_auth.mjs'
test('shared proof protocol exact HMAC and invalid challenge refusal',()=>{
  const dir=mkdtempSync(join(tmpdir(),'mc-proof-contract-'))
  try{
    const key=Buffer.from(Array.from({length:32},(_,i)=>i)),path=join(dir,'proof.json')
    writeFileSync(path,JSON.stringify({type:'mc-proof-v2',key_b64:key.toString('base64url')}))
    const c={algorithm:'HMAC-SHA256',challenge_id:'abc',nonce:'xyz'}
    assert.deepEqual(proofSignature(path,c),{challenge_id:'abc',signature:createHmac('sha256',key).update('MC-AUTH-BOOTSTRAP-V1\nabc\nxyz\n').digest('base64url')})
    assert.throws(()=>proofSignature(path,{...c,nonce:'abc\nxyz'}))
    assert.throws(()=>proofSignature(path,{...c,algorithm:'other'}))
  }finally{rmSync(dir,{recursive:true,force:true})}
})
test('pre-existing session refused before opening a browser page',async()=>{
  let opened=false
  await assert.rejects(authenticateContext({cookies:async origin=>{assert.equal(origin,ORIGIN);return [{name:'__Host-MC-Session'}]},newPage:async()=>{opened=true}}),/context-not-disposable/)
  assert.equal(opened,false)
})
