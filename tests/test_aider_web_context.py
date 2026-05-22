from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from main_computer.aider_web_context import AiderWebContextStore


class AiderWebContextStoreTests(unittest.TestCase):
    def test_status_bootstraps_empty_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AiderWebContextStore(Path(temp))
            status = store.status()
            self.assertIn("active", status)
            self.assertTrue(status["active"]["id"])
            self.assertTrue(status["active"]["archive_id"])
            self.assertEqual(status["active"]["entry_count"], 0)
            self.assertEqual(status["current_archive"]["id"], status["active"]["archive_id"])
            self.assertEqual(len(status["archives"]), 1)
            self.assertEqual(status["archives"][0]["id"], status["active"]["archive_id"])

    def test_prepare_aider_history_files_isolates_loaded_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AiderWebContextStore(root)
            store.append_entry(
                kind="run",
                repo_dir="main_computer_test",
                files=["TODO.md"],
                instruction="First archive prompt.",
                dry_run=True,
                ok=True,
                result_excerpt="First archive response.",
                route="/api/applications/aider/run",
            )
            archived = store.archive_active(label="first thread")
            archive_id = archived["archived"]["id"]

            store.load_archive(archive_id)
            history = store.prepare_aider_history_files()

            self.assertNotEqual(history["archive_id"], archive_id)
            chat_path = Path(history["chat_history_file"])
            input_path = Path(history["input_history_file"])
            self.assertTrue(chat_path.is_file())
            self.assertTrue(input_path.is_file())
            self.assertEqual(chat_path.parent, root / "histories" / history["archive_id"])
            self.assertIn("#### First archive prompt.", chat_path.read_text(encoding="utf-8"))
            self.assertIn("+First archive prompt.", input_path.read_text(encoding="utf-8"))


    def test_load_archive_only_forks_after_new_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AiderWebContextStore(root)
            store.append_entry(
                kind="prepare",
                repo_dir="main_computer_test",
                files=["main_computer/viewport.py"],
                instruction="Prepare a context panel.",
                dry_run=True,
                ok=True,
                result_excerpt="Prepared dry-run Aider command preview.",
                route="/api/applications/aider/prepare",
            )
            archived = store.archive_active()
            archive_id = archived["archived"]["id"]
            archive_count = archived["archive_count"]
            active_archive_id = archived["active"]["archive_id"]
            self.assertNotEqual(active_archive_id, archive_id)
            archive_path = root / "archives" / f"{archive_id}.json"
            archive_payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertEqual(len(archive_payload["entries"]), 1)

            loaded = store.load_archive(archive_id)
            self.assertEqual(loaded["active"]["entry_count"], 1)
            self.assertIsNone(loaded["active"]["origin_archive_id"])
            self.assertEqual(loaded["active"]["archive_id"], archive_id)
            self.assertEqual(loaded["archive_count"], archive_count)

            reloaded = AiderWebContextStore(root).status()
            self.assertEqual(reloaded["active"]["archive_id"], archive_id)
            self.assertEqual(reloaded["archive_count"], archive_count)
            self.assertFalse(any("copy" in str(item.get("label", "")) for item in reloaded["archives"]))

            store.append_entry(
                kind="run",
                repo_dir="main_computer_test",
                files=["main_computer/viewport.py"],
                instruction="Apply the context panel.",
                dry_run=False,
                ok=True,
                result_excerpt="Aider completed.",
                route="/api/applications/aider/run",
            )
            active = store.status()["active"]
            self.assertEqual(active["entry_count"], 2)
            self.assertTrue(active["archive_id"])
            self.assertNotEqual(active["archive_id"], archive_id)
            self.assertEqual(active["origin_archive_id"], archive_id)

            archive_payload = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertEqual(len(archive_payload["entries"]), 1)

    def test_reset_active_keeps_archives_and_sets_repo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AiderWebContextStore(Path(temp))
            store.append_entry(
                kind="read",
                repo_dir="repo",
                files=["README.md"],
                instruction="Read README.md.",
                dry_run=True,
                ok=True,
                result_excerpt="README contents",
                route="/api/applications/editor/read",
            )
            archived = store.archive_active()
            self.assertGreaterEqual(archived["archive_count"], 2)

            reset = store.reset_active(repo_dir="repo", files=["README.md"])
            self.assertEqual(reset["active"]["entry_count"], 0)
            self.assertEqual(reset["active"]["repo_dir"], "repo")
            self.assertEqual(reset["active"]["files"], ["README.md"])
            self.assertFalse(reset["active"]["archive_id"])
            self.assertEqual(reset["archive_count"], archived["archive_count"])

    def test_prepare_history_for_blank_context_stays_session_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AiderWebContextStore(Path(temp))
            reset = store.reset_active(repo_dir="repo", files=["README.md"])
            session_id = reset["active"]["id"]
            archive_count = reset["archive_count"]

            history = store.prepare_aider_history_files()

            self.assertEqual(history["history_id"], session_id)
            self.assertEqual(history["archive_id"], session_id)
            self.assertEqual(store.status()["archive_count"], archive_count)
            self.assertFalse(store.status()["active"]["archive_id"])
            self.assertTrue(Path(history["chat_history_file"]).is_file())
            self.assertTrue(Path(history["input_history_file"]).is_file())

    def test_load_latest_archive_is_bootstrapped_as_active_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AiderWebContextStore(root)
            first = store.status()
            first_archive_id = first["active"]["archive_id"]
            store.append_entry(
                kind="read",
                repo_dir="repo",
                files=["README.md"],
                instruction="Read README.md.",
                dry_run=True,
                ok=True,
                result_excerpt="README contents",
                route="/api/applications/editor/read",
            )
            second = store.archive_active()
            latest_archive_id = second["archives"][0]["id"]
            self.assertNotEqual(latest_archive_id, first_archive_id)

            reloaded = AiderWebContextStore(root)
            status = reloaded.status()
            self.assertEqual(status["active"]["archive_id"], status["current_archive"]["id"])
            self.assertEqual(status["archives"][0]["id"], status["active"]["archive_id"])


    def test_status_does_not_rewrite_archive_files_on_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AiderWebContextStore(root)
            store.append_entry(
                kind="run",
                repo_dir="repo",
                files=["main_computer/viewport.py"],
                instruction="Keep the current thread visible.",
                dry_run=True,
                ok=True,
                result_excerpt="Dry run completed.",
                route="/api/applications/aider/run",
            )
            status = store.status()
            archive_id = status["active"]["archive_id"]
            active_path = root / "active.json"
            index_path = root / "index.json"
            archive_path = root / "archives" / f"{archive_id}.json"
            before = {
                "active": active_path.stat().st_mtime_ns,
                "index": index_path.stat().st_mtime_ns,
                "archive": archive_path.stat().st_mtime_ns,
            }

            time.sleep(0.01)
            reread = AiderWebContextStore(root).status()

            after = {
                "active": active_path.stat().st_mtime_ns,
                "index": index_path.stat().st_mtime_ns,
                "archive": archive_path.stat().st_mtime_ns,
            }
            self.assertEqual(reread["active"]["archive_id"], archive_id)
            self.assertEqual(before, after)


    def test_append_entry_to_archive_keeps_background_result_with_launching_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AiderWebContextStore(root)
            store.append_entry(
                kind="prepare",
                repo_dir="repo-one",
                files=["main_computer/viewport.py"],
                instruction="Prepare first thread.",
                dry_run=True,
                ok=True,
                result_excerpt="Prepared command.",
                route="/api/applications/aider/prepare",
            )
            first_archive_id = store.status()["active"]["archive_id"]

            store.reset_active(repo_dir="repo-two", files=["README.md"])
            second_session_id = store.status()["active"]["id"]

            store.append_entry_to_archive(
                first_archive_id,
                kind="run",
                repo_dir="repo-one",
                files=["main_computer/viewport.py"],
                instruction="Finish the first thread in the backend.",
                dry_run=True,
                ok=True,
                returncode=0,
                duration_ms=42,
                result_excerpt="Dry run completed.",
                route="/api/applications/aider/run",
                metadata={"job_id": "job-1"},
            )

            status = store.status()
            self.assertEqual(status["active"]["id"], second_session_id)
            self.assertFalse(status["active"]["archive_id"])
            first_payload = json.loads((root / "archives" / f"{first_archive_id}.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["kind"] for entry in first_payload["entries"]], ["prepare", "run"])
            self.assertEqual(first_payload["entries"][-1]["metadata"]["job_id"], "job-1")



if __name__ == "__main__":
    unittest.main()
