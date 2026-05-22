#!/usr/bin/env python
"""Verify current Gitea publish UI assumptions before the real UI hotpatch.

This is intentionally a pre-patch verifier. It checks that the current UI is
still in the "default target looks confirmed" state before a follow-up patch
changes that behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def repo_root_from_here() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], Path.cwd().resolve()):
        if (candidate / "main_computer").is_dir() and (candidate / "main_computer/web/applications/apps/git-tools.html").exists():
            return candidate
    raise SystemExit("Could not locate repository root from verifier location or current working directory.")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def contains_all(text: str, snippets: tuple[str, ...]) -> tuple[bool, str]:
    missing = [snippet for snippet in snippets if snippet not in text]
    if missing:
        return False, "missing: " + "; ".join(missing)
    return True, "all expected snippets present"


def lacks_all(text: str, snippets: tuple[str, ...]) -> tuple[bool, str]:
    present = [snippet for snippet in snippets if snippet in text]
    if present:
        return False, "unexpected existing confirmation symbols: " + "; ".join(present)
    return True, "no existing confirmation gate symbols found"


def regex_all(text: str, patterns: tuple[str, ...]) -> tuple[bool, str]:
    missing = [pattern for pattern in patterns if not re.search(pattern, text, flags=re.DOTALL)]
    if missing:
        return False, "missing regex patterns: " + "; ".join(missing)
    return True, "all expected regex patterns matched"


def load_application_index(repo_root: Path) -> tuple[str, str | None]:
    sys.path.insert(0, str(repo_root))
    try:
        from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML  # type: ignore
    except Exception as exc:  # pragma: no cover - diagnostic path
        return "", f"{type(exc).__name__}: {exc}"
    return APPLICATIONS_INDEX_HTML, None


def build_checks(repo_root: Path) -> list[CheckResult]:
    paths = {
        "app_html": repo_root / "main_computer/web/applications/apps/git-tools.html",
        "task_js": repo_root / "main_computer/web/applications/scripts/task-manager.js",
        "dom_bindings_js": repo_root / "main_computer/web/applications/scripts/dom-bindings/git-tools.js",
        "viewport_pages_py": repo_root / "main_computer/viewport_pages.py",
    }
    missing_paths = [str(path.relative_to(repo_root)) for path in paths.values() if not path.exists()]
    if missing_paths:
        return [
            CheckResult(
                "source_paths_exist",
                False,
                "missing source files: " + "; ".join(missing_paths),
            )
        ]

    app_html = read_text(paths["app_html"])
    task_js = read_text(paths["task_js"])
    dom_bindings_js = read_text(paths["dom_bindings_js"])

    checks: list[CheckResult] = []

    ok, detail = contains_all(
        app_html,
        (
            'id="git-server-pane"',
            "Publish this repo to local Gitea",
            'id="git-server-target-preview"',
            "Local Gitea target:",
            "http://localhost:3000/local/main_computer_test.git",
            "Keep GitHub/GitLab as origin and add Local Gitea as a second remote",
            "Replace origin with Local Gitea",
        ),
    )
    checks.append(CheckResult("publish_workflow_html_is_current_gitea_panel", ok, detail))

    ok, detail = regex_all(
        app_html,
        (
            r'<input\s+id="git-server-remote-name"[^>]*value="local"',
            r'<input\s+id="git-server-owner"[^>]*value="local"',
            r'<input\s+id="git-server-repo"[^>]*value="main_computer_test"',
            r'<select\s+id="git-server-remote-protocol".*?<option\s+value="http"\s+selected>HTTP localhost:3000</option>',
        ),
    )
    checks.append(CheckResult("target_fields_are_currently_hardcoded_defaults", ok, detail))

    ok, detail = contains_all(
        app_html,
        (
            "<strong>Confirm Local Gitea target</strong>",
            "Recommended defaults are already filled in. Use the reset button only if the fields look wrong.",
            'id="git-server-use-local"',
            "Reset to safe local defaults",
        ),
    )
    checks.append(CheckResult("step_one_is_currently_reset_defaults_not_explicit_confirmation", ok, detail))

    ok, detail = lacks_all(
        app_html + "\n" + task_js + "\n" + dom_bindings_js,
        (
            'id="git-server-target-confirm"',
            "gitServerTargetConfirmed",
            "gitServerRequireTargetConfirmation",
            "gitServerMarkTargetConfirmed",
            "data-git-server-target-confirmed",
        ),
    )
    checks.append(CheckResult("no_existing_target_confirmation_gate", ok, detail))

    fallback_snippet = """if (!gitServerOwner?.value.trim() || !gitServerRepo?.value.trim()) {
    useLocalGitServerRemote();
  }"""
    fallback_count = task_js.count(fallback_snippet)
    checks.append(
        CheckResult(
            "mutating_actions_silently_reapply_defaults_when_target_blank",
            fallback_count >= 2,
            f"found fallback snippet {fallback_count} time(s); expected at least 2 for setup and push",
        )
    )

    ok, detail = contains_all(
        task_js,
        (
            "function updateGitServerRemoteChoicePreview()",
            'document.createTextNode("Local Gitea target: "),',
            "gitServerTargetPreview.replaceChildren(",
            "function gitServerLocalRepoName()",
            'window.location?.pathname || ""',
            '"main_computer_test"',
        ),
    )
    checks.append(CheckResult("runtime_preview_is_computed_but_still_labelled_as_target", ok, detail))

    ok, detail = contains_all(
        dom_bindings_js,
        (
            'document.querySelector("#git-server-target-preview")',
            'document.querySelector("#git-server-use-local")',
            'document.querySelector("#git-server-remote-apply-local")',
            'document.querySelector("#git-server-push-local")',
            'document.querySelectorAll("[data-git-server-remote-preset]")',
        ),
    )
    checks.append(CheckResult("dom_bindings_cover_existing_gitea_controls", ok, detail))

    ok, detail = contains_all(
        app_html + "\n" + task_js,
        (
            'id="git-server-remote-show"',
            'data-git-server-remote-preset="show-remotes"',
            'case "show-remotes":',
            'return "git remote -v";',
            "fillGitServerRemoteCommand(preset)",
        ),
    )
    checks.append(CheckResult("show_current_remotes_is_currently_a_command_preset", ok, detail))

    application_index, import_error = load_application_index(repo_root)
    if import_error:
        checks.append(CheckResult("application_index_expands_git_ui_sources", False, import_error))
    else:
        ok, detail = contains_all(
            application_index,
            (
                "Publish this repo to local Gitea",
                "Reset to safe local defaults",
                "function updateGitServerRemoteChoicePreview()",
                'document.createTextNode("Local Gitea target: "),',
                'bindGitToolsControl(gitServerRemoteApplyLocal, "click", applyLocalGitServerRemote);',
            ),
        )
        checks.append(CheckResult("application_index_expands_git_ui_sources", ok, detail))

    return checks


def render_text(repo_root: Path, checks: list[CheckResult]) -> str:
    lines = [
        "Gitea publish UI assumption verifier",
        f"repo_root: {repo_root}",
        "",
    ]
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        lines.append(f"{status} {check.name}")
        lines.append(f"  {check.detail}")
    passed = sum(1 for check in checks if check.ok)
    failed = len(checks) - passed
    lines.extend(
        [
            "",
            f"summary: {passed} passed, {failed} failed",
            f"ready_for_real_ui_hotpatch: {'YES' if failed == 0 else 'NO'}",
        ]
    )
    if failed == 0:
        lines.extend(
            [
                "",
                "Verified pre-patch assumptions:",
                "- The visible target is still presented as a target even though it is assembled from defaults.",
                "- Step 1 still offers only a reset-to-defaults action, not explicit target confirmation.",
                "- Setup/push actions can still silently reapply defaults when owner/repo are blank.",
                "- Show current remotes is still a command preset that prepares git remote -v.",
            ]
        )
    return "\n".join(lines) + "\n"


def render_json(repo_root: Path, checks: list[CheckResult]) -> str:
    payload = {
        "repo_root": str(repo_root),
        "ready_for_real_ui_hotpatch": all(check.ok for check in checks),
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in checks
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify assumptions for the Gitea publish UI confirmation hotpatch.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    repo_root = repo_root_from_here()
    checks = build_checks(repo_root)
    output = render_json(repo_root, checks) if args.json else render_text(repo_root, checks)
    print(output, end="")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
