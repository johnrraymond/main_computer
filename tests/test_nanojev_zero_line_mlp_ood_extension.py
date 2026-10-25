"""CPU checks for additional training and fixed-population OOD trend diagnostics."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import torch

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_gaussian_radius import make_cache
from tools.nanojev_zero_line_head_radius_growth import TRAIN_BANDS, OOD_BANDS, sampled_cards
from tools.nanojev_zero_line_incremental_mlp_banks import (
    SCHEMA as PARENT_SCHEMA, IntervalMLPBank, interval_report,
    interval_subset, logits_for_bank,
)
from tools.nanojev_zero_line_mlp_ood_extension import (
    SCHEMA, extension_step, run, validate_parent,
)
from tools.nanojev_zero_line_radius255_probe import sha256


class FakePerfectModel(torch.nn.Module):
    """Synthetic feature provider; not a genuine benchmark/model result."""
    def __init__(self):
        super().__init__()
        self.query=torch.nn.Parameter(torch.zeros(8))
        self.norm=torch.nn.Identity()

    def forward(self, x):
        a=x[:,0].float();b=x[:,1].float()
        features=torch.stack((a/500,b/500,(a+b)/500,a*b/250000,
                              a.sign(),b.sign(),a.abs()/500,b.abs()/500),-1)
        self.norm(features)
        y=x.sum(-1)
        logits=torch.full((len(y),73),-10.,device=x.device)
        logits.scatter_(1,(y.sign()+1).long().unsqueeze(-1),20.)
        digits=target_digits(y)
        for pos in range(7):
            logits.scatter_(1,(digits[:,pos]+3+pos*10).unsqueeze(-1),20.)
        return {'sign':logits[:,:3], 'digits':logits[:,3:].reshape(-1,7,10)}


def fixture(tmp_path, *, interval=(16,16)):
    root=tmp_path/'ladder';root.mkdir()
    ckpt=root/'certified.pt';torch.save({'source':True},ckpt)
    source={'checkpoint':str(ckpt),'checkpoint_sha256':sha256(ckpt),'step':4800}
    parent=tmp_path/'parent';parent.mkdir()
    param={'intervals':f'{interval[0]}:{interval[1]}','steps_per_bank':3000,
           'seed':43,'train_per_cell':1,'dev_per_cell':1,'ood_per_cell':1,
           'min_cell_count':1,'min_cell_rate':.8}
    config={'schema':PARENT_SCHEMA, 'source_sha256':source['checkpoint_sha256'],
            'intervals':[interval], 'train_bands':TRAIN_BANDS,
            'ood_bands':OOD_BANDS,'parameters':param}
    (parent/'config.json').write_text(json.dumps(config))
    bank_folder=parent/f'bank_{interval[0]}_{interval[1]}';bank_folder.mkdir()
    model=FakePerfectModel()
    tr=interval_subset(make_cache(model,sampled_cards(interval[1],TRAIN_BANDS,43,'train',1),'cpu',1000),*interval)
    dv=interval_subset(make_cache(model,sampled_cards(interval[1],TRAIN_BANDS,43,'dev',1),'cpu',1000),*interval)
    od=interval_subset(make_cache(model,sampled_cards(interval[1],OOD_BANDS,43,'ood',1),'cpu',1000),*interval)
    bank=IntervalMLPBank(8,*interval)
    with torch.no_grad():
        # Known deterministic synthetic bank parameters.
        for t in bank.parameters():t.zero_()
    baseline_dev=interval_report(logits_for_bank(bank,dv),dv,*interval,.8,1)
    baseline_ood=interval_report(logits_for_bank(bank,od),od,*interval,.8,1)
    torch.save({'schema':PARENT_SCHEMA+'.specialist','source_sha256':source['checkpoint_sha256'],
                'interval':list(interval),'bank':bank.state_dict(),'training_history':[]},
               bank_folder/'specialist.pt')
    (bank_folder/'result.json').write_text(json.dumps({'parameters':{'mlp_hidden':bank.hidden},
        'specialist':{'dev':baseline_dev,'ood':baseline_ood}}))
    args=SimpleNamespace(ladder_output=root,mlp_output=parent,output=tmp_path/'extension',
        device='cpu',interval=f'{interval[0]}:{interval[1]}',additional_steps=3,probe_every=1,
        batch_size=8,eval_batch_size=1000,train_per_cell=1,dev_per_cell=1,
        ood_per_cell=1,min_cell_count=1,min_cell_rate=.8,lr=.001,seed=43,cpu_threads=2,resume=False)
    return args,model,source


def test_rejects_wrong_source_or_family(tmp_path):
    args,model,source=fixture(tmp_path)
    with pytest.raises(ValueError,match='SOURCE_MISMATCH'):
        validate_parent(args.mlp_output,args.ladder_output,'incorrect',(16,16),43)
    with pytest.raises(ValueError,match='SEED_MISMATCH'):
        validate_parent(args.mlp_output,args.ladder_output,source['checkpoint_sha256'],(16,16),99)
    with pytest.raises(ValueError,match='INTERVAL_NOT_FOUND'):
        validate_parent(args.mlp_output,args.ladder_output,source['checkpoint_sha256'],(17,17),43)


def test_extension_only_mutates_specialist_and_can_resume(tmp_path):
    args,model,source=fixture(tmp_path)
    old_bank=args.mlp_output/'bank_16_16'/'specialist.pt'
    original=old_bank.read_bytes()
    certificate=Path(source['checkpoint']).read_bytes()
    with mock.patch('tools.nanojev_zero_line_mlp_ood_extension.load_certified_model',
                    return_value=(model,source)):
        rows=run(args)
        assert len(rows)==4
        assert rows[0]['step_added']==0 and rows[-1]['step_added']==3
        assert [row['step_added'] for row in rows]==[0,1,2,3]
        assert all(row['ood']['cards']>0 for row in rows)
        assert old_bank.read_bytes()==original
        assert Path(source['checkpoint']).read_bytes()==certificate
        result=json.loads((Path(args.output)/'result.json').read_text())
        assert result['ood_used_for_training'] is False
        assert result['ood_used_for_selection'] is False
        assert result['optimizer_continuation'].startswith('fresh_adamw')
        assert (Path(args.output)/'latest.pt').exists()
        with pytest.raises(FileExistsError):run(args)
        args.resume=True
        rows_again=run(args)
        assert rows_again==rows
    assert old_bank.read_bytes()==original


def test_training_step_requires_train_cache_only():
    torch.manual_seed(7)
    bank=IntervalMLPBank(8,16,16)
    cache={'features':torch.randn(8,8), 'target':torch.tensor([16,-16]*4)}
    opt=torch.optim.AdamW(bank.parameters(),lr=.001)
    before=bank.output[0].weight.detach().clone()
    value=extension_step(bank,opt,cache,__import__('random').Random(3),16,8)
    assert value>0
    assert not torch.equal(before,bank.output[0].weight)


def test_interrupted_training_resumes_same_sample_trajectory(tmp_path):
    args,model,source=fixture(tmp_path)
    import tools.nanojev_zero_line_mlp_ood_extension as ext
    genuine=ext.extension_step
    counter={'n':0}

    def interrupt_after_two(*a,**kw):
        counter['n']+=1
        if counter['n']==3:
            raise RuntimeError('synthetic power interruption')
        return genuine(*a,**kw)

    with mock.patch.object(ext,'load_certified_model',return_value=(model,source)):
        with mock.patch.object(ext,'extension_step',side_effect=interrupt_after_two):
            with pytest.raises(RuntimeError,match='power interruption'):
                run(args)
        args.resume=True
        resumed=run(args)
        resumed_state=torch.load(Path(args.output)/'latest.pt',weights_only=True)
        args.output=tmp_path/'uninterrupted'
        args.resume=False
        uninterrupted=run(args)
        continuous_state=torch.load(Path(args.output)/'latest.pt',weights_only=True)
    assert resumed==uninterrupted
    assert resumed_state['step_added']==continuous_state['step_added']==3
    for key,value in resumed_state['bank'].items():
        assert torch.equal(value,continuous_state['bank'][key])


def test_rejects_incompatible_sample_count_or_baseline(tmp_path):
    args,model,source=fixture(tmp_path)
    args.dev_per_cell=2
    with mock.patch('tools.nanojev_zero_line_mlp_ood_extension.load_certified_model',
                    return_value=(model,source)):
        with pytest.raises(ValueError,match='PARENT_SETTING_MISMATCH'):
            run(args)
