#!/usr/bin/env python3
"""V13: consolidate acquired TRAIN sign margins before cumulative acquisition.

The V11 selected expanded specialist, the V6 production-head bypass, and all
upstream Qwen/LoRA/C0 weights are frozen. A new route-conditioned, feature-only
logit specialist is trained. TRAIN-only sign and safety-reserve ledgers gate
EVERY accepted parameter update under exact V6 confidence-gated inference.

The two acquisition arms have identical parameterization, seed, replay and
selection policy; only the consolidation stage differs. TEST has no role in
fitting or selection. Both selected and terminal model/ledger states survive.
This capture-bound experiment cannot authorize a production router or promotion.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path

from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.margin-consolidation.v13'
ARMS=('no_consolidation_control','consolidated_acquisition')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _margin_model(torch, width, capacity_width):
    """Zero-start format-specific correction; no group IDs or labels at inference."""
    if not 8 <= width <= 256:
        raise ValueError('V13_BAD_MARGIN_WIDTH')
    class MarginSpecialist(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base=v11._expanded_model(torch,capacity_width)
            self.base.requires_grad_(False)
            self.native=torch.nn.Sequential(torch.nn.Linear(1024,width),
                torch.nn.GELU(),torch.nn.Linear(width,3))
            self.game=torch.nn.Sequential(torch.nn.Linear(1024,width),
                torch.nn.GELU(),torch.nn.Linear(width,3))
            for module in (self.native,self.game):
                torch.nn.init.zeros_(module[-1].weight)
                torch.nn.init.zeros_(module[-1].bias)
            self.protected=True
        def forward(self,x):
            if x.ndim!=2 or x.shape[1]!=1026:
                raise ValueError('V13_BAD_ENCODED_INPUT')
            with torch.no_grad():
                baseline=self.base(x)
            route=x[:,-2:-1]
            game=route>0.5
            z=torch.where(game,self.game(x[:,:1024]),self.native(x[:,:1024]))
            # Preserve frozen signed-log output; alter only class logits. The
            # total class correction, not just this adapter, is capped at V6's
            # existing ±2 limit. Derivatives remain finite inside that room.
            room=torch.clamp(v6.MAX_CLASS_LOGIT_CORRECTION-baseline[:,1:].abs(),min=0)
            output=torch.cat((baseline[:,:1],baseline[:,1:]+room*torch.tanh(z)),dim=1)
            return output*(route>=0).to(output.dtype)
    return MarginSpecialist()


def _true_class_margins(torch,net,encoded,old,truth):
    """Raw true-class logit lead, before V6's confidence fallback."""
    logits=joint._head_logits(torch,old,net(encoded))
    labels=torch.as_tensor(truth,dtype=torch.long,device=logits.device)+1
    chosen=logits.gather(1,labels[:,None]).squeeze(1)
    alternatives=logits.scatter(1,labels[:,None],-1e6).amax(dim=1)
    return chosen-alternatives


def _class_margins(torch,net,encoded,old,truth):
    """Differentiable *decision safety* under V6's real abstention policy.

    If the frozen class is WRONG, the specialist must beat every alternative
    by .5, so safety is (true-class lead - .5). If the frozen class is
    CORRECT, a wrong specialist proposal is harmless until its winning-logit
    lead reaches .5. Its safety is (.5 - top-two lead), even if the frozen
    class currently ranks third in the raw logits. If raw winner is correct,
    safety is (.5 + top-two lead). This avoids the incorrect assumption that
    the frozen correct class must always be the runner-up.

    Branches are differentiable inside the current decision region; actual V6
    _predict remains the only authority for accepting an update.
    """
    logits=joint._head_logits(torch,old,net(encoded))
    labels=torch.as_tensor(truth,dtype=torch.long,device=logits.device)+1
    top=torch.topk(logits,k=2,dim=1)
    lead=top.values[:,0]-top.values[:,1]
    correct_top=(top.indices[:,0]==labels)
    cls=torch.where(old>0.05,torch.full_like(labels,2),
        torch.where(old<-.05,torch.zeros_like(labels),torch.ones_like(labels)))
    frozen_correct=(cls==labels)
    chosen=logits.gather(1,labels[:,None]).squeeze(1)
    alternatives=logits.scatter(1,labels[:,None],-1e6).amax(dim=1)
    overriding_safety=chosen-alternatives-v6.MIN_CLASS_CHANGE_MARGIN
    frozen_safety=torch.where(correct_top,v6.MIN_CLASS_CHANGE_MARGIN+lead,
                               v6.MIN_CLASS_CHANGE_MARGIN-lead)
    return torch.where(frozen_correct,frozen_safety,overriding_safety)


