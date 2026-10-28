/* Patch 13 stage 3: opt-in read-only canonical MCEL consumer alongside Task Manager.
 * Operates only on the server's same-response shadow. No fetch, timer, actions,
 * capabilities, host writes, or promotion into the authoritative Task Manager UI.
 * Stage 4 allows source-evidenced row continuity in this lab only; hosted
 * browser tests, not the mere existence of a generation token, certify it.
 */
(function(global){
  'use strict';
  const ENABLED = (() => { try {
    return new URLSearchParams(global.location?.search || '').get('mcel_semantic_shadow') === 'lab';
  } catch (_) { return false; } })();
  const COMPATIBILITY = (() => { try {
    return ENABLED && new URLSearchParams(global.location?.search || '').get('mcel_semantic_presentation') === 'compat';
  } catch (_) { return false; } })();
  const ROOT = 'task-manager.processes';
  let workspace = null, container = null, specSignature = null, last = {mounted:false,status:'not-requested',effects:[],continuity:'gated-unverified'};
  const S = global.McelSemanticCollectionWorkspace;
  function parityAgainstServedSnapshot(payload, snapshot) {
    const rows = payload?.collection?.rows;
    if(!Array.isArray(rows) || !Array.isArray(snapshot?.processes)) return false;
    const fields = ['pid','name','status','cmdline','memory_rss','created_at','is_current_process','is_main_computer'];
    const matched = new Set();
    for(const row of rows) {
      const record = row?.mcelCanonicalRecord;
      if(!record || !record.properties) return false;
      const match = snapshot.processes.findIndex((raw, i) => !matched.has(i) && raw &&
        fields.every((key) => {
          const propName = key === 'status' ? 'state' : key === 'cmdline' ? 'command' :
            key === 'memory_rss' ? 'resident_memory' : key;
          const p = record.properties[propName];
          return p && Object.prototype.hasOwnProperty.call(raw,key) &&
            JSON.stringify(p.raw) === JSON.stringify(raw[key]);
        }));
      if(match<0)return false;
      matched.add(match);
    }
    return (payload?.parity?.sourceRecords===snapshot.processes.length &&
      rows.length+payload.collection.quarantinedRows.length===snapshot.processes.length);
  }
  function capture(shadow,snapshot) {
    if(!ENABLED)return {ok:false,status:'not-opted-in',effects:[]};
    const result=shadow?.observations?.[ROOT];
    if(!shadow || shadow.schema!=='mcel.live-semantic-shadow.v1' ||
       shadow.profile!=='task-manager' || shadow.irSchema!=='mcel.application-ir.v1' ||
       !shadow.ok || result?.status!=='shadow-only' || result.collection?.noOperations!==true ||
       result.collection?.spec?.schema!=='mcel.semantic-collection-spec.v2' ||
       !parityAgainstServedSnapshot(result,snapshot)) {
      last={mounted:!!workspace,status:'refused',reason:'missing-or-nonparity-shadow',effects:[],continuity:'gated-unverified'};
      return last;
    }
    const continuityEvidence=result.continuity;
    const evidenceDeclared=['eligible-read-only','no-qualified-rows'].includes(continuityEvidence?.status) &&
      Number.isInteger(continuityEvidence.qualifiedRows) &&
      result.collection.spec.reconciliation?.generationFrom==='mcelGeneration' &&
      result.collection.spec.sourceFields?.includes('mcelGeneration');
    const eligible=evidenceDeclared && continuityEvidence.qualifiedRows>0 &&
      Number.isInteger(continuityEvidence.qualifiedRows) &&
      result.collection.spec.reconciliation?.generationFrom==='mcelGeneration' &&
      result.collection.spec.sourceFields?.includes('mcelGeneration');
    // Refuse malformed assertions. An unproved shadow remains readable, but
    // must never sneak a reconciliation field past the evidence gate.
    if(!evidenceDeclared && result.collection.spec.reconciliation){
      last={mounted:!!workspace,status:'refused',reason:'reconciliation-without-source-evidence',
        effects:[],continuity:'gated-unverified'};
      return last;
    }
    const host=global.document?.getElementById('task-manager-app');
    if(!host || !S?.mount){last={mounted:false,status:'refused',reason:'no-host-or-consumer',effects:[]};return last;}
    try {
      if(workspace && !container?.isConnected) {
        workspace.destroy(); workspace=null; container=null; specSignature=null;
      }
      const incomingSpec=JSON.stringify(result.collection.spec);
      // Treat changed source or semantic schemas as an explicit remount gate,
      // not an opportunity to silently reinterpret existing row state.
      if(workspace && incomingSpec!==specSignature)throw new Error('shadow-spec-changed');
      let receipt=null;
      if(!workspace) {
        container=global.document.createElement('section');
        container.className='mcel-task-semantic-shadow';
        container.dataset.mcelSemanticShadow='read-only';
        const title=global.document.createElement('h3');
        title.textContent='MCEL semantic shadow · read-only lab · '+
          (eligible?'generation-evidence trial (not production certified)':'continuity not certified');
        const mount=global.document.createElement('div');
        mount.className='mcel-task-semantic-shadow__collection';
        container.append(title,mount);
        // Append after existing application; do not replace or intercept controls.
        host.append(container);
        workspace=S.mount(mount,result.collection.rows,result.collection.spec,{observeResize:false});
        specSignature=incomingSpec;
      } else {
        receipt=workspace.update(result.collection.rows);
        if(receipt?.ok!==true) throw new Error('consumer-update-refused');
      }
      container.dataset.mcelSemanticShadowCount=String(result.collection.rows.length);
      last={mounted:true,status:'shadow-only',sourceCount:result.parity.sourceRecords,
        accepted:result.parity.accepted,quarantined:result.parity.quarantined,
        parity:true,continuity:eligible?(receipt?.retained>0?'observed-retention':'evidence-eligible'):'gated-unverified',
        reconciliation:receipt?{added:receipt.added,replaced:receipt.replaced,retained:receipt.retained,
          removed:receipt.removed,selected:receipt.selected}:null,
        evidenceQualifiedRows:eligible?continuityEvidence.qualifiedRows:0,effects:[]};
      return last;
    } catch (err) {
      last={mounted:!!workspace,status:'refused',reason:String(err?.message||err),
        continuity:'gated-unverified',effects:[]};
      return last;
    }
  }
  function status(){return {...last};}
  function inspect(id){return !!(ENABLED && workspace && workspace.inspect(id));}
  global.McelTaskManagerSemanticShadow=Object.freeze({enabled:ENABLED,compatibility:COMPATIBILITY,capture,status,inspect});
})(typeof window!=='undefined'?window:globalThis);
