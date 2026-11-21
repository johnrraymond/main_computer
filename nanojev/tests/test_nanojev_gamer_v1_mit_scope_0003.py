"""Scientific acceptance checks for MIT-SCOPE-0003; no GPU needed."""
import hashlib
import json
import random

import pytest
import torch

from tools import nanojev_gamer_v1_mit_scope_0003 as s


def sample(family, index=0):
    factory = s.corpus.allocation if family == 'allocation' else s.corpus.encounter
    return factory(random.Random(index + 81), 'test', index)


def visible_objects(graph):
    nodes = [n for n in graph['nodes'] if 'span' in n]
    return nodes, [[*n['span'], n['kind']] for n in nodes]


def test_all_transformations_preserve_all_original_annotations_and_game_result():
    for family in ('allocation', 'encounter'):
        for index in range(8):
            original = sample(family, index)
            original_nodes = {n['id']: n for n in original['graph']['nodes']
                              if 'span' in n}
            for variant in s.VARIANTS:
                prompt, graph = s.transform(original, variant, index)
                assert graph['edges'] == original['graph']['edges']
                assert graph['objective'] == original['graph']['objective']
                for node in graph['nodes']:
                    if 'span' in node:
                        start, end = node['span']
                        old = original_nodes[node['id']]
                        assert prompt[start:end] == original['prompt'][
                            old['span'][0]:old['span'][1]]
                assert s.contract.execute_graph(graph)['decisions'] == original[
                    'expected_decisions']
                if variant == 'distractor':
                    assert 'Unrelated archive note' in prompt
                if variant == 'intra_semicolon':
                    assert prompt.count(';') > original['prompt'].count(';')
                if variant == 'newlines':
                    assert '\n' in prompt


def test_gold_scope_coverage_counterexample_and_no_exposure_in_decoder():
    old_blocks = 0
    new_blocks = 0
    intra_crossings = 0
    for family in ('allocation', 'encounter'):
        for index in range(12):
            original = sample(family, index)
            for variant in s.VARIANTS:
                prompt, graph = s.transform(original, variant, index)
                visible, objects = visible_objects(graph)
                mapping = {node['id']: i for i, node in enumerate(visible)}
                scope_ids = s.scopes(prompt, objects)
                for edge in graph['edges']:
                    if edge['type'] not in ('refers_to', 'measures', 'applies_to'):
                        continue
                    i, j = mapping[edge['src']], mapping[edge['dst']]
                    new_ok = s.allowed(prompt, objects[i], objects[j], edge['type'],
                                       'identity_scope', source_scope=scope_ids[i],
                                       target_scope=scope_ids[j])
                    if not new_ok:
                        new_blocks += 1
                    if not s.allowed(prompt, objects[i], objects[j], edge['type'],
                                     'identity_clause'):
                        old_blocks += 1
                    if variant == 'intra_semicolon' and s.crosses_separator(
                            prompt, graph, edge):
                        intra_crossings += 1
    assert new_blocks == 0
    assert old_blocks > 100
    assert intra_crossings > 100


def contrived_surface():
    prompt = 'Alpha benefit 5; cost 7; Beta benefit 3 cost 2'
    first = prompt.find('Alpha')
    second = prompt.find('Beta')
    cost = prompt.find('7')
    objects = [[first, first + 5, 'action'],
               [cost, cost + 1, 'quantity'],
               [second, second + 4, 'action']]
    pairs = []
    for i in range(3):
        for j in range(3):
            if i != j:
                margins = [-7., -7., -7., -7.]
                if i == 1 and j == 0:  # correctly belongs to Alpha
                    margins[3] = 9.
                if i == 1 and j == 2:  # wrong, but higher model score
                    margins[3] = 10.
                pairs.append([i, j, margins])
    surface = {'g1_objects': objects, 'pair_margins': pairs, 'binding_edges': []}
    spec = {'method': 'joint', 'threshold': 0.,
            'out_caps': {'quantity|action|applies_to': 1},
            'in_caps': {'action|quantity|applies_to': 1}}
    return prompt, surface, spec


def test_action_anchored_scope_rescues_correct_edge_across_valid_semicolon():
    prompt, surface, spec = contrived_surface()
    baseline, _ = s.decode(surface, prompt, spec, 'mit_wsp_baseline')
    semicolon, _ = s.decode(surface, prompt, spec, 'identity_clause')
    generalized, diag = s.decode(surface, prompt, spec, 'identity_scope')
    assert len(baseline) == 1
    assert next(iter(baseline))[3] == tuple(surface['g1_objects'][2][:2])
    assert len(semicolon) == 0
    assert len(generalized) == 1
    assert next(iter(generalized))[3] == tuple(surface['g1_objects'][0][:2])
    assert diag['status'] == 'OPTIMAL'


