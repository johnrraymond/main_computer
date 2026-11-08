#!/usr/bin/env python3
"""GAMER V1: trace frozen component digits through tokenizer, slots, and readout.

Read-only follow-up to the compositional-belief run. Replay its immutable
embedding-slot digit heads; use tokenizer offsets to document exactly where each
numeric character went. A separate train-only embedding-prototype control asks
whether the input embeddings can recover the *token spelling* without using
held-out token IDs, strings, or labels at inference. Neither control is proof
of arithmetic inside LoRA or an authorization to promote a head.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from tools import nanojev_gamer_v1_compositional_belief_lab as compose
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_c0_sign_head as c0sign
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.positional-digit-diagnostic.v1'
PARTS = ('numerator', 'denominator')
PROTOTYPE_TOL = 1e-4
_DIGITS = re.compile(r'[0-9]+\Z')


def _load_source(folder):
    folder = Path(folder).resolve()
    report_path = folder / 'final_report.json'
    r = json.loads(report_path.read_text(encoding='utf-8'))
    if (r.get('schema') != compose.SCHEMA or
        r.get('status') != 'COMPLETE_COMPOSITIONAL_BELIEF_DIAGNOSTIC' or
        r.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        r.get('frozen_lora_step') != compose.STEP or
        any(r.get(flag) is not True for flag in ('no_lora_training',
               'no_c0_training', 'no_payoff_head_training',
               'no_zero_detector_mutation'))):
        raise ValueError('GAMER_POSITIONAL_REQUIRES_FROZEN_COMPOSITION_REPORT')
    source = compose._source(Path(r['source_belief_report']).parent)
    if (token._sha(source['report_path']) != r['source_belief_report_sha256'] or
        source['report']['frozen_lora_sha256'] != r['frozen_lora_sha256']):
        raise ValueError('GAMER_POSITIONAL_UPSTREAM_LINEAGE_CHANGED')
    for part in PARTS:
        chosen = r['selected'][part]
        ck = r['checkpoints'][part]
        if (chosen['stage'] != 'embedding' or chosen['view'] != 'slots' or
            chosen['mode'] != 'digitwise' or ck['stage'] != 'embedding' or
            ck['view'] != 'slots' or ck['mode'] != 'digitwise' or
            ck['replay_matches'] is not True):
            raise ValueError('GAMER_POSITIONAL_EXPECTS_SELECTED_EMBEDDING_DIGIT_SLOTS')
        ckpath = Path(ck['path']).resolve()
        if not ckpath.is_relative_to(folder) or token._sha(ckpath) != ck['sha256']:
            raise ValueError('GAMER_POSITIONAL_CHECKPOINT_CHANGED')
    entry = r['selected_feature_archive']
    archived = Path(entry['path']).resolve()
    if (not archived.is_relative_to(folder) or
        token._sha(archived) != entry['sha256'] or
        entry['dtype'] != 'float32'):
        raise ValueError('GAMER_POSITIONAL_FEATURE_ARCHIVE_CHANGED')
    compose._verify_frozen(source)
    return {'root': folder, 'report': r, 'report_path': report_path,
            'report_sha256': token._sha(report_path),
            'upstream': source, 'archive': archived}


def _deck(source):
    old, tags, ix, challenge, challenge_tags = compose._heldout_split(source['upstream'])
    gen, gen_tags, gix = compose._denominator_deck()
    deck = old + challenge + gen
    if oracle.deck_hash(deck) != source['report']['combined_deck_sha256']:
        raise ValueError('GAMER_POSITIONAL_DECK_FINGERPRINT_CHANGED')
    oldlen, challengelen = len(old), len(challenge)
    train = np.concatenate((ix['train'], oldlen+challengelen+gix['train']))
    dev = oldlen+challengelen+gix['dev']
    indices = {
        'train': train,
        'legacy_dev': ix['dev'],
        'fresh_dev': dev,
        'heldout_independent': ix['heldout_independent'],
        'heldout_zero_anchor': ix['heldout_zero_anchor'],
        'challenge_independent': np.asarray([oldlen+i for i,t in enumerate(challenge_tags)
                                             if t['panel']=='independent'],dtype=int),
        'challenge_zero_anchor': np.asarray([oldlen+i for i,t in enumerate(challenge_tags)
                                             if t['panel']=='zero_anchor'],dtype=int),
    }
    if len(deck) != 1216 or len(train) != 720 or len(dev) != 160:
        raise ValueError('GAMER_POSITIONAL_DECK_LAYOUT_CHANGED')
    return deck, tags+challenge_tags+gen_tags, indices, exact._exact_q(deck)


def _load_features(source, n):
    with np.load(source['archive'],allow_pickle=False) as archive:
        if sorted(archive.files) != sorted(PARTS):
            raise ValueError('GAMER_POSITIONAL_ARCHIVE_KEYS_CHANGED')
        data = {k: archive[k].copy() for k in PARTS}
    if any(v.dtype != np.float32 or v.shape != (n, compose.SLOTS*1024) or not np.isfinite(v).all()
           for v in data.values()):
        raise ValueError('GAMER_POSITIONAL_ARCHIVE_SHAPE_CHANGED')
    return data


def _replay_scores(path, frame, expected_source_sha, deck_sha):
    """Exactly the existing frozen KRR readout, retaining raw digit logits."""
    with np.load(path,allow_pickle=False) as ck:
        meta = json.loads(str(ck['metadata'].item()))
        train_feature = np.asarray(ck['training_feature'],dtype=np.float32)
        tr = np.asarray(ck['training_indices'],dtype=int)
        alpha = np.asarray(ck['alpha'],dtype=np.float64)
        bias = np.asarray(ck['bias'],dtype=np.float64)
    if (meta['schema'] != compose.SCHEMA or
        meta['source_report_sha256'] != expected_source_sha or
        meta['deck_sha256'] != deck_sha or
        meta['stage'] != 'embedding' or meta['view'] != 'slots' or
        meta['target_spec']['mode'] != 'digitwise' or
        hashlib.sha256(train_feature.tobytes()).hexdigest()!=meta['training_feature_sha256'] or
        not np.array_equal(frame[tr],train_feature)):
        raise ValueError('GAMER_POSITIONAL_READOUT_LINEAGE_INVALID')
    cross,_,_=compose.operand._encode_train_map(frame,tr,meta['kernel'])
    logits = cross.astype(np.float64)@alpha + bias
    prediction = compose._decode(logits,meta['target_spec'])
    if logits.shape != (len(frame),compose.DIGITS*10):
        raise ValueError('GAMER_POSITIONAL_READOUT_LOGIT_SHAPE')
    return prediction, logits, meta


def _token_alignment(tokenizer, deck):
    """Use input text/token IDs solely for *diagnostic trace*, never head input."""
    align = compose._aligned_components(tokenizer,deck)
    out=[]
    for case, spec in zip(deck,align):
        record = tokenizer(case.prompt,add_special_tokens=True,truncation=False,
                           return_offsets_mapping=True)
        offsets = np.asarray(record['offset_mapping'],dtype=int)
        ids = [int(x) for x in record['input_ids']]
        if offsets.shape != (spec['length'],2) or len(ids) != spec['length']:
            raise ValueError('GAMER_POSITIONAL_TOKENIZER_NOT_DETERMINISTIC')
        bstart,bend=token._spans(case.prompt)['belief'][0]
        slash=case.prompt.index('/',bstart,bend)
        components={}
        for part, chosen, (a,b) in zip(PARTS,spec['indices'],
                                       ((bstart,slash),(slash+1,bend))):
            literal = case.prompt[a:b]
            if not _DIGITS.fullmatch(literal):
                raise ValueError('GAMER_POSITIONAL_NON_ASCII_DIGIT_COMPONENT')
            tokens=[]
            for j,idx in enumerate(chosen):
                lo,hi=(int(x) for x in offsets[int(idx)])
                piece=case.prompt[lo:hi]
                if not _DIGITS.fullmatch(piece):
                    raise ValueError('GAMER_POSITIONAL_TOKEN_NOT_DIGITS')
                tokens.append({'index':int(idx), 'token_id':ids[int(idx)],
                               'prompt_char_span':[lo,hi],
                               'component_char_span':[lo-a,hi-a],
                               'text':piece,
                               'slot':compose.SLOTS-len(chosen)+j})
            cover=''.join(p['text'] for p in tokens)
            if cover != literal:
                raise ValueError('GAMER_POSITIONAL_COMPONENT_SPAN_NOT_EXACT')
            charmap=[]
            for j,c in enumerate(literal):
                refs=[t for t in tokens if t['component_char_span'][0]<=j<t['component_char_span'][1]]
                if len(refs)!=1:raise ValueError('GAMER_POSITIONAL_CHAR_COVERAGE_NOT_ONE')
                t=refs[0]
                charmap.append({'digit_index':j,'digit_from_right':len(literal)-1-j,
                    'prompt_char_offset':a+j,'true_digit':c,
                    'token_index':t['index'],'token_id':t['token_id'],
                    'token_char_offset':j-t['component_char_span'][0],
                    'activation_slot':t['slot']})
            components[part]={'true_text':literal,'prompt_char_span':[a,b],
                              'tokens':tokens,'digits':charmap,
                              'token_pattern':[len(t['text']) for t in tokens]}
        out.append(components)
    return out


def _build_prototypes(align,frames,train):
    """Train-only token spelling and embedding prototypes.

    Token ID is allowed while assigning TRAIN labels. Evaluation prediction
    sees ONLY its embedding vector; token IDs/text cannot select prototypes.
    """
    result={}
    for part in PARTS:
        pool={}
        for ii in train:
            i=int(ii)
            slots=frames[part][i].reshape(compose.SLOTS,1024)
            for t in align[i][part]['tokens']:
                id_=t['token_id']; label=t['text'];vec=slots[t['slot']]
                if id_ in pool:
                    if pool[id_]['label']!=label:
                        raise ValueError('GAMER_POSITIONAL_INCONSISTENT_TRAIN_TOKEN_SPELLING')
                    pool[id_]['sum'] += vec.astype(np.float64)
                    pool[id_]['count'] += 1
                else:
                    pool[id_]={'label':label,'sum':vec.astype(np.float64).copy(),
                               'count':1}
        ids=sorted(pool)
        prototypes=np.stack([(pool[x]['sum']/pool[x]['count']).astype(np.float32)
                             for x in ids])
        result[part]={'labels':[pool[x]['label'] for x in ids],
                      'training_token_ids':ids,'vectors':prototypes,
                      'observations':sum(z['count'] for z in pool.values())}
    return result


def _prototype_decode(slot_vectors, prototype):
    """Nearest labeled TRAIN embedding. Exact-match flag makes evidence strict."""
    queries=np.asarray(slot_vectors,dtype=np.float32)
    labels=prototype['labels']
    refs=prototype['vectors']
    if queries.ndim!=2 or queries.shape[1]!=1024 or not len(refs):
        raise ValueError('GAMER_POSITIONAL_PROTOTYPE_DIMENSION')
    # Matrix distance avoids materializing a queries x prototypes x 1024 tensor.
    dist=np.maximum(np.sum(queries.astype(np.float64)**2,axis=1)[:,None] +
        np.sum(refs.astype(np.float64)**2,axis=1)[None,:] -
        2.0*(queries.astype(np.float64)@refs.astype(np.float64).T),0.0)
    choice=np.argmin(dist,axis=1)
    diff=np.max(np.abs(queries-refs[choice]),axis=1)
    return [{'predicted_token':labels[int(k)],'nearest_train_token_id':
             prototype['training_token_ids'][int(k)],
             'max_abs_embedding_delta':float(err),'exact_training_embedding_match':
             bool(err <= PROTOTYPE_TOL)} for k,err in zip(choice,diff)]


def _prototype_rows(align,frames,prototypes,rows):
    """No evaluation-token IDs or strings participate in the lookup itself."""
    result={part:{} for part in PARTS}
    for part in PARTS:
        queries=[];keys=[]
        for i in rows:
            for tok in align[i][part]['tokens']:
                queries.append(frames[part][i].reshape(compose.SLOTS,1024)[tok['slot']])
                keys.append((i,tok['slot']))
        decoded=_prototype_decode(np.stack(queries),prototypes[part]) if queries else []
        by_row=defaultdict(dict)
        for (i,slot),record in zip(keys,decoded):by_row[i][slot]=record
        for i in rows:
            pieces=[by_row[i][t['slot']] for t in align[i][part]['tokens']]
            # A nearest-neighbor guess is never promoted to an exact extraction
            # unless EVERY input token has a matching training embedding.
            supported=all(p['exact_training_embedding_match'] for p in pieces)
            text=''.join(p['predicted_token'] for p in pieces) if supported else None
            result[part][i]={'train_prototype_exact_coverage':supported,
                'reconstructed':text,'tokens':pieces}
    return result


def _digit_logits(logits, actual, predicted):
    a=str(actual); n=(str(predicted).zfill(compose.DIGITS)
                         if predicted is not None else None)
    out=[]
    for j,correct in enumerate(a):
        place=compose.DIGITS-len(a)+j
        row=logits[place*10:(place+1)*10]
        order=np.argsort(row)[::-1]
        predicted_digit=str(int(order[0]))
        out.append({'true':correct,'predicted':predicted_digit,
            'matches':bool(predicted_digit==correct),
            'true_digit_score':float(row[int(correct)]),
            'true_digit_rank':int(1+sum(row>row[int(correct)])),
            'readout_place_from_left':place,
            'predicted_score':float(row[int(order[0])]),
            'runner_up_digit':str(int(order[1])),
            'runner_up_score':float(row[int(order[1])]),
            'score_margin':float(row[int(order[0])]-row[int(order[1])])})
    return out


def _case_trace(i,case,alignment,q,preds,logits,proto):
    components={}
    for part in PARTS:
        source=alignment[part]
        true=getattr(q,part)
        pred=preds[part][i]
        if source['true_text']!=str(true):
            raise ValueError('GAMER_POSITIONAL_EXPECTS_CANONICAL_FRACTION_TEXT')
        digit_scores=_digit_logits(logits[part][i],true,pred)
        detailed=[]
        for entry,d in zip(source['digits'],digit_scores):
            detailed.append({**entry,**d})
        p=proto[part][i]
        is_correct=(pred==true)
        if is_correct:
            status='READOUT_CORRECT'
        elif p['train_prototype_exact_coverage'] and p['reconstructed']==str(true):
            status='DECODER_ERROR_INPUT_EMBEDDINGS_RECONSTRUCT_EXACTLY'
        elif not p['train_prototype_exact_coverage']:
            status='NO_MATCHING_TRAIN_TOKEN_PROTOTYPE_INFORMATION_NOT_PROVEN_ABSENT'
        else:
            status='TRAIN_PROTOTYPE_CONTRADICTS_TOKEN_TRACE_CHECK_ALIGNMENT'
        tokens=[{**t,'prototype':p['tokens'][j]}
                for j,t in enumerate(source['tokens'])]
        components[part]={'actual':str(true),'predicted':pred,
            'exact':bool(is_correct),'diagnosis':status,
            'prompt_char_span':source['prompt_char_span'],
            'tokens':tokens,'digit_positions':detailed,
            'token_pattern':source['token_pattern'],
            'train_prototype_reconstruction':p['reconstructed'],
            'train_prototype_exact_coverage':p['train_prototype_exact_coverage']}
    return {'index':i,'case_id':case.id,'true_q':str(q),
            'predicted_q':str(compose._assemble([preds['numerator'][i]],
                  [preds['denominator'][i]])[0]),
            'components':components}


def _summary(rows,panels):
    by_panel={}
    by_width=defaultdict(lambda:Counter())
    by_pattern=defaultdict(lambda:Counter())
    by_place=defaultdict(lambda:Counter())
    for panel,ids in panels.items():
        ix=set(map(int,ids))
        subset=[row for row in rows if row['index'] in ix]
        comp={}
        for part in PARTS:
            counter=Counter(r['components'][part]['diagnosis'] for r in subset)
            comp[part]={'cases':len(subset),'exact':sum(r['components'][part]['exact'] for r in subset),
                        'by_diagnosis':dict(counter)}
        by_panel[panel]={'cases':len(subset),'components':comp,
            'exact_fraction':sum(r['predicted_q']==r['true_q'] for r in subset)}
        for row in subset:
            for part in PARTS:
                v=row['components'][part]
                label=(panel,part,len(v['actual']))
                by_width[label]['total']+=1
                by_width[label]['correct']+=int(v['exact'])
                pat=(panel,part,','.join(map(str,v['token_pattern'])))
                by_pattern[pat]['total']+=1
                by_pattern[pat]['correct']+=int(v['exact'])
                for digit in v.get('digit_positions',[]):
                    key=(panel,part,digit['digit_from_right'],digit['activation_slot'])
                    by_place[key]['total']+=1
                    by_place[key]['correct']+=int(digit['matches'])
    def group(counters,key_names):
        return [dict(zip(key_names,key),**dict(value)) for key,value in sorted(counters.items())]
    return {'panels':by_panel,
            'by_digit_width':group(by_width,('panel','component','digits')),
            'by_token_pattern':group(by_pattern,('panel','component','token_lengths')),
            'by_digit_place_and_slot':group(by_place,('panel','component','place_from_right','activation_slot'))}


def _zero_line_replay(rows,tags,panels,source_report):
    result={}
    by_index={r['index']:r for r in rows}
    for short,panel in (('heldout','heldout_zero_anchor'),
                        ('challenge','challenge_zero_anchor')):
        groups=defaultdict(list)
        for ii in panels[panel]:
            i=int(ii);tag=tags[i]
            if tag['panel']!='zero_anchor':
                raise ValueError('GAMER_POSITIONAL_ZERO_PANEL_NOT_AN_ANCHOR')
            groups[(tag.get('group',tag.get('profile')),tag['surface'])].append(i)
        if len(groups)!=8 or any(len(ids)!=3 for ids in groups.values()):
            raise ValueError('GAMER_POSITIONAL_INCOMPLETE_ZERO_TRIPLES')
        complete=0;centers=0
        for ids in groups.values():
            zero=[i for i in ids if tags[i]['edit']=='zero']
            if len(zero)!=1:
                raise ValueError('GAMER_POSITIONAL_ZERO_CENTER_MISSING')
            centers+=int(by_index[zero[0]]['predicted_q']==by_index[zero[0]]['true_q'])
            complete+=int(all(by_index[i]['predicted_q']==by_index[i]['true_q']
                              for i in ids))
        prior=source_report['zero_anchor_triples'][short]
        if (centers != prior['exact_center_q'] or
            complete != prior['exact_q_all_three']):
            raise ValueError('GAMER_POSITIONAL_ZERO_TRIPLE_REPLAY_CHANGED:'+short)
        # No payoff head is refitted; use the previously certified payoff gate
        # only as a separate recorded condition, not as a new payoff inference.
        result[short]={'triples':8,'exact_center_q':centers,
           'exact_q_all_three':complete,
           'prior_frozen_payoffs_exact_all_three':prior['all_payoffs_exact_all_three'],
           'prior_strict_full_triples':prior['strict_exact_full_triples'],
           'prior_zero_line_accepted':prior['accepted']}
    return result


def run(args):
    source=_load_source(args.composition)
    protected=[source['root'],source['upstream']['root'],
               source['upstream']['precision']['root']]
    out=c0sign._output(args.output,protected)
    if out.exists():
        raise FileExistsError('GAMER_POSITIONAL_OUTPUT_ALREADY_EXISTS')
    deck,tags,panels,q=_deck(source)
    frames=_load_features(source,len(deck))
    report=source['report']
    print(f'GAMER POSITIONAL DIGIT AUDIT: {len(deck)} cached float32 captures, '
          'frozen embedding-slot heads; no new GPU pass',flush=True)
    preds={};logits={}
    for part in PARTS:
        entry=report['checkpoints'][part]
        pred,score,meta=_replay_scores(Path(entry['path']),frames[part],
                         report['source_belief_report_sha256'],
                         report['combined_deck_sha256'])
        preds[part]=pred;logits[part]=score
        for panel in ('fresh_dev','heldout_independent','heldout_zero_anchor',
                      'challenge_independent','challenge_zero_anchor'):
            ix=panels[panel]
            exactness=float(np.mean([pred[int(i)]==getattr(q[int(i)],part)
                                     for i in ix]))
            expected=report['component_accuracy'][part][panel]['exact']
            if not math.isclose(exactness,expected,abs_tol=1e-12):
                raise ValueError('GAMER_POSITIONAL_CHECKPOINT_REPLAY_METRIC_CHANGED:'+part+':'+panel)
    print('  mapping exact prompt digit positions to tokenizer offsets and slots',flush=True)
    # Tokenizer only; do not reload frozen Qwen/LoRA model weights or use GPU.
    from transformers import AutoTokenizer
    original=source['upstream']['precision']['previous']['source']['origin']
    model_id=original['meta']['lineage']['model_id']
    tok=AutoTokenizer.from_pretrained(model_id,trust_remote_code=False)
    tok.padding_side='right'
    aligned=_token_alignment(tok,deck)
    print('  building train-only token-embedding prototype control',flush=True)
    prototypes=_build_prototypes(aligned,frames,panels['train'])
    rows=list(range(len(deck)))
    pdecode=_prototype_rows(aligned,frames,prototypes,rows)
    traced=[]
    for i,case in enumerate(deck):
        item=_case_trace(i,case,aligned[i],q[i],preds,logits,pdecode)
        traced.append(item)
    # Each case has exactly one role; include the role in the trace without
    # risking ambiguous membership or changing canonical row order.
    memberships={int(i):panel for panel,idx in panels.items() for i in idx}
    if len(memberships)!=len(deck):
        raise ValueError('GAMER_POSITIONAL_PANEL_PARTITION_INCOMPLETE')
    for row in traced:row['panel']=memberships[row['index']]
    summary=_summary(traced,panels)
    zero_line=_zero_line_replay(traced,tags,panels,report)
    for panel in ('fresh_dev','heldout_independent','heldout_zero_anchor',
                  'challenge_independent','challenge_zero_anchor'):
        ix=panels[panel]
        expected=report['fraction_accuracy'][panel]['exact']
        actual=summary['panels'][panel]['exact_fraction']/len(ix)
        if not math.isclose(expected,actual,abs_tol=1e-12):
            raise ValueError('GAMER_POSITIONAL_FRACTION_REPLAY_METRIC_CHANGED:'+panel)
    out.mkdir(parents=True,exist_ok=False)
    detail=out/'digit_trace.jsonl'
    with detail.open('w',encoding='utf-8') as f:
        for row in traced:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    fail=[r for r in traced if any(not r['components'][p]['exact'] for p in PARTS)]
    snapshot=out/'failure_trace.json'
    gamer.atomic_json(snapshot,fail)
    diagnostic={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_POSITIONAL_DIAGNOSTIC',
        'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
        'no_lora_training':True,'no_c0_training':True,'no_payoff_head_training':True,
        'no_zero_detector_mutation':True,'no_head_refitting':True,
        'source_composition_report':str(source['report_path']),
        'source_composition_sha256':source['report_sha256'],
        'source_selected_features_sha256':report['selected_feature_archive']['sha256'],
        'combined_deck_sha256':report['combined_deck_sha256'],
        'frozen_lora_sha256':report['frozen_lora_sha256'],
        'selected_checkpoints':{p:report['checkpoints'][p]['sha256'] for p in PARTS},
        'cases':len(deck),'failed_component_cases':len(fail),
        'embedding_prototype':{
            p:{'training_unique_token_embeddings':len(prototypes[p]['labels']),
               'train_token_observations':prototypes[p]['observations'],
               'training_only':True,'exact_embedding_match_tolerance':PROTOTYPE_TOL}
            for p in PARTS},
        'results':summary,'zero_line_replay':zero_line,
        'trace_files':{'all':{'path':str(detail),'sha256':token._sha(detail)},
                       'failures':{'path':str(snapshot),'sha256':token._sha(snapshot)}},
        'interpretation':('A decoder error with exact reconstruction from '
           'TRAIN-labeled token embeddings demonstrates that its selected '
           'embedding representation contains enough token identity information '
           'to reconstruct the component. A missing TRAIN prototype is an '
           'untested representation, not proof that LoRA lacks the information. '
           'Tokenizer IDs/text are used only for diagnostic alignment and '
           'TRAIN prototype labels, never as evaluation predictive inputs. '
           'Embedding-stage readouts cannot demonstrate computation in LoRA.')}
    # Source report/archive and all four upstream heads remain immutable.
    compose._verify_frozen(source['upstream'])
    if (token._sha(source['report_path']) != source['report_sha256'] or
        token._sha(source['archive']) != report['selected_feature_archive']['sha256'] or
        any(token._sha(Path(report['checkpoints'][p]['path'])) !=
                report['checkpoints'][p]['sha256'] for p in PARTS)):
        raise ValueError('GAMER_POSITIONAL_UPSTREAM_MUTATED')
    gamer.atomic_json(out/'final_report.json',diagnostic)
    print('GAMER POSITIONAL DIGIT AUDIT COMPLETE:',out/'final_report.json',flush=True)
    return diagnostic


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--composition',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    return run(parser.parse_args(argv))


if __name__=='__main__':main()
