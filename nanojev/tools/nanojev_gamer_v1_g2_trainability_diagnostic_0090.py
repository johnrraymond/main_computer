"""G2-0090: frozen-G1 progressive exact-graph trainability diagnosis.

    python -m tools.nanojev_gamer_v1_g2_trainability_diagnostic_0090 run --device cuda
    python -m tools.nanojev_gamer_v1_g2_trainability_diagnostic_0090 resume --device cuda

Runs only on the original train split, with the same eight reachable cases and
frozen joint decoder as 0089. No prospective gold, G2 certificate, or G3 unlock.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import torch

from tools import nanojev_gamer_v1_g2_structural_learning_0089 as prior

SCHEMA = 'nanojev.gamer.graph.g2.progressive-trainability-0090.v1'
SEED = 900090
ROOT_DEFAULT = prior.ROOT_DEFAULT
STAGES = (1, 2, 4, 8)
DEFAULT_LIMITS = (300, 450, 600, 1000)
EVAL_INTERVAL = 10
GRAD_INTERVAL = 100
MAX_GRAD_CASES = 8
TRANSFER_RADIUS = 0.01
STABILITY_CHECKS = 2


def require(condition, code):
    if not condition:
        raise ValueError('G2_0090_' + code)


def sha(path):
    return prior.sha(path)


def read(path):
    return prior.js(path)


def write(path, obj):
    prior.write_json(path, obj)


def case_key(item):
    return hashlib.sha256(item['row']['prompt'].encode('utf-8')).hexdigest()


def stage_limits(value):
    if value is None:
        return DEFAULT_LIMITS
    parts = value.split(',')
    require(len(parts) == 4 and all(x.strip().isdigit() for x in parts), 'STAGE_STEPS')
    steps = tuple(int(x) for x in parts)
    require(all(1 <= x <= 5000 for x in steps), 'STAGE_STEP_RANGE')
    return steps


def _case_metrics(head, item, spec, device):
    """Read-only real inference and the real joint graph decoder."""
    with torch.no_grad():
        logits = prior._forward(head, item, device)
        surface = prior._surface_from_logits(item['objects'], logits)
        gold = prior._gold_keys(surface, item['targets'])
        predicted = prior.previous.decode(surface, spec)
        loss, detail = prior.objective(
            logits, item['targets'].to(device), item['spans'], surface['g1_objects'], spec)
        require(bool(torch.isfinite(loss).item()), 'EVALUATION_LOSS_NONFINITE')
        margins = logits[..., 1:] - logits[..., 0:1]
        targets = item['targets'].to(device)
        positive = (targets > 0).nonzero(as_tuple=False)
        gold_scores = [float(margins[i, j, int(targets[i, j]) - 1].item())
                       for i, j in positive.tolist()]
        false_peak = []
        rank_violations = 0
        rank_total = 0
        n = len(item['spans'])
        for i, j in positive.tolist():
            label = int(targets[i, j]); kind = item['spans'][j]['kind']
            peers = [k for k in range(n) if k != i and k != j
                     and item['spans'][k]['kind'] == kind
                     and int(targets[i, k]) != label]
            if not peers:
                continue
            rank_total += 1
            rival = max(float(margins[i, k, label - 1].item()) for k in peers)
            rank_violations += int(rival >= float(margins[i, j, label - 1].item()))
            false_peak.append(rival)
        tp = len(predicted & gold)
        return {
            'prompt_sha256': case_key(item), 'family': item['row']['family'],
            'exact': int(predicted == gold), 'loss': float(loss.item()),
            'false_positive': len(predicted - gold), 'false_negative': len(gold - predicted),
            'true_positive': tp, 'gold_edges': len(gold), 'predicted_edges': len(predicted),
            'min_gold_margin': min(gold_scores) if gold_scores else None,
            'max_same_kind_false_margin': max(false_peak) if false_peak else None,
            'within_kind_inversions': rank_violations, 'within_kind_groups': rank_total,
            'base_loss': detail['base_loss'], 'structured_loss': detail['structured_loss'],
        }


def snapshot(head, items, spec, device):
    was_training = head.training
    head.eval()
    try:
        records = [_case_metrics(head, item, spec, device) for item in items]
    finally:
        head.train(was_training)
    counts = Counter()
    for r in records:
        counts.update({'exact':r['exact'], 'fp':r['false_positive'],
                       'fn':r['false_negative'], 'tp':r['true_positive'],
                       'gold':r['gold_edges'], 'pred':r['predicted_edges'],
                       'rank_inversions':r['within_kind_inversions'],
                       'rank_groups':r['within_kind_groups']})
    return {'cases':len(items), 'exact':counts['exact'],
            'graph_errors':counts['fp'] + counts['fn'],
            'false_positive':counts['fp'], 'false_negative':counts['fn'],
            'true_positive':counts['tp'],
            'edge_f1':2*counts['tp']/max(counts['gold']+counts['pred'],1),
            'ranking_inverted':counts['rank_inversions'],
            'ranking_total':counts['rank_groups'],
            'mean_loss':sum(r['loss'] for r in records) / len(records),
            'records':records}


def quality(result):
    # Strictly primary exact sets, then Hamming errors, then training objective.
    return (result['exact'], -result['graph_errors'], -result['mean_loss'])


def _trainable(head):
    params = prior._mutable(head)
    # Frozen stages must stay frozen even when evaluating gradient interference.
    allowed = prior.cur.STAGE_MODULES['G2']
    require(all((not p.requires_grad) or any(name.startswith(k) for k in allowed)
                for name,p in head.named_parameters()), 'G2_WEIGHT_SCOPE')
    return params


def _per_case_gradients(head, items, spec, device, params):
    vectors = []
    losses = []
    head.eval()
    for item in items:
        loss, _ = prior._train_case(head, item, device, spec)
        require(bool(torch.isfinite(loss).item()), 'GRADIENT_LOSS_NONFINITE')
        grads = torch.autograd.grad(loss, params, allow_unused=True)
        vectors.append(torch.cat([(g.detach().reshape(-1).to('cpu',dtype=torch.float64)
                                   if g is not None else
                                   torch.zeros(p.numel(),dtype=torch.float64))
                                  for g,p in zip(grads,params)]))
        losses.append(float(loss.detach()))
    head.zero_grad(set_to_none=True)
    return torch.stack(vectors), losses


def gradient_diagnosis(head, items, spec, device, *, include_transfer=False):
    """Pairwise per-case gradient alignment; optional reversible one-step probes.

    Gold is used only to evaluate training-set losses. The interventions are
    normalized in weight-space and always restored, including on error.
    """
    head.eval()
    params = _trainable(head)
    vectors, losses = _per_case_gradients(head, items, spec, device, params)
    gram = vectors @ vectors.T
    norms = torch.linalg.vector_norm(vectors,dim=1)
    denom = norms[:,None]*norms[None,:]
    cosine = torch.where(denom>1e-24,gram/denom.clamp(min=1e-24),torch.zeros_like(gram))
    pairs = [float(cosine[i,j]) for i in range(len(items)) for j in range(i+1,len(items))]
    aggregate = vectors.mean(dim=0)
    aggregate_norm = float(torch.linalg.vector_norm(aggregate))
    alignments = []
    for k in range(len(items)):
        divisor = float(norms[k])*aggregate_norm
        alignments.append(float(torch.dot(vectors[k],aggregate))/divisor if divisor>1e-12 else 0.)
    result = {'cases':len(items), 'per_case':[
                {'prompt_sha256':case_key(it), 'loss':losses[i],
                 'gradient_norm':float(norms[i]),
                 'mean_gradient_alignment':alignments[i]}
                for i,it in enumerate(items)],
              'pair_count':len(pairs),'negative_pair_count':sum(x < -1e-9 for x in pairs),
              'mean_pair_cosine':sum(pairs)/len(pairs) if pairs else None,
              'minimum_pair_cosine':min(pairs) if pairs else None,
              'pair_cosines':[[float(cosine[i,j]) for j in range(len(items))]
                              for i in range(len(items))],
              'interpretation':'negative cosine predicts first-order gradient conflict'}
    if include_transfer:
        result['finite_step_transfer'] = reversible_transfer(head, items, spec, device,
                                                             params, vectors, losses)
    return result


def reversible_transfer(head, items, spec, device, params, vectors, baseline_losses):
    """Counterfactual SGD-style perturbation, strictly restored before return."""
    originals = [p.detach().clone() for p in params]
    result = []
    try:
        for i in range(len(items)):
            gradient = vectors[i]
            norm = float(torch.linalg.vector_norm(gradient))
            require(norm > 0 and torch.isfinite(gradient).all().item(),
                    'INVALID_GRADIENT_TRANSFER')
            offset = 0
            with torch.no_grad():
                for p, base in zip(params, originals):
                    count = p.numel()
                    delta = (gradient[offset:offset+count] * (TRANSFER_RADIUS/norm))
                    p.copy_(base-delta.reshape(p.shape).to(p.device,dtype=p.dtype))
                    offset += count
            altered = snapshot(head, items, spec, device)
            result.append({'trained_on_sha256':case_key(items[i]),
                           'source_loss_delta':altered['records'][i]['loss']-baseline_losses[i],
                           'others_loss_increased':sum(
                               r['loss']-baseline_losses[j] > 1e-6
                               for j,r in enumerate(altered['records']) if j!=i),
                           'other_inexact_after':sum(
                               r['exact']==0 for j,r in enumerate(altered['records'])
                               if j!=i),
                           'per_case_loss_delta':[
                               r['loss']-baseline_losses[j]
                               for j,r in enumerate(altered['records'])],
                           'exact_after':altered['exact']})
            with torch.no_grad():
                for p,base in zip(params,originals):p.copy_(base)
    finally:
        with torch.no_grad():
            for p,base in zip(params,originals):p.copy_(base)
        head.zero_grad(set_to_none=True)
    return {'radius_parameter_l2':TRANSFER_RADIUS,'probes':result,
            'interference_total':sum(x['others_loss_increased'] for x in result),
            'probe_count':len(result),
            'causal_scope':'disposable normalized weight perturbations; state restored'}


def _copy_head(head):
    return {k:v.detach().cpu().clone() for k,v in head.state_dict().items()}


def _case_regressions(before,after):
    old={r['prompt_sha256']:r for r in before['records']}
    return {'lost_exact':[r['prompt_sha256'] for r in after['records']
                          if r['prompt_sha256'] in old and old[r['prompt_sha256']]['exact']
                          and not r['exact']],
            'gained_exact':[r['prompt_sha256'] for r in after['records']
                            if r['prompt_sha256'] in old and not old[r['prompt_sha256']]['exact']
                            and r['exact']],
            'existing_case_error_increase':[r['prompt_sha256'] for r in after['records']
                if r['prompt_sha256'] in old and
                (r['false_positive'] + r['false_negative']) >
                (old[r['prompt_sha256']]['false_positive']+
                 old[r['prompt_sha256']]['false_negative'])]}


def train_stage(head, optimizer, items, spec, device, *, limit, stage, every=EVAL_INTERVAL,
                grad_every=GRAD_INTERVAL):
    """Train on the expanded prefix, retaining the best head AND optimizer state."""
    require(len(items)==stage and stage in STAGES,'STAGE_COUNT')
    require(limit>=1 and every>=1 and grad_every>=1,'INTERVALS')
    params=[p for p in head.parameters() if p.requires_grad]
    require(params,'NO_STAGE_TRAINABLE_WEIGHTS')
    head.eval()  # Same deterministic inference mode in training and evaluation.
    first=snapshot(head,items,spec,device)
    best=first
    best_step=0
    best_weights=_copy_head(head)
    best_optimizer=deepcopy(optimizer.state_dict())
    record=[{'step':0,**first}]
    grads=[{'step':0,**gradient_diagnosis(head,items,spec,device,
                                        include_transfer=True)}]
    prior_exact={r['prompt_sha256']:bool(r['exact']) for r in first['records']}
    ever_exact=dict(prior_exact)
    loss_exact_disagreement=0
    loss_improves_exact_worsens=0
    exact_appeared_then_lost=0
    stable_full=0
    last=first
    for step in range(1,limit+1):
        optimizer.zero_grad(set_to_none=True)
        for item in items:
            loss,_=prior._train_case(head,item,device,spec)
            require(bool(torch.isfinite(loss).item()),'TRAIN_LOSS_NONFINITE')
            (loss/len(items)).backward()
        norm=torch.nn.utils.clip_grad_norm_(params,1.0)
        require(bool(torch.isfinite(norm).item()),'NONFINITE_GRADIENT')
        optimizer.step()
        if step!=1 and step%every and step!=limit:
            continue
        current=snapshot(head,items,spec,device)
        current['step']=step
        record.append(current)
        if current['mean_loss'] < last['mean_loss']-1e-6 and current['exact']<=last['exact']:
            loss_exact_disagreement+=1
        if current['mean_loss'] < last['mean_loss']-1e-6 and current['exact']<last['exact']:
            loss_improves_exact_worsens+=1
        for r in current['records']:
            key=r['prompt_sha256']
            if ever_exact[key] and not r['exact'] and prior_exact[key]:
                exact_appeared_then_lost+=1
            ever_exact[key] |= bool(r['exact'])
            prior_exact[key]=bool(r['exact'])
        if quality(current)>quality(best):
            best=current
            best_step=step
            best_weights=_copy_head(head)
            best_optimizer=deepcopy(optimizer.state_dict())
        if step%grad_every==0 or step==limit:
            grads.append({'step':step,**gradient_diagnosis(
                head,items,spec,device,include_transfer=step==limit)})
        if step==1 or step%50==0 or current['exact']==stage or step==limit:
            print('G2_0090_PROGRESS '+json.dumps({'stage':stage,'step':step,
                 'of':limit,'exact':current['exact'],'best_exact':best['exact'],
                 'fp':current['false_positive'],'fn':current['false_negative'],
                 'loss':round(current['mean_loss'],6)},sort_keys=True),flush=True)
        stable_full=stable_full+1 if current['exact']==stage else 0
        last=current
        if stable_full>=STABILITY_CHECKS:
            break
    # Restore best rather than last, including optimizer moments.
    head.load_state_dict(best_weights,strict=True)
    optimizer.load_state_dict(best_optimizer)
    confirmed=snapshot(head,items,spec,device)
    require(confirmed['exact']==best['exact'] and
            confirmed['graph_errors']==best['graph_errors'], 'BEST_RESTORE_MISMATCH')
    summary={'stage':stage,'steps_executed':step,'best_step':best_step,
             'last_exact':last['exact'],'best_exact':best['exact'],
             'passed':best['exact']==stage,
             'stable_two_evaluations':stable_full>=STABILITY_CHECKS,
             'best_graph_errors':best['graph_errors'],
             'best_mean_loss':best['mean_loss'],
             'final_graph_errors':last['graph_errors'],
             'loss_declined_without_exact_gain_observations':loss_exact_disagreement,
             'loss_declined_while_exact_decreased_observations':loss_improves_exact_worsens,
             'exact_flip_losses':exact_appeared_then_lost,
             'ever_exact_cases':sum(ever_exact.values()),
             'recovered_best_checkpoint':True}
    return {'summary':summary,'history':record,'gradients':grads,
            'best':best,'initial':first,'last':last}


def _upstream(paths,previous_root):
    _,spec=prior.preflight(paths)
    require(previous_root.is_dir(),'MISSING_0089_TRAINABILITY')
    old=read(previous_root/'trainability.json')
    require(old.get('status')=='EXACT_GRAPH_TRAINABILITY_BLOCKED' and
            old.get('one_pass') is True and old.get('eight_pass') is False and
            old.get('g3_unlocked') is False,'0089_FAILURE_LINEAGE')
    old_prov=prior._check_provenance(paths,previous_root)
    require(old_prov.get('decoder')==spec,'0089_DECODER_DRIFT')
    return spec,old


def _source_provenance(paths,previous_root,selection,spec,limits):
    selected=previous_root/'trainability-selection.json'
    return {'schema':SCHEMA,'runner_sha256':sha(__file__),
            'parent_0089_record_sha256':sha(previous_root/'trainability.json'),
            'parent_0089_selection_sha256':sha(selected),
            'dataset_sha256':sha(paths['dataset']),
            'head_sha256':sha(paths['parent_0087']/'training'/prior.cur.HEAD_FILE),
            'g1_certificate_sha256':sha(paths['certificate']),
            'g1_composite_sha256':sha(paths['composite']/'composite-interface.json'),
            'decoder':spec,'selected_cases':selection['cases'],
            'selected_cases_sha256':sha(selected),
            'stage_limits':list(limits),'seed':SEED,
            'execution':'train_only_progressive_1_2_4_8',
            'g3_unlocked':False,'g2_certified':False,'production_promotion':False}


def _verify_provenance(paths,previous_root,dest,limits):
    saved=read(dest/'provenance.json')
    spec,old=_upstream(paths,previous_root)
    compare=_source_provenance(paths,previous_root,
                                read(previous_root/'trainability-selection.json'),spec,limits)
    require(saved==compare,'PROVENANCE_CHANGED')
    return saved, spec


def _checkpoint_stage(paths,dest,head,manifest,opt,result,count,provenance):
    stage=dest/f'stage-{count}'
    require(not stage.exists(),'STAGE_ALREADY_COMMITTED')
    stage.mkdir(exist_ok=False)
    info={**result['summary'],'stage':count,'g3_unlocked':False,
          'diagnostic_only':True}
    prior._save_head(head,manifest,stage/'checkpoint',paths,
                     arm='PROGRESSIVE_MEMORIZATION_NOT_DEPLOYABLE',detail=info)
    torch.save(opt.state_dict(),str(stage/'optimizer.pt'))
    write(stage/'history.json',result['history'])
    write(stage/'gradients.json',result['gradients'])
    write(stage/'summary.json',info)
    write(stage/'lock.json',{'schema':SCHEMA,'stage':count,
          'head_sha256':sha(stage/'checkpoint'/prior.cur.HEAD_FILE),
          'checkpoint_manifest_sha256':sha(stage/'checkpoint'/'manifest.json'),
          'optimizer_sha256':sha(stage/'optimizer.pt'),
          'history_sha256':sha(stage/'history.json'),
          'gradients_sha256':sha(stage/'gradients.json'),
          'summary_sha256':sha(stage/'summary.json'),
          'provenance_sha256':sha(dest/'provenance.json'),
          'g3_unlocked':False})
    return info


def _load_stage(paths,dest,stage,device,optimizer,head):
    saved=dest/f'stage-{stage}'
    lock=read(saved/'lock.json')
    require(lock.get('schema')==SCHEMA and lock.get('stage')==stage and
            lock.get('g3_unlocked') is False and
            lock.get('provenance_sha256')==sha(dest/'provenance.json'),
            'STAGE_LOCK_INVALID')
    for rel,key in [('checkpoint/'+prior.cur.HEAD_FILE,'head_sha256'),
                    ('checkpoint/manifest.json','checkpoint_manifest_sha256'),
                    ('optimizer.pt','optimizer_sha256'),('history.json','history_sha256'),
                    ('gradients.json','gradients_sha256'),('summary.json','summary_sha256')]:
        require(sha(saved/rel)==lock[key],'STAGE_FILE_CHANGED')
    checkpoint,record=prior.cur._load_checkpoint(saved/'checkpoint',device=device)
    parent_hash=sha(paths['parent_0087']/'training'/prior.cur.HEAD_FILE)
    require(record.get('g2_0089',{}).get('parent_head_sha256')==parent_hash and
            record.get('g2_0089',{}).get('frozen_g1') is True and
            record.get('g2_0089',{}).get('arm')=='PROGRESSIVE_MEMORIZATION_NOT_DEPLOYABLE',
            'STAGE_CHECKPOINT_LINEAGE')
    prior.native.compare_frozen_stage(paths['parent_0087']/'training',saved/'checkpoint')
    head.load_state_dict(checkpoint.state_dict(),strict=True)
    # Only our own hash-verified optimizer file is loaded; weights_only prevents
    # executing arbitrary Python objects from the checkpoint serializer.
    state=torch.load(str(saved/'optimizer.pt'),map_location=device,weights_only=True)
    optimizer.load_state_dict(state)
    return read(saved/'summary.json')


def diagnose(*,root=ROOT_DEFAULT,output=None,device='cuda',
             previous=None,step_limits=None,resume=False):
    limits=stage_limits(step_limits)
    paths=prior.paths_for(root)
    dest=Path(output).resolve() if output is not None else (
        Path(root)/'oracle-fabric-g2-progressive-trainability-0090').resolve()
    previous_root=(Path(previous) if previous is not None else
        Path(root)/'oracle-fabric-g2-structural-learning-0089').resolve()
    if resume:
        require(dest.is_dir(),'RESUME_OUTPUT_MISSING')
        provenance,spec=_verify_provenance(paths,previous_root,dest,limits)
        require(not (dest/'report.json').exists(),'ALREADY_COMPLETE')
    else:
        require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
        spec,old=_upstream(paths,previous_root)
    network,tok,composite,head,manifest=prior._load_runtime(paths,device)
    chosen,rejections=prior._candidate_training_cases(network,tok,composite,head,paths,spec,device)
    selection=read(previous_root/'trainability-selection.json')
    require([case_key(c) for c in chosen]==[r['prompt_sha256'] for r in selection['cases']],
            'TRAINABILITY_CASES_CHANGED')
    require(all(c['reachable']>0 for c in chosen),'NO_REACHABLE_EDGES')
    if not resume:
        dest.mkdir(exist_ok=False)
        provenance=_source_provenance(paths,previous_root,selection,spec,limits)
        write(dest/'provenance.json',provenance)
    _trainable(head)
    head.eval()
    optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],
                                lr=prior.LR,weight_decay=0.)
    torch.manual_seed(SEED)
    all_records=[];completed=0
    for stage,limit in zip(STAGES,limits):
        saved=dest/f'stage-{stage}'
        if saved.exists():
            require(resume,'OUTPUT_STAGE_PRESENT')
            current=_load_stage(paths,dest,stage,device,optimizer,head)
            all_records.append(current)
            completed=stage
            print('G2_0090_RESUMED '+json.dumps({'stage':stage,'best_exact':current['best_exact'],
                  'head_sha256':sha(saved/'checkpoint'/prior.cur.HEAD_FILE)},sort_keys=True),flush=True)
            continue
        # Check the earlier-stage cases before the new cases are introduced.
        pre_existing=snapshot(head,chosen[:completed],spec,device) if completed else None
        record=train_stage(head,optimizer,chosen[:stage],spec,device,
                           limit=limit,stage=stage)
        post_existing=snapshot(head,chosen[:completed],spec,device) if completed else None
        diagnostics=record['summary']
        diagnostics['prior_case_regressions']=(_case_regressions(pre_existing,post_existing)
                      if completed else {'lost_exact':[], 'gained_exact':[],
                      'existing_case_error_increase':[]})
        diagnostics['new_case_count']=stage-completed
        _checkpoint_stage(paths,dest,head,manifest,optimizer,record,stage,provenance)
        all_records.append(diagnostics)
        print('G2_0090_STAGE '+json.dumps({'stage':stage,'best_exact':diagnostics['best_exact'],
              'passed':diagnostics['passed'],'best_step':diagnostics['best_step'],
              'prior_regressions':len(diagnostics['prior_case_regressions']['lost_exact'])},
              sort_keys=True),flush=True)
        completed=stage
    require(completed==8 and len(all_records)==4,'INCOMPLETE_PROGRESSION')
    final=snapshot(head,chosen,spec,device)
    require(final['exact']==all_records[-1]['best_exact'],'FINAL_CHECKPOINT_MISMATCH')
    stage_reports=[{'stage':s,'checkpoint_sha256':sha(dest/f'stage-{s}'/'checkpoint'/prior.cur.HEAD_FILE),
                    'lock_sha256':sha(dest/f'stage-{s}'/'lock.json'),
                    **data} for s,data in zip(STAGES,all_records)]
    all_stages_pass=all(r['passed'] for r in all_records)
    status=('PROGRESSIVE_ALL_STAGES_PASSED_NOT_CERTIFIED' if all_stages_pass
            else 'EIGHT_GRAPH_MEMORIZATION_WITH_INTERMEDIATE_GAPS'
            if final['exact']==8 else 'PROGRESSIVE_TRAINABILITY_NOT_DEMONSTRATED')
    result={'schema':SCHEMA,
       'status':status,
       'progressive_one_pass':all_records[0]['passed'],
       'progressive_two_pass':all_records[1]['passed'],
       'progressive_four_pass':all_records[2]['passed'],
       'progressive_eight_pass':all_records[3]['passed'],
       'eight_exact':final['exact'], 'eight_graph_errors':final['graph_errors'],
       'stages':stage_reports, 'comparison':{
           'cold_start_0089_one_exact':old['results'][0]['exact'] if not resume else
                   read(previous_root/'trainability.json')['results'][0]['exact'],
           'cold_start_0089_eight_exact':read(previous_root/'trainability.json')['results'][1]['exact'],
           'same_train_case_sha256':sha(previous_root/'trainability-selection.json')},
       'diagnostics':{
           'gradient_interference_files':[f'stage-{s}/gradients.json' for s in STAGES],
           'loss_decoder_files':[f'stage-{s}/history.json' for s in STAGES],
           'measurements':'full-batch objective, per-case graph errors, within-kind inversions, '
                          'gradient cosines, reversible single-case weight perturbations'},
       'checkpoint_mutation':False,'parent_head_unchanged':
           sha(paths['parent_0087']/'training'/prior.cur.HEAD_FILE)==provenance['head_sha256'],
       'g2_certified':False,'g3_unlocked':False,'production_promotion':False,
       'independent_certification':False,'no_automated_bakeoff':True,
       'provenance_sha256':sha(dest/'provenance.json')}
    require(result['parent_head_unchanged'],'PARENT_MUTATED')
    write(dest/'report.json',result)
    print('G2_0090_RESULT '+json.dumps({'status':result['status'],
       'stages':{str(x['stage']):x['best_exact'] for x in stage_reports},
       'eight_exact':final['exact'],'g3_unlocked':False},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('run','resume'),nargs='?',default='run')
    p.add_argument('--root',default=ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--previous')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--step-limits',default=None,
                   help='Four comma-separated stage update budgets, default 300,450,600,1000')
    a=vars(p.parse_args(argv));cmd=a.pop('command')
    return diagnose(**a,resume=(cmd=='resume'))

if __name__=='__main__':
    main()
