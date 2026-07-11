from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "main_computer" / "flog_layout_snapshot_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("flog_layout_snapshot_smoke", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_synthetic_hierarchies_are_mcel_like_and_focus_declared():
    module = load_module()

    hierarchies = module.synthetic_hierarchies()

    assert len(hierarchies) == 11
    for hierarchy in hierarchies:
        slots = {node["slot"] for node in hierarchy["nodes"]}
        contract = hierarchy["roleContract"]
        assert hierarchy["focusSlot"] in slots
        assert hierarchy["sourceApp"]
        assert hierarchy["minFocusShare"] < hierarchy["desiredFocusShare"] < hierarchy["maxFocusShare"]
        assert contract["focus"]["slot"] == hierarchy["focusSlot"]
        assert contract["requiredCompanions"]
        assert contract["forbiddenDefaultHidden"]
        assert contract["preferredFamilies"]
        assert set(contract["requiredCompanions"]).issubset(slots)
        assert set(contract["nearbyCompanions"]).issubset(slots)
        assert set(contract["deferableSlots"]).issubset(slots)
        assert any(node["priority"] == "primary" for node in hierarchy["nodes"])
        assert any(node["slot"] == hierarchy["focusSlot"] for node in hierarchy["nodes"])
        assert all(node.get("connects") for node in hierarchy["nodes"])
        assert all(node.get("role") for node in hierarchy["nodes"])
        assert all(node.get("visibility") for node in hierarchy["nodes"])

        focus_node = next(node for node in hierarchy["nodes"] if node["slot"] == hierarchy["focusSlot"])
        assert len(focus_node["items"]) >= 6
        assert any(item["kind"] == "surface" for item in focus_node["items"])
        assert any(item["kind"] in {"collection", "status", "text"} for item in focus_node["items"])


def test_git_tools_is_included_in_flog_battery_as_generic_repository_workflow():
    module = load_module()

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "git-tools-workflow-workbench")
    slots = {node["slot"] for node in hierarchy["nodes"]}
    nodes_by_slot = {node["slot"]: node for node in hierarchy["nodes"]}

    assert hierarchy["sourceApp"] == "git-tools"
    assert hierarchy["rootConcern"] == "repository.workflow"
    assert hierarchy["focusSlot"] == "workflow"
    assert hierarchy["desiredFocusShare"] == 0.64
    assert hierarchy["roleContract"]["requiredCompanions"] == ["project-context", "command", "status"]
    assert hierarchy["roleContract"]["nearbyCompanions"] == []
    assert set(hierarchy["roleContract"]["deferableSlots"]) == {"project-selector", "server", "evidence", "advanced"}
    assert set(hierarchy["roleContract"]["forbiddenDefaultHidden"]) == {"project-context", "workflow", "command", "status"}
    assert {"project-selector", "project-context", "command", "workflow", "server", "status", "evidence", "advanced"}.issubset(slots)

    assert nodes_by_slot["project-selector"]["role"] == "navigation"
    assert nodes_by_slot["project-selector"]["visibility"] == "deferable"
    assert "phase-specific-selector" in nodes_by_slot["project-selector"]["semantics"]["phasePersistence"]
    assert "collapsed-trigger" in nodes_by_slot["project-selector"]["semantics"]["defaultRealization"]
    assert nodes_by_slot["project-context"]["role"] == "status"
    assert "persistent-after-selection" in nodes_by_slot["project-context"]["semantics"]["phasePersistence"]
    assert nodes_by_slot["command"]["role"] == "command"
    assert nodes_by_slot["workflow"]["role"] == "focus"
    assert nodes_by_slot["server"]["role"] == "detail"
    assert nodes_by_slot["status"]["role"] == "status"
    assert nodes_by_slot["evidence"]["role"] == "evidence"
    assert nodes_by_slot["evidence"]["visibility"] == "deferable"
    assert nodes_by_slot["advanced"]["visibility"] == "deferable"

    audit = module.semantic_contract_audit(hierarchy)
    assert audit["state"] == "complete"
    assert audit["missingPrimitives"] == []
    assert audit["presentationSetCount"] >= 3
    assert "repository-workflow" not in str(audit).lower()

    expectations = {item["slot"]: item["expectation"] for item in module.semantic_affordance_expectations(hierarchy)}
    assert expectations["project-selector"] == "phase-selector-trigger"
    assert expectations["command"] == "command-rail"
    assert expectations["workflow"] == "dominant-surface"
    assert expectations["server"] == "deferable-inspector-trigger"
    assert expectations["status"] == "persistent-status-strip"
    assert expectations["evidence"] == "proof-trigger-or-drawer"

    html = module.render_trial_html(hierarchy, "selected-context-workflow", "mcel-realistic")
    assert 'data-mc-source-app="git-tools"' in html
    assert 'data-mc-slot="workflow"' in html
    assert 'data-mc-slot="project-context"' in html
    assert 'data-mc-slot="project-selector"' in html
    assert 'data-mc-navigates="workflow"' in html
    assert 'data-mc-controls="workflow"' in html
    assert 'data-mc-confirms="workflow.state"' in html
    assert 'data-mc-proves="workflow.claim"' in html
    assert "data-mc-app-archetype" not in html
    assert "data-mc-primary-grammar" not in html



def test_worker_marketplace_policy_is_included_as_non_sidebar_workflow():
    module = load_module()

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "worker-marketplace-policy-workbench")
    slots = {node["slot"] for node in hierarchy["nodes"]}
    nodes_by_slot = {node["slot"]: node for node in hierarchy["nodes"]}

    assert hierarchy["sourceApp"] == "worker"
    assert hierarchy["rootConcern"] == "worker.marketplace-policy"
    assert hierarchy["focusSlot"] == "marketplace"
    assert hierarchy["desiredFocusShare"] == 0.64
    assert hierarchy["roleContract"]["requiredCompanions"] == ["hub", "status"]
    assert hierarchy["roleContract"]["nearbyCompanions"] == []
    assert set(hierarchy["roleContract"]["deferableSlots"]) == {"receipts", "guardrails"}
    assert set(hierarchy["roleContract"]["forbiddenDefaultHidden"]) == {"hub", "marketplace", "status"}
    assert {"hub", "marketplace", "status", "receipts", "guardrails"}.issubset(slots)

    assert nodes_by_slot["hub"]["role"] == "command"
    assert nodes_by_slot["marketplace"]["role"] == "focus"
    assert nodes_by_slot["status"]["role"] == "status"
    assert nodes_by_slot["receipts"]["role"] == "evidence"
    assert nodes_by_slot["guardrails"]["role"] == "detail"
    assert nodes_by_slot["receipts"]["visibility"] == "deferable"
    assert nodes_by_slot["guardrails"]["visibility"] == "deferable"

    dangerous = hierarchy["roleContract"]["dangerousFamilies"]
    assert "sectioned-sidebar" in dangerous
    assert "split-pane" in dangerous
    assert "sidebar" in dangerous["sectioned-sidebar"]
    assert {"top-band-dominant-surface", "top-band-focus-overlay", "progressive-workflow"}.issubset(set(hierarchy["roleContract"]["preferredFamilies"]))

    audit = module.semantic_contract_audit(hierarchy)
    assert audit["state"] == "complete"
    assert audit["missingPrimitives"] == []
    assert audit["presentationSetCount"] >= 3

    expectations = {item["slot"]: item["expectation"] for item in module.semantic_affordance_expectations(hierarchy)}
    assert expectations["hub"] == "command-rail"
    assert expectations["marketplace"] == "dominant-surface"
    assert expectations["status"] == "persistent-status-strip"
    assert expectations["receipts"] == "proof-trigger-or-drawer"
    assert expectations["guardrails"] == "deferable-inspector-trigger"

    html = module.render_trial_html(hierarchy, "focus-priority", "mcel-realistic")
    assert 'data-mc-source-app="worker"' in html
    assert 'data-mc-slot="marketplace"' in html
    assert 'data-mc-controls="marketplace"' in html
    assert 'data-mc-confirms="marketplace.state"' in html
    assert 'data-mc-proves="marketplace.claim"' in html
    assert "data-mc-app-archetype" not in html
    assert "data-mc-primary-grammar" not in html

def test_document_page_overlay_workbench_models_real_document_overlay_layout():
    module = load_module()

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "document-page-overlay-workbench")
    slots = {node["slot"] for node in hierarchy["nodes"]}
    nodes_by_slot = {node["slot"]: node for node in hierarchy["nodes"]}
    contract = hierarchy["roleContract"]

    assert hierarchy["sourceApp"] == "document"
    assert hierarchy["rootConcern"] == "document.page-authoring"
    assert hierarchy["focusSlot"] == "page"
    assert hierarchy["desiredFocusShare"] == 0.66
    assert contract["requiredCompanions"] == ["toolbar", "status"]
    assert contract["nearbyCompanions"] == []
    assert set(contract["deferableSlots"]) == {"library", "ai"}
    assert set(contract["forbiddenDefaultHidden"]) == {"toolbar", "page", "status"}
    assert {"bounded-drawer", "focus-priority", "source-order-stacked"}.issubset(set(contract["preferredFamilies"]))
    assert {"sectioned-sidebar", "split-pane", "dashboard-grid"}.issubset(set(contract["dangerousFamilies"]))
    assert {"toolbar", "page", "status", "library", "ai"}.issubset(slots)

    assert nodes_by_slot["toolbar"]["role"] == "command"
    assert nodes_by_slot["page"]["role"] == "focus"
    assert nodes_by_slot["status"]["role"] == "status"
    assert nodes_by_slot["library"]["visibility"] == "deferable"
    assert nodes_by_slot["ai"]["visibility"] == "deferable"
    assert nodes_by_slot["library"]["priority"] == "secondary"
    assert nodes_by_slot["ai"]["priority"] == "secondary"

    audit = module.semantic_contract_audit(hierarchy)
    assert audit["state"] == "complete"
    assert audit["missingPrimitives"] == []
    assert "document-page-authoring" not in str(audit).lower()

    expectations = {item["slot"]: item["expectation"] for item in module.semantic_affordance_expectations(hierarchy)}
    assert expectations["toolbar"] == "command-rail"
    assert expectations["page"] == "dominant-surface"
    assert expectations["status"] == "persistent-status-strip"
    assert expectations["library"] == "phase-selector-trigger"
    assert expectations["ai"] == "proof-trigger-or-drawer"

    html = module.render_trial_html(hierarchy, "bounded-drawer", "mcel-realistic")
    assert 'data-mc-source-app="document"' in html
    assert 'data-mc-slot="page"' in html
    assert 'data-mc-controls="page"' in html
    assert 'data-mc-confirms="page.state"' in html
    assert 'data-mc-proves="page.claim"' in html
    assert 'data-flog-deferable-slots="library ai"' in html
    assert 'data-flog-nearby-companions=""' in html
    assert "data-mc-app-archetype" not in html
    assert "data-mc-primary-grammar" not in html


def test_parse_candidates_supports_all_and_rejects_unknown():
    module = load_module()

    assert module.parse_candidates("all") == module.LAYOUT_CANDIDATES
    assert module.parse_candidates("split-pane,focus-priority") == ["split-pane", "focus-priority"]

    try:
        module.parse_candidates("fake-layout")
    except ValueError as exc:
        assert "Unknown layout candidate" in str(exc)
    else:
        raise AssertionError("unknown candidates should fail")


def test_render_trial_html_contains_generated_hierarchy_and_candidate():
    module = load_module()

    hierarchy = module.synthetic_hierarchies()[0]
    html = module.render_trial_html(hierarchy, "split-pane", "mcel-realistic")

    assert 'data-flog-candidate="split-pane"' in html
    assert 'data-flog-focus-slot="workspace"' in html
    assert 'data-mc-slot="workspace"' in html
    assert 'data-mc-source-app=' in html
    assert 'data-flog-required-companions=' in html
    assert 'data-flog-forbidden-default-hidden=' in html
    assert 'data-flog-role=' in html
    assert 'data-flog-item="true"' in html
    assert 'data-flog-item-role=' in html
    assert 'data-mc-connects=' in html
    assert "trial-split-pane" in html
    assert "FOCUS" not in html  # overlay labels are added only by Chromium after measurement


def test_parse_viewports_accepts_named_dimensions():
    module = load_module()

    profiles = module.parse_viewports("desktop=1440x900,narrow=390x844")

    assert [(profile.name, profile.width, profile.height) for profile in profiles] == [
        ("desktop", 1440, 900),
        ("narrow", 390, 844),
    ]


def test_write_reports_lists_best_candidate_and_pngs(tmp_path):
    module = load_module()

    report = {
        "kind": "mcel.flog.synthetic.layout.trial.report",
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["split-pane", "focus-priority"],
        "hierarchies": [
            {
                "id": "operator-control-surface",
                "title": "Operator Control Surface",
                "sourceApp": "conductor",
                "rootConcern": "operations.control",
                "focusSlot": "workspace",
                "desiredFocusShare": 0.42,
                "roleContract": {
                    "requiredCompanions": ["command", "status"],
                    "nearbyCompanions": ["evidence"],
                    "deferableSlots": ["detail"],
                    "forbiddenDefaultHidden": ["command", "workspace", "status"],
                    "preferredFamilies": ["split-pane"],
                    "dangerousFamilies": {"bounded-drawer": "drawer hides power"},
                },
                "nodeSlots": ["command", "workspace", "status"],
                "nodeRoles": {"command": "command", "workspace": "focus", "status": "status"},
            }
        ],
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "screenshotMode": "viewport",
        "snapshotDirectory": ".",
        "snapshotFiles": ["operator-control-surface--desktop--split-pane--viewport.png"],
        "bestByHierarchyViewport": [
            {
                "hierarchyId": "operator-control-surface",
                "viewportProfile": "desktop",
                "candidate": "split-pane",
                "score": 91,
                "status": "pass",
                "selectionState": "bestPassingCandidate",
                "noPassingCandidate": False,
                "unclaimedAreaRatio": 0.12,
                "focusShare": 0.44,
                "desiredFocusShare": 0.42,
                "usefulFocusOccupancy": 0.52,
                "companionProximityScore": 0.86,
                "reasons": ["focus workspace got 44% of the root against target 42%"],
                "failureReasons": [],
                "reviewNotes": ["Human review still must compare the PNG proof against the intended app meaning."],
                "snapshots": {"viewport": "operator-control-surface--desktop--split-pane--viewport.png"},
            }
        ],
        "measurements": [
            {
                "hierarchyId": "operator-control-surface",
                "viewportProfile": "desktop",
                "candidate": "split-pane",
                "focusSlot": "workspace",
                "geometryFacts": {
                    "unclaimedAreaRatio": 0.12,
                    "nodeCoverageRatio": 0.88,
                    "focusShare": 0.44,
                    "desiredFocusShare": 0.42,
                    "focusDeviation": 0.02,
                    "usefulFocusOccupancy": 0.52,
                    "focusContentCount": 7,
                    "companionVisibilityRatio": 1,
                    "nearbyCompanionRatio": 1,
                    "companionProximityScore": 0.86,
                    "missingRequiredCompanions": [],
                    "distantNearbyCompanions": [],
                    "clippedCriticalControlCount": 0,
                    "hiddenCriticalControlCount": 0,
                    "scrollOwnerCount": 1,
                },
                "classification": {
                    "score": 91,
                    "status": "pass",
                    "warnings": [],
                    "positiveReasons": ["focus workspace got 44% of the root against target 42%"],
                    "failureReasons": [],
                    "reviewNotes": ["Human review still must compare the PNG proof against the intended app meaning."],
                },
                "snapshots": {"viewport": "operator-control-surface--desktop--split-pane--viewport.png"},
                "humanLoop": {
                    "proved": ["Chromium rendered the candidate layout."],
                    "inferred": ["Whether the unclaimed area is desirable calm spacing or waste."],
                    "unknowns": ["Real app data volume may change scroll pressure."],
                },
            }
        ],
    }

    json_path, md_path = module.write_reports(report, tmp_path)
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    md = md_path.read_text(encoding="utf-8")

    assert parsed["kind"] == "mcel.flog.synthetic.layout.trial.report"
    assert "Best candidate by hierarchy and viewport" in md
    assert "operator-control-surface--desktop--split-pane--viewport.png" in md
    assert "PNG files written: `1`" in md
    assert "Useful focus occupancy" in md
    assert "Why it ranked well" in md
    assert "selectionState=`bestPassingCandidate`" in md
    assert "focus workspace got 44% of the root against target 42%" in md
    assert "Inferred, not proved" in md


def test_verify_png_written_requires_real_file(tmp_path):
    module = load_module()

    png = tmp_path / "proof.png"
    png.write_bytes(b"png-data")
    module.verify_png_written(png)

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    try:
        module.verify_png_written(empty)
    except RuntimeError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty PNG should fail")

    try:
        module.verify_png_written(tmp_path / "missing.png")
    except RuntimeError as exc:
        assert "not created" in str(exc)
    else:
        raise AssertionError("missing PNG should fail")



def test_candidate_geometry_css_allocates_focus_without_hiding_required_companions():
    module = load_module()

    css = module.TRIAL_CSS

    assert '"status focus evidence"' in css
    assert '"command focus"' in css  # sectioned-sidebar keeps command visible beside focus
    assert '"collection focus command"' in css  # focus-priority integrates nearby navigation/collection as a side rail
    assert '"status status evidence"' in css  # compact status can be a persistent band rather than a full panel
    assert ".trial-bounded-drawer .flog-node[data-flog-role=\"focus\"]" in css
    assert ".trial-top-band-focus-overlay" in css
    assert ".trial-selected-context-workflow" in css
    assert 'data-flog-phase-support="true"' in css
    assert ".policy-phase-aware .flog-trigger-strip" in css
    assert 'data-mc-default-realization^="collapsed-trigger"' not in css
    assert "source-order-stacked preserves visibility but starves a high-focus hierarchy" in module.MEASURE_AND_OVERLAY_JS
    assert "nearbyIntegration(record, focusRecord.rect, rootClipped)" in module.MEASURE_AND_OVERLAY_JS
    assert "Nearby was satisfied by semantic docking/integration" in module.MEASURE_AND_OVERLAY_JS
    assert "return Math.max(0.012, declared || 0);" in module.MEASURE_AND_OVERLAY_JS


def test_best_by_hierarchy_viewport_prefers_passing_candidate_and_marks_no_pass():
    module = load_module()

    def measurement(hierarchy_id, candidate, score, status):
        return {
            "hierarchyId": hierarchy_id,
            "viewportProfile": "desktop",
            "candidate": candidate,
            "classification": {
                "score": score,
                "status": status,
                "positiveReasons": [f"{candidate} reason"],
                "failureReasons": [f"{candidate} failure"] if status == "fail" else [],
                "reviewNotes": [],
            },
            "geometryFacts": {
                "unclaimedAreaRatio": 0.2,
                "focusShare": 0.4,
                "desiredFocusShare": 0.5,
                "usefulFocusOccupancy": 0.5,
                "companionProximityScore": 0.7,
            },
            "snapshots": {"viewport": f"{hierarchy_id}--{candidate}.png"},
        }

    rows = module.best_by_hierarchy_viewport_rows(
        [
            measurement("has-watch", "highest-failure", 82, "fail"),
            measurement("has-watch", "safe-watch", 72, "watch"),
            measurement("all-fail", "highest-failure", 61, "fail"),
            measurement("all-fail", "lower-failure", 40, "fail"),
        ]
    )

    by_hierarchy = {row["hierarchyId"]: row for row in rows}

    assert by_hierarchy["has-watch"]["candidate"] == "safe-watch"
    assert by_hierarchy["has-watch"]["selectionState"] == "bestPassingCandidate"
    assert by_hierarchy["has-watch"]["noPassingCandidate"] is False
    assert by_hierarchy["has-watch"]["highestScoringCandidate"]["candidate"] == "highest-failure"

    assert by_hierarchy["all-fail"]["candidate"] == "highest-failure"
    assert by_hierarchy["all-fail"]["selectionState"] == "noPassingCandidate"
    assert by_hierarchy["all-fail"]["noPassingCandidate"] is True
    assert by_hierarchy["all-fail"]["highestScoringFailure"]["candidate"] == "highest-failure"


