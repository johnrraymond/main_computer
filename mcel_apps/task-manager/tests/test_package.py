from __future__ import annotations

import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_package_manifest_resolves_every_declared_authority() -> None:
    manifest = json.loads((PACKAGE_ROOT / "mcel.app.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == "mcel.application-package.v1"
    assert manifest["appId"] == "task-manager"
    assert manifest["title"] == "Task Manager"
    assert manifest["template"] == {
        "id": "mcel.host-bound-authoritative-application",
        "version": "0.1.0",
    }

    references = [manifest["requirements"], manifest["blueprint"]]
    references.extend(manifest["contracts"].values())
    references.extend(manifest.get("runtime", {}).values())
    references.append(manifest["authoring"]["source"])
    references.append(manifest["tests"]["acceptanceBindings"])
    for relative_path in references:
        assert (PACKAGE_ROOT / relative_path).is_file(), relative_path


def test_generated_package_reports_current_and_target_conformance_honestly() -> None:
    manifest = json.loads((PACKAGE_ROOT / "mcel.app.json").read_text(encoding="utf-8"))
    conformance = manifest["conformance"]

    # The package must not claim production parity or human approval merely
    # because its semantic source is hosted and compiler-valid.
    assert conformance["currentMode"] == "structural-only"
    assert conformance["targetMode"] == "semantic-runtime-proven"
    assert {"verified-server-controller-lifecycle", "live-incumbent-parity",
            "human-approval"}.issubset(set(conformance["missingBridges"]))
    assert manifest["promotion"]["promotionEligible"] is False
    assert manifest["projection"]["generatedArtifactsAreDerived"] is True
