from __future__ import annotations

from pathlib import Path


SMOKE = Path(__file__).resolve().parents[1] / "main_computer" / "rag_debug_website_golden_path_smoke.py"


def test_golden_path_uses_blessed_generated_editor_path_instead_of_deterministic_edit_fixture() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert "run_blessed_generated_editor_patch_artifact" in source
    assert "call_model_json_stage" in source
    assert "make_discovery_prompt" in source
    assert "make_discovery_repair_prompt" in source
    assert "make_discovery_anchor_option_repair_prompt" in source
    assert "materialize_discovery_card_from_anchor_option_selection" in source
    assert "build_anchor_options_for_discovery_repair" in source
    assert "make_grounding_validation_repair_prompt" in source
    assert "make_patch_proposal_validation_repair_prompt" in source
    assert "discovery_repair_attempts" in source
    assert "discovery_anchor_option_repair_attempts" in source
    assert "grounding_repair_attempts" in source
    assert "patch_proposal_repair_attempts" in source
    assert "validate_discovery_card" in source
    assert "make_grounding_prompt" in source
    assert "make_excerpt_patch_prompt" in source
    assert "promote_verified_excerpt_to_full_file" in source
    assert "package_full_file_replacement_snapshot_artifact" in source
    assert "ensure_new_patch_for_artifact_packaging" in source
    assert "evaluate_terminal_result_contract" in source

    forbidden = [
        "ORIGINAL_PHRASE",
        "UPDATED_PHRASE",
        "build_promotable_edit_decision",
        "write_patch_zip_from_promotable_edit",
        "patched_source = original.replace",
        "promotable_debug_website_edit_decision",
        "In index.html, update",
        "paragraph that says",
        "generate, repair, and debug websites safely. Make it explain",
        "members[-1]",
        "artifact_packaging_result = None",
        "full_file_promotion_result = None",
        "Path(str(artifact_report.get(\"artifact_path\") or \"\"))",
        "missing_patch_artifact.zip\").write",
        "patched_source =",
    ]
    for needle in forbidden:
        assert needle not in source

    assert "failed_check(" in source
    assert "artifact_path_text and zip_path.is_file()" in source

def test_golden_path_keeps_patching_tool_out_of_ai_discovery_workspace_until_packaging() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    copy_start = source.index("def copy_debug_site_to_ai_workspace")
    ensure_start = source.index("def ensure_new_patch_for_artifact_packaging")
    copy_body = source[copy_start:ensure_start]

    assert "shutil.copy2(root / \"new_patch.py\"" not in copy_body
    assert "ensure_new_patch_for_artifact_packaging(root=root, ai_repo=ai_repo)" in source


def test_golden_path_smoke_emits_human_progress_without_polluting_final_json_stdout() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert "class ProgressReporter" in source
    assert "file=sys.stderr" in source
    assert "flush=True" in source
    assert "START golden-path smoke" in source
    assert "AI model call {stage_name}" in source
    assert "STILL AI model call" in source or "STILL {message}" in source
    assert "START new_patch dry-run" in source or 'label="new_patch dry-run"' in source
    assert "--progress-interval-seconds" in source
    assert "--quiet" in source
    assert "stdout remains reserved" in source or "Stdout remains reserved" in source
    assert "progress_events_tail" in source


def test_patch_proposal_promotion_preflight_rejects_incomplete_homepage_fragment() -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke

    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <title>debug</title>
    <link rel=\"stylesheet\" href=\"/style.css\">
  </head>
  <body>
    <main class=\"debug-shell\">
      <p>Original copy.</p>
    </main>
    <script src=\"/script.js\"></script>
  </body>
</html>
"""
            }
        },
    }
    fragment_proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": "    <main class=\"debug-shell\">\n      <p>Updated copy.</p>\n",
    }

    result = smoke.validate_patch_proposal_preserves_promotable_excerpt(
        proposal=fragment_proposal,
        evidence=evidence,
    )

    assert not result.ok
    assert "full final SOURCE_EXCERPT" in " ".join(result.blocking_reasons or [])
    assert "</html>" in " ".join(result.issues)


def test_patch_proposal_promotion_preflight_accepts_complete_homepage_excerpt() -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke

    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <title>debug</title>
    <link rel=\"stylesheet\" href=\"/style.css\">
  </head>
  <body>
    <main class=\"debug-shell\">
      <p>Original copy.</p>
    </main>
    <script src=\"/script.js\"></script>
  </body>
</html>
"""
            }
        },
    }
    complete_proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <title>debug</title>
    <link rel=\"stylesheet\" href=\"/style.css\">
  </head>
  <body>
    <main class=\"debug-shell\">
      <p>Updated copy.</p>
    </main>
    <script src=\"/script.js\"></script>
  </body>
