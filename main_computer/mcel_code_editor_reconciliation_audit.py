"""Patch 4A: source-grounded, non-promoting Code Editor semantic reconciliation.

Reads two reviewed MCEL source authorities and the incumbent host's in-memory
policy projection. No application is mounted, no intent is executed, no route
is changed, and no candidate is staged. Mappings are hypotheses, NOT proofs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_source_authority_shadow import ROOT, evaluate_source_authority

SCHEMA = "mcel.code-editor-reconciliation.v1"

# Explicitly reviewed semantic bridge, not a name-similarity heuristic.
# A bridge relates user intent; it does NOT assert equivalent arguments,
# permissions, effects, receipts, or live behavior.
BRIDGE = {
    "inspect-workspace": ("list-files", "browse-directory"),
    "open-file": ("select-file", "open-source"),
    "edit-draft": (),  # generated document-editor interaction, no incumbent IR intent
    "save-file": ("save-source",),
    "discard-draft": (),  # generated document-editor control, no incumbent IR intent
    "close-file": (),  # generated document-editor control, no incumbent IR intent
    "preview-aider-plan": ("preview-aider-plan",),
    "apply-reviewed-patch": ("prepare-reviewed-change", "apply-reviewed-change"),
}

# Incumbent-only features must not disappear as a side-effect of collapsing
# semantic definitions. These are complete, independently reviewed groups.
EXTRA_GROUPS = {
    "aider-session-and-execution": (
        "aider-context", "aider-jobs", "run-aider", "archive-aider-context",
        "reset-aider-context", "load-aider-archive",
    ),
    "component-documentation-and-viewport": (
        "list-component-docs", "choose-component-doc", "read-component-doc",
        "read-component-doc-viewport-config", "run-component-doc-script",
    ),
    "scm-contract-inspection": (
        "inspect-scm-component-contract", "inspect-scm-route-contract",
    ),
    "source-validation-and-serialization-preview": (
        "check-studio-draft", "preview-studio-serialization",
    ),
    "isolated-studio-runtime": (
        "studio-runtime-mount", "studio-runtime-validate", "studio-runtime-serialize",
        "studio-runtime-repair", "studio-runtime-stage", "studio-runtime-commit",
        "studio-runtime-route-enter", "studio-runtime-route-leave",
        "studio-runtime-evidence", "studio-runtime-replay",
    ),
    "proof-regression-and-repair": (
        "studio-proof-list", "studio-proof-select", "studio-proof-inspect",
        "studio-proof-export", "studio-regression-run", "studio-repair-packet",
    ),
}

# The VS Code screenshot supplied by the user is the *reference* for these
# semantic roles. This is evidence-led observation, not pixel-matching or a
# demand to implement unrelated VS Code features. Statuses are scoped to the
# reviewed source, not asserted as live-browser certification.
WORKSPACE_ROLES = (
    ("activity-rail", "Primary view navigation", "host-bound-present", "semantic-nav-without-activity-rail", "presentation-composition", "code-editor.html: .code-studio-activitybar; semantic application.html: navigation"),
    ("explorer", "Hierarchical source browsing", "host-bound-tree-present", "generated-folder-tree-supported", "presentation-density", "code-editor.html: .code-studio-tree; mcel-semantic-operational-runtime.js: data-mcel-folder-tree"),
    ("document-tabs", "Switch independent open documents", "host-open-editors-present", "generated-document-tabs-present", "reuse-existing", "code-editor.html: .code-studio-open-editors; mcel-semantic-operational-runtime.js: data-mcel-document-tabs"),
    ("primary-editor", "Source editing dominates center", "host-monaco-present", "generated-monaco-present", "presentation-composition", "code-editor.html: #code-studio-runtime-monaco; code-editor-monaco-adapter.js"),
    ("breadcrumbs", "Document location and hierarchy", "host-shell-unverified", "no-explicit-semantic-region", "authoring-or-composition-unverified", "semantic application.html has no breadcrumbs region"),
    ("bottom-panel", "Docked output, problems, jobs, terminal area", "host-proof-dock-present", "no-dedicated-bottom-dock", "presentation-composition", "code-editor.html: #code-studio-bottom-panel; semantic application.html: status"),
    ("secondary-sidebar", "Aider, documentation and inspection", "host-inspector-present", "supporting-regions-not-a-dock", "presentation-composition", "code-editor.html: .code-studio-inspector; semantic application.html: aider/documentation/studio"),
    ("status-bar", "Persistent compact editor and task context", "host-statusbar-present", "activity-receipt-region-not-statusbar", "presentation-composition", "code-editor.html: .code-studio-statusbar; semantic application.html: status"),
    ("resizable-panes", "Persisted geometry and responsive docking", "host-layout-contract-present", "incumbent-layout-plan-disabled", "presentation-planner-integration", "code-editor-layout-contract.js; mcel_semantic_other_apps_host.py: workspace_enabled"),
    ("command-center", "Menu and command discovery", "host-title-command-center-present", "not-declared", "authoring-or-composition-unverified", "code-editor.html: .code-studio-command-center"),
    ("minimap", "Code overview when appropriate", "adapter-disabled", "adapter-disabled", "configurable-option-not-structural-gap", "code-editor-monaco-adapter.js: minimap enabled false"),
    ("integrated-terminal", "Interactive authorized shell or task console", "not-established", "sandboxed-script-not-terminal", "missing-or-unverified-capability", "run-component-doc-script is sandboxed viewport, not interactive shell"),
)


def _filehash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diff_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Report precise host-only IR mutations without assuming their purpose."""
    if type(before) is not type(after):
        return [prefix or "$"]
    if isinstance(before, dict):
        return [p for k in sorted(before.keys() | after.keys())
                for p in (_diff_paths(before[k], after[k], f"{prefix}.{k}")
                          if k in before and k in after else [f"{prefix}.{k}"])]
    if isinstance(before, list):
        if len(before) != len(after):
            return [prefix + ".length"]
        return [p for i, (b, a) in enumerate(zip(before, after))
                for p in _diff_paths(b, a, f"{prefix}[{i}]")]
    return [] if before == after else [prefix or "$"]


