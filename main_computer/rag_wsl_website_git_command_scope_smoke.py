#!/usr/bin/env python3
"""
Smoke: website Git preflight commands are scoped through WSL, not host Git.

This deterministic, no-model smoke is phase one for containerized website Git
control.  It does not require a real WSL distribution because it is proving the
command boundary and target translation before runtime execution is enabled:

* website targets resolve to a WSL-home websites root
* generated Git commands are wrapped by wsl.exe
* no command directly invokes host/local git
* --cd is always inside the WSL websites root
* Windows paths, /mnt/c host mounts, traversal, and the install-directory hub
  are rejected as website Git targets

The hub remains locked as an install-directory project.  Websites are the only
editable Git target class in this phase.
"""

from __future__ import annotations

import json
import posixpath
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


MODE = "rag_wsl_website_git_command_scope_smoke"
REQUEST = "Prepare Git preflight for the landing-site website."
WSL_COMMAND = "wsl.exe"
WSL_DISTRIBUTION = "MainComputerExecutorTest"
WSL_HOME = "/home/main-computer"
WSL_WEBSITES_ROOT = f"{WSL_HOME}/websites"
WSL_INSTALL_ROOT = f"{WSL_HOME}/install"
LOCKED_HUB_ROOT = f"{WSL_INSTALL_ROOT}/hub"
SELECTED_WEBSITE_ID = "landing-site"
SELECTED_WEBSITE_REQUEST_PATH = f"runtime/websites/{SELECTED_WEBSITE_ID}"


def repo_relative_website_path(site_id: str) -> Path:
    return Path("runtime") / "websites" / site_id


def script_derived_host_website_path(site_id: str) -> Path:
    return Path(__file__).resolve().parents[1] / repo_relative_website_path(site_id)


