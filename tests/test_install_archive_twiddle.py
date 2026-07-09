from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TWIDDLE_PATH = ROOT / "tools" / "twiddle-install-root-archive-failure.py"


def load_twiddle_module():
    spec = importlib.util.spec_from_file_location("twiddle_install_root_archive_failure", TWIDDLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_twiddle_discovers_actual_install_root_from_latest_nsis_log(tmp_path: Path) -> None:
    module = load_twiddle_module()

    package_root = tmp_path / "Programs" / "Main Computer"
    logs = package_root / "logs"
    logs.mkdir(parents=True)
    stamp = "20260706-122941"
    (logs / f"main-computer-python-installer-{stamp}.log").write_text(
        "\n".join(
            [
                "Main Computer NSIS package installer v7",
                f"Package root:  {package_root}",
                f"Payload root:  {package_root / 'payload' / 'main_computer_test'}",
                "",
                "[command]",
                (
                    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
                    f'"{package_root / "payload" / "main_computer_test" / "bootstrap-main-computer-python-windows.ps1"}" '
                    f'-RepoRoot "{package_root / "payload" / "main_computer_test"}" '
                    f'-InstallRoot "{tmp_path / ".main-computer-tools" / "installs" / "main_computer_test-test-unleashed"}" '
                    "-NoReHome"
                ),
                "",
                "[exit]",
                "Exit code: 61",
            ]
        ),
        encoding="utf-8",
    )
    (logs / f"main-computer-python-installer-{stamp}.stdout.txt").write_text(
        "\n".join(
            [
                "Handing off to Python bootstrap driver:",
                f"  repo root: {package_root / 'payload' / 'main_computer_test'}",
                f"  install target: {tmp_path / '.main-computer-tools' / 'installs' / 'main_computer_test-test-unleashed'}",
                "  target source: -InstallRoot",
                "",
                "Existing install root found. Preserving before fresh install:",
                f"  Source:  {tmp_path / '.main-computer-tools' / 'installs' / 'main_computer_test-test-unleashed'}",
                f"  Archive: {tmp_path / '.main-computer-tools' / 'installs' / '.main-computer-install-archives' / 'main_computer_test-test-unleashed' / 'main_computer_test-test-unleashed-20260706-122941.zip'}",
                f"  Move to: {tmp_path / '.main-computer-tools' / 'installs' / '.main-computer-install-archives' / 'main_computer_test-test-unleashed' / 'main_computer_test-test-unleashed-20260706-122941.moved'}",
            ]
        ),
        encoding="utf-8",
    )
    (logs / f"main-computer-python-installer-{stamp}.stderr.txt").write_text(
        (
            "RuntimeError: Archive verification failed; leaving existing install root in place: "
            "archive reports 10 uncompressed bytes, expected 11\n"
        ),
        encoding="utf-8",
    )

    context = module.discover_installer_context(package_root)
    assert context.log_path == logs / f"main-computer-python-installer-{stamp}.log"
    assert context.repo_root.endswith("payload/main_computer_test") or context.repo_root.endswith("payload\\main_computer_test")
    assert context.install_target.endswith("main_computer_test-test-unleashed")
    assert context.preserve_source.endswith("main_computer_test-test-unleashed")
    assert context.failure_detail == "archive reports 10 uncompressed bytes, expected 11"

    class Args:
        install_root = None
        mode = "unleashed"

    selected_root, source, warnings = module.resolve_requested_install_root(Args(), context)
    assert selected_root.name == "main_computer_test-test-unleashed"
    assert source == "latest installer stdout Source"
    assert warnings == []


def test_twiddle_warns_when_package_root_is_mistaken_for_managed_install_root(tmp_path: Path) -> None:
    module = load_twiddle_module()

    package_root = tmp_path / "Programs" / "Main Computer"
    payload_marker = package_root / "payload" / "main_computer_test" / "bootstrap-main-computer-python-windows.ps1"
    payload_marker.parent.mkdir(parents=True)
    payload_marker.write_text("# marker\n", encoding="utf-8")

    context = module.InstallerLogContext(
        package_root=package_root,
        logs_root=package_root / "logs",
        install_target=str(tmp_path / ".main-computer-tools" / "installs" / "main_computer_test-test-unleashed"),
    )

    warnings = module.maybe_warn_package_root_confusion(package_root, package_root, context)
    assert any("NSIS package/log root" in warning for warning in warnings)
    assert any("managed install slot" in warning for warning in warnings)
