from pathlib import Path
import json
import statistics
from types import SimpleNamespace
import pytest
from tools import nanojev_qwen_mixed_robust_plateau as r
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed


def _cells(raw,pattern=None):
    return {f'cell:{j}':(raw if pattern is None else pattern[j]) for j in range(18)}


def _check(root,step,loss=.02,cells=None,phase=None):
    return r.evaluate(out=root,step=step,phase_step=step if phase is None else phase,
       macro_reference_loss=loss,per_cell=_cells(loss) if cells is None else cells,
       parent_sha256='parent-sha',check_every=250,min_steps=1000)


def _history(root,values):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'mixed_history.jsonl').open('w',encoding='utf8') as f:
        for n,value in enumerate(values,1):
            f.write(json.dumps({'step':250*n,'macro_reference_loss':value,
                'per_cell':_cells(value)})+'\n')


def test_reconstructs_entire_history_and_terminates_when_robust_plateau(tmp_path):
    losses=[.02]*12
    _history(tmp_path,losses)
    got=_check(tmp_path,3250)
    assert got['historical_checks_recovered']==12
    assert got['checks_seen']==13
    assert got['best_rolling_median']==.02
    assert got['rolling_mean']==.02
    assert got['converged'] and got['stale']>=4
    assert json.loads((tmp_path/r.SIDECAR).read_text())['decision_step']==3250


def test_real_mean_median_and_no_mean_spike_false_restart(tmp_path):
    values=[.02,.02,.02,.02,.02,.2,.02,.02]
    _history(tmp_path,values)
    got=_check(tmp_path,2250,.02)
    assert got['rolling_median']==pytest.approx(.02)
    assert got['rolling_mean']>got['rolling_median']
    assert got['converged']


def test_significant_robust_improvement_resets_stale(tmp_path):
    values=[.02]*5+[.015]*4
    _history(tmp_path,values)
    got=_check(tmp_path,2500,.015)
    assert got['best_rolling_median']==pytest.approx(.015)
    assert got['stale']<=2
    assert not got['converged']


def test_old_v1_sidecar_integrated_without_losing_any_past_checks(tmp_path):
    _history(tmp_path,[.03,.02,.02,.02,.02,.02])
    side={'schema':'qwen-mixed-robust-plateau.v1','checks':[
        {'step':1250,'macro_reference_loss':.02},
        {'step':1500,'macro_reference_loss':.02},
        {'step':1750,'macro_reference_loss':.02}]}
    (tmp_path/r.SIDECAR).write_text(json.dumps(side))
    got=_check(tmp_path,2000,.02)
    assert got['checks_seen']==8
    assert got['historical_checks_recovered']==6
    history=json.loads((tmp_path/r.SIDECAR).read_text())
    assert history['schema']==r.VERSION
    assert [x['step'] for x in history['checks']]==[250,500,750,1000,1250,1500,1750,2000]


def test_resume_discards_future_history_and_overwrites_same_step(tmp_path):
    _history(tmp_path,[.04]*7)
    (tmp_path/r.SIDECAR).write_text(json.dumps({'schema':r.VERSION,
        'parent_sha256':'parent-sha','checks':[
         {'step':x*250,'macro_reference_loss':.04} for x in range(1,11)]}))
    got=_check(tmp_path,1500,.03)
    history=json.loads((tmp_path/r.SIDECAR).read_text())
    assert [x['step'] for x in history['checks']]==[250,500,750,1000,1250,1500]
    assert history['checks'][-1]['macro_reference_loss']==.03
    assert got['checks_seen']==6


def test_reject_bad_loss_and_missing_cell_without_creating_sidecar(tmp_path):
    with pytest.raises(RuntimeError,match='UNEXPECTED_TASK_LEVEL'):
        r.evaluate(
            out=tmp_path,step=250,phase_step=250,macro_reference_loss=.02,
            per_cell={'a':.01},parent_sha256='parent-sha')
    with pytest.raises(RuntimeError,match='MACRO_LOSS_MISMATCH'):
        _check(tmp_path,250,.02,cells={f'x{i}':.01 for i in range(18)})
    assert not (tmp_path/r.SIDECAR).exists()


def test_direct_real_runner_has_no_import_interceptor_and_uses_correct_gate():
    import ast,inspect,tools
    src=inspect.getsource(mixed)
    tree=ast.parse(src)
    assert "from tools import nanojev_qwen_mixed_robust_plateau as robust" in src
    assert "if robust_result['converged']" in src
    assert "if plateau['plateau']" not in src
    assert not hasattr(tools,'_install_robust_plateau')


def test_real_pasted_macro_reconstructed_accurately(tmp_path):
    # Actual supplied 6,500-step per-cell data; expected mean from the log.
    vals=[.02710429,.060473714,.053659051,.051286164,.000078083,
          .000452658,.02401714,.003175846,.000012823,.000017952,
          .000008922,.000052349,.003137093,.010917853,.000023479,
          .000054871,.000017294,.000008348]
    got=_check(tmp_path,6500,sum(vals)/18,cells={str(i):x for i,x in enumerate(vals)},phase=6500)
    assert got['task_mean']==pytest.approx(.013027662777777777,abs=1e-6)
    assert got['task_median']<.001
    assert got['top4_loss_share']>.8
    assert got['checks_seen']==1 and not got['converged']