def test_generate_rollup_pngs_writes_one_final_rollup_and_caps_each_group_at_eight(tmp_path):
    module = load_module()
    from PIL import Image

    measurements = []
    ranking = [
        ("gold-pass", 95, "pass"),
        ("silver-pass", 91, "pass"),
        ("bronze-pass", 87, "pass"),
        ("steady-watch", 84, "watch"),
        ("late-watch", 81, "watch"),
        ("best-fail", 99, "fail"),
        ("mid-fail", 70, "fail"),
        ("low-fail", 60, "fail"),
        ("overflow-fail", 10, "fail"),
    ]
    for group_name in ["document-workbench", "terminal-console-workbench"]:
        for index, (candidate, score, status) in enumerate(ranking, start=1):
            file_name = f"{group_name}-trial-{index}.png"
            Image.new("RGB", (640, 400), (200, 200, 200)).save(tmp_path / file_name)
            measurements.append(
                {
                    "hierarchyId": group_name,
                    "viewportProfile": "desktop",
                    "candidate": candidate,
                    "classification": {
                        "score": score,
                        "status": status,
                        "positiveReasons": [f"{candidate} summary reason"],
                        "failureReasons": [],
                        "reviewNotes": [],
                    },
                    "geometryFacts": {
                        "focusShare": 0.51,
                        "desiredFocusShare": 0.58,
                    },
                    "snapshots": {"viewport": file_name},
                }
            )

    report = {"measurements": measurements}
    rollups = module.generate_rollup_pngs(report, tmp_path)

    assert len(rollups) == 1
    rollup = rollups[0]
    assert rollup["kind"] == "finalRollup"
    assert rollup["file"] == module.ROLLUP_FILE_NAME
    assert rollup["groupCount"] == 2
    assert rollup["columns"] == 8
    assert rollup["rows"] == 2
    assert len(rollup["groups"]) == 2
    assert rollup["groups"][0]["hierarchyId"] == "document-workbench"
    assert rollup["groups"][0]["topCount"] == 8
    assert rollup["groups"][0]["candidates"] == [
        "gold-pass",
        "silver-pass",
        "bronze-pass",
        "steady-watch",
        "late-watch",
        "best-fail",
        "mid-fail",
        "low-fail",
    ]
    rollup_path = tmp_path / rollup["file"]
    assert rollup_path.exists()

    with Image.open(rollup_path) as image:
        width, height = image.size
    assert width > height
    assert width < 1800
    assert height < 500


def test_write_reports_lists_rollup_pngs(tmp_path):
    module = load_module()
    report = {
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["focus-priority"],
        "hierarchies": [
            {
                "id": "document-workbench",
                "title": "Document Workbench",
                "sourceApp": "document",
                "rootConcern": "document.authoring",
                "focusSlot": "editor",
                "desiredFocusShare": 0.58,
                "requiredCompanions": ["toolbar", "status"],
                "nearbyCompanions": ["outline", "inspector"],
                "deferableSlots": ["evidence"],
                "forbiddenDefaultHidden": ["toolbar", "editor", "status"],
                "preferredFamilies": ["focus-priority"],
                "slots": ["toolbar", "outline", "editor", "inspector", "status", "evidence"],
                "nodeSlots": ["toolbar", "outline", "editor", "inspector", "status", "evidence"],
                "nodeRoles": {"editor": "focus"},
            }
        ],
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "screenshotMode": "viewport",
        "snapshotDirectory": ".",
        "snapshotFiles": ["document-workbench--desktop--focus-priority--viewport.png"],
        "bestByHierarchyViewport": [
            {
                "hierarchyId": "document-workbench",
                "viewportProfile": "desktop",
                "candidate": "focus-priority",
                "score": 88,
                "status": "pass",
                "selectionState": "bestPassingCandidate",
                "noPassingCandidate": False,
                "unclaimedAreaRatio": 0.17,
                "focusShare": 0.51,
                "desiredFocusShare": 0.58,
                "usefulFocusOccupancy": 0.42,
                "companionProximityScore": 0.63,
                "snapshots": {"viewport": "document-workbench--desktop--focus-priority--viewport.png"},
                "reasons": ["all required companions remained visible enough"],
                "failureReasons": [],
                "reviewNotes": ["Human review still must compare the PNG proof against the intended app meaning."],
            }
        ],
        "humanLoop": {"required": True, "reason": "human review required"},
        "measurements": [],
        "rollups": [
            {
                "kind": "finalRollup",
                "file": "layout-snapshot-final-rollup.png",
                "groupCount": 1,
                "columns": 2,
                "rows": 1,
                "topCountPerGroup": 8,
                "groups": [
                    {
                        "hierarchyId": "document-workbench",
                        "viewportProfile": "desktop",
                        "candidates": ["focus-priority", "split-pane"],
                        "topCount": 2,
                    }
                ],
            }
        ],
        "rollupFiles": ["layout-snapshot-final-rollup.png"],
    }

    json_path, md_path = module.write_reports(report, tmp_path)
    assert json_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "## Rollup PNGs" in text
    assert "layout-snapshot-final-rollup.png" in text
    assert "groups=`1`" in text
    assert "focus-priority, split-pane" in text


def test_semantic_primitives_are_generic_and_composable():
    module = load_module()

    for hierarchy in module.synthetic_hierarchies():
        audit = module.semantic_contract_audit(hierarchy)
        assert audit["state"] == "complete"
        assert audit["missingPrimitives"] == []
        assert audit["primitiveEdgeCount"] >= 5
        assert audit["inferredLayoutGrammar"]
        assert "no high-level app archetype" in audit["note"]
        assert audit["presentationSets"]
        assert audit["presentationSetCount"] >= 2
        assert audit["qualityCounts"]["phase"] >= 1
        assert audit["qualityCounts"]["relationshipStrength"] >= 1

        for node in hierarchy["nodes"]:
            semantics = node.get("semantics", {})
            assert semantics
            assert "appArchetype" not in semantics
            assert "primaryGrammar" not in semantics

    html = module.render_trial_html(module.synthetic_hierarchies()[0], "split-pane", "mcel-realistic")
    assert "data-mc-controls=" in html
    assert "data-mc-confirms=" in html
    assert "data-mc-proves=" in html
    assert "data-mc-growth=" in html
    assert "data-mc-scroll-policy=" in html
    assert "data-mc-phase=" in html
    assert "data-mc-presentation-set=" in html
    assert "data-mc-relationship-strength=" in html
    assert "data-mc-hard-constraints=" in html
    assert "data-mc-soft-preferences=" in html
    assert "data-mc-app-archetype" not in html
    assert "data-mc-primary-grammar" not in html


def test_semantic_contract_audit_marks_missing_generic_primitives():
    module = load_module()

    weak_hierarchy = {
        "id": "weak-fixture",
        "focusSlot": "workspace",
        "nodes": [
            {
                "slot": "command",
                "role": "command",
                "connects": ["workspace"],
                "semantics": {},
            },
            {
                "slot": "workspace",
                "role": "focus",
                "connects": [],
                "semantics": {},
            },
            {
                "slot": "status",
                "role": "status",
                "connects": ["workspace"],
                "semantics": {},
            },
        ],
    }

    audit = module.semantic_contract_audit(weak_hierarchy)

    assert audit["state"] == "underspecified"
    assert "workspace emits selection/state" in audit["missingPrimitives"]
    assert "workspace declares generic growth/density behavior" in audit["missingPrimitives"]
    assert "command controls workspace" in audit["missingPrimitives"]
    assert "status confirms workspace.state" in audit["missingPrimitives"]
    assert any("presentation sets" in item for item in audit["missingPrimitives"])
    assert any("relationship edges declare generic strength" in item for item in audit["missingPrimitives"])


def test_write_reports_lists_generic_semantic_contract_audit(tmp_path):
    module = load_module()

    report = {
        "kind": "mcel.flog.synthetic.layout.trial.report",
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["split-pane"],
        "hierarchies": [
            {
                "id": "operator-control-surface",
                "title": "Operator Control Surface",
                "sourceApp": "conductor",
                "rootConcern": "operations.control",
                "focusSlot": "workspace",
                "desiredFocusShare": 0.42,
                "roleContract": {
                    "requiredCompanions": ["command", "status"],
                    "nearbyCompanions": ["evidence"],
                    "deferableSlots": ["detail"],
                    "forbiddenDefaultHidden": ["command", "workspace", "status"],
                    "preferredFamilies": ["split-pane"],
                    "dangerousFamilies": {},
                },
                "nodeSlots": ["command", "workspace", "status"],
                "nodeRoles": {"command": "command", "workspace": "focus", "status": "status"},
            }
        ],
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "screenshotMode": "viewport",
        "snapshotDirectory": ".",
        "snapshotFiles": ["operator-control-surface--desktop--split-pane--viewport.png"],
        "semanticContracts": [
            {
                "hierarchyId": "operator-control-surface",
                "focusSlot": "workspace",
                "state": "complete",
                "primitiveEdgeCount": 6,
                "inferredLayoutGrammar": [
                    "controller-focus-confirmation",
                    "evidence-backed-claim",
                ],
                "missingPrimitives": [],
                "relationships": [
                    "command controls workspace",
                    "status confirms workspace.state",
                    "evidence proves workspace.claim",
                ],
                "presentationSets": [
                    {"phase": "default", "requiredSlots": ["command", "workspace", "status"]},
                    {"phase": "confirmation", "requiredSlots": ["command", "workspace", "status", "evidence"]},
                ],
                "qualityCounts": {
                    "phase": 3,
                    "availability": 3,
                    "presentationSet": 3,
                    "relationshipStrength": 3,
                    "hardConstraints": 2,
                    "softPreferences": 3,
                },
            }
        ],
        "bestByHierarchyViewport": [],
        "humanLoop": {"required": True, "reason": "human review required"},
        "measurements": [],
    }

    _, md_path = module.write_reports(report, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    assert "## Generic semantic contract audit" in text
    assert "composable MCEL primitives" in text
    assert "state=`complete`" in text
    assert "Missing primitives: `none`" in text
    assert "command controls workspace" in text
    assert "Presentation sets" in text
    assert "Generic quality tags" in text


def test_semantic_contract_fit_rewards_generic_spatial_relationships():
    module = load_module()

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "document-workbench")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    def measurement(nodes):
        return {
            "hierarchyId": "document-workbench",
            "candidate": "synthetic",
            "viewportProfile": "desktop",
            "geometryFacts": {
                "root": {
                    "clipped": {
                        "left": 0,
                        "top": 0,
                        "right": 1000,
                        "bottom": 600,
                        "width": 1000,
                        "height": 600,
                        "area": 600000,
                    }
                },
                "focusShare": 0.54,
                "desiredFocusShare": 0.58,
                "minFocusShare": 0.48,
            },
            "examples": {"nodes": nodes},
            "classification": {"score": 82, "status": "pass", "warnings": [], "positiveReasons": [], "failureReasons": [], "reviewNotes": []},
            "snapshots": {},
        }

    well_related = measurement(
        [
            record("toolbar", 210, 20, 580, 52),
            record("outline", 20, 86, 176, 414),
            record("editor", 210, 86, 580, 414),
            record("inspector", 804, 86, 176, 414),
            record("status", 210, 514, 580, 42),
            record("evidence", 804, 514, 176, 42),
        ]
    )
    weakly_related = measurement(
        [
            record("toolbar", 20, 20, 960, 50),
            record("editor", 20, 86, 960, 260),
            record("outline", 20, 390, 300, 58),
            record("inspector", 340, 390, 300, 58),
            record("status", 20, 520, 960, 42),
            record("evidence", 20, 566, 960, 28),
        ]
    )

    good_fit = module.semantic_contract_fit(hierarchy, well_related)
    weak_fit = module.semantic_contract_fit(hierarchy, weakly_related)

    assert good_fit["confidence"] >= 0.9
    assert good_fit["score"] >= 80
    assert good_fit["score"] > weak_fit["score"] + 10
    assert any("outline" in reason or "inspector" in reason for reason in good_fit["positiveReasons"])
    assert "appArchetype" not in str(good_fit)
    assert "primaryGrammar" not in str(good_fit)


def test_apply_semantic_contract_fit_updates_selection_score_without_claiming_perfect_inference():
    module = load_module()

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "file-explorer-workspace")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    measurement = {
        "hierarchyId": "file-explorer-workspace",
        "candidate": "split-pane",
        "viewportProfile": "desktop",
        "geometryFacts": {
            "root": {
                "clipped": {
                    "left": 0,
                    "top": 0,
                    "right": 1000,
                    "bottom": 600,
                    "width": 1000,
                    "height": 600,
                    "area": 600000,
                }
            },
            "focusShare": 0.49,
            "desiredFocusShare": 0.48,
            "minFocusShare": 0.38,
        },
        "examples": {
            "nodes": [
                record("command", 210, 20, 580, 52),
                record("records", 20, 86, 176, 414),
                record("workspace", 210, 86, 580, 414),
                record("detail", 804, 86, 176, 414),
                record("status", 210, 514, 580, 42),
                record("evidence", 804, 514, 176, 42),
            ]
        },
        "classification": {
            "score": 84,
            "status": "pass",
            "warnings": [],
            "positiveReasons": [],
            "failureReasons": [],
            "reviewNotes": [],
        },
        "snapshots": {},
    }

    module.apply_semantic_contract_fit(hierarchy, measurement)
    classification = measurement["classification"]
    assert measurement["contractFit"]["note"].endswith("not a claim of perfect inference.")
    assert classification["geometryScore"] == 84
    assert classification["contractFitScore"] >= 80
    assert classification["affordanceFitScore"] >= 80
    assert classification["selectionScore"] == classification["score"]
    assert classification["contractFitState"] in {"strongContractFit", "usableContractFit"}
    assert classification["affordanceFitState"] in {"strongAffordanceFit", "usableAffordanceFit"}
    assert any("generic contract fit" in reason for reason in classification["positiveReasons"])
    assert any("generic affordance fit" in reason for reason in classification["positiveReasons"])


def test_presentation_sets_are_generic_phase_contracts():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "terminal-console-workbench")

    sets = module.semantic_presentation_sets(hierarchy)
    by_phase = {item["phase"]: item for item in sets}

    assert "default" in by_phase
    assert "confirmation" in by_phase
    assert "proof-review" in by_phase
    assert hierarchy["focusSlot"] in by_phase["default"]["requiredSlots"]
    assert "status" in by_phase["confirmation"]["requiredSlots"]
    assert all("app" not in item["phase"] for item in sets)


def test_hard_contract_risks_cap_contract_fit_without_eliminating_flog_options():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "terminal-console-workbench")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    weak_measurement = {
        "hierarchyId": "terminal-console-workbench",
        "candidate": "synthetic-weak",
        "viewportProfile": "desktop",
        "geometryFacts": {
            "root": {
                "clipped": {
                    "left": 0,
                    "top": 0,
                    "right": 1000,
                    "bottom": 600,
                    "width": 1000,
                    "height": 600,
                    "area": 600000,
                }
            },
            "focusShare": 0.20,
            "desiredFocusShare": 0.62,
            "minFocusShare": 0.50,
        },
        "examples": {
            "nodes": [
                record("command", 0, 0, 180, 50),
                record("terminal", 760, 470, 200, 80),
                record("records", 0, 520, 180, 60),
                record("detail", 240, 0, 200, 60),
                record("status", 0, 560, 180, 30),
                record("evidence", 470, 0, 200, 60),
            ]
        },
        "classification": {"score": 86, "status": "pass", "warnings": [], "positiveReasons": [], "failureReasons": [], "reviewNotes": []},
        "snapshots": {},
    }

    module.apply_semantic_contract_fit(hierarchy, weak_measurement)
    fit = weak_measurement["contractFit"]

    assert fit["hardRiskCount"] >= 1
    assert fit["score"] <= 62
    assert fit["contractLimits"]
    assert weak_measurement["classification"]["status"] in {"watch", "fail"}
    assert weak_measurement["classification"]["hardContractRiskCount"] >= 1
    assert weak_measurement["classification"]["hardAffordanceMissCount"] >= 1


def test_affordance_expectations_are_generic_not_app_archetypes():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "spreadsheet-workbook")

    expectations = module.semantic_affordance_expectations(hierarchy)
    by_slot = {item["slot"]: item for item in expectations}

    assert by_slot["grid"]["expectation"] == "dominant-dense-grid"
    assert by_slot["formula"]["expectation"] == "command-rail"
    assert by_slot["tabs"]["expectation"] == "selection-rail"
    assert by_slot["inspector"]["expectation"] == "inspector-dock"
    assert by_slot["status"]["expectation"] == "persistent-status-strip"
    assert "spreadsheet" not in str(expectations).lower()
    assert "appArchetype" not in str(expectations)


def test_affordance_realization_scores_realized_spatial_forms_over_weak_geometry():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "document-workbench")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 600,
        "width": 1000,
        "height": 600,
        "area": 600000,
    }
    good = {
        "geometryFacts": {
            "root": {"clipped": root},
            "focusShare": 0.56,
            "desiredFocusShare": 0.58,
            "minFocusShare": 0.48,
        },
        "examples": {
            "nodes": [
                record("toolbar", 210, 10, 580, 48),
                record("outline", 20, 72, 176, 430),
                record("editor", 210, 72, 580, 430),
                record("inspector", 804, 72, 176, 430),
                record("status", 210, 516, 580, 38),
                record("evidence", 804, 516, 176, 38),
            ]
        },
    }
    weak = {
        "geometryFacts": {
            "root": {"clipped": root},
            "focusShare": 0.34,
            "desiredFocusShare": 0.58,
            "minFocusShare": 0.48,
        },
        "examples": {
            "nodes": [
                record("toolbar", 0, 0, 1000, 100),
                record("outline", 0, 110, 1000, 80),
                record("editor", 0, 200, 1000, 170),
                record("inspector", 0, 380, 1000, 80),
                record("status", 0, 470, 1000, 80),
                record("evidence", 0, 560, 1000, 30),
            ]
        },
    }

    good_fit = module.semantic_affordance_realization_fit(hierarchy, good)
    weak_fit = module.semantic_affordance_realization_fit(hierarchy, weak)

    assert good_fit["score"] > weak_fit["score"] + 15
    assert good_fit["state"] in {"strongAffordanceFit", "usableAffordanceFit"}
    assert weak_fit["hardMissCount"] >= 1
    assert any("editor realizes" in reason for reason in good_fit["positiveReasons"])
    assert "perfect layout" in good_fit["note"]


def test_write_reports_lists_affordance_fit(tmp_path):
    module = load_module()
    report = {
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["focus-priority"],
        "hierarchies": [
            {
                "id": "document-workbench",
                "title": "Document Workbench",
                "sourceApp": "document",
                "rootConcern": "document.authoring",
                "focusSlot": "editor",
                "desiredFocusShare": 0.58,
                "roleContract": {
                    "requiredCompanions": ["toolbar", "status"],
                    "nearbyCompanions": ["outline", "inspector"],
                    "deferableSlots": ["evidence"],
                    "forbiddenDefaultHidden": ["toolbar", "editor", "status"],
                    "preferredFamilies": ["focus-priority"],
                },
                "nodeSlots": ["toolbar", "outline", "editor", "inspector", "status", "evidence"],
                "nodeRoles": {"editor": "focus"},
            }
        ],
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "screenshotMode": "viewport",
        "snapshotDirectory": ".",
        "snapshotFiles": ["document-workbench--desktop--focus-priority--viewport.png"],
        "semanticContracts": [
            {
                "hierarchyId": "document-workbench",
                "state": "complete",
                "focusSlot": "editor",
                "primitiveEdgeCount": 8,
                "layoutPressureCount": 6,
                "affordanceExpectationCount": 6,
                "contractConfidence": 0.94,
                "inferredLayoutGrammar": ["selector-focus"],
                "missingPrimitives": [],
                "relationships": ["toolbar controls editor"],
                "presentationSets": [{"phase": "default", "requiredSlots": ["toolbar", "editor", "status"]}],
                "qualityCounts": {},
                "affordanceExpectations": ["dominant-surface", "command-rail"],
            }
        ],
        "bestByHierarchyViewport": [
            {
                "hierarchyId": "document-workbench",
                "viewportProfile": "desktop",
                "candidate": "focus-priority",
                "score": 89,
                "status": "pass",
                "selectionState": "bestPassingCandidate",
                "noPassingCandidate": False,
                "unclaimedAreaRatio": 0.17,
                "focusShare": 0.51,
                "desiredFocusShare": 0.58,
                "usefulFocusOccupancy": 0.42,
                "companionProximityScore": 0.63,
                "contractFitScore": 91,
                "contractFitState": "strongContractFit",
                "affordanceFitScore": 88,
                "affordanceFitState": "strongAffordanceFit",
                "affordanceFitReasons": ["editor realizes dominant-surface at 51% against target 58%"],
                "affordanceFitRisks": [],
                "hardAffordanceRisks": [],
                "snapshots": {"viewport": "document-workbench--desktop--focus-priority--viewport.png"},
                "reasons": [],
                "failureReasons": [],
                "reviewNotes": [],
            }
        ],
        "humanLoop": {"required": True, "reason": "human review required"},
        "measurements": [
            {
                "hierarchyId": "document-workbench",
                "viewportProfile": "desktop",
                "candidate": "focus-priority",
                "focusSlot": "editor",
                "geometryFacts": {
                    "unclaimedAreaRatio": 0.17,
                    "nodeCoverageRatio": 0.83,
                    "focusShare": 0.51,
                    "desiredFocusShare": 0.58,
                    "focusDeviation": 0.07,
                    "usefulFocusOccupancy": 0.42,
                    "focusContentCount": 8,
                    "companionVisibilityRatio": 1,
                    "nearbyCompanionRatio": 1,
                    "companionProximityScore": 0.63,
                    "clippedCriticalControlCount": 0,
                    "hiddenCriticalControlCount": 0,
                    "scrollOwnerCount": 0,
                },
                "classification": {
                    "score": 89,
                    "status": "pass",
                    "geometryScore": 88,
                    "contractFitScore": 91,
                    "contractFitState": "strongContractFit",
                    "affordanceFitScore": 88,
                    "affordanceFitState": "strongAffordanceFit",
                    "positiveReasons": [],
                    "failureReasons": [],
                    "reviewNotes": [],
                    "warnings": [],
                },
                "contractFit": {},
                "affordanceFit": {
                    "score": 88,
                    "rawScore": 88,
                    "state": "strongAffordanceFit",
                    "positiveReasons": ["editor realizes dominant-surface at 51% against target 58%"],
                    "riskReasons": [],
                    "hardRiskReasons": [],
                },
                "snapshots": {"viewport": "document-workbench--desktop--focus-priority--viewport.png"},
                "humanLoop": {"proved": [], "inferred": [], "unknowns": []},
            }
        ],
        "rollups": [],
    }

    _, md_path = module.write_reports(report, tmp_path)
    text = md_path.read_text(encoding="utf-8")

    assert "affordances=`6`" in text
    assert "Affordance realization evidence" in text
    assert "Generic affordance realization" in text
    assert "editor realizes dominant-surface" in text

