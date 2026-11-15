"""G2 learning-pipeline pretest: real report/ledger contract, no Qwen dependency."""
import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_curriculum_pretest as p


def _edge(source, destination, relation='owns'):
    return repr(((source, source+2, 'actor'), (destination, destination+2, 'action'), relation))


def row(sec, sha, *, exact=False, family='encounter', reachable=True,
        missed=None, extras=None, gold=2):
    missed = missed if missed is not None else ([] if exact else [_edge(1,10)])
    extras = extras if extras is not None else ([] if exact else [_edge(1,11)])
    return {'section':sec, 'prompt_sha256':sha, 'family':family,
            'gold_count':gold,'selected_count':gold-len(missed)+len(extras),
            'missing':len(missed),'extra':len(extras),'exact':exact,
            'inversions':0 if exact else 1,
            'missing_edges':missed,'extra_edges':extras,
            'missing_candidate_gold_edges':0 if reachable else 1,
            'candidate_reachable':reachable}


def artifact_fixture(tmp_path, *, with47=True):
    f48=tmp_path/p.CURRENT;f48.mkdir()
    f47=tmp_path/p.PREVIOUS;f47.mkdir()
    cohort={p._cohort_key(sec):[hashlib.sha256(sec.encode()).hexdigest()]
            for sec in p.SECTIONS}
    current=[]; old=[]
    for sec in p.SECTIONS:
        sha=cohort[p._cohort_key(sec)][0]
        current.append(row(sec,sha,exact=(sec=='learn'),family='allocation' if sec=='learn' else 'encounter'))
        old.append({**row('train_learn' if sec=='learn' else sec,sha,
                         exact=(sec=='transfer'),family='allocation' if sec=='learn' else 'encounter'),
                    'arm':'frozen:bce'})
    summary=lambda seq:{'all':{
        'cases':len(seq),'exact':sum(r['exact'] for r in seq),
        'missing':sum(r['missing'] for r in seq),
        'extra':sum(r['extra'] for r in seq),
        'rank_inversions':sum(r['inversions'] for r in seq)}}
    safe={'development_evaluation_skipped':True, 'dev_labels_used_for_training':False,
          'dev_labels_used_for_calibration':False, 'sealed_test_used':False,
          'production_promotion':False,'g2_certified':False,
          'frozen_g1':True,'frozen_g2_0033_parent':True,
          'train_policy':{'enabled':False,'config':None}}
    report48={**safe,'schema':p.SCHEMA48,'status':'TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE',
              'cohorts':cohort,'results':{sec:summary([r for r in current if r['section']==sec])
                                       for sec in p.SECTIONS}}
    report47={**safe,'schema':p.SCHEMA47,'status':'TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE',
              'cohorts':cohort,'results':{'arms':{'frozen:bce':{
                  'train_learn' if sec=='learn' else sec:summary([r for r in old if r['section']==('train_learn' if sec=='learn' else sec)])
                  for sec in p.SECTIONS}}}}
    with gzip.open(f48/p.SHA_KEYS['ledger'],'wt') as f:
        for r in current:f.write(json.dumps(r)+'\n')
    (f48/p.SHA_KEYS['weights']).write_bytes(b'frozen test weights')
    (f48/p.SHA_KEYS['policies']).write_text(json.dumps(safe['train_policy']))
    for field,name in p.SHA_KEYS.items():report48[field+'_sha256']=p.digest(f48/name)
    (f48/p.REPORT48).write_text(json.dumps(report48))
    with gzip.open(f47/p.LEDGER47,'wt') as f:
        for r in old:f.write(json.dumps(r)+'\n')
    (f47/p.WEIGHTS47).write_bytes(b'baseline test weights')
    (f47/p.POLICIES47).write_text(json.dumps(safe['train_policy']))
    for field,name in {'ledger':p.LEDGER47,'weights':p.WEIGHTS47,'policies':p.POLICIES47}.items():
        report47[field+'_sha256']=p.digest(f47/name)
    (f47/p.REPORT47).write_text(json.dumps(report47))
    report48['parent_hashes']={('g2_0047_'+key+'_sha256'):p.digest(f47/name)
        for key,name in {'report':p.REPORT47,'ledger':p.LEDGER47,
                         'weights':p.WEIGHTS47,'policies':p.POLICIES47}.items()}
    (f48/p.REPORT48).write_text(json.dumps(report48))
    return report48,report47,current,old


def test_read_only_pretest_explains_competition_and_retention(tmp_path):
    artifact_fixture(tmp_path)
    r=p.run(tmp_path)
    assert r['status']=='READ_ONLY_TRAIN_COHORT_PRETEST_COMPLETE'
    assert r['results']['learn']['exact']==1
    assert r['results']['transfer']['observed_competitive_pairs_lower_bound']==1
    assert r['by_family']['transfer']['encounter']['exact']==0
    assert r['paired_previous_comparison']['transfer']['regressed_graphs']==1
    assert r['paired_previous_comparison']['learn']['fixed_graphs']==1
    assert r['learning_pipeline_readiness']['counterfactual_training']['verified_paraphrase_and_role_reversal_pairs_available'] is False
    assert r['production_promotion'] is False
    assert set(x.name for x in tmp_path.rglob('*') if x.is_file())=={
        p.REPORT48,p.REPORT47,p.SHA_KEYS['ledger'],p.SHA_KEYS['weights'],
        p.SHA_KEYS['policies'],p.LEDGER47,p.WEIGHTS47,p.POLICIES47}


