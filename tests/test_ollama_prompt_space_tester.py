from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools import ollama_prompt_space_tester


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self.payload[self.offset :]
            self.offset = len(self.payload)
            return chunk
        if self.offset >= len(self.payload):
            return b""
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> FakeResponse:
        self.requests.append(request)
        payload = json.loads(request.data.decode("utf-8"))
        content = (
            "Artifact mode: changed-files snapshot zip\n"
            "Touched files:\n"
            "- main_computer/viewport/routes.py\n"
            "- tests/test_viewport_widget_resize.py\n"
            "Replacement payloads:\n"
            "```python\n"
            "def resize_widget(payload):\n"
            "    pass\n"
            "```\n"
            "reference.patch is optional and fuzz only matters when the reference exists.\n"
            "Undo command: python new_patch.py undo_bundle.zip --dry-run\n"
            "Verification: not run locally; unverified runtime behavior.\n"
            "Assumptions and warnings: latest zip is source of truth; changed files only; "
            "omission does not imply deletion; bool, missing, zero, negative, and string values are rejected.\n"
            "Recommended dry-run: python new_patch.py resize_positive_ints.zip --dry-run\n"
        )
        if payload.get("stream"):
            wire = (
                json.dumps({"message": {"thinking": "thinking first ", "content": content[:20]}, "done": False}) + "\n"
                + json.dumps({"message": {"content": content[20:]}, "done": False}) + "\n"
                + json.dumps({"done": True}) + "\n"
            )
            return FakeResponse(wire.encode("utf-8"))
        return FakeResponse(json.dumps({"message": {"content": content}, "done": True}).encode("utf-8"))


class OllamaPromptSpaceSuiteTests(unittest.TestCase):
    def test_default_suite_runs_all_systems_with_winzip_prompt_only_and_writes_master_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ollama_prompt_space_tester.build_arg_parser().parse_args(
                ["--model", "fake-model", "--out-dir", tmp, "--run-id", "suite-check"]
            )
            console = io.StringIO()
            rc = ollama_prompt_space_tester.run_suite(args, opener=FakeOpener(), console_stream=console)

            self.assertEqual(rc, 0)
            out_dir = Path(tmp) / "suite-check"
            master_path = out_dir / "master_results.json"
            self.assertTrue(master_path.exists())

            master = json.loads(master_path.read_text(encoding="utf-8"))
            expected_total = len(ollama_prompt_space_tester.SYSTEM_PROMPTS)
            self.assertEqual(master["status"], "completed")
            self.assertEqual(master["progress"]["total_runs"], expected_total)
            self.assertEqual(master["progress"]["completed_runs"], expected_total)
            self.assertEqual(master["configuration"]["systems"], list(ollama_prompt_space_tester.SYSTEM_PROMPTS))
            self.assertEqual(master["configuration"]["prompts"], ["winzip_patch_artifact_complex"])
            self.assertEqual(len(master["runs"]), expected_total)
            self.assertTrue((out_dir / "metrics.csv").exists())
            self.assertTrue((out_dir / "runs.jsonl").exists())
            self.assertTrue((out_dir / "responses.jsonl").exists())
            self.assertTrue((out_dir / "summary.md").exists())
            self.assertIn("prompt cases: winzip_patch_artifact_complex", console.getvalue())

    def test_fallback_streams_model_text_to_console_and_records_first_output_in_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ollama_prompt_space_tester.build_arg_parser().parse_args(
                [
                    "--model",
                    "fake-model",
                    "--systems",
                    "baseline_helpful",
                    "--prompts",
                    "winzip_patch_artifact_complex",
                    "--out-dir",
                    tmp,
                    "--run-id",
                    "fallback-check",
                    "--fallback",
                ]
            )
            console = io.StringIO()
            rc = ollama_prompt_space_tester.run_suite(args, opener=FakeOpener(), console_stream=console)

            self.assertEqual(rc, 0)
            master = json.loads((Path(tmp) / "fallback-check" / "master_results.json").read_text(encoding="utf-8"))
            self.assertTrue(master["configuration"]["fallback"])
            self.assertTrue(master["configuration"]["stream"])
            self.assertTrue(master["configuration"]["trace_bytes"])
            self.assertEqual(master["progress"]["completed_runs"], 1)

            run = master["runs"][0]
            self.assertTrue(run["stream"])
            self.assertIsNotNone(run["first_http_byte_ms"])
            self.assertIsNotNone(run["first_model_output_ms"])
            response_path = Path(tmp) / "fallback-check" / run["response_file"]
            response_text = response_path.read_text(encoding="utf-8")
            self.assertIn("thinking first", response_text)
            self.assertIn("Artifact mode:", response_text)

            console_text = console.getvalue()
            self.assertIn("[ollama-suite:model-output] BEGIN", console_text)
            self.assertIn("thinking first", console_text)
            self.assertIn("Artifact mode:", console_text)
            self.assertIn("first model text", console_text)

            log_path = Path(tmp) / "fallback-check" / run["log_file"]
            self.assertIn('"event": "first_model_output"', log_path.read_text(encoding="utf-8"))

    def test_all_prompts_flag_includes_optional_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ollama_prompt_space_tester.build_arg_parser().parse_args(
                [
                    "--model",
                    "fake-model",
                    "--systems",
                    "baseline_helpful",
                    "--all-prompts",
                    "--out-dir",
                    tmp,
                    "--run-id",
                    "all-prompts-check",
                ]
            )
            rc = ollama_prompt_space_tester.run_suite(args, opener=FakeOpener(), console_stream=io.StringIO())

            self.assertEqual(rc, 0)
            master = json.loads((Path(tmp) / "all-prompts-check" / "master_results.json").read_text(encoding="utf-8"))
            self.assertEqual(master["configuration"]["prompts"], list(ollama_prompt_space_tester.ALL_TEST_PROMPTS))
            self.assertEqual(master["progress"]["total_runs"], len(ollama_prompt_space_tester.ALL_TEST_PROMPTS))

    def test_model_alias_and_filtering_keep_suite_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ollama_prompt_space_tester.build_arg_parser().parse_args(
                [
                    "--model",
                    "fake-model",
                    "--systems",
                    "baseline_helpful",
                    "--prompts",
                    "root_conflict_delete_semantics",
                    "--out-dir",
                    tmp,
                    "--run-id",
                    "filter-check",
                ]
            )
            rc = ollama_prompt_space_tester.run_suite(args, opener=FakeOpener(), console_stream=io.StringIO())

            self.assertEqual(rc, 0)
            master = json.loads((Path(tmp) / "filter-check" / "master_results.json").read_text(encoding="utf-8"))
            self.assertEqual(master["configuration"]["models"], ["fake-model"])
            self.assertEqual(master["configuration"]["systems"], ["baseline_helpful"])
            self.assertEqual(master["configuration"]["prompts"], ["root_conflict_delete_semantics"])
            self.assertEqual(master["progress"]["total_runs"], 1)


if __name__ == "__main__":
    unittest.main()
