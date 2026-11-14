"""Anti-cheating tests for acquired fixed-game T08..T00 head."""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import json
import pytest

from tools import nanojev_gamer_v1_hawk_dove_acquisition as head
from tools import nanojev_hawk_dove_million as src

FIXED = dict(zip(src.TAGS,('-1','3','0','1.5')))
TEACHER = {k:Decimal(v) for k,v in FIXED.items()}


def t08(a):
    return head._make_t08(a,1_000_000-a)


@pytest.mark.parametrize('a,expected,x,y',[
    (0,'HAWK','3000000','1500000'),
    (200000,'HAWK','2200000','1200000'),
    (599999,'HAWK','600004','600001.5'),
    (600000,'TIE','600000','600000'),
    (600001,'DOVE','599996','599998.5'),
    (940000,'DOVE','-760000','90000'),
    (1000000,'DOVE','-1000000','0'),
])
def test_t08_exact_decisions(a,expected,x,y):
    result=head.infer(t08(a),FIXED)
    assert result['decision']==expected
    assert Fraction(result['hawk_cumulative_payoff'])==Fraction(Decimal(x))
    assert Fraction(result['dove_cumulative_payoff'])==Fraction(Decimal(y))
    assert result['learned_coefficients_used'] is True


@pytest.mark.parametrize('tier',list(src.TIERS))
@pytest.mark.parametrize('rep',list(src.REPRESENTATIONS))
def test_each_source_tier_full_mutation_path(tier,rep):
    x,y,z=src.oracle(940000,60000,TEACHER)
    for _,slots in src.masks(tier,mode='path'):
        prompt=src.render(tier,940000,60000,TEACHER,full_slots=slots,representation=rep)
        result=head.infer(prompt,FIXED)
        assert result['tier']==tier
        assert (Fraction(result['hawk_cumulative_payoff']),Fraction(result['dove_cumulative_payoff']),result['decision'])==(Fraction(x),Fraction(y),z)


@pytest.mark.parametrize('bad',[ 
    'You realize you are playing Hawk-Dove. Your opponent plays Hawk 940000 out of a million times. Your opponent plays Dove 60000 out of the same million times. Which pure strategy gives you the greater total payoff across those encounters?',
    '(ARGMAX 4 (ADD 1 2))',
    '(ARGMAX 4 5) (BAD)',
    '(ARGMAX 2 3) EXPLAIN HAWK',
    'You realize you are playing Hawk–Dove. Your opponent plays Hawk 200000 out of a million times. Your opponent plays Dove 900000 out of the same million times. Which pure strategy gives you the greater total payoff across those encounters?',
])
def test_refuses_unsupported_or_malformed_forms(bad):
    with pytest.raises(ValueError):head.infer(bad,FIXED)


def test_rejects_t07_english_lisp_inconsistency():
    prompt=src.render('T07',940000,60000,TEACHER)
    edited=prompt.replace('(MUL 940000 HAWK_HAWK_PAYOFF)','(MUL 930000 HAWK_HAWK_PAYOFF)')
    assert edited!=prompt
    with pytest.raises(ValueError,match='CONTRADICT'):
        head.infer(edited,FIXED)


def test_rejects_t08_bad_logbar_and_scientific():
    prompt=src.render('T08',940000,60000,TEACHER,src.SLOTS['T08'],src.REP_SCIENCE)
    assert head.infer(prompt,FIXED)['decision']=='DOVE'
    with pytest.raises(ValueError):
        head.infer(prompt.replace('9.4e5','9.3e5'),FIXED)
    with pytest.raises(ValueError):
        head.infer(prompt.replace('||||','|||||',1),FIXED)


