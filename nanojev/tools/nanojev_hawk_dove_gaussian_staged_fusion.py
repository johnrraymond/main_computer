#!/usr/bin/env python3
"""Teacher-recovery-gated, independently trained fourth Hawk-Dove Gaussian head.

Stage 1 uses TRAIN examples only to fit a trainable readout to the already present
reference Gaussian density means. DEV must reproduce the frozen reference before
joint training is permitted. Stage 2 trains the SAME readout and new nonlinear
joint representation with all three frozen source channels enabled. An explicit
reference-only trained control starts from the same recovered state. Full-state
restart and the recovered checkpoint are immutable once established.

Exploratory only: existing base-head model selection has already used DEV and
historical TEST results have been inspected. This is not fresh blind certification.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_joined_fusion as prior
from tools import nanojev_hawk_dove_gaussian_fusion as old
from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref

SCHEMA = 'nanojev.hawk-dove.gaussian-fusion-teacher-recovery.v1'
ORDER = ('reference', 'pythia', 'tinystories')
SPLITS = ('train', 'dev', 'test')


class RecoveredFourthHead(nn.Module):
    """One trainable fourth head; includes learned source-readout and joint branch.

    Its output is a single, trainable linear readout of the learned joint state
    AND the reference density-mean feature. It never calls a frozen reference
    predictor, or adds an external fixed reference prediction at inference.
    The explicit readout feature is necessary to prove numerical information
    survives the previously lossy projector/decoder path.
    """
    def __init__(self, hidden, bins, width=128):
        super().__init__()
        self.hidden = hidden
        self.bins = bins
        self.width = width
        self.head_dim = hidden + 6 + 2*bins
        self.projectors = nn.ModuleList([
            nn.Sequential(nn.LayerNorm(self.head_dim),
                          nn.Linear(self.head_dim, width), nn.GELU())
            for _ in ORDER])
        self.joint = nn.Sequential(nn.Linear(3*width, width), nn.GELU(),
                                   nn.Linear(width, width), nn.GELU())
        # One learned output readout, not a frozen baseline plus correction.
        self.readout = nn.Linear(width + 2, 2)
        self.zero = nn.Linear(width, 2)
        with torch.no_grad():
            self.readout.weight[:, :width].zero_()  # Stage 1 is reference-only.
            self.zero.weight.zero_()
            self.zero.bias.fill_(-10.)

    def forward(self, joined):
        if joined.ndim != 2 or joined.shape[-1] != 3*self.head_dim:
            raise ValueError('RECOVERY_JOINED_FEATURE_SHAPE_DRIFT')
        channels = joined.split(self.head_dim, dim=-1)
        states = torch.cat([layer(x) for layer,x in zip(self.projectors, channels)], dim=-1)
        state = self.joint(states)
        reference_mean = channels[0][:, self.hidden+2:self.hidden+4]
        location = self.readout(torch.cat((state, reference_mean), -1))
        return location, self.zero(state)


def recovered_readout(head, train_features):
    """TRAIN-only least-squares teacher distillation (not a copied checkpoint).

    The reference density mean is present among the frozen representations;
    estimate a trainable 2-output affine readout by minimizing MSE to teacher
    density means. We fit in float64 for small-target numerical fidelity.
    """
    h = head.hidden
    teacher = train_features[:, h+2:h+4].to(torch.float64)
    if len(teacher)<4 or not bool(torch.isfinite(teacher).all()):
        raise RuntimeError('RECOVERY_BAD_TEACHER_TRAIN_DATA')
    design = torch.cat((teacher, torch.ones(len(teacher), 1, dtype=torch.float64)), 1)
    if int(torch.linalg.matrix_rank(design)) != 3:
        raise RuntimeError('RECOVERY_TEACHER_READOUT_RANK_DEFICIENT')
    fitted = torch.linalg.lstsq(design, teacher).solution
    residual = (design @ fitted - teacher).abs().max().item()
    if residual > 1e-8:
        raise RuntimeError('RECOVERY_TEACHER_DISTILLATION_DID_NOT_FIT')
    with torch.no_grad():
        head.readout.weight[:, :head.width].zero_()
        head.readout.weight[:, head.width:].copy_(fitted[:2].T.to(head.readout.weight))
        head.readout.bias.copy_(fitted[2].to(head.readout.bias))
    return {'method':'train_only_float64_least_squares', 'max_train_location_error':residual,
            'training_rows':int(len(teacher))}


def to_number(locations, domain):
    return old._to_number(locations, domain)


@torch.no_grad()
def score(head, features, rows, domain, device, batch_size, tol, *, use_zero=True):
    head.eval()
    output=[]
    for offset in range(0, len(features), batch_size):
        loc,zlogits = head(features[offset:offset+batch_size].to(device))
        numerical=to_number(loc, domain)
        if use_zero:
            numerical=torch.where(torch.sigmoid(zlogits)>.5,torch.zeros_like(numerical),numerical)
        output.extend(numerical.detach().cpu().tolist())
    return mr._score(rows,output,tol),output


def source_teacher_predictions(features, hidden, domain):
    return to_number(features[:,hidden+2:hidden+4],domain).tolist()


def quality(metric):
    return (metric['min_tier_joint_all_masks_rate'],
            metric['mean_tier_joint_all_masks_rate'])


def gate_recovery(student, teacher, predictions, reference_predictions, tolerance):
    """Require accuracy recovery across EVERY tier and close scalar readout parity."""
    gaps={tier:teacher['by_tier_joint_all_masks_rate'][tier]-
          student['by_tier_joint_all_masks_rate'][tier]
          for tier in teacher['by_tier_joint_all_masks_rate']}
    largest=max(gaps.values())
    differences=(torch.tensor(predictions,dtype=torch.float64) -
                 torch.tensor(reference_predictions,dtype=torch.float64)).abs()
    # Precision budget: a few raw units on 4e6-wide normalization is okay,
    # but never allow meaningful degradation of established per-tier accuracy.
    allowed=1/384 + 1e-9  # at most one held-out instance per tier in standard setup
    ok = largest<=allowed and bool(torch.isfinite(differences).all())
    return {'status':'PASS' if ok else 'FAIL',
            'worst_tier_accuracy_drop':largest,
            'allowed_tier_accuracy_drop':allowed,
            'max_abs_numeric_difference':float(differences.max()),
            'tier_accuracy_drop':gaps,
            'tolerance':tolerance}


def loss(head, features, truth, domain):
    pred, zero_logits = head(features)
    target=old._to_unit(truth,domain)
    numeric=to_number(pred,domain)
    denom=truth.abs().clamp_min(1.)
    normalized=10*F.smooth_l1_loss(pred,target,beta=.001)
    relative=.02*torch.log1p((numeric-truth).abs()/denom).mean()
    is_zero=(truth==0).float()
    zero=.025*F.binary_cross_entropy_with_logits(
        zero_logits,is_zero,pos_weight=torch.full((2,),6.,device=truth.device))
    return normalized+relative+zero


def identity_for(args,sources_hash,manifest,reference_identity,hidden,bins):
    return {'schema':SCHEMA,'sources_sha256':sources_hash,
            'matrix_hashes':manifest['matrix_hashes'],
            'hidden':hidden,'bins':bins,'width':args.width,
            'lr':args.lr,'seed':args.seed,'batch_size':args.batch_size,
            'tolerance':args.tolerance,'recovery':'train_only_least_squares',
            'joint_loss':'unit_huber+relative_log+zero_bce',
            'source_reference_identity':reference_identity,
            'test_policy':'EXPLORATORY_TEST_PREVIOUSLY_INSPECTED'}


def _atomic_progress(path,full,control,opt,copt,sampler,step,best_step,best_rank,
                     cbest_step,cbest_rank,history,identity):
    old._atomic_save(path,{'schema':SCHEMA+'.progress', 'identity':identity,
       'step':step,'model':full.state_dict(),'control_model':control.state_dict(),
       'optimizer':opt.state_dict(),'control_optimizer':copt.state_dict(),
       'sampler_rng':sampler.get_state(),'torch_rng':torch.get_rng_state(),
       'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
       'best_step':best_step,'best_rank':best_rank,
       'control_best_step':cbest_step,'control_best_rank':cbest_rank,
       'history':history})


def _restore(progress, full,control,opt,copt,sampler):
    full.load_state_dict(progress['model'],strict=True)
    control.load_state_dict(progress['control_model'],strict=True)
    opt.load_state_dict(progress['optimizer'])
    copt.load_state_dict(progress['control_optimizer'])
    sampler.set_state(progress['sampler_rng'])
    torch.set_rng_state(progress['torch_rng'])
    states=progress['cuda_rng']
    if states:
        if not torch.cuda.is_available() or len(states)!=torch.cuda.device_count():
            raise RuntimeError('RECOVERY_CUDA_RNG_DRIFT')
        torch.cuda.set_rng_state_all(states)


def load_features(args, output, data, manifest, source_hash, hidden, bins, sources):
    expected={k:(len(data[k]['rows']),3*(hidden+6+2*bins)) for k in SPLITS}
    if args.resume:
        cache=torch.load(output/'feature_cache.pt',map_location='cpu',weights_only=True)
        if cache.get('schema')!=SCHEMA+'.features' or cache.get('sources_sha256')!=source_hash or cache.get('matrix_hashes')!=manifest['matrix_hashes']:
            raise RuntimeError('RECOVERY_FEATURE_CACHE_DRIFT')
        tensors=cache['features']
    elif args.reuse_feature_cache is not None:
        cache=torch.load(args.reuse_feature_cache,map_location='cpu',weights_only=True)
        if cache.get('schema')!=prior.SCHEMA+'.frozen_features.v1':
            raise RuntimeError('RECOVERY_REUSED_FEATURE_CACHE_SCHEMA_DRIFT')
        old_ident=cache.get('identity',{})
        if old_ident.get('sources_sha256')!=source_hash or old_ident.get('matrix_hashes')!=manifest['matrix_hashes'] or old_ident.get('head_hidden')!=hidden or old_ident.get('grid_bins')!=bins:
            raise RuntimeError('RECOVERY_REUSED_FEATURE_CACHE_IDENTITY_DRIFT')
        tensors=cache['features']
        print('RECOVERY_VERIFIED_REUSED_JOINED_FEATURE_CACHE='+str(args.reuse_feature_cache),flush=True)
    else:
        tensors=prior.extracted_features(sources,data,torch.device(args.device),args.eval_batch_size,bins,hidden)
    for split,shape in expected.items():
        x=tensors[split]
        if x.shape!=shape or x.dtype!=torch.float32 or not bool(torch.isfinite(x).all()):
            raise RuntimeError('RECOVERY_FEATURE_SHAPE_OR_VALUE_DRIFT:'+split)
    if not args.resume:
        old._atomic_save(output/'feature_cache.pt',{
            'schema':SCHEMA+'.features','sources_sha256':source_hash,
            'matrix_hashes':manifest['matrix_hashes'],'features':tensors})
    return tensors


def train(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('RECOVERY_CUDA_UNAVAILABLE')
    if any(i<1 for i in (args.steps,args.probe_every,args.batch_size,args.eval_batch_size,args.width,args.cpu_threads)):
        raise ValueError('RECOVERY_INVALID_POSITIVE_ARGUMENT')
    if not 0<args.lr or not 0<args.tolerance<1:
        raise ValueError('RECOVERY_BAD_LR_OR_TOLERANCE')
    torch.set_num_threads(args.cpu_threads)
    output=args.output.expanduser().resolve()
    if args.resume:
        if not (output/'latest.pt').is_file() or not (output/'feature_cache.pt').is_file() or not (output/'recovered.pt').is_file():
            raise FileNotFoundError('RECOVERY_RESUME_REQUIRES_STATE_AND_RECOVERY_CHECKPOINT')
        progress=torch.load(output/'latest.pt',weights_only=True,map_location='cpu')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('RECOVERY_REFUSES_OUTPUT_OVERWRITE')

    source_hash=old._fingerprint(args.reference_checkpoint,args.duo_root)
    sources,data,domain,manifest,reference_identity=old._validated_sources(
        args.reference_checkpoint,args.duo_root,args.eval_batch_size)
    if tuple(sources)!=ORDER:
        raise RuntimeError('RECOVERY_SOURCE_ORDER_DRIFT')
    hidden,bins=reference_identity['hidden'],reference_identity['grid_bins']
    identity=identity_for(args,source_hash,manifest,reference_identity,hidden,bins)
    if args.resume and (progress.get('schema')!=SCHEMA+'.progress' or progress.get('identity')!=identity):
        raise RuntimeError('RECOVERY_RESUME_IDENTITY_DRIFT')
    if not args.resume: output.mkdir(parents=True,exist_ok=True)
    features=load_features(args,output,data,manifest,source_hash,hidden,bins,sources)
    del sources
    bases=prior.source_scores(features,data,domain,hidden,bins,args.tolerance)
    reference_dev=bases['dev']['reference']
    print('RECOVERY_FROZEN_REFERENCE_DEV='+str(quality(reference_dev)[0]),flush=True)
    torch.manual_seed(args.seed)
    device=torch.device(args.device)
    full=RecoveredFourthHead(hidden,bins,args.width).to(device)
    control=RecoveredFourthHead(hidden,bins,args.width).to(device)
    head_dim=hidden+6+2*bins
    ablated={split:prior.reference_only(features[split],head_dim) for split in SPLITS}

    if args.resume:
        recovery_ckpt=torch.load(output/'recovered.pt',weights_only=True,map_location='cpu')
        if recovery_ckpt.get('identity')!=identity or recovery_ckpt.get('gate',{}).get('status')!='PASS':
            raise RuntimeError('RECOVERY_CHECKPOINT_GATE_IDENTITY_DRIFT')
        gate=recovery_ckpt['gate']
        print('RECOVERY_GATE=PASS prior_recovered_checkpoint=VERIFIED',flush=True)
    else:
        fitted=recovered_readout(full,features['train'])
        student_dev,preds=score(full,ablated['dev'],data['dev']['rows'],domain,device,args.eval_batch_size,args.tolerance)
        original=source_teacher_predictions(features['dev'],hidden,domain)
        gate=gate_recovery(student_dev,reference_dev,preds,original,args.tolerance)
        print(f'RECOVERY_GATE={gate["status"]} DEV_min_joint5={quality(student_dev)[0]:.4f} '
              f'reference_DEV_min_joint5={quality(reference_dev)[0]:.4f} '
              f'max_abs_numeric_difference={gate["max_abs_numeric_difference"]:.9g}',flush=True)
        old._atomic_json(output/'recovery_evidence.json',{'schema':SCHEMA+'.recovery',
            'identity':identity,'fit':fitted,'gate':gate,
            'student_DEV':student_dev,'reference_DEV':reference_dev})
        if gate['status']!='PASS':
            raise RuntimeError('RECOVERY_GATE_FAILED_JOINT_TRAINING_REFUSED')
        old._atomic_save(output/'recovered.pt',{'schema':SCHEMA+'.recovery',
           'identity':identity,'model':copy.deepcopy(full.state_dict()),
           'gate':gate,'recovery_DEV':student_dev})
        old._atomic_json(output/'config.json',identity)
        old._atomic_json(output/'matrix_split_manifest.json',manifest)
        print('RECOVERY_STAGE2_ACTIVATED=TRUE reference_readout_learned=TRUE',flush=True)

    if not args.resume:
        control.load_state_dict(full.state_dict(),strict=True)
    opt=torch.optim.AdamW(full.parameters(),lr=args.lr,weight_decay=0)
    copt=torch.optim.AdamW(control.parameters(),lr=args.lr,weight_decay=0)
    sampler=torch.Generator(device='cpu').manual_seed(args.seed+947)
    step=0
    best_step=cbest_step=0
    best_rank=cbest_rank=None
    history=[]
    if args.resume:
        step=int(progress['step'])
        if args.steps<step:raise RuntimeError('RECOVERY_CANNOT_REWIND_STEPS')
        _restore(progress,full,control,opt,copt,sampler)
        best_step=int(progress['best_step']);cbest_step=int(progress['control_best_step'])
        best_rank=tuple(progress['best_rank']);cbest_rank=tuple(progress['control_best_rank'])
        history=list(progress['history'])
        print(f'RECOVERY_FULL_STATE_RESUME step={step} target={args.steps}',flush=True)
    else:
        recovery_dev,_=score(full,features['dev'],data['dev']['rows'],domain,device,args.eval_batch_size,args.tolerance)
        recovery_control_dev,_=score(control,ablated['dev'],data['dev']['rows'],domain,device,args.eval_batch_size,args.tolerance)
        best_rank=quality(recovery_dev)
        cbest_rank=quality(recovery_control_dev)
        old._atomic_save(output/'best.pt',{'schema':SCHEMA+'.selected','identity':identity,
            'step':0,'phase':'TRAINED_RECOVERY','model':copy.deepcopy(full.state_dict())})
        old._atomic_save(output/'best_reference_only.pt',{'schema':SCHEMA+'.selected',
            'identity':identity,'step':0,'phase':'TRAINED_RECOVERY','model':copy.deepcopy(control.state_dict())})
        history=[{'step':0,'stage':'TRAINED_RECOVERY','dev_min_joint5':best_rank[0],
                  'control_dev_min_joint5':cbest_rank[0]}]
        _atomic_progress(output/'latest.pt',full,control,opt,copt,sampler,0,
                         best_step,best_rank,cbest_step,cbest_rank,history,identity)

    truth=data['train']['numeric']
    pools=data['train']['tier_indices']
    def probe(step, l, lc):
        nonlocal best_step,best_rank,cbest_step,cbest_rank
        d,_=score(full,features['dev'],data['dev']['rows'],domain,device,args.eval_batch_size,args.tolerance)
        cd,_=score(control,ablated['dev'],data['dev']['rows'],domain,device,args.eval_batch_size,args.tolerance)
        r=quality(d);cr=quality(cd)
        if r>best_rank:
            best_rank=r;best_step=step
            old._atomic_save(output/'best.pt',{'schema':SCHEMA+'.selected','identity':identity,
                'step':step,'phase':'JOINT_TRAINED','model':copy.deepcopy(full.state_dict())})
        if cr>cbest_rank:
            cbest_rank=cr;cbest_step=step
            old._atomic_save(output/'best_reference_only.pt',{'schema':SCHEMA+'.selected',
                'identity':identity,'step':step,'phase':'CONTROL_TRAINED','model':copy.deepcopy(control.state_dict())})
        history.append({'step':step,'stage':'JOINT_TRAINED','dev_min_joint5':r[0],
                        'control_dev_min_joint5':cr[0],'train_loss':l,'control_loss':lc,
                        'best_step':best_step,'best_dev':best_rank[0]})
        _atomic_progress(output/'latest.pt',full,control,opt,copt,sampler,step,
                         best_step,best_rank,cbest_step,cbest_rank,history,identity)
        old._atomic_json(output/'history.json',{'schema':SCHEMA,'probes':history})
        print(f'joint step={step}/{args.steps} DEV_min_joint5={r[0]:.4f} '
              f'control_DEV_min_joint5={cr[0]:.4f} best_step={best_step} '
              f'best_DEV={best_rank[0]:.4f} loss={l:.7f}',flush=True)

    for curr in range(step+1,args.steps+1):
        idx=[]
        for i,tier in enumerate(ref.TIERS):
            n=args.batch_size//len(ref.TIERS)+(i<args.batch_size%len(ref.TIERS))
            if n:
                pool=pools[tier]
                idx.append(pool[torch.randint(len(pool),(n,),generator=sampler)])
        selected=torch.cat(idx)
        batch=features['train'][selected].to(device)
        y=truth[selected].to(device)
        full.train();opt.zero_grad(set_to_none=True)
        lf=loss(full,batch,y,domain)
        if not bool(torch.isfinite(lf)):
            raise FloatingPointError('RECOVERY_NONFINITE_TRAIN_LOSS')
        lf.backward()
        nn.utils.clip_grad_norm_(full.parameters(),1.,error_if_nonfinite=True)
        opt.step()
        control.train();copt.zero_grad(set_to_none=True)
        lc=loss(control,ablated['train'][selected].to(device),y,domain)
        if not bool(torch.isfinite(lc)):
            raise FloatingPointError('RECOVERY_NONFINITE_CONTROL_LOSS')
        lc.backward()
        nn.utils.clip_grad_norm_(control.parameters(),1.,error_if_nonfinite=True)
        copt.step()
        if curr%args.probe_every==0 or curr==args.steps:
            probe(curr,float(lf.detach()),float(lc.detach()))

    def selected_score(target_path,which,split):
        chk=torch.load(output/target_path,weights_only=True,map_location='cpu')
        if chk.get('identity')!=identity:
            raise RuntimeError('RECOVERY_BEST_CHECKPOINT_IDENTITY_DRIFT')
        which.load_state_dict(chk['model'],strict=True)
        inp=features[split] if target_path=='best.pt' else ablated[split]
        return score(which,inp,data[split]['rows'],domain,device,args.eval_batch_size,args.tolerance)
    dev,dp=selected_score('best.pt',full,'dev')
    test,tp=selected_score('best.pt',full,'test')
    cdev,_=selected_score('best_reference_only.pt',control,'dev')
    ctest,_=selected_score('best_reference_only.pt',control,'test')
    # Read-only source ablations of the selected fused checkpoint.
    # No ablation result participates in model selection.
    ablations={}
    for split in ('dev','test'):
        ablations[split]={}
        for i,name in enumerate(ORDER):
            detached=features[split].clone()
            detached[:,i*head_dim:(i+1)*head_dim]=0.
            metric,_=score(full,detached,data[split]['rows'],domain,device,args.eval_batch_size,args.tolerance)
            ablations[split][name]=quality(metric)[0]
    for split,pred in (('dev',dp),('test',tp)):
        with (output/f'predictions_{split}.jsonl').open('w',encoding='utf8') as stream:
            for r,pair in zip(data[split]['rows'],pred):
                stream.write(json.dumps({'instance':r['instance'],'tier':r['tier'],
                    'mask':r['mask'],'X':pair[0],'Y':pair[1],'Z':mr._z(pair)})+'\n')
    report={'schema':SCHEMA+'.report','identity':identity,
            'source_checkpoints_unchanged':True,'test_used_to_select':False,
            'evaluation_status':'EXPLORATORY_NOT_BLIND_CERTIFICATION',
            'recovery_gate':gate,'joint_updates_completed':args.steps,
            'best_joint_step':best_step,'best_control_step':cbest_step,
            'best_phase':'RECOVERED_TEACHER' if best_step==0 else 'IMPROVED_JOINT',
            'DEV':{'joined':dev,'control':cdev,'sources':bases['dev']},
            'TEST':{'joined':test,'control':ctest,'sources':bases['test']},
            'source_ablation_dev_test':ablations,
            'gain_over_reference_only_DEV':quality(dev)[0]-quality(cdev)[0],
            'gain_over_reference_only_TEST':quality(test)[0]-quality(ctest)[0]}
    old._atomic_json(output/'report.json',report)
    print(json.dumps({'report':str(output/'report.json'),
        'recovery_gate':gate['status'],'best_joint_step':best_step,
        'DEV_joined':quality(dev)[0],'TEST_joined':quality(test)[0],
        'TEST_control':quality(ctest)[0]},indent=2),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--duo-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-feature-cache',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--steps',type=int,default=12000,
                   help='Total joint optimizer updates; supervised recovery fit runs first')
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
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
