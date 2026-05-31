from __future__ import annotations

import html as html_lib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"

MCEL_LAYER_INVENTORY = {
    "core-public-api": [
        "main_computer/web/applications/scripts/mcel-core.js",
    ],
    "contract-engine": [
        "main_computer/web/applications/scripts/mcel-contract.js",
        "main_computer/web/applications/scripts/mcel-engine.js",
        "main_computer/web/applications/scripts/mcel-editor.js",
    ],
    "law-internal": [
        "main_computer/web/applications/scripts/mcel-law-registry.js",
        "main_computer/web/applications/scripts/mcel-style-law.js",
        "main_computer/web/applications/scripts/mcel-layout-law.js",
        "main_computer/web/applications/scripts/mcel-component-law.js",
        "main_computer/web/applications/scripts/mcel-state-law.js",
        "main_computer/web/applications/scripts/mcel-data-law.js",
        "main_computer/web/applications/scripts/mcel-form-law.js",
        "main_computer/web/applications/scripts/mcel-action-law.js",
        "main_computer/web/applications/scripts/mcel-render-law.js",
        "main_computer/web/applications/scripts/mcel-a11y-law.js",
        "main_computer/web/applications/scripts/mcel-performance-law.js",
        "main_computer/web/applications/scripts/mcel-platform-spine.js",
    ],
    "browser-proof": [
        "main_computer/web/applications/scripts/mcel-browser-observer.js",
        "main_computer/web/applications/scripts/mcel-browser-runner.js",
    ],
    "chrome-law": [
        "main_computer/web/applications/scripts/mcel-chrome-law.js",
    ],
    "lab-ui": [
        "main_computer/web/applications/scripts/mcel-lab.js",
        "main_computer/web/applications/scripts/dom-bindings/mcel-lab.js",
        "main_computer/web/applications/apps/mcel-lab.html",
    ],
    "dev-proof-harness": [
        "main_computer/web/applications/scripts/mcel-workbench.js",
        "main_computer/web/applications/scripts/mcel-command-surface.js",
        "main_computer/web/applications/scripts/mcel-project-store.js",
        "main_computer/web/applications/scripts/mcel-scenarios.js",
        "main_computer/web/applications/scripts/mcel-graph.js",
        "main_computer/web/applications/scripts/mcel-ops-runner.js",
        "main_computer/web/applications/scripts/mcel-acid-tests.js",
        "main_computer/web/applications/scripts/mcel-test-harness.js",
        "main_computer/web/applications/scripts/mcel-supervisor.js",
        "main_computer/web/applications/scripts/mcel-kernel.js",
        "main_computer/web/applications/scripts/mcel-site-skeleton.js",
    ],
}

CHROME_OPTIONS = [
    ("chrome-strict-hierarchy", "Strict Hierarchy"),
    ("chrome-editorial-flow", "Editorial Flow"),
    ("chrome-cluster-grid", "Cluster Grid"),
    ("chrome-spotlight", "Spotlight"),
    ("chrome-journey", "Journey"),
    ("chrome-compact-disclosure", "Compact Disclosure"),
]

CORE_FACADE_METHODS = [
    "compile",
    "serialize",
    "repair",
    "audit",
    "inspect",
    "planCommand",
    "applyCommand",
    "runScenarioMatrix",
    "runAcidTests",
    "buildEvidencePacket",
    "runProof",
    "buildSubsumptionLattice",
    "buildWorkbenchPlan",
    "listChromes",
    "normalizeChrome",
    "describeChrome",
    "applyChrome",
    "runBrowserProof",
]


def _read_repo_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _inventory_paths() -> list[str]:
    return [path for paths in MCEL_LAYER_INVENTORY.values() for path in paths]


