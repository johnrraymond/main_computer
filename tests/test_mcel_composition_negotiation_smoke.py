"""Isolation and negative controls for the opt-in semantic negotiation smoke.

This suite proves prototype feasibility, NOT production integration. Run the
browser trial explicitly before considering implementation or promotion:
    python -m tools.mcel_composition_negotiation_smoke --browser
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from main_computer.mcel_calculator_expanded_host import compile_expanded
from tools.mcel_composition_negotiation_smoke import (
    SmokeRefusal, derive_unit, negotiate, run_static_smoke,
)


@pytest.fixture(scope="module")
def calculator_ir():
    return compile_expanded()


def test_producer_result_pair_is_proven_from_compiled_ir(calculator_ir):
    source, output, evidence = derive_unit(calculator_ir, "graphing")
    assert source.kind == "interactive-form"
    assert output.kind == "visual-series"
    assert source.field_count == 5
    assert evidence["stateEvidence"] == ["state:graph-points"]
    assert evidence["sourceNodeKinds"] == ["form"]
    assert evidence["resultNodeKinds"] == ["plot"]


@pytest.mark.parametrize("viewport,expected", [
    (390, "stacked"), (800, "two-region"),
    (1100, "three-region"), (1260, "three-region")
])
def test_graph_arrangement_emerges_from_available_space(calculator_ir, viewport, expected):
    result = negotiate(calculator_ir, "graphing", viewport)
    assert result["winner"]["arrangement"] == expected
    assert result["candidateCount"] >= 1
    assert result["winner"]["score"] >= max(x["score"] for x in result["alternatives"])
    assert not result["productionPromotion"] and result["experimental"]
    if expected == "three-region":
        # Explicitly protects the controls from the tempting "graph takes all"
        # solution while rewarding extra graph area after controls saturate.
        assert result["winner"]["input_width"] >= result["sourceDemand"]["min_width"]
        assert result["winner"]["result_width"] > result["winner"]["input_width"]
        assert result["winner"]["input_width"] < 410
    if expected == "stacked":
        assert not any(x["arrangement"] == "three-region" for x in result["alternatives"])


@pytest.mark.parametrize("mode", ["basic", "symbolic"])
def test_scalar_output_stays_attached_to_work_unit(calculator_ir, mode):
    outcome = negotiate(calculator_ir, mode, 1260)
    assert outcome["resultDemand"]["kind"] == "scalar"
    assert outcome["winner"]["arrangement"] == "compact-unit"
    assert outcome["winner"]["input_width"] == outcome["winner"]["result_width"]


def test_result_representation_changes_composition_without_app_specific_rules(calculator_ir):
    alternate = deepcopy(calculator_ir)
    node = next(n for n in alternate["surfaces"][0]["nodes"]
                if n.get("nodeKind") == "plot")
    state = node.pop("state")
    node.update(nodeKind="state-value", value={"kind": "state.read", "state": state})
    node.pop("x", None)
    node.pop("y", None)
    outcome = negotiate(alternate, "graphing", 1260)
    assert outcome["resultDemand"]["kind"] == "scalar"
    assert outcome["winner"]["arrangement"] == "compact-unit"


def test_other_visual_representation_can_use_same_negotiation(calculator_ir):
    alternate = deepcopy(calculator_ir)
    node = next(n for n in alternate["surfaces"][0]["nodes"]
                if n.get("nodeKind") == "plot")
    node["nodeKind"] = "collection"
    node["value"] = {"kind": "state.read", "state": node.pop("state")}
    outcome = negotiate(alternate, "graphing", 1260)
    assert outcome["resultDemand"]["kind"] == "collection"
    assert outcome["winner"]["arrangement"] == "three-region"


def test_names_titles_and_identity_cannot_bias_geometry(calculator_ir):
    alternate = deepcopy(calculator_ir)
    alternate["application"]["appId"] = "unrelated-prototype"
    for region in alternate["surfaces"][0]["interfaceIntent"]["regions"]:
        for block in region.get("staticContent", []):
            block["text"] = "unrelated words and heading"
    for node in alternate["surfaces"][0]["nodes"]:
        node["sourceName"] = "not-calculator"
    a = negotiate(calculator_ir, "graphing", 1260)
    b = negotiate(alternate, "graphing", 1260)
    assert a["winner"] == b["winner"]


def test_broken_dataflow_fails_closed(calculator_ir):
    altered = deepcopy(calculator_ir)
    target = next(n for n in altered["surfaces"][0]["nodes"] if n.get("nodeKind") == "plot")
    target["state"] = {"ref": "state:unrelated-to-producer"}
    with pytest.raises(SmokeRefusal, match="verified mode-local producer/result"):
        negotiate(altered, "graphing", 1260)


def test_ambiguous_dataflow_fails_closed(calculator_ir):
    altered = deepcopy(calculator_ir)
    surface = altered["surfaces"][0]
    result_region = next(r for r in surface["interfaceIntent"]["regions"]
                         if r.get("purpose") == "results" and
                         (r.get("visibleWhen") or {}).get("equals") == "graphing")
    extra = deepcopy(result_region)
    extra["id"] += "-duplicate"
    surface["interfaceIntent"]["regions"].append(extra)
    with pytest.raises(SmokeRefusal, match="exactly one verified"):
        negotiate(altered, "graphing", 1260)


def test_invalid_or_impossibly_small_space_refused(calculator_ir):
    for width, height in ((0,740),(200,740),(800,190)):
        with pytest.raises(SmokeRefusal):
            negotiate(calculator_ir, "graphing", width, height)


def test_height_affects_negotiation_not_just_viewport_width(calculator_ir):
    tall = negotiate(calculator_ir, "graphing", 390, 1400)
    short = negotiate(calculator_ir, "graphing", 390, 500)
    assert tall["winner"]["arrangement"] == "stacked"
    assert short["winner"]["arrangement"] == "stacked"
    assert tall["winner"]["score"] > short["winner"]["score"]


def test_static_rehearsal_covered_modes(calculator_ir):
    report = run_static_smoke(calculator_ir)
    assert len(report) == 7
    assert {r["relationship"]["mode"] for r in report} == {"graphing", "basic", "symbolic"}
    assert all(r["relationship"]["stateEvidence"] for r in report)


def test_live_bound_ui_accepts_negotiated_reflow(calculator_ir):
    pytest.importorskip("playwright")
    from tools.mcel_composition_negotiation_smoke import run_browser_smoke
    results = run_browser_smoke(calculator_ir)
    assert [x["viewport"] for x in results] == [390, 800, 1260]
    assert [x["negotiation"]["winner"]["arrangement"] for x in results] == [
        "stacked", "two-region", "three-region"
    ]
    assert all(x["functionalityRetained"] and x["stateRetained"] for x in results)


def test_width_sweep_has_no_layout_oscillation(calculator_ir):
    """An extra ten pixels must not repeatedly reverse the chosen topology."""
    layouts = [negotiate(calculator_ir, "graphing", width)["winner"]["arrangement"]
               for width in range(260, 1501, 10)]
    transitions = [(a, b) for a, b in zip(layouts, layouts[1:]) if a != b]
    assert transitions == [("stacked", "two-region"), ("two-region", "three-region")]
