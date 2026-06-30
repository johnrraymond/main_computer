from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
PRETTY_DOC = ROOT / "pretty_docs" / "mcel-debug-observability.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL debug observability smoke test cannot run")

    script_path = tmp_path / "mcel-debug-observability-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_mcel_engine_has_first_class_debug_mechanisms() -> None:
    engine = _script("mcel-engine.js")

    assert "const debugMechanisms = Object.freeze" in engine
    assert "function captureDebugEnvelope(" in engine
    assert "function listDebugMechanisms()" in engine
    assert "mcel.debug.css.not-winning.v1" in engine
    assert "mcel.debug.theme-leak.v1" in engine
    assert "mcel.debug.grid-contract.v1" in engine
    assert "mcel.debug.stacked-children.v1" in engine
    assert "mcel.debug.page.too-tall" in engine
    assert "mcel-debug-envelope" in engine


def test_mcel_core_automatically_records_debug_envelopes(tmp_path: Path) -> None:
    script = f"""
const window = {{}};

function makeRoot() {{
  return {{
    nodeType: 1,
    tagName: "DIV",
    id: "runtime-root",
    className: "runtime-root",
    children: [],
    ownerDocument: null,
    dataset: {{}},
    _html: "",
    set innerHTML(value) {{ this._html = String(value || ""); }},
    get innerHTML() {{ return this._html; }},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
    getBoundingClientRect() {{ return {{x: 0, y: 0, width: 640, height: 420, right: 640, bottom: 420}}; }},
    getAttribute() {{ return ""; }},
    setAttribute() {{}}
  }};
}}

const document = {{
  createElement() {{ return makeRoot(); }},
  body: makeRoot(),
  documentElement: {{scrollHeight: 420, dataset: {{}}}},
  scrollingElement: {{scrollHeight: 420}},
  querySelector() {{ return null; }},
  readyState: "complete",
  title: "debug-test",
  location: {{href: "http://example.test/debug"}}
}};
window.document = document;
window.innerWidth = 1280;
window.innerHeight = 720;
window.scrollY = 0;
window.devicePixelRatio = 1;
window.getComputedStyle = () => ({{
  getPropertyValue(property) {{
    if (property === "display") return "grid";
    if (property === "background-color") return "rgb(30, 30, 30)";
    return "";
  }}
}});

var McelLabContract = {{
  contractVersion: "mcel.debug.test",
  defaultSource: "<main data-mc='component'></main>",
  attributes: {{
    type: "data-mc",
    generated: "data-mc-generated",
    sourceIndex: "data-mc-source-index",
    componentName: "data-mc-component"
  }}
}};
var McelLabEngine = {{
  compileSource(source, options) {{
    return {{runtimeHtml: "<main data-mc='component'></main>", sourceCount: 1, events: [options.reason]}};
  }},
  serializeRuntimeRoot(root, options) {{
    return {{serialized: "<main data-mc='component'></main>", report: {{serializerClean: true}}, events: [options.reason]}};
  }},
  repairRuntimeRoot(root, options) {{
    return {{repaired: true, events: [options.reason]}};
  }},
  captureDebugEnvelope(subject, options) {{
    return {{
      kind: "mcel-debug-envelope",
      contractVersion: "mcel.debug.test",
      generatedAt: "2026-01-01T00:00:00.000Z",
      name: options.name,
      reason: options.reason,
      ok: true,
      issues: [],
      subjectWasPresent: Boolean(subject)
    }};
  }},
  listDebugMechanisms() {{
    return [{{id: "mcel.debug.operation.timeline.v1"}}];
  }},
  debuggerStateFor() {{
    return {{geometryProof: "ok", serializerClean: true}};
  }}
}};
var McelLabEditor = {{
  canonicalSource(source) {{ return String(source || "").trim(); }}
}};
var McelLabStyleLaw = {{}};
var McelLabLayoutLaw = {{}};
var McelLabChromeLaw = {{}};
var McelLabBrowserObserver = {{}};
var McelLabPlatformSpine = {{}};
var McelLabWorkbench = {{}};
var McelLabBrowserRunner = {{}};
var McelLabCommandSurface = {{}};
var McelLabGraph = {{}};
var McelLabOpsRunner = {{}};
var McelLabAcidTests = {{}};
var McelLabSupervisor = {{}};
var McelLabLawRegistry = {{}};

{_script("mcel-core.js")}

MCEL.clearDebugTimeline();
const compiled = MCEL.compile("<main data-mc='component'></main>", {{reason: "debug-test:compile"}});
const serialized = MCEL.serialize(compiled.runtimeRoot, {{reason: "debug-test:serialize"}});
const packet = MCEL.exportDebugPacket({{reason: "debug-test:packet"}});

process.stdout.write(JSON.stringify({{
  compileDebugOperation: compiled.debug.operation,
  compileDebugReason: compiled.debug.reason,
  serializeDebugOperation: serialized.debug.operation,
  timelineLength: MCEL.getDebugTimeline().length,
  packetKind: packet.kind,
  mechanisms: MCEL.listDebugMechanisms().map((item) => item.id),
  windowTimelineLength: window.__MCEL_DEBUG_TIMELINE__.length,
  lastEnvelopeOperation: window.__MCEL_LAST_DEBUG_ENVELOPE__.operation
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["compileDebugOperation"] == "compile"
    assert data["compileDebugReason"] == "debug-test:compile"
    assert data["serializeDebugOperation"] == "serialize"
    assert data["timelineLength"] == 2
    assert data["windowTimelineLength"] == 2
    assert data["packetKind"] == "mcel-debug-packet"
    assert data["mechanisms"] == ["mcel.debug.operation.timeline.v1"]
    assert data["lastEnvelopeOperation"] == "serialize"


def test_mcel_debug_observability_doc_is_registered() -> None:
    assert PRETTY_DOC.exists()
    text = PRETTY_DOC.read_text(encoding="utf-8")
    assert "MCEL Debug Observability" in text
    assert "MCEL.captureDebug" in text
    assert "MCEL.exportDebugPacket" in text
    assert "mcel.debug.css.not-winning.v1" in text
    assert "debug packet is evidence, not trust" in text

    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next((item for item in documents if item.get("path") == "mcel-debug-observability.md"), None)
    assert entry is not None
    assert entry.get("title") == "MCEL Debug Observability"
