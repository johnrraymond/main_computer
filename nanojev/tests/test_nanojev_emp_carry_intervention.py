"""Controlled paired carry/borrow diagnosis and campaign immutability contracts."""
from __future__ import annotations

import json

import pytest

from tools import nanojev_emp_carry_intervention as ci
from tools import nanojev_emp_dual_bank_diagnostic as diag
from tools import nanojev_zero_line_sampled_radius_ladder as old


def test_paired_interventions_preserve_strata_and_change_digit_chains():
    cases = [
        (7225, 1301, 8526, 6, 7225),        # same sign
        (-973060, 958000, -15060, 10, 973060), # opposite sign
        (-7225, -1301, -8526, 6, 7225),
    ]
    # Both templates, both orientations; each returned contrast must be validated.
    result=[]
    for row in cases:
        for template in (0,1):
            x=ci.make_pair(row,template)
            if x is None:continue
            result.append(x)
            a,b,dc,_,anchor=x['control']
            a1,b1,dt,_,anchor1=x['treatment']
            assert anchor==anchor1==row[4]
            assert a+b==dc and a1+b1==dt
            assert (a > 0) == (a1 > 0) and (b > 0) == (b1 > 0)
            assert x['control_chain']<=1 and x['treatment_chain']>=3
            assert ci._shell(dc)==ci._shell(dt)
            assert len(str(abs(dc)))==len(str(abs(dt)))
            assert x['relative_answer_drift']<=.2
            assert tuple(abs(z)//1000 for z in (a,b))==tuple(abs(z)//1000 for z in (a1,b1))
    assert {x['group'] for x in result}=={'same_sign','opposite_sign'}
    assert ci.make_pair((1, 2, 3, 0, 1)) is None
    assert ci.make_pair((50000,-50000,0,0,50000)) is None


def test_exact_discordance_stats_and_detectable_counterfactual_failure():
    cards = [(7000+i*200, 1700, 8700+i*200,6,200000+i) for i in range(200)]
    pairs, info = ci.make_pairs(cards,32)
    assert len(pairs['same_sign'])==32
    assert not pairs['opposite_sign']
    def predictor(rows):
        # Known synthetic mechanism: induced carry breaks both selected banks.
        result={name:[] for name in ci.BANK_NAMES}
        for row in rows:
            a,b,correct,_,_=row
            hard=(ci._operation_count(a,b,'same_sign')>=3)
            for bank in ci.BANK_NAMES:
                result[bank].append(correct+100000 if hard else correct)
        return result
    report=ci.analyze(pairs,predictor,'fake-checkpoint',16384,100,('right.right','right.left'))
    s=report['groups']['same_sign']['provisional_pair']
    assert s['control_hard']==0 and s['treatment_hard']==32
    assert s['worsened']==32 and s['improved']==0
    assert s['exact_mcnemar_p']<.0001
    assert report['groups']['opposite_sign']['status']=='insufficient_matched_pairs'
    assert ci._binomial_two_sided(0,0)==1
    assert ci._binomial_two_sided(10,0)<.01
    assert ci._binomial_two_sided(5,5)==1
    assert ci.paired_bank_stats(pairs['same_sign'],[r['control'][2] for r in pairs['same_sign']],
         [r['treatment'][2]+100000 for r in pairs['same_sign']])['worsened']==32


def test_sampling_anchor_separation_train_only_and_reproducibility():
    old_cards, _ = diag.mine_train_cards(16384,47,256)
    banned={r[4] for r in old_cards}
    fresh, info = diag.mine_train_cards(16384,47,2048,
                     namespace='emp-matched-carry-candidates:v1',excluded_anchors=banned)
    pairs, summary = ci.make_pairs(fresh,16)
    assert summary['accepted']=={'same_sign':16,'opposite_sign':16}
    assert all(row[4] not in banned for row in fresh)
    for group,cases in pairs.items():
        anchors=[r['anchor'] for r in cases]
        assert len(anchors)==len(set(anchors))
        assert all(old.stable_fold(a,47)=='train' for a in anchors)
        assert all(diag.specialist_partition(a,47)=='mine' for a in anchors)
    assert ci.make_pairs(fresh,16)==(pairs,summary)


def test_read_only_diagnostic_integration(tmp_path,monkeypatch,capsys):
    from tests.test_nanojev_emp_dual_bank_diagnostic import synthetic_campaign
    args,root=synthetic_campaign(tmp_path,monkeypatch)
    args.compare_parent=False
    args.matched_carry=True
    args.hard_candidates=256
    args.matched_candidates=2048
    args.matched_pairs=12
    args.matched_max_target_drift=.2
    ckpt=root/'stages'/'outward_8192'/'latest.pt'
    before=diag.sha256(ckpt)
    report=diag.run(args)
    assert diag.sha256(ckpt)==before
    m=report['matched_carry_intervention']
    assert m['checkpoint_sha256']==before
    assert m['sampling']['train_family_reserve_opened']==0
    assert m['sampling']['dev_or_test_opened']==0
    assert set(m['groups'])=={'same_sign','opposite_sign'}
    assert 'EMP_DIAG_MATCHED_CARRY=' in capsys.readouterr().out
    assert json.loads((tmp_path/'diagnostic.json').read_text())['matched_carry_intervention']['schema']==ci.SCHEMA
