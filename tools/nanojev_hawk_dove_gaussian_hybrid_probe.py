#!/usr/bin/env python3
"""Read-only trajectory / causal-usefulness probe for Gaussian HybridAgent v1.

Inspects a finalized run's BEST checkpoint; never updates model or run state.
Normal depth traces reuse exactly HybridAgent.latent's shared hidden-layer equation.
DEV by default. TEST is exploratory and never used to select a checkpoint.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_hybrid_agent as hybrid
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss

SCHEMA = 'nanojev.hawk-dove.gaussian-hybrid-probe.v1'
DEPTHS = (0, 1, 2, 4, 8)


def _json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _density_from_hidden(agent, hidden):
    """Reconstruct the frozen decoder exactly as the trainer's depth>0 path."""
    model = agent.reference
    location = model.encoder[4](hidden).sigmoid()
    sigma = model.log_proposal_sigma.exp().clamp(min=.3, max=20.)
    delta = (model.centers.reshape(1, 1, -1) - location.unsqueeze(-1)) * model.bins
    logits = -.5 * (delta / sigma.reshape(1, 2, 1)).square() + model.bank_log_gains
    masses = F.softmax(logits, dim=-1)
    density = masses.reshape(-1, model.banks) @ model.basis
    return density.reshape(-1, 2, model.bins)


def _cpu(tensor):
    return tensor.detach().float().cpu()


def trace_batch(agent, features, domain, max_depth=8):
    """Every real latent iteration, with no teacher vectors or inference changes."""
    if not 0 <= max_depth <= 64:
        raise ValueError('HYBRID_PROBE_INVALID_DEPTH')
    with torch.no_grad():
        initial, hidden = agent.embedding(features)
        result = {0: {'xy': _cpu(gauss.numeric_decode(agent.reference(features), domain)),
                      'hidden': _cpu(hidden)}}
        prior_feedback = None
        for depth in range(1, max_depth + 1):
            feedback = agent.link(hidden)
            next_hidden = F.silu(agent.reference.encoder[2](initial + feedback))
            numeric = gauss.numeric_decode(_density_from_hidden(agent, next_hidden), domain)
            state_step = next_hidden - hidden
            d = {
                'xy': _cpu(numeric), 'hidden': _cpu(next_hidden),
                'feedback': _cpu(feedback),
                'feedback_norm': _cpu(feedback.norm(dim=-1)),
                'input_embedding_norm': _cpu(initial.norm(dim=-1)),
                'feedback_over_input_norm': _cpu(feedback.norm(dim=-1) /
                                                 initial.norm(dim=-1).clamp_min(1e-8)),
                'hidden_step_norm': _cpu(state_step.norm(dim=-1)),
                'hidden_step_over_prior_norm': _cpu(state_step.norm(dim=-1) /
                                                     hidden.norm(dim=-1).clamp_min(1e-8)),
                'hidden_step_cosine': _cpu(F.cosine_similarity(
                    hidden, next_hidden, dim=-1, eps=1e-8)),
                'xy_step_delta': _cpu((numeric - gauss.numeric_decode(
                    agent.reference(features) if depth == 1 else
                    _density_from_hidden(agent, hidden), domain)).abs()),
            }
            if prior_feedback is not None:
                d['feedback_step_norm'] = _cpu((feedback - prior_feedback).norm(dim=-1))
                d['feedback_step_cosine'] = _cpu(F.cosine_similarity(
                    feedback, prior_feedback, dim=-1, eps=1e-8))
            result[depth] = d
            prior_feedback, hidden = feedback, next_hidden
        return result


def controlled_batch(agent, features, domain, depth, mode):
    """Explicit, reproducible interventions on the SAME numerical examples."""
    if mode not in ('normal', 'disabled', 'shuffled', 'frozen_first', 'half', 'double'):
        raise ValueError('HYBRID_PROBE_UNKNOWN_INTERVENTION')
    if depth == 0:
        return gauss.numeric_decode(agent.reference(features), domain)
    with torch.no_grad():
        initial, hidden = agent.embedding(features)
        first = None
        for _ in range(depth):
            feedback = agent.link(hidden)
            if first is None:
                first = feedback.clone()
            if mode == 'disabled':
                feedback = feedback * 0.
            elif mode == 'shuffled':
                # Fixed row order within each batch; no RNG and no oracle use.
                feedback = feedback.roll(1, dims=0)
            elif mode == 'frozen_first':
                feedback = first
            elif mode == 'half':
                feedback = feedback * .5
            elif mode == 'double':
                feedback = feedback * 2.
            hidden = F.silu(agent.reference.encoder[2](initial + feedback))
        density = _density_from_hidden(agent, hidden)
        return gauss.numeric_decode(density, domain)


