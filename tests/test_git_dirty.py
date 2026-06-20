from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import git_dirty


ALLOWED_WINDOWS_EXAMPLE_USER = sorted(git_dirty.WINDOWS_USER_PATH_ALLOWED_EXAMPLE_USERS)[0]


def windows_user_path(user: str, *, slash: str = "\\") -> str:
    separator = "/" if slash == "/" else chr(92)
    return f"C:{separator}Users{separator}{user}{separator}main_computer"


class GitDirtyPlannerTests(unittest.TestCase):
    def test_read_gitignore_file_reads_content_lines_for_ui_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".gitignore").write_bytes(b"# cache\r\n__pycache__/\r\n\r\n*.pyc\r\n")

            payload = git_dirty.read_gitignore_file(root)

        self.assertTrue(payload["exists"])
        self.assertTrue(payload["content_read"])
        self.assertEqual(payload["size"], len(b"# cache\r\n__pycache__/\r\n\r\n*.pyc\r\n"))
        self.assertEqual(payload["newline"], "crlf")
        self.assertEqual(payload["line_count"], 4)
        self.assertEqual(payload["lines"][0]["number"], 1)
        self.assertEqual(payload["lines"][0]["text"], "# cache")
        self.assertTrue(payload["lines"][0]["comment"])
        self.assertEqual(payload["lines"][1]["text"], "__pycache__/")
        self.assertTrue(payload["lines"][2]["blank"])
        self.assertIn("*.pyc", payload["content"])

    def test_read_gitignore_file_distinguishes_missing_and_empty_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            missing = git_dirty.read_gitignore_file(root)
            (root / ".gitignore").write_text("", encoding="utf-8")
            empty = git_dirty.read_gitignore_file(root)

        self.assertFalse(missing["exists"])
        self.assertFalse(missing["content_read"])
        self.assertEqual(missing["newline"], "none")
        self.assertTrue(empty["exists"])
        self.assertTrue(empty["content_read"])
        self.assertEqual(empty["line_count"], 0)
        self.assertEqual(empty["lines"], [])
        self.assertEqual(empty["newline"], "none")

    def test_write_gitignore_file_saves_lines_and_reloads_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            payload = git_dirty.write_gitignore_file(root, [".venv/", "__pycache__/"], newline="lf")

            self.assertTrue(payload["exists"])
            self.assertTrue(payload["content_read"])
            self.assertEqual(payload["path"], ".gitignore")
            self.assertEqual(payload["newline"], "lf")
            self.assertEqual(payload["line_count"], 2)
            self.assertEqual([line["text"] for line in payload["lines"]], [".venv/", "__pycache__/"])
            self.assertEqual((root / ".gitignore").read_text(encoding="utf-8"), ".venv/\n__pycache__/\n")

    def test_write_gitignore_file_writes_empty_file_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".gitignore").write_text("old\n", encoding="utf-8")

            payload = git_dirty.write_gitignore_file(root, [], newline="lf")

            self.assertTrue((root / ".gitignore").exists())
            self.assertEqual((root / ".gitignore").read_text(encoding="utf-8"), "")
            self.assertTrue(payload["exists"])
            self.assertTrue(payload["content_read"])
            self.assertEqual(payload["line_count"], 0)

    def test_write_gitignore_file_rejects_unsafe_path_and_line_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            for unsafe_path in ("../.gitignore", "/tmp/.gitignore", "nested/.gitignore"):
                with self.subTest(unsafe_path=unsafe_path):
                    with self.assertRaises(ValueError):
                        git_dirty.write_gitignore_file(root, [], path=unsafe_path)

            with self.assertRaises(ValueError):
                git_dirty.write_gitignore_file(root, ["bad\nline"])

    def test_action_catalog_uses_readable_action_names(self) -> None:
        actions = {item["id"]: item for item in git_dirty.ACTION_CATALOG}
        removed_action = "_".join(("keep", "changes", "unstaged"))
        self.assertNotIn(removed_action, actions)
        for action_id in [
            "save_current_state",
            "preserve_local_only_files",
            "start_tracking_real_work",
            "ignore_generated_files",
            "remove_untracked_generated_files",
            "choose_correct_repository_root",
            "start_tracking_this_folder",
            "initialize_repository_here",
            "create_initial_snapshot",
            "prepare_commit_snapshot",
            "update_gitignore_before_initial_commit",
            "secrets_filter",
        ]:
            self.assertIn(action_id, actions)
            self.assertIn("label", actions[action_id])
            self.assertIn("git_name", actions[action_id])
        self.assertTrue(actions["remove_untracked_generated_files"]["destructive"])
        self.assertIn("save_current_state", actions["remove_untracked_generated_files"]["requires"])
        self.assertEqual(actions["secrets_filter"]["label"], "Secrets / Filter")
        self.assertEqual(actions["secrets_filter"]["kind"], "workflow")

    def test_plan_for_non_repo_starts_with_repo_reality_and_init(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = git_dirty.make_plan(Path(temp_dir))
        step_ids = [step["id"] for step in plan["steps"]]
        self.assertEqual(step_ids[:2], [
            "initialize_repository_here",
            "make_cleanup_plan",
        ])
        self.assertEqual(plan["recommended_strategy"], "initialize_repository_here_then_recheck")
        self.assertEqual(plan["next_action"]["id"], "initialize_repository_here")
        self.assertTrue(plan["next_action"]["blocks_progress"])
        self.assertFalse(plan["repo"]["git_detection"]["ok"])
        self.assertEqual(plan["repo"]["repo_state"], "not_initialized")
        self.assertFalse(plan["repo"]["has_head"])
        self.assertEqual(plan["repo"]["head_state"], "no-local-git-metadata")

    def test_dirty_repo_orders_snapshot_before_cleanup(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / "real_work.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "debug.log").write_text("generated\n", encoding="utf-8")
            plan = git_dirty.make_plan(root)

        ids = [step["id"] for step in plan["steps"]]
        self.assertIn("save_current_state", ids)
        self.assertIn("classify_changed_files", ids)
        self.assertIn("start_tracking_real_work", ids)
        self.assertIn("ignore_generated_files", ids)
        self.assertIn("remove_untracked_generated_files", ids)
        self.assertLess(ids.index("save_current_state"), ids.index("remove_untracked_generated_files"))
        cleanup = next(step for step in plan["steps"] if step["id"] == "remove_untracked_generated_files")
        self.assertTrue(cleanup["locked"])
        self.assertIn("save_current_state", cleanup["requires"])

    def test_plan_steps_include_filled_and_template_commands_by_default(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / "real_work.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "debug.log").write_text("generated\n", encoding="utf-8")
            plan = git_dirty.make_plan(root)

        for plan_step in plan["steps"]:
            self.assertIn("commands", plan_step)
            self.assertGreater(len(plan_step["commands"]), 0, plan_step["id"])
            for command in plan_step["commands"]:
                self.assertIn("template", command)
                self.assertIn("command", command)
                self.assertIn("purpose", command)

        track_step = next(step for step in plan["steps"] if step["id"] == "start_tracking_real_work")
        self.assertIn("git -C", track_step["commands"][0]["command"])
        self.assertIn("add -- real_work.py", track_step["commands"][0]["command"])
        self.assertEqual(track_step["commands"][0]["template"], "git -C <repo> add -- <paths>")

        cleanup_step = next(step for step in plan["steps"] if step["id"] == "remove_untracked_generated_files")
        cleanup_commands = cleanup_step["commands"]
        self.assertIn("clean -dn", cleanup_commands[0]["command"])
        self.assertIn("clean -f", cleanup_commands[1]["command"])
        self.assertTrue(cleanup_commands[1]["locked"])
        self.assertIn("save_current_state", cleanup_commands[1]["requires"])

    def test_plan_text_renders_commands_by_default(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / "debug.log").write_text("generated\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(Path(git_dirty.__file__).resolve()), "plan", "--repo", str(root)],
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertIn("Commands:", proc.stdout)
        self.assertIn("```shell", proc.stdout)
        self.assertIn("git -C", proc.stdout)
        self.assertIn("clean -dn", proc.stdout)
        self.assertIn("# LOCKED until save_current_state:", proc.stdout)
        self.assertIn("# git -C", proc.stdout)
        self.assertIn("clean -f", proc.stdout)
        self.assertIn("Command notes:", proc.stdout)

    def test_save_current_state_step_uses_snapshot_command(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / "debug.log").write_text("generated\n", encoding="utf-8")
            plan = git_dirty.make_plan(root)

        save_step = next(step for step in plan["steps"] if step["id"] == "save_current_state")
        self.assertEqual(len(save_step["commands"]), 1)
        command = save_step["commands"][0]
        self.assertIn("git_dirty.py", command["command"])
        self.assertIn("snapshot", command["command"])
        self.assertIn("--json", command["command"])
        self.assertEqual(command["template"], "python git_dirty.py snapshot --repo <repo> --json")

    def test_snapshot_creates_manifest_and_archives_untracked_files(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "real_work.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "debug.log").write_text("generated\n", encoding="utf-8")

            snapshot = git_dirty.create_snapshot(root, snapshot_id="unit-snapshot")
            self.assertTrue(snapshot["ok"], snapshot)
            snapshot_dir = Path(snapshot["snapshot_dir"])
            self.assertTrue((snapshot_dir / "manifest.json").exists())
            self.assertTrue((snapshot_dir / "untracked-files.tar.gz").exists())
            self.assertTrue((snapshot_dir / "untracked.zlist").exists())
            self.assertGreaterEqual(snapshot["manifest"]["untracked_archive"]["count"], 2)

            listing = git_dirty.list_snapshots(root)
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["latest"]["snapshot_id"], "unit-snapshot")

    def test_restore_snapshot_is_preview_by_default_and_can_restore_untracked_files(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            target = root / "local-only.txt"
            target.write_text("keep me\n", encoding="utf-8")

            snapshot = git_dirty.create_snapshot(root, snapshot_id="restore-me")
            self.assertTrue(snapshot["ok"], snapshot)
            target.unlink()

            preview = git_dirty.restore_snapshot(root, snapshot_id="restore-me")
            self.assertTrue(preview["ok"], preview)
            self.assertFalse(preview["applied"])
            self.assertFalse(target.exists())

            restored = git_dirty.restore_snapshot(root, snapshot_id="restore-me", apply=True)
            self.assertTrue(restored["ok"], restored)
            self.assertTrue(restored["applied"])
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me\n")

    def test_snapshot_cli_outputs_json(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "local-only.txt").write_text("keep me\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(Path(git_dirty.__file__).resolve()), "snapshot", "--repo", str(root), "--id", "cli-snapshot", "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["snapshot_id"], "cli-snapshot")
            self.assertTrue(Path(payload["snapshot_dir"]).exists())

            list_proc = subprocess.run(
                [sys.executable, str(Path(git_dirty.__file__).resolve()), "list-snapshots", "--repo", str(root), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            listing = json.loads(list_proc.stdout)
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["latest"]["snapshot_id"], "cli-snapshot")


    def test_input_inside_parent_repo_without_local_git_metadata_requires_init_here(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            child = root / "child"
            child.mkdir()
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            plan = git_dirty.make_plan(child)

        detection = plan["repo"]["git_detection"]
        self.assertFalse(detection["ok"])
        self.assertEqual(detection["error"], "selected-path-has-no-git-metadata")
        self.assertTrue(detection["input_inside_parent_repo"])
        self.assertFalse(detection["selected_path_has_git_metadata"])
        self.assertEqual(detection["selected_path_git_metadata_kind"], "missing")
        self.assertEqual(detection["recommended_first_step"], "start_tracking_this_folder")
        self.assertEqual(detection["repo_state"], "inside_parent_repo_only")
        self.assertEqual(plan["repo"]["repo_state"], "inside_parent_repo_only")
        self.assertFalse(plan["repo"]["has_head"])
        self.assertEqual([step["id"] for step in plan["steps"][:2]], [
            "initialize_repository_here",
            "make_cleanup_plan",
        ])
        self.assertEqual(plan["next_action"]["id"], "initialize_repository_here")


    def test_initialized_repository_without_commits_reports_unborn_head(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            plan = git_dirty.make_plan(root)

        detection = plan["repo"]["git_detection"]
        self.assertTrue(detection["ok"], detection)
        self.assertEqual(detection["repo_state"], "initialized_no_head")
        self.assertTrue(detection["selected_path_has_git_metadata"])
        self.assertEqual(detection["selected_path_git_metadata_kind"], "directory")
        self.assertTrue(detection["current_dir_is_git_repo_root"])
        self.assertFalse(detection["has_head"])
        self.assertEqual(detection["head_state"], "unborn")
        self.assertEqual(plan["status"]["branch"]["head_state"], "unborn")
        self.assertEqual(plan["recommended_strategy"], "create_initial_snapshot_then_recheck")
        self.assertEqual(plan["next_action"]["id"], "secrets_filter")
        self.assertTrue(plan["next_action"]["blocks_progress"])
        self.assertEqual(plan["steps"][0]["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(plan["steps"][0]["state"], "completed")
        self.assertTrue(plan["steps"][0]["completed"])
        self.assertFalse(plan["steps"][0]["requires_user"])
        self.assertFalse(plan["steps"][0]["blocks_progress"])
        self.assertEqual(plan["steps"][0]["gitignore_success"]["status"], "passed")
        self.assertEqual(plan["steps"][1]["id"], "secrets_filter")
        self.assertEqual(plan["steps"][1]["kind"], "workflow")
        self.assertTrue(plan["steps"][1]["requires_user"])
        self.assertEqual(plan["steps"][1]["requires"], ["update_gitignore_before_initial_commit"])
        self.assertEqual(plan["steps"][2]["id"], "create_initial_snapshot")
        self.assertEqual(plan["steps"][2]["kind"], "head_fix")
        self.assertEqual(plan["steps"][2]["requires"], ["update_gitignore_before_initial_commit", "secrets_filter"])
        self.assertIn("create_initial_snapshot", plan["status"]["dirty_things"][0]["possible_actions"])
        rendered_commands = "\n".join(command["command"] for command in plan["steps"][2]["commands"])
        self.assertIn("git_tool_fix_project_head.py", rendered_commands)
        self.assertIn("status --short --branch", rendered_commands)
        self.assertIn("ls-files --others --exclude-standard", rendered_commands)
        self.assertIn("config --get user.email", rendered_commands)
        self.assertIn("diff --cached --name-status", rendered_commands)
        self.assertIn("commit -m", rendered_commands)
        self.assertIn("gitignore_file", plan["steps"][2])
        self.assertFalse(plan["steps"][2]["gitignore_file"]["exists"])
        self.assertIn("secrets_filter", plan["steps"][1])

    def test_commit_review_uses_full_untracked_file_paths_for_tree(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "main_computer" / "web" / "applications").mkdir(parents=True)
            (root / "main_computer" / "web" / "applications" / "task-manager.js").write_text("console.log('x')\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_git_page_wizard_workflow.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

            status = git_dirty.collect_status(root)
            plan = git_dirty.make_plan(root)

        status_paths = {item["path"] for item in status["files"]}
        self.assertIn("main_computer/web/applications/task-manager.js", status_paths)
        self.assertIn("tests/test_git_page_wizard_workflow.py", status_paths)
        self.assertNotIn("main_computer/", status_paths)
        self.assertNotIn("tests/", status_paths)
        self.assertEqual({item["status"] for item in status["files"]}, {"untracked"})

        commit_review = next(step["commit_review"] for step in plan["steps"] if "commit_review" in step)
        review_paths = {
            item["path"]
            for group in commit_review["candidate_groups"].values()
            for item in group
        }
        self.assertIn("main_computer/web/applications/task-manager.js", review_paths)
        self.assertIn("tests/test_git_page_wizard_workflow.py", review_paths)

    def test_collect_status_labels_tracked_changes_for_commit_tree(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "--local", "user.name", "Test User"], cwd=root, check=True)
            subprocess.run(["git", "config", "--local", "user.email", "test@example.com"], cwd=root, check=True)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('one')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / "src" / "app.py").write_text("print('two')\n", encoding="utf-8")
            (root / "src" / "new.py").write_text("print('new')\n", encoding="utf-8")

            status = git_dirty.collect_status(root)

        by_path = {item["path"]: item for item in status["files"]}
        self.assertEqual(by_path["src/app.py"]["status"], "tracked_changed")
        self.assertTrue(by_path["src/app.py"]["unstaged"])
        self.assertEqual(by_path["src/new.py"]["status"], "untracked")
        self.assertTrue(by_path["src/new.py"]["untracked"])

    def test_dirty_planner_does_not_emit_removed_unstaged_passthrough_card(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "--local", "user.name", "Test User"], cwd=root, check=True)
            subprocess.run(["git", "config", "--local", "user.email", "test@example.invalid"], cwd=root, check=True)
            (root / "src").mkdir()
            app_path = root / "src" / "app.py"
            app_path.write_text("print('one')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            app_path.write_text("print('two')\n", encoding="utf-8")

            plan = git_dirty.make_plan(root)

        step_ids = [step["id"] for step in plan["steps"]]
        removed_action = "_".join(("keep", "changes", "unstaged"))
        self.assertNotIn(removed_action, step_ids)
        possible_actions = {
            action
            for item in plan["status"]["dirty_things"]
            for action in item.get("possible_actions", [])
        }
        self.assertNotIn(removed_action, possible_actions)


    def test_unborn_repo_with_generated_noise_prioritizes_gitignore_cleanup(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"pyc")
            for folder in ["harness_output_buddhabrot", "harness_output_ready", "harness_output_widget_refactor"]:
                (root / folder).mkdir()
                (root / folder / "index.html").write_text("<html></html>\n", encoding="utf-8")
            for folder in ["diagnostics_output_live_gemma4", "diagnostics_output_viewport", "diagnostics_output_widgets"]:
                (root / folder).mkdir()
                (root / folder / "result.json").write_text("{}\n", encoding="utf-8")
            (root / ".main_computer_heartbeat.pid").write_text("123\n", encoding="utf-8")

            plan = git_dirty.make_plan(root)

        self.assertEqual(plan["repo"]["repo_state"], "initialized_no_head")
        self.assertEqual(plan["recommended_strategy"], "prepare_gitignore_then_create_initial_snapshot")
        self.assertEqual(plan["next_action"]["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(plan["steps"][0]["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(plan["steps"][0]["kind"], "ignore")
        self.assertTrue(plan["steps"][0]["blocks_progress"])
        self.assertIn("__pycache__/", plan["steps"][0]["paths"])
        self.assertIn("harness_output_buddhabrot/", plan["steps"][0]["paths"])
        self.assertIn("diagnostics_output_live_gemma4/", plan["steps"][0]["paths"])
        self.assertIn(".main_computer_heartbeat.pid", plan["steps"][0]["paths"])
        self.assertIn("__pycache__/", plan["steps"][0]["ignore_rules"])
        self.assertIn("harness_output*/", plan["steps"][0]["ignore_rules"])
        self.assertIn("diagnostics_output*/", plan["steps"][0]["ignore_rules"])
        self.assertIn(".main_computer_heartbeat.pid", plan["steps"][0]["questionable_ignore_rules"])
        self.assertIn(".main_computer_heartbeat.pid", plan["steps"][0]["questionable_paths"])
        self.assertNotIn(".main_computer_heartbeat.pid", plan["steps"][0]["ignore_rules"])
        self.assertEqual(plan["steps"][0]["ignore_rule_groups"]["safe"], plan["steps"][0]["ignore_rules"])
        self.assertEqual(plan["steps"][0]["ignore_rule_groups"]["questionable"], plan["steps"][0]["questionable_ignore_rules"])
        self.assertNotIn("harness_output_buddhabrot/", plan["steps"][0]["ignore_rules"])
        self.assertNotIn("diagnostics_output_live_gemma4/", plan["steps"][0]["ignore_rules"])
        self.assertNotEqual(plan["steps"][0]["id"], "create_initial_snapshot")
        self.assertEqual(plan["steps"][1]["id"], "secrets_filter")
        self.assertEqual(plan["steps"][1]["label"], "Secrets / Filter")
        self.assertTrue(plan["steps"][1]["locked"])
        self.assertEqual(plan["steps"][1]["requires"], ["update_gitignore_before_initial_commit"])
        self.assertIn("app.py", plan["steps"][1]["source_config_test_candidates"])
        self.assertEqual(plan["steps"][1]["secrets_filter"]["title"], "SECRETS / FILTER")
        self.assertIn("detect_secrets", {rule["id"] for rule in plan["steps"][1]["secrets_filter"]["rules"]})
        self.assertEqual(plan["steps"][2]["id"], "prepare_commit_snapshot")
        self.assertEqual(plan["steps"][2]["label"], "Take Snapshot / Commit")
        self.assertTrue(plan["steps"][2]["locked"])
        self.assertEqual(plan["steps"][2]["requires"], ["update_gitignore_before_initial_commit", "secrets_filter"])
        self.assertIn("app.py", plan["steps"][2]["source_config_test_candidates"])
        self.assertEqual(plan["steps"][2]["commit_review"]["mode"], "first_commit")
        self.assertEqual(plan["steps"][2]["commit_review"]["sort"], "mtime_desc")
        rendered_commands = "\n".join(command["command"] for command in plan["steps"][0]["commands"])
        self.assertIn("ls-files --others --exclude-standard", rendered_commands)
        self.assertIn("check-ignore", rendered_commands)
        self.assertIn("Suggested safe .gitignore entries", rendered_commands)
        self.assertIn("diff -- .gitignore", rendered_commands)
        self.assertIn("status --short --ignored", rendered_commands)
        self.assertIn("plan --repo", rendered_commands)
        self.assertIn("gitignore_file", plan["steps"][0])
        self.assertFalse(plan["steps"][0]["gitignore_file"]["exists"])
        self.assertEqual(plan["steps"][0]["affected_paths"], plan["steps"][0]["paths"])
        self.assertIn("app.py", plan["steps"][0]["source_config_test_candidates"])
        self.assertIn("update_gitignore_before_initial_commit", plan["status"]["dirty_things"][0]["possible_actions"])


    def test_commit_review_opened_card_initializes_editable_workbench(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "--local", "user.name", "John R Raymond"], cwd=root, check=True)
            subprocess.run(["git", "config", "--local", "user.email", "johnrraymond@yahoo.com"], cwd=root, check=True)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

            plan = git_dirty.make_plan(root)

        self.assertEqual(plan["steps"][0]["id"], "update_gitignore_before_initial_commit")
        self.assertEqual(plan["steps"][0]["state"], "completed")
        self.assertEqual(plan["steps"][1]["id"], "secrets_filter")
        self.assertEqual(plan["steps"][2]["id"], "create_initial_snapshot")
        self.assertEqual(plan["steps"][2]["requires"], ["update_gitignore_before_initial_commit", "secrets_filter"])
        commit_review = plan["steps"][2]["commit_review"]
        card = commit_review["opened_commit_card"]
        self.assertEqual(card["title"], "TAKE SNAPSHOT / COMMIT")
        self.assertEqual(card["subtitle"], "Local commit only · No push · No remote setup")
        self.assertEqual(card["status_strip"]["head"], "unborn")
        self.assertEqual(card["status_strip"]["branch"], commit_review["head"]["branch"])
        self.assertEqual(card["status_strip"]["identity"], "local")
        self.assertEqual(card["status_strip"]["commit_ready"], "no")
        self.assertEqual(card["config_strip"]["fields"]["branch"]["value"], commit_review["head"]["branch"])
        self.assertEqual(card["config_strip"]["fields"]["commit_message"]["value"], "Take project snapshot")
        self.assertEqual(card["config_strip"]["fields"]["git_user_name"]["value"], "John R Raymond")
        self.assertEqual(card["config_strip"]["fields"]["git_user_email"]["value"], "johnrraymond@yahoo.com")
        self.assertEqual(card["config_strip"]["fields"]["identity_scope"]["value"], "use_existing")
        self.assertEqual(card["editable_state"]["selected_paths"], [])
        self.assertFalse(card["editable_state"]["stage_preview_confirmed"])
        review_paths = {
            item["path"]
            for group in commit_review["candidate_groups"].values()
            for item in group
        }
        self.assertIn("app.py", review_paths)
        self.assertNotIn("left_pane", card)
        self.assertEqual(card["center_pane"]["title"], "SELECTED WORK AREA")
        self.assertEqual(card["center_pane"]["selected_step"], "gate_summary")
        self.assertIn("repo_branch", card["center_pane"]["panels"])
        self.assertIn("gate_summary", card["center_pane"]["panels"])
        self.assertIn("stage_preview", card["center_pane"]["panels"])
        self.assertNotIn("privacy_scan", card["center_pane"]["panels"])
        self.assertEqual(card["center_pane"]["panels"]["gate_summary"]["gates"]["secrets_filter"]["label"], "Secrets / Filter")
        self.assertEqual(card["right_pane"]["sort"], "newest_edit_first")
        self.assertEqual(card["right_pane"]["groups_source"], "commit_review.candidate_groups")
        self.assertEqual(card["right_pane"]["counts"]["selected_by_default"], 0)
        self.assertEqual(card["right_pane"]["counts"]["review_before_selecting"], 1)
        self.assertEqual(card["fast_sanity"]["cost"], "client_only")
        self.assertIn("git_status_changed", {item["id"] for item in card["fast_sanity"]["rebuild_triggers"]})

    def test_generic_generated_directory_families_collapse_to_gitignore_patterns(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            for folder in ["qa_output_linux", "qa_output_windows", "qa_output_macos"]:
                (root / folder).mkdir()
                (root / folder / "result.json").write_text("{}\n", encoding="utf-8")
            for folder in ["browser_cache_chrome", "browser_cache_firefox", "browser_cache_webkit"]:
                (root / folder).mkdir()
                (root / folder / "state.bin").write_bytes(b"cache")

            plan = git_dirty.make_plan(root)

        step = plan["steps"][0]
        self.assertEqual(step["id"], "update_gitignore_before_initial_commit")
        self.assertIn("qa_output*/", step["ignore_rules"])
        self.assertIn("browser_cache*/", step["ignore_rules"])
        self.assertNotIn("qa_output_linux/", step["ignore_rules"])
        self.assertNotIn("browser_cache_chrome/", step["ignore_rules"])


    def test_questionable_ignore_candidates_are_separated_from_safe_rules(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            for folder in ["ollama_prompt_space_20260501", "ollama_prompt_space_20260502"]:
                (root / folder).mkdir()
                (root / folder / "output.json").write_text("{}\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"pyc")

            plan = git_dirty.make_plan(root)

        step = plan["steps"][0]
        self.assertEqual(step["id"], "update_gitignore_before_initial_commit")
        self.assertIn("__pycache__/", step["ignore_rules"])
        self.assertNotIn(".env", step["ignore_rules"])
        self.assertIn(".env", step["questionable_paths"])
        self.assertIn(".env", step["questionable_ignore_rules"])
        self.assertIn("ollama_prompt_space_20260501/", step["questionable_paths"])
        self.assertIn("ollama_prompt_space_20260502/", step["questionable_paths"])
        self.assertIn("ollama_prompt_space*/", step["questionable_ignore_rules"])
        self.assertNotIn("ollama_prompt_space*/", step["ignore_rules"])
        self.assertNotIn("ollama_prompt_space_20260501/", step["ignore_rules"])
        self.assertNotIn("ollama_prompt_space_20260502/", step["ignore_rules"])
        rendered_commands = "\n".join(command["command"] for command in step["commands"])
        self.assertIn("Suggested safe .gitignore entries", rendered_commands)
        self.assertIn("Questionable/review-only .gitignore candidates", rendered_commands)


    def test_initialized_repository_with_commit_reports_present_head(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            plan = git_dirty.make_plan(root)

        detection = plan["repo"]["git_detection"]
        self.assertTrue(detection["ok"], detection)
        self.assertEqual(detection["repo_state"], "initialized_has_head")
        self.assertTrue(detection["has_head"])
        self.assertEqual(detection["head_state"], "present")
        self.assertRegex(detection["head_oid"], r"^[0-9a-f]{40}$")
        self.assertTrue(plan["repo"]["has_head"])
        self.assertEqual(plan["repo"]["head_state"], "present")

    def test_broken_selected_git_metadata_is_not_treated_as_repo_or_init_target(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")
            plan = git_dirty.make_plan(root)

        detection = plan["repo"]["git_detection"]
        self.assertFalse(detection["ok"])
        self.assertEqual(detection["error"], "selected-path-git-metadata-invalid")
        self.assertEqual(detection["repo_state"], "broken_git_metadata")
        self.assertFalse(detection["safe_to_init"])
        self.assertEqual(detection["recommended_first_step"], "stop_until_repository_is_clear")
        self.assertEqual([step["id"] for step in plan["steps"][:2]], [
            "find_repository_root",
            "stop_until_repository_is_clear",
        ])

    def test_head_fix_runner_can_create_initial_snapshot_from_planner_commands(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        script = Path(__file__).resolve().parents[1] / "tools" / "git" / "git_tool_fix_project_head.py"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

            plan = git_dirty.make_plan(root)
            step = next(item for item in plan["steps"] if item["id"] == "create_initial_snapshot")
            self.assertEqual(step["id"], "create_initial_snapshot")
            commands = [
                command["command"]
                for command in step["commands"]
                if command.get("implemented", True)
                and not command.get("locked")
                and not command["command"].startswith("#")
                and "\n" not in command["command"]
            ]
            payload_path = root / "head_fix_payload.json"
            payload_path.write_text(json.dumps({
                "action_key": f"wizard:create_initial_snapshot:{step['order']}",
                "repo_dir": str(root),
                "commands": commands,
                "state": {"repo": str(root)},
                "app_root": str(Path(__file__).resolve().parents[1]),
            }), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(script), str(payload_path)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=True,
            )

            result = json.loads(proc.stdout)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["postflight"]["has_head"])
            self.assertEqual(result["postflight"]["head_state"], "present")


    def test_status_json_command_outputs_valid_json(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(Path(git_dirty.__file__).resolve()), "actions", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("actions", payload)
        self.assertGreater(len(payload["actions"]), 20)


    def test_security_rule_catalog_merges_saved_policy_and_detect_secrets_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git_dirty_rules.json").write_text(json.dumps({
                "policy_version": 1,
                "rules": {
                    "windows_user_paths": False,
                    "detect_secrets": True,
                },
            }), encoding="utf-8")

            output = git_dirty.security_rule_catalog_output(root)
            enabled_ids = git_dirty.enabled_security_rule_ids(root)

        rules = {rule["id"]: rule for rule in output["rules"]}
        self.assertIn("detect_secrets", rules)
        self.assertEqual(rules["windows_user_paths"]["enabled"], False)
        self.assertEqual(rules["windows_user_paths"]["source"], "saved")
        self.assertEqual(rules["unix_user_paths"]["enabled"], True)
        self.assertEqual(rules["unix_user_paths"]["source"], "default")
        self.assertEqual(rules["detect_secrets"]["engine"], "detect-secrets")
        self.assertEqual(rules["detect_secrets"]["source"], "saved")
        self.assertIn(rules["detect_secrets"]["availability_status"], {"available", "unavailable"})
        self.assertEqual(enabled_ids & {"windows_user_paths"}, set())
        self.assertIn("detect_secrets", enabled_ids)

    def test_secrets_filter_payload_is_pending_rule_review_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = [{"path": "app.py", "untracked": True}]
            payload = git_dirty.secrets_filter_payload(root, files)

        self.assertEqual(payload["kind"], "secrets_filter")
        self.assertEqual(payload["title"], "SECRETS / FILTER")
        self.assertEqual(payload["policy_path"], ".git_dirty_rules.json")
        self.assertEqual(payload["scan"]["status"], "pending_external_scan")
        self.assertEqual(payload["scan"]["gate_status"], "pending")
        self.assertEqual(payload["scan_result"]["mode"], "pending")
        self.assertTrue(payload["summary"]["requires_user_scan"])
        self.assertIn("saved_rules", payload)
        self.assertIn("saved_summary", payload)
        self.assertFalse(payload["saved_policy_exists"])
        self.assertEqual(payload["saved_rules"], [])
        self.assertEqual(payload["saved_summary"]["enabled_rule_count"], 0)
        action_ids = [action["id"] for action in payload["actions"]]
        self.assertEqual(action_ids, ["merge_rule_choices", "update_saved_rule_choices", "run_selected_rules", "run_saved_filter_check"])
        self.assertTrue(all(action["implemented"] for action in payload["actions"]))
        rule_ids = {rule["id"] for rule in payload["rules"]}
        self.assertEqual(rule_ids, {"windows_user_paths", "unix_user_paths", "user_names", "secrets", "detect_secrets"})
        self.assertEqual({rule["source"] for rule in payload["rules"]}, {"default"})
        self.assertEqual({rule["scan_state"] for rule in payload["rules"]}, {"pending"} | ({"unavailable"} if any(not rule.get("available", True) for rule in payload["rules"]) else set()))


    def test_privacy_evidence_truncates_long_values_only(self) -> None:
        short = "x" * 202
        long_value = "0x" + ("7" * 260) + "4513"

        self.assertEqual(git_dirty.format_privacy_evidence(short), short)
        formatted = git_dirty.format_privacy_evidence(long_value)

        self.assertEqual(len(formatted), 203)
        self.assertTrue(formatted.startswith(long_value[:100]))
        self.assertTrue(formatted.endswith(long_value[-100:]))
        self.assertIn("...", formatted)
        self.assertNotEqual(formatted, long_value)

    def test_windows_allowed_example_path_is_allowed_but_username_path_is_flagged(self) -> None:
        self.assertFalse(git_dirty.should_flag_windows_user_path(windows_user_path(ALLOWED_WINDOWS_EXAMPLE_USER)))
        self.assertFalse(git_dirty.should_flag_windows_user_path(windows_user_path(ALLOWED_WINDOWS_EXAMPLE_USER, slash="/")))
        self.assertTrue(git_dirty.should_flag_windows_user_path(windows_user_path("USERNAME")))
        self.assertTrue(git_dirty.should_flag_windows_user_path(windows_user_path("USERNAME", slash="/")))
        self.assertTrue(git_dirty.should_flag_windows_user_path(r"%USERPROFILE%\main_computer"))




    def test_existing_repo_ignore_steps_include_gitignore_card_payload(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "tester@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "debug.log").write_text("generated\n", encoding="utf-8")

            plan = git_dirty.make_plan(root)

        local_step = next(step for step in plan["steps"] if step["id"] == "ignore_local_environment_files")
        generated_step = next(step for step in plan["steps"] if step["id"] == "ignore_generated_files")

        self.assertIn("gitignore_file", local_step)
        self.assertIn("questionable_ignore_rules", local_step)
        self.assertIn(".env", local_step["questionable_paths"])
        self.assertIn(".env", local_step["affected_paths"])
        self.assertEqual(local_step["ignore_rule_groups"]["questionable"], local_step["questionable_ignore_rules"])

        self.assertIn("gitignore_file", generated_step)
        self.assertIn("ignore_rules", generated_step)
        self.assertIn("debug.log", generated_step["safe_paths"])
        self.assertIn("debug.log", generated_step["affected_paths"])
        self.assertIn("debug.log", generated_step["ignore_rules"])
        self.assertEqual(generated_step["ignore_rule_groups"]["safe"], generated_step["ignore_rules"])


if __name__ == "__main__":
    unittest.main()
