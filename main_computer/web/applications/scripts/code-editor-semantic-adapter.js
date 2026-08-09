(() => {
  (function retireCodeEditorSemanticAdapter(global) {
    "use strict";

    const legacyAdapter = Object.freeze({
      schema: "main-computer-retired-code-editor-semantic-adapter-v1",
      appId: "code-editor",
      retired: true,
      authority: "MainComputerCodeEditorRuntime",
      reason: "Code Editor is now authored by the MCEL DSL package and implemented by the canonical runtime facade.",
      runtimeFacade: "MainComputerCodeEditorRuntime"
    });

    if (global) {
      global.MainComputerCodeEditorSemanticAdapter = legacyAdapter;
    }

    if (typeof module === "object" && module.exports) {
      module.exports = legacyAdapter;
    }
  })(typeof globalThis !== "undefined" ? globalThis : this);
})();
