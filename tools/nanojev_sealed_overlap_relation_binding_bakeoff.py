#!/usr/bin/env python3
"""Frozen-Qwen relation-binding bakeoff: factorized slots vs joint tuple slots.

Both predictors use precisely the SAME clause-level annotated training texts,
semantic labels, RBF kernel, ridge parameter, and 2 frozen Qwen views. The only
change is the output hypothesis: six separately predicted fields recombined into
relations (factorized), or one class per previously observed complete relation
(joint). An unordered per-field inventory is a representation-only ablation.

Probe documents hold two provisions whose actor, recipient, asset, condition,
or consequence may be swapped while maintaining identical word inventories;
other probes check polarity flips, omissions, true contradictions, negation in
different condition scopes, and clause order. Oracle structural assembly is a
post-hoc logical control, NEVER an inference method. The task is a synthetic
single-predicate relational language with lossless fixture delimiters, not a
parser or a general contract language. No production mutation or sealing.

Run from the NanoJev project root:
  & $py -m tools.nanojev_sealed_overlap_relation_binding_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-relation-binding-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
from string import Formatter
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_composition_bakeoff as composition
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers

SCHEMA = "nanojev.sealed-overlap.relation-binding-bakeoff.v1"
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "nanojev_overlap_relation_binding_v1.json"
SOURCE_REPORT = Path("runtime/reports/sealed-overlap-composition-0001.json")
FIELDS = ("actor", "recipient", "asset", "condition", "consequence", "polarity")
SWAPS = ("actor", "recipient", "asset", "condition", "consequence")
VIEWS = ("final:mean", "half:last")
ARMS = ("factorized", "joint")
STATUS = "COMPLETE_RELATION_BINDING_BAKEOFF_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"


def load_fixture(path: Path) -> dict:
    f = json.loads(path.read_text(encoding="utf-8"))
    if f.get("schema") != "nanojev.sealed-overlap.relation-binding-fixture.v1":
        raise ValueError("BINDING_FIXTURE_SCHEMA_INVALID")
    for name in FIELDS[:-1]:
        values = f.get(name + "s", None)
        if name == "asset": values = f.get("assets")
        if name == "condition": values = f.get("conditions")
        if name == "consequence": values = f.get("consequences")
        if name == "recipient": values = f.get("recipients")
        if name == "actor": values = f.get("actors")
        if not isinstance(values,list) or len(values)<3 or len(values)!=len(set(values)):
            raise ValueError("BINDING_FIXTURE_VALUES_INVALID:"+name)
        if any(not isinstance(v,str) or not v.strip() or " || " in v or "." in v for v in values):
            raise ValueError("BINDING_FIXTURE_VALUE_UNSAFE:"+name)
    if f.get("document_delimiter") != " || ":
        raise ValueError("BINDING_FIXTURE_DELIMITER_CHANGED")
    all_templates=[]
    for phase, count in (("train",3),("selection",2),("final",2)):
        templates=f.get("templates",{}).get(phase)
        if not isinstance(templates,list) or len(templates)!=count:
            raise ValueError("BINDING_FIXTURE_TEMPLATES_INVALID")
        for template in templates:
            slots=[field for _,field,_,_ in Formatter().parse(template) if field is not None]
            if sorted(slots) != sorted(["actor","recipient","asset","condition","consequence", "modal" if "{modal}" in template else "contract_modal"]):
                raise ValueError("BINDING_TEMPLATE_ROLE_COVERAGE_INVALID")
            if "." in template or " || " in template:
                raise ValueError("BINDING_TEMPLATE_DELIMITER_UNSAFE")
        all_templates.extend(templates)
    if len(set(all_templates))!=len(all_templates):
        raise ValueError("BINDING_LEXICAL_HOLDOUT_DUPLICATE")
    if f.get("polarity_phrasing") != {
        "positive":{"modal":"is required to","contract_modal":"obligates"},
        "negative":{"modal":"is not required to","contract_modal":"does not obligate"}}:
        raise ValueError("BINDING_POLARITY_PHRASES_CHANGED")
    return f


def values(f: dict) -> tuple[list[str],...]:
    return (f["actors"],f["recipients"],f["assets"],f["conditions"],f["consequences"],[-1,1])


def render_atom(atom: tuple, fixture: dict, phase: str, template_index: int=0) -> str:
    if len(atom)!=6 or atom[-1] not in (-1,1) or phase not in ("train","selection","final"):
        raise ValueError("BINDING_INVALID_ATOM")
    pool=fixture["templates"][phase]
    phr=fixture["polarity_phrasing"]["positive" if atom[-1]==1 else "negative"]
    return pool[template_index % len(pool)].format(**dict(zip(FIELDS,atom)),**phr)


def clause_list(atoms: list[tuple], fixture: dict, phase: str, *, template_offset=0) -> list[str]:
    return [render_atom(a,fixture,phase,template_offset+i) for i,a in enumerate(atoms)]


def document(clauses: list[str]) -> str:
    if not clauses or any(not x or " || " in x for x in clauses):
        raise ValueError("BINDING_INVALID_DOCUMENT_CLAUSES")
    return " || ".join(clauses) + "."


def split_document(doc: str) -> list[str]:
    if not isinstance(doc,str) or not doc.endswith(".") or "\n" in doc:
        raise ValueError("BINDING_INVALID_DOCUMENT")
    chunks=doc[:-1].split(" || ")
    if any(not c or "." in c or c!=c.strip() for c in chunks) or document(chunks)!=doc:
        raise ValueError("BINDING_DOCUMENT_NOT_LOSSLESS")
    return chunks


def random_atom(rng:random.Random, fixture:dict) -> tuple:
    return tuple(rng.choice(v) for v in values(fixture))


def semantic_key(atom:tuple) -> tuple:
    return atom[:-1]


def contract_graph(atoms:list[tuple]) -> tuple[tuple,...]:
    """Binding-preserving set of complete six-field relations, with polarity."""
    return tuple(sorted(set(tuple(a) for a in atoms)))


def inventory(atoms:list[tuple]) -> tuple:
    """Unbound bag: field counts only, incapable of distinguishing role swaps."""
    if not atoms: return tuple()
    return tuple(tuple(sorted(Counter(a[j] for a in atoms).items())) for j in range(len(FIELDS)))


def has_contradiction(atoms:list[tuple]) -> bool:
    signs={}
    for atom in atoms:
        key=semantic_key(atom)
        signs.setdefault(key,set()).add(atom[-1])
    return any(s=={-1,1} for s in signs.values())


def case_variants(a:tuple,b:tuple,fixture:dict) -> dict[str,list[tuple]]:
    if any(a[j]==b[j] for j in range(5)) or a==b:
        raise ValueError("BINDING_BASE_PAIR_NOT_DISTINCT")
    result={"base":[a,b]}
    for field in SWAPS:
        j=FIELDS.index(field)
        aa=list(a);bb=list(b)
        aa[j],bb[j]=bb[j],aa[j]
        result["swap_"+field]=[tuple(aa),tuple(bb)]
    flipped=a[:-1]+(-a[-1],)
    safe=list(flipped);safe[FIELDS.index("condition")]=b[FIELDS.index("condition")]
    result.update({"polarity_flip":[flipped,b],"omission":[b],
                   "contradiction":[a,b,flipped],"different_condition":[a,b,tuple(safe)],
                   "reordered":[b,a]})
    if not has_contradiction(result["contradiction"]) or has_contradiction(result["different_condition"]):
        raise ValueError("BINDING_SCOPE_CHALLENGE_INVALID")
    for field in SWAPS:
        if inventory(result["base"])!=inventory(result["swap_"+field]) or contract_graph(result["base"])==contract_graph(result["swap_"+field]):
            raise ValueError("BINDING_SWAP_CONTROL_NOT_DISCRIMINATIVE")
    return result


def make_dataset(fixture:dict,*,seed:int,train_atoms:int,selection_groups:int,final_groups:int) -> dict:
    if train_atoms<96 or train_atoms>320 or selection_groups<8 or final_groups<8 or train_atoms%8 or selection_groups%8 or final_groups%8:
        raise ValueError("BINDING_DATASET_SIZE_INVALID")
    rng=random.Random(seed)
    universe=[]
    # Enumerate to guarantee every signed value has training coverage.
    import itertools
    for combo in itertools.product(*values(fixture)):
        universe.append(combo)
    rng.shuffle(universe)
    train=universe[:train_atoms]
    train_set=set(train)
    if any(not set(vals).issubset({a[j] for a in train}) for j,vals in enumerate(values(fixture))):
        raise ValueError("BINDING_TRAIN_ROLE_COVERAGE_INVALID")
    rows={"train":[{"text":render_atom(a,fixture,"train",i),"atom":a}
                   for i,a in enumerate(train)]}
    used_pairs=set()
    for phase,groups in (("selection",selection_groups),("final",final_groups)):
        phase_rows=[]
        for i in range(groups):
            want_seen=i%2==0
            candidates=train if want_seen else universe[train_atoms:]
            for attempt in range(10000):
                a,b=rng.sample(candidates,2)
                if (all(a[j]!=b[j] for j in range(5)) and (a,b) not in used_pairs and (b,a) not in used_pairs):
                    break
            else: raise ValueError("BINDING_MATCHED_BASE_PAIR_UNAVAILABLE")
            used_pairs.add((a,b))
            variants=case_variants(a,b,fixture)
            for name,atoms in variants.items():
                clauses=clause_list(atoms,fixture,phase,template_offset=i%2)
                phase_rows.append({"id":f"{phase}:{i:03d}:{name}","group_id":i,"kind":name,
                                   "text":document(clauses),"atoms":atoms,
                                   "base_atoms_seen_in_train":want_seen})
        rows[phase]=phase_rows
    if any(r["text"] in {x["text"] for x in rows["train"]} for p in ("selection","final") for r in rows[p]):
        raise ValueError("BINDING_TRAIN_TEXT_LEAK")
    if {r["text"] for r in rows["selection"]} & {r["text"] for r in rows["final"]}:
        raise ValueError("BINDING_HOLDOUT_TEXT_LEAK")
    return rows


def catalog(rows:list[dict]) -> tuple[list[str],list[list[int]]]:
    seen={}; texts=[]; mapping=[]
    for row in rows:
        ids=[]
        for clause in split_document(row["text"]):
            if clause not in seen:
                seen[clause]=len(texts);texts.append(clause)
            ids.append(seen[clause])
        mapping.append(ids)
    return texts,mapping


def fit_shared_supervision(train_features:np.ndarray, train_atoms:list[tuple], fixture:dict,*,ridge:float) -> dict:
    x=composition.normalized(train_features,rows=len(train_atoms))
    if ridge<=0 or len(set(train_atoms))!=len(train_atoms):
        raise ValueError("BINDING_TRAINING_INVALID")
    field_vocab=values(fixture)
    field_idx=[{v:i for i,v in enumerate(options)} for options in field_vocab]
    target=np.asarray([[field_idx[j][v] for j,v in enumerate(atom)] for atom in train_atoms],dtype=int)
    joint_classes={a:i for i,a in enumerate(train_atoms)}
    segments=[];offset=0
    for j,opts in enumerate(field_vocab):
        segments.append((offset,offset+len(opts)))
        offset+=len(opts)
    joint=(offset,offset+len(train_atoms))
    y=np.zeros((len(train_atoms),joint[1]),dtype=float)
    for j,(begin,end) in enumerate(segments):
        y[np.arange(len(train_atoms)),begin+target[:,j]]=1.
    y[np.arange(len(train_atoms)),joint[0]+np.arange(len(train_atoms))]=1.
    d=np.maximum(2.-2.*(x@x.T),0.)
    pos=d[np.triu_indices(len(x),1)]
    pos=pos[pos>1e-12]
    gamma=float(np.log(2.)/np.median(pos)) if len(pos) else 1.
    k=np.exp(-gamma*d)
    means=y.mean(axis=0)
    weights=np.linalg.solve(k+ridge*np.eye(len(x)),y-means)
    return {"x":x,"weights":weights,"mean":means,"gamma":gamma,
            "segments":segments,"joint":joint,"classes":train_atoms,
            "training_count":len(train_atoms),"label_fields":list(FIELDS)}


def predict_atomic(model:dict, features:np.ndarray, fixture:dict) -> dict[str,list[tuple]]:
    x=composition.normalized(features,width=model["x"].shape[1])
    d=np.maximum(2.-2.*(x@model["x"].T),0.)
    scores=model["mean"]+np.exp(-model["gamma"]*d)@model["weights"]
    slots=values(fixture)
    fact=[]
    for row in scores:
        fact.append(tuple(slots[j][int(np.argmax(row[lo:hi]))] for j,(lo,hi) in enumerate(model["segments"])))
    jl,jh=model["joint"]
    joint=[model["classes"][int(np.argmax(row[jl:jh]))] for row in scores]
    return {"factorized":fact,"joint":joint}


def group_predictions(atoms:list[tuple], indices:list[list[int]]) -> list[list[tuple]]:
    return [[atoms[i] for i in row] for row in indices]


def atomic_scores(truth_atoms:list[tuple], predicted_atoms:list[tuple],train_set:set[tuple]) -> dict:
    if len(truth_atoms)!=len(predicted_atoms):raise ValueError("BINDING_ATOM_LENGTH_INVALID")
    rows=np.asarray([[int(a[j]==p[j]) for j in range(len(FIELDS))] for a,p in zip(truth_atoms,predicted_atoms)])
    out={"count":len(truth_atoms),"all_six_fields_exact":float(np.mean(np.all(rows,axis=1))),
         "by_field":{f:float(np.mean(rows[:,j])) for j,f in enumerate(FIELDS)}}
    for seen in (True,False):
        mask=np.asarray([a in train_set for a in truth_atoms])==seen
        out["seen_tuple" if seen else "unseen_tuple"]={"count":int(mask.sum()),
            "full_tuple_exact":float(np.mean(np.all(rows[mask],axis=1))) if mask.any() else None}
    return out


def scenario_scores(rows:list[dict], preds:list[list[tuple]]) -> dict:
    if len(rows)!=len(preds):raise ValueError("BINDING_SCENARIO_LENGTH_INVALID")
    by_kind={}
    for kind in sorted({r["kind"] for r in rows}):
        ids=[i for i,r in enumerate(rows) if r["kind"]==kind]
        graphs=[contract_graph(preds[i])==contract_graph(rows[i]["atoms"]) for i in ids]
        inventory_matches=[inventory(preds[i])==inventory(rows[i]["atoms"]) for i in ids]
        expected=[has_contradiction(rows[i]["atoms"]) for i in ids]
        observed=[has_contradiction(preds[i]) for i in ids]
        by_kind[kind]={"cases":len(ids),"exact_bound_relations":float(np.mean(graphs)),
                       "unbound_inventory_exact":float(np.mean(inventory_matches)),
                       "contradiction_truth_rate":float(np.mean(expected)),
                       "contradiction_prediction_rate":float(np.mean(observed)),
                       "contradiction_decision_accuracy":float(np.mean(np.asarray(expected)==np.asarray(observed)))}
    groups={}
    for i,row in enumerate(rows):
        groups.setdefault(row["group_id"],{})[row["kind"]]=(row,preds[i])
    swaps={}
    for key in SWAPS:
        kind="swap_"+key
        contrasts=[];both=[];gold_collision=[];pred_collision=[]
        for group in groups.values():
            a,pa=group["base"];b,pb=group[kind]
            contrasts.append(contract_graph(pa)!=contract_graph(pb))
            both.append(contract_graph(pa)==contract_graph(a["atoms"]) and contract_graph(pb)==contract_graph(b["atoms"]))
            gold_collision.append(inventory(a["atoms"])==inventory(b["atoms"]))
            pred_collision.append(inventory(pa)==inventory(pb))
        swaps[key]={"groups":len(contrasts),"predicted_binding_change_detected":float(np.mean(contrasts)),
                    "both_documents_relation_exact":float(np.mean(both)),
                    "gold_unbound_inventory_collision_rate":float(np.mean(gold_collision)),
                    "predicted_unbound_inventory_collision_rate":float(np.mean(pred_collision))}
    omissions=[]; order=[]; conflict=[]; scope=[]
    for group in groups.values():
        a,pa=group["base"]; _,po=group["omission"]
        omissions.append(len(contract_graph(pa))-len(contract_graph(po))==1)
        order.append(contract_graph(pa)==contract_graph(group["reordered"][1]))
        conflict.append(has_contradiction(group["contradiction"][1]))
        scope.append(not has_contradiction(group["different_condition"][1]))
    return {"by_kind":by_kind,"binding_swaps":swaps,
            "omission_one_relation_drop_rate":float(np.mean(omissions)),
            "permutation_invariance_rate":float(np.mean(order)),
            "true_contradiction_detected_rate":float(np.mean(conflict)),
            "different_condition_no_false_contradiction_rate":float(np.mean(scope)),
            "complete_relation_graph_exact":float(np.mean([contract_graph(p)==contract_graph(r["atoms"]) for r,p in zip(rows,preds)]))}


def validate_source(path:Path) -> dict:
    obj=json.loads(path.read_text(encoding="utf-8"))
    if (obj.get("schema")!="nanojev.sealed-overlap.compositional-architecture-bakeoff.v1"
        or obj.get("status")!="COMPLETE_COMPOSITION_BAKEOFF_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
        or obj.get("acceptance",{}).get("no_production_mutation") is not True
        or obj.get("acceptance",{}).get("no_final_based_model_selection") is not True
        or obj.get("design",{}).get("atomic_methods_have_extra_clause_level_training_supervision") is not True
        or not {"slot_factor_rbf","slot_joint_rbf"}.issubset(obj.get("final",{}).get("final:mean",{}))):
        raise ValueError("BINDING_COMPOSITION_REPORT_PROVENANCE_INVALID")
    return obj


def run(args:argparse.Namespace,*,encoder:Callable|None=None) -> dict:
    if args.output.exists(): raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.max_length!=192 or args.batch_size<1 or args.ridge<=0:
        raise ValueError("BINDING_CONFIG_INVALID")
    fixture=load_fixture(args.fixture)
    prior=validate_source(args.composition_report)
    data=make_dataset(fixture,seed=args.seed,train_atoms=args.train_atoms,
                      selection_groups=args.selection_groups,final_groups=args.final_groups)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,
                                          allow_download=args.allow_model_download,
                                          batch_size=args.batch_size,max_length=args.max_length)
    positions=getattr(encoder,"positions",None)
    if positions is not None and (positions.get("half")!=14 or positions.get("final")!=28):
        raise ValueError("BINDING_MODEL_LAYER_LAYOUT_CHANGED")
    train_text=[r["text"] for r in data["train"]]
    def encode(texts):
        result=composition.views_from_encoder(encoder,texts)
        return result
    embeddings=encode(train_text)
    train_atoms=[r["atom"] for r in data["train"]]
    train_set=set(train_atoms)
    models={view:fit_shared_supervision(embeddings[view],train_atoms,fixture,ridge=args.ridge) for view in VIEWS}
    def eval_phase(phase):
        rows=data[phase]; unique,indices=catalog(rows)
        raw=encode(unique)
        truth={s:atom for row in rows for s,atom in zip(split_document(row["text"]),row["atoms"])}
        if len(truth)!=len(unique) or any(s not in truth for s in unique):
            raise ValueError("BINDING_CLAUSE_LABEL_CONFLICT")
        gold=[truth[s] for s in unique]
        outcome={}
        for view in VIEWS:
            atoms=predict_atomic(models[view],raw[view],fixture)
            outcome[view]={arm:{"atomic":atomic_scores(gold,pred,train_set),
                       "composition":scenario_scores(rows,group_predictions(pred,indices))}
                     for arm,pred in atoms.items()}
        oracle=scenario_scores(rows,[r["atoms"] for r in rows])
        if oracle["complete_relation_graph_exact"]!=1. or oracle["true_contradiction_detected_rate"]!=1. or oracle["different_condition_no_false_contradiction_rate"]!=1.:
            raise ValueError("BINDING_ORACLE_STRUCTURAL_CONTROL_FAILED")
        return outcome,{"documents":len(rows),"unique_clause_texts":len(unique),"oracle_structural_control":oracle,
                        "seen_tuple_count":sum(x in train_set for x in gold),"unseen_tuple_count":sum(x not in train_set for x in gold)}
    sel,sel_stats=eval_phase("selection")
    # Locked arms are never selected, tuned or swapped after selection or final.
    final,final_stats=eval_phase("final")
    report={"schema":SCHEMA,"status":STATUS,"acceptance":{"status":"DIAGNOSTIC_ONLY_NOT_SEALABLE",
           "never_updates_model_weights":True,"vocabulary_unchanged":True,"no_contract_seal":True,
           "no_production_mutation":True,"no_final_based_model_selection":True,"ollama_called":False},
        "sources":{"prior_composition_sha256":sha256(args.composition_report.read_bytes()).hexdigest(),
                   "prior_composition_schema":prior["schema"],"fixture_sha256":sha256(args.fixture.read_bytes()).hexdigest()},
        "design":{"model":smoke.MODEL_ID,"model_frozen":True,"views":list(VIEWS),"arms":list(ARMS),
                  "training_atomic_clauses":len(train_atoms),"selection_groups":args.selection_groups,
                  "final_groups":args.final_groups,"fields":list(FIELDS),"relation_predicate":"conditional transfer obligation",
                  "same_training_texts_and_six_field_labels_for_both_arms":True,
                  "same_rbf_kernel_and_ridge_for_both_arms":True,"ridge":args.ridge,
                  "training_template_count":3,"selection_template_count":2,"final_template_count":2,
                  "different_lexical_templates_between_phases":True,
                  "fixture_specific_syntactic_clause_delimiter":fixture["document_delimiter"],
                  "supervision_confound_control":"equal clause-level labels across factorized and joint arms; structural bag ablation uses identical factorized predictions",
                  "relation_scope_limitation":"Six closed-class fields; no pronouns, unrestricted language, event ordering, numeric deadlines, multi-hop causality, or eight-axis full semantics",
                  "token_stats":getattr(encoder,"stats",None)},
        "selection":sel,"selection_stats":sel_stats,"final":final,"final_stats":final_stats,
        "interpretation":"A semantic bag provably collides on role swaps even with oracle atoms. Equal clause-level supervision isolates the effect of output factorization vs joint label classification on novel combinations; it does not establish general language understanding."}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as fp:
        json.dump(report,fp,indent=2,ensure_ascii=False,allow_nan=False);fp.write("\n")
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture",type=Path,default=FIXTURE)
    p.add_argument("--composition-report",type=Path,default=SOURCE_REPORT)
    p.add_argument("--device",default="cuda")
    p.add_argument("--allow-model-download",action="store_true")
    p.add_argument("--output",type=Path,required=True)
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
    r=run(args)
    result={"status":r["status"],"acceptance":r["acceptance"]["status"],
            "final_by_extraction_and_method":{view:{arm:{"atomic_six_field_exact":round(m["atomic"]["all_six_fields_exact"],6),
               "bound_graph_exact":round(m["composition"]["complete_relation_graph_exact"],6),
               "contradiction_detected":round(m["composition"]["true_contradiction_detected_rate"],6),
               "condition_scope_safe":round(m["composition"]["different_condition_no_false_contradiction_rate"],6),
               "actor_swap_both_exact":round(m["composition"]["binding_swaps"]["actor"]["both_documents_relation_exact"],6),
               "consequence_swap_both_exact":round(m["composition"]["binding_swaps"]["consequence"]["both_documents_relation_exact"],6)}
                  for arm,m in views.items()} for view,views in r["final"].items()},
            "gold_inventory_collision_on_swaps":{k:round(v["gold_unbound_inventory_collision_rate"],6)
                   for k,v in r["final_stats"]["oracle_structural_control"]["binding_swaps"].items()},
            "final_seen_tuples":r["final_stats"]["seen_tuple_count"],
            "final_unseen_tuples":r["final_stats"]["unseen_tuple_count"],
            "report":str(args.output)}
    print(smoke.canonical(result))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
