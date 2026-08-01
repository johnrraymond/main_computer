"""Build browser-safe runtime projections for validated MCEL application packages.

Wave 5A keeps canonical packages under the repository-root ``mcel_apps`` authority
and projects only the files required to mount an application in a browser.  The
projection is deterministic, contains source and catalog fingerprints, and never
copies requirements, tests, acceptance contracts, or other development-only
package contents into the served web tree. Operation-linked observation contracts
are browser-safe and are projected once they become executable.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from main_computer.mcel_application_packages import (
    ApplicationPackageCatalog,
    ApplicationPackageRecord,
    build_application_package_catalog,
    repository_root,
)


RUNTIME_PROJECTION_SCHEMA = "mcel.application-runtime-projection.v1"
RUNTIME_PROJECTION_RESULT_SCHEMA = "mcel.application-runtime-projection-result.v1"
RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM = "sha256-mcel-runtime-projection-v1"
DEFAULT_RUNTIME_PROJECTION_ROOT = "main_computer/web/applications/mcel-packages"
RUNTIME_MANIFEST_NAME = "mcel.runtime.json"

_BROWSER_CONTRACT_KEYS = ("domain", "intents", "adapter", "surface", "layout", "observation")
_BROWSER_RUNTIME_KEYS = ("document", "script", "style")


class RuntimeProjectionError(RuntimeError):
    exit_code = 5
    result_code = "runtime_projection_failed"


class InvalidRuntimeProjectionSource(RuntimeProjectionError):
    exit_code = 3
    result_code = "invalid_runtime_projection_source"


class StaleRuntimeProjection(RuntimeProjectionError):
    exit_code = 4
    result_code = "stale_runtime_projection"


class RuntimeProjectionWriteError(RuntimeProjectionError):
    exit_code = 5
    result_code = "runtime_projection_write_failed"


@dataclass(frozen=True)
class ApplicationRuntimeProjection:
    app_id: str
    title: str
    source_package_root: str
    source_package_fingerprint: str
    catalog_fingerprint: str
    projection_root: str
    manifest_path: str
    manifest_url: str
    document_url: str
    script_url: str
    style_url: str
    fingerprint: str
    fingerprint_algorithm: str
    files: Mapping[str, bytes]
    manifest: Mapping[str, Any]

    def browser_record(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_PROJECTION_SCHEMA,
            "root": self.projection_root,
            "manifest": self.manifest_path,
            "manifestUrl": self.manifest_url,
            "documentUrl": self.document_url,
            "scriptUrl": self.script_url,
            "styleUrl": self.style_url,
            "fingerprint": self.fingerprint,
            "fingerprintAlgorithm": self.fingerprint_algorithm,
            "fileCount": len(self.files),
        }


@dataclass(frozen=True)
class RuntimeProjectionSet:
    repository_root: str
    output_root: str
    catalog_fingerprint: str
    projections: tuple[ApplicationRuntimeProjection, ...]

    @property
    def package_count(self) -> int:
        return len(self.projections)


def default_runtime_projection_root(repo_root: Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else repository_root()
    return root / DEFAULT_RUNTIME_PROJECTION_ROOT


def _class_name(app_id: str) -> str:
    return "".join(part.capitalize() for part in app_id.split("-"))


def _hash_framed_items(marker: str, items: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(marker.encode("utf-8"))
    digest.update(b"\0")
    for name, content in items:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _repository_path(repo_root: Path, reference: str | None, label: str) -> Path:
    if not reference:
        raise InvalidRuntimeProjectionSource(f"Package is missing browser runtime reference: {label}.")
    path = repo_root / PurePosixPath(reference)
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise InvalidRuntimeProjectionSource(f"Package reference escapes repository root: {reference}.") from exc
    if not path.is_file() or path.is_symlink():
        raise InvalidRuntimeProjectionSource(f"Package browser runtime source is missing or unsafe: {reference}.")
    return path


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRuntimeProjectionSource(f"Could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidRuntimeProjectionSource(f"{label} must be a JSON object.")
    return value


def _copy_sources(repo_root: Path, record: ApplicationPackageRecord) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for key in _BROWSER_CONTRACT_KEYS:
        source = _repository_path(repo_root, record.contracts.get(key), f"contracts.{key}")
        files[f"contracts/{key}.js"] = source.read_bytes()
    for key in _BROWSER_RUNTIME_KEYS:
        source = _repository_path(repo_root, record.runtime.get(key), f"runtime.{key}")
        extension = {"document": "html", "script": "js", "style": "css"}[key]
        files[f"src/{'index' if key == 'document' else 'app'}.{extension}"] = source.read_bytes()
    return files


def build_application_runtime_projection(
    repo_root: Path,
    catalog: ApplicationPackageCatalog,
    record: ApplicationPackageRecord,
) -> ApplicationRuntimeProjection:
    if not catalog.ok or catalog.invalid_count or not record.valid or not record.app_id or not record.fingerprint:
        raise InvalidRuntimeProjectionSource("Only valid repository application packages may be projected.")

    blueprint = _read_json(_repository_path(repo_root, record.blueprint, "blueprint"), "application blueprint")
    root_selector = blueprint.get("rootSelector")
    if not isinstance(root_selector, str) or not root_selector.strip():
        raise InvalidRuntimeProjectionSource(f"Application {record.app_id} blueprint requires rootSelector.")

    copied = _copy_sources(repo_root, record)
    fingerprint_inputs = dict(copied)
    fingerprint_inputs["@source-package-fingerprint"] = record.fingerprint.encode("utf-8")
    fingerprint_inputs["@catalog-fingerprint"] = catalog.fingerprint.encode("utf-8")
    projection_fingerprint = _hash_framed_items(
        RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
        ((path, fingerprint_inputs[path]) for path in sorted(fingerprint_inputs)),
    )

    app_id = record.app_id
    class_name = _class_name(app_id)
    projection_root = PurePosixPath(DEFAULT_RUNTIME_PROJECTION_ROOT, app_id).as_posix()
    browser_root = PurePosixPath("applications/mcel-packages", app_id).as_posix()
    manifest: dict[str, Any] = {
        "schema": RUNTIME_PROJECTION_SCHEMA,
        "appId": app_id,
        "title": record.title or app_id,
        "source": {
            "packageRoot": record.package_root,
            "packageFingerprint": record.fingerprint,
            "packageFingerprintAlgorithm": record.fingerprint_algorithm,
            "catalogFingerprint": catalog.fingerprint,
            "catalogFingerprintAlgorithm": catalog.fingerprint_algorithm,
        },
        "projection": {
            "fingerprint": projection_fingerprint,
            "fingerprintAlgorithm": RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
            "fileCount": len(copied) + 1,
        },
        "surface": {
            "rootSelector": root_selector.strip(),
        },
        "modules": {
            "domain": {"path": "contracts/domain.js", "export": f"{class_name}Domain"},
            "intents": {"path": "contracts/intents.js", "export": f"{class_name}Intents"},
            "adapter": {"path": "contracts/adapter.js", "export": f"{class_name}Adapter"},
            "surface": {"path": "contracts/surface.js", "export": f"{class_name}Surface"},
            "layout": {"path": "contracts/layout.js", "export": f"{class_name}Layout"},
            "observation": {"path": "contracts/observation.js", "export": f"{class_name}Observation"},
        },
        "runtime": {
            "document": "src/index.html",
            "script": "src/app.js",
            "style": "src/app.css",
        },
        "conformance": {
            "currentMode": record.conformance.get("currentMode", "structural-only"),
            "targetMode": record.conformance.get("targetMode", "semantic-runtime-proven"),
            "missingBridges": list(record.conformance.get("missingBridges", [])),
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    files = {RUNTIME_MANIFEST_NAME: manifest_bytes, **copied}

    return ApplicationRuntimeProjection(
        app_id=app_id,
        title=record.title or app_id,
        source_package_root=record.package_root,
        source_package_fingerprint=record.fingerprint,
        catalog_fingerprint=catalog.fingerprint,
        projection_root=projection_root,
        manifest_path=PurePosixPath(projection_root, RUNTIME_MANIFEST_NAME).as_posix(),
        manifest_url=PurePosixPath(browser_root, RUNTIME_MANIFEST_NAME).as_posix(),
        document_url=PurePosixPath(browser_root, "src/index.html").as_posix(),
        script_url=PurePosixPath(browser_root, "src/app.js").as_posix(),
        style_url=PurePosixPath(browser_root, "src/app.css").as_posix(),
        fingerprint=projection_fingerprint,
        fingerprint_algorithm=RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
        files=files,
        manifest=manifest,
    )


def build_runtime_projection_set(repo_root: Path | None = None) -> RuntimeProjectionSet:
    root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    catalog = build_application_package_catalog(root)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise InvalidRuntimeProjectionSource("Repository application-package catalog must be valid before projection.")
    projections = tuple(
        build_application_runtime_projection(root, catalog, record)
        for record in sorted(catalog.packages, key=lambda item: item.app_id or item.package_root)
    )
    return RuntimeProjectionSet(
        repository_root=root.as_posix(),
        output_root=default_runtime_projection_root(root).relative_to(root).as_posix(),
        catalog_fingerprint=catalog.fingerprint,
        projections=projections,
    )


def _expected_tree(projection_set: RuntimeProjectionSet) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for projection in projection_set.projections:
        for relative, content in projection.files.items():
            files[PurePosixPath(projection.app_id, relative).as_posix()] = content
    return files


def _actual_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            files[f"@symlink:{path.relative_to(root).as_posix()}"] = b""
        elif path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def check_runtime_projections(
    repo_root: Path | None = None,
    *,
    output_root: Path | None = None,
) -> tuple[bool, Path, RuntimeProjectionSet]:
    root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    destination = Path(output_root) if output_root is not None else default_runtime_projection_root(root)
    projection_set = build_runtime_projection_set(root)
    return _actual_tree(destination) == _expected_tree(projection_set), destination, projection_set


def _publish_tree(destination: Path, expected: Mapping[str, bytes]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent))
    backup = destination.parent / f".{destination.name}.backup"
    try:
        for relative, content in expected.items():
            target = temporary / PurePosixPath(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.replace(backup)
        temporary.replace(destination)
        if backup.exists():
            shutil.rmtree(backup)
    except OSError as exc:
        if destination.exists() and backup.exists():
            shutil.rmtree(destination, ignore_errors=True)
            backup.replace(destination)
        elif not destination.exists() and backup.exists():
            backup.replace(destination)
        shutil.rmtree(temporary, ignore_errors=True)
        raise RuntimeProjectionWriteError(f"Could not publish MCEL runtime projections: {exc}") from exc


def write_runtime_projections(
    repo_root: Path | None = None,
    *,
    output_root: Path | None = None,
) -> tuple[Path, RuntimeProjectionSet, bool]:
    root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    destination = Path(output_root) if output_root is not None else default_runtime_projection_root(root)
    projection_set = build_runtime_projection_set(root)
    expected = _expected_tree(projection_set)
    changed = _actual_tree(destination) != expected
    if changed:
        _publish_tree(destination, expected)
    return destination, projection_set, changed
