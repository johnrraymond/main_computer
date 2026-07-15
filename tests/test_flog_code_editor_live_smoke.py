from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "main_computer" / "flog_code_editor_live_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "flog_code_editor_live_smoke",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def flog():
    return load_module()


def good_measurement():
    return {
        "visible": {
            "root": True,
            "shell": True,
            "body": True,
            "editorGroup": True,
            "activePane": True,
            "primarySurface": True,
        },
        "fillRatios": {
            "editorGroupBlock": 0.99,
            "editorGroupInline": 0.99,
            "activePaneBlock": 0.99,
            "primarySurfaceBlock": 0.96,
        },
        "dimensions": {
            "centerSlotBlock": 626,
            "centerSlotInline": 780,
            "editorGroupBlock": 620,
            "primarySurfaceExpectedBlock": 520,
            "unusedPrimaryWorkBlock": 12,
        },
        "ownershipSlot": {
            "source": "inferred-live-dock-ownership",
        },
        "controls": {
            "foreignIntercepted": 0,
            "clipped": 0,
            "withoutActionableSample": 0,
            "outsidePrimarySurface": 0,
            "outsideCenterSlot": 0,
            "foreignOwners": [],
        },
        "proofDock": {
            "expanded": False,
            "blockSize": 36,
        },
    }


def test_parse_viewports(flog):
    profiles = flog.parse_viewports("wide=1600x1000,medium=1200x820")
    assert [(item.name, item.width, item.height) for item in profiles] == [
        ("wide", 1600, 1000),
        ("medium", 1200, 820),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "wide",
        "wide=1600",
        "wide=0x900",
        "wide=1600x0",
    ],
)
def test_parse_viewports_rejects_invalid_values(flog, text):
    with pytest.raises((ValueError, TypeError)):
        flog.parse_viewports(text)


def test_parse_states(flog):
    states = flog.parse_states(
        "runtime-collapsed-proof,inspector-bottom"
    )
    assert [item.name for item in states] == [
        "runtime-collapsed-proof",
        "inspector-bottom",
    ]


def test_unknown_state_is_rejected(flog):
    with pytest.raises(ValueError):
        flog.parse_states("does-not-exist")


def test_good_fill_passes(flog):
    result = flog.classify_measurement(good_measurement())
    assert result["status"] == "pass"
    assert result["failures"] == []


def test_primary_surface_underfill_is_hard_failure(flog):
    measurement = good_measurement()
    measurement["fillRatios"]["primarySurfaceBlock"] = 0.54
    measurement["dimensions"]["unusedPrimaryWorkBlock"] = 260
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "primary work surface fills only" in reason
        for reason in result["failures"]
    )
    assert any(
        "owned but unused" in reason
        for reason in result["failures"]
    )


def test_active_pane_underfill_is_hard_failure(flog):
    measurement = good_measurement()
    measurement["fillRatios"]["activePaneBlock"] = 0.72
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "active pane fills only" in reason
        for reason in result["failures"]
    )


def test_editor_group_underfill_is_hard_failure(flog):
    measurement = good_measurement()
    measurement["fillRatios"]["editorGroupBlock"] = 0.70
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "editor group fills only" in reason
        for reason in result["failures"]
    )


def test_blocked_controls_are_hard_failures(flog):
    measurement = good_measurement()
    measurement["controls"]["foreignIntercepted"] = 1
    measurement["controls"]["withoutActionableSample"] = 1
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any("intercepted" in reason for reason in result["failures"])
    assert any("no actionable sample" in reason for reason in result["failures"])


def test_collapsed_proof_dock_must_be_a_strip(flog):
    measurement = good_measurement()
    measurement["proofDock"]["blockSize"] = 140
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "collapsed proof dock retains" in reason
        for reason in result["failures"]
    )


def test_expanded_proof_dock_must_be_useful(flog):
    measurement = good_measurement()
    measurement["proofDock"] = {"expanded": True, "blockSize": 90}
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "expanded proof dock is only" in reason
        for reason in result["failures"]
    )


