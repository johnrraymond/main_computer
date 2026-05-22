#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import ast
import hashlib
import io
import json
import re
import sys
import time
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_FOCUSED_SOURCE_DIRS = ("main_computer/web/applications",)
PIPELINE_SANDBOX_SMOKE_APPLY_MARKER = "dynamic_receiver_runtime_sandbox_v4_20260510"

EMBEDDED_FIXTURE_TARGET_PATH = "main_computer/web/applications/generated_editor_sandbox_fixture.css"
EMBEDDED_FIXTURE_OLD_TEXT = ".stop-button {\n  background: green;\n}\n"
EMBEDDED_FIXTURE_NEW_TEXT = ".stop-button {\n  background: red;\n}\n"


def embedded_fixture_request(repo_root_name: str) -> str:
    evidence = [
        {
            "path": EMBEDDED_FIXTURE_TARGET_PATH,
            "text": EMBEDDED_FIXTURE_OLD_TEXT,
        }
    ]
    candidate_files = [EMBEDDED_FIXTURE_TARGET_PATH]
    return (
        "Prompt:\n<<<\n"
        "the stop button should be red not green\n"
        ">>>\n\n"
        "Repository root:\n<<<\n"
        f"{repo_root_name}\n"
        ">>>\n\n"
        "Candidate files:\n<<<\n"
        f"{candidate_files!r}\n"
        ">>>\n\n"
        "Evidence:\n<<<\n"
        f"{json.dumps(evidence, indent=2)}\n"
        ">>>\n\n"
        "Additional context:\n<<<\n"
        "This embedded fixture is intentionally self-contained. "
        "The generated editor may only replace the green stop-button background with red in the listed CSS file.\n"
        ">>>\n"
    )


def embedded_fixture_source() -> str:
    return (
        "from pathlib import Path\n"
        "\n"
        f"TARGET = Path({EMBEDDED_FIXTURE_TARGET_PATH!r})\n"
        f"OLD = {EMBEDDED_FIXTURE_OLD_TEXT!r}\n"
        f"NEW = {EMBEDDED_FIXTURE_NEW_TEXT!r}\n"
        "\n"
        "def main():\n"
        "    target = TARGET\n"
        "    text = target.read_text(encoding=\"utf-8\")\n"
        "    if OLD not in text:\n"
        "        return \"needs_more_evidence\"\n"
        "    updated = text.replace(OLD, NEW, 1)\n"
        "    target.write_text(updated, encoding=\"utf-8\")\n"
        "    return \"done\"\n"
    )


FILE_EXTS = (
    ".py", ".js", ".css", ".html", ".ts", ".tsx", ".jsx", ".json", ".md",
    ".yml", ".yaml", ".toml", ".ps1", ".txt",
)
PATH_METHODS = {"exists", "read_text", "write_text", "read_bytes", "write_bytes", "open"}
PATH_ROOT_NAMES = {"repo", "root", "repo_root", "project_root", "base", "base_dir", "workspace_root"}

BLOCKED_IMPORT_ROOTS = {
    "os", "sys", "subprocess", "socket", "requests", "shutil", "tempfile",
    "urllib", "http", "ftplib", "glob", "importlib", "builtins", "io",
}
BLOCKED_CALL_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "input", "globals",
    "locals", "vars", "dir", "setattr", "delattr", "getattr",
}
BLOCKED_ATTR_CALLS = {
    "system", "popen", "remove", "unlink", "rmdir", "mkdir", "rename",
    "chmod", "chown", "symlink", "link", "glob", "rglob", "iterdir",
    "walk", "copy", "copy2", "copyfile", "move", "rmtree", "write_bytes",
    "read_bytes",
}


@dataclass
class PathHit:
    raw: str
    resolved: str | None
    lineno: int
    reason: str
    source: str


@dataclass
class ReplaceHit:
    old: str
    new: str | None
    lineno: int
    evidence_backed: bool
    count_one: bool
    changed: bool


def repo_root_from(start: Path) -> Path:
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / "main_computer").is_dir() and (p / "new_patch.py").exists():
            return p
    raise SystemExit("could not find repo root from current directory; run from repo root or pass --repo")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()



def prompt_text_from_words(prompt_words: list[str]) -> str:
    return " ".join(str(part) for part in prompt_words).strip()




def is_infrastructure_path(path: str) -> bool:
    normalized = norm_slash(path).lower()
    name = normalized.rsplit("/", 1)[-1]
    if "/tests/" in f"/{normalized}/" or "/debug_assets/" in f"/{normalized}/":
        return True
    if any(marker in name for marker in ("smoke", "preflight", "hallucination_miner")):
        return True
    return False

def section(text: str, name: str) -> str:
    marker = f"{name}:\n<<<\n"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n>>>", start)
    if end < 0:
        return text[start:]
    return text[start:end]


def json_string_leaf_text(raw: str) -> str:
    if not raw.strip():
        return ""
    try:
        value = json.loads(raw)
    except Exception:
        return ""

    strings: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str):
                    strings.append(key)
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return "\n".join(strings)


def strip_line_number_prefixes(text: str) -> str:
    """Remove common evidence display prefixes like `930:` from each line."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        # Keep indentation after the line marker. This intentionally only strips at
        # the beginning of a line so JavaScript labels/object literals are not changed.
        out.append(re.sub(r"^\s*(?:L)?\d{1,7}\s*[:|]\s?", "", line))
    return "".join(out)


def evidence_text_variants(text: str) -> list[str]:
    variants: list[str] = []
    for item in (text, strip_line_number_prefixes(text)):
        if item and item not in variants:
            variants.append(item)
    return variants


def evidence_contains_text(evidence_text: str, needle: str) -> bool:
    if not needle:
        return False
    haystacks = evidence_text_variants(evidence_text)
    needles = evidence_text_variants(needle)
    return any(n in h for h in haystacks for n in needles if n)


def strip_module_level_main_calls(source: str) -> tuple[str, int]:
    """Remove top-level bare `main()` calls so the sandbox controls execution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, 0

    remove_lines: set[int] = set()
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if call.args or call.keywords:
            continue
        if not isinstance(call.func, ast.Name) or call.func.id != "main":
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", start)
        if start is None or end is None:
            continue
        remove_lines.update(range(start, end + 1))

    if not remove_lines:
        return source, 0

    kept = [
        line
        for lineno, line in enumerate(source.splitlines(), start=1)
        if lineno not in remove_lines
    ]
    return "\n".join(kept).rstrip() + "\n", len(remove_lines)


def normalize_generated_editor_status(status: str) -> str:
    low = status.strip().lower().replace("-", "_")
    if low in {"done", "success", "changed", "applied"}:
        return "done"
    if low in {"needs_more_evidence", "need_more_evidence", "no_change", "noop", "no_op", "path_not_found", "not_found"}:
        return "needs_more_evidence"
    return status


def evidence_text_from_request(request_text: str) -> str:
    evidence_raw = section(request_text, "Evidence")
    additional_context = section(request_text, "Additional context")
    parts: list[str] = []
    for part in (
        evidence_raw,
        json_string_leaf_text(evidence_raw),
        additional_context,
    ):
        for variant in evidence_text_variants(part):
            if variant and variant not in parts:
                parts.append(variant)
    return "\n".join(parts)


def norm_slash(s: str) -> str:
    return str(s).replace("\\", "/").strip().strip("\"'")


def clean_repo_path(raw: str, repo_root_name: str) -> str | None:
    s = norm_slash(raw)
    s = re.sub(r"^[A-Za-z]:", "", s)
    parts = [p for p in s.split("/") if p and p != "."]
    if not parts or ".." in parts:
        return None
    if repo_root_name in parts:
        parts = parts[parts.index(repo_root_name) + 1:]
        if not parts or ".." in parts:
            return None
    candidate = "/".join(parts)
    if not any(candidate.endswith(ext) for ext in FILE_EXTS):
        return None
    return candidate


