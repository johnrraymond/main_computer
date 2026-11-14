#!/usr/bin/env python3
"""Diagnostic T08 language acquisition: 15 train templates / 5 unseen templates.

The existing fixed-game payoff checkpoint supplies the learned *game* rule.
This separate supervised head learns the relation between two numerical spans
and the Hawk/Dove count roles. The only deterministic input adapter locates
exactly two integer spans and validates their sum; it does not search for Hawk
or Dove words or use an oracle at query time.

Frozen-Qwen mode trains a linear head on frozen LoRA span activations plus a
transparent text-context component. Text-only mode is a diagnostic baseline.
No trained module is promoted into production or overwrites the game head.
"""
from __future__ import annotations
import hashlib
import json
import random
import re
from pathlib import Path
from fractions import Fraction

from tools import nanojev_gamer_v1_hawk_dove_acquisition as acquired

SCHEMA = 'nanojev.gamer.v1.hawk-dove-t08-language-acquisition.v1'
# Bracketed role markers are GENERATOR ONLY; no generated prompt contains them.
NUMBER = re.compile(r'(?<![\w.+-])(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?![\w]|,\d|\.\d)')
WORD = re.compile(r'[a-z]+')


def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as inp:
        for buf in iter(lambda:inp.read(1<<20),b''):
            h.update(buf)
    return h.hexdigest()


def spans_from_prompt(prompt):
    """Only generic numeric detection: no action names, parser templates or labels."""
    if not isinstance(prompt,str) or len(prompt)>4096:
        raise ValueError('HAWK_DOVE_LANGUAGE_INVALID_PROMPT_LENGTH')
    matches=list(NUMBER.finditer(prompt))
    if len(matches)!=2:
        raise ValueError('HAWK_DOVE_LANGUAGE_REQUIRES_EXACTLY_TWO_NUMERIC_COUNTS')
    spans=[]
    for m in matches:
        token=m.group()
        if prompt[m.end():m.end()+1]=='%':
            raise ValueError('HAWK_DOVE_LANGUAGE_PERCENT_NOT_SUPPORTED')
        value=int(token.replace(',',''))
        if not 0<=value<=1_000_000:
            raise ValueError('HAWK_DOVE_LANGUAGE_COUNT_OUT_OF_RANGE')
        spans.append((m.start(),m.end(),None,token))
    if sum(int(s[3].replace(',','')) for s in spans)!=1_000_000:
        raise ValueError('HAWK_DOVE_LANGUAGE_COUNTS_MUST_SUM_TO_MILLION')
    return spans


def _context(prompt,spans):
    """Text feature input never receives generator roles or oracle decisions."""
    items=[]
    for start,end,_role,_number in spans:
        left=WORD.findall(prompt[:start].lower())[-4:]
        right=WORD.findall(prompt[end:].lower())[:1]
        items.append('left '+' '.join(left)+' right '+' '.join(right))
    return items


def _text_pair_matrix(rows, *, size=16384):
    from sklearn.feature_extraction.text import HashingVectorizer
    v=HashingVectorizer(analyzer='char',ngram_range=(3,5),n_features=size,
                        alternate_sign=True,norm='l2',lowercase=True)
    contexts=[]
    for row in rows:
        spans=spans_from_prompt(row['prompt'])
        contexts.extend(_context(row['prompt'],spans))
    feats=v.transform(contexts)
    return feats[0::2]-feats[1::2]


def _qwen_pair_matrix(rows,*,run,model_id,device,layer,max_length):
    import numpy as np
    from tools.nanojev_gamer_v1_construction_head import _capture
    query_rows=[{'prompt':r['prompt'],'spans':spans_from_prompt(r['prompt'])} for r in rows]
    captured,adapter_sha=_capture(model_id,run,device,layer,query_rows,max_length=max_length)
    if len(captured)!=len(rows)*2:
        raise ValueError('HAWK_DOVE_LANGUAGE_WRONG_QWEN_CAPTURE_COUNT')
    vec=np.stack([captured[2*i][0]-captured[2*i+1][0] for i in range(len(rows))]).astype('float32')
    if not np.isfinite(vec).all():raise ValueError('HAWK_DOVE_LANGUAGE_NONFINITE_FROZEN_ACTIVATIONS')
    return vec,adapter_sha


