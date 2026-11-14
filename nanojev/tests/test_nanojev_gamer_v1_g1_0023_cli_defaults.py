"""G1-0023 uses the normal NanoJev module invocation with zero path arguments."""
from pathlib import Path

import pytest
from tools import nanojev_gamer_v1_graph_curriculum as cli
from tools import nanojev_gamer_v1_g1_structural_diagnosis as diag


@pytest.fixture
def g1_layout(tmp_path,monkeypatch):
    nanojev=tmp_path/'nanojev'
    tools=nanojev/'tools'
    tools.mkdir(parents=True)
    monkeypatch.setattr(cli,'__file__',str(tools/'nanojev_gamer_v1_graph_curriculum.py'))
    reports=tmp_path/'runtime'/'reports'/'gamer-v1'
    reports.mkdir(parents=True)
    transfer=reports/'graph-g1-decoder-transfer-0008'/'transfer_report.json'
    transfer.parent.mkdir()
    transfer.write_text('{}',encoding='utf-8')
    for name in ('graph-g1-fresh-dev-0011', 'graph-g1-null-boundary-0012',
                 'graph-g1-calibration-validation-0014','graph-g1-structural-ood-0022'):
        (reports/name).mkdir()
    monkeypatch.chdir(nanojev)
    seen=[]
    def capture(**kwargs):
        seen.append((kwargs,Path.cwd()))
        return {'ok':True}
    monkeypatch.setattr(diag,'run',capture)
    return tmp_path,nanojev,reports,transfer,seen


def test_short_standard_module_command_resolves_everything(g1_layout):
    repo,nanojev,reports,transfer,seen=g1_layout
    assert cli.main(['frob-g1-structural-diagnosis','--device','cuda'])=={'ok':True}
    assert len(seen)==1
    opts,cwd=seen[0]
    assert cwd==repo
    assert Path.cwd()==nanojev
    assert opts=={
        'root':str(reports.resolve()),
        'transfer_report':str(transfer.resolve()),
        'fresh_run':str((reports/'graph-g1-fresh-dev-0011').resolve()),
        'null_run':str((reports/'graph-g1-null-boundary-0012').resolve()),
        'validation_run':str((reports/'graph-g1-calibration-validation-0014').resolve()),
        'ood_run':str((reports/'graph-g1-structural-ood-0022').resolve()),
        'output':str((reports/'graph-g1-structural-diagnosis-0023').resolve()),
        'device':'cuda','max_length':2048,
    }


def test_explicit_path_override_still_supported(g1_layout):
    repo,nanojev,reports,transfer,seen=g1_layout
    custom=reports/'custom_data'; custom.mkdir()
    cli.main(['frob-g1-structural-diagnosis','--fresh-run',str(custom),
              '--output','custom-output','--device','cpu'])
    assert seen[0][0]['fresh_run']==str(custom.resolve())
    assert seen[0][0]['output']==str((nanojev/'custom-output').resolve())
    assert seen[0][0]['device']=='cpu'
    assert Path.cwd()==nanojev


def test_missing_reference_fails_before_running(g1_layout):
    repo,nanojev,reports,transfer,seen=g1_layout
    transfer.unlink()
    with pytest.raises(FileNotFoundError,match='TRANSFER_REPORT_MISSING'):
        cli.main(['frob-g1-structural-diagnosis'])
    assert not seen
    assert Path.cwd()==nanojev


def test_missing_prior_run_fails_before_running(g1_layout):
    repo,nanojev,reports,transfer,seen=g1_layout
    (reports/'graph-g1-fresh-dev-0011').rmdir()
    with pytest.raises(FileNotFoundError,match='PRIOR_RUN_MISSING'):
        cli.main(['frob-g1-structural-diagnosis'])
    assert not seen
    assert Path.cwd()==nanojev


def test_cwd_restored_after_model_error(g1_layout,monkeypatch):
    repo,nanojev,reports,transfer,seen=g1_layout
    def fail(**kwargs):
        assert Path.cwd()==repo
        raise RuntimeError('CUDA crashed')
    monkeypatch.setattr(diag,'run',fail)
    with pytest.raises(RuntimeError,match='CUDA crashed'):
        cli.main(['frob-g1-structural-diagnosis'])
    assert Path.cwd()==nanojev
