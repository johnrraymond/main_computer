"""G2-0046: prompt-disjoint, TRAIN-only frozen-feature relational transfer.

Diagnostics, never selection. No DEV/TEST capture, model production promotion,
or changes to G1/G2/parent policy. Readout candidates use the same frozen h,
prior, cardinality outputs, and exact G2-0033 graph decoder as G2-0045.
"""
from __future__ import annotations

import gzip
import json
import math
import random
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file, save_file
from torch.nn import functional as F

from tools import nanojev_gamer_v1_g2_separability_diagnosis as d45

d44=d45.d44
b43=d45.b43
g40=d45.g40
g41=d45.g41
g42=d45.g42
backbone=d45.backbone

SCHEMA='nanojev.gamer.graph.g2.multiprompt-discrimination-diagnosis.v1'
REPORT='g2_multiprompt_discrimination_report.json'
LEDGER='g2_multiprompt_discrimination_per_case.jsonl.gz'
WEIGHTS='g2_multiprompt_discrimination_development.safetensors'
POLICIES='g2_multiprompt_discrimination_frozen_policies.json'
SEED=460546
STEPS=720
BATCH_PROMPTS=8
EVAL_EVERY=120
KINDS=('linear','nonlinear')
LOSSES=('bce','bce_margin')
MAX_STEPS=4000


def verify_parent45(directory, expected_hashes):
    """Refuse broken G2-0045 provenance, immutable products, or opened splits."""
    folder=Path(directory)
    report=json.loads((folder/d45.REPORT).read_text(encoding='utf-8'))
    expected={
        'schema':d45.SCHEMA,
        'status':'TRAIN_SEPARABILITY_DIAGNOSIS_COMPLETE',
        'source_parent_schema':d44.SCHEMA,
        'parent_hashes':expected_hashes,
        'development_evaluation_skipped':True,
        'dev_labels_used_for_calibration':False,
        'dev_labels_used_for_training':False,
        'sealed_test_used':False,
        'production_promotion':False,
        'g2_certified':False,
        'frozen_g1':True,
        'frozen_g2_0033_parent':True,
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnostic_only_no_selection'},
    }
    for key,value in expected.items():
        if report.get(key)!=value:
            raise ValueError('GAMER_G2_0046_PARENT_0045_CONTRACT:'+key)
    if set(report.get('case_selection',{}))!={'simple','hard'} or \
       len(report.get('unseen_train_prompts',[]))!=d45.N_HELDOUT or \
       len(set(report['unseen_train_prompts']))!=d45.N_HELDOUT:
        raise ValueError('GAMER_G2_0046_PARENT_CASES_INVALID')
    fixed=[report['case_selection'][label]['prompt_sha256'] for label in ('simple','hard')]
    if len(set([*fixed,*report['unseen_train_prompts']]))!=d45.N_HELDOUT+2:
        raise ValueError('GAMER_G2_0046_PARENT_CASES_OVERLAP')
    if set(report.get('experiments',{}))!={'simple','hard'} or any(
        set(report['experiments'][label])!={f'{k}:{v}' for k in d45.KINDS for v in d45.LOSSES}
        for label in ('simple','hard')):
        raise ValueError('GAMER_G2_0046_PARENT_EXPERIMENT_CONTRACT')
    if set(report.get('direct_controls',{}))!={'simple','hard'} or not all(
        v.get('exact') for v in report['direct_controls'].values()):
        raise ValueError('GAMER_G2_0046_PARENT_DIRECT_CONTROL_FAILED')
    policy=json.loads((folder/d45.POLICIES).read_text(encoding='utf-8'))
    if policy!=report['train_policy']:
        raise ValueError('GAMER_G2_0046_PARENT_POLICY_DRIFT')
    hashes={}
    for filename, name in ((d45.REPORT,'report'),(d45.LEDGER,'ledger'),
                           (d45.WEIGHTS,'weights'),(d45.POLICIES,'policies')):
        digest=backbone.sha256(folder/filename)
        hashes[f'g2_0045_{name}_sha256']=digest
        if name!='report' and digest!=report.get(f'{name}_sha256'):
            raise ValueError('GAMER_G2_0046_PARENT_HASH:'+filename)
    weights=load_file(str(folder/d45.WEIGHTS),device='cpu')
    expected_keys={}
    parent=d45.x33.GraphSetEnergy().eval().requires_grad_(False)
    for label in ('simple','hard'):
        for kind in d45.KINDS:
            for loss in d45.LOSSES:
                for name,value in d45.Readout(kind,parent).state_dict().items():
                    expected_keys[f'{label}_{kind}_{loss}.{name}']=value.shape
    if set(weights)!=set(expected_keys) or any(
        value.shape!=expected_keys[name] or not torch.isfinite(value).all()
        for name,value in weights.items()):
        raise ValueError('GAMER_G2_0046_PARENT_WEIGHTS_INVALID')
    rows=0
    with gzip.open(folder/d45.LEDGER,'rt',encoding='utf-8') as f:
        for line in f:
            row=json.loads(line)
            if row.get('section') not in {
                'train_own','train_unseen','train_fit','natural_train_audit'}:
                raise ValueError('GAMER_G2_0046_PARENT_LEDGER_INVALID')
            rows+=1
    expected_rows=2*len(d45.KINDS)*len(d45.LOSSES)*(1+d45.N_HELDOUT+80+80)
    if rows!=expected_rows:
        raise ValueError('GAMER_G2_0046_PARENT_LEDGER_COUNT')
    return report,hashes