def _matrix(rows,*,encoder,run,model_id,device,layer,max_length):
    import numpy as np
    from scipy.sparse import csr_matrix,hstack
    text=_text_pair_matrix(rows)
    if encoder=='text':return text,None,0
    if encoder!='frozen-qwen':raise ValueError('HAWK_DOVE_LANGUAGE_UNKNOWN_ENCODER')
    raw,identity=_qwen_pair_matrix(rows,run=run,model_id=model_id,device=device,
                                    layer=layer,max_length=max_length)
    # Frozen output is normalized per example, never fit using holdout values.
    normalizer=np.linalg.norm(raw,axis=1,keepdims=True)
    raw=raw/np.maximum(normalizer,1e-8)
    # Text and Qwen features each have L2 norm ~1; no extra learned statistics.
    return hstack([text,csr_matrix(raw * 0.25)],format='csr'),identity,raw.shape[1]


def _fit(x,y,*,epochs,seed):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    if not 1<=epochs<=500:raise ValueError('HAWK_DOVE_LANGUAGE_EPOCHS_OUT_OF_RANGE')
    if len(set(y))!=2:raise ValueError('HAWK_DOVE_LANGUAGE_UNBALANCED_LABELS')
    model=LogisticRegression(solver='liblinear',C=10,random_state=seed,
                             max_iter=max(40,epochs))
    model.fit(x,np.asarray(y,dtype=np.int64))
    return model


def _game_checkpoint(output):
    p=Path(output)/'hawk_dove_head.json'
    report=Path(output)/'final_report.json'
    if not p.is_file() or not report.is_file():
        raise FileNotFoundError('HAWK_DOVE_LANGUAGE_NEEDS_ACQUIRED_GAME_CHECKPOINT')
    info=json.loads(report.read_text(encoding='utf-8'))
    if acquired.sha_file(p)!=info['checkpoint_sha256']:
        raise ValueError('HAWK_DOVE_LANGUAGE_GAME_CHECKPOINT_SHA_MISMATCH')
    state=json.loads(p.read_text(encoding='utf-8'))
    if state.get('schema')!=acquired.SCHEMA or set(state['weights'])!=set(acquired.VALUES):
        raise ValueError('HAWK_DOVE_LANGUAGE_GAME_CHECKPOINT_INVALID')
    if state['source_code_sha256']!=acquired.sha_file(Path(acquired.curriculum.__file__)):
        raise ValueError('HAWK_DOVE_LANGUAGE_SOURCE_CHANGED_SINCE_GAME_ACQUISITION')
    return state['weights'],info['checkpoint_sha256']


def _decision_for_row(prompt,first_is_hawk,weights):
    spans=spans_from_prompt(prompt)
    counts=[int(s[3].replace(',','')) for s in spans]
    a,b=(counts if first_is_hawk else counts[::-1])
    # Existing learned coefficients, existing exact fixed-game arithmetic.
    result=acquired.infer(acquired._make_t08(a,b),weights)
    result['construction_mode']='LEARNED_SPAN_ROLE_ASSIGNMENT_EXACT_GAME_ARITHMETIC'
    return result


def _old_parser_coverage(rows):
    accepted=0
    for row in rows:
        try:
            parsed=acquired._input(row['prompt'])
            accepted+=int(parsed['tier']=='T08')
        except ValueError:
            pass
    return {'accepted':accepted,'cases':len(rows)}


def _position_baseline(rows,train_rows):
    preferred=int(sum(r['first_is_hawk'] for r in train_rows)*2>=len(train_rows))
    return {'predict_first_is_hawk':bool(preferred),
            'correct':sum(r['first_is_hawk']==preferred for r in rows),'cases':len(rows)}


def _model_predictions(x,coef,intercept):
    import numpy as np
    if x.shape[1]!=len(coef):raise ValueError('HAWK_DOVE_LANGUAGE_FEATURE_SIZE_MISMATCH')
    return (x@np.asarray(coef,dtype='float32')+float(intercept)>0).astype(int).ravel()


