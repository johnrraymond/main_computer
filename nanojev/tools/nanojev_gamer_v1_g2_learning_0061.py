"""G2-0061: experimental, incomplete-coverage learning on contextual actor candidates.

Separate from formal G2-0059 acceptance. No access to test or challenge
features/labels, no production writes, no change to the frozen source models.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools import nanojev_gamer_v1_g2_actor_bakeoff_0060 as a60

SCHEMA='nanojev.gamer.graph.g2.experimental-learning-0061.v1'
POLICY='actor_cues'
MIN_TRAIN_REACHABLE_PAIRS=8
MIN_VALIDATION_PAIRS=8


def refuse(reason):
    raise ValueError('GAMER_G2_0061_'+str(reason))


def _require_counts(plan,train_graphs,validation_graphs):
    if (type(train_graphs) is not int or type(validation_graphs) is not int or
        train_graphs<16 or train_graphs%8 or validation_graphs<16 or
        validation_graphs%8 or train_graphs>plan['splits']['train']['graphs'] or
        validation_graphs>plan['splits']['validation']['graphs']):
        refuse('GRAPH_COUNTS_MUST_BE_MULTIPLES_OF_8_WITHIN_PLAN')
    return train_graphs//2,validation_graphs//2


def _selected_starts(total_pairs):
    return range(0,total_pairs,g.SHARD_PAIRS)


def capture_learning(plan_dir,output,*,root,device='cuda',train_graphs=1000,
                     validation_graphs=400,max_shards=None):
    """Capture chosen TRAIN prefix + disjoint VALIDATION only; resumable.

    Does not modify previously audited evidence/actor-bakeoff shards. The
    separate destination is identified by pinned policy and frozen-source hashes.
    """
    plan=g._require_plan(plan_dir)
    train_pairs,validation_pairs=_require_counts(plan,train_graphs,validation_graphs)
    allowed={('train',i) for i in _selected_starts(train_pairs)}
    allowed.update(('validation',i) for i in _selected_starts(validation_pairs))
    result=g.capture(plan_dir,output,root=root,device=device,candidate_policy=POLICY,
                     only_shards=allowed,max_shards=max_shards)
    ds=g.FeatureCorpus(plan_dir,output,'train',require_complete=False)
    vd=g.FeatureCorpus(plan_dir,output,'validation',require_complete=False)
    complete=(len(ds.pairs)>=train_pairs and len(vd.pairs)>=validation_pairs)
    if complete:
        _check_prefix(ds,train_pairs,'train')
        _check_prefix(vd,validation_pairs,'validation')
    return {'schema':SCHEMA,'status':('CAPTURE_READY_FOR_RESEARCH_TRAINING'
            if complete else 'CAPTURE_IN_PROGRESS'),
            'policy':POLICY,'train_pairs_captured':len(ds.pairs),
            'train_pairs_requested':train_pairs,'validation_pairs_captured':len(vd.pairs),
            'validation_pairs_requested':validation_pairs,
            'train_reachability':round(ds.reachability,6),
            'validation_reachability':round(vd.reachability,6),
            'formal_98_percent_gate_waived':False,
            'research_only_incomplete_coverage_exception':True,
            'capture':result,'production_promotion':False,'sealed_test_used':False}


def _check_prefix(ds,pairs,split):
    """Prevent a missing middle shard from masquerading as a contiguous prefix."""
    if len(ds.pairs)<pairs:refuse('INCOMPLETE_'+split.upper()+'_PREFIX')
    for i, (start, offset, a, b) in enumerate(ds.pairs[:pairs]):
        if start+offset!=i or a['side']!=0 or b['side']!=1:
            refuse('NONCONTIGUOUS_'+split.upper()+'_PREFIX')
    # Capturing more later is fine, but experiment's validation universe must
    # remain the requested prefix; never evaluate on a different subset.
    return True


class PairPrefix:
    """View of one immutable prefix; no reshuffling of validation membership."""
    def __init__(self,ds,n,*,reachable_only=False):
        self.source=ds
        self.indices=([i for i in range(n) if ds.pairs[i][2]['candidate_reachable']
                       and ds.pairs[i][3]['candidate_reachable']]
                      if reachable_only else list(range(n)))
        self.pairs=[ds.pairs[i] for i in self.indices]

    def pair(self,index,with_labels=True):
        return self.source.pair(self.indices[index],with_labels=with_labels)


def research_budget(plan_dir,capture_dir,*,train_graphs,validation_graphs,
                    epochs,batch_pairs):
    if not 1<=epochs<=100 or not 1<=batch_pairs<=8:
        refuse('TRAINING_BUDGET')
    plan=g._require_plan(plan_dir)
    train_pairs,val_pairs=_require_counts(plan,train_graphs,validation_graphs)
    ds=g.FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    vd=g.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    if ds.config.get('candidate_policy')!=POLICY or vd.config.get('candidate_policy')!=POLICY:
        refuse('CANDIDATE_POLICY_NOT_CONTEXTUAL')
    if ds.config.get('production_promotion') is not False:
        refuse('PRODUCTION_CAPTURE_FORBIDDEN')
    _check_prefix(ds,train_pairs,'train')
    _check_prefix(vd,val_pairs,'validation')
    for split in ('test','challenge'):
        if any(Path(capture_dir).glob(split+'-*.json')) or any(
            Path(capture_dir).glob(split+'-*.safetensors')):
            refuse('SEALED_DATA_IN_EXPERIMENTAL_CAPTURE')
    eligible=[i for i in ds.indices if i<train_pairs]
    eligible_set=set(eligible)
    if len(eligible)<MIN_TRAIN_REACHABLE_PAIRS or val_pairs<MIN_VALIDATION_PAIRS:
        refuse('INSUFFICIENT_TRAINING_OR_VALIDATION')
    by_cell=Counter();excluded_by_cell=Counter();missing=Counter()
    for ix in range(train_pairs):
        _,_,a,b=ds.pairs[ix]
        key=a['family']+':'+a['mode']
        if ix in eligible_set:by_cell[key]+=1
        else:excluded_by_cell[key]+=1
        missing.update(a.get('binding_diagnosis',{}).get('by_relation',{}))
        missing.update(b.get('binding_diagnosis',{}).get('by_relation',{}))
    if any(by_cell[f+':'+m]==0 for f,m in g.CELLS):
        refuse('EMPTY_FAMILY_MODE_LEARNING_CELL')
    per_epoch=math.ceil(len(eligible)/batch_pairs)
    reachable_validation=sum(bool(a['candidate_reachable'] and b['candidate_reachable'])
                              for _,_,a,b in vd.pairs[:val_pairs])
    return {'schema':SCHEMA,'requested_training_graphs':train_graphs,
        'requested_validation_graphs':validation_graphs,
        'eligible_train_pairs':len(eligible),'excluded_train_pairs':train_pairs-len(eligible),
        'eligible_train_graphs':len(eligible)*2,
        'train_pair_reachability':round(len(eligible)/train_pairs,6),
        'validation_pair_reachability':round(reachable_validation/val_pairs,6),
        'eligible_validation_pairs':reachable_validation,
        'train_pairs_per_cell':dict(by_cell),
        'excluded_pairs_per_cell':dict(excluded_by_cell),
        'missing_training_edges_by_relation':dict(missing),
        'steps_per_epoch':per_epoch,'total_optimizer_steps':per_epoch*epochs,
        'epochs':epochs,'batch_pairs':batch_pairs,
        'unreachable_gold_edges_in_training_loss':0,
        'unreachable_pairs_excluded_not_relabeled_as_negative':True,
        'validation_includes_unreachable_pairs':True,
        'formal_coverage_gate_passed':False,
        'research_only_incomplete_coverage_exception':True,
        'production_promotion':False,'sealed_test_used':False}


def train_learning(plan_dir,capture_dir,output,*,root,device='cuda',
                   train_graphs=1000,validation_graphs=400,epochs=4,batch_pairs=2,
                   learning_rate=None,checkpoint_every=None,max_new_steps=None):
    """Actual optimizer loop on only fully representable counterfactual pairs.

    Incomplete pairs are counted and excluded, NOT converted to negatives.
    Validation still includes every graph, so its score exposes the coverage
    ceiling as well as learnable errors. The old formal train/evaluate remains.
    """
    if device not in ('cpu','cuda') or (device=='cuda' and not torch.cuda.is_available()):
        refuse('TRAIN_DEVICE_UNAVAILABLE')
    lr=g.p55.LR if learning_rate is None else learning_rate
    if not 0<lr<=.005 or max_new_steps is not None and max_new_steps<1:
        refuse('TRAINING_CONFIGURATION')
    budget=research_budget(plan_dir,capture_dir,train_graphs=train_graphs,
                  validation_graphs=validation_graphs,epochs=epochs,batch_pairs=batch_pairs)
    ds=g.FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    vd=g.FeatureCorpus(plan_dir,capture_dir,'validation',require_complete=False)
    train_pairs=train_graphs//2;validation_pairs=validation_graphs//2
    ds.indices=[i for i in ds.indices if i<train_pairs]
    validation=PairPrefix(vd,validation_pairs)
    reachable_validation=PairPrefix(vd,validation_pairs,reachable_only=True)
    config=json.loads((Path(capture_dir)/'capture.json').read_text(encoding='utf-8'))
    model,parent_sha=g._model_from_parent(root,capture_config=config)
    model=model.to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr)
    checkpoint_every=checkpoint_every or budget['steps_per_epoch']
    if checkpoint_every<1:refuse('CHECKPOINT_INTERVAL')
    task={'schema':SCHEMA,'budget':budget,'capture_config_sha256':g.file_sha(
            Path(capture_dir)/'capture.json'),
        'plan_sha256':g.file_sha(Path(plan_dir)/'plan.json'),
        'parent57_report_sha256':parent_sha,
        'source_sha256':{k:g.file_sha(Path(module.__file__)) for k,module in (
             ('experimental_lane',__import__(__name__,fromlist=['main'])),
             ('pipeline',g),('actor_policy',a60),('paired_objective',g.p57),
             ('set_predictor',g.p55),('scale_training',g.p56))},
        'device':device,'learning_rate':lr,'checkpoint_every':checkpoint_every,
        'training_rule':'only fully candidate-reachable paired graphs; never fabricate negatives',
        'validation_rule':'all held-out validation graphs including unreachable gold; also separately report reachable-only',
        'formal_training_gate_unchanged':True,
        'production_promotion':False,'sealed_test_used':False}
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    cfg_path=out/'training.json'
    if cfg_path.exists():
        if json.loads(cfg_path.read_text(encoding='utf-8'))!=task:
            refuse('RESTART_CONFIG_OR_SOURCE_DRIFT')
    else:g.atomic_json(cfg_path,task)
    if (out/'selection.json').exists():refuse('ALREADY_TRAINED')
    saved=g._checkpoints(out)
    if saved:
        last=saved[-1]
        w,op,_=g._ckpt_files(out,last['step'])
        model.load_state_dict(load_file(str(w),device='cpu'),strict=True)
        optimizer.load_state_dict(torch.load(op,map_location=device,weights_only=True))
        epoch_start,batch_start,step=last['epoch'],last['next_batch'],last['step']
    else:
        epoch_start=batch_start=step=0
        metrics=g.eval_stream(model,validation)
        # Report true candidate ceiling independently from model accuracy.
        metrics['reachable_only']=g.eval_stream(model,reachable_validation) if reachable_validation.pairs else None
        g._save_checkpoint(out,0,model,optimizer,epoch=0,next_batch=0,metrics=metrics)
    new_steps=0
    for epoch in range(epoch_start,epochs):
        for batch_index,indices in enumerate(ds.batches(epoch,batch_pairs)):
            if epoch==epoch_start and batch_index<batch_start:continue
            pairs=[];rows={}
            for ix in indices:
                (left,right),receipt=ds.pair(ix)
                if not (len(left['positive'])==left['truth_count'] and
                        len(right['positive'])==right['truth_count'] and
                        left.get('training_node_ids') is not None and
                        right.get('training_node_ids') is not None):
                    refuse('CENSORED_GOLD_ENTERED_TRAINING_OBJECTIVE')
                pairs.append(receipt)
                rows[left['prompt_sha256']]=left
                rows[right['prompt_sha256']]=right
            model.train();optimizer.zero_grad(set_to_none=True)
            loss=g.p57.batch_objective(model,pairs,rows,'D_combined')
            if not bool(torch.isfinite(loss)):
                refuse('NONFINITE_TRAINING_LOSS')
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.0)
            if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT')
            optimizer.step();step+=1;new_steps+=1
            complete_epoch=batch_index+1==budget['steps_per_epoch']
            save=step%checkpoint_every==0 or complete_epoch or (
                max_new_steps is not None and new_steps>=max_new_steps)
            if save:
                model.eval();metrics=g.eval_stream(model,validation)
                metrics['reachable_only']=(g.eval_stream(model,reachable_validation)
                                           if reachable_validation.pairs else None)
                next_epoch,next_batch=((epoch+1,0) if complete_epoch
                                       else (epoch,batch_index+1))
                g._save_checkpoint(out,step,model,optimizer,epoch=next_epoch,
                                   next_batch=next_batch,metrics=metrics)
                print('GAMER_G2_0061_LEARNING '+json.dumps({
                    'step':step,'epoch':next_epoch,'validation_exact':metrics['exact'],
                    'validation_pairs_exact':metrics['exact_pairs'],
                    'validation_recall':metrics['edge_recall'],
                    'reachable_validation_exact':metrics['reachable_only']['exact']
                      if metrics['reachable_only'] else None,
                    'loss':round(float(loss.detach()),6)},sort_keys=True),flush=True)
            if max_new_steps is not None and new_steps>=max_new_steps:
                return {'schema':SCHEMA,'status':'PAUSED_RESTARTABLE','global_step':step,
                    'remaining_steps':budget['total_optimizer_steps']-step,
                    'production_promotion':False}
    if step!=budget['total_optimizer_steps']:refuse('TRAINING_STEP_COUNT')
    history=g._checkpoints(out)
    selected=max(history,key=lambda item:g._validation_quality(item['validation'],item['step']))
    # Evaluate the *selected* checkpoint on reachable training graphs. A
    # high training score and flat validation is overfitting, not learning.
    selected_weights=g._ckpt_files(out,selected['step'])[0]
    model.load_state_dict(load_file(str(selected_weights),device='cpu'),strict=True)
    model=model.eval().requires_grad_(False)
    trained_on_reachable=g.eval_stream(model,PairPrefix(ds,train_pairs,reachable_only=True))
    baseline=history[0]['validation']
    train_exact_rate=(trained_on_reachable['exact']/trained_on_reachable['graphs']
                      if trained_on_reachable['graphs'] else 0.)
    reachable_metrics=selected['validation']['reachable_only']
    valid_exact_rate=(reachable_metrics['exact']/reachable_metrics['graphs']
                      if reachable_metrics and reachable_metrics['graphs'] else 0.)
    result={'schema':SCHEMA,'status':'EXPERIMENTAL_LEARNING_COMPLETE_NOT_CERTIFIED',
        'selected_step':selected['step'],
        'baseline_step_zero_validation':baseline,
        'selected_checkpoint_reachable_training':trained_on_reachable,
        'training_minus_reachable_validation_exact_graph_rate':round(train_exact_rate-valid_exact_rate,6),
        'validation_exact_graph_delta_from_parent':selected['validation']['exact']-baseline['exact'],
        'validation_missing_edge_delta_from_parent':selected['validation']['missing']-baseline['missing'],
        'selected_weights_sha256':selected['weights_sha256'],
        'validation':selected['validation'],
        'training_budget':budget,'optimizer_steps':step,
        'selection_uses_test':False,'selection_uses_challenge':False,
        'formal_98_percent_gate_passed':False,
        'candidate_policy':POLICY,'production_promotion':False,'sealed_test_used':False}
    g.atomic_json(out/'selection.json',result)
    return result
