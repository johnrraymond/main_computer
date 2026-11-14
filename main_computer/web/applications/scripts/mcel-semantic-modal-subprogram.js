/* Canonical MCEL semantic modal subprogram: geometry is negotiated and committed.
 * This is presentation infrastructure. Backend work and its lifecycle are not
 * owned by the modal and remain live after the presentation is dismissed.
 */
(function (global, factory) {
  const api = factory(global);
  if (typeof module === "object" && module.exports) module.exports = api;
  global.McelSemanticModalSubprogram = api;
})(typeof window === "object" ? window : globalThis, function (global) {
  "use strict";
  const MODES = new Set(["fixed", "flexible"]);
  const OVERFLOWS = new Set(["scroll", "clip", "wrap"]);
  const finite = (n) => typeof n === "number" && Number.isFinite(n);

  function define(spec) {
    if (!spec || !spec.id || !spec.geometry || !Array.isArray(spec.regions))
      throw Error("MCEL_MODAL_CONTRACT_INVALID:definition");
    const g = spec.geometry;
    const minWidth = Number(g.minimumWidth), minHeight = Number(g.minimumHeight);
    const prefWidth = Number(g.preferredWidth), prefHeight = Number(g.preferredHeight);
    const maxWidthPercent = Number(g.maximumRequestedWidthPercent);
    const maxHeightPercent = Number(g.maximumRequestedHeightPercent);
    if (![minWidth,minHeight,prefWidth,prefHeight,maxWidthPercent,maxHeightPercent].every(finite) ||
        minWidth<=0 || minHeight<=0 || prefWidth<minWidth || prefHeight<minHeight ||
        maxWidthPercent<=0 || maxWidthPercent>100 || maxHeightPercent<=0 || maxHeightPercent>100)
      throw Error("MCEL_MODAL_CONTRACT_INVALID:geometry");
    const seen = new Set();
    let reserved = 0, flex = 0;
    const regions = spec.regions.map((r) => {
      if (!/^[a-z][a-z0-9-]*$/.test(String(r.id||"")) || seen.has(r.id) || !MODES.has(r.budget) ||
          !OVERFLOWS.has(r.overflow)) throw Error("MCEL_MODAL_CONTRACT_INVALID:region");
      seen.add(r.id);
      const size = r.budget === "fixed" ? r.height : r.minimumHeight;
      if (!finite(size) || size<=0) throw Error("MCEL_MODAL_CONTRACT_INVALID:budget");
      reserved += size;
      if (r.budget === "flexible") flex++;
      const weight = r.budget === "flexible" ? (r.weight===undefined?1:Number(r.weight)) : null;
      if (r.budget === "flexible" && (!finite(weight) || weight<=0))
        throw Error("MCEL_MODAL_CONTRACT_INVALID:flex-weight");
      return Object.freeze({id:r.id,budget:r.budget,overflow:r.overflow,
        height:r.budget==="fixed"?size:null,
        minimumHeight:r.budget==="flexible"?size:null,weight});
    });
    if (seen.size===0 || flex===0 || reserved>minHeight)
      throw Error("MCEL_MODAL_CONTRACT_UNSATISFIABLE:region-budgets");
    return Object.freeze({id:String(spec.id),geometry:Object.freeze({minWidth,minHeight,prefWidth,prefHeight,
      maxWidthPercent,maxHeightPercent}),regions:Object.freeze(regions)});
  }

  // The soft percentage is NOT a hard cap. A hard minimum can exceed the view.
  function allocate(definition, available) {
    const g = definition.geometry;
    if (!finite(available.width) || !finite(available.height) ||
        available.width<=0 || available.height<=0) throw Error("MCEL_MODAL_VIEWPORT_UNAVAILABLE");
    const width = Math.round(Math.max(g.minWidth,Math.min(g.prefWidth,
      available.width * g.maxWidthPercent/100)));
    const height = Math.round(Math.max(g.minHeight,Math.min(g.prefHeight,
      available.height * g.maxHeightPercent/100)));
    const allocated = {};
    let fixed = 0, flexibleMin = 0, totalWeight=0;
    for (const r of definition.regions) {
      if(r.budget==="fixed")fixed+=r.height;
      else {flexibleMin+=r.minimumHeight;totalWeight+=r.weight;}
    }
    const surplus=height-fixed-flexibleMin;
    if(surplus<0)throw Error("MCEL_MODAL_CONTRACT_UNSATISFIABLE:allocation");
    let distributed=0,weightSoFar=0;
    for (const r of definition.regions) {
      if(r.budget==="fixed") {allocated[r.id]=r.height;continue;}
      weightSoFar+=r.weight;
      const cumulative=Math.round(surplus*weightSoFar/totalWeight);
      allocated[r.id]=r.minimumHeight+cumulative-distributed;
      distributed=cumulative;
    }
    if (Object.values(allocated).some((value)=>value<=0))
      throw Error("MCEL_MODAL_CONTRACT_UNSATISFIABLE:allocation");
    return Object.freeze({width,height,regions:Object.freeze(allocated),
      exceedsViewport:width>available.width || height>available.height});
  }

  function create({definition,document:doc,root,viewport=null,onDismiss=()=>{},onGeometry=()=>{}}) {
    if (!doc || !root || !definition?.regions) throw Error("MCEL_MODAL_INVALID_HOST");
    const backdrop=doc.createElement("div");
    backdrop.className="mcel-semantic-modal-viewport";
    backdrop.dataset.mcelSemanticModal=definition.id;
    backdrop.setAttribute("role","presentation");
    backdrop.hidden=true;
    const panel=doc.createElement("section");
    panel.className="mcel-semantic-modal-panel";
    panel.setAttribute("role","dialog");
    panel.setAttribute("aria-modal","true");
    panel.setAttribute("aria-label","Semantic operation subprogram");
    panel.tabIndex=-1;
    const elements={};
    for (const r of definition.regions) {
      const el=doc.createElement("div");
      el.className="mcel-semantic-modal-region mcel-semantic-modal-region--"+r.id;
      el.dataset.mcelModalRegion=r.id;
      el.dataset.mcelBudget=r.budget;
      el.dataset.mcelOverflow=r.overflow;
      if (r.overflow==="scroll") el.tabIndex=0;
      panel.appendChild(el);
      elements[r.id]=el;
    }
    backdrop.appendChild(panel);root.appendChild(backdrop);
    let open=false, disposed=false, allocation=null, priorFocus=null, lifecycle="CREATED";
    let blocking=true;
    function setBlocking(value) {
      blocking=value!==false;
      backdrop.dataset.mcelBlocking=blocking?"true":"false";
      panel.setAttribute("aria-modal",blocking?"true":"false");
    }
    setBlocking(true);
    // The host can provide the visible semantic workspace as the available
    // presentation viewport. Otherwise the modal uses the browser viewport.
    function viewportGeometry(){
      const w=global.innerWidth,h=global.innerHeight;
      if(!viewport)return {left:0,top:0,width:w,height:h};
      const rect=viewport.getBoundingClientRect();
      const left=Math.max(0,Math.min(w,rect.left));
      const top=Math.max(0,Math.min(h,rect.top));
      const right=Math.max(left,Math.min(w,rect.right));
      const bottom=Math.max(top,Math.min(h,rect.bottom));
      return {left,top,width:Math.max(1,right-left),height:Math.max(1,bottom-top)};
    }
    let lastViewport=null;
    function positionViewport(rect){
      if(!viewport)return;
      backdrop.style.left=rect.left+"px";
      backdrop.style.top=rect.top+"px";
      backdrop.style.right="auto";
      backdrop.style.bottom="auto";
      backdrop.style.width=rect.width+"px";
      backdrop.style.height=rect.height+"px";
    }
    const onKey=(e)=>{
      if(!open)return;
      if(e.key==="Escape"){e.preventDefault();dismiss();return;}
      if(e.key!=="Tab" || !blocking)return;
      const candidates=Array.from(panel.querySelectorAll('button:not([disabled]), [tabindex]:not([tabindex="-1"])'));
      const items=candidates.filter((el)=>el.getClientRects().length>0);
      if(!items.length){e.preventDefault();panel.focus();return;}
      const first=items[0],last=items[items.length-1],current=doc.activeElement;
      if(e.shiftKey && (current===first || !panel.contains(current))){e.preventDefault();last.focus();}
      else if(!e.shiftKey && (current===last || !panel.contains(current) || current===panel)){
        e.preventDefault();first.focus();
      }
    };
    const onResize=()=>{if (open) commit("viewport-change");};
    const onScroll=()=>{
      if(!open || !viewport)return;
      const rect=viewportGeometry();
      positionViewport(rect);
      // Scrolling the host does not renegotiate content geometry unless the
      // visible workspace dimensions actually change.
      if(!lastViewport || rect.width!==lastViewport.width || rect.height!==lastViewport.height)
        commit("viewport-change");
    };
    function commit(reason) {
      // Geometry is recomputed ONLY on opening or actual viewport changes.
      // No MutationObserver, ResizeObserver, or content-driven reflow.
      const rect=viewportGeometry();
      lastViewport=rect;
      positionViewport(rect);
      allocation=allocate(definition,{width:rect.width,height:rect.height});
      panel.style.width=allocation.width+"px";
      panel.style.height=allocation.height+"px";
      panel.style.gridTemplateRows=definition.regions.map((r)=>allocation.regions[r.id]+"px").join(" ");
      panel.dataset.mcelGeometryCommit=reason;
      backdrop.dataset.mcelOversized=allocation.exceedsViewport?"true":"false";
      onGeometry(allocation,reason);
      return allocation;
    }
    function show(){
      if(disposed)throw Error("MCEL_MODAL_SUBPROGRAM_DISPOSED");
      if(open)return allocation;
      priorFocus=doc.activeElement;
      commit("open");
      open=true;lifecycle="ACTIVE";backdrop.hidden=false;
      doc.addEventListener("keydown",onKey);
      global.addEventListener("resize",onResize);
      if(viewport)global.addEventListener("scroll",onScroll,true);
      panel.focus({preventScroll:true});
      return allocation;
    }
    function dismiss(){
      if(!open)return;
      open=false;lifecycle="DISMISSED";backdrop.hidden=true;
      doc.removeEventListener("keydown",onKey);
      global.removeEventListener("resize",onResize);
      if(viewport)global.removeEventListener("scroll",onScroll,true);
      if(priorFocus?.isConnected)priorFocus.focus({preventScroll:true});
      onDismiss();
    }
    function dispose(){if(open)dismiss();disposed=true;lifecycle="DISPOSED";backdrop.remove();}
    // Content updates never trigger a geometry commit.
    return Object.freeze({show,dismiss,dispose,setBlocking,region:(id)=>elements[id],
      isOpen:()=>open,lifecycle:()=>lifecycle,geometry:()=>allocation,element:backdrop,panel});
  }
  return Object.freeze({define,allocate,create});
});
