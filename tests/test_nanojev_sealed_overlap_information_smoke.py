"""Offline deterministic tests: no Ollama requests, Qwen weights, or production mutations."""
from __future__ import annotations

import io
import json
import math

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke


def test_three_part_calibration_disjoint_and_sealed():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    a, b, c = smoke.calibration_three(space, 90, 24, 36, 41)
    assert [len(x) for x in (a, b, c)] == [90, 24, 36]
    keys = [[tuple(r["dimensions"]) for r in rows] for rows in (a, b, c)]
    assert len(set(keys[0] + keys[1] + keys[2])) == 150
    assert len(set(r["prompt"] for r in a + b + c)) == 150
    assert len(smoke.seal(space)) == 64
    with pytest.raises(ValueError, match="MAX_"):
        smoke.calibration_three(space, 90, 50, 36, 41)


def test_proposals_are_bounded_to_human_dimensions_and_duplicate_safe():
    raw = json.dumps({"entries": [
        {"description": "Permission to expend a scarce resource", "dimensions": ["authority", "resource"]},
        {"description": "Permission to expend a scarce resource", "dimensions": ["authority"]},
        {"description": "Illicit extension of the vocabulary", "dimensions": ["unauthorized"]},
        {"description": "A binding and temporally bounded commitment", "dimensions": ["obligation", "time"]},
    ]})
    allowed = {"authority", "resource", "obligation", "time"}
    results = smoke.parse_proposals(raw, allowed, [], 5)
    assert [e["id"] for e in results] == ["v0001", "v0002"]
    smoke.check_proposer_entries(results, [], allowed, 5)
    assert smoke.parse_proposals(raw, allowed, results, 5) == []
    with pytest.raises(ValueError, match="NON_JSON"):
        smoke.parse_proposals("not JSON", allowed, [], 5)
    with pytest.raises(ValueError, match="UNAUTHORIZED_DIMENSION"):
        smoke.check_proposer_entries([{"id": "v0001", "description": "Unauthorized dimension for a proposed word", "dimensions": ["new_axis"]}], [], allowed, 1)


def test_effective_rank_cka_and_discrete_information():
    independent = smoke.effective_rank(np.eye(6))
    assert math.isclose(independent["effective_rank"], 5.0, abs_tol=1e-8)
    assert smoke.effective_rank(np.ones((6, 8)))["effective_rank"] == 0.0
    assert 0 <= smoke.linear_cka(np.eye(6), np.eye(6)) <= 1.00001
    y = np.array([0, 0, 1, 1] * 50)
    assert math.isclose(smoke.binary_information_bits(y, y), 1.0, abs_tol=1e-8)
    assert math.isclose(smoke.binary_information_bits(y, np.array([0, 1, 0, 1] * 50)), 0.0, abs_tol=1e-8)


def test_independent_overlap_preserves_coactivations_instead_of_competition():
    # Two descriptions apply simultaneously: unlike a softmax, they do not
    # divide a fixed sum of probability mass.
    entries = [{"id": "v0001", "dimensions": ["authority"]},
               {"id": "v0002", "dimensions": ["resource"]}]
    train = [{"dimensions": ["authority", "resource"]},
             {"dimensions": ["authority"]},
             {"dimensions": ["resource"]},
             {"dimensions": []}] * 5
    train_vec = np.array([[1., 1.], [1., 0.], [0., 1.], [0., 0.]] * 5)
    entry_vec = np.eye(2)
    thresholds, support = smoke.train_overlap_thresholds(train_vec, entry_vec, train, entries)
    assert support.tolist() == [10, 10]
    codes = smoke.overlapping_codes(train_vec, entry_vec, thresholds)
    assert codes[0].tolist() == [1.0, 1.0]
    assert codes[1].tolist() == [1.0, 0.0]
    assert codes[2].tolist() == [0.0, 1.0]
    assert codes[3].tolist() == [0.0, 0.0]
    diag = smoke.activation_diagnostics(codes)
    assert diag["fraction_prompts_with_multiple_active_entries"] == 0.25
    assert diag["unique_activation_patterns"] == 4
    assert math.isclose(diag["empirical_pattern_entropy_bits"], 2.0)


