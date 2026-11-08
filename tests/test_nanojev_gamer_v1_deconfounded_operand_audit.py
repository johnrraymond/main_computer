"""Frozen GAMER independent-operand and exact-formula diagnostic regressions."""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_deconfounded_operand_audit as audit
from tools import nanojev_gamer_v1_operand_sufficiency_audit as operand
from tools import nanojev_gamer_v1_token_localization_lab as token


@pytest.fixture(scope='module')
def generated():
    deck, tags, ix, profiles, inputs = audit._make_deck()
    return deck, tags, ix, profiles, inputs, operand._targets(deck)


def test_deck_has_complete_role_and_panel_counts(generated):
    deck, tags, ix, profiles, inputs, labels = generated
    assert len(deck) == 600 and len(tags) == 600
    assert {r: len(ix[r]) for r in audit.CORE_GROUPS} == {
        'train':360, 'dev':120, 'heldout':120}
    assert {r:len(ix[r+'_independent']) for r in audit.CORE_GROUPS} == {
        'train':288, 'dev':96, 'heldout':96}
    assert {r:len(ix[r+'_zero_anchor']) for r in audit.CORE_GROUPS} == {
        'train':72, 'dev':24, 'heldout':24}
    assert len(profiles) == 60
    assert len(labels['q']) == len(deck)


def test_deck_is_deterministic_and_role_disjoint(generated):
    deck, tags, ix, profiles, inputs, labels = generated
    again, other_tags, other_ix, *_ = audit._make_deck()
    assert [c.prompt for c in deck] == [c.prompt for c in again]
    assert tags == other_tags
    assert all(np.array_equal(ix[k],other_ix[k]) for k in ix)
    tuples = {}
    for role in audit.CORE_GROUPS:
        tuples[role] = set()
        for i in ix[role]:
            c=deck[int(i)]
            spans=token._spans(c.prompt)
            tup=(Fraction(c.prompt[slice(*spans['belief'][0])]),) + tuple(
                Fraction(c.prompt[slice(*part)]) for part in spans['payoffs'])
            tuples[role].add(tup)
    assert not (tuples['train'] & tuples['dev'])
    assert not (tuples['train'] & tuples['heldout'])
    assert not (tuples['dev'] & tuples['heldout'])


def test_core_inputs_are_low_correlation_and_support_matched(generated):
    _, _, _, _, inputs, _ = generated
    for role, matrix in inputs.items():
        values = np.asarray(matrix)
        assert values.shape == (audit.CORE_GROUPS[role],5)
        assert np.all((values[:,0] >= 3/17) & (values[:,0] <= 13/17))
        assert np.all((values[:,1:] >= 112) & (values[:,1:] <= 135))
        corr = np.corrcoef(values,rowvar=False)
        max_corr = np.max(np.abs(corr[np.triu_indices(5,1)]))
        assert max_corr < .4  # includes small 8-profile DEV/heldout panels
    train_corr=np.corrcoef(np.asarray(inputs['train']),rowvar=False)
    assert np.max(np.abs(train_corr[np.triu_indices(5,1)])) < .16


def test_all_independent_edits_change_exactly_one_input(generated):
    deck,tags,ix,profiles,inputs,labels = generated
    by_id={c.id:i for i,c in enumerate(deck)}
    for i,tag in enumerate(tags):
        if tag['panel']!='independent' or tag['edit']=='base':continue
        j=by_id[tag['base_id']]
        deltas={key:labels[key][i]-labels[key][j]
                for key in audit.SINGLE_OPERANDS}
        assert {k for k,v in deltas.items() if v!=0} == {tag['changed']}
        q=Fraction(deck[i].prompt[slice(*token._spans(deck[i].prompt)['belief'][0])])
        assert Fraction(0)<q<Fraction(1)


def test_exact_zero_panel_is_balanced_in_every_split(generated):
    deck,tags,ix,profiles,inputs,y=generated
    for role in audit.CORE_GROUPS:
        ids=ix[role+'_zero_anchor']
        result=Counter(np.sign(y['signed_margin'][ids]))
        assert result[-1]==result[0]==result[1]
        for i in ids:
            assert tags[int(i)]['panel']=='zero_anchor'
    assert all(isinstance(c.target,Fraction) for c in deck)


def test_exact_oracle_rejects_any_corrupted_margin(generated):
    deck,*_ = generated
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    bad=list(deck)
    c=bad[-1]
    bad[-1]=oracle.Case(c.id,c.split,c.group,c.surface,c.prompt,
                        c.target+Fraction(1,7),c.family,c.offset)
    with pytest.raises(ValueError,match='ORACLE_MISMATCH'):
        operand._targets(bad)


