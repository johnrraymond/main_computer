"use strict";

// Canonical MCEL DSL authority for the host-bound Code Editor.
// The authored package names the stable route, root selector, and new canonical
// runtime facade. The existing browser shell is not semantic authority.

const mcel = require("@mcel/app");

const APP_ID = "code-editor";
const TITLE = "Code Editor";
const TARGET_TRUTH_STATUS = "semantic-runtime-proven";

const CODE_EDITOR_SEMANTIC_SURFACE = {
  id: "code-editor.semantic-surface.legacy-fidelity",
  surfaceId: "code-editor.surface.monaco-selected-file-editor",
  presentationAuthority: "existing-host-html",
  runtimeFacade: "MainComputerCodeEditorRuntime",
  regions: [
    {
      id: "root",
      role: "application-root",
      selector: "#code-editor-app"
    },
    {
      id: "shell",
      role: "legacy-fidelity-shell",
      selector: ".code-studio-shell"
    },
    {
      id: "titlebar",
      role: "workspace-toolbar",
      selector: ".code-studio-titlebar"
    },
    {
      id: "activitybar",
      role: "navigation",
      selector: ".code-studio-activitybar"
    },
    {
      id: "explorer",
      role: "source-tree",
      selector: ".code-studio-sidebar"
    },
    {
      id: "open-editors",
      role: "open-editors-list",
      selector: "[data-mc-component-id='code-editor.explorer.open-editors']"
    },
    {
      id: "editor-group",
      role: "editor-workbench-group",
      selector: ".code-studio-editor-group"
    },
    {
      id: "primary-editor",
      role: "primary-authoring-surface",
      selector: "#code-studio-runtime-preview",
      runtimeHostSelector: "#code-studio-runtime-monaco",
      primary: true,
      surface: "monaco-selected-file-editor"
    },
    {
      id: "assistant",
      role: "secondary-assistant",
      selector: ".code-studio-inspector"
    },
    {
      id: "proof-dock",
      role: "optional-proof-tools",
      selector: "#code-studio-bottom-panel, .code-studio-proof-dock",
      defaultVisible: false
    },
    {
      id: "diagnostics",
      role: "runtime-diagnostics",
      selector: "#code-studio-runtime-state, #code-studio-top-gate-status, #code-editor-monaco-status"
    }
  ],
  controls: [
    {
      id: "inspect-workspace",
      selector: "#file-map-refresh",
      intent: "inspectWorkspace"
    },
    {
      id: "open-source-file",
      selector: "[data-code-studio-file], #file-map-apply",
      intent: "openFile"
    },
    {
      id: "edit-draft",
      selector: "#code-studio-runtime-preview, #code-studio-source-editor",
      runtimeHostSelector: "#code-studio-runtime-monaco",
      intent: "editDraft"
    },
    {
      id: "save-file",
      selector: "#code-studio-commit-runtime, #code-studio-save-live-workspace",
      intent: "saveFile"
    },
    {
      id: "discard-draft",
      selector: "#code-studio-restore-live-workspace",
      intent: "discardDraft"
    },
    {
      id: "close-file",
      selector: "#code-studio-clear-live-workspace",
      intent: "closeFile"
    },
    {
      id: "preview-aider-plan",
      selector: "#aider-preview",
      intent: "previewAiderPlan"
    },
    {
      id: "apply-reviewed-patch",
      selector: "#aider-run",
      intent: "applyReviewedPatch"
    }
  ],
  forbiddenDefaultRegions: [
    {
      id: "source-pane",
      selector: "[data-code-studio-pane='source']",
      defaultVisible: false
    },
    {
      id: "serialized-pane",
      selector: "[data-code-studio-pane='serialized']",
      defaultVisible: false
    },
    {
      id: "contract-pane",
      selector: "[data-code-studio-pane='contract']",
      defaultVisible: false
    },
    {
      id: "proof-dock",
      selector: "#code-studio-bottom-panel, .code-studio-proof-dock",
      defaultVisible: false
    }
  ]
};

