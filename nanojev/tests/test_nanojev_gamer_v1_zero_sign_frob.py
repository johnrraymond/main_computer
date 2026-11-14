"""Regression gates for read-only zero-line/sign-reversal Gamer frob."""
from __future__ import annotations

import csv
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import nanojev_gamer_v1_zero_sign_frob as frob


def test_independent_oracle_and_mathematically_matched_surfaces():
    probes=frob.build_controlled()
    summary=frob.validate_controlled(probes)
    assert summary['controlled_cases']==46
    assert summary['matched_surface_pairs']==23
    assert summary['zero_targets']==6
    assert summary['positive_targets']==20
    assert summary['negative_targets']==20
    assert len({p.prompt for p in probes})==46
    assert sum(p.target==0 and p.surface=='source_native' for p in probes)==2
    groups={}
    for p in probes:groups.setdefault(p.group,[]).append(p)
    assert all(len(v)==2 and v[0].target==v[1].target for v in groups.values())
    assert frob.independent_oracle(groups['signed_offset.center_10.delta_-1/100'][0])==Fraction(-1,100)
    assert frob.independent_oracle(groups['hawk_indifference.delta_0'][0])==0


def test_oracle_detects_bad_target_and_visible_prompt_corruption():
    rows=frob.build_controlled()
    rows[0]=replace(rows[0],target=rows[0].target+1)
    with pytest.raises(AssertionError,match='VISIBLE_PROMPT_TARGET_MISMATCH'):
        frob.validate_controlled(rows)
    rows=frob.build_controlled()
    rows[0]=replace(rows[0],prompt='the number is missing')
    with pytest.raises(ValueError,match='INVALID_SOURCE_NATIVE_PROMPT'):
        frob.validate_controlled(rows)


def test_sign_reversal_geometry_and_head_nonresponse_cannot_be_overclaimed():
    rows=frob.build_controlled()
    # Features differ, but the mock head produces a positive constant for all.
    feats=np.tile(np.arange(1,9,dtype=np.float32),(len(rows),1))
    feats[:,0]+=np.arange(len(rows))*0.05
    predictions=[0.1 for _ in rows]
    result=frob._pair_analysis(rows,feats,predictions)
    assert result['summary']['surface_pairs']==23
    assert result['summary']['sign_reversal_pairs']==20
    assert result['summary']['both_signs_correct']==0
    assert result['summary']['proper_order']==0
    assert 'cannot' in result['interpretation']
    assert all(p['target_difference']>0 for p in result['sign_reversal_pairs'])
    assert all(p['prediction_difference']==0 for p in result['sign_reversal_pairs'])


def test_source_controls_are_sign_balanced_and_outlier_is_prioritized():
    from tools import nanojev_gamer_v1_handoff as gamer
    rows=[]
    for i in range(90):
        v=[0,-(i+1),i+1][i%3]
        rows.append({'answer':str(v),'game_id':'captain_signed_offset',
                     'prompt_08':f'Frozen source example {i} with signed number {v}.'})
    # An outlier from a real report should be selected by SHA and target, not by
    # the story's case-index alone. This monkeypatch isolates the selector.
    orig=gamer.original_prompt_fingerprint
    try:
        gamer.original_prompt_fingerprint=lambda row: (
            frob.OUTLIER_SHA256 if row['prompt_08'].startswith('Frozen source example 3 ')
            else frob._sha_text(row['prompt_08']))
        rows[3]['game_id']='discounted_repeat'
        selected,report=frob.select_source_controls(rows,18)
        assert report['outlier']=='INCLUDED'
        assert report['outlier_dev_index']==3
        assert len(selected)==18
        signs={int(np.sign(float(p.target))) for p in selected}
        assert signs=={-1,0,1}
        assert any(abs(p.target)>40 for p in selected)
        assert any(p.target<0 and abs(p.target)<10 for p in selected)
        assert any(p.target>0 and abs(p.target)<10 for p in selected)
    finally:
        gamer.original_prompt_fingerprint=orig
    assert frob.select_source_controls(rows,0)[0]==[]
    with pytest.raises(ValueError,match='INVALID_SOURCE_CONTROL_LIMIT'):
        frob.select_source_controls(rows,-1)


