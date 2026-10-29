/* MCEL 14.7: element-owned capability/fallback negotiation. No domain concepts.
 * Pure protocol: hosts assess capabilities; only the element's ordered candidates
 * determine a fallback. Execution is separately verified before acceptance.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.element-presentation-policy.v1';
  const EVENTS=new Set(['onSelect','onHover','onFocus','onResize','onInspect']);
  const MODES=new Set(['inline-expand','floating-over-source']);
  const CAPABILITIES=new Set(['geometry.child-resize','scroll.reveal','scroll.anchor-preserve',
    'scroll.observe-visibility','scroll.user-activity','overlay.anchor-source',
    'overlay.collision','overlay.portal','overlay.dismiss']);
  const MODES_REQUIRE={ 'inline-expand':'geometry.child-resize',
    'floating-over-source':'overlay.anchor-source' };
  function validate(policy){
    if(!policy || policy.schema!==SCHEMA || !policy.events || typeof policy.events!=='object')
      throw Error('MCEL_PRESENTATION_POLICY_INVALID');
    for(const [event,choices] of Object.entries(policy.events)){
      if(!EVENTS.has(event) || !Array.isArray(choices) || !choices.length || choices.length>8)
        throw Error('MCEL_PRESENTATION_EVENT_INVALID');
      for(const choice of choices){
        if(!choice || !MODES.has(choice.mode) || !Array.isArray(choice.requires) ||
           choice.requires.length>CAPABILITIES.size || new Set(choice.requires).size!==choice.requires.length ||
           !choice.requires.includes(MODES_REQUIRE[choice.mode]) ||
           choice.requires.some(c=>!CAPABILITIES.has(c)))throw Error('MCEL_PRESENTATION_CHOICE_INVALID');
        if(choice.lifecycle){
          const l=choice.lifecycle;
          if(typeof l!=='object' || !Number.isInteger(l.pointerLeaveDelayMs??0) ||
             (l.pointerLeaveDelayMs??0)<0 || (l.pointerLeaveDelayMs??0)>30000 ||
             ![undefined,'fade-now','ignore'].includes(l.onUserScroll) ||
             (l.preserveWhileSelecting!==undefined && typeof l.preserveWhileSelecting!=='boolean'))
            throw Error('MCEL_PRESENTATION_LIFECYCLE_INVALID');
        }
      }
    }
    return policy;
  }
  function negotiate(policy,event,providers,context={},execute=()=>({ok:true})){
    validate(policy);
    if(!EVENTS.has(event))return {ok:false,reason:'unknown-event',attempts:[]};
    const choices=policy.events[event]||[];
    const attempts=[];
    for(let i=0;i<choices.length;i++){
      const choice=choices[i], grants=[],refusals=[];
      for(const capability of choice.requires){
        const provider=providers?.[capability];
        let answer;
        try{answer=provider?.assess?.(capability,choice,context)||{ok:false,reason:'not-advertised'};}
        catch(error){answer={ok:false,reason:'host-error'};}
        if(answer.ok===true)grants.push({capability,conditions:answer.conditions||null});
        else refusals.push({capability,reason:answer.reason||'denied'});
      }
      if(refusals.length){attempts.push({index:i,mode:choice.mode,status:'denied',refusals});continue;}
      // New transactional path: register reversible changes *before* mutating
      // presentation. A failed attempt MUST unwind before another candidate.
      let outcome,transaction=null;
      try{
        if(typeof context.beginTransaction==='function'){
          transaction=context.beginTransaction(choice,{grants,providers});
          if(!transaction||typeof transaction.rollback!=='function'||
             typeof transaction.commit!=='function')
            return {ok:false,event,reason:'transaction-host-invalid',attempts};
        }
      }catch(error){return {ok:false,event,reason:'transaction-begin-failed',attempts};}
      try{
        outcome=execute(choice,{grants,providers,context,transaction})||
          {ok:false,reason:'no-execution-receipt'};
        if(outcome.ok===true)transaction?.commit();
      }catch(error){outcome={ok:false,reason:'execution-exception'};}
      if(outcome.ok===true){
        attempts.push({index:i,mode:choice.mode,status:'fulfilled'});
        return {ok:true,event,mode:choice.mode,choice,index:i,grants,attempts,receipt:outcome};
      }
      try{
        // Transaction rollback even when execute throws before returning a receipt.
        // Legacy callers may provide outcome.rollback, but only the transaction
        // path can protect an exception that has no receipt at all.
        if(transaction)transaction.rollback();
        else outcome.rollback?.();
      }catch(error){
        attempts.push({index:i,mode:choice.mode,status:'rollback-failed',reason:String(error)});
        return {ok:false,event,reason:'rollback-failed',attempts};
      }
      attempts.push({index:i,mode:choice.mode,status:'execution-failed',reason:outcome.reason||'not-fulfilled'});
    }
    return {ok:false,event,reason:'no-acceptable-presentation',attempts};
  }
  function createTransaction(){
    let state='open';const undos=[];
    return Object.freeze({
      addUndo(fn){if(state!=='open'||typeof fn!=='function')throw Error('MCEL_TRANSACTION_INVALID_UNDO');
        undos.push(fn);},
      commit(){if(state!=='open')throw Error('MCEL_TRANSACTION_ALREADY_SETTLED');state='committed';undos.length=0;},
      rollback(){if(state!=='open')throw Error('MCEL_TRANSACTION_ALREADY_SETTLED');state='rolled-back';
        let first=null;while(undos.length){try{undos.pop()();}catch(error){first??=error;}}
        if(first)throw first;},
      get state(){return state;}
    });
  }
  const defaultPolicy=Object.freeze({schema:SCHEMA,events:{
    onSelect:[
      {mode:'inline-expand',requires:['geometry.child-resize','scroll.reveal']},
      {mode:'floating-over-source',requires:['overlay.anchor-source','overlay.collision','overlay.portal','overlay.dismiss'],
       lifecycle:{pointerLeaveDelayMs:2000,onUserScroll:'fade-now',preserveWhileSelecting:true}},
      {mode:'inline-expand',requires:['geometry.child-resize']}
    ],
    onHover:[{mode:'floating-over-source',requires:['overlay.anchor-source','overlay.collision','overlay.portal','overlay.dismiss'],
      lifecycle:{pointerLeaveDelayMs:2000,onUserScroll:'fade-now',preserveWhileSelecting:true}}]
  }});
  global.McelPresentationNegotiation=Object.freeze({SCHEMA,validate,negotiate,createTransaction,defaultPolicy,CAPABILITIES:[...CAPABILITIES]});
})(typeof window!=='undefined'?window:globalThis);
