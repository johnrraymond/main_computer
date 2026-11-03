"""Shotgun controlled rewording and v6 additions: no GPU required."""
from collections import defaultdict
import json
import pytest
from tools import nanojev_qwen_shotgun as shotgun
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from test_nanojev_qwen_shotgun_joint import config


def _deck(tmp_path):
    args=config(tmp_path)
    policy=joint._policy(args)
    _,hrows,_=joint._eval(args,policy)
    return hrows,shotgun.diagnostic_evaluation(hrows,per_family=6)


def test_all_fourteen_rewordings_preserve_exact_matched_hdb08_math(tmp_path):
    hrows, rows=_deck(tmp_path)
    assert len(shotgun.REPHRASE_MODES)==14
    assert len(shotgun.DIAGNOSTIC_MODES)==27
    assert len(rows)==27*len({r["scenario_key"] for r in rows})
    assert len({r['source'] for r in rows})==len(rows)
    groups=defaultdict(dict)
    for row in rows:
        groups[row['scenario_key']][row['mode']]=row
        meta=row['metadata']
        assert row['target']==f'PROBLEM: {meta["expected_balance"]}'
        assert meta['expected_balance']==(
            meta['hawk_coefficient']*meta['hawk_count']+
            meta['dove_coefficient']*meta['dove_count'])
        assert meta['is_evaluation']
    assert len(groups)==len({(r["scenario_key"]) for r in rows})
    for ident, modes in groups.items():
        assert set(modes)==set(shotgun.DIAGNOSTIC_MODES)
        assert len({r['target'] for r in modes.values()})==1
        assert modes['prose']['source']==modes['prose']['metadata']['expected_hdb08_source']
        assert 'In half-point units compute exactly' in modes['products_with_matrix']['source']
        for mode in shotgun.REPHRASE_MODES:
            prompt=modes[mode]['source']
            assert prompt.startswith('PROBLEM: ')
            assert 'Return one canonical terminal PROBLEM: integer packet.' in prompt
            assert (shotgun._spoken_points(modes[mode]['metadata']['payoff_matrix']['hawk_dove']) in prompt if mode=='verbal_word_problem' else str(modes[mode]['metadata']['payoff_matrix']['hawk_dove']) in prompt)
            assert str(modes[mode]['metadata']['hawk_count']) in prompt or mode=='verbal_word_problem'
    assert set(shotgun.REPHRASE_MODES).isdisjoint(shotgun.MODES)
    assert set(shotgun.REPHRASE_MODES).isdisjoint(shotgun.OPERATION_MODES)


def test_example_is_original_one_rephrased_fourteen_ways():
    from tools import nanojev_qwen_hdb_balance as hdb
    matrix=next(m for m in hdb.EVAL_VARIANTS if tuple(m)==(5,2,3))
    prompts={m:shotgun.diagnostic_case(m,variant=matrix,offset=1,
             label='EXAMPLE',evaluation=True) for m in shotgun.REPHRASE_MODES}
    for mode,row in prompts.items():
        assert row['target']=='PROBLEM: -25'
        assert row['metadata']['hawk_count']==600001
        assert row['metadata']['dove_count']==399999
        assert row['metadata']['hawk_coefficient']==-10
        assert row['metadata']['dove_coefficient']==15
        assert 'PROBLEM: -25' not in row['source'] # no teacher answer leakage
    assert '"operator": "sum_of_products"' in prompts['json_packet']['source']
    json_prompt=prompts['json_packet']['source']
    packet=json.loads(json_prompt.split('task:\n',1)[1].split('\nReturn one canonical',1)[0])
    assert packet['calculation']['terms'][0]=={'coefficient_half_points':-10,'quantity':600001}
    assert packet['payoffs_in_points']['HH']==-5
    assert packet['payoffs_in_points']['DD']==7.5
    assert 'six hundred thousand and one' in prompts['verbal_word_problem']['source']
    assert 'seven and one half' in prompts['verbal_word_problem']['source']
    assert 'PART A' in prompts['separated_context']['source']
    assert 'PART B' in prompts['separated_context']['source']
    assert 'FIRST evaluate' in prompts['arithmetic_first']['source']
    assert 'result = (a * x) + (b * y)' in prompts['pseudocode']['source']


def test_new_rephrasings_never_change_optimizer_modes_or_training_sampler(tmp_path):
    hrows,rows=_deck(tmp_path)
    assert set(shotgun.exposures())==set(shotgun.MODES)
    for mode in shotgun.REPHRASE_MODES:
        with pytest.raises(ValueError,match='SHOTGUN_UNKNOWN_MODE'):
            shotgun.diagnostic_train_case(42,100,mode,set())
    assert len(shotgun.OPERATION_BOUNDARIES)==8
    scores={frontier._key(r):{'emitted':r['target'], 'correct':True} for r in rows}
    m=shotgun.matched_comparison(rows,scores,frontier._key)
    assert len(m['against_prose_payoff_matrix'])==26
    assert len(m['operation_boundaries'])==8
    assert all(m['against_prose_payoff_matrix'][n]['both_exact']==len({r['scenario_key'] for r in rows})
               for n in shotgun.REPHRASE_MODES)
    assert joint._shotgun_eval(config(tmp_path),joint._policy(config(tmp_path)),[],hrows,[]) == rows