def test_development_csv_reference_is_strictly_checkpoint_step_bound(tmp_path):
    controlled=frob.build_controlled()
    probe=replace(controlled[0],id='certified.dev.00001',kind='certified_source',
                  source_index=1)
    root=tmp_path/'expanded';(root/'dev_evaluations').mkdir(parents=True)
    file=root/'dev_evaluations'/'step_0000700.csv'
    with file.open('w',encoding='utf-8',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=('step','case_index','prompt_sha256','target','prediction'))
        writer.writeheader();writer.writerow({'step':700,'case_index':1,
             'prompt_sha256':frob._sha_text(probe.prompt),
             'target':float(probe.target),'prediction':0.75})
    reference=frob._check_original_evaluation(tmp_path,root,700,[probe],None)
    assert reference['status']=='CHECKPOINT_MATCHED'
    assert reference['validated_reference_predictions'][probe.id]==0.75
    mismatch=frob._check_original_evaluation(tmp_path,root,900,[probe],file)
    assert mismatch['status']=='REFERENCE_STEP_DIFFERS_FROM_BEST_HEAD'
    assert 'validated_reference_predictions' not in mismatch
    with pytest.raises(ValueError,match='SOURCE_REFERENCE_PROMPT_OR_TARGET_MISMATCH'):
        frob._check_original_evaluation(tmp_path,root,700,
                 [replace(probe,prompt=probe.prompt+' changed')],None)
    missing=frob._check_original_evaluation(tmp_path,root,900,[probe],None)
    assert missing['status']=='NO_CHECKPOINT_MATCHED_REFERENCE'


def test_no_implicit_source_directory_mutation_or_report_overwrite(tmp_path):
    run=tmp_path/'source';run.mkdir();(run/'weights.bin').write_bytes(b'original')
    out=frob._output_directory(run,None)
    assert out.parent==run.parent and out!=run
    assert not out.exists()
    frob._write_report(out,{'test':True},[],{'summary':{}})
    assert (out/'summary.json').exists()
    assert (run/'weights.bin').read_bytes()==b'original'
    with pytest.raises(FileExistsError):
        frob._write_report(out,{},[],{})
    with pytest.raises(ValueError,match='FROB_OUTPUT_OVERLAPS_SOURCE_RUN'):
        frob._output_directory(run,run/'output')


def test_stats_never_counts_approximate_zero_as_exact_sign():
    r=[{'target':0.0,'prediction':0.01},
       {'target':-0.05,'prediction':0.02},
       {'target':0.05,'prediction':0.04}]
    out=frob._stats(r)
    assert out['zero']['within_0_05']==1
    assert out['zero']['correct_strict_sign']==0
    assert out['negative']['correct_strict_sign']==0
    assert out['positive']['correct_strict_sign']==1
    assert out['count']==3


def test_oracle_cli_never_needs_gpu_or_model(capsys):
    assert frob.main([])==0
    data=json.loads(capsys.readouterr().out)
    assert data['model_status']=='NOT_EVALUATED'
    assert data['oracle']['independently_recomputed']
    with pytest.raises(ValueError,match='INVALID_FROB_LIMITS'):
        frob.main(['--source-cases','-5'])