def _sha(rec):
    return d44._sha(rec)


def _gold(rec):
    return d44._gold(rec)


def _candidate_gap(rec):
    return len(_gold(rec)-set(rec['shortlisted']['edges']))


def _sha_index(records, label):
    result={}
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0046_NONTRAIN_RECORD:'+label)
        key=_sha(rec)
        if key in result:
            raise ValueError('GAMER_G2_0046_DUPLICATE_PROMPT:'+label)
        result[key]=rec
    return result


def select_cohorts(training, fitting, auditing, parent45, policy, *, minimum_train=8):
    """Predeclare prompts before fitting. Never tune on fixed transfer/audits."""
    all_sets={
        'source_train':_sha_index(training,'source_train'),
        'train_fit':_sha_index(fitting,'train_fit'),
        'natural_train_audit':_sha_index(auditing,'natural_train_audit'),
    }
    if any(set(all_sets[a])&set(all_sets[b]) for a,b in (
            ('source_train','train_fit'),('source_train','natural_train_audit'),
            ('train_fit','natural_train_audit'))):
        raise ValueError('GAMER_G2_0046_TRAIN_COHORT_OVERLAP')
    source=all_sets['source_train']
    fixed=[parent45['case_selection'][label]['prompt_sha256'] for label in ('simple','hard')]
    fixed+=list(parent45['unseen_train_prompts'])
    if any(key not in source for key in fixed) or len(set(fixed))!=len(fixed):
        raise ValueError('GAMER_G2_0046_PARENT_TRANSFER_CASE_MISMATCH')
    # Decoder-reconstructibility is an explicit TRAIN-only eligibility gate,
    # separate from the unavoidable gold edges missing from the shortlist.
    oracle={}
    for key,rec in sorted(source.items()):
        if _candidate_gap(rec):
            oracle[key]=False
            continue
        try:
            oracle[key]=bool(d44.direct_gold_oracle(rec,policy))
        except ValueError as exc:
            if 'GOLD_ORACLE_DECODER_ALIGNMENT' not in str(exc):
                raise
            oracle[key]=False
    eligible={key for key in source if oracle[key] and len(_gold(source[key]))>0 and
              len(source[key]['shortlisted']['edges'])>len(_gold(source[key]))}
    # The exact eight prior G2-0045 cases remain transfer-only even when their
    # edge set is not reproducible under the fixed decoder. Never optimize them.
    heldout=set(fixed)
    # Deterministic 20% source-TRAIN transfer holdout, including unreachable
    # graphs. Choose by prompt ID, independent of gold or learner performance.
    target=max(len(heldout),min(32,round(len(source)*.2)))
    heldout.update(sorted(set(source)-heldout)[:max(0,target-len(heldout))])
    fit=sorted(eligible-heldout)
    if len(fit)<minimum_train:
        raise ValueError('GAMER_G2_0046_INSUFFICIENT_RECONSTRUCTIBLE_TRAIN')
    fit_records=[source[k] for k in fit]
    hold_records=[source[k] for k in sorted(heldout)]
    if set(fit)&heldout:
        raise ValueError('GAMER_G2_0046_FIT_HOLDOUT_COLLISION')
    # Natural audit and train-fit are measurement-only, regardless of names.
    return {
        'learn':fit_records,
        'transfer':hold_records,
        'train_fit':list(fitting),
        'natural_train_audit':list(auditing),
        'oracle_by_sha':oracle,
        'fixed_transfer_prompts':fixed,
        'excluded_train_prompts':sorted(set(source)-set(fit)-heldout),
        'all_source_train_count':len(source),
    }


