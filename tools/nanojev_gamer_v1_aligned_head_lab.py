#!/usr/bin/env python3
"""Disposable two-projector, shared-decoder mathematical-alignment experiment.

Uses ONLY the verified original 100x1024 last-token head input and the frozen
step-500 production head predictions. Three matched arms:
  final_last_control: prior single-view zero-initialized residual (v4 control)
  dual_unaligned: two independently learned format projections + shared decoder
  dual_aligned: same architecture, paired supervised alignment during training

Every arm fits ORIGINAL TRAIN and complete six-case synthetic TRAIN groups.
Original DEV is exclusively used for retention/checkpoint selection; synthetic
DEV selects, and synthetic TEST is previously examined and NOT promotion evidence.
Source-original prompts and out-of-family belief prompts use an explicitly
UNVALIDATED general fallback projection. The two recognized signed-offset prompt
surfaces are routed from *prompt text*, verified against captured fingerprints;
synthetic metadata labels alone are never used to choose the route. A deployable
family router for arbitrary incoming questions is NOT certified by this lab.

No Qwen/LoRA training, original-head updates, production writes or promotion.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path

from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_structured_head_lab as structured

SCHEMA = 'nanojev.gamer.v1.aligned-head-lab.v1'
ARMS = ('final_last_control', 'dual_unaligned', 'dual_aligned')
PROJECT_DIM = 64


def _prompt_surface(prompt):
    """Text-derived routing, independent of numeric answers and dataset labels."""
    if not isinstance(prompt, str):
        raise ValueError('ALIGN_ROUTER_REQUIRES_PROMPT_TEXT')
    if (prompt.startswith('A spacecraft is at signed coordinate ') and
        'What is the spacecraft’s signed position relative to the beacon?' in prompt):
        return 'source_native'
    if (prompt.startswith('Two actions: C (cooperate) or D (defect). Your payoffs: ') and
        'Compute T-R (defection gain when the other cooperates).' in prompt):
        return 'spine_difference'
    return 'unsupported'


def _verified_prompt_routes(info):
    """Independently regenerate prompts; bind them to exact capture fingerprints.

    These labels are used only to *verify* router output. No target, sign, group
    name, or metadata surface is an input to _prompt_surface().
    """
    rows=info['dataset']
    by_split=Counter(r['split'] for r in rows)
    if any(by_split[s] % 6 for s in ('train','dev')) or by_split['test'] < 6:
        raise ValueError('ALIGN_SPLIT_NOT_COMPLETE_SIX_CASE_GROUPS')
    train_groups=by_split['train']//6
    dev_groups=by_split['dev']//6
    ood_groups=sum(r['family']=='hawk_indifference' for r in rows)//6
    test_native=by_split['test']-ood_groups*6
    if test_native<=0 or test_native%6 or ood_groups not in (0,6):
        raise ValueError('ALIGN_UNSUPPORTED_ORACLE_DECK_LAYOUT')
    deck=probe.make_deck(train_groups,dev_groups,test_native//6,ood_groups>0)
    if probe.deck_hash(deck)!=info['dataset_sha256'] or len(deck)!=len(rows):
        raise ValueError('ALIGN_ORACLE_DECK_CAPTURE_HASH_MISMATCH')
    routes=[];summary=Counter()
    for captured,case in zip(rows,deck):
        if case.public()!=captured or probe.sha_text(case.prompt)!=captured['prompt_sha256']:
            raise ValueError('ALIGN_CAPTURE_PROMPT_FINGERPRINT_OR_METADATA_MISMATCH')
        route=_prompt_surface(case.prompt)
        expected={'source_native':'source_native',
                  'spine_difference':'spine_difference'}.get(case.surface,'unsupported')
        if route!=expected:
            raise ValueError('ALIGN_ROUTER_PROMPT_SURFACE_MISMATCH: '+case.id)
        summary[route]+=1
        routes.append(1 if route=='spine_difference' else 0)
    return routes,dict(summary)


def _routed_profiles(data,prepared):
    """Match v4's exact input feature views, augment with verified prompt route.

    Original TRAIN and DEV are not synthetic signed-offset representations: use
    the documented fallback/native projector only. No DEV prompt used in fitting.
    """
    import numpy as np
    variants=structured._assemble(data,prepared)
    base=variants['final_last']
    routes,summary=_verified_prompt_routes(data['probe_info'])
    full=np.asarray(routes,dtype=np.float32)
    part=data['partitions']
    x,y,old=base['train_source']
    sy,so,s_old,dev_x=base['dev_source']
    out={
        'train_source':(x,old,y,np.zeros(len(x),dtype=np.float32)),
        'dev_source':(sy,so,s_old,dev_x,np.zeros(len(dev_x),dtype=np.float32)),
        'train_synthetic':(*base['train_synthetic'],full[part['train']]),
        'dev_synthetic':(*base['dev_synthetic'],full[part['dev']]),
        'test_synthetic':(*base['test_synthetic'],full[part['test']]),
    }
    if (len(out['train_synthetic'][0]) != len(out['train_synthetic'][-1]) or
        len(out['dev_synthetic'][3]) != len(out['dev_synthetic'][-1]) or
        len(out['test_synthetic'][3]) != len(out['test_synthetic'][-1])):
        raise ValueError('ALIGN_ROUTE_FEATURE_COUNT_MISMATCH')
    return out,summary


def _encode(x,old,routes,mu,sigma):
    import numpy as np
    x=np.asarray(x,dtype=np.float32)
    r=np.asarray(routes,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=1024 or len(x)!=len(r) or \
       not np.isin(r,[0,1]).all() or not np.isfinite(x).all():
        raise ValueError('ALIGN_INVALID_ROUTED_HEAD_INPUT')
    normal=joint._to_input(x,old,mu,sigma)
    result=np.concatenate([normal[:,:1024],r[:,None],normal[:,1024:]],axis=1)
    if result.shape!=(len(x),1026) or not np.isfinite(result).all():
        raise ValueError('ALIGN_INVALID_NORMALIZED_INPUT')
    return result.astype(np.float32,copy=False)


def _build_aligned_model(torch,projection_width=PROJECT_DIM):
    from torch.nn import functional as F
    if not isinstance(projection_width,int) or projection_width<8 or projection_width>256:
        raise ValueError('ALIGN_INVALID_PROJECTOR_WIDTH')
    class DualProjector(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.native=torch.nn.Sequential(torch.nn.Linear(1024,projection_width),
                                              torch.nn.LayerNorm(projection_width),
                                              torch.nn.Tanh())
            self.game=torch.nn.Sequential(torch.nn.Linear(1024,projection_width),
                                            torch.nn.LayerNorm(projection_width),
                                            torch.nn.Tanh())
            self.decoder=torch.nn.Sequential(torch.nn.Linear(projection_width+1,128),
                                               torch.nn.GELU(),torch.nn.Linear(128,4))
            torch.nn.init.zeros_(self.decoder[-1].weight)
            torch.nn.init.zeros_(self.decoder[-1].bias)
        def latent(self,x):
            if x.ndim!=2 or x.shape[1]!=1026:
                raise ValueError('ALIGN_EXPECTS_1024_PLUS_ROUTE_PLUS_BASE')
            return torch.where(x[:,-2:-1]>0.5,self.game(x[:,:1024]),self.native(x[:,:1024]))
        def forward(self,x):
            a=self.decoder(torch.cat((self.latent(x),x[:,-1:]),dim=-1))
            return torch.cat((2*torch.tanh(a[:,:1]/2),
                              3*torch.tanh(a[:,1:]/3)),dim=-1)
    return DualProjector()


def _alignment_loss(torch,net,x,group_count,*,temperature=0.2):
    """Supervised paired InfoNCE: matching answers attract, other signs repel.

    Per complete six-case group the first and last three rows are matching
    negative, exact-zero, positive triples in two independent wordings.
    Aligned format embeddings are contrasted *within* groups to avoid treating
    equal numerical answers in other groups as negatives. Signed output and
    triple supervision remain separate, preventing all-zero collapse.
    """
    from torch.nn import functional as F
    if (x.ndim!=2 or len(x)!=6*group_count or group_count<1 or
        not isinstance(group_count,int) or not math.isfinite(temperature) or
        temperature<=0):
        raise ValueError('ALIGN_INVALID_MATCHED_GROUP_BATCH')
    route=x[:,-2].reshape(group_count,6)
    if not bool(torch.all(route[:,:3]==0)) or not bool(torch.all(route[:,3:]==1)):
        raise ValueError('ALIGN_MISMATCHED_GROUP_ROUTING')
    z=net.latent(x).reshape(group_count,6,-1)
    a,b=z[:,:3],z[:,3:]
    a_unit=F.normalize(a,p=2,dim=-1)
    b_unit=F.normalize(b,p=2,dim=-1)
    sim=torch.matmul(a_unit,b_unit.transpose(1,2))/temperature
    wanted=torch.arange(3,device=x.device).repeat(group_count)
    contrast=(F.cross_entropy(sim.reshape(-1,3),wanted)+
              F.cross_entropy(sim.transpose(1,2).reshape(-1,3),wanted))/2
    paired=F.smooth_l1_loss(a,b,beta=0.2)
    # Negative/zero/positive must remain distinct in *each* representation.
    separations=[]
    for view in (a,b):
        d1=(view[:,0]-view[:,1]).square().mean(dim=-1)
        d2=(view[:,1]-view[:,2]).square().mean(dim=-1)
        d3=(view[:,0]-view[:,2]).square().mean(dim=-1)
        separations.append((F.relu(0.2-d1).square()+
                            F.relu(0.2-d2).square()+
                            F.relu(0.4-d3).square()).mean())
    separate=(separations[0]+separations[1])/2
    return contrast+0.2*paired+0.5*separate,{
        'supervised_bidirectional_infonce':float(contrast.detach()),
        'paired_latent_distance':float(paired.detach()),
        'signed_triple_separation':float(separate.detach())}


def _predict(torch,net,x,old,routes,mu,sigma,device,*,return_delta=False):
    import numpy as np
    encoded=_encode(x,old,routes,mu,sigma)
    net.eval();deltas=[]
    with torch.no_grad():
        for start in range(0,len(x),128):
            deltas.append(net(torch.from_numpy(encoded[start:start+128]).to(device))
                          .cpu().numpy())
    delta=np.concatenate(deltas)
    cls,val=joint._decode_correction(delta,old)
    return (cls,val,delta) if return_delta else (cls,val)


def _evaluate(torch,net,spec,mu,sigma,device,split='dev_synthetic'):
    import numpy as np
    source_meta,source_y,source_old,source_x,source_route=spec['dev_source']
    sc,sv,sd=_predict(torch,net,source_x,source_old,source_route,mu,sigma,
                      device,return_delta=True)
    retained=lab._retention(source_y,sv,sc,source_old,source_meta)
    retained['correct_zero_margin_monitor']=joint._correct_original_zero_margins(
        source_old,source_y,sd)
    syn_meta,syn_y,syn_old,syn_x,part,routes=spec[split]
    cls,pred=_predict(torch,net,syn_x,syn_old[part],routes,mu,sigma,device)
    all_pred=np.asarray(syn_old,dtype=np.float64).copy()
    all_cls=lab._classify_continuous(all_pred)
    all_pred[part]=pred;all_cls[part]=cls
    score=lab._group_scores(syn_meta,syn_y,all_pred,all_cls,part)
    score['cross_format']=joint._paired_agreement(syn_meta,syn_y,all_pred,all_cls,part)
    return retained,score


def _fit_aligned(torch,spec,*,device,steps,eval_every,seed,lr,source_weight,
                 anchor_weight,zero_margin_weight,alignment_weight):
    import numpy as np
    torch.manual_seed(seed);random.seed(seed)
    sx,sold,sy,sroute=spec['train_source']
    tx,told,ty,trows,troute=spec['train_synthetic']
    if set(np.unique(troute))!={0.,1.}:
        raise ValueError('ALIGN_TRAIN_REQUIRES_TWO_ROUTED_FORMATS')
    groups=joint._complete_training_groups(trows,ty)
    # Verify each six-case group has exactly the two expected prompt routes,
    # independently of the labels used to construct the group.
    for group,_ in groups:
        if not (np.array_equal(troute[list(group[:3])],[0,0,0]) and
                np.array_equal(troute[list(group[3:])],[1,1,1])):
            raise ValueError('ALIGN_GROUP_ROUTING_INVALID')
    mu,sigma=joint._normalization(sx,tx)
    source=_encode(sx,sold,sroute,mu,sigma)
    synthetic=_encode(tx,told,troute,mu,sigma)
    sign_anchors=joint._original_correct_mask(sy,sold)
    numeric_anchors=joint._accurate_numeric_mask(sy,sold)
    a=joint._tensor_set((source,sold,sy,sign_anchors,numeric_anchors),device,torch)
    b=joint._tensor_set((synthetic,told,ty,
                         np.zeros(len(ty),dtype=bool),
                         np.zeros(len(ty),dtype=bool)),device,torch)
    net=_build_aligned_model(torch).to(device)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    rng=np.random.default_rng(seed)
    def assess(step,loss=None,parts=None):
        retention,dev=_evaluate(torch,net,spec,mu,sigma,device)
        rank=joint._rank(retention,dev)
        hist=joint._history_entry(step,loss,retention,dev,rank,parts)
        return rank,retention,dev,hist
    rank,retention,dev,h=assess(0)
    history=[h]
    best={'rank':rank,'retention':retention,'dev':dev,'step':0,
          'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
    for step in range(1,steps+1):
        net.train()
        si=rng.integers(0,len(sy),size=min(32,len(sy)))
        pi=joint._balanced_protected_indices(rng,sy,sign_anchors,per_class=8)
        chosen=rng.choice(len(groups),size=4,replace=True)
        ti=np.concatenate([groups[int(j)][0] for j in chosen])
        local_triples=[(6*k+j*3,6*k+j*3+1,6*k+j*3+2)
                       for k in range(len(chosen)) for j in range(2)]
        optimizer.zero_grad(set_to_none=True)
        sloss,sinfo=joint._training_loss(torch,net,*(v[si] for v in a[:4]),
                       weight=source_weight,source=True,
                       numeric_anchor=a[4][si],zero_margin_weight=zero_margin_weight)
        tloss,tinfo=joint._training_loss(torch,net,*(v[ti] for v in b[:4]),
                       weight=1.,source=False)
        triple=joint._triple_loss(torch,net,b[0][ti],b[1][ti],local_triples)
        aligned,ainfo=_alignment_loss(torch,net,b[0][ti],len(chosen))
        protected=net(a[0][pi])
        preserve,pinfo=joint._preservation_loss(torch,protected,a[1][pi],a[2][pi],
                                                a[3][pi],a[4][pi],
                                                zero_margin_weight=zero_margin_weight)
        loss=(sloss+tloss+2*triple+alignment_weight*aligned+
              anchor_weight*preserve)
        if not torch.isfinite(loss):raise ValueError('ALIGN_NONFINITE_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.0)
        optimizer.step()
        if step%eval_every==0 or step==steps:
            losses={'source':sinfo,'synthetic':tinfo,'triple':float(triple.detach()),
                    'alignment':ainfo,'balanced_protect':pinfo}
            rank,retention,dev,h=assess(step,float(loss.detach()),losses)
            history.append(h)
            if rank>best['rank']:
                best={'rank':rank,'step':step,'retention':retention,'dev':dev,
                      'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
    net.load_state_dict(best['state']);net.eval()
    counts={'original_train_count':len(sy),'synthetic_train_count':len(ty),
            'complete_training_groups':len(groups),
            'originally_correct_sign_train':int(sign_anchors.sum()),
            'correct_zero_train_count':int((sign_anchors & (sy==0)).sum()),
            'alignment_weight':alignment_weight,'projector_width':PROJECT_DIM,
            'source_dev_used_for_fitting':False,'synthetic_test_used_for_fitting':False}
    return net,mu,sigma,best,history,counts


def _score(torch,model,spec,mu,sigma,best,history,counts,arm,device):
    _,test=_evaluate(torch,model,spec,mu,sigma,device,'test_synthetic')
    # Baseline synthetic is the same exact frozen production head for all arms.
    meta,y,old,_,part,_=spec['test_synthetic']
    baseline=lab._group_scores(meta,y,old,lab._classify_continuous(old),part)
    gate=lab._gates(test,best['retention'],baseline['all'])
    gate['correct_cross_format_invariance']=joint._cross_format_gate(test['cross_format'])
    gate['all_bounded_gates_pass']=bool(gate['all_bounded_gates_pass'] and
                                      gate['correct_cross_format_invariance'])
    return {'id':arm,'architecture':arm,'selected_step':best['step'],
            'original_dev_retention':best['retention'],'dev':best['dev'],
            'test':test,'gates':gate,'history':history,'train_counts':counts,
            'decoder_parameters':sum(p.numel() for p in model.parameters()),
            'promotion_authorized':False,'candidate_never_promoted':True}


def _matched_ablation_effect(candidates):
    """Development-selected aligned minus unaligned on identical train budgets.

    Evaluates held-out differences descriptively; previously examined TEST may
    never drive checkpoint choice or authorization.
    """
    by_id={c['id']:c for c in candidates}
    a=by_id['dual_aligned'];b=by_id['dual_unaligned']
    def differences(item):
        return {
            'balanced_sign':a[item]['all']['balanced_sign_accuracy']-
                            b[item]['all']['balanced_sign_accuracy'],
            'complete_triples':a[item]['all']['triple_accuracy']-
                               b[item]['all']['triple_accuracy'],
            'both_correct_matched_pairs':
                a[item]['cross_format']['both_correct_fraction']-
                b[item]['cross_format']['both_correct_fraction'],
            'complete_six_case_groups':
                a[item]['cross_format']['complete_six_case_fraction']-
                b[item]['cross_format']['complete_six_case_fraction']}
    return {'control':'dual_unaligned','treatment':'dual_aligned',
        'dev_deltas':differences('dev'),
        'already_examined_test_deltas_diagnostic_only':differences('test'),
        'retention_delta':(
            a['original_dev_retention']['original_correct_kept_fraction']-
            b['original_dev_retention']['original_correct_kept_fraction']),
        'identical_architecture_optimizer_data_seed_steps':True,
        'separately_dev_selected_checkpoints':True,
        'test_not_used_for_selection_or_promotion':True}


def _write_outputs(out,report,winner,data):
    import torch
    out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                             allow_nan=False)+'\n',encoding='utf-8')
    fields=['architecture','selected_step','original_correct_kept',
        'retained_negative','retained_zero','retained_positive','original_mae',
        'dev_sign','dev_complete_six','dev_both_correct','test_sign',
        'test_triples','test_complete_six','test_both_correct','gates']
    with (out/'architecture_comparison.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for c in report['candidates']:
            r=c['original_dev_retention']; d=c['dev'];t=c['test']
            w.writerow({'architecture':c['architecture'],'selected_step':c['selected_step'],
                'original_correct_kept':r['original_correct_kept_fraction'],
                **{'retained_'+n:r['by_class'][n]['preserved_fraction']
                   for n in ('negative','zero','positive')},
                'original_mae':r['candidate']['signed_mae'],
                'dev_sign':d['all']['balanced_sign_accuracy'],
                'dev_complete_six':d['cross_format']['complete_six_case_fraction'],
                'dev_both_correct':d['cross_format']['both_correct_fraction'],
                'test_sign':t['all']['balanced_sign_accuracy'],
                'test_triples':t['all']['triple_accuracy'],
                'test_complete_six':t['cross_format']['complete_six_case_fraction'],
                'test_both_correct':t['cross_format']['both_correct_fraction'],
                'gates':c['gates']['all_bounded_gates_pass']})
    with (out/'checkpoint_history.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['architecture','step','retention','failed_gates','retained_negative',
                'retained_zero','retained_positive','lost_negative','lost_zero',
                'lost_positive','dev_sign','dev_triples','dev_both_correct',
                'dev_complete_six','zero_margins_below_floor']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for c in report['candidates']:
            for h in c['history']:
                r=h['source_retention_by_class']; pair=h['synthetic_dev_cross_format'] or {}
                w.writerow({'architecture':c['architecture'],'step':h['step'],
                    'retention':h['source_retention'],
                    'failed_gates':';'.join(h['source_retention_failed_gates']),
                    **{'retained_'+n:r[n]['preserved_fraction'] for n in ('negative','zero','positive')},
                    **{'lost_'+n:r[n]['lost'] for n in ('negative','zero','positive')},
                    'dev_sign':h['synthetic_dev_balanced_sign'],
                    'dev_triples':h['synthetic_dev_triples'],
                    'dev_both_correct':pair.get('both_correct_fraction'),
                    'dev_complete_six':pair.get('complete_six_case_fraction'),
                    'zero_margins_below_floor':(h['source_correct_zero_margin'] or {}).get('below_floor_count')})
    with (out/'ablation_comparisons.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['metric','dev_delta_aligned_minus_unaligned',
                'already_seen_test_delta_diagnostic_only']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        v=report['matched_alignment_ablation']
        for metric,value in v['dev_deltas'].items():
            w.writerow({'metric':metric,
                'dev_delta_aligned_minus_unaligned':value,
                'already_seen_test_delta_diagnostic_only':
                    v['already_examined_test_deltas_diagnostic_only'][metric]})
        w.writerow({'metric':'original_correct_retention',
                    'dev_delta_aligned_minus_unaligned':v['retention_delta']})
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
        'arm':winner['item']['architecture'],'frozen_lineage':data['probe_info']['lineage'],
        'original_train_cache_sha256':data['train_cache']['sha256'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'weights':winner['weights'],'mean':torch.as_tensor(winner['mu']),
        'std':torch.as_tensor(winner['sigma']),
        'selected_step':winner['item']['selected_step'],
        'promotion_authorized':False},out/'disposable_candidate.pt')


def _validate_args(args):
    if (args.steps<1 or args.steps>20000 or args.eval_every<1 or
        args.lr<=0 or args.source_weight<=0 or args.anchor_weight<1 or
        args.alignment_weight<=0 or args.zero_margin_weight<0 or
        not all(math.isfinite(v) for v in (args.lr,args.source_weight,
           args.anchor_weight,args.alignment_weight,args.zero_margin_weight))):
        raise ValueError('ALIGN_INVALID_OPTIMIZER_CONFIGURATION')


def train(args):
    import torch
    _validate_args(args)
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('ALIGN_CUDA_UNAVAILABLE')
    roots=[Path(x) for x in (args.run,args.expanded_run,args.input,args.source_features)
           if x is not None]
    out=lab._output(args.output,roots)
    data,previous=joint._prepare(args)
    spec,routing=_routed_profiles(data,previous)
    print(f'ALIGNED_HEAD source_train={len(data["original_train"])} '
          f'source_dev={len(data["retention_info"]["dataset"])} '
          f'synthetic={len(data["probe_info"]["dataset"])}; '
          'frozen Qwen/LoRA/step-500; text-verified synthetic router',flush=True)
    outcomes=[];winner=None
    for arm in ARMS:
        if arm=='final_last_control':
            original=previous['final_last']
            net,mu,sigma,best,history,counts=joint._fit_one(
                torch,feature='final_last',source=original['train_source'],
                synthetic=original['train_synthetic'],original_dev=original['dev_source'],
                synthetic_dev=original['dev_synthetic'],device=args.device,
                steps=args.steps,lr=args.lr,source_weight=args.source_weight,
                anchor_weight=args.anchor_weight,seed=args.seed,
                eval_every=args.eval_every,consistency_weight=0.,
                zero_margin_weight=args.zero_margin_weight)
            # Evaluate exactly with v4's original oracle, without dummy routing.
            import numpy as np
            meta,y,old,features,part=original['test_synthetic']
            cls,pred=joint._predict(net,features,old[part],mu,sigma,args.device)
            all_pred=np.asarray(old,dtype=np.float64).copy()
            all_cls=lab._classify_continuous(all_pred)
            all_pred[part]=pred;all_cls[part]=cls
            test=lab._group_scores(meta,y,all_pred,all_cls,part)
            test['cross_format']=joint._paired_agreement(meta,y,all_pred,all_cls,part)
            b=lab._group_scores(meta,y,old,lab._classify_continuous(old),part)
            gate=lab._gates(test,best['retention'],b['all'])
            gate['correct_cross_format_invariance']=joint._cross_format_gate(test['cross_format'])
            gate['all_bounded_gates_pass']=bool(gate['all_bounded_gates_pass'] and
                                               gate['correct_cross_format_invariance'])
            item={'id':arm,'architecture':arm,'selected_step':best['step'],
                  'original_dev_retention':best['retention'],'dev':best['dev'],
                  'test':test,'gates':gate,'history':history,'train_counts':counts,
                  'decoder_parameters':sum(p.numel() for p in net.parameters()),
                  'promotion_authorized':False,'candidate_never_promoted':True}
        else:
            net,mu,sigma,best,history,counts=_fit_aligned(
                torch,spec,device=args.device,steps=args.steps,
                eval_every=args.eval_every,seed=args.seed,lr=args.lr,
                source_weight=args.source_weight,anchor_weight=args.anchor_weight,
                zero_margin_weight=args.zero_margin_weight,
                alignment_weight=0. if arm=='dual_unaligned' else args.alignment_weight)
            item=_score(torch,net,spec,mu,sigma,best,history,counts,arm,args.device)
        outcomes.append(item)
        rank=joint._rank(item['original_dev_retention'],item['dev'])
        if winner is None or rank>winner['rank']:
            winner={'rank':rank,'item':item,
                    'weights':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()},
                    'mu':mu.copy(),'sigma':sigma.copy()}
        print(probe.canonical({'architecture':arm,'step':item['selected_step'],
            'kept':item['original_dev_retention']['original_correct_kept_fraction'],
            'retention':item['original_dev_retention']['status'],
            'dev_sign':item['dev']['all']['balanced_sign_accuracy'],
            'test_sign':item['test']['all']['balanced_sign_accuracy'],
            'test_triples':item['test']['all']['triple_accuracy'],
            'test_six_groups':item['test']['cross_format']['complete_six_case_groups'],
            'gates':item['gates']['all_bounded_gates_pass']}),flush=True)
    joint._verify_after(data)
    ablation=_matched_ablation_effect(outcomes)
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'candidate_count':len(outcomes),'candidates':outcomes,
        'matched_alignment_ablation':ablation,
        'selected_candidate':winner['item']['architecture'],
        'selected_bounded_gates_pass':winner['item']['gates']['all_bounded_gates_pass'],
        'experiment':'TWO_INDEPENDENT_PROJECTORS_SHARED_RESIDUAL_DECODER',
        'matched_training_budget':True,
        'normalization':'TRAIN_ONLY_FROZEN_FINAL_LAST_1024',
        'routing':{'synthetic_oracle_fingerprint_checked':True,
            'prompt_text_router_verified':routing,
            'original_task_policy':'UNSUPPORTED_PROMPT_FALLS_BACK_TO_NATIVE_PROJECTOR',
            'unseen_arbitrary_prompt_router':'NOT_CERTIFIED_FOR_PRODUCTION',
            'hawk_belief':'OOD_TEST_ONLY_FALLBACK_NOT_TRAINED'},
        'lineage':data['probe_info']['lineage'],
        'original_train_cache':data['train_cache'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'original_dev_sha256':data['retention_info']['dataset_sha256'],
        'train_configuration':{'steps':args.steps,'eval_every':args.eval_every,
            'seed':args.seed,'lr':args.lr,'source_weight':args.source_weight,
            'anchor_weight':args.anchor_weight,'zero_margin_weight':args.zero_margin_weight,
            'alignment_weight':args.alignment_weight},
        'original_train_used_for_fitting':True,'original_dev_used_for_fitting':False,
        'synthetic_dev_selection_only':True,
        'synthetic_test':'PREVIOUSLY_EXAMINED_NOT_VIRGIN',
        'lora_frozen':True,'production_head_frozen':True,
        'production_promotion':False,'independent_acceptance_required':True}
    _write_outputs(out,report,winner,data)
    print(probe.canonical({'status':report['status'],
        'selected':report['selected_candidate'],
        'bounded_gates_pass':report['selected_bounded_gates_pass'],
        'source_dev_used_for_fitting':False,'production_weights_modified':False,
        'report':str(out/'report.json'),
        'candidate_checkpoint':str(out/'disposable_candidate.pt')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('oracle','train'),default='oracle')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--source-weight',type=float,default=1.)
    p.add_argument('--anchor-weight',type=float,default=3.)
    p.add_argument('--zero-margin-weight',type=float,default=2.)
    p.add_argument('--alignment-weight',type=float,default=2.)
    p.add_argument('--seed',type=int,default=49)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='oracle':
        deck=probe.make_deck()
        info={'dataset':[c.public() for c in deck],
              'dataset_sha256':probe.deck_hash(deck)}
        routing,counts=_verified_prompt_routes(info)
        print(probe.canonical({'status':'ORACLE_ONLY',
          'synthetic':probe.validate_deck(deck),
          'text_router':counts,'route_cases':len(routing),
          'candidate_configs':len(ARMS),'frozen_model_training':False,
          'original_dev_fitting':False,'promotion_authorized':False}))
    else:train(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
