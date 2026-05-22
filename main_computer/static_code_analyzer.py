from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO


DEFAULT_MAX_FILE_BYTES = 1_500_000
DEFAULT_LONG_LINE_THRESHOLD = 120
EXPORT_SCRIPT_NAME = "export-main-computer-test.ps1"

SOURCE_SUFFIX_LANGUAGES = {
    ".bat": "Batch",
    ".cmd": "Batch",
    ".cjs": "JavaScript",
    ".css": "CSS",
    ".go": "Go",
    ".h": "C/C++",
    ".hpp": "C/C++",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsx": "JavaScript JSX",
    ".mjs": "JavaScript",
    ".ps1": "PowerShell",
    ".psd1": "PowerShell",
    ".psm1": "PowerShell",
    ".py": "Python",
    ".rs": "Rust",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript JSX",
    ".vue": "Vue",
    ".yaml": "YAML",
    ".yml": "YAML",
}

SPECIAL_FILENAME_LANGUAGES = {
    ".dockerignore": "Docker ignore",
    ".gitignore": "Git ignore",
    "makefile": "Makefile",
}

DOC_SUFFIX_LANGUAGES = {
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
}

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "archive",
    "build",
    "coverage",
    "diagnostics_output",
    "dist",
    "env",
    "harness_output",
    "htmlcov",
    "logs",
    "node_modules",
    "release_reports",
    "runtime",
    "target",
    "vendor",
    "venv",
}

DEFAULT_EXCLUDED_PATH_PREFIXES = (
    ("contracts", "lib"),
    ("contracts", "out"),
    ("generated_component_docs",),
    ("tools", "patching", "reports"),
)

COMMENT_PREFIXES_BY_LANGUAGE = {
    "Batch": ("rem ", "rem\t", "::"),
    "C/C++": ("//", "/*", "*", "*/"),
    "CSS": ("/*", "*", "*/"),
    "Docker ignore": ("#",),
    "Dockerfile": ("#",),
    "Git ignore": ("#",),
    "Go": ("//", "/*", "*", "*/"),
    "HTML": ("<!--",),
    "Java": ("//", "/*", "*", "*/"),
    "JavaScript": ("//", "/*", "*", "*/"),
    "JavaScript JSX": ("//", "/*", "*", "*/"),
    "Makefile": ("#",),
    "PowerShell": ("#",),
    "Python": ("#",),
    "Rust": ("//", "/*", "*", "*/"),
    "SCSS": ("//", "/*", "*", "*/"),
    "Shell": ("#",),
    "Solidity": ("//", "/*", "*", "*/"),
    "SQL": ("--",),
    "TOML": ("#",),
    "TypeScript": ("//", "/*", "*", "*/"),
    "TypeScript JSX": ("//", "/*", "*", "*/"),
    "Vue": ("<!--", "//", "/*", "*", "*/"),
    "YAML": ("#",),
}

TODO_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
TODO_MARKER_PATTERN = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExportRules:
    export_items: tuple[str, ...]
    allowed_generated_exact_paths: tuple[str, ...]
    blocked_directory_names: tuple[str, ...]
    blocked_exact_paths: tuple[str, ...]
    blocked_prefixes: tuple[str, ...]
    blocked_file_names: tuple[str, ...]
    blocked_extensions: tuple[str, ...]


