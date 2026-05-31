from __future__ import annotations

import json
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
