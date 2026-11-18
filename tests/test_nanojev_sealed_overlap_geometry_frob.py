"""Offline regression for decoder-free overlap geometry localization."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_geometry_frob as lab
from tools import nanojev_sealed_overlap_information_smoke as smoke


def source_chain(tmp_path: Path):
    """Minimal valid immutable artifact chain; follows real source validation."""
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
    growth = {"schema": lab.training.growth.SCHEMA,
              "acceptance": {"never_authorizes_contract_seal": True},
              "model": {"backbone": smoke.MODEL_ID, "frozen": True},
              "experiment": {"sizes": [24,36,48,72,96]},
              "all_candidate_vocabulary_unapproved_unsealed": entries[:96]}
    g = tmp_path / "growth.json"
    g.write_text(json.dumps(growth))
    exact = {"schema": lab.training.exact.SCHEMA,
             "status": "COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance": {"never_authorizes_contract_seal": True,
                            "never_updates_model_weights": True},
             "model": {"backbone": smoke.MODEL_ID, "frozen": True},
             "experiment": {"sizes": [96,128,192,256]},
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


def fake_encoder(space, phrases):
    axes = [d["id"] for d in space["dimensions"]]
    def encode(texts):
        vectors = np.zeros((len(texts), 24), dtype=float)
        vectors[:, 8] = 1.0  # stable neutral signal, even when target is omitted
        for i, text in enumerate(texts):
            for j, axis in enumerate(axes):
                if any(p in text for p in phrases[axis]["negative"]):
                    vectors[i, j] = -1.0
                elif any(p in text for p in phrases[axis]["positive"]):
                    vectors[i, j] = 1.0
                elif any(p in text for p in space["dimensions"][j]["phrases"]):
                    vectors[i, j] = 1.0
                elif f"for {axis} conditions" in text:
                    vectors[i, j] = 1.0
        return vectors
    return encode


def test_phrases_are_new_and_source_reuse_is_refused(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    phrases = lab.read_phrases(lab.DEFAULT_PROBES, space)
    assert len(phrases) == 8
    assert all(len(phrases[axis][state]) == 2 for axis in phrases for state in ("positive", "negative"))
    damaged = json.loads(lab.DEFAULT_PROBES.read_text())
    damaged["axes"]["authority"]["positive"][0] = space["dimensions"][0]["phrases"][0]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match="GEOMETRY_REUSED_PHRASE"):
        lab.read_phrases(path, space)


def test_dataset_is_fully_matched_with_no_axis_leakage_in_omissions():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    phrases = lab.read_phrases(lab.DEFAULT_PROBES, space)
    rows = lab.make_cases(axes, phrases, contexts=4)
    assert len(rows) == 8*4*4*6
    assert len({r["group_id"] for r in rows}) == 128
    # There must be genuine repeated omission text; no target marker was added.
    assert len({r["prompt"] for r in rows}) < len(rows)
    for start in range(0, len(rows), 6):
        group = rows[start:start+6]
        assert [r["state"] for r in group] == list(lab.STATES)*2
        assert [r["variant"] for r in group] == [0,0,0,1,1,1]
        assert len({r["prompt"] for r in group}) == 6
        k = axes.index(group[0]["axis"])
        for version in (0,1):
            a, b, z = group[3*version:3*version+3]
            assert [a["states"][k], b["states"][k], z["states"][k]] == [1,-1,0]
            for j in range(8):
                if j != k:
                    assert a["states"][j] == b["states"][j] == z["states"][j]
            assert not any(s in z["prompt"] for p in phrases[a["axis"]].values() for s in p)
            assert len([v for v in a["states"] if v]) == a["depth"]
    with pytest.raises(ValueError, match="GEOMETRY_EIGHT_AXES"):
        lab.make_cases(axes[:-1],phrases)


def test_nearest_cross_paraphrase_retrieval_recovers_clean_semantics():
    # Two identical geometric exemplars for each of 3 states; no learned model.
    features = np.array([[1,0,0],[0,1,0],[0,0,1]]*2, dtype=float)
    m = lab.group_metrics(features)
    assert m["retrieval_correct"] == 6
    assert m["all_six_retrieved"]
    assert m["denied_and_omitted_both_retrieved"]
    assert all(np.mean(m["margins"][x]) > 0 for x in m["margins"])
    assert lab.aggregate([m])["denial_omission_both_retrieved_rate"] == 1.0


def test_negative_collapsed_into_absent_is_detected():
    features = np.array([[1,0,0],[0,0,1],[0,0,1]]*2, dtype=float)
    m = lab.group_metrics(features)
    assert not m["denied_and_omitted_both_retrieved"]
    assert all(abs(v) < 1e-10 for v in m["margins"]["denied_vs_omitted"])
    assert lab.aggregate([m])["fraction_collapsed_denied_delta"] == 1.0


def test_calibrated_stages_are_finite_and_do_not_preserve_all_direct_info():
    x = np.array([[1, 0],[0,1],[-1,0]], float)
    entries = np.array([[1,0],[0,1]], float)
    thresholds = np.array([0.5,0.5])
    scales = np.array([0.2,0.2])
    support = np.array([1,0])
    features = lab.build_representations(x, entries, thresholds, scales, support)
    assert set(features) == set(lab.STAGES)
    assert np.isfinite(np.concatenate([v.reshape(-1) for v in features.values()])).all()
    assert np.all(features["overlap_evidence"][:,1] == 0)
    assert np.all(features["overlap_binary"][:,1] == 0)
    assert features["direct"].shape[1] == 2
    assert features["description_similarity"].shape[1] == 2


def test_bootstrap_refuses_unpaired_group_lists():
    m = lab.group_metrics(np.eye(3).repeat(2,axis=0).reshape(6,3))
    with pytest.raises(ValueError, match="GEOMETRY_PAIRED_BOOTSTRAP_INPUT_INVALID"):
        lab.pairwise_bootstrap([m], [m,m], seed=1, trials=40)


def test_end_to_end_offline_no_decoder_training_and_source_checks(tmp_path, monkeypatch):
    space, b, g, e = source_chain(tmp_path)
    phrases = lab.read_phrases(lab.DEFAULT_PROBES,space)
    fake = fake_encoder(space, phrases)
    def should_not_fit(*args,**kwargs):
        raise AssertionError("learned decoder must never be invoked")
    monkeypatch.setattr(lab.ternary, "fit_decoder", should_not_fit)
    monkeypatch.setattr(smoke, "fit_kernel_readout", should_not_fit)
    args = lab.parser().parse_args(["--baseline-report",str(b), "--growth-report",str(g),
                                   "--exact-report",str(e), "--output",str(tmp_path/"result.json"),
                                   "--contexts","1", "--bootstrap-trials","15"])
    report = lab.run(args, encoder=fake)
    assert report["acceptance"]["never_updates_model_weights"] is True
    assert report["acceptance"]["no_new_decoder_training"] is True
    assert report["experiment"]["prompt_count"] == 8*4*6
    assert report["experiment"]["matched_groups"] == 32
    assert report["sources"]["vocabulary_sha256"] == smoke.seal(json.loads(e.read_text())["unapproved_unsealed_candidate_vocabulary"])
    assert 0 <= report["overall"]["direct"]["retrieval_accuracy"] <= 1
    assert set(report["by_depth"]["overlap_binary"]) == {"1","2","4","8"}
    assert json.loads(args.output.read_text())["status"] == report["status"]
    with pytest.raises(FileExistsError,match="OUTPUT_EXISTS_REFUSE_TO_OVERWRITE"):
        lab.run(args, encoder=fake)
    altered = json.loads(e.read_text())
    altered["unapproved_unsealed_candidate_vocabulary"][0]["description"] += " changed"
    e.write_text(json.dumps(altered))
    args.output = tmp_path/"different.json"
    with pytest.raises(ValueError, match="SOURCE_OR_STATUS_MISMATCH"):
        lab.run(args, encoder=fake)
    assert not args.output.exists()


def test_concept_direction_coherence_survives_context_and_detects_collapse():
    features = np.array([[1,0,1],[0,1,1],[0,0,1]]*2, float)
    p, n = lab.concept_displacements(features)
    good = lab.displacement_coherence([(p,n),(p,n)])
    assert good["affirmed_direction_pairwise_cosine"] == pytest.approx(1.)
    assert good["denied_direction_pairwise_cosine"] == pytest.approx(1.)
    assert good["collapsed_affirmed_directions"] == 0
    deeper = lab.cross_depth_coherence([(p,n)],[(p,n)])
    assert deeper["affirmed_direction_to_single_axis_cosine"] == pytest.approx(1.)
    assert deeper["denied_direction_to_single_axis_cosine"] == pytest.approx(1.)
    failed = lab.displacement_coherence([(np.zeros(3), n),(np.zeros(3), n)])
    assert failed["collapsed_affirmed_directions"] == 2
    assert failed["affirmed_direction_pairwise_cosine"] == 0
