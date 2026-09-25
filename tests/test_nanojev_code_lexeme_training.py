from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(text.encode("utf-8"))


def test_python_lexer_recognizes_multichar_lexemes():
    data = load("lexeme_data_test_py", TOOLS / "nanojev_code_lexeme_data.py")
    tokens = data.lex_python('if config is None:\n    return value ** 2\n')
    values = [t.text for t in tokens]
    assert "config" in values
    assert "return" in values
    assert "**" in values
    assert any(t.text == "None" for t in tokens)


def test_javascript_lexer_recognizes_multichar_lexemes():
    data = load("lexeme_data_test_js", TOOLS / "nanojev_code_lexeme_data.py")
    tokens = data.lex_javascript('const value = obj?.field ?? /a+/gi;')
    values = [t.text for t in tokens]
    assert "const" in values
    assert "value" in values
    assert "?." in values
    assert "??" in values
    assert "/a+/gi" in values


def test_paired_records_are_balanced_and_share_prefix_family(tmp_path):
    data = load("lexeme_data_test_pair", TOOLS / "nanojev_code_lexeme_data.py")
    text = "def add(left, right):\n    return left + right\n"
    path = tmp_path / "sample.py"
    path.write_text(text, encoding="utf-8")
    doc = data.SourceDoc(path, "sample.py", "python", text, tuple(data.lex_python(text)))
    pool = data.build_super_suffix_pool([doc])
    assert all(key.startswith("python:") for key in pool)
    records = data.sample_paired_records(
        docs=[doc], tokenizer=FakeTokenizer(), split="train", pair_count=5,
        max_prefix_tokens=200, seed=7, pools=pool,
    )
    assert len(records) == 10
    assert sum(r["gold"]["suffix_matches"] is True for r in records) == 5
    assert sum(r["gold"]["suffix_matches"] is False for r in records) == 5
    by_family = {}
    for row in records:
        by_family.setdefault(row["family_id"], []).append(row)
    assert all(len(rows) == 2 for rows in by_family.values())
    for rows in by_family.values():
        actual = {r["metadata"]["actual_lexeme"] for r in rows}
        assert len(actual) == 1
        false_row = next(r for r in rows if not r["gold"]["suffix_matches"])
        assert false_row["metadata"]["proposed_lexeme"] != false_row["metadata"]["actual_lexeme"]