def score_xy(pred, truth, rows, tolerance):
    """Same exact strict-relative, all-mask per-instance gate as gauss.evaluate."""
    if pred.shape != truth.shape or pred.ndim != 2 or pred.shape[1] != 2:
        raise ValueError('HYBRID_PROBE_PREDICTION_SHAPE')
    if pred.shape[0] != len(rows):
        raise ValueError('HYBRID_PROBE_ROW_MISMATCH')
    hits = (pred - truth).abs() <= tolerance * truth.abs()
    joint = hits.all(dim=1)
    tiers = {}
    for tier in blind.TIERS:
        ix = [i for i, r in enumerate(rows) if r['tier'] == tier]
        if not ix:
            raise RuntimeError('HYBRID_PROBE_EMPTY_TIER')
        instances = collections.defaultdict(list)
        for i in ix:
            instances[rows[i]['instance']].append(bool(joint[i]))
        rate = sum(all(h) for h in instances.values()) / len(instances)
        tiers[tier] = {
            'instances_all_masks_xy_hit_rate': rate,
            'joint_presentation_rate': float(joint[ix].float().mean()),
            'x_rate': float(hits[ix, 0].float().mean()),
            'y_rate': float(hits[ix, 1].float().mean()),
            'independent_instances': len(instances),
            'presentations': len(ix),
        }
    values = [tiers[t]['instances_all_masks_xy_hit_rate'] for t in blind.TIERS]
    return {
        'min_tier_joint5': min(values), 'mean_tier_joint5': sum(values) / len(values),
        'joint_presentation_rate': float(joint.float().mean()),
        'x_presentation_rate': float(hits[:, 0].float().mean()),
        'y_presentation_rate': float(hits[:, 1].float().mean()),
        'x_mae': float((pred[:, 0] - truth[:, 0]).abs().mean()),
        'y_mae': float((pred[:, 1] - truth[:, 1]).abs().mean()),
        'zero_X_exact': int((hits[:, 0] & (truth[:, 0] == 0)).sum()),
        'zero_X_total': int((truth[:, 0] == 0).sum()),
        'zero_Y_exact': int((hits[:, 1] & (truth[:, 1] == 0)).sum()),
        'zero_Y_total': int((truth[:, 1] == 0).sum()),
        'by_tier': tiers,
    }


def comparison(a, b, truth, rows, tolerance, *, description=None):
    """a=current vs b=control, presentation and independent-instance transitions."""
    a_hit = ((a - truth).abs() <= tolerance * truth.abs()).all(dim=1)
    b_hit = ((b - truth).abs() <= tolerance * truth.abs()).all(dim=1)
    a_err = (a - truth).abs().sum(dim=1)
    b_err = (b - truth).abs().sum(dim=1)
    group = collections.defaultdict(list)
    for i, row in enumerate(rows):
        group[row['instance']].append(i)
    rescue = lost = instance_rescue = instance_lost = 0
    for indices in group.values():
        aa = all(bool(a_hit[i]) for i in indices)
        bb = all(bool(b_hit[i]) for i in indices)
        instance_rescue += bool(aa and not bb)
        instance_lost += bool(bb and not aa)
    rescue = int((a_hit & ~b_hit).sum())
    lost = int((~a_hit & b_hit).sum())
    delta = (a - b).abs()
    return {
        'reference': description,
        'rescued_presentations': rescue, 'lost_presentations': lost,
        'rescued_all_mask_instances': instance_rescue,
        'lost_all_mask_instances': instance_lost,
        'mean_absolute_xy_prediction_shift': float(delta.mean()),
        'median_max_xy_prediction_shift': float(delta.max(dim=1).values.median()),
        'changed_presentations_gt_1_unit': int((delta.max(dim=1).values > 1).sum()),
        'changed_presentations_gt_100_units': int((delta.max(dim=1).values > 100).sum()),
        'mean_absolute_error_improvement_sum_XY': float((b_err - a_err).mean()),
        'cases_with_better_total_absolute_error': int((a_err < b_err).sum()),
        'cases_with_worse_total_absolute_error': int((a_err > b_err).sum()),
    }


