from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-semantic-surface-ridges.js"
DOC = ROOT / "pretty_docs" / "mcel-semantic-surface-ridges.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; semantic surface ridge smoke test cannot run")
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_semantic_surface_ridge_contract_is_domain_neutral() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "mcel.semantic-surface-ridges.v1" in source
    assert "data-mcel-node-id" in source
    assert "data-mcel-edge-id" in source
    assert "data-mcel-region" in source
    assert "data-mcel-control" in source
    assert "data-mcel-source" in source
    assert "data-mcel-provenance" in source
    assert "data-mcel-home-region" in source
    assert "data-mcel-actual-region" in source
    assert "data-mcel-teleported" in source

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "health_app",
        "elephant",
    ]
    for term in forbidden_domain_terms:
        assert term not in source


def test_semantic_surface_ridge_api_validates_neutral_fixture() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPT))}, "utf8"), sandbox, {{filename: "mcel-semantic-surface-ridges.js"}});
        const api = sandbox.McelSemanticSurfaceRidges;
        const records = api.buildNeutralDemoRidgeRecords();
        const report = api.validateSurfaceRidges(records);
        const classes = records.map((record) => api.classifyAttributes(record));
        const fingerprintA = api.semanticFingerprint(records);
        const fingerprintB = api.semanticFingerprint([...records].reverse());
        const brokenNode = Object.assign({{}}, records.find((record) => record["data-mcel-node-id"] === "Observation.A"));
        delete brokenNode["data-mcel-node-id"];
        const brokenReport = api.validateRidgeRecord(brokenNode);
        process.stdout.write(JSON.stringify({{
          version: api.contractVersion,
          valid: report.valid,
          recordCount: report.recordCount,
          diagnostics: report.diagnostics,
          classes,
          stableFingerprint: fingerprintA === fingerprintB,
          brokenValid: brokenReport.valid,
          brokenCodes: brokenReport.diagnostics.map((item) => item.code),
          nodeRequired: api.requiredForKind("node"),
          edgeRequired: api.requiredForKind("edge")
        }}));
        """
    )

    result = run_node_json(script)

    assert result["version"] == "mcel.semantic-surface-ridges.v1"
    assert result["valid"] is True
    assert result["recordCount"] == 6
    assert result["diagnostics"] == []
    assert result["classes"] == ["surface", "region", "node", "node", "edge", "control"]
    assert result["stableFingerprint"] is True
    assert result["brokenValid"] is False
    assert "missing-required-ridge" in result["brokenCodes"]
    assert "data-mcel-node-id" in result["nodeRequired"]
    assert "data-mcel-node-type" in result["nodeRequired"]
    assert "data-mcel-edge-id" in result["edgeRequired"]
    assert "data-mcel-from" in result["edgeRequired"]
    assert "data-mcel-to" in result["edgeRequired"]


def test_semantic_surface_ridge_api_catches_broken_edge_reference() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPT))}, "utf8"), sandbox, {{filename: "mcel-semantic-surface-ridges.js"}});
        const api = sandbox.McelSemanticSurfaceRidges;
        const records = api.buildNeutralDemoRidgeRecords().map((record) => Object.assign({{}}, record));
        const edge = records.find((record) => record["data-mcel-edge-id"] === "EDGE.observation-supports-hypothesis");
        edge["data-mcel-to"] = "Missing.Node";
        const report = api.validateSurfaceRidges(records);
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          codes: report.diagnostics.map((item) => item.code),
          detail: report.diagnostics.map((item) => item.detail)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "edge-to-node-missing" in result["codes"]


def test_semantic_surface_ridge_documentation_sets_the_boundary() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "semantic ridges" in doc
    assert "rendered output must carry enough stable metadata" in doc
    assert "does not define layout grammar" in doc
    assert "does not define a domain vocabulary" in doc
    assert "Health App" not in doc