def test_missing_attribution_identifies_scope_blocked_gold_without_false_claims():
    prompt, surface, spec = contrived_surface()
    gold = {('quantity', tuple(surface['g1_objects'][1][:2]),
             'action', tuple(surface['g1_objects'][0][:2]), 'applies_to')}
    semi, _ = s.decode(surface, prompt, spec, 'identity_clause')
    stats = s.missing_attribution(prompt, surface, spec, gold, semi,
                                  'identity_clause')
    assert stats['gold_blocked_by_rule'] == 1
    generalized, _ = s.decode(surface, prompt, spec, 'identity_scope')
    assert generalized == gold
    assert sum(s.missing_attribution(prompt, surface, spec, gold, generalized,
                                     'identity_scope').values()) == 0


def test_scope_prediction_survives_malformed_g1_span_over_punctuation():
    prompt = 'Alpha; Beta'
    objects = [[0, 7, 'action'], [7, 11, 'action']]
    assert len(s.scopes(prompt, objects)) == 2


def test_complete_seal_predict_score_resume_with_secret_gold_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(s, 'DEV_BASES', 1)
    monkeypatch.setattr(s, 'CHALLENGE_BASES', 1)
    parent = tmp_path / 'parent'
    (parent / 'seal').mkdir(parents=True)
    (parent / 'seal' / 'commit.json').write_text('{"parent":true}')
    spec = contrived_surface()[2]

    def fake_source(root):
        return {'wsp_dest': parent, 'mit_dest': tmp_path / 'mit',
                'paths': {}, 'spec': spec,
                'edit': {'edited_head_sha256': 'frozen'},
                'checkpoint': parent / 'checkpoint'}

    def cases(*args):
        result = []
        for split, i in (('dev', 2), ('challenge', 3)):
            base = sample('allocation', i)
            for variant in s.VARIANTS:
                prompt, graph = s.transform(base, variant, i)
                result.append({
                    'case_id': split + '/' + variant,
                    'base_id': split + '/1', 'variant': variant,
                    'split': split, 'family': base['family'],
                    'prompt': prompt, 'prompt_sha256': s.digest(prompt),
                    'graph': graph})
        return result

    monkeypatch.setattr(s, 'source_lineage', fake_source)
    monkeypatch.setattr(s, 'new_cases', cases)

    class FakeHead:
        def eval(self): return self
        def requires_grad_(self, *_): return self

    class FakeComposite:
        def predict(self, *args, **kwargs): return {'token_spans': []}

    monkeypatch.setattr(s.structural, '_load_runtime',
                        lambda *args: (None, None, FakeComposite(), None, None))
    monkeypatch.setattr(s.wsp, 'load_edited_head',
                        lambda *args, **kwargs: FakeHead())
    monkeypatch.setattr(s.legacy.previous, '_capture_inference',
                        lambda net, tok, prompt, device, length: (
                            {'eligible': torch.ones(4), 'prompt': prompt}, None))

    def fake_infer(head, hidden, eligible, spans, prepared, spec, *, with_margins):
        prompt = prepared['prompt']
        a = prompt.find('Sigma')
        if a == -1:
            a = prompt.find('Axiom')
        if a == -1:
            a = 0
        a = min(a, len(prompt) - 1)
        b = max(a + 1, min(len(prompt), a + 5))
        other = 0 if a > 10 else len(prompt) - 4
        if other < b and other >= a:
            other = max(b, len(prompt) - 3)
        other_end = min(len(prompt), other + 3)
        objs = [[a, b, 'action'], [other, other_end, 'action']]
        # For this lifecycle test, inference need only provide a legal
        # surface; it is not pretending to model actual game semantics.
        return {'g1_objects': objs, 'binding_edges': [],
                'pair_margins': [[0, 1, [-9., 2., -9., -9.]],
                                 [1, 0, [-9., 1., -9., -9.]]]}

    monkeypatch.setattr(s.legacy, '_infer', fake_infer)
    s.prepare(root=tmp_path)
    dest = s.path_for(tmp_path, None)
    secret = dest / 'seal' / 'answers.SECRET.jsonl'
    locked_gold = secret.read_bytes()
    secret.unlink()
    predicted = s.predict(root=tmp_path, device='cpu')
    assert predicted['gold_accessed'] is False
    assert (dest / 'blind' / 'lock.json').is_file()
    secret.write_bytes(locked_gold)
    report = s.score(root=tmp_path)
    assert report['status'] == 'MIT_SCOPE_BLIND_ROBUSTNESS_MEASURED_NOT_CERTIFIED'
    assert report['model_training'] is False
    assert report['summary']['dev']['periods']['identity_scope']['cases'] == 1
    assert report['summary']['challenge']['intra_semicolon']['identity_clause']['cases'] == 1
    assert s.run(root=tmp_path, device='cpu')['prediction_sha256'] == report['prediction_sha256']


def test_new_version_preserves_old_0002_source_and_distinct_campaign():
    assert s.CAMPAIGN == 'oracle-fabric-g2-mit-scope-0003'
    assert 'identity_scope' not in s.old.ARMS
    assert 'intra_semicolon' in s.VARIANTS
    assert s.DEV_BASES == 16 and s.CHALLENGE_BASES == 16
