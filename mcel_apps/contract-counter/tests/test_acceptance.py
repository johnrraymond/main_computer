from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BUILD_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "runtime/build/mcel/web/applications/mcel-packages/contract-counter"


def _repository_root() -> Path:
    for candidate in (PACKAGE_ROOT, *PACKAGE_ROOT.parents):
        runtime = candidate / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-runtime.js"
        scm = candidate / "main_computer" / "web" / "applications" / "scripts" / "mcel-scm.js"
        if runtime.is_file() and scm.is_file():
            return candidate
    pytest.skip("The shared MCEL application runtime is unavailable outside a Main Computer repository.")


def _node_executable() -> str:
    configured = os.environ.get("MCEL_NODE_EXECUTABLE", "").strip()
    resolved = configured or shutil.which("node") or ""
    if not resolved:
        pytest.skip("Node.js is unavailable; package acceptance cannot execute the MCEL runtime.")
    return resolved


def _run_node_json(tmp_path: Path, source: str) -> dict:
    script_path = tmp_path / "contract_counter-acceptance.js"
    script_path.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [_node_executable(), str(script_path)],
        cwd=_repository_root(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def test_package_acceptance_operation_control(tmp_path: Path) -> None:
    repository = _repository_root()
    scm = (repository / "main_computer" / "web" / "applications" / "scripts" / "mcel-scm.js").read_text(encoding="utf-8")
    runtime = (repository / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-runtime.js").read_text(encoding="utf-8")
    script = f'''\
"use strict";
const fs = require("fs");
{scm}
{runtime}

async function importContract(filePath) {{
  const source = fs.readFileSync(filePath, "utf8");
  const url = `data:text/javascript;base64,${{Buffer.from(source, "utf8").toString("base64")}}`;
  return import(url);
}}

(async () => {{
  const domainModule = await importContract({json.dumps(str(BUILD_PACKAGE_ROOT / "contracts" / "domain.js"))});
  const intentsModule = await importContract({json.dumps(str(BUILD_PACKAGE_ROOT / "contracts" / "intents.js"))});
  const adapterModule = await importContract({json.dumps(str(BUILD_PACKAGE_ROOT / "contracts" / "adapter.js"))});
  const definition = McelApplicationRuntime.defineApplication({{
    appId: "contract-counter",
    domain: domainModule.ContractCounterDomain,
    intents: intentsModule.ContractCounterIntents,
    adapter: adapterModule.ContractCounterAdapter
  }});
  const app = McelApplicationRuntime.createApplicationInstance(definition, {{id: "contract-counter-acceptance-instance"}});

  const increment = app.dispatch({{
    operationId: "contract-counter-acceptance-increment",
    expectedRevision: 0,
    intentId: "increment",
    payload: {{}}
  }});
  const duplicate = app.dispatch({{
    operationId: "contract-counter-acceptance-increment",
    expectedRevision: 1,
    intentId: "increment",
    payload: {{}}
  }});
  const stale = app.dispatch({{
    operationId: "contract-counter-acceptance-stale",
    expectedRevision: 0,
    intentId: "increment",
    payload: {{}}
  }});
  const prohibited = app.dispatch({{
    operationId: "contract-counter-acceptance-prohibited",
    expectedRevision: 1,
    intentId: "direct-set",
    payload: {{value: 99}}
  }});
  const reset = app.dispatch({{
    operationId: "contract-counter-acceptance-reset",
    expectedRevision: 1,
    intentId: "reset",
    payload: {{}}
  }});

  const failingDefinition = McelApplicationRuntime.defineApplication({{
    appId: "contract-counter-failed-postcondition",
    domain: Object.freeze({{
      appId: "contract-counter-failed-postcondition",
      initialState: Object.freeze({{count: 0, revision: 0}}),
      invariantReads: Object.freeze(["state.count", "state.revision"]),
      invariants: Object.freeze([])
    }}),
    intents: Object.freeze({{
      increment: Object.freeze({{
        id: "increment",
        kind: "mutation",
        reads: Object.freeze(["state.count", "state.revision"]),
        writes: Object.freeze(["state.count", "state.revision"])
      }})
    }}),
    adapter: Object.freeze({{
      appId: "contract-counter-failed-postcondition",
      preflight() {{ return Object.freeze({{ok: true}}); }},
      transition({{state}}) {{ return Object.freeze({{count: state.count + 1, revision: state.revision + 1}}); }},
      validateEffects() {{ return false; }}
    }})
  }});
  const failingApp = McelApplicationRuntime.createApplicationInstance(failingDefinition);
  const failedPostcondition = failingApp.dispatch({{
    operationId: "contract-counter-acceptance-failed-postcondition",
    expectedRevision: 0,
    intentId: "increment",
    payload: {{}}
  }});

  process.stdout.write(JSON.stringify({{
    increment,
    duplicate,
    stale,
    prohibited,
    reset,
    finalState: app.readState(),
    finalRevision: app.revision,
    appliedOperationIds: app.appliedOperationIds,
    failedPostcondition,
    failedPostconditionState: failingApp.readState(),
    failedPostconditionRevision: failingApp.revision
  }}));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
'''
    result = _run_node_json(tmp_path, script)

    assert result["increment"]["ok"] is True
    assert result["increment"]["status"] == "committed"
    assert result["increment"]["state"] == {"count": 1, "revision": 1}

    assert result["duplicate"]["ok"] is False
    assert result["duplicate"]["code"] == "SCM_DUPLICATE_OPERATION"
    assert result["duplicate"]["state"] == {"count": 1, "revision": 1}

    assert result["stale"]["ok"] is False
    assert result["stale"]["code"] == "SCM_STALE_REVISION"
    assert result["stale"]["state"] == {"count": 1, "revision": 1}

    assert result["prohibited"]["ok"] is False
    assert result["prohibited"]["code"] == "INTENT_PROHIBITED"
    assert result["prohibited"]["state"] == {"count": 1, "revision": 1}

    assert result["reset"]["ok"] is True
    assert result["reset"]["state"] == {"count": 0, "revision": 2}
    assert result["finalState"] == {"count": 0, "revision": 2}
    assert result["finalRevision"] == 2
    assert result["appliedOperationIds"] == [
        "contract-counter-acceptance-increment",
        "contract-counter-acceptance-reset",
    ]

    assert result["failedPostcondition"]["ok"] is False
    assert result["failedPostcondition"]["code"] == "SCM_TRANSITION_POSTCONDITION_FAILED"
    assert result["failedPostconditionState"] == {"count": 0, "revision": 0}
    assert result["failedPostconditionRevision"] == 0