def test_git_tools_phase_specific_selector_does_not_force_persistent_sidebar():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "git-tools-workflow-workbench")

    sets = module.semantic_presentation_sets(hierarchy)
    by_phase = {item["phase"]: item for item in sets}

    assert "default" in by_phase
    assert "project-selector" not in by_phase.get("selected-project-default", by_phase.get("default", {})).get("requiredSlots", [])
    assert "project-selection" in by_phase
    assert "project-selector" in by_phase["project-selection"]["slots"]

    pressures = module.semantic_layout_pressures(hierarchy)
    project_pressures = [item for item in pressures if item.get("source") == "project-selector"]
    assert project_pressures
    assert all(item["expectation"] == "phase-selector-access" for item in project_pressures)
    assert all(item["requirement"] == "soft" for item in project_pressures)
    assert "selector-adjacent" not in {item["expectation"] for item in project_pressures}


def test_phase_realization_fit_accepts_default_triggers_for_phase_specific_regions():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "git-tools-workflow-workbench")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    measurement = {
        "geometryFacts": {
            "root": {
                "clipped": {
                    "left": 0,
                    "top": 0,
                    "right": 1000,
                    "bottom": 600,
                    "width": 1000,
                    "height": 600,
                    "area": 600000,
                }
            },
            "focusShare": 0.58,
            "desiredFocusShare": 0.58,
            "minFocusShare": 0.46,
        },
        "examples": {
            "nodes": [
                record("project-context", 0, 52, 1000, 44),
                    record("command", 0, 102, 1000, 50),
                    record("workflow", 0, 158, 1000, 400),
                    record("status", 0, 562, 1000, 32),
                    record("project-selector", 10, 566, 132, 28),
                    record("server", 152, 566, 132, 28),
                    record("evidence", 294, 566, 132, 28),
                    record("advanced", 436, 566, 132, 28),
            ]
        },
        "classification": {"score": 88, "status": "pass", "warnings": [], "positiveReasons": [], "failureReasons": [], "reviewNotes": []},
    }

    phase_fit = module.semantic_phase_realization_fit(hierarchy, measurement)
    assert 72 <= phase_fit["score"] < 100
    assert phase_fit["state"] in {"usablePhaseFit", "strongPhaseFit"}
    assert phase_fit["worstScore"] < 100
    assert any("trigger" in reason or "inactive support" in reason for reason in phase_fit["positiveReasons"] + phase_fit["riskReasons"])



def test_phase_realization_fit_penalizes_static_project_roster_tax():
    module = load_module()
    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "git-tools-workflow-workbench")

    def record(slot, left, top, width, height):
        return {
            "slot": slot,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "area": width * height,
            },
        }

    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 600,
        "width": 1000,
        "height": 600,
        "area": 600000,
    }
    compact_trigger_measurement = {
        "geometryFacts": {"root": {"clipped": root}},
        "examples": {
            "nodes": [
                record("project-context", 0, 52, 1000, 44),
                record("command", 0, 102, 1000, 50),
                record("workflow", 0, 158, 1000, 400),
                record("status", 0, 562, 1000, 32),
                record("project-selector", 10, 566, 132, 28),
                record("server", 152, 566, 132, 28),
                record("evidence", 294, 566, 132, 28),
                record("advanced", 436, 566, 132, 28),
            ]
        },
    }
    static_roster_measurement = {
        "geometryFacts": {"root": {"clipped": root}},
        "examples": {
            "nodes": [
                record("project-context", 0, 0, 1000, 44),
                record("command", 240, 52, 760, 50),
                record("workflow", 240, 110, 760, 330),
                record("status", 240, 450, 760, 34),
                record("project-selector", 0, 52, 230, 500),
                record("server", 240, 492, 200, 84),
                record("evidence", 450, 492, 200, 84),
                record("advanced", 660, 492, 200, 84),
            ]
        },
    }

    compact_fit = module.semantic_phase_realization_fit(hierarchy, compact_trigger_measurement)
    static_fit = module.semantic_phase_realization_fit(hierarchy, static_roster_measurement)

    assert compact_fit["score"] > static_fit["score"]
    assert static_fit["score"] < 72
    assert any("inactive phase-specific support" in reason for reason in static_fit["riskReasons"])


def test_git_phase_realization_replaces_inactive_panels_with_real_triggers():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenarios = {
        item["phase"]: item for item in module.phase_trial_scenarios(hierarchy)
    }

    default_realized = module.realize_phase(
        hierarchy,
        "progressive-workflow",
        scenarios["selected-project-default"],
    )
    assert default_realized["focusSlot"] == "workflow"
    assert default_realized["realizationStates"]["workflow"] == "full-active"
    assert default_realized["realizationStates"]["project-context"] == "persistent"
    assert {
        slot
        for slot, state in default_realized["realizationStates"].items()
        if state == "compact-trigger"
    } == {"project-selector", "server", "evidence", "advanced"}

    default_html = module.render_realized_trial_html(
        default_realized,
        "progressive-workflow",
        "mcel-realistic",
    )
    assert default_html.count('data-flog-trigger-for=') == 4
    assert 'data-flog-trigger-for="evidence"' in default_html
    assert 'id="flog-surface-evidence"' not in default_html
    assert "Dry-run transcript and Git receipt" not in default_html
    assert 'data-flog-realization="compact-trigger"' in default_html

    planning_realized = module.realize_phase(
        hierarchy,
        "progressive-workflow",
        scenarios["planning"],
    )
    planning_html = module.render_realized_trial_html(
        planning_realized,
        "progressive-workflow",
        "mcel-realistic",
    )
    assert planning_realized["realizationStates"]["server"] == "full-active"
    assert 'id="flog-surface-server"' in planning_html
    assert 'data-flog-trigger-for="server"' not in planning_html
    assert "Refresh server" in planning_html

    proof_realized = module.realize_phase(
        hierarchy,
        "workflow-with-proof-drawer",
        scenarios["proof-review"],
    )
    proof_html = module.render_realized_trial_html(
        proof_realized,
        "workflow-with-proof-drawer",
        "mcel-realistic",
    )
    assert proof_realized["realizationStates"]["evidence"] == "full-active"
    assert 'id="flog-surface-evidence"' in proof_html
    assert 'data-flog-trigger-for="evidence"' not in proof_html
    assert "Dry-run transcript and Git receipt" in proof_html


def test_git_browser_battery_contains_six_independent_states():
    module = load_module()
    git = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    generic = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "document-workbench"
    )

    assert [item["phase"] for item in module.phase_trial_scenarios(git)] == [
        "project-selection",
        "selected-project-default",
        "planning",
        "execution",
        "proof-review",
        "recovery",
    ]
    assert len(module.phase_trial_scenarios(generic)) == 1
    assert module.phase_trial_scenarios(generic)[0]["phase"] == "default"


def test_phase_policy_score_uses_independent_measurements_and_absolute_hard_failures():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenarios = module.phase_trial_scenarios(hierarchy)
    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 1000,
        "width": 1000,
        "height": 1000,
        "area": 1_000_000,
    }

    def record(slot, share, realization):
        height = share * 1000
        return {
            "slot": slot,
            "realization": realization,
            "rect": {
                "left": 0,
                "top": 0,
                "right": 1000,
                "bottom": height,
                "width": 1000,
                "height": height,
                "area": 1000 * height,
            },
        }

    measurements = []
    for scenario in scenarios:
        dominant = scenario["dominantSlot"]
        records = [
            record(
                dominant,
                scenario["targetDominantShare"],
                "full-active",
            )
        ]
        for slot in scenario["requiredSlots"]:
            if slot == dominant:
                continue
            records.append(
                record(
                    slot,
                    0.06 if slot in {"command", "status"} else 0.03,
                    "persistent",
                )
            )
        for slot in scenario["activeSupportSlots"]:
            if slot not in {item["slot"] for item in records}:
                records.append(
                    record(
                        slot,
                        0.20 if slot == "evidence" else 0.18,
                        "full-active",
                    )
                )
        active = {
            dominant,
            *scenario["requiredSlots"],
            *scenario["activeSupportSlots"],
        }
        for slot in scenario["collapsedSlots"]:
            if slot not in active:
                records.append(record(slot, 0.003, "compact-trigger"))
        measurements.append(
            {
                "phase": scenario["phase"],
                "geometryFacts": {
                    "root": {"clipped": root},
                    "clippedCriticalControlCount": 0,
                    "hiddenCriticalControlCount": 0,
                },
                "examples": {"nodes": records},
                "classification": {
                    "score": 92,
                    "geometryScore": 92,
                    "status": "pass",
                },
                "snapshots": {
                    "viewport": f"git--{scenario['phase']}--viewport.png"
                },
            }
        )

    fit = module.semantic_phase_realization_fit(hierarchy, measurements)
    expected = round(
        (0.50 * fit["worstScore"])
        + (0.30 * fit["selectedDefaultScore"])
        + (0.20 * fit["meanScore"])
    )
    assert fit["phaseCount"] == 6
    assert fit["selectedDefaultPhase"] == "selected-project-default"
    assert fit["score"] == expected
    assert fit["hardFailureCount"] == 0
    assert fit["state"] in {"strongPhaseFit", "usablePhaseFit"}

    broken = [dict(item) for item in measurements]
    broken[4] = {
        **broken[4],
        "geometryFacts": {
            **broken[4]["geometryFacts"],
            "clippedCriticalControlCount": 1,
        },
    }
    broken_fit = module.semantic_phase_realization_fit(hierarchy, broken)
    assert broken_fit["hardFailureCount"] >= 1
    assert broken_fit["state"] in {"weakPhaseFit", "phaseRisk"}
    assert any(
        "proof-review phase has 1 clipped or hidden active critical control"
        in reason
        for reason in broken_fit["hardFailureReasons"]
    )



def test_generic_canonical_trial_does_not_fail_unrendered_fallback_phases():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "document-workbench"
    )
    scenario = module.phase_trial_scenarios(hierarchy)[0]
    assert scenario["phase"] == "default"
    assert not hierarchy.get("phaseScenarios")

    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 1000,
        "width": 1000,
        "height": 1000,
        "area": 1_000_000,
    }

    def record(slot, share, realization):
        height = share * 1000
        return {
            "slot": slot,
            "realization": realization,
            "rect": {
                "left": 0,
                "top": 0,
                "right": 1000,
                "bottom": height,
                "width": 1000,
                "height": height,
                "area": 1000 * height,
            },
        }

    active = {
        scenario["dominantSlot"],
        *scenario["requiredSlots"],
        *scenario["activeSupportSlots"],
    }
    records = [
        record(
            scenario["dominantSlot"],
            scenario["targetDominantShare"],
            "full-active",
        )
    ]
    records.extend(
        record(slot, 0.05, "persistent")
        for slot in scenario["requiredSlots"]
        if slot != scenario["dominantSlot"]
    )
    records.extend(
        record(slot, 0.015, "inactive-panel")
        for slot in scenario["collapsedSlots"]
        if slot not in active
    )
    measurement = {
        "phase": "default",
        "candidate": "focus-priority",
        "candidatePolicy": module.candidate_phase_policy("focus-priority"),
        "geometryFacts": {
            "root": {"clipped": root},
            "clippedCriticalControlCount": 0,
            "hiddenCriticalControlCount": 0,
        },
        "examples": {"nodes": records},
        "classification": {
            "score": 92,
            "geometryScore": 92,
            "status": "pass",
        },
        "snapshots": {"viewport": "document--default--viewport.png"},
    }

    fit = module.semantic_phase_realization_fit(hierarchy, [measurement])

    assert fit["phaseCount"] == 1
    assert fit["selectedDefaultPhase"] == "default"
    assert fit["hardFailureCount"] == 0
    assert not any(
        "no independent browser measurement" in reason
        for reason in fit["hardFailureReasons"]
    )
    assert fit["phases"][0]["supportsCompactTriggers"] is False
    assert fit["phases"][0]["panelLikeTriggerSlots"] == []


def test_explicit_phase_battery_still_requires_every_browser_measurement():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenario = module.phase_trial_scenarios(hierarchy)[0]
    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 1000,
        "width": 1000,
        "height": 1000,
        "area": 1_000_000,
    }

    def record(slot, share, realization):
        height = share * 1000
        return {
            "slot": slot,
            "realization": realization,
            "rect": {
                "left": 0,
                "top": 0,
                "right": 1000,
                "bottom": height,
                "width": 1000,
                "height": height,
                "area": 1000 * height,
            },
        }

    active = {
        scenario["dominantSlot"],
        *scenario["requiredSlots"],
        *scenario["activeSupportSlots"],
    }
    records = [
        record(
            scenario["dominantSlot"],
            scenario["targetDominantShare"],
            "full-active",
        )
    ]
    records.extend(
        record(slot, 0.05, "persistent")
        for slot in scenario["requiredSlots"]
        if slot != scenario["dominantSlot"]
    )
    records.extend(
        record(slot, 0.003, "compact-trigger")
        for slot in scenario["collapsedSlots"]
        if slot not in active
    )
    measurement = {
        "phase": scenario["phase"],
        "candidate": "selected-context-workflow",
        "candidatePolicy": module.candidate_phase_policy(
            "selected-context-workflow"
        ),
        "geometryFacts": {
            "root": {"clipped": root},
            "clippedCriticalControlCount": 0,
            "hiddenCriticalControlCount": 0,
        },
        "examples": {"nodes": records},
        "classification": {
            "score": 92,
            "geometryScore": 92,
            "status": "pass",
        },
        "snapshots": {"viewport": "git--project-selection--viewport.png"},
    }

    fit = module.semantic_phase_realization_fit(hierarchy, [measurement])

    assert fit["phaseCount"] == 6
    assert fit["hardFailureCount"] >= 5
    assert any(
        "selected-project-default phase has no independent browser measurement"
        in reason
        for reason in fit["hardFailureReasons"]
    )


def test_active_phase_form_controls_are_constrained_to_their_panel():
    module = load_module()

    assert ".flog-node input," in module.TRIAL_CSS
    assert ".flog-node select," in module.TRIAL_CSS
    assert ".flog-node textarea {" in module.TRIAL_CSS
    assert "max-width: 100%;" in module.TRIAL_CSS


def test_new_workflow_candidates_are_generic_and_available():
    module = load_module()

    for candidate in [
        "top-band-dominant-surface",
        "top-band-focus-overlay",
        "selected-context-workflow",
        "progressive-workflow",
        "workflow-with-proof-drawer",
    ]:
        assert candidate in module.LAYOUT_CANDIDATES
        assert f"trial-{candidate}" in module.TRIAL_CSS

    hierarchy = next(item for item in module.synthetic_hierarchies() if item["id"] == "git-tools-workflow-workbench")
    html = module.render_trial_html(hierarchy, "progressive-workflow", "mcel-realistic")

    assert 'data-flog-candidate="progressive-workflow"' in html
    assert 'data-flog-interaction-phases=' in html
    assert 'data-mc-phase-persistence="phase-specific-selector' in html
    assert 'data-mc-default-realization="collapsed-trigger' in html
    assert "data-mc-app-archetype" not in html
    assert "data-mc-primary-grammar" not in html




def test_git_layout_is_decomposed_into_recursive_responsibility_units():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    specs = module.layout_unit_specs(hierarchy)
    by_id = {item["id"]: item for item in specs}
    leaves = {item["id"]: item for item in specs if item["leaf"]}

    assert by_id["git-tools-application"]["childIds"] == [
        "project-identity",
        "command-workflow",
        "persistent-feedback",
        "phase-support",
    ]
    assert leaves["project-identity"]["slots"] == ["project-selector"]
    assert leaves["command-workflow"]["slots"] == ["command", "workflow"]
    assert leaves["persistent-feedback"]["slots"] == ["project-context", "status"]
    assert leaves["phase-support"]["slots"] == ["server", "evidence", "advanced"]

    owners = module.layout_unit_slot_map(hierarchy)
    assert set(owners) == {node["slot"] for node in hierarchy["nodes"]}
    assert owners["workflow"]["id"] == "command-workflow"
    assert owners["status"]["id"] == "persistent-feedback"
    assert all("/" not in unit["id"] for unit in specs)


def test_candidate_is_resolved_as_a_composition_of_local_unit_policies():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    selected = module.candidate_unit_composition(
        hierarchy, "selected-context-workflow"
    )
    proof = module.candidate_unit_composition(
        hierarchy, "workflow-with-proof-drawer"
    )
    static = module.candidate_unit_composition(hierarchy, "split-pane")

    assert selected["enabled"] is True
    assert selected["searchMode"] == "bounded-recursive-composition"
    assert selected["rootPolicy"] == "dominant-workflow-stack"
    assert selected["unitPolicies"]["persistent-feedback"] == "shared-horizontal-band"
    assert selected["unitPolicies"]["phase-support"] == "side-active-support"
    assert proof["unitPolicies"]["phase-support"] == "proof-drawer-or-side-support"
    assert static["rootPolicy"] == "legacy-flat"
    assert set(selected["parallelBranches"]) == {
        "project-identity",
        "command-workflow",
        "persistent-feedback",
        "phase-support",
    }


def test_phase_realization_propagates_to_local_layout_units():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "planning"
    )

    realized = module.realize_phase(
        hierarchy, "selected-context-workflow", scenario
    )
    units = {item["id"]: item for item in realized["layoutUnits"]}

    assert units["project-identity"]["realization"] == "trigger-only"
    assert units["command-workflow"]["activeSlots"] == ["command", "workflow"]
    assert units["persistent-feedback"]["activeSlots"] == [
        "project-context",
        "status",
    ]
    assert units["phase-support"]["activeSupportSlots"] == ["server"]
    assert units["phase-support"]["activeSlots"] == ["server"]
    assert units["phase-support"]["triggerSlots"] == ["advanced", "evidence"]
    assert realized["unitComposition"]["unitPolicies"]["persistent-feedback"] == (
        "shared-horizontal-band"
    )


def test_recursive_unit_html_wraps_responsibilities_and_keeps_one_trigger_strip():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "selected-project-default"
    )

    html = module.render_trial_html(
        hierarchy,
        "selected-context-workflow",
        "mcel-realistic",
        scenario,
    )

    assert 'class="flog-unit-tree"' in html
    assert 'data-flog-unit-search-mode="bounded-recursive-composition"' in html
    assert 'data-flog-unit-id="command-workflow"' in html
    assert 'data-flog-unit-id="persistent-feedback"' in html
    assert 'data-flog-unit-policy="shared-horizontal-band"' in html
    assert html.count('class="flog-trigger-strip"') == 1
    assert 'data-flog-trigger-count="4"' in html
    assert 'data-flog-unit-id="project-identity"' in html
    assert 'data-flog-unit-id="phase-support"' in html
    assert (
        '.flog-layout-unit[data-flog-unit-id="persistent-feedback"]'
        in module.TRIAL_CSS
    )
    assert "grid-template-columns: repeat(auto-fit, minmax(0, 1fr));" in module.TRIAL_CSS


