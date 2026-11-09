"""Blind 15/5 T08 language-head experiment: anti-leakage and model intervention."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from tools import nanojev_gamer_v1_hawk_dove_t08_language as lang
from tools import nanojev_gamer_v1_hawk_dove_t08_corpus as corpus
from tools import nanojev_gamer_v1_hawk_dove_acquisition as game
from tools import nanojev_hawk_dove_million as source


def game_head(tmp_path):
    p=tmp_path/'hawk_head'
    p.mkdir()
    state={'schema':game.SCHEMA,
           'weights':{'HAWK_HAWK_PAYOFF':'-1','HAWK_DOVE_PAYOFF':'3',
                      'DOVE_HAWK_PAYOFF':'0','DOVE_DOVE_PAYOFF':'3/2'},
           'source_code_sha256':game.sha_file(Path(source.__file__))}
    ck=p/'hawk_dove_head.json'
    ck.write_text(game.canonical(state)+'\n')
    (p/'final_report.json').write_text(json.dumps({'checkpoint_sha256':game.sha_file(ck)}))
    return p


def test_census_has_15_training_5_unseen_templates_with_disjoint_count_pairs():
    rows=corpus.generate(seed=47,samples_per_template=16)
    assert len(corpus.TRAIN_TEMPLATES)==15 and len(corpus.HOLDOUT_TEMPLATES)==5
    assert len(rows)==320
    a=[r for r in rows if r['split']=='train']
    b=[r for r in rows if r['split']=='holdout']
    assert len(a)==240 and len(b)==80
    assert not set(corpus.TRAIN_TEMPLATES)&set(corpus.HOLDOUT_TEMPLATES)
    assert not set(r['hawk_count'] for r in a)&set(r['hawk_count'] for r in b)
    assert {r['template_id'] for r in a}==set(range(15))
    assert {r['template_id'] for r in b}==set(range(5))
    for r in rows:
        spans=lang.spans_from_prompt(r['prompt'])
        assert len(spans)==2
        assert all(span[2] is None for span in spans)
        assert [span[3] for span in spans]==[span[3] for span in r['spans']]


def test_holdouts_not_recognized_by_old_hardcoded_source_parser():
    rows=[r for r in corpus.generate(seed=47,samples_per_template=16) if r['split']=='holdout']
    assert lang._old_parser_coverage(rows)=={'accepted':0,'cases':80}


def test_no_action_name_logic_in_generic_numeric_adapter():
    prompt='Red team observed 940,000 calls; blue team observed 60,000 calls.'
    spans=lang.spans_from_prompt(prompt)
    assert len(spans)==2 and [int(s[3].replace(',','')) for s in spans]==[940000,60000]
    assert all(s[2] is None for s in spans)


@pytest.mark.parametrize('prompt',[  # refuses unsupported domains, not guesses
    'Hawk 94 percent and Dove 6 percent.',
    'Hawk 300000 Dove 600000.',
    'Hawk 940000 Dove 60000 and total 1000000.',
    'Hawk -940000 Dove 1940000.',
    'Hawk 940000 Dove 60000 other 0.',
    'Hawk 0.5 Dove 999999.5.',
    'Hawk 1000000% Dove 0%.',
    '',
])
def test_rejects_not_two_integer_counts_summing_to_million(prompt):
    with pytest.raises(ValueError):lang.spans_from_prompt(prompt)


def test_generator_has_actual_hawk_dove_reversed_orders():
    rows=corpus.generate(seed=47,samples_per_template=16)
    for split in ('train','holdout'):
        labels={r['first_is_hawk'] for r in rows if r['split']==split}
        assert labels=={0,1}


def test_independent_math_matches_learned_payoff_head(tmp_path):
    g=game_head(tmp_path)
    weights,_=lang._game_checkpoint(g)
    for a,expected in [(599999,'HAWK'),(600000,'TIE'),(600001,'DOVE'),(940000,'DOVE')]:
        prompt=corpus._render('Opponent Dove [[D]] times; Hawk [[H]] times.',a,1000000-a)['prompt']
        result=lang._decision_for_row(prompt,0,weights)
        assert result['decision']==expected
        from fractions import Fraction
        assert Fraction(result['hawk_cumulative_payoff']) == -a+3*(1000000-a)
        assert Fraction(result['dove_cumulative_payoff']) == Fraction(3*(1000000-a),2)
        assert result['opponent_hawk_count']==a
        assert result['opponent_dove_count']==1000000-a


def test_end_to_end_trained_text_head_reports_failures_honestly_and_queries_without_teacher(tmp_path,monkeypatch):
    g=game_head(tmp_path)
    out=tmp_path/'language'
    report=lang.train(language_output=out,hawk_dove_output=g,run=tmp_path,
                      model_id='none',device='cpu',layer=9,encoder='text',
                      epochs=60,seed=47,samples_per_template=16,max_length=256)
    assert report['training_templates']==15 and report['unseen_holdout_templates']==5
    assert report['training_cases']==240 and report['holdout_cases']==80
    assert report['holdout']['cases']==80
    assert report['shuffled_label_control']['cases']==80
    assert report['position_only_baseline']['cases']==80
    assert report['source_t08_strict_parser_coverage_on_holdouts']=={'accepted':0,'cases':80}
    assert report['train_holdout_count_pairs_disjoint']
    assert report['checkpoint_sha256']==lang.sha(out/'language_head.json')
    if report['status'].startswith('PASS_'):
        assert report['holdout']['role_accuracy']>=0.95
        assert report['holdout']['boundary_exact']==report['holdout']['boundary_cases']
        assert report['holdout']['exact_roles']>report['position_only_baseline']['correct']
    else:
        assert report['status']=='FAIL_T08_LANGUAGE_HEAD_DID_NOT_BEAT_CONTROLS'
    monkeypatch.setattr(game,'_teacher',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('TEACHER_INFERENCE')))
    monkeypatch.setattr(source,'oracle',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('ORACLE_INFERENCE')))
    prompt=corpus._render(corpus.TRAIN_TEMPLATES[0],940000,60000)['prompt']
    q=lang.query(language_output=out,hawk_dove_output=g,prompt=prompt,device='cpu')
    assert q['encoder']=='text' and q['result']['decision']=='DOVE'
    assert q['no_oracle_or_sqlite_access_during_query']
    assert q['language_holdout_gate_passed']==report['status'].startswith('PASS_')
    with pytest.raises(FileExistsError):
        lang.train(language_output=out,hawk_dove_output=g,run=tmp_path,
                   model_id='none',device='cpu',layer=9,encoder='text',
                   epochs=60,seed=47,samples_per_template=16,max_length=256)
    state=json.loads((out/'language_head.json').read_text())
    state['weights']=[-float(x) for x in state['weights']]
    state['intercept']=-state['intercept']
    (out/'language_head.json').write_text(lang.canonical(state))
    with pytest.raises(ValueError,match='TAMPERED'):
        lang.query(language_output=out,hawk_dove_output=g,prompt=prompt,device='cpu')
    # Intentional lineage-aware intervention: modified learned classifier flips
    # the assigned roles and therefore changes a payoff-sensitive choice.
    meta=json.loads((out/'final_report.json').read_text())
    meta['checkpoint_sha256']=lang.sha(out/'language_head.json')
    (out/'final_report.json').write_text(json.dumps(meta))
    swapped=lang.query(language_output=out,hawk_dove_output=g,prompt=prompt,device='cpu')
    assert swapped['first_numeric_role']!=q['first_numeric_role']
    assert swapped['result']['decision']!=q['result']['decision']


def test_frozen_qwen_feature_path_can_be_injected_without_teacher(tmp_path,monkeypatch):
    import numpy as np
    rows=corpus.generate(seed=47,samples_per_template=16)[:2]
    def dummy_capture(model_id,run,device,layer,rows,*,max_length):
        # simulated frozen features derived from input text but no labels
        features=[]
        for row in rows:
            for j,span in enumerate(lang.spans_from_prompt(row['prompt'])):
                features.append((np.array([float(j),float(len(span[3]))],dtype='float32'),None,span[3]))
        return features,'simulated_adapter_sha'
    monkeypatch.setattr('tools.nanojev_gamer_v1_construction_head._capture',dummy_capture)
    x,adapter,dim=lang._matrix(rows,encoder='frozen-qwen',run=tmp_path,
                               model_id='test',device='cpu',layer=9,max_length=256)
    assert adapter=='simulated_adapter_sha' and dim==2
    assert x.shape==(2,16386)


def test_language_inference_import_has_no_train_or_holdout_templates(tmp_path):
    # Independent process: the inference module must not import the corpus,
    # which owns the gold role labels, withheld templates and oracle.
    import subprocess
    import sys
    code=('import sys; '
          'from tools import nanojev_gamer_v1_hawk_dove_t08_language as inference; '
          "assert 'tools.nanojev_gamer_v1_hawk_dove_t08_corpus' not in sys.modules; "
          "assert not hasattr(inference, 'HOLDOUT_TEMPLATES'); "
          "assert not hasattr(inference, 'TRAIN_TEMPLATES')")
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,
                          cwd=str(Path(__file__).resolve().parents[1]))
    assert result.returncode==0,(result.stdout,result.stderr)
