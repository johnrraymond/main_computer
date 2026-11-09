#!/usr/bin/env python3
"""V41: prospective Qwen semantic transfer across disjoint language families.

The failed V40 test is CONSUMED and is never read here. Training, development,
and prospective held-out payoff and probability description families are disjoint.
Only a numeric scanner and frozen-Qwen activations enter the prediction path.
A separate TRAIN-fitted V34 numerical head makes the final action decision.
No promotion and no tactical-v6 mutation are permitted by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import tempfile
import os

import numpy as np

from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34
from tools import nanojev_gamer_v1_v40_actor_grounding as v40

SCHEMA = 'nanojev.gamer.v1.disjoint-surface-transfer.v41'
HEAD_SCHEMA = 'nanojev.gamer.v1.disjoint-surface-head.v41'
REPORT_SCHEMA = 'nanojev.gamer.v1.prospective-semantic-audit.v41'
SPLITS = {
    'fit': {'tables':tuple(range(8)), 'beliefs':tuple(range(8)), 'pairs_per_table':30},
    'dev': {'tables':(8,9,10), 'beliefs':(8,9,10), 'pairs_per_table':20},
    'test':{'tables':(11,12,13,14,15), 'beliefs':(11,12,13,14,15),'pairs_per_table':20},
}
DEV = {'structural_exact':108,'decision_exact':108,'belief_exact':108,'pairs_exact':54}
TEST = {'structural_exact':180,'decision_exact':180,'belief_exact':180,'pairs_exact':90}

# Styles 0..7 are previously disclosed V40 source styles (including its
# FAILED, already-opened test styles). They are now TRAIN-family inputs only.
# Styles 8..10 are new DEV families. Styles 11..15 are prospective sealed
# families and must never be instantiated until DEV passes.
# This table controls SUPERVISED DATA GENERATION ONLY. The predictor never
# receives a style number or consults this table.
_NEW_TABLES = (
    'For our {ours}, provided the other picks {theirs}, our score is {value}.',
    'If their move is {theirs} and ours is {ours}, the value we receive is {value}.',
    'A result of {value} goes to us in the event that their move is {theirs} and our move is {ours}.',
    'We receive {value} for the event where their action is {theirs} and our action is {ours}.',
    'Our return totals {value} whenever we select {ours} while they select {theirs}.',
    '{value} points accrue to us if we play {ours} against their {theirs}.',
    'Assume they play {theirs}; if we play {ours} then our net return is {value}.',
    'When their selected move is {theirs}, the benefit to us is {value} if our move is {ours}.',
)
_NEW_BELIEFS = (
    'In a fraction {q} of cases, the rival takes {side}; otherwise they take the other move.',
    'The rival takes {side} at rate {q}, with the remaining probability assigned to its alternative.',
    'For the opponent taking {side}, we place likelihood {q}; the complement belongs to the other move.',
    'The chance assigned to a rival move of {side} is {q}; the other move has the complementary chance.',
    'We estimate probability {q} that the other selects {side}, and the remainder for its alternative.',
    'Our belief weights the rival choosing {side} by {q}, leaving the rest for their other move.',
    'Of the two rival responses, {side} occurs with probability {q}; the remainder is the alternative.',
    'When predicting the other agent, we assign {q} to {side} and the balance to its other choice.',
)
assert len(_NEW_TABLES)==len(_NEW_BELIEFS)==8

# Explicit definitions resolve the synonym ambiguity found in V35--V39;
# these are part of the task text, not a semantic dictionary used by inference.
DEFINITION = (' Here cooperation, sharing, deescalation and Yield all denote C; '
              'defection, claiming, challenge and Contest all denote D.')


def require(ok, reason):
    if not ok:
        raise ValueError('GAMER_V41_' + reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path,obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    require(not path.exists(), 'REFUSE_OVERWRITE_'+path.name)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,prefix='.v41-',encoding='utf-8',delete=False) as fh:
        json.dump(obj,fh,sort_keys=True,indent=2,ensure_ascii=False)
        fh.write('\n')
        temp=Path(fh.name)
    try:
        os.link(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def _table(style,role,value):
    if style<8:
        return v40._surface(role,value,style)
    ours,theirs=role
    return _NEW_TABLES[style-8].format(ours=ours,theirs=theirs,value=value)


def _belief(style,side):
    if style<8:
        return v40._belief(side,Fraction(3,4),style)
    return _NEW_BELIEFS[style-8].format(q='3/4',side=side)


def make_deck(split,seed=41):
    """Fit/DEV only until admitted; never called for test in training path."""
    require(split in SPLITS,'INVALID_SPLIT')
    spec=SPLITS[split]
    rows=[]
    for group_index,style in enumerate(spec['tables']):
        belief_styles=spec['beliefs']
        for pair_index in range(spec['pairs_per_table']):
            rng=random.Random(f'v41-{seed}-{split}-{style}-{pair_index}')
            intervention=('belief','ours','theirs','actor_exchange')[pair_index%4]
            for attempt in range(20000):
                values=dict(zip(v40.ROLE_CHOICES,rng.sample(range(-12,13),4)))
                if pair_index%3==0:
                    values['CC']=rng.randint(-8,8)
                    values['CD']=rng.randint(-8,8)
                    delta=rng.randint(3,10)*(1 if rng.randrange(2) else -1)
                    values['DC']=values['CC']+delta
                    values['DD']=values['CD']-delta
                order=list(v40.ROLE_CHOICES)
                rng.shuffle(order)
                modified=[v40.transform_role(r,intervention) for r in order]
                orders=(order,order) if intervention=='belief' else (order,modified)
                sides=('C','D') if intervention=='belief' else ('C','C')
                literal_values=[values[role] for role in order]
                truths=[]
                for roles,side in zip(orders,sides):
                    by_role=dict(zip(roles,literal_values))
                    p=Fraction(3,4) if side=='C' else Fraction(1,4)
                    margin=p*(by_role['DC']-by_role['CC'])+(1-p)*(by_role['DD']-by_role['CD'])
                    truths.append(margin)
                if all(t!=0 for t in truths) and (pair_index%3 != 0 or (truths[0]>0)!=(truths[1]>0)):
                    break
            else:
                raise ValueError('GAMER_V41_CANNOT_CONSTRUCT_CONTRAST')
            belief_style=belief_styles[(group_index+pair_index)%len(belief_styles)]
            context=v40._CONTEXTS[(group_index+pair_index)%len(v40._CONTEXTS)]
            for choice,(roles,side) in enumerate(zip(orders,sides)):
                clauses=' '.join(_table(style,role,value)
                                 for role,value in zip(roles,literal_values))
                prompt=f'{context}{DEFINITION} Our points by outcome: {clauses} {_belief(belief_style,side)} Which choice maximizes expected points: C or D?'
                rows.append({'id':f'{split}.s{style:02}.p{pair_index:02}.{choice}',
                             'pair':f'{split}.s{style:02}.p{pair_index:02}',
                             'variant':style, 'prompt':prompt,
                             'roles':[v40.ROLE_CHOICES.index(r) for r in roles],
                             'q_side':0 if side=='C' else 1,'label':int(truths[choice]>0),
                             'intervention':intervention,'table_style':style,
                             'belief_style':belief_style})
    n=2*len(spec['tables'])*spec['pairs_per_table']
    require(len(rows)==n and len({r['prompt'] for r in rows})==n,'DECK_INTEGRITY')
    for a,b in zip(rows[::2],rows[1::2]):
        require(a['pair']==b['pair'] and a['intervention']==b['intervention'],'PAIR_INTEGRITY')
        ai,aq=v40.transcribe(a['prompt'])
        bi,bq=v40.transcribe(b['prompt'])
        require([s['value'] for s in ai]==[s['value'] for s in bi] and aq['value']==bq['value'],
                'NUMERIC_PAIR_CHANGED')
        if a['intervention']=='belief':
            require(a['roles']==b['roles'] and a['q_side']!=b['q_side'],'BAD_BELIEF_PAIR')
        else:
            require(a['q_side']==b['q_side'] and a['roles']!=b['roles'],'BAD_ROLE_PAIR')
        if int(a['id'].split('.p')[1].split('.')[0])%3==0:
            require(a['label']!=b['label'],'FORCED_REVERSAL_MISSING')
    return rows


def gate(stats,cutoff):
    return all(stats.get(key,0)>=minimum for key,minimum in cutoff.items()) and \
           stats['decision_exact'] > stats['numeric_only_accuracy_ceiling']


def _scores(rows,model,features,beliefs):
    roles,sides=v40.predict_slots(model,features,beliefs)
    # Importantly: actions are predicted by the frozen TRAIN-fitted V34 head.
    # No true role, true q, answer label or table template is supplied.
    return roles,sides


def checkpoint(root):
    return Path(root)/'semantic_head.pt'


def train(root,*,v34_root,device='cuda',project='.',seed=41,epochs=120,batch_size=4):
    import torch
    root=Path(root)
    require(not root.exists(),'OUTPUT_EXISTS')
    fit=make_deck('fit',seed)
    dev=make_deck('dev',seed)
    require(len(fit)==480 and len(dev)==120,'TRAIN_DEV_SIZE')
    require({r['table_style'] for r in fit}.isdisjoint({r['table_style'] for r in dev}) and
            {r['belief_style'] for r in fit}.isdisjoint({r['belief_style'] for r in dev}),
            'SURFACE_FAMILY_LEAK')
    lineage=v33.activation(project)
    _,numeric=v40._load_v34(v34_root,project)
    root.mkdir(parents=True,exist_ok=False)
    write_new(root/'train_dev_provenance.json',{
        'fit_sha256':digest(fit),'dev_sha256':digest(dev),
        'fit_tables':list(SPLITS['fit']['tables']), 'dev_tables':list(SPLITS['dev']['tables']),
        'fit_beliefs':list(SPLITS['fit']['beliefs']), 'dev_beliefs':list(SPLITS['dev']['beliefs']),
        'source':'precommitted-disjoint-style-families',
        'v40_failed_holdout_read':False})
    t,tok,model=v33.model_context(lineage['model_id'],device)
    try:
        x,q=v40.capture(model,tok,t,[r['prompt'] for r in fit],device=device,batch_size=batch_size,tag='v41-fit')
        dx,dq=v40.capture(model,tok,t,[r['prompt'] for r in dev],device=device,batch_size=batch_size,tag='v41-dev')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    state,history,best,preds=v40.fit_head(x,q,fit,dx,dq,dev,numeric,epochs=epochs,seed=seed)
    stat=v40.scores(dev,*preds)
    accepted=gate(stat,DEV)
    manifest={
        'schema':SCHEMA,'head_schema':HEAD_SCHEMA,
        'status':'TRAIN_NO_TEST_NO_PROMOTION','model_id':lineage['model_id'],
        'lineage':lineage,'seed':seed,'epochs':epochs,'feature_width':int(x.shape[-1]),
        'style_partition':{k:{'tables':list(v['tables']),'beliefs':list(v['beliefs'])}
                           for k,v in SPLITS.items()},
        'fit_deck_sha256':digest(fit),'dev_deck_sha256':digest(dev),
        'provenance_sha256':sha(root/'train_dev_provenance.json'),
        'numeric_head_sha256':sha(v34.checkpoint(v34_root)),
        'numeric_manifest_sha256':sha(Path(v34_root)/'train_manifest.json'),
        'v34_root':str(Path(v34_root).resolve()),
        'dev_pass':accepted,'dev_scores':stat,
        'role_head':'v40-actor-ownership-attention',
        'semantic_parser_inference':False,'oracle_inference':False,
        'production_modified':False,'v40_failed_holdout_read':False}
    torch.save({'schema':HEAD_SCHEMA,'manifest_sha256':digest(manifest),'state':state},checkpoint(root))
    write_new(root/'train_manifest.json',manifest)
    write_new(root/'train_history.json',history)
    result={'status':'PASS_OOD_DEV_TEST_STILL_SEALED' if accepted else 'FAIL_OOD_DEV_TEST_STILL_SEALED',
            'dev':stat,'dev_cutoffs':DEV,'checkpoint_sha256':sha(checkpoint(root)),
            'test_evaluated':False,'production_modified':False}
    write_new(root/'train_result.json',result)
    return result


def load(root,project='.'):
    import torch
    root=Path(root)
    manifest=read(root/'train_manifest.json')
    result=read(root/'train_result.json')
    require(manifest.get('schema')==SCHEMA and manifest.get('head_schema')==HEAD_SCHEMA and
            manifest.get('status')=='TRAIN_NO_TEST_NO_PROMOTION' and
            manifest.get('style_partition')=={k:{'tables':list(v['tables']),'beliefs':list(v['beliefs'])}
                                               for k,v in SPLITS.items()} and
            manifest.get('semantic_parser_inference') is False and
            manifest.get('oracle_inference') is False and
            manifest.get('v40_failed_holdout_read') is False,'BAD_MANIFEST')
    require(sha(checkpoint(root))==result.get('checkpoint_sha256') and
            sha(root/'train_dev_provenance.json')==manifest['provenance_sha256'] and
            sha(v34.checkpoint(manifest['v34_root']))==manifest['numeric_head_sha256'] and
            sha(Path(manifest['v34_root'])/'train_manifest.json')==manifest['numeric_manifest_sha256'],
            'LINEAGE_CHANGED')
    v33.verify_activation(project,manifest['lineage'])
    data=torch.load(checkpoint(root),weights_only=True,map_location='cpu')
    require(data.get('schema')==HEAD_SCHEMA and data.get('manifest_sha256')==digest(manifest),
            'CHECKPOINT_MANIFEST_MISMATCH')
    net=v40.head(torch,manifest['feature_width'])
    net.load_state_dict(data['state'],strict=True)
    net.eval()
    return manifest,net


def audit(root,*,device='cuda',project='.',batch_size=4):
    import torch
    root=Path(root)
    require(not (root/'holdout_consumed.json').exists() and
            not (root/'holdout_acceptance.json').exists(),'TEST_ALREADY_CONSUMED')
    manifest,net=load(root,project)
    require(manifest['dev_pass'] is True,'DEV_GATE_FAILED_TEST_STILL_SEALED')
    # Burn this audit before generating any test case or loading Qwen.
    write_new(root/'holdout_consumed.json',{'status':'CONSUMED_NO_RETRY',
            'checkpoint_sha256':sha(checkpoint(root)),'manifest_sha256':sha(root/'train_manifest.json')})
    rows=make_deck('test',manifest['seed'])
    write_new(root/'holdout_deck.json',rows)
    lineage=manifest['lineage']
    t,tok,model=v33.model_context(lineage['model_id'],device)
    try:
        x,q=v40.capture(model,tok,t,[r['prompt'] for r in rows],device=device,batch_size=batch_size,tag='v41-sealed')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    _,numeric=v40._load_v34(manifest['v34_root'],project)
    roles,sides=_scores(rows,net,x,q)
    decisions=v40.run_decisions(numeric,rows,roles,sides)
    stats=v40.scores(rows,roles,sides,decisions)
    per_variant={str(s):{'total':sum(r['variant']==s for r in rows),
                         'decision_exact':sum(r['variant']==s and d==r['label']
                                              for r,d in zip(rows,decisions)),
                         'structural_exact':sum(r['variant']==s and tuple(p)==tuple(r['roles'])
                                                and b==r['q_side']
                                                for r,p,b in zip(rows,roles,sides))}
                 for s in SPLITS['test']['tables']}
    accepted=gate(stats,TEST) and all(v['decision_exact']>=32 and v['structural_exact']>=32
                                    for v in per_variant.values())
    ledger=[{'id':row['id'],'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
             'roles':list(role),'belief_side':side,'decision':int(decision),
             'truth':row['label']}
            for row,role,side,decision in zip(rows,roles,sides,decisions)]
    write_new(root/'holdout_predictions.json',ledger)
    features_path=root/'holdout_features.npz'
    require(not features_path.exists(),'TEST_FEATURES_EXISTS')
    np.savez_compressed(features_path,role_features=x,belief_features=q)
    report={'schema':REPORT_SCHEMA,
            'status':'PASS_V41_PROSPECTIVE_SEMANTIC_SHADOW_ONLY' if accepted else
                     'FAIL_V41_PROSPECTIVE_SEMANTIC_SHADOW_ONLY',
            'passed':bool(accepted),**stats,'per_variant':per_variant,
            'checkpoint_sha256':sha(checkpoint(root)),
            'deck_sha256':sha(root/'holdout_deck.json'),
            'predictions_sha256':sha(root/'holdout_predictions.json'),
            'features_sha256':sha(features_path),
            'numeric_head_sha256':sha(v34.checkpoint(manifest['v34_root'])),
            'production_modified':False,'tactical_v6_changed':False,
            'v40_failed_holdout_used_for_fitting':False,
            'limitations':'Frozen-Qwen bounded four-payoff two-action descriptions; no live tactical certification.'}
    write_new(root/'holdout_acceptance.json',report)
    return report


def verify(root,*,project='.'):
    """Recompute stored held-out predictions from the saved checkpoint."""
    root=Path(root)
    report=read(root/'holdout_acceptance.json')
    manifest,net=load(root,project)
    require(report['schema']==REPORT_SCHEMA and
            report['checkpoint_sha256']==sha(checkpoint(root)) and
            report['deck_sha256']==sha(root/'holdout_deck.json') and
            report['predictions_sha256']==sha(root/'holdout_predictions.json') and
            report['features_sha256']==sha(root/'holdout_features.npz') and
            report['numeric_head_sha256']==manifest['numeric_head_sha256'],
            'AUDIT_FINGERPRINT_MISMATCH')
    rows=read(root/'holdout_deck.json')
    require(len(rows)==200 and
            {r['table_style'] for r in rows}==set(SPLITS['test']['tables']) and
            {r['belief_style'] for r in rows}==set(SPLITS['test']['beliefs']),
            'AUDIT_DECK_INVALID')
    with np.load(root/'holdout_features.npz',allow_pickle=False) as data:
        x=np.array(data['role_features'],dtype=np.float32)
        q=np.array(data['belief_features'],dtype=np.float32)
    require(x.shape==(200,4,manifest['feature_width']) and q.shape==(200,manifest['feature_width'])
            and np.isfinite(x).all() and np.isfinite(q).all(),'AUDIT_FEATURES_INVALID')
    _,numeric=v40._load_v34(manifest['v34_root'],project)
    roles,sides=_scores(rows,net,x,q)
    actions=v40.run_decisions(numeric,rows,roles,sides)
    ledger=read(root/'holdout_predictions.json')
    expected=[{'id':row['id'],'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
               'roles':list(role),'belief_side':side,'decision':int(action),'truth':row['label']}
              for row,role,side,action in zip(rows,roles,sides,actions)]
    require(ledger==expected,'AUDIT_PREDICTIONS_NOT_REPRODUCIBLE')
    recomputed=v40.scores(rows,roles,sides,actions)
    require(all(report.get(k)==v for k,v in recomputed.items()),'AUDIT_METRICS_CHANGED')
    return {'status':'PASS_V41_CHECKPOINT_REPLAY','scores':recomputed,'passed':report['passed']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('run','train','audit','verify'))
    p.add_argument('--run',required=True)
    p.add_argument('--v34-run',default='runtime/reports/gamer-v1/transition-005-v34-grounded-learning-0001')
    p.add_argument('--root',default='.')
    p.add_argument('--device',default='cuda')
    p.add_argument('--seed',type=int,default=41)
    p.add_argument('--epochs',type=int,default=120)
    p.add_argument('--batch-size',type=int,default=4)
    args=p.parse_args(argv)
    if args.stage in ('run','train'):
        result=train(args.run,v34_root=args.v34_run,device=args.device,project=args.root,
                     seed=args.seed,epochs=args.epochs,batch_size=args.batch_size)
        print('GAMER_V41_TRAIN',canonical(result),flush=True)
        if args.stage=='train' or not result['status'].startswith('PASS_'):
            return 0
    if args.stage in ('run','audit'):
        result=audit(args.run,device=args.device,project=args.root,batch_size=args.batch_size)
        print('GAMER_V41_HOLDOUT',canonical(result),flush=True)
        replay=verify(args.run,project=args.root)
        print('GAMER_V41_REPLAY',canonical(replay),flush=True)
    elif args.stage=='verify':
        print('GAMER_V41_REPLAY',canonical(verify(args.run,project=args.root)),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