def test_unseen_entry_conjunction_calibrated_to_never_activate():
    train = [{"dimensions": ["authority"]}, {"dimensions": ["resource"]}] * 10
    train_vec = np.array([[1., 0.], [0., 1.]] * 10)
    entry_vec = np.array([[1., 1.]])
    e = [{"dimensions": ["authority", "resource"]}]
    thresholds, support = smoke.train_overlap_thresholds(train_vec, entry_vec, train, e)
    assert support.tolist() == [0]
    assert smoke.overlapping_codes(train_vec, entry_vec, thresholds).sum() == 0


def test_fixed_readout_recovers_known_axis_info_and_null():
    rng = np.random.default_rng(2)
    x_train = rng.integers(0, 2, (120, 4)).astype(float)
    x_test = rng.integers(0, 2, (100, 4)).astype(float)
    train_rows = [{"dimensions": [f"d{j}" for j in range(4) if x[j]]} for x in x_train]
    test_rows = [{"dimensions": [f"d{j}" for j in range(4) if x[j]]} for x in x_test]
    metric, predicted = smoke.evaluate_representation(train_rows, test_rows, x_train, x_test,
                                                      [f"d{i}" for i in range(4)], ridge=0.1,
                                                      seed=7, null_trials=99, bootstrap_trials=99)
    assert (predicted == x_test).all()
    assert metric["macro_information_bits"] > 0.9
    assert metric["macro_permutation_p_value"] <= 0.05
    assert len(metric["bootstrap_macro_information_95pct_interval_bits"]) == 2
    shuffled = smoke.information_assessment(smoke.dimension_labels(test_rows, [f"d{i}" for i in range(4)]),
                                            predicted[rng.permutation(len(predicted))], seed=9, null_trials=99)
    assert shuffled["macro_information_bits"] < metric["macro_information_bits"]


def test_ollama_contract_uses_fixed_json_and_proposed_conjunction(monkeypatch):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    captured = {}

    class Reply:
        def __enter__(self):
            return io.BytesIO(json.dumps({"message": {"content": json.dumps({"entries": [
                {"description": "An authorized use of limited resources", "dimensions": ["authority", "resource"]}
            ]})}}).encode())
        def __exit__(self, *_):
            return False

    def fake_open(request, timeout):
        captured.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
        return Reply()

    monkeypatch.setattr(smoke, "urlopen", fake_open)
    e = smoke.ollama_propose(space, [], 1, model="fake", base_url="http://localhost:11434", timeout=9)
    assert e[0]["dimensions"] == ["authority", "resource"]
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["format"] == "json"
    assert captured["body"]["stream"] is False
    assert "conjunction" in captured["body"]["messages"][0]["content"]


def _synthetic_pair(space, calls):
    dims = space["dimensions"]
    ids = [d["id"] for d in dims]

    def encoder(texts):
        calls.append(list(texts))
        rows = []
        for text in texts:
            row = np.zeros(len(ids) + 1, dtype=float)
            for i, dim in enumerate(dims):
                if any(phrase in text for phrase in dim["phrases"]) or dim["definition"] in text:
                    row[i] = 1.0
            row[-1] = 0.05
            rows.append(row)
        return np.array(rows)

    proposals = []
    def proposer(_, existing, count):
        proposals.append(len(existing))
        start = len(existing)
        return [{"id": f"v{start + k + 1:04d}",
                 "description": f"A precise contract semantic {start+k+1}: {dims[(start + k) % len(ids)]['definition']}",
                 "dimensions": [ids[(start + k) % len(ids)]]} for k in range(count)]
    return encoder, proposer, proposals


