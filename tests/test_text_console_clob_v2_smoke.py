from __future__ import annotations

import json
from pathlib import Path

from main_computer import rag_text_console_clob_v2_smoke as smoke


def _write_fake_repo(root: Path, *, file_count: int = 80) -> None:
    (root / "main_computer" / "web").mkdir(parents=True)
    (root / "main_computer" / "action_specs").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "main_computer" / "text_console.py").write_text("# text console\n", encoding="utf-8")
    (root / "main_computer" / "rag_text_console_clob_v2_smoke.py").write_text(
        "\n".join(
            [
                "DEFAULT_FILE_CONTENT_LOOKUP_CONTEXT_CHARS = 2200",
                "def build_clob_lookup_context(lookup_result, max_chars=2200):",
                "    return 'retrieved slice from the saved clob payload'",
                "def query_file_content_clob(clob, terms):",
                "    return {'operation': 'file_content_lookup', 'terms': terms}",
                "def run_rag_proof_cases():",
                "    return {'ok': True}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "main_computer" / "web" / "text.html").write_text("<script></script>\n", encoding="utf-8")
    (root / "main_computer" / "action_specs" / "terminal.md").write_text("# terminal\n", encoding="utf-8")
    (root / "tests" / "test_text_console_structured_artifacts.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (root / "tests" / "test_text_console_clob_v2_smoke.py").write_text(
        "\n".join(
            [
                "def test_clob_lookup_context_is_bounded():",
                "    report = {'full_tree_injected': False, 'context_chars': 1000}",
                "    assert report['full_tree_injected'] is False",
                "    assert report['context_chars'] <= 2200",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for index in range(file_count):
        subdir = root / "sample_tree" / f"dir_{index // 10}"
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / f"file_{index}.py").write_text(f"# file {index}\n", encoding="utf-8")
    # These should not inflate the first recursive tree clob.
    (root / ".git").mkdir()
    (root / ".git" / "ignored").write_text("ignored\n", encoding="utf-8")
    (root / "runtime").mkdir()
    (root / "runtime" / "ignored.log").write_text("ignored\n", encoding="utf-8")
    (root / "tools" / "patching" / "reports" / "new_patch_runs" / "example").mkdir(parents=True)
    (
        root
        / "tools"
        / "patching"
        / "reports"
        / "new_patch_runs"
        / "example"
        / "rag_text_console_clob_v2_smoke.py"
    ).write_text("# patch report noise\n", encoding="utf-8")


def test_recursive_repo_tree_clob_is_generated_and_reused(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo)
    clob_dir = Path("diagnostics_output/text_console_clobs")

    first, first_cache = smoke.load_or_create_recursive_repo_tree_clob(
        root=repo,
        clob_dir=clob_dir,
        refresh=False,
    )
    second, second_cache = smoke.load_or_create_recursive_repo_tree_clob(
        root=repo,
        clob_dir=clob_dir,
        refresh=False,
    )

    assert first["clob"]["id"] == second["clob"]["id"]
    assert first_cache["reused"] is False
    assert second_cache["reused"] is True

    cache_path = repo / clob_dir / smoke.DEFAULT_CLOB_FILENAME
    assert cache_path.exists()

    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in saved["payload"]["entries"]}
    assert "main_computer/text_console.py" in paths
    assert "main_computer/rag_text_console_clob_v2_smoke.py" in paths
    assert "tests/test_text_console_clob_v2_smoke.py" in paths
    assert "main_computer/web/text.html" in paths
    assert "runtime/ignored.log" not in paths
    assert ".git/ignored" not in paths
    assert not any(path.startswith("tools/patching/reports/") for path in paths)


def test_clob_reference_context_is_bounded_side_loaded_summary(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=220)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    tree_text = clob["payload"]["tree_text"]
    context = smoke.build_clob_reference_context(
        clob,
        max_chars=3000,
        head_lines=8,
        tail_lines=5,
    )
    report = smoke.clob_context_report(clob, context, max_context_chars=3000)

    assert report["ok"] is True
    assert len(context) <= 3000
    assert tree_text not in context
    assert clob["clob"]["id"] in context
    assert "full clob payload is saved outside the model context" in context.lower()
    assert "side-loaded" in context.lower()



def test_clob_reference_context_enforces_budget_when_summary_samples_are_large(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=30)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    long_paths = [
        "main_computer/" + ("very_long_nested_directory_name/" * 8) + f"text_console_related_file_{index}.py"
        for index in range(120)
    ]
    clob["summary"]["top_level"] = long_paths
    clob["summary"]["top_extensions"] = [{"extension": ".py", "count": 999999, "note": "x" * 80} for _ in range(80)]
    clob["summary"]["text_console_related_sample"] = long_paths
    clob["summary"]["smoke_related_sample"] = long_paths
    clob["summary"]["action_spec_related_sample"] = long_paths
    clob["payload"]["tree_text"] = "\n".join(long_paths * 200)
    clob["clob"]["tree_text_chars"] = len(clob["payload"]["tree_text"])
    clob["clob"]["line_count"] = len(clob["payload"]["tree_text"].splitlines())

    context = smoke.build_clob_reference_context(
        clob,
        max_chars=6000,
        head_lines=40,
        tail_lines=25,
    )
    report = smoke.clob_context_report(clob, context, max_context_chars=6000)

    assert report["ok"] is True
    assert len(context) <= 6000
    assert clob["payload"]["tree_text"] not in context
    assert clob["clob"]["id"] in context
    assert "full clob payload is saved outside the model context" in context.lower()

def test_generic_clob_lookup_uses_full_payload_not_summary(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=40)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    # Prove the lookup does not depend on pre-baked compact summary samples.
    clob["summary"]["text_console_related_sample"] = []
    clob["summary"]["smoke_related_sample"] = []

    lookup = smoke.query_recursive_tree_clob(
        clob,
        terms=["text_console", "clob"],
        kind="file",
        max_results=10,
    )

    paths = [item["path"] for item in lookup["results"]]
    assert lookup["result_count"] >= 2
    assert "main_computer/rag_text_console_clob_v2_smoke.py" in paths
    assert "tests/test_text_console_clob_v2_smoke.py" in paths


def test_clob_lookup_context_is_bounded_side_loaded_slice(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=300)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    lookup = smoke.query_recursive_tree_clob(
        clob,
        terms=["text_console"],
        kind="file",
        max_results=25,
    )
    context = smoke.build_clob_lookup_context(lookup, max_chars=1800)
    report = smoke.clob_lookup_context_report(clob, lookup, context, max_context_chars=1800)

    assert report["ok"] is True
    assert len(context) <= 1800
    assert clob["payload"]["tree_text"] not in context
    assert "retrieved slice from the saved clob payload" in context.lower()
    assert lookup["clob_id"] in context


def test_lookup_turn_uses_compact_reminder_instead_of_full_first_context(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=350)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    full_reference = smoke.build_clob_reference_context(
        clob,
        max_chars=6000,
        head_lines=40,
        tail_lines=25,
    )
    reminder = smoke.build_clob_reminder_context(clob)
    lookup = smoke.query_recursive_tree_clob(
        clob,
        terms=["text_console", "clob"],
        kind="file",
        max_results=25,
    )
    lookup_context = smoke.build_clob_lookup_context(lookup)

    assert len(reminder) < len(full_reference)
    assert clob["payload"]["tree_text"] not in reminder
    messages = smoke.build_lookup_model_messages(
        initial_prompt="Use the clob to orient yourself.",
        initial_response="This is a deliberately long first response. " * 200,
        clob_context=reminder,
        lookup_context=lookup_context,
        lookup_prompt="Use the lookup slice and name one exact path.",
    )
    request = smoke._request_report(messages, model="fake", think=False, last_user_message="Use the lookup slice and name one exact path.")

    assert request["input_chars"] < 6000
    assert "deliberately long first response" in request["request_text"]
    assert request["request_text"].count("deliberately long first response") < 15
    assert clob["payload"]["tree_text"] not in request["request_text"]


def test_offline_clob_v2_smoke_report_uses_saved_clob(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=50)

    report = smoke.run_clob_v2_smoke(
        root=repo,
        clob_dir=Path("diagnostics_output/text_console_clobs"),
        refresh_clob=False,
        prompt="Use the clob to orient yourself.",
        max_clob_context_chars=4000,
        excerpt_head_lines=12,
        excerpt_tail_lines=6,
        base_url="http://127.0.0.1:11434",
        model="fake-model",
        timeout=1.0,
        think=False,
        offline_contract_only=True,
    )

    assert report["ok"] is True
    assert report["offline_contract_only"] is True
    assert report["clob"]["metadata"]["type"] == smoke.CLOB_TYPE_RECURSIVE_REPO_TREE
    assert report["clob_context"]["validation"]["ok"] is True
    assert report["clob_context"]["validation"]["full_tree_injected"] is False
    assert report["model_request"]["last_user_message"] == "Use the clob to orient yourself."
    assert report["clob_lookup"]["validation"]["ok"] is True
    assert report["clob_lookup"]["result_count"] >= 1
    assert report["clob_lookup"]["response_path_usage"]["ok"] is True
    assert report["final_response"]["content"]


def test_refresh_clob_rebuilds_after_repo_changes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=5)
    clob_dir = Path("diagnostics_output/text_console_clobs")

    first, _ = smoke.load_or_create_recursive_repo_tree_clob(
        root=repo,
        clob_dir=clob_dir,
        refresh=False,
    )
    (repo / "new_file_after_cache.py").write_text("# changed\n", encoding="utf-8")
    reused, reused_cache = smoke.load_or_create_recursive_repo_tree_clob(
        root=repo,
        clob_dir=clob_dir,
        refresh=False,
    )
    refreshed, refreshed_cache = smoke.load_or_create_recursive_repo_tree_clob(
        root=repo,
        clob_dir=clob_dir,
        refresh=True,
    )

    assert reused_cache["reused"] is True
    assert reused["clob"]["id"] == first["clob"]["id"]
    assert refreshed_cache["reused"] is False
    assert refreshed["clob"]["id"] != first["clob"]["id"]
    refreshed_paths = {entry["path"] for entry in refreshed["payload"]["entries"]}
    assert "new_file_after_cache.py" in refreshed_paths


def test_blind_clob_reference_hides_runtime_lookup_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=20)

    clob = smoke.generate_recursive_repo_tree_clob(
        repo,
        clob_dir=repo / "diagnostics_output" / "text_console_clobs",
    )
    lookup = smoke.query_recursive_tree_clob(
        clob,
        terms=["text_console", "clob", "smoke"],
        kind="file",
        max_results=25,
    )
    returned_paths = [item["path"] for item in lookup["results"]]
    blind_context = smoke.build_blind_clob_reference_context(clob, max_chars=1800)

    assert lookup["result_count"] >= 1
    assert "exact_path_samples_intentionally_omitted" in blind_context
    assert smoke.paths_present_in_text(returned_paths, blind_context) == []
    assert clob["payload"]["tree_text"] not in blind_context


def test_file_content_clob_lookup_is_generic_bounded_and_reused(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=10)
    clob_dir = Path("diagnostics_output/text_console_clobs")
    target = "main_computer/rag_text_console_clob_v2_smoke.py"

    first, first_cache = smoke.load_or_create_file_content_clob(
        root=repo,
        clob_dir=clob_dir,
        repo_relative_path=target,
        refresh=False,
    )
    second, second_cache = smoke.load_or_create_file_content_clob(
        root=repo,
        clob_dir=clob_dir,
        repo_relative_path=target,
        refresh=False,
    )

    assert first["clob"]["id"] == second["clob"]["id"]
    assert first_cache["reused"] is False
    assert second_cache["reused"] is True

    lookup = smoke.query_file_content_clob(
        first,
        terms=["clob", "lookup", "context"],
        max_chunks=4,
        context_radius=2,
    )
    context = smoke.build_file_content_lookup_context(lookup, max_chars=1600)
    report = smoke.file_content_lookup_context_report(first, lookup, context, max_context_chars=1600)
    usage = smoke.response_mentions_content_evidence(
        "The helper build_clob_lookup_context should be inspected.",
        lookup,
    )

    assert lookup["match_count"] >= 1
    assert report["ok"] is True
    assert len(context) <= 1600
    assert first["payload"]["text"] not in context
    assert usage["ok"] is True


    test_lookup = smoke.query_file_content_clob(
        smoke.load_or_create_file_content_clob(
            root=repo,
            clob_dir=clob_dir,
            repo_relative_path="tests/test_text_console_clob_v2_smoke.py",
            refresh=False,
        )[0],
        terms=["def test_", "assert", "full_tree_injected", "context_chars"],
        max_chunks=5,
        context_radius=4,
    )
    profile_terms = smoke.content_evidence_terms(test_lookup, evidence_profile="test_assertion")
    assert any(term.startswith("test_") for term in profile_terms)
    assert any(term.startswith("assert ") for term in profile_terms)
    assert not any(term == "build_clob_lookup_context" for term in profile_terms)


def test_offline_rag_proof_cases_use_runtime_evidence_without_expected_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_repo(repo, file_count=25)

    report = smoke.run_clob_v2_smoke(
        root=repo,
        clob_dir=Path("diagnostics_output/text_console_clobs"),
        refresh_clob=True,
        prompt="Use the clob to orient yourself.",
        max_clob_context_chars=4000,
        excerpt_head_lines=12,
        excerpt_tail_lines=6,
        base_url="http://127.0.0.1:11434",
        model="fake-model",
        timeout=1.0,
        think=False,
        offline_contract_only=True,
        run_rag_proof=True,
    )

    assert report["ok"] is True
    rag = report["rag_proof"]
    assert rag["ok"] is True
    assert len(rag["cases"]) >= 3
    case_by_name = {case["name"]: case for case in rag["cases"]}
    path_case = case_by_name["blind tree path-RAG"]
    assert path_case["response_usage"]["ok"] is True
    assert path_case["paths_in_blind_context"] == []
    content_case = case_by_name["tree clob to file-content RAG"]
    assert content_case["selected_path"] in [
        item["path"] for item in path_case["lookup_result"]["results"]
    ]
    assert content_case["evidence_usage"]["ok"] is True
    assert content_case["path_usage"]["ok"] is True
    test_case = case_by_name["test-file assertion RAG"]
    assert str(test_case["selected_path"]).startswith("tests/")
    assert test_case["evidence_profile"] == "test_assertion"
    assert test_case["acceptable_evidence_count"] >= 1
    assert test_case["evidence_usage"]["ok"] is True
    matched = test_case["evidence_usage"]["matched_evidence"]
    assert any(str(item).startswith("test_") or str(item).startswith("assert ") for item in matched)
    assert "build_clob_lookup_context" not in matched
