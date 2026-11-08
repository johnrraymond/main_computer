#!/usr/bin/env python3
"""GAMER V1: repair only the unsupported-S payoff extraction, then live acceptance.

The S replacement reuses the TRAIN-only, 10-digit embedding readout certified
by the shared-q experiment. It NEVER trains/reselects on heldout S=109; it
reads S's tokenizer-aligned embeddings through the frozen model. R/T/P replay
unchanged from their byte-attested layer-9 checkpoints.

The live stage forwards NEW prompts through the actual frozen Qwen+LoRA model,
extracts q and S from the captured input embeddings, replays R/T/P on the
captured layer-9 representations, and composes an exact Fraction margin.
It neither modifies nor claims to certify the existing production to_numerical
head; this is an operational *candidate* gate, not production promotion.
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

from tools import nanojev_gamer_v1_shared_digit_repair_lab as shared
from tools import nanojev_gamer_v1_positional_digit_audit as positional
from tools import nanojev_gamer_v1_compositional_belief_lab as compose
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.s-compositional-operational-acceptance.v1'
SLOTS = shared.SLOTS
HIDDEN = shared.HIDDEN
FRESH_DENOMINATORS = (19, 23, 29, 31, 37, 41, 19, 23)
S_INTERVENTIONS = (107, 109, 136, 140)


def _source(folder):
    root = Path(folder).resolve()
    report_path = root / 'final_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema') != shared.SCHEMA or
        report.get('status') != 'COMPLETE_FROZEN_SHARED_DIGIT_REPAIR' or
        report.get('gate', {}).get('all_1216_fraction_cases_exact') is not True or
        report.get('gate', {}).get('all_16_zero_triples_passed') is not True or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        any(report.get(k) is not True for k in ('no_c0_training', 'no_lora_training',
               'no_payoff_head_training', 'no_zero_detector_mutation'))):
        raise ValueError('GAMER_S_REQUIRES_COMPLETE_FROZEN_SHARED_DIGIT_REPAIR')
    underlying = shared._source(Path(report['source_positional_report']).parent)
    if token._sha(underlying['report_path']) != report['source_positional_sha256']:
        raise ValueError('GAMER_S_POSITIONAL_REPORT_LINEAGE_CHANGED')
    head_path = Path(report['diagnostic_checkpoint']['path']).resolve()
    if (not head_path.is_file() or not head_path.is_relative_to(root) or
        token._sha(head_path) != report['diagnostic_checkpoint']['sha256'] or
        report['diagnostic_checkpoint']['mode'] != report['selected']):
        raise ValueError('GAMER_S_Q_DIGIT_HEAD_MUTATED')
    with np.load(head_path, allow_pickle=False) as ck:
        meta = json.loads(str(ck['metadata'].item()))
        centers = ck['centroids'].copy(); weights = ck['weights'].copy()
    if (meta.get('schema') != shared.SCHEMA or
        meta.get('source_positional_sha256') != report['source_positional_sha256'] or
        meta.get('mode') != report['selected'] or
        centers.shape != (10,HIDDEN) or centers.dtype != np.float32 or
        weights.shape != (HIDDEN,10) or
        hashlib.sha256(centers.tobytes()).hexdigest() != meta['centroids_sha256']):
        raise ValueError('GAMER_S_Q_DIGIT_HEAD_REPLAY_INVALID')
    return {'root':root, 'report':report, 'report_path':report_path,
            'report_sha256':token._sha(report_path), 'underlying':underlying,
            'digit_model':{'mode':meta['mode'], 'centroids':centers, 'weights':weights},
            'head_path':head_path, 'head_sha256':token._sha(head_path)}


def _verify_immutable(source):
    underlying = source['underlying']
    shared._source_verify_after(underlying)
    if (token._sha(source['report_path']) != source['report_sha256'] or
        token._sha(source['head_path']) != source['head_sha256']):
        raise ValueError('GAMER_S_FROZEN_REPAIR_SOURCE_MUTATED')
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    original = underlying['composition']['upstream']['precision']['previous']['source']
    if stress._verify_snapshot(original['snapshot'],compose.STEP) != source['report']['frozen_lora_sha256']:
        raise ValueError('GAMER_S_FROZEN_LORA_CHANGED')


def _model_inputs(source):
    original = source['underlying']['composition']['upstream']['precision']['previous']['source']
    return original['origin']['meta']['lineage']['model_id'], original['snapshot'], original['origin']['meta']['lineage']['configuration']


def _s_span(tok, case):
    """Return ordered, one-character token positions; NEVER digit labels."""
    text = case.prompt
    a,b = token._spans(text)['payoffs'][1]
    encoding = tok(text, add_special_tokens=True, truncation=False,
                   return_offsets_mapping=True)
    ids = np.asarray(encoding['input_ids'], dtype=np.int64)
    offsets = np.asarray(encoding['offset_mapping'], dtype=int)
    if offsets.shape != (len(ids),2) or not len(ids):
        raise ValueError('GAMER_S_BAD_TOKEN_OFFSETS')
    choices = [i for i,(lo,hi) in enumerate(offsets) if hi>lo and hi>a and lo<b]
    if (not 1 <= len(choices) <= SLOTS or len(choices) != b-a or
        [(int(offsets[i,0]),int(offsets[i,1])) for i in choices] !=
            [(a+j,a+j+1) for j in range(b-a)]):
        raise ValueError(f'GAMER_S_UNSUPPORTED_S_DIGIT_TOKENIZATION: {case.id}')
    return ids, choices


def _decode_component_vectors(groups, model, *, bound=999, name='S'):
    """No ground truth, evaluation token text, or evaluation token ID used."""
    if not groups:
        return []
    if any(len(group)<1 or len(group)>SLOTS for group in groups):
        raise ValueError('GAMER_S_COMPONENT_LENGTH_INVALID')
    vectors = np.stack([v for group in groups for v in group]).astype(np.float32)
    predicted = shared._digit_infer(vectors, model)
    out=[];cursor=0
    for group in groups:
        digits=predicted[cursor:cursor+len(group)];cursor+=len(group)
        if any(d is None for d in digits) or digits[0] == '0':
            out.append(None)
        else:
            integer=int(''.join(digits))
            out.append(integer if 0 < integer <= bound else None)
    if cursor != len(predicted):
        raise ValueError('GAMER_S_DIGIT_BATCH_CORRUPTION')
    return out


def _embed_s(model, tok, deck, digit_model, device):
    """Read S through actual frozen embedding weights; no LoRA forward needed."""
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    core=oracle._backbone_parts(model)
    specs=[_s_span(tok,case) for case in deck]
    # The actual tokenizer IDs select embedding rows, but do not predict
    # numbers; the frozen ten-class embedding readout makes that prediction.
    ids=[int(spec[0][j]) for spec in specs for j in spec[1]]
    with torch.inference_mode():
        x=core.embed_tokens(torch.tensor(ids, device=device, dtype=torch.long))
    features=x.float().cpu().numpy()
    groups=[]; k=0
    for _,indexes in specs:
        groups.append(features[k:k+len(indexes)]);k+=len(indexes)
    if k!=len(features):raise ValueError('GAMER_S_EMBEDDING_ALIGNMENT_DRIFT')
    return _decode_component_vectors(groups,digit_model)


def _s_baseline(source, deck):
    saved=shared._payoff_replay(source['underlying'])
    if any(len(saved[p])!=696 for p in payoff.PAYOFFS):
        raise ValueError('GAMER_S_FROZEN_PAYOFF_REPLAY_LENGTH_WRONG')
    return saved


def _repair_score(source, corrected_s, baseline):
    deck,tags,panels,q=positional._deck(source['underlying']['composition'])
    if len(deck)!=1216 or len(corrected_s)!=696:
        raise ValueError('GAMER_S_REPAIR_DECK_LENGTH_CHANGED')
    frames=positional._load_features(source['underlying']['composition'],len(deck))
    rows=shared._aligned_trace(source['underlying'],deck,panels)
    qparts,_=shared._infer(rows,frames,source['digit_model'])
    recovered=compose._assemble(qparts['numerator'],qparts['denominator'])
    if any(recovered[i]!=q[i] for i in range(len(q))):
        raise ValueError('GAMER_S_UPSTREAM_Q_REPLAY_CHANGED')
    patched={p:np.asarray(baseline[p],dtype=np.int64).copy() for p in payoff.PAYOFFS}
    if any(v is None for v in corrected_s):
        # Fail closed; do not fill unsupported values from oracle.
        patched['S']=np.asarray([v if v is not None else -999999
                                 for v in corrected_s], dtype=np.int64)
    else:
        patched['S']=np.asarray(corrected_s,dtype=np.int64)
    metrics,triples=shared._score_exact(deck,tags,panels,q,recovered,patched)
    old_targets=payoff._integer_targets(operand._targets(deck[:696]))['S']
    hold=panels['heldout_independent']
    old_err=[int(i) for i in hold if int(baseline['S'][i]) != int(old_targets[i])]
    closed=[int(i) for i in old_err if corrected_s[i] == int(old_targets[i])]
    s_exact={}
    for k, ids in panels.items():
        if k not in ('train','legacy_dev','heldout_independent',
                     'heldout_zero_anchor','challenge_independent','challenge_zero_anchor'):
            continue
        # The 'train' partition also contains 432 *new q-only* examples after
        # index 696; the S repair runs only against the original 696 payoff
        # captures. Do not accidentally index the later composition deck.
        observed=[int(i) for i in ids if int(i)<696]
        s_exact[k]={'cases':len(observed),
             'exact':sum(corrected_s[i] == int(old_targets[i]) for i in observed),
             'invalid':sum(corrected_s[i] is None for i in observed)}
    # The original S failures were the two renderings of the same unseen 109.
    passed=(len(old_err)==2 and len(closed)==2 and len(corrected_s)==696 and
            all(v['exact']==v['cases'] and v['invalid']==0 for v in s_exact.values()) and
            all(m['exact_fraction_margin']==1. and m['all_payoffs_exact']==1.
                for m in metrics.values()) and
            all(t['strict_exact_full_triples']==8 for t in triples.values()))
    return {'prior_s_failure_indices':old_err,'repaired_s_failure_indices':closed,
            's_component_metrics':s_exact,'recomposition':metrics,
            'zero_anchor_triples':triples,'passed':bool(passed)}, recovered


def _operational_deck():
    """Fresh prompts and S-only edits; no fitting or checkpoint selection."""
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    from tools import nanojev_two_game_spine_smoke as spine
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    renderers=(('spine_belief',prompts._spine_belief_prompt),
               ('game_prose_belief',prompts._game_prose_belief_prompt))
    deck=[]; tags=[]
    for index,den in enumerate(FRESH_DENOMINATORS):
        b=den//2; a=den-b
        # R/T/P are in the old support; S=109 is the previously unsupported
        # value. Every center cancels exactly at q=b/den.
        stage=spine.Stage(Fraction(134),Fraction(109),Fraction(134-a),Fraction(109+b))
        center=Fraction(b,den)
        delta=Fraction(1,300*den)
        if stage.margin(center) != 0:
            raise ValueError('GAMER_S_OPERATIONAL_BAD_EXACT_ZERO_CONSTRUCTION')
        base=f'freshs.{index:02d}'
        for edit,q in (('negative',center+delta),('zero',center),
                       ('positive',center-delta)):
            for surface,render in renderers:
                ident=f'{base}.{edit}.{surface}'
                target=stage.margin(q)
                expected={'negative':-1,'zero':0,'positive':1}[edit]
                if (target>0)-(target<0)!=expected:
                    raise ValueError('GAMER_S_OPERATIONAL_BAD_SIGN_TRIPLE')
                deck.append(oracle.Case(ident,'test',base,surface,render(stage,q),
                         target,'fresh_zero_anchor',target))
                tags.append({'panel':'zero_anchor','profile':base,'surface':surface,
                             'edit':edit,'id':ident})
        for svalue in S_INTERVENTIONS:
            altered=spine.Stage(stage.R,Fraction(svalue),stage.T,stage.P)
            for surface,render in renderers:
                ident=f'{base}.s_edit_{svalue}.{surface}'
                target=altered.margin(center)
                deck.append(oracle.Case(ident,'test',base,surface,render(altered,center),
                          target,'fresh_s_only',target))
                tags.append({'panel':'s_only','profile':base,'surface':surface,
                             'edit':f's_{svalue}','id':ident})
    if len(deck)!=len(FRESH_DENOMINATORS)*(3+len(S_INTERVENTIONS))*2:
        raise ValueError('GAMER_S_OPERATIONAL_DECK_INCOMPLETE')
    return deck,tags


def _live_capture(model,tok,deck, cfg, device, batch_size, *, capture_wide=False):
    """Real model(**inputs) captures embedding + layer-9, not cached features."""
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length
    if batch_size<1: raise ValueError('GAMER_S_BAD_BATCH_SIZE')
    q_aligned=compose._aligned_components(tok,deck)
    s_aligned=[_s_span(tok,c) for c in deck]
    payoff_masks=operand._aligned_token_spans(tok,deck)
    core=oracle._backbone_parts(model)
    if len(core.layers)<=9:raise ValueError('GAMER_S_OPERATIONAL_MISSING_LAYER9')
    model.eval()
    digit_groups={'numerator':[],'denominator':[],'S':[]}
    views={key:[] for key in deconf.VIEWS}
    wide_features=[]
    for start in range(0,len(deck),batch_size):
        chunk=deck[start:start+batch_size]
        if any(len(tok.encode(c.prompt,add_special_tokens=True)) > cfg['max_length'] for c in chunk):
            raise ValueError('GAMER_S_OPERATIONAL_PROMPT_TOO_LONG')
        batch=tok([c.prompt for c in chunk], padding=True, truncation=True,
                  max_length=cfg['max_length'], return_tensors='pt')
        batch={k:v.to(device) for k,v in batch.items()}
        lengths=[int(v) for v in batch['attention_mask'].sum(-1).tolist()]
        observed=defaultdict(list);hooks=[]
        for stage,module in (('embedding',core.embed_tokens),('layer09',core.layers[9])):
            def collect(_module,_args,output,who=stage):
                arr=output[0] if isinstance(output,(list,tuple)) else output
                if not isinstance(arr,torch.Tensor) or arr.ndim!=3:
                    raise ValueError('GAMER_S_OPERATIONAL_CAPTURE_BAD_SHAPE')
                observed[who].append(arr.detach())
            hooks.append(module.register_forward_hook(collect))
        try:
            with torch.inference_mode():
                result=model(**batch,output_hidden_states=capture_wide,return_dict=True,logits_to_keep=1)
        finally:
            for handle in hooks:handle.remove()
        if len(observed['embedding'])!=1 or len(observed['layer09'])!=1:
            raise ValueError('GAMER_S_OPERATIONAL_HOOKS_INCOMPLETE')
        if capture_wide:
            if (not hasattr(result,'hidden_states') or not result.hidden_states or
                result.hidden_states[-1].ndim!=3):
                raise ValueError('GAMER_SHADOW_MISSING_PRODUCTION_FINAL_HIDDEN_STATE')
            last=result.hidden_states[-1]
            for j, n in enumerate(lengths):
                wide_features.append(last[j,n-1].float().cpu().numpy())
        for local,n in enumerate(lengths):
            i=start+local; spec=q_aligned[i]; sm=s_aligned[i]
            if n!=spec['length'] or n!=len(payoff_masks[i]['ids']) or n!=len(sm[0]):
                raise ValueError('GAMER_S_OPERATIONAL_ALIGNMENT_LENGTH_CHANGED')
            embed=observed['embedding'][0][local,:n].float().cpu().numpy()
            layer=observed['layer09'][0][local,:n].float().cpu().numpy()
            for part,indices in zip(('numerator','denominator'),spec['indices']):
                digit_groups[part].append(embed[indices])
            digit_groups['S'].append(embed[sm[1]])
            pos=np.asarray(positions_for_length(n,100,cfg['curvature']),dtype=int)
            projections=operand._feature_from_hidden(layer,payoff_masks[i],pos)
            projections['lexical_numeric']=operand._lexical(payoff_masks[i])
            for key in views:
                views[key].append(projections[key])
        if start==0 or start%40<batch_size:
            print(f'GAMER_S_LIVE_CAPTURE {start}/{len(deck)}',flush=True)
    saved={k:np.stack(v).astype(np.float32) for k,v in views.items()}
    if capture_wide:
        wide=np.stack(wide_features).astype(np.float32)
        if len(wide)!=len(deck) or not np.isfinite(wide).all():
            raise ValueError('GAMER_SHADOW_NONFINITE_WIDE_FEATURES')
        return digit_groups,saved,wide
    return digit_groups,saved


def _predict_live_groups(groups,model):
    n=_decode_component_vectors(groups['numerator'],model,bound=999999,name='numerator')
    d=_decode_component_vectors(groups['denominator'],model,bound=999999,name='denominator')
    s=_decode_component_vectors(groups['S'],model,bound=999,name='S')
    return compose._assemble(n,d),s


def _new_checkpoint_predictions(source, live_views):
    """Append new live rows to archived training features before replay.

    Original checkpoint weights, coefficients, class support and train indices
    are untouched; the append makes the saved train-basis fingerprint check
    verifiable on the full matrix. Only R/T/P heads are used here.
    """
    upstream=source['underlying']['composition']['upstream']
    precision=upstream['precision']
    report=precision['report']
    archive=precision['archive']
    if token._sha(archive)!=report['fresh_feature_archive']['sha256']:
        raise ValueError('GAMER_S_OPERATIONAL_FROZEN_PAYOFF_ARCHIVE_CHANGED')
    out={}
    with np.load(archive,allow_pickle=False) as old:
        for name in ('R','T','P'):
            sel=report['payoffs'][name]['selection']
            view=sel['view']
            history=np.asarray(old['layer_09__'+view],dtype=np.float32)
            new=np.asarray(live_views[view],dtype=np.float32)
            if history.shape[0]!=600 or new.shape[1:]!=history.shape[1:]:
                raise ValueError('GAMER_S_OPERATIONAL_PAYOFF_VIEW_SHAPE_CHANGED')
            checkpoint=Path(report['diagnostic_checkpoints'][name]['path'])
            if token._sha(checkpoint)!=report['diagnostic_checkpoints'][name]['sha256']:
                raise ValueError('GAMER_S_OPERATIONAL_FROZEN_PAYOFF_CHECKPOINT_MUTATED')
            joined=np.concatenate([history,new],axis=0)
            predictions=payoff._replay_checkpoint(checkpoint,joined,
                                 expected_source_sha=report['deconfounded_report_sha256'])
            out[name]=np.asarray(predictions[name][600:],dtype=np.int64)
    return out


def _score_live(deck,tags,qhat,shat,payoffs):
    """Strict fractions/signs; oracle labels used ONLY after prediction."""
    if not (len(deck)==len(tags)==len(qhat)==len(shat)):
        raise ValueError('GAMER_S_OPERATIONAL_SCORING_LENGTH_CHANGED')
    qs=exact._exact_q(deck)
    exact_payoff=[]; numeric=[]; fraction_exact=[]; s_exact=[]; case_records=[]
    for i,case in enumerate(deck):
        truth={k:int(v) for k,v in zip(payoff.PAYOFFS,exact._parse_payoffs(case.prompt))}
        pred={'R':int(payoffs['R'][i]),'S':shat[i],
              'T':int(payoffs['T'][i]),'P':int(payoffs['P'][i])}
        pay_correct=shat[i] is not None and all(pred[k]==truth[k] for k in payoff.PAYOFFS)
        exact_payoff.append(pay_correct)
        s_exact.append(shat[i]==truth['S'])
        fraction_exact.append(qhat[i]==qs[i])
        if qhat[i] is None or shat[i] is None:
            margin=None
        else:
            margin=qhat[i]*(pred['T']-pred['R'])+(1-qhat[i])*(pred['P']-pred['S'])
        numeric.append(margin)
        case_records.append({'id':case.id,'panel':tags[i]['panel'],
          'q_exact':bool(fraction_exact[-1]), 's_exact':bool(s_exact[-1]),
          'payoffs_exact':bool(pay_correct), 'margin_exact':margin==case.target,
          'margin_sign_correct':margin is not None and
             (margin>0)-(margin<0)==(case.target>0)-(case.target<0)})
    zeros=[i for i,t in enumerate(tags) if t['panel']=='zero_anchor']
    triples=exact._triples(tags,zeros,qs,qhat,numeric,exact_payoff,
                          expected_groups=2*len(FRESH_DENOMINATORS))
    s_edges=[i for i,t in enumerate(tags) if t['panel']=='s_only']
    # Compare the counterfactuals with an identical (R,T,P,q) baseline.
    base={(t['profile'],t['surface']):i for i,t in enumerate(tags)
          if t['panel']=='zero_anchor' and t['edit']=='zero'}
    deltas=[]
    for i in s_edges:
        j=base[(tags[i]['profile'],tags[i]['surface'])]
        if qhat[i] is None or qhat[j] is None or shat[i] is None or shat[j] is None:
            deltas.append(False)
        else:
            actual=deck[i].target-deck[j].target
            predicted=numeric[i]-numeric[j]
            deltas.append(predicted==actual and qhat[i]==qhat[j] and
                          all(payoffs[k][i]==payoffs[k][j] for k in ('R','T','P')))
    total=len(deck)
    success=(all(x['margin_exact'] for x in case_records) and
        all(fraction_exact) and all(s_exact) and all(exact_payoff) and
        triples['accepted'] and all(deltas))
    return {'cases':total,'q_exact':sum(fraction_exact),'s_exact':sum(s_exact),
        'all_payoffs_exact':sum(exact_payoff),
        'exact_margin_cases':sum(x['margin_exact'] for x in case_records),
        'strict_zero_triples':triples,
        's_only_counterfactuals':{'cases':len(deltas),'exact':sum(deltas)},
        'passed':bool(success),
        'failures':[r for r in case_records if not
            (r['q_exact'] and r['payoffs_exact'] and r['margin_exact'])]}



def _save_s_checkpoint(path, source):
    """Create an S-specific, replayable alias of the trained shared digit head.

    S's previously unsupported 109 is NEVER included in training/selection;
    this only copies the ten TRAIN-only digit identities, byte for byte.
    """
    model=source['digit_model']
    metadata={'schema':SCHEMA,'component':'S',
              'readout':'SHARED_TRAIN_ONLY_DECIMAL_DIGIT_IDENTITY',
              'source_q_head_sha256':source['head_sha256'],
              'source_shared_sha256':source['report_sha256'],
              'mode':model['mode'],
              'centroids_sha256':hashlib.sha256(model['centroids'].tobytes()).hexdigest()}
    np.savez_compressed(path,centroids=model['centroids'],
        weights=model['weights'],metadata=np.asarray(json.dumps(metadata,sort_keys=True)))
    with np.load(path,allow_pickle=False) as chk:
        restored=json.loads(str(chk['metadata'].item()))
        centers=chk['centroids'].copy();weights=chk['weights'].copy()
    if (restored!=metadata or not np.array_equal(centers,model['centroids']) or
        not np.array_equal(weights,model['weights']) or
        hashlib.sha256(centers.tobytes()).hexdigest()!=metadata['centroids_sha256']):
        raise ValueError('GAMER_S_SAVED_DIGIT_HEAD_DIFFERS_FROM_SOURCE')
    return {'path':str(path),'sha256':token._sha(path),
            'mode':metadata['mode'],'component':'S',
            'trained_on_s_heldout':False,'replay_params_identical':True,
            'source_q_head_sha256':source['head_sha256']}

def run(args):
    if args.capture_batch < 1:raise ValueError('GAMER_S_CAPTURE_BATCH_MUST_BE_POSITIVE')
    source=_source(args.shared)
    upstream=source['underlying']['composition']['upstream']
    blocked=[source['root'],source['underlying']['root'],
             source['underlying']['composition']['root'],upstream['root'],
             upstream['precision']['root']]
    out=c0sign._output(args.output,blocked)
    if out.exists():raise FileExistsError('GAMER_S_ACCEPTANCE_OUTPUT_EXISTS')
    _verify_immutable(source)
    deck,tags,panels,q=positional._deck(source['underlying']['composition'])
    model_id,snapshot,cfg=_model_inputs(source)
    print('GAMER S CLOSURE: frozen LoRA 3000, S-only shared digit readout; '
          'all original payoffs and C0 immutable',flush=True)
    _,tok,model=gamer.model_components(model_id,args.device,adapter_dir=snapshot)
    try:
        model.eval()
        print('  embedding-only S repair on 696 protected-old/challenge prompts',flush=True)
        repaired_s=_embed_s(model,tok,deck[:696],source['digit_model'],args.device)
        baseline=_s_baseline(source,deck[:696])
        repair,_=_repair_score(source,repaired_s,baseline)
        print('  old heldout S failures closed:',len(repair['repaired_s_failure_indices']),
              '/',len(repair['prior_s_failure_indices']),flush=True)
        print('  real frozen-model forward pass on fresh operational cases',flush=True)
        fresh,fresh_tags=_operational_deck()
        groups,views=_live_capture(model,tok,fresh,cfg,args.device,args.capture_batch)
        fresh_q,fresh_s=_predict_live_groups(groups,source['digit_model'])
        unchanged_payoffs=_new_checkpoint_predictions(source,views)
        operational=_score_live(fresh,fresh_tags,fresh_q,fresh_s,unchanged_payoffs)
    finally:
        del model,tok
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()
        except ImportError:
            pass
    _verify_immutable(source)
    out.mkdir(parents=True,exist_ok=False)
    s_checkpoint=_save_s_checkpoint(out/'s-compositional-diagnostic.npz',source)
    result={'schema':SCHEMA,'status':'COMPLETE_BOUNDED_S_REPAIR_AND_LIVE_ACCEPTANCE',
      'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
      'source_shared_report':str(source['report_path']),
      'source_shared_sha256':source['report_sha256'],
      'source_q_head_sha256':source['head_sha256'],
      'frozen_lora_step':compose.STEP,
      'frozen_lora_sha256':source['report']['frozen_lora_sha256'],
      'no_lora_training':True,'no_c0_training':True,
      'no_payoff_head_training':True,'no_zero_detector_mutation':True,
      's_method':'REUSE_TRAIN_FITTED_SHARED_DIGIT_HEAD_ON_ACTUAL_S_EMBEDDING',
      's_diagnostic_checkpoint':s_checkpoint,
      'repair':repair,'fresh_operational_acceptance':operational,
      'gate':{'old_s_errors_closed':bool(repair['passed']),
              'fresh_live_acceptance_passed':bool(operational['passed']),
              'bounded_candidate_passed':bool(repair['passed'] and operational['passed']),
              'production_to_numerical_integration':'NOT_IMPLEMENTED_OR_VERIFIED',
              'promotion':'BLOCKED_DIAGNOSTIC_ONLY'},
      'limitations':('The live stage exercises raw prompts, actual frozen model forward '
         'hooks, frozen diagnostic operand heads and deterministic exact arithmetic. '
         'It does not route through or alter the production to_numerical head; '
         'production parity and unrestricted generalization are not certified.')}
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER S CLOSURE COMPLETE:',out/'final_report.json',flush=True)
    print('  S repair:', 'PASS' if repair['passed'] else 'FAIL',
          ' fresh live:', 'PASS' if operational['passed'] else 'FAIL',
          ' production promotion: BLOCKED',flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--shared',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cuda','cpu','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    return run(p.parse_args(argv))


if __name__=='__main__':
    main()