DEFAULT_EXPORT_RULES = ExportRules(
    export_items=(
        "main_computer",
        "tests",
        "contracts",
        "dev-diagnosis.py",
        "dev-chain-diagnosis.py",
        "dev-chain-reset.py",
        "dev-chain-flow.py",
        "dev-chain-ledger-bridge.py",
        "dev-chain-wallet-smoke-guide.py",
        "docker-compose.executor.yml",
        "Dockerfile.full_executor",
        "Dockerfile.executor",
        "start-main-computer-docker-windows.ps1",
        "bootstrap-main-computer-windows.ps1",
        "pretty_docs",
        "game_projects",
        "new_patch.py",
        "new_diff.py",
        "git-control.py",
        "git_dirty.py",
        "missing.txt",
        "README.md",
        "ENVIRONMENT.md",
        "TODO.md",
        "generated_component_docs",
        "pyproject.toml",
        "requirements.txt",
        ".dockerignore",
        "docker-compose.dev.yml",
        "docker-compose.onlyoffice.yml",
        "docker-compose.applications.yml",
        "docker",
        "deploy/local-platform",
        "deploy/coolify/local-docker",
        "proto-dev",
        "control-main-computer.ps1",
        "dev-control.ps1",
        "prod-command.py",
        EXPORT_SCRIPT_NAME,
        "tools",
        "scripts",
        "runtime/main-computer-runtime.json",
        "diagnosis-docker-windows-host-paths-v5.ps1",
    ),
    allowed_generated_exact_paths=("runtime/main-computer-runtime.json",),
    blocked_directory_names=(
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        ".venv",
        "venv",
        ".tmp",
        "harness_output",
        "harness_output_pretty_docs",
        "harness_output_game_editor",
        "migration",
        "release_reports",
        "debug_assets",
        ".main_computer_browser_profile",
        "cache",
    ),
    blocked_exact_paths=(
        ".prod.lock",
        "aider.log",
        "runtime",
        "energy_credits",
        "release_reports",
        "generated_component_docs/work",
        "generated_component_docs/archive",
        "generated_component_docs/doc-build.json",
        "generated_component_docs/doc-health.json",
        "generated_component_docs/graph.json",
        "main_computer/.main_computer_browser_profile",
        "main_computer/debug_assets",
        "contracts/cache",
        "contracts/out",
    ),
    blocked_prefixes=(
        "runtime/",
        "energy_credits/",
        "release_reports/",
        "aider.log/",
        "generated_component_docs/work/",
        "generated_component_docs/archive/",
        "tools/documentation/plan-",
        "main_computer/.main_computer_browser_profile/",
        "main_computer/debug_assets/",
        "contracts/cache/",
        "contracts/out/",
        "revision_control/",
        "tools/patching/",
    ),
    blocked_file_names=(
        ".DS_Store",
        "Thumbs.db",
        "aider.log",
        "small_aider.log",
        "solidity-files-cache.json",
    ),
    blocked_extensions=(
        ".pyc",
        ".pyo",
        ".tmp",
        ".bak",
        ".pid",
    ),
)


@dataclass(frozen=True)
class FileCodeStats:
    path: str
    language: str
    size_bytes: int
    total_lines: int
    blank_lines: int
    comment_lines: int
    code_lines: int
    todo_lines: int
    long_lines: int
    max_line_length: int
    newline_style: str
    has_trailing_newline: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "total_lines": self.total_lines,
            "blank_lines": self.blank_lines,
            "comment_lines": self.comment_lines,
            "code_lines": self.code_lines,
            "todo_lines": self.todo_lines,
            "long_lines": self.long_lines,
            "max_line_length": self.max_line_length,
            "newline_style": self.newline_style,
            "has_trailing_newline": self.has_trailing_newline,
        }


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class LanguageStats:
    language: str
    file_count: int
    size_bytes: int
    total_lines: int
    blank_lines: int
    comment_lines: int
    code_lines: int
    todo_lines: int
    long_lines: int

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "total_lines": self.total_lines,
            "blank_lines": self.blank_lines,
            "comment_lines": self.comment_lines,
            "code_lines": self.code_lines,
            "todo_lines": self.todo_lines,
            "long_lines": self.long_lines,
        }


