#!/usr/bin/env python3
"""GAMER V1: frozen, tokenizer-aligned compositional belief sufficiency audit.

Train separate numerator/denominator digit (and integer-class controls) readouts
using ONLY frozen input/hidden-state vectors, never the prompt's digits at
prediction time. Select using development denominator values excluded from all
training. Evaluate on the old untouched heldout and 96-case unseen-prime/
cancellation challenge from the previous exact-belief audit, without selecting
on either. Frozen payoff specialists are replayed, never retrained.

This is a diagnostic; even exact extraction is not evidence of intrinsic
mathematical reasoning. A separate exact-prompt oracle is explicitly labelled.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_exact_belief_lab as previous
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign

SCHEMA = 'nanojev.gamer.v1.frozen-compositional-rational.v1'
STEP = 3000
# A six-digit component may be tokenized as six individual digit tokens.
# Never discard tokens when forming ordered, padded feature slots.
DIGITS = 6
SLOTS = DIGITS
REGS = (0.1, 1.0)
KINDS = ('linear', 'rbf')
STAGES = ('embedding', 'layer_09', 'final')
COMPONENTS = ('numerator', 'denominator')
VIEWS = ('pool', 'slots')
# None of these may appear in the frozen challenge, existing heldout or new dev.
TRAIN_DENOMINATORS = (7, 11, 13, 17, 43, 47, 53, 59, 61, 67, 71, 79,
                      83, 89, 97, 101, 103, 107, 113, 127, 131, 149,
                      157, 173, 181, 211, 257, 331, 509, 809,
                      1009, 2003, 4001, 10007, 20011, 100003)
DEV_DENOMINATORS = (277, 73, 109, 137, 163, 191, 223, 271,
                    307, 401, 1201, 3001, 5003, 12007, 30011, 70001)


def _source(root):
    root = Path(root).resolve()
    report_path = root / 'final_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema') != previous.SCHEMA or
        report.get('status') != 'COMPLETE_FROZEN_EXACT_BELIEF_DIAGNOSTIC' or
        report.get('frozen_lora_step') != STEP or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        report.get('no_lora_training') is not True or
        report.get('no_c0_training') is not True or
        report.get('no_payoff_head_training') is not True or
        report.get('no_zero_detector_mutation') is not True):
        raise ValueError('GAMER_COMPOSITION_REQUIRES_COMPLETE_FROZEN_BELIEF_REPORT')
    source = previous._source(Path(report['source_payoff_report']).parent)
    if token._sha(source['report_path']) != report['source_payoff_report_sha256']:
        raise ValueError('GAMER_COMPOSITION_PAYOFF_LINEAGE_CHANGED')
    if source['report']['frozen_lora_sha256'] != report['frozen_lora_sha256']:
        raise ValueError('GAMER_COMPOSITION_LORA_LINEAGE_CHANGED')
    for entry in (report['diagnostic_checkpoint'], report['challenge_feature_archive']):
        path = Path(entry['path'])
        if not path.is_file() or token._sha(path) != entry['sha256'] or not path.is_relative_to(root):
            raise ValueError('GAMER_COMPOSITION_SOURCE_ARCHIVE_CHANGED')
    if not report['diagnostic_checkpoint']['replay_matches_selected_predictions']:
        raise ValueError('GAMER_COMPOSITION_BELIEF_REPLAY_NOT_CERTIFIED')
    return {'root':root,'report_path':report_path,'report':report,
            'attested_sha256':token._sha(report_path),'precision':source}


def _verify_frozen(source):
    if token._sha(source['report_path']) != source['attested_sha256']:
        raise ValueError('GAMER_COMPOSITION_SOURCE_REPORT_MUTATED')
    previous._verify_frozen(source['precision'])
    for entry in (source['report']['diagnostic_checkpoint'],
                  source['report']['challenge_feature_archive']):
        if token._sha(Path(entry['path'])) != entry['sha256']:
            raise ValueError('GAMER_COMPOSITION_SOURCE_CHECKPOINT_MUTATED')


def _denominator_deck():
    """Unseen-denominator DEV, with exact rational labels, no oracle leaks.

    Variation includes one- to six-digit denominators and numerator changes
    holding denominator and payoff values fixed. No production data is edited.
    """
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    renderers = (('spine_belief',prompts._spine_belief_prompt),
                 ('game_prose_belief',prompts._game_prose_belief_prompt))
    deck, tags = [], []
    for role, denoms in (('train',TRAIN_DENOMINATORS),('dev',DEV_DENOMINATORS)):
        for k,den in enumerate(denoms):
            # Different payoff tuples across profiles, but identical for
            # the q-only counterfactual within each profile.
            # Reuse each payoff tuple across multiple denominators so the
            # context cannot identify the denominator by its profile alone.
            slot = k % 8
            stage = spine.Stage(Fraction(113 + (slot * 7) % 19),
                Fraction(119 + (slot * 5) % 17), Fraction(125 + (slot * 11) % 13),
                Fraction(115 + (slot * 13) % 19))
            # These are *not* zero anchors. Exact zeros are independently
            # evaluated on the previous two protected challenge decks.
            for j, frac in enumerate((.17,.31,.49,.67,None)):
                # The final entry has the same numerator across different
                # denominators, forming denominator-only interventions.
                num = 5 if frac is None else max(1,min(den-1,round(den*frac)))
                q = Fraction(num,den)
                for surface, render in renderers:
                    ident = f'compose.{role}.{k:03d}.{j}.{surface}'
                    value = stage.margin(q)
                    deck.append(oracle.Case(ident,role,f'compose.{role}.{k:03d}',
                        surface,render(stage,q),value,'compositional_q',value))
                    tags.append({'role':role,'panel':'independent','denominator':q.denominator,
                                 'stage_slot':slot,'surface':surface,'group':f'compose.{role}.{k:03d}',
                                 'edit':f'numerator_{j}','id':ident})
    if len({x.id for x in deck}) != len(deck):
        raise ValueError('GAMER_COMPOSITION_DUPLICATE_CASE_ID')
    qs = previous._exact_q(deck)
    if any(not 0<q<1 or q.denominator>10**DIGITS-1 or q.numerator>10**DIGITS-1 for q in qs):
        raise ValueError('GAMER_COMPOSITION_FRACTION_OUT_OF_RANGE')
    index = {r:np.asarray([i for i,t in enumerate(tags) if t['role']==r],dtype=int)
             for r in ('train','dev')}
    return deck,tags,index


def _heldout_split(source):
    """Never fit or tune against existing heldout/challenge labels."""
    old,tags,ix,_,_=deconf._make_deck()
    challenge,ct=previous._challenge()
    # Verify that all old labels are still exact prompt-derived values.
    previous._exact_q(old+challenge)
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    if (oracle.deck_hash(old) != source['report']['source_deck_sha256'] or
        oracle.deck_hash(challenge) != source['report']['challenge_deck_sha256']):
        raise ValueError('GAMER_COMPOSITION_PROTECTED_DECK_CHANGED')
    return old,tags,ix,challenge,ct


def _aligned_components(tok,deck):
    """Use tokenizer offsets ONLY to choose hidden-state positions.

    At inference, no token spelling, token ID or numeric prompt substring is
    passed as a predictive feature; labels are used solely for scoring/fitting.
    """
    if getattr(tok,'padding_side',None) != 'right':
        raise ValueError('GAMER_COMPOSITION_RIGHT_PADDING_REQUIRED')
    aligned = []
    for case in deck:
        text = case.prompt
        start,end = token._spans(text)['belief'][0]
        chars = text[start:end]
        if '/' not in chars or chars.count('/') != 1:
            raise ValueError('GAMER_COMPOSITION_Q_NOT_A_FRACTION')
        slash = start + chars.index('/')
        bounds = ((start,slash),(slash+1,end))
        record = tok(text,add_special_tokens=True,truncation=False,
                     return_offsets_mapping=True)
        offsets = np.asarray(record['offset_mapping'],dtype=int)
        ids = np.asarray(record['input_ids'],dtype=int)
        if offsets.shape != (len(ids),2) or not len(ids):
            raise ValueError('GAMER_COMPOSITION_INVALID_TOKEN_OFFSETS')
        pieces = []
        for a,b in bounds:
            chosen = []
            for i,(lo,hi) in enumerate(offsets):
                if hi>lo and hi>a and lo<b:
                    # A single token spanning the slash would compromise
                    # numerator/denominator independence; fail closed.
                    if lo<a or hi>b:
                        raise ValueError('GAMER_COMPOSITION_NUMERIC_TOKEN_CROSSES_BOUNDARY')
                    chosen.append(i)
            if not chosen or len(chosen)>SLOTS:
                raise ValueError(
                    'GAMER_COMPOSITION_UNSUPPORTED_COMPONENT_TOKEN_COUNT: '
                    f'case={case.id} chars={text[a:b]!r} '
                    f'tokens={len(chosen)} slots={SLOTS}'
                )
            if min(offsets[i,0] for i in chosen) != a or max(offsets[i,1] for i in chosen) != b:
                raise ValueError('GAMER_COMPOSITION_PARTIAL_NUMERIC_SPAN')
            pieces.append(np.asarray(chosen,dtype=int))
        if set(pieces[0]) & set(pieces[1]):
            raise ValueError('GAMER_COMPOSITION_OVERLAPPING_COMPONENTS')
        aligned.append({'indices':pieces,'length':len(ids)})
    return aligned


def _component_vectors(hidden, span):
    h = np.asarray(hidden,dtype=np.float32)
    if h.ndim!=2 or h.shape[1]!=1024 or len(h)!=span['length']:
        raise ValueError('GAMER_COMPOSITION_HIDDEN_SHAPE_INVALID')
    fields = {}
    for name,ids in zip(COMPONENTS,span['indices']):
        selected = h[ids]
        pool = selected.mean(axis=0)
        padded = np.zeros((SLOTS,1024),dtype=np.float32)
        padded[-len(selected):] = selected
        fields[f'{name}_pool'] = pool
        fields[f'{name}_slots'] = padded.reshape(-1)
    return fields


def _capture(source,deck,device,batch_size):
    """Fresh frozen forward pass, recording embedding, layer-9 and final.

    Also recover payoff views from *the same* layer-9 states for byte-verified
    frozen-checkpoint replay, without loading the backbone a second time.
    """
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length
    original=source['precision']['previous']['source']['origin']
    parent=source['precision']['previous']['source']
    model_id=original['meta']['lineage']['model_id']
    if stress._verify_snapshot(parent['snapshot'],STEP) != source['report']['frozen_lora_sha256']:
        raise ValueError('GAMER_COMPOSITION_LORA_SNAPSHOT_CHANGED')
    _,tok,model=gamer.model_components(model_id,device,adapter_dir=parent['snapshot'])
    cfg=original['meta']['lineage']['configuration']
    values={stage:{f'{component}_{view}':[] for component in COMPONENTS for view in VIEWS}
            for stage in STAGES}
    payoff_views={key:[] for key in deconf.VIEWS}
    try:
        aligned=_aligned_components(tok,deck)
        payoff_masks=operand._aligned_token_spans(tok,deck)
        core=oracle._backbone_parts(model)
        if len(core.layers)<=9:raise ValueError('GAMER_COMPOSITION_LAYER_09_ABSENT')
        modules={'embedding':core.embed_tokens,'layer_09':core.layers[9], 'final':core.norm}
        model.eval()
        for start in range(0,len(deck),batch_size):
            if start==0 or start%40<batch_size:
                print(f'COMPOSITION_CAPTURE {start}/{len(deck)}',flush=True)
            chunk=deck[start:start+batch_size]
            if any(len(tok.encode(c.prompt,add_special_tokens=True))>cfg['max_length'] for c in chunk):
                raise ValueError('GAMER_COMPOSITION_PROMPT_EXCEEDS_MAX_LENGTH')
            batch=tok([c.prompt for c in chunk],padding=True,truncation=True,
                      max_length=cfg['max_length'],return_tensors='pt')
            batch={k:v.to(device) for k,v in batch.items()}
            lengths=[int(v) for v in batch['attention_mask'].sum(-1).tolist()]
            seen=defaultdict(list);hooks=[]
            for stage,module in modules.items():
                def record(_module,_args,result,key=stage):
                    h=result[0] if isinstance(result,(tuple,list)) else result
                    if not isinstance(h,torch.Tensor) or h.ndim!=3:
                        raise ValueError('GAMER_COMPOSITION_CAPTURE_BAD_TENSOR')
                    seen[key].append(h.detach())
                hooks.append(module.register_forward_hook(record))
            try:
                with torch.inference_mode():
                    model(**batch,output_hidden_states=False,return_dict=True,logits_to_keep=1)
            finally:
                for hook in hooks: hook.remove()
            if any(len(seen[name])!=1 for name in STAGES):
                raise ValueError('GAMER_COMPOSITION_INCOMPLETE_CAPTURE')
            for j,length in enumerate(lengths):
                k=start+j;spec=aligned[k];ps=payoff_masks[k]
                if length!=spec['length'] or length!=len(ps['ids']):
                    raise ValueError('GAMER_COMPOSITION_CAPTURE_TOKEN_MISMATCH')
                positions=np.asarray(positions_for_length(length,100,cfg['curvature']),dtype=int)
                for stage in STAGES:
                    h=seen[stage][0][j,:length].float().cpu().numpy()
                    if not np.isfinite(h).all():
                        raise ValueError('GAMER_COMPOSITION_NONFINITE_CAPTURE')
                    parts=_component_vectors(h,spec)
                    for key,arr in parts.items():values[stage][key].append(arr)
                    if stage=='layer_09':
                        pv=operand._feature_from_hidden(h,ps,positions)
                        pv['lexical_numeric']=operand._lexical(ps)
                        for key in payoff_views:payoff_views[key].append(pv[key])
            del seen
        arrays={stage:{key:np.stack(frame).astype(np.float32)
                        for key,frame in values[stage].items()}
                for stage in STAGES}
        pay={key:np.stack(frame).astype(np.float32) for key,frame in payoff_views.items()}
        if any(not np.isfinite(frame).all() for stage in arrays.values() for frame in stage.values()):
            raise ValueError('GAMER_COMPOSITION_NONFINITE_FEATURES')
        return arrays,pay
    finally:
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()


def _targets(q,part):
    return [getattr(x,part) for x in q]


def _columns(values,train,mode):
    tr=[int(values[int(i)]) for i in train]
    if any(x<1 or x>=10**DIGITS for x in tr):
        raise ValueError('GAMER_COMPOSITION_UNSUPPORTED_DIGIT_WIDTH')
    if mode=='integer_class':
        vocab=sorted(set(tr))
        y=np.asarray([[int(v==x) for v in vocab] for x in tr],dtype=np.float64)
        return y,{'mode':mode,'vocabulary':vocab}
    if mode!='digitwise':raise ValueError('GAMER_COMPOSITION_UNKNOWN_MODE')
    y=np.zeros((len(tr),DIGITS*10),dtype=np.float64)
    for i,num in enumerate(tr):
        for col,c in enumerate(f'{num:0{DIGITS}d}'):
            y[i,10*col+int(c)]=1
    return y,{'mode':mode,'digits':DIGITS}


def _decode(scores,meta):
    mode=meta['mode']
    if mode=='integer_class':
        vocab=meta['vocabulary'];return [int(vocab[int(j)]) for j in np.argmax(scores,axis=1)]
    if mode!='digitwise':raise ValueError('GAMER_COMPOSITION_UNKNOWN_MODE')
    if scores.shape[1]!=DIGITS*10:raise ValueError('GAMER_COMPOSITION_BAD_DIGIT_SCORE_SHAPE')
    out=[]
    for row in scores:
        chars=''.join(str(int(np.argmax(row[pos*10:(pos+1)*10]))) for pos in range(DIGITS))
        n=int(chars);out.append(n if 1<=n<10**DIGITS else None)
    return out


def _regression(frame,values,train,kind,reg,mode):
    """Train-only centered kernel ridge; no DEV/heldout target used in fit."""
    y,meta=_columns(values,train,mode)
    cross,gram,_=operand._encode_train_map(frame,train,kind)
    sym=(gram.astype(np.float64)+gram.T.astype(np.float64))*.5
    diag=np.eye(len(train),dtype=np.float64)*reg
    bias=np.mean(y,axis=0)
    alpha=np.linalg.solve(sym+diag,y-bias)
    pred=_decode(cross.astype(np.float64)@alpha+bias,meta)
    return {'kind':kind,'reg':reg,'mode':mode,'training':np.asarray(train,dtype=int),
            'meta':meta,'alpha':alpha,'bias':bias,'prediction':pred}


def _component_metric(target,pred,indices,train_support):
    idx=[int(i) for i in indices]
    exact=[pred[i]==target[i] for i in idx]
    support=set(train_support)
    unseen=[target[i] not in support for i in idx]
    mae=[abs(target[i]-pred[i]) if pred[i] is not None else 1e6 for i in idx]
    return {'cases':len(idx),'exact':float(np.mean(exact)),
            'exact_cases':int(sum(exact)),'mae_with_invalid_penalty':float(np.mean(mae)),
            'unseen_value_cases':int(sum(unseen)),
            'unseen_value_exact':float(np.mean([correct for correct,is_new in zip(exact,unseen) if is_new])) if any(unseen) else None,
            'train_supported_fraction':float(np.mean([not x for x in unseen]))}


def _candidates(frame,values,train,*,kernels=KINDS,regs=REGS):
    """Factor each train-only kernel once for all digit/class candidates."""
    ydigit,mdigit=_columns(values,train,'digitwise')
    yclass,mclass=_columns(values,train,'integer_class')
    for kind in kernels:
        cross,gram,_=operand._encode_train_map(frame,train,kind)
        sym=(gram.astype(np.float64)+gram.T.astype(np.float64))*.5
        eigen,vec=np.linalg.eigh(sym)
        eigen=np.maximum(eigen,0.)
        for mode,y,meta in (('digitwise',ydigit,mdigit),
                             ('integer_class',yclass,mclass)):
            bias=y.mean(axis=0)
            proj=vec.T@(y-bias)
            for reg in regs:
                alpha=vec@(proj/(eigen[:,None]+reg))
                scores=cross.astype(np.float64)@alpha+bias
                yield {'kind':kind,'reg':reg,'mode':mode,
                       'training':np.asarray(train,dtype=int),
                       'meta':meta,'alpha':alpha,'bias':bias,
                       'prediction':_decode(scores,meta)}


def _choose(features,q,train,dev,*,kernels=KINDS,regs=REGS,stages=STAGES,views=VIEWS):
    selected={};counts={}
    for part in COMPONENTS:
        target=_targets(q,part)
        support=[target[int(i)] for i in train]
        best=None;tested=0
        for stage in stages:
            for view in views:
                frame=features[stage][f'{part}_{view}']
                for fit in _candidates(frame,target,train,kernels=kernels,regs=regs):
                    metric=_component_metric(target,fit['prediction'],dev,support)
                    # Prioritize actual unseen number values on DEV, not
                    # memorized full-number classes from TRAIN.
                    novel=metric['unseen_value_exact']
                    rank=(novel if novel is not None else -1.,metric['exact'],
                          -metric['mae_with_invalid_penalty'])
                    tested+=1
                    if best is None or rank>best['rank']:
                        best={**fit,'stage':stage,'view':view,'rank':rank,
                              'development':metric,'support':support}
        selected[part]=best;counts[part]=tested
    return selected,counts


def _checkpoint(path,fit,feature,source_sha,deck_sha):
    tr=fit['training'];original=np.asarray(feature[tr],dtype=np.float32)
    meta={'schema':SCHEMA,'source_report_sha256':source_sha,'deck_sha256':deck_sha,
          'stage':fit['stage'],'view':fit['view'],'mode':fit['mode'],
          'kernel':fit['kind'],'regularization':fit['reg'],
          'target_spec':fit['meta'],
          'training_feature_sha256':hashlib.sha256(original.tobytes()).hexdigest()}
    np.savez_compressed(path,training_feature=original,training_indices=tr,
                        alpha=fit['alpha'],bias=fit['bias'],
                        metadata=np.asarray(json.dumps(meta,sort_keys=True)))
    replay,_=_replay(path,feature,source_sha,deck_sha)
    if replay!=fit['prediction']:
        raise ValueError('GAMER_COMPOSITION_CHECKPOINT_REPLAY_MISMATCH')
    return {'path':str(path),'sha256':token._sha(path),'replay_matches':True,
            'stage':fit['stage'],'view':fit['view'],'mode':fit['mode']}


def _replay(path,feature,source_sha,deck_sha):
    with np.load(path,allow_pickle=False) as ck:
        meta=json.loads(str(ck['metadata'].item()))
        original=np.asarray(ck['training_feature'],dtype=np.float32)
        tr=np.asarray(ck['training_indices'],dtype=int)
        alpha=np.asarray(ck['alpha'],dtype=np.float64)
        bias=np.asarray(ck['bias'],dtype=np.float64)
    if (meta['schema']!=SCHEMA or meta['source_report_sha256']!=source_sha or
        meta['deck_sha256']!=deck_sha or
        hashlib.sha256(original.tobytes()).hexdigest()!=meta['training_feature_sha256'] or
        not np.array_equal(np.asarray(feature[tr],dtype=np.float32),original)):
        raise ValueError('GAMER_COMPOSITION_CHECKPOINT_LINEAGE_CHANGED')
    cross,_,_=operand._encode_train_map(feature,tr,meta['kernel'])
    return _decode(cross.astype(np.float64)@alpha+bias,meta['target_spec']),meta


def _assemble(numerators,denominators):
    if len(numerators)!=len(denominators):raise ValueError('GAMER_COMPOSITION_COMPONENT_COUNT_MISMATCH')
    result=[]
    for n,d in zip(numerators,denominators):
        if (n is None or d is None or d<1 or n<1 or n>=d):result.append(None)
        else:result.append(Fraction(n,d))
    return result


def _fractions_by_digits(q,pred,ids):
    groups=defaultdict(list)
    for i in ids:groups[len(str(q[int(i)].denominator))].append(int(i))
    return {str(k):previous._metric(q,pred,v,[]) for k,v in sorted(groups.items())}


def _counterfactual(deck,tags,q,pred,ids):
    # Exact numerator-only changes, same denominator and payoffs in each group.
    pairs=defaultdict(dict)
    for i in ids:
        k=int(i);t=tags[k];pairs[(t['group'],t['surface'])][t['edit']]=k
    tested=[]
    for key,part in pairs.items():
        for a,b in (('numerator_0','numerator_1'),('numerator_2','numerator_3')):
            if a not in part or b not in part:continue
            i,j=part[a],part[b]
            if q[i].denominator!=q[j].denominator:
                raise ValueError('GAMER_COMPOSITION_COUNTERFACTUAL_NOT_SINGLE_NUMERATOR')
            delta=q[j]-q[i]
            observed=pred[j]-pred[i] if pred[j] is not None and pred[i] is not None else None
            tested.append((delta,observed))
    return {'pairs':len(tested),
            'exact_rational_delta':float(np.mean([a==b for a,b in tested])) if tested else None,
            'direction_correct':float(np.mean([b is not None and
                     np.sign(float(a))==np.sign(float(b)) for a,b in tested])) if tested else None}



def _denominator_counterfactual(tags,q,pred,ids):
    """Same numerator and payoff tuple, different denominator, no label leak."""
    groups=defaultdict(list)
    for ii in ids:
        i=int(ii);tag=tags[i]
        if tag['edit']=='numerator_4':
            groups[(tag['stage_slot'],tag['surface'])].append(i)
    compared=[]
    for records in groups.values():
        for a,b in zip(records,records[1:]):
            if q[a].numerator!=q[b].numerator or q[a].denominator==q[b].denominator:
                raise ValueError('GAMER_COMPOSITION_BAD_DENOMINATOR_ONLY_PAIR')
            observed=pred[b]-pred[a] if pred[a] is not None and pred[b] is not None else None
            compared.append((q[b]-q[a],observed,
                pred[a] is not None and pred[b] is not None and
                pred[a].numerator==pred[b].numerator==q[a].numerator))
    return {'pairs':len(compared),
        'exact_fraction_delta':float(np.mean([a==b for a,b,_ in compared])) if compared else None,
        'numerator_invariant_exact':float(np.mean([v for _,__,v in compared])) if compared else None,
        'direction_correct':float(np.mean([b is not None and
             np.sign(float(a))==np.sign(float(b)) for a,b,_ in compared])) if compared else None}


def run(args):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_handoff as gamer
    if args.capture_batch<1:raise ValueError('GAMER_COMPOSITION_INVALID_CAPTURE_BATCH')
    source=_source(args.belief)
    prec=source['precision']
    origin=prec['previous']['source']['origin']
    protect=[source['root'],prec['root'],prec['previous']['root'],
             prec['previous']['source']['path'].parent,
             origin['adapter'],origin['ckpt'].parent,origin['training'],
             origin['source'],origin['capture'],origin['audit_path'].parent]
    out=c0sign._output(args.output,protect)
    if out.exists():raise FileExistsError('GAMER_COMPOSITION_OUTPUT_ALREADY_EXISTS')
    generated,generated_tags,gix=_denominator_deck()
    old,tags,ix,challenge,challenge_tags=_heldout_split(source)
    deck=old+challenge+generated
    joint_tags=tags+challenge_tags+generated_tags
    q=previous._exact_q(deck)
    assert len(q)==len(deck)
    oldlen=len(old);newlen=len(challenge)
    train=np.concatenate((ix['train'], oldlen+newlen+gix['train']))
    dev=(oldlen+newlen+gix['dev'])
    old_train_den={q[int(i)].denominator for i in train}
    dev_den={q[int(i)].denominator for i in dev}
    challenges={q[oldlen+i].denominator for i,t in enumerate(challenge_tags)
                if t['panel']=='independent'}
    if (not dev_den.isdisjoint(old_train_den) or
        not challenges.isdisjoint(old_train_den|dev_den) or
        challenges!={19,23,29,31,37,41}):
        raise ValueError('GAMER_COMPOSITION_DENOMINATOR_SPLIT_LEAK')
    # Original old heldout and all challenge denominators are NEVER train/dev.
    for split in (ix['heldout_independent'],):
        if any(int(i) in set(train)|set(dev) for i in split):
            raise ValueError('GAMER_COMPOSITION_HELDOUT_LEAK')
    deck_hash=oracle.deck_hash(deck)
    _verify_frozen(source)
    print(f'GAMER COMPOSITIONAL BELIEF: frozen LoRA {STEP}; '
          f'{len(deck)} captures; {len(train)} TRAIN, {len(dev)} fresh DEV; '
          'old heldout and challenge reserved',flush=True)
    features,payoff_views=_capture(source,deck,args.device,args.capture_batch)
    _verify_frozen(source)
    # Verify byte-wise fp32 parity against previously captured frozen activations.
    with np.load(prec['archive'],allow_pickle=False) as archived:
        old_diff=max(float(np.max(np.abs(payoff_views[key][:oldlen] -
                        np.asarray(archived['layer_09__'+key],dtype=np.float32))))
                     for key in deconf.VIEWS)
    with np.load(source['report']['challenge_feature_archive']['path'],allow_pickle=False) as archived:
        new_diff=max(float(np.max(np.abs(payoff_views[key][oldlen:oldlen+newlen] -
                        np.asarray(archived[key],dtype=np.float32)))) for key in deconf.VIEWS)
    if old_diff>1e-3 or new_diff>1e-3:
        raise ValueError('GAMER_COMPOSITION_FROZEN_FEATURE_PARITY_FAILED')
    print('  selecting independent component digit heads on fresh unseen-denominator DEV',flush=True)
    selected,counts=_choose(features,q,train,dev)
    n=selected['numerator']['prediction'];d=selected['denominator']['prediction']
    qpred=_assemble(n,d)
    _verify_frozen(source)
    print('  replaying frozen payoff heads and scoring strict zero triples',flush=True)
    payoff_preds={}
    old_payoffs=payoff._integer_targets(operand._targets(old))
    for name in payoff.PAYOFFS:
        checkpoint=Path(prec['report']['diagnostic_checkpoints'][name]['path'])
        view=prec['report']['payoffs'][name]['selection']['view']
        replay=payoff._replay_checkpoint(checkpoint,payoff_views[view],
                    expected_source_sha=prec['report']['deconfounded_report_sha256'])
        payoff_preds[name]=replay[name]
        ids=ix['heldout_independent']
        observed=float(np.mean(payoff_preds[name][ids]==old_payoffs[name][ids]))
        expected=prec['report']['payoffs'][name]['heldout']['independent']['exact']
        if abs(observed-expected)>1e-12:
            raise ValueError('GAMER_COMPOSITION_PAYOFF_CHECKPOINT_REPLAY_CHANGED:'+name)
    indices={'heldout_independent':ix['heldout_independent'],
             'heldout_zero_anchor':ix['heldout_zero_anchor'],
             'challenge_independent':np.asarray([oldlen+i for i,t in enumerate(challenge_tags)
                if t['panel']=='independent'],dtype=int),
             'challenge_zero_anchor':np.asarray([oldlen+i for i,t in enumerate(challenge_tags)
                if t['panel']=='zero_anchor'],dtype=int),
             'fresh_dev':dev}
    recomp,margins,payoff_exact=previous._recompose(deck,joint_tags,indices,q,qpred,payoff_preds)
    parsed_control,_,_=previous._recompose(deck,joint_tags,indices,q,q,payoff_preds)
    triples={
       'heldout':previous._triples(joint_tags,indices['heldout_zero_anchor'],q,qpred,margins,payoff_exact,expected_groups=8),
       'challenge':previous._triples(joint_tags,indices['challenge_zero_anchor'],q,qpred,margins,payoff_exact,expected_groups=8)}
    fraction_metrics={key:previous._metric(q,qpred,ids,[q[int(i)] for i in train])
                      for key,ids in indices.items()}
    comp_metrics={part:{key:_component_metric(_targets(q,part),selected[part]['prediction'],ids,
                         selected[part]['support']) for key,ids in indices.items()}
                  for part in COMPONENTS}
    by_length={panel:_fractions_by_digits(q,qpred,indices[panel]) for panel in
               ('challenge_independent','challenge_zero_anchor','heldout_zero_anchor')}
    change={'fresh_dev':_counterfactual(deck,joint_tags,q,qpred,dev),
            'fresh_dev_denominator_only':_denominator_counterfactual(joint_tags,q,qpred,dev),
            'challenge':previous._q_counterfactuals(deck,joint_tags,q,qpred,indices['challenge_independent']),
            'heldout':previous._q_counterfactuals(deck,joint_tags,q,qpred,indices['heldout_independent'])}
    complete=(all(fraction_metrics[panel]['exact']>=.95 for panel in (
            'heldout_independent','heldout_zero_anchor','challenge_independent','challenge_zero_anchor'))
        and all(triples[name]['accepted'] for name in triples)
        and all(recomp[panel]['within_0_05']>=.95 for panel in recomp)
        and all(change[name]['exact_fraction_delta']>=.95 for name in (
            'challenge','heldout','fresh_dev_denominator_only'))
        and all(np.mean([payoff_exact[int(i)] for i in indices[panel]])>=.95 for panel in (
             'heldout_independent','heldout_zero_anchor','challenge_independent','challenge_zero_anchor')))
    report={'schema':SCHEMA,'status':'COMPLETE_COMPOSITIONAL_BELIEF_DIAGNOSTIC',
        'promotion':'BLOCKED_DIAGNOSTIC_ONLY','no_lora_training':True,
        'no_c0_training':True,'no_payoff_head_training':True,'no_zero_detector_mutation':True,
        'frozen_lora_step':STEP,'frozen_lora_sha256':source['report']['frozen_lora_sha256'],
        'source_belief_report':str(source['report_path']),
        'source_belief_report_sha256':source['attested_sha256'],
        'source_deck_sha256':source['report']['source_deck_sha256'],
        'challenge_deck_sha256':source['report']['challenge_deck_sha256'],
        'combined_deck_sha256':deck_hash,
        'split':{'train':len(train),'fresh_dev':len(dev),
            'original_heldout':len(ix['heldout']),'protected_challenge':len(challenge),
            'train_denominators':sorted(old_train_den),'fresh_dev_denominators':sorted(dev_den),
            'heldout_prime_denominators':sorted(challenges),
            'fresh_dev_disjoint_from_training':dev_den.isdisjoint(old_train_den),
            'challenge_disjoint_from_training_and_dev':challenges.isdisjoint(old_train_den|dev_den)},
        'method':('Tokenizer offset maps choose numerator/denominator token positions only. '
            'Frozen hidden activations, not numeric prompt strings or token IDs, are decoder inputs. '
            'Labels are exact strings from TRAIN prompts only; DEV selects candidates; '
            'heldout/challenge are evaluated once. Whole-number classifiers are controls. '
            'A successful embedding readout can exploit lexical token identity, and does not '
            'establish arithmetic reasoning in LoRA.'),
        'selected':{part:{k:fit[k] for k in ('stage','view','kind','reg','mode','development')}
                    for part,fit in selected.items()},
        'candidate_count':counts,
        'component_accuracy':comp_metrics,'fraction_accuracy':fraction_metrics,
        'fraction_by_denominator_digits':by_length,
        'single_q_counterfactuals':change,
        'recomposition':recomp,'parsed_q_frozen_payoffs_oracle':parsed_control,
        'zero_anchor_triples':triples,
        'capture_parity':{'old_fp32_max_delta':old_diff,
                          'challenge_fp32_max_delta':new_diff},
        'error_examples':{panel:[
            {'id':deck[int(i)].id, 'expected':str(q[int(i)]),
             'predicted':str(qpred[int(i)]) if qpred[int(i)] is not None else None,
             'pred_num':n[int(i)],'pred_den':d[int(i)]}
            for i in indices[panel] if qpred[int(i)]!=q[int(i)]][:20]
            for panel in ('fresh_dev','heldout_zero_anchor',
                          'challenge_independent','challenge_zero_anchor')},
        'component_gate':{'passed':bool(complete),'required_exact':.95,
             'strict_zero_triples_required':True,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}}
    _verify_frozen(source)
    out.mkdir(parents=True,exist_ok=False)
    for part in COMPONENTS:
        fit=selected[part]
        path=out/f'{part}-compositional-diagnostic.npz'
        record=_checkpoint(path,fit,features[fit['stage']][f'{part}_{fit["view"]}'],
                           source['attested_sha256'],deck_hash)
        report.setdefault('checkpoints',{})[part]=record
    # Save the two diagnostic feature views, not all high-dimensional capture
    # tensors. SHA binding permits independent replay and protects provenance.
    selected_archive=out/'selected_component_float32.npz'
    selected_frames={part:np.asarray(features[fit['stage']][f'{part}_{fit["view"]}'],
                       dtype=np.float32) for part,fit in selected.items()}
    np.savez_compressed(selected_archive,**selected_frames)
    report['selected_feature_archive']={'path':str(selected_archive),
        'sha256':token._sha(selected_archive), 'dtype':'float32',
        'arrays':{key:list(frame.shape) for key,frame in selected_frames.items()}}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER COMPOSITIONAL BELIEF COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--belief',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    parser.add_argument('--capture-batch',type=int,default=2)
    return run(parser.parse_args(argv))


if __name__=='__main__': main()