def extract_candidate_paths(request_text: str, repo_root_name: str) -> set[str]:
    paths: set[str] = set()

    candidate_block = section(request_text, "Candidate files")
    if candidate_block.strip():
        try:
            value = ast.literal_eval(candidate_block.strip())
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    cleaned = clean_repo_path(str(item), repo_root_name)
                    if cleaned:
                        paths.add(cleaned)
        except Exception:
            pass

    path_re = re.compile(
        r"(?:[A-Za-z]:)?(?:[A-Za-z0-9_. -]+[\\/])+[A-Za-z0-9_. -]+"
        r"(?:\.py|\.js|\.css|\.html|\.ts|\.tsx|\.jsx|\.json|\.md|\.yml|\.yaml|\.toml|\.ps1|\.txt)"
    )
    for m in path_re.finditer(request_text):
        cleaned = clean_repo_path(m.group(0), repo_root_name)
        if cleaned:
            paths.add(cleaned)

    return paths


def resolve_ai_path(raw: str, loaded_paths: set[str], repo_root_name: str) -> tuple[str | None, str]:
    s = norm_slash(raw)
    s = re.sub(r"^[A-Za-z]:", "", s)
    parts = [p for p in s.split("/") if p and p != "."]

    if not parts:
        return None, "empty path"
    if repo_root_name in parts:
        parts = parts[parts.index(repo_root_name) + 1:]
    if not parts:
        return None, "path resolves only to repo root"
    if ".." in parts:
        return None, "path traversal is not authorized"

    candidate = "/".join(parts)
    if candidate in loaded_paths:
        return candidate, "exact loaded path"

    if len(parts) <= 1:
        return None, "basename-only path is not enough authority"

    matches = [
        p for p in loaded_paths
        if p.endswith("/" + candidate) or p == candidate or candidate.endswith("/" + p)
    ]
    if len(matches) == 1:
        return matches[0], "unique loaded suffix"
    if len(matches) > 1:
        return None, "ambiguous suffix"
    return None, "path is not loaded"


def has_comments(source: str) -> list[str]:
    issues: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                issues.append(f"comment at line {tok.start[0]}")
    except tokenize.TokenError as exc:
        issues.append(f"tokenize error: {exc}")
    return issues


def is_runner_if(node: ast.If) -> bool:
    def name_main(n: ast.AST) -> bool:
        return isinstance(n, ast.Name) and n.id == "__name__"

    def const_main(n: ast.AST) -> bool:
        return isinstance(n, ast.Constant) and n.value == "__main__"

    test = node.test
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1:
        return (name_main(test.left) and const_main(test.comparators[0])) or (
            const_main(test.left) and name_main(test.comparators[0])
        )
    return False