@dataclass(frozen=True)
class CodeAnalysisReport:
    root: str
    files: tuple[FileCodeStats, ...]
    skipped: tuple[SkippedFile, ...]
    include_docs: bool
    all_text: bool
    max_file_bytes: int
    long_line_threshold: int
    export_scope: bool

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    @property
    def total_lines(self) -> int:
        return sum(item.total_lines for item in self.files)

    @property
    def blank_lines(self) -> int:
        return sum(item.blank_lines for item in self.files)

    @property
    def comment_lines(self) -> int:
        return sum(item.comment_lines for item in self.files)

    @property
    def code_lines(self) -> int:
        return sum(item.code_lines for item in self.files)

    @property
    def todo_lines(self) -> int:
        return sum(item.todo_lines for item in self.files)

    @property
    def long_lines(self) -> int:
        return sum(item.long_lines for item in self.files)

    def language_stats(self) -> tuple[LanguageStats, ...]:
        buckets: dict[str, dict[str, int]] = {}
        for item in self.files:
            bucket = buckets.setdefault(
                item.language,
                {
                    "file_count": 0,
                    "size_bytes": 0,
                    "total_lines": 0,
                    "blank_lines": 0,
                    "comment_lines": 0,
                    "code_lines": 0,
                    "todo_lines": 0,
                    "long_lines": 0,
                },
            )
            bucket["file_count"] += 1
            bucket["size_bytes"] += item.size_bytes
            bucket["total_lines"] += item.total_lines
            bucket["blank_lines"] += item.blank_lines
            bucket["comment_lines"] += item.comment_lines
            bucket["code_lines"] += item.code_lines
            bucket["todo_lines"] += item.todo_lines
            bucket["long_lines"] += item.long_lines

        return tuple(
            LanguageStats(
                language=language,
                file_count=values["file_count"],
                size_bytes=values["size_bytes"],
                total_lines=values["total_lines"],
                blank_lines=values["blank_lines"],
                comment_lines=values["comment_lines"],
                code_lines=values["code_lines"],
                todo_lines=values["todo_lines"],
                long_lines=values["long_lines"],
            )
            for language, values in sorted(
                buckets.items(),
                key=lambda item: (-item[1]["total_lines"], item[0].lower()),
            )
        )

    def top_files_by_lines(self, limit: int = 20) -> tuple[FileCodeStats, ...]:
        return tuple(
            sorted(
                self.files,
                key=lambda item: (-item.total_lines, -item.code_lines, item.path),
            )[:limit]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "summary": {
                "file_count": self.file_count,
                "skipped_count": self.skipped_count,
                "size_bytes": self.total_size_bytes,
                "total_lines": self.total_lines,
                "blank_lines": self.blank_lines,
                "comment_lines": self.comment_lines,
                "code_lines": self.code_lines,
                "todo_lines": self.todo_lines,
                "long_lines": self.long_lines,
                "include_docs": self.include_docs,
                "all_text": self.all_text,
                "max_file_bytes": self.max_file_bytes,
                "long_line_threshold": self.long_line_threshold,
                "export_scope": self.export_scope,
            },
            "rollup": build_rollup(self),
            "languages": [item.to_dict() for item in self.language_stats()],
            "files": [item.to_dict() for item in self.files],
            "skipped": [item.to_dict() for item in self.skipped],
        }


def _parse_powershell_string_array(script: str, variable_name: str) -> tuple[str, ...] | None:
    match = re.search(rf"\${re.escape(variable_name)}\s*=\s*@\((.*?)\)", script, re.DOTALL)
    if match is None:
        return None
    return tuple(re.findall(r'"([^"]*)"', match.group(1)))


