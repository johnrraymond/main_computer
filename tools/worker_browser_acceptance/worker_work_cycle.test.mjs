import test from 'node:test'
import assert from 'node:assert/strict'
import {captainCommand, captainProof, captainFailureEvidence, captainStdioEvidence, captainPrepareOnlyCommand, probeCaptainPreparation, executeCaptain, workerSessionProof, verifyDeliveredWork, waitForDeliveredWork, waitForLiveWorker, prepareWorkerForWorkNow, postWorkNowDuration, readWorkNowRejection, boundedSmokeOfferTokens, setWorkerOfferTargetTokens} from './worker_work_cycle.mjs'
import {EventEmitter} from 'node:events'
import {PassThrough} from 'node:stream'
const workerId = 'worker-browser-123'
const requestId = 'req-browser-unique'
const sessionId = 'sess-browser-unique'
const receipt = {requestId,sessionId,state:'completed'}
const status = (lastOffer, lastResult, id=workerId) => ({
  ok:true, identity:{requestedRing:'Ring 3'},
  runtime:{phase:'accepting',allowed_to_accept:true,heartbeat_result:{live_session:{alive:true,hub_url:'https://testnet-hub.greatlibrary.io',worker_session:{connected:true},
    last_offer:lastOffer,last_result:lastResult}}}
})
const offer = {type:'hub.work.offer',request_id:requestId,session_id:sessionId}
const result = {type:'worker.work.result',request_id:requestId,session_id:sessionId,result:{status:'success'}}
const captainResult = {ok:true,hub:{network:'testnet',url:'https://testnet-hub.greatlibrary.io',ring:3,
  submit:{request_id:requestId,session_id:sessionId},poll:{session_id:sessionId,status:'succeeded'}}}
