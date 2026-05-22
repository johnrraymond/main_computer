#!/usr/bin/env python3
from __future__ import annotations

"""
Self-contained fail-closed smoke for the generated-editor sandbox.

This script intentionally does not read a prompt, a generated editor, or prior
debug artifacts from another run.  It embeds every generated-editor source and
evidence blob it needs.

It verifies the sandbox contract that matters before the generated-editor
pipeline is allowed to move closer to real repo writes:

- a normal pathlib.Path read/write editor can change an authorized in-memory file;
- unauthorized reads/writes are rejected;
- traversal, ambiguous basename paths, and absolute paths outside the repo fail closed;
- open(), os, and shutil style escape attempts are blocked before execution;
- invalid editor statuses and status/change mismatches fail the smoke;
- the real repository is not modified by any generated editor case.
"""

import ast
import datetime as _dt
import hashlib
import json
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT_NAME = "main_computer_test"
DEBUG_SUBDIR = Path("debug_assets") / "rga_sandbox_negative"

PRIMARY_TARGET = "main_computer/web/applications/generated_editor_sandbox_fixture.css"
PRIMARY_OLD_TEXT = ".stop-button {\n  background: green;\n}\n"
PRIMARY_NEW_TEXT = ".stop-button {\n  background: red;\n}\n"

SECONDARY_TARGET = "main_computer/web/other/generated_editor_sandbox_fixture.css"
SECONDARY_TEXT = ".other-button {\n  background: blue;\n}\n"

BLOCKED_IMPORT_ROOTS = {
    "builtins",
    "glob",
    "http",
    "importlib",
    "io",
    "os",
    "pathlib",  # pathlib itself is provided by the sandbox import hook only.
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "urllib",
}

ALLOWED_IMPORT_ROOTS = {"pathlib"}

BLOCKED_CALL_NAMES = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}

BLOCKED_ATTR_CALLS = {
    "chmod",
    "chown",
    "copy",
    "copy2",
    "copyfile",
    "glob",
    "iterdir",
    "link",
    "mkdir",
    "move",
    "open",
    "read_bytes",
    "remove",
    "rename",
    "resolve",
    "rglob",
    "rmdir",
    "rmtree",
    "symlink",
    "unlink",
    "walk",
    "write_bytes",
}


@dataclass(frozen=True)
class StaticIssue:
    kind: str
    detail: str
    lineno: int | None = None


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    evidence: dict[str, str]
    expect_ok: bool
    expect_issue_kinds: tuple[str, ...] = ()
    expect_changed_paths: tuple[str, ...] = ()


class SandboxViolation(RuntimeError):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def repo_root_from_this_file() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "main_computer").is_dir() and (parent / "new_patch.py").exists():
            return parent
    # The script is intended to live in main_computer/. Falling back to the
    # parent of that directory keeps local copies runnable during development.
    if here.parent.name == "main_computer":
        return here.parent.parent
    raise SystemExit("could not find repo root containing main_computer/ and new_patch.py")


