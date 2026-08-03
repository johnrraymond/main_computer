from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "main_computer/web/applications/scripts/mcel-app-definition.js"
APPLICATION = ROOT / "mcel_apps/contract-workbench/application.js"


def _node() -> str:
    resolved = os.environ.get("MCEL_NODE_EXECUTABLE", "").strip() or shutil.which("node") or ""
    if not resolved:
        pytest.skip("Node.js is unavailable.")
    return resolved


def _run(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )


def test_definition_library_validates_and_inspects_the_forward_app() -> None:
    completed = _run(
        f"const m=require({json.dumps(str(LIBRARY))});"
        f"const app=require({json.dumps(str(APPLICATION))});"
        "process.stdout.write(JSON.stringify({frozen:Object.isFrozen(app),inspection:m.inspect(app)}));"
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["frozen"] is True
    assert payload["inspection"]["appId"] == "contract-workbench"
    assert payload["inspection"]["nodeKindCounts"]["collection"] == 1
    assert payload["inspection"]["operationKindCounts"]["async"] == 1
    assert "keyed-collection-reconciliation" in payload["inspection"]["requiredRuntimeFeatures"]
    assert "multi-instance-proof" in payload["inspection"]["requiredRuntimeFeatures"]
    assert "dynamic-property-projection" in payload["inspection"]["requiredRuntimeFeatures"]


def test_definition_library_rejects_unknown_surface_intents() -> None:
    completed = _run(
        f"const m=require({json.dumps(str(LIBRARY))});"
        "try {"
        "m.defineApplication({id:'bad-app',title:'Bad',state:{count:m.state.canonical(0)},operations:{},surface:m.surface({id:'bad-app.surface',root:'#bad',regions:[{id:'bad-app.region',role:'application'}],nodes:[m.node.control({id:'bad-app.control',regionId:'bad-app.region',intentId:'missing'})]})});"
        "} catch (error) { process.stdout.write(JSON.stringify({code:error.code})); process.exit(0); }"
        "process.exit(2);"
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"code": "MCEL_APP_NODE_INTENT_UNKNOWN"}
