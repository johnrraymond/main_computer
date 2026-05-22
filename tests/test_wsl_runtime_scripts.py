from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wsl_runtime_manifest_defines_separate_test_and_prod_profiles() -> None:
    manifest = json.loads((REPO_ROOT / "runtime" / "main-computer-runtime.json").read_text(encoding="utf-8"))

    assert manifest["defaultProfile"] == "test"
    assert manifest["profiles"]["test"]["distributionName"] == "MainComputerExecutorTest"
    assert manifest["profiles"]["prod"]["distributionName"] == "MainComputerExecutor"
    assert manifest["profiles"]["test"]["rootfsTar"].endswith("wsl-test\\images\\MainComputerExecutorTest-rootfs.tar")
    assert manifest["profiles"]["prod"]["rootfsTar"].endswith("wsl\\images\\MainComputerExecutor-rootfs.tar")
    assert not manifest["profiles"]["test"]["rootfsTar"].startswith("runtime/")
    assert not manifest["profiles"]["prod"]["rootfsTar"].startswith("runtime/")


def test_wsl_runtime_scripts_are_profile_aware() -> None:
    for script_name in (
        "build-main-computer-runtime.ps1",
        "install-main-computer-runtime.ps1",
        "doctor-main-computer-runtime.ps1",
    ):
        text = (REPO_ROOT / "scripts" / "windows" / script_name).read_text(encoding="utf-8")
        assert 'ValidateSet("test", "prod' in text
        assert "MainComputerExecutorTest" in text
        assert "MainComputerExecutor" in text
        assert "ConvertTo-RuntimeImageFileName" in text
        assert '"images"' in text


def test_install_script_writes_executor_backend_environment_for_selected_profile() -> None:
    text = (REPO_ROOT / "scripts" / "windows" / "install-main-computer-runtime.ps1").read_text(encoding="utf-8")

    assert "MAIN_COMPUTER_EXECUTOR_BACKEND" in text
    assert "MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION" in text
    assert "main-computer-runtime.test.json" in text
    assert "main-computer-runtime.json" in text


def test_runtime_builder_resolves_container_tool_as_executable_not_repo_directory() -> None:
    text = (REPO_ROOT / "scripts" / "windows" / "build-main-computer-runtime.ps1").read_text(encoding="utf-8")

    assert "Get-Command $CommandName -CommandType Application" in text
    assert "Test-Path -LiteralPath $CommandName -PathType Leaf" in text
    assert "Container tool executable not found" in text
    assert "No supported container tool executable found" in text

def test_native_command_wrapper_preserves_shell_command_arguments() -> None:
    for script_name in (
        "build-main-computer-runtime.ps1",
        "install-main-computer-runtime.ps1",
        "doctor-main-computer-runtime.ps1",
    ):
        text = (REPO_ROOT / "scripts" / "windows" / script_name).read_text(encoding="utf-8")
        assert "function ConvertTo-NativeArgument" in text
        assert "function Join-NativeArgumentList" in text
        assert "Join-NativeArgumentList -Arguments $Arguments" in text

    doctor_text = (REPO_ROOT / "scripts" / "windows" / "doctor-main-computer-runtime.ps1").read_text(encoding="utf-8")
    assert "main-computer-executor-ok && uname -a" in doctor_text
    assert "Verification marker\" \"OK\" \"main-computer-executor-ok\"" in doctor_text


def test_runtime_builder_writes_shell_entrypoint_as_bom_free_lf_text() -> None:
    text = (REPO_ROOT / "scripts" / "windows" / "build-main-computer-runtime.ps1").read_text(encoding="utf-8")

    assert "function Fail" in text
    assert "$RepoRoot = Get-RepoRoot" in text
    assert "function Write-Utf8NoBomFile" in text
    assert 'New-Object System.Text.UTF8Encoding -ArgumentList $false' in text
    assert '[System.IO.File]::WriteAllText($Path, $normalized, $encoding)' in text
    assert '$entrypointSource = Join-Path $RepoRoot "docker\\executor\\main-computer-exec"' in text
    assert "$entrypoint = Get-Content -LiteralPath $entrypointSource -Raw" in text
    assert 'Write-Utf8NoBomFile -Path (Join-Path $tempDir "main-computer-exec") -Text $entrypoint' in text
    assert 'Set-Content -LiteralPath (Join-Path $tempDir "main-computer-exec")' not in text
    assert '$normalized = $Text -replace "`r`n", "`n" -replace "`r", "`n"' in text
    assert '$normalized[0] -eq [char]0xFEFF' in text


def test_runtime_builders_use_shared_executor_entrypoint_source() -> None:
    for script_path in (
        REPO_ROOT / "scripts" / "windows" / "build-main-computer-runtime.ps1",
        REPO_ROOT / "proto-dev" / "build-proto-dev-runtime.ps1",
    ):
        text = script_path.read_text(encoding="utf-8")
        assert '$entrypointSource = Join-Path $RepoRoot "docker\\executor\\main-computer-exec"' in text
        assert "$entrypoint = Get-Content -LiteralPath $entrypointSource -Raw" in text
        assert 'Executor entrypoint source not found' in text
        assert 'exec "$@"' not in text
        assert "main-computer-exec ready" not in text
        assert "/usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs --" in text
        assert "main-computer-exec-contract-ok" in text
