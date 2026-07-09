#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class FileRecord:
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class InstallerLogContext:
    package_root: Path
    logs_root: Path
    log_path: Path | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    command_line: str = ""
    package_root_from_log: str = ""
    payload_root: str = ""
    repo_root: str = ""
    install_target: str = ""
    install_target_source: str = ""
    preserve_source: str = ""
    archive_path: str = ""
    moved_path: str = ""
    failure_detail: str = ""
    stderr_tail: str = ""


def windows_long_path(path: str | os.PathLike[str]) -> str:
    text = os.fspath(path)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\"):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def normalize_relative(path: Path) -> str:
    return os.fspath(path).replace("\\", "/").strip("/")


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def extract_log_block(text: str, title: str) -> str:
    lines = text.splitlines()
    wanted = f"[{title}]"
    capture: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == wanted:
            in_block = True
            continue
        if in_block and stripped.startswith("[") and stripped.endswith("]"):
            break
        if in_block:
            capture.append(line)
    return "\n".join(capture).strip()


def latest_log_path(logs_root: Path) -> Path | None:
    candidates = sorted(
        logs_root.glob("main-computer-python-installer-*.log"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    return candidates[0] if candidates else None


def sibling_log_paths(log_path: Path | None) -> tuple[Path | None, Path | None]:
    if log_path is None:
        return None, None
    stem = log_path.with_suffix("")
    return Path(os.fspath(stem) + ".stdout.txt"), Path(os.fspath(stem) + ".stderr.txt")


def first_match(patterns: list[str], *texts: str) -> str:
    for text in texts:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1).strip()
    return ""


def command_argument(command_line: str, switch: str) -> str:
    if not command_line:
        return ""
    pattern = rf"(?<!\S){re.escape(switch)}\s+(?:\"((?:\\\"|[^\"])*)\"|'([^']*)'|(\S+))"
    match = re.search(pattern, command_line, flags=re.IGNORECASE)
    if not match:
        return ""
    value = next((group for group in match.groups() if group is not None), "")
    return value.replace('\\"', '"').strip()


def tail_text(text: str, line_count: int = 30) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-line_count:]).strip()


def default_package_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Programs" / "Main Computer"
    return Path.home() / "AppData" / "Local" / "Programs" / "Main Computer"


