"""Focused CPU diagnostics for the frozen v4.2 teacher checkpoint."""
import argparse
import json

import pytest
import torch
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tests.test_nanojev_hawk_dove_gaussian_hybrid_agent_v2 import fixture


def seed_case(tmp_path):
    source,ref,splits=fixture(tmp_path)
    ci=torch.load(source,weights_only=True)['identity']
    data,domain,manifest=blind._dataset(argparse.Namespace(**ci),torch.device('cpu'))
    model=v4.ContextualGaussianHybridAgent(ref)
    run_dir=tmp_path/'run';run_dir.mkdir()
    args=argparse.Namespace(bootstrap_steps=4,warmup_steps=2,steps=2,probe_every=1,
        batch_size=8,bootstrap_lr=.001,warmup_lr=.001,lr=.001,seed=33,heads=4,layers=1,
        tolerance=.05,min_teacher_gain=.02)
    config=v4._identity(args,ci,manifest,probe.sha256(source))
    (run_dir/'config.json').write_text(json.dumps(config))
    torch.save({'schema':v4.SCHEMA+'.teacher-best','identity':config,
                'step':4,'model':v4._weights(model)},run_dir/'teacher_best.pt')
    return source,run_dir,data,domain


def test_oracle_math_reconstructs_both_outputs(tmp_path):
    _,_,data,_=seed_case(tmp_path)
    stats=probe.oracle_product_audit(data['dev']['features'],data['dev']['numeric'],2)
    assert stats['within_one_output_unit_fraction']==1.
    assert stats['max_absolute_roundoff']<1


def test_loaded_teacher_checkpoint_remains_immutable(tmp_path):
    source,run_dir,data,domain=seed_case(tmp_path)
    before=(probe.sha256(source),probe.sha256(run_dir/'teacher_best.pt'))
    model,splits,loaded_domain,config,saved=probe.load_experiment(source,run_dir)
    assert loaded_domain==domain
    result=probe.analyze(model,splits['dev'],loaded_domain,2,2.5,batch_size=16,grad_rows=4)
    assert set(result['paired_dev_certification']['min_tier_accuracy'])==set(probe.CONDITIONS)
    assert result['shuffled_control']['distinct_sequence_donors']==len(splits['dev']['rows'])
    assert result['backprop_sensitivity']['teacher_tokens']['nonzero']
    assert (probe.sha256(source),probe.sha256(run_dir/'teacher_best.pt'))==before


def test_identity_refusal_and_real_shuffle(tmp_path):
    source,run_dir,_,_=seed_case(tmp_path)
    model,splits,domain,config,saved=probe.load_experiment(source,run_dir)
    data=splits['dev']
    preds,stats,shuffle=probe.predictions_and_path(model,data,domain,batch_size=5)
    assert shuffle['distinct_sequence_donors']==len(data['rows'])
    assert (preds['reference']-preds['student_no_thought']).abs().max()<1
    saved['identity']['source_sha256']='corrupt'
    torch.save(saved,run_dir/'teacher_best.pt')
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        probe.load_experiment(source,run_dir)


def test_paired_all_masks_and_failure_classification():
    rows=[{'tier':tier,'instance':f'{tier}/i','matrix_index':0,'mask':m}
          for tier in blind.TIERS for m in (0,1)]
    n=len(rows)
    truth=torch.ones(n,2)
    data={'rows':rows,'numeric':truth}
    preds={c:truth.clone() for c in probe.CONDITIONS}
    scores=probe.paired_scores(preds,data,.05)
    assert scores['min_tier_accuracy']['correct']==1
    assert scores['tiers']['T01']['correct_paired_against']['shuffled']['rescued']==0


def test_no_accidental_training_or_full_test_split_eval(monkeypatch,tmp_path):
    source,run_dir,_,_=seed_case(tmp_path)
    args=probe.parser().parse_args(['--reference-checkpoint',str(source),'--run-dir',str(run_dir),
                                    '--device','cpu','--batch-size','16','--grad-rows','4','--cpu-threads','1'])
    before=(probe.sha256(source),probe.sha256(run_dir/'teacher_best.pt'))
    report=probe.run(args)
    assert report['provenance']['split']=='DEV'
    assert report['paired_dev_certification']['min_tier_accuracy']
    assert (probe.sha256(source),probe.sha256(run_dir/'teacher_best.pt'))==before
    assert (run_dir/'teacher_diagnostic_v1'/'report.json').exists()
