"""G2-0099: train expert competence while developing frozen-checkpoint graph integration.

The old G2 heads, G1, 0098 and production routing are immutable. Every outer
held-out fold is separated by base graph. Inner validation alone chooses an
expert-router checkpoint and a structured-graph policy. Fit-only gold labels
train expert correctness; no gold/author IDs enter route() at inference.

    python -u -m tools.nanojev_gamer_v1_g2_router_continuation_0099 audit
    python -u -m tools.nanojev_gamer_v1_g2_router_continuation_0099 selftest
    python -u -m tools.nanojev_gamer_v1_g2_router_continuation_0099 run --device cuda

Output: candidate checkpoints, locked manifests, development diagnostics and
out-of-fold acceptance gate. NEVER promotes a production route or unlocks G3.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import statistics
import tempfile

import torch
from torch.nn import functional as F
from safetensors.torch import save_file, load_file

from tools import nanojev_gamer_v1_g2_router_trainability_0098 as t
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_router_forensics_0098 as fx
from tools import nanojev_gamer_v1_g2_routing_diagnosis_0098 as diagnosis
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

TAG = 'G2_0099_CONTINUATION'
SCHEMA = 'nanojev.g2.router-continuation-graph-integration-0099.v1'
POLICIES = {
    'margin_current': ('margin', 0.20, 0.05),
    'pin_current': ('pin', 0.20, 0.05),
    'pin_ungated': ('pin', 0.0, 0.0),
    'pin_cautious': ('pin', 0.30, 0.10),
}
ARMS = {
    'competence': {'ref_weight': 1., 'hard_weight': 0.},
    'balanced_refers': {'ref_weight': 3., 'hard_weight': 0.},
    'fuzz_correct': {'ref_weight': 2., 'hard_weight': 2.},
}


def emit(kind, value):
    print(TAG + '_' + kind + ' ' + json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def check(ok, reason):
    if not ok:
        raise ValueError(TAG + '_' + reason)


def _source(root, output):
    source = diagnosis._locate(root, output)
    root, atlas_dir, commit, parent_dir, h95, h96, p97, previous, dest, provenance, report = source
    experts = fx._frozen_experts_in_training_order(previous, provenance, p97)
    check(next(iter(experts)) == m.ANCHOR, 'ANCHOR_MISALIGNED')
    router_path = dest/'competence-router.safetensors'
    check(old.digest(router_path) == report['training']['router_sha256'],
          'HISTORICAL_ROUTER_HASH_DRIFT')
    return source, experts


def _records(source, experts, device):
    root, atlas_dir, commit, parent_dir, _, _, _, _, _, provenance, _ = source
    selected = set(provenance['selection']['fit_case_ids'])
    check(len(selected) == 275, 'FIT_COHORT_CHANGED')
    paths = m.atlas._roots(root)
    parent_record, _ = hard._verify_trained(paths, paths['parent_0087'])
    network, tok = backbone.model_components(parent_record['model_id'], paths['adapter'], device)
    composite = g1native.load_composite(paths['composite'], device=device)
    head, _ = cur._load_checkpoint(parent_dir, device=device)
    old._mutable(head)
    items = old._capture(network, tok, composite,
                         old._get_rows(atlas_dir, selected, split='discovery'),
                         device, must_exact=True)
    records = m._capture_experts(head, items, device, commit['lineage']['decoder'],
                                 experts, '0099-fit-capture', training=False)
    del network, composite, head, items
    check(len(records) == 275 and all(rec['item']['g1_exact'] for rec in records),
          'CAPTURE_COUNT_OR_G1_DRIFT')
    return records, commit['lineage']['decoder']


def _hard_correction_loss(model, rec, experts, device, *, ref_weight, hard_weight):
    """Fit-only Fuzz-and-Correct: prioritize selectable errors, protect successes.

    Only the training split supplies the gold labels below. The forward() call
    is identical during training and inference, with no target/identity input.
    """
    base = t._objective(model, rec, experts, device, ref_weight=ref_weight)
    if base is None or not hard_weight:
        return base
    margins, decoded, gold, relevant, correct, recoverable, protect, ref = (
        t._record_tensors(rec, experts, device))
    _, logits, _ = model(rec['item']['objects'], margins, decoded)
    n = gold.shape[0]
    ix = torch.arange(n, device=device)
    i,j = torch.meshgrid(ix, ix, indexing='ij')
    selected = decoded[logits.detach().argmax(0), i, j]
    available = correct.any(0) & relevant
    # Online hard-example mining uses TRAINING gold only, not dev/test gold.
    hard = available & (selected != gold)
    # Correctness mass is a set target: multiple experts can be right.
    mass = (logits.softmax(0) * correct.float()).sum(0).clamp_min(1e-7)
    weight = available.float() * (1. + 3.*hard.float() + 1.5*protect.float())
    weight = weight * (1. + (ref_weight-1.)*(gold==t.REL_REFERS_TO).float())
    correction = -(mass.log()*weight).sum()/weight.sum().clamp_min(1.)
    return base + hard_weight * .25 * correction


def route_graph(model, rec, experts, spec, device, policy,
                *, decoder=None):
    """Deployable inference adapter: never consult gold, IDs or authored edges.

    Frozen G2 scores -> frozen router competence -> selected-score/label policy
    -> the *real* structured graph decoder. Decoder injection is test-only.
    """
    check(policy in POLICIES, 'UNSUPPORTED_ROUTING_POLICY')
    check(list(rec['predictions']) == list(experts), 'PREDICTION_EXPERT_ORDER_DRIFT')
    check(not model.training, 'ROUTER_MUST_BE_FROZEN_FOR_GRAPH_INFERENCE')
    margins, labels = m._predict_inputs(rec, experts, device)
    with torch.no_grad():
        _, competence, meta = model(rec['item']['objects'], margins, labels)
        kind, threshold, gap = POLICIES[policy]
        scores, swap, winner = fx._route_scores(
            margins, labels, competence, meta['disagreement'],
            kind, threshold, gap, strength=12.)
        decode = old._decode_matrix if decoder is None else decoder
        # Do not even hand target/gold metadata to the inference decoder.
        decoded, edges = decode({'objects':rec['item']['objects']}, scores, spec)
    return decoded, edges, {'switches':int(swap.sum()),
                            'winner_nonanchor':int((winner != 0).sum()),
                            'policy':policy}


def _graph_metrics(records, experts, spec, device, model=None, policy=None):
    count = Counter()
    by_family = defaultdict(Counter)
    for rec in records:
        item = rec['item']
        anchor = rec['predictions'][m.ANCHOR]
        if model is None:
            pred, extra = anchor, {'switches':0}
        else:
            pred, _, extra = route_graph(model, rec, experts, spec, device, policy)
        case = m._count(rec, pred)
        for bucket in (count, by_family[item['row']['family']]):
            bucket.update(case)
            bucket.update(switches=extra['switches'])
        gold = item['targets'].cpu()
        valid = ~torch.eye(len(gold), dtype=torch.bool)
        baseline = anchor.cpu()
        correct = (pred.cpu()==gold)
        changed = (pred.cpu()!=baseline) & valid
        protected = valid & (baseline==gold)
        # Graph correction counts cover differences from baseline, *including*
        # positive/negative relationships. The F1 count is the acceptance metric.
        for bucket in (count, by_family[item['row']['family']]):
            bucket.update(repaired=int((changed&correct).sum()),
                          harmed=int((changed&protected&~correct).sum()))
    return {'overall':m._metric(count),
            'by_family':{k:m._metric(v) for k,v in by_family.items()},
            'repaired':count['repaired'],'harmed':count['harmed'],
            'switched':count['switches']}


def _compare_selector(records, experts, device, model):
    stats, families, _, relation = t._metrics(records, experts, device, model)
    return {'overall':t._rates(stats),
            'score':round(t._score(stats),6),
            'by_family':{k:t._rates(v) for k,v in families.items()},
            'by_relation':{k:{'correctable':v['recoverable'],
                              'correctly_selected':v['rescue']}
                           for k,v in relation.items()}}


def _rank(assembled, selected):
    # Predeclared inner-validation selection rule: whole-graph exact first,
    # edge-F1 next, selection accuracy third, minimize harms as final tie.
    g = assembled['overall']
    s = selected['overall']
    return (g['exact'], g['edge_f1'], s['chosen_accuracy'], -assembled['harmed'])


def _train_candidates(train, val, experts, spec, device, seed, steps, eval_every, lr, fold):
    best = None
    evidence = []
    for arm_index, (arm, config) in enumerate(ARMS.items()):
        torch.manual_seed(seed + fold*1000 + arm_index)
        model = m.CompetenceRouter(len(experts)).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.0001)
        rng = random.Random(seed + fold*1000 + arm_index)
        history = []
        for step in range(1, steps+1):
            model.train()
            optimizer.zero_grad(set_to_none=True)
            sampled = rng.sample(train, min(4, len(train)))
            losses = [_hard_correction_loss(model, rec, experts, device, **config)
                      for rec in sampled]
            losses = [v for v in losses if v is not None]
            check(bool(losses), 'EMPTY_TRAINING_LOSS')
            loss = torch.stack(losses).mean()
            check(bool(torch.isfinite(loss)), 'NONFINITE_TRAINING_LOSS')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            if step != 1 and step%eval_every and step != steps:
                continue
            model.eval()
            selector = _compare_selector(val, experts, device, model)
            # Graph assembly runs on an evaluation-only (non-updating) view of
            # this checkpoint. Its result never enters the training gradient.
            for policy in POLICIES:
                assembled = _graph_metrics(val, experts, spec, device, model, policy)
                ranking = _rank(assembled, selector)
                row = {'fold':fold,'arm':arm,'step':step,'policy':policy,
                       'val_selector':selector['overall'],
                       'val_score':selector['score'],
                       'val_graph':assembled['overall'],
                       'val_graph_harms':assembled['harmed'],
                       'ranking':list(ranking)}
                history.append(row)
                if best is None or ranking > best['ranking']:
                    best = {'arm':arm,'step':step,'policy':policy,
                            'ranking':ranking,'val_selector':selector,
                            'val_graph':assembled,
                            'state':{k:v.detach().cpu().clone()
                                     for k,v in model.state_dict().items()}}
            emit('TRAIN_ASSEMBLY_EVAL',{'fold':fold,'arm':arm,'step':step,
                'loss':round(float(loss.detach()),6),
                'selection':selector['overall']['chosen_accuracy'],
                'refers_to':selector['overall']['refers_to_rate'],
                'candidate_f1':max(x['val_graph']['edge_f1'] for x in history[-len(POLICIES):]),
                'candidate_exact':max(x['val_graph']['exact'] for x in history[-len(POLICIES):])})
        evidence += history
    check(best is not None, 'NO_TRAINED_CHECKPOINT')
    restored = m.CompetenceRouter(len(experts)).to(device)
    restored.load_state_dict(best['state'],strict=True)
    restored.eval()
    return restored, best, evidence


def _persist_router(path, model, experts, policy, *, source_sha, role, arm, step):
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {key:tensor.detach().contiguous().cpu()
             for key,tensor in model.state_dict().items()}
    save_file(state,str(path))
    manifest = {
        'schema':SCHEMA,'status':'EXPERIMENTAL_NO_PRODUCTION_PROMOTION',
        'role':role,'arm':arm,'steps':step,'policy':policy,
        'expert_order':list(experts),
        'expert_checkpoints':{name:record['sha256'] for name,record in experts.items()},
        'source_0098_router_sha256':source_sha,
        'checkpoint_role_is_research_only':True,
        'router_sha256':old.digest(path),
        'confirmation_used':False,'dev_used':False,'g3_unlocked':False,
        'production_promoted':False}
    old.write(path.with_suffix('.manifest.json'),manifest)
    return manifest


def load_frozen_adapter(path, experts, device):
    """Explicit opt-in only: refuse drift and load a candidate without enabling it."""
    path = Path(path)
    manifest = old.read(path.with_suffix('.manifest.json'))
    check(manifest['schema']==SCHEMA, 'BAD_MANIFEST_SCHEMA')
    check(manifest['production_promoted'] is False and
          manifest['g3_unlocked'] is False, 'UNEXPECTED_PROMOTION_FLAG')
    check(manifest['router_sha256']==old.digest(path),'ROUTER_CHECKPOINT_HASH_DRIFT')
    check(manifest['expert_order']==list(experts),'EXPERT_AXIS_MISMATCH')
    check(all(manifest['expert_checkpoints'][k]==v['sha256'] and
              old.digest(Path(v['path']))==v['sha256']
              for k,v in experts.items()), 'FROZEN_EXPERT_HASH_DRIFT')
    adapter = m.CompetenceRouter(len(experts)).to(device)
    adapter.load_state_dict(load_file(str(path),device='cpu'),strict=True)
    adapter.eval()
    check(manifest['policy'] in POLICIES,'UNKNOWN_MANIFEST_POLICY')
    return adapter, manifest['policy']


def audit(root=old.ROOT_DEFAULT, output=None):
    source, experts = _source(root, output)
    selected=source[9]['selection']
    emit('AUDIT',{'ready':True,'expert_count':len(experts),
          'expert_order':list(experts),'fit_cases':len(selected['fit_case_ids']),
          'fit_bases':len(selected['fit_bases']),
          'arms':list(ARMS),'assembly_policies':list(POLICIES),
          'source_frozen':True,'dev_used':False,'confirmation_used':False,
          'g3_unlocked':False,'auto_promote':False})


def run(root=old.ROOT_DEFAULT, output=None, device='cuda', folds=3, steps=600,
        eval_every=100, lr=.002, seed=990099, force=False):
    check(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
          'DEVICE_UNAVAILABLE')
    check(2<=folds<=5 and 100<=steps<=3000 and 1<=eval_every<=steps and
          0<lr<.1, 'INVALID_TRAINING_CONFIG')
    source, experts = _source(root, output)
    dest = source[8]/'router-continuation-0099'
    report_path = dest/'joint-training-and-assembly.json'
    check(force or not report_path.exists(),'REPORT_ALREADY_EXISTS_USE_NEW_OUTPUT_OR_FORCE')
    source_sha=source[10]['training']['router_sha256']
    emit('START',{'device':device,'folds':folds,'steps_per_arm':steps,
          'train_arms':list(ARMS),'graph_policies':list(POLICIES),
          'no_gold_at_inference':True,'prior_dev_used':False,'confirmation_used':False})
    records, spec = _records(source, experts, device)
    results=[]
    configs=[]
    for fold, (train, val, test), sizes in t._split_by_base(records,folds,seed):
        # Independent graph baselines computed *before* training/selection.
        baseline = _graph_metrics(test, experts, spec, device)
        base_select = _compare_selector(test, experts, device, None)
        model, chosen, history = _train_candidates(
            train, val, experts, spec, device, seed, steps, eval_every,lr,fold)
        # The held-out test is only opened after choosing arm, checkpoint and
        # assembly policy from inner validation. Never select from test scores.
        test_selection = _compare_selector(test, experts, device, model)
        test_graph = _graph_metrics(test, experts, spec, device, model, chosen['policy'])
        checkpoint = dest/('fold-%d-router.safetensors'%fold)
        manifest=_persist_router(checkpoint,model,experts,chosen['policy'],
                 source_sha=source_sha,role='cross_validation',
                 arm=chosen['arm'],step=chosen['step'])
        record={'fold':fold,'base_counts':sizes,
                'selected_on':'INNER_VALIDATION_ONLY',
                'arm':chosen['arm'],'step':chosen['step'],'policy':chosen['policy'],
                'val_selection':chosen['val_selector'],
                'val_graph':chosen['val_graph'],
                'test_selection':test_selection,
                'test_graph':test_graph,'anchor_graph':baseline,
                'anchor_selection':base_select,
                'checkpoint':str(checkpoint),'checkpoint_sha256':manifest['router_sha256'],
                'learning_history':history}
        results.append(record)
        configs.append((chosen['arm'],chosen['policy'],chosen['step']))
        emit('FOLD',{'fold':fold,'base_counts':sizes,'arm':chosen['arm'],
              'step':chosen['step'],'policy':chosen['policy'],
              'anchor_f1':baseline['overall']['edge_f1'],
              'graph_f1':test_graph['overall']['edge_f1'],
              'anchor_exact':baseline['overall']['exact'],
              'graph_exact':test_graph['overall']['exact'],
              'selection_accuracy':test_selection['overall']['chosen_accuracy'],
              'selection_rescue_rate':test_selection['overall']['rescue_rate'],
              'selection_harm_rate':test_selection['overall']['harm_rate'],
              'refers_to_rate':test_selection['overall']['refers_to_rate']})
    # A cross-validation estimate is valid for the predeclared per-fold selector.
    # A final model trained on all fit examples is NOT independently certified.
    def sum_graph(field):
        c=Counter()
        for row in results:
            g=row[field]['overall']
            for key in ('tp','fp','fn','cases','exact','g1_exact'):
                c[key]+=g[key]
        return m._metric(c)
    def sum_selection(field):
        c=Counter()
        for row in results:
            r=row[field]['overall']
            c.update(rescue=r['rescue'],recoverable=r['recoverable'],
                     harm=r['harm'],protect=r['protected'],
                     ref_rescued=r['refers_to_rescued'],
                     ref_recoverable=r['refers_to_recoverable'],
                     chosen_correct=r['chosen_correct'],relevant=r['relevant'],
                     oracle_reachable=r['oracle_reachable'],group_cases=r['cases'])
        return t._rates(c)
    oof_graph=sum_graph('test_graph')
    oof_anchor=sum_graph('anchor_graph')
    oof_selector=sum_selection('test_selection')
    oof_anchor_selector=sum_selection('anchor_selection')
    gates={
        'selection_exceeds_baseline_by_3pp':oof_selector['chosen_accuracy']>=
                                        oof_anchor_selector['chosen_accuracy']+.03,
        'rescue_at_least_75pct':oof_selector['rescue_rate']>=.75,
        'selection_harm_at_most_4pct':oof_selector['harm_rate']<=.04,
        'graph_f1_not_worse':oof_graph['edge_f1']>=oof_anchor['edge_f1'],
        'graph_exact_not_worse':oof_graph['exact']>=oof_anchor['exact'],
    }
    accepted=all(gates.values())
    # Select the FINAL training recipe using inner-validation winners ONLY.
    # Most frequent mode/policy; deterministic ties by declared program order.
    # Choose a JOINT arm/policy pair, not two independently selected values
    # whose combination might never have been validated together.
    selected_pairs=Counter((arm,policy) for arm,policy,_ in configs)
    choice,policy=max(selected_pairs,
       key=lambda pair:(selected_pairs[pair],-list(ARMS).index(pair[0]),
                         -list(POLICIES).index(pair[1])))
    step=int(statistics.median(st for arm,p,st in configs
                               if (arm,p)==(choice,policy)))
    # Explicitly train the full candidate WITHOUT consuming the dev/confirmation
    # sets or choosing a checkpoint using the outer test results.
    torch.manual_seed(seed+909)
    final_model=m.CompetenceRouter(len(experts)).to(device)
    optim=torch.optim.AdamW(final_model.parameters(),lr=lr,weight_decay=.0001)
    rng=random.Random(seed+909)
    for index in range(1,step+1):
        final_model.train();optim.zero_grad(set_to_none=True)
        examples=rng.sample(records,min(4,len(records)))
        losses=[_hard_correction_loss(final_model,rec,experts,device,**ARMS[choice])
                for rec in examples]
        loss=torch.stack([v for v in losses if v is not None]).mean()
        check(bool(torch.isfinite(loss)),'NONFINITE_FINAL_TRAINING_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(final_model.parameters(),1.);
        optim.step()
    final_model.eval()
    candidate_path=dest/'candidate-router.safetensors'
    candidate_manifest=_persist_router(candidate_path,final_model,experts,policy,
                    source_sha=source_sha,role='full_fit_candidate_not_certified',
                    arm=choice,step=step)
    # Check that checkpoint reload, model outputs, and structural decoding agree
    # on *the same captured graph*, without looking at validation/test gold.
    reloaded, reloaded_policy = load_frozen_adapter(candidate_path,experts,device)
    anchor_case=records[0]
    a,_,_=route_graph(final_model,anchor_case,experts,spec,device,policy)
    b,_,_=route_graph(reloaded,anchor_case,experts,spec,device,reloaded_policy)
    check(torch.equal(a,b),'SAVED_CHECKPOINT_INFERENCE_DRIFT')
    report={'schema':SCHEMA,'status':('CANDIDATE_GATE_PASS_RESEARCH_ONLY' if accepted
            else 'CANDIDATE_GATE_BLOCKED'),
        'research_only':True,'production_promoted':False,'g3_unlocked':False,
        'dev_used':False,'confirmation_used':False,
        'source_router_sha256':source_sha,'fit_cases':len(records),
        'independence':'outer base-disjoint; nested inner validation selects candidates',
        'oof_router':oof_selector,'oof_anchor_router':oof_anchor_selector,
        'oof_graph':oof_graph,'oof_anchor_graph':oof_anchor,
        'acceptance_gates':gates,'acceptance_pass':accepted,
        'folds':results,'candidate':{'arm':choice,'steps':step,'policy':policy,
          'path':str(candidate_path),'sha256':candidate_manifest['router_sha256']},
        'limitation':'Out-of-fold test data used to describe this research strategy; '
                     'a newly sealed independent cohort is required for promotion.'}
    old.write(report_path,report)
    emit('CROSS_VALIDATED',{'router':oof_selector,'baseline_router':oof_anchor_selector,
          'graph':oof_graph,'baseline_graph':oof_anchor,
          'gates':gates,'acceptance_pass':accepted})
    emit('CANDIDATE',{'checkpoint':str(candidate_path),
         'sha256':candidate_manifest['router_sha256'],
         'selected_arm':choice,'selected_policy':policy,'training_steps':step,
         'loaded_inference_matches':True,'production_promoted':False})
    emit('SAVED',{'path':str(report_path),'sha256':old.digest(report_path)})
    return report


def selftest():
    # Source-order, intervention and inference-only contract without CUDA or
    # external checkpoints. A mock decoder isolates the routing adaptor.
    from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS
    objects=[[0,2,SPAN_KINDS[0]],[4,6,SPAN_KINDS[1]],[8,10,SPAN_KINDS[0]]]
    n=3; e=7
    torch.manual_seed(990099)
    margins=torch.randn(e,n,n,m.R)
    labels=torch.randint(0,m.R+1,(e,n,n))
    names=[m.ANCHOR]+['synthetic_expert_'+str(i) for i in range(1,e)]
    experts={name:{'sha256':str(i)} for i,name in enumerate(names)}
    rec={'item':{'objects':objects},'predictions':{k:labels[i] for i,k in enumerate(experts)},
         'expert_margins':margins}
    model=m.CompetenceRouter(e)
    model.eval()
    def mock_decoder(_item,scores,_spec):
        check(tuple(scores.shape)==(n,n,m.R+1),'MOCK_DECODER_SCORE_SHAPE')
        return scores.argmax(-1).cpu(),[]
    for policy in POLICIES:
        p,_,meta=route_graph(model,rec,experts,None,'cpu',policy,decoder=mock_decoder)
        check(tuple(p.shape)==(n,n) and meta['switches']>=0,
              'SYNTHETIC_INFERENCE_FAILED')
    gold=torch.randint(0,m.R+1,(n,n))
    gold.fill_diagonal_(0)
    rec['item'].update(targets=gold,row={'family':'encounter','base_id':'synthetic'})
    loss=_hard_correction_loss(model,rec,experts,'cpu',ref_weight=2.,hard_weight=2.)
    loss.backward()
    check(bool(torch.isfinite(loss)) and any(p.grad is not None and
          bool((p.grad.abs()>0).any()) for p in model.parameters()),
          'SYNTHETIC_LEARNING_GRADIENT_INVALID')
    # Real structural decoding with synthetic expert margins, not a mock.
    # Verify train -> validate graph -> checkpoint save -> reload -> infer.
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    examples=[]
    for index in range(3):
        random_gen=torch.Generator().manual_seed(1700+index)
        sample_margins=torch.randn(e,n,n,m.R,generator=random_gen)
        sample_margins[...,0] += 0.25
        preds={}
        for x,name in enumerate(experts):
            logits=fx._score_from_margins(sample_margins[x])
            preds[name],_=old._decode_matrix({'objects':objects},logits,spec)
        target=preds[list(experts)[(index+1)%e]].clone()
        full_gold=set(format_edge(objects,i,j,int(target[i,j]))
                      for i in range(n) for j in range(n) if i!=j and int(target[i,j])>0)
        item={'objects':objects,'targets':target,'full_gold':full_gold,'g1_exact':True,
              'row':{'family':'allocation','base_id':str(index),'mode':'base'}}
        examples.append({'item':item,'expert_margins':sample_margins,
                         'predictions':preds})
    synthetic_model, winner, _ = _train_candidates(
        examples[:2], examples[2:], experts, spec, 'cpu',
        seed=12345,steps=2,eval_every=1,lr=.002,fold=0)
    chosen,_,_=route_graph(synthetic_model,examples[2],experts,spec,'cpu',winner['policy'])
    check(tuple(chosen.shape)==(n,n),'REAL_DECODER_INTEGRATION_SHAPE')
    metrics=_graph_metrics(examples[2:],experts,spec,'cpu',synthetic_model,winner['policy'])
    check(metrics['overall']['cases']==1,'REAL_DECODER_ASSEMBLY_COUNT')
    with tempfile.TemporaryDirectory(prefix='g2_0099_selftest_') as folder:
        base=Path(folder)
        inventory={}
        for i,key in enumerate(experts):
            file=base/('expert_'+str(i)+'.chk')
            file.write_bytes(('frozen'+str(i)).encode())
            inventory[key]={'path':str(file),'sha256':old.digest(file)}
        chk=base/'candidate.safetensors'
        _persist_router(chk,synthetic_model,inventory,winner['policy'],
                        source_sha='synthetic',role='selftest',arm=winner['arm'],
                        step=winner['step'])
        reloaded,p=load_frozen_adapter(chk,inventory,'cpu')
        again,_,_=route_graph(reloaded,examples[2],inventory,spec,'cpu',p)
        check(torch.equal(chosen,again),'SYNTHETIC_RELOAD_CHANGED_OUTPUT')
        # A mutated expert must make the adapter refuse to load.
        first=Path(next(iter(inventory.values()))['path'])
        first.write_bytes(b'TAMPERED')
        try:load_frozen_adapter(chk,inventory,'cpu')
        except ValueError as exc:
            check('FROZEN_EXPERT_HASH_DRIFT' in str(exc),'UNEXPECTED_TAMPER_ERROR')
        else:raise ValueError(TAG+'_TAMPER_WAS_NOT_REJECTED')
    emit('SELFTEST',{'policies_tested':list(POLICIES),'finite_training_gradient':True,
         'input_labels_inference_only':True,'real_decoder_is_default':True,
         'no_production_promotion':True,'real_structured_decoder_checked':True,
         'train_assemble_checkpoint_roundtrip':True,'tamper_rejected':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='run',choices=('audit','selftest','run'))
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--folds',type=int,default=3)
    parser.add_argument('--steps',type=int,default=600)
    parser.add_argument('--eval-every',type=int,default=100)
    parser.add_argument('--lr',type=float,default=.002)
    parser.add_argument('--seed',type=int,default=990099)
    parser.add_argument('--force',action='store_true',
        help='Replace only the 0099 experimental output, never old checkpoints')
    args=parser.parse_args(argv)
    if args.command=='selftest':return selftest()
    if args.command=='audit':return audit(root=args.root,output=args.output)
    return run(root=args.root,output=args.output,device=args.device,
               folds=args.folds,steps=args.steps,eval_every=args.eval_every,
               lr=args.lr,seed=args.seed,force=args.force)


if __name__=='__main__':main()
