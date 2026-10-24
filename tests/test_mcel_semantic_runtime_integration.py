"""Negative controls for the additive semantic-runtime authority bridge."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import mcel_application_packages as packages
from main_computer import mcel_truth_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def _witness(app_id="mcel-lab"):
    entries = [{"intentId": "inspect", "status": "runtime-bound", "runtimeBound": True,
                "bindingSource": "actual.inspect"}]
    return {"appId": app_id, "adapterId": "adapter", "adapterVersion": "1",
            "operationalSemanticRuntimeReady": True, "runtimeBindingAuditReady": True,
            "runtimeBindingCoverage": {"verificationMode": "derived-runtime-binding-audit",
                                       "requiredIntentIds": ["inspect"], "entries": entries,
                                       "runtimeBindingReady": True, "verification": {"passed": True}}}


def test_browser_binding_admission_rejects_failed_and_wrong_app():
    witness = _witness()
    trial = {"app": "mcel-lab", "semanticBinding": witness,
             "semanticBindingSource": {"status": "exact"},
             "classification": {"status": "pass"}}
    assert "mcel-lab" in audit._admitted_browser_bindings({"trials": [trial]})
    bad = copy.deepcopy(trial)
    bad["semanticBindingSource"]["status"] = "blocked"
    assert audit._admitted_browser_bindings({"trials": [bad]}) == {}
    bad = copy.deepcopy(trial)
    bad.pop("semanticBindingSource")
    assert audit._admitted_browser_bindings({"trials": [bad]}) == {}
    bad = copy.deepcopy(trial)
    bad["classification"]["status"] = "fail"
    assert audit._admitted_browser_bindings({"trials": [bad]}) == {}
    bad = copy.deepcopy(trial)
    bad["semanticBinding"]["appId"] = "website-builder"
    assert audit._admitted_browser_bindings({"trials": [bad]}) == {}
    bad = copy.deepcopy(trial)
    bad["semanticBinding"]["runtimeBindingCoverage"]["runtimeBindingReady"] = False
    assert audit._admitted_browser_bindings({"trials": [bad]}) == {}


def test_package_proof_requires_matching_source_and_referenced_bytes(tmp_path, monkeypatch):
    package_root = tmp_path / "runtime/reports/mcel-app-proof/apps/calculator"
    package_root.mkdir(parents=True)
    evidence_root = tmp_path / "runtime/evidence"
    evidence_root.mkdir(parents=True)
    referenced = {}
    for label in ("acceptance", "browserObservation"):
        path = evidence_root / f"{label}.json"
        path.write_text("{}", encoding="utf-8")
        referenced[label] = {"path": path.relative_to(tmp_path).as_posix(),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    proof = {
        "schema": "mcel.application-proof-report.v1", "status": "pass", "ok": True,
        "evidenceScope": "app-scoped", "appId": "calculator",
        "truthStatus": "semantic-runtime-proven", "generatedAt": "2026-10-22T10:00:00Z",
        "package": {"fingerprint": "package-fingerprint"},
        "repositoryProvenance": {"fingerprint": "current-repository"},
        "truthSnapshot": {"appId": "calculator", "overallStatus": "semantic-runtime-proven",
                          "claims": {"semanticRuntimeProven": True},
                          "adapter": {"adapterId": "calculator.pkg", "operationalSemanticRuntimeReady": True,
                                      "fullApplicationSemanticReady": True}},
        "evidence": referenced,
    }
    path = package_root / "mcel-app-proof-report.json"
    def admit():
        path.write_text(json.dumps(proof), encoding="utf-8")
        return audit._admitted_package_proofs(
            tmp_path, repository_provenance={"fingerprint": "current-repository"},
            now="2026-10-22T10:02:00Z", max_age_hours=1,
        )
    monkeypatch.setattr(packages, "build_application_package_catalog",
                        lambda _: SimpleNamespace(packages=[SimpleNamespace(app_id="calculator", valid=True,
                                                                    fingerprint="package-fingerprint")]))
    assert "calculator" in admit()
    proof["appId"] = "code-editor"
    assert admit() == {}
    proof["appId"] = "calculator"
    proof["repositoryProvenance"]["fingerprint"] = "stale"
    assert admit() == {}
    proof["repositoryProvenance"]["fingerprint"] = "current-repository"
    (evidence_root / "acceptance.json").write_text('{"tampered":true}', encoding="utf-8")
    assert admit() == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node needed")
def test_headless_binding_coverage_refuses_unconfigured_runtime():
    scripts = ROOT / "main_computer/web/applications/scripts"
    payload = {"scripts": [str(scripts / name) for name in (
        "mcel-domain-adapter-registry.js", "mcel-semantic-adapter-toolkit.js",
        "file-explorer-semantic-adapter.js", "git-tools-semantic-adapter.js",
        "mcel-lab-semantic-adapter.js", "website-builder-semantic-adapter.js")],
    }
    js = r'''
        const fs = require("fs"), vm = require("vm");
        const opts = JSON.parse(fs.readFileSync(0, "utf8"));
        const sandbox = {console: {log(){}, warn(){}, error(){}}, URL, URLSearchParams};
        sandbox.window = sandbox; sandbox.globalThis = sandbox;
        sandbox.localStorage = {getItem(){return null},setItem(){},removeItem(){}};
        sandbox.fetch = async () => {throw Error("stub only")};
        vm.createContext(sandbox);
        opts.scripts.forEach(path => vm.runInContext(fs.readFileSync(path, "utf8"), sandbox, {filename:path}));
        const ids = ["file-explorer","git-tools","mcel-lab","website-builder"];
        console.log(JSON.stringify(Object.fromEntries(ids.map(id => [id,
          sandbox.McelDomainAdapterRegistry.evaluateAdapterReadiness(id)]))));
    '''
    completed = subprocess.run(["node", "-e", js], input=json.dumps(payload), text=True,
                               capture_output=True, cwd=ROOT, check=True)
    states = json.loads(completed.stdout)
    for app_id, state in states.items():
        assert state["runtimeBindingReady"] is False, (app_id, state)
        assert state["operationalSemanticRuntimeReady"] is False, app_id
        assert state["unboundIntentCount"] > 0, app_id


def test_release_gate_requires_semantic_runtime_failing_independently_of_portability():
    if not shutil.which("node"):
        pytest.skip("Node needed")
    result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
    cmd = [__import__('sys').executable, str(ROOT / "main_computer/mcel_truth_audit.py"),
           "--release-gate", "--no-write", "--json"]
    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    codes = [reason["code"] for reason in report["auditLevelReasons"]]
    assert "audit-enrolled-semantic-runtime-not-proven" in codes

@pytest.mark.skipif(shutil.which("node") is None, reason="Node needed")
def test_runtime_binding_audits_accept_callable_methods_and_reject_one_removed():
    scripts = ROOT / "main_computer/web/applications/scripts"
    payload = {"scripts": [str(scripts / name) for name in (
        "mcel-domain-adapter-registry.js", "mcel-semantic-adapter-toolkit.js",
        "file-explorer-semantic-adapter.js", "git-tools-semantic-adapter.js",
        "mcel-lab-semantic-adapter.js", "website-builder-semantic-adapter.js")],
    }
    js = r'''
        const fs=require("fs"), vm=require("vm");
        const o=JSON.parse(fs.readFileSync(0,"utf8"));
        const s={console:{log(){},warn(){},error(){}},URL,URLSearchParams};
        s.window=s;s.globalThis=s;
        s.document={querySelector(){return null}};
        s.fetch=async()=>({ok:true,json:async()=>({ok:true})});
        s.localStorage={getItem(){return null},setItem(){},removeItem(){}};
        s.GitToolsStatusApi={fetchStatus:async()=>({ok:true})};
        s.GitToolsServerPanel={pushLocalGitServerRemote:async()=>({ok:true})};
        vm.createContext(s);
        o.scripts.forEach(p=>vm.runInContext(fs.readFileSync(p,"utf8"),s,{filename:p}));
        const registry=s.McelDomainAdapterRegistry;
        const fill=(adapter, missing)=>{
          const bindings={};
          for(const definition of adapter.listIntents()) {
            if(definition.runtimeMethod && definition.runtimeMethod !== missing)
              bindings[definition.runtimeMethod]=()=>({ok:true});
          }
          adapter.setRuntimeBindings(bindings);
        };
        fill(s.McelLabSemanticAdapter);
        fill(s.WebsiteBuilderSemanticAdapter);
        const full=Object.fromEntries(["file-explorer","git-tools","mcel-lab","website-builder"].map(id=>[id,
          registry.evaluateAdapterReadiness(id).runtimeBindingReady]));
        const missing=s.McelLabSemanticAdapter.listIntents().find(x=>x.runtimeMethod)?.runtimeMethod;
        fill(s.McelLabSemanticAdapter, missing);
        const dropped=registry.evaluateAdapterReadiness("mcel-lab");
        console.log(JSON.stringify({full,dropped: {bound:dropped.runtimeBindingReady,unbound:dropped.unboundIntentIds}}));
    '''
    completed=subprocess.run(["node","-e",js],cwd=ROOT,input=json.dumps(payload),
                             text=True,capture_output=True,check=True)
    result=json.loads(completed.stdout)
    assert result["full"]["file-explorer"] is True
    assert result["full"]["git-tools"] is True
    assert result["full"]["mcel-lab"] is True
    assert result["full"]["website-builder"] is True
    assert result["dropped"]["bound"] is False
    assert result["dropped"]["unbound"]
