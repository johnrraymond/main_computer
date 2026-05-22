from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class GitControlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        self.source_root = Path.cwd().resolve()
        self.script_source = self.source_root / "git-control.py"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "git-control.py").write_text(self.script_source.read_text(encoding="utf-8"), encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True, text=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, "git-control.py", "--json", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and completed.returncode != 0:
            self.fail(f"git-control.py failed with {completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_plan_creates_first_class_plan_shim_with_includes(self) -> None:
        payload = self.run_cli("--plan", "--prompt", "inspect repository")
        self.assertTrue(payload["ok"])
        plan_id = payload["plan_shim"]["id"]
        self.assertTrue(plan_id.startswith("plan-documentation-git-control-plan-context-"))
        self.assertEqual(len(payload["included_shims"]), 3)

        read = self.run_cli("--read-shim", plan_id)
        self.assertTrue(read["ok"])
        self.assertIn("shim-include", read["text"])
        self.assertIn("Plan mode is shim-first", read["text"])

    def test_direct_git_command_records_rerunnable_shim(self) -> None:
        payload = self.run_cli("--git", "status", "--short")
        self.assertTrue(payload["ok"])
        shim_id = payload["shim"]["id"]
        self.assertIn("git-command-read-only-git-status-short", shim_id)
        self.assertEqual(payload["shim"]["ordination_recommendation"], "good")

        listing = self.run_cli("--list-shims")
        self.assertTrue(any(item["id"] == shim_id for item in listing["shims"]))

        read = self.run_cli("--read-shim", shim_id)
        self.assertEqual(read["git_commands"], ["git status --short"])
        self.assertIn("# command: git status --short", read["text"])
        self.assertIn("# ordination-recommendation: good", read["text"])

        rerun = self.run_cli("--run-shim", shim_id)
        self.assertTrue(rerun["ok"])
        self.assertEqual(rerun["results"][-1]["result"]["returncode"], 0)

    def test_extract_ai_python_output_creates_shim_without_running_it(self) -> None:
        ai_output = """The AI suggests:

```python
import subprocess
subprocess.run([sys.executable, "git-control.py", "--recommend", "good", "--git", "status", "--short", "--branch"])
```

Human shell alternative:
python git-control.py --recommend good --doc-shim clean
"""
        source = self.repo / "ai-output.txt"
        source.write_text(ai_output, encoding="utf-8")

        extracted = self.run_cli("--extract-shims-from", str(source))
        self.assertTrue(extracted["ok"])
        self.assertEqual(extracted["command_count"], 2)
        self.assertTrue(any(item["kind"] == "git-command" for item in extracted["shims"]))
        self.assertTrue(any(item["kind"] == "git-doc" for item in extracted["shims"]))

        listing = self.run_cli("--list-shims")
        commands = "\n".join("\n".join(item["git_commands"]) for item in listing["shims"])
        self.assertIn("git status --short --branch", commands)

    def test_ai_shim_block_recommendation_and_ordained_context(self) -> None:
        ai_output = """The AI recommends this candidate:

```shim
# git-control-shim: 1
# title: inspect branch status
# kind: git-command
# ordination-recommendation: good
# ordination-reason: read-only status is safe context
shim-doc inspect the branch and dirty state
git status --short --branch
```
"""
        source = self.repo / "ai-shim-output.txt"
        source.write_text(ai_output, encoding="utf-8")

        extracted = self.run_cli("--extract-shims-from", str(source))
        self.assertTrue(extracted["ok"])
        self.assertEqual(extracted["shim_block_count"], 1)
        shim = extracted["shims"][0]
        self.assertEqual(shim["ordination_recommendation"], "good")
        self.assertEqual(shim["metadata"]["ordination-recommendation"], "good")

        ordained = self.run_cli("--ordain-shim", shim["id"])
        self.assertTrue(ordained["ok"])
        self.assertTrue(ordained["shim"]["ordained"])

        context = self.run_cli("--ordained-context")
        self.assertTrue(context["ok"])
        self.assertEqual(context["count"], 1)
        self.assertIn("ordination-recommendation: good", context["context"])
        self.assertIn("git status --short --branch", context["context"])

    def test_ai_brief_loads_ordained_shims_and_requests_recommendations(self) -> None:
        payload = self.run_cli("--recommend", "good", "--git", "status", "--short")
        shim_id = payload["shim"]["id"]
        self.run_cli("--ordain-shim", shim_id)

        brief = self.run_cli("--ai-brief", "--prompt", "what should I inspect next?")
        self.assertTrue(brief["ok"])
        self.assertIn("Ordained shim context:", brief["prompt"])
        self.assertIn("git status --short", brief["prompt"])
        self.assertIn("# ordination-recommendation: good", brief["prompt"])
        self.assertIn("# ordination-recommendation: not-recommended", brief["prompt"])

    def test_delete_shim_removes_stored_file(self) -> None:
        payload = self.run_cli("--git", "status", "--short")
        shim_id = payload["shim"]["id"]

        deleted = self.run_cli("--delete-shim", shim_id)
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["deleted"])

        listing = self.run_cli("--list-shims")
        self.assertFalse(any(item["id"] == shim_id for item in listing["shims"]))


if __name__ == "__main__":
    unittest.main()
