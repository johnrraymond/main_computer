import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_zero_line_absolute_magnitude_banks as absb
from tools.nanojev_addition_curriculum import target_digits


def logits_for(values):
    y = torch.as_tensor(values,dtype=torch.long)
    out = torch.full((len(y),73),-12.,dtype=torch.float)
    out[torch.arange(len(y)), y.sign()+1] = 12.
    digits = target_digits(y)
    for col in range(7):
        out[torch.arange(len(y)), 3 + col*10 + digits[:,col]] = 12.
    return out


def synthetic_cache(rows, device='cpu'):
    if isinstance(rows,dict):
        operands=rows['x'].long().clone()
        target=rows['y'].long().clone()
    else:
        operands=torch.tensor([(r[0],r[1]) for r in rows],dtype=torch.long)
        target=torch.tensor([r[2] for r in rows],dtype=torch.long)
    x=operands.float()
    # Fake NUMERICAL features for isolated CPU model tests.
    features=torch.cat((x/512., x.abs()/512.,(x[:,0:1]+x[:,1:2])/512.,
                        (x[:,0:1]-x[:,1:2])/512., x.sign()),-1)
    return {'features':features,'original':logits_for(target),
            'target':target,'operands':operands,'bands':None}


def test_absolute_bands_and_boundary():
    assert absb.parse_bands('16:32,128:159') == ((16,32),(128,159))
    for value in ('16:32,32:64','160:191,128:159','127:159','0:4','16-32','512:520'):
        with pytest.raises(ValueError):absb.parse_bands(value)
    assert absb.bank_for_inputs(159, -159, ((128,159),))==0
    assert absb.bank_for_inputs(159, -143, ((128,159),))==0
    assert absb.bank_for_inputs(-143, 159, ((128,159),))==0
    assert absb.bank_for_inputs(-159, -159, ((128,159),))==0
    assert absb.bank_for_inputs(127, -127, ((16,32),(128,159))) is None
    assert absb.bank_for_inputs(20,-20,((16,32),)) is None
    assert absb.bank_for_inputs(191,-191,((128,159),)) is None


def test_partition_by_actual_absolute_input_magnitude():
    data=absb.sample_absolute_band(128,138,20261025,per_stratum=64)
    mags={}
    signed={}
    for split,rows in data.items():
        assert rows
        for a,b,d,_,_ in rows:
            assert d==a+b
            m=absb.absolute_magnitude(a,b)
            assert 128<=m<=138
            if split=='ood':assert absb.heldout_magnitude(m,20261025)
            else:
                assert not absb.heldout_magnitude(m,20261025)
                assert absb.family_fold(a,b,20261025)==split
            mags.setdefault(m,set()).add(split)
            fam=(tuple(sorted((abs(a),abs(b)))) ,m)
            signed.setdefault(fam,set()).add(split)
    assert all(len(v)==1 for v in mags.values() if 'ood' in v)
    assert all(len(v)==1 for v in signed.values())
    assert set(data)=={'train','dev','ood'}
    assert all(absb.residual_group(r[2]) in absb.GROUPS for rows in data.values() for r in rows)


def test_bank_receives_only_frozen_features():
    bank=absb.AbsoluteBank(8,hidden=12)
    feats=torch.randn(5,8)
    assert bank(feats).shape==(5,73)
    assert all('coordinate' not in name and 'gaussian' not in name
               for name,_ in bank.named_parameters())


def test_composition_preserves_certified_zero_line():
    cards=[(1,-1,0,0,1),(16,-15,1,0,1),(127,-125,2,0,1),
           (128,-128,0,0,1),(151,-135,16,0,1),(160,-160,0,0,1)]
    cache=synthetic_cache(cards)
    class WrongBank(nn.Module):
        def forward(self,features):return logits_for(torch.full((len(features),),32,dtype=torch.long))
    candidate=((128,159),WrongBank())
    logits=absb.assembled_logits(cache,[],candidate)
    correct=absb.exact_mask(logits,cache['target'])
    assert correct.tolist()==[True,True,True,False,False,True]
    # Even an explicitly named 16:32 bank CANNOT override the certified base.
    in_base=absb.assembled_logits(cache,[],((16,32),WrongBank()))
    assert torch.equal(in_base,cache['original'])


def test_score_detects_near_zero_damage():
    data=synthetic_cache([(128,-128,0,0,1),(151,-150,1,0,1),
                          (159,-143,16,0,1),(159,159,318,0,1)])
    bad=logits_for([1,1,16,318])
    result=absb.score(bad,data,baseline=data['original'])
    assert result['groups']['zero']['newly_wrong']==1
    assert result['groups']['frontier']['accuracy']==1.
    assert result['newly_wrong']==1


def test_isolated_train_cpu_smoke():
    rows=absb.sample_absolute_band(16,32,57,per_stratum=12)
    tr,dev,ood=[synthetic_cache(rows[k]) for k in ('train','dev','ood')]
    net=absb.AbsoluteBank(8,hidden=16)
    history=absb.train_bank(net,tr,dev,ood,steps=2,batch_size=16,probe_every=1,
                            lr=.0003,seed=8,prefix='CPU')
    assert len(history)==2
    assert history[-1]['dev']['accuracy'] is not None
    assert history[-1]['ood']['accuracy'] is not None


def test_end_to_end_cpu_source_untouched(tmp_path,monkeypatch):
    file=tmp_path/'source.pt'
    file.write_bytes(b'untouched certified source')
    original=file.read_bytes()
    class TinyBase(nn.Module):
        def __init__(self):
            super().__init__()
            self.query=nn.Parameter(torch.zeros(8))
    model=TinyBase()
    monkeypatch.setattr(absb,'load_certified_model',lambda path,device:(model,{
        'checkpoint':str(file),'checkpoint_sha256':absb.sha256(file),'step':4800}))
    monkeypatch.setattr(absb,'make_cache',lambda model,cards,device,batch_size:synthetic_cache(cards))
    monkeypatch.setattr(absb,'deck',lambda radius:{'x':torch.tensor([[0,0],[1,-1],[127,-127]]),
                                                     'y':torch.tensor([0,0,0])})
    args=SimpleNamespace(ladder_output=str(tmp_path/'certified'),output=str(tmp_path/'new_output'),
        bands='16:32,128:132',device='cpu',steps_per_bank=2,probe_every=1,batch_size=12,
        eval_batch_size=256,per_stratum=10,hidden=12,lr=.0003,seed=20261025,
        min_accuracy=.8,cpu_threads=1)
    report=absb.run(args)
    assert file.read_bytes()==original
    assert report['source_immutable'] and report['original_deck_unchanged']
    assert report['results'][0]['status']=='DIAGNOSTIC_IN_CERTIFIED_DOMAIN'
    assert report['results'][0]['eligible'] is False
    assert report['results'][1]['integrated']['retention']['accuracy']==1.
    assert (tmp_path/'new_output'/'bank_128_132'/'candidate.pt').is_file()
    assert json.loads((tmp_path/'new_output'/'result.json').read_text())['test_untouched']


def test_fail_closed_on_preexisting_output(tmp_path, monkeypatch):
    path=tmp_path/'out'; path.mkdir();(path/'marker').write_text('keep')
    args=SimpleNamespace(ladder_output=str(tmp_path/'src'),output=str(path),bands='128:159',
        device='cpu',steps_per_bank=1,probe_every=1,batch_size=4,eval_batch_size=1,
        per_stratum=1,hidden=8,lr=.0003,seed=12,min_accuracy=.8,cpu_threads=1)
    with pytest.raises(FileExistsError):absb.run(args)
    assert (path/'marker').read_text()=='keep'
