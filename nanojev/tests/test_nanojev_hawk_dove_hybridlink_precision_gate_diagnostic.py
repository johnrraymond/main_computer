"""Precision zero-gate read-only controls, including an oracle-only ceiling."""
from argparse import Namespace

import torch

from tools import nanojev_hawk_dove_hybridlink_precision_gate_diagnostic as gate
from tools import nanojev_hawk_dove_hybridlink_precision as precision
from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tests.test_nanojev_hawk_dove_hybridlink_precision import opts
from tests.test_nanojev_hawk_dove_hybridlink_curriculum import args as curriculum_args
from tests.test_nanojev_hawk_dove_hybridlink_sequence import setup


def _split(truth):
    # strict_metrics requires all eight tiers and every row to be covered.
    n=len(truth)
    return {'numeric':truth.repeat((len(base.blind.TIERS),1)),
            'rows':[{'tier':tier,'instance':f'i{idx}','mask':0}
                    for tier in base.blind.TIERS for idx in range(n)]}


def test_oracle_gate_ceiling_exposes_nonzero_regression_failure():
    # Two nearly-zero misses, one exact zero that a perfect gate can fix,
    # and one away-from-zero hit.
    scale=1000.
    truth=torch.tensor([[0.,30.],[.5,40.],[10.,20.],[900.,900.]])
    pred=torch.tensor([[2.,.03],[.05,.04],[.02,.02],[.9,.9]])
    prob=torch.tensor([[.9,.01],[.9,.01],[.01,.01],[.01,.01]])
    audit=gate.audit_predictions(pred.repeat((8,1)),prob.repeat((8,1)),_split(truth),scale,thresholds=(.5,1.))
    assert audit['ungated_raw']['strata']['exact_zero']['joint_all_masks_rate']==0.
    assert audit['oracle_zero_gate_diagnostic_only']['strata']['exact_zero']['joint_all_masks_rate']==1.
    assert audit['oracle_zero_gate_diagnostic_only']['strata']['near_zero_nonzero']['joint_all_masks_rate']==0.
    t=audit['thresholds'][0]
    assert t['zero_confusion']['false_positive_near']==8
    assert t['zero_confusion']['true_positive']==8


def test_threshold_one_disables_zero_gate():
    truth=torch.tensor([[0.,1.],[2.,3.]])
    pred=torch.tensor([[.1,.1],[.2,.3]])
    prob=torch.full_like(pred,.999)
    audit=gate.audit_predictions(pred.repeat((8,1)),prob.repeat((8,1)),_split(truth),10.,thresholds=(.5,1.))
    assert audit['thresholds'][1]['zero_confusion']['false_positive']==0
    assert audit['thresholds'][1]['zero_confusion']['predicted_zero_coordinates']==0
    assert audit['thresholds'][0]['zero_confusion']['predicted_zero_coordinates']==32


def test_read_only_checkpoint_audit(tmp_path):
    source, _, _, _=setup(tmp_path)
    old=tmp_path/'curriculum'
    curriculum.run(curriculum_args(source,old,steps=2))
    out=tmp_path/'precision'
    precision.run(opts(source,old,out,steps=1))
    best=out/'best.pt'; fmt=old/'arms/recurrent/format/latest.pt'
    before=[base.file_sha256(p) for p in (source,fmt,best)]
    args=Namespace(reference_checkpoint=source,curriculum_output=old,
                   precision_output=out,device='cpu',eval_batch_size=64,
                   cpu_threads=1,near_fraction=.01,tolerance=.05,
                   report=out/'gate_audit.json')
    report=gate.run(args)
    assert report['schema']==gate.SCHEMA
    assert args.report.is_file()
    assert report['evaluation']['oracle_zero_gate_diagnostic_only']
    assert before==[base.file_sha256(p) for p in (source,fmt,best)]
    assert not (out/'gate.json').exists()