def annotation_is_path(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "Path"
    if isinstance(node, ast.Attribute):
        return node.attr == "Path"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith("Path") or node.value == "pathlib.Path"
    return False


def literal_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


def collect_string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            target: ast.Name | None = None
            value: ast.AST | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target = node.target
                value = node.value
            if target is None or value is None:
                continue
            val = literal_string(value, constants)
            if val is not None and constants.get(target.id) != val:
                constants[target.id] = val
                changed = True
    return constants


def contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def call_func_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
    else:
        func = node
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def looks_like_repo_root_source(node: ast.AST) -> bool:
    func_name = call_func_name(node)
    if func_name and ("repo" in func_name.lower() or "root" in func_name.lower()):
        return True
    return contains_name(node, "__file__")


def collect_root_names(tree: ast.AST) -> set[str]:
    names = set(PATH_ROOT_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in PATH_ROOT_NAMES or annotation_is_path(arg.annotation):
                    names.add(arg.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    low = target.id.lower()
                    if target.id in PATH_ROOT_NAMES or "repo" in low or "root" in low or low in {"base", "base_dir"}:
                        if looks_like_repo_root_source(node.value):
                            names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            low = node.target.id.lower()
            if annotation_is_path(node.annotation) or node.target.id in PATH_ROOT_NAMES or "repo" in low or "root" in low:
                if node.value is None or looks_like_repo_root_source(node.value):
                    names.add(node.target.id)
    return names


def static_path_expr(
    node: ast.AST,
    constants: dict[str, str],
    path_vars: dict[str, str],
    root_names: set[str],
) -> str | None:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = static_path_expr(node.left, constants, path_vars, root_names)
        right = static_path_expr(node.right, constants, path_vars, root_names) or literal_string(node.right, constants)
        if left is not None and right is not None:
            if left == "":
                return right.strip("/\\")
            if right == "":
                return left.strip("/\\")
            return left.rstrip("/\\") + "/" + right.strip("/\\")
        return None

    if isinstance(node, ast.Name):
        if node.id in path_vars:
            return path_vars[node.id]
        if node.id in root_names:
            return ""
        return literal_string(node, constants)

    if isinstance(node, ast.Call):
        func = node.func
        is_path = isinstance(func, ast.Name) and func.id == "Path"
        is_pathlib_path = (
            isinstance(func, ast.Attribute)
            and func.attr == "Path"
            and isinstance(func.value, ast.Name)
            and func.value.id == "pathlib"
        )
        if is_path or is_pathlib_path:
            pieces: list[str] = []
            for arg in node.args:
                val = literal_string(arg, constants)
                if val is None:
                    return None
                pieces.append(val)
            return "/".join(pieces)

    return literal_string(node, constants)


def is_path_candidate_text(value: str, target_name: str = "") -> bool:
    low_name = target_name.lower()
    if "/" in value or "\\" in value:
        return True
    if any(value.endswith(ext) for ext in FILE_EXTS):
        return True
    return any(word in low_name for word in ("path", "target", "file", "dir"))


def collect_path_vars(tree: ast.AST, constants: dict[str, str], root_names: set[str]) -> dict[str, str]:
    path_vars: dict[str, str] = {}
    changed = True
    passes = 0
    while changed and passes < 5:
        passes += 1
        changed = False
        for node in ast.walk(tree):
            targets: list[ast.Name] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
                value = node.value
            if value is None:
                continue
            val = static_path_expr(value, constants, path_vars, root_names)
            if val is None:
                continue
            for target in targets:
                if val == "":
                    root_names.add(target.id)
                    continue
                if not is_path_candidate_text(val, target.id):
                    continue
                if path_vars.get(target.id) != val:
                    path_vars[target.id] = val
                    changed = True
    return path_vars


def call_name(node: ast.Call) -> str | None:
    return call_func_name(node)


def node_kind(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def add_path_hit(
    *,
    raw: str,
    lineno: int,
    source: str,
    loaded_paths: set[str],
    repo_root_name: str,
    issues: list[str],
    path_hits: list[PathHit],
    seen: set[tuple[str, int, str]],
) -> None:
    key = (raw, lineno, source)
    if key in seen:
        return
    seen.add(key)
    resolved, reason = resolve_ai_path(raw, loaded_paths, repo_root_name)
    path_hits.append(PathHit(raw=raw, resolved=resolved, lineno=lineno, reason=reason, source=source))
    if resolved is None:
        issues.append(f"path at line {lineno} is not authorized: {raw!r} ({reason})")


def static_preflight(source: str, request_text: str, repo_root_name: str) -> dict[str, Any]:
    issues: list[str] = []
    path_hits: list[PathHit] = []
    replace_hits: list[ReplaceHit] = []
    seen_path_hits: set[tuple[str, int, str]] = set()
    path_method_call_counts: dict[str, int] = {name: 0 for name in sorted(PATH_METHODS)}
    return_literals: list[str] = []
    uses_pathlib = False
    runner_block_seen = False
    docstring_seen = False
    blocked_orchestration_seen = False

    comment_warnings = has_comments(source)
    runtime_enforced_notes: list[str] = []
    loaded_paths = extract_candidate_paths(request_text, repo_root_name)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "ok": False,
            "issues": [f"syntax error: {exc}"],
            "loaded_paths": sorted(loaded_paths),
            "path_hits": [],
            "replace_hits": [],
            "editor_contract": {
                "parses": False,
                "compiles": False,
                "ran_generated_editor": False,
            },
            "source_sha256": sha256_text(source),
        }

    compiles = True
    try:
        compile(source, "<generated_editor>", "exec")
    except SyntaxError as exc:
        compiles = False
        where = f" at line {exc.lineno}" if exc.lineno else ""
        issues.append(f"compile error: {exc.msg}{where}")

    if ast.get_docstring(tree):
        docstring_seen = True
        issues.append("module docstring is not allowed")

    main_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    main_ok = False
    if not main_defs:
        issues.append("missing top-level main()")
    elif len(main_defs) > 1:
        issues.append("multiple top-level main() definitions")
    else:
        args = main_defs[0].args
        argc = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
        if argc or args.vararg or args.kwarg:
            issues.append("main() must take no arguments")
        else:
            main_ok = True

    constants = collect_string_constants(tree)
    root_names = collect_root_names(tree)
    path_vars = collect_path_vars(tree, constants, root_names)
    infrastructure_paths = sorted(path for path in loaded_paths if is_infrastructure_path(path))
    evidence_text = evidence_text_from_request(request_text)

    anchor_checks: set[str] = set()

    def mark_noop(value: str) -> bool:
        low = value.strip().lower().replace("-", "_")
        return low in {"needs_more_evidence", "need_more_evidence", "no_op", "noop"} or "needs_more_evidence" in low

    def is_path_ctor(call: ast.Call) -> bool:
        func = call.func
        return (
            (isinstance(func, ast.Name) and func.id == "Path")
            or (
                isinstance(func, ast.Attribute)
                and func.attr == "Path"
                and isinstance(func.value, ast.Name)
                and func.value.id == "pathlib"
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "Path" and isinstance(node.value, ast.Name) and node.value.id == "pathlib":
            uses_pathlib = True

        if isinstance(node, ast.Name) and node.id == "Path":
            # This may be an imported pathlib.Path symbol or a local variable. Import checks below still decide safety.
            uses_pathlib = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node):
                docstring_seen = True
                issues.append(f"docstring is not allowed in {getattr(node, 'name', '<node>')} at line {node.lineno}")

        if isinstance(node, ast.If) and is_runner_if(node):
            runner_block_seen = True
            issues.append('if __name__ == "__main__" block is not allowed')

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "pathlib":
                    uses_pathlib = True
                if root not in {"pathlib", "__future__"}:
                    issues.append(f"disallowed import: {alias.name}")
                if root in BLOCKED_IMPORT_ROOTS:
                    blocked_orchestration_seen = True
                    issues.append(f"blocked import: {alias.name}")

        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "pathlib":
                uses_pathlib = True
                allowed = {alias.name for alias in node.names}
                if allowed != {"Path"}:
                    issues.append(f"from pathlib may only import Path, got: {sorted(allowed)}")
            elif root != "__future__":
                issues.append(f"disallowed import-from: {node.module}")
            if root in BLOCKED_IMPORT_ROOTS:
                blocked_orchestration_seen = True
                issues.append(f"blocked import-from: {node.module}")

        if isinstance(node, ast.Return) and node.value is not None:
            value = literal_string(node.value, constants)
            if value is not None:
                return_literals.append(value)

        if isinstance(node, ast.Call):
            cname = call_name(node)
            if cname in BLOCKED_CALL_NAMES:
                issues.append(f"disallowed call: {cname}() at line {node.lineno}")
            if isinstance(node.func, ast.Attribute) and cname in BLOCKED_ATTR_CALLS:
                issues.append(f"disallowed attribute call: {cname}() at line {node.lineno}")
            if cname == "print":
                issues.append(f"print() is not allowed at line {node.lineno}")

            if is_path_ctor(node):
                uses_pathlib = True
                raw_path = static_path_expr(node, constants, path_vars, root_names)
                if raw_path is not None and is_path_candidate_text(raw_path):
                    add_path_hit(
                        raw=raw_path,
                        lineno=node.lineno,
                        source="Path()",
                        loaded_paths=loaded_paths,
                        repo_root_name=repo_root_name,
                        issues=issues,
                        path_hits=path_hits,
                        seen=seen_path_hits,
                    )

            if isinstance(node.func, ast.Attribute) and node.func.attr in PATH_METHODS:
                method = node.func.attr
                path_method_call_counts[method] = path_method_call_counts.get(method, 0) + 1
                receiver_path = static_path_expr(node.func.value, constants, path_vars, root_names)
                if receiver_path is None or receiver_path == "":
                    runtime_enforced_notes.append(
                        f"{method}() receiver at line {node.lineno} is dynamic and will be authorized by the fake pathlib sandbox: "
                        f"{node_kind(node.func.value)}"
                    )
                else:
                    add_path_hit(
                        raw=receiver_path,
                        lineno=node.lineno,
                        source=f"{method}() receiver",
                        loaded_paths=loaded_paths,
                        repo_root_name=repo_root_name,
                        issues=issues,
                        path_hits=path_hits,
                        seen=seen_path_hits,
                    )

            if isinstance(node.func, ast.Attribute) and node.func.attr == "replace" and len(node.args) >= 2:
                old = literal_string(node.args[0], constants)
                new = literal_string(node.args[1], constants)
                if old is None:
                    issues.append(f"replace old text at line {node.lineno} is not a static string")
                    continue
                if new is None:
                    issues.append(f"replace new text at line {node.lineno} is not a static string")
                count_one = len(node.args) >= 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value == 1
                backed = evidence_contains_text(evidence_text, old)
                changed = new is not None and old != new
                replace_hits.append(ReplaceHit(
                    old=old,
                    new=new,
                    lineno=node.lineno,
                    evidence_backed=backed,
                    count_one=count_one,
                    changed=changed,
                ))
                if old == "":
                    issues.append(f"replace old text at line {node.lineno} must not be empty")
                if not backed:
                    issues.append(f"replace old text at line {node.lineno} is not present verbatim in evidence/additional context")
                if not count_one:
                    issues.append(f"replace at line {node.lineno} must use count=1")
                if not changed:
                    issues.append(f"replace at line {node.lineno} does not change the matched text")

        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotIn):
            left = literal_string(node.left, constants)
            if left is not None:
                anchor_checks.add(left)

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            raw_path = static_path_expr(node, constants, path_vars, root_names)
            if raw_path is not None and any(raw_path.endswith(ext) for ext in FILE_EXTS):
                add_path_hit(
                    raw=raw_path,
                    lineno=getattr(node, "lineno", 1),
                    source="path expression",
                    loaded_paths=loaded_paths,
                    repo_root_name=repo_root_name,
                    issues=issues,
                    path_hits=path_hits,
                    seen=seen_path_hits,
                )

    for target_name, raw in sorted(path_vars.items()):
        if any(raw.endswith(ext) for ext in FILE_EXTS):
            line = 1
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets: list[ast.AST] = []
                    if isinstance(node, ast.Assign):
                        targets = list(node.targets)
                    else:
                        targets = [node.target]
                    if any(isinstance(target, ast.Name) and target.id == target_name for target in targets):
                        line = getattr(node, "lineno", 1)
                        break
            add_path_hit(
                raw=raw,
                lineno=line,
                source=f"assignment:{target_name}",
                loaded_paths=loaded_paths,
                repo_root_name=repo_root_name,
                issues=issues,
                path_hits=path_hits,
                seen=seen_path_hits,
            )

    for hit in replace_hits:
        if hit.old not in anchor_checks:
            issues.append(f"replace old text at line {hit.lineno} has no static exact-anchor guard like `if old not in text`")

    has_edit_operation = bool(replace_hits) or path_method_call_counts.get("write_text", 0) > 0
    has_path_io = any(path_method_call_counts.get(name, 0) for name in PATH_METHODS)
    explicit_noop = any(mark_noop(value) for value in return_literals) and not has_edit_operation and not has_path_io
    authorized_paths = sorted({hit.resolved for hit in path_hits if hit.resolved})
    authorized_write_paths = sorted({
        hit.resolved for hit in path_hits
        if hit.resolved and hit.source.startswith("write_text()")
    })
    authorized_read_paths = sorted({
        hit.resolved for hit in path_hits
        if hit.resolved and (
            hit.source.startswith("read_text()")
            or hit.source.startswith("exists()")
            or hit.source.startswith("open()")
        )
    })
    dynamic_path_io_seen = any(
        " will be authorized by the fake pathlib sandbox" in note
        for note in runtime_enforced_notes
    )
    runtime_authorized_write_candidates = authorized_write_paths
    if not runtime_authorized_write_candidates and dynamic_path_io_seen and path_method_call_counts.get("write_text", 0) > 0:
        runtime_authorized_write_candidates = authorized_paths
    runtime_authorized_read_candidates = authorized_read_paths
    if not runtime_authorized_read_candidates and dynamic_path_io_seen and (
        path_method_call_counts.get("read_text", 0) > 0 or path_method_call_counts.get("exists", 0) > 0
    ):
        runtime_authorized_read_candidates = authorized_paths
    bounded_replace_count = sum(1 for hit in replace_hits if hit.count_one and hit.evidence_backed and hit.changed)

    if not uses_pathlib:
        issues.append("returned editor does not use pathlib.Path")

    if not explicit_noop:
        if not loaded_paths:
            issues.append("request file did not expose any loaded candidate/evidence paths")
        if not authorized_paths:
            issues.append("no generated repo path resolved to a loaded candidate/evidence file")
        if path_method_call_counts.get("write_text", 0) == 0:
            issues.append("returned editor does not call write_text(); it is not a direct file editor candidate")
        if not runtime_authorized_write_candidates:
            issues.append("no sandbox-authorizable write_text() to a loaded repo-relative path was found")

    if blocked_orchestration_seen and not runtime_authorized_write_candidates:
        issues.append("generated source looks like an orchestration script rather than a direct editor")

    if infrastructure_paths and not runtime_authorized_write_candidates:
        if len(infrastructure_paths) >= max(1, len(loaded_paths) // 2):
            issues.append(
                "loaded evidence is dominated by smoke/preflight infrastructure; rerun with a focused --inner-source-dir or improve retrieval"
            )
        else:
            issues.append("loaded evidence includes smoke/preflight infrastructure paths that are not safe edit targets")

    issue_set = sorted(set(issues))
    editor_contract = {
        "parses": True,
        "compiles": compiles,
        "top_level_main_no_args": main_ok,
        "comments_ok": not comment_warnings,
        "comments_are_warnings": True,
        "docstrings_ok": not docstring_seen,
        "runner_block_ok": not runner_block_seen,
        "uses_pathlib": uses_pathlib,
        "explicit_noop": explicit_noop,
        "loaded_path_count": len(loaded_paths),
        "infrastructure_loaded_path_count": len(infrastructure_paths),
        "infrastructure_loaded_paths": infrastructure_paths,
        "authorized_path_count": len(authorized_paths),
        "authorized_paths": authorized_paths,
        "authorized_read_paths": authorized_read_paths,
        "authorized_write_paths": authorized_write_paths,
        "runtime_authorized_read_candidates": sorted(runtime_authorized_read_candidates),
        "runtime_authorized_write_candidates": sorted(runtime_authorized_write_candidates),
        "path_method_call_counts": path_method_call_counts,
        "replace_call_count": len(replace_hits),
        "bounded_evidence_replace_count": bounded_replace_count,
        "ran_generated_editor": False,
    }

    return {
        "ok": not issue_set,
        "issues": issue_set,
        "warnings": sorted(set(comment_warnings + runtime_enforced_notes)),
        "loaded_paths": sorted(loaded_paths),
        "path_hits": [hit.__dict__ for hit in path_hits],
        "replace_hits": [hit.__dict__ for hit in replace_hits],
        "path_variables": dict(sorted(path_vars.items())),
        "root_variables": sorted(root_names),
        "return_literals": return_literals[:10],
        "editor_contract": editor_contract,
        "source_sha256": sha256_text(source),
    }




def request_evidence_files(request_text: str, repo_root_name: str) -> dict[str, str]:
    loaded_paths = extract_candidate_paths(request_text, repo_root_name)
    chunks_by_path: dict[str, list[str]] = {path: [] for path in loaded_paths}
    evidence_raw = section(request_text, "Evidence")

    try:
        evidence = json.loads(evidence_raw)
    except Exception:
        evidence = None

    def add_item(item: Any) -> None:
        if not isinstance(item, dict):
            return
        raw_path = item.get("path")
        text_value = item.get("text")
        if not isinstance(raw_path, str) or not isinstance(text_value, str):
            return
        path = clean_repo_path(raw_path, repo_root_name)
        if not path or path not in loaded_paths:
            return
        normalized = strip_line_number_prefixes(text_value)
        chosen = normalized if normalized else text_value
        if chosen and chosen not in chunks_by_path[path]:
            chunks_by_path[path].append(chosen)

    if isinstance(evidence, list):
        for item in evidence:
            add_item(item)
    elif isinstance(evidence, dict):
        add_item(evidence)
        for child in evidence.values():
            if isinstance(child, list):
                for item in child:
                    add_item(item)
            elif isinstance(child, dict):
                add_item(child)

    return {
        path: "\n".join(part for part in chunks if part)
        for path, chunks in sorted(chunks_by_path.items())
    }


def resolve_sandbox_path(raw: str, loaded_paths: set[str], repo_root_name: str) -> tuple[str | None, str]:
    s = norm_slash(raw)
    had_drive = bool(re.match(r"^[A-Za-z]:", s))
    absolute = s.startswith("/") or had_drive
    if had_drive:
        s = re.sub(r"^[A-Za-z]:", "", s)
    parts = [p for p in s.split("/") if p and p != "."]

    if not parts:
        return None, "path does not identify a loaded file"
    if ".." in parts:
        return None, "path traversal is not authorized"

    stripped_repo_root = False
    if repo_root_name in parts:
        parts = parts[parts.index(repo_root_name) + 1:]
        stripped_repo_root = True
        if not parts:
            return None, "path resolves only to repo root"
        if ".." in parts:
            return None, "path traversal is not authorized"

    candidate = "/".join(parts)
    if candidate in loaded_paths:
        if absolute:
            return candidate, "absolute path reduced to exact loaded path"
        if stripped_repo_root:
            return candidate, "repo-root prefix reduced to exact loaded path"
        return candidate, "exact loaded path"

    if len(parts) <= 1:
        return None, "basename-only path is not enough authority"

    matches = [
        path for path in loaded_paths
        if path.endswith("/" + candidate) or path == candidate or candidate.endswith("/" + path)
    ]
    if len(matches) == 1:
        if absolute:
            return matches[0], "absolute path uniquely reduced to loaded suffix"
        if stripped_repo_root:
            return matches[0], "repo-root prefix uniquely reduced to loaded suffix"
        return matches[0], "unique loaded suffix"
    if len(matches) > 1:
        return None, "ambiguous loaded suffix"
    if absolute:
        return None, "absolute path is not a uniquely loaded repo path"
    return None, "path is not loaded"


def repo_file_hashes(repo: Path, paths: set[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for relative in sorted(paths):
        try:
            cleaned = clean_repo_path(relative, repo.name)
            if cleaned is None or cleaned != relative:
                hashes[relative] = None
                continue
            path = repo / relative
            if path.exists() and path.is_file():
                hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                hashes[relative] = None
        except Exception:
            hashes[relative] = None
    return hashes


def unified_text_diff(path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )


def run_generated_editor_sandbox(
    source_text: str,
    request_text: str,
    repo: Path,
    repo_root_name: str,
    static_report: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    unauthorized: list[dict[str, str]] = []
    authorized_reads: set[str] = set()
    authorized_writes: set[str] = set()
    loaded_original = request_evidence_files(request_text, repo_root_name)
    loaded_paths = set(static_report.get("loaded_paths") or extract_candidate_paths(request_text, repo_root_name))
    for path in sorted(loaded_paths):
        loaded_original.setdefault(path, "")
    overlay: dict[str, str] = {}
    path_resolution_log: list[dict[str, str]] = []
    before_hashes = repo_file_hashes(repo, loaded_paths)
    ran_generated_editor = False
    result: Any = None

    if not static_report.get("ok"):
        issues.append("static preflight did not pass; sandbox execution is not allowed")
    if not source_text.strip():
        issues.append("generated editor source is empty")

    def resolve_for_io(raw: str, operation: str) -> str:
        resolved, reason = resolve_sandbox_path(raw, loaded_paths, repo_root_name)
        entry = {"operation": operation, "raw": raw, "reason": reason}
        if resolved:
            entry["resolved"] = resolved
        path_resolution_log.append(entry)
        if not resolved:
            unauthorized.append(entry)
            raise PermissionError(f"{operation} rejected for unauthorized path {raw!r}: {reason}")
        return resolved

    class FakePath:
        def __init__(self, *parts: Any) -> None:
            if not parts:
                self._raw = "."
                return
            pieces: list[str] = []
            for part in parts:
                if isinstance(part, FakePath):
                    value = part._raw
                else:
                    value = str(part)
                if value:
                    pieces.append(value)
            self._raw = "/".join(pieces) if pieces else "."

        def __truediv__(self, other: Any) -> "FakePath":
            return FakePath(self._raw, other)

        def __fspath__(self) -> str:
            return self._raw

        def __str__(self) -> str:
            return self._raw

        def __repr__(self) -> str:
            return f"FakePath({self._raw!r})"

        def exists(self) -> bool:
            resolved = resolve_for_io(self._raw, "exists")
            authorized_reads.add(resolved)
            return resolved in loaded_original

        def read_text(self, encoding: str = "utf-8", errors: str | None = None) -> str:
            resolved = resolve_for_io(self._raw, "read_text")
            authorized_reads.add(resolved)
            if resolved in overlay:
                return overlay[resolved]
            return loaded_original[resolved]

        def write_text(
            self,
            data: str,
            encoding: str = "utf-8",
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            resolved = resolve_for_io(self._raw, "write_text")
            authorized_writes.add(resolved)
            if not isinstance(data, str):
                raise TypeError("sandbox write_text only accepts str data")
            overlay[resolved] = data
            return len(data)

    class FakePathlibModule:
        Path = FakePath
        __name__ = "pathlib"
        __all__ = ["Path"]

    fake_pathlib = FakePathlibModule()

    def sandbox_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> Any:
        if level != 0:
            raise ImportError("relative imports are not available in the generated-editor sandbox")
        if name == "pathlib":
            return fake_pathlib
        raise ImportError(f"import {name!r} is not available in the generated-editor sandbox")

    safe_builtins: dict[str, Any] = {
        "__import__": sandbox_import,
        "Exception": Exception,
        "PermissionError": PermissionError,
        "RuntimeError": RuntimeError,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "NameError": NameError,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "repr": repr,
        "set": set,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "sorted": sorted,
        "any": any,
        "all": all,
    }

    execution_source, stripped_top_level_main_call_lines = strip_module_level_main_calls(source_text)

    if not issues:
        try:
            compiled = compile(execution_source, "<generated_editor_sandbox>", "exec")
            sandbox_globals: dict[str, Any] = {
                "__builtins__": safe_builtins,
                "__name__": "generated_editor_sandbox",
            }
            exec(compiled, sandbox_globals)
            main_fn = sandbox_globals.get("main")
            if not callable(main_fn):
                issues.append("generated editor did not define a callable main()")
            else:
                ran_generated_editor = True
                result = main_fn()
        except Exception as exc:
            issues.append(f"sandbox execution failed: {type(exc).__name__}: {exc}")

    after_hashes = repo_file_hashes(repo, loaded_paths)
    real_repo_modified = before_hashes != after_hashes
    if real_repo_modified:
        issues.append("real repo file hash changed during sandbox execution")

    changed_files = sorted(
        path for path, value in overlay.items()
        if value != loaded_original.get(path, "")
    )
    diffs = [
        {
            "path": path,
            "diff": unified_text_diff(path, loaded_original.get(path, ""), overlay[path]),
        }
        for path in changed_files
    ]

    if isinstance(result, str):
        raw_status = result
        status = normalize_generated_editor_status(result)
    elif result is None and issues:
        raw_status = "exception"
        status = "exception"
    else:
        raw_status = f"non_string_result:{type(result).__name__}"
        status = raw_status

    if not issues:
        if status not in {"done", "needs_more_evidence"}:
            issues.append(f"generated editor returned unsupported status {status!r}")
        if status == "needs_more_evidence" and changed_files:
            issues.append("generated editor changed files while returning needs_more_evidence")
        if status == "done" and not changed_files:
            issues.append("generated editor returned done without changing an authorized loaded file")
        if overlay and not authorized_writes:
            issues.append("sandbox overlay changed without an authorized write record")

    if unauthorized:
        issues.append("generated editor attempted unauthorized path access")

    issue_set = sorted(set(issues))
    return {
        "mode": "generated_editor_sandbox_only",
        "ok": not issue_set,
        "ran_generated_editor": ran_generated_editor,
        "real_repo_modified": real_repo_modified,
        "status": status,
        "raw_status": raw_status,
        "stripped_top_level_main_call_lines": stripped_top_level_main_call_lines,
        "loaded_paths": sorted(loaded_paths),
        "evidence_file_count": len([path for path, content in loaded_original.items() if content]),
        "authorized_reads": sorted(authorized_reads),
        "authorized_writes": sorted(authorized_writes),
        "changed_files": changed_files,
        "diffs": diffs,
        "issues": issue_set,
        "unauthorized_attempts": unauthorized,
        "path_resolution_log": path_resolution_log,
        "source_sha256": sha256_text(source_text),
    }


def write_static_report(report: dict[str, Any], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "12_generated_editor_preflight.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def write_sandbox_report_and_return(report: dict[str, Any], run_dir: Path) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "13_generated_editor_sandbox.json"
    diff_path = run_dir / "13_generated_editor_diff.patch"
    diff_text = "".join(item.get("diff", "") for item in report.get("diffs", []))
    diff_path.write_text(diff_text, encoding="utf-8")
    report["diff_path"] = str(diff_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get("ok"):
        print(f"[done] generated editor sandbox passed: {report_path}")
        return 0
    print(f"[fail] generated editor sandbox failed: {report_path}", file=sys.stderr)
    return 1



LOCKED_PIPELINE_PROMPT = "the stop button should be red not green"
PIPELINE_SOURCE_DIRS = ("main_computer/web/applications",)
PIPELINE_MODE = "real_rag_generated_editor_to_sandbox"


def pipeline_utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def pipeline_short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def pipeline_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pipeline_write_json(path: Path, value: Any) -> None:
    pipeline_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def pipeline_compact_error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def load_real_pipeline_modules(repo: Path) -> tuple[Any, Any]:
    repo_text = str(repo)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    from main_computer import rag_gremlin_pyramid_atom_smoke as base  # noqa: E402
    from main_computer import rag_gremlin_action_smoke as action  # noqa: E402
    return base, action


def validate_generated_editor_generator_source(source: str, filename: str) -> dict[str, Any]:
    issues: list[str] = []
    if not source.strip():
        issues.append("generator source is empty")
        return {
            "ok": False,
            "issues": issues,
            "source_sha256": sha256_text(source),
            "editor_contract": {"parses": False, "compiles": False},
        }

    issues.extend(f"generator {item}" for item in has_comments(source))

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return {
            "ok": False,
            "issues": [f"syntax error: {exc}"],
            "source_sha256": sha256_text(source),
            "editor_contract": {"parses": False, "compiles": False},
        }

    compiles = True
    try:
        compile(source, filename, "exec")
    except SyntaxError as exc:
        compiles = False
        where = f" at line {exc.lineno}" if exc.lineno else ""
        issues.append(f"compile error: {exc.msg}{where}")

    if ast.get_docstring(tree):
        issues.append("generator module docstring is not allowed")

    main_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    main_ok = False
    if not main_defs:
        issues.append("generator missing top-level main()")
    elif len(main_defs) > 1:
        issues.append("generator has multiple top-level main() definitions")
    else:
        args = main_defs[0].args
        argc = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
        if argc or args.vararg or args.kwarg:
            issues.append("generator main() must take no arguments")
        else:
            main_ok = True

    blocked_import_roots = {
        "os", "sys", "subprocess", "socket", "requests", "shutil", "tempfile",
        "urllib", "http", "ftplib", "glob", "importlib", "builtins", "io",
        "pathlib",
    }
    blocked_calls = {
        "open", "eval", "exec", "compile", "__import__", "input", "globals",
        "locals", "vars", "dir", "setattr", "delattr", "getattr", "print",
    }
    blocked_attr_calls = {
        "read_text", "write_text", "read_bytes", "write_bytes", "open",
        "exists", "unlink", "remove", "rename", "mkdir", "rmdir",
        "iterdir", "glob", "rglob", "walk", "system", "popen",
    }

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Lambda)):
            issues.append(f"generator disallowed node {type(node).__name__} at line {getattr(node, 'lineno', 1)}")
        if isinstance(node, ast.If) and is_runner_if(node):
            issues.append('generator if __name__ == "__main__" block is not allowed')
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            issues.append(f"generator disallowed {type(node).__name__} at line {getattr(node, 'lineno', 1)}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node):
                issues.append(f"generator docstring is not allowed in {getattr(node, 'name', '<node>')} at line {node.lineno}")

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in blocked_import_roots or root != "__future__":
                    issues.append(f"generator disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in blocked_import_roots or root != "__future__":
                issues.append(f"generator disallowed import-from: {node.module}")

        if isinstance(node, ast.Call):
            func = node.func
            name = call_name(node)
            if isinstance(func, ast.Name) and name in blocked_calls:
                issues.append(f"generator disallowed call: {name}() at line {node.lineno}")
            if isinstance(func, ast.Attribute) and name in blocked_attr_calls:
                issues.append(f"generator disallowed attribute call: {name}() at line {node.lineno}")

    issue_set = sorted(set(issues))
    return {
        "ok": not issue_set,
        "issues": issue_set,
        "source_sha256": sha256_text(source),
        "editor_contract": {
            "parses": True,
            "compiles": compiles,
            "top_level_main_no_args": main_ok,
            "restricted_imports": not any("import" in issue for issue in issue_set),
            "no_direct_file_io": not any("read_text" in issue or "write_text" in issue or "open" in issue for issue in issue_set),
            "returns_editor_source": None,
        },
    }


def run_generated_editor_generator_restricted(source: str, run_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    result: Any = None
    stdout_text = ""
    stderr_text = ""

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError(f"imports are not available while running the generated editor-generator: {name!r}")

    safe_builtins: dict[str, Any] = {
        "__import__": blocked_import,
        "Exception": Exception,
        "RuntimeError": RuntimeError,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "AssertionError": AssertionError,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "repr": repr,
        "set": set,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "sorted": sorted,
        "any": any,
        "all": all,
    }

    try:
        compiled = compile(source, "<generated_editor_generator>", "exec")
        generator_globals: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": "generated_editor_generator",
        }
        exec(compiled, generator_globals)
        main_fn = generator_globals.get("main")
        if not callable(main_fn):
            issues.append("generated editor-generator did not define a callable main()")
        else:
            result = main_fn()
    except Exception as exc:
        issues.append(f"restricted generator execution failed: {type(exc).__name__}: {exc}")

    generated_source = result if isinstance(result, str) else ""
    if result is not None and not isinstance(result, str):
        issues.append(f"generated editor-generator returned {type(result).__name__}, not str")
    if isinstance(result, str) and not result.strip():
        issues.append("generated editor-generator returned an empty source string")

    editor_compiles = False
    if generated_source.strip():
        try:
            compile(generated_source, "<generated_editor>", "exec")
            editor_compiles = True
        except SyntaxError as exc:
            issues.append(f"returned generated editor source does not compile: {exc}")

    output_path = run_dir / "11_gremlin_generator_output.json"
    source_path = run_dir / "11_gremlin_source.py"
    if generated_source:
        pipeline_write_text(source_path, generated_source)

    report = {
        "ok": not issues,
        "issues": sorted(set(issues)),
        "mode": "restricted_in_process_generator_to_source",
        "generated_source_path": str(source_path),
        "generated_source_chars": len(generated_source),
        "generated_source_sha256": sha256_text(generated_source) if generated_source else None,
        "generated_source_compiles": editor_compiles,
        "generator_stdout": stdout_text,
        "generator_stderr": stderr_text,
    }
    pipeline_write_json(output_path, report)
    return report


def build_direct_gremlin_editor_prompt(
    *,
    user_prompt: str,
    repo_name: str,
    selected_atoms: list[dict[str, Any]],
    symbol_context: str,
    candidate_files: list[str],
    max_prompt_chars: int = 24000,
    max_atom_json_chars: int = 9000,
    max_symbol_context_chars: int = 3000,
) -> tuple[str, dict[str, Any]]:
    selected_for_ai = selected_atoms[:3]
    if hasattr(selected_atoms, "__iter__"):
        trimmed: list[dict[str, Any]] = []
        used = 0
        for atom in selected_atoms:
            if len(trimmed) >= 3:
                break
            item = {
                "path": atom.get("path"),
                "line_start": atom.get("line_start"),
                "line_end": atom.get("line_end"),
                "category": atom.get("category"),
                "text": atom.get("text"),
            }
            encoded = json.dumps(item, sort_keys=True)
            if used + len(encoded) > max_atom_json_chars:
                continue
            trimmed.append(item)
            used += len(encoded)
        selected_for_ai = trimmed
    capped_symbol_context = symbol_context[:max_symbol_context_chars]

    def render() -> str:
        selected_atom_json = json.dumps(selected_for_ai, indent=2, sort_keys=True)
        return f"""Return only Python source code. No markdown, no backticks, no prose.

You are writing one direct generated editor program, not a generator program.

The program must:
- import pathlib.Path using `from pathlib import Path`
- define main()
- take no arguments
- not print
- not include comments or docstrings
- not include an if __name__ == "__main__" block
- not call main() at module top level
- use only pathlib.Path read_text(), write_text(), and optional exists()
- edit only repo-relative paths shown in Candidate files or Evidence
- make the smallest safe edit satisfying the request
- return "done" after one authorized write changes content
- return "needs_more_evidence" without writing if the exact edit is not grounded

Replacement edit contract:
- the old string must be an exact literal substring copied from Evidence or Additional context
- if Evidence displays line numbers like `930: text`, remove those display prefixes before copying the old string
- the new string must be the intended end-state string
- use replace(old, new, 1)
- guard replacement with `if old not in text: return "needs_more_evidence"`
- write only after the replacement produces changed text

Original request:
<<<
{user_prompt}
>>>

Repo root:
<<<
{repo_name}
>>>

Candidate files:
<<<
{candidate_files}
>>>

Evidence:
<<<
{selected_atom_json}
>>>

Additional context:
<<<
{capped_symbol_context}
>>>
"""

    prompt = render()
    if len(prompt) > max_prompt_chars:
        while len(prompt) > max_prompt_chars and selected_for_ai:
            selected_for_ai.pop()
            prompt = render()
        if len(prompt) > max_prompt_chars:
            overflow = len(prompt) - max_prompt_chars
            capped_symbol_context = capped_symbol_context[:-overflow] if overflow < len(capped_symbol_context) else ""
            prompt = render()
        if len(prompt) > max_prompt_chars:
            prompt = prompt[:max_prompt_chars]
    stats = {
        "direct_editor_prompt_chars": len(prompt),
        "selected_atom_count_for_ai": len(selected_for_ai),
        "symbol_context_chars": len(capped_symbol_context),
        "candidate_file_count": len(candidate_files),
        "prompt_mode": "direct_generated_editor_source",
    }
    return prompt, stats


def pipeline_run_offline_self_check(repo: Path, run_dir: Path, repo_root_name: str) -> int:
    request_text = embedded_fixture_request(repo_root_name)
    source_text = embedded_fixture_source()
    request_path = run_dir / "11_offline_fixture_request.txt"
    source_path = run_dir / "11_offline_fixture_source.py"
    pipeline_write_text(request_path, request_text)
    pipeline_write_text(source_path, source_text)

    static_report = static_preflight(source_text, request_text, repo_root_name)
    static_report.update({
        "mode": "offline_self_check_static_preflight",
        "external_model_dependency": False,
        "request_path": str(request_path),
        "source_path": str(source_path),
        "repo_root_name": repo_root_name,
    })
    write_static_report(static_report, run_dir)

    sandbox_report = run_generated_editor_sandbox(
        source_text=source_text,
        request_text=request_text,
        repo=repo,
        repo_root_name=repo_root_name,
        static_report=static_report,
    )
    sandbox_report.update({
        "mode": "offline_self_check_generated_editor_sandbox",
        "external_model_dependency": False,
        "request_path": str(request_path),
        "source_path": str(source_path),
        "repo_root_name": repo_root_name,
    })
    final = {
        "ok": bool(static_report.get("ok") and sandbox_report.get("ok")),
        "mode": "offline_self_check",
        "external_model_dependency": False,
        "run_dir": str(run_dir),
        "static_report": str(run_dir / "12_generated_editor_preflight.json"),
        "sandbox_report": str(run_dir / "13_generated_editor_sandbox.json"),
    }
    pipeline_write_json(run_dir / "final_report.json", final)
    rc = write_sandbox_report_and_return(sandbox_report, run_dir)
    return 0 if final["ok"] and rc == 0 else 1


def run_real_pipeline_to_sandbox(args: argparse.Namespace) -> int:
    repo = repo_root_from(Path(args.repo))
    repo_root_name = args.repo_root_name or repo.name
    run_id = f"rga_pipeline_{pipeline_utc_stamp()}_{pipeline_short_hash(LOCKED_PIPELINE_PROMPT)}"
    run_dir = Path(args.out).resolve() if args.out else repo / "debug_assets" / "rga_pipeline_sandbox" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.offline_self_check:
        return pipeline_run_offline_self_check(repo, run_dir, repo_root_name)

    final_report: dict[str, Any] = {
        "ok": False,
        "mode": PIPELINE_MODE,
        "status": "started",
        "external_model_dependency": True,
        "generated_editor_real_repo_execution": False,
        "prompt": LOCKED_PIPELINE_PROMPT,
        "repo": str(repo),
        "repo_root_name": repo_root_name,
        "run_dir": str(run_dir),
        "phase_status": {
            "pipeline_modules_loaded": False,
            "source_files_selected": False,
            "ai_pyramid_generated": False,
            "evidence_selected": False,
            "generator_prompt_built": False,
            "generator_source_generated": False,
            "generator_source_validated": False,
            "editor_source_generated": False,
            "editor_static_preflight_passed": False,
            "editor_sandbox_passed": False,
        },
    }

    try:
        base, action = load_real_pipeline_modules(repo)
        final_report["phase_status"]["pipeline_modules_loaded"] = True

        log = base.Logger(run_dir / "verbose.log", quiet=args.quiet)
        log.banner("REAL RAG GENERATED-EDITOR PIPELINE -> SANDBOX ONLY")
        config = {
            "mode": PIPELINE_MODE,
            "prompt": LOCKED_PIPELINE_PROMPT,
            "repo": str(repo),
            "run_dir": str(run_dir),
            "source_dirs": list(args.source_dir or PIPELINE_SOURCE_DIRS),
            "model": args.model,
            "gremlin_model": args.gremlin_model or args.model,
            "ollama_url": args.ollama_url,
            "ollama_think": args.ollama_think,
            "generated_editor_real_repo_execution": False,
            "external_model_dependency": True,
            "generator_execution": "direct_response_to_editor_preflight_and_sandbox",
        }
        base.print_block(log, "CONFIG", config)
        pipeline_write_json(run_dir / "00_config.json", config)
        pipeline_write_text(run_dir / "00_prompt.txt", LOCKED_PIPELINE_PROMPT)

        source_dirs = [str(item).strip() for item in (args.source_dir or PIPELINE_SOURCE_DIRS) if str(item).strip()]
        source_files = base.iter_source_files(repo, source_dirs, include_runner=False)
        if not source_files:
            raise RuntimeError(f"no source files found for source dirs: {source_dirs!r}")
        final_report["phase_status"]["source_files_selected"] = True
        pipeline_write_json(run_dir / "03_source_files.json", [path.relative_to(repo).as_posix() for path in source_files])

        ai_pyramid, pyramid_info = action.call_ollama_pyramid_direct(
            LOCKED_PIPELINE_PROMPT,
            args.model,
            args.ollama_url,
            args.ai_timeout_s,
            log,
            run_dir,
            think=action.parse_ollama_think_choice(args.ollama_think),
        )
        final_report["phase_status"]["ai_pyramid_generated"] = True
        parsed_tree = action.parse_ai_pyramid_or_fail(ai_pyramid, run_dir, log)

        word_summary: list[dict[str, Any]] = []
        for term in parsed_tree["unique_terms"]:
            out_path = run_dir / "04_word_greps" / f"{base.safe_name(term)}.jsonl"
            summary = base.grep_term_global(repo, source_files, term, out_path)
            word_summary.append(summary)
            log(f"[grep-word] {term!r}: {summary['hit_count']} hits across {summary['file_count']} files -> {out_path}")
        pipeline_write_json(run_dir / "05_word_hit_summary.json", word_summary)

        word_hits = base.load_word_hits(run_dir, parsed_tree["unique_terms"])
        atoms = base.compute_atoms(repo, word_hits, parsed_tree["term_scores"], window=3)
        pipeline_write_json(run_dir / "06_atoms_all.json", atoms)
        pipeline_write_json(run_dir / "07_top_atoms.json", atoms[:50])

        evidence_buffer, selected_atoms, used_by_category = base.fill_evidence_buffer(
            atoms=atoms,
            max_chars=args.max_evidence_chars,
            category_limits={
                "same_line": 1800,
                "nearby_window": 4200,
                "block_window": 3500,
            },
        )
        if not selected_atoms:
            raise RuntimeError("real retrieval selected no atom evidence")
        final_report["phase_status"]["evidence_selected"] = True
        pipeline_write_text(run_dir / "08_selected_atom_buffer.txt", evidence_buffer)
        pipeline_write_json(run_dir / "08_selected_atoms.json", selected_atoms)
        pipeline_write_json(run_dir / "08_category_usage.json", used_by_category)

        symbol_context, symbols_found = base.supplemental_symbol_context(
            repo=repo,
            evidence=evidence_buffer,
            selected_atoms=selected_atoms,
            source_files=source_files,
            max_chars=args.symbol_context_chars,
            context=args.symbol_context_lines,
            log=log,
        )
        pipeline_write_text(run_dir / "10_symbol_context.txt", symbol_context)
        pipeline_write_json(run_dir / "10_symbols_found.json", symbols_found)

        candidate_files = action.candidate_files_from_atoms(selected_atoms)
        pipeline_write_json(run_dir / "10_candidate_files.json", candidate_files)

        generator_prompt, generator_prompt_stats = build_direct_gremlin_editor_prompt(
            user_prompt=LOCKED_PIPELINE_PROMPT,
            repo_name=repo_root_name,
            selected_atoms=selected_atoms,
            symbol_context=symbol_context,
            candidate_files=candidate_files,
            max_symbol_context_chars=args.symbol_context_chars,
        )
        final_report["phase_status"]["generator_prompt_built"] = True
        pipeline_write_text(run_dir / "11_gremlin_generator_request.txt", generator_prompt)
        pipeline_write_json(run_dir / "11_gremlin_generator_request_stats.json", generator_prompt_stats)

        generator_source, generator_info = action.call_ollama_gremlin_source(
            prompt_text=generator_prompt,
            model=args.gremlin_model or args.model,
            url=args.ollama_url,
            timeout_s=args.gremlin_timeout_s,
            log=log,
            out_dir=run_dir,
            think=action.parse_ollama_think_choice(args.ollama_think),
        )
        final_report["phase_status"]["generator_source_generated"] = True

        # The existing real gremlin call already returns the generated editor source.
        # Do not treat that response as a second-stage "editor generator"; doing so
        # rejects normal editor capabilities such as pathlib.Path/read_text/write_text
        # before the real editor preflight and sandbox can evaluate them.
        editor_source = generator_source
        editor_source_path = run_dir / "11_gremlin_source.py"
        pipeline_write_text(editor_source_path, editor_source)
        pipeline_write_json(run_dir / "11_gremlin_source_capture.json", {
            "ok": bool(editor_source.strip()),
            "mode": "direct_ollama_response_is_generated_editor_source",
            "source_path": str(editor_source_path),
            "source_chars": len(editor_source),
            "source_sha256": sha256_text(editor_source),
            "generated_editor_real_repo_execution": False,
        })
        if not editor_source.strip():
            final_report["status"] = "generated_editor_source_empty"
            raise RuntimeError("Ollama returned an empty generated editor source")
        final_report["phase_status"]["generator_source_validated"] = True
        final_report["phase_status"]["editor_source_generated"] = True
        static_report = static_preflight(editor_source, generator_prompt, repo_root_name)
        static_report.update({
            "mode": "real_pipeline_generated_editor_static_preflight",
            "pipeline_mode": PIPELINE_MODE,
            "external_model_dependency": True,
            "generated_editor_real_repo_execution": False,
            "run_dir": str(run_dir),
            "source_path": str(editor_source_path),
            "request_path": str(run_dir / "11_gremlin_generator_request.txt"),
            "repo_root_name": repo_root_name,
            "prompt": LOCKED_PIPELINE_PROMPT,
            "source_dirs": source_dirs,
        })
        static_report_path = write_static_report(static_report, run_dir)
        if not static_report.get("ok"):
            final_report["status"] = "generated_editor_failed_static_preflight"
            final_report["static_report"] = static_report
            print(json.dumps(static_report, indent=2, sort_keys=True))
            raise RuntimeError(f"generated editor static preflight failed: {static_report.get('issues')}")
        final_report["phase_status"]["editor_static_preflight_passed"] = True
        print(f"[done] real-pipeline generated editor preflight passed: {static_report_path}")

        sandbox_report = run_generated_editor_sandbox(
            source_text=editor_source,
            request_text=generator_prompt,
            repo=repo,
            repo_root_name=repo_root_name,
            static_report=static_report,
        )
        sandbox_report.update({
            "mode": "real_pipeline_generated_editor_sandbox_only",
            "pipeline_mode": PIPELINE_MODE,
            "external_model_dependency": True,
            "generated_editor_real_repo_execution": False,
            "run_dir": str(run_dir),
            "source_path": str(editor_source_path),
            "request_path": str(run_dir / "11_gremlin_generator_request.txt"),
            "repo_root_name": repo_root_name,
            "prompt": LOCKED_PIPELINE_PROMPT,
        })
        if sandbox_report.get("ok"):
            final_report["phase_status"]["editor_sandbox_passed"] = True
            final_report["status"] = "pipeline_sandbox_passed"
        else:
            final_report["status"] = "generated_editor_failed_sandbox"
        rc = write_sandbox_report_and_return(sandbox_report, run_dir)

        final_report.update({
            "ok": bool(sandbox_report.get("ok")),
            "status": final_report["status"],
            "ai_pyramid_path": str(run_dir / "01_ai_pyramid_text.txt"),
            "selected_atoms_path": str(run_dir / "08_selected_atoms.json"),
            "candidate_files_path": str(run_dir / "10_candidate_files.json"),
            "generator_request_path": str(run_dir / "11_gremlin_generator_request.txt"),
            "generator_source_path": str(editor_source_path),
            "editor_source_path": str(editor_source_path),
            "static_report_path": str(run_dir / "12_generated_editor_preflight.json"),
            "sandbox_report_path": str(run_dir / "13_generated_editor_sandbox.json"),
            "sandbox_diff_path": str(run_dir / "13_generated_editor_diff.patch"),
            "generator_info": generator_info,
            "source_capture_path": str(run_dir / "11_gremlin_source_capture.json"),
            "sandbox_status": sandbox_report.get("status"),
            "sandbox_changed_files": sandbox_report.get("changed_files"),
            "sandbox_real_repo_modified": sandbox_report.get("real_repo_modified"),
        })
        pipeline_write_json(run_dir / "final_report.json", final_report)
        print(json.dumps({
            "ok": final_report["ok"],
            "status": final_report["status"],
            "run_dir": str(run_dir),
            "final_report": str(run_dir / "final_report.json"),
            "sandbox_report": str(run_dir / "13_generated_editor_sandbox.json"),
            "diff": str(run_dir / "13_generated_editor_diff.patch"),
            "generated_editor_real_repo_execution": False,
        }, indent=2, sort_keys=True))
        return rc

    except Exception as exc:
        final_report.setdefault("errors", []).append(pipeline_compact_error(exc))
        if final_report.get("status") == "started":
            final_report["status"] = "failed"
        final_report["ok"] = False
        pipeline_write_json(run_dir / "final_report.json", final_report)
        print(json.dumps({
            "ok": False,
            "status": final_report.get("status"),
            "error": f"{type(exc).__name__}: {exc}",
            "run_dir": str(run_dir),
            "final_report": str(run_dir / "final_report.json"),
            "generated_editor_real_repo_execution": False,
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the locked real RAG generated-editor pipeline and execute the produced editor only "
            "inside the fake-path sandbox. The prompt/task is baked in; no prompt argument is accepted."
        )
    )
    parser.add_argument("--repo", default=".", help="Repository root. Defaults to the current working tree.")
    parser.add_argument("--out", default=None, help="Output directory. Defaults under debug_assets/rga_pipeline_sandbox.")
    parser.add_argument("--repo-root-name", default=None, help="Override repository root name recorded in the generated request.")
    parser.add_argument("--source-dir", action="append", default=None, help="Optional technical override for focused retrieval roots. No prompt override is accepted.")
    parser.add_argument("--model", default=None, help="Ollama model. Defaults to MAIN_COMPUTER_GREMLIN_MODEL or the action smoke default.")
    parser.add_argument("--gremlin-model", default=None, help="Optional separate Ollama model for the generated-editor generator call.")
    parser.add_argument("--ollama-url", default=None, help="Ollama /api/generate URL. Defaults to the action smoke default.")
    parser.add_argument("--ollama-think", choices=["default", "off", "false", "on", "true"], default="default")
    parser.add_argument("--ai-timeout-s", type=int, default=0, help="Compatibility timeout setting for existing streaming call path.")
    parser.add_argument("--gremlin-timeout-s", type=int, default=0, help="Compatibility timeout setting for existing streaming call path.")
    parser.add_argument("--max-evidence-chars", type=int, default=7000)
    parser.add_argument("--symbol-context-chars", type=int, default=3000)
    parser.add_argument("--symbol-context-lines", type=int, default=12)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--offline-self-check",
        action="store_true",
        help="Verify the embedded sandbox mechanics without Ollama. This is not the real pipeline path.",
    )
    args = parser.parse_args(argv)

    repo = repo_root_from(Path(args.repo))
    if args.model is None or args.ollama_url is None:
        try:
            _base, action = load_real_pipeline_modules(repo)
            if args.model is None:
                args.model = action.DEFAULT_MODEL
            if args.ollama_url is None:
                args.ollama_url = action.DEFAULT_OLLAMA_URL
        except Exception:
            if args.model is None:
                args.model = "qwen3.6:35b-a3b"
            if args.ollama_url is None:
                args.ollama_url = "http://127.0.0.1:11434/api/generate"

    return run_real_pipeline_to_sandbox(args)


if __name__ == "__main__":
    raise SystemExit(main())
