from __future__ import annotations

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

    assert len(hierarchies) == 8
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


def test_generate_rollup_pngs_orders_best_to_worst_and_caps_at_eight(tmp_path):
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
    for index, (candidate, score, status) in enumerate(ranking, start=1):
        file_name = f"trial-{index}.png"
        Image.new("RGB", (640, 400), (200, 200, 200)).save(tmp_path / file_name)
        measurements.append(
            {
                "hierarchyId": "document-workbench",
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
    assert rollup["hierarchyId"] == "document-workbench"
    assert rollup["viewportProfile"] == "desktop"
    assert rollup["topCount"] == 8
    assert rollup["columns"] == 4
    assert rollup["rows"] == 2
    assert rollup["candidates"] == [
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
    assert width < 1600
    assert height < 900


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
                "hierarchyId": "document-workbench",
                "viewportProfile": "desktop",
                "file": "document-workbench--desktop--rollup.png",
                "candidates": ["focus-priority", "split-pane"],
                "columns": 4,
                "rows": 2,
                "topCount": 2,
            }
        ],
        "rollupFiles": ["document-workbench--desktop--rollup.png"],
    }

    json_path, md_path = module.write_reports(report, tmp_path)
    assert json_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "## Rollup PNGs" in text
    assert "document-workbench--desktop--rollup.png" in text
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
