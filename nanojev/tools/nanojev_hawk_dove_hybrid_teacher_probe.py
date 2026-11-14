#!/usr/bin/env python3
"""Read-only v4.2 diagnostic: do typed arithmetic thoughts influence Gaussian X/Y?

Loads the original Gaussian source and a failed teacher-bootstrap checkpoint.
Never trains, changes checkpoint files, or uses TEST for selecting anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import torch
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4

SCHEMA = 'nanojev.hawk-dove.hybrid-teacher-diagnostic.v1'
CONDITIONS = ('reference', 'student_no_thought', 'correct', 'shuffled', 'zero_tokens', 'thought_only')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_experiment(source_path, run_dir, device='cpu', checkpoint_name='teacher_best.pt'):
    """Verify source hash, model schema, train config and matrix identity before inference."""
    source_path = Path(source_path).expanduser().resolve()
    run_dir = Path(run_dir).expanduser().resolve()
    source_hash = sha256(source_path)
    config = json.loads((run_dir / 'config.json').read_text(encoding='utf-8'))
    saved = torch.load(run_dir / checkpoint_name, map_location='cpu', weights_only=True)
    if config.get('schema') != v4.SCHEMA or config.get('source_sha256') != source_hash:
        raise RuntimeError('HYBRID_TEACHER_PROBE_SOURCE_OR_SCHEMA_DRIFT')
    if saved.get('schema') != v4.SCHEMA + '.teacher-best' or saved.get('identity') != config:
        raise RuntimeError('HYBRID_TEACHER_PROBE_CHECKPOINT_IDENTITY_DRIFT')
    original = torch.load(source_path, map_location='cpu', weights_only=True)
    if original.get('schema') != blind.SCHEMA or original.get('identity') != config['source_identity']:
        raise RuntimeError('HYBRID_TEACHER_PROBE_ORIGINAL_IDENTITY_DRIFT')
    ci = original['identity']
    splits, domain, manifest = blind._dataset(argparse.Namespace(**ci), torch.device(device))
    if manifest['matrix_hashes'] != config['matrix_hashes'] or manifest['matrix_hashes'] != ci['matrix_hashes']:
        raise RuntimeError('HYBRID_TEACHER_PROBE_SPLIT_DRIFT')
    if [list(d) for d in domain] != ci['domain']:
        raise RuntimeError('HYBRID_TEACHER_PROBE_DOMAIN_DRIFT')
    reference = blind.BlindDensityNetwork(ci['banks'], ci['grid_bins'], ci['seed'], ci['hidden'])
    reference.load_state_dict(original['model'], strict=True)
    reference = reference.to(device).eval()
    model = v4.ContextualGaussianHybridAgent(reference, config['heads'], config['layers']).to(device)
    v4._load_agent(model, saved['model'])
    model.eval()
    return model, splits, domain, config, saved


def oracle_product_audit(features, truth, payoff_bound):
    """Mathematical sufficiency check independent of learned encoder/decoder."""
    x64 = features[:, :6].to(torch.float64)
    products = torch.stack((x64[:, 0]*x64[:, 2], x64[:, 1]*x64[:, 3],
                            x64[:, 0]*x64[:, 4], x64[:, 1]*x64[:, 5]), dim=1)
    reconstructed = torch.stack((products[:, 0]+products[:, 1],
                                 products[:, 2]+products[:, 3]), dim=1) * (blind.TOTAL * payoff_bound)
    errors = (reconstructed-truth.double()).abs()
    zero = truth == 0
    return {'formula': 'X=(a*HH+b*HD), Y=(a*DH+b*DD); a,b and payoffs are normalized',
            'uses_only_six_visible_numeric_features': True,
            'max_absolute_roundoff': float(errors.max()),
            'mean_absolute_roundoff': float(errors.mean()),
            'max_absolute_roundoff_at_exact_zero_targets': float(errors[zero].max()) if bool(zero.any()) else None,
            'zero_target_coordinates': int(zero.sum()),
            'within_one_output_unit_fraction': float((errors <= 1.).all(dim=1).float().mean())}


def _diff_stats(a, b):
    delta = (a-b).abs()
    return {'mean_absolute_xy_change': float(delta.mean()),
            'max_absolute_xy_change': float(delta.max()),
            'fraction_rows_xy_changed_above_1': float((delta>1.).any(dim=1).float().mean()),
            'fraction_rows_xy_changed_above_100': float((delta>100.).any(dim=1).float().mean())}


def _hit(pred, truth, tolerance):
    return ((pred-truth).abs() <= tolerance*truth.abs()).all(dim=-1)


def paired_scores(predictions, data, tolerance):
    """Identical (tier,instance) paired masks under every condition."""
    truth = data['numeric'].detach().cpu()
    hits = {key:_hit(pred, truth, tolerance).tolist() for key,pred in predictions.items()}
    by_instance = defaultdict(lambda: defaultdict(list))
    matrix_for = {}
    for idx, row in enumerate(data['rows']):
        key = (row['tier'], row['instance'])
        matrix_for[key] = row['matrix_index']
        for name in predictions:
            by_instance[key][name].append(bool(hits[name][idx]))
    tier_out = {}
    for tier in blind.TIERS:
        keys = [key for key in by_instance if key[0] == tier]
        if not keys:
            raise RuntimeError('HYBRID_TEACHER_PROBE_EMPTY_TIER')
        success = {name:[all(by_instance[key][name]) for key in keys] for name in predictions}
        scores = {name: sum(success[name])/len(keys) for name in predictions}
        paired = {}
        for control in ('reference', 'student_no_thought', 'shuffled', 'zero_tokens'):
            win = sum(c and not b for c,b in zip(success['correct'], success[control]))
            lose = sum(b and not c for c,b in zip(success['correct'], success[control]))
            paired[control] = {'rescued':win,'regressed':lose,'net':win-lose,
                               'absolute_accuracy_gain':(win-lose)/len(keys)}
        tier_out[tier]={'instances':len(keys),'accuracy':scores,'correct_paired_against':paired}
    return {'metric':'strict_all_masks_joint_XY_within_5_percent',
            'tolerance':tolerance, 'tiers':tier_out,
            'min_tier_accuracy':{name:min(tier_out[t]['accuracy'][name] for t in blind.TIERS)
                                 for name in predictions}}


def predictions_and_path(model, data, domain, batch_size=128):
    """Evaluate same held-out rows, with donor assignment across entire DEV split."""
    features = data['features']
    with torch.no_grad():
        # Entire split is required so that shuffled donors are distinct numerical instances.
        all_tokens = torch.cat([model.teacher_tokens(chunk) for chunk in features.split(batch_size)])
        donors = v4.distinct_instance_thought_donors(features, all_tokens, data['rows'])
        shuffled = all_tokens.index_select(0, donors)
        if bool((all_tokens == shuffled).flatten(1).all(dim=1).any()):
            raise RuntimeError('HYBRID_TEACHER_PROBE_INVALID_SHUFFLE')
        preds = {key:[] for key in CONDITIONS}
        correct_shifts=[]; thought_norm=[]; update_norm=[]; anchor_norm=[]
        token_dist=[]; token_cos=[]
        for start in range(0, features.shape[0], batch_size):
            end = min(start+batch_size, features.shape[0]); x = features[start:end]
            seq, anchor = model._context(x)
            t = all_tokens[start:end]; s = shuffled[start:end]
            correctly = model.core(torch.cat((seq,t),dim=1))
            update = model.thought_update(correctly)
            dens = {
                'reference':model.reference(x),
                'student_no_thought':model.teacherless_density(x),
                'correct':model.density_from_hidden(anchor + update),
                'shuffled':model.density_from_hidden(anchor + model.thought_update(
                    model.core(torch.cat((seq,s),dim=1)))),
                'zero_tokens':model.density_from_hidden(anchor + model.thought_update(
                    model.core(torch.cat((seq,torch.zeros_like(t)),dim=1)))),
                'thought_only':model.density_from_hidden(update),
            }
            for key in preds: preds[key].append(gauss.numeric_decode(dens[key],domain).detach().cpu())
            thought_norm.append(correctly.norm(dim=1).cpu())
            update_norm.append(update.norm(dim=1).cpu())
            anchor_norm.append(anchor.norm(dim=1).cpu())
            correct_shifts.append((gauss.numeric_decode(dens['correct'],domain)-
                                   gauss.numeric_decode(dens['student_no_thought'],domain)).abs().cpu())
            token_dist.append((t-s).flatten(1).norm(dim=1).cpu())
            token_cos.append(torch.nn.functional.cosine_similarity(t.flatten(1),s.flatten(1),dim=1).cpu())
    predictions={key:torch.cat(value) for key,value in preds.items()}
    def summary(x):
        x=torch.cat(x).float()
        return {'mean':float(x.mean()),'median':float(x.median()),'max':float(x.max())}
    sizes = {'anchor_hidden_l2':summary(anchor_norm),'thought_hidden_l2':summary(thought_norm),
             'thought_update_l2':summary(update_norm),
             'teacher_token_difference_l2':summary(token_dist),
             'teacher_token_shuffled_cosine_similarity':summary(token_cos),
             'teacher_to_no_thought_xy_shift':summary(correct_shifts)}
    return predictions,sizes,{'rows':int(features.shape[0]), 'distinct_sequence_donors':int(features.shape[0]),
                               'paired_masks_receive_consistent_donor':True}


def gradient_diagnostic(model, data, domain, bins, sigma, max_rows=64):
    """Read-only: d(numerical loss)/d(input teacher tokens), readout and update."""
    x=data['features'][:max_rows]
    y=data['numeric'][:max_rows]
    if not len(x): raise ValueError('HYBRID_TEACHER_PROBE_EMPTY_GRADIENT_BATCH')
    with torch.enable_grad():
        tokens = model.teacher_tokens(x).detach().requires_grad_(True)
        seq, anchor=model._context(x)
        hidden=model.core(torch.cat((seq,tokens),dim=1))
        update=model.thought_update(hidden)
        density=model.density_from_hidden(anchor+update)
        loss=v4._numerical_loss(density,y,domain,bins,sigma)
        targets=[tokens,update,model.thought_update.weight,model.answer.weight]
        gradients=torch.autograd.grad(loss,targets,allow_unused=True)
    result={}
    for name, g in zip(('teacher_tokens','thought_update_output','thought_update_weight','numeric_readout_weight'),gradients):
        result[name]={'gradient_l2':float(g.detach().norm()) if g is not None else 0.,
                      'nonzero':bool(g is not None and g.detach().abs().max()>0)}
    result['loss_at_teacher_condition']=float(loss.detach())
    result['batch_rows']=int(len(x))
    return result



def linear_encoding_readout(model, train, dev, payoff_bound, batch_size=128,
                            max_train_rows=4096, ridge=.01, tolerance=.05):
    """Fit a separate *diagnostic* linear probe on TRAIN; never change model.

    Tests whether arithmetic information survives the learned typed embeddings.
    This is NOT a proposed inference head or a test of the causal-memory core.
    """
    device=train['features'].device
    n=train['features'].shape[0]
    if n<2: raise ValueError('HYBRID_TEACHER_PROBE_LINEAR_TOO_FEW_TRAIN')
    take=torch.linspace(0,n-1,min(n,max_train_rows),device=device).long()
    def collect(features,kind):
        result=[]
        with torch.no_grad():
            for chunk in features.split(batch_size):
                if kind=='raw_products': tensor=v4.stage_values(chunk)
                else: tensor=model.teacher_tokens(chunk).flatten(1)
                result.append(tensor.detach().cpu().to(torch.float64))
        return torch.cat(result)
    train_x=train['features'].index_select(0,take)
    y=train['numeric'].index_select(0,take).detach().cpu().to(torch.float64)
    scale=blind.TOTAL*payoff_bound
    output={}
    for kind in ('raw_products','typed_embeddings'):
        a=collect(train_x,kind)
        b=collect(dev['features'],kind)
        mean=a.mean(dim=0)
        sd=a.std(dim=0,unbiased=False).clamp_min(1.e-5)
        a=(a-mean)/sd
        b=(b-mean)/sd
        a=torch.cat((a,torch.ones(len(a),1,dtype=a.dtype)),dim=1)
        b=torch.cat((b,torch.ones(len(b),1,dtype=b.dtype)),dim=1)
        penalty=1.e-10 if kind=='raw_products' else ridge
        reg=torch.eye(a.shape[1],dtype=a.dtype)*penalty
        reg[-1,-1]=0.
        weights=torch.linalg.solve(a.T@a/len(a)+reg,
                                   a.T@(y/scale)/len(a))
        pred=(b@weights*scale).to(torch.float32)
        truth=dev['numeric'].detach().cpu()
        hits=_hit(pred,truth,tolerance).tolist()
        groups=defaultdict(list)
        for row,ok in zip(dev['rows'],hits):
            groups[(row['tier'],row['instance'])].append(bool(ok))
        tier_scores={tier:sum(all(oks) for (t,i),oks in groups.items() if t==tier)/
                     sum(t==tier for t,i in groups) for tier in blind.TIERS}
        output[kind]={'train_rows':int(len(a)),
                      'feature_columns':int(a.shape[1]-1),
                      'ridge':penalty,
                      'DEV_min_tier_all_masks_joint5':min(tier_scores.values()),
                      'DEV_mean_absolute_xy_error':float((pred-truth).abs().mean()),
                      'by_tier':tier_scores}
    return output


def analyze(model, data, domain, payoff_bound, target_sigma_bins, batch_size=128,
            grad_rows=64, tolerance=.05, train=None):
    model.eval()
    oracle=oracle_product_audit(data['features'],data['numeric'],payoff_bound)
    predictions,path,shuffle=predictions_and_path(model,data,domain,batch_size)
    paired=paired_scores(predictions,data,tolerance)
    grad=gradient_diagnostic(model,data,domain,model.bins,target_sigma_bins,grad_rows)
    diff={f'correct_vs_{condition}':_diff_stats(predictions['correct'],predictions[condition])
          for condition in ('reference','student_no_thought','shuffled','zero_tokens')}
    truth=data['numeric'].detach().cpu()
    failures={}
    for label,mask in [('exact_zero',(truth==0).any(dim=1)),
                       ('near_zero_nonzero',((truth.abs()>0)&(truth.abs()<10000)).any(dim=1)),
                       ('ordinary', (truth.abs()>=10000).all(dim=1))]:
        if bool(mask.any()):
            failures[label]={'rows':int(mask.sum()),
                             'joint_correct':float(_hit(predictions['correct'][mask],truth[mask],tolerance).float().mean()),
                             'joint_reference':float(_hit(predictions['reference'][mask],truth[mask],tolerance).float().mean()),
                             'joint_shuffled':float(_hit(predictions['shuffled'][mask],truth[mask],tolerance).float().mean())}
    linear=linear_encoding_readout(model,train,data,payoff_bound,batch_size,tolerance=tolerance) if train is not None else None
    return {'schema':SCHEMA, 'oracle_product_reconstruction':oracle,
            'paired_dev_certification':paired,'thought_path_magnitudes':path,
            'shuffled_control':shuffle,'prediction_changes':diff,
            'backprop_sensitivity':grad,'failure_slices':failures,
            'TRAIN_fitted_diagnostic_linear_readout_DEV':linear,
            'interpretation':classify(oracle,paired,diff,grad)}


def classify(oracle,paired,diff,grad):
    """Evidence-based hypotheses, not automatic claims of a causal mechanism."""
    possible=[]
    if oracle['max_absolute_roundoff']>1.:
        possible.append('ORACLE_PRODUCT_RECONSTRUCTION_MISMATCH: inspect normalizations and payoff order')
    if diff['correct_vs_shuffled']['fraction_rows_xy_changed_above_1'] < .001:
        possible.append('NEGLIGIBLE_PREDICTION_SENSITIVITY_TO_CORRECT_VS_SHUFFLED_THOUGHTS')
    else:
        possible.append('THOUGHTS_CHANGE_PREDICTIONS: compare rescued/regressed cases to determine usefulness')
    if not grad['teacher_tokens']['nonzero']:
        possible.append('NO_DETECTABLE_NUMERICAL_GRADIENT_THROUGH_TEACHER_TOKEN_PATH')
    if paired['min_tier_accuracy']['correct']<=paired['min_tier_accuracy']['reference']:
        possible.append('TEACHER_DOES_NOT_BEAT_ORIGINAL_REFERENCE_ON_WEAKEST_TIER')
    return possible


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('HYBRID_TEACHER_PROBE_CUDA_UNAVAILABLE')
    torch.set_num_threads(args.cpu_threads)
    source=Path(args.reference_checkpoint).resolve()
    run_dir=Path(args.run_dir).resolve()
    selected=run_dir/args.checkpoint
    before=(sha256(source),sha256(selected))
    model,splits,domain,config,saved=load_experiment(source,run_dir,args.device,args.checkpoint)
    if args.split != 'dev':
        raise ValueError('HYBRID_TEACHER_PROBE_DEV_ONLY_NO_TEST_SELECTION')
    result=analyze(model,splits['dev'],domain,config['source_identity']['payoff_bound'],
                   config['source_identity']['target_sigma_bins'],args.batch_size,
                   args.grad_rows,args.tolerance,train=splits['train'])
    result['provenance']={'reference_sha256':before[0],'teacher_checkpoint_sha256':before[1],
                          'teacher_selected_step':saved['step'],
                          'matrix_hashes':config['matrix_hashes']['dev'],
                          'split':'DEV','experimental_evaluation_not_new_blind_test':True}
    if (sha256(source),sha256(selected)) != before:
        raise RuntimeError('HYBRID_TEACHER_PROBE_MUTATED_CHECKPOINT')
    output=run_dir/'teacher_diagnostic_v1'
    output.mkdir(parents=True,exist_ok=True)
    path=output/'report.json'
    path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('HYBRID_TEACHER_PROBE_SOURCE_VERIFIED=TRUE READ_ONLY_CHECKPOINTS=TRUE')
    print('HYBRID_TEACHER_PROBE_ORACLE='+json.dumps(result['oracle_product_reconstruction'],separators=(',',':')))
    print('HYBRID_TEACHER_PROBE_MIN_JOINT5='+json.dumps(result['paired_dev_certification']['min_tier_accuracy'],separators=(',',':')))
    print('HYBRID_TEACHER_PROBE_TOKEN_EFFECT='+json.dumps(result['prediction_changes']['correct_vs_shuffled'],separators=(',',':')))
    print('HYBRID_TEACHER_PROBE_GRADIENT='+json.dumps(result['backprop_sensitivity'],separators=(',',':')))
    print('HYBRID_TEACHER_PROBE_LINEAR_READOUT='+json.dumps(result['TRAIN_fitted_diagnostic_linear_readout_DEV'],separators=(',',':')))
    print('HYBRID_TEACHER_PROBE_DIAGNOSES='+json.dumps(result['interpretation']))
    print('HYBRID_TEACHER_PROBE_REPORT='+str(path))
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--checkpoint',default='teacher_best.pt',choices=('teacher_best.pt','latest.pt'))
    p.add_argument('--split',default='dev',choices=('dev',))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--grad-rows',type=int,default=64)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--tolerance',type=float,default=.05)
    return p


def main(argv=None):
    a=parser().parse_args(argv)
    if a.batch_size<1 or a.grad_rows<1 or a.cpu_threads<1 or not 0<a.tolerance<1:
        raise ValueError('HYBRID_TEACHER_PROBE_BAD_ARGUMENT')
    return run(a)


if __name__=='__main__':
    main()
