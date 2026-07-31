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

def test_verified_promotion_span_is_exact_contiguous_source(tmp_path) -> None:
    from main_computer import rag_generated_editor_discovery_grounding_smoke as smoke

    source = (
        "<!doctype html>\n"
        "<html>\n"
        "<body>\n"
        "<main>\n"
        "<h1>Old heading</h1>\n"
        "<p>Stable copy.</p>\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    target = tmp_path / "index.html"
    target.write_text(source, encoding="utf-8", newline="\n")
    first = source.index("<h1>")
    second = source.index("<p>")
    selected = {
        "target_file": "index.html",
        "source_sha256": smoke.sha256_text(source),
        "size_bytes": target.stat().st_size,
        "anchors": [
            {"id": "A1", "role": "edit_target", "exact_text": "<h1>Old heading</h1>", "offset": first},
            {"id": "A2", "role": "preservation", "exact_text": "<p>Stable copy.</p>", "offset": second},
        ],
    }

    evidence = smoke.make_verified_evidence_excerpt(
        repo_root=tmp_path,
        task="Update the heading.",
        selected_candidate=selected,
        max_evidence_chars=16000,
        excerpt_window_lines=1,
    )
    target_file, span, metadata = smoke.get_verified_promotion_span(evidence)

    assert target_file == "index.html"
    expected = "<main>\n<h1>Old heading</h1>\n<p>Stable copy.</p>\n</main>\n"
    assert span == expected
    assert metadata["sha256"] == smoke.sha256_text(expected)
    prompt = smoke.make_excerpt_patch_prompt(
        evidence,
        {"mode": "claim_grounding_card", "acceptance_checks": []},
    )
    assert "VERIFIED_PROMOTION_SPAN:" in prompt
    assert expected in prompt
    assert "Do not add a second H1." in prompt


def test_patch_validator_rejects_duplicate_h1_and_added_wrapper(monkeypatch) -> None:
    from main_computer import rag_generated_editor_discovery_grounding_smoke as smoke

    monkeypatch.setattr(
        smoke,
        "validate_grounded_patch_proposal",
        lambda **_kwargs: (smoke.CheckResult(True, [], [], []), "diff"),
    )
    source = "<main>\n<h1>Old heading</h1>\n<p>Stable copy.</p>\n</main>\n"
    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": source,
                "promotion_span": {
                    "start_line": 4,
                    "end_line": 7,
                    "content": source,
                    "sha256": smoke.sha256_text(source),
                },
            }
        },
    }
    proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": (
            "<body>\n<main>\n<h1>New heading</h1>\n"
            "<h1>New heading</h1>\n<p>Stable copy.</p>\n</main>\n</body>\n"
        ),
    }

    result, _diff = smoke.validate_patch_proposal(
        proposal=proposal,
        card={},
        evidence=evidence,
    )

    assert not result.ok
    combined = " ".join([*result.issues, *(result.blocking_reasons or [])])
    assert "duplicate H1" in combined
    assert "additional HTML document wrapper" in combined


