from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.mcel_scaffolding import (
    InvalidScaffoldInput,
    ScaffoldWriteError,
    UnsafeScaffoldDestination,
    generate_application,
    render_package_files,
    validate_app_id,
    validate_package_path,
)
from main_computer.mcel_scaffolding import generator as generator_module
from tools.mcel_requirements_registry import (
    RequirementsRegistry,
    derive_required_fields_from_grammar,
    extract_blocks_from_file,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mcel_create_app.py"
GOLDEN = ROOT / "tests" / "fixtures" / "mcel_application_template_v1" / "contract-counter"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _strict_requirements_registry(package_root: Path) -> RequirementsRegistry:
    requirements = package_root / "requirements.md"
    blocks, errors = extract_blocks_from_file(requirements, package_root)
    registry = RequirementsRegistry(
        repo_root=package_root,
        pretty_docs_root=package_root,
        blocks=blocks,
        errors=errors,
    )
    registry.grammar_required_fields = derive_required_fields_from_grammar(registry.blocks)
    validate_registry(registry, strict_schema=True)
    return registry


@pytest.mark.parametrize(
    "app_id",
    ["counter", "contract-counter", "mcel-app-1", "a1-b2-c3"],
)
def test_mcel_create_app_accepts_canonical_identifiers(app_id: str) -> None:
    assert validate_app_id(app_id) == app_id


@pytest.mark.parametrize(
    "app_id",
    ["", "Counter", "contract_counter", "contract counter", "-counter", "counter-", "a--b", "../counter", "a/b", r"a\\b"],
)
def test_mcel_create_app_refuses_unsafe_or_noncanonical_identifiers(app_id: str) -> None:
    with pytest.raises(InvalidScaffoldInput):
        validate_app_id(app_id)


def test_mcel_create_app_dry_run_is_write_free(tmp_path: Path) -> None:
    output_root = tmp_path / "not-created"

    result = generate_application(
        "contract-counter",
        title="Contract Counter",
        output_root=output_root,
        dry_run=True,
    )

    assert result.ok is True
    assert result.result_code == "dry_run_valid"
    assert result.validation.ok is True
    assert result.created_files
    assert not output_root.exists()


def test_mcel_create_app_generates_structurally_valid_package(tmp_path: Path) -> None:
    result = generate_application(
        "contract-counter",
        title="Contract Counter",
        output_root=tmp_path,
    )
    package_root = tmp_path / "contract-counter"

    assert result.result_code == "generated"
    assert package_root.is_dir()
    assert validate_package_path(
        package_root,
        expected_app_id="contract-counter",
        expected_title="Contract Counter",
        expected_template_id="mcel.canonical-application-template",
        expected_template_version="1.0.0",
    ).ok

    manifest = json.loads((package_root / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["conformance"]["currentMode"] == "structural-only"
    assert manifest["conformance"]["targetMode"] == "semantic-runtime-proven"
    assert set(manifest["conformance"]["missingBridges"]) == set(result.target_gaps)


def test_mcel_create_app_is_byte_deterministic_and_path_independent(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "nested" / "second"

    generate_application("contract-counter", title="Contract Counter", output_root=first_root)
    generate_application("contract-counter", title="Contract Counter", output_root=second_root)

    assert _tree_bytes(first_root / "contract-counter") == _tree_bytes(second_root / "contract-counter")


def test_mcel_create_app_matches_checked_in_golden_fixture(tmp_path: Path) -> None:
    generate_application("contract-counter", title="Contract Counter", output_root=tmp_path)

    assert _tree_bytes(tmp_path / "contract-counter") == _tree_bytes(GOLDEN)


def test_mcel_create_app_refuses_destination_collision_without_mutation(tmp_path: Path) -> None:
    destination = tmp_path / "contract-counter"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("do not overwrite\n", encoding="utf-8")

    with pytest.raises(UnsafeScaffoldDestination):
        generate_application("contract-counter", title="Contract Counter", output_root=tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "do not overwrite\n"
    assert list(destination.iterdir()) == [sentinel]


def test_mcel_create_app_cleans_partial_output_after_write_failure(tmp_path: Path) -> None:
    writes = 0

    def failing_writer(path: Path, text: str) -> None:
        nonlocal writes
        writes += 1
        if writes == 4:
            raise OSError("injected write failure")
        generator_module._write_text_file(path, text)

    with pytest.raises(ScaffoldWriteError):
        generate_application(
            "contract-counter",
            title="Contract Counter",
            output_root=tmp_path / "apps",
            writer=failing_writer,
        )

    output_root = tmp_path / "apps"
    assert not (output_root / "contract-counter").exists()
    assert not list(output_root.glob(".contract-counter.mcel-create-*")) if output_root.exists() else True


def test_mcel_create_app_generated_requirements_pass_current_strict_parser(tmp_path: Path) -> None:
    generate_application("contract-counter", title="Contract Counter", output_root=tmp_path)
    registry = _strict_requirements_registry(tmp_path / "contract-counter")

    assert registry.valid is True
    assert registry.strict_schema_ready is True
    assert registry.errors == []
    assert registry.warnings == []


def test_mcel_create_app_generated_tests_collect_and_pass(tmp_path: Path) -> None:
    generate_application("contract-counter", title="Contract Counter", output_root=tmp_path)
    package_root = tmp_path / "contract-counter"

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(package_root / "tests")],
        cwd=package_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "passed" in completed.stdout


def test_mcel_create_app_cli_json_result_is_machine_readable_and_write_free(tmp_path: Path) -> None:
    output_root = tmp_path / "apps"
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "contract-counter",
            "--title",
            "Contract Counter",
            "--output-root",
            str(output_root),
            "--dry-run",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "mcel.create-app-result.v1"
    assert payload["result_code"] == "dry_run_valid"
    assert payload["validation"]["ok"] is True
    assert "Target integrations still missing" in completed.stderr
    assert not output_root.exists()


def test_mcel_create_app_cli_uses_stable_invalid_input_exit_class(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "Not-Valid",
            "--output-root",
            str(tmp_path),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["result_code"] == "invalid_input"


def test_template_rendering_substitutes_identity_without_contract_counter_leakage() -> None:
    _template, title, files = render_package_files("sample-app", "Sample Application")
    joined = "\n".join(files.values())

    assert title == "Sample Application"
    assert '"appId": "sample-app"' in files["mcel.app.json"]
    assert 'id="sample-app-app"' in files["src/index.html"]
    assert "Contract Counter" not in joined
    assert "contract-counter" not in joined
    assert "{{" not in joined
