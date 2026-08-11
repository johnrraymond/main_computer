from __future__ import annotations

import shutil
from pathlib import Path

from main_computer.mcel_application_build import ensure_mcel_browser_build
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_virtual_assets import (
    CATALOG_ROUTE,
    build_virtual_mcel_browser_assets,
    normalize_mcel_asset_route,
)
from mcel_dsl_authoring_harness import discover_valid_application_package_cases


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_SOURCE_NAMES = ("contracts", "generated", "mcel.generated.json")


def _files_beneath(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def _runtime_asset_route(app_id: str, relative_path: str) -> str:
    return f"applications/mcel-packages/{app_id}/{relative_path}"


def test_authored_packages_contain_no_materialized_generated_source() -> None:
    for case in discover_valid_application_package_cases(REPO_ROOT):
        for name in GENERATED_SOURCE_NAMES:
            path = case.package_path / name
            assert not _files_beneath(path), f"generated source-tree artifact remains: {path}"


def test_checked_in_browser_projection_is_absent() -> None:
    assert not _files_beneath(REPO_ROOT / "main_computer/web/applications/mcel-packages")
    assert not (
        REPO_ROOT / "main_computer/web/applications/scripts/mcel-application-package-catalog.js"
    ).exists()


def test_duplicate_live_dsl_fixture_copies_are_absent() -> None:
    assert not _files_beneath(REPO_ROOT / "tests/fixtures/mcel_dsl")


def test_package_catalog_reconstructs_generated_files_in_memory() -> None:
    catalog = build_application_package_catalog(REPO_ROOT)
    assert catalog.ok
    cases = discover_valid_application_package_cases(REPO_ROOT)
    assert cases

    for case in cases:
        record = case.record
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

    assert any(
        "generated/mcel.application.normalized.json" in case.record.files
        for case in cases
    )


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

    cases = discover_valid_application_package_cases(REPO_ROOT)
    assert cases
    for case in cases:
        assert (runtime_root / case.app_id / "contracts/domain.js").is_file()
    assert any(not (runtime_root / case.app_id / "src").exists() for case in cases)

    virtual_assets = build_virtual_mcel_browser_assets(REPO_ROOT)
    physical_files = {
        CATALOG_ROUTE: catalog_path.read_bytes(),
    }
    for path in sorted(runtime_root.rglob("*")):
        if path.is_file():
            route = "applications/mcel-packages/" + path.relative_to(runtime_root).as_posix()
            physical_files[route] = path.read_bytes()
    assert physical_files == dict(virtual_assets.files)

    for case in cases:
        assert not _files_beneath(case.package_path / "contracts")
    assert not _files_beneath(REPO_ROOT / "main_computer/web/applications/mcel-packages")


def test_normal_viewport_mount_assets_stay_in_memory(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "mcel_apps", tmp_path / "mcel_apps")

    assets = build_virtual_mcel_browser_assets(tmp_path)
    cases = discover_valid_application_package_cases(tmp_path)
    assert cases

    assert assets.files[CATALOG_ROUTE].startswith(b"var McelApplicationPackages")
    for case in cases:
        assert assets.files[_runtime_asset_route(case.app_id, "contracts/domain.js")]
        assert assets.files[_runtime_asset_route(case.app_id, "mcel.runtime.json")]

    assert any(
        assets.files[_runtime_asset_route(case.app_id, "contracts/adapter.js")]
        for case in cases
    )
    assert not any(
        path.startswith(_runtime_asset_route(case.app_id, "src/"))
        for case in cases
        for path in assets.files
        if not case.record.runtime
    )
    assert not (tmp_path / "runtime").exists()


def test_normal_viewport_mount_path_has_no_materializing_build_dependency() -> None:
    pages = (REPO_ROOT / "main_computer/viewport_pages.py").read_text(encoding="utf-8")
    routes = (REPO_ROOT / "main_computer/viewport_routes_applications.py").read_text(encoding="utf-8")

    assert "read_virtual_mcel_browser_asset" in pages
    assert "read_virtual_mcel_browser_asset" in routes
    assert "ensure_mcel_browser_build" not in pages
    assert "ensure_mcel_browser_build" not in routes


def test_virtual_mount_asset_paths_fail_closed() -> None:
    assert (
        normalize_mcel_asset_route("/applications/scripts/mcel-application-package-catalog.js")
        == CATALOG_ROUTE
    )

    valid_case = discover_valid_application_package_cases(REPO_ROOT)[0]

    for path in (
        "",
        "/applications/mcel-packages/",
        f"/applications/mcel-packages/{valid_case.app_id}",
        "/applications/mcel-packages/../secret.txt",
        "/main_computer/web/applications/scripts/mcel-core.js",
    ):
        try:
            normalize_mcel_asset_route(path)
        except RuntimeError:
            continue
        raise AssertionError(f"unsafe virtual MCEL asset path was accepted: {path!r}")