def host_mount_negative_target(site_id: str) -> str:
    host_path = script_derived_host_website_path(site_id).resolve()
    normalized = str(host_path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"/mnt/{normalized[0].lower()}/{normalized[3:]}"
    relative = repo_relative_website_path(site_id).as_posix()
    return f"/mnt/c/main-computer-fixtures/{Path(__file__).resolve().parents[1].name}/{relative}"


def windows_path_negative_target(site_id: str) -> str:
    host_path = script_derived_host_website_path(site_id).resolve()
    normalized = str(host_path).replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"{normalized[0].upper()}:\\\\" + normalized[3:].replace("/", "\\")
    relative = str(repo_relative_website_path(site_id)).replace("/", "\\")
    return f"C:\\main-computer-fixtures\\{Path(__file__).resolve().parents[1].name}\\{relative}"

@dataclass(frozen=True)
class TargetResolution:
    ok: bool
    input_path: str
    wsl_path: str | None = None
    reason: str | None = None


def repo_root() -> PurePosixPath:
    return PurePosixPath(__file__).parents[1]


def output_root() -> PurePosixPath:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PurePosixPath("debug_assets") / MODE / stamp


def is_windows_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", normalized)) or value.startswith("\\\\")


def contains_parent_traversal(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return any(part == ".." for part in normalized.split("/"))


def is_host_mount_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized == "/mnt" or normalized.startswith("/mnt/")


def normalize_absolute_wsl_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized == ".":
        normalized = ""
    if not normalized.startswith("/"):
        raise ValueError("expected absolute WSL path")
    return normalized


def relative_site_id_from_request(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip("/")

    prefixes = (
        "runtime/websites/",
        "websites/",
    )
    for prefix in prefixes:
        if normalized.startswith(prefix):
            candidate = normalized[len(prefix) :]
            if "/" not in candidate and candidate:
                return candidate

    if "/" not in normalized and normalized:
        return normalized

    return None


def is_allowed_site_id(site_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", site_id or ""))


def is_inside_or_equal(path: str, root: str) -> bool:
    root_clean = normalize_absolute_wsl_path(root)
    path_clean = normalize_absolute_wsl_path(path)
    return path_clean == root_clean or path_clean.startswith(root_clean.rstrip("/") + "/")


def resolve_website_target(value: str) -> TargetResolution:
    raw = str(value or "").strip()
    if not raw:
        return TargetResolution(ok=False, input_path=raw, reason="empty_target")
    if "\x00" in raw:
        return TargetResolution(ok=False, input_path=raw, reason="nul_rejected")
    if is_windows_path(raw):
        return TargetResolution(ok=False, input_path=raw, reason="windows_path_rejected")
    if contains_parent_traversal(raw):
        return TargetResolution(ok=False, input_path=raw, reason="parent_traversal_rejected")
    if is_host_mount_path(raw):
        return TargetResolution(ok=False, input_path=raw, reason="host_mount_rejected")

    if raw.startswith("/"):
        try:
            wsl_path = normalize_absolute_wsl_path(raw)
        except ValueError:
            return TargetResolution(ok=False, input_path=raw, reason="invalid_wsl_path")
        if wsl_path == LOCKED_HUB_ROOT or is_inside_or_equal(wsl_path, LOCKED_HUB_ROOT):
            return TargetResolution(ok=False, input_path=raw, reason="hub_install_locked")
        if not is_inside_or_equal(wsl_path, WSL_WEBSITES_ROOT):
            return TargetResolution(ok=False, input_path=raw, reason="outside_websites_root")
        site_id = wsl_path.removeprefix(WSL_WEBSITES_ROOT.rstrip("/") + "/").split("/", 1)[0]
        if not is_allowed_site_id(site_id):
            return TargetResolution(ok=False, input_path=raw, reason="invalid_site_id")
        return TargetResolution(ok=True, input_path=raw, wsl_path=wsl_path)

    site_id = relative_site_id_from_request(raw)
    if not site_id:
        return TargetResolution(ok=False, input_path=raw, reason="unsupported_relative_target")
    if not is_allowed_site_id(site_id):
        return TargetResolution(ok=False, input_path=raw, reason="invalid_site_id")

    return TargetResolution(
        ok=True,
        input_path=raw,
        wsl_path=f"{WSL_WEBSITES_ROOT}/{site_id}",
    )


def build_wsl_git_command(target: str, git_args: list[str]) -> list[str]:
    resolution = resolve_website_target(target)
    if not resolution.ok or not resolution.wsl_path:
        raise ValueError(f"Unsafe website Git target: {resolution.reason or 'unknown'}")
    if not git_args or git_args[0] == "git":
        raise ValueError("git_args must be the arguments after the git executable")

    return [
        WSL_COMMAND,
        "--distribution",
        WSL_DISTRIBUTION,
        "--cd",
        resolution.wsl_path,
        "--exec",
        "git",
        *git_args,
    ]


def planned_git_preflight(target: str) -> dict[str, list[str]]:
    return {
        "inside": build_wsl_git_command(target, ["rev-parse", "--is-inside-work-tree"]),
        "branch": build_wsl_git_command(target, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": build_wsl_git_command(target, ["rev-parse", "HEAD"]),
        "status": build_wsl_git_command(target, ["status", "--porcelain=v1"]),
    }


def command_cd(command: list[str]) -> str | None:
    try:
        index = command.index("--cd")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def command_exec(command: list[str]) -> str | None:
    try:
        index = command.index("--exec")
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def command_uses_wsl_git(command: list[str]) -> bool:
    return (
        len(command) >= 8
        and command[0] == WSL_COMMAND
        and "--distribution" in command
        and WSL_DISTRIBUTION in command
        and "--cd" in command
        and command_exec(command) == "git"
    )


def command_avoids_local_git(command: list[str]) -> bool:
    return bool(command) and command[0] != "git"


def command_cd_inside_websites(command: list[str]) -> bool:
    cwd = command_cd(command)
    return bool(cwd) and is_inside_or_equal(cwd, WSL_WEBSITES_ROOT)


def command_avoids_forbidden_paths(command: list[str]) -> bool:
    text = "\n".join(command).replace("\\", "/")
    return (
        LOCKED_HUB_ROOT not in text
        and "/mnt/" not in text
        and not re.search(r"[A-Za-z]:/", text)
    )


def evaluate_case() -> dict[str, Any]:
    selected_resolution = resolve_website_target(SELECTED_WEBSITE_REQUEST_PATH)
    commands = planned_git_preflight(SELECTED_WEBSITE_REQUEST_PATH)

    negative_targets = {
        "install_hub": LOCKED_HUB_ROOT,
        "host_mount": host_mount_negative_target(SELECTED_WEBSITE_ID),
        "windows_path": windows_path_negative_target(SELECTED_WEBSITE_ID),
        "traversal": "runtime/websites/../install/hub",
        "install_other": f"{WSL_INSTALL_ROOT}/other-project",
    }
    negative_resolutions = {
        name: resolve_website_target(target)
        for name, target in negative_targets.items()
    }

    command_values = list(commands.values())
    checks = {
        "selected_target_translates_to_wsl_home_website": (
            selected_resolution.ok
            and selected_resolution.wsl_path == f"{WSL_WEBSITES_ROOT}/{SELECTED_WEBSITE_ID}"
        ),
        "preflight_command_count": len(command_values) == 4,
        "all_commands_use_wsl_executor": all(command_uses_wsl_git(command) for command in command_values),
        "no_commands_call_local_git": all(command_avoids_local_git(command) for command in command_values),
        "all_command_cwds_inside_wsl_websites": all(command_cd_inside_websites(command) for command in command_values),
        "commands_avoid_host_mount_windows_and_locked_hub_paths": all(
            command_avoids_forbidden_paths(command) for command in command_values
        ),
        "install_hub_rejected": negative_resolutions["install_hub"].reason == "hub_install_locked",
        "host_mount_rejected": negative_resolutions["host_mount"].reason == "host_mount_rejected",
        "windows_path_rejected": negative_resolutions["windows_path"].reason == "windows_path_rejected",
        "parent_traversal_rejected": negative_resolutions["traversal"].reason == "parent_traversal_rejected",
        "install_directory_rejected_for_non_hub_too": negative_resolutions["install_other"].reason == "outside_websites_root",
        "auto_applied_false": True,
        "committed_false": True,
        "executed_false": True,
    }

    return {
        "name": "wsl_scoped_website_git_preflight_commands_are_prepared_without_host_git",
        "ok": all(checks.values()),
        "request": REQUEST,
        "selected_target": SELECTED_WEBSITE_REQUEST_PATH,
        "selected_resolution": selected_resolution.__dict__,
        "wsl_distribution": WSL_DISTRIBUTION,
        "wsl_websites_root": WSL_WEBSITES_ROOT,
        "locked_hub_root": LOCKED_HUB_ROOT,
        "planned_commands": commands,
        "negative_resolutions": {
            name: resolution.__dict__
            for name, resolution in negative_resolutions.items()
        },
        "checks": checks,
        "auto_applied": False,
        "committed": False,
        "executed": False,
    }


def main() -> int:
    case = evaluate_case()
    report = {
        "mode": MODE,
        "ok": case["ok"],
        "case_count": 1,
        "passed_case_count": 1 if case["ok"] else 0,
        "failed_case_count": 0 if case["ok"] else 1,
        "cases": [case],
    }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
