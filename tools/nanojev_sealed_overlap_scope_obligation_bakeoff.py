#!/usr/bin/env python3
"""Frozen Qwen obligation-scope repair: independent refusal and scope-parse arms.

Prior lexicon-blind result: 30 confidently wrong + 24 indeterminate accepted.
This runner does not tune its parser or classifier to the evaluated labels.
Compare the untouched previous refusal gate, an independent semantic-status
refusal gate, and the same gate with a narrowly specified polarity AST overlay.

No new Qwen training, no vocabulary/production mutation, no contract sealing.
All samples are authored and explicitly separated by ' || '; this is NOT an
open-domain contract parser. Rejection is safety/coverage, NOT comprehension.

Run from repo root:
  & $py -m tools.nanojev_sealed_overlap_scope_obligation_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-scope-obligation-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
import re
from string import Formatter
from typing import Callable

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind

SCHEMA='nanojev.sealed-overlap.scope-obligation-bakeoff.v1'
STATUS='COMPLETE_SCOPE_OBLIGATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION'
FIXTURE=Path(__file__).resolve().parent/'fixtures'/'nanojev_overlap_scope_obligation_v1.json'
PRIOR_REPORT=Path('runtime/reports/sealed-overlap-lexicon-blind-0001.json')
TRUTHS={'positive','negative','positive_scope_not','negative_scope_not','indeterminate'}
POLICIES={'supported','abstain'}


def load_fixture(path:Path,base:dict,st:dict,iface:dict,rf:dict,prior_fixture:dict)->dict:
    f=json.loads(path.read_text(encoding='utf8'))
    if f.get('schema')!='nanojev.sealed-overlap.scope-obligation-fixture.v1' or f.get('document_delimiter')!=' || ':
        raise ValueError('SCOPE_FIXTURE_SCHEMA_INVALID')
    challenges=f.get('challenges')
    if not isinstance(challenges,list) or len(challenges)!=25 or len({x.get('kind') for x in challenges})!=25:
        raise ValueError('SCOPE_CHALLENGES_INVALID')
    forbidden=set(sum((base['templates'][k] for k in ('train','selection','final')),[]))
    forbidden.update(st['selection_templates']+st['final_templates'])
    forbidden.update(iface['augmentation_templates']+iface['fresh_templates'])
    forbidden.update(rf['targeted_training_templates']+rf['new_holdout_templates'])
    forbidden.update(x['template'] for x in prior_fixture['challenges'])
    tpl_seen=set()
    for c in challenges:
        tpl=c.get('template')
        if c.get('policy') not in POLICIES or c.get('truth') not in TRUTHS or not isinstance(tpl,str):
            raise ValueError('SCOPE_CHALLENGE_EXPECTATION_INVALID')
        if tpl in forbidden or tpl in tpl_seen or ' || ' in tpl:
            raise ValueError('SCOPE_TEMPLATE_REUSE_OR_LEAK')
        tpl_seen.add(tpl)
        fields={name for _,name,_,_ in Formatter().parse(tpl) if name is not None}
        if not ({'recipient','asset','condition','consequence'}.issubset(fields) and
                (c['policy']!='supported' or 'actor' in fields)):
            raise ValueError('SCOPE_TEMPLATE_FIELDS_INVALID')
        if fields-{'actor','recipient','asset','condition','consequence','other_actor','other_condition'}:
            raise ValueError('SCOPE_TEMPLATE_FIELDS_INVALID')
        if c['policy']=='supported' and c['truth']=='indeterminate':
            raise ValueError('SCOPE_UNKNOWN_SUPPORTED')
    return f


def validate_prior(path:Path,base_path:Path,st_path:Path,iface_path:Path,rf_path:Path,blind_path:Path)->dict:
    p=json.loads(path.read_text(encoding='utf8'))
    d=p.get('design',{}); s=p.get('sources',{});a=p.get('results',{}).get('aggregate',{})
    expected={
      'base_fixture_sha256':base_path,'stress_fixture_sha256':st_path,
      'interface_fixture_sha256':iface_path,'repair_fixture_sha256':rf_path,
      'blind_fixture_sha256':blind_path,
    }
    if (p.get('schema')!=blind.SCHEMA or p.get('status')!=blind.STATUS or
        p.get('acceptance',{}).get('no_model_weight_updates') is not True or
        p.get('acceptance',{}).get('no_final_based_model_selection') is not True or
        d.get('view')!='final:mean' or d.get('train_examples')!=648 or
        a.get('documents')!=432 or a.get('false_confident_wrong')!=30 or
        a.get('accepted_indeterminate')!=24 or
        any(s.get(k)!=sha256(f.read_bytes()).hexdigest() for k,f in expected.items())):
        raise ValueError('SCOPE_PRIOR_REPORT_INVALID')
    return p


def make_holdout(data:dict,base:dict,st:dict,iface:dict,rf:dict,old:dict,new:dict,*,seed:int,groups:int)->list[dict]:
    if groups<8 or groups%8:raise ValueError('SCOPE_GROUPS_INVALID')
    # Exclude previous selection/final, previous repair, and already-inspected blind pairs.
    old_blind=blind.make_holdout(data,base,st,iface,rf,old,seed=seed,groups=24)
    used={tuple(sorted(tuple(x) for x in row['atoms'])) for part in ('selection','final')
          for row in data[part] if row['kind']=='base'}
    used.update(tuple(sorted(tuple(x) for x in r['atoms'])) for r in repair.make_fresh(
        data,base,iface,st,rf,seed=seed,groups=24) if r['kind']=='base')
    used.update(tuple(sorted(tuple(x) for x in r['truth_atoms']))
                for r in old_blind if r['kind']=='known_affirm')
    training={tuple(r['atom']) for r in data['train']}
    unseen=sorted(set(itertools.product(*binding.values(base)))-training)
    rng=random.Random(seed+290181)
    rows=[]
    for g in range(groups):
        pool=sorted(training) if g%2==0 else unseen
        for _ in range(30000):
            a,b=rng.sample(pool,2)
            pair=tuple(sorted((a,b)))
            if pair not in used and all(a[j]!=b[j] for j in range(5)):
                used.add(pair);break
        else:raise ValueError('SCOPE_PAIR_EXHAUSTED')
        fields=dict(zip(binding.FIELDS,a))
        fields['other_actor']=next(x for x in base['actors'] if x!=a[0])
        fields['other_condition']=next(x for x in base['conditions'] if x!=a[3])
        # Accompanying clause remains canonical to isolate challenged semantics.
        second=repair.render(b,rf,'new_holdout_templates',0)
        for spec in new['challenges']:
            first=spec['template'].format(**fields)
            atom=list(a);truth=spec['truth']
            if truth in ('positive','positive_scope_not'):atom[5]=1
            if truth in ('negative','negative_scope_not'):atom[5]=-1
            if truth.endswith('_scope_not'):atom[3]='(not '+a[3]+')'
            gold=None if truth=='indeterminate' else [tuple(atom),b]
            rows.append({'id':f'scope:{g:03d}:{spec["kind"]}',
                         'kind':spec['kind'],'group_id':g,
                         'truth_atoms':gold,'policy':spec['policy'],
                         'text':binding.document([first,second])})
    if len({r['text'] for r in rows})!=len(rows):raise ValueError('SCOPE_DUPLICATE_HOLDOUT')
    if {r['text'] for r in rows}&{r['text'] for r in old_blind}:
        raise ValueError('SCOPE_PRIOR_HOLDOUT_TEXT_OVERLAP')
    return rows


# Semantic status classification happens before the learned clause extractor.
# A denial of an *assertion* is not reinterpreted as an operative negative duty.
# Reporting, proposals, uncertainty, counterfactuals and quoted clauses do
# not establish a contract. All of these yield ABSTAIN, never a guessed sign.
_NONOPERATIVE=(
  (r'\b(?:rejects?|rejected|disputes?|disputed|refutes?|refuted)\b.{0,90}\b(?:assertion|allegation|claim|contention)\b','DENIED_ASSERTION_NOT_DUTY'),
  (r'\b(?:denies?|denied|dismissed)\b.{0,90}\b(?:petition|claim|assertion)\b','DENIED_ASSERTION_NOT_DUTY'),
  (r'\b(?:draft|proposal|proposed|preliminary|unadopted|not adopted|not enacted|never adopted|was not adopted)\b','NONOPERATIVE_PROPOSAL_OR_DRAFT'),
  (r'\b(?:says?|said|reported|reports?|witness|memorandum|letter|quotation|quoted|obsolete|outdated|note)\b','QUOTED_OR_REPORTED_NOT_OPERATIVE'),
  (r'\b(?:no evidence|no proof|uncertain|not known|unknown|whether|unclear|possibly|perhaps)\b','EPISTEMIC_UNCERTAINTY'),
  (r'\b(?:suppose|hypothetical|counterfactual|had the contract|would exist|would impose|ought to)\b','HYPOTHETICAL_NOT_OPERATIVE'),
  (r'\bnot (?:true|the case) that a duty exists\b','META_DENIAL_NOT_NEGATIVE_DUTY'),
)
_DOUBLE_NEGATIONS=(
  r'\bit is not true that no duty exists\b',
  r'\bit is not the case that no duty exists\b',
  r'\bit is false that no duty exists\b',
)


def clause_scope_polarity(text:str)->tuple[int|None,tuple[str,...]]:
    """Supported evidence only: signs on duties distinct from condition NOT.

    Reject quotations and attribution before recognizing embedded duty markers.
    Never infer that a denial of an allegation imposes an opposite duty.
    """
    s=text.lower();reasons=[]
    if any(q in text for q in ("'",'"','“','”','‘','’')):
        reasons.append('QUOTED_SOURCE_NOT_OPERATIVE')
    for pattern,reason in _NONOPERATIVE:
        if re.search(pattern,s,re.I|re.S):reasons.append(reason)
    if re.search(r'\b(?:unless|except|only if|notwithstanding|subject to)\b',s):
        reasons.append('UNSUPPORTED_SCOPE_OPERATOR')
    if reasons:return None,tuple(sorted(set(reasons)))
    # A duty marker cannot license unrelated, unscoped negation. Remove only
    # explicitly supported operators and the parenthesized condition NOT.
    # All remaining negation operators are an abstention, even if a positive
    # duty marker also appears elsewhere in the text.
    stripped=s
    for p in (*_DOUBLE_NEGATIONS,
              r'\bneed not (?:transfer|deliver|provide)\b',
              r'\bis not required to\b',r'\bno duty exists\b',
              r'\bhas no obligation to\b',r'\bdoes not (?:impose|obligate)\b',
              r'\bdoes not impose a duty of delivery\b',
              r'\bno obligation\b',r'\(not [^)]+\)'):
        stripped=re.sub(p,' ',stripped)
    if re.search(r'\b(?:not|no|never|without|neither|none|cannot|false|untrue|nor)\b',stripped):
        return None,('NEGATION_SCOPE_UNKNOWN',)
    double=[p for p in _DOUBLE_NEGATIONS if re.search(p,s)]
    if double:
        # Exactly one scope-carrying statement; additional contradictory
        # independent duty markers are not resolved by counting words.
        stripped=s
        for p in _DOUBLE_NEGATIONS:stripped=re.sub(p,' ',stripped)
        if repair.extract_polarity(stripped) is not None:
            return None,('MULTIPLE_DUTY_ASSERTIONS',)
        return 1,()
    if re.search(r'\bneed not (?:transfer|deliver|provide)\b',s):
        rest=re.sub(r'\bneed not (?:transfer|deliver|provide)\b',' ',s)
        if repair.extract_polarity(rest) is not None:
            return None,('MULTIPLE_DUTY_ASSERTIONS',)
        return -1,()
    sign=repair.extract_polarity(text)
    return (sign,()) if sign is not None else (None,('DUTY_SCOPE_UNKNOWN_OR_CONFLICTING',))


def scope_gate(document:str,base:dict,refs:list[str])->tuple[bool,tuple[str,...],tuple[int|None,...]]:
    """Independent text-only status gate. No gold, template IDs or Qwen labels."""
    reasons=set();signs=[]
    old_ok,old_reasons=blind.fail_closed_gate(document,base,refs)
    for clause in binding.split_document(document):
        sign,status=clause_scope_polarity(clause)
        signs.append(sign)
        reasons.update(status)
    # Reuse all text-grounded uniqueness protections. Only permit prior gate
    # refusal codes explained *solely* by a now-supported duty scope marker.
    ignorable={'DUTY_POLARITY_UNSUPPORTED_OR_CONFLICTING','UNSUPPORTED_SCOPE_OR_META_NEGATION'}
    reasons.update(set(old_reasons)-ignorable)
    if not old_ok and set(old_reasons)&ignorable and any(s is None for s in signs):
        reasons.add('UNSUPPORTED_DUTY_SCOPE')
    # Old gate never rejected two known problematic meta frames; the new gate
    # does so independently, without reading challenge labels or gold.
    return not reasons,tuple(sorted(reasons)),tuple(signs)


def overlay_supported_signs(pred:list[list[tuple]],documents:list[str],base:dict,refs:list[str])->list[list[tuple]]:
    """Only a parsed and accepted operative scope may override the frozen head.

    This is a separate semantic intervention from the rejection-only gate.
    No overwriting of roles, asset, consequence or condition from gold labels.
    """
    repaired=[]
    for row,doc in zip(pred,documents):
        ok,_,signs=scope_gate(doc,base,refs)
        if not ok:
            repaired.append(row)
            continue
        if len(signs)!=len(row):raise ValueError('SCOPE_SIGN_COUNT_MISMATCH')
        repaired.append([tuple([*atom[:-1],sign]) for atom,sign in zip(row,signs)])
    return repaired


def score(rows:list[dict],preds:list[list[tuple]],base:dict,refs:list[str],*,arm:str)->dict:
    if len(rows)!=len(preds):raise ValueError('SCOPE_ROWS_PREDICTIONS_MISMATCH')
    by_kind=defaultdict(list);reason_counts=Counter();observed=[]
    for row,pred in zip(rows,preds):
        if arm=='untouched_gate':accepted,reasons=blind.fail_closed_gate(row['text'],base,refs)
        else:accepted,reasons,_=scope_gate(row['text'],base,refs)
        reason_counts.update(reasons)
        gold=row['truth_atoms']
        correct=None if gold is None else (repair.graph(gold,base)==repair.graph(pred,base))
        datum={'accepted':accepted,'correct':correct,'should_accept':row['policy']=='supported'}
        by_kind[row['kind']].append(datum);observed.append(datum)
    def aggregate(v):
        yes=[x for x in v if x['accepted']]
        labeled=[x for x in v if x['correct'] is not None]
        accepted_labeled=[x for x in yes if x['correct'] is not None]
        supported=[x for x in v if x['should_accept']]
        unsafe=[x for x in v if not x['should_accept']]
        return {
           'documents':len(v),'labeled_documents':len(labeled),
           'raw_graph_exact':sum(x['correct'] for x in labeled)/len(labeled) if labeled else None,
           'coverage':len(yes)/len(v) if v else None,
           'accepted_labeled_count':len(accepted_labeled),
           'selective_graph_exact':sum(x['correct'] for x in accepted_labeled)/len(accepted_labeled) if accepted_labeled else None,
           'supported_accepted':sum(x['accepted'] for x in supported),'supported_total':len(supported),
           'unsafe_accepted':sum(x['accepted'] for x in unsafe),'unsafe_total':len(unsafe),
           'unsafe_rejected':sum(not x['accepted'] for x in unsafe),
           'false_confident_wrong':sum(x['accepted'] and x['correct'] is False for x in v),
           'accepted_indeterminate':sum(x['accepted'] and x['correct'] is None for x in v),
        }
    return {'aggregate':aggregate(observed),
            'by_challenge':{k:aggregate(v) for k,v in by_kind.items()},
            'reason_counts':dict(sorted(reason_counts.items()))}


def historic_gate_audit(data,base,st,iface,rf,old,*,seed:int)->dict:
    """Previously inspected rows used ONLY as a regression, never model selection."""
    rows=blind.make_holdout(data,base,st,iface,rf,old,seed=seed,groups=24)
    assert len(rows)==432
    baseline=Counter();repaired=Counter();diff=defaultdict(lambda:Counter())
    for row in rows:
        o,_=blind.fail_closed_gate(row['text'],base,rf['reference_markers'])
        n,_,_=scope_gate(row['text'],base,rf['reference_markers'])
        baseline['accepted']+=o;repaired['accepted']+=n
        diff[row['kind']]['baseline_accepted']+=o
        diff[row['kind']]['repaired_accepted']+=n
    return {'rows':len(rows),'untouched_gate_accepted':baseline['accepted'],
            'repaired_gate_accepted':repaired['accepted'],
            'inspected_canaries':{k:dict(diff[k]) for k in ('denied_assertion','reported_draft_only','known_affirm','known_deny','scoped_not')},
            'not_used_for_training_selection_or_final_scoring':True}


def run(args:argparse.Namespace,*,encoder:Callable|None=None)->dict:
    if args.output.exists():raise FileExistsError('OUTPUT_EXISTS_REFUSE_OVERWRITE')
    if (args.groups<8 or args.groups%8 or args.seed!=261117 or args.ridge!=0.1 or
        args.train_atoms!=216 or args.selection_groups!=16 or args.final_groups!=24 or
        args.batch_size<1 or args.max_length!=192):
        raise ValueError('SCOPE_FROZEN_CONFIG_CHANGED')
    base=binding.load_fixture(args.base_fixture)
    st=stress.load_fixture(args.stress_fixture,base)
    iface=interface.load_fixture(args.interface_fixture,base,st)
    rf=repair.load_fixture(args.repair_fixture,base,iface,st)
    old=blind.load_fixture(args.blind_fixture,base,st,iface,rf)
    f=load_fixture(args.fixture,base,st,iface,rf,old)
    prior=validate_prior(args.prior_report,args.base_fixture,args.stress_fixture,
                         args.interface_fixture,args.repair_fixture,args.blind_fixture)
    data=stress.derive_data(base,st,seed=args.seed,train_atoms=args.train_atoms,
                            selection_groups=args.selection_groups,final_groups=args.final_groups)
    rows=make_holdout(data,base,st,iface,rf,old,f,seed=args.seed,groups=args.groups)
    audit=historic_gate_audit(data,base,st,iface,rf,old,seed=args.seed)
    if encoder is None:
        encoder=layers.FrozenLayerEncoder(smoke.MODEL_ID,args.device,allow_download=args.allow_model_download,
                                         batch_size=args.batch_size,max_length=args.max_length)
    positions=getattr(encoder,'positions',None)
    if positions and (positions.get('half')!=14 or positions.get('final')!=28):
        raise ValueError('SCOPE_LAYER_POSITIONS_CHANGED')
    model=blind.train_frozen(encoder,base,st,iface,rf,data,ridge=args.ridge)
    preds=blind.predict_frozen(rows,model,encoder,base,rf)
    docs=[r['text'] for r in rows]
    overlaid=overlay_supported_signs(preds,docs,base,rf['reference_markers'])
    arms={
      'untouched_gate':score(rows,preds,base,rf['reference_markers'],arm='untouched_gate'),
      'scope_rejection_only':score(rows,preds,base,rf['reference_markers'],arm='scope_rejection_only'),
      'scope_with_polarity_grounding':score(rows,overlaid,base,rf['reference_markers'],arm='scope_with_polarity_grounding'),
    }
    if getattr(encoder,'stats',None) and encoder.stats.get('texts_truncated',0):
        raise ValueError('SCOPE_TEXT_TRUNCATION')
    report={
      'schema':SCHEMA,'status':STATUS,
      'acceptance':{'status':'DIAGNOSTIC_ONLY_NOT_SEALABLE',
                    'model_frozen':True,'no_model_weight_updates':True,
                    'no_production_mutation':True,'no_contract_seal':True,
                    'no_final_based_model_selection':True},
      'sources':{
          **{k:sha256(v.read_bytes()).hexdigest() for k,v in (
              ('base_fixture_sha256',args.base_fixture),
              ('stress_fixture_sha256',args.stress_fixture),
              ('interface_fixture_sha256',args.interface_fixture),
              ('repair_fixture_sha256',args.repair_fixture),
              ('blind_fixture_sha256',args.blind_fixture),
              ('scope_fixture_sha256',args.fixture))},
          'prior_report_sha256':sha256(args.prior_report.read_bytes()).hexdigest()},
      'design':{'model':smoke.MODEL_ID,'view':'final:mean','frozen_training_examples':648,
          'unique_training_tuples':216,'groups':args.groups,
          'fresh_documents':len(rows),'fresh_challenges':len(f['challenges']),
          'explicit_clause_delimiter':' || ',
          'arms':['untouched_gate','scope_rejection_only','scope_with_polarity_grounding'],
          'status_scope_parser_is_text_grounding_not_qwen_generalization':True,
          'all_policy_labels_and_gold_excluded_from_inference':True,
          'no_final_based_tuning':True,
          'statistical_limitation':'Authored restricted synthetic grammar and correlated variants; hard safety limits remain unproven',
          'tokens':getattr(encoder,'stats',None)},
      'historical_from_inspected_report':prior['results']['aggregate'],
      'historical_gate_audit':audit,
      'fresh':arms,
      'interpretation':'Confident mistakes and accepted indeterminate documents are the primary hazards. Rejection does not equal semantic understanding. Parser recognition of narrow forms is not open-language generalization.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as fp:
        json.dump(report,fp,indent=2,ensure_ascii=False,allow_nan=False);fp.write('\n')
    return report


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',default='cuda')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prior-report',type=Path,default=PRIOR_REPORT)
    p.add_argument('--fixture',type=Path,default=FIXTURE)
    p.add_argument('--blind-fixture',type=Path,default=blind.FIXTURE)
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


def main(argv=None)->int:
    args=parser().parse_args(argv)
    report=run(args)
    ag={k:report['fresh'][k]['aggregate'] for k in report['design']['arms']}
    print(smoke.canonical({'status':report['status'],'acceptance':report['acceptance']['status'],
          'fresh_documents':report['design']['fresh_documents'],
          'historical_canaries':report['historical_gate_audit']['inspected_canaries'],
          'fresh':{k:{'raw_graph_exact':None if v['raw_graph_exact'] is None else round(v['raw_graph_exact'],6),
                       'coverage':round(v['coverage'],6),'selective_graph_exact':None if v['selective_graph_exact'] is None else round(v['selective_graph_exact'],6),
                       'supported_accepted':v['supported_accepted'],'unsafe_accepted':v['unsafe_accepted'],
                       'false_confident_wrong':v['false_confident_wrong'],
                       'accepted_indeterminate':v['accepted_indeterminate']} for k,v in ag.items()},
          'report':str(args.output)}))
    return 0

if __name__=='__main__':raise SystemExit(main())
