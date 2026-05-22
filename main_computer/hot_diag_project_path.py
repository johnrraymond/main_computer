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
    except Exception as exc:
        return -999, "", f"{type(exc).__name__}: {exc}"


def section(name: str) -> None:
    print("\n" + "=" * 88)
    print(name)
    print("=" * 88)


def git_probe() -> None:
    section("GIT STATE FOR SCRIPT-DERIVED REPO ROOT")
    print("repo root:", ROOT)
    print("python:", sys.executable)

    for label, args in [
        ("root", ["git", "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "branch", "--show-current"]),
        ("HEAD verify", ["git", "rev-parse", "--verify", "HEAD"]),
        ("status branch", ["git", "status", "--short", "--branch"]),
        ("staged files", ["git", "diff", "--cached", "--name-only"]),
        ("selected .dockerignore", ["git", "status", "--porcelain=v1", "--", ".dockerignore"]),
    ]:
        rc, out, err = run(args)
        print(f"\n[{label}] rc={rc}")
        if out:
            print(out.rstrip())
        if err:
            print("STDERR:", err.rstrip())


def search_text_files() -> None:
    section("SEARCH FOR FOREIGN ABSOLUTE PATH REFERENCES TO THIS REPO")
    exts = {
        ".py", ".js", ".json", ".html", ".css", ".md", ".txt", ".yml", ".yaml",
        ".toml", ".ps1", ".bat", ".cmd", ".cfg", ".ini",
    }
    skip_dirs = {
        ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
        "dist", "build", ".mypy_cache", ".ruff_cache",
    }

    hits: list[tuple[str, str, list[int]]] = []

    for path in ROOT.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for candidate in find_foreign_repo_path_refs(text):
            rel = path.relative_to(ROOT)
            line_nums = [
                idx for idx, line in enumerate(text.splitlines(), 1)
                if candidate in line or normalized_path_text(candidate) in normalized_path_text(line)
            ][:10]
            hits.append((str(rel), candidate, line_nums))

    if not hits:
        print("No foreign absolute repo-root references found in normal text files under this repo.")
    else:
        print("FOREIGN ABSOLUTE REPO PATH HITS FOUND:")
        for rel, candidate, lines in hits:
            print(f"- {rel}")
            print(f"  path: {candidate}")
            print(f"  lines: {lines}")


def inspect_known_config_locations() -> None:
    section("LIKELY PROJECT / UI CONFIG FILES")

    candidates = [
        ROOT / "main_computer" / "config",
        ROOT / "hub_configuration.json",
        ROOT / "main_computer" / "config.py",
        ROOT / "main_computer" / "viewport_state.py",
        ROOT / "main_computer" / "viewport_routes_git.py",
        ROOT / "main_computer" / "git_panel_runner.py",
        ROOT / "main_computer" / "git_commit.py",
        ROOT / "main_computer" / "web" / "applications" / "scripts" / "git-tools.js",
        ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "git-tools.js",
    ]

    for item in candidates:
        print("\n-", item.relative_to(ROOT) if item.exists() else item)
        if not item.exists():
            print("  missing")
            continue
        if item.is_dir():
            for child in sorted(item.glob("*"))[:50]:
                print("  ", child.relative_to(ROOT))
        else:
            try:
                text = item.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                print("  unreadable:", exc)
                continue

            foreign_refs = find_foreign_repo_path_refs(text)
            current_refs = [
                ref for ref in CURRENT_ROOT_BITS
                if ref and (ref in text or normalized_path_text(ref) in normalized_path_text(text))
            ]
            print("  contains current repo root:", bool(current_refs))
            print("  contains foreign absolute repo root:", bool(foreign_refs))
            for ref in foreign_refs[:10]:
                print("  foreign path:", ref)


def main() -> int:
    git_probe()
    inspect_known_config_locations()
    search_text_files()

    section("INTERPRETATION")
    print("If staged files is empty here, this checkout is safe for a selected-file first commit.")
    print("If the app still reports many staged files, the commit job is using a different repo root at runtime.")
    print("The next fix should log/patch the backend job creation path, not substitute personal path literals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
