from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

import git_dirty
from main_computer.git_tools import GitToolsService
from main_computer.git_panel_runner import clean_path_token

ALLOWED_WINDOWS_EXAMPLE_USER = sorted(git_dirty.WINDOWS_USER_PATH_ALLOWED_EXAMPLE_USERS)[0]
FIXTURE_WINDOWS_ROOT = PureWindowsPath("C:/main-computer-fixtures")


def windows_user_path(user: str, repo_name: str = "main_computer", *, slash: str = "\\") -> str:
    separator = "/" if slash == "/" else chr(92)
    return f"C:{separator}Users{separator}{user}{separator}{repo_name}"


class GitToolsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path.cwd().resolve()
        self._registry_temp = tempfile.TemporaryDirectory()
        self.project_registry_path = Path(self._registry_temp.name) / "git-tools-projects.json"
        self.service = self._service()
        self.incoming = self.repo_root / "tools" / "patching" / "patches" / "incoming"
        self.temp_target = self.repo_root / "git_patch_test_target.txt"
        self.temp_patch = self.incoming / "git_patch_test_target.patch"

    def tearDown(self) -> None:
        for path in (self.temp_patch, self.temp_target):
            if path.exists():
                path.unlink()
        self._registry_temp.cleanup()

    def _service(self, repo_root: Path | None = None) -> GitToolsService:
        service = GitToolsService(repo_root or self.repo_root)
        service._project_registry_path = lambda: self.project_registry_path  # type: ignore[method-assign]
        return service

    def _write_patch_fixture(self) -> None:
        self.temp_target.write_text("alpha\n", encoding="utf-8")
        self.temp_patch.write_text(
            """--- a/git_patch_test_target.txt
+++ b/git_patch_test_target.txt
@@ -1 +1,2 @@
 alpha
+beta
""",
            encoding="utf-8",
        )

    def _wait_for_secrets_scan_job(self, job_id: str, *, timeout: float = 5.0) -> list[dict[str, object]]:
        deadline = time.time() + timeout
        after_seq = 0
        events: list[dict[str, object]] = []
        while time.time() < deadline:
            snapshot = self.service.git_project_secrets_filter_job_events(job_id, after_seq=after_seq)
            self.assertTrue(snapshot["ok"], snapshot)
            batch = snapshot.get("events", [])
            events.extend(batch)
            if batch:
                after_seq = max(int(event.get("seq") or 0) for event in events)
            if snapshot.get("done"):
                return events
            time.sleep(0.05)
        self.fail(f"Secrets / Filter scan job did not finish: {job_id}")

    def test_read_gitignore_file_returns_line_elements_for_ui_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".gitignore").write_text("# cache\n__pycache__/\n\n*.pyc\n", encoding="utf-8")

            result = self.service._read_gitignore_file(repo)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["exists"])
        self.assertEqual(result["relative_path"], ".gitignore")
        self.assertEqual(result["line_count"], 4)
        self.assertEqual(result["lines"][0]["number"], 1)
        self.assertTrue(result["lines"][0]["comment"])
        self.assertEqual(result["lines"][1]["text"], "__pycache__/")
        self.assertTrue(result["lines"][2]["blank"])
        self.assertIn("*.pyc", result["content"])

    def test_wizard_keeps_gitignore_rule_groups_for_card_workbench(self) -> None:
        dirty_plan = {
            "ok": True,
            "plan_id": "plan-test",
            "recommended_strategy": "prepare_gitignore_then_create_initial_snapshot",
            "steps": [
                {
                    "id": "update_gitignore_before_initial_commit",
                    "order": 0,
                    "label": "Clean up .gitignore before first commit",
                    "kind": "ignore",
                    "why": "Review ignore rules first.",
                    "paths": ["__pycache__/", "runtime/"],
                    "commands": [],
                    "ignore_rules": ["__pycache__/"],
                    "questionable_ignore_rules": ["runtime/"],
                    "safe_paths": ["__pycache__/"],
                    "questionable_paths": ["runtime/"],
                    "ignore_rule_groups": {"safe": ["__pycache__/"], "questionable": ["runtime/"]},
                }
            ],
        }

        wizard = self.service._wizard_from_dirty_plan(
            dirty_plan,
            git={"is_git_repo": True, "has_head": False},
            project={"locked": False},
        )

        step = wizard["steps"][0]
        self.assertEqual(step["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(step["ignore_rule_groups"]["safe"], ["__pycache__/"])
        self.assertEqual(step["ignore_rule_groups"]["questionable"], ["runtime/"])
        self.assertEqual(step["safe_paths"], ["__pycache__/"])
        self.assertEqual(step["questionable_paths"], ["runtime/"])

    def test_wizard_preserves_completed_gitignore_success_card(self) -> None:
        dirty_plan = {
            "ok": True,
            "plan_id": "plan-test",
            "recommended_strategy": "create_initial_snapshot_then_recheck",
            "steps": [
                {
                    "id": "update_gitignore_before_initial_commit",
                    "order": 0,
                    "label": "Clean up .gitignore before first commit",
                    "kind": "ignore",
                    "why": "No ignore cleanup needed.",
                    "paths": [],
                    "commands": [],
                    "state": "completed",
                    "completed": True,
                    "requires_user": False,
                    "blocks_progress": False,
                    "gitignore_file": {"exists": True, "relative_path": ".gitignore"},
                    "gitignore_success": {"status": "passed", "message": "No generated/runtime ignore cleanup is currently blocking."},
                },
                {
                    "id": "secrets_filter",
                    "order": 1,
                    "label": "Secrets / Filter",
                    "kind": "workflow",
                    "why": "Review security/privacy rules before commit.",
                    "paths": ["app.py"],
                    "commands": [],
                },
            ],
        }

        wizard = self.service._wizard_from_dirty_plan(
            dirty_plan,
            git={"is_git_repo": True, "has_head": False},
            project={"locked": False},
        )

        step = wizard["steps"][0]
        self.assertEqual(step["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(step["state"], "completed")
        self.assertEqual(step["gitignore_success"]["status"], "passed")
        self.assertFalse(step["locked"])

    def test_wizard_keeps_secrets_filter_payload_for_standalone_card(self) -> None:
        secrets_filter = {
            "kind": "secrets_filter",
            "title": "SECRETS / FILTER",
            "rules": [
                {"id": "detect_secrets", "engine": "detect-secrets", "enabled": True, "source": "default"},
            ],
            "summary": {"available_rule_count": 1, "enabled_rule_count": 1, "gate_status": "pending"},
        }
        dirty_plan = {
            "ok": True,
            "plan_id": "plan-test",
            "recommended_strategy": "prepare_gitignore_then_create_initial_snapshot",
            "steps": [
                {
                    "id": "update_gitignore_before_initial_commit",
                    "order": 0,
                    "label": "Clean up .gitignore before first commit",
                    "kind": "ignore",
                    "why": "Review ignore rules first.",
                    "paths": ["__pycache__/"],
                    "commands": [],
                    "gitignore_file": {"exists": True, "relative_path": ".gitignore"},
                },
                {
                    "id": "secrets_filter",
                    "order": 1,
                    "label": "Secrets / Filter",
                    "kind": "workflow",
                    "why": "Review security/privacy rules before commit.",
                    "paths": ["app.py"],
                    "commands": [],
                    "locked": True,
                    "requires": ["update_gitignore_before_initial_commit"],
                    "secrets_filter": secrets_filter,
                },
                {
                    "id": "prepare_commit_snapshot",
                    "order": 2,
                    "label": "Take Snapshot / Commit",
                    "kind": "commit",
                    "why": "Prepare a local commit.",
                    "paths": ["app.py"],
                    "commands": [],
                    "locked": True,
                    "requires": ["update_gitignore_before_initial_commit", "secrets_filter"],
                    "commit_review": {"mode": "first_commit"},
                },
            ],
        }

        wizard = self.service._wizard_from_dirty_plan(
            dirty_plan,
            git={"is_git_repo": True, "has_head": False},
            project={"locked": False},
        )

        step_ids = [step["id"] for step in wizard["steps"]]
        self.assertLess(step_ids.index("update_gitignore_before_initial_commit"), step_ids.index("secrets_filter"))
        self.assertLess(step_ids.index("secrets_filter"), step_ids.index("prepare_commit_snapshot"))
        filter_step = next(step for step in wizard["steps"] if step["id"] == "secrets_filter")
        self.assertEqual(filter_step["secrets_filter"], secrets_filter)
        self.assertEqual(filter_step["secrets_filter"]["rules"][0]["id"], "detect_secrets")

    def test_secrets_filter_model_does_not_show_saved_rows_without_policy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self.assertFalse((repo / ".git_dirty_rules.json").exists())

            model = self.service._git_project_secrets_filter_model(
                repo,
                candidate_paths=["app.py"],
            )

        self.assertFalse(model["policy"]["exists"])
        self.assertFalse(model["saved_policy_exists"])
        self.assertEqual(len(model["rules"]), 5)
        self.assertEqual(model.get("saved_rules", []), [])
        self.assertEqual(model.get("saved_summary", {}).get("enabled_rule_count", 0), 0)

    def test_secrets_filter_full_saved_scan_without_policy_does_not_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "app.py").write_text("api_key = 'sk-1234567890abcdefghijklmnop'\n", encoding="utf-8")
            self.assertFalse((repo / ".git_dirty_rules.json").exists())

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:run_saved_filter_check:0",
                "label": "Run full saved filter check",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "run_saved_filter_check",
                    "rule_choices": {
                        "windows_user_paths": True,
                        "unix_user_paths": True,
                        "user_names": True,
                        "secrets": True,
                        "detect_secrets": False,
                    },
                    "candidate_paths": ["app.py"],
                },
            })

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "secrets-filter")
        self.assertNotIn("scan_job_id", result)
        self.assertEqual(result["scan_result"]["mode"], "no_saved_policy")
        self.assertEqual(result["scan_result"]["summary"]["finding_count"], 0)
        self.assertEqual(result["secrets_filter"].get("saved_rules", []), [])

    def test_secrets_filter_merge_action_writes_policy_and_returns_saved_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            choices = {
                "windows_user_paths": True,
                "unix_user_paths": False,
                "user_names": True,
                "secrets": True,
                "detect_secrets": False,
            }

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:merge_rule_choices:0",
                "label": "Merge rule choices",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "merge_rule_choices",
                    "rule_choices": choices,
                    "candidate_paths": ["app.py"],
                },
            })

            policy = json.loads((repo / ".git_dirty_rules.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "merge_rule_choices")
        self.assertEqual(policy["policy_version"], 1)
        self.assertEqual(policy["rules"], choices)
        saved_rules = {rule["id"]: rule for rule in result["secrets_filter"]["saved_rules"]}
        self.assertFalse(saved_rules["unix_user_paths"]["enabled"])
        self.assertEqual(saved_rules["unix_user_paths"]["source"], "saved")
        self.assertEqual(result["scan_result"]["mode"], "policy_saved")

    def test_secrets_filter_saved_policy_checkbox_update_persists_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            initial_choices = {
                "windows_user_paths": False,
                "unix_user_paths": True,
                "user_names": True,
                "secrets": True,
                "detect_secrets": True,
            }
            (repo / ".git_dirty_rules.json").write_text(
                json.dumps({"policy_version": 1, "rules": initial_choices}, indent=2) + "\n",
                encoding="utf-8",
            )
            updated_choices = {
                **initial_choices,
                "detect_secrets": False,
            }

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:update_saved_rule_choices:0",
                "label": "Save saved rule choice",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "update_saved_rule_choices",
                    "rule_choices": {"detect_secrets": False},
                    "changed_rule_id": "detect_secrets",
                    "changed_rule_enabled": False,
                    "candidate_paths": ["app.py"],
                },
            })

            policy = json.loads((repo / ".git_dirty_rules.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "update_saved_rule_choices")
        self.assertEqual(policy["rules"], updated_choices)
        saved_rules = {rule["id"]: rule for rule in result["secrets_filter"]["saved_rules"]}
        self.assertFalse(saved_rules["windows_user_paths"]["enabled"])
        self.assertTrue(saved_rules["unix_user_paths"]["enabled"])
        self.assertFalse(saved_rules["detect_secrets"]["enabled"])
        self.assertEqual(result["secrets_filter"]["saved_summary"]["enabled_rule_count"], 3)
        self.assertEqual(result["scan_result"]["mode"], "policy_saved")
        self.assertIn("immediately", result["scan_result"]["pending_message"])

    def test_secrets_filter_draft_scan_uses_left_choices_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "app.py").write_text("api_key = 'sk-1234567890abcdefghijklmnop'\n", encoding="utf-8")
            choices = {
                "windows_user_paths": False,
                "unix_user_paths": False,
                "user_names": False,
                "secrets": True,
                "detect_secrets": False,
            }

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:run_selected_rules:0",
                "label": "Run selected rules only",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "run_selected_rules",
                    "rule_choices": choices,
                    "candidate_paths": ["app.py"],
                },
            })

            policy_exists = (repo / ".git_dirty_rules.json").exists()
            self.assertTrue(result["ok"], result)
            self.assertFalse(policy_exists)
            self.assertEqual(result["mode"], "secrets-filter-stream")
            self.assertEqual(result["scan_result"]["mode"], "draft_selected_rules")
            self.assertEqual(result["scan_result"]["status"], "running")
            self.assertIn("scan_job_id", result)
            self.assertIn("stream_url", result)
            events = self._wait_for_secrets_scan_job(result["scan_job_id"])

        event_types = [event["type"] for event in events]
        self.assertIn("started", event_types)
        self.assertIn("file_scanned", event_types)
        self.assertIn("finding", event_types)
        self.assertIn("finished", event_types)
        finding_events = [event for event in events if event.get("type") == "finding"]
        finding_rule_ids = {event["finding"]["rule_id"] for event in finding_events}
        self.assertEqual(finding_rule_ids, {"secrets"})
        self.assertTrue(any("sk-1234567890abcdefghijklmnop" in event["finding"].get("evidence", "") for event in finding_events))
        draft_rules = {rule["id"]: rule for rule in result["secrets_filter"]["rules"]}
        self.assertTrue(draft_rules["secrets"]["enabled"])
        self.assertFalse(draft_rules["windows_user_paths"]["enabled"])

    def test_secrets_filter_scan_truncates_long_evidence_and_allows_windows_example_path(self) -> None:
        long_secret = "0x" + ("7" * 260) + "4513"
        allowed_path = windows_user_path(ALLOWED_WINDOWS_EXAMPLE_USER)
        bad_path = windows_user_path("USERNAME")
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "app.py").write_text(
                "\n".join([
                    f'safe_path = "{allowed_path}"',
                    f'bad_path = "{bad_path}"',
                    f"api_key = '{long_secret}'",
                ]) + "\n",
                encoding="utf-8",
            )
            choices = {
                "windows_user_paths": True,
                "unix_user_paths": False,
                "user_names": True,
                "secrets": True,
                "detect_secrets": False,
            }

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:run_selected_rules:0",
                "label": "Run selected rules only",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "run_selected_rules",
                    "rule_choices": choices,
                    "candidate_paths": ["app.py"],
                },
            })
            events = self._wait_for_secrets_scan_job(result["scan_job_id"])

        finding_events = [event for event in events if event.get("type") == "finding"]
        evidences = [str(event["finding"].get("evidence", "")) for event in finding_events]
        self.assertFalse(any(allowed_path in evidence for evidence in evidences))
        self.assertTrue(any(bad_path in evidence for evidence in evidences))
        secret_evidence = next(evidence for evidence in evidences if evidence.startswith(long_secret[:100]))
        self.assertEqual(len(secret_evidence), 203)
        self.assertTrue(secret_evidence.endswith(long_secret[-100:]))
        self.assertIn("...", secret_evidence)
        self.assertNotEqual(secret_evidence, long_secret)
        secret_finding = next(event["finding"] for event in finding_events if event["finding"].get("evidence") == secret_evidence)
        self.assertTrue(secret_finding.get("evidence_truncated"))
        self.assertEqual(secret_finding.get("evidence_length"), len(long_secret))

    def test_secrets_filter_full_saved_scan_uses_saved_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "app.py").write_text("api_key = 'sk-1234567890abcdefghijklmnop'\n", encoding="utf-8")
            (repo / ".git_dirty_rules.json").write_text(json.dumps({
                "policy_version": 1,
                "rules": {
                    "windows_user_paths": False,
                    "unix_user_paths": False,
                    "user_names": False,
                    "secrets": False,
                    "detect_secrets": False,
                },
            }), encoding="utf-8")

            result = self.service.run_git_project_panel_action({
                "action_key": "secrets_filter:run_saved_filter_check:0",
                "label": "Run full saved filter check",
                "repo_dir": str(repo),
                "commands": [],
                "state": {
                    "repo": str(repo),
                    "secrets_filter_action": "run_saved_filter_check",
                    "rule_choices": {
                        "windows_user_paths": False,
                        "unix_user_paths": False,
                        "user_names": False,
                        "secrets": True,
                        "detect_secrets": False,
                    },
                    "candidate_paths": ["app.py"],
                },
            })

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["mode"], "secrets-filter-stream")
            self.assertEqual(result["scan_result"]["mode"], "full_saved_policy")
            self.assertEqual(result["scan_result"]["status"], "running")
            events = self._wait_for_secrets_scan_job(result["scan_job_id"])

        finished = next(event for event in events if event.get("type") == "finished")
        self.assertEqual(finished["scan_result"]["summary"]["finding_count"], 0)
        saved_rules = {rule["id"]: rule for rule in result["secrets_filter"]["saved_rules"]}
        draft_rules = {rule["id"]: rule for rule in result["secrets_filter"]["rules"]}
        self.assertFalse(saved_rules["secrets"]["enabled"])
        self.assertFalse(draft_rules["secrets"]["enabled"])

    def test_wizard_places_take_snapshot_before_local_gitea(self) -> None:
        dirty_plan = {
            "ok": True,
            "plan_id": "plan-test",
            "recommended_strategy": "prepare_gitignore_then_create_initial_snapshot",
            "steps": [
                {
                    "id": "update_gitignore_before_initial_commit",
                    "order": 0,
                    "label": "Clean up .gitignore before first commit",
                    "kind": "ignore",
                    "why": "Review ignore rules first.",
                    "paths": ["__pycache__/"],
                    "commands": [],
                },
                {
                    "id": "make_cleanup_plan",
                    "order": 1,
                    "label": "Make cleanup plan",
                    "kind": "analysis",
                    "why": "Re-run planning.",
                    "paths": [],
                    "commands": [],
                },
                {
                    "id": "prepare_commit_snapshot",
                    "order": 2,
                    "label": "Take Snapshot / Commit",
                    "kind": "commit",
                    "why": "Prepare a local commit.",
                    "paths": ["app.py"],
                    "commands": [],
                    "locked": True,
                    "requires": ["update_gitignore_before_initial_commit", "make_cleanup_plan"],
                    "commit_review": {"mode": "first_commit"},
                },
            ],
        }

        wizard = self.service._wizard_from_dirty_plan(
            dirty_plan,
            git={"is_git_repo": True, "has_head": False},
            project={"locked": False},
        )

        step_ids = [step["id"] for step in wizard["steps"]]
        self.assertLess(step_ids.index("prepare_commit_snapshot"), step_ids.index("push-local-gitea"))
        commit_step = next(step for step in wizard["steps"] if step["id"] == "prepare_commit_snapshot")
        self.assertEqual(commit_step["order"], 2)
        self.assertEqual(commit_step["commit_review"]["mode"], "first_commit")

    def test_git_status_reports_patch_capabilities(self) -> None:
        report = self.service.git_status(".")
        self.assertTrue(report["ok"])
        self.assertTrue(report["is_git_repo"])
        self.assertIn("branch", report)
        self.assertIn("patching", report)
        self.assertIn("patch_list", report["capabilities"])
        self.assertIn("patch_apply", report["capabilities"])
        self.assertIn("patch_dry_run_preview", report["capabilities"])
        if self.service.patching_available:
            self.assertTrue(report["capabilities"]["patch_list"])
            self.assertTrue(report["capabilities"]["patch_apply"])
            self.assertTrue(report["capabilities"]["patch_dry_run_preview"])
        else:
            self.assertFalse(report["capabilities"]["patch_list"])
            self.assertIn("patch_import_error", report["capabilities"])
        self.assertTrue(report["capabilities"]["git_control_cli"]["available"])
        self.assertEqual(report["capabilities"]["git_control_cli"]["doc_shim_command"], "python git-control.py --doc-shim <git-command>")
        self.assertEqual(report["capabilities"]["git_control_cli"]["list_shims_command"], "python git-control.py --list-shims")
        self.assertEqual(report["capabilities"]["git_control_cli"]["delete_shim_command"], "python git-control.py --delete-shim <shim-id>")
        self.assertEqual(report["capabilities"]["git_control_cli"]["extract_ai_output_command"], "python git-control.py --extract-shims-from <file>")
        self.assertEqual(report["capabilities"]["git_control_cli"]["ordain_shim_command"], "python git-control.py --ordain-shim <shim-id>")
        self.assertEqual(report["capabilities"]["git_control_cli"]["ai_brief_command"], "python git-control.py --ai-brief --prompt <request>")
        self.assertEqual(report["capabilities"]["git_control_cli"]["recommendation_values"], ["good", "not-recommended"])
        self.assertEqual(report["capabilities"]["git_control_cli"]["shim_format"], "metadata-rich .shim files with #-comments, ordination metadata, and git-control shim-code")
        self.assertTrue(any(item["name"] == "arbitrary_git_args" for item in report["capabilities"]["planned_git_commands"]))
        self.assertTrue(any(item["name"] == "plan_shim" for item in report["capabilities"]["planned_git_commands"]))
        self.assertTrue(any(item["name"] == "documentation_shims" for item in report["capabilities"]["planned_git_commands"]))
        self.assertTrue(any(item["name"] == "git_console" for item in report["capabilities"]["planned_git_commands"]))
        self.assertTrue(any(item["name"] == "ai_generated_shims" for item in report["capabilities"]["planned_git_commands"]))
        self.assertTrue(any(item["name"] == "ordained_context" for item in report["capabilities"]["planned_git_commands"]))

    def test_git_server_wait_reports_container_exit_logs(self) -> None:
        service = self._service()
        calls = {"ps": 0, "logs": 0}
        import urllib.error

        original_urlopen = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("connection refused"))  # type: ignore[assignment]
        try:
            def fake_run_docker_compose(args: list[str], **kwargs: object) -> dict[str, object]:
                if args[:2] == ["ps", service.GIT_SERVER_SERVICE]:
                    calls["ps"] += 1
                    return {
                        "command": ["docker", "compose", *args],
                        "returncode": 0,
                        "stdout": "NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS\n",
                        "stderr": "",
                    }
                if args[:2] == ["logs", "--tail"]:
                    calls["logs"] += 1
                    return {
                        "command": ["docker", "compose", *args],
                        "returncode": 0,
                        "stdout": "gitea failed to start because app.ini is invalid\n",
                        "stderr": "",
                    }
                raise AssertionError(args)

            service._run_docker_compose = fake_run_docker_compose  # type: ignore[method-assign]
            wait = service._wait_for_gitea_http(timeout_s=5)
        finally:
            urllib.request.urlopen = original_urlopen  # type: ignore[assignment]

        self.assertFalse(wait["ok"], wait)
        self.assertEqual(wait["reason"], "container-not-running")
        self.assertIn("not running", wait["error"])
        self.assertIn("app.ini is invalid", wait["error"])
        self.assertIn("logs", wait)
        self.assertGreaterEqual(calls["ps"], 1)
        self.assertEqual(calls["logs"], 1)

    def test_docker_compose_exec_runs_gitea_cli_as_git_user(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_docker_compose(args: list[str], *, allow_failure: bool = False, timeout: int = 60) -> dict[str, object]:
            captured["args"] = args
            captured["allow_failure"] = allow_failure
            captured["timeout"] = timeout
            return {"command": ["docker", "compose", *args], "returncode": 0, "stdout": "", "stderr": ""}

        self.service._run_docker_compose = fake_run_docker_compose  # type: ignore[method-assign]
        result = self.service._docker_compose_exec(["gitea", "admin", "user", "list"], allow_failure=True, timeout=12)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(
            captured["args"],
            ["exec", "-T", "--user", "git", "gitea", "gitea", "admin", "user", "list"],
        )
        self.assertTrue(captured["allow_failure"])
        self.assertEqual(captured["timeout"], 12)

    def test_git_server_capabilities_are_exposed_without_running_docker(self) -> None:
        capabilities = self.service.capabilities()["git_server"]
        self.assertTrue(capabilities["available"])
        self.assertEqual(capabilities["service"], "gitea")
        self.assertEqual(capabilities["profile"], "")
        self.assertEqual(capabilities["compose_project"], "main-computer-gitea")
        self.assertEqual(capabilities["web_url"], "http://localhost:3000/")
        self.assertFalse(capabilities["ssh_available"])
        self.assertIn("docker-compose.gitea.yml", capabilities["start_command"])
        self.assertIn("up -d gitea", capabilities["start_command"])
        self.assertNotIn("--env-file", capabilities["start_command"])
        self.assertEqual(capabilities["clone_examples"], ["http://localhost:3000/<owner>/<repo>.git"])
        servers = {item["name"]: item for item in capabilities["managed_server_options"]}
        self.assertEqual(set(servers), {"shared"})
        self.assertEqual(servers["shared"]["service"], "gitea")
        self.assertEqual(servers["shared"]["web_url"], "http://localhost:3000/")
        self.assertFalse(servers["shared"]["ssh_available"])
        self.assertTrue(servers["shared"]["persistent"])
        self.assertIn("up -d gitea", servers["shared"]["start_command"])
        self.assertEqual(servers["shared"]["clone_examples"], ["http://localhost:3000/<owner>/<repo>.git"])
        self.assertEqual(capabilities["default_local_remote"]["remote"], "local-gitea")
        self.assertEqual(capabilities["default_local_remote"]["owner"], "local")
        self.assertEqual(capabilities["default_local_remote"]["configure_endpoint"], "/api/applications/git/server/remote/configure")
        self.assertEqual(capabilities["default_local_remote"]["prefunk_endpoint"], "/api/applications/git/server/target-prefunk")
        self.assertEqual(capabilities["default_local_remote"]["setup_endpoint"], "/api/applications/git/server/setup-local")
        self.assertEqual(capabilities["default_local_remote"]["push_endpoint"], "/api/applications/git/server/push-local")
        self.assertEqual(capabilities["default_local_remote"]["mirror_setup_endpoint"], "/api/applications/git/server/mirror/setup")
        self.assertTrue(capabilities["default_local_remote"]["preserve_origin_by_default"])
        external_commands = "\n".join(item["command"] for item in capabilities["external_remote_options"])
        self.assertIn("git remote set-url origin <external-url>", external_commands)
        self.assertIn("git remote add upstream <external-url>", external_commands)
        preset_commands = "\n".join(item["command"] for item in capabilities["remote_command_presets"])
        self.assertIn("git remote add <remote>", preset_commands)
        self.assertIn("git remote set-url <remote>", preset_commands)
        self.assertIn("git push -u <remote> HEAD", preset_commands)

        status = self.service.git_server_status()
        self.assertTrue(status["compose_file_exists"])
        self.assertTrue(status["configured"])
        self.assertIn("docker_available", status)

        with self.assertRaises(ValueError):
            self.service.git_server_action("destroy")

    def test_git_server_docker_unavailable_payload_has_next_actions(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        service = self._service()
        original_which = shutil.which
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            shutil.which = lambda name: None if name == "docker" else original_which(name)  # type: ignore[assignment]
            try:
                action = service.git_server_action("start")
                setup = service.setup_local_git_server(repo_dir=str(temp_repo), owner="local", repo_name="demo")
            finally:
                shutil.which = original_which  # type: ignore[assignment]

        for payload in (action, setup):
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["reason"], "docker-cli-unavailable")
            self.assertTrue(payload["requires_docker_cli"])
            self.assertTrue(payload["can_configure_local_remote_without_docker"])
            self.assertIn("next_actions", payload)
            self.assertIn("Docker CLI", payload["error"])
            self.assertIn("Apply Command", payload["manual_remote_note"])

    def test_git_server_configures_local_remote_add_and_update(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            service = GitToolsService(temp_repo)

            add = service.configure_git_server_remote(
                repo_dir=".",
                remote="origin",
                owner="local",
                repo_name="demo",
                protocol="http",
            )
            self.assertTrue(add["ok"], add)
            self.assertEqual(add["action"], "add")
            self.assertEqual(add["url"], "http://localhost:3000/local/demo.git")
            self.assertIn("http://localhost:3000/local/demo.git", add["remotes"]["stdout"])

            with self.assertRaisesRegex(ValueError, "SSH is disabled"):
                service.configure_git_server_remote(
                    repo_dir=".",
                    remote="origin",
                    owner="local",
                    repo_name="demo",
                    protocol="ssh",
                )

    def test_git_server_configures_local_remote_initializes_missing_git_repo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            service = GitToolsService(temp_repo)

            add = service.configure_git_server_remote(
                repo_dir=".",
                remote="local",
                owner="local",
                repo_name="demo",
                protocol="http",
            )
            self.assertTrue(add["ok"], add)
            self.assertTrue(add["git_init"]["created"], add)
            self.assertEqual(add["action"], "add")
            self.assertIn("http://localhost:3000/local/demo.git", add["remotes"]["stdout"])
            self.assertTrue((temp_repo / ".git").exists())


    def test_git_server_target_prefunk_suggests_from_git_root_without_saving(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory(prefix="prefunk-demo-") as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            service = GitToolsService(temp_repo)

            prefunk = service.git_server_target_prefunk(".")

            self.assertTrue(prefunk["ok"], prefunk)
            self.assertTrue(prefunk["is_git_repo"], prefunk)
            self.assertFalse(prefunk["has_head"], prefunk)
            self.assertEqual(prefunk["target"]["remote"], "local-gitea")
            self.assertEqual(prefunk["target"]["owner"], "local")
            self.assertEqual(prefunk["target"]["repo"], temp_repo.name)
            self.assertEqual(prefunk["target"]["source"], "suggested-from-git-root")
            self.assertFalse(prefunk["target"]["saved"])
            self.assertFalse(prefunk["target"]["pushable"])

    def test_git_server_target_prefunk_detects_saved_local_gitea_remote(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "remote", "add", "local-gitea", "http://localhost:3000/acme/demo.git"],
                cwd=temp_repo,
                check=True,
                capture_output=True,
                text=True,
            )
            service = GitToolsService(temp_repo)

            prefunk = service.git_server_target_prefunk(".")

            self.assertTrue(prefunk["ok"], prefunk)
            self.assertTrue(prefunk["is_git_repo"], prefunk)
            self.assertEqual(prefunk["target"]["remote"], "local-gitea")
            self.assertEqual(prefunk["target"]["owner"], "acme")
            self.assertEqual(prefunk["target"]["repo"], "demo")
            self.assertEqual(prefunk["target"]["protocol"], "http")
            self.assertEqual(prefunk["target"]["source"], "detected-from-git-remote")
            self.assertTrue(prefunk["target"]["saved"])

    def test_setup_local_git_server_rejects_non_git_project_without_initializing(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            service = GitToolsService(temp_repo)

            result = service.setup_local_git_server(repo_dir=".", owner="local", repo_name="demo")

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["reason"], "git-init-required")
            self.assertEqual(result["target"]["remote"], "local-gitea")
            self.assertFalse((temp_repo / ".git").exists())

    def test_git_console_remote_add_initializes_missing_git_repo_and_uses_repo_dir(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested-repo"
            nested.mkdir()
            service = GitToolsService(root)

            add = service.run_git_console_command(
                "git remote add local http://localhost:3000/local/nested.git",
                repo_dir="nested-repo",
            )
            self.assertTrue(add["ok"], add)
            self.assertTrue(add["git_init"]["created"], add)
            self.assertEqual(add["action"], "add")
            self.assertEqual(Path(add["repo"]), nested.resolve())
            self.assertIn("http://localhost:3000/local/nested.git", add["remotes"]["stdout"])

    def test_git_server_configures_external_direct_remote(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            service = GitToolsService(temp_repo)

            add = service.configure_external_git_remote(
                repo_dir=".",
                remote="origin",
                url="https://github.com/example/demo.git",
            )
            self.assertTrue(add["ok"], add)
            self.assertEqual(add["action"], "add")
            self.assertIn("https://github.com/example/demo.git", add["remotes"]["stdout"])

            update = service.configure_external_git_remote(
                repo_dir=".",
                remote="origin",
                url="git@github.com:example/demo.git",
            )
            self.assertTrue(update["ok"], update)
            self.assertEqual(update["action"], "set-url")
            self.assertIn("git@github.com:example/demo.git", update["remotes"]["stdout"])

            with self.assertRaises(ValueError):
                service.configure_external_git_remote(repo_dir=".", remote="origin", url="file:///tmp/not-allowed.git")

    def test_gitea_push_mirror_plan_does_not_store_external_token(self) -> None:
        plan = self.service.plan_gitea_push_mirror(
            owner="local",
            repo_name="demo",
            external_url="https://github.com/example/demo.git",
            external_username="octocat",
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["mode"], "gitea-push-mirror-plan")
        self.assertIn("Set Up Server", "\n".join(plan["steps"]))
        self.assertNotIn("external-secret", json.dumps(plan).lower())

    def test_gitea_push_mirror_setup_redacts_external_secret(self) -> None:
        service = self._service()
        calls: list[tuple[str, str, dict[str, object] | None]] = []

        service.setup_local_git_server = lambda **kwargs: {"ok": True, "remote": "local-gitea", "url": "http://localhost:3000/local/demo.git"}  # type: ignore[method-assign]
        service._create_gitea_access_token = lambda username: {"ok": True, "token": "local-token"}  # type: ignore[method-assign]

        def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
            calls.append((method, path, kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else None))
            if method == "GET":
                return {"ok": True, "status": 200, "json": []}
            return {"ok": True, "status": 201, "json": {"remote_address": "https://github.com/example/demo.git"}}

        service._gitea_json_request = fake_request  # type: ignore[method-assign]
        result = service.setup_gitea_push_mirror(
            owner="local",
            repo_name="demo",
            external_url="https://github.com/example/demo.git",
            external_username="octocat",
            external_password="external-secret",
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "gitea-push-mirror-setup")
        self.assertIn(("POST", "/api/v1/repos/local/demo/push_mirrors"), [(method, path) for method, path, _payload in calls])
        post_payloads = [payload for method, _path, payload in calls if method == "POST" and payload]
        self.assertEqual(post_payloads[0]["remote_password"], "external-secret")
        self.assertNotIn("external-secret", json.dumps(result).lower())
        self.assertNotIn("local-token", json.dumps(result))

    def test_git_console_remote_commands_can_run_without_git_control_script(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True, text=True)
            service = GitToolsService(temp_repo)

            add = service.run_git_console_command("git remote add origin http://localhost:3000/local/demo.git")
            self.assertTrue(add["ok"], add)
            self.assertEqual(add["mode"], "idempotent-git-remote-add")
            self.assertEqual(add["action"], "add")

            add_again = service.run_git_console_command("git remote add origin http://localhost:3000/local/demo.git")
            self.assertTrue(add_again["ok"], add_again)
            self.assertEqual(add_again["action"], "already-configured")
            self.assertIn("Nothing changed", add_again["stdout"])

            add_new_url = service.run_git_console_command("git remote add origin ssh://git@example.com/local/demo.git")
            self.assertTrue(add_new_url["ok"], add_new_url)
            self.assertEqual(add_new_url["action"], "set-url")

            remotes = service.run_git_console_command("git remote -v")
            self.assertTrue(remotes["ok"], remotes)
            self.assertIn("ssh://git@example.com/local/demo.git", remotes["stdout"])

            set_url = service.run_git_console_command("git remote set-url origin http://localhost:3000/local/demo.git")
            self.assertTrue(set_url["ok"], set_url)
            remotes_after = service.run_git_console_command("git remote -v")
            self.assertIn("http://localhost:3000/local/demo.git", remotes_after["stdout"])

    def test_git_console_extracts_reads_runs_and_deletes_shims(self) -> None:
        extracted = self.service.extract_git_console_shims("python git-control.py --git status --short")
        self.assertTrue(extracted["ok"])
        self.assertTrue(extracted["shims"])
        shim_id = extracted["shims"][0]["id"]

        detail = self.service.read_git_shim(shim_id)
        self.assertTrue(detail["ok"])
        self.assertIn("git status --short", detail["git_commands"])

        run = self.service.run_git_shim(shim_id)
        self.assertIn("results", run)

        deleted = self.service.delete_git_shim(shim_id)
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["deleted"])

    def test_git_console_plain_git_command_saves_shim(self) -> None:
        result = self.service.run_git_console_command("git status --short")
        self.assertIn("shim", result)
        shim = result.get("shim") or {}
        shim_id = shim.get("id", "")
        self.assertTrue(shim_id)

        deleted = self.service.delete_git_shim(shim_id)
        self.assertTrue(deleted["ok"])

    def test_git_ai_brief_loads_ordained_shim_context(self) -> None:
        result = self.service.run_git_console_command("git status --short")
        shim_id = (result.get("shim") or {}).get("id", "")
        self.assertTrue(shim_id)

        ordained = self.service.ordain_git_shim(shim_id)
        self.assertTrue(ordained["ok"])

        brief = self.service.git_ai_brief("recommend a safe inspection shim")
        self.assertTrue(brief["ok"])
        self.assertIn("Ordained shim context:", brief["prompt"])
        self.assertIn("git status --short", brief["prompt"])
        self.assertIn("ordination-recommendation", brief["prompt"])

        deleted = self.service.delete_git_shim(shim_id)
        self.assertTrue(deleted["ok"])

    def test_patch_inventory_preview_and_dry_run_manifest(self) -> None:
        if not self.service.patching_available:
            self.skipTest("Patch harness is unavailable in this snapshot.")
        self.incoming.mkdir(parents=True, exist_ok=True)
        self._write_patch_fixture()

        inventory = self.service.list_patches()
        self.assertTrue(inventory["ok"])
        self.assertTrue(any(item["name"] == self.temp_patch.name for item in inventory["incoming"]))

        preview = self.service.read_patch(self.temp_patch.name)
        self.assertTrue(preview["ok"])
        self.assertIn("git_patch_test_target.txt", preview["preview"])

        run = self.service.apply_patch(
            patch_name=self.temp_patch.name,
            target_root=".",
            dry_run=True,
        )
        self.assertTrue(run["ok"])
        self.assertTrue(run["dry_run"])
        self.assertTrue(run["dry_run_output_dir"])

        dry_run_dir = Path(run["dry_run_output_dir"])
        manifest_path = dry_run_dir / "manifest.json"
        preview_target = dry_run_dir / "files" / "git_patch_test_target.txt"

        self.assertTrue(manifest_path.exists())
        self.assertTrue(preview_target.exists())
        self.assertEqual(preview_target.read_text(encoding="utf-8"), "alpha\nbeta\n")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("changes", manifest)
        self.assertTrue(any(item["resolved_path"] == "git_patch_test_target.txt" for item in manifest["changes"]))

        runs = self.service.list_dry_runs(limit=10)
        self.assertTrue(runs["ok"])
        self.assertTrue(any(item["name"] == dry_run_dir.name for item in runs["runs"]))

        detail = self.service.read_dry_run(dry_run_dir.name)
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["name"], dry_run_dir.name)
        self.assertTrue(any(item["relative_path"] == "git_patch_test_target.txt" for item in detail["preview_files"]))


    def test_git_operation_lock_rejects_parallel_operation(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_operation() -> dict[str, object]:
            started.set()
            release.wait(timeout=5)
            return {"ok": True, "value": "done"}

        results: list[dict[str, object]] = []

        thread = threading.Thread(
            target=lambda: results.append(self.service.run_git_operation("slow", "Slow operation", slow_operation)),
            daemon=True,
        )
        thread.start()
        self.assertTrue(started.wait(timeout=2))

        busy = self.service.run_git_operation("second", "Second operation", lambda: {"ok": True})
        self.assertFalse(busy["ok"])
        self.assertTrue(busy["busy"])
        self.assertIn("already running", busy["error"])
        self.assertTrue(self.service.git_operation_status()["busy"])

        release.set()
        thread.join(timeout=5)
        self.assertEqual(results[0]["operation"]["status"], "succeeded")
        self.assertFalse(self.service.git_operation_status()["busy"])

    def test_git_operation_logs_result_for_ui(self) -> None:
        result = self.service.run_git_operation(
            "unit-test",
            "Unit test operation",
            lambda: {"ok": True, "answer": 42},
            payload={"source": "test"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["operation"]["status"], "succeeded")
        status = self.service.git_operation_status()
        self.assertFalse(status["busy"])
        self.assertTrue(status["history"])
        last = status["history"][-1]
        self.assertEqual(last["label"], "Unit test operation")
        self.assertEqual(last["result"]["answer"], 42)
        self.assertTrue(any("Operation started" in entry["message"] for entry in last["logs"]))

    def test_git_operation_cancel_marks_active_operation(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def cancellable() -> dict[str, object]:
            started.set()
            release.wait(timeout=5)
            return {"ok": False, "error": "cancelled by test"}

        results: list[dict[str, object]] = []
        thread = threading.Thread(
            target=lambda: results.append(self.service.run_git_operation("cancel-test", "Cancel test", cancellable)),
            daemon=True,
        )
        thread.start()
        self.assertTrue(started.wait(timeout=2))
        cancel = self.service.cancel_git_operation()
        self.assertTrue(cancel["ok"])
        self.assertTrue(cancel["cancelled"])
        release.set()
        thread.join(timeout=5)

        self.assertEqual(results[0]["operation"]["status"], "cancelled")
        self.assertFalse(self.service.git_operation_status()["busy"])

    def test_run_command_drains_large_stdout_without_waiting_for_timeout(self) -> None:
        payload_size = 256 * 1024
        script = (
            "import os, sys, time\n"
            "remaining = int(sys.argv[1])\n"
            "chunk = b'x' * 4096\n"
            "while remaining:\n"
            "    try:\n"
            "        written = os.write(1, chunk[:min(len(chunk), remaining)])\n"
            "        remaining -= written\n"
            "    except BlockingIOError:\n"
            "        time.sleep(0.005)\n"
        )
        started = time.monotonic()

        result = self.service._run_command(
            [sys.executable, "-c", script, str(payload_size)],
            cwd=self.repo_root,
            timeout=5,
        )

        self.assertEqual(
            result["returncode"],
            0,
            f"returncode={result['returncode']} stdout_len={len(result['stdout'])} stderr={result['stderr'][:500]}",
        )
        self.assertEqual(len(result["stdout"]), payload_size)
        self.assertLess(time.monotonic() - started, 5)

    def test_git_project_registry_defaults_to_mct_without_unlocking_main_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"MAIN_COMPUTER_DEFAULT_MCT_WORKTREE": ""}):
                service = GitToolsService(Path(temp_dir))
                projects = service.git_projects()
            by_id = {item["id"]: item for item in projects["projects"]}

            self.assertEqual(projects["current_project_id"], "default-mct-worktree")
            self.assertEqual(projects["current_project"]["path"], str(Path.home() / "mct"))

            main = by_id["main-computer"]
            self.assertTrue(main["vip"])
            self.assertTrue(main["locked"])
            self.assertFalse(main["can_archive"])

            mct = by_id["default-mct-worktree"]
            self.assertFalse(mct["vip"])
            self.assertFalse(mct["locked"])
            self.assertTrue(mct["can_archive"])

        with self.assertRaises(ValueError):
            self.service.archive_git_project(project_id="main-computer")

    def test_git_project_registry_default_mct_worktree_uses_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_worktree = Path(temp_dir) / "custom-mct"
            with mock.patch.dict(os.environ, {"MAIN_COMPUTER_DEFAULT_MCT_WORKTREE": str(default_worktree)}):
                service = GitToolsService(Path(temp_dir))
                projects = service.git_projects()

        self.assertEqual(projects["current_project_id"], "default-mct-worktree")
        self.assertEqual(projects["current_project"]["path"], str(default_worktree))

    def test_git_project_registry_adds_external_project_and_can_archive_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            added = self.service.add_git_project(str(external), select=True)
            project = added["project"]
            self.assertFalse(project["vip"])
            self.assertFalse(project["locked"])
            self.assertTrue(project["can_archive"])
            self.assertEqual(Path(project["path"]), external.resolve())

            archived = self.service.archive_git_project(project_id=project["id"])
            self.assertTrue(any(item["id"] == project["id"] for item in archived["archived_projects"]))

            restored = self.service.restore_git_project(project_id=project["id"], select=True)
            self.assertTrue(any(item["id"] == project["id"] for item in restored["projects"]))
            self.assertFalse(any(item["id"] == project["id"] for item in restored["archived_projects"]))
            self.assertEqual(restored["current_project_id"], project["id"])

    def test_git_project_add_restores_archived_project_for_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            added = self.service.add_git_project(str(external), select=True)
            project = added["project"]

            self.service.archive_git_project(project_id=project["id"])
            readded = self.service.add_git_project(str(external), select=True)

            self.assertEqual(readded["project"]["id"], project["id"])
            self.assertEqual(readded["current_project_id"], project["id"])
            self.assertTrue(any(item["id"] == project["id"] for item in readded["projects"]))
            self.assertFalse(any(item["id"] == project["id"] for item in readded["archived_projects"]))

    def test_archived_default_mct_worktree_is_not_reinserted_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_worktree = Path(temp_dir) / "mct"
            with mock.patch.dict(os.environ, {"MAIN_COMPUTER_DEFAULT_MCT_WORKTREE": str(default_worktree)}):
                service = self._service(Path(temp_dir))
                initial = service.git_projects()
                self.assertTrue(any(item["id"] == "default-mct-worktree" for item in initial["projects"]))

                archived = service.archive_git_project(project_id="default-mct-worktree")
                active_ids = {item["id"] for item in archived["projects"]}
                archived_ids = {item["id"] for item in archived["archived_projects"]}
                self.assertNotIn("default-mct-worktree", active_ids)
                self.assertIn("default-mct-worktree", archived_ids)
                self.assertEqual(archived["current_project_id"], "main-computer")

                listed = service.git_projects()
                listed_active_ids = {item["id"] for item in listed["projects"]}
                listed_archived_ids = {item["id"] for item in listed["archived_projects"]}
                self.assertNotIn("default-mct-worktree", listed_active_ids)
                self.assertIn("default-mct-worktree", listed_archived_ids)

    def test_commit_job_uses_selected_project_when_payload_repo_is_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir).resolve()
            added = self.service.add_git_project(str(external), select=True)
            project = added["project"]
            captured: dict[str, object] = {}

            class DummyCommitJobs:
                def start_job(self, payload: dict[str, object]) -> dict[str, object]:
                    captured.update(payload)
                    return {"ok": True, "job_id": "dummy"}

            self.service._commit_jobs = DummyCommitJobs()  # type: ignore[assignment]

            result = self.service.start_git_project_commit_job({"repo_dir": ".", "paths": [".dockerignore"]})

            self.assertTrue(result["ok"], result)
            self.assertEqual(Path(str(captured["repo_dir"])), external)
            self.assertEqual(captured["repo_dir_source"], f"current_project:{project['id']}")
            self.assertEqual(captured["project_id"], project["id"])
            self.assertEqual(Path(str(captured["project_path"])), external)

    def test_commit_job_refuses_project_id_with_stale_repo_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir).resolve()
            added = self.service.add_git_project(str(external), select=True)
            project = added["project"]

            with self.assertRaisesRegex(ValueError, "Commit target mismatch"):
                self.service.start_git_project_commit_job({
                    "project_id": project["id"],
                    "repo_dir": str(self.repo_root),
                    "paths": [".dockerignore"],
                })

    def test_commit_job_refuses_project_id_with_stale_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir) / "external"
            other = Path(temp_dir) / "other-project"
            external.mkdir()
            other.mkdir()
            added = self.service.add_git_project(str(external), select=True)
            project = added["project"]

            with self.assertRaisesRegex(ValueError, "Commit target mismatch"):
                self.service.start_git_project_commit_job({
                    "project_id": project["id"],
                    "project_path": str(other),
                    "paths": [".dockerignore"],
                })

    def test_commit_job_refuses_implicit_app_root_fallback(self) -> None:
        self.service.select_git_project(project_id="main-computer")
        with self.assertRaisesRegex(ValueError, "refusing to default selected-file commit execution to the app repository"):
            self.service.start_git_project_commit_job({"repo_dir": ".", "paths": [".dockerignore"]})

    def test_git_project_inspection_renders_dirty_plan_wizard_for_no_head_repo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=external, check=True, capture_output=True, text=True)
            (external / "README.md").write_text("# demo\n", encoding="utf-8")
            added = self.service.add_git_project(str(external), select=True)
            inspection = self.service.inspect_git_project(project_id=added["project"]["id"])

            self.assertTrue(inspection["ok"], inspection)
            self.assertTrue(inspection["git"]["is_git_repo"])
            self.assertFalse(inspection["git"]["has_head"])
            self.assertTrue(inspection["wizard"]["steps"])
            step_ids = [step["id"] for step in inspection["wizard"]["steps"]]
            self.assertTrue(any(step_id in {"create_initial_snapshot", "prepare_commit_snapshot", "initial-snapshot-required"} for step_id in step_ids))
            self.assertTrue(any(item["reason"] == "initial-snapshot-required" for item in inspection["blocking"]))

    def test_git_project_inspection_treats_selected_folder_without_git_metadata_as_not_initialized(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            child = parent / "mct"
            child.mkdir()
            subprocess.run(["git", "init"], cwd=parent, check=True, capture_output=True, text=True)
            (child / "README.md").write_text("# mct\n", encoding="utf-8")
            added = self.service.add_git_project(str(child), select=True)
            inspection = self.service.inspect_git_project(project_id=added["project"]["id"])

            self.assertTrue(inspection["ok"], inspection)
            self.assertFalse(inspection["git"]["is_git_repo"])
            self.assertFalse(inspection["git"]["has_head"])
            self.assertFalse(inspection["git"]["selected_path_has_git_metadata"])
            self.assertEqual(inspection["git"]["selected_path_git_metadata_kind"], "missing")
            self.assertTrue(inspection["git"]["input_inside_parent_repo"])
            self.assertTrue(any(item["reason"] == "git-init-required" for item in inspection["blocking"]))
            step_ids = [step["id"] for step in inspection["wizard"]["steps"]]
            self.assertIn("start_tracking_this_folder", step_ids)
            self.assertNotIn("initial-snapshot-required", step_ids)


    def test_git_project_panel_runner_refuses_mutating_commands_by_default(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=external, check=True, capture_output=True, text=True)
            result = self.service.run_git_project_panel_action(
                {
                    "action_key": "test:unsafe-clean",
                    "label": "Unsafe clean",
                    "repo_dir": str(external),
                    "commands": ["git clean -fd"],
                    "state": {"repo": str(external), "allow_mutating_actions": False},
                }
            )

            self.assertFalse(result["ok"], result)
            serialized = json.dumps(result)
            self.assertIn("Refusing mutating git command", serialized)
            self.assertIn("started_at", serialized)
            self.assertIn("finished_at", serialized)

    def test_git_project_panel_runner_unwraps_quoted_repo_paths(self) -> None:
        fixture_repo = str(FIXTURE_WINDOWS_ROOT / "main_computer_test")
        self.assertEqual(clean_path_token(f'"{fixture_repo}"'), fixture_repo)
        self.assertEqual(clean_path_token(r'\"' + fixture_repo + r'\"'), fixture_repo)
        self.assertEqual(clean_path_token('"/tmp/main computer"'), "/tmp/main computer")

    def test_git_project_panel_runner_accepts_quoted_git_c_path(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=external, check=True, capture_output=True, text=True)
            quoted = f'"{external}"'
            result = self.service.run_git_project_panel_action(
                {
                    "action_key": "test:quoted-repo",
                    "label": "Quoted repo",
                    "repo_dir": quoted,
                    "commands": [f'git -C "{external}" rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree'],
                    "state": {"repo": quoted, "allow_mutating_actions": False},
                }
            )

            self.assertTrue(result["ok"], result)
            runner_result = result["runner_result"]
            self.assertEqual(Path(runner_result["repo"]), external.resolve())
            self.assertEqual(runner_result["commands"][0]["returncode"], 0)

    def test_git_project_head_fix_runner_routes_supported_no_head_actions(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=external, check=True, capture_output=True, text=True)
            (external / "README.md").write_text("# demo\n", encoding="utf-8")

            staged = self.service.run_git_project_panel_action(
                {
                    "action_key": "wizard:start_tracking_real_work:1",
                    "label": "Start tracking real work",
                    "repo_dir": str(external),
                    "commands": [f'git -C "{external}" add -- README.md'],
                    "state": {"repo": str(external), "allow_mutating_actions": False},
                }
            )

            self.assertTrue(staged["ok"], staged)
            self.assertEqual(staged["mode"], "git-tool-fix-project-head")
            staged_result = staged["runner_result"]
            preflight = staged_result.get("before") or staged_result.get("preflight") or {}
            self.assertFalse(preflight["has_head"])
            if "backup" in staged_result:
                self.assertIn("backup_dir", staged_result["backup"])
                self.assertTrue(Path(staged_result["backup"]["backup_dir"]).exists())

    def test_git_project_head_fix_runner_initializes_selected_folder_without_git_metadata(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            child = parent / "mct"
            child.mkdir()
            subprocess.run(["git", "init"], cwd=parent, check=True, capture_output=True, text=True)

            initialized = self.service.run_git_project_panel_action(
                {
                    "action_key": "wizard:start_tracking_this_folder:1",
                    "label": "Start tracking this folder",
                    "repo_dir": str(child),
                    "commands": [f'git -C "{child}" init'],
                    "state": {"repo": str(child), "allow_mutating_actions": False},
                }
            )

            self.assertTrue(initialized["ok"], initialized)
            self.assertEqual(initialized["mode"], "git-tool-fix-project-head")
            runner_result = initialized["runner_result"]
            self.assertEqual(runner_result["mode"], "initialize_repository_here")
            self.assertEqual(runner_result["before"]["selected_path_git_metadata_kind"], "missing")
            self.assertIn(runner_result["after"]["selected_path_git_metadata_kind"], {"directory", "file"})
            self.assertFalse(runner_result["after"]["has_head"])
            self.assertTrue((child / ".git").exists())


    def test_git_project_frontend_labels_nested_commit_workbench_cards(self) -> None:
        script = (self.repo_root / "main_computer" / "web" / "applications" / "scripts" / "task-manager.js").read_text(encoding="utf-8")

        self.assertIn("GIT_PROJECT_COMMIT_CARD_STEP_IDS", script)
        self.assertIn("function gitProjectVisibleStepLabel", script)
        self.assertIn("Open commit pane", script)
        self.assertIn("Commit workbench attached here", script)
        self.assertIn("has-commit-workbench", script)
        self.assertIn("TAKE SNAPSHOT / COMMIT", script)

    def test_git_operation_cancel_marks_active_subprocess_style_work_canceled(self) -> None:
        service = self._service()
        finished: dict[str, object] = {}

        def callback() -> dict[str, object]:
            deadline = time.time() + 5
            while time.time() < deadline:
                if service._operation_cancel_requested():
                    return {"ok": False, "error": "cancelled from test"}
                time.sleep(0.02)
            return {"ok": True}

        thread = threading.Thread(
            target=lambda: finished.update(service.run_git_operation("test-panel", "Test panel", callback)),
            daemon=True,
        )
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not service.git_operation_status().get("active"):
            time.sleep(0.01)

        cancel_result = service.cancel_git_operation()
        thread.join(timeout=5)

        self.assertTrue(cancel_result["cancelled"], cancel_result)
        self.assertFalse(thread.is_alive())
        self.assertEqual(finished.get("operation", {}).get("status"), "cancelled")


    def _run_temp_git(self, repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def test_archive_files_status_splits_staged_unstaged_and_untracked_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._run_temp_git(repo, ["init"])
            self._run_temp_git(repo, ["config", "user.name", "Test User"])
            self._run_temp_git(repo, ["config", "user.email", "test@example.invalid"])
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            self._run_temp_git(repo, ["add", "tracked.txt"])
            self._run_temp_git(repo, ["commit", "-m", "initial"])
            (repo / "staged_new.txt").write_text("staged\n", encoding="utf-8")
            self._run_temp_git(repo, ["add", "staged_new.txt"])
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (repo / "untracked").mkdir()
            (repo / "untracked" / "note.txt").write_text("untracked\n", encoding="utf-8")

            service = self._service(repo)
            status = service.git_project_archive_files_status({"repo_dir": str(repo)})

            self.assertTrue(status["ok"])
            self.assertIn("staged_new.txt", {item["path"] for item in status["groups"]["staged"]})
            self.assertIn("tracked.txt", {item["path"] for item in status["groups"]["unstaged"]})
            self.assertTrue(any(item["path"].startswith("untracked") for item in status["groups"]["untracked"]))
            self.assertEqual(status["counts"]["staged"], 1)
            self.assertEqual(status["counts"]["unstaged"], 1)
            self.assertGreaterEqual(status["counts"]["untracked"], 1)

    def test_archive_files_preserves_selected_status_groups_on_archive_branch_and_removes_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._run_temp_git(repo, ["init"])
            self._run_temp_git(repo, ["config", "user.name", "Test User"])
            self._run_temp_git(repo, ["config", "user.email", "test@example.invalid"])
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            self._run_temp_git(repo, ["add", "tracked.txt"])
            self._run_temp_git(repo, ["commit", "-m", "initial"])
            (repo / "staged_new.txt").write_text("staged\n", encoding="utf-8")
            self._run_temp_git(repo, ["add", "staged_new.txt"])
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (repo / "untracked").mkdir()
            (repo / "untracked" / "note.txt").write_text("untracked\n", encoding="utf-8")

            service = self._service(repo)
            preview = service.archive_git_project_files(
                {
                    "repo_dir": str(repo),
                    "archive_branch": "archive/files-test",
                    "message": "archive: selected files",
                    "paths": ["staged_new.txt", "tracked.txt", "untracked"],
                    "dry_run": True,
                }
            )
            self.assertTrue(preview["ok"])
            self.assertTrue(preview["dry_run"])
            self.assertTrue((repo / "staged_new.txt").exists())

            result = service.archive_git_project_files(
                {
                    "repo_dir": str(repo),
                    "archive_branch": "archive/files-test",
                    "message": "archive: selected files",
                    "paths": ["staged_new.txt", "tracked.txt", "untracked"],
                    "dry_run": False,
                }
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["dry_run"])
            self.assertTrue(result["archive_commit"])
            self.assertFalse((repo / "staged_new.txt").exists())
            self.assertFalse((repo / "tracked.txt").exists())
            self.assertFalse((repo / "untracked").exists())

            staged_blob = self._run_temp_git(repo, ["show", "archive/files-test:staged_new.txt"]).stdout
            tracked_blob = self._run_temp_git(repo, ["show", "archive/files-test:tracked.txt"]).stdout
            untracked_blob = self._run_temp_git(repo, ["show", "archive/files-test:untracked/note.txt"]).stdout
            self.assertEqual(staged_blob, "staged\n")
            self.assertEqual(tracked_blob, "changed\n")
            self.assertEqual(untracked_blob, "untracked\n")



if __name__ == "__main__":
    unittest.main()
