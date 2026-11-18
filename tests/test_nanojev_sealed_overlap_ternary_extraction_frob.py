"""Offline tests for locked half:last ternary semantic recovery and codec recalibration."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_ternary_bakeoff as ternary
from tools import nanojev_sealed_overlap_ternary_extraction_frob as lab
import test_nanojev_sealed_overlap_ternary_bakeoff as old_tests


def report_chain(tmp_path, *, budgets=(24, 48, 72), selection_groups=16, final_groups=16):
    space, b, g, e = old_tests.source_chain(tmp_path)
    phrases = ternary.read_phrases(ternary.DEFAULT_PROBES, space)
    axes = [d["id"] for d in space["dimensions"]]
    rows = ternary.make_cases(axes, phrases, budgets=budgets,
                             selection_groups=selection_groups, final_groups=final_groups, seed=1337)
    entries = json.loads(e.read_text())["unapproved_unsealed_candidate_vocabulary"]
    confirm = {"schema": "nanojev.sealed-overlap.polarity-confirmation.v1",
               "status": "COMPLETE_LOCKED_POLARITY_CONFIRMATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
               "acceptance": {"candidate_not_promoted": True, "never_updates_model_weights": True},
               "design": {"candidate_locked_before_new_holdout": "half:last",
                          "reference_locked_before_new_holdout": "final:mean",
                          "selection_on_new_holdout": False},
               "sources": {"exact_report_sha256": sha256(e.read_bytes()).hexdigest(),
                           "vocabulary_sha256": smoke.seal(entries), "space_sha256": smoke.seal(space)},
               "results": {
                   "final:mean": {"direct": {"polarity_accuracy": .65},
                                  "vocabulary_similarity": {"polarity_accuracy": .55},
                                  "projection_numeric_stability": {"fp32": {"comparison_flip_rate": 0.}}},
                   "half:last": {"direct": {"polarity_accuracy": .79},
                                 "vocabulary_similarity": {"polarity_accuracy": .72},
                                 "projection_numeric_stability": {"fp32": {"comparison_flip_rate": 0.}}}}}
    c = tmp_path / "confirmed.json"
    c.write_text(json.dumps(confirm))
    prior = {"schema": ternary.SCHEMA,
             "status": "COMPLETE_TERNARY_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance": {"never_updates_model_weights": True, "vocabulary_unchanged": True},
             "model": {"backbone": smoke.MODEL_ID, "vocabulary_sha256": smoke.seal(entries)},
             "sources": {"exact_sha256": sha256(e.read_bytes()).hexdigest(),
                         "growth_sha256": sha256(g.read_bytes()).hexdigest(),
                         "baseline_sha256": sha256(b.read_bytes()).hexdigest(),
                         "human_space_sha256": smoke.seal(space),
                         "phrases_sha256": sha256(ternary.DEFAULT_PROBES.read_bytes()).hexdigest()},
             "experiment": {"budgets": list(budgets), "train_count": len(rows["train"]),
                            "selection_count": len(rows["selection"]),
                            "final_count": len(rows["final"]),
                            "ridge": .5, "representations": list(ternary.REPRESENTATIONS),
                            "axis_states": {"-1": "explicitly denied", "0": "not mentioned", "1": "explicitly affirmed"}},
             "arms": [{"training_examples": budget, "representation": rep,
                       "final": {"exact_semantic_pattern_accuracy": 0., "mean_axis_accuracy": .4}}
                      for budget in budgets for rep in ternary.REPRESENTATIONS]}
    t = tmp_path / "prior.json"
    t.write_text(json.dumps(prior))
    args = lab.parser().parse_args([
        "--exact-report", str(e), "--growth-report", str(g), "--baseline-report", str(b),
        "--confirmation-report", str(c), "--ternary-report", str(t),
        "--budgets", ",".join(map(str, budgets)),
        "--selection-groups", str(selection_groups), "--final-groups", str(final_groups),
        "--bootstrap-trials", "10", "--null-trials", "1",
        "--output", str(tmp_path / "result.json")])
    return space, phrases, entries, rows, args


def mock_layers_encoder(space, phrases, calls):
    historical = old_tests.fake_encoder(space, phrases, [])
    def encode(texts):
        calls.append(list(texts))
        base = historical(texts)
        # In this deliberately artificial test, confirmed half:last carries a
        # distinct geometric embedding. It is NOT obtained by reading labels.
        candidate = np.concatenate([base[:, :16] * 2.5, base[:, 16:]], axis=1)
        reference = np.concatenate([base[:, :16], base[:, 16:] * 1.8], axis=1)
        return {"half:last": candidate, "final:mean": reference}
    return encode


def test_lock_views_source_and_strict_float_validation(tmp_path):
    space, phrases, entries, rows, args = report_chain(tmp_path)
    assert lab.CANDIDATE == "half:last"
    assert lab.REFERENCE == "final:mean"
    assert lab.REPS == ("vocabulary", "direct", "fused")
    confirm, prior = lab.read_lineage(args, space=space, entries=entries,
                                      baseline=json.loads(args.baseline_report.read_text()),
                                      rows=rows, budgets=(24,48,72))
    assert confirm["design"]["candidate_locked_before_new_holdout"] == lab.CANDIDATE
    assert len(prior["arms"]) == 9
    a = np.ones((2, 32))
    assert lab.take_views({"final:mean": a, "half:last": 2*a}, count=2)["half:last"].shape == (2,32)
    with pytest.raises(ValueError, match="MISSING_LOCKED"):
        lab.take_views({"final:mean":a}, count=2)
    with pytest.raises(ValueError, match="INVALID_EMBEDDINGS"):
        lab.take_views({"final:mean":a, "half:last":a*np.nan}, count=2)
    with pytest.raises(ValueError, match="FEATURE_WIDTH"):
        lab.take_views({"final:mean":a, "half:last":np.ones((2,16))}, count=2)


def test_confirmation_and_prior_cannot_be_substituted(tmp_path):
    space, phrases, entries, rows, args = report_chain(tmp_path)
    base = json.loads(args.baseline_report.read_text())
    confirmation = json.loads(args.confirmation_report.read_text())
    confirmation["design"]["candidate_locked_before_new_holdout"] = "quarter:mean"
    args.confirmation_report.write_text(json.dumps(confirmation))
    with pytest.raises(ValueError, match="CONFIRMATION_SOURCE_OR_METHOD_MISMATCH"):
        lab.read_lineage(args, space=space, entries=entries, baseline=base, rows=rows, budgets=(24,48,72))
    confirmation["design"]["candidate_locked_before_new_holdout"] = lab.CANDIDATE
    args.confirmation_report.write_text(json.dumps(confirmation))
    prior = json.loads(args.ternary_report.read_text())
    prior["sources"]["phrases_sha256"] = "not-the-original-ternary-data"
    args.ternary_report.write_text(json.dumps(prior))
    with pytest.raises(ValueError, match="HISTORICAL_SOURCE_OR_CONFIG_MISMATCH"):
        lab.read_lineage(args, space=space, entries=entries, baseline=base, rows=rows, budgets=(24,48,72))


def test_codec_is_fitted_separately_for_each_extraction(tmp_path):
    space, phrases, entries, rows, args = report_chain(tmp_path)
    cal = json.loads(args.baseline_report.read_text())["calibration"]
    train, _, _ = smoke.calibration_three(space, cal["train_count"], cal["selection_count"],
                                          cal["final_test_count"], cal["seed"])
    calls=[]
    encoder=mock_layers_encoder(space,phrases,calls)
    vocab=lab.take_views(encoder([e["description"] for e in entries]), count=256)
    values=lab.take_views(encoder([r["prompt"] for r in train]),count=90)
    out=lab.calibrate_independently(vocab,values,train,entries)
    assert set(out)==set(lab.EXTRACTIONS)
    assert all(x["thresholds"].shape==(256,) for x in out.values())
    assert out[lab.CANDIDATE]["description_vector_sha256"] != out[lab.REFERENCE]["description_vector_sha256"]
    assert not np.array_equal(out[lab.CANDIDATE]["thresholds"],out[lab.REFERENCE]["thresholds"])
    feature=lab.features_by_extraction(values,vocab,out)
    assert feature[lab.CANDIDATE]["direct"].shape==(90,32)
    assert feature[lab.CANDIDATE]["vocabulary"].shape==(90,256)
    assert feature[lab.CANDIDATE]["fused"].shape==(90,288)


def test_triad_paired_deltas_not_omission_count_surrogates():
    rows = [
        {"states": [1,0,0,0,0,0,0,0], "variant": "affirmed"},
        {"states": [-1,0,0,0,0,0,0,0], "variant": "denied"},
        {"states": [0,0,0,0,0,0,0,0], "variant": "unspecified"},
    ] * 3
    truth = ternary.truth_matrix(rows)
    reference = truth.copy()
    reference[1::3,0] = 0
    candidate = truth.copy()
    out = lab.compare_final(rows,candidate,reference,seed=7,trials=30)
    assert out["candidate_minus_reference_target_denial_vs_omission"] == 1.
    assert out["candidate_minus_reference_mean_axis_accuracy"] == 1 / 24
    assert out["candidate_minus_reference_exact_pattern_accuracy"] == 1 / 3
    broken = [dict(r) for r in rows]
    broken[2]["variant"] = "denied"
    with pytest.raises(ValueError, match="TRIAD_CONTROL_LOST"):
        lab.compare_final(broken,candidate,reference,seed=7,trials=30)


def test_full_size_mock_run_same_cases_and_locked_selection_before_final(tmp_path):
    # All historical budgets and split sizes, but only one null trial to keep
    # this exercise focused on orchestration rather than statistical precision.
    space, phrases, entries, rows, args = report_chain(tmp_path, budgets=(120,240,480,720),
                                                         selection_groups=24, final_groups=24)
    args.bootstrap_trials = 10
    calls=[]
    result=lab.run(args,encoder=mock_layers_encoder(space,phrases,calls))
    assert result["status"] == "COMPLETE_TERNARY_EXTRACTION_COMPARISON_NO_SEAL_NO_PROMOTION"
    assert len(result["arms"]) == 24
    assert result["experiment"]["historical_final_reused_not_fresh_holdout"] is True
    assert result["acceptance"]["never_updates_model_weights"] is True
    assert result["experiment"]["experimental_default_extraction"] == "half:last"
    assert all(len(a["final"]["per_axis"]) == 8 for a in result["arms"])
    assert all(a["final"]["triads"]["triads"] == 24 for a in result["arms"])
    assert len(result["matched_candidate_minus_reference"]) == 12
    assert len(result["historical_final_mean_parity"]) == 12
    assert result["selection"]["final_used_for_selection"] is False
    assert result["selection"]["winners_by_extraction"].keys() == set(lab.EXTRACTIONS)
    assert len(calls)==5
    assert len(calls[0])==256 and len(calls[1])==90 and len(calls[2])==720
    assert calls[3][0].startswith("Determine the expressed status")
    assert calls[4][0].startswith("Assess the explicitly stated")
    assert json.loads(args.output.read_text())["schema"]==lab.SCHEMA
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        lab.run(args,encoder=mock_layers_encoder(space,phrases,[]))


def test_invalid_train_parameters_refused_before_model_load(tmp_path):
    space, phrases, entries, rows, args = report_chain(tmp_path)
    args.max_length=128
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        lab.run(args,encoder=lambda _:None)
    args.max_length=192
    args.budgets="24,120"
    with pytest.raises(ValueError,match="HISTORICAL_SOURCE_OR_CONFIG_MISMATCH"):
        lab.run(args,encoder=lambda _:None)


def test_no_inadvertent_provider_or_production_mutation():
    assert "ollama" not in lab.run.__code__.co_names
    assert "fit_decoder" in lab.run.__code__.co_names
    assert "FrozenLayerEncoder" in lab.run.__code__.co_names
    assert "ternary_extraction" in lab.SCHEMA.replace("-", "_")
