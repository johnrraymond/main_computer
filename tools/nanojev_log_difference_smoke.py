#!/usr/bin/env python3
"""NanoJev learned (a,b) logarithmic-difference numerical-output A/B smoke.

Research question: does a learned pair whose squared difference decodes to the
answer improve arithmetic learning over an equally sized direct numeric head?

For real target y, enforce |a|>|b| and decode in log magnitude space:
  y_hat = 1/2 Log(a^2 - b^2)
        = log|a| + 1/2 log(1-exp(-2*(log|a|-log|b|))).

Never exponentiate the large learned log magnitudes. The exact cancellation
singularity is a SEPARATE null signal; target y=0 requires a^2-b^2=1.

Controls share the same seed, network parameter count, training rows/batches,
source tuple protocol, and frozen feature source. Neither arm sees oracle y as
an input. Does not modify existing NanoJev checkpoints or frontiers.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import time

import numpy as np

from tools.nanojev_qwen_c2_compressive_train import frontier_deck, outer_shell
from tools.nanojev_qwen_c2_local_train import answer, batched, parse_centers

SCHEMA = 'nanojev.log-difference-output-smoke.v2'
# Stay on the mathematically real branch for ordinary integer answers. A bounded
# learned gap keeps the training gradient finite without changing the zero test.
MIN_GAP = .025
MAX_GAP = 1.50
LOG_2 = math.log(2.)


def log1mexp(torch, x):
    """Stable log(1-exp(x)) on x<=0; exact x=0 returns -inf.

    Mächler's two-regime identity. Use float64 for near-null diagnostics.
    The two branches are evaluated at safe clipped values to avoid a NaN in
    an inactive branch poisoning backward on platforms with eager torch.where.
    """
    low = x <= -LOG_2
    safe_low = torch.minimum(x, x.new_full((), -LOG_2))
    safe_high = torch.maximum(x, x.new_full((), -LOG_2))
    return torch.where(low, torch.log1p(-torch.exp(safe_low)),
                       torch.log(-torch.expm1(safe_high)))


def decode_log_pair(torch, log_abs_a, log_abs_b):
    """Principal continuation of 1/2 Log(a²-b²) without exp/square/subtract.

    Return (real_log_magnitude, imaginary_component, exact_null, branch_sign).
    For real a,b, imaginary is 0 for positive diff and pi/2 for negative.
    At exact cancellation, real=-inf; imaginary is NaN (not a finite answer).
    """
    if log_abs_a.shape != log_abs_b.shape:
        raise ValueError('log magnitude shape mismatch')
    larger = torch.maximum(log_abs_a, log_abs_b)
    gap = (log_abs_a - log_abs_b).abs()
    real = larger + 0.5 * log1mexp(torch, -2.0 * gap)
    null = (log_abs_a == log_abs_b)
    real = torch.where(null, torch.full_like(real, -math.inf), real)
    pos = (log_abs_a > log_abs_b)
    imaginary = torch.where(null, torch.full_like(real, math.nan),
                            torch.where(pos, torch.zeros_like(real),
                                        torch.full_like(real, math.pi / 2)))
    side = torch.where(null, torch.zeros_like(real),
                       torch.where(pos, torch.ones_like(real), -torch.ones_like(real)))
    return real, imaginary, null, side


def half_logdiff_from_log_gap(torch, log_gap):
    """0.5*log(1-exp(-2*exp(log_gap))), stable even below float64 gap underflow.

    log_gap=log(log|a|-log|b|). For log_gap << 0 the exact correction is
    .5*(log(2)+log_gap)+O(exp(log_gap)); using the asymptotic branch avoids
    materializing indistinguishably close a and b as floating-point numbers.
    This is a latent log-gap representation, NOT a reconstructed float pair.
    """
    cutoff=-14.  # asymptotic absolute error < 4.2e-7 for log_gap < -14
    safe=torch.maximum(log_gap,log_gap.new_full((),cutoff))
    main=0.5*log1mexp(torch,-2.*torch.exp(safe))
    asymptote=0.5*(LOG_2+log_gap)
    return torch.where(log_gap<cutoff,asymptote,main)


def numeric_features(points):
    """Only the four source operands and single-operand transforms; no products/answers."""
    raw = np.asarray(points, dtype=np.float64)
    signed_log = np.sign(raw) * np.log1p(np.abs(raw))
    return np.concatenate((raw, signed_log), axis=1).astype(np.float32)


def zero_line_points(centers, radius):
    """Add actual pa=qb cancellation and two immediate flanking outcomes."""
    rows = set()
    for p, q, a, b in centers:
        gcd = math.gcd(p, q)
        step_a, step_b = q // gcd, p // gcd
        k0 = round((a / step_a + b / step_b) / 2)
        width = max(5, radius // 2)
        for k in range(k0-width, k0+width+1):
            va, vb = step_a*k, step_b*k
            for shift in (-2, -1, 0, 1, 2):
                rows.add((p, q, va, vb+shift))
    return sorted(rows)


def data_decks(args):
    centers = parse_centers(args.centers)
    inner = set(frontier_deck(centers, args.radius, args.seed,
                              cap=args.train_cap, replay_fraction=0.,
                              vary_coefficients=args.vary_coefficients,
                              coefficient_radius=args.coefficient_radius))
    inner.update(zero_line_points(centers, args.radius))
    train = sorted(inner)  # Acquisition: no withheld examples inside the learned radius.
    outside = outer_shell(centers, args.radius, args.seed+31,
                          count=args.outer_count,
                          vary_coefficients=args.vary_coefficients,
                          coefficient_radius=args.coefficient_radius)
    outside = sorted(set(outside)-set(inner))
    if set(train)&set(outside):
        raise AssertionError('FRONTIER_DECK_OVERLAP')
    if not all(len(d)>0 for d in (train, outside)):
        raise ValueError('EMPTY_EVALUATION_DECK')
    if not any(answer(*t)==0 for t in train):
        raise ValueError('NO_EXACT_ZERO_TRAINING_EXAMPLES')
    return {'train':train,'outside':outside}


def digest_points(points):
    return hashlib.sha256(json.dumps(points,separators=(',',':')).encode()).hexdigest()


def _capture_qwen(torch, args, decks):
    """Use existing C2 frozen-Qwen final non-pad-token capture, with no training."""
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from tools.nanojev_qwen_c2_local_train import features
    if args.device.startswith('cuda'):
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        dtype = torch.float32
    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = 'right'
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=dtype,
                                                 trust_remote_code=False).to(args.device).eval()
    if args.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, args.adapter).eval()
    for p in model.parameters():
        p.requires_grad_(False)
    result = {}
    with torch.no_grad():
        for name,rows in decks.items():
            chunks=[]
            for piece in batched(rows,args.capture_batch):
                chunks.append(features(torch,model,tok,piece,args.device,args.max_length).cpu().numpy())
            result[name] = np.concatenate(chunks).astype(np.float32)
    del model, tok
    if args.device.startswith('cuda'):
        torch.cuda.empty_cache()
    return result


def prepare_inputs(torch, args, decks):
    if args.mode == 'numeric':
        raw = {name:numeric_features(rows) for name,rows in decks.items()}
    else:
        raw = _capture_qwen(torch,args,decks)
    # Both arms receive identical train-only standardized features; no test fit.
    mean = raw['train'].astype(np.float64).mean(axis=0)
    scale = raw['train'].astype(np.float64).std(axis=0)
    scale = np.where(scale>1e-6,scale,1.)
    values = {name:torch.tensor(np.clip((x-mean)/scale,-12.,12.),
                                 device=args.device,dtype=torch.float32)
              for name,x in raw.items()}
    return values


def make_net(torch, width, hidden):
    from torch import nn
    class TwoChannel(nn.Module):
        def __init__(self):
            super().__init__()
            self.network = nn.Sequential(nn.LayerNorm(width),nn.Linear(width,hidden),
                                         nn.GELU(),nn.Linear(hidden,hidden),nn.GELU(),
                                         nn.Linear(hidden,2))
        def forward(self,x):
            return self.network(x)
    return TwoChannel()


def predict(torch, net, features, arm, output_scale):
    channels = net(features)
    u, v = channels.unbind(-1)
    if arm == 'direct':
        return output_scale * ((u+v) / math.sqrt(2)), None
    if arm == 'log_cancellation':
        # This arm tests the user's stronger hypothesis: the *closeness* of
        # learned a and b is itself the learned information-bearing quantity.
        # a=exp(log_a), b=a*exp(-exp(log_gap)); the log gap is independently
        # learned and may be too tiny to express by two ordinary fp64 floats.
        log_a=(output_scale*(1.25+torch.nn.functional.softplus(u))).to(torch.float64)
        log_gap=(-output_scale*torch.nn.functional.softplus(v)).to(torch.float64)
        decoded=log_a+half_logdiff_from_log_gap(torch,log_gap)
        return decoded.to(torch.float32), {'log_a':log_a,'log_gap':log_gap}
    if arm != 'log_difference':
        raise ValueError('UNKNOWN_ARM:'+str(arm))
    # Both learned components participate. Their squared difference is positive
    # in the real-valued training branch and their ratio remains substantial.
    gap = MIN_GAP + (MAX_GAP-MIN_GAP)*torch.sigmoid(v)
    log_a = (output_scale*u).to(torch.float64)
    log_b = log_a - gap.to(torch.float64)
    decoded, imaginary, null, side = decode_log_pair(torch,log_a,log_b)
    if torch.any(null) or torch.any(side!=1):
        raise RuntimeError('REAL_LOGDIFF_BRANCH_INVARIANT_FAILED')
    return decoded.to(torch.float32), {'gap':gap,'log_a':log_a,'log_b':log_b}


def _errors(torch, net, X, Y, arm, scale, batch_size):
    net.eval(); preds=[]
    with torch.inference_mode():
        for block in batched(X,batch_size):
            pred,_=predict(torch,net,block,arm,scale)
            preds.append(pred.detach().cpu().numpy().astype(np.float64))
    y=Y.cpu().numpy().astype(np.float64)
    p=np.concatenate(preds)
    delta=np.abs(p-y)
    rounded=(np.rint(p)==y)
    selected={
        'all':np.ones(len(y),dtype=bool),
        'zero':(y==0), 'negative':(y<0), 'positive':(y>0),
        'near_zero':(np.abs(y)<=2),
    }
    groups={}
    for name,mask in selected.items():
        groups[name]={'count':int(mask.sum()),
                      'mae':float(delta[mask].mean()) if mask.any() else None,
                      'rounded_exact_fraction':float(rounded[mask].mean()) if mask.any() else None}
    metrics={'n':len(y),'mae':float(delta.mean()),'rmse':float(np.sqrt(np.mean((p-y)**2))),
             'rounded_exact_fraction':float(rounded.mean()),
             'within_1_fraction':float((delta<=1).mean()),
             'nonfinite_count':int((~np.isfinite(p)).sum()),
             'groups':groups}
    return metrics,p


def evaluate(torch, net, X, Y, arm, scale, batch_size):
    metrics,p=_errors(torch,net,X,Y,arm,scale,batch_size)
    if arm in ('log_difference','log_cancellation'):
        # With b=0 the decoder becomes log|a|, so removing b measures whether
        # the purported second component actually contributes to the result.
        without_b=[]; ratios=[]; gaps=[]; carrier=[]; log_gaps=[]
        net.eval()
        with torch.inference_mode():
            for block in batched(X,batch_size):
                pred, representation=predict(torch,net,block,arm,scale)
                carrier.append(representation['log_a'].cpu().numpy())
                without_b.append(representation['log_a'].cpu().numpy())
                if arm=='log_difference':
                    gap=representation['gap']
                    loggap=torch.log(gap.double())
                else:
                    loggap=representation['log_gap']
                    gap=torch.exp(loggap)
                ratios.append(torch.exp(-gap).cpu().numpy())
                gaps.append(gap.cpu().numpy())
                log_gaps.append(loggap.cpu().numpy())
        counterfactual=np.concatenate(without_b)
        ablation_delta=np.abs(counterfactual-Y.cpu().numpy())
        ratios=np.concatenate(ratios)
        gaps=np.concatenate(gaps)
        log_gaps=np.concatenate(log_gaps)
        carrier=np.concatenate(carrier)
        # Paired causal test: keep learned a for each case; rotate the gap
        # from another case. A near-identical result flags b as decorative.
        if arm=='log_difference':
            shuffled_log_b=carrier-np.roll(gaps,1)
            shuffled,_,_,_=decode_log_pair(torch,torch.from_numpy(carrier),
                                          torch.from_numpy(shuffled_log_b))
        else:
            shuffled=torch.from_numpy(carrier)+half_logdiff_from_log_gap(
                torch,torch.from_numpy(np.roll(log_gaps,1)))
        shuffled_error=np.abs(shuffled.cpu().numpy()-Y.cpu().numpy())
        metrics['b_shuffled_mae']=float(shuffled_error.mean())
        metrics['b_shuffled_change_in_mae']=float(shuffled_error.mean()-metrics['mae'])
        metrics['b_gap_std']=float(gaps.std())
        metrics['b_log_gap_std']=float(log_gaps.std())
        # A raw fp64 pair cannot distinguish a from b if gap is below machine
        # epsilon, even though the symbolic log-gap representation can.
        metrics['raw_float64_pair_unresolvable_fraction']=float(
            (log_gaps<math.log(np.finfo(np.float64).eps/2)).mean())
        metrics['b_ablated_mae']=float(ablation_delta.mean())
        metrics['b_contribution_mean_abs']=float(np.abs(counterfactual-p).mean())
        metrics['b_over_a_min']=float(ratios.min())
        metrics['b_over_a_max']=float(ratios.max())
        metrics['b_over_a_mean']=float(ratios.mean())
        # Do not falsely claim independence: both log magnitudes share log_a.
        metrics['identifiability']='one equation in two learned components; nonunique encoding'
    return metrics,p


def _check_mathematics(torch):
    dtype=torch.float64
    aa=torch.tensor([0.,80.,1e6,1.,0.,-20.],dtype=dtype)
    bb=torch.tensor([-1.,79.,1e6-1e-5,1.,1.,-20.-1e-9],dtype=dtype)
    re,im,null,side=decode_log_pair(torch,aa,bb)
    expected=aa[0]+.5*math.log1p(-math.exp(-2.))
    if abs(re[0].item()-expected)>1e-12:
        raise AssertionError('LOGDIFF_REFERENCE_FAILED')
    if not math.isfinite(re[2].item()) or not math.isfinite(re[1].item()):
        raise AssertionError('LOGDIFF_UNSTABLE_LARGE_MAGNITUDE')
    if not math.isinf(re[3].item()) or re[3].item()>=0 or not bool(null[3]):
        raise AssertionError('EXACT_CANCELLATION_NOT_SINGULAR')
    if not (im[4].item()==math.pi/2 and side[4].item()==-1):
        raise AssertionError('NEGATIVE_DIFFERENCE_BRANCH_WRONG')
    if not (abs(re[5].item()-(-20.+.5*math.log(-math.expm1(-2e-9))))<1e-5):
        raise AssertionError('NEAR_NULL_PRECISION_FAILED')
    # A correctly decoded numerical zero is a^2-b^2=1, NOT exact cancellation.
    n0,_,is_null,_=decode_log_pair(torch,torch.tensor([0.],dtype=dtype),
                                   torch.tensor([-math.inf],dtype=dtype))
    if abs(n0.item())>1e-12 or bool(is_null.item()):
        raise AssertionError('TARGET_ZERO_CONFLATED_WITH_NULL_SINGULARITY')
    fine_gap=torch.tensor([-1000.,-100.,-30.,-14.,-1.],dtype=dtype)
    formal=half_logdiff_from_log_gap(torch,fine_gap)
    if not bool(torch.isfinite(formal).all()) or abs(formal[0].item()-(LOG_2-1000)/2)>1e-10:
        raise AssertionError('LOG_GAP_ASYMPTOTE_FAILED')
    gradient=torch.tensor([.01,.1,1.],dtype=dtype,requires_grad=True)
    rval,*_=decode_log_pair(torch,torch.zeros_like(gradient),-gradient)
    rval.sum().backward()
    if not bool(torch.isfinite(gradient.grad).all()):
        raise AssertionError('LOGDIFF_NONFINITE_GRADIENT')
    return {'stable_log1mexp':True,'large_log_magnitude':True,
            'exact_null_is_singular':True,'principal_negative_branch':True,
            'answer_zero_is_not_log_singularity':True,'near_null_gradient_finite':True,
            'formal_log_gap_underflow_safe':True}


def run(args):
    import torch
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA_UNAVAILABLE')
    if args.steps<1 or args.radius<2 or args.train_cap<32 or args.outer_count<1:
        raise ValueError('INVALID_SMOKE_ARGUMENT')
    if args.batch_size<1 or args.lr<=0 or args.output_scale<=0:
        raise ValueError('INVALID_TRAINING_ARGUMENT')
    if not args.allow_nondeterministic:
        # Must be set before CUDA context creation on NVIDIA to make matmul
        # deterministic. Unavailable deterministic kernels fail rather than lie.
        if args.device.startswith('cuda'):
            import os
            os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.benchmark=False
    torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    if args.device.startswith('cuda'): torch.cuda.manual_seed_all(args.seed)
    if args.cpu_threads>0:torch.set_num_threads(args.cpu_threads)
    math_checks=_check_mathematics(torch)
    decks=data_decks(args)
    X=prepare_inputs(torch,args,decks)
    Y={name:torch.as_tensor([answer(*r) for r in rows],dtype=torch.float32,device=args.device)
       for name,rows in decks.items()}
    train_scale=args.output_scale
    if args.output_scale_mode=='auto':
        train_scale=max(1.,float(Y['train'].abs().max().item()))
    net=make_net(torch,int(X['train'].shape[1]),args.hidden).to(args.device)
    arms={'direct':net,'log_difference':copy.deepcopy(net),
          'log_cancellation':copy.deepcopy(net)}
    opts={name:torch.optim.AdamW(module.parameters(),lr=args.lr,weight_decay=args.weight_decay)
          for name,module in arms.items()}
    rng=np.random.default_rng(args.seed+137)
    batch_indices=[rng.integers(0,len(decks['train']),size=args.batch_size)
                   for _ in range(args.steps)]
    before={arm:evaluate(torch,model,X['train'],Y['train'],arm,train_scale,args.eval_batch)[0]
            for arm,model in arms.items()}
    checkpoints=[];start=time.perf_counter()
    for step,idx in enumerate(batch_indices,1):
        indices=torch.as_tensor(idx,dtype=torch.long,device=args.device)
        x=X['train'].index_select(0,indices)
        target=Y['train'].index_select(0,indices)
        for arm,model in arms.items():
            model.train();opts[arm].zero_grad(set_to_none=True)
            decoded,_=predict(torch,model,x,arm,train_scale)
            # Scale is used for numerical conditioning of the optimization ONLY;
            # decoded answers, oracle targets and all reported errors are unscaled.
            loss=torch.nn.functional.smooth_l1_loss(decoded/train_scale,
                                                    target/train_scale,beta=args.huber_beta)
            if not torch.isfinite(loss):
                raise RuntimeError('NONFINITE_TRAINING_LOSS:'+arm+':'+str(step))
            loss.backward()
            for p in model.parameters():
                if p.grad is not None and not bool(torch.isfinite(p.grad).all()):
                    raise RuntimeError('NONFINITE_TRAINING_GRADIENT:'+arm+':'+str(step))
            torch.nn.utils.clip_grad_norm_(model.parameters(),args.grad_clip)
            opts[arm].step()
        if step in (1,args.steps) or step % args.log_every==0:
            sample={'step':step}
            for arm,model in arms.items():
                sample[arm]=evaluate(torch,model,X['train'],Y['train'],arm,train_scale,args.eval_batch)[0]['mae']
            checkpoints.append(sample)
            if args.verbose: print(json.dumps({'event':'step',**sample}),flush=True)
    elapsed=time.perf_counter()-start
    final={};predictions={}
    for arm,model in arms.items():
        final[arm]={};predictions[arm]={}
        for deck in decks:
            final[arm][deck],predictions[arm][deck]=evaluate(
                torch,model,X[deck],Y[deck],arm,train_scale,args.eval_batch)
    compared={}
    for deck in decks:
        truth=Y[deck].detach().cpu().numpy().astype(np.float64)
        d=np.abs(predictions['direct'][deck]-truth)
        compared[deck]={}
        for arm in ('log_difference','log_cancellation'):
            l=np.abs(predictions[arm][deck]-truth)
            compared[deck][arm]={'candidate_better':int((l<d).sum()),
                                 'direct_better':int((d<l).sum()),
                                 'ties':int((d==l).sum()),
                                 'mae_delta_candidate_minus_direct':float(l.mean()-d.mean())}
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    for deck,rows in decks.items():
        with (output/(deck+'_predictions.csv')).open('w',newline='',encoding='utf-8') as handle:
            w=csv.writer(handle);w.writerow(('p','q','source_a','source_b','truth',
                         'direct','log_difference','log_cancellation',
                         'direct_abs_error','log_difference_abs_error','log_cancellation_abs_error'))
            for i,row in enumerate(rows):
                target=answer(*row)
                d=predictions['direct'][deck][i]
                l=predictions['log_difference'][deck][i]
                c=predictions['log_cancellation'][deck][i]
                w.writerow((*row,target,d,l,c,abs(d-target),abs(l-target),abs(c-target)))
    for arm,model in arms.items():
        torch.save({'schema':SCHEMA,'arm':arm,'model':model.state_dict(),
                    'input_width':int(X['train'].shape[1]),'hidden':args.hidden,
                    'output_scale':train_scale,'seed':args.seed},output/(arm+'.pt'))
    finite=all(m['nonfinite_count']==0 for arm in final.values() for m in arm.values())
    learns={arm:final[arm]['train']['mae']<before[arm]['mae']
            for arm in arms}
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    from tools import nanojev_qwen_c2_compressive_train as c2
    from tools import nanojev_qwen_c2_local_train as c2local
    report={'schema':SCHEMA,'status':'PASS' if finite else 'FAIL',
            'learning_status':'ALL_IMPROVED' if all(learns.values()) else
                              'PARTIAL_OR_NO_IMPROVEMENT',
            'interpretation':'A/B evidence, not proof of superiority or exact integer acquisition',
            'mode':args.mode,'model':args.model if args.mode=='qwen' else None,
            'device':args.device,'torch_version':torch.__version__,
            'seed':args.seed,'steps':args.steps,'radius':args.radius,
            'deterministic_algorithms':not args.allow_nondeterministic,
            'source_sha256':{'smoke':sha(__file__),
                             'c2_deck':sha(c2.__file__),
                             'c2_oracle':sha(c2local.__file__)},
            'output_scale':train_scale,'elapsed_seconds':elapsed,
            'architecture':'matched two-channel MLP; direct, stable real log-difference, near-null log-gap cancellation',
            'source_oracle':'C2: integer p*a - q*b; target never supplied as a feature',
            'acquisition_policy':'all radius samples trained; no DEV/held-out split inside acquisition; outside shell is unseen frontier',
            'mathematical_checks':math_checks,
            'data':{name:{'count':len(rows),'sha256':digest_points(rows),
                           'exact_answer_zeros':sum(answer(*r)==0 for r in rows)}
                    for name,rows in decks.items()},
            'acquired_initial':before,'metrics':final,'paired':compared,
            'learning_improved_from_initial':learns,'acquired_history':checkpoints,
            'limitations':['real answer training uses a^2>b^2; complex continuation is diagnostic only',
                           'near-null arm is a symbolic logarithmic-gap representation: two distinct raw float64 a,b may be unmaterializable',
                           'representations are not identifiable from the scalar answer',
                           'finite precision cannot represent exact cancellation as a finite answer',
                           'C2 operand arithmetic is a local smoke, not the full T/HDB frontier']}
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':report['status'],'learning_status':report['learning_status'],
                      'output':str(output),'acquired_mae':{k:v['train']['mae'] for k,v in final.items()},
                      'outside_mae':{k:v['outside']['mae'] for k,v in final.items()},
                      'mathematical_checks':math_checks},sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('numeric','qwen'),default='numeric',
                   help='Numeric source-operand features; or real frozen-Qwen C2 activations')
    p.add_argument('--model',default='Qwen/Qwen3-0.6B')
    p.add_argument('--adapter',default='')
    p.add_argument('--device',default='cpu')
    p.add_argument('--centers',default='3,4,20,10;3,4,32,16;4,5,30,20;5,6,35,25')
    p.add_argument('--radius',type=int,default=8)
    p.add_argument('--train-cap',type=int,default=640)
    p.add_argument('--outer-count',type=int,default=256)
    p.add_argument('--vary-coefficients',action='store_true')
    p.add_argument('--coefficient-radius',type=int,default=1)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--steps',type=int,default=500)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch',type=int,default=256)
    p.add_argument('--capture-batch',type=int,default=8)
    p.add_argument('--max-length',type=int,default=128)
    p.add_argument('--hidden',type=int,default=96)
    p.add_argument('--lr',type=float,default=.002)
    p.add_argument('--weight-decay',type=float,default=.0001)
    p.add_argument('--grad-clip',type=float,default=2.)
    p.add_argument('--huber-beta',type=float,default=.2)
    p.add_argument('--output-scale-mode',choices=('auto','manual'),default='auto')
    p.add_argument('--output-scale',type=float,default=128.)
    p.add_argument('--cpu-threads',type=int,default=1)
    p.add_argument('--log-every',type=int,default=100)
    p.add_argument('--output',default='runtime/reports/nanojev-log-difference-smoke-v1')
    p.add_argument('--allow-nondeterministic',action='store_true',
                   help='Unsafe for byte-reproducible comparison; opt out of deterministic kernels')
    p.add_argument('--verbose',action='store_true')
    return p


def main(argv=None):
    run(parser().parse_args(argv))


if __name__=='__main__':
    main()
