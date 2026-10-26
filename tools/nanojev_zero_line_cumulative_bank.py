#!/usr/bin/env python3
"""Cumulative learned magnitude-bank expansion: certified base + accepted 128:159 + new 160:191.

This is deliberately NOT a Python magnitude router. The prior promoted neural
router is frozen. A newly learned gate chooses between that incumbent's HARD
answer and the new frozen specialist's HARD answer. Targets supervise training
only. No gold sum, oracle expert ID, residual, magnitude threshold, or
correctness test enters either inference gate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

import torch
from torch.nn import functional as F

from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, atomic_json, atomic_pt, exact_mask, sample_absolute_band,
    make_cache, bank_logits, train_bank, score, parse_bands,
)
from tools.nanojev_zero_line_learned_router import (
    NeuralRouter, cache_with_tokens, frozen_bank_outputs, hard_select,
    mixture_loss, validate_bank, independent_inference,
)

SCHEMA = 'nanojev.zero-line-cumulative-learned-bank.v1'
SOURCE_RADIUS = 127
PRIOR_BAND = (128,159)
NEW_BAND = (160,191)


def require_paths(output: Path, roots: list[Path]):
    for src in roots:
        if output == src or output in src.parents or src in output.parents:
            raise RuntimeError('ZERO_CUMULATIVE_OUTPUT_SOURCE_OVERLAP')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('ZERO_CUMULATIVE_OUTPUT_EXISTS')


def load_prior(source, base, bank_dir, router_dir, device):
    """Fail closed unless the exact promoted previous bank and router match."""
    old_bank, old_bank_path, old_cfg = validate_bank(
        source, bank_dir, *PRIOR_BAND, device, base)
    prior_pt = router_dir/'promoted.pt'
    prior_result = router_dir/'result.json'
    if not prior_pt.is_file() or not prior_result.is_file():
        raise RuntimeError('ZERO_CUMULATIVE_PRIOR_ROUTER_NOT_PROMOTED')
    if not json.loads(prior_result.read_text(encoding='utf-8')).get('eligible'):
        raise RuntimeError('ZERO_CUMULATIVE_PRIOR_ROUTER_NOT_ELIGIBLE')
    saved = torch.load(prior_pt,map_location=device,weights_only=True)
    if saved.get('band') != list(PRIOR_BAND) or saved.get('source_sha256') != source['checkpoint_sha256']:
        raise RuntimeError('ZERO_CUMULATIVE_PRIOR_SOURCE_MISMATCH')
    if saved.get('specialist_sha256') != sha256(old_bank_path):
        raise RuntimeError('ZERO_CUMULATIVE_PRIOR_SPECIALIST_MISMATCH')
    if saved.get('router_input') not in ('frozen_backbone_and_input_tokens','frozen_backbone_only'):
        raise RuntimeError('ZERO_CUMULATIVE_UNSUPPORTED_PRIOR_ROUTER_INPUT')
    include_tokens = saved['router_input']=='frozen_backbone_and_input_tokens'
    old_router = NeuralRouter(saved['feature_width'], saved['token_width'],
                              saved['hidden'],include_tokens).to(device)
    old_router.load_state_dict(saved['router'],strict=True)
    old_router.eval().requires_grad_(False)
    old_bank.eval().requires_grad_(False)
    return old_bank,old_router,include_tokens,old_bank_path,prior_pt,old_cfg


@torch.inference_mode()
def prior_choice(data, prior_router, prior_bank, batch_size=2048):
    """Construct the INCUMBENT hard output without labels or Python routing."""
    bank_log = frozen_bank_outputs(prior_bank,data,batch_size)
    logits=[]; decisions=[]
    for i in range(0,len(data['features']),batch_size):
        j=i+batch_size
        gate=prior_router(data['features'][i:j],data['tokens'][i:j])
        selected,selection=hard_select(gate,data['original'][i:j],bank_log[i:j])
        logits.append(selected); decisions.append(selection)
    return torch.cat(logits),torch.cat(decisions)


def prepare(base, old_bank,old_router, rows,device,batch_size,include_tokens,new_bank=None):
    data=cache_with_tokens(base,rows,device,batch_size,include_tokens)
    incumbent,prior_route=prior_choice(data,old_router,old_bank,batch_size)
    data['incumbent']=incumbent
    data['prior_route']=prior_route
    if new_bank is not None:
        data['new_logits']=frozen_bank_outputs(new_bank,data,batch_size)
    return data


def new_neural_selection(logit,incumbent,new_logits):
    """Hard neural gate; WRONG choices remain wrong."""
    return hard_select(logit,incumbent,new_logits)


@torch.inference_mode()
def evaluate(gate, data, *, batch_size=2048):
    gate.eval()
    decisions=[]; outs=[]
    for i in range(0,len(data['target']),batch_size):
        j=i+batch_size
        output_gate=gate(data['features'][i:j],data['tokens'][i:j])
        out,choice=new_neural_selection(output_gate,data['incumbent'][i:j],data['new_logits'][i:j])
        decisions.append(choice);outs.append(out)
    chosen=torch.cat(outs);selected=torch.cat(decisions)
    y=data['target']; prior=exact_mask(data['incumbent'],y)
    bank=exact_mask(data['new_logits'],y); actual=exact_mask(chosen,y)
    near=y.abs()<=2
    # Oracle comparator must NEVER be used in inference or training. Explicit
    # input-magnitude comparison likewise exists only in the evaluation report.
    true_mag=data['operands'].abs().amax(-1).to(selected.device)
    python_choice=(true_mag>=NEW_BAND[0])&(true_mag<=NEW_BAND[1])
    deterministic=torch.where(python_choice[:,None],data['new_logits'],data['incumbent'])
    return {
        'cards':int(len(y)),
        'prior_accuracy':float(prior.float().mean()),
        'new_bank_accuracy':float(bank.float().mean()),
        'learned_accuracy':float(actual.float().mean()),
        'new_bank_selection_rate':float(selected.float().mean()),
        'previous_128_159_selection_rate':float(data['prior_route'].float().mean()),
        'input_magnitude_reference_accuracy':float(exact_mask(deterministic,y).float().mean()),
        'newly_correct_vs_incumbent':int((~prior&actual).sum()),
        'newly_wrong_vs_incumbent':int((prior&~actual).sum()),
        'near_zero_new_errors':int((near&prior&~actual).sum()),
        'oracle_either_expert_accuracy_DIAGNOSTIC_ONLY':float((prior|bank).float().mean()),
        'incorrect_with_correct_alternative':int((~actual&(prior|bank)).sum()),
    }


def train_gate(gate, center, previous_train, new_train, previous_dev, new_dev, *,
               steps,probe_every,batch_size,learning_rate,seed,output):
    """Train from FINAL ANSWER only. OOD and held-out magnitudes never select weights."""
    rng=random.Random(seed)
    opt=torch.optim.AdamW(gate.parameters(),lr=learning_rate,weight_decay=0.)
    pools=(center,previous_train,new_train)
    history=[];best=None;best_quality=None;best_step=None
    for step in range(1,steps+1):
        idxs=[]
        counts=(batch_size//3,batch_size//3,batch_size-2*(batch_size//3))
        for data,n in zip(pools,counts):
            idxs.append(torch.tensor([rng.randrange(len(data['target'])) for _ in range(n)],
                                     dtype=torch.long,device=data['target'].device))
        batch=lambda key:torch.cat([d[key][i] for d,i in zip(pools,idxs)])
        gate.train()
        logit=gate(batch('features'),batch('tokens'))
        loss=mixture_loss(logit,batch('incumbent'),batch('new_logits'),batch('target'))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(gate.parameters(),1.)
        opt.step()
        if step%probe_every!=0 and step!=steps:continue
        mid=evaluate(gate,center)
        old=evaluate(gate,previous_dev)
        new=evaluate(gate,new_dev)
        row={'step':step,'loss':float(loss.detach()),'center':mid,'previous_dev':old,'new_dev':new}
        history.append(row)
        # Select using only CENTER and DEV. OOD is an untouched final evaluation.
        quality=(int(mid['newly_wrong_vs_incumbent']==0 and old['newly_wrong_vs_incumbent']==0),
                 -mid['newly_wrong_vs_incumbent'],-old['newly_wrong_vs_incumbent'],
                 new['learned_accuracy'],-new['newly_wrong_vs_incumbent'])
        if best_quality is None or quality>best_quality:
            best_quality=quality;best_step=step
            best={k:v.detach().clone() for k,v in gate.state_dict().items()}
        print(f'ZERO_CUM_ROUTER step={step}/{steps} loss={float(loss.detach()):.5f} '
              f'CENTER={mid["learned_accuracy"]:.5f} OLD_DEV={old["learned_accuracy"]:.5f} '
              f'NEW_DEV={new["learned_accuracy"]:.5f} NEW_ROUTE={new["new_bank_selection_rate"]:.4f} '
              f'CENTER_HARM={mid["newly_wrong_vs_incumbent"]} OLD_HARM={old["newly_wrong_vs_incumbent"]}',flush=True)
        atomic_pt(output/'latest.pt',{'schema':SCHEMA+'.train','step':step,
                  'gate':gate.state_dict(),'optimizer':opt.state_dict(),'rng':rng.getstate()})
        atomic_json(output/'progress.json',{'schema':SCHEMA,'history':history})
    gate.load_state_dict(best,strict=True)
    return history,best_step


@torch.inference_mode()
def infer(base, prior_bank, prior_router, new_bank, new_gate, a,b,device,include_tokens):
    """Live prediction takes ONLY raw operand integers; no target or category."""
    operands=torch.tensor([[a,b]],dtype=torch.long,device=device)
    slot={}
    def capture(_module,_args,out):slot['features']=out.detach()
    hook=base.norm.register_forward_hook(capture)
    try:
        result=base(operands)
    finally:
        hook.remove()
    if 'features' not in slot:
        raise RuntimeError('ZERO_CUMULATIVE_MISSING_FEATURES')
    features=slot['features']
    tokens=base.input(operands).flatten(1) if include_tokens else features.new_empty((1,0))
    original=torch.cat((result['sign'],result['digits'].reshape(-1,70)),dim=-1)
    old_logits=prior_bank(features)
    incumbent,previous_decision=hard_select(prior_router(features,tokens),original,old_logits)
    selected,new_decision=hard_select(new_gate(features,tokens),incumbent,new_bank(features))
    sign=selected[:,:3].argmax(-1).long()-1
    digs=selected[:,3:].reshape(-1,7,10).argmax(-1).long()
    magnitude=(digs*digs.new_tensor([10**i for i in range(7)])).sum(-1)
    return {'a':a,'b':b,'route':('160-191' if bool(new_decision[0]) else
             '128-159' if bool(previous_decision[0]) else 'base'),
            'prediction':int((sign*magnitude)[0]),
            'new_bank_probability':float(torch.sigmoid(new_gate(features,tokens))[0]),
            'routing':'neural_only_no_fallback'}


def _whole_shell(low,high):
    """Exhaustive existing-band retention test, never router training data."""
    pairs=[]
    for a in range(-high,high+1):
        for b in range(-high,high+1):
            if low<=max(abs(a),abs(b))<=high:
                pairs.append((a,b))
    x=torch.tensor(pairs,dtype=torch.long)
    return {'x':x,'y':x.sum(1)}


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_CUMULATIVE_CUDA_UNAVAILABLE')
    if tuple(parse_bands(args.new_band)[0])!=NEW_BAND or len(parse_bands(args.new_band))!=1:
        raise ValueError('ZERO_CUMULATIVE_EXPECTS_NEW_BAND_160_191')
    if min(args.steps_per_bank,args.probe_every,args.router_steps,args.router_probe_every,
           args.batch_size,args.eval_batch_size,args.hidden,args.router_hidden,
           args.per_stratum,args.cpu_threads)<1 or args.batch_size<3:
        raise ValueError('ZERO_CUMULATIVE_INVALID_OPTIONS')
    if not (0<args.lr<1 and 0<args.router_lr<1 and
            0<=args.max_prior_new_bank_rate<=1 and
            0<=args.min_accuracy<=1 and 0<=args.min_new_bank_rate<=1 and
            0<=args.allowed_gap<=1):
        raise ValueError('ZERO_CUMULATIVE_INVALID_LEARNING_RATE')
    src=Path(args.ladder_output).expanduser().resolve()
    old_bank_dir=Path(args.prior_bank_output).expanduser().resolve()
    old_router_dir=Path(args.prior_router_output).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    require_paths(out,[src,old_bank_dir,old_router_dir])
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    base,source=load_certified_model(src,args.device)
    base.eval().requires_grad_(False)
    old_bank,old_router,include_tokens,old_bank_file,old_router_file,_=load_prior(
        source,base,old_bank_dir,old_router_dir,args.device)
    originals=[source['checkpoint'],old_bank_file,old_router_file]
    hashes=[sha256(x) for x in originals]
    out.mkdir(parents=True,exist_ok=True)
    cfg={'schema':SCHEMA,'source_sha256':hashes[0],
         'prior_bank_sha256':hashes[1], 'prior_router_sha256':hashes[2],
         'new_band':list(NEW_BAND),'router_input':'frozen_backbone_features_and_tokens' if include_tokens else 'frozen_backbone_features',
         'training':'final_signed_answer_mixture_likelihood_no_bank_labels',
         'inference':'two_learned_gates_hard_decisions_no_python_fallback',
         'parameters':{k:v for k,v in vars(args).items() if k not in ('ladder_output','prior_bank_output','prior_router_output','output','device')}}
    atomic_json(out/'config.json',cfg)
    new_splits=sample_absolute_band(*NEW_BAND,args.seed,args.per_stratum)
    prior_splits=sample_absolute_band(*PRIOR_BAND,args.seed,args.per_stratum)
    new_cache={key:prepare(base,old_bank,old_router,rows,args.device,args.eval_batch_size,include_tokens)
               for key,rows in new_splits.items()}
    old_cache={key:prepare(base,old_bank,old_router,rows,args.device,args.eval_batch_size,include_tokens)
               for key,rows in prior_splits.items()}
    center=prepare(base,old_bank,old_router,deck(SOURCE_RADIUS),args.device,args.eval_batch_size,include_tokens)
    if not bool(exact_mask(center['original'],center['target']).all()):
        raise RuntimeError('ZERO_CUMULATIVE_BASE_DECK_BROKEN')
    if not bool(exact_mask(center['incumbent'],center['target']).all()):
        raise RuntimeError('ZERO_CUMULATIVE_PROMOTED_ROUTER_BROKE_BASE_DECK')
    # Train new bank, with no changes to base/prior bank/prior router.
    bank=AbsoluteBank(int(base.query.shape[0]),args.hidden).to(args.device)
    bank_history=train_bank(bank,new_cache['train'],new_cache['dev'],new_cache['ood'],
                            steps=args.steps_per_bank,batch_size=args.batch_size,
                            probe_every=args.probe_every,lr=args.lr,seed=args.seed+17,
                            prefix='M=160:191')
    bank.eval().requires_grad_(False)
    bank_dev=score(bank_logits(bank,new_cache['dev']),new_cache['dev'],baseline=new_cache['dev']['incumbent'])
    bank_ood=score(bank_logits(bank,new_cache['ood']),new_cache['ood'],baseline=new_cache['ood']['incumbent'])
    bank_payload={'schema':SCHEMA+'.bank-candidate','source_sha256':hashes[0],
                  'band':list(NEW_BAND),'hidden':args.hidden,'bank':bank.state_dict()}
    atomic_pt(out/'bank_candidate.pt',bank_payload)
    # Freeze bank and build all logits. No target or prior correctness is given to gate.
    for data in [center,*old_cache.values(),*new_cache.values()]:
        data['new_logits']=frozen_bank_outputs(bank,data,args.eval_batch_size)
    gate=NeuralRouter(center['features'].shape[-1],center['tokens'].shape[-1],
                      args.router_hidden,include_tokens).to(args.device)
    # Favor the incumbent at initialization; neural gradients must earn a new route.
    with torch.no_grad():gate.net[-1].bias.fill_(-3.)
    history,chosen_step=train_gate(gate,center,old_cache['train'],new_cache['train'],
                                   old_cache['dev'],new_cache['dev'],steps=args.router_steps,
                                   probe_every=args.router_probe_every,
                                   batch_size=args.batch_size,learning_rate=args.router_lr,
                                   seed=args.seed+9000,output=out)
    metrics={'center_full':evaluate(gate,center),
             'prior_band_dev':evaluate(gate,old_cache['dev']),
             'prior_band_ood':evaluate(gate,old_cache['ood']),
             'new_band_dev':evaluate(gate,new_cache['dev']),
             'new_band_ood':evaluate(gate,new_cache['ood'])}
    # Exhaustive old-bank coverage; don't infer it from a sparse DEV sample.
    old_full=prepare(base,old_bank,old_router,_whole_shell(*PRIOR_BAND),
                     args.device,args.eval_batch_size,include_tokens,bank)
    metrics['prior_band_full']=evaluate(gate,old_full)
    # Adjacent untrained region: diagnostic, not used for weight selection.
    adjacent=sample_absolute_band(192,223,args.seed,args.per_stratum,extrapolation=True)['ood']
    adj_data=prepare(base,old_bank,old_router,adjacent,args.device,args.eval_batch_size,include_tokens,bank)
    metrics['next_magnitude_untrained']=evaluate(gate,adj_data)
    # The base and previously accepted bank must not lose previously correct cases.
    protect=all(metrics[k]['newly_wrong_vs_incumbent']==0 and
                metrics[k]['new_bank_selection_rate']<=args.max_prior_new_bank_rate
                for k in ('center_full','prior_band_full','prior_band_dev','prior_band_ood'))
    new_good=all(metrics[k]['learned_accuracy']>=args.min_accuracy and
                 metrics[k]['new_bank_selection_rate']>=args.min_new_bank_rate and
                 metrics[k]['near_zero_new_errors']==0 and
                 metrics[k]['learned_accuracy']+args.allowed_gap>=metrics[k]['input_magnitude_reference_accuracy']
                 for k in ('new_band_dev','new_band_ood'))
    bank_good=all(x['accuracy']>=args.min_accuracy for x in (bank_dev,bank_ood))
    eligible=protect and new_good and bank_good
    if [sha256(x) for x in originals] != hashes:
        raise RuntimeError('ZERO_CUMULATIVE_INPUT_ARTIFACT_MUTATED')
    new_gate_payload={'schema':SCHEMA+'.gate-candidate',
                      'new_bank_sha256':sha256(out/'bank_candidate.pt'),
                      'source_sha256':hashes[0], 'prior_bank_sha256':hashes[1],
                      'prior_router_sha256':hashes[2],
                      'new_band':list(NEW_BAND),'hidden':args.router_hidden,
                      'feature_width':center['features'].shape[-1],
                      'token_width':center['tokens'].shape[-1],
                      'include_tokens':include_tokens,'gate':gate.state_dict()}
    atomic_pt(out/'gate_candidate.pt',new_gate_payload)
    if eligible:
        atomic_pt(out/'bank_promoted.pt',{**bank_payload,'schema':SCHEMA+'.bank-promoted'})
        atomic_pt(out/'gate_promoted.pt',{**new_gate_payload,
                  'schema':SCHEMA+'.gate-promoted',
                  'new_bank_sha256':sha256(out/'bank_promoted.pt')})
    report={'schema':SCHEMA,'eligible':eligible,'bank_eligible':bank_good,'routing_eligible':protect and new_good,
            'source_immutable':True,'prior_bank_immutable':True,'prior_router_immutable':True,
            'no_oracle_inference':True,'no_python_fallback':True,'test_untouched':True,
            'selected_step_by_dev_only':chosen_step,
            'new_specialist':{'dev':bank_dev,'ood':bank_ood},
            'evaluations':metrics,'history':history,
            'bank_checkpoint':'bank_promoted.pt' if eligible else 'bank_candidate.pt',
            'gate_checkpoint':'gate_promoted.pt' if eligible else 'gate_candidate.pt'}
    atomic_json(out/'result.json',report)
    print('ZERO_CUMULATIVE_RESULT='+json.dumps({
        'eligible':eligible,'source_unchanged':True,'center':metrics['center_full']['learned_accuracy'],
        'prior_full':metrics['prior_band_full']['learned_accuracy'],
        'prior_full_new_errors':metrics['prior_band_full']['newly_wrong_vs_incumbent'],
        'new_dev':metrics['new_band_dev']['learned_accuracy'],
        'new_ood':metrics['new_band_ood']['learned_accuracy'],
        'new_ood_bank_selection':metrics['new_band_ood']['new_bank_selection_rate'],
        'specialist_ood':bank_ood['accuracy'],
        'adjacent':metrics['next_magnitude_untrained']['learned_accuracy'],
        'selected_step':chosen_step,'report':str(out/'result.json')},sort_keys=True),flush=True)
    print('ZERO_CUMULATIVE_SOURCE_IMMUTABLE=TRUE PRIOR_BANK_FROZEN=TRUE PRIOR_ROUTER_FROZEN=TRUE ZERO_ORACLE_INFERENCE=TRUE',flush=True)
    return report


def predict(args):
    output=Path(args.output).expanduser().resolve()
    suffix='candidate' if args.allow_candidate else 'promoted'
    newbankpt=output/f'bank_{suffix}.pt';gatept=output/f'gate_{suffix}.pt'
    bankdata=torch.load(newbankpt,map_location=args.device,weights_only=True)
    gatedata=torch.load(gatept,map_location=args.device,weights_only=True)
    if (bankdata.get('band') != list(NEW_BAND) or
        gatedata.get('new_band') != list(NEW_BAND) or
        gatedata.get('new_bank_sha256') != sha256(newbankpt)):
        raise RuntimeError('ZERO_CUMULATIVE_PREDICT_NEW_BANK_MISMATCH')
    base,source=load_certified_model(Path(args.ladder_output).expanduser().resolve(),args.device)
    priorbank,priorrouter,include_tokens,bank_file,router_file,_=load_prior(
        source,base,Path(args.prior_bank_output).expanduser().resolve(),
        Path(args.prior_router_output).expanduser().resolve(),args.device)
    hashes=(source['checkpoint_sha256'],sha256(bank_file),sha256(router_file))
    for data in (bankdata,gatedata):
        if (data['source_sha256'],data['prior_bank_sha256'] if 'prior_bank_sha256' in data else hashes[1],
            data['prior_router_sha256'] if 'prior_router_sha256' in data else hashes[2]) != hashes:
            raise RuntimeError('ZERO_CUMULATIVE_PREDICT_PROVENANCE_MISMATCH')
    bank=AbsoluteBank(int(base.query.shape[0]),bankdata['hidden']).to(args.device)
    bank.load_state_dict(bankdata['bank'],strict=True);bank.eval().requires_grad_(False)
    gate=NeuralRouter(gatedata['feature_width'],gatedata['token_width'],
                      gatedata['hidden'],gatedata['include_tokens']).to(args.device)
    gate.load_state_dict(gatedata['gate'],strict=True);gate.eval().requires_grad_(False)
    base.eval().requires_grad_(False)
    result=infer(base,priorbank,priorrouter,bank,gate,args.a,args.b,args.device,include_tokens)
    print('ZERO_CUMULATIVE_PREDICT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=['train','predict'],default='train')
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--prior-bank-output',required=True)
    p.add_argument('--prior-router-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--new-band',default='160:191')
    p.add_argument('--steps-per-bank',type=int,default=1500)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--router-steps',type=int,default=2000)
    p.add_argument('--router-probe-every',type=int,default=200)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--per-stratum',type=int,default=256)
    p.add_argument('--hidden',type=int,default=256)
    p.add_argument('--router-hidden',type=int,default=128)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--router-lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--min-accuracy',type=float,default=.90)
    p.add_argument('--min-new-bank-rate',type=float,default=.90)
    p.add_argument('--allowed-gap',type=float,default=.02)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--allow-candidate',action='store_true',
                   help='For diagnostic predictions using a non-promoted checkpoint')
    p.add_argument('--max-prior-new-bank-rate',type=float,default=.01)
    p.add_argument('--a',type=int,default=175)
    p.add_argument('--b',type=int,default=-159)
    return p


def main(argv=None):
    a=parser().parse_args(argv)
    return predict(a) if a.phase=='predict' else run(a)

if __name__=='__main__':main()
