"""Independent, immutable G1 fresh-development replication; G2 remains locked.

Predeclared 80 fresh author-annotated dev examples (40 per family) are generated
with a new seed. The two highest-ranked *previous* transfer arms are chosen
from the frozen 0009/0010 report BEFORE any new labels or scores are inspected.
Each prompt is captured once and both frozen heads use the same predeclared
length-linear decoder. This is within-generator replication, NOT a sealed
challenge/test evaluation, official certification, or a license to train G2.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import random
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_decoder_frob import _candidate_scores, _char_set, decode_whole_span_scores
from tools.nanojev_gamer_v1_g1_decoder_transfer import ARMS, SCHEMA as TRANSFER_SCHEMA, _preflight
from tools.nanojev_gamer_v1_graph_comparison import _stats
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.fresh-dev-replication.v1'
SEED = 94117
CASES = 80
DECODER = 'length_linear'


def _select_arms(transfer, entries, dataset_sha, adapter_sha):
    """Rank ONLY previously reported dev scores; never fresh examples."""
    arms = transfer.get('arms', {})
    names = {a[0] for a in ARMS}
    if (transfer.get('schema') != TRANSFER_SCHEMA
            or transfer.get('status') != 'DEVELOPMENT_FROZEN_DECODER_TRANSFER_ONLY_NOT_CERTIFICATION'
            or transfer.get('frozen_neural_scores') is not True
            or transfer.get('dataset_sha256') != dataset_sha
            or transfer.get('source_adapter_sha256') != adapter_sha
            or transfer.get('decoder') != DECODER
            or transfer.get('length_power') != 1.0
            or transfer.get('per_object_penalty') != 0.0
            or transfer.get('same_decoder_for_all_checkpoints') is not True
            or transfer.get('same_development_split_for_all_checkpoints') is not True
            or transfer.get('challenge_or_test_split_used') is not False
            or transfer.get('production_promotion') is not False
            or transfer.get('g2_unlocked') is not False
            or transfer.get('no_training') is not True
            or set(arms) != names):
        raise ValueError('GAMER_G1_FRESH_UNTRUSTED_TRANSFER_REPORT')
    for name in names:
        data = arms[name]
        metrics = data.get('linear', {})
        if (data.get('head_sha256') != entries[name]['manifest']['head_sha256']
                or metrics.get('cases') != transfer.get('cases')
                or not isinstance(metrics.get('exact_prompts'), int)
                or not isinstance(metrics.get('f1'), (float, int))
                or not isinstance(metrics.get('recall'), (float, int))
                or not 0 <= metrics['exact_prompts'] <= metrics['cases']
                or not 0 <= metrics['f1'] <= 1
                or not 0 <= metrics['recall'] <= 1):
            raise ValueError('GAMER_G1_FRESH_TRANSFER_METRIC_LINEAGE')
    ranked = sorted(names, key=lambda name: (
        -arms[name]['linear']['exact_prompts'],
        -arms[name]['linear']['f1'],
        -arms[name]['linear']['recall'], name))
    if ranked[0] != 'structured':
        raise ValueError('GAMER_G1_FRESH_STRUCTURED_NOT_RECORDED_LEADER')
    return tuple(ranked[:2]), tuple(ranked)


def _original_prompts(dataset):
    """Read only train/dev prompt surfaces; never inspect sealed labels."""
    used = set()
    with Path(dataset).open(encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record['split'] in ('train', 'dev'):
                prompt = record['prompt']
                if not isinstance(prompt, str) or prompt in used:
                    raise ValueError('GAMER_G1_FRESH_ORIGINAL_PROMPTS_INVALID')
                used.add(prompt)
    if not used:
        raise ValueError('GAMER_G1_FRESH_EMPTY_REFERENCE')
    return used


def fresh_rows(dataset, *, seed=SEED, cases=CASES):
    """Reproducible dev-only generation, with disjoint original train/dev prompts."""
    from tools.nanojev_gamer_v1_graph_corpus import allocation, encounter
    if (type(seed) is not int or seed < 1 or type(cases) is not int
            or cases < 2 or cases % 2):
        raise ValueError('GAMER_G1_FRESH_INVALID_GENERATION_SETTINGS')
    used = _original_prompts(dataset)
    rng = random.Random(seed)
    rows = []
    for index in range(cases):
        generator = encounter if index % 2 == 0 else allocation
        for _attempt in range(100):
            row = generator(rng, 'dev', index)
            if row['prompt'] not in used:
                break
        else:
            raise ValueError('GAMER_G1_FRESH_PROMPT_COLLISION')
        used.add(row['prompt'])
        rows.append(row)
    return rows


def _miss_reasons(prediction, prepared, row, predicted):
    """Attribute misses for the selected linear decoder, not the old global one."""
    from tools.nanojev_gamer_v1_graph_curriculum import aligned_labels
    aligned = aligned_labels(prepared, row)['spans']
    gold = {(s['start'], s['end'], s['kind']) for s in row['object_spans']}
    if len(aligned) != len(gold):
        raise ValueError('GAMER_G1_FRESH_GOLD_ALIGNMENT')
    logits = prediction['span_logits'].detach().float().cpu()
    valid = prediction['span_valid'].detach().bool().cpu()
    probs = logits.softmax(-1)
    reasons = Counter()
    per_kind = {kind: Counter() for kind in SPAN_KINDS}
    for span in aligned:
        bounds = token_span_char_bounds(span, prepared)
        first, last = span['token_first'], span['token_last']
        kind = span['kind']
        if bounds is None:
            reason = 'token_to_character_alignment'
        elif (*bounds, kind) in predicted:
            continue
        elif last - first >= valid.shape[1] or first < 0 or first >= valid.shape[0]:
            reason = 'span_exceeds_window'
        elif not bool(valid[first, last - first]):
            reason = 'invalid_token_span'
        else:
            scores = probs[first, last-first]
            top = int(scores.argmax().item())
            label = SPAN_KINDS.index(kind)+1
            if top == 0:
                reason = 'null_wins'
            elif top != label:
                reason = 'wrong_type'
            elif float(scores[label]) < .50:
                reason = 'below_threshold'
            else:
                reason = 'linear_decoder_excluded'
        reasons[reason] += 1
        per_kind[kind][reason] += 1
    if sum(reasons.values()) != len(gold - predicted):
        raise ValueError('GAMER_G1_FRESH_MISS_ACCOUNTING')
    return reasons, per_kind


def _summary(records, misses, by_kind_misses, families):
    metric = _stats(records)
    metric['missing_objects'] = metric['expected'] - metric['correct']
    metric['false_positive_objects'] = metric['predicted'] - metric['correct']
    metric['miss_reasons'] = dict(sorted(misses.items()))
    metric['by_kind_miss_reasons'] = {kind: dict(sorted(by_kind_misses[kind].items()))
                                      for kind in SPAN_KINDS}
    metric['by_family'] = {family: _stats(families[family])
                           for family in ('encounter', 'allocation')}
    return metric


def run(*, root, transfer_report, output, device='cuda', max_length=2048,
        seed=SEED, cases=CASES):
    """No training; creates one immutable report only after both arms complete."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur

    root = Path(root)
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_FRESH_IMMUTABLE_OUTPUT')
    if max_length < 1:
        raise ValueError('GAMER_G1_FRESH_INVALID_MAX_LENGTH')
    dataset, dataset_sha, adapter_sha, entries = _preflight(root, output)
    transfer_path = Path(transfer_report)
    transfer_sha = backbone.sha256(transfer_path)
    transfer = json.loads(transfer_path.read_text(encoding='utf-8'))
    selected, ranking = _select_arms(transfer, entries, dataset_sha, adapter_sha)
    rows = fresh_rows(dataset, seed=seed, cases=cases)
    row_text = ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n' for row in rows)
    fresh_sha = hashlib.sha256(row_text.encode('utf-8')).hexdigest()

    # Verify frozen model/adapter lineage before observing any new examples.
    first = entries[selected[0]]['manifest']
    if (backbone.adapter_fingerprint(first['source_adapter']) != adapter_sha
            or any(entries[name]['manifest']['model_id'] != first['model_id']
                   or entries[name]['manifest']['source_adapter'] != first['source_adapter']
                   or entries[name]['manifest']['head_sha256'] == ''
                   for name in selected)):
        raise ValueError('GAMER_G1_FRESH_MODEL_LINEAGE')
    heads = {}
    for name in selected:
        head, manifest = cur._load_checkpoint(entries[name]['checkpoint'], device=device)
        if (manifest['head_sha256'] != entries[name]['manifest']['head_sha256']
                or manifest['stage'] != 'G1'):
            raise ValueError('GAMER_G1_FRESH_HEAD_CHANGED')
        head.eval()
        heads[name] = head
    model, tokenizer = backbone.model_components(first['model_id'], first['source_adapter'], device)
    records = {name: [] for name in selected}
    misses = {name: Counter() for name in selected}
    kinds = {name: {kind: Counter() for kind in SPAN_KINDS} for name in selected}
    families = {name: {'encounter': [], 'allocation': []} for name in selected}
    per_case = []
    for index, row in enumerate(rows, 1):
        # Shared frozen Qwen features; NO gold, graph, or expected_decisions in inference.
        item = cur.capture(model, tokenizer, row, device, max_length)
        prepared = item['prepared']
        hidden = item['hidden'].to(device=device, dtype=torch.float32)
        gold = {(s['start'], s['end'], s['kind']) for s in row['object_spans']}
        predictions = {}
        case = {'index': index, 'family': row['family'],
                'prompt_sha256': hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'gold_count': len(gold), 'arms': {}}
        for name in selected:
            with torch.inference_mode():
                prediction = heads[name](hidden, eligible=prepared['eligible'].to(device),
                                         active_stage='G1')
            spans = decode_whole_span_scores(_candidate_scores(
                prediction['span_logits'], prediction['span_valid']),
                length_power=1.0, per_object_penalty=0.0)
            predicted = _char_set(spans, prepared)
            why, kinds_why = _miss_reasons(prediction, prepared, row, predicted)
            records[name].append((predicted, gold))
            families[name][row['family']].append((predicted, gold))
            misses[name].update(why)
            for kind in SPAN_KINDS:
                kinds[name][kind].update(kinds_why[kind])
            predictions[name] = predicted
            case['arms'][name] = {'correct': len(predicted & gold),
                                  'predicted': len(predicted),
                                  'missing': len(gold - predicted),
                                  'false_positives': len(predicted - gold),
                                  'exact': predicted == gold,
                                  'miss_reasons': dict(sorted(why.items()))}
        leader, challenger = selected
        case['paired'] = {
            'leader_only_correct': len((predictions[leader] & gold) - predictions[challenger]),
            'runner_up_only_correct': len((predictions[challenger] & gold) - predictions[leader]),
            'leader_exact_only': case['arms'][leader]['exact'] and not case['arms'][challenger]['exact'],
            'runner_up_exact_only': case['arms'][challenger]['exact'] and not case['arms'][leader]['exact'],
        }
        per_case.append(case)
        if index == 1 or index % 10 == 0 or index == cases:
            print(f'GAMER_G1_FRESH_DEV {index}/{cases}', flush=True)
    scores = {name: _summary(records[name], misses[name], kinds[name], families[name])
              for name in selected}
    leader, runner_up = selected
    lead = scores[leader]
    other = scores[runner_up]
    paired = {
        'leader_exact_only': sum(v['paired']['leader_exact_only'] for v in per_case),
        'runner_up_exact_only': sum(v['paired']['runner_up_exact_only'] for v in per_case),
        'leader_only_correct_objects': sum(v['paired']['leader_only_correct'] for v in per_case),
        'runner_up_only_correct_objects': sum(v['paired']['runner_up_only_correct'] for v in per_case),
    }
    # A predeclared comparison, not a G1 pass/fail gate: a regression is reported,
    # never used to tune this decoder or unlock the next stage.
    replicated_lead = (lead['exact_prompts'] >= other['exact_prompts']
                       and lead['f1'] >= other['f1']
                       and (lead['exact_prompts'] > other['exact_prompts']
                            or lead['f1'] > other['f1']))
    result = {
        'schema': SCHEMA,
        'status': 'FRESH_WITHIN_GENERATOR_DEVELOPMENT_ONLY_NOT_CERTIFICATION',
        'seed': seed, 'cases': cases, 'split': 'fresh_dev',
        'families': {'encounter': cases//2, 'allocation': cases//2},
        'fresh_dataset_sha256': fresh_sha,
        'original_dataset_sha256': dataset_sha,
        'source_adapter_sha256': adapter_sha,
        'transfer_report_sha256': transfer_sha,
        'selected_before_fresh_capture': list(selected),
        'previous_development_ranking': list(ranking),
        'decoder': DECODER, 'length_power': 1.0, 'per_object_penalty': 0.0,
        'old_development_metrics': {name: transfer['arms'][name]['linear'] for name in selected},
        'head_sha256': {name: entries[name]['manifest']['head_sha256'] for name in selected},
        'fresh_metrics': scores, 'paired_comparison': paired,
        'leader_outperforms_runner_up_on_fresh_dev': replicated_lead,
        'interpretation': ('Within-generator, seed-disjoint development replication only; '
                           'does not establish broad out-of-distribution generalization'),
        'no_training': True, 'heads_frozen': True,
        'shared_prompt_captures': True, 'decoder_frozen': True,
        'challenge_or_test_split_used': False,
        'production_promotion': False, 'certification_authorized': False,
        'g2_unlocked': False,
    }
    if backbone.sha256(transfer_path) != transfer_sha:
        raise ValueError('GAMER_G1_FRESH_TRANSFER_REPORT_CHANGED_DURING_RUN')
    output.mkdir(parents=True)
    (output / 'fresh_dev.jsonl').write_text(row_text, encoding='utf-8')
    (output / 'per_case.jsonl').write_text(''.join(
        json.dumps(case, sort_keys=True) + '\n' for case in per_case), encoding='utf-8')
    (output / 'fresh_report.json').write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result
