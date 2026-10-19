// Package-owned domain and capability contract smoke; no browser/host facade.
const {pathToFileURL}=require('url');
const path=require('path');
const assert=require('assert/strict');
const fs=require('fs');
const [outputRoot,app]=process.argv.slice(2);
(async()=>{
  const dir=path.resolve(outputRoot,app);
  const manifest=JSON.parse(fs.readFileSync(path.join(dir,'presentation.json'),'utf8'));
  const runtime=await import(pathToFileURL(path.join(dir,'portable.js')));
  const h=await runtime.createPortableRuntime(app,manifest.declaredIntents);
  const seen=[];
  const run=async (n,p)=>{const r=await h.dispatch(n,p);assert.equal(r.ok,true);seen.push(n);return r};
  const reject=async(n,p)=>{await assert.rejects(()=>h.dispatch(n,p),/MCEL_PORTABLE_REFUSED/);seen.push(n)};
  if(app==='calculator'){
    await run('clearExpression');await run('enterToken',{token:'2'});
    const math=await run('evaluateExpression',{expression:'3 m + 40 cm'});
    assert.equal(math.receipt.displayValue,'3.4 m');
    await run('switchMode',{mode:'graphing'});
    const graph=await run('drawGraph',{expression:'x*2',width:50});assert.equal(graph.receipt.samples.length,51);
    await run('resetGraph');
    for(const n of ['askModelForExpression','askModelForGraphExpression','askModelForMathicsExpression','evaluateMathics','askResultQuestion'])
      await reject(n,{});
    let corrupt=true;
    let graphMalformed=false;
    h.configureCapabilities({request:async (intent,payload)=>{
      const sourceField=intent==='askResultQuestion'?'question':intent==='evaluateMathics'?'expression':'prompt';
      const {createHash}=require('crypto');
      const hash=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
      const text=intent==='askModelForGraphExpression' ? (graphMalformed?'2x':'2*x') : 'AUTHORIZED RESULT';
      return {ok:true,text,provider:'contract-test-provider',evidence:{
        schema:'mcel.external-provider-receipt.v1',intent,backend:'contract-test-provider',
        verifiedExecution:true,inputSha256:hash(payload[sourceField]),
        outputSha256:corrupt?'0'.repeat(64):hash(text)}};
    }});
    const beforeForged=h.inspect().expression;
    await reject('askModelForExpression',{prompt:'test'});
    assert.equal(h.inspect().expression,beforeForged); // invalid hash cannot mutate state
    corrupt=false;
    graphMalformed=true;
    const beforeMalformedGraph=h.inspect().graphExpression;
    await reject('askModelForGraphExpression',{prompt:'twice x'});
    assert.equal(h.inspect().graphExpression,beforeMalformedGraph);
    graphMalformed=false;
    for(const n of ['askModelForExpression','askModelForGraphExpression','askModelForMathicsExpression','evaluateMathics','askResultQuestion'])
      await run(n,{question:'test',prompt:'test',expression:'2+2'});
    assert.equal(h.inspect().answer,'AUTHORIZED RESULT');
  }else{
    await reject('openFile',{path:'demo.txt'});
    let bytes='before\n';
    const digest=x=>require('crypto').createHash('sha256').update(x).digest('hex');
    const reviewed=new Map();
    h.configureCapabilities({request:async(n,p)=>{
      if(n==='inspectWorkspace')return {ok:true,files:[{path:'demo.txt',name:'demo.txt',kind:'file',bytes:bytes.length}]};
      if(n==='openFile')return {ok:true,path:'demo.txt',content:bytes,sourceHash:digest(bytes)};
      if(n==='saveFile'){
        if(p.expected_before_sha256!==digest(bytes))return {ok:false,error:'stale'};
        bytes=p.replacement_text;return {ok:true,changed:true,path:'demo.txt',sourceHash:digest(bytes)};
      }
      if(n==='previewAiderPlan'){
        const handle='d'.repeat(32);reviewed.set(handle,p.changes[0]);
        return {ok:true,handle,mutationAllowed:false,changedFiles:['demo.txt'],transaction:{changes:p.changes}};
      }
      if(n==='applyReviewedPatch'){
        const change=reviewed.get(p.handle);if(!change)return{ok:false,error:'missing'};
        if(digest(bytes)!==change.expected_before_sha256)return {ok:false,error:'stale'};
        bytes=change.replacement_text;reviewed.delete(p.handle);
        return {ok:true,handle:p.handle,state:'applied',mutationAllowed:true,changedFiles:['demo.txt']};
      }
      return {ok:false,error:'unsupported'};
    }});
    await run('inspectWorkspace');await run('openFile',{path:'demo.txt'});
    await run('editDraft',{text:'saved\n'});await run('saveFile');
    assert.equal(bytes,'saved\n');await run('editDraft',{text:'ignored'});await run('discardDraft');
    await run('editDraft',{text:'reviewed\n'});
    const preview=await run('previewAiderPlan');
    await reject('applyReviewedPatch',{reviewed:false,approved:false});
    await run('applyReviewedPatch',{reviewed:true,approved:true,handle:preview.receipt.handle});
    assert.equal(bytes,'reviewed\n');await run('closeFile');
  }
  const declared=Object.keys(manifest.portableImplementations).sort();
  assert.deepEqual(declared,[...manifest.declaredIntents].sort());
  assert.ok(manifest.assetDigests['portable.js']);
  console.log(JSON.stringify({app,declaredIntents:declared.length,observed:seen,fullSemanticRuntimeProven:false}));
})().catch(e=>{console.error(e);process.exitCode=1});
