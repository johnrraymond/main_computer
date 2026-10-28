import csv
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_emp_natural_repair_tomography as tomo


def test_contrasts_hold_out_target():
    repaired={(0,0):torch.tensor([10.,1.]), (1,1):torch.tensor([10.,5.]),
              (2,2):torch.tensor([10.,9.])}
    stable={(3,3):torch.tensor([0.,0.]),(4,4):torch.tensor([0.,0.])}
    a=tomo.contrasting_channels(repaired,stable,(0,0),2)
    modified=dict(repaired);modified[(0,0)]*=1000
    b=tomo.contrasting_channels(modified,stable,(0,0),2)
    assert a==b and a[0]['channel']==0


def test_nearest_control_deterministic():
    controls=[(0,0),(3,3),(3,5),(4,4)]
    assert tomo.nearest_stable((3,4),controls,2)==[(3,3),(3,5)]


def test_baseline_requires_exact_card_conservation(tmp_path):
    f=tmp_path/'natural.csv'
    f.write_text('a,b,target,before,after,repaired,regressed\n-455,442,-13,-113,-13,True,False\n')
    cases=tomo.read_baseline(f)
    x=[{'card':[-455,442,-13], 'prediction':-113}]
    y=[{'card':[-455,442,-13], 'prediction':-13}]
    assert tomo.verify_baseline(cases,x,y)[(-455,442)]==x[0]
    with pytest.raises(RuntimeError, match='PREDICTION_DRIFT'):
        tomo.verify_baseline(cases,x,[{'card':[-455,442,-13],'prediction':-113}])
    f.write_text('a,b,target,before,after,repaired,regressed\n-455,442,-12,-113,-13,True,False\n')
    with pytest.raises(RuntimeError,match='INVALID_ARITHMETIC'):
        tomo.read_baseline(f)


def test_site_capture_baseline_and_edit_same_module(monkeypatch):
    class Toy(nn.Module):
        def __init__(self):
            super().__init__();self.norm=nn.Identity()
        def forward(self,x):
            y=self.norm(x)
            return {'sign':torch.stack((y[:,0],-y[:,0],y[:,0]),dim=1),
                    'digits':torch.stack((y[:,0],-y[:,0]),dim=1).unsqueeze(1)}
    model=Toy()
    monkeypatch.setattr(tomo.ff,'activation_modules',lambda m:[('norm',m.norm)])
    monkeypatch.setattr(tomo,'input_tensor',lambda cards,device:torch.tensor([[float(cards[0][0])]]))
    monkeypatch.setattr(tomo.dual,'decoded',lambda out:[int(out['sign'][0,0]<out['sign'][0,1])])
    base=tomo.capture_site(model,[3,0,3], 'cpu', 'norm@0')
    assert base['prediction']==0 and base['vector'][0]==3
    sham=tomo.capture_site(model,[3,0,3], 'cpu', 'norm@0',0,3)
    assert sham['prediction']==base['prediction']
    edit=tomo.capture_site(model,[3,0,3], 'cpu', 'norm@0',0,-3)
    assert edit['prediction']==1
    with pytest.raises(RuntimeError,match='INVOCATION_MISSING'):
        tomo.capture_site(model,[3,0,3], 'cpu', 'norm@1')


def test_tomography_summary():
    base={'status':'tested','forward_repaired':False,'reverse_regressed':False,
          'matched_control_repaired':False,'forward_controls_damaged':0,
          'reverse_controls_damaged':0}
    a={**base,'forward_repaired':True,'reverse_regressed':True}
    b={**base,'matched_control_repaired':True,'forward_controls_damaged':2}
    result=tomo.summarize_effects([a,b,{'status':'parity_drift'}])
    assert result=={'attempts':3,'tested':2,'forward_repairs':1,'reverse_regressions':1,
                     'both_directions':1,'matched_control_repairs':1,
                     'forward_control_damage':2,'reverse_control_damage':0}