def test_expanded_cpu_fake_weights_integrated_read_only(tmp_path,monkeypatch):
    """Test the ACTUAL 100-vector capture/head path using small mock weights.

    This does not certify user checkpoints or use a hidden oracle for inference.
    """
    torch=pytest.importorskip('torch')
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head,SCHEMA
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as smoke
    from tools import nanojev_gamer_v1_zero_sign_frob as tested
    torch.set_num_threads(1)
    run=tmp_path/'run';(run/'staircase'/'head_cycle_0002'/'lora'/'best_adapter').mkdir(parents=True)
    adapter=run/'staircase'/'head_cycle_0002'/'lora'/'best_adapter'
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'frozen')
    original_files={f.name:f.read_bytes() for f in adapter.iterdir()}
    metadata={'schema':SCHEMA,'model':'fake-qwen','adapter_sha256':smoke.adapter_identity(adapter),
       'configuration':{'samples':100,'input_width':102400,
           'feature_policy':'100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER',
           'hidden':[8,6,4,2],'max_length':1024,'curvature':4.0}}
    expanded=tmp_path/'expanded';expanded.mkdir()
    (expanded/'provenance.json').write_text(json.dumps(metadata))
    head=make_expanded_head(torch,102400,(8,6,4,2))
    torch.save({'schema':SCHEMA,'identity':metadata,'step':700,'model':head.state_dict()},
               expanded/'best_model.pt')
    orig_checkpoint=(expanded/'best_model.pt').read_bytes()
    # Controlled source rows provide the already-certified original prompts.
    anchors=[{'prompt_08':p.prompt,'game_id':'captain_signed_offset',
              'answer':str(float(p.target))} for p in tested.build_controlled()[:6]]
    monkeypatch.setattr(tested,'_manifest_verified_dev',lambda run,metadata:anchors)
    class Tok:
        padding_side='right'
        def encode(self,text,add_special_tokens=True):
            # Bounded and deterministic, 1024 hidden dimensions in model.
            return [(ord(ch)%83)+1 for ch in text]
        def __call__(self,texts,padding=True,truncation=True,max_length=1024,return_tensors='pt'):
            seqs=[self.encode(p) for p in texts]
            n=max(len(s) for s in seqs)
            return {'input_ids':torch.tensor([s+[0]*(n-len(s)) for s in seqs]),
                    'attention_mask':torch.tensor([[1]*len(s)+[0]*(n-len(s)) for s in seqs])}
    class MockQwen(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.config=SimpleNamespace(hidden_size=1024)
            self.model=torch.nn.Module();self.model.norm=torch.nn.Identity()
        def forward(self,input_ids,attention_mask,*,output_hidden_states=False,
                    return_dict=True,logits_to_keep=0):
            raw=input_ids.float().unsqueeze(-1)
            dims=torch.arange(1024).float().view(1,1,-1)
            self.model.norm(raw/(dims+2))
            return SimpleNamespace()
    monkeypatch.setattr(gamer,'model_components',lambda model_id,device,adapter_dir=None:
                       (torch,Tok(),MockQwen()))
    out=tmp_path/'frob-result'
    assert tested.main(['--mode','expanded','--run',str(run),
        '--expanded-run',str(expanded),'--device','cpu','--model-id','fake-qwen',
        '--output',str(out),'--source-cases','6','--capture-batch','2',
        '--export-features'])==0
    report=json.loads((out/'summary.json').read_text())
    assert report['checkpoint_step']==700
    assert report['source_controls']['count']==6
    assert report['controlled']['count']==46
    assert report['pairs']['surface_pairs']==23
    assert report['pairs']['sign_reversal_pairs']==20
    assert report['original_eval_comparison']['status']=='NO_CHECKPOINT_MATCHED_REFERENCE'
    assert report['no_training']
    assert (expanded/'best_model.pt').read_bytes()==orig_checkpoint
    with np.load(out/'frob_features.npz',allow_pickle=False) as exported:
        assert exported['features'].shape==(52,102400)
        provenance=json.loads(str(exported['metadata'].item()))
        assert provenance['checkpoint_step']==700
        assert provenance['checkpoint_sha256']==smoke.file_sha(expanded/'best_model.pt')
        assert provenance['adapter_sha256']==smoke.adapter_identity(adapter)
    assert {f.name:f.read_bytes() for f in adapter.iterdir()}==original_files
    assert len(list(csv.DictReader((out/'predictions.csv').open())))==52
    # A second invocation must refuse the already-created destination.
    with pytest.raises(FileExistsError):
        tested.main(['--mode','expanded','--run',str(run),
            '--expanded-run',str(expanded),'--device','cpu','--model-id','fake-qwen',
            '--output',str(out),'--source-cases','6','--capture-batch','2'])
