"""Offline tests for equal-supervision NanoJev relation-binding bakeoff."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_relation_binding_bakeoff as lab


def fixture(): return lab.load_fixture(lab.FIXTURE)


def dataset(*, train_atoms=216, selection_groups=16, final_groups=24):
    return lab.make_dataset(fixture(),seed=261117,train_atoms=train_atoms,
                            selection_groups=selection_groups,final_groups=final_groups)


def lexical_mock(f):
    """Synthetic deterministic vectorizer of TEXT ONLY (not metadata labels)."""
    voc=lab.values(f)
    base=[]
    for names in voc[:-1]: base.extend(names)
    width=len(base)+2
    def encode(texts):
        x=np.zeros((len(texts),width))
        for i,s in enumerate(texts):
            for j,phrase in enumerate(base):
                if phrase in s:x[i,j]=1.
            if "not required to" in s or "does not obligate" in s:
                x[i,-2]=1.
            elif "is required to" in s or "obligates" in s:
                x[i,-1]=1.
        if (np.linalg.norm(x,axis=1)<1e-12).any():
            raise ValueError("MOCK_COULD_NOT_READ_CLAUSE")
        return {"final:mean":x,"half:last":np.column_stack([x[:,:-2],x[:,-2:]*1.5])}
    return encode


def fake_provenance(tmp_path:Path):
    obj={"schema":"nanojev.sealed-overlap.compositional-architecture-bakeoff.v1",
         "status":"COMPLETE_COMPOSITION_BAKEOFF_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
         "acceptance":{"no_production_mutation":True,"no_final_based_model_selection":True},
         "design":{"atomic_methods_have_extra_clause_level_training_supervision":True},
         "final":{"final:mean":{"slot_factor_rbf":{},"slot_joint_rbf":{}}}}
    p=tmp_path/"composition.json";p.write_text(json.dumps(obj));return p


def test_fixture_all_slots_and_fresh_templates():
    f=fixture()
    assert len(lab.values(f))==6
    assert len(f["templates"]["train"])==3
    assert set(f["templates"]["train"]).isdisjoint(f["templates"]["selection"])
    assert set(f["templates"]["train"]).isdisjoint(f["templates"]["final"])
    a=("Mira","Arlen","sensor","the permit is approved","access is suspended",1)
    p=lab.render_atom(a,f,"final")
    assert "Mira" in p and "Arlen" in p and "oblig" not in p
    n=lab.render_atom(a[:-1]+(-1,),f,"final")
    assert n!=p and "not required" in n
    with pytest.raises(ValueError,match="INVALID_ATOM"):
        lab.render_atom(a[:-1]+(0,),f,"final")


def test_fixture_rejects_silent_loss_of_role_or_unsafe_delimiter(tmp_path):
    f=fixture();f["templates"]["final"][0]="{actor} is required"
    p=tmp_path/"f.json";p.write_text(json.dumps(f))
    with pytest.raises(ValueError,match="ROLE_COVERAGE"):
        lab.load_fixture(p)
    f=fixture();f["document_delimiter"]=". "
    p.write_text(json.dumps(f))
    with pytest.raises(ValueError,match="DELIMITER_CHANGED"):
        lab.load_fixture(p)


def test_dataset_bounded_split_and_seen_unseen_halves():
    d=dataset()
    assert len(d["train"])==216
    assert len(d["selection"])==16*11 and len(d["final"])==24*11
    assert len({x["atom"] for x in d["train"]})==216
    assert set(x["text"] for x in d["train"]).isdisjoint(x["text"] for x in d["final"])
    assert set(x["text"] for x in d["selection"]).isdisjoint(x["text"] for x in d["final"])
    for phase in ("selection","final"):
        groups={}
        for r in d[phase]: groups.setdefault(r["group_id"],{})[r["kind"]]=r
        assert len(groups)==(16 if phase=="selection" else 24)
        assert all(len(g)==11 for g in groups.values())
        assert sum(g["base"]["base_atoms_seen_in_train"] for g in groups.values())==len(groups)//2
    with pytest.raises(ValueError,match="SIZE_INVALID"):
        lab.make_dataset(fixture(),seed=1,train_atoms=10,selection_groups=8,final_groups=8)


def test_lossless_fixture_delimiter_without_semantic_clues():
    s=lab.document(["Alice must run", "Bob is not required to run"])
    assert lab.split_document(s)==["Alice must run","Bob is not required to run"]
    with pytest.raises(ValueError,match="DOCUMENT"):
        lab.split_document("The statement has no trailing punctuation")
    with pytest.raises(ValueError,match="DOCUMENT_CLAUSES"):
        lab.document(["Has || separator"])


def test_swaps_have_same_unbound_inventory_but_different_binding():
    f=fixture();a=("Mira","Arlen","sensor",f["conditions"][0],f["consequences"][0],1)
    b=("Tarin","Bela","ledger",f["conditions"][1],f["consequences"][1],-1)
    variants=lab.case_variants(a,b,f)
    assert len(variants)==11
    for field in lab.SWAPS:
        assert lab.inventory(variants["base"])==lab.inventory(variants["swap_"+field])
        assert lab.contract_graph(variants["base"])!=lab.contract_graph(variants["swap_"+field])
    assert lab.has_contradiction(variants["contradiction"])
    assert not lab.has_contradiction(variants["different_condition"])
    assert not lab.has_contradiction(variants["base"])
    assert lab.contract_graph(variants["reordered"])==lab.contract_graph(variants["base"])


def test_scope_does_not_conflate_explicit_denial_and_absence():
    f=fixture();a=("Mira","Arlen","sensor",f["conditions"][0],f["consequences"][0],1)
    b=a[:-1]+(-1,)
    assert lab.has_contradiction([a,b])
    assert not lab.has_contradiction([a,b[:-3]+(f["conditions"][1],)+b[-2:]])
    assert not lab.has_contradiction([a])
    assert lab.contract_graph([a])!=lab.contract_graph([b])
    assert lab.contract_graph([a])!=lab.contract_graph([])


def test_oracle_composition_is_exact_and_binding_ablation_collides():
    d=dataset()
    metrics=lab.scenario_scores(d["final"],[r["atoms"] for r in d["final"]])
    assert metrics["complete_relation_graph_exact"]==1.
    assert metrics["true_contradiction_detected_rate"]==1.
    assert metrics["different_condition_no_false_contradiction_rate"]==1.
    assert metrics["omission_one_relation_drop_rate"]==1.
    assert metrics["permutation_invariance_rate"]==1.
    assert all(k["gold_unbound_inventory_collision_rate"]==1. for k in metrics["binding_swaps"].values())
    assert all(k["both_documents_relation_exact"]==1. for k in metrics["binding_swaps"].values())


def test_identical_kernel_and_training_labels_for_both_arms():
    f=fixture();d=dataset(train_atoms=96,selection_groups=8,final_groups=8)
    encoder=lexical_mock(f)
    train=[x["atom"] for x in d["train"]]
    x=encoder([x["text"] for x in d["train"]])["final:mean"]
    model=lab.fit_shared_supervision(x,train,f,ridge=.1)
    assert model["training_count"]==96
    assert model["joint"][1]-model["joint"][0]==96
    assert model["label_fields"]==list(lab.FIELDS)
    pred=lab.predict_atomic(model,x,f)
    assert set(pred)==set(lab.ARMS)
    assert len(pred["factorized"])==len(pred["joint"])==96
    assert lab.atomic_scores(train,pred["factorized"],set(train))["count"]==96
    with pytest.raises(ValueError,match="TRAINING_INVALID"):
        lab.fit_shared_supervision(x,train,f,ridge=0)


def test_source_provenance_validation(tmp_path):
    p=fake_provenance(tmp_path)
    assert lab.validate_source(p)["schema"].endswith("bakeoff.v1")
    obj=json.loads(p.read_text());obj["design"]["atomic_methods_have_extra_clause_level_training_supervision"]=False
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="PROVENANCE_INVALID"):
        lab.validate_source(p)


def test_full_mock_runner_and_output_no_overwrite(tmp_path):
    f=fixture();p=fake_provenance(tmp_path)
    out=tmp_path/"binding.json"
    args=lab.parser().parse_args(["--device","cpu","--composition-report",str(p),"--output",str(out),
        "--train-atoms","96","--selection-groups","8","--final-groups","8"])
    r=lab.run(args,encoder=lexical_mock(f))
    assert r["status"]==lab.STATUS
    assert r["design"]["same_training_texts_and_six_field_labels_for_both_arms"]
    assert r["design"]["same_rbf_kernel_and_ridge_for_both_arms"]
    assert r["final_stats"]["oracle_structural_control"]["complete_relation_graph_exact"]==1.
    assert all(set(r["final"][view])==set(lab.ARMS) for view in lab.VIEWS)
    assert r["final_stats"]["unseen_tuple_count"]>0
    assert all(len(r["final"][view][arm]["atomic"]["by_field"])==6
               for view in lab.VIEWS for arm in lab.ARMS)
    assert r["final"]["final:mean"]["factorized"]["atomic"]["all_six_fields_exact"] > 0.5
    assert json.loads(out.read_text())["schema"]==lab.SCHEMA
    with pytest.raises(FileExistsError,match="OUTPUT_EXISTS"):
        lab.run(args,encoder=lexical_mock(f))
