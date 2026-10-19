// MCEL independently generated presentation host.  Deliberately does not load
// existing host-authored document, host CSS, or a host runtime facade.
// Unimplemented domain operations must never become fabricated PASS evidence.
const root = document.querySelector('#mcel-standalone-host');
const content = root?.querySelector('[data-mcel-standalone-content]');
const status = root?.querySelector('[data-mcel-standalone-status]');
const schema = 'mcel.standalone-package-browser.v1';
const identity = (text) => String(text || '').trim();
const cssSelector = /^([#.])([A-Za-z_][\w-]*)$/;
const declaredArea = /^[A-Za-z][A-Za-z0-9-]*$/;
const state = {ready: false, structuralVerified: false, fullSemanticRuntimeProven: false,
  presentationAuthority: 'generated-from-compiled-layout-tree', error: null,
  requested: [], nodes: [], selected: [], disabledIntents: []};
function fail(message) {throw new Error(`MCEL_STANDALONE_REFUSED: ${message}`);}
function make(tag, attributes = {}) {
  const el = document.createElement(tag);
  Object.entries(attributes).forEach(([name, value]) => el.setAttribute(name, String(value)));
  return el;
}
function setSelector(element, selector) {
  const matched = cssSelector.exec(identity(selector));
  if (!matched) fail(`unsafe or unsupported selector ${selector}`);
  if (matched[1] === '#') element.id = matched[2]; else element.classList.add(matched[2]);
}
function minTrackWidth(track) {
  const raw = identity(track);
  if (/^\d+(?:\.\d+)?px$/.test(raw)) return parseFloat(raw);
  if (/^minmax\(0(?:px)?,\s*(?:\d+(?:\.\d+)?fr)\)$/.test(raw)) return 200;
  return Infinity; // Unknown track grammar never silently becomes feasible.
}
function validVariant(node, variant, width) {
  const children = (node.children || []).map(c => c.id);
  if (!Array.isArray(variant.tracks) || !Array.isArray(variant.areas)) return false;
  if (!variant.tracks.length || !variant.areas.length) return false;
  const cells = variant.areas.map(row => String(row).split(/\s+/));
  if (cells.some(row => row.length !== variant.tracks.length)) return false;
  const covered = new Set(cells.flat().filter(x => x !== '.'));
  if ([...covered].some(id => !children.includes(id) || !declaredArea.test(id))) return false;
  if (covered.size !== children.length) return false; // Never quietly hide a declared region.
  return variant.tracks.reduce((sum, t) => sum + minTrackWidth(t), 0)
    + (variant.tracks.length - 1) * 8 <= width + 0.1;
}
function applyGroup(node, element) {
  const children = node.children || [];
  if (!children.length) return;
  element.style.display = 'grid';
  const width = Math.max(1, element.getBoundingClientRect().width - 16);
  const variants = node.variants || [];
  const choice = variants.find(v => validVariant(node, v, width));
  if (choice) {
    element.style.gridTemplateColumns = choice.tracks.join(' ');
    element.style.gridTemplateAreas = choice.areas.map(line => `"${line}"`).join(' ');
    children.forEach(child => {element.querySelector(`[data-mcel-direct-child="${child.id}"]`)?.style.setProperty('grid-area', child.id);});
    state.selected.push({id: node.id, tracks: choice.tracks.length, areas: choice.areas});
  } else {
    element.style.gridTemplateColumns = 'minmax(0,1fr)';
    element.style.gridTemplateAreas = 'none';
    state.selected.push({id: node.id, tracks: 1, areas: [], fallback: true});
  }
}
function createTree(node, depth = 0) {
  if (!node || depth > 24 || !declaredArea.test(identity(node.id))) fail('invalid canonical tree node');
  const element = make('section', {'data-mcel-structural-node': node.id, 'aria-label': node.id});
  setSelector(element, node.selector);
  state.nodes.push(node.id);
  if (!depth) element.dataset.mcelStructuralRoot = 'true';
  const label = make('div', {'data-mcel-structural-title': ''});
  label.textContent = node.id.replace(/-/g, ' ');
  element.append(label);
  for (const child of node.children || []) {
    const childElement = createTree(child, depth+1);
    childElement.dataset.mcelDirectChild = child.id;
    element.append(childElement);
  }
  return element;
}
function applyRecursive(node, element) {
  applyGroup(node, element);
  for (const child of node.children || []) {
    const childElement = [...element.children].find(x => x.dataset.mcelDirectChild === child.id);
    if (!childElement) fail(`missing generated region ${child.id}`);
    applyRecursive(child, childElement);
  }
}
function portMissing(intentName) {
  fail(`portable implementation is missing for ${intentName}`);
}
async function load() {
  if (!root || !content || !status) fail('standalone host DOM unavailable');
  const response = await fetch('./presentation.json', {cache:'no-store'});
  if (!response.ok) fail(`presentation manifest: HTTP ${response.status}`);
  const manifest = await response.json();
  if (manifest.schema !== 'mcel.standalone-package-presentation.v1'
     || manifest.presentationAuthority !== 'generated-from-compiled-layout-tree'
     || manifest.sourceHostAssetsIncluded !== false
     || manifest.behavioralParityProven !== false
     || manifest.fullSemanticRuntimeProven !== false) fail('invalid or overclaimed presentation manifest');
  const [layoutModule, surfaceModule, intentsModule] = await Promise.all([
    import(manifest.modules.layout), import(manifest.modules.surface), import(manifest.modules.intents)]);
  const exportPrefix = manifest.appId.split('-').map(s => s[0].toUpperCase()+s.slice(1)).join('');
  const layout = layoutModule[`${exportPrefix}Layout`];
  const surface = surfaceModule[`${exportPrefix}Surface`];
  const intents = intentsModule[`${exportPrefix}Intents`];
  if (layout?.appId !== manifest.appId || surface?.appId !== manifest.appId ||
      !intents || typeof intents !== 'object') fail('generated contract identity mismatch');
  const names = Object.keys(intents).sort();
  if (JSON.stringify(names) !== JSON.stringify([...manifest.declaredIntents].sort()))
    fail('generated intents do not match compiled declaration');
  const layoutTree = layout?.layoutTree;
  if (!layoutTree || JSON.stringify(layoutTree) !== JSON.stringify(manifest.layoutTree))
    fail('compiled layout tree differs from projected layout');
  const portable = manifest.portableMode === true;
  if (!Array.isArray(manifest.missingPortableIntents) ||
      JSON.stringify([...manifest.missingPortableIntents].sort()) !== JSON.stringify(portable ? [] : names))
    fail('unsupported intent inventory must be complete');
  if (portable && (Object.keys(manifest.portableImplementations || {}).sort().join('|') !== names.join('|')))
    fail('portable implementation registry is not bound to compiled intents');
  const surfaceSelector = identity(surface?.rootSelector);
  const appRoot = make('article', {'data-mcel-standalone-app-id':manifest.appId});
  setSelector(appRoot, surfaceSelector);
  const treeRoot = createTree(layoutTree.root);
  appRoot.append(treeRoot);
  const controlPanel = make('div', {'data-mcel-standalone-controls':''});
  const parameters = portable ? make('div', {'data-mcel-portable-parameters':''}) : null;
  if (parameters) {
    for (const [key, label] of [['path','Project-relative file path'],['text','Draft / expression / prompt'],
                                  ['token','Calculator token'],['mode','Calculator mode']]) {
      const field=make('input',{'data-mcel-parameter':key,'aria-label':label,placeholder:label});
      field.value=key==='path'?'example.txt':key==='mode'?'graphing':key==='token'?'1':'';
      parameters.append(field);
    }
    appRoot.append(parameters);
  }
  const portableRuntime = portable ? await (await import('./portable.js')).createPortableRuntime(manifest.appId,names) : null;
  const responseView = portable ? make('pre', {'data-mcel-portable-result':''}) : null;
  for (const name of names) {
    const button = make('button', {'data-mcel-unimplemented-intent':name,
      'aria-label': `Unavailable: ${name}`,title:'No portable execution implementation is declared',type:'button'});
    if (!portable) button.disabled = true;
    else button.disabled = manifest.portableImplementations[name] !== 'local-pure-core';
    if (portable) {
      button.removeAttribute('data-mcel-unimplemented-intent');
      button.dataset.mcelPortableIntent=name;
      button.title=button.disabled?'Explicit capability grant required':'Package-owned pure-core operation';
      button.setAttribute('aria-label',name);
      button.addEventListener('click',async () => {
        const values=Object.fromEntries([...parameters.querySelectorAll('input')]
          .map(element=>[element.dataset.mcelParameter,element.value]));
        const payload={...values,expression:values.text || undefined};
        if (name==='editDraft')payload.path=portableRuntime.inspect()?.activeFile?.path || values.path;
        if (name==='drawGraph')payload.expression=values.text || 'x';
        if (name==='evaluateMathics')payload.expression=values.text;
        try { const outcome=await portableRuntime.dispatch(name,payload);
          responseView.textContent=JSON.stringify(outcome,null,2); }
        catch(e){responseView.textContent=String(e.message||e);}
      });
    }
    button.textContent = intents[name]?.label || name;
    controlPanel.append(button);
  }
  appRoot.append(controlPanel);
  if(responseView)appRoot.append(responseView);
  content.replaceChildren(appRoot);
  applyRecursive(layoutTree.root, treeRoot);
  state.ready = true;
  state.structuralVerified = [...treeRoot.querySelectorAll('[data-mcel-structural-node]')].length + 1 === state.nodes.length
    && (portable ? names.every(name => !!controlPanel.querySelector(`[data-mcel-portable-intent="${name}"]`))
                 : !names.some(name => !controlPanel.querySelector(`[data-mcel-unimplemented-intent="${name}"]:disabled`)));
  if (!state.structuralVerified) fail('independent structural region/control inventory is incomplete');
  state.disabledIntents = portable ? names.filter(n => manifest.portableImplementations[n] !== 'local-pure-core') : names;
  status.textContent = portable
    ? `Independent ${manifest.appId} domain runtime ready. External capabilities require separate grants; full proof pending.`
    : `Independent ${manifest.appId} presentation ready; ${names.length} portable operations unimplemented. Semantic proof refused.`;
  const host = Object.freeze({schema, appId:manifest.appId, ...state,
    portableMode:portable,
    declaredIntents:Object.freeze([...names]),
    dispatch:portable ? portableRuntime.dispatch : portMissing,
    inspect:() => portable ? { ...state,domain:portableRuntime.inspect()} : ({...state}),
    configureCapabilities(provider) {
      if (!portable)fail('non-portable presentation cannot receive capabilities');
      portableRuntime.configureCapabilities(provider);
      for (const button of controlPanel.querySelectorAll('[data-mcel-portable-intent]')) {
        button.disabled=false;button.title='Authorized portable operation';
      }
      state.disabledIntents=[];
    },
    packageFingerprint:manifest.source.packageFingerprint});
  globalThis.McelStandalonePackageHost = host;
  window.dispatchEvent(new CustomEvent('mcel-standalone-package-ready',{detail:{appId:manifest.appId}}));
}
load().catch(error => {
  state.error=String(error?.message || error);if(status)status.textContent=state.error;
  globalThis.McelStandalonePackageHost=Object.freeze({schema,ready:false,
    structuralVerified:false,fullSemanticRuntimeProven:false,error:state.error});
});