def _normalize_text(text: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _element_body_by_id(markup: str, tag: str, element_id: str) -> str:
    match = re.search(
        rf"<{tag}\b(?=[^>]*\bid=[\"']{re.escape(element_id)}[\"'])(?P<attrs>[^>]*)>"
        rf"(?P<body>.*?)</{tag}>",
        markup,
        re.S,
    )
    assert match, f"missing <{tag}> with id={element_id!r}"
    return match.group("body")


def _select_options(markup: str, select_id: str) -> list[tuple[str, str]]:
    body = _element_body_by_id(markup, "select", select_id)
    options: list[tuple[str, str]] = []
    for option in re.finditer(r"<option\b(?P<attrs>[^>]*)>(?P<body>.*?)</option>", body, re.S):
        value_match = re.search(r"\bvalue=([\"'])(?P<value>.*?)\1", option.group("attrs"))
        assert value_match, f"option in {select_id!r} is missing a value"
        options.append((value_match.group("value"), _normalize_text(option.group("body"))))
    return options


def _extract_function_source(script: str, function_name: str) -> str:
    match = re.search(rf"function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{", script)
    assert match, f"missing function {function_name}"
    depth = 0
    for index in range(match.start(), len(script)):
        char = script[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[match.start() : index + 1]
    raise AssertionError(f"could not find end of function {function_name}")


def test_mcel_layer_inventory_is_complete_and_machine_readable() -> None:
    listed_paths = _inventory_paths()

    assert len(listed_paths) == len(set(listed_paths)), "MCEL layer inventory contains duplicate paths"

    missing = [path for path in listed_paths if not (ROOT / path).exists()]
    assert missing == []

    discovered_script_paths = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (WEB_APP / "scripts").rglob("*.js")
        if path.name.startswith("mcel-")
        or str(path.relative_to(WEB_APP / "scripts")).replace("\\", "/") == "dom-bindings/mcel-lab.js"
    )
    inventoried_script_paths = sorted(
        path for path in listed_paths if path.startswith("main_computer/web/applications/scripts/")
    )

    assert discovered_script_paths == inventoried_script_paths


def test_mcel_core_public_facade_boundary_is_exported() -> None:
    core = _read_repo_text("main_computer/web/applications/scripts/mcel-core.js")

    assert "var MCEL" in core
    assert "window.MCEL = MCEL" in core
    assert "window.McelLabCore = MCEL" in core
    assert "McelLabChromeLaw" in core

    exported_object = re.search(r"return Object\.freeze\(\{(?P<body>.*?)\}\);", core, re.S)
    assert exported_object, "MCEL core should return a frozen public facade"

    facade_body = exported_object.group("body")
    assert "version:" in facade_body
    for method in CORE_FACADE_METHODS:
        assert re.search(rf"\b{re.escape(method)}\s*(?:,|:)", facade_body), method

    assert "platform: platformSpine" in facade_body
    assert "workbench" in facade_body
    assert "browserRunner" in facade_body
    assert "laws: registry" in facade_body


def test_mcel_visible_surface_and_current_chromes_are_guarded() -> None:
    app = _read_repo_text("main_computer/web/applications/apps/mcel-lab.html")

    assert _normalize_text(_element_body_by_id(app, "button", "mcel-open-editor-modal")) == "Open Site Editor"
    assert _normalize_text(_element_body_by_id(app, "button", "mcel-open-site-modal")) == "Open Rendered Site"
    assert re.search(r"<iframe\b(?=[^>]*\bid=[\"']mcel-site-frame[\"'])", app, re.S)
    assert re.search(r"<div\b(?=[^>]*\bid=[\"']mcel-editor-modal[\"'])", app, re.S)
    assert re.search(r"<div\b(?=[^>]*\bid=[\"']mcel-site-modal[\"'])", app, re.S)
    assert _select_options(app, "mcel-chrome-select") == CHROME_OPTIONS


def test_mcel_chrome_law_contract_and_region_frame_protocol_are_guarded() -> None:
    chrome_law = _read_repo_text("main_computer/web/applications/scripts/mcel-chrome-law.js")

    assert 'CONTRACT_VERSION = "mcel.chrome.v1"' in chrome_law
    assert 'CHROME_GENERATED_ATTR = "data-mcel-chrome-generated"' in chrome_law
    assert 'CHROME_ID_ATTR = "data-mcel-chrome-id"' in chrome_law
    assert 'CHROME_FRAME_ATTR = "data-mcel-chrome-frame"' in chrome_law
    assert 'CHROME_REGION_ROLE_ATTR = "data-mcel-chrome-region-role"' in chrome_law
    assert 'FIT_REGION_ATTR = "data-mcel-fit-region"' in chrome_law
    assert 'FIT_POLICY_ATTR = "data-mcel-fit-policy"' in chrome_law
    assert 'FIT_REMEDIATION_ATTR = "data-mcel-fit-remediation"' in chrome_law

    for chrome_id, _label in CHROME_OPTIONS:
        assert f'"{chrome_id}"' in chrome_law

    assert 'generatedRegion("compact-summary", chrome, "header", "summary")' in chrome_law
    assert 'generatedRegion("compact-body", chrome, "body")' in chrome_law
    assert 'element.setAttribute(CHROME_FRAME_ATTR, frame)' in chrome_law
    assert 'element.setAttribute(CHROME_REGION_ROLE_ATTR, role)' in chrome_law


def test_mcel_default_state_keeps_machine_theme_and_strict_hierarchy() -> None:
    bindings = _read_repo_text("main_computer/web/applications/scripts/dom-bindings/mcel-lab.js")

    create_default_state = _extract_function_source(bindings, "createDefaultMcelLabState")
    assert 'theme: "theme-machine"' in create_default_state
    assert 'chrome: "chrome-strict-hierarchy"' in create_default_state
    assert "siteFrameTwiddle" in create_default_state

    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; static default-state guards above still ran")

    script = (
        "const window = {};\n"
        f"{create_default_state}\n"
        "process.stdout.write(JSON.stringify(createDefaultMcelLabState()));\n"
    )
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    state = json.loads(result.stdout)

    assert state["theme"] == "theme-machine"
    assert state["chrome"] == "chrome-strict-hierarchy"
    assert state["activeModal"] is None
    assert state["siteFrameTwiddle"]["lastReason"] == "boot"
    assert state["siteFrameTwiddle"]["lastFitStatus"] == "unavailable"


def test_mcel_lower_layers_do_not_reach_into_lab_ui_surface() -> None:
    lower_layers = ["contract-engine", "law-internal", "browser-proof", "chrome-law"]
    forbidden_lab_surface_markers = [
        "#mcel-",
        "mcelLabApp",
        "mcel-open-editor-modal",
        "mcel-open-site-modal",
        "mcel-site-frame",
        "mcel-editor-modal",
        "mcel-site-modal",
    ]

    violations: list[str] = []
    for layer in lower_layers:
        for relative_path in MCEL_LAYER_INVENTORY[layer]:
            source = _read_repo_text(relative_path)
            for marker in forbidden_lab_surface_markers:
                if marker in source:
                    violations.append(f"{relative_path} contains {marker!r}")

    assert violations == []
