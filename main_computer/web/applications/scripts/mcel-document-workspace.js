/* Application composition only. Document content and editing remain element-owned. */
(function(global){"use strict";
function mount(root,app,port){
 const doc=root.ownerDocument,tree=root.querySelector('[data-mcel-natural-layout]'),
  region=name=>root.querySelector('[data-mcel-region="interface-region:document.'+name+'"]'),
  header=region('header'), library=region('library'), companion=region('companion'),
  nav=library.parentElement, tools=companion.parentElement,
  surface=app.getDocumentSurface('surface-node:document.draft');
 if(!tree||!header||!surface)throw Error('DOCUMENT_WORKSPACE_UNAVAILABLE');
 tree.prepend(header);tree.dataset.richDocumentWorkspace='';
 nav.querySelector('[data-mcel-navigation-chrome]')?.remove();
 function button(text,fn){const b=doc.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',fn);return b;}
 function toggle(label,zone,key){const b=button(label,()=>{const hidden=tree.toggleAttribute(key);b.setAttribute('aria-expanded',String(!hidden));zone.inert=hidden;});b.setAttribute('aria-expanded','true');header.append(b);}
 toggle('Library / outline',nav,'data-document-hide-library');toggle('Document AI',tools,'data-document-hide-ai');
 const title=doc.createElement('h3');title.textContent='Document outline';library.append(title);
 const outline=doc.createElement('nav');outline.setAttribute('aria-label','Document outline');outline.dataset.documentOutline='';library.append(outline);
 let last='';function refreshOutline(){const headings=surface.getOutline();const key=JSON.stringify(headings);if(key===last)return;last=key;
  outline.replaceChildren();if(!headings.length){const p=doc.createElement('p');p.textContent='Headings will appear here.';outline.append(p);return;}
  for(const h of headings){const b=button(h.text||'(Untitled heading)',()=>surface.navigateHeading(h.index));b.style.setProperty('--outline-depth',String(h.level-1));outline.append(b);}}
 refreshOutline();root.addEventListener('mcel:document-surface-change',e=>{if(e.detail.nodeId==='surface-node:document.draft'&&e.detail.kind==='content')refreshOutline();});
 const save=root.querySelector('[data-mcel-node="surface-node:document.save-candidate-draft"] button');if(save)save.textContent='Save';
 const input=companion.querySelector('input');if(input)input.placeholder='Ask AI to revise the locked target…';
 const meta=doc.createElement('details');meta.dataset.documentMetadata='';const summary=doc.createElement('summary');summary.textContent='Document properties';meta.append(summary);
 const labels={name:'Name',date:'Document date',type:'Type',author:'Author',description:'Description'};
 function field(parent,key,value,change){const label=doc.createElement('label');label.textContent=labels[key];const f=doc.createElement(key==='description'?'textarea':'input');if(key==='date')f.type='date';f.name=key;f.value=value;f.addEventListener('change',()=>change(key,f.value));label.append(f);parent.append(label);return f;}
 const controls={};for(const [k,v] of Object.entries(port.getMetadata()))controls[k]=field(meta,k,v,(key,value)=>{try{port.editMetadata({[key]:value});}catch(e){message.textContent=String(e.message||e);refresh();}});
 companion.prepend(meta);
 const queue=doc.createElement('pre');queue.dataset.documentCreationStatus='';queue.setAttribute('role','status');companion.append(queue);
 const message=doc.createElement('output');message.dataset.documentLocalStatus='';header.append(message);
 const name=doc.createElement('span');name.dataset.documentName='';header.prepend(name);const opened=header.querySelector('[data-mcel-node="surface-node:document.opened-document"]');if(opened)opened.hidden=true;
 function refresh(){const state=port.getMetadata();for(const [k,v] of Object.entries(state))if(doc.activeElement!==controls[k])controls[k].value=v;name.textContent=state.name||'Untitled Document';queue.textContent=port.getCreationStatus();queue.hidden=!queue.textContent;}
 port.subscribe(refresh);refresh();
 const dialog=doc.createElement('dialog');dialog.dataset.documentNewDialog='';root.append(dialog);
 header.prepend(button('New Document',()=>{
   dialog.replaceChildren();const form=doc.createElement('form');form.method='dialog';const h=doc.createElement('h2');h.textContent='New Document';form.append(h);
   const baseline=port.getDefaults(),values={...baseline},explicit=new Set();
   for(const [k,v] of Object.entries(baseline)){
     const f=field(form,k,v,(key,value)=>{explicit.add(key);values[key]=value;});
     f.addEventListener('input',()=>{explicit.add(k);values[k]=f.value;});
     if(k==='date'){const current=button('Current',()=>{explicit.add(k);values[k]=baseline.date;f.value=baseline.date;});form.append(current);}
   }
   const label=doc.createElement('label');label.textContent='What is this document about? (optional)';const prompt=doc.createElement('textarea');prompt.name='prompt';prompt.placeholder='Instructions, notes, or an outline';label.append(prompt);form.append(label);
   const scratch=doc.createElement('label'),use=doc.createElement('input');use.type='checkbox';use.name='includeCanvas';scratch.append(use,doc.createTextNode(' Include current canvas as source material'));form.append(scratch);
   const error=doc.createElement('output');error.setAttribute('role','status');form.append(error);
   const ok=doc.createElement('button');ok.type='submit';ok.textContent='OK';form.append(button('Cancel',()=>dialog.close()),ok);
   form.addEventListener('submit',async e=>{e.preventDefault();ok.disabled=true;try{
     const context={defaults:baseline,values,explicit:[...explicit],prompt:prompt.value,sourceHtml:use.checked?surface.getState().html:null};
     const receipt=await app.execute('intent:new-document',{context:JSON.stringify(context)});
     if(receipt.disposition!=='completed')throw Error(receipt.reason||'New document failed');dialog.close();message.textContent='New document created';void app.execute('intent:list-documents');
   }catch(e){error.textContent=String(e.message||e);}finally{ok.disabled=false;}});
   dialog.append(form);dialog.showModal();
 }));
 const lockWhole=companion.querySelector('[data-mcel-node="surface-node:document.lock-document"] button');if(lockWhole)lockWhole.textContent='Lock Entire Document';
 const lockSelection=companion.querySelector('[data-mcel-node="surface-node:document.lock-selection"] button');if(lockSelection)lockSelection.textContent='Lock Selection';
 const apply=companion.querySelector('[data-mcel-node="surface-node:document.apply-ai-proposal"] button');if(apply)apply.textContent='Apply AI Proposal';
}
global.McelDocumentWorkspace=Object.freeze({mount});})(window);
