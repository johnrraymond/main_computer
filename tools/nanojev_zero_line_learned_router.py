#!/usr/bin/env python3
"""Learn to select the certified zero-line model or one frozen magnitude bank.

No magnitude classifier labels and no target-derived oracle route enter training.
A small neural router receives *only frozen neural activations* from the input
operands and is optimized against the final signed-sum likelihood. Inference
selects exactly one expert using the neural router. There is no Python fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, exact_mask, sample_absolute_band, make_cache, score, atomic_json,
    atomic_pt, parse_bands,
)

SCHEMA = 'nanojev.zero-line-learned-router.v1'
BASE_RADIUS = 127


class NeuralRouter(nn.Module):
    """No operand arithmetic, hard-coded magnitude thresholds, or answer inputs."""
    def __init__(self, feature_width: int, token_width: int, hidden: int = 128,
                 include_tokens: bool = True):
        super().__init__()
        self.include_tokens = bool(include_tokens)
        self.feature_width = feature_width
        self.token_width = token_width
        inputs = feature_width + (token_width if include_tokens else 0)
        self.net = nn.Sequential(nn.LayerNorm(inputs), nn.Linear(inputs, hidden),
                                 nn.GELU(), nn.Linear(hidden, hidden), nn.GELU(),
                                 nn.Linear(hidden, 1))

    def forward(self, features: torch.Tensor, tokens: torch.Tensor) -> torch.Tensor:
        x = torch.cat((features, tokens.flatten(1)), -1) if self.include_tokens else features
        return self.net(x).squeeze(-1)


def cache_with_tokens(base, cards, device, batch_size, include_tokens=True):
    """Cache frozen features and input token embeddings; targets are stored separately."""
    old = make_cache(base, cards, device, batch_size)
    if include_tokens:
        with torch.no_grad():
            tokens = torch.cat([base.input(xs.to(device)).flatten(1).detach()
                                for xs in old['operands'].split(batch_size)], 0)
    else:
        tokens = old['features'].new_empty((len(old['target']), 0))
    old['tokens'] = tokens
    return old


def frozen_bank_outputs(bank, data, chunk=2048):
    """No inference from target or base correctness."""
    bank.eval()
    with torch.no_grad():
        result = torch.cat([bank(features) for features in data['features'].split(chunk)], 0)
    return result.detach()


def gold_log_likelihood(logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """Signed digit-token likelihood for every example, shape [N,8]."""
    sign = F.log_softmax(logits[:, :3], dim=-1).gather(1, (targets.sign().long() + 1)[:, None])
    digits = F.log_softmax(logits[:, 3:].reshape(-1, 7, 10), dim=-1)
    digits = digits.gather(-1, target_digits(targets).unsqueeze(-1)).squeeze(-1)
    return torch.cat((sign, digits), 1)


def mixture_loss(router_logits: torch.Tensor, base_logits: torch.Tensor,
                 bank_logits: torch.Tensor, targets: torch.Tensor):
    """Only the correct final sum supervises training; no bank-ID labels."""
    b = gold_log_likelihood(base_logits, targets)
    k = gold_log_likelihood(bank_logits, targets)
    # A *single* expert must explain the entire answer. Mixing individual
    # digit likelihoods could train a token-wise oracle that cannot actually
    # be deployed with the hard base/bank decision below.
    whole_b = b[:, 0] + 2 * b[:, 1:].mean(-1)
    whole_k = k[:, 0] + 2 * k[:, 1:].mean(-1)
    mixed = torch.logaddexp(F.logsigmoid(-router_logits) + whole_b,
                            F.logsigmoid(router_logits) + whole_k)
    return -mixed.mean()


def hard_select(router_logits: torch.Tensor, base_logits: torch.Tensor,
                bank_logits: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Hard inference. No oracle, magnitude rule, threshold exception or fallback."""
    decision = router_logits >= 0.
    return torch.where(decision[:, None], bank_logits, base_logits), decision


