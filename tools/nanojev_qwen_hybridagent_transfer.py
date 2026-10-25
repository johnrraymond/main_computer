#!/usr/bin/env python3
"""Read-only paired transfer experiment: initialized vs trained HybridLink.

Qwen3-0.6B-Base generates its OWN latent recurrent inputs; no answer or
teacher-supplied latent vector is given to Qwen. Gold values are used only
AFTER the model completes its answer, to score the paired results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import torch

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation

SCHEMA = 'nanojev.qwen3-06b.hybridlink-heldout-transfer.v1'
DEFAULT_RUN = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_base_hybridlink_transfer_v1'
BASELINE = hybrid.DEFAULT_OUTPUT
ACTIVATION = activation.DEFAULT_RUN


def cases():
    """Fixed beforehand; none of these operand magnitudes occur in activation.

    Direct tests have the signed-zero-line invariant A+(-A+d)=d. Composed
    tests A+(-A+u)+v=u+v prevent the target always being a copied literal.
    All cancellation magnitudes are unseen in train/dev and include negative A.
    """
    specs = [
        ('direct', 100003, -1, 0), ('direct', -200003, 0, 0),
        ('direct', 999983, 1, 0), ('direct', -131071, -3, 0),
        ('direct', 2000000, 0, 0), ('direct', 773117, 5, 0),
        ('direct', -1999997, 2, 0), ('direct', 104729, -7, 0),
        ('composed', 1000003, 4, -4), ('composed', -999979, 2, -5),
        ('composed', 400009, -3, 8), ('composed', -524287, 6, 1),
        ('composed', 777777, -9, 9), ('composed', -1000009, 11, -8),
        ('composed', 131101, -2, -3), ('composed', -654323, 7, -7),
    ]
    result = []
    for kind, a, u, v in specs:
        expr = (f'({a}) + (-({a}) + ({u}))' if kind == 'direct' else
                f'({a}) + (-({a}) + ({u})) + ({v})')
        result.append({'id': f'holdout_{len(result):02d}', 'family': kind,
                       'a': a, 'u': u, 'v': v, 'answer': u+v,
                       'sign': 'zero' if u+v == 0 else ('positive' if u+v > 0 else 'negative'),
                       'expression': expr,
                       'prompt': ('Compute the expression. Return only the final integer.\n'
                                  f'Expression: {expr}\nReasoning:')})
    assert len({x['a'] for x in result}) == len(result), 'TRANSFER_CASE_MAGNITUDE_REUSED'
    assert {'zero','positive','negative'} <= {x['sign'] for x in result}, 'TRANSFER_MISSING_SIGN_TIER'
    assert {x['family'] for x in result} == {'direct','composed'}, 'TRANSFER_MISSING_COMPOSITION'
    train, dev = activation.examples()
    observed = {abs(x['a']) for x in train+dev}
    assert all(abs(x['a']) not in observed and abs(x['a']) >= 1000 for x in result), (
        'TRANSFER_HOLDOUT_MAGNITUDE_CONTAMINATED')
    assert all(x['answer'] == x['u']+x['v'] for x in result), 'TRANSFER_GOLD_LABEL_DRIFT'
    return result


def case_fingerprint(samples):
    return hashlib.sha256(json.dumps(samples, sort_keys=True).encode('utf-8')).hexdigest()


def verify_training_artifacts(baseline: Path, activation_dir: Path, cfg, *,
                              best_filename='best_head.pt'):
    """Fail closed on mismatched heads or activation runs; never writes them."""
    cfg_file = activation_dir / 'run_config.json'
    result_file = activation_dir / 'result.json'
    head_file = activation_dir / best_filename
    assert best_filename in ('best_head.pt', 'last.pt'), 'TRANSFER_HEAD_FILE_NOT_ALLOWED'
    assert baseline.resolve() != activation_dir.resolve(), 'TRANSFER_INPUT_PATH_COLLISION'
    assert cfg['schema'] == hybrid.SCHEMA, 'TRANSFER_BASELINE_NOT_V2'
    assert cfg['base_model'] == hybrid.MODEL_ID == 'Qwen/Qwen3-0.6B-Base', (
        'TRANSFER_REQUIRES_BASE_MODEL')
    assert cfg['training_status'] == 'untrained', 'TRANSFER_BASELINE_NOT_UNTRAINED'
    run_cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
    result = json.loads(result_file.read_text(encoding='utf-8'))
    assert run_cfg['schema'] == activation.SCHEMA, 'TRANSFER_TRAINING_SCHEMA_DRIFT'
    assert run_cfg['adapter_schema'] == hybrid.SCHEMA, 'TRANSFER_HEAD_SCHEMA_DRIFT'
    assert run_cfg['data_version'] == activation.DATA_VERSION, 'TRANSFER_TRAINING_DATA_VERSION_DRIFT'
    assert run_cfg['data_hash'] == activation.data_fingerprint(), 'TRANSFER_TRAINING_DATA_CHANGED'
    assert run_cfg['baseline_dir'] == str(baseline.resolve()), 'TRANSFER_TRAINING_BASE_PATH_DRIFT'
    assert run_cfg['baseline_adapter_hash'] == activation.file_hash(baseline / 'adapter.pt'), (
        'TRANSFER_BASELINE_WEIGHTS_CHANGED')
    assert run_cfg['baseline_config_hash'] == activation.file_hash(baseline / 'adapter_config.json'), (
        'TRANSFER_BASELINE_CONFIG_CHANGED')
    assert run_cfg['base_model'] == cfg['base_model'] and run_cfg['revision'] == cfg['revision'], (
        'TRANSFER_MODEL_PROVENANCE_DRIFT')
    assert run_cfg['stage'] == 'teacher_forced_hybridlink_activation_only', 'TRANSFER_WRONG_TRAINING_STAGE'
    assert result['status'] == 'PASS_MECHANICS_ONLY' and result['frozen_backbone'] is True, (
        'TRANSFER_ACTIVATION_NOT_COMPLETE')
    assert result['backbone_hash_preserved'] and result['baseline_mutations'] == 0, (
        'TRANSFER_TRAINING_MUTATED_BASELINE')
    checkpoint = torch.load(head_file, map_location='cpu', weights_only=True)
    assert checkpoint.get('schema') == activation.SCHEMA, 'TRANSFER_TRAINED_HEAD_SCHEMA_DRIFT'
    assert 'head' in checkpoint and 'step' in checkpoint, 'TRANSFER_TRAINED_HEAD_INCOMPLETE'
    assert int(checkpoint['step']) >= 1 and int(checkpoint['step']) <= int(result['step']), (
        'TRANSFER_TRAINED_HEAD_INVALID_STEP')
    if best_filename == 'best_head.pt':
        assert checkpoint['training_stage'] == 'activation_only', 'TRANSFER_WRONG_CHECKPOINT_STAGE'
        assert checkpoint['adaptive_switching_certified'] is False, 'TRANSFER_UNJUSTIFIED_ADAPTIVE_CERT'
        assert math.isclose(float(checkpoint['dev']['loss']), float(result['best_dev_loss']),
                            abs_tol=1e-5, rel_tol=1e-5), 'TRANSFER_BEST_DEV_MISMATCH'
    else:
        assert checkpoint['config_sha256'] == hashlib.sha256(
            json.dumps(run_cfg, sort_keys=True).encode('utf-8')).hexdigest(), (
            'TRANSFER_LAST_CHECKPOINT_CONFIG_MISMATCH')
    assert isinstance(checkpoint['head'], dict) and checkpoint['head'], 'TRANSFER_EMPTY_TRAINED_HEAD'
    assert all(isinstance(v, torch.Tensor) and torch.isfinite(v).all()
               for v in checkpoint['head'].values()), 'TRANSFER_TRAINED_HEAD_NONFINITE'
    return checkpoint, run_cfg, result


def parse_number(text: str):
    """Accept only a standalone signed integer on the first answer line."""
    m = re.match(r'^\s*([+-]?[0-9]+)[ \t]*(?:\r?\n|\Z)', text)
    return int(m.group(1)) if m else None


def autonomous_answer(model, tokenizer, prompt: str, *, latent_steps, max_new_tokens):
    """The answer is NEVER passed to Qwen; labels are accessed only by scorer.

    The single common, predeclared answer cue comes after the generated latent
    sequence in both arms. This is not a teacher thought or target value.
    """
    device = model.backbone.get_input_embeddings().weight.device
    prompt_ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
    assert prompt_ids and isinstance(prompt_ids[0], int), 'TRANSFER_EMPTY_PROMPT_TOKENS'
    prompt = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        h, _, cache, mask, _ = model.rollout(prompt, latent_steps,
                                           enter_latent=latent_steps > 0,
                                           stop_on_end=False, return_trace=False)
        assert torch.isfinite(h).all(), 'TRANSFER_NONFINITE_RECURRENT_HIDDEN'
        # Exit latent mode; append a fixed, model-independent answer cue.
        answer_cue = tokenizer('\nAnswer:', add_special_tokens=False)['input_ids']
        assert answer_cue and isinstance(answer_cue[0], int), 'TRANSFER_EMPTY_ANSWER_CUE'
        suffix = ([model.end_id] if latent_steps else []) + answer_cue
        suffix_ids = torch.tensor([suffix], dtype=torch.long, device=device)
        mask = torch.cat((mask, mask.new_ones((1, len(suffix)))), dim=1)
        _, logits, cache = model._step(input_ids=suffix_ids, cache=cache, mask=mask)
        first_logits = logits.detach().float()
        output = []
        for step in range(max_new_tokens):
            token = logits.argmax(-1, keepdim=True)
            tok = int(token.item())
            if tokenizer.eos_token_id is not None and tok == tokenizer.eos_token_id:
                break
            output.append(tok)
            if step + 1 < max_new_tokens:
                mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
                _, logits, cache = model._step(input_ids=token, cache=cache, mask=mask)
    completion = tokenizer.decode(output, skip_special_tokens=True)
    parsed = parse_number(completion)
    return {'completion': completion[:240], 'predicted': parsed,
            'first_logits': first_logits.cpu(),
            'final_latent_hidden': h.detach().float().cpu()}


def gold_first_token_probability(tokenizer, logits, expected):
    """Read gold solely in the scorer, after completion has been generated."""
    ids = tokenizer(str(expected), add_special_tokens=False)['input_ids']
    assert ids and isinstance(ids[0], int), 'TRANSFER_GOLD_NOT_TOKENIZABLE'
    assert 0 <= ids[0] < logits.shape[-1], 'TRANSFER_GOLD_ID_OUT_OF_BOUNDS'
    return float(torch.softmax(logits[0], dim=-1)[ids[0]].item())


def compare_results(baseline_rows, trained_rows, samples, *, min_net_gain=2):
    assert len(baseline_rows) == len(trained_rows) == len(samples), 'TRANSFER_ROW_COUNT_DRIFT'
    base_n = sum(x['predicted'] == c['answer'] for x, c in zip(baseline_rows, samples))
    train_n = sum(x['predicted'] == c['answer'] for x, c in zip(trained_rows, samples))
    wins = sum(x['predicted'] != c['answer'] and y['predicted'] == c['answer']
               for x, y, c in zip(baseline_rows, trained_rows, samples))
    losses = sum(x['predicted'] == c['answer'] and y['predicted'] != c['answer']
                 for x, y, c in zip(baseline_rows, trained_rows, samples))
    sign = {}
    for tier in ('negative', 'zero', 'positive'):
        indices = [i for i,c in enumerate(samples) if c['sign'] == tier]
        assert indices, 'TRANSFER_EMPTY_SIGN_TIER:' + tier
        sign[tier] = {'cases':len(indices),
            'initial_correct':sum(baseline_rows[i]['predicted'] == samples[i]['answer'] for i in indices),
            'trained_correct':sum(trained_rows[i]['predicted'] == samples[i]['answer'] for i in indices)}
    if train_n - base_n >= min_net_gain:
        decision = 'NUMERICAL_TRANSFER_OBSERVED'
    elif train_n == base_n and train_n > 0:
        decision = 'NO_MEASURABLE_ACCURACY_GAIN'
    else:
        decision = 'NO_NUMERICAL_TRANSFER_DEMONSTRATED'
    return {'status':decision,'cases':len(samples), 'initial_correct':base_n,
            'trained_correct':train_n, 'net_accuracy_gain':train_n-base_n,
            'initial_accuracy':base_n/len(samples), 'trained_accuracy':train_n/len(samples),
            'trained_only_correct':wins, 'initial_only_correct':losses,
            'paired_sign_breakdown':sign,
            'mean_first_token_gold_probability_initial':sum(x['gold_probability'] for x in baseline_rows)/len(samples),
            'mean_first_token_gold_probability_trained':sum(x['gold_probability'] for x in trained_rows)/len(samples),
            'min_net_gain':min_net_gain}


def run_transfer(*, device='cuda', baseline=BASELINE, activation_dir=ACTIVATION,
                 output=DEFAULT_RUN, latent_steps=4, max_new_tokens=12,
                 min_net_gain=2, use_last=False, allow_download=False,
                 model_loader=None):
    if not (1 <= latent_steps <= 16 and 1 <= max_new_tokens <= 64):
        raise ValueError('TRANSFER_INVALID_GENERATION_BUDGET')
    if min_net_gain < 1:
        raise ValueError('TRANSFER_INVALID_ACCEPTANCE_THRESHOLD')
    roots = [baseline.resolve(), activation_dir.resolve(), output.resolve()]
    assert len(set(roots)) == 3, 'TRANSFER_INPUT_OUTPUT_PATH_COLLISION'
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('TRANSFER_REFUSES_EXISTING_OUTPUT:' + str(output))
    filenames = [baseline/'adapter.pt',baseline/'adapter_config.json',
                 activation_dir/'run_config.json',activation_dir/'result.json',
                 activation_dir/('last.pt' if use_last else 'best_head.pt')]
    before = {str(f): activation.file_hash(f) for f in filenames}
    selected = 'last.pt' if use_last else 'best_head.pt'
    config = hybrid.read_config(baseline)
    trained, run_cfg, training_report = verify_training_artifacts(
        baseline, activation_dir, config, best_filename=selected)
    cases_ = cases()
    loader = model_loader or hybrid.load_qwen
    model, tokenizer, details = loader(device=device, allow_download=allow_download)
    hybrid.load_adapter(baseline, model, details)
    model.backbone.eval()
    model.backbone.requires_grad_(False)
    model.head.eval()
    params_init = {k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}
    assert set(params_init) == set(trained['head']), 'TRANSFER_TRAINED_HEAD_KEYS_MISMATCH'
    for key, init_value in params_init.items():
        assert init_value.shape == trained['head'][key].shape, 'TRANSFER_TRAINED_HEAD_SHAPE_MISMATCH:' + key
    assert any(not torch.equal(v.float(), trained['head'][k].float()) for k,v in params_init.items()), (
        'TRANSFER_TRAINED_HEAD_IDENTICAL_TO_INITIALIZED')
    # Match Qwen's forward precision in both arms (float16 on CUDA, float32 on CPU).
    # Training saved fp32; model.load_state_dict casts to its actual runtime dtype.
    assert all(not p.requires_grad for p in model.backbone.parameters()), 'TRANSFER_BACKBONE_NOT_FROZEN'
    b_hash = activation.tensor_hash(model.backbone)
    initial, final = [], []
    records = []
    for c in cases_:
        outputs = []
        for which, state in (('initialized',params_init),('trained',trained['head'])):
            model.head.load_state_dict(state, strict=True)
            model.head.eval()
            result = autonomous_answer(model, tokenizer, c['prompt'], latent_steps=latent_steps,
                                       max_new_tokens=max_new_tokens)
            gold_p = gold_first_token_probability(tokenizer, result['first_logits'], c['answer'])
            assert math.isfinite(gold_p) and 0 <= gold_p <= 1, 'TRANSFER_GOLD_PROBABILITY_NONFINITE'
            outputs.append({'predicted':result['predicted'], 'completion':result['completion'],
                            'gold_probability':gold_p, 'hidden':result['final_latent_hidden']})
        latent_diff = float((outputs[1]['hidden']-outputs[0]['hidden']).abs().max().item())
        assert math.isfinite(latent_diff), 'TRANSFER_LATENT_DIFF_NONFINITE'
        record = {'id':c['id'], 'family':c['family'],'sign':c['sign'],
                  'a':c['a'],'u':c['u'],'v':c['v'],'expected':c['answer'],
                  'expression':c['expression'], 'latent_max_abs_delta':round(latent_diff,6),
                  'initial_prediction':outputs[0]['predicted'],
                  'trained_prediction':outputs[1]['predicted'],
                  'initial_completion':outputs[0]['completion'],
                  'trained_completion':outputs[1]['completion'],
                  'initial_gold_first_token_prob':outputs[0]['gold_probability'],
                  'trained_gold_first_token_prob':outputs[1]['gold_probability']}
        records.append(record)
        initial.append({'predicted':outputs[0]['predicted'],
                        'gold_probability':outputs[0]['gold_probability']})
        final.append({'predicted':outputs[1]['predicted'],
                      'gold_probability':outputs[1]['gold_probability']})
        print('HYBRID_TRANSFER_CASE='+json.dumps(record,sort_keys=True),flush=True)
    assert any(x['latent_max_abs_delta'] > 1e-5 for x in records), (
        'TRANSFER_TRAINED_HEAD_NO_CAUSAL_EFFECT')
    assert activation.tensor_hash(model.backbone) == b_hash, 'TRANSFER_BACKBONE_CHANGED'
    assert before == {str(f):activation.file_hash(f) for f in filenames}, (
        'TRANSFER_INPUT_CHECKPOINT_MUTATED')
    summary = compare_results(initial,final,cases_,min_net_gain=min_net_gain)
    summary.update({'schema':SCHEMA, 'head_file':selected,'head_step':int(trained['step']),
       'latent_steps':latent_steps, 'max_new_tokens':max_new_tokens,
       'case_fingerprint':case_fingerprint(cases_), 'baseline_model':details['base_model'],
       'baseline_revision':details['revision'], 'backbone_hash_preserved':True,
       'source_mutations':0,'optimizer_steps':0,
       'oracle_latent_states_supplied':0,'teacher_forced_answer_tokens_supplied':0,
       'note':'Small held-out deterministic evaluation; improvement is not yet certification or proof of generalized arithmetic.',
       'records':records})
    output.mkdir(parents=True,exist_ok=True)
    activation.write_json_atomic(output/'result.json',summary)
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path,default=BASELINE)
    p.add_argument('--activation',type=Path,default=ACTIVATION)
    p.add_argument('--output',type=Path,default=DEFAULT_RUN)
    p.add_argument('--latent-steps',type=int,default=4)
    p.add_argument('--max-new-tokens',type=int,default=12)
    p.add_argument('--min-net-gain',type=int,default=2)
    p.add_argument('--use-last',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        result=run_transfer(device=args.device,baseline=args.baseline,
            activation_dir=args.activation,output=args.output,
            latent_steps=args.latent_steps,max_new_tokens=args.max_new_tokens,
            min_net_gain=args.min_net_gain,use_last=args.use_last,
            allow_download=args.allow_model_download)
        print('HYBRID_TRANSFER_RESULT='+json.dumps(result,sort_keys=True),flush=True)
        return 0 if result['status']=='NUMERICAL_TRANSFER_OBSERVED' else 1
    except (AssertionError,RuntimeError,OSError,ValueError,KeyError,FileExistsError) as exc:
        print('HYBRID_TRANSFER_REFUSED='+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':
    sys.exit(main())