def _git_selected_default_unit_measurement(module, *, stacked_feedback=False):
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "selected-project-default"
    )
    realized = module.realize_phase(
        hierarchy, "selected-context-workflow", scenario
    )

    def rect(left, top, right, bottom):
        return {
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
            "area": (right - left) * (bottom - top),
        }

    if stacked_feedback:
        context_rect = rect(0, 860, 1000, 900)
        status_rect = rect(0, 900, 1000, 940)
        feedback_rect = rect(0, 860, 1000, 940)
    else:
        context_rect = rect(0, 860, 500, 920)
        status_rect = rect(500, 860, 1000, 920)
        feedback_rect = rect(0, 860, 1000, 920)

    geometry = {
        "command": rect(0, 0, 1000, 60),
        "workflow": rect(0, 60, 1000, 860),
        "project-context": context_rect,
        "status": status_rect,
        "project-selector": rect(0, 930, 250, 970),
        "server": rect(250, 930, 500, 970),
        "evidence": rect(500, 930, 750, 970),
        "advanced": rect(750, 930, 1000, 970),
    }
    roles = {
        "command": "command",
        "workflow": "focus",
        "project-context": "status",
        "status": "status",
        "project-selector": "navigation",
        "server": "detail",
        "evidence": "evidence",
        "advanced": "detail",
    }
    unit_for_slot = {
        slot: unit["id"]
        for slot, unit in module.layout_unit_slot_map(hierarchy).items()
    }
    nodes = []
    for slot, item_rect in geometry.items():
        state = realized["realizationStates"][slot]
        nodes.append(
            {
                "slot": slot,
                "role": roles[slot],
                "unitId": unit_for_slot[slot],
                "realization": state,
                "rect": item_rect,
            }
        )

    units = [
        {
            "unitId": "command-workflow",
            "role": "primary-work",
            "policy": "command-over-dominant",
            "rect": rect(0, 0, 1000, 860),
        },
        {
            "unitId": "persistent-feedback",
            "role": "persistent-feedback",
            "policy": "shared-horizontal-band",
            "rect": feedback_rect,
        },
    ]
    return hierarchy, realized, {
        "phase": "selected-project-default",
        "geometryFacts": {
            "root": {"clipped": rect(0, 0, 1000, 1000)},
            "clippedCriticalControlCount": 0,
            "hiddenCriticalControlCount": 0,
        },
        "examples": {
            "nodes": nodes,
            "units": units,
            "clippedCriticalControls": [],
            "hiddenCriticalControls": [],
        },
        "classification": {"score": 90, "status": "pass"},
    }


def test_recursive_unit_fit_identifies_the_lowest_failing_feedback_unit():
    module = load_module()
    _, realized, shared = _git_selected_default_unit_measurement(module)
    shared_fit = module.semantic_layout_unit_state_fit(realized, shared)

    _, realized, stacked = _git_selected_default_unit_measurement(
        module, stacked_feedback=True
    )
    stacked_fit = module.semantic_layout_unit_state_fit(realized, stacked)
    stacked_by_id = {item["unitId"]: item for item in stacked_fit["units"]}

    assert shared_fit["hardFailureCount"] == 0
    assert shared_fit["score"] >= 88
    assert stacked_by_id["persistent-feedback"]["hardFailureCount"] == 1
    assert stacked_by_id["command-workflow"]["hardFailureCount"] == 0
    assert any(
        "persistent-feedback violates local policy shared-horizontal-band"
        in reason
        for reason in stacked_fit["hardFailureReasons"]
    )
    assert stacked_fit["state"] == "unitRisk"


def test_recursive_unit_aggregation_retains_worst_branch_and_hard_failures():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    measurements = [
        {
            "phase": "selected-project-default",
            "layoutUnitFit": {
                "score": 96,
                "hardFailureReasons": [],
                "units": [
                    {
                        "unitId": "persistent-feedback",
                        "score": 96,
                        "hardFailureCount": 0,
                    },
                    {
                        "unitId": "command-workflow",
                        "score": 98,
                        "hardFailureCount": 0,
                    },
                ],
            },
        },
        {
            "phase": "recovery",
            "layoutUnitFit": {
                "score": 62,
                "hardFailureReasons": [
                    "phase-support has 1 clipped or hidden active critical control(s)"
                ],
                "units": [
                    {
                        "unitId": "phase-support",
                        "score": 40,
                        "hardFailureCount": 1,
                    },
                    {
                        "unitId": "persistent-feedback",
                        "score": 94,
                        "hardFailureCount": 0,
                    },
                ],
            },
        },
    ]

    fit = module.aggregate_layout_unit_fit(hierarchy, measurements)
    branches = {item["unitId"]: item for item in fit["parallelBranches"]}

    assert fit["worstScore"] == 62
    assert fit["selectedDefaultScore"] == 96
    assert fit["hardFailureCount"] == 1
    assert fit["state"] == "unitRisk"
    assert branches["phase-support"]["worstScore"] == 40
    assert branches["phase-support"]["hardFailureCount"] == 1




def test_recursive_unit_feed_graph_matches_responsibility_ownership():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    audit = module.layout_unit_dataflow_audit(hierarchy)
    edges = {
        (item["sourceUnit"], item["targetUnit"], item["signal"])
        for item in audit["edges"]
    }

    assert audit["state"] == "complete"
    assert audit["missingInputs"] == []
    assert audit["cycles"] == []
    assert audit["duplicateOutputs"] == []
    assert audit["parallelRoots"] == ["project-identity"]
    assert (
        "project-identity",
        "command-workflow",
        "selected-project-scope",
    ) in edges
    assert (
        "command-workflow",
        "persistent-feedback",
        "workflow-state",
    ) in edges
    assert (
        "command-workflow",
        "phase-support",
        "workflow-claim",
    ) in edges



def test_recursive_policy_catalog_exposes_real_local_alternatives():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    catalog = module.layout_unit_policy_catalog(hierarchy)

    assert {item["policy"] for item in catalog["project-identity"]} == {
        "phase-selector-unit",
        "compact-project-rail",
        "selector-overlay",
    }
    assert {item["policy"] for item in catalog["command-workflow"]} == {
        "command-over-dominant",
        "command-inline-header",
        "side-command-rail",
    }
    assert {item["policy"] for item in catalog["persistent-feedback"]} == {
        "shared-horizontal-band",
        "stacked-feedback",
        "workflow-footer-overlay",
    }
    assert {item["policy"] for item in catalog["phase-support"]} == {
        "one-active-plus-triggers",
        "bounded-bottom-drawer",
        "bounded-side-drawer",
        "inline-phase-stage",
        "tabbed-phase-support",
        "sequential-phase-stage",
    }


def test_default_recursive_search_generates_composition_identities_not_legacy_families():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    candidates = module.candidate_specs_for_hierarchy(
        hierarchy, list(module.LAYOUT_CANDIDATES)
    )

    generated = [
        item for item in candidates if item["mode"] == "recursive-composition"
    ]
    shadow = [
        item for item in candidates if item["mode"] == "layout-hint-shadow"
    ]

    assert len(generated) == len(module.LAYOUT_CANDIDATES)
    assert len(shadow) == 1
    assert all(isinstance(item, dict) for item in candidates)
    assert all(item["id"].startswith("compose--") for item in generated)
    assert all(item["renderFamily"] == "recursive-composition" for item in candidates)
    assert all(item["mode"] == "recursive-composition" for item in generated)
    assert shadow[0]["id"].startswith("hint-compiled-default--")
    assert shadow[0]["shadowOnly"] is True
    assert len({item["id"] for item in candidates}) == len(candidates)

    support_policies = {
        item["composition"]["unitPolicies"]["phase-support"]
        for item in generated
    }
    command_policies = {
        item["composition"]["unitPolicies"]["command-workflow"]
        for item in generated
    }
    assert {
        "one-active-plus-triggers",
        "bounded-bottom-drawer",
        "bounded-side-drawer",
        "inline-phase-stage",
    }.issubset(support_policies)
    assert {
        "command-over-dominant",
        "command-inline-header",
        "side-command-rail",
    }.issubset(command_policies)


def test_explicit_legacy_candidate_subset_remains_an_exact_request():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    assert module.candidate_specs_for_hierarchy(
        hierarchy, ["progressive-workflow"]
    ) == ["progressive-workflow"]


def test_generated_composition_drives_html_and_report_identity():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    candidate = module.recursive_composition_candidate_specs(
        hierarchy, max_candidates=1
    )[0]
    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "selected-project-default"
    )

    realized = module.realize_phase(hierarchy, candidate, scenario)
    html = module.render_realized_trial_html(
        realized, candidate, "mcel-realistic"
    )

    assert realized["unitComposition"]["origin"] == "generated-policy-tuple"
    assert (
        realized["unitComposition"]["searchMode"]
        == "generated-bounded-recursive-composition"
    )
    assert realized["unitComposition"]["candidate"] == candidate["id"]
    assert 'trial-recursive-composition' in html
    assert f'data-flog-candidate="{candidate["id"]}"' in html
    assert 'data-flog-candidate-mode="recursive-composition"' in html
    assert 'data-flog-render-family="recursive-composition"' in html
    assert (
        f'data-flog-unit-policy="{candidate["composition"]["unitPolicies"]["phase-support"]}"'
        in html
    )
    assert 'class="flog-root trial-bounded-drawer ' not in html


def test_local_support_policy_scoring_distinguishes_side_and_bottom_geometry():
    module = load_module()

    def rect(left, top, right, bottom):
        return {
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
            "area": (right - left) * (bottom - top),
        }

    root = rect(0, 0, 1000, 1000)
    unit = {
        "activeSupportSlots": ["evidence"],
        "activeSlots": ["evidence"],
        "triggerSlots": ["server", "advanced"],
    }
    records = {
        "evidence": {
            "realization": "full-active",
            "rect": rect(720, 120, 1000, 900),
        }
    }
    side_record = {"rect": rect(720, 120, 1000, 900)}
    bottom_record = {"rect": rect(0, 740, 1000, 940)}

    side_score, _, side_hard = module._phase_support_policy_score(
        "bounded-side-drawer", unit, records, root, side_record
    )
    wrong_bottom_score, _, wrong_bottom_hard = module._phase_support_policy_score(
        "bounded-bottom-drawer", unit, records, root, side_record
    )
    bottom_score, _, bottom_hard = module._phase_support_policy_score(
        "bounded-bottom-drawer", unit, records, root, bottom_record
    )

    assert side_hard is False
    assert bottom_hard is False
    assert wrong_bottom_hard is True
    assert side_score > wrong_bottom_score
    assert bottom_score > wrong_bottom_score


def test_recursive_beam_records_preflight_scores_and_collision_penalties():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    candidates = module.recursive_composition_candidate_specs(
        hierarchy, max_candidates=12
    )

    assert candidates[0]["preflightScore"] >= candidates[-1]["preflightScore"]
    assert all(item["localPolicyPreflight"] for item in candidates)
    assert all(
        item["compositionLabel"].startswith("project-identity=")
        for item in candidates
    )
    assert module._composition_compatibility_penalty(
        {
            "command-workflow": "side-command-rail",
            "phase-support": "bounded-side-drawer",
        }
    ) == 12


def test_painted_ownership_policy_metadata_distinguishes_layout_intent():
    module = load_module()

    assert module.layout_unit_ownership_spec(
        "bounded-side-drawer", "active"
    ) == {
        "mode": "partition",
        "overlayTarget": "",
        "maxOcclusionShare": 0.0,
    }
    assert module.layout_unit_ownership_spec(
        "workflow-footer-overlay", "active"
    ) == {
        "mode": "overlay",
        "overlayTarget": "workflow",
        "maxOcclusionShare": 0.06,
    }
    assert module.layout_unit_ownership_spec(
        "selector-overlay", "active"
    ) == {
        "mode": "overlay",
        "overlayTarget": "workflow",
        "maxOcclusionShare": 0.10,
    }
    assert module.layout_unit_ownership_spec(
        "bounded-bottom-drawer", "trigger-only"
    )["mode"] == "trigger"
    assert module.layout_unit_ownership_spec(
        "bounded-bottom-drawer", "absent"
    )["mode"] == "absent"


def test_recursive_html_emits_shadow_ownership_contracts():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    candidate = {
        "id": "compose--shadow-ownership-test",
        "mode": "recursive-composition",
        "renderFamily": "recursive-composition",
        "compositionLabel": "shadow ownership test",
        "composition": {
            "rootPolicy": "dominant-workflow-stack",
            "unitPolicies": {
                "project-identity": "phase-selector-unit",
                "command-workflow": "command-over-dominant",
                "persistent-feedback": "workflow-footer-overlay",
                "phase-support": "bounded-side-drawer",
            },
        },
    }
    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "selected-project-default"
    )

    realized = module.realize_phase(hierarchy, candidate, scenario)
    html = module.render_realized_trial_html(
        realized, candidate, "mcel-realistic"
    )

    rendered_body = html[html.index("</style>") :]
    feedback_start = rendered_body.index(
        'data-flog-unit-id="persistent-feedback"'
    )
    feedback_section = rendered_body[feedback_start : feedback_start + 900]
    assert 'data-flog-ownership-mode="overlay"' in feedback_section
    assert 'data-flog-overlay-target="workflow"' in feedback_section
    assert 'data-flog-max-occlusion-share="0.06"' in feedback_section

    support_start = rendered_body.index(
        'data-flog-unit-id="phase-support"'
    )
    support_section = rendered_body[support_start : support_start + 900]
    assert 'data-flog-ownership-mode="trigger"' in support_section
    assert 'data-flog-overlay-target=""' in support_section

    evidence_trigger = next(
        line for line in html.splitlines()
        if 'data-flog-trigger-for="evidence"' in line
    )
    assert 'data-flog-ownership-mode="trigger"' in evidence_trigger


def test_measurement_javascript_enforces_exclusive_painted_geometry():
    module = load_module()
    javascript = module.MEASURE_AND_OVERLAY_JS

    assert "paintedOwnershipShadowFor" in javascript
    assert "doc.elementsFromPoint" in javascript
    assert 'mode: "exclusive-enforced"' in javascript
    assert "enforced: true" in javascript
    assert "controlInterceptionShadowFor" in javascript
    assert "blockedCriticalControls" in javascript
    assert "partitionOwnershipViolation" in javascript
    assert "overlayBudgetViolations" in javascript
    assert "effectiveVisibleShare" in javascript
    assert "undeclaredPartitionOverlapShare" in javascript
    assert "partitionOverlapCellShare" in javascript
    assert "foreignInterceptedPointCount" in javascript
    assert "pointerEventsNonePassThroughPointCount" in javascript
    assert 'data-flog-control-proxy-for' in javascript

    scoring_block = javascript[
        javascript.index("  let score = 100;") :
        javascript.index("  score = Math.max", javascript.index("  let score = 100;"))
    ]
    hard_gate_block = javascript[
        javascript.index("  const hardGeometryGatePassed") :
        javascript.index("  if (preferredFamilyMatch", javascript.index("  const hardGeometryGatePassed"))
    ]
    assert "blockedCriticalControls.length * 12" in scoring_block
    assert "partitionOwnershipViolation" in scoring_block
    assert "overlayBudgetViolations.length * 16" in scoring_block
    assert "blockedCriticalControls.length === 0" in hard_gate_block
    assert "!partitionOwnershipViolation" in hard_gate_block
    assert "overlayBudgetViolations.length === 0" in hard_gate_block



def test_shadow_hit_testing_distinguishes_self_owned_from_foreign_interception():
    module = load_module()
    javascript = module.MEASURE_AND_OVERLAY_JS

    control_block = javascript[
        javascript.index("  function controlInterceptionShadowFor(el) {") :
        javascript.index("  function isCriticalControl(el) {")
    ]

    assert "controlOwner === targetOwner" in control_block
    assert 'outcome: "foreign-intercepted"' in control_block
    assert '"self-owned"' in control_block
    assert '"no-pointer-target"' in control_block
    assert '"pointer-events-none-pass-through"' in control_block
    assert "interceptedPointCount: foreignInterceptedPointCount" in control_block
    assert "fullyIntercepted: fullyForeignIntercepted" in control_block
    assert "partiallyIntercepted: partiallyForeignIntercepted" in control_block


def test_shadow_partition_overlap_is_reported_separately_from_overlay_budget():
    module = load_module()
    javascript = module.MEASURE_AND_OVERLAY_JS

    ownership_block = javascript[
        javascript.index("  function paintedOwnershipShadowFor(") :
        javascript.index("  function associatedLabelsForControl(")
    ]

    assert "partitionClaimsForeignCell" in ownership_block
    assert 'topOwner.record.ownershipMode === "partition"' in ownership_block
    assert "topOwner.record.unitId !== lower.record.unitId" in ownership_block
    assert "undeclaredPartitionOverlapArea" in ownership_block
    assert "doubleClaimedArea - declaredOverlayArea" in ownership_block
    assert "undeclaredPartitionOverlapMatrix" in ownership_block


def test_selection_row_surfaces_shadow_diagnostics_without_changing_rank():
    module = load_module()
    item = {
        "hierarchyId": "git-tools-workflow-workbench",
        "viewportProfile": "desktop",
        "candidate": "compose--shadow",
        "classification": {
            "score": 92,
            "status": "pass",
            "selectionScore": 92,
            "positiveReasons": [],
            "failureReasons": [],
            "reviewNotes": [],
        },
        "geometryFacts": {
            "focusShare": 0.71,
            "desiredFocusShare": 0.68,
            "paintedOwnershipMode": "shadow-only",
            "paintedOwnershipEnforced": False,
            "paintedOwnershipShadow": {
                "effectiveFocusShare": 0.49,
                "focusOccludedShare": 0.22,
                "exclusiveOwnedShare": 0.78,
                "doubleClaimedShare": 0.27,
                "declaredOverlayShare": 0.18,
                "undeclaredPartitionOverlapShare": 0.09,
                "partitionOverlapCellShare": 0.08,
                "partitionOverlapByUnit": [
                    {
                        "unitId": "phase-support",
                        "area": 800,
                        "shareOfRoot": 0.08,
                    }
                ],
                "undeclaredPartitionOverlapMatrix": [
                    {
                        "occludedSlot": "workflow",
                        "occludingSlot": "server",
                        "shareOfRoot": 0.08,
                        "undeclaredPartitionOverlap": True,
                    }
                ],
                "overlayBudgetExceeded": [
                    {
                        "unitId": "phase-support",
                        "occlusionShare": 0.18,
                        "maxOcclusionShare": 0.0,
                    }
                ],
                "overlapMatrix": [
                    {
                        "occludedSlot": "workflow",
                        "occludingSlot": "server",
                        "shareOfRoot": 0.18,
                        "shareOfOccludedNode": 0.25,
                    }
                ],
            },
            "interceptedCriticalControlCountShadow": 1,
            "partiallyInterceptedCriticalControlCountShadow": 2,
            "foreignInterceptedCriticalControlCountShadow": 2,
            "controlInterceptionOutcomeTotalsShadow": {
                "actionable": 20,
                "selfOwned": 22,
                "foreignIntercepted": 3,
                "noPointerTarget": 0,
                "pointerEventsNonePassThrough": 1,
                "unownedPointerTarget": 0,
            },
        },
        "phaseFit": {},
        "layoutUnitFit": {},
        "unitComposition": {},
        "snapshots": {},
    }

    row = module.measurement_selection_row(
        item,
        selection_state="bestPassingCandidate",
        highest_scoring=item,
    )

    assert row["score"] == 92
    assert row["selectionScore"] == 92
    assert row["focusShare"] == 0.71
    assert row["effectiveFocusShareShadow"] == 0.49
    assert row["focusOccludedShareShadow"] == 0.22
    assert row["doubleClaimedShareShadow"] == 0.27
    assert row["undeclaredPartitionOverlapShareShadow"] == 0.09
    assert row["partitionOverlapCellShareShadow"] == 0.08
    assert row["foreignInterceptedCriticalControlCountShadow"] == 2
    assert row["controlInterceptionOutcomeTotalsShadow"]["selfOwned"] == 22
    assert row["interceptedCriticalControlCountShadow"] == 1
    assert row["paintedOwnershipEnforced"] is False
    assert row["paintedOwnershipOverlapMatrixShadow"][0][
        "occludingSlot"
    ] == "server"


def test_stage_b_css_partitions_recursive_support_and_contains_commands():
    module = load_module()
    css = module.TRIAL_CSS

    assert 'grid-template-areas:\n    "main support"\n    "feedback support"' in css
    assert 'grid-template-areas: "identity feedback"' in css
    assert (
        '.flog-layout-unit[data-flog-unit-id="phase-support"]'
        '[data-flog-unit-has-active-support="true"]:not('
        '[data-flog-ownership-mode="overlay"])'
    ) in css
    assert "position: static !important;" in css
    assert 'grid-auto-columns: minmax(0, 1fr);' in css
    assert '.flog-node[data-flog-role="command"] > .node-body' in css


def test_record_area_share_prefers_effective_painted_geometry():
    module = load_module()
    root = {"area": 1000}
    record = {
        "rect": {"area": 800},
        "effectiveVisibleShare": 0.37,
    }
    unit_record = {
        "rect": {"area": 900},
        "effectiveOwnedShare": 0.22,
    }

    assert module._record_area_share(record, root) == 0.37
    assert module._record_area_share(unit_record, root) == 0.22


