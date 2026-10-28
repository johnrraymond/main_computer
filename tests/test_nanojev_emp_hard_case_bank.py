"""Hard-memory ledger, sampler and preservation protocol regressions."""
from types import SimpleNamespace
import sqlite3
import pytest
import torch
from tools.nanojev_emp_hard_case_bank import HardCaseBank,card
from tools import nanojev_emp_frontier_repair_campaign as frontier
from tools.nanojev_addition_curriculum import AdditionNet


def test_bad_answers_durable_and_correct_reobservations(tmp_path):
    path=tmp_path/'hard.sqlite3'
    bad=card(5,-4)
    other=card(4,-4)
    with HardCaseBank(path,source_sha256='source-A',max_delta=3) as bank:
        x=bank.observe([bad,other],[99,0],radius=5,delta=3,wave='0')
        assert x['wrong']==1 and bank.summary()['ever_wrong']==1
        bank.observe([bad],[1],radius=5,delta=3,wave='1')
        bank.regressions([other],radius=5,delta=3,wave='trial')
        assert bank.summary()['ever_threatened']==1
        got=bank.prioritized(5,3,3,seed=111,wave=3)
        assert {(r['a'],r['b']) for r in got}=={(5,-4),(4,-4)}
    with HardCaseBank(path,source_sha256='source-A',max_delta=3) as bank:
        assert bank.summary()['ever_verified_correct']==1
        values=bank.conn.execute('SELECT wrong_count,correct_count FROM cases WHERE a=5 AND b=-4').fetchone()
        assert values==(1,1)
    with pytest.raises(RuntimeError,match='SOURCE_OR_DOMAIN_MISMATCH'):
        HardCaseBank(path,source_sha256='source-B',max_delta=3)


def test_bank_refuses_invalid_card_and_prediction_lengths(tmp_path):
    with HardCaseBank(tmp_path/'bank.db',source_sha256='S',max_delta=4) as bank:
        with pytest.raises(ValueError,match='LENGTH_DRIFT'):
            bank.observe([card(5,-4)],[],radius=5,delta=4,wave=0)
        with pytest.raises(ValueError,match='INVALID_ARITHMETIC'):
            bank.observe([{'a':5,'b':-4,'target':999}],[999],radius=5,delta=4,wave=0)
        assert bank.summary()['entries']==0


def test_neighborhood_candidates_are_not_false_errors(tmp_path):
    with HardCaseBank(tmp_path/'hard.sqlite',source_sha256='S',max_delta=3) as bank:
        n=bank.neighbors([card(5,-4)],radius=7,delta=3,wave=3)
        assert n>0
        stats=bank.summary()
        assert stats['neighbor_candidates']==n and stats['ever_wrong']==0
        samples=bank.prioritized(7,3,20,seed=2,wave=1)
        assert all(frontier.pair_key(r)!=(5,-4) for r in samples)
        assert all(r['a']+r['b']==r['target'] for r in samples)


def test_exact_census_cardinality_and_sampling_boundary(tmp_path):
    for radius in [1,2,3,16,40,640,1024]:
        for delta in [0,1,2,32]:
            if radius<=40 or delta==32 and radius in (640,1024):
                cards=frontier.enumerate_domain(radius,delta)
                assert frontier.domain_bound(radius,delta)==len(cards)
    assert frontier.domain_bound(640,32)==81217
    assert frontier.domain_bound(1024,32)==131137
    args=frontier.parser().parse_args(['--run','a','--campaign','b',
                        '--ladder-output','c','--output','d','--max-radius','1024'])
    frontier.validate_args(args)  # Now exactly enumerable within the default cap.
    args.census_cap=70000;args.require_exhaustive=False;args.audit_size=400
    args.hard_audit_cards=50;args.shell_audit_cards=100
    with HardCaseBank(tmp_path/'bank.sqlite',source_sha256='S',max_delta=32) as bank:
        bank.observe([card(640,-639)],[999],radius=640,delta=32,wave='before')
        first,meta=frontier.scheduled_audit(args,1024,17,bank)
        second,_=frontier.scheduled_audit(args,1024,17,bank)
        third,_=frontier.scheduled_audit(args,1024,18,bank)
        assert first==second and first!=third
        assert (640,-639) in {frontier.pair_key(r) for r in first}
        assert meta['coverage']=='monte_carlo'
        assert meta['exploration_cards']>0 and meta['shell_cards']>0
        assert all(frontier.pair_key(r) and r['target']==r['a']+r['b'] for r in first)
        assert len(first)<=args.audit_size


