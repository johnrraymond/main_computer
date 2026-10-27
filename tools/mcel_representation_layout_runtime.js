/* Patch 2 — actual DOM geometry connects semantic representation to layout.
 * Both before and after use the SAME algorithm and breakpoint. No fabricated
 * size-field visibility, hidden columns, scroll hacks, or source data changes.
 */
(() => {
  'use strict';
  const data = window.MCEL_INFORMATION_CONTRACT;
  const link = data && data.layout_link;
  const original = window.__MCEL_INFORMATION_FROB;
  if (!data || !original || link?.schema !== 'mcel.representation-layout-link.experiment.v1') {
    throw Error('MCEL_REPRESENTATION_LAYOUT_CONTRACT_MISSING');
  }
  const panel = document.querySelector('#panel');
  const mount = document.querySelector('#mcel-collection-mount');
  const columns = data.field_contracts;
  // Patch 3: the Frob is only a renderer and evidence collector. It does NOT
  // plan tracks or write CSS. The shared application layout engine owns both.
  const authority = window.McelSemanticLayoutInference;
  if (typeof authority?.mountSemanticCollectionLayout !== 'function')
    throw Error('MCEL_SHARED_LAYOUT_AUTHORITY_UNAVAILABLE');
  const mountResult=authority.mountSemanticCollectionLayout(panel,data,window.McelElementRegistry);
  if (mountResult.status!=='pass')
    throw Error('MCEL_SHARED_LAYOUT_AUTHORITY_REFUSED: '+mountResult.reason);
  const history = [];
  let last = null;
  function apply() {
    const result=mountResult.refresh();
    if (result.status!=='pass') throw Error('MCEL_SHARED_LAYOUT_AUTHORITY_REFUSED: '+result.reason);
    last=result.plan;
    history.push({width:panel.clientWidth,mode:last.mode,deficit:last.deficit_px,
      allocated:{...last.allocated_px}});
    const status=document.querySelector('#mode');
    if (status) status.textContent='Shared MCEL layout · '+last.mode+' · intrinsic '+last.intrinsic_px+'px';
    return last;
  }
  function measure() {
    if (!last) apply();
    const panelRect = panel.getBoundingClientRect();
    const required = {};
    const byKey = {};
    const geometry = [];
    for (const field of mount.querySelectorAll('mcel-field')) {
      const key = field.dataset.semanticField;
      const rect = field.getBoundingClientRect();
      const value = field.querySelector('.field-value');
      const valueRect = value.getBoundingClientRect();
      const label = field.querySelector('.field-label');
      const labelRect = label.getBoundingClientRect();
      const inside = rect.left >= panelRect.left - 1 && rect.right <= panelRect.right + 1 &&
        valueRect.left >= panelRect.left - 1 && valueRect.right <= panelRect.right + 1 &&
        labelRect.left >= panelRect.left - 1 && labelRect.right <= panelRect.right + 1;
      const uncut = value.scrollWidth <= value.clientWidth + 1 &&
        label.scrollWidth <= label.clientWidth + 1;
      const visible = inside && uncut && rect.width > 0 && valueRect.height > 0;
      if (!byKey[key]) byKey[key] = [];
      byKey[key].push(visible);
      geometry.push({row:field.dataset.sourceRowId,key,visible,
        displayed:value.textContent,raw:field.dataset.full,
        missing:field.dataset.sourceMissing === 'true',
        explicitly_empty:field.dataset.sourceEmpty === 'true',
        label_visible:label.scrollWidth <= label.clientWidth + 1,
        x:Math.round(rect.x),right:Math.round(rect.right),
        width:Math.round(rect.width),valueWidth:Math.round(valueRect.width),
        valueScrollWidth:value.scrollWidth});
    }
    for (const key of link.required_keys) required[key] = !!byKey[key]?.length && byKey[key].every(Boolean);
    const reduced = geometry.filter(g => !g.missing && !g.explicitly_empty &&
      g.displayed!==g.raw && columns.some(c => c.key===g.key &&
        c.presentation?.text_shrinker?.enabled) && data.enabled);
    return {...last, panel_width_px:panel.clientWidth,
      required_visible:required,all_required_visible:Object.values(required).every(Boolean),
      compressed_cell_count:reduced.length,
      missing_cell_count:geometry.filter(g=>g.missing).length,
      empty_cell_count:geometry.filter(g=>g.explicitly_empty).length,
      required_unsatisfied_keys:Object.keys(required).filter(k=>!required[k]),
      registered_component_tags:[...new Set(mount.querySelectorAll('mcel-field'))].map(f=>f.dataset.mcelElementId)
        .filter((v,i,a)=>a.indexOf(v)===i),
      rows:mount.querySelectorAll('mcel-row').length,
      cells:geometry,
      horizontal_document_overflow:document.documentElement.scrollWidth>innerWidth+1,
      layout_authority:panel.dataset.mcelCollectionLayoutAuthority,
      engine_schema:mountResult.evaluation.schema,
      events:history.slice()};
  }
  window.__MCEL_REPRESENTATION_LAYOUT = {apply,measure,history,
    engine:authority, evaluation:mountResult.evaluation, unmount:mountResult.unmount};
  apply();
})();
