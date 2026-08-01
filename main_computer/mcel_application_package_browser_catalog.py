"""Generate the browser-safe MCEL application-package catalog.

Wave 3B projects the validated, read-only repository package catalog into a
static browser artifact.  The artifact contains declarative package metadata
only.  It does not import or execute application code, register semantic
adapters, enroll surfaces, or change application conformance.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_application_packages import (
    ApplicationPackageCatalog,
    ApplicationPackageRecord,
    build_application_package_catalog,
    repository_root,
)


BROWSER_CATALOG_SCHEMA = "mcel.application-package-browser-catalog.v1"
BROWSER_CATALOG_FORMAT = "mcel-application-package-browser-catalog-v1"
BROWSER_CATALOG_RESULT_SCHEMA = "mcel.application-package-browser-catalog-result.v1"
DEFAULT_BROWSER_CATALOG_RELATIVE_PATH = (
    "main_computer/web/applications/scripts/mcel-application-package-catalog.js"
)


class BrowserCatalogError(RuntimeError):
    """Base failure with a stable command exit class."""

    exit_code = 5
    result_code = "browser_catalog_failed"


class InvalidRepositoryPackageCatalog(BrowserCatalogError):
    exit_code = 3
    result_code = "invalid_repository_package_catalog"


class StaleBrowserPackageCatalog(BrowserCatalogError):
    exit_code = 4
    result_code = "stale_browser_package_catalog"


class BrowserCatalogWriteError(BrowserCatalogError):
    exit_code = 5
    result_code = "browser_catalog_write_failed"


def default_browser_catalog_path(repo_root: Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else repository_root()
    return root / DEFAULT_BROWSER_CATALOG_RELATIVE_PATH


def _browser_record(record: ApplicationPackageRecord, runtime_projection: Mapping[str, Any]) -> dict[str, Any]:
    if not record.valid or not record.app_id or not record.fingerprint:
        raise InvalidRepositoryPackageCatalog(
            f"Application package {record.package_root!r} is not valid for browser projection."
        )
    return {
        "appId": record.app_id,
        "title": record.title,
        "packageRoot": record.package_root,
        "manifest": record.manifest,
        "requirements": record.requirements,
        "blueprint": record.blueprint,
        "authoring": dict(sorted(record.authoring.items())),
        "contracts": dict(sorted(record.contracts.items())),
        "runtime": dict(sorted(record.runtime.items())),
        "testsRoot": record.tests_root,
        "template": dict(sorted(record.template.items())),
        "conformance": json.loads(json.dumps(record.conformance, sort_keys=True)),
        "fingerprint": record.fingerprint,
        "fingerprintAlgorithm": record.fingerprint_algorithm,
        "fileCount": record.file_count,
        "runtimeProjection": dict(runtime_projection),
    }


def build_browser_catalog_payload(
    catalog: ApplicationPackageCatalog,
    runtime_projections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return deterministic browser-safe metadata from a valid repository catalog."""

    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise InvalidRepositoryPackageCatalog(
            "Repository application-package catalog must be valid before browser projection."
        )

    packages = [
        _browser_record(record, runtime_projections.get(record.app_id or "", {}))
        for record in catalog.packages
    ]
    if any(not package["runtimeProjection"] for package in packages):
        raise InvalidRepositoryPackageCatalog(
            "Every browser package record requires a validated runtime projection."
        )
    packages.sort(key=lambda package: package["appId"])
    return {
        "schema": BROWSER_CATALOG_SCHEMA,
        "format": BROWSER_CATALOG_FORMAT,
        "sourceSchema": catalog.to_dict()["schema"],
        "sourceFormat": catalog.to_dict()["format"],
        "catalogFingerprint": catalog.fingerprint,
        "catalogFingerprintAlgorithm": catalog.fingerprint_algorithm,
        "packageCount": len(packages),
        "packages": packages,
    }