def test_promotion_preserves_outside_span_and_surfaces_candidate_failures(
    tmp_path, monkeypatch
) -> None:
    from main_computer import rag_generated_editor_discovery_grounding_smoke as smoke

    monkeypatch.setattr(
        smoke,
        "validate_grounded_patch_proposal",
        lambda **_kwargs: (smoke.CheckResult(True, [], [], []), "diff"),
    )
    source = (
        "<!doctype html>\n"
        "<html>\n"
        "<body>\n"
        "<main>\n"
        "<h1>Old heading</h1>\n"
        "<p>Stable copy.</p>\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    target = tmp_path / "index.html"
    target.write_text(source, encoding="utf-8", newline="\n")
    span = "<main>\n<h1>Old heading</h1>\n<p>Stable copy.</p>\n</main>\n"
    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": span,
                "line_ranges": [{"start_line": 4, "end_line": 7}],
                "full_file_sha256": smoke.file_sha256(target),
                "promotion_span": {
                    "start_line": 4,
                    "end_line": 7,
                    "content": span,
                    "sha256": smoke.sha256_text(span),
                },
            }
        },
    }
    valid = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": "<main>\n<h1>New heading</h1>\n<p>Stable copy.</p>\n</main>\n",
        "grounding_ids_used": ["C1"],
    }
    result, report, _diff = smoke.promote_verified_excerpt_to_full_file(
        repo_root=tmp_path,
        evidence=evidence,
        grounding_card={},
        proposal=valid,
        patch_result=smoke.CheckResult(True, [], [], []),
        output_root=tmp_path / "out",
    )

    assert result.ok
    assert report["outside_promotion_span_unchanged"] is True
    replacement = Path(report["replacement_file"]).read_text(encoding="utf-8")
    assert replacement.startswith("<!doctype html>\n<html>\n<body>\n")
    assert replacement.endswith("</body>\n</html>\n")
    assert replacement.count("<h1") == 1

    duplicate = {
        **valid,
        "patched_source": (
            "<main>\n<h1>New heading</h1>\n<h1>Duplicate</h1>\n"
            "<p>Stable copy.</p>\n</main>\n"
        ),
    }
    failed, failed_report, _failed_diff = smoke.promote_verified_excerpt_to_full_file(
        repo_root=tmp_path,
        evidence=evidence,
        grounding_card={},
        proposal=duplicate,
        patch_result=smoke.CheckResult(True, [], [], []),
        output_root=None,
    )

    assert not failed.ok
    assert failed_report["candidate_failures"]
    assert "structure checks" in failed_report["candidate_failures"][0]["reason"]


def test_patch_repair_prompt_contains_complete_verified_span_contract() -> None:
    from main_computer import rag_debug_website_golden_path_smoke as smoke
    from main_computer import rag_generated_editor_discovery_grounding_smoke as discovery

    span = "<main>\n<h1>Old heading</h1>\n<p>Stable copy.</p>\n</main>\n"
    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": "// excerpt lines 4-7\n" + span,
                "promotion_span": {
                    "start_line": 4,
                    "end_line": 7,
                    "content": span,
                    "sha256": discovery.sha256_text(span),
                },
            }
        },
    }
    prompt = smoke.make_patch_proposal_validation_repair_prompt(
        evidence=evidence,
        grounding_card={"mode": "claim_grounding_card"},
        previous_proposal={"patched_source": "<h1>Fragment</h1>"},
        validation_report={"ok": False},
    )

    assert "VERIFIED_PROMOTION_SPAN:" in prompt
    assert span in prompt
    assert "Do not add a second H1." in prompt
    assert "Do not add a new <!doctype>, <html>, <head>, or <body> wrapper." in prompt
    assert "byte-for-byte" in prompt


def test_golden_path_top_level_report_surfaces_promotion_candidate_failures() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert '"promotion_candidate_failures"' in source
    assert 'full_file_promotion_report.get("candidate_failures")' in source


