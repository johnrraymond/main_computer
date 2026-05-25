from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/windows/build-main-computer-nsis-installer.experimental-v5.ps1"


def read_builder() -> str:
    return BUILDER.read_text(encoding="utf-8")


def test_v5_builder_is_additive_and_uses_separate_output_tree() -> None:
    text = read_builder()

    assert BUILDER.is_file()
    assert "build-main-computer-nsis-installer.experimental-v5.ps1" in text
    assert "installer-nsis-experimental-v5" in text
    assert "mc-nsis-v5" in text
    assert "MainComputer.experimental-v5.generated.nsi" in text
    assert "Install-MainComputer-from-Package.nsis-experimental-v5.ps1" in text


def test_v5_installer_prompts_for_mode_with_nsdialogs() -> None:
    text = read_builder()

    assert '!include "nsDialogs.nsh"' in text
    assert "Page custom ModePage ModePageLeave" in text
    assert "Function ModePage" in text
    assert "Function ModePageLeave" in text
    assert '${NSD_CreateRadioButton} 0 32u 100% 12u "Main Computer - Unleashed"' in text
    assert '${NSD_CreateRadioButton} 0 70u 100% 12u "Main Computer - Debug"' in text
    assert '${NSD_CreateRadioButton} 0 108u 100% 12u "Main Computer - Safe"' in text
    assert 'StrCpy $InstallModeArg "Unleashed"' in text
    assert 'StrCpy $InstallModeArg "Debug"' in text
    assert 'StrCpy $InstallModeArg "Safe"' in text


def test_v5_passes_selected_mode_and_install_root_to_python_bootstrap() -> None:
    text = read_builder()

    assert 'StrCpy $ResolvedInstallRoot "$PROFILE\\.main-computer-tools\\installs\\main_computer_test-test-$InstallModeKey"' in text
    assert '-RuntimeProfile test -Mode "$InstallModeArg" -InstallRoot "$ResolvedInstallRoot" -VerboseBootstrap' in text
    assert '"-RuntimeProfile", $RuntimeProfile' in text
    assert '"-Mode", $Mode' in text
    assert '"-InstallRoot", $InstallRoot' in text


def test_v5_creates_desktop_and_start_menu_shortcuts_to_start_v2_open_browser() -> None:
    text = read_builder()

    assert "Function CreateMainComputerModeShortcuts" in text
    assert 'CreateShortCut "$SMPROGRAMS\\Main Computer\\Main Computer - $ShortcutModeName.lnk"' in text
    assert 'CreateShortCut "$DESKTOP\\Main Computer - $ShortcutModeName.lnk"' in text
    assert '"$ResolvedInstallRoot\\start_v2.bat" "-OpenBrowser"' in text
    assert "DetailPrint \"Shortcut target: $ResolvedInstallRoot\\start_v2.bat -OpenBrowser\"" in text


def test_v5_uninstaller_removes_mode_shortcuts() -> None:
    text = read_builder()

    assert "Function RemoveMainComputerModeShortcuts" in text
    assert 'Delete "$DESKTOP\\Main Computer - Unleashed.lnk"' in text
    assert 'Delete "$DESKTOP\\Main Computer - Debug.lnk"' in text
    assert 'Delete "$DESKTOP\\Main Computer - Safe.lnk"' in text
    assert 'Delete "$SMPROGRAMS\\Main Computer\\Main Computer - Unleashed.lnk"' in text
    assert 'Call RemoveMainComputerModeShortcuts' in text
