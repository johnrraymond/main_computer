#!/usr/bin/env python3
"""Numerical HybridAgent v2: contextual latent tokens + jointly trained reasoning.

Separate research run. Frozen original Gaussian remains exact depth-zero control.
Phase A: teacher-conditioned arithmetic trajectory activation.
Phase B: self-generated latent tokens in a growing causal-attention context,
         jointly optimizing reasoning backbone, HybridLink and numerical readout.
Training-only teacher values are derived from the six visible numeric inputs.
No teacher vector or oracle X/Y is an inference input. The stop policy is NOT
implemented (depth is fixed). Historical DEV/TEST are exploratory only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as prior

SCHEMA = 'nanojev.hawk-dove.hybrid-contextual.v2'
DEPTHS = (0, 1, 2, 4, 8)


def stage_values(x):
    """Eight distinct teacher stages; all arithmetically derived from visible x.

    Four partial products, payoff X sum, payoff Y sum, X sign, Y sign. The
    signs are stage-specific diagnostic targets, NOT inference inputs.
    """
    p = prior.partial_products(x)
    xx, yy = p[:, 0] + p[:, 1], p[:, 2] + p[:, 3]
    return torch.stack((p[:, 0], p[:, 1], p[:, 2], p[:, 3],
                        xx, yy, xx.sign(), yy.sign()), dim=1)


class ResidualHybridLink(nn.Module):
    """Residual hidden->input-token map, no numerical output shortcut."""
    def __init__(self, dim):
        super().__init__()
        self.net = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, dim),
                                 nn.GELU(), nn.Linear(dim, dim))
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, hidden):
        return hidden + self.net(hidden)


class CausalMemoryCore(nn.Module):
    """Shared transformer repeatedly consumes all prior input/latent tokens."""
    def __init__(self, dim, heads, layers=1, max_tokens=9):
        super().__init__()
        if dim % heads:
            raise ValueError('HYBRID_V2_HEAD_DIM_NOT_DIVISIBLE')
        self.positions = nn.Parameter(torch.randn(max_tokens, dim) * .01)
        layer = nn.TransformerEncoderLayer(dim, heads, dim_feedforward=dim * 2,
                                            dropout=0., activation='gelu',
                                            batch_first=True, norm_first=True)
        self.layers = nn.TransformerEncoder(layer, num_layers=layers,
                                            enable_nested_tensor=False)
        self.out_norm = nn.LayerNorm(dim)

    def forward(self, tokens):
        size = tokens.shape[1]
        if size > self.positions.shape[0]:
            raise ValueError('HYBRID_V2_MEMORY_OVERFLOW')
        mask = torch.triu(torch.ones(size, size, dtype=torch.bool,
                                     device=tokens.device), diagonal=1)
        return self.out_norm(self.layers(tokens + self.positions[:size],
                                         mask=mask))[:, -1, :]


class ContextualGaussianHybridAgent(nn.Module):
    """Full-prefix numerical HybridAgent; original reference is untouched."""
    def __init__(self, reference, heads=4, layers=1):
        super().__init__()
        self.reference = reference
        for p in reference.parameters():
            p.requires_grad_(False)
        self.reference.eval()
        h = reference.hidden
        self.bins = reference.bins
        self.core = CausalMemoryCore(h, heads, layers)
        self.link = ResidualHybridLink(h)
        self.hidden_update = nn.Linear(h, h, bias=False)
        nn.init.zeros_(self.hidden_update.weight)
        self.stage_probe = nn.Linear(h, 1)

    def train(self, mode=True):
        super().train(mode)
        self.reference.eval()
        return self

    def _context(self, features):
        with torch.no_grad():
            first = F.silu(self.reference.encoder[0](features))
            anchor = F.silu(self.reference.encoder[2](first))
        return first.unsqueeze(1), anchor

    def teacher_tokens(self, features):
        """Target input embeddings encoded by the pretrained FIRST input layer.

        Teacher features are intermediate computations, not legitimate prompt
        instances. They are a training-only bridge into the input-embedding
        *space*, with ordered stages; they are never fed at inference.
        """
        values = stage_values(features)
        toks = []
        for step in range(8):
            synthetic = torch.zeros_like(features)
            synthetic[:, 0] = values[:, step]
            synthetic[:, 1] = (step + 1) / 8.
            synthetic[:, 6:] = features[:, 6:]
            with torch.no_grad():
                toks.append(F.silu(self.reference.encoder[0](synthetic)))
        return torch.stack(toks, dim=1)

    def teacher_rollout(self, features, depth=8):
        """Teacher-conditioned activation: generated vectors predict NEXT tokens."""
        if not 1 <= depth <= 8:
            raise ValueError('HYBRID_V2_INVALID_DEPTH')
        sequence, _ = self._context(features)
        targets = self.teacher_tokens(features)
        losses = []
        generated = []
        for step in range(depth):
            h = self.core(sequence)
            candidate = self.link(h)
            target = targets[:, step]
            losses.append(F.mse_loss(candidate, target) +
                          .02 * (1 - F.cosine_similarity(candidate, target,
                                                        dim=-1).mean()))
            generated.append(candidate)
            sequence = torch.cat((sequence, target.unsqueeze(1)), dim=1)
        return torch.stack(losses).mean(), generated

    def rollout(self, features, depth=2, intervention='normal', trace=False):
        if not 0 <= depth <= 8 or intervention not in ('normal','disabled','zero_tokens','shuffled','frozen_first'):
            raise ValueError('HYBRID_V2_INVALID_DEPTH_OR_CONTROL')
        if depth == 0 or intervention == 'disabled':
            _, anchor = self._context(features)
            return (anchor, [], []) if trace else anchor
        sequence, anchor = self._context(features)
        states, thoughts = [], []
        for step in range(depth):
            h = self.core(sequence)
            token = self.link(h)
            if intervention == 'zero_tokens':
                token = torch.zeros_like(token)
            elif intervention == 'shuffled':
                token = token.roll(1, 0)
            elif intervention == 'frozen_first' and thoughts:
                token = thoughts[0]
            thoughts.append(token)
            sequence = torch.cat((sequence, token.unsqueeze(1)), dim=1)
            # The new numerical representation depends on the accumulated
            # thought prefix, never on teacher-supplied tokens at inference.
            step_hidden = self.core(sequence)
            states.append(anchor + self.hidden_update(step_hidden))
        return (states[-1], states, thoughts) if trace else states[-1]

    def density_from_hidden(self, hidden):
        model = self.reference
        location = model.encoder[4](hidden).sigmoid()
        sigma = model.log_proposal_sigma.exp().clamp(min=.3, max=20.)
        delta = (model.centers.reshape(1, 1, -1) - location.unsqueeze(-1)) * model.bins
        logits = -.5 * (delta / sigma.reshape(1,2,1)).square() + model.bank_log_gains
        masses = F.softmax(logits, dim=-1)
        density = masses.reshape(-1,model.banks) @ model.basis
        return density.reshape(-1,2,model.bins)

    def forward(self, features, depth=0, intervention='normal'):
        if depth == 0:
            return self.reference(features)  # exact, bitwise reference
        return self.density_from_hidden(self.rollout(features, depth, intervention))


class DepthView(nn.Module):
    def __init__(self, model, depth, intervention='normal'):
        super().__init__()
        self.model = model
        self.depth = depth
        self.intervention = intervention
        self.bins = model.bins
    def forward(self, features):
        return self.model(features, self.depth, self.intervention)


def score(model, split, domain, args, depth, intervention='normal'):
    metrics = gauss.evaluate(DepthView(model,depth,intervention), split,domain,
                             args.target_sigma_bins,batch_size=args.eval_batch_size,
                             relative_tolerance=args.tolerance)
    return metrics, blind._rank(metrics)



def trajectory_diagnostic(model, split, domain, max_rows=256):
    """Read-only step traces; report whether new thoughts change answers."""
    model.eval()
    n=split['features'].shape[0]
    indices=torch.linspace(0,n-1,min(n,max_rows),device=split['features'].device).long()
    x=split['features'][indices]
    truth=split['numeric'][indices]
    with torch.no_grad():
        _,base_h=model._context(x)
        baseline=gauss.numeric_decode(model(x,0),domain)
        _, states, thoughts=model.rollout(x,8,trace=True)
        prev_h=base_h
        prev_xy=baseline
        records=[]
        for step,(hidden,thought) in enumerate(zip(states,thoughts),1):
            xy=gauss.numeric_decode(model.density_from_hidden(hidden),domain)
            err=(xy-truth).abs()
            prior_err=(prev_xy-truth).abs()
            distance=(hidden-prev_h).norm(dim=1).mean()
            records.append({'depth':step,'hidden_l2':float(distance),
                            'thought_l2':float(thought.norm(dim=1).mean()),
                            'xy_shift':float((xy-prev_xy).abs().mean()),
                            'xy_absolute_error':float(err.mean()),
                            'improved_coordinates':int((err<prior_err).sum()),
                            'worsened_coordinates':int((err>prior_err).sum())})
            prev_xy,prev_h=xy,hidden
    return {'sample_rows':len(indices),'records':records}


def _params(model):
    return list(model.core.parameters()) + list(model.link.parameters()) + list(model.hidden_update.parameters()) + list(model.stage_probe.parameters())


def _numerical_loss(density, truth, domain, bins, target_sigma_bins):
    target = gauss.normalized_target(truth,domain,bins,target_sigma_bins)
    kl = (target * (target.clamp_min(1e-12).log() - density.clamp_min(1e-12).log())).sum(-1).mean()
    mse = bins * (density-target).square().mean()
    pred = gauss.numeric_decode(density,domain)
    den = torch.tensor([d[1]-d[0] for d in domain],device=pred.device,dtype=pred.dtype)
    loc = (pred-truth)/den
    rel = torch.log1p((pred-truth).abs()/(truth.abs()*.05).clamp_min(1.)).mean()
    return kl + mse + 10*loc.square().mean() + .004*rel


def self_conditioned_loss(agent,x,y,domain,bins,sigma,depth):
    """Two passes: teacher context for alignment, THEN generated input context.

    Every self-conditioned step consumes the prior generated token with gradient
    flow through all prior steps. Numerical targets supervise all prefixes,
    with highest weight at the selected training depth (not monotone constraint).
    """
    _, states, _ = agent.rollout(x,depth,trace=True)
    num = 0.
    trace_values = stage_values(x)
    aux = 0.
    for i,state in enumerate(states):
        numerical = _numerical_loss(agent.density_from_hidden(state),y,domain,bins,sigma)
        num = num + numerical * (1. if i == depth-1 else .05/depth)
        # Distinct intermediate arithmetic values, not one static teacher.
        stage_target = trace_values[:,i]
        aux = aux + F.smooth_l1_loss(agent.stage_probe(state).squeeze(1),stage_target)/depth
    teacher_loss,_ = agent.teacher_rollout(x,depth)
    return num + .03*aux + .005*teacher_loss, (num,aux,teacher_loss)


def _identity(args,source_id,manifest,source_sha):
    return {'schema':SCHEMA, 'source_sha256':source_sha,'source_identity':source_id,
            'matrix_hashes':manifest['matrix_hashes'],
            'warmup_steps':args.warmup_steps,'steps':args.steps,
            'probe_every':args.probe_every,'batch_size':args.batch_size,
            'warmup_lr':args.warmup_lr,'lr':args.lr,'seed':args.seed,
            'heads':args.heads,'layers':args.layers,'tolerance':args.tolerance,
            'architecture':'causal_prefix_residual_hybridlink_joint_core_and_readout',
            'teacher':'ordered_four_products_two_sums_two_signs_training_only',
            'holdout':'exploratory_previously_seen_test'}


def _save(path,agent,wopt,sopt,rng,ws,ss,best_rank,best_step,best_depth,history,identity):
    gauss._atomic_save(path,{'schema':SCHEMA+'.progress','identity':identity,
        'model':copy.deepcopy({k:v for k,v in agent.state_dict().items() if not k.startswith('reference.')}),
        'warm_optimizer':wopt.state_dict(),'self_optimizer':sopt.state_dict(),
        'sampler_rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'warm_step':ws,'self_step':ss,'best_rank':best_rank,'best_step':best_step,
        'best_depth':best_depth,'history':history})


def _load_agent(agent,weights):
    missing,unexpected=agent.load_state_dict(weights,strict=False)
    if unexpected or set(missing)!={k for k in agent.state_dict() if k.startswith('reference.')}:
        raise RuntimeError('HYBRID_V2_CHECKPOINT_LAYOUT_DRIFT')


def _restore(state,agent,wopt,sopt,rng):
    _load_agent(agent,state['model'])
    wopt.load_state_dict(state['warm_optimizer'])
    sopt.load_state_dict(state['self_optimizer'])
    rng.set_state(state['sampler_rng'])
    torch.set_rng_state(state['torch_rng'])
    if state['cuda_rng']:
        if not torch.cuda.is_available() or len(state['cuda_rng'])!=torch.cuda.device_count():
            raise RuntimeError('HYBRID_V2_CUDA_RNG_TOPOLOGY_DRIFT')
        torch.cuda.set_rng_state_all(state['cuda_rng'])


def _validate(args):
    if any(v<1 for v in (args.warmup_steps,args.steps,args.batch_size,args.eval_batch_size,
                          args.probe_every,args.heads,args.layers,args.cpu_threads)):
        raise ValueError('HYBRID_V2_INVALID_TRAINING_DIMENSIONS')
    if not (0<args.warmup_lr<=1 and 0<args.lr<=1 and 0<args.tolerance<1):
        raise ValueError('HYBRID_V2_INVALID_OPTIMIZATION')
    if args.stop_after is not None and not 1<=args.stop_after<=args.steps:
        raise ValueError('HYBRID_V2_INVALID_PAUSE_BOUNDARY')
    if args.warmup_stop_after is not None and not 1<=args.warmup_stop_after<=args.warmup_steps:
        raise ValueError('HYBRID_V2_INVALID_PAUSE_BOUNDARY')
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('HYBRID_V2_CUDA_UNAVAILABLE')


def train(args):
    _validate(args)
    torch.set_num_threads(args.cpu_threads)
    device=torch.device(args.device)
    source=args.reference_checkpoint.resolve()
    sha=prior.sha256_file(source)
    checkpoint=torch.load(source,map_location='cpu',weights_only=True)
    if checkpoint.get('schema')!=blind.SCHEMA:
        raise RuntimeError('HYBRID_V2_SOURCE_SCHEMA_DRIFT')
    ci=checkpoint['identity']
    args.target_sigma_bins=ci['target_sigma_bins']
    original=blind.BlindDensityNetwork(ci['banks'],ci['grid_bins'],ci['seed'],ci['hidden'])
    original.load_state_dict(checkpoint['model'],strict=True)
    splits,domain,manifest=blind._dataset(argparse.Namespace(**ci),device)
    if ci['matrix_hashes']!=manifest['matrix_hashes'] or ci['domain']!=[list(d) for d in domain]:
        raise RuntimeError('HYBRID_V2_MATRIX_OR_DOMAIN_DRIFT')
    original=original.to(device)
    identity=_identity(args,ci,manifest,sha)
    output=args.output.expanduser().resolve()
    if ci['hidden'] % args.heads:
        raise ValueError('HYBRID_V2_HEAD_DIM_NOT_DIVISIBLE')
    if args.resume:
        if (output/'report.json').exists():
            raise RuntimeError('HYBRID_V2_FINALIZED_CANNOT_RESUME')
        state=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA+'.progress' or state.get('identity')!=identity:
            raise RuntimeError('HYBRID_V2_RESUME_IDENTITY_DRIFT')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('HYBRID_V2_REFUSES_OVERWRITE')
    else:
        output.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(output/'config.json',identity)
        gauss._atomic_json(output/'matrix_split_manifest.json',manifest)
    torch.manual_seed(args.seed)
    agent=ContextualGaussianHybridAgent(original,args.heads,args.layers).to(device)
    params=_params(agent)
    agent.eval()
    with torch.no_grad():
        initial_activation_loss=float(agent.teacher_rollout(splits['dev']['features'])[0])
    warm_opt=torch.optim.AdamW(params,lr=args.warmup_lr,weight_decay=0.)
    self_opt=torch.optim.AdamW(params,lr=args.lr,weight_decay=0.)
    rng=torch.Generator(device='cpu').manual_seed(args.seed+91)
    ws=ss=0;best_rank=best_step=best_depth=None;history=[]
    if args.resume:
        _restore(state,agent,warm_opt,self_opt,rng)
        ws,ss=state['warm_step'],state['self_step']
        best_rank,best_step,best_depth=state['best_rank'],state['best_step'],state['best_depth']
        history=state['history']
    agent.eval()
    with torch.no_grad():
        subset=splits['dev']['features'][:64]
        if not torch.equal(original(subset),agent(subset,0)):
            raise RuntimeError('HYBRID_V2_ZERO_STEP_PARITY_FAILED')
    print('HYBRID_V2_ZERO_STEP_PARITY=PASS source_frozen=TRUE',flush=True)
    baseline,base_rank=score(agent,splits['dev'],domain,args,0)
    print(f'HYBRID_V2_REFERENCE_DEV={base_rank[0]:.6f}',flush=True)
    if not args.resume:
        gauss._atomic_json(output/'baseline.json',{'rank':base_rank,'dev':baseline,'source_sha256':sha})

    if ws<args.warmup_steps:
        limit=min(args.warmup_steps,args.warmup_stop_after or args.warmup_steps)
        for step in range(ws+1,limit+1):
            agent.train()
            ids=prior.balanced_ids(splits['train'],args.batch_size,rng,device)
            x=splits['train']['features'][ids]
            warm_opt.zero_grad(set_to_none=True)
            loss,_=agent.teacher_rollout(x)
            if not torch.isfinite(loss):raise FloatingPointError('HYBRID_V2_ACTIVATION_NONFINITE')
            loss.backward()
            nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
            warm_opt.step();ws=step
            if step%args.probe_every==0 or step==args.warmup_steps:
                agent.eval()
                with torch.no_grad():
                    value=float(agent.teacher_rollout(splits['dev']['features'])[0])
                history.append({'phase':'activation','step':step,'dev_alignment_loss':value})
                print(f'HYBRID_V2_ACTIVATION step={step}/{args.warmup_steps} DEV_alignment_loss={value:.7g}',flush=True)
                _save(output/'latest.pt',agent,warm_opt,self_opt,rng,ws,ss,best_rank,best_step,best_depth,history,identity)
        if ws<args.warmup_steps:
            _save(output/'latest.pt',agent,warm_opt,self_opt,rng,ws,ss,best_rank,best_step,best_depth,history,identity)
            print(f'HYBRID_V2_PAUSED_ACTIVATION={ws}',flush=True)
            return {'status':'PAUSED_ACTIVATION','warm_step':ws}
        agent.eval()
        with torch.no_grad():
            post=float(agent.teacher_rollout(splits['dev']['features'])[0])
        first=initial_activation_loss
        gate=math.isfinite(post) and post<first
        gauss._atomic_json(output/'activation.json',{'status':'PASS' if gate else 'FAIL',
             'initial_or_first_probe':first,'final':post,
             'teacher_only_in_training':True,'trainable_core':True,'residual_link':True})
        print(f'HYBRID_V2_ACTIVATION_GATE={"PASS" if gate else "FAIL"} final={post:.7g}',flush=True)
        if not gate:raise RuntimeError('HYBRID_V2_ACTIVATION_GATE_FAILED')
    else:
        if json.loads((output/'activation.json').read_text())['status']!='PASS':
            raise RuntimeError('HYBRID_V2_ACTIVATION_EVIDENCE_INVALID')
    print('HYBRID_V2_SELF_CONDITIONING=ACTIVE CONTEXT=ACCUMULATED CORE_TRAINABLE=TRUE TEACHER_AT_INFERENCE=FALSE',flush=True)
    limit=min(args.steps,args.stop_after or args.steps)
    for step in range(ss+1,limit+1):
        depth=prior.phase_depth(step,args.steps)
        agent.train()
        ids=prior.balanced_ids(splits['train'],args.batch_size,rng,device)
        x=splits['train']['features'][ids]
        y=splits['train']['numeric'][ids]
        self_opt.zero_grad(set_to_none=True)
        loss,parts=self_conditioned_loss(agent,x,y,domain,ci['grid_bins'],ci['target_sigma_bins'],depth)
        if not torch.isfinite(loss):raise FloatingPointError('HYBRID_V2_SELF_NONFINITE')
        loss.backward()
        grad=nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        if not torch.isfinite(grad):raise FloatingPointError('HYBRID_V2_GRADIENT_NONFINITE')
        self_opt.step();ss=step
        if step%args.probe_every==0 or step==args.steps:
            agent.eval()
            with torch.no_grad():
                scores={}
                for d in DEPTHS[1:]:
                    metrics,rank=score(agent,splits['dev'],domain,args,d)
                    scores[str(d)]=rank[0]
                    if best_rank is None or rank>tuple(best_rank):
                        best_rank=rank;best_step=step;best_depth=d
                        gauss._atomic_save(output/'best.pt',{'schema':SCHEMA+'.best',
                            'identity':identity,'model':copy.deepcopy({k:v for k,v in agent.state_dict().items() if not k.startswith('reference.')}),
                            'step':step,'depth':d,'dev_rank':rank})
            history.append({'phase':'self-conditioned','step':step,'curriculum_depth':depth,
                            'loss':float(loss.detach()),'dev_by_depth':scores,
                            'best_step':best_step,'best_depth':best_depth})
            print(f'HYBRID_V2_SELF step={step}/{args.steps} train_depth={depth} DEV={json.dumps(scores,separators=(",",":"))} best={best_step}@{best_depth} loss={float(loss.detach()):.6g}',flush=True)
            gauss._atomic_json(output/'history.json',{'schema':SCHEMA,'records':history})
            _save(output/'latest.pt',agent,warm_opt,self_opt,rng,ws,ss,best_rank,best_step,best_depth,history,identity)
    if ss<args.steps:
        _save(output/'latest.pt',agent,warm_opt,self_opt,rng,ws,ss,best_rank,best_step,best_depth,history,identity)
        print(f'HYBRID_V2_PAUSED_SELF={ss}/{args.steps}',flush=True)
        return {'status':'PAUSED_SELF','self_step':ss}
    best=torch.load(output/'best.pt',map_location='cpu',weights_only=True)
    if best.get('schema')!=SCHEMA+'.best' or best.get('identity')!=identity or best.get('step')!=best_step:
        raise RuntimeError('HYBRID_V2_BEST_DRIFT')
    _load_agent(agent,best['model'])
    agent.eval()
    with torch.no_grad():
        dev,devrank=score(agent,splits['dev'],domain,args,best_depth)
        test,testrank=score(agent,splits['test'],domain,args,best_depth)
        base_test,base_test_rank=score(agent,splits['test'],domain,args,0)
        disabled,disabled_rank=score(agent,splits['dev'],domain,args,best_depth,'disabled')
        shuffled,shuffled_rank=score(agent,splits['dev'],domain,args,best_depth,'shuffled')
        frozen,frozen_rank=score(agent,splits['dev'],domain,args,best_depth,'frozen_first')
        zeroed,zeroed_rank=score(agent,splits['dev'],domain,args,best_depth,'zero_tokens')
        evolution=trajectory_diagnostic(agent,splits['dev'],domain)
    report={'schema':SCHEMA+'.report','identity':identity,
            'baseline_DEV_min_joint5':base_rank[0],'baseline_TEST_min_joint5':base_test_rank[0],
            'DEV_hybrid':devrank[0],'TEST_hybrid':testrank[0],
            'DEV_disabled':disabled_rank[0],'DEV_shuffled':shuffled_rank[0],
            'DEV_frozen_first':frozen_rank[0], 'DEV_zero_tokens':zeroed_rank[0],
            'best_step':best_step,'best_depth':best_depth,'trained_steps':ss,
            'status':'LATENT_GAIN_OBSERVED' if devrank[0]>max(base_rank[0],disabled_rank[0],shuffled_rank[0],frozen_rank[0],zeroed_rank[0]) else 'NO_CAUSAL_LATENT_GAIN_ESTABLISHED',
            'DEV':dev,'TEST':test,'step_evolution':evolution,
            'holdout_caveat':'EXPLORATORY previously examined TEST; not blind certification'}
    gauss._atomic_json(output/'report.json',report)
    print(json.dumps({k:report[k] for k in ('status','best_step','best_depth','DEV_hybrid','TEST_hybrid','DEV_disabled','DEV_shuffled','DEV_frozen_first','DEV_zero_tokens')},indent=2),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--warmup-stop-after',type=int,default=None)
    p.add_argument('--warmup-steps',type=int,default=2000)
    p.add_argument('--steps',type=int,default=12000)
    p.add_argument('--probe-every',type=int,default=500)
    p.add_argument('--batch-size',type=int,default=64)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--warmup-lr',type=float,default=.0005)
    p.add_argument('--lr',type=float,default=.0002)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    return train(parser().parse_args(argv))

if __name__=='__main__':
    main()
