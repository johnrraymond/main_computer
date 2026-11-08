#!/usr/bin/env python3
"""Frozen-LoRA exact-rational belief decoding and end-to-end recomposition audit.

Retains all four previously selected payoff heads byte-for-byte. Fits only
new, *diagnostic* belief readouts on the 600-case deconfounded deck, with
TRAIN fitting / DEV selection / heldout and newly generated fraction challenge
scoring. The challenge uses unseen prime denominators and cancellation anchors.

A parsed-fraction oracle is always shown separately. No parsed q is fed into
neural q predictions, and no oracle operand is fed into neural recomposition.
Never changes LoRA, C0, previous q/zero detectors, or the four payoff heads.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign
from tools import nanojev_gamer_v1_zero_sign_frob as prompts
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA = 'nanojev.gamer.v1.frozen-exact-rational-belief.v1'
STEP = 3000
VIEWS = ('belief', 'operand_joint', 'raster_mean100', 'lexical_numeric')
REGS = (0.1, 1.0)
KERNELS = ('linear', 'rbf')
MODES = ('fraction_class', 'numerator_denominator_class', 'digitwise_fraction',
         'rationalized_regression')
DIGITS = 6
MAX_DENOMINATOR = 100000
PANELS = ('independent', 'zero_anchor')


def _source(folder: Path):
    """Validate all frozen source hashes before any additional model capture."""
    root = Path(folder).resolve()
    report_path = root / 'final_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema') != payoff.SCHEMA or
        report.get('status') != 'COMPLETE_FROZEN_PAYOFF_PRECISION_DIAGNOSTIC' or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        report.get('no_lora_training') is not True or
        report.get('no_c0_training') is not True or
        report.get('no_zero_detector_mutation') is not True or
        report.get('frozen_lora_step') != STEP):
        raise ValueError('GAMER_BELIEF_REQUIRES_COMPLETE_PAYOFF_PRECISION_REPORT')
    previous = payoff._source(Path(report['deconfounded_report_path']).parent)
    if (token._sha(previous['report_path']) != report['deconfounded_report_sha256'] or
        previous['source']['snapshot_sha'] != report['frozen_lora_sha256'] or
        previous['report']['deck_sha256'] != report['deck_sha256'] or
        previous['report']['frozen_zero_head_sha256'] != report['frozen_zero_head_sha256']):
        raise ValueError('GAMER_BELIEF_UPSTREAM_LINEAGE_CHANGED')
    archive = Path(report['fresh_feature_archive']['path']).resolve()
    if (not archive.is_file() or token._sha(archive) !=
        report['fresh_feature_archive']['sha256'] or
        report['fresh_feature_archive']['dtype'] != 'float32'):
        raise ValueError('GAMER_BELIEF_FROZEN_FLOAT32_FEATURES_CHANGED')
    for name in payoff.PAYOFFS:
        ckpt = Path(report['diagnostic_checkpoints'][name]['path']).resolve()
        if (not ckpt.is_file() or
            token._sha(ckpt) != report['diagnostic_checkpoints'][name]['sha256'] or
            not report['diagnostic_checkpoints'][name]['replay_matches_selected_predictions'] or
            not ckpt.is_relative_to(root)):
            raise ValueError('GAMER_BELIEF_FROZEN_PAYOFF_HEAD_CHANGED:' + name)
    return {'root': root, 'report': report, 'report_path': report_path,
            'attested_report_sha256':token._sha(report_path),
            'previous': previous, 'archive': archive}


def _verify_frozen(source):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    parent=source['previous']['source']
    report=source['report']
    if (token._sha(source['report_path']) != source['attested_report_sha256'] or
        token._sha(source['archive']) != report['fresh_feature_archive']['sha256'] or
        stress._verify_snapshot(parent['snapshot'],STEP) != report['frozen_lora_sha256'] or
        token._sha(parent['zero_head']) != report['frozen_zero_head_sha256']):
        raise ValueError('GAMER_BELIEF_PROTECTED_SOURCE_MUTATED')
    for name in payoff.PAYOFFS:
        entry=report['diagnostic_checkpoints'][name]
        if token._sha(Path(entry['path'])) != entry['sha256']:
            raise ValueError('GAMER_BELIEF_PAYOFF_CHECKPOINT_MUTATED:' + name)


def _exact_q(deck):
    """Ground numerical labels in exact prompt text, never float ground truth."""
    result = []
    for case in deck:
        spans = token._spans(case.prompt)['belief']
        if len(spans) != 1:
            raise ValueError('GAMER_BELIEF_AMBIGUOUS_PROBABILITY_SPAN')
        f = Fraction(case.prompt[slice(*spans[0])])
        if not (Fraction(0) < f < Fraction(1)):
            raise ValueError('GAMER_BELIEF_OUTSIDE_OPEN_UNIT_INTERVAL')
        r,s,t,p = [Fraction(case.prompt[slice(*span)])
                   for span in token._spans(case.prompt)['payoffs']]
        if f*(t-r)+(1-f)*(p-s) != case.target:
            raise ValueError('GAMER_BELIEF_PROMPT_ARITHMETIC_MISMATCH')
        result.append(f)
    return result


def _challenge():
    """Fresh q-only interventions and unseen-denominator exact-zero anchors.

    72 independent q edits on six heldout payoff tuples + 24 zero-anchor
    cases. No challenge cases are used for fitting or candidate selection.
    """
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    deck, tags = [], []
    renderers = (('spine_belief', prompts._spine_belief_prompt),
                 ('game_prose_belief', prompts._game_prose_belief_prompt))
    primes = (19, 23, 29, 31, 37, 41)
    for group, den in enumerate(primes):
        stage = spine.Stage(Fraction(114 + (group*3)%12),
            Fraction(120 + (group*2)%13), Fraction(128 - (group*2)%12),
            Fraction(116 + (group*4)%15))
        for k in range(3):
            n = max(1, min(den-2, int(round(den*(.24 + .17*k)))))
            for edit, numerator in (('base', n), ('q_edit', n+1)):
                q = Fraction(numerator, den)
                for surface, render in renderers:
                    ident = f'fraction_challenge.core.{group:02d}.{k}.{edit}.{surface}'
                    value = stage.margin(q)
                    deck.append(oracle.Case(ident,'test',f'freshcore.{group}.{k}',
                        surface,render(stage,q),value,'fresh_fraction_core',value))
                    tags.append({'panel':'independent','group':f'freshcore.{group}.{k}',
                        'surface':surface,'edit':edit,'changed':'q',
                        'denominator':den,'id':ident})
    # Exactly 4 independent cancellation profiles, with unseen prime
    # center denominators; each has three signs x two prompt surfaces.
    for group,(left,right) in enumerate(((7,12),(8,15),(13,16),(15,16))):
        stage=spine.Stage(Fraction(115+group),Fraction(130+group),
                           Fraction(115+group+left),Fraction(130+group-right))
        critical=spine.critical_q(stage)
        assert critical is not None and critical.denominator in (19,23,29,31)
        radius=Fraction(1,300+60*group)
        for sign,offset in (('negative',-1),('zero',0),('positive',1)):
            q=critical+offset*radius
            for surface,render in renderers:
                ident=f'fraction_challenge.zero.{group:02d}.{sign}.{surface}'
                value=stage.margin(q)
                if (value>0)-(value<0)!=offset:
                    raise ValueError('GAMER_BELIEF_CHALLENGE_BAD_ZERO_SIGN')
                deck.append(oracle.Case(ident,'test',f'freshzero.{group}',surface,
                   render(stage,q),value,'fresh_fraction_zero_anchor',value))
                tags.append({'panel':'zero_anchor','group':f'freshzero.{group}',
                    'surface':surface,'edit':sign,'changed':'q',
                    'denominator':critical.denominator,'id':ident})
    if len(deck)!=96 or len({c.id for c in deck})!=96:
        raise ValueError('GAMER_BELIEF_CHALLENGE_INVALID_DECK')
    return deck,tags


def _output_columns(qs, tr):
    """Train-only multi-output targets for exact fraction and digit decoding."""
    qtrain=[qs[int(i)] for i in tr]
    classes=sorted(set(qtrain))
    nums=sorted({q.numerator for q in qtrain})
    dens=sorted({q.denominator for q in qtrain})
    table=[]; specs={}
    for task, values, vocabulary in (
        ('fraction',qtrain,classes),('numerator',[q.numerator for q in qtrain],nums),
        ('denominator',[q.denominator for q in qtrain],dens)):
        a=len(table)
        for value in vocabulary:
            table.append(np.asarray([x==value for x in values],dtype=float))
        specs[task]={'start':a,'stop':len(table),
                     'classes':[str(x) for x in vocabulary]}
    for part in ('numerator','denominator'):
        for pos in range(DIGITS):
            a=len(table)
            for digit in range(10):
                table.append(np.asarray([
                    (int(getattr(q,part))//10**(DIGITS-1-pos))%10==digit
                    for q in qtrain],dtype=float))
            specs[f'{part}_digit_{pos}']={'start':a,'stop':len(table),
                                          'classes':[str(i) for i in range(10)]}
    # Regression target is separated from categorical readouts, and can only
    # be rationalized against a *fixed* public denominator ceiling.
    specs['q_regression']={'start':len(table),'stop':len(table)+1,'classes':[]}
    table.append(np.asarray([float(q) for q in qtrain],dtype=float))
    return np.stack(table,axis=1),specs


def _decoded(scores, specs, mode):
    def class_one(task):
        spec=specs[task]; a,b=spec['start'],spec['stop']
        classes=spec['classes']
        return [classes[int(j)] for j in np.argmax(scores[:,a:b],axis=1)]
    if mode=='fraction_class':
        return [Fraction(x) for x in class_one('fraction')]
    if mode=='numerator_denominator_class':
        numerators=[int(x) for x in class_one('numerator')]
        denominators=[int(x) for x in class_one('denominator')]
    elif mode=='digitwise_fraction':
        numerators=np.zeros(len(scores),dtype=int)
        denominators=np.zeros(len(scores),dtype=int)
        for name,array in (('numerator',numerators),('denominator',denominators)):
            for position in range(DIGITS):
                digs=class_one(f'{name}_digit_{position}')
                array += np.asarray([int(v) for v in digs]) * 10**(DIGITS-1-position)
    elif mode=='rationalized_regression':
        cfg=specs['q_regression'];vals=scores[:,cfg['start']]
        return [Fraction(float(v)).limit_denominator(MAX_DENOMINATOR)
                if np.isfinite(v) and 0<float(v)<1 else None for v in vals]
    else:
        raise ValueError('GAMER_BELIEF_UNKNOWN_DECODER_MODE')
    return [Fraction(int(a),int(b)) if b>0 and 0<a<b else None
            for a,b in zip(numerators,denominators)]


def _metric(truth,pred,indices,support):
    idx=[int(i) for i in indices]; support=set(support)
    ok=[pred[i] == truth[i] for i in idx]
    valid=[pred[i] is not None for i in idx]
    seen=[truth[i] in support for i in idx]
    n_err=[pred[i] is not None and pred[i].numerator==truth[i].numerator for i in idx]
    d_err=[pred[i] is not None and pred[i].denominator==truth[i].denominator for i in idx]
    mae=[abs(float(pred[i]-truth[i])) if pred[i] is not None else 1.0 for i in idx]
    return {'cases':len(idx),'exact':float(np.mean(ok)),
            'exact_cases':int(sum(ok)), 'numerator_exact':float(np.mean(n_err)),
            'denominator_exact':float(np.mean(d_err)),
            'valid_fraction_fraction':float(np.mean(valid)),
            'belief_mae_with_invalid_penalty':float(np.mean(mae)),
            'seen_fraction':float(np.mean(seen)),
            'unseen_exact':float(np.mean([v for v,known in zip(ok,seen) if not known]))
                if not all(seen) else None,
            'unseen_cases':int(len(idx)-sum(seen))}


def _rank(dev_independent,dev_zero):
    # Prefer complete zero-anchor rational exactness over a high ordinary mean.
    return (min(dev_independent['exact'],dev_zero['exact']),
            dev_zero['exact'],dev_independent['exact'],
            -.25*dev_independent['belief_mae_with_invalid_penalty']
            -.75*dev_zero['belief_mae_with_invalid_penalty'])


def _candidates(frame,qs,train,regs=REGS,kernels=KERNELS):
    from tools import nanojev_gamer_v1_operand_sufficiency_audit as oa
    y,specs=_output_columns(qs,train)
    bias=np.mean(y,axis=0);residual=y-bias
    for kind in kernels:
        cross,gram,meta=oa._encode_train_map(frame,train,kind)
        sym=(gram.astype(np.float64)+gram.T.astype(np.float64))*.5
        eigval,eigvec=np.linalg.eigh(sym)
        proj=eigvec.T@residual
        for reg in regs:
            alpha=eigvec@(proj/(np.maximum(eigval,0.)[:,None]+reg))
            scores=cross.astype(np.float64)@alpha+bias
            if not np.isfinite(scores).all():
                raise ValueError('GAMER_BELIEF_NONFINITE_PROBE')
            for mode in MODES:
                yield {'kind':kind,'reg':reg,'mode':mode,
                       'prediction':_decoded(scores,specs,mode),
                       'alpha':alpha,'bias':bias,'specs':specs,
                       'train':np.asarray(train,dtype=int)}


def _select(views,qs,ix,*,views_to_try=VIEWS,regs=REGS,kernels=KERNELS,
            train_panels=('train','train_independent')):
    best=None;lexical=None;candidate_count=0
    for view in views_to_try:
        if view not in views:
            raise ValueError('GAMER_BELIEF_MISSING_FEATURE_VIEW:' + view)
        for panel in train_panels:
            tr=ix[panel]; support=[qs[int(i)] for i in tr]
            for item in _candidates(views[view],qs,tr,regs,kernels):
                candidate_count+=1
                core=_metric(qs,item['prediction'],ix['dev_independent'],support)
                zero=_metric(qs,item['prediction'],ix['dev_zero_anchor'],support)
                scored={**item,'view':view,'panel':panel,
                        'dev_independent':core,'dev_zero_anchor':zero,
                        'rank':_rank(core,zero)}
                if view=='lexical_numeric':
                    if lexical is None or scored['rank']>lexical['rank']:lexical=scored
                else:
                    if best is None or scored['rank']>best['rank']:best=scored
    if best is None or lexical is None:
        raise ValueError('GAMER_BELIEF_NO_DEVELOPMENT_SELECTED_READOUT')
    return best,lexical,candidate_count


def _save(path,selected,frame,source_hash,deck_hash):
    train=selected['train']
    reference=np.ascontiguousarray(frame[train],dtype=np.float32)
    meta={'schema':SCHEMA,'source_report_sha256':source_hash,
          'deck_sha256':deck_hash,'view':selected['view'],
          'mode':selected['mode'],'kernel':selected['kind'],
          'regularization':selected['reg'],'train_panel':selected['panel'],
          'train_feature_sha256':hashlib.sha256(reference.tobytes()).hexdigest(),
          'specs':selected['specs']}
    np.savez_compressed(path, training_feature=reference,train_indices=train,
                        alpha=selected['alpha'],bias=selected['bias'],
                        metadata=np.asarray(json.dumps(meta,sort_keys=True)))
    replay, _=_replay(path,frame,source_hash,deck_hash)
    if replay!=selected['prediction']:
        raise ValueError('GAMER_BELIEF_DIAGNOSTIC_REPLAY_MISMATCH')
    return {'path':str(path),'sha256':token._sha(path),
            'replay_matches_selected_predictions':True}


def _replay(path,frame,source_hash,deck_hash):
    from tools import nanojev_gamer_v1_operand_sufficiency_audit as oa
    with np.load(path,allow_pickle=False) as f:
        meta=json.loads(str(f['metadata'].item()))
        reference=np.asarray(f['training_feature'],dtype=np.float32)
        tr=np.asarray(f['train_indices'],dtype=int)
        alpha=np.asarray(f['alpha'],dtype=np.float64)
        bias=np.asarray(f['bias'],dtype=np.float64)
    if (meta['schema']!=SCHEMA or meta['source_report_sha256']!=source_hash or
        meta['deck_sha256']!=deck_hash or
        not np.array_equal(reference,np.asarray(frame[tr],dtype=np.float32)) or
        meta['train_feature_sha256']!=hashlib.sha256(reference.tobytes()).hexdigest()):
        raise ValueError('GAMER_BELIEF_CHECKPOINT_LINEAGE_OR_FEATURE_CHANGED')
    cross,_,_=oa._encode_train_map(frame,tr,meta['kernel'])
    prediction=_decoded(cross.astype(np.float64)@alpha+bias,
                        meta['specs'],meta['mode'])
    return prediction,meta


def _q_counterfactuals(deck,tags,qs,pred,ids):
    indexes=defaultdict(dict)
    for i in ids:
        tag=tags[int(i)]; indexes[(tag.get('group',tag.get('profile')),tag['surface'])][tag['edit']]=int(i)
    pairs=[]
    for (group,surface),pair in indexes.items():
        if group.startswith('freshcore.'):
            a,b=pair['base'],pair['q_edit']
        elif group.startswith('orthogonal.'):
            a,b=pair['base'],pair['q_edit']
        else:continue
        true=qs[b]-qs[a]
        observed=pred[b]-pred[a] if pred[b] is not None and pred[a] is not None else None
        pairs.append((true,observed))
    return {'pairs':len(pairs),
            'exact_fraction_delta':float(np.mean([a==b for a,b in pairs])) if pairs else None,
            'direction_correct':float(np.mean([b is not None and
                np.sign(float(a))==np.sign(float(b)) for a,b in pairs])) if pairs else None}


def _recompose(deck,tags,ix,qs,qpred,payoffs):
    """Exact Fraction arithmetic exclusively from neural decoded operands."""
    predicted=[];actual=[];operand_all_exact=[];q_is_exact=[]
    for i,case in enumerate(deck):
        true=case.target; actual.append(true)
        operand_all_exact.append(all(int(payoffs[name][i])==int(spine_value)
           for name,spine_value in zip(payoff.PAYOFFS,
                        _parse_payoffs(case.prompt))))
        q_is_exact.append(qpred[i]==qs[i])
        if qpred[i] is None:
            predicted.append(None)
        else:
            r,s,t,p=[int(payoffs[key][i]) for key in payoff.PAYOFFS]
            predicted.append(qpred[i]*(t-r)+(1-qpred[i])*(p-s))
    result={}
    for panel,ident in ix.items():
        if panel not in ('heldout_independent','heldout_zero_anchor',
                         'challenge_independent','challenge_zero_anchor'):continue
        ids=[int(i) for i in ident]
        correct=[predicted[i]==actual[i] for i in ids]
        errors=[abs(float(predicted[i]-actual[i])) if predicted[i] is not None
                else 1e6 for i in ids]
        result[panel]={'cases':len(ids),'exact_fraction_margin':float(np.mean(correct)),
           'within_0_05':float(np.mean([e<=.05 for e in errors])),
           'mae_valid_only':float(np.mean([v for v in errors if v<1e6]))
             if any(v<1e6 for v in errors) else None,
           'invalid_q_cases':int(sum(qpred[i] is None for i in ids)),
           'all_payoffs_exact':float(np.mean([operand_all_exact[i] for i in ids])),
           'q_exact':float(np.mean([q_is_exact[i] for i in ids]))}
    return result,predicted,operand_all_exact


def _parse_payoffs(prompt):
    spans=token._spans(prompt)['payoffs']
    if len(spans)!=4:raise ValueError('GAMER_BELIEF_BAD_PAYOFF_SPANS')
    return [Fraction(prompt[slice(*s)]) for s in spans]


def _triples(tags,indices,qs,pred,margin,all_payoffs_exact,*,expected_groups):
    groups=defaultdict(list)
    for raw in indices:
        i=int(raw);t=tags[i]
        if t['panel']!='zero_anchor':raise ValueError('GAMER_BELIEF_BAD_ZERO_PANEL')
        groups[(t.get('group',t.get('profile')),t['surface'])].append(i)
    if len(groups)!=expected_groups or any(len(ids)!=3 for ids in groups.values()):
        raise ValueError('GAMER_BELIEF_INCOMPLETE_ZERO_TRIPLES')
    q_all=0;q_centers=0;fully=0;all_operands=0
    for ids in groups.values():
        zero=[i for i in ids if tags[i]['edit']=='zero']
        if len(zero)!=1:raise ValueError('GAMER_BELIEF_MISSING_ZERO_CENTER')
        i=zero[0];q_centers+=int(pred[i]==qs[i]);q_all+=int(all(pred[j]==qs[j] for j in ids))
        all_operands+=int(all(all_payoffs_exact[j] for j in ids))
        actual_sign=[(float(margin[j])>0)-(float(margin[j])<0) if margin[j] is not None else None for j in ids]
        expected_sign=[(-1 if tags[j]['edit']=='negative' else
                         1 if tags[j]['edit']=='positive' else 0) for j in ids]
        fully+=int(actual_sign==expected_sign and margin[i]==0 and
                   all(pred[j]==qs[j] for j in ids) and all(all_payoffs_exact[j] for j in ids))
    return {'triples':len(groups),'exact_q_all_three':q_all,
            'exact_center_q':q_centers,'all_payoffs_exact_all_three':all_operands,
            'strict_exact_full_triples':fully,'accepted':fully==len(groups)}


def run(args):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_handoff as gamer
    if args.capture_batch<1:raise ValueError('GAMER_BELIEF_INVALID_CAPTURE_BATCH')
    source=_source(args.precision)
    previous=source['previous']
    origin=previous['source']['origin']
    protected=[source['root'],previous['root'],previous['source']['path'].parent,
               previous['source']['parent']['pool_path'].parent,
               origin['adapter'],origin['ckpt'].parent,origin['training'],
               origin['source'],origin['capture'],origin['audit_path'].parent]
    out=c0sign._output(args.output,protected)
    if out.exists():raise FileExistsError('GAMER_BELIEF_OUTPUT_ALREADY_EXISTS')
    deck,tags,ix,_,_=deconf._make_deck()
    if oracle.deck_hash(deck)!=source['report']['deck_sha256']:
        raise ValueError('GAMER_BELIEF_SOURCE_DECK_CHANGED')
    challenge,challenge_tags=_challenge()
    challenge_hash=oracle.deck_hash(challenge)
    if {c.id for c in deck}&{c.id for c in challenge}:
        raise ValueError('GAMER_BELIEF_CHALLENGE_OVERLAP')
    q_source=_exact_q(deck)
    q_new=_exact_q(challenge)
    # Unseen denominators are evaluated without touching the fitting or DEV sets.
    train_den={q_source[int(i)].denominator for i in ix['train']}
    new_den={q.denominator for q,t in zip(q_new,challenge_tags) if t['panel']=='independent'}
    if new_den & train_den or new_den != {19,23,29,31,37,41}:
        raise ValueError('GAMER_BELIEF_CHALLENGE_DENOMINATOR_LEAK')
    model_sha=token._sha(source['report_path'])
    print(f'GAMER EXACT BELIEF: frozen LoRA {STEP}; 600 reused + {len(challenge)} new cases; four payoff heads frozen',flush=True)
    with np.load(source['archive'],allow_pickle=False) as archive:
        old_views={key:np.asarray(archive['layer_09__'+key],dtype=np.float32)
                   for key in deconf.VIEWS}
    for key,frame in old_views.items():
        if frame.shape[0]!=600 or not np.isfinite(frame).all():
            raise ValueError('GAMER_BELIEF_BAD_ARCHIVED_FEATURES')
    print('  capturing previously unseen denominators and cancellation triples',flush=True)
    new_views=deconf._captured_features(previous['source'],challenge,
                              device=args.device,batch_size=args.capture_batch)['layer_09']
    full_views={k:np.concatenate((old_views[k],new_views[k]),axis=0)
                for k in old_views}
    _verify_frozen(source)
    print('  replaying four immutable payoff specialists',flush=True)
    replayed={}
    for name in payoff.PAYOFFS:
        checkpoint=Path(source['report']['diagnostic_checkpoints'][name]['path'])
        view=source['report']['payoffs'][name]['selection']['view']
        replay=payoff._replay_checkpoint(checkpoint,full_views[view],
                           expected_source_sha=source['report']['deconfounded_report_sha256'])
        replayed[name]=replay[name]
        # Must reproduce published original heldout accuracy before proceeding.
        indices=ix['heldout_independent'];truth=payoff._integer_targets(operand._targets(deck))[name]
        observed=float(np.mean(replayed[name][indices]==truth[indices]))
        expected=source['report']['payoffs'][name]['heldout']['independent']['exact']
        if abs(observed-expected)>1e-12:
            raise ValueError('GAMER_BELIEF_FROZEN_PAYOFF_REPLAY_NOT_IDENTICAL:' + name)
    full=deck+challenge
    tags_all=tags+challenge_tags
    qs=q_source+q_new
    ix_all=dict(ix)
    ix_all['challenge_independent']=np.asarray([600+i for i,t in enumerate(challenge_tags) if t['panel']=='independent'],dtype=int)
    ix_all['challenge_zero_anchor']=np.asarray([600+i for i,t in enumerate(challenge_tags) if t['panel']=='zero_anchor'],dtype=int)
    print('  selecting exact-rational readout using development cases only',flush=True)
    best,lexical,n_candidates=_select(full_views,qs,ix)
    source_support=[qs[int(i)] for i in ix[best['panel']]]
    lexical_support=[qs[int(i)] for i in ix[lexical['panel']]]
    qpred=best['prediction']
    report={'schema':SCHEMA,'status':'COMPLETE_FROZEN_EXACT_BELIEF_DIAGNOSTIC',
         'promotion':'BLOCKED_DIAGNOSTIC_ONLY','no_lora_training':True,
         'no_c0_training':True,'no_payoff_head_training':True,
         'no_zero_detector_mutation':True,'frozen_lora_step':STEP,
         'frozen_lora_sha256':source['report']['frozen_lora_sha256'],
         'source_payoff_report':str(source['report_path']),
         'source_payoff_report_sha256':model_sha,
         'frozen_payoff_checkpoints':source['report']['diagnostic_checkpoints'],
         'source_deck_sha256':source['report']['deck_sha256'],
         'challenge_deck_sha256':challenge_hash,
         'selection':'TRAIN_FIT_DEV_SELECT_HELDOUT_AND_CHALLENGE_EVALUATE',
         'candidate_count':n_candidates,
         'chosen':{key:best[key] for key in ('view','panel','kind','reg','mode')},
         'development':{p:_metric(qs,qpred,ix['dev_'+p],source_support) for p in PANELS},
         'heldout':{p:_metric(qs,qpred,ix['heldout_'+p],source_support) for p in PANELS},
         'challenge':{p:_metric(qs,qpred,ix_all['challenge_'+p],source_support) for p in PANELS},
         'lexical_control':{
             p:_metric(qs,lexical['prediction'],ix_all[p],lexical_support)
             for p in ('heldout_independent','heldout_zero_anchor','challenge_independent','challenge_zero_anchor')},
         'new_denominators':sorted(new_den),
         'explanation':('q classifier and digit readouts learn from frozen layer-9 hidden states. '
             'Prompt parsing supplies labels and an independent arithmetic oracle only; '
             'it is not a neural prediction. Existing payoff heads are replayed, never refit. '
             'Generated q challenge is never fitted or used for model selection.')}
    composition,margins,exact_payoffs=_recompose(full,tags_all,ix_all,qs,qpred,replayed)
    report['recomposition']=composition
    oracle_q_control,_,_=_recompose(full,tags_all,ix_all,qs,qs,replayed)
    report['parsed_q_frozen_payoffs_control']=oracle_q_control
    # Replayed heads are frozen, but new q challenges may expose payoff OOD
    # failures. A q head cannot earn acceptance by masking a payoff error.
    original_payoff_labels=payoff._integer_targets(operand._targets(deck))
    exact_payoff_labels={name:np.asarray([
       int(_parse_payoffs(c.prompt)[j]) for c in full],dtype=np.int64)
       for j,name in enumerate(payoff.PAYOFFS)}
    report['frozen_payoff_replay_metrics']={}
    for name in payoff.PAYOFFS:
        train_panel=source['report']['payoffs'][name]['selection']['train_panel']
        support=original_payoff_labels[name][ix[train_panel]]
        report['frozen_payoff_replay_metrics'][name]={
            p:payoff._operand_metric(exact_payoff_labels[name],replayed[name],
                                    ix_all[p],support)
            for p in ('heldout_independent','heldout_zero_anchor',
                      'challenge_independent','challenge_zero_anchor')}
    report['q_error_examples']={p:[
         {'id':full[int(i)].id,'expected':str(qs[int(i)]),
          'predicted':str(qpred[int(i)]) if qpred[int(i)] is not None else None}
         for i in ix_all[p] if qpred[int(i)]!=qs[int(i)]][:24]
         for p in ('heldout_independent','heldout_zero_anchor',
                   'challenge_independent','challenge_zero_anchor')}
    report['zero_anchor_triples']={
        'heldout':_triples(tags_all,ix['heldout_zero_anchor'],qs,qpred,margins,exact_payoffs,expected_groups=8),
        'challenge':_triples(tags_all,ix_all['challenge_zero_anchor'],qs,qpred,margins,exact_payoffs,expected_groups=8)}
    report['single_q_counterfactuals']={
       'heldout':_q_counterfactuals(full,tags_all,qs,qpred,ix['heldout_independent']),
       'challenge':_q_counterfactuals(full,tags_all,qs,qpred,ix_all['challenge_independent'])}
    report['oracle_control']={'exact_prompt_fraction':True,
         'exact_parsed_payoffs_and_exact_fraction_formula':True,
         'never_substituted_into_neural_recomposition':True}
    accepted=(all(report[k][p]['exact']>=.95 for k in ('heldout','challenge') for p in PANELS)
        and all(report['zero_anchor_triples'][p]['accepted'] for p in ('heldout','challenge'))
        and all(report['recomposition'][p]['within_0_05']>=.95 for p in (
                 'heldout_independent','heldout_zero_anchor','challenge_independent','challenge_zero_anchor'))
        and all(report['single_q_counterfactuals'][p]['exact_fraction_delta']>=.95 for p in ('heldout','challenge'))
        and all(report['frozen_payoff_replay_metrics'][name][panel]['exact']>=.95
            for name in payoff.PAYOFFS for panel in (
                'heldout_independent','heldout_zero_anchor',
                'challenge_independent','challenge_zero_anchor')))
    report['component_gate']={'passed':bool(accepted),
         'required_exact_recovery':.95,'strict_zero_triples_required':True,
         'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}
    _verify_frozen(source)
    out.mkdir(parents=True,exist_ok=False)
    checkpoint=out/'q-exact-rational-diagnostic.npz'
    report['diagnostic_checkpoint']=_save(checkpoint,best,full_views[best['view']],model_sha,
                                          source['report']['deck_sha256'])
    np.savez_compressed(out/'challenge_layer09_float32.npz',
        **{k:np.asarray(v,dtype=np.float32) for k,v in new_views.items()})
    report['challenge_feature_archive']={'path':str(out/'challenge_layer09_float32.npz'),
       'sha256':token._sha(out/'challenge_layer09_float32.npz'),
       'arrays':{k:list(v.shape) for k,v in new_views.items()},'dtype':'float32'}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER EXACT BELIEF COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--precision',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    return run(p.parse_args(argv))


if __name__=='__main__':main()