def _required_margin(old,truth,epsilon):
    """Positive reserve above the actual sign-change/fallback boundary."""
    import numpy as np
    if not 0<epsilon<0.5:
        raise ValueError('V13_INVALID_MARGIN_RESERVE')
    return np.full(len(old),epsilon,dtype=np.float32)


def _margin_census(torch,net,encoded,old,truth,required):
    import numpy as np
    net.eval();observed=[]
    with torch.no_grad():
        for start in range(0,len(old),128):
            current=_class_margins(torch,net,encoded[start:start+128],
                              old[start:start+128],truth[start:start+128])
            observed.append(current.detach().cpu().numpy())
    margins=np.concatenate(observed).astype(np.float64)
    deficits=np.maximum(np.asarray(required,dtype=np.float64)-margins,0)
    return margins,deficits


def _training_floor(margins,required,protected):
    """A near-threshold acquired decision cannot spend nonexistent slack.

    Acquired cases with sufficient margin may spend margin down to the reserve.
    Cases below the reserve must not worsen before consolidation succeeds.
    """
    import numpy as np
    floor=np.minimum(margins,np.asarray(required,dtype=np.float64))
    return np.where(protected,floor,-1e12)


def _safety(torch,net,spec,encoded,old,truth,required,ledger,floors,mu,sd,device,
            *,tolerance=2e-5):
    import numpy as np
    correct=v9._correct_train(torch,net,spec,mu,sd,device)
    if not ledger.accepts(correct):
        return False,correct,{'sign_lost':int((ledger.mask & ~correct).sum()),
                              'margin_lost':None}
    margins,deficits=_margin_census(torch,net,encoded,old,truth,required)
    lost=ledger.mask & (margins<np.asarray(floors)-tolerance)
    return not bool(lost.any()),correct,{'sign_lost':0,
        'margin_lost':int(lost.sum()),'minimum_margin':float(margins[ledger.mask].min()),
        'mean_deficit':float(deficits[ledger.mask].mean())}


def _affine_projection(direction,grads,offsets,passes,*,max_ratio=3.0):
    """Project a proposed displacement onto g·d >= requested-current.

    Allows spending existing margin surplus, unlike V10 g·d>=0. This is a
    linearized proposal only; nonlinear exact-inference gates still decide.
    """
    import torch
    if len(grads)!=len(offsets) or passes<1:
        raise ValueError('V13_INVALID_AFFINE_CONSTRAINTS')
    proposed=direction.detach().cpu().float().clone()
    base_norm=torch.linalg.vector_norm(proposed)
    informative=[]
    for grad,need in zip(grads,offsets):
        g=grad.detach().cpu().float()
        if g.shape!=proposed.shape or not bool(torch.isfinite(g).all()) or not math.isfinite(float(need)):
            raise ValueError('V13_INVALID_AFFINE_GRADIENT')
        norm2=torch.dot(g,g)
        if float(norm2)>1e-16:
            informative.append((g,norm2,float(need)))
    before=sum(float(torch.dot(g,proposed))<needed for g,_,needed in informative)
    for _ in range(passes):
        for g,n2,needed in informative:
            shortage=needed-float(torch.dot(g,proposed))
            if shortage>0:
                proposed+=(shortage/n2)*g
    norm=torch.linalg.vector_norm(proposed)
    if float(base_norm)>0 and float(norm)>float(base_norm)*max_ratio:
        proposed*=base_norm*max_ratio/norm
    if not bool(torch.isfinite(proposed).all()):
        raise ValueError('V13_NONFINITE_AFFINE_PROPOSAL')
    return proposed,{'constraints':len(informative),'violations_before':before,
        'violations_after':sum(float(torch.dot(g,proposed))<needed-1e-6
                               for g,_,needed in informative),
        'projected_norm':float(torch.linalg.vector_norm(proposed))}


