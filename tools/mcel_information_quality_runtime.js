/* Frob-only DOM binding of genuine MCEL registered component contracts.
   Uses presentation-only labels: all lookup/actions use source_rows. */
(() => {
  'use strict';
  const data = window.MCEL_INFORMATION_CONTRACT;
  const registry = window.McelElementRegistry;
  if (!data || !registry) throw Error('MCEL_INFORMATION_CONTRACT_PREREQUISITE_MISSING');
  const main = document.getElementById('mcel-collection-mount');
  const inspector = document.getElementById('mcel-inspector');
  const cols = data.field_contracts;
  const widths = data.projection.column_widths_px;
  const projected = Object.fromEntries(data.projection.rows.map(row => [row.id, row]));
  const source = Object.fromEntries(data.source_rows.map(row => [String(row.id), row]));
  const nodeTree = data.tree;
  const hasField = (rowId, key) => Object.prototype.hasOwnProperty.call(source[rowId], key);
  const original = (rowId, key) => hasField(rowId, key) ? source[rowId][key] : '';
  const statusText = (rowId, key) => !hasField(rowId, key) ? 'Not provided' :
    original(rowId, key) === '' ? 'Empty' : null;
  let activeIds = data.source_rows.map(row => String(row.id));
  const evidence = {events: [], identityMapping: true, searchUsesRaw: true, sortUsesRaw: true};
  const tagFor = id => {
    const def = registry.get(id);
    if (!def || !def.renderer || !def.renderer.htmlTag) throw Error('UNREGISTERED_MCEL_ELEMENT ' + id);
    return def.renderer.htmlTag;
  };
  function cellFor(node, rowId) {
    const key = node.props.semanticField;
    const v = projected[rowId].cells[key];
    const el = document.createElement(tagFor(node.elementId));
    el.dataset.mcelElementId = node.elementId;
    el.dataset.semanticInstanceId = node.id;
    el.dataset.semanticField = key;
    el.dataset.sourceRowId = rowId;
    el.dataset.full = String(original(rowId, key));
    el.dataset.sourceMissing = String(!hasField(rowId, key));
    el.dataset.sourceEmpty = String(hasField(rowId, key) && original(rowId, key) === '');
    el.dataset.enabled = String(v.enabled);
    el.dataset.strategy = v.strategy;
    const label = document.createElement('span');
    label.className = 'field-label'; label.textContent = node.label;
    const value = document.createElement('span');
    value.className = 'field-value';
    // Missing and explicitly empty are observable, but never manufactured data.
    value.textContent = statusText(rowId, key) ?? String(v.display);
    value.title = statusText(rowId, key) ?? String(original(rowId, key));
    el.append(label, value);
    return el;
  }
  function render(ids) {
    main.replaceChildren();
    const collection = document.createElement(tagFor(nodeTree.elementId));
    collection.dataset.mcelElementId = nodeTree.elementId;
    collection.dataset.semanticInstanceId = nodeTree.id;
    for (const rowId of ids) {
      const node = nodeTree.children.find(row => row.props.rowId === rowId);
      const row = document.createElement(tagFor(node.elementId));
      row.dataset.mcelElementId = node.elementId;
      row.dataset.semanticInstanceId = node.id;
      row.dataset.rowId = rowId;
      row.setAttribute('role', 'button'); row.tabIndex = 0;
      row.setAttribute('aria-label', 'Inspect ' + rowId);
      for (const field of node.children) row.append(cellFor(field, rowId));
      const inspect = () => {
        inspector.replaceChildren();
        const h = document.createElement('h2'); h.textContent = 'Selected ' + rowId;
        inspector.append(h);
        for (const col of cols) {
          const p = document.createElement('p');
          p.dataset.sourceField = col.key;
          p.textContent = col.label + ': ' +
            (statusText(rowId, col.key) === 'Not provided' ? '[not provided]' :
             statusText(rowId, col.key) === 'Empty' ? '[empty]' : String(original(rowId, col.key)));
          inspector.append(p);
        }
        evidence.events.push({action:'select', rowId});
      };
      row.addEventListener('click', inspect);
      row.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); inspect(); }
      });
      collection.append(row);
    }
    main.append(collection);
    evidence.events.push({action:'render', rowIds:ids});
  }
  function filter(query) {
    const term = String(query).toLowerCase();
    activeIds = data.source_rows.filter(row => cols.some(col => String(Object.prototype.hasOwnProperty.call(row,col.key) ? row[col.key] : '').toLowerCase().includes(term)))
      .map(row => String(row.id));
    render(activeIds);
    return activeIds;
  }
  function sortBy(key, descending=false) {
    if (!cols.some(col => col.key === key)) throw Error('INVALID_SORT_KEY');
    activeIds = activeIds.slice().sort((a,b) => {
      const av=original(a,key), bv=original(b,key);
      const cmp= typeof av === 'number' && typeof bv === 'number' ? av-bv : String(av).localeCompare(String(bv));
      return (descending ? -1 : 1)*cmp || a.localeCompare(b);
    });
    render(activeIds);
    evidence.events.push({action:'sort', key, descending});
    return activeIds;
  }
  document.getElementById('search').addEventListener('input', ev=>filter(ev.target.value));
  document.getElementById('sort').addEventListener('change', ev=>sortBy(ev.target.value));
  window.__MCEL_INFORMATION_FROB = {data, evidence, filter, sortBy,
    get rows() {return activeIds.slice()},
    original: (id,key) => original(String(id),key)};
  render(activeIds);
})();