@torch.inference_mode()
def evaluate_router(router, data, bank_outputs, batch_size=2048):
    decisions, combined = [], []
    router.eval()
    for i in range(0, len(data['target']), batch_size):
        j = i + batch_size
        d = router(data['features'][i:j], data['tokens'][i:j])
        selected, mask = hard_select(d, data['original'][i:j], bank_outputs[i:j])
        decisions.append(mask)
        combined.append(selected)
    route = torch.cat(decisions)
    chosen = torch.cat(combined)
    y = data['target']
    base_good = exact_mask(data['original'], y)
    bank_good = exact_mask(bank_outputs, y)
    chosen_good = exact_mask(chosen, y)
    # Evaluation-only *reference* from the observable INPUT magnitude;
    # never used in forward(), optimization, or route selection.
    reference = (data['operands'].abs().amax(-1) > BASE_RADIUS)
    reference &= (data['operands'].abs().amax(-1) >= data['band'][0])
    reference &= (data['operands'].abs().amax(-1) <= data['band'][1])
    reference = reference.to(route.device)
    deterministic = torch.where(reference[:, None], bank_outputs, data['original'])
    fixed = exact_mask(deterministic, y)
    near = y.abs()<=2
    zero = y==0
    near_harm = int((near & base_good & ~chosen_good).sum())
    return {
        'cards': int(len(y)),
        'base_accuracy': float(base_good.float().mean()),
        'bank_accuracy': float(bank_good.float().mean()),
        'deterministic_input_magnitude_accuracy': float(fixed.float().mean()),
        'learned_arithmetic_accuracy': float(chosen_good.float().mean()),
        'learned_bank_selection_rate': float(route.float().mean()),
        'match_input_magnitude_reference_rate': float((route == reference).float().mean()),
        'reference_bank_count': int(reference.sum()),
        'learned_selected_bank_count': int(route.sum()),
        'newly_correct_vs_base': int((~base_good & chosen_good).sum()),
        'newly_wrong_vs_base': int((base_good & ~chosen_good).sum()),
        'near_zero_cards':int(near.sum()),
        'near_zero_base_correct':int((near & base_good).sum()),
        'near_zero_learned_correct':int((near & chosen_good).sum()),
        'near_zero_newly_wrong':near_harm,
        'exact_zero_cards':int(zero.sum()),
        'exact_zero_learned_correct':int((zero & chosen_good).sum()),
        'oracle_any_expert_upper_bound': float((base_good | bank_good).float().mean()),
        'oracle_note': 'DIAGNOSTIC ONLY: labels used for this upper bound, never training or inference',
        'false_switches_when_base_only_correct': int((route & base_good & ~bank_good).sum()),
        'missed_switches_when_bank_only_correct': int((~route & bank_good & ~base_good).sum()),
        'selected_bad_with_good_alternative': int((~chosen_good & (base_good | bank_good)).sum()),
    }


def set_band(data, band):
    data['band'] = tuple(band)
    return data


def is_safe_output(output: Path, source_root: Path, bank_root: Path):
    for p in (source_root, bank_root):
        if output == p or output in p.parents or p in output.parents:
            raise RuntimeError('ZERO_ROUTER_OUTPUT_SOURCE_OVERLAP')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('ZERO_ROUTER_OUTPUT_EXISTS')


