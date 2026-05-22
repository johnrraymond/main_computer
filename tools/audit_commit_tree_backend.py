#!/usr/bin/env python3
from __future__ import annotations

import collections
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_porcelain_paths(raw: str) -> list[str]:
    paths: list[str] = []
    entries = [item for item in raw.split("\0") if item and not item.startswith("# ")]
    index = 0
    while index < len(entries):
        entry = entries[index]
        kind = entry[:1]

        if kind in {"?", "!"}:
            paths.append(entry[2:])
        elif kind == "1":
            parts = entry.split(" ", 8)
            paths.append(parts[8] if len(parts) > 8 else entry.split(" ")[-1])
        elif kind == "2":
            parts = entry.split(" ", 9)
            paths.append(parts[9] if len(parts) > 9 else entry.split(" ")[-1])
            index += 1  # old path follows in porcelain v2 -z rename records
        elif kind == "u":
            parts = entry.split(" ", 10)
            paths.append(parts[10] if len(parts) > 10 else entry.split(" ")[-1])

        index += 1

    return [path.replace("\\", "/") for path in paths if path]


def load_git_dirty(root: Path):
    module_path = root / "git_dirty.py"
    if not module_path.exists():
        raise SystemExit(f"Cannot find {module_path}")

    spec = importlib.util.spec_from_file_location("git_dirty_audit_target", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot import {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarize_paths(paths: list[str]) -> dict[str, Any]:
    collapsed_dirs = [path for path in paths if path.endswith("/")]
    roots = collections.Counter((path.split("/", 1)[0] if "/" in path else "(repo root file)") for path in paths)
    return {
        "count": len(paths),
        "collapsedDirCount": len(collapsed_dirs),
        "collapsedDirFirst20": collapsed_dirs[:20],
        "rootCounts": dict(roots.most_common(30)),
        "first50": paths[:50],
    }


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    normal = run_git(root, ["status", "--porcelain=v2", "-z", "--branch"])
    full = run_git(root, ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all"])

    normal_paths = parse_porcelain_paths(normal.stdout)
    full_paths = parse_porcelain_paths(full.stdout)

    report: dict[str, Any] = {
        "repo": str(root),
        "gitStatusNormalReturnCode": normal.returncode,
        "gitStatusFullReturnCode": full.returncode,
        "normalStatus": summarize_paths(normal_paths),
        "fullStatusUntrackedAll": summarize_paths(full_paths),
        "statusDeltaFullMinusNormal": len(full_paths) - len(normal_paths),
        "backend": {},
    }

    try:
        gd = load_git_dirty(root)
        collected = gd.collect_status(root)
        files = collected.get("files") or []
        file_paths = [str(item.get("path") or "").replace("\\", "/") for item in files if item.get("path")]

        review = gd.commit_review_payload(
            Path((collected.get("git_detection") or {}).get("worktree_root") or root),
            collected.get("git_detection") or {},
            files,
            commit_identity=collected.get("commit_identity"),
        )

        groups = review.get("candidate_groups") or {}
        group_counts = {key: len(value or []) for key, value in groups.items()}
        group_paths = {
            key: [str(item.get("path") or "").replace("\\", "/") for item in (value or []) if item.get("path")]
            for key, value in groups.items()
        }

        report["backend"] = {
            "collectStatusFileCount": len(files),
            "collectStatusSummary": collected.get("summary"),
            "collectStatusPathSummary": summarize_paths(file_paths),
            "candidateGroupCounts": group_counts,
            "candidateGroupTotal": sum(group_counts.values()),
            "blockedFirst20": group_paths.get("blocked_possible_secrets", [])[:20],
            "reviewFirst20": group_paths.get("review_before_selecting", [])[:20],
            "selectedFirst20": group_paths.get("selected_by_default", [])[:20],
            "excludedFirst20": group_paths.get("excluded_generated_runtime", [])[:20],
        }

        report["verdicts"] = {
            "backendUsesFullUntrackedPathsLikely": len(files) >= len(full_paths) or len(full_paths) == len(normal_paths),
            "backendMayStillBeCollapsed": len(full_paths) > len(file_paths),
            "normalGitStatusCollapsedDirs": bool(report["normalStatus"]["collapsedDirCount"]),
            "fullGitStatusCollapsedDirs": bool(report["fullStatusUntrackedAll"]["collapsedDirCount"]),
        }

    except Exception as exc:
        report["backendError"] = repr(exc)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())