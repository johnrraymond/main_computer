"""Intel HDB BF16 source reconstruction: guard remains mandatory and fail-closed."""
from types import SimpleNamespace
from pathlib import Path
import json
import torch
import pytest

from tools import nanojev_qwen_intel_dml as intel
from tools import nanojev_hybridlink_prompt_learning_probe as probe
from tools import nanojev_hybridlink_micro_reduction as micro


def test_intel_bf16_context_restores_state_even_on_exception():
    assert intel.provenance_cpu_precision('cpu') is None
    assert intel.provenance_cpu_precision('cuda') is None
    with pytest.raises(ValueError):
        with intel.historical_bf16_cpu_source():
            assert intel.provenance_cpu_precision('cpu') == 'bfloat16'
            assert intel.provenance_cpu_precision('cuda') is None
            raise ValueError('expected')
    assert intel.provenance_cpu_precision('cpu') is None


def test_intel_source_provenance_uses_bf16_not_fp32(monkeypatch, tmp_path):
    # Dummy frozen tensor recreates the source dtype hash difference precisely.
    from tools import nanojev_qwen_zero_line_backbone as zero
    a=torch.nn.Linear(3,4,bias=False)
    a.requires_grad_(False)
    a=a.to(dtype=torch.bfloat16)
    bf16=zero.frozen_backbone_sha256(a)
    fp32=zero.frozen_backbone_sha256(a.float())
    assert bf16!=fp32
    assert a.weight.dtype==torch.float32
    # Intel precision override only exists within its intentional scope.
    with intel.historical_bf16_cpu_source():
        assert intel.provenance_cpu_precision('cpu')=='bfloat16'
    assert intel.provenance_cpu_precision('cpu') is None


def test_micro_hash_failure_does_not_restore_lora_or_head(monkeypatch, tmp_path):
    class DummyHead:
        def load_state_dict(self, *a,**k):
            raise AssertionError('must not restore mismatched checkpoint')
    class DummyModel:
        head=DummyHead()
        backbone=object()
    def load(args, trained=None):
        return DummyModel(), None, {'fileA':'new','extra':'sha'}, 'actual-frozen', []
    monkeypatch.setattr(micro.base,'_load',load)
    ck={'schema':micro.SCHEMA,'status':'MICRO_TEXT',
        'source_sha256':{'fileA':'original','missing':'sha'},
        'frozen_backbone_sha256':'saved-frozen'}
    cp=tmp_path/'best.pt'
    torch.save(ck,cp)
    with pytest.raises(RuntimeError,match='MICRO_SOURCE_HASH_DRIFT:') as e:
        micro._load(SimpleNamespace(),checkpoint=cp)
    payload=json.loads(str(e.value).split('MICRO_SOURCE_HASH_DRIFT: ',1)[1])
    assert payload['source_file_hashes_match'] is False
    assert payload['frozen_backbone_hash_matches'] is False
    assert payload['missing_source_paths']==['missing']
    assert payload['new_source_paths']==['extra']
    assert payload['modified_source_paths']==['fileA']


def test_micro_frozen_dtype_mismatch_still_fails(monkeypatch,tmp_path):
    class DummyHead:
        def load_state_dict(self,*a,**kw):
            raise AssertionError('checkpoint load must stop first')
    class DummyModel:
        head=DummyHead()
        backbone=object()
    monkeypatch.setattr(micro.base,'_load',lambda *a,**kw:(DummyModel(),None,{'a':'sha'},'cpu-fp32',[]))
    ck={'schema':micro.SCHEMA,'status':'MICRO_TEXT',
        'source_sha256':{'a':'sha'},'frozen_backbone_sha256':'historical-bf16'}
    p=tmp_path/'best.pt';torch.save(ck,p)
    with pytest.raises(RuntimeError,match='MICRO_SOURCE_HASH_DRIFT:') as e:
        micro._load(SimpleNamespace(),checkpoint=p)
    status=json.loads(str(e.value).split(': ',1)[1]);assert status['source_file_hashes_match']
    assert not status['frozen_backbone_hash_matches']


def test_hdb_robust_metrics_and_persistence_unchanged():
    import inspect
    from tools import nanojev_qwen_joint_frontier as joint
    src=inspect.getsource(joint)
    for anchor in ('focus_baseline_balance_error','hdb_approximate_balance',
                   'balance_error_trend','compare_balance_distributions',
                   'HDB_BALANCE_METRICS_REPORT_VERSION',
                   '_assert_hdb_rescore_metrics_contract',
                   '_commit_hdb_rescore_report'):
        assert anchor in src, anchor
    assert "with intel_dml.historical_bf16_cpu_source():" in src
