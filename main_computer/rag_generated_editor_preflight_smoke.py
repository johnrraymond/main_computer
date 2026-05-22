#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MODEL = (
    os.environ.get("RAG_SMOKE_MODEL")
    or os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL")
    or "qwen3.6:35b-a3b"
)
DEFAULT_OLLAMA_GENERATE_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
DEFAULT_FOCUSED_SOURCE_DIRS = ("main_computer/web/applications",)

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


def normalize_ollama_generate_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return DEFAULT_OLLAMA_GENERATE_URL
    if value.endswith("/api/generate"):
        return value
    if value.endswith("/api"):
        return value + "/generate"
    return value + "/api/generate"


def prompt_text_from_words(prompt_words: list[str]) -> str:
    return " ".join(str(part) for part in prompt_words).strip()


def should_focus_default_source_dirs(prompt_words: list[str]) -> bool:
    prompt = prompt_text_from_words(prompt_words).lower()
    words = set(re.findall(r"[a-z0-9_]+", prompt))
    if {"stop", "button"}.issubset(words) and words.intersection({"red", "green", "color", "colour"}):
        return True
    return False


def choose_inner_source_dirs(prompt_words: list[str], args: argparse.Namespace) -> list[str]:
    explicit = [str(item).strip() for item in (args.inner_source_dir or []) if str(item).strip()]
    if explicit:
        return explicit
    if getattr(args, "no_default_inner_source_dir", False):
        return []
    if should_focus_default_source_dirs(prompt_words):
        return list(DEFAULT_FOCUSED_SOURCE_DIRS)
    return []


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


def evidence_text_from_request(request_text: str) -> str:
    evidence_raw = section(request_text, "Evidence")
    additional_context = section(request_text, "Additional context")
    return "\n".join(
        part for part in (
            evidence_raw,
            json_string_leaf_text(evidence_raw),
            additional_context,
        )
        if part
    )


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

    issues.extend(has_comments(source))
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
                    issues.append(
                        f"{method}() receiver at line {node.lineno} cannot be statically resolved to a loaded repo path: "
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
                backed = old in evidence_text
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
        if not authorized_write_paths:
            issues.append("no authorized write_text() to a loaded repo-relative path was found")

    if blocked_orchestration_seen and not authorized_write_paths:
        issues.append("generated source looks like an orchestration script rather than a direct editor")

    if infrastructure_paths and not authorized_write_paths:
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
        "comments_ok": not any(item.startswith("comment ") for item in issue_set),
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
        "path_method_call_counts": path_method_call_counts,
        "replace_call_count": len(replace_hits),
        "bounded_evidence_replace_count": bounded_replace_count,
        "ran_generated_editor": False,
    }

    return {
        "ok": not issue_set,
        "issues": issue_set,
        "loaded_paths": sorted(loaded_paths),
        "path_hits": [hit.__dict__ for hit in path_hits],
        "replace_hits": [hit.__dict__ for hit in replace_hits],
        "path_variables": dict(sorted(path_vars.items())),
        "root_variables": sorted(root_names),
        "return_literals": return_literals[:10],
        "editor_contract": editor_contract,
        "source_sha256": sha256_text(source),
    }



def safe_read_text(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return ""


def safe_read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.is_file():
            value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(value, dict):
                return value
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {}


def returns_non_none(fn: ast.FunctionDef) -> bool:
    return any(isinstance(node, ast.Return) and node.value is not None for node in ast.walk(fn))


def returns_name(fn: ast.FunctionDef, name: str) -> bool:
    return any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
        for node in ast.walk(fn)
    )


