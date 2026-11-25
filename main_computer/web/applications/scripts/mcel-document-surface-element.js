/* A mountable MCEL element, not a Pretty Docs or Chat Console application. */
(function (global) {
  "use strict";
  const elementId = "element.authoring.document-surface";
  const version = "mcel.document-surface.v1";
  const engine = McelDocumentEngine;
  let sequence = 0;
  const mounted = new WeakMap();
  const allowed = new Set("P DIV BR H1 H2 H3 H4 H5 H6 STRONG B EM I U S STRIKE SUB SUP UL OL LI BLOCKQUOTE PRE CODE SPAN FIGURE FIGCAPTION HR TABLE THEAD TBODY TR TH TD".split(" "));
  function cleanHtml(doc, html) {
    const t = doc.createElement("template");
    t.innerHTML = String(html ?? "");
    // Inert parse; never mount arbitrary AI HTML, event handlers, external URLs,
    // styles, custom elements, embedded executable content or application IDs.
    for (const n of [...t.content.querySelectorAll("*")]) {
      if (!allowed.has(n.tagName)) {
        if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "SVG", "MATH"].includes(n.tagName) || n.namespaceURI !== "http://www.w3.org/1999/xhtml") n.remove();
        else n.replaceWith(...n.childNodes);
        continue;
      }
      const math = n.dataset.docObject === "math" && ["SPAN", "FIGURE"].includes(n.tagName);
      for (const a of [...n.attributes]) {
        if (math && ["data-doc-object", "data-doc-object-id", "data-doc-object-layout", "data-latex"].includes(a.name)) continue;
        n.removeAttribute(a.name);
      }
      if (math) n.textContent = n.dataset.latex || "";
    }
    return t.innerHTML;
  }
  function mount(host, props = {}) {
    if (!host?.ownerDocument || mounted.has(host)) throw new Error("DOCUMENT_SURFACE_HOST_INVALID_OR_MOUNTED");
    const document = host.ownerDocument;
    const window = document.defaultView;
    const instanceId = `document-surface-${++sequence}`;
    const root = document.createElement("section");
    root.className = "mcel-document-surface-element";
    root.dataset.mcelElement = elementId;
    root.dataset.mcelElementKind = "document";
    root.dataset.mcelElementFamily = "authoring";
    root.dataset.mcelDocumentSurface = instanceId;
    root.setAttribute("aria-label", props.label || "Document surface");
    const documentCanvas = document.createElement("div");
    documentCanvas.className = "document-canvas";
    documentCanvas.setAttribute("data-mcel-chrome-exclusion", "");
    const documentPage = document.createElement("div");
    documentPage.className = "mc-page";
    documentPage.dataset.documentPageIndex = "1";
    const guide = document.createElement("div");
    guide.className = "mc-page-break-guide";
    guide.setAttribute("aria-hidden", "true");
    const documentEditor = document.createElement("div");
    documentEditor.className = "mc-page-content document-editor";
    documentEditor.contentEditable = "true";
    documentEditor.setAttribute("role", "textbox");
    documentEditor.setAttribute("aria-multiline", "true");
    documentEditor.setAttribute("aria-label", props.label || "Document content");
    documentEditor.spellcheck = true;
    documentPage.append(guide, documentEditor);
    documentCanvas.append(documentPage);
    root.append(documentCanvas);
    host.append(root);
    const documentSession = {layoutState: engine.normalizeDocumentLayoutState(props.layout || {}), selectedPath: ""};
    const storageData = new Map();
    const storage = {getItem: key => storageData.get(key) ?? null, setItem: (key, value) => storageData.set(key, value)};
    const controller = new AbortController();
    const eventOptions = {signal: controller.signal};
    let ruler = null, zoomLabel = null, fitWidth = true;
    let destroyed = false, revision = 0, current = "", pending = false, composing = false;
    const undo = [], redo = [], subscribers = new Set();
    const pagination = engine.createPagination({documentCanvas, documentPage, documentEditor, documentSession,
      saveDocumentDraft: commit, hydrateDocumentObjects: root => objects.hydrateAll(root),
      prepareDocumentObjectsForSerialization: root => objects.prepareForSerialization(root),
      renderDocumentPluginRail: () => objects.renderPluginRail(), deferWhileSelecting: true,
      onDeferred: () => { pending = true; }});
    const objects = engine.createObjects({documentCanvas, documentPage, documentEditor, documentSession, storage,
      getActiveDocumentEditor: pagination.getActiveDocumentEditor, documentBlockForRange: pagination.documentBlockForRange,
      saveDocumentDraft: commit, scheduleDocumentRepagination: schedule});
    function alive() { if (destroyed) throw new Error("DOCUMENT_SURFACE_DESTROYED"); }
    function notify(kind) {
      const event = {kind, ...snapshot()};
      for (const fn of [...subscribers]) { try { fn(event); } catch (e) { console.error(e); } }
    }
    function snapshot() {
      alive();
      return {instanceId, revision, html: current, layout: JSON.parse(JSON.stringify(documentSession.layoutState)), canUndo: !!undo.length, canRedo: !!redo.length};
    }
    function commit() {
      if (destroyed) return;
      const next = pagination.getDocumentEditorHtml();
      if (next === current) return;
      undo.push(current); if (undo.length > 100) undo.shift();
      redo.length = 0; current = next; revision++; notify("content");
    }
    function schedule() { if (!destroyed && !composing) pagination.scheduleDocumentRepagination(); }
    function ownsSelection() {
      const sel = window.getSelection();
      return sel?.rangeCount && documentCanvas.contains(sel.anchorNode) && documentCanvas.contains(sel.focusNode);
    }
    function clearOwnSelection() { if (ownsSelection()) window.getSelection().removeAllRanges(); }
    function setHtml(html) { clearOwnSelection(); pagination.setDocumentEditorHtml(html || "<p><br></p>"); }
    function setLayout(layout, emit = true) {
      alive();
      documentSession.layoutState = engine.normalizeDocumentLayoutState(layout);
      const state = documentSession.layoutState, size = engine.documentLayoutSize(state), margins = state.layout.margins;
      const available = documentCanvas.clientWidth - 64;
      const zoom = root.classList.contains("mcel-document-surface-with-toolbar") ? (fitWidth ? Math.min(state.view.zoom, available > 0 ? Math.max(.45, available / size.widthPx) : state.view.zoom) : state.view.zoom) : Math.min(state.view.zoom, available > 0 ? Math.max(.45, Math.min(1, available / size.widthPx)) : state.view.zoom);
      for (const [key, value] of Object.entries({"page-width": `${size.widthPx}px`, "page-height": `${size.heightPx}px`,
        "margin-top": `${margins.top}px`, "margin-right": `${margins.right}px`, "margin-bottom": `${margins.bottom}px`, "margin-left": `${margins.left}px`, zoom})) root.style.setProperty(`--document-${key}`, value);
      documentCanvas.classList.toggle("document-view-paged", state.view.mode === "paged");
      documentCanvas.classList.toggle("document-view-endless", state.view.mode === "endless");
      documentCanvas.classList.toggle("document-show-page-breaks", state.view.showPageBreaks);
      if (zoomLabel) zoomLabel.textContent = Math.round(zoom * 100) + "%";
      if (ruler) { ruler.style.width = `${size.widthPx * zoom}px`; ruler.style.setProperty("--ruler-inch",`${96*zoom}px`); ruler.style.setProperty("--ruler-tick",`${12*zoom}px`); ruler.style.setProperty("--ruler-left",`${margins.left*zoom}px`); ruler.style.setProperty("--ruler-right",`${margins.right*zoom}px`); if(ruler.dataset.geometry!==`${size.widthPx}:${zoom}`){ruler.dataset.geometry=`${size.widthPx}:${zoom}`;ruler.replaceChildren(); for(let i=1;i<size.widthPx/96;i++){ const mark=document.createElement("span"); mark.style.left=`${i*96*zoom}px`; mark.textContent=String(i); ruler.append(mark); } } }
      schedule(); if (emit) notify("layout");
    }
    function blocks() { return pagination.getDocumentPageContents().flatMap(page => [...page.childNodes]); }
    function blockFor(node) {
      const el = node.nodeType === 1 ? node : node.parentElement;
      const page = el?.closest(".mc-page-content");
      if (!page || !documentCanvas.contains(page) || node === page) return null;
      let top = node;
      while (top.parentNode !== page) top = top.parentNode;
      return top;
    }
    function selection() {
      alive(); if (!ownsSelection()) return null;
      const range = window.getSelection().getRangeAt(0);
      const a = blockFor(range.startContainer), b = blockFor(range.endContainer);
      // Only a single text-bearing block is a supported v1 replacement anchor.
      if (!a || a !== b || a.nodeType !== 1 || a.matches("[data-doc-object]") || a.querySelector("[data-doc-object]")) return null;
      const offset = (node, index) => { const r = document.createRange(); r.selectNodeContents(a); r.setEnd(node, index); return r.toString().length; };
      return {instanceId, revision, block: blocks().indexOf(a), start: offset(range.startContainer, range.startOffset), end: offset(range.endContainer, range.endOffset), text: range.toString()};
    }
    function rangeFor(anchor) {
      if (!anchor || anchor.instanceId !== instanceId || anchor.revision !== revision) throw new Error("DOCUMENT_SURFACE_STALE_ANCHOR");
      const block = blocks()[anchor.block];
      if (!block || block.nodeType !== 1 || block.matches("[data-doc-object]") || block.querySelector("[data-doc-object]")) throw new Error("DOCUMENT_SURFACE_INVALID_ANCHOR");
      const text = block.textContent;
      if (!Number.isInteger(anchor.start) || !Number.isInteger(anchor.end) || anchor.start < 0 || anchor.end < anchor.start || anchor.end > text.length || text.slice(anchor.start, anchor.end) !== anchor.text) throw new Error("DOCUMENT_SURFACE_INVALID_ANCHOR");
      function locate(offset, forward = false) {
        const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) { const n = walker.currentNode; if (offset < n.length || (!forward && offset === n.length)) return [n, offset]; offset -= n.length; }
        return [block, block.childNodes.length];
      }
      const range = document.createRange(); const start = locate(anchor.start, anchor.start !== anchor.end); range.setStart(...start); range.setEnd(...(anchor.start === anchor.end ? start : locate(anchor.end))); return range;
    }
    function mutate(request) {
      alive(); commit();
      if (request?.expectedRevision !== revision) throw new Error("DOCUMENT_SURFACE_REVISION_CONFLICT");
      if (request.operation === "replace-document") setHtml(cleanHtml(document, request.html));
      else if (request.operation === "append") {
        const t = document.createElement("template"); t.innerHTML = cleanHtml(document, request.html);
        pagination.getDocumentPageContents().at(-1).append(t.content); objects.hydrateAll(documentCanvas);
      } else if (request.operation === "replace-selection") {
        const range = rangeFor(request.anchor); clearOwnSelection(); range.deleteContents();
        range.insertNode(document.createTextNode(String(request.text ?? "")));
      } else throw new Error("DOCUMENT_SURFACE_UNKNOWN_OPERATION");
      commit(); schedule(); return snapshot();
    }
    // Loading another document is not an undoable edit of the previous one.
    // Refuse markup that would be silently stripped by the supported importer.
    function validateImport(html) {
      alive();if(composing)throw Error("DOCUMENT_SURFACE_COMPOSITION_ACTIVE");
      const original=document.createElement("template"); original.innerHTML=String(html??"");
      for(const node of original.content.querySelectorAll('[data-doc-object="math"]')) {
        const expectedClass=`document-object document-math-object document-math-${node.dataset.docObjectLayout}`;
        const latex=node.dataset.latex||"";
        const expectedText=node.dataset.docObjectLayout==="paragraph"?`\\[${latex}\\]`:`\\(${latex}\\)`;
        if(!["SPAN","FIGURE"].includes(node.tagName) || node.children.length || ![latex,expectedText].includes(node.textContent))throw Error("DOCUMENT_SURFACE_IMPORT_UNSUPPORTED");
        if(node.getAttribute("class")===expectedClass)node.removeAttribute("class");
        if(node.getAttribute("contenteditable")==="false")node.removeAttribute("contenteditable");
        if(node.getAttribute("draggable")==="true")node.removeAttribute("draggable");
        node.textContent=latex;
      }
      const cleaned=cleanHtml(document,html);
      if(original.innerHTML!==cleaned)throw Error("DOCUMENT_SURFACE_IMPORT_UNSUPPORTED");
      return cleaned;
    }
    function loadDocument(request) {
      alive(); if(composing)throw Error("DOCUMENT_SURFACE_COMPOSITION_ACTIVE"); commit();
      if(request?.expectedRevision!==revision)throw Error("DOCUMENT_SURFACE_REVISION_CONFLICT");
      const html=validateImport(request.html);
      pagination.cancelPagination(); setHtml(html); undo.length=0; redo.length=0;
      current=pagination.getDocumentEditorHtml(); revision++;
      setLayout(request.layout||engine.defaultDocumentLayoutState(),false);
      notify("content");notify("layout");return snapshot();
    }
    function getOutline(){ alive(); return [...documentCanvas.querySelectorAll(".mc-page-content :is(h1,h2,h3,h4,h5,h6)")].map((h,index)=>({index,level:Number(h.tagName[1]),text:h.textContent.trim()})); }
    function navigateHeading(index){ alive(); const h=documentCanvas.querySelectorAll(".mc-page-content :is(h1,h2,h3,h4,h5,h6)")[index]; if(!h)return false; const r=h.getBoundingClientRect(), c=documentCanvas.getBoundingClientRect(); documentCanvas.scrollTop+=r.top-c.top-16; return true; }
    function getExportPages(){
      alive();if(composing)throw Error("DOCUMENT_SURFACE_COMPOSITION_ACTIVE");commit();
      return pagination.getDocumentPageContents().map(page=>{
        const copy=page.cloneNode(true);objects.prepareForSerialization(copy);
        validateImport(copy.innerHTML);return {contentHtml:copy.innerHTML,overlayHtml:""};
      });
    }
    function travel(from, to) {
      alive(); commit(); if (!from.length) return snapshot();
      to.push(current); const next = from.pop(); setHtml(next); current = pagination.getDocumentEditorHtml(); revision++; notify("content"); schedule(); return snapshot();
    }
    function format(command, value) {
      alive(); if (!ownsSelection()) return false;
      if (!["bold", "italic", "underline", "strikeThrough", "insertUnorderedList", "insertOrderedList", "removeFormat", "formatBlock"].includes(command)) throw new Error("DOCUMENT_SURFACE_FORMAT_UNSUPPORTED");
      if (command === "formatBlock") { if (!window.getSelection().isCollapsed) return false; pagination.applyDocumentBlockFormat(value); return true; }
      const result = document.execCommand(command, false, null); commit(); schedule(); return result;
    }
    function insertMath(latex, layout = "inline") {
      alive(); if (layout === "inline" && !ownsSelection()) return false;
      const ok = layout === "paragraph" ? objects.insertParagraphMath(String(latex)) : objects.insertInlineMath(String(latex));
      if (ok) { objects.hydrateAll(documentCanvas); commit(); schedule(); } return ok;
    }
    // Native input, custom Enter/Backspace, IME, paste and history stay instance-local.
    documentCanvas.addEventListener("input", () => { if (!composing) { objects.hydrateAll(documentCanvas); commit(); schedule(); } }, eventOptions);
    documentCanvas.addEventListener("compositionstart", () => { composing = true; pagination.cancelPagination(); }, eventOptions);
    documentCanvas.addEventListener("compositionend", () => { composing = false; commit(); schedule(); }, eventOptions);
    documentCanvas.addEventListener("keydown", event => {
      if (composing || event.isComposing) return;
      if (ownsSelection() && !window.getSelection().isCollapsed && event.key === "Enter" && !selection()) { event.preventDefault(); return; }
      if ((event.ctrlKey || event.metaKey) && !event.altKey && ["z", "y"].includes(event.key.toLowerCase())) {
        event.preventDefault(); (event.shiftKey || event.key.toLowerCase() === "y") ? travel(redo, undo) : travel(undo, redo); return;
      }
      if (!objects.handleObjectKeydown(event)) pagination.handleDocumentEditorKeydown(event);
    }, eventOptions);
    documentCanvas.addEventListener("beforeinput", event => {
      const sel = window.getSelection();
      if (sel?.rangeCount && !sel.isCollapsed && !ownsSelection()) { event.preventDefault(); return; }
      if (event.inputType === "historyUndo" || event.inputType === "historyRedo") { event.preventDefault(); event.inputType === "historyUndo" ? travel(undo, redo) : travel(redo, undo); }
    }, eventOptions);
    documentCanvas.addEventListener("paste", event => {
      event.preventDefault(); if (!ownsSelection()) return;
      const text = event.clipboardData?.getData("text/plain") || "";
      document.execCommand("insertText", false, text); commit(); schedule();
    }, eventOptions);
    documentCanvas.addEventListener("drop", event => event.preventDefault(), eventOptions);
    document.addEventListener("selectionchange", () => { if (pending && !composing && (!ownsSelection() || window.getSelection().isCollapsed)) { pending = false; schedule(); } }, eventOptions);
    objects.wireEvents();
    setHtml(cleanHtml(document, props.html || "<p><br></p>")); current = pagination.getDocumentEditorHtml();
    setLayout(documentSession.layoutState, false);
    const resize = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (!destroyed) setLayout(documentSession.layoutState, false); }) : null;
    resize?.observe(documentCanvas);
    if(props.toolbar===true){
      const bar=document.createElement("div");bar.className="mcel-document-surface-toolbar";bar.setAttribute("role","group");bar.setAttribute("aria-label","Document editing controls");
      const status=document.createElement("span");status.setAttribute("role","status");
      function button(label,fn){const b=document.createElement("button");b.type="button";b.textContent=label;
        b.addEventListener("pointerdown",e=>e.preventDefault(),eventOptions);
        b.addEventListener("click",()=>{try{const result=fn();status.textContent=result===false?"Select document text or caret first":"";}catch(e){status.textContent=String(e.message||e);}},eventOptions);bar.append(b);}
      button("Undo",()=>travel(undo,redo));button("Redo",()=>travel(redo,undo));
      button("Zoom out",()=>{const l=snapshot().layout;l.view.zoom=Number(root.style.getPropertyValue("--document-zoom"))-.1;fitWidth=false;setLayout(l);});
      zoomLabel=document.createElement("span"); zoomLabel.setAttribute("aria-label","Document zoom");bar.append(zoomLabel);
      button("Zoom in",()=>{const l=snapshot().layout;l.view.zoom=Number(root.style.getPropertyValue("--document-zoom"))+.1;fitWidth=false;setLayout(l);});
      button("Fit width",()=>{const l=snapshot().layout;fitWidth=true;l.view.zoom=2;setLayout(l);});
      for(const [label,command] of [["Bold","bold"],["Italic","italic"],["Underline","underline"],["Strike","strikeThrough"],["Bullets","insertUnorderedList"],["Numbering","insertOrderedList"],["Clear format","removeFormat"]])button(label,()=>format(command));
      button("Paragraph",()=>format("formatBlock","p"));button("Heading",()=>format("formatBlock","h1"));
      button("Math",()=>{const latex=window.prompt("LaTeX expression");return latex===null?true:insertMath(latex,"paragraph");});
      // A bound mode is controlled by the surrounding MCEL declaration, not a second writer.
      if(!props.modeOwned){button("Paged / endless",()=>{const l=snapshot().layout;l.view.mode=l.view.mode==="paged"?"endless":"paged";setLayout(l);});}
      for(const preset of ["letter","a4","legal"]){button(preset.toUpperCase(),()=>{const l=snapshot().layout;l.layout.mode="preset";l.layout.preset=preset;setLayout(l);});}
      bar.append(status);root.prepend(bar);root.classList.add("mcel-document-surface-with-toolbar");
      const rail=document.createElement("div");rail.className="mcel-document-ruler-rail";rail.setAttribute("aria-label","Horizontal page ruler in inches");
      ruler=document.createElement("div");ruler.className="mcel-document-ruler";rail.append(ruler);root.insertBefore(rail,documentCanvas);
      documentCanvas.addEventListener("scroll",()=>{rail.scrollLeft=documentCanvas.scrollLeft;},eventOptions);
      setLayout(documentSession.layoutState,false);
    }
    const api = Object.freeze({version, elementId, instanceId, root, getState: snapshot, getSelection: selection, mutate, setLayout,validateImport,loadDocument,getExportPages,getOutline,navigateHeading,
      format, insertMath, undo: () => travel(undo, redo), redo: () => travel(redo, undo),
      subscribe(fn) { alive(); if (typeof fn !== "function") throw new TypeError("subscriber must be a function"); subscribers.add(fn); return () => subscribers.delete(fn); },
      destroy() { if (destroyed) return; clearOwnSelection(); destroyed = true; controller.abort(); resize?.disconnect(); objects.unwireEvents(); pagination.cancelPagination(); subscribers.clear(); mounted.delete(host); root.remove(); }});
    mounted.set(host, api);
    return api;
  }
  global.McelDocumentSurfaceElement = Object.freeze({elementId, version, mount});
  global.McelElementRegistry.registerRenderer(elementId, mount);
})(window);
