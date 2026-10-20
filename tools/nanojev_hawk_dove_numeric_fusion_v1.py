"""T02 numeric-fusion candidate: live parsed operands + frozen V3 features.

This is an opt-in, independent candidate, NOT a replacement for the production
V3 head. The T02 operand mapping is learned from TRAIN labels; no payoff or
arithmetic coefficients are encoded in the architecture. The existing V3
semantic features remain available to a regularized residual readout.

T02 contains one fixed symbolic rule. Passing T02 does NOT certify the semantic
branch or generalization to different operators/game payoffs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import numpy as np
from tools import nanojev_hawk_dove_continuous_benchmark as benchmark
from tools import nanojev_hawk_dove_numeric_pathway_probe as pathway
from tools import nanojev_hawk_dove_million as hd

SCHEMA = 'nanojev.hawk-dove.numeric-fusion-t02.v1'
SCALE = 1_000_000.0
METHODS = ('corrected_v3', 'numeric_only', 'fusion', 'fusion_without_numeric',
           'fusion_without_semantics', 'fusion_shuffled_semantics')


def exact_input_counts(prompt):
    """Parse only count operands; reject unsupported/altered T02 expression.

    The displayed Y argument is intentionally validated structurally but NEVER
    forwarded as a feature. ADD child order is irrelevant: payoff labels bind
    operands by their role, not their textual position.
    """
    import re
    tree = hd.parse_lisp(hd.strip_and_validate_bars(prompt, hd.REP_SCIENCE))
    if not (isinstance(tree, tuple) and len(tree) == 3 and tree[0] == 'ARGMAX'):
        raise ValueError('UNSUPPORTED_T02_ROOT')
    add, displayed_y = tree[1:]
    if not (isinstance(displayed_y, str) and re.fullmatch(r'[+-]?\d+', displayed_y)):
        raise ValueError('T02_DISPLAYED_Y_MUST_BE_LITERAL')
    if not (isinstance(add, tuple) and len(add) == 3 and add[0] == 'ADD'):
        raise ValueError('UNSUPPORTED_T02_ARITHMETIC')
    counts = {}
    for term in add[1:]:
        if not (isinstance(term, tuple) and len(term) == 3 and term[0] == 'MUL'):
            raise ValueError('UNSUPPORTED_T02_TERM')
        _, count, payoff = term
        if payoff not in ('HAWK_HAWK_PAYOFF', 'HAWK_DOVE_PAYOFF') or payoff in counts:
            raise ValueError('T02_OPERAND_ROLE_INVALID')
        if not isinstance(count, str) or not re.fullmatch(r'\d+', count):
            raise ValueError('T02_COUNT_INVALID')
        counts[payoff] = int(count)
    if set(counts) != {'HAWK_HAWK_PAYOFF', 'HAWK_DOVE_PAYOFF'}:
        raise ValueError('T02_COUNT_ROLES_INCOMPLETE')
    a, b = counts['HAWK_HAWK_PAYOFF'], counts['HAWK_DOVE_PAYOFF']
    if a + b != 1_000_000:
        raise ValueError('T02_CONSERVATION_VIOLATION')
    return (a, b)


def manifest_identity(args):
    import types
    previous_args = types.SimpleNamespace(root=args.previous, donor=args.donor,
                                           teacher=args.teacher, source=args.source)
    benchmark.verify(previous_args)
    # The earlier pathway probe captured the corrected 10M V3 baseline. Do
    # not recompute it using the previous benchmark's incorrect cap.
    pathway_args = types.SimpleNamespace(root=args.pathway, previous=args.previous,
        donor=args.donor, teacher=args.teacher, source=args.source, splits=('train','dev'),
        epochs=args.pathway_epochs, learning_rate=args.pathway_lr,
        train_batch_size=args.pathway_batch_size, seed=args.pathway_seed)
    pathway.verify(pathway_args)
    for split in ('train','dev'):
        benchmark.load_dataset(previous_args, split)
        pathway.load_capture(pathway_args, split)
    if args.root == args.previous or args.root == args.pathway or args.root == args.donor:
        raise ValueError('OUTPUT_ROOT_MUST_BE_INDEPENDENT')
    for original in (args.previous, args.pathway, args.donor):
        if original in args.root.parents or args.root in original.parents:
            raise ValueError('OUTPUT_ROOT_MUST_BE_INDEPENDENT')
    return {
        'schema': SCHEMA,
        'module_sha256': benchmark.digest(Path(__file__)),
        'source_sha256': benchmark.digest(args.source),
        'donor_checkpoint_sha256': benchmark.digest(args.donor/'latest.pt'),
        'teacher_sha256': benchmark.digest(args.teacher),
        'previous_manifest_sha256': benchmark.digest(args.previous/'manifest.json'),
        'pathway_manifest_sha256': benchmark.digest(args.pathway/'manifest.json'),
        'feature_train_sha256': benchmark.digest(args.previous/'features-train.npz'),
        'feature_dev_sha256': benchmark.digest(args.previous/'features-dev.npz'),
        'corrected_v3_train_sha256': benchmark.digest(args.pathway/'pathway-train.npz'),
        'corrected_v3_dev_sha256': benchmark.digest(args.pathway/'pathway-dev.npz'),
        'seed': args.seed, 'epochs': args.epochs, 'learning_rate': args.learning_rate,
        'train_batch_size': args.train_batch_size, 'semantic_scale': args.semantic_scale,
        'test_split_sealed': True, 'tier': 'T02',
        'input': 'validated T02 operand-role parser + frozen pretrained V3 body',
        'outputs': 'continuous signed XY',
        'training': 'train-only learned affine numerical map + regularized semantic residual',
    }


def prepare(args):
    args.root.mkdir(parents=True, exist_ok=True)
    expected = manifest_identity(args)
    file = args.root/'manifest.json'
    if file.exists():
        if json.loads(file.read_text(encoding='utf8')) != expected:
            raise ValueError('NUMERIC_FUSION_IDENTITY_DRIFT')
    else:
        if any(args.root.iterdir()):
            raise ValueError('NONEMPTY_UNINITIALIZED_NUMERIC_FUSION_ROOT')
        benchmark.write_json(file, expected)
    return expected


def verify(args):
    file = args.root/'manifest.json'
    if not file.exists():
        raise FileNotFoundError('RUN_PREPARE_FIRST')
    if json.loads(file.read_text(encoding='utf8')) != manifest_identity(args):
        raise ValueError('NUMERIC_FUSION_IDENTITY_DRIFT')


def inputs_and_labels(args, split):
    """Read split-preserving exact integer operands; labels only for scoring.

    We deliberately do not reuse cached float32 operands: their rounding can
    miss the 5%-relative metric when X is exactly zero.
    """
    from types import SimpleNamespace
    ds = benchmark.load_dataset(SimpleNamespace(root=args.previous, donor=args.donor,
                         teacher=args.teacher, source=args.source), split)
    rows = benchmark.dataset_rows(args.teacher, split)
    if [r['case_id'] for r in rows] != ds['case_ids'].tolist():
        raise ValueError('CASE_ORDER_DRIFT')
    operands = np.asarray([exact_input_counts(r['prompt']) for r in rows], dtype=np.float64)
    if not np.allclose(operands / SCALE, ds['operands'], atol=6e-8, rtol=0):
        raise ValueError('NUMERIC_FEATURE_INPUT_MISMATCH')
    if ds['features'].shape[1:] != (2, 192):
        raise ValueError('UNEXPECTED_V3_BODY_SHAPE')
    return ds, operands, rows


def affine_from_train(operands, targets):
    """Fit coefficients from TRAIN examples alone; no hardcoded X/Y algebra."""
    design = np.column_stack((operands / SCALE, np.ones(len(operands))))
    coef = np.linalg.lstsq(design, targets.astype(np.float64) / SCALE, rcond=None)[0]
    return coef


def create_fusion(dim, coef, semantic_scale):
    import torch
    from torch import nn

    class NumericFusionHead(nn.Module):
        def __init__(self):
            super().__init__()
            self.numeric = nn.Linear(2, 2, dtype=torch.float64)
            with torch.no_grad():
                self.numeric.weight.copy_(torch.as_tensor(coef[:2].T, dtype=torch.float64))
                self.numeric.bias.copy_(torch.as_tensor(coef[2], dtype=torch.float64))
            # Preserve the pretrained V3 *features*. Only these new small
            # residual parameters are trained, never Qwen or the V3 decoder.
            self.semantic = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, 64),
                                          nn.GELU(), nn.Linear(64, 2))
            nn.init.zeros_(self.semantic[-1].weight)
            nn.init.zeros_(self.semantic[-1].bias)
            self.semantic_scale = float(semantic_scale)

        def forward(self, v3_features, input_counts, *, zero_numeric=False,
                    zero_semantics=False):
            if v3_features.ndim != 3 or v3_features.shape[1] != 2 or v3_features.shape[2]*2 != dim:
                raise ValueError('FUSION_FEATURE_SHAPE')
            if input_counts.shape != (v3_features.shape[0], 2):
                raise ValueError('FUSION_INPUT_COUNT_SHAPE')
            if not torch.isfinite(v3_features).all() or not torch.isfinite(input_counts).all():
                raise ValueError('FUSION_NONFINITE_INPUT')
            x = input_counts.to(torch.float64) / SCALE
            if zero_numeric:
                x = torch.zeros_like(x)
            baseline = self.numeric(x)
            f = v3_features.flatten(1).to(torch.float32)
            if zero_semantics:
                f = torch.zeros_like(f)
            # Regularized bounded residual allows symbolic information while
            # preventing a small memorized set from shifting the learned rule
            # by arbitrarily many orders of magnitude.
            correction = torch.tanh(self.semantic(f)).to(torch.float64)*self.semantic_scale
            if zero_semantics:
                correction = torch.zeros_like(correction)
            return (baseline + correction)*SCALE

    return NumericFusionHead()


def save_model(args, model, coef, log):
    import torch
    destination = args.root/'candidate.pt'
    if destination.exists():
        raise FileExistsError('NUMERIC_FUSION_CANDIDATE_ALREADY_EXISTS')
    state = {
        'schema': SCHEMA, 'manifest_sha256': benchmark.digest(args.root/'manifest.json'),
        'feature_dim': int(model.semantic[0].normalized_shape[0]),
        'semantic_scale': args.semantic_scale,
        'coef': torch.from_numpy(coef),
        'weights': {k: v.detach().cpu().clone() for k,v in model.state_dict().items()},
        'training_log': log,
        'model_purpose': 'T02_CANDIDATE_NOT_MAIN_NANOJEV_REPLACEMENT',
    }
    with tempfile.NamedTemporaryFile('wb', suffix='.pt', dir=args.root, delete=False) as fd:
        temporary = Path(fd.name)
    try:
        torch.save(state, temporary)
        if destination.exists():
            raise FileExistsError('NUMERIC_FUSION_CANDIDATE_ALREADY_EXISTS')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    benchmark.write_json(args.root/'candidate.json', {
        'schema': SCHEMA, 'sha256': benchmark.digest(destination),
        'manifest_sha256': benchmark.digest(args.root/'manifest.json'),
        'epochs': args.epochs, 'train_split_only': True,
    })


def load_model(args):
    import torch
    file = args.root/'candidate.pt'
    report = json.loads((args.root/'candidate.json').read_text(encoding='utf8'))
    if report['sha256'] != benchmark.digest(file):
        raise ValueError('FUSION_MODEL_SHA_DRIFT')
    saved = torch.load(file, map_location='cpu', weights_only=True)
    if saved['schema'] != SCHEMA or saved['manifest_sha256'] != benchmark.digest(args.root/'manifest.json'):
        raise ValueError('FUSION_MODEL_PROVENANCE_MISMATCH')
    if saved['semantic_scale'] != args.semantic_scale:
        raise ValueError('FUSION_MODEL_SCALE_DRIFT')
    model = create_fusion(saved['feature_dim'], saved['coef'].numpy(), args.semantic_scale)
    model.load_state_dict(saved['weights'], strict=True)
    model.eval()
    return model, saved


def train(args):
    import torch
    import torch.nn.functional as F
    verify(args)
    if (args.root/'candidate.pt').exists():
        load_model(args)
        print(json.dumps({'event': 'fusion_candidate_reused'}), flush=True)
        return
    train_ds, exact, _ = inputs_and_labels(args, 'train')
    coef = affine_from_train(exact, train_ds['targets'])
    torch.manual_seed(args.seed)
    model = create_fusion(train_ds['features'].shape[1]*train_ds['features'].shape[2],
                          coef, args.semantic_scale)
    # The affine map is already fitted to train examples and held fixed here.
    # The symbolic residual trains separately. The training target is only XY,
    # never a supplied coefficient or formula.
    model.numeric.requires_grad_(False)
    feat = torch.from_numpy(train_ds['features'])
    operands = torch.from_numpy(exact)
    labels = torch.from_numpy(train_ds['targets'].astype(np.float64))
    optimizer = torch.optim.AdamW(model.semantic.parameters(), lr=args.learning_rate,
                                  weight_decay=.01)
    generator = torch.Generator().manual_seed(args.seed)
    log = []
    for epoch in range(1, args.epochs+1):
        model.train()
        batch_losses = []
        for idx in torch.randperm(len(feat), generator=generator).split(args.train_batch_size):
            pred = model(feat[idx], operands[idx])
            numeric_loss = F.smooth_l1_loss(pred/SCALE, labels[idx]/SCALE, beta=.05)
            correction = (pred - model.numeric(operands[idx]/SCALE)*SCALE)/SCALE
            # Prevent the feature branch from overfitting the few fixed-rule
            # training instances at the expense of held-out generalization.
            loss = numeric_loss + correction.square().mean()
            if not bool(torch.isfinite(loss)):
                raise RuntimeError('NONFINITE_FUSION_LOSS')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.semantic.parameters(), max_norm=5)
            optimizer.step()
            batch_losses.append(float(loss.detach()))
        if epoch % max(1, args.epochs//4) == 0 or epoch == args.epochs:
            record = {'epoch': epoch, 'train_loss': float(np.mean(batch_losses))}
            log.append(record)
            print(json.dumps({'event': 'fusion_training_progress', **record}), flush=True)
    model.eval()
    save_model(args, model, coef, log)
    print(json.dumps({'event':'numeric_fusion_candidate_saved',
                      'path':str(args.root/'candidate.pt'),
                      'train_learned_coefficients':coef.tolist()}), flush=True)


def inference(model, ds, exact_counts, variant='fusion', batch_size=256):
    import torch
    if variant not in ('numeric_only','fusion','fusion_without_numeric',
                       'fusion_without_semantics','fusion_shuffled_semantics'):
        raise ValueError('INVALID_FUSION_VARIANT')
    original = torch.from_numpy(ds['features'])
    inputs = torch.from_numpy(exact_counts)
    result = []
    with torch.inference_mode():
        for k in range(0, len(original), batch_size):
            f = original[k:k+batch_size]
            n = inputs[k:k+batch_size]
            if variant == 'fusion_shuffled_semantics':
                # Shift whole *instance* features, preserving the mask index.
                # Duplicated T02 masks cannot leak from the same instance.
                indices = (np.arange(k,k+len(f)) + 8) % len(original)
                f = original[indices]
            pred = model(f, n, zero_numeric=(variant=='fusion_without_numeric'),
                         zero_semantics=(variant in ('numeric_only', 'fusion_without_semantics')))
            if variant == 'numeric_only':
                pred = model.numeric(n/SCALE)*SCALE
            result.append(pred.cpu().numpy())
    return np.concatenate(result, axis=0)


def analyze(args):
    verify(args)
    model, state = load_model(args)
    record = {'schema': SCHEMA, 'manifest_sha256': benchmark.digest(args.root/'manifest.json'),
              'candidate_sha256': benchmark.digest(args.root/'candidate.pt'),
              'dev_not_used_in_training': True, 'held_out_test_sealed': True,
              't02_only_no_cross_operator_generalization_claim': True,
              'fit_coefficient_matrix': state['coef'].tolist(),
              'splits': {}}
    for split in ('train', 'dev'):
        ds, exact, rows = inputs_and_labels(args, split)
        correction = pathway.load_capture(__import__('types').SimpleNamespace(
            root=args.pathway, previous=args.previous, donor=args.donor,
            source=args.source, teacher=args.teacher), split)['corrected_v3']
        results = {'corrected_v3': {'overall': benchmark.accuracy(correction, ds['targets']),
                                    'per_mask': benchmark.per_mask_accuracy(correction, ds)}}
        predictions = {}
        for method in METHODS[1:]:
            pred = inference(model, ds, exact, method)
            predictions[method] = pred
            results[method] = {'overall': benchmark.accuracy(pred, ds['targets']),
                               'per_mask': benchmark.per_mask_accuracy(pred, ds)}
        combined = predictions['fusion']
        without = predictions['numeric_only']
        results['fusion']['symbolic_residual_mae_count'] = float(np.abs(combined-without).mean())
        results['fusion']['symbolic_residual_max_count'] = float(np.abs(combined-without).max())
        results['fusion']['numeric_channel_ablation_delta'] = (
            results['fusion']['overall']['joint_5pct'] -
            results['fusion_without_numeric']['overall']['joint_5pct'])
        results['fusion']['semantic_shuffle_delta'] = (
            results['fusion']['overall']['joint_5pct'] -
            results['fusion_shuffled_semantics']['overall']['joint_5pct'])
        results['fusion']['passes_T02_70pct'] = results['fusion']['overall']['joint_5pct'] >= .70
        record['splits'][split] = {'n': len(rows), 'cases': len(set(ds['instances'].tolist())),
                                   'score': results}
        print(json.dumps({'event': 'numeric_fusion_evaluation', 'split': split,
            'scores': {m: round(results[m]['overall']['joint_5pct'],6) for m in METHODS},
            'fusion_symbolic_residual_mae': results['fusion']['symbolic_residual_mae_count'],
            'passes_70pct': results['fusion']['passes_T02_70pct']}), flush=True)
    file = args.root/'results.json'
    if file.exists() and json.loads(file.read_text(encoding='utf8')) != record:
        raise ValueError('FUSION_REPORT_EXISTS_DIFFERENT_CONTENT')
    if not file.exists():
        benchmark.write_json(file, record)
    return record


def predict_prompts(model, frozen_model, prompts, device):
    """Actual runtime integration: raw prompts -> frozen V3 -> fusion -> XY."""
    import torch
    from tools import nanojev_game_transform_experiment as ex
    inputs = np.asarray([exact_input_counts(prompt) for prompt in prompts], dtype=np.float64)
    captured = []
    def hook(_module, _args, value):
        captured.append(value.detach().float().cpu().clone())
    handle = frozen_model['decoder'].body.register_forward_hook(hook)
    try:
        frozen_model['decoder'].eval()
        with torch.inference_mode():
            out, _ = ex.forward(frozen_model, prompts)
    finally:
        handle.remove()
    if len(captured) != 1 or captured[0].shape != (len(prompts),2,192):
        raise RuntimeError('FUSION_RUNTIME_V3_FEATURE_DRIFT')
    model.eval()
    with torch.inference_mode():
        result = model(captured[0], torch.from_numpy(inputs)).cpu().numpy()
    if not np.isfinite(result).all():
        raise RuntimeError('FUSION_RUNTIME_OUTPUT_NONFINITE')
    return result, captured[0].numpy()


def live_smoke(args):
    """Verify deployable inference path matches cached dev rows, no retraining."""
    import torch
    from tools import nanojev_game_transform_experiment as ex
    verify(args)
    model, _ = load_model(args)
    data, exact, rows = inputs_and_labels(args, 'dev')
    n = min(args.live_cases, len(rows)//8)
    if n < 1:
        raise ValueError('NO_LIVE_CASES')
    selected = rows[:n*8]
    frozen_cp = torch.load(args.donor/'latest.pt', map_location='cpu', weights_only=True)
    frozen = ex.load_current(args.source, args.device,
                             'float16' if args.device=='cuda' else 'float32',
                             False, int(frozen_cp['config']['seed']))
    frozen['decoder'].load_state_dict(frozen_cp['decoder'], strict=True)
    frozen['gamehead'].load_state_dict(frozen_cp['game_operator_head'], strict=True)
    frozen['decoder'].eval()
    all_pred = []
    all_feat = []
    for pos in range(0,len(selected),args.capture_batch_size):
        part = selected[pos:pos+args.capture_batch_size]
        prediction, feature = predict_prompts(model, frozen, [r['prompt'] for r in part], args.device)
        all_pred.append(prediction)
        all_feat.append(feature)
    pred = np.concatenate(all_pred)
    features = np.concatenate(all_feat)
    if not np.allclose(features, data['features'][:len(selected)], atol=2e-4,rtol=2e-4):
        raise RuntimeError('LIVE_FUSION_FEATURE_CAPTURE_MISMATCH')
    expected = inference(model, {**data, 'features': data['features'][:len(selected)]},
                         exact[:len(selected)], 'fusion')
    if not np.allclose(pred, expected, atol=.25, rtol=1e-6):
        raise RuntimeError('LIVE_FUSION_INFERENCE_MISMATCH')
    score = benchmark.accuracy(pred, data['targets'][:len(selected)])
    print(json.dumps({'event':'numeric_fusion_live_smoke_pass', 'n':len(selected),
                      'cases':n, 'score':score,
                      'candidate':'T02_ONLY_NOT_PROMOTED'}), flush=True)
    return score


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('all','prepare','train','evaluate','live'))
    parser.add_argument('--previous', type=Path,
                        default=Path.home()/'NanoJev/runs/hawk_dove_t02_continuous_benchmark_v1')
    parser.add_argument('--pathway', type=Path,
                        default=Path.home()/'NanoJev/runs/hawk_dove_numeric_pathway_probe_v1')
    parser.add_argument('--root', type=Path,
                        default=Path.home()/'NanoJev/runs/hawk_dove_numeric_fusion_v1')
    parser.add_argument('--donor', type=Path,
                        default=Path.home()/'NanoJev/runs/hawk_dove_million_scientific_v2')
    parser.add_argument('--teacher', type=Path,
                        default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    parser.add_argument('--source', type=Path,
                        default=Path('runtime/game-transform-experiment/source-v4.pt'))
    parser.add_argument('--device', choices=('cuda','cpu'), default='cuda')
    parser.add_argument('--capture-batch-size',type=int,default=4)
    parser.add_argument('--live-cases',type=int,default=2)
    parser.add_argument('--epochs',type=int,default=60)
    parser.add_argument('--learning-rate',type=float,default=.0003)
    parser.add_argument('--semantic-scale',type=float,default=.01)
    parser.add_argument('--train-batch-size',type=int,default=128)
    parser.add_argument('--seed',type=int,default=20261019)
    parser.add_argument('--pathway-epochs',type=int,default=80)
    parser.add_argument('--pathway-lr',type=float,default=.001)
    parser.add_argument('--pathway-batch-size',type=int,default=128)
    parser.add_argument('--pathway-seed',type=int,default=20261019)
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    if not (1 <= args.epochs <= 2000 and 1 <= args.train_batch_size <= 4096 and
            1 <= args.capture_batch_size <= 32 and 1 <= args.live_cases <= 23 and
            0 < args.learning_rate <= .1 and 0 <= args.semantic_scale <= 1):
        raise ValueError('INVALID_FUSION_RUN_PARAMETERS')
    for attr in ('previous','pathway','root','donor','teacher','source'):
        setattr(args, attr, getattr(args,attr).expanduser().resolve())
    if args.stage in ('all','prepare'):
        prepare(args)
    if args.stage in ('all','train'):
        train(args)
    if args.stage in ('all','evaluate'):
        analyze(args)
    if args.stage in ('all','live'):
        live_smoke(args)


if __name__ == '__main__':
    main()
