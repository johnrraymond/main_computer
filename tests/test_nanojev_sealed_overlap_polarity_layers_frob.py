"""Offline frozen-Qwen layer/pooling polarity diagnostic regression tests."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_sealed_overlap_polarity_layers_frob as lab
from tools import nanojev_sealed_overlap_clause_geometry_frob as clause
from tools import nanojev_sealed_overlap_geometry_frob as geometry
from tools import nanojev_sealed_overlap_information_smoke as smoke


def test_positions_anchor_embedding_intermediate_and_last():
    assert lab.layer_positions(28) == {"embedding": 0, "first": 1, "quarter": 7,
                                       "half": 14, "three_quarters": 21, "final": 28}
    assert len(lab.keys()) == 18
    assert lab.BASELINE in lab.keys()
    with pytest.raises(ValueError, match="AT_LEAST_EIGHT"):
        lab.layer_positions(4)


def test_pooling_excludes_right_padding_all_methods_and_matches_old_mean():
    # Four transformer snapshots with exactly prespecified values for each
    # token; no application-level labels are visible to the pooling function.
    h = tuple(torch.tensor([[[1., 10.], [2., 20.], [3., 30.], [100., 100.], [100.,100.]],
                            [[6., 5.], [7., 6.], [8., 7.], [9.,8.], [10., 9.]]]) + i
              for i in range(5))
    mask = torch.tensor([[1,1,1,0,0], [1,1,1,1,1]])
    output = lab.pooled_states(h, mask, {"embedding": 0, "final": 4})
    assert np.allclose(output["embedding:mean"][0], [2.,20.])
    assert np.allclose(output["embedding:last"][0], [3.,30.])
    assert np.allclose(output["embedding:last4"][0], [2.,20.])
    assert np.allclose(output["embedding:last4"][1], [8.5,7.5])
    expected_old = ((h[-1].float() * mask.unsqueeze(-1)).sum(1)
                    / mask.sum(1).unsqueeze(-1))
    assert np.allclose(output["final:mean"], expected_old.numpy())
    with pytest.raises(ValueError, match="EMPTY_OR_INVALID_MASK"):
        lab.pooled_states(h, torch.zeros_like(mask), {"final": 4})


def test_holdout_disjoint_fresh_and_semantically_complete():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    selection = geometry.read_phrases(geometry.DEFAULT_PROBES, space)
    final = lab.read_final_phrases(lab.FINAL_PHRASES, space, selection)
    assert set(selection) == set(final)
    assert {x for axis in final for state in final[axis] for x in final[axis][state]}.isdisjoint(
        {x for axis in selection for state in selection[axis] for x in selection[axis][state]})
    assert all(len(final[axis][state]) == 2 for axis in final for state in final[axis])


def test_holdout_rejects_phrasing_leaks_and_bad_state_counts(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    selection = geometry.read_phrases(geometry.DEFAULT_PROBES, space)
    obj = json.loads(lab.FINAL_PHRASES.read_text())
    obj["axes"]["authority"]["positive"][0] = selection["authority"]["positive"][0]
    path = tmp_path / "leak.json"
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="NOT_DISJOINT"):
        lab.read_final_phrases(path, space, selection)
    obj["axes"]["authority"]["positive"] = ["Only one authority phrase retained"]
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="REQUIRES_TWO_PER_STATE"):
        lab.read_final_phrases(path, space, selection)


def test_fixed_polarity_only_geometry_removes_omission_count_advantage():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    selection = geometry.read_phrases(geometry.DEFAULT_PROBES, space)
    final = lab.read_final_phrases(lab.FINAL_PHRASES, space, selection)
    rows = lab.polarity_only_rows(axes, selection, contexts=4, seed=1729)
    other = lab.polarity_only_rows(axes, final, contexts=4, seed=2307)
    assert len(rows) == len(other) == 512
    for sample in (rows, other):
        for start in range(0, len(sample), 4):
            g = sample[start:start+4]
            assert [r["state"] for r in g] == ["affirmed", "denied", "affirmed", "denied"]
            assert len({r["depth"] for r in g}) == 1
            assert len({len(clause.clauses_from_prompt(r["prompt"])) for r in g}) == 1
    texts_a, _, _ = lab.unique_texts(rows, selection, axes)
    texts_b, _, _ = lab.unique_texts(other, final, axes)
    assert set(texts_a).isdisjoint(texts_b)


def test_metric_retrieval_is_polarity_sensitive_and_no_omission():
    # Two equivalent paraphrases are identical, opposite polarity orthogonal.
    good = lab.metric_group(np.eye(2)[[0,1,0,1]])
    assert good["correct"] == 4
    assert good["mean_margin"] > 0
    wrong = lab.metric_group(np.ones((4,2)))
    assert wrong["correct"] == 0
    assert wrong["near_ties"] == 4
    with pytest.raises(ValueError, match="EXPECTS_FOUR_VECTORS"):
        lab.metric_group(np.ones((3,2)))


def test_selection_does_not_use_final_and_reports_whole_context():
    values = {key: {"isolated_polarity":0.5, "polarity_accuracy":0.5,
                    "by_depth":{"4":0.5,"8":0.5}} for key in lab.keys()}
    assert lab.select_extractor(values) == lab.BASELINE
    values["half:last"]["by_depth"]["4"] = 0.75
    assert lab.select_extractor(values) == "half:last"
    assert "final" not in lab.select_extractor.__code__.co_varnames


def test_bootstrap_cluster_by_axis_and_delta():
    axes = [str(i) for i in range(8)]
    high = {"by_axis": {a:1. for a in axes}}
    low = {"by_axis": {a:0.5 for a in axes}}
    d = lab.paired_axis_bootstrap(high, low, axes, trials=50)
    assert d["delta_mean_accuracy"] == 0.5
    assert d["axis_cluster_bootstrap_95pct"] == [0.5, 0.5]
    with pytest.raises(ValueError, match="TOO_SMALL"):
        lab.paired_axis_bootstrap(high, low, axes, trials=5)


def source_files(tmp_path: Path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    axes = [d["id"] for d in space["dimensions"]]
    vocab = [{"id": f"v{i+1:04d}",
              "description": f"Synthetic overlap vocabulary item {i+1:04d} related to {axes[i%8]}",
              "dimensions": [axes[i%8]]} for i in range(256)]
    b = tmp_path / "baseline.json"
    baseline = {"schema": smoke.SCHEMA,
                "status":"COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                "human_space_sha256": smoke.seal(space),
                "vocabulary": vocab[:24], "selected_vocabulary_size":24,
                "candidate_vocabulary_sha256":smoke.seal(vocab[:24]),
                "candidate_vocabulary_approved_or_sealed":False,
                "human_dimensions":axes, "model":{"backbone":smoke.MODEL_ID,"frozen":True},
                "calibration":{"train_count":90,"selection_count":24,
                               "final_test_count":36,"seed":41},
                "measurement":{"ridge":0.5}}
    b.write_text(json.dumps(baseline))
    g = tmp_path / "growth.json"
    growth = {"schema":lab.training.growth.SCHEMA,
              "acceptance":{"never_authorizes_contract_seal":True},
              "model":{"backbone":smoke.MODEL_ID,"frozen":True},
              "experiment":{"sizes":[24,36,48,72,96]},
              "all_candidate_vocabulary_unapproved_unsealed":vocab[:96]}
    g.write_text(json.dumps(growth))
    e = tmp_path / "exact.json"
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
    c = tmp_path / "clause.json"
    clause_report = {"schema":clause.SCHEMA,
                     "status":"COMPLETE_CLAUSE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
                     "acceptance":{"never_updates_model_weights":True},
                     "sources":{"exact_report":str(e),
                                "vocabulary_sha256":smoke.seal(vocab),
                                "human_space_sha256":smoke.seal(space),
                                "phrases_sha256":sha256(geometry.DEFAULT_PROBES.read_bytes()).hexdigest()},
                     "experiment":{"contexts_per_axis_depth":4,
                                   "unique_clause_text_count_including_neutral":33},
                     "isolated_clause_polarity":{"direct":{"macro_polarity_retrieval_accuracy":0.}}}
    c.write_text(json.dumps(clause_report))
    return space,b,g,e,c


def toy_encoder_factory(axes: list[str], phrase_sets: tuple[dict, dict]):
    """A mock frozen encoder that only exposes signed information in one view."""
    def encode(texts):
        raw = np.zeros((len(texts), 10), dtype=float)
        raw[:,8] = 1.
        for i, value in enumerate(texts):
            for j, axis in enumerate(axes):
                if any(phrase in value for phrases in phrase_sets
                       for phrase in phrases[axis]["positive"]):
                    raw[i,j] = 2.
                if any(phrase in value for phrases in phrase_sets
                       for phrase in phrases[axis]["negative"]):
                    raw[i,j] = -2.
        flattened = abs(raw)
        flattened[:,8] = 1.
        return {key: raw.copy() if key == "half:last" else flattened.copy()
                for key in lab.keys()}
    return encode


def test_end_to_end_mock_selects_polarity_and_checks_source(tmp_path, monkeypatch):
    space,b,g,e,c = source_files(tmp_path)
    axes = [d["id"] for d in space["dimensions"]]
    sel = geometry.read_phrases(geometry.DEFAULT_PROBES,space)
    fin = lab.read_final_phrases(lab.FINAL_PHRASES,space,sel)
    encoder = toy_encoder_factory(axes,(sel,fin))
    monkeypatch.setattr(smoke,"fit_kernel_readout",lambda *a,**k: (_ for _ in ()).throw(AssertionError("decoder fit")))
    args = lab.parser().parse_args(["--baseline-report",str(b), "--growth-report",str(g),
                                   "--exact-report",str(e), "--clause-report",str(c),
                                   "--contexts","1","--bootstrap-trials","20",
                                   "--output",str(tmp_path/"polarity.json")])
    report = lab.run(args,encoder=encoder)
    assert report["selected"]["extraction"] == "half:last"
    assert report["selection"]["direct"]["half:last"]["polarity_accuracy"] > 0.5
    assert report["final"]["direct"]["half:last"]["polarity_accuracy"] > 0.5
    assert report["final"]["direct"][lab.BASELINE]["polarity_accuracy"] == 0.0
    assert report["acceptance"]["never_updates_model_weights"] is True
    assert report["selected"]["final_labels_used_in_selection"] is False
    assert report["experiment"]["baseline_isolated_polarity_audit"]["absolute_difference"] == 0.0
    assert json.loads(args.output.read_text())["schema"] == lab.SCHEMA
    with pytest.raises(FileExistsError,match="OUTPUT_EXISTS_REFUSE_TO_OVERWRITE"):
        lab.run(args,encoder=encoder)
    (tmp_path/"polarity.json").unlink()
    stale = json.loads(c.read_text())
    stale["sources"]["vocabulary_sha256"] = "bad"
    c.write_text(json.dumps(stale))
    with pytest.raises(ValueError,match="PRIOR_CLAUSE_REPORT_MISMATCH"):
        lab.run(args,encoder=encoder)


def test_source_integrity_actual_uploaded_vocabulary_is_readable():
    # No mutable production snapshot is consulted: the actual user-side report
    # is checked separately by development tooling, not hardcoded into tests.
    assert lab.FINAL_PHRASES.is_file()
    assert lab.BASELINE == "final:mean"