def prepare_supervision(records, parent):
    """Frozen features, TRAIN-only edge labels, per-group contrast indices."""
    items={}
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0046_DEV_LABEL_SUPERVISION_FORBIDDEN')
        sha=_sha(rec)
        if sha in items:
            raise ValueError('GAMER_G2_0046_DUPLICATE_SUPERVISION')
        if _candidate_gap(rec):
            raise ValueError('GAMER_G2_0046_UNREACHABLE_FITTING_PROMPT')
        h,prior,_=d45.extract_frozen(rec,parent)
        labels=torch.as_tensor([edge in _gold(rec) for edge in rec['shortlisted']['edges']],
                               dtype=h.dtype)
        if float(labels.sum())<=0 or float(labels.sum())>=len(labels):
            raise ValueError('GAMER_G2_0046_UNINFORMATIVE_FITTING_PROMPT')
        groupids=np.asarray(rec['features']['index'],dtype=np.int64)
        contrasts=[]
        for g in range(len(rec['features']['groups'])):
            positions=np.flatnonzero(groupids==g)
            yes=torch.as_tensor([int(i) for i in positions if labels[int(i)]>0],dtype=torch.long)
            no=torch.as_tensor([int(i) for i in positions if labels[int(i)]==0],dtype=torch.long)
            if len(yes) and len(no):
                contrasts.append((yes,no))
        pos=float(labels.sum())
        weight=min(4.,max(1.,math.sqrt(max(1.,len(labels)-pos)/max(1.,pos))))
        items[sha]=(h,prior,labels,contrasts,torch.tensor(weight,dtype=h.dtype))
    return items


def prompt_loss(scores,supervision,variant):
    """Each prompt contributes one loss, independently of candidate count."""
    if variant not in LOSSES:
        raise ValueError('GAMER_G2_0046_BAD_OBJECTIVE')
    _,_,truth,contrasts,pos_weight=supervision
    if scores.shape!=truth.shape:
        raise ValueError('GAMER_G2_0046_SCORE_SHAPE_MISMATCH')
    bce=F.binary_cross_entropy_with_logits(scores,truth,pos_weight=pos_weight)
    margin=scores.sum()*0.
    if variant=='bce_margin' and contrasts:
        margins=[F.softplus(1.5+scores[neg].max()-scores[yes].min())
                 for yes,neg in contrasts]
        margin=torch.stack(margins).mean()
    loss=bce+(1.1*margin if variant=='bce_margin' else 0.)
    if not torch.isfinite(loss):
        raise ValueError('GAMER_G2_0046_LOSS_NONFINITE')
    return loss


def fixed_prompt_batches(count,steps,batch_size,seed):
    """Identical bounded prompt schedule for every architecture/objective."""
    if count<1 or steps<1 or batch_size<1:
        raise ValueError('GAMER_G2_0046_BATCH_CONTRACT')
    rng=random.Random(seed)
    cycle=[]
    index=0
    result=[]
    for _ in range(steps):
        batch=[]
        while len(batch)<batch_size:
            if index==len(cycle):
                cycle=list(range(count))
                rng.shuffle(cycle)
                index=0
            take=min(batch_size-len(batch),len(cycle)-index)
            batch.extend(cycle[index:index+take]);index+=take
        result.append(tuple(batch))
    return result


