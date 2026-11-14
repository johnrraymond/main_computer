"""Synthetic, CPU-only tests of read-only T01 training-ladder forensics."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import nanojev_hybridlink_t01_ladder_diagnostic as diag


def fixture(tmp_path):
    t00=tmp_path/'old_t00';t01=tmp_path/'new_t01';out=tmp_path/'analysis'
    t00.mkdir();t01.mkdir()
    (t00/'history.json').write_text(json.dumps([{'rung':'T00','step':100,'gate_pass':True,
        'dev':{'cases':12,'generated_exit_rate':.8},
        't00_retention':{'generated_exit_rate':.8}}]))
    history=[{'rung':'T01','step':50,'gate_pass':False,'gate_reasons':['T00_RETENTION_FAILED'],
         'dev':{'cases':12,'generated_exit_rate':.3,'early_false_stops':2,
                'mean_generated_to_gold_cosine':.4},
         't00_retention':{'cases':12,'generated_exit_rate':.3},
         'window_contains_answer':False},
        {'rung':'T01','step':100,'gate_pass':True,
         'dev':{'cases':12,'generated_exit_rate':.65},
         't00_retention':{'cases':12,'generated_exit_rate':.75}}]
    (t01/'history.json').write_text(json.dumps(history))
    (t01/'progress.json').write_text(json.dumps(history[-1]))
    (t01/'cases.jsonl').write_text('\n'.join(json.dumps(x) for x in [
      {'step':50,'rung':'T00','source':'PROBLEM: a','target':'ANSWER: 1','correct':True,'p_generated':.99},
      {'step':100,'rung':'T00','source':'PROBLEM: a','target':'ANSWER: 1','correct':False,'p_generated':.08},
      {'step':100,'rung':'T01','source':'PROBLEM: b','target':'ANSWER: 2','status':'ANSWER',
       'steps':1,'expected_steps':2,'correct_stop':False},
    ])+'\n')
    args=SimpleNamespace(t00_run=str(t00),t01_run=str(t01),t01_source=None,
      output=str(out),low_probability=.55,low_cosine=.7,max_files=100,max_bytes=1_000_000)
    return args,t00,t01,out


def test_end_to_end_retention_provenance_hard_bank(tmp_path):
    args,t00,t01,out=fixture(tmp_path)
    result=diag.inspect(args)
    assert result['status']=='diagnostic_only'
    assert not result['t01_source']['present']
    assert len(result['hard_cases'])==2
    codes={x['code'] for x in result['findings']}
    assert {'T00_EXIT_RETENTION_RISK','T00_RETENTION_GATE_FAILED','NON_ANSWER_ANCHORED_WINDOWS',
           'EARLY_FALSE_ANSWER_EXITS','CASE_LEVEL_RETENTION_RISK'}<=codes
    assert result['case_count']==3
    assert result['event_count']==5 # t00 100, t01 50/100, and 2 distinct per-case steps
    assert (out/'diagnostic.md').is_file()
    assert 'T01' in (out/'diagnostic.md').read_text()
    assert (out/'rung_metrics.csv').is_file()
    with sqlite3.connect(out/'hard_examples.sqlite3') as con:
        assert con.execute('select count(*) from hard_examples').fetchone()[0]==2
        assert con.execute("select count(*) from hard_examples where tags_json like '%inconsistent_or_regressed%'").fetchone()[0]==1
    # Read-only sources, never changed.
    assert json.loads((t01/'history.json').read_text())[0]['step']==50
    assert (t00/'history.json').is_file()


def test_no_artifacts_no_fabricated_pass(tmp_path):
    t=tmp_path/'empty';t.mkdir()
    args=SimpleNamespace(t00_run=None,t01_run=str(t),t01_source=None,output=str(tmp_path/'r'),
          low_probability=.55,low_cosine=.70,max_files=10,max_bytes=1_000_000)
    result=diag.inspect(args)
    assert result['rungs']==[]
    assert result['hard_cases']==[]
    assert 'T01_PROGRESS_NOT_VISIBLE' in {x['code'] for x in result['findings']}
    assert result['t01_per_case_coverage']=='not_present_in_artifacts'


def test_output_overlap_refused_before_creation(tmp_path):
    args,t00,t01,out=fixture(tmp_path)
    args.output=str(t01/'analysis')
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        diag.inspect(args)
    assert not (t01/'analysis').exists()


def test_ast_source_discovery_without_import(tmp_path):
    x=tmp_path/'nanojev_hybridlink_t01_retention_cutover.py'
    x.write_text("import argparse\ndef get():\n p=argparse.ArgumentParser()\n p.add_argument('--stage')\n p.add_argument('--checkpoint')\n return p\n")
    r=diag.source_audit(x)
    assert r['present'] and r['source_inspected_without_import']
    assert '--stage' in r['cli_options'] and '--checkpoint' in r['cli_options']
    assert len(r['sha256'])==64


def test_case_result_not_inferred_from_probability():
    raw={'source':'PROBLEM: 1','target':'ANSWER: 2','p_generated':.99,
         '_rung':'T00','_provenance':{'file':'x','label':'t00','step':1,'case_index':0}}
    r=diag.normalize_case(raw)
    assert r['correct'] is None
    assert r['probability']==.99


def test_parse_t00_metrics_without_a_t01_specific_schema():
    doc={'file':'history.json','label':'t00','data':[{'rung':'T00','step':25,
        'dev':{'correct_stop_depth':6,'cases':12,'generated_exit_rate':.5},
        't00_retention':{'generated_exit_rate':.5,'mean_intact_minus_zero_cosine':.2}}]}
    rows,cases=diag.extract_events(doc)
    assert len(rows)==1 and cases==[]
    assert rows[0]['current_correct_stop_depth']==6
    assert rows[0]['t00_mean_intact_minus_zero_cosine']==.2


def test_case_pair_is_rung_scoped_and_conflicting_history_mined():
    def c(rung,correct,step):
        return {'source':'PROBLEM: same','target':'ANSWER: 3','correct':correct,'_rung':rung,
            '_provenance':{'file':'z','label':'t01','step':step,'case_index':0}}
    cases=diag.build_atlas([c('T00',True,10),c('T00',False,20),c('T01',False,10)])
    assert len(cases)==2
    assert 'inconsistent_or_regressed' in next(x for x in cases if x['rung']=='T00')['tags']


def test_unknown_output_never_mislabels_success():
    s={'source':'p','target':'ANSWER: 3','status':'ANSWER',
       '_rung':'T01','_provenance':{'label':'t01','file':'x','step':1,'case_index':0}}
    assert diag.normalize_case(s)['correct'] is None


def test_truncated_or_invalid_files_report_skip(tmp_path):
    t=tmp_path/'t';t.mkdir()
    (t/'history.json').write_text('{not json')
    docs,skipped=diag.scan(t,'t01')
    assert docs==[] and skipped[0]['reason']=='JSONDecodeError'


def test_t01_retention_low_priority_unknown_when_missing():
    items=[{'label':'t01','rung':'T01','step':50,'current_generated_exit_rate':.5}]
    labels=[x['code'] for x in diag.findings(items,[])]
    assert 'T00_EXIT_RETENTION_RISK' not in labels
    assert 'PER_CASE_EVIDENCE_MISSING_OR_ALL_CLEAN' in labels


def test_real_t01_result_accepted_despite_prior_failed_steps(tmp_path):
    t00=tmp_path/'t00';t00.mkdir()
    t01=tmp_path/'t01';t01.mkdir()
    out=tmp_path/'atlas'
    history=[]
    cases=[(50,['PROBLEM: (-132300)+(-28589)'],11,.51785,.82746,False),
           (100,['PROBLEM: 2304+632'],11,.63927,.84710,False),
           (150,['PROBLEM: 2304+632'],11,.66540,.85049,False),
           (200,['PROBLEM: 2304+632'],11,.69408,.87426,False),
           (250,[],12,.73387,.88373,True)]
    for step,lost,current,first,final,gate in cases:
        history.append({'step':step,'train_mode':'T00_REPLAY' if step%100!=0 else 'T01_TWO_HOP',
            'gate_pass':gate,'gate_reasons':[] if gate else ['T00_RETENTION_REGRESSED'],
            'retention':{'baseline_cases':12,'baseline_correct':12,'current_correct':current,
              'lost_sources':lost,'gold_baseline_lost_sources':[],
              'retention_pass':gate,'mean_gold_cosine_drop':-.05},
            't00':{'cases':12,'correct_stop_depth':current,'generated_exit_rate':current/12},
            't01':{'cases':12,'correct_stop_depth':12 if step!=150 else 11,
               'mean_first_step_gold_cosine':first,'mean_final_gold_cosine':final,
               'mean_second_step_p_answer':.99}})
    (t01/'history.json').write_text(json.dumps(history))
    (t01/'progress.json').write_text(json.dumps(history[-1]))
    (t01/'result.json').write_text(json.dumps({'schema':'nanojev.hybridlink.t01-gated-t00-retention.v1',
      'status':'T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED','accepted_rung':'T01',
      'checkpoint':str(t01/'best.pt'),'trained_steps':250,
      'last_t00_retention':history[-1]['retention'],'last_t01':history[-1]['t01']}))
    args=SimpleNamespace(t00_run=str(t00),t01_run=str(t01),t01_source=None,
       output=str(out),low_probability=.55,low_cosine=.7,max_files=100,max_bytes=1_000_000)
    r=diag.inspect(args)
    assert r['terminal_gate']['accepted'] is True
    assert r['terminal_gate']['step']==250
    assert r['terminal_gate']['full_latent_certified'] is False
    assert len(r['events'])==5
    assert [e['t01_mean_first_step_gold_cosine'] for e in r['events']]==[x[3] for x in cases]
    assert r['case_count']==4
    assert {c['source'] for c in r['hard_cases']}=={'PROBLEM: 2304+632','PROBLEM: (-132300)+(-28589)'}
    assert len(r['hard_cases'])==2
    a={x['code'] for x in r['findings']}
    assert 'T01_GATE_ACCEPTED' in a
    assert 'T01_GATE_NOT_PASSED' not in a
    assert 'TRANSIENT_T00_RETENTION_LOSS' in a
    assert 'DETAILED_PER_CASE_TRAJECTORIES_MISSING' in a
    assert 'T00_RETENTION_GATE_FAILED' in a
    with sqlite3.connect(out/'hard_examples.sqlite3') as con:
        assert con.execute('select count(*) from hard_examples').fetchone()[0]==2
        assert con.execute("select observations from hard_examples where source='PROBLEM: 2304+632'").fetchone()[0]==3
    md=(out/'diagnostic.md').read_text()
    assert 'Final T01 gate: True' in md
    assert 'PROBLEM: 2304+632' in md
    assert 'Step 250' in md


def test_progress_only_late_gate_pass_supersedes_historical_failure(tmp_path):
    t=tmp_path/'t';t.mkdir()
    (t/'history.json').write_text(json.dumps([
        {'step':50,'gate_pass':False,'gate_reasons':['T00_RETENTION_REGRESSED']},
        {'step':100,'gate_pass':True,'gate_reasons':[]}]))
    args=SimpleNamespace(t00_run=None,t01_run=str(t),t01_source=None,
       output=str(tmp_path/'out'),low_probability=.55,low_cosine=.7,max_files=100,max_bytes=1_000_000)
    r=diag.inspect(args)
    assert r['terminal_gate']['accepted'] is True
    assert 'T01_GATE_NOT_PASSED' not in [x['code'] for x in r['findings']]


def test_unaccepted_run_still_reports_failures(tmp_path):
    t=tmp_path/'t';t.mkdir()
    (t/'history.json').write_text(json.dumps([{'step':50,'gate_pass':False,
        'gate_reasons':['T00_RETENTION_REGRESSED'],'retention':{'lost_sources':['PROBLEM: 1+3']}}]))
    args=SimpleNamespace(t00_run=None,t01_run=str(t),t01_source=None,
       output=str(tmp_path/'out'),low_probability=.55,low_cosine=.7,max_files=100,max_bytes=1_000_000)
    r=diag.inspect(args)
    assert r['terminal_gate']['accepted'] is False
    assert 'T01_GATE_NOT_PASSED' in [x['code'] for x in r['findings']]
    assert r['case_count']==1


def test_t01_timeline_reports_nested_metrics(tmp_path):
    t=tmp_path/'t';t.mkdir()
    (t/'history.json').write_text(json.dumps([
      {'step':50,'gate_pass':False,'retention':{'baseline_correct':12,'current_correct':11,
         'lost_sources':['PROBLEM: 1+1']},
       't01':{'correct_exit_rate':1.0,'correct_stop_depth':12,'mean_final_gold_cosine':.84}},
      {'step':100,'gate_pass':True,'retention':{'baseline_correct':12,'current_correct':12,
         'lost_sources':[]},
       't01':{'correct_exit_rate':1.0,'correct_stop_depth':12,'mean_final_gold_cosine':.89}}]))
    args=SimpleNamespace(t00_run=None,t01_run=str(t),t01_source=None,
       output=str(tmp_path/'out'),low_probability=.55,low_cosine=.7,max_files=100,max_bytes=1_000_000)
    r=diag.inspect(args)
    assert r['rungs'][0]['last_current_exit_rate']==1.0
    assert r['rungs'][0]['last_gold_cosine']==.89
    rows=list(__import__('csv').DictReader((tmp_path/'out'/'ladder_timeline.csv').open()))
    assert len(rows)==2
    assert rows[0]['retention_current_correct']=='11'
    assert 'PROBLEM: 1+1' in rows[0]['retention_lost_sources']