def _affine_gradients(torch,net,before,encoded,old,truth,positions):
    """Differentiate margins at the SAME pre-proposal model state."""
    net.load_state_dict(before,strict=True)
    net.train()
    params=list(net.parameters());trainable=[p for p in params if p.requires_grad]
    if not trainable:
        raise ValueError('V13_NO_TRAINABLE_PARAMETERS')
    gradients=[]
    for ix in positions:
        margin=_class_margins(torch,net,encoded[ix:ix+1],old[ix:ix+1],truth[ix:ix+1]).sum()
        parts=torch.autograd.grad(margin,trainable,allow_unused=True)
        used={id(p):g for p,g in zip(trainable,parts)}
        flat=torch.cat([torch.zeros(p.numel(),dtype=torch.float32) if used.get(id(p)) is None
            else used[id(p)].detach().cpu().float().reshape(-1) for p in params])
        gradients.append(flat)
    net.zero_grad(set_to_none=True)
    return gradients


def _try_displacement(torch,net,before,direction,ledger,spec,encoded,old,truth,
                      required,floors,mu,sd,device,max_halvings):
    for h in range(max_halvings+1):
        alpha=2.**(-h)
        v10._set_displacement(torch,net,before,direction,alpha)
        ok,correct,detail=_safety(torch,net,spec,encoded,old,truth,required,ledger,
                                  floors,mu,sd,device)
        if ok:
            return alpha,correct,detail
    return None,None,detail


def _consolidate(torch,starting,spec,mu,sd,device,*,width,capacity_width,steps,
                 lr,seed,epsilon,max_halvings):
    """No DEV/TEST gradients. Improve weak acquired margins, never lose signs."""
    import numpy as np
    groups,_=v9._groups_and_fingerprints(spec)
    tx,old,y,rows,routes=spec['train_synthetic']
    torch.manual_seed(seed)
    net=_margin_model(torch,width,capacity_width).to(device)
    net.base.load_state_dict(starting,strict=True)
    x=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(device)
    oldt=torch.as_tensor(old,device=device,dtype=torch.float32)
    truth=np.sign(y).astype(np.int64)
    if not np.array_equal(truth,[v7._signed(Fraction(str(r['target']))) for r in rows]):
        raise ValueError('V13_SYNTHETIC_TRAIN_SIGN_MISMATCH')
    target=_required_margin(old,truth,epsilon)
    initial_correct=v9._correct_train(torch,net,spec,mu,sd,device)
    ledger=v9.MathematicalLedger(initial_correct,rows,groups)
    m0,d0=_margin_census(torch,net,x,oldt,truth,target)
    floor=_training_floor(m0,target,ledger.mask)
    start={'step':0,'weights':v9._copy_state(net), 'ledger_mask':ledger.mask.copy(),
            'acquired_step':ledger.acquired_step.copy(), 'floor':floor.copy()}
    opt=torch.optim.AdamW([p for p in net.parameters() if p.requires_grad],lr=lr,weight_decay=0)
    events=[]
    for step in range(1,steps+1):
        before=v9._copy_state(net);obefore=copy.deepcopy(opt.state_dict())
        net.train();opt.zero_grad(set_to_none=True)
        margins=_class_margins(torch,net,x,oldt,truth)
        locked=torch.as_tensor(ledger.mask,device=device,dtype=torch.bool)
        needed=torch.as_tensor(target,device=device,dtype=margins.dtype)
        # Only acquired TRAIN answers define consolidation; no focus ID at inference.
        missing=torch.relu(needed[locked]-margins[locked])
        loss=(missing.square().mean()+0.02*missing.mean())
        if not torch.isfinite(loss):raise ValueError('V13_NONFINITE_CONSOLIDATION_LOSS')
        if float(loss.detach())<1e-14:
            events.append({'step':step,'status':'already_consolidated','accepted':0,
                          'mean_deficit':0.0})
            break
        loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.0);opt.step()
        direction=v10._parameter_displacement(net,before)
        alpha,correct,detail=_try_displacement(torch,net,before,direction,ledger,
            spec,x,oldt,truth,target,floor,mu,sd,device,max_halvings)
        newm,newdef=_margin_census(torch,net,x,oldt,truth,target)
        improved=(float(newdef[ledger.mask].sum()) <
                   float(d0[ledger.mask].sum())-1e-7)
        if alpha is None or not improved:
            net.load_state_dict(before,strict=True);opt.load_state_dict(obefore)
            events.append({'step':step,'status':'rejected','accepted':0,
                            'mean_deficit':float(d0[ledger.mask].mean()),
                            'reason':'actual_retention_or_margin_deficit_not_improved'})
        else:
            ledger.acquire(correct,step)
            if alpha<1.:opt.load_state_dict(obefore)
            floor=np.maximum(floor,_training_floor(newm,target,ledger.mask))
            d0=newdef
            events.append({'step':step,'status':'accepted','accepted':1,'alpha':alpha,
                 'mean_deficit':float(d0[ledger.mask].mean()),'train_locked':int(ledger.mask.sum())})
        if step%15==0 or step==steps:
            print('V13_CONSOLIDATION_PROGRESS '+probe.canonical({
                'step':step,'accepted':sum(r['accepted'] for r in events),
                'rejected':sum(r['status']=='rejected' for r in events),
                'train_locked':int(ledger.mask.sum()),
                'mean_deficit':float(d0[ledger.mask].mean())}),flush=True)
    ok,actual,detail=_safety(torch,net,spec,x,oldt,truth,target,ledger,
                            floor,mu,sd,device)
    if not ok or not np.array_equal(actual,ledger.mask):
        raise ValueError('V13_CONSOLIDATION_FINAL_LEDGER_OR_RESERVE_FAILURE')
    snapshot={'step':steps,'weights':v9._copy_state(net),
        'ledger_mask':ledger.mask.copy(),'acquired_step':ledger.acquired_step.copy(),
        'floor':floor.copy()}
    return net,start,snapshot,events