def score_records(records,model,policy,cohort,arm,step,caches=None):
    rows=[]
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0046_DEV_EVALUATION_FORBIDDEN')
        key=_sha(rec)
        if caches is not None and key in caches:
            h,prior,_=caches[key]
        else:
            raise ValueError('GAMER_G2_0046_MISSING_FROZEN_CACHE')
        with torch.inference_mode():
            scores=model(h,prior).detach().double().numpy()
        pred=rec['structured_pred']
        selected=d44._decode(rec,(scores,pred[1],pred[2]),policy)
        row=d44._quality(rec,selected,scores,section=cohort,kind=arm,
                         phase='multiprompt',step=step)
        row['missing_candidate_gold_edges']=_candidate_gap(rec)
        row['candidate_reachable']=row['missing_candidate_gold_edges']==0
        rows.append(row)
    return rows


def score_parent(records,policy,cohort):
    rows=[]
    for rec in records:
        scores=rec['structured_pred'][0]
        selected=d44._decode(rec,rec['structured_pred'],policy)
        row=d44._quality(rec,selected,scores,section=cohort,kind='frozen_parent',
                         phase='baseline',step=0)
        row['missing_candidate_gold_edges']=_candidate_gap(rec)
        row['candidate_reachable']=row['missing_candidate_gold_edges']==0
        rows.append(row)
    return rows


def summarize(rows):
    def measures(selected):
        return {**d44._summary(selected),
                'missing_candidate_gold_edges':sum(r['missing_candidate_gold_edges'] for r in selected),
                'exact_rate':(sum(r['exact'] for r in selected)/len(selected)) if selected else None}
    families=sorted({str(row.get('family')) for row in rows})
    return {
        'all':measures(rows),
        'candidate_reachable':measures([r for r in rows if r['candidate_reachable']]),
        'candidate_unreachable':measures([r for r in rows if not r['candidate_reachable']]),
        'by_family':{family:measures([row for row in rows
                        if str(row.get('family'))==family]) for family in families},
    }


def fit_shared(records,parent,kind,variant,policy,*,steps=STEPS,batch_size=BATCH_PROMPTS,
               eval_every=EVAL_EVERY,seed=SEED,supervision=None,caches=None):
    if not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or not 1<=eval_every<=steps or \
       kind not in KINDS or variant not in LOSSES:
        raise ValueError('GAMER_G2_0046_TRAIN_BUDGET_OR_KIND')
    if not records or any(r['row'].get('split')!='train' for r in records):
        raise ValueError('GAMER_G2_0046_TRAIN_ONLY')
    torch.manual_seed(seed+(0 if kind=='linear' else 100))
    model=d45.Readout(kind,parent).train()
    supervision=supervision if supervision is not None else prepare_supervision(records,parent)
    if set(supervision)!={_sha(r) for r in records}:
        raise ValueError('GAMER_G2_0046_SUPERVISION_COHORT_MISMATCH')
    caches=caches if caches is not None else {
        key:(val[0],val[1],None) for key,val in supervision.items()}
    trainable=[p for p in model.parameters() if p.requires_grad]
    if not trainable:
        raise ValueError('GAMER_G2_0046_NO_TRAINABLE_HEAD')
    optimizer=torch.optim.Adam(trainable,lr=.008 if kind=='linear' else .004)
    prior_state={key:value.detach().clone() for key,value in parent.state_dict().items()}
    schedule=fixed_prompt_batches(len(records),steps,batch_size,seed)
    history=[]
    for step,batch in enumerate(schedule,1):
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for i in batch:
            fields=supervision[_sha(records[i])]
            scores=model(fields[0],fields[1])
            losses.append(prompt_loss(scores,fields,variant))
        loss=torch.stack(losses).mean()
        loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(trainable,5.)
        if not torch.isfinite(norm) or any(p.grad is None or not torch.isfinite(p.grad).all()
                                            for p in trainable):
            raise ValueError('GAMER_G2_0046_GRADIENT_INVALID')
        optimizer.step()
        if step==1 or step%eval_every==0 or step==steps:
            model.eval()
            rows=score_records(records,model,policy,'train_learn',f'{kind}:{variant}',step,caches)
            summary=summarize(rows)
            history.append({'step':step,'loss':float(loss.detach()),
                            'gradient_norm':float(norm),'training':summary})
            if step==1 or step%max(240,eval_every)==0 or step==steps:
                print(f'GAMER_G2_0046_FIT {kind}:{variant} {step}/{steps} '
                      f'exact={summary["all"]["exact"]}/{len(records)} '
                      f'loss={float(loss.detach()):.6f}',flush=True)
            model.train()
    for name,value in prior_state.items():
        if not torch.equal(value,parent.state_dict()[name]):
            raise ValueError('GAMER_G2_0046_FROZEN_PARENT_MUTATED')
    model.eval().requires_grad_(False)
    return model,{'steps_completed':steps,'prompts_trained':len(records),
                  'batch_prompts':batch_size,'history':history,
                  'train_final':history[-1]['training']}