</html>
""",
    }

    result = smoke.validate_patch_proposal_preserves_promotable_excerpt(
        proposal=complete_proposal,
        evidence=evidence,
    )

    assert result.ok



def test_patch_proposal_shape_diagnostic_does_not_complete_incomplete_homepage_fragment() -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke

    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <title>debug</title>
    <link rel=\"stylesheet\" href=\"/style.css\">
  </head>
  <body>
    <main class=\"debug-shell\">
      <p>Original copy.</p>
      <dl>
        <dt>Purpose</dt><dd>golden-path</dd>
        <dt>Managed by</dt><dd>tools/local-platform/debug-website.py</dd>
      </dl>
    </main>
    <script src=\"/script.js\"></script>
  </body>
</html>
"""
            }
        },
    }
    fragment_body = """    <main class=\"debug-shell\">
      <p>Updated copy.</p>
      <dl>
        <dt>Purpose</dt><dd>golden-path</dd>
        <dt>Managed by</dt><dd>tools/local-platform/debug-website.py</dd>"""
    fragment_proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": fragment_body,
    }

    report = smoke.summarize_patch_proposal_body_shape(
        proposal=fragment_proposal,
        evidence=evidence,
    )

    assert not report["ok"]
    assert report["next_step"] == "model_patch_proposal_repair"
    assert report["deterministic_completion_performed"] is False
    assert report["candidate_line_count"] < report["source_line_count"]
    assert fragment_proposal["patched_source"] == fragment_body
    assert "<script src=\"/script.js\"></script>" not in fragment_proposal["patched_source"]


def test_patch_proposal_loop_has_no_deterministic_context_completion_success_path() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    forbidden_completion_rails = [
        "complete_patch_proposal_for_promotable_excerpt",
        "anchored_fragment_context_restoration",
        "grounded_single_line_context_restoration",
        "_complete_single_generated_line_with_grounded_context",
        "_find_source_line_for_completion",
    ]
    for needle in forbidden_completion_rails:
        assert needle not in source

    assert "summarize_patch_proposal_body_shape" in source
    assert "model_patch_proposal_repair" in source
    assert "deterministic_completion_performed" in source
    assert "make_patch_proposal_validation_repair_prompt" in source

def test_blessed_not_ready_reason_surfaces_failed_artifact_gate() -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke

    reason = smoke.blessed_artifact_not_ready_reason(
        {
            "ok": False,
            "artifact_packaging": {
                "ok": False,
                "blocking_reasons": ["replacement file path is unavailable"],
                "issues": [],
            },
            "terminal_result": {
                "failed_gate": "artifact.replacement_files_exist",
            },
        },
        setup_ok=True,
    )

    assert "artifact_packaging" in reason
    assert "replacement file path is unavailable" in reason
    assert "artifact.replacement_files_exist" in reason



def test_golden_path_smoke_can_bundle_blessed_diagnostics(tmp_path) -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke

    output_root = tmp_path / "blessed"
    output_root.mkdir()
    (output_root / "09_blessed_patch_proposal_verification.json").write_text(
        '{"ok":false,"blocking_reasons":["patch proposal unavailable"],"issues":[]}',
        encoding="utf-8",
    )
    (output_root / "10_blessed_full_file_promotion_verification.json").write_text(
        '{"ok":false,"blocking_reasons":["patch proposal unavailable"],"issues":["full-file promotion not run"]}',
        encoding="utf-8",
    )
    (output_root / "12_blessed_generated_editor_final_report.json").write_text(
        '{"ok":false,"selected_target_file":"index.html","terminal_result":{"failed_gate":"artifact.replacement_files_exist"}}',
        encoding="utf-8",
    )
    ai_workspace = output_root / "generated_editor_ai_workspace"
    ai_workspace.mkdir()
    (ai_workspace / "index.html").write_text("<html></html>", encoding="utf-8")

    destination = tmp_path / "diag"
    archive = tmp_path / "diag.zip"
    report = smoke.write_blessed_diagnostic_outputs(
        output_root=output_root,
        destination_dir=destination,
        archive_path=archive,
        include_ai_workspace=False,
        run_context={
            "site_id": "debug-golden-path-test",
            "case_ok": False,
            "blessed_ok": False,
            "blessed_not_ready_reason": "artifact not ready",
            "failed_checks": ["blessed_generated_editor_path_ok"],
        },
        progress=None,
    )

    assert report["file_count"] >= 3
    assert (destination / "diagnostic_manifest.json").is_file()
    assert (destination / "diagnostic_summary.txt").is_file()
    assert (destination / "blessed_output" / "09_blessed_patch_proposal_verification.json").is_file()
    assert not (destination / "blessed_output" / "generated_editor_ai_workspace" / "index.html").exists()
    assert archive.is_file()
    assert report["summary"]["stage_summaries"]["09_blessed_patch_proposal_verification.json"]["blocking_reasons"] == [
        "patch proposal unavailable"
    ]


def test_golden_path_smoke_exposes_power_diagnostic_cli_flags() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert "--diagnostic-dir" in source
    assert "--diagnostic-archive" in source
    assert "--diagnostic-include-ai-workspace" in source
    assert "diagnostic_manifest.json" in source
    assert "diagnostic_summary.txt" in source
    assert "Blessed diagnostics collected" in source

