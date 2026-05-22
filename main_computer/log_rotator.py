from __future__ import annotations

import argparse
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SECONDS_PER_DAY = 24 * 60 * 60
LOG_FILE_SUFFIXES = {".log", ".jsonl", ".out", ".err", ".trace"}
PROJECT_MARKERS = ("pyproject.toml", "new_patch.py")
PROJECT_DIAGNOSTIC_ARTIFACT_DIR_NAMES = {
    "diagnostics_output",
    "harness_output",
}
PROJECT_DIAGNOSTIC_ARTIFACT_DIR_PREFIXES = (
    "diagnostics_output_",
    "harness_output_",
)
PROJECT_GENERATED_DIAGNOSTIC_DIR_PARTS = (
    ("generated_component_docs", "work"),
)
PROJECT_GENERATED_DOCUMENTATION_PLAN_DIR_PARTS = ("tools", "documentation")
PROJECT_GENERATED_DOCUMENTATION_PLAN_SUFFIXES = (".py", ".state.json")
PROJECT_GENERATED_DOCUMENTATION_REPORT_FILES = {
    ("generated_component_docs", "doc-build.json"),
    ("generated_component_docs", "doc-health.json"),
    ("generated_component_docs", "graph.json"),
}


@dataclass(frozen=True)
class RotatedLog:
    source: Path
    archive: Path
    size_bytes: int
    modified_at: float


@dataclass(frozen=True)
class RotationError:
    source: Path
    archive: Path
    message: str


@dataclass(frozen=True)
class RotationReport:
    log_root: Path
    archive_root: Path
    max_age_days: float
    cutoff_time: float
    scanned_files: int
    rotated: tuple[RotatedLog, ...]
    errors: tuple[RotationError, ...]

    @property
    def rotated_count(self) -> int:
        return len(self.rotated)

    @property
    def error_count(self) -> int:
        return len(self.errors)


@dataclass(frozen=True)
class LogScanPlan:
    root: Path
    archive_root: Path
    files: tuple[Path, ...]


def default_archive_root(log_root: Path) -> Path:
    """Return the default archive tree for a log directory.

    A log root of ``logs`` archives into ``../archive/logs``.
    """

    return log_root.parent / "archive" / "logs"


def _is_relative_to(path: Path, possible_parent: Path) -> bool:
    try:
        path.resolve().relative_to(possible_parent.resolve())
    except ValueError:
        return False
    return True


def _iter_regular_files(log_root: Path, archive_root: Path) -> Iterable[Path]:
    for path in sorted(log_root.rglob("*")):
        if not path.is_file():
            continue
        if _is_relative_to(path, archive_root):
            continue
        yield path


def _looks_like_default_logs_alias(log_root: Path | str) -> bool:
    normalized = str(log_root).replace("\\", "/").rstrip("/")
    return normalized in {"logs", "./logs"}


def _find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if not (candidate / "main_computer").is_dir():
            continue
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def _default_project_root() -> Path | None:
    return _find_project_root(Path.cwd()) or _find_project_root(Path(__file__).resolve())


def _is_diagnostic_artifact_dir(part: str) -> bool:
    return part in PROJECT_DIAGNOSTIC_ARTIFACT_DIR_NAMES or any(
        part.startswith(prefix) for prefix in PROJECT_DIAGNOSTIC_ARTIFACT_DIR_PREFIXES
    )


def _has_project_diagnostic_artifact_dir(lower_parts: tuple[str, ...]) -> bool:
    return bool(lower_parts[:-1]) and _is_diagnostic_artifact_dir(lower_parts[0])


def _has_generated_diagnostic_dir(lower_parts: tuple[str, ...]) -> bool:
    directory_parts = lower_parts[:-1]
    for wanted_parts in PROJECT_GENERATED_DIAGNOSTIC_DIR_PARTS:
        wanted_length = len(wanted_parts)
        for index in range(0, len(directory_parts) - wanted_length + 1):
            if directory_parts[index : index + wanted_length] == wanted_parts:
                return True
    return False


def _is_generated_documentation_plan(lower_parts: tuple[str, ...]) -> bool:
    if len(lower_parts) != 3:
        return False
    if lower_parts[:2] != PROJECT_GENERATED_DOCUMENTATION_PLAN_DIR_PARTS:
        return False
    name = lower_parts[-1]
    return name.startswith("plan-") and name.endswith(PROJECT_GENERATED_DOCUMENTATION_PLAN_SUFFIXES)


def _is_generated_documentation_report(lower_parts: tuple[str, ...]) -> bool:
    return lower_parts in PROJECT_GENERATED_DOCUMENTATION_REPORT_FILES


def _is_project_log_file(path: Path, project_root: Path) -> bool:
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return False

    lower_parts = tuple(part.lower() for part in relative.parts)
    skipped_dirs = {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "archive",
    }
    if any(part in skipped_dirs for part in lower_parts[:-1]):
        return False

    if _has_project_diagnostic_artifact_dir(lower_parts):
        return True

    if _has_generated_diagnostic_dir(lower_parts):
        return True

    if _is_generated_documentation_plan(lower_parts):
        return True

    if _is_generated_documentation_report(lower_parts):
        return True

    if path.suffix.lower() in LOG_FILE_SUFFIXES and "test_log_rotator.py" not in lower_parts:
        return True

    return "logs" in lower_parts[:-1]


def _iter_project_log_files(project_root: Path, archive_root: Path) -> Iterable[Path]:
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if _is_relative_to(path, archive_root):
            continue
        if _is_project_log_file(path, project_root):
            yield path


