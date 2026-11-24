/* This page runs on the unprivileged Document Server origin, never MC's origin. */
(() => {
  "use strict";
  const hosts = new Set(["dev.main-computer.localhost", "unleashed.main-computer.localhost", "debug.main-computer.localhost", "safe.main-computer.localhost"]);
  const params = new URLSearchParams(location.hash.slice(1));
  const parentOrigin = params.get("parent"), channel = params.get("channel");
  let origin;
  try { origin = new URL(parentOrigin); } catch { return; }
  if (window.parent === window || location.protocol !== "https:" || location.hostname !== "onlyoffice.main-computer.localhost" ||
      origin.protocol !== "https:" || !hosts.has(origin.hostname) || origin.origin !== parentOrigin || !/^[a-f0-9-]{36}$/.test(channel || "")) return;
  let editor = null, configured = false;
  function tell(type, extra = {}) { window.parent.postMessage({type, channel, ...extra}, parentOrigin); }
  window.addEventListener("message", event => {
    const msg = event.data;
    if (event.origin !== parentOrigin || event.source !== window.parent || !msg || msg.channel !== channel) return;
    if (msg.type === "mc-oo-config" && !configured) {
      configured = true;
      try {
        if (!msg.config || !msg.config.document || !msg.config.editorConfig || typeof msg.config.token !== "string") throw Error("Missing signed editor configuration");
        if (!window.DocsAPI || typeof window.DocsAPI.DocEditor !== "function") throw Error("Document Server API not available");
        const config = {...msg.config, events: {
          onAppReady() { tell("mc-oo-app-ready"); },
          onDocumentReady() { tell("mc-oo-document-ready"); },
          onError() { tell("mc-oo-error", {message: "Document Server reported an editor error"}); }
        }};
        editor = new DocsAPI.DocEditor("editor", config);
      } catch (e) { tell("mc-oo-error", {message: String(e.message || e)}); }
    } else if (msg.type === "mc-oo-comment" && editor && typeof msg.requestId === "string") {
      // Structured operation, never arbitrary parent-supplied JavaScript.
      try {
        if (typeof msg.text !== "string" || msg.text.length > 100000) throw Error("Invalid comment text");
        if (typeof editor.createConnector !== "function") throw Error("ONLYOFFICE Automation connector unavailable");
        const connector = editor.createConnector();
        const command = new Function(`const text = ${JSON.stringify(msg.text)};
          let selection = typeof Api.GetSelection === 'function' ? Api.GetSelection() : null;
          if (!selection && typeof Api.GetActiveSheet === 'function') {
            const sheet = Api.GetActiveSheet();
            selection = typeof sheet.GetSelection === 'function' ? sheet.GetSelection() : sheet.GetActiveCell();
          }
          if (!selection) return 'No cell selection available';
          if (typeof selection.AddComment === 'function') selection.AddComment(text, 'Main Computer');
          else if (typeof selection.ForEach === 'function') selection.ForEach(cell => cell.AddComment(text, 'Main Computer'));
          else return 'Selected range cannot receive comments';
          return 'Code add-on attached';`);
        connector.callCommand(command, result => tell("mc-oo-comment-result", {requestId: msg.requestId, message: typeof result === "string" ? result.slice(0, 500) : "Command finished"}));
      } catch (e) { tell("mc-oo-comment-result", {requestId: msg.requestId, message: String(e.message || e).slice(0, 500)}); }
    }
  });
  tell("mc-oo-host-ready");
})();
