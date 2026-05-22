#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path.cwd()


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def normalize_repo_path(path: str) -> str:
    path = str(path or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def state_from_xy(xy: str) -> str:
    if "U" in xy:
        return "conflicted"
    if "R" in xy:
        return "renamed"
    if "D" in xy:
        return "deleted"
    if "A" in xy:
        return "added"
    if "M" in xy or "T" in xy:
        return "modified"
    return "changed"


def status_label(rec: dict[str, Any]) -> str:
    if rec.get("untracked"):
        return "untracked"
    if rec.get("ignored"):
        return "ignored"
    if rec.get("conflicted"):
        return "conflicted"
    if rec.get("renamed"):
        return "tracked_renamed"
    if rec.get("deleted"):
        return "tracked_deleted"
    if rec.get("staged") or rec.get("unstaged"):
        return "tracked_changed"
    return str(rec.get("state") or "unknown")


def parse_porcelain_v2_z(text: str) -> list[dict[str, Any]]:
    entries = [entry for entry in text.split("\0") if entry and not entry.startswith("# ")]
    files: list[dict[str, Any]] = []
    i = 0

    while i < len(entries):
        entry = entries[i]
        kind = entry[:1]

        if kind == "?":
            raw_path = entry[2:]
            files.append({
                "path": normalize_repo_path(raw_path),
                "raw_path": raw_path,
                "state": "untracked",
                "status": "untracked",
                "untracked": True,
                "tracked": False,
                "staged": False,
                "unstaged": False,
                "deleted": False,
                "renamed": False,
                "conflicted": False,
                "collapsed_directory": raw_path.endswith("/") or raw_path.endswith("\\"),
            })

        elif kind == "!":
            raw_path = entry[2:]
            files.append({
                "path": normalize_repo_path(raw_path),
                "raw_path": raw_path,
                "state": "ignored",
                "status": "ignored",
                "ignored": True,
                "untracked": False,
                "tracked": False,
                "collapsed_directory": raw_path.endswith("/") or raw_path.endswith("\\"),
            })

        elif kind == "1":
            parts = entry.split(" ", 8)
            xy = parts[1] if len(parts) > 1 else ".."
            raw_path = parts[8] if len(parts) > 8 else entry.split(" ")[-1]
            state = state_from_xy(xy)
            rec = {
                "path": normalize_repo_path(raw_path),
                "raw_path": raw_path,
                "xy": xy,
                "state": state,
                "untracked": False,
                "tracked": True,
                "staged": xy[0] != ".",
                "unstaged": len(xy) > 1 and xy[1] != ".",
                "deleted": "D" in xy,
                "renamed": False,
                "conflicted": False,
                "collapsed_directory": False,
            }
            rec["status"] = status_label(rec)
            files.append(rec)

        elif kind == "2":
            parts = entry.split(" ", 9)
            xy = parts[1] if len(parts) > 1 else ".."
            raw_path = parts[9] if len(parts) > 9 else entry.split(" ")[-1]
            old_path = entries[i + 1] if i + 1 < len(entries) else ""
            i += 1
            rec = {
                "path": normalize_repo_path(raw_path),
                "old_path": normalize_repo_path(old_path),
                "raw_path": raw_path,
                "xy": xy,
                "state": "renamed",
                "untracked": False,
                "tracked": True,
                "staged": xy[0] != ".",
                "unstaged": len(xy) > 1 and xy[1] != ".",
                "deleted": "D" in xy,
                "renamed": True,
                "conflicted": False,
                "collapsed_directory": False,
            }
            rec["status"] = status_label(rec)
            files.append(rec)

        elif kind == "u":
            parts = entry.split(" ", 10)
            xy = parts[1] if len(parts) > 1 else "UU"
            raw_path = parts[10] if len(parts) > 10 else entry.split(" ")[-1]
            rec = {
                "path": normalize_repo_path(raw_path),
                "raw_path": raw_path,
                "xy": xy,
                "state": "conflicted",
                "status": "conflicted",
                "untracked": False,
                "tracked": True,
                "staged": True,
                "unstaged": True,
                "deleted": "D" in xy,
                "renamed": False,
                "conflicted": True,
                "collapsed_directory": False,
            }
            files.append(rec)

        i += 1

    return files


def count_by(files: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in files:
        value = str(item.get(key) or "(missing)")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def path_stats(files: list[dict[str, Any]]) -> dict[str, Any]:
    depths: dict[int, int] = {}
    roots: dict[str, int] = {}
    for item in files:
        path = str(item.get("path") or "")
        parts = [part for part in path.split("/") if part]
        depths[len(parts)] = depths.get(len(parts), 0) + 1
        root = parts[0] if len(parts) > 1 else "(repo root file)"
        roots[root] = roots.get(root, 0) + 1

    return {
        "slash_paths": sum(1 for item in files if "/" in str(item.get("path") or "")),
        "backslash_paths": sum(1 for item in files if "\\" in str(item.get("path") or "")),
        "collapsed_directories": sum(1 for item in files if item.get("collapsed_directory")),
        "depths": dict(sorted(depths.items())),
        "roots": dict(sorted(roots.items())),
    }


def import_git_dirty() -> Any | None:
    candidate = ROOT / "git_dirty.py"
    if not candidate.exists():
        return None

    spec = importlib.util.spec_from_file_location("git_dirty", candidate)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules["git_dirty"] = module
    spec.loader.exec_module(module)
    return module


def flatten_commit_groups(groups: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for group_name, items in groups.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("path"):
                copy = dict(item)
                copy["group"] = group_name
                files.append(copy)
    return files


def find_commit_review(plan: dict[str, Any]) -> dict[str, Any]:
    for step in plan.get("steps", []):
        if isinstance(step, dict) and isinstance(step.get("commit_review"), dict):
            return step["commit_review"]
    return {}


def summarize(label: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "label": label,
        "total": len(files),
        "by_status": count_by(files, "status"),
        "by_state": count_by(files, "state"),
        "path_stats": path_stats(files),
        "first_30": [
            {
                "path": item.get("path"),
                "status": item.get("status"),
                "state": item.get("state"),
                "xy": item.get("xy"),
                "collapsed_directory": item.get("collapsed_directory", False),
            }
            for item in files[:30]
        ],
    }


def main() -> None:
    current = run_git(["status", "--porcelain=v2", "-z", "--branch"])
    proposed = run_git(["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all"])

    current_files = parse_porcelain_v2_z(current.stdout)
    proposed_files = parse_porcelain_v2_z(proposed.stdout)

    report: dict[str, Any] = {
        "repo": str(ROOT),
        "git_current_command": "git status --porcelain=v2 -z --branch",
        "git_proposed_command": "git status --porcelain=v2 -z --branch --untracked-files=all",
        "current_git_status": summarize("current_collapsed_git_status", current_files),
        "proposed_git_status": summarize("proposed_full_git_status", proposed_files),
        "diagnosis": [],
    }

    if report["current_git_status"]["path_stats"]["collapsed_directories"]:
        report["diagnosis"].append(
            "Current git status is collapsing untracked directories. The commit basket cannot build real subfile trees from this."
        )

    if len(proposed_files) > len(current_files):
        report["diagnosis"].append(
            "Adding --untracked-files=all exposes more real file paths for the tree."
        )

    if report["proposed_git_status"]["path_stats"]["backslash_paths"]:
        report["diagnosis"].append(
            "Backend still has backslash paths. Normalize all commit candidate paths to forward slashes before sending UI payload."
        )

    git_dirty = import_git_dirty()
    if git_dirty is not None:
        status = git_dirty.collect_status(ROOT)
        backend_files = list(status.get("files") or [])
        for item in backend_files:
            item["path"] = normalize_repo_path(str(item.get("path") or ""))
            item["status"] = item.get("status") or item.get("state") or status_label(item)

        plan = git_dirty.make_plan(ROOT)
        commit_review = find_commit_review(plan)
        commit_group_files = flatten_commit_groups(commit_review.get("candidate_groups") or {})
        for item in commit_group_files:
            item["path"] = normalize_repo_path(str(item.get("path") or ""))
            item["status"] = item.get("status") or item.get("state") or (
                "untracked" if item.get("untracked") else "tracked_changed"
            )

        report["git_dirty_collect_status"] = summarize("git_dirty.collect_status", backend_files)
        report["git_dirty_commit_review"] = {
            "commit_step_found": bool(commit_review),
            "candidate_group_counts": {
                name: len(items)
                for name, items in (commit_review.get("candidate_groups") or {}).items()
                if isinstance(items, list)
            },
            "candidate_files": summarize("commit_review.candidate_groups", commit_group_files),
        }

        backend_total = len(backend_files)
        proposed_total = len(proposed_files)
        if backend_total < proposed_total:
            report["diagnosis"].append(
                f"git_dirty.collect_status sees {backend_total} files but full git status sees {proposed_total}; backend should use --untracked-files=all."
            )

    else:
        report["diagnosis"].append("Could not import git_dirty.py from repo root.")

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()