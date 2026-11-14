"""Gamer v18 numerical error geometry and read-only staircase diagnostics."""
import csv
import json
import shutil
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_staircase as staircase
from tools import nanojev_gamer_v1_diagnostic as diagnostic
from tools.nanojev_gamer_v1_error_atlas import (build_error_atlas, export_csv, write_atlas, describe)
from tools.nanojev_gamer_v1_pairs import write_dataset


def _row(answer, *, gid='test', residual=None, provenance=None, tables=None):
    r={'answer':str(answer),'game_id':gid,'case_sha256':gid+str(answer),
       'table_families':tables or ['calculation']}
    if residual is not None:r['zero_line_residual']=str(residual)
    if provenance=='transition':r['bounded_transition_objective']=True
    if provenance=='catalog':r['bounded_catalog_objective']=True
    return r


def test_error_atlas_distinguishes_signed_target_distance_and_independent_residual(tmp_path):
    rows=[_row(0,residual=30),_row(.05,residual=-.1),_row(-.05,residual=0),
          _row(-.7),_row(1,residual=-100),_row(-4,residual=2),
          _row(30,residual=-30),_row(-3000,provenance='transition'),
          _row(200,provenance='catalog',tables=['calculation','strategic_payoff'])]
    predictions=[.09,.08,-.2,0,1.4,-1,40,-2750,150]
    atlas=build_error_atlas(rows,predictions,split='dev',step=200,
          adapter_sha256='a',dataset_sha256='d',checkpoint_kind='best')
    assert atlas['summary']['cases']==len(rows)
    assert sum(x['cases'] for x in atlas['by_target_signed_bin'].values())==len(rows)
    assert sum(x['cases'] for x in atlas['by_target_abs_bin'].values())==len(rows)
    assert sum(x['cases'] for x in atlas['by_error_band'].values())==len(rows)
    assert sum(x['cases'] for x in atlas['target_bin_by_error_band'])==len(rows)
    assert atlas['by_target_signed_bin']['zero']['cases']==1
    assert atlas['zero_line_residual']['available']==6
    assert atlas['zero_line_residual']['missing']==3
    assert atlas['records'][0]['target_signed_bin']=='zero'
    assert atlas['records'][0]['residual_signed_bin']=='positive:(10,100]'
    assert atlas['records'][2]['residual_signed_bin']=='zero'
    assert atlas['records'][7]['target_signed_bin']=='negative:>1000'
    assert atlas['records'][0]['correct_sign']==0
    assert atlas['by_provenance']['transition']['cases']==1
    assert atlas['by_table_family']['calculation']['cases']==len(rows)
    assert atlas['by_table_family']['strategic_payoff']['cases']==1
    assert atlas['summary']['p99_abs_error']>=atlas['summary']['p90_abs_error']
    assert atlas['summary']['median_absolute_deviation']>=0
    assert atlas['summary']['relative_nonzero_count']==8
    assert atlas['summary']['zero_target_within_0_1']==1.0
    assert 'Target signed distance from zero' in describe(atlas)
    path=tmp_path/'atlas.json'
    write_atlas(atlas,path)
    assert json.loads(path.read_text())['records'][0]['target']==0
    export_csv(atlas,tmp_path/'cases.csv')
    with (tmp_path/'cases.csv').open(newline='',encoding='utf-8') as f:
        recs=list(csv.DictReader(f))
    assert len(recs)==len(rows)
    assert recs[0]['residual_signed_bin']=='positive:(10,100]'
    assert recs[-1]['table_families']=='calculation|strategic_payoff'


def test_error_atlas_keeps_large_and_small_errors_separate():
    cases=[_row(0),_row(0),_row(.2),_row(5),_row(5),_row(500),_row(-500)]
    preds=[0,.2,.1,4,200,495,-600]
    a=build_error_atlas(cases,preds,split='test',step=9,
        adapter_sha256='foo',dataset_sha256='bar',checkpoint_kind='latest')
    assert a['summary']['cases']==7
    assert a['by_target_zone']['zero']['cases']==2
    assert a['by_target_zone']['near_zero_abs_le_1']['cases']==1
    assert a['by_target_zone']['intermediate_1_to_10']['cases']==2
    assert a['by_target_zone']['far_gt_10']['cases']==2
    assert a['by_error_band']['zero']['cases']==1
    assert a['summary']['max_abs_error']==195
    assert a['summary']['within_1']==pytest.approx(4/7)
    assert a['zero_line_residual']['available']==0
    assert a['zero_line_residual']['missing']==7


