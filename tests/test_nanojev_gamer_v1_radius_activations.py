"""Radius activation analysis tests; no model download or CUDA required."""
from __future__ import annotations

import numpy as np
import pytest

from tools import nanojev_gamer_v1_radius_activations as radius


def _case(prompt='The ship has 17 engines. Determine the total fuel consumed.'):
    return {'game_id':'ship','case_sha256':'test','split':'dev','prompt_08':prompt,
            'source':{'kind':'scalar','parameters':{'engines':17}}}


def test_one_public_numeric_register_selected_and_single_span_edited():
    case=_case()
    selected=radius.select_public_register(case)
    assert selected[0]=='engines'
    assert radius.intervene(case['prompt_08'],selected,-2)=='The ship has -2 engines. Determine the total fuel consumed.'
    assert radius.intervene(case['prompt_08'],selected,0)=='The ship has +0 engines. Determine the total fuel consumed.'
    assert radius.intervene(case['prompt_08'],selected,2)=='The ship has +2 engines. Determine the total fuel consumed.'


def test_reject_ambiguous_or_embedded_literal():
    assert radius.select_public_register(_case('17 engines and 17 sailors determine the cost.')) is None
    assert radius.select_public_register(_case('117 engines determine the cost of sailing the ship.')) is None
    assert radius.select_public_register(_case('There are 17')) is None


def test_not_replace_probability_or_hidden_source_fields():
    case=_case('Chance is 0.8 and nothing else matters to the next decision.')
    case['source']['parameters']={'probability':0.8}
    assert radius.select_public_register(case) is None
    case['source']['kind']='matrix'
    assert radius.select_public_register(case) is None


def test_varied_game_contexts_not_repeat_game():
    cases=[_case() for _ in range(4)]
    cases[2]['game_id']='another'
    cases[3]['game_id']='third'
    assert len(radius.contexts_from_rows(cases,count=3))==3
    with pytest.raises(ValueError,match='ENOUGH'):
        radius.contexts_from_rows([_case()],count=3)


def test_integer_radius_validation():
    assert radius.radius_values(2)==(-2,-1,0,1,2)
    with pytest.raises(ValueError):radius.radius_values(0)
    with pytest.raises(ValueError):radius.radius_values(100)


def _synthetic(*,contexts=7,values=(-2,-1,0,1,2),width=12,noise=0.0,seed=3):
    rng=np.random.default_rng(seed)
    d=rng.normal(size=width)
    d/=np.linalg.norm(d)
    offsets=rng.normal(size=(contexts,width))*2
    y=np.asarray(values)
    arr=offsets[:,None,:]+y[None,:,None]*d[None,None,:]
    if noise:arr+=rng.normal(size=arr.shape)*noise
    counts=np.full((contexts,len(y)),40,dtype=int)
    return arr,y,counts


def test_shared_slope_recovers_on_heldout_contexts():
    arr,y,counts=_synthetic()
    result=radius.shared_radius_analysis(arr,y,counts,permutations=128)
    assert result['status']=='COMPLETE'
    assert result['mean_pairwise_slope_cosine']>0.99999
    assert result['heldout_context_r2_vs_zero']>0.99999
    assert result['matched_nonzero_pairs']==7*4
    assert result['permutation_p_one_sided']<0.10


def test_context_specific_directions_not_mistaken_as_shared():
    rng=np.random.default_rng(3)
    contexts=9; width=20;y=np.asarray((-2,-1,0,1,2))
    direction=rng.normal(size=(contexts,width))
    direction/=np.linalg.norm(direction,axis=1)[:,None]
    arr=rng.normal(size=(contexts,1,width))+y[None,:,None]*direction[:,None,:]
    counts=np.full((contexts,len(y)),22,dtype=int)
    result=radius.shared_radius_analysis(arr,y,counts,permutations=12)
    assert result['status']=='COMPLETE'
    assert result['mean_pairwise_slope_cosine']<.5
    assert result['heldout_context_r2_vs_zero']<.9


def test_token_count_mismatches_excluded_not_silently_compared():
    arr,y,counts=_synthetic(contexts=6)
    # Leave exactly two values per context at matching lengths: ±1.
    counts[:,0]=41; counts[:,-1]=41
    result=radius.shared_radius_analysis(arr,y,counts,permutations=0)
    assert result['status']=='COMPLETE'
    assert result['matched_nonzero_pairs']==12
    counts[:,:]=40
    counts[:,2]=42
    result=radius.shared_radius_analysis(arr,y,counts,permutations=0)
    assert result['status']=='INSUFFICIENT_SAME_TOKEN_COUNT_CONTEXTS'


