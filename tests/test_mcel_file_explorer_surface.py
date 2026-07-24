from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
FILE_EXPLORER_HTML = WEB / "apps" / "file-explorer.html"
FILE_EXPLORER_JS = SCRIPTS / "file-explorer.js"
SURFACE_JS = SCRIPTS / "mcel-file-explorer-surface.js"
DOC = ROOT / "pretty_docs" / "mcel-file-explorer-surface.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; File Explorer MCEL surface smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_surface_stack(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const name of [
          "mcel-semantic-surface-ridges.js",
          "mcel-semantic-surface-ir.js",
          "mcel-shared-layout-grammar.js",
          "mcel-surface-extractors.js",
          "mcel-file-explorer-surface.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const surface = sandbox.McelFileExplorerSurface;
        {body}
        """
    )


def test_file_explorer_surface_files_are_wired_without_new_visible_panel() -> None:
    assert SURFACE_JS.exists()
    assert DOC.exists()

    app_shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-file-explorer-surface.js" in app_shell
    assert "file-explorer.js" in app_shell
    assert app_shell.index("mcel-file-explorer-surface.js") < app_shell.index("file-explorer.js")

    html = FILE_EXPLORER_HTML.read_text(encoding="utf-8")
    assert 'data-mcel-surface-id="file-explorer.surface.primary"' in html
    assert "mcel-preview" not in html.lower()
    assert "mcel-inspector" not in html.lower()


def test_file_explorer_static_markup_extracts_as_valid_mcel_surface() -> None:
    html = FILE_EXPLORER_HTML.read_text(encoding="utf-8")
    script = load_surface_stack(
        f"""
        const html = {json.dumps(html)};
        const bundle = sandbox.McelSurfaceExtractors.extractSurfaceBundleFromHtml(html, {{
          surfaceId: "file-explorer.surface.primary"
        }});
        const diagnostics = bundle.diagnostics
          .concat(bundle.validation.surface.diagnostics)
          .concat(bundle.validation.layout.diagnostics)
          .map((item) => item.code);
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          surfaceId: bundle.surfaceIR.surface.id,
          nodeIds: bundle.surfaceIR.graph.nodes.map((node) => node.id).sort(),
          regionIds: bundle.surfaceIR.graph.regions.map((region) => region.id).sort(),
          edgeIds: bundle.surfaceIR.graph.edges.map((edge) => edge.id).sort(),
          controlIds: bundle.surfaceIR.graph.controls.map((control) => control.id).sort(),
          diagnostics
        }}));
        """
    )
    data = run_node_json(script)

    assert data["valid"] is True
    assert data["surfaceId"] == "file-explorer.surface.primary"
    assert data["diagnostics"] == []
    assert data["nodeIds"] == [
        "file-explorer.node.current-directory",
        "file-explorer.node.details-panel",
        "file-explorer.node.directory-list",
        "file-explorer.node.root-set",
    ]
    assert data["regionIds"] == [
        "file-explorer.region.details",
        "file-explorer.region.file-list",
        "file-explorer.region.roots",
        "file-explorer.region.status",
        "file-explorer.region.toolbar",
    ]
    assert data["edgeIds"] == [
        "file-explorer.edge.current-contains-list",
        "file-explorer.edge.list-describes-details",
        "file-explorer.edge.roots-select-current",
    ]
    assert data["controlIds"] == [
        "file-explorer.control.search",
        "file-explorer.control.up",
    ]


def test_file_explorer_surface_contract_builds_reusable_ir_and_layout() -> None:
    script = load_surface_stack(
        """
        const records = surface.buildStaticSurfaceRidgeRecords();
        const irResult = sandbox.McelSemanticSurfaceIR.buildSurfaceIRFromRidges(records, {requireSurface: true});
        const regionRecords = records
          .filter((record) => record["data-mcel-region"])
          .map((record) => ({
            id: record["data-mcel-region"],
            role: record["data-mcel-region-role"],
            x: Number(record["data-layout-x"]),
            y: Number(record["data-layout-y"]),
            width: Number(record["data-layout-region-width"]),
            height: Number(record["data-layout-region-height"])
          }));
        const nodePorts = Object.fromEntries(records
          .filter((record) => record["data-mcel-node-id"])
          .map((record) => [record["data-mcel-node-id"], ["north", "south", "east", "west"]]));
        const layoutResult = sandbox.McelSharedLayoutGrammar.buildSharedLayoutGrammar(irResult.ir, {
          viewport: {width: 1280, height: 720, safeMargin: 16},
          regions: regionRecords,
          nodePorts
        });
        process.stdout.write(JSON.stringify({
          recordCount: records.length,
          irValid: irResult.valid,
          layoutValid: layoutResult.valid,
          nodes: irResult.ir.graph.nodes.length,
          edges: irResult.ir.graph.edges.length,
          controls: irResult.ir.graph.controls.length,
          layoutDiagnostics: layoutResult.diagnostics.map((item) => item.code)
        }));
        """
    )
    data = run_node_json(script)

    assert data["recordCount"] == 16
    assert data["irValid"] is True
    assert data["layoutValid"] is True
    assert data["layoutDiagnostics"] == []
    assert data["nodes"] == 4
    assert data["edges"] == 3
    assert data["controls"] == 3


def test_file_explorer_runtime_helpers_decorate_dynamic_entries() -> None:
    script = load_surface_stack(
        """
        const attrs = {};
        const element = {
          setAttribute(name, value) { attrs[name] = String(value); }
        };
        surface.decorateEntryElement(element, {
          kind: "directory",
          name: "contracts",
          relative_path: "contracts",
          path_display: "contracts"
        }, {index: 2, rootId: "workspace"});
        const rootAttrs = {};
        const rootButton = {
          setAttribute(name, value) { rootAttrs[name] = String(value); }
        };
        surface.decorateRootButton(rootButton, {
          id: "workspace",
          label: "Workspace",
          path_display: "."
        }, 0);
        process.stdout.write(JSON.stringify({
          entryId: attrs["data-mcel-node-id"],
          entryType: attrs["data-mcel-node-type"],
          entryRegion: attrs["data-mcel-home-region"],
          entrySource: attrs["data-mcel-source"],
          rootId: rootAttrs["data-mcel-node-id"],
          rootType: rootAttrs["data-mcel-node-type"]
        }));
        """
    )
    data = run_node_json(script)

    assert data == {
        "entryId": "file-explorer.node.entry.folder.contracts",
        "entryType": "folder_item",
        "entryRegion": "file-explorer.region.file-list",
        "entrySource": "file-explorer.list-api",
        "rootId": "file-explorer.node.root.workspace",
        "rootType": "filesystem_root",
    }


def test_file_explorer_runtime_script_consumes_surface_helpers_defensively() -> None:
    script = FILE_EXPLORER_JS.read_text(encoding="utf-8")

    for text in [
        "function systemFileExplorerMcelSurface()",
        "systemFileExplorerDecorateTreeHost(host)",
        "systemFileExplorerDecorateEntryElement(button, entry, index)",
        "systemFileExplorerDecorateRootButton(button, root, index)",
        "systemFileExplorerDecoratePreviewPanel(entry)",
        "surface.applyStaticSurfaceRidges(document)",
    ]:
        assert text in script

    assert "window.McelFileExplorerSurface.decorate" not in script


def test_file_explorer_surface_pilot_is_domain_neutral_and_documented() -> None:
    script = SURFACE_JS.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.file-explorer-surface.v1" in script
    assert "file-explorer.surface.primary" in script
    assert "decorateEntryElement" in script
    assert "extractCurrentSurface" in script
    assert "first non-editor MCEL surface pilot" in doc

    forbidden_terms = ["Health", "BIO_HEALTH", "SYS_HEALTH"]
    for text in [script, doc]:
        for term in forbidden_terms:
            assert term not in text