def test_phase_fit_hard_fails_blocked_controls_and_partition_overlap():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "operator-control-surface"
    )
    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 600,
        "width": 1000,
        "height": 600,
        "area": 600000,
    }
    records = []
    for index, node in enumerate(hierarchy["nodes"]):
        share = 0.50 if node["slot"] == hierarchy["focusSlot"] else 0.08
        records.append(
            {
                "slot": node["slot"],
                "role": node.get("role", "support"),
                "realization": "full-active",
                "effectiveVisibleShare": share,
                "rect": {
                    "left": 0,
                    "top": index * 50,
                    "right": 1000,
                    "bottom": index * 50 + max(24, share * 600),
                    "width": 1000,
                    "height": max(24, share * 600),
                    "area": share * root["area"],
                },
            }
        )
    measurement = {
        "geometryFacts": {
            "root": {"clipped": root},
            "blockedCriticalControlCount": 1,
            "undeclaredPartitionOverlapShare": 0.05,
            "paintedOwnershipEnforced": True,
            "paintedOwnership": {"overlayBudgetExceeded": []},
        },
        "examples": {"nodes": records},
        "classification": {
            "score": 90,
            "geometryScore": 90,
            "status": "pass",
        },
    }

    fit = module.semantic_phase_realization_fit(hierarchy, measurement)

    assert fit["hardFailureCount"] >= 2
    assert any("foreign-intercepted" in reason for reason in fit["riskReasons"])
    assert any("undeclared partition overlap" in reason for reason in fit["riskReasons"])


def test_markdown_report_marks_painted_ownership_as_enforced(tmp_path):
    module = load_module()
    measurement = {
        "hierarchyId": "git-tools-workflow-workbench",
        "viewportProfile": "desktop",
        "candidate": "compose--enforced",
        "focusSlot": "workflow",
        "classification": {
            "score": 68,
            "status": "fail",
            "positiveReasons": [],
            "failureReasons": ["undeclared partition overlap"],
            "reviewNotes": [],
            "warnings": ["partition ownership overlap exceeds tolerance"],
        },
        "geometryFacts": {
            "rawFocusShare": 0.71,
            "focusShare": 0.49,
            "effectiveFocusShare": 0.49,
            "focusOccludedShare": 0.22,
            "desiredFocusShare": 0.68,
            "paintedOwnershipMode": "exclusive-enforced",
            "paintedOwnershipEnforced": True,
            "paintedOwnership": {
                "rawFocusShare": 0.71,
                "effectiveFocusShare": 0.49,
                "focusOccludedShare": 0.22,
                "exclusiveOwnedShare": 0.78,
                "doubleClaimedShare": 0.27,
                "declaredOverlayShare": 0.18,
                "undeclaredPartitionOverlapShare": 0.09,
                "partitionOverlapCellShare": 0.08,
                "partitionOverlapByUnit": [
                    {
                        "unitId": "phase-support",
                        "area": 800,
                        "shareOfRoot": 0.08,
                    }
                ],
                "undeclaredPartitionOverlapMatrix": [],
                "overlapMatrix": [
                    {
                        "occludedSlot": "workflow",
                        "occludingSlot": "server",
                        "shareOfRoot": 0.18,
                        "shareOfOccludedNode": 0.25,
                    }
                ],
                "overlayBudgetExceeded": [],
            },
            "blockedCriticalControlCount": 1,
            "interceptedCriticalControlCount": 1,
            "partiallyInterceptedCriticalControlCount": 2,
            "foreignInterceptedCriticalControlCount": 2,
            "controlInterceptionOutcomeTotals": {
                "actionable": 20,
                "selfOwned": 22,
                "foreignIntercepted": 3,
                "noPointerTarget": 0,
                "pointerEventsNonePassThrough": 1,
                "unownedPointerTarget": 0,
            },
        },
        "snapshots": {},
        "phaseMeasurements": [],
        "humanLoop": {
            "proved": [],
            "inferred": [],
            "unknowns": [],
        },
    }
    report = {
        "generatedAt": "2026-07-10T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["compose--enforced"],
        "candidateCatalogByHierarchy": {},
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": [],
        "hierarchies": [],
        "semanticContracts": [],
        "bestByHierarchyViewport": [],
        "rollups": [],
        "measurements": [measurement],
    }

    _, markdown_path = module.write_reports(report, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "Painted ownership: `exclusive-enforced` (enforced=`True`)" in markdown
    assert "Stage B enforces exclusive painted ownership" in markdown
    assert "Painted ownership diagnostics (enforced)" in markdown
    assert "Effective focus share: `0.4900`" in markdown
    assert "Undeclared partition overlap share: `0.0900`" in markdown
    assert "Blocked critical controls: `1`" in markdown
    assert "Critical controls with any foreign interception: `2`" in markdown
    assert "selfOwned=`22` foreignIntercepted=`3`" in markdown
    assert "Undeclared partition overlap: `phase-support` rootShare=`0.0800`" in markdown
    assert "`workflow` occluded by `server`" in markdown

def test_stage_c_uses_phase_relative_active_presentation_density():
    module = load_module()
    javascript = module.MEASURE_AND_OVERLAY_JS

    assert 'const rootUnclaimedAreaRatio' in javascript
    assert 'const activePresentationUnclaimedRatio' in javascript
    assert 'const unclaimedAreaRatio = activePresentationUnclaimedRatio;' in javascript
    assert 'activePresentationMode = "focus-layout-unit"' in javascript
    assert 'intentionalInactiveRootRatio' in javascript
    assert 'score -= Math.round(unclaimedAreaRatio * 54);' in javascript
    assert 'score -= Math.round(rootUnclaimedAreaRatio * 54);' not in javascript


def test_phase_fit_reports_unrounded_scores_and_dominant_headroom():
    module = load_module()
    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 600,
        "width": 1000,
        "height": 600,
        "area": 600000,
    }
    hierarchy = {
        "id": "margin-test",
        "focusSlot": "workflow",
        "desiredFocusShare": 0.64,
        "minFocusShare": 0.50,
        "maxFocusShare": 0.90,
        "roleContract": {"deferableSlots": []},
        "nodes": [
            {"slot": "workflow", "role": "focus"},
            {"slot": "status", "role": "status", "minVisibleShare": 0.02},
        ],
        "phaseScenarios": [
            {
                "phase": "default",
                "dominantSlot": "workflow",
                "requiredSlots": ["status"],
                "activeSupportSlots": [],
                "collapsedSlots": [],
                "minDominantShare": 0.50,
                "targetDominantShare": 0.64,
                "maxInactiveTax": 0.12,
                "weight": 1.0,
            }
        ],
    }
    measurement = {
        "phase": "default",
        "geometryFacts": {
            "root": {"clipped": root},
            "clippedCriticalControlCount": 0,
            "hiddenCriticalControlCount": 0,
            "blockedCriticalControlCount": 0,
            "undeclaredPartitionOverlapShare": 0,
            "paintedOwnership": {"overlayBudgetExceeded": []},
        },
        "examples": {
            "nodes": [
                {
                    "slot": "workflow",
                    "realization": "full-active",
                    "effectiveVisibleShare": 0.5375,
                    "rect": {**root, "area": 322500},
                },
                {
                    "slot": "status",
                    "realization": "persistent",
                    "effectiveVisibleShare": 0.03,
                    "rect": {
                        "left": 0,
                        "top": 570,
                        "right": 1000,
                        "bottom": 588,
                        "width": 1000,
                        "height": 18,
                        "area": 18000,
                    },
                },
            ]
        },
        "classification": {
            "score": 91,
            "geometryScore": 91,
            "status": "pass",
        },
        "snapshots": {"viewport": "margin-test.png"},
    }

    fit = module.semantic_phase_realization_fit(hierarchy, [measurement])

    assert fit["score"] == round(fit["policyScoreRaw"])
    assert round(fit["worstDominantHeadroom"], 4) == 0.0375
    assert round(fit["selectedDefaultHeadroom"], 4) == 0.0375
    assert fit["scoreVariance"] == 0
    assert round(fit["phases"][0]["dominantHeadroom"], 4) == 0.0375
    assert isinstance(fit["phases"][0]["rawScore"], float)


def test_margin_aware_candidate_ranking_breaks_integer_score_ties_in_declared_order():
    module = load_module()

    def measurement(
        candidate,
        *,
        headroom,
        unit_score=96.0,
        overlay=False,
        variance=2.0,
        occupancy=0.50,
        preflight=90,
    ):
        policy = "workflow-footer-overlay" if overlay else "shared-horizontal-band"
        return {
            "hierarchyId": "git-tools-workflow-workbench",
            "viewportProfile": "desktop",
            "candidate": candidate,
            "classification": {
                "score": 92,
                "selectionScore": 92,
                "selectionScoreRaw": 92.25,
                "status": "pass",
                "positiveReasons": [],
                "failureReasons": [],
                "reviewNotes": [],
            },
            "phaseFit": {
                "worstDominantHeadroom": headroom,
                "scoreVariance": variance,
                "phases": [],
            },
            "layoutUnitFit": {
                "worstScore": round(unit_score),
                "worstScoreRaw": unit_score,
            },
            "unitComposition": {
                "preflightScore": preflight,
                "unitPolicies": {
                    "persistent-feedback": policy,
                    "phase-support": "bounded-side-drawer",
                },
            },
            "geometryFacts": {
                "unclaimedAreaRatio": 0.10,
                "focusShare": 0.60,
                "desiredFocusShare": 0.60,
                "usefulFocusOccupancy": occupancy,
                "companionProximityScore": 1.0,
            },
            "phaseMeasurements": [
                {"geometryFacts": {"usefulFocusOccupancy": occupancy}}
            ],
            "snapshots": {"viewport": f"{candidate}.png"},
        }

    candidates = [
        measurement("lower-headroom", headroom=0.01, unit_score=100),
        measurement(
            "higher-headroom-overlay",
            headroom=0.02,
            unit_score=96,
            overlay=True,
            variance=0.5,
            occupancy=0.9,
            preflight=100,
        ),
        measurement(
            "higher-headroom-partition",
            headroom=0.02,
            unit_score=96,
            overlay=False,
            variance=0.5,
            occupancy=0.9,
            preflight=100,
        ),
    ]

    rows = module.best_by_hierarchy_viewport_rows(candidates)

    assert rows[0]["candidate"] == "higher-headroom-partition"
    evidence = rows[0]["selectionMarginEvidence"]
    assert evidence["worstPhaseHeadroom"] == 0.02
    assert evidence["overlayPolicyCount"] == 0
    assert evidence["worstUnitScore"] == 96


def test_margin_aware_sort_uses_variance_occupancy_and_preflight_after_hard_margins():
    module = load_module()

    def item(candidate, *, variance, occupancy, preflight):
        return {
            "candidate": candidate,
            "classification": {"score": 92, "status": "pass"},
            "phaseFit": {
                "worstDominantHeadroom": 0.02,
                "scoreVariance": variance,
            },
            "layoutUnitFit": {"worstScoreRaw": 96.0},
            "unitComposition": {
                "preflightScore": preflight,
                "unitPolicies": {
                    "persistent-feedback": "shared-horizontal-band"
                },
            },
            "geometryFacts": {"usefulFocusOccupancy": occupancy},
            "phaseMeasurements": [
                {"geometryFacts": {"usefulFocusOccupancy": occupancy}}
            ],
        }

    ordered = sorted(
        [
            item("high-variance", variance=2.0, occupancy=0.99, preflight=100),
            item("low-occupancy", variance=1.0, occupancy=0.50, preflight=100),
            item("low-preflight", variance=1.0, occupancy=0.75, preflight=80),
            item("winner", variance=1.0, occupancy=0.75, preflight=95),
        ],
        key=module.measurement_ranking_sort_key,
    )

    assert [entry["candidate"] for entry in ordered] == [
        "winner",
        "low-preflight",
        "low-occupancy",
        "high-variance",
    ]