def _mean(metric_rows, key):
    values = [float(x) for batch in metric_rows for x in batch[key].reshape(-1).tolist()]
    return sum(values) / len(values) if values else None


def _samples(predictions, truth, rows, max_examples, tolerance, max_depth):
    """Choose representative cases deterministically from the entire DEV set."""
    base = predictions[0]
    final = predictions[max_depth]
    base_hit = ((base-truth).abs() <= tolerance*truth.abs()).all(dim=1)
    final_hit = ((final-truth).abs() <= tolerance*truth.abs()).all(dim=1)
    eb = (base-truth).abs().sum(dim=1)
    ef = (final-truth).abs().sum(dim=1)
    priorities = [
        ('rescued', [i for i in range(len(rows)) if not bool(base_hit[i]) and bool(final_hit[i])]),
        ('regressed', [i for i in range(len(rows)) if bool(base_hit[i]) and not bool(final_hit[i])]),
        ('error_improved_not_pass', [i for i in range(len(rows)) if not bool(final_hit[i]) and float(ef[i]) < float(eb[i])]),
        ('error_worsened', [i for i in range(len(rows)) if float(ef[i]) > float(eb[i])]),
        ('exact_zero_targets', [i for i in range(len(rows)) if float(truth[i,0])==0 or float(truth[i,1])==0]),
        ('unchanged_or_other', list(range(len(rows)))),
    ]
    result = []
    seen = set()
    if max_examples == 0:
        return result
    quota = max(1, math.ceil(max_examples / len(priorities)))
    for label, indices in priorities:
        n = 0
        for i in indices:
            if i in seen or len(result) >= max_examples or n >= quota:
                continue
            seen.add(i);n += 1
            row = rows[i]
            result.append({'category':label,'instance':row['instance'], 'tier':row['tier'],
                           'mask':row['mask'], 'truth':truth[i].tolist(),
                           'xy_by_depth':{str(d):predictions[d][i].tolist() for d in range(max_depth+1)}})
    return result


