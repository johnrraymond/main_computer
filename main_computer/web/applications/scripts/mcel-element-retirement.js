/* MCEL 14.7.3: element-owned retirement clock and interaction protection.
 * Domain-independent; source disappearance is not permission to erase a presentation.
 * Does not mutate authoritative records or grant source operations.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.element-retirement-policy.v1';
  const MODES=new Set(['resume','reset','continue']);
  const DEFAULT=Object.freeze({schema:SCHEMA,visible:'timed',offscreen:'remove',delayMs:5000,
    pauseWhen:['text-selected','focused','hovered','expanded'],onPause:'reset',maxRetained:40});
  function validate(p){
    if(!p||p.schema!==SCHEMA||!['timed','remove','retain'].includes(p.visible)||
       !['timed','remove','retain'].includes(p.offscreen)||!Number.isInteger(p.delayMs)||
       p.delayMs<0||p.delayMs>120000||!MODES.has(p.onPause)||
       !Number.isInteger(p.maxRetained)||p.maxRetained<1||p.maxRetained>1000||
       !Array.isArray(p.pauseWhen)||p.pauseWhen.length>4||
       new Set(p.pauseWhen).size!==p.pauseWhen.length||
       p.pauseWhen.some(v=>!['text-selected','focused','hovered','expanded'].includes(v)))
      throw Error('MCEL_RETIREMENT_POLICY_INVALID');
    return p;
  }
  function create({doc,list,visibility,policy=DEFAULT,onRemove,onChange,interactionFlags=()=>[],interactionRegions=()=>[],now=()=>global.performance.now()}={}){
    validate(policy);
    if(!doc||!list||typeof visibility!=='function'||typeof onRemove!=='function')
      throw Error('MCEL_RETIREMENT_HOST_INVALID');
    const entries=new Map();let disposed=false;let ticker=null;
    function selected(node){
      const sel=doc.getSelection?.();if(!sel||sel.isCollapsed)return false;
      for(let i=0;i<sel.rangeCount;i++){
        try{if(sel.getRangeAt(i).intersectsNode(node))return true;}catch(_ignored){}
      }
      return false;
    }
    function protection(node,config=policy){
      const flags=[];
      // A logical element can have multiple concurrently mounted presentations:
      // source row, inline detail, floating overlay. All are interaction owners.
      // The host supplies additional regions, not hardcoded domain semantics.
      const regions=[node,...(interactionRegions(node)||[])].filter(
        (region,index,array)=>region?.isConnected && array.indexOf(region)===index);
      for(const region of regions){
        if(config.pauseWhen.includes('text-selected')&&selected(region)&&!flags.includes('text-selected'))
          flags.push('text-selected');
        if(config.pauseWhen.includes('focused')&&region.contains(doc.activeElement)&&!flags.includes('focused'))
          flags.push('focused');
        if(config.pauseWhen.includes('hovered')&&region.matches(':hover')&&!flags.includes('hovered'))
          flags.push('hovered');
        if(config.pauseWhen.includes('expanded')&&region.getAttribute('aria-expanded')==='true'&&
           !flags.includes('expanded'))flags.push('expanded');
      }
      // Preserve legacy custom interaction flags for specialized hosts.
      for(const flag of interactionFlags(node)||[])if(config.pauseWhen.includes(flag)&&!flags.includes(flag))flags.push(flag);
      return flags;
    }
    function visible(node){const r=visibility(node);return r && r.top<r.available && r.bottom> -r.available;}
    function end(id,reason){
      const e=entries.get(id);if(!e)return false;
      entries.delete(id);e.node.removeAttribute('data-mcel-retirement-state');
      onRemove(id,e,reason);onChange?.({id,state:'removed',reason});return true;
    }
    function tick(){
      if(disposed)return;
      const t=now();
      for(const [id,e] of entries){
        const config=e.policy;
        if(!e.node.isConnected){end(id,'detached');continue;}
        const reasons=protection(e.node,config),held=reasons.length>0;
        if(held&&!e.protected){
          if(config.onPause==='resume')e.remaining=Math.max(0,e.deadline-t);
          if(config.onPause==='reset')e.remaining=config.delayMs;
          e.protected=true;
        }else if(!held&&e.protected){
          e.protected=false;
          if(config.onPause!=='continue')e.deadline=t+e.remaining;
        }
        e.reasons=reasons;
        const newState=held?'protected':'retiring';
        if(newState!==e.state){e.state=newState;e.node.dataset.mcelRetirementState=newState;
          onChange?.({id,state:newState,reasons});}
        if(held)continue;
        if(e.action==='remove'||e.action==='timed' && t>=e.deadline)end(id,'retirement-complete');
      }
      if(!entries.size){clearInterval(ticker);ticker=null;}
    }
    function retire(id,node,metadata){
      if(disposed)return {ok:false,reason:'disposed'};
      if(entries.has(id))return {ok:false,reason:'already-retiring'};
      const config=metadata?.policy||policy;validate(config);
      const currentlyVisible=!!visible(node),reasons=protection(node,config),held=reasons.length>0;
      const action=currentlyVisible?config.visible:config.offscreen;
      const overBudget=entries.size>=config.maxRetained;
      if((action==='remove'||overBudget)&&!held)return {ok:true,retained:false,reason:overBudget?'capacity-refused':'immediate'};
      const t=now(),entry={id,node,metadata,policy:config,action:overBudget?'remove':action,state:held?'protected':'retiring',
        reasons,remaining:config.delayMs,deadline:t+config.delayMs,protected:held,visibleAtRetirement:currentlyVisible};
      if(held&&config.onPause==='reset')entry.remaining=config.delayMs;
      node.dataset.mcelRetirementState=entry.state;
      node.dataset.mcelSourceStatus='retired';
      node.setAttribute('aria-label','Retired record — no longer in the current source observation');
      entries.set(id,entry);onChange?.({id,state:entry.state,reasons});
      if(!ticker)ticker=setInterval(tick,50);
      tick();return {ok:true,retained:entries.has(id),reason:overBudget?'protected-capacity-override':action};
    }
    function destroy(){disposed=true;clearInterval(ticker);ticker=null;entries.clear();}
    return Object.freeze({retire,tick,end,destroy,get size(){return entries.size;},
      get(id){const e=entries.get(id);return e?{id,state:e.state,visible:visible(e.node),remainingMs:e.protected&&e.policy.onPause!=='continue'?e.remaining:
        Math.max(0,e.deadline-now()),reasons:e.reasons.slice(),visibleAtRetirement:e.visibleAtRetirement}:null;},
      get ids(){return [...entries.keys()];}});
  }
  global.McelElementRetirement=Object.freeze({SCHEMA,DEFAULT,validate,create});
})(typeof window!=='undefined'?window:globalThis);