def _effect_authority(ir: dict, intent: dict) -> str | None:
    refs = intent.get("effectRefs") or []
    effects = {e["id"]: e for e in ir.get("effects", [])}
    authorities = {effects.get(r.get("ref"), {}).get("authority", {}).get("ref") for r in refs}
    authorities.discard(None)
    return next(iter(authorities)) if len(authorities) == 1 else None


def _intent_details(ir: dict, intent_id: str) -> dict:
    lookup = {i["id"].removeprefix("intent:").removeprefix("code-editor."): i for i in ir["intents"]}
    item = lookup[intent_id]
    authority = _effect_authority(ir, item)
    cap = next((c for c in ir.get("capabilities", []) if c["id"] == authority), {})
    effects = {e["id"]: e for e in ir.get("effects", [])}
    return {
        "id": item["id"],
        "operationKind": item.get("operationKind"),
        "risk": item.get("risk"),
        "operation": item.get("operation"),
        "runtimeMethod": item.get("runtimeMethod"),
        "capability": authority,
        "capabilityRisk": cap.get("risk"),
        "effectIds": [r["ref"] for r in item.get("effectRefs", [])],
        "effects": [
            {"id": r["ref"], "effectKind": effects.get(r["ref"], {}).get("effectKind"),
             "risk": effects.get(r["ref"], {}).get("risk")}
            for r in item.get("effectRefs", [])
        ],
        "input": [{"name": inp.get("sourceName"), "schema": inp.get("schema")} for inp in item.get("input", [])],
        "requestArguments": item.get("requestArguments", []),
        "reads": [x.get("ref") for x in item.get("reads", [])],
        "resultBindings": item.get("resultBindings", []),
        "invariants": [x.get("ref") for x in item.get("invariants", [])],
        "outcomes": item.get("outcomes", []),
        "typedOperation": item.get("typedOperation"),
    }


def _node_links(nodes: list[dict]) -> dict[str, list[str]]:
    """Find surface exposure structurally; do not infer from matching labels."""
    links: dict[str, set[str]] = {}
    def visit(value: Any, path: str, name: str = "") -> None:
        if isinstance(value, dict):
            if name.lower().endswith("intent") and isinstance(value.get("ref"), str):
                links.setdefault(value["ref"], set()).add(path)
            for k, v in value.items():
                visit(v, path, k)
        elif isinstance(value, list):
            for v in value:
                visit(v, path, name)
    for node in nodes:
        visit(node, node.get("id", "unknown"))
    return {key: sorted(value) for key, value in links.items()}


