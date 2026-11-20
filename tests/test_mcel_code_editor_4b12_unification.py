"""4B.1/4B.2 in-place Code Editor source authority and security contracts."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_phase2c_candidate_host import CandidateHostError

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "mcel_apps/code-editor"


def test_single_canonical_paired_source_for_live_route():
    manifest = json.loads((PKG / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["authoring"]["html"] == "application.html"
    assert manifest["authoring"]["script"] == "application.js"
    assert manifest["nativeRoute"] == "/applications/code-editor"
    assert not (PKG / "blueprint.json").exists()
    ir = compile_semantic_app("code-editor", ROOT)
    assert ir["application"]["appId"] == "code-editor"
    sources = ir["provenance"]["frontend"]["sourceFiles"]
    assert {Path(source["path"]).name for source in sources} == {"application.js", "application.html"}
    assert all("tools/fixtures" not in str(source["path"]) for source in sources)
    assert len(ir["intents"]) == 39
    assert len(ir["capabilities"]) == 13
    assert len(ir["effects"]) == 36
    assert len([x for x in ir["surfaces"][0]["nodes"] if x["nodeKind"] == "document-editor"]) == 1


def test_fixture_is_not_live_application_authority(tmp_path):
    path = tmp_path / "mcel_apps/code-editor"
    path.mkdir(parents=True)
    shutil.copyfile(PKG / "application.js", path / "application.js")
    shutil.copyfile(PKG / "application.html", path / "application.html")
    older = tmp_path / "tools/fixtures/mcel_semantic_other_apps/code-editor"
    older.mkdir(parents=True)
    (older / "application.js").write_text("this is invalid fixture source\n", encoding="utf-8")
    ir = compile_semantic_app("code-editor", tmp_path)
    assert ir["application"]["appId"] == "code-editor"
    # Conversely, invalidating the canonical source must fail even with a
    # syntactically valid fixture present: no implicit fallback.
    (path / "application.js").write_text("this is invalid canonical source\n", encoding="utf-8")
    with pytest.raises(CandidateHostError):
        compile_semantic_app("code-editor", tmp_path)


def test_authority_separates_reads_writes_and_consequential_actions():
    ir = compile_semantic_app("code-editor", ROOT)
    intents = {i["id"]: i for i in ir["intents"]}
    caps = {c["id"]: c for c in ir["capabilities"]}
    effects = {e["id"]: e for e in ir["effects"]}
    expected = {
        "intent:save-source": ("source-write", "capability:source-writer"),
        "intent:apply-reviewed-change": ("source-write", "capability:review-applier"),
        "intent:run-aider": ("consequential", "capability:aider-control"),
        "intent:archive-aider-context": ("consequential", "capability:aider-control"),
        "intent:reset-aider-context": ("consequential", "capability:aider-control"),
        "intent:load-aider-archive": ("consequential", "capability:aider-control"),
        "intent:open-source": ("external-read", "capability:source-reader"),
        "intent:preview-aider-plan": ("external-read", "capability:aider-preview"),
    }
    for intent_id, (risk, cap_id) in expected.items():
        intent = intents[intent_id]
        assert intent["risk"] == risk
        effect = effects[intent["effectRefs"][0]["ref"]]
        assert effect["risk"] == risk
        assert effect["authority"] == {"ref": cap_id}
        assert caps[cap_id]["risk"] == risk
    assert all(i.get("risk") == "local-state" and not i.get("effectRefs")
               for i in intents.values() if i["operationKind"] == "interaction")


def test_canonical_port_preserves_observation_and_write_guards():
    script = ROOT / "main_computer/web/applications/scripts/mcel-semantic-read-ports.js"
    probe = f'''const fs=require("fs");eval(fs.readFileSync({json.dumps(str(script))},"utf8"));
(async()=>{{
 const port=globalThis.McelSemanticReadPorts.create({{appId:"code-editor",fetcher:async()=>{{throw Error("should-not-fetch")}}}});
 const base={{appId:"code-editor",intentId:"intent:save-source",effectId:"effect:save-source.request",
  capabilityId:"capability:source-writer",operation:"save",runtimeMethod:"saveSource",
  risk:"source-write",arguments:{{path:"README.md",text:"unsafe"}}}};
 const result=[await port.authorize(base),await port.authorize({{...base,risk:"external-read"}}),
   await port.authorize({{...base,appId:"semantic-code-editor"}}),
   await port.authorize({{...base,arguments:{{path:"../private",text:"unsafe"}}}}),
   await port.authorize({{appId:"code-editor",intentId:"intent:list-files",effectId:"effect:list-files.request",
     capabilityId:"capability:source-reader",operation:"list",runtimeMethod:"listSourceFiles",
     risk:"external-read",arguments:{{}}}})];
 console.log(JSON.stringify(result));
}})().catch(e=>{{console.error(e);process.exit(1)}});'''
    done = subprocess.run(["node", "-e", probe], text=True, capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [False, False, False, False, True]


def test_browser_catalog_retains_code_editor_without_second_runtime_authority():
    catalog = build_application_package_catalog(ROOT)
    assert catalog.ok
    record = next(p for p in catalog.packages if p.app_id == "code-editor")
    assert record.valid
    assert record.blueprint is None
    assert record.runtime == {}
    projected = build_runtime_projection_set(ROOT)
    native = next(p for p in projected.projections if p.app_id == "code-editor")
    assert native.mount_mode == "native-semantic-route"
    assert native.host_route == "/applications/code-editor"
    assert native.manifest["modules"] == {}
    assert native.document_url is None and native.script_url is None
    assert set(native.files) == {"mcel.runtime.json"}
    assert {p.app_id for p in projected.projections} >= {"calculator", "slh", "code-editor"}


def test_live_page_contains_canonical_ir_and_retained_aider_job_safety():
    page = render_semantic_page("code-editor", ROOT, promoted=True)
    assert '"appId":"code-editor"' in page
    assert '"launchIntent":{"ref":"intent:run-aider"}' in page
    assert '"statusIntent":{"ref":"intent:aider-jobs"}' in page
    assert '"pathArgument":"path"' in page
    assert '"unverifiedStatus":"aider-outcome-unverified"' in page
    assert "McelSemanticReadPorts.create" in page