def test_stage_c_report_exposes_density_and_margin_evidence(tmp_path):
    module = load_module()
    measurement = {
        "hierarchyId": "margin-report",
        "viewportProfile": "desktop",
        "candidate": "compose--margin",
        "focusSlot": "workflow",
        "classification": {
            "score": 92,
            "selectionScoreRaw": 92.375,
            "status": "pass",
            "positiveReasons": [],
            "failureReasons": [],
            "reviewNotes": [],
            "warnings": [],
        },
        "geometryFacts": {
            "unclaimedAreaRatio": 0.04,
            "activePresentationUnclaimedRatio": 0.04,
            "rootUnclaimedAreaRatio": 0.64,
            "intentionalInactiveRootRatio": 0.60,
            "accidentalUnclaimedRootRatio": 0.04,
            "activePresentationMode": "focus-layout-unit",
            "activePresentationUnitId": "project-identity",
            "activePresentationOccupancy": 0.96,
            "focusShare": 0.28,
            "desiredFocusShare": 0.20,
            "usefulFocusOccupancy": 0.55,
        },
        "phaseFit": {
            "score": 93,
            "policyScoreRaw": 92.875,
            "meanScoreRaw": 94.0,
            "worstScoreRaw": 91.5,
            "scoreVariance": 1.25,
            "worstDominantHeadroom": 0.01,
            "meanDominantHeadroom": 0.05,
            "selectedDefaultHeadroom": 0.08,
            "state": "strongPhaseFit",
            "phases": [
                {
                    "phase": "planning",
                    "score": 92,
                    "rawScore": 91.5,
                    "dominantShare": 0.57,
                    "minDominantShare": 0.56,
                    "dominantHeadroom": 0.01,
                }
            ],
            "positiveReasons": [],
            "riskReasons": [],
        },
        "layoutUnitFit": {
            "worstScore": 96,
            "worstScoreRaw": 95.75,
            "parallelBranches": [],
        },
        "unitComposition": {
            "preflightScore": 98,
            "unitPolicies": {
                "persistent-feedback": "shared-horizontal-band"
            },
        },
        "phaseMeasurements": [],
        "snapshots": {},
        "humanLoop": {"proved": [], "inferred": [], "unknowns": []},
    }
    row = module.measurement_selection_row(
        measurement,
        selection_state="bestPassingCandidate",
        highest_scoring=measurement,
    )
    report = {
        "generatedAt": "2026-07-10T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "densityScoringMode": "phase-relative-active-presentation",
        "candidateRankingMode": "margin-aware-composition-tiebreak",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": ["compose--margin"],
        "candidateCatalogByHierarchy": {},
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": [],
        "hierarchies": [],
        "semanticContracts": [],
        "bestByHierarchyViewport": [row],
        "rollups": [],
        "measurements": [measurement],
    }

    _, markdown_path = module.write_reports(report, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "Density scoring: `phase-relative-active-presentation`" in markdown
    assert "Candidate ranking: `margin-aware-composition-tiebreak`" in markdown
    assert "Active-presentation unclaimed ratio: `0.0400`" in markdown
    assert "Root-wide unclaimed ratio (diagnostic): `0.6400`" in markdown
    assert "Intentional inactive root ratio: `0.6000`" in markdown
    assert "Margin-aware tie-break:" in markdown
    assert "headroom=`+0.0100`" in markdown




def test_stage_d_support_policies_declare_distinct_realization_placements():
    module = load_module()

    def candidate(policy):
        return {
            "id": f"compose--{policy}",
            "mode": "recursive-composition",
            "renderFamily": "recursive-composition",
            "composition": {
                "rootPolicy": "dominant-workflow-stack",
                "unitPolicies": {"phase-support": policy},
            },
        }

    assert module.candidate_phase_policy(
        candidate("one-active-plus-triggers")
    )["activeSupportPlacement"] == "neutral-phase-stage"
    assert module.candidate_phase_policy(
        candidate("bounded-side-drawer")
    )["activeSupportPlacement"] == "side-drawer"
    assert module.candidate_phase_policy(
        candidate("bounded-bottom-drawer")
    )["activeSupportPlacement"] == "bottom-drawer"
    assert module.candidate_phase_policy(
        candidate("inline-phase-stage")
    )["activeSupportPlacement"] == "inline-stage"


def test_stage_d_css_reserves_distinct_tracks_for_declared_local_policies():
    module = load_module()
    css = module.TRIAL_CSS

    assert "unit-policy-one-active-plus-triggers" in css
    assert '"main"\n    "support"\n    "feedback"' in css
    assert "unit-policy-bounded-bottom-drawer" in css
    assert '"main"\n    "feedback"\n    "support"' in css
    assert "unit-policy-inline-phase-stage" in css
    assert '"support"\n    "main"\n    "feedback"' in css
    assert "unit-policy-phase-selector-unit" in css
    assert "unit-policy-compact-project-rail" in css
    assert "minmax(300px, 33fr)" in css
    assert "minmax(210px, 21fr)" in css


def _stage_d_rendered_measurement(
    candidate,
    *,
    support_left=760.0,
    support_top=120.0,
    support_width=240.0,
    support_height=760.0,
    preflight=90,
):
    root = {
        "left": 0.0,
        "top": 0.0,
        "right": 1000.0,
        "bottom": 1000.0,
        "width": 1000.0,
        "height": 1000.0,
        "area": 1000000.0,
    }
    workflow = {
        "left": 0.0,
        "top": 120.0,
        "right": support_left,
        "bottom": 880.0,
        "width": support_left,
        "height": 760.0,
        "area": support_left * 760.0,
    }
    support = {
        "left": support_left,
        "top": support_top,
        "right": support_left + support_width,
        "bottom": support_top + support_height,
        "width": support_width,
        "height": support_height,
        "area": support_width * support_height,
    }
    phase = {
        "phase": "planning",
        "focusSlot": "workflow",
        "realizationStates": {
            "workflow": "full-active",
            "server": "full-active",
            "evidence": "compact-trigger",
        },
        "geometryFacts": {"root": {"clipped": root}},
        "examples": {
            "nodes": [
                {
                    "slot": "workflow",
                    "realization": "full-active",
                    "ownershipMode": "partition",
                    "unitId": "command-workflow",
                    "unitRole": "command-workflow",
                    "rect": workflow,
                    "effectiveVisibleShare": workflow["area"] / root["area"],
                },
                {
                    "slot": "server",
                    "realization": "full-active",
                    "ownershipMode": "partition",
                    "unitId": "phase-support",
                    "unitRole": "phase-support",
                    "rect": support,
                    "effectiveVisibleShare": support["area"] / root["area"],
                },
            ],
            "units": [
                {
                    "unitId": "command-workflow",
                    "role": "command-workflow",
                    "realization": "active",
                    "ownershipMode": "partition",
                    "activeSlots": ["workflow"],
                    "activeSupportSlots": [],
                    "triggerSlots": [],
                    "rect": workflow,
                },
                {
                    "unitId": "phase-support",
                    "role": "phase-support",
                    "realization": "active",
                    "ownershipMode": "partition",
                    "activeSlots": ["server"],
                    "activeSupportSlots": ["server"],
                    "triggerSlots": ["evidence"],
                    "rect": support,
                },
            ],
        },
    }
    return {
        "hierarchyId": "git-tools-workflow-workbench",
        "viewportProfile": "desktop",
        "candidate": candidate,
        "candidateMode": "recursive-composition",
        "classification": {
            "score": 96,
            "selectionScore": 96,
            "selectionScoreRaw": 95.8,
            "status": "pass",
            "positiveReasons": [],
            "failureReasons": [],
            "reviewNotes": [],
        },
        "phaseFit": {
            "worstDominantHeadroom": 0.02,
            "scoreVariance": 0.5,
            "phases": [],
        },
        "layoutUnitFit": {"worstScoreRaw": 96.0},
        "unitComposition": {
            "preflightScore": preflight,
            "unitPolicies": {
                "phase-support": candidate.rsplit("--", 1)[-1],
            },
        },
        "geometryFacts": {
            "root": {"clipped": root},
            "focusShare": workflow["area"] / root["area"],
            "desiredFocusShare": 0.56,
            "usefulFocusOccupancy": 0.6,
            "unclaimedAreaRatio": 0.0,
        },
        "phaseMeasurements": [phase],
        "snapshots": {"viewport": f"{candidate}.png"},
    }


def test_stage_d_fingerprint_ignores_policy_names_and_tolerates_subpixel_jitter():
    module = load_module()
    first = _stage_d_rendered_measurement(
        "compose--support-side",
        support_left=760.0,
    )
    alias = _stage_d_rendered_measurement(
        "compose--support-triggers",
        support_left=760.2,
    )
    distinct = _stage_d_rendered_measurement(
        "compose--support-bottom",
        support_left=0.0,
        support_top=780.0,
        support_width=1000.0,
        support_height=200.0,
    )

    first_fingerprint = module.measurement_rendered_policy_fingerprint(first)
    alias_fingerprint = module.measurement_rendered_policy_fingerprint(alias)
    distinct_fingerprint = module.measurement_rendered_policy_fingerprint(distinct)

    assert first_fingerprint
    assert first_fingerprint == alias_fingerprint
    assert distinct_fingerprint != first_fingerprint


def test_stage_d_deduplicates_equivalent_compositions_before_ranking():
    module = load_module()
    representative = _stage_d_rendered_measurement(
        "compose--support-side",
        preflight=98,
    )
    alias = _stage_d_rendered_measurement(
        "compose--support-triggers",
        preflight=92,
    )
    distinct = _stage_d_rendered_measurement(
        "compose--support-bottom",
        support_left=0.0,
        support_top=780.0,
        support_width=1000.0,
        support_height=200.0,
        preflight=95,
    )

    measurements = [alias, distinct, representative]
    groups = module.annotate_rendered_policy_equivalence(measurements)
    rows = module.best_by_hierarchy_viewport_rows(measurements)

    assert len(groups) == 1
    assert groups[0]["representative"] == "compose--support-side"
    assert groups[0]["equivalentAliases"] == ["compose--support-triggers"]
    assert alias["renderedEquivalenceExcludedFromRanking"] is True
    assert representative["renderedEquivalenceExcludedFromRanking"] is False
    assert rows[0]["candidate"] == "compose--support-side"
    assert rows[0]["renderedEquivalentAliases"] == [
        "compose--support-triggers"
    ]


def test_stage_d_report_surfaces_rendered_equivalence_diagnostics(tmp_path):
    module = load_module()
    representative = _stage_d_rendered_measurement(
        "compose--support-side",
        preflight=98,
    )
    alias = _stage_d_rendered_measurement(
        "compose--support-triggers",
        preflight=92,
    )
    measurements = [representative, alias]
    groups = module.annotate_rendered_policy_equivalence(measurements)
    rows = module.best_by_hierarchy_viewport_rows(measurements)

    report = {
        "generatedAt": "2026-07-10T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "densityScoringMode": "phase-relative-active-presentation",
        "candidateRankingMode": "rendered-equivalence-deduplicated-margin-ranking",
        "renderedPolicyFingerprintVersion": module.RENDERED_POLICY_FINGERPRINT_VERSION,
        "renderedPolicyFingerprintBins": module.RENDERED_POLICY_FINGERPRINT_BINS,
        "renderedPolicyEquivalenceGroups": groups,
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": [],
        "candidateCatalogByHierarchy": {},
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": [],
        "hierarchies": [],
        "semanticContracts": [],
        "bestByHierarchyViewport": rows,
        "rollups": [],
        "measurements": measurements,
    }

    _, markdown_path = module.write_reports(report, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "Rendered-policy fingerprint:" in markdown
    assert "## Rendered-policy equivalence groups" in markdown
    assert "compose--support-triggers" in markdown
    assert "Policy realization alias in `phase-support`" in markdown


def _stage_e_phase_fixture(module, dominant_share: float):
    root = {
        "left": 0,
        "top": 0,
        "right": 1000,
        "bottom": 1000,
        "width": 1000,
        "height": 1000,
        "area": 1_000_000,
    }
    hierarchy = {
        "id": "stage-e-floor",
        "focusSlot": "workflow",
        "desiredFocusShare": 0.64,
        "minFocusShare": 0.56,
        "maxFocusShare": 0.90,
        "roleContract": {"deferableSlots": []},
        "nodes": [
            {"slot": "workflow", "role": "focus"},
            {"slot": "status", "role": "status", "minVisibleShare": 0.02},
        ],
        "phaseScenarios": [
            {
                "phase": "planning",
                "dominantSlot": "workflow",
                "requiredSlots": ["status"],
                "activeSupportSlots": [],
                "collapsedSlots": [],
                "minDominantShare": 0.56,
                "targetDominantShare": 0.64,
                "maxInactiveTax": 0.12,
                "weight": 1.0,
            }
        ],
    }
    measurement = {
        "phase": "planning",
        "geometryFacts": {
            "root": {"clipped": root},
            "clippedCriticalControlCount": 0,
            "hiddenCriticalControlCount": 0,
            "blockedCriticalControlCount": 0,
            "undeclaredPartitionOverlapShare": 0,
            "paintedOwnership": {"overlayBudgetExceeded": []},
        },
        "examples": {
            "nodes": [
                {
                    "slot": "workflow",
                    "realization": "full-active",
                    "effectiveVisibleShare": dominant_share,
                    "rect": {
                        **root,
                        "height": dominant_share * 1000,
                        "bottom": dominant_share * 1000,
                        "area": dominant_share * 1_000_000,
                    },
                },
                {
                    "slot": "status",
                    "realization": "persistent",
                    "effectiveVisibleShare": 0.03,
                    "rect": {
                        "left": 0,
                        "top": 970,
                        "right": 1000,
                        "bottom": 1000,
                        "width": 1000,
                        "height": 30,
                        "area": 30_000,
                    },
                },
            ]
        },
        "classification": {
            "score": 95,
            "geometryScore": 95,
            "status": "pass",
            "warnings": [],
            "positiveReasons": [],
            "failureReasons": [],
            "reviewNotes": [],
        },
        "snapshots": {"viewport": "stage-e.png"},
    }
    return hierarchy, measurement


def test_stage_e_absolute_floor_helper_handles_below_exact_and_tolerance():
    module = load_module()
    tolerance = module.PHASE_SHARE_FLOOR_TOLERANCE

    below = module.phase_share_floor_check(0.56 - tolerance - 0.0001, 0.56)
    within_tolerance = module.phase_share_floor_check(0.56 - tolerance / 2, 0.56)
    exact = module.phase_share_floor_check(0.56, 0.56)
    above = module.phase_share_floor_check(0.561, 0.56)

    assert below["met"] is False
    assert below["headroom"] < 0
    assert within_tolerance["met"] is True
    assert within_tolerance["rawHeadroom"] < 0
    assert within_tolerance["headroom"] == 0
    assert exact["met"] is True
    assert exact["headroom"] == 0
    assert above["met"] is True
    assert round(above["headroom"], 4) == 0.001


def test_stage_e_phase_aggregation_hard_fails_any_real_floor_shortfall():
    module = load_module()
    hierarchy, measurement = _stage_e_phase_fixture(module, 0.5142)

    fit = module.semantic_phase_realization_fit(hierarchy, [measurement])
    phase = fit["phases"][0]
    expected = module.phase_share_floor_failure_reason(
        "planning",
        "workflow",
        module.phase_share_floor_check(0.5142, 0.56),
    )

    assert phase["dominantFloorMet"] is False
    assert round(phase["dominantRawHeadroom"], 4) == -0.0458
    assert round(phase["dominantHeadroom"], 4) == -0.0458
    assert phase["hardFailure"] is True
    assert phase["score"] <= 68
    assert expected in phase["hardFailureReasons"]
    assert expected in fit["hardFailureReasons"]
    assert fit["phaseFloorFailureCount"] == 1


def test_stage_e_tolerance_pass_never_reports_negative_ranked_headroom():
    module = load_module()
    share = 0.56 - (module.PHASE_SHARE_FLOOR_TOLERANCE / 2)
    hierarchy, measurement = _stage_e_phase_fixture(module, share)

    fit = module.semantic_phase_realization_fit(hierarchy, [measurement])
    phase = fit["phases"][0]

    assert phase["dominantFloorMet"] is True
    assert phase["dominantRawHeadroom"] < 0
    assert phase["dominantHeadroom"] == 0
    assert phase["hardFailure"] is False
    assert fit["hardFailureCount"] == 0
    assert fit["phaseFloorFailureCount"] == 0
    assert fit["worstDominantHeadroom"] == 0


def test_stage_e_browser_and_aggregate_share_one_floor_contract():
    module = load_module()
    javascript = module.MEASURE_AND_OVERLAY_JS

    assert 'data-flog-phase-floor-tolerance' in javascript
    assert 'function shareFloorGate(actual, floor)' in javascript
    assert 'const focusMeetsMinimum = focusFloorGate.met;' in javascript
    assert 'if (!focusMeetsMinimum) warnings.push(focusFloorFailureReason);' in javascript
    assert 'focusMeetsMinimum &&' in javascript
    assert 'if (!focusMeetsMinimum) score -= 16;' in javascript
    assert 'focusShare >= minFocusShare &&' not in javascript
    assert 'if (focusShare < minFocusShare)' not in javascript


def test_stage_e_aggregate_status_excludes_floor_miss_from_passing_selection():
    module = load_module()
    hierarchy, phase_measurement = _stage_e_phase_fixture(module, 0.5142)
    aggregate = {
        **phase_measurement,
        "phaseMeasurements": [phase_measurement],
        "contractFit": {
            "score": 95,
            "rawScore": 95,
            "state": "strongContractFit",
            "hardRiskCount": 0,
            "softRiskCount": 0,
            "positiveReasons": [],
            "riskReasons": [],
            "presentationSetReasons": [],
            "hardRiskReasons": [],
            "contractLimits": [],
        },
        "affordanceFit": {
            "score": 95,
            "rawScore": 95,
            "state": "strongAffordanceFit",
            "hardMissCount": 0,
            "missedAffordanceCount": 0,
            "positiveReasons": [],
            "riskReasons": [],
            "hardRiskReasons": [],
            "limits": [],
        },
        "layoutUnitFit": {
            "score": 100,
            "rawScore": 100,
            "state": "notDeclared",
            "evaluated": False,
            "hardFailureCount": 0,
            "hardFailureReasons": [],
            "positiveReasons": [],
            "riskReasons": [],
        },
    }

    module.apply_semantic_contract_fit(hierarchy, aggregate)

    assert aggregate["classification"]["status"] == "fail"
    assert aggregate["classification"]["phaseFloorFailureCount"] == 1
    assert aggregate["phaseFit"]["worstDominantHeadroom"] < 0
    assert any(
        "planning phase gives workflow 51.42% against phase floor 56.00%"
        in reason
        for reason in aggregate["classification"]["failureReasons"]
    )


def test_stage_e_report_surfaces_unified_floor_gate(tmp_path):
    module = load_module()
    report = {
        "generatedAt": "2026-07-10T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "phaseFloorGateMode": "unified-effective-share-absolute",
        "phaseFloorTolerance": module.PHASE_SHARE_FLOOR_TOLERANCE,
        "densityScoringMode": "phase-relative-active-presentation",
        "candidateRankingMode": "rendered-equivalence-deduplicated-margin-ranking",
        "renderedPolicyFingerprintVersion": module.RENDERED_POLICY_FINGERPRINT_VERSION,
        "renderedPolicyFingerprintBins": module.RENDERED_POLICY_FINGERPRINT_BINS,
        "renderedPolicyEquivalenceGroups": [],
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": [],
        "candidateCatalogByHierarchy": {},
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": [],
        "hierarchies": [],
        "semanticContracts": [],
        "bestByHierarchyViewport": [],
        "rollups": [],
        "measurements": [],
    }

    _, markdown_path = module.write_reports(report, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert "Phase-floor gate: `unified-effective-share-absolute`" in markdown
    assert "Stage E applies one absolute effective-share phase-floor gate" in markdown


def test_stage_e_python_gate_canonicalizes_browser_state_before_aggregation():
    module = load_module()
    hierarchy, measurement = _stage_e_phase_fixture(module, 0.5142)
    realized = {
        "phase": "planning",
        "focusSlot": "workflow",
        "minFocusShare": 0.56,
    }
    measurement["geometryFacts"]["focusShare"] = 0.5142
    measurement["geometryFacts"]["effectiveFocusShare"] = 0.5142
    measurement["classification"]["status"] = "pass"

    module.apply_phase_floor_gate_to_measurement(realized, measurement)

    assert measurement["classification"]["status"] == "fail"
    assert measurement["classification"]["phaseFloorGate"]["met"] is False
    assert measurement["geometryFacts"]["focusFloorMet"] is False
    assert any(
        "planning phase gives workflow 51.42% against phase floor 56.00%"
        in reason
        for reason in measurement["classification"]["failureReasons"]
    )


def _responsive_measurement(
    *,
    hierarchy_id: str,
    viewport: str,
    candidate: str,
    support_policy: str,
    status: str = "pass",
    score: float = 95.0,
    headroom: float = 0.04,
) -> dict:
    return {
        "hierarchyId": hierarchy_id,
        "viewportProfile": viewport,
        "candidate": candidate,
        "candidateMode": "recursive-composition",
        "renderedEquivalenceExcludedFromRanking": False,
        "classification": {
            "status": status,
            "score": round(score),
            "selectionScore": round(score),
            "selectionScoreRaw": score,
        },
        "phaseFit": {
            "worstDominantHeadroom": headroom,
            "phaseFloorFailureCount": 0 if status != "fail" else 1,
            "scoreVariance": 0.0,
            "phases": [
                {
                    "phase": "default",
                    "dominantShare": 0.60 + headroom,
                    "minDominantShare": 0.60,
                    "rawScore": score,
                }
            ],
        },
        "layoutUnitFit": {
            "worstScore": 96,
            "worstScoreRaw": 96.0,
        },
        "geometryFacts": {"usefulFocusOccupancy": 0.6},
        "unitComposition": {
            "enabled": True,
            "preflightScore": 96,
            "unitPolicies": {
                "git-tools-application": "dominant-workflow-stack",
                "project-identity": "phase-selector-unit",
                "command-workflow": "command-inline-header",
                "persistent-feedback": "shared-horizontal-band",
                "phase-support": support_policy,
            },
        },
        "phaseMeasurements": [],
    }


def test_responsive_viewports_merge_without_duplicate_desktop():
    module = load_module()

    base = module.parse_viewports("desktop=1440x900")
    probes = module.parse_viewports(
        "wide=1600x1000,desktop=1440x900,narrow=840x720"
    )
    merged = module.merge_viewport_profiles(base, probes)

    assert [(item.name, item.width) for item in merged] == [
        ("wide", 1600),
        ("desktop", 1440),
        ("narrow", 840),
    ]
    assert merged[0].responsive_probe is True
    assert merged[1].responsive_probe is False
    assert merged[2].responsive_probe is True


def test_responsive_contract_allows_stronger_remediation_only_as_width_shrinks():
    module = load_module()
    hierarchy = {
        "id": "responsive-fixture",
        "responsiveContract": {
            "bands": [
                {"id": "wide", "minWidth": 1200, "maxRemediationLevel": 0},
                {"id": "medium", "minWidth": 800, "maxRemediationLevel": 1},
                {"id": "compact", "minWidth": 0, "maxRemediationLevel": 3},
            ]
        },
    }

    contract = module.responsive_contract_for_hierarchy(hierarchy)

    assert module.responsive_capacity_band(contract, 1400)["maxRemediationLevel"] == 0
    assert module.responsive_capacity_band(contract, 900)["maxRemediationLevel"] == 1
    assert module.responsive_capacity_band(contract, 600)["maxRemediationLevel"] == 3


def test_responsive_policy_switches_from_side_partition_to_sequential_stage():
    module = load_module()
    hierarchy = {
        "id": "responsive-fixture",
        "layoutUnitTree": {"id": "root"},
        "responsiveContract": {
            "bands": [
                {"id": "wide", "minWidth": 1000, "maxRemediationLevel": 0},
                {"id": "compact", "minWidth": 0, "maxRemediationLevel": 3},
            ],
            "minimumRobustHeadroom": 0.01,
            "hysteresisPx": 40,
        },
    }
    profiles = [
        module.ViewportProfile("wide", 1400, 900, True),
        module.ViewportProfile("compact", 700, 720, True),
    ]
    measurements = [
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="wide",
            candidate="support-side",
            support_policy="bounded-side-drawer",
            score=96,
            headroom=0.05,
        ),
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="wide",
            candidate="support-triggers",
            support_policy="one-active-plus-triggers",
            score=99,
            headroom=0.08,
        ),
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="compact",
            candidate="support-side",
            support_policy="bounded-side-drawer",
            status="fail",
            score=70,
            headroom=-0.08,
        ),
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="compact",
            candidate="support-triggers",
            support_policy="one-active-plus-triggers",
            score=94,
            headroom=0.03,
        ),
    ]

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=measurements,
        profiles=profiles,
    )

    assert [item["candidate"] for item in policy["selections"]] == [
        "support-side",
        "support-triggers",
    ]
    assert [item["remediationLevel"] for item in policy["selections"]] == [0, 3]
    assert policy["state"] == "pass"
    assert policy["semanticContractStable"] is True
    assert policy["wideToNarrowStable"] is True
    assert policy["narrowToWideStable"] is True
    assert len(policy["transitions"]) == 1
    transition = policy["transitions"][0]
    assert transition["switchDownBelow"] < transition["switchUpAbove"]


def test_responsive_policy_rejects_unnecessary_wide_remediation():
    module = load_module()
    hierarchy = {
        "id": "responsive-fixture",
        "layoutUnitTree": {"id": "root"},
        "responsiveContract": {
            "bands": [
                {"id": "wide", "minWidth": 0, "maxRemediationLevel": 1},
            ],
            "minimumRobustHeadroom": 0.01,
            "unnecessaryRemediationPenalty": 20,
        },
    }
    profiles = [module.ViewportProfile("wide", 1400, 900, True)]
    measurements = [
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="wide",
            candidate="side",
            support_policy="bounded-side-drawer",
            score=94,
            headroom=0.04,
        ),
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="wide",
            candidate="bottom",
            support_policy="bounded-bottom-drawer",
            score=99,
            headroom=0.05,
        ),
    ]

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=measurements,
        profiles=profiles,
    )

    assert policy["selections"][0]["candidate"] == "side"
    assert policy["selections"][0]["remediationLevel"] == 0
    assert policy["unnecessaryRemediationCount"] == 0


def test_responsive_policy_reports_transition_gap_when_no_candidate_passes():
    module = load_module()
    hierarchy = {
        "id": "responsive-fixture",
        "layoutUnitTree": {"id": "root"},
        "responsiveContract": {
            "bands": [{"id": "compact", "minWidth": 0, "maxRemediationLevel": 3}]
        },
    }
    profiles = [module.ViewportProfile("compact", 600, 720, True)]
    measurements = [
        _responsive_measurement(
            hierarchy_id="responsive-fixture",
            viewport="compact",
            candidate="broken",
            support_policy="one-active-plus-triggers",
            status="fail",
            score=60,
            headroom=-0.2,
        )
    ]

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=measurements,
        profiles=profiles,
    )

    assert policy["state"] == "fail"
    assert policy["transitionGapCount"] == 1
    assert policy["semanticContractStable"] is False


def test_resize_hysteresis_simulation_uses_different_entry_and_exit_thresholds():
    module = load_module()
    selections = [
        {
            "candidate": "wide-layout",
            "width": 1400,
            "remediationLevel": 0,
            "band": "wide",
        },
        {
            "candidate": "compact-layout",
            "width": 800,
            "remediationLevel": 2,
            "band": "narrow",
        },
    ]

    transitions = module.responsive_transition_rules(
        selections,
        hysteresis_px=60,
    )
    down = module.simulate_responsive_resize(
        selections, transitions, direction="down"
    )
    up = module.simulate_responsive_resize(
        selections, transitions, direction="up"
    )

    assert transitions[0]["switchDownBelow"] < transitions[0]["switchUpAbove"]
    assert down["stable"] is True
    assert up["stable"] is True
    assert down["switches"][0]["width"] == transitions[0]["switchDownBelow"]
    assert up["switches"][0]["width"] == transitions[0]["switchUpAbove"]


def test_cli_defaults_to_recursive_responsive_trials():
    module = load_module()

    args = module.build_arg_parser().parse_args([])

    assert args.responsive_mode == "recursive"
    assert args.responsive_hysteresis_px == module.DEFAULT_RESPONSIVE_HYSTERESIS_PX
    assert "wide=1600x1000" in args.responsive_viewports


def test_best_rows_use_cross_viewport_policy_selection_instead_of_local_score():
    module = load_module()
    side = _responsive_measurement(
        hierarchy_id="responsive-fixture",
        viewport="wide",
        candidate="side",
        support_policy="bounded-side-drawer",
        score=92,
        headroom=0.05,
    )
    bottom = _responsive_measurement(
        hierarchy_id="responsive-fixture",
        viewport="wide",
        candidate="bottom",
        support_policy="bounded-bottom-drawer",
        score=99,
        headroom=0.06,
    )
    side["responsivePolicySelected"] = True
    policies = [
        {
            "hierarchyId": "responsive-fixture",
            "selections": [
                {
                    "viewportProfile": "wide",
                    "candidate": "side",
                }
            ],
        }
    ]

    rows = module.best_by_hierarchy_viewport_rows(
        [side, bottom],
        responsive_policies=policies,
    )

    assert rows[0]["candidate"] == "side"
    assert rows[0]["selectionState"] == "responsivePolicySelection"
    assert rows[0]["highestScoringCandidate"]["candidate"] == "bottom"


def _stage_f1_minimal_report(module, measurements):
    return {
        "generatedAt": "2026-07-10T00:00:00+00:00",
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "exclusive-enforced",
        "paintedOwnershipEnforced": True,
        "phaseFloorGateMode": "unified-effective-share-absolute",
        "phaseFloorTolerance": module.PHASE_SHARE_FLOOR_TOLERANCE,
        "densityScoringMode": "phase-relative-active-presentation",
        "candidateRankingMode": "rendered-equivalence-deduplicated-margin-ranking",
        "responsiveMode": "recursive",
        "responsivePolicyVersion": module.RESPONSIVE_POLICY_VERSION,
        "responsiveHysteresisPx": 40,
        "responsivePolicies": [],
        "renderedPolicyFingerprintVersion": module.RENDERED_POLICY_FINGERPRINT_VERSION,
        "renderedPolicyFingerprintBins": module.RENDERED_POLICY_FINGERPRINT_BINS,
        "renderedPolicyEquivalenceGroups": [],
        "hierarchySource": "generated-mcel-like-html",
        "chrome": "mcel-realistic",
        "candidates": [],
        "candidateCatalogByHierarchy": {},
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": [],
        "hierarchies": [],
        "semanticContracts": [],
        "bestByHierarchyViewport": [],
        "rollups": [],
        "measurements": measurements,
    }


