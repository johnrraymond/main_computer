#!/usr/bin/env python3
"""HybridAgent-style numerical latent-context recurrence (independent v1 experiment).

Format activation -> frozen-backbone HybridLink alignment -> two-pass joint SFT.
A single trainable causal backbone consumes original numerical tokens and its own
appended latent thoughts. The pretrained Gaussian reference is a frozen seed
feature extractor only; X/Y are final supervision, never recurrent inputs.

The 'static' arm is an equal-update, equal-parameter no-recurrent-read control.
Training teachers come only from visible count/payoff partial products; inference
never invokes the teacher embedding path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab

SCHEMA = 'nanojev.hawk-dove.hybridlink-sequence.v1'
DEPTH = 4
PHASES = ('format','alignment','joint')
ARMS = ('recurrent','static')


def xy_scale(payoff_bound):
    return float(blind.TOTAL * payoff_bound)


def normalized_truth(truth,scale):
    return truth / scale


def numeric_loss(pred, target):
    # L1 supplies an unambiguous gradient for near-zero targets, unlike relative
    # loss alone; scale-invariant MSE keeps the broad domain represented.
    difference = pred - target
    return 10. * difference.square().mean() + .2 * difference.abs().mean()


def latent_loss(pred, target):
    mse = F.mse_loss(pred,target)
    cos = (1. - F.cosine_similarity(pred,target,dim=-1)).mean()
    return mse + .1*cos


class SharedCausalBackbone(nn.Module):
    """Return all positions, not merely the last one; one set of weights."""
    def __init__(self,width,heads=4,layers=1,max_positions=6):
        super().__init__()
        if width % heads:raise ValueError('HYBRIDLINK_BAD_HEADS')
        self.positions = nn.Parameter(torch.zeros(max_positions,width))
        layer=nn.TransformerEncoderLayer(width,heads,width*2,dropout=0.,
                                          activation='gelu',batch_first=True,norm_first=True)
        self.layers=nn.TransformerEncoder(layer,num_layers=layers,enable_nested_tensor=False)
        self.norm=nn.LayerNorm(width)

    def forward(self,tokens):
        if tokens.ndim!=3 or tokens.shape[-1]!=self.positions.shape[-1] or not 1<=tokens.shape[1]<=len(self.positions):
            raise ValueError('HYBRIDLINK_CONTEXT_SHAPE_OR_OVERFLOW')
        t=tokens.shape[1]
        causal=torch.ones(t,t,dtype=torch.bool,device=tokens.device).triu(1)
        return self.norm(self.layers(tokens+self.positions[:t],mask=causal))


class HybridSequence(nn.Module):
    """Same backbone sees [numerical seed; t1; ...; tk] on every forward pass."""
    def __init__(self,reference,heads=4,layers=1):
        super().__init__()
        self.reference=reference
        for param in self.reference.parameters():param.requires_grad_(False)
        self.reference.eval()
        width=reference.hidden
        self.width=width
        self.seed_numeric=nn.Sequential(nn.Linear(blind.INPUT_DIM,width),nn.LayerNorm(width))
        self.seed_reference=nn.Sequential(nn.Linear(width,width),nn.LayerNorm(width))
        self.backbone=SharedCausalBackbone(width,heads,layers)
        self.link=v4.ResidualHybridLink(width)
        self.roles=nn.Embedding(DEPTH,width)
        self.product_encoder=nn.Sequential(nn.Linear(3,width),nn.Tanh(),nn.Linear(width,width))
        self.product_norm=nn.LayerNorm(width)
        self.value_probe=nn.Linear(width,1)
        self.answer=nn.Sequential(nn.Linear(width,width),nn.GELU(),nn.Linear(width,2))
        # The control uses step encodings instead of new backbone hidden states.
        # Parameter exists in BOTH arms to keep the parameter inventory equal.
        self.control_steps=nn.Parameter(torch.zeros(DEPTH,width))

    def train(self,mode=True):
        super().train(mode)
        self.reference.eval()
        return self

    def seed(self,features):
        with torch.no_grad():
            first=F.silu(self.reference.encoder[0](features))
            late=F.silu(self.reference.encoder[2](first))
        return torch.stack((self.seed_numeric(features), self.seed_reference(late)),dim=1)

    def teacher_tokens(self,features):
        """Training only: products from visible inputs, never oracle X/Y."""
        products=v4.stage_values(features)
        stats=torch.stack((products,torch.sign(products),torch.log1p(products.abs())),dim=-1)
        role=self.roles(torch.arange(DEPTH,device=features.device)).unsqueeze(0)
        return self.product_norm(role+self.product_encoder(stats))

    def teacher_first_pass(self,features,mode):
        seed=self.seed(features)
        if mode=='recurrent':
            teacher=self.teacher_tokens(features)
            teacher_context=torch.cat((seed,teacher),dim=1)
            h=self.backbone(teacher_context)
            # Hidden at position 1 predicts first latent input; at position 4
            # predicts the fourth. Never use the final-answer hidden position.
            generated=self.link(h[:,1:1+DEPTH,:])
        elif mode=='static':
            h0=self.backbone(seed)[:,-1,:]
            generated=self.link(h0[:,None,:]+self.control_steps.unsqueeze(0))
        else:raise ValueError('HYBRIDLINK_BAD_ARM')
        return generated

    def format_pass(self,features):
        teacher=self.teacher_tokens(features)
        hidden=self.backbone(torch.cat((self.seed(features),teacher),dim=1))
        answer=self.answer(hidden[:,-1])
        # Auxiliary product readout: make latent positions useful as numerical
        # representations, not just unobserved sequence decorations.
        values=self.value_probe(hidden[:,2:]).squeeze(-1)
        return answer,values

    def generated_second_pass(self,features,generated):
        if generated.shape!=(features.shape[0],DEPTH,self.width):
            raise ValueError('HYBRIDLINK_GENERATED_SHAPE_DRIFT')
        states=self.backbone(torch.cat((self.seed(features),generated),dim=1))
        answer=self.answer(states[:,-1,:])
        return answer,self.link(states[:,1:1+DEPTH,:])

    def rollout(self,features,depth=DEPTH,mode='recurrent', intervention='normal',trace=False):
        """Real inference: no ground-truth product embeddings or X/Y supplied."""
        if depth not in (0,1,2,4):raise ValueError('HYBRIDLINK_BAD_DEPTH')
        if intervention not in ('normal','zero','repeat_first','reverse'):
            raise ValueError('HYBRIDLINK_BAD_INTERVENTION')
        context=self.seed(features)
        thoughts=[]
        if mode=='static':
            h0=self.backbone(context)[:,-1]
            proposals=self.link(h0[:,None,:]+self.control_steps.unsqueeze(0))
            # All four proposals use one seed hidden; they never see a prior thought.
        elif mode!='recurrent':raise ValueError('HYBRIDLINK_BAD_ARM')
        if intervention=='reverse':
            # Generate a genuine free-running trajectory first, then change only
            # the order consumed by the final backbone answer pass.
            answer,normal_thoughts=self.rollout(features,depth,mode,'normal',trace=True)
            reordered=torch.stack(normal_thoughts[::-1],dim=1) if normal_thoughts else context[:,:0]
            hidden=self.backbone(torch.cat((self.seed(features),reordered),dim=1))
            return (self.answer(hidden[:,-1]),list(reordered.unbind(1))) if trace else self.answer(hidden[:,-1])
        for idx in range(depth):
            last=self.backbone(context)[:,-1] if mode=='recurrent' else None
            generated=self.link(last) if mode=='recurrent' else proposals[:,idx,:]
            if intervention=='zero':generated=torch.zeros_like(generated)
            if intervention=='repeat_first' and thoughts:generated=thoughts[0]
            thoughts.append(generated)
            context=torch.cat((context,generated[:,None,:]),dim=1)
        last=self.backbone(context)[:,-1]
        xy=self.answer(last)
        return (xy,thoughts) if trace else xy


def trainable(model,stage):
    for p in model.parameters():p.requires_grad_(stage!='alignment')
    for p in model.reference.parameters():p.requires_grad_(False)
    if stage=='format':
        for p in model.link.parameters():p.requires_grad_(False)
        model.control_steps.requires_grad_(False)
    if stage=='alignment':
        for p in model.link.parameters():p.requires_grad_(True)
        model.control_steps.requires_grad_(True)
    return [p for p in model.parameters() if p.requires_grad]


def stage_loss(model,features,truth,stage,mode,scale,autonomy_weight=.25):
    target=normalized_truth(truth,scale)
    if stage=='format':
        answer,values=model.format_pass(features)
        products=v4.stage_values(features)
        return numeric_loss(answer,target)+.3*F.mse_loss(values,products), {'answer':answer}
    if stage=='alignment':
        generated=model.teacher_first_pass(features,mode)
        with torch.no_grad():correct=model.teacher_tokens(features)
        return latent_loss(generated,correct),{'generated':generated}
    if stage=='joint':
        # PASS 1 (teacher forced): generate latent inputs at supervised positions.
        # Inputs to pass 2 are detached as in cached first-pass generation; link
        # still gets gradients from the second pass's latent regression objective.
        with torch.no_grad():generated=model.teacher_first_pass(features,mode)
        # PASS 2: the SAME backbone consumes HybridLink's own generated embeddings.
        answer,regenerated=model.generated_second_pass(features,generated.detach())
        correct=model.teacher_tokens(features).detach()
        loss=numeric_loss(answer,target)+.15*latent_loss(regenerated,correct)
        if autonomy_weight:
            # Numerical adaptation: close the teacher-forced-first-pass to
            # free-running-inference mismatch, absent from the original paper.
            free=model.rollout(features,DEPTH,mode)
            loss=loss+autonomy_weight*numeric_loss(free,target)
        return loss, {'answer':answer}
    raise ValueError('HYBRIDLINK_BAD_STAGE')


@torch.no_grad()
def score(model,split,scale,mode,depth,device,batch_size,tolerance,intervention='normal'):
    model.eval();values=[]
    for features in split['features'].split(batch_size):
        values.append((model.rollout(features,depth,mode,intervention)*scale).detach().cpu())
    return lab.strict_metrics(torch.cat(values),split,tolerance)


@torch.no_grad()
def compare_thoughts(model,split,scale,mode,device,batch_size,tolerance):
    results={name:score(model,split,scale,mode,DEPTH,device,batch_size,tolerance,name)
             for name in ('normal','zero','repeat_first','reverse')}
    x=split['features'][:min(batch_size,len(split['features']))]
    _,thoughts=model.rollout(x,DEPTH,mode,trace=True)
    norms=[float(t.norm(dim=-1).mean()) for t in thoughts]
    adjacent=[float((a-b).abs().mean()) for a,b in zip(thoughts,thoughts[1:])]
    return {'accuracy':results,'mean_thought_l2_by_step':norms,
            'mean_adjacent_thought_change':adjacent}


def file_sha256(path):
    import hashlib
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_source(source,device):
    """Original Gaussian checkpoint only: no v4 run/teacher checkpoint required."""
    source=Path(source).expanduser().resolve()
    original=torch.load(source,map_location='cpu',weights_only=True)
    if original.get('schema')!=blind.SCHEMA or not isinstance(original.get('identity'),dict):
        raise RuntimeError('HYBRIDLINK_SOURCE_SCHEMA_REFUSED')
    ci=original['identity']
    splits,domain,manifest=blind._dataset(argparse.Namespace(**ci),torch.device(device))
    if (manifest['matrix_hashes']!=ci['matrix_hashes'] or
            [list(bounds) for bounds in domain]!=ci['domain']):
        raise RuntimeError('HYBRIDLINK_MATRIX_OR_DOMAIN_DRIFT')
    reference=blind.BlindDensityNetwork(ci['banks'],ci['grid_bins'],ci['seed'],ci['hidden'])
    reference.load_state_dict(original['model'],strict=True)
    reference=reference.to(device).eval()
    return reference,splits,domain,{'source_identity':ci,'matrix_hashes':manifest['matrix_hashes']}


def fingerprint(args,source,config):
    fields=('format_steps','align_steps','joint_steps','probe_every','batch_size',
            'eval_batch_size','lr','seed','heads','layers','cpu_threads','tolerance',
            'autonomy_weight','min_gain','final_test','arms')
    return {'schema':SCHEMA,'source_sha256':file_sha256(source),
            'source_identity':config['source_identity'],'matrix_hashes':config['matrix_hashes'],
            'options':{name:getattr(args,name) for name in fields},
            'inference_policy':'six visible numeric features; no teacher products or oracle X/Y fed back',
            'backbone_policy':'trainable causal transformer; frozen Gaussian seed features only',
            'stages':list(PHASES)}


def run_arm(name,reference,splits,scale,args,root,identity):
    torch.manual_seed(args.seed)
    model=HybridSequence(reference,args.heads,args.layers).to(args.device)
    rng=torch.Generator(device='cpu').manual_seed(args.seed+941)
    folder=root/name;folder.mkdir(parents=True,exist_ok=True)
    latest=folder/'latest.pt';best=folder/'best.pt';done=folder/'done.json'
    if args.resume and done.exists():
        result=json.loads(done.read_text(encoding='utf-8'))
        if result['identity']!=identity or result['arm']!=name:raise RuntimeError('HYBRIDLINK_DONE_IDENTITY_DRIFT')
        return result
    if not args.resume and any(folder.iterdir()):raise FileExistsError('HYBRIDLINK_ARM_OVERWRITE_REFUSED')
    if args.resume and not latest.exists() and any(folder.iterdir()):
        raise RuntimeError('HYBRIDLINK_RESUME_PROGRESS_MISSING')
    planned=[args.format_steps,args.align_steps,args.joint_steps]
    total=sum(planned)
    index=0;optimizer=None;history=[];best_rank=None;best_step=0;saved=None
    if args.resume and latest.exists():
        saved=torch.load(latest,map_location='cpu',weights_only=True)
        if saved['schema']!=SCHEMA+'.progress' or saved['identity']!=identity or saved['arm']!=name:
            raise RuntimeError('HYBRIDLINK_PROGRESS_IDENTITY_DRIFT')
        model.load_state_dict(saved['model'],strict=True)
        index=saved['index'];history=saved['history'];best_rank=saved['best_rank'];best_step=saved['best_step']
        rng.set_state(saved['rng']);torch.set_rng_state(saved['torch_rng'])
        if saved['cuda_rng'] is not None and torch.cuda.is_available():torch.cuda.set_rng_state_all(saved['cuda_rng'])
    train=splits['train'];dev=splits['dev'];count=len(train['rows'])
    budget=total if args.stop_after is None else min(total,args.stop_after)
    phase_offsets=[0,planned[0],planned[0]+planned[1]]
    last_phase=None
    for step in range(index+1,budget+1):
        phase_index=next(i for i,offset in reversed(list(enumerate(phase_offsets))) if step>offset)
        phase=PHASES[phase_index]
        if phase!=last_phase:
            params=trainable(model,phase)
            optimizer=torch.optim.AdamW(params,lr=args.lr,weight_decay=0.)
            if saved is not None and step==index+1 and saved.get('phase')==phase and 'optimizer' in saved:
                optimizer.load_state_dict(saved['optimizer'])
            last_phase=phase
        model.train()
        idx=torch.randint(count,(args.batch_size,),generator=rng).to(args.device)
        x=train['features'].index_select(0,idx);y=train['numeric'].index_select(0,idx)
        loss,_=stage_loss(model,x,y,phase,name,scale,args.autonomy_weight)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('HYBRIDLINK_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        optimizer.step()
        checkpoint=(step%args.probe_every==0 or step in (phase_offsets[1],phase_offsets[2],total,budget))
        if checkpoint:
            metrics=score(model,dev,scale,name,DEPTH,args.device,args.eval_batch_size,args.tolerance)
            r=(metrics['min_tier_joint5'],-metrics['mean_abs_xy_error'])
            if phase=='joint' and (best_rank is None or r>tuple(best_rank)):
                best_rank=list(r);best_step=step
                gauss._atomic_save(best,{'schema':SCHEMA+'.best','identity':identity,'arm':name,
                                         'step':step,'model':model.state_dict(),'DEV':metrics})
            history.append({'step':step,'stage':phase,'loss':float(loss.detach()),'DEV':metrics})
            print(f'HYBRIDLINK_TRAIN arm={name} stage={phase} step={step}/{total} '
                  f'DEV_joint5={r[0]:.4f} DEV_MAE={metrics["mean_abs_xy_error"]:.6g}',flush=True)
            gauss._atomic_save(latest,{'schema':SCHEMA+'.progress','identity':identity,'arm':name,
                          'index':step,'phase':phase,'model':model.state_dict(),'optimizer':optimizer.state_dict(),
                          'rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
                          'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
                          'history':history,'best_rank':best_rank,'best_step':best_step})
    if budget<total:
        return {'status':'PAUSED','arm':name,'index':budget,'planned':total}
    saved_best=torch.load(best,map_location='cpu',weights_only=True)
    if saved_best['identity']!=identity or saved_best['arm']!=name:raise RuntimeError('HYBRIDLINK_BEST_DRIFT')
    model.load_state_dict(saved_best['model'],strict=True)
    scores={str(depth):score(model,dev,scale,name,depth,args.device,args.eval_batch_size,args.tolerance)
            for depth in (0,1,2,4)}
    effects=compare_thoughts(model,dev,scale,name,args.device,args.eval_batch_size,args.tolerance)
    result={'schema':SCHEMA,'identity':identity,'arm':name,'best_step':best_step,'DEV_by_depth':scores,
            'DEV_interventions':effects,'history':history,
            'TEST':score(model,splits['test'],scale,name,4,args.device,args.eval_batch_size,args.tolerance)
                   if args.final_test else None}
    gauss._atomic_json(done,result)
    return result


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('HYBRIDLINK_CUDA_UNAVAILABLE')
    if not (all(getattr(args,x)>0 for x in ('format_steps','align_steps','joint_steps','probe_every',
                   'batch_size','eval_batch_size','cpu_threads','heads','layers')) and 0<args.lr<1
            and 0<args.tolerance<1 and 0<args.min_gain<1 and 0<=args.autonomy_weight<=1):
        raise ValueError('HYBRIDLINK_INVALID_OPTIONS')
    arms=args.arms.split(',')
    if len(set(arms))!=len(arms) or not arms or any(x not in ARMS for x in arms):
        raise ValueError('HYBRIDLINK_BAD_ARMS')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    source=Path(args.reference_checkpoint).expanduser().resolve()
    initial_sha=file_sha256(source)
    reference,splits,domain,config=load_source(source,args.device)
    if reference.hidden%args.heads:raise ValueError('HYBRIDLINK_WIDTH_HEADS_MISMATCH')
    scale=xy_scale(config['source_identity']['payoff_bound'])
    ident=fingerprint(args,source,config)
    root=Path(args.output).expanduser().resolve()
    if args.resume:
        if not (root/'config.json').exists() or json.loads((root/'config.json').read_text(encoding='utf-8'))!=ident:
            raise RuntimeError('HYBRIDLINK_CONFIG_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):raise FileExistsError('HYBRIDLINK_OUTPUT_EXISTS')
        root.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(root/'config.json',ident)
    print('HYBRIDLINK_SOURCE_VERIFIED=TRUE TRAINABLE_SHARED_CAUSAL_BACKBONE=TRUE '
          'GENERATED_INPUT_CONTEXT=TRUE GAUSSIAN_REFERENCE_FROZEN=TRUE',flush=True)
    with torch.no_grad():
        ref_predictions=torch.cat([gauss.numeric_decode(reference(features),domain).cpu()
                                  for features in splits['dev']['features'].split(args.eval_batch_size)])
    reference_score=lab.strict_metrics(ref_predictions,splits['dev'],args.tolerance)
    print(f'HYBRIDLINK_REFERENCE_DEV={reference_score["min_tier_joint5"]:.6f}',flush=True)
    reports={}
    for name in arms:
        report=run_arm(name,reference,splits,scale,args,root,ident)
        reports[name]=report
        if report.get('status')=='PAUSED':
            print('HYBRIDLINK_PAUSED='+json.dumps(report,separators=(',',':')),flush=True)
            return report
    gain={}
    if all(name in reports for name in ARMS):
        rec=reports['recurrent']['DEV_by_depth']['4']
        static=reports['static']['DEV_by_depth']['4']
        effects=reports['recurrent']['DEV_interventions']['accuracy']
        comparisons={'static':static,'reference_gaussian':reference_score,
                     'zero_thoughts':effects['zero'],
                     'repeat_first':effects['repeat_first'],
                     'depth1_ablation':reports['recurrent']['DEV_by_depth']['1'],
                     'depth2_ablation':reports['recurrent']['DEV_by_depth']['2']}
        per_tier={label:{tier:rec['per_tier'][tier]-control['per_tier'][tier]
                         for tier in blind.TIERS}
                  for label,control in comparisons.items()}
        # Only the separately trained, equally updated static arm and matched
        # interventions certify causal feedback. Shallower depths are ablations
        # of a depth-4-trained model, not independently trained controls.
        required=('static','reference_gaussian','zero_thoughts','repeat_first')
        certified=all(all(per_tier[label][tier]>=args.min_gain for tier in blind.TIERS)
                      for label in required)
        gain={'per_tier_paired_accuracy_differences':per_tier,
              'tested_controls':list(required),
              'shorter_depths_are_untrained_ablations':True,
              'min_required':args.min_gain,
              'status':'PASS' if certified else 'NOT_ESTABLISHED'}
    if file_sha256(source)!=initial_sha:
        raise RuntimeError('HYBRIDLINK_SOURCE_CHECKPOINT_MUTATED')
    report={'schema':SCHEMA,'identity':ident,'source_unchanged':True,'reference_gaussian_DEV':reference_score,
            'arms':reports,'feedback_certification':gain,
            'comparison_caveat':'trainable causal reasoning backbone is new; pretrained numerical MLP is frozen input feature extractor'}
    gauss._atomic_json(root/'report.json',report)
    print('HYBRIDLINK_FEEDBACK='+json.dumps(gain,separators=(',',':')),flush=True)
    print('HYBRIDLINK_REPORT='+str(root/'report.json'),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--format-steps',type=int,default=600)
    p.add_argument('--align-steps',type=int,default=400)
    p.add_argument('--joint-steps',type=int,default=1400)
    p.add_argument('--probe-every',type=int,default=200)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--autonomy-weight',type=float,default=.25)
    p.add_argument('--min-gain',type=float,default=.02)
    p.add_argument('--arms',default='recurrent,static')
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--final-test',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