const CODE_EDITOR_LAYOUT_GRAMMAR = {
  id: "code-editor.layout.legacy-fidelity-workbench",
  rootSelector: ".code-studio-shell",
  presentationAuthority: "existing-host-html",
  regions: [
    {
      id: "shell",
      selector: ".code-studio-shell",
      layout: "grid",
      children: ["titlebar", "workbench", "proof", "statusbar"]
    },
    {
      id: "titlebar",
      selector: ".code-studio-titlebar",
      gridArea: "titlebar"
    },
    {
      id: "workbench",
      selector: ".code-studio-body",
      gridArea: "workbench",
      layout: "grid",
      children: ["activitybar", "explorer", "editor-group", "assistant"]
    },
    {
      id: "activitybar",
      selector: ".code-studio-activitybar",
      gridArea: "activitybar"
    },
    {
      id: "explorer",
      selector: ".code-studio-sidebar",
      gridArea: "sidebar"
    },
    {
      id: "editor-group",
      selector: ".code-studio-editor-group",
      gridArea: "editor",
      children: ["primary-editor"]
    },
    {
      id: "primary-editor",
      selector: "#code-studio-runtime-preview",
      runtimeHostSelector: "#code-studio-runtime-monaco",
      gridArea: "editor"
    },
    {
      id: "assistant",
      selector: ".code-studio-inspector",
      gridArea: "inspector"
    },
    {
      id: "proof",
      selector: "#code-studio-bottom-panel, .code-studio-proof-dock",
      gridArea: "proof",
      defaultVisible: false
    },
    {
      id: "statusbar",
      selector: ".code-studio-statusbar",
      gridArea: "statusbar"
    }
  ],
  constraints: [
    {
      id: "shell-single-column",
      selector: ".code-studio-shell",
      rule: "single-nonzero-explicit-column"
    },
    {
      id: "shell-direct-children-pinned",
      selector: ".code-studio-shell",
      rule: "direct-children-remain-in-declared-shell-rows"
    },
    {
      id: "workbench-nonzero-tracks",
      selector: ".code-studio-body",
      rule: "no-zero-width-implicit-tracks"
    },
    {
      id: "primary-editor-nonzero",
      selector: "#code-studio-runtime-preview",
      runtimeHostSelector: "#code-studio-runtime-monaco",
      minWidth: 360,
      minHeight: {
        default: 320,
        compactViewport: 240
      }
    },
    {
      id: "proof-dock-hidden-by-default",
      selector: "#code-studio-bottom-panel, .code-studio-proof-dock",
      defaultVisible: false
    },
    {
      id: "source-contract-panes-hidden-by-default",
      selector: "[data-code-studio-pane='source'], [data-code-studio-pane='serialized'], [data-code-studio-pane='contract']",
      defaultVisible: false
    }
  ]
};