def _acquire(torch,model,initial,spec,mu,sd,device,*,steps,eval_every,lr,seed,
            replay_weight,epsilon,max_halvings,projection_passes,max_constraints):
    """Same acquisition algorithm for both arms; exact sign + reserve gates."""
    import numpy as np
    groups,_=v9._groups_and_fingerprints(spec)
    tx,old,y,rows,routes=spec['train_synthetic']
    model.load_state_dict(initial['weights'],strict=True)
    x=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(device)
    oldt=torch.as_tensor(old,device=device,dtype=torch.float32)
    yt=torch.as_tensor(y,device=device,dtype=torch.float32)
    truth=np.sign(y).astype(np.int64)
    required=_required_margin(old,truth,epsilon)
    ledger=v9.MathematicalLedger(initial['ledger_mask'],rows,groups)
    ledger.acquired_step=initial['acquired_step'].copy()
    floor=initial['floor'].copy()
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,device),ledger.mask):
        raise ValueError('V13_START_LEDGER_NOT_ACTUAL')
    opt=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=lr,weight_decay=1e-4)
    rng=np.random.default_rng(seed)
    def snapshot(step):
        ret,dev=v6._evaluate(torch,model,spec,mu,sd,device)
        if ret['status']!='PASS':raise ValueError('V13_ORIGINAL_TASK_RETENTION_DRIFT')
        return {'step':step,'weights':v9._copy_state(model),
          'ledger_mask':ledger.mask.copy(),'acquired_step':ledger.acquired_step.copy(),
          'floor':floor.copy(),'retention':ret,'dev':dev,'rank':joint._rank(ret,dev)}
    best=snapshot(0);events=[];history=[]
    for step in range(1,steps+1):
        new=rng.choice(len(groups),size=2,replace=True)
        replay=ledger.sample_replay_groups(rng,2)
        chosen=[groups[int(i)] for i in np.concatenate([new,replay])]
        ix=np.concatenate([grp[0] for grp in chosen]);anchor=torch.as_tensor(ledger.mask[ix],device=device)
        before=v9._copy_state(model);obefore=copy.deepcopy(opt.state_dict())
        loss,anchor_loss=v10._loss_and_proposal(torch,model,opt,x[ix],oldt[ix],yt[ix],
            anchor,group_count=len(chosen),replay_weight=replay_weight)
        direction=v10._parameter_displacement(model,before)
        alpha,correct,detail=_try_displacement(torch,model,before,direction,ledger,
            spec,x,oldt,truth,required,floor,mu,sd,device,max_halvings)
        status='raw' if alpha is not None else 'rejected'
        projection={'constraints':0,'violations_before':0,'violations_after':0}
        if alpha is None:
            # The most fragile TRAIN answers and all raw-sign casualties form
            # affine halfspaces around the pre-proposal parameters. Selection
            # never uses DEV, TEST, or knowledge of group identity at inference.
            v10._set_displacement(torch,model,before,direction,2.**(-max_halvings))
            current=v9._correct_train(torch,model,spec,mu,sd,device)
            fragile=np.flatnonzero(ledger.mask & ~current).tolist()
            model.load_state_dict(before,strict=True)
            margins,_=_margin_census(torch,model,x,oldt,truth,required)
            ordered=sorted(np.flatnonzero(ledger.mask),key=lambda i:
                (margins[i]-required[i],str(rows[i]['id'])))
            cases=list(dict.fromkeys(fragile+ordered))[:max_constraints]
            grads=_affine_gradients(torch,model,before,x,oldt,truth,cases)
            offsets=[float(required[i]-margins[i]) for i in cases]
            projected,projection=_affine_projection(direction,grads,offsets,
                                                   projection_passes)
            alpha,correct,detail=_try_displacement(torch,model,before,projected,
                ledger,spec,x,oldt,truth,required,floor,mu,sd,device,max_halvings)
            if alpha is not None:status='projected'
        if alpha is None:
            model.load_state_dict(before,strict=True);opt.load_state_dict(obefore)
        else:
            if status=='projected':opt.load_state_dict(obefore)
            old_mask=ledger.mask.copy();ledger.acquire(correct,step)
            m,_=_margin_census(torch,model,x,oldt,truth,required)
            fresh=ledger.mask & ~old_mask
            floor[fresh]=np.minimum(m[fresh],required[fresh])
            # Never relax any previous reserve floor by re-acquisition.
        events.append({'step':step,'status':status,'accepted':int(alpha is not None),
          'alpha':alpha or 0.0,'loss':loss,'anchor_loss':anchor_loss,
          'train_locked':int(ledger.mask.sum()),'sign_lost_raw':detail.get('sign_lost'),
          'margin_lost_raw':detail.get('margin_lost'),**projection})
        if step%eval_every==0 or step==steps:
            ok,actual,details=_safety(torch,model,spec,x,oldt,truth,required,ledger,
                                       floor,mu,sd,device)
            if not ok:raise ValueError('V13_ACCEPTED_STATE_FAILED_EXACT_RETENTION')
            snap=snapshot(step)
            history.append({'step':step,'dev_sign':snap['dev']['all']['balanced_sign_accuracy'],
                'dev_six':snap['dev']['cross_format']['complete_six_case_groups'],
                'train_locked':int(ledger.mask.sum()),
                'accepted':sum(r['accepted'] for r in events),
                'rescued':sum(r['status']=='projected' for r in events),
                'rejected':sum(r['status']=='rejected' for r in events)})
            if snap['rank']>best['rank']:best=snap
            print('V13_ACQUISITION_PROGRESS '+probe.canonical(history[-1]),flush=True)
    terminal=snapshot(steps)
    return best,terminal,events,history


