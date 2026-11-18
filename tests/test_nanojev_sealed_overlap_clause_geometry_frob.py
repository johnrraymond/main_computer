"""Offline regression: sentence-level versus full-prompt retrieval, no learned readout."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_clause_geometry_frob as lab
from tools import nanojev_sealed_overlap_geometry_frob as geometry
from tools import nanojev_sealed_overlap_information_smoke as smoke


def make_sources(tmp_path: Path, *, contexts: int = 1):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    entries = [{"id": f"v{i+1:04d}",
                "description": f"Synthetic overlap vocabulary item {i+1:04d} related to {axes[i % 8]}",
                "dimensions": [axes[i % 8]]} for i in range(256)]
    b = tmp_path / "baseline.json"
    baseline = {"schema": smoke.SCHEMA,
                "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256": smoke.seal(space),
                "vocabulary": entries[:24], "selected_vocabulary_size": 24,
                "candidate_vocabulary_sha256": smoke.seal(entries[:24]),
                "candidate_vocabulary_approved_or_sealed": False,
                "human_dimensions": axes,
                "model": {"backbone": smoke.MODEL_ID, "frozen": True},
                "calibration": {"train_count": 90, "selection_count": 24,
                                "final_test_count": 36, "seed": 41},
                "measurement": {"ridge": 0.5}}
    b.write_text(json.dumps(baseline))
    growth = {"schema": lab.training.growth.SCHEMA,
              "acceptance": {"never_authorizes_contract_seal": True},
              "model": {"backbone": smoke.MODEL_ID, "frozen": True},
              "experiment": {"sizes": [24, 36, 48, 72, 96]},
              "all_candidate_vocabulary_unapproved_unsealed": entries[:96]}
    g = tmp_path / "growth.json"
    g.write_text(json.dumps(growth))
    exact = {"schema": lab.training.exact.SCHEMA,
             "status": "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance": {"never_authorizes_contract_seal": True,
                            "never_updates_model_weights": True},
             "model": {"backbone": smoke.MODEL_ID, "frozen": True},
             "experiment": {"sizes": [96, 128, 192, 256]},
             "selection": {"selected_entries": 256},
             "sources": {"baseline_sha256": sha256(b.read_bytes()).hexdigest(),
                         "growth_sha256": sha256(g.read_bytes()).hexdigest(),
                         "human_space_sha256": smoke.seal(space),
                         "retained_vocabulary_96_sha256": smoke.seal(entries[:96])},
             "unapproved_unsealed_candidate_vocabulary": entries,
             "unapproved_vocabulary_sha256": smoke.seal(entries)}
    e = tmp_path / "exact.json"
    e.write_text(json.dumps(exact))
    historical = {"schema": geometry.SCHEMA,
                  "status": "COMPLETE_DECODER_FREE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                  "acceptance": {"never_updates_model_weights": True},
                  "sources": {"baseline_sha256": sha256(b.read_bytes()).hexdigest(),
                              "growth_sha256": sha256(g.read_bytes()).hexdigest(),
                              "exact_sha256": sha256(e.read_bytes()).hexdigest(),
                              "human_space_sha256": smoke.seal(space),
                              "vocabulary_sha256": smoke.seal(entries),
                              "phrases_sha256": sha256(geometry.DEFAULT_PROBES.read_bytes()).hexdigest()},
                  "experiment": {"prompt_count": 8*4*contexts*6,
                                 "contexts_per_axis_depth": contexts,
                                 "depths": list(geometry.DEPTHS),
                                 "representation_stages": list(geometry.STAGES)},
                  "overall": {stage: {"retrieval_accuracy": 0.5} for stage in geometry.STAGES}}
    h = tmp_path / "geometry.json"
    h.write_text(json.dumps(historical))
    return space, b, g, e, h


def fake_encoder(space: dict, phrases: dict):
    dims = [d["id"] for d in space["dimensions"]]
    def encode(texts):
        result = np.zeros((len(texts), 20), dtype=float)
        result[:, 16] = 1.0
        for i, text in enumerate(texts):
            for j, axis in enumerate(dims):
                neg = any(p in text for p in phrases[axis]["negative"])
                pos = any(p in text for p in phrases[axis]["positive"])
                if neg:
                    result[i, j] = -2.
                elif pos or any(p in text for p in space["dimensions"][j]["phrases"]):
                    result[i, j] = 2.
                elif f"to {axis}" in text:
                    result[i, j] = 1.
        return result
    return encode


def test_sentence_split_is_lossless_and_omission_has_no_target_token():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    phrases = geometry.read_phrases(geometry.DEFAULT_PROBES, space)
    rows = geometry.make_cases(axes, phrases, contexts=4)
    unique, indices, counts = lab.prepare_clause_indices(rows)
    assert len(rows) == 768
    assert len(indices) == 768
    assert len(unique) == 33  # 8 axes x (2 positive + 2 negative) plus one neutral
    assert sum(c == 0 for c in counts) == 8 * 4 * 2  # depth=1 only
    for i, row in enumerate(rows):
        split = lab.clauses_from_prompt(row["prompt"])
        assert len(split) == counts[i]
        assert len(indices[i]) == max(1, counts[i])
        if row["depth"] == 1 and row["state"] == "omitted":
            assert split == []
            assert unique[indices[i][0]] == geometry.NEUTRAL
            assert not any(p in row["prompt"] for p in phrases[row["axis"]]["positive"])
            assert not any(p in row["prompt"] for p in phrases[row["axis"]]["negative"])
    assert all(phrase.strip() == phrase for phrase in unique)


def test_segmentation_rejects_unknown_or_ambiguous_input():
    with pytest.raises(ValueError, match="WRAPPER_NOT_RECOGNIZED"):
        lab.clauses_from_prompt("The operation is prohibited.")
    original = geometry.WRAPPERS[0][0] + geometry.NEUTRAL
    with pytest.raises(ValueError, match="INVALID_CLAUSE_BOUNDARY"):
        lab.clauses_from_prompt(original + " The operation is prohibited")
    with pytest.raises(ValueError, match="INVALID_CLAUSE_BOUNDARY"):
        lab.clauses_from_prompt(original + " Clause. .")


def test_count_only_cannot_distinguish_affirmation_from_denial():
    s = lab.cross_variant_score_matrix([np.ones((1,3))]*6, [2,2,1,2,2,1], "count_only")
    assert s[0, 0] == s[0, 1] == 0
    assert s[2, 2] == 0 and s[2, 0] < 0
    m = lab.scored_group(s)
    assert m["retrieval_correct"] < 6
    assert all(value == 0 for value in m["margins"]["affirmed_vs_denied"])
    assert lab.aggregate([m])["affirmed_vs_denied"]["fraction_positive"] == 0


def test_nearest_clause_set_is_semantics_sensitive_and_label_blind():
    # Same cardinality for affirmed/denied; no leakage from the count penalty.
    unit = np.eye(3)
    sets = [unit[[0]],unit[[1]],unit[[2]]] * 2
    counts = [1]*6
    s = lab.cross_variant_score_matrix(sets, counts, "clause_set")
    assert np.allclose(s, np.eye(3))
    m = lab.scored_group(s)
    assert m["retrieval_correct"] == 6
    assert m["denial_and_omission_both_retrieved"]
    for method in ("clause_set", "count_only"):
        assert np.allclose(lab.cross_variant_score_matrix(sets, counts, method),
                           lab.cross_variant_score_matrix(sets[3:]+sets[:3], counts, method))


def test_clause_mean_and_set_do_not_need_target_axis_and_detect_invalid_inputs():
    x = np.eye(4)
    pooled = lab.clause_mean(x, [[1,2], [2,1], [0]])
    assert pooled.shape == (3,4)
    assert np.allclose(pooled[0], pooled[1])
    with pytest.raises(ValueError, match="INVALID_ROW_INDICES"):
        lab.clause_mean(x, [[]])
    with pytest.raises(ValueError, match="INVALID_CLAUSE_SETS"):
        lab.clause_set_score(x, x, -1, 1)
    with pytest.raises(ValueError, match="SCORE_MATRIX_MUST_BE"):
        lab.scored_group(np.zeros((3,2)))


def test_isolated_clause_polarity_detects_affirmation_denial_separation():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    phrases = geometry.read_phrases(geometry.DEFAULT_PROBES,space)
    axes = [d["id"] for d in space["dimensions"]]
    unique = []
    for axis in axes:
        unique.extend(phrases[axis]["positive"])
        unique.extend(phrases[axis]["negative"])
    encoder = fake_encoder(space, phrases)
    metrics = lab.isolated_polarity(encoder(unique), unique, phrases, axes)
    assert metrics["macro_polarity_retrieval_accuracy"] == 1
    assert all(v["polarity_displacement_cosine"] > 0.99 for v in metrics["by_axis"].values())
    # Deliberately collapse polarity to show the test cannot pass on shared presence alone.
    collapsed = encoder(unique)
    collapsed = abs(collapsed)
    wrong = lab.isolated_polarity(collapsed, unique, phrases, axes)
    assert wrong["macro_polarity_retrieval_accuracy"] < 1


def test_paired_bootstrap_uses_same_cases_and_refuses_mismatched_lengths():
    good = lab.scored_group(np.eye(3))
    bad = lab.scored_group(np.ones((3,3)))
    out = lab.paired_differences([good]*8, [bad]*8, seed=4, trials=20)
    assert out["retrieval_accuracy"]["delta"] > 0
    assert out["affirmation_denial_margin"]["delta"] > 0
    with pytest.raises(ValueError, match="UNPAIRED_COMPARISON"):
        lab.paired_differences([good], [bad,bad], seed=4, trials=20)


def test_end_to_end_uses_frozen_source_and_rejects_stale_geometry(tmp_path,monkeypatch):
    space, b, g, e, h = make_sources(tmp_path)
    phrases = geometry.read_phrases(geometry.DEFAULT_PROBES,space)
    encoder = fake_encoder(space,phrases)
    def no_fit(*args,**kwargs):
        raise AssertionError("A learned decoder was invoked")
    monkeypatch.setattr(geometry.ternary,"fit_decoder",no_fit)
    monkeypatch.setattr(smoke,"fit_kernel_readout",no_fit)
    args = lab.parser().parse_args([
        "--baseline-report",str(b), "--growth-report",str(g),
        "--exact-report",str(e), "--geometry-report",str(h),
        "--contexts","1","--bootstrap-trials","15",
        "--output",str(tmp_path/"result.json")])
    report = lab.run(args, encoder=encoder)
    assert report["acceptance"]["no_decoder_fitting"] is True
    assert report["experiment"]["prompt_count"] == 8*4*6
    assert report["experiment"]["empty_clause_cases"] == 8*2
    assert set(report["overall"]) == set(geometry.STAGES)
    assert set(report["overall"]["direct"]) == set(lab.METHODS) | {"count_only"}
    assert set(report["by_depth"]["direct"]) == {"1","2","4","8"}
    assert report["isolated_clause_polarity"]["direct"]["macro_polarity_retrieval_accuracy"] == 1
    assert len(report["paired_vs_whole_prompt"]) == 4
    assert json.loads(args.output.read_text())["schema"] == lab.SCHEMA
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS_REFUSE_TO_OVERWRITE"):
        lab.run(args, encoder=encoder)
    altered = json.loads(h.read_text())
    altered["sources"]["phrases_sha256"] = "bad"
    h.write_text(json.dumps(altered))
    args.output = tmp_path / "new.json"
    with pytest.raises(ValueError, match="BASELINE_SOURCE_OR_DESIGN_MISMATCH"):
        lab.run(args, encoder=encoder)
    assert not args.output.exists()


def test_verifies_existing_geometry_vocabulary_and_calibration_chains(tmp_path):
    space, b,g,e,h = make_sources(tmp_path)
    original = json.loads(e.read_text())
    original["unapproved_unsealed_candidate_vocabulary"][0]["description"] += " stale"
    e.write_text(json.dumps(original))
    args = lab.parser().parse_args([
        "--baseline-report",str(b),"--growth-report",str(g),
        "--exact-report",str(e),"--geometry-report",str(h),
        "--contexts","1","--bootstrap-trials","15",
        "--output",str(tmp_path/"bad.json")])
    with pytest.raises(ValueError, match="SOURCE_OR_STATUS_MISMATCH"):
        lab.run(args, encoder=lambda texts: np.ones((len(texts),20)))
    assert not args.output.exists()
