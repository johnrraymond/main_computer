#!/usr/bin/env python3
"""QW5 staged join: PREPARE -> TRAIN (QW5 only) -> later HybridLink/output gates.

The original EMP and HybridLink files are not edited by this patch.  Four EMP
source checkpoints and exactly one trained Hard EMP checkpoint are mandatory.
Qwen intermediate/final states and operands feed the five readout branches.
Output is decoded from QW5; the Qwen LM head is NOT used to select answers.
No model checkpoint is promoted to the application by this training harness.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import torch

from tools import nanojev_qw5 as qw
from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_zero_line_sampled_radius_ladder as sampled
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import prediction_values

SCHEMA = 'nanojev.qw5.join-training.v1'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qw5_phase1_v1'


def write_json(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def write_state(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    torch.save(obj, tmp)
    os.replace(tmp, path)


def word_problem(a, b, style=0):
    # Gold result is never incorporated in prompt; only the two operands.
    styles = (
        f'A vehicle is at position {a} on a number line. It moves {b} positions. What is its new position?',
        f'An account balance is {a} units. A transaction changes it by {b} units. What is the balance?',
        f'A temperature is {a} degrees and changes by {b} degrees. What is the final temperature?',
        f'A player has a score of {a}. The next event changes the score by {b}. Find the new score.',
    )
    return styles[style % len(styles)] + '\nNumerical answer:'


def create_split(split, radius, seed, count):
    """Independent magnitude-family splits, not random pair-only leakage.

    A + (-A + delta)=delta is the signed cancellation curriculum. Wider
    residuals are included as controls. Fold is always applied to the actual
    maximum operand magnitude, not an uncorrected nominal anchor.
    """
    if split not in ('train', 'dev') or count < 2 or not 16 <= radius <= 2_000_000:
        raise ValueError('QW5_INVALID_DATA_REQUEST')
    rng = random.Random(f'qw5-data:v1:{seed}:{split}:{radius}')
    residuals = (-128, -64, -32, -8, -3, -2, -1, 0, 1, 2, 3, 8, 32, 64, 128)
    rows = []; seen = set()
    for attempts in range(count * 500):
        if len(rows) >= count:
            break
        # Log-spaced magnitudes, with regular full-frontier exposures.
        if len(rows) % 4 == 0:
            magnitude = rng.randint(max(1, radius // 2), radius)
        else:
            magnitude = rng.randint(1, radius) if radius <= 32 else round(2 ** rng.uniform(0, (radius).bit_length()-1))
        magnitude = max(1, min(radius, magnitude))
        delta = residuals[len(rows) % len(residuals)]
        if abs(delta) > 2 * magnitude:
            continue
        a = magnitude * (-1 if rng.randrange(2) else 1)
        b = -a + delta
        if rng.randrange(2):
            a, b = b, a
        actual = max(abs(a), abs(b))
        if actual > radius or (a, b) in seen:
            continue
        if sampled.stable_fold(actual, seed) != split:
            continue
        seen.add((a, b)); rows.append((a, b, delta))
    if len(rows) != count:
        raise RuntimeError(f'QW5_SPLIT_EXHAUSTED:{split}:{len(rows)}/{count}; try a larger --radius')
    return rows


def encoded_states(qwen, tokenizer, rows, device, *, style_offset=0):
    """Collect actual Qwen intermediate/final states; never numerical targets."""
    qwen.backbone.eval()
    data = []
    with torch.inference_mode():
        for i, (a, b, _expected) in enumerate(rows):
            prompt = word_problem(a, b, i + style_offset)
            ids = tokenizer(prompt, add_special_tokens=False, return_tensors='pt')['input_ids'].to(device)
            result = qwen.backbone(input_ids=ids, output_hidden_states=True,
                                   use_cache=False, return_dict=True)
            if len(result.hidden_states) <= max(qw.QWEN_TAPS):
                raise RuntimeError('QW5_MISSING_QWEN_LAYER_TAPS')
            item = torch.stack([result.hidden_states[t][0, -1].float()
                                for t in qw.QWEN_TAPS], dim=0).cpu()
            data.append(item)
    return torch.stack(data)


def verify_sources(cfg, run_dir=None):
    """Resolve the EXACT original frozen bank checkpoints, preferring an archive.

    Live trainer `latest.pt` files are mutable. Never treat a newer specialist
    as interchangeable with the checkpoint that the old QW5 adapter learned.
    """
    files = qw.source_paths(Path(cfg['source']['campaign']), cfg['source']['stage_radius'],
                            hard_state_dir=cfg['source'].get('hard_state_dir', 'hard_emp'),
                            allow_missing=True)
    expected = cfg['source']['sha256']
    resolved = {}
    for key, live in files.items():
        archive = Path(run_dir) / 'source_archive' / (key + '.pt') if run_dir is not None else None
        if archive is not None and archive.is_file():
            if qw.sha(archive) != expected[key]:
                raise RuntimeError('QW5_SOURCE_ARCHIVE_CORRUPT:' + key)
            resolved[key] = archive
        elif live.is_file() and qw.sha(live) == expected[key]:
            resolved[key] = live
        else:
            raise RuntimeError('QW5_SOURCE_SHA_DRIFT:' + key +
                               ':original frozen checkpoint required; '
                               'run tools.nanojev_qw5_source_archive inspect/recover')
    return resolved


def archive_sources(run_dir, cfg, source_files=None):
    """Atomically store all 4 verified sources under the Stage-1 QW5 run.

    Caller must have matched the recorded SHA for each file. Does not edit
    config.json, trained_state, accepted checkpoints, or the live EMP campaign.
    """
    import shutil
    archive_dir = Path(run_dir) / 'source_archive'
    expected = cfg['source']['sha256']
    original = (source_files if source_files is not None
                else verify_sources(cfg, run_dir))
    if set(original) != set(expected) or set(expected) != {'stage', 'hard', 'left', 'right'}:
        raise RuntimeError('QW5_ARCHIVE_SOURCE_KEY_MISMATCH')
    for key, file in original.items():
        if not Path(file).is_file() or qw.sha(file) != expected[key]:
            raise RuntimeError('QW5_ARCHIVE_SOURCE_SHA_DRIFT:' + key)
    archive_dir.mkdir(parents=True, exist_ok=True)
    for key, file in original.items():
        target = archive_dir / (key + '.pt')
        if target.is_file():
            if qw.sha(target) != expected[key]:
                raise RuntimeError('QW5_SOURCE_ARCHIVE_CORRUPT:' + key)
            continue
        tmp = archive_dir / (key + '.pt.tmp')
        try:
            shutil.copyfile(file, tmp)
            if qw.sha(tmp) != expected[key]:
                raise RuntimeError('QW5_ARCHIVE_COPY_SHA_DRIFT:' + key)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
    return {key: archive_dir / (key + '.pt') for key in expected}


def restore(run_dir, device):
    cfg = json.loads((run_dir / 'config.json').read_text('utf-8'))
    if cfg.get('schema') != SCHEMA:
        raise RuntimeError('QW5_CONFIG_SCHEMA_CHANGED')
    pinned_files = verify_sources(cfg, run_dir)
    units, info = qw.load_five(cfg['source']['campaign'], cfg['source']['ladder_output'],
                               stage=cfg['source']['stage_radius'], device=device,
                               hard_state_dir=cfg['source'].get('hard_state_dir', 'hard_emp'),
                               source_files=pinned_files)
    if info != cfg['source']:
        raise RuntimeError('QW5_SOURCE_IDENTITY_CHANGED')
    model = qw.QW5(units).to(device)
    checkpoint = torch.load(run_dir / 'latest.pt', map_location='cpu', weights_only=True)
    if checkpoint.get('schema') != SCHEMA + '.checkpoint':
        raise RuntimeError('QW5_CHECKPOINT_SCHEMA_CHANGED')
    state = checkpoint['trained_state']
    trainable = dict(model.named_parameters())
    expected = {n for n, p in trainable.items() if p.requires_grad}
    if set(state) != expected:
        raise RuntimeError('QW5_TRAINABLE_PARAMETER_SCHEMA_DRIFT')
    with torch.no_grad():
        for name in expected:
            if trainable[name].shape != state[name].shape:
                raise RuntimeError('QW5_WEIGHT_SHAPE_DRIFT:' + name)
            trainable[name].copy_(state[name].to(device))
    return model, checkpoint, cfg


def learned_state(model):
    return {n: p.detach().cpu().clone() for n, p in model.named_parameters() if p.requires_grad}


def prep(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError('QW5_REFUSES_EXISTING_RUN')
    banks, provenance = qw.load_five(args.campaign, args.ladder_output, stage=args.stage,
                                     hard_state_dir=args.hard_state_dir)
    model = qw.QW5(banks)
    cfg = {'schema': SCHEMA, 'source': provenance,
           'qwen': {'model': args.model, 'revision': args.revision or (hybrid.MODEL_REVISION if args.model == hybrid.MODEL_ID
                                            else hybrid.INSTRUCT_REVISION if args.model == hybrid.INSTRUCT_ID
                                            else None),
                    'taps': list(qw.QWEN_TAPS), 'width': 1024,
                    'link_checkpoint': None, 'qwen_conditioning': 'initial_transformer_states'},
           'dataset': {'radius': args.radius, 'seed': args.seed,
                       'train_cards': args.train_cards, 'dev_cards': args.dev_cards},
           'stages': {'qw5_training': 'READY_UNTRAINED',
                      'hybridlink_feedback': 'DISABLED_UNCERTIFIED',
                      'qw5_output_authority': 'DISABLED_UNCERTIFIED'},
           'architecture': 'QW5 trains five independent Qwen-conditioned correction heads and a fused numerical output; frozen source banks'}
    # Probe deterministically BEFORE any files are published.
    train = create_split('train', args.radius, args.seed, args.train_cards)
    dev = create_split('dev', args.radius, args.seed, args.dev_cards)
    if {(a,b) for a,b,_ in train} & {(a,b) for a,b,_ in dev}:
        raise RuntimeError('QW5_DATA_LEAKAGE')
    args.output.mkdir(parents=True)
    # Pin frozen sources before publishing the Stage-1 checkpoint.
    archive_sources(args.output, cfg)
    write_json(args.output / 'config.json', cfg)
    write_state(args.output / 'latest.pt', {'schema': SCHEMA + '.checkpoint',
                                            'step': 0, 'trained_state': learned_state(model),
                                            'optimizer': None, 'train_card_ids': train,
                                            'dev_card_ids': dev, 'source': provenance})
    print('QW5_PREPARED=' + json.dumps({'stage': 1, 'state': 'READY_UNTRAINED',
                                        'five_banks': list(qw.HEAD_NAMES),
                                        'output': str(args.output)}, sort_keys=True))


def features_for(run_dir, cfg, model_id, tokenizer, qwen, device, checkpoint):
    # Cache states for efficiency; session validates SHA and Qwen source identity.
    # We deliberately do not cache final numerical answers or targets as features.
    path = run_dir / 'qwen_states.pt'
    sig = qw.sha(run_dir / 'config.json')
    if path.is_file():
        cache = torch.load(path, map_location='cpu', weights_only=True)
        if cache.get('schema') != SCHEMA + '.qwen-cache' or cache.get('config_sha') != sig:
            raise RuntimeError('QW5_QWEN_CACHE_IDENTITY_DRIFT')
        return cache['train'].float(), cache['dev'].float()
    result = {}
    for split in ('train', 'dev'):
        print(f'QW5_QWEN_ENCODING={split} {len(checkpoint[split+"_card_ids"])} story prompts', flush=True)
        result[split] = encoded_states(qwen, tokenizer, checkpoint[split + '_card_ids'], device,
                                        style_offset=0 if split == 'train' else 1001).half()
    write_state(path, {'schema': SCHEMA + '.qwen-cache', 'config_sha': sig, **result})
    return result['train'].float(), result['dev'].float()


def train(args):
    run_dir = args.output.resolve()
    model, cp, cfg = restore(run_dir, args.device)
    if cfg['stages']['hybridlink_feedback'] != 'DISABLED_UNCERTIFIED' or cfg['stages']['qw5_output_authority'] != 'DISABLED_UNCERTIFIED':
        raise RuntimeError('QW5_UNEXPECTED_LATER_STAGE_ACTIVATION')
    qwen, tokenizer, details = hybrid.load_qwen(cfg['qwen']['model'], cfg['qwen']['revision'],
                                                device=args.device, precision=args.precision,
                                                allow_download=args.allow_model_download)
    if details['hidden_size'] != cfg['qwen']['width']:
        raise RuntimeError('QW5_QWEN_HIDDEN_SIZE_DRIFT')
    qwen.eval().requires_grad_(False)
    hidden_train, hidden_dev = features_for(run_dir, cfg, details, tokenizer, qwen, args.device, cp)
    del qwen
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    tr = cp['train_card_ids']; dev = cp['dev_card_ids']
    train_x = input_tensor(tr, args.device)
    train_y = torch.tensor([v[2] for v in tr], dtype=torch.long, device=args.device)
    dev_x = input_tensor(dev, args.device)
    dev_y = torch.tensor([v[2] for v in dev], dtype=torch.long, device=args.device)
    train_h = hidden_train.to(args.device)
    dev_h = hidden_dev.to(args.device)
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.lr, weight_decay=0.)
    if cp['optimizer'] is not None:
        optimizer.load_state_dict(cp['optimizer'])
        for group in optimizer.param_groups:
            if abs(group['lr'] - args.lr) > 1e-12:
                raise RuntimeError('QW5_LEARNING_RATE_DRIFT')
    rng = random.Random(cfg['dataset']['seed'] + cp['step'])
    if args.steps < 1 or args.batch_size < 2:
        raise ValueError('QW5_TRAINING_ARGUMENTS_INVALID')
    report = None
    for t in range(1, args.steps + 1):
        model.train()
        indices = [rng.randrange(len(tr)) for _ in range(args.batch_size)]
        idx = torch.tensor(indices, device=args.device)
        logits = model(train_x[idx], train_h[idx])
        loss = qw.qw5_loss(logits, train_y[idx])
        if not torch.isfinite(loss):
            raise RuntimeError('QW5_NONFINITE_TRAINING_LOSS')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        grads = [p.grad for p in model.conditioners.parameters()]
        if not any(g is not None and bool(g.abs().sum() > 0) for g in grads):
            raise RuntimeError('QW5_QWEN_CONDITIONING_GRADIENT_ABSENT')
        torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1., error_if_nonfinite=True)
        optimizer.step()
        if t == args.steps or t % args.probe_every == 0:
            report = qw.result_stats(model, dev_x, dev_h, dev_y)
            report.update({'step': cp['step'] + t, 'loss': float(loss.detach()),
                           'conditioning_gain_over_zero': round(report['exact'] - report['qwen_zero_exact'], 6),
                           'stage2_feedback': 'DISABLED_UNCERTIFIED',
                           'stage3_output_authority': 'DISABLED_UNCERTIFIED',
                           'qw5_numerical_accuracy': 'EXPERIMENTAL_NOT_CERTIFIED'})
            print('QW5_PROBE=' + json.dumps(report, sort_keys=True), flush=True)
            write_state(run_dir / 'latest.pt', {'schema': SCHEMA + '.checkpoint',
                        'step': cp['step'] + t, 'trained_state': learned_state(model),
                        'optimizer': optimizer.state_dict(),
                        'train_card_ids': tr, 'dev_card_ids': dev, 'source': cfg['source']})
            write_json(run_dir / 'latest_report.json', report)
    print('QW5_TRAIN_COMPLETE=' + json.dumps({'step': cp['step'] + args.steps,
          'report': str(run_dir / 'latest_report.json'), 'next_stage_enabled': False}, sort_keys=True))


def status(args):
    cfg = json.loads((args.output / 'config.json').read_text('utf-8'))
    verify_sources(cfg, args.output)
    cp = torch.load(args.output / 'latest.pt', map_location='cpu', weights_only=True)
    print('QW5_STATUS=' + json.dumps({'steps': cp['step'], 'stages': cfg['stages'],
                 'source': cfg['source'], 'report': str(args.output / 'latest_report.json')}, sort_keys=True))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('run', 'prepare', 'train', 'status', 'stage2', 'stage3'))
    p.add_argument('--output', type=Path, default=ROOT)
    p.add_argument('--campaign', type=Path, help='EMP outward ladder campaign containing the hard_emp sidecar')
    p.add_argument('--ladder-output', type=Path, help='Original certified AdditionNet checkpoint root')
    p.add_argument('--hard-state-dir', default='hard_emp',
                   help='Hard EMP sidecar under campaign; must match the EMP --hard-state-dir')
    p.add_argument('--stage', type=int, default=None, help='Explicit existing outward radius; default most recent')
    p.add_argument('--radius', type=int, default=512, help='Max magnitude for signed cancellation cards')
    p.add_argument('--train-cards', type=int, default=256)
    p.add_argument('--dev-cards', type=int, default=64)
    p.add_argument('--seed', type=int, default=20261026)
    p.add_argument('--model', default=hybrid.MODEL_ID)
    p.add_argument('--revision', default=None)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--precision', choices=('auto', 'float16', 'float32', 'bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--steps', type=int, default=500)
    p.add_argument('--batch-size', type=int, default=16)
    p.add_argument('--lr', type=float, default=2e-4)
    p.add_argument('--probe-every', type=int, default=100)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ('run', 'prepare'):
            if args.command == 'prepare' or not (args.output / 'config.json').is_file():
                if args.campaign is None or args.ladder_output is None:
                    raise ValueError('QW5_PREPARE_REQUIRES_EMP_CAMPAIGN_AND_LADDER_OUTPUT')
                prep(args)
            if args.command == 'run':
                if args.probe_every < 1:
                    raise ValueError('QW5_PROBE_EVERY_INVALID')
                train(args)
        elif args.command == 'train':
            if args.probe_every < 1:
                raise ValueError('QW5_PROBE_EVERY_INVALID')
            train(args)
        elif args.command == 'status':
            status(args)
        else:
            raise RuntimeError('QW5_' + args.command.upper() + '_DISABLED_UNTIL_TRAINING_GATE')
        return 0
    except (RuntimeError, FileNotFoundError, OSError, ValueError) as exc:
        print('QW5_REFUSED:' + str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