def _check_v12(path,proof,capture,prior_hash,focus,v11_report_sha):
    root=Path(path);source=root/'report.json'
    if not source.is_file():raise ValueError('V13_V12_REPORT_MISSING')
    doc=json.loads(source.read_text(encoding='utf-8'))
    if (doc.get('schema')!=v12.SCHEMA or doc.get('mode')!='repair' or
        doc.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        doc.get('promotion_authorized') is not False or
        doc.get('source_dev_used_for_fitting') is not False or
        doc.get('synthetic_dev_used_for_fitting') is not False or
        doc.get('previously_seen_test_used_for_fitting_selection_or_metric') is not False or
        doc.get('training_capture_sha256')!=capture or
        doc.get('v6_sha256')!=prior_hash or
        doc.get('v11_verified_checkpoints')!=proof or
        doc.get('v11_report_sha256')!=v11_report_sha or
        doc.get('focus_member_count')!=6 or
        int(doc.get('v11_historical_conflict_events') or 0)<1 or
        doc.get('focus_group')!=focus):
        raise ValueError('V13_V12_REPORT_PROVENANCE_MISMATCH')
    return {'report_sha256':_sha(source),'historical_focus':doc['focus_group'],
      'weakest_selected_margin':doc.get('focus_case_min_true_sign_margin_by_state',{}).get('selected'),
      'weakest_terminal_margin':doc.get('focus_case_min_true_sign_margin_by_state',{}).get('terminal')}


