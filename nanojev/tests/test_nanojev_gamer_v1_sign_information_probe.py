"""Regression gates for read-only, grouped NanoJev sign-information diagnostics."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_sign_information_probe as probe


def test_oracle_exact_triples_grouped_and_range_disjoint():
    deck=probe.make_deck()
    report=probe.validate_deck(deck)
    assert report['cases']==612
    assert report['groups']==102
    assert report['classes']=={'negative':204,'zero':204,'positive':204}
    assert report['by_split']=={'train':288,'dev':144,'test':180}
    assert report['test_only_family']=='hawk_indifference'
    assert all(x.split=='test' for x in deck if x.family=='hawk_indifference')
    centers={split:{x.group for x in deck if x.split==split} for split in ('train','dev','test')}
    assert all(centers[a].isdisjoint(centers[b]) for a,b in [('train','dev'),('dev','test'),('train','test')])
    assert probe.deck_hash(deck)==probe.deck_hash(probe.make_deck())
    assert probe.deck_hash(deck)!=probe.deck_hash(probe.make_deck(47,24,24))
    assert all(x.target==0 for x in deck if x.id.endswith('.zero.source_native'))


def test_independent_oracle_refuses_corruption_and_missing_counterfactual():
    deck=probe.make_deck(2,2,2,False)
    bad=list(deck)
    bad[0]=replace(bad[0],target=Fraction(999))
    with pytest.raises(ValueError,match='INDEPENDENT_ORACLE_MISMATCH'):
        probe.validate_deck(bad)
    with pytest.raises(ValueError,match='LEAKY_OR_INCOMPLETE_MATHEMATICAL_GROUP'):
        probe.validate_deck(deck[1:])
    bad=list(deck)
    bad[0]=replace(bad[0],split='test')
    with pytest.raises(ValueError,match='LEAKY_OR_INCOMPLETE_MATHEMATICAL_GROUP'):
        probe.validate_deck(bad)
    with pytest.raises(ValueError,match='AT_LEAST_TWO_GROUPS'):
        probe.make_deck(1,2,2)


def test_oracle_only_cli_requires_no_checkpoint_or_gpu(capsys):
    assert probe.main([])==0
    out=json.loads(capsys.readouterr().out)
    assert out['model_status']=='NOT_EVALUATED'
    assert out['deck']['independent_exact_fraction_oracle']
    with pytest.raises(ValueError,match='ANALYZE_MODE_REQUIRES_INPUT'):
        probe.main(['--mode','analyze'])
    with pytest.raises(ValueError,match='CAPTURE_REQUIRES_RUN_AND_EXPANDED_RUN'):
        probe.main(['--mode','capture'])


def test_report_directory_never_overlaps_source_or_expanded(tmp_path):
    source=tmp_path/'source';expanded=tmp_path/'expanded'
    source.mkdir();expanded.mkdir()
    p=probe._safe_report_directory(source,expanded,None)
    assert p.parent==tmp_path and not p.exists()
    assert probe._safe_report_directory(source,expanded,tmp_path/'report')==tmp_path/'report'
    for path in [source,source/'out',expanded/'report',tmp_path]:
        with pytest.raises(ValueError,match='OUTPUT_OVERLAPS_SOURCE'):
            probe._safe_report_directory(source,expanded,path)
    p.mkdir()
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        probe._safe_report_directory(source,expanded,p)


def test_metrics_uses_local_positions_with_global_case_ids():
    rows=[x.public() for x in probe.make_deck(2,2,2,False)]
    ids=np.asarray([i for i,r in enumerate(rows)
                    if r['split']=='test' and r['surface']=='source_native'],dtype=int)
    y=np.asarray([float(Fraction(rows[i]['target'])) for i in ids])
    good=np.sign(y).astype(np.int8)
    m=probe._metrics(y,y,good,rows,ids)
    assert m['cases']==6
    assert m['triple_all_correct']==2
    assert m['triples']==2
    assert m['triple_accuracy']==1
    assert m['balanced_sign_accuracy']==1
    assert m['zero_recall']==1
    assert m['reversal_both_signs_correct']==2
    pred=np.full(len(y),0.1)
    bad=probe._metrics(y,pred,np.ones(len(y),dtype=np.int8),rows,ids)
    assert bad['recall']['-1']==0
    assert bad['zero_recall']==0
    assert bad['triples']==2
    assert bad['triple_all_correct']==0


def test_frozen_feature_linear_readout_recovers_signed_information_out_of_group():
    deck=probe.make_deck(8,4,4,False)
    rows=[x.public() for x in deck]
    # A diagnostic feature that explicitly contains the required sign and zero
    # information must be recoverable on unseen baselines and prompt formats.
    features=[]
    for c in deck:
        s=-1 if c.target<0 else 1 if c.target>0 else 0
        features.append([float(s),float(s==0),float(c.target),1.0])
    arrays={'lora__final_last':np.asarray(features,dtype=np.float32)}
    out=probe._evaluate_policy(rows,arrays,'lora','final_last',seed=17)
    assert out['train_count']==48 and out['dev_count']==24 and out['test_count']==24
    assert out['test']['balanced_sign_accuracy']==1
    assert out['test']['triple_accuracy']==1
    assert out['test']['reversal_both_signs_correct']==8
    assert out['test']['zero_recall']==1
    assert out['test']['balanced_sign_accuracy']>out['shuffled_null_max']
    transfer=probe._evaluate_policy(rows,arrays,'lora','final_last',
                                    train_surface='source_native',seed=17)
    assert transfer['test_by_surface']['spine_difference']['balanced_sign_accuracy']==1
    assert transfer['test']['triple_accuracy']==1


def test_constant_features_cannot_certify_sign_or_zero():
    deck=probe.make_deck(8,4,4,False)
    rows=[x.public() for x in deck]
    arrays={'lora__final_last':np.ones((len(deck),8),dtype=np.float32)}
    out=probe._evaluate_policy(rows,arrays,'lora','final_last',seed=17)
    assert out['test']['balanced_sign_accuracy']<=1/3+1e-8
    assert out['test']['triple_accuracy']==0
    assert not out['passes_95pct_sign_and_triple_gate']


def test_archive_rejects_corrupted_hashes_and_incomplete_profiles(tmp_path):
    deck=probe.make_deck(2,2,2,False)
    entries=[x.public() for x in deck]
    metadata={'schema':probe.SCHEMA,'dataset':entries,
        'dataset_sha256':probe.deck_hash(deck),
        'captures':{'lora':{'available_profiles':['final_flat']}},
        'ablation':['lora'],'lineage':{},
        'array_sha256':{
         'lora__final_flat':probe.hashlib.sha256(np.zeros((len(deck),102400),dtype=np.float16).tobytes()).hexdigest(),
         'production_head_predictions':probe.hashlib.sha256(np.zeros(len(deck),dtype=np.float32).tobytes()).hexdigest()}}
    path=tmp_path/'frozen_features.npz'
    with path.open('wb') as out:
        np.savez(out,metadata=probe.canonical(metadata),
                 lora__final_flat=np.zeros((len(deck),102400),dtype=np.float16),
                 production_head_predictions=np.zeros(len(deck),dtype=np.float32))
    loaded,array=probe._verify_archive(path,require_source=False)
    assert loaded['dataset_sha256']==probe.deck_hash(deck)
    assert array['lora__final_flat'].shape==(len(deck),102400)
    with path.open('wb') as out:
        np.savez(out,metadata=probe.canonical(metadata),
                 lora__final_flat=np.ones((len(deck),102400),dtype=np.float16),
                 production_head_predictions=np.zeros(len(deck),dtype=np.float32))
    with pytest.raises(ValueError,match='ARCHIVE_ARRAY_FINGERPRINT_MISMATCH'):
        probe._verify_archive(path,require_source=False)
    tampered=dict(metadata);tampered['dataset_sha256']='a'*64
    with path.open('wb') as out:
        np.savez(out,metadata=probe.canonical(tampered),
                 lora__final_flat=np.zeros((len(deck),102400),dtype=np.float16),
                 production_head_predictions=np.zeros(len(deck),dtype=np.float32))
    with pytest.raises(ValueError,match='ARCHIVE_DATASET_HASH_MISMATCH'):
        probe._verify_archive(path,require_source=False)


def test_mocked_capture_and_analysis_complete_without_touching_source(tmp_path,monkeypatch):
    """Exercise ACTUAL final-raster capture, head readout and both Qwen variants.

    The mock backbone has 1024 channels and real embedding/intermediate/final
    module hooks, but its activations are deliberately NOT claimed to be NanoJev.
    """
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as smoke
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head,SCHEMA
    torch.set_num_threads(1)
    run=tmp_path/'transition-005'
    adapter=run/'staircase'/'head_cycle_0002'/'lora'/'best_adapter'
    adapter.mkdir(parents=True)
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'known-frozen')
    adapter_bytes={p.name:p.read_bytes() for p in adapter.iterdir()}
    expanded=tmp_path/'expanded';expanded.mkdir()
    metadata={'schema':SCHEMA,'model':'mock-qwen',
        'adapter_sha256':smoke.adapter_identity(adapter),
        'configuration':{'samples':100,'input_width':102400,
           'feature_policy':'100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER',
           'hidden':[8,6,4,2],'max_length':1024,'curvature':4.0}}
    (expanded/'provenance.json').write_text(json.dumps(metadata))
    torch.manual_seed(123)
    head=make_expanded_head(torch,102400,(8,6,4,2))
    torch.save({'schema':SCHEMA,'identity':metadata,'step':500,
                'model':head.state_dict()},expanded/'best_model.pt')
    original_head=(expanded/'best_model.pt').read_bytes()

    class Tokenizer:
        padding_side='right'
        def encode(self,text,add_special_tokens=True):
            return [int(ord(c)%90+1) for c in text]
        def __call__(self,texts,padding=True,truncation=True,max_length=1024,return_tensors='pt'):
            seqs=[self.encode(p) for p in texts]
            length=max(map(len,seqs))
            return {'input_ids':torch.tensor([s+[0]*(length-len(s)) for s in seqs]),
                    'attention_mask':torch.tensor([[1]*len(s)+[0]*(length-len(s)) for s in seqs])}

    class Layer(torch.nn.Module):
        def forward(self,x):return (x*0.99+0.001,)

    class MockQwen(torch.nn.Module):
        def __init__(self,variant):
            super().__init__()
            self.config=SimpleNamespace(hidden_size=1024)
            self.model=torch.nn.Module()
            self.model.embed_tokens=torch.nn.Embedding(128,1024)
            self.model.layers=torch.nn.ModuleList([Layer(),Layer(),Layer()])
            self.model.norm=torch.nn.Identity()
            self.variant=variant
        def forward(self,input_ids,attention_mask,*,output_hidden_states=False,
                    return_dict=True,logits_to_keep=1):
            x=self.model.embed_tokens(input_ids)
            if self.variant=='lora':x=x*1.01
            for layer in self.model.layers:x=layer(x)[0]
            self.model.norm(x)
            return SimpleNamespace()

    def fake_model_components(model_id,device,adapter_dir=None):
        torch.manual_seed(17)
        return torch,Tokenizer(),MockQwen('lora' if adapter_dir else 'base')
    monkeypatch.setattr(gamer,'model_components',fake_model_components)
    out=tmp_path/'sign-probe'
    assert probe.main(['--mode','capture','--run',str(run),
         '--expanded-run',str(expanded),'--model-id','mock-qwen',
         '--device','cpu','--capture-batch','2','--head-batch','4',
         '--train-groups','2','--dev-groups','2','--test-groups','2',
         '--skip-ood','--output',str(out)])==0
    info,arrays=probe._verify_archive(out/probe.CAPTURE_NAME)
    assert info['lineage']['checkpoint_step']==500
    assert info['ablation']==['lora','base']
    assert arrays['lora__final_flat'].shape==(36,102400)
    assert arrays['base__final_flat'].shape==(36,102400)
    assert not np.array_equal(arrays['lora__final_flat'],arrays['base__final_flat'])
    assert info['captures']['lora']['final_capture_parity_max_delta']==0
    assert (expanded/'best_model.pt').read_bytes()==original_head
    assert {p.name:p.read_bytes() for p in adapter.iterdir()}==adapter_bytes
    assert probe.main(['--mode','analyze','--input',str(out),
                       '--skip-full-vector'])==0
    result=json.loads((out/'analysis.json').read_text())
    assert result['status']=='INCOMPLETE_OPTIONAL_ABLATION_OR_FULL_VECTOR'
    assert result['production_head_test']['cases']==12
    assert result['development_selected_readouts']['lora']['test']['cases']==12
    assert result['development_selected_readouts']['base']['test']['cases']==12
    assert (out/'readout_scores.csv').is_file()
    assert (expanded/'best_model.pt').read_bytes()==original_head
    with pytest.raises(FileExistsError,match='ANALYSIS_ALREADY_EXISTS'):
        probe.main(['--mode','analyze','--input',str(out), '--skip-full-vector'])
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        probe.main(['--mode','capture','--run',str(run),
         '--expanded-run',str(expanded),'--model-id','mock-qwen',
         '--device','cpu','--train-groups','2','--dev-groups','2',
         '--test-groups','2','--skip-ood','--output',str(out)])


def test_full_100_vector_kernel_and_strict_zero_gate():
    # Test the high-dimensional linear-kernel path, not just low-width profiles.
    deck=probe.make_deck(6,3,3,False)
    rows=[x.public() for x in deck]
    feature=np.zeros((len(deck),102400),dtype=np.float16)
    for i,c in enumerate(deck):
        sign=-1 if c.target<0 else 1 if c.target>0 else 0
        feature[i,0]=sign
        feature[i,100]=float(c.target==0)
        feature[i,1024]=float(c.target)
    result=probe._evaluate_policy(rows,{'lora__final_flat':feature},
                                  'lora','final_flat')
    assert result['test']['cases']==18
    assert result['test']['balanced_sign_accuracy']==1
    assert result['test']['triple_accuracy']==1
    assert result['test']['zero_within_0_05']==1
    assert result['passes_95pct_sign_and_triple_gate']
    assert result['test_by_surface']['spine_difference']['balanced_sign_accuracy']==1