def test_matched_source_group_only_and_no_hallucinated_opportunities():
    x=row('transfer','a'*64,missed=[_edge(1,10)],extras=[_edge(2,11)])
    assert p.opportunity(x)==0
    x['extra_edges']=[_edge(1,11)]
    assert p.opportunity(x)==1
    x['candidate_reachable']=False
    assert p.opportunity(x)==0
    x['candidate_reachable']=True
    x['extra_edges']=['not Python literals','(very large rubbish']
    assert p.opportunity(x)==0


def test_integrity_tamper_fails_closed(tmp_path):
    artifact_fixture(tmp_path)
    with (tmp_path/p.CURRENT/p.SHA_KEYS['ledger']).open('ab') as f:
        f.write(b'\n')
    with pytest.raises(ValueError,match='INTEGRITY_LEDGER'):
        p.run(tmp_path)


def test_report_split_flags_fail_closed(tmp_path):
    artifact_fixture(tmp_path)
    path=tmp_path/p.CURRENT/p.REPORT48
    r=json.loads(path.read_text());r['sealed_test_used']=True
    path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='SAFETY_SEALED_TEST_USED'):
        p.run(tmp_path)


def test_ledger_prompt_mismatch_fails_closed(tmp_path):
    artifact_fixture(tmp_path)
    folder=tmp_path/p.CURRENT
    with gzip.open(folder/p.SHA_KEYS['ledger'],'rt') as f:rows=[json.loads(line) for line in f]
    rows[0]['prompt_sha256']='f'*64
    with gzip.open(folder/p.SHA_KEYS['ledger'],'wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    r=json.loads((folder/p.REPORT48).read_text())
    r['ledger_sha256']=p.digest(folder/p.SHA_KEYS['ledger'])
    (folder/p.REPORT48).write_text(json.dumps(r))
    with pytest.raises(ValueError,match='LEDGER_COHORT'):
        p.run(tmp_path)


def test_row_conservation_refuses_broken_counts(tmp_path):
    report48,report47,rows48,rows47=artifact_fixture(tmp_path)
    rows48[-1]['selected_count']+=1
    entries={sec:{r['prompt_sha256']:r for r in rows48 if r['section']==sec} for sec in p.SECTIONS}
    with pytest.raises(ValueError,match='ROW_SET_ARITHMETIC'):
        p.analyze(report48,entries)


def test_unreachable_exact_refused():
    r=row('transfer','a'*64,exact=True,reachable=False)
    with pytest.raises(ValueError,match='UNREACHABLE_EXACT'):
        p._validate_row(r)


def test_reported_metric_mismatch_refused(tmp_path):
    report48,_,rows48,_=artifact_fixture(tmp_path)
    report48['results']['transfer']['all']['exact']=100
    entries={sec:{r['prompt_sha256']:r for r in rows48 if r['section']==sec} for sec in p.SECTIONS}
    with pytest.raises(ValueError,match='SUMMARY_MISMATCH_TRANSFER_EXACT'):
        p.analyze(report48,entries)


def test_cli_reads_artifacts_and_supports_optional_output(tmp_path,capsys):
    artifact_fixture(tmp_path)
    out=tmp_path/'pretest.json'
    assert p.main(['--report-root',str(tmp_path),'--output',str(out)])==0
    assert 'READ-ONLY PRETEST' in capsys.readouterr().out
    assert json.loads(out.read_text())['g2_certified'] is False
    with pytest.raises(ValueError,match='OUTPUT_EXISTS'):
        p.main(['--report-root',str(tmp_path),'--output',str(out)])


def test_wilson_zero_success_has_nonzero_uncertainty():
    lo,hi=p.wilson(0,17)
    assert lo==0 and 0.18<hi<0.19
    assert p.wilson(1,1)[1]==1


def test_paired_significance_is_exact_not_unpaired():
    assert p.mcnemar_two_sided(6,0)==0.03125
    assert p.mcnemar_two_sided(7,1)==0.0703125
    assert p.mcnemar_two_sided(0,0)==1
    assert p.mcnemar_two_sided(5,5)==1


def test_size_stratification_keeps_family_separate(tmp_path):
    artifact_fixture(tmp_path)
    report=p.run(tmp_path)
    assert report['by_family_and_gold_graph_size']['transfer']['encounter']['0-10']['cases']==1
    assert report['by_family_and_gold_graph_size']['transfer']['encounter']['11-20']['cases']==0
