#!/usr/bin/env python3
"""Reproducibly define 24 fresh post-baseline, author-synthesized challenge cases.

Not an expert gold corpus. The fixed 40-case baseline is NOT edited. Fixture
construction is separate from any model inference, and no gold enters prompts.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

from tools import nanojev_contract_v3_live_head as bridge

POLICY_FIELDS=('review_seconds','dispute_filing_seconds','appointment_seconds','ruling_seconds')
OUT=Path(__file__).resolve().parent/'fixtures'/'nanojev_v3_fresh_challenge'/'manifest.json'


def prototype(rows:list[tuple[str,int,int,int,int,int]], *, window=(4,3,5,8),
              silence=('pay_worker','refund_payer','refund_payer'), style:int=0):
    total=sum(x[1] for x in rows)
    return {'language':'nanojev.contract.governed_escrow.v3','asset':'native_eth',
            'settlement_mode':'pull','payer':'deployer','worker':'constructor_worker',
            'arbitrator':'jointly_appointed_after_dispute','amount_wei':total*10**18,
            'milestones':[{'id':ident,'amount_wei':amount*10**18,
                           'deadline_seconds':deadline*86400,
                           'extension_seconds':extension*86400,
                           'extension_decision_seconds':window_days*86400,
                           'max_extensions':count}
                          for ident,amount,deadline,extension,window_days,count in rows],
            'policy':dict(zip(POLICY_FIELDS,[int(x)*86400 for x in window])) | {
            'on_review_silence':silence[0],
            'on_appointment_failure':silence[1],
            'on_arbitrator_silence':silence[2],
            'on_missed_submission':'refund_payer',
            'on_undisputed_rejection':'refund_payer'}}


def request_and_replay(spec:dict, style:int):
    lines=[];spans={}
    def add(path,line):
        lines.append(line);spans[path]=line
    amount=spec['amount_wei']//10**18
    add('amount_wei',
        f'The payer escrows {amount} ETH for the worker, with disputes decided by a mutually appointed arbitrator.'
        if style==0 else
        f'The payer deposits {amount} ETH into escrow for the worker; disputes go to a mutually appointed arbitrator.')
    for m in spec['milestones']:
        i=m['id']; n=m['amount_wei']//10**18;d=m['deadline_seconds']//86400
        e=m['extension_seconds']//86400;w=m['extension_decision_seconds']//86400;c=m['max_extensions']
        if style==0:
            add(f'milestones.{i}.amount_wei',f'The {i} milestone is worth {n} ETH.')
            add(f'milestones.{i}.deadline_seconds',f'The {i} deadline is {d} days after activation.')
            add(f'milestones.{i}.extension_seconds',f'The {i} extension adds {e} days.')
            add(f'milestones.{i}.extension_decision_seconds',f'The {i} extension decision window is {w} days after its due time.')
            add(f'milestones.{i}.max_extensions',f'The {i} permits a maximum of {c} extensions.')
        else:
            add(f'milestones.{i}.amount_wei',f'The {i} milestone pays {n} ETH.')
            add(f'milestones.{i}.deadline_seconds',f'The {i} delivery is due within {d} days of activation.')
            add(f'milestones.{i}.extension_seconds',f'The {i} extension is {e} days long.')
            add(f'milestones.{i}.extension_decision_seconds',f'The {i} extension decision window lasts {w} days after due.')
            add(f'milestones.{i}.max_extensions',f'The {i} extension limit is {c} extensions.')
    for field,label in [('review_seconds','review'),('dispute_filing_seconds','dispute filing'),
                        ('appointment_seconds','appointment'),('ruling_seconds','ruling')]:
        days=spec['policy'][field]//86400
        add('policy.'+field,f'The {label} window is {days} days.' if style==0 else
            f'The {label} window lasts {days} days.')
    for field,label in [('on_review_silence','review silence'),('on_appointment_failure','appointment failure'),
                        ('on_arbitrator_silence','arbitrator silence'),('on_missed_submission','missed submission'),
                        ('on_undisputed_rejection','undisputed rejection')]:
        policy=spec['policy'][field].replace('_',' ')
        add('policy.'+field,f'On {label}, {policy}.' if style==0 else
            f'If there is {label}, {policy}.')
    text='\n'.join(lines)+'\n'
    return text, {'status':'contract','contract':deepcopy(spec),'source_spans':spans}


def build():
    cases=[]
    def add(id,family,spec,style=0,*,pairing=None,pair_type=None,changed_path=None,request=None,
            expected='contract',topic=None):
        rendered,replay=request_and_replay(spec,style)
        if request is not None:rendered=request
        if expected=='needs_clarification':
            gold=None; replay={'status':'needs_clarification','missing':[' '.join(topic)]}
        else:gold=deepcopy(spec)
        cases.append({'id':id,'family':family,'expected':expected,'request':rendered,
            'gold':gold,'replay':replay,'expected_refusal_topic':topic,'pairing':pairing,
            'pair_type':pair_type,'changed_path':changed_path})
    inv=[
        ('inv_q1',[('proposal',5,11,2,1,0)],(4,3,5,8)),
        ('inv_q2',[('design',2,8,3,2,1),('delivery',6,17,4,1,0)],(6,4,3,7)),
        ('inv_q3',[('outline',2,6,2,1,1),('implementation',4,17,3,2,1),('handoff',3,27,5,1,0)],(4,2,4,9)),
        ('inv_q4',[('specification',1,7,2,1,0),('implementation',2,15,3,2,1),('qa',3,24,3,1,2),('release',4,32,2,1,0)],(3,2,3,6))
    ]
    for id,rows,windows in inv:
        spec=prototype(rows,window=windows)
        for variant in (0,1):
            add(f'{id}_v{variant}','paraphrase',spec,variant,
                pairing=id,pair_type='invariant')
    # Contrast experiments: one and only one IR value changed per pair.
    contrast_specs=[
        ('contrast_due',prototype([('audit',4,12,2,1,1),('transfer',7,20,3,2,1)]),
         'milestones.audit.deadline_seconds'),
        ('contrast_silence',prototype([('intake',3,9,3,2,1),('settlement',6,18,2,1,1)]),
         'policy.on_review_silence'),
        ('contrast_limit',prototype([('delivery',8,20,4,1,1)]),
         'milestones.delivery.max_extensions'),
    ]
    for id,spec,path in contrast_specs:
        mutated=deepcopy(spec)
        if path.startswith('policy.'):
            k=path.split('.')[1];mutated['policy'][k]='refund_payer' if spec['policy'][k]=='pay_worker' else 'pay_worker'
        else:
            _,ident,k=path.split('.')
            one=next(m for m in mutated['milestones'] if m['id']==ident)
            one[k]+=86400 if k.endswith('_seconds') else 1
        for variant,obj in enumerate((spec,mutated)):
            add(f'{id}_v{variant}','structure_and_contrast',obj,0,
                pairing=id,pair_type='contrast',changed_path=path)
    # Ten negative cases. Each has its own failure target; no gold sent to model.
    seed=prototype([('research',4,10,2,1,0),('deployment',5,23,4,2,1)],window=(4,2,3,8))
    original,_=request_and_replay(seed,0)
    def neg(id,family,request,topic):
        add(id,family,seed,0,request=request,expected='needs_clarification',topic=topic)
    neg('fresh_worker_approves','authority',original+'The worker alone may approve its own completion; payer approval is prohibited.\n',['worker','approv'])
    neg('fresh_payer_appoints','authority',original+'The payer can appoint the arbitrator alone, without worker agreement.\n',['appoint','arbitrat'])
    neg('fresh_observer_release','authority',original+'A fourth-party observer may release escrow despite payer rejection.\n',['observer','release'])
    neg('fresh_worker_extension','authority',original+'The worker can unilaterally extend any due date without payer consent.\n',['worker','exten'])
    neg('fresh_conflict_deadline','contradiction',original+'The research deadline is 19 days after activation; both deadlines remain binding.\n',['research','deadline'])
    neg('fresh_conflict_total','contradiction',original+'The total escrow must equal 11 ETH, not 9 ETH; the earlier amount remains binding.\n',['escrow','amount'])
    neg('fresh_conflict_silence','contradiction',original+'On review silence, refund payer; the prior pay-worker rule is also binding.\n',['review','silence'])
    neg('fresh_conflict_extension','contradiction',original+'The deployment extension adds 9 days; neither duration supersedes the other.\n',['deployment','extension'])
    neg('fresh_missing_review','omission',original.replace('The review window is 4 days.\n',''),['review','window'])
    neg('fresh_missing_amount','omission',original.replace('The deployment milestone is worth 5 ETH.\n',''),['deployment','amount'])
    return {'schema':'nanojev.contract.v3.generalization_corpus.v1',
            'origin':'ASSISTANT_AUTHORED_SYNTHESES_NOT_INDEPENDENT_GOLD',
            'mode':'FRESH_POST_BASELINE_CHALLENGE_NO_GOLD_PROMPT_ACCESS',
            'training_separation':'CASESET_CREATED_AFTER_40_CASE_MEASUREMENT_NOT_INDEPENDENTLY_ADJUDICATED',
            'cases':cases}


def main():
    obj=build()
    if OUT.exists():
        existing=json.loads(OUT.read_text(encoding='utf-8'))
        if existing!=obj:raise SystemExit('ERROR: pinned corpus differs from constructor; refusing rewrite')
        print('Verified pinned fresh challenge:',len(obj['cases']))
        return
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('Created:',OUT,len(obj['cases']))

if __name__=='__main__':main()
