from __future__ import annotations

import json
from pathlib import Path

from main_computer import rag_text_console_clob_v2_smoke as smoke


def _write_fake_repo(root: Path, *, file_count: int = 80) -> None:
    (root / "main_computer" / "web").mkdir(parents=True)
    (root / "main_computer" / "action_specs").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "main_computer" / "text_console.py").write_text("# text console\n", encoding="utf-8")
    (root / "main_computer" / "rag_text_console_clob_v2_smoke.py").write_text("# clob smoke\n", encoding="utf-8")
    (root / "main_computer" / "web" / "text.html").write_text("<script></script>\n", encoding="utf-8")
    (root / "main_computer" / "action_specs" / "terminal.md").write_text("# terminal\n", encoding="utf-8")
    (root / "tests" / "test_text_console_structured_artifacts.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (root / "tests" / "test_text_console_clob_v2_smoke.py").write_text("def test_clob(): pass\n", encoding="utf-8")
    for index in range(file_count):
        subdir = root / "sample_tree" / f"dir_{index // 10}"
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / f"file_{index}.py").write_text(f"# file {index}\n", encoding="utf-8")
    # These should not inflate the first recursive tree clob.
    (root / ".git").mkdir()
    (root / ".git" / "ignored").write_text("ignored\n", encoding="utf-8")
    (root / "runtime").mkdir()
    (root / "runtime" / "ignored.log").write_text("ignored\n", encoding="utf-8")


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
