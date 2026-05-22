from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

FIXTURE_WINDOWS_REPO = PureWindowsPath("C:/main-computer-fixtures/main_computer_test")

from main_computer.recurrent_thinking import (
    main_computer_default_roots,
    scan_recurrent_thinking,
    visible_artifact_text,
)


def test_main_computer_default_roots_include_visible_ai_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Main Computer\n", encoding="utf-8")
    (repo / "TODO.md").write_text("remember viewport diagnostics\n", encoding="utf-8")
    (repo / "aider.log").write_text("visible aider action\n", encoding="utf-8")
    (repo / "aider_web_context").mkdir()
    (repo / "debug_assets").mkdir()
    (repo / "harness_output_20260502").mkdir()
    (repo / "node_modules").mkdir()

    roots = [path.relative_to(repo).as_posix() for path in main_computer_default_roots(repo)]

    assert "README.md" in roots
    assert "TODO.md" in roots
    assert "aider.log" in roots
    assert "aider_web_context" in roots
    assert "debug_assets" in roots
    assert "harness_output_20260502" in roots
    assert "node_modules" not in roots


def test_visible_artifact_text_flattens_aider_json_without_path_noise(tmp_path: Path) -> None:
    payload = {
        "id": "abc123",
        "repo_dir": str(FIXTURE_WINDOWS_REPO),
        "entries": [
            {
                "instruction": "Keep the widget viewport state synchronized before rendering application panels.",
                "result_excerpt": "The widget viewport state sync helper was reused in the application workspace.",
            }
        ],
    }

    flattened = visible_artifact_text(tmp_path / "aider_web_context" / "active.json", json.dumps(payload))
    log_flattened = visible_artifact_text(tmp_path / "aider.log", json.dumps(payload) + "\n")

    assert "widget viewport state synchronized" in flattened
    assert "sync helper was reused" in flattened
    assert str(FIXTURE_WINDOWS_REPO) not in flattened
    assert "widget viewport state synchronized" in log_flattened
    assert str(FIXTURE_WINDOWS_REPO) not in log_flattened


def test_scan_recurrent_thinking_finds_repeated_visible_project_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    debug_assets = repo / "debug_assets"
    aider_web_context = repo / "aider_web_context"
    debug_assets.mkdir()
    aider_web_context.mkdir()

    (repo / "README.md").write_text(
        "The viewport diagnostics should keep compact ticker mode separate from fullscreen projection mode.\n",
        encoding="utf-8",
    )
    (debug_assets / "note.md").write_text(
        "Remember: compact ticker mode and fullscreen projection mode are separate widget surfaces.\n",
        encoding="utf-8",
    )
    (aider_web_context / "active.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "instruction": "Verify compact ticker mode before changing fullscreen projection mode.",
                        "result_excerpt": "Compact ticker mode remained stable while fullscreen projection mode changed.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = scan_recurrent_thinking(repo_dir=repo, min_files=2, min_occurrences=2, top=10)
    concepts = [idea.concept for idea in result.ideas]

    assert result.scanned_files >= 3
    assert any("compact ticker mode" in concept or "fullscreen projection mode" in concept for concept in concepts)
    assert any("debug-asset" in idea.artifact_kinds or "aider-web-context" in idea.artifact_kinds for idea in result.ideas)