def train(*,language_output,hawk_dove_output,run,model_id,device,layer,encoder,
          epochs,seed,samples_per_template,max_length):
    import numpy as np
    output=Path(language_output)
    if output.exists():raise FileExistsError('HAWK_DOVE_LANGUAGE_OUTPUT_EXISTS:'+str(output))
    weights,game_sha=_game_checkpoint(hawk_dove_output)
    from tools import nanojev_gamer_v1_hawk_dove_t08_corpus as corpus
    rows=corpus.generate(seed=seed,samples_per_template=samples_per_template)
    train_rows=[r for r in rows if r['split']=='train']
    test_rows=[r for r in rows if r['split']=='holdout']
    assert {r['template_id'] for r in train_rows}==set(range(15))
    assert {r['template_id'] for r in test_rows}==set(range(5))
    assert set(corpus.TRAIN_TEMPLATES).isdisjoint(corpus.HOLDOUT_TEMPLATES)
    if set(r['hawk_count'] for r in train_rows).intersection(r['hawk_count'] for r in test_rows):
        raise ValueError('HAWK_DOVE_LANGUAGE_TRAIN_HOLDOUT_COUNT_OVERLAP')
    print(f'GAMER T08 LANGUAGE: capturing {len(rows)} prompts; 15 training templates, 5 held out',flush=True)
    x,adapter_sha,dim=_matrix(rows,encoder=encoder,run=run,model_id=model_id,
                              device=device,layer=layer,max_length=max_length)
    n=len(train_rows)
    xtrain=x[:n];xtest=x[n:]
    labels=np.asarray([r['first_is_hawk'] for r in train_rows],dtype='int64')
    model=_fit(xtrain,labels,epochs=epochs,seed=seed)
    # Randomly permute labels and retrain as an anti-label-leakage control.
    rng=np.random.default_rng(seed+909)
    shuffled=labels[rng.permutation(n)]
    null=_fit(xtrain,shuffled,epochs=epochs,seed=seed)
    exact_train=corpus._score(model,xtrain,train_rows,weights)
    heldout=corpus._score(model,xtest,test_rows,weights)
    null_heldout=corpus._score(null,xtest,test_rows,weights)
    baseline=_position_baseline(test_rows,train_rows)
    # Prohibit a claim of success if a trivial position predictor is as good.
    useful=(heldout['role_accuracy']>=0.95 and
            all(v['exact_roles']/v['cases']>=0.90 for v in heldout['per_template'].values()) and
            heldout['boundary_exact']==heldout['boundary_cases'] and
            heldout['exact_roles']>baseline['correct'] and
            heldout['exact_roles']>null_heldout['exact_roles'])
    state={'schema':SCHEMA,'encoder':encoder,'model_id':model_id if encoder=='frozen-qwen' else None,
           'layer':layer if encoder=='frozen-qwen' else None,'run':str(Path(run).resolve()) if encoder=='frozen-qwen' else None,
           'adapter_sha256':adapter_sha,'feature_dimensions':x.shape[1],
           'max_length':max_length,
           'qwen_pair_dimension':dim,'weights':model.coef_[0].astype('float32').tolist(),
           'intercept':float(model.intercept_[0]), 'game_checkpoint_sha256':game_sha,
           'seed':seed}
    # Fingerprint source templates but never put their gold role labels into query.
    source_sha=hashlib.sha256(canonical({'train':corpus.TRAIN_TEMPLATES,'holdout':corpus.HOLDOUT_TEMPLATES}).encode()).hexdigest()
    state['template_source_sha256']=source_sha
    state['training_acceptance_status']='PASS_T08_LANGUAGE_HEAD_BEATS_CONTROLS' if useful else 'FAIL_T08_LANGUAGE_HEAD_DID_NOT_BEAT_CONTROLS'
    report={'status':'PASS_T08_LANGUAGE_HEAD_BEATS_CONTROLS' if useful else 'FAIL_T08_LANGUAGE_HEAD_DID_NOT_BEAT_CONTROLS',
            'schema':SCHEMA,'trained_head_diagnostic_only':True,'encoder':encoder,'epochs_requested_solver_max_iter':epochs,
            'training_templates':15,'unseen_holdout_templates':5,
            'training_cases':n,'holdout_cases':len(test_rows),
            'no_holdout_examples_used_for_fit':True,
            'train_holdout_count_pairs_disjoint':True,
            'pass_requires_95pct_role_accuracy_all_templates_90pct_and_all_boundaries':True,
            'train':exact_train,'holdout':heldout,
            'position_only_baseline':baseline,
            'shuffled_label_control':{'exact_roles':null_heldout['exact_roles'],'cases':len(test_rows)},
            'source_t08_strict_parser_coverage_on_holdouts':_old_parser_coverage(test_rows),
            'game_checkpoint_sha256':game_sha,
            'adapter_sha256':adapter_sha,
            'template_source_sha256':source_sha,
            'frozen_qwen_or_lora_updated':False,
            'base_qwen_weights_sha256_pinned':False,
            'payoff_head_updated':False,
            'teacher_or_sqlite_used_for_inference':False,
            'inference_does_not_import_training_corpus':True,
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
            'limitations':'Exactly two integer encounter counts summing to one million; '
                'the head learns count-role assignment from language, not the Hawk-Dove payoff rule. '
                'Qwen mode uses frozen representations; text mode is a linear lexical baseline. '
                'Neither demonstrates arbitrary language or arbitrary games.'}
    # Store only after evaluation, preserve even failed competence for diagnosis.
    output.mkdir(parents=True,exist_ok=False)
    checkpoint=output/'language_head.json'
    checkpoint.write_text(canonical(state)+'\n',encoding='utf-8')
    report['checkpoint_sha256']=sha(checkpoint)
    report['checkpoint']=str(checkpoint.resolve())
    (output/'final_report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def query(*,language_output,hawk_dove_output,prompt,device):
    p=Path(language_output)/'language_head.json'
    rp=Path(language_output)/'final_report.json'
    if not p.is_file() or not rp.is_file():
        raise FileNotFoundError('HAWK_DOVE_LANGUAGE_HEAD_NOT_TRAINED')
    info=json.loads(rp.read_text(encoding='utf-8'))
    if info['checkpoint_sha256']!=sha(p):
        raise ValueError('HAWK_DOVE_LANGUAGE_CHECKPOINT_TAMPERED')
    state=json.loads(p.read_text(encoding='utf-8'))
    if state.get('schema')!=SCHEMA or state['feature_dimensions']!=len(state['weights']):
        raise ValueError('HAWK_DOVE_LANGUAGE_BAD_CHECKPOINT')
    if state.get('training_acceptance_status')!=info.get('status'):
        raise ValueError('HAWK_DOVE_LANGUAGE_REPORT_AND_CHECKPOINT_STATUS_MISMATCH')
    weights,game_sha=_game_checkpoint(hawk_dove_output)
    if game_sha!=state['game_checkpoint_sha256']:
        raise ValueError('HAWK_DOVE_LANGUAGE_GAME_CHECKPOINT_CHANGED')
    rows=[{'prompt':prompt}]
    x,adapter_sha,dim=_matrix(rows,encoder=state['encoder'],
                              run=state.get('run'),model_id=state.get('model_id'),device=device,
                              layer=state.get('layer'),max_length=state['max_length'])
    if adapter_sha!=state['adapter_sha256'] or dim!=state['qwen_pair_dimension']:
        raise ValueError('HAWK_DOVE_LANGUAGE_FROZEN_ADAPTER_CHANGED')
    pred=int(_model_predictions(x,state['weights'],state['intercept'])[0])
    result=_decision_for_row(prompt,pred,weights)
    return {'status':'ACQUIRED_T08_LANGUAGE_DECISION_DIAGNOSTIC' if info['status'].startswith('PASS_') else 'UNCERTIFIED_T08_LANGUAGE_DECISION_DIAGNOSTIC',
            'language_holdout_gate_passed':info['status'].startswith('PASS_'),
            'schema':SCHEMA, 'language_checkpoint_sha256':info['checkpoint_sha256'],
            'game_checkpoint_sha256':game_sha,
            'encoder':state['encoder'],
            'first_numeric_role':'HAWK' if pred else 'DOVE',
            'decision_source':'LEARNED_LANGUAGE_ROLE_HEAD_AND_LEARNED_PAYOFF_HEAD_PLUS_EXACT_ARITHMETIC',
            'no_oracle_or_sqlite_access_during_query':True,
            'no_production_authority_change':True,
            'result':result}
