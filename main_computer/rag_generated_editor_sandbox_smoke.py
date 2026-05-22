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
        if text_value not in chunks_by_path[path]:
            chunks_by_path[path].append(text_value)

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

    if not issues:
        try:
            compiled = compile(source_text, "<generated_editor_sandbox>", "exec")
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
        status = result
    elif result is None and issues:
        status = "exception"
    else:
        status = f"non_string_result:{type(result).__name__}"

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












def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a self-contained generated-editor sandbox smoke. "
            "The prompt, request fixture, generated editor source, and evidence blob are embedded; "
            "no prior debug run or prompt argument is required."
        )
    )
    parser.add_argument("--repo", default=".", help="Repository root. Defaults to the current working tree.")
    parser.add_argument("--out", default=None, help="Output directory for smoke reports. Defaults under debug_assets/rga_sandbox_embedded.")
    parser.add_argument("--repo-root-name", default=None, help="Override the repository root name recorded in the embedded request.")
    args = parser.parse_args(argv)

    repo = repo_root_from(Path(args.repo))
    repo_root_name = args.repo_root_name or repo.name
    prompt_words = ["the", "stop", "button", "should", "be", "red", "not", "green"]
    inner_source_dirs = list(DEFAULT_FOCUSED_SOURCE_DIRS)
    fixture_mode = "embedded_fixture"

    if args.out:
        run_dir = Path(args.out).resolve()
    else:
        run_dir = repo / "debug_assets" / "rga_sandbox_embedded" / f"rga_{time.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    request_path = run_dir / "11_embedded_gremlin_generator_request.txt"
    source_path = run_dir / "11_embedded_gremlin_source.py"
    request_text = embedded_fixture_request(repo_root_name)
    source_text = embedded_fixture_source()
    request_path.write_text(request_text, encoding="utf-8")
    source_path.write_text(source_text, encoding="utf-8")

    report = static_preflight(source_text, request_text, repo_root_name)
    report.update({
        "mode": "generated_editor_static_preflight_before_embedded_sandbox",
        "fixture_mode": fixture_mode,
        "ran_generated_editor": False,
        "run_dir": str(run_dir),
        "source_path": str(source_path),
        "request_path": str(request_path),
        "repo_root_name": repo_root_name,
        "prompt": prompt_text_from_words(prompt_words),
        "inner_source_dirs": inner_source_dirs,
        "external_run_dependency": False,
    })

    static_report_path = write_static_report(report, run_dir)
    if not report.get("ok"):
        print(json.dumps(report, indent=2, sort_keys=True))
        print(f"[fail] embedded generated editor preflight failed: {static_report_path}", file=sys.stderr)
        return 1

    print(f"[done] embedded generated editor preflight passed: {static_report_path}")
    sandbox_report = run_generated_editor_sandbox(
        source_text=source_text,
        request_text=request_text,
        repo=repo,
        repo_root_name=repo_root_name,
        static_report=report,
    )
    sandbox_report.update({
        "fixture_mode": fixture_mode,
        "run_dir": str(run_dir),
        "source_path": str(source_path),
        "request_path": str(request_path),
        "repo_root_name": repo_root_name,
        "external_run_dependency": False,
    })
    return write_sandbox_report_and_return(sandbox_report, run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
