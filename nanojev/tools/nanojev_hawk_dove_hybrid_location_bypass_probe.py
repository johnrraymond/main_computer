#!/usr/bin/env python3
"""Read-only DEV paired diagnostic: pre-Gaussian X/Y versus Gaussian reconstruction.

Load immutable representation-lab BEST attention checkpoints and the exact
oracle-blind DEV split. Decode the *same* model locations two ways, with direct
true-location and density true-location controls. No retraining, TEST access,
or mutation of any model/checkpoint. Numerical inputs are the same four visible
count-payoff products as the representation lab; true X/Y are diagnostic labels
only, never inputs to a trained model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import torch
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_hybrid_teacher_probe as prior_probe
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind

SCHEMA = 'nanojev.hawk-dove.hybrid-location-bypass-diagnostic.v1'
ARMS = ('attention_fixed','attention_frozen','attention_joint')


def location_to_numeric(locations, domain):
    """Undo normalized sigmoid coordinates without Gaussian bank discretization."""
    if locations.ndim != 2 or locations.shape[1] != 2:
        raise ValueError('LOCATION_PROBE_BAD_LOCATION_SHAPE')
    return torch.stack([locations[:,i]*(domain[i][1]-domain[i][0])+domain[i][0]
                        for i in range(2)],dim=1)


def numeric_to_location(numeric, domain):
    return torch.stack([(numeric[:,i]-domain[i][0])/(domain[i][1]-domain[i][0])
                        for i in range(2)],dim=1)


def paired_metrics(predictions, data, tolerance):
    """Evaluate exact same rows; an instance passes only if both masks do."""
    results = {key:lab.strict_metrics(val,data,tolerance) for key,val in predictions.items()}
    truth = data['numeric'].detach().cpu()
    hits={name:((pred.detach().cpu()-truth).abs()<=tolerance*truth.abs()).all(dim=1).tolist()
          for name,pred in predictions.items()}
    group={}
    for i,row in enumerate(data['rows']):
        key=(row['tier'],row['instance'])
        group.setdefault(key,{name:[] for name in predictions})
        for name in predictions:group[key][name].append(bool(hits[name][i]))
    def pair(a,b):
        rescued=sum(all(v[a]) and not all(v[b]) for v in group.values())
        regressed=sum(all(v[b]) and not all(v[a]) for v in group.values())
        by_tier={}
        for tier in blind.TIERS:
            keys=[v for (t,_),v in group.items() if t==tier]
            if not keys:raise RuntimeError('LOCATION_PROBE_EMPTY_TIER')
            good=sum(all(v[a]) and not all(v[b]) for v in keys)
            bad=sum(all(v[b]) and not all(v[a]) for v in keys)
            by_tier[tier]={'rescued':good,'regressed':bad,'net':good-bad,'instances':len(keys)}
        return {'rescued':rescued,'regressed':regressed,'net':rescued-regressed,'by_tier':by_tier}
    comparison={
        'direct_vs_gaussian':pair('direct','gaussian'),
        'oracle_direct_vs_oracle_gaussian':pair('oracle_direct','oracle_gaussian'),
        'gaussian_vs_true_numeric':pair('oracle_numeric','gaussian'),
    }
    return results, comparison


def _error_slices(predictions,data):
    """Expose cancellation/zero/near-zero error that all-mask scores obscure."""
    truth=data['numeric'].detach().cpu()
    scale=float(max(truth.abs().max().item(),1.))
    zero=truth==0
    near=(truth.abs()>0)&(truth.abs()<=.001*scale)
    nonzero=truth!=0
    cuts={'exact_zero':zero,'near_zero_nonzero':near,'other_nonzero':nonzero & ~near}
    out={'near_zero_bound_in_output_units':.001*scale}
    for k,pred in predictions.items():
        errors=(pred.detach().cpu()-truth).abs()
        d={}
        for s,mask in cuts.items():
            vals=errors[mask]
            d[s]={'coordinates':int(mask.sum()),'mean_abs_error':float(vals.mean()) if len(vals) else None,
                  'max_abs_error':float(vals.max()) if len(vals) else None,
                  'exact_hits':int((vals==0).sum())}
        out[k]=d
    return out


@torch.no_grad()
def evaluate_arm(arm,split,domain,batch_size,tolerance):
    """All four paths share the same oracle-blind rows and learned locations."""
    arm.eval()
    outputs={k:[] for k in ('direct','gaussian','oracle_direct','oracle_gaussian','oracle_numeric')}
    geometry=[]
    for start in range(0,len(split['rows']),batch_size):
        end=min(start+batch_size,len(split['rows']))
        x=split['features'][start:end]
        y=split['numeric'][start:end]
        density,location=arm(x,return_location=True)
        bank=gauss.numeric_decode(density,domain)
        # Verify we compare the precise same frozen model locations, not a
        # separately trained or otherwise reparameterized output head.
        direct=location_to_numeric(location,domain)
        oracle_location=numeric_to_location(y,domain)
        # Source numeric targets are within the Gaussian model's domain.
        if bool(((oracle_location<0)|(oracle_location>1)).any()):
            raise RuntimeError('LOCATION_PROBE_ORACLE_LOCATION_OUT_OF_RANGE')
        truth_direct=location_to_numeric(oracle_location,domain)
        truth_bank=gauss.numeric_decode(arm.density_from_location(oracle_location),domain)
        for key,value in [('direct',direct),('gaussian',bank),
                          ('oracle_direct',truth_direct),('oracle_gaussian',truth_bank),
                          ('oracle_numeric',y)]:
            outputs[key].append(value.detach().cpu())
        geometry.append((bank-direct).detach().abs().cpu())
    preds={key:torch.cat(parts) for key,parts in outputs.items()}
    metrics,paired=paired_metrics(preds,split,tolerance)
    delta=torch.cat(geometry)
    return {'paths':metrics,'paired':paired,
            'geometry_shift':{'mean_abs_xy':float(delta.mean()),
                              'max_abs_xy':float(delta.max()),
                              'rows_changed_above_1':float((delta>1).any(dim=1).float().mean()),
                              'rows_changed_above_100':float((delta>100).any(dim=1).float().mean())},
            'error_slices':_error_slices(preds,split)}


def _source_and_lab(source,run_dir,lab_dir,device):
    model,splits,domain,config,_=prior_probe.load_experiment(source,run_dir,device)
    identity=json.loads((lab_dir/'config.json').read_text(encoding='utf-8'))
    if identity.get('schema')!=lab.SCHEMA or identity.get('source_sha256')!=prior_probe.sha256(source) or \
       identity.get('teacher_sha256')!=prior_probe.sha256(run_dir/'teacher_best.pt') or \
       identity.get('teacher_schema')!=config['schema'] or \
       identity.get('source_identity')!=config['source_identity'] or \
       identity.get('matrix_hashes')!=config['matrix_hashes'] or \
       identity.get('input_policy')!='four visible count-payoff products; no anchor or oracle answer at inference':
        raise RuntimeError('LOCATION_PROBE_LAB_IDENTITY_DRIFT')
    if not identity.get('train_only_supervision') or not identity.get('DEV_selection'):
        raise RuntimeError('LOCATION_PROBE_LAB_SPLIT_POLICY_DRIFT')
    return model,splits,domain,identity


def _load_arm(key,model,lab_dir,identity,device):
    path=lab_dir/key/'best.pt'
    best=torch.load(path,map_location='cpu',weights_only=True)
    if (best.get('schema')!=lab.SCHEMA+'.best' or best.get('identity')!=identity or
        best.get('key')!=key or not isinstance(best.get('step'),int)):
        raise RuntimeError('LOCATION_PROBE_BEST_CHECKPOINT_IDENTITY_DRIFT:'+key)
    result_path=lab_dir/key/'done.json'
    completed=json.loads(result_path.read_text(encoding='utf-8'))
    if completed.get('schema')!=lab.SCHEMA or completed.get('arm')!=key or \
       completed.get('best_step')!=best['step'] or completed.get('DEV')!=best.get('DEV'):
        raise RuntimeError('LOCATION_PROBE_BEST_SELECTION_DRIFT:'+key)
    if not completed.get('train_updates') or best['step']>completed['train_updates']:
        raise RuntimeError('LOCATION_PROBE_UNFINISHED_ARM:'+key)
    arms=lab.make_arms(model,identity['options']['heads'],identity['options']['layers'])
    arm=arms[key]
    arm.load_state_dict(best['model'],strict=True)
    return arm.to(device).eval(),best['step'],completed['DEV']


def _replay_matches(observed, saved):
    # Numeric reductions depend slightly on batched GEMM order. Require exact
    # strict case scores, counts, and zero behavior, while tolerating a tiny
    # floating-point change in the two continuous error summaries.
    for key in ('min_tier_joint5','per_tier','exact_zero_coordinates',
                'exact_zero_hits','presentations','independent_instances'):
        if observed.get(key)!=saved.get(key):return False
    for key in ('mean_abs_xy_error','max_abs_xy_error'):
        if not math.isclose(observed[key],saved[key],rel_tol=1e-6,abs_tol=.5):
            return False
    return True


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('LOCATION_PROBE_CUDA_UNAVAILABLE')
    if min(args.batch_size,args.cpu_threads)<1 or not math.isfinite(args.tolerance) or not 0<args.tolerance<1:
        raise ValueError('LOCATION_PROBE_INVALID_ARGUMENTS')
    source=Path(args.reference_checkpoint).expanduser().resolve()
    run_dir=Path(args.run_dir).expanduser().resolve()
    lab_dir=Path(args.lab_dir).expanduser().resolve()
    output=Path(args.output).expanduser().resolve() if args.output else lab_dir/'location_bypass_probe_v1'
    if output.exists():raise FileExistsError('LOCATION_PROBE_REFUSES_OVERWRITE:'+str(output))
    if output==source or output==run_dir or output==lab_dir or output in source.parents:
        raise ValueError('LOCATION_PROBE_INVALID_OUTPUT')
    torch.set_num_threads(args.cpu_threads)
    inputs=[source,run_dir/'teacher_best.pt',run_dir/'config.json',lab_dir/'config.json']
    for key in ARMS:
        inputs.extend((lab_dir/key/'best.pt',lab_dir/key/'done.json'))
    hashes_before={str(p):prior_probe.sha256(p) for p in inputs}
    original,splits,domain,identity=_source_and_lab(source,run_dir,lab_dir,args.device)
    device=torch.device(args.device)
    reports={}
    for key in ARMS:
        arm,step,saved_dev=_load_arm(key,original,lab_dir,identity,device)
        outcome=evaluate_arm(arm,splits['dev'],domain,args.batch_size,args.tolerance)
        # Read-only replay of the original lab's density evaluation is the
        # necessary consistency test, not an asserted equal score.
        observed=outcome['paths']['gaussian']
        if not _replay_matches(observed,saved_dev):
            raise RuntimeError('LOCATION_PROBE_GAUSSIAN_REPLAY_DRIFT:'+key)
        reports[key]={'best_step':step,'DEV':outcome}
        v=outcome['paths']
        print(f'LOCATION_PROBE_ARM={key} step={step} direct={v["direct"]["min_tier_joint5"]:.6f} '
              f'gaussian={v["gaussian"]["min_tier_joint5"]:.6f} '
              f'oracle_direct={v["oracle_direct"]["min_tier_joint5"]:.6f} '
              f'oracle_gaussian={v["oracle_gaussian"]["min_tier_joint5"]:.6f}',flush=True)
    hashes_after={str(p):prior_probe.sha256(p) for p in inputs}
    if hashes_after!=hashes_before:raise RuntimeError('LOCATION_PROBE_SOURCE_MUTATED')
    out={'schema':SCHEMA,'source_verified':True,'checkpoint_immutable':True,
         'evaluated_split':'DEV','test_evaluated':False,
         'shared_examples':len(splits['dev']['rows']),
         'source_hashes':hashes_before,'lab_identity':identity,
         'strict_metric':'all-masks joint X/Y within 5%; exact zeros require exact equality',
         'interpretation_policy':'paired same checkpoint and same DEV rows; raw location bypass is diagnostic only, not retrained',
         'arms':reports}
    output.mkdir(parents=True,exist_ok=False)
    gauss._atomic_json(output/'report.json',out)
    print('LOCATION_PROBE_INPUTS_VERIFIED=TRUE READ_ONLY=TRUE TEST_UNTOUCHED=TRUE',flush=True)
    print('LOCATION_PROBE_REPORT='+str(output/'report.json'),flush=True)
    return out


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',required=True,type=Path)
    p.add_argument('--run-dir',required=True,type=Path,help='Failed HybridAgent v4.2 teacher run')
    p.add_argument('--lab-dir',required=True,type=Path,help='Completed representation lab v1 output directory')
    p.add_argument('--output',type=Path,default=None,help='New report-only output directory')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--tolerance',type=float,default=.05)
    return p


def main(argv=None):return run(parser().parse_args(argv))

if __name__=='__main__':main()
