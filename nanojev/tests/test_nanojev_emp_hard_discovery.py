"""Large-population zero-line difficulty discovery: sampling, controls and restart."""
from __future__ import annotations

import gzip
import json
from argparse import Namespace

import pytest

from tools import nanojev_emp_hard_discovery as discovery
from tools import nanojev_emp_dual_bank_diagnostic as diag


def test_broad_and_native_candidates_remain_train_only_and_zero_line():
    stream = discovery.candidates(16384, 47)
    cards = [next(stream) for _ in range(160)]
    assert len({(c[0][0], c[0][1]) for c in cards}) == len(cards)
    assert any(source == 'native' for _, source in cards)
    assert any(source.startswith('broad:') for _, source in cards)
    assert any(card[2] == 0 for card, _ in cards)
    assert any(card[2] > 0 for card, _ in cards)
    assert any(card[2] < 0 for card, _ in cards)
    for (a,b,d,band,anchor),_ in cards:
        assert a+b == d
        assert max(abs(a),abs(b)) == anchor
        assert abs(d) <= 16384
        assert discovery.old.stable_fold(anchor,47) == 'train'
        assert diag.specialist_partition(anchor,47) == 'mine'
    stream2 = discovery.candidates(16384,47)
    assert cards == [next(stream2) for _ in range(len(cards))]


def test_label_grades_distinct_bank_failures():
    rows = [((100, -99, 1, 0, 100), 'native'), ((100,-100,0,0,100),'broad:zero')]
    names=discovery.dual.BANKS
    preds={n: [9 if i < 4 else 1, 1 if i < 3 else 0] for i,n in enumerate(names)}
    scored=list(discovery.scored_rows(rows,preds,'x',8192))
    assert scored[0]['wrong_count']==4 and scored[0]['all_four_wrong']
    assert scored[1]['wrong_count']==3 and scored[1]['three_or_more_wrong']
    assert not scored[1]['all_four_wrong']


def test_population_analysis_uses_easy_controls_and_holdout():
    rows=[]
    names=discovery.dual.BANKS
    # Systematically hard only when both operands positive, with many anchor families.
    for k in range(3000):
        a = 100000 + 13*k
        b = (30000+7*k) if k%2 else -(30000+7*k)
        d=a+b
        hard=b>0
        preds={name: d+(900000 if hard else 0) for name in names}
        rows.append({'a':a,'b':b,'target':d,'anchor':max(abs(a),abs(b)),
                     'band':k%8,'sampler':'native' if k%2 else 'broad:uniform',
                     'predictions':preds,'wrong_banks':list(names) if hard else [],
                     'wrong_count':4 if hard else 0,'all_four_wrong':hard})
    report=discovery.analyze_rows(rows,seed=47,reservoir_max=1000,min_support=20)
    assert report['scanned']==3000 and report['all_four_wrong']==1500
    assert report['discovery_fold']['n']>0 and report['validation_fold']['n']>0
    assert report['failures_by_wrong_bank_count']=={'0':1500,'4':1500}
    features=report['top_discovered_features_with_heldout_validation']
    assert any(r['feature']=='same_sign' and r['value'] and r['validation']['hard_rate']>.95 for r in features)
    assert report['predictive_detector']['evaluation']['roc_auc']>.95


def test_run_resume_and_read_only_campaign(tmp_path, monkeypatch, capsys):
    from tests.test_nanojev_emp_dual_bank_diagnostic import synthetic_campaign
    _, root=synthetic_campaign(tmp_path, monkeypatch)
    args=Namespace(output=str(root),ladder_output='unused',radius=8192,device='cpu',
                   cpu_threads=1,eval_batch_size=32,target_hard=1000,max_candidates=48,
                   chunk_size=16,reservoir_max=32,min_support=2,
                   report_dir=str(tmp_path/'discovery'),resume=False)
    before=diag.sha256(root/'stages'/'outward_8192'/'latest.pt')
    report=discovery.run(args)
    assert report['analysis']['scanned']==48
    assert not report['target_reached']
    assert diag.sha256(root/'stages'/'outward_8192'/'latest.pt')==before
    path=tmp_path/'discovery'/'manifest.json'
    manifest=json.loads(path.read_text('utf8'))
    assert manifest['complete'] and len(manifest['chunks'])==3
    assert all((tmp_path/'discovery'/part['path']).exists() for part in manifest['chunks'])
    with gzip.open(tmp_path/'discovery'/'all_four_wrong.jsonl.gz','rt') as f:
        hard=[json.loads(x) for x in f]
    assert len(hard)==report['analysis']['all_four_wrong']
    assert 'EMP_HARD_DISCOVERY_RESULT=' in capsys.readouterr().out
    args.resume=True
    assert discovery.run(args)['analysis']['scanned']==48
    args.resume=False
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        discovery.run(args)
    args.resume=True
    args.target_hard=2
    with pytest.raises(RuntimeError,match='RESUME_CONFIG'):
        discovery.run(args)
    args.report_dir=str(root/'forbidden')
    with pytest.raises(ValueError,match='SEPARATE_FROM_CAMPAIGN'):
        discovery.run(args)
