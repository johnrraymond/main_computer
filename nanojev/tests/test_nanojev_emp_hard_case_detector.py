"""Input-only hardness detection, independent anchor validation, and read-only integration."""
from __future__ import annotations

import json
from argparse import Namespace

import pytest

from tools import nanojev_emp_hard_case_detector as det
from tools import nanojev_emp_dual_bank_diagnostic as diag


def labelled_pool(n=1200):
    """A deliberately learnable difficulty trigger, never a prediction feature."""
    rows = []
    predicted = {bank: [] for bank in det.BANK_NAMES}
    for k in range(n):
        a = 100000 + 137*k
        b = 99000 + 127*k if k%2 else -(99000 + 127*k)
        true = a+b
        # Both chosen bank errors are deliberately tied to operand-only sign.
        difficult = a*b > 0
        rows.append((a,b,true,k%8,10000000+k))
        for bank in det.BANK_NAMES:
            predicted[bank].append(true+(100000 if difficult else 0))
    return rows,predicted


def test_lexical_features_known_patterns_and_only_two_input_parameters():
    f=det.features(199900,-199899)
    assert f['opposite_sign'] and f['leading_digits_match_3']
    assert f['any_trailing_zero'] and f['tens_suffix_equal'] is False
    assert det.features(1555,556)['repeated_digits']
    assert det.features(999,11)['units_sum_to_ten']
    assert det.features(999,1)['units_sum_to_ten']
    assert det.features(888,-777)['opposite_sign']
    assert det.features(8,5)['same_sign']
    assert det.digit_chain(99,1)[0] >= 2
    assert det.digit_chain(1000,-1)[1] >= 3


def test_heldout_anchor_signal_vs_uninformative_model_and_target_leakage():
    rows,pred=labelled_pool()
    report=det.make_detector(rows,pred,('right.right','right.left'),47,depth=3,min_leaf=24)
    assert report['schema']==det.SCHEMA
    assert report['predictive_signal']
    assert report['evaluation']['roc_auc']>.98
    assert report['evaluation']['lift_at_top_10pct']>1.5
    assert set(report['model']['features']) == set(det.features(1,2))
    assert all('target' not in f and 'error' not in f for f in report['model']['features'])
    held={int(r[4]) for r in rows if det.detector_fold(r[4],47)=='evaluation'}
    trained={int(r[4]) for r in rows if det.detector_fold(r[4],47)=='fit'}
    assert held.isdisjoint(trained)
    assert report['evaluation']['anchors'] == len(held)
    assert report['train']['anchors'] == len(trained)
    assert det.score(report['model']['tree'],det.features(110000,120000)) > det.score(report['model']['tree'],det.features(110000,-120000))
    # No prediction labels given at scoring time.
    p,path = det.score_path(report['model']['tree'],det.features(110000,120000))
    assert p and 0 <= path <= 1
    with pytest.raises(ValueError,match='DUPLICATE_OPERANDS'):
        det.make_detector(rows+[rows[0]], {k:v+[v[0]] for k,v in pred.items()},('right.right','right.left'),47)


def test_auc_ties_and_intervals():
    assert det.auc([0.4,0.4,0.4,0.4],[0,1,0,1])==pytest.approx(.5)
    assert det.auc([0.1,0.8],[0,1])==pytest.approx(1)
    assert det.auc([0.1,0.2],[0,0]) is None
    assert det.wilson(5,10)[0] < .5 < det.wilson(5,10)[1]


def test_signatures_and_failed_detector_do_not_claim_causation():
    assert det._signature(100,1100)=='digit_displacement_1000'
    assert det._signature(-100,100)=='sign_crossing'
    rows,pred=labelled_pool()
    # Flip the label independently of available features by anchor-hash,
    # giving no genuine predictive signal on new anchors.
    for i,r in enumerate(rows):
        fail=int.from_bytes(__import__('hashlib').sha256(str(r[4]).encode()).digest()[:4], 'big')%2==0
        for bank in det.BANK_NAMES: pred[bank][i]=r[2]+(100000 if fail else 0)
    report=det.make_detector(rows,pred,('right.right','right.left'),47,depth=3,min_leaf=24)
    assert report['evaluation']['roc_auc']<.65
    assert not report['predictive_signal']


def test_diagnostic_integration_and_immutable_campaign(tmp_path,monkeypatch,capsys):
    from tests.test_nanojev_emp_dual_bank_diagnostic import synthetic_campaign
    args,root=synthetic_campaign(tmp_path,monkeypatch)
    args.mine_hard=True;args.detect_hard=True;args.hard_candidates=128
    args.hard_max_cases=20;args.hard_per_anchor=2;args.hard_min_failures=1
    args.hard_export=str(tmp_path/'hard.jsonl')
    args.detector_depth=3;args.detector_target='both_selected_wrong';args.detector_min_leaf=8
    rows,pred=labelled_pool(128)
    monkeypatch.setattr(diag,'mine_train_cards',lambda radius, seed, count:(rows,{'requested':count,'eligible_unique':len(rows)}))
    monkeypatch.setattr(diag,'predict_training_cases',lambda banks, cards, device, batch_size:pred)
    before=diag.sha256(root/'stages'/'outward_8192'/'latest.pt')
    report=diag.run(args)
    assert diag.sha256(root/'stages'/'outward_8192'/'latest.pt')==before
    assert report['hard_mining']['detector']['schema']==det.SCHEMA
    model_file=tmp_path/'campaign_hard_detector_8192.json'
    assert model_file.is_file()
    model=json.loads(model_file.read_text())
    assert model['evaluation']['n']>0
    assert 'EMP_DIAG_HARD_DETECTOR=' in capsys.readouterr().out
