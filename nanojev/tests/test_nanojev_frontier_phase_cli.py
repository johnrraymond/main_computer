"""Unified restartable phase-flag tests, with no CUDA or Qwen downloads."""
from pathlib import Path
import json
import pytest
import torch

import tools
root=Path(__file__).resolve().parents[1]/'tools'
if root.is_dir() and str(root) not in tools.__path__:
    if hasattr(tools.__path__,'insert'):tools.__path__.insert(0,str(root))
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed
from tools import nanojev_qwen_lm_frontier_tasks as tasks


def _base(tmp_path):
    return ['--stage','run','--device','cpu',
        '--seed-working',str(tmp_path/'ladder'/'working.pt'),
        '--stage1-checkpoint',str(tmp_path/'stage'/'best.pt'),
        '--text-checkpoint',str(tmp_path/'text'/'best.pt'),
        '--output',str(tmp_path/'ordered')]


def test_unchanged_old_command_and_explicit_phase_one_identical(tmp_path,monkeypatch):
    collected=[]
    monkeypatch.setattr(fr,'run',lambda a:collected.append((a.phase,fr._policy(a),a.output)) or {'ok':True})
    args=_base(tmp_path)
    assert fr.main(args)=={'ok':True}
    assert fr.main(['--phase','systematic',*args])=={'ok':True}
    assert collected[0][1:]==collected[1][1:]
    assert collected[0][0]=='systematic'
    assert collected[0][1]['seed']==42257
    assert collected[0][1]['check_every']==50


def test_samples_dispatch_same_entrypoint_without_resetting_phase_one(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(mixed,'main',lambda argv:seen.append(argv) or {'phase':2})
    args=[*(_base(tmp_path)),'--phase','samples','--samples-check-every','10',
          '--samples-max-steps','20','--samples-seed','9001']
    assert fr.main(args)=={'phase':2}
    invocation=seen[0]
    def val(key):return invocation[invocation.index('--'+key)+1]
    assert val('frontier-output')==str((tmp_path/'ordered').resolve())
    assert val('output')==str((tmp_path/'ordered_mixed_v1').resolve())
    assert val('seed')=='9001'
    assert val('check-every')=='10'
    assert val('max-steps')=='20'
    assert '--seed-working' not in invocation  # Phase 2 must not seed from old ladder
    assert val('stage1-checkpoint')==str(tmp_path/'stage'/'best.pt')
    assert val('text-checkpoint')==str(tmp_path/'text'/'best.pt')


def test_sample_defaults_not_inherited_from_ordered_phase(tmp_path,monkeypatch):
    result=[]
    monkeypatch.setattr(mixed,'main',lambda argv:result.extend(argv))
    fr.main([*_base(tmp_path),'--phase','samples','--seed','123',
            '--check-every','5','--greedy-cases','1','--lr','0.1'])
    def val(key):return result[result.index('--'+key)+1]
    assert val('seed')=='91733'
    assert val('check-every')=='250'
    assert val('greedy-cases')=='6'
    assert val('lr')=='5e-05'


def test_separate_phase_directories_nonoverlap(tmp_path):
    args=fr.main
    source=str(tmp_path/'ordered')
    for invalid in (source,str(tmp_path/'ordered'/'nested'),str(tmp_path)):
        with pytest.raises(ValueError,match='OUTPUTS_MUST_BE_SEPARATE'):
            fr.main(['--phase','samples','--stage','status','--output',source,
                    '--samples-output',invalid])


def test_glasses_status_preview_and_unified_command_no_old_deck(tmp_path):
    argv=['--phase','glasses','--stage','status','--output',str(tmp_path/'ordered')]
    first=fr.main(argv)
    assert first['samples_ready'] is False
    assert first['current'] is None
    assert not (tmp_path/'ordered_mixed_v1').exists()
    assert fr.main([*argv[:3], 'preview', *argv[4:]])['ready'] is False
    with pytest.raises(RuntimeError,match='COMPLETED_EASY|COMPLETED_MIXED'):
        fr.main(['--phase','glasses','--glass-scheme','mixed',
                 '--stage','run','--output',str(tmp_path/'ordered')])


def test_phase_two_real_preflight_requires_completed_ordered_checkpoint(tmp_path):
    ordered=tmp_path/'ordered';ordered.mkdir()
    a=tmp_path/'stage'/'best.pt';b=tmp_path/'text'/'best.pt'
    for path in (a,b):path.parent.mkdir();path.write_bytes(b'fake')
    argv=['--phase','samples','--stage','run','--device','cpu',
          '--output',str(ordered),
          '--stage1-checkpoint',str(a),'--text-checkpoint',str(b)]
    with pytest.raises(FileNotFoundError,match='MIXED_REQUIRES_ORDERED_FINAL'):
        fr.main(argv)
    assert not (tmp_path/'ordered_mixed_v1'/'working.pt').exists()


def test_samples_status_resume_existing_mixed_checkpoint(tmp_path):
    ordered=tmp_path/'ordered';ordered.mkdir()
    samples=tmp_path/'ordered_mixed_v1';samples.mkdir()
    ck={'schema':mixed.SCHEMA,'state':{'schema':mixed.SCHEMA,'status':'PAUSED',
        'parent_sha256':'abc','phase_step':37,'sample_cursor':148}}
    torch.save(ck,samples/'working.pt')
    state=fr.main(['--phase','samples','--stage','status','--output',str(ordered)])
    assert state['phase_step']==37 and state['sample_cursor']==148
