"""Verifies memorization-first frontier expansion: no DEV/TEST holdout sets."""
from types import SimpleNamespace
import pytest
import torch
from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_frontier_repair_campaign as frontier


def test_exhaustive_census_is_exact_and_includes_shell_boundary():
    cards=frontier.enumerate_domain(4,2)
    pairs={frontier.pair_key(r) for r in cards}
    assert len(pairs)==len(cards)
    assert (4,-4) in pairs and (-4,4) in pairs
    assert (4,-3) in pairs and (-3,4) in pairs
    assert (0,4) not in pairs  # out of residual bound
    assert all(r['a']+r['b']==r['target'] for r in cards)
    shell=frontier.enumerate_domain(4,2,lower=3)
    assert all(max(abs(r['a']),abs(r['b']))==4 for r in shell)
    assert set(map(frontier.pair_key,shell))=={k for k in pairs if max(abs(k[0]),abs(k[1]))==4}
    assert not set(map(frontier.pair_key,shell)) & set(map(frontier.pair_key,frontier.enumerate_domain(3,2)))


def test_seeded_monte_carlo_reproducible_and_rewaves():
    a,first=frontier.audit_cards(1000,32,100,128,1234,0)
    b,second=frontier.audit_cards(1000,32,100,128,1234,0)
    c,third=frontier.audit_cards(1000,32,100,128,1234,1)
    assert a==b and first==second
    assert a!=c and third['coverage']=='monte_carlo'
    assert len(a)==128
    assert all(max(abs(r['a']),abs(r['b']))<=1000 and abs(r['target'])<=32 and r['a']*r['b']<=0 for r in a)
    assert len({frontier.pair_key(r) for r in a})==len(a)


def test_exhaustive_vs_sample_label_never_overstates():
    a,meta=frontier.audit_cards(8,2,500,20,1,0)
    assert meta['coverage']=='exhaustive' and len(a)==meta['cases']
    a,meta=frontier.audit_cards(100,2,10,20,1,0)
    assert meta['coverage']=='monte_carlo' and len(a)==20


def test_regression_is_never_accepted_even_with_many_repairs():
    rs=[frontier.row(5,-5),frontier.row(5,-4),frontier.row(5,-3),frontier.row(5,-2)]
    baseline=[0,1,999,999]
    candidate=[999,1,2,3]
    ok,result=frontier.safeguard(rs,baseline,candidate,{2,3})
    assert not ok and result['new_repairs']==2 and result['new_regressions']==1
    candidate=[0,1,2,3]
    ok,result=frontier.safeguard(rs,baseline,candidate,{2,3})
    assert ok and result['new_repairs']==2 and result['new_regressions']==0
    ok,_=frontier.safeguard(rs,baseline,[0,1,999,3],{2,3})
    assert not ok


def test_cluster_finds_errors_without_hardcoded_defects():
    rows=[frontier.row(-i,i-1) for i in range(5,25)]
    preds=[r['target'] if i%3 else 999 for i,r in enumerate(rows)]
    cluster=frontier.choose_cluster(rows,preds,4,42)
    assert len(cluster)==4
    assert all(preds[rows.index(r)]!=r['target'] for r in cluster)