def test_stage_f1_compact_report_strips_nested_browser_diagnostics(tmp_path):
    module = load_module()
    measurement = _responsive_measurement(
        hierarchy_id="responsive-fixture",
        viewport="desktop",
        candidate="support-side",
        support_policy="bounded-side-drawer",
    )
    measurement["viewportWidth"] = 1440
    measurement["viewportHeight"] = 900
    measurement["phaseMeasurements"] = [
        {
            "phase": "default",
            "examples": {"nodes": [{"payload": "x" * 20000}]},
            "geometryFacts": {"focusShare": 0.64},
            "classification": {"score": 95, "status": "pass"},
        }
    ]
    report = _stage_f1_minimal_report(module, [measurement])

    json_path, markdown_path = module.write_reports(
        report,
        tmp_path,
        report_detail=module.REPORT_DETAIL_COMPACT,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert payload["reportDetail"] == "compact"
    assert payload["measurementRecordMode"] == module.COMPACT_MEASUREMENT_VERSION
    assert payload["fullDiagnosticMeasurementsIncluded"] is False
    assert payload["fullDiagnosticMeasurementCount"] == 1
    assert "phaseMeasurements" not in payload["measurements"][0]
    assert "examples" not in payload["measurements"][0]
    assert payload["measurements"][0]["phases"][0]["phase"] == "default"
    assert "## Compact trial measurement index" in markdown
    assert "## All trial measurements" not in markdown


def test_stage_f1_full_report_remains_opt_in(tmp_path):
    module = load_module()
    measurement = _responsive_measurement(
        hierarchy_id="responsive-fixture",
        viewport="desktop",
        candidate="support-side",
        support_policy="bounded-side-drawer",
    )
    measurement["phaseMeasurements"] = [
        {
            "phase": "default",
            "examples": {"nodes": [{"payload": "diagnostic"}]},
            "geometryFacts": {"focusShare": 0.64},
            "classification": {"score": 95, "status": "pass"},
        }
    ]
    report = _stage_f1_minimal_report(module, [measurement])

    json_path, markdown_path = module.write_reports(
        report,
        tmp_path,
        report_detail=module.REPORT_DETAIL_FULL,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert payload["reportDetail"] == "full"
    assert payload["fullDiagnosticMeasurementsIncluded"] is True
    assert payload["measurements"][0]["phaseMeasurements"][0]["examples"]
    assert "## All trial measurements" in markdown


def test_stage_f1_cli_defaults_to_compact_report_detail():
    module = load_module()
    parser = module.build_arg_parser()

    args = parser.parse_args([])

    assert args.report_detail == module.REPORT_DETAIL_COMPACT


def test_layout_hint_milestone1_compiles_a_deterministic_shadow_dock_tree():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    normalized = module.normalize_layout_hint_contract(hierarchy)
    compilation = module.compile_layout_hint_default(hierarchy)

    assert normalized["state"] == "complete"
    assert normalized["sourceKind"] == "synthetic-data-mc-layout-attributes"
    assert normalized["version"] == module.LAYOUT_HINT_CONTRACT_VERSION
    assert normalized["root"]["model"] == "dock-workbench"
    assert normalized["root"]["zones"] == [
        "top",
        "left",
        "center",
        "right",
        "bottom",
        "tab",
        "stage",
        "trigger",
    ]

    assert compilation["state"] == "complete"
    assert compilation["mode"] == "shadow-only"
    assert compilation["liveApplicationFilesTouched"] is False
    assert compilation["issues"] == []
    assert compilation["dockTree"]["unitPlacements"] == {
        "project-identity": "left",
        "command-workflow": "center",
        "persistent-feedback": "bottom",
        "phase-support": "right",
    }
    assert compilation["candidate"]["composition"] == {
        "rootPolicy": "dominant-workflow-stack",
        "unitPolicies": {
            "project-identity": "phase-selector-unit",
            "command-workflow": "command-inline-header",
            "persistent-feedback": "shared-horizontal-band",
            "phase-support": "bounded-side-drawer",
        },
    }
    assert len(compilation["annotationRecommendations"]) == 4
    assert all(
        recommendation["reason"].startswith("Shadow recommendation only")
        for recommendation in compilation["annotationRecommendations"]
    )


def test_layout_hint_parser_fails_closed_on_an_invalid_preferred_placement():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    broken = copy.deepcopy(hierarchy)
    broken["layoutHintSource"]["units"]["phase-support"][
        "data-mc-layout-prefer"
    ] = "left"
    broken["layoutHintSource"]["units"]["phase-support"][
        "data-mc-layout-allowed"
    ] = "right bottom tab stage trigger"

    normalized = module.normalize_layout_hint_contract(broken)
    compilation = module.compile_layout_hint_default(broken)

    assert normalized["state"] == "invalid"
    assert any(
        "phase-support prefers 'left'" in issue
        for issue in normalized["issues"]
    )
    assert compilation["state"] == "invalid"
    assert compilation["candidate"] is None


def test_layout_hint_shadow_candidate_is_wide_only_phase_aware_and_not_ranked():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    candidate = module.layout_hint_shadow_candidate_spec(hierarchy)
    assert candidate is not None

    assert module.candidate_shadow_only(candidate) is True
    assert module.candidate_applies_to_viewport(
        candidate,
        module.ViewportProfile(name="wide", width=1600, height=1000),
    )
    assert module.candidate_applies_to_viewport(
        candidate,
        module.ViewportProfile(name="desktop", width=1440, height=900),
    )
    assert not module.candidate_applies_to_viewport(
        candidate,
        module.ViewportProfile(name="medium", width=1200, height=820),
    )
    assert module.candidate_phase_policy(candidate)["phaseAware"] is True

    scenario = next(
        item
        for item in hierarchy["phaseScenarios"]
        if item["phase"] == "selected-project-default"
    )
    realized = module.realize_phase(hierarchy, candidate, scenario)
    html = module.render_realized_trial_html(
        realized,
        candidate,
        "mcel-realistic",
    )

    assert (
        realized["unitComposition"]["searchMode"]
        == "deterministic-layout-hint-compilation"
    )
    assert (
        realized["unitComposition"]["origin"]
        == "authored-layout-hints-shadow"
    )
    assert 'data-flog-candidate-mode="layout-hint-shadow"' in html
    assert candidate["id"] in html

    measurement = {
        "hierarchyId": hierarchy["id"],
        "viewportProfile": "wide",
        "candidate": candidate["id"],
        "candidateMode": "layout-hint-shadow",
        "shadowOnly": True,
        "phaseMeasurements": [],
    }
    module.annotate_rendered_policy_equivalence([measurement])
    assert measurement["renderedEquivalenceExcludedFromRanking"] is True


def test_compact_measurement_preserves_layout_hint_shadow_evidence():
    module = load_module()
    item = {
        "hierarchyId": "git-tools-workflow-workbench",
        "viewportProfile": "wide",
        "viewportWidth": 1600,
        "viewportHeight": 1000,
        "candidate": "hint-compiled-default--git-tools-workflow-workbench",
        "candidateMode": "layout-hint-shadow",
        "renderFamily": "recursive-composition",
        "shadowOnly": True,
        "layoutHintCompilation": {
            "version": module.LAYOUT_HINT_CONTRACT_VERSION,
            "capacity": "wide",
            "state": "complete",
            "dockTree": {
                "id": "git-tools-application",
                "model": "dock-workbench",
            },
        },
        "classification": {"status": "pass", "score": 90},
        "geometryFacts": {},
        "phaseFit": {},
        "layoutUnitFit": {},
        "unitComposition": {},
        "snapshots": {},
        "phaseSnapshots": {},
    }

    compact = module.compact_measurement_summary(item)

    assert compact["shadowOnly"] is True
    assert compact["candidateMode"] == "layout-hint-shadow"
    assert compact["layoutHintCompilation"]["state"] == "complete"
    assert (
        compact["layoutHintCompilation"]["dockTree"]["model"]
        == "dock-workbench"
    )


def _layout_hint_refinement_measurement(
    module,
    *,
    candidate,
    viewport,
    policies,
    status="pass",
    headroom=0.01,
    raw_score=95.0,
    shadow=False,
    responsive=False,
):
    return {
        "hierarchyId": "git-tools-workflow-workbench",
        "viewportProfile": viewport,
        "viewportWidth": {
            "wide": 1600,
            "desktop": 1440,
            "medium": 1200,
            "constrained": 1024,
            "narrow": 840,
            "compact": 680,
            "small": 560,
        }[viewport],
        "viewportHeight": 900,
        "candidate": candidate,
        "candidateMode": (
            "layout-hint-responsive-shadow"
            if responsive
            else ("layout-hint-shadow" if shadow else "recursive-composition")
        ),
        "shadowOnly": shadow or responsive,
        "responsiveEligible": responsive,
        "classification": {
            "status": status,
            "score": round(raw_score),
            "selectionScore": round(raw_score),
            "selectionScoreRaw": raw_score,
            "failureReasons": (
                [] if status in module.ACCEPTABLE_LAYOUT_STATUSES
                else ["phase floor was not met"]
            ),
        },
        "phaseFit": {
            "worstDominantHeadroom": headroom,
            "hardFailureCount": 0 if status in module.ACCEPTABLE_LAYOUT_STATUSES else 1,
            "scoreVariance": 1.0,
            "phases": [
                {
                    "phase": "planning",
                    "dominantShare": 0.56 + headroom,
                    "minDominantShare": 0.56,
                    "score": 95,
                    "rawScore": 95.0,
                }
            ],
        },
        "layoutUnitFit": {
            "hardFailureCount": 0,
            "worstScore": 97,
            "worstScoreRaw": 97.0,
        },
        "geometryFacts": {"usefulFocusOccupancy": 0.8},
        "unitComposition": {
            "rootUnitId": "git-tools-application",
            "unitPolicies": {
                **policies,
                "git-tools-application": "dominant-workflow-stack",
            },
            "preflightScore": 98,
        },
        "phaseMeasurements": [],
        "snapshots": {},
        "phaseSnapshots": {},
    }


def test_layout_hint_milestone1e_finds_the_smallest_browser_verified_refinement():
    module = load_module()
    hierarchy = copy.deepcopy(
        next(
            item
            for item in module.synthetic_hierarchies()
            if item["id"] == "git-tools-workflow-workbench"
        )
    )
    hierarchy["layoutHintSource"]["units"]["command-workflow"][
        "data-mc-layout-policy"
    ] = "command-over-dominant"
    hierarchy["layoutHintSource"]["units"]["command-workflow"][
        "data-mc-layout-internal"
    ] = "command-top workflow-center"
    authored = {
        "project-identity": "phase-selector-unit",
        "command-workflow": "command-over-dominant",
        "persistent-feedback": "shared-horizontal-band",
        "phase-support": "bounded-side-drawer",
    }
    refined = {
        **authored,
        "command-workflow": "command-inline-header",
    }
    measurements = [
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-compiled-default--git-tools-workflow-workbench",
            viewport="wide",
            policies=authored,
            headroom=0.0303,
            raw_score=97.0,
            shadow=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-compiled-default--git-tools-workflow-workbench",
            viewport="desktop",
            policies=authored,
            headroom=0.00016,
            raw_score=96.0,
            shadow=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="compose--selector-trigger--command-inline--feedback-band--support-side",
            viewport="desktop",
            policies=refined,
            headroom=0.0101,
            raw_score=96.2,
        ),
    ]
    module.annotate_rendered_policy_equivalence(measurements)

    reports = module.analyze_layout_hint_refinements(
        hierarchies=[hierarchy],
        measurements=measurements,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["state"] == "complete"
    assert report["liveApplicationFilesTouched"] is False
    assert report["applicationMutationAllowed"] is False
    assert (
        report["authoredEvidenceByViewport"]["desktop"]["outcome"]
        == "accepted-with-warning"
    )
    assert report["authoredEvidenceByViewport"]["wide"]["outcome"] == "accepted"
    assert len(report["recommendedContractRevisions"]) == 1
    recommendation = report["recommendedContractRevisions"][0]
    assert recommendation["unitId"] == "command-workflow"
    assert recommendation["currentPolicy"] == "command-over-dominant"
    assert recommendation["suggestedPolicy"] == "command-inline-header"
    assert abs(recommendation["headroomImprovement"] - 0.00994) < 1e-9
    assert recommendation["browserVerified"] is True
    assert recommendation["applyAutomatically"] is False


def test_layout_hint_milestone2_prepares_distinct_responsive_fallbacks():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    plan = module.layout_hint_fallback_plan(hierarchy)

    assert plan["state"] == "complete"
    assert plan["unitId"] == "phase-support"
    assert [entry["placement"] for entry in plan["chain"]] == [
        "right",
        "bottom",
        "tab",
        "stage",
        "trigger",
    ]
    by_placement = {entry["placement"]: entry for entry in plan["chain"]}
    assert by_placement["right"]["policy"] == "bounded-side-drawer"
    assert by_placement["bottom"]["policy"] == "bounded-bottom-drawer"
    assert by_placement["tab"]["policy"] == "tabbed-phase-support"
    assert by_placement["stage"]["policy"] == "sequential-phase-stage"
    assert by_placement["trigger"]["policy"] == "one-active-plus-triggers"
    assert all(entry["state"] == "ready" for entry in plan["chain"])
    assert by_placement["bottom"]["viewportProfiles"] == [
        "medium",
        "constrained",
    ]
    assert by_placement["tab"]["viewportProfiles"] == ["narrow"]
    assert by_placement["stage"]["viewportProfiles"] == ["compact", "small"]


def test_layout_hint_milestone2_uses_responsive_shadow_trials_for_fallback_evidence():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )
    authored = {
        "project-identity": "phase-selector-unit",
        "command-workflow": "command-inline-header",
        "persistent-feedback": "shared-horizontal-band",
        "phase-support": "bounded-side-drawer",
    }
    measurements = [
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-compiled-default--git-tools-workflow-workbench",
            viewport="desktop",
            policies=authored,
            headroom=0.0101,
            shadow=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-responsive-bottom--git-tools-workflow-workbench",
            viewport="medium",
            policies={**authored, "phase-support": "bounded-bottom-drawer"},
            headroom=0.018,
            raw_score=94.0,
            responsive=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-responsive-tab--git-tools-workflow-workbench",
            viewport="narrow",
            policies={**authored, "phase-support": "tabbed-phase-support"},
            headroom=0.024,
            raw_score=93.0,
            responsive=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-responsive-stage--git-tools-workflow-workbench",
            viewport="compact",
            policies={**authored, "phase-support": "sequential-phase-stage"},
            headroom=0.03,
            raw_score=92.0,
            responsive=True,
        ),
        _layout_hint_refinement_measurement(
            module,
            candidate="hint-responsive-trigger--git-tools-workflow-workbench",
            viewport="small",
            policies={**authored, "phase-support": "one-active-plus-triggers"},
            headroom=0.012,
            raw_score=90.0,
            responsive=True,
        ),
    ]
    module.annotate_rendered_policy_equivalence(measurements)

    report = module.analyze_layout_hint_refinements(
        hierarchies=[hierarchy],
        measurements=measurements,
    )[0]
    chain = {
        entry["placement"]: entry
        for entry in report["fallbackPreparation"]["chain"]
    }

    assert chain["bottom"]["measurements"][0]["outcome"] == "accepted"
    assert chain["tab"]["measurements"][0]["outcome"] == "accepted"
    assert chain["stage"]["measurements"][0]["outcome"] == "accepted"
    assert any(
        item["outcome"] == "accepted"
        for item in chain["trigger"]["measurements"]
    )
    assert chain["tab"]["state"] == "ready"


def test_layout_hint_milestone2_adds_only_bounded_responsive_shadow_candidates():
    module = load_module()
    hierarchy = next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )

    candidates = module.candidate_specs_for_hierarchy(
        hierarchy,
        module.LAYOUT_CANDIDATES,
    )

    shadow = [item for item in candidates if module.candidate_shadow_only(item)]
    responsive = [
        item for item in candidates if module.candidate_responsive_eligible(item)
    ]
    assert len(candidates) == len(module.LAYOUT_CANDIDATES) + 5
    assert len(shadow) == 5
    assert len(responsive) == 5
    assert {
        item["responsivePlacement"] for item in responsive
    } == {"right", "bottom", "tab", "stage", "trigger"}
    assert any(
        item["id"] == "hint-compiled-default--git-tools-workflow-workbench"
        for item in shadow
    )


def test_compact_measurement_preserves_layout_hint_refinement_outcome():
    module = load_module()
    item = _layout_hint_refinement_measurement(
        module,
        candidate="hint-compiled-default--git-tools-workflow-workbench",
        viewport="desktop",
        policies={
            "project-identity": "phase-selector-unit",
            "command-workflow": "command-over-dominant",
            "persistent-feedback": "shared-horizontal-band",
            "phase-support": "bounded-side-drawer",
        },
        headroom=0.00016,
        shadow=True,
    )
    item["layoutHintOutcome"] = module.layout_hint_measurement_outcome(item)

    compact = module.compact_measurement_summary(item)

    assert compact["layoutHintOutcome"]["outcome"] == "accepted-with-warning"
    assert abs(compact["layoutHintOutcome"]["worstPhaseHeadroom"] - 0.00016) < 1e-9


def _git_hierarchy_for_milestone2(module):
    return next(
        item
        for item in module.synthetic_hierarchies()
        if item["id"] == "git-tools-workflow-workbench"
    )


def test_layout_hint_milestone2_derives_capacity_bands_from_authored_minima():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)

    derivation = module.derive_responsive_capacity_bands_from_hints(hierarchy)
    contract = module.responsive_contract_for_hierarchy(hierarchy)

    assert derivation["state"] == "complete"
    assert derivation["version"] == module.RESPONSIVE_PRESENTATION_CONTRACT_VERSION
    assert [item["id"] for item in derivation["bands"]] == [
        "wide",
        "medium",
        "narrow",
        "compact",
    ]
    assert [item["minWidth"] for item in derivation["bands"]] == [
        1440,
        1024,
        720,
        0,
    ]
    assert contract["mode"] == "derived-from-layout-hints"
    assert contract["bands"] == derivation["bands"]
    assert contract["capacityDerivation"]["inputs"]["phaseSupportMinInline"] == 300.0
    assert contract["capacityDerivation"]["inputs"]["robustnessFactor"] == 1.368


def test_layout_hint_milestone2_changes_the_semantic_presentation_by_capacity():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    planning = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "planning"
    )
    proof = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "proof-review"
    )

    medium = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile("medium", 1200, 820, True),
    )
    narrow = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile("narrow", 840, 720, True),
    )
    compact_proof = module.responsive_phase_scenario(
        hierarchy,
        proof,
        module.ViewportProfile("compact", 680, 720, True),
    )

    assert medium["dominantSlot"] == "workflow"
    assert medium["presentationMode"] == "workflow-with-bottom-support"
    assert medium["minDominantShare"] == 0.40

    assert narrow["dominantSlot"] == "server"
    assert narrow["presentationMode"] == "support-tab"
    assert narrow["summarySlots"] == ["command", "workflow"]
    assert narrow["returnToSlot"] == "workflow"

    assert compact_proof["dominantSlot"] == "evidence"
    assert compact_proof["presentationMode"] == "sequential-support-stage"
    assert compact_proof["summarySlots"] == ["workflow"]
    assert compact_proof["returnToSlot"] == "workflow"


def test_layout_hint_milestone21_binds_semantics_to_the_rendered_realization():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    planning = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "planning"
    )
    candidates = {
        item["responsivePlacement"]: item
        for item in [
            module.layout_hint_shadow_candidate_spec(hierarchy),
            *module.compile_layout_hint_responsive_candidates(hierarchy),
        ]
        if item is not None
    }

    right_below_boundary = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile(
            "boundary-right-bottom-below-1439", 1439, 900, True
        ),
        candidates["right"],
    )
    bottom_above_boundary = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile(
            "boundary-right-bottom-above-1441", 1441, 900, True
        ),
        candidates["bottom"],
    )

    assert right_below_boundary["responsivePresentation"]["presentationBand"] == "wide"
    assert right_below_boundary["responsivePresentation"]["contractSource"] == "realization-policy"
    assert right_below_boundary["dominantSlot"] == "workflow"
    assert right_below_boundary["minDominantShare"] == 0.56

    assert bottom_above_boundary["responsivePresentation"]["presentationBand"] == "medium"
    assert bottom_above_boundary["responsivePresentation"]["contractSource"] == "realization-policy"
    assert bottom_above_boundary["dominantSlot"] == "workflow"
    assert bottom_above_boundary["minDominantShare"] == 0.40