function declareCodeEditor(app) {
  app.presentation.hostBound("workspace", {
    route: "/applications/code-editor",
    root: "#code-editor-app",
    presentationAuthority: "existing-host-html",
    runtimeFacade: "MainComputerCodeEditorRuntime",
  });
  app.presentation.semanticSurface(CODE_EDITOR_SEMANTIC_SURFACE);
  app.layout.grammar(CODE_EDITOR_LAYOUT_GRAMMAR);

  app.state.rendererLocal("workspace", app.field.record(), {
    initial: {id: "", title: "", root: "", files: []},
  });
  app.state.rendererLocal("file-tree", app.field.record(), {
    initial: {files: [], selectedPath: ""},
  });
  app.state.rendererLocal("active-file", app.field.record(), {
    initial: {path: "", content: "", language: "", sourceHash: ""},
  });
  app.state.rendererLocal("dirty-draft", app.field.record(), {
    initial: {path: "", text: "", dirty: false, baseHash: ""},
  });
  app.state.rendererLocal("aider-plan", app.field.record(), {
    initial: {status: "idle", instruction: "", selectedFiles: []},
  });
  app.state.derived("reviewed-patch", app.field.record(), {
    initial: {status: "none", files: [], transactionHandle: ""},
  });
  app.state.derived("operation-receipt", app.field.record(), {
    initial: {status: "idle", intent: "", mutationAllowed: false},
  });
  app.state.derived("execution-policy", app.field.record(), {
    initial: {commandExecution: "prohibited", hiddenMutation: "blocked"},
  });

  const sourceWorkspace = app.capability
    .external("source-workspace", {
      sourceName: "sourceWorkspace",
      description: "Inspect, read, and explicitly save author-owned source files through the host repository APIs.",
    })
    .operation("inspect", "inspectWorkspace")
    .operation("open", "openFile")
    .operation("save", "saveFile");

  const aiderPlanning = app.capability
    .external("aider-planning", {
      sourceName: "aiderPlanning",
      description: "Preview bounded Aider plans without granting write authority.",
    })
    .operation("preview", "previewAiderPlan");

  const reviewedPatchApplication = app.capability
    .external("reviewed-patch-application", {
      sourceName: "reviewedPatchApplication",
      description: "Apply only reviewed patch evidence through an explicit mutation receipt boundary.",
    })
    .operation("apply", "applyReviewedPatch");

  app.intent.capabilityRequest("inspect-workspace", sourceWorkspace, {
    sourceName: "inspectWorkspace",
    runtimeMethod: "inspectWorkspace",
    binding: "inspect-workspace",
    label: "Inspect source workspace",
    lane: "source-inspection",
    reads: ["workspace", "file-tree"],
    risk: "read-only",
  });
  app.intent.capabilityRequest("open-file", sourceWorkspace, {
    sourceName: "openFile",
    runtimeMethod: "openFile",
    binding: "open-file",
    label: "Open an author-owned source file",
    lane: "source-selection",
    reads: ["workspace", "file-tree", "active-file"],
    risk: "read-only",
    invariants: ["workspace-path-contained"],
  });
  app.intent.interaction("edit-draft", {
    sourceName: "editDraft",
    runtimeMethod: "editDraft",
    binding: "edit-draft",
    label: "Edit a local source draft",
    lane: "local-draft",
    reads: ["active-file", "dirty-draft"],
    risk: "local-state",
  });
  app.intent.capabilityRequest("save-file", sourceWorkspace, {
    sourceName: "saveFile",
    runtimeMethod: "saveFile",
    binding: "save-file",
    label: "Save an explicit source file draft",
    lane: "source-write",
    reads: ["active-file", "dirty-draft", "operation-receipt"],
    risk: "source-write",
    invariants: ["explicit-save-before-source-write", "workspace-path-contained"],
  });
  app.intent.interaction("discard-draft", {
    sourceName: "discardDraft",
    runtimeMethod: "discardDraft",
    binding: "discard-draft",
    label: "Discard local draft changes",
    lane: "local-draft",
    reads: ["active-file", "dirty-draft"],
    risk: "local-state",
  });
  app.intent.interaction("close-file", {
    sourceName: "closeFile",
    runtimeMethod: "closeFile",
    binding: "close-file",
    label: "Close the active source file",
    lane: "local-selection",
    reads: ["active-file", "dirty-draft"],
    risk: "local-state",
  });
  app.intent.capabilityRequest("preview-aider-plan", aiderPlanning, {
    sourceName: "previewAiderPlan",
    runtimeMethod: "previewAiderPlan",
    binding: "preview-aider-plan",
    label: "Preview an Aider edit plan",
    lane: "aider-preview",
    reads: ["workspace", "active-file", "dirty-draft", "aider-plan"],
    risk: "external-read",
    invariants: ["aider-preview-is-read-only"],
  });
  app.intent.capabilityRequest("apply-reviewed-patch", reviewedPatchApplication, {
    sourceName: "applyReviewedPatch",
    runtimeMethod: "applyReviewedPatch",
    binding: "apply-reviewed-patch",
    label: "Apply reviewed patch evidence",
    lane: "reviewed-source-write",
    reads: ["workspace", "reviewed-patch", "operation-receipt"],
    risk: "source-write",
    invariants: ["reviewed-patch-before-apply", "workspace-path-contained"],
  });

  app.invariant.semantic("workspace-path-contained", {
    label: "Workspace paths remain contained",
    description: "File operations must target repository-contained author-owned source paths.",
    examples: ["src/app.js is accepted after workspace evidence", "../secrets.txt is refused"],
  });
  app.invariant.semantic("explicit-save-before-source-write", {
    label: "Explicit save before source write",
    description: "Saving requires user intent, stale-source checks, and a classified operation receipt.",
    examples: ["dirty draft plus explicit save produces a receipt", "implicit localStorage persistence is not source authority"],
  });
  app.invariant.semantic("aider-preview-is-read-only", {
    label: "Aider preview is read-only",
    description: "Aider planning may propose edits but cannot mutate source without later reviewed patch evidence.",
    examples: ["preview returns a plan", "preview cannot apply a patch"],
  });
  app.invariant.semantic("reviewed-patch-before-apply", {
    label: "Reviewed patch before apply",
    description: "Patch application requires reviewed evidence, approval, recovery information, and a mutation receipt.",
    examples: ["reviewed transaction handle may apply", "unreviewed patch text is refused"],
  });

  app.scenario.example("inspect-workspace-happy-path", {
    label: "Inspect workspace happy path",
    intent: "inspect-workspace",
    given: {workspaceRoot: "main_computer_test"},
    expect: {ok: true, filesVisible: true},
  });
  app.scenario.example("edit-draft-local-dirty-state", {
    label: "Edit draft local dirty state",
    intent: "edit-draft",
    given: {path: "src/app.js", textChanged: true},
    expect: {dirty: true, mutationAllowed: false},
  });
  app.scenario.example("save-file-explicit-receipt", {
    label: "Save file explicit receipt",
    intent: "save-file",
    given: {explicitSave: true, staleSourceChecked: true},
    expect: {receipt: "source-write", mutationAllowed: true},
  });
  app.scenario.example("preview-aider-read-only", {
    label: "Preview Aider read-only",
    intent: "preview-aider-plan",
    given: {instruction: "Plan the smallest safe edit"},
    expect: {mutationAllowed: false},
  });
  app.scenario.example("apply-reviewed-patch-receipt", {
    label: "Apply reviewed patch receipt",
    intent: "apply-reviewed-patch",
    given: {reviewed: true, approved: true, recoveryPath: "transaction receipt"},
    expect: {receipt: "reviewed-source-write", mutationAllowed: true},
  });

  app.layout.zones(["workspace", "file-tree", "editor", "draft-controls", "aider", "review", "receipts", "status"]);
  app.proof.semanticRuntimeProven({
    requiredAuthorities: [
      "visible-surface",
      "operation-receipt",
      "capability-response",
      "reviewed-patch-evidence",
    ],
  });
}


module.exports = mcel.defineApp(
  {
    id: APP_ID,
    title: TITLE,
    semanticVersion: "1",
    targetTruthStatus: TARGET_TRUTH_STATUS,
  },
  ({application}) => application.hostBound(declareCodeEditor)
);
