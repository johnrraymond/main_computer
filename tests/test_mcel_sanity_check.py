from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mcel_sanity_check import (  # noqa: E402
    SANITY_VERSION,
    _extract_browser_payload,
    run_sanity_check,
)


def test_sanity_check_v1_passes_current_docs_without_contract_warnings() -> None:
    report = run_sanity_check(ROOT, strict=True)
    data = report.to_dict()

    assert data["schema"] == SANITY_VERSION
    assert report.valid
    assert data["counts"]["errors"] == 0
    assert data["registry_summary"]["strict_schema_ready"] is True
    assert set(data["registry_summary"]["app_contracts"]) == {
        "calculator",
        "code-editor",
        "file-explorer",
        "git-tools",
        "mcel-lab",
        "website-builder",
    }

    assert data["counts"]["warnings"] == 0
    assert {issue.code for issue in report.warnings} == set()


def test_sanity_check_detects_generated_browser_registry_payload() -> None:
    registry_js = ROOT / "main_computer/web/applications/scripts/mcel-requirements-registry.js"
    payload = _extract_browser_payload(registry_js.read_text(encoding="utf-8"))

    assert payload["registry_version"] == "mcel-requirements-registry-v1"
    assert payload["strict_schema_ready"] is True
    assert payload["summary"]["total_blocks"] >= 300
    assert "code-editor" in payload["app_contracts"]
    assert "mcel-lab" in payload["app_contracts"]
    assert payload["app_comparison_seeds"]["code-editor"]["declared_form_primitive_count"] >= 7
    assert payload["app_comparison_seeds"]["calculator"]["declared_form_primitive_count"] >= 6


def test_sanity_check_cli_supports_text_and_json_output() -> None:
    text_run = subprocess.run(
        [sys.executable, "tools/mcel_sanity_check.py", "--strict"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert text_run.returncode == 0
    assert "mcel-sanity-check-v1" in text_run.stdout
    assert "valid: true" in text_run.stdout
    assert "errors: 0" in text_run.stdout

    json_run = subprocess.run(
        [sys.executable, "tools/mcel_sanity_check.py", "--strict", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert json_run.returncode == 0
    payload = json.loads(json_run.stdout)
    assert payload["schema"] == "mcel-sanity-check-v1"
    assert payload["valid"] is True
    assert payload["counts"]["errors"] == 0
    assert payload["counts"]["warnings"] == 0
