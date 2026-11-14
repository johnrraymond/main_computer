"""G1-0014 frozen 320-prompt calibration replication contracts (CPU/mock only)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_calibration_validation as val
from tools import nanojev_gamer_v1_g1_null_boundary as null
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_comparison import _stats
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT
from tests.test_nanojev_gamer_v1_graph_system import CharTokenizer


def test_predeclared_candidates_and_seed_are_locked():
    assert val.CASES == 320
    assert val.SEED != 94117
    assert val.CONFIGURATIONS == (
        ('structured_0', 'structured', 0.0),
        ('structured_2', 'structured', 2.0),
        ('structured_4', 'structured', 4.0),
        ('null_focused_4', 'null_focused', 4.0),
    )


def test_320_new_prompts_are_balanced_reproducible_and_prior_disjoint(tmp_path):
    ds = tmp_path/'orig.jsonl'
    corpus.generate(ds, train=8, dev=8, challenge=4, test=4, seed=31)
    from tools.nanojev_gamer_v1_g1_fresh_dev import fresh_rows
    previous = fresh_rows(ds)
    first = val.validation_rows(ds, previous)
    assert first == val.validation_rows(ds, previous)
    assert len(first) == 320
    assert [r['family'] for r in first].count('encounter') == 160
    assert [r['family'] for r in first].count('allocation') == 160
    assert {r['split'] for r in first} == {'dev'}
    assert len({r['prompt'] for r in first}) == 320
    seen = {r['prompt'] for r in previous}
    seen |= {r['prompt'] for r in cur.load_rows(ds) if r['split'] in ('train','dev')}
    assert not seen.intersection(r['prompt'] for r in first)
    with pytest.raises(ValueError, match='LOCKED_GENERATION'):
        val.validation_rows(ds, previous, seed=val.SEED+1)
    with pytest.raises(ValueError, match='LOCKED_GENERATION'):
        val.validation_rows(ds, previous, cases=80)
    with pytest.raises(ValueError, match='INVALID_PRIOR_FRESH_PROMPTS'):
        val.validation_rows(ds, previous + [previous[0]])


def _base_metrics():
    gold={(1,2,'action'),(3,4,'quantity')}
    return _stats([(gold, gold)])


def _lineage_fixture():
    m = _base_metrics()
    names = ('structured','null_focused')
    info = {
        'selected': names,
        'old_rows': [{}], 'fresh_rows': [{}],
        'dataset_sha': 'dataset-sha', 'adapter_sha': 'adapter-sha',
        'transfer_sha': 'transfer-sha', 'fresh_report_sha': 'fresh-sha',
        'fresh_dataset_sha': 'canonical-sha', 'fresh_dataset_file_sha': 'bytes-sha',
        'entries': {name: {'manifest': {'head_sha256':'head-'+name}}
                    for name in names},
        'transfer': {'arms': {name: {'linear':m} for name in names}},
        'fresh_report': {'fresh_metrics': {name:m for name in names}},
    }
    arm = {'baseline':m, 'shift_sweep': {str(s): {
        'complete':True, 'overflow_cases':0, 'cases':1, 'metrics':m}
        for s in null.NULL_LOGIT_SHIFTS}}
    report = {
        'schema': null.SCHEMA, 'status':'DIAGNOSTIC_ONLY_NOT_CERTIFICATION',
        'arms':list(names), 'splits':{'dev':1,'fresh_dev':1},
        'null_logit_shifts_predeclared':list(null.NULL_LOGIT_SHIFTS),
        'decoder':'length_linear','length_power':1.0,'per_object_penalty':0.0,
        'dataset_sha256':'dataset-sha','source_adapter_sha256':'adapter-sha',
        'transfer_report_sha256':'transfer-sha','fresh_report_sha256':'fresh-sha',
        'fresh_dataset_sha256':'canonical-sha',
        'fresh_dataset_file_sha256':'bytes-sha',
        'head_sha256': {name:'head-'+name for name in names},
        'no_training':True,'heads_frozen':True,'original_decoder_unchanged':True,
        'challenge_or_test_split_used':False,'production_promotion':False,
        'certification_authorized':False,'g2_unlocked':False,
        'metrics':{split:{name:json.loads(json.dumps(arm)) for name in names}
                   for split in ('dev','fresh_dev')},
    }
    return info,report


def test_prior_diagnostic_lineage_and_complete_selection_required():
    info, report = _lineage_fixture()
    val._validate_diagnostic(report, info)
    for change in (
        lambda r: r.update(g2_unlocked=True),
        lambda r: r.update(source_adapter_sha256='wrong'),
        lambda r: r.update(null_logit_shifts_predeclared=[0.0,2.0,4.0]),
        lambda r: r['metrics']['fresh_dev']['structured']['shift_sweep']['2.0'].update(complete=False),
    ):
        candidate = json.loads(json.dumps(report))
        change(candidate)
        with pytest.raises(ValueError, match='GAMER_G1_0014_'):
            val._validate_diagnostic(candidate, info)


def test_paired_320_exact_and_object_errors():
    gold = {(0,2,'action'), (3,4,'actor')}
    a = []; b = []
    for i in range(val.CASES):
        if i == 0:
            p, q = {next(iter(gold))}, set(gold)
        elif i == 1:
            p, q = set(gold), set()
        elif i == 2:
            p, q = set(), {(0,2,'action'),(9,10,'action')}
        else:
            p = q = set(gold)
        a.append((p,gold)); b.append((q,gold))
    result = val._paired(a,b)
    assert result['b_only_exact'] == 1
    assert result['a_only_exact'] == 1
    assert result['exact_discordant_pairs'] == 2
    assert result['mcnemar_exact_two_sided_p'] == 1.0
    assert result['b_new_false_positives'] == 1
    assert result['b_previously_correct_gold_lost'] == 2
    with pytest.raises(ValueError, match='GOLD_DRIFT'):
        damaged = list(b);damaged[0]=(damaged[0][0],set())
        val._paired(a, damaged)


class FrozenAllNullHead:
    def __init__(self):self.calls=0
    def eval(self):return self
    def __call__(self, hidden, *, eligible, active_stage):
        assert not torch.is_grad_enabled() and active_stage=='G1'
        self.calls+=1
        count=hidden.shape[0]
        logits=torch.full((count,1,SPAN_CLASS_COUNT),-30.)
        logits[:,:,0]=20.
        return {'span_logits':logits,'span_valid':eligible.bool().reshape(-1,1)}


def _mock_run_inputs(tmp_path, monkeypatch):
    ds=tmp_path/'original.jsonl'
    corpus.generate(ds,train=4,dev=4,challenge=2,test=2,seed=31)
    from tools.nanojev_gamer_v1_g1_fresh_dev import fresh_rows
    previous = fresh_rows(ds)
    adapter=tmp_path/'adapter'; adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'fake-adapter')
    adapter_sha=backbone.adapter_fingerprint(adapter)
    entries={}
    heads={}
    for name in ('structured','null_focused'):
        path=tmp_path/name;path.mkdir()
        (path/'manifest.json').write_text(json.dumps({'stage':'G1','head_sha256':'head-'+name}))
        (path/cur.HEAD_FILE).write_bytes(b'frozen-head-'+name.encode())
        entries[name]={'checkpoint':path, 'manifest': {
            'stage':'G1','head_sha256':'head-'+name,'model_id':'mock-Qwen',
            'source_adapter':str(adapter),'source_adapter_sha256':adapter_sha}}
        heads[name]=FrozenAllNullHead()
    transfer=tmp_path/'transfer_report.json';transfer.write_text('{}')
    prior=tmp_path/'prior';prior.mkdir()
    (prior/'fresh_report.json').write_text('{}')
    (prior/'fresh_dev.jsonl').write_bytes(val._jsonl_bytes(previous).replace(b'\n',b'\r\n'))
    (prior/'per_case.jsonl').write_text('{}\n')
    null_run=tmp_path/'null';null_run.mkdir()
    (null_run/'null_boundary_report.json').write_text('{}')
    info={'dataset':ds,'dataset_sha':backbone.sha256(ds),'adapter_sha':adapter_sha,
          'entries':entries,'selected':('structured','null_focused'),
          'old_rows':[{}], 'fresh_rows':previous,
          'transfer_sha':backbone.sha256(transfer),
          'fresh_report_sha':backbone.sha256(prior/'fresh_report.json'),
          'fresh_dataset_sha':hashlib.sha256(val._jsonl_bytes(previous)).hexdigest(),
          'fresh_dataset_file_sha':backbone.sha256(prior/'fresh_dev.jsonl'),
          'fresh_case_sha':backbone.sha256(prior/'per_case.jsonl'),
          'null_report_sha':backbone.sha256(null_run/'null_boundary_report.json'),
          'null_report_path':null_run/'null_boundary_report.json'}
    monkeypatch.setattr(val,'_load_inputs',lambda *args:dict(info))
    monkeypatch.setattr(cur,'_load_checkpoint',lambda path,*,device:
        (heads[Path(path).name], entries[Path(path).name]['manifest']))
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(), CharTokenizer()))
    captured=[]
    def capture(model,tok,row,device,max_length):
        assert row['split']=='dev'
        captured.append(row['prompt'])
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        return {'prepared':prepared,'hidden':torch.zeros((len(prepared['eligible']),8))}
    monkeypatch.setattr(cur,'capture',capture)
    return transfer,prior,null_run,entries,heads,captured


def test_320_prompt_mocked_run_writes_immutable_readonly_reports(tmp_path,monkeypatch):
    transfer, prior, null_run, entries, heads, captured=_mock_run_inputs(tmp_path,monkeypatch)
    out=tmp_path/'output'
    report=val.run(root=tmp_path,transfer_report=transfer,fresh_run=prior,
                   null_run=null_run,output=out,device='cpu')
    assert len(captured)==320 and len(set(captured))==320
    assert all(h.calls==320 for h in heads.values())
    assert report['families']=={'encounter':160,'allocation':160}
    assert report['same_prompts_for_all_configurations'] is True
    assert report['ranking_rule_predeclared'].startswith('exact_prompts_desc')
    assert all(m['cases']==320 for m in report['metrics'].values())
    assert len(report['paired_comparisons'])==6
    assert all(m['correct']==0 for m in report['metrics'].values())
    assert report['development_leader_preserved']=='structured'
    assert report['g2_unlocked'] is False and report['no_training'] is True
    assert report['challenge_or_test_split_used'] is False
    assert report['production_promotion'] is False
    assert report['certification_authorized'] is False
    assert len((out/'validation_dev.jsonl').read_text().splitlines())==320
    assert len((out/'validation_per_case.jsonl').read_text().splitlines())==320
    assert json.loads((out/'validation_report.json').read_text())==report
    assert (out/'validation_dev.jsonl').read_bytes().count(b'\r\n')==0
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        val.run(root=tmp_path,transfer_report=transfer,fresh_run=prior,
                null_run=null_run,output=out,device='cpu')


def test_real_byte_changes_in_null_report_or_checkpoint_rejected(tmp_path,monkeypatch):
    transfer, prior, null_run, entries, _, _ = _mock_run_inputs(tmp_path,monkeypatch)
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    checkpoint_files={name:{
        'manifest_sha256':backbone.sha256(entry['checkpoint']/'manifest.json'),
        'head_file_sha256':backbone.sha256(entry['checkpoint']/cur.HEAD_FILE)}
        for name,entry in entries.items()}
    info=val._load_inputs(tmp_path,transfer,prior,null_run,tmp_path/'out')
    info['checkpoint_files']=checkpoint_files
    source=entries['structured']['manifest']['source_adapter']
    val._verify_inputs_unchanged(info,transfer,prior,source,entries)
    (null_run/'null_boundary_report.json').write_bytes(b'{"changed":1}')
    with pytest.raises(ValueError, match='NULL_REPORT_CHANGED'):
        val._verify_inputs_unchanged(info,transfer,prior,source,entries)
    (null_run/'null_boundary_report.json').write_text('{}')
    (entries['structured']['checkpoint']/cur.HEAD_FILE).write_bytes(b'changed')
    with pytest.raises(ValueError, match='CHECKPOINT_CHANGED'):
        val._verify_inputs_unchanged(info,transfer,prior,source,entries)


def test_cli_locks_validation_without_promoting_or_training(capsys):
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-calibration-validation','--help'])
    assert exc.value.code == 0
    body=capsys.readouterr().out
    for flag in ('--root','--transfer-report','--fresh-run','--null-run','--output','--device'):
        assert flag in body
    for forbidden in ('--train','--certify','--promote','--challenge','--g2','--seed','--cases','--shifts'):
        assert forbidden not in body
