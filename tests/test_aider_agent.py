from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path

from main_computer.aider_agent import (
    AiderAgentConfig,
    AiderActionRequest,
    AiderValidationError,
    append_aider_log,
    parse_file_list,
    prepare_aider_action,
    run_aider_action,
)



def write_mock_aider(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "import argparse",
                "import sys",
                "parser = argparse.ArgumentParser(add_help=False)",
                "parser.add_argument('--message', default='')",
                "parser.add_argument('files', nargs='*')",
                "args, _ = parser.parse_known_args()",
                "print(args.message)",
                "for item in sys.argv[1:]:",
                "    print(item)",
                "raise SystemExit(0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class AiderAgentTests(unittest.TestCase):
    def test_prepare_builds_guarded_dry_run_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Add a health endpoint.",
                    files=["app.py"],
                    dry_run=True,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin="aider",
                    default_model="ollama_chat/llama3.1:8b",
                    timeout_seconds=45,
                ),
            )

            self.assertTrue(result.ok)
            self.assertEqual(Path(result.repo_dir), repo.resolve())
            self.assertEqual(result.timeout_seconds, 45)
            self.assertIn("--yes-always", result.command)
            self.assertNotIn("--no-stream", result.command)
            self.assertIn("--no-pretty", result.command)
            self.assertIn("--no-show-model-warnings", result.command)
            self.assertIn("--subtree-only", result.command)
            self.assertIn("--no-restore-chat-history", result.command)
            self.assertIn("--dry-run", result.command)
            self.assertIn("ollama_chat/llama3.1:8b", result.command)
            self.assertEqual(result.command[-1], "app.py")

    def test_prepare_fallback_command_prefers_fast_visible_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Use fallback diagnostics.",
                    files=["app.py"],
                    dry_run=True,
                    fallback=True,
                ),
                AiderAgentConfig(workspace=workspace, aider_bin="aider"),
            )

            self.assertIn("--stream", result.command)
            self.assertIn("--verbose", result.command)
            self.assertIn("--no-pretty", result.command)
            self.assertNotIn("--no-show-model-warnings", result.command)

    def test_prepare_uses_explicit_archive_history_files_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            chat_history = repo / "aider_web_context" / "histories" / "thread" / ".aider.chat.history.md"
            input_history = repo / "aider_web_context" / "histories" / "thread" / ".aider.input.history"

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Continue this archive.",
                    files=["app.py"],
                    dry_run=True,
                    chat_history_file=str(chat_history),
                    input_history_file=str(input_history),
                ),
                AiderAgentConfig(workspace=workspace, aider_bin="aider"),
            )

            self.assertIn("--restore-chat-history", result.command)
            self.assertNotIn("--no-restore-chat-history", result.command)
            self.assertEqual(result.command[result.command.index("--chat-history-file") + 1], str(chat_history))
            self.assertEqual(result.command[result.command.index("--input-history-file") + 1], str(input_history))


    def test_prepare_keeps_cli_file_args_relative_to_selected_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / ".git").mkdir()
            repo = workspace / "main_computer_test"
            repo.mkdir()
            (repo / "main_computer").mkdir()
            (repo / "main_computer" / "viewport.py").write_text("# viewport\n", encoding="utf-8")

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="main_computer_test",
                    instruction="Inspect viewport.",
                    files=["main_computer/viewport.py"],
                    dry_run=True,
                ),
                AiderAgentConfig(workspace=workspace, aider_bin="aider"),
            )

            self.assertEqual(result.git_root, str(workspace.resolve()))
            self.assertEqual(result.command[-1], "main_computer/viewport.py")


    def test_prepare_accepts_git_root_above_workspace_without_rebasing_selected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            git_root = Path(temp) / "git-root"
            git_root.mkdir()
            (git_root / ".git").mkdir()
            workspace = git_root / "dsl"
            workspace.mkdir()
            repo = workspace / "main_computer_test"
            repo.mkdir()
            (repo / "main_computer").mkdir()
            (repo / "main_computer" / "viewport.py").write_text("# viewport\n", encoding="utf-8")

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="main_computer_test",
                    instruction="Inspect viewport.",
                    files=["main_computer/viewport.py"],
                    dry_run=True,
                ),
                AiderAgentConfig(workspace=workspace, aider_bin="aider"),
            )

            self.assertEqual(Path(result.repo_dir), repo.resolve())
            self.assertEqual(result.git_root, str(git_root.resolve()))
            self.assertEqual(result.command[-1], "main_computer/viewport.py")


    def test_prepare_uses_request_timeout_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

            result = prepare_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Inspect app.py.",
                    files=["app.py"],
                    dry_run=True,
                    timeout_seconds=17,
                ),
                AiderAgentConfig(workspace=workspace, aider_bin="aider", timeout_seconds=45),
            )

            self.assertEqual(result.timeout_seconds, 17)

    def test_rejects_repo_and_file_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            config = AiderAgentConfig(workspace=workspace)

            with self.assertRaises(AiderValidationError):
                prepare_aider_action(
                    AiderActionRequest(repo_dir=str(workspace.parent), instruction="Nope."),
                    config,
                )

            with self.assertRaises(AiderValidationError):
                prepare_aider_action(
                    AiderActionRequest(repo_dir="repo", instruction="Nope.", files=["../secret.txt"]),
                    config,
                )

    def test_run_invokes_mock_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
            mock = write_mock_aider(workspace / "mock_aider.py")

            result = run_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Mock edit request.",
                    files=["app.py"],
                    dry_run=True,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin=f'"{sys.executable}" "{mock.resolve()}"',
                    default_model="mock-model",
                    timeout_seconds=30,
                ),
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Mock edit request.", result.stdout)
            self.assertIn("app.py", result.stdout)


    def test_run_forces_utf8_child_output_and_decodes_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            script = repo / "emit_utf8.py"
            script.write_text(
                "\n".join(
                    [
                        "import os",
                        "import sys",
                        "sys.stdout.write(f\"PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING')}\\n\")",
                        "sys.stdout.write(f\"PYTHONUTF8={os.environ.get('PYTHONUTF8')}\\n\")",
                        "sys.stdout.write(f\"PYTHONUNBUFFERED={os.environ.get('PYTHONUNBUFFERED')}\\n\")",
                        "sys.stdout.buffer.write(\"► utf8 output\\n\".encode(\"utf-8\"))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Emit utf-8 output.",
                    dry_run=False,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin=f'"{sys.executable}" "{script.resolve()}"',
                    timeout_seconds=30,
                ),
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.returncode, 0)
            self.assertIn("PYTHONIOENCODING=utf-8", result.stdout)
            self.assertIn("PYTHONUTF8=1", result.stdout)
            self.assertIn("PYTHONUNBUFFERED=1", result.stdout)
            self.assertIn("► utf8 output", result.stdout)


    def test_run_streams_output_to_callback_and_response_file_before_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            script = repo / "slow_output.py"
            script.write_text(
                "\n".join(
                    [
                        "import sys",
                        "import time",
                        "sys.stdout.write('stdout before timeout\\n')",
                        "sys.stdout.flush()",
                        "sys.stderr.write('stderr before timeout\\n')",
                        "sys.stderr.flush()",
                        "time.sleep(2)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            response_file = repo / "aider-response.txt"
            chunks: list[tuple[str, str]] = []

            result = run_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Stream output.",
                    dry_run=False,
                    timeout_seconds=1,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin=f'"{sys.executable}" -S "{script.resolve()}"',
                    timeout_seconds=30,
                ),
                output_callback=lambda stream_name, text: chunks.append((stream_name, text)),
                response_file=response_file,
                stream_to_console=False,
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.timed_out)
            self.assertIn("stdout before timeout", result.stdout)
            self.assertIn("stderr before timeout", result.stderr)
            saved_output = response_file.read_text(encoding="utf-8")
            self.assertIn("stdout before timeout", saved_output)
            self.assertIn("stderr before timeout", saved_output)
            self.assertIn("Aider action timed out after 1 seconds.", saved_output)
            self.assertTrue(any(stream == "stdout" and "stdout before timeout" in text for stream, text in chunks))
            self.assertTrue(any(stream == "stderr" and "stderr before timeout" in text for stream, text in chunks))


    def test_run_fallback_records_first_byte_before_newline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            script = repo / "partial_output.py"
            script.write_text(
                "\n".join(
                    [
                        "import sys",
                        "import time",
                        "sys.stdout.write('first-byte-without-newline')",
                        "sys.stdout.flush()",
                        "time.sleep(2)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            response_file = repo / "fallback-response.txt"

            result = run_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Stream fallback bytes.",
                    dry_run=False,
                    timeout_seconds=1,
                    fallback=True,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin=f'"{sys.executable}" -S "{script.resolve()}"',
                    timeout_seconds=30,
                ),
                response_file=response_file,
                stream_to_console=False,
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.first_output_stream, "stdout")
            self.assertIsNotNone(result.first_output_ms)
            self.assertIn("first-byte-without-newline", result.stdout)
            saved_output = response_file.read_text(encoding="utf-8")
            self.assertIn("[fallback] Aider fallback mode enabled", saved_output)
            self.assertIn("[fallback] first stdout output after", saved_output)
            self.assertIn("first-byte-without-newline", saved_output)

    def test_run_uses_request_timeout_override_when_subprocess_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            result = run_aider_action(
                AiderActionRequest(
                    repo_dir="repo",
                    instruction="Wait for timeout.",
                    dry_run=True,
                    timeout_seconds=1,
                ),
                AiderAgentConfig(
                    workspace=workspace,
                    aider_bin=f'"{sys.executable}" -c "import time; time.sleep(2)"',
                    timeout_seconds=30,
                ),
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.timeout_seconds, 1)
            self.assertIn("1 seconds", result.error or "")

    def test_parse_file_list_accepts_text_or_list(self) -> None:
        self.assertEqual(parse_file_list("a.py, b.py\nc.py"), ["a.py", "b.py", "c.py"])
        self.assertEqual(parse_file_list(["a.py", " ", "b.py"]), ["a.py", "b.py"])

    def test_append_aider_log_writes_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "aider.log"
            append_aider_log(log_path, "prepare", repo_dir="repo", files=["TODO.md"], ok=True)
            append_aider_log(log_path, "run", returncode=0, stdout_excerpt="done")

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            self.assertEqual(first["event"], "prepare")
            self.assertEqual(first["files"], ["TODO.md"])
            self.assertEqual(second["event"], "run")


if __name__ == "__main__":
    unittest.main()