def interpret(results):
    """Evidence classification, never a policy winner or representation proof."""
    transfer=results['parent']['transfer']['candidate_reachable']
    comparator=(transfer['exact'],transfer['missing']+transfer['extra'])
    changes=[]
    for key,value in results['arms'].items():
        candidate=value['transfer']['candidate_reachable']
        changes.append((key,candidate['exact']-comparator[0],
                        (candidate['missing']+candidate['extra'])-comparator[1]))
    if any(delta>0 for _,delta,_ in changes):
        return 'MULTIPROMPT_EXACT_TRANSFER_IMPROVEMENT_OBSERVED'
    if any(delta==0 and error<0 for _,delta,error in changes):
        return 'MULTIPROMPT_PARTIAL_TRANSFER_IMPROVEMENT_OBSERVED'
    return 'MULTIPROMPT_TRANSFER_NOT_DEMONSTRATED_WITH_TESTED_READOUTS'


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,parent45,hashes,
           *,steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY):
    folder=Path(output)
    if folder.exists():
        raise FileExistsError('GAMER_G2_0046_IMMUTABLE_OUTPUT')
    if not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or not 1<=eval_every<=steps:
        raise ValueError('GAMER_G2_0046_STEP_BOUNDS')
    cohort=select_cohorts(training,fitting,auditing,parent45,policy)
    parent_state={k:v.detach().clone() for k,v in parent.state_dict().items()}
    fit_records=cohort['learn']
    # All expensive frozen feature capture is label-free; both heads consume
    # precisely the same representation. No DEV capture or gradients upstream.
    combined=[*fit_records,*cohort['transfer'],*fitting,*auditing]
    caches={_sha(rec):d45.extract_frozen(rec,parent) for rec in combined}
    supervision=prepare_supervision(fit_records,parent)
    sets={name:cohort[name] for name in (
        'learn','transfer','train_fit','natural_train_audit')}
    parent_rows={name:score_parent(recs,policy,name) for name,recs in sets.items()}
    ledger=[*sum(parent_rows.values(),[])]
    baseline={name:summarize(rows) for name,rows in parent_rows.items()}
    results={};weights={}
    for kind in KINDS:
        for variant in LOSSES:
            key=f'{kind}:{variant}'
            model,stats=fit_shared(fit_records,parent,kind,variant,policy,
                     steps=steps,batch_size=batch_size,eval_every=eval_every,
                     supervision=supervision,caches=caches)
            for name,recs in sets.items():
                scored=score_records(recs,model,policy,name,key,steps,caches)
                ledger.extend(scored)
                stats[name]=summarize(scored)
            results[key]=stats
            weights.update({f'{kind}_{variant}.{name}':value.detach().cpu().contiguous().clone()
                            for name,value in model.state_dict().items()})
            assert_frozen()
    if any(not torch.equal(value,parent.state_dict()[name])
           for name,value in parent_state.items()):
        raise ValueError('GAMER_G2_0046_FROZEN_PARENT_MUTATED')
    report={
        'schema':SCHEMA,'status':'TRAIN_MULTIPROMPT_DIAGNOSIS_COMPLETE',
        'source_parent_schema':d45.SCHEMA,'parent_hashes':hashes,
        'train_dataset_sha256':parent45['train_dataset_sha256'],
        'training_sha256':parent45['training_sha256'],
        'cohorts':{
            'train_source_count':cohort['all_source_train_count'],
            'train_learn_prompts':list(map(_sha,fit_records)),
            'transfer_prompts':list(map(_sha,cohort['transfer'])),
            'fixed_g2_0045_transfer_prompts':cohort['fixed_transfer_prompts'],
            'excluded_train_prompts':cohort['excluded_train_prompts'],
            'train_fit_prompts':list(map(_sha,fitting)),
            'natural_train_audit_prompts':list(map(_sha,auditing)),
            'decoder_reconstructible_in_source':sum(cohort['oracle_by_sha'].values()),
            'candidate_unreachable_in_source':sum(_candidate_gap(r)>0 for r in training),
        },
        'protocol':{
            'architecture':KINDS,'objectives':LOSSES,
            'steps':steps,'batch_prompts':batch_size,'eval_every':eval_every,
            'optimizer':'Adam','linear_learning_rate':.008,
            'nonlinear_learning_rate':.004,
            'seed':SEED,'identical_training_batches':True,
            'training_selection':'TRAIN_ONLY_CANDIDATE_AND_DECODER_RECONSTRUCTIBLE',
            'independent_train_fit_measurement_only':True,
            'natural_train_audit_measurement_only':True,
            'fixed_0045_cases_reserved_for_transfer':True,
            'same_frozen_features_and_prior':True,
            'same_frozen_decoder_and_count_heads':True,
            'no_holdout_or_audit_checkpoint_selection':True,
            'never_open_dev':True,'immutable_results':True,
        },
        'results':{'parent':baseline,'arms':results},
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'frozen_g1':True,'frozen_g2_0033_parent':True,
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnostic_only_no_selection'},
    }
    report['conclusion']=interpret(report['results'])
    assert_frozen()
    folder.parent.mkdir(parents=True,exist_ok=True)
    # Atomic publication: do not expose an incomplete output after a failure.
    temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
    try:
        with gzip.open(temp/LEDGER,'wt',encoding='utf-8',compresslevel=6) as stream:
            for row in ledger:
                stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        save_file(weights,str(temp/WEIGHTS))
        (temp/POLICIES).write_text(json.dumps(report['train_policy'],sort_keys=True,indent=2)+'\n',
                                   encoding='utf-8')
        report.update({'ledger_sha256':backbone.sha256(temp/LEDGER),
                       'weights_sha256':backbone.sha256(temp/WEIGHTS),
                       'policies_sha256':backbone.sha256(temp/POLICIES)})
        (temp/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,
                                             allow_nan=False)+'\n',encoding='utf-8')
        if folder.exists():
            raise FileExistsError('GAMER_G2_0046_IMMUTABLE_OUTPUT_RACE')
        temp.rename(folder)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,memorization44_run,
        separability45_run,output,steps=STEPS,batch_size=BATCH_PROMPTS,
        eval_every=EVAL_EVERY,**kwargs):
    p40,h40=g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g42.verify_parent41(oracle41_run,h40)
    p42,h42=b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    for name in ('training_sha256','train_dataset_sha256'):
        if p45[name]!=p44[name] or p45[name]!=p43[name]:
            raise ValueError('GAMER_G2_0046_PARENT_TRAIN_HASH_DRIFT:'+name)
    if Path(output).exists():
        raise FileExistsError('GAMER_G2_0046_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,assert_all_frozen,**ignored):
        for name,rows in [('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)]:
            if p45['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0046_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:
            raise ValueError('GAMER_G2_0046_PARENT_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,
            parent33_models[g40.SOURCE_SEED],assert_all_frozen,
            output,p45,{**h40,**h41,**h42,**h43,**h44,**h45},
            steps=steps,batch_size=batch_size,eval_every=eval_every)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
