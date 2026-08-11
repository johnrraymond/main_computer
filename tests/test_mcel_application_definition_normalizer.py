from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.mcel_application_definition_normalizer import (
    NORMALIZER_VERSION,
    NORMALIZED_PATH,
    NormalizationPlan,
    check_normalization,
    render_application_definition_files,
)


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "sample-normalized-app"


def _fn(source: str) -> dict[str, str]:
    import hashlib

    return {
        "$function": source,
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


def _schema(name: str, **describe) -> dict[str, object]:
    return {"name": name, "describe": describe}


def _sample_definition() -> dict[str, object]:
    return {
        "id": APP_ID,
        "title": "Sample Normalized App",
        "state": {
            "items": {
                "authority": "canonical",
                "initial": [],
                "schema": _schema("array", item="record"),
            },
            "nextItemId": {
                "authority": "canonical",
                "initial": 1,
                "schema": _schema("integer", minimum=1),
            },
            "revision": {
                "authority": "canonical",
                "initial": 0,
                "schema": _schema("integer", minimum=0),
            },
            "draftName": {
                "authority": "renderer-local",
                "initial": "",
                "schema": _schema("string"),
            },
            "quoteProgress": {
                "authority": "provisional",
                "initial": {},
                "schema": _schema("object"),
            },
            "visibleItems": {
                "authority": "derived",
                "reads": ["items"],
                "initial": [],
                "schema": _schema("array"),
                "compute": _fn("({state}) => state.items"),
            },
        },
        "capabilities": {
            "quotes": {
                "id": "sample.quote-service",
                "risk": "external-read-stream",
                "description": "Request independently streamed quote reports.",
                "operations": {
                    "requestQuote": {
                        "stream": True,
                        "cancellable": True,
                        "request": _schema("object"),
                        "response": _schema("object"),
                    }
                },
            }
        },
        "operations": {
            "add-item": {
                "operationKind": "mutation",
                "risk": "local-state",
                "reads": ["items", "nextItemId", "revision"],
                "writes": ["items", "nextItemId", "revision"],
                "payload": {
                    "name": {
                        "sourceKind": "node-property",
                        "nodeId": "sample.input.name",
                        "property": "value",
                    }
                },
                "transition": _fn(
                    "({state, payload}) => ({...state, "
                    "items:[...state.items, {id:`item-${state.nextItemId}`, name:payload.name}], "
                    "nextItemId:state.nextItemId+1, revision:state.revision+1})"
                ),
                "ensures": _fn("({before, after}) => after.items.length === before.items.length + 1"),
            },
            "request-quote": {
                "operationKind": "async",
                "risk": "external-read-stream",
                "reads": ["items"],
                "writes": [],
                "uses": ["quotes"],
                "provisionalPath": "quoteProgress",
                "cancellable": True,
            },
        },
        "surface": {
            "id": "sample.surface.primary",
            "root": "#sample-normalized-app",
            "regions": [
                {"id": "sample.region.form", "role": "form"},
                {"id": "sample.region.collection", "role": "list"},
                {"id": "sample.region.evidence", "role": "status"},
            ],
            "nodes": [
                {
                    "id": "sample.input.name",
                    "nodeKind": "input",
                    "regionId": "sample.region.form",
                    "localPath": "draftName",
                },
                {
                    "id": "sample.control.add",
                    "nodeKind": "control",
                    "regionId": "sample.region.form",
                    "intentId": "add-item",
                    "payload": {
                        "name": {
                            "sourceKind": "node-property",
                            "nodeId": "sample.input.name",
                            "property": "value",
                        }
                    },
                },
                {
                    "id": "sample.collection.items",
                    "nodeKind": "collection",
                    "regionId": "sample.region.collection",
                    "statePath": "visibleItems",
                    "keyPath": "id",
                    "item": {"controls": {"quote": {"intentId": "request-quote"}}},
                },
                {
                    "id": "sample.receipt.latest",
                    "nodeKind": "operation-evidence",
                    "regionId": "sample.region.evidence",
                    "source": {"sourceKind": "latest-receipt", "path": "receipts.add-item"},
                },
            ],
        },
        "layout": {
            "id": "sample.layout.primary",
            "regions": ["sample.region.form", "sample.region.collection", "sample.region.evidence"],
        },
        "invariants": [
            {
                "id": "sample.invariant.named-items",
                "reads": ["items"],
                "check": _fn("({state}) => state.items.every((item) => Boolean(item.name))"),
                "description": "All items have display names.",
            }
        ],
        "acceptance": [
            {
                "id": "sample.acceptance.add-item",
                "acceptanceKind": "workflow",
                "operationId": "add-item",
                "given": {"items": []},
                "when": {"payload": {"name": "Steel"}},
                "expect": {"itemCount": 1},
            }
        ],
        "observations": [
            {
                "id": "sample.observation.items",
                "observationKind": "collection",
                "source": "browser-dom",
                "nodeId": "sample.collection.items",
                "statePath": "visibleItems",
            }
        ],
        "proof": {
            "runtimeStatus": "semantic-runtime-proven",
            "acceptanceStatus": "semantic-runtime-proven",
            "browserObservation": "semantic-runtime-proven",
        },
    }


def _sample_projection():
    fingerprint, files = render_application_definition_files(
        APP_ID,
        _sample_definition(),
        source_reference="tests/fixtures/sample-normalized-app/application.js",
        source_sha256="0" * 64,
    )
    return type("SampleProjection", (), {
        "definition_fingerprint": fingerprint,
        "files": files,
    })()


def _write_projection(tmp_path: Path) -> Path:
    package = tmp_path / APP_ID
    projection = _sample_projection()
    for relative, content in projection.files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return package


def _run_module(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )


def test_definition_projection_is_deterministic_and_complete() -> None:
    projection = _sample_projection()
    assert len(projection.files) == 8
    assert projection.definition_fingerprint.startswith("sha256:")
    assert NORMALIZED_PATH in projection.files
    for name in ("domain", "intents", "adapter", "surface", "layout", "acceptance", "observation"):
        source = projection.files[f"contracts/{name}.js"].decode("utf-8")
        assert NORMALIZER_VERSION in source
        assert projection.definition_fingerprint in source
        assert "Do not edit this file directly." in source


def test_normalized_definition_carries_function_hashes_not_function_source() -> None:
    projection = _sample_projection()
    normalized = json.loads(projection.files[NORMALIZED_PATH])
    assert normalized["schema"] == "mcel.application-definition.normalized.v1"
    assert normalized["definitionFingerprint"] == projection.definition_fingerprint
    assert normalized["definition"]["operations"]["add-item"]["transition"]["kind"] == "function"
    assert normalized["definition"]["operations"]["add-item"]["transition"]["sha256"].startswith("sha256:")
    assert "$function" not in projection.files[NORMALIZED_PATH].decode("utf-8")


def test_check_detects_generated_contract_drift_in_disposable_package(tmp_path: Path) -> None:
    projection = _sample_projection()
    package = tmp_path / APP_ID
    for relative, content in projection.files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    drifted = package / "contracts/surface.js"
    drifted.write_text(drifted.read_text(encoding="utf-8") + "// drift\n", encoding="utf-8")
    plan = NormalizationPlan(
        APP_ID,
        package,
        ROOT / "tests/fixtures/sample-normalized-app/application.js",
        "tests/fixtures/sample-normalized-app/application.js",
        NORMALIZED_PATH,
        projection.definition_fingerprint,
        "",
        projection.files,
    )
    fresh, stale = check_normalization(plan)
    assert fresh is False
    assert stale == ("contracts/surface.js",)


def test_generated_adapter_preserves_synchronous_operation_semantics(tmp_path: Path) -> None:
    package = _write_projection(tmp_path)
    adapter = (package / "contracts/adapter.js").as_uri()
    source = f"""
      import {{SampleNormalizedAppAdapter as adapter}} from {json.dumps(adapter)};
      const state={{items:[],nextItemId:1,revision:0}};
      const input={{expectedRevision:0,payload:{{name:'Steel'}}}};
      const preflight=adapter.preflight({{intentId:'add-item',input,state}});
      const after=adapter.transition({{intentId:'add-item',input,state}});
      const valid=adapter.validateEffects({{intentId:'add-item',before:state,after,input}});
      process.stdout.write(JSON.stringify({{preflight,after,valid}}));
    """
    completed = _run_module(source)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["preflight"] == {"ok": True}
    assert payload["after"]["items"][0]["id"] == "item-1"
    assert payload["after"]["revision"] == 1
    assert payload["valid"] is True


def test_generated_domain_invariants_are_self_contained_and_executable(tmp_path: Path) -> None:
    package = _write_projection(tmp_path)
    domain = (package / "contracts/domain.js").as_uri()
    source = f"""
      import {{SampleNormalizedAppDomain as domain}} from {json.dumps(domain)};
      const valid={{items:[{{id:'item-1',name:'Steel'}}],nextItemId:2,revision:1}};
      const invalid={{items:[{{id:'item-1',name:''}}],nextItemId:2,revision:1}};
      process.stdout.write(JSON.stringify({{
        valid:domain.invariants.every((entry)=>entry.check({{state:valid}})),
        invalid:domain.invariants.every((entry)=>entry.check({{state:invalid}}))
      }}));
    """
    completed = _run_module(source)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"valid": True, "invalid": False}


