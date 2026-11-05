"""Permanent read-only Gamer v1 diagnostic acceptance tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.nanojev_gamer_v1_diagnostic import diagnose, format_report, _deck_sha, main
from tools.nanojev_gamer_v1_handoff import fast_probe_deck


def _rows():
    rows = []
    for category, base in [('native', 'base'), ('catalog', 'added'), ('transition', 'state')]:
        for i in range(15):
            row = {'game_id': f'{base}_{i:02}', 'split': 'dev',
                   'prompt_08': f'Question for {category} {i}',
                   'math_prompt': f'| Step | Value |\n|---|---|\n| {category} {i} | – |',
                   'table_families': ['calculation'],
                   'bounded_catalog_objective': category == 'catalog',
                   'bounded_transition_objective': category == 'transition'}
            if category == 'transition':
                row['table_families'].append('state_transition')
            rows.append(row)
    return rows


def _make_run(tmp_path, step=800, matches=None):
    torch = pytest.importorskip('torch')
    run = tmp_path/'transition-005'
    (run/'lora').mkdir(parents=True)
    rows = _rows()
    rows[0]['split'] = 'train'
    rows[1]['split'] = 'test'
    source = ''.join(json.dumps(row, sort_keys=True)+'\n' for row in rows)
    (run/'certified_pairs.jsonl').write_text(source, encoding='utf-8')
    h = hashlib.sha256(source.encode()).hexdigest()
    (run/'handoff_manifest.json').write_text(json.dumps({
        'dataset_sha256':h, 'number_of_verified_games':len(rows),
        'number_of_cases':len(rows),'by_split':{'train':1,'dev':len(rows)-2,'test':1},
        'provenance':'source_recomputed','fourth_table_family':'state_transition'}))
    state = {'phase':'lora','step':step,'dataset_sha256':h,'pending_eval':False,
             'last_step_loss': 1.078, 'best_step':0,'best_score':-1.,
             'history': [], 'sampling_counts': {'named_replay':40},
             'hyperparameters': {'lora_eval_every':200,'lora_probe_cases':17,'max_new_tokens':2048},
             'bootstrap': {'source_run':'catalog-004','source_checkpoint_step':795,'checkpoint_kind':'latest'}}
    torch.save(state, run/'lora'/'latest.pt')
    dev = [row for row in rows if row['split']=='dev']
    deck, anchors = fast_probe_deck(dev,budget=17,evaluation_index=(step-1)//200)
    matches = matches if matches is not None else [i < 2 for i in range(17)]
    (run/'lora'/f'eval_step_{step:07d}.json').write_text(json.dumps({
        'schema':'nanojev.gamer.lora.eval-progress.v1',
        'key':'a'*64,'deck_sha256':_deck_sha(deck),
        'matches': matches, 'target_bounded':True,
        'max_length':1024,'max_new_tokens':2048
    }))
    return run,deck,anchors


def test_live_diagnostic_exact_mapping_and_read_only(tmp_path):
    run, deck, anchors = _make_run(tmp_path)
    before = {p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file()}
    d = diagnose(run)
    assert d['status'] == 'CHECKPOINTED_ACTIVE_OR_INTERRUPTED'
    assert d['checkpoint']['step']==800
    assert d['checkpoint']['source_step']==795
    assert d['dataset']['manifest_hash_matches']
    e = d['latest_evaluation']
    assert e['correct']==2 and e['evaluated']==17 and e['expected']==17 and e['complete']
    assert e['anchors']['expected']==anchors
    assert e['by_group']['native']['evaluated'] >= 1
    assert e['by_group']['catalog']['evaluated'] >= 1
    assert e['by_group']['transition']['evaluated'] >= 1
    assert e['by_table']['state_transition']['evaluated'] >= 1
    assert any('LOW_BOUNDED_PROBE_ACCURACY' in s for s in d['warnings'])
    assert 'NOT ESTABLISHED' in format_report(d)
    after = {p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file()}
    assert before == after


def test_partial_probe_and_pending_eval(tmp_path):
    torch = pytest.importorskip('torch')
    run,deck,anchors=_make_run(tmp_path, matches=[True,False])
    ck = torch.load(run/'lora'/'latest.pt',map_location='cpu',weights_only=True)
    ck['pending_eval']=True
    torch.save(ck,run/'lora'/'latest.pt')
    result=diagnose(run)
    e=result['latest_evaluation']
    assert e['evaluated']==2 and e['expected']==17 and not e['complete']
    assert result['checkpoint']['pending_evaluation']
    assert any('pending' in x.lower() for x in result['warnings'])


def test_wrong_deck_hash_not_misattributed(tmp_path):
    run,deck,anchors=_make_run(tmp_path)
    path=run/'lora'/'eval_step_0000800.json'
    payload=json.loads(path.read_text())
    payload['deck_sha256']='f'*64
    path.write_text(json.dumps(payload))
    result=diagnose(run)
    assert not result['latest_evaluation']['deck_verified']
    assert result['latest_evaluation']['by_game']=={}
    assert result['latest_evaluation']['by_table']=={}
    assert any('attribution withheld' in x for x in result['warnings'])


def test_dataset_tampering_detected(tmp_path):
    run,deck,anchors=_make_run(tmp_path)
    with (run/'certified_pairs.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({'game_id':'fake','split':'train','prompt_08':'tamper'})+'\n')
    result=diagnose(run)
    assert result['dataset']['manifest_hash_matches'] is False
    assert any('HASH MISMATCH' in x for x in result['warnings'])


def test_missing_and_unavailable_checkpoint_dont_fabricate_steps(tmp_path,monkeypatch):
    run,deck,anchors=_make_run(tmp_path)
    (run/'lora'/'latest.pt').unlink()
    result=diagnose(run)
    assert result['checkpoint'] is None
    assert result['status']=='NO_TRAINING_CHECKPOINT'
    assert result['latest_evaluation']['correct']==2
    assert result['latest_evaluation']['by_game']=={} # no guess at deck index
    assert 'Checkpoint: absent' in format_report(result)


def test_fixed_anchor_comparison_only(tmp_path):
    run,deck,anchors=_make_run(tmp_path, step=800)
    # Another evaluated step, with a different rotation but matching fixed anchors.
    dev = [json.loads(s) for s in (run/'certified_pairs.jsonl').read_text().splitlines()]
    dev = [r for r in dev if r['split']=='dev']
    prior, same = fast_probe_deck(dev, budget=17, evaluation_index=(600-1)//200)
    assert _deck_sha(deck[:anchors]) == _deck_sha(prior[:same])
    (run/'lora'/'eval_step_0000600.json').write_text(json.dumps({
        'key': 'a'*64,'deck_sha256': _deck_sha(prior), 'matches': [True]*17}))
    result=diagnose(run)
    history=result['comparable_anchor_history']
    assert [e['step'] for e in history]==[600,800]
    assert history[0]['correct'] == same
    assert history[1]['correct'] <= same


def test_cli_json_and_text(tmp_path, capsys):
    run, _, _ = _make_run(tmp_path)
    assert main(['--run',str(run),'--json'])==0
    payload=json.loads(capsys.readouterr().out)
    assert payload['checkpoint']['step']==800
    assert main(['--run',str(run)])==0
    assert 'Step' not in capsys.readouterr().out[:40]  # human-readable heading
