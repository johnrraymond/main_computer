    const documentObjectRuntime = McelDocumentEngine.createObjects({
      documentCanvas, documentPage, documentEditor, documentSession, documentPluginMarkers, documentStatus,
      getActiveDocumentEditor, documentBlockForRange, saveDocumentDraft: () => saveDocumentDraft(),
      scheduleDocumentRepagination, storage: {getItem: key => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value)}, globalResize: true
    });
    function hydrateDocumentObjects(root = documentCanvas) {
      documentObjectRuntime.hydrateAll(root);
    }
    function prepareDocumentObjectsForSerialization(root) {
      documentObjectRuntime.prepareForSerialization(root);
      return root;
    }
    function renderDocumentPluginRail() {
      documentObjectRuntime.renderPluginRail();
    }
    function insertDocumentMathObject(layout = "inline") {
      documentObjectRuntime.insertMath(layout);
    }
    function toggleSelectedDocumentMathLayout() {
      documentObjectRuntime.toggleMathLayout();
    }
    function createHiddenDocumentPlugin() {
      documentObjectRuntime.createHiddenPlugin();
    }