def test_training_uses_labels_only_as_targets_not_prompt_features(tmp_path,monkeypatch):
    output=tmp_path/'acquired'
    report=head.train(output,epochs=200,step=10000)
    assert report['status']=='PASS_ISOLATED_HAWK_DOVE_T08_T00_ACQUISITION'
    assert report['acquired_t08_recall']=={'correct':101,'cases':101}
    assert report['fresh_t08_probe']=={'correct':100,'cases':100,'scope':'UNSEEN_INTERSTITIAL_COUNTS_SAME_GAME'}
    assert report['exact_zero_boundary']['correct']==3
    assert report['acquisition_ledger']['cases']==909
    assert (output/'acquired_cases.jsonl').is_file()
    assert len(report['full_ladder'])==9 and all(r['correct']==r['cases'] for r in report['full_ladder'])
    assert report['learned_payoff_coefficients']=={k:head._fmt(head._to_fraction(v)) for k,v in FIXED.items()}
    # The live query cannot call the teacher's oracle or read the fixture.
    monkeypatch.setattr(src,'oracle',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('ORACLE_USED_AT_INFERENCE')))
    monkeypatch.setattr(src,'read_fixture',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('FIXTURE_USED_AT_INFERENCE')))
    monkeypatch.setattr(head,'_teacher',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('TRAINING_TEACHER_USED_AT_INFERENCE')))
    result=head.query(output,t08(940000))
    assert result['result']['decision']=='DOVE'
    assert result['result']['hawk_cumulative_payoff']=='-760000'
    assert result['result']['dove_cumulative_payoff']=='90000'
    assert result['no_teacher_fixture_or_sqlite_access_during_query'] is True
    assert result['no_frozen_qwen_or_lora_inference'] is True
    with pytest.raises(FileExistsError):head.train(output,epochs=200,step=10000)


def test_rejects_tampered_checkpoint(tmp_path):
    output=tmp_path/'head'
    head.train(output,epochs=200,step=100000)
    data=json.loads((output/'hawk_dove_head.json').read_text())
    data['weights']['HAWK_HAWK_PAYOFF']='55'
    (output/'hawk_dove_head.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='FINGERPRINT'):
        head.query(output,t08(940000))


def test_insufficient_training_refuses_checkpoint(tmp_path):
    output=tmp_path/'fail'
    with pytest.raises(ValueError,match='TRAINING_NOT_CONVERGED|TARGET_MISMATCH'):
        head.train(output,epochs=1,step=10000)
    assert not output.exists()


def test_query_head_independent_of_teacher_source_module(tmp_path):
    # The inference result must be determined by the file's learned weights,
    # not by the compiler's teacher coefficients or a hard-coded game answer.
    changed={k:head._fmt(head._to_fraction(v)) for k,v in FIXED.items()}
    changed['HAWK_HAWK_PAYOFF']='10'
    before=head.infer(t08(940000),FIXED)
    after=head.infer(t08(940000),changed)
    assert before['decision']=='DOVE'
    assert after['decision']=='HAWK'


def test_cli_has_both_modes_registered():
    from tools import nanojev_gamer_v1_prompt_db_smoke as cli
    # No training or CUDA process required: argument parsing reaches the
    # appropriate mode and rejects missing essential fields, not a bad choice.
    with pytest.raises(SystemExit) as exc:
        cli.main(['hawk-dove-query'])
    assert exc.value.code==2


def test_supervised_head_learns_different_game_not_embedded_teacher_answer():
    # A completely different payoff table produces a different fitted head.
    # Confirms the four coefficients are fitted from samples, not injected.
    coefficients={'HAWK_HAWK_PAYOFF':'-3','HAWK_DOVE_PAYOFF':'1',
                  'DOVE_HAWK_PAYOFF':'2','DOVE_DOVE_PAYOFF':'-1'}
    rows=[]
    for a in range(0,1_000_001,10000):
        b=1_000_000-a
        rows.append((a,b,Fraction(a)*Fraction(-3)+b,
                     Fraction(a)*2-Fraction(b)))
    learned,_=head._train_head(rows,epochs=200)
    assert learned==coefficients
    # This game has a different preferred strategy than the original one.
    assert head.infer(t08(940000),learned)['decision']=='DOVE'
    assert head.infer(t08(0),learned)['decision']=='HAWK'


def test_count_conflicts_rejected_on_lisp_tiers():
    original=src.render('T03',940000,60000,TEACHER)
    modified=original.replace('940000 DOVE_HAWK_PAYOFF','930000 DOVE_HAWK_PAYOFF')
    with pytest.raises(ValueError,match='REPEATED_LISP_COUNTS_DISAGREE'):
        head.infer(modified,FIXED)
    partial=src.render('T01',940000,60000,TEACHER)
    # T01 provides the Hawk total as a raw oracle result: tampering with it
    # is detectable using the acquired game's learned coefficients.
    partial_changed=partial.replace('(ARGMAX\n  -760000','(ARGMAX\n  760000')
    assert partial!=partial_changed
    with pytest.raises(ValueError,match='CONTRADICTION'):
        head.infer(partial_changed,FIXED)