def load_export_rules(root: Path) -> ExportRules:
    script_path = root / EXPORT_SCRIPT_NAME
    if not script_path.exists():
        return DEFAULT_EXPORT_RULES

    try:
        script = script_path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_EXPORT_RULES

    def values(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        parsed = _parse_powershell_string_array(script, name)
        return fallback if parsed is None else parsed

    return ExportRules(
        export_items=values("exportItems", DEFAULT_EXPORT_RULES.export_items),
        allowed_generated_exact_paths=values(
            "allowedGeneratedExactPaths",
            DEFAULT_EXPORT_RULES.allowed_generated_exact_paths,
        ),
        blocked_directory_names=values("blockedDirectoryNames", DEFAULT_EXPORT_RULES.blocked_directory_names),
        blocked_exact_paths=values("blockedExactPaths", DEFAULT_EXPORT_RULES.blocked_exact_paths),
        blocked_prefixes=values("blockedPrefixes", DEFAULT_EXPORT_RULES.blocked_prefixes),
        blocked_file_names=values("blockedFileNames", DEFAULT_EXPORT_RULES.blocked_file_names),
        blocked_extensions=values("blockedExtensions", DEFAULT_EXPORT_RULES.blocked_extensions),
    )


def normalize_repo_path(path: str | Path) -> str:
    repo_path = str(path).replace("\\", "/").strip()
    while repo_path.startswith("./"):
        repo_path = repo_path[2:]
    while repo_path.startswith("/"):
        repo_path = repo_path[1:]
    return repo_path


def _lower_set(values: Sequence[str]) -> set[str]:
    return {value.lower() for value in values}


def is_export_path_allowed(repo_path: str | Path, *, is_directory: bool = False, rules: ExportRules = DEFAULT_EXPORT_RULES) -> bool:
    normalized = normalize_repo_path(repo_path)
    name = normalized.rsplit("/", 1)[-1]
    normalized_lower = normalized.lower()

    if not normalized:
        return True

    if normalized_lower in _lower_set(rules.allowed_generated_exact_paths):
        return True

    if normalized_lower in _lower_set(rules.blocked_exact_paths):
        return False

    for prefix in rules.blocked_prefixes:
        prefix_lower = prefix.lower()
        prefix_root = prefix_lower.rstrip("/")
        if normalized_lower == prefix_root:
            return False
        if normalized_lower.startswith(prefix_lower):
            return False

    blocked_directory_names = _lower_set(rules.blocked_directory_names)
    parts = [part for part in re.split(r"/+", normalized_lower) if part]
    if any(part in blocked_directory_names for part in parts):
        return False

    if not is_directory:
        if name.lower() in _lower_set(rules.blocked_file_names):
            return False
        extension = Path(name).suffix.lower()
        if extension in _lower_set(rules.blocked_extensions):
            return False

    return True


def detect_language(path: Path, *, include_docs: bool = False, all_text: bool = False) -> str | None:
    name_lower = path.name.lower()
    if name_lower.startswith("dockerfile"):
        return "Dockerfile"
    if name_lower in SPECIAL_FILENAME_LANGUAGES:
        return SPECIAL_FILENAME_LANGUAGES[name_lower]

    suffix = path.suffix.lower()
    if suffix in SOURCE_SUFFIX_LANGUAGES:
        return SOURCE_SUFFIX_LANGUAGES[suffix]
    if include_docs and suffix in DOC_SUFFIX_LANGUAGES:
        return DOC_SUFFIX_LANGUAGES[suffix]
    if all_text:
        return DOC_SUFFIX_LANGUAGES.get(suffix, "Text")
    return None


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _starts_with_parts(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(parts) >= len(prefix) and parts[: len(prefix)] == prefix


def is_excluded_path(path: Path, root: Path, exclude_patterns: Sequence[str] = (), *, is_directory: bool = False) -> bool:
    relative = path.relative_to(root)
    lower_parts = tuple(part.lower() for part in relative.parts)
    directory_parts = lower_parts if is_directory else lower_parts[:-1]

    if any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in directory_parts):
        return True
    if any(part.startswith("diagnostics_output_") or part.startswith("harness_output_") for part in directory_parts):
        return True

    for prefix in DEFAULT_EXCLUDED_PATH_PREFIXES:
        if _starts_with_parts(lower_parts, prefix):
            return True

    relative_text = relative.as_posix()
    return any(fnmatch.fnmatch(relative_text, pattern) for pattern in exclude_patterns)


def _repo_has_export_script(root: Path) -> bool:
    return root.is_dir() and (root / EXPORT_SCRIPT_NAME).is_file()


def _iter_scan_roots(root: Path, *, export_scope: bool, rules: ExportRules) -> Iterable[Path]:
    if root.is_file() or not export_scope:
        yield root
        return

    for item in rules.export_items:
        repo_path = normalize_repo_path(item)
        if not repo_path:
            continue
        path = root / repo_path
        if not path.exists():
            continue
        if not is_export_path_allowed(repo_path, is_directory=path.is_dir(), rules=rules):
            continue
        yield path


def _iter_children(path: Path) -> Iterable[Path]:
    try:
        yield from sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return


def iter_candidate_files(
    root: Path,
    *,
    include_docs: bool = False,
    all_text: bool = False,
    exclude_patterns: Sequence[str] = (),
    export_scope: bool | None = None,
    export_rules: ExportRules | None = None,
) -> Iterable[tuple[Path, str]]:
    root = root.resolve()
    if root.is_file():
        language = detect_language(root, include_docs=include_docs, all_text=all_text)
        if language is not None:
            yield root, language
        return

    use_export_scope = _repo_has_export_script(root) if export_scope is None else export_scope
    rules = export_rules or load_export_rules(root)
    seen_roots: set[Path] = set()

    for scan_root in _iter_scan_roots(root, export_scope=use_export_scope, rules=rules):
        scan_root = scan_root.resolve()
        if scan_root in seen_roots:
            continue
        seen_roots.add(scan_root)

        if scan_root.is_file():
            relative = relative_posix(scan_root, root)
            if use_export_scope and not is_export_path_allowed(relative, is_directory=False, rules=rules):
                continue
            if is_excluded_path(scan_root, root, exclude_patterns, is_directory=False):
                continue
            language = detect_language(scan_root, include_docs=include_docs, all_text=all_text)
            if language is not None:
                yield scan_root, language
            continue

        stack = [scan_root]
        while stack:
            current = stack.pop()
            for child in _iter_children(current):
                try:
                    relative = relative_posix(child, root)
                except ValueError:
                    continue

                if child.is_dir():
                    if use_export_scope and not is_export_path_allowed(relative, is_directory=True, rules=rules):
                        continue
                    if is_excluded_path(child, root, exclude_patterns, is_directory=True):
                        continue
                    stack.append(child)
                    continue

                if not child.is_file():
                    continue
                if use_export_scope and not is_export_path_allowed(relative, is_directory=False, rules=rules):
                    continue
                if is_excluded_path(child, root, exclude_patterns, is_directory=False):
                    continue
                language = detect_language(child, include_docs=include_docs, all_text=all_text)
                if language is None:
                    continue
                yield child, language


def detect_newline_style(data: bytes) -> str:
    if not data:
        return "none"

    crlf = data.count(b"\r\n")
    without_crlf = data.replace(b"\r\n", b"")
    lf = without_crlf.count(b"\n")
    cr = without_crlf.count(b"\r")

    styles = [name for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr)) if count]
    if not styles:
        return "none"
    if len(styles) == 1:
        return styles[0]
    return "mixed"


def decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte found")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("utf-8")


def is_comment_line(stripped: str, language: str) -> bool:
    if not stripped:
        return False

    prefixes = COMMENT_PREFIXES_BY_LANGUAGE.get(language, ())
    lower_stripped = stripped.lower()
    for prefix in prefixes:
        if prefix == "rem " or prefix == "rem\t":
            if lower_stripped.startswith(prefix):
                return True
        elif stripped.startswith(prefix):
            return True
    return False


def count_todo_line(line: str) -> bool:
    return TODO_MARKER_PATTERN.search(line) is not None


def analyze_file(
    path: Path,
    root: Path,
    language: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    long_line_threshold: int = DEFAULT_LONG_LINE_THRESHOLD,
) -> FileCodeStats | SkippedFile:
    data = path.read_bytes()
    relative = relative_posix(path, root if root.is_dir() else root.parent)
    size_bytes = len(data)

    if size_bytes > max_file_bytes:
        return SkippedFile(path=relative, reason=f"larger than max_file_bytes ({max_file_bytes})", size_bytes=size_bytes)

    try:
        text = decode_text(data)
    except UnicodeDecodeError:
        return SkippedFile(path=relative, reason="not UTF-8 text", size_bytes=size_bytes)

    lines = text.splitlines()
    total_lines = len(lines)
    blank_lines = 0
    comment_lines = 0
    todo_lines = 0
    long_lines = 0
    max_line_length = 0

    for line in lines:
        stripped = line.strip()
        length = len(line)
        max_line_length = max(max_line_length, length)
        if length > long_line_threshold:
            long_lines += 1
        if not stripped:
            blank_lines += 1
        elif is_comment_line(stripped, language):
            comment_lines += 1
        if count_todo_line(line):
            todo_lines += 1

    code_lines = max(0, total_lines - blank_lines - comment_lines)
    return FileCodeStats(
        path=relative,
        language=language,
        size_bytes=size_bytes,
        total_lines=total_lines,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        code_lines=code_lines,
        todo_lines=todo_lines,
        long_lines=long_lines,
        max_line_length=max_line_length,
        newline_style=detect_newline_style(data),
        has_trailing_newline=bool(data.endswith((b"\n", b"\r"))),
    )


