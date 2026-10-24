#!/usr/bin/env python3
"""Train a NEW, independent fourth X/Y head on three frozen Gaussian heads.

This is an exploratory stacked fusion experiment: the base heads have seen TRAIN
and previously selected DEV; TEST has been inspected. No claim of new blind
certification or out-of-fold stacking is made. Source checkpoints are read-only.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_fusion as old
from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref

SCHEMA = 'nanojev.hawk-dove.joined-gaussian-head-fusion.v2'
ORDER = ('reference', 'pythia', 'tinystories')
SPLITS = ('train', 'dev', 'test')


def extracted_features(sources, data, device, batch_size, bins, head_hidden):
    """Join each frozen head's 128 hidden features, six location statistics,
    and the COMPLETE two 256-bin X/Y predicted density distributions.
    No oracle targets, target densities, correctness flags, or source labels.
    """
    if tuple(sources) != ORDER:
        raise RuntimeError('JOINED_SOURCE_ORDER_DRIFT')
    for model, _ in sources.values():
        model.to(device).eval().requires_grad_(False)
    channel_dim = head_hidden + 6 + 2*bins
    output = {}
    with torch.inference_mode():
        for split in SPLITS:
            blocks = []
            for first in range(0, len(data[split]['rows']), batch_size):
                last = min(first+batch_size, len(data[split]['rows']))
                numeric = data[split]['features'][first:last].to(device)
                per_head = []
                for name in ORDER:
                    model, cache = sources[name]
                    if cache is None:
                        inp = numeric
                        first_layer = model.encoder[0](numeric)
                    else:
                        activation = cache['splits'][split]['activations'][first:last].to(device)
                        inp = torch.cat((numeric, activation), dim=-1)
                        first_layer = (model.encoder[0](numeric) +
                                       model.backbone_scale * model.backbone_projection(activation))
                    hidden = model.encoder[3](model.encoder[2](model.encoder[1](first_layer)))
                    loc = model.encoder[4](hidden).sigmoid()
                    density = model(inp)
                    if density.shape != (last-first, 2, bins):
                        raise RuntimeError('JOINED_DENSITY_SHAPE_DRIFT:' + name)
                    positions = (torch.arange(bins, device=device, dtype=density.dtype)+.5)/bins
                    mean = (density*positions.view(1,1,-1)).sum(-1)
                    var = (density*(positions.view(1,1,-1)-mean.unsqueeze(-1)).square()).sum(-1)
                    channel = torch.cat((hidden, loc, mean, var, density.flatten(1)), dim=-1)
                    if channel.shape[-1] != channel_dim:
                        raise RuntimeError('JOINED_CHANNEL_DIMENSION_DRIFT')
                    per_head.append(channel)
                joined = torch.cat(per_head, dim=-1).float().cpu()
                if not bool(torch.isfinite(joined).all()):
                    raise RuntimeError('JOINED_NONFINITE_FROZEN_FEATURE')
                blocks.append(joined)
            output[split] = torch.cat(blocks)
            print(f'JOINED_FEATURES split={split} rows={len(output[split])} '
                  f'per_head={channel_dim} total={output[split].shape[1]} '
                  f'full_density_bins={bins}', flush=True)
    return output


class JoinedFusionHead(nn.Module):
    """Three learned source encoders -> NEW shared latent -> new signed-log X/Y.

    Critically, there is NO reference anchor, additive skip, residual correction,
    nor zero initialized final layer. This is a fresh predictor.
    """
    def __init__(self, hidden, bins, width=128):
        super().__init__()
        if min(hidden, bins, width) < 1:
            raise ValueError('JOINED_INVALID_DIMENSIONS')
        self.hidden = hidden
        self.bins = bins
        self.width = width
        self.per_head_dim = hidden + 6 + 2*bins
        self.projectors = nn.ModuleList([
            nn.Sequential(nn.LayerNorm(self.per_head_dim),
                          nn.Linear(self.per_head_dim, width), nn.GELU())
            for _ in ORDER])
        self.joint = nn.Sequential(nn.Linear(len(ORDER)*width, width), nn.GELU(),
                                   nn.Linear(width, width), nn.GELU())
        self.xy = nn.Linear(width, 2)
        self.zero = nn.Linear(width, 2)

    def forward(self, joined):
        if joined.ndim != 2 or joined.shape[-1] != len(ORDER)*self.per_head_dim:
            raise ValueError('JOINED_FEATURE_SHAPE_DRIFT')
        channels = joined.split(self.per_head_dim, dim=-1)
        learned = torch.cat([projector(x) for projector,x in zip(self.projectors, channels)], dim=-1)
        state = self.joint(learned)
        return torch.tanh(self.xy(state)), self.zero(state)


def to_log(value, log_scale):
    return torch.sign(value)*torch.log1p(value.abs())/log_scale


def decode(signed_log, zero_logits, log_scale):
    s = signed_log.clamp(-1.,1.)
    numbers = torch.sign(s)*torch.expm1(s.abs()*log_scale)
    return torch.where(torch.sigmoid(zero_logits) >= .5, torch.zeros_like(numbers), numbers)


def target_log_scale(domain):
    largest = max(abs(float(edge)) for lo,hi in domain for edge in (lo,hi))
    return math.log1p(max(largest, 1.))


def training_loss(signed_log, zero_logits, truth, log_scale):
    truth_log = to_log(truth, log_scale)
    # Signed-log error resolves small values which an absolute 10,000 floor ignored.
    main = F.smooth_l1_loss(signed_log, truth_log, beta=.01)
    is_zero = (truth == 0).to(zero_logits.dtype)
    zero = F.binary_cross_entropy_with_logits(
        zero_logits, is_zero, pos_weight=torch.full((2,), 6., device=truth.device))
    return main + .15*zero


def reference_only(features, head_dim):
    view = features.clone()
    view[:,head_dim:] = 0.
    return view


@torch.no_grad()
def score(model, features, data_split, log_scale, device, batch_size, tolerance):
    model.eval()
    guesses = []
    for first in range(0,len(features),batch_size):
        signed, zeros = model(features[first:first+batch_size].to(device))
        guesses.extend(decode(signed, zeros, log_scale).cpu().tolist())
    return mr._score(data_split['rows'], guesses, tolerance), guesses


def rank(metric):
    return (metric['min_tier_joint_all_masks_rate'],metric['mean_tier_joint_all_masks_rate'])


def source_scores(features, data, domain, hidden, bins, tolerance):
    """Audit the independently trained source outputs on the exact joined rows."""
    result={}
    width=hidden+6+2*bins
    for split in ('dev','test'):
        result[split]={}
        for i,name in enumerate(ORDER):
            positions=features[split][:,i*width+hidden+2:i*width+hidden+4]
            predictions=old._to_number(positions,domain).tolist()
            result[split][name]=mr._score(data[split]['rows'],predictions,tolerance)
    return result


def save_progress(path, *, full, ablated, opt, control_opt, sampler, step,
                  best_step, best_rank, control_step, control_rank, identity, history):
    old._atomic_save(path, {
        'schema':SCHEMA+'.progress.v1','identity':identity, 'step':step,
        'model':full.state_dict(),'control_model':ablated.state_dict(),
        'optimizer':opt.state_dict(),'control_optimizer':control_opt.state_dict(),
        'sampler_rng':sampler.get_state(), 'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'best_step':best_step,'best_rank':best_rank,
        'control_best_step':control_step,'control_best_rank':control_rank,'history':history})


def restore(progress, full, control, opt, control_opt, sampler):
    full.load_state_dict(progress['model'],strict=True)
    control.load_state_dict(progress['control_model'],strict=True)
    opt.load_state_dict(progress['optimizer'])
    control_opt.load_state_dict(progress['control_optimizer'])
    sampler.set_state(progress['sampler_rng'])
    torch.set_rng_state(progress['torch_rng'])
    saved_cuda = progress['cuda_rng']
    if saved_cuda:
        if not torch.cuda.is_available() or len(saved_cuda) != torch.cuda.device_count():
            raise RuntimeError('JOINED_CUDA_RNG_DRIFT')
        torch.cuda.set_rng_state_all(saved_cuda)


def train(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('JOINED_CUDA_UNAVAILABLE')
    if any(n<1 for n in (args.steps,args.probe_every,args.batch_size,
                          args.eval_batch_size,args.width,args.cpu_threads)):
        raise ValueError('JOINED_BAD_POSITIVE_OPTION')
    if args.lr<=0 or not 0<args.tolerance<1:
        raise ValueError('JOINED_INVALID_LR_OR_TOLERANCE')
    torch.set_num_threads(args.cpu_threads)
    output = args.output.expanduser().resolve()
    sources_fingerprint = old._fingerprint(args.reference_checkpoint,args.duo_root)
    if args.resume:
        if not (output/'latest.pt').is_file() or not (output/'feature_cache.pt').is_file():
            raise FileNotFoundError('JOINED_RESUME_REQUIRES_FULL_CHECKPOINT_AND_CACHE')
        progress = torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
        if progress.get('identity',{}).get('sources_sha256') != sources_fingerprint:
            raise RuntimeError('JOINED_SOURCE_DRIFT')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('JOINED_REFUSES_OUTPUT_OVERWRITE')

    sources,data,domain,manifest,ref_ident=old._validated_sources(
        args.reference_checkpoint,args.duo_root,args.eval_batch_size)
    if tuple(sources) != ORDER:
        raise RuntimeError('JOINED_SOURCE_ORDER_MISMATCH')
    hidden,bins=ref_ident['hidden'],ref_ident['grid_bins']
    head_dim=hidden+6+2*bins
    identity={'schema':SCHEMA, 'sources_sha256':sources_fingerprint,
              'matrix_hashes':manifest['matrix_hashes'],
              'head_hidden':hidden,'grid_bins':bins,'head_width':args.width,
              'seed':args.seed,'batch_size':args.batch_size,'lr':args.lr,
              'tolerance':args.tolerance,'domain':[list(d) for d in domain],
              'feature_dim':head_dim*3,'loss':'signed_log_huber+exact_zero_BCE',
              'sources':list(ORDER),'test_policy':'EXPLORATORY: TEST previously inspected'}
    if args.resume:
        if progress.get('schema') != SCHEMA+'.progress.v1' or progress.get('identity') != identity:
            raise RuntimeError('JOINED_RESUME_IDENTITY_DRIFT')
        cache = torch.load(output/'feature_cache.pt',map_location='cpu',weights_only=True)
        if cache.get('schema') != SCHEMA+'.frozen_features.v1' or cache.get('identity') != identity:
            raise RuntimeError('JOINED_CACHE_IDENTITY_DRIFT')
        features=cache['features']
        for split in SPLITS:
            expected=(len(data[split]['rows']),head_dim*3)
            if features[split].shape!=expected or features[split].dtype!=torch.float32 or not bool(torch.isfinite(features[split]).all()):
                raise RuntimeError('JOINED_CACHE_SHAPE_OR_VALUES_DRIFT:'+split)
    else:
        features=extracted_features(sources,data,torch.device(args.device),args.eval_batch_size,bins,hidden)
        output.mkdir(parents=True,exist_ok=True)
        old._atomic_save(output/'feature_cache.pt',{'schema':SCHEMA+'.frozen_features.v1',
                                                    'identity':identity,'features':features})
        old._atomic_json(output/'config.json',identity)
        old._atomic_json(output/'matrix_split_manifest.json',manifest)
    del sources
    baseline_scores=source_scores(features,data,domain,hidden,bins,args.tolerance)
    print('JOINED_FROZEN_PREFLIGHT_DEV '+
          ' '.join(f'{name}={baseline_scores["dev"][name]["min_tier_joint_all_masks_rate"]:.4f}'
                   for name in ORDER),flush=True)
    print('JOINED_FOURTH_HEAD_RANDOM_INIT=TRUE REFERENCE_SHORTCUT=NONE',flush=True)
    device=torch.device(args.device)
    log_scale=target_log_scale(domain)
    torch.manual_seed(args.seed)
    full=JoinedFusionHead(hidden,bins,args.width).to(device)
    control=JoinedFusionHead(hidden,bins,args.width).to(device)
    control.load_state_dict(full.state_dict(),strict=True)
    opt=torch.optim.AdamW(full.parameters(),lr=args.lr,weight_decay=0)
    control_opt=torch.optim.AdamW(control.parameters(),lr=args.lr,weight_decay=0)
    ablated={split:reference_only(t,head_dim) for split,t in features.items()}
    generator=torch.Generator(device='cpu').manual_seed(args.seed+799)
    start=best_step=control_step=0
    best_rank=control_rank=None
    history=[]
    if args.resume:
        start=int(progress['step'])
        if args.steps<start:
            raise RuntimeError('JOINED_CANNOT_REWIND_STEPS')
        best_step=int(progress['best_step']); control_step=int(progress['control_best_step'])
        best_rank=tuple(progress['best_rank']) if progress['best_rank'] is not None else None
        control_rank=tuple(progress['control_best_rank']) if progress['control_best_rank'] is not None else None
        history=progress['history']
        restore(progress,full,control,opt,control_opt,generator)
        print(f'JOINED_FULL_STATE_RESUME step={start} target={args.steps}',flush=True)

    tier_indices=data['train']['tier_indices']
    truth=data['train']['numeric']

    def probe(step,losses=None):
        nonlocal best_step, best_rank, control_step, control_rank
        dev,guess=score(full,features['dev'],data['dev'],log_scale,device,args.eval_batch_size,args.tolerance)
        control_dev,control_guess=score(control,ablated['dev'],data['dev'],log_scale,device,args.eval_batch_size,args.tolerance)
        r,cr=rank(dev),rank(control_dev)
        if step>0 and (best_rank is None or r>best_rank):
            best_rank,best_step=r,step
            old._atomic_save(output/'best.pt',{'schema':SCHEMA,'identity':identity,
                                               'step':step,'model':full.state_dict()})
        if step>0 and (control_rank is None or cr>control_rank):
            control_rank,control_step=cr,step
            old._atomic_save(output/'best_reference_only.pt',{'schema':SCHEMA,'identity':identity,
                                                              'step':step,'model':control.state_dict()})
        row={'step':step,'dev_min_joint5':r[0], 'control_dev_min_joint5':cr[0],
             'best_trained_step':best_step,'train_loss':losses[0] if losses else None,
             'control_train_loss':losses[1] if losses else None}
        history.append(row)
        save_progress(output/'latest.pt',full=full,ablated=control,opt=opt,
                      control_opt=control_opt,sampler=generator,step=step,best_step=best_step,
                      best_rank=best_rank,control_step=control_step,control_rank=control_rank,
                      identity=identity,history=history)
        old._atomic_json(output/'history.json',{'schema':SCHEMA,'probes':history})
        print(f'joined step={step}/{args.steps} DEV_min_joint5={r[0]:.4f} '
              f'reference_only_DEV={cr[0]:.4f} best_trained_step={best_step} '
              f'loss={row["train_loss"]}',flush=True)

    if not args.resume:
        probe(0)
    for step in range(start+1,args.steps+1):
        selected=[]
        for i,tier in enumerate(ref.TIERS):
            n=args.batch_size//len(ref.TIERS)+(i<args.batch_size%len(ref.TIERS))
            if n:
                pool=tier_indices[tier]
                selected.append(pool[torch.randint(len(pool),(n,),generator=generator)])
        idx=torch.cat(selected)
        x=features['train'][idx].to(device)
        ctrl=ablated['train'][idx].to(device)
        y=truth[idx].to(device)
        full.train();opt.zero_grad(set_to_none=True)
        sy,z=full(x)
        l=training_loss(sy,z,y,log_scale)
        if not bool(torch.isfinite(l)):
            raise FloatingPointError('JOINED_NONFINITE_TRAIN_LOSS')
        l.backward()
        nn.utils.clip_grad_norm_(full.parameters(),1.,error_if_nonfinite=True)
        opt.step()
        control.train();control_opt.zero_grad(set_to_none=True)
        cy,cz=control(ctrl)
        lc=training_loss(cy,cz,y,log_scale)
        if not bool(torch.isfinite(lc)):
            raise FloatingPointError('JOINED_NONFINITE_CONTROL_LOSS')
        lc.backward()
        nn.utils.clip_grad_norm_(control.parameters(),1.,error_if_nonfinite=True)
        control_opt.step()
        if step%args.probe_every==0 or step==args.steps:
            probe(step,(float(l.detach()),float(lc.detach())))
    if best_step==0 or control_step==0:
        raise RuntimeError('JOINED_NO_TRAINED_CHECKPOINT')
    def evaluate(model,path,split,source):
        chk=torch.load(output/path,weights_only=True,map_location='cpu')
        if chk['identity']!=identity or chk['step']<=0:
            raise RuntimeError('JOINED_SELECTED_CHECKPOINT_INVALID')
        model.load_state_dict(chk['model'],strict=True)
        return score(model,source[split],data[split],log_scale,device,args.eval_batch_size,args.tolerance)
    dev,dev_p=evaluate(full,'best.pt','dev',features)
    test,test_p=evaluate(full,'best.pt','test',features)
    cdev,cdev_p=evaluate(control,'best_reference_only.pt','dev',ablated)
    ctest,ctest_p=evaluate(control,'best_reference_only.pt','test',ablated)

    # Ablate each learned source from the SELECTED full model, no training on TEST.
    drop_effect={}
    for split in ('dev','test'):
        drop_effect[split]={}
        for i,name in enumerate(ORDER):
            chopped=features[split].clone()
            chopped[:,i*head_dim:(i+1)*head_dim]=0.
            s,_=score(full,chopped,data[split],log_scale,device,args.eval_batch_size,args.tolerance)
            drop_effect[split][name]=s['min_tier_joint_all_masks_rate']

    for split,pred in (('dev',dev_p),('test',test_p)):
        with (output/f'predictions_{split}.jsonl').open('w',encoding='utf8') as stream:
            for row,pair in zip(data[split]['rows'],pred):
                stream.write(json.dumps({'instance':row['instance'],'tier':row['tier'],
                    'mask':row['mask'],'X':pair[0],'Y':pair[1],'Z':mr._z(pair)})+'\n')
    bases=baseline_scores
    report={'schema':SCHEMA+'.report.v1', 'identity':identity,
            'completed_updates':args.steps,'best_trained_step':best_step,
            'control_best_trained_step':control_step,
            'DEV':{'joined':dev,'reference_only_joined':cdev,
                   'sources':bases['dev']},
            'TEST':{'joined':test,'reference_only_joined':ctest,
                    'sources':bases['test']},
            'source_ablation_dev_test':drop_effect,
            'gain_over_reference_only_DEV':rank(dev)[0]-rank(cdev)[0],
            'gain_over_reference_only_TEST':rank(test)[0]-rank(ctest)[0],
            'evaluation_status':'EXPLORATORY_NOT_BLIND_CERTIFICATION',
            'training_target':'new signed-log X/Y estimator plus exact-zero classifier',
            'source_checkpoints_frozen':True,
            'test_used_to_select':False}
    old._atomic_json(output/'report.json',report)
    print(json.dumps({'report':str(output/'report.json'),
        'best_trained_step':best_step, 'DEV_joined':rank(dev)[0],
        'TEST_joined':rank(test)[0],'TEST_reference_only_control':rank(ctest)[0]},indent=2),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--duo-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--steps',type=int,default=12000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=128)
    p.add_argument('--width',type=int,default=128)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--seed',type=int,default=20261022)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return train(parser().parse_args(argv))


if __name__=='__main__':
    main()
