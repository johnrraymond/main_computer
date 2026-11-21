"""Focused functional counterexamples for frozen MIT binding experiment.

No CUDA, checkpoints, or external dataset required; these exercise the real
MILP and the new scoring-stage attribution, not peripheral import machinery.
"""

from tools import nanojev_gamer_v1_mit_binding_0002 as b


def identity_surface():
    prompt = 'Alpha; Beta; Alpha'
    objs = [[0, 5, 'action'], [7, 11, 'action'], [13, 18, 'action']]
    scores = {(0, 1): 10., (0, 2): 9.}
    surface = {
        'g1_objects': objs,
        'binding_edges': [],
        'pair_margins': [[i, j, [-6., scores.get((i, j), -6.), -6., -6.]]
                         for i in range(3) for j in range(3) if i != j],
    }
    spec = {'method': 'joint', 'threshold': 0.,
            'out_caps': {'action|action|refers_to': 1},
            'in_caps': {'action|action|refers_to': 1}}
    return prompt, surface, spec


def test_identity_is_an_in_solver_constraint_and_recovers_correct_competitor():
    prompt, surface, spec = identity_surface()
    original_margins = [[i, j, m.copy()] for i, j, m in surface['pair_margins']]
    baseline, _ = b.decode(surface, prompt, spec, 'mit_wsp_baseline')
    identity, meta = b.decode(surface, prompt, spec, 'identity')
    assert len(baseline) == len(identity) == 1
    assert next(iter(baseline))[3] == (7, 11)    # Wrong high-scoring name.
    assert next(iter(identity))[3] == (13, 18)  # Right name, lower margin.
    assert meta['status'] == 'OPTIMAL'
    assert surface['pair_margins'] == original_margins  # No mutation.


def test_locality_and_scope_are_separate_precommitted_ablations():
    prompt = '5 benefit; ' + 'x' * 50 + ' 7 outcome'
    quantity = [0, 1, 'quantity']
    outcome_near = [2, 9, 'outcome']
    outcome_far = [len(prompt)-7, len(prompt), 'outcome']
    assert b.allowed(prompt, quantity, outcome_far, 'measures', 'identity')
    assert not b.allowed(prompt, quantity, outcome_far, 'measures', 'identity_gap45')
    assert b.allowed(prompt, quantity, outcome_near, 'measures', 'identity_gap45')
    assert not b.allowed(prompt, quantity, outcome_far, 'measures', 'identity_clause')
    assert b.allowed(prompt, quantity, outcome_far, 'applies_to', 'identity_gap45')
    assert not b.allowed(prompt, quantity, outcome_far, 'applies_to', 'identity_clause')
    assert b.allowed(prompt, quantity, outcome_near, 'measures', 'identity_clause')


def test_missing_gold_is_attributed_to_solver_not_false_negative_training_noise():
    prompt, surface, spec = identity_surface()
    baseline, _ = b.decode(surface, prompt, spec, 'mit_wsp_baseline')
    truth = {('action', (0, 5), 'action', (13, 18), 'refers_to')}
    missing = b.missing_attribution(prompt, surface, spec, truth, baseline,
                                    'mit_wsp_baseline')
    assert missing['solver_competition'] == 1
    assert missing['stronger_same_source_rival'] == 1
    assert missing['candidate_below_threshold'] == 0
    repaired, _ = b.decode(surface, prompt, spec, 'identity')
    assert repaired == truth
    assert sum(b.missing_attribution(prompt, surface, spec, truth,
                                     repaired, 'identity').values()) == 0


def test_missing_gold_can_be_due_to_threshold_or_upstream_g1():
    prompt, surface, spec = identity_surface()
    predicted = set()
    truth = {('action', (7, 11), 'action', (13, 18), 'refers_to'),
             ('action', (20, 22), 'action', (13, 18), 'refers_to')}
    stats = b.missing_attribution(prompt, surface, spec, truth,
                                  predicted, 'mit_wsp_baseline')
    assert stats['candidate_below_threshold'] == 1
    assert stats['g1_unreachable'] == 1
    assert stats['solver_competition'] == 0


