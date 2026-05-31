from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"

CHROME_IDS = [
    "chrome-strict-hierarchy",
    "chrome-editorial-flow",
    "chrome-cluster-grid",
    "chrome-spotlight",
    "chrome-journey",
    "chrome-compact-disclosure",
]


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _extract_function_source(script: str, function_name: str) -> str:
    match = re.search(rf"function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{", script)
    assert match, f"missing function {function_name}"
    body_start = match.end() - 1
    depth = 0
    for index in range(body_start, len(script)):
        char = script[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[match.start() : index + 1]
    raise AssertionError(f"could not find end of function {function_name}")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; core facade functional smoke test cannot run")

    script_path = tmp_path / "mcel-core-facade-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _core_dependency_stubs() -> str:
    return """
const window = {};
var McelLabContract = {
  contractVersion: "mcel.facade.test",
  defaultSource: "<main data-mc='component'></main>",
  attributes: {sourceIndex: "data-mc-source-index"}
};
var McelLabEngine = {};
var McelLabEditor = {};
var McelLabStyleLaw = {};
var McelLabLayoutLaw = {};
var McelLabBrowserObserver = {};
var McelLabPlatformSpine = {};
var McelLabWorkbench = {};
var McelLabBrowserRunner = {};
var McelLabCommandSurface = {};
var McelLabGraph = {};
var McelLabOpsRunner = {};
var McelLabAcidTests = {};
var McelLabSupervisor = {};
var McelLabLawRegistry = {};
"""


def test_mcel_core_chrome_catalog_facade_uses_current_chrome_law(tmp_path: Path) -> None:
    script = f"""
{_core_dependency_stubs()}
{_script("mcel-chrome-law.js")}
{_script("mcel-core.js")}

const catalog = MCEL.listChromes();
const strict = MCEL.describeChrome("chrome-strict-hierarchy");
process.stdout.write(JSON.stringify({{
  version: MCEL.version,
  chromeIds: catalog.map((item) => item.id),
  chromeLabels: catalog.map((item) => item.label),
  clusterAlias: MCEL.normalizeChrome("cluster-grid.v1"),
  unknownFallback: MCEL.normalizeChrome("not-a-real-chrome"),
  strictPreservesPixelBaseline: Boolean(strict && strict.preservesPixelBaseline),
  strictContractVersion: strict && strict.contractVersion
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["version"] == "mcel.facade.test"
    assert data["chromeIds"] == CHROME_IDS
    assert data["chromeLabels"] == [
        "Strict Hierarchy",
        "Editorial Flow",
        "Cluster Grid",
        "Spotlight",
        "Journey",
        "Compact Disclosure",
    ]
    assert data["clusterAlias"] == "chrome-cluster-grid"
    assert data["unknownFallback"] == "chrome-strict-hierarchy"
    assert data["strictPreservesPixelBaseline"] is True
    assert data["strictContractVersion"] == "mcel.chrome.v1"


def test_mcel_core_apply_chrome_is_additive_delegation_without_call_migration(tmp_path: Path) -> None:
    script = f"""
{_core_dependency_stubs()}
var calls = [];
var McelLabChromeLaw = {{
  chromeCatalog: Object.freeze([
    Object.freeze({{id: "chrome-strict-hierarchy", label: "Strict Hierarchy"}}),
    Object.freeze({{id: "chrome-journey", label: "Journey"}})
  ]),
  normalizeChrome(chrome) {{
    calls.push(["normalize", chrome]);
    return chrome === "journey-alias" ? "chrome-journey" : "chrome-strict-hierarchy";
  }},
  chromeDefinition(chrome) {{
    calls.push(["definition", chrome]);
    return {{id: this.normalizeChrome(chrome), label: "Defined " + chrome}};
  }},
  applyChromeHtml(html, options) {{
    calls.push(["apply", html, options.chrome, options.reason]);
    return {{
      html: "<chrome data-id='" + options.chrome + "'>" + html + "</chrome>",
      report: {{chrome: options.chrome, reason: options.reason || null}}
    }};
  }}
}};
{_script("mcel-core.js")}

const result = {{
  listed: MCEL.listChromes(),
  described: MCEL.describeChrome("journey-alias"),
  applied: MCEL.applyChrome("<main></main>", {{chrome: "journey-alias", reason: "facade-test"}}),
  calls
}};
process.stdout.write(JSON.stringify(result));
"""

    data = _run_node_json(tmp_path, script)

    assert data["listed"] == [
        {"id": "chrome-strict-hierarchy", "label": "Strict Hierarchy"},
        {"id": "chrome-journey", "label": "Journey"},
    ]
    assert data["described"]["id"] == "chrome-journey"
    assert data["applied"]["html"] == "<chrome data-id='chrome-journey'><main></main></chrome>"
    assert data["applied"]["report"] == {"chrome": "chrome-journey", "reason": "facade-test"}
    assert ["normalize", "journey-alias"] in data["calls"]
    assert ["apply", "<main></main>", "chrome-journey", "facade-test"] in data["calls"]


def test_mcel_lab_rendered_site_chrome_call_routes_through_core_facade(tmp_path: Path) -> None:
    lab_ui = _script("mcel-lab.js")
    isolated_site_css = _extract_function_source(lab_ui, "isolatedSiteCss")
    isolated_site_document = _extract_function_source(lab_ui, "isolatedSiteDocument")

    assert "MCEL.applyChrome(runtimeHtml" in isolated_site_document
    assert "McelLabChromeLaw.applyChromeHtml" not in isolated_site_document

    script = f"""
var calls = [];
var mcelLabState = {{
  theme: "theme-machine",
  chrome: "journey-alias",
  lastChromeReport: null
}};
var McelLabStyleLaw = {{
  normalizeTheme(theme) {{
    calls.push(["theme", theme]);
    return theme || "theme-machine";
  }}
}};
var MCEL = {{
  normalizeChrome(chrome) {{
    calls.push(["normalizeChrome", chrome]);
    return chrome === "journey-alias" ? "chrome-journey" : "chrome-strict-hierarchy";
  }},
  applyChrome(html, options) {{
    calls.push(["applyChrome", html, options.chrome, options.theme, options.reason]);
    return {{
      html: "<section data-core-chrome='" + options.chrome + "'>" + html + "</section>",
      report: {{chrome: options.chrome, delegated: true, reason: options.reason}}
    }};
  }}
}};
{isolated_site_css}
{isolated_site_document}

const rendered = isolatedSiteDocument("<main data-mc='component'>Hello</main>", {{
  reason: "caller-migration-test",
  nonce: "42",
  hash: "hash-42"
}});
process.stdout.write(JSON.stringify({{
  calls,
  stateChrome: mcelLabState.chrome,
  lastChromeReport: mcelLabState.lastChromeReport,
  rendered
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["calls"] == [
        ["theme", "theme-machine"],
        ["normalizeChrome", "journey-alias"],
        [
            "applyChrome",
            "<main data-mc='component'>Hello</main>",
            "chrome-journey",
            "theme-machine",
            "caller-migration-test",
        ],
    ]
    assert data["stateChrome"] == "chrome-journey"
    assert data["lastChromeReport"] == {
        "chrome": "chrome-journey",
        "delegated": True,
        "reason": "caller-migration-test",
    }
    assert 'data-mcel-chrome="chrome-journey"' in data["rendered"]
    assert "data-core-chrome='chrome-journey'" in data["rendered"]