def mode_key(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ").replace("-", " ")
    if normalized in {"debug"}:
        return "debug"
    if normalized in {"safe", "safe mode"}:
        return "safe"
    return "unleashed"


def default_managed_install_root(mode: str) -> Path:
    return Path.home() / ".main-computer-tools" / "installs" / f"main_computer_test-test-{mode_key(mode)}"


def discover_installer_context(package_root: Path, logs_root: Path | None = None) -> InstallerLogContext:
    logs = logs_root or (package_root / "logs")
    log_path = latest_log_path(logs)
    stdout_path, stderr_path = sibling_log_paths(log_path)

    log_text = read_text(log_path)
    stdout_text = read_text(stdout_path)
    stderr_text = read_text(stderr_path)
    command_line = extract_log_block(log_text, "command")

    package_root_from_log = first_match([r"^\s*Package root:\s*(.+?)\s*$"], log_text)
    payload_root = first_match([r"^\s*Payload root:\s*(.+?)\s*$"], log_text)
    repo_root = first_match([r"^\s*repo root:\s*(.+?)\s*$"], stdout_text)
    install_target = first_match(
        [
            r"^\s*install target:\s*(.+?)\s*$",
            r"^\s*Source:\s*(.+?)\s*$",
        ],
        stdout_text,
    )
    install_target_source = first_match([r"^\s*target source:\s*(.+?)\s*$"], stdout_text)
    preserve_source = first_match([r"^\s*Source:\s*(.+?)\s*$"], stdout_text)
    archive_path = first_match([r"^\s*Archive:\s*(.+?)\s*$"], stdout_text)
    moved_path = first_match([r"^\s*Move to:\s*(.+?)\s*$"], stdout_text)

    if not install_target:
        install_target = command_argument(command_line, "-InstallRoot")
    if not repo_root:
        repo_root = command_argument(command_line, "-RepoRoot")

    failure_detail = first_match(
        [
            r"Archive verification failed; leaving existing install root in place:\s*(.+?)\s*$",
            r"RuntimeError:\s*Archive verification failed; leaving existing install root in place:\s*(.+?)\s*$",
            r"\[FAIL\]\s*Archive verification failed; leaving existing install root in place:\s*(.+?)\s*$",
        ],
        stdout_text,
        stderr_text,
        log_text,
    )

    return InstallerLogContext(
        package_root=package_root,
        logs_root=logs,
        log_path=log_path,
        stdout_path=stdout_path if stdout_path and stdout_path.exists() else None,
        stderr_path=stderr_path if stderr_path and stderr_path.exists() else None,
        command_line=command_line,
        package_root_from_log=package_root_from_log,
        payload_root=payload_root,
        repo_root=repo_root,
        install_target=install_target,
        install_target_source=install_target_source,
        preserve_source=preserve_source,
        archive_path=archive_path,
        moved_path=moved_path,
        failure_detail=failure_detail,
        stderr_tail=tail_text(stderr_text),
    )


def scan_tree(root: Path) -> tuple[dict[str, FileRecord], list[str]]:
    records: dict[str, FileRecord] = {}
    errors: list[str] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        current_dir = Path(dirpath)
        for filename in filenames:
            path = current_dir / filename
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                relative = normalize_relative(path.relative_to(root))
                records[relative] = FileRecord(size=int(stat.st_size), mtime_ns=int(stat.st_mtime_ns))
            except Exception as exc:  # pragma: no cover - depends on live Windows file state
                errors.append(f"{path}: {exc}")
    return records, errors


def install_archive_root(destination_root: Path) -> Path:
    destination_root = destination_root.resolve()
    return destination_root.parent / ".main-computer-install-archives" / destination_root.name


def unique_probe_path(archive_root: Path, install_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    probe_root = archive_root / "twiddle-probes"
    probe_root.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f"-{attempt}"
        candidate = probe_root / f"{install_root.name}-{timestamp}{suffix}.zip"
        if not candidate.exists():
            return candidate
        attempt += 1


def write_probe_archive(source_root: Path, archive_path: Path) -> tuple[int, int]:
    source_root = source_root.resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    written_count = 0
    written_size = 0
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for dirpath, dirnames, filenames in os.walk(source_root):
            dirnames.sort()
            filenames.sort()
            current_dir = Path(dirpath)
            for filename in filenames:
                path = current_dir / filename
                if not path.is_file():
                    continue
                relative = normalize_relative(path.relative_to(source_root))
                size = int(path.stat().st_size)
                archive.write(windows_long_path(path), relative)
                written_count += 1
                written_size += size
    return written_count, written_size


def verify_archive(archive_path: Path, expected_file_count: int, expected_total_bytes: int) -> tuple[bool, str, dict[str, int]]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        entry_bytes = sum(int(info.file_size) for info in entries)
        details = {
            "entry_count": len(entries),
            "entry_bytes": entry_bytes,
            "stream_read_bytes": 0,
        }

        if len(entries) != expected_file_count:
            return (
                False,
                f"archive contains {len(entries)} file entries, expected {expected_file_count}",
                details,
            )

        if entry_bytes != expected_total_bytes:
            return (
                False,
                f"archive reports {entry_bytes} uncompressed bytes, expected {expected_total_bytes}",
                details,
            )

        read_bytes = 0
        for info in entries:
            with archive.open(info, "r") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    read_bytes += len(chunk)
        details["stream_read_bytes"] = read_bytes

        if read_bytes != expected_total_bytes:
            return (
                False,
                f"archive stream read returned {read_bytes} bytes, expected {expected_total_bytes}",
                details,
            )

    return True, f"verified {expected_file_count} files and {expected_total_bytes} bytes", details


def archive_records(archive_path: Path) -> dict[str, int]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        return {
            normalize_relative(Path(info.filename)): int(info.file_size)
            for info in archive.infolist()
            if not info.is_dir()
        }


def changed_records(before: dict[str, FileRecord], after: dict[str, FileRecord]) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    resized = [
        path
        for path in sorted(before_keys & after_keys)
        if before[path].size != after[path].size
    ]
    touched = [
        path
        for path in sorted(before_keys & after_keys)
        if before[path].mtime_ns != after[path].mtime_ns and before[path].size == after[path].size
    ]
    return {
        "deleted": sorted(before_keys - after_keys),
        "created": sorted(after_keys - before_keys),
        "resized": resized,
        "touched": touched,
    }


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def print_path_list(title: str, values: list[str], limit: int) -> None:
    print_section(title)
    if not values:
        print("  none")
        return
    for value in values[:limit]:
        print(f"  {value}")
    if len(values) > limit:
        print(f"  ... {len(values) - limit} more")


def watch_for_changes(root: Path, seconds: float, limit: int) -> dict[str, list[str]]:
    if seconds <= 0:
        return {"deleted": [], "created": [], "resized": [], "touched": []}
    before, _errors = scan_tree(root)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.time())))
    after, _errors = scan_tree(root)
    changes = changed_records(before, after)
    total = sum(len(items) for items in changes.values())
    print_section(f"Live-change watch ({seconds:g}s)")
    if total == 0:
        print("No file creates/deletes/resizes/touches observed during the watch window.")
    else:
        print(f"Observed {total} path-level changes while idle.")
        for key in ("deleted", "created", "resized", "touched"):
            print_path_list(f"Watch {key}", changes[key], limit)
    return changes


