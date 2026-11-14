"""Carry generalization and protected-rehearsal regression tests."""
from __future__ import annotations
import json

import pytest
import torch

from tools import nanojev_addition_curriculum as base
from tools import nanojev_addition_carry_stability as lab


@pytest.fixture(autouse=True)
def one_thread():
    torch.set_num_threads(1)


def make_source(tmp_path):
    f = tmp_path / 'signed_passed.pt'
    model = base.AdditionNet('digit_tokens',16,4,1,1,seed=19)
    identity={'schema':base.SCHEMA,'source_sha256':base.sha256(base.__file__),
              'options':{'seed':19,'width':16,'heads':4,'layers':1,
                         'rounds':1,'dev_per_group':8}}
    base.write_torch(f, {'schema':base.SCHEMA+'.passed', 'stage':'signed',
                         'arm':'digit_tokens','identity':identity,
                         'model':model.state_dict(),
                         'result':{'gate':{'pass':True}}})
    return f


def opts(source, output, *extra):
    return lab.parser().parse_args(['--signed-checkpoint',str(source),
        '--output',str(output),'--device','cpu','--cpu-threads','1',
        '--steps','2','--probe-every','1','--batch-size','32',
        '--train-per-group','8','--dev-per-group','4',
        '--replay-per-group','4',*extra])


def test_classify_carries():
    assert lab.category(111,222)=='no_carry'
    assert lab.category(125,115)=='single_carry'
    assert lab.category(199,801)=='chain_carry'
    assert lab.carry_count(199,801)==3


def test_dataset_has_hard_groups_and_split_isolation():
    sets={s:lab.make_dataset(s,20261024,32) for s in ('train','dev','test')}
    magnitudes=[]
    for split,d in sets.items():
        assert len(d['y'])==96
        assert set(d['groups'])==set(lab.CATEGORIES)
        assert all(d['groups'].count(g)==32 for g in lab.CATEGORIES)
        pairs={tuple(sorted(row)) for row in d['x'].tolist()}
        magnitudes.append(pairs)
        assert len(pairs)==96
        for row,truth,g in zip(d['x'].tolist(),d['y'].tolist(),d['groups']):
            assert lab.category(*row)==g
            assert sum(row)==truth
            assert base.pair_fold(*row,20261024)==split
    assert all(magnitudes[i].isdisjoint(magnitudes[j])
               for i in range(3) for j in range(i+1,3))


def test_batch_contains_replay_from_all_stages():
    assert lab.replay_allocation(128,.5)=={
        'atomic':5,'no_carry':10,'carry':16,'signed':33}
    assert sum(lab.replay_allocation(128,.7).values())==90
    assert lab.replay_allocation(128,.7)['signed'] > lab.replay_allocation(128,.5)['signed']


def test_gate_requires_retention_and_all_carry_categories():
    truth=torch.tensor([100,200,300])
    categories=list(lab.CATEGORIES)
    dev={'digit':base.score(truth,truth,categories)}
    old={s:{'digit':{'all':{'rate':1}, 'g':{'rate':1}}} for s in lab.SOURCE_STAGES}
    assert lab.decision(dev,old,.8)['pass']
    old['signed']['digit']['g']['rate']=.79
    assert not lab.decision(dev,old,.8)['pass']
    old['signed']['digit']['g']['rate']=1.
    old['atomic']['digit']['g']['rate']=.99
    assert not lab.decision(dev,old,.8)['pass']
    old['atomic']['digit']['g']['rate']=1.
    dev['digit']['chain_carry']['rate']=.5
    assert not lab.decision(dev,old,.8)['pass']


def test_bottleneck_checkpoint_selection_prefers_retention():
    dev={'digit':{'all':{'rate':.85}}}
    weak={'pass':False,'bottleneck_ratio':.5,'dev_min':.95}
    protected={'pass':False,'bottleneck_ratio':.9,'dev_min':.81}
    assert lab.rank(protected,dev)>lab.rank(weak,dev)
    passing={'pass':True,'bottleneck_ratio':1.,'dev_min':.80}
    assert lab.rank(passing,dev)>lab.rank(protected,dev)


def test_runs_without_test_and_keeps_source_immutable(tmp_path,monkeypatch):
    source=make_source(tmp_path)
    before=base.sha256(source)
    calls=[]
    real=lab.make_dataset
    def check(split,*a,**k):
        calls.append(split)
        assert split!='test'
        return real(split,*a,**k)
    monkeypatch.setattr(lab,'make_dataset',check)
    out=tmp_path/'lab'
    result=lab.run(opts(source,out))
    assert set(result)==set(lab.ARMS)
    assert set(calls)=={'train','dev'}
    assert base.sha256(source)==before
    summary=json.loads((out/'summary.json').read_text())
    assert summary['TEST'] is None and not summary['hybridlink_certified']
    for arm in lab.ARMS:
        path=out/'arms'/arm
        assert (path/'baseline.json').is_file()
        assert (path/'best.pt').is_file()
        assert (path/'latest.pt').is_file()
        record=json.loads((path/'best.json').read_text())
        assert set(record['replay_counts'])==set(lab.SOURCE_STAGES)
        assert all(s in record['retention'] for s in lab.SOURCE_STAGES)
    with pytest.raises(FileExistsError,match='OVERWRITE_REFUSED'):
        lab.run(opts(source,out))


def test_resume_deterministic_and_drift_refused(tmp_path):
    source=make_source(tmp_path)
    full=tmp_path/'full'; part=tmp_path/'part'
    lab.run(opts(source,full,'--arms','balanced','--steps','4', '--probe-every','2'))
    lab.run(opts(source,part,'--arms','balanced','--steps','2','--probe-every','2'))
    lab.run(opts(source,part,'--arms','balanced','--steps','4','--probe-every','2','--resume'))
    a=torch.load(full/'arms'/'balanced'/'latest.pt',map_location='cpu',weights_only=True)
    b=torch.load(part/'arms'/'balanced'/'latest.pt',map_location='cpu',weights_only=True)
    assert a['step']==b['step']==4
    assert all(torch.equal(a['model'][k],b['model'][k]) for k in a['model'])
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        lab.run(opts(source,part,'--arms','balanced','--steps','6','--learning-rate','.005','--resume'))


def test_decode_depends_on_model_not_supplied_sum():
    out={'sign':torch.full((1,3),-100.),'digits':torch.full((1,7,10),-100.)}
    out['sign'][0,2]=100.
    for i,n in enumerate((3,2,1,0,0)):
        out['digits'][0,i,n]=100.
    assert base.decode(out,5).item()==123


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA unavailable')
def test_decoder_cuda_parity():
    pred={'sign':torch.randn(8,3),'digits':torch.randn(8,7,10)}
    assert torch.equal(base.decode(pred,5),base.decode({k:v.cuda() for k,v in pred.items()},5).cpu())