def test_smoke_selection_isolated_from_final_test_and_never_seals(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    test_phrases = {phrase for dim in space["dimensions"] for phrase in dim["phrases"][2:]}
    calls = []
    encoder, proposer, proposals = _synthetic_pair(space, calls)
    args = smoke.parser().parse_args(["--output", str(tmp_path / "v2.json"),
                                      "--train-count", "72", "--selection-count", "24",
                                      "--test-count", "24", "--rounds", "3",
                                      "--batch-entries", "4", "--max-entries", "12",
                                      "--null-trials", "19", "--bootstrap-trials", "19"])
    result = smoke.run(args, proposer=proposer, encoder=encoder)
    assert result["schema"].endswith(".v2")
    assert result["candidate_vocabulary_approved_or_sealed"] is False
    assert result["acceptance"]["never_authorizes_contract_seal"]
    assert result["calibration"]["final_test_used_for_selection"] is False
    assert len(result["rounds"]) == 3
    assert len(proposals) == 3
    assert len(result["vocabulary"]) == result["selected_vocabulary_size"]
    assert result["final_evaluation"]["vocabulary_information"]["sample_count"] == 24
    assert result["final_evaluation"]["direct_frozen_backbone"]["sample_count"] == 24
    # The ONLY request containing a test-only phrase arrives AFTER all
    # proposals were made. No metric from final test influenced the winner.
    assert all(not any(any(p in t for p in test_phrases) for t in call) for call in calls[:-1])
    assert any(any(p in t for p in test_phrases) for t in calls[-1])
    assert json.loads((tmp_path / "v2.json").read_text()) == result
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        smoke.run(args, proposer=proposer, encoder=encoder)


def test_final_test_shift_does_not_change_vocabulary_choice(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    def make_encoder(shift):
        calls = []
        encoder, proposer, proposed = _synthetic_pair(space, calls)
        def wrapped(texts):
            output = encoder(texts)
            # Changing ONLY TEST encodings must never change selection rounds.
            if any(any(p in text for d in space["dimensions"] for p in d["phrases"][2:]) for text in texts):
                output = output + shift * np.arange(output.shape[1], dtype=float)
            return output
        return wrapped, proposer
    one, prop1 = make_encoder(0.0)
    two, prop2 = make_encoder(20.0)
    base = ["--train-count", "72", "--selection-count", "20", "--test-count", "24",
            "--rounds", "3", "--batch-entries", "4", "--max-entries", "12",
            "--null-trials", "19", "--bootstrap-trials", "0"]
    a = smoke.run(smoke.parser().parse_args(["--output", str(tmp_path / "a.json"), *base]),
                  encoder=one, proposer=prop1)
    b = smoke.run(smoke.parser().parse_args(["--output", str(tmp_path / "b.json"), *base]),
                  encoder=two, proposer=prop2)
    assert a["rounds"] == b["rounds"]
    assert a["selected_vocabulary_size"] == b["selected_vocabulary_size"]
    assert a["vocabulary"] == b["vocabulary"]
    assert a["candidate_vocabulary_sha256"] == b["candidate_vocabulary_sha256"]


def test_continuous_overlap_is_independent_and_train_calibrated():
    train = np.array([[1., 1.], [1., 0.], [0., 1.], [0., 0.]] * 5)
    entries = np.eye(2)
    rows = [{"dimensions": ["authority", "resource"]},
            {"dimensions": ["authority"]}, {"dimensions": ["resource"]},
            {"dimensions": []}] * 5
    proposed = [{"dimensions": ["authority"]}, {"dimensions": ["resource"]}]
    thresholds, support = smoke.train_overlap_thresholds(train, entries, rows, proposed)
    scales = smoke.train_overlap_scales(train, entries)
    evidence = smoke.independent_overlap_evidence(train, entries, thresholds, scales, support)
    assert np.all(evidence >= 0) and np.all(evidence <= 1)
    assert np.all(evidence[0] > 0.5)
    assert evidence[0].sum() > 1.0  # no normalization across entries
    assert evidence[1, 0] > evidence[1, 1]
    suppressed = smoke.independent_overlap_evidence(train, entries, thresholds, scales, np.array([0, 10]))
    assert np.all(suppressed[:, 0] == 0.)
    assert smoke.evidence_diagnostics(evidence)["mean_total_evidence_per_prompt_not_normalized"] > 0.