def test_tiny_c0_layer_tracing_is_real_forward_path():
    torch=pytest.importorskip('torch')
    from tools.nanojev_qwen_c0_compressive_train import make_network
    head=make_network(torch,12,(9,7,5,3))
    x=np.linspace(-1,1,12,dtype=np.float32)
    trace=radius.capture_c0(torch,head,x,device='cpu')
    assert trace['c0_input_norm'].shape==(12,)
    assert trace['c0_hidden_1'].shape==(9,)
    assert trace['c0_hidden_2'].shape==(7,)
    assert trace['c0_hidden_3'].shape==(5,)
    assert trace['c0_bottleneck'].shape==(3,)
    assert trace['c0_answer'].shape==(1,)
    with torch.inference_mode():
        a,b=head(torch.tensor(x).reshape(1,-1))
    np.testing.assert_allclose(trace['c0_logit'],a.numpy(),atol=1.e-5)
    np.testing.assert_allclose(trace['c0_aux_logit'],b.numpy(),atol=1.e-5)


def test_qwen_hook_capture_embedding_intermediate_and_final_without_model_download():
    torch=pytest.importorskip('torch')
    from torch import nn
    class FakeTokenizer:
        def __call__(self,prompt,return_tensors,add_special_tokens):
            assert return_tensors=='pt'
            return {'input_ids':torch.tensor([[ord(ch)%32+1 for ch in prompt]],dtype=torch.long)}
    class Body(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed_tokens=nn.Embedding(36,8)
            self.layers=nn.ModuleList([nn.Linear(8,8) for _ in range(4)])
            self.norm=nn.LayerNorm(8)
        def forward(self,ids):
            x=self.embed_tokens(ids)
            for layer in self.layers:x=layer(x)
            return self.norm(x)
    class FakeQwen(nn.Module):
        def __init__(self):
            super().__init__();self.model=Body()
        def get_base_model(self):return self
        def forward(self,input_ids,output_hidden_states,return_dict,logits_to_keep):
            assert logits_to_keep==1
            return {'last_hidden_state':self.model(input_ids)}
    model=FakeQwen().eval()
    trace,meta=radius.capture_qwen(torch,model,FakeTokenizer(),
        'a number +1 and a longer trailing narrative',device='cpu',max_length=128,
        curvature=4.,layer_indices=(0,2))
    assert meta['token_count']>10
    assert trace['embedding_last'].shape==(8,)
    assert trace['embedding_mean100'].shape==(8,)
    assert trace['qwen_layer_01_mean100'].shape==(8,)
    assert trace['qwen_layer_03_last'].shape==(8,)
    assert trace['qwen_final_100'].shape==(800,)
    assert trace['qwen_final_mean100'].shape==(8,)
    np.testing.assert_allclose(trace['qwen_final_last'],trace['qwen_final_100'][-8:])
    # Hooks must be removed or later inference can leak data or duplicate captures.
    assert not model.model.norm._forward_hooks
    assert not model.model.embed_tokens._forward_hooks


def test_cli_radius_is_read_only_and_has_dedicated_parameters():
    from tools.nanojev_gamer_v1_staircase import parser,main
    args=parser().parse_args(['head-100-radius','--run','some-expanded-run',
         '--radius-contexts','9','--radius-width','3','--radius-checkpoint','latest'])
    assert (args.radius_contexts,args.radius_width,args.radius_checkpoint)==(9,3,'latest')
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-radius','--run','some-expanded-run','--steps','4'])


def test_null_cosine_for_unchanged_embedding_is_expected_and_printable():
    # The unedited suffix token gives the embedding_last hook exactly the
    # same vector across all radius interventions. Its zero slope has no
    # defined cosine and must not crash the completed diagnostic.
    arr,y,counts=_synthetic(contexts=5)
    arr[:]=arr[:,2:3,:]
    result=radius.shared_radius_analysis(arr,y,counts,permutations=10)
    assert result['status']=='COMPLETE'
    assert result['mean_pairwise_slope_cosine'] is None
    assert result['nonzero_slope_contexts']==0
    summary=radius._format_radius_stage_summary('embedding_last',result)
    assert 'cosine=N/A' in summary
    assert 'heldout_r2=0.0000' in summary
    assert 'nonzero_slopes=0/5' in summary


def test_formatted_summary_does_not_hide_undefined_heldout_metric():
    summary=radius._format_radius_stage_summary('stage', {
        'status':'COMPLETE', 'contexts_valid':4, 'nonzero_slope_contexts':2,
        'mean_pairwise_slope_cosine':-0.25, 'heldout_context_r2_vs_zero':None,
        'matched_nonzero_pairs':16})
    assert 'cosine=-0.2500' in summary
    assert 'heldout_r2=N/A' in summary
    assert 'nonzero_slopes=2/4' in summary


def test_insufficient_context_summary_remains_explicit():
    summary=radius._format_radius_stage_summary('stage', {
        'status':'INSUFFICIENT_SAME_TOKEN_COUNT_CONTEXTS',
        'matched_nonzero_pairs':4})
    assert 'INSUFFICIENT_SAME_TOKEN_COUNT_CONTEXTS' in summary
    assert 'matched=4' in summary
