import test from 'node:test'
import assert from 'node:assert/strict'
import {captainCommand, captainProof, captainFailureEvidence, captainStdioEvidence, captainPrepareOnlyCommand, probeCaptainPreparation, executeCaptain, workerSessionProof, verifyDeliveredWork, waitForDeliveredWork, waitForLiveWorker, prepareWorkerForWorkNow, postWorkNowDuration, readWorkNowRejection} from './worker_work_cycle.mjs'
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
test('Captain request is one controlled testnet transaction, no mainnet, no chain smoke',()=>{
 const {executable,args}=captainCommand({python:'python',nonce:'c23142a2-908e-4972-a6c7-c30259944f8a',model:'gemma4:26b-a4b-it-q4_K_M'})
 assert.equal(executable,'python')
 assert.deepEqual(args.slice(0,4),['-m','main_computer.captain_entrypoint','smoke',args[3]])
 for (const [name,value] of [['--network','testnet'],['--ring','3'],['--max-credits','1'],['--bridge-credits','1'],['--model','gemma4:26b-a4b-it-q4_K_M']])
  assert.equal(args[args.indexOf(name)+1],value)
 assert.ok(args.includes('--no-chain') && args.includes('--yes') && args.includes('--json'))
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
