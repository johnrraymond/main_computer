#!/usr/bin/env python3
"""V42 DEV-only replay of failed V41 out-of-family semantics.

This is a diagnostic, not a new learner or a promotion route.  It never
constructs TEST, reads sealed artifacts, fits weights, or alters production.
The fixed V41 checkpoint is scored on its original DEV deck, with new Qwen
activations.  Gold semantics are used only for comparisons AFTER prediction.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_v41_surface_generalization as v41
from tools import nanojev_gamer_v1_v40_actor_grounding as v40
from tools import nanojev_gamer_v1_v33_game_learning as v33

SCHEMA='nanojev.gamer.v1.ood-dev-diagnostic.v42'


def require(condition, reason):
    if not condition:
        raise ValueError('GAMER_V42_'+reason)


def summarize(rows, roles, sides, decisions, gold_numeric=None):
    require(len(rows)==len(roles)==len(sides)==len(decisions)==120, 'INVALID_DEV_REPLAY')
    groups=defaultdict(list)
    records=[]
    for i,(r,p,s,d) in enumerate(zip(rows,roles,sides,decisions)):
        require(sorted(p)==[0,1,2,3] and s in (0,1) and d in (0,1),'INVALID_PREDICTION')
        good_roles=tuple(p)==tuple(r['roles'])
        good_belief=s==r['q_side']
        good_decision=d==r['label']
        entry={'id':r['id'],'table_style':r['table_style'],'belief_style':r['belief_style'],
               'intervention':r['intervention'],'predicted_roles':list(p),
               'true_roles':list(r['roles']),'predicted_belief':s,
               'true_belief':r['q_side'],'predicted_action':int(d),
               'true_action':r['label'],'roles_exact':good_roles,
               'belief_exact':good_belief,'structural_exact':good_roles and good_belief,
               'decision_exact':good_decision,
               'our_actor_slots_correct':sum((p[j]//2)==(r['roles'][j]//2) for j in range(4)),
               'rival_actor_slots_correct':sum((p[j]%2)==(r['roles'][j]%2) for j in range(4)),
               'prompt_sha256':hashlib.sha256(r['prompt'].encode()).hexdigest()}
        records.append(entry)
        for name,key in (('table_style',r['table_style']),
                         ('belief_style',r['belief_style']),
                         ('intervention',r['intervention'])):
            groups[(name,str(key))].append(i)
    def group_report(indices):
        return {'total':len(indices),
                'roles_exact':sum(records[i]['roles_exact'] for i in indices),
                'belief_exact':sum(records[i]['belief_exact'] for i in indices),
                'structural_exact':sum(records[i]['structural_exact'] for i in indices),
                'decision_exact':sum(records[i]['decision_exact'] for i in indices),
                'our_actor_slots_correct':sum(records[i]['our_actor_slots_correct'] for i in indices),
                'rival_actor_slots_correct':sum(records[i]['rival_actor_slots_correct'] for i in indices)}
    by={name:{} for name in ('table_style','belief_style','intervention')}
    for (name,key),inds in sorted(groups.items()):
        by[name][key]=group_report(inds)
    pair_records=[]
    for i in range(0,len(rows),2):
        a,b=rows[i:i+2]
        require(a['pair']==b['pair'],'BROKEN_PAIR')
        pair_records.append({'pair_id':a['pair'],'table_style':a['table_style'],
             'intervention':a['intervention'],'requires_reversal':a['label']!=b['label'],
             'both_decisions_exact':records[i]['decision_exact'] and records[i+1]['decision_exact'],
             'both_structures_exact':records[i]['structural_exact'] and records[i+1]['structural_exact']})
    overall=v40.scores(rows,roles,sides,decisions)
    errors=[record for record in records if not record['structural_exact'] or not record['decision_exact']]
    out={'total':len(rows),'overall':overall,'by':by,
         'number_before_action_description_table_style':10,
         'number_before_action_description':group_report([i for i,r in enumerate(rows) if r['table_style']==10]),
         'number_after_action_description':group_report([i for i,r in enumerate(rows) if r['table_style'] in (8,9)]),
         'pairs':{'total':len(pair_records),
                  'both_decisions_exact':sum(x['both_decisions_exact'] for x in pair_records),
                  'reversals_total':sum(x['requires_reversal'] for x in pair_records),
                  'reversals_exact':sum(x['requires_reversal'] and x['both_decisions_exact'] for x in pair_records)},
         'errors':errors}
    if gold_numeric is not None:
        require(len(gold_numeric)==len(rows),'INVALID_NUMERIC_CONTROL')
        out['oracle_assisted_dev_numeric_diagnostic_only']={
            'correct_with_gold_semantic_slots':sum(int(p)==r['label'] for p,r in zip(gold_numeric,rows)),
            'total':len(rows),'gold_semantics_never_used_for_inference':True}
    return out


def run(v41_root,output,*,project='.',device='cuda',batch_size=4):
    import torch
    base=Path(v41_root)
    dest=Path(output)
    require(not dest.exists(),'OUTPUT_EXISTS')
    # No approval gate is bypassed: this is replay of already-open DEV only.
    manifest,semantic=v41.load(base,project)
    require(manifest.get('dev_pass') is False,'EXPECTED_FAILED_DEV_CHECKPOINT')
    require(not (base/'holdout_deck.json').exists() and
            not (base/'holdout_consumed.json').exists() and
            not (base/'holdout_acceptance.json').exists(), 'UNEXPECTED_TEST_ARTIFACT')
    rows=v41.make_deck('dev',seed=manifest['seed'])
    require(v41.digest(rows)==manifest['dev_deck_sha256'] and
            len(rows)==120 and
            {r['table_style'] for r in rows}==set(v41.SPLITS['dev']['tables']) and
            {r['belief_style'] for r in rows}==set(v41.SPLITS['dev']['beliefs']),
            'DEV_DECK_CHANGED')
    lineage=manifest['lineage']
    t,tok,model=v33.model_context(lineage['model_id'],device)
    try:
        x,q=v40.capture(model,tok,t,[r['prompt'] for r in rows],
                         device=device,batch_size=batch_size,tag='v42-dev-only')
    finally:
        del model,tok
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    roles,sides=v40.predict_slots(semantic,x,q)
    _,numeric=v40._load_v34(manifest['v34_root'],project)
    decisions=v40.run_decisions(numeric,rows,roles,sides)
    score=v40.scores(rows,roles,sides,decisions)
    require(score==manifest['dev_scores'], 'DEV_CHECKPOINT_REPLAY_MISMATCH')
    # Gold control is explicitly separate from learned inference above.
    gold=v40.run_decisions(numeric,rows,
                           [r['roles'] for r in rows],
                           [r['q_side'] for r in rows])
    report=summarize(rows,roles,sides,decisions,gold)
    report.update({'schema':SCHEMA,'status':'DEV_ONLY_OOD_DIAGNOSTIC_NO_PROMOTION',
                   'checkpoint_sha256':v41.sha(v41.checkpoint(base)),
                   'v41_manifest_sha256':v41.sha(base/'train_manifest.json'),
                   'dev_deck_sha256':v41.digest(rows),
                   'holdout_accessed':False,'new_training_performed':False,
                   'production_modified':False,
                   'limitations':['DEV was already used for V41 checkpoint selection; it is not a new blind sample',
                                 'Per-template breakdown is diagnostic, not evidence of future out-of-family generalization',
                                 'Oracle-assisted numeric control is not used for predictions']})
    dest.mkdir(parents=True,exist_ok=False)
    v41.write_new(dest/'ood_dev_report.json',report)
    print('GAMER_V42_DEV_OOD_AUDIT',v41.canonical({
        'status':report['status'],'structural_exact':score['structural_exact'],
        'decision_exact':score['decision_exact'], 'belief_exact':score['belief_exact'],
        'by_table_style':report['by']['table_style'],
        'report':str(dest/'ood_dev_report.json')}),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--v41-run',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--root',default='.')
    p.add_argument('--batch-size',type=int,default=4)
    args=p.parse_args(argv)
    run(args.v41_run,args.output,project=args.root,device=args.device,batch_size=args.batch_size)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
