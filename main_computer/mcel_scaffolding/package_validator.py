"""Structural validation for generated MCEL application packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


PACKAGE_SCHEMA = "mcel.application-package.v1"
BLUEPRINT_SCHEMA = "mcel.application-blueprint.v1"
REQUIRED_PACKAGE_FILES = (
    "mcel.app.json",
    "requirements.md",
    "blueprint.json",
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/adapter.js",
    "contracts/surface.js",
    "contracts/layout.js",
    "contracts/observation.js",
    "contracts/acceptance.js",
    "src/index.html",
    "src/app.js",
    "src/app.css",
    "tests/test_package.py",
    "tests/test_operations.py",
    "tests/test_surface.py",
    "tests/test_browser.py",
    "tests/test_truth.py",
)
REQUIRED_REQUIREMENTS_BLOCKS = {
    "mcel-app",
    "mcel-use-case",
    "mcel-region",
    "mcel-requirement",
    "mcel-intent",
    "mcel-acceptance",
    "mcel-finding",
}
FENCE_PATTERN = re.compile(r"```(mcel-[a-z-]+)\n(.*?)\n```", re.DOTALL)
ID_PATTERN = re.compile(r"^id:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PackageValidationIssue:
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class PackageValidationResult:
    ok: bool
    errors: tuple[PackageValidationIssue, ...]
    warnings: tuple[PackageValidationIssue, ...]
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "file_count": self.file_count,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def _safe_manifest_path(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return None
    return path.as_posix()


def _load_json(files: Mapping[str, str], path: str, errors: list[PackageValidationIssue]) -> Any:
    raw = files.get(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(
            PackageValidationIssue(
                code="invalid-json",
                message=f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                path=path,
            )
        )
        return None


def _manifest_references(manifest: Mapping[str, Any]) -> list[tuple[str, Any]]:
    references: list[tuple[str, Any]] = [
        ("requirements", manifest.get("requirements")),
        ("blueprint", manifest.get("blueprint")),
    ]
    for group_name in ("contracts", "runtime"):
        group = manifest.get(group_name)
        if isinstance(group, dict):
            for key, value in sorted(group.items()):
                references.append((f"{group_name}.{key}", value))
    tests = manifest.get("tests")
    if isinstance(tests, dict):
        references.append(("tests.root", tests.get("root")))
    return references


def validate_package_files(
    files: Mapping[str, str],
    *,
    expected_app_id: str | None = None,
    expected_title: str | None = None,
    expected_template_id: str | None = None,
    expected_template_version: str | None = None,
) -> PackageValidationResult:
    normalized = {str(path).replace("\\", "/"): text for path, text in files.items()}
    errors: list[PackageValidationIssue] = []
    warnings: list[PackageValidationIssue] = []

    for required_path in REQUIRED_PACKAGE_FILES:
        if required_path not in normalized:
            errors.append(
                PackageValidationIssue(
                    code="missing-required-file",
                    message="Generated package is missing a required file.",
                    path=required_path,
                )
            )

    for path, text in normalized.items():
        safe = _safe_manifest_path(path)
        if safe != path:
            errors.append(
                PackageValidationIssue(
                    code="unsafe-package-path",
                    message="Package file path is not a safe normalized relative path.",
                    path=path,
                )
            )
        if not isinstance(text, str):
            errors.append(
                PackageValidationIssue(
                    code="non-text-template-output",
                    message="Wave 2 template output must be UTF-8 text.",
                    path=path,
                )
            )
        elif "\r" in text:
            errors.append(
                PackageValidationIssue(
                    code="non-canonical-line-ending",
                    message="Generated text must use LF line endings.",
                    path=path,
                )
            )

    manifest = _load_json(normalized, "mcel.app.json", errors)
    if isinstance(manifest, dict):
        if manifest.get("schema") != PACKAGE_SCHEMA:
            errors.append(PackageValidationIssue("unsupported-package-schema", "Unsupported package schema.", "mcel.app.json"))
        if expected_app_id is not None and manifest.get("appId") != expected_app_id:
            errors.append(PackageValidationIssue("app-id-mismatch", "Manifest appId does not match generation input.", "mcel.app.json"))
        if expected_title is not None and manifest.get("title") != expected_title:
            errors.append(PackageValidationIssue("title-mismatch", "Manifest title does not match generation input.", "mcel.app.json"))

        template = manifest.get("template")
        if not isinstance(template, dict):
            errors.append(PackageValidationIssue("missing-template-identity", "Manifest template identity is missing.", "mcel.app.json"))
        else:
            if expected_template_id is not None and template.get("id") != expected_template_id:
                errors.append(PackageValidationIssue("template-id-mismatch", "Manifest template id does not match.", "mcel.app.json"))
            if expected_template_version is not None and template.get("version") != expected_template_version:
                errors.append(PackageValidationIssue("template-version-mismatch", "Manifest template version does not match.", "mcel.app.json"))

        for field, raw_path in _manifest_references(manifest):
            safe = _safe_manifest_path(raw_path)
            if safe is None:
                errors.append(PackageValidationIssue("unsafe-manifest-reference", f"Manifest reference {field} is invalid.", "mcel.app.json"))
                continue
            if field == "tests.root":
                prefix = safe.rstrip("/") + "/"
                if not any(path.startswith(prefix) for path in normalized):
                    errors.append(PackageValidationIssue("missing-manifest-reference", f"Manifest reference {field} does not resolve.", safe))
            elif safe not in normalized:
                errors.append(PackageValidationIssue("missing-manifest-reference", f"Manifest reference {field} does not resolve.", safe))

        conformance = manifest.get("conformance")
        if not isinstance(conformance, dict):
            errors.append(PackageValidationIssue("missing-conformance-contract", "Manifest conformance contract is missing.", "mcel.app.json"))
        else:
            if conformance.get("currentMode") != "structural-only":
                errors.append(PackageValidationIssue("invalid-current-mode", "Wave 2 generated packages must report structural-only current capability.", "mcel.app.json"))
            if conformance.get("targetMode") != "semantic-runtime-proven":
                errors.append(PackageValidationIssue("invalid-target-mode", "Generated package target mode must be semantic-runtime-proven.", "mcel.app.json"))
            gaps = conformance.get("missingBridges")
            if not isinstance(gaps, list) or not gaps:
                errors.append(PackageValidationIssue("missing-target-gaps", "Generated package must report unresolved target bridges.", "mcel.app.json"))

    blueprint = _load_json(normalized, "blueprint.json", errors)
    if isinstance(blueprint, dict):
        if blueprint.get("schema") != BLUEPRINT_SCHEMA:
            errors.append(PackageValidationIssue("unsupported-blueprint-schema", "Unsupported blueprint schema.", "blueprint.json"))
        if expected_app_id is not None and blueprint.get("appId") != expected_app_id:
            errors.append(PackageValidationIssue("blueprint-app-id-mismatch", "Blueprint appId does not match.", "blueprint.json"))
        if not isinstance(blueprint.get("layoutZones"), list) or not blueprint.get("layoutZones"):
            errors.append(PackageValidationIssue("missing-layout-zones", "Blueprint must declare layout zones.", "blueprint.json"))

    requirements = normalized.get("requirements.md", "")
    block_types: set[str] = set()
    block_ids: set[str] = set()
    for match in FENCE_PATTERN.finditer(requirements):
        block_types.add(match.group(1))
        id_match = ID_PATTERN.search(match.group(2))
        if id_match:
            block_id = id_match.group(1)
            if block_id in block_ids:
                errors.append(PackageValidationIssue("duplicate-requirements-id", f"Duplicate requirements id {block_id!r}.", "requirements.md"))
            block_ids.add(block_id)
    missing_blocks = sorted(REQUIRED_REQUIREMENTS_BLOCKS.difference(block_types))
    if missing_blocks:
        errors.append(
            PackageValidationIssue(
                "missing-requirements-family",
                f"Requirements file is missing block families: {', '.join(missing_blocks)}.",
                "requirements.md",
            )
        )

    return PackageValidationResult(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        file_count=len(normalized),
    )


def validate_package_path(
    root: Path,
    **expected: Any,
) -> PackageValidationResult:
    package_root = Path(root)
    files: dict[str, str] = {}
    if not package_root.is_dir():
        issue = PackageValidationIssue("missing-package-root", "Package root does not exist or is not a directory.", str(package_root))
        return PackageValidationResult(False, (issue,), (), 0)
    for path in sorted(package_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(package_root).as_posix()
            try:
                files[relative] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                issue = PackageValidationIssue("unreadable-package-file", f"Could not read package file: {exc}", relative)
                return PackageValidationResult(False, (issue,), (), len(files))
    return validate_package_files(files, **expected)
