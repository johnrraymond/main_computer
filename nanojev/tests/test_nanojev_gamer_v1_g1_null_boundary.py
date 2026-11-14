"""G1 null-boundary probe: frozen inference, paired diagnostics, no gate use."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import pytest
import torch

from tools import nanojev_gamer_v1_g1_null_boundary as null
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_comparison import _stats
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT
from tests.test_nanojev_gamer_v1_graph_system import CharTokenizer


def test_predeclared_null_shifts_restore_candidates_without_oracle():
    logits=torch.full((3,1,SPAN_CLASS_COUNT),-10.)
    logits[:,:,0]=0.
    action=SPAN_KINDS.index('action')+1
    logits[0,0,action]=-.75
    logits[2,0,action]=-1.25
    valid=torch.tensor([[True],[False],[True]])
    assert null._decode_shift(logits,valid,0.)==[]
    assert null._decode_shift(logits,valid,1.) == [
        {'kind':'action','token_first':0,'token_last':0}]
    assert null._decode_shift(logits,valid,2.) == [
        {'kind':'action','token_first':0,'token_last':0},
        {'kind':'action','token_first':2,'token_last':2}]
    assert torch.all(logits[:,:,0] == 0), 'frozen tensor must not be modified'


def test_excluded_gold_is_attributed_to_selected_interval_conflict(monkeypatch):
    kind='action'; label=SPAN_KINDS.index(kind)+1
    # The shorter wrong interval has a greater total weight than the gold span.
    logits=torch.full((3,3,SPAN_CLASS_COUNT),-20.)
    logits[:,:,0]=0.
    logits[0,0,label]=5.0
    logits[0,1,label]=1.0
    valid=torch.zeros((3,3),dtype=torch.bool)
    valid[0,0]=True;valid[0,1]=True
    prediction={'span_logits':logits,'span_valid':valid}
    prepared={'eligible':torch.tensor([1,1,1]),'offsets':[(0,1),(1,2),(2,3)],'prompt_start':0,'prompt':'abc'}
    row={'prompt':'abc','family':'encounter',
         'object_spans':[{'start':0,'end':2,'kind':kind}]}
    monkeypatch.setattr(cur,'aligned_labels',lambda prep,row: {'spans':[
        {'token_first':0,'token_last':1,'kind':kind}]})
    spans=null._decode_shift(logits,valid,0.)
    pred={(*null.token_span_char_bounds(s,prepared),s['kind']) for s in spans}
    misses,evidence,kinds=null._explain_gold(prediction,prepared,row,spans,pred,
                                           split='dev',case_index=1,arm='structured')
    assert len(misses)==1
    missed=misses[0]
    assert missed['reason']=='linear_decoder_excluded'
    assert missed['conflicts_only_false_positives'] is True
    assert missed['selected_conflicts']==[{'kind':kind,'correct_gold':False}]
    assert missed['best_nonnull_is_correct_kind'] is True
    assert kinds['action']==1
    assert evidence['best_nonnull_kind_correct']==1


def test_paired_recovery_and_false_positives_are_distinct():
    gold={(0,2,'action'),(4,6,'actor')}
    base={(0,2,'action'),(10,11,'action')}
    trial={(4,6,'actor'),(12,14,'actor')}
    paired=null._paired_metrics([(trial,gold)],[(base,gold)])
    assert paired == {'newly_recovered_gold':1,'previously_correct_gold_lost':1,
                      'new_false_positives':1,'old_false_positives_removed':1}
    with pytest.raises(ValueError,match='GOLD_DRIFT'):
        null._paired_metrics([(trial,gold)],[(base,set())])


def test_zero_shift_and_incomplete_sweep_handling():
    gold={(0,1,'actor')}
    case={'arms':{'structured':{
        'baseline':(set(),gold),'misses':[{'reason':'null_wins','kind':'actor',
            'best_nonnull_is_correct_kind':True}],
        'evidence':Counter({'best_nonnull_kind_correct':1}),
        'gold_counts':Counter({'actor':1}),
        'sweeps':{v: ((gold,gold) if v == 0.5 else (set(),gold) if v == 0. else None)
                  for v in null.NULL_LOGIT_SHIFTS}
    }}}
    result=null._summarize_split([case],'structured')
    assert result['baseline']['missing_objects']==1
    assert result['null_miss_best_nonnull_kind_correct']==1
    assert result['shift_sweep']['0.5']['paired_change']['newly_recovered_gold']==1
    assert result['shift_sweep']['1.0']['complete'] is False
    assert result['shift_sweep']['1.0']['cases']==0
    assert result['shift_sweep']['1.0']['overflow_cases']==1
    assert result['shift_sweep']['0.0']['complete'] is True


class FrozenNullHead:
    def __init__(self): self.calls=0
    def eval(self): return self
    def __call__(self, hidden, *, eligible, active_stage):
        assert active_stage=='G1' and not torch.is_grad_enabled()
        self.calls += 1
        n=hidden.shape[0]
        logits=torch.full((n,24,SPAN_CLASS_COUNT),-20.)
        logits[:,:,0]=10.
        valid=torch.zeros((n,24),dtype=torch.bool)
        for first in range(n):
            for offset in range(min(24,n-first)):
                valid[first,offset]=bool(eligible[first:first+offset+1].all())
        return {'span_logits':logits,'span_valid':valid}


def test_full_readonly_run_reproduces_both_splits_with_mocked_heads(tmp_path,monkeypatch):
    ds=tmp_path/'graph-span-curriculum-0005.jsonl'
    corpus.generate(ds,train=4,dev=4,challenge=2,test=2,seed=11)
    dev=[r for r in cur.load_rows(ds) if r['split']=='dev']
    fresh=[corpus.encounter(random.Random(8),'dev',1),
           corpus.allocation(random.Random(9),'dev',2)]
    source=tmp_path/'adapter'
    source.mkdir()
    (source/'adapter_config.json').write_text('{}')
    (source/'adapter_model.safetensors').write_bytes(b'fake-adapter')
    adapter_sha=backbone.adapter_fingerprint(source)
    entries={name:{'checkpoint':tmp_path/name,'manifest':{'stage':'G1','head_sha256':'head_'+name,
               'model_id':'mock-Qwen','source_adapter':str(source),
               'source_adapter_sha256':adapter_sha}} for name in ('structured','null_focused')}
    gold_dev=[(set(),{(s['start'],s['end'],s['kind']) for s in r['object_spans']}) for r in dev]
    gold_fresh=[(set(),{(s['start'],s['end'],s['kind']) for s in r['object_spans']}) for r in fresh]
    baseline_dev=_stats(gold_dev)
    baseline_fresh=_stats(gold_fresh)
    paths=tmp_path/'fresh';paths.mkdir()
    (paths/'fresh_report.json').write_text('{}')
    # Reproduce Windows Path.write_text() behavior: canonical LF content
    # is persisted as CRLF bytes, without any semantic change to JSONL.
    canonical_fresh = ''.join(json.dumps(r)+'\n' for r in fresh)
    (paths/'fresh_dev.jsonl').write_bytes(canonical_fresh.replace('\n', '\r\n').encode('utf-8'))
    (paths/'per_case.jsonl').write_text('{}\n{}\n')
    transfer=tmp_path/'transfer_report.json';transfer.write_text('{}')
    info={'dataset':ds,'dataset_sha':backbone.sha256(ds),'adapter_sha':adapter_sha,
          'entries':entries, 'selected':('structured','null_focused'),
          'transfer':{'arms':{n:{'linear':baseline_dev} for n in entries}},
          'transfer_sha':backbone.sha256(transfer),
          'fresh_report':{'fresh_metrics':{n:baseline_fresh for n in entries}},
          'fresh_report_sha':backbone.sha256(paths/'fresh_report.json'),
          'fresh_dataset_sha':hashlib.sha256(canonical_fresh.encode('utf-8')).hexdigest(),
          'fresh_dataset_file_sha':backbone.sha256(paths/'fresh_dev.jsonl'),
          'fresh_case_sha':backbone.sha256(paths/'per_case.jsonl'),
          'old_rows':dev,'fresh_rows':fresh}
    monkeypatch.setattr(null,'_load_reference',lambda *args:info)
    heads={name:FrozenNullHead() for name in entries}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda path,*,device:(heads[path.name],entries[path.name]['manifest']))
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),CharTokenizer()))
    captures=[]
    def capture(model,tok,row,device,max_length):
        captures.append(row['prompt'])
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        return {'prepared':prepared,'hidden':torch.zeros((len(prepared['eligible']),8))}
    monkeypatch.setattr(cur,'capture',capture)
    out=tmp_path/'output'
    report=null.run(root=tmp_path,transfer_report=transfer,fresh_run=paths,output=out,
                    device='cpu')
    assert len(captures)==len(dev)+len(fresh)
    assert heads['structured'].calls==len(captures)
    assert heads['null_focused'].calls==len(captures)
    assert report['metrics']['dev']['structured']['baseline']['correct']==0
    assert report['metrics']['fresh_dev']['structured']['baseline']['correct']==0
    assert report['metrics']['fresh_dev']['structured']['shift_sweep']['8.0']['complete']
    assert report['fresh_dataset_sha256'] == hashlib.sha256(canonical_fresh.encode()).hexdigest()
    assert report['fresh_dataset_file_sha256'] == backbone.sha256(paths/'fresh_dev.jsonl')
    assert report['fresh_dataset_sha256'] != report['fresh_dataset_file_sha256']
    assert report['g2_unlocked'] is False
    assert report['no_training'] is True
    assert report['challenge_or_test_split_used'] is False
    assert report['certification_authorized'] is False
    assert json.loads((out/'null_boundary_report.json').read_text()) == report
    assert len((out/'null_boundary_misses.jsonl').read_text().splitlines()) == 2*(
            baseline_dev['expected']+baseline_fresh['expected'])
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        null.run(root=tmp_path,transfer_report=transfer,fresh_run=paths,output=out,device='cpu')


def test_cli_exposes_only_diagnostic_readonly_parameters(capsys):
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-null-boundary','--help'])
    assert exc.value.code==0
    out=capsys.readouterr().out
    for parameter in ('--root','--transfer-report','--fresh-run','--output','--device'):
        assert parameter in out
    for forbidden in ('--train','--certify','--challenge','--promote','--g2'):
        assert forbidden not in out


def test_reference_checks_regenerated_fresh_prompts_and_lineage(tmp_path, monkeypatch):
    from tests.test_nanojev_gamer_v1_g1_fresh_dev import _inputs
    from tools import nanojev_gamer_v1_g1_fresh_dev as fresh
    dataset, entries, transfer_path = _inputs(tmp_path, monkeypatch)
    # The compact fixture has four original development rows, not eighty.
    transfer=json.loads(transfer_path.read_text())
    transfer['cases']=4
    for name, exact in (('structured',4),('null_focused',3),('control',2),('g1_0007',1)):
        transfer['arms'][name]['linear']['cases']=4
        transfer['arms'][name]['linear']['exact_prompts']=exact
    transfer_path.write_text(json.dumps(transfer))
    monkeypatch.setattr(null, '_preflight', lambda root, output: (
        dataset, backbone.sha256(dataset),
        entries['structured']['manifest']['source_adapter_sha256'], entries))
    rows = fresh.fresh_rows(dataset)
    run_dir = tmp_path/'fresh';run_dir.mkdir()
    corpus_text = ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False)+'\n' for row in rows)
    (run_dir/'fresh_dev.jsonl').write_bytes(corpus_text.replace('\n','\r\n').encode('utf-8'))
    records=[]
    for i,row in enumerate(rows,1):
        records.append({'index':i, 'family':row['family'],
                        'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                        'gold_count':len(row['object_spans'])})
    (run_dir/'per_case.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    report={
        'schema':fresh.SCHEMA,
        'status':'FRESH_WITHIN_GENERATOR_DEVELOPMENT_ONLY_NOT_CERTIFICATION',
        'original_dataset_sha256':backbone.sha256(dataset),
        'source_adapter_sha256':entries['structured']['manifest']['source_adapter_sha256'],
        'transfer_report_sha256':backbone.sha256(transfer_path),
        'fresh_dataset_sha256':hashlib.sha256(corpus_text.encode()).hexdigest(),
        'seed':fresh.SEED,'cases':fresh.CASES,
        'selected_before_fresh_capture':['structured','null_focused'],
        'head_sha256':{n:entries[n]['manifest']['head_sha256'] for n in ('structured','null_focused')},
        'decoder':fresh.DECODER,'length_power':1.0,'per_object_penalty':0.0,
        'families':{'encounter':40,'allocation':40},'no_training':True,'heads_frozen':True,
        'decoder_frozen':True,'challenge_or_test_split_used':False,
        'production_promotion':False,'g2_unlocked':False,
    }
    path = run_dir/'fresh_report.json';path.write_text(json.dumps(report))
    inputs=null._load_reference(tmp_path,transfer_path,run_dir,tmp_path/'output')
    assert inputs['selected']==('structured','null_focused')
    assert inputs['fresh_dataset_sha'] == hashlib.sha256(corpus_text.encode()).hexdigest()
    assert inputs['fresh_dataset_file_sha'] == backbone.sha256(run_dir/'fresh_dev.jsonl')
    assert inputs['fresh_dataset_file_sha'] != inputs['fresh_dataset_sha']
    assert len(inputs['fresh_rows'])==80
    # Tampering with archived labels and updating only the report's digest
    # cannot defeat regeneration from the original generator and seed.
    altered=json.loads(json.dumps(rows))
    altered[0]['object_spans'][0]['kind']='actor' if altered[0]['object_spans'][0]['kind']!='actor' else 'action'
    tampered=''.join(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n' for row in altered)
    (run_dir/'fresh_dev.jsonl').write_text(tampered)
    report['fresh_dataset_sha256']=hashlib.sha256(tampered.encode()).hexdigest()
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='GENERATION_DIVERGED'):
        null._load_reference(tmp_path,transfer_path,run_dir,tmp_path/'output')


def test_rejects_baseline_drift_in_frozen_metrics():
    with pytest.raises(ValueError,match='BASELINE_DIVERGED_correct'):
        null._verify_metrics({'correct':2},{'correct':3},'FRESH')


def test_running_input_guard_rejects_real_byte_changes_even_with_crlf(tmp_path, monkeypatch):
    adapter = tmp_path/'adapter'
    adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'fake-adapter')
    fingerprint = backbone.adapter_fingerprint(adapter)
    transfer = tmp_path/'transfer_report.json'
    transfer.write_text('\n{}\n', encoding='utf-8')
    dataset = tmp_path/'dataset.jsonl'
    dataset.write_text('\n{}\n', encoding='utf-8')
    fresh_run = tmp_path/'fresh'
    fresh_run.mkdir()
    fresh_dataset = fresh_run/'fresh_dev.jsonl'
    fresh_dataset.write_bytes(b'{"prompt":"example"}\r\n')
    (fresh_run/'fresh_report.json').write_text('\n{}\n', encoding='utf-8')
    (fresh_run/'per_case.jsonl').write_text('\n{}\n', encoding='utf-8')

    info = {'transfer_sha':backbone.sha256(transfer),
            'fresh_report_sha':backbone.sha256(fresh_run/'fresh_report.json'),
            'fresh_dataset_file_sha':backbone.sha256(fresh_dataset),
            'fresh_dataset_sha':hashlib.sha256(b'{"prompt":"example"}\n').hexdigest(),
            'fresh_case_sha':backbone.sha256(fresh_run/'per_case.jsonl'),
            'dataset':dataset,'dataset_sha':backbone.sha256(dataset),
            'adapter_sha':fingerprint}
    assert info['fresh_dataset_sha'] != info['fresh_dataset_file_sha']
    null._verify_unchanged_inputs(info, transfer, fresh_run, adapter)
    # A real byte mutation, even a newline-only one, must still be rejected.
    fresh_dataset.write_bytes(b'{"prompt":"example"}\n')
    with pytest.raises(ValueError, match='GAMER_G1_NULL_INPUT_CHANGED_DURING_RUN'):
        null._verify_unchanged_inputs(info, transfer, fresh_run, adapter)
    fresh_dataset.write_bytes(b'{"prompt":"changed"}\r\n')
    with pytest.raises(ValueError, match='GAMER_G1_NULL_INPUT_CHANGED_DURING_RUN'):
        null._verify_unchanged_inputs(info, transfer, fresh_run, adapter)
