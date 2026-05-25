from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_experimental_native_installer_files_are_additive() -> None:
    expected_new_files = [
        "installer/windows/MainComputer.experimental.iss",
        "installer/windows/Install-MainComputer-from-Package.experimental.ps1",
        "installer/windows/README.experimental.md",
        "scripts/windows/build-main-computer-native-installer.experimental.ps1",
    ]

    for relative_path in expected_new_files:
        assert (ROOT / relative_path).is_file(), relative_path

    # The experimental builder is deliberately separate from the existing
    # runtime builder and does not depend on replacing the export script.
    assert (ROOT / "scripts/windows/build-main-computer-runtime.ps1").is_file()
    assert (ROOT / "export-main-computer-test.ps1").is_file()


def test_experimental_builder_compiles_inno_setup_exe_without_touching_existing_builders() -> None:
    builder = read_text("scripts/windows/build-main-computer-native-installer.experimental.ps1")

    assert "MainComputer.experimental.iss" in builder
    assert "Install-MainComputer-from-Package.experimental.ps1" in builder
    assert "installer-native-experimental" in builder
    assert "ISCC.exe" in builder
    assert "build-main-computer-installer.ps1" not in builder


def test_experimental_inno_definition_has_setup_exe_contract() -> None:
    definition = read_text("installer/windows/MainComputer.experimental.iss")

    assert "OutputBaseFilename=MainComputer-{#AppVersion}-Setup" in definition
    assert "DefaultDirName={localappdata}\\Programs\\Main Computer" in definition
    assert "PrivilegesRequired=lowest" in definition
    assert "payload\\main_computer_test" in definition
    assert "Install-MainComputer-from-Package.ps1" in definition


def test_package_wrapper_delegates_to_existing_python_installer_payload() -> None:
    wrapper = read_text("installer/windows/Install-MainComputer-from-Package.experimental.ps1")

    assert "payload\\main_computer_test" in wrapper
    assert "bootstrap-main-computer-python-windows.ps1" in wrapper
    assert '"-RepoRoot", $payloadRoot' in wrapper
    assert '"-NoReHome"' in wrapper
