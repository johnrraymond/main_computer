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
      if(['scroll.anchor-preserve','scroll.observe-visibility','scroll.user-activity'].includes(capability))return {ok:true};
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
    function destroy(){if(disposed)return;disposed=true;list.removeEventListener('wheel',onScroll);
      list.removeEventListener('pointerdown',onScroll);list.removeEventListener('touchmove',onScroll);}
    return Object.freeze({assess,reveal,visibility,captureAnchor,restoreAnchor,destroy,
      get lastUserScroll(){return lastUserScroll;}});
  }
  function createOverlayHost(doc,options={}){
    let active=null,disposed=false;
    function assess(capability){
      if(disposed || options.allowOverlay===false || options.capabilities?.allowOverlay===false)
        return {ok:false,reason:disposed?'disposed':'overlay-denied'};
      if(['overlay.anchor-source','overlay.collision','overlay.portal','overlay.dismiss'].includes(capability))return {ok:true};
      return {ok:false,reason:'not-overlay-capability'};
    }
    function close(reason='dismissed'){
      if(!active)return;
      const a=active;active=null;clearTimeout(a.timer);clearTimeout(a.fadeTimer);
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
    return Object.freeze({assess,open,close,destroy(){disposed=true;close('destroy');},get active(){return active?.node||null;},
      get sourceConnected(){return !!active?.source?.isConnected;},
      get sourceId(){return active?.source?.dataset.rowId||null;}});
  }
  global.McelPresentationHosts=Object.freeze({createScrollHost,createOverlayHost});
})(typeof window!=='undefined'?window:globalThis);