def _debug_printer(enabled: bool, stream: TextIO | None) -> Callable[[str], None]:
    if not enabled:
        return lambda message: None

    output = stream or sys.stderr

    def emit(message: str) -> None:
        print(f"code-stats: {message}", file=output, flush=True)

    return emit


def analyze_path(
    root: Path | str,
    *,
    include_docs: bool = False,
    all_text: bool = False,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    long_line_threshold: int = DEFAULT_LONG_LINE_THRESHOLD,
    exclude_patterns: Sequence[str] = (),
    export_scope: bool | None = None,
    debug: bool = False,
    debug_every: int = 500,
    debug_stream: TextIO | None = None,
) -> CodeAnalysisReport:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"analysis root not found: {root_path}")
    if max_file_bytes < 0:
        raise ValueError("max_file_bytes must be greater than or equal to zero")
    if long_line_threshold < 1:
        raise ValueError("long_line_threshold must be greater than zero")
    if debug_every < 1:
        raise ValueError("debug_every must be greater than zero")

    rules = load_export_rules(root_path) if root_path.is_dir() else DEFAULT_EXPORT_RULES
    use_export_scope = _repo_has_export_script(root_path) if export_scope is None else export_scope
    emit_debug = _debug_printer(debug, debug_stream)
    emit_debug(
        f"root={root_path} export_scope={'enabled' if use_export_scope else 'disabled'} "
        f"include_docs={include_docs} all_text={all_text}"
    )

    files: list[FileCodeStats] = []
    skipped: list[SkippedFile] = []
    candidates = 0

    for path, language in iter_candidate_files(
        root_path,
        include_docs=include_docs,
        all_text=all_text,
        exclude_patterns=exclude_patterns,
        export_scope=use_export_scope,
        export_rules=rules,
    ):
        candidates += 1
        result = analyze_file(
            path,
            root_path,
            language,
            max_file_bytes=max_file_bytes,
            long_line_threshold=long_line_threshold,
        )
        if isinstance(result, SkippedFile):
            skipped.append(result)
        else:
            files.append(result)

        if candidates % debug_every == 0:
            emit_debug(
                f"candidates={candidates} analyzed={len(files)} skipped={len(skipped)} "
                f"current={relative_posix(path, root_path if root_path.is_dir() else root_path.parent)}"
            )

    emit_debug(f"finished candidates={candidates} analyzed={len(files)} skipped={len(skipped)}")

    return CodeAnalysisReport(
        root=str(root_path),
        files=tuple(sorted(files, key=lambda item: item.path)),
        skipped=tuple(sorted(skipped, key=lambda item: item.path)),
        include_docs=include_docs,
        all_text=all_text,
        max_file_bytes=max_file_bytes,
        long_line_threshold=long_line_threshold,
        export_scope=use_export_scope,
    )


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def build_rollup(report: CodeAnalysisReport, *, top: int = 20) -> dict[str, object]:
    language_stats = report.language_stats()
    displayed_top = max(0, min(top, report.file_count))
    top_files = report.top_files_by_lines(displayed_top)
    top_file_lines = sum(item.total_lines for item in top_files)
    top_languages = language_stats[:3]
    top_language_lines = sum(item.total_lines for item in top_languages)
    largest_file = top_files[0] if top_files else None
    largest_language = language_stats[0] if language_stats else None

    observations: list[str] = []
    if report.file_count == 0:
        observations.append(
            "No supported source files were found with the current scope and file-type options."
        )
    else:
        scope = (
            "Export-scoped project code is enabled, so generated/build/export debris should be outside this count."
            if report.export_scope
            else "Export scope is disabled, so the scan covers every supported source file under the root after generic exclusions."
        )
        if not report.include_docs and not report.all_text:
            scope += " Documentation and arbitrary text files are excluded unless --include-docs or --all-text is used."
        observations.append(f"Scope: {scope}")

        observations.append(
            "Size: "
            f"{_format_int(report.file_count)} files contain {_format_int(report.total_lines)} total lines; "
            f"{_format_int(report.code_lines)} are counted as code "
            f"({_format_percent(report.code_lines, report.total_lines)}), "
            f"{_format_int(report.blank_lines)} as blank "
            f"({_format_percent(report.blank_lines, report.total_lines)}), and "
            f"{_format_int(report.comment_lines)} as comment-only "
            f"({_format_percent(report.comment_lines, report.total_lines)})."
        )

        if largest_language is not None:
            if len(top_languages) == 1:
                language_note = (
                    f"{largest_language.language} is the only language detected, with "
                    f"{_format_int(largest_language.total_lines)} lines."
                )
            else:
                top_names = ", ".join(item.language for item in top_languages)
                language_note = (
                    f"{largest_language.language} is the largest language at "
                    f"{_format_int(largest_language.total_lines)} lines "
                    f"({_format_percent(largest_language.total_lines, report.total_lines)}). "
                    f"The top {len(top_languages)} languages ({top_names}) cover "
                    f"{_format_percent(top_language_lines, report.total_lines)} of all lines."
                )
            observations.append(f"Language mix: {language_note}")

        if largest_file is not None:
            observations.append(
                "Concentration: "
                f"the top {displayed_top} files by line count hold {_format_int(top_file_lines)} lines "
                f"({_format_percent(top_file_lines, report.total_lines)}). "
                f"The largest file is {largest_file.path} with {_format_int(largest_file.total_lines)} lines."
            )

        observations.append(
            "Maintenance signals: "
            f"{_format_int(report.todo_lines)} TODO/FIXME/HACK/XXX lines and "
            f"{_format_int(report.long_lines)} lines over {report.long_line_threshold} characters were found. "
            "These are triage counters, not pass/fail verdicts."
        )

        observations.append(
            "Skipped files: "
            f"{_format_int(report.skipped_count)} candidate files were skipped after matching the analyzer scope. "
            "Paths pruned by export-scope or exclude rules are not counted as skipped."
        )

        observations.append(
            "Counting note: comment lines are detected with simple language-specific prefixes, "
            "so block comments and embedded comments are approximate."
        )

    return {
        "headline": (
            f"{_format_int(report.file_count)} files, "
            f"{_format_int(report.total_lines)} total lines, "
            f"{_format_int(report.code_lines)} code lines"
        ),
        "observations": observations,
    }


