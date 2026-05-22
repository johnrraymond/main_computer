from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_release_diagnosis():
    spec = importlib.util.spec_from_file_location("release_diagnosis", ROOT / "tools" / "release_diagnosis.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_release_critical_tests_exist() -> None:
    preflight = load_release_diagnosis()

    for relative in preflight.RELEASE_CRITICAL_TESTS:
        assert (ROOT / relative).exists(), relative


def test_build_steps_uses_read_only_health_and_focused_pytest() -> None:
    preflight = load_release_diagnosis()

    steps = preflight.build_steps(ROOT, python_executable="python", max_source_bytes=123)

    assert [step.name for step in steps] == ["source-health", "release-critical-tests"]
    assert steps[0].command == (
        "python",
        "tools/project_diagnosis.py",
        "--stage",
        "simple",
        "--max-source-bytes",
        "123",
    )
    assert steps[1].command[:3] == ("python", "-m", "pytest")
    assert "tests/test_prod_command_script.py" in steps[1].command
    assert "tests/test_project_health_check.py" in steps[1].command
    assert "tests/test_executor_preflight.py" in steps[1].command
    assert "tests/test_viewport_onlyoffice.py" in steps[1].command
    assert steps[1].command[-1] == "-q"


def test_build_steps_can_include_executor_diagnosis() -> None:
    preflight = load_release_diagnosis()

    steps = preflight.build_steps(
        ROOT,
        python_executable="python",
        include_health=False,
        include_pytest=False,
        include_executor=True,
    )

    assert [step.name for step in steps] == ["executor-preflight"]
    assert steps[0].command == ("python", "tools/executor_diagnosis.py")


def test_run_preflight_reports_failure_from_any_step(monkeypatch) -> None:
    preflight = load_release_diagnosis()

    def fake_run_step(step, *, cwd, timeout):
        return preflight.StepResult(
            name=step.name,
            command=step.command,
            returncode=1 if step.name == "release-critical-tests" else 0,
            stdout="",
            stderr="boom" if step.name == "release-critical-tests" else "",
            elapsed_s=0.01,
        )

    monkeypatch.setattr(preflight, "run_step", fake_run_step)

    report = preflight.run_preflight(ROOT)

    assert report.ok is False
    assert len(report.steps) == 2
    assert report.steps[0].ok is True
    assert report.steps[1].ok is False


def test_main_supports_json_output_and_skip_pytest(monkeypatch, capsys) -> None:
    preflight = load_release_diagnosis()

    def fake_run_step(step, *, cwd, timeout):
        return preflight.StepResult(
            name=step.name,
            command=step.command,
            returncode=0,
            stdout="",
            stderr="",
            elapsed_s=0.01,
        )

    monkeypatch.setattr(preflight, "run_step", fake_run_step)

    code = preflight.main(["--repo-root", str(ROOT), "--skip-pytest", "--json"])

    assert code == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert '"source-health"' in output
    assert "release-critical-tests" not in output


def test_main_can_include_executor_diagnosis(monkeypatch, capsys) -> None:
    preflight = load_release_diagnosis()

    def fake_run_step(step, *, cwd, timeout):
        return preflight.StepResult(
            name=step.name,
            command=step.command,
            returncode=0,
            stdout="",
            stderr="",
            elapsed_s=0.01,
        )

    monkeypatch.setattr(preflight, "run_step", fake_run_step)

    code = preflight.main(["--repo-root", str(ROOT), "--skip-health", "--skip-pytest", "--include-executor", "--json"])

    assert code == 0
    output = capsys.readouterr().out
    assert '"executor-preflight"' in output
    assert '"tools/executor_diagnosis.py"' in output


def test_write_release_evidence_report_records_preflight_and_source_manifest(tmp_path) -> None:
    preflight = load_release_diagnosis()
    (tmp_path / "main_computer").mkdir()
    (tmp_path / "main_computer" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "release_reports").mkdir()
    (tmp_path / "release_reports" / "old.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runtime" / "deployments").mkdir(parents=True)
    (tmp_path / "runtime" / "deployments" / "current.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /private/path\n", encoding="utf-8")
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "scratch.txt").write_text("temporary\n", encoding="utf-8")
    (tmp_path / "aider.log").mkdir()
    (tmp_path / "aider.log" / "aider.log").write_text("local log\n", encoding="utf-8")
    (tmp_path / ".prod.lock").write_text("locked\n", encoding="utf-8")
    (tmp_path / "worker.pid").write_text("123\n", encoding="utf-8")
    (tmp_path / "debug_assets" / "rga").mkdir(parents=True)
    (tmp_path / "debug_assets" / "rga" / "gremlin_stdout.txt").write_text("local output\n", encoding="utf-8")
    (tmp_path / "revision_control" / "snapshots").mkdir(parents=True)
    (tmp_path / "revision_control" / "snapshots" / "old_stderr.txt").write_text("old output\n", encoding="utf-8")
    (tmp_path / "contracts" / "cache").mkdir(parents=True)
    (tmp_path / "contracts" / "cache" / "solidity-files-cache.json").write_text("{}", encoding="utf-8")
    (tmp_path / "main_computer" / ".main_computer_browser_profile" / "Default").mkdir(parents=True)
    (tmp_path / "main_computer" / ".main_computer_browser_profile" / "Default" / "Cookies").write_text(
        "browser local state\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "patching" / "reports").mkdir(parents=True)
    (tmp_path / "tools" / "patching" / "reports" / "aider_stdout.txt").write_text("old patch output\n", encoding="utf-8")

    report = preflight.PreflightReport(
        ok=True,
        repo_root=str(tmp_path),
        elapsed_s=0.2,
        steps=(
            preflight.StepResult(
                name="source-health",
                command=(str(Path.home() / "dsl" / ".venv" / "Scripts" / "python.exe"), "tools/project_diagnosis.py"),
                returncode=0,
                stdout=f"PASS from {tmp_path}\\runtime\\deployments\\current.json\n",
                stderr=f"venv={tmp_path / '.venv'}\n",
                elapsed_s=0.1,
            ),
        ),
    )

    report_path = preflight.write_release_evidence_report(
        tmp_path,
        report,
        created_at=dt.datetime(2026, 5, 10, 12, 34, 56, tzinfo=dt.timezone.utc),
    )

    assert report_path.name == "rc-20260510-123456Z.json"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["schema"] == "main-computer.release-candidate-report.v1"
    assert data["release_status"] == "candidate-preflight-passed"
    assert data["repo"] == {"name": tmp_path.name, "root": "."}
    assert data["python"] == {
        "implementation": preflight.platform.python_implementation(),
        "version": preflight.platform.python_version(),
    }
    assert data["preflight"]["ok"] is True
    assert data["preflight"]["repo_root"] == "."
    assert data["preflight"]["steps"][0]["command"] == ["python", "tools/project_diagnosis.py"]
    assert "stdout" not in data["preflight"]["steps"][0]
    assert "stderr" not in data["preflight"]["steps"][0]
    assert "repo_root" not in data
    assert "executable" not in data["python"]
    assert data["source_manifest_scope"] == "clean-source-exclusions-applied"
    assert data["source_manifest_policy"]["name"] == "clean-release-source"
    assert "clean_source_exclusions" not in data
    assert "real production deploy command is not implemented" in data["known_not_release_ready_exclusions"]

    serialized = json.dumps(data, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert r"C:\Users" not in serialized
    assert r"\Users\\" not in serialized
    assert "C:/Users" not in serialized
    assert "/home/" not in serialized
    assert "Scripts\\\\python.exe" not in serialized
    assert ".venv" not in serialized
    assert ".prod.lock" not in serialized
    assert "runtime/deployments/current.json" not in serialized
    assert "contracts/cache/solidity-files-cache.json" not in serialized
    assert "main_computer/.main_computer_browser_profile" not in serialized
    assert "release_reports/" not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized

    manifest_paths = {item["path"] for item in data["source_manifest"]["files"]}
    assert "main_computer/__init__.py" in manifest_paths
    assert "release_reports/old.json" not in manifest_paths
    assert "runtime/deployments/current.json" not in manifest_paths
    assert ".venv/pyvenv.cfg" not in manifest_paths
    assert ".tmp/scratch.txt" not in manifest_paths
    assert "aider.log/aider.log" not in manifest_paths
    assert ".prod.lock" not in manifest_paths
    assert "worker.pid" not in manifest_paths
    assert "debug_assets/rga/gremlin_stdout.txt" not in manifest_paths
    assert "revision_control/snapshots/old_stderr.txt" not in manifest_paths
    assert "contracts/cache/solidity-files-cache.json" not in manifest_paths
    assert "main_computer/.main_computer_browser_profile/Default/Cookies" not in manifest_paths
    assert "tools/patching/reports/aider_stdout.txt" not in manifest_paths
    assert report_path.relative_to(tmp_path).as_posix() not in manifest_paths


def test_release_evidence_avoids_windows_wmi_platform_helpers(monkeypatch, tmp_path) -> None:
    preflight = load_release_diagnosis()
    (tmp_path / "main_computer").mkdir()
    (tmp_path / "main_computer" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    def fail_platform_call(*args, **kwargs):
        raise AssertionError("release evidence must not call WMI-backed platform helpers")

    monkeypatch.setattr(preflight.platform, "platform", fail_platform_call)
    monkeypatch.setattr(preflight.platform, "system", fail_platform_call)
    monkeypatch.setattr(preflight.platform, "release", fail_platform_call)

    evidence = preflight.build_release_evidence(
        tmp_path,
        preflight.PreflightReport(ok=True, repo_root=str(tmp_path), elapsed_s=0.0, steps=()),
        created_at=dt.datetime(2026, 5, 10, 12, 34, 56, tzinfo=dt.timezone.utc),
    )

    assert evidence["platform"]["platform"]
    assert evidence["platform"]["system"]
    assert "source_manifest" in evidence


def test_main_can_write_release_report(monkeypatch, capsys, tmp_path) -> None:
    preflight = load_release_diagnosis()

    def fake_run_step(step, *, cwd, timeout):
        return preflight.StepResult(
            name=step.name,
            command=step.command,
            returncode=0,
            stdout="",
            stderr="",
            elapsed_s=0.01,
        )

    monkeypatch.setattr(preflight, "run_step", fake_run_step)

    code = preflight.main(
        [
            "--repo-root",
            str(tmp_path),
            "--skip-pytest",
            "--write-report",
            "--report-dir",
            "reports",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "Release report:" in output
    report_files = list((tmp_path / "reports").glob("rc-*.json"))
    assert len(report_files) == 1
