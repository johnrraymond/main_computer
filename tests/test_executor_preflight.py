from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_executor_preflight():
    spec = importlib.util.spec_from_file_location("executor_diagnosis", ROOT / "tools" / "executor_diagnosis.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_minimal_required_files(repo_root: Path, preflight) -> None:
    for required in preflight.REQUIRED_FILES:
        path = repo_root / required.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(required.markers or (required.purpose,)) + "\n", encoding="utf-8")


def test_required_executor_files_exist_in_current_repo() -> None:
    preflight = load_executor_preflight()

    report = preflight.run_preflight(ROOT)

    assert report.ok is True
    assert len(report.checks) == len(preflight.REQUIRED_FILES) + 1
    assert all(check.ok for check in report.checks)
    assert report.checks[-1].name == "docker-cli"
    assert report.checks[-1].skipped is True


def test_required_paths_are_repo_relative_and_safe() -> None:
    preflight = load_executor_preflight()

    for required in preflight.REQUIRED_FILES:
        assert preflight.is_safe_repo_relative_path(required.path), required.path
        assert not Path(required.path).is_absolute(), required.path
        assert ".." not in Path(required.path).parts, required.path


def test_missing_required_file_fails_with_clear_path(tmp_path: Path) -> None:
    preflight = load_executor_preflight()
    write_minimal_required_files(tmp_path, preflight)
    missing = tmp_path / "docker-compose.executor.yml"
    missing.unlink()

    report = preflight.run_preflight(tmp_path)

    assert report.ok is False
    failed = [check for check in report.checks if not check.ok]
    assert len(failed) == 1
    assert failed[0].path == "docker-compose.executor.yml"
    assert "missing executor compose file" in failed[0].message


def test_marker_mismatch_fails_with_clear_marker(tmp_path: Path) -> None:
    preflight = load_executor_preflight()
    write_minimal_required_files(tmp_path, preflight)
    dockerfile = tmp_path / "docker" / "executor" / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

    report = preflight.run_preflight(tmp_path)

    assert report.ok is False
    failed = [check for check in report.checks if check.path == "docker/executor/Dockerfile"]
    assert len(failed) == 1
    assert failed[0].ok is False
    assert "missing expected marker" in failed[0].message
    assert "main-computer-exec" in failed[0].message


def test_json_output_is_machine_readable(capsys) -> None:
    preflight = load_executor_preflight()

    code = preflight.main(["--repo-root", str(ROOT), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["require_docker"] is False
    assert any(check["name"] == "docker-cli" and check["skipped"] for check in payload["checks"])
    assert any(check["path"] == "docker-compose.executor.yml" for check in payload["checks"])


def test_require_docker_fails_when_docker_is_missing(monkeypatch, tmp_path: Path) -> None:
    preflight = load_executor_preflight()
    write_minimal_required_files(tmp_path, preflight)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    report = preflight.run_preflight(tmp_path, require_docker=True)

    assert report.ok is False
    docker_check = report.checks[-1]
    assert docker_check.name == "docker-cli"
    assert docker_check.ok is False
    assert docker_check.skipped is False
    assert "not found" in docker_check.message