def test_error_atlas_requires_matching_cases_and_finite_predictions():
    kw=dict(split='dev',step=1,adapter_sha256='a',dataset_sha256='d',checkpoint_kind='latest')
    with pytest.raises(ValueError,match='LENGTH_MISMATCH'):
        build_error_atlas([_row(2)],[],**kw)
    with pytest.raises(ValueError,match='NONFINITE'):
        build_error_atlas([_row(2)],[float('nan')],**kw)
    with pytest.raises(ValueError,match='NONFINITE_ZERO_LINE'):
        build_error_atlas([_row(1,residual='inf')],[1.],**kw)


def test_diagnostic_can_grade_old_head_checkpoint_read_only(tmp_path,monkeypatch):
    run=tmp_path/'transition-005'
    run.mkdir()
    dataset=run/'certified_pairs.jsonl'
    write_dataset(dataset,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=gamer.ensure_manifest(run,dataset,'Qwen/Qwen3-0.6B',False,
                                     include_transition_games=True)
    folder=run/'lora'/'best_adapter'
    folder.mkdir(parents=True)
    (folder/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([.5,1.])},str(folder/'adapter_model.safetensors'))
    state={'schema':gamer.SCHEMA,'phase':'lora','step':100,'pending_eval':False,
           'dataset_sha256':manifest['dataset_sha256'],
           'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
           'hyperparameters':{'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,
               'max_new_tokens':2048,'lora_rank':16,'lora_batch':1,'lora_accum':8,
               'lora_lr':.0001,'lora_eval_every':50,'lora_probe_cases':17},
           'adapter':{'foo.weight':torch.tensor([3.,4.])},'best_step':50}
    torch.save(state,run/'lora'/'latest.pt')
    monkeypatch.setattr(gamer,'model_components',lambda model,device,adapter_dir=None:
                        (torch,object(),SimpleNamespace(eval=lambda: None)))
    monkeypatch.setattr(gamer,'capture_original_features',lambda _torch,model,tok,cases,device,*,batch_size,max_length:
        np.asarray([[len(row['prompt_08'])%11,1.,.5,0.,0.,.1,.2,.3] for row in cases],dtype=np.float32))
    result=staircase.head(run,steps=2,device='cpu',head_eval_every=1)
    assert result['latest_step']==2
    folder=run/'staircase'/'head_cycle_0001'/'to_numerical'
    assert (folder/'error_atlas'/'dev_step_0000001.json').is_file()
    assert (folder/'error_atlas'/'dev_step_0000002.csv').is_file()
    assert (folder/'error_atlas'/'dev_best_step_0000001.json').is_file() or (folder/'error_atlas'/'dev_best_step_0000002.json').is_file()
    # Emulate a PRE-v18 run: no per-case atlas was saved during training.
    shutil.rmtree(folder/'error_atlas')
    ckpt=folder/'latest.pt'
    saved=ckpt.read_bytes()
    diag=diagnostic.diagnose(run,atlas_weights='both')
    assert diag['numerical_error_atlases']
    assert {r['kind'] for r in diag['numerical_error_atlases']}=={'latest','best'}
    assert {r['split'] for r in diag['numerical_error_atlases']}=={'dev','test'}
    for entry in diag['numerical_error_atlases']:
        a=entry['atlas']
        assert len(a['by_game'])==146
        assert sum(v['cases'] for v in a['by_target_signed_bin'].values())==a['summary']['cases']
        assert 'records' not in a
        assert a['summary']['mae']>=0
    assert ckpt.read_bytes()==saved
    assert 'NUMERICAL HEAD ERROR GEOMETRY' in diagnostic.format_report(diag)
    assert len(diag['numerical_error_trends'])==2
    assert {t['split'] for t in diag['numerical_error_trends']}=={'dev','test'}
    assert all(t['scope']=='best_vs_latest' for t in diag['numerical_error_trends'])
    assert all(sum(cell['cases'] for cell in t['by_target_zone'])==
               (146 if t['split']=='dev' else 146) for t in diag['numerical_error_trends'])
    exported=tmp_path/'outside_atlas'
    exported_report=diagnostic.diagnose(run,atlas_weights='best',atlas_split='dev',atlas_csv=exported)
    assert exported_report['numerical_error_atlases']
    csvs=list(exported.glob('*.csv'))
    assert len(csvs)==1
    with csvs[0].open(newline='',encoding='utf-8') as f:
        assert len(list(csv.DictReader(f)))==len([x for x in dataset.read_text().splitlines() if json.loads(x)['split']=='dev'])
    assert not (folder/'error_atlas').exists()
    assert any('GAMER_DIAGNOSTIC' in w for w in diagnostic.diagnose(run,atlas_csv=run/'bad')['warnings'])
