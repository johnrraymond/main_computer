/* Semantic document workbench. The canonical paired HTML chooses what each
 * region means and which side it prefers. This reusable presenter arranges
 * EXISTING, bound operational nodes; it defines no intents or capabilities.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.document-workbench.v1';
  const STORAGE='mcel.semantic-document-workbench.v1:';
  const DEFAULT=Object.freeze({explorerWidth:238,companionWidth:310,bottomHeight:196,
    explorerOpen:true,companionOpen:true,bottomOpen:true,companionTab:0,bottomTab:0,narrowActive:"explorer"});
  const RANGES=Object.freeze({explorerWidth:[160,480],companionWidth:[210,560],bottomHeight:[100,440]});
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
  function readPreferences(win,key){
    let saved;
    try {saved=JSON.parse(win.localStorage.getItem(key)||'null');} catch(_){return {...DEFAULT};}
    if(!saved||saved.schema!==SCHEMA||typeof saved.preferences!=='object'||!saved.preferences) return {...DEFAULT};
    const result={...DEFAULT};
    for(const [key,[low,high]] of Object.entries(RANGES)){
      const value=saved.preferences[key];
      if(Number.isFinite(value))result[key]=clamp(Math.round(value),low,high);
    }
    for(const key of ['explorerOpen','companionOpen','bottomOpen'])
      if(typeof saved.preferences[key]==='boolean')result[key]=saved.preferences[key];
    if(['explorer','companion'].includes(saved.preferences.narrowActive))
      result.narrowActive=saved.preferences.narrowActive;
    for(const key of ['companionTab','bottomTab'])
      if(Number.isSafeInteger(saved.preferences[key])&&saved.preferences[key]>=0&&saved.preferences[key]<32)
        result[key]=saved.preferences[key];
    return result;
  }
  function mount(tree,plan,regions){
    if(!tree||!plan||!(regions instanceof Map)||plan.documentWorkbench?.schema!==SCHEMA)
      throw Error('document-workbench-contract-invalid');
    const d=plan.documentWorkbench,doc=tree.ownerDocument,win=doc.defaultView;
    const ids=[d.document,...d.navigation,...d.companion,...d.bottom,...d.status];
    if(new Set(ids).size!==ids.length||ids.some(id=>!regions.has(id)))
      throw Error('document-workbench-region-coverage');
    // Workspace zones own every descendant declared below them. A nested
    // semantic group never becomes a separate dock or a DOM-order heuristic.
    const listed=new Set(ids);
    for(const [id,node] of regions){
      if(id===plan.rootId||listed.has(id))continue;
      let parent=node.parentElement,owner=null;
      while(parent&&parent!==tree){
        const regionId=parent.getAttribute('data-mcel-region');
        if(listed.has(regionId)){owner=regionId;break;}
        parent=parent.parentElement;
      }
      if(!owner)throw Error('document-workbench-nested-group-unowned:'+id);
    }
    if(d.navigation.length!==1||d.status.length!==1)
      throw Error('document-workbench-single-explorer-and-status-required');
    const key=STORAGE+plan.rootId.replace(/^interface-region:/,'').split('.')[0];
    const prefs=readPreferences(win,key);
    let disposed=false,rung='',resizeObserver=null,windowResize=null,raf=0,contractFrame=0;
    const make=(tag,attrs={},text)=>{
      const el=doc.createElement(tag);
      for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));
      if(text!==undefined)el.textContent=String(text);
      return el;
    };
    const titleOf=id=>regions.get(id).querySelector('h2,h3,h4')?.textContent?.trim()||id.split('.').pop().replace(/[-_]/g,' ');
    const surface=make('div',{'data-mcel-workspace-layout':'','data-mcel-document-workbench':'',
      'data-mcel-layout-rung':'wide','aria-label':'Document authoring workspace'});
    const titlebar=make('header',{'data-mcel-zone':'navigator','data-mcel-document-titlebar':''});
    const toolbar=make('div',{'data-mcel-document-toolbar':'','role':'toolbar','aria-label':'Workspace layout'});
    titlebar.append(toolbar);
    const rail=make('nav',{'data-mcel-document-activity-rail':'','aria-label':'Workspace destinations'});
    const explorer=make('aside',{'data-mcel-zone':'explorer','data-mcel-document-explorer':'','aria-label':'File explorer'});
    for(const id of d.navigation)explorer.append(regions.get(id));
    const editor=make('section',{'data-mcel-document-center':'','aria-label':'Editor and bottom dock'});
    const primary=make('main',{'data-mcel-zone':'workspace','data-mcel-document-primary':'','aria-label':'Source editor'});
    primary.append(regions.get(d.document));
    const bottom=make('section',{'data-mcel-zone':'bottom','data-mcel-document-bottom':'','aria-label':'Output and operations'});
    const companion=make('aside',{'data-mcel-zone':'tools','data-mcel-document-companion':'','aria-label':'Assistant and inspection'});
    const status=make('footer',{'data-mcel-zone':'status','data-mcel-document-status':'','aria-label':'Operation status'});
    for(const id of d.status)status.append(regions.get(id));
    const idsuffix=String(plan.fingerprint).slice(-10);
    const tabGroups={};
    function makeTabs(host,members,kind){
      const bar=make('div',{'data-mcel-document-tablist':kind,role:'tablist', 'aria-label':kind==='companion'?'Assistant and inspection':'Bottom workspace'});
      const panels=[];const buttons=[];
      members.forEach((id,i)=>{
        const tabId='mcel-dwb-'+idsuffix+'-'+kind+'-tab-'+i;
        const panelId='mcel-dwb-'+idsuffix+'-'+kind+'-panel-'+i;
        const button=make('button',{type:'button',role:'tab',id:tabId,'aria-controls':panelId,'aria-selected':'false',tabindex:'-1'},titleOf(id));
        const panel=make('div',{'data-mcel-document-tabpanel':kind,role:'tabpanel',id:panelId,'aria-labelledby':tabId});
        panel.append(regions.get(id));
        bar.append(button);buttons.push(button);panels.push(panel);
      });
      host.append(bar,...panels);
      const group={bar,buttons,panels,members};tabGroups[kind]=group;
      buttons.forEach((button,i)=>{
        button.addEventListener('click',()=>{prefs[kind+'Tab']=i;prefs[kind+'Open']=true;if(kind==='companion')prefs.narrowActive='companion';apply(true);});
        button.addEventListener('keydown',event=>{
          const step=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;
          const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:step?(i+step+buttons.length)%buttons.length:null;
          if(next!==null){event.preventDefault();prefs[kind+'Tab']=next;prefs[kind+'Open']=true;if(kind==='companion')prefs.narrowActive='companion';apply(true);buttons[next].focus();}
        });
      });
      return group;
    }
    makeTabs(companion,d.companion,'companion');
    makeTabs(bottom,d.bottom,'bottom');
    const splitter=(kind,orientation,label)=>make('div',{'data-mcel-document-splitter':kind,
      role:'separator','aria-label':label,'aria-orientation':orientation,tabindex:'0',
      'aria-valuemin':String(RANGES[kind+'Width']?.[0]||RANGES.bottomHeight[0]),
      'aria-valuemax':String(RANGES[kind+'Width']?.[1]||RANGES.bottomHeight[1])});
    const leftDivider=splitter('explorer','vertical','Resize Explorer');
    const rightDivider=splitter('companion','vertical','Resize secondary sidebar');
    const bottomDivider=splitter('bottom','horizontal','Resize bottom panel');
    editor.append(primary,bottomDivider,bottom);
    surface.append(titlebar,rail,explorer,leftDivider,editor,rightDivider,companion,status);
    const activityButtons=[];
    function activity(kind,index,label,symbol){
      const button=make('button',{type:'button','data-mcel-document-activity':kind,
        'aria-label':label,title:label,'aria-pressed':'false'},symbol);
      button.setAttribute('aria-controls',kind==='explorer'?'mcel-dwb-'+idsuffix+'-explorer':
        tabGroups[kind].panels[index].id);
      activityButtons.push({button,kind,index});rail.append(button);
      button.addEventListener('click',()=>{
        const visible=surface.getAttribute('data-mcel-'+kind+'-open')==='true';
        if(kind==='explorer'){
          prefs.explorerOpen=!visible;
          if(prefs.explorerOpen)prefs.narrowActive='explorer';
        } else if(visible&&prefs[kind+'Tab']===index)prefs[kind+'Open']=false;
        else{
          prefs[kind+'Open']=true;prefs[kind+'Tab']=index;
          if(kind==='companion')prefs.narrowActive='companion';
        }
        apply(true);
      });
      return button;
    }
    explorer.id='mcel-dwb-'+idsuffix+'-explorer';
    activity('explorer',0,'Explorer','E');
    d.companion.forEach((id,i)=>activity('companion',i,titleOf(id),i===0?'AI':'D'));
    d.bottom.forEach((id,i)=>activity('bottom',i,titleOf(id),i===0?'R':'S'));
    function tool(kind,text){
      const button=make('button',{type:'button','data-mcel-document-toggle':kind,'aria-label':text,title:text},text);
      toolbar.append(button);return button;
    }
    const explorerToggle=tool('explorer','Explorer');
    const bottomToggle=tool('bottom','Bottom panel');
    const companionToggle=tool('companion','Secondary sidebar');
    const reset=tool('reset','Reset layout');
    for(const [kind,button] of [['explorer',explorerToggle],['bottom',bottomToggle],['companion',companionToggle]])
      button.addEventListener('click',()=>{
        const visible=surface.getAttribute('data-mcel-'+kind+'-open')==='true';
        prefs[kind+'Open']=!visible;
        if(prefs[kind+'Open']&&(kind==='explorer'||kind==='companion'))prefs.narrowActive=kind;
        apply(true);
      });
    reset.addEventListener('click',()=>{Object.assign(prefs,DEFAULT);apply(true);});
    function persist(){
      try{win.localStorage.setItem(key,JSON.stringify({schema:SCHEMA,preferences:prefs}));}catch(_){}
    }
    function announceLayout(){
      if(raf||disposed)return;
      raf=win.requestAnimationFrame(()=>{raf=0;if(!disposed)win.dispatchEvent(new win.Event('resize'));});
    }
    function inspectSurface(){
      // Pure geometry/interaction observation: no capabilities, no mutation
      // arguments and no Chrome material decisions are made here.
      if(!surface.isConnected)return {status:'not-mounted',violations:['surface-not-mounted']};
      const outer=surface.getBoundingClientRect(),violations=[];
      const bounded=(name,element,required)=>{
        if(!required)return;
        const rect=element.getBoundingClientRect();
        if(rect.width<24||rect.height<24)violations.push(name+':zero-or-unusable-geometry');
        if(rect.left<outer.left-1||rect.right>outer.right+1||
            rect.top<outer.top-1||rect.bottom>outer.bottom+1)
          violations.push(name+':out-of-bounds');
      };
      bounded('editor',primary,true);
      bounded('explorer',explorer,surface.dataset.mcelExplorerOpen==='true');
      bounded('companion',companion,surface.dataset.mcelCompanionOpen==='true');
      bounded('bottom',bottom,surface.dataset.mcelBottomOpen==='true');
      for(const [kind,group] of Object.entries(tabGroups)){
        if(surface.getAttribute('data-mcel-'+kind+'-open')!=='true')continue;
        const panel=group.panels[prefs[kind+'Tab']];
        const overflow=win.getComputedStyle(panel).overflowY;
        if(!['auto','scroll'].includes(overflow))violations.push(kind+':scroll-ownership');
      }
      if(doc.documentElement.scrollWidth>win.innerWidth+1)
        violations.push('document:horizontal-overflow');
      return {status:violations.length?'failed':'pass',violations};
    }
    function recordContract(){
      contractFrame=0;
      if(disposed)return;
      const inspection=inspectSurface();
      surface.setAttribute('data-mcel-surface-contract',inspection.status);
      if(inspection.violations.length)
        surface.setAttribute('data-mcel-surface-violations',inspection.violations.join(' '));
      else surface.removeAttribute('data-mcel-surface-violations');
    }
    function apply(save=false){
      if(disposed)return;
      prefs.companionTab=clamp(prefs.companionTab,0,d.companion.length-1);
      prefs.bottomTab=clamp(prefs.bottomTab,0,d.bottom.length-1);
      const available=surface.getBoundingClientRect().width||tree.getBoundingClientRect().width||win.innerWidth||1200;
      const availableHeight=surface.getBoundingClientRect().height||tree.getBoundingClientRect().height||win.innerHeight||720;
      const next=available>=1180?'wide':available>=780?'medium':'narrow';
      rung=next;surface.setAttribute('data-mcel-layout-rung',next);
      const maxExplorer=Math.max(160,Math.min(RANGES.explorerWidth[1],available-420));
      const maxCompanion=Math.max(210,Math.min(RANGES.companionWidth[1],available-610));
      surface.style.setProperty('--mcel-document-explorer-width',clamp(prefs.explorerWidth,160,maxExplorer)+'px');
      surface.style.setProperty('--mcel-document-companion-width',clamp(prefs.companionWidth,210,maxCompanion)+'px');
      surface.style.setProperty('--mcel-document-bottom-height',clamp(prefs.bottomHeight,100,Math.max(100,availableHeight-250))+'px');
      const visible={explorer:prefs.explorerOpen,companion:prefs.companionOpen,bottom:prefs.bottomOpen};
      // At narrow widths only one overlay can cover the editor at a time.
      // This is an interaction constraint, not an application-specific choice.
      // Preserve the user's wider-workspace preferences across rung changes.
      if(next==='narrow'&&visible.explorer&&visible.companion){
        visible[prefs.narrowActive==='companion'?'explorer':'companion']=false;
      }
      for(const kind of ['explorer','companion','bottom']){
        surface.setAttribute('data-mcel-'+kind+'-open',String(visible[kind]));
      }
      for(const [kind,group] of Object.entries(tabGroups)){
        group.buttons.forEach((button,i)=>{
          const active=i===prefs[kind+'Tab'];
          button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
          group.panels[i].hidden=!active;
        });
      }
      for(const {button,kind,index} of activityButtons){
        button.setAttribute('aria-pressed',String(visible[kind]&&(kind==='explorer'||prefs[kind+'Tab']===index)));
      }
      for(const [kind,button] of [['explorer',explorerToggle],['bottom',bottomToggle],['companion',companionToggle]])
        button.setAttribute('aria-pressed',String(visible[kind]));
      for(const [kind,handle] of [['explorer',leftDivider],['companion',rightDivider],['bottom',bottomDivider]]){
        const prop=kind==='bottom'?'bottomHeight':kind+'Width';
        const [min,max]=RANGES[prop];
        handle.setAttribute('aria-valuemin',String(min));handle.setAttribute('aria-valuemax',String(max));
        handle.setAttribute('aria-valuenow',String(prefs[prop]));
        handle.setAttribute('aria-valuetext',String(prefs[prop])+' pixels');
        handle.setAttribute('aria-disabled',String(!visible[kind]||kind!=='bottom'&&rung!=='wide'));
      }
      if(contractFrame)win.cancelAnimationFrame?.(contractFrame);
      contractFrame=win.requestAnimationFrame(recordContract);
      if(save){persist();announceLayout();}
    }
    function resize(kind,delta,save){
      const prop=kind==='bottom'?'bottomHeight':kind+'Width';
      const [low,high]=RANGES[prop];
      prefs[prop]=clamp(prefs[prop]+Math.round(delta),low,high);
      apply(save);
    }
    for(const [kind,handle] of [['explorer',leftDivider],['companion',rightDivider],['bottom',bottomDivider]]){
      handle.addEventListener('keydown',event=>{
        const hor=kind!=='bottom';
        if(event.key==='Home'||event.key==='End'){
          event.preventDefault();const [low,high]=RANGES[kind==='bottom'?'bottomHeight':kind+'Width'];
          prefs[kind==='bottom'?'bottomHeight':kind+'Width']=event.key==='Home'?low:high;apply(true);return;
        }
        const step=event.shiftKey?40:16;
        const inc=hor?(event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0):
          (event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0);
        if(inc){event.preventDefault();resize(kind,inc*step*(kind==='companion'?-1:kind==='bottom'?-1:1),true);}
      });
      handle.addEventListener('pointerdown',event=>{
        if(event.button!==0||!prefs[kind+'Open']||(kind!=='bottom'&&rung!=='wide'))return;
        event.preventDefault();const start=kind==='bottom'?event.clientY:event.clientX;
        const prop=kind==='bottom'?'bottomHeight':kind+'Width',initial=prefs[prop];
        const sign=kind==='explorer'?1:-1;
        handle.setPointerCapture?.(event.pointerId);
        const move=e=>{
          const pos=kind==='bottom'?e.clientY:e.clientX;
          const [low,high]=RANGES[prop];prefs[prop]=clamp(initial+(pos-start)*sign,low,high);
          apply(false);announceLayout();
        };
        const end=e=>{
          handle.removeEventListener('pointermove',move);
          handle.removeEventListener('pointerup',end);
          handle.removeEventListener('pointercancel',end);
          if(handle.hasPointerCapture?.(e.pointerId))handle.releasePointerCapture(e.pointerId);
          persist();
        };
        handle.addEventListener('pointermove',move);
        handle.addEventListener('pointerup',end);
        handle.addEventListener('pointercancel',end);
      });
    }
    tree.setAttribute('data-mcel-planned-root','');
    tree.append(surface);
    if(typeof win.ResizeObserver==='function'){
      resizeObserver=new win.ResizeObserver(()=>apply());
      // Watch the surface contract's physical participants, not merely the
      // outer tree. A CSS or dock change can collapse a child track without
      // resizing its containing root.
      for(const element of [tree,primary,explorer,companion,bottom])
        resizeObserver.observe(element);
    } else {
      windowResize=()=>apply();win.addEventListener('resize',windowResize);
    }
    apply();
    return Object.freeze({refresh:()=>apply(),validate:inspectSurface,getRung:()=>rung,preferences:()=>({...prefs}),unmount:()=>{
      disposed=true;if(raf)win.cancelAnimationFrame?.(raf);if(contractFrame)win.cancelAnimationFrame?.(contractFrame);
      resizeObserver?.disconnect();if(windowResize)win.removeEventListener('resize',windowResize);
    }});
  }
  global.McelSemanticDocumentWorkbench=Object.freeze({SCHEMA,mount});
})(typeof window!=='undefined'?window:globalThis);
