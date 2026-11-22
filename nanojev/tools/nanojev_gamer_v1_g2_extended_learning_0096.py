"""G2-0096: extend the demonstrated G2-0095 learning trajectory.

The warmed 0095 head is continued on a larger, sealed-discovery G1-exact
fuzz cohort. Compare ordinary supervision to pair-aligned semantic-surface
consistency from IDENTICAL starting weights and minibatch schedules. An
inherited 0092-B head and optional raw seeded head are training controls.
Finally, compare rank-one / Reptile correction from one frozen, dev-selected
checkpoint. Confirmation is evaluated ONLY after all weights are fixed;
confirmation has been reused since 0093 and does not certify generalization.

Run from main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_extended_learning_0096 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random

import torch
import torch.nn.functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_backbone_first as backbone

SCHEMA = 'nanojev.gamer.graph.g2.extended-fuzz-learning-0096.v1'
NAME = 'oracle-fabric-g2-extended-learning-0096'
SEED = 960096
PRIOR_SEED = 950095
INITIALIZATIONS = ('warm_supervised', 'warm_consistency', 'inherited_consistency')
FUZZ_MODES = ('base', 'renaming', 'role_swap', 'added_distractor', 'action_removal')
INVARIANT_MODES = ('renaming', 'role_swap', 'added_distractor')


def require(ok, msg):
    if not ok:
        raise ValueError('G2_0096_' + msg)


def log(tag, value):
    print(tag + ' ' + json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def _select(atlas_dir, per_family, dev_per_family):
    # The original G2-0095 dev cases were NOT trained on by 0095. Preserve that
    # separation even while enlarging the discovery training cohort.
    prior, _ = old._discovery_selection(atlas_dir, 12, 6, PRIOR_SEED)
    original_train = set(prior['train'])
    original_dev = set(prior['dev'])
    by_base = defaultdict(dict)
    for rec in old.gzlines(atlas_dir / 'discovery' / 'cases.jsonl.gz'):
        by_base[rec['base_id']][rec['mode']] = rec
    pools = defaultdict(list)
    selected_rows = {}
    for bid, modes in by_base.items():
        if not all(mode in modes and modes[mode]['g1_exact'] for mode in old.NEEDED_MODES):
            continue
        if not any(row['arms']['g2_0092_B']['errors'] for row in modes.values()):
            continue
        family = modes['base']['family']
        pools[family].append(bid)
        selected_rows[bid] = [modes[m]['case_id'] for m in FUZZ_MODES
                              if m in modes and modes[m]['g1_exact']]
    train, dev = [], []
    for idx, family in enumerate(atlas.FAMILIES):
        pool = sorted(pools[family])
        old_train = sorted(original_train.intersection(pool))
        require(len(old_train) == 12, 'PRIOR_TRAIN_NOT_REPRODUCIBLE_' + family)
        remaining = [bid for bid in pool if bid not in original_train and bid not in original_dev]
        random.Random(SEED + idx).shuffle(remaining)
        need = per_family - len(old_train)
        require(need >= 0 and len(remaining) >= need + dev_per_family,
                'INSUFFICIENT_NEW_DISCOVERY_CASES_' + family)
        train.extend(old_train + remaining[:need])
        dev.extend(remaining[need:need + dev_per_family])
    require(not (set(train) & set(dev) or set(train) & original_dev or
                 set(dev) & original_dev), 'DISCOVERY_SPLIT_LEAK')
    train_cases = [cid for bid in train for cid in selected_rows[bid]]
    dev_cases = [cid for bid in dev for cid in selected_rows[bid]]
    return {'train_bases': train, 'dev_bases': dev,
            'train_ids': train_cases, 'dev_ids': dev_cases,
            'excluded_0095_dev_bases': sorted(original_dev),
            'retained_0095_train_bases': sorted(original_train),
            'prior_dev_never_used_as_training': True}


def _identity(item):
    """Only align G1-observed objects to stable authored IDs for loss construction.

    The model still receives only frozen G1 representations, not oracle nodes.
    """
    keys = {(n['kind'], tuple(n['span'])): str(n['id'])
            for n in item['row']['graph']['nodes'] if 'span' in n}
    result = {}
    for idx, (a, b, kind) in enumerate(item['objects']):
        uid = keys.get((kind, (a, b)))
        if uid is not None:
            require(uid not in result, 'DUPLICATE_STABLE_AUTHOR_ID')
            result[uid] = idx
    return result


def _consistency_pair_loss(item_a, logits_a, item_b, logits_b, *, max_negatives):
    """Preserve the relation-margin surface only for stable, gold-equal pairs.

    Role swaps can change meaning; do NOT constrain pairs whose gold label has
    changed. Negative pairs are capped to prevent background no-edge dominance.
    """
    ia, ib = _identity(item_a), _identity(item_b)
    shared = sorted(set(ia).intersection(ib))
    positive, negative = [], []
    for source in shared:
        for target in shared:
            if source == target:
                continue
            a = (ia[source], ia[target])
            b = (ib[source], ib[target])
            label_a = int(item_a['targets'][a])
            label_b = int(item_b['targets'][b])
            if label_a != label_b:
                continue
            if label_a:
                positive.append((a, b))
            else:
                # Choose hard negatives based on the detached current margin.
                margin = float((logits_a[a][1:] - logits_a[a][0]).detach().max())
                negative.append((-margin, source, target, a, b))
    negative.sort()
    pairs = positive + [(a, b) for _, _, _, a, b in negative[:max_negatives]]
    if not pairs:
        return None, {'shared_pairs': 0, 'positive_pairs': 0}
    a_i = [a[0] for a, _ in pairs]
    a_j = [a[1] for a, _ in pairs]
    b_i = [b[0] for _, b in pairs]
    b_j = [b[1] for _, b in pairs]
    ma = logits_a[a_i, a_j, 1:] - logits_a[a_i, a_j, 0:1]
    mb = logits_b[b_i, b_j, 1:] - logits_b[b_i, b_j, 0:1]
    # Symmetric objective: do not treat either fuzz variant as a fixed teacher.
    return F.smooth_l1_loss(ma, mb), {
        'shared_pairs': len(pairs), 'positive_pairs': len(positive),
        'negative_pairs': len(pairs) - len(positive)}


def _example_loss(logits, item, positive_cap):
    gold = item['targets'].to(logits.device)
    valid = ~torch.eye(len(gold), dtype=torch.bool, device=logits.device)
    labels = gold[valid]
    scores = logits[valid]
    npos = int((labels > 0).sum())
    nneg = int((labels == 0).sum())
    pos_weight = min(positive_cap, max(2., .5 * nneg / npos)) if npos else 1.
    weights = torch.where(labels > 0, pos_weight, 1.).to(scores.dtype)
    return (F.cross_entropy(scores, labels, reduction='none') * weights).sum() / weights.sum()


def _save_head(head, base, *, parent_hash, frozen_hash, start_hash, phase, step):
    base.mkdir(parents=True, exist_ok=False)
    dest = base / cur.HEAD_FILE
    save_file({k: v.detach().cpu().contiguous() for k, v in head.state_dict().items()}, str(dest))
    out = {'schema': SCHEMA, 'phase': phase, 'step': step,
           'head_sha256': old.digest(dest), 'trainable_sha256': old._trainable_hash(head),
           'frozen_non_G2_sha256': old._frozen_hash(head),
           'parent_0092_B_sha256': parent_hash, 'initial_trainable_sha256': start_hash,
           'g3_training_unlocked': False, 'promotion_allowed': False}
    require(out['frozen_non_G2_sha256'] == frozen_hash, 'FROZEN_WEIGHT_MUTATED')
    old.write(base / 'manifest.json', out)
    return out


def _load_state_to_head(head, checkpoint, *, frozen_hash, expected_hash=None):
    require(Path(checkpoint).is_file(), 'START_CHECKPOINT_MISSING')
    if expected_hash is not None:
        require(old.digest(checkpoint) == expected_hash, 'START_CHECKPOINT_DRIFT')
    data = load_file(str(checkpoint), device='cpu')
    head.load_state_dict(data, strict=True)
    old._mutable(head)
    require(old._frozen_hash(head) == frozen_hash, 'NON_G2_CHECKPOINT_DRIFT')
    return old._trainable_hash(head)


def _train(head, groups, dev_items, spec, device, cfg, name, dest, *, use_consistency,
           parent_hash, frozen_hash, step_count=None):
    """Identical scheduled minibatch draws across arms, with frozen G1."""
    count = step_count if step_count is not None else cfg.steps
    head.eval()  # Differentiation remains enabled; inference geometry is stable.
    output = dest / name
    output.mkdir(exist_ok=False)
    train = [item for bid in sorted(groups) for item in groups[bid].values()]
    base_ids = sorted(groups)
    require(len(base_ids) >= cfg.batch_groups, 'INSUFFICIENT_GROUPS')
    init_hash = old._trainable_hash(head)
    optimizer = torch.optim.AdamW([p for _, p in old._mutable(head)], lr=cfg.lr,
                                  weight_decay=cfg.weight_decay)
    rng = random.Random(cfg.seed + 200)
    turns = list(base_ids)
    rng.shuffle(turns)
    cursor = 0
    best = None
    best_dir = None
    history = output / 'history.jsonl'
    for step in range(1, count + 1):
        if cursor + cfg.batch_groups > len(turns):
            rng.shuffle(turns)
            cursor = 0
        ids = turns[cursor: cursor + cfg.batch_groups]
        cursor += len(ids)
        # Cosine decay with a nonzero floor, identical across arms.
        phase = (step - 1) / max(1, count - 1)
        lr = cfg.lr * (.2 + .8 * .5 * (1. + math.cos(math.pi * phase)))
        optimizer.param_groups[0]['lr'] = lr
        optimizer.zero_grad(set_to_none=True)
        total = torch.zeros((), device=device)
        total_ce = 0.
        total_consistency = 0.
        checked_pairs = 0
        for bid in ids:
            case = groups[bid]
            # Train every available exact-G1 fuzz variant, not just the base.
            logits = {mode: old.structural._forward(head, case[mode], device)
                      for mode in FUZZ_MODES if mode in case}
            ce = torch.stack([_example_loss(value, case[mode], cfg.positive_cap)
                              for mode, value in logits.items()]).mean()
            total = total + ce / len(ids)
            total_ce += float(ce.detach()) / len(ids)
            if use_consistency:
                matches = []
                for mode in INVARIANT_MODES:
                    if mode not in logits:
                        continue
                    loss, info = _consistency_pair_loss(case['base'], logits['base'],
                        case[mode], logits[mode], max_negatives=cfg.max_negatives)
                    if loss is not None:
                        matches.append(loss)
                        checked_pairs += info['shared_pairs']
                if matches:
                    smooth = torch.stack(matches).mean()
                    total = total + cfg.consistency_weight * smooth / len(ids)
                    total_consistency += float(smooth.detach()) / len(ids)
        require(bool(torch.isfinite(total).item()), 'NONFINITE_LEARNING_OBJECTIVE')
        total.backward()
        grad = torch.nn.utils.clip_grad_norm_([p for _, p in old._mutable(head)], 1.)
        require(bool(torch.isfinite(grad).item()), 'NONFINITE_LEARNING_GRADIENT')
        optimizer.step()
        if step == 1 or step % cfg.eval_every == 0 or step == count:
            with torch.no_grad():
                tr, _ = old._score(head, train, spec, device)
                dv, _ = old._score(head, dev_items, spec, device)
            # Development is allowed to choose a checkpoint, but never drives
            # a weight update. Confirmation is inaccessible during training.
            metric = dv['total']['edge_f1']
            quality = (metric, dv['total']['exact'])
            updated = best is None or quality > best
            entry = {'arm': name, 'step': step, 'train': tr, 'dev': dv,
                     'trainable_sha256': old._trainable_hash(head),
                     'learning_rate': lr, 'ce': total_ce,
                     'consistency': total_consistency,
                     'aligned_pairs': checked_pairs,
                     'uses_consistency': use_consistency, 'best_so_far': updated,
                     'selection_uses_development': True,
                     'confirmation_seen': False}
            with history.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, sort_keys=True, allow_nan=False) + '\n')
            if updated:
                best = quality
                if best_dir is not None:
                    # Keep the previous checkpoint for provenance / trajectory.
                    pass
                best_dir = output / ('milestone-%05d' % step)
                best_manifest = _save_head(head, best_dir,
                    parent_hash=parent_hash, frozen_hash=frozen_hash,
                    start_hash=init_hash, phase='SELECTED_BY_DISCOVERY_DEV', step=step)
            log('G2_0096_LEARN', {'arm': name, 'step': step, 'lr': round(lr, 8),
                'train_f1': round(tr['total']['edge_f1'], 5),
                'dev_f1': round(metric, 5), 'dev_exact': dv['total']['exact'],
                'best_step': int(best_dir.name.rsplit('-', 1)[-1]),
                'consistency_loss': round(total_consistency, 6),
                'aligned_pairs': checked_pairs})
    final_dir = output / 'final'
    final_manifest = _save_head(head, final_dir, parent_hash=parent_hash,
        frozen_hash=frozen_hash, start_hash=init_hash,
        phase='FINAL_TRAINING_STEP', step=count)
    # Use the best development checkpoint as the frozen test candidate.
    selected_sha = _load_state_to_head(head, best_dir / cur.HEAD_FILE,
        frozen_hash=frozen_hash, expected_hash=best_manifest['head_sha256'])
    return {'arm': name, 'steps': count, 'uses_consistency': use_consistency,
            'initial_sha256': init_hash, 'best_checkpoint': str(best_dir),
            'best_step': int(best_dir.name.rsplit('-', 1)[-1]),
            'best_dev_f1': best[0], 'best_dev_exact': best[1],
            'selected_trainable_sha256': selected_sha,
            'final_trainable_sha256': final_manifest['trainable_sha256'],
            'final_checkpoint_sha256': final_manifest['head_sha256'],
            'selected_checkpoint_sha256': best_manifest['head_sha256'],
            'history_sha256': old.digest(history),
            'selection_uses_only_discovery_dev': True}


def run(*, root=old.ROOT_DEFAULT, output=None, device='cuda', seed=SEED,
        train_bases=36, dev_bases=12, steps=1600, eval_every=200,
        batch_groups=2, lr=0.0005, weight_decay=0., positive_cap=12.,
        consistency_weight=0.12, max_negatives=24, editor_episodes=40,
        editor_methods=('reptile', 'binding_rank_one'),
        run_fresh_restart=False, confirmation_bases=300):
    require(device == 'cpu' or (str(device).startswith('cuda') and torch.cuda.is_available()),
            'CUDA_NOT_AVAILABLE')
    require(12 <= train_bases <= 100 and dev_bases >= 1 and 1 <= steps <= 20000 and
            1 <= eval_every <= steps and 1 <= batch_groups <= 16 and
            0. < lr <= .01 and 0. <= weight_decay <= 1. and 0. <= consistency_weight <= 10. and
            0 <= max_negatives <= 256 and 0 <= editor_episodes <= 2000 and
            all(m in ('reptile', 'binding_rank_one') for m in editor_methods), 'BAD_CONFIG')
    require(confirmation_bases == 0 or 1 <= confirmation_bases <= 300,
            'BAD_CONFIRMATION_BUDGET')
    cfg = argparse.Namespace(root=root, device=device, seed=seed, train_bases=train_bases,
        dev_bases=dev_bases, steps=steps, eval_every=eval_every,
        batch_groups=batch_groups, lr=lr, weight_decay=weight_decay,
        positive_cap=positive_cap, consistency_weight=consistency_weight,
        max_negatives=max_negatives, editor_episodes=editor_episodes,
        editor_methods=tuple(editor_methods), run_fresh_restart=run_fresh_restart,
        confirmation_bases=confirmation_bases)
    root, atlas_dir, commit, source_dir = old._source(root)
    prior_root = root / old.NAME
    prior_checkpoint = prior_root / 'fresh-bootstrap-checkpoint' / cur.HEAD_FILE
    prior_manifest_path = prior_root / 'fresh-bootstrap-checkpoint' / 'manifest.json'
    prior_lock_path = prior_root / 'lock.json'
    require(prior_manifest_path.is_file() and prior_lock_path.is_file(),
            '0095_SUCCESSFUL_RUN_REQUIRED')
    prior_manifest = old.read(prior_manifest_path)
    prior_lock = old.read(prior_lock_path)
    require(prior_manifest['phase'] == 'COMMON_FRESH_G2_BOOTSTRAP_NOT_EDITOR' and
            prior_manifest['checkpoint_sha256'] == old.digest(prior_checkpoint) and
            prior_lock['shared_bootstrap_checkpoint_sha256'] == old.digest(prior_checkpoint),
            '0095_BOOTSTRAP_CHECKPOINT_DRIFT')
    require(prior_manifest['g3_unlocked'] is False and prior_lock['g3_unlocked'] is False,
            '0095_UNEXPECTED_G3_PROMOTION')
    champion_name = old.ARM_FRESH + '__reptile'
    champion_path = prior_root / champion_name / 'checkpoint' / cur.HEAD_FILE
    require(champion_name in prior_lock['checkpoints'] and champion_path.is_file() and
            old.digest(champion_path) == prior_lock['checkpoints'][champion_name],
            '0095_BEST_REPTILE_CHECKPOINT_DRIFT')
    dest = (Path(output) if output else root / NAME).resolve()
    require(dest.parent.is_dir() and not dest.exists(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    selection = _select(atlas_dir, train_bases, dev_bases)
    train_rows = old._get_rows(atlas_dir, set(selection['train_ids']), split='discovery')
    dev_rows = old._get_rows(atlas_dir, set(selection['dev_ids']), split='discovery')
    dest.mkdir(exist_ok=False)
    old.write(dest / 'provenance.json', {'schema': SCHEMA, 'runner_sha256': old.digest(__file__),
        'prior_0095_checkpoint_sha256': old.digest(prior_checkpoint),
        'prior_0095_reptile_checkpoint_sha256': old.digest(champion_path),
        'source_0093_discovery_lock_sha256': old.digest(atlas_dir / 'discovery' / 'lock.json'),
        'source_0092_B_checkpoint_sha256': old.digest(source_dir / cur.HEAD_FILE),
        'selection': selection, 'config': vars(cfg),
        'confirmation_reused_after_0095': True, 'independent_certification': False,
        'g3_unlocked': False})
    torch.manual_seed(seed)
    random.seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    paths = atlas._roots(root)
    parent_record, _ = hard._verify_trained(paths, paths['parent_0087'])
    network, tok = backbone.model_components(parent_record['model_id'], paths['adapter'], device)
    require(all(not p.requires_grad for p in network.parameters()), 'G1_BACKBONE_TRAINABLE')
    composite = g1native.load_composite(paths['composite'], device=device)
    log('G2_0096_CAPTURE', {'training_cases': len(train_rows),
                           'development_cases': len(dev_rows),
                           'training_bases': len(selection['train_bases'])})
    train_items = old._capture(network, tok, composite, train_rows, device, must_exact=True)
    dev_items = old._capture(network, tok, composite, dev_rows, device, must_exact=True)
    groups = old._group(train_items)
    for bid in selection['train_bases']:
        require(set(old.NEEDED_MODES) <= set(groups[bid]), 'LOST_FUZZ_CASE')
    spec = commit['lineage']['decoder']
    parent_hash = old.digest(source_dir / cur.HEAD_FILE)
    reference, template_manifest = cur._load_checkpoint(source_dir, device=device)
    old._mutable(reference)
    frozen = old._frozen_hash(reference)
    require(prior_manifest['frozen_non_G2_sha256'] == frozen,
            '0095_G1_OR_PARENT_CHANGED')
    head_init, _ = cur._load_checkpoint(source_dir, device=device)
    old._mutable(head_init)
    warm_hash = _load_state_to_head(head_init, prior_checkpoint,
        frozen_hash=frozen, expected_hash=prior_manifest['checkpoint_sha256'])
    require(warm_hash == prior_manifest['warmed_trainable_hash'], '0095_WARM_TRAINABLE_DRIFT')
    warm_state = old._weights(head_init)
    _load_state_to_head(head_init, champion_path, frozen_hash=frozen,
                        expected_hash=prior_lock['checkpoints'][champion_name])
    champion_state = old._weights(head_init)
    champion_hash = old._trainable_hash(head_init)
    unedited_warm, _ = cur._load_checkpoint(source_dir, device=device)
    old._mutable(unedited_warm)
    old._restore(unedited_warm, warm_state)
    outcomes, heads = {}, {}
    trajectories = [('warm_supervised', 'warm', False),
                    ('warm_consistency', 'warm', True),
                    ('champion_consistency', 'champ', True),
                    ('inherited_consistency', 'parent', True)]
    if run_fresh_restart:
        trajectories.append(('raw_fresh_consistency', 'raw', True))
    for name, start, consistency in trajectories:
        head, _ = cur._load_checkpoint(source_dir, device=device)
        old._mutable(head)
        if start == 'warm':
            old._restore(head, warm_state)
            require(old._trainable_hash(head) == warm_hash, 'WARM_ARMS_START_DIFFERENT')
        elif start == 'champ':
            old._restore(head, champion_state)
            require(old._trainable_hash(head) == champion_hash,
                    'CHAMPION_START_MISMATCH')
        elif start == 'raw':
            old._seed_cutover(head, PRIOR_SEED)
        require(old._frozen_hash(head) == frozen, 'G1_CHANGED_BEFORE_TRAINING')
        log('G2_0096_ARM_START', {'arm': name, 'initial_sha256': old._trainable_hash(head),
            'frozen_sha256': frozen, 'consistency': consistency})
        outcome = _train(head, groups, dev_items, spec, device, cfg, name, dest,
            use_consistency=consistency, parent_hash=parent_hash, frozen_hash=frozen)
        outcomes[name] = outcome
        heads[name] = head
    if editor_episodes:
        base = heads['warm_consistency']
        selected = old._weights(base)
        for technique in editor_methods:
            name = 'edited_warm_consistency__' + technique
            head, _ = cur._load_checkpoint(source_dir, device=device)
            old._mutable(head)
            old._restore(head, selected)
            require(old._trainable_hash(head) == outcomes['warm_consistency']['selected_trainable_sha256'],
                    'EDITOR_START_MISMATCH')
            edit_cfg = argparse.Namespace(seed=seed, episodes=editor_episodes,
                eval_every=min(20, editor_episodes), inner_lr=.0002,
                inner_steps=3, meta_rate=.5, salient_k=24)
            log('G2_0096_EDITOR_START', {'arm': name, 'from': 'warm_consistency',
                'initial_sha256': old._trainable_hash(head)})
            result = old._fit(head, groups, dev_items, spec, device, edit_cfg,
                              name, dest, technique)
            checkpoint = _save_head(head, dest/name/'checkpoint',
                parent_hash=parent_hash, frozen_hash=frozen,
                start_hash=outcomes['warm_consistency']['selected_trainable_sha256'],
                phase='POST_TRAINING_NEUROEDIT', step=editor_episodes)
            outcomes[name] = {'arm': name, 'edited_from': 'warm_consistency',
                              'editor': technique, 'episodes': editor_episodes,
                              'accepted': result['accepted'],
                              'rejected': result['rejected'],
                              'checkpoint_sha256': checkpoint['head_sha256'],
                              'history_sha256': result['history_sha256']}
            heads[name] = head
    require(old.digest(source_dir/cur.HEAD_FILE) == parent_hash,
            'PARENT_CHECKPOINT_MUTATED')
    heads['control_0095_warm_400']=unedited_warm
    heads['control_0092_B']=reference
    evaluation = old._confirmation(root, atlas_dir, network, tok, composite,
        heads, spec, device, confirmation_bases)
    summary = {k: {'overall': v['overall'], 'by_family': v['by_family'], 'fuzz': v['fuzz']}
               for k, v in evaluation['results'].items()}
    report = {'schema': SCHEMA,
              'status': 'G2_0096_EXTENDED_LEARNING_EVALUATED_NOT_CERTIFIED',
              'provenance_sha256': old.digest(dest/'provenance.json'),
              'models': outcomes, 'confirmation': evaluation,
              'parent_unchanged': True, 'frozen_non_G2_sha256': frozen,
              'g3_unlocked': False, 'production_promotion': False,
              'confirmation_is_reused_not_blind': True}
    old.write(dest/'report.json', report)
    old.write(dest/'lock.json', {'schema': SCHEMA,
        'report_sha256': old.digest(dest/'report.json'),
        'provenance_sha256': report['provenance_sha256'],
        'parent_checkpoint_sha256': parent_hash, 'g3_unlocked': False})
    log('G2_0096_RESULT', {'status': report['status'], 'confirmation': {
        k: {'overall': v['overall'], 'by_family': v['by_family']} for k, v in summary.items()},
        'parent_unchanged': True, 'g3_unlocked': False})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='run', choices=('run',))
    parser.add_argument('--root', default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--train-bases', type=int, default=36,
                        help='G1-exact base graphs PER family, including 0095 train')
    parser.add_argument('--dev-bases', type=int, default=12,
                        help='Disjoint new G1-exact development bases PER family')
    parser.add_argument('--steps', type=int, default=1600)
    parser.add_argument('--eval-every', type=int, default=200)
    parser.add_argument('--batch-groups', type=int, default=2)
    parser.add_argument('--lr', type=float, default=.0005)
    parser.add_argument('--weight-decay', type=float, default=0.)
    parser.add_argument('--positive-cap', type=float, default=12.)
    parser.add_argument('--consistency-weight', type=float, default=.12)
    parser.add_argument('--max-negatives', type=int, default=24)
    parser.add_argument('--editor-episodes', type=int, default=40)
    parser.add_argument('--editor-methods', default='reptile,binding_rank_one')
    parser.add_argument('--run-fresh-restart', action='store_true')
    parser.add_argument('--confirmation-bases', type=int, default=300,
                        help='0 means all 300 atlas confirmation bases')
    args = parser.parse_args(argv)
    config = vars(args)
    config['editor_methods'] = tuple(x.strip() for x in config['editor_methods'].split(',') if x.strip())
    config.pop('command')
    return run(**config)


if __name__ == '__main__':
    main()