def validate_bank(source, trained, low, high, device, base):
    cfg = json.loads((trained / 'config.json').read_text(encoding='utf-8'))
    if cfg['source_sha256'] != source['checkpoint_sha256']:
        raise RuntimeError('ZERO_ROUTER_SOURCE_MISMATCH')
    if cfg.get('routing') != 'max_abs_operand_input_only':
        raise RuntimeError('ZERO_ROUTER_UNEXPECTED_BANK_SOURCE')
    if [low, high] not in [list(x) for x in cfg['bands']]:
        raise RuntimeError('ZERO_ROUTER_BAND_MISSING')
    filepath = trained / f'bank_{low}_{high}' / 'promoted.pt'
    if not filepath.is_file():
        raise RuntimeError('ZERO_ROUTER_BANK_NOT_PROMOTED')
    payload = torch.load(filepath, map_location=device, weights_only=True)
    if payload['source_sha256'] != source['checkpoint_sha256'] or payload['band'] != [low,high]:
        raise RuntimeError('ZERO_ROUTER_BANK_CHECKPOINT_MISMATCH')
    specialist = AbsoluteBank(int(base.query.shape[0]), int(cfg['parameters']['hidden'])).to(device)
    specialist.load_state_dict(payload['bank'], strict=True)
    specialist.eval().requires_grad_(False)
    return specialist, filepath, cfg


def prepare(base, bank, rows, device, batch_size, band, include_tokens):
    cache = set_band(cache_with_tokens(base, rows, device, batch_size, include_tokens), band)
    cache['bank_logits'] = frozen_bank_outputs(bank, cache)
    return cache


def train_router(router, train_old, train_new, dev_old, dev_new, *, steps,
                 batch_size, probe_every, seed, learning_rate, bank_outputs_old=None,
                 bank_outputs_new=None, output=None):
    if not 1 <= steps or not 2 <= batch_size or not 1 <= probe_every:
        raise ValueError('ZERO_ROUTER_BAD_STEPS')
    rng = random.Random(seed)
    optimizer = torch.optim.AdamW(router.parameters(), lr=learning_rate, weight_decay=0.)
    history = []
    best_state = None
    best_step = None
    best_quality = None
    if bank_outputs_old is None: bank_outputs_old = train_old['bank_logits']
    if bank_outputs_new is None: bank_outputs_new = train_new['bank_logits']
    for step in range(1, steps+1):
        old_count = batch_size//2
        new_count = batch_size-old_count
        a = torch.tensor([rng.randrange(len(train_old['target'])) for _ in range(old_count)],
                         dtype=torch.long, device=train_old['target'].device)
        b = torch.tensor([rng.randrange(len(train_new['target'])) for _ in range(new_count)],
                         dtype=torch.long, device=train_new['target'].device)
        router.train()
        prediction_a = router(train_old['features'][a], train_old['tokens'][a])
        prediction_b = router(train_new['features'][b], train_new['tokens'][b])
        logits = torch.cat((prediction_a, prediction_b))
        base_pred = torch.cat((train_old['original'][a],train_new['original'][b]))
        bank_pred = torch.cat((bank_outputs_old[a],bank_outputs_new[b]))
        truth = torch.cat((train_old['target'][a],train_new['target'][b]))
        loss = mixture_loss(logits, base_pred, bank_pred, truth)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(router.parameters(),1.)
        optimizer.step()
        if step%probe_every and step != steps:continue
        old_metrics = evaluate_router(router, dev_old, dev_old['bank_logits'])
        new_metrics = evaluate_router(router, dev_new, dev_new['bank_logits'])
        row={'step':step,'loss':float(loss.detach()),'center':old_metrics,'band_dev':new_metrics}
        history.append(row)
        # Candidate selection uses CENTER and DEV only, never OOD.
        quality = (int(old_metrics['learned_arithmetic_accuracy']==1. and
                       new_metrics['near_zero_newly_wrong']==0),
                   old_metrics['learned_arithmetic_accuracy'],
                   new_metrics['learned_arithmetic_accuracy'])
        if best_quality is None or quality > best_quality:
            best_quality = quality
            best_step = step
            best_state = {k:v.detach().clone() for k,v in router.state_dict().items()}
        print(f'ZERO_LEARNED_ROUTER step={step}/{steps} loss={row["loss"]:.5f} '
              f'CENTER={old_metrics["learned_arithmetic_accuracy"]:.5f} '
              f'BAND_DEV={new_metrics["learned_arithmetic_accuracy"]:.5f} '
              f'BANK_ROUTE={new_metrics["learned_bank_selection_rate"]:.4f} '
              f'CENTER_ROUTE={old_metrics["learned_bank_selection_rate"]:.4f}',flush=True)
        if output is not None:
            atomic_pt(output/'latest.pt', {'schema':SCHEMA+'.train', 'step':step,
                       'router':router.state_dict(),'optimizer':optimizer.state_dict(),
                       'rng':rng.getstate()})
            atomic_json(output/'progress.json',{'schema':SCHEMA,'history':history})
    if best_state is not None:
        router.load_state_dict(best_state,strict=True)
    return history, best_step


