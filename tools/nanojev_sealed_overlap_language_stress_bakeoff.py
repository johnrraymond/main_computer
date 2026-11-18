#!/usr/bin/env python3
"""Novel-grammar relation-binding stress bakeoff: factorized vs generative pairwise slots.

Both heads use identical frozen Qwen text features, same clause-level six-field
supervision, same RBF Gram matrix and ridge. The comparator can generate full
six-field tuples unseen in training, unlike the historical joint-class head:
it learns all pairwise field couplings and globally scores the Cartesian product
of field values. A selection cohort chooses a pairwise coefficient before the
final cohort is evaluated; final labels never tune models or choose an arm.

A separate holdout reports six-field in-schema grammar tests and unsupported
OOV entity, conjunctive scope, and intra-document anaphora stress. The latter
failures are not included in the in-schema aggregate and MUST NOT be described
as learned open-vocabulary or coreference capabilities. Explicit fixture clause
boundaries remain; this is NOT a natural-language parser.

Run from project root:
  & $py -m tools.nanojev_sealed_overlap_language_stress_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-language-stress-0001.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import itertools
import json
import random
from pathlib import Path
from string import Formatter
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_composition_bakeoff as composition
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as prior

SCHEMA = "nanojev.sealed-overlap.language-stress-bakeoff.v1"
STATUS = "COMPLETE_LANGUAGE_STRESS_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "nanojev_overlap_language_stress_v1.json"
PRIOR_REPORT = Path("runtime/reports/sealed-overlap-relation-binding-0001.json")
VIEWS = ("final:mean", "half:last")
ARMS = ("factorized", "structured_pairwise")
PAIRS = tuple(itertools.combinations(range(len(prior.FIELDS)), 2))
PAIR_WEIGHTS = (0.25, 0.75, 1.5)
STRESS = ("oov_actor", "oov_recipient", "nested_condition", "anaphoric_actor")
KINDS = tuple(prior.case_variants(
    ("Mira","Arlen","sensor","the permit is approved","access is suspended",1),
    ("Tarin","Bela","ledger","the audit is complete","payment is withheld",-1),
    {}
).keys()) + STRESS


def load_fixture(path: Path, base: dict) -> dict:
    f = json.loads(path.read_text(encoding="utf-8"))
    if f.get("schema") != "nanojev.sealed-overlap.language-stress-fixture.v1":
        raise ValueError("LANGUAGE_STRESS_FIXTURE_SCHEMA_INVALID")
    if f.get("document_delimiter") != " || ":
        raise ValueError("LANGUAGE_STRESS_DELIMITER_INVALID")
    names = set(base["actors"]) | set(base["recipients"])
    for key in ("new_actors", "new_recipients"):
        vals = f.get(key)
        if not isinstance(vals,list) or len(vals)<3 or len(vals)!=len(set(vals)) or names.intersection(vals):
            raise ValueError("LANGUAGE_STRESS_OOV_NAMES_NOT_FRESH")
        if any(not isinstance(v,str) or not v.isalpha() for v in vals):
            raise ValueError("LANGUAGE_STRESS_INVALID_OOV_NAME")
        names.update(vals)
    old = set(sum((base["templates"][p] for p in ("train", "selection", "final")), []))
    seen = set()
    for key, count in (("selection_templates",2),("final_templates",4)):
        templates=f.get(key)
        if not isinstance(templates,list) or len(templates)!=count:
            raise ValueError("LANGUAGE_STRESS_TEMPLATES_INVALID")
        for text in templates:
            fields = [name for _,name,_,_ in Formatter().parse(text) if name is not None]
            required=set(prior.FIELDS[:-1]) | ({"modal"} if "{modal}" in text else {"duty"})
            if set(fields)!=required or len(fields)!=len(required) or " || " in text or "." in text:
                raise ValueError("LANGUAGE_STRESS_TEMPLATE_ROLE_INVALID")
            if text in old or text in seen:
                raise ValueError("LANGUAGE_STRESS_TEMPLATES_NOT_FRESH")
            seen.add(text)
    if f.get("positive") != {"modal":"is obligated to", "duty":"imposes a duty of delivery"} or f.get("negative") != {"modal":"has no obligation to", "duty":"does not impose a duty of delivery"}:
        raise ValueError("LANGUAGE_STRESS_POLARITY_CONTRACT_CHANGED")
    if f.get("anaphor") != "the same supplier" or f.get("nested_condition_connector") != "the conjunction of {first} and {second} has been satisfied":
        raise ValueError("LANGUAGE_STRESS_SCOPE_CONTRACT_CHANGED")
    return f


def validate_prior_report(path: Path) -> dict:
    report=json.loads(path.read_text(encoding="utf-8"))
    if (report.get("schema")!=prior.SCHEMA or report.get("status")!=prior.STATUS
            or report.get("acceptance",{}).get("no_production_mutation") is not True
            or report.get("acceptance",{}).get("no_final_based_model_selection") is not True
            or report.get("design",{}).get("same_training_texts_and_six_field_labels_for_both_arms") is not True
            or report.get("design",{}).get("fields") != list(prior.FIELDS)
            or report.get("design",{}).get("views") != list(VIEWS)
            or not {"joint","factorized"}.issubset(report.get("final",{}).get("final:mean",{}))):
        raise ValueError("LANGUAGE_STRESS_PRIOR_REPORT_PROVENANCE_INVALID")
    return report


def render(atom: tuple, f: dict, phase: str, index: int, *, use_anaphor=False) -> str:
    if len(atom)!=len(prior.FIELDS) or atom[-1] not in (-1,1) or phase not in ("selection","final"):
        raise ValueError("LANGUAGE_STRESS_ATOM_INVALID")
    options=f[phase+"_templates"]
    phr=f["positive" if atom[-1]==1 else "negative"]
    slots=dict(zip(prior.FIELDS, atom))
    if use_anaphor: slots["actor"]=f["anaphor"]
    return options[index%len(options)].format(**slots,**phr)


def derive_data(base:dict,f:dict,*,seed:int,train_atoms:int,selection_groups:int,final_groups:int) -> dict:
    old=prior.make_dataset(base,seed=seed,train_atoms=train_atoms,
                           selection_groups=selection_groups,final_groups=final_groups)
    output={"train":old["train"]}
    # Hold training atoms, labels, and templates IDENTICAL to the prior trial,
    # but generate genuinely NEW selection/final semantic base pairs. Exclude
    # the former selection/final base pairs to prevent reusing inspected tests.
    rng=random.Random(seed+9287)
    training=[r["atom"] for r in old["train"]]
    train_set=set(training)
    other=[t for t in itertools.product(*prior.values(base)) if t not in train_set]
    previous_pairs=set()
    for phase in ("selection","final"):
        for row in old[phase]:
            if row["kind"]=="base":
                a,b=row["atoms"]
                previous_pairs.add(tuple(sorted((a,b))))
    used_pairs=set(previous_pairs)
    for phase,group_count in (("selection",selection_groups),("final",final_groups)):
        records=[]
        for group in range(group_count):
            seen=group%2==0
            candidates=training if seen else other
            for attempt in range(20000):
                a,b=rng.sample(candidates,2)
                key=tuple(sorted((a,b)))
                if key not in used_pairs and all(a[j]!=b[j] for j in range(5)):
                    used_pairs.add(key)
                    break
            else: raise ValueError("LANGUAGE_STRESS_NEW_HOLDOUT_PAIRS_EXHAUSTED")
            kinds=prior.case_variants(a,b,base)
            unseen_actor=f["new_actors"][group%len(f["new_actors"])]
            other_recipient=f["new_recipients"][group%len(f["new_recipients"])]
            a_oov=(unseen_actor,)+a[1:]
            b_rec=b[:1]+(other_recipient,)+b[2:]
            second_condition=next(c for c in base["conditions"] if c!=a[3])
            nested=f["nested_condition_connector"].format(first=a[3],second=second_condition)
            a_nested=a[:3]+(nested,)+a[4:]
            reference=b[:0]+(a[0],)+b[1:]
            kinds.update({"oov_actor":[a_oov,b],"oov_recipient":[a,b_rec],
                          "nested_condition":[a_nested,b],"anaphoric_actor":[a,reference]})
            if set(kinds)!=set(KINDS):
                raise ValueError("LANGUAGE_STRESS_CHALLENGES_MISSING")
            for kind,atoms in kinds.items():
                # Distinct grammatical templates within documents; use same
                # lexical phase across arms. Pronoun is substituted in second
                # clause only: first clause supplies an unambiguous antecedent.
                clauses=[render(atom,f,phase,group+i, use_anaphor=(kind=="anaphoric_actor" and i==1))
                         for i,atom in enumerate(atoms)]
                if kind=="anaphoric_actor" and a[0] not in clauses[0]:
                    raise ValueError("LANGUAGE_STRESS_ANAPHOR_ANTECEDENT_MISSING")
                records.append({"id":f"{phase}:{group:03d}:{kind}","group_id":group,
                    "kind":kind,"scope":"out_of_schema" if kind in STRESS else "in_schema",
                    "text":prior.document(clauses),"atoms":atoms,
                    "base_atoms_seen_in_train":seen})
        output[phase]=records
    # OOV labels and nested labels must be absent from training, and all unseen
    # forms must remain lexically disjoint from the training and other holdout.
    train_vocabulary=[set(v) for v in prior.values(base)]
    for phase in ("selection","final"):
        for row in output[phase]:
            if row["kind"] in STRESS and all(all(a[j] in train_vocabulary[j] for j in range(6)) for a in row["atoms"]):
                if row["kind"]!="anaphoric_actor":
                    raise ValueError("LANGUAGE_STRESS_OOS_CHALLENGE_NOT_OOS")
    if {r["text"] for r in output["selection"]} & {r["text"] for r in output["final"]}:
        raise ValueError("LANGUAGE_STRESS_HOLDOUT_TEXT_OVERLAP")
    return output


def fit_structured_model(x: np.ndarray, train: list[tuple], base:dict, *,ridge:float) -> dict:
    """Fit unary and pairwise RBF outputs with exactly the same six-field labels.

    An unseen six-field combination is always a legal output: pairwise classes
    cover ALL combinations of each pair of known values, not whole tuple IDs.
    """
    shared=prior.fit_shared_supervision(x,train,base,ridge=ridge)
    x=shared["x"]
    fields=prior.values(base)
    maps=[{v:i for i,v in enumerate(vocab)} for vocab in fields]
    targets=np.asarray([[maps[j][v] for j,v in enumerate(atom)] for atom in train],dtype=int)
    pair_intervals=[]
    offset=0
    for i,j in PAIRS:
        pair_intervals.append((offset,offset+len(fields[i])*len(fields[j]),i,j))
        offset+=len(fields[i])*len(fields[j])
    yy=np.zeros((len(train),offset),dtype=np.float64)
    for lo,hi,i,j in pair_intervals:
        yy[np.arange(len(train)),lo+targets[:,i]*len(fields[j])+targets[:,j]]=1.
    d=np.maximum(2.-2.*(x@x.T),0.)
    k=np.exp(-shared["gamma"]*d)
    means=yy.mean(axis=0)
    weights=np.linalg.solve(k+ridge*np.eye(len(train)),yy-means)
    choices=list(itertools.product(*fields))
    # Explicitly enumerate all tuples; no class restriction to observed tuples.
    indices=np.asarray([[maps[j][v] for j,v in enumerate(a)] for a in choices],dtype=int)
    return {"shared":shared,"pair_weights":weights,"pair_means":means,
            "pair_intervals":pair_intervals,"choices":choices,"choice_indices":indices,
            "training_tuple_count":len(train),"legal_output_tuple_count":len(choices)}


def predict(model:dict,x:np.ndarray,base:dict,*,pair_weight:float) -> dict[str,list[tuple]]:
    if pair_weight<0 or not np.isfinite(pair_weight):
        raise ValueError("LANGUAGE_STRESS_PAIR_WEIGHT_INVALID")
    x=composition.normalized(x,width=model["shared"]["x"].shape[1])
    shared=model["shared"]
    dist=np.maximum(2.-2.*(x@shared["x"].T),0.)
    kern=np.exp(-shared["gamma"]*dist)
    logits=shared["mean"]+kern@shared["weights"]
    unary_segments=shared["segments"]
    choices=model["choices"]
    choices_ix=model["choice_indices"]
    unaries=np.zeros((len(x),len(choices)),dtype=np.float64)
    for j,(lo,hi) in enumerate(unary_segments):
        unaries+=logits[:,lo:hi][:,choices_ix[:,j]]/len(unary_segments)
    fact=[tuple(prior.values(base)[j][int(np.argmax(row[lo:hi]))] for j,(lo,hi) in enumerate(unary_segments))
          for row in logits]
    if pair_weight:
        plog=model["pair_means"]+kern@model["pair_weights"]
        pairs=np.zeros_like(unaries)
        for lo,hi,i,j in model["pair_intervals"]:
            width=len(prior.values(base)[j])
            pairs+=plog[:,lo:hi][:,choices_ix[:,i]*width+choices_ix[:,j]]/len(PAIRS)
        unaries+=pair_weight*pairs
    return {"factorized":fact,"structured_pairwise":[choices[int(i)] for i in np.argmax(unaries,axis=1)]}


def phase_metrics(rows:list[dict], models:dict[str,dict], features:dict[str,np.ndarray],
                  base:dict,train_set:set[tuple],*,pair_weight:float) -> dict:
    unique,mapping=prior.catalog(rows)
    # Identical anaphoric clauses may have different gold actors depending on
    # preceding context. Do NOT pretend their labels are a function of the
    # isolated clause text; these are deliberate coreference failure probes.
    labels={}
    for row in rows:
        if row["kind"]=="anaphoric_actor": continue
        for text,atom in zip(prior.split_document(row["text"]),row["atoms"]):
            if text in labels and labels[text]!=atom:
                raise ValueError("LANGUAGE_STRESS_CLAUSE_LABEL_CONFLICT")
            labels[text]=atom
    outputs={}
    for view in VIEWS:
        atoms=predict(models[view],features[view],base,pair_weight=pair_weight)
        for arm in ARMS:
            if len(atoms[arm])!=len(unique):
                raise ValueError("LANGUAGE_STRESS_PREDICTION_SIZE_MISMATCH")
        outputs[view]={}
        for arm in ARMS:
            groups=prior.group_predictions(atoms[arm],mapping)
            by_kind={}
            for kind in KINDS:
                indices=[i for i,r in enumerate(rows) if r["kind"]==kind]
                if not indices:continue
                exact=np.asarray([prior.contract_graph(groups[i])==prior.contract_graph(rows[i]["atoms"]) for i in indices])
                field_match=np.asarray([[int(a[j]==b[j]) for j in range(6)]
                    for i in indices for a,b in zip(rows[i]["atoms"],groups[i])])
                contradictions=np.asarray([prior.has_contradiction(groups[i])==prior.has_contradiction(rows[i]["atoms"])
                                            for i in indices])
                by_kind[kind]={"documents":len(indices),"graph_exact":float(exact.mean()),
                               "atomic_six_field_exact":float(np.all(field_match,axis=1).mean()),
                               "mean_field_accuracy":float(field_match.mean()),
                               "contradiction_decision_accuracy":float(contradictions.mean())}
            supported=[i for i,r in enumerate(rows) if r["scope"]=="in_schema"]
            in_atoms=[(a,p) for i in supported for a,p in zip(rows[i]["atoms"],groups[i])]
            errors=np.asarray([[int(a[j]==p[j]) for j in range(6)] for a,p in in_atoms],dtype=int)
            in_graph=[prior.contract_graph(groups[i])==prior.contract_graph(rows[i]["atoms"]) for i in supported]
            in_conflict=[prior.has_contradiction(groups[i])==prior.has_contradiction(rows[i]["atoms"])
                         for i in supported]
            swaps={}
            for f in prior.SWAPS:
                exact=[]
                for group in sorted({row["group_id"] for row in rows}):
                    ids={r["kind"]:i for i,r in enumerate(rows) if r["group_id"]==group}
                    exact.append(all(prior.contract_graph(groups[ids[k]])==prior.contract_graph(rows[ids[k]]["atoms"])
                                         for k in ("base","swap_"+f)))
                swaps[f]=float(np.mean(exact))
            outputs[view][arm]={"in_schema":{"documents":len(supported),
                    "graph_exact":float(np.mean(in_graph)),
                    "atomic_six_field_exact":float(np.all(errors,axis=1).mean()),
                    "mean_field_accuracy":float(errors.mean()),
                    "contradiction_decision_accuracy":float(np.mean(in_conflict)),
                    "by_field":dict(zip(prior.FIELDS,[float(errors[:,j].mean()) for j in range(6)])),
                    "swap_pair_both_graph_exact":swaps},"by_challenge":by_kind}
    oracle_rows=[r for r in rows if r["scope"]=="in_schema"]
    oracle=prior.scenario_scores(oracle_rows,[r["atoms"] for r in oracle_rows])
    if (oracle["complete_relation_graph_exact"]!=1. or oracle["true_contradiction_detected_rate"]!=1.
            or oracle["different_condition_no_false_contradiction_rate"]!=1.
            or oracle["omission_one_relation_drop_rate"]!=1.
            or any(x["gold_unbound_inventory_collision_rate"]!=1. for x in oracle["binding_swaps"].values())):
        raise ValueError("LANGUAGE_STRESS_ORACLE_RELATIONS_INVALID")
    # Gold-only ceiling: if labels are not among known class values, these
    # output heads cannot express them. This is NOT an inference-time oracle.
    allowed=[set(v) for v in prior.values(base)]
    ceilings={}
    for kind in KINDS:
        indices=[i for i,row in enumerate(rows) if row["kind"]==kind]
        ceilings[kind]={"documents":len(indices),
            "full_graph_representable_fraction":float(np.mean([
                all(all(atom[j] in allowed[j] for j in range(6)) for atom in rows[i]["atoms"])
                for i in indices])),"in_schema":kind not in STRESS}
    return outputs,{"unique_clauses":len(unique),"documents":len(rows),
                    "by_challenge_representability_ceiling":ceilings,
                    "oracle_graph_exact":oracle["complete_relation_graph_exact"],
                    "oracle_true_contradictions_detected":oracle["true_contradiction_detected_rate"],
                    "oracle_scope_false_contradiction_avoided":oracle["different_condition_no_false_contradiction_rate"],
                    "oracle_inventory_collision_on_swaps":{k:v["gold_unbound_inventory_collision_rate"] for k,v in oracle["binding_swaps"].items()},
                    "out_of_schema_challenges_do_not_enter_in_schema_aggregate":True}


def run(args: argparse.Namespace,*,encoder:Callable|None=None) -> dict:
    if args.output.exists():raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.max_length!=192 or args.batch_size<1 or args.ridge<=0:
        raise ValueError("LANGUAGE_STRESS_CONFIG_INVALID")
    base=prior.load_fixture(args.base_fixture)
    fixture=load_fixture(args.fixture,base)
    source=validate_prior_report(args.prior_report)
    if (source.get("sources",{}).get("fixture_sha256")!=sha256(args.base_fixture.read_bytes()).hexdigest()
            or source.get("design",{}).get("training_atomic_clauses")!=args.train_atoms):
        raise ValueError("LANGUAGE_STRESS_PRIOR_TRAINING_CONTRACT_CHANGED")
    data=derive_data(base,fixture,seed=args.seed,train_atoms=args.train_atoms,
                     selection_groups=args.selection_groups,final_groups=args.final_groups)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,
                    allow_download=args.allow_model_download,
                    batch_size=args.batch_size,max_length=args.max_length)
    positions=getattr(encoder,"positions",None)
    if positions is not None and (positions.get("half")!=14 or positions.get("final")!=28):
        raise ValueError("LANGUAGE_STRESS_LAYER_LAYOUT_CHANGED")
    train=data["train"]
    train_raw=composition.views_from_encoder(encoder,[r["text"] for r in train])
    train_atoms=[r["atom"] for r in train]
    models={v:fit_structured_model(train_raw[v],train_atoms,base,ridge=args.ridge) for v in VIEWS}
    phase_features={};phase_unique={}
    for phase in ("selection","final"):
        unique,_=prior.catalog(data[phase]); phase_unique[phase]=unique
        phase_features[phase]=composition.views_from_encoder(encoder,unique)
    # Selection is train-disjoint; tune only the structured-pairwise coefficient.
    # A zero pair coefficient would reduce this arm to factorized unaries,
    # so it is a separate unit-test control, NOT an eligible joint method.
    selection_scan=[]
    for weight in PAIR_WEIGHTS:
        metrics,_=phase_metrics(data["selection"],models,phase_features["selection"],base,
                                set(train_atoms),pair_weight=weight)
        metric=metrics["final:mean"]["structured_pairwise"]["in_schema"]
        selection_scan.append({"weight":weight,"graph_exact":metric["graph_exact"],
                               "atomic_six_field_exact":metric["atomic_six_field_exact"]})
    # Deterministic ties choose LESS pairwise influence.
    best=max(selection_scan,key=lambda item:(item["graph_exact"],item["atomic_six_field_exact"],-item["weight"]))
    selection,selection_stats=phase_metrics(data["selection"],models,phase_features["selection"],base,
                                             set(train_atoms),pair_weight=best["weight"])
    final,final_stats=phase_metrics(data["final"],models,phase_features["final"],base,
                                     set(train_atoms),pair_weight=best["weight"])
    if (getattr(encoder,"stats",None) is not None
            and encoder.stats.get("texts_truncated",0)!=0):
        raise ValueError("LANGUAGE_STRESS_ENCODER_TEXT_TRUNCATED")
    if final_stats["oracle_graph_exact"]!=1 or any(final_stats["by_challenge_representability_ceiling"][kind]["full_graph_representable_fraction"]!=0 for kind in ("oov_actor","oov_recipient","nested_condition")):
        raise ValueError("LANGUAGE_STRESS_ORACLE_CONTROL_FAILED")
    report={"schema":SCHEMA,"status":STATUS,"acceptance":{
        "status":"DIAGNOSTIC_ONLY_NOT_SEALABLE","never_updates_model_weights":True,
        "vocabulary_unchanged":True,"no_contract_seal":True,"no_production_mutation":True,
        "no_final_based_model_selection":True,"ollama_called":False},
        "sources":{"prior_binding_report_sha256":sha256(args.prior_report.read_bytes()).hexdigest(),
                   "prior_binding_schema":source["schema"],
                   "base_fixture_sha256":sha256(args.base_fixture.read_bytes()).hexdigest(),
                   "stress_fixture_sha256":sha256(args.fixture.read_bytes()).hexdigest()},
        "design":{"model":smoke.MODEL_ID,"model_frozen":True,"views":list(VIEWS),
                  "arms":list(ARMS),"fields":list(prior.FIELDS),
                  "training_atomic_clauses":len(train),"selection_groups":args.selection_groups,
                  "final_groups":args.final_groups,"same_six_field_supervision":True,
                  "same_training_texts":True,"same_rbf_kernel":True,"ridge":args.ridge,
                  "pairwise_candidate_weights":list(PAIR_WEIGHTS),
                  "pairwise_coefficient_chosen_on_selection_only":True,
                  "pairwise_weight_locked_before_final":best["weight"],
                  "structured_pairwise_can_output_unseen_tuples":True,
                  "legal_known_vocabulary_tuples":models["final:mean"]["legal_output_tuple_count"],
                  "learned_training_tuples":len(train),
                  "new_grammar_templates":{"selection":2,"final":4},
                  "fresh_semantic_base_pairs_excluding_prior_inspected_pairs":True,
                  "prior_training_atoms_reused_with_identical_labels":True,
                  "out_of_schema_strata":list(STRESS),
                  "syntax_contract":"Explicit || fixture delimiter. All atomic clauses retain one predicate; no natural-language segmentation.",
                  "out_of_schema_policy":"Scores are reported, not accepted as supported; both heads are closed-class and have no coreference or nested-condition solver.",
                  "limits":"Authored single-predicate grammars, same fixed concepts as earlier train; OOV and nested conditions deliberately unrepresentable; no natural-language understanding claim.",
                  "token_stats":getattr(encoder,"stats",None)},
        "pairwise_selection_scan":selection_scan,"selection":selection,"selection_stats":selection_stats,
        "final":final,"final_stats":final_stats,
        "interpretation":"Tests whether factorization survives fresh grammatical forms against a pairwise generative comparator with equal clause-level supervision; OOV/coreference/nesting are failure probes, not supported representations."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as fp:
        json.dump(report,fp,indent=2,ensure_ascii=False,allow_nan=False);fp.write("\n")
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device",default="cuda")
    p.add_argument("--allow-model-download",action="store_true")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--fixture",type=Path,default=FIXTURE)
    p.add_argument("--base-fixture",type=Path,default=prior.FIXTURE)
    p.add_argument("--prior-report",type=Path,default=PRIOR_REPORT)
    p.add_argument("--train-atoms",type=int,default=216)
    p.add_argument("--selection-groups",type=int,default=16)
    p.add_argument("--final-groups",type=int,default=24)
    p.add_argument("--seed",type=int,default=261117)
    p.add_argument("--ridge",type=float,default=.1)
    p.add_argument("--batch-size",type=int,default=4)
    p.add_argument("--max-length",type=int,default=192)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    report=run(args)
    output={"status":report["status"],"acceptance":report["acceptance"]["status"],
        "selection_locked_pair_weight":report["design"]["pairwise_weight_locked_before_final"],
        "final_in_schema":{view:{arm:{"atomic_six_field_exact":round(m["in_schema"]["atomic_six_field_exact"],6),
             "bound_graph_exact":round(m["in_schema"]["graph_exact"],6),
             "true_contradiction_detected":round(m["by_challenge"]["contradiction"]["contradiction_decision_accuracy"],6),
             "different_condition_no_false_contradiction":round(m["by_challenge"]["different_condition"]["contradiction_decision_accuracy"],6),
             "actor_swap_pair_both_exact":round(m["in_schema"]["swap_pair_both_graph_exact"]["actor"],6)}
            for arm,m in arms.items()} for view,arms in report["final"].items()},
        "out_of_schema":{view:{arm:{kind:round(m["by_challenge"][kind]["graph_exact"],6)
                        for kind in STRESS} for arm,m in arms.items()}
                      for view,arms in report["final"].items()},
        "report":str(args.output)}
    print(smoke.canonical(output))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
