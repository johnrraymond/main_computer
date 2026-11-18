"""G2-0058: gold-blind binding diagnosis, then matched candidate-aware repair.

Stage 1 (development only) holds the validation examples and the selected
G2-0057 checkpoint fixed. Gold labels enter only *after* native predictions
for auditing and explicitly marked oracle interventions. Stage 2 compares
matched continued training with and without a same-relation, hard-negative
endpoint margin, using the same G2-0057 combined loss and data schedule.
The new final holdout is untouched until both models and checkpoint selection
have been frozen. This is research-only: no sealed test and no promotion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

import torch
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_learning_scale_0056 as p56
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_frozen_generalization_0055 as frozen55
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52

SCHEMA='nanojev.gamer.graph.g2.binding-repair-0058.v1'
STATUS='G2_0058_BINDING_REPAIR_RESEARCH_NOT_CERTIFIED'
REPORT='g2_0058_binding_repair_report.json'
LEDGER='g2_0058_binding_candidate_per_case.jsonl.gz'
PREDICTIONS='g2_0058_binding_predictions.jsonl.gz'
WEIGHTS='g2_0058_binding_models.safetensors'
POLICY='g2_0058_binding_policy.json'
ARMS=('A_continue','B_hard_binding')
STEPS=960
INTERVAL=120
MAX_STEPS=4800
HARD_WEIGHT=.25
HARD_MARGIN=.5
HARD_TOPK=8
FINAL_NEW_PAIRS=16
FINAL_ALL_PAIRS=48


def refuse(reason):
    raise ValueError('GAMER_G2_0058_'+reason)


def _sha(path):
    return d48.backbone.sha256(path)


def verify_parent57(folder):
    folder=Path(folder)
    report=json.loads((folder/p57.REPORT).read_text(encoding='utf-8'))
    exp=report.get('scoring_experiment',{})
    if (report.get('schema')!=p57.SCHEMA or report.get('status')!=p57.STATUS
            or report.get('sealed_test_used') is not False
            or report.get('production_promotion') is not False
            or exp.get('selected_arm')!='D_combined'
            or exp.get('training',{}).get('optimizer_steps_total')!=3840
            or exp.get('gates',{}).get('new_final_test_evaluated_once') is not True
            or exp.get('arms',{}).get('D_combined',{}).get('best_step')!=720
            or len(exp.get('cohorts',{}).get('train_sha256',[]))!=80
            or len(exp.get('cohorts',{}).get('validation_sha256',[]))!=16
            or len(exp.get('cohorts',{}).get('new_final_sha256',[]))!=32):
        refuse('PARENT57_CONTRACT')
    hashes={}
    for filename,field in ((p57.REPORT,None),(p57.LEDGER,'ledger_sha256'),
                           (p57.WEIGHTS,'scoring_weights_sha256'),
                           (p57.POLICY,'scoring_policy_sha256')):
        hashes[filename]=_sha(folder/filename)
        if field and report.get(field)!=hashes[filename]:
            refuse('PARENT57_HASH_'+filename)
    auxiliary=exp.get('auxiliary_prediction_ledger',{})
    if (auxiliary.get('filename')!=p57.PREDICTIONS or
            auxiliary.get('sha256')!=_sha(folder/p57.PREDICTIONS)):
        refuse('PARENT57_PREDICTIONS_HASH')
    hashes[p57.PREDICTIONS]=auxiliary['sha256']
    policy=json.loads((folder/p57.POLICY).read_text(encoding='utf-8'))
    if (policy.get('selected_development_arm')!='D_combined' or
            policy.get('enabled') is not False or
            policy.get('production_promotion') is not False):
        refuse('PARENT57_POLICY')
    model=p55.model_for('set_prediction')
    tensors=load_file(str(folder/p57.WEIGHTS),device='cpu')
    prefix='D_combined.'
    selected={k[len(prefix):]:v for k,v in tensors.items() if k.startswith(prefix)}
    if (set(selected)!=set(model.state_dict()) or
            any(not bool(torch.isfinite(v).all()) or
                v.shape!=model.state_dict()[k].shape for k,v in selected.items())):
        refuse('PARENT57_CHECKPOINT_SHAPE')
    model.load_state_dict(selected,strict=True)
    return p56._copy_state(model),hashes,report


def prepare_cohorts(samples,pairs,parent_report):
    """Re-use exactly the old 0057 cohorts; exclude the newly authored final 32.

    The old 0057 final includes the 0056 final and its own 0057 final. The
    additional last 16 pairs here are not inspected during development.
    """
    old_final=pairs.get(p57.FINAL_GROUP,[])
    if len(old_final)!=FINAL_ALL_PAIRS:refuse('FINAL_PAIR_COUNT')
    old_ids=p56._ids(old_final[:32]);new_ids=p56._ids(old_final[32:])
    if len(old_ids)!=64 or len(new_ids)!=32 or old_ids&new_ids:
        refuse('FINAL_DUPLICATION')
    expected=set(parent_report['scoring_experiment']['cohorts']['new_final_sha256'])
    if expected!=p56._ids(old_final[16:32]):refuse('PARENT57_FINAL_DRIFT')
    old_pairs={**pairs,p57.FINAL_GROUP:old_final[:32]}
    old_samples=[s for s in samples if s['prompt_sha256'] not in new_ids]
    # The gate identities are included in the 0056 report, not the 0057
    # source-parent hash map. Derive the four original cells by their fixed
    # first training pair, exactly as in 0056/0057 and check all parent hashes.
    grouped=defaultdict(list)
    for pair in pairs[p57.TRAIN_GROUP]:grouped[(pair['family'],pair['mode'])].append(pair)
    if set(grouped)!=set(p56.CELLS):refuse('TRAIN_CELL_SET')
    gate_ids=p56._ids([grouped[cell][2] for cell in p56.CELLS])
    (training,train_map,validation,val_pairs,holdouts,previous,old_new)=p57.prepare_cohorts(
        old_samples,old_pairs,gate_ids)
    if (set(train_map)!=set(parent_report['scoring_experiment']['cohorts']['train_sha256'])
        or {x['prompt_sha256'] for x in validation}!=set(
            parent_report['scoring_experiment']['cohorts']['validation_sha256'])):
        refuse('PARENT57_TRAIN_VALIDATION_DRIFT')
    for name,group in (('counterfactual_probe',pairs['counterfactual_probe']),
                       ('fresh_counterfactual_probe',pairs['fresh_counterfactual_probe']),
                       ('old_final',old_final[:32])):
        if p56._ids(group)&new_ids:refuse('FINAL_LEAK_'+name)
    new_rows=[s for s in samples if s['section']==p57.FINAL_GROUP
              and s['prompt_sha256'] in new_ids]
    if len(new_rows)!=32 or {s['prompt_sha256'] for s in new_rows}!=new_ids:
        refuse('NEW_FINAL_CAPTURE_IDENTITY')
    if any('positive' in s or 'training_node_ids' in s for s in new_rows):
        refuse('FINAL_LABEL_LEAK')
    # The fresh test records are deliberately returned *last*, and never
    # passed to diagnosis, model selection or training.
    return training,train_map,validation,val_pairs,holdouts,new_rows,old_final[32:]


def _inference_input(sample):
    return {key:value for key,value in sample.items() if key not in
            ('positive','truth_count','labels','edges','training_node_ids','graph')}


def _decode_logits(logits,sample):
    """Exact reference reproduction of 0055 set decoding, for interventions."""
    rel,src,dst=logits
    rr=F.log_softmax(rel,-1);ss=F.log_softmax(src,-1);dd=F.log_softmax(dst,-1)
    dev=rel.device
    a=sample['src'].to(dev);b=sample['dst'].to(dev)
    labels=sample['relation'].to(dev)+1
    selected={}
    for q in range(p55.SLOTS):
        if int(rel[q].argmax())==0:continue
        scores=ss[q,a]+dd[q,b]+rr[q,labels]
        edge=int(scores.argmax())
        if float(rel[q,labels[edge]])<=float(rel[q,0]):continue
        value=float(scores[edge])
        if edge not in selected or value>selected[edge]:selected[edge]=value
    return set(sorted(selected,key=lambda edge:-selected[edge])[:p55.MAX_EDGE])


def _gold_assignment(logits,sample):
    rel,src,dst=logits;dev=rel.device
    gold=sample['positive'].to('cpu')
    a=sample['src'].index_select(0,gold).to(dev)
    b=sample['dst'].index_select(0,gold).to(dev)
    r=sample['relation'].index_select(0,gold).to(dev)+1
    score=-(F.log_softmax(rel,-1)[:,r]+F.log_softmax(src,-1)[:,a]
            +F.log_softmax(dst,-1)[:,b]).detach().cpu().numpy()
    slots,targets=p55.rectangular_assignment(score)
    return {int(gold[target]):int(slot) for slot,target in zip(slots,targets)}


def _force_one(logits,slot,index):
    logits[slot].fill_(-10000.)
    logits[slot,index]=10000.


def oracle_decode(logits,sample,assignment,what):
    """Post-inference gold interventions, explicitly NONDEPLOYABLE."""
    if what not in ('label','source','destination','endpoints',
                    'matched_active_slots','complete'):
        refuse('UNKNOWN_ORACLE')
    rel,src,dst=[v.detach().clone() for v in logits]
    if what in ('matched_active_slots','complete'):
        rel[:,0]=10000.
        if what=='complete':
            # Absent slots must be null; for complete, force all label logits
            # below null on slots not aligned to a gold triple.
            rel[:,:]=-10000.;rel[:,0]=10000.
    for edge,slot in assignment.items():
        gold_rel=int(sample['relation'][edge])+1
        gold_src=int(sample['src'][edge]);gold_dst=int(sample['dst'][edge])
        if what in ('label','complete'):_force_one(rel,slot,gold_rel)
        elif what=='matched_active_slots':
            rel[slot,0]=-10000.
        if what in ('source','endpoints','complete'):_force_one(src,slot,gold_src)
        if what in ('destination','endpoints','complete'):_force_one(dst,slot,gold_dst)
    return _decode_logits((rel,src,dst),sample)


def diagnose(model,validation,val_pairs,*,details=True):
    """Audit matched gold *after* native gold-blind predictions.

    Matched-slot oracle ceilings are deliberately optimistic because the
    Hungarian assignment itself uses gold. They are NOT model predictions.
    """
    model.eval().requires_grad_(False)
    totals=Counter();oracles=Counter();rows=[]
    oracle_names=('label','source','destination','endpoints','matched_active_slots','complete')
    for sample in validation:
        view=_inference_input(sample)
        with torch.inference_mode():
            native=p55.infer_edges(model,view,'set_prediction')
            logits=model(view)
            if _decode_logits(logits,view)!=native:refuse('REFERENCE_DECODER_DIVERGENCE')
            assignment=_gold_assignment(logits,sample)
            gold=set(sample['positive'].tolist())
            interventions={name:oracle_decode(logits,view,assignment,name)
                           for name in oracle_names}
            entry={'section':'validation_diagnosis','prompt_sha256':sample['prompt_sha256'],
                'family':sample['family'],'mode':sample['mode'],
                'gold_count':sample['truth_count'], 'predicted_count':len(native),
                'baseline_missing':len(gold-native),'baseline_extra':len(native-gold),
                'baseline_exact':native==gold,
                'oracle':{name:{'exact':edges==gold,'missing':len(gold-edges),
                                 'extra':len(edges-gold)} for name,edges in interventions.items()},
                'matched_gold':[]}
            rs,ss,ds=[F.log_softmax(t,-1) for t in logits]
            sc=sample['src'].to(rs.device);dc=sample['dst'].to(rs.device)
            rc=sample['relation'].to(rs.device)+1
            for edge,slot in sorted(assignment.items()):
                gs=int(sample['src'][edge]);gd=int(sample['dst'][edge]);gr=int(sample['relation'][edge])+1
                src_rank=1+int((ss[slot]>ss[slot,gs]).sum())
                dst_rank=1+int((ds[slot]>ds[slot,gd]).sum())
                rel_rank=1+int((rs[slot]>rs[slot,gr]).sum())
                score=ss[slot,sc]+ds[slot,dc]+rs[slot,rc]
                gold_rank=1+int((score>score[edge]).sum())
                same_label=rc==gr
                hard_src=same_label&(dc==gd)&(sc!=gs)
                hard_dst=same_label&(sc==gs)&(dc!=gd)
                src_best=float(score[hard_src].max()-score[edge]) if bool(hard_src.any()) else None
                dst_best=float(score[hard_dst].max()-score[edge]) if bool(hard_dst.any()) else None
                totals.update({'gold':1,'source_wrong':src_rank!=1,
                    'destination_wrong':dst_rank!=1,'relation_wrong':rel_rank!=1,
                    'candidate_not_top':gold_rank!=1,
                    'same_label_hard_src':src_best is not None,
                    'same_label_hard_dst':dst_best is not None})
                entry['matched_gold'].append({'gold_edge_index':edge,'slot':slot,
                    'source_rank':src_rank,'destination_rank':dst_rank,
                    'relation_rank':rel_rank,'admissible_edge_rank':gold_rank,
                    'same_label_wrong_source_margin':round(src_best,5) if src_best is not None else None,
                    'same_label_wrong_destination_margin':round(dst_best,5) if dst_best is not None else None,
                    'recovered_by_native':edge in native})
            for name,edges in interventions.items():
                oracles.update({name+'_exact':edges==gold,name+'_missing':len(gold-edges),
                    name+'_extra':len(edges-gold)})
            totals.update({'graphs':1,'baseline_exact':native==gold,
                'baseline_missing':len(gold-native),'baseline_extra':len(native-gold),
                'candidate_reachable':len(gold)==sample['truth_count']})
            rows.append(entry)
    # Gate a binding-only intervention on actual endpoint evidence, rather
    # than presuming all errors have the same source.
    endpoint_signal=(totals['source_wrong']+totals['destination_wrong']>
                     totals['relation_wrong'] and totals['candidate_not_top']>0 and
                     totals['same_label_hard_src']+totals['same_label_hard_dst']>0)
    result={'graphs':totals['graphs'],'gold_edges':totals['gold'],
        'native':{'exact':totals['baseline_exact'],'missing':totals['baseline_missing'],
                  'extra':totals['baseline_extra']},
        'candidate_reachable':totals['candidate_reachable'],
        'matched_slot_diagnostics':{k:totals[k] for k in ('source_wrong','destination_wrong',
            'relation_wrong','candidate_not_top','same_label_hard_src','same_label_hard_dst')},
        'posthoc_oracle':{name:{k:oracles[name+'_'+k] for k in ('exact','missing','extra')}
                          for name in oracle_names},
        'endpoint_learning_signal':endpoint_signal,
        'posthoc_assignment_uses_gold':True}
    return result,rows


def hard_binding_loss(forward,sample,assignment):
    """Same-relation, one-endpoint hard negatives of actual admissible edges.

    Only TRAIN positives choose matched prediction slots and hard-negative
    alternatives; no gold is consumed by inference or validation gradients.
    The relationship logit is constant for same-label alternatives, so the
    loss directly discriminates source and destination choices.
    """
    rel,src,dst=forward
    dev=src.device
    ss=F.log_softmax(src,-1);dd=F.log_softmax(dst,-1)
    cs=sample['src'].to(dev);cd=sample['dst'].to(dev);cr=sample['relation'].to(dev)
    terms=[]
    for edge,slot in assignment.items():
        gs=cs[edge];gd=cd[edge];gr=cr[edge]
        score=ss[slot,cs]+dd[slot,cd]
        good=score[edge]
        # Hard negatives explicitly compete for exactly one endpoint at the
        # same directed relation, not arbitrary other relation labels.
        for mask in ((cr==gr)&(cd==gd)&(cs!=gs),
                     (cr==gr)&(cs==gs)&(cd!=gd)):
            if not bool(mask.any()):continue
            scores=score[mask]
            hardest=torch.topk(scores,k=min(HARD_TOPK,scores.numel())).values
            terms.append(F.softplus(HARD_MARGIN+hardest-good).mean())
    if not terms:
        return (src.sum()+dst.sum())*0.,0
    return torch.stack(terms).mean(),len(terms)


def batch_loss(model,chosen_pairs,rows_by_sha,arm):
    if arm not in ARMS:refuse('UNKNOWN_ARM')
    supervised=[];paired=[];hard=[]
    for pair in chosen_pairs:
        a,b=(rows_by_sha[s] for s in pair['sha256'])
        fl=model(a);fr=model(b)
        la,aa=p57._matched_set_loss(fl,a);lb,ab=p57._matched_set_loss(fr,b)
        supervised.extend((la,lb))
        paired.append(p57.paired_consistency(a,b,fl,fr,aa,ab))
        if arm=='B_hard_binding':
            for forward,sample,mapping in ((fl,a,aa),(fr,b,ab)):
                term,n=hard_binding_loss(forward,sample,mapping)
                if n:hard.append(term)
    loss=torch.stack(supervised).mean()+p57.PAIR_WEIGHT*torch.stack(paired).mean()
    if arm=='B_hard_binding' and hard:
        loss+=HARD_WEIGHT*torch.stack(hard).mean()
    return loss


def train_one(base,train_pairs,train_map,validation,val_pairs,*,device,steps,interval,arm):
    model=p56._load_state(base).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    model.eval()
    initial,_=p57._safe_evaluate(model,validation,val_pairs,'validation')
    best,best_step,best_state=initial,0,p56._copy_state(model)
    checkpoints=[{'step':0,'validation':initial}]
    for step,batch in enumerate(p57.pair_batches(train_pairs,steps,p55.SEED+5700),start=1):
        model.train();opt.zero_grad(set_to_none=True)
        loss=batch_loss(model,batch,train_map,arm)
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_LOSS_'+arm)
        loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT_'+arm)
        opt.step()
        if step%interval==0 or step==steps:
            model.eval()
            observed,_=p57._safe_evaluate(model,validation,val_pairs,'validation')
            if p57.quality(observed,step)>p57.quality(best,best_step):
                best,best_step,best_state=observed,step,p56._copy_state(model)
            checkpoints.append({'step':step,'loss':round(float(loss.detach().cpu()),6),
                                'validation':observed})
            print('GAMER_G2_0058_VALIDATION '+json.dumps({'arm':arm,'step':step,
                'exact':observed['exact'],'pairs':observed['exact_pairs'],
                'missing':observed['missing'],'extra':observed['extra'],
                'best_step':best_step},sort_keys=True),flush=True)
    selected=p56._load_state(best_state).to(device).eval().requires_grad_(False)
    training=[train_map[s] for pair in train_pairs for s in pair['sha256']]
    tr,tr_details=p57._safe_evaluate(selected,training,train_pairs,'train_'+arm,details=True)
    val,val_details=p57._safe_evaluate(selected,validation,val_pairs,'validation_'+arm,details=True)
    return ({'arm':arm,'best_step':best_step,'training':tr,'validation':val,
             'optimizer_steps':steps,'checkpoints':checkpoints},best_state,tr_details+val_details)


def preflight(base,device):
    if device not in ('cpu','cuda') or (device=='cuda' and not torch.cuda.is_available()):
        refuse('PREFLIGHT_DEVICE')
    torch.manual_seed(460558)
    node=torch.randn(6,96,dtype=torch.float16)
    src=torch.tensor([0,0,0,1,2,3,4,1,2],dtype=torch.long)
    dst=torch.tensor([1,2,3,2,3,4,5,4,5],dtype=torch.long)
    relation=torch.tensor([0,0,0,1,1,1,1,1,1],dtype=torch.long)
    left={'node':node,'kinds':torch.zeros(6,dtype=torch.long),'margin':torch.zeros(6),
        'src':src,'dst':dst,'relation':relation,'edge_count':len(src),
        'positive':torch.tensor([0,4]),'truth_count':2,
        'training_node_ids':tuple('n'+str(i) for i in range(6))}
    right={**left,'node':(node.float()+.01*torch.randn(6,96)).half()}
    rows={'a':left,'b':right};pairs=[{'sha256':('a','b'),'mode':'renaming'}]
    diagnostics={}
    for arm in ARMS:
        model=p56._load_state(base).to(device)
        opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
        opt.zero_grad(set_to_none=True)
        loss=batch_loss(model,pairs,rows,arm)
        if not bool(torch.isfinite(loss)):refuse('PREFLIGHT_LOSS_'+arm)
        loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all())
               for p in model.parameters()):refuse('PREFLIGHT_GRADIENT_'+arm)
        opt.step()
        with torch.no_grad():
            if not isinstance(p56._infer(model.eval(),left),set):
                refuse('PREFLIGHT_INFERENCE_'+arm)
        diagnostics[arm]=round(float(loss.detach().cpu()),6)
    print('GAMER_G2_0058_DEVICE_PREFLIGHT '+json.dumps({'device':device,
        'arms':diagnostics,'matched_optimization':True,'status':'PASS'},sort_keys=True),flush=True)


def finalize(samples,pairs,replay,report,*,base,parent_hashes,parent_report,
             encoder_sha,device,steps,interval):
    if (replay.get('exact_parent50_legacy_replay') is not True or
        report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True):
        refuse('PARENT52_REPLAY')
    train_pairs,train_map,validation,val_pairs,holdouts,new_rows,new_pairs=prepare_cohorts(
        samples,pairs,parent_report)
    frozen=p56._load_state(base).to(device).eval().requires_grad_(False)
    diagnosis,diag_records=diagnose(frozen,validation,val_pairs)
    print('GAMER_G2_0058_DIAGNOSIS '+json.dumps(diagnosis,sort_keys=True),flush=True)
    if not diagnosis['endpoint_learning_signal']:
        # A different mechanism must be justified before spending CUDA time;
        # this is an explicit safe stop, not an ineffective extra bake-off.
        refuse('NO_TRAINABLE_ENDPOINT_BINDING_SIGNAL')
    arms={};weights={};ledger=list(diag_records)
    for arm in ARMS:
        summary,state,details=train_one(base,train_pairs,train_map,validation,val_pairs,
            device=device,steps=steps,interval=interval,arm=arm)
        arms[arm]=summary
        weights.update({arm+'.'+k:v for k,v in state.items()})
        ledger.extend(details)
    chosen=max(ARMS,key=lambda arm:(p57.quality(arms[arm]['validation'],
                        arms[arm]['best_step']),-ARMS.index(arm)))
    # Freeze BOTH arms before any test graph is scored. Tests cannot affect
    # choice or checkpoint selection; both results are reported for causality.
    selected={arm:p56._load_state({k:weights[arm+'.'+k] for k in base}).to(device)
              .eval().requires_grad_(False) for arm in ARMS}
    for arm,model in selected.items():
        if any(p.grad is not None or p.requires_grad for p in model.parameters()):
            refuse('SELECTED_WEIGHTS_GRADIENT_'+arm)
    final={}
    for section,groups in (
        ('counterfactual_probe',pairs['counterfactual_probe']),
        ('fresh_counterfactual_probe',pairs['fresh_counterfactual_probe']),
        ('previous_0056_and_0057_final',pairs[p57.FINAL_GROUP][:32]),
        ('new_0058_final',new_pairs)):
        source=p57.FINAL_GROUP if 'final' in section else section
        scoped=p56._ids(groups)
        source_rows=new_rows if section=='new_0058_final' else holdouts[source]
        evaluation=[row for row in source_rows if row['prompt_sha256'] in scoped]
        if len(evaluation)!=len(scoped):refuse('FINAL_TEST_COUNT_'+section)
        for row in evaluation:g53.mark_truth(row)
        final[section]={}
        for arm in ARMS:
            scores,details=p57._safe_evaluate(selected[arm],evaluation,groups,
                section+'_'+arm,details=True)
            final[section][arm]=scores
            ledger.extend(details)
        print('GAMER_G2_0058_FINAL_EVAL '+json.dumps({'section':section,
            'control_exact':final[section][ARMS[0]]['exact'],
            'repair_exact':final[section][ARMS[1]]['exact'],
            'control_missing':final[section][ARMS[0]]['missing'],
            'repair_missing':final[section][ARMS[1]]['missing']},sort_keys=True),flush=True)
    if len(ledger)>4096:refuse('PREDICTION_LEDGER_BUDGET')
    exp={'schema':SCHEMA,'status':STATUS,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'source_parent57_hashes':parent_hashes,'frozen_encoder_sha256':encoder_sha,
        'diagnosis':diagnosis,
        'training':{'optimizer_steps_total':steps*len(ARMS),'steps_per_arm':steps,
            'shared_schedule':True,'same_0057_initial_weights':True,
            'frozen_g1_g2_0052_encoder':True,
            'hard_loss_weight':HARD_WEIGHT,'hard_loss_margin':HARD_MARGIN,
            'hard_negative_topk':HARD_TOPK,
            'no_validation_or_test_gradients':True},
        'cohorts':{'training_graphs':80,'validation_graphs':16,
            'new_final_graphs':32,'new_final_scored_after_selection':True,
            'new_final_sha256':sorted(p56._ids(new_pairs))},
        'arms':arms,'selected_development_arm':chosen,
        'selection_basis':'validation_only_exact_graph_then_edge_errors; control_tiebreak',
        'final_evaluation_frozen_both_arms':final,
        'gates':{'diagnosis_passed':True,'model_selection_before_final':True,
            'new_final_test_scored_once_per_frozen_arm':True,
            'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
            'next_step':'REVIEW_ENDPOINT_RANKING_AND_EXACT_GRAPH_TRANSFER'},
        '_auxiliary_ledger':{'filename':PREDICTIONS,'rows':ledger}}
    policy={'enabled':False,'research_only':True,'sealed_test_used':False,
        'production_promotion':False,'selected_development_arm':chosen,
        'new_final_not_used_for_selection':True}
    return exp,weights,policy


def run(*,parent57_run,scale56_run,diagnosis55_run,candidate55_run,
        candidate54_run,candidate53_run,candidate52_run,candidate51_run,
        occurrence48_run,counterfactual50_run,output,device='cuda',
        steps=STEPS,checkpoint_interval=INTERVAL,
        train_pairs=48,probe_pairs=16,**kwargs):
    if train_pairs!=48 or probe_pairs!=16 or not 1<=steps<=MAX_STEPS or not 1<=checkpoint_interval<=steps:
        refuse('TRAINING_COHORT_OR_BUDGET')
    g53.verify_parent52(candidate52_run)
    p54.verify_parent53(candidate53_run,{},candidate52_run)
    p55.verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    frozen55.verify_frozen_checkpoint(diagnosis55_run,candidate55_run,candidate54_run)
    p57.verify_parent56(scale56_run)
    base,parent_hashes,parent_report=verify_parent57(parent57_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0058_IMMUTABLE_OUTPUT')
    preflight(base,device)
    report48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    encoder_sha=_sha(Path(occurrence48_run)/d48.WEIGHTS)
    if report48.get('schema')!=d48.SCHEMA or report48.get('weights_sha256')!=encoder_sha:
        refuse('PARENT48_HASH')
    raw=load_file(str(Path(occurrence48_run)/d48.WEIGHTS),device='cpu')
    encoder=d48.OccurrenceEvidenceScorer(raw['encoder.norm.weight'].shape[0])
    encoder.load_state_dict(raw,strict=True)
    encoder.eval().requires_grad_(False).to(device)
    frozen_encoder=p56._copy_state(encoder)
    def features(row,expanded,section):
        compatible='fresh_counterfactual_probe' if section==p57.FINAL_GROUP else section
        out=g53.frozen_features(row,expanded,encoder,compatible)
        out['section']=section
        return out
    def finish(samples,pairs,replay,report):
        result=finalize(samples,pairs,replay,report,base=base,
            parent_hashes=parent_hashes,parent_report=parent_report,
            encoder_sha=encoder_sha,device=device,steps=steps,interval=checkpoint_interval)
        if any(not torch.equal(encoder.state_dict()[k].detach().cpu(),value)
               for k,value in frozen_encoder.items()):
            refuse('FROZEN_ENCODER_MUTATION')
        return result
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        interface_training_alignment=p57.training_node_alignment,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,final_test_pairs=FINAL_ALL_PAIRS,
        train_pairs=train_pairs,probe_pairs=probe_pairs,**kwargs)
