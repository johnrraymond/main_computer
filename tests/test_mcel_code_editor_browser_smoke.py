from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
SMOKE_SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-browser-smoke.js"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor browser smoke fixture cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_code_editor_browser_smoke_script_is_part_of_browser_bundle() -> None:
    shell = APPLICATIONS_HTML.read_text(encoding="utf-8")
    smoke = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/code-editor-browser-smoke.js -->" in shell
    assert shell.index("applications/scripts/code-editor.js") < shell.index("applications/scripts/mcel-self-diagnosis.js")
    assert shell.index("applications/scripts/mcel-diagnostics-counter-widget.js") < shell.index("applications/scripts/code-editor-browser-smoke.js")
    assert "code-editor.browser-smoke.v1" in smoke
    assert "shell-single-column-grid" in smoke
    assert "monaco-primary-surface-usable" in smoke
    assert "proof-dock-hidden-by-default" in smoke
    assert "surface-bundle-available" in smoke
    assert "semantic-surface-declared" in smoke
    assert "layout-grammar-declared" in smoke
    assert "diagnostics-required-layers-pass" in smoke
    assert "diagnostics-raw-verdict-pass" in smoke


def test_code_editor_browser_smoke_passes_healthy_legacy_fidelity_fixture_and_fails_zero_shell_tracks() -> None:
    result = run_node_json(textwrap.dedent(f"""
        const smoke = require({json.dumps(str(SMOKE_SCRIPT))});

        class FakeElement {{
          constructor(name, options = {{}}, documentRef = null) {{
            this.name = name;
            this.tagName = options.tagName || "DIV";
            this.id = options.id || "";
            this.className = options.className || "";
            this.dataset = options.dataset || {{}};
            this.attrs = Object.assign({{}}, options.attrs || {{}});
            this.rect = Object.assign({{x: 0, y: 0, width: 0, height: 0}}, options.rect || {{}});
            this.style = Object.assign({{
              display: "block",
              position: "static",
              width: `${{this.rect.width}}px`,
              height: `${{this.rect.height}}px`,
              minWidth: "0px",
              maxWidth: "none",
              minHeight: "0px",
              maxHeight: "none",
              overflow: "hidden",
              gridTemplateColumns: "none",
              gridTemplateRows: "none",
              gridColumn: "auto",
              gridRow: "auto"
            }}, options.style || {{}});
            this.documentRef = documentRef;
          }}
          getAttribute(name) {{
            if (name === "id") return this.id;
            if (name === "class") return this.className;
            if (name === "style") return this.attrs.style || "";
            if (name === "data-code-studio-pane") return this.dataset.codeStudioPane || "";
            if (name.startsWith("data-")) {{
              const camel = name.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
              return this.dataset[camel] || "";
            }}
            return this.attrs[name] == null ? null : this.attrs[name];
          }}
          querySelector(selector) {{
            return this.documentRef ? this.documentRef.querySelector(selector) : null;
          }}
          querySelectorAll(selector) {{
            return this.documentRef ? this.documentRef.querySelectorAll(selector) : [];
          }}
          getBoundingClientRect() {{
            const r = this.rect;
            return {{
              x: r.x || 0,
              y: r.y || 0,
              left: r.x || 0,
              top: r.y || 0,
              width: r.width || 0,
              height: r.height || 0,
              right: (r.x || 0) + (r.width || 0),
              bottom: (r.y || 0) + (r.height || 0)
            }};
          }}
        }}

        function buildWindow(broken = false, includeSurfaceBundle = true) {{
          const doc = {{
            map: new Map(),
            all: [],
            querySelector(selector) {{
              if (selector === "#code-studio-runtime-monaco .monaco-editor") return this.map.get(".monaco-editor") || null;
              return this.map.get(selector) || null;
            }},
            querySelectorAll(selector) {{
              if (selector === "[data-code-studio-pane].active") {{
                return [this.map.get('[data-code-studio-pane="runtime"].active')].filter(Boolean);
              }}
              return this.map.has(selector) ? [this.map.get(selector)] : [];
            }}
          }};

          const add = (selector, options) => {{
            const element = new FakeElement(selector, options, doc);
            doc.map.set(selector, element);
            doc.all.push(element);
            return element;
          }};

          add("#code-editor-app", {{
            id: "code-editor-app",
            className: "code-editor-app mc-app",
            dataset: {{
              codeEditorMode: "legacy-fidelity",
              codeEditorRuntimeSurfaceMode: "legacy-fidelity"
            }},
            rect: {{width: broken ? 1281 : 1054, height: 640}},
            style: {{display: "grid", width: broken ? "1281px" : "1054px"}}
          }});
          add(".code-studio-shell", {{
            className: "code-studio-shell mc-shell",
            rect: {{width: broken ? 1278 : 1052, height: 632}},
            style: {{
              display: "grid",
              width: broken ? "1278px" : "1052px",
              gridTemplateColumns: broken ? "0px 0px 1250px" : "1024px",
              gridTemplateRows: "36px 574px 22px"
            }}
          }});
          add(".code-studio-titlebar", {{
            className: "code-studio-titlebar",
            rect: {{width: broken ? 16 : 1024, height: 36}},
            style: {{display: "grid", width: broken ? "16px" : "1024px", gridColumn: "1 / -1"}}
          }});
          add(".code-studio-body", {{
            className: "code-studio-body",
            rect: {{width: broken ? 0 : 1024, height: 574}},
            style: {{
              display: "grid",
              width: broken ? "0px" : "1024px",
              gridTemplateColumns: broken ? "50px 245px 0px 326px" : "50px 240px 734px",
              gridColumn: "1 / -1"
            }}
          }});
          add(".code-studio-activitybar", {{
            className: "code-studio-activitybar",
            rect: {{width: 50, height: 574}},
            style: {{display: "grid", width: "50px"}}
          }});
          add(".code-studio-sidebar", {{
            className: "code-studio-sidebar",
            rect: {{width: 240, height: 574}},
            style: {{display: "grid", width: "240px"}}
          }});
          add(".code-studio-editor-group", {{
            className: "code-studio-editor-group",
            rect: {{width: broken ? 0 : 734, height: 574}},
            style: {{display: "grid", width: broken ? "0px" : "734px", gridTemplateColumns: broken ? "0px" : "734px"}}
          }});
          add('[data-code-studio-pane="runtime"].active', {{
            className: "code-studio-editor-pane active",
            dataset: {{codeStudioPane: "runtime"}},
            rect: {{width: broken ? 0 : 734, height: 538}},
            style: {{display: "grid", width: broken ? "0px" : "734px", gridTemplateColumns: broken ? "0px" : "734px"}}
          }});
          doc.map.set(".code-studio-editor-pane.active", doc.map.get('[data-code-studio-pane="runtime"].active'));
          add("#code-studio-runtime-preview", {{
            id: "code-studio-runtime-preview",
            className: "code-studio-runtime-preview",
            rect: {{width: broken ? 28 : 734, height: 538}},
            style: {{display: "grid", width: broken ? "28px" : "734px", gridTemplateColumns: broken ? "0px" : "689px"}}
          }});
          add(".code-studio-monaco-authoring-surface", {{
            className: "code-studio-monaco-authoring-surface",
            rect: {{width: broken ? 0 : 689, height: 500}},
            style: {{display: "grid", width: broken ? "0px" : "689px"}}
          }});
          add("#code-studio-runtime-monaco", {{
            id: "code-studio-runtime-monaco",
            className: "mcel-code-editor-authoring-host code-studio-monaco-host",
            rect: {{width: broken ? 0 : 689, height: 420}},
            style: {{display: "block", width: broken ? "0px" : "689px", height: "420px"}}
          }});
          add(".monaco-editor", {{
            className: "monaco-editor no-user-select showUnused showDeprecated vs-dark",
            rect: {{width: broken ? 0 : 689, height: 420}},
            style: {{display: "block", width: broken ? "0px" : "689px", height: "420px"}}
          }});
          add(".code-studio-inspector", {{
            className: "code-studio-inspector",
            rect: {{width: broken ? 326 : 0, height: 574}},
            style: {{display: broken ? "block" : "none", width: broken ? "326px" : "0px"}}
          }});
          add("#code-studio-bottom-panel", {{
            id: "code-studio-bottom-panel",
            className: "code-studio-bottom-panel code-studio-proof-dock",
            attrs: {{"aria-hidden": "true"}},
            rect: {{width: 0, height: 0}},
            style: {{display: "none", width: "0px", height: "0px"}}
          }});

          const surfaceBundle = {{
            schema: "mcel.application-surface-bundle.v1",
            appId: "code-editor",
            surfaceId: "code-editor.surface.monaco-selected-file-editor",
            presentationAuthority: "existing-host-html",
            semanticSurface: {{
              surfaceId: "code-editor.surface.monaco-selected-file-editor",
              regions: [
                {{id: "root", selector: "#code-editor-app"}},
                {{id: "shell", selector: ".code-studio-shell"}},
                {{id: "activitybar", selector: ".code-studio-activitybar"}},
                {{id: "explorer", selector: ".code-studio-sidebar"}},
                {{id: "editor-group", selector: ".code-studio-editor-group"}},
                {{id: "primary-editor", selector: "#code-studio-runtime-preview", runtimeHostSelector: "#code-studio-runtime-monaco", primary: true}},
                {{id: "assistant", selector: ".code-studio-inspector"}},
                {{id: "proof-dock", selector: "#code-studio-bottom-panel", defaultVisible: false}}
              ],
              controls: [
                {{id: "open-source-file", intent: "openFile", selector: "[data-code-studio-file]"}}
              ],
              forbiddenDefaultRegions: [
                {{id: "source-pane", selector: "[data-code-studio-pane='source']", defaultVisible: false}},
                {{id: "serialized-pane", selector: "[data-code-studio-pane='serialized']", defaultVisible: false}},
                {{id: "contract-pane", selector: "[data-code-studio-pane='contract']", defaultVisible: false}},
                {{id: "proof-dock", selector: "#code-studio-bottom-panel", defaultVisible: false}}
              ]
            }},
            layoutGrammar: {{
              rootSelector: ".code-studio-shell",
              regions: [
                {{id: "shell", selector: ".code-studio-shell"}},
                {{id: "workbench", selector: ".code-studio-body"}},
                {{id: "activitybar", selector: ".code-studio-activitybar"}},
                {{id: "explorer", selector: ".code-studio-sidebar"}},
                {{id: "editor-group", selector: ".code-studio-editor-group"}},
                {{id: "primary-editor", selector: "#code-studio-runtime-preview", runtimeHostSelector: "#code-studio-runtime-monaco"}},
                {{id: "assistant", selector: ".code-studio-inspector"}}
              ],
              constraints: [
                {{id: "shell-single-column", selector: ".code-studio-shell"}},
                {{id: "workbench-nonzero-tracks", selector: ".code-studio-body"}},
                {{id: "primary-editor-nonzero", selector: "#code-studio-runtime-preview", runtimeHostSelector: "#code-studio-runtime-monaco"}},
                {{id: "proof-dock-hidden-by-default", selector: "#code-studio-bottom-panel"}}
              ]
            }}
          }};

          const appSurfaceConformance = {{
            status: "pass",
            valid: true,
            requiredLayerIds: [
              "semantic-surface",
              "layout-grammar",
              "runtime-ownership",
              "runtime-visual-fit",
              "diagnostic-no-throw"
            ],
            policyFailedLayerIds: [],
            policyUnavailableLayerIds: [],
            layers: [
              {{id: "semantic-surface", status: "pass"}},
              {{id: "layout-grammar", status: "pass"}},
              {{id: "runtime-ownership", status: "pass"}},
              {{id: "runtime-visual-fit", status: "pass"}},
              {{id: "diagnostic-no-throw", status: "pass"}}
            ]
          }};

          return {{
            document: doc,
            getComputedStyle: (el) => el.style,
            McelApplicationPackages: {{
              surfaceBundleCount: includeSurfaceBundle ? 1 : 0,
              getSurfaceBundle: (appId) => includeSurfaceBundle && appId === "code-editor" ? surfaceBundle : null,
              hasSurfaceBundle: (appId) => includeSurfaceBundle && appId === "code-editor"
            }},
            McelSelfDiagnosis: {{
              diagnose: () => ({{
                verdict: "pass",
                findings: [],
                appSurfaceConformance,
                summary: {{
                  errors: 0,
                  warnings: 0,
                  ok: 1,
                  appSurfaceConformance
                }}
              }})
            }},
            MainComputerCodeEditorRuntime: {{
              state: () => ({{activeFile: {{path: "src/app.js"}}}}),
              monacoDebug: () => ({{runtimePaneActive: true, mounted: true}})
            }}
          }};
        }}

        const healthy = smoke.run({{global: buildWindow(false), requireDiagnosis: true}});
        const broken = smoke.run({{global: buildWindow(true), requireDiagnosis: true}});
        const missingBundle = smoke.run({{global: buildWindow(false, false), requireDiagnosis: true}});
        console.log(JSON.stringify({{
          healthyVerdict: healthy.verdict,
          healthyStaticSurface: healthy.staticSurface,
          healthyCodes: healthy.checks.filter((check) => check.ok).map((check) => check.code),
          brokenVerdict: broken.verdict,
          brokenCodes: broken.checks.filter((check) => !check.ok).map((check) => check.code),
          missingBundleVerdict: missingBundle.verdict,
          missingBundleCodes: missingBundle.checks.filter((check) => !check.ok).map((check) => check.code)
        }}));
        """))

    assert result["healthyVerdict"] == "pass"
    assert result["healthyStaticSurface"]["schema"] == "mcel.application-surface-bundle.v1"
    assert result["healthyStaticSurface"]["surfaceId"] == "code-editor.surface.monaco-selected-file-editor"
    assert "surface-bundle-available" in result["healthyCodes"]
    assert "semantic-surface-declared" in result["healthyCodes"]
    assert "layout-grammar-declared" in result["healthyCodes"]
    assert "diagnostics-required-layers-pass" in result["healthyCodes"]
    assert result["brokenVerdict"] == "fail"
    assert "shell-single-column-grid" in result["brokenCodes"]
    assert "workbench-grid-nonzero-tracks" in result["brokenCodes"]
    assert "monaco-primary-surface-usable" in result["brokenCodes"]
    assert result["missingBundleVerdict"] == "fail"
    assert "surface-bundle-available" in result["missingBundleCodes"]
    assert "semantic-surface-declared" in result["missingBundleCodes"]
    assert "layout-grammar-declared" in result["missingBundleCodes"]
