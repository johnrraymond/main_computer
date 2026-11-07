/* Shared MCEL semantic composition for Chat Console, standalone and embedded.
 * Notebook state, execution, thread storage and mountEmbedded remain owned by
 * the existing Chat Console runtime. This module owns layout semantics and
 * reflow of those DOM controls; no host-specific markup or viewport guessing.
 */
(function (root) {
  "use strict";
  if (!root || root.MainComputerMCELChatConsole) return;

  const CONTRACT = Object.freeze({
    id: "mcel.chat-console.composition.v1",
    authority: "semantic-composition",
    host: "[data-chat-console-embed], #chat-console-app",
    regions: Object.freeze([
      {role: "shell", selector: ".chat-console-shell", required: true},
      {role: "thread-layout", selector: ".chat-console-thread-layout", required: true},
      {role: "threads", selector: ".chat-thread-rail"},
      {role: "workspace", selector: ".chat-thread-workspace", required: true},
      {role: "notebook", selector: ".chat-console-notebook", required: true},
      {role: "cell", selector: ".chat-cell", many: true},
      {role: "cell-choices", selector: ".chat-cell-tabs", many: true},
      {role: "cell-body", selector: ".chat-cell-content", many: true},
      {role: "cell-editor", selector: ".chat-cell textarea", many: true},
      {role: "cell-actions", selector: ".chat-cell-controls", many: true},
      {role: "thinking", selector: ".chat-thinking-frame", many: true},
      {role: "thinking-collection", selector: ".chat-thinking-grid", many: true},
      {role: "thinking-card", selector: ".chat-thinking-card", many: true},
      {role: "output", selector: ".chat-console-cell-output", many: true},
      {role: "rag-controls", selector: ".chat-rag-at-controls", many: true}
    ])
  });

  function compose(hostOrChild) {
    if (!hostOrChild || typeof hostOrChild.querySelector !== "function") return null;
    const host = hostOrChild.matches?.(CONTRACT.host)
      ? hostOrChild : hostOrChild.closest?.(CONTRACT.host);
    if (!host) return null;
    // Never declare a raw/partial mount semantic: all mandatory regions must
    // be present before the host is marked composed.
    const found = [];
    for (const region of CONTRACT.regions) {
      const matches = [...host.querySelectorAll(region.selector)];
      if (region.required && matches.length !== 1) {
        host.removeAttribute("data-mcel-semantic-chat");
        host.removeAttribute("data-mcel-chat-contract");
        return null;
      }
      found.push({region, matches});
    }
    // Annotate the existing interactive nodes rather than replacing them.
    // Event listeners, textarea drafts, pending operations and thread state
    // survive every composition and every resize without remounting.
    for (const {region, matches} of found) {
      for (const element of matches) element.setAttribute("data-mcel-chat-role", region.role);
    }
    host.setAttribute("data-mcel-semantic-chat", "");
    host.setAttribute("data-mcel-chat-contract", CONTRACT.id);
    host.setAttribute("data-mcel-chat-context",
      host.matches("[data-chat-console-embed]") ? "embedded" : "standalone");
    return {contract: CONTRACT.id, context: host.getAttribute("data-mcel-chat-context"),
      regions: found.reduce((count, item) => count + item.matches.length, 0)};
  }

  // MCEL owns each notebook cell's structure. Existing typed-cell and output
  // adapters contribute interactive content; their handlers and data remain
  // untouched. The same construction is used in both mounting contexts.
  function renderCell({id, type, renderTabs, renderBody, onSelect, onFiles} = {}) {
    if (typeof id !== "string" || !id || typeof type !== "string" ||
        typeof renderTabs !== "function" || typeof renderBody !== "function") {
      throw new TypeError("mcel-chat-cell-contract-invalid");
    }
    const doc = root.document;
    const element = doc.createElement("article");
    const output = type === "output";
    element.className = `chat-cell chat-cell-${type} ` +
      (output ? "chat-console-cell-output chat-console-output-indent" : "chat-console-cell-input");
    element.dataset.cellId = id;
    element.dataset.mcelChatRole = output ? "output" : "cell";
    element.dataset.mcelChatCellType = type;
    const tabs = renderTabs();
    const body = renderBody();
    if (!(tabs instanceof doc.defaultView.Node) || !(body instanceof doc.defaultView.Node)) {
      throw new TypeError("mcel-chat-cell-adapter-node-required");
    }
    tabs.dataset.mcelChatRole = "cell-choices";
    body.classList.add("chat-cell-content");
    body.dataset.mcelChatRole = "cell-body";
    element.append(tabs, body);
    if (typeof onSelect === "function") {
      element.addEventListener("focusin", onSelect);
      element.addEventListener("click", onSelect);
    }
    if (!output && typeof onFiles === "function") {
      element.addEventListener("dragover", event => {
        event.preventDefault();
        element.classList.add("drag-over");
      });
      element.addEventListener("dragleave", () => element.classList.remove("drag-over"));
      element.addEventListener("drop", event => {
        event.preventDefault();
        element.classList.remove("drag-over");
        onFiles([...(event.dataTransfer?.files || [])]);
      });
    }
    return element;
  }

  function composeAll() {
    if (!root.document) return [];
    return [...root.document.querySelectorAll(CONTRACT.host)].map(compose).filter(Boolean);
  }

  root.MainComputerMCELChatConsole = Object.freeze({contract: CONTRACT, compose, composeAll, renderCell});
  if (root.document?.readyState === "loading") {
    root.document.addEventListener("DOMContentLoaded", composeAll, {once: true});
  } else {
    composeAll();
  }
})(typeof window !== "undefined" ? window : null);