def _check_snapshot_tokens(repo: Path) -> dict:
    """A small set of implementation witnesses; never substitutes for browser checks."""
    files = {
        "existingWorkbench": repo / "main_computer/web/applications/apps/code-editor.html",
        "existingDockController": repo / "main_computer/web/applications/scripts/code-editor-layout-contract.js",
        "generatedRuntime": repo / "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js",
        "monacoAdapter": repo / "main_computer/web/applications/scripts/code-editor-monaco-adapter.js",
        "semanticHost": repo / "main_computer/mcel_semantic_other_apps_host.py",
    }
    tokens = {
        "existingWorkbench": ("code-studio-activitybar", "code-studio-inspector", "code-studio-bottom-panel", "code-studio-statusbar"),
        "existingDockController": ("data-code-editor-layout-splitter", "tab-with"),
        "generatedRuntime": ("data-mcel-document-tabs", "data-mcel-folder-tree", "document-editor"),
        "monacoAdapter": ("minimap: {enabled: false}",),
        "semanticHost": ('workspace_enabled = name in {"task-manager", "file-manager"}',),
    }
    return {name: {"path": path.relative_to(repo).as_posix(),
                   "exists": path.is_file(),
                   "witnesses": {token: token in path.read_text(encoding="utf-8") if path.is_file() else False
                                 for token in tokens[name]}}
            for name, path in files.items()}


