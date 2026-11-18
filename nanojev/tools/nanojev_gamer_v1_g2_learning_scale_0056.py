"""G2-0056: matched-budget learning-scale study of the G2-0055 set predictor.

Four independent continuations of the exact step-320 memorized checkpoint train
on nested balanced 8/16/32/80 counterfactual TRAIN graphs. 16 disjoint TRAIN-
fit graphs select checkpoints/stage; previously inspected probes are scored
ONLY AFTER selection. 32 additional pairs? No: 16 NEW counterfactual pairs
(32 graphs) beyond 0053 fresh probes form a never-before-scored final test.
Frozen G1, G2-0052 candidate construction and G2-0048 encoder. Research only.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_frozen_generalization_0055 as frozen55
from tools import nanojev_gamer_v1_g2_memorization_diagnosis_0055 as mem55
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

SCHEMA='nanojev.gamer.graph.g2.learning-scale-0056.v1'
STATUS='G2_0056_LEARNING_SCALE_RESEARCH_NOT_CERTIFIED'
REPORT='g2_0056_learning_scale_report.json'
LEDGER='g2_0056_learning_scale_candidate_per_case.jsonl.gz'
PREDICTIONS='g2_0056_learning_scale_predictions.jsonl.gz'
WEIGHTS='g2_0056_learning_scale_checkpoints.safetensors'
POLICY='g2_0056_learning_scale_policy.json'
SIZES=(8,16,32,80)
STEPS=960
INTERVAL=120
BATCH=4
MAX_STEPS=4800
TRAIN_GROUP='counterfactual_train'
FINAL_GROUP='final_counterfactual_test'
FINAL_COUNT=32
CELLS=tuple((family,mode) for family in ('allocation','encounter') for mode in ('renaming','role_swap'))


def refuse(reason):
    raise ValueError('GAMER_G2_0056_'+reason)


def _sha(folder_file):
    return d48.backbone.sha256(folder_file)


def _ids(pairs):
    return {sha for pair in pairs for sha in pair['sha256']}


def arrange_cohorts(samples,pairs,gate_ids):
    """Pair-disjoint balanced scale and validation split; no test labels read."""
    if (set(pairs)!={'counterfactual_train','counterfactual_probe',
                    'fresh_counterfactual_probe',FINAL_GROUP}
        or tuple(len(pairs[k]) for k in pairs)!=(48,16,16,16)):
        refuse('PAIR_COHORT_COUNTS')
    grouped=defaultdict(list)
    for pair in pairs[TRAIN_GROUP]:
        grouped[(pair['family'],pair['mode'])].append(pair)
    if set(grouped)!=set(CELLS) or any(len(grouped[cell])!=12 for cell in CELLS):
        refuse('CELL_BALANCE')
    # Original 0055 split: first two pairs/cell held for train_fit; the
    # remaining ten pairs/cell form the original 80-graph training cohort.
    validation_pairs=[pair for cell in CELLS for pair in grouped[cell][:2]]
    train_pairs=[pair for cell in CELLS for pair in grouped[cell][2:]]
    stage_pairs={size:[pair for cell in CELLS for pair in
                            grouped[cell][2:2+size//8]] for size in SIZES}
    if [len(stage_pairs[size]) for size in SIZES]!=[4,8,16,40]:
        refuse('SCALE_PAIR_COUNT')
    if _ids(stage_pairs[8])!=set(gate_ids) or len(gate_ids)!=8:
        refuse('PARENT_8_COHORT_DRIFT')
    if _ids(stage_pairs[80])!=_ids(train_pairs):
        refuse('TRAIN_NESTING')
    if any(not _ids(stage_pairs[a])<_ids(stage_pairs[b])
           for a,b in zip(SIZES,SIZES[1:])):
        refuse('STAGES_NOT_STRICTLY_NESTED')
    # Pair and prompt splits audited before mark_truth; all final-test labels
    # are still opaque in this stage-selection function.
    groups=[validation_pairs,train_pairs,pairs['counterfactual_probe'],
            pairs['fresh_counterfactual_probe'],pairs[FINAL_GROUP]]
    id_sets=[_ids(p) for p in groups]
    if any(len(sha_set)!=2*len(pair_list) for sha_set,pair_list in zip(id_sets,groups)):
        refuse('PROMPT_DUPLICATION')
    if any(id_sets[i]&id_sets[j] for i in range(len(groups))
                                 for j in range(i+1,len(groups))):
        refuse('TRAIN_VALIDATION_TEST_OVERLAP')
    for section,expected in ((TRAIN_GROUP,96),('counterfactual_probe',32),
                             ('fresh_counterfactual_probe',32),(FINAL_GROUP,FINAL_COUNT)):
        rows=[sample for sample in samples if sample['section']==section]
        if len(rows)!=expected:refuse('CAPTURE_COUNTS_'+section)
        if set(x['prompt_sha256'] for x in rows)!=_ids(pairs[section]):
            refuse('CAPTURE_IDENTITIES_'+section)
    train_map={row['prompt_sha256']:row for row in samples if row['section']==TRAIN_GROUP}
    # Only the 96 labeled TRAIN-domain graphs are marked until model selection.
    for row in train_map.values():g53.mark_truth(row)
    training={size:[train_map[sha] for pair in stage_pairs[size]
                     for sha in pair['sha256']] for size in SIZES}
    validation=[train_map[sha] for pair in validation_pairs for sha in pair['sha256']]
    if len(validation)!=16 or any(len(training[size])!=size for size in SIZES):
        refuse('TRAIN_VALIDATION_GRAPH_COUNTS')
    if any(x['truth_count']!=len(x['positive']) for x in validation+training[80]):
        refuse('TRAIN_VALIDATION_NOT_CANDIDATE_REACHABLE')
    holdouts={key:[s for s in samples if s['section']==key]
        for key in ('counterfactual_probe','fresh_counterfactual_probe',FINAL_GROUP)}
    return training,validation,validation_pairs,stage_pairs,holdouts


def _infer(model,sample):
    # Models never receive labels, positive indices or graph targets.
    features={k:v for k,v in sample.items() if k not in
              ('positive','truth_count','labels','edges')}
    with torch.inference_mode():
        result=p55.infer_edges(model,features,'set_prediction')
    if (not isinstance(result,set) or
        any(not isinstance(i,int) or not 0<=i<sample['edge_count'] for i in result)):
        refuse('INVALID_MODEL_OUTPUT')
    return result


def evaluate(model,rows,pair_list,section,*,details=False):
    by_sha={x['prompt_sha256']:x for x in rows}
    if len(by_sha)!=len(rows) or _ids(pair_list)!=set(by_sha):
        refuse('EVAL_PAIR_MAPPING_'+section)
    results=[]
    model.eval()
    for sample in rows:
        predicted=_infer(model,sample)  # prediction comes BEFORE reading truth
        gold=set(sample['positive'].tolist())
        hits=len(predicted&gold)
        missing=sample['truth_count']-hits
        extra=len(predicted-gold)
        result={'section':section,'prompt_sha256':sample['prompt_sha256'],
            'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
            'gold_count':sample['truth_count'],'gold_reachable':len(gold),
            'predicted_count':len(predicted),'true_positive':hits,
            'missing':missing,'extra':extra,
            'exact':missing==0 and extra==0,
            'candidate_reachable':len(gold)==sample['truth_count']}
        if details:
            result.update(predicted_edge_indices=sorted(predicted),
                          false_negative_indices=sorted(gold-predicted),
                          false_positive_indices=sorted(predicted-gold))
            # Post-inference category of nearest error; never used for decoding.
            errors=defaultdict(int)
            for idx in gold-predicted:
                d=mem55._nearest_mismatch(sample,idx,sorted(predicted))
                key='+'.join(d['different_fields']) or 'unmatched'
                errors[key]+=1
            result['missing_nearest_field_differences']=dict(errors)
        results.append(result)
    record_map={r['prompt_sha256']:r for r in results}
    stats={'cases':len(results),'candidate_reachable':sum(r['candidate_reachable'] for r in results),
           'exact':sum(r['exact'] for r in results),'missing':sum(r['missing'] for r in results),
           'extra':sum(r['extra'] for r in results),
           'gold_edges':sum(r['gold_count'] for r in results),
           'true_positive':sum(r['true_positive'] for r in results),
           'predicted_edges':sum(r['predicted_count'] for r in results),
           'exact_pairs':sum(all(record_map[sha]['exact'] for sha in p['sha256']) for p in pair_list),
           'pairs':len(pair_list)}
    stats['edge_recall']=round(stats['true_positive']/stats['gold_edges'],6) if stats['gold_edges'] else 0
    stats['edge_precision']=round(stats['true_positive']/stats['predicted_edges'],6) if stats['predicted_edges'] else 0
    stats['families']={fam:{'cases':sum(r['family']==fam for r in results),
        'exact':sum(r['exact'] for r in results if r['family']==fam),
        'missing':sum(r['missing'] for r in results if r['family']==fam),
        'extra':sum(r['extra'] for r in results if r['family']==fam)}
        for fam in ('allocation','encounter')}
    stats['modes']={mode:{'cases':sum(r['mode']==mode for r in results),
        'exact':sum(r['exact'] for r in results if r['mode']==mode)}
        for mode in ('renaming','role_swap')}
    return stats,results


def _quality(metrics,step):
    # Validation only. Larger is better; prefer earlier step after all ties.
    return (metrics['exact'],-metrics['missing']-metrics['extra'],
            -metrics['missing'],metrics['exact_pairs'],-step)


def _copy_state(model):
    return {k:v.detach().cpu().contiguous().clone() for k,v in model.state_dict().items()}


def _load_state(base):
    net=p55.model_for('set_prediction')
    net.load_state_dict(base,strict=True)
    return net


def training_preflight(base,device):
    if device not in ('cpu','cuda') or (device=='cuda' and not torch.cuda.is_available()):
        refuse('TRAIN_DEVICE_UNAVAILABLE')
    sample={'node':torch.randn(5,96,dtype=torch.float16),
        'kinds':torch.zeros(5,dtype=torch.long),'margin':torch.zeros(5),
        'src':torch.tensor([0,0,1,1,2,2],dtype=torch.long),
        'dst':torch.tensor([1,2,2,3,3,4],dtype=torch.long),
        'relation':torch.tensor([0,1,0,1,0,1],dtype=torch.long),
        'positive':torch.tensor([0,3]),'truth_count':2,'edge_count':6}
    model=_load_state(base).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    loss=p55.set_objective(model,sample)
    if not bool(torch.isfinite(loss)):refuse('PREFLIGHT_LOSS')
    loss.backward()
    if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in model.parameters()):
        refuse('PREFLIGHT_GRADIENT')
    opt.step()
    chosen=_infer(model.eval(),sample)
    if any(not 0<=idx<6 for idx in chosen):refuse('PREFLIGHT_INFERENCE')
    print('GAMER_G2_0056_DEVICE_PREFLIGHT '+json.dumps({'device':device,
        'optimizer_steps':1,'status':'PASS','architecture':'set_prediction'},sort_keys=True),flush=True)


def train_stage(base,train,train_pairs,validation,val_pairs,*,size,device,steps,interval,batch):
    model=_load_state(base).to(device).eval()
    initial,ignored=evaluate(model,validation,val_pairs,'validation')
    best=initial;best_at=0;best_state=_copy_state(model)
    checkpoints=[{'step':0,'validation':initial}]
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    gen=torch.Generator().manual_seed(p55.SEED+5600)
    model.train()
    for step in range(1,steps+1):
        order=torch.randperm(len(train),generator=gen)[:batch].tolist()
        opt.zero_grad(set_to_none=True)
        objective=torch.stack([p55.set_objective(model,train[i]) for i in order]).mean()
        if not bool(torch.isfinite(objective)):refuse('NONFINITE_LOSS')
        objective.backward()
        grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.0)
        if not bool(torch.isfinite(grad)):refuse('NONFINITE_GRADIENT')
        opt.step()
        if step%interval==0 or step==steps:
            model.eval()
            observed,_=evaluate(model,validation,val_pairs,'validation')
            checkpoints.append({'step':step,'loss_last_batch':round(float(objective.detach().cpu()),6),
                                'validation':observed})
            if _quality(observed,step)>_quality(best,best_at):
                best=observed;best_at=step;best_state=_copy_state(model)
            print('GAMER_G2_0056_SCALE_CHECK '+json.dumps({'size':size,'step':step,
                'exact_validation':observed['exact'],'recall':observed['edge_recall'],
                'missing':observed['missing'],'extra':observed['extra'],
                'best_step':best_at},sort_keys=True),flush=True)
            model.train()
    selected=_load_state(best_state).to(device).eval().requires_grad_(False)
    train_stats,train_rows=evaluate(selected,train,train_pairs,'train_'+str(size),details=True)
    val_stats,val_rows=evaluate(selected,validation,val_pairs,'validation_'+str(size),details=True)
    return {'size':size,'steps':steps,'best_step':best_at,'validation':val_stats,
            'train':train_stats,'checkpoints':checkpoints},best_state,train_rows+val_rows


def learn_and_select(base,training,validation,val_pairs,stage_pairs,*,device,steps,interval,batch):
    """Input deliberately contains no probe/final samples; selection is isolated."""
    if (not 1<=steps<=MAX_STEPS or not 1<=interval<=steps or
        batch not in (2,4,8) or device not in ('cpu','cuda')):
        refuse('TRAINING_BUDGET')
    results={};weights={};ledger=[]
    for size in SIZES:
        result,best,rows=train_stage(base,training[size],stage_pairs[size],
            validation,val_pairs,size=size,device=device,
            steps=steps,interval=interval,batch=batch)
        results[str(size)]=result;ledger.extend(rows)
        weights.update({'stage_'+str(size)+'.'+name:tensor for name,tensor in best.items()})
    chosen=max(SIZES,key=lambda size:(_quality(results[str(size)]['validation'],
                                              results[str(size)]['best_step']),-size))
    if chosen not in SIZES:refuse('NO_SELECTED_STAGE')
    selected=_load_state({name:weights['stage_'+str(chosen)+'.'+name]
                          for name in base}).to(device).eval().requires_grad_(False)
    return results,chosen,weights,ledger,selected


def finalize(samples,pairs,replay,report,*,device,base,source_hashes,gate_ids,
             encoder_sha,steps,interval,batch):
    if (replay.get('exact_parent50_legacy_replay') is not True or
        report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True):
        refuse('PARENT52_REPLAY')
    training,validation,val_pairs,stage_pairs,holdouts=arrange_cohorts(samples,pairs,gate_ids)
    # Entire model selection happens in the next call. holdouts are never
    # passed into the trainer or the stage comparison.
    results,chosen,weights,ledger,selected=learn_and_select(base,training,validation,
        val_pairs,stage_pairs,device=device,steps=steps,interval=interval,batch=batch)
    # Only now reveal test labels and evaluate the selected checkpoint once.
    final={};new_ids=_ids(pairs[FINAL_GROUP])
    if len(new_ids)!=FINAL_COUNT:refuse('NEW_TEST_IDENTITIES')
    for section in ('counterfactual_probe','fresh_counterfactual_probe',FINAL_GROUP):
        rows=holdouts[section]
        for row in rows:g53.mark_truth(row)
        metrics,detail=evaluate(selected,rows,pairs[section],section,details=True)
        final[section]=metrics;ledger.extend(detail)
        print('GAMER_G2_0056_FINAL_EVAL '+json.dumps({'section':section,**{
            field:metrics[field] for field in ('cases','exact','missing','extra','edge_recall',
                                                'exact_pairs')}},sort_keys=True),flush=True)
    if len(ledger)>4096:refuse('PREDICTION_LEDGER_BUDGET')
    if not all(not p.requires_grad and p.grad is None for p in selected.parameters()):
        refuse('SELECTED_MODEL_NOT_FROZEN')
    if any(not torch.equal(weights['stage_'+str(chosen)+'.'+k],v.detach().cpu())
           for k,v in selected.state_dict().items()):
        refuse('SELECTED_CHECKPOINT_DRIFT')
    exp={'schema':SCHEMA,'status':STATUS,'production_promotion':False,
        'source_checkpoint':source_hashes,'frozen_encoder_sha256':encoder_sha,
        'cohorts':{'nested_train_graphs':list(SIZES),'validation_graphs':16,
            'validation_origin':'original_0053_train_fit_previously_used_for_other_models',
            'historical_probe_graphs':32,'historical_fresh_probe_graphs':32,
            'new_final_test_graphs':FINAL_COUNT,
            'selection_uses_final_test':False,'selection_uses_historical_probes':False,
            'memorization_gate_ids':sorted(gate_ids),
            'training_ids':sorted(s['prompt_sha256'] for s in training[80]),
            'validation_ids':sorted(s['prompt_sha256'] for s in validation),
            'new_final_test_ids':sorted(new_ids)},
        'training':{'initial_checkpoint_step':320,'independent_arms_from_same_checkpoint':True,
            'same_optimizer_budget_each':True,'steps_per_stage':steps,'optimizer_steps_total':steps*len(SIZES),
            'batch_prompts':batch,'frozen_g1_and_0052':True,'frozen_0048_encoder':True},
        'scale_results':results,'selected_stage':chosen,
        'selection_basis':'validation_graph_exact_then_edge_errors_then_pair_accuracy_then_earlier_step; smaller_stage_on_ties',
        'final_evaluation':final,
        'gates':{'g2_certified':False,'sealed_test_used':False,'production_promotion':False,
            'final_new_cohort_evaluated_once':True,
            'next_step':'REVIEW_LEARNING_SCALE_AND_TRANSFER_BEFORE_PROMOTION'},
        '_auxiliary_ledger':{'filename':PREDICTIONS,'rows':ledger}}
    policy={'enabled':False,'research_only':True,'production_promotion':False,
            'sealed_test_used':False,'selected_stage':chosen,
            'validation_selection_only':True,'new_final_test_not_used_for_selection':True}
    return exp,weights,policy


def run(*,diagnosis55_run,candidate55_run,candidate54_run,candidate53_run,candidate52_run,
        candidate51_run,occurrence48_run,counterfactual50_run,output,device='cuda',
        scale_steps=STEPS,checkpoint_interval=INTERVAL,batch_prompts=BATCH,**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('PARENT_COHORT_COUNTS')
    if not 1<=scale_steps<=MAX_STEPS or not 1<=checkpoint_interval<=scale_steps:
        refuse('TRAINING_BUDGET')
    g53.verify_parent52(candidate52_run)
    p54.verify_parent53(candidate53_run,{},candidate52_run)
    p55.verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    baseline,sha,gate_ids=frozen55.verify_frozen_checkpoint(diagnosis55_run,
        candidate55_run,candidate54_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0056_IMMUTABLE_OUTPUT')
    base=_copy_state(baseline)
    training_preflight(base,device)
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    encoder_sha=_sha(weight_path)
    if parent48.get('schema')!=d48.SCHEMA or parent48.get('weights_sha256')!=encoder_sha:
        refuse('PARENT48_HASH')
    saved=load_file(str(weight_path),device='cpu')
    encoder=d48.OccurrenceEvidenceScorer(saved['encoder.norm.weight'].shape[0])
    encoder.load_state_dict(saved,strict=True)
    encoder.eval().requires_grad_(False).to(device)
    encoder_state=_copy_state(encoder)
    def features(raw,expanded,section):
        # The 0053 frozen-feature extractor predated this new cohort name;
        # only its section validator is historical. Retain identical features.
        compatible=('fresh_counterfactual_probe' if section==FINAL_GROUP else section)
        result=g53.frozen_features(raw,expanded,encoder,compatible)
        result['section']=section
        return result
    def finish(cases,pair_sets,replay,report):
        outcome=finalize(cases,pair_sets,replay,report,device=device,base=base,
            source_hashes=sha,gate_ids=gate_ids,encoder_sha=encoder_sha,
            steps=scale_steps,interval=checkpoint_interval,batch=batch_prompts)
        if any(not torch.equal(value,encoder.state_dict()[name].detach().cpu())
               for name,value in encoder_state.items()):
            refuse('FROZEN_0048_MUTATION')
        return outcome
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,final_test_pairs=16,**kwargs)
