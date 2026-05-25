from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_experimental_nsis_installer_files_are_additive() -> None:
    expected_new_files = [
        "installer/windows/MainComputer.experimental.nsi",
        "scripts/windows/build-main-computer-nsis-installer.experimental.ps1",
    ]

    for relative_path in expected_new_files:
        assert (ROOT / relative_path).is_file(), relative_path

    # This experiment is intentionally separate from the existing runtime and
    # Inno experiments. It does not replace or delete either path.
    assert (ROOT / "scripts/windows/build-main-computer-runtime.ps1").is_file()
    assert "build-main-computer-native-installer.experimental.ps1" not in expected_new_files


def test_experimental_builder_uses_nsis_not_inno_setup() -> None:
    builder = read_text("scripts/windows/build-main-computer-nsis-installer.experimental.ps1")

    assert "makensis.exe" in builder
    assert "NSIS\\makensis.exe" in builder
    assert "installer-nsis-experimental" in builder
    assert "MainComputer.experimental.nsi" in builder
    assert "ISCC.exe" not in builder
    assert "Inno Setup" not in builder


def test_experimental_builder_skips_generated_long_path_trees() -> None:
    builder = read_text("scripts/windows/build-main-computer-nsis-installer.experimental.ps1")

    assert "diagnostics_output" in builder
    assert "ollama_prompt_space_" in builder
    assert "harness_output" in builder
    assert "release_reports" in builder
    assert '".map"' in builder
    assert "[System.IO.File]::Copy" in builder


def test_experimental_builder_generates_package_wrapper_for_python_bootstrap() -> None:
    builder = read_text("scripts/windows/build-main-computer-nsis-installer.experimental.ps1")

    assert "Install-MainComputer-from-Package.nsis-experimental.ps1" in builder
    assert "payload\\main_computer_test" in builder
    assert "bootstrap-main-computer-python-windows.ps1" in builder
    assert '"-RepoRoot", $payloadRoot' in builder
    assert '"-NoReHome"' in builder


def test_experimental_nsis_definition_builds_setup_exe_contract() -> None:
    definition = read_text("installer/windows/MainComputer.experimental.nsi")

    assert 'OutFile "${OutputRoot}\\MainComputer-${MainComputerVersion}-Setup.exe"' in definition
    assert 'InstallDir "$LOCALAPPDATA\\Programs\\Main Computer"' in definition
    assert "RequestExecutionLevel user" in definition
    assert 'File /r "${StageRoot}\\payload\\main_computer_test"' in definition
    assert "ExecWait" in definition
    assert "WriteUninstaller" in definition
