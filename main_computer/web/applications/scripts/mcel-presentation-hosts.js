/* MCEL 14.7: generic scrolling and floating-presentation capability providers.
 * Zero knowledge of process/task/file semantics. Read-only DOM rendering.
 */
(function(global){
  'use strict';
  function createScrollHost(list,options={}){
    let disposed=false;
    const onScroll=()=>{lastUserScroll=Date.now();};
    let lastUserScroll=0;
    list.addEventListener('wheel',onScroll,{passive:true});
    list.addEventListener('pointerdown',onScroll,{passive:true});
    list.addEventListener('touchmove',onScroll,{passive:true});
    function assess(capability){
      if(disposed)return {ok:false,reason:'disposed'};
      if(capability==='geometry.child-resize')return options.allowResize===false?{ok:false,reason:'resize-denied'}:{ok:true};
      if(capability==='scroll.reveal')return options.allowScroll===false?{ok:false,reason:'scroll-denied'}:{ok:true};
      if(['scroll.anchor-preserve','scroll.observe-visibility','scroll.user-activity','scroll.threshold-handoff'].includes(capability))return {ok:true};
      return {ok:false,reason:'not-scroll-capability'};
    }
    function visibility(target){
      const a=list.getBoundingClientRect(),b=target.getBoundingClientRect();
      return {top:b.top-a.top,bottom:b.bottom-a.bottom,height:b.height,available:a.height,
        fullyVisible:b.top>=a.top-1&&b.bottom<=a.bottom+1};
    }
    function reveal(target,{userInitiated=true}={}){
      if(!assess('scroll.reveal').ok)return {ok:false,reason:'scroll-denied'};
      if(!list.contains(target))return {ok:false,reason:'foreign-target'};
      if(!userInitiated && Date.now()-lastUserScroll<750)return {ok:false,reason:'user-scroll-precedence'};
      const a=list.getBoundingClientRect(),b=target.getBoundingClientRect();
      const before=list.scrollTop;
      // Oversized elements: reveal their heading/top instead of chasing their bottom.
      if(b.height>a.height-8){list.scrollTop+=b.top-a.top-6;}
      else if(b.bottom>a.bottom-6){list.scrollTop+=b.bottom-a.bottom+6;}
      else if(b.top<a.top+6){list.scrollTop+=b.top-a.top-6;}
      const geometry=visibility(target);
      const fulfilled=geometry.height>geometry.available-8 ? geometry.top>=-10 && geometry.top<=10 :
        geometry.top>=-10 && geometry.bottom<=10;
      return {ok:fulfilled,reason:fulfilled?null:'scroll-unfulfilled',before,after:list.scrollTop,geometry};
    }
    // Preserve the visible *element*, not the raw numeric scrollTop offset.
    // Geometry may change above it while the user's focus remains stationary.
    function captureAnchor(){
      const viewport=list.getBoundingClientRect();
      const child=[...list.children].find(el=>el.getBoundingClientRect().bottom>viewport.top+1);
      return {id:child?.dataset.rowId||null,offset:child?child.getBoundingClientRect().top-viewport.top:0,
        scrollTop:list.scrollTop};
    }
    function restoreAnchor(anchor){
      if(!anchor)return {ok:false,reason:'missing-anchor'};
      const child=[...list.children].find(el=>el.dataset.rowId===anchor.id);
      if(!child){list.scrollTop=anchor.scrollTop;return {ok:false,reason:'anchor-removed'};}
      const viewport=list.getBoundingClientRect();
      list.scrollTop+=child.getBoundingClientRect().top-viewport.top-anchor.offset;
      return {ok:true,scrollTop:list.scrollTop};
    }
    // Transfer a quantified scroll delta by changing the real owning scroll host,
    // never by dispatching a synthetic WheelEvent (which would be untrusted).
    function transferScroll(delta){
      if(disposed || options.allowScroll===false)return {ok:false,reason:'scroll-denied'};
      if(!Number.isFinite(delta))return {ok:false,reason:'invalid-scroll-delta'};
      const before=list.scrollTop;
      list.scrollTop+=delta;
      let consumed=list.scrollTop-before;
      // If the immediate host is exhausted, continue up the real scroll-host
      // hierarchy instead of synthesizing browser wheel events.
      if(Math.abs(delta-consumed)>0.5){
        let parent=list.parentElement;
        while(parent){
          if(parent.scrollHeight>parent.clientHeight+1 &&
             /(auto|scroll)/.test(global.getComputedStyle(parent).overflowY)){
            const prior=parent.scrollTop;parent.scrollTop+=delta-consumed;
            consumed+=parent.scrollTop-prior;
            if(Math.abs(delta-consumed)<0.5)break;
          }
          parent=parent.parentElement;
        }
        const page=list.ownerDocument.scrollingElement;
        if(page && Math.abs(delta-consumed)>0.5){
          const prior=page.scrollTop;page.scrollTop+=delta-consumed;consumed+=page.scrollTop-prior;
        }
      }
      return {ok:true,before,after:list.scrollTop,consumed};
    }
    function destroy(){if(disposed)return;disposed=true;list.removeEventListener('wheel',onScroll);
      list.removeEventListener('pointerdown',onScroll);list.removeEventListener('touchmove',onScroll);}
    return Object.freeze({assess,reveal,visibility,captureAnchor,restoreAnchor,transferScroll,destroy,
      get lastUserScroll(){return lastUserScroll;}});
  }
  // Universal wheel-ownership arbitration for an independent scrollable extension.
  // The threshold is measured against the extension's visible scrollport height.
  // A new gesture after a quiet interval can cross the boundary; rapid inertial
  // tail events from the gesture that hit the boundary cannot immediately chain.
  function createScrollHandoff(extension,host,policy={}){
    const thresholdPercent=policy.handoffThresholdPercent??15;
    const guard=policy.momentumGuardMs??140;
    if(!Number.isFinite(thresholdPercent)||thresholdPercent<0||thresholdPercent>100 ||
       !Number.isInteger(guard)||guard<0||guard>1500)throw Error('MCEL_SCROLL_HANDOFF_POLICY_INVALID');
    let owner='extension',overscroll=0,direction=0,boundarySince=0,lastInput=0,disposed=false;
    const now=()=>doclessNow();
    function doclessNow(){return (extension.ownerDocument.defaultView?.performance?.now?.() ?? Date.now());}
    const epsilon=1;
    const diagnostic=typeof policy.onDiagnostic==='function'?policy.onDiagnostic:null;
    function report(reason,details){if(!diagnostic)return;
      try{diagnostic({reason,owner,overscroll,direction,
        rowScrollTop:extension.scrollTop,rowClientHeight:extension.clientHeight,
        rowScrollHeight:extension.scrollHeight,thresholdPercent,...details});}catch(_ignored){}
    }
    function wheel(ev){
      if(disposed)return;
      const value=ev.deltaY*(ev.deltaMode===1?16:ev.deltaMode===2?extension.clientHeight:1);
      if(!Number.isFinite(value)||value===0)return;
      const at=now(),sign=Math.sign(value),before=extension.scrollTop;
      const ownerBefore=owner,previousDirection=direction;
      if(ev.cancelable)ev.preventDefault();ev.stopPropagation();
      if(direction!==sign){owner='extension';overscroll=0;boundarySince=0;}
      direction=sign;
      const max=Math.max(0,extension.scrollHeight-extension.clientHeight);
      const boundary=sign>0?extension.scrollTop>=max-epsilon:extension.scrollTop<=epsilon;
      const snapshot=(reason,additional={})=>report(reason,{deltaY:ev.deltaY,deltaMode:ev.deltaMode,
        normalizedDeltaY:value,cancelable:ev.cancelable,ownerBefore,previousDirection,
        before,after:extension.scrollTop,max,boundary,thresholdPixels:Math.max(1,extension.clientHeight*thresholdPercent/100),
        quietForMs:lastInput?+(at-lastInput).toFixed(1):null,...additional});
      // A parent-owned gesture remains parent-owned until reversal.
      if(owner==='parent'){
        const moved=host?.transferScroll?.(value);
        if(!moved?.ok){owner='extension';overscroll=0;}
        snapshot('parent-owned-forward',{transfer:moved?{ok:moved.ok,consumed:moved.consumed,before:moved.before,after:moved.after}:null});
        lastInput=at;return;
      }
      if(!boundary){
        extension.scrollTop+=value;
        overscroll=0;boundarySince=0;
        snapshot('row-scroll-consumed',{consumed:extension.scrollTop-before});
        lastInput=at;return;
      }
      if(!boundarySince)boundarySince=at;
      // A pause signals a renewed scroll attempt beyond the edge, rather than
      // a trackpad's unintentional inertial tail.
      const deliberate=guard===0 || (at-lastInput)>=guard || (at-boundarySince)>=Math.max(800,guard*5);
      if(deliberate)overscroll+=Math.abs(value);
      const threshold=Math.max(1,extension.clientHeight*thresholdPercent/100);
      let forwarded=null;
      if(overscroll>=threshold){
        forwarded=host?.transferScroll?.(value);
        if(forwarded?.ok && Math.abs(forwarded.consumed)>0.5){
          owner='parent';overscroll=0;
        }
      }
      snapshot('boundary-arbitration',{deliberate,guardMs:guard,boundarySince,
        thresholdCrossed:!!forwarded,transfer:forwarded?{ok:forwarded.ok,consumed:forwarded.consumed,
          before:forwarded.before,after:forwarded.after}:null});
      lastInput=at;
    }
    extension.style.overscrollBehavior='contain';
    extension.addEventListener('wheel',wheel,{passive:false});
    return Object.freeze({handleWheel:wheel,destroy(){disposed=true;extension.removeEventListener('wheel',wheel);},
      reset(){owner='extension';overscroll=0;direction=0;boundarySince=0;lastInput=0;},
      get state(){return {owner,overscroll,direction,thresholdPixels:Math.max(1,extension.clientHeight*thresholdPercent/100)};}});
  }
  // Collection-owned hover: the exact clipping/scrolling element enters the
  // browser top layer. No mirror, no row popover, no detached content replica.
  // This is opt-in so existing per-row presentation consumers are unchanged.
  function createCollectionUnclip(collection,{parentScroll,capabilities={},activationDelayMs=1000,
    pointerLeaveDelayMs=1000,handoffThresholdPercent=15,momentumGuardMs=140,onClose,makeExtension}={}){
    const doc=collection.ownerDocument,win=doc.defaultView;
    let active=null,disposed=false,activation=0,dismissal=0,lastPointer={x:-1,y:-1},hoveredRowId=null;
    const safe=fn=>{try{fn();}catch(_ignored){}};
    const inRect=(rect,x,y)=>rect && x>=rect.left && x<rect.right && y>=rect.top && y<rect.bottom;
    function inside(){return inRect(collection.getBoundingClientRect(),lastPointer.x,lastPointer.y) ||
      (active?.track && inRect(active.track.getBoundingClientRect(),lastPointer.x,lastPointer.y));}
    function clearTimers(){win.clearTimeout(activation);win.clearTimeout(dismissal);activation=0;dismissal=0;}
    function close(reason='dismissed'){
      clearTimers();if(!active)return false;
      const a=active;active=null;const finalScroll=collection.scrollTop;win.cancelAnimationFrame(a.frame);
      a.handoff.destroy();a.cleanup.forEach(safe);
      safe(()=>{if(a.track.matches(':popover-open'))a.track.hidePopover();});a.track.remove();
      safe(()=>{if(collection.matches(':popover-open'))collection.hidePopover();});
      a.extension?.remove();
      if(a.style===null)collection.removeAttribute('style');else collection.setAttribute('style',a.style);
      if(a.popover===null)collection.removeAttribute('popover');else collection.setAttribute('popover',a.popover);
      collection.classList.remove('mcel-collection-unclipped');a.anchor.remove();
      collection.scrollTop=finalScroll;
      onClose?.(reason);return true;
    }
    function parentTransfer(delta){
      if(capabilities.allowScroll===false)return {ok:false,reason:'scroll-denied',consumed:0};
      let remaining=delta,consumed=0,parent=collection.parentElement;
      while(parent && Math.abs(remaining)>.5){
        const style=win.getComputedStyle(parent);
        if(parent.scrollHeight>parent.clientHeight+1 && /(auto|scroll)/.test(style.overflowY)){
          const before=parent.scrollTop;parent.scrollTop+=remaining;
          const moved=parent.scrollTop-before;remaining-=moved;consumed+=moved;
        }
        parent=parent.parentElement;
      }
      const page=doc.scrollingElement;
      if(page && Math.abs(remaining)>.5){const before=page.scrollTop;
        page.scrollTop+=remaining;consumed+=page.scrollTop-before;}
      return {ok:true,consumed};
    }
    function open(extension=null){
      if(disposed||capabilities.allowOverlay===false)return {ok:false,reason:'overlay-denied'};
      if(active)return {ok:true,alreadyOpen:true,node:collection};
      if(!collection.isConnected||typeof collection.showPopover!=='function')return {ok:false,reason:'source-unavailable'};
      const rect=collection.getBoundingClientRect();if(rect.width<1||rect.height<1)return {ok:false,reason:'empty-source'};
      clearTimers();
      if(!extension&&typeof makeExtension==='function')extension=makeExtension(hoveredRowId);
      const ancestor=collection.parentElement;
      const anchor=doc.createElement('div');anchor.dataset.mcelCollectionAnchor='true';
      anchor.className='mcel-collection-unclip-anchor';
      anchor.style.cssText=`height:${rect.height}px;width:${rect.width}px;box-sizing:border-box;flex:0 0 ${rect.height}px;pointer-events:none;visibility:hidden;`;
      ancestor.insertBefore(anchor,collection);
      const style=collection.getAttribute('style'),popover=collection.getAttribute('popover'),scrollTop=collection.scrollTop;
      const background=(()=>{for(let node=collection;node;node=node.parentElement){const c=win.getComputedStyle(node).backgroundColor;
        if(c&&c!=='transparent'&&c!=='rgba(0, 0, 0, 0)' && !/rgba\([^)]*,\s*0(?:\.0+)?\s*\)/.test(c))return c;}
        return 'var(--mcel-surface, #101822)';})();
      if(extension){extension.dataset.mcelPresentationSlot='collection-unclip-extension';
        extension.classList.add('mcel-collection-unclip-extension');collection.append(extension);}
      const set=(name,value)=>collection.style.setProperty(name,value,'important');
      collection.setAttribute('popover','manual');collection.classList.add('mcel-collection-unclipped');
      for(const [key,value] of Object.entries({position:'fixed',inset:'auto',right:'auto',bottom:'auto',margin:'0',padding:'0',
        left:rect.left+'px',top:rect.top+'px',width:rect.width+'px','min-width':rect.width+'px',
        'max-width':rect.width+'px','box-sizing':'border-box','max-height':'none',background,
        'overflow-y':'auto','overflow-x':'hidden','scrollbar-width':'none'}))set(key,value);
      // Retain the original scroll offset and all ORIGINAL row nodes.
      const track=doc.createElement('div');track.className='mcel-collection-exterior-scrollbar';
      track.setAttribute('popover','manual');track.setAttribute('aria-label','Scroll expanded collection');
      const thumb=doc.createElement('div');thumb.className='mcel-collection-exterior-thumb';track.append(thumb);doc.body.append(track);
      let began=false;
      try {collection.showPopover();began=true;track.showPopover();}
      catch(error){safe(()=>{if(began)collection.hidePopover();});track.remove();extension?.remove();anchor.remove();
        if(style===null)collection.removeAttribute('style');else collection.setAttribute('style',style);
        if(popover===null)collection.removeAttribute('popover');else collection.setAttribute('popover',popover);
        collection.classList.remove('mcel-collection-unclipped');collection.scrollTop=scrollTop;
        return {ok:false,reason:'top-layer-refused',message:String(error)};}
      const handoff=createScrollHandoff(collection,{transferScroll:parentTransfer},{handoffThresholdPercent,momentumGuardMs});
      const state={anchor,track,extension,style,popover,scrollTop,handoff,frame:0,cleanup:[]};active=state;
      const listen=(el,event,fn,options)=>{el.addEventListener(event,fn,options);state.cleanup.push(()=>el.removeEventListener(event,fn,options));};
      function follow(){if(active!==state)return;
        if(!anchor.isConnected||!collection.isConnected){close('source-detached');return;}
        const a=anchor.getBoundingClientRect();
        set('left',a.left+'px');set('top',a.top+'px');
        const below=win.innerHeight-a.top-8;
        const maxHeight=Math.max(a.height,below);
        const intended=Math.max(a.height,Math.min(collection.scrollHeight,maxHeight));
        set('height',intended+'px');
        // External scrollbar is an accessory: zero width is removed from the
        // original collection. The track is separate, but never its contents.
        const max=Math.max(0,collection.scrollHeight-collection.clientHeight);
        track.hidden=max<=1;
        const placeRight=win.innerWidth-a.right>=14||a.left<14;
        track.style.left=(placeRight?a.right:a.left-12)+'px';
        track.style.top=a.top+'px';track.style.height=intended+'px';
        const length=max<=1?intended:Math.max(18,intended*intended/collection.scrollHeight);
        thumb.style.height=length+'px';thumb.style.transform='translateY('+(
          max?collection.scrollTop/max*(intended-length):0)+'px)';
        state.frame=win.requestAnimationFrame(follow);
      }
      follow();
      // showPopover may reset the source scroll offset while it temporarily
      // has auto height. Restore AFTER installing the constrained scrollport.
      collection.scrollTop=scrollTop;
      let drag=null;
      listen(track,'pointerdown',ev=>{ev.preventDefault();ev.stopPropagation();
        const box=track.getBoundingClientRect(),max=collection.scrollHeight-collection.clientHeight;
        if(ev.target!==thumb)collection.scrollTop=Math.max(0,(ev.clientY-box.top)/box.height*max);
        drag={y:ev.clientY,scroll:collection.scrollTop};track.setPointerCapture?.(ev.pointerId);});
      listen(track,'pointermove',ev=>{if(!drag)return;const travel=Math.max(1,track.clientHeight-thumb.offsetHeight);
        collection.scrollTop=drag.scroll+(ev.clientY-drag.y)*(collection.scrollHeight-collection.clientHeight)/travel;});
      listen(track,'pointerup',()=>{drag=null;});listen(track,'pointercancel',()=>{drag=null;});
      listen(track,'wheel',ev=>handoff.handleWheel(ev),{passive:false});
      listen(doc,'keydown',ev=>{if(ev.key==='Escape')close('escape');},true);
      // Existing collection is the only presentation owning hover; its track
      // participates in the same pointer-leave grace interval.
      listen(track,'pointerenter',()=>{win.clearTimeout(dismissal);dismissal=0;});
      listen(track,'pointerleave',()=>scheduleDismiss());
      return {ok:true,node:collection,position:{left:rect.left,top:rect.top,width:rect.width},close};
    }
    function scheduleDismiss(){if(!active)return;win.clearTimeout(dismissal);
      dismissal=win.setTimeout(()=>{dismissal=0;
        if(!active||inside())return;
        const selected=doc.getSelection();
        if(selected&&!selected.isCollapsed && collection.contains(selected.anchorNode))
          scheduleDismiss();
        else close('pointer-left');},pointerLeaveDelayMs);}
    function onEnter(){win.clearTimeout(dismissal);dismissal=0;
      if(!active && !activation)activation=win.setTimeout(()=>{activation=0;
        if(collection.matches(':hover')||inside())open();},activationDelayMs);}
    function onLeave(){win.clearTimeout(activation);activation=0;scheduleDismiss();}
    function onMove(ev){lastPointer={x:ev.clientX,y:ev.clientY};
      const row=ev.target instanceof Element?ev.target.closest('mcel-row'):null;
      if(row&&collection.contains(row))hoveredRowId=row.dataset.rowId;
      if(active){if(inside()){win.clearTimeout(dismissal);dismissal=0;}
        else if(!dismissal)scheduleDismiss();}}
    collection.addEventListener('pointerenter',onEnter);
    collection.addEventListener('pointerleave',onLeave);
    doc.addEventListener('pointermove',onMove,true);
    return Object.freeze({open,close,get active(){return active?.node||null;},get handoff(){return active?.handoff||null;},
      destroy(){disposed=true;close('destroy');clearTimers();collection.removeEventListener('pointerenter',onEnter);
        collection.removeEventListener('pointerleave',onLeave);doc.removeEventListener('pointermove',onMove,true);}});
  }
  function createOverlayHost(doc,options={}){
    let active=null,disposed=false;
    function assess(capability){
      if(disposed || options.allowOverlay===false || options.capabilities?.allowOverlay===false)
        return {ok:false,reason:disposed?'disposed':'overlay-denied'};
      if(['overlay.anchor-source','overlay.collision','overlay.portal','overlay.dismiss','overlay.unclip-preserve-geometry'].includes(capability))return {ok:true};
      return {ok:false,reason:'not-overlay-capability'};
    }
    function close(reason='dismissed'){
      if(!active)return;
      const a=active;active=null;clearTimeout(a.timer);clearTimeout(a.fadeTimer);
      if(a.kind==='unclip-top-layer')traceHover('close-committed',a.source,{reason,open:false});
      a.cleanup.forEach(fn=>fn());a.node.remove();
      options.onClose?.(reason);
    }
    function open(source,content,lifecycle={},scrollContainer){
      if(!assess('overlay.anchor-source').ok)return {ok:false,reason:'overlay-denied'};
      if(!source?.isConnected)return {ok:false,reason:'source-unavailable'};
      close('replaced');
      const pane=doc.createElement('section');pane.className='mcel-negotiated-floating-card';
      pane.setAttribute('role','region');pane.setAttribute('aria-label','Expanded record preview');
      pane.tabIndex=-1;pane.append(content);
      doc.body.append(pane);
      const sourceRect=source.getBoundingClientRect(),vp=doc.defaultView;
      const width=Math.min(Math.max(240,sourceRect.width),Math.max(100,vp.innerWidth-12));
      pane.style.width=width+'px';
      // Source-overlay default: original pointer coordinates lie inside card.
      const maxX=Math.max(6,vp.innerWidth-width-6);
      const left=Math.max(6,Math.min(sourceRect.left,maxX));
      let top=Math.max(6,sourceRect.top);
      const maxY=Math.max(6,vp.innerHeight-pane.getBoundingClientRect().height-6);
      top=Math.min(top,maxY);
      pane.style.left=left+'px';pane.style.top=top+'px';
      const state={node:pane,source,cleanup:[],timer:null};active=state;
      const cancel=()=>{clearTimeout(state.timer);state.timer=null;};
      function selectionInside(){
        const sel=doc.getSelection?.();
        return !!(sel && !sel.isCollapsed && (pane.contains(sel.anchorNode)||pane.contains(sel.focusNode)));
      }
      function schedule(){
        if(active!==state)return;
        cancel();const delay=lifecycle.pointerLeaveDelayMs??2000;
        state.timer=setTimeout(()=>{
          if(active!==state)return;
          if(lifecycle.preserveWhileSelecting && (selectionInside()||pane.contains(doc.activeElement))){
            schedule();return;
          }
          close('pointer-leave-timeout');
        },delay);
      }
      const exit=ev=>{
        const dest=ev.relatedTarget;
        if(dest&&(source.contains(dest)||pane.contains(dest)))return;
        schedule();
      };
      const enter=()=>cancel();
      const scroll=ev=>{
        if(active!==state||lifecycle.onUserScroll!=='fade-now')return;
        // Scroll on the floating card itself must remain useful for long records.
        if(pane.contains(ev.target))return;
        pane.classList.add('mcel-negotiated-floating-card--fade');
        clearTimeout(state.fadeTimer);
        state.fadeTimer=setTimeout(()=>{if(active===state)close('user-scroll');},130);
      };
      for(const element of [source,pane]){
        element.addEventListener('pointerenter',enter);
        element.addEventListener('pointerleave',exit);
        state.cleanup.push(()=>{element.removeEventListener('pointerenter',enter);element.removeEventListener('pointerleave',exit);});
      }
      for(const event of ['wheel','scroll','touchmove']){
        scrollContainer?.addEventListener(event,scroll,{capture:true,passive:true});
        state.cleanup.push(()=>scrollContainer?.removeEventListener(event,scroll,true));
      }
      // Middle-button auto-scroll may generate scroll rather than wheel events.
      const pointerDown=ev=>{if(ev.button===1 && !pane.contains(ev.target))scroll(ev);};
      scrollContainer?.addEventListener('pointerdown',pointerDown,true);
      state.cleanup.push(()=>scrollContainer?.removeEventListener('pointerdown',pointerDown,true));
      const escape=ev=>{if(ev.key==='Escape')close('escape');};
      doc.addEventListener('keydown',escape,true);state.cleanup.push(()=>doc.removeEventListener('keydown',escape,true));
      return {ok:true,node:pane,position:{left,top,width},close};
    }
    // One record, one real DOM identity. The collection is ONLY its scroll host.
    // All extension geometry is planned before promoting the existing row into
    // the top layer; scrollbar geometry is part of that same presentation.
    function traceHover(kind,source,data={}) {
      // No raw field values or document text; plugins observe geometry/decisions.
      if(!global?.dispatchEvent || typeof global.CustomEvent!=='function')return;
      global.dispatchEvent(new global.CustomEvent('mcel-hover-trace',{
        detail:{scope:'mcel-row-hover',kind,source,rowId:source?.dataset.rowId||null,...data}}));
    }
    function openUnclip(source,extension,lifecycle={},scrollContainer,scrollHost,callbacks={}){
      if(!assess('overlay.unclip-preserve-geometry').ok)return {ok:false,reason:'unclip-denied'};
      if(!source?.isConnected || typeof source.showPopover!=='function')
        return {ok:false,reason:'source-unavailable'};
      // Repeated hover activation must never tear down and reopen the same row.
      if(active?.kind==='unclip-top-layer' && active.source===source){
        traceHover('open-same-row-ignored',source,{reason:'already-open'});
        return {ok:true,node:active.node,alreadyOpen:true,close};
      }
      close('replaced');
      const win=doc.defaultView,origin=source.getBoundingClientRect(),parent=source.parentElement;
      if(!parent||origin.width<1||origin.height<1)return {ok:false,reason:'source-has-no-geometry'};
      const initialStyle=source.getAttribute('style'),initialPopover=source.getAttribute('popover');
      const computed=win.getComputedStyle(source),originalTracks=computed.gridTemplateRows;
      const originalTrackCount=(originalTracks.match(/-?\d+(?:\.\d+)?px/g)||[]).length;
      const originalPaddingBottom=parseFloat(computed.paddingBottom)||0;
      const originalBorderWidth=parseFloat(computed.borderBottomWidth)||0;
      // Match the original blended row background while lifting the REAL node
      // above its previous painted ancestor. No copy of the row is created.
      const opaqueBackground=(()=>{
        const layers=[];
        for(let el=source;el;el=el.parentElement){
          const m=win.getComputedStyle(el).backgroundColor.match(
            /^rgba?\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\s*\)$/);
          if(m)layers.push([+m[1],+m[2],+m[3],m[4]===undefined?1:+m[4]]);
        }
        let rgb=[255,255,255];
        for(const [r,g,b,a] of layers.reverse())rgb=rgb.map((v,i)=>v*(1-a)+[r,g,b][i]*a);
        return 'rgb('+rgb.map(v=>Math.floor(v)).join(',')+')';
      })();
      const anchor=doc.createElement('div');anchor.className='mcel-unclip-layout-anchor';
      anchor.dataset.mcelPopoverAnchor='true';anchor.dataset.rowId=source.dataset.rowId;
      anchor.style.cssText=`height:${origin.height}px;width:${origin.width}px;box-sizing:border-box;flex:0 0 ${origin.height}px;`;
      // Exactly ONE visual card and ONE scrollport: the original source row.
      // The extension is just another in-flow grid child, not another viewport.
      const pane=doc.createElement('section');pane.className='mcel-unclip-card';
      pane.dataset.mcelUnclip='true';pane.dataset.mcelPresentationSlot='unclip-extension';
      pane.dataset.sourceRowId=source.dataset.rowId;pane.dataset.presentation='unclip-preserve-geometry';
      pane.setAttribute('role','region');pane.setAttribute('aria-label','Additional information for this record');
      const extensionFrame=doc.createElement('div');extensionFrame.className='mcel-unclip-extension-frame';
      extensionFrame.append(extension);pane.append(extensionFrame);
      pane.style.gridRow=String(Math.max(1,originalTrackCount+1));
      pane.style.visibility='hidden';
      source.append(pane);
      // The scrollbar belongs to the whole row. It has to escape the row's
      // own overflow clipping, so it is an accessory in the browser top layer,
      // with a shared logical pointer interaction region.
      const track=doc.createElement('div');track.className='mcel-unclip-row-scroll-track';
      track.setAttribute('popover','manual');track.setAttribute('role','scrollbar');
      track.setAttribute('aria-label','Scroll the expanded original record');
      const thumb=doc.createElement('div');thumb.className='mcel-unclip-row-scroll-thumb';
      track.append(thumb);doc.body.append(track);
      const set=(key,value)=>source.style.setProperty(key,value,'important');
      const savedScroll=source.scrollTop;
      parent.insertBefore(anchor,source);source.mcelPopoverAnchor=anchor;
      source.setAttribute('popover','manual');source.classList.add('mcel-source-toplayer');
      set('left',origin.left+'px');set('top',origin.top+'px');set('width',origin.width+'px');
      set('background-color',opaqueBackground);
      set('--mcel-hover-original-height',origin.height+'px');
      set('--mcel-hover-original-border-width',originalBorderWidth+'px');
      set('--mcel-hover-original-border-color',computed.borderBottomColor);
      const fullExtensionHeight=()=>{
        // Measure the real in-flow content without making a visible change:
        // the row is not in the top layer during initial negotiation.
        const height=Math.max(1,pane.scrollHeight,pane.getBoundingClientRect().height);
        return Math.ceil(height);
      };
      const planFor=(extHeight)=>{
        const r=anchor.getBoundingClientRect();
        const fullHeight=Math.ceil(origin.height+extHeight+originalPaddingBottom);
        const below=Math.max(origin.height,win.innerHeight-r.top-8);
        const height=Math.max(origin.height,Math.min(fullHeight,below));
        const placement=win.innerWidth-r.right>=12?'right':r.left>=12?'left':'unavailable';
        return {width:origin.width,height,fullHeight,extensionHeight:extHeight,
          needsRail:fullHeight>height+1,placement};
      };
      function apply(next){
        // One synchronous commit: no independent extension resize/reclip.
        set('grid-template-rows',originalTracks+' '+next.extensionHeight+'px');
        set('height',next.height+'px');
        set('overflow-y','auto');set('overflow-x','hidden');set('scrollbar-width','none');
        track.hidden=!next.needsRail||next.placement==='unavailable';
        const r=anchor.getBoundingClientRect();
        const railX=next.placement==='right'?r.right:r.left-12;
        track.style.left=railX+'px';track.style.top=r.top+'px';
        track.style.height=next.height+'px';
        track.classList.toggle('mcel-unclip-row-scroll-track--left',next.placement==='left');
        const thumbHeight=Math.min(next.height,Math.max(18,next.height*next.height/next.fullHeight));
        thumb.style.height=thumbHeight+'px';
        const max=Math.max(0,source.scrollHeight-source.clientHeight);
        thumb.style.transform='translateY('+(max?source.scrollTop/max*Math.max(0,next.height-thumbHeight):0)+'px)';
        traceHover('geometry-apply',source,{reason:'plan-applied',geometry:next,
          extra:{anchorX:r.left,anchorY:r.top,anchorRight:r.right,railRequestedX:railX,
            railRequestedHeight:next.height,rowRequestedHeight:next.height}});
      }
      let plan=planFor(fullExtensionHeight());
      traceHover('geometry-plan',source,{geometry:{...plan,x:origin.x,y:origin.y},reason:'before-top-layer-commit'});
      apply(plan);
      const restore=()=>{
        try{if(track.matches(':popover-open'))track.hidePopover();}catch(_ignored){}
        track.remove();
        try{if(source.matches(':popover-open'))source.hidePopover();}catch(_ignored){}
        if(initialStyle===null)source.removeAttribute('style');else source.setAttribute('style',initialStyle);
        if(initialPopover===null)source.removeAttribute('popover');else source.setAttribute('popover',initialPopover);
        source.classList.remove('mcel-source-toplayer');
        delete source.mcelPopoverAnchor;anchor.remove();source.scrollTop=savedScroll;
      };
      try{source.showPopover();track.showPopover();pane.style.visibility='visible';
        // Trace actual browser hit-test geometry, not the style values which
        // can be displaced by UA popover centering/margins.
        const paintedRow=source.getBoundingClientRect(),paintedTrack=track.getBoundingClientRect();
        traceHover('open-committed',source,{open:true,geometry:{...plan,x:paintedRow.x,y:paintedRow.y,
          rowRight:paintedRow.right,railLeft:paintedTrack.left,
          railGap:plan.placement==='right'?paintedTrack.left-paintedRow.right:
            paintedRow.left-paintedTrack.right}});} 
      catch(error){traceHover('open-failed',source,{reason:String(error).slice(0,200)});restore();pane.remove();return {ok:false,reason:'source-top-layer-promotion-failed',message:String(error)};}
      const state={node:pane,source,anchor,track,cleanup:[restore],timer:null,kind:'unclip-top-layer',plan};active=state;
      const cleanup=fn=>state.cleanup.push(fn);
      const handoff=createScrollHandoff(source,scrollHost,{...lifecycle,
        onDiagnostic:data=>traceHover('wheel-arbitration',source,{reason:data.reason,scrollOwner:data.owner,extra:data})});
      cleanup(()=>handoff.destroy());
      let previousScrollOwner=handoff.state.owner;
      const reportScrollOwner=()=>win.queueMicrotask(()=>{
        if(active!==state)return;
        const current=handoff.state;
        if(current.owner!==previousScrollOwner){
          traceHover('scroll-owner-change',source,{scrollOwner:current.owner,extra:current});
          previousScrollOwner=current.owner;
        }
      });
      source.addEventListener('wheel',reportScrollOwner);
      cleanup(()=>source.removeEventListener('wheel',reportScrollOwner));
      const sync=()=>{
        const max=Math.max(0,source.scrollHeight-source.clientHeight);
        const height=track.getBoundingClientRect().height;
        const tHeight=Math.min(height,Math.max(18,height*height/Math.max(1,source.scrollHeight)));
        thumb.style.height=tHeight+'px';
        thumb.style.transform='translateY('+(max?source.scrollTop/max*Math.max(0,height-tHeight):0)+'px)';
      };
      source.addEventListener('scroll',sync,{passive:true});cleanup(()=>source.removeEventListener('scroll',sync));
      let drag=null;
      const trackDown=ev=>{ev.preventDefault();ev.stopPropagation();
        if(ev.target!==thumb){const r=track.getBoundingClientRect();
          source.scrollTop=(ev.clientY-r.top)/Math.max(1,r.height)*Math.max(0,source.scrollHeight-source.clientHeight);}
        drag={y:ev.clientY,offset:source.scrollTop};track.setPointerCapture?.(ev.pointerId);};
      const trackMove=ev=>{if(!drag)return;
        source.scrollTop=drag.offset+(ev.clientY-drag.y)*Math.max(0,source.scrollHeight-source.clientHeight)/
          Math.max(1,track.clientHeight-thumb.offsetHeight);};
      const trackUp=()=>{drag=null;};
      for(const [name,fn] of [['pointerdown',trackDown],['pointermove',trackMove],
        ['pointerup',trackUp],['pointercancel',trackUp]]){
        track.addEventListener(name,fn);cleanup(()=>track.removeEventListener(name,fn));
      }
      const trackWheel=ev=>handoff.handleWheel(ev);
      track.addEventListener('wheel',trackWheel,{passive:false});
      cleanup(()=>track.removeEventListener('wheel',trackWheel));
      // Pointer ownership is a geometric state, not a CSS :hover query.
      // Layout-induced pointerleave must never close a card under the mouse.
      let point=null;
      const inRect=(r,x,y)=>x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom;
      const inside=()=>!!(point&&(inRect(source.getBoundingClientRect(),point.x,point.y)||
        (!track.hidden&&inRect(track.getBoundingClientRect(),point.x,point.y))));
      const selectionInside=()=>{const sel=doc.getSelection?.();
        return !!(sel&&!sel.isCollapsed&&(source.contains(sel.anchorNode)||source.contains(sel.focusNode)));};
      const cancel=()=>{win.clearTimeout(state.timer);state.timer=null;};
      const schedule=()=>{if(active!==state||state.timer)return;
        traceHover('leave-timer-start',source,{pointer:point,reason:'pointer-outside',scrollOwner:'source-row'});
        state.timer=win.setTimeout(()=>{state.timer=null;if(active!==state)return;
          if(inside()){traceHover('leave-timer-canceled',source,{pointer:point,reason:'pointer-inside'});return;}
          if(lifecycle.preserveWhileSelecting&&(selectionInside()||source.contains(doc.activeElement))){traceHover('leave-timer-deferred',source,{reason:'selection-or-focus'});schedule();return;}
          traceHover('leave-timer-expired',source,{pointer:point,reason:'pointer-outside'});
          close('pointer-leave-timeout');
        },lifecycle.pointerLeaveDelayMs??1000);
      };
      let previousInside=null;
      const notePointer=(eventType,ev)=>{
        point={x:ev.clientX,y:ev.clientY};
        const nowInside=inside();
        if(previousInside!==nowInside){
          const rr=source.getBoundingClientRect(),tr=track.getBoundingClientRect();
          traceHover('hit-region-transition',source,{reason:eventType,pointer:point,
            extra:{inside:nowInside,previousInside,row:{left:rr.left,top:rr.top,right:rr.right,bottom:rr.bottom},
              rail:{left:tr.left,top:tr.top,right:tr.right,bottom:tr.bottom,hidden:track.hidden}}});
          previousInside=nowInside;
        }
        return nowInside;
      };
      const pointerMove=ev=>{
        if(notePointer('pointermove',ev))cancel();else schedule();
      };
      const pointerOut=ev=>{
        if(!notePointer('pointerleave',ev))schedule();
      };
      const pointerIn=ev=>{notePointer('pointerenter',ev);cancel();};
      doc.addEventListener('pointermove',pointerMove,true);
      source.addEventListener('pointerenter',pointerIn);
      source.addEventListener('pointerleave',pointerOut);
      track.addEventListener('pointerenter',pointerIn);
      track.addEventListener('pointerleave',pointerOut);
      cleanup(()=>{cancel();doc.removeEventListener('pointermove',pointerMove,true);
        source.removeEventListener('pointerenter',pointerIn);source.removeEventListener('pointerleave',pointerOut);
        track.removeEventListener('pointerenter',pointerIn);track.removeEventListener('pointerleave',pointerOut);});
      const escape=ev=>{if(ev.key==='Escape')close('escape');};
      doc.addEventListener('keydown',escape,true);cleanup(()=>doc.removeEventListener('keydown',escape,true));
      let frame=null;
      const follow=(reason='frame')=>{
        if(active!==state)return;
        if(!source.isConnected||!anchor.isConnected){close('source-detached');return;}
        const r=anchor.getBoundingClientRect(),changed=[];
        for(const [name,value] of [['left',r.left+'px'],['top',r.top+'px'],
          ['width',plan.width+'px'],['height',plan.height+'px'],
          ['grid-template-rows',originalTracks+' '+plan.extensionHeight+'px'],
          ['overflow-y','auto'],['overflow-x','hidden'],['scrollbar-width','none']]){
          if(source.style.getPropertyValue(name)!==value){
            changed.push({property:name,before:source.style.getPropertyValue(name),after:value});
            source.style.setProperty(name,value,'important');
          }
        }
        const railX=(plan.placement==='right'?r.right:r.left-12)+'px';
        if(track.style.left!==railX)changed.push({property:'rail.left',before:track.style.left,after:railX});
        if(track.style.top!==r.top+'px')changed.push({property:'rail.top',before:track.style.top,after:r.top+'px'});
        track.style.left=railX;track.style.top=r.top+'px';sync();
        if(changed.length)traceHover('follow-repair',source,{reason,geometry:{anchorLeft:r.left,
          anchorTop:r.top,anchorRight:r.right,rowLeft:source.getBoundingClientRect().left,
          rowTop:source.getBoundingClientRect().top,railLeft:track.getBoundingClientRect().left},
          extra:{changed,sourceScrollTop:source.scrollTop,hostScrollTop:scrollContainer?.scrollTop??null}});
      };
      let followReason='frame';
      const requestFollow=(reason='scroll')=>{followReason=typeof reason==='string'?reason:'scroll';
        if(frame!==null)return;
        frame=win.requestAnimationFrame(()=>{frame=null;follow(followReason);});};
      for(const target of [scrollContainer,win,doc]){
        const onScroll=()=>requestFollow('scroll-event');
        target?.addEventListener('scroll',onScroll,true);
        cleanup(()=>target?.removeEventListener('scroll',onScroll,true));
      }
      const obs=new MutationObserver(records=>{
        if(records.some(r=>r.attributeName==='style'))requestFollow('style-mutation');
      });obs.observe(source,{attributes:true,attributeFilter:['style']});
      cleanup(()=>obs.disconnect());
      const replan=(reason='content-update')=>{
        if(active!==state)return false;
        const next=planFor(fullExtensionHeight()),previous={...plan};
        traceHover('geometry-replan',source,{geometry:next,reason,
          extra:{previous,sourceScrollTop:source.scrollTop,hostScrollTop:scrollContainer?.scrollTop??null}});
        apply(next);plan=state.plan=next;follow('replan');return true;
      };
      const onResize=()=>replan('window-resize');
      win.addEventListener('resize',onResize);cleanup(()=>win.removeEventListener('resize',onResize));
      cleanup(()=>{if(frame!==null)win.cancelAnimationFrame(frame);});
      pane.mcelUnclipRefresh=()=>{
        if(active!==state)return false;
        const h=fullExtensionHeight();
        if(Math.abs(h-plan.extensionHeight)>1)replan('extension-content-height-changed');else{sync();requestFollow('extension-content-refresh');}
        return true;
      };
      pane.mcelScrollHandoff=handoff;
      // Pointer location on initial open comes from real pointer events; don't
      // invent a leave event merely because promotion changed browser hit-test.
      return {ok:true,node:pane,position:{left:origin.left,top:origin.top,width:origin.width},close};
    }
    return Object.freeze({assess,open,openUnclip,close,destroy(){disposed=true;close('destroy');},get active(){return active?.node||null;},
      get sourceConnected(){return !!active?.source?.isConnected;},
      get sourceId(){return active?.source?.dataset.rowId||null;}});
  }
  global.McelPresentationHosts=Object.freeze({createScrollHost,createOverlayHost,createScrollHandoff,createCollectionUnclip});
})(typeof window!=='undefined'?window:globalThis);
