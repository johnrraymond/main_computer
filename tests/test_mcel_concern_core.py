from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_concern_detector_on_project() -> dict:
    core = SCRIPTS / "mcel-concern-core.js"
    files = [
        SCRIPTS / "git-tools-project-workflow.js",
        SCRIPTS / "git-tools-file-basket.js",
        SCRIPTS / "task-manager.js",
        SCRIPTS / "file-explorer.js",
        SCRIPTS / "website-builder.js",
        SCRIPTS / "chat-console.js",
        SCRIPTS / "worker.js",
    ]
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync({json.dumps(str(core))}, "utf8"), {{filename: "mcel-concern-core.js"}});
const files = {json.dumps([str(path) for path in files])}.map((path) => {{
  return {{path, text: fs.readFileSync(path, "utf8")}};
}});
const report = globalThis.McelConcernCore.analyzeProject(files, {{projectId: "main_computer_test.real"}});
console.log(JSON.stringify(report));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_mcel_concern_core_is_loaded_before_lab_acid_test() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    core = (SCRIPTS / "mcel-concern-core.js").read_text(encoding="utf-8")
    elements = (SCRIPTS / "mcel-elements-core.js").read_text(encoding="utf-8")
    acid = (SCRIPTS / "mcel-element-acid-test.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-concern-core.js -->" in html
    assert "<!-- @include applications/scripts/mcel-project-concern-workbench.js -->" in html
    assert (
        html.index("mcel-toolkit-core.js")
        < html.index("mcel-concern-core.js")
        < html.index("mcel-project-concern-workbench.js")
        < html.index("mcel-element-registry.js")
    )

    assert "global.McelConcernCore" in core
    assert "analyzeProject" in core
    assert "projectSpecimenFiles" in core
    assert "concern.file-basket" in core
    assert "concern.resource-browser" in core
    assert "concern.deploy-preflight" in core
    assert "concern.execution-cell" in core

    assert "element.concern.catalog" in elements
    assert "element.concern.detector" in elements
    assert "element.concern.boundary-map" in elements
    assert "element.concern.contract-gap" in elements
    assert "element.concern.mvc-split" in elements
    assert "element.concern.replacement-plan" in elements
    assert "element.concern.project-workbench" in elements
    assert "element.concern.work-order" in elements

    assert "renderConcernIntelligenceAtlas" in acid
    assert "mcelConcernCore" in acid
    assert "concernDetectorReady" in acid
    assert "Concern Intelligence Atlas" in acid
    assert "Concern → contract → toolkit plan" in acid
    assert "renderProjectConcernWorkbench" in acid

    assert ".mcel-concern-atlas" in css
    assert ".mcel-concern-card" in css
    assert ".mcel-concern-boundary-map" in css
    assert ".mcel-concern-mvc-split" in css


def test_mcel_concern_detector_finds_real_project_concerns() -> None:
    report = _run_concern_detector_on_project()
    ids = {concern["id"] for concern in report["concerns"]}

    assert report["projectId"] == "main_computer_test.real"
    assert report["analyzedFileCount"] == 7
    assert report["detectedConcernCount"] >= 6
    assert report["severeContractGapCount"] >= 3
    assert report["canDriveMcelContracts"] is True

    assert "concern.file-basket" in ids
    assert "concern.resource-browser" in ids
    assert "concern.deploy-preflight" in ids
    assert "concern.change-review-list" in ids
    assert "concern.execution-cell" in ids
    assert "concern.output-renderer" in ids


def test_mcel_concern_detector_maps_file_basket_to_mvc_contract_and_toolkit() -> None:
    report = _run_concern_detector_on_project()
    file_basket = next(concern for concern in report["concerns"] if concern["id"] == "concern.file-basket")

    assert file_basket["contractGap"] == "severe"
    assert file_basket["recommendedContract"] == "pattern.file-basket"
    assert file_basket["boundaryHealth"] == "tangled-mvc-boundary"
    assert "model" in file_basket["roles"]
    assert "controller" in file_basket["roles"]
    assert "view-gap" in file_basket["roles"]
    assert "safety" in file_basket["roles"]

    labels = {item["label"] for item in file_basket["ranges"]}
    assert "candidate file model" in labels
    assert "typed fields collapsed into node title" in labels
    assert "selected file output extraction" in labels
    assert "preserved view adapter source" in labels

    toolkit = set(file_basket["recommendedToolkit"])
    assert "control.selection.tristate" in toolkit
    assert "control.disclosure" in toolkit
    assert "controller.selection" in toolkit
    assert "controller.safety-gate" in toolkit
    assert "collection.treegrid" in toolkit


def test_mcel_concern_detector_flags_project_contract_gaps_with_line_ranges() -> None:
    report = _run_concern_detector_on_project()
    major = [
        concern for concern in report["concerns"]
        if concern["contractGap"] in {"severe", "major"}
    ]

    assert major
    for concern in major:
        assert concern["file"].endswith((".js",))
        assert concern["confidence"] >= 0.6
        assert concern["missingContractReason"]
        assert concern["ranges"], concern["id"]
        assert concern["recommendedToolkit"], concern["id"]
        assert concern["recommendedContract"].startswith("pattern.")
