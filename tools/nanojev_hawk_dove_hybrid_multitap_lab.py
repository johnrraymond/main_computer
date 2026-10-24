#!/usr/bin/env python3
"""Frozen-Gaussian multi-tap HybridLink diagnostic; no existing trainer mutation.

Readouts probe early/late/head/bank activations. Train sidecar circuits which
reinject generated latent thoughts before the frozen second backbone layer;
compare 1/2/4 recurrent passes with an equally trained 4-pass no-reentry control.
TRAIN only for fits; DEV model selection; TEST only with --final-test.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab

SCHEMA = 'nanojev.hawk-dove.hybrid-multitap-feedback-lab.v1'
TAPS = ('early','late','prehead','bank')
SPEC = {'early':('early',), 'late':('late',), 'prehead':('prehead',),
        'bank':('bank',), 'early+late':('early','late'),
        'late+prehead':('late','prehead'), 'all':TAPS}
DEFAULT_ARMS = tuple(SPEC) + ('all_depth1','all_depth2','all_nofeedback')


def numeric_location(truth,domain):
    return torch.stack([(truth[:,j]-domain[j][0])/(domain[j][1]-domain[j][0])
                        for j in range(2)],dim=-1)


def direct_decode(location,domain):
    return torch.stack([location[:,j]*(domain[j][1]-domain[j][0])+domain[j][0]
                        for j in range(2)],dim=-1)


class ReferenceTaps(nn.Module):
    """Frozen, real pretrained activations; no oracle numeric values as inputs."""
    def __init__(self,reference,bank_groups=16):
        super().__init__()
        self.reference=reference
        for p in self.reference.parameters():p.requires_grad_(False)
        self.reference.eval()
        self.dim=reference.hidden
        self.bank_groups=bank_groups
        self.dims={'early':reference.hidden,'late':reference.hidden,'prehead':2,
                   'bank':2*bank_groups}
        self.register_buffer('groups',torch.floor(reference.centers*bank_groups).long().clamp(0,bank_groups-1))

    def train(self,mode=True):
        super().train(mode)
        self.reference.eval()
        return self

    def early(self,x):
        return F.silu(self.reference.encoder[0](x))

    def late(self,first):
        return F.silu(self.reference.encoder[2](first))

    def taps(self,first,late,names=TAPS):
        out={}
        if 'early' in names:out['early']=first
        if 'late' in names:out['late']=late
        if 'prehead' in names or 'bank' in names:
            logits=self.reference.encoder[4](late)
            if 'prehead' in names:out['prehead']=logits
            if 'bank' in names:
                loc=logits.sigmoid()
                ref=self.reference
                sigma=ref.log_proposal_sigma.exp().clamp(min=.3,max=20.)
                delta=(ref.centers.reshape(1,1,-1)-loc.unsqueeze(-1))*ref.bins
                weights=F.softmax(-.5*(delta/sigma.reshape(1,2,1)).square()+ref.bank_log_gains,dim=-1)
                mass=weights.new_zeros(len(weights),2,self.bank_groups)
                mass.scatter_add_(2,self.groups.reshape(1,1,-1).expand(len(weights),2,-1),weights)
                out['bank']=mass.flatten(1)
        return out


class MultitapSidecar(nn.Module):
    """HybridLink generates hidden tokens; reinjection happens before encoder[2].

    nofeedback uses the same modules and 4 sidecar steps, but only injects the
    last token (one backbone re-entry). No X/Y decoder appears in this loop.
    """
    def __init__(self,reference,taps=TAPS,heads=4,layers=1,nofeedback=False):
        super().__init__()
        self.source=ReferenceTaps(reference)
        self.selected=tuple(taps)
        self.nofeedback=bool(nofeedback)
        h=self.source.dim
        # All arms instantiate the same projections, core, link and readout.
        self.projections=nn.ModuleDict({name:nn.Sequential(nn.LayerNorm(dim),nn.Linear(dim,h))
                                       for name,dim in self.source.dims.items()})
        self.fuse_logits=nn.Parameter(torch.zeros(len(TAPS)))
        self.core=v4.CausalMemoryCore(h,heads,layers,max_tokens=9)
        self.link=v4.ResidualHybridLink(h)
        self.inject=nn.Linear(h,h,bias=False)
        nn.init.zeros_(self.inject.weight)
        self.answer=nn.Linear(h,2)
        with torch.no_grad():
            self.answer.weight.copy_(reference.encoder[4].weight)
            self.answer.bias.copy_(reference.encoder[4].bias)

    def _fuse(self,tap_values,removed=()):
        indexes=[TAPS.index(name) for name in self.selected]
        w=F.softmax(self.fuse_logits[indexes],dim=0)
        chunks=[]
        for k,name in enumerate(self.selected):
            encoded=self.projections[name](tap_values[name])
            if name in removed:encoded=torch.zeros_like(encoded)
            chunks.append(w[k]*encoded)
        return torch.stack(chunks).sum(dim=0)

    def forward(self,features,depth=4,removed=(),replacement=None,return_trace=False):
        if depth not in (0,1,2,4):raise ValueError('MULTITAP_UNSUPPORTED_DEPTH')
        s=self.source
        first=s.early(features)
        original_second=s.late(first)
        if depth==0:
            loc=self.answer(original_second).sigmoid()
            return (loc,[]) if return_trace else loc
        second=original_second
        history=[]; thoughts=[]
        for index in range(depth):
            if self.nofeedback:
                state_taps=s.taps(first,original_second,self.selected)
            else:
                state_taps=s.taps(first if index==0 else state_first,second,self.selected)
            if replacement:
                for key,value in replacement.items():
                    if key in state_taps:
                        if value.shape!=state_taps[key].shape:
                            raise ValueError('MULTITAP_REPLACEMENT_SHAPE_DRIFT')
                        state_taps[key]=value
            input_token=self._fuse(state_taps,removed)
            # Accumulated thought tokens and the current backbone observation.
            hidden=self.core(torch.stack(history+[input_token],dim=1))
            thought=self.link(hidden)
            thoughts.append(thought)
            history.append(thought)
            state_first=first+self.inject(thought)
            if not self.nofeedback or index==depth-1:
                second=s.late(state_first)
        loc=self.answer(second).sigmoid()
        return (loc,thoughts) if return_trace else loc


def linear_probes(source,splits,ridge=.01,max_rows=4096):
    """TRAIN-only ridge fits on independent examples; DEV independent matrices."""
    def observations(data):
        idx=[i for i,row in enumerate(data['rows'])
             if row['tier']==blind.TIERS[0] and row['mask']==0][:max_rows]
        if not idx:raise RuntimeError('MULTITAP_NO_UNIQUE_INSTANCES')
        x=data['features'][idx]
        with torch.no_grad():
            first=source.early(x);taps=source.taps(first,source.late(first))
        targets={'operands':x[:,:6],'products':v4.stage_values(x),
                 'partial_sums':torch.stack((v4.stage_values(x)[:,0]+v4.stage_values(x)[:,1],
                                             v4.stage_values(x)[:,2]+v4.stage_values(x)[:,3]),dim=1)}
        return ({name:v.detach().cpu().double() for name,v in taps.items()},
                {name:y.detach().cpu().double() for name,y in targets.items()})
    train,train_targets=observations(splits['train'])
    dev,dev_targets=observations(splits['dev'])
    result={}
    for name,chosen in SPEC.items():
        a=torch.cat([train[k] for k in chosen],dim=1)
        b=torch.cat([dev[k] for k in chosen],dim=1)
        mu=a.mean(0,keepdim=True);std=a.std(0,unbiased=False,keepdim=True).clamp_min(1e-6)
        a=torch.cat(((a-mu)/std,torch.ones(len(a),1,dtype=a.dtype)),dim=1)
        b=torch.cat(((b-mu)/std,torch.ones(len(b),1,dtype=b.dtype)),dim=1)
        result[name]={}
        for target in train_targets:
            y=train_targets[target];yt=dev_targets[target]
            center=y.mean(0,keepdim=True);scale=y.std(0,unbiased=False,keepdim=True).clamp_min(1e-6)
            yn=(y-center)/scale
            # Stable regularized fit. Ridge never sees DEV labels.
            gram=a.T@a+ridge*torch.eye(a.shape[1],dtype=a.dtype)
            coef=torch.linalg.solve(gram,a.T@yn)
            pred=(b@coef)*scale+center
            residual=pred-yt
            denom=((yt-yt.mean(dim=0,keepdim=True))**2).sum().clamp_min(1e-12)
            result[name][target]={'DEV_R2':float(1-residual.square().sum()/denom),
                                  'DEV_normalized_MAE':float((residual.abs()/scale).mean()),
                                  'TRAIN_instances':len(a),'DEV_instances':len(b)}
    return result


def paired_gain(correct,control,split,tolerance):
    truth=split['numeric'].detach().cpu()
    grouped={}
    for i,row in enumerate(split['rows']):
        key=(row['tier'],row['instance'])
        good_c=bool(((correct[i]-truth[i]).abs()<=tolerance*truth[i].abs()).all())
        good_b=bool(((control[i]-truth[i]).abs()<=tolerance*truth[i].abs()).all())
        grouped.setdefault(key,([],[]))
        grouped[key][0].append(good_c);grouped[key][1].append(good_b)
    rescued=sum(all(v[0]) and not all(v[1]) for v in grouped.values())
    regressed=sum(all(v[1]) and not all(v[0]) for v in grouped.values())
    return {'rescued':rescued,'regressed':regressed,'net':rescued-regressed,
            'absolute_gain':(rescued-regressed)/len(grouped)}


@torch.no_grad()
def predictions(model,split,domain,batch,depth=4,removed=(),replacements=None):
    model.eval()
    output=[]
    for start in range(0,len(split['features']),batch):
        x=split['features'][start:start+batch]
        local={k:v[start:start+batch] for k,v in (replacements or {}).items()}
        output.append(direct_decode(model(x,depth,removed,replacement=local),domain).cpu())
    return torch.cat(output)


def evaluation(model,split,domain,batch,tolerance,depth=4,removed=(),replacements=None):
    pred=predictions(model,split,domain,batch,depth,removed,replacements)
    return lab.strict_metrics(pred,split,tolerance)


@torch.no_grad()
def intervention_report(model,dev,domain,batch,tolerance):
    correct=predictions(model,dev,domain,batch,4)
    baseline=lab.strict_metrics(correct,dev,tolerance)
    out={}
    # Each tap is removed independently. Contrast at fixed model/checkpoint.
    for tap in model.selected:
        ablate=predictions(model,dev,domain,batch,4,removed=(tap,))
        out['zero_'+tap]={'score':lab.strict_metrics(ablate,dev,tolerance),
                         'paired':paired_gain(correct,ablate,dev,tolerance),
                         'mean_absolute_xy_change':float((correct-ablate).abs().mean())}
    # True distinct-instance shuffle across the entire DEV split.
    x=dev['features']
    initial=[]
    for part in x.split(batch):
        first=model.source.early(part)
        initial.append(model.source.taps(first,model.source.late(first)))
    for tap in model.selected:
        tensor=torch.cat([chunk[tap] for chunk in initial])
        try:
            donors=v4.distinct_instance_thought_donors(x,tensor.unsqueeze(1),dev['rows'])
        except ValueError as exc:
            # A compressed bank-mass tap may be indistinguishable for otherwise
            # distinct products. Never claim it was successfully shuffled.
            if 'HYBRID_V4_SHUFFLE_INDISTINGUISHABLE_TOKENS' not in str(exc):
                raise
            out['shuffle_'+tap]={'status':'UNVERIFIED_IDENTICAL_TAP_VECTORS',
                                 'reason':str(exc)}
            continue
        shuffled=tensor.index_select(0,donors)
        if bool(torch.equal(shuffled,tensor)):
            raise RuntimeError('MULTITAP_SHUFFLE_IDENTICAL_TAP')
        changed=predictions(model,dev,domain,batch,4,replacements={tap:shuffled})
        out['shuffle_'+tap]={'score':lab.strict_metrics(changed,dev,tolerance),
                            'paired':paired_gain(correct,changed,dev,tolerance),
                            'mean_absolute_xy_change':float((correct-changed).abs().mean())}
    return {'correct':baseline,'interventions':out,'shuffle_distinct_instance':True}


def identity(args,source,run_dir,config):
    keys=('arms','steps','probe_every','batch_size','eval_batch_size','lr','seed',
          'heads','layers','tolerance','cpu_threads','min_gain','ridge','final_test')
    return {'schema':SCHEMA,'source_sha256':probe.sha256(source),
            'teacher_sha256':probe.sha256(run_dir/args.checkpoint),
            'source_identity':config['source_identity'],
            'matrix_hashes':config['matrix_hashes'],
            'options':{k:getattr(args,k) for k in keys},
            'split_policy':'TRAIN gradients and linear fits; DEV promotion; TEST optional after freeze',
            'inference_policy':'six visible features only; no oracle products, sums or X/Y fed into sidecar',
            'feedback_site':'first frozen reference hidden layer -> second frozen reference hidden layer'}


def train_arm(key,reference,splits,domain,config,args,root,ident):
    control=key.endswith('_nofeedback')
    train_depth=1 if key=='all_depth1' else 2 if key=='all_depth2' else 4
    chosen=SPEC['all'] if key.startswith('all_') else SPEC[key]
    torch.manual_seed(args.seed)  # identical initial weights across arms
    model=MultitapSidecar(reference,chosen,args.heads,args.layers,control).to(args.device)
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=args.lr,weight_decay=0.)
    rng=torch.Generator(device='cpu').manual_seed(args.seed+71)
    directory=root/key;directory.mkdir(parents=True,exist_ok=True)
    latest=directory/'latest.pt';best=directory/'best.pt';done=directory/'done.json'
    if args.resume and done.exists():
        result=json.loads(done.read_text(encoding='utf-8'))
        if result.get('identity')!=ident:raise RuntimeError('MULTITAP_DONE_IDENTITY_DRIFT')
        return result
    if not args.resume and any(directory.iterdir()):raise FileExistsError('MULTITAP_ARM_OVERWRITE_REFUSED')
    history=[];best_rank=None;start=0
    if args.resume and latest.exists():
        saved=torch.load(latest,map_location='cpu',weights_only=True)
        if saved.get('identity')!=ident or saved.get('schema')!=SCHEMA+'.progress' or saved.get('arm')!=key:
            raise RuntimeError('MULTITAP_RESUME_IDENTITY_DRIFT')
        model.load_state_dict(saved['model'],strict=True)
        optimizer.load_state_dict(saved['optimizer']);rng.set_state(saved['rng'])
        torch.set_rng_state(saved['torch_rng'])
        if saved['cuda_rng'] is not None and torch.cuda.is_available():torch.cuda.set_rng_state_all(saved['cuda_rng'])
        history=saved['history'];best_rank=saved['best_rank'];start=saved['step']
    elif args.resume and any(directory.iterdir()):raise RuntimeError('MULTITAP_PROGRESS_MISSING')
    train=splits['train'];dev=splits['dev'];n=len(train['rows'])
    stop=min(args.steps,args.stop_after or args.steps)
    for step in range(start+1,stop+1):
        model.train()
        draws=torch.randint(n,(args.batch_size,),generator=rng).to(args.device)
        feats=train['features'].index_select(0,draws)
        truth=train['numeric'].index_select(0,draws)
        loc=model(feats,train_depth)
        target=numeric_location(truth,domain)
        scale=truth.new_tensor([d[1]-d[0] for d in domain])
        rel_denom=(truth.abs()/scale).clamp_min(.005)
        loss=20*F.mse_loss(loc,target)+.002*((loc-target).abs()/rel_denom).mean()
        if not bool(torch.isfinite(loss)):raise FloatingPointError('MULTITAP_LOSS_NOT_FINITE')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],1.,error_if_nonfinite=True)
        optimizer.step()
        if step%args.probe_every==0 or step==args.steps:
            dev_result=evaluation(model,dev,domain,args.eval_batch_size,args.tolerance,depth=train_depth)
            rank=(dev_result['min_tier_joint5'],-dev_result['mean_abs_xy_error'])
            if best_rank is None or rank>tuple(best_rank):
                best_rank=list(rank)
                gauss._atomic_save(best,{'schema':SCHEMA+'.best','identity':ident,
                                       'arm':key,'step':step,'model':model.state_dict(),'DEV':dev_result})
            history.append({'step':step,'loss':float(loss.detach()),'DEV':dev_result})
            print(f'MULTITAP_TRAIN arm={key} step={step}/{args.steps} '
                  f'DEV_joint5={rank[0]:.4f} DEV_MAE={dev_result["mean_abs_xy_error"]:.6g}',flush=True)
            gauss._atomic_save(latest,{'schema':SCHEMA+'.progress','identity':ident,'arm':key,
                   'step':step,'model':model.state_dict(),'optimizer':optimizer.state_dict(),
                   'rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
                   'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
                   'best_rank':best_rank,'history':history})
    if stop<args.steps:
        return {'status':'PAUSED','arm':key,'step':stop,'planned':args.steps}
    record=torch.load(best,map_location='cpu',weights_only=True)
    if record.get('schema')!=SCHEMA+'.best' or record.get('identity')!=ident or record.get('arm')!=key:
        raise RuntimeError('MULTITAP_BEST_IDENTITY_DRIFT')
    model.load_state_dict(record['model'],strict=True)
    depth_metrics={str(d):evaluation(model,dev,domain,args.eval_batch_size,args.tolerance,depth=d)
                   for d in (0,1,2,4)}
    for d in (0,1,2,4):
        print(f'MULTITAP_DEPTH arm={key} depth={d} DEV_joint5={depth_metrics[str(d)]["min_tier_joint5"]:.6f}',flush=True)
    interventions=(intervention_report(model,dev,domain,args.eval_batch_size,args.tolerance)
                   if train_depth==4 else None)
    result={'schema':SCHEMA,'identity':ident,'arm':key,'chosen_taps':chosen,
            'feedback_mode':'final-only-backbone-reentry' if control else 'each-step-backbone-reentry',
            'best_step':record['step'],'trained_depth':train_depth,'DEV':record['DEV'],'DEV_by_depth':depth_metrics,
            'DEV_interventions':interventions,'TEST':(evaluation(model,splits['test'],domain,args.eval_batch_size,args.tolerance,depth=train_depth)
                    if args.final_test else None),'history':history}
    gauss._atomic_json(done,result)
    return result


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('MULTITAP_CUDA_UNAVAILABLE')
    if not (args.steps>0 and args.probe_every>0 and args.batch_size>0 and args.eval_batch_size>0
            and args.cpu_threads>0 and args.heads>0 and args.layers>0 and 0<args.lr<=1
            and 0<args.tolerance<1 and 0<args.min_gain<1 and args.ridge>0):
        raise ValueError('MULTITAP_INVALID_ARGUMENTS')
    names=args.arms.split(',')
    if not names or len(set(names))!=len(names) or any(name not in DEFAULT_ARMS for name in names):
        raise ValueError('MULTITAP_UNKNOWN_OR_REPEATED_ARM')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    source=Path(args.reference_checkpoint).expanduser().resolve()
    run_dir=Path(args.run_dir).expanduser().resolve()
    prior_hash=(probe.sha256(source),probe.sha256(run_dir/args.checkpoint))
    model,splits,domain,config,_=probe.load_experiment(source,run_dir,args.device,args.checkpoint)
    if model.reference.hidden%args.heads:raise ValueError('MULTITAP_INVALID_HEADS')
    ident=identity(args,source,run_dir,config)
    out=Path(args.output).expanduser().resolve()
    if args.resume:
        if not (out/'config.json').exists() or json.loads((out/'config.json').read_text(encoding='utf-8'))!=ident:
            raise RuntimeError('MULTITAP_RESUME_IDENTITY_DRIFT')
    else:
        if out.exists() and any(out.iterdir()):raise FileExistsError('MULTITAP_OUTPUT_OVERWRITE_REFUSED')
        out.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(out/'config.json',ident)
    source_taps=ReferenceTaps(model.reference).to(args.device).eval()
    readouts=linear_probes(source_taps,splits,args.ridge)
    gauss._atomic_json(out/'activation_readouts.json',readouts)
    print('MULTITAP_SOURCE_VERIFIED=TRUE REFERENCE_FROZEN=TRUE SIX_FEATURE_INPUT_ONLY=TRUE',flush=True)
    print('MULTITAP_ACTIVATION_READOUTS='+json.dumps({k:{t:round(readouts[k][t]['DEV_R2'],4)
          for t in ('operands','products','partial_sums')} for k in readouts},separators=(',',':')),flush=True)
    reference_gaussian=[];reference_direct=[]
    with torch.no_grad():
        for x in splits['dev']['features'].split(args.eval_batch_size):
            reference_gaussian.append(gauss.numeric_decode(model.reference(x),domain).cpu())
            reference_direct.append(direct_decode(model.reference.locations(x),domain).cpu())
    base_scores={'gaussian':lab.strict_metrics(torch.cat(reference_gaussian),splits['dev'],args.tolerance),
                 'direct':lab.strict_metrics(torch.cat(reference_direct),splits['dev'],args.tolerance)}
    gauss._atomic_json(out/'reference_controls.json',base_scores)
    reports={}
    for key in names:
        reports[key]=train_arm(key,model.reference,splits,domain,config,args,out,ident)
        if reports[key].get('status')=='PAUSED':
            print('MULTITAP_PAUSED='+json.dumps(reports[key]),flush=True)
            return reports[key]
    metrics={k:report['DEV_by_depth'] for k,report in reports.items()}
    gains={}
    if all(key in reports for key in ('all','all_depth1','all_depth2','all_nofeedback')):
        a=reports['all']['DEV_by_depth'];b=reports['all_nofeedback']['DEV_by_depth']
        d1=reports['all_depth1']['DEV_by_depth']['1']['min_tier_joint5']
        d2=reports['all_depth2']['DEV_by_depth']['2']['min_tier_joint5']
        d4=a['4']['min_tier_joint5']
        comparisons={'depth4_vs_trained_depth1':d4-d1,
                     'depth4_vs_trained_depth2':d4-d2,
                     'depth4_vs_matched_no_feedback':d4-b['4']['min_tier_joint5'],
                     'depth4_vs_original_gaussian':d4-base_scores['gaussian']['min_tier_joint5']}
        controls=reports['all']['DEV_interventions']['interventions']
        any_causal=any(v.get('paired',{}).get('net',0)>0 for v in controls.values())
        gains={**comparisons,'thought_ablation_hurts_on_some_cases':any_causal,
               'status':'PASS' if min(comparisons.values())>=args.min_gain and any_causal else 'NOT_ESTABLISHED',
               'required_absolute_gain':args.min_gain}
    after=(probe.sha256(source),probe.sha256(run_dir/args.checkpoint))
    if after!=prior_hash:raise RuntimeError('MULTITAP_SOURCE_CHECKPOINT_MUTATED')
    summary={'schema':SCHEMA,'identity':ident,'activation_readouts':readouts,
             'reference_controls':base_scores,
             'arms':{k:{'best_step':v['best_step'],'DEV_by_depth':v['DEV_by_depth'],
                        'DEV_interventions':v['DEV_interventions'],'TEST':v['TEST']} for k,v in reports.items()},
             'causal_feedback_gain':gains,'source_checkpoints_unchanged':True}
    gauss._atomic_json(out/'report.json',summary)
    print('MULTITAP_CAUSAL_FEEDBACK='+json.dumps(gains,separators=(',',':')),flush=True)
    print('MULTITAP_REPORT='+str(out/'report.json'),flush=True)
    return summary


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--checkpoint',default='teacher_best.pt',choices=('teacher_best.pt',))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--arms',default=','.join(DEFAULT_ARMS))
    p.add_argument('--steps',type=int,default=1500)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--ridge',type=float,default=.01)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--min-gain',type=float,default=.02)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--final-test',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