def function_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
    else:
        func = node
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def analyze_generator_source(source: str) -> dict[str, Any]:
    issues: list[str] = []
    helper_issues: list[str] = []
    extend_issues: list[str] = []
    helper_contracts: dict[str, dict[str, Any]] = {}

    if not source.strip():
        return {
            "ok": False,
            "issues": ["missing gremlin-generator source"],
            "parses": False,
            "source_sha256": None,
        }

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "ok": False,
            "issues": [f"gremlin-generator syntax error: {exc}"],
            "parses": False,
            "source_sha256": sha256_text(source),
        }

    main_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    main_ok = False
    if not main_defs:
        issues.append("gremlin-generator is missing top-level main()")
    elif len(main_defs) > 1:
        issues.append("gremlin-generator has multiple top-level main() definitions")
    else:
        args = main_defs[0].args
        argc = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
        if argc or args.vararg or args.kwarg:
            issues.append("gremlin-generator main() must take no arguments")
        else:
            main_ok = True
        if not returns_non_none(main_defs[0]):
            issues.append("gremlin-generator main() does not statically return a value")

    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    for fn_name, fn in functions.items():
        list_vars: dict[str, int] = {}
        list_mutations: dict[str, int] = {}
        returns: list[str] = []
        returns_joined_list = False

        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for assign_target in node.targets:
                    if isinstance(assign_target, ast.Name) and isinstance(node.value, ast.List):
                        list_vars[assign_target.id] = getattr(node, "lineno", fn.lineno)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and isinstance(node.value, ast.List):
                list_vars[node.target.id] = getattr(node, "lineno", fn.lineno)

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend"}
                and isinstance(node.func.value, ast.Name)
            ):
                list_mutations[node.func.value.id] = getattr(node, "lineno", fn.lineno)

            if isinstance(node, ast.Return):
                if node.value is None:
                    returns.append("None")
                elif isinstance(node.value, ast.Name):
                    returns.append(node.value.id)
                else:
                    returns.append(node_kind(node.value))
                    if (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr == "join"
                        and node.value.args
                        and isinstance(node.value.args[0], ast.Name)
                        and node.value.args[0].id in list_vars
                    ):
                        returns_joined_list = True

        built_lists = sorted(set(list_vars).intersection(list_mutations))
        missing_returns = [name for name in built_lists if not returns_name(fn, name)]
        returns_value = returns_non_none(fn)

        helper_contracts[fn_name] = {
            "lineno": fn.lineno,
            "builds_lists": built_lists,
            "returns": returns,
            "returns_value": returns_value,
            "returns_joined_list": returns_joined_list,
            "missing_built_list_returns": missing_returns,
        }

        if fn_name != "main" and built_lists and not returns_value:
            issue = (
                f"gremlin-generator helper {fn_name} builds list(s) {built_lists} "
                "but has no value return; callers may fail with NoneType not iterable"
            )
            helper_issues.append(issue)
            issues.append(issue)

    for fn_name, fn in functions.items():
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "extend"
                and node.args
                and isinstance(node.args[0], ast.Call)
            ):
                callee = function_call_name(node.args[0])
                if callee and callee in helper_contracts and not helper_contracts[callee]["returns_value"]:
                    issue = (
                        f"gremlin-generator {fn_name} extends a list with {callee}() at line {node.lineno}, "
                        "but that helper has no value return"
                    )
                    extend_issues.append(issue)
                    issues.append(issue)

    issue_set = sorted(set(issues))
    return {
        "ok": not issue_set,
        "issues": issue_set,
        "parses": True,
        "top_level_main_no_args": main_ok,
        "helper_contracts": helper_contracts,
        "helper_issues": helper_issues,
        "extend_issues": extend_issues,
        "source_sha256": sha256_text(source),
    }