def test_reclamation_passes_when_editor_recovers_proof_space(flog):
    collapsed = {
        "dimensions": {"editorGroupBlock": 700},
        "proofDock": {"blockSize": 36},
    }
    expanded = {
        "dimensions": {"editorGroupBlock": 500},
        "proofDock": {"blockSize": 236},
    }
    result = flog.classify_reclamation(collapsed, expanded)
    assert result["status"] == "pass"
    assert result["reclaimRatio"] == pytest.approx(1.0)


def test_reclamation_fails_when_editor_does_not_grow(flog):
    collapsed = {
        "dimensions": {"editorGroupBlock": 540},
        "proofDock": {"blockSize": 36},
    }
    expanded = {
        "dimensions": {"editorGroupBlock": 500},
        "proofDock": {"blockSize": 236},
    }
    result = flog.classify_reclamation(collapsed, expanded)
    assert result["status"] == "fail"
    assert result["reclaimRatio"] == pytest.approx(0.2)



def test_reclamation_compares_each_center_slot_after_responsive_remediation(flog):
    collapsed = {
        "dimensions": {"centerSlotBlock": 362, "editorGroupBlock": 362},
        "fillRatios": {"editorGroupBlock": 1.0},
        "layout": {
            "inspectorPlacement": "bottom",
            "explorerPlacement": "left",
            "centerTab": "editor",
        },
        "proofDock": {"blockSize": 34},
    }
    expanded = {
        "dimensions": {"centerSlotBlock": 372, "editorGroupBlock": 372},
        "fillRatios": {"editorGroupBlock": 1.0},
        "layout": {
            "inspectorPlacement": "tab",
            "explorerPlacement": "left",
            "centerTab": "editor",
        },
        "proofDock": {"blockSize": 216},
    }
    result = flog.classify_reclamation(collapsed, expanded)
    assert result["status"] == "pass"
    assert result["comparable"] is False
    assert result["mode"] == "responsive-ownership-remediation"

def test_static_contract_recognizes_live_code_editor(flog):
    result = flog.inspect_static_contract(REPO_ROOT)
    assert result["state"] == "complete"
    checks = {item["id"]: item for item in result["checks"]}
    assert checks["editor-primary-work"]["passed"] is True
    assert checks["stable-editor-user-id"]["passed"] is True
    assert checks["contract-primary-work-role"]["passed"] is True
    assert checks["editor-minimum-block"]["passed"] is True
    assert checks["semantic-layout-controller"]["passed"] is True
    assert result["fillLaw"]["source"] in {
        "explicit",
        "inferred-from-required-center-primary-work",
    }
    assert result["ownershipSlotLaw"]["source"] in {
        "explicit-center-slot",
        "inferred-from-live-dock-owners",
    }


def test_compact_measurement_excludes_full_dom_rect_dump(flog):
    item = {
        "viewportProfile": "desktop",
        "state": "runtime-collapsed-proof",
        "activePane": "runtime",
        "fillRatios": {"primarySurfaceBlock": 0.5},
        "dimensions": {"unusedPrimaryWorkBlock": 200},
        "ownershipSlot": {"source": "inferred-live-dock-ownership"},
        "proofDock": {},
        "layout": {},
        "controls": {},
        "wrapperChain": [{"selector": ".example"}],
        "classification": {"status": "fail"},
        "configuration": {"ok": True},
        "png": "proof.png",
        "rects": {"root": {"width": 1440}},
        "hugeDomDump": ["not", "wanted"],
    }
    compact = flog.compact_measurement(item)
    assert compact["png"] == "proof.png"
    assert compact["ownershipSlot"]["source"] == "inferred-live-dock-ownership"
    assert "rects" not in compact
    assert "hugeDomDump" not in compact


