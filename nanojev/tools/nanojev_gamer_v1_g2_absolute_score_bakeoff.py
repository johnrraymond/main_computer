"""G2-0043: absolute scores, group-conditioned margins, joint edge/count learning.

Fixed TRAIN-only alternatives on the same G2-0040 occurrence-expanded shortlists.
Independent natural TRAIN audit can veto a TRAIN-fit winner. DEV is exclusively
post-selection evaluation. No labels, gold, or oracle scores enter inference.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_rank_calibration_bakeoff as g42

g41=g42.g41
g40=g42.g40
x33=g42.x33
base=g42.base
backbone=g42.backbone
SCHEMA='nanojev.gamer.graph.g2.absolute-score-bakeoff.v1'
REPORT='g2_absolute_score_bakeoff_report.json'
LEDGER='g2_absolute_score_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_absolute_score_bakeoff_development.safetensors'
POLICIES='g2_absolute_score_bakeoff_frozen_policies.json'
SEED=460543
STEPS=240
LR=6e-4
KINDS=('absolute','group_margin','joint_count')
BIASES=(-1.,0.,1.)


def verify_parent42(directory, hashes40, hashes41):
    folder=Path(directory)
    report=json.loads((folder/g42.REPORT).read_text(encoding='utf-8'))
    checks={'schema':g42.SCHEMA,'status':'TRAIN_SCORE_BAKEOFF_NO_SAFE_WINNER',
            'source_parent_schema':g41.SCHEMA,
            'parent_hashes':{**hashes40,**hashes41},
            'development_evaluation_skipped':True,
            'dev_labels_used_for_training':False,
            'dev_labels_used_for_calibration':False,
            'production_promotion':False,'sealed_test_used':False,
            'frozen_g2_0033_parent':True,'g2_certified':False,
            'by_split':{}}
    for key,val in checks.items():
        if report.get(key)!=val:
            raise ValueError('GAMER_G2_0043_PARENT_0042_CONTRACT:'+key)
    if report.get('train_policy',{}).get('enabled') or report.get('train_policy',{}).get('config') is not None:
        raise ValueError('GAMER_G2_0043_PARENT_POLICY_NOT_DISABLED')
    if len(report.get('train_bakeoff',[]))!=len(g42.candidate_configs()):
        raise ValueError('GAMER_G2_0043_PARENT_CANDIDATE_COUNT_DRIFT')
    hashes={}
    for name,key in ((g42.REPORT,'g2_0042_report_sha256'),
                     (g42.LEDGER,'g2_0042_ledger_sha256'),
                     (g42.POLICIES,'g2_0042_policies_sha256'),
                     (g42.WEIGHTS,'g2_0042_weights_sha256')):
        hashes[key]=backbone.sha256(folder/name)
        if name!=g42.REPORT and report.get(key.removeprefix('g2_0042_'))!=hashes[key]:
            raise ValueError('GAMER_G2_0043_PARENT_HASH:'+name)
    if json.loads((folder/g42.POLICIES).read_text(encoding='utf-8'))!=report['train_policy']:
        raise ValueError('GAMER_G2_0043_PARENT_POLICY_DRIFT')
    # Verify shape and finite contents of the frozen G2-0042 training products.
    weights=load_file(str(folder/g42.WEIGHTS),device='cpu')
    state=x33.GraphSetEnergy().state_dict()
    if set(weights)!={f'{kind}.{name}' for kind in g42.RANK_KINDS for name in state}:
        raise ValueError('GAMER_G2_0043_PARENT_WEIGHTS_KEYS')
    if any(weights[f'{kind}.{key}'].shape!=value.shape or
           not torch.isfinite(weights[f'{kind}.{key}']).all()
           for kind in g42.RANK_KINDS for key,value in state.items()):
        raise ValueError('GAMER_G2_0043_PARENT_WEIGHTS_SHAPES')
    return report,hashes


class ScoreRepair(nn.Module):
    """Frozen G2-0033 representation, with exactly scoped trainable heads.

    absolute: edge scores free to change magnitude and order.
    group_margin: edge scores plus learned group-specific affine margins.
    joint_count: edge scores plus group and total graph-count potentials.
    """
    def __init__(self,parent,kind):
        super().__init__()
        if kind not in KINDS:raise ValueError('GAMER_G2_0043_UNKNOWN_ARM')
        self.kind=kind
        self.core=copy.deepcopy(parent).to('cpu').eval().requires_grad_(False)
        self.core.edge_readout.requires_grad_(True)
        if kind=='joint_count':
            self.core.group_readout.requires_grad_(True)
            self.core.graph_readout.requires_grad_(True)
        if kind=='group_margin':
            self.group_adapter=nn.Sequential(nn.Linear(x33.GROUP_DIM,16),nn.Tanh(),
                                               nn.Linear(16,2))
            nn.init.zeros_(self.group_adapter[-1].weight)
            nn.init.zeros_(self.group_adapter[-1].bias)

    def forward(self,features):
        edge,group,index=x33._tensor_features(features,'cpu')
        logits,group_counts,total_log=self.core(edge,group,index)
        if self.kind=='group_margin':
            adjustment=self.group_adapter(group)
            # Positive temperature is group-conditioned; neither score magnitudes
            # nor selection counts are artificially pinned to the parent.
            scale=torch.exp(torch.clamp(adjustment[:,0],-1.5,1.5))
            logits=logits*scale[index]+adjustment[:,1][index]
        return logits,group_counts,total_log

    def predict(self,features):
        self.eval()
        with torch.inference_mode():
            edge,groups,total_log=self(features)
            result=(edge.detach().double().cpu().numpy(),
                    F.log_softmax(groups,-1).detach().double().cpu().numpy(),
                    float(torch.expm1(total_log.detach()).item()))
        if not all(np.isfinite(v).all() for v in result[:2]) or not math.isfinite(result[2]):
            raise ValueError('GAMER_G2_0043_NONFINITE_INFERENCE')
        return result


def _selection_energy(scores,group_logits,total_log,choice,features,policy):
    ids=torch.as_tensor(features['index'],dtype=torch.long)
    n_groups=len(features['groups'])
    group_sum=torch.zeros(n_groups,dtype=scores.dtype).index_add(0,ids,choice)
    group_counts=group_sum.long().clamp(0,x33.MAX_GROUP_EDGES)
    logcounts=F.log_softmax(group_logits,-1)
    total=choice.sum()
    # Matches the group/graph energy decoded by x33.decode, including its
    # log1p-cardinality potential (target is expm1(total_log), clipped there).
    target=torch.expm1(total_log).clamp(0.,float(x33.source13.MAX_EDGES))
    energy=(choice*scores).sum()+policy['group_weight']*logcounts.gather(
        1,group_counts[:,None]).sum()
    energy=energy-policy['global_weight']*(torch.log1p(total)-torch.log1p(target))**2
    return energy


def train_objective(model,rec,policy):
    if rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0043_DEV_TRAIN_FORBIDDEN')
    if not rec.get('opportunity',{}).get('shortlist_representable'):
        raise ValueError('GAMER_G2_0043_UNREACHABLE_TRAIN_GRAPH')
    edges=rec['shortlisted']['edges']
    gold=base.gold_binding_edges(rec['row'])
    truth=torch.as_tensor([e in gold for e in edges],dtype=torch.float32)
    logits,count_logits,total_log=model(rec['features'])
    pos=float(truth.sum().item())
    neg=float(len(truth)-pos)
    pos_weight=torch.tensor(min(4.,max(1.,math.sqrt(max(1.,neg)/max(1.,pos)))))
    binary=F.binary_cross_entropy_with_logits(logits,truth,pos_weight=pos_weight)
    groupids=np.asarray(rec['features']['index'],dtype=np.int64)
    negatives=[]
    for group in range(len(rec['features']['groups'])):
        idx=np.flatnonzero(groupids==group)
        yes=[i for i in idx if edges[i] in gold]
        no=[i for i in idx if edges[i] not in gold]
        if yes and no:
            # The maximum wrong relationship must score below the weakest
            # correct one: a direct hard-negative, graph-completion margin.
            negatives.append(F.softplus(1.5+torch.max(logits[no])-torch.min(logits[yes])))
    margin=torch.stack(negatives).mean() if negatives else logits.sum()*0.
    probs=torch.sigmoid(logits)
    group_count_pred=torch.zeros(len(rec['features']['groups'])).index_add(
        0,torch.as_tensor(groupids,dtype=torch.long),probs)
    group_count_true=torch.zeros_like(group_count_pred).index_add(
        0,torch.as_tensor(groupids,dtype=torch.long),truth)
    count_penalty=F.smooth_l1_loss(group_count_pred,group_count_true)
    # Contrast the exact gold graph with the *actual model-decoded* TRAIN graph.
    # This is a piecewise-differentiable structured-margin objective, not a
    # gold-informed inference selection rule.
    with torch.no_grad():
        decoded=x33.decode(rec['shortlisted'],rec['features'],(
            logits.detach().double().numpy(),
            F.log_softmax(count_logits,-1).detach().double().numpy(),
            float(torch.expm1(total_log.detach()).item())),policy)
    choice=torch.as_tensor([e in decoded for e in edges],dtype=truth.dtype)
    diff=(choice-truth).abs().sum().detach()
    structured=logits.sum()*0.
    if float(diff)>0:
        truth_energy=_selection_energy(logits,count_logits,total_log,truth,rec['features'],policy)
        choice_energy=_selection_energy(logits,count_logits,total_log,choice,rec['features'],policy)
        structured=F.relu(.7*diff+choice_energy-truth_energy)/diff
    loss=binary+1.1*margin+0.45*count_penalty+1.5*structured
    extra={}
    if model.kind=='group_margin':
        # Learn the number of edges admitted in each source/relation group.
        loss=loss+0.5*count_penalty
    if model.kind=='joint_count':
        count_target=group_count_true.long().clamp(max=x33.MAX_GROUP_EDGES)
        group_ce=F.cross_entropy(count_logits,count_target)
        total_target=torch.log1p(truth.sum())
        graph_l1=F.smooth_l1_loss(total_log,total_target)
        loss=loss+0.85*group_ce+0.35*graph_l1
        extra={'count_ce':float(group_ce.detach()),'graph_l1':float(graph_l1.detach())}
    if not torch.isfinite(loss):raise ValueError('GAMER_G2_0043_NONFINITE_LOSS')
    return loss,{'loss':float(loss.detach()),'bce':float(binary.detach()),
                 'margin':float(margin.detach()),'structured':float(structured.detach()),
                 **extra}


def train_arm(training,parent,kind,*,steps=STEPS,seed=SEED):
    if kind not in KINDS or not 1<=steps<=STEPS:
        raise ValueError('GAMER_G2_0043_BAD_TRAIN_CONFIG')
    reachable=[r for r in training if r.get('opportunity',{}).get('repairable')]
    controls=[r for r in training if not r['admitted'] and
              r.get('opportunity',{}).get('shortlist_representable')]
    if len(reachable)<8 or len(controls)<8:
        raise ValueError('GAMER_G2_0043_INSUFFICIENT_TRAIN_OPPORTUNITIES')
    before={k:v.detach().clone() for k,v in parent.state_dict().items()}
    torch.manual_seed(seed+KINDS.index(kind))
    model=ScoreRepair(parent,kind).train()
    params=[p for p in model.parameters() if p.requires_grad]
    opt=torch.optim.AdamW(params,lr=LR,weight_decay=1e-5)
    rng=random.Random(seed+31*KINDS.index(kind))
    last={}
    for step in range(1,steps+1):
        opt.zero_grad(set_to_none=True)
        sample=(reachable[rng.randrange(len(reachable))],
                controls[rng.randrange(len(controls))])
        losses=[]
        for rec in sample:
            loss,details=train_objective(model,rec,policy=rec['structured_policy'])
            losses.append(loss)
        batch_loss=torch.stack(losses).mean()
        batch_loss.backward()
        torch.nn.utils.clip_grad_norm_(params,3.)
        opt.step()
        last={'step':step,'loss':float(batch_loss.detach()),
              'last_case':details}
        if step==1 or step%60==0 or step==steps:
            print(f'GAMER_G2_0043_TRAIN {kind} {step}/{steps} '
                  f'loss={last["loss"]:.5f}',flush=True)
    if any(not torch.equal(parent.state_dict()[k],value) for k,value in before.items()):
        raise ValueError('GAMER_G2_0043_PARENT_MUTATED')
    for key,val in model.core.state_dict().items():
        is_trainable=(key.startswith('edge_readout.') or
                      kind=='joint_count' and (key.startswith('group_readout.') or
                                               key.startswith('graph_readout.')))
        if not is_trainable and not torch.equal(val,before[key]):
            raise ValueError('GAMER_G2_0043_UNAUTHORIZED_WEIGHT_CHANGE:'+key)
    model.eval().requires_grad_(False)
    return model,last


def candidate_configs():
    return [{'kind':kind,'bias':bias} for kind in KINDS for bias in BIASES]


def predict(rec,cfg,models):
    edge,count,total=models[cfg['kind']].predict(rec['features'])
    return edge+cfg['bias'],count,total


def choose(rec,cfg,models,policy):
    if not rec['admitted']:
        return g40.baseline_selection(rec,'constrained')
    result=predict(rec,cfg,models)
    return x33.decode(rec['shortlisted'],rec['features'],result,policy)


def measure(records,cfg,models,policy,*,allow_dev=False):
    aggregate=Counter();baseline=Counter();ledger=[]
    for rec in records:
        row=rec['row']
        if row.get('split')!='train' and not allow_dev:
            raise ValueError('GAMER_G2_0043_DEV_TRAIN_METRICS_FORBIDDEN')
        gold=base.gold_binding_edges(row)
        parent=g40.baseline_selection(rec,'constrained')
        challenger=choose(rec,cfg,models,policy)
        new=challenger==gold;old=parent==gold
        missing=gold-challenger;false=challenger-gold
        missing_old=gold-parent;false_old=parent-gold
        aggregate.update({'correct':len(challenger&gold),'missing':len(missing),
                          'false_positive':len(false),'exact':int(new)})
        baseline.update({'correct':len(parent&gold),'missing':len(missing_old),
                         'false_positive':len(false_old),'exact':int(old)})
        inversion=lambda scores:sum(float(scores[j])>=float(scores[i])
            for g in range(len(rec['features']['groups']))
            for i in np.flatnonzero(rec['features']['index']==g)
            if rec['shortlisted']['edges'][i] in gold
            for j in np.flatnonzero(rec['features']['index']==g)
            if rec['shortlisted']['edges'][j] not in gold)
        if rec['admitted']:
            aggregate['rank_inversions']+=inversion(predict(rec,cfg,models)[0])
            baseline['rank_inversions']+=inversion(rec['structured_pred'][0])
        ledger.append({'prompt_sha256':base._sha(row['prompt']),
             'admitted':bool(rec['admitted']),
             'reachable':bool(rec.get('opportunity',{}).get('shortlist_representable',False)),
             'parent_exact':bool(old),'exact':bool(new),
             'new_exact':bool(new and not old),'lost_exact':bool(old and not new),
             'missing':len(missing),'false_positive':len(false)})
    f1=lambda z:2*z['correct']/max(1.,2*z['correct']+z['missing']+z['false_positive'])
    return {'cases':len(records),'f1':f1(aggregate),'parent_f1':f1(baseline),
        'exact':aggregate['exact'],'parent_exact':baseline['exact'],
        'new_exact':sum(c['new_exact'] for c in ledger),
        'lost_exact':sum(c['lost_exact'] for c in ledger),
        'new_exact_reachable':sum(c['new_exact'] and c['reachable'] for c in ledger),
        'reachable_expanded':sum(c['admitted'] and c['reachable'] for c in ledger),
        'fp':aggregate['false_positive'],'parent_fp':baseline['false_positive'],
        'rank_inversions':aggregate['rank_inversions'],
        'parent_rank_inversions':baseline['rank_inversions']},ledger


def select(fit,audit,models,policy):
    ledger=[];candidates=[]
    for i,cfg in enumerate(candidate_configs()):
        stat,case_rows=measure(fit,cfg,models,policy)
        eligible=g42.admissible(stat,require_gain=True)
        item={'config':cfg,'fit':stat,'eligible_fit':eligible,
              'audit':None,'eligible_audit':False,
              '_fit_cases':case_rows}
        ledger.append(item)
        if eligible:
            candidates.append(((stat['new_exact'],stat['f1']-stat['parent_f1'],
                                -stat['fp'],-i),item))
    if not candidates:
        return {'enabled':False,'config':None,'disabled_reason':'no_safe_train_fit_winner'},ledger
    winner=max(candidates,key=lambda x:x[0])[1]
    metrics,audit_rows=measure(audit,winner['config'],models,policy)
    winner['_audit_cases']=audit_rows
    winner['audit']=metrics
    winner['eligible_audit']=g42.admissible(metrics,require_gain=False)
    if not winner['eligible_audit']:
        return {'enabled':False,'config':None,'disabled_reason':'winner_vetoed_by_natural_train_audit'},ledger
    return {'enabled':True,'config':winner['config']},ledger


def persist(output,report,ledger,models,policy):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0043_IMMUTABLE_OUTPUT')
    folder.mkdir(parents=True)
    with gzip.open(folder/LEDGER,'wt',encoding='utf-8',compresslevel=6) as fh:
        for row in ledger:fh.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
    save_file({f'{kind}.{key}':val.detach().clone().contiguous()
        for kind,net in models.items() for key,val in net.state_dict().items()},
        str(folder/WEIGHTS))
    (folder/POLICIES).write_text(json.dumps(policy,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    report.update({'ledger_sha256':backbone.sha256(folder/LEDGER),
                   'weights_sha256':backbone.sha256(folder/WEIGHTS),
                   'policies_sha256':backbone.sha256(folder/POLICIES)})
    (folder/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,
                                   allow_nan=False)+'\n',encoding='utf-8')
    return report


def stage2(training,fitting,auditing,policy,parent,dev_capture,assert_frozen,
           output,parent42,hashes):
    orig={k:v.detach().clone() for k,v in parent.state_dict().items()}
    for rec in training:rec['structured_policy']=policy
    models={};losses={}
    for kind in KINDS:
        models[kind],losses[kind]=train_arm(training,parent,kind)
    if any(not torch.equal(parent.state_dict()[k],v) for k,v in orig.items()):
        raise ValueError('GAMER_G2_0043_FROZEN_PARENT_MUTATED')
    assert_frozen()
    winner,comparison=select(fitting,auditing,models,policy)
    print('GAMER_G2_0043_SELECT '+json.dumps(winner,sort_keys=True),flush=True)
    ledger=[]
    for item in comparison:
        for ordinal,row in enumerate(item.pop('_fit_cases',[]),1):
            ledger.append({'section':'train_fit','config':item['config'],
                           'index':ordinal,**row})
        for ordinal,row in enumerate(item.pop('_audit_cases',[]),1):
            ledger.append({'section':'natural_train_audit','config':item['config'],
                           'index':ordinal,**row})
    report={'schema':SCHEMA,'status':'TRAIN_SCORE_BAKEOFF_NO_SAFE_WINNER',
        'parent_hashes':hashes,'source_parent_schema':g42.SCHEMA,
        'training_sha256':parent42['training_sha256'],
        'train_dataset_sha256':parent42['train_dataset_sha256'],
        'opportunity_frontier':parent42['opportunity_frontier'],
        'train_bakeoff':comparison,'train_policy':winner,'training_losses':losses,
        'by_split':{},'development_gates':{'passed':False,'reason':'no_train_winner'},
        'development_evaluation_skipped':not winner['enabled'],
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'production_promotion':False,'sealed_test_used':False,'g2_certified':False,
        'frozen_g1':True,'frozen_g2_0031_parent':True,'frozen_g2_0032_parent':True,
        'frozen_g2_0033_parent':True,'frozen_g2_0038_acceptor':True,'frozen_g2_0039_scorer':True,
        'protocol':{'source_seed':g40.SOURCE_SEED,'train_steps':STEPS,
            'learning_arms':KINDS,'bias_grid':BIASES,
            'independent_train_audit_can_only_veto':True,'no_dev_policy_selection':True,
            'train_fit_and_audit_disjoint':True,'unchanged_graphs_preserved':True,
            'train_labels_for_supervision_only':True,
            'g2_0033_parent_backbone_frozen':True,
            'joint_count_arm_trains_only_edge_group_graph_readouts':True}}
    if winner['enabled']:
        assert_frozen()
        data=dev_capture()
        summaries={}
        for split,records in data.items():
            result,rows=measure(records,winner['config'],models,policy,allow_dev=True)
            summaries[split]=result
            ledger.extend({'section':'development','split':split,'index':i,**row}
                          for i,row in enumerate(rows,1))
        report['by_split']=summaries
        independent=summaries['new_independent_dev_0040']
        topology=summaries['new_topology_dev_0040']
        replay=[v for k,v in summaries.items() if k not in (
            'new_independent_dev_0040','new_topology_dev_0040')]
        passed=(g42.admissible(independent,require_gain=True) and
                g42.admissible(topology,require_gain=False) and
                all(g42.admissible(r,require_gain=False) for r in replay))
        report['development_gates']={'passed':passed,
            'independent_new_exact':independent['new_exact'],
            'independent_lost_exact':independent['lost_exact'],
            'topology_new_exact':topology['new_exact'],
            'topology_lost_exact':topology['lost_exact'],
            'replay_lost_exact':sum(r['lost_exact'] for r in replay)}
        report['status']=('SCORE_BAKEOFF_DEV_GATES_PASSED_UNPROMOTED' if passed
                          else 'SCORE_BAKEOFF_DEV_NOT_CERTIFIED')
    assert_frozen()
    return persist(output,report,ledger,models,winner)


def run(*,bakeoff40_run,oracle41_run,rank42_run,output,**kwargs):
    parent40,hash40=g41.verify_parent40(bakeoff40_run)
    parent41,_,hash41=g42.verify_parent41(oracle41_run,hash40)
    parent42,hash42=verify_parent42(rank42_run,hash40,hash41)
    if parent42['training_sha256']!=parent41['training_sha256']:
        raise ValueError('GAMER_G2_0043_PARENT_COHORT_DRIFT')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0043_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,pol33,
                 parent33_models,post_selection_dev_capture,assert_all_frozen,**ignored):
        for name,rows in (('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)):
            if parent42['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0043_TRAIN_COHORT_HASH:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=parent41['protocol']['config']:
            raise ValueError('GAMER_G2_0043_PARENT_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,parent33_models[g40.SOURCE_SEED],
                      post_selection_dev_capture,assert_all_frozen,output,parent42,
                      {**hash40,**hash41,**hash42})
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