def run(args):
    import numpy as np
    import torch
    mandatory=('run','expanded_run','input','source_features','prior','v11_run','v12_run','output')
    if any(getattr(args,n,None) is None for n in mandatory):
        raise ValueError('V13_REQUIRED_ARGUMENT_MISSING')
    if (args.steps<1 or args.eval_every<1 or args.consolidation_steps<1 or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.consolidation_lr) or args.consolidation_lr<=0 or
        not 0<args.reserve<0.5 or not 8<=args.margin_width<=256 or
        not 0<=args.max_halvings<=12 or not 1<=args.projection_passes<=20 or
        not 1<=args.max_constraints<=128 or args.replay_weight<0):
        raise ValueError('V13_BAD_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V13_CUDA_NOT_AVAILABLE')
    out=lab._output(args.output,[Path(getattr(args,n)) for n in mandatory[:-1]])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                               _sha(args.prior),spec,args.device)
    if not (np.array_equal(mu,cp11['selected']['mean'].numpy()) and
            np.array_equal(sd,cp11['selected']['std'].numpy())):
        raise ValueError('V13_NORMALIZER_CHANGED')
    provenance=_check_v12(args.v12_run,proof,capture,_sha(args.prior),args.focus_group,
                           _sha(Path(args.v11_run)/'report.json'))
    selected=models['selected']
    baseline_cases=v7._case_atlas(torch,selected,spec,mu,sd,args.device,'dev')
    initial_mask=v9._correct_train(torch,selected,spec,mu,sd,args.device)
    width=report11['config']['capacity_width']
    consolidation,initial,consolidated,stage_a=_consolidate(torch,cp11['selected']['weights'],
        spec,mu,sd,args.device,width=args.margin_width,capacity_width=width,
        steps=args.consolidation_steps,lr=args.consolidation_lr,seed=args.seed,
        epsilon=args.reserve,max_halvings=args.max_halvings)
    tx,old,y,rows,routes=spec['train_synthetic']
    encoded=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(args.device)
    oldt=torch.as_tensor(old,dtype=torch.float32,device=args.device)
    truth=np.sign(y).astype(np.int64)
    targets=_required_margin(old,truth,args.reserve)
    saved={};comp=[];dev_cases=[];dev_groups=[]
    for arm in ARMS:
        net=_margin_model(torch,args.margin_width,width).to(args.device)
        start=initial if arm==ARMS[0] else consolidated
        best,terminal,attempts,history=_acquire(torch,net,start,spec,mu,sd,args.device,
            steps=args.steps,eval_every=args.eval_every,lr=args.lr,seed=args.seed,
            replay_weight=args.replay_weight,epsilon=args.reserve,
            max_halvings=args.max_halvings,projection_passes=args.projection_passes,
            max_constraints=args.max_constraints)
        saved[arm]={'net':net,'states':{'selected':best,'terminal':terminal},
                    'attempts':attempts,'history':history}
        print('V13_ARM '+probe.canonical({'arm':arm,'accepted':sum(a['accepted'] for a in attempts),
            'rescues':sum(a['status']=='projected' for a in attempts),
            'selected_step':best['step'],'terminal_train':int(terminal['ledger_mask'].sum())}),
            flush=True)
    for arm,rec in saved.items():
        net=rec['net']
        for state,snap in rec['states'].items():
            net.load_state_dict(snap['weights'],strict=True)
            evaluated=v9._correct_train(torch,net,spec,mu,sd,args.device)
            if not np.array_equal(evaluated,snap['ledger_mask']):
                raise ValueError('V13_SNAPSHOT_LEDGER_INCONSISTENT')
            restored=v9.MathematicalLedger(snap['ledger_mask'],rows,groups)
            safe,_,detail=_safety(torch,net,spec,encoded,oldt,truth,targets,restored,
                     snap['floor'],mu,sd,args.device)
            if not safe:
                raise ValueError('V13_SNAPSHOT_MARGIN_RESERVE_DRIFT')
            atlas=v7._case_atlas(torch,net,spec,mu,sd,args.device,'dev')
            cases,group=v11._dev_transitions(baseline_cases,atlas,arm,state)
            dev_cases.extend(cases);dev_groups.extend(group)
            r={'arm':arm,'state':state,'step':snap['step'],
               'train_correct':int(snap['ledger_mask'].sum()),
               **v9._memory_summary(snap['ledger_mask'],groups),
               'dev_correct':snap['dev']['all']['correct_class'],
               'dev_sign':snap['dev']['all']['balanced_sign_accuracy'],
               'dev_zero_recall':snap['dev']['all']['zero_recall'],
               'dev_six':snap['dev']['cross_format']['complete_six_case_groups'],
               'dev_gained_cases':sum(c['transition']=='newly_correct' for c in cases),
               'dev_lost_cases':sum(c['transition']=='lost_correct' for c in cases),
               'dev_gained_groups':sum(c['new_complete'] for c in group),
               'dev_lost_groups':sum(c['lost_complete'] for c in group),
               'original_retention':snap['retention']['status']}
            comp.append(r)
    winner=max((r for r in comp if r['state']=='selected'),
               key=lambda r:saved[r['arm']]['states']['selected']['rank'])
    # TEST is deliberately not used for v13 checkpoint selection or fitting.
    ref=consolidation
    focus=[];ix=[i for i,r in enumerate(rows) if r['group']==args.focus_group]
    if len(ix)!=6:
        raise ValueError('V13_FOCUS_TRAIN_GROUP_NOT_COMPLETE')
    for state,snap in (('before',initial),('consolidated',consolidated)):
        ref.load_state_dict(snap['weights'],strict=True)
        margins,deficits=_margin_census(torch,ref,encoded,oldt,truth,targets)
        true_leads=[]
        with torch.no_grad():
            for start in range(0,len(old),128):
                true_leads.extend(_true_class_margins(torch,ref,encoded[start:start+128],
                    oldt[start:start+128],truth[start:start+128]).cpu().tolist())
        predicted,_=v6._predict(torch,ref,tx,old,routes,mu,sd,args.device)
        for i in ix:
            focus.append({'state':state,'id':rows[i]['id'],
                'group':rows[i]['group'],'surface':rows[i]['surface'],
                'prompt_sha256':rows[i]['prompt_sha256'],'target_sign':int(truth[i]),
                'predicted_sign':int(predicted[i]),
                'raw_true_class_margin':float(true_leads[i]),
                'decision_safety':float(margins[i]),
                'required_decision_reserve':float(targets[i]),
                'reserve_shortfall':float(deficits[i])})
    stage_a_summary={'attempts':len(stage_a),'accepted':sum(r['accepted'] for r in stage_a),
       'initial_locked':int(initial['ledger_mask'].sum()),
       'final_locked':int(consolidated['ledger_mask'].sum()),
       'focus_before_min_safety':min(r['decision_safety'] for r in focus if r['state']=='before'),
       'focus_after_min_safety':min(r['decision_safety'] for r in focus if r['state']=='consolidated'),
       'focus_before_min_true_lead':min(r['raw_true_class_margin'] for r in focus if r['state']=='before'),
       'focus_after_min_true_lead':min(r['raw_true_class_margin'] for r in focus if r['state']=='consolidated'),
       'target_override_margin':v6.MIN_CLASS_CHANGE_MARGIN+args.reserve,
       'target_decision_reserve':args.reserve}
    doc={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
      'frozen_lineage':data['probe_info']['lineage'],
      'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
      'training_capture_sha256':capture,'original_train_cache_sha256':data['train_cache']['sha256'],
      'v6_prior_sha256':_sha(args.prior),'v11_verified':proof,
      'v12_verified':provenance,'focus_group':args.focus_group,'comparison':comp,'selected':winner['arm'],
      'selected_step':winner['step'],'consolidation':stage_a_summary,
      'config':{'steps':args.steps,'eval_every':args.eval_every,
          'consolidation_steps':args.consolidation_steps,'lr':args.lr,
          'consolidation_lr':args.consolidation_lr,'reserve':args.reserve,
          'margin_width':args.margin_width,'seed':args.seed,
          'max_halvings':args.max_halvings,'projection_passes':args.projection_passes,
          'max_constraints':args.max_constraints,'replay_weight':args.replay_weight},
      'train_only_memory':True,'exact_inference_retention_gate':'UNCHANGED_PLUS_MARGIN_FLOORS',
      'source_dev_used_for_training':False,'synthetic_dev_used_for_training':False,
      'previously_seen_test_used_for_training_or_selection':False,
      'general_production_router':'NOT_CERTIFIED','original_task_bypass':'UNCHANGED_V6',
      'production_weights_modified':False,'promotion_authorized':False}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'comparison.csv',comp)
    v10._csv(out/'focus_train_margins.csv',focus)
    v10._csv(out/'consolidation_attempts.csv',stage_a)
    v10._csv(out/'dev_case_transitions.csv',dev_cases)
    v10._csv(out/'dev_group_transitions.csv',dev_groups)
    for arm,rec in saved.items():
        v10._csv(out/(arm+'_attempts.csv'),rec['attempts'])
        v10._csv(out/(arm+'_history.csv'),rec['history'])
        for state,snap in rec['states'].items():
            checkpoint={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
              'arm':arm,'state':state,'step':snap['step'],'weights':snap['weights'],
              'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
              'train_capture_sha256':capture,'frozen_lineage':data['probe_info']['lineage'],
              'v11_selected_sha256':proof['selected']['checkpoint_sha256'],
              'v12_report_sha256':provenance['report_sha256'],
              'margin_width':args.margin_width,'capacity_width':width,
              'mathematical_train_retention_ledger':{
                 'source_split':'SYNTHETIC_TRAIN_ONLY',
                 'rows':v11._ledger_csv(rows,snap['ledger_mask'],snap['acquired_step']),
                 'margin_floor':torch.as_tensor(snap['floor']),
                 'train_capture_sha256':capture},
              'capture_bound_router_only':True,'protected_original_passthrough':True,
              'promotion_authorized':False}
            torch.save(checkpoint,out/f'{arm}_{state}_candidate.pt')
            if arm==winner['arm'] and state=='selected':
                torch.save(checkpoint,out/'disposable_candidate.pt')
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
       'state':'post_consolidation','weights':consolidated['weights'],
       'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
       'frozen_lineage':data['probe_info']['lineage'],
       'margin_width':args.margin_width,'capacity_width':width,
       'train_ledger_mask':torch.as_tensor(consolidated['ledger_mask']),
       'train_acquired_step':torch.as_tensor(consolidated['acquired_step']),
       'train_margin_floor':torch.as_tensor(consolidated['floor']),
       'train_capture_sha256':capture,'capture_bound_router_only':True,
       'protected_original_passthrough':True,'promotion_authorized':False},
       out/'consolidated_start_candidate.pt')
    (out/'report.json').write_text(json.dumps(doc,indent=2,sort_keys=True,allow_nan=False)+'\n')
    for item in comp:print('V13_RESULT '+probe.canonical(item),flush=True)
    print('V13_COMPLETE '+probe.canonical({'selected':winner['arm'],
       'consolidation_accepted':stage_a_summary['accepted'],
       'report':str(out/'report.json'),'promotion':False}),flush=True)
    return doc


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--expanded-run',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--source-features',type=Path,required=True)
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--v11-run',type=Path,required=True)
    p.add_argument('--v12-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=180)
    p.add_argument('--consolidation-steps',type=int,default=90)
    p.add_argument('--eval-every',type=int,default=15)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--consolidation-lr',type=float,default=.0005)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--margin-width',type=int,default=32)
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