def test_report_summary_fails_on_reclamation_gap(flog):
    measurements = [
        {
            "viewportProfile": "desktop",
            "state": "runtime-collapsed-proof",
            "fillRatios": {
                "editorGroupBlock": 1.0,
                "editorGroupInline": 1.0,
                "primarySurfaceBlock": 0.96,
            },
            "dimensions": {"editorGroupBlock": 540},
            "proofDock": {"blockSize": 36},
            "classification": {"status": "pass"},
        },
        {
            "viewportProfile": "desktop",
            "state": "runtime-expanded-proof",
            "fillRatios": {
                "editorGroupBlock": 1.0,
                "editorGroupInline": 1.0,
                "primarySurfaceBlock": 0.96,
            },
            "dimensions": {"editorGroupBlock": 500},
            "proofDock": {"blockSize": 236},
            "classification": {"status": "pass"},
        },
    ]
    summary = flog.summarize_report(
        static_contract={"state": "complete"},
        measurements=measurements,
    )
    assert summary["status"] == "fail"
    assert summary["reclamationFailureCount"] == 1


def test_markdown_reports_fill_ratios(flog):
    report = {
        "generatedAt": "2026-07-12T00:00:00+00:00",
        "kind": flog.REPORT_KIND,
        "version": flog.REPORT_VERSION,
        "staticContract": {
            "state": "complete",
            "fillLaw": {"source": "inferred-from-required-center-primary-work"},
        },
        "summary": {
            "status": "fail",
            "trialCount": 1,
            "byViewport": {
                "desktop": {
                    "statusCounts": {"pass": 0, "watch": 0, "fail": 1},
                    "worstCenterBlockFill": 0.98,
                    "worstCenterInlineFill": 0.99,
                    "worstSurfaceFill": 0.54,
                    "foreignOwnerCount": 0,
                    "reclamation": {"status": "not-measured"},
                }
            },
        },
        "measurements": [
            {
                "viewportProfile": "desktop",
                "state": "runtime-collapsed-proof",
                "fillRatios": {
                    "editorGroupBlock": 0.98,
                    "editorGroupInline": 0.99,
                    "activePaneBlock": 0.97,
                    "primarySurfaceBlock": 0.54,
                },
                "dimensions": {
                    "centerSlotInline": 780,
                    "centerSlotBlock": 620,
                    "unusedPrimaryWorkBlock": 220,
                },
                "ownershipSlot": {
                    "source": "inferred-live-dock-ownership",
                },
                "controls": {"foreignOwners": []},
                "classification": {
                    "status": "fail",
                    "score": 60,
                    "failures": ["primary work surface fills only 54.0%"],
                    "warnings": [],
                },
                "png": "proof.png",
            }
        ],
        "pngFiles": ["proof.png"],
    }
    markdown = flog.render_markdown(report)
    assert "Editor center block fill: `98.0%`" in markdown
    assert "Primary surface fill: `54.0%`" in markdown
    assert "primary work surface fills only 54.0%" in markdown



def test_missing_center_slot_is_hard_failure(flog):
    measurement = good_measurement()
    measurement["ownershipSlot"] = {}
    measurement["dimensions"]["centerSlotBlock"] = 0
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any(
        "center ownership slot could not be resolved" in reason
        for reason in result["failures"]
    )


def test_foreign_owner_identity_is_reported(flog):
    measurement = good_measurement()
    measurement["controls"]["foreignIntercepted"] = 1
    measurement["controls"]["foreignOwners"] = [
        {
            "layoutUserId": "code-editor.inspector",
            "placement": "bottom",
            "controls": ["code-studio-runtime-draft"],
        }
    ]
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    joined = " ".join(result["failures"])
    assert "code-editor.inspector" in joined
    assert "code-studio-runtime-draft" in joined


def test_control_escape_is_hard_failure(flog):
    measurement = good_measurement()
    measurement["controls"]["outsidePrimarySurface"] = 1
    measurement["controls"]["outsideCenterSlot"] = 1
    result = flog.classify_measurement(measurement)
    assert result["status"] == "fail"
    assert any("escape their primary-surface bounds" in reason for reason in result["failures"])
    assert any("escape the owned center slot" in reason for reason in result["failures"])