def build_unavailable_generated_editor_report(
    *,
    run_dir: Path,
    repo_root_name: str,
    args: argparse.Namespace,
    prompt_words: list[str],
    inner_rc: int | None,
    reason: str,
) -> dict[str, Any]:
    request_path = run_dir / "11_gremlin_generator_request.txt"
    source_path = run_dir / "11_gremlin_source.py"
    generator_source_path = run_dir / "11_gremlin_generator_source.py"
    generator_output_path = run_dir / "11_gremlin_generator_output.json"
    generator_stdout_path = run_dir / "11_gremlin_generator_stdout.txt"
    generator_stderr_path = run_dir / "11_gremlin_generator_stderr.txt"

    request_text = safe_read_text(request_path)
    generator_source = safe_read_text(generator_source_path)
    generator_output = safe_read_json(generator_output_path)
    generator_analysis = analyze_generator_source(generator_source)

    issues: list[str] = [reason]
    if inner_rc is not None:
        issues.append(f"inner RAG action smoke returned {inner_rc} before generated-editor preflight could run")
    if not request_path.exists():
        issues.append(f"missing request file: {request_path}")
    if not source_path.exists():
        issues.append("missing generated editor source: 11_gremlin_source.py")
    if generator_output:
        error = generator_output.get("error")
        if error:
            issues.append(f"gremlin-generator execution failed: {error}")
    if not generator_source.strip():
        issues.append("missing gremlin-generator source: 11_gremlin_generator_source.py")
    issues.extend(generator_analysis.get("issues") or [])

    loaded_paths = extract_candidate_paths(request_text, repo_root_name) if request_text else set()
    infrastructure_paths = sorted(path for path in loaded_paths if is_infrastructure_path(path))

    report = {
        "ok": False,
        "mode": "generated_editor_static_preflight_only",
        "stage": "generated_editor_unavailable",
        "ran_generated_editor": False,
        "inner_returncode": inner_rc,
        "issues": sorted(set(issues)),
        "loaded_paths": sorted(loaded_paths),
        "editor_contract": {
            "parses": False,
            "generated_editor_available": False,
            "ran_generated_editor": False,
            "loaded_path_count": len(loaded_paths),
            "infrastructure_loaded_path_count": len(infrastructure_paths),
            "infrastructure_loaded_paths": infrastructure_paths,
        },
        "generator_contract": generator_analysis,
        "generator_output": generator_output,
        "generator_artifacts": {
            "request_path": str(request_path),
            "source_path": str(source_path),
            "source_exists": source_path.exists(),
            "generator_source_path": str(generator_source_path),
            "generator_source_exists": generator_source_path.exists(),
            "generator_output_path": str(generator_output_path),
            "generator_output_exists": generator_output_path.exists(),
            "generator_stdout_path": str(generator_stdout_path),
            "generator_stdout_exists": generator_stdout_path.exists(),
            "generator_stderr_path": str(generator_stderr_path),
            "generator_stderr_exists": generator_stderr_path.exists(),
        },
        "source_sha256": None,
        "mode_note": "The generated editor was not executed. The wrapper inspected available debug artifacts only.",
        "run_dir": str(run_dir),
        "source_path": str(source_path),
        "request_path": str(request_path),
        "repo_root_name": repo_root_name,
        "ollama_url": normalize_ollama_generate_url(args.ollama_url),
        "model": args.model,
        "gremlin_model": args.gremlin_model or args.model,
        "prompt": prompt_text_from_words(prompt_words),
        "inner_source_dirs": choose_inner_source_dirs(prompt_words, args),
    }
    return report


def write_report_and_return(report: dict[str, Any], run_dir: Path) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "12_generated_editor_preflight.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get("ok"):
        print(f"[done] generated editor preflight passed: {report_path}")
        return 0
    print(f"[fail] generated editor preflight failed: {report_path}", file=sys.stderr)
    return 1

def run_existing_smoke(repo: Path, out_dir: Path, prompt_words: list[str], args: argparse.Namespace) -> int:
    smoke = repo / "main_computer" / "rag_gremlin_action_smoke.py"
    if not smoke.exists():
        raise SystemExit(f"missing existing smoke: {smoke}")

    cmd = [
        sys.executable,
        str(smoke),
        "--repo",
        str(repo),
        "--out",
        str(out_dir),
        "--model",
        args.model,
        "--gremlin-model",
        args.gremlin_model or args.model,
        "--ollama-url",
        normalize_ollama_generate_url(args.ollama_url),
        "--ollama-think",
        args.ollama_think,
    ]

    inner_source_dirs = choose_inner_source_dirs(prompt_words, args)
    for source_dir in inner_source_dirs:
        cmd.extend(["--source-dir", source_dir])

    if args.quiet_inner:
        cmd.append("--quiet")

    cmd.extend(prompt_words)

    print("[run]", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(repo))
    return proc.returncode


