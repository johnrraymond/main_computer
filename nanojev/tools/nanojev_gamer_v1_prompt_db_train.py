#!/usr/bin/env python3
"""Isolated smoke training on the complete GAMER SQLite registry.

This trains a *small auxiliary prompt-to-game-planning router*, NOT Qwen/LoRA,
C0, Wide, or the activated exact to_numerical authority. Every registered game
contributes to family/scope training. Only certified executable objectives
contribute to operator-program training; catalog games have no numerical label.

All labels are source-verified; the input is only the original prompt, never
its expected answer, mathematical target, or oracle program. Outputs are
standalone, immutable-once-written diagnostic checkpoints and a metrics report.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_prompt_db_deconfound as deconfound

SCHEMA = 'nanojev.gamer.v1.sqlite-strategy-router-train.v2'
LEGACY_SCHEMA = 'nanojev.gamer.v1.sqlite-strategy-router-train.v1'
FEATURE_SCHEMA = 'nanojev.gamer.v1.cue-blind-prompt-features.v1'
NUMERIC = re.compile(r'(?<![\w])[-+]?(?:\d+/\d+|\d+(?:\.\d+)?)(?![\w])')
FEATURES = 1 << 14
SCOPES = tuple(smoke.SCOPES)


def _digest(path: Path) -> str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def _sanitize(prompt: str) -> str:
    """Mask supplied numeral values for program recognition; retain prose."""
    return NUMERIC.sub('NUMVALUE', prompt)


def _program(record: dict) -> str:
    steps=record.get('factor_steps')
    if not isinstance(steps,list) or not steps or not all(isinstance(x,dict) and isinstance(x.get('operation'),str) for x in steps):
        raise ValueError('GAMER_TRAIN_CERTIFIED_PROGRAM_MISSING')
    if record.get('factor_final_step') != len(steps):
        raise ValueError('GAMER_TRAIN_CERTIFIED_PROGRAM_INCONSISTENT')
    return '|'.join(x['operation'] for x in steps)


def load_training_rows(path: Path, *, full_oracle_verify=True):
    """Read-only verified corpus. Catalog gets one train, one dev example."""
    path=Path(path)
    verified=smoke.verify(path,verify_each_oracle=full_oracle_verify)
    rows=[]
    with sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        for (pid,game,family,scope,split,task,prompt,eligible,verified_oracle,source) in conn.execute('''
            SELECT p.prompt_id,p.game_id,g.family,g.scope,p.split,p.task,p.prompt,
                   p.numerical_training_eligible,p.oracle_verified,p.source_case_json
            FROM prompts p JOIN games g ON g.game_id=p.game_id
            ORDER BY p.game_id,p.split,p.prompt_id'''):
            if eligible:
                if verified_oracle!=1 or source is None or split not in ('train','dev','test'):
                    raise ValueError('GAMER_TRAIN_UNCERTIFIED_ROW_REFUSED')
                program=_program(json.loads(source))
                used_split=split
            else:
                if source is not None or scope!='catalog_only' or split!='catalog':
                    raise ValueError('GAMER_TRAIN_CATALOG_LABEL_LEAKAGE')
                if task=='structural_inventory':used_split='train'
                elif task=='transformation_specification':used_split='dev'
                else:raise ValueError('GAMER_TRAIN_BAD_CATALOG_TASK')
                program=None
            rows.append(dict(id=pid,game=game,family=family,scope=scope,
                             split=used_split,catalog_only=not bool(eligible),
                             program=program,text=_sanitize(prompt)))
    if len({r['game'] for r in rows})!=229 or len(rows)!=verified['prompts']:
        raise ValueError('GAMER_TRAIN_INCOMPLETE_GAME_COVERAGE')
    if any(r['catalog_only'] and r['program'] is not None for r in rows):
        raise ValueError('GAMER_TRAIN_CATALOG_PROGRAM_LABEL_LEAK')
    by_group=Counter((r['split'],r['catalog_only']) for r in rows)
    if any(by_group.get((s,False),0)==0 for s in ('train','dev','test')):
        raise ValueError('GAMER_TRAIN_NUMERICAL_SPLIT_EMPTY')
    if by_group.get(('train',True),0)!=83 or by_group.get(('dev',True),0)!=83:
        # One catalog prompt per game cannot supply independent held-out evaluation.
        # Refuse to report a misleading result when the second prompt is absent.
        raise ValueError('GAMER_TRAIN_REQUIRES_TWO_CATALOG_PROMPTS_PER_GAME')
    return rows,verified


def _split(rows, split, *, numerical_only=False):
    return [r for r in rows if r['split']==split and (not numerical_only or r['program'] is not None)]


def _accuracy(model, x, labels, *, target):
    if len(labels)==0:return None
    pred=model.predict(x)
    n=int((pred==labels).sum())
    return {'cases':len(labels),'correct':n,'accuracy':n/len(labels)}


def _new_model(*,seed,alpha):
    from sklearn.linear_model import SGDClassifier
    return SGDClassifier(loss='log_loss',alpha=alpha,learning_rate='optimal',
                         random_state=seed,shuffle=False,average=False,fit_intercept=True)


def _checkpoint(path,models,vectorizer,meta):
    import numpy as np
    arrays={'schema':np.array(SCHEMA),
            'meta_json':np.array(smoke.canonical(meta)),
            'n_features':np.array(FEATURES,dtype='int64')}
    for name,model in models.items():
        arrays[f'{name}_classes']=np.asarray(model.classes_,dtype=np.str_)
        arrays[f'{name}_coef']=np.asarray(model.coef_,dtype=np.float64)
        arrays[f'{name}_intercept']=np.asarray(model.intercept_,dtype=np.float64)
    with path.open('wb') as f:
        np.savez_compressed(f,**arrays)
    with np.load(path,allow_pickle=False) as loaded:
        if str(loaded['schema'])!=SCHEMA or int(loaded['n_features'])!=FEATURES:
            raise ValueError('GAMER_TRAIN_CHECKPOINT_REPLAY_FAILED')
        for name,model in models.items():
            if not np.array_equal(loaded[f'{name}_coef'],model.coef_) or not np.array_equal(loaded[f'{name}_classes'],model.classes_):
                raise ValueError('GAMER_TRAIN_CHECKPOINT_REPLAY_FAILED')
    return _digest(path)


def validate_train_request(db: Path, train_output: Path, *, epochs=8, alpha=1e-5):
    """Check destination safety before a CLI train command creates a missing corpus.

    The corpus is not built when the requested training run is invalid or the
    output would overwrite an existing experiment. The trainer repeats these
    checks when invoked directly.
    """
    if epochs < 1 or epochs > 500:
        raise ValueError('GAMER_TRAIN_EPOCHS_OUT_OF_RANGE')
    if not (0 < alpha <= 1):
        raise ValueError('GAMER_TRAIN_ALPHA_OUT_OF_RANGE')
    db = Path(db).resolve()
    train_output = Path(train_output).resolve()
    if (train_output == db.parent or db.is_relative_to(train_output)
            or train_output.is_relative_to(db) or any(
                p.name in ('transition-005', 'c0-s-closure-0001', 'c0-shared-digit-0001')
                for p in (train_output, *train_output.parents))):
        raise ValueError('GAMER_TRAIN_PROTECTED_SOURCE_DIRECTORY')
    if train_output.exists():
        raise FileExistsError('GAMER_TRAIN_OUTPUT_ALREADY_EXISTS:' + str(train_output))
    return db, train_output


def train(db: Path, train_output: Path, *, epochs=8,alpha=1e-5,seed=47,full_oracle_verify=True):
    """Train the isolated operator/family/scope router on ALL 229 named games."""
    import numpy as np
    from sklearn.feature_extraction.text import HashingVectorizer

    db, train_output = validate_train_request(db, train_output, epochs=epochs, alpha=alpha)
    rows,verified=load_training_rows(db,full_oracle_verify=full_oracle_verify)
    db_sha=_digest(db)
    registry=smoke.source_registry()
    cue_titles=deconfound.titles_from_registry(registry)
    cue_family_names=deconfound.family_names_from_registry(smoke.FAMILY_MODELS)
    # Versioned, label-neutral, inference-replayable feature representation.
    # The registry supplies only a vocabulary of strings to remove, not labels.
    for row in rows:
        row['text'] = deconfound.blind_text(row['text'], cue_titles, cue_family_names)
    train_templates={r['text'] for r in rows if r['split']=='train'}
    test_cert=[r for r in rows if r['split']=='test' and not r['catalog_only']]
    template_overlap=sum(r['text'] in train_templates for r in test_cert)

    vectorizer=HashingVectorizer(n_features=FEATURES,ngram_range=(1,2),lowercase=True,
                                 alternate_sign=False,norm='l2',binary=True)
    datasets={k:_split(rows,k) for k in ('train','dev','test')}
    feature={k:vectorizer.transform([r['text'] for r in v]) for k,v in datasets.items()}
    objectives={k:_split(rows,k,numerical_only=True) for k in datasets}
    objective_features={k:vectorizer.transform([r['text'] for r in v]) for k,v in objectives.items()}
    # Catalog-only entries are missing rules. Their family names and equations
    # were their only distinguishing evidence; scoring them as learned family
    # competence would be dishonest. Retain them for scope assessment only.
    family_sets={k:[r for r in datasets[k] if not r['catalog_only']] for k in datasets}
    family_features={k:vectorizer.transform([r['text'] for r in family_sets[k]]) for k in datasets}
    tasks={
        'family':(family_sets,family_features,'family'),
        'scope':(datasets,feature,'scope'),
        'program':(objectives,objective_features,'program'),
    }
    classifiers={}; metrics={}; classes_map={}
    for name,(parts,inputs,label) in tasks.items():
        ytrain=np.array([r[label] for r in parts['train']],dtype=np.str_)
        classes=np.array(sorted(set(ytrain.tolist())),dtype=np.str_)
        if len(classes)<2:raise ValueError('GAMER_TRAIN_INSUFFICIENT_LABEL_CLASSES:'+name)
        for split in ('dev','test'):
            unknown=set(r[label] for r in parts[split])-set(classes.tolist())
            if unknown:raise ValueError('GAMER_TRAIN_UNSEEN_EVAL_CLASS:'+name+':'+str(unknown))
        model=_new_model(seed=seed+len(classifiers),alpha=alpha)
        # Each epoch fits TRAIN ONLY. DEV/TEST are read strictly for measurement.
        for epoch in range(epochs):
            if epoch==0:model.partial_fit(inputs['train'],ytrain,classes=classes)
            else:model.partial_fit(inputs['train'],ytrain)
        classifiers[name]=model
        classes_map[name]=classes.tolist()
        metrics[name]={s:_accuracy(model,inputs[s],np.array([r[label] for r in parts[s]],dtype=np.str_),target=name)
                       for s in ('train','dev','test')}
    count=Counter((r['split'],r['catalog_only']) for r in rows)
    # A second, separate model is evaluated on entire games absent from its
    # training. The published checkpoint still learns ALL 229 games; nothing
    # is held out permanently. This is diagnostic, NOT its own success score.
    from tools import nanojev_gamer_v1_prompt_db_query as blind_probe
    heldout=blind_probe._select_heldout_games(rows,seed=seed)
    diagnostic_fit=[r for r in rows if r['split']=='train' and r['game'] not in heldout]
    diagnostic_test=[r for r in rows if r['game'] in heldout and
                     (r['split']=='test' and not r['catalog_only'] or
                      r['split']=='dev' and r['catalog_only'])]
    if {r['game'] for r in diagnostic_fit} & {r['game'] for r in diagnostic_test}:
        raise ValueError('GAMER_TRAIN_GAME_DISJOINT_PROBE_LEAK')
    train_features={r['text'] for r in diagnostic_fit}
    if any(r['text'] in train_features for r in diagnostic_test):
        # Do not certify a probe with identical feature templates.
        raise ValueError('GAMER_TRAIN_GAME_DISJOINT_PROBE_FEATURE_OVERLAP')
    diagnostic_scores=blind_probe._fit_probe(diagnostic_fit,diagnostic_test,seed=seed,epochs=epochs,
                                              redact=False,registry=registry,
                                              certified_family_only=True)
    game_disjoint_probe={
        'training_games':len({r['game'] for r in diagnostic_fit}),
        'heldout_games':len(heldout),
        'certified_evaluation_games':len({r['game'] for r in diagnostic_test if not r['catalog_only']}),
        'catalog_evaluation_games':len({r['game'] for r in diagnostic_test if r['catalog_only']}),
        'evaluation_feature_overlap':0,
        'separate_diagnostic_model_not_saved_checkpoint':True,
        'trained_on_title_and_family_cue_blind_features':True,
        'metrics':diagnostic_scores,
    }
    per_game={}
    for row,pr in zip(objectives['test'],classifiers['program'].predict(objective_features['test'])):
        rec=per_game.setdefault(row['game'],{'tested':0,'correct':0})
        rec['tested']+=1
        rec['correct']+=int(str(pr)==row['program'])
    imperfect=sorted(({'game':g,**v} for g,v in per_game.items() if v['correct']!=v['tested']),
                    key=lambda v:(v['correct']/v['tested'],v['game']))

    report=dict(schema=SCHEMA,status='COMPLETE_ISOLATED_GAME_PLANNING_SMOKE_TRAIN',
                model_type='HASHED_TEXT_LINEAR_MULTI_TASK_ROUTER',
                trained_artifact='ISOLATED_AUXILIARY_STRATEGY_ROUTER_NOT_QWEN_OR_LORA',
                database=str(db), database_sha256=db_sha,
                seed=seed,epochs=epochs,alpha=alpha,feature_dimensions=FEATURES,
                source_games=verified['games'],source_game_families=verified['families'],
                certified_games=verified['games']-verified['game_scopes']['catalog_only'],
                catalog_only_games=verified['game_scopes']['catalog_only'],
                training_rows=len(datasets['train']),dev_rows=len(datasets['dev']),test_rows=len(datasets['test']),
                splits={'train_certified':count['train',False],'dev_certified':count['dev',False],
                        'test_certified':count['test',False], 'catalog_train':count['train',True],
                        'catalog_dev':count['dev',True], 'catalog_test':count['test',True]},
                metrics=metrics,label_classes={k:len(v) for k,v in classes_map.items()},
                input_feature_contract=FEATURE_SCHEMA,
                game_disjoint_probe=game_disjoint_probe,
                known_certified_game_program_recall={
                    'games':len(per_game),'perfect_games':len(per_game)-len(imperfect),
                    'imperfect_games':len(imperfect),'first_imperfect':imperfect[:20],
                    'evidence':'SAME_GAME_TEMPLATE_RECALL_ONLY'},
                source_title_cue_count=len(cue_titles),
                source_family_name_cue_count=len(cue_family_names),
                catalog_only_family_recognition_certified=False,
                certified_test_masked_feature_template_overlap=template_overlap,
                certified_test_masked_feature_template_cases=len(test_cert),
                certified_test_is_independent_generalization_evidence=(template_overlap==0),
                original_test_metrics_scope='SAME_GAME_TEMPLATE_RECALL_NOT_NEW_GAME_GENERALIZATION',
                source_labels='certified factor-step operation sequences and registry family/scope; catalog has no numeric target',
                no_c0_training=True,no_lora_training=True,no_wide_training=True,
                active_exact_numerical_authority_unchanged=True,
                numerical_training_on_catalog_only=False,
                limitations=('Direct game and family name cues and catalog formulas excluded from model features. The known-game test still overlaps training templates and does not prove independent strategic generalization. Catalog family recognition is not certified without game rules. Auxiliary game-family/scope/objective-program selection only; not Qwen/LoRA fine-tuning, '
                             'not full-game strategic reasoning, not numerical computation and not production promotion. '
                             'Catalog dev uses the second prompt for each game and has no independent catalog test.'),
                promotion='BLOCKED_DIAGNOSTIC_ONLY')
    if _digest(db)!=db_sha:
        raise ValueError('GAMER_TRAIN_DB_MUTATED_DURING_TRAINING')
    train_output.parent.mkdir(parents=True,exist_ok=True)
    # Do not overwrite any existing training run, even on failures.
    train_output.mkdir(parents=False,exist_ok=False)
    try:
        checkpoint=train_output/'strategy_router.npz'
        report['checkpoint_sha256']=_checkpoint(checkpoint,classifiers,vectorizer,
                                                {'db_sha256':db_sha,'classes':classes_map,'seed':seed,'epochs':epochs,
                                                 'input_feature_contract':FEATURE_SCHEMA,
                                                 'cue_titles':cue_titles,'cue_family_names':cue_family_names})
        report['checkpoint']=str(checkpoint)
        report_path=train_output/'final_report.json'
        report_path.write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    except BaseException:
        # Clean up uncommitted artifacts only, never source database or active checkpoints.
        import shutil
        shutil.rmtree(train_output)
        raise
    return report


def score_checkpoint(checkpoint: Path, prompt: str, *, report: Path | None = None):
    """Inference through saved unpickled weights; never asserts numeric certification."""
    import numpy as np
    from sklearn.feature_extraction.text import HashingVectorizer
    checkpoint=Path(checkpoint)
    if not checkpoint.is_file():
        raise FileNotFoundError('GAMER_TRAIN_CHECKPOINT_MISSING:'+str(checkpoint))
    if report is not None:
        receipt=json.loads(Path(report).read_text(encoding='utf-8'))
        if receipt.get('schema') not in (SCHEMA,LEGACY_SCHEMA) or receipt.get('checkpoint_sha256')!=_digest(checkpoint):
            raise ValueError('GAMER_TRAIN_CHECKPOINT_FINGERPRINT_MISMATCH')
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('GAMER_TRAIN_EMPTY_INFERENCE_PROMPT')
    vec=HashingVectorizer(n_features=FEATURES,ngram_range=(1,2),lowercase=True,
                          alternate_sign=False,norm='l2',binary=True)
    results={}
    with np.load(checkpoint,allow_pickle=False) as bundle:
        schema=str(bundle['schema'])
        if schema not in (SCHEMA, LEGACY_SCHEMA) or int(bundle['n_features'])!=FEATURES:
            raise ValueError('GAMER_TRAIN_INCOMPATIBLE_CHECKPOINT')
        if report is not None and receipt['schema'] != schema:
            raise ValueError('GAMER_TRAIN_CHECKPOINT_REPORT_SCHEMA_MISMATCH')
        if schema == SCHEMA:
            meta=json.loads(str(bundle['meta_json']))
            if meta.get('input_feature_contract')!=FEATURE_SCHEMA:
                raise ValueError('GAMER_TRAIN_FEATURE_CONTRACT_MISSING')
            clean=deconfound.blind_text(prompt,meta['cue_titles'],meta['cue_family_names'])
        else:
            clean=_sanitize(prompt)
        x=vec.transform([clean])
        for name in ('family','scope','program'):
            classes=bundle[name+'_classes']
            weights=bundle[name+'_coef']
            bias=bundle[name+'_intercept']
            if len(classes)!=weights.shape[0] or weights.shape[1]!=FEATURES or bias.shape!=(len(classes),):
                raise ValueError('GAMER_TRAIN_BAD_CHECKPOINT_SHAPE:'+name)
            logits=np.asarray(x@weights.T).reshape(-1)+bias
            results[name]=str(classes[int(logits.argmax())])
    if results['scope']=='catalog_only':results['program']=None
    return {'schema':schema,'status':'ADVISORY_UNCERTIFIED_PROMPT_ROUTING',
            'family':results['family'],'scope':results['scope'],
            'operator_pattern':results['program'],
            'numerical_answer':None,'production_authority_changed':False,
            'checkpoint_sha256':_digest(checkpoint),
            'input_feature_contract':FEATURE_SCHEMA if schema==SCHEMA else 'LEGACY_GAME_TITLE_AND_FAMILY_CUES_UNCONTROLLED',
            'numeric_payoff_sensitivity':False}
