// Independent MCEL domain executor.  Loaded from and fingerprinted with the
// generated package, never from an existing application host facade.
export async function createPortableRuntime(appId, declaredIntents) {
  if (!['calculator', 'code-editor'].includes(appId)) throw Error('MCEL_PORTABLE_REFUSED: app unsupported');
  await import(appId === 'calculator' ? './calculator-core.js' : './code-editor-core.js');
  const core = appId === 'calculator' ? globalThis.MainComputerCalculatorCore : globalThis.MainComputerCodeEditorCore;
  if (!core || typeof core !== 'object') throw Error('MCEL_PORTABLE_REFUSED: pure domain core unavailable');
  const supported = appId === 'calculator' ? [
    'switchMode','enterToken','clearExpression','evaluateExpression','drawGraph','resetGraph',
    'askModelForExpression','askModelForGraphExpression','askModelForMathicsExpression','evaluateMathics','askResultQuestion'
  ] : ['inspectWorkspace','openFile','editDraft','saveFile','discardDraft','closeFile','previewAiderPlan','applyReviewedPatch'];
  if (JSON.stringify([...supported].sort()) !== JSON.stringify([...declaredIntents].sort()))
    throw Error('MCEL_PORTABLE_REFUSED: DSL intent/implementation registry mismatch');
  async function sha256(text) {
    if (!globalThis.crypto?.subtle) return failure('SHA-256 digest authority unavailable');
    const bytes=new TextEncoder().encode(text);
    const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
  }
  let capabilities = null;
  let value = appId === 'calculator' ? {
    mode:'basic', expression:'0', result:null, graphExpression:'x', graph:null,
    range:{xMin:-10,xMax:10,yMin:-10,yMax:10}, mathicsExpression:'', mathicsResult:null, answer:null
  } : core.initialState();
  let serial = Promise.resolve();
  const failure = (why) => { throw Error(`MCEL_PORTABLE_REFUSED: ${why}`); };
  async function request(intent, payload) {
    if (!capabilities || typeof capabilities.request !== 'function') return failure(`missing authorized capability ${intent}`);
    const response = await capabilities.request(intent, JSON.parse(JSON.stringify(payload)));
    if (!response || typeof response !== 'object' || response.ok !== true)
      return failure(`capability ${intent} refused: ${response?.error || 'no verified response'}`);
    return response;
  }
  async function perform(name, data) {
    if (!supported.includes(name)) return failure(`undeclared intent ${name}`);
    const p = data && typeof data === 'object' ? data : {};
    let receipt;
    if (appId === 'calculator') {
      switch(name) {
        case 'switchMode': {
          const mode = p.mode;
          if (!['basic','graphing'].includes(mode)) return failure('invalid calculator mode');
          value.mode = mode; receipt={mode}; break;
        }
        case 'enterToken': {
          if (typeof p.token !== 'string' || p.token.length > 80) return failure('invalid calculator token');
          const next = core.appendCalculatorDisplayToken(value.expression,p.token);
          value.expression = next.expression; receipt=next;break;
        }
        case 'clearExpression': {
          value.expression=core.clearCalculatorDisplayExpression().expression; value.result=null;
          receipt={expression:value.expression};break;
        }
        case 'evaluateExpression': {
          if (typeof p.expression === 'string') value.expression=p.expression;
          receipt=core.evaluateCalculatorExpression(value.expression);
          if (!receipt.ok) return failure(`arithmetic evaluation failed: ${receipt.error || receipt.statusText || 'invalid result'}`);
          value.result=receipt;break;
        }
        case 'drawGraph': {
          if (typeof p.expression === 'string') value.graphExpression=p.expression;
          const range=p.range || value.range;
          receipt=core.sampleCalculatorGraphExpression(value.graphExpression,range,Math.min(800,Math.max(20,Number(p.width)||120)));
          if (!receipt.ok) return failure('graph evaluation failed');
          value.graph=receipt;value.range=range;break;
        }
        case 'resetGraph': {
          value.graph=null;value.graphExpression='x';value.range={xMin:-10,xMax:10,yMin:-10,yMax:10};
          receipt={range:value.range,expression:value.graphExpression};break;
        }
        case 'askModelForExpression':
        case 'askModelForGraphExpression':
        case 'askModelForMathicsExpression':
        case 'evaluateMathics':
        case 'askResultQuestion': {
          const reply=await request(name,{...p,context:{expression:value.expression,result:value.result,
            graphExpression:value.graphExpression,mathicsExpression:value.mathicsExpression}});
          if (typeof reply.text !== 'string' || !reply.text.trim() || typeof reply.provider !== 'string'
              || !reply.provider || !reply.evidence || typeof reply.evidence !== 'object')
            return failure(`unverifiable external result from ${name}`);
          const e=reply.evidence;
          const sourceField=name==='askResultQuestion'?'question':name==='evaluateMathics'?'expression':'prompt';
          if (e.schema!=='mcel.external-provider-receipt.v1' || e.intent!==name
              || e.backend!==reply.provider || e.verifiedExecution!==true
              || !/^[a-f0-9]{64}$/.test(e.inputSha256||'')
              || !/^[a-f0-9]{64}$/.test(e.outputSha256||'')
              || e.inputSha256!==(await sha256(JSON.stringify(p[sourceField])))
              || e.outputSha256!==(await sha256(JSON.stringify(reply.text))))
            return failure(`unverified external receipt for ${name}`);
          // Receipt integrity alone is not executable graph syntax. Refuse
          // malformed external text before it can enter Calculator state.
          if (name==='askModelForGraphExpression') {
            try { core.parseCalculatorGraphExpression(reply.text); }
            catch (_) { return failure('external graph expression not parseable by Calculator core'); }
          }
          if (name==='askModelForExpression') value.expression=reply.text;
          if (name==='askModelForGraphExpression') value.graphExpression=reply.text;
          if (name==='askModelForMathicsExpression') value.mathicsExpression=reply.text;
          if (name==='evaluateMathics') value.mathicsResult=reply.text;
          if (name==='askResultQuestion') value.answer=reply.text;
          receipt={text:reply.text,provider:reply.provider,evidence:reply.evidence};break;
        }
      }
    } else {
      switch(name) {
        case 'inspectWorkspace': {
          const r=await request(name,{repoDir:value.repoDir});
          if (!Array.isArray(r.files))return failure('workspace listing missing');
          value=core.applyWorkspaceInspection(value,{files:r.files,repoDir:value.repoDir});receipt={files:r.files};break;
        }
        case 'openFile': {
          const path=core.normalizeProjectPath(p.path);
          const r=await request(name,{path});
          if (r.path!==path || typeof r.content!=='string' || !/^[a-f0-9]{64}$/i.test(r.sourceHash||'')
              || (await sha256(r.content)) !== r.sourceHash.toLowerCase())
            return failure('open-file proof missing');
          value=core.applyOpenedFile(value,r);receipt={path,sourceHash:r.sourceHash,content:r.content};break;
        }
        case 'editDraft': {
          value=core.applyDraftEdit(value,p);receipt={dirty:value.draft?.dirty,path:value.draft?.path};break;
        }
        case 'saveFile': {
          const req=core.buildSaveRequest(value,p);
          const r=await request(name,req);
          if (r.path!==req.path || !/^[a-f0-9]{64}$/i.test(r.sourceHash||'') || r.changed!==true
              || (await sha256(req.replacement_text)) !== r.sourceHash.toLowerCase())
            return failure('save transaction receipt invalid');
          value=core.applySavedFile(value,{path:req.path,text:req.replacement_text,
            sourceHash:r.sourceHash,changedFiles:[req.path],raw:r});receipt={path:req.path,sourceHash:r.sourceHash,transaction:r};break;
        }
        case 'discardDraft':value=core.discardDraft(value);receipt={dirty:!!value.draft?.dirty};break;
        case 'closeFile':value=core.closeFile(value);receipt={closed:true};break;
        case 'previewAiderPlan': {
          const req=core.buildPatchPreviewRequest(value,p);
          const r=await request(name,req);
          if (!/^[a-f0-9]{32}$/i.test(r.handle||'') || r.mutationAllowed!==false)
            return failure('preview transaction handle or mutation guarantee invalid');
          value=core.applyPatchPreview(value,r);receipt={handle:r.handle,changedFiles:r.changedFiles};break;
        }
        case 'applyReviewedPatch': {
          const req=core.buildReviewedPatchApplyRequest(value,p);
          const r=await request(name,req);
          if (r.handle!==req.handle || r.mutationAllowed!==true || r.state!=='applied')
            return failure('reviewed apply transaction receipt invalid');
          value=core.applyReviewedPatchResult(value,r);receipt={handle:r.handle,changedFiles:r.changedFiles};break;
        }
      }
    }
    return {ok:true,intent:name,receipt,state:JSON.parse(JSON.stringify(value))};
  }
  return Object.freeze({
    appId, supportedIntents:Object.freeze([...supported]),
    configureCapabilities(provider) {
      if (!provider || typeof provider.request !== 'function') return failure('invalid capability provider');
      capabilities=provider;
    },
    inspect:()=>JSON.parse(JSON.stringify(value)),
    dispatch(name,payload={}) {
      // Serialize against concurrent source mutations; a rejected intent does not poison the queue.
      const result=serial.then(()=>perform(name,payload)).catch(error => {
        if (String(error?.message || error).includes('MCEL_PORTABLE_REFUSED')) throw error;
        return failure(String(error?.code || error?.message || error));
      });
      serial=result.catch(()=>{});return result;
    }
  });
}
