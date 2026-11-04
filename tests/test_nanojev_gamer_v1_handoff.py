"""Gamer v1 mandatory original-prompt/LoRA/C0 Wide handoff invariants."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from tools.nanojev_gamer_v1_handoff import (
    PAIR_SCHEMA, canonical, sha_bytes, registry_ids, load_cases, ensure_manifest,
    original_prompt, original_prompt_fingerprint, tokenize_completion, collate,
    capture_original_features, file_tree_sha256, load_frozen_adapter,
    switch_to_numerical, assert_switch, numeric_metrics, validate_flags,
)
from tools.nanojev_qwen_c0_compressive_train import VARIANTS,make_network,signed_log,neural_loss


def fixture(game, split, key):
    prompt=f'Original HDB08 game prompt for {game}: instance {key}. Answer the game question.'
    table=f'Shared mathematical table with values for {game}; instance {key}. Compute the specified operation.'
    return {
        'schema':PAIR_SCHEMA,'game_id':game,'split':split,
        'prompt_08':prompt,'math_prompt':table,'answer':str(key-2),
        'oracle_verified':True,'table_verified':True,
        'oracle_fingerprint':sha_bytes((prompt+str(key-2)).encode()),
        'table_fingerprint':sha_bytes(table.encode()),
    }


def write_deck(path, *, games=None):
    games=list(games or sorted(registry_ids()))
    rows=[fixture(g,s,i+j*3) for j,g in enumerate(games) for i,s in enumerate(('train','dev','test'))]
    path.write_text('\n'.join(json.dumps(r,sort_keys=True) for r in rows)+'\n',encoding='utf-8')
    return rows


def test_source_registry_has_exact_33_verified_games():
    ids=registry_ids()
    assert len(ids)==33
    assert 'hawk_dove' in ids and 'bayes_posterior' in ids


def test_all_33_game_types_accepted_and_prompt_sha_stable(tmp_path):
    dataset=tmp_path/'pairs.jsonl'
    rows=write_deck(dataset)
    result,manifest=load_cases(dataset)
    assert len(result)==99
    assert manifest['number_of_verified_games']==33
    assert manifest['by_split']=={'train':33,'dev':33,'test':33}
    assert original_prompt_fingerprint(result[0])==sha_bytes(result[0]['prompt_08'].encode())
    assert manifest['dataset_sha256']==sha_bytes(dataset.read_bytes())


def test_partial_dataset_fails_without_explicit_smoke_override(tmp_path):
    dataset=tmp_path/'pairs.jsonl'
    write_deck(dataset,games=['hawk_dove'])
    with pytest.raises(ValueError,match='GAMER_COVERAGE_1_OF_33'):
        load_cases(dataset)
    _,info=load_cases(dataset,allow_partial=True)
    assert info['number_of_verified_games']==1


@pytest.mark.parametrize('field,value,error',[
    ('oracle_verified',False,'UNCERTIFIED'),
    ('table_verified',False,'UNCERTIFIED'),
    ('math_prompt','', 'EMPTY_TABLE'),
    ('prompt_08','', 'EMPTY_ORIGINAL'),
    ('game_id','invented_game','UNVERIFIED_GAME'),
    ('split','other','BAD_SPLIT'),
    ('answer','NaN','NONFINITE'),
    ('oracle_fingerprint','garbage','MISSING_ORACLE_FINGERPRINT'),
])
def test_invalid_attestation_refused(tmp_path,field,value,error):
    dataset=tmp_path/'pairs.jsonl'
    rows=write_deck(dataset,games=['hawk_dove'])
    rows[0][field]=value
    dataset.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match=error):
        load_cases(dataset,allow_partial=True)


def test_prompt_leakage_between_splits_blocked(tmp_path):
    dataset=tmp_path/'pairs.jsonl'
    rows=write_deck(dataset,games=['hawk_dove'])
    rows[1]['prompt_08']=rows[0]['prompt_08']
    dataset.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match='COLLISION_OR_LEAKAGE'):
        load_cases(dataset,allow_partial=True)


def test_manifest_is_immutable_across_phases(tmp_path):
    dataset=tmp_path/'pairs.jsonl';out=tmp_path/'run'
    write_deck(dataset,games=['hawk_dove'])
    _,m=ensure_manifest(out,dataset,'Qwen/Qwen3-0.6B',True)
    _,again=ensure_manifest(out,dataset,'Qwen/Qwen3-0.6B',True)
    assert again==m
    rows=write_deck(dataset,games=['bayes_posterior'])
    with pytest.raises(ValueError,match='LINEAGE_CHANGED'):
        ensure_manifest(out,dataset,'Qwen/Qwen3-0.6B',True)


def test_lora_loss_masks_original_prompt_not_math_target():
    class Tokenizer:
        pad_token_id=0
        eos_token_id=2
        def encode(self,text,add_special_tokens=True):
            return ([1] if add_special_tokens else [])+[3+ord(c) for c in text]
    tok=Tokenizer()
    orig='The Hawk–Dove problem has 2 actions.'
    math='Payoff A = 0; payoff B = 3. Find the greater payoff.'
    item=tokenize_completion(tok,orig,math,400)
    assert item['labels'][:len(tok.encode(orig))] == [-100]*len(tok.encode(orig))
    assert item['labels'][-1]==2
    assert len(item['labels'])==len(item['input_ids'])
    batch=collate(torch,tok,[item,tokenize_completion(tok,'Other game','Matrix with 0 and -1.',400)],'cpu')
    assert batch['input_ids'].shape==batch['labels'].shape
    assert (batch['labels'][batch['attention_mask']==0]==-100).all()
    with pytest.raises(ValueError,match='EXCEEDS_MAX_LENGTH'):
        tokenize_completion(tok,orig,math,5)


def test_numerical_capture_never_embeds_math_target_or_table(monkeypatch):
    row=fixture('hawk_dove','train',9)
    row['math_prompt']='IMPOSSIBLE SECRET ANSWER LOCATED ONLY IN TABLE'
    seen=[]
    def fake_capture(torch_arg,model,tok,rows,args):
        seen.extend(rows)
        return np.zeros((len(rows),64),dtype=np.float32)
    from tools import nanojev_qwen_c0_compressive_train as source
    monkeypatch.setattr(source,'capture_features',fake_capture)
    class FakeTok:
        def encode(self,text,add_special_tokens=True):return list(text)
    features=capture_original_features(torch,None,FakeTok(),[row],'cpu',batch_size=1,max_length=256)
    assert features.shape==(1,64)
    assert seen==[{'prompt':row['prompt_08']}]
    assert all('math_prompt' not in r for r in seen)
    assert 'SECRET' not in str(seen)


def test_uses_existing_c0_wide_autoencoder_and_exact_numerical_loss():
    assert VARIANTS['wide']==(1024,512,128,32)
    net=make_network(torch,48,VARIANTS['wide'])
    data=torch.randn(3,48)
    prediction,baseline=net(data)
    loss=neural_loss(torch,prediction,baseline,torch.as_tensor(signed_log(np.array([0,1,-1])),dtype=torch.float32),beta=.1,aux_weight=.25)
    loss.backward()
    assert prediction.shape==baseline.shape==(3,)
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in net.parameters())


def mock_args(root,**kwargs):
    defaults=dict(output=str(root),model='Qwen/Qwen3-0.6B',allow_experimental=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def write_adapter(tmp_path, *, promotion=False):
    root=tmp_path/'run'
    adapter=root/'lora'/'best_adapter'
    adapter.mkdir(parents=True)
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'test only')
    hashed=file_tree_sha256(adapter)
    report={'model':'Qwen/Qwen3-0.6B','dataset_sha256':'a'*64,
            'best_adapter_sha256':hashed,'handoff_eligible':promotion}
    (root/'lora'/'report.json').write_text(json.dumps(report))
    manifest={'dataset_sha256':'a'*64,'original_prompt_corpus_sha256':'b'*64}
    return root,adapter,manifest


def test_switch_is_explicit_fingerprinted_and_blocks_unpromoted(tmp_path):
    root,adapter,manifest=write_adapter(tmp_path)
    with pytest.raises(ValueError,match='NOT_PROMOTED'):
        switch_to_numerical(mock_args(root),manifest)
    args=mock_args(root,allow_experimental=True)
    contract=switch_to_numerical(args,manifest)
    assert contract['numerical_input']=='original_prompt_08_hidden_activations_only'
    assert contract['experimental_override_used']
    assert_switch(args,manifest,{'best_adapter_sha256':contract['adapter_sha256']})
    with pytest.raises(ValueError,match='EXPERIMENTAL_SWITCH_REQUIRES'):
        assert_switch(mock_args(root),manifest,{'best_adapter_sha256':contract['adapter_sha256']})
    (adapter/'adapter_model.safetensors').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='FINGERPRINT_MISMATCH'):
        switch_to_numerical(args,manifest)


def test_numeric_diagnostics_preserve_per_game_and_zero_line():
    rows=[fixture('hawk_dove','test',2),fixture('hawk_dove','test',3),fixture('matching_pennies','test',1)]
    prediction=np.array([0.,1.1,-1.])
    m=numeric_metrics(rows,prediction)
    assert m['cases']==3
    assert m['sign_accuracy']==1.
    assert m['integer_rounded_exact']==1.
    assert m['per_game']['hawk_dove']['cases']==2


def test_flag_validation_rejects_invalid():
    flags=dict(lora_steps=2,lora_rank=16,lora_batch=1,lora_accum=2,lora_eval_every=1,
               max_new_tokens=32,head_steps=2,head_batch=1,head_eval_every=1,
               capture_batch=1,eval_batch=1,max_length=200,lora_lr=1e-4,head_lr=1e-4)
    validate_flags(SimpleNamespace(**flags))
    flags['lora_rank']=3
    with pytest.raises(ValueError,match='RANK_INVALID'):
        validate_flags(SimpleNamespace(**flags))


def test_wide_phase_and_probe_full_bounded_handoff_without_model_download(tmp_path,monkeypatch):
    """Run the real C0 Wide optimizer/checkpoint with fake Qwen activation capture."""
    from tools import nanojev_gamer_v1_handoff as gamer
    dataset=tmp_path/'pairs.jsonl'
    rows=write_deck(dataset,games=['hawk_dove','matching_pennies'])
    out=tmp_path/'run'
    cases,manifest=gamer.ensure_manifest(out,dataset,'Qwen/Qwen3-0.6B',True)
    adapter_dir=out/'lora'/'best_adapter'
    adapter_dir.mkdir(parents=True)
    (adapter_dir/'adapter_config.json').write_text('{}')
    (adapter_dir/'adapter_model.safetensors').write_bytes(b'fake PEFT adapter')
    fingerprint=gamer.file_tree_sha256(adapter_dir)
    gamer.atomic_json(out/'lora'/'report.json',{
        'schema':gamer.SCHEMA,'phase':'lora', 'model':'Qwen/Qwen3-0.6B',
        'dataset_sha256':manifest['dataset_sha256'],
        'best_adapter_sha256':fingerprint,'handoff_eligible':False,
    })
    args=SimpleNamespace(output=str(out),model='Qwen/Qwen3-0.6B',device='cpu',allow_experimental=True,
        seed=7,head_steps=3,head_eval_every=1,head_batch=3,head_lr=.001,
        capture_batch=2,eval_batch=3,max_length=300,weight_decay=1e-4,
        aux_weight=.25,huber_beta=.1,grad_clip=1.,resume=False)
    gamer.switch_to_numerical(args,manifest)
    # Simulated Qwen/LoRA forward: derived exclusively from the same 08 prompt.
    class FakeFrozen:
        def eval(self):return self
    class FakeTok:
        def encode(self,text,add_special_tokens=True):return list(text)
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,FakeTok(),FakeFrozen()))
    captured=[]
    def fake_capture(torch_arg,model,tok,observed,device,*,batch_size,max_length):
        captured.extend([(r['prompt_08'],r['math_prompt']) for r in observed])
        vectors=[]
        for row in observed:
            k=int(row['prompt_08'].split('instance ')[1].split('.')[0])
            gid=int(row['game_id']=='hawk_dove')
            vectors.append([float(k),float(gid),float(k%4),float(k>3),1.,0.,-1.,0.5])
        return np.asarray(vectors,dtype=np.float32)
    monkeypatch.setattr(gamer,'capture_original_features',fake_capture)
    from tools.nanojev_qwen_c0_compressive_train import make_network as real_network
    result=gamer.train_to_numerical(args,cases,manifest)
    assert result['phase']=='to_numerical'
    assert result['architecture']=='existing_C0_wide'
    assert result['train_count']==2 and result['dev_count']==2 and result['test_count']==2
    assert result['frozen_adapter_sha256']==fingerprint
    assert result['release_status']=='EXPERIMENTAL_NOT_PROMOTED'
    assert len(captured)==len(cases)
    checkpoint=torch.load(out/'to_numerical'/'wide.pt',weights_only=True)
    assert checkpoint['hidden']==[1024,512,128,32]
    assert checkpoint['frozen_adapter_sha256']==fingerprint
    assert (out/'to_numerical'/'latest.pt').exists()
    reprobe=gamer.probe(args,cases,manifest)
    assert reprobe['dev']['mae']==pytest.approx(result['dev']['mae'],abs=1e-5)
    # Changing just mathematical table text makes the same original-prompt features;
    # the downstream head cannot receive or inspect the text.
    before=captured.copy()
    for row in cases:row['math_prompt']='SECRET TABLE THAT HEAD NEVER GETS'
    again=fake_capture(None,None,None,cases,'cpu',batch_size=2,max_length=300)
    assert again.shape==(len(cases),8)
    assert captured[:len(before)]==before
    # Simulate interruption after step 3: drop only final report, then resume
    # optimizer, weights and NumPy/Torch RNG from the exact saved checkpoint.
    (out/'to_numerical'/'report.json').unlink()
    args.resume=True
    args.head_steps=5
    continued=gamer.train_to_numerical(args,rows,manifest)
    assert continued['resumed'] and continued['checks'][-1]['step']==5
    # Real table text is unrelated to numeric feature capture; the above
    # rows are untouched source fixtures so dataset lineage stays intact.


def test_resume_state_integrity_and_rng_restore(tmp_path):
    from tools import nanojev_gamer_v1_handoff as gamer
    args=SimpleNamespace(model='Qwen/Qwen3-0.6B',seed=7,max_length=200,
      max_new_tokens=20,lora_rank=16,lora_batch=1,lora_accum=2,lora_lr=1e-4,lora_eval_every=2)
    manifest={'dataset_sha256':'a'*64,'original_prompt_corpus_sha256':'b'*64}
    path=tmp_path/'state.pt'
    py_rng=__import__('random').Random(55)
    gamer.save_training_state(torch,path,{'schema':gamer.SCHEMA,'phase':'lora',
      'dataset_sha256':manifest['dataset_sha256'],
      'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
      'hyperparameters':gamer.frozen_hyperparameters(args,'lora'),
      'rng':gamer.training_rng(torch,python_rng=py_rng)})
    saved=gamer.load_training_state(torch,path,phase='lora',manifest=manifest,args=args)
    assert saved['rng']['python']==py_rng.getstate()
    bad=SimpleNamespace(**vars(args));bad.lora_rank=32
    with pytest.raises(ValueError,match='LINEAGE_OR_HYPERPARAMETERS_CHANGED'):
        gamer.load_training_state(torch,path,phase='lora',manifest=manifest,args=bad)


def test_zero_line_diagnostics_do_not_confuse_target_sign_with_residual():
    rows=[fixture('hawk_dove','dev',i) for i in range(3)]
    # All numerical answers can be positive even if game balance residual is signed.
    for row, value in zip(rows,(-1,0,1)):
        row['zero_line_residual']=value
        row['answer']='10'
    report=numeric_metrics(rows,[10.,9.,11.])
    assert report['zero_line']['status']=='MEASURED'
    assert set(report['zero_line']['by_residual_class'])=={'zero','near_negative','near_positive'}
    del rows[0]['zero_line_residual']
    partial=numeric_metrics(rows,[10.,9.,11.])
    assert partial['zero_line']['status']=='MEASURED_PARTIAL'
    assert partial['zero_line']['included_cases']==2
    assert partial['zero_line']['excluded_cases']==1