def test_all_five_operand_interventions_are_diagnosed(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    scores=audit._counterfactuals(deck,tags,ix,labels,labels)
    assert set(scores)==set(audit.SINGLE_OPERANDS)
    for item in scores.values():
        assert item['cases']==16
        assert item['operand_delta_mae']<1e-12
        assert item['margin_delta_mae']<1e-12
        assert item['margin_direction_accuracy']==1.
        assert item['mean_spurious_unchanged_operand_delta']==0.


def test_counterfactual_guard_refuses_multiple_edited_inputs(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    altered={key:v.copy() for key,v in labels.items()}
    first=next(int(i) for i in ix['heldout_independent'] if tags[int(i)]['edit']=='q_edit')
    altered['R'][first]+=7.
    with pytest.raises(ValueError,match='NOT_ONE_OPERAND_EDIT'):
        audit._counterfactuals(deck,tags,ix,altered,altered)


def test_counterfactual_detects_spurious_unchanged_operand_drift(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    predicted={key:v.copy() for key,v in labels.items()}
    first=next(int(i) for i in ix['heldout_independent'] if tags[int(i)]['edit']=='q_edit')
    predicted['R'][first]+=7.
    out=audit._counterfactuals(deck,tags,ix,labels,predicted)
    assert out['q']['mean_spurious_unchanged_operand_delta']>0
    assert out['q']['margin_delta_mae']==0.


def test_composition_exact_parser_passes_and_decoded_error_isolated(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    output=audit._composition(labels,labels,ix)
    for panel in ('independent','zero_anchor'):
        for key,value in output[panel].items():
            if key!='always_zero':
                assert value['mae'] < 1e-10, (panel,key,value)
        assert output[panel]['exact_parsed_operands_exact_formula']['mae']<1e-10
    perturbed={key:v.copy() for key,v in labels.items()}
    perturbed['q'][:]+=.12
    error=audit._composition(labels,perturbed,ix)
    assert error['independent']['decoded_q_exact_payoffs']['mae']>0
    assert error['independent']['exact_q_decoded_payoffs']['mae']<1e-10


def test_exact_zero_recalls_and_counterfactual_polarity(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    result=audit._zero_panel(labels,labels,ix)
    assert result['zero_cases']==8 and result['nonzero_cases']==16
    assert result['zero_recall']==result['nonzero_recall']==1.
    assert result['polarity_accuracy_from_signed_margin']==1.


def test_train_only_kernel_prediction_ignores_holdout_features():
    rng=np.random.default_rng(401)
    a=rng.normal(size=(30,6)).astype(np.float32)
    labels={'q':a[:,0]}
    first=audit._ridge_tasks(a,labels,np.arange(20),('q',),'linear',.1)['q']
    changed=a.copy();changed[20:]+=40.
    second=audit._ridge_tasks(changed,labels,np.arange(20),('q',),'linear',.1)['q']
    assert np.allclose(first[:20],second[:20])


def test_numerical_probe_fit_ignores_zero_anchor_labels(generated):
    deck,tags,ix,profiles,inputs,labels=generated
    # Exercise the split contract directly with a small feature subset.
    rng=np.random.default_rng(93)
    features=rng.normal(size=(len(deck),9)).astype(np.float32)
    baseline=audit._ridge_tasks(features,labels,ix['train_independent'],
                                 ('q','R','S','T','P'), 'linear', .1)
    edited={k:v.copy() for k,v in labels.items()}
    edited['q'][ix['train_zero_anchor']]+=12345
    edited['R'][ix['train_zero_anchor']]-=90000
    replay=audit._ridge_tasks(features,edited,ix['train_independent'],
                               ('q','R','S','T','P'), 'linear', .1)
    for k in baseline:
        assert np.array_equal(baseline[k],replay[k])


def test_heldout_labels_do_not_affect_candidate_model_selection(monkeypatch,generated):
    deck,tags,ix,profiles,inputs,labels=generated
    frame=np.linspace(-1,1,len(deck),dtype=np.float32)[:,None]
    frames={'final':{'belief':frame,'lexical_numeric':frame}}
    monkeypatch.setattr(audit,'SELECTABLE',('belief',))
    # Avoid an expensive full kernel fit; select scores from deterministic
    # predicted values depending only on TRAIN features.
    def fake_fit(frame, targets, indices, kind, reg):
        return {k:np.full(len(deck),np.median(targets[k][indices['train_independent']]),
                   dtype=float) for k in audit.DIAGNOSTIC_TASKS}
    monkeypatch.setattr(audit,'_fit_candidate',fake_fit)
    _,p1=audit._fit_stages(frames,labels,ix)
    modified={k:v.copy() for k,v in labels.items()}
    for k in operand.CONTINUOUS:modified[k][ix['heldout']]+=10000.
    _,p2=audit._fit_stages(frames,modified,ix)
    for k in audit.DIAGNOSTIC_TASKS:
        assert np.array_equal(p1['final'][k],p2['final'][k])


def test_source_rejects_incomplete_report_before_cuda(tmp_path):
    folder=tmp_path/'operand'
    folder.mkdir()
    (folder/'final_report.json').write_text(json.dumps(
        {'schema':operand.SCHEMA,'status':'FAILED'}))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE'):
        audit._source(folder)


def test_negative_capture_batch_rejected_before_cuda(tmp_path):
    with pytest.raises(ValueError,match='INVALID_CAPTURE_BATCH'):
        audit.run(argparse.Namespace(operand=tmp_path,output=tmp_path/'target',
                   device='cuda',capture_batch=0))


def test_mock_end_to_end_writes_fingerprinted_report(monkeypatch,tmp_path,generated):
    deck,tags,ix,profiles,inputs,labels=generated
    # Mock only the GPU capture and test-selected probe outputs. The full
    # validation, exact oracle controls, report writing and archive execute.
    operand_source=tmp_path/'source';operand_source.mkdir()
    source_report=operand_source/'final_report.json';source_report.write_text('{}')
    archive=operand_source/'previous.npz';np.savez(archive, a=np.array([1]))
    origin={k:tmp_path/k for k in ('training','source','capture','adapter')}
    origin.update({'ckpt':tmp_path/'ckpt.pt','audit_path':tmp_path/'audit.json'})
    old={'report_path':tmp_path/'old_report.json',
         'source': {'v2_report_path':tmp_path/'v2.json',
                    'original': {'report_path':tmp_path/'v1.json',
                                 'prior':{'joint_dir':tmp_path/'joint',
                                          'lineage':{'report_path':tmp_path/'joint.json'}}}}}
    fake={'path':source_report,'archive':archive,
          'report':{'feature_archive':{'sha256':token._sha(archive)},
                    'zero_head_sha256':'zero'},
          'parent':{'pool_path':tmp_path/'pool.npz','source':old},
          'origin':origin,'snapshot':tmp_path/'snapshot',
          'snapshot_sha':'sha'}
    monkeypatch.setattr(audit,'_source',lambda _:fake)
    monkeypatch.setattr(audit.c0sign,'_output',lambda path, protect:Path(path))
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    monkeypatch.setattr(stress,'_verify_snapshot',lambda path,step:'sha')
    fake['zero_head']=tmp_path/'zero.npz'
    fake['zero_head'].write_bytes(b'zero')
    fake['report']['zero_head_sha256']=token._sha(fake['zero_head'])
    monkeypatch.setattr(audit,'_make_deck',lambda:(deck,tags,ix,profiles,inputs))
    monkeypatch.setattr(audit,'_captured_features',lambda *a,**kw:{
        stage:{'belief':np.zeros((len(deck),3),dtype=np.float32)}
        for stage in audit.STAGES})
    def exact_fits(features,targets,indices):
        return ({stage:{'tasks':{'zero':{'development':{'threshold':.5}}}}
                 for stage in audit.STAGES},
                {stage:{k:v.copy() for k,v in targets.items()}
                 for stage in audit.STAGES})
    monkeypatch.setattr(audit,'_fit_stages',exact_fits)
    out=tmp_path/'result'
    result=audit.run(argparse.Namespace(operand=operand_source,output=out,
                             device='cpu',capture_batch=2))
    report=json.loads((out/'final_report.json').read_text())
    assert report['status']=='COMPLETE_DECONFOUNDED_OPERAND_DIAGNOSTIC_NO_PROMOTION'
    assert report['promotion']=='BLOCKED_DIAGNOSTIC_ONLY'
    assert report['cases']['total']==600
    assert report['stages']['final']['composition']['independent'][
        'exact_parsed_operands_exact_formula']['mae']<1e-12
    assert report['stages']['final']['single_operand_counterfactuals']['T']['cases']==16
    assert report['stages']['final']['zero_panel']['zero_recall']==1.
    assert token._sha(out/'frozen_deconfounded_views.npz') == report['feature_archive']['sha256']
    assert report['no_lora_training'] and report['no_c0_training']
    assert not (out/'production.pt').exists()


def test_source_resolves_real_report_shape_and_guards_fingerprints(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    parent_dir=tmp_path/'prior';parent_dir.mkdir()
    report_path=parent_dir/'final_report.json'
    token_dir=tmp_path/'token';token_dir.mkdir()
    tok_report=token_dir/'final_report.json';tok_report.write_text('attested token report')
    archive=parent_dir/'stage.npz';np.savez(archive,x=np.array([2.]))
    snapshot=tmp_path/'lora.pt';snapshot.write_bytes(b'checkpoint')
    head=tmp_path/'zero.npz';head.write_bytes(b'zero-head')
    snapsha='9d239991dbe208cdb075b0f008f46738b6cdf1ca3cc3a7079d113aff53757dd8'
    content={'schema':operand.SCHEMA,
      'status':'COMPLETE_FROZEN_OPERAND_SUFFICIENCY_NO_PROMOTION',
      'lora_step':3000,'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
      'no_lora_training':True,'no_c0_training':True,
      'token_report_path':str(tok_report),
      'token_report_sha256':token._sha(tok_report),
      'feature_archive':{'path':str(archive),'sha256':token._sha(archive)},
      'frozen_lora_sha256':snapsha,'zero_head_sha256':token._sha(head)}
    report_path.write_text(json.dumps(content))
    upstream={'token_report':{'frozen_zero_head_sha256':token._sha(head)},
      'source':{'origin':{'snapshots':{3000:(snapshot,snapsha)}},
                'source':{'original':{'prior':{'zero_head':head}}}}}
    monkeypatch.setattr(operand,'_source',lambda _:upstream)
    monkeypatch.setattr(stress,'_verify_snapshot',lambda path,step:snapsha)
    result=audit._source(parent_dir)
    assert result['snapshot']==snapshot
    assert result['zero_head']==head
    assert result['snapshot_sha']==snapsha
    content['feature_archive']['sha256']='bad'
    report_path.write_text(json.dumps(content))
    with pytest.raises(ValueError,match='FEATURE_ARCHIVE_CHANGED'):
        audit._source(parent_dir)


def test_capture_hooks_embedding_intermediate_final_without_training(monkeypatch,generated):
    import re, hashlib
    import torch
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_sign_information_probe as oracle

    class ToyTokenizer:
        padding_side='right'
        def _encode(self,prompt):
            words=list(re.finditer(r'\S+',prompt))
            ids=[int.from_bytes(hashlib.sha256(m.group().encode()).digest()[:2],
                                'big')%512 for m in words]
            return ids, [(m.start(),m.end()) for m in words]
        def encode(self,prompt,add_special_tokens=True):
            return self._encode(prompt)[0]
        def __call__(self,prompt,**kwargs):
            if isinstance(prompt,str):
                ids,spans=self._encode(prompt)
                return {'input_ids':ids,'offset_mapping':spans}
            rows=[self._encode(text)[0] for text in prompt]
            pad=max(map(len,rows))
            return {'input_ids':torch.tensor([r+[0]*(pad-len(r)) for r in rows]),
                    'attention_mask':torch.tensor([[1]*len(r)+[0]*(pad-len(r))
                                                  for r in rows])}

    class ToyModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed=torch.nn.Embedding(512,1024)
            self.layers=torch.nn.ModuleList([torch.nn.Identity() for _ in range(10)])
            self.norm=torch.nn.Identity()
        def forward(self,input_ids,attention_mask,**kwargs):
            h=self.embed(input_ids)
            for mod in self.layers:h=mod(h)
            return self.norm(h)

    model=ToyModel().eval()
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(None,ToyTokenizer(),model))
    monkeypatch.setattr(oracle,'_backbone_parts',lambda model:SimpleNamespace(
        embed_tokens=model.embed,layers=model.layers,norm=model.norm))
    deck=generated[0][:4]
    info={'origin':{'meta':{'lineage':{'model_id':'toy','configuration':{
           'max_length':1024,'curvature':4.}}}},'snapshot':'immutable'}
    result=audit._captured_features(info,deck,device='cpu',batch_size=2)
    assert set(result)==set(audit.STAGES)
    for stage in audit.STAGES:
        assert result[stage]['operand_joint'].shape==(4,5120)
        assert result[stage]['belief'].shape==(4,1024)
        assert result[stage]['lexical_numeric'].shape==(4,512)
        assert np.isfinite(result[stage]['operand_joint']).all()
    assert np.allclose(result['embedding']['R'],result['final']['R'])