def resolve_requested_install_root(args: argparse.Namespace, context: InstallerLogContext) -> tuple[Path, str, list[str]]:
    warnings: list[str] = []
    if args.install_root:
        requested = Path(args.install_root).expanduser()
        source = "--install-root"
        log_root_text = context.preserve_source or context.install_target
        if log_root_text:
            try:
                log_root = Path(log_root_text)
                if requested.resolve() != log_root.resolve():
                    warnings.append(
                        "The supplied --install-root does not match the latest installer log. "
                        f"Latest failing source/install target: {log_root_text}"
                    )
            except Exception:
                pass
        return requested, source, warnings

    if context.preserve_source:
        return Path(context.preserve_source), "latest installer stdout Source", warnings

    if context.install_target:
        return Path(context.install_target), "latest installer command/install target", warnings

    default_root = default_managed_install_root(args.mode)
    warnings.append(
        "No latest installer install root was found in logs; using the NSIS v7 managed default "
        f"for mode '{mode_key(args.mode)}'."
    )
    return default_root, "computed NSIS v7 managed default", warnings


def maybe_warn_package_root_confusion(install_root: Path, package_root: Path, context: InstallerLogContext) -> list[str]:
    warnings: list[str] = []
    try:
        same = install_root.resolve() == package_root.resolve()
    except Exception:
        same = False
    payload_marker = package_root / "payload" / "main_computer_test" / "bootstrap-main-computer-python-windows.ps1"
    if same and payload_marker.exists():
        warnings.append(
            "This path is the NSIS package/log root, not the managed install slot that v7 normally archives. "
            "Run without --install-root so the twiddle can use the latest installer log, or pass the Source path "
            "printed in the installer stdout."
        )
    if context.install_target and not same:
        try:
            if Path(context.install_target).resolve() != package_root.resolve():
                warnings.append(
                    "Installer logs live under the package root, but the archived install root is separate: "
                    f"{context.install_target}"
                )
        except Exception:
            pass
    return warnings