def test_generated_domain_materializes_runtime_state_and_capability_definitions(tmp_path: Path) -> None:
    package = _write_projection(tmp_path)
    domain = (package / "contracts/domain.js").as_uri()
    source = f"""
      import {{SampleNormalizedAppDomain as domain}} from {json.dumps(domain)};
      const local=domain.rendererLocalStateDefinitions.map((entry)=>({{id:entry.id,initial:entry.initial,schema:entry.schema.name}}));
      const derived=domain.derivedState.map((entry)=>({{id:entry.id,reads:entry.reads,schema:entry.schema.name,computeType:typeof entry.compute}}));
      const provisional=domain.provisionalStateDefinitions.map((entry)=>({{id:entry.id,initial:entry.initial,schema:entry.schema.name}}));
      const capabilities=Object.fromEntries(Object.entries(domain.capabilities).map(([alias,entry])=>[alias,{{id:entry.id,operations:Object.keys(entry.operations)}}]));
      process.stdout.write(JSON.stringify({{local,derived,provisional,capabilities}}));
    """
    completed = _run_module(source)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["local"] == [{"id": "draftName", "initial": "", "schema": "string"}]
    assert payload["derived"] == [
        {"id": "visibleItems", "reads": ["items"], "schema": "array", "computeType": "function"}
    ]
    assert payload["provisional"] == [{"id": "quoteProgress", "initial": {}, "schema": "object"}]
    assert payload["capabilities"] == {
        "quotes": {"id": "sample.quote-service", "operations": ["requestQuote"]}
    }
