"""G2-0057: four-arm, matched-budget counterfactual set-prediction bake-off.

Starts from the G2-0056 validation-selected 80-graph set predictor. All arms
observe the same 40 authored TRAIN pairs and get the same optimizer schedule.
Only training graph annotations establish a *canonical node-ID alignment*;
validation/test inference is gold-blind. A/B/C/D respectively use supervised
set loss alone, renaming correspondence, role-swap correspondence, or both.
The labels in the authored role-swap pairs retain canonical graph identities:
the intervention changes textual roles, not the directed semantic gold edges.
An independently authored final 16 pairs (32 graphs) are exposed ONCE after
validation selects the arm/checkpoint. No sealed test, no production promotion.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import torch
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_learning_scale_0056 as p56
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_frozen_generalization_0055 as frozen55
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52

SCHEMA='nanojev.gamer.graph.g2.counterfactual-bakeoff-0057.v1'
STATUS='G2_0057_COUNTERFACTUAL_BAKEOFF_RESEARCH_NOT_CERTIFIED'
REPORT='g2_0057_counterfactual_bakeoff_report.json'
LEDGER='g2_0057_counterfactual_candidate_per_case.jsonl.gz'
PREDICTIONS='g2_0057_counterfactual_predictions.jsonl.gz'
WEIGHTS='g2_0057_counterfactual_models.safetensors'
POLICY='g2_0057_counterfactual_policy.json'
ARMS=('A_control','B_renaming','C_role_swap','D_combined')
TRAIN_GROUP='counterfactual_train'
FINAL_GROUP=p56.FINAL_GROUP
STEPS=960
INTERVAL=120
PAIRS_PER_BATCH=2
MAX_STEPS=4800
PAIR_WEIGHT=0.30
NEW_FINAL_PAIRS=16
PREVIOUS_FINAL_PAIRS=16
CELLS=p56.CELLS


def refuse(message):
    raise ValueError('GAMER_G2_0057_'+message)


def _sha(path):
    return d48.backbone.sha256(path)


def verify_parent56(folder):
    """Require the *actual* 0056 validated 80-scale checkpoint and receipts."""
    folder=Path(folder)
    report=json.loads((folder/p56.REPORT).read_text(encoding='utf-8'))
    exp=report.get('scoring_experiment',{})
    required={p56.REPORT:None,p56.LEDGER:'ledger_sha256',
              p56.WEIGHTS:'scoring_weights_sha256',p56.POLICY:'scoring_policy_sha256'}
    hashes={}
    for filename,field in required.items():
        digest=_sha(folder/filename)
        hashes[filename]=digest
        if field and report.get(field)!=digest:refuse('PARENT56_HASH_'+filename)
    aux=exp.get('auxiliary_prediction_ledger',{})
    # Parent 0056 contains a second full prediction ledger in the scoring experiment.
    if aux.get('filename')!=p56.PREDICTIONS or aux.get('sha256')!=_sha(folder/p56.PREDICTIONS):
        refuse('PARENT56_PREDICTION_HASH')
    hashes[p56.PREDICTIONS]=aux['sha256']
    parent_cohorts=exp.get('cohorts',{})
    if (len(parent_cohorts.get('memorization_gate_ids',[]))!=8 or
        len(parent_cohorts.get('training_ids',[]))!=80 or
        len(parent_cohorts.get('validation_ids',[]))!=16 or
        len(parent_cohorts.get('new_final_test_ids',[]))!=32):
        refuse('PARENT56_COHORT_LEDGER')
    if (report.get('schema')!=p56.SCHEMA or report.get('status')!=p56.STATUS
        or report.get('production_promotion') is not False
        or report.get('sealed_test_used') is not False
        or exp.get('selected_stage')!=80
        or exp.get('gates',{}).get('final_new_cohort_evaluated_once') is not True
        or exp.get('gates',{}).get('production_promotion') is not False
        or exp.get('training',{}).get('steps_per_stage')!=960
        or exp.get('training',{}).get('optimizer_steps_total')!=3840
        or exp.get('scale_results',{}).get('80',{}).get('best_step')!=720
        or exp.get('cohorts',{}).get('validation_graphs')!=16
        or len(exp.get('cohorts',{}).get('new_final_test_ids',[]))!=32):
        refuse('PARENT56_EXPERIMENT_CONTRACT')
    model=p55.model_for('set_prediction')
    data=load_file(str(folder/p56.WEIGHTS),device='cpu')
    prefix='stage_80.'
    keys=set(model.state_dict())
    found={name[len(prefix):]:value for name,value in data.items() if name.startswith(prefix)}
    if set(found)!=keys or any(not bool(torch.isfinite(value).all()) for value in found.values()):
        refuse('PARENT56_CHECKPOINT_SHAPE_OR_FINITE')
    model.load_state_dict(found,strict=True)
    return p56._copy_state(model),hashes,report


def training_node_alignment(row,expanded,score_case):
    """Training-only span-to-canonical-ID map, never passed to the decoder.

    The author graph has identical node IDs and edge semantics across a pair.
    Graph labels/IDs are NEVER used for inference or candidate generation.
    """
    nodes=row['graph']['nodes']
    spans={}
    for n in nodes:
        if 'span' not in n:continue
        key=(*n['span'],n['kind'])
        if key in spans:refuse('DUPLICATE_AUTHOR_SPAN')
        spans[key]=n['id']
    anchors=expanded['anchors']
    if len(anchors)!=score_case['node'].shape[0]:refuse('ALIGNMENT_NODE_DIM')
    identifiers=[spans.get(a) for a in anchors]
    if len([v for v in identifiers if v is not None])!=len(set(v for v in identifiers if v is not None)):
        refuse('DUPLICATE_ALIGNED_AUTHOR_NODE')
    return {'training_node_ids':tuple(identifiers)}


def canonical_edges(sample):
    ids=sample.get('training_node_ids')
    if not isinstance(ids,tuple) or len(ids)!=sample['node'].shape[0]:
        refuse('TRAIN_ALIGNMENT_MISSING')
    result={}
    for edge in sample['positive'].tolist():
        src=ids[int(sample['src'][edge])];dst=ids[int(sample['dst'][edge])]
        if src is None or dst is None:refuse('TRAIN_GOLD_UNMAPPED_AUTHOR_NODE')
        key=(src,dst,int(sample['relation'][edge]))
        if key in result:refuse('DUPLICATE_SEMANTIC_GOLD_EDGE')
        result[key]=edge
    if len(result)!=sample['truth_count'] or len(result)!=len(sample['positive']):
        refuse('TRAIN_GOLD_NOT_REACHABLE')
    return result


def _matched_set_loss(forward,sample):
    """Exact G2-0055 supervised objective plus its detached gold slot assignment.

    This mirrors p55.set_objective so the control is a matched continuation,
    while pair arms reuse the same assignment without a second forward pass.
    """
    rel,src,dst=forward
    dev=rel.device
    gold=sample['positive'].to('cpu')
    if not len(gold) or len(gold)>p55.SLOTS:refuse('SET_GOLD_CAPACITY')
    sg=sample['src'].index_select(0,gold).to(dev)
    dg=sample['dst'].index_select(0,gold).to(dev)
    rg=sample['relation'].index_select(0,gold).to(dev)+1
    lr=F.log_softmax(rel,-1);ls=F.log_softmax(src,-1);ld=F.log_softmax(dst,-1)
    cost=-(lr[:,rg]+ls[:,sg]+ld[:,dg]).detach().cpu().numpy()
    slots,targets=p55.rectangular_assignment(cost)
    a=torch.as_tensor(slots,dtype=torch.long,device=dev)
    b=torch.as_tensor(targets,dtype=torch.long,device=dev)
    desired=torch.zeros(p55.SLOTS,dtype=torch.long,device=dev)
    desired[a]=rg[b]
    cls=F.cross_entropy(rel,desired,reduction='none')
    remaining=torch.ones(p55.SLOTS,dtype=torch.bool,device=dev)
    remaining[a]=False
    loss=cls[a].mean()+.2*cls[remaining].mean() if bool(remaining.any()) else cls[a].mean()
    loss+=F.cross_entropy(src[a],sg[b])+F.cross_entropy(dst[a],dg[b])
    assignment={int(gold[int(target)]):int(slot) for slot,target in zip(slots,targets)}
    if len(assignment)!=len(gold):refuse('ASSIGNMENT_CARDINALITY')
    return loss,assignment


def _project_nodes(logits,node_ids,canon):
    """Project different surface node inventories onto shared logical IDs.

    Extra frozen-G1 anchors are mapped to one OTHER bucket; source/destination
    equivariance is measured in authored logical-role space, NOT span index.
    """
    dev=logits.device
    classes={identifier:i for i,identifier in enumerate(canon)}
    loc=torch.as_tensor([classes.get(i,len(canon)) for i in node_ids],
                        dtype=torch.long,device=dev)
    probs=F.softmax(logits,dim=-1)
    projected=torch.zeros(probs.shape[0],len(canon)+1,device=dev)
    projected.scatter_add_(1,loc.unsqueeze(0).expand(probs.shape[0],-1),probs)
    return projected


def _sym_kl(p,q):
    eps=1e-8
    return .5*((p*(p.clamp_min(eps).log()-q.clamp_min(eps).log())).sum(-1)
             +(q*(q.clamp_min(eps).log()-p.clamp_min(eps).log())).sum(-1))


def paired_consistency(left,right,fl,fr,assign_l,assign_r):
    """Canonical-ID aligned, permutation-invariant relation and role agreement."""
    lg=canonical_edges(left);rg=canonical_edges(right)
    if set(lg)!=set(rg):refuse('AUTHOR_PAIRED_SEMANTICS_CHANGED')
    canon=sorted(set(x for x in left['training_node_ids']+right['training_node_ids'] if x is not None))
    if not canon:refuse('CANONICAL_NODE_EMPTY')
    rl,sl,dl=fl;rr,sr,dr=fr
    pl=F.softmax(rl,-1);pr=F.softmax(rr,-1)
    psl=_project_nodes(sl,left['training_node_ids'],canon)
    psr=_project_nodes(sr,right['training_node_ids'],canon)
    pdl=_project_nodes(dl,left['training_node_ids'],canon)
    pdr=_project_nodes(dr,right['training_node_ids'],canon)
    losses=[]
    for key in sorted(lg):
        il=assign_l[lg[key]];ir=assign_r[rg[key]]
        losses.append((_sym_kl(pl[il],pr[ir])
                       +_sym_kl(psl[il],psr[ir])
                       +_sym_kl(pdl[il],pdr[ir]))/3)
    return torch.stack(losses).mean()


def arm_uses_pair(arm,mode):
    if arm not in ARMS or mode not in ('renaming','role_swap'):refuse('ARM_OR_MODE')
    return arm=='D_combined' or (arm=='B_renaming' and mode=='renaming') or (
        arm=='C_role_swap' and mode=='role_swap')


def batch_objective(model,chosen_pairs,rows_by_sha,arm):
    losses=[];paired=[]
    for pair in chosen_pairs:
        a,b=(rows_by_sha[sha] for sha in pair['sha256'])
        fl=model(a);fr=model(b)
        la,aa=_matched_set_loss(fl,a);lb,ab=_matched_set_loss(fr,b)
        losses.extend([la,lb])
        if arm_uses_pair(arm,pair['mode']):
            paired.append(paired_consistency(a,b,fl,fr,aa,ab))
    loss=torch.stack(losses).mean()
    if paired:
        # Divide by batch PAIRS (not only active pairs), so B/C introduce the
        # same consistency budget per eligible transformation as D.
        loss+=PAIR_WEIGHT*torch.stack(paired).sum()/len(chosen_pairs)
    return loss


def pair_batches(pairs,steps,seed):
    """Common, pair-complete, evenly mixed schedule for all four arms."""
    if not pairs or len(pairs)%PAIRS_PER_BATCH:refuse('PAIR_BATCH_COUNTS')
    gen=torch.Generator().manual_seed(seed)
    order=[];offset=0
    for _ in range(steps):
        if offset+PAIRS_PER_BATCH>len(order):
            order=torch.randperm(len(pairs),generator=gen).tolist();offset=0
        chosen=order[offset:offset+PAIRS_PER_BATCH];offset+=PAIRS_PER_BATCH
        yield [pairs[i] for i in chosen]


def _safe_evaluate(model,rows,pairs,section,*,details=False):
    # Author alignment is training-only and MUST NOT cross the inference boundary.
    sanitized=[{k:v for k,v in row.items() if k!='training_node_ids'} for row in rows]
    metrics,records=p56.evaluate(model,sanitized,pairs,section,details=details)
    # Cardinality is a distinct source of graph-level failures. Preserve it
    # even when decoder edge recall looks promising.
    # Detailed false-edge index lists are optional; cardinality is not.
    count_l1=sum(abs(row['gold_count']-row['predicted_count']) for row in records)
    metrics['count_error_l1']=count_l1
    metrics['mean_abs_cardinality_error']=round(count_l1/len(records),6) if records else 0
    return metrics,records


def quality(metrics,step):
    return p56._quality(metrics,step)


def train_arm(base,train_pairs,train_map,validation,val_pairs,*,arm,device,steps,interval):
    model=p56._load_state(base).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    model.eval()
    initial,_=_safe_evaluate(model,validation,val_pairs,'validation')
    best,best_step,best_state=initial,0,p56._copy_state(model)
    checkpoints=[{'step':0,'validation':initial}]
    model.train()
    for step,batch in enumerate(pair_batches(train_pairs,steps,p55.SEED+5700),start=1):
        opt.zero_grad(set_to_none=True)
        loss=batch_objective(model,batch,train_map,arm)
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_OBJECTIVE_'+arm)
        loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT_'+arm)
        opt.step()
        if step%interval==0 or step==steps:
            model.eval()
            observed,_=_safe_evaluate(model,validation,val_pairs,'validation')
            if quality(observed,step)>quality(best,best_step):
                best,best_step,best_state=observed,step,p56._copy_state(model)
            checkpoints.append({'step':step,'last_batch_loss':round(float(loss.detach().cpu()),6),
                                'validation':observed})
            print('GAMER_G2_0057_VALIDATION '+json.dumps({'arm':arm,'step':step,
                'exact':observed['exact'],'exact_pairs':observed['exact_pairs'],
                'recall':observed['edge_recall'],'missing':observed['missing'],
                'extra':observed['extra'],'best_step':best_step},sort_keys=True),flush=True)
            model.train()
    selected=p56._load_state(best_state).to(device).eval().requires_grad_(False)
    rows=[train_map[sha] for p in train_pairs for sha in p['sha256']]
    train_metrics,training_records=_safe_evaluate(selected,rows,train_pairs,'train_'+arm,details=True)
    validation_metrics,validation_records=_safe_evaluate(selected,validation,val_pairs,'validation_'+arm,details=True)
    return {'arm':arm,'best_step':best_step,'optimizer_steps':steps,
            'training':train_metrics,'validation':validation_metrics,
            'checkpoints':checkpoints},best_state,training_records+validation_records


def prepare_cohorts(samples,pairs,gate_ids,parent_cohorts=None):
    if set(pairs)!={TRAIN_GROUP,'counterfactual_probe','fresh_counterfactual_probe',FINAL_GROUP}:
        refuse('COHORT_SET')
    if tuple(len(pairs[s]) for s in (TRAIN_GROUP,'counterfactual_probe',
             'fresh_counterfactual_probe',FINAL_GROUP))!=(48,16,16,32):
        refuse('COHORT_PAIR_COUNTS')
    grouped=defaultdict(list)
    for p in pairs[TRAIN_GROUP]:grouped[(p['family'],p['mode'])].append(p)
    if set(grouped)!=set(CELLS) or any(len(grouped[cell])!=12 for cell in CELLS):
        refuse('TRAIN_CELL_BALANCE')
    val_pairs=[p for cell in CELLS for p in grouped[cell][:2]]
    train_pairs=[p for cell in CELLS for p in grouped[cell][2:]]
    if p56._ids([grouped[cell][2] for cell in CELLS])!=set(gate_ids):
        refuse('ORIGINAL_0055_GATE_DRIFT')
    if len(train_pairs)!=40 or len(val_pairs)!=8:refuse('TRAIN_VALIDATION_SPLIT')
    original_final=pairs[FINAL_GROUP][:PREVIOUS_FINAL_PAIRS]
    new_final=pairs[FINAL_GROUP][PREVIOUS_FINAL_PAIRS:]
    if parent_cohorts is not None and any(
        set(parent_cohorts.get(field,()))!=p56._ids(group)
        for field,group in (
            ('training_ids',train_pairs),('validation_ids',val_pairs),
            ('new_final_test_ids',original_final),
            ('memorization_gate_ids',[grouped[cell][2] for cell in CELLS]))):
        refuse('PARENT56_COHORT_DRIFT')
    cohorts=[val_pairs,train_pairs,pairs['counterfactual_probe'],
             pairs['fresh_counterfactual_probe'],original_final,new_final]
    sets=[p56._ids(g) for g in cohorts]
    if any(len(s)!=2*len(g) for s,g in zip(sets,cohorts)) or any(sets[i]&sets[j]
            for i in range(len(sets)) for j in range(i+1,len(sets))):
        refuse('PROMPT_SPLIT_LEAK')
    for section,expected in ((TRAIN_GROUP,96),('counterfactual_probe',32),
                             ('fresh_counterfactual_probe',32),(FINAL_GROUP,64)):
        found=[s for s in samples if s['section']==section]
        if len(found)!=expected or {s['prompt_sha256'] for s in found}!=p56._ids(pairs[section]):
            refuse('CAPTURE_IDENTITY_'+section)
    rows={s['prompt_sha256']:s for s in samples if s['section']==TRAIN_GROUP}
    for row in rows.values():g53.mark_truth(row)
    train_map={sha:rows[sha] for pair in train_pairs for sha in pair['sha256']}
    validation=[rows[sha] for p in val_pairs for sha in p['sha256']]
    if any(row['truth_count']!=len(row['positive']) for row in train_map.values()):
        refuse('TRAIN_CANDIDATE_UNREACHABLE')
    if any('training_node_ids' not in row for row in train_map.values()):
        refuse('AUTHOR_ALIGNMENT_NOT_CAPTURED')
    # Training-only transformation contract: authored canonical edges agree
    # across each pair; do NOT infer this from lexical span position.
    for pair in train_pairs:
        a,b=(train_map[sha] for sha in pair['sha256'])
        if canonical_edges(a).keys()!=canonical_edges(b).keys():
            refuse('PAIR_AUTHOR_SEMANTIC_DRIFT')
    # Expose test rows but NEVER mark their truth until after selection.
    holdouts={s:[x for x in samples if x['section']==s] for s in
              ('counterfactual_probe','fresh_counterfactual_probe',FINAL_GROUP)}
    return train_pairs,train_map,validation,val_pairs,holdouts,original_final,new_final


def preflight(base,device):
    if device not in ('cpu','cuda') or device=='cuda' and not torch.cuda.is_available():
        refuse('PREFLIGHT_DEVICE')
    # Reproduce a real paired backward path, including Hungarian and ID-space
    # alignment, before costly frozen-Qwen capture.
    torch.manual_seed(460557)
    node=torch.randn(5,96,dtype=torch.float16)
    src=torch.tensor([0,0,1,2,3,1,0,2],dtype=torch.long)
    dst=torch.tensor([1,2,3,4,4,4,4,1],dtype=torch.long)
    rel=torch.tensor([0,1,0,1,0,1,0,1],dtype=torch.long)
    left={'node':node,'kinds':torch.tensor([0,0,1,1,1]),
        'margin':torch.zeros(5),'src':src,'dst':dst,'relation':rel,
        'positive':torch.tensor([0,4]),'edge_count':8,'truth_count':2,
        'training_node_ids':('A','B','C','D','E')}
    order=[1,0,2,4,3]
    inverse={old:i for i,old in enumerate(order)}
    right={**left,'node':(node[order].float()+.07*torch.randn(5,96)).half().contiguous(),
           'kinds':left['kinds'][order],
           'src':torch.tensor([inverse[int(v)] for v in src]),
           'dst':torch.tensor([inverse[int(v)] for v in dst]),
           'training_node_ids':tuple(left['training_node_ids'][i] for i in order)}
    # Both transformation types are present, so each arm's *additional*
    # objective (including B_renaming) is actually exercised on CUDA/CPU.
    paired=[{'sha256':('l','r'),'mode':mode} for mode in ('renaming','role_swap')]
    right_map={'l':left,'r':right}
    for arm in ARMS:
        model=p56._load_state(base).to(device)
        opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
        opt.zero_grad(set_to_none=True)
        loss=batch_objective(model,paired,right_map,arm)
        if not bool(torch.isfinite(loss)):refuse('PREFLIGHT_LOSS_'+arm)
        loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all())
               for p in model.parameters()):refuse('PREFLIGHT_GRADIENT_'+arm)
        opt.step()
        predicted=p56._infer(model.eval(),left)
        if not isinstance(predicted,set):refuse('PREFLIGHT_PREDICTION_'+arm)
    print('GAMER_G2_0057_DEVICE_PREFLIGHT '+json.dumps({'status':'PASS',
        'device':device,'arms':list(ARMS),'real_paired_gradient':True},sort_keys=True),flush=True)


def train_and_select(base,train_pairs,train_map,validation,val_pairs,*,device,steps,interval):
    if not 1<=steps<=MAX_STEPS or not 1<=interval<=steps:refuse('TRAINING_BUDGET')
    records={};weights={};ledger=[]
    for arm in ARMS:
        result,state,detail=train_arm(base,train_pairs,train_map,validation,val_pairs,
            arm=arm,device=device,steps=steps,interval=interval)
        records[arm]=result
        weights.update({arm+'.'+key:value for key,value in state.items()})
        ledger.extend(detail)
    # No probe/final rows enter this call. Ties favor control/less intervention.
    chosen=max(ARMS,key=lambda arm:(quality(records[arm]['validation'],
                                             records[arm]['best_step']),-ARMS.index(arm)))
    model=p56._load_state({key:weights[chosen+'.'+key] for key in base}).to(device)
    model.eval().requires_grad_(False)
    return chosen,records,weights,ledger,model


def finalize(samples,pairs,replay,report,*,device,base,hashes,gate_ids,
             encoder_sha,steps,interval,parent_cohorts=None):
    if (replay.get('exact_parent50_legacy_replay') is not True or
        report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True):
        refuse('PARENT52_REPLAY')
    (train_pairs,train_map,validation,val_pairs,holdouts,previous_pairs,
     new_pairs)=prepare_cohorts(samples,pairs,gate_ids,parent_cohorts)
    chosen,arms,weights,ledger,selected=train_and_select(base,train_pairs,train_map,
        validation,val_pairs,device=device,steps=steps,interval=interval)
    # After arm/checkpoint choice: evaluate historical probes and previous final
    # cohort, then the new 0057 cohort ONCE. No further fitting/selection.
    final={}
    for section,pair_list in (('counterfactual_probe',pairs['counterfactual_probe']),
        ('fresh_counterfactual_probe',pairs['fresh_counterfactual_probe']),
        ('previous_0056_final',previous_pairs),('new_0057_final',new_pairs)):
        source=FINAL_GROUP if section in ('previous_0056_final','new_0057_final') else section
        scoped=p56._ids(pair_list)
        rows=[row for row in holdouts[source] if row['prompt_sha256'] in scoped]
        if len(rows)!=len(scoped):refuse('FINAL_TEST_ROW_COUNT_'+section)
        for row in rows:g53.mark_truth(row)
        stats,detail=_safe_evaluate(selected,rows,pair_list,section,details=True)
        final[section]=stats;ledger.extend(detail)
        print('GAMER_G2_0057_FINAL_EVAL '+json.dumps({'section':section,
            'exact':stats['exact'],'exact_pairs':stats['exact_pairs'],
            'recall':stats['edge_recall'],'missing':stats['missing'],
            'extra':stats['extra']},sort_keys=True),flush=True)
    if len(ledger)>4096:refuse('LEDGER_BUDGET')
    if any(p.requires_grad or p.grad is not None for p in selected.parameters()):
        refuse('SELECTED_MODEL_GRADIENT')
    exp={'schema':SCHEMA,'status':STATUS,'production_promotion':False,
        'g2_certified':False,'sealed_test_used':False,
        'source_parent56_hashes':hashes,'frozen_encoder_sha256':encoder_sha,
        'training':{'initial_checkpoint':'0056_selected_stage_80_step_720',
            'steps_per_arm':steps,'optimizer_steps_total':steps*len(ARMS),
            'pairs_per_batch':PAIRS_PER_BATCH,'paired_consistency_weight':PAIR_WEIGHT,
            'same_training_pairs_and_batch_order':True,
            'frozen_g1_0052_encoder':True,'no_validation_or_test_gradients':True,
            'canonical_ids_training_only':True},
        'cohorts':{'train_graphs':80,'train_pairs':40,'validation_graphs':16,
            'validation_pairs':8,'historical_probe_graphs':32,
            'fresh_probe_graphs':32,'previous_0056_final_graphs':32,
            'new_0057_final_graphs':32,'new_final_test_scored_after_selection':True,
            'train_sha256':sorted(train_map),
            'validation_sha256':sorted(x['prompt_sha256'] for x in validation),
            'new_final_sha256':sorted(p56._ids(new_pairs))},
        'arms':arms,'selected_arm':chosen,'selection_basis':
            'validation_only_exact_graph_then_errors_then_pairs_then_earliest; control_tiebreak',
        'final_evaluation_selected_arm_only':final,
        'gates':{'g2_certified':False,'sealed_test_used':False,
            'production_promotion':False,'new_final_test_evaluated_once':True,
            'next_step':'REVIEW_COUNTERFACTUAL_BAKEOFF_TRANSFER_AND_EXACT_GRAPHS'},
        '_auxiliary_ledger':{'filename':PREDICTIONS,'rows':ledger}}
    policy={'enabled':False,'research_only':True,'sealed_test_used':False,
        'production_promotion':False,'selected_development_arm':chosen,
        'new_final_test_not_used_for_selection':True}
    return exp,weights,policy


def run(*,scale56_run,diagnosis55_run,candidate55_run,candidate54_run,
        candidate53_run,candidate52_run,candidate51_run,occurrence48_run,
        counterfactual50_run,output,device='cuda',steps=STEPS,
        checkpoint_interval=INTERVAL,train_pairs=48,probe_pairs=16,**kwargs):
    if train_pairs!=48 or probe_pairs!=16:refuse('PARENT_COHORT_COUNTS')
    if not 1<=steps<=MAX_STEPS or not 1<=checkpoint_interval<=steps:
        refuse('TRAINING_BUDGET')
    g53.verify_parent52(candidate52_run)
    p54.verify_parent53(candidate53_run,{},candidate52_run)
    p55.verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    frozen55.verify_frozen_checkpoint(diagnosis55_run,candidate55_run,candidate54_run)
    base,hashes,previous_report=verify_parent56(scale56_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0057_IMMUTABLE_OUTPUT')
    preflight(base,device)
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    encoder_sha=_sha(Path(occurrence48_run)/d48.WEIGHTS)
    if parent48.get('schema')!=d48.SCHEMA or parent48.get('weights_sha256')!=encoder_sha:
        refuse('PARENT48_HASH')
    tensors=load_file(str(Path(occurrence48_run)/d48.WEIGHTS),device='cpu')
    encoder=d48.OccurrenceEvidenceScorer(tensors['encoder.norm.weight'].shape[0])
    encoder.load_state_dict(tensors,strict=True)
    encoder.eval().requires_grad_(False).to(device)
    frozen_encoder=p56._copy_state(encoder)
    gate_ids=previous_report['scoring_experiment']['cohorts']['memorization_gate_ids']
    def features(raw,expanded,section):
        compatible='fresh_counterfactual_probe' if section==FINAL_GROUP else section
        out=g53.frozen_features(raw,expanded,encoder,compatible)
        out['section']=section
        return out
    def finish(samples,pairs,replay,report):
        result=finalize(samples,pairs,replay,report,device=device,base=base,
            hashes=hashes,gate_ids=gate_ids,encoder_sha=encoder_sha,
            steps=steps,interval=checkpoint_interval,
            parent_cohorts=previous_report['scoring_experiment']['cohorts'])
        if any(not torch.equal(encoder.state_dict()[name].detach().cpu(),value)
               for name,value in frozen_encoder.items()):
            refuse('FROZEN_ENCODER_MUTATION')
        return result
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        interface_training_alignment=training_node_alignment,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,final_test_pairs=32,
        train_pairs=train_pairs,probe_pairs=probe_pairs,**kwargs)
