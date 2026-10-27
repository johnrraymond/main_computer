"use strict";
/* MCEL declarative collection runtime — generic consumer of validated application IR.
 * It does not compile declarations, infer permissions, call remote services, or
 * assert canonical persistence. Mounted instances own independent volatile state.
 */
(function (global) {
  const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const schemaEquivalent = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function invariant(condition, reason) { if (!condition) throw Error("MCEL_COLLECTION_REFUSED:" + reason); }
  function node(tag, name, cls) {
    const el = document.createElement(tag);
    if (name) el.setAttribute("data-mcel", name);
    if (cls) el.className = cls;
    return el;
  }
  function fieldLabel(name) { return name.replace(/([A-Z])/g, " $1").replace(/[-_]/g, " ").replace(/^./, c => c.toUpperCase()); }
  function validate(schema, value, label) {
    invariant(schema && typeof schema === "object", "schema-missing:" + label);
    if (schema.kind === "string") {
      invariant(typeof value === "string", "type:" + label);
      invariant(value.trim().length >= (schema.minLength || 0), "required:" + label);
      if (Array.isArray(schema.enum)) invariant(schema.enum.includes(value), "enum:" + label);
    } else if (schema.kind === "integer") {
      invariant(Number.isSafeInteger(value), "integer:" + label);
      if (own(schema, "minimum")) invariant(value >= schema.minimum, "minimum:" + label);
    } else if (schema.kind === "boolean") invariant(typeof value === "boolean", "boolean:" + label);
    else if (schema.kind === "record") {
      invariant(value && typeof value === "object" && !Array.isArray(value), "record:" + label);
      invariant(Object.keys(value).length === Object.keys(schema.fields || {}).length &&
        Object.keys(value).every(k => own(schema.fields, k)), "record-fields:" + label);
      for (const [key, nested] of Object.entries(schema.fields)) validate(nested, value[key], label + "." + key);
    } else if (schema.kind === "list") {
      invariant(Array.isArray(value), "list:" + label);
      const ids = new Set();
      for (const item of value) {
        validate(schema.items, item, label + ".item");
        invariant(typeof item[schema.key] === "string" && item[schema.key].trim(), "blank-key");
        invariant(!ids.has(item[schema.key]), "duplicate:" + item[schema.key]);
        ids.add(item[schema.key]);
      }
    } else invariant(false, "unknown-schema-kind:" + label);
  }
  function contract(ir) {
    invariant(ir && ir.schema === "mcel.application-ir.v1", "unknown-ir");
    const nodes = (ir.surfaces || []).flatMap(surface => surface.nodes || []);
    const collections = nodes.filter(x => x.nodeKind === "collection");
    const forms = nodes.filter(x => x.nodeKind === "form");
    invariant(collections.length === 1 && forms.length === 1, "single-collection-contract-required");
    const collection = collections[0];
    const form = forms[0];
    invariant(collection.state && collection.actions && form.intent &&
      collection.actions.add?.ref === form.intent.ref, "collection-form-not-bound");
    const states = new Map((ir.states || []).map(x => [x.id, x]));
    const state = states.get(collection.state.ref);
    invariant(state && state.authority === "canonical" && state.schema?.kind === "list" &&
      state.schema.key === collection.key && state.schema.items?.kind === "record", "keyed-state-required");
    const expectedFields = Object.keys(state.schema.items.fields);
    invariant(Array.isArray(collection.fields) && collection.fields.length > 0 &&
      new Set(collection.fields).size === collection.fields.length &&
      collection.fields.includes(state.schema.key) &&
      collection.fields.every(f => expectedFields.includes(f)) &&
      Object.keys(form.fields).length === expectedFields.length &&
      Object.keys(form.fields).every(f => expectedFields.includes(f) &&
        schemaEquivalent(form.fields[f], state.schema.items.fields[f])), "declared-field-mismatch");
    const intents = new Map((ir.intents || []).map(x => [x.id, x]));
    const effects = new Map((ir.effects || []).map(x => [x.id, x]));
    const expected = {add: "list.append", update: "list.update-by-key", remove: "list.remove-by-key"};
    const actions = {};
    for (const [operation, kind] of Object.entries(expected)) {
      const handle = collection.actions[operation];
      invariant(handle && typeof handle.ref === "string", "missing-action:" + operation);
      const intent = intents.get(handle.ref);
      const steps = intent?.transition?.steps;
      const effect = effects.get(intent?.effectRefs?.[0]?.ref);
      invariant(intent?.operationKind === "mutation" && steps?.length === 1 && steps[0].kind === kind &&
        steps[0].target?.ref === state.id && intent.writes?.length === 1 && intent.writes[0].ref === state.id &&
        effect?.effectKind === "canonical-write" && effect.owner.ref === intent.id && effect.authority.ref === state.id,
        "unverified-action:" + operation);
      const inputs = new Map((intent.input || []).map(x => [x.sourceName, x]));
      const from = (expression, name) => expression?.kind === "input.read" &&
        expression.input?.ref === inputs.get(name)?.id;
      if (operation === "add") {
        invariant(inputs.size === 1 && from(steps[0].value, "item") &&
          schemaEquivalent(inputs.get("item")?.schema, state.schema.items), "add-input-contract-mismatch");
      } else {
        invariant(steps[0].keyField === state.schema.key && from(steps[0].key, "key") &&
          inputs.get("key")?.schema.kind === "string", "action-key-mismatch");
        if (operation === "remove") invariant(inputs.size === 1, "remove-input-contract-mismatch");
        else invariant(inputs.size === 2 && from(steps[0].update, "item") &&
          schemaEquivalent(inputs.get("item")?.schema, state.schema.items), "update-input-contract-mismatch");
      }
      actions[operation] = {intent, effect};
    }
    validate(state.schema, state.initial || [], "initial");
    return {state, collection, form, actions};
  }
  function mount(root, ir) {
    invariant(root && root.nodeType === 1, "missing-root");
    invariant(!root.hasAttribute("data-mcel-mounted"), "duplicate-mount");
    const c = contract(ir); // refuse before touching DOM
    let data = copy(c.state.initial || []);
    let version = 0;
    let search = "";
    let sort = c.collection.key;
    const receipts = [];
    root.setAttribute("data-mcel-mounted", ir.application.appId);
    root.replaceChildren();
    const wrap = node("div", "application", "mcel-app");
    const heading = node("header", "heading", "mcel-heading");
    const h1 = node("h1"); h1.textContent = ir.application.title;
    const subtitle = node("p"); subtitle.textContent = "A governed collection workspace";
    heading.append(h1, subtitle);
    const feedback = node("div", "feedback", "mcel-feedback"); feedback.setAttribute("role", "status");
    const form = node("form", "create-form", "mcel-form"); form.noValidate = true;
    function typedInput(name, schema, value, role) {
      const input = node(Array.isArray(schema.enum) ? "select" : "input", role, "mcel-input");
      input.name = name;
      if (Array.isArray(schema.enum)) {
        for (const choice of schema.enum) {
          const option = node("option"); option.value = choice; option.textContent = choice; input.append(option);
        }
      } else if (schema.kind === "integer") {
        input.type = "number"; input.step = "1";
        if (own(schema, "minimum")) input.min = String(schema.minimum);
      } else if (schema.kind === "boolean") input.type = "checkbox";
      else input.type = "text";
      if (value !== undefined) {
        if (schema.kind === "boolean") input.checked = value;
        else input.value = String(value);
      }
      return input;
    }
    const inputs = {};
    for (const [name, schema] of Object.entries(c.form.fields)) {
      const label = node("label", null, "mcel-label"); label.textContent = fieldLabel(name);
      const input = typedInput(name, schema, undefined, "field-" + name);
      label.append(input); inputs[name] = input; form.append(label);
    }
    const addButton = node("button", "create", "mcel-button mcel-primary"); addButton.type = "submit"; addButton.textContent = "Add record";
    form.append(addButton);
    const toolbar = node("div", "toolbar", "mcel-toolbar");
    const searchLabel = node("label", null, "mcel-label"); searchLabel.textContent = "Search";
    const searchInput = node("input", "search", "mcel-input"); searchInput.type = "search"; searchLabel.append(searchInput);
    const sortLabel = node("label", null, "mcel-label"); sortLabel.textContent = "Sort by";
    const sortSelect = node("select", "sort", "mcel-input");
    for (const field of c.collection.fields) {const opt = node("option");opt.value=field;opt.textContent=fieldLabel(field);sortSelect.append(opt);}
    sortSelect.value = sort;
    sortLabel.append(sortSelect);toolbar.append(searchLabel,sortLabel);
    const count = node("p", "count", "mcel-count");
    const list = node("div", "rows", "mcel-rows");
    const receiptList = node("ol", "receipts", "mcel-receipts");receiptList.setAttribute("aria-label", "Operation receipts");
    function addReceipt(op, disposition, reason, before, after) {
      const evidence = c.actions[op];
      const receipt = Object.freeze({sequence: receipts.length + 1, intent: evidence.intent.id,
        effect: evidence.effect.id, disposition, reason, before: copy(before), after: copy(after), version});
      receipts.push(receipt);
      const el = node("li", "receipt"); el.dataset.disposition = disposition; el.dataset.intent = evidence.intent.id;
      el.dataset.reason = reason || ""; el.textContent = `${op}: ${disposition}${reason ? " (" + reason + ")" : ""}`;
      receiptList.prepend(el);
      root.dataset.receiptCount = String(receipts.length);
      return receipt;
    }
    function execute(op, payload) {
      invariant(own(c.actions, op), "undeclared-operation");
      const before = copy(data);
      try {
        const schema = c.state.schema;
        const key = schema.key;
        if (op === "add") {
          validate(schema.items, payload.item, "item");
          invariant(!data.some(x => x[key] === payload.item[key]), "duplicate:" + payload.item[key]);
          data = [...data, copy(payload.item)];
        } else if (op === "update") {
          validate(schema.items.fields[key], payload.key, "key");
          validate(schema.items, payload.item, "item");
          invariant(payload.item[key] === payload.key, "identity-change");
          invariant(data.some(x => x[key] === payload.key), "missing-row:" + payload.key);
          data = data.map(x => x[key] === payload.key ? copy(payload.item) : x);
        } else if (op === "remove") {
          validate(schema.items.fields[key], payload.key, "key");
          invariant(data.some(x => x[key] === payload.key), "missing-row:" + payload.key);
          data = data.filter(x => x[key] !== payload.key);
        }
        validate(schema, data, "collection");
        version += 1;
        addReceipt(op, "completed", "", before, data);
        feedback.textContent = fieldLabel(op) + " completed";
        renderRows();
        return true;
      } catch (err) {
        data = before;
        const reason = String(err.message || err).replace(/^MCEL_COLLECTION_REFUSED:/, "");
        addReceipt(op, "refused-before-attempt", reason, before, data);
        feedback.textContent = "Refused: " + reason;
        return false;
      }
    }
    function readInput(name, schema, el) {
      if (schema.kind === "boolean") return el.checked;
      if (schema.kind === "integer") return el.value.trim() === "" ? null : Number(el.value);
      return el.value.trim();
    }
    form.addEventListener("submit", evt => {
      evt.preventDefault();
      const item = {};
      for (const [name, schema] of Object.entries(c.form.fields)) item[name] = readInput(name, schema, inputs[name]);
      if (execute("add", {item})) form.reset();
    });
    function renderRows() {
      const filtered = data.filter(item => c.collection.fields.some(f => String(item[f]).toLowerCase().includes(search.toLowerCase())));
      filtered.sort((a,b) => String(a[sort]).localeCompare(String(b[sort]), undefined, {numeric:true}));
      list.replaceChildren();
      count.textContent = `${filtered.length} of ${data.length} records`;
      for (const record of filtered) {
        const row = node("article", "row", "mcel-row"); row.dataset.key = record[c.collection.key];
        const grid = node("div", null, "mcel-row-fields");
        for (const field of c.collection.fields) {
          const cell = node("div", "cell-" + field, "mcel-cell");
          const name = node("span", null, "mcel-cell-name"); name.textContent = fieldLabel(field);
          const value = node("span", "value-" + field, "mcel-cell-value"); value.textContent = String(record[field]);
          cell.append(name, value);grid.append(cell);
        }
        row.append(grid);
        const actions = node("div", null, "mcel-row-actions");
        const edits = {};
        for (const [field, schema] of Object.entries(c.state.schema.items.fields)) {
          if (field === c.collection.key) continue; // identity cannot be reassigned by row editing
          const label = node("label", null, "mcel-edit-label");
          label.textContent = fieldLabel(field);
          const edit = typedInput(field, schema, record[field], "edit-" + field);
          edit.setAttribute("aria-label", `Edit ${field} for ${record[c.collection.key]}`);
          label.append(edit); actions.append(label); edits[field] = {edit, schema};
        }
        const save = node("button", "update", "mcel-button");
        save.type = "button"; save.textContent = "Save";
        save.addEventListener("click", () => {
          const next = {...record};
          for (const [field, {edit, schema}] of Object.entries(edits)) next[field] = readInput(field, schema, edit);
          execute("update", {key: record[c.collection.key], item: next});
        });
        actions.append(save);
        const remove = node("button", "remove", "mcel-button mcel-danger");
        remove.type = "button"; remove.textContent = "Remove";
        remove.addEventListener("click", () => execute("remove", {key: record[c.collection.key]}));
        actions.append(remove);row.append(actions);list.append(row);
      }
      root.dataset.version = String(version);
      root.dataset.rowCount = String(data.length);
    }
    searchInput.addEventListener("input", () => {search=searchInput.value;renderRows();});
    sortSelect.addEventListener("change", () => {sort=sortSelect.value;renderRows();});
    wrap.append(heading,feedback,form,toolbar,count,list,receiptList);
    root.append(wrap);renderRows();
    return Object.freeze({snapshot: () => copy(data), receipts: () => copy(receipts), version: () => version,
      destroy: () => {root.replaceChildren();root.removeAttribute("data-mcel-mounted");}});
  }
  global.McelCollectionApplication = Object.freeze({mount});
})(window);
