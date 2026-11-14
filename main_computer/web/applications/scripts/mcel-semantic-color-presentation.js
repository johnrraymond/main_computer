/* Canonical semantic Color Law binding for the MCEL operational renderer.
 * Applications author meaning on IR; Theme owns every physical color value.
 * This consumer changes no legacy element without an authored color contract.
 * Browser input pseudo-states are handled by generated CSS, never JS events.
 */
(function(global) { "use strict";
  const schema="mcel.semantic-color-presentation.v1";
  let mountSequence=0;
  const own=(v,k)=>Object.prototype.hasOwnProperty.call(v,k);
  const ref=v=>typeof v==="string"?v:v?.ref;
  const records=v=>Array.isArray(v)?v:[];
  const failed=(reason,details=[])=>({schema,status:"unresolved",reason,details});
  function declarationKind(n) {
    if(n.nodeKind==="control")return "control";
    if(n.nodeKind==="form")return "form";
    if(n.nodeKind==="state-value")return "text";
    if(n.nodeKind==="plot")return "plot";
    if(n.nodeKind==="operation-evidence")return "evidence";
    if(n.nodeKind==="embedded-chat")return "embedded";
    return null;
  }
  function declaredRecords(ir) {
    const surface=records(ir?.surfaces)[0];
    if(ir?.schema!=="mcel.application-ir.v1" || records(ir.surfaces).length!==1 ||
        !Array.isArray(surface?.nodes))return [];
    return [
      ...records(surface.interfaceIntent?.regions)
        .filter(r=>own(r,"color")).map(r=>({id:r.id,kind:"region",color:r.color})),
      ...records(surface.nodes).filter(n=>own(n,"color"))
        .map(n=>({id:n.id,kind:declarationKind(n),intentId:ref(n.intent),color:n.color}))
    ];
  }
  function validate({ir,law,composition=null}={}) {
    if(!law?.validateColorDeclaration || !law?.compileDeclaredColor)
      return failed("canonical-color-law-unavailable");
    if(ir?.schema!=="mcel.application-ir.v1" || records(ir.surfaces).length!==1)
      return failed("canonical-ir-required");
    const declarations=declaredRecords(ir),errors=[];
    // In generated workspaces the synthesized region IDs intentionally differ
    // from authoring regions. Do not pretend those are source-bound yet.
    if(composition && declarations.some(d=>d.kind==="region"))
      return failed("generated-region-color-binding-not-declared");
    for(const entry of declarations) {
      const verdict=law.validateColorDeclaration(entry.color,entry.kind);
      if(verdict.status!=="declared")errors.push({id:entry.id,reason:verdict.reason});
    }
    if(errors.length)return failed("invalid-color-declarations",errors);
    return {schema,status:"valid",declarations,authored:declarations.length,
      legacy:records(ir.surfaces[0].nodes).length-declarations.filter(x=>x.kind!=="region").length};
  }
  function findTarget(root,entry) {
    const attr=entry.kind==="region"?"data-mcel-region":"data-mcel-node";
    const holders=[...root.querySelectorAll(`[${attr}]`)]
      .filter(el=>el.getAttribute(attr)===entry.id);
    if(holders.length!==1)return null;
    const holder=holders[0];
    if(entry.kind==="region")return holder;
    // Bind the canonical action to its actual input control. A mode-tabs
    // wrapper is a noninteractive group, never the switch-mode action target.
    if(entry.kind==="control")return holder.querySelector('button[data-intent]') ||
      holder.querySelector('[data-intent]');
    if(entry.kind==="form")return holder.querySelector('form button[type="submit"]');
    if(entry.kind==="text")return holder.querySelector('output');
    if(entry.kind==="plot")return holder.querySelector('[data-mcel-plot]');
    if(entry.kind==="evidence"||entry.kind==="embedded")return holder;
    return null;
  }
  const fullColorMarker="data-mcel-semantic-color-full";
  function generatedBindings(root,plan,law) {
    const regions=plan.declarations.filter(e=>e.kind==="region");
    const allRegions=records(plan.ir?.surfaces?.[0]?.interfaceIntent?.regions);
    // Only a complete source-authored region contract opts into generated
    // descendants. Partial declarations remain exactly on their v1 path.
    const complete=allRegions.length>0 && allRegions.length===regions.length;
    if(!complete)return {complete:false,bindings:[],unresolved:[]};
    const coloredNodes=new Set(plan.declarations.filter(e=>e.kind!=="region").map(e=>e.id));
    const consumed=new Set(plan.declarations.map(e=>findTarget(root,e)).filter(Boolean));
    const bindings=[],unresolved=[];
    // A built-in widget with its own semantic Color Law (E/W/G), or an
    // embedded, independently governed app, is not reclassified by the host.
    const excluded='[data-mcel-embedded-chat],.mcel-diagnostics-counter,[data-mcel-color-role^="diagnostic."]';
    const isExcluded=element=>!!element.closest(excluded);
    function add(element,color,kind="control") {
      if(consumed.has(element)||isExcluded(element))return;
      const verdict=law.validateColorDeclaration(color,kind);
      if(verdict.status!=="declared"){
        unresolved.push({element,reason:verdict.reason});return;
      }
      consumed.add(element);
      bindings.push({id:element.getAttribute("data-mcel-node")||"generated:"+bindings.length,
        kind,color,element,generated:true});
    }
    for(const element of root.querySelectorAll('*')) {
      if(isExcluded(element)||consumed.has(element))continue;
      const node=element.closest('[data-mcel-node]');
      if(node && !coloredNodes.has(node.getAttribute('data-mcel-node')))continue;
      const role=element.getAttribute('data-mcel-generated-color-role');
      if(role){
        add(element,{role,surface:element.getAttribute('data-mcel-generated-color-surface')||'control',
          context:element.getAttribute('data-mcel-generated-color-context')||'form',
          consequence:element.getAttribute('data-mcel-generated-color-consequence')||'unspecified'},
          element.matches('[role="group"]')?'region':'control');
        continue;
      }
      if(element.matches('h1,h2,h3,h4,h5,h6,label,legend,small,summary,p,[data-mcel-value-label]')) {
        // These are renderer-created text presentations inside a source-bound
        // semantic region. The native content role, not the string, determines
        // the foreground class; the Theme determines the physical color.
        add(element,{role:element.matches('label,legend,small,[data-mcel-value-label]')?
          'text.muted':'text.primary',surface:'text',context:'nested'},'text');
      }
    }
    return {complete:true,bindings,unresolved};
  }
  function mount({root,ir,law=global.McelLabStyleLaw,theme="default",palette=null,
      operationForIntent=null}={}) {
    const plan=validate({ir,law});
    if(plan.status!=="valid")return {...plan,refresh:()=>plan,unmount:()=>{}};
    if(!root?.ownerDocument || !root.isConnected)return failed("mounted-root-required");
    plan.ir=ir;
    const missing=[],bindings=[];
    for(const entry of plan.declarations){
      const element=findTarget(root,entry);
      if(!element)missing.push({id:entry.id,reason:"canonical-target-unavailable"});
      else bindings.push({...entry,element});
    }
    if(missing.length)return failed("unbound-color-declarations",missing);
    const generated=generatedBindings(root,plan,law);
    if(generated.unresolved.length)return failed("invalid-generated-color-semantics",
      generated.unresolved.map(x=>({reason:x.reason})));
    bindings.push(...generated.bindings);
    const doc=root.ownerDocument,style=doc.createElement('style');
    const tag=String(++mountSequence);
    style.setAttribute('data-mcel-semantic-color-law',tag);
    const attributes=[];
    let disposed=false,lastSignature='',lastCss='',lastReport=null;
    const getPalette=()=>palette || law.themePaletteFor?.(theme) ||
      law.semanticPaletteFor(root,doc.defaultView.getComputedStyle.bind(doc.defaultView));
    const canonicalTheme=()=>law.normalizeTheme?.(theme)||theme;
    const priorFull=root.getAttribute(fullColorMarker);
    const capture=element=>{
      if(attributes.some(item=>item.element===element))return;
      attributes.push({element,priorId:element.getAttribute('data-mcel-color-state'),
        priorRole:element.getAttribute('data-mcel-color-role')});
    };
    function refresh(){
      if(disposed)return failed('presentation-unmounted');
      const colors=getPalette();
      if(bindings.length && !colors){lastReport=failed('computed-theme-palette-required');return lastReport;}
      const groups=new Map(),signature=[];
      for(const binding of bindings){
        const element=binding.element;
        if(!element.isConnected){lastReport=failed('semantic-target-detached',[{id:binding.id}]);return lastReport;}
        const availability=element.matches(':disabled,[aria-disabled="true"]')?'disabled':'available';
        const selection=element.matches('[aria-pressed="true"],[aria-selected="true"],[data-selected="true"]')?
          'on':element.matches('[aria-checked="mixed"]')?'mixed':binding.color.selection||'off';
        const op=binding.intentId && typeof operationForIntent==='function'?operationForIntent(binding.intentId):null;
        const operation=op||binding.color.operation||'idle';
        // Identical semantic treatments share one CSS state group: the number
        // of interaction rules scales with semantic variants, not DOM nodes.
        const key=JSON.stringify([binding.kind,binding.color,availability,selection,operation]);
        signature.push(key);
        if(!groups.has(key))groups.set(key,{binding,availability,selection,operation,elements:[]});
        groups.get(key).elements.push(element);
      }
      const sig=JSON.stringify([theme,colors,signature]);
      if(sig===lastSignature)return lastReport;
      const rules=[],assign=[];
      let i=0;
      for(const group of groups.values()){
        const id=`mcel-color-${tag}-${i++}`;
        const compiled=law.compileDeclaredColor({declaration:group.binding.color,
          kind:group.binding.kind,theme,palette:colors,availability:group.availability,
          selection:group.selection,operation:group.operation,id,important:true});
        if(compiled.status!=='resolved'){
          lastReport=failed('color-composition-refused',
            [{id:group.binding.id,reason:compiled.reason}]);return lastReport;
        }
        rules.push(compiled.css);
        for(const element of group.elements)assign.push({element,id,role:group.binding.color.role});
      }
      const css=[];
      if(generated.complete){
        const tokens=Object.entries(colors).map(([name,value])=>`${name}:${value}!important;`).join('');
        // Theme tokens are supplied by the same canonical authority in Lab
        // and standalone. Never mutate authored or application source CSS.
        css.push(`[${fullColorMarker}]{${tokens}color:${colors['--mcel-theme-ink']}!important;}`);
        css.push(`body.mcel-expert-page:has(> [${fullColorMarker}]){`+
          `background-color:${colors['--mcel-theme-card']}!important;color:${colors['--mcel-theme-ink']}!important;}`);
        css.push(`body.mcel-expert-page:has(> [${fullColorMarker}]) > [role="status"][aria-live]{`+
          `color:${colors['--mcel-theme-muted']}!important;}`);
        css.push(`[${fullColorMarker}] [data-mcel-plot] svg{`+
          `background-color:${colors['--mcel-theme-card']}!important;}`);
        css.push(`[${fullColorMarker}] [data-mcel-purpose="results"] output:empty::before{`+
          `color:${colors['--mcel-theme-muted']}!important;}`);
      }
      css.push(...rules);
      const newCss=`@layer mcel-semantic-color-canonical{${css.join('\n')}}`;
      for(const {element,id,role} of assign){
        capture(element);
        element.setAttribute('data-mcel-color-state',id);
        element.setAttribute('data-mcel-color-role',role);
      }
      if(generated.complete)root.setAttribute(fullColorMarker,'true');
      if(!style.isConnected && bindings.length)doc.head.append(style);
      const changedCss=newCss!==lastCss;
      if(changedCss){style.textContent=newCss;lastCss=newCss;}
      lastSignature=sig;
      lastReport={schema,status:'applied',theme:canonicalTheme(),authored:plan.authored,
        generated:generated.bindings.length,coverage:generated.complete?'source-complete':'partial',
        legacy:plan.legacy,unresolved:[],cssIdentity:tag,groups:groups.size,
        mechanism:'native-css',changedCss};
      return lastReport;
    }
    function setTheme(nextTheme,nextPalette=null){
      if(!law.themePaletteFor?.(nextTheme) && !nextPalette)
        return failed('unknown-theme');
      theme=nextTheme;palette=nextPalette;return refresh();
    }
    function unmount(){
      if(disposed)return;
      disposed=true;style.remove();
      if(priorFull===null)root.removeAttribute(fullColorMarker);
      else root.setAttribute(fullColorMarker,priorFull);
      for(const a of attributes){
        if(a.priorId===null)a.element.removeAttribute('data-mcel-color-state');
        else a.element.setAttribute('data-mcel-color-state',a.priorId);
        if(a.priorRole===null)a.element.removeAttribute('data-mcel-color-role');
        else a.element.setAttribute('data-mcel-color-role',a.priorRole);
      }
    }
    const initial=refresh();
    return {schema,status:initial.status,authored:plan.authored,legacy:plan.legacy,
      refresh,setTheme,unmount,report:()=>lastReport};
  }
  global.McelSemanticColorPresentation=Object.freeze({schema,declaredRecords,validate,mount});
})(typeof globalThis!=="undefined"?globalThis:this);
