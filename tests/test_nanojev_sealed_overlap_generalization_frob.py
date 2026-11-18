"""Offline deterministic regression of NanoJev 2x2 generalization; no Qwen/Ollama/network."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as frob


def make_baseline(tmp_path, space, *, n=24):
    axes = [d["id"] for d in space["dimensions"]]
    entries = [{"id": f"v{i+1:04d}",
                "description": f"Contract description {i+1}: {space['dimensions'][i % len(axes)]['definition']}",
                "dimensions": [axes[i % len(axes)]]} for i in range(n)]
    baseline = {"schema": smoke.SCHEMA, "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256": smoke.seal(space),
                "candidate_vocabulary_sha256": smoke.seal(entries),
                "candidate_vocabulary_approved_or_sealed": False,
                "selected_vocabulary_size": n,
                "human_dimensions": axes,
                "calibration": {"train_count": 90, "selection_count": 24,
                                "final_test_count": 36, "seed": 41},
                "model": {"frozen": True, "backbone": smoke.MODEL_ID},
                "measurement": {"ridge": 0.5}, "vocabulary": entries}
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    return path, baseline


def test_four_factorial_cells_isolate_words_and_combinations(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    _, baseline = make_baseline(tmp_path, space)
    words = frob.read_paraphrases(frob.DEFAULT_PARAPHRASES, space)
    train, cells, design = frob.build_factorial(space, baseline, words, seed=104)
    assert len(train) == 90
    assert design["strict_unused_combinations"] == 12
    assert all(len(cells[k]) == 24 for k in frob.CELLS)
    assert design["total_prompts"] == 96
    assert design["known_combination_sizes"] == design["novel_combination_sizes"]
    v2_train, v2_select, v2_test = smoke.calibration_three(space, 90, 24, 36, 41)
    used = {frozenset(r["dimensions"]) for r in v2_train+v2_select+v2_test}
    for name, rows in cells.items():
        for row in rows:
            if name.startswith("novel"):
                assert frozenset(row["dimensions"]) not in used
            else:
                assert frozenset(row["dimensions"]) in {frozenset(r["dimensions"]) for r in train}
        assert [r["dimensions"] for r in rows] == [r["dimensions"] for r in cells[name.replace("paraphrase", "familiar")]]
    assert len(set(row["prompt"] for rows in cells.values() for row in rows)) == 96
    all_train_lexemes = {p.casefold() for d in space["dimensions"] for p in d["phrases"]}
    for row in cells["known_paraphrase"] + cells["novel_paraphrase"]:
        assert not any(p in row["prompt"].casefold() for p in all_train_lexemes)
    assert all(row["group_id"] == cells["known_paraphrase"][i]["group_id"]
               for i, row in enumerate(cells["known_familiar"]))


def test_24_entry_baseline_rejects_tampering_and_wrong_space(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    path, baseline = make_baseline(tmp_path, space)
    assert len(frob.read_baseline(path, space, expected_entries=24)["vocabulary"]) == 24
    with pytest.raises(ValueError, match="SIZE_MISMATCH"):
        frob.read_baseline(path, space, expected_entries=25)
    baseline["vocabulary"][0]["description"] += " modified"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        frob.read_baseline(path, space, expected_entries=24)


def test_wording_and_composition_shift_metrics_use_correct_truths():
    rows = [{"group_id": "a"}, {"group_id": "a"}, {"group_id": "b"}, {"group_id": "b"}]
    base = np.array([[1, 0], [1, 0], [0, 1], [0, 1]], dtype=np.int8)
    novel_truth = np.array([[0, 1], [0, 1], [1, 0], [1, 0]], dtype=np.int8)
    # Composition predictions swap with truth: both cases are correct.
    shift = frob.paired_shift(base, novel_truth, base, novel_truth, rows,
                             seed=3, bootstrap_trials=39)
    assert shift["mean_axis_accuracy_delta_new_minus_reference"] == 0
    # Same predictions would fail on novel labels; tests the truth change.
    failed = frob.paired_shift(base, base, base, novel_truth, rows,
                              seed=3, bootstrap_trials=39)
    assert failed["mean_axis_accuracy_delta_new_minus_reference"] == -1


def test_groupwise_information_preserves_correlated_variants():
    rows = [{"group_id": f"g{k}"} for k in range(12) for _ in range(2)]
    truth = np.array([[k%2, (k//2)%2] for k in range(12) for _ in range(2)], dtype=np.int8)
    result = frob.grouped_info(truth, truth, rows, seed=11, null_trials=99, bootstrap_trials=39)
    assert result["mean_axis_accuracy"] == 1.0
    assert result["macro_information_bits"] > 0.9
    assert result["group_label_permutation_p_value"] < 0.05
    assert result["independent_combination_groups"] == 12
    assert result["exact_semantic_pattern_accuracy"] == 1.0


def _offline_encoder(space):
    words = frob.read_paraphrases(frob.DEFAULT_PARAPHRASES, space)
    patterns = [[*d["phrases"], *words[d["id"]], d["definition"]] for d in space["dimensions"]]
    def encode(texts):
        result = np.zeros((len(texts), len(patterns)+1), dtype=float)
        for i, line in enumerate(texts):
            for j, phrases in enumerate(patterns):
                result[i, j] = 1.0 if any(p in line for p in phrases) else 0.0
            result[i, -1] = 0.01
        return result
    return encode


def test_full_offline_run_uses_fixed_report_only_and_writes_report(tmp_path, monkeypatch):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    input_path, baseline = make_baseline(tmp_path, space)
    def forbid(*args, **kwargs):
        raise AssertionError("Ollama must never be invoked during this test")
    monkeypatch.setattr(smoke, "ollama_propose", forbid)
    output = tmp_path / "report.json"
    args = frob.parser().parse_args(["--baseline-report", str(input_path), "--output", str(output),
                                      "--null-trials", "19", "--bootstrap-trials", "39"])
    report = frob.run(args, encoder=_offline_encoder(space))
    assert report["vocabulary_unchanged_no_ollama_calls"] is True
    assert report["vocabulary_sha256"] == baseline["candidate_vocabulary_sha256"]
    assert report["fixed_vocabulary_size"] == 24
    assert report["acceptance"]["never_authorizes_contract_seal"] is True
    assert report["status"].startswith("COMPLETE_FACTORIAL")
    assert list(report["cells"]) == list(frob.CELLS)
    assert report["cells"]["novel_paraphrase"]["direct_backbone"]["mean_axis_accuracy"] > 0.8
    assert report["cells"]["known_familiar"]["fixed_vocabulary"]["sample_count"] == 24
    assert len(report["cells"]["novel_paraphrase"]["per_case"]) == 24
    assert json.loads(output.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        frob.run(args, encoder=_offline_encoder(space))


def test_shuffled_labels_dont_create_information():
    rng = np.random.default_rng(17)
    rows = [{"group_id": f"g{k}"} for k in range(20) for _ in range(2)]
    y = rng.integers(0, 2, size=(20, 3)).repeat(2, axis=0).astype(np.int8)
    guesses = np.roll(y, 12, axis=0)
    r = frob.grouped_info(y, guesses, rows, seed=2, null_trials=99, bootstrap_trials=29)
    assert r["macro_information_bits"] < 0.20


def test_unseen_paraphrase_validation_rejects_training_phrase(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    obj = json.loads(frob.DEFAULT_PARAPHRASES.read_text(encoding="utf-8"))
    obj["paraphrases"]["authority"][0] = space["dimensions"][0]["phrases"][0]
    p = tmp_path / "words.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="REPEATED_WITH_TRAIN"):
        frob.read_paraphrases(p, space)


def test_synthetic_paraphrase_breakage_is_observable(tmp_path):
    """A decoder blind to all new phrases must show a measurable wording loss."""
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    base_path, _ = make_baseline(tmp_path, space)
    original = _offline_encoder(space)
    novel_phrases = [p for d in frob.read_paraphrases(frob.DEFAULT_PARAPHRASES, space).values() for p in d]

    def broken(texts):
        vecs = original(texts)
        for i, text in enumerate(texts):
            if any(p in text for p in novel_phrases):
                vecs[i, :-1] = 0.0  # fails to recognize previously unseen wording
        return vecs

    args = frob.parser().parse_args(["--baseline-report", str(base_path),
                                      "--output", str(tmp_path / "failed-words.json"),
                                      "--null-trials", "19", "--bootstrap-trials", "49"])
    report = frob.run(args, encoder=broken)
    assert report["contrasts"]["vocabulary"]["paraphrase_effect_known"]["mean_axis_accuracy_delta_new_minus_reference"] < -0.10
    assert report["cells"]["known_paraphrase"]["fixed_vocabulary"]["mean_axis_accuracy"] < report["cells"]["known_familiar"]["fixed_vocabulary"]["mean_axis_accuracy"]


def test_synthetic_new_combination_breakage_is_observable(tmp_path):
    """A decoder blind to unseen compositions must show a composition loss."""
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    base_path, baseline = make_baseline(tmp_path, space)
    original = _offline_encoder(space)
    train, _, _ = smoke.calibration_three(space, 90, 24, 36, 41)
    known = {frozenset(r["dimensions"]) for r in train}
    axes = [d["id"] for d in space["dimensions"]]

    def broken(texts):
        vecs = original(texts)
        for i, text in enumerate(texts):
            if text.startswith(("Assess these contract conditions:", "In this contract scenario,")):
                active = frozenset(axes[j] for j, v in enumerate(vecs[i, :-1]) if v > 0.5)
                if active not in known:
                    vecs[i, :-1] = 0.0
        return vecs

    args = frob.parser().parse_args(["--baseline-report", str(base_path),
                                      "--output", str(tmp_path / "failed-combos.json"),
                                      "--null-trials", "19", "--bootstrap-trials", "49"])
    report = frob.run(args, encoder=broken)
    assert report["contrasts"]["vocabulary"]["composition_effect_familiar"]["mean_axis_accuracy_delta_new_minus_reference"] < -0.10
    assert report["cells"]["novel_familiar"]["fixed_vocabulary"]["mean_axis_accuracy"] < report["cells"]["known_familiar"]["fixed_vocabulary"]["mean_axis_accuracy"]
