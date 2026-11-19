"""G1-0075: candidate-coverage and structured-selection bake-off (research only).

Freeze both G1 heads, Qwen and LoRA. Compete three distinct ways of adding
previously unreachable neural span/type proposals against the exact G1-0074
structured-set max-margin control:
  * frozen-logit, deterministic candidate expansion;
  * supervised learned whole-span proposals with the structured selector frozen;
  * jointly optimized proposal scores and structured-set scores.

All proposal construction and inference features use only frozen neural logits.
Oracle labels are accessed in fitting and post-hoc diagnostics ONLY. The prior
G1-0074 report must match per phase, case totals, checkpoint hashes, and selected
interface parameters. No G2 activation, production mutation, or certification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as prior
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission
from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as complete
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import _summarize, _checked_preflight, sha256

SCHEMA='nanojev.gamer.graph.g1.candidate-coverage-bakeoff-0075.v1'
CONTROL='structured_set_maxmargin'
FROZEN='frozen_score_candidate_expansion'
PROPOSAL='learned_whole_span_proposal'
JOINT='joint_proposal_structured_set'
ARMS=(CONTROL,FROZEN,PROPOSAL,JOINT)
MAX_OPTIONS=learned.MAX_OPTIONS
PROP_DIM=18
SET_DIM=prior.DIM
REQUIRED_KINDS=prior.REQUIRED_KINDS
MIN_LOGIT_MARGIN=-12.0
MAX_TYPE_GAP=12.0
MAX_WEIGHT=prior.MAX_WEIGHT
# Predeclared scores and scales; they are not tuned using test labels.
PROPOSAL_SCALE=0.40
SET_SCALE=0.30


@dataclass
class Expanded:
    base: prior.Encoded
    added: prior.Encoded
    prop_features: tuple[tuple[float,...], ...]
    is_new: tuple[bool,...]
    base_scores: tuple[float,...]
    candidate_count_before_budget: int
    discarded_by_budget: int
    valid_token_spans: int
    raw_span_gold_types: frozenset[tuple]  # label-blind coordinate inventory; oracle reads it later
    # No label information is required by any of the feature/score methods.


def _proposal_features(opt,n):
    """Inferred from frozen scores/geometry only; never accepts target labels."""
    f=opt.features
    width=opt.end-opt.start
    v=tuple(float(x) for x in f)+(
        math.tanh(2.*f[1]), f[1]*f[2], f[1]*f[3],
        float(width==1),float(width>=4),
        math.sin(math.pi*opt.start/max(1,n)),
        math.cos(math.pi*opt.start/max(1,n)))
    if len(v)!=PROP_DIM or any(not math.isfinite(x) for x in v):
        raise ValueError('G1_0075_PROPOSAL_FEATURE_CONTRACT')
    return v


def _extend_lattice(base,logits,valid,prepared,budget=MAX_OPTIONS):
    """Label-blind generation of NEW type/span options from *raw frozen logits*.

    Existing options, their parameters and their previously encoded scores are
    retained exactly. All non-null types are considered for every valid
    token span, rather than restricting proposals to its top two types or to
    the earlier admission margin. The budget uses scores only, never gold.
    """
    if not 1<=budget<=MAX_OPTIONS or len(base.case.options)>budget:
        raise ValueError('G1_0075_CANDIDATE_BUDGET')
    if (logits.ndim!=3 or valid.shape!=logits.shape[:2] or
            logits.shape[-1]!=len(SPAN_KINDS)+1):
        raise ValueError('G1_0075_FROZEN_LOGIT_SHAPE')
    scores=logits.detach().float().cpu()
    mask=valid.detach().bool().cpu()
    if not torch.isfinite(scores[mask]).all():
        raise ValueError('G1_0075_NONFINITE_NEURAL_INPUT')
    n=int(scores.shape[0]); pool=[]; raw_count=0
    original={(o.start,o.end,o.kind) for o in base.case.options}
    if len(original)!=len(base.case.options):
        raise ValueError('G1_0075_DUPLICATE_PREVIOUS_OPTIONS')
    raw_span_keys=set();valid_spans=0
    for start,offset in mask.nonzero(as_tuple=False).tolist():
        end=start+offset+1
        if end>n:raise ValueError('G1_0075_BAD_FROZEN_SPAN')
        row=scores[start,offset]
        best_nonnull=float(row[1:].max().item())
        norm=torch.logsumexp(row[1:],dim=0)
        valid_spans+=1
        for kind in range(1,len(SPAN_KINDS)+1):
            # Every valid span/type coordinate can be counted by the oracle
            # independently of whether a resource budget later retains it.
            span={'token_first':start,'token_last':end-1,'kind':SPAN_KINDS[kind-1]}
            bounds=token_span_char_bounds(span,prepared)
            if bounds is None:continue
            key=(*bounds,SPAN_KINDS[kind-1]);raw_span_keys.add(key)
            if (start,end,kind) in original:continue
            margin=float(row[kind]-row[0]);gap=best_nonnull-float(row[kind]);
            if margin<MIN_LOGIT_MARGIN or gap>MAX_TYPE_GAP:continue
            cond=math.exp(float(row[kind]-norm))
            # Potential below positive default for uncertain extras; the
            # trained proposal or global selector can override it.
            base_score=-.35+.18*margin+.25*cond
            feat=learned._feature(margin=margin,p_cond=cond,width=end-start,n=n,
                first=start,kind=kind,original=False,base=base_score,
                top=(gap<1e-7))
            opt=learned.Option(start,end,kind,float(base_score),feat,key,False)
            # Deterministic label-independent priority, with tokenized tie break.
            priority=(margin-.20*gap,cond,-(end-start),-start,-end,-kind)
            pool.append((priority,opt))
            raw_count+=1
    # Reserve space for every old option. Never remove a baseline candidate.
    extras=sorted(pool,key=lambda p:p[0],reverse=True)
    remaining=budget-len(base.case.options)
    chosen=[opt for _,opt in extras[:remaining]]
    combined=sorted(base.case.options+chosen,key=lambda o:(o.end,o.start,o.kind))
    if len(combined)>MAX_OPTIONS:raise ValueError('G1_0075_OPTION_OVERFLOW')
    # New geometry can affect neighbor/fragment statistics. Retain the exact
    # previous feature vectors for every original candidate; only newly added
    # candidates receive the enlarged-lattice context.
    new_case=learned.Case(base.case.model,base.case.split,base.case.family,
        base.case.prompt_hash,n,combined,set(base.case.control),set(base.case.gold),
        list(base.case.control_prediction))
    preliminary=prior._encode(new_case)
    old_encoded={(o.start,o.end,o.kind):f for o,f in zip(
        base.case.options,base.features)}
    # The prior structured scores are injected by _prepare_scores below.
    features=[];is_new=[]
    for o,f in zip(combined,preliminary.features):
        key=(o.start,o.end,o.kind)
        if key in old_encoded:
            f=old_encoded[key]
        features.append(f);is_new.append(key not in old_encoded)
    extended=prior.Encoded(new_case,features)
    return Expanded(base,extended,
        tuple(_proposal_features(o,n) for o in combined),tuple(is_new),(),
        raw_count,len(pool)-len(chosen),valid_spans,frozenset(raw_span_keys))


def _safe_vector(weights,dim):
    if (not isinstance(weights,(tuple,list)) or len(weights)!=dim or
        any(not math.isfinite(float(v)) or abs(float(v))>MAX_WEIGHT+1e-9 for v in weights)):
        raise ValueError('G1_0075_INVALID_LEARNED_WEIGHTS')


def _prepare_scores(data,reference_weights):
    """Pin previous learned logits for every old option, without refitting."""
    prior._safe_weights(CONTROL,reference_weights)
    old={ (o.start,o.end,o.kind):score for o,score in zip(data.base.case.options,
                prior._scores(data.base,CONTROL,reference_weights))}
    extended=prior._scores(data.added,CONTROL,reference_weights)
    fixed=[]
    for o,s in zip(data.added.case.options,extended):
        key=(o.start,o.end,o.kind)
        fixed.append(float(old[key]) if key in old else float(s))
    data.base_scores=tuple(fixed)
    return data


def _inference_scores(data,arm,proposal_weights=None,set_weights=None):
    """Gold-blind scores for all inference strategies."""
    if arm not in ARMS:raise ValueError('G1_0075_UNKNOWN_ARM')
    if arm in (PROPOSAL,JOINT):_safe_vector(proposal_weights,PROP_DIM)
    if arm==JOINT:_safe_vector(set_weights,SET_DIM)
    if len(data.base_scores)!=len(data.is_new):
        raise ValueError('G1_0075_UNPREPARED_SCORES')
    output=[]
    for j,(o,score,is_new) in enumerate(zip(data.added.case.options,data.base_scores,data.is_new)):
        width=o.end-o.start
        if is_new and arm in (PROPOSAL,JOINT):
            score+=PROPOSAL_SCALE*width*sum(a*b for a,b in zip(proposal_weights,data.prop_features[j]))
        if arm==JOINT and o.kind in (prior.ACTOR,prior.ACTION):
            score+=SET_SCALE*width*sum(a*b for a,b in zip(set_weights,data.added.features[j]))
        if not math.isfinite(score):raise ValueError('G1_0075_SCORE_NOT_FINITE')
        output.append(score)
    return output


def _decode(data,arm,proposal_weights=None,set_weights=None,reference_weights=None):
    if arm==CONTROL:
        if reference_weights is None:raise ValueError('G1_0075_NO_PRIOR_CONTROL')
        return prior._predict(data.base,CONTROL,reference_weights)
    selected=prior._viterbi(data.added,_inference_scores(data,arm,proposal_weights,set_weights))
    return {data.added.case.options[j].char_key for j in selected
            if data.added.case.options[j].char_key is not None}


def _fit_proposals(train,steps=115):
    """Balanced local cross-entropy on *new* whole-span typed proposals."""
    positives=[];negatives=[]
    counts=Counter()
    for data in train:
        local_neg=[];local_pos=[]
        for j,(opt,is_new) in enumerate(zip(data.added.case.options,data.is_new)):
            if not is_new or opt.char_key is None:continue
            feat=data.prop_features[j]
            if opt.char_key in data.added.case.gold:
                local_pos.append(feat);counts['positive']+=1
            else:
                local_neg.append((data.base_scores[j],feat,opt.start,opt.end,opt.kind))
        positives.extend(local_pos)
        # Frozen-score hard-negative sampling; gold is used only to assign
        # positives/negatives in supervised fitting, not to form candidates.
        local_neg.sort(key=lambda entry:(-entry[0],entry[2],entry[3],entry[4]))
        negatives.extend(x[1] for x in local_neg[:max(25,5*len(local_pos))])
    if not positives or not negatives:
        # A saturated lattice or lack of reachable training labels should be
        # reported as a negative result, not crash away the oracle ceiling.
        return (0.,)*PROP_DIM,{'positive':len(positives),'hard_negative':len(negatives),
            'steps':0,'trainability':'UNTRAINABLE_NO_POSITIVE_OR_NEGATIVE_NEW_PROPOSALS'}
    Xp=torch.tensor(positives,dtype=torch.float64)
    Xn=torch.tensor(negatives,dtype=torch.float64)
    w=torch.zeros(PROP_DIM,dtype=torch.float64,requires_grad=True)
    optimizer=torch.optim.Adam([w],lr=.035)
    for _ in range(steps):
        optimizer.zero_grad()
        loss=(.5*F.softplus(-Xp@w).mean()+.5*F.softplus(Xn@w).mean()+.035*w.square().sum())
        loss.backward();optimizer.step()
        with torch.no_grad():w.clamp_(-MAX_WEIGHT,MAX_WEIGHT)
    counts['hard_negative']=len(negatives);counts['steps']=steps
    return tuple(float(x) for x in w.detach()),dict(counts)


def _sum_prop(data,indices):
    values=[0.]*PROP_DIM
    for i in indices:
        if data.is_new[i]:
            width=data.added.case.options[i].end-data.added.case.options[i].start
            for j,v in enumerate(data.prop_features[i]):values[j]+=width*v
    return values


def _sum_set(data,indices):
    values=[0.]*SET_DIM
    for i in indices:
        opt=data.added.case.options[i]
        if opt.kind in (prior.ACTOR,prior.ACTION):
            width=opt.end-opt.start
            for j,v in enumerate(data.added.features[i]):values[j]+=width*v
    return values


def _fit_joint(train,epochs=18):
    """Joint residual proposal + global structured-margin updates.

    Both parameter groups receive whole-assignment loss-augmented subgradients
    on each update (not a second pointwise fit). Gold oracle is used ONLY for
    the training target path. Baseline G1-0074 weights remain frozen.
    """
    prop=[0.]*PROP_DIM;setres=[0.]*SET_DIM
    targets=[];missing=0
    for data in train:
        oracle=prior._viterbi(data.added,[0.]*len(data.added.case.options),
                               oracle_gold=data.added.case.gold)
        reconstructed={data.added.case.options[j].char_key for j in oracle}
        missing+=len(data.added.case.gold-reconstructed)
        targets.append(oracle)
    violations=0
    for epoch in range(epochs):
        rate=.045/(1.+epoch/9.)
        for data,oracle in zip(train,targets):
            hard=prior._viterbi(data.added,
                _inference_scores(data,JOINT,tuple(prop),tuple(setres)),
                loss_gold=data.added.case.gold,delta=.75)
            if set(hard)==set(oracle):
                prop=[x*.9995 for x in prop];setres=[x*.9995 for x in setres]
                continue
            goodp,badp=_sum_prop(data,oracle),_sum_prop(data,hard)
            goods,bads=_sum_set(data,oracle),_sum_set(data,hard)
            denom=max(1.,math.sqrt(len(hard)+len(oracle)))
            prop=[max(-MAX_WEIGHT,min(MAX_WEIGHT,v*.9995-rate*(b-g)/denom))
                  for v,b,g in zip(prop,badp,goodp)]
            setres=[max(-MAX_WEIGHT,min(MAX_WEIGHT,v*.9995-rate*.65*(b-g)/denom))
                    for v,b,g in zip(setres,bads,goods)]
            violations+=1
    return (tuple(prop),tuple(setres)),{'epochs':epochs,'structured_violations':violations,
        'unrecoverable_train_gold_after_expansion':missing,
        'joint_proposal_and_set_updates':True}


def _fit(train):
    p,pdiag=_fit_proposals(train)
    joint,jdiag=_fit_joint(train)
    return {CONTROL:(None,None),FROZEN:(None,None),
            PROPOSAL:(p,None),JOINT:joint},{PROPOSAL:pdiag,JOINT:jdiag}


def _metrics(cases,parameters,reference_weights):
    records={arm:[(_decode(c,arm,*parameters[arm],reference_weights=reference_weights),
                    c.added.case.gold) for c in cases] for arm in ARMS}
    return {arm:_summarize(r) for arm,r in records.items()},records


def _choose(metrics):
    control=metrics[CONTROL]
    viable=[CONTROL]
    for name in ARMS[1:]:
        candidate=metrics[name]
        if (candidate['exact_prompts']>=control['exact_prompts'] and
            candidate['f1']>=control['f1']-1.e-12 and
            candidate['precision']>=control['precision']-.005 and
            all(candidate['by_kind'][kind]['recall']+1e-12>=control['by_kind'][kind]['recall']
                for kind in REQUIRED_KINDS) and
            (candidate['exact_prompts']>control['exact_prompts'] or
             candidate['f1']>control['f1']+1.e-9)):
            viable.append(name)
    return max(viable,key=lambda arm:(metrics[arm]['exact_prompts'],
        metrics[arm]['f1'],metrics[arm]['precision'],-ARMS.index(arm)))


def _oracle_coverage(cases):
    """Separate *oracle* ceilings: old, budgeted expanded, raw valid-logit.

    Gold labels are deliberately consulted here and never in inference.
    Raw valid-logit coverage is a coordinate-wise ceiling; nonoverlap may
    still make those spans impossible to emit together.
    """
    old=[];expanded=[];raw=[];distribution=Counter()
    for data in cases:
        gold=data.added.case.gold
        for k in gold:
            old_reachable=any(o.char_key==k for o in data.base.case.options)
            new_reachable=any(o.char_key==k for o in data.added.case.options)
            raw_reachable=k in data.raw_span_gold_types
            distribution[(k[-1],'old')] += int(old_reachable)
            distribution[(k[-1],'expanded')] += int(new_reachable)
            distribution[(k[-1],'raw')] += int(raw_reachable)
        before=prior._viterbi(data.base,[0.]*len(data.base.case.options),oracle_gold=gold)
        after=prior._viterbi(data.added,[0.]*len(data.added.case.options),oracle_gold=gold)
        old.append(({data.base.case.options[i].char_key for i in before},gold))
        expanded.append(({data.added.case.options[i].char_key for i in after},gold))
        raw.append((set(gold)&set(data.raw_span_gold_types),gold))
    return {'previous_reachable_nonoverlap':_summarize(old),
            'expanded_reachable_nonoverlap':_summarize(expanded),
            'all_valid_logit_coordinates_independent_upper_bound':_summarize(raw),
            'per_kind_coordinate_counts':{kind:{label:distribution[(kind,label)]
                for label in ('old','expanded','raw')} for kind in REQUIRED_KINDS},
            'budget_diagnostics':{'mean_options':sum(len(c.added.case.options) for c in cases)/max(1,len(cases)),
                'total_new_options':sum(sum(c.is_new) for c in cases),
                'total_discarded_by_budget':sum(c.discarded_by_budget for c in cases),
                'total_valid_token_spans':sum(c.valid_token_spans for c in cases)},
            'label_assisted_oracle_not_inference':True}


def _details(cases,parameters,reference_weights,selected):
    results=[]
    for c in cases:
        gold=c.added.case.gold
        before=_decode(c,CONTROL,reference_weights=reference_weights)
        proposed={arm:_decode(c,arm,*parameters[arm],reference_weights=reference_weights)
                  for arm in ARMS[1:]}
        row={'split':c.added.case.split,'model':c.added.case.model,
             'family':c.added.case.family,'prompt_sha256':c.added.case.prompt_hash,
             'gold_count':len(gold),'control_exact':before==gold,
             'selected':selected,'selected_exact':(before if selected==CONTROL
                                                   else proposed[selected])==gold,
             'candidate_counts':{'previous':len(c.base.case.options),
                 'new':sum(c.is_new),'budget_discarded':c.discarded_by_budget},
             'arm_changes':{}}
        for arm,after in proposed.items():
            row['arm_changes'][arm]={
                'recovered_correct':[list(v) for v in sorted((after&gold)-(before&gold))],
                'lost_correct':[list(v) for v in sorted((before&gold)-(after&gold))],
                'introduced_false':[list(v) for v in sorted((after-gold)-(before-gold))],
                'removed_false':[list(v) for v in sorted((before-gold)-(after-gold))],
                'exact':after==gold}
        results.append(row)
    return results


def _require_previous(path,entries,dataset_hash,partitions,challenge_length):
    file=path/'report.json'
    report=json.loads(file.read_text(encoding='utf8'))
    expected={k:e['manifest']['head_sha256'] for k,e in entries.items()}
    if (report.get('schema')!=prior.SCHEMA or
        report.get('status')!='EXPLORATORY_STRUCTURAL_LEARNING_NOT_CERTIFIED' or
        report.get('lineage',{}).get('dataset_sha256')!=dataset_hash or
        report.get('lineage',{}).get('checkpoint_sha256')!=expected or
        report.get('selected')!={'cutover':CONTROL,'legacy':CONTROL} or
        report.get('design',{}).get('cohorts')!={k:len(v) for k,v in partitions.items()} or
        report.get('design',{}).get('historical_challenge_cases')!=challenge_length or
        any(report.get(k) is not False for k in ('g1_head_weights_modified',
            'production_promotion','g2_unlocked','sealed_test_used','certification_authorized'))):
        raise ValueError('G1_0075_REFERENCE_LINEAGE_OR_SELECTION_DRIFT')
    weights={}
    for name in entries:
        matrix=report.get('interface_weights',{}).get(name,{}).get(CONTROL)
        if matrix is None:raise ValueError('G1_0075_MISSING_STRUCTURED_WEIGHTS')
        prior._safe_weights(CONTROL,matrix)
        weights[name]=tuple(tuple(float(v) for v in row) for row in matrix)
    return report,sha256(file),weights


def _check_phase_parity(reference,phase,model,observed):
    expected=reference['phases'][phase][model]['selected_metrics']
    for key in ('cases','correct','expected','predicted','exact_prompts','false_positive_objects'):
        if observed[CONTROL][key]!=expected[key]:
            raise ValueError('G1_0075_G1_0074_CONTROL_DRIFT:'+phase+':'+model+':'+key)
    for kind in REQUIRED_KINDS:
        if observed[CONTROL]['by_kind'][kind]!=expected['by_kind'][kind]:
            raise ValueError('G1_0075_G1_0074_KIND_DRIFT:'+phase+':'+model+':'+kind)


def _require_lattice_config(learned_reference,structural_report):
    """Recover exact *model-specific* G1-0071 lattice source pinned by 0074.

    Critically, G1-0074's structured decoder control is the same for both
    models, but its candidate source used DIFFERENT G1-0071 priors for cutover
    and legacy. Substituting the G1-0072 name for both changes the lattice.
    """
    ref=Path(learned_reference).resolve()/'report.json'
    digest=sha256(ref)
    if digest!=structural_report.get('lineage',{}).get('learned_reference_sha256'):
        raise ValueError('G1_0075_G1_0073_REFERENCE_HASH_DRIFT')
    report=json.loads(ref.read_text(encoding='utf8'))
    source=report.get('design',{}).get('per_model_0072_control')
    if (report.get('schema')!=learned.SCHEMA or
        source is None or set(source)!= {'legacy','cutover'} or
        any(not isinstance(arm,str) or not arm for arm in source.values())):
        raise ValueError('G1_0075_G1_0073_LATTICE_SOURCE_DRIFT')
    return source,digest


def _check_oracle_parity(expected,observed,context):
    """Pin the old candidate inventory, not just its decoded control score."""
    for key in ('cases','correct','expected','exact_prompts'):
        if expected[key]!=observed[key]:
            raise ValueError('G1_0075_G1_0074_CANDIDATE_CEILING_DRIFT:'+context+':'+key)


def _capture(rows,heads,model,tok,*,device,max_length,prior_selected,reference_weights,
             split,budget):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
    output={name:[] for name in heads}
    for i,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            results={name:head(hidden.to(device=device,dtype=torch.float32),
                eligible=prepared['eligible'].to(device),active_stage='G1')
                for name,head in heads.items()}
        frozen={}
        for name,result in results.items():
            logits,valid=result['span_logits'],result['span_valid']
            options,ctl,n=learned._lattice(logits,valid,prepared,prior_selected[name])
            originals,_=complete._decode(logits,valid,prior_selected[name])
            if ctl!=originals['action_rescue_2p00_cond0p50']:
                raise ValueError('G1_0075_G1_0072_CONTROL_DRIFT')
            frozen[name]=(options,ctl,n,logits,valid)
        # Build *every* proposal for *both* models before materializing labels.
        for name,(options,ctl,n,logits,valid) in frozen.items():
            case=learned.Case(name,split,row['family'],hashlib.sha256(
                row['prompt'].encode('utf8')).hexdigest(),n,options,
                _char_set(ctl,prepared),set(),ctl)
            base=prior._encode(case)
            expanded=_extend_lattice(base,logits,valid,prepared,budget)
            _prepare_scores(expanded,reference_weights[name])
            output[name].append(expanded)
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        for name in heads:
            output[name][-1].base.case.gold=set(gold)
            output[name][-1].added.case.gold=set(gold)
        if i==1 or i%10==0 or i==len(rows):
            print(f'G1_COVERAGE_{split.upper()} {i}/{len(rows)} legacy+cutover frozen',flush=True)
    return output


def run(*,dataset,legacy,cutover,structural_reference,learned_reference,output,device='cuda',
        max_length=2048,independent_dataset=None,proposal_budget=MAX_OPTIONS):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dest=Path(output).resolve();ref=Path(structural_reference).resolve()
    if dest.exists():raise FileExistsError('G1_0075_IMMUTABLE_OUTPUT')
    if dest==ref or ref in dest.parents:raise ValueError('G1_0075_OUTPUT_INSIDE_REFERENCE')
    if not 1<=proposal_budget<=MAX_OPTIONS or max_length<1:
        raise ValueError('G1_0075_INVALID_CONFIG')
    entries,ds_hash,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    partitions=learned._partition(cohorts['dev'])
    old,ref_hash,weights=_require_previous(ref,entries,ds_hash,partitions,len(cohorts['challenge']))
    prior_selected,learned_hash=_require_lattice_config(learned_reference,old)
    extra,external_hash=prior._load_external(independent_dataset,dataset,cohorts)
    heads={name:cur._load_checkpoint(e['path'],device=device)[0].eval() for name,e in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    # Retain distinct G1-0071 candidate sources as recorded by G1-0073:
    # cutover runner-up joint prior; legacy actor prior. Never replace them
    # with the common 0072 decoder name used for G1-0074 scoring.
    phase_rows={**partitions,'challenge':cohorts['challenge']}
    if extra is not None:phase_rows['external']=extra
    captured={}
    for phase,rows in phase_rows.items():
        captured[phase]=_capture(rows,heads,model,tok,device=device,max_length=max_length,
            prior_selected=prior_selected,reference_weights=weights,split=phase,
            budget=proposal_budget)
        if phase in old['phases']:
            for name,batch in captured[phase].items():
                control=_summarize([(_decode(c,CONTROL,reference_weights=weights[name]),
                                     c.added.case.gold) for c in batch])
                _check_phase_parity(old,phase,name,{CONTROL:control})
                previous_ceiling=_oracle_coverage(batch)['previous_reachable_nonoverlap']
                _check_oracle_parity(
                    old['phases'][phase][name]['oracle_candidate_ceiling_diagnostic_not_inference'],
                    previous_ceiling,phase+':'+name)
    trained={};ledger={}
    for name in entries:
        trained[name],ledger[name]=_fit(captured['train'][name])
    selection={name:_metrics(captured['selection'][name],trained[name],weights[name])[0]
               for name in entries}
    selected={name:_choose(selection[name]) for name in entries}
    print('G1_COVERAGE_SELECTION '+json.dumps(selected,sort_keys=True),flush=True)
    phases={};case_rows=[]
    for phase in phase_rows:
        phases[phase]={}
        for name in entries:
            batch=captured[phase][name]
            metrics,records=_metrics(batch,trained[name],weights[name])
            phases[phase][name]={'arms':metrics,'selected':selected[name],
                'selected_metrics':metrics[selected[name]],'control_metrics':metrics[CONTROL],
                'changes_vs_control':{arm:admission._changes(records[CONTROL],records[arm])
                    for arm in ARMS[1:]},
                'oracle_candidate_coverage_not_inference':_oracle_coverage(batch)}
            case_rows.extend(_details(batch,trained[name],weights[name],selected[name]))
    retrospective={}
    for name in entries:
        batch=sum((captured[ph][name] for ph in ('train','selection','audit')),[])
        metrics,records=_metrics(batch,trained[name],weights[name])
        retrospective[name]={'arms':metrics,'selected':selected[name],
            'selected_metrics':metrics[selected[name]],'control_metrics':metrics[CONTROL],
            'changes_vs_control':{arm:admission._changes(records[CONTROL],records[arm])
                for arm in ARMS[1:]},
            'oracle_candidate_coverage_not_inference':_oracle_coverage(batch),
            'in_sample_includes_training_and_selection':True}
        previous=old['full_dev_retrospective_not_holdout'][name]['selected_metrics']
        _check_phase_parity({'phases':{'full_dev':{name:{'selected_metrics':previous}}}},
            'full_dev',name,{CONTROL:metrics[CONTROL]})
        _check_oracle_parity(
            old['full_dev_retrospective_not_holdout'][name][
                'oracle_candidate_ceiling_diagnostic_not_inference'],
            retrospective[name]['oracle_candidate_coverage_not_inference'][
                'previous_reachable_nonoverlap'],'full_dev:'+name)
    # All mutable experiment inputs are reverified before writing a new output.
    if (sha256(dataset)!=ds_hash or sha256(ref/'report.json')!=ref_hash or
        sha256(Path(learned_reference).resolve()/'report.json')!=learned_hash):
        raise ValueError('G1_0075_INPUT_DRIFT')
    if extra is not None and sha256(independent_dataset)!=external_hash:
        raise ValueError('G1_0075_EXTERNAL_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_0075_ADAPTER_DRIFT')
    for name,e in entries.items():
        if sha256(Path(e['path'])/cur.HEAD_FILE)!=e['manifest']['head_sha256']:
            raise ValueError('G1_0075_G1_CHECKPOINT_DRIFT')
    report={'schema':SCHEMA,'status':'EXPLORATORY_CANDIDATE_COVERAGE_NOT_CERTIFIED',
        'design':{'arms':ARMS,'cohorts':{p:len(r) for p,r in partitions.items()},
            'historical_challenge_cases':len(cohorts['challenge']),
            'external_cases':len(extra or []),'proposal_budget':proposal_budget,
            'raw_logit_margin_floor':MIN_LOGIT_MARGIN,'raw_nonnull_type_gap_ceiling':MAX_TYPE_GAP,
            'selection':'DEV_SELECTION_ONLY','control':'exact frozen G1-0074 structured_set_maxmargin',
            'competitors':{
                FROZEN:'frozen neural logit top-four candidate expansion; fixed prior structured-set scoring',
                PROPOSAL:'supervised whole-span proposal admission with frozen G1-0074 set scoring',
                JOINT:'joint loss-augmented proposal and structured-set residual updates on expanded candidate sets'},
            'control_reproduced_phase_by_phase':True,
            'no_new_independent_dataset_supplied':extra is None,
            'external_provenance_independence_not_verified':True,
            'audit_not_used_for_training_or_selection':True,
            'historical_challenge_previously_inspected':True,
            'oracle_gold_not_available_to_inference':True,
            'candidate_ranking_never_uses_gold':True,
            'full_development_retrospective_not_holdout':True},
        'lineage':{'dataset_sha256':ds_hash,'structural_reference_sha256':ref_hash,
            'learned_reference_sha256':learned_hash,'per_model_0071_lattice_prior':prior_selected,
            'source_adapter_sha256':manifest['source_adapter_sha256'],
            'checkpoint_sha256':{name:e['manifest']['head_sha256'] for name,e in entries.items()},
            'external_dataset_sha256':external_hash},
        'previous_0074_selected':old['selected'],
        'frozen_0074_weights':{name:[list(row) for row in matrix] for name,matrix in weights.items()},
        'interface_weights':{name:{arm:{'proposal':list(pair[0]) if pair[0] is not None else None,
                        'structured':list(pair[1]) if pair[1] is not None else None}
                for arm,pair in arms.items()} for name,arms in trained.items()},
        'training_ledger':ledger,'selected':selected,'phases':phases,
        'full_dev_retrospective_not_holdout':retrospective,
        'g1_head_weights_modified':False,'interface_training_performed':True,
        'production_promotion':False,'g2_unlocked':False,'sealed_test_used':False,
        'certification_authorized':False}
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf8')
    with (dest/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in case_rows:f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_COVERAGE_RESULT '+json.dumps({name:{
        'selected':selected[name],
        'selection_exact':phases['selection'][name]['selected_metrics']['exact_prompts'],
        'audit_exact':phases['audit'][name]['selected_metrics']['exact_prompts'],
        'audit_control_exact':phases['audit'][name]['control_metrics']['exact_prompts'],
        'historical_challenge_exact':phases['challenge'][name]['selected_metrics']['exact_prompts'],
        'full_dev_exact_retrospective':retrospective[name]['selected_metrics']['exact_prompts'],
        'full_dev_control_exact':retrospective[name]['control_metrics']['exact_prompts'],
        'oracle_ceiling_expanded':retrospective[name]['oracle_candidate_coverage_not_inference'][
            'expanded_reachable_nonoverlap']['exact_prompts']} for name in entries},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','legacy','cutover','structural-reference','learned-reference','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    parser.add_argument('--proposal-budget',type=int,default=MAX_OPTIONS)
    parser.add_argument('--independent-dataset')
    run(**vars(parser.parse_args(argv)))


if __name__=='__main__':main()
