/* MCEL 14.7.2 — universal semantic selection and content-mutation service.
 * No application/domain concepts. Never silently select different source text.
 * Native browser selection remains the authoritative selection mechanism.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.selection-aware-content-mutation.v1';
  const KEY='data-mcel-selection-key';
  const SCOPE='data-mcel-selection-scope';
  const services=new WeakMap();
  function selectedText(doc){
    const s=doc.getSelection?.();return s&&!s.isCollapsed?String(s):'';
  }
  function overlaps(doc,node){
    const selection=doc.getSelection?.();
    if(!selection || selection.isCollapsed || !node?.isConnected)return false;
    for(let i=0;i<selection.rangeCount;i++){
      try{if(selection.getRangeAt(i).intersectsNode(node))return true;}catch(_){/* detached nodes */}
    }
    return false;
  }
  function leaf(node){
    const el=node?.nodeType===1?node:node?.parentElement;
    return el?.closest?.('['+KEY+']')||null;
  }
  function textNodes(element){
    const walker=element.ownerDocument.createTreeWalker(element,4);
    const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
    return nodes;
  }
  function charOffset(element,node,offset){
    let count=0;
    for(const part of textNodes(element)){
      if(part===node)return count+Math.min(Math.max(0,offset),part.length);
      if(part.contains?.(node))return null;
      count+=part.length;
    }
    if(node===element)return offset===0?0:count;
    return null;
  }
  function endpoint(element,offset){
    const nodes=textNodes(element);let left=Math.max(0,offset);
    for(const part of nodes){if(left<=part.length)return [part,left];left-=part.length;}
    const last=nodes.at(-1);return last?[last,last.length]:[element,0];
  }
  function capture(root){
    const doc=root.ownerDocument,s=doc.getSelection?.();
    if(!s||s.isCollapsed||!s.rangeCount)return null;
    const a=leaf(s.anchorNode),f=leaf(s.focusNode);
    if(!a||!f||!root.contains(a)||!root.contains(f))return null;
    const start=charOffset(a,s.anchorNode,s.anchorOffset);
    const end=charOffset(f,s.focusNode,s.focusOffset);
    if(start===null||end===null)return null;
    return {schema:SCHEMA,scope:root.getAttribute(SCOPE)||null,
      anchor:{key:a.getAttribute(KEY),offset:start,text:a.textContent},
      focus:{key:f.getAttribute(KEY),offset:end,text:f.textContent},
      selectedText:String(s)};
  }
  function find(root,key){
    // Attribute comparison avoids selector escaping requirements for arbitrary field keys.
    return [...root.querySelectorAll('['+KEY+']')].find(n=>n.getAttribute(KEY)===key)||null;
  }
  function restore(root,snapshot){
    if(!snapshot||snapshot.schema!==SCHEMA||snapshot.scope!==(root.getAttribute(SCOPE)||null))
      return {ok:false,reason:'identity-mismatch'};
    const a=find(root,snapshot.anchor.key),f=find(root,snapshot.focus.key);
    if(!a||!f)return {ok:false,reason:'anchor-missing'};
    let ao=snapshot.anchor.offset,fo=snapshot.focus.offset;
    // Semantic identity alone isn't enough. Preserve the *actual selected evidence*.
    if(a.textContent!==snapshot.anchor.text || f.textContent!==snapshot.focus.text){
      if(a!==f)return {ok:false,reason:'selected-content-changed'};
      const start=snapshot.anchor.offset,end=snapshot.focus.offset;
      const old=snapshot.anchor.text.slice(Math.min(start,end),Math.max(start,end));
      const at=old? a.textContent.indexOf(old):-1;
      if(at<0 || a.textContent.indexOf(old,at+1)>=0)
        return {ok:false,reason:'ambiguous-changed-content'};
      ao=at+(start<end?0:old.length);fo=at+(start<end?old.length:0);
    }
    const [an,ai]=endpoint(a,ao),[fn,fi]=endpoint(f,fo);
    const doc=root.ownerDocument,selection=doc.getSelection?.();
    if(!selection)return {ok:false,reason:'selection-unavailable'};
    try{
      // Validate the actual text between the semantic endpoints BEFORE
      // disturbing whatever native selection the person already holds.
      let range=doc.createRange();range.setStart(an,ai);range.setEnd(fn,fi);
      if(String(range)!==snapshot.selectedText){
        range=doc.createRange();range.setStart(fn,fi);range.setEnd(an,ai);
      }
      if(String(range)!==snapshot.selectedText)
        return {ok:false,reason:'selected-evidence-changed'};
      selection.removeAllRanges();
      if(selection.setBaseAndExtent)selection.setBaseAndExtent(an,ai,fn,fi);
      else selection.addRange(range);
      return {ok:true};
    }catch(_){return {ok:false,reason:'restore-refused'};}
  }
  function compatible(oldNode,newNode){
    if(oldNode.nodeType!==newNode.nodeType)return false;
    if(oldNode.nodeType===3)return true;
    if(oldNode.nodeType!==1)return false;
    return oldNode.tagName===newNode.tagName &&
      oldNode.getAttribute(KEY)===newNode.getAttribute(KEY) &&
      oldNode.className===newNode.className;
  }
  function reconcile(existing,fresh,doc,state){
    if(existing.nodeType===3){
      if(existing.nodeValue!==fresh.nodeValue){
        if(overlaps(doc,existing)){state.deferred++;return;}
        existing.nodeValue=fresh.nodeValue;state.updated++;
      }
      return;
    }
    if(existing.nodeType!==1)return;
    // Keep disclosure state, focused controls and their event listeners intact.
    // Text and source values are the mutable payload, not layout identity.
    const old=[...existing.childNodes].filter(n=>!(n.nodeType===1 &&
      n.classList.contains('mcel-semantic-collection__selection-stale')));
    const incoming=[...fresh.childNodes];
    for(let i=0;i<incoming.length;i++){
      const previous=old[i],next=incoming[i];
      if(!previous){existing.append(next.cloneNode(true));state.updated++;continue;}
      if(!compatible(previous,next)){
        if(overlaps(doc,previous)){state.deferred++;continue;}
        // Do not replace live copy buttons (their handlers close over current data).
        if(previous.nodeType===1&&previous.tagName==='BUTTON'&&next.tagName==='BUTTON')continue;
        previous.replaceWith(next.cloneNode(true));state.updated++;continue;
      }
      reconcile(previous,next,doc,state);
    }
    for(let i=incoming.length;i<old.length;i++){
      if(overlaps(doc,old[i])){state.deferred++;continue;}
      old[i].remove();state.updated++;
    }
  }
  function mutate(surface,fresh){
    if(surface.getAttribute(SCOPE)!==fresh.getAttribute(SCOPE))
      return {ok:false,reason:'identity-mismatch'};
    const state={updated:0,deferred:0};
    reconcile(surface,fresh,surface.ownerDocument,state);
    if(state.deferred){surface.dataset.updatePending='true';
      let note=surface.querySelector(':scope > .mcel-semantic-collection__selection-stale');
      if(!note){note=surface.ownerDocument.createElement('p');
        note.className='mcel-semantic-collection__selection-stale';note.setAttribute('role','status');
        note.textContent='Selected text is from an earlier observation; newer source data is available.';
        surface.append(note);}
    }else{
      delete surface.dataset.updatePending;
      surface.querySelector(':scope > .mcel-semantic-collection__selection-stale')?.remove();
    }
    return {ok:true,updated:state.updated,deferred:state.deferred};
  }
  function mutateText(element,next){
    const incoming=String(next),old=element.textContent;
    if(old===incoming){delete element.dataset.updatePending;return {ok:true,updated:false,deferred:false};}
    if(overlaps(element.ownerDocument,element)){
      element.dataset.updatePending='true';
      return {ok:true,updated:false,deferred:true};
    }
    // Reuse the original text node so even a stable selection endpoint never
    // depends on a generated replacement node.
    if(element.childNodes.length===1 && element.firstChild.nodeType===3)
      element.firstChild.nodeValue=incoming;
    else element.textContent=incoming;
    delete element.dataset.updatePending;
    return {ok:true,updated:true,deferred:false};
  }
  function service(doc){
    let instance=services.get(doc);
    if(instance)return instance.api;
    const observers=new Set();
    const notify=()=>{for(const fn of [...observers])fn();};
    doc.addEventListener('selectionchange',notify);
    const api=Object.freeze({schema:SCHEMA,overlaps:node=>overlaps(doc,node),capture,restore,mutate,mutateText,
      selectedText:()=>selectedText(doc),observe(fn){observers.add(fn);return ()=>{
        observers.delete(fn);if(!observers.size){doc.removeEventListener('selectionchange',notify);
          services.delete(doc);}
      };}});
    instance={api};services.set(doc,instance);return api;
  }
  global.McelSelectionContinuity=Object.freeze({SCHEMA,service,capture,restore,mutate,mutateText,overlaps});
})(typeof window!=='undefined'?window:globalThis);
