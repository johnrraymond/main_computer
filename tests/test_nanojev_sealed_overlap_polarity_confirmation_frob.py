"""Offline source, holdout, numeric and end-to-end tests for locked polarity confirmation."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_polarity_confirmation_frob as lab
from tools import nanojev_sealed_overlap_geometry_frob as geometry
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_information_smoke as smoke


def actual_fixture():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    prior_sel = geometry.read_phrases(geometry.DEFAULT_PROBES, space)
    prior_fin = layers.read_final_phrases(layers.FINAL_PHRASES, space, prior_sel)
    return space, prior_sel, prior_fin, lab.read_fresh_phrases(lab.FRESH_PHRASES, space, prior_sel, prior_fin)


def test_new_holdout_disjoint_and_has_eight_complete_axes():
    space, prior_sel, prior_fin, new = actual_fixture()
    assert len(new) == 8
    historical = {text.casefold() for pool in (prior_sel, prior_fin) for pair in pool.values()
                  for state in ("positive", "negative") for text in pair[state]}
    test = [text for pair in new.values() for state in ("positive", "negative") for text in pair[state]]
    assert len(test) == len(set(test)) == 32
    assert not ({t.casefold() for t in test} & historical)
    assert lab.CANDIDATE == "half:last" and lab.BASELINE == "final:mean"
    assert lab.CONTENDERS == ("half:last", "final:mean")


def test_holdout_detects_leaks_and_invalid_axes(tmp_path):
    space, prior_sel, prior_fin, _ = actual_fixture()
    obj = json.loads(lab.FRESH_PHRASES.read_text())
    p = tmp_path / "holdout.json"
    obj["axes"]["authority"]["positive"][0] = prior_fin["authority"]["positive"][0]
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="TEXT_REUSE"):
        lab.read_fresh_phrases(p, space, prior_sel, prior_fin)
    obj["axes"]["authority"]["positive"][0] = "The subject possesses authorization for this exercise"
    obj["axes"]["authority"]["positive"] = [obj["axes"]["authority"]["positive"][0]]
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="TWO_PER_STATE"):
        lab.read_fresh_phrases(p, space, prior_sel, prior_fin)
    obj["axes"].pop("resource")
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="SCHEMA_OR_AXES"):
        lab.read_fresh_phrases(p, space, prior_sel, prior_fin)


def test_fresh_rows_have_matched_polarity_and_new_wrappers():
    space, prior_sel, prior_fin, new = actual_fixture()
    axes = [x["id"] for x in space["dimensions"]]
    rows = lab.make_new_rows(axes, new)
    old = layers.polarity_only_rows(axes, prior_fin, contexts=4, seed=2307)
    assert len(rows) == len(old) == 512
    assert len(set(r["group_id"] for r in rows)) == 128
    assert all(r["state"] != "omitted" for r in rows)
    assert all(lab.NEW_NEUTRAL in r["prompt"] for r in rows)
    assert not ({r["prompt"] for r in rows} & {r["prompt"] for r in old})
    for i in range(0, len(rows), 4):
        part = rows[i:i+4]
        assert [r["state"] for r in part] == ["affirmed", "denied", "affirmed", "denied"]
        assert len({r["prompt"].count(". ") for r in part}) == 1
        assert part[0]["states"] != part[1]["states"]
        assert part[2]["states"] != part[3]["states"]
    with pytest.raises(ValueError, match="EIGHT_AXES"):
        lab.make_new_rows(axes[:3], new)


def test_fixed_retrieval_metric_and_batch_meta():
    arr = np.eye(2)[[0, 1, 0, 1]]
    values = lab.four_margins(arr)
    assert np.allclose(values, np.ones(4))
    assert layers.metric_group(arr)["correct"] == 4
    assert not (lab.four_margins(np.ones((4, 2))) > lab.EPS).any()
    with pytest.raises(ValueError, match="FOUR_PER_GROUP"):
        lab.four_margins(np.ones((3, 2)))
    space, _, _, new = actual_fixture()
    axes = [d["id"] for d in space["dimensions"]]
    rows = lab.make_new_rows(axes, new)
    features = np.tile(arr, (128, 1))
    scored = lab.score(features, rows, axes)
    assert scored["summary"]["polarity_accuracy"] == 1.0
    assert all(value == 1.0 for value in scored["summary"]["by_depth"].values())
    assert all(value == 1.0 for value in scored["summary"]["by_axis"].values())
    assert scored["summary"]["comparisons"] == 512
    assert scored["summary"]["near_tie_fraction_by_abs_tolerance"]["1e-10"] == 0


def test_projection_numerics_tiny_margins_are_audited():
    query = np.array([[1., .00001], [1., -.00001], [1., .000011], [1., -.000011]])
    vocab = np.array([[1., 0.], [1., 0.0001], [0., 1.], [0., -1.]])
    outputs = {prec: lab.project(query, vocab, prec) for prec in ("fp64", "fp32", "fp16_storage")}
    assert all(a.shape == (4, 4) and np.isfinite(a).all() for a in outputs.values())
    assert np.linalg.norm(outputs["fp64"]-outputs["fp16_storage"]) > 0
    with pytest.raises(ValueError, match="UNKNOWN_PRECISION"):
        lab.project(query, vocab, "special")
    with pytest.raises(ValueError, match="COLLAPSED_INPUT_VECTOR"):
        lab.project(np.zeros_like(query), vocab, "fp64")
    report_rows = [{"axis":"authority", "depth":1} for _ in range(4)]
    a = lab.score(outputs["fp64"], report_rows, ["authority"])
    b = lab.score(outputs["fp16_storage"], report_rows, ["authority"])
    result = lab.precision_sensitivity(a,b)
    assert 0 <= result["comparison_flip_rate"] <= 1
    assert result["max_abs_margin_change"] >= 0


def test_paired_bootstrap_cluster_by_axis_and_context():
    space, _, _, new = actual_fixture()
    axes = [d["id"] for d in space["dimensions"]]
    rows = lab.make_new_rows(axes, new)
    good = lab.score(np.tile(np.eye(2)[[0,1,0,1]], (128,1)),rows,axes)
    bad = lab.score(np.tile(np.ones((4,2)), (128,1)),rows,axes)
    result = lab.paired_improvement(good,bad,axes,trials=40)
    assert result["candidate_minus_baseline"] == 1
    assert result["axis_cluster_bootstrap_95pct"] == [1,1]
    assert all(v==1 for v in result["by_depth"].values())
    with pytest.raises(ValueError,match="BOOTSTRAP_INVALID"):
        lab.paired_improvement(good,bad,axes,trials=2)


def source_files(tmp_path: Path):
    """Synthetic reports matching legacy source contracts, never real weights."""
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    vocab = [{"id":f"v{i+1:04d}",
              "description":f"Synthetic vocabulary description {i+1:04d} for axis {axes[i%8]}",
              "dimensions":[axes[i%8]]} for i in range(256)]
    b = tmp_path/"baseline.json"
    baseline = {"schema": smoke.SCHEMA,
                "status":"COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256":smoke.seal(space),
                "vocabulary":vocab[:24], "selected_vocabulary_size":24,
                "candidate_vocabulary_sha256":smoke.seal(vocab[:24]),
                "candidate_vocabulary_approved_or_sealed":False,
                "human_dimensions":axes, "model":{"backbone":smoke.MODEL_ID,"frozen":True},
                "calibration":{"train_count":90,"selection_count":24,"final_test_count":36,"seed":41},
                "measurement":{"ridge":0.5}}
    b.write_text(json.dumps(baseline))
    g = tmp_path/"growth.json"
    growth = {"schema":lab.training.growth.SCHEMA,
              "acceptance":{"never_authorizes_contract_seal":True},
              "model":{"backbone":smoke.MODEL_ID,"frozen":True},
              "experiment":{"sizes":[24,36,48,72,96]},
              "all_candidate_vocabulary_unapproved_unsealed":vocab[:96]}
    g.write_text(json.dumps(growth))
    e = tmp_path/"exact.json"
    exact = {"schema":lab.training.exact.SCHEMA,
             "status":"COMPLETE_EXACT_GROWTH_BAKEOFF_NO_SEAL_NO_PROMOTION",
             "acceptance":{"never_authorizes_contract_seal":True,"never_updates_model_weights":True},
             "model":{"backbone":smoke.MODEL_ID,"frozen":True},
             "experiment":{"sizes":[96,128,192,256]},
             "selection":{"selected_entries":256},
             "sources":{"baseline_sha256":sha256(b.read_bytes()).hexdigest(),
                        "growth_sha256":sha256(g.read_bytes()).hexdigest(),
                        "human_space_sha256":smoke.seal(space),
                        "retained_vocabulary_96_sha256":smoke.seal(vocab[:96])},
             "unapproved_unsealed_candidate_vocabulary":vocab,
             "unapproved_vocabulary_sha256":smoke.seal(vocab)}
    e.write_text(json.dumps(exact))
    prior=tmp_path/"prior.json"
    pp={"schema":layers.SCHEMA,
        "status":"COMPLETE_POLARITY_LAYER_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance":{"never_updates_model_weights":True,"vocabulary_unchanged":True},
        "sources":{"vocabulary_sha256":smoke.seal(vocab), "space_sha256":smoke.seal(space),
                   "exact_report_sha256":sha256(e.read_bytes()).hexdigest(),
                   "selection_phrases_sha256":sha256(geometry.DEFAULT_PROBES.read_bytes()).hexdigest(),
                   "final_phrases_sha256":sha256(layers.FINAL_PHRASES.read_bytes()).hexdigest()},
        "experiment":{"baseline":lab.BASELINE,"candidates":list(layers.keys())},
        "selected":{"extraction":"quarter:mean","final_labels_used_in_selection":False},
        "final":{"direct":{k:{"polarity_accuracy":.75} for k in lab.CONTENDERS},
                 "vocabulary_similarity":{k:{"polarity_accuracy":.5} for k in lab.CONTENDERS}}}
    prior.write_text(json.dumps(pp))
    return space,axes,b,g,e,prior


def frozen_toy_encoder(axes: list[str], phrases: dict):
    def encode(texts):
        base = np.zeros((len(texts), 12),dtype=float)
        base[:,10]=1
        for i,t in enumerate(texts):
            for k,axis in enumerate(axes):
                if any(s in t for s in phrases[axis]["positive"]):
                    base[i,k] = 1
                if any(s in t for s in phrases[axis]["negative"]):
                    base[i,k] = -1
        flat = np.abs(base)
        return {"half:last":base,"final:mean":flat}
    return encode


def test_mock_end_to_end_prespecified_two_view_holdout(tmp_path,monkeypatch):
    space,axes,b,g,e,p = source_files(tmp_path)
    _,oldsel,oldfin,new = actual_fixture()
    enc=frozen_toy_encoder(axes,new)
    monkeypatch.setattr(smoke,"fit_kernel_readout",lambda *a,**k: (_ for _ in ()).throw(AssertionError("must not fit")))
    args=lab.parser().parse_args(["--baseline-report",str(b),"--growth-report",str(g),
       "--exact-report",str(e),"--prior-report",str(p),"--bootstrap-trials","40",
       "--output",str(tmp_path/"result.json")])
    report=lab.run(args,encoder=enc)
    assert report["results"][lab.CANDIDATE]["direct"]["polarity_accuracy"] > .5
    assert report["results"][lab.BASELINE]["direct"]["polarity_accuracy"] <= .5
    assert report["acceptance"]["candidate_not_promoted"] is True
    assert report["design"]["selection_on_new_holdout"] is False
    assert report["query_batch_sensitivity"]["status"] == "NOT_AVAILABLE_FOR_INJECTED_ENCODER"
    assert report["results"][lab.CANDIDATE]["projection_numeric_stability"]["fp32"]
    assert json.loads(args.output.read_text())["schema"] == lab.SCHEMA
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        lab.run(args,encoder=enc)
    args.output.unlink()
    prior = json.loads(p.read_text())
    prior["sources"]["vocabulary_sha256"] = "bad"
    p.write_text(json.dumps(prior))
    with pytest.raises(ValueError,match="PRIOR_SOURCE_OR_METHOD_MISMATCH"):
        lab.run(args,encoder=enc)


def test_mock_batch_sensitivity_reembeds_without_model_mutation():
    # Keep the batch audit independently testable without a heavyweight model.
    _, _, _, new = actual_fixture()
    axes = list(new)
    rows = lab.make_new_rows(axes,new)
    texts, _, idx = layers.unique_texts(rows,new,axes)
    class SmallEncoder:
        batch_size=4
        def __call__(self, texts):
            base=frozen_toy_encoder(axes,new)(texts)
            return {**base}
    enc=SmallEncoder()
    original=enc(texts)
    vocab=enc(["Synthetic overlap vocabulary" for _ in range(256)])
    result=lab.batch_audit(enc,original,texts,rows,idx,vocab,axes)
    assert result["status"]=="COMPLETE"
    assert result["groups"]==32 and result["query_count"]==128
    assert enc.batch_size==4
    assert result["views"][lab.CANDIDATE]["direct_retrieval_flip_rate"]==0


def test_numeric_precision_can_flip_near_tied_retrieval():
    reference = {"margins":np.array([[1e-11,-1e-11, .5, -.5]]),
                 "correct":np.array([[False,False,True,False]]),
                 "summary":{"polarity_accuracy":.25,
                            "near_tie_fraction_by_abs_tolerance":{"1e-10":.5}}}
    perturbed = {"margins":np.array([[2e-9,2e-9,.5,-.5]]),
                 "correct":np.array([[True,True,True,False]]),
                 "summary":{"polarity_accuracy":.75,
                            "near_tie_fraction_by_abs_tolerance":{"1e-10":0}}}
    stability = lab.precision_sensitivity(reference, perturbed)
    assert stability["comparison_flip_rate"] == .5
    assert stability["polarity_accuracy_delta_vs_fp64"] == .5
    assert stability["comparison_flip_rate_outside_reference_near_ties"] == 0.0


def test_prior_report_requires_unmodified_holdout_sources(tmp_path):
    space, axes, b,g,e,p = source_files(tmp_path)
    _, old_sel, old_fin, fresh = actual_fixture()
    obj=json.loads(p.read_text())
    obj["sources"]["final_phrases_sha256"]="changed"
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="PRIOR_SOURCE_OR_METHOD_MISMATCH"):
        lab.validate_prior(p, space, json.loads(e.read_text())["unapproved_unsealed_candidate_vocabulary"],
                exact=e,selection=geometry.DEFAULT_PROBES,prior_final=layers.FINAL_PHRASES)
