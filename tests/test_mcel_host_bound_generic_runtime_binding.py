"""Execute real IR-generated ESM adapters against the host-bound MCEL runtime."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
)
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets

ROOT = Path(__file__).resolve().parents[1]
HOST_RUNTIME = ROOT / "main_computer/web/applications/scripts/mcel-host-bound-application-runtime.js"
CODE_EDITOR = ROOT / "main_computer/web/applications/scripts/code-editor.js"


def _run_probe(mode: str) -> dict:
    assets = build_virtual_mcel_browser_assets(ROOT)
    payload = build_repository_browser_catalog_payload(ROOT)
    record = next(item for item in payload["packages"] if item["appId"] == "code-editor")
    manifest = json.loads(assets.read("applications/mcel-packages/code-editor/mcel.runtime.json"))
    sources = {
        entry["path"]: assets.read(f"applications/mcel-packages/code-editor/{entry['path']}").decode("utf-8")
        for entry in manifest["modules"].values()
    }
    script = r'''
const vm = require("node:vm");
const fs = require("node:fs");
const mode = __MODE__;
const record = __RECORD__;
const manifest = __MANIFEST__;
const sources = __SOURCES__;
const catalog = {
  catalogFingerprint: __CATALOG_FP__,
  getPackage: (id) => id === record.appId ? record : null,
  listPackages: () => [record]
};
globalThis.location = {origin: "http://example.test"};
require(__CODE_EDITOR__);
const originalFacade = globalThis.MainComputerCodeEditorRuntime;
if (mode === "missing-method") {
  globalThis.MainComputerCodeEditorRuntime = Object.freeze({...originalFacade, saveFile: undefined});
}
const layoutHost = {
  hidden: true,
  dataset: {},
  getBoundingClientRect: () => ({width: 0, height: 0})
};
globalThis.ResizeObserver = class {
  observe() {} disconnect() {}
};
const root = {
  dataset: {},
  matches: (selector) => selector === "#code-editor-app",
  querySelectorAll: (selector) => selector === ".code-studio-shell" ? [layoutHost] : [],
  contains: (node) => node === layoutHost,
  dispatchEvent: () => true
};
const layoutEngine = {
  mountCanonicalLayoutTree() { throw Error("Hidden layout must remain deferred"); },
  unmountCanonicalLayoutTree() {}
};
vm.runInThisContext(fs.readFileSync(__HOST_RUNTIME__, "utf8"), {filename: "mcel-host-bound-application-runtime.js"});
(async () => {
  try {
    const mount = await McelHostBoundApplicationRuntime.mountApplication({
      appId: "code-editor", root, manifest, packageCatalog: catalog, layoutEngine,
      moduleLoader: async (_url, entry, label) => {
        const source = sources[entry.path];
        if (typeof source !== "string") throw Error(`Missing source ${entry.path}`);
        const namespace = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
        if (label !== "adapter" || mode === "valid") return namespace;
        const adapter = namespace[entry.export];
        if (mode === "mismatched-contract") {
          const changed = {...adapter.bindings, saveFile: {...adapter.bindings.saveFile, runtimeMethod: "openFile"}};
          return {[entry.export]: {...adapter, bindings: changed}};
        }
        if (mode === "missing-binding") {
          const changed = {...adapter.bindings};
          delete changed.saveFile;
          return {[entry.export]: {...adapter, bindings: changed}};
        }
        return namespace;
      }
    });
    const result = await mount.invoke("closeFile", {});
    process.stdout.write(JSON.stringify({mounted: true, status: root.dataset.mcelHostBoundStatus,
      bindingReport: mount.bindingReport, closeOk: result?.ok === true,
      closeReceipt: result?.state?.receipts?.at(-1)?.intent || null, active: mount.active}));
  } catch (error) {
    process.stdout.write(JSON.stringify({mounted: false, error: String(error.message),
      activeMount: !!McelHostBoundApplicationRuntime.getMount("code-editor"),
      rootStatus: root.dataset.mcelHostBoundStatus}));
  }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
'''
    for marker, value in {
        "__MODE__": mode,
        "__RECORD__": record,
        "__MANIFEST__": manifest,
        "__SOURCES__": sources,
        "__CATALOG_FP__": payload["catalogFingerprint"],
        "__CODE_EDITOR__": str(CODE_EDITOR),
        "__HOST_RUNTIME__": str(HOST_RUNTIME),
    }.items():
        script = script.replace(marker, json.dumps(value))
    # Generated adapter modules can make the probe larger than Windows' command-line
    # limit (WinError 206). Execute exactly the same script from a temporary .cjs
    # file rather than passing it as the argument to ``node -e``.
    with tempfile.TemporaryDirectory(prefix="mcel-host-bound-probe-") as directory:
        script_path = Path(directory) / "probe.cjs"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        proc = subprocess.run(["node", str(script_path)], cwd=ROOT, capture_output=True,
                              text=True, timeout=45, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_node_probe_uses_temporary_file_instead_of_long_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Path] = {}
    original_run = subprocess.run

    def capture_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] != "node":
            return original_run(command, **kwargs)
        # Keep the generated JavaScript off the process command line on Windows.
        assert len(command) == 2 and command[0] == "node"
        probe = Path(command[1])
        assert probe.is_file() and probe.suffix == ".cjs"
        source = probe.read_text(encoding="utf-8")
        assert "McelHostBoundApplicationRuntime.mountApplication" in source
        assert "\r\n" not in source
        assert kwargs["cwd"] == ROOT
        observed["file"] = probe
        return subprocess.CompletedProcess(command, 0, '{"probe":"file"}', "")

    monkeypatch.setattr(subprocess, "run", capture_probe)
    assert _run_probe("valid") == {"probe": "file"}
    assert not observed["file"].exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_real_generated_code_editor_adapter_binds_all_host_intents_and_invokes_local_action() -> None:
    result = _run_probe("valid")
    assert result["mounted"] is True, result
    assert result["status"] == "mounted"
    assert result["bindingReport"]["status"] == "bound"
    assert result["bindingReport"]["intentCount"] == 8
    assert {entry["intentName"] for entry in result["bindingReport"]["entries"]} == {
        "inspectWorkspace", "openFile", "editDraft", "saveFile", "discardDraft",
        "closeFile", "previewAiderPlan", "applyReviewedPatch",
    }
    assert result["closeOk"] is True
    assert result["closeReceipt"] == "closeFile"
    assert result["active"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
@pytest.mark.parametrize("mode, message", [
    ("missing-method", "runtime method is unavailable: saveFile"),
    ("mismatched-contract", "intent binding contract mismatch: saveFile"),
    ("missing-binding", "intent and adapter binding sets differ"),
])
def test_host_mount_fails_closed_when_generated_binding_is_incomplete(mode: str, message: str) -> None:
    result = _run_probe(mode)
    assert result["mounted"] is False
    assert result["activeMount"] is False
    assert result["rootStatus"] == "error"
    assert message in result["error"]


def test_browser_observer_executes_real_generated_adapter_instead_of_stub() -> None:
    text = (ROOT / "main_computer/mcel_host_bound_browser_observation.py").read_text(encoding="utf-8")
    assert "await import(`data:text/javascript;base64,${btoa(binary)}`)" in text
    assert "adapter.invoke = (intentName" not in text
    assert '"generatedAdapterModuleExecuted": browser_report.get("generatedAdapterModuleExecuted") is True' in text
