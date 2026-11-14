#!/usr/bin/env python3
"""Resume a completed ROM-mix v2 cycle with one supervised routing adapter.

Deliberately narrow changes to the v2 champion cutover:
  * unfreeze only ``tinystories_residual.option_question.weight`` among the
    original routing-frozen weights, with its own AdamW group (LR 1e-5);
  * use routing supervision at 0.02 on ROM (including ROM pair questions),
    retaining 0.25 on natural examples and all existing field supervision;
  * migrate the incumbent two-group AdamW state losslessly to a three-group
    optimizer, leaving the new adapter without moments until its first update;
  * preserve all v2 weights, parent, PREDEV, selection and rip/promote logic.

Run ONLY after a completed v2 cycle, using --resume.  No active run may share
its output directory. The full cutover experiment remains in the same v2 dir.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parent
V2_PATH = TOOLS / 'nanojev_three_backbone_clef_tinystories_rom_mix_v2_train.py'
TARGET_PARAMETER = 'tinystories_residual.option_question.weight'
TARGET_LR = 1e-5
ROM_ROUTING_WEIGHT = 0.02
NATURAL_ROUTING_WEIGHT = 0.25
NATURAL_MAX_LOSS_REGRESSION = 0.02
NATURAL_MAX_ACCURACY_DROP = 0.02
TRAIN_PHASE = 'option-question-routing-grad-v1'
ACTIVE_REPRESENTATION = ContextVar('clef_rom_mix_training_representation', default=None)


def _load_module(name: str, path: Path):
    if not path.is_file():
        raise RuntimeError(f'required installed v2 cutover missing: {path}')
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot import {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _is_rom_source_question(question, cutover) -> bool:
    name = str(question.question_id)
    return bool(cutover.is_rom_question(question) or '::rom::pair-' in name)


def _assert_v2_training_contract(cutover) -> None:
    for name in ('build_mixed_frozen_training_cache', 'train_mixed_population',
                 'evaluate_mixed_population'):
        fn = getattr(cutover, name, None)
        if fn is None or not callable(fn):
            raise RuntimeError(f'installed v2 cutover missing {name}')
    body = inspect.getsource(cutover.train_mixed_population)
    if body.count('champion.compose_training_evidence(') != 3:
        raise RuntimeError('training evidence call layout changed: cannot safely scope ROM loss')
    if body.count('champion.structured_supervision_loss(') != 3:
        raise RuntimeError('structured supervision call layout changed: cannot safely scope ROM loss')
    if 'optimizer.step()' not in body or 'clip_grad_norm_' not in body:
        raise RuntimeError('v2 training optimizer/clip layout changed')
    if 'pair_question' not in body or 'question=question' not in body:
        raise RuntimeError('ROM/natural consensus training layout changed')


def configure_targeted_routing(head, original_freeze) -> dict[str, Any]:
    report = dict(original_freeze(head))
    named = dict(head.named_parameters())
    param = named.get(TARGET_PARAMETER)
    if param is None:
        raise RuntimeError(f'published CLEF head missing adapter: {TARGET_PARAMETER}')
    if param.requires_grad:
        raise RuntimeError('targeted adapter was unexpectedly trainable before unfreeze')
    if not param.numel():
        raise RuntimeError('targeted routing adapter is empty')
    param.requires_grad_(True)
    unfrozen_routing = [name for name, p in named.items()
                        if name != TARGET_PARAMETER and name in report['frozen_names']
                        and p.requires_grad]
    if unfrozen_routing:
        raise RuntimeError(f'unexpected routing unfreezes: {unfrozen_routing}')
    if sum(p.requires_grad for name, p in named.items() if name == TARGET_PARAMETER) != 1:
        raise RuntimeError('target routing adapter not trainable')
    report['frozen_names'].remove(TARGET_PARAMETER)
    report['trainable_names'].append(TARGET_PARAMETER)
    report['frozen_parameters'] -= int(param.numel())
    report['trainable_parameters'] += int(param.numel())
    report['frozen_parameter_tensors'] -= 1
    report['trainable_parameter_tensors'] += 1
    report['targeted_routing_adapter'] = TARGET_PARAMETER
    report['targeted_unfrozen_parameters'] = int(param.numel())
    report['targeted_routing_phase'] = TRAIN_PHASE
    return report


def build_targeted_optimizer(*, torch, head, tinystories_lm, args):
    named = dict(head.named_parameters())
    adapter = named.get(TARGET_PARAMETER)
    if adapter is None or not adapter.requires_grad:
        raise RuntimeError('target adapter must be unfrozen before optimizer construction')
    if any(p.requires_grad for p in tinystories_lm.parameters()):
        raise RuntimeError('TinyStories backbone unexpectedly trainable')
    mature = []
    residual = []
    routing = []
    for name, p in head.named_parameters():
        if not p.requires_grad:
            continue
        if name == TARGET_PARAMETER:
            routing.append(p)
        elif name.startswith('tinystories_residual.'):
            residual.append(p)
        else:
            mature.append(p)
    if len(routing) != 1 or not mature or not residual:
        raise RuntimeError('routing optimizer partition incomplete')
    groups = [
        {'params': mature, 'lr': float(args.clef_head_lr),
         'weight_decay': float(args.weight_decay), 'group_name': 'clef_mature_head'},
        {'params': residual, 'lr': float(args.head_lr),
         'weight_decay': float(args.weight_decay), 'group_name': 'tinystories_residual_taps'},
        {'params': routing, 'lr': TARGET_LR,
         'weight_decay': float(args.weight_decay), 'group_name': 'rom_routing_option_question'},
    ]
    return torch.optim.AdamW(groups, foreach=False)


def migrate_optimizer(*, torch, optimizer, source_state, original_loader, champion):
    source_groups = list(source_state.get('param_groups') or [])
    target_groups = list(optimizer.state_dict().get('param_groups') or [])
    if len(source_groups) == 3:
        # Exact 3-group resume (after the targeted fork's first promotion).
        if [g.get('group_name') for g in source_groups] != [
            'clef_mature_head', 'tinystories_residual_taps', 'rom_routing_option_question'
        ]:
            raise RuntimeError('three-group optimizer schema mismatch')
        return original_loader(torch=torch, optimizer=optimizer, source_state=source_state)
    if len(source_groups) != 2 or len(target_groups) != 3:
        raise RuntimeError('unsupported optimizer groups for targeted routing migration')
    source_names = [g.get('group_name') for g in source_groups]
    if source_names != ['clef_mature_head', 'tinystories_residual_taps']:
        raise RuntimeError(f'v2 source optimizer group identity drifted: {source_names}')
    target_names = [g.get('group_name') for g in target_groups]
    if target_names != ['clef_mature_head', 'tinystories_residual_taps', 'rom_routing_option_question']:
        raise RuntimeError(f'target optimizer group identity drifted: {target_names}')
    for old, new in zip(source_groups, target_groups[:2]):
        if len(old['params']) != len(new['params']):
            raise RuntimeError(
                f'optimizer parameter count mismatch for {old["group_name"]}: '
                f'{len(old["params"])} vs {len(new["params"])}'
            )
    if len(target_groups[2]['params']) != 1:
        raise RuntimeError('routing optimizer group must contain exactly one weight tensor')
    restored = optimizer.state_dict()
    remapped_state = {}
    for old, new in zip(source_groups, target_groups[:2]):
        for old_id, new_id in zip(old['params'], new['params']):
            if old_id in source_state['state']:
                remapped_state[new_id] = source_state['state'][old_id]
    groups = [dict(group) for group in target_groups]
    for i, old in enumerate(source_groups):
        for key, value in old.items():
            if key not in {'params', 'group_name', 'lr'}:
                groups[i][key] = value
    restored['state'] = remapped_state
    restored['param_groups'] = groups
    optimizer.load_state_dict(restored)
    champion.base.optimizer_to_cuda(optimizer)
    return {
        'mode': 'v2-two-groups-to-routing-three-groups',
        'migrated_state_entries': len(remapped_state),
        'fresh_routing_parameters': 1,
        'old_groups_preserved': source_names,
        'optimizer_reset': False,
    }


def adjust_supervision_for_representation(parts: dict[str, Any], representation: str):
    """Downweight only ROM routing; preserve all field loss and natural routing."""
    if representation not in ('rom', 'natural'):
        raise RuntimeError(f'unscoped training representation: {representation!r}')
    if representation == 'rom':
        parts = dict(parts)
        parts['loss'] = (
            parts['loss']
            - NATURAL_ROUTING_WEIGHT * parts['routing_loss']
            + ROM_ROUTING_WEIGHT * parts['routing_loss']
        )
        parts['routing_weight'] = ROM_ROUTING_WEIGHT
    return parts


def natural_retention_accepts(incumbent: dict, candidate: dict) -> bool:
    """Prevent mixed loss from hiding material natural-language regression."""
    incumbent_overall = (incumbent or {}).get('overall') or {}
    candidate_overall = (candidate or {}).get('overall') or {}
    if not incumbent_overall or not candidate_overall:
        raise RuntimeError('natural retention metrics missing from mixed PREDEV')
    return (
        float(candidate_overall['mean_loss'])
        <= float(incumbent_overall['mean_loss']) + NATURAL_MAX_LOSS_REGRESSION
        and float(candidate_overall['accuracy'])
        >= float(incumbent_overall['accuracy']) - NATURAL_MAX_ACCURACY_DROP
    )


def _state_guard(output_dir: Path) -> dict[str, Any]:
    experiment_path = output_dir / 'experiment.json'
    state_path = output_dir / 'training_state.json'
    if not experiment_path.is_file() or not state_path.is_file():
        raise RuntimeError('requires an existing completed v2 run with experiment.json and training_state.json')
    experiment = json.loads(experiment_path.read_text(encoding='utf-8'))
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if experiment.get('trainer_variant') != 'three-backbone-clef-tinystories-additive-rom-mix-v2':
        raise RuntimeError(f'not a v2 ROM-mix experiment: {experiment.get("trainer_variant")!r}')
    if int(state.get('cycle', 0)) < 137:
        raise RuntimeError('v2 cycle 137 must finish before targeted routing unfreeze')
    last_cycle = int(state['cycle'])
    if not (output_dir / 'cycles' / f'cycle-{last_cycle:06d}' / 'metrics.json').is_file():
        raise RuntimeError('last committed v2 cycle metrics are missing')
    next_cycle_dir = output_dir / 'cycles' / f'cycle-{last_cycle+1:06d}'
    if next_cycle_dir.exists():
        raise RuntimeError(f'next cycle already has in-progress data: {next_cycle_dir}; stop trainer and reconcile first')
    checkpoint = Path(state.get('best_checkpoint') or '')
    if not checkpoint.is_dir() or not (checkpoint / 'optimizer.pt').is_file():
        raise RuntimeError(f'committed v2 optimizer checkpoint missing: {checkpoint}')
    phase = (experiment.get('contract') or {}).get('targeted_routing_phase')
    if phase not in (None, TRAIN_PHASE):
        raise RuntimeError(f'unsupported prior targeted routing phase {phase!r}')
    return {'experiment': experiment, 'state': state, 'experiment_path': experiment_path}


def install_targeted_routing(cutover, *, persist_contract_path: Path | None = None) -> None:
    _assert_v2_training_contract(cutover)
    champion = cutover.champion
    if getattr(cutover, '_targeted_routing_installed', False):
        return
    original_freeze = champion.configure_routing_freeze
    original_optimizer_loader = champion._load_optimizer_state_with_unfrozen_head_compat
    original_compose = champion.compose_training_evidence
    original_structured = champion.structured_supervision_loss
    original_evaluate = cutover.evaluate_mixed_population
    original_choose = champion.choose_predev_winner
    retention_baseline: dict[str, Any] = {}
    if 'option_question' not in champion.ROUTING_RESIDUAL_FIELDS:
        raise RuntimeError('champion no longer identifies option_question as a routing residual')
    def freeze(head):
        return configure_targeted_routing(head, original_freeze)
    def build_optimizer(*, torch, head, tinystories_lm, args):
        return build_targeted_optimizer(torch=torch, head=head, tinystories_lm=tinystories_lm, args=args)
    def load_optimizer(*, torch, optimizer, source_state):
        result = migrate_optimizer(
            torch=torch, optimizer=optimizer, source_state=source_state,
            original_loader=original_optimizer_loader, champion=champion,
        )
        if persist_contract_path is not None and result.get('mode') == 'v2-two-groups-to-routing-three-groups':
            experiment = json.loads(persist_contract_path.read_text(encoding='utf-8'))
            contract = experiment.setdefault('contract', {})
            contract.update({
                'targeted_routing_phase': TRAIN_PHASE,
                'targeted_routing_adapter': TARGET_PARAMETER,
                'targeted_routing_lr': TARGET_LR,
                'rom_routing_supervision_weight': ROM_ROUTING_WEIGHT,
                'natural_routing_supervision_weight': NATURAL_ROUTING_WEIGHT,
                'natural_max_loss_regression': NATURAL_MAX_LOSS_REGRESSION,
                'natural_max_accuracy_drop': NATURAL_MAX_ACCURACY_DROP,
                'optimizer_migration': result['mode'],
                'routing_freeze_schema': champion.ROUTING_FREEZE_SCHEMA,
            })
            champion.smoke.atomic_json(persist_contract_path, experiment)
        return result
    def compose_training_evidence(*, torch, bundles, question, frozen_rows, args, logger):
        ACTIVE_REPRESENTATION.set('rom' if _is_rom_source_question(question, cutover) else 'natural')
        return original_compose(
            torch=torch, bundles=bundles, question=question,
            frozen_rows=frozen_rows, args=args, logger=logger,
        )
    def supervision(*, torch, supervision, gold_index, args):
        representation = ACTIVE_REPRESENTATION.get()
        if representation not in ('rom', 'natural'):
            raise RuntimeError('structured supervision outside scoped training question')
        if abs(float(args.routing_supervision_weight) - NATURAL_ROUTING_WEIGHT) > 1e-9:
            raise RuntimeError('base natural routing weight drifted: expected 0.25')
        parts = original_structured(torch=torch, supervision=supervision, gold_index=gold_index, args=args)
        return adjust_supervision_for_representation(parts, representation)
    champion.ROUTING_FREEZE_SCHEMA = TRAIN_PHASE
    def mixed_eval(*, torch, head, bundles, questions, args, logger, phase):
        result = original_evaluate(
            torch=torch, head=head, bundles=bundles, questions=questions,
            args=args, logger=logger, phase=phase,
        )
        if str(phase).endswith('-incumbent-mixed-predev'):
            representation = result.get('representation') or {}
            natural = representation.get('natural')
            if natural is None:
                raise RuntimeError('incumbent mixed PREDEV missing natural metrics')
            retention_baseline.clear()
            retention_baseline.update({'natural': natural, 'logger': logger, 'phase': str(phase)})
        return result
    def choose_with_retention(*, incumbent_accuracy, incumbent_loss, attempts, use_loss=False):
        if not retention_baseline:
            raise RuntimeError('cannot promote before incumbent natural PREDEV baseline')
        eligible = []
        for attempt in attempts:
            natural = (attempt.get('predev_representation') or {}).get('natural')
            passes = natural_retention_accepts(retention_baseline['natural'], natural)
            retention_baseline['logger'].emit(
                'clef_rom_mix_natural_retention_check',
                reuse_depth=attempt.get('reuse_depth'), accepted=passes,
                max_loss_regression=NATURAL_MAX_LOSS_REGRESSION,
                max_accuracy_drop=NATURAL_MAX_ACCURACY_DROP,
                incumbent_natural_loss=retention_baseline['natural']['overall']['mean_loss'],
                candidate_natural_loss=natural['overall']['mean_loss'],
                incumbent_natural_accuracy=retention_baseline['natural']['overall']['accuracy'],
                candidate_natural_accuracy=natural['overall']['accuracy'],
            )
            if passes:
                eligible.append(attempt)
        return original_choose(
            incumbent_accuracy=incumbent_accuracy,
            incumbent_loss=incumbent_loss,
            attempts=eligible, use_loss=use_loss,
        )
    champion.configure_routing_freeze = freeze
    champion.build_optimizer = build_optimizer
    champion._load_optimizer_state_with_unfrozen_head_compat = load_optimizer
    champion.compose_training_evidence = compose_training_evidence
    champion.structured_supervision_loss = supervision
    cutover.evaluate_mixed_population = mixed_eval
    champion.choose_predev_winner = choose_with_retention
    cutover._targeted_routing_installed = True


def gradient_self_test(cutover):
    """Actual CLEF forward/backward (synthetic evidence; no HF download or CUDA)."""
    import torch
    champion = cutover.champion
    torch.manual_seed(13)
    head, _ = champion.build_layer_tap_head(
        torch=torch, hidden_sizes={'qwen': 1024, 'pythia': 512, 'tinystories': 768}
    )
    report = champion.configure_routing_freeze(head)
    assert report['targeted_unfrozen_parameters'] == 1_179_648
    assert sum(p.requires_grad for p in head.parameters()) > 0
    head.eval()
    evidence = {}
    for label, width in [('qwen', 1024), ('pythia', 512), ('tinystories', 768)]:
        evidence[label] = {
            'memory': torch.randn(3, width),
            'option_context': torch.randn(2, width),
            'option_predictor': torch.randn(2, width),
            'option_terminal': torch.randn(2, width),
            'option_question': torch.randn(2, width),
            'option_lexical': torch.randn(2, width),
            'option_logp': torch.randn(2),
            'global': torch.randn(width),
        }
    for field in champion.TINYSTORIES_RESIDUAL_FIELDS:
        shape = evidence['tinystories'][field].shape
        evidence['tinystories']['residual_source_' + field] = torch.randn(*shape[:-1], 1536)
    _, supervision = head.forward_with_supervision(evidence)
    routing_logits = supervision['routing_logits']
    routing_loss, _, _ = champion.smoke.loss_parts(torch, routing_logits, 0)
    (ROM_ROUTING_WEIGHT * routing_loss).backward()
    selected = dict(head.named_parameters())[TARGET_PARAMETER]
    grad = selected.grad
    actual_grad = None if grad is None else float(grad.detach().float().norm().item())
    all_frozen_clear = all(p.grad is None for name, p in head.named_parameters()
                           if name != TARGET_PARAMETER and champion.is_routing_parameter_name(name))
    result = {
        'schema': 'nanojev-rom-mix-targeted-routing-gradient-test-v1',
        'adapter': TARGET_PARAMETER,
        'adapter_parameters': selected.numel(),
        'gradient_norm': actual_grad,
        'routing_frozen_remainder_has_no_grads': all_frozen_clear,
        'success': (actual_grad is not None and actual_grad > 0 and
                    torch.isfinite(grad).all().item() and all_frozen_clear),
    }
    del head, evidence
    if not result['success']:
        raise RuntimeError(f'targeted routing gradient test failed: {result}')
    return result


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--gradient-self-test', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-cycles', type=int, default=1)
    parser.add_argument('--output-dir', default=None)
    options = parser.parse_args(raw)

    v2 = _load_module('nanojev_rom_mix_v2_targeted_routing', V2_PATH)
    cutover = v2.load_module('nanojev_rom_mix_v1_targeted_routing', v2.PARENT_SCRIPT)
    v2.install_cutover_adapter(cutover)
    if options.self_test or options.gradient_self_test:
        install_targeted_routing(cutover)
        checks = {
            'version': TRAIN_PHASE,
            'adapter': TARGET_PARAMETER,
            'adapter_lr': TARGET_LR,
            'rom_routing_weight': ROM_ROUTING_WEIGHT,
            'natural_routing_weight': NATURAL_ROUTING_WEIGHT,
            'natural_max_loss_regression': NATURAL_MAX_LOSS_REGRESSION,
            'natural_max_accuracy_drop': NATURAL_MAX_ACCURACY_DROP,
            'backbones_frozen_by_champion': True,
        }
        if options.gradient_self_test:
            checks['gradient_test'] = gradient_self_test(cutover)
        print(json.dumps({'ok': True, 'checks': checks}, sort_keys=True), flush=True)
        return 0

    if not options.resume:
        parser.error('targeted routing unfreeze requires --resume of a completed v2 cycle')
    if options.max_cycles <= 0:
        parser.error('--max-cycles must be finite and positive for first targeted migration')
    output_dir = Path(options.output_dir or cutover.DEFAULT_OUTPUT).expanduser()
    guarded = _state_guard(output_dir)
    install_targeted_routing(cutover, persist_contract_path=guarded['experiment_path'])
    forwarded = ['--resume', '--max-cycles', str(options.max_cycles), '--output-dir', str(output_dir)]
    return cutover.main(forwarded)


if __name__ == '__main__':
    raise SystemExit(main())
