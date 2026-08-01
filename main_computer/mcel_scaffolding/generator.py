"""Deterministic MCEL application scaffold generation.

Wave 2 intentionally stops at structural generation.  The generated package records the
future application-runtime bridges it requires, but the generator does not pretend those
bridges are live.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .package_validator import PackageValidationResult, validate_package_files, validate_package_path


TEMPLATE_ID = "mcel.canonical-application-template"
DEFAULT_TEMPLATE_VERSION = "1.0.0"
RESULT_SCHEMA = "mcel.create-app-result.v1"
DEFAULT_OUTPUT_DIRECTORY = "mcel_apps"
APP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
TARGET_GAPS = (
    "operation-linked-browser-observation",
    "app-oriented-proof-orchestration",
)


class McelScaffoldingError(RuntimeError):
    """Base error with a stable exit class and result code."""

    exit_code = 4
    result_code = "scaffolding_failed"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class InvalidScaffoldInput(McelScaffoldingError):
    exit_code = 2
    result_code = "invalid_input"


class UnsafeScaffoldDestination(McelScaffoldingError):
    exit_code = 3
    result_code = "unsafe_destination"


class ScaffoldValidationError(McelScaffoldingError):
    exit_code = 4
    result_code = "validation_failed"


class ScaffoldWriteError(McelScaffoldingError):
    exit_code = 5
    result_code = "write_failed"


@dataclass(frozen=True)
class TemplateFile:
    source: str
    target: str
    ownership: str


@dataclass(frozen=True)
class TemplateDefinition:
    template_id: str
    version: str
    files: tuple[TemplateFile, ...]


@dataclass(frozen=True)
class ScaffoldResult:
    ok: bool
    result_code: str
    app_id: str
    title: str
    template_id: str
    template_version: str
    output_root: str
    destination: str
    dry_run: bool
    created_files: tuple[str, ...]
    validation: PackageValidationResult
    target_gaps: tuple[str, ...] = TARGET_GAPS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESULT_SCHEMA,
            "ok": self.ok,
            "result_code": self.result_code,
            "app_id": self.app_id,
            "title": self.title,
            "template": {
                "id": self.template_id,
                "version": self.template_version,
            },
            "output_root": self.output_root,
            "destination": self.destination,
            "dry_run": self.dry_run,
            "created_files": list(self.created_files),
            "validation": self.validation.to_dict(),
            "target_gaps": list(self.target_gaps),
        }


TextWriter = Callable[[Path, str], None]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_output_root() -> Path:
    return repository_root() / DEFAULT_OUTPUT_DIRECTORY


def validate_app_id(app_id: str) -> str:
    value = str(app_id or "").strip()
    if not APP_ID_PATTERN.fullmatch(value):
        raise InvalidScaffoldInput(
            "Application id must match ^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$.",
            details={"app_id": value},
        )
    return value


def normalize_title(app_id: str, title: str | None) -> str:
    if title is None:
        return " ".join(part.capitalize() for part in app_id.split("-"))
    value = str(title).strip()
    if not value:
        raise InvalidScaffoldInput("Application title must not be empty.")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise InvalidScaffoldInput("Application title must be a single line without NUL characters.")
    if len(value) > 160:
        raise InvalidScaffoldInput("Application title must be 160 characters or fewer.")
    return value


def _safe_relative_path(raw: str, *, field: str) -> str:
    normalized = str(raw or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ScaffoldValidationError(
            f"Template {field} path is not a safe relative path: {raw!r}.",
            details={field: raw},
        )
    return path.as_posix()


def _template_root():
    return resources.files("main_computer.mcel_scaffolding").joinpath(
        "templates", "canonical_application_v1"
    )


def load_template_definition(version: str = DEFAULT_TEMPLATE_VERSION) -> TemplateDefinition:
    if version != DEFAULT_TEMPLATE_VERSION:
        raise InvalidScaffoldInput(
            f"Unsupported template version {version!r}; expected {DEFAULT_TEMPLATE_VERSION!r}.",
            details={"template_version": version},
        )

    root = _template_root()
    try:
        raw = root.joinpath("template.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaffoldValidationError(f"Could not load MCEL template metadata: {exc}") from exc

    if data.get("schema") != "mcel.application-template-source.v1":
        raise ScaffoldValidationError("Template metadata uses an unsupported schema.")
    if data.get("templateId") != TEMPLATE_ID or data.get("version") != version:
        raise ScaffoldValidationError("Template metadata identity does not match the requested template.")

    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ScaffoldValidationError("Template metadata must contain a non-empty files list.")

    files: list[TemplateFile] = []
    targets: set[str] = set()
    sources: set[str] = set()
    allowed_ownership = {"generator-owned", "user-owned", "mixed", "derived"}
    for item in raw_files:
        if not isinstance(item, dict):
            raise ScaffoldValidationError("Each template file entry must be an object.")
        source = _safe_relative_path(item.get("source", ""), field="source")
        target = _safe_relative_path(item.get("target", ""), field="target")
        ownership = str(item.get("ownership") or "")
        if ownership not in allowed_ownership:
            raise ScaffoldValidationError(f"Unknown template ownership class {ownership!r}.")
        if source in sources:
            raise ScaffoldValidationError(f"Duplicate template source {source!r}.")
        if target in targets:
            raise ScaffoldValidationError(f"Duplicate template target {target!r}.")
        if not root.joinpath(source).is_file():
            raise ScaffoldValidationError(f"Template source file does not exist: {source}.")
        sources.add(source)
        targets.add(target)
        files.append(TemplateFile(source=source, target=target, ownership=ownership))

    files.sort(key=lambda item: item.target)
    return TemplateDefinition(template_id=TEMPLATE_ID, version=version, files=tuple(files))


def _json_string_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_template_context(app_id: str, title: str) -> dict[str, str]:
    python_name = app_id.replace("-", "_")
    class_name = "".join(part.capitalize() for part in app_id.split("-"))
    return {
        "APP_ID": app_id,
        "APP_ID_JSON": _json_string_literal(app_id),
        "APP_TITLE": title,
        "APP_TITLE_HTML": html.escape(title, quote=True),
        "APP_TITLE_JSON": _json_string_literal(title),
        "APP_PY_NAME": python_name,
        "APP_CLASS_NAME": class_name,
        "TEMPLATE_ID": TEMPLATE_ID,
        "TEMPLATE_ID_JSON": _json_string_literal(TEMPLATE_ID),
        "TEMPLATE_VERSION": DEFAULT_TEMPLATE_VERSION,
        "TEMPLATE_VERSION_JSON": _json_string_literal(DEFAULT_TEMPLATE_VERSION),
    }


def render_template_text(text: str, context: Mapping[str, str], *, source: str) -> str:
    names = set(PLACEHOLDER_PATTERN.findall(text))
    unknown = sorted(names.difference(context))
    if unknown:
        raise ScaffoldValidationError(
            f"Template source {source!r} contains unknown placeholders: {', '.join(unknown)}."
        )

    rendered = PLACEHOLDER_PATTERN.sub(lambda match: context[match.group(1)], text)
    leftovers = sorted(set(PLACEHOLDER_PATTERN.findall(rendered)))
    if leftovers:
        raise ScaffoldValidationError(
            f"Template source {source!r} left unresolved placeholders: {', '.join(leftovers)}."
        )
    return rendered.replace("\r\n", "\n").replace("\r", "\n")


def render_package_files(
    app_id: str,
    title: str | None = None,
    *,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
) -> tuple[TemplateDefinition, str, dict[str, str]]:
    clean_app_id = validate_app_id(app_id)
    clean_title = normalize_title(clean_app_id, title)
    template = load_template_definition(template_version)
    context = build_template_context(clean_app_id, clean_title)
    root = _template_root()

    rendered: dict[str, str] = {}
    for item in template.files:
        source_text = root.joinpath(item.source).read_text(encoding="utf-8")
        rendered[item.target] = render_template_text(source_text, context, source=item.source)

    validation = validate_package_files(
        rendered,
        expected_app_id=clean_app_id,
        expected_title=clean_title,
        expected_template_id=template.template_id,
        expected_template_version=template.version,
    )
    if not validation.ok:
        raise ScaffoldValidationError(
            "Rendered MCEL application package failed structural validation.",
            details={"validation": validation.to_dict()},
        )
    return template, clean_title, rendered


def _write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(repository_root()).as_posix()
    except ValueError:
        return str(path)


def _resolve_output_root(output_root: str | Path | None) -> Path:
    if output_root is None:
        path = default_output_root()
    else:
        path = Path(output_root).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
    return path.resolve(strict=False)


def generate_application(
    app_id: str,
    *,
    title: str | None = None,
    output_root: str | Path | None = None,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    dry_run: bool = False,
    writer: TextWriter | None = None,
) -> ScaffoldResult:
    clean_app_id = validate_app_id(app_id)
    template, clean_title, files = render_package_files(
        clean_app_id,
        title,
        template_version=template_version,
    )
    destination_root = _resolve_output_root(output_root)
    destination = destination_root / clean_app_id

    if destination.exists() or destination.is_symlink():
        raise UnsafeScaffoldDestination(
            f"MCEL application destination already exists: {_display_path(destination)}.",
            details={"destination": str(destination)},
        )
    if destination_root.exists() and not destination_root.is_dir():
        raise UnsafeScaffoldDestination(
            f"MCEL application output root is not a directory: {_display_path(destination_root)}.",
            details={"output_root": str(destination_root)},
        )

    in_memory_validation = validate_package_files(
        files,
        expected_app_id=clean_app_id,
        expected_title=clean_title,
        expected_template_id=template.template_id,
        expected_template_version=template.version,
    )
    if not in_memory_validation.ok:
        raise ScaffoldValidationError(
            "Rendered package failed validation before write.",
            details={"validation": in_memory_validation.to_dict()},
        )

    created_files = tuple(sorted(files))
    if dry_run:
        return ScaffoldResult(
            ok=True,
            result_code="dry_run_valid",
            app_id=clean_app_id,
            title=clean_title,
            template_id=template.template_id,
            template_version=template.version,
            output_root=_display_path(destination_root),
            destination=_display_path(destination),
            dry_run=True,
            created_files=created_files,
            validation=in_memory_validation,
        )

    write_text = writer or _write_text_file
    root_created = False
    temp_path: Path | None = None
    try:
        if not destination_root.exists():
            destination_root.mkdir(parents=True, exist_ok=False)
            root_created = True
        temp_path = Path(
            tempfile.mkdtemp(prefix=f".{clean_app_id}.mcel-create-", dir=str(destination_root))
        )
        for relative_path in created_files:
            write_text(temp_path / relative_path, files[relative_path])

        disk_validation = validate_package_path(
            temp_path,
            expected_app_id=clean_app_id,
            expected_title=clean_title,
            expected_template_id=template.template_id,
            expected_template_version=template.version,
        )
        if not disk_validation.ok:
            raise ScaffoldValidationError(
                "Written package failed validation before atomic publication.",
                details={"validation": disk_validation.to_dict()},
            )

        os.rename(temp_path, destination)
        temp_path = None
        return ScaffoldResult(
            ok=True,
            result_code="generated",
            app_id=clean_app_id,
            title=clean_title,
            template_id=template.template_id,
            template_version=template.version,
            output_root=_display_path(destination_root),
            destination=_display_path(destination),
            dry_run=False,
            created_files=created_files,
            validation=disk_validation,
        )
    except McelScaffoldingError:
        raise
    except OSError as exc:
        raise ScaffoldWriteError(
            f"Could not create MCEL application {_display_path(destination)}: {exc}",
            details={"destination": str(destination), "error_type": type(exc).__name__},
        ) from exc
    except Exception as exc:
        raise ScaffoldWriteError(
            f"MCEL application generation failed: {exc}",
            details={"destination": str(destination), "error_type": type(exc).__name__},
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            shutil.rmtree(temp_path, ignore_errors=True)
        if root_created and destination_root.exists():
            try:
                destination_root.rmdir()
            except OSError:
                pass