@torch.no_grad()
def independent_inference(base, bank, router, a, b, device, include_tokens=True):
    """Deployed forward path: ONLY A and B. No target object is constructed."""
    base.eval(); bank.eval(); router.eval()
    operands = torch.tensor([[a,b]],dtype=torch.long,device=device)
    slot = {}
    def capture(_module, _inputs, result):
        slot['features'] = result.detach()
    handle = base.norm.register_forward_hook(capture)
    try:
        original = base(operands)
    finally:
        handle.remove()
    if 'features' not in slot:
        raise RuntimeError('ZERO_ROUTER_INFERENCE_BASE_FEATURE_MISSING')
    features = slot['features']
    tokens = (base.input(operands).flatten(1) if include_tokens else
              features.new_empty((len(features), 0)))
    original_logits = torch.cat((original['sign'],original['digits'].reshape(-1,70)),dim=-1)
    specialist_logits = bank(features)
    decision_logit = router(features,tokens)
    selected,choice = hard_select(decision_logit,original_logits,specialist_logits)
    sign = selected[:, :3].argmax(-1).long()-1
    digs = selected[:, 3:].reshape(-1,7,10).argmax(-1).long()
    place = digs.new_tensor([10**i for i in range(7)])
    return {'a':a,'b':b,'selected':'bank' if bool(choice[0]) else 'base',
            'prediction':int((sign*(digs*place).sum(-1))[0]),
            'bank_probability':float(torch.sigmoid(decision_logit)[0]),
            'route_source':'neural_features_only'}


