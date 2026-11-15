"""G2-0045: TRAIN-only frozen-feature separability and loss-interference diagnosis.

No model selection, DEV label access, sealed TEST access, or promotion. Compare
per-edge scores, linear frozen-feature readout, and nonlinear frozen-feature
readout. All capacity/loss probes use identical candidate rows and decoder.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file, save_file
from torch import nn
from torch.nn import functional as F

from tools import nanojev_gamer_v1_g2_memorization_diagnosis as d44

b43=d44.b43
g40=d44.g40
g41=d44.g41
g42=d44.g42
x33=d44.x33
base=d44.base
backbone=d44.backbone

SCHEMA='nanojev.gamer.graph.g2.separability-diagnosis.v1'
REPORT='g2_separability_diagnosis_report.json'
LEDGER='g2_separability_diagnosis_per_case.jsonl.gz'
WEIGHTS='g2_separability_diagnosis_development.safetensors'
POLICIES='g2_separability_diagnosis_frozen_policies.json'
SEED=460545
SIMPLE_STEPS=420
HARD_STEPS=420
EVAL_EVERY=35
N_HELDOUT=6
KINDS=('linear','nonlinear')
LOSSES=('bce','margin','structured','bce_margin','bce_structured','original')


def verify_parent44(directory, h40, h41, h42, h43):
    folder=Path(directory)
    report=json.loads((folder/d44.REPORT).read_text(encoding='utf-8'))
    required={
        'schema':d44.SCHEMA,
        'status':'TRAIN_MEMORIZATION_DIAGNOSIS_COMPLETE',
        'source_parent_schema':b43.SCHEMA,
        'parent_hashes':{**h40,**h41,**h42,**h43},
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnosis_only_no_model_selection'},
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'frozen_g2_0033_parent':True,
        'conclusion':'MODEL_HEAD_SINGLE_GRAPH_FIT_NOT_DEMONSTRATED',
    }
    for name,value in required.items():
        if report.get(name)!=value:
            raise ValueError('GAMER_G2_0045_PARENT_0044_CONTRACT:'+name)
    if report['direct_logit_fit'].get('exact') is not True:
        raise ValueError('GAMER_G2_0045_DIRECT_CONTROL_NOT_EXACT')
    if len(report.get('mini_train_prompts',[]))!=d44.N_MULTI or \
       len(report.get('disjoint_train_prompts',[]))!=d44.N_HELDOUT or \
       set(report['mini_train_prompts'])&set(report['disjoint_train_prompts']):
        raise ValueError('GAMER_G2_0045_PARENT_COHORT_CONTRACT')
    if set(report.get('arm_results',{}))!={f'{k}:{p}' for k in d44.KINDS for p in ('single','multi')}:
        raise ValueError('GAMER_G2_0045_PARENT_ARM_SET')
    hashes={}
    for file,key in ((d44.REPORT,'g2_0044_report_sha256'),
                     (d44.LEDGER,'g2_0044_ledger_sha256'),
                     (d44.WEIGHTS,'g2_0044_weights_sha256'),
                     (d44.POLICIES,'g2_0044_policies_sha256')):
        hashes[key]=backbone.sha256(folder/file)
        if file!=d44.REPORT and report.get(key.removeprefix('g2_0044_'))!=hashes[key]:
            raise ValueError('GAMER_G2_0045_PARENT_HASH:'+file)
    if json.loads((folder/d44.POLICIES).read_text(encoding='utf-8'))!=report['train_policy']:
        raise ValueError('GAMER_G2_0045_PARENT_POLICY_DRIFT')
    weights=load_file(str(folder/d44.WEIGHTS),device='cpu')
    expected={}
    for kind in d44.KINDS:
        for phase in ('single','multi'):
            net=b43.ScoreRepair(x33.GraphSetEnergy().eval().requires_grad_(False),kind)
            expected.update({f'{kind}_{phase}.{name}':param
                             for name,param in net.state_dict().items()})
    if set(weights)!=set(expected) or any(
       val.shape!=expected[key].shape or not torch.isfinite(val).all()
       for key,val in weights.items()):
        raise ValueError('GAMER_G2_0045_PARENT_WEIGHTS_INVALID')
    # Ensure the diagnostic ledger corresponds to the report, without reading DEV.
    rows=0
    with gzip.open(folder/d44.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            row=json.loads(raw)
            if row.get('section') not in {
              'train_single','train_multi','unseen_train','train_fit','natural_train_audit'}:
                raise ValueError('GAMER_G2_0045_PARENT_LEDGER_SECTION')
            rows+=1
    if rows!=len(d44.KINDS)*2*(1+d44.N_MULTI+d44.N_HELDOUT+80+80):
        raise ValueError('GAMER_G2_0045_PARENT_LEDGER_SIZE')
    return report,hashes


def select_cases(training, parent44, *,heldout=N_HELDOUT):
    reachable=[r for r in training if r.get('opportunity',{}).get('repairable')]
    if len(reachable)<heldout+2 or any(r['row'].get('split')!='train' for r in reachable):
        raise ValueError('GAMER_G2_0045_TRAIN_REACHABILITY')
    sha={d44._sha(rec):rec for rec in reachable}
    if len(sha)!=len(reachable):
        raise ValueError('GAMER_G2_0045_DUPLICATE_TRAIN_PROMPTS')
    hard_sha=parent44['mini_train_prompts'][0]
    if hard_sha not in sha:
        raise ValueError('GAMER_G2_0045_HARD_PARENT_CASE_MISSING')
    easy=min((r for r in reachable if d44._sha(r)!=hard_sha),
             key=lambda r:(len(d44._gold(r)),len(r['shortlisted']['edges']),d44._sha(r)))
    hard=sha[hard_sha]
    heldout_rows=[r for r in sorted(reachable,key=d44._sha)
                  if d44._sha(r) not in {hard_sha,d44._sha(easy)}][:heldout]
    if len(heldout_rows)!=heldout:
        raise ValueError('GAMER_G2_0045_HELDOUT_COUNT')
    return {'simple':easy,'hard':hard},heldout_rows


def extract_frozen(rec,parent):
    """Return EXACT G2-0033 post-interaction h and its additive prior, once.

    No labels, prompt strings, or candidate edge identities enter h.
    """
    with torch.no_grad():
        edge,groups,index=x33._tensor_features(rec['features'],'cpu')
        embedded=parent.edge_embed(edge)
        pooled=torch.zeros((len(groups),x33.HIDDEN),dtype=embedded.dtype)
        pooled=pooled.index_add(0,index,embedded)
        size=torch.bincount(index,minlength=len(groups)).clamp(min=1)
        pooled=pooled/size[:,None]
        g=parent.group_norm(parent.group_embed(groups)+pooled)
        global_g=g.mean(dim=0)
        h=parent.edge_update(torch.cat((embedded,g[index],global_g.expand_as(embedded)),-1))
        prior=edge[:,1]*10-math.log(.93/.07)
        parent_score=prior+parent.edge_readout(h).squeeze(-1)
        original=torch.as_tensor(rec['structured_pred'][0],dtype=h.dtype)
        if not torch.allclose(parent_score,original,atol=2e-4,rtol=2e-4):
            # Parent G2-0033 must be the same captured model and representation.
            raise ValueError('GAMER_G2_0045_FROZEN_FEATURE_SCORE_MISMATCH')
        return (h.detach().clone(),prior.detach().clone(),
                original.detach().clone())


class Readout(nn.Module):
    def __init__(self,kind,parent):
        super().__init__()
        if kind=='linear':
            self.head=copy.deepcopy(parent.edge_readout).requires_grad_(True)
        elif kind=='nonlinear':
            self.base=copy.deepcopy(parent.edge_readout).requires_grad_(False)
            self.head=nn.Sequential(nn.Linear(x33.HIDDEN,96),nn.GELU(),
                                    nn.Linear(96,1))
            # Begin at precisely the frozen parent score, adding a nonlinear
            # correction. The parent readout is a frozen control, not retrained.
            with torch.no_grad():
                nn.init.zeros_(self.head[-1].weight)
                nn.init.zeros_(self.head[-1].bias)
        else:raise ValueError('GAMER_G2_0045_UNKNOWN_READOUT')
        self.kind=kind

    def forward(self,h,prior):
        if self.kind=='nonlinear':
            return prior+self.base(h).squeeze(-1)+self.head(h).squeeze(-1)
        return prior+self.head(h).squeeze(-1)


def objective(logits,truth,rec,policy,variant,*,count_pred):
    if variant not in LOSSES:
        raise ValueError('GAMER_G2_0045_BAD_LOSS_VARIANT')
    if rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0045_DEV_SUPERVISION_FORBIDDEN')
    edges=rec['shortlisted']['edges']
    gold=d44._gold(rec)
    groupids=torch.as_tensor(rec['features']['index'],dtype=torch.long)
    pos=float(truth.sum().item());neg=float(len(truth)-pos)
    pos_weight=torch.tensor(min(4.,max(1.,math.sqrt(max(1.,neg)/max(1.,pos)))))
    bce=F.binary_cross_entropy_with_logits(logits,truth,pos_weight=pos_weight)
    margins=[]
    for group in range(len(rec['features']['groups'])):
        ids=torch.nonzero(groupids==group).flatten().tolist()
        yes=[i for i in ids if edges[i] in gold]
        no=[i for i in ids if edges[i] not in gold]
        if yes and no:
            margins.append(F.softplus(1.5+logits[no].max()-logits[yes].min()))
    margin=torch.stack(margins).mean() if margins else logits.sum()*0.
    probs=torch.sigmoid(logits)
    group_pred=torch.zeros(len(rec['features']['groups'])).index_add(0,groupids,probs)
    group_gold=torch.zeros_like(group_pred).index_add(0,groupids,truth)
    count_penalty=F.smooth_l1_loss(group_pred,group_gold)
    with torch.no_grad():
        selected=d44._decode(rec,(logits.detach().double().numpy(),
             count_pred[1],count_pred[2]),policy)
    choice=torch.as_tensor([e in selected for e in edges],dtype=truth.dtype)
    diff=(choice-truth).abs().sum().detach()
    structured=logits.sum()*0.
    if float(diff)>0:
        # Parent G2-0033 count and graph potentials cancel between gold and
        # decoded configurations except the group-dependent choice of k.
        group_logits=torch.as_tensor(count_pred[1],dtype=logits.dtype)
        total_log=torch.log1p(torch.tensor(float(count_pred[2]),dtype=logits.dtype).clamp(min=0.))
        a=b43._selection_energy(logits,group_logits,total_log,truth,rec['features'],policy)
        b=b43._selection_energy(logits,group_logits,total_log,choice,rec['features'],policy)
        structured=F.relu(.7*diff+b-a)/diff
    parts={'bce':bce,'margin':margin,'count_penalty':count_penalty,
           'structured':structured}
    if variant=='bce':loss=bce
    elif variant=='margin':loss=margin
    elif variant=='structured':loss=structured
    elif variant=='bce_margin':loss=bce+1.1*margin
    elif variant=='bce_structured':loss=bce+1.5*structured
    else:loss=bce+1.1*margin+.45*count_penalty+1.5*structured
    if not torch.isfinite(loss):
        raise ValueError('GAMER_G2_0045_NONFINITE_OBJECTIVE')
    return loss, {k:float(v.detach()) for k,v in parts.items()}


def score_rows(records,model,caches,policy,*,run_key,step,cohort):
    rows=[]
    for rec in records:
        h,prior,_=caches[d44._sha(rec)]
        with torch.inference_mode():
            scores=model(h,prior).detach().double().numpy()
        selected=d44._decode(rec,(scores,rec['structured_pred'][1],rec['structured_pred'][2]),policy)
        row=d44._quality(rec,selected,scores,section=cohort,kind=run_key,
                          phase='separability',step=step)
        rows.append(row)
    return rows


def fit_case(rec,parent,policy,kind,variant,*,steps,checkpoint_every=EVAL_EVERY):
    if steps<1 or steps>4000 or checkpoint_every<1 or rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0045_TRAIN_CONTRACT')
    if not rec.get('opportunity',{}).get('repairable'):
        raise ValueError('GAMER_G2_0045_UNREACHABLE_TRAIN_CASE')
    torch.manual_seed(SEED+(0 if kind=='linear' else 100))
    model=Readout(kind,parent).train()
    cache=extract_frozen(rec,parent)
    h,prior,_=cache
    truth=torch.as_tensor([e in d44._gold(rec) for e in rec['shortlisted']['edges']],dtype=h.dtype)
    pred=rec['structured_pred']
    opt=torch.optim.Adam([p for p in model.parameters() if p.requires_grad],
                           lr=.015 if kind=='linear' else .008)
    before={k:v.detach().clone() for k,v in parent.state_dict().items()}
    history=[];first_exact=None;best={'exact':False,'missing':10**9,'extra':10**9}
    for step in range(steps+1):
        if step>0:
            model.train();opt.zero_grad(set_to_none=True)
            logits=model(h,prior)
            loss,parts=objective(logits,truth,rec,policy,variant,count_pred=pred)
            loss.backward()
            if any(p.grad is None or not torch.isfinite(p.grad).all()
                   for p in model.parameters() if p.requires_grad):
                raise ValueError('GAMER_G2_0045_GRADIENT_INVALID')
            grad_norm=float(torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad],5.))
            if not math.isfinite(grad_norm):
                raise ValueError('GAMER_G2_0045_BAD_GRADIENT_NORM')
            opt.step()
        else:
            loss=None;parts={};grad_norm=None
        if step==0 or step==1 or step%checkpoint_every==0 or step==steps:
            model.eval()
            row=score_rows([rec],model,{d44._sha(rec):cache},policy,
                           run_key=f'{kind}:{variant}',step=step,cohort='memorized_train')[0]
            history.append({'step':step,'exact':row['exact'],'missing':row['missing'],
                'extra':row['extra'],'inversions':row['inversions'],
                'worst_group_margin':row['worst_group_margin'],
                'loss':None if loss is None else float(loss.detach()),
                'gradient_norm':grad_norm,'loss_components':parts})
            if row['exact'] and first_exact is None:first_exact=step
            if (not best['exact'] and row['exact']) or (
                not best['exact'] and not row['exact'] and
                (row['missing']+row['extra'],row['inversions'])<
                 (best['missing']+best['extra'],best.get('inversions',10**9))):
                best={'exact':row['exact'],'missing':row['missing'],
                      'extra':row['extra'],'inversions':row['inversions'],'step':step}
            if step==1 or step%max(140,checkpoint_every)==0 or step==steps:
                print(f'GAMER_G2_0045_FIT {kind}:{variant} {step}/{steps} '
                      f'exact={int(row["exact"])} missing={row["missing"]} '
                      f'extra={row["extra"]}',flush=True)
            if row['exact'] and len(history)>=2 and history[-2]['exact']:
                break
    if any(not torch.equal(val,parent.state_dict()[name]) for name,val in before.items()):
        raise ValueError('GAMER_G2_0045_PARENT_MUTATED')
    model.eval().requires_grad_(False)
    return model,{'steps_completed':step,'first_exact_checkpoint':first_exact,
                  'ever_exact':first_exact is not None,
                  'memorized':bool(history[-1]['exact']),
                  'history':history,'best_training_checkpoint':best,
                  'train_final':history[-1]}


def direct_control(rec,policy,*,steps=220):
    return d44.direct_score_optimization(rec,policy,steps=steps)


def classify(report):
    if not all(v['exact'] for v in report['direct_controls'].values()):
        return 'DIRECT_LOGIT_CONTROL_NOT_EXACT_IN_BUDGET'
    simple=report['experiments']['simple']
    if any(v['ever_exact'] for k,v in simple.items() if k.startswith('linear:')):
        if any(v['ever_exact'] for k,v in simple.items() if k=='linear:bce'):
            return 'LINEAR_FROZEN_FEATURES_SUFFICIENT_WITH_TESTED_OBJECTIVE'
        return 'LINEAR_FITS_ONLY_WITH_DIFFERENT_OBJECTIVE'
    if any(v['ever_exact'] for k,v in simple.items() if k.startswith('nonlinear:')):
        return 'NONLINEAR_FITS_WHERE_TESTED_LINEAR_DOES_NOT'
    return 'NEITHER_SHARED_HEAD_MEMORIZED_WITHIN_TESTED_BUDGETS'


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,parent44,hashes,
           *,simple_steps=SIMPLE_STEPS,hard_steps=HARD_STEPS):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0045_IMMUTABLE_OUTPUT')
    if not 1<=simple_steps<=4000 or not 1<=hard_steps<=4000:
        raise ValueError('GAMER_G2_0045_STEP_BOUNDS')
    selected,unseen=select_cases(training,parent44)
    cases={'simple':selected['simple'],'hard':selected['hard']}
    selected_shas={d44._sha(r) for r in cases.values()}
    for rec in [*cases.values(),*unseen,*fitting,*auditing]:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0045_DEV_EVALUATION_FORBIDDEN')
    if selected_shas & {d44._sha(r) for r in [*unseen,*fitting,*auditing]}:
        raise ValueError('GAMER_G2_0045_CASE_CONTAMINATION')
    for rec in cases.values():d44.direct_gold_oracle(rec,policy)
    if any(rec['row'].get('split')!='train' for rec in fitting+auditing):
        raise ValueError('GAMER_G2_0045_HOLDOUT_LABEL_FORBIDDEN')
    parent_state={k:v.detach().clone() for k,v in parent.state_dict().items()}
    controls={name:direct_control(rec,policy) for name,rec in cases.items()}
    shared_caches={d44._sha(r):extract_frozen(r,parent)
                   for r in [*cases.values(),*unseen,*fitting,*auditing]}
    results={};ledger=[];weights={}
    for label,rec in cases.items():
        results[label]={}
        for kind in KINDS:
            for variant in LOSSES:
                key=f'{kind}:{variant}'
                model,stats=fit_case(rec,parent,policy,kind,variant,
                    steps=simple_steps if label=='simple' else hard_steps)
                rows=[]
                for cohort,records in [('train_own',[rec]),('train_unseen',unseen),
                                        ('train_fit',fitting),('natural_train_audit',auditing)]:
                    scored=score_rows(records,model,shared_caches,policy,
                        run_key=f'{label}:{key}',step=stats['steps_completed'],cohort=cohort)
                    stats[cohort]=d44._summary(scored)
                    # Keep per-case ledger reasonably compact and complete on the
                    # supervised case and unseen TRAIN, plus independent audits.
                    ledger.extend(scored)
                results[label][key]=stats
                for name,val in model.state_dict().items():
                    weights[f'{label}_{kind}_{variant}.{name}']=val.detach().cpu().contiguous().clone()
                assert_frozen()
    if any(not torch.equal(val,parent.state_dict()[name]) for name,val in parent_state.items()):
        raise ValueError('GAMER_G2_0045_PARENT_MUTATED')
    report={'schema':SCHEMA,'status':'TRAIN_SEPARABILITY_DIAGNOSIS_COMPLETE',
            'source_parent_schema':d44.SCHEMA,
            'parent_hashes':hashes,'training_sha256':parent44['training_sha256'],
            'train_dataset_sha256':parent44['train_dataset_sha256'],
            'case_selection':{label:{'prompt_sha256':d44._sha(rec),
                     'gold_count':len(d44._gold(rec)),
                     'candidate_count':len(rec['shortlisted']['edges'])}
                    for label,rec in cases.items()},
            'unseen_train_prompts':[d44._sha(r) for r in unseen],
            'direct_controls':controls,'experiments':results,
            'protocol':{'architectures':KINDS,'losses':LOSSES,
                        'simple_steps':simple_steps,'hard_steps':hard_steps,
                        'same_frozen_edge_representation':True,
                        'same_frozen_prior_and_count_predictions':True,
                        'test_only_gold_in_loss':True,
                        'never_open_dev':True,'immutable_results':True,
                        'independent_train_audit_measurement_only':True},
            'development_evaluation_skipped':True,
            'dev_labels_used_for_calibration':False,
            'dev_labels_used_for_training':False,
            'sealed_test_used':False,'production_promotion':False,
            'g2_certified':False,'frozen_g1':True,'frozen_g2_0033_parent':True,
            'train_policy':{'enabled':False,'config':None,
                            'disabled_reason':'diagnostic_only_no_selection'}}
    report['conclusion']=classify(report)
    assert_frozen()
    folder.mkdir(parents=True)
    with gzip.open(folder/LEDGER,'wt',encoding='utf-8',compresslevel=6) as f:
        for row in ledger:f.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
    save_file(weights,str(folder/WEIGHTS))
    (folder/POLICIES).write_text(json.dumps(report['train_policy'],indent=2,sort_keys=True)+'\n',
                                 encoding='utf-8')
    report.update({'ledger_sha256':backbone.sha256(folder/LEDGER),
                   'weights_sha256':backbone.sha256(folder/WEIGHTS),
                   'policies_sha256':backbone.sha256(folder/POLICIES)})
    (folder/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',
                               encoding='utf-8')
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,memorization44_run,
        output,simple_steps=SIMPLE_STEPS,hard_steps=HARD_STEPS,**kwargs):
    p40,h40=g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g42.verify_parent41(oracle41_run,h40)
    p42,h42=b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=verify_parent44(memorization44_run,h40,h41,h42,h43)
    if any(p44[key]!=p43[key] for key in ('training_sha256','train_dataset_sha256')):
        raise ValueError('GAMER_G2_0045_TRAIN_HASH_DRIFT')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0045_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,pol33,
                 parent33_models,assert_all_frozen,**ignored):
        for name,rows in [('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)]:
            if p44['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0045_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:
            raise ValueError('GAMER_G2_0045_PARENT_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,
             parent33_models[g40.SOURCE_SEED],assert_all_frozen,output,p44,
             {**h40,**h41,**h42,**h43,**h44},
             simple_steps=simple_steps,hard_steps=hard_steps)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
