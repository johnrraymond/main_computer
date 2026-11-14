#!/usr/bin/env python3
"""Hawk–Dove T08 15-train/5-holdout role-acquisition repair, version 2.

This is a new, isolated training artifact. The failed v1 checkpoint, original
Hawk–Dove payoff head, Qwen/LoRA and production numerical authority are never
mutated. Queries import neither training curriculum nor oracle.

The model learns linear weights on generic clause-local word features, with an
optional separate frozen-Qwen span-difference input. Clause splitting locates
phrases; it never labels Hawk or Dove using rules. A deterministic label parser
is scored solely as an independent comparator, never used to answer queries.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from fractions import Fraction

SCHEMA='nanojev.gamer.v1.hawk-dove-t08-role-acquisition-v2.v1'
WORD=re.compile(r'[a-z]+',re.I)
CLAUSE=re.compile(r'(?i)(?:[.;]|\b(?:and|while|whereas|versus|but)\b)')


def _canonical(data):
    return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for buf in iter(lambda:f.read(1<<20),b''):h.update(buf)
    return h.hexdigest()


def generic_number_spans(prompt):
    # No role vocabulary or Hawk–Dove template knowledge is used here.
    from tools.nanojev_gamer_v1_hawk_dove_t08_language import spans_from_prompt
    return spans_from_prompt(prompt)


def _context_features(prompt):
    spans=generic_number_spans(prompt)
    boundaries=[0]+[m.end() for m in CLAUSE.finditer(prompt)]+[len(prompt)]
    out=[]
    for start,end,*_ in spans:
        lo=max(x for x in boundaries if x<=start)
        hi=min(x for x in boundaries if x>=end)
        words=[m.group().lower() for m in WORD.finditer(prompt[lo:hi])]
        left=[m.group().lower() for m in WORD.finditer(prompt[lo:start])]
        right=[m.group().lower() for m in WORD.finditer(prompt[end:hi])]
        features={}
        for w in words:features['word='+w]=1.0
        for w in left:features['left='+w]=1.0
        for w in right:features['right='+w]=1.0
        out.append(features)
    if len(out)!=2:
        raise ValueError('T08_V2_GENERIC_NUMERIC_ADAPTER_MUST_RETURN_TWO_SPANS')
    return out


def _word_vector(rows, vocabulary=None):
    from sklearn.feature_extraction import DictVectorizer
    pairs=[]
    for row in rows:
        f0,f1=_context_features(row['prompt'])
        pairs.append((f0,f1))
    vectors=DictVectorizer(sparse=True,sort=True)
    examples=[f for pair in pairs for f in pair]
    if vocabulary is None:
        merged=vectors.fit_transform(examples)
    else:
        vectors.vocabulary_=dict(vocabulary)
        vectors.feature_names_=[k for k,v in sorted(vocabulary.items(),key=lambda kv:kv[1])]
        merged=vectors.transform(examples)
    return merged[::2]-merged[1::2],vectors.vocabulary_


def _frozen_features(rows,*,run,model_id,device,layer,max_length):
    from tools.nanojev_gamer_v1_hawk_dove_t08_language import _qwen_pair_matrix
    # Reuse certified frozen LoRA capture; labels are never provided to encoder.
    return _qwen_pair_matrix([{'prompt':r['prompt']} for r in rows],run=run,
                             model_id=model_id,device=device,layer=layer,max_length=max_length)


def _combine(lexical,qwen,*,qwen_weight=0.25):
    if qwen is None:return lexical
    import numpy as np
    from scipy.sparse import hstack,csr_matrix
    qwen=qwen.astype('float32')
    scale=np.linalg.norm(qwen,axis=1,keepdims=True)
    qwen=(qwen/np.maximum(scale,1e-8))*qwen_weight
    return hstack([lexical,csr_matrix(qwen)],format='csr')


def _fit(x,labels,*,seed,epochs):
    from sklearn.linear_model import LogisticRegression
    if len(set(labels))!=2:raise ValueError('T08_V2_TRAIN_LABEL_COLLAPSE')
    clf=LogisticRegression(solver='liblinear',C=10.0,random_state=seed,
                           max_iter=max(50,epochs))
    clf.fit(x,labels)
    return clf


def _predict(x,coef,intercept):
    import numpy as np
    if len(coef)!=x.shape[1]:raise ValueError('T08_V2_CHECKPOINT_FEATURE_WIDTH_MISMATCH')
    return (x@np.asarray(coef,dtype='float32')+float(intercept)>0).astype(int).ravel()


def _independent_decision(a,b):
    # Evaluation only; Qwen and trained game head never receive this function.
    x=3*b-a
    y=Fraction(3*b,2)
    return 'HAWK' if Fraction(x)>y else 'DOVE' if Fraction(x)<y else 'TIE'


def _decision(prompt,first_role,weights):
    from tools.nanojev_gamer_v1_hawk_dove_t08_language import _decision_for_row
    return _decision_for_row(prompt,int(first_role),weights)


def _evaluate(rows,pred,weights):
    exact=0;decision=0;boundary=0;bound_total=0;by_template={}
    for row,role in zip(rows,pred):
        correct=int(int(role)==int(row['first_is_hawk']))
        result=_decision(row['prompt'],role,weights)
        good=int(result['decision']==_independent_decision(row['hawk_count'],row['dove_count']))
        exact+=correct;decision+=good
        if row.get('is_boundary_probe'):
            bound_total+=1;boundary+=correct*good
        t=by_template.setdefault(str(row['template_id']),{'cases':0,'exact_roles':0,'correct_decisions':0})
        t['cases']+=1;t['exact_roles']+=correct;t['correct_decisions']+=good
    n=len(rows)
    return {'cases':n,'exact_roles':exact,'role_accuracy':exact/n,
            'correct_decisions':decision,'decision_accuracy':decision/n,
            'boundary_exact':boundary,'boundary_cases':bound_total,
            'per_template':by_template}


def _simple_label_baseline(rows):
    # Deliberately deterministic external comparator: nearest Hawk/Dove word in
    # the clause, no acquired weights. This is NEVER used by _decision/query.
    result=[]
    for row in rows:
        prompt=row['prompt'];spans=generic_number_spans(prompt)
        boundaries=[0]+[m.end() for m in CLAUSE.finditer(prompt)]+[len(prompt)]
        votes=[]
        for st,en,*_ in spans:
            lo=max(x for x in boundaries if x<=st)
            hi=min(x for x in boundaries if x>=en)
            words=[(m.group().lower(),abs(lo+(m.start()+m.end())/2-st)) for m in WORD.finditer(prompt[lo:hi])]
            labels=[(dist,w) for w,dist in words if w in ('hawk','dove')]
            votes.append(sorted(labels)[0][1] if labels else None)
        result.append(1 if votes[0]=='hawk' and votes[1]!='hawk' else 0 if votes[0]=='dove' and votes[1]!='dove' else -1)
    return result


def _load_game_head(directory):
    from tools.nanojev_gamer_v1_hawk_dove_t08_language import _game_checkpoint
    return _game_checkpoint(directory)


def _freeze_previous(prior):
    if prior is None:return None
    prior=Path(prior)
    report_path=prior/'final_report.json'
    chk_path=prior/'language_head.json'
    if not report_path.is_file() or not chk_path.is_file():
        raise FileNotFoundError('T08_V2_REQUIRED_PRIOR_FAILURE_REPORT_OR_CHECKPOINT_MISSING')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('status')!='FAIL_T08_LANGUAGE_HEAD_DID_NOT_BEAT_CONTROLS':
        raise ValueError('T08_V2_PRIOR_MUST_BE_FAILED_V1_HEAD')
    if report.get('checkpoint_sha256')!=_sha(chk_path):
        raise ValueError('T08_V2_PRIOR_CHECKPOINT_FINGERPRINT_MISMATCH')
    return {'prior_dir':str(prior.resolve()),'status':report['status'],
            'prior_checkpoint_sha256':_sha(chk_path),'prior_report_sha256':_sha(report_path),
            'prior_holdout_exact_roles':report.get('holdout',{}).get('exact_roles'),
            'prior_holdout_cases':report.get('holdout',{}).get('cases')}


def train(*,output,game_head,prior,run,model_id,device,layer,max_length,
          encoder,epochs,seed,samples_per_template):
    from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2_corpus as corpus
    import numpy as np
    output=Path(output)
    if output.exists():raise FileExistsError('T08_V2_IMMUTABLE_OUTPUT_ALREADY_EXISTS:'+str(output))
    weights,game_sha=_load_game_head(game_head)
    prior_lineage=_freeze_previous(prior)
    training,heldout=corpus.curriculum(seed=seed,samples_per_template=samples_per_template)
    print(f'GAMER T08 V2: {len(training)} trained prompts in 15 role-balanced templates; {len(heldout)} holdouts in five untouched templates',flush=True)
    train_x,vocab=_word_vector(training)
    test_x,_=_word_vector(heldout,vocabulary=vocab)
    qtrain=qtest=None; adapter_sha=None;qdim=0
    if encoder=='frozen-qwen':
        # FROZEN features: capture the training partition without holdouts first.
        qtrain,adapter_sha=_frozen_features(training,run=run,model_id=model_id,device=device,
                                            layer=layer,max_length=max_length)
        qdim=qtrain.shape[1]
        qtest,test_sha=_frozen_features(heldout,run=run,model_id=model_id,device=device,
                                        layer=layer,max_length=max_length)
        if test_sha!=adapter_sha:raise ValueError('T08_V2_ADAPTER_CHANGED_BETWEEN_TRAIN_AND_TEST')
    elif encoder!='text':raise ValueError('T08_V2_UNKNOWN_ENCODER')
    x=_combine(train_x,qtrain);xt=_combine(test_x,qtest)
    labels=np.asarray([r['first_is_hawk'] for r in training],dtype=np.int64)
    model=_fit(x,labels,seed=seed,epochs=epochs)
    # No hyperparameters are chosen using the holdout. The original five
    # surfaces have already been inspected in V1, so label this a regression.
    # All training complete before any holdout prediction.
    prediction=_predict(xt,model.coef_[0],model.intercept_[0])
    train_pred=_predict(x,model.coef_[0],model.intercept_[0])
    train_score=_evaluate(training,train_pred,weights)
    score=_evaluate(heldout,prediction,weights)
    rng=np.random.default_rng(seed+931)
    shuffled=_fit(x,labels[rng.permutation(len(labels))],seed=seed,epochs=epochs)
    null_pred=_predict(xt,shuffled.coef_[0],shuffled.intercept_[0])
    null_score=_evaluate(heldout,null_pred,weights)
    lex_head=_fit(train_x,labels,seed=seed,epochs=epochs)
    lex_score=_evaluate(heldout,_predict(test_x,lex_head.coef_[0],lex_head.intercept_[0]),weights)
    qwen_score=None
    if encoder=='frozen-qwen':
        from scipy.sparse import csr_matrix
        # Informational ablation: is frozen Qwen carrying usable role signal?
        qtrain_only=csr_matrix(qtrain);qtest_only=csr_matrix(qtest)
        qmodel=_fit(qtrain_only,labels,seed=seed,epochs=epochs)
        qwen_score=_evaluate(heldout,_predict(qtest_only,qmodel.coef_[0],qmodel.intercept_[0]),weights)
    position_guess=int(labels.sum()*2>=len(labels))
    position_score=_evaluate(heldout,[position_guess]*len(heldout),weights)
    detpred=_simple_label_baseline(heldout)
    det_correct=sum(int(p==r['first_is_hawk']) for p,r in zip(detpred,heldout))
    # If the deterministic comparator cannot identify a pair, it abstains (-1)
    # rather than taking a decision or getting credit.
    # The V1 five holdout surfaces were inspected before V2 was built. They
    # are useful regression checks, but they are NOT independent certification.
    reused_holdouts=True
    diagnostic_gate=(score['role_accuracy']>=.95 and
                     score['boundary_exact']==score['boundary_cases'] and
                     all(v['exact_roles']/v['cases']>=.90 for v in score['per_template'].values()) and
                     score['exact_roles']>position_score['exact_roles'] and
                     score['exact_roles']>null_score['exact_roles'])
    # An accurate neural+lexical head does not establish that Qwen contributes.
    # The neural pathway must be independently competent AND necessary to
    # improve the combined output relative to both bypasses.
    neural_necessary=(encoder=='frozen-qwen' and qwen_score is not None and
                      qwen_score['role_accuracy']>=.95 and
                      score['exact_roles']>lex_score['exact_roles'] and
                      score['exact_roles']>det_correct)
    certified=bool(diagnostic_gate and neural_necessary and not reused_holdouts)
    state={'schema':SCHEMA,'encoder':encoder,'coef':model.coef_[0].astype('float32').tolist(),
           'intercept':float(model.intercept_[0]),'vocabulary':vocab,
           'feature_count':x.shape[1],'lexical_feature_count':train_x.shape[1],
           'qwen_feature_count':qdim,'model_id':model_id if encoder=='frozen-qwen' else None,
           'run':str(Path(run).resolve()) if encoder=='frozen-qwen' else None,
           'layer':layer if encoder=='frozen-qwen' else None,'adapter_sha256':adapter_sha,
           'max_length':max_length,'game_checkpoint_sha256':game_sha,
           'acceptance_status':'PASS_T08_V2_ROLE_TRANSFER' if certified else 'FAIL_T08_V2_NEURAL_ROLE_UNPROVEN',
           'neural_role_transfer_certified':certified,
           'seed':seed}
    report={'status':state['acceptance_status'],'schema':SCHEMA,
            'encoder':encoder,'epochs':epochs,'seed':seed,
            'train_templates':15,'holdout_templates':5,
            'training_cases':len(training),'holdout_cases':len(heldout),
            'per_template_train_role_balance':True,
            'train_holdout_template_disjoint':True,
            'train_holdout_count_pairs_disjoint':True,
            'reused_holdouts_known_from_v1_failure':reused_holdouts,
            'diagnostic_role_gate_passed':bool(diagnostic_gate),
            'neural_necessary_vs_lexical_and_parser':bool(neural_necessary),
            'neural_role_transfer_certified':certified,
            'training_uses_original_15_framings_with_order_reversal_counterfactuals':True,
            'non_neural_baseline_may_match_model':True,
            'holdout_data_used_in_fitting':False,
            'train':train_score,'holdout':score,
            'lexical_only_control':lex_score,'frozen_qwen_only_control':qwen_score,
            'position_only_control':position_score,
            'shuffled_label_control':{'exact_roles':null_score['exact_roles'],'cases':len(heldout)},
            'deterministic_nearest_label_control':{'exact_roles':det_correct,'cases':len(heldout),'abstentions':detpred.count(-1)},
            'neural_superiority_over_deterministic_baseline_demonstrated':(score['exact_roles']>det_correct if encoder=='frozen-qwen' else False),
            'game_checkpoint_sha256':game_sha,'prior_failed_checkpoint':prior_lineage,
            'game_payoff_head_changed':False,'frozen_qwen_lora_changed':False,
            'production_authority_changed':False,
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
            'limitations':'Game-specific count-role language task, not arbitrary game acquisition. '
                          'Deterministic clause segmentation and numeric extraction remain. '
                          'Prior five holdouts are known from the failed v1 run, so this is a regression comparison, not fresh blind certification.'}
    # Immutable output: create only after all scoring and prior validation.
    output.mkdir(parents=True,exist_ok=False)
    checkpoint=output/'role_head.json'
    checkpoint.write_text(_canonical(state)+'\n',encoding='utf-8')
    report['checkpoint']=str(checkpoint.resolve())
    report['checkpoint_sha256']=_sha(checkpoint)
    (output/'final_report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def _neural_role_certified(meta,state):
    """Fail-closed certification; historic PASS strings grant no authority."""
    return (meta.get('neural_role_transfer_certified') is True and
            state.get('neural_role_transfer_certified') is True and
            meta.get('reused_holdouts_known_from_v1_failure') is False and
            meta.get('neural_necessary_vs_lexical_and_parser') is True and
            meta.get('neural_superiority_over_deterministic_baseline_demonstrated') is True)


def query(*,output,game_head,prompt,device,diagnostic=False):
    """Retired legacy inference; inspect only by explicit diagnostic opt-in."""
    output=Path(output)
    checkpoint=output/'role_head.json'
    report=output/'final_report.json'
    if not checkpoint.is_file() or not report.is_file():
        raise FileNotFoundError('T08_V2_CHECKPOINT_OR_REPORT_MISSING')
    meta=json.loads(report.read_text(encoding='utf-8'))
    if meta.get('checkpoint_sha256')!=_sha(checkpoint):
        raise ValueError('T08_V2_CHECKPOINT_FINGERPRINT_MISMATCH')
    state=json.loads(checkpoint.read_text(encoding='utf-8'))
    if state.get('schema')!=SCHEMA or state.get('acceptance_status')!=meta.get('status'):
        raise ValueError('T08_V2_CHECKPOINT_SCHEMA_OR_STATUS_MISMATCH')
    # Integrity may be inspected, but the old role head has NO non-diagnostic
    # inference authority, even if a historical checkpoint claims PASS.
    if not diagnostic:
        raise ValueError('T08_V2_NEURAL_ROLE_UNPROVEN_LEGACY_QUERY_RETIRED_USE_BACKBONE_FIRST')
    certified=_neural_role_certified(meta,state)
    if not certified and not diagnostic:
        raise ValueError('T08_V2_NEURAL_ROLE_UNPROVEN_DIAGNOSTIC_ONLY')
    words,vocab=_word_vector([{'prompt':prompt}],vocabulary=state['vocabulary'])
    q=None;adapter_sha=None
    if state['encoder']=='frozen-qwen':
        q,adapter_sha=_frozen_features([{'prompt':prompt}],run=state['run'],
                                       model_id=state['model_id'],device=device,
                                       layer=state['layer'],max_length=state['max_length'])
        if adapter_sha!=state['adapter_sha256'] or q.shape[1]!=state['qwen_feature_count']:
            raise ValueError('T08_V2_FROZEN_QWEN_LINEAGE_MISMATCH')
    x=_combine(words,q)
    if x.shape[1]!=state['feature_count']:
        raise ValueError('T08_V2_SAVED_HEAD_FEATURE_COUNT_MISMATCH')
    role=int(_predict(x,state['coef'],state['intercept'])[0])
    weights,game_sha=_load_game_head(game_head)
    if state['game_checkpoint_sha256']!=game_sha:
        raise ValueError('T08_V2_ORIGINAL_GAME_CHECKPOINT_CHANGED')
    result=_decision(prompt,role,weights)
    return {'status':'ACQUIRED_T08_V2_ROLE_DECISION' if certified else 'UNCERTIFIED_T08_V2_ROLE_DECISION',
            'language_holdout_gate_passed':certified,
            'diagnostic_role_gate_passed':bool(meta.get('diagnostic_role_gate_passed',False)),
            'neural_role_transfer_certified':certified,
            'schema':SCHEMA,'first_numeric_role':'HAWK' if role else 'DOVE',
            'language_checkpoint_sha256':meta['checkpoint_sha256'],
            'game_checkpoint_sha256':game_sha,
            'decision_source':'TRAINED_ROLE_HEAD_PLUS_UNCHANGED_ACQUIRED_PAYOFF_HEAD_EXACT_ARITHMETIC',
            'no_teacher_or_training_corpus_used_during_query':True,
            'production_authority_changed':False,'result':result}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('train','query'))
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--game-head',type=Path,help='legacy diagnostic only; forbidden in neural query')
    parser.add_argument('--prior',type=Path)
    parser.add_argument('--run',type=Path,default=Path('runtime/reports/gamer-v1/transition-005'))
    parser.add_argument('--model-id',default='Qwen/Qwen3-0.6B')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--layer',type=int,default=9)
    parser.add_argument('--max-length',type=int,default=256)
    parser.add_argument('--encoder',choices=('frozen-qwen','text'),default='frozen-qwen')
    parser.add_argument('--epochs',type=int,default=200)
    parser.add_argument('--seed',type=int,default=47)
    parser.add_argument('--samples-per-template',type=int,default=48)
    parser.add_argument('--prompt')
    parser.add_argument('--diagnostic',action='store_true',
                        help='Permit inspection of uncertified lexical/bypassed decisions (never promotes)')
    args=parser.parse_args(argv)
    # Retire legacy role-extractor inference. It remains opt-in only for
    # historical diagnostics; never silently promote or answer via lexical
    # bypass. Production-facing queries now require the raw-prompt Qwen route.
    if args.mode=='query' and not args.diagnostic:
        if args.game_head is not None:
            raise ValueError('GAMER_EXTERNAL_GAME_HEAD_SELECTION_FORBIDDEN')
        if not args.prompt:parser.error('query requires --prompt')
        from tools import nanojev_gamer_v1_backbone_first as backbone
        r=backbone.query(checkpoint=args.output,prompt=args.prompt,device=args.device)
        print(json.dumps(r,indent=2,sort_keys=True))
        return r
    if args.mode=='train' and not args.diagnostic:
        raise ValueError('GAMER_LEGACY_ROLE_TRAINING_RETIRED_USE_BACKBONE_FIRST_TRAIN')
    if args.game_head is None:
        parser.error('legacy diagnostic requires --game-head')
    if args.mode=='train':
        r=train(output=args.output,game_head=args.game_head,prior=args.prior,
                run=args.run,model_id=args.model_id,device=args.device,layer=args.layer,
                max_length=args.max_length,encoder=args.encoder,epochs=args.epochs,
                seed=args.seed,samples_per_template=args.samples_per_template)
    else:
        if not args.prompt:parser.error('query requires --prompt')
        r=query(output=args.output,game_head=args.game_head,prompt=args.prompt,device=args.device,
                diagnostic=args.diagnostic)
    print('GAMER T08 ROLE V2 '+str(r['status']))
    print(json.dumps(r,indent=2,sort_keys=True))
    return r


if __name__=='__main__':main()
