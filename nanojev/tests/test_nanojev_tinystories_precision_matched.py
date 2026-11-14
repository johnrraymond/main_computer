from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[1]/'tools'/'nanojev_tinystories_precision_matched.py'
spec=importlib.util.spec_from_file_location('matched_tinystories',MODULE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def setup(tmp_path):
    src=tmp_path/'parent';src.mkdir()
    (src/'best.pt').write_bytes(b'parent')
    (src/'validation-cache.pt').write_bytes(b'cache')
    trainer=tmp_path/m.TRAINER;trainer.write_text('')
    return src,trainer

def test_both_arms_share_parent_and_differ_only_weight_and_output(tmp_path):
    src,trainer=setup(tmp_path)
    args=lambda arm:m.parser().parse_args(['--arm',arm,'--fork-v1-root',str(src),'--results-root',str(tmp_path/'runs')])
    ctrl,cfg0,cmd0=m.preflight(args('control'),trainer)
    trt,cfg1,cmd1=m.preflight(args('treatment'),trainer)
    assert ctrl!=trt and cfg0['parent_best_sha256']==cfg1['parent_best_sha256']
    assert cfg0['parent_cache_sha256']==cfg1['parent_cache_sha256']
    assert cfg0['precision_weight']==0 and cfg1['precision_weight']==.01
    assert cmd0[cmd0.index('--precision-weight')+1]=='0.0'
    assert cmd1[cmd1.index('--precision-weight')+1]=='0.01'

def test_reject_nonempty_output(tmp_path):
    src,trainer=setup(tmp_path)
    a=m.parser().parse_args(['--arm','control','--fork-v1-root',str(src),'--results-root',str(tmp_path/'runs')])
    d=m.arm_dir(a.results_root,'control');d.mkdir(parents=True);(d/'existing.pt').write_bytes(b'old')
    with pytest.raises(RuntimeError,match='OUTPUT_NOT_EMPTY'):m.preflight(a,trainer)

def test_resume_accepts_exact_lineage_and_rejects_drift(tmp_path):
    src,trainer=setup(tmp_path)
    raw=['--arm','control','--fork-v1-root',str(src),'--results-root',str(tmp_path/'runs')]
    a=m.parser().parse_args(raw)
    d,cfg,_=m.preflight(a,trainer);d.mkdir(parents=True)
    marker=m.lineage_marker(a.results_root,'control');marker.parent.mkdir(parents=True);marker.write_text(json.dumps(cfg));(d/'latest.pt').write_bytes(b'checkpoint')
    resume=m.parser().parse_args(raw+['--resume'])
    assert '--additional-steps' in m.preflight(resume,trainer)[2]
    (src/'best.pt').write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='LINEAGE_DRIFT'):m.preflight(resume,trainer)

def test_missing_v1_fails(tmp_path):
    trainer=tmp_path/m.TRAINER;trainer.write_text('')
    a=m.parser().parse_args(['--arm','control','--fork-v1-root',str(tmp_path/'none')])
    with pytest.raises(FileNotFoundError,match='V1_SOURCE_MISSING'):m.preflight(a,trainer)

def test_equal_data_configuration(tmp_path):
    src,trainer=setup(tmp_path)
    a=m.parser().parse_args(['--arm','treatment','--fork-v1-root',str(src),'--steps','10000','--val-examples','128','--test-examples','128'])
    _,cfg,cmd=m.preflight(a,trainer)
    assert cfg['seed']==20261013 and cfg['val_examples']==128 and cfg['test_examples']==128
    assert cmd[cmd.index('--steps')+1]=='10000'


def test_launch_keeps_trainer_output_empty(monkeypatch,tmp_path):
    src,trainer=setup(tmp_path)
    # The launcher's module expects a colocated trainer. Use an inert file and
    # emulate the real trainer's strict fresh-output precondition.
    monkeypatch.setattr(m, 'TRAINER', 'dummy_trainer.py')
    repo_trainer=MODULE.with_name('dummy_trainer.py')
    repo_trainer.write_text('# stub\n')
    try:
        root=tmp_path/'runs'
        def fake_subprocess(cmd):
            output=Path(cmd[cmd.index('--results-root')+1])
            assert '--resume' in cmd or not output.exists() or not any(output.iterdir())
            output.mkdir(parents=True,exist_ok=True)
            (output/'latest.pt').write_bytes(b'checkpoint')
            return 0
        monkeypatch.setattr(m.subprocess,'call',fake_subprocess)
        argv=['--arm','control','--fork-v1-root',str(src),'--results-root',str(root)]
        assert m.main(argv)==0
        assert (m.arm_dir(root,'control')/'latest.pt').is_file()
        assert m.lineage_marker(root,'control').is_file()
        assert m.main(argv+['--resume'])==0
    finally:
        repo_trainer.unlink(missing_ok=True)
