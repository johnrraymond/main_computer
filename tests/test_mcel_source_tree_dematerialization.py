from __future__ import annotations

from pathlib import Path

from main_computer.mcel_application_build import ensure_mcel_browser_build
from main_computer.mcel_application_packages import build_application_package_catalog


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMOTED_APPS = ("contract-counter", "contract-workbench")
GENERATED_SOURCE_NAMES = ("contracts", "generated", "mcel.generated.json")


def _package(catalog, app_id: str):
    return next(record for record in catalog.packages if record.app_id == app_id)


def _files_beneath(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def test_promoted_packages_contain_no_materialized_generated_source() -> None:
    for app_id in PROMOTED_APPS:
        root = REPO_ROOT / "mcel_apps" / app_id
        for name in GENERATED_SOURCE_NAMES:
            path = root / name
            assert not _files_beneath(path), f"generated source-tree artifact remains: {path}"


def test_checked_in_browser_projection_is_absent() -> None:
    assert not _files_beneath(REPO_ROOT / "main_computer/web/applications/mcel-packages")
    assert not (
        REPO_ROOT / "main_computer/web/applications/scripts/mcel-application-package-catalog.js"
    ).exists()


def test_duplicate_live_dsl_fixtures_are_absent() -> None:
    assert not (REPO_ROOT / "tests/fixtures/mcel_dsl/contract-counter.application.js").exists()
    assert not (REPO_ROOT / "tests/fixtures/mcel_dsl/contract-workbench.application.js").exists()


def test_package_catalog_reconstructs_generated_files_in_memory() -> None:
    catalog = build_application_package_catalog(REPO_ROOT)
    assert catalog.ok

    counter = _package(catalog, "contract-counter")
    workbench = _package(catalog, "contract-workbench")

    for record in (counter, workbench):
        assert record.files["mcel.generated.json"]
        for contract in (
            "acceptance",
            "adapter",
            "domain",
            "intents",
            "layout",
            "observation",
            "surface",
        ):
            assert record.files[f"contracts/{contract}.js"]

    assert "generated/mcel.application.normalized.json" not in counter.files
    assert workbench.files["generated/mcel.application.normalized.json"]


def test_runtime_build_is_ephemeral_and_does_not_repopulate_source_tree(tmp_path: Path) -> None:
    # The production helper writes beneath runtime/build. Its deterministic output
    # may be deleted and recreated without placing generated files in mcel_apps or
    # the checked-in browser source tree.
    runtime_root, catalog_path = ensure_mcel_browser_build(REPO_ROOT)
    assert runtime_root == REPO_ROOT / "runtime/build/mcel/web/applications/mcel-packages"
    assert catalog_path == (
        REPO_ROOT / "runtime/build/mcel/web/applications/scripts/mcel-application-package-catalog.js"
    )
    assert runtime_root.is_dir()
    assert catalog_path.is_file()
    assert (runtime_root / "contract-counter/contracts/domain.js").is_file()
    assert (runtime_root / "contract-workbench/contracts/domain.js").is_file()

    for app_id in PROMOTED_APPS:
        assert not _files_beneath(REPO_ROOT / "mcel_apps" / app_id / "contracts")
    assert not _files_beneath(REPO_ROOT / "main_computer/web/applications/mcel-packages")
