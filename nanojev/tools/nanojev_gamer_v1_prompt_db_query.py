#!/usr/bin/env python3
"""Read-only GAMER prompt-router queries and game-disjoint anti-leakage audit.

The existing reported 438/438 program result is on *masked training templates*.
This tool exposes that overlap and evaluates an independently fitted probe on
entire games absent from the probe's training split. It never promotes or trains
LoRA, C0, Wide, the production router, or the numerical authority.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import hashlib
import json
import random
import re
import sqlite3

from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_prompt_db_train as trainer
from tools import nanojev_gamer_v1_prompt_db_deconfound as deconfound
from tools import nanojev_gamer_v1_strategic_execution as strategic

AUDIT_SCHEMA = 'nanojev.gamer.v1.sqlite-router-game-disjoint-audit.v1'
QUERY_SCHEMA = 'nanojev.gamer.v1.sqlite-router-blind-query.v1'


def _load_lineage(db: Path, train_output: Path):
    db = Path(db).resolve()
    train_output = Path(train_output).resolve()
    checkpoint = train_output / 'strategy_router.npz'
    report = train_output / 'final_report.json'
    if not report.is_file():
        raise FileNotFoundError('GAMER_QUERY_TRAIN_REPORT_MISSING:' + str(report))
    receipt = json.loads(report.read_text(encoding='utf-8'))
    if (receipt.get('schema') not in (trainer.SCHEMA,trainer.LEGACY_SCHEMA)
            or receipt.get('checkpoint_sha256') != trainer._digest(checkpoint)
            or receipt.get('database_sha256') != trainer._digest(db)):
        raise ValueError('GAMER_QUERY_SOURCE_OR_CHECKPOINT_FINGERPRINT_MISMATCH')
    return db, checkpoint, report, receipt


def template_overlap(rows):
    """A prompt match after the EXACT same number mask used during training."""
    train = [r for r in rows if r['split'] == 'train']
    test = [r for r in rows if r['split'] == 'test' and not r['catalog_only']]
    templates = {r['text'] for r in train}
    original_games = {r['game'] for r in train}
    overlaps = [r for r in test if r['text'] in templates]
    return {
        'certified_test_cases': len(test),
        'masked_test_template_matches_training': len(overlaps),
        'masked_test_template_match_rate': len(overlaps) / len(test) if test else 0.0,
        'test_games_also_present_in_training': len({r['game'] for r in test} & original_games),
        'distinct_certified_test_games': len({r['game'] for r in test}),
        'source_of_overlap': 'SAME_NUMBER_MASK_USED_BY_TRAINER',
        'interpretation': ('The original held-out split tests novel numbers on familiar prompt '
                           'templates and games. It does not measure new-game generalization.'),
    }


def query(db: Path, train_output: Path, prompt: str):
    """Run saved weights on an unlabeled prompt. SQLite is used AFTER prediction only.

    No source_case_json, numeric_answer, math_target, program, or game family
    from the database is passed to checkpoint inference.
    """
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16384:
        raise ValueError('GAMER_QUERY_PROMPT_MUST_BE_1_TO_16384_CHARACTERS')
    db, checkpoint, receipt_path, receipt = _load_lineage(db, train_output)
    prediction = trainer.score_checkpoint(checkpoint, prompt, report=receipt_path)
    sanitized = trainer._sanitize(prompt)
    if receipt['schema']==trainer.SCHEMA:
        import numpy as np
        with np.load(checkpoint,allow_pickle=False) as bundle:
            meta=json.loads(str(bundle['meta_json']))
        features=lambda text: deconfound.blind_text(text,meta['cue_titles'],meta['cue_family_names'])
    else:
        features=trainer._sanitize
    feature_prompt=features(prompt)
    with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        training = conn.execute("SELECT game_id,prompt FROM prompts WHERE split='train'").fetchall()
    exact_matches = [(game, text) for game, text in training if text == prompt]
    template_matches = [(game, text) for game, text in training
                        if trainer._sanitize(text) == sanitized]
    feature_matches = [(game, text) for game, text in training
                       if features(text) == feature_prompt]
    execution = strategic.execute(prompt)
    solved = execution['status'] == 'BOUNDED_EXACT_TWO_PLAYER_DECISION_COMPUTED'
    return {
        'schema': QUERY_SCHEMA,
        'status': ('BOUNDED_EXACT_STRATEGIC_EXECUTION_NOT_NEURAL_CERTIFICATION' if solved
                   else 'UNLABELLED_ADVISORY_QUERY_NOT_STRATEGIC_CERTIFICATION'),
        'prediction': {
            'family': prediction['family'],
            'scope': prediction['scope'],
            'operator_pattern': prediction['operator_pattern'],
            'numerical_answer': None,
            'strategic_decision': None,  # Classifier does not make game decisions.
        },
        'novelty': {
            'exact_training_prompt_matches': len(exact_matches),
            'masked_training_template_matches': len(template_matches),
            'matched_train_games': sorted({g for g, _ in template_matches}),
            'is_new_masked_template': not bool(template_matches),
            'model_feature_training_matches':len(feature_matches),
            'is_new_model_feature_template':not bool(feature_matches),
        },
        'lineage': {
            'checkpoint_sha256': prediction['checkpoint_sha256'],
            'database_sha256': receipt['database_sha256'],
        },
        'numerical_values_masked_as_in_training': True,
        'direct_game_title_and_family_cues_removed':receipt['schema']==trainer.SCHEMA,
        'numeric_payoff_sensitive_decision_produced': solved,
        'strategic_decision': execution['strategic_decision'],
        'decision_source': 'bounded_deterministic_solver_not_classifier_weights' if solved else None,
        'strategic_execution': execution,
        'inference_reads_only_checkpoint_weights': True,
        'database_used_after_prediction_for_novelty_only': True,
        'production_authority_changed': False,
        'limitation': ('The router is still a number-masked text classifier. Any strategic decision '
                       'is produced by an explicitly scoped deterministic payoff parser and the GAMER '
                       'exact source factor-DAG arithmetic, NOT by trained neural reasoning or the '
                       'production to_numerical serving head. Unsupported inputs have no decision.'),
    }


def _select_heldout_games(rows, *, seed=47, fraction=0.20):
    """Hold out entire games, while retaining every scored class in the fit.

    Deterministic selection is built from labels in TRAIN only. Original TEST
    labels are never used for selection or fitting. Catalog scopes are tested
    through one withheld catalog game, never numerical labels.
    """
    train = [r for r in rows if r['split'] == 'train']
    classes_by_game = defaultdict(set)
    for r in train:
        classes_by_game[r['game']].add(('family', r['family']))
        classes_by_game[r['game']].add(('scope', r['scope']))
        if r['program'] is not None:
            classes_by_game[r['game']].add(('program', r['program']))
    support = Counter(c for labels in classes_by_game.values() for c in labels)
    games = sorted(classes_by_game)
    order = games.copy()
    random.Random(seed).shuffle(order)
    target = max(1, int(round(len(games) * fraction)))
    heldout = set()
    for game in order:
        if len(heldout) >= target:
            break
        if all(support[c] >= 2 for c in classes_by_game[game]):
            heldout.add(game)
            for c in classes_by_game[game]:
                support[c] -= 1
    if len(heldout) < 2 or not any(any(r['game']==g and r['program'] is not None for r in train) for g in heldout):
        raise ValueError('GAMER_AUDIT_INSUFFICIENT_GROUP_HOLDOUT')
    return heldout


def _blind_text(text, game, registry):
    """Ablation of explicit game title / family name, never reads target labels."""
    title, family, _scope = registry[game]
    family_name = smoke.FAMILY_MODELS[family][0]
    for cue in sorted({title, family_name}, key=len, reverse=True):
        if cue:
            text = re.sub(re.escape(cue), ' CUE_REDACTED ', text, flags=re.I)
    return text


def _fit_probe(training, evaluation, *, seed, epochs, redact, registry, certified_family_only=False):
    """Fresh independent models. No existing checkpoint is changed."""
    import numpy as np
    from sklearn.feature_extraction.text import HashingVectorizer

    vectorizer = HashingVectorizer(n_features=trainer.FEATURES, ngram_range=(1,2),
                                  lowercase=True, alternate_sign=False, norm='l2', binary=True)
    def text_of(rows):
        return [(_blind_text(r['text'], r['game'], registry) if redact else r['text']) for r in rows]
    results = {}
    for index, name in enumerate(('family','scope','program')):
        fit = [r for r in training if (name != 'program' or r['program'] is not None)
               and (not certified_family_only or name!='family' or not r['catalog_only'])]
        test = [r for r in evaluation if (name != 'program' or r['program'] is not None)
                and (not certified_family_only or name!='family' or not r['catalog_only'])]
        if not fit or not test:
            raise ValueError('GAMER_AUDIT_EMPTY_GROUP_TASK:' + name)
        classes = np.array(sorted({r[name] for r in fit}), dtype=np.str_)
        unsupported = sorted({r[name] for r in test} - set(classes.tolist()))
        if unsupported:
            raise ValueError('GAMER_AUDIT_UNSUPPORTED_GAME_HELDOUT_CLASSES:' + name)
        x = vectorizer.transform(text_of(fit))
        y = np.asarray([r[name] for r in fit], dtype=np.str_)
        model = trainer._new_model(seed=seed+index, alpha=1e-5)
        for epoch in range(epochs):
            if epoch == 0: model.partial_fit(x,y,classes=classes)
            else: model.partial_fit(x,y)
        xt = vectorizer.transform(text_of(test))
        pred = model.predict(xt)
        gold = np.asarray([r[name] for r in test], dtype=np.str_)
        correct = int((pred == gold).sum())
        results[name] = {
            'cases': len(test), 'correct': correct, 'accuracy': correct / len(test),
            'distinct_evaluation_games': len({r['game'] for r in test}),
            'labels_never_passed_as_features': True,
        }
    return results


def audit(db: Path, train_output: Path, *, seed=47, epochs=8, report_path=None):
    """Honest original-split audit plus fresh game-disjoint model evaluation."""
    if not 1 <= epochs <= 100:
        raise ValueError('GAMER_AUDIT_EPOCHS_OUT_OF_RANGE')
    db, checkpoint, _receipt_path, receipt = _load_lineage(db, train_output)
    rows, verified = trainer.load_training_rows(db, full_oracle_verify=True)
    overlaps = template_overlap(rows)
    registry = smoke.source_registry()
    heldout = _select_heldout_games(rows, seed=seed)
    fit = [r for r in rows if r['split']=='train' and r['game'] not in heldout]
    # Retain original test boundary, but exclude ALL training records of held-out games.
    unseen = [r for r in rows if r['game'] in heldout
              and ((r['split']=='test' and not r['catalog_only'])
                   or (r['split']=='dev' and r['catalog_only']))]
    if not unseen or ({r['game'] for r in unseen} & {r['game'] for r in fit}):
        raise ValueError('GAMER_AUDIT_GAME_LEAKAGE')
    tr_templates = {r['text'] for r in fit}
    probe = {
        'training_games': len({r['game'] for r in fit}),
        'heldout_games': len(heldout),
        'heldout_certified_games': len({r['game'] for r in unseen if not r['catalog_only']}),
        'heldout_catalog_games': len({r['game'] for r in unseen if r['catalog_only']}),
        'training_rows': len(fit), 'evaluated_rows': len(unseen),
        'exact_masked_template_overlap': sum(r['text'] in tr_templates for r in unseen),
        'no_training_examples_from_evaluation_games': True,
        'selection_policy': 'DETERMINISTIC_GAME_DISJOINT_KEEP_EVERY_TRAIN_LABEL_CLASS',
        'ordinary_text': _fit_probe(fit, unseen, seed=seed, epochs=epochs,
                                    redact=False, registry=registry),
        'game_and_family_cue_redacted': _fit_probe(fit, unseen, seed=seed,
                                    epochs=epochs, redact=True, registry=registry),
    }
    result = {
        'schema': AUDIT_SCHEMA,
        'status': 'COMPLETE_READ_ONLY_NONCERTIFYING_LEAKAGE_AUDIT',
        'source_games': verified['games'],
        'checkpoint_sha256': trainer._digest(checkpoint),
        'database_sha256': receipt['database_sha256'],
        'original_reported_certified_test_program_accuracy': receipt['metrics']['program']['test']['accuracy'],
        'original_reported_certified_test_family_accuracy': receipt['metrics']['family']['test']['accuracy'],
        'original_split': overlaps,
        'independent_game_holdout_probe': probe,
        'does_not_certify_full_game_decisions': True,
        'no_lora_or_c0_or_wide_or_numerical_authority_training': True,
        'no_original_checkpoint_modification': True,
        'conclusion': ('The old test reuses identical number-masked templates and the same game IDs. '
                       'The independent game-level probe measures a stricter but still bounded task. '
                       'Neither result is evidence of full strategic decision correctness.'),
    }
    # For the cue-blind v2 checkpoint, separately report exact feature
    # overlap; unlike the old masked text, this also removes names/equations.
    # Do not alter legacy audit output or invalidate already written reports.
    if receipt['schema']==trainer.SCHEMA:
        import numpy as np
        with np.load(checkpoint,allow_pickle=False) as bundle:
            meta=json.loads(str(bundle['meta_json']))
        t=lambda r:deconfound.blind_text(r['text'],meta['cue_titles'],meta['cue_family_names'])
        known={t(r) for r in rows if r['split']=='train'}
        tests=[r for r in rows if r['split']=='test' and not r['catalog_only']]
        result['cue_blind_checkpoint']={
            'feature_contract':deconfound.SCHEMA,
            'masked_test_template_matches_training':sum(t(r) in known for r in tests),
            'certified_test_cases':len(tests),
            'interpretation':'Known-template recall only; not novel game generalization or strategic decision correctness.',
            'no_numeric_decision_claim':True,
            'no_catalog_family_claim':True,
        }
    if report_path is not None:
        path = Path(report_path)
        # Require a separate location from the immutable training checkpoint.
        if path.resolve().is_relative_to(Path(train_output).resolve()):
            raise ValueError('GAMER_AUDIT_REPORT_INSIDE_IMMUTABLE_TRAIN_RUN')
        if path.exists():
            # Repeat the audit with the requested seed/epochs before trusting a
            # previous report. Reuse only if ALL results and source digests match.
            # An existing report from another experiment is never overwritten.
            try:
                existing = json.loads(path.read_text(encoding='utf-8'))
            except (UnicodeError, ValueError, OSError) as exc:
                raise ValueError('GAMER_AUDIT_REPORT_EXISTING_INVALID:' + str(path)) from exc
            if existing != result:
                raise FileExistsError('GAMER_AUDIT_REPORT_DIFFERENT_RESULT_USE_NEW_PATH:' + str(path))
            result['report_reused_verified_identical'] = True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open('x', encoding='utf-8') as out:
                    out.write(json.dumps(result, indent=2, sort_keys=True,
                                         ensure_ascii=False, allow_nan=False)+'\n')
            except FileExistsError:
                # Fail closed on concurrent writers; do not claim this run
                # verified a report written after our existence check.
                raise FileExistsError('GAMER_AUDIT_REPORT_RACE_USE_NEW_PATH:' + str(path))
            result['report_reused_verified_identical'] = False
        result['report_path'] = str(path)
    return result