def run(args):
    band = parse_bands(args.band)
    if len(band)!=1 or band[0][0]<=BASE_RADIUS:
        raise ValueError('ZERO_ROUTER_EXPECTS_ONE_EXTERNAL_MAGNITUDE_BAND')
    low,high = band[0]
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_ROUTER_CUDA_UNAVAILABLE')
    if args.batch_size < 2 or min(args.batch_size,args.eval_batch_size,args.steps,args.probe_every,
           args.hidden,args.per_stratum,args.cpu_threads) < 1:
        raise ValueError('ZERO_ROUTER_BAD_OPTIONS')
    if not 0 < args.lr < 1 or not all(0 <= x <= 1 for x in (
            args.min_accuracy,args.allowed_gap,args.max_center_bank_rate,
            args.min_band_bank_rate)):
        raise ValueError('ZERO_ROUTER_BAD_THRESHOLDS')
    src=Path(args.ladder_output).expanduser().resolve()
    bank_root=Path(args.bank_output).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    is_safe_output(out,src,bank_root)
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    base,source = load_certified_model(src,args.device)
    base.eval().requires_grad_(False)
    bank,bank_file,bank_config=validate_bank(source,bank_root,low,high,args.device,base)
    before=(sha256(source['checkpoint']),sha256(bank_file))
    include_tokens=not args.backbone_only
    old = prepare(base,bank,deck(BASE_RADIUS),args.device,args.eval_batch_size,(low,high),include_tokens)
    if not bool(exact_mask(old['original'],old['target']).all()):
        raise RuntimeError('ZERO_ROUTER_CERTIFIED_BASE_DECK_FAILED')
    samples=sample_absolute_band(low,high,args.seed,args.per_stratum)
    new={key:prepare(base,bank,rows,args.device,args.eval_batch_size,(low,high),include_tokens)
         for key,rows in samples.items()}
    if min(len(new[k]['target']) for k in ('train','dev','ood'))==0:
        raise RuntimeError('ZERO_ROUTER_EMPTY_SPLIT')
    n_features=old['features'].shape[-1]
    n_tokens=old['tokens'].shape[-1]
    router=NeuralRouter(n_features,n_tokens,args.hidden,include_tokens).to(args.device)
    out.mkdir(parents=True,exist_ok=True)
    config={'schema':SCHEMA,'band':[low,high],'source_sha256':before[0],
            'specialist_sha256':before[1],
            'router_input':'frozen_backbone_and_input_tokens' if include_tokens else 'frozen_backbone_only',
            'router_training':'final_signed_sum_mixture_likelihood_only',
            'router_inference':'hard_neural_choice_no_fallback',
            'parameters':{k:v for k,v in vars(args).items()
                          if k not in ('ladder_output','bank_output','output','device')}}
    atomic_json(out/'config.json',config)
    # Baseline gate before router changes: deterministic input-magnitude route
    # is diagnostic and cannot contribute gradients or select deployed outputs.
    training_history, selected_step=train_router(router,old,new['train'],old,new['dev'],
                  steps=args.steps,batch_size=args.batch_size,probe_every=args.probe_every,
                  seed=args.seed+1000,learning_rate=args.lr,output=out)
    center=evaluate_router(router,old,old['bank_logits'])
    dev=evaluate_router(router,new['dev'],new['dev']['bank_logits'])
    ood=evaluate_router(router,new['ood'],new['ood']['bank_logits'])
    # Adjacent magnitude band is never passed to training, selection or optimizer.
    adjacent=None
    if high<511:
        end=min(511, high+(high-low+1))
        if high+1<=end:
            adjacent_rows=sample_absolute_band(high+1,end,args.seed,args.per_stratum,extrapolation=True)['ood']
            if adjacent_rows:
                adjacent_cache=prepare(base,bank,adjacent_rows,args.device,args.eval_batch_size,(low,high),include_tokens)
                adjacent=evaluate_router(router,adjacent_cache,adjacent_cache['bank_logits'])
    source_same=(sha256(source['checkpoint']),sha256(bank_file)) == before
    if not source_same:raise RuntimeError('ZERO_ROUTER_SOURCE_OR_BANK_MUTATED')
    eligible=(center['learned_arithmetic_accuracy']==1. and
              center['learned_bank_selection_rate']<=args.max_center_bank_rate and
              dev['learned_bank_selection_rate']>=args.min_band_bank_rate and
              ood['learned_bank_selection_rate']>=args.min_band_bank_rate and
              dev['near_zero_newly_wrong']==0 and
              ood['near_zero_newly_wrong']==0 and
              dev['learned_arithmetic_accuracy']>=args.min_accuracy and
              ood['learned_arithmetic_accuracy']>=args.min_accuracy and
              dev['learned_arithmetic_accuracy']+args.allowed_gap>=dev['deterministic_input_magnitude_accuracy'] and
              ood['learned_arithmetic_accuracy']+args.allowed_gap>=ood['deterministic_input_magnitude_accuracy'])
    if not bool(torch.isfinite(torch.cat([p.detach().flatten() for p in router.parameters()])).all()):
        raise RuntimeError('ZERO_ROUTER_NONFINITE')
    atomic_pt(out/'candidate.pt',{'schema':SCHEMA+'.candidate',
                 'source_sha256':before[0],'specialist_sha256':before[1],
                 'band':[low,high], 'router_input':config['router_input'],
                 'feature_width':n_features,'token_width':n_tokens,
                 'hidden':args.hidden,'router':router.state_dict()})
    if eligible:
        atomic_pt(out/'promoted.pt',{'schema':SCHEMA+'.promoted',
                 'source_sha256':before[0], 'specialist_sha256':before[1],
                 'band':[low,high], 'router_input':config['router_input'],
                 'feature_width':n_features,'token_width':n_tokens,
                 'hidden':args.hidden,'router':router.state_dict()})
    report={'schema':SCHEMA,'source_immutable':True,'bank_immutable':True,
            'test_untouched':True,'no_oracle_inference':True,'no_python_fallback':True,
            'trained_only_on_final_answer':True,'eligible':eligible,
            'selected_step_using_center_and_dev_only':selected_step,
            'center_all_65025':center,'band_dev':dev,'band_ood':ood,
            'adjacent_untrained':adjacent,'history':training_history,
            'checkpoint':'promoted.pt' if eligible else 'candidate.pt'}
    atomic_json(out/'result.json',report)
    print('ZERO_LEARNED_ROUTER_RESULT='+json.dumps({
        'eligible':eligible,
        'selected_step':selected_step,
        'center_exact':center['learned_arithmetic_accuracy'],
        'center_wrong':center['newly_wrong_vs_base'],
        'center_bank_selection':center['learned_bank_selection_rate'],
        'band_dev_near_zero_new_errors':dev['near_zero_newly_wrong'],
        'band_ood_near_zero_new_errors':ood['near_zero_newly_wrong'],
        'band_dev_learned':dev['learned_arithmetic_accuracy'],
        'band_dev_python_reference':dev['deterministic_input_magnitude_accuracy'],
        'band_ood_learned':ood['learned_arithmetic_accuracy'],
        'band_ood_python_reference':ood['deterministic_input_magnitude_accuracy'],
        'band_ood_bank_selection':ood['learned_bank_selection_rate'],
        'adjacent_learned':adjacent['learned_arithmetic_accuracy'] if adjacent else None,
        'originals_unchanged':source_same,'report':str(out/'result.json')},sort_keys=True),flush=True)
    return report