def test_auc_helper():
    train = load("lexeme_train_test", TOOLS / "nanojev_code_train.py")
    assert train.binary_auc([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) == 1.0


def test_discriminator_checkpoint_selection_prioritizes_auc_then_separation_then_nll():
    train = load("lexeme_train_selection_test", TOOLS / "nanojev_code_train.py")
    cycle1 = train.discriminator_selection(cycle=1, auc=0.60, probability_separation=0.010, mean_nll=0.69)
    cycle2 = train.discriminator_selection(cycle=2, auc=0.61, probability_separation=0.001, mean_nll=1.20)
    cycle3 = train.discriminator_selection(cycle=3, auc=0.61, probability_separation=0.002, mean_nll=1.50)
    cycle4 = train.discriminator_selection(cycle=4, auc=0.61, probability_separation=0.002, mean_nll=0.70)
    assert train.is_better_selection(cycle2, cycle1)
    assert train.is_better_selection(cycle3, cycle2)
    assert train.is_better_selection(cycle4, cycle3)


def test_garbage_collect_preserves_best_cycle(tmp_path):
    train = load("lexeme_train_gc_test", TOOLS / "nanojev_code_train.py")
    generations = tmp_path / "checkpoints" / "generations"
    for cycle in range(1, 6):
        (generations / f"cycle-{cycle:06d}").mkdir(parents=True)
    train.garbage_collect(tmp_path, 2, protected_cycles={2})
    remaining = sorted(p.name for p in generations.iterdir() if p.is_dir())
    assert remaining == ["cycle-000002", "cycle-000004", "cycle-000005"]


def test_best_selection_from_history_prefers_auc(tmp_path):
    train = load("lexeme_train_history_test", TOOLS / "nanojev_code_train.py")
    (tmp_path / "baseline_dev.json").write_text(
        '{"auc":0.512,"probability_separation":0.0005,"mean_nll":0.719}\n', encoding="utf-8"
    )
    rows = [
        {"cycle": 1, "dev_auc": 0.549, "dev_probability_separation": 0.0014, "dev_mean_nll": 0.6918},
        {"cycle": 2, "dev_auc": 0.579, "dev_probability_separation": 0.0019, "dev_mean_nll": 0.7059},
        {"cycle": 3, "dev_auc": 0.612, "dev_probability_separation": 0.0032, "dev_mean_nll": 0.6928},
        {"cycle": 4, "dev_auc": 0.630, "dev_probability_separation": 0.0041, "dev_mean_nll": 0.7064},
    ]
    (tmp_path / "history.jsonl").write_text(
        "".join(__import__("json").dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    best = train.best_selection_from_history(tmp_path)
    assert best["cycle"] == 4
    assert best["dev_auc"] == 0.630


def test_training_pair_builder_requires_exact_false_true_family():
    train = load("lexeme_train_pairs_test", TOOLS / "nanojev_code_train.py")
    examples = [
        {"id": "x-false", "family_id": "x", "candidate_ids": ["false", "true"], "gold_index": 0},
        {"id": "x-true", "family_id": "x", "candidate_ids": ["false", "true"], "gold_index": 1},
        {"id": "y-true", "family_id": "y", "candidate_ids": ["false", "true"], "gold_index": 1},
        {"id": "y-false", "family_id": "y", "candidate_ids": ["false", "true"], "gold_index": 0},
    ]
    pairs = train.build_training_pairs(examples)
    assert len(pairs) == 2
    assert all(pair[0]["gold_index"] == 0 and pair[1]["gold_index"] == 1 for pair in pairs)


def test_best_available_generation_selection_uses_retained_preferred(tmp_path):
    train = load("lexeme_train_phase2_origin_test", TOOLS / "nanojev_code_train.py")
    generations = tmp_path / "checkpoints" / "generations"
    selected = generations / "cycle-000014"
    selected.mkdir(parents=True)
    preferred = train.discriminator_selection(
        cycle=14, auc=0.6513671875, probability_separation=0.01258772099, mean_nll=0.6822195
    )
    resolved, generation = train.best_available_generation_selection(tmp_path, preferred)
    assert resolved["cycle"] == 14
    assert generation == selected


def test_paired_ranking_metrics_measure_same_family_ordering():
    train = load("lexeme_train_dev_pair_metrics_test", TOOLS / "nanojev_code_train.py")
    # family a wins and satisfies the 0.10 margin; b wins but misses margin; c loses.
    metrics = train.paired_ranking_metrics(
        {
            "a": {0: -0.20, 1: 0.05},   # gap +0.25
            "b": {0: 0.10, 1: 0.15},    # gap +0.05
            "c": {0: 0.30, 1: 0.20},    # gap -0.10
        },
        0.10,
    )
    assert metrics["pair_count"] == 3
    assert metrics["pair_win_rate"] == 2 / 3
    assert abs(metrics["mean_pair_logodds_gap"] - (0.20 / 3)) < 1e-12
    assert metrics["pair_margin_satisfied_rate"] == 1 / 3


def test_paired_ranking_metrics_reject_incomplete_family():
    train = load("lexeme_train_dev_pair_incomplete_test", TOOLS / "nanojev_code_train.py")
    try:
        train.paired_ranking_metrics({"x": {1: 0.4}}, 0.10)
    except RuntimeError as exc:
        assert "exact FALSE/TRUE pair" in str(exc)
    else:
        raise AssertionError("incomplete paired dev family must fail closed")


def test_rolling_dev_keeps_exactly_half_of_canonical_pairs_deterministically():
    train = load("lexeme_train_rolling_half_test", TOOLS / "nanojev_code_train.py")
    canonical = [
        (
            {"id": f"p{i}-false", "family_id": f"p{i}", "candidate_ids": ["false", "true"], "gold_index": 0},
            {"id": f"p{i}-true", "family_id": f"p{i}", "candidate_ids": ["false", "true"], "gold_index": 1},
        )
        for i in range(64)
    ]
    before = [pair[0]["family_id"] for pair in canonical]
    a = train.select_canonical_half(canonical, seed=1234)
    b = train.select_canonical_half(canonical, seed=1234)
    c = train.select_canonical_half(canonical, seed=5678)
    assert len(a) == 32
    assert [pair[0]["family_id"] for pair in a] == [pair[0]["family_id"] for pair in b]
    assert [pair[0]["family_id"] for pair in a] != [pair[0]["family_id"] for pair in c]
    assert [pair[0]["family_id"] for pair in canonical] == before


def test_rolling_dev_fresh_source_selection_excludes_canonical_probe_files():
    train = load("lexeme_train_rolling_sources_test", TOOLS / "nanojev_code_train.py")
    manifest = [{"relative": f"src/f{i}.py", "language": "python"} for i in range(100)]
    excluded = {f"src/f{i}.py" for i in range(40)}
    rows = train.select_fresh_dev_rows(manifest, excluded, count=40, seed=9)
    assert len(rows) == 40
    assert all(row["relative"] not in excluded for row in rows)
    assert rows == train.select_fresh_dev_rows(manifest, excluded, count=40, seed=9)
