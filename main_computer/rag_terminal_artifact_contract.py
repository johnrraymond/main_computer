#!/usr/bin/env python3
"""
Mode-aware terminal artifact contract helpers.

A verified edit proposal is not promotable by itself.  Promotion becomes true
only after a mode-explicit terminal artifact object satisfies the contract for
its declared artifact mode.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


ACCEPTED_TERMINAL_ARTIFACT = "accepted_terminal_artifact"
NONTERMINAL_RESULT = "nonterminal_result"

SNAPSHOT_ZIP = "snapshot_zip"
VERIFIED_BUNDLE = "verified_bundle"
SUPPORTED_ARTIFACT_MODES = {SNAPSHOT_ZIP, VERIFIED_BUNDLE}


def normalize_repo_relative_path(raw_path: Any) -> str | None:
    """Return a normalized repo-relative path, or None when it is unsafe.

    This helper is intentionally conservative because terminal artifact
    acceptance is allowed to set promotable=True.
    """

    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    value = raw_path.strip().replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        return None

    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None

    return "/".join(parts)


def repo_relative_paths_are_safe(paths: list[Any]) -> bool:
    return bool(paths) and all(normalize_repo_relative_path(path) is not None for path in paths)


def _replacement_file_path(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("path", entry.get("target_file"))
    return entry


def _replacement_file_exists(entry: Any) -> bool:
    if isinstance(entry, dict):
        return entry.get("exists") is True
    return isinstance(entry, str) and bool(entry.strip())


def _replacement_file_paths(candidate: dict[str, Any]) -> list[Any]:
    files = candidate.get("replacement_files")
    if isinstance(files, list):
        return [_replacement_file_path(entry) for entry in files]
    return []


def _replacement_files_exist(candidate: dict[str, Any]) -> bool:
    files = candidate.get("replacement_files")
    return isinstance(files, list) and bool(files) and all(_replacement_file_exists(entry) for entry in files)


def _dry_run_command_known(candidate: dict[str, Any]) -> bool:
    command = candidate.get("dry_run_command")
    return isinstance(command, str) and bool(command.strip())


def _first_failed_gate(contract: dict[str, bool], ordered_gates: list[str]) -> str | None:
    for gate in ordered_gates:
        if contract.get(gate) is not True:
            return gate
    return None


def _snapshot_zip_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    replacement_paths = _replacement_file_paths(candidate)
    contract = {
        "replacement_files_exist": _replacement_files_exist(candidate),
        "repo_relative_paths_safe": repo_relative_paths_are_safe(replacement_paths),
        "root_contract_valid": candidate.get("root_contract_valid") is True,
        "new_patch_usable": candidate.get("new_patch_usable") is True,
        "dry_run_command_known": _dry_run_command_known(candidate),
    }
    ordered_gates = [
        "replacement_files_exist",
        "repo_relative_paths_safe",
        "root_contract_valid",
        "new_patch_usable",
        "dry_run_command_known",
    ]
    return contract, ordered_gates


def _verified_bundle_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    replacement_paths = _replacement_file_paths(candidate)
    contract = {
        "manifest_json_exists": candidate.get("manifest_json_exists") is True,
        "reference_patch_exists": candidate.get("reference_patch_exists") is True,
        "replacement_files_exist": _replacement_files_exist(candidate),
        "repo_relative_paths_safe": repo_relative_paths_are_safe(replacement_paths),
        "reference_matches_replacement_files": candidate.get("reference_matches_replacement_files") is True,
        "new_patch_usable": candidate.get("new_patch_usable") is True,
        "dry_run_command_known": _dry_run_command_known(candidate),
    }
    ordered_gates = [
        "manifest_json_exists",
        "reference_patch_exists",
        "replacement_files_exist",
        "repo_relative_paths_safe",
        "reference_matches_replacement_files",
        "new_patch_usable",
        "dry_run_command_known",
    ]
    return contract, ordered_gates


def evaluate_terminal_artifact_contract(candidate: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate whether a candidate artifact state is terminal and promotable.

    The returned report is intentionally mode-aware: snapshot zips and verified
    bundles have different contracts, but both can become accepted terminal
    artifacts when their own contract passes.
    """

    if not isinstance(candidate, dict):
        candidate = {}

    artifact_mode = candidate.get("artifact_mode")
    report: dict[str, Any] = {
        "terminal_state": NONTERMINAL_RESULT,
        "artifact_mode": artifact_mode,
        "artifact_contract": {},
        "artifact_contract_passed": False,
        "verification": {
            "level": None,
            "dry_run_command": candidate.get("dry_run_command") if isinstance(candidate.get("dry_run_command"), str) else None,
        },
        "failed_gate": None,
        "promotable": False,
    }

    if artifact_mode not in SUPPORTED_ARTIFACT_MODES:
        report["failed_gate"] = "artifact_mode"
        return report

    if artifact_mode == SNAPSHOT_ZIP:
        contract, ordered_gates = _snapshot_zip_contract(candidate)
        verification_level = "no_reference"
    else:
        contract, ordered_gates = _verified_bundle_contract(candidate)
        verification_level = "exact"

    artifact_contract_passed = all(contract.get(gate) is True for gate in ordered_gates)
    failed_gate = _first_failed_gate(contract, ordered_gates)

    report.update(
        {
            "artifact_contract": contract,
            "artifact_contract_passed": artifact_contract_passed,
            "verification": {
                "level": verification_level if artifact_contract_passed else None,
                "dry_run_command": candidate.get("dry_run_command") if _dry_run_command_known(candidate) else None,
            },
            "failed_gate": failed_gate,
        }
    )

    if artifact_contract_passed:
        report["terminal_state"] = ACCEPTED_TERMINAL_ARTIFACT

    # Single source of truth: promotable is derived only from the accepted
    # terminal state plus the mode-specific artifact contract result.
    report["promotable"] = (
        report["terminal_state"] == ACCEPTED_TERMINAL_ARTIFACT
        and report["artifact_contract_passed"] is True
        and report["artifact_mode"] in SUPPORTED_ARTIFACT_MODES
    )

    return report
