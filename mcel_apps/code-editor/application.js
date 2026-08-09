"use strict";

// Canonical MCEL DSL authority for the host-bound Code Editor.
// The authored package names the stable route, root selector, and new canonical
// runtime facade. The existing browser shell is not semantic authority.

const mcel = require("@mcel/app");

const APP_ID = "code-editor";
const TITLE = "Code Editor";
const TARGET_TRUTH_STATUS = "semantic-runtime-proven";

function declareCodeEditor(app) {
  app.presentation.hostBound("workspace", {
    route: "/applications/code-editor",
    root: "#code-editor-app",
    presentationAuthority: "existing-host-html",
    runtimeFacade: "MainComputerCodeEditorRuntime",
  });

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
