"""In-memory MCEL browser assets for normal viewport mounting.

The authoritative DSL packages are compiled into canonical IR and projected into
browser-safe logical files without publishing a runtime/build tree. Explicit
build and proof commands continue to use :mod:`mcel_application_build` when a
disposable physical projection is required.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from main_computer.mcel_application_package_browser_catalog import (
    build_browser_catalog_payload,
    render_browser_catalog_javascript,
)
from main_computer.mcel_application_packages import (
    IGNORED_PACKAGE_DIRECTORY_NAMES,
    IGNORED_PACKAGE_FILE_SUFFIXES,
    ApplicationPackageCatalog,
    build_application_package_catalog,
    repository_root,
)
from main_computer.mcel_application_runtime_projection import (
    ApplicationRuntimeProjection,
    build_application_runtime_projection,
)


CATALOG_ROUTE = "applications/scripts/mcel-application-package-catalog.js"
PACKAGE_ROUTE_PREFIX = "applications/mcel-packages/"


class VirtualMcelAssetError(RuntimeError):
    """Raised when a requested logical MCEL browser asset is invalid or absent."""


@dataclass(frozen=True)
class VirtualMcelBrowserAssets:
    repository_root: str
    source_stamp: str
    catalog_fingerprint: str
    files: Mapping[str, bytes]

    def read(self, route_path: str) -> bytes:
        normalized = normalize_mcel_asset_route(route_path)
        try:
            return self.files[normalized]
        except KeyError as exc:
            raise VirtualMcelAssetError(f"MCEL browser asset does not exist: {normalized}") from exc


_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, VirtualMcelBrowserAssets] = {}


def normalize_mcel_asset_route(route_path: str) -> str:
    raw = str(route_path or "").replace("\\", "/").lstrip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise VirtualMcelAssetError("MCEL browser asset path is invalid.")
    normalized = path.as_posix()
    if normalized == CATALOG_ROUTE:
        return normalized
    if not normalized.startswith(PACKAGE_ROUTE_PREFIX):
        raise VirtualMcelAssetError("MCEL browser asset route is outside the virtual package boundary.")
    remainder = normalized.removeprefix(PACKAGE_ROUTE_PREFIX)
    remainder_path = PurePosixPath(remainder)
    if len(remainder_path.parts) < 2:
        raise VirtualMcelAssetError("MCEL package asset route must include an app id and file path.")
    return normalized


def _source_tree_stamp(repo: Path) -> str:
    """Return a deterministic cache invalidation stamp for authored MCEL packages."""

    packages_root = repo / "mcel_apps"
    digest = hashlib.sha256()
    digest.update(b"mcel-virtual-browser-assets-source-stamp-v1\0")
    if not packages_root.is_dir():
        digest.update(b"@missing")
        return "sha256:" + digest.hexdigest()

    candidates = []
    for path in packages_root.rglob("*"):
        if not path.is_file():
            continue
        relative_to_package = path.relative_to(packages_root)
        if any(part in IGNORED_PACKAGE_DIRECTORY_NAMES for part in relative_to_package.parts):
            continue
        if path.suffix.lower() in IGNORED_PACKAGE_FILE_SUFFIXES:
            continue
        candidates.append(path)

    for path in sorted(candidates):
        relative = path.relative_to(repo).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _runtime_projections(
    repo: Path,
    catalog: ApplicationPackageCatalog,
) -> tuple[ApplicationRuntimeProjection, ...]:
    return tuple(
        build_application_runtime_projection(repo, catalog, record)
        for record in sorted(catalog.packages, key=lambda item: item.app_id or item.package_root)
    )


def build_virtual_mcel_browser_assets(
    repo_root: Path | None = None,
    *,
    source_stamp: str | None = None,
) -> VirtualMcelBrowserAssets:
    repo = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise VirtualMcelAssetError(
            "Repository application-package catalog must be valid before virtual browser projection."
        )

    projections = _runtime_projections(repo, catalog)
    runtime_records = {projection.app_id: projection.browser_record() for projection in projections}
    payload = build_browser_catalog_payload(catalog, runtime_records)

    files: dict[str, bytes] = {
        CATALOG_ROUTE: render_browser_catalog_javascript(payload).encode("utf-8"),
    }
    for projection in projections:
        for relative, content in projection.files.items():
            route = PurePosixPath(PACKAGE_ROUTE_PREFIX, projection.app_id, relative).as_posix()
            files[route] = content

    return VirtualMcelBrowserAssets(
        repository_root=repo.as_posix(),
        source_stamp=source_stamp or _source_tree_stamp(repo),
        catalog_fingerprint=catalog.fingerprint,
        files=MappingProxyType(dict(sorted(files.items()))),
    )


def get_virtual_mcel_browser_assets(
    repo_root: Path | None = None,
) -> VirtualMcelBrowserAssets:
    repo = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    cache_key = repo.as_posix()
    source_stamp = _source_tree_stamp(repo)

    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None and cached.source_stamp == source_stamp:
            return cached

    built = build_virtual_mcel_browser_assets(repo, source_stamp=source_stamp)
    with _CACHE_LOCK:
        current = _CACHE.get(cache_key)
        if current is not None and current.source_stamp == source_stamp:
            return current
        _CACHE[cache_key] = built
        return built


def read_virtual_mcel_browser_asset(
    repo_root: Path | None,
    route_path: str,
) -> bytes:
    return get_virtual_mcel_browser_assets(repo_root).read(route_path)


def clear_virtual_mcel_browser_asset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
