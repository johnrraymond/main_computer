#!/usr/bin/env python3
"""V33: train a NEW GAMER decision head on 15 T08 Hawk/Dove surface variants.

The model sees only the original narrative and frozen base-Qwen representations.
No rule-derived payoff components, oracle answer, V30 deterministic difference,
case ID, or labels enter features or inference. Five whole surface variants are
sealed until an irrevocable, one-time test audit. NEVER promotes tactical-v6.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace

SCHEMA = 'nanojev.gamer.v1.hawk-dove-15train-5holdout.v33'
HEAD_SCHEMA = 'nanojev.gamer.v1.learned-t08-action-head.v33'
TRAIN_VARIANTS = tuple(i for i in range(20) if i not in (1, 6, 11, 16, 19))
TEST_VARIANTS = (1, 6, 11, 16, 19)
N_PAIRS = 12
# Different encounters and payoff table formats. None contains the expected action.
CONTEXTS = (
    'An expedition captain faces a rival at a passage. The captain can Contest (D) or Yield (C).',
    'At a contested rendezvous, two crews select actions independently. Our actions are Contest (D) and Yield (C).',
    'A patrol must decide whether to assert a claim or give way. Contest is D; Yield is C.',
    'At a supply checkpoint the other ship may cooperate or defect. Choose between Contest (D) and Yield (C).',
    'Two captains meet over disputed access. Our captain has the actions Contest (D) and Yield (C).',
)
TABLES = (
    'Outcomes for us: CC={R}; CD={S}; DC={T}; DD={P}.',
    'Payoff ledger, our score first: DD={P}, DC={T}, CD={S}, CC={R}.',
    'When we Yield and they Yield: {R}; when we Yield and they Contest: {S}; when we Contest and they Yield: {T}; when both Contest: {P}.',
    'Scoring for our captain: (our D, other C)={T}; (our C, other D)={S}; (our D, other D)={P}; (our C, other C)={R}.',
)
QUESTIONS = (
    'The other captain chooses C with probability {q} and D otherwise. Which action maximizes our expected score? Answer only Contest or Yield.',
    'Our forecast says the opposing crew selects C with probability {q}, otherwise D. Choose the better expected-payoff action: Contest or Yield.',
    'Probability of rival cooperation (C): {q}; all remaining probability belongs to D. For highest expected payoff choose Contest or Yield.',
    'Take the chance of their choosing C to be {q}, with D complementary. Decide which of our two actions has greater expected value: Contest or Yield.',
    'Our estimate of the rival yielding (C) is {q}; otherwise they contest (D). Which of our actions has higher expected reward? Answer Contest or Yield.',
)


def require(ok, reason):
    if not ok:
        raise ValueError('GAMER_V33_' + reason)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return sha_bytes(Path(path).read_bytes())


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def write_new(path, data):
    path = Path(path)
    require(not path.exists(), 'REFUSE_OVERWRITE_' + path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.v33-', delete=False) as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write('\n')
        staging = Path(f.name)
    # Atomic link creation fails closed on an existing destination.
    try:
        os.link(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def exact_teacher(R, S, T, P, q):
    """Oracle is for training labels and sealed evaluation only; NEVER inference."""
    q = Fraction(q)
    contest = q * T + (1 - q) * P
    yield_ = q * R + (1 - q) * S
    require(contest != yield_, 'AMBIGUOUS_TIE')
    return 1 if contest > yield_ else 0


def make_deck(split, seed):
    """Construct only requested split; no holdout generated during training."""
    require(split in ('fit', 'holdout'), 'UNKNOWN_SPLIT')
    variants = TRAIN_VARIANTS if split == 'fit' else TEST_VARIANTS
    rows = []
    for variant in variants:
        rng = random.Random(f'v33-{seed}-{variant}-{split}')
        for pair in range(N_PAIRS):
            # Independently vary both payoff contrasts. Across the two q values,
            # the best action reverses. No constant-choice heuristic can pass.
            R = rng.randrange(-12, 12)
            S = rng.randrange(-12, 12)
            difference = rng.randrange(2, 12)
            direction = 1 if rng.randrange(2) else -1
            T = R + direction * difference
            P = S - direction * difference
            for q in (Fraction(1, 4), Fraction(3, 4)):
                prompt = (' '.join((CONTEXTS[variant // 4],
                    TABLES[variant % 4].format(R=R, S=S, T=T, P=P),
                    QUESTIONS[variant // 4].format(q=str(q)))))
                target = exact_teacher(R, S, T, P, q)
                rows.append({'id': f'{split}.surface{variant:02}.pair{pair:02}.q{q.numerator}',
                             'variant': variant, 'pair': f'{variant:02}:{pair:02}',
                             'prompt': prompt, 'label': target,
                             'signed_margin': str(q * (T - R) + (1 - q) * (P - S))})
    require(len(rows) == len(variants) * N_PAIRS * 2 and
            len({r['id'] for r in rows}) == len(rows) and
            len({r['prompt'] for r in rows}) == len(rows), 'DECK_INTEGRITY')
    for i in range(0, len(rows), 2):
        require(rows[i]['pair'] == rows[i+1]['pair'] and
                rows[i]['label'] != rows[i+1]['label'], 'PAIR_NO_CAUSAL_REVERSAL')
    return rows


def fit_partition(rows):
    # Hold out whole payoff-pairs within each of the 15 TRAIN templates for
    # checkpoint selection. The five TEST templates are not accessed.
    train = [r for r in rows if int(r['pair'].split(':')[1]) < 9]
    dev = [r for r in rows if int(r['pair'].split(':')[1]) >= 9]
    require(len(train) == 270 and len(dev) == 90 and not
            ({r['pair'] for r in train} & {r['pair'] for r in dev}),
            'TRAIN_DEV_PAIR_LEAK')
    return train, dev


def activation(root):
    from main_computer import gamer_numerical_authority as auth
    from tools import nanojev_gamer_v1_v30_live_inference as live
    status = auth.status(root)
    auth.require(status.get('authority') == auth.V30, 'V32_AUTHORITY_NOT_ACTIVE')
    auth._verify_active(status)
    paths = status['paths']
    source = live.load(SimpleNamespace(v27_run=paths['v27_run'],
                                       v29_run=paths['v29_run'], v30_run=paths['v30_run']))
    require(auth.canonical_source_files(source['source_files']) == status['source_files'] and
            source['lineage']['adapter_sha256'] == status['adapter_sha256'],
            'FROZEN_LINEAGE_MISMATCH')
    return {'model_id': source['lineage']['model_id'],
            'v32_state_sha256': sha(auth.state_path(root)),
            'source_files': status['source_files'],
            'base_activation': status['activation_source'],
            'v31_acceptance_sha256': status['evidence']['v31_report_sha256']}


def verify_activation(root, lineage):
    # Re-run active authority verification, not just state-file presence.
    current = activation(root)
    require(current == lineage, 'FROZEN_MODEL_OR_AUTHORITY_CHANGED')
    return current


def capture(model, tok, torch, prompts, *, device, max_tokens=256, batch_size=4, tag='capture'):
    """Inference-only hidden states of RAW T08 prompts, without labels/answers."""
    import numpy as np
    require(0 < len(prompts) <= 1024 and 1 <= batch_size <= 32,
            'INVALID_CAPTURE_ARGUMENTS')
    parts = []
    model.eval()
    for start in range(0, len(prompts), batch_size):
        batch = prompts[start:start + batch_size]
        encoded = tok(batch, return_tensors='pt', padding=True,
                      truncation=False, add_special_tokens=True)
        lengths = encoded['attention_mask'].sum(-1)
        require(bool((lengths <= max_tokens).all()) and
                bool((lengths > 0).all()), 'PROMPT_TOO_LONG_OR_EMPTY')
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with torch.inference_mode():
            out = model(**encoded, output_hidden_states=True, use_cache=False)
            h = out.hidden_states
            require(len(h) >= 20, 'FROZEN_MODEL_TOO_SHALLOW')
            indices = (lengths.to(device) - 1).long()
            positions = torch.arange(len(batch), device=device)
            mask = encoded['attention_mask'].unsqueeze(-1)
            at9 = h[9][positions, indices, :].float()
            at18 = h[18][positions, indices, :].float()
            mean18 = (h[18].float() * mask).sum(1) / lengths.to(device).float().unsqueeze(-1)
            vector = torch.cat((at9, at18, mean18), dim=1)
            vector = torch.nn.functional.normalize(vector, p=2, dim=1)
            require(bool(torch.isfinite(vector).all()), 'NONFINITE_CAPTURE')
            parts.append(vector.cpu().numpy().astype(np.float32))
        if (start + len(batch)) % 32 == 0 or start + len(batch) == len(prompts):
            print(f'GAMER_V33_CAPTURE {tag} {start + len(batch)}/{len(prompts)}', flush=True)
    return np.concatenate(parts, axis=0)


def head(torch, width):
    return torch.nn.Sequential(torch.nn.LayerNorm(width),
                               torch.nn.Linear(width, 96),
                               torch.nn.GELU(),
                               torch.nn.Linear(96, 2))


def train_head(torch, train_x, train_y, dev_x, dev_y, *, seed=33, epochs=160):
    import numpy as np
    require(epochs >= 1 and len(train_x) == 270 and len(dev_x) == 90 and
            train_x.ndim == 2 and dev_x.shape[1] == train_x.shape[1],
            'INVALID_FIT_SHAPES')
    torch.manual_seed(seed)
    train_tensor = torch.from_numpy(train_x.copy()).float()
    dev_tensor = torch.from_numpy(dev_x.copy()).float()
    labels = torch.tensor(train_y, dtype=torch.long)
    dev_labels = torch.tensor(dev_y, dtype=torch.long)
    net = head(torch, train_x.shape[1])
    opt = torch.optim.AdamW(net.parameters(), lr=0.001, weight_decay=0.02)
    best_state = None
    best_score = -1
    best_epoch = 0
    history = []
    for epoch in range(1, epochs + 1):
        net.train()
        # Deterministic, precommitted seed; no test-derived resampling.
        generator = torch.Generator().manual_seed(seed * 10000 + epoch)
        order = torch.randperm(len(labels), generator=generator)
        for indices in order.split(32):
            opt.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(net(train_tensor[indices]), labels[indices])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 2.0)
            opt.step()
        net.eval()
        with torch.inference_mode():
            pred = net(dev_tensor).argmax(-1)
            exact = int((pred == dev_labels).sum().item())
        history.append({'epoch': epoch, 'dev_exact': exact, 'dev_total': len(dev_y)})
        if epoch % 20 == 0 or epoch == epochs:
            print(f'GAMER_V33_TRAIN_EPOCH {epoch}/{epochs} dev_exact={exact}/{len(dev_y)}', flush=True)
        if exact > best_score:
            best_score = exact
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
    require(best_state is not None and best_score >= 0, 'NO_TRAINED_HEAD')
    return best_state, best_epoch, history


def model_context(model_id, device):
    from tools.nanojev_gamer_v1_handoff import model_components
    from tools.nanojev_two_game_spine_smoke import cache_policy
    cache_policy(False)
    torch, tok, model = model_components(model_id, device)
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    return torch, tok, model


def checkpoint(root):
    return Path(root) / 'learned_head.pt'


def training(root, *, project='.', device='cuda', seed=33, epochs=160, batch_size=4):
    import numpy as np
    import torch
    root = Path(root)
    require(not root.exists(), 'OUTPUT_ALREADY_EXISTS')
    require(seed >= 0 and epochs > 0 and 1 <= batch_size <= 32, 'BAD_TRAINING_CONFIG')
    lineage = activation(project)
    train_rows, dev_rows = fit_partition(make_deck('fit', seed))
    # Verify no oracle-derived numeric values are concatenated into the prompt.
    require(all('signed_margin' not in r['prompt'] for r in train_rows + dev_rows),
            'ORACLE_LEAK')
    root.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    t, tok, model = model_context(lineage['model_id'], device)
    try:
        train_x = capture(model, tok, t, [r['prompt'] for r in train_rows],
                          device=device, batch_size=batch_size, tag='train')
        dev_x = capture(model, tok, t, [r['prompt'] for r in dev_rows],
                        device=device, batch_size=batch_size, tag='dev')
    finally:
        del model, tok
        if t.cuda.is_available(): t.cuda.empty_cache()
    state, epoch, history = train_head(t, train_x, [r['label'] for r in train_rows],
                                      dev_x, [r['label'] for r in dev_rows],
                                      seed=seed, epochs=epochs)
    verify_activation(project, lineage)
    manifest = {'schema': SCHEMA, 'status': 'TRAIN_FITTED_NO_TEST_NO_PROMOTION',
                'train_variants': list(TRAIN_VARIANTS), 'sealed_holdout_variants': list(TEST_VARIANTS),
                'seed': seed, 'epochs': epochs, 'best_epoch': epoch,
                'fit_count': len(train_rows), 'dev_count': len(dev_rows),
                'dev_pass': history[epoch-1]['dev_exact'] >= 72,
                'fit_deck_sha256': sha_bytes(canonical(train_rows).encode()),
                'dev_deck_sha256': sha_bytes(canonical(dev_rows).encode()),
                'lineage': lineage, 'feature_width': int(train_x.shape[1]),
                'no_test_used': True, 'head_only': True, 'wide_head_unchanged': True,
                'tactical_v6_unchanged': True, 'inference_uses_oracle': False,
                'head_authority': 'experimental_shadow_only'}
    t.save({'schema': HEAD_SCHEMA, 'state': state, 'manifest_sha256':
            sha_bytes(canonical(manifest).encode())}, checkpoint(root))
    write_new(root / 'train_manifest.json', manifest)
    write_new(root / 'train_history.json', {'history': history})
    result = {'status': 'TRAIN_FITTED_NO_TEST_NO_PROMOTION',
              'best_dev_exact': history[epoch-1]['dev_exact'], 'dev_total': len(dev_rows),
              'fit': len(train_rows), 'holdout_variants_unopened': len(TEST_VARIANTS),
              'checkpoint_sha256': sha(checkpoint(root)), 'output': str(root)}
    write_new(root / 'train_result.json', result)
    return result


def load_head(root, project, *, require_audit=False):
    import torch
    root = Path(root)
    manifest = read(root / 'train_manifest.json')
    require(manifest['schema'] == SCHEMA and manifest['status'] ==
            'TRAIN_FITTED_NO_TEST_NO_PROMOTION' and
            manifest['train_variants'] == list(TRAIN_VARIANTS) and
            manifest['sealed_holdout_variants'] == list(TEST_VARIANTS) and
            manifest['no_test_used'] is True and manifest['head_only'] is True and
            manifest['inference_uses_oracle'] is False and
            manifest['head_authority'] == 'experimental_shadow_only',
            'INVALID_TRAIN_MANIFEST')
    verify_activation(project, manifest['lineage'])
    train_rows, dev_rows = fit_partition(make_deck('fit', manifest['seed']))
    require(sha_bytes(canonical(train_rows).encode()) == manifest['fit_deck_sha256']
            and sha_bytes(canonical(dev_rows).encode()) == manifest['dev_deck_sha256']
            and manifest['fit_count'] == len(train_rows) == 270
            and manifest['dev_count'] == len(dev_rows) == 90,
            'TRAIN_DATA_OR_SPLIT_PROVENANCE_CHANGED')
    recorded = read(root / 'train_result.json')
    require(sha(checkpoint(root)) == recorded['checkpoint_sha256'], 'HEAD_CHECKPOINT_CHANGED')
    data = torch.load(checkpoint(root), weights_only=True, map_location='cpu')
    require(data['schema'] == HEAD_SCHEMA and data['manifest_sha256'] ==
            sha_bytes(canonical(manifest).encode()), 'HEAD_MANIFEST_MISMATCH')
    net = head(torch, manifest['feature_width'])
    net.load_state_dict(data['state'], strict=True)
    net.eval()
    if require_audit:
        report = read(root / 'holdout_acceptance.json')
        require(report.get('status') == 'PASS_SEALED_HOLDOUT_SHADOW_ONLY'
                and report.get('heldout_pass') is True, 'HOLDOUT_NOT_CERTIFIED')
        ledger_path = root / 'holdout_predictions.json'
        require(ledger_path.exists() and
                sha(ledger_path) == report.get('predictions_sha256'),
                'HOLDOUT_EVIDENCE_CHANGED')
        expected_rows = make_deck('holdout', manifest['seed'])
        ledger = read(ledger_path)
        require(isinstance(ledger, list) and len(ledger) == len(expected_rows),
                'HOLDOUT_LEDGER_INCOMPLETE')
        preds = []
        for record, expected in zip(ledger, expected_rows):
            require(isinstance(record, dict) and
                    record.get('id') == expected['id'] and
                    record.get('prompt_sha256') == sha_bytes(expected['prompt'].encode()) and
                    type(record.get('prediction')) is int and
                    record['prediction'] in (0, 1) and
                    record.get('truth') == expected['label'],
                    'HOLDOUT_LEDGER_INVALID')
            preds.append(record['prediction'])
        correct = sum(r['label'] == p for r, p in zip(expected_rows, preds))
        both, total_pairs = paired_accuracy(expected_rows, preds)
        require(correct == report.get('exact') and
                report.get('total') == len(expected_rows) == 120 and
                both == report.get('paired_reversals_exact') and
                total_pairs == report.get('paired_reversals_total') == 60 and
                correct >= 96 and both >= 48 and
                all(sum(r['label'] == p for r, p in zip(expected_rows, preds)
                        if r['variant'] == variant) >= 16
                    for variant in TEST_VARIANTS),
                'HOLDOUT_ACCEPTANCE_REPLAY_FAILED')
        require(report.get('status') == 'PASS_SEALED_HOLDOUT_SHADOW_ONLY' and
                report.get('heldout_pass') is True and
                report.get('head_checkpoint_sha256') == sha(checkpoint(root)) and
                report.get('manifest_sha256') == sha(root / 'train_manifest.json'),
                'HOLDOUT_NOT_CERTIFIED')
    return manifest, net


def predict_features(torch, net, features):
    with torch.inference_mode():
        logits = net(torch.from_numpy(features.copy()).float())
        require(bool(torch.isfinite(logits).all()), 'NONFINITE_LOGITS')
        return logits.argmax(-1).tolist()


def paired_accuracy(rows, predictions):
    by_pair = {}
    for row, pred in zip(rows, predictions):
        by_pair.setdefault(row['pair'], []).append(int(pred == row['label']))
    require(all(len(pair) == 2 for pair in by_pair.values()), 'INCOMPLETE_TEST_PAIRS')
    return sum(all(pair) for pair in by_pair.values()), len(by_pair)


def audit(root, *, project='.', device='cuda', batch_size=4):
    root = Path(root)
    require(not (root / 'holdout_consumed.json').exists() and
            not (root / 'holdout_acceptance.json').exists(), 'HOLDOUT_ALREADY_CONSUMED')
    manifest, net = load_head(root, project)
    require(manifest.get('dev_pass') is True, 'DEV_GATE_NOT_MET_TEST_REMAINS_SEALED')
    # Sentinel comes BEFORE any holdout generation, feature capture or scoring.
    write_new(root / 'holdout_consumed.json', {
        'schema': SCHEMA, 'status': 'HOLDOUT_CONSUMED_NO_SECOND_ATTEMPT',
        'manifest_sha256': sha(root / 'train_manifest.json'),
        'checkpoint_sha256': sha(checkpoint(root)), 'variants': list(TEST_VARIANTS)})
    rows = make_deck('holdout', manifest['seed'])
    from tools.nanojev_gamer_v1_handoff import model_components
    t, tok, model = model_context(manifest['lineage']['model_id'], device)
    try:
        x = capture(model, tok, t, [r['prompt'] for r in rows],
                    device=device, batch_size=batch_size, tag='heldout')
    finally:
        del model, tok
        if t.cuda.is_available(): t.cuda.empty_cache()
    predictions = predict_features(t, net, x)
    correct = sum(int(a['label'] == p) for a, p in zip(rows, predictions))
    pair_correct, n_pairs = paired_accuracy(rows, predictions)
    by_variant = {str(v): {'correct': sum(r['label'] == p for r, p in zip(rows, predictions)
                                             if r['variant'] == v),
                           'total': sum(r['variant'] == v for r in rows)} for v in TEST_VARIANTS}
    count = Counter(r['label'] for r in rows)
    majority = max(count.values())
    # A useful capability requires learning both response directions on novel
    # surfaces, not just always choosing the majority label (50%).
    train_pairs = {r['pair'] for r in make_deck('fit', manifest['seed'])}
    passed = (correct >= 96 and pair_correct >= 48 and
              all(v['correct'] >= 16 for v in by_variant.values()) and
              correct > majority and all(r['pair'] not in train_pairs for r in rows))
    verify_activation(project, manifest['lineage'])
    ledger = [{'id': row['id'], 'prompt_sha256': sha_bytes(row['prompt'].encode()),
               'truth': row['label'], 'prediction': pred}
              for row, pred in zip(rows, predictions)]
    write_new(root / 'holdout_predictions.json', ledger)
    result = {'schema': SCHEMA,
              'predictions_sha256': sha(root / 'holdout_predictions.json'),
              'status': 'PASS_SEALED_HOLDOUT_SHADOW_ONLY' if passed else
                        'FAIL_SEALED_HOLDOUT_NO_PROMOTION',
              'heldout_pass': passed, 'exact': correct, 'total': len(rows),
              'paired_reversals_exact': pair_correct, 'paired_reversals_total': n_pairs,
              'majority_baseline_exact': majority, 'majority_baseline_total': len(rows),
              'per_variant': by_variant, 'head_checkpoint_sha256': sha(checkpoint(root)),
              'manifest_sha256': sha(root / 'train_manifest.json'),
              'holdout_deck_sha256': sha_bytes(canonical(rows).encode()),
              'test_variants': list(TEST_VARIANTS),
              'holdout_used_for_training': False,
              'production_authority_changed': False, 'tactical_v6_replaced': False,
              'limitations': 'One task family (T08 Hawk/Dove), five held-out prompt surfaces. Does not prove arbitrary-game learning, payoff inference from live sensors, or tactical-v6 competence.'}
    write_new(root / 'holdout_acceptance.json', result)
    return result


def infer(root, prompt, *, project='.', device='cuda'):
    require(isinstance(prompt, str) and prompt.strip() and len(prompt) <= 4000,
            'BAD_PROMPT')
    manifest, net = load_head(root, project, require_audit=True)
    t, tok, model = model_context(manifest['lineage']['model_id'], device)
    try:
        x = capture(model, tok, t, [prompt], device=device)
    finally:
        del model, tok
        if t.cuda.is_available(): t.cuda.empty_cache()
    prediction = predict_features(t, net, x)[0]
    verify_activation(project, manifest['lineage'])
    return {'choice': 'Contest' if prediction == 1 else 'Yield',
            'learned': True, 'model': HEAD_SCHEMA,
            'checkpoint_sha256': sha(checkpoint(root)), 'scoring_oracle_used': False,
            'production_authority': False}


def shadow_smoke(run, *, project='.', device='cuda'):
    from main_computer.tactical_ai_service import TacticalAIService
    # New scenario, not used in the training/heldout decks; oracle checks the
    # result only AFTER the live neural prediction has completed.
    prompt = ('A patrol must choose Contest (D) or Yield (C). Our payoffs: '
              'CC=8; CD=1; DC=14; DD=-5. The rival chooses C with '
              'probability 3/4, otherwise D. Which maximizes our expected '
              'score? Answer only Contest or Yield.')
    observation = {'schema': 'game.bridgeCaptainObservation.v1',
                   'captainId': 'captain.beta', 'shipId': 'ship.beta',
                   'mission': 'pursue-main-ship-and-seek-boarding-range',
                   'simulationSeconds': 10.0, 'rangeM': 3000.0,
                   'radialVelocityMps': -2.0, 'targetHullPercent': 100.0,
                   'transporterMaxRangeM': 1500.0,
                   'relativePositionM': [3000.0, 0.0],
                   'relativeVelocityMps': [-2.0, 0.0],
                   'boarding': {'phase': 'idle', 'boarders': 'aboard'}}
    response = TacticalAIService(Path(project)).shadow_gamer_hawk_dove(
        {'schema': 'game.bridgeGamerShadowRequest.v33', 'observation': observation,
         't08Prompt': prompt, 'run': str(Path(run).resolve())}, device=device)
    expected = exact_teacher(8, 1, 14, -5, Fraction(3,4))
    passed = response['t08Decision'] == ('Contest' if expected else 'Yield') and \
             response['actionPublished'] is False and \
             response['captainOrder'] is None and \
             response['suggestedLegalIntent']['maneuver'] == 'approach'
    result = {'status': 'PASS_V33_CAPTAIN_SHADOW_NO_ORDER' if passed else
                        'FAIL_V33_CAPTAIN_SHADOW_NO_ORDER',
              'expected_action': 'Contest' if expected else 'Yield',
              'observed_action': response['t08Decision'],
              'captain_response': response, 'production_modified': False}
    write_new(Path(run) / 'captain_shadow_smoke.json', result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('run', 'train', 'audit', 'status', 'shadow-smoke'))
    p.add_argument('--root', default='.')
    p.add_argument('--run', required=True)
    p.add_argument('--device', default='cuda')
    p.add_argument('--seed', type=int, default=33)
    p.add_argument('--epochs', type=int, default=160)
    p.add_argument('--batch-size', type=int, default=4)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.stage == 'run':
        trained = training(args.run, project=args.root, device=args.device,
                           seed=args.seed, epochs=args.epochs, batch_size=args.batch_size)
        print('GAMER_V33_TRAIN ' + json.dumps(trained, sort_keys=True), flush=True)
        if not read(Path(args.run) / 'train_manifest.json')['dev_pass']:
            result = {'status': 'FAIL_DEV_GATE_HOLDOUT_STILL_SEALED',
                      'dev_correct': trained['best_dev_exact'], 'dev_total': trained['dev_total'],
                      'test_evaluated': False, 'production_modified': False}
        else:
            audited = audit(args.run, project=args.root, device=args.device,
                            batch_size=args.batch_size)
            print('GAMER_V33_HOLDOUT ' + json.dumps(audited, sort_keys=True), flush=True)
            if not audited['heldout_pass']:
                result = audited
            else:
                smoke = shadow_smoke(args.run, project=args.root, device=args.device)
                print('GAMER_V33_CAPTAIN ' + json.dumps(smoke, sort_keys=True), flush=True)
                result = {'status': 'PASS_V33_GAME_LEARNING_SHADOW_ACCEPTANCE'
                          if smoke['status'] == 'PASS_V33_CAPTAIN_SHADOW_NO_ORDER'
                          else 'FAIL_V33_CAPTAIN_SHADOW_ACCEPTANCE',
                          'heldout_exact': audited['exact'],
                          'heldout_total': audited['total'],
                          'paired_reversals_exact': audited['paired_reversals_exact'],
                          'paired_reversals_total': audited['paired_reversals_total'],
                          'captain_shadow_pass': smoke['status'] == 'PASS_V33_CAPTAIN_SHADOW_NO_ORDER',
                          'tactical_v6_authority_changed': False,
                          'v32_numerical_authority_changed': False,
                          'output': str(Path(args.run).resolve())}
                write_new(Path(args.run) / 'operational_acceptance.json', result)
    elif args.stage == 'train':
        result = training(args.run, project=args.root, device=args.device,
                          seed=args.seed, epochs=args.epochs, batch_size=args.batch_size)
    elif args.stage == 'audit':
        result = audit(args.run, project=args.root, device=args.device,
                       batch_size=args.batch_size)
    elif args.stage == 'status':
        from main_computer import gamer_numerical_authority as auth
        result = {'status': 'V33_SHADOW_STATUS', 'v32_authority': auth.status(args.root)['authority'],
                  'train': (read(Path(args.run) / 'train_result.json') if
                            (Path(args.run) / 'train_result.json').exists() else None),
                  'holdout': (read(Path(args.run) / 'holdout_acceptance.json') if
                              (Path(args.run) / 'holdout_acceptance.json').exists() else None),
                  'captain': (read(Path(args.run) / 'captain_shadow_smoke.json') if
                              (Path(args.run) / 'captain_shadow_smoke.json').exists() else None)}
    else:
        result = shadow_smoke(args.run, project=args.root, device=args.device)
    print('GAMER_V33_' + result['status'] + ' ' + json.dumps(result, sort_keys=True), flush=True)
    return 0 if not str(result['status']).startswith('FAIL_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
