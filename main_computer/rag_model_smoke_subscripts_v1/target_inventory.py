from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from main_computer.rag_model_smoke_assembly_v1 import ASSEMBLY_VERSION
from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StepContract,
    StepResult,
    json_stdout,
    step_cli,
)


TARGET_MODULE_PATH = Path("main_computer/rag_model_smoke.py")
TARGET_TEST_PATH = Path("tests/test_rag_model_smoke.py")
REQUIRED_LITERALS = (
    "CSV_AUDIT_SCRIPT_PROMPT",
    "CSV_AUDIT_QUERIES",
    "run_csv_audit_model_smoke",
    "validate_csv_audit_model_smoke",
    "use_model=True",
    "Smoke test expected a real model-backed plan",
)


CONTRACT = StepContract(
    step_id="target_inventory",
    version=ASSEMBLY_VERSION,
    description=(
        "Locate the existing AI-backed rag_model_smoke CSV-audit scenario and prove "
        "this assembly is wrapping it rather than replacing it."
    ),
    provides=(
        StateProvision("target_smoke_module", "Python module name for the existing smoke script."),
        StateProvision("target_smoke_file", "Repository-relative path to the existing smoke script."),
        StateProvision("target_test_file", "Repository-relative path to the existing tests for the smoke."),
        StateProvision("target_scenario", "Existing rag_model_smoke scenario selected by this assembly."),
        StateProvision("target_prompt_sha256", "Stable fingerprint of the existing CSV-audit prompt literal region."),
        StateProvision("target_required_literals", "Required source literals found in the existing smoke file."),
        StateProvision("target_smoke_ready", "True when the existing target smoke file and test file are present."),
    ),
    evidence_required=("target_files", "source_fingerprint", "required_literals"),
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _literal_region(source: str) -> str:
    start = source.find("CSV_AUDIT_SCRIPT_PROMPT")
    end = source.find("BROKEN_CODE_REPAIR_PROMPT")
    if start < 0:
        return source[:4000]
    if end < start:
        end = min(len(source), start + 4000)
    return source[start:end]


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    target_file = repo_dir / TARGET_MODULE_PATH
    test_file = repo_dir / TARGET_TEST_PATH
    missing = [path.as_posix() for path in (TARGET_MODULE_PATH, TARGET_TEST_PATH) if not (repo_dir / path).exists()]
    if missing:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"missing": missing},
        )

    source = target_file.read_text(encoding="utf-8")
    found = [literal for literal in REQUIRED_LITERALS if literal in source]
    missing_literals = [literal for literal in REQUIRED_LITERALS if literal not in source]
    if missing_literals:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={
                "target_files": [TARGET_MODULE_PATH.as_posix(), TARGET_TEST_PATH.as_posix()],
                "required_literals": found,
            },
            details={"missing_literals": missing_literals},
        )

    fingerprint = _sha256_text(_literal_region(source))
    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "target_smoke_module": "main_computer.rag_model_smoke",
            "target_smoke_file": TARGET_MODULE_PATH.as_posix(),
            "target_test_file": TARGET_TEST_PATH.as_posix(),
            "target_scenario": "csv-audit",
            "target_prompt_sha256": fingerprint,
            "target_required_literals": found,
            "target_smoke_ready": True,
        },
        evidence={
            "target_files": {
                TARGET_MODULE_PATH.as_posix(): target_file.stat().st_size,
                TARGET_TEST_PATH.as_posix(): test_file.stat().st_size,
            },
            "source_fingerprint": fingerprint,
            "required_literals": found,
        },
        details={
            "left_existing_smoke_untouched": True,
            "assembly_scope": "new wrapper files only",
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
