"""Offline regression for frozen, read-only NanoJev ternary semantics bakeoff."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_ternary_bakeoff as lab


def source_chain(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    entries = [{"id": f"v{i+1:04d}",
                "description": f"Synthetic diagnostic vocabulary entry {i+1:04d} for {axes[i%8]} conditions",
                "dimensions": [axes[i%8]]} for i in range(256)]
    baseline = {"schema": smoke.SCHEMA, "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256": smoke.seal(space), "vocabulary": entries[:24],
                "selected_vocabulary_size": 24, "candidate_vocabulary_sha256": smoke.seal(entries[:24]),
                "candidate_vocabulary_approved_or_sealed": False,
                "human_dimensions": axes, "model": {"backbone": smoke.MODEL_ID, "frozen": True},
                "calibration": {"train_count": 90, "selection_count": 24,
                                "final_test_count": 36, "seed": 41},
                "measurement": {"ridge": .5}}
    b = tmp_path / "base.json"
    b.write_text(json.dumps(baseline))
    growth = {"schema": lab.growth.SCHEMA, "acceptance": {"never_authorizes_contract_seal": True},
              "model": {"backbone": smoke.MODEL_ID, "frozen": True},
              "experiment": {"sizes": [24,36,48,72,96],
                             "selection_combinations": [["authority","obligation","resource","evidence","time"]],
                             "final_combinations": [["authority","obligation","resource","evidence","exclusion"]]},
              "all_candidate_vocabulary_unapproved_unsealed": entries[:96]}
    g = tmp_path / "growth.json"
    g.write_text(json.dumps(growth))
    exact = {"schema": lab.exact.SCHEMA,
             "status": "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance": {"never_authorizes_contract_seal": True,
                            "never_updates_model_weights": True},
             "model": {"backbone": smoke.MODEL_ID, "frozen": True},
             "experiment": {"sizes": [96,128,192,256],
                            "selection_combinations": [["authority","obligation","resource","time","consequence"]],
                            "final_combinations": [["authority","obligation","resource","uncertainty","exclusion"]]},
             "selection": {"selected_entries": 256},
             "sources": {"baseline_sha256": sha256(b.read_bytes()).hexdigest(),
                         "growth_sha256": sha256(g.read_bytes()).hexdigest(),
                         "human_space_sha256": smoke.seal(space),
                         "retained_vocabulary_96_sha256": smoke.seal(entries[:96])},
             "unapproved_unsealed_candidate_vocabulary": entries,
             "unapproved_vocabulary_sha256": smoke.seal(entries)}
    e = tmp_path / "exact.json"
    e.write_text(json.dumps(exact))
    return space, b, g, e


def fake_encoder(space, phrases, trace):
    axes = [d["id"] for d in space["dimensions"]]
    active = {axis: [s for phase in lab.PHASES for s in phrases[phase][axis]["positive"]]
              + next(d["phrases"] for d in space["dimensions"] if d["id"] == axis)
              for axis in axes}
    negated = {axis: [s for phase in lab.PHASES for s in phrases[phase][axis]["negative"]]
               for axis in axes}
    def encode(texts):
        trace.append(list(texts))
        features = np.zeros((len(texts), 32), dtype=float)
        for i, text in enumerate(texts):
            for j, axis in enumerate(axes):
                if any(phrase in text for phrase in negated[axis]):
                    features[i, j] = -1
                elif any(phrase in text for phrase in active[axis]):
                    features[i, j] = 1
                elif axis in text and "Synthetic diagnostic vocabulary entry" in text:
                    features[i, j] = 1
                features[i, 8+j] = features[i, j] * .3
            for j in range(16):
                features[i,16+j] = ((sum(text.encode()) + j*7) % (19+j)) / 600
        return features
    return encode


def test_vocab_provenance_and_tamper_refused(tmp_path):
    space, b, g, e = source_chain(tmp_path)
    _, _, entries, _ = lab.training.read_sources(e, g, b, space)
    assert len(entries) == 256
    content = json.loads(e.read_text())
    content["unapproved_unsealed_candidate_vocabulary"][0]["description"] = "changed"
    e.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="SOURCE_OR_STATUS_MISMATCH"):
        lab.training.read_sources(e, g, b, space)


def test_new_phrase_pools_and_reject_leakage(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    pools = lab.read_phrases(lab.DEFAULT_PROBES, space)
    assert set(pools) == set(lab.PHASES)
    assert len(pools["train"]["authority"]["positive"]) == 3
    damaged = json.loads(lab.DEFAULT_PROBES.read_text())
    damaged["phases"]["final"]["authority"]["negative"][0] = damaged["phases"]["train"]["authority"]["positive"][0]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match="PHRASE_REUSE_OR_LEAKAGE"):
        lab.read_phrases(path, space)


def test_ternary_triad_dataset_disjoint_and_no_clause_for_unspecified():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    phrases = lab.read_phrases(lab.DEFAULT_PROBES, space)
    rows = lab.make_cases(axes, phrases, budgets=lab.DEFAULT_BUDGETS,
                          selection_groups=24, final_groups=24, seed=1337)
    assert [len(rows[x]) for x in lab.PHASES] == [720, 72, 72]
    labels = [tuple(r["states"]) for phase in lab.PHASES for r in rows[phase]]
    assert len(labels) == len(set(labels))
    prompts = [r["prompt"] for phase in lab.PHASES for r in rows[phase]]
    assert len(prompts) == len(set(prompts))
    for phase in lab.PHASES:
        for start in range(0, len(rows[phase]), 3):
            a, b, c = rows[phase][start:start + 3]
            j = axes.index(a["target_axis"])
            assert [a["states"][j], b["states"][j], c["states"][j]] == [1, -1, 0]
            for k in range(8):
                if k != j:
                    assert a["states"][k] == b["states"][k] == c["states"][k]
            # For the no-mention variant the target's entire clause is omitted.
            for phrase in phrases[phase][a["target_axis"]]["positive"] + phrases[phase][a["target_axis"]]["negative"]:
                assert phrase not in c["prompt"]


def test_onehot_and_three_states_preserved():
    y = np.array([[1, -1, 0, 1, 0, -1, 1, 0]], dtype=np.int8)
    z = lab.one_hot(y).reshape(1, 8, 3)
    assert z[0,0].tolist() == [0,0,1]
    assert z[0,1].tolist() == [1,0,0]
    assert z[0,2].tolist() == [0,1,0]
    with pytest.raises(ValueError):
        lab.one_hot(np.full((1,8), 2, dtype=np.int8))


def test_triad_metrics_identify_denial_versus_absence_and_preservation():
    axes = list("abcdefgh")
    states = [[1,0,1,0,0,0,0,0],[-1,0,1,0,0,0,0,0],[0,0,1,0,0,0,0,0]]
    rows = [{"group_id":"g0","variant":name,"target_axis":"a","states":s}
            for name,s in zip(("affirmed","denied","unspecified"),states)]
    truth = lab.truth_matrix(rows)
    assert lab.triad_metrics(rows, truth, truth, axes)["target_denied_vs_unmentioned_both_correct"] == 1.
    collapsed = truth.copy()
    collapsed[1,0] = 0
    assert lab.triad_metrics(rows, truth, collapsed, axes)["target_denied_vs_unmentioned_both_correct"] == 0.
    changed = truth.copy()
    changed[1,2] = 0
    assert lab.triad_metrics(rows, truth, changed, axes)["non_target_dimensions_invariant"] == 0.
    with pytest.raises(ValueError):
        lab.triad_metrics(rows[::-1], truth, truth, axes)


def test_discrete_mi_and_decoder_not_binary_threshold():
    y = np.array([-1,0,1] * 20, dtype=np.int8)
    p = y.copy()
    mi, entropy = lab.discrete_mi(y,p)
    assert abs(mi - math.log2(3)) < 1e-10
    assert abs(entropy - math.log2(3)) < 1e-10
    assert lab.discrete_mi(y,np.zeros_like(y))[0] == 0
    x = np.vstack([np.full((20,1),-1), np.zeros((20,1)), np.full((20,1),1)])
    truth = np.tile(np.repeat(np.array([-1,0,1]), 20)[:,None], (1,8))
    model = lab.fit_decoder(x,truth,ridge=.01)
    assert np.array_equal(lab.predict_decoder(model,x),truth)


def test_selection_independent_of_final_results():
    def metric(exact, denied, balanced, information):
        return {"exact_semantic_pattern_accuracy":exact,
                "triads":{"target_denied_vs_unmentioned_both_correct":denied},
                "macro_three_state_balanced_accuracy":balanced,
                "macro_information_bits":information}
    sel = {(120,"direct"):metric(.05,.01,.8,.2),
           (240,"vocabulary"):metric(.08,.03,.6,.1),
           (720,"fused"):metric(.08,.06,.7,.1)}
    winner = lab.select_winner(sel)
    assert winner["representation"] == "fused" and winner["training_examples"] == 720
    sel[120,"direct"]["final_exact"] = 1.
    assert lab.select_winner(sel) == winner


def test_budget_validation():
    assert lab.parse_budgets("120,240,480,720") == lab.DEFAULT_BUDGETS
    for budgets in ("120,121", "240,120", "120,120", "13", "999", "abc"):
        with pytest.raises(ValueError):
            lab.parse_budgets(budgets)


def test_full_offline_run_locked_selection_source_unchanged(tmp_path):
    space, b, g, e = source_chain(tmp_path)
    probes = lab.read_phrases(lab.DEFAULT_PROBES, space)
    traces = []
    args = lab.parser().parse_args([
        "--baseline-report", str(b), "--growth-report", str(g),
        "--exact-report", str(e), "--output", str(tmp_path / "ternary.json"),
        "--budgets", "24,48,72", "--selection-groups", "16", "--final-groups", "16",
        "--bootstrap-trials", "10", "--null-trials", "5"])
    result = lab.run(args, encoder=fake_encoder(space,probes,traces))
    assert len(result["arms"]) == 9
    assert result["model"]["ollama_called"] is False
    assert result["model"]["vocabulary_entries"] == 256
    assert result["acceptance"]["never_authorizes_contract_seal"]
    assert result["experiment"]["state_vectors_disjoint_across_all_splits"]
    assert result["selection"]["final_used_for_selection"] is False
    assert result["experiment"]["selection_locked_before_final_embedded_or_scored"]
    assert traces[-1][0].startswith("Assess the explicitly stated")
    assert traces[-2][0].startswith("Determine the expressed status")
    assert traces[-3][0].startswith("Review the following contract propositions")
    for arm in result["arms"]:
        assert len(arm["final"]["per_axis"]) == 8
        assert arm["final"]["triads"]["triads"] == 16
    with pytest.raises(FileExistsError):
        lab.run(args, encoder=fake_encoder(space,probes,[]))


def test_import_contains_no_provider_activity():
    assert "ollama" not in lab.run.__code__.co_names
    assert lab.REPRESENTATIONS == ("vocabulary", "direct", "fused")