def test_gold_violation_of_a_rule_is_reported_not_silently_discarded():
    prompt, surface, spec = identity_surface()
    truth = {('action', (0, 5), 'action', (7, 11), 'refers_to')}
    selected, _ = b.decode(surface, prompt, spec, 'identity')
    stats = b.missing_attribution(prompt, surface, spec, truth, selected, 'identity')
    assert stats['gold_blocked_by_rule'] == 1
    assert stats['g1_unreachable'] == 0


def test_default_is_new_sealed_run_not_prior_exposed_campaign():
    assert b.CAMPAIGN == 'oracle-fabric-g2-mit-binding-0002'
    assert b.DEV == 48 and b.CHALLENGE == 32
    assert b.GAP_LIMIT == 45
    assert 'mit_wsp_baseline' in b.ARMS


def test_end_to_end_seal_blind_score_and_resume_without_gold_leak(tmp_path, monkeypatch):
    """Exercise actual stage writing/verification with a mocked inference runtime.

    The gold answer file is removed during blind inference to prove that
    predict() never reads it. We restore the identical file for score().
    """
    import hashlib
    import json
    import torch

    monkeypatch.setattr(b, 'DEV', 1)
    monkeypatch.setattr(b, 'CHALLENGE', 1)
    source = tmp_path / 'existing-wsp'
    (source / 'seal').mkdir(parents=True)
    (source / 'seal' / 'commit.json').write_text('{"prior":true}')
    prompt, surface, spec = identity_surface()
    true_edge = ('action', (0, 5), 'action', (13, 18), 'refers_to')
    gold = {true_edge}

    def fake_lineage(root):
        return {'wsp_dest': source, 'mit_dest': tmp_path / 'existing-mit',
                'paths': {}, 'spec': spec,
                'edit': {'edited_head_sha256': 'frozen-head-hash'},
                'checkpoint': source / 'checkpoint'}

    monkeypatch.setattr(b, 'source_lineage', fake_lineage)
    def cases(*args):
        return [{ 'case_id': 'case-' + split, 'split': split,
                  'family': 'allocation', 'prompt': prompt,
                  'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                  'graph': {'toy': True}}
                for split in ('dev', 'challenge')]
    monkeypatch.setattr(b, 'new_cases', cases)

    class FakeHead:
        def eval(self): return self
        def requires_grad_(self, *_): return self

    class FakeComposite:
        def predict(self, *args, **kwargs):
            return {'token_spans': []}

    monkeypatch.setattr(b.structural, '_load_runtime',
                        lambda *args: (None, None, FakeComposite(), None, None))
    monkeypatch.setattr(b.wsp, 'load_edited_head', lambda *args, **kwargs: FakeHead())
    monkeypatch.setattr(b.legacy.previous, '_capture_inference',
                        lambda *args: ({'eligible': torch.ones(3)}, None))
    monkeypatch.setattr(b.legacy, '_infer', lambda *args, **kwargs: surface)
    monkeypatch.setattr(b.legacy, 'truth', lambda *args: gold)

    b.prepare(root=tmp_path)
    dest = b.path_for(tmp_path, None)
    answer = dest / 'seal' / 'answers.SECRET.jsonl'
    sealed_answer = answer.read_bytes()
    answer.unlink()
    lock = b.predict(root=tmp_path, device='cpu')
    assert lock['gold_accessed'] is False
    assert (dest / 'blind' / 'predictions.jsonl').exists()
    # The original sealed bytes must be restored before scoring.
    answer.write_bytes(sealed_answer)
    report = b.score(root=tmp_path)
    assert report['splits']['dev']['identity']['exact'] == 1
    assert report['splits']['dev']['mit_wsp_baseline']['exact'] == 0
    assert report['splits']['challenge']['identity']['exact'] == 1
    assert report['missing_attribution']['dev']['mit_wsp_baseline']['solver_competition'] == 1
    replay = b.run(root=tmp_path, device='cpu')
    assert replay['status'] == report['status']
    assert replay['predictions_sha256'] == report['predictions_sha256']
