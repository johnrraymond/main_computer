#!/usr/bin/env python3
"""GAMER V1: shared position-independent digit repair, no model retraining.

Reuses the 1,216 attested embedding-slot captures and positional trace.
Fits ten shared digit identities from TRAIN component token embeddings only;
selects the readout on the fresh unseen-denominator DEV set; then evaluates
protected heldout/challenge cases exactly once. Inference consumes only the
frozen embedding vectors and the tokenizer's offset-derived token/slot layout,
never the heldout token spellings, IDs, numerals, oracle q or payoff labels.

Four frozen payoff checkpoints are replayed on saved layer-9 feature archives
for *actual* recovered-operand arithmetic and strict zero-line triples.
Production C0, LoRA, payoff checkpoints and zero detector are never changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_positional_digit_audit as positional
from tools import nanojev_gamer_v1_compositional_belief_lab as compose
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
from tools import nanojev_gamer_v1_deconfounded_operand_audit as deconf
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.frozen-shared-position-independent-digit.v1'
DIGITS = tuple(str(x) for x in range(10))
PARTS = positional.PARTS
SLOTS = compose.SLOTS
HIDDEN = 1024
TOL = 1e-4


def _source(folder):
    root = Path(folder).resolve()
    rp = root / 'final_report.json'
    r = json.loads(rp.read_text(encoding='utf-8'))
    if (r.get('schema') != positional.SCHEMA or
        r.get('status') != 'COMPLETE_READ_ONLY_POSITIONAL_DIAGNOSTIC' or
        r.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        r.get('cases') != 1216 or
        any(r.get(k) is not True for k in ('no_lora_training', 'no_c0_training',
                  'no_head_refitting', 'no_payoff_head_training', 'no_zero_detector_mutation'))):
        raise ValueError('GAMER_SHARED_REQUIRES_COMPLETE_POSITIONAL_AUDIT')
    src = positional._load_source(Path(r['source_composition_report']).parent)
    if (token._sha(src['report_path']) != r['source_composition_sha256'] or
        token._sha(src['archive']) != r['source_selected_features_sha256'] or
        r['frozen_lora_sha256'] != src['report']['frozen_lora_sha256'] or
        src['report']['combined_deck_sha256'] != r['combined_deck_sha256']):
        raise ValueError('GAMER_SHARED_UPSTREAM_LINEAGE_CHANGED')
    trace = Path(r['trace_files']['all']['path']).resolve()
    failure = Path(r['trace_files']['failures']['path']).resolve()
    for path, rec in ((trace, r['trace_files']['all']),
                      (failure, r['trace_files']['failures'])):
        if not path.is_file() or not path.is_relative_to(root) or token._sha(path) != rec['sha256']:
            raise ValueError('GAMER_SHARED_POSITIONAL_TRACE_MUTATED')
    return {'root': root, 'report_path': rp, 'report_sha256': token._sha(rp),
            'report': r, 'composition': src, 'trace': trace, 'failure': failure}


def _aligned_trace(source, deck, panels):
    """Use stored tokenizer offsets as *routing* metadata, not as digit labels."""
    rows = [json.loads(line) for line in source['trace'].read_text(encoding='utf-8').splitlines()]
    if len(rows) != len(deck):
        raise ValueError('GAMER_SHARED_TRACE_CASE_COUNT_CHANGED')
    memberships = {int(i): name for name, idx in panels.items() for i in idx}
    if len(memberships) != len(deck):
        raise ValueError('GAMER_SHARED_PANEL_PARTITION_INVALID')
    for i, (row, case) in enumerate(zip(rows, deck)):
        if (row['index'] != i or row['case_id'] != case.id or
            row['panel'] != memberships[i]):
            raise ValueError('GAMER_SHARED_TRACE_CASE_ID_OR_PANEL_CHANGED')
        for part in PARTS:
            piece = row['components'][part]
            tokens = piece['tokens']
            if not 1 <= len(tokens) <= SLOTS:
                raise ValueError('GAMER_SHARED_UNSUPPORTED_TOKEN_SPAN')
            slots = [int(t['slot']) for t in tokens]
            spans = [t['component_char_span'] for t in tokens]
            if (slots != list(range(SLOTS-len(tokens), SLOTS)) or
                any(len(s) != 2 or s[1] != s[0]+1 for s in spans) or
                spans != [[j, j+1] for j in range(len(tokens))]):
                # No decoding using an ambiguous/multicharacter token: its
                # digit identity cannot be assumed from the slot alone.
                raise ValueError('GAMER_SHARED_EXPECTS_ONE_DIGIT_PER_TOKEN')
    return rows


def _training_bank(rows, frames, train):
    """Fit exactly ten TRAIN-labeled input-embedding centroids shared by parts."""
    sums = np.zeros((10,HIDDEN), dtype=np.float64)
    counts = np.zeros(10,dtype=np.int64)
    vectors = {digit: [] for digit in DIGITS}
    for raw in train:
        i = int(raw)
        for part in PARTS:
            slots = frames[part][i].reshape(SLOTS,HIDDEN)
            for t in rows[i]['components'][part]['tokens']:
                # Token spelling is a label ONLY in the train partition.
                label = t['text']
                if label not in DIGITS:
                    raise ValueError('GAMER_SHARED_TRAIN_TOKEN_NOT_ONE_ASCII_DIGIT')
                vec = slots[int(t['slot'])]
                cls = int(label)
                counts[cls] += 1
                sums[cls] += vec.astype(np.float64)
                vectors[label].append(vec)
    if np.any(counts == 0):
        raise ValueError('GAMER_SHARED_TRAIN_DIGIT_COVERAGE_INCOMPLETE')
    means = np.asarray(sums / counts[:,None],dtype=np.float32)
    if not np.isfinite(means).all():
        raise ValueError('GAMER_SHARED_NONFINITE_TRAIN_EMBEDDING')
    # The embedding layer is pre-transformer and must not vary by position.
    # Fail closed if the proposed identity-based repair does not apply.
    largest = max(float(np.max(np.abs(np.stack(vectors[d])-means[int(d)])))
                  for d in DIGITS)
    if largest > TOL:
        raise ValueError('GAMER_SHARED_TRAIN_EMBEDDING_NOT_POSITION_INVARIANT')
    return {'centroids':means,'counts':counts,'maximum_train_deviation':largest}


def _candidate(bank, mode):
    centers = bank['centroids'].astype(np.float64)
    if mode == 'exact_centroid':
        return {'mode':mode,'centroids':bank['centroids'].copy(),
                'weights':np.zeros((HIDDEN,10),dtype=np.float64)}
    if mode != 'shared_linear':
        raise ValueError('GAMER_SHARED_UNKNOWN_DECODER')
    # Ten train-only class centroids, one head shared across all positions.
    # This is a supervised linear readout, not an exact token-ID lookup.
    gram = centers @ centers.T
    lam = 1e-3
    coef = np.linalg.solve(gram + lam*np.eye(10),np.eye(10))
    return {'mode':mode,'centroids':bank['centroids'].copy(),
            'weights':centers.T@coef}


def _digit_infer(query, decoder):
    """No label, token ID or evaluation prompt text is read here."""
    x = np.asarray(query,dtype=np.float32)
    centers = decoder['centroids']
    if x.ndim != 2 or x.shape[1] != HIDDEN or centers.shape != (10,HIDDEN):
        raise ValueError('GAMER_SHARED_DECODER_SHAPE_INVALID')
    if not np.isfinite(x).all() or not np.isfinite(centers).all():
        raise ValueError('GAMER_SHARED_NONFINITE_DIGIT_FEATURE')
    if decoder['mode'] == 'exact_centroid':
        # Avoid an N x 10 x 1024 temporary for the full 1,216-case archive.
        a=x.astype(np.float64)
        b=centers.astype(np.float64)
        dist=(np.sum(a*a,axis=1)[:,None]+
              np.sum(b*b,axis=1)[None,:]-2*(a@b.T))
        cls=np.argmin(dist,axis=1)
        ok=np.max(np.abs(x-centers[cls]),axis=1) <= TOL
    elif decoder['mode'] == 'shared_linear':
        logits = x.astype(np.float64)@decoder['weights']
        cls = np.argmax(logits,axis=1)
        # Fail closed when a frozen input digit is not covered by TRAIN.
        ok = np.max(np.abs(x-centers[cls]),axis=1) <= TOL
    else:
        raise ValueError('GAMER_SHARED_DECODER_UNKNOWN_MODE')
    return [DIGITS[int(k)] if supported else None for k,supported in zip(cls,ok)]


def _infer(rows,frames,decoder):
    """Route token-order metadata to one shared readout; no eval strings/IDs."""
    result={part:[] for part in PARTS}
    unsupported=Counter()
    for part in PARTS:
        # Batch all token embeddings through exactly the same 10-digit head.
        x=[]; counts=[]
        for i,row in enumerate(rows):
            toks=row['components'][part]['tokens']
            counts.append(len(toks))
            slots=frames[part][i].reshape(SLOTS,HIDDEN)
            x.extend([slots[int(t['slot'])] for t in toks])
        predicted=_digit_infer(np.stack(x),decoder)
        cursor=0
        for i,width in enumerate(counts):
            digits=predicted[cursor:cursor+width]
            cursor+=width
            if any(v is None for v in digits):
                result[part].append(None)
                unsupported[part]+=1
            else:
                value=int(''.join(digits))
                # Source fractions are canonical; a leading zero or zero
                # numerator/denominator must not be silently normalized.
                if not 1 <= value < 10**compose.DIGITS or digits[0] == '0':
                    result[part].append(None)
                    unsupported[part]+=1
                else:
                    result[part].append(value)
        if cursor!=len(predicted):
            raise ValueError('GAMER_SHARED_BAD_BATCH_RECONSTRUCTION')
    return result,dict(unsupported)


def _metric(q,pred,panels):
    out={}
    for name,idx in panels.items():
        seq=[int(i) for i in idx]
        exact_count=sum(pred[i]==q[i] for i in seq)
        out[name]={'cases':len(seq),'exact_cases':exact_count,
                   'exact':exact_count/len(seq),
                   'invalid':sum(pred[i] is None for i in seq)}
    return out


def _choose(rows,frames,q,panels,bank):
    candidates={}; chosen=None
    for mode in ('shared_linear','exact_centroid'):
        model=_candidate(bank,mode)
        parts,unsupported=_infer(rows,frames,model)
        predicted=compose._assemble(parts['numerator'],parts['denominator'])
        scores=_metric(q,predicted,{'fresh_dev':panels['fresh_dev']})['fresh_dev']
        candidates[mode]={'development':scores,'unsupported_parts':unsupported}
        rank=(scores['exact'], -scores['invalid'], int(mode=='shared_linear'))
        if chosen is None or rank>chosen['rank']:
            chosen={'rank':rank,'model':model,'parts':parts,'q':predicted,
                    'unsupported':unsupported}
    return chosen,candidates


def _save_and_verify(path, fit, bank, source, rows, frames):
    meta={'schema':SCHEMA,'source_positional_sha256':source['report_sha256'],
          'source_composition_sha256':source['report']['source_composition_sha256'],
          'source_deck_sha256':source['report']['combined_deck_sha256'],
          'source_trace_sha256':source['report']['trace_files']['all']['sha256'],
          'mode':fit['model']['mode'],
          'train_counts':bank['counts'].tolist(),
          'centroids_sha256':hashlib.sha256(bank['centroids'].tobytes()).hexdigest()}
    np.savez_compressed(path,centroids=fit['model']['centroids'],
        weights=fit['model']['weights'],metadata=np.array(json.dumps(meta,sort_keys=True)))
    with np.load(path,allow_pickle=False) as z:
        got=json.loads(str(z['metadata'].item()))
        centers=z['centroids'].copy(); w=z['weights'].copy()
    if (got!=meta or centers.dtype!=np.float32 or w.shape!=(HIDDEN,10) or
        not np.array_equal(centers,bank['centroids']) or
        hashlib.sha256(centers.tobytes()).hexdigest()!=meta['centroids_sha256']):
        raise ValueError('GAMER_SHARED_SAVED_HEAD_LINEAGE_INVALID')
    model={'mode':got['mode'],'centroids':centers,'weights':w}
    replay,_=_infer(rows,frames,model)
    if replay!=fit['parts']:
        raise ValueError('GAMER_SHARED_HEAD_REPLAY_DIFFERENT')
    return {'path':str(path),'sha256':token._sha(path),
            'replay_matches_selected_predictions':True,'mode':meta['mode']}


def _payoff_replay(source):
    """Replay all four *saved* integer heads over the old+challenge 696 cases."""
    composition=source['composition']
    belief_source=composition['upstream']       # compose._source(belief)
    precision=belief_source['precision']        # exact._source(payoff)
    old_archive=precision['archive']
    challenge_archive=Path(belief_source['report']['challenge_feature_archive']['path'])
    old_sha=precision['report']['fresh_feature_archive']['sha256']
    challenge_sha=belief_source['report']['challenge_feature_archive']['sha256']
    if token._sha(old_archive)!=old_sha or token._sha(challenge_archive)!=challenge_sha:
        raise ValueError('GAMER_SHARED_FROZEN_PAYOFF_INPUT_ARCHIVES_CHANGED')
    all_views={}
    with np.load(old_archive,allow_pickle=False) as old, np.load(challenge_archive,allow_pickle=False) as new:
        for key in deconf.VIEWS:
            x=old['layer_09__'+key]
            y=new[key]
            if (x.dtype!=np.float32 or y.dtype!=np.float32 or
                x.shape[0]!=600 or y.shape[0]!=96):
                raise ValueError('GAMER_SHARED_BAD_FROZEN_PAYOFF_FEATURES')
            all_views[key]=np.concatenate([x,y])
    report=precision['report']
    payoffs={}
    for part in payoff.PAYOFFS:
        ck=Path(report['diagnostic_checkpoints'][part]['path'])
        view=report['payoffs'][part]['selection']['view']
        replay=payoff._replay_checkpoint(ck,all_views[view],
                  expected_source_sha=report['deconfounded_report_sha256'])
        payoffs[part]=replay[part]
    return payoffs


def _score_exact(deck,tags,panels,q,qpred,payoffs):
    # Only original 600 and protected challenge 96 have archived payoff views;
    # NO true payoff value is substituted for a neural prediction.
    count=696
    sub={k:v for k,v in panels.items() if k in (
        'heldout_independent','heldout_zero_anchor',
        'challenge_independent','challenge_zero_anchor')}
    if (any(np.any(v>=count) for v in sub.values()) or
        any(len(payoffs[k])!=count for k in payoff.PAYOFFS)):
        raise ValueError('GAMER_SHARED_PAYOFF_REPLAY_LENGTH_INVALID')
    report,margins,payoff_exact=exact._recompose(
        deck[:count],tags[:count],sub,q[:count],qpred[:count],payoffs)
    triple={}
    for short,panel in (('heldout','heldout_zero_anchor'),
                        ('challenge','challenge_zero_anchor')):
        triple[short]=exact._triples(tags,sub[panel],q,qpred,margins,payoff_exact,
                                     expected_groups=8)
    return report,triple


def _source_verify_after(source):
    positional.compose._verify_frozen(source['composition']['upstream'])
    if (token._sha(source['report_path'])!=source['report_sha256'] or
        token._sha(source['trace'])!=source['report']['trace_files']['all']['sha256'] or
        token._sha(source['failure'])!=source['report']['trace_files']['failures']['sha256'] or
        token._sha(source['composition']['archive'])!=
           source['composition']['report']['selected_feature_archive']['sha256']):
        raise ValueError('GAMER_SHARED_PROTECTED_SOURCE_MUTATED')


def run(args):
    source=_source(args.positional)
    blocked=[source['root'],source['composition']['root'],
             source['composition']['upstream']['root'],
             source['composition']['upstream']['precision']['root']]
    out=c0sign._output(args.output,blocked)
    if out.exists():
        raise FileExistsError('GAMER_SHARED_OUTPUT_ALREADY_EXISTS')
    deck,tags,panels,q=positional._deck(source['composition'])
    if len(deck)!=1216 or source['report']['cases']!=len(deck):
        raise ValueError('GAMER_SHARED_DECK_LENGTH_CHANGED')
    frames=positional._load_features(source['composition'],len(deck))
    rows=_aligned_trace(source,deck,panels)
    print('GAMER SHARED DIGIT REPAIR: 1216 cached float32 captures; frozen LoRA 3000; no GPU pass',flush=True)
    bank=_training_bank(rows,frames,panels['train'])
    print('  selecting shared 10-class digit readout using fresh development only',flush=True)
    chosen,candidates=_choose(rows,frames,q,panels,bank)
    qpred=chosen['q']
    scores=_metric(q,qpred,panels)
    for part in PARTS:
        before=source['composition']['report']['component_accuracy'][part]
        # Explain changes without ever adjusting the chosen head on a heldout label.
        if any(p not in before for p in ('challenge_zero_anchor','fresh_dev')):
            raise ValueError('GAMER_SHARED_MISSING_COMPOSITION_BASELINE')
    print('  replaying four frozen payoff checkpoints and strict 16 zero triples',flush=True)
    payoffs=_payoff_replay(source)
    composition,triples=_score_exact(deck,tags,panels,q,qpred,payoffs)
    paired={
        'heldout':exact._q_counterfactuals(deck,tags,q,qpred,panels['heldout_independent']),
        'challenge':exact._q_counterfactuals(deck,tags,q,qpred,panels['challenge_independent']),
        'fresh_dev':compose._counterfactual(deck,tags,q,qpred,panels['fresh_dev']),
        'fresh_dev_denominator_only':compose._denominator_counterfactual(
                       tags,q,qpred,panels['fresh_dev'])}
    zero_pass=all(triples[s]['strict_exact_full_triples']==8 for s in ('heldout','challenge'))
    full_exact=all(score['exact']==1.0 for score in scores.values())
    # Frozen payoff S remains imperfect on 2 old independent heldout cases.
    payoff_pass=all(v['all_payoffs_exact']==1. for v in composition.values())
    report={'schema':SCHEMA,'status':'COMPLETE_FROZEN_SHARED_DIGIT_REPAIR',
        'promotion':'BLOCKED_DIAGNOSTIC_ONLY','frozen_lora_step':compose.STEP,
        'frozen_lora_sha256':source['report']['frozen_lora_sha256'],
        'no_lora_training':True,'no_c0_training':True,
        'no_payoff_head_training':True,'no_zero_detector_mutation':True,
        'source_positional_report':str(source['report_path']),
        'source_positional_sha256':source['report_sha256'],
        'source_digit_trace_sha256':source['report']['trace_files']['all']['sha256'],
        'source_deck_sha256':source['report']['combined_deck_sha256'],
        'training_digit_observations':int(bank['counts'].sum()),
        'training_per_digit':dict(zip(DIGITS,bank['counts'].astype(int).tolist())),
        'maximum_train_embedding_deviation':bank['maximum_train_deviation'],
        'selection_policy':'TRAIN_DIGITS_ONLY_FRESH_DEV_SELECT_PROTECTED_HOLDOUT_ONCE',
        'candidates':candidates,'selected':chosen['model']['mode'],
        'fraction_accuracy':scores,'recomposition':composition,
        'zero_anchor_triples':triples,'counterfactuals':paired,
        'prior_component_error_cases':source['report']['failed_component_cases'],
        'unsupported_components':chosen['unsupported'],
        'gate':{'all_16_zero_triples_passed':bool(zero_pass),
             'all_1216_fraction_cases_exact':bool(full_exact),
             'all_four_payoffs_exact_all_protected_panels':bool(payoff_pass),
             'diagnostic_repair_passed':bool(zero_pass and full_exact),
             'production_promotion':'BLOCKED_DIAGNOSTIC_ONLY'},
        'limitations':('This is input-token identity decoding from frozen embeddings, '
             'not a proof of arithmetic in LoRA. Validation of a bounded '
             'archive is not a certification of unrestricted numerals. '
             'The earlier two unsupported S values remain a separate payoff defect.')}
    _source_verify_after(source)
    out.mkdir(parents=True,exist_ok=False)
    head=out/'shared-digit-diagnostic.npz'
    report['diagnostic_checkpoint']=_save_and_verify(
           head,chosen,bank,source,rows,frames)
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER SHARED DIGIT REPAIR COMPLETE:',out/'final_report.json',flush=True)
    print('  strict zero triples:',
          f"{triples['heldout']['strict_exact_full_triples']}/8 + "
          f"{triples['challenge']['strict_exact_full_triples']}/8",flush=True)
    print('  all 1216 fractions exact:',full_exact,flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--positional',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    return run(p.parse_args(argv))


if __name__=='__main__':
    main()
