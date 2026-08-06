from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
)
from main_computer.mcel_application_runtime_projection import (
    RUNTIME_MANIFEST_NAME,
    build_runtime_projection_set,
)
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPT = (
    ROOT
    / "main_computer"
    / "web"
    / "applications"
    / "scripts"
    / "mcel-host-bound-application-runtime.js"
)


def _calculator_projection():
    projection_set = build_runtime_projection_set(ROOT)
    return next(item for item in projection_set.projections if item.app_id == "calculator")


def test_calculator_host_bound_projection_is_virtual_and_presentation_free() -> None:
    projection = _calculator_projection()

    assert projection.mount_mode == "host-bound"
    assert projection.host_route == "/applications/calculator"
    assert projection.root_selector == "#calculator-app"
    assert projection.runtime_facade == "MainComputerCalculatorRuntime"
    assert projection.document_url is None
    assert projection.script_url is None
    assert projection.style_url is None
    assert RUNTIME_MANIFEST_NAME in projection.files
    assert "contracts/adapter.js" in projection.files
    assert not any(path.startswith("src/") for path in projection.files)

    assets = build_virtual_mcel_browser_assets(ROOT)
    prefix = "applications/mcel-packages/calculator/"
    calculator_assets = {path for path in assets.files if path.startswith(prefix)}
    assert calculator_assets == {
        prefix + RUNTIME_MANIFEST_NAME,
        prefix + "contracts/acceptance.js",
        prefix + "contracts/adapter.js",
        prefix + "contracts/domain.js",
        prefix + "contracts/intents.js",
        prefix + "contracts/layout.js",
        prefix + "contracts/observation.js",
        prefix + "contracts/surface.js",
    }


def test_calculator_browser_catalog_declares_one_host_bound_record() -> None:
    payload = build_repository_browser_catalog_payload(ROOT)
    records = [item for item in payload["packages"] if item["appId"] == "calculator"]

    assert len(records) == 1
    record = records[0]
    runtime = record["runtimeProjection"]
    assert runtime["mountMode"] == "host-bound"
    assert runtime["hostRoute"] == "/applications/calculator"
    assert runtime["rootSelector"] == "#calculator-app"
    assert runtime["runtimeFacade"] == "MainComputerCalculatorRuntime"
    assert runtime["documentUrl"] is None
    assert runtime["scriptUrl"] is None
    assert runtime["styleUrl"] is None


def test_calculator_host_bound_runtime_is_included_after_catalog() -> None:
    shell = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
    catalog = "<!-- @include applications/scripts/mcel-application-package-catalog.js -->"
    runtime = "<!-- @include applications/scripts/mcel-host-bound-application-runtime.js -->"

    assert catalog in shell
    assert runtime in shell
    assert shell.index(catalog) < shell.index(runtime)
    assert RUNTIME_SCRIPT.is_file()