def test_v3_v5_checkpoint_policy_are_accepted_for_diagnostic_only_migration(tmp_path):
    a=config(tmp_path)
    p=joint._policy(a)
    old={**p,'shotgun_version':'nanojev.qwen.shotgun.operation-isolation.v3'}
    assert joint._compatible_policy(old,p)
    assert joint._compatible_policy({**old,'shotgun_version':'nanojev.qwen.shotgun.rephrase-neighborhood.v5'},p)
    assert not joint._compatible_policy({**old,'steps':p['steps']+1},p)
    assert not joint._compatible_policy({**old,'shotgun_version':'unknown'},p)


def test_verbal_numbers_cover_half_points_and_million_counts():
    assert shotgun._spoken_integer(600001)=='six hundred thousand and one'
    assert shotgun._spoken_integer(399999)=='three hundred ninety-nine thousand nine hundred ninety-nine'
    assert shotgun._spoken_integer(-10)=='negative ten'
    assert shotgun._spoken_integer(10_000_000)=='ten million'
    assert shotgun._spoken_points(-10.5)=='negative ten and one half'
    assert shotgun._spoken_points(7.5)=='seven and one half'


def test_v6_four_new_prompts_preserve_math_and_canonical_originals(tmp_path):
    """Four specified rewordings, same heldout problem; previous 23 frozen modes."""
    import hashlib
    import json
    from tools import nanojev_qwen_hdb_balance as hdb
    hrows,rows=_deck(tmp_path)
    new_modes=('ledger_reconciliation','debit_credit','numbered_operations',
               'authoritative_calculation')
    assert shotgun.REPHRASE_MODES[-4:]==new_modes
    scenarios={r['scenario_key'] for r in rows}
    assert len(rows)==27*len(scenarios)
    assert len(scenarios) in (15,18) # HDB policy may expose five or six offsets per family
    old_modes=shotgun.MODES+shotgun.OPERATION_MODES+shotgun.REPHRASE_MODES[:10]
    old_rows=[r for r in rows if r['mode'] in old_modes]
    assert len(old_rows)==23*len(scenarios)
    # Protect all 414 existing v5 prompts, metadata, expected results, and order.
    old_digest=hashlib.sha256(json.dumps(old_rows,sort_keys=True).encode()).hexdigest()
    assert old_digest in ('6a23cc704a38ef33c46fede725532d5324037b81fdf453aab791040e5dc666b5',
                          '03405d12e53db6e9feb9298614786d380cba794396673f3d15d2efc6ce5c8011')
    for matrix in hdb.EVAL_VARIANTS:
        for k in shotgun.DIAGNOSTIC_OFFSETS[:6]:
            cases={m:shotgun.diagnostic_case(m,variant=matrix,offset=k,
                    label='EXAMPLE',evaluation=True) for m in new_modes}
            assert len({r['target'] for r in cases.values()})==1
            for mode,row in cases.items():
                meta=row['metadata']
                assert row['target']==('PROBLEM: '+str(
                    meta['hawk_coefficient']*meta['hawk_count']+
                    meta['dove_coefficient']*meta['dove_count']))
                assert row['target'] not in row['source']  # no answer leakage
                assert 'Return one canonical terminal PROBLEM: integer packet.' in row['source']
                assert str(meta['hawk_count']) in row['source']
                assert str(meta['dove_count']) in row['source']
                assert str(meta['payoff_matrix']['hawk_hawk']) in row['source']
    case={mode:shotgun.diagnostic_case(mode,variant=(5,2,3),offset=1,
         label='EXAMPLE',evaluation=True)['source'] for mode in new_modes}
    assert 'RECONCILE TWO SIGNED HALF-POINT ACCOUNTS' in case['ledger_reconciliation']
    assert 'Account A has 600001 entries of -10' in case['ledger_reconciliation']
    assert 'Account B has 399999 entries of +15' in case['ledger_reconciliation']
    assert 'DEBITS: 600001 entries of 10' in case['debit_credit']
    assert 'CREDITS: 399999 entries of 15' in case['debit_credit']
    assert 'Step 1: Multiply -10 by 600001' in case['numbered_operations']
    assert 'Step 2: Multiply +15 by 399999' in case['numbered_operations']
    assert '(-10*600001)+(15*399999)' in case['authoritative_calculation']
    for mode in new_modes:
        with pytest.raises(ValueError,match='SHOTGUN_UNKNOWN_MODE'):
            shotgun.diagnostic_train_case(42,100,mode,set())