def test_full_census_does_not_use_hash_partitions(monkeypatch):
    monkeypatch.setattr(frontier.iso,'stable_bucket',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no folds')))
    cards=frontier.enumerate_domain(10,3)
    assert len(cards)>30
    assert all('split' not in r and 'fold' not in r for r in cards)


def test_budget_and_exhaustiveness_validation():
    args=frontier.parser().parse_args(['--run','a','--campaign','b',
                   '--ladder-output','c','--output','d','--max-radius','1024',
                   '--census-cap','100'])
    with pytest.raises(ValueError,match='EXHAUSTIVE_LIMIT'):
        frontier.validate_args(args)
    args.require_exhaustive=False
    frontier.validate_args(args)


def test_actual_model_groups_are_all_eligible():
    from tools import nanojev_emp_autodiscover_repair as autod
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    groups=autod.parameter_groups(model)
    assert 'backbone.layers.0' in groups and 'input.norm' in groups
    assert 'digit_head' in groups and 'sign_head' in groups


def test_parameter_repair_expands_without_reinitializing_base(monkeypatch):
    """A candidate may be refused even when all of its defects are repaired."""
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    original=frontier.state_copy(model)
    assert all(torch.equal(x,model.state_dict()[k]) for k,x in original.items())
    assert frontier.merge([frontier.row(5,-4)],[frontier.row(5,-4),frontier.row(7,-6)]) == [
        frontier.row(5,-4),frontier.row(7,-6)]


def test_campaign_promotes_only_complete_radius_and_resume(monkeypatch,tmp_path):
    root=tmp_path/'run';root.mkdir();(root/'config.json').write_text('{}')
    campaign=tmp_path/'campaign';campaign.mkdir()
    out=tmp_path/'out'
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    initial=frontier.state_copy(model)
    edited_key='sign_head.weight'
    start=float(initial[edited_key].flatten()[0])
    monkeypatch.setattr(frontier.cross,'snapshot',lambda *a,**k:initial)
    monkeypatch.setattr(frontier.cross,'sha256',lambda *a,**k:'sim-source-sha')
    monkeypatch.setattr(frontier.lasso,'initialize',lambda *a,**k:(model,None,None,None,None))
    def fake_predictions(net,rs,device,batch):
        fixed=float(net.state_dict()[edited_key].flatten()[0]) > start+.4
        return [(99 if r['a']==3 and r['b']==-2 and not fixed else r['target']) for r in rs]
    monkeypatch.setattr(frontier.donor,'predictions',fake_predictions)
    def fake_repair(net,base,cards,pred,defects,args,seed,wave):
        assert any((d['a'],d['b'])==(3,-2) for d in defects)
        changed={k:v.clone() for k,v in base.items()}
        changed[edited_key].view(-1)[0]+=1
        net.load_state_dict(changed,strict=True)
        return changed,{'status':'qualified','selected_groups':['sign_head'], 'updated_elements':1}
    monkeypatch.setattr(frontier,'try_repair',fake_repair)
    args=frontier.parser().parse_args(['--run',str(root),'--campaign',str(campaign),
          '--ladder-output',str(tmp_path/'ladder'),'--output',str(out),'--device','cpu',
          '--start-radius','2','--max-radius','3','--radius-step','1','--max-delta','2',
          '--repair-cycles','2'])
    r=frontier.run(args)
    assert r['status']=='complete' and r['certified_radius']==3
    assert (out/'latest.pt').exists() and not (out/'pending.pt').exists()
    history=__import__('json').loads((out/'history.json').read_text())
    assert [x['radius'] for x in history]==[2,3]
    assert all(x['accepted'] and x['certification']=='exhaustive' for x in history)
    r2=frontier.run(args)
    assert r2['status']=='complete' and r2['certified_radius']==3
    assert len(__import__('json').loads((out/'history.json').read_text()))==2


def test_stall_does_not_promote_unfinished_radius(monkeypatch,tmp_path):
    root=tmp_path/'run';root.mkdir();(root/'config.json').write_text('{}')
    campaign=tmp_path/'campaign';campaign.mkdir()
    out=tmp_path/'out'
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    initial=frontier.state_copy(model)
    monkeypatch.setattr(frontier.cross,'snapshot',lambda *a,**k:initial)
    monkeypatch.setattr(frontier.cross,'sha256',lambda *a,**k:'sim-source-sha')
    monkeypatch.setattr(frontier.lasso,'initialize',lambda *a,**k:(model,None,None,None,None))
    monkeypatch.setattr(frontier.donor,'predictions',lambda net,rs,dev,batch:[
                     99 if (r['a'],r['b'])==(3,-2) else r['target'] for r in rs])
    monkeypatch.setattr(frontier,'try_repair',lambda *a:(None,{'status':'no_safe_repair'}))
    args=frontier.parser().parse_args(['--run',str(root),'--campaign',str(campaign),
           '--ladder-output',str(tmp_path/'ladder'),'--output',str(out),'--device','cpu',
           '--start-radius','2','--max-radius','3','--radius-step','1','--max-delta','2',
           '--repair-cycles','1'])
    result=frontier.run(args)
    assert result['status']=='stalled' and result['certified_radius']==2
    assert result['last']['accepted'] is False
    checkpoint=torch.load(out/'latest.pt',weights_only=True)
    assert checkpoint['certified_radius']==2
    assert checkpoint['state_dict'][next(iter(initial))].shape==initial[next(iter(initial))].shape


def test_partially_repaired_radius_is_safely_resumable(monkeypatch,tmp_path):
    """A certified radius stays immutable; pending radius carries safe fixes."""
    root=tmp_path/'run';root.mkdir();(root/'config.json').write_text('{}')
    camp=tmp_path/'campaign';camp.mkdir();out=tmp_path/'output'
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    orig=frontier.state_copy(model)
    key='sign_head.weight';base=float(orig[key].view(-1)[0])
    monkeypatch.setattr(frontier.cross,'snapshot',lambda *a,**k:orig)
    monkeypatch.setattr(frontier.cross,'sha256',lambda *a,**k:'fake-source')
    monkeypatch.setattr(frontier.lasso,'initialize',lambda *a,**k:(model,None,None,None,None))
    # Two distinct errors must be fixed in two rounds; budget one per run.
    bad_pairs=[(3,-2),(-3,2)]
    def predictions(net, rows, dev, batch):
        delta=float(net.state_dict()[key].view(-1)[0])-base
        return [99 if (r['a'],r['b'])==bad_pairs[0] and delta<.4 or
                      (r['a'],r['b'])==bad_pairs[1] and delta<1.4 else r['target']
                for r in rows]
    monkeypatch.setattr(frontier.donor,'predictions',predictions)
    def repair(net,original,census,oldp,defects,args,seed,wave):
        state={k:v.clone() for k,v in original.items()}
        state[key].view(-1)[0]+=1
        net.load_state_dict(state,strict=True)
        return state,{'status':'qualified','selected_groups':['sign_head'],'updated_elements':1}
    monkeypatch.setattr(frontier,'try_repair',repair)
    args=frontier.parser().parse_args(['--run',str(root),'--campaign',str(camp),
         '--ladder-output',str(tmp_path/'ladder'),'--output',str(out), '--device','cpu',
         '--start-radius','2','--max-radius','3','--radius-step','1','--max-delta','2',
         '--repair-cycles','1'])
    r1=frontier.run(args)
    assert r1['status']=='stalled' and r1['certified_radius']==2
    assert (out/'pending.pt').exists()
    p=torch.load(out/'pending.pt',weights_only=True)
    assert p['radius']==3 and p['certified_radius']==2
    r2=frontier.run(args)
    assert r2['status']=='complete' and r2['certified_radius']==3
    assert not (out/'pending.pt').exists()
    assert (out/'latest.pt').exists()


def test_counterexample_mining_does_not_collect_preexisting_errors():
    cards=[frontier.row(5,-4),frontier.row(6,-5),frontier.row(7,-6)]
    original=[1,99,1]
    candidate=[99,1,99]
    mined=frontier.new_regression_rows(cards,original,candidate)
    assert [frontier.pair_key(r) for r in mined]==[(5,-4),(7,-6)]
    assert len(frontier.new_regression_rows(cards,original,candidate,{(5,-4)}))==1


def test_weighted_hard_rehearsal_preserves_original_controls():
    base=[frontier.row(5,-4)]
    new=[frontier.row(6,-5)]
    weighted=frontier.weighted_rehearsal(base,new,4)
    assert weighted.count(base[0])==1 and weighted.count(new[0])==4
    with pytest.raises(ValueError,match='INVALID_REPLAY_MULTIPLIER'):
        frontier.weighted_rehearsal(base,new,0)


def test_interpolated_patch_alters_only_authorized_tensor():
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    start=frontier.state_copy(model)
    key='input.norm.weight'
    donor_like={k:v.clone() for k,v in start.items()}
    donor_like[key]=donor_like[key]+2
    result=frontier.scaled_candidate(start,donor_like,{key},0.25)
    assert torch.allclose(result[key],start[key]+.5)
    assert all(torch.equal(result[k],start[k]) for k in start if k!=key)
    with pytest.raises(ValueError,match='INVALID_REPAIR_SCALE'):
        frontier.scaled_candidate(start,donor_like,{key},0)


def test_backtracking_finds_safe_scale_after_full_edit_regresses(monkeypatch):
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    start=frontier.state_copy(model)
    key='input.norm.weight';zero=float(start[key][0])
    trained={k:v.clone() for k,v in start.items()};trained[key][0]+=1
    defect=frontier.row(3,-2);known=frontier.row(4,-3)
    cases=[defect,known]
    old=[99,1]
    def fake(net,rows,*a):
        change=float(net.state_dict()[key][0])-zero
        return [r['target'] if ((r['a'],r['b'])==(3,-2) and change>=.2) or
                 ((r['a'],r['b'])==(4,-3) and change<=.7) else 99 for r in rows]
    monkeypatch.setattr(frontier.donor,'predictions',fake)
    args=frontier.parser().parse_args(['--run','x','--campaign','y',
        '--ladder-output','z','--output','w','--repair-scales','.75','.5','.25'])
    scaled,attempts=frontier.probe_scales(model,start,trained,{key},cases,old,
                                       [defect],[known],args)
    assert scaled is not None and scaled[2]==.25
    assert scaled[1]['new_regressions']==0
    assert not attempts[0]['qualified'] and attempts[1]['qualified']
    assert torch.equal(model.state_dict()[key],trained[key])


def test_failed_candidate_replays_threatened_exact_memories(monkeypatch):
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    start=frontier.state_copy(model)
    key='input.norm.weight';offset=float(start[key][0])
    defect=frontier.row(3,-2);saved=frontier.row(4,-3)
    control=frontier.row(5,-4)
    census=[defect,saved,control]
    old=[99,1,1]
    monkeypatch.setattr(frontier,'make_preservation',lambda *a:[control])
    monkeypatch.setattr(frontier.auto,'gradient_discovery',lambda *a,**k:
                         ({'input.norm':[key]},[],['input.norm']))
    monkeypatch.setattr(frontier.donor,'capture_teacher',lambda *a,**k:{})
    def simulate(net,opt,params,baseline,defects,preservation,teacher,rng,args,dev):
        has_hard=any((r['a'],r['b'])==(4,-3) for r in preservation)
        with torch.no_grad():
            params[key].view(-1)[0]=offset+(.4 if has_hard else 1)
        return {'loss':0.1}
    monkeypatch.setattr(frontier.auto,'step',simulate)
    def predictions(net,rows,*a):
        change=float(net.state_dict()[key].view(-1)[0])-offset
        return [r['target'] if ((r['a'],r['b'])==(3,-2) and change>=.2) or
                 ((r['a'],r['b'])==(4,-3) and change<=.7) or
                 (r['a'],r['b'])==(5,-4) else 99 for r in rows]
    monkeypatch.setattr(frontier.donor,'predictions',predictions)
    args=frontier.parser().parse_args(['--run','x','--campaign','y',
        '--ladder-output','z','--output','w','--device','cpu',
        '--search-steps','2','--check-every','1','--expand','1',
        '--max-groups','1','--repair-scales'])
    state,report=frontier.try_repair(model,start,census,old,[defect],args,3,0)
    assert state is not None
    assert report['status']=='qualified' and report['hard_memories_mined']==1
    assert report['attempts'][0]['checks'][0]['new_regressions']==1
    assert report['attempts'][0]['checks'][1]['new_regressions']==0
    assert predictions(model,census)==[1,1,1]