def test_generic_package_host_redirects_host_bound_records_to_the_existing_route() -> None:
    source = (
        ROOT
        / "main_computer"
        / "web"
        / "applications"
        / "scripts"
        / "mcel-application-package-host.js"
    ).read_text(encoding="utf-8")

    branch = 'if (record.runtimeProjection.mountMode === "host-bound")'
    redirect = "location.replace(new URL(hostRoute, location.origin).href)"
    document_load = "appendProjectedRoot(await readProjectedRoot(record, manifest), true)"
    assert branch in source
    assert redirect in source
    assert source.index(branch) < source.index(document_load)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_projected_calculator_adapter_invokes_the_stable_runtime_facade(tmp_path: Path) -> None:
    projection = _calculator_projection()
    adapter_path = tmp_path / "calculator-adapter.mjs"
    adapter_path.write_bytes(projection.files["contracts/adapter.js"])

    script = f"""
globalThis.MainComputerCalculatorRuntime = new Proxy({{}}, {{
  get(_target, property) {{
    return (...args) => ({{method: String(property), args}});
  }}
}});
(async () => {{
  const module = await import({json.dumps(adapter_path.as_uri())});
  const adapter = module.CalculatorAdapter;
  const names = Object.keys(adapter.bindings).sort();
  const results = names.map((name) => adapter.invoke(name, name));
  console.log(JSON.stringify({{names, results}}));
}})().catch((error) => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert len(payload["names"]) == 11
    assert {result["method"] for result in payload["results"]} == set(payload["names"])
    assert all(result["args"] == [name] for result, name in zip(payload["results"], payload["names"]))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_host_bound_runtime_mounts_generated_adapter_onto_existing_root() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");

globalThis.location = {{origin: "http://example.test"}};
globalThis.MainComputerCalculatorRuntime = Object.freeze({{
  switchMode(value) {{ return `mode:${{value}}`; }}
}});

const root = {{
  dataset: {{}},
  matches(selector) {{ return selector === "#calculator-app"; }},
  dispatchEvent() {{ return true; }}
}};

const record = {{
  appId: "calculator",
  fingerprint: "sha256:package",
  runtimeProjection: {{
    manifestUrl: "applications/mcel-packages/calculator/mcel.runtime.json",
    fingerprint: "sha256:projection",
    mountMode: "host-bound",
    hostRoute: "/applications/calculator",
    rootSelector: "#calculator-app",
    runtimeFacade: "MainComputerCalculatorRuntime"
  }}
}};
const catalog = {{
  catalogFingerprint: "sha256:catalog",
  getPackage(appId) {{ return appId === "calculator" ? record : null; }},
  listPackages() {{ return [record]; }}
}};
const manifest = {{
  schema: "mcel.application-runtime-projection.v1",
  appId: "calculator",
  source: {{
    packageFingerprint: "sha256:package",
    catalogFingerprint: "sha256:catalog"
  }},
  projection: {{fingerprint: "sha256:projection"}},
  surface: {{rootSelector: "#calculator-app"}},
  runtime: {{
    mode: "host-bound",
    route: "/applications/calculator",
    rootSelector: "#calculator-app",
    facade: "MainComputerCalculatorRuntime"
  }},
  modules: {{
    domain: {{path: "contracts/domain.js", export: "CalculatorDomain"}},
    intents: {{path: "contracts/intents.js", export: "CalculatorIntents"}},
    adapter: {{path: "contracts/adapter.js", export: "CalculatorAdapter"}},
    surface: {{path: "contracts/surface.js", export: "CalculatorSurface"}},
    layout: {{path: "contracts/layout.js", export: "CalculatorLayout"}},
    observation: {{path: "contracts/observation.js", export: "CalculatorObservation"}},
    acceptance: {{path: "contracts/acceptance.js", export: "CalculatorAcceptance"}}
  }}
}};
const values = {{
  CalculatorDomain: {{appId: "calculator"}},
  CalculatorIntents: Object.freeze({{switchMode: {{id: "intent:calculator.switch-mode"}}}}),
  CalculatorAdapter: {{
    appId: "calculator",
    bindings: Object.freeze({{switchMode: {{runtimeMethod: "switchMode"}}}}),
    invoke(name, ...args) {{
      const binding = this.bindings[name];
      return globalThis.MainComputerCalculatorRuntime[binding.runtimeMethod](...args);
    }}
  }},
  CalculatorSurface: {{appId: "calculator"}},
  CalculatorLayout: {{appId: "calculator"}},
  CalculatorObservation: {{appId: "calculator"}},
  CalculatorAcceptance: {{appId: "calculator"}}
}};

vm.runInThisContext(fs.readFileSync({json.dumps(str(RUNTIME_SCRIPT))}, "utf8"), {{
  filename: "mcel-host-bound-application-runtime.js"
}});

(async () => {{
  const mount = await McelHostBoundApplicationRuntime.mountApplication({{
    appId: "calculator",
    packageCatalog: catalog,
    manifest,
    root,
    moduleLoader: async (_url, entry) => ({{[entry.export]: values[entry.export]}})
  }});
  const same = await McelHostBoundApplicationRuntime.mountApplication({{
    appId: "calculator",
    packageCatalog: catalog,
    manifest,
    root,
    moduleLoader: async (_url, entry) => ({{[entry.export]: values[entry.export]}})
  }});
  console.log(JSON.stringify({{
    schema: mount.schema,
    kind: mount.kind,
    sameMount: same === mount,
    status: root.dataset.mcelHostBoundStatus,
    app: root.dataset.mcelHostBoundApp,
    result: mount.invoke("switchMode", "graphing"),
    activeBefore: mount.active,
    unmounted: mount.unmount(),
    activeAfter: mount.active,
    finalStatus: root.dataset.mcelHostBoundStatus
  }}));
}})().catch((error) => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "schema": "mcel.host-bound-application-mount.v1",
        "kind": "host-bound",
        "sameMount": True,
        "status": "mounted",
        "app": "calculator",
        "result": "mode:graphing",
        "activeBefore": True,
        "unmounted": True,
        "activeAfter": False,
        "finalStatus": "unmounted",
    }
