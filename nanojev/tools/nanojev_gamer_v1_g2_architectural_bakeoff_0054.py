"""G2-0054: controlled ranking/decoding bake-off over G2-0052 repaired graphs.

A/B share the same original-loss continued G2-0053 ranker; C/D share an
identically budgeted competition-trained ranker. Each decoder is applied
without gold to its evaluation rows. Calibrators see only the reserved 0053
training-fit split. An explicitly gold-aware oracle is a coverage AUDIT only.
Nothing is promoted, and neither inspected probe is used to choose an arm.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import torch
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

SCHEMA='nanojev.gamer.graph.g2.architectural-bakeoff-0054.v1'
STATUS='G2_0054_CONTROLLED_BAKEOFF_AUDITED_NOT_CERTIFIED'
REPORT='g2_architectural_bakeoff_report.json'
LEDGER='g2_architectural_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_0054_scoring_development.safetensors'
POLICY='g2_0054_frozen_policy.json'
STEPS=160
MAX_STEPS=400
BATCH=4
RANK_BUDGET=g53.RANK_BUDGET
DECODE_BUDGET=g53.DECODE_BUDGET
NEGATIVES=g53.NEGATIVES
SEED=460554


def refuse(reason):
    raise ValueError('GAMER_G2_0054_'+reason)


def verify_parent53(folder, parent52_report, candidate52_dir):
    folder=Path(folder)
    report=json.loads((folder/g53.REPORT).read_text(encoding='utf-8'))
    scorer=report.get('scoring_experiment',{})
    parent_hashes=scorer.get('parent_hashes',{})
    parent52_hash=g53.p52.p51.SOURCE.backbone.sha256(Path(candidate52_dir)/p52.REPORT)
    weight_path=folder/g53.WEIGHTS
    if (report.get('schema')!=g53.SCHEMA or report.get('status')!=g53.STATUS
        or report.get('production_promotion') is not False
        or report.get('scorer_retraining_performed') is not True
        or report.get('new_optimizer_steps')!=240
        or report.get('sealed_test_used') is not False
        or scorer.get('gates',{}).get('production_promotion') is not False
        or scorer.get('training',{}).get('frozen_g1_and_frozen_0048_encoder') is not True
        or scorer.get('results',{}).get('train',{}).get('cases')!=80
        or scorer.get('results',{}).get('train_fit',{}).get('cases')!=16
        or scorer.get('results',{}).get('counterfactual_probe',{}).get('cases')!=32
        or parent_hashes.get('g2_0052_report_sha256')!=parent52_hash
        or report.get('scoring_weights_sha256')!=g53.p52.p51.SOURCE.backbone.sha256(weight_path)
        or report.get('ledger_sha256')!=g53.p52.p51.SOURCE.backbone.sha256(folder/g53.LEDGER)):
        refuse('PARENT53_CONTRACT_OR_HASH')
    state=load_file(str(weight_path),device='cpu')
    model=g53.EdgeRanker()
    model.load_state_dict(state,strict=True)
    if any(not bool(torch.isfinite(x).all()) for x in state.values()):
        refuse('PARENT53_NONFINITE')
    return report, state, {
        'g2_0053_report_sha256':g53.p52.p51.SOURCE.backbone.sha256(folder/g53.REPORT),
        'g2_0053_weights_sha256':g53.p52.p51.SOURCE.backbone.sha256(weight_path),
        'g2_0053_ledger_sha256':g53.p52.p51.SOURCE.backbone.sha256(folder/g53.LEDGER)}


def split_cohorts(samples,pairs):
    samples=[g53.mark_truth(x) for x in samples]
    if len(samples)!=2*sum(len(v) for v in pairs.values()) or len({s['prompt_sha256'] for s in samples})!=len(samples):
        refuse('CAPTURE_COUNTS')
    by_cell={}
    for pair in pairs['counterfactual_train']:
        by_cell.setdefault((pair['family'],pair['mode']),[]).append(pair)
    cells={(family,mode) for family in ('allocation','encounter') for mode in ('role_swap','renaming')}
    if set(by_cell)!=cells or any(len(group)!=12 for group in by_cell.values()):
        refuse('TRAIN_SPLIT_BALANCE')
    fit_ids={p['id'] for cell in sorted(cells) for p in by_cell[cell][:2]}
    train_ids={p['id'] for p in pairs['counterfactual_train'] if p['id'] not in fit_ids}
    cohorts={
        'train':[s for s in samples if s['section']=='counterfactual_train' and s['pair'] in train_ids],
        'train_fit':[s for s in samples if s['section']=='counterfactual_train' and s['pair'] in fit_ids],
        'counterfactual_probe':[s for s in samples if s['section']=='counterfactual_probe'],
        'fresh_counterfactual_probe':[s for s in samples if s['section']=='fresh_counterfactual_probe']}
    pair_sets={
        'train':g53._pairs_for_names(pairs['counterfactual_train'],train_ids),
        'train_fit':g53._pairs_for_names(pairs['counterfactual_train'],fit_ids),
        'counterfactual_probe':pairs['counterfactual_probe'],
        'fresh_counterfactual_probe':pairs.get('fresh_counterfactual_probe',[])}
    if tuple(len(cohorts[k]) for k in cohorts)!=(80,16,32,32) or len(pair_sets['fresh_counterfactual_probe'])!=16:
        refuse('COHORT_COUNTS')
    return cohorts,pair_sets


def competing_negative_indices(sample,scores, generator):
    """Mine high-scoring competitors sharing G1 endpoints with gold TRAIN edges.

    All candidate features and scores are label-blind. The gold indices are
    accessed only here in the TRAINING miner, never during inference.
    """
    positive=set(sample['positive'].tolist())
    if not positive or len(positive)==sample['edge_count']:
        refuse('TRAIN_POSITIVE_NEGATIVE_REQUIRED')
    negatives=scores.detach().clone()
    negatives[list(positive)]=-float('inf')
    n=sample['edge_count']
    src=sample['src'];dst=sample['dst'];relation=sample['relation']
    selected=set()
    # Each source/destination is its own competing decision. Include relation
    # substitutions on the exact same endpoints, not only globally hard edges.
    for p in sorted(positive):
        mask=(src==src[p]) | (dst==dst[p]) | ((src==src[p]) & (dst==dst[p]))
        eligible=negatives.masked_fill(~mask,-float('inf'))
        k=min(4,int(torch.isfinite(eligible).sum()))
        if k:
            selected.update(int(x) for x in torch.topk(eligible,k=k).indices)
        if len(selected)>=128:break
    if len(selected)>128:
        selected=set(sorted(selected,key=lambda x:(-float(negatives[x]),x))[:128])
    hard_n=min(NEGATIVES-len(selected),int(torch.isfinite(negatives).sum()))
    # Ensure general hard adversaries beyond shared-node competitors.
    if hard_n:
        selected.update(int(x) for x in torch.topk(negatives,k=hard_n).indices)
    # If the first two sets overlap, fill remaining capacity with independent
    # random negative examples, with deterministic per-arm seed.
    for ix in torch.randperm(n,generator=generator).tolist():
        if len(selected)>=NEGATIVES:break
        if ix not in positive:selected.add(int(ix))
    if not selected or selected.intersection(positive):refuse('MINER_LEAKAGE')
    return torch.tensor(sorted(positive|selected),dtype=torch.long)


def _loss(model,sample,idx,mode):
    values=model(sample,idx)
    indices=idx.tolist();gold=set(sample['positive'].tolist())
    # Captured edge identities (idx/src/dst) live on CPU; learned edge
    # scores live on the scorer's device (CUDA in the operator run).
    # Never use the CUDA score mask to index CPU endpoint IDs.
    truth_cpu=torch.tensor([int(i) in gold for i in indices],dtype=torch.bool)
    truth=truth_cpu.to(device=values.device)
    if not bool(truth.any()) or not bool((~truth).any()):refuse('BAD_BATCH')
    positives=values[truth];negatives=values[~truth]
    basic=F.softplus(-positives).mean()+F.softplus(negatives).mean()
    hardest=torch.topk(negatives,k=min(8,negatives.numel())).values.mean()
    if mode=='control':
        # Reproduce G2-0053's published loss with an equal new step budget.
        return basic+.25*F.softplus(.5+hardest-positives.mean())
    if mode!='competition':refuse('BAD_TRAIN_ARM')
    p_ids=idx[truth_cpu];n_ids=idx[~truth_cpu]
    ps=sample['src'][p_ids];pd=sample['dst'][p_ids]
    ns=sample['src'][n_ids];nd=sample['dst'][n_ids]
    competitors=((ps[:,None]==ns[None,:]) | (pd[:,None]==nd[None,:]))
    if bool(competitors.any()):
        rivalry=F.softplus(.5+negatives[None,:]-positives[:,None])
        hardest_per_pos=rivalry.masked_fill(~competitors.to(values.device),0).max(dim=1).values
        valid=competitors.any(dim=1).to(values.device)
        contrast=(hardest_per_pos*valid).sum()/valid.sum().clamp_min(1)
    else:
        contrast=F.softplus(.5+hardest-positives.mean())
    return basic+.75*contrast


def continue_ranker(initial_state,samples,*,mode,device,steps,batch):
    if device not in ('cpu','cuda') or not 1<=steps<=MAX_STEPS or not 1<=batch<=8 or not samples:
        refuse('TRAIN_BUDGET')
    torch.manual_seed(SEED)
    generator=torch.Generator().manual_seed(SEED)
    model=g53.EdgeRanker().to(device)
    model.load_state_dict(initial_state,strict=True)
    opt=torch.optim.AdamW(model.parameters(),lr=g53.LR,weight_decay=1e-4)
    cache={};history=[]
    for step in range(1,steps+1):
        if step==1 or (step-1)%80==0:
            for s in samples:
                scores=g53.score_all(model,s)
                if not bool(torch.isfinite(scores).all()):refuse('NONFINITE_TRAIN_SCORES')
                if mode=='control':
                    # Same original miner and objective as G2-0053.
                    scores[s['positive']]=-float('inf')
                    k=min(NEGATIVES//2,s['edge_count']-len(s['positive']))
                    top=torch.topk(scores,k=k).indices
                    perm=torch.randperm(s['edge_count'],generator=generator)
                    gold=set(s['positive'].tolist())
                    random=[int(i) for i in perm.tolist() if i not in gold][:NEGATIVES-k]
                    idx=torch.tensor(sorted(set(s['positive'].tolist()+top.tolist()+random)),dtype=torch.long)
                else:
                    idx=competing_negative_indices(s,scores,generator)
                cache[s['prompt_sha256']]=idx
            model.train()
        pick=torch.randperm(len(samples),generator=generator)[:batch].tolist()
        opt.zero_grad(set_to_none=True)
        loss=torch.stack([_loss(model,samples[i],cache[samples[i]['prompt_sha256']],mode)
                          for i in pick]).mean()
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_LOSS')
        loss.backward()
        grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(grad)):refuse('NONFINITE_GRADIENT')
        opt.step()
        if step==1 or step%80==0 or step==steps:
            point={'step':step,'loss':round(float(loss.detach().cpu()),6)}
            history.append(point)
            print('GAMER_G2_0054_TRAIN '+json.dumps({'arm':mode,**point},sort_keys=True),flush=True)
    model.eval().requires_grad_(False)
    return model,history


def ranked_predictions(model,samples):
    output=[]
    for sample in samples:
        scores=g53.score_all(model,sample)
        if not bool(torch.isfinite(scores).all()):refuse('NONFINITE_EVAL_SCORES')
        order=torch.argsort(scores,descending=True,stable=True)[:RANK_BUDGET]
        output.append((sample,scores,order))
    return output


def decode(scores,order,threshold):
    chosen=order[scores[order]>=threshold][:DECODE_BUDGET]
    return set(chosen.tolist())


def calibrate(rows):
    """Choose one global stopping logit exclusively on TRAIN-FIT rows.

    Maximizes micro edge F1 with a small exact-graph preference. The class
    prior and inferred graph length are learned here, never supplied by gold
    at inference. Ties prefer fewer predicted edges.
    """
    if not rows or len(rows)!=16 or any(s['section']!='counterfactual_train' for s,_,_ in rows):
        refuse('CALIBRATION_COHORT')
    candidates=[]
    for _,scores,ranked in rows:
        subset=scores[ranked]
        candidates += [float(subset[min(i,subset.numel()-1)]) for i in
                       (0,1,2,4,8,12,16,20,24,32,40,48,64,80,95,127,191,255)]
    if not candidates:refuse('CALIBRATION_EMPTY')
    sorted_logits=sorted(set(candidates))
    trials=sorted_logits+[max(sorted_logits)+1.]
    best=None
    for threshold in trials:
        tp=fp=fn=exact=pred=0
        for sample,scores,ranked in rows:
            chosen=decode(scores,ranked,threshold)
            gold=set(sample['positive'].tolist())
            tp+=len(chosen&gold);fp+=len(chosen-gold)
            fn+=sample['truth_count']-len(chosen&gold)
            pred+=len(chosen)
            exact+=int(chosen==gold and sample['truth_count']==len(gold))
        micro=(2*tp/max(1,2*tp+fp+fn))
        score=micro+0.02*exact/len(rows)
        candidate=(round(score,10),-pred,threshold)
        if best is None or candidate>best[0]:
            best=(candidate,{'threshold':float(threshold),'micro_f1':round(micro,6),
                'exact':exact,'selected_edges':pred,'true_positive':tp,'false_positive':fp,
                'false_negative':fn,'candidate_thresholds_tested':len(trials)})
    return best[1]


def evaluate_rows(rows,*,arm,threshold=0.,oracle=False):
    result=[]
    for sample,scores,ranked in rows:
        gold=set(sample['positive'].tolist())
        # Oracle knows gold; it is strictly an upper-bound audit. No scored
        # training, calibration, or deployed policy calls this branch.
        chosen=set(gold) if oracle else decode(scores,ranked,threshold)
        ranked_set=gold if oracle else set(ranked.tolist())
        result.append({'arm':arm,'section':sample['section'],
            'prompt_sha256':sample['prompt_sha256'],
            'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
            'gold_edges':sample['truth_count'],'reachable_gold_edges':len(gold),
            'candidate_edges':sample['edge_count'],
            'ranked_edges':min(RANK_BUDGET,sample['edge_count']),
            'selected_edges':len(chosen),'rank_missing':sample['truth_count']-len(gold&ranked_set),
            'missing':sample['truth_count']-len(gold&chosen),
            'extra':len(chosen-gold),'reachable':len(gold)==sample['truth_count'],
            'exact':len(gold)==sample['truth_count'] and chosen==gold})
    return result


def choose_arm(report):
    """Selection is restricted to train-fit. Probe rows are NEVER consulted."""
    fit=report['train_fit']
    options=('A_control_fixed','B_control_calibrated','C_competition_fixed','D_competition_calibrated')
    def objective(arm):
        value=fit[arm]
        return (value['exact'], -(value['missing']+value['extra']),
                value['rank_coverage'], -value['extra'])
    return max(options,key=lambda a:(objective(a),-options.index(a)))


def finalize(samples,pairs,replay,report,*,device,steps,batch,parent53_state,
             parent53_report,parent53_hashes,parent48_sha):
    if replay['exact_parent50_legacy_replay'] is not True or \
       report['interface_repair']['decision']['coverage_targets_met'] is not True:
        refuse('COVERAGE_REPLAY_GATE')
    cohorts,pair_sets=split_cohorts(samples,pairs)
    parent53_baseline=parent53_report['scoring_experiment']['results']
    # Exactly 160 extra training steps per ranking family, from SAME frozen 0053
    # checkpoint. The decoder arms reuse one scorer each, no optimizer calls.
    control,hist_a=continue_ranker(parent53_state,cohorts['train'],mode='control',
                                   device=device,steps=steps,batch=batch)
    competition,hist_c=continue_ranker(parent53_state,cohorts['train'],mode='competition',
                                       device=device,steps=steps,batch=batch)
    fit_a=ranked_predictions(control,cohorts['train_fit'])
    fit_c=ranked_predictions(competition,cohorts['train_fit'])
    cal_a=calibrate(fit_a);cal_c=calibrate(fit_c)
    threshold={
        'A_control_fixed':0.,'B_control_calibrated':cal_a['threshold'],
        'C_competition_fixed':0.,'D_competition_calibrated':cal_c['threshold']}
    scores={};ledger=[];by_group={}
    for name,cohort in cohorts.items():
        rows_control=fit_a if name=='train_fit' else ranked_predictions(control,cohort)
        rows_comp=fit_c if name=='train_fit' else ranked_predictions(competition,cohort)
        group={}
        for arm,raw in (('A_control_fixed',rows_control),('B_control_calibrated',rows_control),
                        ('C_competition_fixed',rows_comp),('D_competition_calibrated',rows_comp)):
            entries=evaluate_rows(raw,arm=arm,threshold=threshold[arm])
            group[arm]=g53.aggregate(entries,pair_sets[name]);ledger+=entries
        oracle=evaluate_rows(rows_control,arm='Oracle_coverage_ceiling',oracle=True)
        group['Oracle_coverage_ceiling']=g53.aggregate(oracle,pair_sets[name]);ledger+=oracle
        scores[name]=group
    picked=choose_arm(scores)
    # No model is promoted, regardless of which arm wins on train-fit. Existing
    # and fresh probes are diagnostic cohorts; no sealed test access occurs.
    report_data={
        'schema':SCHEMA,'status':STATUS,'production_promotion':False,
        'parent53_hashes':parent53_hashes,'frozen_encoder_parent48_sha256':parent48_sha,
        'design':{'arms':{
            'A_control_fixed':'same checkpoint, continued 0053 objective, original cutoff',
            'B_control_calibrated':'A scorer, train-fit selected stopping threshold',
            'C_competition_fixed':'same checkpoint and steps, endpoint-competitor loss, original cutoff',
            'D_competition_calibrated':'C scorer, train-fit selected stopping threshold',
            'Oracle_coverage_ceiling':'gold-aware audit only; NOT deployable or learned'},
            'parent53_comparison_only':'Original frozen 0053 results, not an equal-step experimental arm',
            'rank_budget':RANK_BUDGET,'max_decoded_edges':DECODE_BUDGET,
            'frozen_g1_and_0048_encoder':True,'repaired_g1_g2_0052_inventory':True,
            'oracle_accesses_gold_only_for_ceiling_audit':True,
            'calibration_uses_train_fit_labels_only':True,
            'probes_used_for_model_selection':False,'sealed_test_used':False},
        'training':{'steps':steps,'optimizer_steps_total':steps*2,
                    'steps_per_ranker':steps,'batch':batch,
                    'equal_step_budget':True,'same_initial_parent53_weights':True,
                    'history':{'control':hist_a,'competition':hist_c}},
        'calibration':{'control':cal_a,'competition':cal_c},
        'parent53_historical_metrics':parent53_baseline,
        'results':scores,'development_selection':{'chosen_arm':picked,
             'criterion':'train_fit exact then total edge errors then rank coverage; probes excluded',
             'train_fit_is_calibration_not_independent_holdout':True},
        'gates':{
             'oracle_probe_reaches_parent52_ceiling':scores['counterfactual_probe']['Oracle_coverage_ceiling']['exact']==31,
             'oracle_probe_pairs_reach_parent52_ceiling':scores['counterfactual_probe']['Oracle_coverage_ceiling']['both_exact_pairs']==15,
             'candidate_inventory_preserved':True,
             'production_promotion':False,'g2_certified':False,
             'sealed_test_used':False,
             'next_step':'SELECT_ARCHITECTURE_FROM_TRAIN_FIT_THEN_VALIDATE_INDEPENDENTLY'}}
    # The existing 0051 parent writes a single research weights file; keep
    # both trained rankers namespaced so the bake-off is fully reproducible.
    weights={**{'control.'+k:v.detach().cpu().clone() for k,v in control.state_dict().items()},
             **{'competition.'+k:v.detach().cpu().clone() for k,v in competition.state_dict().items()}}
    policy={'enabled':False,'research_only':True,'chosen_development_arm':picked,
            'rank_budget':RANK_BUDGET,'max_decoded_edges':DECODE_BUDGET,
            'calibration':{'control':cal_a['threshold'],'competition':cal_c['threshold']},
            'oracle_is_not_deployable':True}
    return report_data,weights,policy


def training_device_preflight(initial_state,device):
    """Exercise both actual optimizer/miner/loss paths before costly capture.

    The synthetic edges have the same CPU-resident feature/index contract as
    G2-0052's real capture. CUDA runs the real CUDA model and backpropagation;
    no gold labels are used outside this disposable preflight batch.
    """
    n=6
    sample={
        'prompt_sha256':'g2_0054_device_preflight',
        'node':torch.zeros(3,g53.EMBED_WIDTH,dtype=torch.float16),
        'src':torch.tensor([0,0,2,2,1,0],dtype=torch.long),
        'dst':torch.tensor([1,2,1,0,0,1],dtype=torch.long),
        'relation':torch.arange(n,dtype=torch.long)%len(g53.RELATIONS),
        'kinds':torch.zeros(3,dtype=torch.long),
        'margin':torch.zeros(3,dtype=torch.float32),
        'geometry':torch.zeros(n,5,dtype=torch.float32),
        'positive':torch.tensor([0,3],dtype=torch.long),
        'edge_count':n,
    }
    for mode in ('control','competition'):
        trained,history=continue_ranker(initial_state,[sample],mode=mode,
                                        device=device,steps=1,batch=1)
        if len(history)!=1 or not all(bool(torch.isfinite(x).all())
                                      for x in trained.state_dict().values()):
            refuse('DEVICE_PREFLIGHT_TRAINING')
    print('GAMER_G2_0054_DEVICE_PREFLIGHT '+json.dumps(
        {'device':device,'modes':['control','competition'],'status':'PASS'},
        sort_keys=True),flush=True)


def run(*,candidate53_run,candidate52_run,candidate51_run,occurrence48_run,
        counterfactual50_run,output,device='cuda',steps=STEPS,batch_prompts=BATCH,**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('COHORT_COUNTS_FIXED')
    g53.verify_parent52(candidate52_run)
    parent52=json.loads((Path(candidate52_run)/p52.REPORT).read_text(encoding='utf-8'))
    p53,state,hashes=verify_parent53(candidate53_run,parent52,candidate52_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0054_IMMUTABLE_OUTPUT')
    # Fail fast on the real selected device rather than after 0051/0052's
    # repeated source captures. Both branches backpropagate once here.
    training_device_preflight(state,device)
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    sha=d48.backbone.sha256(weight_path)
    if parent48.get('schema')!=d48.SCHEMA or sha!=parent48.get('weights_sha256'):
        refuse('PARENT48_HASH')
    checkpoint=load_file(str(weight_path),device='cpu')
    width=checkpoint['encoder.norm.weight'].shape[0]
    frozen=d48.OccurrenceEvidenceScorer(width).eval().requires_grad_(False).to(device)
    frozen.load_state_dict(checkpoint,strict=True)
    snapshot={k:v.detach().clone() for k,v in frozen.state_dict().items()}
    def get_features(raw,extra,section):
        return g53.frozen_features(raw,extra,frozen,section)
    def finish(cases,pairs,replay,report):
        data=finalize(cases,pairs,replay,report,device=device,steps=steps,
            batch=batch_prompts,parent53_state=state,parent53_report=p53,
            parent53_hashes=hashes,parent48_sha=sha)
        if any(not torch.equal(v,frozen.state_dict()[k]) for k,v in snapshot.items()):
            refuse('FROZEN_0048_MUTATED')
        return data
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=get_features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,**kwargs)
