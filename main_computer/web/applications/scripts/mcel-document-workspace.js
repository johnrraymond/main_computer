/* Application composition only. Document content and editing remain element-owned. */
(function(global){"use strict";
function mount(root,app){
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
 const input=companion.querySelector('input');if(input)input.placeholder='Ask about the selected passage…';
}
global.McelDocumentWorkspace=Object.freeze({mount});})(window);