def build_repository_browser_catalog_payload(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else repository_root()
    catalog = build_application_package_catalog(root)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise InvalidRepositoryPackageCatalog(
            "Repository application-package catalog must be valid before browser projection."
        )
    projection_set = build_runtime_projection_set(root)
    projections = {
        projection.app_id: projection.browser_record()
        for projection in projection_set.projections
    }
    if projection_set.catalog_fingerprint != catalog.fingerprint:
        raise InvalidRepositoryPackageCatalog(
            "Runtime projection catalog fingerprint does not match package authority."
        )
    return build_browser_catalog_payload(catalog, projections)


def render_browser_catalog_javascript(payload: Mapping[str, Any]) -> str:
    """Render a deterministic data-only browser module with read-only accessors."""

    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    return f'''var McelApplicationPackages = (() => {{
  "use strict";

  function clonePlain(value) {{
    if (value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(clonePlain);
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, clonePlain(entry)]));
  }}

  function deepFreeze(value) {{
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }}

  const PAYLOAD = deepFreeze({encoded});
  const PACKAGES_BY_ID = new Map(PAYLOAD.packages.map((record) => [record.appId, record]));

  function normalizeAppId(value) {{
    return String(value || "").trim();
  }}

  function getCatalog() {{
    return clonePlain(PAYLOAD);
  }}

  function listPackages() {{
    return PAYLOAD.packages.map(clonePlain);
  }}

  function getPackage(appId) {{
    const record = PACKAGES_BY_ID.get(normalizeAppId(appId));
    return record ? clonePlain(record) : null;
  }}

  function hasPackage(appId) {{
    return PACKAGES_BY_ID.has(normalizeAppId(appId));
  }}

  return Object.freeze({{
    SCHEMA: PAYLOAD.schema,
    FORMAT: PAYLOAD.format,
    catalogFingerprint: PAYLOAD.catalogFingerprint,
    catalogFingerprintAlgorithm: PAYLOAD.catalogFingerprintAlgorithm,
    packageCount: PAYLOAD.packageCount,
    getCatalog,
    listPackages,
    getPackage,
    hasPackage
  }});
}})();

if (typeof window !== "undefined") {{
  window.McelApplicationPackages = McelApplicationPackages;
}}

if (typeof module !== "undefined" && module.exports) {{
  module.exports = McelApplicationPackages;
}}
'''


def extract_browser_catalog_payload(javascript: str) -> dict[str, Any]:
    marker = "const PAYLOAD = deepFreeze("
    start = javascript.find(marker)
    if start < 0:
        raise ValueError("Could not find browser application-package catalog payload marker.")
    open_brace = javascript.find("{", start + len(marker))
    if open_brace < 0:
        raise ValueError("Could not find browser application-package catalog JSON object.")

    depth = 0
    in_string = False
    escaped = False
    end: int | None = None
    for index in range(open_brace, len(javascript)):
        char = javascript[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise ValueError("Browser application-package catalog payload is not balanced.")
    value = json.loads(javascript[open_brace:end])
    if not isinstance(value, dict):
        raise ValueError("Browser application-package catalog payload must be an object.")
    return value


def expected_browser_catalog_javascript(repo_root: Path | None = None) -> str:
    return render_browser_catalog_javascript(build_repository_browser_catalog_payload(repo_root))


def check_browser_catalog(
    repo_root: Path | None = None,
    *,
    output_path: Path | None = None,
) -> tuple[bool, Path, str]:
    root = Path(repo_root) if repo_root is not None else repository_root()
    destination = Path(output_path) if output_path is not None else default_browser_catalog_path(root)
    expected = expected_browser_catalog_javascript(root)
    try:
        actual = destination.read_text(encoding="utf-8")
    except OSError:
        return False, destination, expected
    return actual == expected, destination, expected


def write_browser_catalog(
    repo_root: Path | None = None,
    *,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    root = Path(repo_root) if repo_root is not None else repository_root()
    destination = Path(output_path) if output_path is not None else default_browser_catalog_path(root)
    payload = build_repository_browser_catalog_payload(root)
    rendered = render_browser_catalog_javascript(payload)

    try:
        current = destination.read_text(encoding="utf-8") if destination.exists() else None
    except OSError as exc:
        raise BrowserCatalogWriteError(f"Could not read browser catalog destination: {exc}") from exc
    changed = current != rendered
    if not changed:
        return destination, payload, False

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            text=True,
        )
        temporary_path = Path(raw_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise BrowserCatalogWriteError(f"Could not publish browser application-package catalog: {exc}") from exc

    return destination, payload, True
