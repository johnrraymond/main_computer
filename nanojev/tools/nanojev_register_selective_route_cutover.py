#!/usr/bin/env python3
"""NanoJev v3.1: staged selective route replacement with oracle-route diagnostics.

New trainer; leaves original v1.2/v2/v3 trainers and checkpoints untouched.
Stages: detector -> selector -> reconstruct -> calibrate -> direct/result.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_register_magnitude_route_cutover as v3

op, h, c, previous = v3.op, v3.h, v3.c, v3.previous
emit, atomic_json = v3.emit, v3.atomic_json
SCHEMA = 'nanojev.selective-magnitude-route-cutover.v3.1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_selective_route_cutover_v3_1'
STAGES = ('detector', 'selector', 'reconstruct', 'calibrate', 'direct', 'result')
TRAIN_STAGES = ('detector','selector','reconstruct','direct','result')


def make_signal(features, raw, base, candidates):
    norm_features=F.layer_norm(features.float(), (h.INPUT_WIDTH,))
    return torch.cat((norm_features, torch.tanh(raw.float()),
        torch.asinh(base).float()/15., candidates.flatten(1).float()/15.), dim=-1)


class SelectiveRouteNumeracy(nn.Module):
    """Independent detector, route selector and oracle-route reconstruction.

    The primary output is *exactly* the frozen source for every operand unless
    a calibrated high-confidence alternative route is selected. Inference never
    receives ground-truth routing labels.
    """
    def __init__(self,stage='continuity',rank=8,residual_scale=.25,op_width=96,width=128):
        super().__init__()
        self.source=op.ExecutableNumeracy(stage,rank,residual_scale,op_width)
        input_width=h.INPUT_WIDTH+2*h.HEADS_PER_REGISTER+2+26
        def trunk(n):
            return nn.Sequential(nn.LayerNorm(n,elementwise_affine=False),
                                 nn.Linear(n,width),nn.GELU())
        self.detector=nn.Sequential(trunk(input_width),nn.Linear(width,2))
        self.selector=nn.Sequential(trunk(input_width),nn.Linear(width,26))
        # Candidate-dependent reconstruction; each operand has its own signed
        # candidate, source estimate and one-hot target route (training only).
        self.reconstructor=nn.Sequential(trunk(input_width+13+1),nn.Linear(width,1))
        nn.init.zeros_(self.detector[-1].weight)
        nn.init.zeros_(self.detector[-1].bias)
        nn.init.zeros_(self.selector[-1].weight)
        nn.init.zeros_(self.selector[-1].bias)
        nn.init.zeros_(self.reconstructor[-1].weight)
        nn.init.zeros_(self.reconstructor[-1].bias)
        self.threshold=1.0  # fail-closed until calibration
        self.route_threshold=1.0
        self.freeze_source()

    def freeze_source(self):
        for p in self.source.parameters(): p.requires_grad_(False)
        self.source.eval()

    def train(self,mode=True):
        super().train(mode)
        self.source.eval()
        return self

    def freeze_for(self,phase):
        for name in ('detector','selector','reconstructor'):
            for p in getattr(self,name).parameters():p.requires_grad_(False)
        train_name={'detector':'detector','selector':'selector',
                    'reconstruct':'reconstructor','direct':'reconstructor',
                    'result':'reconstructor'}.get(phase)
        if train_name:
            for p in getattr(self,train_name).parameters():p.requires_grad_(True)
        self.freeze_source()

    def encode(self,features):
        with torch.no_grad():
            raw,op_logits=self.source(features)
            source_np=c.decode(raw.float().detach().cpu().numpy())
            base=torch.as_tensor(source_np,dtype=torch.float64,device=features.device)
            candidates=v3.RouteAwareNumeracy.expert_candidates(raw).detach()
            source_route=v3.RouteAwareNumeracy.route_classes(base)
        signal=make_signal(features,raw,base,candidates)
        detector_logits=self.detector(signal).double()
        selector_logits=self.selector(signal).reshape(-1,2,13).double()
        return signal,base,candidates,source_route,detector_logits,selector_logits,op_logits

    def reconstruct(self,signal,candidates,route):
        # Supports route [N,2]. Conditional expert selection is discrete;
        # gradients train only the fine reconstruction head, not the oracle route.
        route=route.long()
        candidate=candidates.gather(-1,route.unsqueeze(-1)).squeeze(-1)
        n=len(signal)
        base_signal=signal[:,None,:].expand(-1,2,-1)
        onehot=F.one_hot(route,13).float()
        values=torch.cat((base_signal,onehot,candidate.unsqueeze(-1).float()/15.),dim=-1)
        correction=self.reconstructor(values.reshape(n*2,-1)).reshape(n,2).double()
        reconstructed=torch.sinh(candidate+correction)
        return reconstructed,correction,candidate

    def infer_from_encoded(self,encoded,threshold=None,route_threshold=None):
        signal,base,candidates,source_route,detector,selector,op_logits=encoded
        threshold=self.threshold if threshold is None else float(threshold)
        route_threshold=self.route_threshold if route_threshold is None else float(route_threshold)
        dprob=detector.sigmoid()
        probs=selector.softmax(-1)
        confidence,new_route=probs.max(-1)
        replace=(dprob>=threshold)&(confidence>=route_threshold)&(new_route!=source_route)
        # Keep source values bit-for-bit when replacement is disallowed.
        alternate,correction,candidate=self.reconstruct(signal,candidates,new_route)
        predicted=torch.where(replace,alternate,base)
        return predicted,base,op_logits,{
            'replaced':replace,'source_route':source_route,'selected_route':new_route,
            'detector_prob':dprob,'selector_confidence':confidence,
            'alternate':alternate,'selected_candidate_asinh':candidate,
            'fine_correction':correction}

    def forward(self,features):
        return self.infer_from_encoded(self.encode(features))


def initial_report(data,net,device,threshold=None,route_threshold=None,batch_size=128,oracle=False):
    features,labels,operations,families=data
    results=[];sources=[];routes=[];wrong=[];replacements=[];oracle_predictions=[]
    detect_prob=[];selector_conf=[];candidate_log_mae=[];oracle_log_mae=[]
    was_training=net.training;net.eval()
    with torch.inference_mode():
        for i in range(0,len(features),batch_size):
            x=torch.as_tensor(features[i:i+batch_size],device=device,dtype=torch.float32)
            enc=net.encode(x)
            p,base,_,aux=net.infer_from_encoded(enc,threshold,route_threshold)
            true=torch.as_tensor(labels[i:i+batch_size],device=device,dtype=torch.long)
            target_route=v3.RouteAwareNumeracy.route_classes(true)
            results.append(v3.decode_int(p).cpu().numpy())
            sources.append(base.long().cpu().numpy())
            routes.append(aux['selected_route'].cpu().numpy())
            wrong.append((aux['source_route']!=target_route).cpu().numpy())
            replacements.append(aux['replaced'].cpu().numpy())
            detect_prob.append(aux['detector_prob'].cpu().numpy())
            selector_conf.append(aux['selector_confidence'].cpu().numpy())
            if oracle:
                oracle_pred,_,candidate=net.reconstruct(enc[0],enc[2],target_route)
                oracle_predictions.append(v3.decode_int(oracle_pred).cpu().numpy())
                candidate_log_mae.append((candidate-torch.asinh(true.double())).abs().cpu().numpy())
                oracle_log_mae.append((torch.asinh(oracle_pred)-torch.asinh(true.double())).abs().cpu().numpy())
    net.train(was_training)
    pred=np.concatenate(results);base=np.concatenate(sources)
    true=np.asarray(labels,dtype=np.int64);ops_list=list(operations)
    wrong=np.concatenate(wrong);replacement=np.concatenate(replacements)
    classes=v3.RouteAwareNumeracy.route_classes(torch.from_numpy(true)).numpy()
    selected=np.concatenate(routes)
    detect_score=np.concatenate(detect_prob)
    # ROC AUC is threshold-free and is defined only with both classes present.
    auc=None
    if wrong.any() and (~wrong).any():
        pos=detect_score[wrong]; neg=detect_score[~wrong]
        auc=float(((pos[:,None]>neg[None,:]).mean()+.5*(pos[:,None]==neg[None,:]).mean()))
    detected=detect_score>=.5
    true_pos=int((detected&wrong).sum());false_pos=int((detected&~wrong).sum())
    false_neg=int((~detected&wrong).sum())
    precision=true_pos/(true_pos+false_pos) if true_pos+false_pos else 0.
    recall=true_pos/(true_pos+false_neg) if true_pos+false_neg else 0.
    correct=previous.correctness(pred,true)
    correct_base=previous.correctness(base,true)
    numeric=np.array([op.OP_TO_PROGRAM[s] not in op.BOOL_PROGRAMS for s in ops_list])
    def result_hits(values):
        result=[]
        for (x,y),(tx,ty),name in zip(values,true,ops_list):
            program=op.OP_TO_PROGRAM[name]
            actual=op.exact_execute(program,int(tx),int(ty))
            estimate=op.exact_execute(program,int(x),int(y))
            result.append(bool(op.relative_pass([estimate],[actual],[program in op.BOOL_PROGRAMS])[0]))
        return np.array(result,dtype=bool)
    hit=result_hits(pred);source_hit=result_hits(base)
    correct_route=~wrong
    severe=(np.abs(np.arcsinh(true.astype(float))-np.arcsinh(base.astype(float)))>=np.log(2))
    def frac(mask,vals):return float(np.mean(vals[mask])) if mask.any() else None
    output={
      'rows':len(true),'numeric_rows':int(numeric.sum()),
      'threshold':threshold if threshold is not None else net.threshold,
      'route_threshold':route_threshold if route_threshold is not None else net.route_threshold,
      'numeric_result_5pct_pass_rate':frac(numeric,hit),
      'source_numeric_result_5pct_pass_rate':frac(numeric,source_hit),
      'result_5pct_pass_rate':float(hit.mean()),
      'source_result_5pct_pass_rate':float(source_hit.mean()),
      'joint_xy_5pct_pass_rate':float(correct.all(axis=1).mean()),
      'source_joint_xy_5pct_pass_rate':float(correct_base.all(axis=1).mean()),
      'global_log_mae':float(np.abs(np.log1p(np.abs(pred.astype(float)))-np.log1p(np.abs(true.astype(float)))).mean()),
      'source_wrong_route_operands':int(wrong.sum()),
      'source_wrong_route_rows':int(wrong.any(-1).sum()),
      'selected_route_accuracy_on_source_wrong':frac(wrong,selected==classes),
      'detector_roc_auc':auc,'detector_precision_at_half':precision,
      'detector_recall_at_half':recall,
      'selected_route_top1_accuracy':float((selected==classes).mean()),
      'wrong_route_operand_5pct_recovery':frac(wrong,correct),
      'source_correct_route_operand_5pct_recovery':frac(correct_route,correct),
      'source_correct_route_operand_5pct_baseline':frac(correct_route,correct_base),
      'correct_route_retention':frac(correct_route,~replacement),
      'incorrect_route_replacement_rate':frac(correct_route,replacement),
      'wrong_route_replacement_rate':frac(wrong,replacement),
      'replacements':int(replacement.sum()),
      'severe_source_error_operands':int(severe.sum()),
      'severe_operand_5pct_recovery':frac(severe,correct),
      'detector_source_wrong_avg':frac(wrong,np.concatenate(detect_prob)),
      'detector_source_correct_avg':frac(correct_route,np.concatenate(detect_prob)),
      'selector_confidence_source_wrong_avg':frac(wrong,np.concatenate(selector_conf)),
    }
    if oracle:
        pred_o=np.concatenate(oracle_predictions)
        oc=previous.correctness(pred_o,true)
        output.update(oracle_route_joint_xy_5pct=float(oc.all(1).mean()),
                      oracle_route_operand_5pct=float(oc.mean()),
                      oracle_route_wrong_source_5pct=frac(wrong,oc),
                      oracle_route_candidate_asinh_mae=float(np.concatenate(candidate_log_mae).mean()),
                      oracle_route_reconstructed_asinh_mae=float(np.concatenate(oracle_log_mae).mean()))
    if len(true):
        output['examples']=[dict(true_x=int(true[i,0]),true_y=int(true[i,1]),
          source_x=int(base[i,0]),source_y=int(base[i,1]),
          predicted_x=int(pred[i,0]),predicted_y=int(pred[i,1]),
          selected_x_route=int(selected[i,0]),selected_y_route=int(selected[i,1]),
          replaced_x=bool(replacement[i,0]),replaced_y=bool(replacement[i,1]))
          for i in range(min(5,len(true)))]
    return output


def data_subset(data,indices):
    x,y,opnames,families=data
    return (x[indices],y[indices], [opnames[int(k)] for k in indices],
            [families[int(k)] for k in indices])


def validation_partition(data):
    """Calibration and model-selection rows never overlap. Test stays sealed."""
    rng=np.random.default_rng(3102026)
    indices=rng.permutation(len(data[0]))
    split=len(indices)//2
    if split<1:raise ValueError('SELECTIVE_TOO_FEW_VALIDATION_EXAMPLES')
    return data_subset(data,indices[:split]),data_subset(data,indices[split:])


def calibrate(net,data,device,min_retention=.99):
    """Conservative grid search; never choose an option worse than keeping source.

    Thresholds are selected ONLY from calibration rows, never held-out scoring.
    Candidate decisions preserve source-correct route on >= min_retention rows.
    Any tie/non-improvement falls back to no replacements.
    """
    baseline=initial_report(data,net,device,1.01,1.01)
    best=(baseline['numeric_result_5pct_pass_rate'],baseline['joint_xy_5pct_pass_rate'],0)
    best_values=(1.01,1.01)
    trace=[]
    for d in (.50,.65,.75,.85,.90,.95,.975,.99,.995):
        for s in (.50,.65,.75,.85,.90,.95,.975,.99,.995):
            m=initial_report(data,net,device,d,s)
            if (m['correct_route_retention'] is None or
                m['correct_route_retention']<min_retention or
                # We are testing route repair, not arbitrary improvements that
                # come from perturbing correctly routed operands.
                not (m['wrong_route_operand_5pct_recovery'] is not None and
                     m['wrong_route_operand_5pct_recovery']>
                       (baseline['wrong_route_operand_5pct_recovery'] or 0.))):
                continue
            value=(m['numeric_result_5pct_pass_rate'],m['joint_xy_5pct_pass_rate'],-m['replacements'])
            trace.append(dict(detector=d,selector=s,numeric=value[0],retention=m['correct_route_retention'],replacements=m['replacements']))
            if value>best:
                best=value;best_values=(d,s)
    return {'detector_threshold':best_values[0],'selector_threshold':best_values[1],
            'calibration_baseline':baseline['numeric_result_5pct_pass_rate'],
            'calibration_selected':best[0],
            'min_retention':min_retention,'safe_candidate_count':len(trace),
            'chosen_is_source_only':best_values==(1.01,1.01)}


def supervised_loss(net,enc,labels,ops_list,phase,args):
    signal,base,candidates,source_route,detect,selector,op_logits=enc
    target=v3.RouteAwareNumeracy.route_classes(labels)
    wrong=target!=source_route
    if phase=='detector':
        # Training-only rare-class weighting, no truth in predictions.
        losses=F.binary_cross_entropy_with_logits(detect,wrong.double(),reduction='none',
                pos_weight=torch.full((2,),args.detector_pos_weight,device=detect.device,dtype=torch.float64))
        return losses.mean(),{'detector_bce':float(losses.mean().detach()),'wrong_rate':float(wrong.float().mean())}
    if phase=='selector':
        ce=F.cross_entropy(selector.reshape(-1,13),target.reshape(-1),reduction='none').reshape_as(detect)
        if wrong.any():
            loss=ce[wrong].mean()
        else:
            loss=ce.sum()*0
        return loss,{'wrong_route_ce':float(loss.detach()),'wrong_rows':int(wrong.sum())}
    if phase=='reconstruct':
        oracle,delta,candidate=net.reconstruct(signal,candidates,target)
        target_asinh=torch.asinh(labels.double())
        loss_raw=F.smooth_l1_loss(torch.asinh(oracle),target_asinh,reduction='none',beta=.12)
        weight=torch.where(wrong,torch.full_like(loss_raw,args.reconstruct_wrong_weight),torch.ones_like(loss_raw))
        loss=(loss_raw*weight).sum()/weight.sum()
        return loss,{'oracle_asinh_loss':float(loss.detach()),'wrong_rate':float(wrong.float().mean())}
    # Integration: train numerical reconstruction only for activated, correctly
    # routed replacements. Do not try to refine a false route into a true number.
    prediction,old,_,aux=net.infer_from_encoded(enc)
    selected_good=aux['replaced']&(aux['selected_route']==target)
    if selected_good.any():
        direct=F.smooth_l1_loss(torch.asinh(prediction[selected_good]),
                    torch.asinh(labels.double()[selected_good]),beta=.12)
    else:
        direct=prediction.sum()*0
    result=prediction.sum()*0
    if phase=='result' and aux['replaced'].any():
        # Ground truth only enters the target loss; no oracle route at inference.
        targets=torch.as_tensor([op.exact_execute(op.OP_TO_PROGRAM[name],int(x),int(y))
                     for name,(x,y) in zip(ops_list,labels.tolist())],
                     dtype=torch.float64,device=labels.device)
        losses=op._per_candidate_losses(prediction,labels,targets,
                                [op.OP_TO_PROGRAM[name] for name in ops_list])
        ids=torch.as_tensor([op.OP_ID[name] for name in ops_list],device=labels.device)
        chosen=losses.gather(1,ids[:,None]).squeeze(-1)
        weighted=aux['replaced'].any(-1)
        if weighted.any():result=chosen[weighted].mean()
    total=direct+args.result_weight*result
    return total,{'integrated_direct':float(direct.detach()),'integrated_result':float(result.detach()),
                  'active_operands':int(aux['replaced'].sum()),'correct_active':int(selected_good.sum())}


def parser():
    p=v3.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT,steps=1000)
    p.add_argument('--stage',choices=STAGES,default='detector')
    p.add_argument('--selective-width',type=int,default=128)
    p.add_argument('--detector-pos-weight',type=float,default=16.)
    p.add_argument('--reconstruct-wrong-weight',type=float,default=4.)
    p.add_argument('--min-correct-route-retention',type=float,default=.99)
    return p


def args_config(args,benchmark):
    conf=v3.config_for(args,benchmark)
    # Stage-specific controls are recorded, but steps are deliberately not,
    # allowing --resume to extend the number of optimizer updates.
    conf.update(schema=SCHEMA,stage=args.stage,selective_width=args.selective_width,
       detector_pos_weight=args.detector_pos_weight,
       reconstruct_wrong_weight=args.reconstruct_wrong_weight,
       min_correct_route_retention=args.min_correct_route_retention)
    conf.pop('steps',None)
    return conf



def stage_score(stage,metrics):
    """Checkpoints optimize the objective actually trained in each stage."""
    if stage=='detector':
        return (float(metrics.get('detector_roc_auc') or 0.),
                float(metrics.get('detector_recall_at_half') or 0.))
    if stage=='selector':
        return (float(metrics.get('selected_route_accuracy_on_source_wrong') or 0.),
                float(metrics.get('selected_route_top1_accuracy') or 0.))
    if stage=='reconstruct':
        return (float(metrics.get('oracle_route_wrong_source_5pct') or 0.),
                -float(metrics.get('oracle_route_reconstructed_asinh_mae') or 0.))
    return (float(metrics['numeric_result_5pct_pass_rate']),
            float(metrics['joint_xy_5pct_pass_rate']))

def save_ckpt(path,net,optimizer,step,config,stream,replay,identity,source,calibration=None,best=None,complete=False):
    obj=dict(schema=SCHEMA,stage=config['stage'],step=step,config=config,
      network=net.state_dict(),optimizer=optimizer.state_dict() if optimizer is not None else None,
      random_stream=stream.state_dict(),replay=replay.state_dict(),source_cutover=source,
      backbone_identity=identity,calibration=calibration,best=best,complete=bool(complete),
      torch_rng=torch.get_rng_state())
    if torch.cuda.is_available():obj['cuda_rng']=torch.cuda.get_rng_state_all()
    temp=path.with_name(path.name+'.partial')
    torch.save(obj,temp);os.replace(temp,path)


def load_ckpt(path,net,identity,source_digest):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if (ck['schema']!=SCHEMA or ck['backbone_identity']!=identity or
        ck['source_cutover']['sha256']!=source_digest):
        raise RuntimeError('SELECTIVE_CHECKPOINT_IDENTITY_OR_SOURCE_DRIFT')
    net.load_state_dict(ck['network'],strict=True)
    net.freeze_source()
    return ck


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:self_test();return 0
    if args.init_checkpoint:raise ValueError('SELECTIVE_REQUIRES_SOURCE_CHECKPOINT')
    if args.constraint_stage!='continuity':raise ValueError('SELECTIVE_REQUIRES_CONTINUITY')
    if (min(args.selective_width,args.batch_size,args.buffer_capacity,args.per_expert_batch,
           args.replay_updates,args.threads,args.val_examples,args.eval_every,args.report_every,
           args.checkpoint_every,args.steps)<1 or args.detector_pos_weight<=0 or
        args.reconstruct_wrong_weight<=0 or not 0<args.min_correct_route_retention<=1 or
        args.result_weight<0):raise ValueError('SELECTIVE_INVALID_CONFIG')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('SELECTIVE_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark,tasks=previous.read_benchmark(args.v3_root)
    b_root=args.v3_root.expanduser().resolve()
    identity=json.loads((b_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':raise RuntimeError('SELECTIVE_EXPECTS_FROZEN_QWEN')
    origin=args.source_checkpoint.expanduser().resolve()
    source_digest=hashlib.sha256(origin.read_bytes()).hexdigest()
    base_root=args.results_root.expanduser().resolve()
    root=base_root/args.stage
    if any(root==path or path in root.parents or root in path.parents for path in (origin,b_root)):
        raise RuntimeError('SELECTIVE_OUTPUT_COLLIDES_WITH_SOURCE')
    config=args_config(args,benchmark)
    root.mkdir(parents=True,exist_ok=True)
    conf=root/'run_config.json'
    torch.manual_seed(args.seed)
    net=SelectiveRouteNumeracy(args.constraint_stage,args.rank,args.residual_scale,
                                args.op_width,args.selective_width).to(device)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=op.OperationReplay(args.buffer_capacity,args.seed+17)
    latest=root/'latest.pt'; bestpath=root/'best.pt'
    if args.stage=='detector': parent=None
    elif args.stage=='selector':parent=base_root/'detector'/'latest.pt'
    elif args.stage=='reconstruct':parent=base_root/'selector'/'latest.pt'
    elif args.stage=='calibrate':parent=base_root/'reconstruct'/'latest.pt'
    else:parent=base_root/'calibrate'/'latest.pt'
    net.freeze_for(args.stage)
    trainable=[p for p in net.parameters() if p.requires_grad]
    optimizer=torch.optim.AdamW(trainable,lr=args.lr,weight_decay=args.weight_decay) if trainable else None
    if args.resume or args.eval_only:
        checkpoint=bestpath if args.eval_only and args.eval_checkpoint=='best' else latest
        if not checkpoint.exists():raise FileNotFoundError('SELECTIVE_RESUME_CHECKPOINT_MISSING')
        ck=load_ckpt(checkpoint,net,identity,source_digest)
        if ck['config']!=config:raise RuntimeError('SELECTIVE_CONFIG_DRIFT')
        stream.load_state_dict(ck['random_stream']);replay.load_state_dict(ck['replay'])
        if optimizer and ck['optimizer']:optimizer.load_state_dict(ck['optimizer'])
        if 'torch_rng' in ck:torch.set_rng_state(ck['torch_rng'].cpu().to(torch.uint8))
        if torch.cuda.is_available() and 'cuda_rng' in ck:
            torch.cuda.set_rng_state_all([r.cpu().to(torch.uint8) for r in ck['cuda_rng']])
        step=ck['step'];source=ck['source_cutover'];calibration=ck['calibration'];best=ck['best']
        emit('selective_resumed',stage=args.stage,step=step)
    else:
        if latest.exists() or (conf.exists() and any(root.glob('*.pt'))):
            raise RuntimeError('SELECTIVE_USE_--resume')
        if parent is None:
            source=v3.cutover(origin,net,stream,replay,identity,args)
            calibration=None;best=None
        else:
            if not parent.exists():raise FileNotFoundError('SELECTIVE_PREVIOUS_STAGE_REQUIRED:'+str(parent))
            ck=load_ckpt(parent,net,identity,source_digest)
            if not ck.get('complete'):
                raise RuntimeError('SELECTIVE_PARENT_STAGE_INCOMPLETE')
            # Select the previous stage's best *weights*, but continue the
            # completed stage's replay/generator stream. This avoids inheriting
            # late-stage overfitting without discarding generated examples.
            if args.stage in ('selector','reconstruct','calibrate'):
                chosen=parent.with_name('best.pt')
                if not chosen.is_file():
                    raise FileNotFoundError('SELECTIVE_PARENT_BEST_MISSING')
                load_ckpt(chosen,net,identity,source_digest)
            stream.load_state_dict(ck['random_stream']);replay.load_state_dict(ck['replay'])
            source=ck['source_cutover'];calibration=ck['calibration'];best=None
            if args.stage in ('direct','result'):
                if not calibration:raise RuntimeError('SELECTIVE_CALIBRATION_REQUIRED')
                net.threshold=calibration['detector_threshold']
                net.route_threshold=calibration['selector_threshold']
        step=0
        if args.stage=='detector':
            init=initial_report(previous.load_eval(b_root,benchmark,tasks,'val',args.val_examples),net,device)
            if (init['numeric_result_5pct_pass_rate']!=init['source_numeric_result_5pct_pass_rate'] or
                init['joint_xy_5pct_pass_rate']!=init['source_joint_xy_5pct_pass_rate']):
                raise RuntimeError('SELECTIVE_STEP_ZERO_PARITY_FAILURE')
            emit('selective_step_zero',**init)
        save_ckpt(latest,net,optimizer,0,config,stream,replay,identity,source,calibration,best)
        emit('selective_initialized',stage=args.stage,source_sha256=source_digest,
             trainable_parameters=sum(p.numel() for p in trainable))
    # Each phase config is immutable even when the run extends steps.
    if conf.exists():
        if json.loads(conf.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('SELECTIVE_CONFIG_DRIFT')
    else:atomic_json(conf,config)
    data=previous.load_eval(b_root,benchmark,tasks,'val',args.val_examples)
    calib,heldout=validation_partition(data)
    # Calibration sees calibration split ONLY; the other half is for model selection.
    if args.stage=='calibrate' and not args.eval_only:
        if args.resume:raise ValueError('SELECTIVE_CALIBRATE_ONCE')
        calibration=calibrate(net,calib,device,args.min_correct_route_retention)
        net.threshold=calibration['detector_threshold'];net.route_threshold=calibration['selector_threshold']
        output=initial_report(heldout,net,device,oracle=True)
        # Independent validation veto; no test/control rows are consulted.
        # After veto, these held-out rows are part of model selection and
        # cannot be reported as an untouched final performance estimate.
        veto=[]
        if output['correct_route_retention'] is not None and output['correct_route_retention']<args.min_correct_route_retention:
            veto.append('SOURCE_CORRECT_ROUTE_REPLACEMENT_BUDGET_EXCEEDED')
        if output['numeric_result_5pct_pass_rate']<output['source_numeric_result_5pct_pass_rate']:
            veto.append('HELDOUT_NUMERIC_RESULT_REGRESSION')
        if output['joint_xy_5pct_pass_rate']<output['source_joint_xy_5pct_pass_rate']:
            veto.append('HELDOUT_JOINT_OPERAND_REGRESSION')
        if veto:
            calibration['rejected_thresholds']=(calibration['detector_threshold'],calibration['selector_threshold'])
            calibration['detector_threshold']=1.01
            calibration['selector_threshold']=1.01
            calibration['chosen_is_source_only']=True
            net.threshold=1.01;net.route_threshold=1.01
        calibration['heldout_veto_reasons']=veto
        calibration['heldout_pre_veto']=output
        save_ckpt(latest,net,None,0,config,stream,replay,identity,source,calibration,best,complete=True)
        emit('selective_calibrated',calibration=calibration,heldout=output)
        return 0
    if args.eval_only:
        if calibration:
            net.threshold=calibration['detector_threshold'];net.route_threshold=calibration['selector_threshold']
        for split in ('val','test','control'):
            sample=previous.load_eval(b_root,benchmark,tasks,split)
            emit('selective_offline_evaluation',stage=args.stage,split=split,
                 step=step,**initial_report(sample,net,device,oracle=True))
        return 0
    if args.stage=='calibrate':return 0
    if args.stage in ('direct','result') and calibration:
        net.threshold=calibration['detector_threshold'];net.route_threshold=calibration['selector_threshold']
    if not best:
        measure=initial_report(heldout,net,device,oracle=True)
        score=stage_score(args.stage,measure)
        best=dict(numeric=measure['numeric_result_5pct_pass_rate'],
                  joint=measure['joint_xy_5pct_pass_rate'],score=score,step=0)
        save_ckpt(bestpath,net,optimizer,0,config,stream,replay,identity,source,calibration,best)
        emit('selective_initial_heldout',stage=args.stage,**measure)
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,info=cassette.load_source('qwen',args)
    if any(str(info.get(key))!=str(identity['model_info'].get(key)) for key in
           ('resolved_commit','source','dtype')):
        raise RuntimeError('SELECTIVE_BACKBONE_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,b_root,tasks,args.max_prompt_tokens)
    started=time.monotonic()
    for k in range(step+1,args.steps+1):
        rows=stream.batch(args.batch_size)
        activations=previous.extract_batched(torch,frozen,tokenizer,
                      [row['prompt'] for row in rows],source_device,args.max_prompt_tokens)
        if activations.requires_grad:raise RuntimeError('SELECTIVE_FROZEN_BACKBONE_GRAD')
        replay.add(activations,rows)
        active=0;part={};loss_value=0.
        for _ in range(args.replay_updates):
            feat,nums,names=replay.sample_labeled(args.per_expert_batch)
            targets=torch.as_tensor(nums,dtype=torch.long,device=device)
            net.train()
            enc=net.encode(feat.to(device,dtype=torch.float32))
            loss,part=supervised_loss(net,enc,targets,names,args.stage,args)
            optimizer.zero_grad(set_to_none=True)
            if loss.requires_grad:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(trainable,1.)
                optimizer.step()
                active+=1
            loss_value=float(loss.detach())
        if k%args.report_every==0 or k==args.steps:
            emit('selective_train_progress',stage=args.stage,step=k,
              loss=loss_value,optimizer_updates=active,
              source_step=source['source_step'],
              seen_pairs=len(stream.seen),new_pairs=len(stream.seen)-source['source_seen_pairs'],
              elapsed_seconds=round(time.monotonic()-started,2),**part)
        if k%args.eval_every==0 or k==args.steps:
            metrics=initial_report(heldout,net,device,oracle=True)
            emit('selective_heldout_evaluation',stage=args.stage,step=k,**metrics)
            score=stage_score(args.stage,metrics)
            if score>tuple(best['score']):
                best.update(numeric=metrics['numeric_result_5pct_pass_rate'],
                            joint=metrics['joint_xy_5pct_pass_rate'],score=score,step=k)
                save_ckpt(bestpath,net,optimizer,k,config,stream,replay,identity,source,calibration,best)
                emit('selective_best',stage=args.stage,step=k,best=best)
        if k%args.checkpoint_every==0 or k==args.steps:
            save_ckpt(latest,net,optimizer,k,config,stream,replay,identity,source,calibration,best,
                      complete=(k==args.steps))
    emit('selective_complete',stage=args.stage,step=args.steps,best=best)
    return 0


def self_test():
    torch.manual_seed(31)
    net=SelectiveRouteNumeracy(op_width=8,width=8)
    x=torch.randn(3,h.INPUT_WIDTH)
    p,base,_,aux=net(x)
    assert torch.equal(v3.decode_int(p),base.long())
    assert not aux['replaced'].any()
    assert all(not p.requires_grad for p in net.source.parameters())
    # Replacement path chooses ONE expert, not a mixture of the 13 routes.
    net.threshold=.0;net.route_threshold=.0
    with torch.no_grad():
        net.selector[-1].bias[10]=100
        net.detector[-1].bias[:]=5
    q,base,_,aux=net(x)
    assert torch.isfinite(q).all()
    assert aux['selected_route'].shape==(3,2)
    assert (q!=base).any()
    emit('selective_self_test_pass',step_zero_exact=True,fail_closed=True,
         source_frozen=True,hard_candidate_replacement=True)


if __name__=='__main__':raise SystemExit(main())
