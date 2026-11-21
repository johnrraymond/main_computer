"""G2-0095: global-preservation neuroediting with seeded-cutover bootstrap.

Run inside nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_fuzz_and_correct_0095 run --device cuda

A: reset OPTIMIZER and replay error-guided meta-learning from frozen 0092-B G2.
B: identical frozen non-G2 weights, deterministic seeded fresh G2 trainable layers.
The freshly initialized G2 first receives one common supervised fuzz-cohort
bootstrap; all five editors start from that IDENTICAL bootstrap checkpoint.
Each contender then attempts edits with global preservation of previously correct
TRAINING decisions, not merely the current local neighborhood.
Original 0093 confirmation gold is opened ONLY after both arms finish training.
All outputs are experimental. Parent weights are never written. No G3 promotion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import json
import math
from pathlib import Path
import random

import torch
import torch.nn.functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, token_span_char_bounds
from tools import nanojev_gamer_v1_g2_neuroedit_0094 as neuroedit

SCHEMA = 'nanojev.gamer.graph.g2.fuzz-correct-preserving-neuroediting-0095.v1'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
NAME = 'oracle-fabric-g2-fuzz-and-correct-0095'
SEED = 950095
ARM_RESET = 'A_reset_optimizer_from_0092_B'
ARM_FRESH = 'B_fresh_seeded_G2_cutover'
ARMS = (ARM_RESET, ARM_FRESH)
TECHNIQUES = neuroedit.TECHNIQUES
TRAIN_MODES = ('base', 'renaming', 'role_swap', 'action_removal', 'added_distractor')
NEEDED_MODES = ('base', 'renaming', 'role_swap')


def require(ok, reason):
    if not ok:
        raise ValueError('G2_0095_' + reason)


def digest(path):
    return atlas.sha(path)


def read(path):
    return atlas.read(path)


def write(path, value):
    atlas.write(path, value)


def lines(path):
    with Path(path).open(encoding='utf-8') as src:
        for line in src:
            if line.strip():
                yield json.loads(line)


def gzlines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as src:
        for line in src:
            if line.strip():
                yield json.loads(line)


def _source(root):
    root = Path(root).resolve()
    atlas_dir = root / atlas.OUTPUT_NAME
    require(atlas_dir.is_dir(), '0093_ATLAS_NOT_FOUND')
    commit = read(atlas_dir / 'seal' / 'commit.json')
    require(commit['schema'] == atlas.SCHEMA and commit['state'] == 'GOLD_COMMITTED_BEFORE_INFERENCE',
            '0093_COMMIT_INVALID')
    require(digest(atlas_dir / 'seal' / 'prompts.jsonl') == commit['prompt_sha256'],
            '0093_PROMPTS_CHANGED')
    lock = read(atlas_dir / 'discovery' / 'lock.json')
    for key, file in [('cases_sha256', 'cases.jsonl.gz'),
                      ('hypotheses_sha256', 'hypotheses.json'),
                      ('summary_sha256', 'summary.json'),
                      ('counterfactual_sha256', 'counterfactuals.jsonl')]:
        require(digest(atlas_dir / 'discovery' / file) == lock[key], '0093_DISCOVERY_DRIFT_' + key)
    require(lock['g3_unlocked'] is False, '0093_G3_ALREADY_UNLOCKED')
    for split in ('discovery', 'confirmation'):
        filename = atlas_dir / 'seal' / ('answers.' + split + '.SECRET.jsonl')
        require(filename.is_file() and digest(filename) == commit['answers_sha256'][split],
                '0093_ANSWER_SEAL_DRIFT_' + split)
    source_dir = root / 'oracle-fabric-g2-mixed-family-fuzz-0092' / 'B_from_0090' / 'stage-4' / 'checkpoint'
    head_path = source_dir / cur.HEAD_FILE
    require(source_dir.is_dir() and digest(head_path) == commit['lineage']['head_0092_B_sha256'],
            '0092_B_HEAD_DRIFT')
    return root, atlas_dir, commit, source_dir


def _discovery_selection(atlas_dir, n_train, n_dev, seed):
    require(n_train > 0 and n_dev > 0, 'EMPTY_COHORT')
    by_base = defaultdict(dict)
    for rec in gzlines(atlas_dir / 'discovery' / 'cases.jsonl.gz'):
        require(rec['split'] == 'discovery', 'DISCOVERY_MISLABELED')
        by_base[rec['base_id']][rec['mode']] = rec
    grouped = {fam: [] for fam in atlas.FAMILIES}
    for bid, modes in by_base.items():
        if not all(m in modes and modes[m]['g1_exact'] for m in NEEDED_MODES):
            continue
        family = modes['base']['family']
        require(all(r['family'] == family for r in modes.values()), 'FAMILY_DRIFT')
        # Baseline errors are a criterion of interest, not gold from the confirmation split.
        failures = sum(len(r['arms']['g2_0092_B']['errors']) for r in modes.values())
        if failures:
            grouped[family].append((bid, failures))
    selected = {'train': [], 'dev': []}
    for offset, family in enumerate(atlas.FAMILIES):
        ids = sorted(grouped[family])
        random.Random(seed + offset).shuffle(ids)
        require(len(ids) >= n_train + n_dev, 'INSUFFICIENT_G1_EXACT_' + family)
        selected['train'].extend(bid for bid, _ in ids[:n_train])
        selected['dev'].extend(bid for bid, _ in ids[n_train:n_train + n_dev])
    require(set(selected['train']).isdisjoint(selected['dev']), 'TRAIN_DEV_LEAK')
    ids = set(selected['train']) | set(selected['dev'])
    selection = {}
    for bid in ids:
        rows = by_base[bid]
        selection[bid] = [rows[m]['case_id'] for m in TRAIN_MODES
                          if m in rows and rows[m]['g1_exact']]
        require(set(NEEDED_MODES) <= {rows[m]['mode'] for m in TRAIN_MODES
                                      if m in rows and rows[m]['g1_exact']},
                'REQUIRED_FUZZ_MISSING')
    return selected, selection


def _get_rows(atlas_dir, ids, *, split):
    """Read only the requested seal. Never touch confirmation gold during fitting."""
    public = {r['case_id']: r for r in lines(atlas_dir / 'seal' / 'prompts.jsonl')
              if r['split'] == split and r['case_id'] in ids}
    answers = {r['case_id']: r for r in lines(
        atlas_dir / 'seal' / ('answers.' + split + '.SECRET.jsonl'))
        if r['case_id'] in ids}
    require(set(public) == set(answers) == set(ids), 'SELECTED_ROW_MISSING_' + split)
    output = []
    for cid in sorted(ids):
        p, a = public[cid], answers[cid]
        require(p['prompt_sha256'] == a['prompt_sha256'] == atlas.digest(p['prompt']) and
                p['base_id'] == a['base_id'] and p['family'] == a['family'],
                'SOURCE_IDENTITY_DRIFT')
        row = {'prompt': p['prompt'], 'graph': a['graph'], 'object_spans': a['gold_objects'],
               'family': p['family'], 'split': 'train' if split == 'discovery' else 'challenge',
               'annotation_provenance': 'AUTHOR_MARKED_IN_SOURCE_GENERATOR',
               'base_id': p['base_id'], 'case_id': cid, 'mode': p['mode'],
               'original_split': split}
        output.append(row)
    return output


def _capture(network, tok, composite, rows, device, *, must_exact):
    """Frozen G1 feature capture. Never substitute gold nodes for predicted G1."""
    items = []
    for index, row in enumerate(rows, 1):
        item = structural._item_capture(network, tok, composite, row, device)
        objs = []
        for span in item['spans']:
            bounds = token_span_char_bounds(span, item['prepared'])
            require(bounds is not None, 'UNMAPPED_G1_SPAN')
            objs.append([bounds[0], bounds[1], span['kind']])
        item['objects'] = objs
        expected = {(n['kind'], tuple(n['span'])) for n in row['graph']['nodes']
                    if 'span' in n}
        observed = {(k, (a,b)) for a,b,k in objs}
        item['g1_exact'] = observed == expected
        require(not must_exact or item['g1_exact'], 'RECURRENT_G1_EXACT_DRIFT_' + row['case_id'])
        item['full_gold'] = structural.parent.previous._gold_edges(row['graph'])
        if item['g1_exact']:
            actual_gold = {structural.previous.format_edge(objs, i, j, int(item['targets'][i,j]))
                           for i in range(len(objs)) for j in range(len(objs))
                           if i != j and int(item['targets'][i,j]) > 0}
            require(actual_gold == item['full_gold'],
                    'GOLD_PAIR_MISALIGNMENT_' + row['case_id'])
        items.append(item)
        if index == 1 or index % 25 == 0 or index == len(rows):
            print('G2_0095_CAPTURE ' + json.dumps({'done': index, 'of': len(rows),
                    'exact_g1': sum(it['g1_exact'] for it in items)}, sort_keys=True), flush=True)
    return items


def _group(items):
    groups = defaultdict(dict)
    for item in items:
        groups[item['row']['base_id']][item['row']['mode']] = item
    return groups


def _mutable(head):
    structural._mutable(head)
    permitted = cur.STAGE_MODULES['G2']
    active = [(name, p) for name, p in head.named_parameters() if p.requires_grad]
    require(active and all(any(n.startswith(prefix) for prefix in permitted) for n,_ in active),
            'G2_SCOPE_INVALID')
    return active


def _seed_cutover(head, seed):
    """CPU-generator deterministic initialization; no G1 or G3+ weight reset."""
    generator = torch.Generator(device='cpu').manual_seed(seed)
    names = ('pair_projection', 'binding')
    modules = [getattr(head, name) for name in names]
    if head.interface_couplers is not None:
        modules.append(head.interface_couplers['G1_G2'].delta)
    with torch.no_grad():
        for module in modules:
            fan_in = module.weight.shape[1]
            w = torch.empty(tuple(module.weight.shape), dtype=torch.float32, device='cpu')
            torch.nn.init.kaiming_uniform_(w, a=math.sqrt(5), generator=generator)
            module.weight.copy_(w.to(module.weight))
            if module.bias is not None:
                bound = 1 / math.sqrt(fan_in)
                b = torch.empty(tuple(module.bias.shape), dtype=torch.float32, device='cpu')
                b.uniform_(-bound, bound, generator=generator)
                module.bias.copy_(b.to(module.bias))
    return _trainable_hash(head)


def _trainable_hash(head):
    digestor = hashlib.sha256()
    for name, p in head.named_parameters():
        if p.requires_grad:
            digestor.update(name.encode('utf-8'))
            digestor.update(p.detach().cpu().contiguous().numpy().tobytes())
    return digestor.hexdigest()


def _frozen_hash(head):
    result = hashlib.sha256()
    for name, value in head.state_dict().items():
        if any(name.startswith(prefix) for prefix in cur.STAGE_MODULES['G2']):
            continue
        result.update(name.encode('utf-8'))
        result.update(value.detach().cpu().contiguous().numpy().tobytes())
    return result.hexdigest()


def _decode_matrix(item, logits, spec):
    """The actual structured graph answer projected to the pair-label grid."""
    surface = structural._surface_from_logits(item['objects'], logits)
    selected = structural.previous.decode(surface, spec)
    n = len(item['objects'])
    matrix = torch.zeros((n,n), dtype=torch.long, device='cpu')
    lookup = {(k, (a,b)): i for i,(a,b,k) in enumerate(item['objects'])}
    for a, aa, b, bb, binding in selected:
        require((a,aa) in lookup and (b,bb) in lookup and binding in BIND_CLASSES,
                'DECODER_EMITTED_UNKNOWN_EDGE')
        i,j = lookup[(a,aa)], lookup[(b,bb)]
        require(i != j, 'DECODER_SELF_EDGE')
        matrix[i,j] = BIND_CLASSES.index(binding)
    return matrix, selected


def _correct_mask(item, matrix):
    target = item['targets']
    require(matrix.shape == target.shape, 'PAIR_GRID_DRIFT')
    diag = torch.eye(len(target), dtype=torch.bool)
    return (matrix == target) & ~diag


def _anchor(item, head, spec, device):
    """Pin initially right graph decisions and their scoring margins."""
    with torch.no_grad():
        logits = structural._forward(head, item, device)
        pred, _ = _decode_matrix(item, logits, spec)
        correct = _correct_mask(item, pred)
        return {'preserve': correct, 'margins': (logits[...,1:] - logits[...,0:1])
                .detach().cpu().float().clone(), 'initial_correct': int(correct.sum())}


def _anchor_indices(item, anchor, limit=96):
    positive = (anchor['preserve'] & (item['targets'] > 0)).nonzero(as_tuple=False).tolist()
    negative = (anchor['preserve'] & (item['targets'] == 0)).nonzero(as_tuple=False)
    if len(negative):
        values = anchor['margins'][negative[:,0], negative[:,1], :].amax(-1)
        order = torch.argsort(values, descending=True, stable=True)
        hard = negative[order[:max(0, limit - len(positive))]].tolist()
    else:
        hard = []
    pairs = positive + hard
    return (torch.tensor(pairs, dtype=torch.long).reshape(-1,2) if pairs else
            torch.zeros((0,2), dtype=torch.long))


def _loss(head, item, anchor, spec, device):
    logits = structural._forward(head, item, device)
    matrix, _ = _decode_matrix(item, logits, spec)
    gold = item['targets'].to(device)
    wrong = (matrix != item['targets']) & ~torch.eye(len(matrix), dtype=torch.bool)
    positions = wrong.nonzero(as_tuple=False)
    if len(positions):
        i,j = positions[:,0].to(device), positions[:,1].to(device)
        weights = torch.ones(len(BIND_CLASSES), device=device)
        weights[1:] = 3.0
        repair = F.cross_entropy(logits[i,j], gold[i,j], weight=weights)
    else:
        repair = logits.sum() * 0
    pairs = _anchor_indices(item, anchor)
    if len(pairs):
        i,j = pairs[:,0].to(device), pairs[:,1].to(device)
        margins = logits[...,1:] - logits[...,0:1]
        preserve = F.smooth_l1_loss(margins[i,j],
            anchor['margins'][pairs[:,0], pairs[:,1]].to(device))
        correct_ce = F.cross_entropy(logits[i,j], gold[i,j])
    else:
        preserve = logits.sum() * 0
        correct_ce = logits.sum() * 0
    hinge, _ = structural.graph_set_hinge(logits, gold, item['objects'], spec)
    total = repair + 0.55*preserve + 0.15*correct_ce + 0.15*hinge
    require(bool(torch.isfinite(total).item()), 'NONFINITE_CORRECTION_LOSS')
    return total, {'wrong_decisions': int(wrong.sum()), 'anchor_points': len(pairs),
                   'repair_ce': float(repair.detach()), 'preservation_loss': float(preserve.detach())}


def _weights(head):
    return {n:p.detach().clone() for n,p in head.named_parameters() if p.requires_grad}


def _restore(head, state):
    with torch.no_grad():
        for name,p in head.named_parameters():
            if p.requires_grad:
                p.copy_(state[name])


def _meta_blend(head, old, adapted, rate):
    with torch.no_grad():
        for name,p in head.named_parameters():
            if p.requires_grad:
                p.copy_(old[name] + (adapted[name]-old[name])*rate)


def _fitness(head, item, spec, device):
    with torch.no_grad():
        logits = structural._forward(head, item, device)
        pred_matrix, selected = _decode_matrix(item, logits, spec)
        pred_gold = item['targets']
        wrong = int(((pred_matrix != pred_gold) &
                     ~torch.eye(len(pred_gold), dtype=torch.bool)).sum())
        full_gold = item['full_gold']
        return {'pred_matrix':pred_matrix, 'edges':selected, 'pair_errors':wrong,
                'fp':len(selected-full_gold), 'fn':len(full_gold-selected),
                'exact':int(selected==full_gold), 'tp':len(selected&full_gold),
                'pred':len(selected), 'gold':len(full_gold)}


def _support_error_objective(head, support, spec, device, wrong_positions):
    """Continuous progress toward repairing the *same* mistakes as pre-edit.

    A correct, useful weight edit may improve a margin before it flips the
    structured decoder. Requiring immediate decoded repairs would prevent
    accumulation of small updates along the locally fitted correction surface.
    """
    total = 0.0
    count = 0
    with torch.no_grad():
        for item in support:
            cid = item['row']['case_id']
            pairs = wrong_positions[cid]
            if not len(pairs):
                continue
            logits = structural._forward(head, item, device)
            i, j = pairs[:, 0].to(device), pairs[:, 1].to(device)
            gold = item['targets'][pairs[:, 0], pairs[:, 1]].to(device)
            loss = F.cross_entropy(logits[i, j], gold, reduction='sum')
            total += float(loss.detach())
            count += len(pairs)
    return total / max(1, count)


def _guard_scan(head, items, anchors, spec, device, *, stop_on_damage=False):
    """Directly check the structured decoder at EVERY protected train point.

    Only gold-labeled discovery training examples are checked. Neither
    development nor confirmation gold participates in edit admission.
    """
    errors = damaged = correct = 0
    gained = []
    with torch.no_grad():
        for item in items:
            cid = item['row']['case_id']
            logits = structural._forward(head, item, device)
            decoded, _ = _decode_matrix(item, logits, spec)
            gold = item['targets']
            valid = ~torch.eye(len(gold), dtype=torch.bool)
            now_right = (decoded == gold) & valid
            errors += int((~now_right & valid).sum())
            protected = anchors[cid]['preserve']
            lost = protected & ~now_right
            damaged += int(lost.sum())
            correct += int(now_right.sum())
            if bool((now_right & ~protected).any()) and not stop_on_damage:
                gained.append((cid, now_right & ~protected,
                    (logits[..., 1:] - logits[..., :1]).detach().cpu().float()))
            if stop_on_damage and damaged:
                break
    return {'errors':errors, 'damaged_protected':damaged,
            'correct':correct, 'gained':gained}


def _apply_gained(anchors, gained):
    # Monotone expansion: every newly repaired point becomes protected.
    for cid, mask, margins in gained:
        anchors[cid]['preserve'] |= mask
        anchors[cid]['margins'][mask] = margins[mask]


def _safe_step(head, old, adapted, rate, support, global_guards, anchors,
               spec, device, current_errors, *, max_backtracks=9):
    """Trust-region line search with a global, rather than local, guard.

    Check a small support neighborhood first, then EVERY labeled train case.
    Preserve all verified correct decisions. Permit a continuously improving
    margin edit even when it has not yet changed the discrete graph; that is
    essential when compiling a smooth fuzz-derived surface into weights.
    """
    wrong_positions = {}
    with torch.no_grad():
        for item in support:
            logits = structural._forward(head, item, device)
            pred, _ = _decode_matrix(item, logits, spec)
            gold = item['targets']
            mask = (pred != gold) & ~torch.eye(len(gold), dtype=torch.bool)
            wrong_positions[item['row']['case_id']] = mask.nonzero(as_tuple=False)
    if not any(len(v) for v in wrong_positions.values()):
        _restore(head, old)
        return {'accepted':False, 'reason':'NO_LOCAL_ERRORS', 'rate':0.,
                'repaired_here':0, 'backtracks':0}, current_errors, []
    initial_objective = _support_error_objective(head,support,spec,device,wrong_positions)
    violations = failures = 0
    for attempt in range(max_backtracks):
        factor = rate / (2 ** attempt)
        _meta_blend(head, old, adapted, factor)
        # Fast local preservation veto, then the global in-distribution audit.
        local = _guard_scan(head, support, anchors, spec, device, stop_on_damage=True)
        if local['damaged_protected']:
            violations += 1
            continue
        proposed_objective = _support_error_objective(
            head,support,spec,device,wrong_positions)
        if (not math.isfinite(proposed_objective) or
            proposed_objective >= initial_objective - 1e-7):
            failures += 1
            continue
        global_state = _guard_scan(head,global_guards,anchors,spec,device)
        if global_state['damaged_protected'] or global_state['errors'] > current_errors:
            violations += 1
            continue
        repaired = current_errors - global_state['errors']
        return {'accepted':True,'rate':factor,'repaired_here':repaired,
            'backtracks':attempt,'objective_before':initial_objective,
            'objective_after':proposed_objective,
            'preserved_points':global_state['correct'] - repaired,
            'admission':'DISCRETE_REPAIR' if repaired else 'CONTINUOUS_MARGIN_PROGRESS',
            'global_guard_cases':len(global_guards)}, global_state['errors'], global_state['gained']
    _restore(head,old)
    return {'accepted':False,'rate':0.,'reason':'GLOBAL_PRESERVATION_OR_NO_MARGIN_GAIN',
            'preservation_vetoes':violations,'nonimproving_trials':failures,
            'backtracks':max_backtracks,'repaired_here':0,
            'global_guard_cases':len(global_guards)}, current_errors, []


def _bootstrap(head, train, dev_items, spec, device, cfg, dest):
    """Learn a nontrivial function from a *fresh* deterministic G2 start.

    One shared bootstrap is reused for every fresh-arm editor, so its learning
    is not counted as neuroediting performance or cherry-picked per method.
    No hard preservation is imposed before a functional baseline exists.
    All update targets come from the 0093 DISCOVERY train split only.
    """
    active = _mutable(head)
    optimizer = torch.optim.AdamW([p for _,p in active], lr=cfg.bootstrap_lr,
                                  weight_decay=0.)
    ordered = sorted(train,key=lambda item:item['row']['case_id'])
    init_metrics, _ = _score(head,ordered,spec,device)
    init_dev, _ = _score(head,dev_items,spec,device)
    rng = random.Random(cfg.seed + 202)
    require(ordered and all(len(it['targets'])>1 for it in ordered),
            'BOOTSTRAP_GRAPH_NO_PAIR_CANDIDATES')
    turns = list(range(len(ordered)))
    rng.shuffle(turns)
    cursor = 0
    history_path=dest/'fresh-bootstrap-history.jsonl'
    for step in range(1,cfg.bootstrap_steps+1):
        if cursor+cfg.bootstrap_batch > len(turns):
            rng.shuffle(turns)
            cursor=0
        batch = [ordered[i] for i in turns[cursor:cursor+cfg.bootstrap_batch]]
        cursor += len(batch)
        optimizer.zero_grad(set_to_none=True)
        cumulative=0.
        for item in batch:
            logits=structural._forward(head,item,device)
            target=item['targets'].to(device)
            valid=~torch.eye(len(target),dtype=torch.bool,device=device)
            scores=logits[valid]
            gold=target[valid]
            pos=int((gold!=0).sum().item())
            neg=int((gold==0).sum().item())
            # Some graphs have no positive relationship; these still teach the
            # no-edge class and must not abort a seeded curriculum.
            pos_weight=(min(cfg.bootstrap_positive_cap,
                         max(2.0, 0.5*neg/pos)) if pos else 1.0)
            example_weight=torch.where(gold>0, pos_weight,
                             torch.ones_like(gold,dtype=scores.dtype)).float()
            ce=F.cross_entropy(scores,gold,reduction='none')
            loss=(ce*example_weight).sum()/example_weight.sum()
            (loss/len(batch)).backward()
            cumulative+=float(loss.detach())/len(batch)
        grad = torch.nn.utils.clip_grad_norm_([p for _,p in active],1.0)
        require(bool(torch.isfinite(grad).item()),'NONFINITE_BOOTSTRAP_GRADIENT')
        optimizer.step()
        if step==1 or step%cfg.bootstrap_eval_every==0 or step==cfg.bootstrap_steps:
            # Passive measurement: NO evaluation statistic influences training.
            with torch.no_grad():
                trained,_=_score(head,ordered,spec,device)
                dev,_=_score(head,dev_items,spec,device)
            rec={'step':step,'batch_loss':cumulative,'train':trained,
                 'dev_passive':dev,'trainable_hash':_trainable_hash(head),
                 'selection_uses_dev':False}
            with history_path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(rec,sort_keys=True,allow_nan=False)+'\n')
            print('G2_0095_BOOTSTRAP '+json.dumps({'step':step,
                 'train_f1':round(trained['total']['edge_f1'],4),
                 'dev_f1_passive':round(dev['total']['edge_f1'],4),
                 'train_exact':trained['total']['exact'],
                 'loss':round(cumulative,4)},sort_keys=True),flush=True)
    final_train,_=_score(head,ordered,spec,device)
    final_dev,_=_score(head,dev_items,spec,device)
    summary={'steps':cfg.bootstrap_steps,'batch':cfg.bootstrap_batch,
       'initial':{'train':init_metrics,'dev':init_dev},
       'after':{'train':final_train,'dev':final_dev},
       'history_sha256':digest(history_path),
       'result_sha256':_trainable_hash(head),
       'shared_across_five_editors':True,'uses_confirmation':False,
       'bootstrap_not_counted_as_neuroediting':True}
    write(dest/'fresh-bootstrap-report.json',summary)
    return summary


def _refresh_anchors(head, guards, anchors, spec, device):
    """Newly corrected points become additional immutable local constraints."""
    with torch.no_grad():
        for item in guards:
            anchor = anchors[item['row']['case_id']]
            logits = structural._forward(head, item, device)
            matrix, _ = _decode_matrix(item, logits, spec)
            current = _correct_mask(item, matrix)
            gained = current & ~anchor['preserve']
            if gained.any():
                margin = (logits[...,1:] - logits[...,0:1]).detach().cpu().float()
                anchor['margins'][gained] = margin[gained]
                anchor['preserve'] |= gained


def _score(head, items, spec, device):
    counts = Counter()
    by_family = defaultdict(Counter)
    by_case = {}
    for item in items:
        outcome = _fitness(head, item, spec, device)
        row = item['row']; fam = row['family']
        for group in (counts, by_family[fam]):
            group.update({'cases':1, 'tp':outcome['tp'], 'fp':outcome['fp'],
                          'fn':outcome['fn'], 'exact':outcome['exact'],
                          'gold':outcome['gold'], 'pred':outcome['pred'],
                          'g1_exact':int(item['g1_exact'])})
        by_case[row['case_id']] = {'family':fam, 'mode':row['mode'], 'base_id':row['base_id'],
                                   'edges':outcome['edges'], 'gold':item['full_gold'],
                                   'graph':dict(row['graph'],_g2_0093_prompt=row['prompt']),
                                   'g1_exact':item['g1_exact']}
    def compact(c):
        return {'cases':c['cases'], 'exact':c['exact'], 'exact_rate':c['exact']/max(1,c['cases']),
                'edge_f1':2*c['tp']/max(1,c['gold']+c['pred']),
                'fp':c['fp'], 'fn':c['fn'], 'tp':c['tp'], 'g1_exact':c['g1_exact']}
    fuzz = defaultdict(Counter)
    groups = defaultdict(dict)
    for cid,row in by_case.items():
        groups[row['base_id']][row['mode']] = row
    for group in groups.values():
        if 'base' not in group:
            continue
        a = group['base']
        for mode, b in group.items():
            if mode == 'base':
                continue
            # For removal the unchanged occurrence string is the semantic anchor.
            semantic = mode == 'action_removal'
            na = atlas._id_edge_set(a['edges'], a['graph'], semantic=semantic)
            nb = atlas._id_edge_set(b['edges'], b['graph'], semantic=semantic)
            ga = atlas._id_edge_set(a['gold'], a['graph'], semantic=semantic)
            gb = atlas._id_edge_set(b['gold'], b['graph'], semantic=semantic)
            c = fuzz[(a['family'],mode)]
            c.update({'pairs':1, 'delta_correct':int((na^nb)==(ga^gb)),
                      'predicted_invariant':int(na==nb),
                      'gold_invariant':int(ga==gb),
                      'g1_exact_both':int(a['g1_exact'] and b['g1_exact'])})
    return {'total':compact(counts),
            'by_family':{fam:compact(by_family[fam]) for fam in atlas.FAMILIES},
            'fuzz':{'|'.join(k):dict(v) for k,v in sorted(fuzz.items())}}, by_case


def _delta(before, after):
    counts = Counter()
    shared = sorted(set(before) & set(after))
    for cid in shared:
        old, now = before[cid], after[cid]
        require(old['gold'] == now['gold'], 'EVALUATION_GOLD_CHANGED')
        gold = old['gold']
        union = gold | old['edges'] | now['edges']
        for edge in union:
            was_right = (edge in old['edges']) == (edge in gold)
            is_right = (edge in now['edges']) == (edge in gold)
            if not was_right and is_right: counts['repaired']+=1
            if was_right and not is_right: counts['damaged']+=1
        counts['cases']+=1
        if old['edges'] != gold and now['edges'] == gold: counts['newly_exact']+=1
        if old['edges'] == gold and now['edges'] != gold: counts['lost_exact']+=1
    return dict(counts)


def _fit(head, train_groups, dev_items, spec, device, cfg, arm, dest, technique):
    """One contender. Global correct-point constraints over entire train set."""
    head.eval()
    active=_mutable(head)
    parameters=[p for _,p in active]
    train=[it for bid in sorted(train_groups) for it in train_groups[bid].values()]
    anchors={it['row']['case_id']:_anchor(it,head,spec,device) for it in train}
    base_train,baseline_rows=_score(head,train,spec,device)
    base_dev,baseline_dev=_score(head,dev_items,spec,device)
    output=dest/arm
    output.mkdir(parents=True,exist_ok=False)
    write(output/'baseline.json',{'train':base_train,'dev':base_dev,
          'trainables_sha256':_trainable_hash(head),'g3_unlocked':False})
    journal=output/'history.jsonl'
    ids=sorted(train_groups)
    rng=random.Random(cfg.seed+41)
    rng.shuffle(ids)
    accepted=rejected=fixed=continuous=0
    initially_protected=sum(int(a['preserve'].sum()) for a in anchors.values())
    current_errors=_guard_scan(head,train,anchors,spec,device)['errors']
    last_record=None
    for episode in range(1,cfg.episodes+1):
        bid=ids[(episode-1)%len(ids)]
        group=train_groups[bid]
        variants=[group[m] for m in TRAIN_MODES[1:] if m in group]
        require('base' in group and variants,'FUZZ_NEIGHBOR_MISSING')
        support=[group['base']]+variants
        other=train_groups[ids[(episode+len(ids)//2-1)%len(ids)]]['base']
        local_guards=list({it['row']['case_id']:it for it in support+[other]}.values())
        old=_weights(head)
        details=[]
        if technique=='reptile':
            optimizer=torch.optim.AdamW(parameters,lr=cfg.inner_lr,weight_decay=0.)
            for inner in range(cfg.inner_steps):
                optimizer.zero_grad(set_to_none=True)
                loss=0
                for item in support:
                    part,info=_loss(head,item,anchors[item['row']['case_id']],spec,device)
                    loss=loss+part/len(support)
                    details.append(info)
                loss.backward()
                grad=torch.nn.utils.clip_grad_norm_(parameters,1.)
                require(bool(torch.isfinite(grad).item()),'NONFINITE_EDIT_GRADIENT')
                optimizer.step()
        else:
            details.append(neuroedit.propose(head,support,local_guards,anchors,
                structural._forward,lambda item,logits,_:_decode_matrix(item,logits,spec),
                device,technique,salient_k=cfg.salient_k))
        adapted=_weights(head)
        _restore(head,old)
        rate=cfg.meta_rate if technique=='reptile' else 1.
        result,next_errors,gained=_safe_step(head,old,adapted,rate,support,
            train,anchors,spec,device,current_errors)
        if result['accepted']:
            accepted+=1
            fixed+=result['repaired_here']
            continuous+=int(result['admission']=='CONTINUOUS_MARGIN_PROGRESS')
            current_errors=next_errors
            _apply_gained(anchors,gained)
        else:
            rejected+=1
        if episode==1 or episode%cfg.eval_every==0 or episode==cfg.episodes:
            training,rows_train=_score(head,train,spec,device)
            development,rows_dev=_score(head,dev_items,spec,device)
            direct=_guard_scan(head,train,anchors,spec,device)
            require(direct['damaged_protected']==0,'GLOBAL_ANCHOR_REGRESSION')
            require(direct['errors']==current_errors,'GLOBAL_GUARD_DRIFT')
            record={'episode':episode,'arm':arm,'technique':technique,
                'accepted':accepted,'rejected':rejected,
                'fixed_on_global_train':fixed,'continuous_margin_accepts':continuous,
                'protected_points_at_start':initially_protected,
                'protected_points_now':sum(int(a['preserve'].sum()) for a in anchors.values()),
                'global_pair_errors':current_errors,'last_update':result,
                'train':training,'dev':development,
                'train_correction':_delta(baseline_rows,rows_train),
                'dev_correction':_delta(baseline_dev,rows_dev),
                'last_inner':details[-2:],'g3_unlocked':False,
                'dev_used_in_admission':False}
            with journal.open('a',encoding='utf-8') as f:
                f.write(json.dumps(record,sort_keys=True,allow_nan=False)+'\n')
                f.flush()
            print('G2_0095_META '+json.dumps({'arm':arm,'technique':technique,
                'episode':episode,'accepted':accepted,'rejected':rejected,
                'train_exact':training['total']['exact'],
                'dev_exact':development['total']['exact'],
                'train_f1':round(training['total']['edge_f1'],4),
                'dev_f1':round(development['total']['edge_f1'],4),
                'global_protected':record['protected_points_now'],
                'continuous_accepts':continuous,'dev_repair':record['dev_correction']},
                sort_keys=True),flush=True)
            last_record=record
    final=_guard_scan(head,train,anchors,spec,device)
    require(final['damaged_protected']==0,'FINAL_ANCHOR_REGRESSION')
    return {'training':last_record,'baseline_train':base_train,'baseline_dev':base_dev,
            'initial_rows_train':baseline_rows,'initial_rows_dev':baseline_dev,
            'accepted':accepted,'rejected':rejected,'technique':technique,
            'protected_initial':initially_protected,
            'protected_final':sum(int(a['preserve'].sum()) for a in anchors.values()),
            'no_regression_on_known_correct_train_points':True,
            'continuous_margin_accepts':continuous,
            'history_sha256':digest(journal),
            'training_final_sha256':_trainable_hash(head)}


def _confirmation(root, atlas_dir, network, tok, composite, heads, spec, device, max_bases):
    """The ONLY confirmation-gold access is after both heads have finished fitting."""
    public = [r for r in lines(atlas_dir/'seal'/'prompts.jsonl')
              if r['split']=='confirmation']
    groups = defaultdict(list)
    for r in public:
        groups[r['base_id']].append(r)
    ids = sorted(groups)
    if max_bases != 0:
        require(max_bases > 0, 'BAD_CONFIRMATION_BUDGET')
        rng = random.Random(SEED + 99)
        rng.shuffle(ids)
        ids = sorted(ids[:min(max_bases,len(ids))])
    cids = {r['case_id'] for bid in ids for r in groups[bid]}
    cases = _get_rows(atlas_dir,cids,split='confirmation')
    # Store only graph decisions/labels, never the large token hidden-state tensors.
    total = {arm: [] for arm in heads}
    for index, row in enumerate(cases, 1):
        item = _capture_quiet(network,tok,composite,row,device)
        for name,head in heads.items():
            with torch.no_grad():
                outcome = _fitness(head,item,spec,device)
            total[name].append({'row':row, 'g1_exact':item['g1_exact'],
                 'edges':outcome['edges'], 'gold_edges':item['full_gold'],
                 'metrics':{k:outcome[k] for k in ('tp','fp','fn','exact','gold','pred')}})
        if index==1 or index%50==0 or index==len(cases):
            print('G2_0095_CONFIRM '+json.dumps({'done':index,'of':len(cases)}),flush=True)
    results = {}
    for name, pairs in total.items():
        counters = Counter()
        fam = defaultdict(Counter)
        fuzz_cases = defaultdict(dict)
        for record in pairs:
            row=record['row']; outcome=record['metrics']
            family = row['family']
            for counts in (counters,fam[family]):
                counts.update({'cases':1, 'tp':outcome['tp'], 'fp':outcome['fp'],
                    'fn':outcome['fn'], 'exact':outcome['exact'],
                    'gold':outcome['gold'], 'pred':outcome['pred'],
                    'g1_exact':int(record['g1_exact'])})
            fuzz_cases[row['base_id']][row['mode']] = {
                'pred':record['edges'], 'gold':record['gold_edges'],
                'graph':dict(row['graph'],_g2_0093_prompt=row['prompt'])}
        def summary(c):
            return {'cases':c['cases'],'exact':c['exact'],
                    'exact_rate':c['exact']/max(1,c['cases']),
                    'edge_f1':2*c['tp']/max(1,c['gold']+c['pred']),
                    'tp':c['tp'],'fp':c['fp'],'fn':c['fn'],
                    'g1_exact':c['g1_exact']}
        fs=defaultdict(Counter)
        for bid,g in fuzz_cases.items():
            a=g['base']
            for mode in TRAIN_MODES[1:]:
                b=g[mode]
                semantic=mode=='action_removal'
                orig = atlas._id_edge_set(a['pred'],a['graph'],semantic=semantic)
                variant = atlas._id_edge_set(b['pred'],b['graph'],semantic=semantic)
                orig_gold = atlas._id_edge_set(a['gold'],a['graph'],semantic=semantic)
                variant_gold = atlas._id_edge_set(b['gold'],b['graph'],semantic=semantic)
                fs[mode].update({'pairs':1, 'delta_correct':int((orig^variant)==(orig_gold^variant_gold)),
                                 'invariant':int(orig==variant),
                                 'gold_invariant':int(orig_gold==variant_gold)})
        results[name] = {'overall':summary(counters),
            'by_family':{f:summary(fam[f]) for f in atlas.FAMILIES},
            'fuzz':{m:dict(v) for m,v in fs.items()}}
    # Genuine held-out edge edits, relative to each starting condition.
    # The controls are only evaluated AFTER all methods and edits are frozen.
    references={
        ARM_RESET:'C_unedited_0092_B',
        ARM_FRESH:'C_fresh_seeded_after_bootstrap',
    }
    for name, records in total.items():
        initialization=next((arm for arm in ARMS if name.startswith(arm+'__')),None)
        if initialization is None:
            continue
        reference=references[initialization]
        control={r['row']['case_id']:r for r in total[reference]}
        comparison=Counter()
        family_counts=defaultdict(Counter)
        for updated in records:
            prev=control[updated['row']['case_id']]
            require(updated['gold_edges']==prev['gold_edges'],
                    'CONFIRMATION_CONTROL_GOLD_DRIFT')
            gold=updated['gold_edges']
            for edge in updated['edges']|prev['edges']|gold:
                old_right=(edge in prev['edges'])==(edge in gold)
                new_right=(edge in updated['edges'])==(edge in gold)
                for count in (comparison,family_counts[updated['row']['family']]):
                    if not old_right and new_right: count['repaired']+=1
                    if old_right and not new_right: count['damaged']+=1
            for count in (comparison,family_counts[updated['row']['family']]):
                count['cases']+=1
                if prev['edges']!=gold and updated['edges']==gold: count['newly_exact']+=1
                if prev['edges']==gold and updated['edges']!=gold: count['lost_exact']+=1
        results[name]['relative_to_initialization']={
            'control':reference,'overall':dict(comparison),
            'by_family':{f:dict(family_counts[f]) for f in atlas.FAMILIES}}
    return {'cases':len(cases),'base_graphs':len(ids), 'results':results,
            'original_split':'0093_confirmation_reused_after_training',
            'distribution_independent':False}


def _capture_quiet(network,tok,composite,row,device):
    item = structural._item_capture(network,tok,composite,row,device)
    objs = []
    for span in item['spans']:
        bounds=token_span_char_bounds(span,item['prepared'])
        require(bounds is not None,'CONFIRM_UNMAPPED_SPAN')
        objs.append([bounds[0],bounds[1],span['kind']])
    item['objects'] = objs
    observed={(k,(a,b)) for a,b,k in objs}
    expected={(n['kind'],tuple(n['span'])) for n in row['graph']['nodes'] if 'span' in n}
    item['g1_exact'] = observed==expected
    item['full_gold'] = structural.parent.previous._gold_edges(row['graph'])
    return item


def run(*, root=ROOT_DEFAULT, output=None, device='cuda', seed=SEED, train_bases=12,
        dev_bases=6, episodes=120, inner_steps=3, inner_lr=0.0002,
        meta_rate=0.5, eval_every=20, confirmation_bases=300,
        salient_k=24, techniques=None, bootstrap_steps=400,
        bootstrap_batch=4, bootstrap_lr=0.0008, bootstrap_eval_every=100,
        bootstrap_positive_cap=12.0):
    techniques = TECHNIQUES if techniques is None else tuple(techniques)
    require(techniques and len(set(techniques))==len(techniques) and
            all(t in TECHNIQUES for t in techniques), 'INVALID_TECHNIQUES')
    require(1<=salient_k<=192, 'BAD_SALIENCE_BUDGET')
    require(1<=episodes<=20000 and 1<=inner_steps<=16 and 0<inner_lr<=0.005 and
            0<meta_rate<=1 and 1<=eval_every<=episodes, 'BAD_TRAINING_CONFIG')
    require(bootstrap_steps >= 1 and 1 <= bootstrap_batch <= 16 and
            0 < bootstrap_lr <= .01 and 1 <= bootstrap_eval_every <= bootstrap_steps and
            1 <= bootstrap_positive_cap <= 100, 'BAD_BOOTSTRAP_CONFIG')
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'REQUESTED_DEVICE_NOT_AVAILABLE')
    cfg=argparse.Namespace(seed=seed,train_bases=train_bases,dev_bases=dev_bases,
          episodes=episodes,inner_steps=inner_steps,inner_lr=inner_lr,
          meta_rate=meta_rate,eval_every=eval_every,confirmation_bases=confirmation_bases,
          salient_k=salient_k, techniques=list(techniques),
          bootstrap_steps=bootstrap_steps,bootstrap_batch=bootstrap_batch,
          bootstrap_lr=bootstrap_lr,bootstrap_eval_every=bootstrap_eval_every,
          bootstrap_positive_cap=bootstrap_positive_cap)
    root, atlas_dir, commit, source_dir = _source(root)
    dest=(Path(output) if output else root/NAME).resolve()
    require(dest.parent.is_dir() and not dest.exists(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    selected, selection = _discovery_selection(atlas_dir,train_bases,dev_bases,seed)
    train_ids=[cid for bid in selected['train'] for cid in selection[bid]]
    dev_ids=[cid for bid in selected['dev'] for cid in selection[bid]]
    require(set(train_ids).isdisjoint(dev_ids), 'DISCOVERY_TRAIN_DEV_COLLISION')
    train_rows=_get_rows(atlas_dir,set(train_ids),split='discovery')
    dev_rows=_get_rows(atlas_dir,set(dev_ids),split='discovery')
    dest.mkdir(exist_ok=False)
    provenance={'schema':SCHEMA,'runner_sha256':digest(__file__),
        'original_0093_commit_sha256':digest(atlas_dir/'seal'/'commit.json'),
        'original_0093_discovery_lock_sha256':digest(atlas_dir/'discovery'/'lock.json'),
        'frozen_head_0092_B_sha256':digest(source_dir/cur.HEAD_FILE),
        'seed':seed,'fresh_cutover_modules':['pair_projection','binding',
                    'interface_couplers.G1_G2.delta if present'],
        'frozen_modules':'all non-G2 modules including G1 and G3-G7',
        'train_case_ids':train_ids, 'dev_case_ids':dev_ids,
        'confirmation_gold_used_in_selection':False,
        'selection_policy':'discovery G1 exact base-renaming-role_swap, nonzero failed-head errors',
        'algorithm':'fresh seeded G2 supervised discovery-only bootstrap common to all five editors; '
                    'Reptile, inverse Jacobian, rank-one, local ridge, RBF; global decoder-level '
                    'hard preservation on all verified correct discovery train pairs; continuous '
                    'repair-margin admission, backtracking, and untouched confirmation',
        'related_existing_tools':['nanojev_qwen_centroid_local_math.py',
                                  'nanojev_qwen_vector_gradient.py'],
        'config':vars(cfg),'independent_distribution':False,
        'g2_certified':False,'g3_unlocked':False}
    write(dest/'provenance.json',provenance)
    torch.manual_seed(seed)
    random.seed(seed)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(seed)
    parent_record,_=hard._verify_trained(atlas._roots(root),atlas._roots(root)['parent_0087'])
    network,tok=backbone.model_components(parent_record['model_id'],atlas._roots(root)['adapter'],device)
    require(all(not p.requires_grad for p in network.parameters()),'BACKBONE_IS_TRAINABLE')
    composite=g1native.load_composite(atlas._roots(root)['composite'],device=device)
    print('G2_0095_CAPTURE_TRAIN_DEV '+json.dumps({'train':len(train_rows),
          'dev':len(dev_rows)},sort_keys=True),flush=True)
    train_items=_capture(network,tok,composite,train_rows,device,must_exact=True)
    dev_items=_capture(network,tok,composite,dev_rows,device,must_exact=True)
    groups=_group(train_items)
    for bid in selected['train']:
        require(set(NEEDED_MODES)<=set(groups[bid]),'BASE_LOST_FUZZ_NEIGHBORS')
    source_hash=digest(source_dir/cur.HEAD_FILE)
    baseline_head,manifest=cur._load_checkpoint(source_dir,device=device)
    _mutable(baseline_head)
    frozen_digest=_frozen_hash(baseline_head)
    # The fresh-start curriculum happens ONCE, before comparing editors. Its
    # optimizer never sees the inherited G2 head or confirmation labels.
    fresh_head, _ = cur._load_checkpoint(source_dir,device=device)
    _mutable(fresh_head)
    raw_fresh_hash=_seed_cutover(fresh_head,seed)
    require(_frozen_hash(fresh_head)==frozen_digest,'BOOTSTRAP_MUTATED_FROZEN')
    boot_report=_bootstrap(fresh_head,train_items,dev_items,
        commit['lineage']['decoder'],device,cfg,dest)
    warmed_weights=_weights(fresh_head)
    warmed_hash=_trainable_hash(fresh_head)
    require(warmed_hash==boot_report['result_sha256'],'WARMUP_HASH_DRIFT')
    boot_ckpt=dest/'fresh-bootstrap-checkpoint'
    boot_ckpt.mkdir(exist_ok=False)
    save_file({k:v.detach().cpu().contiguous() for k,v in fresh_head.state_dict().items()},
              str(boot_ckpt/cur.HEAD_FILE))
    write(boot_ckpt/'manifest.json',{'schema':SCHEMA,
        'phase':'COMMON_FRESH_G2_BOOTSTRAP_NOT_EDITOR',
        'parent_sha256':source_hash,'raw_seed_hash':raw_fresh_hash,
        'warmed_trainable_hash':warmed_hash,
        'checkpoint_sha256':digest(boot_ckpt/cur.HEAD_FILE),
        'frozen_non_G2_sha256':frozen_digest,
        'g3_unlocked':False,'no_promotion':True})
    print('G2_0095_CUTOVER '+json.dumps({'raw_seed_hash':raw_fresh_hash,
        'warmed_hash':warmed_hash,'initial_f1':round(
            boot_report['initial']['train']['total']['edge_f1'],5),
        'warmed_f1':round(boot_report['after']['train']['total']['edge_f1'],5),
        'same_warm_start_for_all_methods':True},sort_keys=True),flush=True)
    heads={}
    outcomes={}
    for initialization in ARMS:
        for technique in techniques:
            name = initialization + '__' + technique
            head,loaded_manifest=cur._load_checkpoint(source_dir,device=device)
            _mutable(head)
            require(_frozen_hash(head)==frozen_digest,'FROZEN_COMPONENT_DIFFERENT')
            if initialization==ARM_FRESH:
                _restore(head,warmed_weights)
            init_hash=_trainable_hash(head)
            if initialization==ARM_FRESH:
                require(init_hash==warmed_hash,'FRESH_EDITORS_NOT_IDENTICAL')
            require(_frozen_hash(head)==frozen_digest,'G1_CHANGED_BY_FRESH_INIT')
            head.eval()
            print('G2_0095_ARM_START '+json.dumps({'arm':name,'method':technique,
                  'initial_G2_sha256':init_hash,'frozen_sha256':frozen_digest},
                  sort_keys=True),flush=True)
            result=_fit(head,groups,dev_items,commit['lineage']['decoder'],
                        device,cfg,name,dest,technique)
            require(_frozen_hash(head)==frozen_digest,'NON_G2_WEIGHT_MUTATED')
            arms_path=dest/name/'checkpoint'
            arms_path.mkdir(exist_ok=False)
            save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
                      str(arms_path/cur.HEAD_FILE))
            new_manifest=dict(loaded_manifest)
            new_manifest.update({'head_sha256':digest(arms_path/cur.HEAD_FILE),
               'status':'TRAINED_NOT_CERTIFIED','g3_training_unlocked':False,
               'production_promotion':False,'g2_0095':{'arm':initialization,
               'technique':technique,'initial_sha256':init_hash,
               'seeded_raw_sha256':raw_fresh_hash if initialization==ARM_FRESH else None,
               'frozen_sha256':frozen_digest,'parent_sha256':source_hash,
               'g3_unlocked':False}})
            write(arms_path/'manifest.json',new_manifest)
            result['initial_G2_sha256']=init_hash
            result['final_G2_sha256']=_trainable_hash(head)
            result['checkpoint_sha256']=digest(arms_path/cur.HEAD_FILE)
            del result['initial_rows_train'];del result['initial_rows_dev']
            outcomes[name]=result
            heads[name]=head
            write(dest/name/'training-report.json',result)
    require(digest(source_dir/cur.HEAD_FILE)==source_hash,'PARENT_MODIFIED')
    # All edited trajectories are fixed. Add the immutable starting head as a
    # confirmation control; no data selection or weight edit uses its scores.
    heads['C_unedited_0092_B']=baseline_head
    heads['C_fresh_seeded_after_bootstrap']=fresh_head
    evaluation=_confirmation(root,atlas_dir,network,tok,composite,heads,
                  commit['lineage']['decoder'],device,confirmation_bases)
    report={'schema':SCHEMA,'status':'G2_0095_NEUROEDIT_BAKEOFF_TRAINED_NOT_CERTIFIED',
            'arms':{name:{key:v for key,v in report.items() if key not in
                    ('training','baseline_train','baseline_dev')} for name,report in outcomes.items()},
            'training':{name:report['training'] for name,report in outcomes.items()},
            'initial':{name:{'train':r['baseline_train'],'dev':r['baseline_dev']}
                       for name,r in outcomes.items()},
            'confirmation':evaluation,'fresh_bootstrap':boot_report,
            'techniques':list(techniques),
            'initializations':list(ARMS),
            'confirmation_control':'C_unedited_0092_B',
            'fresh_bootstrap_control':'C_fresh_seeded_after_bootstrap',
            'global_training_anchor_protection':True,
            'frozen_non_G2_sha256':frozen_digest,
            'parent_checkpoint_unchanged':True,'independent_certification':False,
            'g2_certified':False,'g3_unlocked':False,'no_promotion':True,
            'provenance_sha256':digest(dest/'provenance.json')}
    write(dest/'report.json',report)
    write(dest/'lock.json',{'schema':SCHEMA,'report_sha256':digest(dest/'report.json'),
          'provenance_sha256':report['provenance_sha256'],
          'checkpoints':{name:outcomes[name]['checkpoint_sha256'] for name in outcomes},
          'parent_checkpoint_sha256':source_hash,
          'shared_bootstrap_checkpoint_sha256':digest(boot_ckpt/cur.HEAD_FILE),
          'g3_unlocked':False})
    print('G2_0095_RESULT '+json.dumps({'status':report['status'],
          'confirmation':{name:{'by_family':evaluation['results'][name]['by_family'],
          'overall':evaluation['results'][name]['overall']} for name in heads},
          'parent_unchanged':True,'g3_unlocked':False},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='run',choices=('run',))
    parser.add_argument('--root',default=ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--train-bases',type=int,default=12,
                        help='G1-exact training base graphs PER family')
    parser.add_argument('--dev-bases',type=int,default=6,
                        help='Disjoint G1-exact development base graphs PER family')
    parser.add_argument('--episodes',type=int,default=120)
    parser.add_argument('--inner-steps',type=int,default=3)
    parser.add_argument('--inner-lr',type=float,default=0.0002)
    parser.add_argument('--meta-rate',type=float,default=0.5)
    parser.add_argument('--eval-every',type=int,default=20)
    parser.add_argument('--salient-k',type=int,default=24)
    parser.add_argument('--techniques',default=','.join(TECHNIQUES),
                        help='comma-separated neuroediting techniques to run')
    parser.add_argument('--bootstrap-steps',type=int,default=400)
    parser.add_argument('--bootstrap-batch',type=int,default=4)
    parser.add_argument('--bootstrap-lr',type=float,default=0.0008)
    parser.add_argument('--bootstrap-eval-every',type=int,default=100)
    parser.add_argument('--bootstrap-positive-cap',type=float,default=12.0)
    parser.add_argument('--confirmation-bases',type=int,default=300,
                        help='0 means all 300; otherwise balanced random bases')
    args=parser.parse_args(argv)
    config={k:v for k,v in vars(args).items() if k!='command'}
    config['techniques']=tuple(t.strip() for t in args.techniques.split(',') if t.strip())
    return run(**config)


if __name__=='__main__':main()
