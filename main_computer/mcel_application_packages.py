"""Read-only repository authority for MCEL application packages.

Wave 3A discovers direct-child packages under ``mcel_apps/``, validates their
repository-bound contents, rejects ambiguous or unsafe identity, and emits a
deterministic catalog.  It does not execute package code, register browser
adapters, derive compatibility indexes, or promote application conformance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from main_computer.mcel_scaffolding.package_validator import (
    PackageValidationIssue,
    validate_package_files,
)


CATALOG_SCHEMA = "mcel.application-package-catalog.v1"
CATALOG_FORMAT = "mcel-application-packages-v1"
DEFAULT_PACKAGES_DIRECTORY = "mcel_apps"
PACKAGE_FINGERPRINT_ALGORITHM = "sha256-mcel-package-path-content-v1"
CATALOG_FINGERPRINT_ALGORITHM = "sha256-mcel-package-catalog-v1"
APP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
IGNORED_PACKAGE_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
IGNORED_PACKAGE_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})


@dataclass(frozen=True)
class ApplicationPackageIssue:
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True)
class ApplicationPackageRecord:
    directory_name: str
    app_id: str | None
    title: str | None
    package_root: str
    manifest: str
    requirements: str | None
    blueprint: str | None
    authoring: Mapping[str, str]
    contracts: Mapping[str, str]
    runtime: Mapping[str, str]
    tests_root: str | None
    acceptance_bindings: str | None
    template: Mapping[str, str]
    conformance: Mapping[str, Any]
    fingerprint: str | None
    fingerprint_algorithm: str
    file_count: int
    valid: bool
    errors: tuple[ApplicationPackageIssue, ...]
    warnings: tuple[ApplicationPackageIssue, ...]
    files: Mapping[str, bytes] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "directoryName": self.directory_name,
            "appId": self.app_id,
            "title": self.title,
            "packageRoot": self.package_root,
            "manifest": self.manifest,
            "requirements": self.requirements,
            "blueprint": self.blueprint,
            "authoring": dict(sorted(self.authoring.items())),
            "contracts": dict(sorted(self.contracts.items())),
            "runtime": dict(sorted(self.runtime.items())),
            "testsRoot": self.tests_root,
            "acceptanceBindings": self.acceptance_bindings,
            "template": dict(sorted(self.template.items())),
            "conformance": _canonical_value(self.conformance),
            "fingerprint": self.fingerprint,
            "fingerprintAlgorithm": self.fingerprint_algorithm,
            "fileCount": self.file_count,
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class ApplicationPackageCatalog:
    ok: bool
    repository_root: str
    packages_root: str
    package_count: int
    valid_count: int
    invalid_count: int
    fingerprint: str
    fingerprint_algorithm: str
    packages: tuple[ApplicationPackageRecord, ...]
    errors: tuple[ApplicationPackageIssue, ...]
    warnings: tuple[ApplicationPackageIssue, ...]
    files: Mapping[str, bytes] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA,
            "format": CATALOG_FORMAT,
            "ok": self.ok,
            "repositoryRoot": self.repository_root,
            "packagesRoot": self.packages_root,
            "packageCount": self.package_count,
            "validCount": self.valid_count,
            "invalidCount": self.invalid_count,
            "fingerprint": self.fingerprint,
            "fingerprintAlgorithm": self.fingerprint_algorithm,
            "packages": [record.to_dict() for record in self.packages],
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _repository_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_relative_path(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        return None
    return path.as_posix()


def _join_repository_reference(package_root: str, raw: Any) -> str | None:
    relative = _safe_relative_path(raw)
    if relative is None:
        return None
    return PurePosixPath(package_root, relative).as_posix()


def _hash_framed_items(
    algorithm_marker: str,
    items: Iterable[tuple[str, bytes]],
) -> str:
    digest = hashlib.sha256()
    digest.update(algorithm_marker.encode("utf-8"))
    digest.update(b"\0")
    for name, content in items:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def fingerprint_package_files(files: Mapping[str, bytes]) -> str:
    return _hash_framed_items(
        PACKAGE_FINGERPRINT_ALGORITHM,
        ((path, files[path]) for path in sorted(files)),
    )


def _catalog_fingerprint(records: Iterable[ApplicationPackageRecord]) -> str:
    items: list[tuple[str, bytes]] = []
    for record in records:
        payload = {
            "directoryName": record.directory_name,
            "appId": record.app_id,
            "packageRoot": record.package_root,
            "fingerprint": record.fingerprint,
            "valid": record.valid,
            "errors": [issue.to_dict() for issue in record.errors],
        }
        items.append(
            (
                record.package_root,
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            )
        )
    return _hash_framed_items(CATALOG_FINGERPRINT_ALGORITHM, items)


def _convert_validation_issue(issue: PackageValidationIssue) -> ApplicationPackageIssue:
    return ApplicationPackageIssue(issue.code, issue.message, issue.path)


def _read_package_files_safely(
    package_path: Path,
) -> tuple[dict[str, bytes], dict[str, str], list[ApplicationPackageIssue]]:
    byte_files: dict[str, bytes] = {}
    text_files: dict[str, str] = {}
    errors: list[ApplicationPackageIssue] = []

    if package_path.is_symlink():
        errors.append(
            ApplicationPackageIssue(
                "symlink-package-root",
                "Application package root must not be a symlink.",
                package_path.name,
            )
        )
        return byte_files, text_files, errors
    if not package_path.is_dir():
        errors.append(
            ApplicationPackageIssue(
                "invalid-package-root",
                "Application package candidate is not a directory.",
                package_path.name,
            )
        )
        return byte_files, text_files, errors

    for current, dirnames, filenames in os.walk(package_path, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for dirname in sorted(dirnames):
            child = current_path / dirname
            relative = child.relative_to(package_path).as_posix()
            if dirname in IGNORED_PACKAGE_DIRECTORY_NAMES:
                continue
            if child.is_symlink():
                errors.append(
                    ApplicationPackageIssue(
                        "symlink-package-entry",
                        "Application package contents must not contain symlinks.",
                        relative,
                    )
                )
                continue
            kept_directories.append(dirname)
        dirnames[:] = kept_directories

        for filename in sorted(filenames):
            path = current_path / filename
            relative = path.relative_to(package_path).as_posix()
            if path.suffix.lower() in IGNORED_PACKAGE_FILE_SUFFIXES:
                continue
            if path.is_symlink():
                errors.append(
                    ApplicationPackageIssue(
                        "symlink-package-entry",
                        "Application package contents must not contain symlinks.",
                        relative,
                    )
                )
                continue
            if not path.is_file():
                errors.append(
                    ApplicationPackageIssue(
                        "non-regular-package-entry",
                        "Application package contents must be regular files.",
                        relative,
                    )
                )
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                errors.append(
                    ApplicationPackageIssue(
                        "unreadable-package-file",
                        f"Could not read application package file: {exc}",
                        relative,
                    )
                )
                continue
            byte_files[relative] = content
            try:
                text_files[relative] = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(
                    ApplicationPackageIssue(
                        "non-utf8-package-file",
                        f"Application package files must be UTF-8 text at the current structural boundary: {exc}",
                        relative,
                    )
                )

    return byte_files, text_files, errors


def _load_json_text(
    text_files: Mapping[str, str],
    relative: str,
) -> Mapping[str, Any] | None:
    raw = text_files.get(relative)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _mapping_of_strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key] = item
    return result


def _build_record(
    repository: Path,
    package_path: Path,
) -> ApplicationPackageRecord:
    directory_name = package_path.name
    package_root = _repository_relative(package_path, repository)
    manifest_path = PurePosixPath(package_root, "mcel.app.json").as_posix()
    byte_files, text_files, read_errors = _read_package_files_safely(package_path)
    errors = list(read_errors)
    warnings: list[ApplicationPackageIssue] = []

    # DSL-authored packages keep only authored source in ``mcel_apps``. Build a
    # deterministic virtual overlay for promoted or explicitly shadowed package
    # compatibility artifacts before
    # package validation and fingerprinting. Existing generated source-tree files
    # are ignored so stale intermediates cannot become authority.
    try:
        from main_computer.mcel_application_materialization import (
            is_generated_source_tree_path,
            materialize_generated_package_files,
        )
        manifest_probe = _load_json_text(text_files, "mcel.app.json") or {}
        authoring_probe = manifest_probe.get("authoring") if isinstance(manifest_probe.get("authoring"), Mapping) else {}
        if authoring_probe.get("status") in {"dsl-authoritative", "dsl-shadow"}:
            byte_files = {path: content for path, content in byte_files.items() if not is_generated_source_tree_path(path)}
            text_files = {path: content for path, content in text_files.items() if not is_generated_source_tree_path(path)}
            generated = materialize_generated_package_files(repository, package_path, text_files)
            byte_files.update(generated)
            for path, content in generated.items():
                text_files[path] = content.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        errors.append(ApplicationPackageIssue(
            "generated-package-materialization-failed",
            f"Could not materialize DSL-authoritative package: {exc}",
            package_root,
        ))

    if not APP_ID_PATTERN.fullmatch(directory_name):
        errors.append(
            ApplicationPackageIssue(
                "invalid-package-directory-name",
                "Application package directory must use the canonical lowercase hyphenated app id form.",
                package_root,
            )
        )

    validation = validate_package_files(text_files, expected_app_id=directory_name)
    errors.extend(_convert_validation_issue(issue) for issue in validation.errors)
    warnings.extend(_convert_validation_issue(issue) for issue in validation.warnings)

    manifest = _load_json_text(text_files, "mcel.app.json") or {}
    blueprint = _load_json_text(text_files, "blueprint.json") or {}
    declared_app_id = manifest.get("appId") if isinstance(manifest.get("appId"), str) else None
    title = manifest.get("title") if isinstance(manifest.get("title"), str) else None

    if declared_app_id is not None and not APP_ID_PATTERN.fullmatch(declared_app_id):
        errors.append(
            ApplicationPackageIssue(
                "invalid-declared-app-id",
                "Manifest appId must use the canonical lowercase hyphenated form.",
                manifest_path,
            )
        )
    if declared_app_id is not None and declared_app_id != directory_name:
        errors.append(
            ApplicationPackageIssue(
                "package-directory-app-id-mismatch",
                "Package directory name must equal manifest appId.",
                manifest_path,
            )
        )
    if declared_app_id is not None and blueprint.get("appId") != declared_app_id:
        errors.append(
            ApplicationPackageIssue(
                "manifest-blueprint-app-id-mismatch",
                "Blueprint appId must equal manifest appId.",
                PurePosixPath(package_root, "blueprint.json").as_posix(),
            )
        )

    requirements = _join_repository_reference(package_root, manifest.get("requirements"))
    blueprint_path = _join_repository_reference(package_root, manifest.get("blueprint"))
    authoring = {
        key: reference
        for key, raw in sorted(_mapping_of_strings(manifest.get("authoring")).items())
        if key not in {"schema", "status"} and (reference := _join_repository_reference(package_root, raw)) is not None
    }
    contracts = {
        key: reference
        for key, raw in sorted(_mapping_of_strings(manifest.get("contracts")).items())
        if (reference := _join_repository_reference(package_root, raw)) is not None
    }
    runtime = {
        key: reference
        for key, raw in sorted(_mapping_of_strings(manifest.get("runtime")).items())
        if (reference := _join_repository_reference(package_root, raw)) is not None
    }
    tests = manifest.get("tests")
    tests_root = None
    acceptance_bindings = None
    if isinstance(tests, Mapping):
        tests_root = _join_repository_reference(package_root, tests.get("root"))
        acceptance_bindings = _join_repository_reference(package_root, tests.get("acceptanceBindings"))

    template = _mapping_of_strings(manifest.get("template"))
    conformance = manifest.get("conformance") if isinstance(manifest.get("conformance"), Mapping) else {}
    fingerprint = fingerprint_package_files(byte_files) if byte_files and not read_errors else None

    deduplicated_errors = _deduplicate_issues(errors)
    deduplicated_warnings = _deduplicate_issues(warnings)
    return ApplicationPackageRecord(
        directory_name=directory_name,
        app_id=declared_app_id,
        title=title,
        package_root=package_root,
        manifest=manifest_path,
        requirements=requirements,
        blueprint=blueprint_path,
        authoring=authoring,
        contracts=contracts,
        runtime=runtime,
        tests_root=tests_root,
        acceptance_bindings=acceptance_bindings,
        template=template,
        conformance=_canonical_value(conformance),
        fingerprint=fingerprint,
        fingerprint_algorithm=PACKAGE_FINGERPRINT_ALGORITHM,
        file_count=len(byte_files),
        valid=not deduplicated_errors,
        errors=deduplicated_errors,
        warnings=deduplicated_warnings,
        files=dict(byte_files),
    )


def _deduplicate_issues(
    issues: Iterable[ApplicationPackageIssue],
) -> tuple[ApplicationPackageIssue, ...]:
    unique: dict[tuple[str, str, str | None], ApplicationPackageIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.message, issue.path)] = issue
    return tuple(sorted(unique.values(), key=lambda item: (item.path or "", item.code, item.message)))


def _apply_duplicate_app_id_errors(
    records: tuple[ApplicationPackageRecord, ...],
) -> tuple[ApplicationPackageRecord, ...]:
    by_app_id: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        if record.app_id:
            by_app_id.setdefault(record.app_id, []).append(index)

    mutable = list(records)
    for app_id, indexes in sorted(by_app_id.items()):
        if len(indexes) < 2:
            continue
        roots = ", ".join(records[index].package_root for index in indexes)
        for index in indexes:
            issue = ApplicationPackageIssue(
                "duplicate-application-id",
                f"Application id {app_id!r} is declared by multiple package roots: {roots}.",
                records[index].manifest,
            )
            errors = _deduplicate_issues((*records[index].errors, issue))
            mutable[index] = replace(records[index], valid=False, errors=errors)
    return tuple(mutable)


def build_application_package_catalog(
    repo_root: Path | None = None,
    *,
    packages_directory: str = DEFAULT_PACKAGES_DIRECTORY,
) -> ApplicationPackageCatalog:
    requested_root = Path(repo_root) if repo_root is not None else repository_root()
    errors: list[ApplicationPackageIssue] = []
    warnings: list[ApplicationPackageIssue] = []

    try:
        repository = requested_root.resolve(strict=True)
    except OSError as exc:
        issue = ApplicationPackageIssue(
            "invalid-repository-root",
            f"Repository root cannot be resolved: {exc}",
            str(requested_root),
        )
        return ApplicationPackageCatalog(
            ok=False,
            repository_root=".",
            packages_root=packages_directory,
            package_count=0,
            valid_count=0,
            invalid_count=0,
            fingerprint=_catalog_fingerprint(()),
            fingerprint_algorithm=CATALOG_FINGERPRINT_ALGORITHM,
            packages=(),
            errors=(issue,),
            warnings=(),
        )

    if not repository.is_dir():
        errors.append(
            ApplicationPackageIssue(
                "invalid-repository-root",
                "Repository root is not a directory.",
                str(requested_root),
            )
        )

    safe_packages_directory = _safe_relative_path(packages_directory)
    if safe_packages_directory is None or "/" in safe_packages_directory:
        errors.append(
            ApplicationPackageIssue(
                "unsafe-packages-directory",
                "Packages directory must be one safe repository-root child name.",
                packages_directory,
            )
        )
        safe_packages_directory = DEFAULT_PACKAGES_DIRECTORY

    packages_root_path = repository / safe_packages_directory
    if packages_root_path.is_symlink():
        errors.append(
            ApplicationPackageIssue(
                "symlink-packages-root",
                "The repository MCEL packages root must not be a symlink.",
                safe_packages_directory,
            )
        )
    if not packages_root_path.is_dir():
        errors.append(
            ApplicationPackageIssue(
                "missing-packages-root",
                "The repository MCEL packages root does not exist or is not a directory.",
                safe_packages_directory,
            )
        )

    records: tuple[ApplicationPackageRecord, ...] = ()
    if not errors:
        candidates: list[Path] = []
        for child in sorted(packages_root_path.iterdir(), key=lambda item: item.name):
            if child.name.startswith("."):
                continue
            if child.is_symlink():
                candidates.append(child)
                continue
            if child.is_dir():
                candidates.append(child)
                continue
            warnings.append(
                ApplicationPackageIssue(
                    "ignored-non-package-entry",
                    "Non-directory entry under mcel_apps is not an application package and was ignored.",
                    _repository_relative(child, repository),
                )
            )
        records = tuple(_build_record(repository, candidate) for candidate in candidates)
        records = _apply_duplicate_app_id_errors(records)

    valid_count = sum(1 for record in records if record.valid)
    invalid_count = len(records) - valid_count
    catalog_errors = _deduplicate_issues(errors)
    catalog_warnings = _deduplicate_issues(warnings)
    fingerprint = _catalog_fingerprint(records)
    return ApplicationPackageCatalog(
        ok=not catalog_errors and invalid_count == 0,
        repository_root=".",
        packages_root=safe_packages_directory,
        package_count=len(records),
        valid_count=valid_count,
        invalid_count=invalid_count,
        fingerprint=fingerprint,
        fingerprint_algorithm=CATALOG_FINGERPRINT_ALGORITHM,
        packages=records,
        errors=catalog_errors,
        warnings=catalog_warnings,
    )
