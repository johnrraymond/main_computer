"use strict";
/* MCEL semantic value updaters. A marked element owns its update contract;
 * callers propose values, never replacement DOM. This does not schedule work,
 * authorize operations, render HTML, or touch unmarked elements.
 */
(function (global) {
  const TYPES = new Set(["text", "disabled", "hidden", "aria-selected"]);
  function mount(root) {
    if (!root || typeof root.querySelectorAll !== "function")
      throw Error("MCEL_SEMANTIC_UPDATE_ROOT_REQUIRED");
    const entries = new Map();
    for (const element of root.querySelectorAll("[data-mcel-updatable][data-mcel-update-key]")) {
      const key = element.getAttribute("data-mcel-update-key");
      const type = element.getAttribute("data-mcel-updatable");
      if (!key || !TYPES.has(type) || entries.has(key))
        throw Error("MCEL_SEMANTIC_UPDATE_BINDING_INVALID:" + key);
      entries.set(key, {element, type});
    }
    let disposed = false;
    function has(key) {
      return !disposed && entries.has(key) && root.contains(entries.get(key).element);
    }
    function update(proposed) {
      if (disposed || !proposed || typeof proposed !== "object" || Array.isArray(proposed))
        throw Error("MCEL_SEMANTIC_UPDATE_INVALID_PROPOSAL");
      const items = Object.entries(proposed);
      // Validate all bindings before touching anything. An absent binding is
      // a contract error, never permission to replace the existing DOM.
      const missing = items.find(([key]) => !has(key));
      if (missing) throw Error("MCEL_SEMANTIC_UPDATE_BINDING_MISSING:" + missing[0]);
      for(const [key,value] of items){
        const {element,type}=entries.get(key);
        if(type==="text" && element.textContent!==(value == null ? "" : String(value)) &&
            [...element.childNodes].some(child => child.nodeType!==3))
          throw Error("MCEL_SEMANTIC_UPDATE_TEXT_OWNERSHIP:"+key);
      }
      let changed = 0;
      for (const [key, value] of items) {
        const {element, type} = entries.get(key);
        if (type === "text") {
          const next = value == null ? "" : String(value);
          if (element.textContent !== next) {
            if (element.firstChild) element.firstChild.nodeValue = next;
            else element.appendChild(element.ownerDocument.createTextNode(next));
            changed++;
          }
        } else if (type === "disabled" || type === "hidden") {
          const next = !!value;
          if (element[type] !== next) {element[type] = next; changed++;}
        } else if (type === "aria-selected") {
          const next = String(!!value);
          if (element.getAttribute("aria-selected") !== next) {
            element.setAttribute("aria-selected", next); changed++;
          }
        }
      }
      return {changed};
    }
    function dispose() { disposed = true; entries.clear(); }
    return Object.freeze({has, update, dispose});
  }
  global.McelSemanticValueUpdaters = Object.freeze({mount});
})(typeof window !== "undefined" ? window : globalThis);
