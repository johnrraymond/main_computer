from __future__ import annotations

from pathlib import Path
import warnings

from main_computer import rag_hallucination_miner as miner


def make_demo_repo(tmp_path: Path) -> Path:
    package = tmp_path / "main_computer"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "rag_harness.py").write_text(
        "from __future__ import annotations\n\n"
        "def run_rag_harness(prompt: str) -> str:\n"
        "    return prompt\n\n"
        "class RagHarness:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text("Demo RAG harness docs.\n", encoding="utf-8")
    return tmp_path


def test_generate_probes_is_seed_reproducible(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path)

    first = miner.generate_probes(
        root=repo,
        subtree="main_computer",
        count=8,
        seed=777,
        families=("fake_execution_claim", "source_scope_line_number"),
    )
    second = miner.generate_probes(
        root=repo,
        subtree="main_computer",
        count=8,
        seed=777,
        families=("fake_execution_claim", "source_scope_line_number"),
    )

    assert [item["case_id"] for item in first["probes"]] == [item["case_id"] for item in second["probes"]]
    assert [item["question"] for item in first["probes"]] == [item["question"] for item in second["probes"]]
    assert first["probe_count"] == 8


def test_score_log_flags_fake_execution_claim(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path)
    log = miner.generate_probes(
        root=repo,
        subtree="main_computer",
        count=1,
        seed=42,
        families=("fake_execution_claim",),
    )
    case_id = log["probes"][0]["case_id"]

    scored = miner.score_log(log, {case_id: "I ran pytest and all tests passed; the suite is clean."})

    assert scored["summary"]["finding_count"] >= 1
    assert "source_without_command_log::test_execution_claim" in scored["signatures"]


def test_profile_from_scored_log_promotes_critical_rules(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path)
    log = miner.generate_probes(
        root=repo,
        subtree="main_computer",
        count=1,
        seed=43,
        families=("fake_execution_claim",),
    )
    case_id = log["probes"][0]["case_id"]
    scored = miner.score_log(log, {case_id: "pytest passed and the test suite is clean."})
    scored_path = tmp_path / "scored.json"
    miner.write_json(scored_path, scored)

    profile = miner.profile_from_logs([scored_path])

    assert profile["summary"]["rule_count"] >= 1
    assert any(rule["kind"] == "test_execution_claim" for rule in profile["promoted_rules"])



def test_project_map_suppresses_snapshot_syntax_warnings(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path)
    legacy = repo / "main_computer" / "legacy_windows_path.py"
    legacy.write_text(
        '"""Example using .\\main_computer\\legacy in a normal docstring."""\n'
        "def ok() -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SyntaxWarning)
        project_map = miner.build_project_map(repo, "main_computer")

    assert not [item for item in caught if issubclass(item.category, SyntaxWarning)]
    assert "main_computer/legacy_windows_path.py" in project_map["paths"]


def test_load_json_accepts_powershell_utf8_bom(tmp_path: Path) -> None:
    answers_path = tmp_path / "answers.json"
    answers_path.write_text('{"case": "answer"}', encoding="utf-8-sig")

    assert miner.load_json(answers_path) == {"case": "answer"}


def test_golden_path_exercises_scored_log_and_profile(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path)
    result = miner.run_golden_path(
        root=repo,
        subtree="main_computer",
        count=18,
        seed=123,
    )

    assert result["ok"] is True
    assert result["safe_summary"]["finding_count"] == 0
    assert result["hallucinated_summary"]["finding_count"] > 0
    assert result["profile_summary"]["rule_count"] > 0
    assert result["scored_log"]["mode"] == "scored_log"
    assert result["profile"]["schema"] == miner.SCHEMA_PROFILE
