from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


PROJECT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "composer.json",
    "Gemfile",
)

PRIORITY_PROJECTS = (
    "main_computer",
    "main_computer_test",
    "main_copmputer_production",
    "holographic_plate_bundle",
)

MANIFEST_EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    "debug_assets",
    "debug_asset_revisions",
    "diagnostics_output",
    "diagnostics_output_functional_gemma4",
    "diagnostics_output_functional_gemma4_26b",
    "diagnostics_output_live_gemma4",
    "diagnostics_output_server",
    "diagnostics_output_viewport",
    "diagnostics_output_widgets",
    "energy_credits",
    "harness_output",
    "revision_control",
}

MANIFEST_FILE_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
}

PINNED_CONTEXT_FILENAMES = ("README.md", "TODO.md", "missing.txt")

CONTEXT_STOPWORDS = {
    "about",
    "also",
    "and",
    "are",
    "answer",
    "context",
    "does",
    "find",
    "from",
    "keep",
    "list",
    "load",
    "project",
    "projects",
    "say",
    "short",
    "supplied",
    "that",
    "the",
    "this",
    "visible",
    "whether",
    "workspace",
}


# File excerpt retrieval is intentionally narrower than project matching.
# Natural text-console control requests often contain broad words such as
# "computer", "terminal", "mount", and "files".  Those words describe the
# control surface, not a source file.  Treating them as file-match terms caused
# the chat console to attach large, irrelevant excerpts from nearly every path
# under main_computer, exhausting small Ollama context windows before the model
# had room to answer.
CONTEXT_FILE_STOPWORDS = CONTEXT_STOPWORDS | {
    "action",
    "actions",
    "command",
    "commands",
    "computer",
    "describe",
    "directory",
    "directories",
    "file",
    "files",
    "folder",
    "folders",
    "into",
    "just",
    "local",
    "main",
    "mount",
    "mounted",
    "not",
    "request",
    "requested",
    "run",
    "running",
    "runs",
    "terminal",
    "terminals",
    "use",
    "using",
    "with",
}


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    path: Path
    markers: tuple[str, ...]
    child_count: int
    file_count: int


@dataclass(frozen=True)
class ContextEvidence:
    kind: str
    path: str
    reason: str
    chars: int = 0


@dataclass(frozen=True)
class WorkspaceContextPack:
    query: str
    text: str
    evidence: tuple[ContextEvidence, ...]
    manifest_chars: int