def test_measurement_uses_center_ownership_not_whole_body(flog):
    script = flog.MEASURE_JS
    assert "inferred-live-dock-ownership" in script
    assert 'inspectorPlacement === "bottom"' in script
    assert 'inspectorPlacement === "tab"' in script
    assert "ratio(editorCenterIntersection.height, centerSlotRect.height)" in script
    assert "ratio(editorRect.height, bodyRect.height)" not in script
    assert "foreignOwners" in script
    assert "elementsFromPoint" in script


def test_markdown_reports_foreign_owner(flog):
    report = {
        "generatedAt": "2026-07-12T00:00:00+00:00",
        "kind": flog.REPORT_KIND,
        "version": flog.REPORT_VERSION,
        "staticContract": {
            "state": "complete",
            "fillLaw": {"source": "inferred-from-required-center-primary-work"},
            "ownershipSlotLaw": {"source": "inferred-from-live-dock-owners"},
        },
        "summary": {
            "status": "fail",
            "trialCount": 1,
            "byViewport": {
                "compact": {
                    "statusCounts": {"pass": 0, "watch": 0, "fail": 1},
                    "worstCenterBlockFill": 1.0,
                    "worstCenterInlineFill": 1.0,
                    "worstSurfaceFill": 1.0,
                    "foreignOwnerCount": 1,
                    "reclamation": {"status": "not-measured"},
                }
            },
        },
        "measurements": [
            {
                "viewportProfile": "compact",
                "state": "runtime-expanded-proof",
                "fillRatios": {
                    "editorGroupBlock": 1.0,
                    "editorGroupInline": 1.0,
                    "activePaneBlock": 1.0,
                    "primarySurfaceBlock": 1.0,
                },
                "dimensions": {
                    "centerSlotInline": 500,
                    "centerSlotBlock": 400,
                    "unusedPrimaryWorkBlock": 0,
                },
                "ownershipSlot": {"source": "inferred-live-dock-ownership"},
                "controls": {
                    "foreignOwners": [
                        {
                            "layoutUserId": "code-editor.inspector",
                            "placement": "bottom",
                            "sampleCount": 2,
                            "controls": ["code-studio-runtime-draft"],
                        }
                    ]
                },
                "classification": {
                    "status": "fail",
                    "score": 95,
                    "failures": ["foreign interception"],
                    "warnings": [],
                },
                "png": "proof.png",
            }
        ],
        "pngFiles": ["proof.png"],
    }
    markdown = flog.render_markdown(report)
    assert "code-editor.inspector" in markdown
    assert "code-studio-runtime-draft" in markdown

def test_static_only_main_writes_reports(flog, tmp_path):
    output = tmp_path / "report"
    exit_code = flog.main(
        [
            "--repo",
            str(REPO_ROOT),
            "--output-dir",
            str(output),
            "--static-only",
        ]
    )
    assert exit_code == 0
    json_path = output / "code-editor-live-flog-report.json"
    md_path = output / "code-editor-live-flog-report.md"
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["staticContract"]["state"] == "complete"
    assert payload["summary"]["status"] == "pass"

def test_code_editor_live_overlay_uses_edge_rounded_device_pixels(flog):
    measure_js = flog.MEASURE_JS
    annotate_js = flog.ANNOTATE_JS

    assert "pixelGeometry: \"css-edge-rounded-device-pixels-v1\"" in measure_js
    assert "Math.round(leftCss * devicePixelRatio)" in measure_js
    assert "Math.round(rightCss * devicePixelRatio)" in measure_js
    assert "measuredRect?.pixelRect ? cssRectFromPixelRect(measuredRect.pixelRect)" in annotate_js
    assert "border: `1px solid ${colors[key]}`" in annotate_js
    assert "borderRadius: \"0\"" in annotate_js