def run_probe(install_root: Path, args: argparse.Namespace) -> dict[str, object]:
    report: dict[str, object] = {
        "install_root": os.fspath(install_root),
        "ok": False,
    }

    if not install_root.exists():
        report["error"] = f"Install root does not exist: {install_root}"
        print(report["error"])
        return report
    if not install_root.is_dir():
        report["error"] = f"Install root is not a directory: {install_root}"
        print(report["error"])
        return report

    print_section("Before archive scan")
    before, before_errors = scan_tree(install_root)
    expected_count = len(before)
    expected_bytes = sum(record.size for record in before.values())
    print(f"Expected files: {expected_count}")
    print(f"Expected bytes: {expected_bytes}")
    if before_errors:
        print(f"Scan errors: {len(before_errors)}")

    archive_root = install_archive_root(install_root)
    probe_archive = unique_probe_path(archive_root, install_root)

    print_section("Writing production-style probe archive")
    print(f"Probe archive: {probe_archive}")
    start = time.monotonic()
    try:
        written_count, written_size = write_probe_archive(install_root, probe_archive)
    except Exception as exc:
        report["error"] = f"Archive write failed: {exc}"
        print(report["error"])
        if probe_archive.exists() and not args.keep_archive:
            probe_archive.unlink(missing_ok=True)
        return report

    elapsed = round(time.monotonic() - start, 3)
    write_details = {
        "written_count": written_count,
        "written_size_after_write": written_size,
        "elapsed_seconds": elapsed,
    }
    print(json.dumps(write_details, indent=2, sort_keys=True))

    print_section("Production-style verification")
    try:
        ok, detail, verification_details = verify_archive(
            probe_archive,
            expected_file_count=expected_count,
            expected_total_bytes=expected_bytes,
        )
    except Exception as exc:
        ok = False
        detail = f"verification raised {exc.__class__.__name__}: {exc}"
        verification_details = {}
    print(f"PRODUCTION-STYLE RESULT: {'PASS' if ok else 'FAIL'}: {detail}")
    print(json.dumps(verification_details, indent=2, sort_keys=True))

    print_section("After archive scan")
    after, after_errors = scan_tree(install_root)
    current_count = len(after)
    current_bytes = sum(record.size for record in after.values())
    print(f"Current files: {current_count}")
    print(f"Current bytes: {current_bytes}")
    if after_errors:
        print(f"Scan errors: {len(after_errors)}")

    changes = changed_records(before, after)
    archived = archive_records(probe_archive) if probe_archive.exists() else {}
    before_sizes = {path: record.size for path, record in before.items()}
    archive_keys = set(archived)
    before_keys = set(before_sizes)
    missing_from_archive = sorted(before_keys - archive_keys)
    unexpected_in_archive = sorted(archive_keys - before_keys)
    archive_size_mismatches = sorted(
        path for path in (before_keys & archive_keys) if before_sizes[path] != archived[path]
    )

    print_section("Likely cause clues")
    print(f"Files deleted during archive:   {len(changes['deleted'])}")
    print(f"Files created during archive:   {len(changes['created'])}")
    print(f"Files resized during archive:   {len(changes['resized'])}")
    print(f"Files touched during archive:   {len(changes['touched'])}")
    print(f"Files missing from archive:     {len(missing_from_archive)}")
    print(f"Unexpected archive entries:     {len(unexpected_in_archive)}")
    print(f"Archive/source size mismatches: {len(archive_size_mismatches)}")

    limit = args.limit
    print_path_list("Deleted during archive", changes["deleted"], limit)
    print_path_list("Created during archive", changes["created"], limit)
    print_path_list("Resized during archive", changes["resized"], limit)
    print_path_list("Touched during archive", changes["touched"], limit)
    print_path_list("Missing from archive", missing_from_archive, limit)
    print_path_list("Unexpected archive entries", unexpected_in_archive, limit)
    print_path_list("Archive size mismatches", archive_size_mismatches, limit)

    watch_changes = watch_for_changes(install_root, args.watch_seconds, limit)

    if not args.keep_archive and probe_archive.exists():
        probe_archive.unlink(missing_ok=True)

    report.update(
        {
            "ok": ok,
            "detail": detail,
            "expected_count": expected_count,
            "expected_bytes": expected_bytes,
            "write_details": write_details,
            "verification_details": verification_details,
            "current_count": current_count,
            "current_bytes": current_bytes,
            "deleted_during_archive": changes["deleted"],
            "created_during_archive": changes["created"],
            "resized_during_archive": changes["resized"],
            "touched_during_archive": changes["touched"],
            "missing_from_archive": missing_from_archive,
            "unexpected_in_archive": unexpected_in_archive,
            "archive_size_mismatches": archive_size_mismatches,
            "watch_changes": watch_changes,
            "probe_archive": os.fspath(probe_archive) if probe_archive.exists() else "",
        }
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the Main Computer install-root archive verification failure by reading the "
            "latest NSIS installer logs, selecting the same install root the failing command used, "
            "and running a no-move production-style archive verification probe."
        )
    )
    parser.add_argument(
        "--package-root",
        type=Path,
        default=default_package_root(),
        help="NSIS package/log root. Default: %%LOCALAPPDATA%%\\Programs\\Main Computer",
    )
    parser.add_argument(
        "--logs-root",
        type=Path,
        help="Override installer logs directory. Default: <package-root>\\logs",
    )
    parser.add_argument(
        "--install-root",
        type=Path,
        help="Override the install root to probe. By default this is discovered from the latest installer log.",
    )
    parser.add_argument(
        "--mode",
        default="unleashed",
        help="Mode used only when no installer log is available: unleashed, debug, or safe.",
    )
    parser.add_argument(
        "--watch-seconds",
        type=float,
        default=5.0,
        help="After the archive probe, watch the selected install root for idle file changes.",
    )
    parser.add_argument("--limit", type=int, default=30, help="Maximum paths to print per clue section.")
    parser.add_argument("--keep-archive", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json-report", type=Path, help="Optional path to write the full JSON report.")
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Only print the latest installer command/log context and selected install root.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    package_root = args.package_root.expanduser()
    logs_root = args.logs_root.expanduser() if args.logs_root else package_root / "logs"
    context = discover_installer_context(package_root, logs_root)
    install_root, install_root_source, warnings = resolve_requested_install_root(args, context)
    warnings.extend(maybe_warn_package_root_confusion(install_root, package_root, context))

    print("Main Computer install-root archive verification twiddle")
    print(f"Package/log root: {package_root}")
    print(f"Logs root:        {logs_root}")
    print(f"Install root:     {install_root}")
    print(f"Install source:   {install_root_source}")
    print("Move step:        skipped intentionally")

    print_section("Latest installer log context")
    if context.log_path:
        print(f"Log:     {context.log_path}")
        print(f"Stdout:  {context.stdout_path or '(missing)'}")
        print(f"Stderr:  {context.stderr_path or '(missing)'}")
    else:
        print("No main-computer-python-installer-*.log file was found.")
    if context.command_line:
        print()
        print("Command line from wrapper log:")
        print(context.command_line)
    if context.package_root_from_log:
        print(f"Package root from log: {context.package_root_from_log}")
    if context.payload_root:
        print(f"Payload root from log: {context.payload_root}")
    if context.repo_root:
        print(f"Python repo root:      {context.repo_root}")
    if context.install_target:
        print(f"Python install target: {context.install_target}")
    if context.install_target_source:
        print(f"Target source:         {context.install_target_source}")
    if context.archive_path:
        print(f"Failed archive path:   {context.archive_path}")
    if context.moved_path:
        print(f"Failed moved path:     {context.moved_path}")
    if context.failure_detail:
        print()
        print(f"Failure detail from latest log: {context.failure_detail}")
    if context.stderr_tail and not context.failure_detail:
        print()
        print("Stderr tail:")
        print(context.stderr_tail)

    if warnings:
        print_section("Warnings")
        for warning in warnings:
            print(f"- {warning}")

    report: dict[str, object] = {
        "package_root": os.fspath(package_root),
        "logs_root": os.fspath(logs_root),
        "install_root": os.fspath(install_root),
        "install_root_source": install_root_source,
        "warnings": warnings,
        "log_context": {
            "log_path": os.fspath(context.log_path) if context.log_path else "",
            "stdout_path": os.fspath(context.stdout_path) if context.stdout_path else "",
            "stderr_path": os.fspath(context.stderr_path) if context.stderr_path else "",
            "command_line": context.command_line,
            "package_root_from_log": context.package_root_from_log,
            "payload_root": context.payload_root,
            "repo_root": context.repo_root,
            "install_target": context.install_target,
            "install_target_source": context.install_target_source,
            "preserve_source": context.preserve_source,
            "archive_path": context.archive_path,
            "moved_path": context.moved_path,
            "failure_detail": context.failure_detail,
        },
    }

    if not args.no_probe:
        probe_report = run_probe(install_root.expanduser(), args)
        report["probe"] = probe_report

        print_section("What this means")
        if probe_report.get("ok"):
            print(
                "The selected install root passed the same count/byte/stream verification now. "
                "That usually means the installer-time failure was transient or the latest log points "
                "to a different run than the one being diagnosed."
            )
        else:
            print(
                "The selected install root failed the no-move probe. The clue sections above show "
                "which files changed, disappeared, or differed from the archive while the archive was written."
            )
        if warnings:
            print(
                "Resolve the warnings first if they say the package root and the managed install root were confused."
            )

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print()
        print(f"JSON report written: {args.json_report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