def resolve_run_dir(value: str, repo: Path) -> Path:
    candidate = Path(value)
    if candidate.exists():
        return candidate.resolve()
    repo_candidate = repo / value
    if repo_candidate.exists():
        return repo_candidate.resolve()
    return candidate.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the current RAG action smoke, then statically preflight the generated editor without executing it."
    )
    parser.add_argument("prompt", nargs="*", help="Prompt to forward to rag_gremlin_action_smoke.py.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--from-run", default=None, help="Validate an existing debug_assets/rga*/rga_* run instead of invoking the AI smoke.")
    parser.add_argument("--out", default=None, help="Output directory for the inner run. Must not already exist.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--gremlin-model", default=None)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_GENERATE_URL)
    parser.add_argument("--ollama-think", choices=["default", "off", "false", "on", "true"], default="default")
    parser.add_argument("--repo-root-name", default=None)
    parser.add_argument(
        "--inner-source-dir",
        action="append",
        default=None,
        help=(
            "Source directory to pass through to rag_gremlin_action_smoke.py. "
            "Repeatable. If omitted, the default stop-button smoke is focused to main_computer/web/applications."
        ),
    )
    parser.add_argument(
        "--no-default-inner-source-dir",
        action="store_true",
        help="Do not auto-focus the default stop-button smoke to main_computer/web/applications.",
    )
    parser.add_argument("--quiet-inner", action="store_true")
    args = parser.parse_args(argv)

    repo = repo_root_from(Path(args.repo))
    repo_root_name = args.repo_root_name or repo.name

    prompt_words = list(args.prompt) or ["the", "stop", "button", "should", "be", "red", "not", "green"]
    inner_source_dirs = choose_inner_source_dirs(prompt_words, args)

    if args.from_run:
        run_dir = resolve_run_dir(args.from_run, repo)
    else:
        if args.out:
            run_dir = Path(args.out).resolve()
        else:
            run_dir = repo / "debug_assets" / "rga_preflight" / f"rga_{time.strftime('%Y%m%dT%H%M%SZ')}"
        rc = run_existing_smoke(repo, run_dir, prompt_words, args)
        if rc != 0:
            report = build_unavailable_generated_editor_report(
                run_dir=run_dir,
                repo_root_name=repo_root_name,
                args=args,
                prompt_words=prompt_words,
                inner_rc=rc,
                reason="inner RAG action smoke failed before a generated editor source was available",
            )
            return write_report_and_return(report, run_dir)

    request_path = run_dir / "11_gremlin_generator_request.txt"
    source_path = run_dir / "11_gremlin_source.py"
    if not request_path.exists() or not source_path.exists():
        missing_reason = "generated editor source was not available for static preflight"
        if not request_path.exists() and not source_path.exists():
            missing_reason = "request file and generated editor source were not available for static preflight"
        elif not request_path.exists():
            missing_reason = "request file was not available for static preflight"
        report = build_unavailable_generated_editor_report(
            run_dir=run_dir,
            repo_root_name=repo_root_name,
            args=args,
            prompt_words=prompt_words,
            inner_rc=None,
            reason=missing_reason,
        )
        return write_report_and_return(report, run_dir)

    request_text = request_path.read_text(encoding="utf-8", errors="replace")
    source_text = source_path.read_text(encoding="utf-8", errors="replace")

    report = static_preflight(source_text, request_text, repo_root_name)
    report.update({
        "mode": "generated_editor_static_preflight_only",
        "ran_generated_editor": False,
        "run_dir": str(run_dir),
        "source_path": str(source_path),
        "request_path": str(request_path),
        "repo_root_name": repo_root_name,
        "ollama_url": normalize_ollama_generate_url(args.ollama_url),
        "model": args.model,
        "gremlin_model": args.gremlin_model or args.model,
        "prompt": prompt_text_from_words(prompt_words),
        "inner_source_dirs": inner_source_dirs,
    })

    return write_report_and_return(report, run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
