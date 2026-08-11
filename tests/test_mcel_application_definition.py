from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "main_computer/web/applications/scripts/mcel-app-definition.js"


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


def _sample_definition_source() -> str:
    return f"""
      const m=require({json.dumps(str(LIBRARY))});
      const app=m.defineApplication({{
        id:'sample-definition-app',
        title:'Sample Definition App',
        state:{{
          items:m.state.canonical([], {{
            schema:m.schema.array(m.schema.object({{
              id:m.schema.string({{minLength:1}}),
              name:m.schema.string()
            }}))
          }}),
          draft:m.state.local('', {{schema:m.schema.string()}}),
          pending:m.state.provisional({{}}, {{schema:m.schema.object({{}})}}),
          itemCount:m.state.derived(['items'], (state)=>state.items.length, {{
            schema:m.schema.integer({{minimum:0}})
          }})
        }},
        capabilities:{{
          quotes:m.capability('sample.quote-service', {{
            operations:{{
              requestQuote:{{
                request:m.schema.object({{id:m.schema.string({{minLength:1}})}}),
                response:m.schema.object({{amount:m.schema.integer({{minimum:0}})}}),
                stream:true,
                cancellable:true
              }}
            }}
          }})
        }},
        operations:{{
          addItem:m.operation.mutation({{
            writes:['items'],
            payload:{{name:m.source.nodeValue('sample.input.name')}}
          }}),
          requestQuote:m.operation.async({{
            reads:['items'],
            uses:['quotes'],
            provisionalPath:'pending',
            cancellable:true
          }})
        }},
        surface:m.surface({{
          id:'sample.surface',
          root:'#sample',
          regions:[
            {{id:'sample.region.form', role:'form'}},
            {{id:'sample.region.list', role:'list'}}
          ],
          nodes:[
            m.node.input({{
              id:'sample.input.name',
              regionId:'sample.region.form',
              localPath:'draft'
            }}),
            m.node.property({{
              id:'sample.property.count',
              regionId:'sample.region.form',
              statePath:'itemCount',
              property:'textContent'
            }}),
            m.node.control({{
              id:'sample.control.add',
              regionId:'sample.region.form',
              intentId:'addItem'
            }}),
            m.node.collection({{
              id:'sample.collection.items',
              regionId:'sample.region.list',
              statePath:'items',
              keyPath:'id',
              item:{{controls:{{quote:{{intentId:'requestQuote'}}}}}}
            }})
          ]
        }}),
        observations:[
          m.observe('sample.observation.items', {{
            kind:'collection',
            nodeId:'sample.collection.items'
          }})
        ],
        multiInstance:{{required:true}}
      }});
    """


def test_definition_library_validates_and_inspects_a_fixture_neutral_forward_app() -> None:
    completed = _run(
        _sample_definition_source()
        + "process.stdout.write(JSON.stringify({frozen:Object.isFrozen(app),inspection:m.inspect(app)}));"
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["frozen"] is True

    inspection = payload["inspection"]
    assert inspection["appId"] == "sample-definition-app"
    assert inspection["nodeKindCounts"]["collection"] == 1
    assert inspection["operationKindCounts"]["async"] == 1
    assert "keyed-collection-reconciliation" in inspection["requiredRuntimeFeatures"]
    assert "multi-instance-proof" in inspection["requiredRuntimeFeatures"]
    assert "dynamic-property-projection" in inspection["requiredRuntimeFeatures"]


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
