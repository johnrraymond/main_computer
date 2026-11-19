"""G1-0076: fail-closed candidate discrimination / trainability bake-off.

Frozen G1/Qwen/LoRA only. First attempt to *fit* 1, then 8 previously
inexact training cases, with no oracle information in inference. If either
stage fails, write a diagnostic report and do NOT run a full-cohort learner
competition. If both pass, fit on 48 TRAIN cases, select on 16 SELECTION,
and audit on 16 AUDIT and the previously examined historical challenge.

Competitors: conservative frozen candidate budgets, class-balanced hard-
negative discrimination, and staged discriminator + loss-augmented structured
set training, at fixed label-blind candidate budgets. The original G1-0074
structured_set_maxmargin decoder is pinned as the unchanged control.

No model-head mutation, G2 training, production promotion, or certification.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as prior
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import _checked_preflight, _summarize, sha256

SCHEMA = 'nanojev.gamer.graph.g1.discrimination-trainability-bakeoff-0076.v1'
CONTROL = coverage.CONTROL
BUDGETS = (64, 256, 1024)  # EXTRA candidates per example, on top of ALL old options
TRAIN_BUDGET = 1024
CLOSED_PENALTY = 3.0
HIDDEN = 40
KIND_COUNT = len(SPAN_KINDS)
DIM = coverage.PROP_DIM + coverage.SET_DIM + 1 + KIND_COUNT
ARMS = (CONTROL, 'closed_budget_64', 'closed_budget_256', 'closed_budget_1024',
        'hard_negative_64', 'hard_negative_256', 'hard_negative_1024',
        'staged_joint_64', 'staged_joint_256', 'staged_joint_1024',
        'balanced_closed_256', 'balanced_staged_256')
REQUIRED_KINDS = coverage.REQUIRED_KINDS
MAX_OPTIONS = coverage.MAX_OPTIONS


def _features(data):
    """Entirely label-blind model inputs. Never reads case.gold."""
    rows = []
    for opt, a, b, new in zip(data.added.case.options, data.prop_features,
                               data.added.features, data.is_new):
        if not 1 <= opt.kind <= KIND_COUNT:
            raise ValueError('G1_0076_UNKNOWN_SPAN_KIND')
        row = list(a) + list(b) + [float(new)] + [float(opt.kind == k)
                                                for k in range(1, KIND_COUNT + 1)]
        if len(row) != DIM or not all(math.isfinite(float(x)) for x in row):
            raise ValueError('G1_0076_FEATURE_CONTRACT')
        rows.append(tuple(max(-6.0, min(6.0, float(x))) / 3.0 for x in row))
    return torch.tensor(rows, dtype=torch.float64)


def _selection_priority(data, index, mode):
    """Frozen-score or balanced type-specific candidate budget; never gold."""
    opt = data.added.case.options[index]
    f = data.prop_features[index]
    margin, cond = f[1], f[2]
    if mode == 'frozen':
        return (margin, cond, data.base_scores[index] / max(1, opt.end-opt.start),
                -opt.start, -opt.end, -opt.kind)
    if mode == 'balanced':
        # Type-specific rank remains label-blind and is fixed *before* tuning.
        # Quotas are enforced in _restrict(), not by a gold-type distribution.
        return (margin + .4 * math.log(max(1.e-8, cond)), cond,
                -abs((opt.end-opt.start)-3), -opt.start, -opt.end)
    raise ValueError('G1_0076_UNKNOWN_RANK')


def _restrict(data, extra, mode='frozen'):
    """Preserve every old candidate, slice only the extra candidate universe.

    Frozen feature vectors, scores and source sequence are not regenerated;
    a smaller lattice has *exactly* the same options and scores for retained
    objects. `extra` specifies new candidates, not a destructive total cap.
    """
    if extra not in BUDGETS and extra != MAX_OPTIONS:
        raise ValueError('G1_0076_UNKNOWN_BUDGET')
    old = [i for i, new in enumerate(data.is_new) if not new]
    added = [i for i, new in enumerate(data.is_new) if new]
    if len(old) != len(data.base.case.options):
        raise ValueError('G1_0076_BASELINE_LATTICE_DRIFT')
    if extra == MAX_OPTIONS:
        picked = added
    elif mode == 'balanced':
        # Equal quotas across all canonical span kinds, plus a score spillover.
        # The quotas never
        # depend on how many actors/actions are in the gold labels.
        quota = extra // KIND_COUNT
        chosen = set()
        for kind in range(1, KIND_COUNT + 1):
            group = (j for j in added if data.added.case.options[j].kind == kind)
            top = sorted(group, key=lambda j: _selection_priority(data, j, mode),
                         reverse=True)
            chosen.update(top[:quota])
        rest = sorted((j for j in added if j not in chosen),
                      key=lambda j: _selection_priority(data, j, mode), reverse=True)
        chosen.update(rest[:max(0, extra - len(chosen))])
        picked = sorted(chosen)
    else:
        picked = sorted(added, key=lambda j: _selection_priority(data, j, mode),
                        reverse=True)[:extra]
    indices = sorted(old + picked, key=lambda j:(data.added.case.options[j].end,
                        data.added.case.options[j].start, data.added.case.options[j].kind))
    options = [data.added.case.options[j] for j in indices]
    base = data.added.case
    case = learned.Case(base.model, base.split, base.family, base.prompt_hash,
        base.n_tokens, options, set(base.control), set(base.gold),
        list(base.control_prediction))
    subset = coverage.Expanded(data.base, prior.Encoded(case,
        [data.added.features[j] for j in indices]),
        tuple(data.prop_features[j] for j in indices),
        tuple(data.is_new[j] for j in indices),
        tuple(data.base_scores[j] for j in indices),
        data.candidate_count_before_budget,
        data.discarded_by_budget + len(added) - len(picked),
        data.valid_token_spans, data.raw_span_gold_types)
    if len(subset.base.case.options) + sum(subset.is_new) > MAX_OPTIONS:
        raise ValueError('G1_0076_CANDIDATE_OVERFLOW')
    return subset


class Discriminator(torch.nn.Module):
    """Tiny nonlinear scorer: explicitly deterministic hidden numerical fabric.

    A zero output projection makes the learned residual *exactly zero* before
    training. The feature map is a deterministic signed dyadic matrix; no
    torch.nn.Linear constructor or random initializer is called.
    """
    def __init__(self):
        super().__init__()
        # Signed low-discrepancy dyadic pattern with bounded fan-in magnitude.
        matrix = [[((1.0 if ((i + j * 3) % 2) else -1.0) / (16.0 *
                   (2.0 ** ((i * 7 + j * 5) % 5)))) for j in range(HIDDEN)]
                  for i in range(DIM)]
        self.hidden = torch.nn.Parameter(torch.tensor(matrix,dtype=torch.float64))
        self.proj = torch.nn.Parameter(torch.zeros(HIDDEN,dtype=torch.float64))
        self.type_offset = torch.nn.Parameter(torch.zeros(KIND_COUNT,dtype=torch.float64))
        self.new_offset = torch.nn.Parameter(torch.zeros(1,dtype=torch.float64))

    def forward(self, inputs, kinds, new):
        h = torch.tanh(inputs @ self.hidden)
        return h @ self.proj + self.type_offset[kinds] + self.new_offset[0]*new


def _tensor_cache(data):
    features = _features(data)
    opt = data.added.case.options
    if any(not 1 <= o.kind <= KIND_COUNT for o in opt):
        raise ValueError('G1_0076_UNKNOWN_SPAN_KIND')
    kinds = torch.tensor([o.kind-1 for o in opt],dtype=torch.long)
    new = torch.tensor([float(v) for v in data.is_new],dtype=torch.float64)
    width = torch.tensor([o.end-o.start for o in opt],dtype=torch.float64)
    fixed = torch.tensor(data.base_scores,dtype=torch.float64)
    return features,kinds,new,width,fixed


def _model_scores(data, model, *, cache=None):
    """Inference path, strictly frozen inputs and learned parameters, no gold."""
    feats,kinds,new,width,fixed = cache if cache is not None else _tensor_cache(data)
    correction = model(feats,kinds,new)
    scores = fixed + width*(correction - CLOSED_PENALTY*new)
    if not bool(torch.isfinite(scores).all()):
        raise ValueError('G1_0076_NONFINITE_SCORE')
    return scores


def _predict(data, arm, weights, model=None, *, cache=None):
    if arm == CONTROL:
        return coverage._decode(data,CONTROL,reference_weights=weights)
    if arm.startswith('closed_budget_') or arm.startswith('balanced_closed_'):
        scores=[score-CLOSED_PENALTY*(o.end-o.start)*float(new)
            for o,score,new in zip(data.added.case.options,data.base_scores,data.is_new)]
    else:
        if model is None: raise ValueError('G1_0076_MISSING_TRAINED_MODEL')
        with torch.no_grad():
            scores=_model_scores(data,model,cache=cache).tolist()
    indices=prior._viterbi(data.added,scores)
    return {data.added.case.options[j].char_key for j in indices
            if data.added.case.options[j].char_key is not None}


def _hard_rows(data, cache, *, per_case=256, forced=()):
    """Oracle labels consulted ONLY here for supervised fitting.

    Every positive gold option is included. Negatives are mined from the
    overlapping candidate neighborhood and currently high-scoring false
    candidates; the procedure never feeds gold back to inference.
    """
    options=data.added.case.options
    gold=data.added.case.gold
    positive=[i for i,o in enumerate(options) if o.char_key in gold]
    if not positive: raise ValueError('G1_0076_NO_TRAIN_POSITIVE')
    posopts=[options[i] for i in positive]
    _,_,new,width,fixed=cache
    negatives=[]
    for i,o in enumerate(options):
        if o.char_key in gold:continue
        overlaps=any(o.start<p.end and p.start<o.end for p in posopts)
        score=float(fixed[i]/width[i] - CLOSED_PENALTY*new[i])
        if overlaps or score > -.2:
            negatives.append((int(overlaps),score,i))
    negatives.sort(key=lambda t:(-t[0],-t[1],t[2]))
    neg=[t[-1] for t in negatives[:per_case]]
    # Counterexample-guided mining: retain observed false decoded objects even
    # if the original top-256 overlap heuristic did not admit them.
    for index in sorted(set(forced)):
        if not 0 <= index < len(options) or options[index].char_key in gold:
            raise ValueError('G1_0076_INVALID_FORCED_NEGATIVE')
        if index not in neg:neg.append(index)
    if not neg:raise ValueError('G1_0076_NO_TRAIN_NEGATIVE')
    return positive,neg


def _fit_hard(train, *, steps=240, start=None, lr=.035, forced_by_case=None):
    """Class-balanced pointwise hard-negative discriminator."""
    model=Discriminator() if start is None else copy.deepcopy(start)
    optimizer=torch.optim.Adam(model.parameters(),lr=lr)
    batches=[];counts=Counter()
    if forced_by_case is None:
        forced_by_case=[() for _ in train]
    if len(forced_by_case)!=len(train):
        raise ValueError('G1_0076_FORCED_NEGATIVE_COUNT')
    for d, forced in zip(train, forced_by_case):
        cache=_tensor_cache(d)
        pos,neg=_hard_rows(d,cache,forced=forced)
        subset=pos+neg
        fitted_cache=tuple(t[subset] for t in cache)
        batches.append((d,fitted_cache,list(range(len(pos))),
                        list(range(len(pos),len(subset)))))
        counts['positive']+=len(pos);counts['hard_negative']+=len(neg)
    losses=[]
    for step in range(steps):
        optimizer.zero_grad()
        total=torch.zeros((),dtype=torch.float64)
        for data,cache,pos,neg in batches:
            scores=_model_scores(data,model,cache=cache)
            width=cache[3]
            margin=scores/width
            total=total+(.5*F.softplus(1.0-margin[pos]).mean()+
                         .5*F.softplus(1.0+margin[neg]).mean())/len(batches)
        total=total+1.e-5*(model.proj.square().sum()+model.hidden.square().sum())
        if not torch.isfinite(total):raise ValueError('G1_0076_NONFINITE_TRAIN_LOSS')
        total.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),max_norm=5.0)
        optimizer.step()
        with torch.no_grad():
            for p in model.parameters():p.clamp_(-8.0,8.0)
        if step in (0,steps//2,steps-1):losses.append(float(total.detach()))
        if steps>=90 and step in (0,steps//4,steps//2,3*steps//4,steps-1):
            print('G1_DISCRIMINATION_HARD '+json.dumps({'cases':len(batches),
                'step':step+1,'total_steps':steps,'loss':round(float(total.detach()),6)}),
                flush=True)
    return model,{'steps':steps,'positive':counts['positive'],
                  'hard_negative':counts['hard_negative'],'loss_samples':losses}


def _training_quality(train, model):
    """Training-only ranking: exact sets, correct objects, false objects.

    The old trainer saved the FIRST epoch whenever every epoch had zero exact
    sets. That discarded all later sub-exact improvement. Gold is consulted
    only to evaluate fitted models on TRAIN, never by inference.
    """
    correct=0;false=0;missed=0;exact=0;problems=[]
    for d in train:
        predicted=_predict(d,'staged_joint_1024',(),model)
        gold=d.added.case.gold
        right=len(predicted&gold)
        fp=len(predicted-gold)
        fn=len(gold-predicted)
        correct+=right;false+=fp;missed+=fn
        exact+=int(not fp and not fn)
        problems.append({'prompt_sha256':d.added.case.prompt_hash,
            'correct':right,'expected':len(gold),'false_positive':fp,
            'missing':fn,'exact':not fp and not fn})
    # Exact reconstruction is primary; partial progress is the tie break.
    return (exact,correct,-false,-missed),problems


def _mine_counterexamples(train, model, weights, *, rounds=3, steps=120):
    """Fit observed false *decoded* options, not just a fixed negative pool.

    Only TRAIN labels choose negatives. All candidate generation, prediction
    and inference scoring remain label blind; checkpoint parameters are frozen.
    Keep the best model even if an additional optimization round regresses.
    """
    best=copy.deepcopy(model)
    best_quality,_=_training_quality(train,best)
    forced=[set() for _ in train]
    history=[]
    for round_id in range(1,rounds+1):
        new=0
        for idx,d in enumerate(train):
            predicted=_predict(d,'hard_negative_1024',weights,model)
            false=predicted-d.added.case.gold
            for j,opt in enumerate(d.added.case.options):
                if opt.char_key in false and j not in forced[idx]:
                    forced[idx].add(j);new+=1
        if new==0:
            history.append({'round':round_id,'new_false_candidates':0,
                            'stop':'NO_NEW_DECODED_COUNTEREXAMPLES'})
            break
        model, fit=_fit_hard(train,steps=steps,start=model,
                           forced_by_case=[sorted(x) for x in forced])
        quality,problems=_training_quality(train,model)
        if quality>best_quality:
            best=copy.deepcopy(model);best_quality=quality
        history.append({'round':round_id,'new_false_candidates':new,
                        'total_forced':sum(len(x) for x in forced),
                        'quality':list(quality),'fit':fit,
                        'correct':sum(x['correct'] for x in problems),
                        'missing':sum(x['missing'] for x in problems),
                        'false_positive':sum(x['false_positive'] for x in problems)})
        if best_quality[0]==len(train):break
    return best,{'rounds':history,'best_quality':list(best_quality)}


def _fit_structured(train, start, *, epochs=16, lr=.016, strict_targets=True):
    """Stage-2 whole-assignment max-margin optimization after hard negatives.

    Loss-augmented Viterbi returns a non-differentiable assignment. The hinge
    gradient flows to candidate scores, with the original G1-0074 interface
    held fixed. No oracle is consulted by `_predict` or `_model_scores`.
    """
    model=copy.deepcopy(start)
    opt=torch.optim.Adam(model.parameters(),lr=lr)
    caches=[_tensor_cache(d) for d in train]
    good=[];unreachable=0
    for d in train:
        indices=prior._viterbi(d.added,[0.0]*len(d.added.case.options),
                              oracle_gold=d.added.case.gold)
        recovered={d.added.case.options[i].char_key for i in indices}
        unreachable+=len(d.added.case.gold-recovered)
        if strict_targets and recovered != d.added.case.gold:
            raise ValueError('G1_0076_UNREACHABLE_STRUCTURED_TRAIN_TARGET')
        good.append(indices)
    violations=0; history=[]; quality_history=[]
    best=copy.deepcopy(model)
    best_quality,_=_training_quality(train,model)
    best_epoch=0
    for epoch in range(epochs):
        for d,cache,goal in zip(train,caches,good):
            opt.zero_grad()
            scored=_model_scores(d,model,cache=cache)
            pred=prior._viterbi(d.added,scored.detach().tolist(),
                        loss_gold=d.added.case.gold,delta=.35)
            if set(pred)==set(goal): continue
            # Structured hinge, subgradient in the wrong and correct paths.
            loss = scored[list(pred)].sum()-scored[list(goal)].sum() + \
                   .35*len(set(pred).symmetric_difference(goal))
            if float(loss.detach()) <= 0:continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),max_norm=5.0)
            opt.step()
            with torch.no_grad():
                for p in model.parameters():p.clamp_(-8.0,8.0)
            violations+=1
        quality,_=_training_quality(train,model)
        correct=quality[0]
        history.append(correct)
        quality_history.append(list(quality))
        if epochs>=12 and (epoch==0 or (epoch+1)%8==0 or correct==len(train)):
            print('G1_DISCRIMINATION_STRUCTURED '+json.dumps({'cases':len(train),
                'epoch':epoch+1,'train_exact':correct,'target':len(train),
                'correct_objects':quality[1],'false_objects':-quality[2],
                'missing_objects':-quality[3],
                'unreachable_gold':unreachable}),flush=True)
        if quality>best_quality:
            best_quality=quality;best=copy.deepcopy(model);best_epoch=epoch+1
        if correct==len(train):break
    _,errors=_training_quality(train,best)
    return best,{'epochs_run':len(history),'violations':violations,
                 'unreachable_gold_under_budget':unreachable,
                 'strict_targets':strict_targets,
                 'train_exact_by_epoch':history,'quality_by_epoch':quality_history,
                 'best_epoch':best_epoch,'best_quality':list(best_quality),
                 'best_train_exact':best_quality[0],
                 'remaining_train_object_errors':errors}


def _trainability(train,weights,*,one_steps=440,eight_steps=600):
    """Exactly 1 then 8 HARD training examples; block later phases on failure.

    All examples are drawn deterministically from TRAIN only. The candidates
    have to be reachable, so an optimizer failure cannot be confused with a
    missing coordinate. Exact reconstruction, not F1, defines success.
    """
    difficult=[];skipped=Counter()
    for data in train:
        if _predict(data,CONTROL,weights)==data.added.case.gold:
            skipped['already_exact']+=1;continue
        oracle=prior._viterbi(data.added,[0.]*len(data.added.case.options),
                             oracle_gold=data.added.case.gold)
        reached={data.added.case.options[j].char_key for j in oracle}
        if reached != data.added.case.gold:
            skipped['unreachable']+=1;continue
        difficult.append(data)
    if len(difficult)<8:
        return {'status':'BLOCKED_INSUFFICIENT_HARD_REACHABLE_TRAIN_CASES',
                'hard_available':len(difficult),'skipped':dict(skipped)},None
    ledger={'status':'PENDING','hard_available':len(difficult),
            'skipped':dict(skipped),'one_case_sha256':difficult[0].added.case.prompt_hash,
            'eight_case_sha256':[d.added.case.prompt_hash for d in difficult[:8]],
            'stages':{}}
    candidate=None
    for count,steps in ((1,one_steps),(8,eight_steps)):
        cohort=difficult[:count]
        hard,fit=_fit_hard(cohort,steps=steps)
        hard_quality,hard_errors=_training_quality(cohort,hard)
        hard_exact=hard_quality[0]
        mined=None;mining_log={}
        if hard_exact<count:
            mined,mining_log=_mine_counterexamples(cohort,hard,weights,
                        rounds=3,steps=120 if count==1 else 160)
        mined_quality,mined_errors=(_training_quality(cohort,mined) if mined is not None
                                    else (hard_quality,hard_errors))
        initial=mined if mined is not None and mined_quality>hard_quality else hard
        structured=None;stage_diag={}
        if max(hard_exact,mined_quality[0])<count:
            structured,stage_diag=_fit_structured(cohort,initial,
                              epochs=96 if count==1 else 120)
        quality_candidates=[(hard_quality,hard,'hard_negative')]
        if mined is not None:
            quality_candidates.append((mined_quality,mined,'counterexample_mined'))
        if structured is not None:
            structured_quality,structured_errors=_training_quality(cohort,structured)
            quality_candidates.append((structured_quality,structured,'structured'))
        else:
            structured_quality=None;structured_errors=None
        achieved_quality,best,source=max(quality_candidates,key=lambda t:t[0])
        achieved=achieved_quality[0]
        _,remaining=_training_quality(cohort,best)
        ledger['stages'][str(count)]={
            'required_exact':count,'hard_negative_exact':hard_exact,
            'counterexample_mined_exact':mined_quality[0] if mined else None,
            'structured_exact':structured_quality[0] if structured_quality else None,
            'structured_attempted':structured is not None,
            'best_exact':achieved,'best_source':source,
            'best_quality':list(achieved_quality),'remaining_object_errors':remaining,
            'fit':fit,'counterexample_mining':mining_log,
            'structured_fit':stage_diag,
            'status':'PASS' if achieved==count else 'FAIL_TRAINABILITY'}
        print('G1_DISCRIMINATION_MEMORIZE '+json.dumps({'cases':count,
            'hard_negative_exact':hard_exact,
            'counterexample_mined_exact':mined_quality[0] if mined else None,
            'structured_exact':structured_quality[0] if structured_quality else None,
            'best_correct_objects':achieved_quality[1],
            'best_missing_objects':-achieved_quality[3],
            'best_false_objects':-achieved_quality[2],
            'status':ledger['stages'][str(count)]['status']}),flush=True)
        if achieved<count:
            ledger['status']='BLOCKED_'+str(count)+'_CASE_TRAINABILITY'
            return ledger,None
        candidate=best
    ledger['status']='PASS_1_AND_8'
    return ledger,candidate


def _oracle_budget(cases):
    pairs=[];n_added=0;total=0
    for d in cases:
        oracle=prior._viterbi(d.added,[0.]*len(d.added.case.options),
                             oracle_gold=d.added.case.gold)
        pairs.append(({d.added.case.options[i].char_key for i in oracle},d.added.case.gold))
        n_added+=sum(d.is_new); total+=len(d.added.case.options)
    return {'oracle_not_inference':_summarize(pairs),'mean_new':n_added/max(1,len(cases)),
            'mean_options':total/max(1,len(cases))}


def _metric(cases,arm,weights,model=None):
    pairs=[(_predict(d,arm,weights,model),d.added.case.gold) for d in cases]
    return _summarize(pairs),pairs


def _choose(metrics):
    control=metrics[CONTROL]
    viable=[CONTROL]
    for name in ARMS[1:]:
        m=metrics[name]
        if (m['exact_prompts']>=control['exact_prompts'] and
            m['f1']>=control['f1']-1.e-12 and
            m['precision']>=control['precision']-.005 and
            all(m['by_kind'].get(k,{'recall':0.0})['recall']+1.e-12 >=
                control['by_kind'][k]['recall'] for k in REQUIRED_KINDS
                if k in control['by_kind']) and
            (m['exact_prompts']>control['exact_prompts'] or
             m['f1']>control['f1']+1.e-9)):
            viable.append(name)
    return max(viable,key=lambda arm:(metrics[arm]['exact_prompts'],
        metrics[arm]['f1'],metrics[arm]['precision'],-ARMS.index(arm)))


def _reference(report_dir, entries, dataset_hash, phase_rows, structural_hash, learned_hash):
    source=Path(report_dir).resolve()/'report.json'
    record=json.loads(source.read_text(encoding='utf8'))
    if (record.get('schema')!=coverage.SCHEMA or
        record.get('status')!='EXPLORATORY_CANDIDATE_COVERAGE_NOT_CERTIFIED' or
        record.get('selected')!={'legacy':CONTROL,'cutover':CONTROL} or
        record.get('design',{}).get('proposal_budget')!=MAX_OPTIONS or
        record.get('lineage',{}).get('dataset_sha256')!=dataset_hash or
        record.get('lineage',{}).get('structural_reference_sha256')!=structural_hash or
        record.get('lineage',{}).get('learned_reference_sha256')!=learned_hash or
        record.get('lineage',{}).get('checkpoint_sha256')!={
            k:v['manifest']['head_sha256'] for k,v in entries.items()} or
        record.get('design',{}).get('cohorts')!={
            k:len(v) for k,v in phase_rows.items() if k in ('train','selection','audit')} or
        record.get('design',{}).get('historical_challenge_cases')!=len(phase_rows['challenge']) or
        any(record.get(k) is not False for k in (
            'g1_head_weights_modified','production_promotion','g2_unlocked',
            'sealed_test_used','certification_authorized'))):
        raise ValueError('G1_0076_REFERENCE_LINEAGE_OR_CONTROL_DRIFT')
    return record,sha256(source)


def _verify_control(record, phase, name, cases,weights):
    expected=record['phases'][phase][name]['selected_metrics']
    observed=_metric(cases,CONTROL,weights)[0]
    for key in ('cases','correct','expected','predicted','exact_prompts','false_positive_objects'):
        if observed[key]!=expected[key]:
            raise ValueError('G1_0076_CONTROL_PARITY_FAIL:'+phase+':'+name+':'+key)
    for kind in REQUIRED_KINDS:
        if observed['by_kind'][kind]!=expected['by_kind'][kind]:
            raise ValueError('G1_0076_CONTROL_KIND_PARITY_FAIL:'+phase+':'+name+':'+kind)
    # Candidate count and expanded oracle ceiling must also reproduce.
    oracle=_oracle_budget(cases)['oracle_not_inference']
    before=record['phases'][phase][name]['oracle_candidate_coverage_not_inference'][
        'expanded_reachable_nonoverlap']
    for key in ('cases','correct','expected','exact_prompts'):
        if oracle[key]!=before[key]:
            raise ValueError('G1_0076_ORACLE_PARITY_FAIL:'+phase+':'+name+':'+key)


def _prepare_phase(rows,models):
    return {p:{name:{**{b:[_restrict(c,b,'frozen') for c in cases]
                              for b in BUDGETS},
                      'balanced_256':[_restrict(c,256,'balanced') for c in cases]}
                    for name,cases in models[p].items()}
            for p in rows}


def _variants(cases,budgets,weights,models):
    output={}
    for arm in ARMS:
        if arm==CONTROL:
            result=_metric(cases,CONTROL,weights)
        else:
            b=int(arm.rsplit('_',1)[1]);
            c=budgets['balanced_256' if arm.startswith('balanced_') else b]
            m=None if arm.startswith(('closed_','balanced_closed_')) else models[
                'hard_negative' if arm.startswith('hard_negative') else 'staged_joint']
            result=_metric(c,arm,weights,m)
        output[arm]=result
    return output


def _case_rows(cases,budgets,weights,models,selected):
    rows=[]
    for i,data in enumerate(cases):
        gold=data.added.case.gold
        baseline=_predict(data,CONTROL,weights)
        record={'phase':data.added.case.split,'model':data.added.case.model,
                'prompt_sha256':data.added.case.prompt_hash,'family':data.added.case.family,
                'gold_count':len(gold),'control_exact':baseline==gold,
                'selected':selected,'selected_exact':None,'arms':{}}
        for arm in ARMS[1:]:
            b=int(arm.rsplit('_',1)[1]);
            case=budgets['balanced_256' if arm.startswith('balanced_') else b][i]
            model=None if arm.startswith(('closed_','balanced_closed_')) else models[
                'hard_negative' if arm.startswith('hard_negative') else 'staged_joint']
            pred=_predict(case,arm,weights,model)
            record['arms'][arm]={
                'exact':pred==gold,
                'recovered_correct':[list(x) for x in sorted((pred&gold)-(baseline&gold))],
                'lost_correct':[list(x) for x in sorted((baseline&gold)-(pred&gold))],
                'introduced_false':[list(x) for x in sorted((pred-gold)-(baseline-gold))],
                'removed_false':[list(x) for x in sorted((baseline-gold)-(pred-gold))]}
        record['selected_exact']=baseline==gold if selected==CONTROL else record['arms'][selected]['exact']
        rows.append(record)
    return rows


def _export_model(model):
    # Portable numeric coefficients, not opaque pickle/checkpoint blobs.
    params={k:v.detach().cpu().tolist() for k,v in model.state_dict().items()}
    raw=json.dumps(params,sort_keys=True,allow_nan=False,separators=(',',':'))
    return {'parameters':params,'sha256':hashlib.sha256(raw.encode('utf8')).hexdigest(),
            'deterministic_zero_output_and_dyadic_hidden_initialization':True}


def _write(dest,report,rows):
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf8')
    with (dest/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in rows:f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')


def run(*,dataset,legacy,cutover,structural_reference,learned_reference,coverage_reference,
        output,device='cuda',max_length=2048,independent_dataset=None,
        one_steps=440,eight_steps=600,full_steps=300):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dest=Path(output).resolve()
    sources=[Path(k).resolve() for k in (structural_reference,learned_reference,coverage_reference)]
    if dest.exists():raise FileExistsError('G1_0076_IMMUTABLE_OUTPUT')
    if any(dest==s or s in dest.parents for s in sources):
        raise ValueError('G1_0076_OUTPUT_INSIDE_REFERENCE')
    if min(one_steps,eight_steps,full_steps,max_length)<=0:
        raise ValueError('G1_0076_INVALID_CONFIG')
    entries,ds_hash,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    partitions=learned._partition(cohorts['dev'])
    structural,struct_hash,weights=coverage._require_previous(sources[0],entries,
        ds_hash,partitions,len(cohorts['challenge']))
    prior_source,learned_hash=coverage._require_lattice_config(sources[1],structural)
    extra,external_hash=prior._load_external(independent_dataset,dataset,cohorts)
    phase_rows={**partitions,'challenge':cohorts['challenge']}
    if extra is not None: phase_rows['external']=extra
    reference,coverage_hash=_reference(sources[2],entries,ds_hash,phase_rows,
                                        struct_hash,learned_hash)
    heads={name:cur._load_checkpoint(e['path'],device=device)[0].eval()
           for name,e in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    captured={}
    for phase,rows in phase_rows.items():
        captured[phase]=coverage._capture(rows,heads,model,tok,device=device,
            max_length=max_length,prior_selected=prior_source,reference_weights=weights,
            split=phase,budget=MAX_OPTIONS)
        if phase in ('train','selection','audit','challenge'):
            for name,batch in captured[phase].items():
                _verify_control(reference,phase,name,batch,weights[name])
    # Label-blind candidate budget variants; all old options survive intact.
    restricted=_prepare_phase(phase_rows,captured)
    training={};trained={};eligible=True
    for name in entries:
        # Stage 1: 1 case; stage 2: 8, both among the 48 TRAIN only.
        mem,seed=_trainability(captured['train'][name],weights[name],
                                one_steps=one_steps,eight_steps=eight_steps)
        training[name]={'trainability':mem}
        print('G1_DISCRIMINATION_TRAINABILITY '+json.dumps({
            'model':name,'status':mem['status']},sort_keys=True),flush=True)
        if seed is None:
            eligible=False
            trained[name]=None
            continue
        # Both full-cohort learners share the *same* 48 case training material.
        # Staging begins from hard-negative discrimination, then fine-tunes
        # on whole-assignment errors; the original G1-0074 weights are frozen.
        hard,hard_log=_fit_hard(restricted['train'][name][TRAIN_BUDGET],steps=full_steps)
        joint,joint_log=_fit_structured(restricted['train'][name][TRAIN_BUDGET],hard,
                                      epochs=20,strict_targets=False)
        training[name]['full']={'hard_negative':hard_log,'staged_joint':joint_log,
                                'training_uses_selection_or_audit':False}
        trained[name]={'hard_negative':hard,'staged_joint':joint}
    status=('EXPLORATORY_TRAINABLE_NOT_CERTIFIED' if eligible else
            'TRAINABILITY_BLOCKED_NO_FULL_COMPETITION')
    phases={};cases=[];selected={n:CONTROL for n in entries}
    budget_ceilings={p:{name:{str(b):_oracle_budget(restricted[p][name][b])
                          for b in (*BUDGETS,'balanced_256')}
                       for name in entries} for p in phase_rows}
    if eligible:
        selection={}
        for name in entries:
            metrics=_variants(captured['selection'][name],restricted['selection'][name],
                              weights[name],trained[name])
            selection[name]={arm:value[0] for arm,value in metrics.items()}
            selected[name]=_choose(selection[name])
        print('G1_DISCRIMINATION_SELECTION '+json.dumps(selected,sort_keys=True),flush=True)
        for phase in phase_rows:
            phases[phase]={}
            for name in entries:
                evals=_variants(captured[phase][name],restricted[phase][name],
                                weights[name],trained[name])
                metrics={a:v[0] for a,v in evals.items()}
                phases[phase][name]={'arms':metrics,'selected':selected[name],
                    'selected_metrics':metrics[selected[name]],'control_metrics':metrics[CONTROL],
                    'changes_vs_control':{a:admission._changes(evals[CONTROL][1],evals[a][1])
                                          for a in ARMS[1:]}}
                cases.extend(_case_rows(captured[phase][name],restricted[phase][name],
                                        weights[name],trained[name],selected[name]))
    retrospective={}
    if eligible:
        for name in entries:
            result={arm:[] for arm in ARMS}
            for phase in ('train','selection','audit'):
                rows=captured[phase][name]
                evals=_variants(rows,restricted[phase][name],weights[name],trained[name])
                for arm in ARMS:result[arm].extend(evals[arm][1])
            summary={arm:_summarize(sets) for arm,sets in result.items()}
            retrospective[name]={'arms':summary,'selected_metrics':summary[selected[name]],
                                 'control_metrics':summary[CONTROL],
                                 'includes_training_and_selection':True}
            prev=reference['full_dev_retrospective_not_holdout'][name]['selected_metrics']
            for key in ('cases','correct','expected','predicted','exact_prompts'):
                if summary[CONTROL][key]!=prev[key]:
                    raise ValueError('G1_0076_FULL_DEV_CONTROL_DRIFT:'+name+':'+key)
    # Hash checks also protect against inadvertent external edits during capture.
    for source,digest in ((dataset,ds_hash),(sources[0]/'report.json',struct_hash),
                          (sources[1]/'report.json',learned_hash),
                          (sources[2]/'report.json',coverage_hash)):
        if sha256(source)!=digest:raise ValueError('G1_0076_INPUT_DRIFT')
    if extra is not None and sha256(independent_dataset)!=external_hash:
        raise ValueError('G1_0076_EXTERNAL_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_0076_ADAPTER_DRIFT')
    for name,e in entries.items():
        if sha256(Path(e['path'])/cur.HEAD_FILE)!=e['manifest']['head_sha256']:
            raise ValueError('G1_0076_G1_CHECKPOINT_DRIFT')
    learned_coefficients={name:{arm:_export_model(model) for arm,model in pair.items()}
                          for name,pair in trained.items() if pair is not None}
    report={'schema':SCHEMA,'status':status,
        'design':{'control':'exact frozen G1-0074 structured_set_maxmargin',
            'competitors':ARMS,'extra_candidate_budgets':BUDGETS,
            'candidate_budget_ranking':'frozen neural margins or fixed equal-type quotas; no oracle ranking',
            'balanced_budget_variants':('balanced_closed_256','balanced_staged_256'),
            'canonical_span_kinds':SPAN_KINDS,
            'balanced_quotas_per_kind':256//KIND_COUNT,
            'new_candidate_initial_penalty_per_token':CLOSED_PENALTY,
            'trainability_required':'exact fit on 1 then 8 prior-inexact reachable TRAIN cases',
            'trainability_one_steps':one_steps,'trainability_eight_steps':eight_steps,
            'full_fit_steps':full_steps,'cohorts':{p:len(r) for p,r in partitions.items()},
            'historical_challenge_cases':len(cohorts['challenge']),
            'external_cases':len(extra or []),
            'training_selection_audit_separated':True,
            'historical_challenge_previously_exposed':True,
            'audit_cohort_previously_exposed':True,
            'independent_provenance_not_verified':True,
            'oracle_used_only_in_training_and_posthoc_coverage':True,
            'gold_never_used_for_candidate_ranking_or_inference':True},
        'lineage':{'dataset_sha256':ds_hash,'coverage_reference_sha256':coverage_hash,
            'structural_reference_sha256':struct_hash,
            'learned_reference_sha256':learned_hash,
            'source_adapter_sha256':manifest['source_adapter_sha256'],
            'checkpoint_sha256':{name:e['manifest']['head_sha256'] for name,e in entries.items()},
            'model_specific_0071_source':prior_source,'external_dataset_sha256':external_hash},
        'trainability':training,'learned_interface_parameters':learned_coefficients,
        'candidate_budget_oracle_coverage_not_inference':budget_ceilings,
        'selected':selected,'phases':phases,
        'full_dev_retrospective_not_holdout':retrospective,
        'g1_head_weights_modified':False,'interface_training_performed':True,
        'production_promotion':False,'g2_unlocked':False,'sealed_test_used':False,
        'certification_authorized':False}
    _write(dest,report,cases)
    print('G1_DISCRIMINATION_RESULT '+json.dumps({'status':status,'selected':selected,
        'audit_exact':{n:phases.get('audit',{}).get(n,{}).get('selected_metrics',{}).get('exact_prompts')
                       for n in entries},
        'oracle_budget_ceilings_train':{n:{b:budget_ceilings['train'][n][b][
              'oracle_not_inference']['exact_prompts'] for b in map(str,BUDGETS)}
              for n in entries}},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','legacy','cutover','structural-reference',
                 'learned-reference','coverage-reference','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    parser.add_argument('--one-steps',type=int,default=440)
    parser.add_argument('--eight-steps',type=int,default=600)
    parser.add_argument('--full-steps',type=int,default=300)
    parser.add_argument('--independent-dataset')
    return run(**vars(parser.parse_args(argv)))


if __name__=='__main__':
    result=main()
    if result['status']=='TRAINABILITY_BLOCKED_NO_FULL_COMPETITION':
        raise SystemExit(2)
