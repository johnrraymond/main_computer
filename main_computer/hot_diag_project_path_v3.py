from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def find_repo_root_from_script(script_file: str = __file__) -> Path:
    """Find the repository root by walking upward from this script file."""

    start = Path(script_file).resolve()
    for candidate in (start.parent, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "main_computer").is_dir():
            return candidate
    raise RuntimeError(f"Could not find repo root above script: {start}")


def normalized_path_text(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def path_display_variants(path: Path) -> set[str]:
    resolved = path.resolve()
    normalized = normalized_path_text(resolved)
    variants = {normalized, str(resolved)}

    if len(normalized) >= 3 and normalized[1:3] == ":/":
        drive = normalized[0].lower()
        tail = normalized[3:]
        variants.add(f"/mnt/{drive}/{tail}")

    if normalized.startswith("/mnt/") and len(normalized) > 7 and normalized[6] == "/":
        drive = normalized[5].upper()
        tail = normalized[7:]
        variants.add(f"{drive}:/{tail}")
        variants.add(f"{drive}:\\" + tail.replace("/", "\\"))

    return variants


def clean_path_candidate(candidate: str) -> str:
    return candidate.strip().rstrip(".,;:)]}\"'")


def candidate_is_under_current_root(candidate: str, current_variants: set[str]) -> bool:
    normalized = normalized_path_text(clean_path_candidate(candidate))
    for variant in current_variants:
        root = normalized_path_text(variant).rstrip("/")
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False


ROOT = find_repo_root_from_script()
CURRENT_ROOT_BITS = path_display_variants(ROOT)
REPO_ABSOLUTE_PATH_RE = re.compile(
    rf"(?:[A-Za-z]:[\\/]|/mnt/[a-z]/|/)[^\s\"'<>`]*{re.escape(ROOT.name)}[^\s\"'<>`]*",
    re.IGNORECASE,
)


LIKELY_FILES = [
    "hub_configuration.json",
    "main_computer/config.py",
    "main_computer/viewport_state.py",
    "main_computer/viewport_routes_git.py",
    "main_computer/git_panel_runner.py",
    "main_computer/git_commit.py",
    "main_computer/git_tools.py",
    "main_computer/web/applications/scripts/git-tools.js",
    "main_computer/web/applications/scripts/dom-bindings/git-tools.js",
    "main_computer/web/applications/scripts/dom-bindings/git-task-state.js",
    "main_computer/config/code_editor_viewport_snap.json",
]


LIKELY_DIRS = [
    "main_computer/config",
    "main_computer/web/applications/scripts/dom-bindings",
]


def find_foreign_repo_path_refs(text: str) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for match in REPO_ABSOLUTE_PATH_RE.finditer(text):
        candidate = clean_path_candidate(match.group(0))
        if candidate_is_under_current_root(candidate, CURRENT_ROOT_BITS):
            continue
        key = normalized_path_text(candidate)
        if key not in seen:
            seen.add(key)
            hits.append(candidate)
    return hits


def say(*args: object) -> None:
    print(*args, flush=True)


def section(title: str) -> None:
    say("\n" + "=" * 88)
    say(title)
    say("=" * 88)


def run(args: list[str]) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        return -998, "", "TIMEOUT"
    except Exception as exc:
        return -999, "", f"{type(exc).__name__}: {exc}"


def git_state() -> None:
    section("GIT STATE: SCRIPT-DERIVED TARGET REPO")
    say("repo root:", ROOT)
    say("python:", sys.executable)

    probes = [
        ("root", ["git", "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "branch", "--show-current"]),
        ("HEAD verify", ["git", "rev-parse", "--verify", "HEAD"]),
        ("staged files", ["git", "diff", "--cached", "--name-only"]),
        ("selected .dockerignore", ["git", "status", "--porcelain=v1", "--", ".dockerignore"]),
    ]

    for label, cmd in probes:
        rc, out, err = run(cmd)
        say(f"\n[{label}] rc={rc}")
        if out.strip():
            say(out.rstrip())
        if err.strip():
            say("STDERR:", err.rstrip())


def read_small(path: Path, max_bytes: int = 2_000_000) -> str | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        size = path.stat().st_size
        if size > max_bytes:
            return f"<<SKIPPED LARGE FILE {size} bytes>>"
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<<READ ERROR {type(exc).__name__}: {exc}>>"


def report_file(path: Path) -> None:
    rel = path.relative_to(ROOT) if path.is_absolute() and path.exists() else path
    text = read_small(path)

    say(f"\n- {rel}")
    if text is None:
        say("  missing")
        return

    if text.startswith("<<"):
        say(" ", text)
        return

    foreign_hits = find_foreign_repo_path_refs(text)
    current_hits = [
        ref for ref in CURRENT_ROOT_BITS
        if ref and (ref in text or normalized_path_text(ref) in normalized_path_text(text))
    ]

    say("  contains current repo root:", bool(current_hits))
    say("  contains foreign absolute repo root:", bool(foreign_hits))

    for candidate in foreign_hits[:10]:
        lines = [
            idx for idx, line in enumerate(text.splitlines(), 1)
            if candidate in line or normalized_path_text(candidate) in normalized_path_text(line)
        ][:10]
        say("  FOREIGN path:", candidate)
        say("  lines:", lines)


def targeted_file_scan() -> None:
    section("TARGETED FILE SCAN: NO FULL RECURSION")

    for rel in LIKELY_FILES:
        report_file(ROOT / rel)

    section("TARGETED DIRECTORY LISTINGS")

    for rel in LIKELY_DIRS:
        path = ROOT / rel
        say(f"\n- {rel}")
        if not path.exists():
            say("  missing")
            continue
        if not path.is_dir():
            say("  not a directory")
            continue

        children = sorted(path.iterdir(), key=lambda p: p.name.lower())
        for child in children[:80]:
            say(" ", child.relative_to(ROOT))
        if len(children) > 80:
            say(f"  ... {len(children) - 80} more omitted")


def search_recent_runtime_logs() -> None:
    section("BOUNDED LOG / STATE FILE SEARCH")

    candidate_roots = [
        ROOT,
        ROOT / "main_computer",
        ROOT / "main_computer" / "config",
        ROOT / "main_computer" / "web",
    ]

    allowed_suffixes = {".log", ".json", ".txt", ".state", ".cache"}
    max_files = 300
    checked = 0
    hits: list[tuple[Path, list[str]]] = []

    for base in candidate_roots:
        if not base.exists():
            continue

        say("scanning bounded root:", base)
        stack = [base]

        while stack and checked < max_files:
            current = stack.pop()

            try:
                if current.name in {".git", ".venv", "__pycache__", "vendor", "node_modules"}:
                    continue

                if current.is_dir():
                    for child in list(current.iterdir())[:100]:
                        stack.append(child)
                    continue

                if current.suffix.lower() not in allowed_suffixes:
                    continue

                checked += 1
                text = read_small(current, max_bytes=500_000)
                if not text or text.startswith("<<"):
                    continue

                foreign_hits = find_foreign_repo_path_refs(text)
                if foreign_hits:
                    hits.append((current.relative_to(ROOT), foreign_hits[:5]))

            except Exception as exc:
                say("scan warning:", current, type(exc).__name__, exc)

    say("checked_files:", checked)

    if not hits:
        say("No foreign absolute repo-root references found in bounded log/state scan.")
    else:
        say("Foreign absolute repo path hits:")
        for path, candidates in hits:
            say("-", path)
            for candidate in candidates:
                say("  path:", candidate)


def main() -> int:
    section("HOT DIAG PROJECT PATH V3")
    say("This diagnostic avoids full recursive repo scanning.")
    say("Repository root is derived from this script location.")
    say("repo root:", ROOT)

    git_state()
    targeted_file_scan()
    search_recent_runtime_logs()

    section("READ THIS")
    say("If staged files is empty here, selected-file commit should not reject because of staged files.")
    say("If the app still prints a huge staged-file list, the backend commit job is using another repo path.")
    say("Next code fix should add backend logging right where the commit job receives project_path/repo_path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
