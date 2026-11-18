"""Offline and synthetic controls for the new stress bakeoff; never Qwen claims."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_language_stress_bakeoff as lab
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as prior


def fixtures():
    base=prior.load_fixture(prior.FIXTURE)
    stress=lab.load_fixture(lab.FIXTURE,base)
    return base,stress


def data(*,train_atoms=96,selection_groups=8,final_groups=8):
    base,stress=fixtures()
    return lab.derive_data(base,stress,seed=261117,train_atoms=train_atoms,
                           selection_groups=selection_groups,final_groups=final_groups)


def lexical_mock(base):
    """Text-only deterministic controls; no access to labels or fixture metadata.

    Recognizes known lexical concepts, not the positions of entities or
    grammatical scope. This intentionally fails OOV and coreference probes.
    """
    words=[(j,phrase) for j,opts in enumerate(prior.values(base)[:-1]) for phrase in opts]
    d=len(words)+4
    def encode(texts):
        xs=np.zeros((len(texts),d))
        for i,text in enumerate(texts):
            for j,(field,word) in enumerate(words):
                if word in text:xs[i,j]=1.
            if any(w in text for w in ("has no obligation", "does not impose", "is not required", "does not obligate")):
                xs[i,-2]=1.
            elif any(w in text for w in ("is obligated", "imposes a duty", "is required", "obligates")):
                xs[i,-1]=1.
            xs[i,-4]=1.
        return {"final:mean":xs,"half:last":np.column_stack([xs[:,:-4],xs[:,-4:]*1.02])}
    return encode


def fake_source(tmp_path):
    p=tmp_path/"prior.json"
    p.write_text(json.dumps({"schema":prior.SCHEMA,"status":prior.STATUS,
        "acceptance":{"no_production_mutation":True,"no_final_based_model_selection":True},
        "design":{"same_training_texts_and_six_field_labels_for_both_arms":True,
                  "fields":list(prior.FIELDS),"views":list(lab.VIEWS),"training_atomic_clauses":96},
        "sources":{"fixture_sha256":sha256(prior.FIXTURE.read_bytes()).hexdigest()},
        "final":{"final:mean":{"joint":{},"factorized":{}}}}))
    return p


def test_fresh_grammar_and_fresh_values():
    base,stress=fixtures()
    old=sum((base["templates"][phase] for phase in ("train","selection","final")),[])
    assert set(stress["selection_templates"]+stress["final_templates"]).isdisjoint(old)
    assert not set(stress["new_actors"]) & (set(base["actors"])|set(base["recipients"]))
    assert not set(stress["new_recipients"]) & (set(base["actors"])|set(base["recipients"]))
    assert "{first}" in stress["nested_condition_connector"]


def test_fixture_safety_rejects_data_leaks(tmp_path):
    base,stress=fixtures()
    stress["new_actors"][0]=base["actors"][0]
    p=tmp_path/"fixture.json";p.write_text(json.dumps(stress))
    with pytest.raises(ValueError,match="OOV_NAMES_NOT_FRESH"):
        lab.load_fixture(p,base)
    stress=fixtures()[1]
    stress["final_templates"][0]=base["templates"]["train"][0]
    p.write_text(json.dumps(stress))
    with pytest.raises(ValueError,match="TEMPLATES_NOT_FRESH"):
        lab.load_fixture(p,base)
    stress=fixtures()[1]
    stress["final_templates"][0]="{actor} {asset}"
    p.write_text(json.dumps(stress))
    with pytest.raises(ValueError,match="TEMPLATE_ROLE_INVALID"):
        lab.load_fixture(p,base)


def test_dataset_preserves_gold_under_swaps_and_out_of_schema():
    d=data()
    assert len(d["train"])==96
    assert len(d["selection"])==8*len(lab.KINDS)
    assert len(d["final"])==8*len(lab.KINDS)
    base,_=fixtures()
    previous=prior.make_dataset(base,seed=261117,train_atoms=96,selection_groups=8,final_groups=8)
    assert d["train"]==previous["train"]  # no training provenance drift
    old_pairs={tuple(sorted(tuple(r["atoms"]))) for phase in ("selection","final") for r in previous[phase] if r["kind"]=="base"}
    new_pairs={tuple(sorted(tuple(r["atoms"]))) for phase in ("selection","final") for r in d[phase] if r["kind"]=="base"}
    assert old_pairs.isdisjoint(new_pairs) and len(new_pairs)==16
    assert {x["text"] for x in d["selection"]}.isdisjoint({x["text"] for x in d["final"]})
    assert all(len(prior.split_document(x["text"]))==len(x["atoms"]) for p in ("selection","final") for x in d[p])
    for p in ("selection","final"):
        cases={x["kind"]:x for x in d[p] if x["group_id"]==0}
        assert set(cases)==set(lab.KINDS)
        for name in prior.SWAPS:
            assert prior.inventory(cases["base"]["atoms"])==prior.inventory(cases["swap_"+name]["atoms"])
            assert prior.contract_graph(cases["base"]["atoms"])!=prior.contract_graph(cases["swap_"+name]["atoms"])
        assert cases["anaphoric_actor"]["atoms"][0][0]==cases["anaphoric_actor"]["atoms"][1][0]
        assert "the same supplier" in cases["anaphoric_actor"]["text"]
        assert "the conjunction" in cases["nested_condition"]["text"]
        assert cases["oov_actor"]["scope"]=="out_of_schema"
        assert cases["base"]["scope"]=="in_schema"
        assert prior.has_contradiction(cases["contradiction"]["atoms"])
        assert not prior.has_contradiction(cases["different_condition"]["atoms"])


def test_pairwise_can_emit_unseen_cartesian_tuples():
    base,_=fixtures();d=data()
    mock=lexical_mock(base)
    atoms=[r["atom"] for r in d["train"]]
    x=mock([r["text"] for r in d["train"]])["final:mean"]
    fitted=lab.fit_structured_model(x,atoms,base,ridge=.1)
    assert fitted["legal_output_tuple_count"]>fitted["training_tuple_count"]
    assert len(set(fitted["choices"]) - set(atoms))>0
    assert len(fitted["pair_intervals"])==15
    out=lab.predict(fitted,x[:3],base,pair_weight=.75)
    assert set(out)==set(lab.ARMS)
    assert len(out["structured_pairwise"])==3
    with pytest.raises(ValueError,match="PAIR_WEIGHT_INVALID"):
        lab.predict(fitted,x[:3],base,pair_weight=-1)


def test_exact_canonical_assembly_detects_matched_scope_only():
    base,_=fixtures();a=(base["actors"][0],base["recipients"][0],base["assets"][0],base["conditions"][0],base["consequences"][0],1)
    neg=a[:-1]+(-1,)
    assert prior.has_contradiction([a,neg])
    assert not prior.has_contradiction([a,neg[:3]+(base["conditions"][1],)+neg[4:]])


def test_pairwise_zero_weight_is_factorized_prediction_under_no_ties():
    base,_=fixtures();d=data()
    encoder=lexical_mock(base)
    x=encoder([r["text"] for r in d["train"]])["final:mean"]
    m=lab.fit_structured_model(x,[r["atom"] for r in d["train"]],base,ridge=.1)
    output=lab.predict(m,x,base,pair_weight=0.0)
    assert output["factorized"]==output["structured_pairwise"]


def test_full_runner_synthetic_and_no_overwrite(tmp_path):
    base,_=fixtures();prior_path=fake_source(tmp_path)
    output=tmp_path/"new.json"
    args=lab.parser().parse_args(["--device","cpu","--output",str(output),
        "--prior-report",str(prior_path),"--train-atoms","96",
        "--selection-groups","8","--final-groups","8"])
    result=lab.run(args,encoder=lexical_mock(base))
    assert result["status"]==lab.STATUS
    assert result["design"]["same_six_field_supervision"]
    assert result["design"]["structured_pairwise_can_output_unseen_tuples"]
    assert result["design"]["pairwise_weight_locked_before_final"] in lab.PAIR_WEIGHTS
    assert result["final_stats"]["by_challenge_representability_ceiling"]["oov_actor"]["full_graph_representable_fraction"]==0
    assert result["final_stats"]["by_challenge_representability_ceiling"]["nested_condition"]["full_graph_representable_fraction"]==0
    assert result["final_stats"]["by_challenge_representability_ceiling"]["base"]["full_graph_representable_fraction"]==1
    assert result["final_stats"]["oracle_graph_exact"]==1
    assert result["final_stats"]["oracle_true_contradictions_detected"]==1
    assert all(v==1 for v in result["final_stats"]["oracle_inventory_collision_on_swaps"].values())
    assert result["design"]["training_atomic_clauses"]==96
    assert result["design"]["fresh_semantic_base_pairs_excluding_prior_inspected_pairs"]
    assert len(result["pairwise_selection_scan"])==3
    assert len(result["final"]["final:mean"]["factorized"]["by_challenge"])==len(lab.KINDS)
    assert json.loads(output.read_text())["schema"]==lab.SCHEMA
    with pytest.raises(FileExistsError,match="OUTPUT_EXISTS"):
        lab.run(args,encoder=lexical_mock(base))


def test_tampered_previous_report_rejected(tmp_path):
    path=fake_source(tmp_path)
    assert lab.validate_prior_report(path)["schema"]==prior.SCHEMA
    raw=json.loads(path.read_text());raw["acceptance"]["no_production_mutation"]=False
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match="PROVENANCE_INVALID"):
        lab.validate_prior_report(path)


def test_prior_training_contract_mismatch_rejected(tmp_path):
    base,_=fixtures();source=fake_source(tmp_path)
    raw=json.loads(source.read_text());raw["sources"]["fixture_sha256"]="bad"
    source.write_text(json.dumps(raw))
    args=lab.parser().parse_args(["--device","cpu","--output",str(tmp_path/"output.json"),
        "--prior-report",str(source),"--train-atoms","96", "--selection-groups","8","--final-groups","8"])
    with pytest.raises(ValueError,match="TRAINING_CONTRACT_CHANGED"):
        lab.run(args,encoder=lexical_mock(base))
