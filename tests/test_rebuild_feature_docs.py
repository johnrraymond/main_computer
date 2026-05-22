from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import crawl_component_docs, rebuild_feature_docs


REPO_ROOT = Path(__file__).resolve().parents[1]


class RebuildFeatureDocsTests(unittest.TestCase):
    def test_script_accepts_feature_and_id_arguments(self) -> None:
        args = rebuild_feature_docs.parse_args(["--feature", "Aider run flow", "--id", "code-editor.aider.run", "--verbose"])
        self.assertEqual(args.feature, "Aider run flow")
        self.assertEqual(args.id, "code-editor.aider.run")
        self.assertTrue(args.verbose)
        self.assertEqual(args.engine, "ollama")
        self.assertEqual(args.model, "ollama_chat/gemma4:26b")
        self.assertEqual(rebuild_feature_docs.resolved_ollama_model(args), "gemma4:26b")
        self.assertEqual(args.aider_source_mode, "evidence")
        self.assertEqual(args.max_files_per_pass, 4)
        self.assertTrue(args.resume)
        self.assertFalse(args.force_full_rebuild)
        self.assertFalse(args.fallback)

    def test_fallback_flag_enables_prompt_script_fallback_mode(self) -> None:
        args = rebuild_feature_docs.parse_args(
            ["--feature", "Aider run flow", "--id", "code-editor.aider.run", "--fallback"]
        )
        self.assertTrue(args.fallback)
        self.assertTrue(rebuild_feature_docs.fallback_enabled(args))

    def test_fallback_aider_pass_streams_first_output_to_console_and_log(self) -> None:
        class FakeStdout:
            def __init__(self, chunks: list[bytes]) -> None:
                self.chunks = chunks

            def read(self, size: int = -1) -> bytes:
                return self.chunks.pop(0) if self.chunks else b""

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = FakeStdout([b"h", b"i"])

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_file = root / "prompt.txt"
            output_file = root / "work" / "fragments" / "overview.html"
            prompt_file.write_text("Write docs", encoding="utf-8")
            args = rebuild_feature_docs.parse_args(
                [
                    "--feature",
                    "Aider run flow",
                    "--id",
                    "code-editor.aider.run",
                    "--engine",
                    "aider",
                    "--fallback",
                    "--aider-command",
                    "fake-aider",
                ]
            )

            stderr = io.StringIO()
            with patch("tools.rebuild_feature_docs.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                with patch("sys.stderr", stderr):
                    rebuild_feature_docs.run_aider_fragment(args, prompt_file, output_file, ["evidence.json"], root)

            command = popen_mock.call_args.args[0]
            self.assertIn("--stream", command)
            self.assertIn("--verbose", command)
            self.assertNotIn("--no-stream", command)
            log_text = (root / "work" / "logs" / "overview.log").read_text(encoding="utf-8")
            self.assertIn("capture: byte-immediate", log_text)
            self.assertIn("first Aider fragment overview output", log_text)
            self.assertIn("hi", log_text)
            self.assertIn("hi", stderr.getvalue())

    def test_discovery_finds_known_code_editor_component(self) -> None:
        discovery = rebuild_feature_docs.discover("Aider run flow in the code editor", "code-editor.aider.run", REPO_ROOT)
        self.assertIn("main_computer/web/applications/apps/code-editor.html", discovery["source_files"])
        self.assertTrue(any(component for component in discovery["matched_components"] if "code-editor.aider.run" in component))
        self.assertNotIn("tools/rebuild_feature_docs.py", discovery["source_files"])
        self.assertNotIn("tests/test_rebuild_feature_docs.py", discovery["source_files"])

    def test_doc_target_identity_prefers_component_id_and_collects_aliases(self) -> None:
        dry_run = rebuild_feature_docs.resolve_doc_target_identity("aider-dry-run", REPO_ROOT)
        self.assertEqual(dry_run["id"], "code-editor.aider.dry-run")
        self.assertIn("aider-dry-run", dry_run["aliases"])
        self.assertIn("code-editor.aider-dry-run", dry_run["aliases"])
        self.assertEqual(dry_run["title"], "Dry Run First")

        run_aider = rebuild_feature_docs.resolve_doc_target_identity("code-editor.aider.run", REPO_ROOT)
        self.assertEqual(run_aider["id"], "code-editor.aider.run")
        self.assertIn("aider-run", run_aider["aliases"])
        self.assertIn("code-editor.aider-run", run_aider["aliases"])
        self.assertEqual(run_aider["title"], "Run Aider")

    def test_doc_target_identity_keeps_unannotated_dom_widgets_addressable(self) -> None:
        search_button = rebuild_feature_docs.resolve_doc_target_identity("file-explorer-search-run", REPO_ROOT)
        self.assertEqual(search_button["id"], "file-explorer-search-run")
        self.assertEqual(search_button["matched_node_path"], "main_computer/web/applications/apps/file-explorer.html")
        self.assertEqual(search_button["title"], "file-explorer-search-run")

    def test_crawl_planner_flag_recommends_limited_real_run(self) -> None:
        args = crawl_component_docs.build_arg_parser().parse_args(["--planner", "--target", "applications"])
        self.assertTrue(args.planner)

        audit_args = crawl_component_docs.build_planner_audit_args(args)
        self.assertTrue(audit_args.dry_run)
        self.assertTrue(audit_args.no_repair)
        self.assertFalse(audit_args.repair)
        self.assertFalse(audit_args.run_new_doc_plan)
        self.assertFalse(audit_args.execute_plan)
        self.assertTrue(audit_args.no_plan_script)

        result = {
            "health": {
                "summary": {
                    "total_manifest_entries": 2,
                    "total_docs_scanned": 2,
                    "current": 0,
                    "stale": 2,
                    "missing": 0,
                    "blocked": 0,
                    "orphaned": 0,
                    "queued_rebuilds": 2,
                },
                "warnings": [],
                "errors": [],
                "blocked_docs": [],
                "unsafe_docs": [],
            },
            "plan": {
                "repairs": [],
                "queue": [
                    {"id": "applications.launcher", "reasons": ["source_newer_than_doc"]},
                    {"id": "applications.workspace", "reasons": ["source_newer_than_doc"]},
                ],
            },
        }
        report = crawl_component_docs.format_planner_report(args, result)
        self.assertIn(
            "python tools/crawl_component_docs.py --verbose --target applications --run-new-doc-plan --max-rebuilds 1",
            report,
        )
        self.assertIn("python tools/crawl_component_docs.py --verbose --target applications --run-new-doc-plan", report)
        self.assertIn("Multiple rebuilds are queued", report)

    def test_crawl_component_scan_includes_dom_id_widgets_without_component_ids(self) -> None:
        scan = crawl_component_docs.scan_components(REPO_ROOT)
        components = scan["components"]

        expected_widgets = {
            "document-library-refresh": "main_computer/web/applications/apps/document.html",
            "file-explorer-search-run": "main_computer/web/applications/apps/file-explorer.html",
            "spreadsheet-refresh": "main_computer/web/applications/apps/spreadsheet.html",
            "task-refresh": "main_computer/web/applications/apps/task-manager.html",
            "terminal-ai-suggest": "main_computer/web/applications/apps/terminal.html",
        }
        for widget_id, source_file in expected_widgets.items():
            with self.subTest(widget_id=widget_id):
                self.assertIn(widget_id, components)
                self.assertIn(source_file, components[widget_id]["source_files"])
                self.assertIn(widget_id, components[widget_id]["dom_ids"])

        self.assertEqual(components["document-library-refresh"]["kind"], "action")
        self.assertEqual(components["terminal-ai-suggest"]["kind"], "action")
        self.assertGreater(len(components), 184)
        self.assertNotIn("chat-console-js-worker-source", components)

    def test_include_docgen_source_includes_docgen_files(self) -> None:
        discovery = rebuild_feature_docs.discover(
            "Aider run flow in the code editor",
            "code-editor.aider.run",
            REPO_ROOT,
            include_docgen_source=True,
        )
        self.assertIn("tools/rebuild_feature_docs.py", discovery["source_files"])
        self.assertIn("tests/test_rebuild_feature_docs.py", discovery["source_files"])

    def test_no_aider_mode_writes_html_docs_manifest_and_work_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            result = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "Aider run flow in the code editor",
                        "--id",
                        "code-editor.aider.run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--scope",
                        "feature",
                    ]
                )
            )
            work_dir = Path(result["work_dir"])
            if not work_dir.is_absolute():
                work_dir = REPO_ROOT / work_dir
            self.assertTrue((work_dir / "discovery.json").is_file())
            self.assertTrue((work_dir / "context_pack.json").is_file())
            for name, _focus in rebuild_feature_docs.FRAGMENT_PASSES:
                self.assertTrue((work_dir / "fragments" / f"{name}.html").is_file())
                self.assertTrue((work_dir / "evidence" / f"{name}.json").is_file())
            final_doc = Path(result["final_doc"])
            if not final_doc.is_absolute():
                final_doc = REPO_ROOT / final_doc
            self.assertTrue(final_doc.is_file())
            html = final_doc.read_text(encoding="utf-8")
            self.assertIn('<article class="mc-component-doc"', html)
            self.assertNotIn("<script", html.lower())
            self.assertNotIn("onclick=", html.lower())
            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            entry = next(item for item in manifest["entries"] if item["id"] == "code-editor.aider.run")
            self.assertIn("aider-run", entry["aliases"])
            self.assertIn("code-editor.aider-run", entry["aliases"])
            self.assertEqual(entry["title"], "Run Aider")
            self.assertEqual(entry["doc_path"], "nodes/code-editor.aider.run.html")
            self.assertFalse(entry["doc_path"].startswith("generated_component_docs/"))
            self.assertFalse(Path(entry["doc_path"]).is_absolute())
            self.assertNotIn("..", entry["doc_path"].split("/"))
            self.assertNotIn("\\", entry["doc_path"])
            self.assertEqual(entry["content_type"], "text/html")
            self.assertEqual(result["manifest_doc_path"], "nodes/code-editor.aider.run.html")
            self.assertTrue(result["final_doc"].endswith("docs/nodes/code-editor.aider.run.html"))
            self.assertIn("main_computer/web/applications/apps/code-editor.html", entry["source_files"])
            run_state = json.loads((work_dir / "run_state.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["manifest_doc_path"], "nodes/code-editor.aider.run.html")
            self.assertTrue((work_dir / "run_state.json").is_file())
            self.assertTrue((work_root / "index.json").is_file())

    def test_dom_id_generation_migrates_to_canonical_component_manifest_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            result = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "The aider dry run checkbox.",
                        "--id",
                        "aider-dry-run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--scope",
                        "micro",
                    ]
                )
            )
            self.assertEqual(result["target_id"], "code-editor.aider.dry-run")
            self.assertEqual(result["manifest_doc_path"], "nodes/code-editor.aider.dry-run.html")
            self.assertFalse(result["manifest_doc_path"].startswith("generated_component_docs/"))
            final_doc = Path(result["final_doc"])
            html = final_doc.read_text(encoding="utf-8")
            self.assertIn('data-mc-doc-target="code-editor.aider.dry-run"', html)

            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in manifest["entries"] if item["id"] == "code-editor.aider.dry-run")
            self.assertEqual(entry["doc_path"], "nodes/code-editor.aider.dry-run.html")
            self.assertEqual(entry["content_type"], "text/html")
            self.assertIn("aliases", entry)
            self.assertIn("aider-dry-run", entry["aliases"])
            self.assertIn("code-editor.aider-dry-run", entry["aliases"])
            self.assertEqual(entry["title"], "Dry Run First")

    def test_manifest_doc_path_helpers_normalize_and_reject_unsafe_paths(self) -> None:
        self.assertEqual(rebuild_feature_docs.normalize_manifest_doc_path("nodes/aider-dry-run.html"), "nodes/aider-dry-run.html")
        self.assertEqual(rebuild_feature_docs.normalize_manifest_doc_path("./nodes/aider-dry-run.html"), "nodes/aider-dry-run.html")
        self.assertEqual(
            rebuild_feature_docs.normalize_manifest_doc_path("generated_component_docs/nodes/aider-dry-run.html"),
            "nodes/aider-dry-run.html",
        )
        self.assertEqual(rebuild_feature_docs.normalize_manifest_doc_path("nodes\\aider-dry-run.html"), "nodes/aider-dry-run.html")
        with self.assertRaises(ValueError):
            rebuild_feature_docs.normalize_manifest_doc_path("../bad.html")
        with self.assertRaises(ValueError):
            rebuild_feature_docs.normalize_manifest_doc_path("C:/tmp/aider-dry-run.html")
        with self.assertRaises(ValueError):
            rebuild_feature_docs.normalize_manifest_doc_path("nodes/aider-dry-run.md")

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "generated_component_docs"
            final_path = output_root / "nodes" / "aider-dry-run.html"
            final_path.parent.mkdir(parents=True)
            final_path.write_text("<article></article>", encoding="utf-8")
            self.assertEqual(rebuild_feature_docs.docs_root_relative_path(final_path, output_root), "nodes/aider-dry-run.html")
            with self.assertRaises(ValueError):
                rebuild_feature_docs.docs_root_relative_path(Path(tmp) / "outside.html", output_root)

    def test_existing_prefixed_manifest_doc_path_is_normalized_on_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            output_root.mkdir(parents=True)
            (output_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "id": "code-editor.aider.run",
                                "doc_path": "generated_component_docs/nodes/code-editor.aider.run.html",
                                "content_type": "text/html",
                                "status": "legacy",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "Aider run flow in the code editor",
                        "--id",
                        "code-editor.aider.run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--scope",
                        "micro",
                    ]
                )
            )
            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in manifest["entries"] if item["id"] == "code-editor.aider.run")
            self.assertEqual(entry["doc_path"], "nodes/code-editor.aider.run.html")
            self.assertEqual(entry["content_type"], "text/html")

    def test_manifest_update_migrates_alias_entry_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            output_root.mkdir(parents=True)
            (output_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "id": "aider-dry-run",
                                "aliases": [],
                                "doc_path": "nodes/aider-dry-run.html",
                                "content_type": "text/html",
                                "status": "legacy",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rebuild_feature_docs.update_manifest(
                output_root / "manifest.json",
                {
                    "id": "code-editor.aider.dry-run",
                    "aliases": ["aider-dry-run", "code-editor.aider-dry-run"],
                    "title": "Dry Run First",
                    "doc_path": "nodes/code-editor.aider.dry-run.html",
                    "content_type": "text/html",
                    "status": "generated",
                },
                False,
            )
            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual([entry["id"] for entry in manifest["entries"]], ["code-editor.aider.dry-run"])
            entry = manifest["entries"][0]
            self.assertEqual(entry["doc_path"], "nodes/code-editor.aider.dry-run.html")
            self.assertIn("aider-dry-run", entry["aliases"])

    def test_fragment_evidence_pack_is_bounded(self) -> None:
        discovery = rebuild_feature_docs.discover("Aider run flow in the code editor", "code-editor.aider.run", REPO_ROOT)
        pack = rebuild_feature_docs.build_fragment_evidence_pack(
            "frontend_flow",
            "Explain frontend flow.",
            discovery,
            REPO_ROOT,
            max_files=2,
            max_snippets_per_file=1,
            max_snippet_chars=240,
            max_total_chars=2500,
        )
        self.assertLessEqual(len(pack["files"]), 2)
        self.assertLessEqual(len(json.dumps(pack)), 4000)
        for item in pack["files"]:
            self.assertLessEqual(len(item["snippets"]), 1)

    def test_source_fingerprint_ignores_docgen_files_by_default(self) -> None:
        discovery = rebuild_feature_docs.discover("Aider run flow in the code editor", "code-editor.aider.run", REPO_ROOT)
        context_pack = rebuild_feature_docs.build_context_pack(discovery, REPO_ROOT)
        before = rebuild_feature_docs.source_fingerprint(discovery, context_pack)
        tool_path = REPO_ROOT / "tools" / "rebuild_feature_docs.py"
        original = tool_path.read_text(encoding="utf-8")
        try:
            tool_path.write_text(original + "\n# temporary fingerprint comment\n", encoding="utf-8")
            changed_discovery = rebuild_feature_docs.discover("Aider run flow in the code editor", "code-editor.aider.run", REPO_ROOT)
            changed_context = rebuild_feature_docs.build_context_pack(changed_discovery, REPO_ROOT)
            after = rebuild_feature_docs.source_fingerprint(changed_discovery, changed_context)
        finally:
            tool_path.write_text(original, encoding="utf-8")
        self.assertEqual(after, before)

    def test_discovery_excludes_doc_build_artifacts_by_default(self) -> None:
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("tools/documentation/plan-example.py"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("tools/documentation/plan-example.state.json"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("tools/crawl_component_docs.py"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("tools/crawl_component_docs-1.py"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("generated_component_docs/doc-build.json"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("generated_component_docs/doc-health.json"))
        self.assertTrue(rebuild_feature_docs.is_doc_build_artifact_path("generated_component_docs/nodes/aider-dry-run.html"))

        docs_dir = REPO_ROOT / "tools" / "documentation"
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_path = docs_dir / "__cache_pollution_plan.py"
        state_path = docs_dir / "__cache_pollution_plan.state.json"
        try:
            plan_path.write_text(
                "code-editor.aider.dry-run The aider dry run checkbox code-editor.aider.run\n",
                encoding="utf-8",
            )
            state_path.write_text(
                "{\"running\": \"code-editor.aider.dry-run\", \"feature\": \"The aider dry run checkbox.\"}\n",
                encoding="utf-8",
            )
            discovery = rebuild_feature_docs.discover(
                "The aider dry run checkbox.",
                "code-editor.aider.dry-run",
                REPO_ROOT,
            )
        finally:
            for path in (plan_path, state_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self.assertNotIn("tools/documentation/__cache_pollution_plan.py", discovery["source_files"])
        self.assertNotIn("tools/documentation/__cache_pollution_plan.state.json", discovery["source_files"])
        self.assertIn("tools/documentation/__cache_pollution_plan.py", discovery.get("excluded_doc_build_artifacts", []))
        self.assertIn("tools/documentation/__cache_pollution_plan.state.json", discovery.get("excluded_doc_build_artifacts", []))

    def test_source_fingerprint_ignores_doc_build_artifact_changes(self) -> None:
        docs_dir = REPO_ROOT / "tools" / "documentation"
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_path = docs_dir / "__fingerprint_pollution_plan.py"
        state_path = docs_dir / "__fingerprint_pollution_plan.state.json"
        try:
            plan_path.write_text("code-editor.aider.dry-run The aider dry run checkbox.\n", encoding="utf-8")
            state_path.write_text("{\"step\": 1}\n", encoding="utf-8")
            discovery = rebuild_feature_docs.discover(
                "The aider dry run checkbox.",
                "code-editor.aider.dry-run",
                REPO_ROOT,
            )
            context_pack = rebuild_feature_docs.build_context_pack(discovery, REPO_ROOT)
            before = rebuild_feature_docs.source_fingerprint(discovery, context_pack)
            state_path.write_text("{\"step\": 2, \"running\": true}\n", encoding="utf-8")
            changed_discovery = rebuild_feature_docs.discover(
                "The aider dry run checkbox.",
                "code-editor.aider.dry-run",
                REPO_ROOT,
            )
            changed_context = rebuild_feature_docs.build_context_pack(changed_discovery, REPO_ROOT)
            after = rebuild_feature_docs.source_fingerprint(changed_discovery, changed_context)
        finally:
            for path in (plan_path, state_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self.assertEqual(after, before)
        self.assertFalse(any(path.startswith("tools/documentation/") for path in changed_discovery["source_files"]))

    def test_crawl_source_info_ignores_doc_build_artifacts_for_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "src" / "feature.py"
            plan_path = tmp_path / "tools" / "documentation" / "plan-later.py"
            source_path.parent.mkdir(parents=True)
            plan_path.parent.mkdir(parents=True)
            source_path.write_text("feature source\n", encoding="utf-8")
            plan_path.write_text("generated plan mentions code-editor.aider.dry-run\n", encoding="utf-8")
            os.utime(source_path, (100.0, 100.0))
            os.utime(plan_path, (300.0, 300.0))

            info = crawl_component_docs.source_info(
                tmp_path,
                ["src/feature.py", "tools/documentation/plan-later.py"],
            )

        self.assertEqual(info["max_mtime"], 100.0)
        self.assertEqual([item["path"] for item in info["files"]], ["src/feature.py"])
        self.assertEqual(info["missing"], [])
        self.assertEqual(info["skipped_artifacts"], ["tools/documentation/plan-later.py"])

    def test_crawl_normalize_entry_removes_doc_build_artifact_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "generated_component_docs"
            output_root.mkdir(parents=True)
            entry = {
                "id": "code-editor.aider.dry-run",
                "doc_path": "nodes/code-editor.aider.dry-run.html",
                "source_files": [
                    "main_computer/web/applications/apps/code-editor.html",
                    "tools/documentation/plan-previous.py",
                    "tools/crawl_component_docs-1.py",
                ],
            }

            normalized, repairs, warnings, errors = crawl_component_docs.normalize_entry(
                entry,
                output_root=output_root,
                repo_root=tmp_path,
                components={},
                alias_to_component={},
                doc_files={},
                repair=True,
                dry_run=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(normalized["source_files"], ["main_computer/web/applications/apps/code-editor.html"])
        self.assertTrue(any(repair["kind"] == "source_files_doc_build_artifacts" for repair in repairs))

    def test_aider_command_disables_commits_and_repo_map_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt = tmp_path / "prompt.txt"
            output = tmp_path / "fragments" / "overview.html"
            evidence = tmp_path / "evidence.json"
            prompt.write_text("prompt", encoding="utf-8")
            evidence.write_text("{}", encoding="utf-8")
            commands = []

            class Result:
                returncode = 0

            def fake_run(command, **kwargs):
                commands.append(command)
                return Result()

            args = rebuild_feature_docs.parse_args(
                ["--feature", "Aider run flow", "--id", "code-editor.aider.run", "--aider-command", "aider"]
            )
            with patch.object(rebuild_feature_docs.subprocess, "run", side_effect=fake_run):
                rebuild_feature_docs.run_aider_fragment(args, prompt, output, [str(evidence)], REPO_ROOT)
            command = commands[0]
            self.assertIn("--no-git", command)
            self.assertIn("--no-auto-commits", command)
            self.assertIn("--no-dirty-commits", command)
            self.assertIn("--map-tokens", command)
            self.assertIn("0", command)

    def test_ollama_model_override_wins(self) -> None:
        args = rebuild_feature_docs.parse_args(
            [
                "--feature",
                "Aider run flow",
                "--id",
                "code-editor.aider.run",
                "--model",
                "ollama_chat/gemma4:26b",
                "--ollama-model",
                "gemma4:26b",
            ]
        )
        self.assertEqual(rebuild_feature_docs.resolved_ollama_model(args), "gemma4:26b")

    def test_ollama_fragment_http_call_can_be_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prompt = tmp_path / "prompt.txt"
            evidence = tmp_path / "evidence.json"
            fragment = tmp_path / "fragments" / "overview.html"
            log = tmp_path / "logs" / "overview.log"
            prompt.write_text("Prompt", encoding="utf-8")
            evidence.write_text("{}", encoding="utf-8")
            args = rebuild_feature_docs.parse_args(
                ["--feature", "Aider run flow", "--id", "code-editor.aider.run", "--ollama-model", "fake-model"]
            )

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return json.dumps({"message": {"content": "```html\n<p>Mocked Ollama.</p>\n```"}}).encode("utf-8")

            with patch.object(rebuild_feature_docs.urllib.request, "urlopen", return_value=FakeResponse()) as mocked:
                meta = rebuild_feature_docs.run_ollama_fragment(args, prompt, fragment, evidence, log, "fake-model")
            self.assertTrue(mocked.called)
            self.assertIn("Mocked Ollama", fragment.read_text(encoding="utf-8"))
            self.assertEqual(meta["model"], "fake-model")
            self.assertTrue(log.is_file())

    def test_default_engine_ollama_does_not_invoke_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"

            def fake_ollama(args, prompt_path, fragment_path, evidence_path, log_path, model):
                fragment_path.write_text("<p>Generated by Ollama.</p>", encoding="utf-8")
                rebuild_feature_docs.write_json(log_path, {"engine": "ollama", "model": model})
                return {"elapsed_seconds": 0.01, "model": model}

            with patch.object(rebuild_feature_docs, "run_ollama_fragment", side_effect=fake_ollama), patch.object(
                rebuild_feature_docs.subprocess, "run", side_effect=AssertionError("subprocess should not run")
            ):
                result = rebuild_feature_docs.build_docs(
                    rebuild_feature_docs.parse_args(
                        [
                            "--feature",
                            "The aider dry run checkbox.",
                            "--id",
                            "aider-dry-run",
                            "--output-root",
                            str(output_root),
                            "--work-root",
                            str(work_root),
                        ]
                    )
                )
            self.assertEqual(result["engine"], "ollama")
            self.assertEqual(result["scope"], "micro")
            final_doc = Path(result["final_doc"])
            self.assertIn("Generated by Ollama", final_doc.read_text(encoding="utf-8"))

    def test_tainted_fragment_is_not_reused_without_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fragment = Path(tmp) / "overview.html"
            fragment.write_text("<section><p>Generated.</p></section>", encoding="utf-8")
            state = {"status": "generated_tainted", "fingerprint": "abc"}
            reusable, reason, _validation = rebuild_feature_docs.fragment_is_complete(fragment, state, "abc", False, False)
            self.assertFalse(reusable)
            self.assertIn("tainted", reason)
            reusable, _reason, _validation = rebuild_feature_docs.fragment_is_complete(fragment, state, "abc", False, True)
            self.assertTrue(reusable)

    def test_aider_target_source_change_is_restored_and_tainted(self) -> None:
        source_path = REPO_ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
        before = source_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"

            def fake_aider(args, prompt_file, output_file, files, root):
                output_file.write_text("<p>Useful fragment.</p>", encoding="utf-8")
                source_path.write_text(before + "\n<!-- forbidden edit -->\n", encoding="utf-8")

            with patch.object(rebuild_feature_docs, "run_aider_fragment", side_effect=fake_aider):
                with self.assertRaisesRegex(RuntimeError, "Aider modified source files"):
                    rebuild_feature_docs.build_docs(
                        rebuild_feature_docs.parse_args(
                            [
                                "--feature",
                                "Aider run flow in the code editor",
                                "--id",
                                "code-editor.aider.run",
                                "--output-root",
                                str(output_root),
                                "--work-root",
                                str(work_root),
                                "--engine",
                                "aider",
                                "--force-full-rebuild",
                            ]
                        )
                    )
            self.assertEqual(source_path.read_text(encoding="utf-8"), before)
            state_paths = list(work_root.glob("*/run_state.json"))
            self.assertTrue(state_paths)
            state = json.loads(state_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(state["fragments"]["overview"]["status"], "generated_tainted")

    def test_aider_docgen_change_is_restored_with_specific_error(self) -> None:
        tool_path = REPO_ROOT / "tools" / "rebuild_feature_docs.py"
        before = tool_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"

            def fake_aider(args, prompt_file, output_file, files, root):
                output_file.write_text("<p>Useful fragment.</p>", encoding="utf-8")
                tool_path.write_text(before + "\n# forbidden docgen edit\n", encoding="utf-8")

            with patch.object(rebuild_feature_docs, "run_aider_fragment", side_effect=fake_aider):
                with self.assertRaisesRegex(RuntimeError, "documentation generator files"):
                    rebuild_feature_docs.build_docs(
                        rebuild_feature_docs.parse_args(
                            [
                                "--feature",
                                "Aider run flow in the code editor",
                                "--id",
                                "code-editor.aider.run",
                                "--output-root",
                                str(output_root),
                                "--work-root",
                                str(work_root),
                                "--engine",
                                "aider",
                                "--force-full-rebuild",
                            ]
                        )
                    )
            self.assertEqual(tool_path.read_text(encoding="utf-8"), before)

    def test_no_aider_mode_does_not_modify_source_files(self) -> None:
        source_path = REPO_ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
        before = hashlib.sha256(source_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/rebuild_feature_docs.py",
                    "--feature",
                    "Aider run flow in the code editor",
                    "--id",
                    "code-editor.aider.run",
                    "--output-root",
                    str(Path(tmp) / "docs"),
                    "--work-root",
                    str(Path(tmp) / "work"),
                    "--no-aider",
                    "--scope",
                    "feature",
                    "--verbose",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertIn("[rebuild-feature-docs]", result.stderr)
        after = hashlib.sha256(source_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def _build_no_aider(self, output_root: Path, work_root: Path, *extra: str) -> dict[str, object]:
        return rebuild_feature_docs.build_docs(
            rebuild_feature_docs.parse_args(
                [
                    "--feature",
                    "Aider run flow in the code editor",
                    "--id",
                    "code-editor.aider.run",
                    "--output-root",
                    str(output_root),
                    "--work-root",
                    str(work_root),
                    "--no-aider",
                    "--scope",
                    "feature",
                    *extra,
                ]
            )
        )

    def test_second_run_reuses_work_dir_and_completed_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            second = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            self.assertEqual(second["work_dir"], first["work_dir"])
            self.assertTrue(second["resumed"])
            self.assertEqual(sorted(second["fragments_reused"]), sorted(name for name, _focus in rebuild_feature_docs.FRAGMENT_PASSES))
            self.assertEqual(second["fragments_generated"], [])

    def test_resume_regenerates_only_missing_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            work_dir = Path(first["work_dir"])
            if not work_dir.is_absolute():
                work_dir = REPO_ROOT / work_dir
            (work_dir / "fragments" / "backend_flow.html").unlink()
            second = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            self.assertEqual(second["work_dir"], first["work_dir"])
            self.assertIn("backend_flow", second["fragments_generated"])
            self.assertNotIn("overview", second["fragments_generated"])
            self.assertIn("overview", second["fragments_reused"])

    def test_dry_run_work_dir_can_be_resumed_by_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            dry = self._build_no_aider(output_root, work_root, "--dry-run")
            real = self._build_no_aider(output_root, work_root)
            self.assertEqual(real["work_dir"], dry["work_dir"])
            self.assertTrue(real["resumed"])
            self.assertEqual(sorted(real["fragments_generated"]), sorted(name for name, _focus in rebuild_feature_docs.FRAGMENT_PASSES))

    def test_force_full_rebuild_creates_new_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            second = self._build_no_aider(output_root, work_root, "--accept-template-fragments", "--force-full-rebuild")
            self.assertNotEqual(second["work_dir"], first["work_dir"])
            self.assertFalse(second["resumed"])

    def test_timeout_does_not_invalidate_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "Aider run flow in the code editor",
                        "--id",
                        "code-editor.aider.run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--accept-template-fragments",
                        "--timeout",
                        "10",
                    ]
                )
            )
            second = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "Aider run flow in the code editor",
                        "--id",
                        "code-editor.aider.run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--accept-template-fragments",
                        "--timeout",
                        "20",
                    ]
                )
            )
            self.assertEqual(second["work_dir"], first["work_dir"])

    def test_corrupt_fragment_is_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            work_dir = Path(first["work_dir"])
            if not work_dir.is_absolute():
                work_dir = REPO_ROOT / work_dir
            (work_dir / "fragments" / "overview.html").write_text("<script>alert(1)</script>", encoding="utf-8")
            second = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            self.assertIn("overview", second["fragments_generated"])

    def test_paragraph_only_fragment_is_normalized(self) -> None:
        normalized = rebuild_feature_docs.normalize_fragment_html("overview", "Explain it.", "<p>Hello docs.</p>")
        self.assertIn('<section class="mc-doc-fragment"', normalized)
        self.assertIn("Hello docs.", normalized)
        self.assertTrue(rebuild_feature_docs.validate_fragment_html_text(normalized)["ok"])

    def test_markdown_fenced_html_is_unfenced_and_normalized(self) -> None:
        normalized = rebuild_feature_docs.normalize_fragment_html("overview", "Explain it.", "```html\n<p>Hello docs.</p>\n```")
        self.assertNotIn("```", normalized)
        self.assertIn("<p>Hello docs.</p>", normalized)
        self.assertTrue(rebuild_feature_docs.validate_fragment_html_text(normalized)["ok"])

    def test_full_html_body_wrappers_are_stripped(self) -> None:
        normalized = rebuild_feature_docs.normalize_fragment_html("overview", "Explain it.", "<html><body><p>Hello docs.</p></body></html>")
        self.assertNotIn("<html", normalized.lower())
        self.assertNotIn("<body", normalized.lower())
        self.assertTrue(rebuild_feature_docs.validate_fragment_html_text(normalized)["ok"])

    def test_unsafe_script_content_still_fails_validation(self) -> None:
        result = rebuild_feature_docs.validate_fragment_html_text('<section><script>alert(1)</script></section>')
        self.assertFalse(result["ok"])

    def test_resume_repairs_paragraph_only_fragment_without_regenerating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            first = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            work_dir = Path(first["work_dir"])
            if not work_dir.is_absolute():
                work_dir = REPO_ROOT / work_dir
            (work_dir / "fragments" / "overview.html").write_text("<p>Already generated.</p>", encoding="utf-8")
            second = self._build_no_aider(output_root, work_root, "--accept-template-fragments")
            self.assertIn("overview", second["fragments_reused"])
            self.assertNotIn("overview", second["fragments_generated"])
            repaired = (work_dir / "fragments" / "overview.html").read_text(encoding="utf-8")
            self.assertIn('<section class="mc-doc-fragment"', repaired)

    def test_force_full_rebuild_deletes_older_matching_dry_runs_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            old_dry = self._build_no_aider(output_root, work_root, "--dry-run", "--force-full-rebuild")
            latest_dry = self._build_no_aider(output_root, work_root, "--dry-run", "--force-full-rebuild")
            other_dry = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        "Different feature",
                        "--id",
                        "code-editor.aider.run",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--dry-run",
                        "--force-full-rebuild",
                    ]
                )
            )
            full = self._build_no_aider(output_root, work_root, "--force-full-rebuild")
            old_path = REPO_ROOT / str(old_dry["work_dir"])
            latest_path = REPO_ROOT / str(latest_dry["work_dir"])
            other_path = REPO_ROOT / str(other_dry["work_dir"])
            full_path = REPO_ROOT / str(full["work_dir"])
            self.assertFalse(old_path.exists())
            self.assertTrue(latest_path.exists())
            self.assertTrue(other_path.exists())
            self.assertTrue(full_path.exists())
            self.assertTrue((output_root / "nodes").exists())

    def test_force_full_rebuild_cleans_legacy_work_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            work_root.mkdir()
            old_legacy = work_root / "Aider-run-flow-in-the-code-editor__code-editor.aider.run__0001"
            latest_legacy = work_root / "Aider-run-flow-in-the-code-editor__code-editor.aider.run__0002"
            other_legacy = work_root / "Other-feature__code-editor.aider.run__0001"
            for path in [old_legacy, latest_legacy, other_legacy]:
                (path / "prompts").mkdir(parents=True)
                (path / "prompts" / "overview.txt").write_text("prompt", encoding="utf-8")
            old_time = 1_700_000_000
            latest_time = 1_700_000_100
            os.utime(old_legacy, (old_time, old_time))
            os.utime(latest_legacy, (latest_time, latest_time))
            self._build_no_aider(output_root, work_root, "--force-full-rebuild")
            self.assertFalse(old_legacy.exists())
            self.assertTrue(latest_legacy.exists())
            self.assertTrue(other_legacy.exists())

    def test_preserve_dry_runs_skips_legacy_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            legacy = work_root / "Aider-run-flow-in-the-code-editor__code-editor.aider.run__0001"
            (legacy / "prompts").mkdir(parents=True)
            self._build_no_aider(output_root, work_root, "--force-full-rebuild", "--preserve-dry-runs")
            self.assertTrue(legacy.exists())


    def test_long_feature_description_uses_short_work_dir_name(self) -> None:
        long_feature = (
            "Embedded Code Editor viewport that renders generated HTML developer documentation "
            "with resolution presets snap sizing handheld desktop modes and backend control"
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "docs"
            work_root = Path(tmp) / "work"
            result = rebuild_feature_docs.build_docs(
                rebuild_feature_docs.parse_args(
                    [
                        "--feature",
                        long_feature,
                        "--id",
                        "code-editor.viewport.root",
                        "--output-root",
                        str(output_root),
                        "--work-root",
                        str(work_root),
                        "--no-aider",
                        "--scope",
                        "micro",
                    ]
                )
            )
            work_dir = Path(result["work_dir"])
            if not work_dir.is_absolute():
                work_dir = REPO_ROOT / work_dir
            self.assertLessEqual(len(work_dir.name), 120)
            self.assertIn("code-editor.viewport.root", work_dir.name)
            self.assertNotIn("generated-HTML-developer-documentation-with-resolution", work_dir.name)
            context = json.loads((work_dir / "context_pack.json").read_text(encoding="utf-8"))
            self.assertEqual(context["feature_description"], long_feature)

    def test_write_json_creates_parent_and_uses_short_temp_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long" / "nested" / "implementation_flow.json"
            rebuild_feature_docs.write_json(path, {"ok": True})
            self.assertTrue(path.is_file())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertFalse(any(item.name.startswith(".implementation_flow.json.") for item in path.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
