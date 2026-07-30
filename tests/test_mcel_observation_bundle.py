from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
EPISTEMIC = SCRIPTS / "mcel-epistemic-status.js"
OBSERVATION = SCRIPTS / "mcel-observation-bundle.js"


def run_node_json(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; observation bundle tests cannot run")
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const path of [{json.dumps(str(EPISTEMIC))}, {json.dumps(str(OBSERVATION))}]) {{
          vm.runInNewContext(fs.readFileSync(path, "utf8"), sandbox, {{filename: path}});
        }}
        const observations = sandbox.McelObservationBundle;
        {body}
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def bundle_input_js(*, reverse: bool = False) -> str:
    facts = [
        {
            "id": "dom.form",
            "kind": "element",
            "locator": "#parcel-form",
            "value": {"tagName": "FORM"},
            "fingerprint": "sha256:dom-form",
        },
        {
            "id": "dom.submit",
            "kind": "element",
            "locator": "#parcel-form button",
            "value": {"tagName": "BUTTON", "type": "submit"},
            "fingerprint": "sha256:dom-submit",
        },
    ]
    if reverse:
        facts.reverse()
    return json.dumps(
        {
            "observationId": "observation.parcel-desk.1",
            "appId": "parcel-desk",
            "route": "/fixtures/parcel-desk",
            "mode": "read-only",
            "capturedAt": "2026-07-30T18:00:00Z",
            "repositoryFingerprint": "repo:abc123",
            "viewport": {"width": 1280, "height": 720, "deviceScaleFactor": 1},
            "stateMarkers": ["clean-start", "route:parcel-desk"],
            "provenance": {
                "producer": "mcel-browser-observer",
                "collectorVersion": "observation-contract-test-v1",
                "codeFingerprint": "sha256:collector",
                "browser": {"name": "Chromium", "version": "test"},
            },
            "lenses": {
                "dom": {"status": "captured", "facts": facts},
                "accessibility": {
                    "status": "captured",
                    "facts": [
                        {
                            "id": "a11y.submit",
                            "kind": "role",
                            "locator": "AXNode:17",
                            "value": {"role": "button", "name": "Create parcel"},
                            "fingerprint": "sha256:ax-submit",
                        }
                    ],
                },
                "layout": {"status": "missing", "reason": "not-requested", "facts": []},
                "visual": {"status": "unavailable", "reason": "headless-no-screenshot", "facts": []},
                "source": {"status": "missing", "reason": "source-map-unavailable", "facts": []},
                "transition": {"status": "missing", "reason": "active-exploration-forbidden", "facts": []},
                "ridges": {"status": "captured", "facts": []},
            },
            "claims": [
                {
                    "claimId": "claim.submit.role",
                    "subject": "control.submit",
                    "predicate": "semantic-role",
                    "value": "submit",
                    "status": "observed",
                    "sources": [
                        {
                            "id": "a11y.submit",
                            "kind": "browser-accessibility",
                            "locator": "AXNode:17",
                            "fingerprint": "sha256:ax-submit",
                        }
                    ],
                    "confidence": None,
                    "contradictions": [],
                    "validatorResults": [],
                }
            ],
        }
    )


def test_observation_bundle_is_complete_read_only_and_repository_bound() -> None:
    result = run_node_json(
        f"""
        const bundle = observations.createObservationBundle({bundle_input_js()});
        const report = observations.validateObservationBundle(bundle);
        process.stdout.write(JSON.stringify({{
          contractVersion: bundle.contractVersion,
          mode: bundle.mode,
          repositoryFingerprint: bundle.repositoryFingerprint,
          lensIds: Object.keys(bundle.lenses),
          lensStatuses: Object.fromEntries(Object.entries(bundle.lenses).map(([id, lens]) => [id, lens.status])),
          claimStatus: bundle.claims[0].status,
          resolvedStatus: bundle.resolvedClaims[0].status,
          fingerprint: bundle.bundleFingerprint,
          valid: report.valid,
          frozen: Object.isFrozen(bundle) && Object.isFrozen(bundle.lenses.dom.facts)
        }}));
        """
    )

    assert result["contractVersion"] == "mcel.observation-bundle.v1"
    assert result["mode"] == "read-only"
    assert result["repositoryFingerprint"] == "repo:abc123"
    assert result["lensIds"] == [
        "dom",
        "accessibility",
        "layout",
        "visual",
        "source",
        "transition",
        "ridges",
    ]
    assert result["lensStatuses"]["layout"] == "missing"
    assert result["lensStatuses"]["visual"] == "unavailable"
    assert result["claimStatus"] == "observed"
    assert result["resolvedStatus"] == "observed"
    assert result["fingerprint"].startswith("mcel-observation.")
    assert result["valid"] is True
    assert result["frozen"] is True


def test_observation_fingerprint_is_stable_across_fact_order() -> None:
    result = run_node_json(
        f"""
        const first = observations.createObservationBundle({bundle_input_js()});
        const second = observations.createObservationBundle({bundle_input_js(reverse=True)});
        process.stdout.write(JSON.stringify({{
          first: first.bundleFingerprint,
          second: second.bundleFingerprint,
          equal: first.bundleFingerprint === second.bundleFingerprint
        }}));
        """
    )

    assert result["equal"] is True
    assert result["first"] == result["second"]


def test_observation_bundle_rejects_active_mutation_mode() -> None:
    result = run_node_json(
        f"""
        const input = {bundle_input_js()};
        input.mode = "active-exploration";
        let code = "";
        let message = "";
        try {{
          observations.createObservationBundle(input);
        }} catch (error) {{
          code = error.code;
          message = error.message;
        }}
        process.stdout.write(JSON.stringify({{code, message}}));
        """
    )

    assert result["code"] == "MCEL_OBSERVATION_MODE_FORBIDDEN"
    assert "read-only" in result["message"]


def test_absent_lenses_are_explicitly_missing_not_silent_successes() -> None:
    result = run_node_json(
        """
        const bundle = observations.createObservationBundle({
          observationId: "observation.minimal",
          appId: "minimal",
          capturedAt: "2026-07-30T18:00:00Z",
          repositoryFingerprint: "repo:abc123",
          provenance: {
            producer: "contract-test",
            collectorVersion: "v1",
            codeFingerprint: "sha256:contract-test"
          },
          lenses: {
            dom: {status: "captured", facts: []}
          },
          claims: []
        });
        process.stdout.write(JSON.stringify({
          valid: observations.validateObservationBundle(bundle).valid,
          missing: Object.values(bundle.lenses)
            .filter((lens) => lens.status === "missing")
            .map((lens) => [lens.id, lens.reason])
        }));
        """
    )

    assert result["valid"] is True
    assert result["missing"] == [
        ["accessibility", "not-collected"],
        ["layout", "not-collected"],
        ["visual", "not-collected"],
        ["source", "not-collected"],
        ["transition", "not-collected"],
        ["ridges", "not-collected"],
    ]


def test_tampered_claim_resolution_and_bundle_fingerprint_are_rejected() -> None:
    result = run_node_json(
        f"""
        const bundle = observations.createObservationBundle({bundle_input_js()});
        const tampered = JSON.parse(JSON.stringify(bundle));
        tampered.resolvedClaims[0].value = "link";
        const report = observations.validateObservationBundle(tampered);
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          diagnosticCodes: report.diagnosticCodes
        }}));
        """
    )

    assert result["valid"] is False
    assert "observation-claim-resolution-invalid" in result["diagnosticCodes"]
    assert "observation-fingerprint-invalid" in result["diagnosticCodes"]
