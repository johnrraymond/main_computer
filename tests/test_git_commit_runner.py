from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from main_computer.git_commit import GitCommitError, GitCommitRunner


def git_available() -> bool:
    return shutil.which("git") is not None


class GitCommitRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        if not git_available():
            self.skipTest("git executable is not available")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self._git(["init"])
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("bravo\n", encoding="utf-8")
        self._git(["add", "a.txt", "b.txt"])
        self._git(["-c", "user.name=Tester", "-c", "user.email=tester@example.com", "commit", "-m", "initial"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def _runner(self, events: list[dict[str, object]] | None = None) -> GitCommitRunner:
        if events is None:
            events = []
        return GitCommitRunner(
            self.repo,
            emit=lambda event: events.append(event),
            cancel_event=threading.Event(),
            set_process=lambda process, command: None,
        )

    def test_dry_run_does_not_mutate_index_or_create_commit(self) -> None:
        before_head = self._git(["rev-parse", "HEAD"]).stdout.strip()
        (self.repo / "a.txt").write_text("alpha\nchanged\n", encoding="utf-8")
        events: list[dict[str, object]] = []

        result = self._runner(events).run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt"],
            "message": "dry run",
            "dry_run": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self._git(["rev-parse", "HEAD"]).stdout.strip(), before_head)
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertIn("a.txt", self._git(["diff", "--name-only"]).stdout)
        self.assertTrue(any(event.get("type") == "complete" for event in events))

    def test_real_commit_stages_exact_selected_set_only(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nchanged\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("bravo\nleft unstaged\n", encoding="utf-8")

        result = self._runner().run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt"],
            "message": "commit selected a",
            "dry_run": False,
            "confirm_real_commit": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual([commit["paths"] for commit in result["commits"]], [["a.txt"]])
        committed_files = self._git(["show", "--name-only", "--pretty=format:", "HEAD"]).stdout.splitlines()
        self.assertIn("a.txt", committed_files)
        self.assertNotIn("b.txt", committed_files)
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertEqual(self._git(["diff", "--name-only"]).stdout.strip(), "b.txt")

    def test_real_commit_treats_cached_diff_check_as_advisory(self) -> None:
        (self.repo / "a.txt").write_text("alpha with trailing whitespace \n", encoding="utf-8")
        events: list[dict[str, object]] = []

        result = self._runner(events).run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt"],
            "message": "commit whitespace warning",
            "dry_run": False,
            "confirm_real_commit": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["commits"][0]["paths"], ["a.txt"])
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertIn("commit whitespace warning", self._git(["log", "--oneline", "-1"]).stdout)
        self.assertTrue(any(
            event.get("level") == "warning"
            and "diff --cached --check" in str(event.get("message", ""))
            for event in events
        ))

    def test_one_at_a_time_creates_one_commit_per_selected_file(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nchanged\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("bravo\nchanged\n", encoding="utf-8")

        result = self._runner().run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt", "b.txt"],
            "message": "commit one file",
            "dry_run": False,
            "confirm_real_commit": True,
            "one_at_a_time": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["one_at_a_time"])
        self.assertEqual(len(result["commits"]), 2)
        self.assertEqual([commit["paths"] for commit in result["commits"]], [["a.txt"], ["b.txt"]])
        log_count = int(self._git(["rev-list", "--count", "HEAD"]).stdout.strip())
        self.assertEqual(log_count, 3)
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertEqual(self._git(["diff", "--name-only"]).stdout.strip(), "")

    def test_unsafe_selected_path_is_rejected(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nchanged\n", encoding="utf-8")

        with self.assertRaises(GitCommitError):
            self._runner().run({
                "repo_dir": str(self.repo),
                "paths": ["../outside.txt"],
                "message": "unsafe",
                "dry_run": True,
                "git_user_name": "Tester",
                "git_user_email": "tester@example.com",
            })

    def test_pre_existing_staged_files_block_real_commit(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nchanged\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("bravo\nchanged\n", encoding="utf-8")
        self._git(["add", "b.txt"])

        with self.assertRaisesRegex(GitCommitError, "already has staged files"):
            self._runner().run({
                "repo_dir": str(self.repo),
                "paths": ["a.txt"],
                "message": "blocked by staged",
                "dry_run": False,
                "confirm_real_commit": True,
                "git_user_name": "Tester",
                "git_user_email": "tester@example.com",
            })

    def test_real_commit_retries_when_index_already_matches_selected_set(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nretry staged\n", encoding="utf-8")
        self._git(["add", "a.txt"])
        events: list[dict[str, object]] = []

        result = self._runner(events).run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt"],
            "message": "retry selected staged set",
            "dry_run": False,
            "confirm_real_commit": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["commits"][0]["paths"], ["a.txt"])
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertIn("retry selected staged set", self._git(["log", "--oneline", "-1"]).stdout)
        self.assertTrue(any(
            "already contains exactly the selected files" in str(event.get("message", ""))
            for event in events
        ))

    def test_real_commit_recovers_when_index_contains_selected_subset(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nretry staged subset\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("bravo\nretry unstaged selected\n", encoding="utf-8")
        self._git(["add", "a.txt"])
        events: list[dict[str, object]] = []

        result = self._runner(events).run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt", "b.txt"],
            "message": "retry selected staged subset",
            "dry_run": False,
            "confirm_real_commit": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["commits"][0]["paths"], ["a.txt", "b.txt"])
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertIn("retry selected staged subset", self._git(["log", "--oneline", "-1"]).stdout)
        self.assertTrue(any(
            "already contains a subset of the selected files" in str(event.get("message", ""))
            for event in events
        ))

    def test_real_commit_recovers_when_selected_subset_is_changed(self) -> None:
        (self.repo / "a.txt").write_text("alpha\nretry staged subset only\n", encoding="utf-8")
        self._git(["add", "a.txt"])
        events: list[dict[str, object]] = []

        result = self._runner(events).run({
            "repo_dir": str(self.repo),
            "paths": ["a.txt", "b.txt"],
            "message": "retry selected changed subset",
            "dry_run": False,
            "confirm_real_commit": True,
            "git_user_name": "Tester",
            "git_user_email": "tester@example.com",
        })

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["commits"][0]["paths"], ["a.txt"])
        self.assertEqual(result["staged_files"], ["a.txt"])
        self.assertEqual(self._git(["diff", "--cached", "--name-only"]).stdout.strip(), "")
        self.assertIn("retry selected changed subset", self._git(["log", "--oneline", "-1"]).stdout)
        self.assertTrue(any(
            "unchanged selected files were skipped" in str(event.get("message", ""))
            for event in events
        ))

    def test_git_process_output_is_drained_without_poll_wait_deadlock(self) -> None:
        events: list[dict[str, object]] = []
        created_processes: list[object] = []

        class LargeOutputProcess:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.returncode: int | None = None
                self.communicate_calls = 0
                self.communicate_timeouts: list[float | None] = []
                created_processes.append(self)

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                self.communicate_calls += 1
                self.communicate_timeouts.append(timeout)
                self.returncode = 0
                return ("large-output\0" * 20000, "")

            def poll(self) -> int | None:
                raise AssertionError("Git output must be drained with communicate() instead of poll-waiting on a pipe.")

            def send_signal(self, signum: int) -> None:
                self.returncode = -1

            def terminate(self) -> None:
                self.returncode = -1

            def kill(self) -> None:
                self.returncode = -9

        with mock.patch("main_computer.git_commit.subprocess.Popen", LargeOutputProcess):
            result = self._runner(events)._git(self.repo, ["diff", "--cached", "--name-only", "-z"], check=True, phase="inspect_index")

        self.assertEqual(result["returncode"], 0)
        self.assertGreater(len(result["stdout"]), 100000)
        self.assertEqual(len(created_processes), 1)
        process = created_processes[0]
        self.assertEqual(getattr(process, "communicate_calls"), 1)
        self.assertIn(0.2, getattr(process, "communicate_timeouts"))
        self.assertTrue(any(event.get("type") == "command_finish" for event in events))

    def test_git_state_skips_expensive_untracked_scan(self) -> None:
        (self.repo / "slow-untracked.tmp").write_text("do not scan me\n", encoding="utf-8")
        events: list[dict[str, object]] = []
        state = self._runner(events)._git_state(self.repo)

        self.assertEqual(state["untracked"], [])
        self.assertTrue(state["untracked_skipped"])
        commands = [
            " ".join(str(part) for part in event.get("command", []))
            for event in events
            if event.get("type") == "command_start"
        ]
        self.assertFalse(any("ls-files" in command and "--others" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