def _build_scan_plan(log_root: Path | str, archive_root: Path | str | None) -> LogScanPlan:
    requested_root = Path(log_root)
    resolved_root = requested_root.resolve()

    if resolved_root.exists():
        if not resolved_root.is_dir():
            raise NotADirectoryError(f"log root is not a directory: {resolved_root}")
        resolved_archive = (
            Path(archive_root).resolve()
            if archive_root is not None
            else default_archive_root(resolved_root).resolve()
        )
        return LogScanPlan(
            root=resolved_root,
            archive_root=resolved_archive,
            files=tuple(_iter_regular_files(resolved_root, resolved_archive)),
        )

    if _looks_like_default_logs_alias(log_root):
        project_root = _default_project_root()
        if project_root is not None:
            resolved_archive = (
                Path(archive_root).resolve()
                if archive_root is not None
                else default_archive_root(project_root / "logs").resolve()
            )
            return LogScanPlan(
                root=project_root,
                archive_root=resolved_archive,
                files=tuple(_iter_project_log_files(project_root, resolved_archive)),
            )

    raise FileNotFoundError(f"log root not found: {resolved_root}")


def _archive_path_for(source: Path, log_root: Path, archive_root: Path) -> Path:
    relative = source.relative_to(log_root)
    archived = archive_root / relative
    return archived.with_name(f"{archived.name}.zip")


def _write_single_file_zip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temp_handle:
            temp_name = temp_handle.name

        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(source, arcname=source.name)

        Path(temp_name).replace(destination)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def rotate_logs(
    log_root: Path | str = ".",
    *,
    archive_root: Path | str | None = None,
    max_age_days: float = 3.0,
    now: float | None = None,
    dry_run: bool = False,
) -> RotationReport:
    """Move old log files into an archive tree as one-file zip archives.

    Files are rotated when their modification time is older than
    ``max_age_days``.  Each file keeps its relative location below
    ``archive_root`` and gets ``.zip`` added to its filename.  For example,
    ``logs/app/server.log`` becomes ``archive/logs/app/server.log.zip`` when
    ``log_root`` is ``logs``.

    When the default ``logs`` directory is absent, the rotator treats ``logs``
    as this repository's project-log profile and rotates known project log
    files such as top-level ``*.log`` files and files below nested ``logs``
    directories.
    """

    if max_age_days < 0:
        raise ValueError("max_age_days must be greater than or equal to zero")

    plan = _build_scan_plan(log_root, archive_root)
    current_time = time.time() if now is None else now
    cutoff = current_time - (max_age_days * SECONDS_PER_DAY)

    scanned = 0
    rotated: list[RotatedLog] = []
    errors: list[RotationError] = []

    for source in plan.files:
        scanned += 1
        try:
            stat = source.stat()
        except OSError as exc:
            errors.append(RotationError(source=source, archive=plan.archive_root, message=str(exc)))
            continue

        if stat.st_mtime >= cutoff:
            continue

        destination = _archive_path_for(source, plan.root, plan.archive_root)
        if destination.exists():
            errors.append(
                RotationError(
                    source=source,
                    archive=destination,
                    message="archive file already exists; leaving source in place",
                )
            )
            continue

        if dry_run:
            rotated.append(
                RotatedLog(
                    source=source,
                    archive=destination,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                )
            )
            continue

        try:
            _write_single_file_zip(source, destination)
            source.unlink()
        except OSError as exc:
            errors.append(RotationError(source=source, archive=destination, message=str(exc)))
            continue

        rotated.append(
            RotatedLog(
                source=source,
                archive=destination,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
            )
        )

    return RotationReport(
        log_root=plan.root,
        archive_root=plan.archive_root,
        max_age_days=max_age_days,
        cutoff_time=cutoff,
        scanned_files=scanned,
        rotated=tuple(rotated),
        errors=tuple(errors),
    )


def selected_archive_root_from_args(args) -> str | Path | None:
    """Return the archive root chosen by CLI args.

    ``main-computer rotate-logs logs ../archive/logs`` and
    ``main-computer rotate-logs logs --archive-root ../archive/logs`` are both
    supported.  The named option wins if both are supplied.
    """

    option_value = getattr(args, "archive_root_option", None)
    if option_value is not None:
        return option_value
    return getattr(args, "archive_root", None)


def run_from_args(args) -> RotationReport:
    return rotate_logs(
        args.log_root,
        archive_root=selected_archive_root_from_args(args),
        max_age_days=args.max_age_days,
        dry_run=args.dry_run,
    )


def report_lines(report: RotationReport, *, dry_run: bool) -> list[str]:
    action = "Would rotate" if dry_run else "Rotated"
    lines = [
        f"{action} {report.rotated_count} of {report.scanned_files} scanned files older than {report.max_age_days:g} days",
        f"Log root: {report.log_root}",
        f"Archive root: {report.archive_root}",
    ]
    for item in report.rotated:
        lines.append(f"{item.source} -> {item.archive}")
    for error in report.errors:
        lines.append(f"error: {error.source} -> {error.archive}: {error.message}")
    return lines


def print_report(report: RotationReport, *, dry_run: bool) -> None:
    for line in report_lines(report, dry_run=dry_run):
        print(line)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m main_computer.log_rotator",
        description="Compress and move old log files into ../archive/logs by default.",
    )
    parser.add_argument(
        "log_root",
        nargs="?",
        default="logs",
        help="Current log directory to rotate. Defaults to logs.",
    )
    parser.add_argument(
        "archive_root",
        nargs="?",
        default=None,
        help="Optional archive root, for example ../archive/logs.",
    )
    parser.add_argument(
        "--archive-root",
        dest="archive_root_option",
        default=None,
        help="Archive root override. Defaults to ../archive/logs relative to log_root.",
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=3.0,
        help="Rotate files older than this many days. Defaults to 3.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show files that would rotate without moving them.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        report = run_from_args(args)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print_report(report, dry_run=args.dry_run)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
