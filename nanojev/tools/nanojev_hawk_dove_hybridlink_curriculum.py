#!/usr/bin/env python3
"""Independently runnable, gate-controlled HybridLink curriculum v2.

A new experiment; imports the unchanged v1 shared causal backbone. Each phase
has its own checkpoint, exact deterministic resume and separate evidence. Only
DEV is used for gates; TEST is never loaded into an evaluation function.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss

SCHEMA = 'nanojev.hawk-dove.hybridlink-curriculum.v2'
TRAIN_PHASES = ('format', 'alignment', 'joint')
ARMS = ('recurrent', 'static')


class CurriculumModel(base.HybridSequence):
    def __init__(self, reference, heads=4, layers=1):
        super().__init__(reference, heads, layers)
        # A genuine NEXT-product readout: h[1] predicts product 1, h[2]
        # predicts product 2, ... h[4] predicts product 4. The product
        # predicted from each prefix is NOT available at its target position.
        self.next_product = nn.Linear(self.width, 1)

    def teacher_state(self, features):
        teacher = self.teacher_tokens(features)
        state = self.backbone(torch.cat((self.seed(features), teacher), dim=1))
        return teacher, state

    def teacher_predictions(self, features):
        teacher, states = self.teacher_state(features)
        return (self.answer(states[:, -1]),
                self.next_product(states[:, 1:5]).squeeze(-1),
                self.value_probe(states[:, 2:]).squeeze(-1))


def configure_trainable(model, phase):
    parameters = base.trainable(model, phase)
    # No next-product loss in alignment; its probe and all other parts of the
    # backbone are immutable while HybridLink learns input-space alignment.
    model.next_product.requires_grad_(phase != 'alignment')
    return [p for p in model.parameters() if p.requires_grad]


def curriculum_loss(model, features, truth, phase, arm, scale, autonomy_weight):
    truth = base.normalized_truth(truth, scale)
    if phase == 'format':
        answer, next_values, consumed = model.teacher_predictions(features)
        products = base.v4.stage_values(features)
        next_mse = F.mse_loss(next_values, products)
        return (base.numeric_loss(answer, truth) +
                .3 * F.mse_loss(consumed, products) + 2. * next_mse)
    if phase == 'alignment':
        generated = model.teacher_first_pass(features, arm)
        with torch.no_grad():
            teacher = model.teacher_tokens(features)
        return base.latent_loss(generated, teacher)
    if phase == 'joint':
        old, _ = base.stage_loss(model, features, truth, 'joint', arm, scale, autonomy_weight)
        # Keep teacher comprehension AND prefix prediction trained during
        # the autonomous stage; this preserves the prerequisites explicitly.
        answer, next_values, consumed = model.teacher_predictions(features)
        products = base.v4.stage_values(features)
        return (old + .3 * base.numeric_loss(answer, truth) +
                .3 * F.mse_loss(consumed, products) +
                .5 * F.mse_loss(next_values, products))
    raise ValueError('CURRICULUM_UNKNOWN_PHASE')


def _product_stats(pred, actual):
    err = pred - actual
    var = ((actual - actual.mean()) ** 2).sum().clamp_min(1e-10)
    return {'normalized_mae': float(err.abs().mean()),
            'normalized_mse': float(err.square().mean()),
            'r2': float(1 - err.square().sum() / var),
            'per_step_mae': [float(v) for v in err.abs().mean(0)]}


@torch.no_grad()
def teacher_evaluation(model, split, scale, eval_batch_size, tolerance):
    model.eval()
    answers, next_pred, consumed_pred, products = [], [], [], []
    for x in split['features'].split(eval_batch_size):
        a, n, c = model.teacher_predictions(x)
        answers.append((a * scale).cpu())
        next_pred.append(n.cpu()); consumed_pred.append(c.cpu())
        products.append(base.v4.stage_values(x).cpu())
    actual = torch.cat(products)
    return {'teacher_xy': base.lab.strict_metrics(torch.cat(answers), split, tolerance),
            'next_product': _product_stats(torch.cat(next_pred), actual),
            'consumed_product': _product_stats(torch.cat(consumed_pred), actual)}


@torch.no_grad()
def alignment_evaluation(model, split, arm, scale, eval_batch_size, tolerance):
    model.eval()
    generated, targets, decoded, numeric, generated_xy = [], [], [], [], []
    for x in split['features'].split(eval_batch_size):
        proposal = model.teacher_first_pass(x, arm)
        teacher = model.teacher_tokens(x)
        result, _ = model.generated_second_pass(x, proposal)
        seed = model.seed(x)
        consumed = model.backbone(torch.cat((seed, proposal), dim=1))
        decoded.append(model.value_probe(consumed[:, 2:]).squeeze(-1).cpu())
        generated_xy.append((result * scale).cpu())
        generated.append(proposal.cpu()); targets.append(teacher.cpu())
        numeric.append(base.v4.stage_values(x).cpu())
    p = torch.cat(generated); t = torch.cat(targets)
    product = torch.cat(numeric); decoded = torch.cat(decoded)
    # A deterministic row derangement in the *target-embedding* comparison.
    # Unlike raw cosine alone, this establishes per-case distinguishability.
    n = len(p)
    if n < 2: raise RuntimeError('CURRICULUM_SHUFFLE_INSUFFICIENT_CASES')
    perm = torch.arange(n).roll(1)
    # Same-instance masks can have identical targets. A fixed half-set shift
    # is a stronger first control, checked against actual product values.
    differences = (product - product[perm]).abs().sum(dim=1)
    if bool((differences == 0).any()):
        # Explicit and repeatable: match each sample to a different product
        # signature, failing if there is no valid pairing at all.
        sig = product.tolist()
        buckets = {}
        for idx, item in enumerate(sig): buckets.setdefault(tuple(item), []).append(idx)
        count = len(sig); largest = max(map(len, buckets.values()))
        if 2 * largest > count: raise RuntimeError('CURRICULUM_DISTINCT_DONOR_UNAVAILABLE')
        order = sorted(range(count), key=lambda j: (sig[j], j))
        perm = torch.empty(count, dtype=torch.long)
        for pos, j in enumerate(order): perm[j] = order[(pos + largest) % count]
        if bool(((product - product[perm]).abs().sum(-1) == 0).any()):
            raise RuntimeError('CURRICULUM_DISTINCT_DONOR_INVALID')
    err = (p-t).square().mean().item()
    shuffle_err = (p-t[perm]).square().mean().item()
    return {'link_mse': err, 'shuffled_target_mse': shuffle_err,
            'link_over_shuffle_mse_ratio': err / max(shuffle_err, 1e-12),
            'generated_product': _product_stats(decoded, product),
            'generated_xy': base.lab.strict_metrics(torch.cat(generated_xy), split, tolerance),
            'cosine': float(F.cosine_similarity(p, t, dim=-1).mean())}


def format_gate(metrics, args):
    tests = {'teacher_xy_min_joint5': metrics['teacher_xy']['min_tier_joint5'] >= args.min_teacher,
             'next_product_mae': metrics['next_product']['normalized_mae'] <= args.max_next_mae,
             'next_product_r2': metrics['next_product']['r2'] >= args.min_next_r2,
             'consumed_product_mae': metrics['consumed_product']['normalized_mae'] <= args.max_consumed_mae}
    return {'status': 'PASS' if all(tests.values()) else 'FAIL', 'checks': tests, 'metrics': metrics}


def alignment_gate(metrics, args):
    tests = {'generated_product_mae': metrics['generated_product']['normalized_mae'] <= args.max_generated_mae,
             'link_beats_shuffled': metrics['link_over_shuffle_mse_ratio'] <= args.max_shuffle_ratio}
    return {'status': 'PASS' if all(tests.values()) else 'FAIL', 'checks': tests, 'metrics': metrics}


def make_identity(args, source, config):
    fields = ('seed', 'lr', 'heads', 'layers', 'batch_size', 'eval_batch_size',
              'tolerance', 'autonomy_weight', 'min_teacher', 'max_next_mae',
              'min_next_r2', 'max_consumed_mae', 'max_generated_mae', 'max_shuffle_ratio')
    return {'schema': SCHEMA, 'source_sha256': base.file_sha256(source),
            'source_identity': config['source_identity'],
            'matrix_hashes': config['matrix_hashes'],
            'options': {f: getattr(args, f) for f in fields},
            'data_policy': 'DEV gates only, disjoint matrix splits, no TEST evaluation',
            'inference_policy': 'six visible numeric features only; teacher products training only'}


def _load_checkpoint(path, identity, arm, phase, model):
    if not path.is_file():raise FileNotFoundError(f'CURRICULUM_CHECKPOINT_MISSING {path}')
    state = torch.load(path, map_location='cpu', weights_only=True)
    if (state.get('schema') != SCHEMA+'.checkpoint' or state.get('identity') != identity
            or state.get('arm') != arm or state.get('phase') != phase):
        raise RuntimeError('CURRICULUM_CHECKPOINT_IDENTITY_DRIFT')
    model.load_state_dict(state['model'], strict=True)
    return state


def _approved_gate(path, checkpoint, gate_schema):
    if not path.exists():raise RuntimeError('CURRICULUM_PREVIOUS_GATE_MISSING')
    approval = json.loads(path.read_text(encoding='utf-8'))
    if (approval.get('schema') != gate_schema or approval.get('status') != 'PASS'
            or approval.get('checkpoint_sha256') != base.file_sha256(checkpoint)):
        raise RuntimeError('CURRICULUM_PREVIOUS_GATE_NOT_ACCEPTED_OR_STALE')
    return approval


def _gate_path(root, arm, phase):
    return root / 'arms' / arm / phase / 'gate.json'


def _evaluate(model, split, args, scale, arm, phase):
    if phase == 'format':
        return teacher_evaluation(model, split, scale, args.eval_batch_size, args.tolerance)
    if phase == 'alignment':
        return alignment_evaluation(model, split, arm, scale, args.eval_batch_size, args.tolerance)
    results = teacher_evaluation(model, split, scale, args.eval_batch_size, args.tolerance)
    results['autonomous'] = base.score(model, split, scale, arm, 4, args.device,
                                       args.eval_batch_size, args.tolerance)
    results['teacher_generated'] = alignment_evaluation(model, split, arm, scale,
                                                         args.eval_batch_size, args.tolerance)
    return results


def _train_one(model, splits, scale, args, identity, root, arm, phase):
    stage_dir = root / 'arms' / arm / phase
    ck = stage_dir / 'latest.pt'
    # Upstream weights cannot be modified once a downstream phase has begun.
    # Downstream phases must always verify the exact accepted upstream bytes,
    # including on resume. This prevents silently continuing from stale proof.
    if phase == 'format' and (root / 'arms' / arm / 'alignment').exists():
        raise RuntimeError('CURRICULUM_FORMAT_LOCKED_BY_ALIGNMENT')
    if phase == 'alignment' and (root / 'arms' / arm / 'joint').exists():
        raise RuntimeError('CURRICULUM_ALIGNMENT_LOCKED_BY_JOINT')
    if phase != 'format':
        previous = 'format' if phase == 'alignment' else 'alignment'
        prev_ck = root / 'arms' / arm / previous / 'latest.pt'
        _approved_gate(_gate_path(root, arm, previous), prev_ck,
                       SCHEMA + '.' + ('format-verified' if previous == 'format' else 'alignment-verified'))
    if args.resume:
        if not ck.exists():raise RuntimeError('CURRICULUM_RESUME_CHECKPOINT_MISSING')
        resume = _load_checkpoint(ck, identity, arm, phase, model)
        index = resume['step']
        torch.set_rng_state(resume['torch_rng'])
        if resume.get('cuda_rng') is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(resume['cuda_rng'])
        rng = torch.Generator(device='cpu');rng.set_state(resume['sampling_rng'])
    else:
        if stage_dir.exists() and any(stage_dir.iterdir()):
            raise FileExistsError('CURRICULUM_PHASE_OVERWRITE_REFUSED')
        index = 0; resume = None
        if phase != 'format':
            previous = 'format' if phase == 'alignment' else 'alignment'
            prev_ck = root / 'arms' / arm / previous / 'latest.pt'
            _load_checkpoint(prev_ck, identity, arm, previous, model)
        rng = torch.Generator(device='cpu').manual_seed(args.seed + 941)
    if args.steps < index:raise ValueError('CURRICULUM_STEPS_BEHIND_CHECKPOINT')
    stage_dir.mkdir(parents=True, exist_ok=True)
    params = configure_trainable(model, phase)
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.)
    if resume is not None:opt.load_state_dict(resume['optimizer'])
    train = splits['train'];count = len(train['rows'])
    if count == 0:raise RuntimeError('CURRICULUM_TRAIN_EMPTY')
    last = None
    for step in range(index+1, args.steps+1):
        model.train()
        indices = torch.randint(count, (args.batch_size,), generator=rng).to(args.device)
        features = train['features'].index_select(0, indices)
        targets = train['numeric'].index_select(0, indices)
        loss = curriculum_loss(model, features, targets, phase, arm, scale, args.autonomy_weight)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('CURRICULUM_NONFINITE_LOSS')
        opt.zero_grad(set_to_none=True); loss.backward()
        nn.utils.clip_grad_norm_(params, 1., error_if_nonfinite=True)
        opt.step()
        if step % args.probe_every == 0 or step == args.steps:
            last = _evaluate(model, splits['dev'], args, scale, arm, phase)
            marker = ('teacher=' + str(round(last['teacher_xy']['min_tier_joint5'], 6)) +
                      ' nextMAE=' + str(round(last['next_product']['normalized_mae'], 6))
                      if phase == 'format' else
                      'productMAE=' + str(round(last['generated_product']['normalized_mae'], 6)) +
                      ' shuffleRatio=' + str(round(last['link_over_shuffle_mse_ratio'], 6))
                      if phase == 'alignment' else
                      'autonomous=' + str(round(last['autonomous']['min_tier_joint5'], 6)) +
                      ' teacher=' + str(round(last['teacher_xy']['min_tier_joint5'], 6)))
            print(f'CURRICULUM_TRAIN arm={arm} phase={phase} step={step} '
                  f'loss={float(loss.detach()):.6f} {marker}', flush=True)
            gauss._atomic_save(ck, {'schema': SCHEMA+'.checkpoint', 'identity': identity,
                                    'arm': arm, 'phase': phase, 'step': step,
                                    'model': model.state_dict(), 'optimizer': opt.state_dict(),
                                    'sampling_rng': rng.get_state(),
                                    'torch_rng': torch.get_rng_state(),
                                    'cuda_rng': torch.cuda.get_rng_state_all()
                                                if torch.cuda.is_available() else None})
    if last is None:last = _evaluate(model, splits['dev'], args, scale, arm, phase)
    if phase == 'alignment':
        gate = alignment_gate(last, args)
        gauss._atomic_json(_gate_path(root, arm, phase),
                           {'schema': SCHEMA+'.alignment-verified', 'status':gate['status'],
                            'checkpoint_sha256':base.file_sha256(ck),'checks':gate['checks'],
                            'metrics':gate['metrics']})
        print('CURRICULUM_ALIGNMENT_GATE='+json.dumps({'arm':arm,'status':gate['status'],
                                                       'checks':gate['checks']}), flush=True)
        # The caller must see a *gate outcome*, not an unlabelled metric
        # dictionary: FAILED alignment must also exit nonzero.
        last = {'status':gate['status'], 'checks':gate['checks'], 'metrics':last}
    if phase == 'joint':
        gauss._atomic_json(stage_dir / 'result.json',
                           {'schema': SCHEMA+'.joint-result', 'identity': identity, 'arm': arm,
                            'checkpoint_sha256':base.file_sha256(ck), 'metrics':last})
    return last


def _verify_one(model, splits, scale, args, root, identity, arm):
    checkpoint = root / 'arms' / arm / 'format' / 'latest.pt'
    _load_checkpoint(checkpoint, identity, arm, 'format', model)
    metrics = teacher_evaluation(model, splits['dev'], scale, args.eval_batch_size, args.tolerance)
    gate = format_gate(metrics, args)
    location = _gate_path(root, arm, 'format')
    gauss._atomic_json(location, {'schema':SCHEMA+'.format-verified',
                                  'status':gate['status'],
                                  'checkpoint_sha256':base.file_sha256(checkpoint),
                                  'checks':gate['checks'], 'metrics':metrics})
    print('CURRICULUM_FORMAT_GATE='+json.dumps({'arm':arm, 'status':gate['status'],
                                                'checks':gate['checks']}),flush=True)
    return gate


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CURRICULUM_CUDA_UNAVAILABLE')
    if (args.steps < 1 or args.probe_every < 1 or args.batch_size < 1 or
            args.eval_batch_size < 1 or args.cpu_threads < 1 or args.heads < 1 or
            args.layers < 1 or not 0 < args.lr < 1 or not 0 < args.tolerance < 1 or
            not 0 <= args.autonomy_weight <= 1 or not 0 <= args.min_teacher <= 1 or
            not 0 <= args.min_next_r2 <= 1 or not 0 < args.max_next_mae < 2 or
            not 0 < args.max_consumed_mae < 2 or not 0 < args.max_generated_mae < 2 or
            not 0 < args.max_shuffle_ratio < 1):
        raise ValueError('CURRICULUM_BAD_OPTIONS')
    arms = args.arms.split(',')
    if len(set(arms)) != len(arms) or any(a not in ARMS for a in arms):
        raise ValueError('CURRICULUM_BAD_ARMS')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    source = Path(args.reference_checkpoint).expanduser().resolve()
    sha = base.file_sha256(source)
    reference, splits, _, config = base.load_source(source, args.device)
    if reference.hidden % args.heads:raise ValueError('CURRICULUM_HEAD_DIM_MISMATCH')
    identity = make_identity(args, source, config)
    root = Path(args.output).expanduser().resolve()
    if (root/'config.json').exists():
        if json.loads((root/'config.json').read_text(encoding='utf-8')) != identity:
            raise RuntimeError('CURRICULUM_CONFIG_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('CURRICULUM_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True, exist_ok=True)
        gauss._atomic_json(root/'config.json', identity)
    scale = base.xy_scale(config['source_identity']['payoff_bound'])
    report = {}
    for arm in arms:
        torch.manual_seed(args.seed)
        model = CurriculumModel(reference, args.heads, args.layers).to(args.device)
        if args.phase == 'verify':
            result = _verify_one(model, splits, scale, args, root, identity, arm)
        else:
            result = _train_one(model, splits, scale, args, identity, root, arm, args.phase)
        report[arm] = result
    if base.file_sha256(source) != sha:
        raise RuntimeError('CURRICULUM_SOURCE_MUTATED')
    print('CURRICULUM_SOURCE_IMMUTABLE=TRUE TEST_UNTOUCHED=TRUE', flush=True)
    if args.phase in ('verify', 'alignment') and any(
            value.get('status') == 'FAIL' for value in report.values()):
        raise RuntimeError('CURRICULUM_' + args.phase.upper() + '_GATE_FAILED')
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=(*TRAIN_PHASES,'verify'),required=True)
    p.add_argument('--steps',type=int,default=600,help='Target step WITHIN this phase; --resume --steps bigger extends it')
    p.add_argument('--resume',action='store_true',help='Resume same phase to --steps, preserving optimizer and RNG')
    p.add_argument('--arms',default='recurrent',help='recurrent, static or recurrent,static')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--probe-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--autonomy-weight',type=float,default=.25)
    p.add_argument('--min-teacher',type=float,default=.8)
    p.add_argument('--max-next-mae',type=float,default=.08)
    p.add_argument('--min-next-r2',type=float,default=.8)
    p.add_argument('--max-consumed-mae',type=float,default=.08)
    p.add_argument('--max-generated-mae',type=float,default=.10)
    p.add_argument('--max-shuffle-ratio',type=float,default=.5)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))

if __name__ == '__main__':
    main()
