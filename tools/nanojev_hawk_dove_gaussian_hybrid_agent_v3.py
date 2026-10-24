#!/usr/bin/env python3
"""Numerical HybridAgent v3: typed teacher tokens, frozen link activation, two-pass HybridSFT.

Separate research run. The original Gaussian is an exact frozen zero-step control.
Phase 0: bootstrap a typed four-partial-product teacher and prove utility.
Phase 1: freeze reasoning core/readout, activate residual HybridLink.
Phase 2: teacher-conditioned first-pass token generation, second-pass conditioning
         on the model's own tokens, and parallel fully autonomous rollout;
         jointly train the reasoning core, HybridLink and independent readout.
Training-only teacher values are derived from the six visible numeric inputs.
No teacher vector or oracle X/Y is an inference input. The stop policy is NOT
implemented (depth is fixed). Historical DEV/TEST are exploratory only.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as prior
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v2 as v2

SCHEMA = 'nanojev.hawk-dove.hybrid-typed-selfsft.v3'
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


class TypedThoughtEncoder(nn.Module):
    """A typed operation/value vocabulary, never masquerading as 14 raw facts.

    The eight roles are PRODUCT_HH, PRODUCT_HD, PRODUCT_DH, PRODUCT_DD,
    SUM_X, SUM_Y, SIGN_X, SIGN_Y. Only the first four are used for the
    teacher-information gate, so it cannot pass by providing final answers.
    """
    def __init__(self, dim):
        super().__init__()
        self.role = nn.Embedding(8, dim)
        self.value = nn.Sequential(nn.Linear(3,dim), nn.Tanh(), nn.Linear(dim,dim))
        self.norm = nn.LayerNorm(dim)

    def forward(self, x):
        values = stage_values(x)
        roles = self.role(torch.arange(8, device=x.device)).unsqueeze(0)
        stats = torch.stack((values, torch.sign(values),
                             torch.log1p(values.abs())), dim=-1)
        return self.norm(roles + self.value(stats))


class ContextualGaussianHybridAgent(nn.Module):
    """Independent readout from causal thought memory; no reference anchor bypass."""
    def __init__(self, reference, heads=4, layers=1):
        super().__init__()
        self.reference = reference
        for p in reference.parameters():
            p.requires_grad_(False)
        self.reference.eval()
        h = reference.hidden
        self.bins = reference.bins
        self.core = CausalMemoryCore(h, heads, layers, max_tokens=12)
        self.link = ResidualHybridLink(h)
        self.typed = TypedThoughtEncoder(h)
        self.answer = nn.Linear(h, 2)

    def train(self, mode=True):
        super().train(mode)
        self.reference.eval()
        return self

    def _context(self, features):
        with torch.no_grad():
            first = F.silu(self.reference.encoder[0](features))
            anchor = F.silu(self.reference.encoder[2](first))
        return torch.stack((first,anchor), dim=1), anchor

    def teacher_tokens(self, features):
        return self.typed(features)

    def teacher_prefix(self, features, depth=4, intervention='normal'):
        seq, _ = self._context(features)
        toks = self.teacher_tokens(features)[:, :depth]
        if intervention == 'shuffled':
            toks = toks.roll(1, 0)
        elif intervention == 'zero_tokens':
            toks = torch.zeros_like(toks)
        elif intervention != 'normal':
            raise ValueError('HYBRID_V3_BAD_TEACHER_INTERVENTION')
        return torch.cat((seq,toks),dim=1)

    def link_activation(self, features, depth=8):
        """Frozen-core, frozen-target teacher-forced link training."""
        if not 1 <= depth <= 8:
            raise ValueError('HYBRID_V3_INVALID_ACTIVATION_DEPTH')
        seq, _ = self._context(features)
        teacher = self.teacher_tokens(features).detach()
        losses=[]
        for step in range(depth):
            candidate = self.link(self.core(seq))
            target = teacher[:,step]
            losses.append(F.mse_loss(candidate,target) +
                          .02*(1-F.cosine_similarity(candidate,target,dim=-1).mean()))
            seq = torch.cat((seq,target.unsqueeze(1)),dim=1)
        return torch.stack(losses).mean()

    def first_pass(self, features, depth):
        """Pass A: generate latent predictions conditioned on typed teacher prefix."""
        seq, _ = self._context(features)
        teacher=self.teacher_tokens(features)
        predictions=[]
        for step in range(depth):
            predictions.append(self.link(self.core(seq)))
            seq=torch.cat((seq,teacher[:,step:step+1]),dim=1)
        return torch.stack(predictions,dim=1)

    def rollout(self, features, depth=2, intervention='normal', trace=False,
                supplied_tokens=None):
        if not 0<=depth<=8 or intervention not in ('normal','disabled','zero_tokens',
                    'shuffled','frozen_first'):
            raise ValueError('HYBRID_V3_INVALID_DEPTH_OR_CONTROL')
        seq,anchor=self._context(features)
        if depth==0 or intervention=='disabled':
            return (anchor,[],[]) if trace else anchor
        states=[];thoughts=[]
        for step in range(depth):
            token = (self.link(self.core(seq)) if supplied_tokens is None
                     else supplied_tokens[:,step])
            if intervention == 'zero_tokens':
                token=torch.zeros_like(token)
            elif intervention == 'shuffled':
                token=token.roll(1,0)
            elif intervention=='frozen_first' and thoughts:
                token=thoughts[0]
            thoughts.append(token)
            seq=torch.cat((seq,token.unsqueeze(1)),dim=1)
            states.append(self.core(seq))
        return (states[-1],states,thoughts) if trace else states[-1]

    def density_from_hidden(self, hidden):
        model=self.reference
        location = self.answer(hidden).sigmoid()
        sigma = model.log_proposal_sigma.exp().clamp(min=.3, max=20.)
        delta = (model.centers.reshape(1,1,-1) - location.unsqueeze(-1)) * model.bins
        logits = -.5*(delta/sigma.reshape(1,2,1)).square() + model.bank_log_gains
        masses=F.softmax(logits,dim=-1)
        density=masses.reshape(-1,model.banks)@model.basis
        return density.reshape(-1,2,model.bins)

    def forward(self, features, depth=0, intervention='normal', supplied_tokens=None):
        if depth==0 or intervention=='disabled':
            return self.reference(features)
        hidden=self.rollout(features,depth,intervention,supplied_tokens=supplied_tokens)
        return self.density_from_hidden(hidden)

    def teacher_answer(self,features,depth=4,intervention='normal'):
        return self.density_from_hidden(self.core(self.teacher_prefix(features,depth,intervention)))


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


def _params(model, mode):
    groups={'bootstrap':[model.core,model.typed,model.answer],
            'activation':[model.link],
            'joint':[model.core,model.link,model.answer]}
    return [p for m in groups[mode] for p in m.parameters()]


def configure_trainable(model,mode):
    for p in model.parameters():
        p.requires_grad_(False)
    for p in _params(model,mode):
        p.requires_grad_(True)
    model.reference.eval()


def _numerical_loss(density, truth, domain, bins, target_sigma_bins):
    return v2._numerical_loss(density,truth,domain,bins,target_sigma_bins)


def self_conditioned_loss(agent,x,y,domain,bins,sigma,depth):
    """HybridSFT-style Pass A (teacher-prefix generations), Pass B (their outputs).

    A separate free-running rollout is included to train the same inference
    distribution. No typed/teacher embeddings reach the inference API.
    """
    teacher=agent.teacher_tokens(x).detach()
    predicted=agent.first_pass(x,depth)
    align=F.mse_loss(predicted,teacher[:,:depth]) + .02*(1-F.cosine_similarity(
        predicted,teacher[:,:depth],dim=-1).mean())
    # The conditioning inputs are model-generated, NOT ideal teacher tokens.
    # Detach them here: gradients to HybridLink flow through alignment and
    # the independent free-running rollout, avoiding a teacher-answer bypass.
    second=agent(x,depth,supplied_tokens=predicted.detach())
    num_second=_numerical_loss(second,y,domain,bins,sigma)
    free=agent(x,depth)
    num_free=_numerical_loss(free,y,domain,bins,sigma)
    return num_second + num_free + .02*align, (num_second,num_free,align)


def _identity(args,source_id,manifest,sha):
    return {'schema':SCHEMA,'source_sha256':sha,'source_identity':source_id,
            'matrix_hashes':manifest['matrix_hashes'],
            'bootstrap_steps':args.bootstrap_steps,'warmup_steps':args.warmup_steps,
            'steps':args.steps,'probe_every':args.probe_every,'batch_size':args.batch_size,
            'bootstrap_lr':args.bootstrap_lr,'warmup_lr':args.warmup_lr,'lr':args.lr,
            'seed':args.seed,'heads':args.heads,'layers':args.layers,
            'tolerance':args.tolerance,'min_teacher_gain':args.min_teacher_gain,
            'architecture':'typed_four_products_then_causal_memory_independent_answer',
            'training':'bootstrap_frozen_link_activation_two_pass_selfsft_and_free_run',
            'holdout':'EXPLORATORY_previously_examined_TEST'}


def _weights(agent):
    return copy.deepcopy({k:v for k,v in agent.state_dict().items()
                          if not k.startswith('reference.')})


def _save(path,agent,bopt,wopt,sopt,rng,bs,ws,ss,best_rank,best_step,
          best_depth,history,identity):
    gauss._atomic_save(path,{'schema':SCHEMA+'.progress','identity':identity,
        'model':_weights(agent),'bootstrap_optimizer':bopt.state_dict(),
        'warm_optimizer':wopt.state_dict(),'self_optimizer':sopt.state_dict(),
        'sampler_rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'bootstrap_step':bs,'warm_step':ws,'self_step':ss,'best_rank':best_rank,
        'best_step':best_step,'best_depth':best_depth,'history':history})


def _load_agent(agent,weights):
    missing,unexpected=agent.load_state_dict(weights,strict=False)
    if unexpected or set(missing)!={k for k in agent.state_dict() if k.startswith('reference.')}:
        raise RuntimeError('HYBRID_V3_CHECKPOINT_LAYOUT_DRIFT')


def _restore(state,agent,bopt,wopt,sopt,rng):
    _load_agent(agent,state['model'])
    bopt.load_state_dict(state['bootstrap_optimizer'])
    wopt.load_state_dict(state['warm_optimizer'])
    sopt.load_state_dict(state['self_optimizer'])
    rng.set_state(state['sampler_rng'])
    torch.set_rng_state(state['torch_rng'])
    if state['cuda_rng']:
        if not torch.cuda.is_available() or len(state['cuda_rng'])!=torch.cuda.device_count():
            raise RuntimeError('HYBRID_V3_CUDA_RNG_TOPOLOGY_DRIFT')
        torch.cuda.set_rng_state_all(state['cuda_rng'])


def teacher_score(agent,split,domain,args,intervention='normal'):
    class TeacherView(nn.Module):
        def __init__(self):
            super().__init__()
            self.bins=agent.bins
        def forward(self,features):
            return agent.teacher_answer(features,4,intervention)
    return gauss.evaluate(TeacherView(),split,domain,args.target_sigma_bins,
        batch_size=args.eval_batch_size,relative_tolerance=args.tolerance)


def teacher_diagnostic(agent,split,domain,args):
    agent.eval()
    with torch.no_grad():
        good=teacher_score(agent,split,domain,args)
        wrong=teacher_score(agent,split,domain,args,'shuffled')
        zero=teacher_score(agent,split,domain,args,'zero_tokens')
        source=gauss.evaluate(agent.reference,split,domain,args.target_sigma_bins,
            batch_size=args.eval_batch_size,relative_tolerance=args.tolerance)
        def normalized_mae(metrics):
            # Use continuous numerical error for the teaching gate: tiny CPU
            # fixtures can have ZERO strict hits despite genuine improvement.
            return (metrics['overall']['x_mae']+metrics['overall']['y_mae']) / (
                2*(domain[0][1]-domain[0][0]))
        return {'teacher_dev_min_joint5':blind._rank(good)[0],
                'shuffled_dev_min_joint5':blind._rank(wrong)[0],
                'zero_dev_min_joint5':blind._rank(zero)[0],
                'baseline_normalized_mae':normalized_mae(source),
                'teacher_normalized_mae':normalized_mae(good),
                'shuffled_normalized_mae':normalized_mae(wrong),
                'zero_normalized_mae':normalized_mae(zero)}


def _validate(args):
    if any(v<1 for v in (args.bootstrap_steps,args.warmup_steps,args.steps,
            args.batch_size,args.eval_batch_size,args.probe_every,args.heads,
            args.layers,args.cpu_threads)):
        raise ValueError('HYBRID_V3_INVALID_TRAINING_DIMENSIONS')
    if not all(0<v<=1 for v in (args.bootstrap_lr,args.warmup_lr,args.lr,args.tolerance)):
        raise ValueError('HYBRID_V3_INVALID_OPTIMIZATION')
    if not -1<=args.min_teacher_gain<=1:
        raise ValueError('HYBRID_V3_INVALID_TEACHER_GATE')
    for step,total in ((args.bootstrap_stop_after,args.bootstrap_steps),
                       (args.warmup_stop_after,args.warmup_steps),
                       (args.stop_after,args.steps)):
        if step is not None and not 1<=step<=total:
            raise ValueError('HYBRID_V3_INVALID_PAUSE_BOUNDARY')
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('HYBRID_V3_CUDA_UNAVAILABLE')


def train(args):
    _validate(args)
    torch.set_num_threads(args.cpu_threads)
    device=torch.device(args.device)
    source=args.reference_checkpoint.expanduser().resolve()
    sha=prior.sha256_file(source)
    checkpoint=torch.load(source,map_location='cpu',weights_only=True)
    if checkpoint.get('schema')!=blind.SCHEMA:
        raise RuntimeError('HYBRID_V3_SOURCE_SCHEMA_DRIFT')
    ci=checkpoint['identity']
    args.target_sigma_bins=ci['target_sigma_bins']
    reference=blind.BlindDensityNetwork(ci['banks'],ci['grid_bins'],ci['seed'],ci['hidden'])
    reference.load_state_dict(checkpoint['model'],strict=True)
    splits,domain,manifest=blind._dataset(argparse.Namespace(**ci),device)
    if ci['matrix_hashes']!=manifest['matrix_hashes'] or ci['domain']!=[list(d) for d in domain]:
        raise RuntimeError('HYBRID_V3_MATRIX_OR_DOMAIN_DRIFT')
    reference=reference.to(device)
    identity=_identity(args,ci,manifest,sha)
    output=args.output.expanduser().resolve()
    if ci['hidden']%args.heads:
        raise ValueError('HYBRID_V3_HEAD_DIM_NOT_DIVISIBLE')
    if args.resume:
        if (output/'report.json').exists():
            raise RuntimeError('HYBRID_V3_FINALIZED_CANNOT_RESUME')
        state=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA+'.progress' or state.get('identity')!=identity:
            raise RuntimeError('HYBRID_V3_RESUME_IDENTITY_DRIFT')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('HYBRID_V3_REFUSES_OVERWRITE')
    else:
        output.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(output/'config.json',identity)
        gauss._atomic_json(output/'matrix_split_manifest.json',manifest)

    torch.manual_seed(args.seed)
    agent=ContextualGaussianHybridAgent(reference,args.heads,args.layers).to(device)
    bparams=_params(agent,'bootstrap');wparams=_params(agent,'activation');sparams=_params(agent,'joint')
    bopt=torch.optim.AdamW(bparams,lr=args.bootstrap_lr,weight_decay=0.)
    wopt=torch.optim.AdamW(wparams,lr=args.warmup_lr,weight_decay=0.)
    sopt=torch.optim.AdamW(sparams,lr=args.lr,weight_decay=0.)
    rng=torch.Generator(device='cpu').manual_seed(args.seed+91)
    bs=ws=ss=0;best_rank=best_step=best_depth=None;history=[]
    if args.resume:
        _restore(state,agent,bopt,wopt,sopt,rng)
        bs,ws,ss=state['bootstrap_step'],state['warm_step'],state['self_step']
        best_rank,best_step,best_depth=state['best_rank'],state['best_step'],state['best_depth']
        history=state['history']
    agent.eval()
    with torch.no_grad():
        x0=splits['dev']['features'][:64]
        if not torch.equal(reference(x0),agent(x0,0)):
            raise RuntimeError('HYBRID_V3_ZERO_STEP_PARITY_FAILED')
    print('HYBRID_V3_ZERO_STEP_PARITY=PASS source_frozen=TRUE',flush=True)
    _,baseline_rank=score(agent,splits['dev'],domain,args,0)
    print(f'HYBRID_V3_REFERENCE_DEV={baseline_rank[0]:.6f}',flush=True)

    if bs<args.bootstrap_steps:
        configure_trainable(agent,'bootstrap')
        limit=min(args.bootstrap_steps,args.bootstrap_stop_after or args.bootstrap_steps)
        for step in range(bs+1,limit+1):
            agent.train()
            ids=prior.balanced_ids(splits['train'],args.batch_size,rng,device)
            x=splits['train']['features'][ids];y=splits['train']['numeric'][ids]
            bopt.zero_grad(set_to_none=True)
            # Train all eight typed roles; validate useful *partial-product*
            # thought prefixes separately at depth four (no final sums given).
            loss=(.7*_numerical_loss(agent.teacher_answer(x,4),y,domain,
                                      ci['grid_bins'],ci['target_sigma_bins']) +
                  .3*_numerical_loss(agent.teacher_answer(x,8),y,domain,
                                      ci['grid_bins'],ci['target_sigma_bins']))
            if not torch.isfinite(loss):raise FloatingPointError('HYBRID_V3_BOOTSTRAP_NONFINITE')
            loss.backward()
            nn.utils.clip_grad_norm_(bparams,1.,error_if_nonfinite=True)
            bopt.step();bs=step
            if step%args.probe_every==0 or step==args.bootstrap_steps:
                diag=teacher_diagnostic(agent,splits['dev'],domain,args)
                history.append({'phase':'teacher-bootstrap','step':step,'loss':float(loss.detach()),**diag})
                print(f'HYBRID_V3_TEACHER step={step}/{args.bootstrap_steps} '
                      f'DEV_correct={diag["teacher_dev_min_joint5"]:.4f} '
                      f'DEV_shuffled={diag["shuffled_dev_min_joint5"]:.4f}',flush=True)
                _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,
                    best_rank,best_step,best_depth,history,identity)
        if bs<args.bootstrap_steps:
            _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,best_rank,best_step,best_depth,history,identity)
            return {'status':'PAUSED_BOOTSTRAP','bootstrap_step':bs}
    diag=teacher_diagnostic(agent,splits['dev'],domain,args)
    # Require improvement over BOTH the source and corrupted thought sequence,
    # measured on continuously varying numerical error rather than hit counts.
    prior_mae=min(diag['baseline_normalized_mae'],diag['shuffled_normalized_mae'])
    gain=(prior_mae-diag['teacher_normalized_mae']) / max(prior_mae,1e-12)
    gate=gain>args.min_teacher_gain
    evidence={'status':'PASS' if gate else 'FAIL','relative_mae_gain_over_both_controls':gain,
        'required_relative_mae_gain':args.min_teacher_gain,**diag}
    gauss._atomic_json(output/'teacher_gate.json',evidence)
    print(f'HYBRID_V3_TEACHER_GATE={evidence["status"]} '
          f'gain={gain:.6f} required>{args.min_teacher_gain:.6f}',flush=True)
    if not gate:
        raise RuntimeError('HYBRID_V3_TEACHER_INFORMATIVITY_GATE_FAILED')

    if ws<args.warmup_steps:
        configure_trainable(agent,'activation')
        with torch.no_grad():
            initial=float(agent.link_activation(splits['dev']['features']))
        limit=min(args.warmup_steps,args.warmup_stop_after or args.warmup_steps)
        for step in range(ws+1,limit+1):
            agent.train()
            ids=prior.balanced_ids(splits['train'],args.batch_size,rng,device)
            x=splits['train']['features'][ids]
            wopt.zero_grad(set_to_none=True)
            loss=agent.link_activation(x)
            if not torch.isfinite(loss):raise FloatingPointError('HYBRID_V3_ACTIVATION_NONFINITE')
            loss.backward()
            nn.utils.clip_grad_norm_(wparams,1.,error_if_nonfinite=True)
            wopt.step();ws=step
            if step%args.probe_every==0 or step==args.warmup_steps:
                agent.eval()
                with torch.no_grad():
                    value=float(agent.link_activation(splits['dev']['features']))
                history.append({'phase':'link-activation','step':step,'dev_alignment_loss':value})
                print(f'HYBRID_V3_LINK step={step}/{args.warmup_steps} '
                      f'DEV_alignment={value:.7g}',flush=True)
                _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,
                      best_rank,best_step,best_depth,history,identity)
        if ws<args.warmup_steps:
            _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,
                  best_rank,best_step,best_depth,history,identity)
            return {'status':'PAUSED_ACTIVATION','warm_step':ws}
        with torch.no_grad():
            final=float(agent.link_activation(splits['dev']['features']))
        gate=math.isfinite(final) and final<initial
        gauss._atomic_json(output/'activation.json',{'status':'PASS' if gate else 'FAIL',
                  'first_dev_alignment_loss':initial,'final':final,
                  'core_frozen':True,'typed_target_frozen':True,'answer_frozen':True})
        print(f'HYBRID_V3_ACTIVATION_GATE={"PASS" if gate else "FAIL"} '
              f'initial={initial:.7g} final={final:.7g}',flush=True)
        if not gate:raise RuntimeError('HYBRID_V3_ACTIVATION_GATE_FAILED')
    elif json.loads((output/'activation.json').read_text())['status']!='PASS':
        raise RuntimeError('HYBRID_V3_ACTIVATION_EVIDENCE_INVALID')

    configure_trainable(agent,'joint')
    print('HYBRID_V3_TWO_PASS_SELF_CONDITIONING=ACTIVE '
          'TYPED_TEACHER_ONLY_TRAINING=TRUE CORE_AND_LINK_TRAINABLE=TRUE',flush=True)
    limit=min(args.steps,args.stop_after or args.steps)
    for step in range(ss+1,limit+1):
        depth=prior.phase_depth(step,args.steps)
        agent.train()
        ids=prior.balanced_ids(splits['train'],args.batch_size,rng,device)
        x=splits['train']['features'][ids];y=splits['train']['numeric'][ids]
        sopt.zero_grad(set_to_none=True)
        loss,parts=self_conditioned_loss(agent,x,y,domain,ci['grid_bins'],ci['target_sigma_bins'],depth)
        if not torch.isfinite(loss):raise FloatingPointError('HYBRID_V3_SELF_NONFINITE')
        loss.backward()
        nn.utils.clip_grad_norm_(sparams,1.,error_if_nonfinite=True)
        sopt.step();ss=step
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
                          'identity':identity,'model':_weights(agent),'step':step,
                          'depth':d,'dev_rank':rank})
            history.append({'phase':'two-pass-self-conditioned','step':step,'depth':depth,
                 'loss':float(loss.detach()),'dev_by_depth':scores,'best_step':best_step,
                 'best_depth':best_depth})
            print(f'HYBRID_V3_SELF step={step}/{args.steps} depth={depth} '
                 f'DEV={json.dumps(scores,separators=(",",":"))} best={best_step}@{best_depth}',flush=True)
            gauss._atomic_json(output/'history.json',{'schema':SCHEMA,'records':history})
            _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,
                  best_rank,best_step,best_depth,history,identity)
    if ss<args.steps:
        _save(output/'latest.pt',agent,bopt,wopt,sopt,rng,bs,ws,ss,
              best_rank,best_step,best_depth,history,identity)
        return {'status':'PAUSED_SELF','self_step':ss}
    best=torch.load(output/'best.pt',map_location='cpu',weights_only=True)
    if best.get('schema')!=SCHEMA+'.best' or best.get('identity')!=identity or best.get('step')!=best_step:
        raise RuntimeError('HYBRID_V3_BEST_DRIFT')
    _load_agent(agent,best['model'])
    agent.eval()
    with torch.no_grad():
        dev,devrank=score(agent,splits['dev'],domain,args,best_depth)
        test,testrank=score(agent,splits['test'],domain,args,best_depth)
        _,base_test=score(agent,splits['test'],domain,args,0)
        _,disabled_rank=score(agent,splits['dev'],domain,args,best_depth,'disabled')
        _,shuffled_rank=score(agent,splits['dev'],domain,args,best_depth,'shuffled')
        _,frozen_rank=score(agent,splits['dev'],domain,args,best_depth,'frozen_first')
        _,zero_rank=score(agent,splits['dev'],domain,args,best_depth,'zero_tokens')
        evolution=trajectory_diagnostic(agent,splits['dev'],domain)
    status='LATENT_GAIN_OBSERVED' if devrank[0]>max(baseline_rank[0],
          disabled_rank[0],shuffled_rank[0],frozen_rank[0],zero_rank[0]) else 'NO_CAUSAL_GAIN_ESTABLISHED'
    report={'schema':SCHEMA+'.report','identity':identity,'status':status,
        'teacher_gate':evidence,'best_step':best_step,'best_depth':best_depth,
        'trained_steps':ss,'DEV_hybrid':devrank[0],'TEST_hybrid':testrank[0],
        'baseline_DEV':baseline_rank[0],'baseline_TEST':base_test[0],
        'DEV_disabled':disabled_rank[0],'DEV_shuffled':shuffled_rank[0],
        'DEV_frozen_first':frozen_rank[0],'DEV_zero_tokens':zero_rank[0],
        'depth_evolution':evolution,'holdout_caveat':'TEST was previously examined; exploratory only'}
    gauss._atomic_json(output/'report.json',report)
    print(json.dumps({k:report[k] for k in ('status','best_step','best_depth',
         'DEV_hybrid','TEST_hybrid','DEV_disabled','DEV_shuffled')},indent=2),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--bootstrap-stop-after',type=int,default=None)
    p.add_argument('--warmup-stop-after',type=int,default=None)
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--bootstrap-steps',type=int,default=4000)
    p.add_argument('--warmup-steps',type=int,default=2000)
    p.add_argument('--steps',type=int,default=12000)
    p.add_argument('--probe-every',type=int,default=500)
    p.add_argument('--batch-size',type=int,default=64)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--bootstrap-lr',type=float,default=.0005)
    p.add_argument('--warmup-lr',type=float,default=.0005)
    p.add_argument('--lr',type=float,default=.0002)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--min-teacher-gain',type=float,default=0.)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    return train(parser().parse_args(argv))

if __name__=='__main__':
    main()