def test_full_file_promotion_scopes_negative_literal_checks_to_verified_span(tmp_path) -> None:
    from main_computer import rag_generated_editor_discovery_grounding_smoke as smoke

    source = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<title>debug-site</title>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        "<h1>debug-site</h1>\n"
        "<p>Stable copy.</p>\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    span = (
        "<body>\n"
        "<main>\n"
        "<h1>debug-site</h1>\n"
        "<p>Stable copy.</p>\n"
        "</main>\n"
        "</body>\n"
    )
    replacement_span = span.replace(
        "<h1>debug-site</h1>",
        "<h1>Operations Test Workbench</h1>",
    )
    target = tmp_path / "index.html"
    target.write_text(source, encoding="utf-8", newline="\n")
    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": span,
                "line_ranges": [{"start_line": 6, "end_line": 11}],
                "full_file_sha256": smoke.file_sha256(target),
                "promotion_span": {
                    "start_line": 6,
                    "end_line": 11,
                    "content": span,
                    "sha256": smoke.sha256_text(span),
                },
            }
        },
    }
    card = {
        "mode": "claim_grounding_card",
        "target_file": "index.html",
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior",
                "kind": "literal_must_contain",
                "value": "<h1>Operations Test Workbench</h1>",
                "critical": True,
            },
            {
                "id": "P2",
                "intent": "preservation",
                "kind": "literal_must_not_contain",
                "value": "debug-site",
                "critical": True,
            },
        ],
    }
    proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": replacement_span,
        "grounding_ids_used": ["P1", "P2"],
    }

    patch_result, _diff = smoke.validate_patch_proposal(
        proposal=proposal,
        card=card,
        evidence=evidence,
    )
    assert patch_result.ok

    result, report, _promotion_diff = smoke.promote_verified_excerpt_to_full_file(
        repo_root=tmp_path,
        evidence=evidence,
        grounding_card=card,
        proposal=proposal,
        patch_result=patch_result,
        output_root=tmp_path / "out",
    )

    assert result.ok
    assert report["acceptance_check_scope"] == "verified_promotion_span"
    assert report["full_file_validation_scope"] == "structure_and_outside_span_preservation"
    promoted = Path(report["replacement_file"]).read_text(encoding="utf-8")
    assert "<title>debug-site</title>" in promoted
    assert "<h1>debug-site</h1>" not in promoted
    assert promoted.count("<h1") == 1


def test_negative_literal_check_still_blocks_when_old_literal_remains_in_promotion_span(
    tmp_path,
) -> None:
    from main_computer import rag_generated_editor_discovery_grounding_smoke as smoke

    source = (
        "<!doctype html>\n"
        "<html>\n"
        "<body>\n"
        "<main data-site=\"debug-site\">\n"
        "<h1>debug-site</h1>\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )
    span = (
        "<body>\n"
        "<main data-site=\"debug-site\">\n"
        "<h1>debug-site</h1>\n"
        "</main>\n"
        "</body>\n"
    )
    target = tmp_path / "index.html"
    target.write_text(source, encoding="utf-8", newline="\n")
    evidence = {
        "target_file": "index.html",
        "files": {
            "index.html": {
                "content": span,
                "line_ranges": [{"start_line": 3, "end_line": 7}],
                "full_file_sha256": smoke.file_sha256(target),
                "promotion_span": {
                    "start_line": 3,
                    "end_line": 7,
                    "content": span,
                    "sha256": smoke.sha256_text(span),
                },
            }
        },
    }
    card = {
        "mode": "claim_grounding_card",
        "target_file": "index.html",
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior",
                "kind": "literal_must_contain",
                "value": "<h1>Operations Test Workbench</h1>",
                "critical": True,
            },
            {
                "id": "P2",
                "intent": "preservation",
                "kind": "literal_must_not_contain",
                "value": "debug-site",
                "critical": True,
            },
        ],
    }
    proposal = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": "index.html",
        "patched_source": span.replace(
            "<h1>debug-site</h1>",
            "<h1>Operations Test Workbench</h1>",
        ),
        "grounding_ids_used": ["P1", "P2"],
    }

    patch_result, _diff = smoke.validate_patch_proposal(
        proposal=proposal,
        card=card,
        evidence=evidence,
    )
    assert not patch_result.ok
    assert "check 'P2' failed" in " ".join(patch_result.blocking_reasons or [])

    result, report, _promotion_diff = smoke.promote_verified_excerpt_to_full_file(
        repo_root=tmp_path,
        evidence=evidence,
        grounding_card=card,
        proposal=proposal,
        patch_result=patch_result,
        output_root=None,
    )
    assert not result.ok
    assert "edit proposal was not verified" in report["blocking_reasons"]
