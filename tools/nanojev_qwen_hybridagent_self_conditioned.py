#!/usr/bin/env python3
"""Bounded, resumable, two-pass self-conditioned HybridSFT for Qwen3-0.6B.

Loads the verified Base checkpoint, BEST backbone numerical LoRA, and BEST
HybridLink activation state. Frozen Qwen weights remain immutable; both
attention LoRA and HybridLink train on self-predicted latent INPUT embeddings.
The teacher sequence is a supervised TARGET, never an inference-side oracle.
This 6GB adaptation detaches pass-1 decoder hidden states to save VRAM;
therefore it is NOT parameter-identical to full upstream HybridSFT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-self-conditioned-lora.v1'
BASELINE = hybrid.DEFAULT_OUTPUT
ACTIVATION = activation.DEFAULT_RUN
BACKBONE = zero.DEFAULT_OUTPUT
OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_self_conditioned_v1'


def sha_config(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def assert_disjoint_paths(output, sources):
    actual = output.resolve()
    for source in sources:
        src = Path(source).resolve()
        assert actual != src and actual not in src.parents and src not in actual.parents, (
            'HYBRID_SELF_CONDITIONED_OUTPUT_OVERLAPS_SOURCE:' + str(src))


def source_fingerprints(baseline, activation_dir, backbone_dir):
    paths = {
        'base_adapter': baseline / 'adapter.pt',
        'base_config': baseline / 'adapter_config.json',
        'head_best': activation_dir / 'best_head.pt',
        'head_run_config': activation_dir / 'run_config.json',
        'head_result': activation_dir / 'result.json',
        'lora_best': backbone_dir / 'best_lora.pt',
        'lora_run_config': backbone_dir / 'run_config.json',
        'lora_result': backbone_dir / 'result.json',
    }
    for key, path in paths.items():
        assert path.is_file(), 'HYBRID_SELF_CONDITIONED_SOURCE_MISSING:' + key + ':' + str(path)
    return {key: activation.file_hash(path) for key, path in paths.items()}


def validate_source_contract(baseline, activation_dir, backbone_dir, fingerprints):
    head_config = json.loads((activation_dir / 'run_config.json').read_text(encoding='utf8'))
    head_result = json.loads((activation_dir / 'result.json').read_text(encoding='utf8'))
    lora_config = json.loads((backbone_dir / 'run_config.json').read_text(encoding='utf8'))
    lora_result = json.loads((backbone_dir / 'result.json').read_text(encoding='utf8'))
    assert head_config['schema'] == activation.SCHEMA and head_result.get('status') == 'PASS_MECHANICS_ONLY', (
        'HYBRID_SELF_CONDITIONED_HEAD_NOT_COMPLETED')
    assert lora_config['schema'] == zero.SCHEMA and lora_result.get('status') == 'TRAINING_SMOKE_COMPLETED_UNCERTIFIED', (
        'HYBRID_SELF_CONDITIONED_LORA_NOT_COMPLETED')
    assert Path(head_config['baseline_dir']).resolve() == baseline.resolve(), (
        'HYBRID_SELF_CONDITIONED_HEAD_BASELINE_DRIFT')
    assert Path(lora_config['baseline']).resolve() == baseline.resolve(), (
        'HYBRID_SELF_CONDITIONED_LORA_BASELINE_DRIFT')
    assert head_config['baseline_adapter_hash'] == fingerprints['base_adapter'], (
        'HYBRID_SELF_CONDITIONED_HEAD_ADAPTER_HASH_DRIFT')
    assert head_config['baseline_config_hash'] == fingerprints['base_config'], (
        'HYBRID_SELF_CONDITIONED_HEAD_CONFIG_HASH_DRIFT')
    for source_name, key in [('adapter.pt', 'base_adapter'), ('adapter_config.json', 'base_config')]:
        assert lora_config['base_hashes'][source_name] == fingerprints[key], (
            'HYBRID_SELF_CONDITIONED_LORA_BASELINE_HASH_DRIFT:' + source_name)
    assert head_config['base_model'] == lora_config['base_model'] == hybrid.MODEL_ID, (
        'HYBRID_SELF_CONDITIONED_NONBASE_OR_MODEL_DRIFT')
    assert head_config['revision'] == lora_config['revision'] == hybrid.MODEL_REVISION, (
        'HYBRID_SELF_CONDITIONED_REVISION_DRIFT')
    return head_config, lora_config


def encode_example(tokenizer, model, row, max_seq):
    """Mirror upstream masks: <latent> and content -> latent; </latent> -> CE*2.

    Prompt is context only. No training answer is supplied to free-running
    inference; here it is an ordinary supervised training label.
    """
    prompt = f"Question: Compute {row['expression']}.\nReasoning: "
    thought = ("Equal and opposite terms cancel. "
               + (f"Combine remainders to obtain {row['answer']}." if row['family'] == 'composed'
                  else f"Keep signed remainder {row['answer']}."))
    suffix = '\nAnswer: ' + str(row['answer']) + '\n'
    p = tokenizer(prompt, add_special_tokens=False)['input_ids']
    t = tokenizer(thought, add_special_tokens=False)['input_ids']
    end = tokenizer(suffix, add_special_tokens=False)['input_ids']
    assert p and t and end and model.start_id != model.end_id, 'HYBRID_SELF_CONDITIONED_EMPTY_SEGMENT'
    ids = p + [model.start_id] + t + [model.end_id] + end
    assert len(ids) <= max_seq, 'HYBRID_SELF_CONDITIONED_MAX_SEQ_EXCEEDED'
    # A target mask indexed at the target-token position, not the logit position.
    text_mask = ([0.] * len(p) + [1.] + [0.] * len(t) + [2.] + [1.] * len(end))
    latent_mask = ([0.] * len(p) + [1.] + [1.] * len(t) + [0.] + [0.] * len(end))
    assert len(text_mask) == len(ids) == len(latent_mask)
    enter = len(p)
    exit_at = enter + 1 + len(t)
    assert ids[enter] == model.start_id and ids[exit_at] == model.end_id
    assert text_mask[enter] == 1 and text_mask[exit_at] == 2, 'HYBRID_SELF_CONDITIONED_EXIT_CE_MISSING'
    assert latent_mask[enter] == 1 and latent_mask[exit_at] == 0, 'HYBRID_SELF_CONDITIONED_LATENT_MASK_DRIFT'
    assert all(latent_mask[k] == 1 and text_mask[k] == 0 for k in range(enter + 1, exit_at))
    return {'ids': ids, 'text_mask': text_mask, 'latent_mask': latent_mask,
            'entry_index': enter, 'exit_index': exit_at}


def _last_hidden(output):
    if hasattr(output, 'last_hidden_state'):
        return output.last_hidden_state
    if isinstance(output, tuple):
        return output[0]
    raise AssertionError('HYBRID_SELF_CONDITIONED_DECODER_HAS_NO_LAST_HIDDEN')


def forward_losses(model, example, device, *, mode='self_conditioned'):
    """Full two-pass supervision, with detached teacher decoder for VRAM.

    Gradient is NOT detached from HybridLink's predicted inputs. The second
    pass must carry its CE gradient into both LoRA and the link. In teacher
    control mode the replacement is disabled, so the two modes are comparable.
    """
    assert mode in ('self_conditioned', 'teacher_control'), 'HYBRID_SELF_CONDITIONED_UNKNOWN_MODE'
    ids = torch.tensor([example['ids']], dtype=torch.long, device=device)
    ce_mask = torch.tensor([example['text_mask']], dtype=torch.float32, device=device)
    lat = torch.tensor([example['latent_mask']], dtype=torch.bool, device=device)
    n = ids.size(1)
    assert lat.sum().item() >= 2 and (ce_mask[:, 1:] > 0).sum() > 1, (
        'HYBRID_SELF_CONDITIONED_EMPTY_LOSS_MASK')
    decoder = model.backbone.get_decoder()
    embed = model.backbone.get_input_embeddings()
    out_embed = model.backbone.get_output_embeddings()
    mask = torch.ones_like(ids)
    # The first decoder pass is deliberately detached (6 GB adaptation).
    with torch.no_grad():
        target_embeddings = embed(ids).detach()
        teacher_hidden = _last_hidden(decoder(inputs_embeds=target_embeddings,
                                    attention_mask=mask, use_cache=False, return_dict=True)).detach()
    assert teacher_hidden.shape == target_embeddings.shape, 'HYBRID_SELF_CONDITIONED_TEACHER_SHAPE'
    # HybridLink is kept in fp32; the decoder's BF16 inputs remain BF16.
    pred_inputs = model.head(teacher_hidden[:, :-1].float())
    assert pred_inputs.shape == target_embeddings[:, 1:].shape
    replace_mask = lat[:, :-1].unsqueeze(-1)
    if mode == 'self_conditioned':
        next_embeds = torch.where(replace_mask, pred_inputs.to(target_embeddings.dtype),
                                  target_embeddings[:, 1:])
    else:
        next_embeds = target_embeddings[:, 1:]
    feedback_delta = ((next_embeds.float() - target_embeddings[:, 1:].float()).abs()
                      * replace_mask).sum() / replace_mask.sum().clamp(min=1)
    modified = torch.cat((target_embeddings[:, :1], next_embeds), dim=1)
    second = _last_hidden(decoder(inputs_embeds=modified,attention_mask=mask,
                                use_cache=False,return_dict=True))
    assert second.shape == target_embeddings.shape and torch.isfinite(second).all(), (
        'HYBRID_SELF_CONDITIONED_SECOND_PASS_SHAPE_NONFINITE')
    supervised = torch.nonzero(ce_mask[:, 1:] > 0, as_tuple=False)
    assert supervised.shape[0] >= 2
    positions = supervised[:, 1]
    # Select ONLY supervised text positions to avoid full [T, vocab] logits.
    chosen_hidden = second[0].index_select(0, positions)
    chosen_labels = ids[0].index_select(0, positions + 1)
    # The final decoder hidden state may be fp32 even when lm_head is bf16.
    # Cast activations to the projection weights, then compute CE in fp32.
    weighted_logits = out_embed(chosen_hidden.to(dtype=out_embed.weight.dtype)).float()
    individual_ce = F.cross_entropy(weighted_logits,chosen_labels,reduction='none')
    weights = ce_mask[0].index_select(0, positions + 1)
    text_ce = (individual_ce * weights).sum() / weights.sum()
    src = lat[:, :-1]
    preds2 = model.head(second[:, :-1].float())
    targets = target_embeddings[:, 1:].float()
    cos = 1.0 - F.cosine_similarity(preds2, targets, dim=-1)
    mse = (preds2 - targets).square().mean(-1)
    count = src.sum().clamp(min=1)
    regression = ((cos + 0.2 * mse) * src).sum() / count
    exit_positions = src & (~lat[:, 1:])
    assert exit_positions.sum().item() == 1, 'HYBRID_SELF_CONDITIONED_EXIT_TARGET_MISSING'
    exit_reg = (cos + 0.2 * mse)[exit_positions].mean()
    link_loss = regression + exit_reg
    total = text_ce + link_loss
    assert bool(torch.isfinite(total)), 'HYBRID_SELF_CONDITIONED_NONFINITE_LOSS'
    return {'loss':total,'text_ce':text_ce,'link_loss':link_loss,
            'cos':(cos * src).sum() / count, 'mse':(mse * src).sum()/count,
            'exit':exit_reg, 'feedback_delta':feedback_delta,
            'latent_tokens':int(src.sum().item()), 'text_tokens':int(weights.numel())}


def verify_gradient_scope(model, optimizer):
    lora_params = zero.lora_named_parameters(model.backbone)
    head_params = [(name, p) for name,p in model.head.named_parameters() if p.requires_grad]
    required = {id(p) for _,p in lora_params + head_params}
    actual = {id(p) for group in optimizer.param_groups for p in group['params']}
    assert required and actual == required, 'HYBRID_SELF_CONDITIONED_OPTIMIZER_SCOPE_DRIFT'
    assert len(optimizer.param_groups) == 2, 'HYBRID_SELF_CONDITIONED_PARAM_GROUP_DRIFT'
    for name,p in model.backbone.named_parameters():
        if not name.endswith(('.lora_a','.lora_b')):
            assert not p.requires_grad, 'HYBRID_SELF_CONDITIONED_FROZEN_BACKBONE_TRAINABLE:' + name
    return lora_params, head_params


def assert_good_gradients(model, optimizer):
    lora, head = verify_gradient_scope(model,optimizer)
    for groupname, params in [('lora',lora), ('head',head)]:
        missing = [n for n,p in params if p.grad is None]
        nonfinite = [n for n,p in params if p.grad is not None and not bool(torch.isfinite(p.grad).all())]
        nonzero = [n for n,p in params if p.grad is not None and bool(torch.isfinite(p.grad).all())
                   and bool((p.grad != 0).any())]
        assert not missing, 'HYBRID_SELF_CONDITIONED_GRAD_MISSING_' + groupname + ':' + str(missing[:6])
        assert not nonfinite, 'HYBRID_SELF_CONDITIONED_GRAD_NONFINITE_' + groupname + ':' + str(nonfinite[:6])
        assert nonzero, 'HYBRID_SELF_CONDITIONED_GRAD_ZERO_' + groupname
    assert all(p.grad is None for n,p in model.backbone.named_parameters()
               if not n.endswith(('.lora_a','.lora_b'))), 'HYBRID_SELF_CONDITIONED_FROZEN_GRAD'


def cpu_head(model):
    return {k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}


def evaluate(model, tokenizer, rows, device, max_seq):
    was_backbone, was_head = model.backbone.training, model.head.training
    model.backbone.eval(); model.head.eval()
    try:
        values = []
        with torch.no_grad():
            for row in rows:
                output = forward_losses(model, encode_example(tokenizer,model,row,max_seq),device)
                values.append({k:float(output[k]) for k in ('loss','text_ce','link_loss','feedback_delta')})
        return {k:sum(r[k] for r in values)/len(values) for k in values[0]}
    finally:
        model.backbone.train(was_backbone); model.head.train(was_head)


def blind_probes(model, tokenizer, rows, device, *, latent_steps=4, max_new_tokens=32):
    """Free-running recurrent latent inference; no gold answer or teacher span."""
    results=[]
    model.backbone.eval();model.head.eval()
    with torch.inference_mode():
        for row in rows:
            prompt = f"Question: Compute {row['expression']}.\nReasoning: "
            ids=tokenizer(prompt,add_special_tokens=False)['input_ids']
            assert ids, 'HYBRID_SELF_CONDITIONED_BLIND_EMPTY_PROMPT'
            src=torch.tensor([ids],device=device,dtype=torch.long)
            _,_,cache,attention,trace=model.rollout(src,latent_steps,return_trace=True)
            produced=model.text_after_latents(cache,attention,max_tokens=max_new_tokens,
                                             eos_token_id=tokenizer.eos_token_id)
            text=tokenizer.decode(produced,skip_special_tokens=True)
            m=re.search(r'(?:^|\n)\s*Answer:\s*([+-]?\d+)(?!\d)',text)
            predicted=int(m.group(1)) if m else None
            results.append({'id':row['id'],'expected':row['answer'],'predicted':predicted,
                            'correct':predicted==row['answer'],'completion':text[:200],
                            'latent_steps':len(trace)})
    return {'cases':len(results),'correct':sum(r['correct'] for r in results),
            'accuracy':sum(r['correct'] for r in results)/len(results), 'records':results}


def run(*, device='cuda', baseline=BASELINE, activation_dir=ACTIVATION,
        backbone_dir=BACKBONE, output=OUTPUT, steps=12, eval_every=4, seed=71,
        train_cases=96, dev_cases=18, test_cases=18, max_seq=128, lora_lr=5e-5,
        head_lr=5e-5, stop_after=None, resume=False, blind_cases=6,
        allow_download=False, model_loader=None):
    baseline, activation_dir, backbone_dir, output = map(Path,
                                        (baseline, activation_dir, backbone_dir, output))
    assert_disjoint_paths(output,(baseline,activation_dir,backbone_dir))
    assert 1 <= steps <= 400 and 1 <= eval_every <= steps and max_seq >= 48 and 0 <= blind_cases <= test_cases, (
        'HYBRID_SELF_CONDITIONED_INVALID_SHAPE_OR_STEPS')
    assert 0 < lora_lr <= 1e-2 and 0 < head_lr <= 1e-2, 'HYBRID_SELF_CONDITIONED_INVALID_LR'
    if stop_after is not None:
        assert 1 <= stop_after <= steps, 'HYBRID_SELF_CONDITIONED_BAD_STOP'
    assert train_cases % 6 == dev_cases % 6 == test_cases % 6 == 0, 'HYBRID_SELF_CONDITIONED_UNBALANCED_DATA'
    # A failed integrity preflight may leave only run_config.json, created
    # before the first optimizer step. Recover it only if its exact contract
    # still matches this invocation. Never overwrite trained checkpoints.
    recover_prestep = False
    if not resume and output.exists() and any(output.iterdir()):
        assert (output.is_dir() and {entry.name for entry in output.iterdir()} == {'run_config.json'}
                and (output/'run_config.json').is_file()), (
                    'HYBRID_SELF_CONDITIONED_REFUSES_EXISTING_OUTPUT')
        recover_prestep = True
    sources = source_fingerprints(baseline,activation_dir,backbone_dir)
    head_cfg, lora_cfg = validate_source_contract(baseline,activation_dir,backbone_dir,sources)
    cases = zero.make_cases(seed,train_cases,dev_cases,test_cases)
    seed_hash=zero.fingerprint(cases)
    loader=model_loader or hybrid.load_qwen
    model,tokenizer,details=loader(device=device,allow_download=allow_download,
                                    precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'auto')
    hybrid.load_adapter(baseline,model,details)
    assert details['base_model'] == hybrid.MODEL_ID and details['revision'] == hybrid.MODEL_REVISION, (
        'HYBRID_SELF_CONDITIONED_MODEL_REVISION_DRIFT')
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(False)
    modules=zero.install_lora(model.backbone, int(lora_cfg['rank']), float(lora_cfg['alpha']),
                  required_layers=int(model.backbone.config.num_hidden_layers))
    lora_best=torch.load(backbone_dir/'best_lora.pt',map_location='cpu',weights_only=True)
    assert lora_best['schema'] == zero.SCHEMA, 'HYBRID_SELF_CONDITIONED_LORA_STATE_SCHEMA'
    zero.restore_lora(model.backbone,lora_best['lora'])
    head_best=torch.load(activation_dir/'best_head.pt',map_location='cpu',weights_only=True)
    assert head_best['schema'] == activation.SCHEMA and head_best['training_stage']=='activation_only', (
        'HYBRID_SELF_CONDITIONED_HEAD_STATE_SCHEMA')
    model.head.load_state_dict(head_best['head'],strict=True)
    model.head.to(device=device,dtype=torch.float32)
    model.head.requires_grad_(True)
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed);torch.cuda.reset_peak_memory_stats()
    lora_params=[p for _,p in zero.lora_named_parameters(model.backbone)]
    optimizer=torch.optim.AdamW([{'params':lora_params,'lr':lora_lr},
                                 {'params':list(model.head.parameters()),'lr':head_lr}],
                                 betas=(0.9,0.95),weight_decay=0.)
    verify_gradient_scope(model,optimizer)
    config={'schema':SCHEMA,'sources':sources,'baseline':str(baseline.resolve()),
            'activation':str(activation_dir.resolve()),'backbone':str(backbone_dir.resolve()),
            'base_model':details['base_model'],'revision':details['revision'],
            'lora_best_step':int(lora_best['step']),'head_best_step':int(head_best['step']),
            'steps':steps,'eval_every':eval_every,'seed':seed,'train_cases':train_cases,
            'dev_cases':dev_cases,'test_cases':test_cases,'max_seq':max_seq,
            'lora_lr':lora_lr,'head_lr':head_lr,'blind_cases':blind_cases,
            'data_hash':seed_hash,'module_names':list(modules),
            'format_activation_preceded':False, 'hybridlink_activation_preceded':True,
            'algorithm':'two_pass_self_conditioned_detached_teacher_decoder;full_second_pass_gradient',
            'role':'bounded_HybridSFT_stage_not_joint_certification'}
    if recover_prestep:
        assert json.loads((output/'run_config.json').read_text(encoding='utf8')) == config, (
            'HYBRID_SELF_CONDITIONED_RECOVERY_CONFIG_OR_SOURCE_DRIFT')
    if resume:
        assert (output/'run_config.json').is_file() and (output/'last.pt').is_file(), (
            'HYBRID_SELF_CONDITIONED_RESUME_FILES_MISSING')
        assert json.loads((output/'run_config.json').read_text()) == config, (
            'HYBRID_SELF_CONDITIONED_RESUME_CONFIG_OR_SOURCE_DRIFT')
        checkpoint=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        assert checkpoint['schema'] == SCHEMA and checkpoint['config_sha256'] == sha_config(config), (
            'HYBRID_SELF_CONDITIONED_CHECKPOINT_SIGNATURE_DRIFT')
        zero.restore_lora(model.backbone,checkpoint['lora'])
        model.head.load_state_dict(checkpoint['head'],strict=True)
        optimizer.load_state_dict(checkpoint['optimizer'])
        torch.set_rng_state(checkpoint['torch_rng'])
        if device=='cuda' and checkpoint['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(checkpoint['cuda_rng'])
        start=int(checkpoint['step']);best=float(checkpoint['best_dev'])
        initial=checkpoint['initial_dev']
        initial_blind=checkpoint['initial_blind']
        assert 0 < start < steps, 'HYBRID_SELF_CONDITIONED_ALREADY_COMPLETE_OR_BAD_STEP'
    else:
        start=0;best=float('inf')
        initial=evaluate(model,tokenizer,cases['dev'][:6],device,max_seq)
        initial_blind=(blind_probes(model,tokenizer,cases['test'][:blind_cases],device)
                       if blind_cases else None)
        if recover_prestep:
            print('HYBRID_SELF_CONDITIONED_RECOVERED_PRESTEP=' + str(output), flush=True)
        else:
            output.mkdir(parents=True,exist_ok=False)
            zero.atomic_json(output/'run_config.json',config)
    # Establish hash AFTER loading known source LoRA so none of the frozen tensors may change.
    frozen_hash=zero.frozen_backbone_sha256(model.backbone)
    initial_head=cpu_head(model)
    initial_lora=zero.lora_state(model.backbone)
    sample_control=encode_example(tokenizer,model,cases['dev'][0],max_seq)
    model.backbone.eval();model.head.eval()
    with torch.no_grad():
        live=forward_losses(model,sample_control,device,mode='self_conditioned')
        forced=forward_losses(model,sample_control,device,mode='teacher_control')
        input_feedback=float(live['feedback_delta'])
        training_condition_difference=float((live['text_ce']-forced['text_ce']).abs())
    assert input_feedback > 1e-5, 'HYBRID_SELF_CONDITIONED_PREDICTED_INPUTS_NOT_USED'
    assert training_condition_difference > 1e-8, 'HYBRID_SELF_CONDITIONED_TEACHER_CONTROL_INVARIANT'
    last_metrics=None
    for step in range(start+1,steps+1):
        model.backbone.train();model.head.train()
        optimizer.zero_grad(set_to_none=True)
        row=cases['train'][(step-1) % len(cases['train'])]
        data=encode_example(tokenizer,model,row,max_seq)
        with torch.autocast(device_type='cuda' if device=='cuda' else 'cpu',
                            enabled=(device=='cuda'),
                            dtype=torch.bfloat16 if (device=='cuda' and torch.cuda.is_bf16_supported()) else torch.float16):
            result=forward_losses(model,data,device)
        result['loss'].backward()
        assert_good_gradients(model,optimizer)
        torch.nn.utils.clip_grad_norm_([p for group in optimizer.param_groups for p in group['params']],1.)
        optimizer.step()
        if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
            now=evaluate(model,tokenizer,cases['dev'][:6],device,max_seq)
            last_metrics=now
            if now['loss'] < best:
                best=now['loss']
                zero.atomic_torch(output/'best.pt',{'schema':SCHEMA,'step':step,'lora':zero.lora_state(model.backbone),
                                                     'head':cpu_head(model),'dev':now,'config_sha256':sha_config(config)})
            cp={'schema':SCHEMA,'step':step,'lora':zero.lora_state(model.backbone),'head':cpu_head(model),
                'optimizer':optimizer.state_dict(),'best_dev':best,'initial_dev':initial,
                'config_sha256':sha_config(config),'initial_blind':initial_blind,
                'torch_rng':torch.get_rng_state(),
                'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None}
            zero.atomic_torch(output/'last.pt',cp)
            print('HYBRID_SELF_CONDITIONED_STEP='+json.dumps({
                'step':step,'total':steps,'train_loss':float(result['loss'].detach()),
                'train_text_ce':float(result['text_ce'].detach()), 'train_link_loss':float(result['link_loss'].detach()),
                'feedback_delta':float(result['feedback_delta'].detach()), 'dev_loss':now['loss'],
                'best_dev_loss':best},sort_keys=True),flush=True)
        if stop_after is not None and step>=stop_after:break
    assert frozen_hash==zero.frozen_backbone_sha256(model.backbone), 'HYBRID_SELF_CONDITIONED_FROZEN_WEIGHTS_CHANGED'
    assert source_fingerprints(baseline,activation_dir,backbone_dir)==sources, (
        'HYBRID_SELF_CONDITIONED_SOURCE_MUTATION')
    assert any(not torch.equal(v,model.head.state_dict()[k].detach().cpu()) for k,v in initial_head.items()), (
        'HYBRID_SELF_CONDITIONED_HEAD_NOT_UPDATED')
    current_lora=zero.lora_state(model.backbone)
    assert any(not torch.equal(v,current_lora[k]) for k,v in initial_lora.items()), (
        'HYBRID_SELF_CONDITIONED_LORA_NOT_UPDATED')
    finished=step==steps
    blind=blind_probes(model,tokenizer,cases['test'][:blind_cases],device) if finished and blind_cases else None
    report={'schema':SCHEMA,'status':'SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED' if finished else 'PAUSED_RESUMABLE',
            'step':step,'steps':steps,'start_step':start,'optimizer_steps_this_run':step-start,
            'initial_dev':initial,'current_dev':last_metrics,'best_dev_loss':best,
            'causal_input_delta':input_feedback,'teacher_vs_self_text_ce_difference':training_condition_difference,
            'head_updated':True,'lora_updated':True,'frozen_backbone_sha256':frozen_hash,
            'source_mutations':0,'blind':blind,'initial_blind':initial_blind,
            'blind_net_correct_gain':(blind['correct']-initial_blind['correct'])
                if blind is not None and initial_blind is not None else 'NOT_TESTED',
            'oracle_latent_states_supplied_at_inference':0,
            'first_pass_decoder_gradient':'detached_for_6GB_memory',
            'training_mode':'two_pass_joint_LoRA_HybridLink',
            'format_activation_preceded':False,
            'cuda_peak_allocated_mb':round(torch.cuda.max_memory_allocated()/1048576,2) if device=='cuda' else None,
            'gpu_total_mb':round(torch.cuda.get_device_properties(device).total_memory/1048576,2) if device=='cuda' else None,
            'note':'Mechanical self-conditioning only; no causal reasoning, tool-use, adaptive switching, or efficiency certification.'}
    zero.atomic_json(output/'result.json',report)
    print('HYBRID_SELF_CONDITIONED_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--baseline',type=Path,default=BASELINE)
    p.add_argument('--activation',type=Path,default=ACTIVATION)
    p.add_argument('--backbone',type=Path,default=BACKBONE)
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--steps',type=int,default=12)
    p.add_argument('--eval-every',type=int,default=4)
    p.add_argument('--max-seq',type=int,default=128)
    p.add_argument('--lora-lr',type=float,default=5e-5)
    p.add_argument('--head-lr',type=float,default=5e-5)
    p.add_argument('--blind-cases',type=int,default=6)
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int)
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        report=run(device=args.device,baseline=args.baseline,activation_dir=args.activation,
                   backbone_dir=args.backbone,output=args.output,steps=args.steps,
                   eval_every=args.eval_every,max_seq=args.max_seq,lora_lr=args.lora_lr,
                   head_lr=args.head_lr,blind_cases=args.blind_cases,seed=args.seed,
                   resume=args.resume,stop_after=args.stop_after,allow_download=args.allow_model_download)
        return 0 if report['status'] in ('PAUSED_RESUMABLE','SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED') else 2
    except (AssertionError,ValueError,RuntimeError,FileExistsError,FileNotFoundError,OSError) as exc:
        print('HYBRID_SELF_CONDITIONED_REFUSED='+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':
    sys.exit(main())