def test_layout_hint_milestone21_compact_default_replaces_full_command_controls():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    selected_default = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "selected-project-default"
    )
    stage = next(
        item
        for item in module.compile_layout_hint_responsive_candidates(hierarchy)
        if item["responsivePlacement"] == "stage"
    )
    scenario = module.responsive_phase_scenario(
        hierarchy,
        selected_default,
        module.ViewportProfile("compact", 680, 720, True),
        stage,
    )
    realized = module.realize_phase(hierarchy, stage, scenario)
    command = next(item for item in realized["nodes"] if item["slot"] == "command")
    markup = module.node_markup(command, realized["focusSlot"])

    assert realized["realizationStates"]["command"] == "compact-summary"
    assert 'data-flog-realization="compact-summary"' in markup
    assert "<button" in markup  # the semantic return control only
    assert "Plan commit" not in markup
    assert "Publish" not in markup


def test_layout_hint_milestone21_authored_policy_does_not_use_legacy_rescue():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    profile = module.ViewportProfile("medium", 1200, 820, True)

    hinted = _responsive_measurement(
        hierarchy_id=hierarchy["id"],
        viewport="medium",
        candidate="hint-responsive-bottom",
        support_policy="bounded-bottom-drawer",
        status="fail",
        score=70,
        headroom=-0.03,
    )
    hinted["candidateMode"] = "layout-hint-responsive-shadow"
    hinted["responsiveEligible"] = True
    hinted["shadowOnly"] = True

    legacy = _responsive_measurement(
        hierarchy_id=hierarchy["id"],
        viewport="medium",
        candidate="compose-legacy-rescue",
        support_policy="bounded-side-drawer",
        status="pass",
        score=99,
        headroom=0.08,
    )

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=[hinted, legacy],
        profiles=[profile],
    )

    assert policy["selectionMode"] == "authored-hint-chain"
    assert policy["state"] == "fail"
    assert policy["selections"][0]["candidate"] == "hint-responsive-bottom"
    assert policy["transitionGapCount"] == 1


def test_layout_hint_milestone2_realizes_compact_context_as_a_summary_not_a_panel():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    planning = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "planning"
    )
    scenario = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile("compact", 680, 720, True),
    )
    candidate = next(
        item
        for item in module.compile_layout_hint_responsive_candidates(hierarchy)
        if item["responsivePlacement"] == "stage"
    )

    realized = module.realize_phase(hierarchy, candidate, scenario)
    workflow = next(
        item for item in realized["nodes"] if item["slot"] == "workflow"
    )
    markup = module.node_markup(workflow, realized["focusSlot"])

    assert realized["realizationStates"]["workflow"] == "compact-summary"
    assert workflow["items"]
    assert 'data-flog-realization="compact-summary"' in markup
    assert 'data-flog-return-to="workflow"' in markup
    assert "Return to workflow" in markup
    assert "Changed file:" not in markup


def test_layout_hint_milestone2_compiles_distinct_dock_tree_placements():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)

    candidates = [
        module.layout_hint_shadow_candidate_spec(hierarchy),
        *module.compile_layout_hint_responsive_candidates(hierarchy),
    ]
    candidates = [item for item in candidates if item is not None]
    by_placement = {item["responsivePlacement"]: item for item in candidates}

    assert set(by_placement) == {"right", "bottom", "tab", "stage", "trigger"}
    assert {
        item["composition"]["unitPolicies"]["phase-support"]
        for item in candidates
    } == {
        "bounded-side-drawer",
        "bounded-bottom-drawer",
        "tabbed-phase-support",
        "sequential-phase-stage",
        "one-active-plus-triggers",
    }
    for placement, candidate in by_placement.items():
        dock_tree = candidate["layoutHintCompilation"]["dockTree"]
        assert dock_tree["unitPlacements"]["phase-support"] == placement
        target_zone = next(
            item for item in dock_tree["zones"] if item["id"] == placement
        )
        assert "phase-support" in target_zone["units"]


def test_layout_hint_milestone2_sampled_coverage_fails_closed_on_bad_probe():
    module = load_module()
    selections = [
        {
            "width": 1600,
            "candidate": "wide",
            "band": "wide",
            "remediationLevel": 0,
            "capacityAdmissible": True,
            "status": "pass",
            "phaseFloorFailureCount": 0,
            "transitionGap": False,
        },
        {
            "width": 1200,
            "candidate": "medium",
            "band": "medium",
            "remediationLevel": 1,
            "capacityAdmissible": True,
            "status": "pass",
            "phaseFloorFailureCount": 0,
            "transitionGap": False,
        },
        {
            "width": 680,
            "candidate": "compact",
            "band": "compact",
            "remediationLevel": 3,
            "capacityAdmissible": True,
            "status": "pass",
            "phaseFloorFailureCount": 0,
            "transitionGap": False,
        },
    ]

    complete = module.responsive_sampled_coverage(selections)
    assert complete["coverageComplete"] is True
    assert complete["uncoveredIntervals"] == []

    selections[1]["capacityAdmissible"] = False
    gapped = module.responsive_sampled_coverage(selections)
    assert gapped["coverageComplete"] is False
    assert gapped["state"] == "gapped"
    assert len(gapped["uncoveredIntervals"]) == 1
    assert "outside its capacity contract" in gapped["uncoveredIntervals"][0]["reason"]


def test_layout_hint_milestone2_css_contains_distinct_tab_and_stage_realizations():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    planning = next(
        item
        for item in module.phase_trial_scenarios(hierarchy)
        if item["phase"] == "planning"
    )
    scenario = module.responsive_phase_scenario(
        hierarchy,
        planning,
        module.ViewportProfile("narrow", 840, 720, True),
    )
    candidate = next(
        item
        for item in module.compile_layout_hint_responsive_candidates(hierarchy)
        if item["responsivePlacement"] == "tab"
    )
    realized = module.realize_phase(hierarchy, candidate, scenario)

    html = module.render_realized_trial_html(
        realized,
        candidate,
        "mcel-realistic",
    )

    assert "unit-policy-tabbed-phase-support" in html
    assert "unit-policy-sequential-phase-stage" in module.render_realized_trial_html(
        module.realize_phase(
            hierarchy,
            next(
                item
                for item in module.compile_layout_hint_responsive_candidates(hierarchy)
                if item["responsivePlacement"] == "stage"
            ),
            module.responsive_phase_scenario(
                hierarchy,
                planning,
                module.ViewportProfile("compact", 680, 720, True),
            ),
        ),
        next(
            item
            for item in module.compile_layout_hint_responsive_candidates(hierarchy)
            if item["responsivePlacement"] == "stage"
        ),
        "mcel-realistic",
    )
    assert "compact-summary" in html
    assert module.RESPONSIVE_POLICY_VERSION == "capacity-derived-presentation-contract-v6"


def test_layout_hint_milestone21_policy_covers_all_capacity_probes_with_verified_overlap():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    profiles = [
        module.ViewportProfile("wide", 1600, 1000, True),
        module.ViewportProfile("desktop", 1440, 900, True),
        module.ViewportProfile("boundary-right-bottom-above-1441", 1441, 900, True),
        module.ViewportProfile("boundary-right-bottom-below-1439", 1439, 900, True),
        module.ViewportProfile("medium", 1200, 820, True),
        module.ViewportProfile("constrained", 1024, 768, True),
        module.ViewportProfile("boundary-bottom-tab-above-1025", 1025, 768, True),
        module.ViewportProfile("boundary-bottom-tab-below-1023", 1023, 768, True),
        module.ViewportProfile("narrow", 840, 720, True),
        module.ViewportProfile("boundary-tab-stage-trigger-above-721", 721, 720, True),
        module.ViewportProfile("boundary-tab-stage-trigger-below-719", 719, 720, True),
        module.ViewportProfile("compact", 680, 720, True),
        module.ViewportProfile("small", 560, 720, True),
    ]
    policy_by_placement = {
        "right": "bounded-side-drawer",
        "bottom": "bounded-bottom-drawer",
        "tab": "tabbed-phase-support",
        "stage": "sequential-phase-stage",
    }
    placements_by_viewport = {
        "wide": ["right"],
        "desktop": ["right"],
        "boundary-right-bottom-above-1441": ["right", "bottom"],
        "boundary-right-bottom-below-1439": ["right", "bottom"],
        "medium": ["bottom"],
        "constrained": ["bottom"],
        "boundary-bottom-tab-above-1025": ["bottom", "tab"],
        "boundary-bottom-tab-below-1023": ["bottom", "tab"],
        "narrow": ["tab"],
        "boundary-tab-stage-trigger-above-721": ["tab", "stage"],
        "boundary-tab-stage-trigger-below-719": ["tab", "stage"],
        "compact": ["stage"],
        "small": ["stage"],
    }

    measurements = []
    for profile in profiles:
        for placement in placements_by_viewport[profile.name]:
            item = _responsive_measurement(
                hierarchy_id=hierarchy["id"],
                viewport=profile.name,
                candidate=f"hint-responsive-{placement}",
                support_policy=policy_by_placement[placement],
                score=95,
                headroom=0.025,
            )
            item["candidateMode"] = "layout-hint-responsive-shadow"
            item["shadowOnly"] = True
            item["responsiveEligible"] = True
            measurements.append(item)

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=measurements,
        profiles=profiles,
    )

    assert policy["state"] == "watch"
    assert policy["selectionMode"] == "authored-hint-chain"
    assert policy["semanticContractStable"] is True
    assert policy["coverageComplete"] is True
    assert policy["transitionGapCount"] == 0
    assert policy["unverifiedTransitionCount"] == 0
    assert policy["insufficientHysteresisTransitionCount"] == 3
    assert policy["forcedBeyondBandCount"] == 0
    assert policy["monotonicViolationCount"] == 0
    assert [item["remediationLevel"] for item in policy["selections"]] == [
        0,
        0,
        0,
        0,
        1,
        1,
        1,
        1,
        2,
        2,
        2,
        3,
        3,
    ]
    assert all(
        item["capacityAdmissibilityState"] == "admissible"
        for item in policy["selections"]
    )
    assert len(policy["transitions"]) == 3
    assert all(item["overlapVerified"] for item in policy["transitions"])
    assert {
        tuple(item["overlapProbeWidths"])
        for item in policy["transitions"]
    } == {
        (1439, 1441),
        (1023, 1025),
        (719, 721),
    }

def test_layout_hint_milestone2_marks_out_of_band_selection_as_forced_not_valid():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    profile = module.ViewportProfile("wide", 1600, 1000, True)
    measurement = _responsive_measurement(
        hierarchy_id=hierarchy["id"],
        viewport="wide",
        candidate="hint-responsive-stage",
        support_policy="sequential-phase-stage",
        score=98,
        headroom=0.04,
    )
    measurement["candidateMode"] = "layout-hint-responsive-shadow"
    measurement["shadowOnly"] = True
    measurement["responsiveEligible"] = True

    policy = module.responsive_policy_for_hierarchy(
        hierarchy=hierarchy,
        measurements=[measurement],
        profiles=[profile],
    )

    selection = policy["selections"][0]
    assert selection["capacityAdmissible"] is False
    assert selection["forcedBeyondBand"] is True
    assert selection["capacityAdmissibilityState"] == "forced"
    assert policy["semanticContractStable"] is False
    assert policy["forcedBeyondBandCount"] == 1
    assert policy["state"] == "fail"


def test_layout_hint_milestone2_samples_both_sides_of_derived_capacity_boundaries():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    reference = [
        module.ViewportProfile("wide", 1600, 1000, True),
        module.ViewportProfile("desktop", 1440, 900, True),
        module.ViewportProfile("medium", 1200, 820, True),
        module.ViewportProfile("constrained", 1024, 768, True),
        module.ViewportProfile("narrow", 840, 720, True),
        module.ViewportProfile("compact", 680, 720, True),
        module.ViewportProfile("small", 560, 720, True),
    ]

    probes = module.responsive_boundary_viewports_for_hierarchy(
        hierarchy,
        reference_profiles=reference,
    )

    assert sorted(item.width for item in probes) == [
        719,
        721,
        1023,
        1025,
        1439,
        1441,
    ]
    assert all(item.responsive_probe for item in probes)
    assert all(item.height > 0 for item in probes)


def test_layout_hint_milestone2_boundary_probes_run_only_adjacent_authored_fallbacks():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    candidates = module.candidate_specs_for_hierarchy(
        hierarchy,
        module.LAYOUT_CANDIDATES,
    )
    by_placement = {
        item.get("responsivePlacement"): item
        for item in candidates
        if isinstance(item, dict) and item.get("responsivePlacement")
    }
    probe = module.ViewportProfile(
        "boundary-right-bottom-below-1439",
        1439,
        830,
        True,
    )

    assert module.candidate_applies_to_viewport(by_placement["right"], probe)
    assert module.candidate_applies_to_viewport(by_placement["bottom"], probe)
    assert not module.candidate_applies_to_viewport(by_placement["tab"], probe)
    normal = next(
        item
        for item in candidates
        if isinstance(item, dict) and item.get("mode") == "recursive-composition"
    )
    assert not module.candidate_applies_to_viewport(normal, probe)


def test_layout_hint_milestone24_generates_all_transition_envelope_probes():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    reference = [
        module.ViewportProfile("wide", 1600, 1000, True),
        module.ViewportProfile("desktop", 1440, 900, True),
        module.ViewportProfile("medium", 1200, 820, True),
        module.ViewportProfile("constrained", 1024, 768, True),
        module.ViewportProfile("narrow", 840, 720, True),
        module.ViewportProfile("compact", 680, 720, True),
        module.ViewportProfile("small", 560, 720, True),
    ]

    probes = module.responsive_transition_proof_viewports_for_hierarchy(
        hierarchy,
        reference_profiles=reference,
    )

    by_pair = {}
    for probe in probes:
        pair = probe.name.split("transition-proof-", 1)[1].rsplit("-", 1)[0]
        by_pair.setdefault(pair, []).append(probe.width)

    assert by_pair == {
        "right-bottom": [1345, 1370, 1395, 1420, 1445, 1470, 1495],
        "bottom-tab": [937, 955, 972, 990, 1007, 1025, 1042],
        "tab-stage": [660, 680, 700, 720, 740, 760, 780],
    }
    assert all(item.responsive_probe for item in probes)
    assert len(probes) == 21


def test_layout_hint_milestone24_bisection_widths_cover_a_bounded_envelope():
    module = load_module()

    assert module._transition_bisection_widths(
        lower=920,
        upper=1060,
        max_depth=1,
    ) == [990]
    assert module._transition_bisection_widths(
        lower=920,
        upper=1060,
        max_depth=2,
    ) == [955, 990, 1025]
    assert module._transition_bisection_widths(
        lower=920,
        upper=1060,
        max_depth=3,
    ) == [937, 955, 972, 990, 1007, 1025, 1042]


def test_layout_hint_milestone24_transition_probes_render_only_adjacent_pairs():
    module = load_module()
    hierarchy = _git_hierarchy_for_milestone2(module)
    candidates = module.candidate_specs_for_hierarchy(
        hierarchy,
        module.LAYOUT_CANDIDATES,
    )
    by_placement = {
        item.get("responsivePlacement"): item
        for item in candidates
        if isinstance(item, dict) and item.get("responsivePlacement")
    }
    probes = [
        ("transition-proof-right-bottom-1420", {"right", "bottom"}),
        ("transition-proof-bottom-tab-990", {"bottom", "tab"}),
        ("transition-proof-tab-stage-720", {"tab", "stage"}),
    ]

    for name, allowed in probes:
        probe = module.ViewportProfile(name, int(name.rsplit("-", 1)[1]), 760, True)
        for placement, candidate in by_placement.items():
            assert module.candidate_applies_to_viewport(candidate, probe) is (
                placement in allowed
            )
        normal = next(
            item
            for item in candidates
            if isinstance(item, dict) and item.get("mode") == "recursive-composition"
        )
        assert not module.candidate_applies_to_viewport(normal, probe)
        assert not module.candidate_applies_to_viewport("split-pane", probe)


def _transition_profile_row(
    module,
    *,
    width: int,
    from_candidate: str = "hint-responsive-bottom",
    to_candidate: str = "hint-responsive-tab",
    from_headroom: float = 0.006,
    to_headroom: float = 0.004,
    from_raw_headroom: float | None = None,
    to_raw_headroom: float | None = None,
):
    return {
        "profile": module.ViewportProfile(
            f"transition-proof-bottom-tab-{width}",
            width,
            744,
            True,
        ),
        "allOptions": [
            {
                "candidate": from_candidate,
                "status": "pass",
                "headroom": from_headroom,
                "rawHeadroom": (
                    from_headroom
                    if from_raw_headroom is None
                    else from_raw_headroom
                ),
            },
            {
                "candidate": to_candidate,
                "status": "pass",
                "headroom": to_headroom,
                "rawHeadroom": (
                    to_headroom
                    if to_raw_headroom is None
                    else to_raw_headroom
                ),
            },
        ],
    }


def test_layout_hint_milestone24_requires_full_positive_hysteresis_envelope():
    module = load_module()
    selections = [
        {
            "candidate": "hint-responsive-bottom",
            "width": 1023,
            "remediationLevel": 1,
            "band": "medium",
        },
        {
            "candidate": "hint-responsive-tab",
            "width": 840,
            "remediationLevel": 2,
            "band": "narrow",
        },
    ]
    profile_rows = [
        _transition_profile_row(module, width=960),
        _transition_profile_row(module, width=984),
        _transition_profile_row(module, width=1008),
    ]

    transition = module.responsive_transition_rules(
        selections,
        hysteresis_px=48,
        profile_rows=profile_rows,
    )[0]

    assert transition["overlapVerified"] is True
    assert transition["positiveOverlapVerified"] is True
    assert transition["positiveOverlapProbeWidths"] == [960, 984, 1008]
    assert transition["positiveOverlapMinWidth"] == 960
    assert transition["positiveOverlapMaxWidth"] == 1008
    assert transition["positiveOverlapWidthPx"] == 48
    assert transition["requiredHysteresisPx"] == 48
    assert transition["hysteresisRequirementMet"] is True
    assert transition["transitionState"] == "verified"
    assert transition["switchDownBelow"] == 960
    assert transition["switchUpAbove"] == 1008
    assert transition["hysteresisPx"] == 48


def test_layout_hint_milestone24_marks_narrow_positive_overlap_as_watch_evidence():
    module = load_module()
    selections = [
        {
            "candidate": "hint-responsive-bottom",
            "width": 1023,
            "remediationLevel": 1,
            "band": "medium",
        },
        {
            "candidate": "hint-responsive-tab",
            "width": 840,
            "remediationLevel": 2,
            "band": "narrow",
        },
    ]
    profile_rows = [
        _transition_profile_row(module, width=984),
        _transition_profile_row(module, width=1008),
    ]

    transition = module.responsive_transition_rules(
        selections,
        hysteresis_px=48,
        profile_rows=profile_rows,
    )[0]

    assert transition["positiveOverlapVerified"] is True
    assert transition["positiveOverlapWidthPx"] == 24
    assert transition["hysteresisRequirementMet"] is False
    assert transition["transitionState"] == "narrow-positive-overlap"
    assert transition["hysteresisPx"] == 24


def test_layout_hint_milestone24_rejects_tolerance_only_overlap_as_unverified():
    module = load_module()
    selections = [
        {
            "candidate": "hint-responsive-bottom",
            "width": 1023,
            "remediationLevel": 1,
            "band": "medium",
        },
        {
            "candidate": "hint-responsive-tab",
            "width": 840,
            "remediationLevel": 2,
            "band": "narrow",
        },
    ]
    profile_rows = [
        _transition_profile_row(
            module,
            width=960,
            from_headroom=0.0,
            to_headroom=0.0,
            from_raw_headroom=-0.0001,
            to_raw_headroom=-0.0002,
        ),
        _transition_profile_row(
            module,
            width=1008,
            from_headroom=0.0,
            to_headroom=0.0,
            from_raw_headroom=-0.0001,
            to_raw_headroom=-0.0002,
        ),
    ]

    transition = module.responsive_transition_rules(
        selections,
        hysteresis_px=48,
        profile_rows=profile_rows,
    )[0]

    assert transition["overlapVerified"] is True
    assert transition["positiveOverlapVerified"] is False
    assert transition["hysteresisRequirementMet"] is False
    assert transition["transitionState"] == "tolerance-only-overlap"
    assert transition["toleranceOnlyProbeWidths"] == [960, 1008]


def test_layout_hint_milestone24_css_expands_transition_envelopes_without_lowering_floors():
    module = load_module()

    assert "minmax(300px, 33fr) minmax(0, 67fr)" in module.TRIAL_CSS
    assert "minmax(112px, 15%)" in module.TRIAL_CSS
    assert "minmax(48px, 8%)" in module.TRIAL_CSS
    assert module.PHASE_SHARE_FLOOR_TOLERANCE == 0.0005
    assert (
        module.RESPONSIVE_POLICY_VERSION
        == "capacity-derived-presentation-contract-v6"
    )
    assert (
        module.RESPONSIVE_TRANSITION_PROOF_VERSION
        == "robust-transition-envelope-proof-v3"
    )
