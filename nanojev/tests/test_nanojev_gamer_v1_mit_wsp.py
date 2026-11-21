"""Focused numerical checks for genuine direct-weight programming; no CUDA or corpus required."""

import pytest
import torch

from tools import nanojev_gamer_v1_mit_wsp as w


def toy():
    return {
        'pair_projection.weight':torch.arange(24,dtype=torch.float32).reshape(4,6)/100,
        'pair_projection.bias':torch.tensor([.01,.02,.03,.04]),
        'binding.weight':torch.arange(12,dtype=torch.float32).reshape(3,4)/10,
        'binding.bias':torch.tensor([.01,.03,-.02]),
        'project.1.weight':torch.randn(5,7),
    }


def proposal(*ops):
    return {'schema':w.SCHEMA,
            'diagnosis':'The faulty score comes from one overrepresented input channel.',
            'editor':'human-reviewed-reasoning-model',
            'operations':list(ops)}


def test_direction_projection_is_exact_no_grad_and_frozen_parameters_preserved():
    source=toy()
    old={k:v.clone() for k,v in source.items()}
    result,report=w.execute_operations(source,proposal({
        'op':'remove_output_direction','tensor':'pair_projection.weight',
        'columns':[1],'direction':[1,0,0,0],'alpha':1,
        'reason':'Isolate first output direction that causes spurious activation'}),
        max_fraction=.1)
    assert result['pair_projection.weight'][0,1]==0
    assert torch.equal(result['pair_projection.weight'][1:,1],old['pair_projection.weight'][1:,1])
    assert torch.equal(result['project.1.weight'],old['project.1.weight'])
    assert all(torch.equal(source[k],old[k]) for k in source)
    assert report['method'].endswith('NO_GRADIENTS')
    assert report['changed_coefficients']==1


def test_invalid_tensors_and_large_edits_refused():
    source=toy()
    with pytest.raises(ValueError,match='FORBIDDEN_TENSOR_EDIT'):
        w.execute_operations(source,proposal({'op':'zero_columns',
            'tensor':'project.1.weight','columns':[0],
            'reason':'Never touch the frozen G1 project weight'}))
    with pytest.raises(ValueError,match='SPARSE_EDIT_BUDGET_EXCEEDED'):
        w.execute_operations(source,proposal({'op':'zero_columns',
            'tensor':'pair_projection.weight','columns':[0,1],
            'reason':'This changes more weights than the allowed edit budget'}))


def test_scoped_manual_set_and_provenance_required():
    source=toy()
    with pytest.raises(ValueError,match='INVALID_PROPOSAL_SCHEMA'):
        w.execute_operations(source,{'schema':'wrong','operations':[]})
    changed,summary=w.execute_operations(source,proposal({
        'op':'set','tensor':'binding.bias',
        'changes':[{'index':[1],'value':.5}],
        'reason':'Reduce one false margin by adjusting a single output bias'}),
        max_fraction=.1)
    assert changed['binding.bias'][1]==.5
    assert summary['changed_coefficients']==1
    assert torch.equal(changed['binding.weight'],source['binding.weight'])


def test_reopen_legacy_wsp_checkpoint_with_real_gamer_loader(tmp_path):
    """Previous source patch wrote a nonstandard status; resume must load it.

    Use the real Gamer graph-head loader, not a mocked checkpoint interface.
    Fail on corruption or silently falling back to the unedited coefficients.
    """
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    import json
    import hashlib

    parent=tmp_path/'parent';parent.mkdir()
    edited=tmp_path/'edited';edited.mkdir()
    config={'hidden_size':16,'width':16,'layers':1,'latent_slots':2,
            'memory_slots':2,'max_operands':2,'max_span_tokens':4,
            'oracle_coupling':False,'object_extractor':'joint-span-v1'}
    model=GamerGraphHead(**{k:v for k,v in config.items()
                             if k!='object_extractor'})
    state={k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()}
    save_file(state,str(parent/cur.HEAD_FILE))
    edited_state={k:v.clone() for k,v in state.items()}
    edited_state['binding.bias'][4]-=.75
    save_file(edited_state,str(edited/cur.HEAD_FILE))
    h=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    parent_manifest={'schema':cur.SCHEMA,'status':'TRAINED_NOT_CERTIFIED',
                     'head':config,'head_sha256':h(parent/cur.HEAD_FILE)}
    (parent/'manifest.json').write_text(json.dumps(parent_manifest))
    edit_manifest={**parent_manifest,
        'status':'MIT_WSP_DIRECT_EDIT_NOT_CERTIFIED',
        'head_sha256':h(edited/cur.HEAD_FILE),
        'mit_wsp':{'source_head_sha256':h(parent/cur.HEAD_FILE),
                   'no_gradients':True}}
    (edited/'manifest.json').write_text(json.dumps(edit_manifest))
    report={'source_head_sha256':h(parent/cur.HEAD_FILE),
            'edited_head_sha256':h(edited/cur.HEAD_FILE)}
    with pytest.raises(ValueError,match='GAMER_GRAPH_BAD_CHECKPOINT_SCHEMA'):
        cur._load_checkpoint(edited,device='cpu')
    restored=w.load_edited_head(parent,edited,report,device='cpu')
    assert torch.equal(restored.state_dict()['binding.bias'],
                       edited_state['binding.bias'])
    assert not torch.equal(restored.state_dict()['binding.bias'],
                           state['binding.bias'])
    assert all(torch.equal(restored.state_dict()[k],state[k])
               for k in state if k!='binding.bias')
    assert (edited/'manifest.json').read_text()==json.dumps(edit_manifest)
    with torch.no_grad():
        edited_state['binding.bias'][4]=0
        save_file(edited_state,str(edited/cur.HEAD_FILE))
    with pytest.raises(ValueError,match='EDITED_CHECKPOINT_METADATA_DRIFT'):
        w.load_edited_head(parent,edited,report,device='cpu')


def test_run_resumes_after_prior_edit_without_preparing_or_applying(tmp_path,monkeypatch):
    dest=tmp_path/'oracle-fabric-g2-mit-wsp-0001'
    (dest/'seal').mkdir(parents=True)
    (dest/'seal'/'commit.json').write_text('{}')
    (dest/'edit-report.json').write_text('{}')
    invoked=[]
    def no_repeat(**kw):
        raise AssertionError('A completed seal/edit must not be repeated')
    def fake_predict(**kwargs):
        invoked.append(('predict',kwargs['device']))
        (dest/'blind').mkdir()
        (dest/'blind'/'lock.json').write_text('{}')
    def fake_score(**kwargs):
        invoked.append(('score',None))
        return {'status':'SCORED'}
    monkeypatch.setattr(w,'prepare',no_repeat)
    monkeypatch.setattr(w,'apply',no_repeat)
    monkeypatch.setattr(w,'predict',fake_predict)
    monkeypatch.setattr(w,'score',fake_score)
    assert w.run(root=tmp_path,device='cuda')=={'status':'SCORED'}
    assert invoked==[('predict','cuda'),('score',None)]
