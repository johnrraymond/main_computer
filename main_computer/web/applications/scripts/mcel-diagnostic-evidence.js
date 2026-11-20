/* Shared observational MCEL evidence. Does not mutate layouts, start audits, or
 * inspect message/input contents. Exposes one read-only snapshot to E/W/G and
 * Twiddle, including what was *not* verified by the canonical contract. */
(function (global) {
  "use strict";
  if (!global || global.MCELDiagnosticEvidence) return;
  const entries = new Map(), histories = new Map(), operationalReaders = new Map();
  const LIMIT = 60, HISTORY = 100;
  const now = () => new Date().toISOString();
  const rounded = n => Math.round(n * 10) / 10;
  const box = element => {
    const b = element.getBoundingClientRect();
    return {x:rounded(b.x),y:rounded(b.y),width:rounded(b.width),height:rounded(b.height),right:rounded(b.right),bottom:rounded(b.bottom)};
  };
  const visible = element => {
    if (!element || element.closest('[hidden], [inert]')) return false;
    const s=global.getComputedStyle(element), r=element.getBoundingClientRect();
    return r.width>0 && r.height>0 && s.display!=="none" && s.visibility!=="hidden";
  };
  const identity = element => {
    const source = element?.closest('[data-mcel-node], [data-mcel-region], [data-mcel-chat-role], [data-mcel-region-id]');
    return {region:element?.closest('[data-mcel-region], [data-mcel-region-id]')?.getAttribute('data-mcel-region') ||
      element?.closest('[data-mcel-region-id]')?.getAttribute('data-mcel-region-id') || null,
      node:element?.closest('[data-mcel-node]')?.getAttribute('data-mcel-node') || null,
      role:source?.getAttribute('data-mcel-chat-role') || null,
      tag:element?.tagName?.toLowerCase() || ''};
  };
  const finding = (code, element, evidence, severity='warning') => ({
    code, severity, identity:identity(element), geometry:box(element), evidence,
    time:now(), source:'mcel-diagnostic-evidence-v1'
  });
  const safeLabel = text => {
    const label=String(text||'').replace(/\s+/g,' ').trim().slice(0,64);
    return /[@\r\n<>]/.test(label) ? '[redacted]' : label;
  };
  function brokenWords(element) {
    const doc=element.ownerDocument, walk=doc.createTreeWalker(element,NodeFilter.SHOW_TEXT), range=doc.createRange(), items=[];
    let node;
    while ((node=walk.nextNode()) && items.length<6) {
      // Do not inspect text from user inputs, editable outputs, or transcripts.
      if (node.parentElement?.closest('textarea,[contenteditable],.chat-console-cell-output,[data-chat-conversation-role="user"]')) continue;
      for (const match of node.textContent.matchAll(/[A-Za-z]{3,}/g)) {
        const tops=[], parts=[];
        for(let i=0;i<match[0].length;i++) {
          range.setStart(node,match.index+i);range.setEnd(node,match.index+i+1);
          const r=range.getBoundingClientRect();if(!(r.width>0&&r.height>0))continue;
          let j=tops.findIndex(y=>Math.abs(y-r.top)<=2);
          if(j===-1){j=tops.length;tops.push(r.top);parts.push('');}parts[j]+=match[0][i];
        }
        if(parts.length>1)items.push({token:safeLabel(match[0]),lines:parts.map(safeLabel)});
        if(items.length>=6)break;
      }
    }
    return items;
  }
  // Read-only visual-integrity check. A heading can occupy the excluded arc
  // of a rounded, painted region even when its ordinary DOM rectangles do not
  // extend beyond the region's rectangular bounding box. That is a concrete
  // collision, not merely a stylistic spacing preference. Inspect rendered
  // text geometry rather than heading wording, region IDs, or app names.
  function headingRegionOverrun(heading) {
    const region=heading.parentElement;
    if (!region?.matches('[data-mcel-region]')) return null;
    const range=heading.ownerDocument.createRange();
    range.selectNodeContents(heading);
    const text=range.getBoundingClientRect(), bounds=region.getBoundingClientRect();
    if (!(text.width>0 && text.height>0 && bounds.width>0 && bounds.height>0))return null;
    const tolerance=1;
    const outside={left:Math.max(0,bounds.left-text.left),right:Math.max(0,text.right-bounds.right),
      top:Math.max(0,bounds.top-text.top),bottom:Math.max(0,text.bottom-bounds.bottom)};
    const collisions=[];
    for(const [side,depth] of Object.entries(outside))if(depth>tolerance)
      collisions.push({kind:'region-bounds',side,penetrationPx:rounded(depth)});
    const style=global.getComputedStyle(region);
    const painted=style.backgroundImage!=='none' || (style.backgroundColor!=='transparent' &&
      !/^rgba?\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)$/.test(style.backgroundColor)) ||
      ['Top','Right','Bottom','Left'].some(side=>parseFloat(style['border'+side+'Width'])>0);
    // Rounded corners only constrain the heading when the region actually
    // paints a rounded panel. No layout or computed style is changed here.
    if(painted) {
      const corners=[['top-left','borderTopLeftRadius',true,true],
        ['top-right','borderTopRightRadius',true,false],
        ['bottom-left','borderBottomLeftRadius',false,true],
        ['bottom-right','borderBottomRightRadius',false,false]];
      for(const [corner,property,top,left] of corners) {
        const raw=style[property];
        if(!/^\d+(?:\.\d+)?px(?:\s|$)/.test(raw))continue;
        const radius=Math.min(parseFloat(raw),bounds.width/2,bounds.height/2);
        if(radius<=tolerance)continue;
        // A sample a few pixels inside the line box avoids false positives
        // from line-height leading while still testing the rendered first row.
        const sample=top ? Math.max(bounds.top,text.top)+Math.min(3,text.height/4)
          : Math.min(bounds.bottom,text.bottom)-Math.min(3,text.height/4);
        const offset=top ? sample-bounds.top : bounds.bottom-sample;
        if(offset<0 || offset>=radius)continue;
        const required=radius-Math.sqrt(Math.max(0,radius*radius-(radius-offset)**2));
        const actual=left ? text.left-bounds.left : bounds.right-text.right;
        if(required-actual>tolerance)collisions.push({kind:'rounded-corner',corner,
          penetrationPx:rounded(required-actual),radiusPx:rounded(radius)});
      }
    }
    if(!collisions.length)return null;
    return {collisions,textBounds:box({getBoundingClientRect:()=>text}),regionBounds:box(region),
      note:'Heading text intersects a region boundary or the excluded corner of its rounded panel.'};
  }
  function matchedDisplayRules(element) {
    const rules=[];
    // Include the final inline declaration and directly matching CSS rules.
    const inline=element.style;
    if(inline.getPropertyValue('display'))rules.push({origin:'inline',property:'display',value:inline.getPropertyValue('display'),important:inline.getPropertyPriority('display')==='important'});
    for (const sheet of Array.from(element.ownerDocument.styleSheets).slice(0,32)) {
      let css;
      try{css=sheet.cssRules;}catch{continue;}
      const visit=(ruleset,depth=0)=>{
        if(depth>2||rules.length>=18)return;
        for(const rule of Array.from(ruleset).slice(0,5000)) {
          if(rules.length>=18)break;
          if(rule.cssRules && rule.cssRules.length){visit(rule.cssRules,depth+1);continue;}
          const value=rule.style?.getPropertyValue('display');
          if(!value || !rule.selectorText)continue;
          try{if(element.matches(rule.selectorText))rules.push({origin:'stylesheet',selector:rule.selectorText.slice(0,160),property:'display',value:value.slice(0,45),important:rule.style.getPropertyPriority('display')==='important'});}catch{}
        }
      };
      visit(css);
    }
    return rules;
  }
  function declaredCoverage(ir) {
    const tree=ir?.layouts?.find(x=>x?.layoutTree)?.layoutTree;
    if(!tree)return {status:'no-layout-tree',verifiedCount:0,excluded:[]};
    const excluded=[];let verified=0, unspecified=0;
    function walk(n) {
      if(n.verifyControls===false)excluded.push({layoutNode:n.id||null,reason:'verifyControls:false',selector:n.selector||null});
      else if(n.verifyControls===true)verified++;else unspecified++;
      for(const c of n.children||[])walk(c);
    }
    walk(tree.root||{});
    return {status:'declared',verifiedCount:verified,unspecifiedCount:unspecified,excluded};
  }
  function pluginSnapshot() {
    const registry=global.MCELDiagnosticPlugins;
    if(!registry)return {status:'unavailable',reason:'plugin-registry-not-loaded',inventory:[],findings:[]};
    try {
      const inventory=registry.list().map(x=>({id:x.id,kind:x.kind,enabled:!!x.enabled,
        availability:typeof x.availability==='string'?x.availability:'unknown'}));
      const items=registry.findings().slice(-50).map(x=>({pluginId:x.pluginId,code:x.code,severity:x.severity,
        timestamp:x.timestamp,status:x.status||null, detailRedacted:true}));
      const status=registry.status?.()||{};
      return {status:'observed',inventory,findings:items,
        registryStatus:{armedAt:status.armedAt||null,earlyEvents:Number(status.earlyEvents||0),
          states:Object.fromEntries(Object.entries(status.states||{}).map(([id,value])=>[id,value?.status||'unknown']))},
        notExecuted:inventory.filter(x=>!x.enabled).map(x=>x.id)};
    } catch(e) {return {status:'collection-failed',reason:String(e?.name||'unknown'),inventory:[],findings:[]};}
  }
  function snapshot(appId) {
    const entry=entries.get(appId);
    if(!entry)return null;
    const root=entry.root, time=now(), results=[];
    if(!root?.isConnected)return {schema:'mcel-diagnostic-evidence-v1',appId,time,status:'unmounted'};
    // Include generated region headings (not only buttons/labels). This
    // catches the SLH Operation Evidence title at the rounded upper-left
    // corner and applies to every semantic application using this collector.
    for(const heading of [...root.querySelectorAll('[data-mcel-region] > :is(h1,h2,h3,h4,h5,h6)')].filter(visible).slice(0,150)) {
      if(results.length>=LIMIT)break;
      const collision=headingRegionOverrun(heading);
      if(collision)results.push(finding('heading-text-region-overrun',heading,collision,'error'));
    }
    const candidates=[...root.querySelectorAll('label,button,[role="button"],summary,.chat-rag-at-hint')]
      .filter(el=>visible(el)&&!el.closest('.mcel-diagnostics-counter')).slice(0,250);
    const verified=[];
    for(const el of candidates) {
      if(results.length>=LIMIT)break;
      const lines=brokenWords(el);
      if(lines.length)results.push(finding('atomic-text-split',el,{brokenWords:lines,
        textFit:{wordBreak:global.getComputedStyle(el).wordBreak,overflowWrap:global.getComputedStyle(el).overflowWrap}},'warning'));
      const r=box(el);
      if(r.width<20 && el.matches('button,[role="button"]'))results.push(finding('control-too-narrow',el,{actualWidth:r.width,minimumObservedPx:20},'warning'));
      verified.push(identity(el));
    }
    const layouts=[];
    for (const el of [...root.querySelectorAll('[data-mcel-chat-role="workspace"],[data-mcel-chat-role="notebook"],[data-mcel-natural-scroller],[data-mcel-canonical-layout-authority]')].filter(visible).slice(0,20)) {
      const st=global.getComputedStyle(el),rules=matchedDisplayRules(el);
      const disagreement=rules.some(r=>r.origin==='stylesheet'&&r.value!==st.display);
      const evidence={identity:identity(el),geometry:box(el),computed:{display:st.display,flexDirection:st.flexDirection,
        gridTemplateRows:st.gridTemplateRows,alignContent:st.alignContent},rules,
        authority:el.getAttribute('data-mcel-canonical-layout-authority')||el.closest('[data-mcel-canonical-layout-authority]')?.getAttribute('data-mcel-canonical-layout-authority')||null};
      layouts.push(evidence);
      if(disagreement && rules.some(r=>r.origin==='inline'&&r.important))
        results.push(finding('layout-authority-override',el,{computedDisplay:st.display,competingRules:rules,
          note:'A canonical inline important declaration overrides a matched stylesheet display; inspect intended layout by mode.'},'warning'));
      // Generic geometric whitespace evidence, not a universal invalidity rule.
      const children=Array.from(el.children).filter(visible).sort((a,b)=>box(a).y-box(b).y);
      for(let i=1;i<children.length;i++){
        const gap=box(children[i]).y-box(children[i-1]).bottom;
        if(gap>120 && gap>box(el).height*0.15 && results.length<LIMIT)
          results.push(finding('unallocated-vertical-space',el,{gapPx:rounded(gap),thresholdPx:120,
            previous:identity(children[i-1]),next:identity(children[i]),
            note:'Heuristic: verify whether this spacing is intentional before classifying as an error.'},'warning'));
      }
    }
    const chat=root.querySelector('[data-mcel-chat-layout-status]');
    const nested=chat ? {status:chat.getAttribute('data-mcel-chat-layout-status'),
      authority:chat.querySelector('[data-mcel-canonical-layout-authority]')?.getAttribute('data-mcel-canonical-layout-authority')||null,
      projection:chat.getAttribute('data-chat-conversation-notebook')==='1'?'notebook':'conversation'} : {status:'not-present'};
    const ir=entry.ir;
    const coverage={status:'measured',visibleControls:candidates.length,checkedControls:verified.length,
      rule:'visible controls and captions, including labels',limits:{controls:250,findings:LIMIT},
      truncated:candidates.length>=250||results.length>=LIMIT,
      semanticRegions:root.querySelectorAll('[data-mcel-region],[data-mcel-region-id]').length,
      nestedCanonical:declaredCoverage(entry.childIr),
      selfDiagnosis:(()=>{const names=global.MCEL?.selfDiagnosis?.listContracts?.() || [];return {status:names.some(c=>c.appId===appId)?'contract-covered':'not-covered',reason:'semantic-app-not-in-legacy-contract-registry',registeredContracts:names.length};})()};
    // Operational sources are registered by semantic hosts, not inferred from
    // their rendered DOM. This supplements the shared structural collector
    // without invoking a capability, repeating an observation, or reading an
    // input value. The host supplies an explicitly bounded public projection.
    let operational={status:'not-instrumented'};
    const operationalReader=operationalReaders.get(appId);
    if(operationalReader) {
      try {
        const observed=operationalReader();
        operational=observed && typeof observed==='object' && !Array.isArray(observed)
          ? {status:'observed',...observed} : {status:'invalid-observation'};
      } catch(error) {
        operational={status:'collection-failed',errorClass:String(error?.name||'unknown')};
      }
      for(const item of (Array.isArray(operational.findings)?operational.findings:[]).slice(0,12)) {
        if(!/^[a-z0-9-]{1,80}$/.test(String(item?.code||'')))continue;
        if(!['warning','error'].includes(item?.severity))continue;
        if(results.length>=LIMIT)break;
        results.push({code:item.code,severity:item.severity,identity:{region:'operational',node:null,role:null,tag:''},
          evidence:{status:safeLabel(item.status||'observed')},time,source:'mcel-operational-observation'});
      }
    }
    const plugins=pluginSnapshot();
    const current={schema:'mcel-diagnostic-evidence-v1',appId,contractId:ir?.application?.appId||appId,
      route:global.location?.pathname||'',timestamp:time,coverage,nested,findings:results,layoutDecisions:layouts,
      plugins,operational,privacy:{mode:'structural-plus-declared-operational',
        inputValues:operational.capturesConfigurationInputs===true,
        configurationFields:operational.capturesConfigurationInputs===true?['bindAddress','port']:[],
        conversationMessages:false,rawPluginDetails:false}};
    const history=histories.get(appId)||[];
    const found=results.map(r=>[r.code,r.identity.region,r.identity.role,r.geometry?.x??null,r.geometry?.y??null].join('|'));
    if(found.join('~')!==(history[history.length-1]?.signatures||[]).join('~')) {
      history.push({timestamp:time,projection:nested.projection||null,codes:results.map(r=>r.code),signatures:found});
      if(history.length>HISTORY)history.splice(0,history.length-HISTORY);
      histories.set(appId,history);
    }
    current.history=history.slice(-HISTORY);
    return current;
  }
  function register(appId,root,ir,childIr) {
    if(!appId||!root)throw Error('MCEL diagnostic evidence registration needs an app and root');
    entries.set(String(appId),{root,ir,childIr});
    return snapshot(String(appId));
  }
  function registerOperational(appId,reader) {
    if(!appId||typeof reader!=='function')throw Error('MCEL operational diagnostic registration requires a reader');
    operationalReaders.set(String(appId),reader);
    return {appId:String(appId),status:'registered'};
  }
  const api=Object.freeze({VERSION:'mcel-diagnostic-evidence-v1',register,registerOperational,snapshot,
    history:appId=>(histories.get(appId)||[]).slice()});
  global.MCELDiagnosticEvidence=api;
})(typeof window!=='undefined'?window:null);
