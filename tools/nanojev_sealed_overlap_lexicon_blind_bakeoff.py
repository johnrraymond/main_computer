#!/usr/bin/env python3
"""Frozen-hybrid lexicon-blind semantic stress and fail-closed acceptance bakeoff.

Do NOT modify or train any Qwen weights. Fit the prior declared training-only
ridge heads exactly as the 2026-11-17 repair runner does. The primary hybrid
uses the unchanged repair text observations, overlays and reference policy.
An independently predeclared gate may ABSTAIN, never edit the hybrid's answer.

Unknown synonyms have labeled semantics for *evaluation*, but no mapping from
those synonyms enters inference. A refusal on those rows is safe, NOT a semantic
success. Ambiguous/no-operative clauses carry no forced binary gold polarity.
No template IDs, gold tuples, challenge/policy labels or source truth enter
inference. Every input clause is synthetically delimited with " || ".

From repo root:
  & $py -m tools.nanojev_sealed_overlap_lexicon_blind_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-lexicon-blind-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
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
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair

SCHEMA='nanojev.sealed-overlap.lexicon-blind-bakeoff.v1'
STATUS='COMPLETE_LEXICON_BLIND_DIAGNOSTIC_NO_SEAL_NO_PROMOTION'
HERE=Path(__file__).resolve().parent
FIXTURE=HERE/'fixtures'/'nanojev_overlap_lexicon_blind_v1.json'
PRIOR_REPORT=Path('runtime/reports/sealed-overlap-semantic-interface-repair-0001.json')
POLICIES={'supported','abstain'}
TRUTHS={'positive','negative','positive_scope_not','positive_novel','indeterminate'}


def load_fixture(path:Path,base:dict,stress_fixture:dict,iface:dict,repair_fixture:dict)->dict:
    f=json.loads(path.read_text(encoding='utf-8'))
    if f.get('schema')!='nanojev.sealed-overlap.lexicon-blind-fixture.v1' or f.get('document_delimiter')!=' || ':
        raise ValueError('LEXICON_BLIND_FIXTURE_SCHEMA_INVALID')
    names=f.get('fresh_names')
    forbidden=set(base['actors']+base['recipients']+repair_fixture['new_actors']+repair_fixture['new_recipients']+
                  stress_fixture['new_actors']+stress_fixture['new_recipients'])
    if (not isinstance(names,list) or len(names)<8 or len(names)!=len(set(names))
        or forbidden.intersection(names) or any(not re.fullmatch('[A-Z][a-zA-Z]{3,}',n) for n in names)):
        raise ValueError('LEXICON_BLIND_NAMES_INVALID')
    previous_templates=set(sum((base['templates'][k] for k in ('train','selection','final')),[]))
    previous_templates.update(stress_fixture['selection_templates']+stress_fixture['final_templates'])
    previous_templates.update(iface['augmentation_templates']+iface['fresh_templates'])
    previous_templates.update(repair_fixture['targeted_training_templates']+repair_fixture['new_holdout_templates'])
    challenges=f.get('challenges')
    if not isinstance(challenges,list) or len(challenges)!=18 or len({x.get('kind') for x in challenges})!=18:
        raise ValueError('LEXICON_BLIND_CHALLENGES_INVALID')
    templates=set()
    for item in challenges:
        if item.get('policy') not in POLICIES or item.get('truth') not in TRUTHS:
            raise ValueError('LEXICON_BLIND_EXPECTATIONS_INVALID')
        tpl=item.get('template')
        if not isinstance(tpl,str) or tpl in previous_templates or tpl in templates or ' || ' in tpl:
            raise ValueError('LEXICON_BLIND_TEMPLATE_LEAK_OR_DUPLICATE')
        fields=[p for _,p,_,_ in Formatter().parse(tpl) if p is not None]
        if (len(fields)!=len(set(fields)) or not {'asset','condition','consequence'}.issubset(fields)
            or (item['policy']=='supported' and not ({'actor','novel_actor'} & set(fields)))
            or not ({'recipient','novel_recipient'} & set(fields))):
            raise ValueError('LEXICON_BLIND_TEMPLATE_FIELDS_INVALID')
        if not set(fields).issubset({'actor','recipient','asset','condition','consequence','novel_actor','novel_recipient','other_actor','other_condition'}):
            raise ValueError('LEXICON_BLIND_TEMPLATE_FIELDS_INVALID')
        templates.add(tpl)
    if any(c['policy']=='supported' and c['truth']=='indeterminate' for c in challenges):
        raise ValueError('LEXICON_BLIND_SUPPORTED_UNKNOWN_TRUTH')
    return f


def validate_prior(path:Path,base_path:Path,stress_path:Path,interface_path:Path,repair_path:Path)->dict:
    report=json.loads(path.read_text(encoding='utf-8'))
    d=report.get('design',{});s=report.get('sources',{})
    if (report.get('schema')!=repair.SCHEMA or report.get('status')!=repair.STATUS
        or report.get('acceptance',{}).get('no_production_mutation') is not True
        or report.get('acceptance',{}).get('no_final_based_model_selection') is not True
        or d.get('extraction')!='final:mean' or d.get('original_examples')!=216
        or d.get('generic_augmentation_examples')!=216 or d.get('targeted_augmentation_examples')!=216
        or not {'targeted_grammar','targeted_lexical','targeted_typed'}.issubset(report.get('new_holdout',{}))
        or any(s.get(k)!=sha256(p.read_bytes()).hexdigest() for k,p in (
          ('base_fixture_sha256',base_path),('stress_fixture_sha256',stress_path),
          ('interface_fixture_sha256',interface_path),('repair_fixture_sha256',repair_path)))):
        raise ValueError('LEXICON_BLIND_PRIOR_REPORT_INVALID')
    return report


def make_holdout(data:dict,base:dict,st:dict,iface:dict,rf:dict,fixture:dict,*,seed:int,groups:int)->list[dict]:
    if groups<8 or groups%8:raise ValueError('LEXICON_BLIND_GROUPS_INVALID')
    rng=random.Random(seed+877121)
    training={tuple(x['atom']) for x in data['train']}
    unseen=sorted(set(itertools.product(*binding.values(base)))-training)
    # Previously inspected semantic base pairs are excluded.
    seen={tuple(sorted(tuple(x) for x in r['atoms'])) for phase in ('selection','final') for r in data[phase] if r['kind']=='base'}
    prior=repair.make_fresh(data,base,iface,st,rf,seed=seed,groups=groups)
    seen.update(tuple(sorted(tuple(x) for x in r['atoms'])) for r in prior if r['kind']=='base')
    pairs=[]
    for g in range(groups):
        pool=sorted(training) if g%2==0 else unseen
        for _ in range(30000):
            a,b=rng.sample(pool,2)
            pair=tuple(sorted((a,b)))
            if pair not in seen and all(a[j]!=b[j] for j in range(5)):
                seen.add(pair);pairs.append((a,b));break
        else:raise ValueError('LEXICON_BLIND_PAIR_EXHAUSTION')
    rows=[]
    for g,(a,b) in enumerate(pairs):
        novel_actor=fixture['fresh_names'][g%len(fixture['fresh_names'])]
        novel_recipient=fixture['fresh_names'][(g+3)%len(fixture['fresh_names'])]
        other_actor=next(x for x in base['actors'] if x!=a[0])
        other_condition=next(x for x in base['conditions'] if x!=a[3])
        fmt={**dict(zip(binding.FIELDS,a)),
             'novel_actor':novel_actor,'novel_recipient':novel_recipient,
             'other_actor':other_actor,'other_condition':other_condition}
        # A single unmodified reference clause ensures paired document composition.
        second=repair.render(b,rf,'new_holdout_templates',0)
        for spec in fixture['challenges']:
            first=spec['template'].format(**fmt)
            truth=spec['truth'];atom=list(a)
            if truth=='negative':atom[5]=-1
            if truth=='positive':atom[5]=1
            if truth=='positive_scope_not':atom[3]='(not '+a[3]+')';atom[5]=1
            if truth=='positive_novel':atom[0]=novel_actor;atom[1]=novel_recipient;atom[5]=1
            truth_atoms=None if truth=='indeterminate' else [tuple(atom),b]
            rows.append({'id':f'blind:{g:03d}:{spec["kind"]}',
                         'group_id':g,'kind':spec['kind'],'policy':spec['policy'],
                         'truth_atoms':truth_atoms,'text':binding.document([first,second])})
    if len({r['text'] for r in rows})!=len(rows):raise ValueError('LEXICON_BLIND_DUPLICATE_HOLDOUT')
    if {r['text'] for r in rows}&{r['text'] for r in prior}:
        raise ValueError('LEXICON_BLIND_PRIOR_TEXT_OVERLAP')
    return rows


def fail_closed_gate(document:str,base:dict,refs:list[str])->tuple[bool,tuple[str,...]]:
    """Predeclared high-precision *refusal* policy. Never edits decoded semantics.

    This is deliberately restrictive. Strong refusal results mean safe abstention,
    not successful comprehension of unrecognized language.
    """
    reasons=set()
    for clause in binding.split_document(document):
        l=clause.lower()
        if "'" in clause or '"' in clause or '“' in clause or '”' in clause:
            reasons.add('QUOTED_OR_ATTRIBUTED_DUTY')
        # Reject alternate logical scope and metalinguistic reversal that the
        # existing clause-level semantic contract cannot represent. Do not
        # list or teach the unfamiliar obligation synonyms in the holdout:
        # unsupported wording is handled by the positive evidence requirement
        # (extract_polarity returns None), not a holdout-specific blacklist.
        if re.search(r'\b(?:unless|except|only if|notwithstanding|whereas|false that|not true that)\b',l):
            reasons.add('UNSUPPORTED_SCOPE_OR_META_NEGATION')
        if re.search(r'\b(?:they|them|their|someone|either|whoever)\b',l):
            reasons.add('UNRESOLVED_ENTITY_REFERENCE')
        named_actor=set(re.findall(r'\bactor is (?:also )?([A-Z][A-Za-z]{2,})\b',clause))
        named_recipient=set(re.findall(r'\brecipient is (?:also )?([A-Z][A-Za-z]{2,})\b',clause))
        if len(named_actor)>1 or len(named_recipient)>1:
            reasons.add('CONTRADICTORY_NAMED_ROLE_BINDING')
        roles,issue=repair.observed_roles(clause,refs,None,base)
        if issue:reasons.add('ROLE_'+issue)
        if len(roles)!=2:reasons.add('ROLE_NOT_UNIQUELY_GROUNDED')
        asset=repair.extract_asset(clause,base)
        if asset is None:reasons.add('ASSET_NOT_UNIQUELY_GROUNDED')
        polarity=repair.extract_polarity(clause)
        if polarity is None:reasons.add('DUTY_POLARITY_UNSUPPORTED_OR_CONFLICTING')
        consequence=[c for c in base['consequences'] if re.search(r'(?<![A-Za-z])'+re.escape(c)+r'(?![A-Za-z])',clause)]
        if len(consequence)!=1:reasons.add('CONSEQUENCE_NOT_UNIQUELY_GROUNDED')
        cond,cond_issue=repair.observed_condition(clause,base)
        if cond_issue:reasons.add('CONDITION_'+cond_issue)
        if cond is None:
            matched=[c for c in base['conditions'] if c in clause]
            if len(matched)!=1:reasons.add('CONDITION_NOT_UNIQUELY_GROUNDED')
        # Reject unparsed multiple conditions even when one is recognizable.
        elif not cond_issue and cond is not None:
            try:repair.condition_node(cond,base)
            except ValueError:reasons.add('CONDITION_NOT_PARSED')
    return not reasons,tuple(sorted(reasons))


def train_frozen(encoder:Callable,base:dict,st:dict,iface:dict,rf:dict,data:dict,*,ridge:float):
    """Bit-for-bit training recipe from repair: 216+216+216, no test fitting."""
    train=data['train'];generic=interface.augmented_training(train,iface)[len(train):]
    targeted=repair.targeted_training(train,rf)
    blocks=[composition.views_from_encoder(encoder,[x['text'] for x in items])['final:mean']
            for items in (train,generic,targeted)]
    y=[r['atom'] for r in train]
    return interface.fit_model(np.vstack(blocks),y*3,base,ridge)


def predict_frozen(rows:list[dict],model,encoder:Callable,base:dict,fixture:dict)->list[list[tuple]]:
    """Same normalization / overlay / prediction flow as repair.evaluate.

    The fact that the policy or ground truth belongs to a challenge is never
    an input to this decoder. Caching changes speed, not predictions.
    """
    rawtexts=[x for row in rows for x in binding.split_document(row['text'])]
    raw_unique=list(dict.fromkeys(rawtexts))
    raw_emb=composition.views_from_encoder(encoder,raw_unique)['final:mean']
    embed_cache=dict(zip(raw_unique,raw_emb))
    predict_cache={}
    refs=fixture['reference_markers']
    def predict(s):
        if s not in predict_cache:
            if s not in embed_cache:
                embed_cache[s]=composition.views_from_encoder(encoder,[s])['final:mean'][0]
            predict_cache[s]=interface.predict_model(model,np.asarray([embed_cache[s]]),base)[0]
        return predict_cache[s]
    # Predict canonical, text-only normalizations once in batches.
    possible=[]
    for clause in raw_unique:
        roles,issue=repair.observed_roles(clause,refs,None,base)
        cond,cond_issue=repair.observed_condition(clause,base)
        if not issue and not cond_issue:
            possible.append(repair.normalize_for_model(clause,base,roles,cond,refs))
    missing=[s for s in dict.fromkeys(possible) if s not in embed_cache]
    if missing:
        batch=composition.views_from_encoder(encoder,missing)['final:mean']
        embed_cache.update(zip(missing,batch))
    results=[]
    for row in rows:
        last=None;predicted=[]
        for clause in binding.split_document(row['text']):
            roles,issue=repair.observed_roles(clause,refs,last,base)
            cond,cond_issue=repair.observed_condition(clause,base)
            normalized=repair.normalize_for_model(clause,base,roles,cond,refs) if not (issue or cond_issue) else clause
            atom=list(predict(normalized))
            asset=repair.extract_asset(clause,base)
            polarity=repair.extract_polarity(clause)
            if asset is not None:atom[2]=asset
            if polarity is not None:atom[5]=polarity
            if not issue:
                for k in ('actor','recipient'):
                    if k in roles:atom[binding.FIELDS.index(k)]=roles[k]
            if cond is not None and not cond_issue:atom[3]=cond
            last=atom[0];predicted.append(tuple(atom))
        results.append(predicted)
    return results


def score(rows:list[dict],predictions:list[list[tuple]],base:dict,refs:list[str])->dict:
    if len(rows)!=len(predictions):raise ValueError('LEXICON_BLIND_PREDICTION_COUNT')
    cohorts=defaultdict(list);reasons=Counter();values=[]
    for row,pred in zip(rows,predictions):
        accepted,why=fail_closed_gate(row['text'],base,refs)
        reasons.update(why)
        gold=row['truth_atoms']
        correct=None if gold is None else (repair.graph(gold,base)==repair.graph(pred,base))
        if gold is not None and accepted and not correct:reasons['UNSAFE_CONFIDENT_WRONG']+=1
        if gold is None and accepted:reasons['UNSAFE_ACCEPTED_INDETERMINATE']+=1
        first_correct=None if gold is None else (gold[0]==pred[0])
        first_polarity=None if gold is None else (gold[0][-1]==pred[0][-1])
        info={'kind':row['kind'],'policy':row['policy'],'accepted':accepted,'correct':correct,
              'first_correct':first_correct,'first_polarity':first_polarity,'reasons':why}
        cohorts[row['kind']].append(info);values.append(info)
    def aggregate(items):
        labeled=[x for x in items if x['correct'] is not None]
        accepted=[x for x in items if x['accepted']]
        accepted_labeled=[x for x in accepted if x['correct'] is not None]
        target_supported=[x for x in items if x['policy']=='supported']
        target_abstain=[x for x in items if x['policy']=='abstain']
        return {'documents':len(items),'labeled_documents':len(labeled),
                'raw_graph_exact':(sum(x['correct'] for x in labeled)/len(labeled) if labeled else None),
                'challenged_clause_exact':(sum(x['first_correct'] for x in labeled)/len(labeled) if labeled else None),
                'challenged_clause_polarity_accuracy':(sum(x['first_polarity'] for x in labeled)/len(labeled) if labeled else None),
                'acceptance_coverage':len(accepted)/len(items) if items else None,
                'selective_graph_exact':(sum(x['correct'] for x in accepted_labeled)/len(accepted_labeled) if accepted_labeled else None),
                'selective_labeled_count':len(accepted_labeled),
                'false_confident_wrong':sum(x['accepted'] and x['correct'] is False for x in items),
                'accepted_indeterminate':sum(x['accepted'] and x['correct'] is None for x in items),
                'supported_accepted':sum(x['accepted'] for x in target_supported),
                'supported_total':len(target_supported),
                'unsafe_rejected':sum(not x['accepted'] for x in target_abstain),
                'unsafe_total':len(target_abstain)}
    return {'aggregate':aggregate(values),'by_challenge':{k:aggregate(v) for k,v in cohorts.items()},
            'gate_reason_counts':dict(sorted(reasons.items()))}


def run(args:argparse.Namespace,*,encoder:Callable|None=None)->dict:
    if args.output.exists():raise FileExistsError('OUTPUT_EXISTS_REFUSE_OVERWRITE')
    if (args.groups<8 or args.groups%8 or args.ridge!=0.1 or args.train_atoms!=216
        or args.selection_groups!=16 or args.final_groups!=24 or args.seed!=261117
        or args.batch_size<1 or args.max_length!=192):
        raise ValueError('LEXICON_BLIND_FROZEN_CONFIG_CHANGED')
    base=binding.load_fixture(args.base_fixture)
    st=stress.load_fixture(args.stress_fixture,base)
    iface=interface.load_fixture(args.interface_fixture,base,st)
    rf=repair.load_fixture(args.repair_fixture,base,iface,st)
    fixture=load_fixture(args.fixture,base,st,iface,rf)
    prior=validate_prior(args.prior_report,args.base_fixture,args.stress_fixture,args.interface_fixture,args.repair_fixture)
    data=stress.derive_data(base,st,seed=args.seed,train_atoms=args.train_atoms,
                            selection_groups=args.selection_groups,final_groups=args.final_groups)
    rows=make_holdout(data,base,st,iface,rf,fixture,seed=args.seed,groups=args.groups)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,allow_download=args.allow_model_download,
                                         batch_size=args.batch_size,max_length=args.max_length)
    positions=getattr(encoder,'positions',None)
    if positions is not None and (positions.get('half')!=14 or positions.get('final')!=28):
        raise ValueError('LEXICON_BLIND_LAYER_POSITIONS_CHANGED')
    model=train_frozen(encoder,base,st,iface,rf,data,ridge=args.ridge)
    predictions=predict_frozen(rows,model,encoder,base,rf)
    metrics=score(rows,predictions,base,rf['reference_markers'])
    if getattr(encoder,'stats',None) and encoder.stats.get('texts_truncated',0):raise ValueError('LEXICON_BLIND_TRUNCATION')
    result={'schema':SCHEMA,'status':STATUS,'acceptance':{
          'status':'DIAGNOSTIC_ONLY_NOT_SEALABLE','no_production_mutation':True,'no_contract_seal':True,
          'no_model_weight_updates':True,'no_final_based_model_selection':True,'training_fixed':True},
          'sources':{'prior_report_sha256':sha256(args.prior_report.read_bytes()).hexdigest(),
            'base_fixture_sha256':sha256(args.base_fixture.read_bytes()).hexdigest(),
            'stress_fixture_sha256':sha256(args.stress_fixture.read_bytes()).hexdigest(),
            'interface_fixture_sha256':sha256(args.interface_fixture.read_bytes()).hexdigest(),
            'repair_fixture_sha256':sha256(args.repair_fixture.read_bytes()).hexdigest(),
            'blind_fixture_sha256':sha256(args.fixture.read_bytes()).hexdigest()},
          'design':{'model':smoke.MODEL_ID,'model_frozen':True,'view':'final:mean',
            'head':'targeted_grammar + unchanged targeted_typed surface',
            'train_examples':216*3,'unique_training_tuples':216,
            'groups':args.groups,'documents':len(rows),'text_is_explicitly_delimited':True,
            'no_gold_or_challenge_labels_in_decoder_or_gate':True,
            'gate_is_rejection_only_no_semantic_repair':True,
            'candidate_policy_set_before_evaluation':True,
            'frozen_ridge':args.ridge,'frozen_seed':args.seed,
            'tokens':getattr(encoder,'stats',None),
            'limitations':'Synthetic, two-clause authored grammar; no open-domain parsing or naturally occurring legal contracts'},
          'results':metrics,
          'previous_diagnostic_graph_exact':prior['new_holdout']['targeted_typed']['in_schema']['graph_exact'],
          'interpretation':'Raw graph accuracy measures generalized extraction on labeled text; abstention is safe refusal, NOT understanding. Compare selective correctness only alongside nonzero supported coverage. 98.5% previous holdout is not transferable to lexicon-blind forms.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',default='cuda')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prior-report',type=Path,default=PRIOR_REPORT)
    p.add_argument('--fixture',type=Path,default=FIXTURE)
    p.add_argument('--base-fixture',type=Path,default=binding.FIXTURE)
    p.add_argument('--stress-fixture',type=Path,default=stress.FIXTURE)
    p.add_argument('--interface-fixture',type=Path,default=interface.FIXTURE)
    p.add_argument('--repair-fixture',type=Path,default=repair.FIXTURE)
    p.add_argument('--train-atoms',type=int,default=216)
    p.add_argument('--selection-groups',type=int,default=16)
    p.add_argument('--final-groups',type=int,default=24)
    p.add_argument('--groups',type=int,default=24)
    p.add_argument('--seed',type=int,default=261117)
    p.add_argument('--ridge',type=float,default=0.1)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--max-length',type=int,default=192)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    report=run(args)
    a=report['results']['aggregate']
    print(smoke.canonical({'status':report['status'],'acceptance':report['acceptance']['status'],
      'heldout_documents':a['documents'],'labeled_documents':a['labeled_documents'],
      'raw_graph_exact':None if a['raw_graph_exact'] is None else round(a['raw_graph_exact'],6),
      'accepted_coverage':round(a['acceptance_coverage'],6),
      'selective_graph_exact':None if a['selective_graph_exact'] is None else round(a['selective_graph_exact'],6),
      'supported_accepted':a['supported_accepted'],'supported_total':a['supported_total'],
      'unsafe_rejected':a['unsafe_rejected'],'unsafe_total':a['unsafe_total'],
      'unsafe_accepted':a['unsafe_total']-a['unsafe_rejected'],
      'false_confident_wrong':a['false_confident_wrong'],
      'accepted_indeterminate':a['accepted_indeterminate'],
      'negation_probes':{k:{'challenged_polarity':round(report['results']['by_challenge'][k]['challenged_clause_polarity_accuracy'],6),
                             'raw_graph_exact':round(report['results']['by_challenge'][k]['raw_graph_exact'],6),
                             'accepted_coverage':round(report['results']['by_challenge'][k]['acceptance_coverage'],6)}
                          for k in ('known_deny','scoped_not','unseen_need_not','unseen_exempt',
                                    'double_negation','quoted_distractor','unless_scope')},
      'report':str(args.output)}))
    return 0

if __name__=='__main__':raise SystemExit(main())