def build_rollup_lines(report: CodeAnalysisReport, *, top: int = 20) -> tuple[str, ...]:
    rollup = build_rollup(report, top=top)
    return tuple([str(rollup["headline"]), *[str(item) for item in rollup["observations"]]])


def format_text_report(report: CodeAnalysisReport, *, top: int = 20) -> str:
    lines = [
        f"Static code stats for {report.root}",
        f"Export scope: {'enabled' if report.export_scope else 'disabled'}",
        f"Files analyzed: {_format_int(report.file_count)}",
        f"Files skipped: {_format_int(report.skipped_count)}",
        f"Bytes analyzed: {_format_int(report.total_size_bytes)}",
        f"Total lines: {_format_int(report.total_lines)}",
        f"Code lines: {_format_int(report.code_lines)}",
        f"Blank lines: {_format_int(report.blank_lines)}",
        f"Comment lines: {_format_int(report.comment_lines)}",
        f"TODO/FIXME/HACK/XXX lines: {_format_int(report.todo_lines)}",
        f"Long lines > {report.long_line_threshold} chars: {_format_int(report.long_lines)}",
        "",
        "By language:",
    ]

    if report.file_count == 0:
        lines.append("  (no supported source files found)")
    else:
        for item in report.language_stats():
            lines.append(
                "  "
                f"{item.language}: "
                f"{_format_int(item.total_lines)} lines, "
                f"{_format_int(item.code_lines)} code, "
                f"{_format_int(item.file_count)} files"
            )

    displayed_top = max(0, min(top, report.file_count))
    lines.extend(["", f"Top files by line count ({displayed_top}):"])
    for item in report.top_files_by_lines(displayed_top):
        lines.append(
            "  "
            f"{item.path}: "
            f"{_format_int(item.total_lines)} lines, "
            f"{_format_int(item.code_lines)} code, "
            f"{item.language}"
        )

    if report.skipped:
        lines.extend(["", "Skipped files:"])
        for item in report.skipped[:displayed_top]:
            lines.append(f"  {item.path}: {item.reason} ({_format_int(item.size_bytes)} bytes)")
        remaining_skipped = len(report.skipped) - displayed_top
        if remaining_skipped > 0:
            lines.append(f"  ... {_format_int(remaining_skipped)} more")

    lines.extend(["", "Rollup:"])
    for item in build_rollup_lines(report, top=displayed_top):
        lines.append(f"  {item}")

    return "\n".join(lines)


