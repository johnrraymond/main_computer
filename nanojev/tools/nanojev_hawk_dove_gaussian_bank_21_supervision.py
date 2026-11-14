#!/usr/bin/env python3
"""Train and audit all 21 [LHS, Gaussian, RHS] banks using oracle targets.

Research-only. No numerical decoder optimizer, overall accuracy gate, or dev
backpropagation. Each bank receives its own KL gradients and independent
clipping. Train and held-out Gaussian KL must decrease to establish learning.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import hashlib
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable as old
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_internal_curriculum_master as master
from tools import nanojev_hawk_dove_nested90_expected as mixture
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21

SCHEMA = 'nanojev.hawk-dove.gaussian-bank-21.direct-supervision.v1'
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_bank_21_direct_v1'
STAGES = old.STAGES
TIERS = old.TIERS
REGIONS = ('lhs','interior','rhs')


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def oracle_bank_terms(bank, features, target):
    """True distribution KL, not CE's irreducible Gaussian entropy.

    Use logits/log_softmax to avoid clipping the predicted probability gradient.
    Equalize regions PRESENT in a minibatch, not the 21 banks' geometry.
    """
    y = target.float()
    _, detail = bank(features)
    log_probs = F.log_softmax(detail['logits'].float(), dim=-1)
    truth = bank.supervised_distribution(y)
    # Zero * log(0) is exactly zero; guard only oracle log, not predicted logits.
    log_truth = truth.clamp_min(1e-30).log()
    kl = (truth * (log_truth - log_probs)).sum(dim=-1)
    ce = -(truth * log_probs).sum(dim=-1)
    entropy = -(truth * log_truth).sum(dim=-1)
    group = torch.where(y < -bank.radius, 0,
                        torch.where(y > bank.radius, 2, 1))
    available = [kl[group == i].mean() for i in range(3) if bool((group == i).any())]
    objective = torch.stack(available).mean()
    return objective, {'kl': kl, 'cross_entropy': ce, 'target_entropy': entropy,
                       'region': group, 'probabilities': detail['probabilities'],
                       'oracle': truth, 'logits': detail['logits']}


def bank_only_step(system, batch, optimizer, *, clip_norm=1.0):
    """Dedicated oracle updates; no primary-numeric gradient cap/deadlock."""
    system.train()
    system.cascade.stage2.eval()
    optimizer.zero_grad(set_to_none=True)
    with torch.no_grad():
        _, _, features = system.cascade.bank_input(batch)
    per_bank = [oracle_bank_terms(system.cascade.banks[str(k)], features, batch['target'])[0]
                for k in STAGES]
    # Sum of 21 independently normalized objectives: gradients are NOT /21.
    total = torch.stack(per_bank).sum()
    if not bool(torch.isfinite(total)):
        raise FloatingPointError('BANK_SUPERVISION_NONFINITE_LOSS')
    total.backward()
    per_norm, clipped, nonzero = [], [], []
    for k in STAGES:
        bank = system.cascade.banks[str(k)]
        params = list(bank.parameters())
        norm = float(torch.nn.utils.clip_grad_norm_(params, clip_norm,
                                                   error_if_nonfinite=True))
        if not math.isfinite(norm):
            raise FloatingPointError('BANK_SUPERVISION_NONFINITE_GRADIENT')
        per_norm.append(norm)
        clipped.append(int(norm > clip_norm))
        nonzero.append(int(norm > 1e-12))
    optimizer.step()
    for k in STAGES:
        if not all(bool(torch.isfinite(p).all()) for p in system.cascade.banks[str(k)].parameters()):
            raise FloatingPointError('BANK_SUPERVISION_NONFINITE_WEIGHT')
    return {'optimizer_bank_kl_sum':float(total.detach()),
            'mean_minibatch_balanced_kl':float(total.detach())/len(STAGES),
            'nonzero_gradient_banks':sum(nonzero),
            'banks_clipped':sum(clipped),
            'gradient_l2_by_bank':per_norm,
            'batch_region_support':{str(k):[int(((batch['target'] < -system.cascade.banks[str(k)].radius) if r==0 else
                  ((batch['target'].abs()<=system.cascade.banks[str(k)].radius) if r==1 else
                   (batch['target']>system.cascade.banks[str(k)].radius))).sum())
                  for r in range(3)] for k in STAGES}}


@torch.no_grad()
def _split_bank_probe(system, split, initial):
    """Measurement only: TRAIN and held-out DEV never alter weights."""
    system.eval()
    accum = {k:{'sum_kl':0.,'sum_ce':0.,'sum_entropy':0.,'total':0,
                'region':{name:{'n':0,'kl_sum':0.,'ce_sum':0.,'correct':0,
                    'interior_mass_sum':0.,'center_error_bins_sum':0.,'shape_kl_sum':0.,'oracle_center_span':[],
                    'center_values_by_tier':{}} for name in REGIONS}}
             for k in STAGES}
    for tier in TIERS:
        data = split[tier]
        _,_,features=system.cascade.bank_input(data)
        y=data['target'].float()
        for k in STAGES:
            bank=system.cascade.banks[str(k)]
            _,p=oracle_bank_terms(bank,features,y)
            a=accum[k]
            a['sum_kl']+=float(p['kl'].sum());a['sum_ce']+=float(p['cross_entropy'].sum())
            a['sum_entropy']+=float(p['target_entropy'].sum());a['total']+=len(y)
            class_pred=torch.stack((p['probabilities'][:,0],
                                    p['probabilities'][:,1:-1].sum(-1),
                                    p['probabilities'][:,-1]),-1).argmax(-1)
            for region,name in enumerate(REGIONS):
                which=p['region']==region
                n=int(which.sum())
                if not n:continue
                entry=a['region'][name]
                entry['n']+=n
                entry['kl_sum']+=float(p['kl'][which].sum())
                entry['ce_sum']+=float(p['cross_entropy'][which].sum())
                entry['correct']+=int((class_pred[which]==region).sum())
                if name=='interior':
                    probs=p['probabilities'][which,1:-1]
                    mass=probs.sum(-1)
                    centers=bank.centers.float()
                    predicted=(probs/(mass[:,None].clamp_min(1e-20)) @ centers)
                    oracle=p['oracle'][which,1:-1] @ centers
                    entry['interior_mass_sum']+=float(mass.sum())
                    entry['center_error_bins_sum']+=float(((predicted-oracle).abs()/bank.bin_spacing).sum())
                    oracle_gaussian=p['oracle'][which,1:-1]
                    conditional=(probs/mass[:,None].clamp_min(1e-30)).clamp_min(1e-30)
                    shape_kl=(oracle_gaussian*(oracle_gaussian.clamp_min(1e-30).log() - conditional.log())).sum(-1)
                    entry['shape_kl_sum']+=float(shape_kl.sum())
                    # Only compare sorted values within a tier; prompt tiers are different.
                    entry['center_values_by_tier'][tier]=(oracle.cpu().tolist(), predicted.cpu().tolist())
    rows=[]
    for j,k in enumerate(STAGES):
        a=accum[k];bank=system.cascade.banks[str(k)]
        n=a['total'];region={}
        present=[]
        for name in REGIONS:
            b=a['region'][name];count=b['n']
            if count:present.append(b['kl_sum']/count)
            row={'n':count,'mean_kl':b['kl_sum']/count if count else None,
                 'mean_cross_entropy':b['ce_sum']/count if count else None,
                 'region_class_accuracy':b['correct']/count if count else None}
            if name=='interior':
                row['mean_interior_mass']=b['interior_mass_sum']/count if count else None
                row['conditional_gaussian_shape_kl']=b['shape_kl_sum']/count if count else None
                row['mean_oracle_center_error_bins']=b['center_error_bins_sum']/count if count else None
                comparable=violations=0
                for tier,(truth,decoded) in b['center_values_by_tier'].items():
                    ordered=sorted(zip(truth,decoded))
                    for (t1,d1),(t2,d2) in zip(ordered,ordered[1:]):
                        # Ignore unresolvable pairs: the oracle itself is flat.
                        if t2-t1 > bank.bin_spacing * .1:
                            comparable+=1;violations+=int(d2<d1)
                row['ordered_oracle_pairs']=comparable
                row['center_order_violation_fraction']=violations/comparable if comparable else None
            region[name]=row
        before=initial[str(k)]
        drift=0.
        for name,value in bank.state_dict().items():
            if torch.is_floating_point(value):
                drift+=float((value.detach().cpu().float()-before[name].cpu().float()).square().sum())
        rows.append({'bank':j+1,'stage':k,'radius':bank.radius,'n':n,
                     'mean_kl':a['sum_kl']/n if n else None,
                     'balanced_region_kl':sum(present)/len(present) if present else None,
                     'mean_cross_entropy':a['sum_ce']/n if n else None,
                     'oracle_entropy_floor':a['sum_entropy']/n if n else None,
                     'weight_change_l2':math.sqrt(drift),'regions':region,
                     'interior_supported':region['interior']['n']>0,
                     'lhs_supported':region['lhs']['n']>0,
                     'rhs_supported':region['rhs']['n']>0})
    overall=sum(r['balanced_region_kl'] for r in rows)/len(rows)
    return {'balanced_mean_bank_kl':overall,
            'ordinary_mean_bank_kl':sum(r['mean_kl'] for r in rows)/len(rows),
            'regions_with_training_support':sum(sum(r[key+'_supported'] for key in REGIONS) for r in rows),
            'banks_with_interior_support':sum(r['interior_supported'] for r in rows),
            'banks_with_lhs_support':sum(r['lhs_supported'] for r in rows),
            'banks_with_rhs_support':sum(r['rhs_supported'] for r in rows),
            'per_bank':rows}


def _bank_states(system):
    return {str(k):{name:t.detach().cpu().clone() for name,t in
                     system.cascade.banks[str(k)].state_dict().items()} for k in STAGES}


def _state_file_sha(path):
    return hashlib.sha256(master.checked_file(path)).hexdigest()


def _serialize(value):
    output=io.BytesIO();torch.save(value,output);return output.getvalue()


def _prep(args, out):
    info,train_rows,dev_rows=old.build_mixture(args)
    if args.learnability_train_per_tier!=0:
        raise ValueError('BANK_SUPERVISION_NO_DOWNSAMPLING')
    names=('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt',
           'bank_initial.pt','config.json','latest.pt')
    present=[(out/n).exists() for n in names]
    if any(present) and not all(present):
        raise ValueError('BANK_SUPERVISION_PARTIAL_RUN_STATE')
    resume=all(present)
    first,original,donor,_=prior.check_parent(args)
    if resume:
        for name,raw in zip(names[:3],(first,original,donor)):
            if old.sha(master.checked_file(out/name))!=old.sha(raw):
                raise ValueError('BANK_SUPERVISION_PARENT_SNAPSHOT_DRIFT')
    first_ck=master.load_state(first)
    metadata={**first_ck['identity'],'step':first_ck['step']}
    initial,bank_sha=old.verify_bank_init(args,old.sha(first),old.sha(original),old.sha(donor))
    decoder_initial,decoder_sha=old.verify_decoder_init(args,old.sha(first),old.sha(original),old.sha(donor),bank_sha)
    args._decoder_init_sha256=decoder_sha
    ident=old.identity(args,first,original,donor,metadata,bank_sha,initial['schema'] if initial else None)
    ident.update({'schema':SCHEMA,'training':'21_bank_gaussian_kl_only_no_decoder',
                  'optimizer':'independent_per_bank_gradient_clip_adamw',
                  'head':'frozen_unused_numerical_decoder',
                  'experiment_arm':'bank-only',
                  'bank_clip_norm':args.bank_clip_norm,
                  'target':'oracle_lhs_gaussian_rhs_region_balanced_kl',
                  'split_name':'focus_1_to_1000_train_and_new_heldout_dev'})
    if resume:
        previous=json.loads((out/'config.json').read_text('utf8'))
        ident['initial_bank_file_sha256']=previous.get('initial_bank_file_sha256')
        if not isinstance(ident['initial_bank_file_sha256'], str):
            raise ValueError('BANK_SUPERVISION_MISSING_INIT_HASH')
        if previous!=ident:raise ValueError('BANK_SUPERVISION_RESUME_IDENTITY_DRIFT')
        saved=master.load_state(master.checked_file(out/'latest.pt'))
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('BANK_SUPERVISION_INVALID_CHECKPOINT')
    else:saved=None
    return info,train_rows,dev_rows,(first,original,donor),ident,initial,decoder_initial,saved


def run(args):
    if args.experiment_arm!='joint':
        raise ValueError('BANK_SUPERVISION_ARM_MUST_BE_JOINT_BUT_DECODER_IS_FROZEN')
    if not _finite(args.bank_clip_norm) or args.bank_clip_norm<=0:
        raise ValueError('BANK_SUPERVISION_INVALID_CLIP')
    if args.test_only:
        raise ValueError('BANK_SUPERVISION_NO_TEST_ACCESS')
    if args.mixture_audit_only:
        return old.audit_only(args)
    out=old.validate(args)
    info,train_rows,dev_rows,sources,ident,initial,decoder_initial,saved=_prep(args,out)
    start=int(saved['step']) if saved else 0
    if start+args.additional_steps>args.max_steps:
        raise ValueError('BANK_SUPERVISION_STEPS_EXCEED_MAX')
    first,original,donor=sources
    fork.emit('bank_supervision_prepare',start_step=start,steps=args.additional_steps,
              banks=len(STAGES),objective='direct_region_balanced_KL',decoder_trained=False)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    orig_ck=master.load_state(original);donor_ck=master.load_state(donor)
    original_args=argparse.Namespace(arm=ident['original_bridge_arm'])
    decoder,bridge=fine.init_frozen(original_args,orig_ck,donor_ck,model)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-bank-supervision-v1',{
       'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
       'teacher_sha256':ident['teacher_sha256'],
       'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    train=mixture.load_features(model,decoder,bridge,cache,args,train_rows,'train')
    dev=mixture.load_features(model,decoder,bridge,cache,args,dev_rows,'dev')
    # Oracle instance identity is disjoint across these splits.
    if {str(r['instance']) for t in TIERS for r in train_rows[t]} & \
       {str(r['instance']) for t in TIERS for r in dev_rows[t]}:
        raise ValueError('BANK_SUPERVISION_TRAIN_DEV_LEAKAGE')
    system=old.construct_system(train[TIERS[0]]['features'].shape[-1],first,model['device'],args)
    system.head.eval().requires_grad_(False)
    if saved:
        initialization=master.load_state(master.checked_file(out/'bank_initial.pt'))
        if initialization.get('schema')!=SCHEMA+'.initial.v1':
            raise ValueError('BANK_SUPERVISION_BAD_INIT_FILE')
        if old.sha(master.checked_file(out/'bank_initial.pt')) != ident['initial_bank_file_sha256']:
            raise ValueError('BANK_SUPERVISION_INIT_FILE_DRIFT')
        initial_states=initialization['bank_states']
        for k in STAGES:system.cascade.banks[str(k)].load_state_dict(saved['bank_states'][str(k)])
    else:
        if decoder_initial is not None:
            stages=STAGES if decoder_initial['schema']==old21.SCHEMA else old.LEGACY_STAGES
            for k in stages:system.cascade.banks[str(k)].load_state_dict(decoder_initial['bank_states'][str(k)])
        elif initial is not None:
            for k in old.LEGACY_STAGES:
                system.cascade.banks[str(k)].load_state_dict(initial['bank_states'][str(k)])
        initial_states=_bank_states(system)
        init_bytes=_serialize({'schema':SCHEMA+'.initial.v1','bank_states':initial_states})
        ident['initial_bank_file_sha256']=old.sha(init_bytes)
    banks=[system.cascade.banks[str(k)] for k in STAGES]
    params=[p for bank in banks for p in bank.parameters()]
    opt=torch.optim.AdamW(params,lr=args.lr)
    if saved:
        opt.load_state_dict(saved['optimizer'])
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:
            torch.cuda.set_rng_state_all(saved['rng_cuda'])
    if not saved:
        out.mkdir(parents=True,exist_ok=True)
        for name,raw in zip(('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt'),sources):
            store.atomic_bytes(out/name,raw)
        store.atomic_bytes(out/'bank_initial.pt',init_bytes)
        store.atom_json(out/'config.json',ident)
        store.atom_json(out/'focused_curriculum.json',info)
    sampler=__import__('tools.nanojev_hawk_dove_focus_1k_curriculum',fromlist=['Sampler']).Sampler(train)
    def checkpoint(step,chunk=None):
        data={'schema':SCHEMA,'identity':ident,'step':step,'bank_states':_bank_states(system),
              'optimizer':opt.state_dict(),'rng_cpu':torch.get_rng_state(),
              'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}
        store.atom_torch(out/'latest.pt',data)
        tr=_split_bank_probe(system,train,initial_states)
        dv=_split_bank_probe(system,dev,initial_states)
        result={'schema':SCHEMA+'.probe.v1','step':step,'train':tr,'held_out_dev':dv,
                'chunk':chunk,'bank_count':len(STAGES),'decoder_trained':False,
                'test_split_read':False,
                'bank_goal':'held_out_region_balanced_KL_to_zero_and_interior_shape_alignment',
                'checkpoint_path':str(out/'latest.pt')}
        store.atom_json(out/f'probe-step-{step:07d}.json',result)
        research=out/'research_best_bank_kl.pt';meta=out/'research_best_bank_kl.json'
        if research.exists()!=meta.exists():
            raise ValueError('BANK_SUPERVISION_BEST_PAIR_BROKEN')
        previous=json.loads(meta.read_text('utf8')) if meta.exists() else None
        if previous is not None and old.sha(master.checked_file(research))!=previous.get('checkpoint_sha256'):
            raise ValueError('BANK_SUPERVISION_BEST_CHECKPOINT_INTEGRITY_DRIFT')
        if previous is None or dv['balanced_mean_bank_kl']<previous['held_out_balanced_mean_bank_kl']:
            buf=_serialize(data);store.atomic_bytes(research,buf)
            store.atom_json(meta,{'schema':SCHEMA+'.research-only.v1','step':step,
                'held_out_balanced_mean_bank_kl':dv['balanced_mean_bank_kl'],
                'checkpoint_sha256':old.sha(buf),'deployment_eligible':False})
        # Logging only bank loss/shape; do not show a final-answer accuracy rollup.
        fork.emit('bank_supervision_probe',step=step,
                  train_bank_kl=tr['balanced_mean_bank_kl'],
                  held_out_bank_kl=dv['balanced_mean_bank_kl'],
                  train_interior_supported=tr['banks_with_interior_support'],
                  dev_interior_supported=dv['banks_with_interior_support'],
                  train_lhs_supported=tr['banks_with_lhs_support'],
                  dev_lhs_supported=dv['banks_with_lhs_support'],
                  all_bank_weights_changed=sum(p['weight_change_l2']>0 for p in tr['per_bank']),
                  per_bank=[{'bank':b['bank'],'radius':b['radius'],
                             'train_kl':a['balanced_region_kl'],
                             'dev_kl':b['balanced_region_kl'],
                             'interior_n':b['regions']['interior']['n'],
                             'interior_center_error_bins':b['regions']['interior']['mean_oracle_center_error_bins'],
                             'conditional_gaussian_shape_kl':b['regions']['interior']['conditional_gaussian_shape_kl'],
                             'delta_l2':a['weight_change_l2']} for a,b in zip(tr['per_bank'],dv['per_bank'])],
                  clip_banks_mean=(chunk.get('mean_banks_clipped') if chunk else None))
        return result
    if not saved:checkpoint(0)
    summed={'optimizer_bank_kl_sum':0.,'mean_minibatch_balanced_kl':0.,
            'nonzero_gradient_banks':0.,'banks_clipped':0.}
    n=0
    for step in range(start+1,start+args.additional_steps+1):
        batch=sampler.draw(args.seed,step,args.views)
        stats=bank_only_step(system,batch,opt,clip_norm=args.bank_clip_norm)
        for key in summed:summed[key]+=stats[key]
        n+=1
        if step%args.chunk_steps==0 or step==start+args.additional_steps:
            checkpoint(step,{'mean_optimizer_bank_kl_sum':summed['optimizer_bank_kl_sum']/n,
                'mean_train_minibatch_bank_kl':summed['mean_minibatch_balanced_kl']/n,
                'mean_banks_with_nonzero_gradient':summed['nonzero_gradient_banks']/n,
                'mean_banks_clipped':summed['banks_clipped']/n})
            summed={key:0. for key in summed};n=0
    fork.emit('bank_supervision_finished',step=start+args.additional_steps,
              latest=str(out/'latest.pt'),decoder_trained=False,test_split_read=False)


def parser():
    p=old.parser()
    p.set_defaults(output=DEFAULT_OUTPUT,experiment_arm='joint',lr=0.0001,
                   additional_steps=1000,chunk_steps=100,retention_fraction=0.,
                   mixture_train_per_tier=900,mixture_new_dev_per_tier=180)
    p.add_argument('--bank-clip-norm',type=float,default=1.,
                   help='Independent per-bank gradient clipping, not 21-bank global clipping')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return run(args)

if __name__=='__main__':main()
