"""Regression tests for the numeric zero-line HDB/T integrated curriculum."""
from types import SimpleNamespace

from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_hdb_difficulty as difficulty
from tools import nanojev_qwen_joint_frontier as joint


def test_actual_fixed_balance_example_and_all_tiers():
    for tier in range(9):
        row=hdb._row(tier,-1,(1,2,3),label='unit')
        assert (row['a'],row['b'],row['delta'])==(599999,400001,5)
        assert row['target'].startswith('PROBLEM: ')
        assert row['zero_side']=='positive'
        assert row['family']=='hdb' and row['tier']==f'HDB{tier:02d}'
        assert 'ARGMAX' not in row['source']
        assert 'Return only HAWK, DOVE, or TIE.' not in row['source']
        assert row['half_point_units']
        if tier<=6:
            assert row['source'].startswith('PROBLEM: ')
            assert row['rung']==(0,1,2,3,3,5,7)[tier]
            assert row['target']==row['complete_chain'][1]
            assert row['complete_chain'][-1]=='PROBLEM: 5'
        else:
            assert row['target']=='PROBLEM: 5'
            assert row['complete_chain'] is None
    assert hdb._row(0,-1,(1,2,3),label='unit')['source']=='PROBLEM: (-1199998)+1200003'
    hdb8=hdb._row(8,-1,(1,2,3),label='unit')
    assert '599999' in hdb8['source'] and '400001' in hdb8['source']
    assert 'pays -1 points' in hdb8['source']
    assert 'pays 3 points' in hdb8['source']
    assert 'pays 1.5 points' in hdb8['source']


def test_both_sides_and_true_zero_at_all_tiers():
    for tier in range(9):
        negative=hdb._row(tier,1,(2,3,2),label='unit')
        zero=hdb._row(tier,0,(2,3,2),label='unit')
        positive=hdb._row(tier,-1,(2,3,2),label='unit')
        assert [r['zero_side'] for r in (negative,zero,positive)]==['negative','zero','positive']
        assert [r['delta'] for r in (negative,zero,positive)]==[-10,0,10]
        assert all('HAWK\n' not in r['source'] for r in (negative,zero,positive))


def test_partition_and_full_reduction_canonical_chain():
    for tier in range(7):
        for k in (-64,-1,0,1,64):
            row=hdb._row(tier,k,(3,2,3),label='unit')
            assert row['complete_chain'][-1]==f"PROBLEM: {row['delta']}"
            assert row['source']!=row['target']
            assert len(row['complete_chain'])==row['rung']+2
    assert sum(hdb._split(-1199998,4))==-1199998
    assert sum(hdb._split(1200003,5))==1200003


def test_eval_disjoint_coefficients_and_numeric_batch():
    a=SimpleNamespace(seed=435,batch_size=4,eval_batch=8)
    policy={'steps':100,'max_offset':64,'batch_size':4,
            't_replay_per_batch':1,'hdb_eval_cases':9}
    heldout=hdb._evaluation(policy)
    assert len(heldout)==243
    assert {r['tier'] for r in heldout}=={f'HDB{i:02d}' for i in range(9)}
    assert len({r['source'] for r in heldout})==len(heldout)
    for step in range(100):
        batch=hdb._batch(a,step,policy,[ladder._case(0,[1,-1],'replay','numeric')])
        assert len(batch)==4
        assert {r['zero_side'] for r in batch if r.get('family')=='hdb'}=={'negative','zero','positive'}
        assert all(r['source']!=held['source'] for r in batch for held in heldout)
        assert all(r['target'].startswith('PROBLEM: ') for r in batch)


def test_exact_hdb_scoring_rejects_choices_and_wrong_balances():
    rows=[hdb._row(0,-1,(1,2,3),label='test'),hdb._row(8,0,(1,2,3),label='test')]
    a=SimpleNamespace(eval_batch=8,device='cpu',competency_progress_every=100)
    answers=['PROBLEM: 5\n','TIE']
    result=hdb._score_hdb(None,None,rows,a,{'max_tokens':1024,'max_new_tokens':48},
            greedy=lambda *args:answers)
    assert [result[competency._digest(r['source'])]['correct'] for r in rows]==[True,False]
    baseline={competency._digest(r['source']):{'correct':False} for r in rows}
    attribution,errors=difficulty.analyze_hdb(rows,baseline,result)
    assert attribution['failure_types']=={'invalid_numeric_packet':1}
    assert errors[0]['correct_balance_half_units']==0


def test_autonomous_hdb_chain_never_teacher_corrects_wrong_intermediate():
    rows=[hdb._row(3,-1,(1,2,3),label='test')]
    expected=rows[0]['complete_chain'][1:]
    answers=iter([expected[0], 'PROBLEM: 999'])
    a=SimpleNamespace(device='cpu')
    def greedy(*_):return [next(answers)]
    got=hdb._score_balance_chains(None,None,rows,a,{'max_tokens':1024,'max_new_tokens':48},greedy=greedy)
    one=got[competency._digest(rows[0]['source'])]
    assert not one['correct'] and one['completed_hops']==2
    assert one['first_wrong_rung']=='T02'
    assert one['trace'][1]['input']==expected[0]


def test_hdb_full_chains_with_correct_oracle_and_distinct_result():
    row=hdb._row(6,-1,(1,2,3),label='test')
    emitted=iter(row['complete_chain'][1:])
    a=SimpleNamespace(device='cpu')
    got=hdb._score_balance_chains(None,None,[row],a,{'max_tokens':1024,'max_new_tokens':48},
                                  greedy=lambda *_:[next(emitted)])
    key=competency._digest(row['source'])
    assert got[key]['correct'] and got[key]['terminal_correct']
    assert got[key]['completed_hops']==8
    assert hdb._chain_metrics([row],{key:{'correct':False}},got)['net']==1


def test_numeric_joint_workspace_isolated_from_classification_checkpoint(tmp_path):
    a=SimpleNamespace(output=str(tmp_path/'numeric'),frontier_output=None,joint_output=None)
    assert joint._out(a).name.endswith('_joint_t_hdb_numeric_frontier_v2')
    assert 'v3' in joint.SCHEMA