class ProjectCatalog:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def list_projects(self) -> list[ProjectInfo]:
        if not self.workspace.exists():
            return []

        projects: list[ProjectInfo] = []
        for child in sorted(self.workspace.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            projects.append(self.inspect(child.name))
        return projects

    def inspect(self, name: str) -> ProjectInfo:
        path = self.workspace / name
        if not path.exists() or not path.is_dir():
            raise KeyError(f"No project folder named {name!r} under {self.workspace}")

        markers = tuple(marker for marker in PROJECT_MARKERS if (path / marker).exists())
        child_count = 0
        file_count = 0
        for item in path.iterdir():
            if item.is_dir():
                child_count += 1
            elif item.is_file():
                file_count += 1

        return ProjectInfo(
            name=path.name,
            path=path,
            markers=markers,
            child_count=child_count,
            file_count=file_count,
        )

    def main_computer_manifest(self, max_files_per_project: int = 80) -> str:
        lines = ["Main computer file manifest:"]
        for project_name in PRIORITY_PROJECTS:
            project_path = self.workspace / project_name
            if not project_path.is_dir():
                lines.append(f"- {project_name}: missing")
                continue
            files = self._manifest_files(project_path, max_files=max_files_per_project)
            lines.append(f"- {project_name}:")
            if not files:
                lines.append("  - no source files found")
                continue
            for file_path in files:
                lines.append(f"  - {file_path.as_posix()}")
        return "\n".join(lines)

    def build_context_pack(
        self,
        query: str,
        *,
        max_matches: int = 12,
        max_excerpt_chars: int = 1200,
    ) -> WorkspaceContextPack:
        manifest = self.main_computer_manifest()
        projects = self.list_projects()
        terms = self._context_terms(query)
        evidence: list[ContextEvidence] = []
        lines = [
            "Deterministic workspace context pack:",
            f"Workspace root: {self.workspace}",
            "Priority main computer projects:",
        ]
        for project_name in PRIORITY_PROJECTS:
            status = "present" if (self.workspace / project_name).is_dir() else "missing"
            lines.append(f"- {project_name}: {status}")
            evidence.append(ContextEvidence("project", project_name, status))

        project_terms = [term for term in terms if "." not in term and (len(term) >= 5 or "_" in term or "-" in term)]
        matched_projects = [
            project for project in projects if any(term in project.name.lower() for term in project_terms)
        ]
        if matched_projects:
            lines.append("")
            lines.append("Matched projects:")
            for project in matched_projects[:max_matches]:
                markers = ", ".join(project.markers) if project.markers else "no root marker"
                lines.append(f"- {project.name} ({markers})")
                evidence.append(ContextEvidence("project", project.name, "matched query"))

        pinned_files = self._pinned_context_files()
        if pinned_files:
            lines.append("")
            lines.append("Pinned project guidance:")
            for path in pinned_files:
                relative = path.relative_to(self.workspace).as_posix()
                lines.append(f"- {relative}")
                evidence.append(ContextEvidence("pinned_file", relative, "always included"))

            lines.append("")
            lines.append("Pinned guidance excerpts:")
            for path in pinned_files:
                relative = path.relative_to(self.workspace).as_posix()
                excerpt = self._read_context_excerpt(path, max_chars=max_excerpt_chars)
                evidence.append(ContextEvidence("pinned_excerpt", relative, "always included excerpt", len(excerpt)))
                lines.append(f"--- {relative} ---")
                lines.append(excerpt if excerpt else "[no readable excerpt]")

        file_terms = self._context_file_terms(terms)
        matched_files = self._context_file_matches(file_terms, max_matches=max_matches)
        if matched_files:
            lines.append("")
            lines.append("Matched files:")
            for path in matched_files:
                relative = path.relative_to(self.workspace).as_posix()
                lines.append(f"- {relative}")
                evidence.append(ContextEvidence("file", relative, "matched query"))

            lines.append("")
            lines.append("Matched file excerpts:")
            for path in matched_files:
                relative = path.relative_to(self.workspace).as_posix()
                excerpt = self._read_context_excerpt(path, max_chars=max_excerpt_chars)
                evidence.append(ContextEvidence("excerpt", relative, "attached excerpt", len(excerpt)))
                lines.append(f"--- {relative} ---")
                lines.append(excerpt if excerpt else "[no readable excerpt]")

        lines.append("")
        lines.append(manifest)
        return WorkspaceContextPack(
            query=query,
            text="\n".join(lines),
            evidence=tuple(evidence),
            manifest_chars=len(manifest),
        )

    def _pinned_context_files(self) -> list[Path]:
        project_root = self._primary_context_project()
        if project_root is None:
            return []

        files: list[Path] = []
        for filename in PINNED_CONTEXT_FILENAMES:
            candidate = project_root / filename
            if candidate.is_file():
                files.append(candidate)
        return files

    def _primary_context_project(self) -> Path | None:
        workspace_resolved = self.workspace.resolve()
        cwd = Path.cwd().resolve()
        for candidate in (cwd, *cwd.parents):
            if candidate.parent == workspace_resolved and candidate.is_dir():
                return candidate

        for name in PRIORITY_PROJECTS:
            candidate = self.workspace / name
            if candidate.is_dir():
                return candidate
        return None

    def _manifest_files(self, project_path: Path, max_files: int) -> list[Path]:
        files: list[Path] = []
        for path in sorted(project_path.rglob("*"), key=lambda p: p.relative_to(project_path).as_posix().lower()):
            relative = path.relative_to(project_path)
            parts = relative.parts
            if any(self._is_manifest_excluded_dir(part) for part in parts[:-1]):
                continue
            if path.is_dir():
                continue
            if path.suffix.lower() not in MANIFEST_FILE_SUFFIXES:
                continue
            if path.name.endswith(".log"):
                continue
            files.append(relative)
            if len(files) >= max_files:
                break
        return files

    def _context_terms(self, query: str) -> list[str]:
        lowered = query.lower()
        terms: list[str] = []
        for raw_term in re.findall(r"[a-z0-9_.-]+", lowered):
            term = raw_term.strip("._-")
            if len(term) >= 3 and term not in CONTEXT_STOPWORDS:
                terms.append(term)
        if "todo" in lowered:
            terms.append("todo.md")
        if "readme" in lowered or "read me" in lowered:
            terms.append("readme.md")
        if "viewport" in lowered:
            terms.append("viewport.py")
        if "diagnostic" in lowered:
            terms.append("diagnostics.py")
        if "main computer" in lowered:
            terms.extend(PRIORITY_PROJECTS)
        priority = ["main_computer", "main_computer_test", "main_copmputer_production"]
        ordered: list[str] = []
        for term in [*priority, *terms]:
            if term and term not in ordered:
                ordered.append(term)
        return ordered

    def _context_file_terms(self, terms: list[str]) -> list[str]:
        """Return query terms that are safe to use for source-file excerpt lookup.

        Project names and broad control-surface words are useful for choosing
        project-level context, but they are too noisy for file excerpt lookup.
        Matching those terms against full project-prefixed paths can attach a
        wall of unrelated files to ordinary chat-console control requests.
        """

        filtered: list[str] = []
        for term in terms:
            normalized = str(term or "").strip().lower()
            if not normalized:
                continue
            if normalized in PRIORITY_PROJECTS or normalized in CONTEXT_FILE_STOPWORDS:
                continue
            if normalized not in filtered:
                filtered.append(normalized)
        return filtered

    def _context_file_matches(self, terms: list[str], *, max_matches: int) -> list[Path]:
        matches: list[Path] = []
        roots = [self.workspace / name for name in PRIORITY_PROJECTS if (self.workspace / name).is_dir()]
        if not terms:
            return matches
        for root in roots:
            for relative in self._manifest_files(root, max_files=200):
                absolute = root / relative
                # Match only the path inside the project root.  Including the
                # project directory name (for example "main_computer/") made a
                # request containing "computer" match nearly every file.
                haystack = relative.as_posix().lower()
                if any(term in haystack for term in terms):
                    matches.append(absolute)
                    if len(matches) >= max_matches:
                        return matches
        return matches

    def _read_context_excerpt(self, path: Path, *, max_chars: int) -> str:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "[binary or non-UTF-8 file]"
        except OSError as exc:
            return f"[could not read file: {exc}]"
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n[excerpt truncated]"

    def _is_manifest_excluded_dir(self, name: str) -> bool:
        lowered = name.lower()
        return (
            lowered in MANIFEST_EXCLUDED_DIRS
            or lowered.startswith("harness_output")
            or lowered.startswith("diagnostics_output")
            or lowered.startswith("tmp")
        )

    def context_summary(self, limit: int = 80) -> str:
        projects = self.list_projects()
        priority_names = set(PRIORITY_PROJECTS)
        priority_projects = [project for project in projects if project.name in priority_names]
        remaining_projects = [project for project in projects if project.name not in priority_names]
        visible_projects = priority_projects + remaining_projects[: max(0, limit - len(priority_projects))]
        lines = [
            f"Workspace: {self.workspace}",
            f"Visible project folders: {len(projects)}",
            "Projects:",
        ]
        for project in visible_projects:
            markers = ", ".join(project.markers) if project.markers else "no root marker"
            lines.append(f"- {project.name} ({markers})")
        hidden_count = max(0, len(projects) - len(visible_projects))
        if hidden_count:
            lines.append(f"- ... {hidden_count} more")
        lines.append("")
        lines.append(self.main_computer_manifest())
        return "\n".join(lines)