def inspect(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('HYBRID_PROBE_CUDA_UNAVAILABLE')
    if (args.batch_size < 2 or args.examples < 0 or args.cpu_threads < 1 or
            args.split not in ('dev', 'test') or not (0 < args.tolerance < 1)):
        raise ValueError('HYBRID_PROBE_INVALID_ARGUMENTS')
    torch.set_num_threads(args.cpu_threads)
    run = args.run_dir.expanduser().resolve()
    source = args.reference_checkpoint.expanduser().resolve()
    output = (args.output or (run / 'probe_v1')).expanduser().resolve()
    if output.exists():
        raise FileExistsError('HYBRID_PROBE_REFUSES_OUTPUT_OVERWRITE:'+str(output))
    config = _json_read(run/'config.json')
    if not math.isclose(args.tolerance, config.get('tolerance', float('nan')),
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError('HYBRID_PROBE_TOLERANCE_IDENTITY_DRIFT')
    report = _json_read(run/'report.json')
    best = torch.load(run/'best.pt', map_location='cpu', weights_only=True)
    source_ck = torch.load(source, map_location='cpu', weights_only=True)
    if (best.get('schema') != hybrid.SCHEMA + '.best' or
        report.get('schema') != hybrid.SCHEMA + '.report' or
        source_ck.get('schema') != blind.SCHEMA or
        best.get('identity') != config or report.get('identity') != config or
        best.get('step') != report.get('best_step') or
        best.get('depth') != report.get('best_depth') or
        config.get('source_identity') != source_ck.get('identity') or
        config.get('source_sha256') != hybrid.sha256_file(source)):
        raise RuntimeError('HYBRID_PROBE_CHECKPOINT_IDENTITY_DRIFT')
    if report.get('trained_steps', 0) < best['step'] or report.get('activation_gate') != 'PASS':
        raise RuntimeError('HYBRID_PROBE_UNCERTIFIED_SOURCE_RUN')
    ci = source_ck['identity']
    device = torch.device(args.device)
    split_data, domain, manifest = blind._dataset(argparse.Namespace(**ci), device)
    if (manifest['matrix_hashes'] != ci['matrix_hashes'] or
        ci['domain'] != [list(d) for d in domain] or
        config['matrix_hashes'] != ci['matrix_hashes']):
        raise RuntimeError('HYBRID_PROBE_MATRIX_SPLIT_DRIFT')
    data = split_data[args.split]
    original = blind.BlindDensityNetwork(ci['banks'], ci['grid_bins'], ci['seed'], ci['hidden'])
    original.load_state_dict(source_ck['model'], strict=True)
    agent = hybrid.GaussianHybridAgent(original, seed=config['seed'],
                 teacher_scale=config['teacher_scale']).to(device)
    agent.link.load_state_dict(best['link'], strict=True)
    agent.eval()

    # Probe all fixed depths, not merely the checkpoint's selected depth.
    pred_chunks = {d: [] for d in range(9)}
    traces = {d: [] for d in range(1, 9)}
    interventions = {name: [] for name in ('disabled','shuffled','frozen_first','half','double')}
    selected_depth = best['depth']
    with torch.inference_mode():
        for start in range(0, len(data['rows']), args.batch_size):
            x = data['features'][start:start+args.batch_size]
            paths = trace_batch(agent, x, domain, max_depth=8)
            if start == 0:
                if not torch.equal(paths[0]['xy'], _cpu(gauss.numeric_decode(agent(x,0),domain))):
                    raise RuntimeError('HYBRID_PROBE_ZERO_STEP_NOT_EXACT')
                for d in (1, selected_depth, 8):
                    direct = _cpu(gauss.numeric_decode(agent(x,d),domain))
                    if not torch.equal(paths[d]['xy'], direct):
                        raise RuntimeError('HYBRID_PROBE_INSTRUMENTATION_CHANGED_FORWARD')
            for d in range(9):
                pred_chunks[d].append(paths[d]['xy'])
                if d:
                    traces[d].append({k:v for k,v in paths[d].items()
                                      if k not in ('xy','hidden','feedback')})
            for mode in interventions:
                interventions[mode].append(_cpu(controlled_batch(
                    agent,x,domain,selected_depth,mode)))
    pred = {d:torch.cat(pred_chunks[d],dim=0) for d in pred_chunks}
    ctr = {mode:torch.cat(chunks,dim=0) for mode,chunks in interventions.items()}
    truth = _cpu(data['numeric'])
    rows = data['rows']
    all_scores = {str(d):score_xy(pred[d],truth,rows,args.tolerance) for d in DEPTHS}
    if args.split == 'dev':
        score = all_scores[str(selected_depth)]['min_tier_joint5']
        if not math.isclose(score,report['selected_DEV_min_joint5'],rel_tol=0,abs_tol=1e-9):
            raise RuntimeError('HYBRID_PROBE_SAVED_REPORT_SCORE_MISMATCH')
    summary = {}
    for d in range(1,9):
        summary[str(d)] = {
            'feedback_mean_l2': _mean(traces[d],'feedback_norm'),
            'input_embedding_mean_l2': _mean(traces[d],'input_embedding_norm'),
            'feedback_over_input_mean_ratio': _mean(traces[d],'feedback_over_input_norm'),
            'hidden_change_mean_l2':_mean(traces[d],'hidden_step_norm'),
            'hidden_step_over_prior_mean_ratio':_mean(traces[d],'hidden_step_over_prior_norm'),
            'hidden_step_mean_cosine':_mean(traces[d],'hidden_step_cosine'),
            'xy_step_mean_absolute_change': _mean(traces[d],'xy_step_delta'),
            'xy_vs_prior_depth': comparison(pred[d],pred[d-1],truth,rows,args.tolerance,
                                           description=f'depth_{d-1}'),
        }
        if d > 1:
            summary[str(d)]['feedback_change_mean_l2'] = _mean(traces[d],'feedback_step_norm')
            summary[str(d)]['feedback_step_mean_cosine'] = _mean(traces[d],'feedback_step_cosine')
    effects = {}
    for mode, values in ctr.items():
        effects[mode] = {
            'accuracy':score_xy(values,truth,rows,args.tolerance),
            'vs_normal':comparison(pred[selected_depth],values,truth,rows,args.tolerance,
                                   description=mode),
        }
    report_out = {
        'schema':SCHEMA,
        'source_hybrid_identity':config,
        'source_checkpoint_sha256':config['source_sha256'],
        'probe':{'split':args.split,'rows':len(rows),'selected_checkpoint_step':best['step'],
                 'selected_checkpoint_depth':selected_depth,'evaluated_depths':list(DEPTHS),
                 'tolerance':args.tolerance,'batch_size':args.batch_size,
                 'shuffling':'deterministic roll by one position within each evaluation batch'},
        'scores_by_depth':all_scores,
        'step_evolution':summary,
        'selected_depth_interventions':effects,
        'vs_zero_step':{str(d):comparison(pred[d],pred[0],truth,rows,args.tolerance,
                                         description='zero_step') for d in DEPTHS[1:]},
        'representative_examples':_samples(pred,truth,rows,args.examples,
                                            args.tolerance,selected_depth),
        'interpretation_rules':{
            'feedback_ignored':'Normal and disabled predictions almost identical on individual cases',
            'depth_fixed_point':'Later hidden and feedback changes are very small and predicted X/Y stop moving',
            'decoder_insensitive':'Hidden/feedback moves significantly while X/Y predictions stay constant',
            'useful_recursion':'Later-depth rescues exceed regressions on held-out cases AND normal beats controls',
            'not_causal_proof':'Aggregate score equality alone does not prove feedback is unused',
        },
        'holdout_caveat':'Historical matrix DEV/TEST have been examined; diagnostic only',
        'run_read_only':True,
    }
    if hybrid.sha256_file(source) != config['source_sha256']:
        raise RuntimeError('HYBRID_PROBE_SOURCE_MUTATED_DURING_INSPECTION')
    output.mkdir(parents=True, exist_ok=False)
    # Full presentation-level X/Y trajectories for later analysis, no prompt/labels leaked into inference.
    flat_state = {d:{name:torch.cat([part[name] for part in traces[d]])
                     for name in traces[d][0]} for d in range(1,9)}
    with (output/'trajectories.jsonl').open('w',encoding='utf-8',newline='\n') as stream:
        for i,row in enumerate(rows):
            item = {'instance':row['instance'],'tier':row['tier'],'mask':row['mask'],
                    'true_X':float(truth[i,0]),'true_Y':float(truth[i,1]),
                    'predictions':{str(d):{'X':float(pred[d][i,0]),
                                            'Y':float(pred[d][i,1])} for d in range(9)},
                    'latent_state':{str(d):{name:float(val[i].mean())
                                  for name,val in flat_state[d].items()} for d in range(1,9)}}
            stream.write(json.dumps(item,allow_nan=False,separators=(',',':'))+'\n')
    (output/'report.json').write_text(json.dumps(report_out,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    selected = all_scores[str(selected_depth)]['min_tier_joint5']
    disabled = effects['disabled']['accuracy']['min_tier_joint5']
    shuffled = effects['shuffled']['accuracy']['min_tier_joint5']
    frozen = effects['frozen_first']['accuracy']['min_tier_joint5']
    print('HYBRID_PROBE_SOURCE_VERIFIED=TRUE READ_ONLY=TRUE')
    print('HYBRID_PROBE_SPLIT='+args.split.upper()+' SELECTED_DEPTH='+str(selected_depth))
    print('HYBRID_PROBE_DEPTHS='+json.dumps({k:v['min_tier_joint5'] for k,v in all_scores.items()},separators=(',',':')))
    print(f'HYBRID_PROBE_CONTROLS selected={selected:.6f} disabled={disabled:.6f} '
          f'shuffled={shuffled:.6f} frozen_first={frozen:.6f}')
    print('HYBRID_PROBE_STEP_EVOLUTION='+json.dumps({k:{'hidden_l2':v['hidden_change_mean_l2'],
          'feedback_l2':v['feedback_mean_l2'],'xy_shift':v['xy_step_mean_absolute_change']}
          for k,v in summary.items()},separators=(',',':')))
    print('HYBRID_PROBE_REPORT='+str(output/'report.json'))
    return report_out


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True,help='Finalized HybridAgent run containing best.pt')
    p.add_argument('--output',type=Path,help='New empty destination; defaults to <run-dir>/probe_v1')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--split',choices=('dev','test'),default='dev')
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--examples',type=int,default=24)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    return inspect(parser().parse_args(argv))


if __name__=='__main__':
    main()
