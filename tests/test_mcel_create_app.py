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
    validate_package_files,
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
SAMPLE_APP_ID = "sample-app"
SAMPLE_TITLE = "Sample Application"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _rendered_tree_bytes(app_id: str = SAMPLE_APP_ID, title: str = SAMPLE_TITLE) -> dict[str, bytes]:
    _template, _title, files = render_package_files(app_id, title)
    return {relative_path: text.encode("utf-8") for relative_path, text in sorted(files.items())}


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
    ["counter", SAMPLE_APP_ID, "mcel-app-1", "a1-b2-c3"],
)
def test_mcel_create_app_accepts_canonical_identifiers(app_id: str) -> None:
    assert validate_app_id(app_id) == app_id


@pytest.mark.parametrize(
    "app_id",
    ["", "Counter", "sample_app", "sample app", "-sample", "sample-", "a--b", "../sample", "a/b", r"a\\b"],
)
def test_mcel_create_app_refuses_unsafe_or_noncanonical_identifiers(app_id: str) -> None:
    with pytest.raises(InvalidScaffoldInput):
        validate_app_id(app_id)


def test_mcel_create_app_dry_run_is_write_free(tmp_path: Path) -> None:
    output_root = tmp_path / "not-created"

    result = generate_application(
        SAMPLE_APP_ID,
        title=SAMPLE_TITLE,
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
        SAMPLE_APP_ID,
        title=SAMPLE_TITLE,
        output_root=tmp_path,
    )
    package_root = tmp_path / SAMPLE_APP_ID

    assert result.result_code == "generated"
    assert package_root.is_dir()
    assert validate_package_path(
        package_root,
        expected_app_id=SAMPLE_APP_ID,
        expected_title=SAMPLE_TITLE,
        expected_template_id="mcel.canonical-application-template",
        expected_template_version="1.0.0",
    ).ok

    manifest = json.loads((package_root / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["conformance"]["currentMode"] == "semantic-runtime-proven"
    assert manifest["conformance"]["targetMode"] == "semantic-runtime-proven"
    assert set(manifest["conformance"]["missingBridges"]) == set(result.target_gaps)
    assert manifest["tests"]["acceptanceBindings"] == "tests/mcel_acceptance_bindings.json"
    assert "package-local-acceptance-discovery" not in manifest["conformance"]["missingBridges"]


def test_mcel_create_app_is_byte_deterministic_and_path_independent(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "nested" / "second"

    generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=first_root)
    generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=second_root)

    assert _tree_bytes(first_root / SAMPLE_APP_ID) == _tree_bytes(second_root / SAMPLE_APP_ID)


def test_mcel_create_app_writes_the_rendered_template_without_fixture_golden(tmp_path: Path) -> None:
    generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=tmp_path)

    assert _tree_bytes(tmp_path / SAMPLE_APP_ID) == _rendered_tree_bytes()


def test_mcel_create_app_refuses_destination_collision_without_mutation(tmp_path: Path) -> None:
    destination = tmp_path / SAMPLE_APP_ID
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("do not overwrite\n", encoding="utf-8")

    with pytest.raises(UnsafeScaffoldDestination):
        generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=tmp_path)

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
            SAMPLE_APP_ID,
            title=SAMPLE_TITLE,
            output_root=tmp_path / "apps",
            writer=failing_writer,
        )

    output_root = tmp_path / "apps"
    assert not (output_root / SAMPLE_APP_ID).exists()
    assert not list(output_root.glob(f".{SAMPLE_APP_ID}.mcel-create-*")) if output_root.exists() else True


def test_mcel_create_app_generated_requirements_pass_current_strict_parser(tmp_path: Path) -> None:
    generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=tmp_path)
    registry = _strict_requirements_registry(tmp_path / SAMPLE_APP_ID)

    assert registry.valid is True
    assert registry.strict_schema_ready is True
    assert registry.errors == []
    assert registry.warnings == []


def test_mcel_create_app_generated_tests_collect_and_pass(tmp_path: Path) -> None:
    generate_application(SAMPLE_APP_ID, title=SAMPLE_TITLE, output_root=tmp_path)
    package_root = tmp_path / SAMPLE_APP_ID

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
            SAMPLE_APP_ID,
            "--title",
            SAMPLE_TITLE,
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
    assert "Target integrations: complete" in completed.stderr
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


def test_template_rendering_substitutes_identity_without_unresolved_placeholders() -> None:
    _template, title, files = render_package_files(SAMPLE_APP_ID, SAMPLE_TITLE)
    joined = "\n".join(files.values())

    assert title == SAMPLE_TITLE
    assert f'"appId": "{SAMPLE_APP_ID}"' in files["mcel.app.json"]
    assert f'id="{SAMPLE_APP_ID}-app"' in files["src/index.html"]
    assert SAMPLE_TITLE in joined
    assert generator_module.PLACEHOLDER_PATTERN.search(joined) is None


def test_package_validator_requires_current_mode_and_gap_consistency() -> None:
    _template, _title, files = render_package_files(SAMPLE_APP_ID, SAMPLE_TITLE)
    manifest = json.loads(files["mcel.app.json"])

    manifest["conformance"]["currentMode"] = "semantic-runtime-proven"
    manifest["conformance"]["missingBridges"] = ["still-open"]
    files_with_open_gap = dict(files)
    files_with_open_gap["mcel.app.json"] = json.dumps(manifest, indent=2) + "\n"
    result = validate_package_files(
        files_with_open_gap,
        expected_app_id=SAMPLE_APP_ID,
        expected_title=SAMPLE_TITLE,
        expected_template_id="mcel.canonical-application-template",
        expected_template_version="1.0.0",
    )
    assert not result.ok
    assert "proven-package-with-open-gap" in {issue.code for issue in result.errors}

    manifest["conformance"]["currentMode"] = "structural-only"
    manifest["conformance"]["missingBridges"] = []
    files_without_gap = dict(files)
    files_without_gap["mcel.app.json"] = json.dumps(manifest, indent=2) + "\n"
    result = validate_package_files(
        files_without_gap,
        expected_app_id=SAMPLE_APP_ID,
        expected_title=SAMPLE_TITLE,
        expected_template_id="mcel.canonical-application-template",
        expected_template_version="1.0.0",
    )
    assert not result.ok
    assert "missing-target-gaps" in {issue.code for issue in result.errors}
