"""G2-0044: TRAIN-only exact-graph memorization and learning-machinery diagnosis.

This is not a model-selection or promotion experiment. Gold is confined to TRAIN
supervision/measurement. G2-0043, its parents and the frozen backbone remain
unchanged. DEV and sealed test are never captured.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_absolute_score_bakeoff as b43

g42=b43.g42
g41=b43.g41
g40=b43.g40
x33=b43.x33
base=b43.base
backbone=b43.backbone

SCHEMA='nanojev.gamer.graph.g2.train-memorization-diagnosis.v1'
REPORT='g2_memorization_diagnosis_report.json'
LEDGER='g2_memorization_diagnosis_per_case.jsonl.gz'
WEIGHTS='g2_memorization_diagnosis_development.safetensors'
POLICIES='g2_memorization_diagnosis_frozen_policies.json'
SEED=460544
SINGLE_STEPS=480
MULTI_STEPS=840
N_MULTI=6
N_HELDOUT=6
EVAL_EVERY=40
DIRECT_STEPS=220
KINDS=b43.KINDS


def verify_parent43(directory, hashes40, hashes41, hashes42):
    folder=Path(directory)
    report=json.loads((folder/b43.REPORT).read_text(encoding='utf-8'))
    required={
        'schema':b43.SCHEMA,
        'status':'TRAIN_SCORE_BAKEOFF_NO_SAFE_WINNER',
        'source_parent_schema':g42.SCHEMA,
        'parent_hashes':{**hashes40,**hashes41,**hashes42},
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'development_evaluation_skipped':True,
        'development_gates':{'passed':False,'reason':'no_train_winner'},
        'by_split':{},'production_promotion':False,
        'sealed_test_used':False,'g2_certified':False,
        'frozen_g2_0033_parent':True,
    }
    for key,expected in required.items():
        if report.get(key)!=expected:
            raise ValueError('GAMER_G2_0044_PARENT_0043_CONTRACT:'+key)
    if report.get('train_policy')!={'enabled':False,'config':None,
                                     'disabled_reason':'no_safe_train_fit_winner'}:
        raise ValueError('GAMER_G2_0044_PARENT_POLICY_NOT_DISABLED')
    if len(report.get('train_bakeoff',[]))!=len(b43.candidate_configs()):
        raise ValueError('GAMER_G2_0044_PARENT_CANDIDATE_COUNT_DRIFT')
    hashes={}
    for name,key in ((b43.REPORT,'g2_0043_report_sha256'),
                     (b43.LEDGER,'g2_0043_ledger_sha256'),
                     (b43.WEIGHTS,'g2_0043_weights_sha256'),
                     (b43.POLICIES,'g2_0043_policies_sha256')):
        actual=backbone.sha256(folder/name)
        hashes[key]=actual
        short=key.removeprefix('g2_0043_')
        if name!=b43.REPORT and report.get(short)!=actual:
            raise ValueError('GAMER_G2_0044_PARENT_HASH:'+name)
    policy=json.loads((folder/b43.POLICIES).read_text(encoding='utf-8'))
    if policy!=report['train_policy']:
        raise ValueError('GAMER_G2_0044_PARENT_POLICY_DRIFT')
    weights=load_file(str(folder/b43.WEIGHTS),device='cpu')
    expected={}
    for kind in KINDS:
        net=b43.ScoreRepair(x33.GraphSetEnergy().eval().requires_grad_(False),kind)
        for name,weight in net.state_dict().items():
            expected[f'{kind}.{name}']=weight
    if set(weights)!=set(expected) or any(
        weights[key].shape!=value.shape or not torch.isfinite(weights[key]).all()
        for key,value in expected.items()):
        raise ValueError('GAMER_G2_0044_PARENT_WEIGHTS_SHAPE')
    # Every candidate must have a complete TRAIN-fit ledger. A missing ledger
    # would conceal losses behind aggregate metrics.
    count=0
    with gzip.open(folder/b43.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            row=json.loads(raw)
            if row.get('section')!='train_fit':
                raise ValueError('GAMER_G2_0044_PARENT_LEDGER_SECTION')
            if row.get('config')!=b43.candidate_configs()[count//80] or row.get('index')!=count%80+1:
                raise ValueError('GAMER_G2_0044_PARENT_LEDGER_ORDER')
            count+=1
    if count!=80*len(b43.candidate_configs()):
        raise ValueError('GAMER_G2_0044_PARENT_LEDGER_SIZE')
    return report,hashes


def _gold(rec):
    if rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0044_NONTRAIN_GOLD_FORBIDDEN')
    return base.gold_binding_edges(rec['row'])


def _sha(rec):
    return base._sha(rec['row']['prompt'])


def choose_cases(training, *, multi=N_MULTI, heldout=N_HELDOUT):
    """Deterministically choose reachable positives; hold out different prompts.

    Selection uses only already-computed TRAIN feasibility and prompt hashes.
    It does not optimize the set of examples for the later probe outcome.
    """
    if not 2<=multi<=12 or heldout<1:
        raise ValueError('GAMER_G2_0044_BAD_MINI_COHORT_SIZE')
    reachable=[r for r in training if r.get('opportunity',{}).get('repairable')]
    if len(reachable)<multi+heldout:
        raise ValueError('GAMER_G2_0044_INSUFFICIENT_REACHABLE_TRAIN')
    if any(r['row'].get('split')!='train' or not r.get('admitted') or
           not r['opportunity'].get('shortlist_representable') for r in reachable):
        raise ValueError('GAMER_G2_0044_INVALID_TRAIN_OPPORTUNITY')
    ordered=sorted(reachable,key=_sha)
    identifiers=[_sha(r) for r in ordered]
    if len(identifiers)!=len(set(identifiers)):
        raise ValueError('GAMER_G2_0044_DUPLICATE_TRAIN_PROMPT')
    return ordered[:multi],ordered[multi:multi+heldout]


def _decode(rec, pred, policy):
    return x33.decode(rec['shortlisted'],rec['features'],pred,policy)


def direct_gold_oracle(rec,policy):
    """Strict ceiling control: ±24 gold edge scores, frozen cardinality heads."""
    gold=_gold(rec)
    original=rec['structured_pred']
    edges=rec['shortlisted']['edges']
    if not gold<=set(edges):
        raise ValueError('GAMER_G2_0044_UNREACHABLE_ORACLE_CONTROL')
    scores=np.asarray([24. if e in gold else -24. for e in edges],dtype=np.float64)
    selected=_decode(rec,(scores,original[1],original[2]),policy)
    if selected!=gold:
        raise ValueError('GAMER_G2_0044_GOLD_ORACLE_DECODER_ALIGNMENT')
    return True


def _quality(rec,selected,scores,*,section,kind,phase,step):
    gold=_gold(rec)
    edges=rec['shortlisted']['edges']
    scores=np.asarray(scores,dtype=np.float64)
    ids=np.asarray(rec['features']['index'],dtype=np.int64)
    inversions=0;worst_margin=None
    for group in range(len(rec['features']['groups'])):
        positions=np.flatnonzero(ids==group)
        positives=[int(i) for i in positions if edges[i] in gold]
        negatives=[int(i) for i in positions if edges[i] not in gold]
        if positives and negatives:
            inversions+=sum(float(scores[n])>=float(scores[p])
                            for p in positives for n in negatives)
            margin=min(float(scores[p]) for p in positives)-max(float(scores[n]) for n in negatives)
            worst_margin=margin if worst_margin is None else min(margin,worst_margin)
    missed=gold-selected;extra=selected-gold
    return {'section':section,'arm':kind,'phase':phase,'step':step,'prompt_sha256':_sha(rec),
        'family':rec['row'].get('family'),'gold_count':len(gold),
        'selected_count':len(selected),'missing':len(missed),'extra':len(extra),
        'exact':selected==gold,'inversions':inversions,
        'worst_group_margin':worst_margin,
        'missing_edges':[repr(v) for v in sorted(missed,key=repr)][:12],
        'extra_edges':[repr(v) for v in sorted(extra,key=repr)][:12]}


def _evaluate_model(records,model,policy,*,section,kind,phase,step):
    rows=[]
    for rec in records:
        pred=model.predict(rec['features'])
        selected=_decode(rec,pred,policy)
        rows.append(_quality(rec,selected,pred[0],section=section,
                             kind=kind,phase=phase,step=step))
    return rows


def _summary(rows):
    return {'cases':len(rows),'exact':sum(r['exact'] for r in rows),
            'missing':sum(r['missing'] for r in rows),
            'extra':sum(r['extra'] for r in rows),
            'rank_inversions':sum(r['inversions'] for r in rows),
            'count_error_l1':sum(abs(r['gold_count']-r['selected_count']) for r in rows)}




def frozen_edge_identifiability(records,parent):
    """Exact positive/negative readout-feature collisions under frozen encoding.

    A collision requires the SAME frozen h *and* the SAME additive prior score.
    A linear edge readout then cannot assign different logits to the two edges,
    irrespective of training steps. Absence of collisions is not a capacity
    guarantee.
    """
    seen={};total=0
    with torch.inference_mode():
        for rec in records:
            gold=_gold(rec)
            edges,groups,index=x33._tensor_features(rec['features'],'cpu')
            e=parent.edge_embed(edges)
            pooled=torch.zeros((len(groups),x33.HIDDEN),dtype=e.dtype).index_add(0,index,e)
            size=torch.bincount(index,minlength=len(groups)).clamp(min=1)
            pooled=pooled/size[:,None]
            g=parent.group_norm(parent.group_embed(groups)+pooled)
            global_g=g.mean(dim=0)
            h=parent.edge_update(torch.cat((e,g[index],global_g.expand_as(e)),-1))
            priors=edges[:,1]*10-math.log(.93/.07)
            for i,edge in enumerate(rec['shortlisted']['edges']):
                key=hashlib.sha256(h[i].contiguous().numpy().tobytes()+
                    priors[i].contiguous().numpy().tobytes()).hexdigest()
                seen.setdefault(key,set()).add(edge in gold)
                total+=1
    contradictions=sum(len(labels)>1 for labels in seen.values())
    return {'edges':total,'distinct_frozen_edge_signatures':len(seen),
            'conflicting_gold_label_signatures':contradictions,
            'exact_collision_proves_edge_readout_insufficient':contradictions>0}

def direct_score_optimization(rec,policy,*,steps=DIRECT_STEPS):
    """Can independently adjustable logits fit a reachable TRAIN graph?

    This oracle-parametric fit does NOT test the representation or G2 model.
    It does test whether logits and the existing decoder can be optimized to a
    correct set under the frozen cardinality heads using a simple BCE loss.
    """
    if not 1<=steps<=3000:raise ValueError('GAMER_G2_0044_DIRECT_STEP_BOUNDS')
    gold=_gold(rec)
    truth=torch.as_tensor([e in gold for e in rec['shortlisted']['edges']],dtype=torch.float32)
    param=torch.nn.Parameter(torch.as_tensor(rec['structured_pred'][0],dtype=torch.float32).clone())
    opt=torch.optim.Adam([param],lr=.55)
    first_exact=None;last_loss=None
    for step in range(steps+1):
        result=_decode(rec,(param.detach().double().numpy(),rec['structured_pred'][1],
                            rec['structured_pred'][2]),policy)
        if result==gold:
            first_exact=step
            break
        if step==steps:break
        opt.zero_grad(set_to_none=True)
        loss=F.binary_cross_entropy_with_logits(param,truth)
        loss.backward()
        if not torch.isfinite(loss) or param.grad is None or not torch.isfinite(param.grad).all():
            raise ValueError('GAMER_G2_0044_DIRECT_GRADIENT_INVALID')
        opt.step();last_loss=float(loss.detach())
    return {'exact':first_exact is not None,'first_exact_step':first_exact,
            'steps_cap':steps,'last_bce':last_loss}


def _probe_gradient(model,rec,policy):
    model.train()
    model.zero_grad(set_to_none=True)
    loss,parts=b43.train_objective(model,rec,policy)
    loss.backward()
    gradients={}
    for name,param in model.named_parameters():
        if not param.requires_grad:continue
        if param.grad is None:
            gradients[name]={'exists':False,'norm':None,'finite':False}
        else:
            gradients[name]={'exists':True,'norm':float(param.grad.norm()),
                             'finite':bool(torch.isfinite(param.grad).all())}
    if not gradients or any(not g['exists'] or not g['finite'] for g in gradients.values()):
        raise ValueError('GAMER_G2_0044_TRAINABLE_GRADIENT_FAILURE')
    if not any(g['norm']>1e-10 for g in gradients.values()):
        raise ValueError('GAMER_G2_0044_ZERO_GRADIENT')
    model.zero_grad(set_to_none=True)
    return {'loss':parts,'gradients':gradients}


def train_memorization(records,parent,kind,policy,*,steps,phase,checkpoint_every=EVAL_EVERY):
    """Reset parent every run; cycle every fixed TRAIN example, no random draw.

    The model must attain exact reconstruction by its actual production decoder.
    Reporting a low loss or perfect edge classification is insufficient.
    """
    if kind not in KINDS or not 1<=steps<=3000 or not records:
        raise ValueError('GAMER_G2_0044_BAD_TRAIN_PROTOCOL')
    if any(r['row'].get('split')!='train' or not r['opportunity'].get('repairable')
           for r in records):
        raise ValueError('GAMER_G2_0044_BAD_TRAIN_SPLIT')
    torch.manual_seed(SEED+KINDS.index(kind))
    model=b43.ScoreRepair(parent,kind).train()
    frozen={k:v.detach().clone() for k,v in parent.state_dict().items()}
    probe=_probe_gradient(model,records[0],policy)
    params=[p for p in model.parameters() if p.requires_grad]
    opt=torch.optim.AdamW(params,lr=b43.LR,weight_decay=0.)
    checkpoints=[];first_full=None
    checkpoints.append({'step':0,'training':_summary(_evaluate_model(records,model,policy,
        section='train_memorization',kind=kind,phase=phase,step=0)),'loss':None})
    for step in range(1,steps+1):
        model.train()
        opt.zero_grad(set_to_none=True)
        # One pass over every case per optimization step; a rare difficult
        # example cannot disappear due to stochastic subsampling.
        losses=[];parts=[]
        for rec in records:
            loss,detail=b43.train_objective(model,rec,policy)
            losses.append(loss);parts.append(detail)
        average=torch.stack(losses).mean()
        if not torch.isfinite(average):raise ValueError('GAMER_G2_0044_NONFINITE_TRAIN_LOSS')
        average.backward()
        grad_norm=float(torch.nn.utils.clip_grad_norm_(params,3.))
        if not math.isfinite(grad_norm) or grad_norm<=0:
            raise ValueError('GAMER_G2_0044_BAD_STEP_GRADIENT')
        opt.step()
        if step==1 or step%checkpoint_every==0 or step==steps:
            model.eval()
            scored=_summary(_evaluate_model(records,model,policy,
                section='train_memorization',kind=kind,phase=phase,step=step))
            checkpoints.append({'step':step,'training':scored,'loss':float(average.detach()),
                                'gradient_norm':grad_norm,
                                'loss_components':{key:float(np.mean([p[key] for p in parts]))
                                  for key in parts[0]}})
            if scored['exact']==len(records) and first_full is None:
                first_full=step
            if step==1 or step%max(100,checkpoint_every)==0 or step==steps:
                print(f'GAMER_G2_0044_{phase.upper()} {kind} {step}/{steps} '
                      f'exact={scored["exact"]}/{len(records)} '
                      f'loss={float(average.detach()):.5f}',flush=True)
            # Require a repeat pass at the next checkpoint before early stop.
            if len(checkpoints)>1 and checkpoints[-1]['training']['exact']==len(records) and \
               checkpoints[-2]['training']['exact']==len(records):
                break
    if any(not torch.equal(value,parent.state_dict()[key]) for key,value in frozen.items()):
        raise ValueError('GAMER_G2_0044_PARENT_MUTATED')
    for key,value in model.core.state_dict().items():
        authorized=(key.startswith('edge_readout.') or kind=='joint_count' and
                    (key.startswith('group_readout.') or key.startswith('graph_readout.')))
        if not authorized and not torch.equal(value,frozen[key]):
            raise ValueError('GAMER_G2_0044_UNAUTHORIZED_HEAD_CHANGE:'+key)
    deltas={name:float((value.detach()-frozen[name]).norm())
            for name,value in model.core.state_dict().items()
            if name in frozen and name.startswith(('edge_readout.','group_readout.','graph_readout.'))}
    model.eval().requires_grad_(False)
    return model,{'phase':phase,'arm':kind,'examples':len(records),
                  'steps_completed':step,'steps_cap':steps,'first_exact_checkpoint':first_full,
                  'gradient_probe':probe,'readout_parameter_deltas':deltas,'checkpoints':checkpoints,
                  'memorized':checkpoints[-1]['training']['exact']==len(records)}


def _record_parent(rec,policy,section):
    gold=_gold(rec)
    selected=g40.baseline_selection(rec,'constrained')
    oracle=direct_gold_oracle(rec,policy)
    return {'prompt_sha256':_sha(rec),'family':rec['row'].get('family'),
            'parent_exact':selected==gold,'oracle_exact':oracle,
            'parent_missing':len(gold-selected),'parent_extra':len(selected-gold),
            'gold_count':len(gold),'section':section}


def interpret(results,direct):
    """Report evidence, not an unearned categorical diagnosis."""
    single=[v for k,v in results.items() if k.endswith(':single')]
    multi=[v for k,v in results.items() if k.endswith(':multi')]
    if not direct['exact']:
        return 'DIRECT_LOGIT_OPTIMIZATION_DID_NOT_REACH_EXACT_AT_STEP_BUDGET'
    if not any(r['memorized'] for r in single):
        return 'MODEL_HEAD_SINGLE_GRAPH_FIT_NOT_DEMONSTRATED'
    if not any(r['memorized'] for r in multi):
        return 'SINGLE_GRAPH_FIT_WITH_MULTI_GRAPH_INTERFERENCE_OR_CAPACITY_LIMIT'
    return 'TRAIN_MEMORIZATION_DEMONSTRATED_CHECK_DISJOINT_GENERALIZATION'


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,
           report43,hashes,*,single_steps=SINGLE_STEPS,multi_steps=MULTI_STEPS):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0044_IMMUTABLE_OUTPUT')
    if not 1<=single_steps<=3000 or not 1<=multi_steps<=3000:
        raise ValueError('GAMER_G2_0044_INVALID_STEP_BUDGET')
    mini,heldout=choose_cases(training)
    for rec in (*mini,*heldout,*fitting,*auditing):
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0044_EVALUATION_NONTRAIN_SPLIT')
    if set(map(_sha,mini))&set(map(_sha,heldout)) or \
       set(map(_sha,mini))&set(map(_sha,fitting)) or \
       set(map(_sha,mini))&set(map(_sha,auditing)):
        raise ValueError('GAMER_G2_0044_COHORT_CONTAMINATION')
    before={k:v.detach().clone() for k,v in parent.state_dict().items()}
    baseline={name:[_record_parent(r,policy,name) for r in rows]
              for name,rows in [('train_single',mini[:1]),('train_multi',mini),
                                ('unseen_train',heldout)]}
    identifiability=frozen_edge_identifiability(mini,parent)
    independent=direct_score_optimization(mini[0],policy)
    results={};ledger=[];weights={}
    for kind in KINDS:
        for phase,records,cap in [('single',mini[:1],single_steps),
                                  ('multi',mini,multi_steps)]:
            model,stats=train_memorization(records,parent,kind,policy,steps=cap,phase=phase)
            key=f'{kind}:{phase}'
            results[key]=stats
            for name,rows in [('train_single',mini[:1]),('train_multi',mini),
                              ('unseen_train',heldout),('train_fit',fitting),
                              ('natural_train_audit',auditing)]:
                # No use of fit/audit examples for training or choosing a winner.
                scored=_evaluate_model(rows,model,policy,section=name,
                    kind=kind,phase=phase,step=stats['steps_completed'])
                stats[name]=_summary(scored)
                ledger.extend(scored)
            for name,value in model.state_dict().items():
                weights[f'{kind}_{phase}.{name}']=value.detach().cpu().contiguous().clone()
            assert_frozen()
    if any(not torch.equal(value,parent.state_dict()[key]) for key,value in before.items()):
        raise ValueError('GAMER_G2_0044_FROZEN_PARENT_MUTATED')
    report={'schema':SCHEMA,'status':'TRAIN_MEMORIZATION_DIAGNOSIS_COMPLETE',
        'conclusion':interpret(results,independent),
        'parent_hashes':hashes,'source_parent_schema':b43.SCHEMA,
        'training_sha256':report43['training_sha256'],
        'train_dataset_sha256':report43['train_dataset_sha256'],
        'mini_train_prompts':list(map(_sha,mini)),
        'disjoint_train_prompts':list(map(_sha,heldout)),
        'direct_logit_fit':independent,'frozen_edge_identifiability':identifiability,
        'parent_controls':baseline,
        'arm_results':results,
        'development_evaluation_skipped':True,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'frozen_g1':True,'frozen_g2_0033_parent':True,
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnosis_only_no_model_selection'},
        'protocol':{'single_steps':single_steps,'multi_steps':multi_steps,
                    'mini_train_size':len(mini),'unseen_train_size':len(heldout),
                    'arms':KINDS,'independent_direct_score_fit':True,
                    'gold_oracle_control':True,'training_on_train_only':True,
                    'evaluation_on_disjoint_train_only':True,
                    'never_capture_dev':True,'no_model_promotion':True}}
    assert_frozen()
    folder.mkdir(parents=True)
    with gzip.open(folder/LEDGER,'wt',encoding='utf-8',compresslevel=6) as stream:
        for row in ledger:
            stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
    save_file(weights,str(folder/WEIGHTS))
    (folder/POLICIES).write_text(json.dumps(report['train_policy'],sort_keys=True,indent=2)+'\n',
                                 encoding='utf-8')
    report.update({'ledger_sha256':backbone.sha256(folder/LEDGER),
        'weights_sha256':backbone.sha256(folder/WEIGHTS),
        'policies_sha256':backbone.sha256(folder/POLICIES)})
    (folder/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,output,
        single_steps=SINGLE_STEPS,multi_steps=MULTI_STEPS,**kwargs):
    parent40,h40=g41.verify_parent40(bakeoff40_run)
    parent41,_,h41=g42.verify_parent41(oracle41_run,h40)
    parent42,h42=b43.verify_parent42(rank42_run,h40,h41)
    parent43,h43=verify_parent43(absolute43_run,h40,h41,h42)
    if any(parent43[key]!=parent42[key] for key in ('training_sha256','train_dataset_sha256')):
        raise ValueError('GAMER_G2_0044_PARENT_COHORT_DRIFT')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0044_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,pol33,
                 parent33_models,assert_all_frozen,**ignored):
        for name,rows in [('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)]:
            if parent43['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0044_TRAIN_HASH_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=parent41['protocol']['config']:
            raise ValueError('GAMER_G2_0044_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,
                      parent33_models[g40.SOURCE_SEED],assert_all_frozen,
                      output,parent43,{**h40,**h41,**h42,**h43},
                      single_steps=single_steps,multi_steps=multi_steps)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