def evaluate_reconciliation(repo: Path = ROOT) -> dict[str, Any]:
    """Return a complete, deterministic read-only pre-migration audit."""
    repo = Path(repo).resolve()
    prior = evaluate_source_authority("code-editor", repo)
    canonical_path = repo / "mcel_apps/code-editor/application.js"
    incumbent_path = repo / "tools/fixtures/mcel_semantic_other_apps/code-editor/application.js"
    c_report = compile_dsl_application(canonical_path, write_candidate=False)
    i_report = compile_dsl_application(incumbent_path, write_candidate=False)
    if not (c_report.valid and i_report.valid):
        raise ValueError("Both compiler inputs must validate before intent reconciliation")
    c_ir, i_ir = c_report.normalized_ir, i_report.normalized_ir
    assert c_ir is not None and i_ir is not None
    host_ir = compile_semantic_app("code-editor", repo)
    c_ids = {i["id"].removeprefix("intent:code-editor.") for i in c_ir["intents"]}
    i_ids = {i["id"].split(":", 1)[1] for i in i_ir["intents"]}
    if c_ids != set(BRIDGE):
        raise ValueError("Canonical intent inventory drift: review semantic bridge")
    used = set()
    bridges = []
    for name, targets in BRIDGE.items():
        if any(t not in i_ids for t in targets):
            raise ValueError("Incumbent intent inventory drift: review semantic bridge")
        if used.intersection(targets):
            raise ValueError("Ambiguous bridge: incumbent mapped to multiple canonical intents")
        used.update(targets)
        status = ("runtime-generated-interaction" if not targets else
                  "multi-operation-candidate" if len(targets) > 1 else
                  "semantic-overlap-unverified")
        bridges.append({
            "canonical": _intent_details(c_ir, name),
            "incumbent": [_intent_details(i_ir, t) for t in targets],
            "classification": status,
            "behaviorallyEquivalent": False,
            "note": ("Source, draft and document controls are generated by the incumbent document-editor runtime; "
                     "there is no one-to-one incumbent intent" if not targets else
                     "Similar user goal, but no proven equality of arguments, authorization, effects, or receipts"),
        })
    remaining = i_ids - used
    grouped = {key: [_intent_details(i_ir, name) for name in names] for key, names in EXTRA_GROUPS.items()}
    all_grouped = [item["id"].split(":", 1)[1] for arr in grouped.values() for item in arr]
    if len(all_grouped) != len(set(all_grouped)) or set(all_grouped) != remaining:
        raise ValueError("Unaccounted incumbent intent inventory: update reviewed groups")
    host_changes = _diff_paths(i_ir, host_ir)
    expected_host_policy = len(host_changes) == 1 and host_changes[0].endswith(".externalJob")
    surface_links = _node_links(i_ir["surfaces"][0].get("nodes", []))
    capabilities_c = {x["id"]: x for x in c_ir["capabilities"]}
    capabilities_i = {x["id"]: x for x in i_ir["capabilities"]}
    role_data = [dict(zip(("id", "vscodeRole", "canonicalEvidence", "incumbentEvidence", "gapClassification", "repositoryWitness"), row))
                 for row in WORKSPACE_ROLES]
    probes = _check_snapshot_tokens(repo)
    return {
        "schema": SCHEMA,
        "mode": "offline-read-only-no-promotion",
        "reference": {
            "vscode": "User-supplied VS Code workbench screenshot (2026-11-19)",
            "policy": "Map semantic relationships and user tasks, not colors or pixel geometry",
            "codeEditorScreenshot": "User-supplied MCEL semantic Code Editor screenshot",
        },
        "sourceFiles": {"canonical": {"path": canonical_path.relative_to(repo).as_posix(), "sha256": _filehash(canonical_path)},
                        "incumbent": {"path": incumbent_path.relative_to(repo).as_posix(), "sha256": _filehash(incumbent_path)}},
        "shadowOutcome": prior["outcome"],
        "sourceAuthority": prior["sourceResolution"],
        "compiler": {"canonical": prior["canonicalCompile"], "incumbent": prior["incumbentCompile"]},
        "hostProjection": {
            "sourceVsHostChangedPaths": host_changes,
            "onlyExpectedExternalJobAttachment": expected_host_policy,
            "explanation": "Patch 3 comparison uses post-host IR; incumbentCompile fingerprints use raw compiled source. "
                           "They are different comparands, not evidence of source-file mutation.",
        },
        "functionalReconciliation": {
            "canonicalIntents": len(c_ids), "incumbentIntents": len(i_ids),
            "bridgeMappedIncumbentIntents": len(used), "incumbentAdditionalIntents": len(remaining),
            "bridge": bridges, "incumbentAdditionalGroups": grouped,
            "coverageComplete": len(used) + len(remaining) == len(i_ids),
            "incumbentSurfaceIntentReferences": surface_links,
            "canonicalCapabilityRisks": {k: v.get("risk") for k, v in capabilities_c.items()},
            "incumbentCapabilityRisks": {k: v.get("risk") for k, v in capabilities_i.items()},
            "authorityWarning": "Canonical source-workspace and reviewed-patch-application capability risks are external-read "
                                "while associated write intents are source-write. Reconcile capability/intent authority "
                                "before migration; compile PASS is not live authorization proof.",
        },
        "vscodeSemanticWorkspace": {
            "roles": role_data, "witnesses": probes,
            "presentationPlannerEnabledForIncumbentCodeEditor": False,
            "appearanceRule": "Retain MCEL Theme/Form/Chrome as authority; VS Code supplies semantic workspace roles only",
        },
        "interventionOrder": [
            "Reconcile required operations, their arguments and host authority before merging source definitions",
            "Adopt one canonical paired HTML+JS source only after operation/receipt equivalence is established",
            "Express workbench roles using existing layout/renderer capabilities; test composition gaps before adding primitives",
            "Prove browser state, document tabs, writes, Aider, SCM, and visual geometry on multiple viewports",
            "Promote production route only through an explicit separate acceptance decision",
        ],
        "promotion": {"eligible": False, "performed": False,
                      "blockers": sorted(set(prior["promotion"]["blockers"] + [
                          "INTENT_ARGUMENT_AUTHORITY_EQUIVALENCE_UNVERIFIED",
                          "VS_CODE_WORKSPACE_SEMANTIC_PARITY_UNVERIFIED",
                          "INCUMBENT_EXTRA_OPERATIONS_NOT_RECONCILED",
                      ]))},
        "noninterference": {"productionRouteChanged": False, "runtimeMounted": False,
                            "capabilityInvoked": False, "candidateWritten": False,
                            "sourceFilesMutated": False},
    }
