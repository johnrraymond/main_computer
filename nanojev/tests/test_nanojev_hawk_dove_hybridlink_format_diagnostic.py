"""Read-only format diagnostics and strict-metric controls."""
import json
from argparse import Namespace

import torch

from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_format_diagnostic as diag
from tests.test_nanojev_hawk_dove_hybridlink_curriculum import args as curriculum_args
from tests.test_nanojev_hawk_dove_hybridlink_sequence import setup


def test_strata_exact_zero_policy():
    split = {'rows': [
        {'tier':'T01', 'instance':'a', 'mask':0},
        {'tier':'T01', 'instance':'a', 'mask':1},
        {'tier':'T01', 'instance':'b', 'mask':0}],
        'numeric': torch.tensor([[0.,1.],[0.,1.],[2.,3.]])}
    pred = torch.tensor([[.0001,1.],[0.,1.],[2.,3.]])
    result = diag._xy_strata(pred, split, 10., .05)
    assert result['by_stratum']['exact_zero']['instances'] == 1
    assert result['by_stratum']['exact_zero']['passed'] == 0
    assert result['by_stratum']['away_from_zero']['passed'] == 1
    assert result['exact_zero_coordinates'] == 2


def test_diagnostic_loads_checkpoint_without_mutation(tmp_path):
    source, _, _, _ = setup(tmp_path)
    output = tmp_path / 'experiment'
    curriculum.run(curriculum_args(source, output, 'format', steps=2))
    checkpoint = output / 'arms/recurrent/format/latest.pt'
    sha = diag.base.file_sha256(checkpoint)
    original = diag.base.file_sha256(source)
    report = tmp_path / 'diagnostic.json'
    result = diag.run(Namespace(reference_checkpoint=source, output=output,
                                device='cpu', arm='recurrent', eval_batch_size=32,
                                gradient_samples=4, report=report))
    assert result['trained_step'] == 2
    assert result['dataset_policy'] == 'DEV only; TEST not evaluated'
    assert result['evaluation']['oracle_visible_sum']['max_abs_error'] >= 0
    assert 'shuffled_teacher' in result['evaluation']['xy']
    assert 'answer' in result['loss_gradients_on_train']
    assert json.loads(report.read_text())['checkpoint_sha256'] == sha
    assert diag.base.file_sha256(checkpoint) == sha
    assert diag.base.file_sha256(source) == original