test('Captain work cycle uses existing testnet credits without bridge deposits or stipends',()=>{
 const {executable,args}=captainCommand({python:'python',nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 assert.equal(executable,'python')
 assert.deepEqual(args.slice(0,4),['-m','main_computer.captain_entrypoint','smoke',args[3]])
 for (const [name,value] of [['--network','testnet'],['--ring','3'],['--max-credits','1'],['--model','gemma4:26b-a4b-it-q4_K_M']])
  assert.equal(args[args.indexOf(name)+1],value)
 assert.ok(args.includes('--no-chain') && args.includes('--yes') && args.includes('--json'))
 assert.ok(args.includes('--no-bridge') && args.includes('--no-stipend'))
 assert.ok(!args.includes('--bridge-credits') && !args.includes('--stipend-credits'))
 assert.doesNotMatch(args.join(' '),/mainnet/)
})
test('Valid stable-Hub Captain completion is correlated',()=>{
 assert.deepEqual(captainProof(captainResult),receipt)
})
test('Captain Hub, status, request/session mismatches fail',()=>{
 for (const x of [
  {...captainResult,hub:{...captainResult.hub,network:'mainnet'}},
  {...captainResult,hub:{...captainResult.hub,url:'https://mainnet-hub.greatlibrary.io'}},
  {...captainResult,hub:{...captainResult.hub,ring:1}},
  {...captainResult,hub:{...captainResult.hub,poll:{session_id:sessionId,status:'pending'}}},
  {...captainResult,hub:{...captainResult.hub,poll:{session_id:'other',status:'succeeded'}}},
 ]) assert.throws(()=>captainProof(x))
})
test('This browser-registered Worker proves matching offer and result',()=>{
 assert.deepEqual(verifyDeliveredWork(status(offer,result),workerId,receipt),{
  workerId,requestId,sessionId,offerObserved:true,resultObserved:true,resultType:'worker.work.result'})
})
test('Another worker or request cannot satisfy browser acceptance',()=>{
 for (const s of [status(offer,result),
  status({...offer,request_id:'other'},result),
  status(offer,{...result,session_id:'other'}),
  status(offer,{...result,type:'worker.work.failed'}),
  status(offer,{...result,result:{status:'failed'}}),
  status(offer,{}),
  status(offer,result)]) {
  assert.throws(()=>verifyDeliveredWork(s, s===undefined?'':workerId, {requestId:'different',sessionId}))
 }
 const s=status(offer,result);s.identity.workerId='another-worker'
 assert.throws(()=>workerSessionProof(s,workerId))
})
test('A registered Worker that is not accepting jobs fails before Captain spend',()=>{
 const s=status(offer,result);s.runtime.allowed_to_accept=false
 assert.throws(()=>workerSessionProof(s,workerId),/not accepting/)
})
test('A stale or disconnected live WebSocket fails closed',()=>{
 const s=status(offer,result);s.runtime.heartbeat_result.live_session.alive=false
 assert.throws(()=>workerSessionProof(s,workerId))
})
test('After transient status, matching delivery is accepted',async()=>{
 let attempts=0
 const proof=await waitForDeliveredWork({page:{},workerId,captain:receipt,timeoutMs:3000,
  readStatus:async()=>++attempts<3?status({},{}):status(offer,result),sleep:async()=>{}})
 assert.equal(attempts,3);assert.equal(proof.requestId,requestId)
})

test('Waits for backend live session instead of racing Work now supervisor',async()=>{
 let attempts=0
 const proof=await waitForLiveWorker({page:{},workerId,timeoutMs:3000,
   readStatus:async()=>{attempts++;const s=status(offer,result);if(attempts<3)s.runtime.heartbeat_result.live_session.alive=false;return s},
   sleep:async()=>{}})
 assert.equal(proof.workerId,workerId);assert.equal(attempts,3)
})

test('Hub work result acknowledgement must represent succeeded execution',()=>{
 assert.throws(()=>verifyDeliveredWork(status(offer,{
   type:'hub.work.result.accepted',request_id:requestId,session_id:sessionId,status:'failed'
 }),workerId,receipt),/successful execution/)
 assert.equal(verifyDeliveredWork(status(offer,{
   type:'hub.work.result.accepted',request_id:requestId,session_id:sessionId,status:'succeeded'
 }),workerId,receipt).resultType,'hub.work.result.accepted')
})

const mockWorkNowPage = ({paid = true, ring = '3', saveStatus = 200, ringStatus = 200, registrationStatus = 200} = {}) => {
  const calls = []
  const paidField = {isChecked: async()=>paid, check:async()=>{calls.push('paid.check');paid=true}}
  const ringField = {inputValue:async()=>ring,selectOption:async v=>{calls.push('ring.select.'+v);ring=v}}
  const duration = {click:async()=>{calls.push('duration.click')}}
  const mkResponse=(path,code)=>({request:()=>({method:()=> 'POST'}),url:()=>`http://127.0.0.1:8765${path}`,ok:()=>code<400,status:()=>code})
  const responses={
    '/api/applications/worker/settings':saveStatus,
    '/api/applications/worker/network-session':ringStatus,
    '/api/applications/worker/work-now':registrationStatus,
  }
  const page={
    locator: id=>({'#worker-rental-enabled':paidField,'#worker-network-ring':ringField,'#worker-work-now-15':duration})[id],
    waitForResponse:async predicate=>{
      const match=Object.entries(responses).map(([path,code])=>mkResponse(path,code)).find(predicate)
      assert.ok(match,'Expected matching Worker POST')
      return match
    }
  }
  return {page,calls}
}

test('Work now preserves already-selected ring 3 and does not cause a session reset',async()=>{
  const {page,calls}=mockWorkNowPage()
  const progress=[]
  const result=await prepareWorkerForWorkNow(page,{onProgress:e=>progress.push(e)})
  assert.equal(result.changedPaidPolicy,false)
  assert.deepEqual(calls,[])
  assert.ok(progress.includes('worker.work-now.ring-three-preserved'))
})
test('Work now awaits paid-policy save and ring change only when needed',async()=>{
  const {page,calls}=mockWorkNowPage({paid:false,ring:'1'})
  const progress=[];let changed=false
  const result=await prepareWorkerForWorkNow(page,{onProgress:e=>progress.push(e),onPaidPolicyChanged:()=>changed=true})
  assert.equal(result.changedPaidPolicy,true)
  assert.equal(changed,true)
  assert.deepEqual(calls,['paid.check','ring.select.3'])
  assert.ok(progress.includes('worker.work-now.paid-work-saved'))
  assert.ok(progress.includes('worker.work-now.ring-three-selected'))
})
test('Work now fails closed if paid-policy save fails and exposes changed checkbox for cleanup',async()=>{
  const {page}=mockWorkNowPage({paid:false,saveStatus:400})
  let changed=false
  await assert.rejects(prepareWorkerForWorkNow(page,{onPaidPolicyChanged:()=>changed=true}),/policy save failed \(HTTP 400\)/)
  assert.equal(changed,true)
})
test('Work now duration click must result in an actual backend registration POST',async()=>{
  const {page,calls}=mockWorkNowPage({registrationStatus:400})
  const progress=[]
  const response=await postWorkNowDuration(page,{onProgress:e=>progress.push(e)})
  assert.deepEqual(calls,['duration.click'])
  assert.equal(response.status(),400)
  assert.ok(progress.includes('worker.work-now.local-api-http.400'))
})


test('Work now HTTP 400 reports the local/Hub reason, never raw request or response', async()=>{
  const response = {status:()=>400, json:async()=>({ok:false,error:'Hub returned HTTP 400: {"error":"missing required pricing field"}'})}
  assert.deepEqual(await readWorkNowRejection(response), {httpStatus:400,reason:'hub-http-400: missing required pricing field'})
})

test('Work now failed responses redact sensitive values before logging', async()=>{
  const sensitive = '0x' + 'a'.repeat(130)
  const result = await readWorkNowRejection({status:()=>400,json:async()=>({error:'wallet ' + sensitive})})
  assert.equal(result.reason,'wallet [hex-redacted]')
  assert.ok(!JSON.stringify(result).includes(sensitive))
})

test('Work now success body is never inspected and malformed failures remain bounded', async()=>{
  assert.equal(await readWorkNowRejection({status:()=>200,json:()=>{throw Error('should not read success')}}),null)
  assert.deepEqual(await readWorkNowRejection({status:()=>400,json:async()=>{throw Error('unparseable')}}),
    {httpStatus:400,reason:'json-error-unavailable'})
})


test('Captain rejection preserves an operator-facing error category without raw output',()=>{
  const evidence = captainFailureEvidence({exitCode:2, stdout:'ERROR: Testnet Hub rejects wallet funding: deposit not reconciled\n', stderr:'logging wallet funding records\n'})
  assert.equal(evidence.exitCode, 2)
  assert.equal(evidence.category, 'captain-funding-or-bridge')
  assert.match(evidence.reason, /deposit not reconciled/)
  assert.ok(!JSON.stringify(evidence).includes('logging wallet funding records'))
})

test('Captain argparse diagnostic identifies invalid options without outputting usage dump',()=>{
  const evidence = captainFailureEvidence({exitCode:2, stderr:'usage: main-computer captain ...\nmain-computer captain: error: unrecognized arguments: --wrong\n'})
  assert.equal(evidence.category, 'captain-arguments')
  assert.match(evidence.reason, /unrecognized arguments/)
})

test('Captain failure diagnostics redact wallet identifiers and sensitive values',()=>{
  const secret = '0x' + 'a'.repeat(64)
  const wallet = '0x' + 'b'.repeat(40)
  const evidence = captainFailureEvidence({exitCode:2, stdout:`ERROR: Captain wallet ${wallet} missing transaction at ${secret}\n`})
  assert.ok(!JSON.stringify(evidence).includes(secret))
  assert.ok(!JSON.stringify(evidence).includes(wallet))
  assert.match(evidence.reason, /\[hex-redacted\]/)
  assert.equal(captainFailureEvidence({exitCode:2,stdout:'ERROR: authorization: Bearer secret-is-here'}).reason, 'sensitive-error-detail-redacted')
  assert.deepEqual(captainFailureEvidence({exitCode:2,stderr:'Traceback: <confidential info>'}),
    {exitCode:2,category:'captain-unclassified',reason:'no-classified-captain-error-line'})
})

test('Captain subprocess failure automatically reports safe error reason rather than exit code only',async()=>{
  function fakeSpawn() {
    const child = new EventEmitter()
    child.stdout = new PassThrough()
    child.stderr = new PassThrough()
    child.kill = () => {}
    queueMicrotask(() => {
      child.stdout.write('ERROR: Testnet wallet escrow unavailable\n')
      child.stderr.write('some detailed authorization payload not for logs\n')
      child.emit('close',2)
    })
    return child
  }
  const command = captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
  await assert.rejects(executeCaptain(command,{spawnImpl:fakeSpawn,timeoutMs:500}),error=> {
    assert.match(error.message,/category=captain-funding-or-bridge/)
    assert.match(error.message,/Testnet wallet escrow unavailable/)
    assert.doesNotMatch(error.message,/authorization payload/)
    return true
  })
})

test('Captain structured error containing secrets is suppressed entirely',()=>{
 const raw='ERROR: Hub rejected {"authorization":"Bearer token-value", "signed_request":"hidden"}'
 const result=captainFailureEvidence({exitCode:2,stdout:raw})
 assert.equal(result.reason,'sensitive-error-detail-redacted')
 assert.doesNotMatch(JSON.stringify(result),/token-value|signed_request/)
})

test('Malformed Captain success output is not copied into an error message',async()=>{
 function fakeSpawn() {
   const child=new EventEmitter()
   child.stdout=new PassThrough()
   child.stderr=new PassThrough()
   child.kill=()=>{}
   queueMicrotask(()=>{child.stdout.write('{"secret":"do-not-print"');child.emit('close',0)})
   return child
 }
 const command=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 await assert.rejects(executeCaptain(command,{spawnImpl:fakeSpawn,timeoutMs:500}),error=>{
  assert.match(error.message,/malformed JSON/)
  assert.doesNotMatch(error.message,/do-not-print/)
  return true
 })
})

test('Captain uncaught Python exception is reported without traceback files or source',()=>{
 const stderr = 'Traceback (most recent call last):\n  File "C:\\Users\\test\\private\\captain.py", line 44, in handler\n    token = "hidden-secret"\nTypeError: float value is not valid for credits\n'
 const failure = captainFailureEvidence({stdout:'',stderr,exitCode:1})
 assert.equal(failure.exitCode,1)
 assert.equal(failure.category,'captain-python-exception')
 assert.equal(failure.reason,'TypeError: float value is not valid for credits')
 assert.doesNotMatch(JSON.stringify(failure),/hidden-secret|Users|private/)
})

test('Captain traceback redacts addresses and never leaks JSON-bearing exception',()=>{
 const address='0x'+'a'.repeat(40)
 const err=`Traceback (most recent call last):\n  File "handler.py", line 5\nValueError: RPC missing account ${address}\n`
 const evidence = captainFailureEvidence({exitCode:1, stderr:err})
 assert.equal(evidence.category,'captain-python-exception')
 assert.match(evidence.reason,/\[hex-redacted\]/)
 assert.ok(!JSON.stringify(evidence).includes(address))
 const opaque = captainFailureEvidence({exitCode:1,stderr:'Traceback (most recent call last):\nRuntimeError: unexpected {"authorization":"bearer-token"}\n'})
 assert.equal(opaque.reason,'sensitive-error-detail-redacted')
})

test('Python tracebacks with no final exception do not dump traceback',()=>{
 const evidence=captainFailureEvidence({exitCode:1,stderr:'Traceback (most recent call last):\n  File "C:\\private\\x.py"\n'})
 assert.equal(evidence.reason,'no-classified-captain-error-line')
})

test('Captain exit 1 propagates a sanitized traceback exception through executable',async()=>{
 function fakeSpawn(){
  const child=new EventEmitter()
  child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{}
  queueMicrotask(()=>{
   child.stderr.write('Traceback (most recent call last):\n  File "C:\\Secrets\\script.py"\nConnectionError: Testnet Hub unavailable\n')
   child.emit('close',1)
  })
  return child
 }
 const cmd=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 await assert.rejects(executeCaptain(cmd,{spawnImpl:fakeSpawn,timeoutMs:500}),error=>{
  assert.match(error.message,/captain-python-exception/)
  assert.match(error.message,/ConnectionError: Testnet Hub unavailable/)
  assert.doesNotMatch(error.message,/Secrets/)
  return true
 })
})


test('unclassified Captain exit reports non-secret stdout/stderr shape', async()=>{
 const stdio=captainStdioEvidence({stdout:'unexpected opaque response with wallet 0x'+ 'a'.repeat(40),stderr:''})
 assert.equal(stdio.stdout.shape,'other-text')
 assert.equal(stdio.stderr.shape,'empty')
 assert.equal(stdio.stderr.bytes,0)
 assert.doesNotMatch(JSON.stringify(stdio),/0x[a-f0-9]{40}/i)
 const command=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 const spawnImpl=()=>{
   const c=new EventEmitter();c.stdout=new PassThrough();c.stderr=new PassThrough();c.kill=()=>{}
   queueMicrotask(()=>{c.stdout.write('unclassified opaque refusal');c.emit('close',2)})
   return c
 }
 await assert.rejects(executeCaptain(command,{spawnImpl,timeoutMs:700}),error=>{
  assert.match(error.message,/captain-unclassified/)
  assert.match(error.message,/stdout=other-text:\d+/)
  assert.match(error.message,/stderr=empty:0/)
  return true
 })
})

test('prepare-only command preserves network and smoke id, removes execution flag',()=>{
 const original=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 const safe=captainPrepareOnlyCommand(original)
 assert.equal(safe.executable,original.executable)
 assert.deepEqual(safe.args.filter(x=>x!=='--prepare-only'), original.args.filter(x=>x!=='--yes'))
 assert.equal(safe.args.includes('--prepare-only'),true)
 assert.equal(safe.args.includes('--yes'),false)
 assert.equal(original.args.includes('--yes'),true)
 assert.equal(safe.args[safe.args.indexOf('--network')+1],'testnet')
 assert.equal(safe.args[safe.args.indexOf('--smoke-id')+1], original.args[original.args.indexOf('--smoke-id')+1])
 assert.throws(()=>captainPrepareOnlyCommand({args:['--yes','--network','mainnet','--smoke-id','f','--no-chain']}),/Refusing/)
 assert.throws(()=>captainPrepareOnlyCommand({args:[...original.args, '--private-key','x']}),/refuses/)
})

test('read-only Captain probe performs no execute or signing and reports PASS without secrets',async()=>{
 const cmd=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 const spawnImpl=(exe,args,options)=>{
   assert.equal(exe,cmd.executable)
   assert.ok(args.includes('--prepare-only') && !args.includes('--yes'))
   assert.equal(options.stdio[0],'ignore')
   const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{}
   queueMicrotask(()=>{
     child.stdout.write(JSON.stringify({ok:true,execute:false,hub:{network:'testnet'},chain:{chain_id:42424241},private_key:'DO_NOT_LEAK'}))
     child.emit('close',0)
   })
   return child
 }
 const report=await probeCaptainPreparation(cmd,{spawnImpl,timeoutMs:700})
 assert.deepEqual(report,{result:'prepare-only-pass',network:'testnet',chainId:42424241})
 assert.doesNotMatch(JSON.stringify(report),/DO_NOT_LEAK/)
})

test('read-only Captain probe classifies refusals without copying raw stdout or stderr',async()=>{
 const cmd=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 const spawnImpl=()=>{
   const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{}
   queueMicrotask(()=>{
     child.stdout.write('ERROR: Testnet Hub wallet balance unavailable 0x'+'f'.repeat(40)+'\n')
     child.stderr.write('SENSITIVE SIGNED REQUEST CONTENT')
     child.emit('close',2)
   })
   return child
 }
 const diagnostic=await probeCaptainPreparation(cmd,{spawnImpl,timeoutMs:700})
 assert.equal(diagnostic.result,'prepare-only-refused')
 assert.equal(diagnostic.error.category,'captain-funding-or-bridge')
 assert.match(diagnostic.error.reason,/Testnet Hub wallet balance unavailable/)
 assert.doesNotMatch(JSON.stringify(diagnostic),/0x[f]{40}|SIGNED REQUEST CONTENT/)
})

test('read-only Captain probe refuses unknown invocation without launching subprocess',async()=>{
 const report=await probeCaptainPreparation({executable:'python',args:['--yes','--network','mainnet']},{spawnImpl:()=>{throw Error('must not execute')}})
 assert.deepEqual(report,{result:'refused-unsafe-command'})
})


test('explicit Captain error is not lost behind more than 32K later output',()=>{
 const data='ERROR: Bridge completion rejected the pending deposit\n' + ('logging work-cycle snapshot\n'.repeat(2500))
 const result=captainFailureEvidence({exitCode:2,stdout:data,stderr:''})
 assert.equal(result.category,'captain-funding-or-bridge')
 assert.match(result.reason,/Bridge completion rejected/)
})

test('Captain nested Hub JSON failure reports only validated HTTP metadata and error field',()=>{
  const secret = 'nonlogged-private-random-secret'
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 400: '
    + JSON.stringify({ok:false,error:'no eligible live worker for requested ring',private_key:secret,authorization:secret})+'\n'
  const failure=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(failure.category,'captain-hub-http')
  assert.equal(failure.hubHttpStatus,400)
  assert.equal(failure.hubOperation,'live-worker-request-submit')
  assert.equal(failure.reason,'no eligible live worker for requested ring')
  assert.doesNotMatch(JSON.stringify(failure),/private_key|authorization|nonlogged-private/)
})

test('Captain refusal with credentials inside nested JSON is not logged, but endpoint and HTTP are retained',()=>{
  const secret='super-secret-credential-123'
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/credits/wallet-funding/complete with HTTP 403: '
    +JSON.stringify({ok:false,error:`authorization ${secret}`,request:{signed_request:secret}})
  const evidence=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(evidence.hubHttpStatus,403)
  assert.equal(evidence.hubOperation,'bridge-completion')
  assert.equal(evidence.reason,'hub-error-body-redacted')
  assert.doesNotMatch(JSON.stringify(evidence),/super-secret|signed_request|authorization/)
})

test('Unknown Hub paths never expose arbitrary account or request identifiers',()=>{
  const id='0x'+'e'.repeat(64)
  const raw=`ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/internal/wallet/${id}?token=secret with HTTP 500: {"error":"unavailable"}`
  const result=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(result.hubHttpStatus,500)
  assert.equal(result.hubOperation,'unknown')
  assert.ok(!JSON.stringify(result).includes(id))
  assert.doesNotMatch(JSON.stringify(result),/token=secret/)
})

test('Unstructured or truncated Hub error does not expose HTML, JSON or credentials',()=>{
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/requests with HTTP 502: <html>redacted-data</html>'
  const x=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(x.hubOperation,'request-submit')
  assert.equal(x.hubHttpStatus,502)
  assert.equal(x.reason,'hub-error-body-redacted')
  assert.doesNotMatch(JSON.stringify(x),/redacted-data|html/)
})

test('Structured Hub error output from Captain is exposed as safe categorical metadata',async()=>{
  function fakeSpawn(){
    const child=new EventEmitter()
    child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{}
    queueMicrotask(()=>{
      child.stdout.write('ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 400: '
        + JSON.stringify({error:'no matching worker sessions',signed_request:'secret-value'}))
      child.emit('close',2)
    })
    return child
  }
  const command=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
  await assert.rejects(executeCaptain(command,{spawnImpl:fakeSpawn,timeoutMs:500}),err=>{
    assert.match(err.message,/hubHttp=400/)
    assert.match(err.message,/hubOperation=live-worker-request-submit/)
    assert.match(err.message,/reason=no matching worker sessions/)
    assert.doesNotMatch(err.message,/secret-value|signed_request/)
    return true
  })
})


test('Every browser acceptance Captain command is deposit-free even across different nonce values',()=>{
 for (const nonce of ['c23142a2-908e-4972-a6c7-c30259944f8a','c23142a2-908e-4972-a6c7-c30259944f8b']) {
  const {args}=captainCommand({python:'python',nonce,model:'gemma4:26b-a4b-it-q4_K_M'})
  const asFlags=new Set(args)
  assert.ok(asFlags.has('--no-bridge'), 'must skip chain-funded bridge deposit')
  assert.ok(asFlags.has('--no-stipend'), 'must skip admin credit issuance')
  assert.ok(asFlags.has('--no-chain'), 'must skip unrelated smoke transaction')
  assert.equal(args[args.indexOf('--max-credits')+1],'1')
  assert.equal(args[args.indexOf('--network')+1],'testnet')
  assert.ok(!asFlags.has('--bridge-credits'))
 }
})

test('same work-cycle diagnoses Hub selector rejection and still masks owner data', async()=>{
  const {captainWorkerAtFailure} = await import('./worker_work_cycle.mjs')
  const response = JSON.stringify({
    ok:false,error:'worker_not_live',message:'No live worker matched the request market constraints.',
    selected_worker:{worker_id:'0x'+'a'.repeat(40),auth_token:'do-not-expose'},
    owner:{wallet_address:'0x'+'b'.repeat(40)},request_id:'0x'+'c'.repeat(64),
  })
  const stdout = 'ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 409: '+response
  const failure = captainFailureEvidence({exitCode:2,stdout})
  assert.equal(failure.hubHttpStatus,409)
  assert.equal(failure.hubOperation,'live-worker-request-submit')
  assert.equal(failure.hubCode,'worker_not_live')
  assert.equal(failure.hubMessage,'No live worker matched the request market constraints.')
  assert.equal(failure.verdict,'no-eligible-live-worker-in-hub-market')
  assert.doesNotMatch(JSON.stringify(failure),/do-not-expose|0x[a-c]{40}/)
  const snapshot=captainWorkerAtFailure(status({},{}))
  assert.deepEqual(snapshot, {statusReadable:true, accepting:true, websocketAlive:true,
    websocketConnected:true, testnetHub:true, ring3Selected:true})
  assert.doesNotMatch(JSON.stringify(snapshot),/worker-browser-123|worker_id/)
})

test('Hub requester MSK failure is diagnosed as authorization, not worker selection',()=>{
  const response={ok:false,error:'requester_msk_invalid',reason_code:'expired',message:'Request key expired',signature:'do-not-log'}
  const r=captainFailureEvidence({exitCode:2,
    stdout:'ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 401: '+JSON.stringify(response)})
  assert.equal(r.hubHttpStatus,401)
  assert.equal(r.hubCode,'requester_msk_invalid')
  assert.equal(r.hubReasonCode,'expired')
  assert.equal(r.verdict,'requester-multisession-authorization-rejected')
  assert.doesNotMatch(JSON.stringify(r),/do-not-log|signature/)
})

test('owner/market and other Hub refusal types produce bounded distinct diagnoses',()=>{
  function classify(code,status=409){
    const r=captainFailureEvidence({exitCode:2,stdout:'ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP '+status+': '+JSON.stringify({ok:false,error:code})})
    return r.verdict
  }
  assert.equal(classify('worker_owner_not_local'),'hub-worker-owner-or-socket-mismatch')
  assert.equal(classify('worker_capacity_unavailable'),'hub-worker-capacity-unavailable')
  assert.equal(classify('worker_selection_unstable'),'hub-worker-market-changed-during-selection')
  assert.equal(classify('temporal_unavailable',503),'hub-temporal-service-unavailable')
  assert.equal(classify('anything_unknown',403),'hub-access-rejected-authorization-or-edge-policy-unresolved')
  assert.equal(classify('anything_unknown',400),'hub-work-payload-rejected-unclassified')
})

test('same process error retains bounded diagnosis for browser runner without raw Hub response',async()=>{
  const secret='hidden-authorization-secret'
  const makeProcess=()=>{
    const c=new EventEmitter();c.stdout=new PassThrough();c.stderr=new PassThrough();c.kill=()=>{}
    queueMicrotask(()=>{
      c.stdout.write('ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 409: '
        +JSON.stringify({error:'worker_owner_changed',message:'Worker owner record changed before work could be offered.',authorization:secret}))
      c.emit('close',2)
    })
    return c
  }
  const cmd=captainCommand({nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
  await assert.rejects(executeCaptain(cmd,{spawnImpl:makeProcess,timeoutMs:500}),err=>{
    assert.equal(err.captainEvidence.hubCode,'worker_owner_changed')
    assert.equal(err.captainEvidence.verdict,'hub-worker-owner-or-socket-mismatch')
    assert.match(err.message,/hubHttp=409/)
    assert.match(err.message,/verdict=hub-worker-owner-or-socket-mismatch/)
    assert.doesNotMatch(JSON.stringify(err.captainEvidence)+err.message,/hidden-authorization-secret|authorization:/)
    return true
  })
})

test('unrecognized response retains HTTP status without emitting arbitrary body',()=>{
  const secret='<html>developer internal signed_request='+ 'X'.repeat(120) +'</html>'
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 502: '+secret
  const f=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(f.hubHttpStatus,502)
  assert.equal(f.verdict,'hub-service-unavailable-or-internal-error')
  assert.equal(f.reason,'hub-error-body-redacted')
  assert.doesNotMatch(JSON.stringify(f),/signed_request|developer internal/)
})

test('oversized or truncated owner evidence cannot mask known Hub worker selector code',()=>{
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 409: '
    + JSON.stringify({ok:false,error:'worker_not_live',message:'Selected worker has no live owner record.',
                      owner:{payload:'private-data-'.repeat(3000)}})
  const failure=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(failure.hubHttpStatus,409)
  assert.equal(failure.hubCode,'worker_not_live')
  assert.equal(failure.verdict,'no-eligible-live-worker-in-hub-market')
  assert.doesNotMatch(JSON.stringify(failure),/private-data/)
})

test('truncated unknown or unsafe JSON error cannot escape through fallback',()=>{
  const raw='ERROR: Hub request failed for https://testnet-hub.greatlibrary.io/api/hub/v1/work/requests with HTTP 409: '
    + '{"ok":false,"error":"my_secret_password","owner":{"token":"do-not-print"' + 'x'.repeat(25000)
  const failure=captainFailureEvidence({exitCode:2,stdout:raw})
  assert.equal(failure.hubHttpStatus,409)
  assert.equal(failure.reason,'hub-error-body-redacted')
  assert.equal(failure.verdict,'hub-work-selection-or-conflict-rejected-unclassified')
  assert.doesNotMatch(JSON.stringify(failure),/do-not-print|secret_password/)
})

test('worker_not_live 409 gets exactly one two-credit retry, with bounded bridge targeting',async()=>{
  const {executeCaptainWithPriceRetry} = await import('./worker_work_cycle.mjs')
  const calls=[];const notes=[]
  const execute=async command=>{
    calls.push(command)
    if(calls.length===1){
      const error=new Error('safe rejection')
      error.captainEvidence={hubHttpStatus:409,hubCode:'worker_not_live',hubOperation:'live-worker-request-submit'}
      throw error
    }
    return receipt
  }
  const answer=await executeCaptainWithPriceRetry({python:'python',model:'gemma4:26b-a4b-it-q4_K_M',execute,onRetry:()=>notes.push('retry')})
  assert.deepEqual(answer,receipt)
  assert.deepEqual(notes,['retry'])
  assert.equal(calls.length,2)
  const first=calls[0].args,second=calls[1].args
  assert.equal(first[first.indexOf('--max-credits')+1],'1')
  assert.ok(first.includes('--no-bridge') && first.includes('--no-stipend'))
  assert.equal(second[second.indexOf('--max-credits')+1],'2')
  assert.equal(second[second.indexOf('--bridge-balance-target')+1],'2')
  assert.ok(second.includes('--no-stipend') && second.includes('--no-chain'))
  assert.ok(!second.includes('--bridge-credits') && !second.includes('--no-bridge'))
  assert.notEqual(first[first.indexOf('--smoke-id')+1],second[second.indexOf('--smoke-id')+1])
})

test('non-market rejection never triggers a second Captain submission',async()=>{
  const {executeCaptainWithPriceRetry} = await import('./worker_work_cycle.mjs')
  for(const refusal of [
    {hubHttpStatus:409,hubCode:'worker_owner_changed',hubOperation:'live-worker-request-submit'},
    {hubHttpStatus:401,hubCode:'worker_not_live',hubOperation:'live-worker-request-submit'},
    {hubHttpStatus:409,hubCode:'worker_not_live',hubOperation:'request-submit'},
    {hubHttpStatus:409,hubCode:'insufficient_credits',hubOperation:'live-worker-request-submit'},
    null,
  ]){
    let attempts=0
    const execute=async()=>{
      attempts++
      const error=new Error('safe')
      error.captainEvidence=refusal
      throw error
    }
    await assert.rejects(executeCaptainWithPriceRetry({model:'gemma4:26b-a4b-it-q4_K_M',execute}),/safe/)
    assert.equal(attempts,1)
  }
})

test('second worker_not_live does not retry a third time or issue additional funds',async()=>{
  const {executeCaptainWithPriceRetry} = await import('./worker_work_cycle.mjs')
  let count=0
  const execute=async()=>{
    count++
    const error=new Error('rejected')
    error.captainEvidence={hubHttpStatus:409,hubCode:'worker_not_live',hubOperation:'live-worker-request-submit'}
    throw error
  }
  await assert.rejects(executeCaptainWithPriceRetry({model:'gemma4:26b-a4b-it-q4_K_M',execute}),/rejected/)
  assert.equal(count,2)
})

test('work-cycle Captain poll waits beyond the Worker 300-second local-AI deadline', async()=>{
  const {WORK_CYCLE_TIMEOUT_MS} = await import('./worker_work_cycle.mjs')
  const command=captainCommand({nonce:'d0c628a3-5b33-4b5a-b4d3-dc41c161a89d',model:'gemma4:26b-a4b-it-q4_K_M'})
  const index=command.args.indexOf('--poll-seconds')
  assert.equal(command.args[index+1],'360')
  assert.ok(WORK_CYCLE_TIMEOUT_MS>=400_000)
  assert.ok(command.args.includes('--no-bridge') && command.args.includes('--no-stipend'))
  const retry=captainCommand({nonce:'a064c5aa-f23e-4287-8ec5-10865f8eb7ff',model:'gemma4:26b-a4b-it-q4_K_M',retryTwoCredits:true})
  assert.equal(retry.args[retry.args.indexOf('--poll-seconds')+1],'360')
  assert.equal(retry.args[retry.args.indexOf('--bridge-balance-target')+1],'2')
})

test('accepted session remains failure, but preserves only correlated progress identifiers', ()=>{
  const incomplete={...captainResult,hub:{...captainResult.hub,
    poll:{session_id:sessionId,request_id:requestId,status:'accepted',
      worker_data:{secret:'must-not-leak'}}}}
  try {
    captainProof(incomplete)
    assert.fail('Accepted cannot prove terminal success')
  } catch (error) {
    assert.match(error.message,/state=accepted/)
    assert.deepEqual(error.captainProgress,{requestId,sessionId,state:'accepted',verdict:'hub-accepted-worker-not-terminal'})
    assert.doesNotMatch(JSON.stringify(error.captainProgress),/secret|worker_data/)
  }
  // A mismatched session must not gain a trusted progress identity.
  assert.throws(()=>captainProof({...incomplete,hub:{...incomplete.hub,poll:{status:'accepted',session_id:'other'}}}),
    error => !error.captainProgress && /session does not match/.test(error.message))
})

test('accepted-session Worker diagnostic identifies matching offer still computing', async()=>{
  const {captainWorkerExecutionAtFailure} = await import('./worker_work_cycle.mjs')
  const s=status(offer, {})
  s.runtime.heartbeat_result.live_session.active_work_count=1
  s.runtime.heartbeat_result.live_session.last_error='secret wallet credentials'
  assert.deepEqual(captainWorkerExecutionAtFailure(s,{requestId,sessionId}), {
    statusReadable:true,workerWebsocketAlive:true,workerWebsocketConnected:true,
    matchingOfferObserved:true,matchingResultObserved:false,
    resultType:'none',resultStatus:'unknown',activeWorkCount:1,
    verdict:'matching-offer-still-executing',
  })
})

test('accepted-session Worker diagnostic distinguishes failed work from unacknowledged result', async()=>{
  const {captainWorkerExecutionAtFailure} = await import('./worker_work_cycle.mjs')
  const failed=captainWorkerExecutionAtFailure(status(offer, {
    type:'worker.work.failed',request_id:requestId,session_id:sessionId,
    error:{private_key:'do-not-log',error:'failure details must stay private'},
  }),{requestId,sessionId})
  assert.equal(failed.verdict,'worker-reported-execution-failure')
  assert.equal(failed.resultType,'worker.work.failed')
  assert.doesNotMatch(JSON.stringify(failed),/private_key|do-not-log/)
  const sent=captainWorkerExecutionAtFailure(status(offer, result),{requestId,sessionId})
  assert.equal(sent.verdict,'worker-result-sent-but-hub-termination-unverified')
  const hubAck=captainWorkerExecutionAtFailure(status(offer, {
    type:'hub.work.result.accepted',request_id:requestId,session_id:sessionId,status:'succeeded',
  }),{requestId,sessionId})
  assert.equal(hubAck.verdict,'worker-received-terminal-hub-ack-but-captain-poll-not-terminal')
  const other=captainWorkerExecutionAtFailure(status(offer,{...result,request_id:'another-request'}),{requestId,sessionId})
  assert.equal(other.matchingResultObserved,false)
})

test('subprocess accepted result propagates progress without permitting successful certification', async()=>{
  const {EventEmitter} = await import('node:events')
  const payload={...captainResult,hub:{...captainResult.hub,
    poll:{session_id:sessionId,request_id:requestId,status:'accepted'}}}
  const spawnImpl=()=>{
    const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter()
    child.kill=()=>{}
    queueMicrotask(()=>{child.stdout.emit('data',JSON.stringify(payload));child.emit('close',0)})
    return child
  }
  try {
    await executeCaptain({executable:'python',args:[]},{spawnImpl,timeoutMs:2000})
    assert.fail('Accepted result must not be successful')
  } catch (error) {
    assert.match(error.message,/state=accepted/)
    assert.equal(error.captainProgress?.requestId,requestId)
    assert.equal(error.captainProgress?.sessionId,sessionId)
    assert.equal(error.captainEvidence,undefined)
  }
})

test('the actual Worker offer of 1024 tokens at 0.001 costs 1.024 credits', async()=>{
 const {priceOfWorkerOffer} = await import('./worker_work_cycle.mjs')
 assert.deepEqual(priceOfWorkerOffer({targetTokens:'1024',creditPerToken:'0.001'}),
   {requiredCreditWei:'1024000000000000000',priceCeilingCredits:2})
 const two=captainCommand({nonce:'d0c628a3-5b33-4b5a-b4d3-dc41c161a89d',
   model:'gemma4:26b-a4b-it-q4_K_M',priceCeilingCredits:2,bridgeFundingRequired:true})
 assert.equal(two.args[two.args.indexOf('--max-credits')+1],'2')
 assert.equal(two.args[two.args.indexOf('--bridge-balance-target')+1],'2')
 assert.ok(!two.args.includes('--no-bridge'))
 assert.deepEqual(priceOfWorkerOffer({targetTokens:'900',creditPerToken:'0.001'}),
   {requiredCreditWei:'900000000000000000',priceCeilingCredits:1})
 for (const [tokens,rate] of [['1024','0.002'],['1024','1e-3'],['0','0.001']]) {
   assert.throws(()=>priceOfWorkerOffer({targetTokens:tokens,creditPerToken:rate}))
 }
})

test('known two-credit offer performs one Captain submission, never a second paid retry', async()=>{
 const {executeCaptainWithPriceRetry} = await import('./worker_work_cycle.mjs')
 let count=0
 const execute=async()=>{count++;throw Object.assign(new Error('rejected'),{
   captainEvidence:{hubHttpStatus:409,hubCode:'worker_not_live',hubOperation:'live-worker-request-submit'},
 })}
 await assert.rejects(executeCaptainWithPriceRetry({model:'gemma4:26b-a4b-it-q4_K_M',execute,
   priceCeilingCredits:2}),/rejected/)
 assert.equal(count,1)
})

test('two-credit Worker offer with sufficient existing balance never bridges',()=>{
 const command=captainCommand({nonce:'d0c628a3-5b33-4b5a-b4d3-dc41c161a89e',
  model:'gemma4:26b-a4b-it-q4_K_M',priceCeilingCredits:2,bridgeFundingRequired:false})
 assert.equal(command.args[command.args.indexOf('--max-credits')+1],'2')
 assert.ok(command.args.includes('--no-bridge') && command.args.includes('--no-stipend'))
 assert.ok(!command.args.includes('--bridge-balance-target'))
 assert.throws(()=>captainCommand({model:'gemma4:26b-a4b-it-q4_K_M',priceCeilingCredits:1,
   bridgeFundingRequired:true}))
})

test('known one-credit offer does not bridge or blindly retry a market 409',async()=>{
 const {executeCaptainWithPriceRetry} = await import('./worker_work_cycle.mjs')
 let count=0
 const execute=async()=>{ count++; throw Object.assign(new Error('rejected'),{
   captainEvidence:{hubHttpStatus:409,hubCode:'worker_not_live',hubOperation:'live-worker-request-submit'},
 }) }
 await assert.rejects(executeCaptainWithPriceRetry({model:'gemma4:26b-a4b-it-q4_K_M',
  execute,priceCeilingCredits:1,bridgeFundingRequired:false}),/rejected/)
 assert.equal(count,1)
})


test('Work-cycle token budget is reduced only for a nonzero ACK request', () => {
 assert.deepEqual(boundedSmokeOfferTokens('1024'),{originalTokens:1024,testTokens:512})
 assert.deepEqual(boundedSmokeOfferTokens('512'),{originalTokens:512,testTokens:512})
 assert.deepEqual(boundedSmokeOfferTokens('256'),{originalTokens:256,testTokens:256})
 for (const invalid of ['', '0', '-1', '7.5', 'foo', '128001'])
   assert.throws(()=>boundedSmokeOfferTokens(invalid),/budget/)
})

test('Worker smoke offer is persisted by its own UI before Hub registration, and restored',async()=>{
 let value='1024'
 const calls=[]
 const page={
   locator:()=>({fill:async v=>{calls.push('fill:'+v);value=v},inputValue:async()=>value}),
   waitForResponse:async predicate=>{
     const response={request:()=>({method:()=> 'POST'}),url:()=> 'http://127.0.0.1:8765/api/applications/worker/settings',
       ok:()=>true,status:()=>200,json:async()=>({ok:true,settings:{sellerTargetTokens:Number(value)}})}
     assert.ok(predicate(response));return response
   }
 }
 await setWorkerOfferTargetTokens(page,512)
 await setWorkerOfferTargetTokens(page,1024)
 assert.deepEqual(calls,['fill:512','fill:1024'])
})

test('Refuse silent repricing when Worker backend rejects its settings save',async()=>{
 let value='1024'
 const page={
   locator:()=>({fill:async v=>{value=v},inputValue:async()=>value}),
   waitForResponse:async()=>({ok:()=>true,status:()=>200,
     json:async()=>({ok:true,settings:{sellerTargetTokens:1024}})})
 }
 await assert.rejects(setWorkerOfferTargetTokens(page,512),/not confirmed/)
})


test('Hub-acknowledged market price is required before any paid Captain dispatch', async()=>{
 const s=status(offer,result)
 s.runtime.heartbeat_result.live_session.market_proof={
  hub_acknowledged:true,price_credit_wei:'1024000000000000000',
  ring_three:true,chat_completions:true,capacity_available:true,
 }
 assert.throws(()=>workerSessionProof(s,workerId,'512000000000000000'),/Hub advertised Worker price differs/)
 s.runtime.heartbeat_result.live_session.market_proof.price_credit_wei='512000000000000000'
 assert.equal(workerSessionProof(s,workerId,'512000000000000000').workerId,workerId)
 for (const key of ['hub_acknowledged','ring_three','chat_completions','capacity_available']) {
  const t=structuredClone(s);t.runtime.heartbeat_result.live_session.market_proof[key]=false
  assert.throws(()=>workerSessionProof(t,workerId,'512000000000000000'))
 }
 const missing=status(offer,result)
 assert.throws(()=>workerSessionProof(missing,workerId,'512000000000000000'),/not acknowledged/)
})

test('Hub market preflight never proceeds when price remains stale after re-registration',async()=>{
 const s=status(offer,result)
 s.runtime.heartbeat_result.live_session.market_proof={hub_acknowledged:true,
  price_credit_wei:'1024000000000000000',ring_three:true,
  chat_completions:true,capacity_available:true}
 let attempts=0
 await assert.rejects(waitForLiveWorker({page:{},workerId,expectedPriceWei:'512000000000000000',timeoutMs:5,
  readStatus:async()=>{attempts++;return s},sleep:async()=>new Promise(r=>setTimeout(r,6))}),/advertised Worker price differs/)
 assert.ok(attempts>=1)
})