def norm_slash(value: str) -> str:
    return value.replace("\\", "/")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_existing_repo_paths(repo_root: Path, paths: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for rel in sorted(set(paths)):
        candidate = repo_root / rel
        if candidate.exists() and candidate.is_file():
            result[rel] = sha256_bytes(candidate.read_bytes())
        else:
            result[rel] = None
    return result


def static_preflight(source: str) -> list[StaticIssue]:
    issues: list[StaticIssue] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [StaticIssue("syntax_error", str(exc), exc.lineno)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in BLOCKED_IMPORT_ROOTS and root not in ALLOWED_IMPORT_ROOTS:
                    issues.append(StaticIssue("blocked_import", root, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in BLOCKED_IMPORT_ROOTS and root not in ALLOWED_IMPORT_ROOTS:
                issues.append(StaticIssue("blocked_import", root, node.lineno))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALL_NAMES:
                issues.append(StaticIssue("blocked_call", func.id, node.lineno))
            elif isinstance(func, ast.Attribute) and func.attr in BLOCKED_ATTR_CALLS:
                issues.append(StaticIssue("blocked_attr_call", func.attr, node.lineno))

    return issues


class GeneratedEditorSandbox:
    def __init__(self, repo_root: Path, evidence: dict[str, str]) -> None:
        self.repo_root = repo_root.resolve()
        self.repo_root_name = self.repo_root.name or REPO_ROOT_NAME
        self.loaded: dict[str, str] = {
            self._canonical_loaded_path(path): text
            for path, text in evidence.items()
        }
        self.overlay: dict[str, str] = {}
        self.authorized_reads: list[str] = []
        self.authorized_writes: list[str] = []
        self.unauthorized_attempts: list[dict[str, str]] = []
        self.path_resolution_log: list[dict[str, str | None]] = []

    def _canonical_loaded_path(self, raw_path: str) -> str:
        normalized = norm_slash(str(raw_path)).strip()
        if not normalized:
            raise ValueError("empty evidence path")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            raise ValueError(f"unsafe embedded evidence path: {raw_path!r}")
        return pure.as_posix()

    def _record_attempt(self, operation: str, raw: str, kind: str, detail: str) -> None:
        self.unauthorized_attempts.append(
            {
                "operation": operation,
                "raw": raw,
                "kind": kind,
                "detail": detail,
            }
        )
        self.path_resolution_log.append(
            {
                "operation": operation,
                "raw": raw,
                "resolved": None,
                "reason": kind,
            }
        )

    def _resolve_path(self, raw_path: Any, operation: str) -> str:
        raw = norm_slash(str(raw_path)).strip()
        if not raw:
            self._record_attempt(operation, raw, "empty_path", "empty path")
            raise SandboxViolation("empty_path", operation)

        if "\x00" in raw:
            self._record_attempt(operation, raw, "nul_path", "NUL byte in path")
            raise SandboxViolation("nul_path", raw)

        is_windows_abs = bool(re.match(r"^[A-Za-z]:/", raw))
        is_posix_abs = raw.startswith("/")
        if is_windows_abs or is_posix_abs:
            marker = f"/{self.repo_root_name}/"
            comparable = raw
            if is_windows_abs:
                comparable = raw[2:]
            if marker in comparable:
                raw = comparable.split(marker, 1)[1]
            else:
                self._record_attempt(operation, raw, "absolute_outside_repo", "absolute path is outside repo")
                raise SandboxViolation("absolute_outside_repo", raw)

        pure = PurePosixPath(raw)
        parts = [part for part in pure.parts if part not in ("", ".")]
        if not parts:
            self._record_attempt(operation, raw, "empty_path", "empty normalized path")
            raise SandboxViolation("empty_path", raw)
        if any(part == ".." for part in parts):
            self._record_attempt(operation, raw, "path_traversal", "parent traversal is forbidden")
            raise SandboxViolation("path_traversal", raw)

        candidate = "/".join(parts)
        if parts and parts[0] == self.repo_root_name:
            candidate = "/".join(parts[1:])

        if candidate in self.loaded:
            self.path_resolution_log.append(
                {
                    "operation": operation,
                    "raw": raw,
                    "resolved": candidate,
                    "reason": "exact loaded path",
                }
            )
            return candidate

        suffix_matches = [path for path in self.loaded if path.endswith("/" + candidate)]
        if len(suffix_matches) == 1:
            resolved = suffix_matches[0]
            self.path_resolution_log.append(
                {
                    "operation": operation,
                    "raw": raw,
                    "resolved": resolved,
                    "reason": "unique loaded suffix",
                }
            )
            return resolved
        if len(suffix_matches) > 1:
            self._record_attempt(operation, raw, "ambiguous_path", "path matches multiple loaded evidence files")
            raise SandboxViolation("ambiguous_path", raw)

        kind = "unauthorized_write" if operation.startswith("write") else "unauthorized_read"
        self._record_attempt(operation, raw, kind, "path was not present in embedded evidence")
        raise SandboxViolation(kind, raw)

    def read_text(self, raw_path: Any, encoding: str = "utf-8", *args: Any, **kwargs: Any) -> str:
        del encoding, args, kwargs
        resolved = self._resolve_path(raw_path, "read_text")
        if resolved not in self.authorized_reads:
            self.authorized_reads.append(resolved)
        return self.overlay.get(resolved, self.loaded[resolved])

    def write_text(self, raw_path: Any, text: str, encoding: str = "utf-8", *args: Any, **kwargs: Any) -> int:
        del encoding, args, kwargs
        resolved = self._resolve_path(raw_path, "write_text")
        if resolved not in self.authorized_writes:
            self.authorized_writes.append(resolved)
        self.overlay[resolved] = str(text)
        return len(str(text))

    def changed_paths(self) -> list[str]:
        return sorted(path for path, text in self.overlay.items() if self.loaded.get(path) != text)

    def diffs(self) -> list[dict[str, str]]:
        import difflib

        result: list[dict[str, str]] = []
        for path in self.changed_paths():
            old_lines = self.loaded[path].splitlines(keepends=True)
            new_lines = self.overlay[path].splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="\n",
                )
            )
            result.append({"path": path, "diff": diff})
        return result

    def pathlib_module(self) -> types.ModuleType:
        sandbox = self

        class FakePath:
            def __init__(self, *parts: Any) -> None:
                joined = "/".join(norm_slash(str(part)).strip("/") for part in parts if str(part) != "")
                self._raw = joined or "."

            def __truediv__(self, child: Any) -> "FakePath":
                return FakePath(self._raw, child)

            def __fspath__(self) -> str:
                return self._raw

            def __str__(self) -> str:
                return self._raw

            def __repr__(self) -> str:
                return f"SandboxPath({self._raw!r})"

            @property
            def name(self) -> str:
                return PurePosixPath(norm_slash(self._raw)).name

            @property
            def suffix(self) -> str:
                return PurePosixPath(norm_slash(self._raw)).suffix

            def exists(self) -> bool:
                try:
                    sandbox._resolve_path(self._raw, "exists")
                except SandboxViolation:
                    return False
                return True

            def read_text(self, encoding: str = "utf-8", *args: Any, **kwargs: Any) -> str:
                return sandbox.read_text(self._raw, encoding=encoding, *args, **kwargs)

            def write_text(self, text: str, encoding: str = "utf-8", *args: Any, **kwargs: Any) -> int:
                return sandbox.write_text(self._raw, text, encoding=encoding, *args, **kwargs)

        module = types.ModuleType("pathlib")
        module.Path = FakePath
        module.PurePosixPath = PurePosixPath
        return module


def safe_import_factory(fake_pathlib: types.ModuleType):
    def safe_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        del globals, locals, fromlist, level
        root = name.split(".", 1)[0]
        if root == "pathlib":
            return fake_pathlib
        raise ImportError(f"import blocked by generated-editor sandbox: {name}")

    return safe_import


def run_case(repo_root: Path, case: Case) -> dict[str, Any]:
    before_hashes = hash_existing_repo_paths(repo_root, list(case.evidence))
    sandbox = GeneratedEditorSandbox(repo_root=repo_root, evidence=case.evidence)

    static_issues = static_preflight(case.source)
    result: dict[str, Any] = {
        "name": case.name,
        "expected_ok": case.expect_ok,
        "expected_issue_kinds": list(case.expect_issue_kinds),
        "expected_changed_paths": list(case.expect_changed_paths),
        "static_issues": [issue.__dict__ for issue in static_issues],
        "status": None,
        "exception": None,
        "issue_kinds": [],
        "authorized_reads": [],
        "authorized_writes": [],
        "unauthorized_attempts": [],
        "changed_paths": [],
        "diffs": [],
        "path_resolution_log": [],
        "real_repo_modified": None,
        "ok": False,
        "expectation_met": False,
    }

    issue_kinds: list[str] = []

    if static_issues:
        issue_kinds.extend(issue.kind for issue in static_issues)
    else:
        fake_pathlib = sandbox.pathlib_module()
        builtins_for_editor = {
            "__import__": safe_import_factory(fake_pathlib),
            "Exception": Exception,
            "False": False,
            "RuntimeError": RuntimeError,
            "True": True,
            "ValueError": ValueError,
            "dict": dict,
            "enumerate": enumerate,
            "len": len,
            "list": list,
            "print": print,
            "range": range,
            "repr": repr,
            "set": set,
            "str": str,
            "tuple": tuple,
        }
        globals_for_editor: dict[str, Any] = {
            "__builtins__": builtins_for_editor,
            "__name__": "__generated_editor__",
        }
        try:
            exec(compile(case.source, f"<{case.name}>", "exec"), globals_for_editor, globals_for_editor)
            main = globals_for_editor.get("main")
            if not callable(main):
                issue_kinds.append("missing_main")
            else:
                status = main()
                result["status"] = status
                if status not in {"done", "needs_more_evidence"}:
                    issue_kinds.append("invalid_status")
        except SandboxViolation as exc:
            result["exception"] = {"type": type(exc).__name__, "kind": exc.kind, "detail": exc.detail}
            issue_kinds.append(exc.kind)
        except Exception as exc:  # noqa: BLE001 - this smoke must report generated-editor failures.
            result["exception"] = {"type": type(exc).__name__, "detail": str(exc)}
            issue_kinds.append("execution_error")

    changed_paths = sandbox.changed_paths()
    if result["status"] == "done" and not changed_paths:
        issue_kinds.append("done_without_changes")
    if result["status"] == "needs_more_evidence" and changed_paths:
        issue_kinds.append("changes_with_needs_more_evidence")

    after_hashes = hash_existing_repo_paths(repo_root, list(case.evidence))
    real_repo_modified = before_hashes != after_hashes
    if real_repo_modified:
        issue_kinds.append("real_repo_modified")

    result.update(
        {
            "issue_kinds": sorted(set(issue_kinds)),
            "authorized_reads": sandbox.authorized_reads,
            "authorized_writes": sandbox.authorized_writes,
            "unauthorized_attempts": sandbox.unauthorized_attempts,
            "changed_paths": changed_paths,
            "diffs": sandbox.diffs(),
            "path_resolution_log": sandbox.path_resolution_log,
            "real_repo_modified": real_repo_modified,
        }
    )

    result["ok"] = not result["issue_kinds"] and not real_repo_modified
    expected_issue_set = set(case.expect_issue_kinds)
    actual_issue_set = set(result["issue_kinds"])
    expected_changed_set = set(case.expect_changed_paths)
    actual_changed_set = set(changed_paths)
    result["expectation_met"] = (
        result["ok"] is case.expect_ok
        and expected_issue_set.issubset(actual_issue_set)
        and expected_changed_set == actual_changed_set
    )
    return result


def happy_source() -> str:
    return f"""from pathlib import Path

TARGET = Path({PRIMARY_TARGET!r})
OLD = {PRIMARY_OLD_TEXT!r}
NEW = {PRIMARY_NEW_TEXT!r}

def main():
    text = TARGET.read_text(encoding="utf-8")
    if OLD not in text:
        return "needs_more_evidence"
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return "done"
"""


def unauthorized_read_source() -> str:
    return """from pathlib import Path

def main():
    Path("main_computer/private/secret.txt").read_text(encoding="utf-8")
    return "done"
"""


def unauthorized_write_source() -> str:
    return """from pathlib import Path

def main():
    Path("main_computer/private/secret.txt").write_text("stolen", encoding="utf-8")
    return "done"
"""


def traversal_source() -> str:
    return """from pathlib import Path

def main():
    Path("../outside_repo.txt").read_text(encoding="utf-8")
    return "done"
"""


def ambiguous_basename_source() -> str:
    return """from pathlib import Path

def main():
    Path("generated_editor_sandbox_fixture.css").read_text(encoding="utf-8")
    return "done"
"""


def blocked_open_source() -> str:
    return f"""def main():
    with open({PRIMARY_TARGET!r}, "r", encoding="utf-8") as handle:
        handle.read()
    return "done"
"""


def blocked_os_source() -> str:
    return """import os

def main():
    os.remove("main_computer/web/applications/generated_editor_sandbox_fixture.css")
    return "done"
"""


def blocked_shutil_source() -> str:
    return """import shutil

def main():
    shutil.rmtree("main_computer")
    return "done"
"""


def absolute_outside_repo_source() -> str:
    return """from pathlib import Path

def main():
    Path("C:/Windows/win.ini").read_text(encoding="utf-8")
    return "done"
"""


def wrong_status_source() -> str:
    return """from pathlib import Path

def main():
    Path("main_computer/web/applications/generated_editor_sandbox_fixture.css").read_text(encoding="utf-8")
    return "finished"
"""


def done_no_changes_source() -> str:
    return """from pathlib import Path

def main():
    Path("main_computer/web/applications/generated_editor_sandbox_fixture.css").read_text(encoding="utf-8")
    return "done"
"""


def needs_more_evidence_with_changes_source() -> str:
    return f"""from pathlib import Path

def main():
    target = Path({PRIMARY_TARGET!r})
    target.write_text({PRIMARY_NEW_TEXT!r}, encoding="utf-8")
    return "needs_more_evidence"
"""


def cases() -> list[Case]:
    primary = {PRIMARY_TARGET: PRIMARY_OLD_TEXT}
    ambiguous = {
        PRIMARY_TARGET: PRIMARY_OLD_TEXT,
        SECONDARY_TARGET: SECONDARY_TEXT,
    }
    return [
        Case(
            name="happy_path_authorized_pathlib_edit",
            source=happy_source(),
            evidence=primary,
            expect_ok=True,
            expect_changed_paths=(PRIMARY_TARGET,),
        ),
        Case(
            name="reject_unauthorized_read",
            source=unauthorized_read_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("unauthorized_read",),
        ),
        Case(
            name="reject_unauthorized_write",
            source=unauthorized_write_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("unauthorized_write",),
        ),
        Case(
            name="reject_parent_traversal",
            source=traversal_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("path_traversal",),
        ),
        Case(
            name="reject_ambiguous_basename",
            source=ambiguous_basename_source(),
            evidence=ambiguous,
            expect_ok=False,
            expect_issue_kinds=("ambiguous_path",),
        ),
        Case(
            name="block_builtin_open",
            source=blocked_open_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("blocked_call",),
        ),
        Case(
            name="block_os_import",
            source=blocked_os_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("blocked_import", "blocked_attr_call"),
        ),
        Case(
            name="block_shutil_import",
            source=blocked_shutil_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("blocked_import", "blocked_attr_call"),
        ),
        Case(
            name="reject_absolute_path_outside_repo",
            source=absolute_outside_repo_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("absolute_outside_repo",),
        ),
        Case(
            name="reject_wrong_status",
            source=wrong_status_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("invalid_status",),
        ),
        Case(
            name="reject_done_without_changes",
            source=done_no_changes_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("done_without_changes",),
        ),
        Case(
            name="reject_needs_more_evidence_with_changes",
            source=needs_more_evidence_with_changes_source(),
            evidence=primary,
            expect_ok=False,
            expect_issue_kinds=("changes_with_needs_more_evidence",),
            expect_changed_paths=(PRIMARY_TARGET,),
        ),
    ]


def write_debug_report(repo_root: Path, report: dict[str, Any]) -> Path:
    timestamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = repo_root / DEBUG_SUBDIR / f"rga_negative_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "generated_editor_sandbox_negative_smoke.json"
    report["run_dir"] = str(run_dir)
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    diff_chunks: list[str] = []
    for case_report in report["cases"]:
        for diff in case_report.get("diffs", []):
            diff_chunks.append(f"# case: {case_report['name']}\n{diff['diff']}")
    if diff_chunks:
        (run_dir / "generated_editor_sandbox_negative_smoke.patch").write_text(
            "\n".join(diff_chunks),
            encoding="utf-8",
        )
    return report_path


def main() -> int:
    repo_root = repo_root_from_this_file()
    case_reports = [run_case(repo_root, case) for case in cases()]
    passed = all(item["expectation_met"] for item in case_reports)
    report: dict[str, Any] = {
        "ok": passed,
        "mode": "embedded_generated_editor_sandbox_fail_closed_contract",
        "external_run_dependency": False,
        "fixture_mode": "embedded_negative_and_positive_fixtures",
        "repo_root": str(repo_root),
        "repo_root_name": repo_root.name,
        "case_count": len(case_reports),
        "passed_case_count": sum(1 for item in case_reports if item["expectation_met"]),
        "failed_case_count": sum(1 for item in case_reports if not item["expectation_met"]),
        "cases": case_reports,
    }
    report_path = write_debug_report(repo_root, report)

    if passed:
        print(f"[done] generated editor sandbox fail-closed smoke passed: {report_path}")
        summary = {
            "ok": True,
            "case_count": report["case_count"],
            "passed_case_count": report["passed_case_count"],
            "external_run_dependency": False,
            "report_path": str(report_path),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"[fail] generated editor sandbox fail-closed smoke failed: {report_path}", file=sys.stderr)
    failing = [
        {
            "name": item["name"],
            "issue_kinds": item["issue_kinds"],
            "expected_issue_kinds": item["expected_issue_kinds"],
            "ok": item["ok"],
            "expected_ok": item["expected_ok"],
            "changed_paths": item["changed_paths"],
            "expected_changed_paths": item["expected_changed_paths"],
        }
        for item in case_reports
        if not item["expectation_met"]
    ]
    print(json.dumps({"ok": False, "failing_cases": failing, "report_path": str(report_path)}, indent=2, sort_keys=True), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
