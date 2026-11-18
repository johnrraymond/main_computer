"""Offline and deterministic tests; never load Qwen or connect to Ollama."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as factorial
from tools import nanojev_sealed_overlap_vocabulary_bakeoff as growth
from tools import nanojev_sealed_overlap_exact_growth_bakeoff as lab


def sources(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    vocab = [{"id": f"v{i+1:04d}",
              "description": f"Number {i+1} unique semantic evidence of {space['dimensions'][i%8]['definition']}",
              "dimensions": [axes[i%8]]} for i in range(96)]
    baseline = {"schema": smoke.SCHEMA, "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256": smoke.seal(space),
                "candidate_vocabulary_sha256": smoke.seal(vocab[:24]),
                "candidate_vocabulary_approved_or_sealed": False,
                "selected_vocabulary_size": 24, "human_dimensions": axes,
                "calibration": {"train_count": 90, "selection_count": 24,
                                "final_test_count": 36, "seed": 41},
                "measurement": {"ridge": .5},
                "model": {"frozen": True, "backbone": smoke.MODEL_ID}, "vocabulary": vocab[:24]}
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    fact = {"schema": factorial.SCHEMA, "v2_report_sha256": sha256(baseline_path.read_bytes()).hexdigest(),
            "human_space_sha256": smoke.seal(space),
            "vocabulary_sha256": smoke.seal(vocab[:24]),
            "vocabulary_unchanged_no_ollama_calls": True,
            "cells": {cell: {"per_case": [
                {"dimensions": ["authority", "resource"], "vocabulary_predictions": ["resource", "evidence"]},
                {"dimensions": ["obligation", "time"], "vocabulary_predictions": ["time", "consequence"]}]}
                for cell in ("known_paraphrase", "novel_paraphrase")}}
    fact_path = tmp_path / "factorial.json"
    fact_path.write_text(json.dumps(fact), encoding="utf-8")
    five = list(__import__("itertools").combinations(axes, 5))
    six = list(__import__("itertools").combinations(axes, 6))
    prior = {"schema": growth.SCHEMA,
             "status": "COMPLETE_VOCABULARY_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance": {"never_authorizes_contract_seal": True},
             "model": {"frozen": True, "backbone": smoke.MODEL_ID},
             "source": {"baseline_sha256": sha256(baseline_path.read_bytes()).hexdigest(),
                        "failure_focus": {"source_sha256": sha256(fact_path.read_bytes()).hexdigest()},
                        "space_sha256": smoke.seal(space),
                        "probes_sha256": sha256(growth.DEFAULT_PROBES.read_bytes()).hexdigest(),
                        "baseline_vocabulary_sha256": smoke.seal(vocab[:24])},
             "human_dimensions": axes,
             "experiment": {"sizes": [24,36,48,72,96], "selection_final_combinations_disjoint": True,
                            "selection_combinations": [list(c) for c in five[:12] + six[:6]],
                            "final_combinations": [list(c) for c in five[12:24] + six[6:12]]},
             "selection": {"selected_entries": 96},
             "all_candidate_vocabulary_unapproved_unsealed": vocab,
             "all_candidate_vocabulary_sha256": smoke.seal(vocab)}
    prior_path = tmp_path / "growth.json"
    prior_path.write_text(json.dumps(prior), encoding="utf-8")
    return space, baseline, baseline_path, fact_path, prior_path, prior


def encode_factory(space):
    # Synthetic encoder with semantically distinct embeddings; no actual weights.
    phrases = [[*d["phrases"], *factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space)[d["id"]],
                *growth.read_growth_probes(growth.DEFAULT_PROBES, space)["selection"][d["id"]],
                *growth.read_growth_probes(growth.DEFAULT_PROBES, space)["final"][d["id"]],
                *lab.read_new_probes(lab.DEFAULT_PROBES, space)["selection"][d["id"]],
                *lab.read_new_probes(lab.DEFAULT_PROBES, space)["final"][d["id"]],
                d["definition"]] for d in space["dimensions"]]
    def encode(texts):
        arr = np.zeros((len(texts), len(phrases)+1), dtype=float)
        for i, line in enumerate(texts):
            for j, variants in enumerate(phrases):
                arr[i,j] = float(any(x in line for x in variants))
            arr[i,-1] = .01
        return arr
    return encode


def propose(space, existing, count, focus, **kwargs):
    axes = [d["id"] for d in space["dimensions"]]
    return [{"id": f"v{len(existing)+i+1:04d}",
             "description": f"Unique additional diagnostic description number {len(existing)+i+1} for {axes[(len(existing)+i)%8]}",
             "dimensions": [axes[(len(existing)+i)%8]]} for i in range(count)]


def test_prior_source_authenticated_and_96_prefix_retained(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    got = lab.validate_source_growth(p, b, f, baseline, space)
    assert got["all_candidate_vocabulary_unapproved_unsealed"] == obj["all_candidate_vocabulary_unapproved_unsealed"]
    obj["all_candidate_vocabulary_unapproved_unsealed"][0]["description"] = "An unauthorized different description"
    p.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="UNVERIFIED"):
        lab.validate_source_growth(p, b, f, baseline, space)


def test_new_probes_exclude_all_prior_phrase_pools(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    good = lab.read_new_probes(lab.DEFAULT_PROBES, space)
    assert len(good["final"]) == len(space["dimensions"]) == 8
    edited = json.loads(lab.DEFAULT_PROBES.read_text(encoding="utf-8"))
    edited["selection"]["authority"][0] = growth.read_growth_probes(growth.DEFAULT_PROBES, space)["final"]["authority"][0]
    target = tmp_path / "bad_probe.json"
    target.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(ValueError, match="LEAKAGE"):
        lab.read_new_probes(target, space)


def test_holdouts_have_never_seen_prior_combinations(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    train, cases, design = lab.build_cases(space, baseline, obj,
        lab.read_new_probes(lab.DEFAULT_PROBES, space), seed=943, five_count=12, six_count=6)
    assert len(train) == 90
    assert design["combinations_strictly_unused_in_prior_experiments"]
    for phase in lab.PHASES:
        assert len(cases[phase]["familiar"]) == len(cases[phase]["paraphrase"]) == 36
        assert [x["dimensions"] for x in cases[phase]["familiar"]] == [x["dimensions"] for x in cases[phase]["paraphrase"]]
    old = {frozenset(c) for key in ("selection_combinations", "final_combinations") for c in obj["experiment"][key]}
    assert not old.intersection(map(frozenset, design["selection_combinations"] + design["final_combinations"]))


def test_joint_calibrator_improves_exact_recovery_on_oof_scores():
    rng = np.random.default_rng(12)
    truth = rng.integers(0, 2, (90, 8), dtype=np.int8)
    oof = np.where(truth, 0.43, 0.18) + rng.normal(0, .006, (90, 8))
    result = lab.calibrate_joint_thresholds(oof, truth)
    assert result["oof_exact_before"] == 0.0
    assert result["oof_exact_after"] == 1.0
    assert result["trained_exclusively_on_oof_training_labels"] is True
    assert (result["thresholds"] < .5).any()


def test_decoder_and_size_winner_selection_is_final_blind():
    def metric(exact, bal):
        return {"exact_semantic_pattern_accuracy": exact,
                "macro_balanced_accuracy": bal,
                "macro_target_entropy_coverage_fraction": 0.3}
    grid = {(n, d): metric(0., .55) for n in lab.SIZES for d in lab.DECODERS}
    grid[128, "joint_exact"] = metric(.10, .30)
    grid[256, "current"] = metric(0., .95)
    winner = lab.choose_winner(grid)
    assert winner["selected_entries"] == 128 and winner["selected_decoder"] == "joint_exact"
    grid[256, "current"]["final_test_exact"] = 1.0
    assert lab.choose_winner(grid) == winner


def test_growth_retains_original_and_rejects_unauthorized_axis(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    prior = obj["all_candidate_vocabulary_unapproved_unsealed"]
    grew = lab.grow_from_96(space, prior, proposer=propose, focus={}, sizes=lab.SIZES,
                             batch_size=16, attempts=2, model="synthetic", base_url="synthetic", timeout=1)
    assert [len(grew[x]) for x in lab.SIZES] == [96,128,192,256]
    assert grew[256][:96] == prior
    assert prior[-1]["id"] == "v0096"
    def malicious(space, existing, count, focus, **kwargs):
        return [{"id": f"v{len(existing)+1:04d}", "description": "This refers to an alien unknown coordinate", "dimensions": ["alien"]}]
    with pytest.raises(ValueError, match="UNAUTHORIZED_DIMENSION"):
        lab.grow_from_96(space, prior, proposer=malicious, focus={}, sizes=lab.SIZES,
                         batch_size=16, attempts=1, model="synthetic", base_url="synthetic", timeout=1)


def test_end_to_end_offline_selection_final_isolation_and_outputs(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    args = lab.parser().parse_args(["--baseline-report", str(b),
        "--factorial-report", str(f), "--growth-report", str(p),
        "--output", str(tmp_path / "extension.json"),
        "--null-trials", "7", "--bootstrap-trials", "11",
        "--five-count", "2", "--six-count", "2"])
    out = lab.run(args, proposer=propose, encoder=encode_factory(space))
    assert out["status"] == "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION"
    assert out["acceptance"]["never_authorizes_contract_seal"] is True
    assert out["selection"]["final_evaluations_used_for_selection"] is False
    assert out["experiment"]["selection_locked_before_final_embedded_or_scored"] is True
    assert [x["entries"] for x in out["arms"]] == list(lab.SIZES)
    assert out["unapproved_unsealed_candidate_vocabulary"][:96] == obj["all_candidate_vocabulary_unapproved_unsealed"]
    for arm in out["arms"]:
        for decoder in lab.DECODERS:
            assert arm["cells"][f"final_paraphrase_{decoder}"]["sample_count"] == 8
            assert "exact_pattern_accuracy_delta" in arm["paired_effects"][f"final_paraphrase_{decoder}"]
        assert "exact_pattern_accuracy_delta" in arm["paired_effects"]["final_paraphrase_joint_vs_current"]
        assert len(arm["joint_exact_train_oof_calibration"]["thresholds"]) == 8
    assert json.loads(args.output.read_text(encoding="utf-8")) == out
    assert not args.output.with_name(args.output.stem + ".growth-checkpoint.json").exists()
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        lab.run(args, proposer=propose, encoder=encode_factory(space))


def test_original_ridge_is_preserved(tmp_path):
    space, baseline, b, f, p, obj = sources(tmp_path)
    args = lab.parser().parse_args(["--baseline-report", str(b),
        "--factorial-report", str(f), "--growth-report", str(p),
        "--output", str(tmp_path / "unused.json"), "--ridge", "0.2"])
    with pytest.raises(ValueError, match="RIDGE_MUST_MATCH"):
        lab.run(args, proposer=propose, encoder=encode_factory(space))


def test_selection_occurs_before_any_final_prompt_encoding(tmp_path, monkeypatch):
    space, baseline, b, f, p, obj = sources(tmp_path)
    args = lab.parser().parse_args(["--baseline-report", str(b),
        "--factorial-report", str(f), "--growth-report", str(p),
        "--output", str(tmp_path / "order.json"),
        "--null-trials", "5", "--bootstrap-trials", "10",
        "--five-count", "2", "--six-count", "2"])
    original_encoder = encode_factory(space)
    observed = []
    def guarded_encoder(texts):
        observed.extend(texts)
        return original_encoder(texts)
    original_choose = lab.choose_winner
    def checked_choose(grid):
        final_phrases = lab.read_new_probes(lab.DEFAULT_PROBES, space)["final"]
        assert not any(any(phrase in sentence for arr in final_phrases.values() for phrase in arr)
                       for sentence in observed)
        return original_choose(grid)
    monkeypatch.setattr(lab, "choose_winner", checked_choose)
    report = lab.run(args, proposer=propose, encoder=guarded_encoder)
    assert report["selection"]["final_evaluations_used_for_selection"] is False
    assert any(any(p in sentence for arr in lab.read_new_probes(lab.DEFAULT_PROBES, space)["final"].values()
                   for p in arr) for sentence in observed)


def test_growth_reduces_batch_then_checkpoints_and_resumes(tmp_path):
    space, baseline, b, f, p, prior = sources(tmp_path)
    original = prior["all_candidate_vocabulary_unapproved_unsealed"]
    checkpoint = tmp_path / "experiment.growth-checkpoint.json"
    identity = {"prior_sha256": smoke.seal(original), "ollama_model": "fake"}
    focus = {"axes_ranked_by_paraphrase_errors": [d["id"] for d in space["dimensions"]]}
    request_sizes = []
    state = {"fail_after": 100}

    def flaky(space, existing, count, focus, **kwargs):
        request_sizes.append(count)
        if count > 4 or (state["fail_after"] is not None and len(existing) >= state["fail_after"]):
            raise growth.OllamaProviderHTTPError(500, detail="runner capacity", model="fake",
                                                 requested=count, prompt_chars=100)
        return propose(space, existing, count, focus, **kwargs)

    kw = dict(proposer=flaky, focus=focus, sizes=lab.SIZES, batch_size=8,
              attempts=2, model="fake", base_url="http://127.0.0.1:11434", timeout=3,
              checkpoint_path=checkpoint, checkpoint_identity=identity)
    with pytest.raises(RuntimeError, match="EXACT_GROWTH_PROVIDER_FAILED_AT_100_ENTRIES"):
        lab.grow_from_96(space, original, **kw)
    assert request_sizes[:2] == [8, 4]
    assert checkpoint.exists()
    saved = lab._read_growth_checkpoint(checkpoint, identity=identity, original=original,
                                        allowed=set(baseline["human_dimensions"]))
    assert len(saved) == 100
    state["fail_after"] = None
    all_sizes = lab.grow_from_96(space, original, **kw)
    assert list(all_sizes) == [96, 128, 192, 256]
    assert len(all_sizes[256]) == 256
    assert all_sizes[96] == original
    assert all_sizes[128][:100] == saved
    assert all_sizes[192] == all_sizes[256][:192]
    assert len(lab._read_growth_checkpoint(checkpoint, identity=identity, original=original,
                                           allowed=set(baseline["human_dimensions"]))) == 256


def test_growth_rejects_tampered_and_stale_checkpoints(tmp_path):
    space, baseline, b, f, p, prior = sources(tmp_path)
    original = prior["all_candidate_vocabulary_unapproved_unsealed"]
    checkpoint = tmp_path / "experiment.growth-checkpoint.json"
    identity = {"source": smoke.seal(original)}
    lab._write_growth_checkpoint(checkpoint, identity=identity, entries=original)
    with pytest.raises(ValueError, match="CHECKPOINT_MISMATCH"):
        lab._read_growth_checkpoint(checkpoint, identity={"source": "wrong"}, original=original,
                                    allowed=set(baseline["human_dimensions"]))
    obj = json.loads(checkpoint.read_text(encoding="utf-8"))
    obj["entries"][-1]["description"] += " altered"
    checkpoint.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="CHECKPOINT_MISMATCH"):
        lab._read_growth_checkpoint(checkpoint, identity=identity, original=original,
                                    allowed=set(baseline["human_dimensions"]))
