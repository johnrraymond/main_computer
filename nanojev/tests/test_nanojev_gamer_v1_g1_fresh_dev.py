"""G1 fresh dev replication: no sealed examples, no training and no G2 unlock."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g1_fresh_dev as fresh
from tools.nanojev_gamer_v1_g1_decoder_transfer import SCHEMA as TRANSFER_SCHEMA
from tests.test_nanojev_gamer_v1_graph_system import CharTokenizer
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT


def _inputs(tmp_path, monkeypatch):
    dataset = tmp_path/'graph-span-curriculum-0005.jsonl'
    corpus.generate(dataset, train=4, dev=4, challenge=4, test=4, seed=19)
    source = tmp_path/'adapter'
    source.mkdir()
    (source/'adapter_config.json').write_text('{}')
    (source/'adapter_model.safetensors').write_bytes(b'not-used-by-fake-model')
    adapter_sha = backbone.adapter_fingerprint(source)
    entries = {}
    for name in ('g1_0007', 'control', 'structured', 'null_focused'):
        cp = tmp_path/name
        cp.mkdir()
        manifest = {'head_sha256': 'head_'+name, 'stage': 'G1',
                    'source_adapter': str(source), 'source_adapter_sha256': adapter_sha,
                    'model_id': 'frozen-mock-Qwen'}
        entries[name] = {'checkpoint':cp, 'manifest':manifest}
    monkeypatch.setattr(fresh, '_preflight', lambda root, output: (
        dataset, backbone.sha256(dataset), adapter_sha, entries))
    arms = {}
    for name, exact, f1 in (('g1_0007',13,.93),('control',14,.94),
                            ('structured',22,.9669),('null_focused',19,.96)):
        arms[name] = {'head_sha256':entries[name]['manifest']['head_sha256'],
                      'linear':{'cases':80,'exact_prompts':exact,'f1':f1,
                                'recall':f1}}
    report = {'schema':TRANSFER_SCHEMA,'dataset_sha256':backbone.sha256(dataset),
              'source_adapter_sha256':adapter_sha, 'decoder':'length_linear',
              'length_power':1.0,'per_object_penalty':0.0,
              'same_decoder_for_all_checkpoints':True,
              'same_development_split_for_all_checkpoints':True,
              'challenge_or_test_split_used':False,
              'production_promotion':False,'g2_unlocked':False,
              'no_training':True,'frozen_neural_scores':True,
              'status':'DEVELOPMENT_FROZEN_DECODER_TRANSFER_ONLY_NOT_CERTIFICATION',
              'cases':80,'arms':arms}
    report_path = tmp_path/'transfer_report.json'
    report_path.write_text(json.dumps(report))
    return dataset, entries, report_path


def test_fresh_generation_seeded_disjoint_and_family_balanced(tmp_path):
    dataset = tmp_path/'original.jsonl'
    corpus.generate(dataset, train=8, dev=8, challenge=4, test=4, seed=47)
    rows = fresh.fresh_rows(dataset, cases=80)
    assert rows == fresh.fresh_rows(dataset, cases=80)
    assert rows != fresh.fresh_rows(dataset, seed=fresh.SEED+1, cases=80)
    assert len(rows)==80
    assert {r['split'] for r in rows}=={'dev'}
    assert [r['family'] for r in rows].count('encounter')==40
    assert [r['family'] for r in rows].count('allocation')==40
    old = {r['prompt'] for r in cur.load_rows(dataset) if r['split'] in ('train','dev')}
    assert not old.intersection(r['prompt'] for r in rows)
    assert len(set(r['prompt'] for r in rows))==80
    with pytest.raises(ValueError,match='INVALID_GENERATION'):
        fresh.fresh_rows(dataset,cases=3)


def test_previous_transfer_ranking_is_pinned_before_fresh_examples(tmp_path, monkeypatch):
    dataset, entries, path = _inputs(tmp_path, monkeypatch)
    report = json.loads(path.read_text())
    names, ranking = fresh._select_arms(report, entries, backbone.sha256(dataset),
                                        report['source_adapter_sha256'])
    assert names==('structured','null_focused')
    assert ranking==('structured','null_focused','control','g1_0007')
    report['arms']['control']['linear']['exact_prompts']=20
    chosen, _ = fresh._select_arms(report, entries, backbone.sha256(dataset),
                                   report['source_adapter_sha256'])
    assert chosen==('structured','control'), 'runner-up must come from recorded metrics'
    report['arms']['control']['linear']['exact_prompts']=14
    report['arms']['control']['linear']['exact_prompts']=25
    with pytest.raises(ValueError,match='STRUCTURED_NOT_RECORDED_LEADER'):
        fresh._select_arms(report, entries, backbone.sha256(dataset),
                           report['source_adapter_sha256'])
    report['arms']['control']['linear']['exact_prompts']=14
    report['g2_unlocked']=True
    with pytest.raises(ValueError,match='UNTRUSTED_TRANSFER_REPORT'):
        fresh._select_arms(report, entries, backbone.sha256(dataset),
                           report['source_adapter_sha256'])


class NullHead:
    def __init__(self):
        self.calls=0
    def eval(self):
        return self
    def __call__(self, hidden, *, eligible, active_stage):
        assert active_stage=='G1'
        assert not torch.is_grad_enabled()
        self.calls+=1
        count=hidden.shape[0]
        logits=torch.zeros((count,1,SPAN_CLASS_COUNT))
        logits[:,:,0]=10
        return {'span_logits':logits,'span_valid':eligible.bool().reshape(-1,1)}


def test_fresh_run_two_frozen_heads_one_capture_and_readonly(tmp_path, monkeypatch):
    _dataset, entries, transfer_path = _inputs(tmp_path, monkeypatch)
    heads={k:NullHead() for k in ('structured','null_focused')}
    def load_checkpoint(path, *, device):
        name=Path(path).name
        assert name in heads, 'only preselected heads should be opened'
        return heads[name],entries[name]['manifest']
    monkeypatch.setattr(cur,'_load_checkpoint',load_checkpoint)
    tokenizer=CharTokenizer()
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),tokenizer))
    captures=[]
    def capture(model,tok,row,device,max_length):
        assert row['split']=='dev'
        captures.append(row['prompt'])
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        return {'prepared':prepared, 'hidden':torch.zeros((len(prepared['eligible']),8))}
    monkeypatch.setattr(cur,'capture',capture)
    out=tmp_path/'fresh'
    result=fresh.run(root=tmp_path,transfer_report=transfer_path,
                     output=out,device='cpu',cases=4)
    assert result['selected_before_fresh_capture']==['structured','null_focused']
    assert len(captures)==4
    assert all(h.calls==4 for h in heads.values())
    assert result['families']=={'encounter':2,'allocation':2}
    assert result['source_adapter_sha256']==entries['structured']['manifest']['source_adapter_sha256']
    assert result['fresh_metrics']['structured']['missing_objects']>0
    assert result['fresh_metrics']['structured']['miss_reasons']['null_wins'] > 0
    assert sum(result['fresh_metrics']['structured']['miss_reasons'].values()) == result['fresh_metrics']['structured']['missing_objects']
    assert result['fresh_metrics']['structured']['false_positive_objects']==0
    assert result['leader_outperforms_runner_up_on_fresh_dev'] is False
    assert result['production_promotion'] is False
    assert result['g2_unlocked'] is False
    assert result['challenge_or_test_split_used'] is False
    assert result['no_training'] is True
    assert result['shared_prompt_captures'] is True
    assert len((out/'per_case.jsonl').read_text().splitlines())==4
    assert len((out/'fresh_dev.jsonl').read_text().splitlines())==4
    assert json.loads((out/'fresh_report.json').read_text())==result
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        fresh.run(root=tmp_path,transfer_report=transfer_path,
                  output=out,device='cpu',cases=4)


def test_fresh_run_rejects_modified_transfer_before_model_capture(tmp_path,monkeypatch):
    _dataset, _entries, path=_inputs(tmp_path, monkeypatch)
    value=json.loads(path.read_text())
    value['arms']['structured']['head_sha256']='substituted'
    path.write_text(json.dumps(value))
    def should_not_load(*args,**kwargs):
        raise AssertionError('G1 weights should not be loaded')
    monkeypatch.setattr(cur,'_load_checkpoint',should_not_load)
    with pytest.raises(ValueError,match='TRANSFER_METRIC_LINEAGE'):
        fresh.run(root=tmp_path,transfer_report=path,output=tmp_path/'not_created',device='cpu',cases=4)
    assert not (tmp_path/'not_created').exists()


def test_fresh_dev_cli_is_not_stage_gate(capsys):
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-fresh-dev','--help'])
    assert exc.value.code==0
    help_text=capsys.readouterr().out
    for required in ('--root','--transfer-report','--output','--device'):
        assert required in help_text
    for forbidden in ('--train','--certify','--challenge','--promote','--g2'):
        assert forbidden not in help_text