def test_real_v5_to_v6_diagnostic_migration_archives_deck_without_optimizer_step(monkeypatch,tmp_path):
    """Migration, archived deck, model and optimizer checked against live-format state."""
    torch=pytest.importorskip('torch')
    from tools import nanojev_qwen_hdb_balance as hdb
    from tools import nanojev_qwen_lm_radius_ladder as ladder
    a=config(tmp_path,joint_steps=1,joint_check_every=1,
             joint_t_eval_per_shell=36,joint_t_train_cases_per_shell=81)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.tensor(0.))
    class Zero:
        @staticmethod
        def lora_state(bb):return {'weight':bb.weight.detach().clone()}
        @staticmethod
        def restore_lora(bb,state):
            with torch.no_grad():bb.weight.copy_(state['weight'])
    monkeypatch.setattr(hdb,'_checkpoint',lambda *_:({'state':{'phase_step':1200},'optimizer':None},'frozen-parent-sha'))
    def load_model(*_):
        bb=Model();return bb,None,Zero,[bb.weight]
    monkeypatch.setattr(hdb,'_model',load_model)
    def train_step(bb,tok,opt,rows,args):
        with torch.no_grad():bb.weight.add_(1.)
        return 0.125
    monkeypatch.setattr(ladder,'_train_step',train_step)
    def score(bb,tok,rows,args):
        return {frontier._key(r):{'correct':True,'emitted':r['target']} for r in rows}
    monkeypatch.setattr(joint,'_score_t',score)
    monkeypatch.setattr(joint,'_score_shotgun',score)
    monkeypatch.setattr(hdb,'_score_hdb',lambda bb,tok,rows,args,policy:{
        joint.competency._digest(r['source']):{'correct':True,'emitted':r['target']}
        for r in rows})
    monkeypatch.setattr(hdb,'_score_balance_chains',lambda bb,tok,rows,args,policy:{
        joint.competency._digest(r['source']):{'correct':True,'terminal_correct':True,'first_wrong_rung':None}
        for r in rows})
    monkeypatch.setattr(frontier,'_chain_score',lambda bb,tok,rows,args,**kw:{
        frontier._key(r):{'correct':True,'terminal_correct':True,'first_wrong_rung':None,
                         'failure_reason':None} for r in rows})
    state=joint.run(a)
    assert state['step']==1
    out=joint._out(a)
    current=json.loads((out/'shotgun_eval_deck.json').read_text())
    assert len(current)==27*len({r['scenario_key'] for r in current})
    old=current[:]
    prior_modes=set(shotgun.DIAGNOSTIC_MODES[:-4])
    old=[r for r in current if r['mode'] in prior_modes]
    assert len(old)==23*len({r['scenario_key'] for r in current})
    ladder._atomic_json(out/'shotgun_eval_deck.json',old)
    cp=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    cp['state']['policy']['shotgun_version']='nanojev.qwen.shotgun.rephrase-neighborhood.v5'
    cp['state']['shotgun_diagnostic_version']='nanojev.qwen.shotgun.rephrase-neighborhood.v5'
    cp['state']['shotgun_eval_sha256']=ladder.deck_fingerprint(old)
    prior_report=json.loads((out/'joint_frontier_report.json').read_text())
    prior_report['shotgun']['diagnostic_version']='nanojev.qwen.shotgun.rephrase-neighborhood.v5'
    ladder._atomic_json(out/'joint_frontier_report.json',prior_report)
    before_lora=cp['lora']['weight'].clone()
    before_opt=cp['optimizer']
    ladder._atomic_torch(out/'working.pt',cp)
    after=joint.run(a)
    assert after['step']==1  # read-only migration; no new training iteration
    assert after['shotgun_diagnostic_version']==shotgun.DIAGNOSTIC_VERSION
    assert after['shotgun_eval_sha256']==ladder.deck_fingerprint(current)
    assert json.loads((out/'shotgun_v5_eval_deck.json').read_text())==old
    archived=json.loads((out/'shotgun_v5_archived_diagnostic.json').read_text())
    assert archived['version']=='nanojev.qwen.shotgun.rephrase-neighborhood.v5'
    assert archived['original_eval_sha256']==ladder.deck_fingerprint(old)
    snapshot=torch.load(out/'pre_shotgun_ledger_context_migration.pt',map_location='cpu',weights_only=True)
    assert torch.equal(snapshot['lora']['weight'],before_lora)
    assert snapshot['optimizer']==before_opt
    migrated=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    assert torch.equal(migrated['lora']['weight'],before_lora)
    assert migrated['optimizer']==before_opt
    assert len(json.loads((out/'shotgun_eval_deck.json').read_text()))==len(current)
    assert len(json.loads((out/'shotgun_operation_cases.json').read_text())['scenarios'])==len({r['scenario_key'] for r in current})
    assert joint.latest_competency(a)['shotgun_report_is_current'] is False