def predict(args):
    source_root=Path(args.ladder_output).expanduser().resolve()
    bank_root=Path(args.bank_output).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    payload_path=out/('promoted.pt' if args.promoted_only else 'candidate.pt')
    payload=torch.load(payload_path,map_location=args.device,weights_only=True)
    low,high=payload['band']
    base,source=load_certified_model(source_root,args.device)
    if source['checkpoint_sha256']!=payload['source_sha256']:
        raise RuntimeError('ZERO_ROUTER_PREDICT_SOURCE_MISMATCH')
    bank,bank_path,_=validate_bank(source,bank_root,low,high,args.device,base)
    if sha256(bank_path)!=payload['specialist_sha256']:
        raise RuntimeError('ZERO_ROUTER_PREDICT_BANK_MISMATCH')
    include_tokens=payload['router_input']=='frozen_backbone_and_input_tokens'
    router=NeuralRouter(payload['feature_width'],payload['token_width'],payload['hidden'],include_tokens).to(args.device)
    router.load_state_dict(payload['router'],strict=True)
    router.eval().requires_grad_(False)
    base.eval().requires_grad_(False)
    with torch.no_grad():
        result=independent_inference(base,bank,router,args.a,args.b,args.device,include_tokens)
    print('ZERO_LEARNED_ROUTER_INFERENCE='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=['train','predict'],default='train')
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--bank-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--band',default='128:159')
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--probe-every',type=int,default=200)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--hidden',type=int,default=128)
    p.add_argument('--per-stratum',type=int,default=256)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--min-accuracy',type=float,default=.9)
    p.add_argument('--allowed-gap',type=float,default=.02)
    p.add_argument('--max-center-bank-rate',type=float,default=.01)
    p.add_argument('--min-band-bank-rate',type=float,default=.90)
    p.add_argument('--backbone-only',action='store_true',
                   help='Ablation: do not supply frozen operand-token embeddings')
    p.add_argument('--a',type=int,default=149)
    p.add_argument('--b',type=int,default=-133)
    p.add_argument('--promoted-only',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return predict(args) if args.phase=='predict' else run(args)


if __name__=='__main__':main()