def test_protected_exhaustive_core_refuses_regression(monkeypatch):
    net=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    original=frontier.state_copy(net)
    edited={k:v.clone() for k,v in original.items()}
    key='input.norm.weight'; edited[key][0]+=1
    anchor=float(original[key][0])
    def predictions(model,rs,*a):
        changed=float(model.state_dict()[key][0])>anchor+.5
        return [999 if changed and (r['a'],r['b'])==(2,-2) else r['target'] for r in rs]
    monkeypatch.setattr(frontier.donor,'predictions',predictions)
    args=SimpleNamespace(max_delta=1,protected_core_cap=1000,device='cpu',eval_batch=64)
    good,details=frontier.verify_protected_core(net,original,edited,5,args)
    assert not good and details['new_regressions']==1
    assert torch.equal(net.state_dict()[key],edited[key])


def test_hard_numbers_and_pairs_csv_export(tmp_path):
    with HardCaseBank(tmp_path/'bank.sqlite',source_sha256='Z',max_delta=2) as bank:
        bank.observe([card(-5,4),card(-5,5)],[999,0],radius=5,delta=2,wave='A')
        exported=bank.export(tmp_path)
        import csv
        with open(exported['pairs_csv'],newline='') as f:
            rows=list(csv.DictReader(f))
        assert len(rows)==1 and rows[0]['a']=='-5' and rows[0]['wrong_count']=='1'
        with open(exported['numbers_csv'],newline='') as f:
            numbers=list(csv.DictReader(f))
        assert {int(r['operand']) for r in numbers}=={-5,4}


def test_frontier_checkpoint_inherits_exact_core_and_rejects_foreign_source(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import json
    root=tmp_path/'run';(root/'stage_027').mkdir(parents=True)
    (root/'config.json').write_text('{}')
    snapshot=root/'stage_027'/'mastered_snapshot.pt';snapshot.write_bytes(b'fake')
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    state=frontier.state_copy(model)
    monkeypatch.setattr(frontier.cross,'snapshot',lambda *a,**kw:state)
    monkeypatch.setattr(frontier.cross,'sha256',lambda *a,**kw:'source-id')
    previous=tmp_path/'latest.pt'
    cfg={'schema':frontier.SCHEMA,'source_sha256':'source-id'}
    torch.save({'schema':frontier.SCHEMA+'.checkpoint','config':cfg,
                'state_dict':state,'certified_radius':640,
                'history':[{'radius':640,'certification':'exhaustive'}],
                'sampled_memories':[]},previous)
    args=SimpleNamespace(run=str(root),stage=27,start_repair=None,
                         start_frontier_checkpoint=str(previous),start_radius=640)
    recovered,digest,info=frontier.source_state(args)
    assert digest=='source-id' and info['inherited_exact_core']==640
    assert set(recovered)==set(state)
    args.start_radius=608
    with pytest.raises(ValueError,match='EXCEEDS_START'):
        frontier.source_state(args)
    args.start_radius=640
    cfg['source_sha256']='wrong';torch.save({'schema':frontier.SCHEMA+'.checkpoint',
         'config':cfg,'state_dict':state,'certified_radius':640},previous)
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):
        frontier.source_state(args)


def test_end_to_end_from_prior_checkpoint_creates_durable_bank(monkeypatch,tmp_path):
    import json
    root=tmp_path/'run';(root/'stage_027').mkdir(parents=True)
    (root/'config.json').write_text('{}')
    (root/'stage_027'/'mastered_snapshot.pt').write_bytes(b'fake')
    camp=tmp_path/'campaign';camp.mkdir()
    out=tmp_path/'hard_campaign'
    previous=tmp_path/'prior_latest.pt'
    net=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    weights=frontier.state_copy(net)
    monkeypatch.setattr(frontier.cross,'snapshot',lambda *args,**kwargs:weights)
    monkeypatch.setattr(frontier.cross,'sha256',lambda *args,**kwargs:'original-sha')
    monkeypatch.setattr(frontier.lasso,'initialize',lambda *args,**kwargs:(net,None,None,None,None))
    monkeypatch.setattr(frontier.donor,'predictions',lambda m,rows,dev,batch:[r['target'] for r in rows])
    torch.save({'schema':frontier.SCHEMA+'.checkpoint',
                'config':{'source_sha256':'original-sha'},'state_dict':weights,
                'certified_radius':2,'history':[{'radius':2,'certification':'exhaustive'}]},previous)
    args=frontier.parser().parse_args(['--run',str(root),'--campaign',str(camp),
                '--ladder-output',str(tmp_path/'ladder'),'--output',str(out),
                '--start-frontier-checkpoint',str(previous),'--start-radius','2',
                '--max-radius','3','--radius-step','1','--max-delta','2','--device','cpu'])
    result=frontier.run(args)
    assert result['status']=='complete'
    assert result['protected_exact_radius']==3
    assert (out/'hard_cases.sqlite3').exists()
    assert (out/'hard_pairs.csv').exists() and (out/'hard_numbers.csv').exists()
    assert torch.load(out/'latest.pt',weights_only=True)['protected_exact_radius']==3
    assert not hasattr(args,'_hard_bank')
    same=frontier.run(args)
    assert same['status']=='complete' and same['hard_case_summary']['entries']==0