def report_to_json(report: CodeAnalysisReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def run_from_args(args: argparse.Namespace) -> CodeAnalysisReport:
    return analyze_path(
        args.root,
        include_docs=args.include_docs,
        all_text=args.all_text,
        max_file_bytes=args.max_file_bytes,
        long_line_threshold=args.long_line_threshold,
        exclude_patterns=tuple(args.exclude or ()),
        export_scope=False if getattr(args, "no_export_scope", False) else None,
        debug=bool(getattr(args, "debug", False) or getattr(args, "verbose", False)),
        debug_every=getattr(args, "debug_every", 500),
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", nargs="?", type=Path, default=Path("."), help="Repository or file to analyze.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    parser.add_argument("--output", type=Path, help="Optional output file. Defaults to stdout.")
    parser.add_argument("--top", type=int, default=20, help="Number of top files/skipped files to show in text output.")
    parser.add_argument("--include-docs", action="store_true", help="Include Markdown/reStructuredText/Text files.")
    parser.add_argument("--all-text", action="store_true", help="Try to analyze every non-excluded text file.")
    parser.add_argument(
        "--no-export-scope",
        action="store_true",
        help="Scan the provided root directly instead of limiting repository scans to export-main-computer-test.ps1.",
    )
    parser.add_argument("--debug", action="store_true", help="Emit progress/debug information to stderr while scanning.")
    parser.add_argument("--verbose", action="store_true", help="Alias for --debug.")
    parser.add_argument(
        "--debug-every",
        type=int,
        default=500,
        help="Emit a progress line after this many candidate source files when --debug is enabled.",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help=f"Skip supported files larger than this many bytes. Defaults to {DEFAULT_MAX_FILE_BYTES}.",
    )
    parser.add_argument(
        "--long-line-threshold",
        type=int,
        default=DEFAULT_LONG_LINE_THRESHOLD,
        help=f"Count lines longer than this many characters. Defaults to {DEFAULT_LONG_LINE_THRESHOLD}.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional repository-relative glob to exclude. May be passed more than once.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze static code statistics for a repository.")
    add_arguments(parser)
    return parser


def emit_report(report: CodeAnalysisReport, *, output_format: str, output: Path | None, top: int) -> None:
    payload = report_to_json(report) if output_format == "json" else format_text_report(report, top=top)
    if output is None:
        print(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv or sys.argv[1:]))
    report = run_from_args(args)
    emit_report(report, output_format=args.format, output=args.output, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
