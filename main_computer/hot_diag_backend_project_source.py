from __future__ import annotations

import json
import os
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


def env_path(name: str, fallback: Path | None = None) -> Path | None:
    value = os.environ.get(name, "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return fallback


def all_path_display_variants(paths: list[Path | None]) -> list[str]:
    variants: set[str] = set()
    for path in paths:
        if path is None:
            continue
        variants.update(path_display_variants(path))
    return sorted(variants, key=len, reverse=True)


APP_ROOT = env_path("MAIN_COMPUTER_APP_ROOT", find_repo_root_from_script())
TARGET_ROOT = env_path("MAIN_COMPUTER_TARGET_ROOT", APP_ROOT)
STALE_ROOT = env_path("MAIN_COMPUTER_STALE_ROOT")


def say(*parts: object) -> None:
    print(*parts, flush=True)


def section(title: str) -> None:
    say("\n" + "=" * 92)
    say(title)
    say("=" * 92)


def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(cwd),
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


def git_summary(label: str, root: Path) -> None:
    section(f"GIT SUMMARY: {label}")
    say("path:", root)
    if not root.exists():
        say("missing")
        return

    probes = [
        ("root", ["git", "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "branch", "--show-current"]),
        ("HEAD", ["git", "rev-parse", "--verify", "HEAD"]),
        ("staged", ["git", "diff", "--cached", "--name-only"]),
        ("dockerignore", ["git", "status", "--porcelain=v1", "--", ".dockerignore"]),
    ]

    for name, cmd in probes:
        rc, out, err = run(cmd, root)
        say(f"\n[{name}] rc={rc}")
        if out.strip():
            say(out.rstrip()[:4000])
        if err.strip():
            say("STDERR:", err.rstrip()[:4000])


def read_text(path: Path, max_bytes: int = 1_500_000) -> str | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return f"<<SKIPPED LARGE FILE {path.stat().st_size} bytes>>"
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<<READ ERROR {type(exc).__name__}: {exc}>>"


def show_matching_lines(path: Path, needles: list[str]) -> None:
    text = read_text(path)
    rel = path
    try:
        rel = path.relative_to(APP_ROOT)
    except Exception:
        pass

    say(f"\n- {rel}")
    if text is None:
        say("  missing")
        return
    if text.startswith("<<"):
        say(" ", text)
        return

    hit = False
    lines = text.splitlines()

    for idx, line in enumerate(lines, 1):
        if any(needle in line for needle in needles):
            hit = True
            say(f"  L{idx}: {line[:500]}")

    if not hit:
        say("  no direct hits")


def inspect_backend_code() -> None:
    section("BACKEND CODE HOTSPOTS")

    files = [
        APP_ROOT / "main_computer" / "git_commit.py",
        APP_ROOT / "main_computer" / "git_panel_runner.py",
        APP_ROOT / "main_computer" / "git_tools.py",
        APP_ROOT / "main_computer" / "viewport_routes_git.py",
        APP_ROOT / "main_computer" / "viewport_state.py",
        APP_ROOT / "main_computer" / "web" / "applications" / "scripts" / "git-tools.js",
        APP_ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "git-tools.js",
        APP_ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "git-task-state.js",
    ]

    needles = [
        "project_path",
        "repo_path",
        "selected",
        "commit",
        "selected-file",
        "selected_file",
        "git_commit",
        "GitCommitRunner",
        "start",
        "run",
    ]

    for path in files:
        show_matching_lines(path, needles)


def find_recent_state_files() -> None:
    section("RECENT APP STATE / CONFIG FILES WITH PROJECT PATHS")

    roots = [
        APP_ROOT,
        APP_ROOT / "main_computer",
        APP_ROOT / "main_computer" / "config",
        Path.home(),
    ]

    names_or_suffixes = {
        ".json",
        ".log",
        ".txt",
        ".state",
        ".cache",
    }

    hard_names = {
        "hub_configuration.json",
        "code_editor_viewport_snap.json",
        "schedules.json",
    }

    path_needles = all_path_display_variants([APP_ROOT, TARGET_ROOT, STALE_ROOT])

    checked = 0
    hits = []

    for root in roots:
        if not root.exists():
            continue

        say("scanning:", root)

        stack = [root]
        while stack and checked < 800:
            p = stack.pop()

            try:
                if p.name in {".git", ".venv", "__pycache__", "node_modules", "vendor", "generated_component_docs"}:
                    continue

                if p.is_dir():
                    children = sorted(p.iterdir(), key=lambda x: x.name.lower())
                    stack.extend(children[:120])
                    continue

                if p.suffix.lower() not in names_or_suffixes and p.name not in hard_names:
                    continue

                checked += 1
                text = read_text(p, max_bytes=700_000)
                if not text or text.startswith("<<"):
                    continue

                matched = [needle for needle in path_needles if needle in text]
                if matched:
                    hits.append((p, matched[:5]))

            except Exception:
                continue

    say("checked files:", checked)

    if not hits:
        say("No project-path hits found in bounded state/config scan.")
        return

    for path, matched in hits[:80]:
        say("\nHIT:", path)
        for needle in matched:
            say("  contains:", needle)


def inspect_running_processes() -> None:
    section("PYTHON PROCESSES THAT MAY BE RUNNING THE APP")

    try:
        cp = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'python|py.exe' } | "
                "Select-Object ProcessId,CommandLine | "
                "ConvertTo-Json -Depth 3",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        say("process inspection failed:", type(exc).__name__, exc)
        return

    say("rc:", cp.returncode)
    if cp.stderr.strip():
        say("stderr:", cp.stderr.strip())

    out = cp.stdout.strip()
    if not out:
        say("No python processes found or PowerShell returned no output.")
        return

    say(out[:10000])


def main() -> int:
    section("BACKEND PROJECT-PATH SOURCE DIAGNOSTIC")
    say("app/backend root:", APP_ROOT)
    say("comparison target repo:", TARGET_ROOT)
    if STALE_ROOT is not None:
        say("explicit stale/foreign root:", STALE_ROOT)

    git_summary("comparison target repo", TARGET_ROOT)
    if APP_ROOT != TARGET_ROOT:
        git_summary("app/backend repo, inspect only", APP_ROOT)
    inspect_backend_code()
    find_recent_state_files()
    inspect_running_processes()

    section("INTERPRETATION")
    say("If the comparison target has empty staged output, Git is not the cause there.")
    say("If app/backend state or running process command lines mention a foreign repo path, the commit job is using stale project selection.")
    say("Patch target should be the backend code receiving project_path/repo_path, not a literal path substitution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
