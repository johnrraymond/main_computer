/* Patch 14.3: explicitly opt-in *read-only* composition host.
 * Layout decisions originate in the canonical semantic composer; this file
 * only assembles registered MCEL regions and invokes existing renderer/continuity.
 * No network calls, effect buttons, alternate renderer, domain names or templates.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.universal-semantic-composition.v1';
  const PROFILE='mcel.layout.universal.v1';
  const ROLES=new Map([
    ['observed-status','summary-strip'],
    ['comparable-entities','comparable-collection'],
    ['selected-details','context-inspector']
  ]);
  function validate(plan){
    if(plan?.schema!==SCHEMA || plan.profile!==PROFILE || plan.readOnly!==true ||
       plan.productionPromotion!==false || plan.collection?.noOperations!==true ||
       plan.collection?.spec?.schema!=='mcel.semantic-collection-spec.v2' ||
       !Array.isArray(plan.regions) || !plan.regions.length || !Array.isArray(plan.collection.rows))
      throw Error('MCEL_UNIVERSAL_LAB_INVALID_SEMANTIC_PLAN');
    const ids=new Set();
    for(const r of plan.regions){
      if(!ROLES.has(r.role) || ROLES.get(r.role)!==r.recipe || r.id!=='semantic-'+r.role || ids.has(r.id))
        throw Error('MCEL_UNIVERSAL_LAB_INVALID_REGION');
      ids.add(r.id);
    }
    if(!ids.has('semantic-comparable-entities'))throw Error('MCEL_UNIVERSAL_LAB_COLLECTION_REQUIRED');
    const children=plan.tree?.root?.children;
    if(plan.tree?.schema!=='mcel.layout-tree.v1' || !Array.isArray(children) ||
       children.length!==ids.size || children.some(n=>!ids.has(n.id) || n.selector!=='#'+n.id))
      throw Error('MCEL_UNIVERSAL_LAB_TREE_MISMATCH');
    return true;
  }
  function mount(target,plan,opts={}){
    if(opts.lab!==true)throw Error('MCEL_UNIVERSAL_LAB_EXPLICIT_OPT_IN_REQUIRED');
    validate(plan);
    if(!target?.replaceChildren || !global.McelSemanticCollectionWorkspace?.mount ||
       !global.McelSemanticLayoutInference?.mountCanonicalLayoutTree)
      throw Error('MCEL_UNIVERSAL_LAB_EXISTING_RENDERER_UNAVAILABLE');
    const doc=target.ownerDocument;
    const el=(tag,cls)=>{const x=doc.createElement(tag);if(cls)x.className=cls;return x;};
    const host=el('section','mcel-universal-composition mcel-universal-composition--human-first');host.id='mcel-universal-workspace';
    if(opts.presentationNegotiation===true)host.classList.add('mcel-universal-composition--negotiated');
    host.dataset.profile=PROFILE;host.dataset.semanticSignature=plan.decision.semanticSignature;
    host.style.cssText='display:grid;gap:8px;max-width:100%;min-width:0;box-sizing:border-box;';
    const regions=new Map();
    for(const region of plan.regions){
      const node=el('section','mcel-universal-composition__region');node.id=region.id;
      node.dataset.recipe=region.recipe;node.style.cssText='min-width:0;max-width:100%;box-sizing:border-box;';
      const heading=el('h3');heading.textContent=region.title;node.append(heading);
      host.append(node);regions.set(region.role,node);
    }
    target.replaceChildren(host);
    let collection=null,tree=null,destroyed=false,active=plan;
    const summary=regions.get('observed-status');
    const info=summary&&el('p');if(info)summary.append(info);
    function updateSummary(next){
      if(info){
        const parts=[String(next.capabilities.entityCount)+' observed records · scope: '+
          String(next.capabilities.observationCompleteness)];
        for(const fact of next.summaryFacts||[]){
          const values=fact.counts||fact.states||{};
          parts.push(fact.property.replaceAll('_',' ')+': '+Object.entries(values).map(([key,n])=>key+' '+n).join(', '));
        }
        info.textContent=parts.join(' · ');
      }
    }
    try{
      updateSummary(plan);
      collection=global.McelSemanticCollectionWorkspace.mount(regions.get('comparable-entities'),
        plan.collection.rows,plan.collection.spec,{observeResize:false,layoutContinuity:'lab',humanFirst:true,
          presentationNegotiation:opts.presentationNegotiation===true,
          presentationPolicy:opts.presentationPolicy,capabilities:opts.capabilities,
          sourceRetirement:opts.sourceRetirement===true,retirementPolicy:opts.retirementPolicy,
          retirementPolicies:opts.retirementPolicies});
      // Move the existing generic inspector exactly once *before interaction*;
      // it remains the same node, owned and updated by the original consumer.
      const inspector=regions.get('selected-details');
      if(inspector && opts.presentationNegotiation!==true){
        const detail=regions.get('comparable-entities').querySelector('.mcel-semantic-collection__inspector');
        if(detail)inspector.append(detail);
      }
      if(inspector && opts.presentationNegotiation===true){inspector.dataset.detailOwnership='inline-or-floating';
        inspector.append(el('p'));inspector.lastChild.textContent='Selection expands inside the list; hover opens a floating card.';}
      tree=global.McelSemanticLayoutInference.mountCanonicalLayoutTree(host,plan.tree,
        {continuity:'lab',observeResize:false,observeMutations:false});
      if(tree.status!=='pass')throw Error('MCEL_UNIVERSAL_LAB_TREE_REFUSED: '+tree.reason);
    }catch(err){
      collection?.destroy?.();tree?.unmount?.();target.replaceChildren();throw err;
    }
    return Object.freeze({
      get profile(){return PROFILE;},get decision(){return active.decision;},
      get collection(){return collection;},get tree(){return tree;},
      get state(){return tree?.getCommittedState?.()||null;},
      update(next,{observation}={}){
        if(destroyed)return {ok:false,reason:'destroyed'};
        try{validate(next);}catch(err){return {ok:false,reason:String(err)}}
        // In-place updates only for identical semantic structure/purpose;
        // a structural change requires explicit remount, never silent redesign.
        if(next.decision.semanticSignature!==active.decision.semanticSignature ||
           next.decision.name!==active.decision.name)
          return {ok:false,reason:'structural-change-requires-explicit-remount'};
        const response=collection.update(next.collection.rows,{observation});
        if(!response.ok)return response;
        active=next;updateSummary(next);
        const reflow=tree.refresh();
        return {ok:reflow.status==='pass',collection:response,tree:reflow,
                effects:[],decisionUnchanged:true};
      },
      destroy(){if(destroyed)return;destroyed=true;tree?.unmount?.();collection?.destroy?.();target.replaceChildren();}
    });
  }
  global.McelUniversalSemanticLayoutLab=Object.freeze({SCHEMA,PROFILE,validate,mount});
})(typeof window!=='undefined'?window:globalThis);
