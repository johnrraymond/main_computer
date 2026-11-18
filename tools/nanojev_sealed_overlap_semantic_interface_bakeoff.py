#!/usr/bin/env python3
"""Frozen-Qwen grammatical augmentation + typed relational-interface experiment.

Separates (1) learned six-field extraction with train-only grammatical variations
from (2) what a typed relation representation can express, and (3) limited,
explicit surface interventions for one OOV name, an explicit same-supplier
reference, or a two-atom conjunction. No gold labels or challenge kind enter
inference. The representational oracle is independently reported, never an
inference arm. Original inspected language-stress final is diagnostic context;
the new authored grammar cohort is the principal evaluation.

This is *not* general contract parsing, unrestricted named-entity recognition,
pronoun resolution, or learned Boolean scope. Clause boundaries are explicit.

From repository root:
  & $py -m tools.nanojev_sealed_overlap_semantic_interface_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-semantic-interface-0001.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
import re
from string import Formatter
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_composition_bakeoff as composition
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress

SCHEMA = "nanojev.sealed-overlap.semantic-interface-bakeoff.v1"
STATUS = "COMPLETE_SEMANTIC_INTERFACE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "nanojev_overlap_semantic_interface_v1.json"
PRIOR_REPORT = Path("runtime/reports/sealed-overlap-language-stress-0001.json")
VIEWS = ("final:mean",)
ARMS = ("original", "duplicate_control", "grammar_augmented", "augmented_typed_surface")
CAPITALIZED = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
START_WORDS = {"The","With","When","Upon","Provided","Subject","Following",
               "After","Under","For","To","Once","This","An","And","If"}


def load_fixture(path: Path, base: dict, previous:dict) ->dict:
    f=json.loads(path.read_text(encoding="utf-8"))
    if f.get("schema")!="nanojev.sealed-overlap.semantic-interface-fixture.v1" or f.get("document_delimiter")!=" || ":
        raise ValueError("SEMANTIC_INTERFACE_FIXTURE_SCHEMA_INVALID")
    old=set(sum((base["templates"][k] for k in ("train","selection","final")),[]))
    old.update(previous["selection_templates"]+previous["final_templates"])
    seen=set()
    for field,expected in (("augmentation_templates",3),("fresh_templates",3)):
        items=f.get(field)
        if not isinstance(items,list) or len(items)!=expected:
            raise ValueError("SEMANTIC_INTERFACE_TEMPLATE_COUNT_INVALID")
        for t in items:
            slots=[name for _,name,_,_ in Formatter().parse(t) if name is not None]
            expected_slots=set(binding.FIELDS[:-1])|({"modal"} if "{modal}" in t else {"duty"})
            if set(slots)!=expected_slots or len(slots)!=len(expected_slots) or t in old or t in seen or "." in t or " || " in t:
                raise ValueError("SEMANTIC_INTERFACE_TEMPLATE_INVALID")
            seen.add(t)
    if f.get("positive")!={"modal":"is required to","duty":"a duty exists"} or f.get("negative")!={"modal":"is not required to","duty":"no duty exists"}:
        raise ValueError("SEMANTIC_INTERFACE_POLARITY_CHANGED")
    if f.get("reference_marker")!=previous["anaphor"] or f.get("compound_condition_policy","").split(";")[0]!="the conjunction of X and Y has been satisfied":
        raise ValueError("SEMANTIC_INTERFACE_SCOPE_CHANGED")
    return f


def validate_prior_report(path:Path,base_path:Path,stress_path:Path) ->dict:
    p=json.loads(path.read_text(encoding="utf-8"))
    if (p.get("schema")!=stress.SCHEMA or p.get("status")!=stress.STATUS
        or p.get("acceptance",{}).get("no_production_mutation") is not True
        or p.get("acceptance",{}).get("no_final_based_model_selection") is not True
        or p.get("design",{}).get("same_six_field_supervision") is not True
        or p.get("sources",{}).get("base_fixture_sha256")!=sha256(base_path.read_bytes()).hexdigest()
        or p.get("sources",{}).get("stress_fixture_sha256")!=sha256(stress_path.read_bytes()).hexdigest()
        or p.get("design",{}).get("training_atomic_clauses") is None):
        raise ValueError("SEMANTIC_INTERFACE_PRIOR_REPORT_INVALID")
    return p


def render(atom:tuple,f:dict,mode:str,index:int)->str:
    if len(atom)!=6 or atom[-1] not in (-1,1) or mode not in ("augmentation","fresh"):
        raise ValueError("SEMANTIC_INTERFACE_ATOM_INVALID")
    fields=dict(zip(binding.FIELDS,atom))
    phr=f["positive" if atom[-1]==1 else "negative"]
    return f[mode+"_templates"][index%3].format(**fields,**phr)


def augmented_training(training:list[dict],f:dict) ->list[dict]:
    # Exactly one new surface for each old tuple. No novel gold labels or final phrasing.
    addition=[{"text":render(row["atom"],f,"augmentation",i),"atom":row["atom"]}
              for i,row in enumerate(training)]
    previous={row["text"] for row in training}
    if previous.intersection(row["text"] for row in addition) or len({r["text"] for r in addition})!=len(addition):
        raise ValueError("SEMANTIC_INTERFACE_AUGMENT_DUPLICATE")
    return training+addition


def fresh_data(data:dict,base:dict,stress_fixture:dict,our:dict,*,seed:int,groups:int)->list[dict]:
    """Novel semantic pairs and novel lexical templates, independent of past final rows.

    Same original 216 labeled tuples; no new semantic label supervision.
    """
    if groups<8 or groups%8:raise ValueError("SEMANTIC_INTERFACE_GROUP_COUNT_INVALID")
    prior_pairs={tuple(sorted(row["atoms"])) for phase in ("selection","final")
                 for row in data[phase] if row["kind"]=="base"}
    training={r["atom"] for r in data["train"]}
    universe=set(itertools.product(*binding.values(base)))
    holdout=list(universe-training)
    rng=random.Random(seed+24523)
    used=set(prior_pairs)
    rows=[]
    for g in range(groups):
        pool=sorted(training if g%2==0 else holdout)
        for _ in range(50000):
            a,b=rng.sample(pool,2)
            key=tuple(sorted((a,b)))
            if key not in used and all(a[j]!=b[j] for j in range(5)):
                used.add(key);break
        else: raise ValueError("SEMANTIC_INTERFACE_HOLDOUT_EXHAUSTED")
        variants=binding.case_variants(a,b,base)
        a_oov=(stress_fixture["new_actors"][g%len(stress_fixture["new_actors"])],)+a[1:]
        b_oov=b[:1]+(stress_fixture["new_recipients"][g%len(stress_fixture["new_recipients"])],)+b[2:]
        other_cond=next(c for c in base["conditions"] if c!=a[3])
        nested=stress_fixture["nested_condition_connector"].format(first=a[3],second=other_cond)
        anaphor=b[:0]+(a[0],)+b[1:]
        variants.update({"oov_actor":[a_oov,b],"oov_recipient":[a,b_oov],
                         "nested_condition":[a[:3]+(nested,)+a[4:],b],
                         "anaphoric_actor":[a,anaphor]})
        if set(variants)!=set(stress.KINDS): raise ValueError("SEMANTIC_INTERFACE_VARIANTS_INVALID")
        for kind,atoms in variants.items():
            clauses=[render(atom,our,"fresh",g+i) for i,atom in enumerate(atoms)]
            if kind=="anaphoric_actor":
                # Label is resolved identity; observed sentence keeps a genuine reference.
                old_actor=atoms[1][0]
                if clauses[1].count(old_actor)!=1:raise ValueError("SEMANTIC_INTERFACE_ANAPHOR_AMBIGUOUS")
                clauses[1]=clauses[1].replace(old_actor,our["reference_marker"],1)
            rows.append({"id":f"fresh:{g:03d}:{kind}","kind":kind,"group_id":g,
                         "scope":"out_of_schema" if kind in stress.STRESS else "in_schema",
                         "text":binding.document(clauses),"atoms":atoms,
                         "grammar_template_ids":[(g+i)%3 for i in range(len(atoms))]})
    texts={r["text"] for r in rows}
    if len(texts)!=len(rows) or texts.intersection(row["text"] for phase in ("selection","final") for row in data[phase]):
        raise ValueError("SEMANTIC_INTERFACE_HOLDOUT_OVERLAP")
    return rows


def condition_ast(surface,known:list[str],*,depth:int=0)->tuple:
    """Typed nested Boolean scope; text extraction supports only one AND.

    A caller may supply an explicit JSON-like semantic AST with unbounded
    literal leaves. The learned extraction lane never receives such an AST;
    only the representational oracle exercises that broader type contract.
    """
    if depth>12:raise ValueError("SEMANTIC_INTERFACE_SCOPE_DEPTH_EXCEEDED")
    if isinstance(surface,dict):
        op=surface.get("op")
        if op=="atom" and set(surface)=={"op","value"} and isinstance(surface["value"],str) and surface["value"].strip():
            return ("atom",surface["value"])
        if op=="not" and set(surface)=={"op","child"}:
            return ("not",condition_ast(surface["child"],known,depth=depth+1))
        if op in ("and","or") and set(surface)=={"op","children"}:
            children=surface["children"]
            if not isinstance(children,list) or len(children)<2:
                raise ValueError("SEMANTIC_INTERFACE_SCOPE_CHILDREN_INVALID")
            nested=tuple(sorted(set(condition_ast(x,known,depth=depth+1) for x in children)))
            if len(nested)<2:raise ValueError("SEMANTIC_INTERFACE_SCOPE_DUPLICATE_CHILDREN")
            return (op,)+nested
        raise ValueError("SEMANTIC_INTERFACE_SCOPE_OPERATOR_UNSUPPORTED")
    if not isinstance(surface,str):raise ValueError("SEMANTIC_INTERFACE_CONDITION_UNSUPPORTED")
    if surface in known:return ("atom",surface)
    prefix="the conjunction of "; suffix=" has been satisfied"
    if surface.startswith(prefix) and surface.endswith(suffix):
        inside=surface[len(prefix):-len(suffix)]
        possibilities=[(a,b) for a in known for b in known if a!=b and inside==a+" and "+b]
        if len(possibilities)!=1:raise ValueError("SEMANTIC_INTERFACE_CONDITION_UNPARSEABLE")
        a,b=possibilities[0]
        return ("and",)+tuple(sorted((("atom",a),("atom",b))))
    raise ValueError("SEMANTIC_INTERFACE_CONDITION_UNSUPPORTED")


def typed_atom(atom:tuple,known:list[str],*,antecedent:str|None=None)->tuple:
    if len(atom)!=6 or atom[-1] not in (-1,1): raise ValueError("SEMANTIC_INTERFACE_RELATION_INVALID")
    a,r,asset,condition,result,sign=atom
    if isinstance(a,dict):
        if a!={"ref":"previous_actor"} or antecedent is None:
            raise ValueError("SEMANTIC_INTERFACE_UNRESOLVED_REFERENCE")
        a=antecedent
    if not all(isinstance(s,str) and s.strip() for s in (a,r,asset,result)):
        raise ValueError("SEMANTIC_INTERFACE_RELATION_FIELD_INVALID")
    return (a,r,asset,condition_ast(condition,known),result,sign)


def typed_relations(atoms:list[tuple],known:list[str])->list[tuple]:
    resolved=[];preceding_actor=None
    for atom in atoms:
        rel=typed_atom(atom,known,antecedent=preceding_actor)
        resolved.append(rel)
        preceding_actor=rel[0]
    return resolved


def typed_graph(atoms:list[tuple],known:list[str])->tuple:
    # Complete graphs retain roles and nested scope; clause order is ignored
    # only AFTER explicit references have been resolved in document order.
    return tuple(sorted(set(typed_relations(atoms,known))))


def typed_contradiction(atoms:list[tuple],known:list[str])->bool:
    seen=defaultdict(set)
    for relation in typed_relations(atoms,known):
        seen[relation[:-1]].add(relation[-1])
    return any(v=={-1,1} for v in seen.values())


def discover_oov(text:str,base:dict)->str|None:
    known=set(base["actors"]+base["recipients"])
    unknown=sorted(set(CAPITALIZED.findall(text))-known-START_WORDS)
    if len(unknown)>1:raise ValueError("SEMANTIC_INTERFACE_MULTIPLE_UNKNOWN_ENTITIES")
    return unknown[0] if unknown else None


def parse_compound_surface(text:str,conditions:list[str])->tuple[str|None,str|None]:
    # Grammar-limited conjunction: find a complete canonical surface from two
    # known condition atoms. Never use gold labels or the challenge kind.
    prefix="the conjunction of "; suffix=" has been satisfied"
    matches=[prefix+a+" and "+b+suffix for a in conditions for b in conditions if a!=b]
    matches=[x for x in matches if x in text]
    if len(matches)>1:raise ValueError("SEMANTIC_INTERFACE_MULTIPLE_COMPOUNDS")
    return (matches[0],matches[0][len(prefix):].split(" and ")[0]) if matches else (None,None)


def surface_transform(text:str,base:dict,*,antecedent:str|None,reference_marker:str)->tuple[str,dict]:
    """Clause-only observed-text features, not fixture gold or challenge ID.

    Supports exactly one novel entity when the other role is known. Resolves
    one explicit same-supplier phrase using a previously PREDICTED actor. It
    does not attempt unrestricted NER or pronoun/coreference resolution.
    """
    changes={"novel_actor":None,"novel_recipient":None,"compound":None,"referenced_actor":None,
             "supported":True,"reason":None}
    sentence=text
    if reference_marker in sentence:
        if sentence.count(reference_marker)!=1 or antecedent is None:
            changes.update(supported=False,reason="REFERENCE_NO_PRIOR_PREDICTED_ACTOR")
            return sentence,changes
        sentence=sentence.replace(reference_marker,antecedent,1)
        changes["referenced_actor"]=antecedent
    c,replacement=parse_compound_surface(sentence,base["conditions"])
    if c:
        sentence=sentence.replace(c,replacement,1)
        changes["compound"]=c
    novel=discover_oov(sentence,base)
    if novel:
        actor_names=set(base["actors"]);recipient_names=set(base["recipients"])
        has_actor=any(re.search(r"\b"+re.escape(x)+r"\b",sentence) for x in actor_names)
        has_recipient=any(re.search(r"\b"+re.escape(x)+r"\b",sentence) for x in recipient_names)
        if has_actor == has_recipient:
            changes.update(supported=False,reason="OOV_ROLE_AMBIGUOUS")
            return sentence,changes
        role="recipient" if has_actor else "actor"
        surrogate=base["recipients"][0] if has_actor else base["actors"][0]
        sentence=re.sub(r"\b"+re.escape(novel)+r"\b",surrogate,sentence,count=1)
        changes["novel_"+role]=novel
    return sentence,changes


def fit_model(features:np.ndarray,labels:list[tuple],base:dict,ridge:float)->dict:
    # Unary-only ridge head. Unlike the historical joint head, this deliberately
    # permits repeated supervision for a controlled duplicate-example ablation.
    x=composition.normalized(features,rows=len(labels))
    if len(labels)<2 or ridge<=0:raise ValueError("SEMANTIC_INTERFACE_MODEL_INVALID")
    opts=binding.values(base)
    indexes=[{v:i for i,v in enumerate(c)} for c in opts]
    intervals=[];offset=0
    for vocab in opts:
        intervals.append((offset,offset+len(vocab)))
        offset+=len(vocab)
    yy=np.zeros((len(labels),offset),dtype=np.float64)
    for i,atom in enumerate(labels):
        for j,value in enumerate(atom):yy[i,intervals[j][0]+indexes[j][value]]=1.
    d=np.maximum(2.-2.*(x@x.T),0.)
    pos=d[np.triu_indices(len(x),1)]
    pos=pos[pos>1e-12]
    gamma=float(np.log(2.)/np.median(pos)) if len(pos) else 1.
    k=np.exp(-gamma*d)
    mean=yy.mean(axis=0)
    weights=np.linalg.solve(k+ridge*np.eye(len(x)),yy-mean)
    return {"x":x,"mean":mean,"weights":weights,"gamma":gamma,"segments":intervals}


def predict_model(model:dict,features:np.ndarray,base:dict)->list[tuple]:
    x=composition.normalized(features,width=model["x"].shape[1])
    d=np.maximum(2.-2.*(x@model["x"].T),0.)
    scores=model["mean"]+np.exp(-model["gamma"]*d)@model["weights"]
    vocabs=binding.values(base)
    return [tuple(vocabs[j][int(np.argmax(row[lo:hi]))]
                  for j,(lo,hi) in enumerate(model["segments"])) for row in scores]


def phase_scoring(rows:list[dict],pred:list[list[tuple]],conditions:list[str])->dict:
    kinds={k:[i for i,r in enumerate(rows) if r["kind"]==k] for k in stress.KINDS}
    by_kind={}
    for kind,indices in kinds.items():
        if not indices:continue
        matched=[];field=[];contradictions=[]
        for i in indices:
            truth=rows[i]["atoms"]
            obs=pred[i]
            matched.append(typed_graph(obs,conditions)==typed_graph(truth,conditions))
            field.extend([tuple(int(a[j]==b[j]) for j in range(6)) for a,b in zip(truth,obs)])
            contradictions.append(typed_contradiction(obs,conditions)==typed_contradiction(truth,conditions))
        field=np.asarray(field,dtype=int)
        by_kind[kind]={"documents":len(indices),"graph_exact":float(np.mean(matched)),
                       "atomic_six_field_exact":float(np.all(field,axis=1).mean()),
                       "by_field":dict(zip(binding.FIELDS,[float(field[:,j].mean()) for j in range(6)])),
                       "contradiction_decision_accuracy":float(np.mean(contradictions))}
    inc=[i for i,r in enumerate(rows) if r["scope"]=="in_schema"]
    comparison=[[int(a[j]==b[j]) for j in range(6)] for i in inc for a,b in zip(rows[i]["atoms"],pred[i])]
    matrix=np.asarray(comparison,dtype=int)
    correct=[typed_graph(pred[i],conditions)==typed_graph(rows[i]["atoms"],conditions) for i in inc]
    consequence_confusion=defaultdict(lambda:defaultdict(int))
    actor_confusion=defaultdict(lambda:defaultdict(int))
    grammar=defaultdict(list)
    for i in inc:
        for j,(actual,predicted) in enumerate(zip(rows[i]["atoms"],pred[i])):
            consequence_confusion[actual[4]][predicted[4]]+=1
            actor_confusion[actual[0]][predicted[0]]+=1
            if "grammar_template_ids" in rows[i]:
                grammar[rows[i]["grammar_template_ids"][j]].append((actual,predicted))
    consequence_confusion={true:dict(preds) for true,preds in sorted(consequence_confusion.items())}
    actor_confusion={true:dict(preds) for true,preds in sorted(actor_confusion.items())}
    grammar_breakdown={str(k):{"clauses":len(items),
              "consequence_accuracy":float(np.mean([a[4]==p[4] for a,p in items])),
              "actor_accuracy":float(np.mean([a[0]==p[0] for a,p in items])),
              "six_field_exact":float(np.mean([a==p for a,p in items]))}
              for k,items in sorted(grammar.items())}
    return {"in_schema":{"documents":len(inc),"graph_exact":float(np.mean(correct)),
             "atomic_six_field_exact":float(np.all(matrix,axis=1).mean()),
             "by_field":dict(zip(binding.FIELDS,[float(matrix[:,j].mean()) for j in range(6)]))},
             "by_challenge":by_kind,"consequence_confusion":consequence_confusion,
            "actor_confusion":actor_confusion,"by_grammar_template":grammar_breakdown}


def evaluate_rows(rows:list[dict],models:dict,encoder:Callable,base:dict,fixture:dict)->dict:
    unique,mapping=binding.catalog(rows)
    embedded=composition.views_from_encoder(encoder,unique)
    # The first three heads see exactly the same raw text; no input metadata.
    out={}
    augmented_raw_pred=None
    for arm in ("original","duplicate_control","grammar_augmented"):
        x=embedded["final:mean"]
        predicted=predict_model(models[arm],x,base)
        if arm=="grammar_augmented":augmented_raw_pred=predicted
        out[arm]=binding.group_predictions(predicted,mapping)
    # Typed surface intervention decodes clauses in DOCUMENT order. References
    # can use only earlier PREDICTED actors in that same document. The same
    # observed clause can therefore resolve differently in distinct documents.
    # Query the large, non-anaphoric batch only once. Context-sensitive
    # references are a separate tiny pass after antecedent predictions exist.
    cache=dict(zip(unique,augmented_raw_pred))
    def lookup(strings):
        missing=list(dict.fromkeys(s for s in strings if s not in cache))
        if missing:
            embeddings=composition.views_from_encoder(encoder,missing)["final:mean"]
            preds=predict_model(models["grammar_augmented"],embeddings,base)
            cache.update(zip(missing,preds))
        return [cache[s] for s in strings]
    precomputed=[]
    for row in rows:
        for clause in binding.split_document(row["text"]):
            if fixture["reference_marker"] in clause:continue
            normalized,m=surface_transform(clause,base,antecedent=None,
                                          reference_marker=fixture["reference_marker"])
            precomputed.append(normalized if m["supported"] else clause)
    lookup(precomputed)
    references=[]
    for row in rows:
        clauses=binding.split_document(row["text"])
        for index,clause in enumerate(clauses):
            if fixture["reference_marker"] not in clause:continue
            if index!=1 or len(clauses)!=2:
                raise ValueError("SEMANTIC_INTERFACE_REFERENCE_COMPLEXITY_UNSUPPORTED")
            first,m=surface_transform(clauses[0],base,antecedent=None,
                                      reference_marker=fixture["reference_marker"])
            first_atom=list(lookup([first if m["supported"] else clauses[0]])[0])
            if m["novel_actor"] is not None:first_atom[0]=m["novel_actor"]
            second,m=surface_transform(clause,base,antecedent=first_atom[0],
                                       reference_marker=fixture["reference_marker"])
            references.append(second if m["supported"] else clause)
    lookup(references)
    seq=[];counts=defaultdict(int)
    for row in rows:
        antecedent=None;document=[]
        for clause in binding.split_document(row["text"]):
            normalized,meta=surface_transform(clause,base,antecedent=antecedent,
                                              reference_marker=fixture["reference_marker"])
            if not meta["supported"]:
                # Closed-class model output remains visible as a failure, but
                # unsupported role inference never silently imports labels.
                counts[meta["reason"]]+=1
                pred=lookup([clause])[0]
            else:
                pred=lookup([normalized])[0]
                out_atom=list(pred)
                if meta["novel_actor"] is not None:out_atom[0]=meta["novel_actor"]
                if meta["novel_recipient"] is not None:out_atom[1]=meta["novel_recipient"]
                if meta["compound"] is not None:out_atom[3]=meta["compound"]
                if meta["referenced_actor"] is not None:out_atom[0]=meta["referenced_actor"]
                pred=tuple(out_atom)
                for key in ("novel_actor","novel_recipient","compound","referenced_actor"):
                    if meta[key] is not None:counts[key]+=1
            antecedent=pred[0]
            document.append(pred)
        seq.append(document)
    out["augmented_typed_surface"]=seq
    return {arm:phase_scoring(rows,pred,base["conditions"]) for arm,pred in out.items()},dict(counts)


def run(args:argparse.Namespace,*,encoder:Callable|None=None)->dict:
    if args.output.exists():raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if args.max_length!=192 or args.batch_size<1 or args.ridge<=0:raise ValueError("SEMANTIC_INTERFACE_CONFIGURATION_INVALID")
    base=binding.load_fixture(args.base_fixture)
    previous=stress.load_fixture(args.stress_fixture,base)
    fixture=load_fixture(args.fixture,base,previous)
    source=validate_prior_report(args.prior_report,args.base_fixture,args.stress_fixture)
    if source["design"]["training_atomic_clauses"]!=args.train_atoms:
        raise ValueError("SEMANTIC_INTERFACE_TRAINING_PROVENANCE_CHANGED")
    data=stress.derive_data(base,previous,seed=args.seed,train_atoms=args.train_atoms,
                            selection_groups=args.selection_groups,final_groups=args.final_groups)
    fresh=fresh_data(data,base,previous,fixture,seed=args.seed,groups=args.fresh_groups)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,
                   allow_download=args.allow_model_download,batch_size=args.batch_size,
                   max_length=args.max_length)
    positions=getattr(encoder,"positions",None)
    if positions is not None and (positions.get("half")!=14 or positions.get("final")!=28):
        raise ValueError("SEMANTIC_INTERFACE_LAYER_LAYOUT_CHANGED")
    old=data["train"];aug=augmented_training(old,fixture)
    x_original=composition.views_from_encoder(encoder,[r["text"] for r in old])["final:mean"]
    x_new=composition.views_from_encoder(encoder,[r["text"] for r in aug[len(old):]])["final:mean"]
    y_original=[r["atom"] for r in old]
    augmented_features=np.concatenate((x_original,x_new),axis=0)
    models={
        "original":fit_model(x_original,y_original,base,args.ridge),
        "duplicate_control":fit_model(np.concatenate((x_original,x_original),axis=0),y_original*2,base,args.ridge),
        "grammar_augmented":fit_model(augmented_features,y_original*2,base,args.ridge),
    }
    # The previously inspected final set is a regression diagnostic only.
    historical,historical_transforms=evaluate_rows(data["final"],models,encoder,base,fixture)
    principal,principal_transforms=evaluate_rows(fresh,models,encoder,base,fixture)
    oracle={}
    for name,rows in (("historical",data["final"]),("fresh",fresh)):
        truths=[r["atoms"] for r in rows]
        test=phase_scoring(rows,truths,base["conditions"])
        if any(test["by_challenge"][k]["graph_exact"]!=1 for k in stress.KINDS):
            raise ValueError("SEMANTIC_INTERFACE_TYPED_ORACLE_INVALID")
        oracle[name]={"graph_exact":test["in_schema"]["graph_exact"],
                      "out_of_schema_graph_exact":{k:test["by_challenge"][k]["graph_exact"] for k in stress.STRESS}}
    if getattr(encoder,"stats",None) is not None and encoder.stats.get("texts_truncated",0):
        raise ValueError("SEMANTIC_INTERFACE_TRUNCATED")
    report={"schema":SCHEMA,"status":STATUS,"acceptance":{
        "status":"DIAGNOSTIC_ONLY_NOT_SEALABLE","never_updates_model_weights":True,
        "vocabulary_unchanged":True,"no_contract_seal":True,"no_production_mutation":True,
        "no_final_based_model_selection":True,"ollama_called":False},
        "sources":{"previous_report_sha256":sha256(args.prior_report.read_bytes()).hexdigest(),
                   "base_fixture_sha256":sha256(args.base_fixture.read_bytes()).hexdigest(),
                   "stress_fixture_sha256":sha256(args.stress_fixture.read_bytes()).hexdigest(),
                   "interface_fixture_sha256":sha256(args.fixture.read_bytes()).hexdigest()},
        "design":{"model":smoke.MODEL_ID,"model_frozen":True,"view":"final:mean",
            "original_training_examples":len(old),"additional_surface_forms":len(old),
            "duplicate_control_examples":2*len(old),"augmentation_examples":len(aug),
            "no_new_distinct_gold_training_tuples":True,
            "augmentation_uses_train_only_templates":True,"all_heads_use_identical_field_labels":True,
            "historical_final_reused_diagnostic_only":True,"fresh_groups":args.fresh_groups,
            "fresh_documents":len(fresh),"fresh_grammar_templates":3,
            "fixture_delimiter":" || ","ridge":args.ridge,
            "oracle_is_not_inference":True,
            "typed_representation_supports":["unseen proper-name literals","explicit conjunction of two known atomic conditions","same-supplier reference to prior predicted actor"],
            "typed_surface_limitations":"One novel capitalized name with a known counterparty, conjunction of two known condition strings, exact explicit same-supplier marker; no unrestricted entity detection, coreference, or nested scope generalization",
            "token_stats":getattr(encoder,"stats",None)},
        "historical":historical,"historical_transforms":historical_transforms,
        "fresh":principal,"fresh_transforms":principal_transforms,"typed_oracle":oracle,
        "interpretation":"A typed oracle measures representability only. Augmentation vs duplicate control separates extra grammatical variety from the effect of extra supervised examples (same labels). Fresh grammar is independent but still authored single-predicate clauses. The surface arm performs documented, narrow text-only copying and literal condition parsing; it is not a general language parser."}
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
    p.add_argument("--base-fixture",type=Path,default=binding.FIXTURE)
    p.add_argument("--stress-fixture",type=Path,default=stress.FIXTURE)
    p.add_argument("--prior-report",type=Path,default=PRIOR_REPORT)
    p.add_argument("--train-atoms",type=int,default=216)
    p.add_argument("--selection-groups",type=int,default=16)
    p.add_argument("--final-groups",type=int,default=24)
    p.add_argument("--fresh-groups",type=int,default=24)
    p.add_argument("--seed",type=int,default=261117)
    p.add_argument("--ridge",type=float,default=.1)
    p.add_argument("--batch-size",type=int,default=4)
    p.add_argument("--max-length",type=int,default=192)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    report=run(args)
    def short(phase):
        return {arm:{"complete_graph":round(m["in_schema"]["graph_exact"],6),
                     "consequence":round(m["in_schema"]["by_field"]["consequence"],6),
                     "actor":round(m["in_schema"]["by_field"]["actor"],6),
                     "oov_actor":round(m["by_challenge"]["oov_actor"]["graph_exact"],6),
                     "oov_recipient":round(m["by_challenge"]["oov_recipient"]["graph_exact"],6),
                     "nested_condition":round(m["by_challenge"]["nested_condition"]["graph_exact"],6),
                     "anaphoric_actor":round(m["by_challenge"]["anaphoric_actor"]["graph_exact"],6)}
                for arm,m in report[phase].items()}
    print(smoke.canonical({"status":report["status"],"acceptance":report["acceptance"]["status"],
          "fresh":short("fresh"),"historical":short("historical"),
          "typed_oracle":report["typed_oracle"],"report":str(args.output)}))
    return 0

if __name__=="__main__":raise SystemExit(main())
