#!/usr/bin/env python3
"""Optional, independently gated LoRA backbone-adaptation phase for C0.

The existing C0 encoder is not updated here. Training labels are exact integers,
never deterministic-local-teacher predictions. Gate/audit examples are never
used for gradient updates. The base Qwen checkpoint is never overwritten.
"""
from __future__ import annotations
import copy
import json
import random
import re
from pathlib import Path

import numpy as np
from tools.nanojev_qwen_c0_balance import make_balance_deck
from tools.nanojev_qwen_c0_zero_line import (
    PROGRAMS, FORMS, DELTA_CLASSES, make_focus_rows, focused_prompt,
    parse_focus_weights, focus_sampling_weights,
)


def validate_backbone_args(args):
    if not getattr(args, 'zero_line_backbone_phase', False):
        return
    if not getattr(args, 'zero_line_hard_train', False):
        raise ValueError('--zero-line-backbone-phase requires --zero-line-hard-train')
    if getattr(args, 'adapter', None):
        raise ValueError('backbone phase starts from the original model; omit --adapter')
    if not getattr(args, 'zero_line_mixture', False):
        raise ValueError('--zero-line-backbone-phase requires --zero-line-mixture')
    for name in ('zero_line_backbone_steps', 'zero_line_backbone_batch',
                 'zero_line_backbone_accum', 'zero_line_backbone_eval_every',
                 'zero_line_backbone_eval_cases', 'zero_line_backbone_rank'):
        if getattr(args, name) < 1:
            raise ValueError(f'--{name.replace("_", "-")} must be positive')
    if args.zero_line_backbone_rank > 256 or args.zero_line_backbone_rank % 2:
        raise ValueError('LoRA rank must be even and <=256')
    if not (0 < args.zero_line_backbone_lr <= .01):
        raise ValueError('backbone learning rate must be in (0,.01]')
    if not 0 <= args.zero_line_backbone_min_improvement <= 1:
        raise ValueError('backbone minimum improvement must be in [0,1]')
    if args.zero_line_backbone_train_cap < 16:
        raise ValueError('backbone training pool must have >=16 examples')


def make_backbone_decks(shells, radii, magnitudes, args):
    """Generate training-only transformations; development never enters training."""
    programs = parse_focus_weights(args.zero_line_program_weights, PROGRAMS)
    forms = parse_focus_weights(args.zero_line_form_weights, FORMS)
    deltas = parse_focus_weights(args.zero_line_delta_weights, DELTA_CLASSES)
    mags = parse_focus_weights(args.zero_line_magnitude_weights, (str(m) for m in magnitudes))
    # Pool several shells for nonzero training even when first acquisition radius is zero.
    train = make_focus_rows(shells, radii, radii[0], args, programs, forms, deltas, mags)
    if not train:
        raise ValueError('no enabled backbone training examples')
    rng = np.random.default_rng(args.seed + 60191)
    if len(train) > args.zero_line_backbone_train_cap:
        probabilities = focus_sampling_weights(train, programs, forms, deltas, mags)
        chosen = rng.choice(len(train), size=args.zero_line_backbone_train_cap,
                            replace=False, p=probabilities)
        train = [train[int(i)] for i in chosen]
    # Dedicated backbone-development source: disjoint from all downstream
    # zero-line train/gate/audit shells, including every nonzero radius.
    excluded = {r['prompt'] for shell in shells.values() for group in shell.values() for r in group}
    independent = make_balance_deck(deltas=(0,1,2,4), magnitudes=magnitudes,
                                    samples_per_cell=4, seed=args.seed+900019,
                                    excluded_prompts=excluded)
    val = []
    seen = {row['prompt'] for row in train}
    dev_seen = set()
    for row in independent:
            for program in PROGRAMS:
                if not programs[program]: continue
                for form in FORMS:
                    if not forms[form]: continue
                    example = focused_prompt(row, program, form)
                    if example is None or not deltas[example['focus_delta']]: continue
                    if not mags[str(example['focus_magnitude'])]: continue
                    if example['prompt'] in seen or example['prompt'] in dev_seen: continue
                    dev_seen.add(example['prompt'])
                    val.append(example)
    if not val:
        raise ValueError('no disjoint backbone development examples')
    rng.shuffle(val)
    val = val[:args.zero_line_backbone_eval_cases]
    return train, val


def encode_answer(tok, prompt, answer, max_length):
    """Token-level causal supervision restricted to answer and EOS, not prompt."""
    prefix = tok.encode(prompt, add_special_tokens=True)
    suffix = tok.encode(' ' + str(int(answer)), add_special_tokens=False)
    if tok.eos_token_id is not None:
        suffix.append(tok.eos_token_id)
    if not suffix or len(prefix) + len(suffix) > max_length:
        return None
    return {'input_ids': prefix + suffix, 'labels': [-100]*len(prefix) + suffix}


def collate(torch, tok, items, device):
    max_len = max(len(item['input_ids']) for item in items)
    pad = tok.pad_token_id
    if pad is None: raise ValueError('backbone tokenizer requires pad token')
    ids = [item['input_ids'] + [pad]*(max_len-len(item['input_ids'])) for item in items]
    labels = [item['labels'] + [-100]*(max_len-len(item['labels'])) for item in items]
    mask = [[1]*len(item['input_ids']) + [0]*(max_len-len(item['input_ids'])) for item in items]
    return {'input_ids':torch.tensor(ids,device=device,dtype=torch.long),
            'attention_mask':torch.tensor(mask,device=device,dtype=torch.long),
            'labels':torch.tensor(labels,device=device,dtype=torch.long)}


def evaluate_generation(torch, model, tok, rows, args):
    """Measure real greedy integer answers, rather than teacher-forced accuracy."""
    model.eval()
    correct = 0
    valid = 0
    with torch.inference_mode():
        for row in rows:
            inputs = tok(row['prompt'], truncation=True,
                         max_length=args.max_length, return_tensors='pt')
            inputs = {k:v.to(args.device) for k,v in inputs.items()}
            output = model.generate(**inputs, max_new_tokens=12, do_sample=False,
                                    pad_token_id=tok.pad_token_id,
                                    eos_token_id=tok.eos_token_id)
            generated = tok.decode(output[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True)
            match = re.match(r'^\s*([+-]?\d+)(?!\d)',generated)
            valid += 1
            correct += bool(match and int(match.group(1)) == int(row['answer']))
    return {'correct':correct,'total':valid,'exact':correct/max(1,valid)}


def adapt_backbone(args, torch, model, tok, shells, radii, magnitudes, ordinary_val, out):
    """Return either the adapted model or the untouched base model (if not promoted)."""
    from peft import LoraConfig, get_peft_model
    validate_backbone_args(args)
    path = Path(out)/'backbone_phase'
    path.mkdir(parents=True,exist_ok=True)
    marker = path/'report.json'
    if marker.exists():
        raise FileExistsError(f'backbone phase output already exists: {marker}; choose fresh --output')
    train, dev = make_backbone_decks(shells,radii,magnitudes,args)
    prepared = [(r,encode_answer(tok,r['prompt'],r['answer'],args.max_length)) for r in train]
    train = [(r,e) for r,e in prepared if e is not None]
    if not train:
        raise ValueError('every backbone example exceeds --max-length')
    print(f'BACKBONE phase: train={len(train)} gate={len(dev)} LoRA rank={args.zero_line_backbone_rank}',flush=True)
    baseline = evaluate_generation(torch,model,tok,dev,args)
    ordinary_dev = [r for r in ordinary_val if r['prompt'] not in {x['prompt'] for x, _ in train}][:16]
    ordinary_base = evaluate_generation(torch,model,tok,ordinary_dev,args) if ordinary_dev else None
    print(f'BACKBONE base exact={baseline["exact"]:.3f} ({baseline["correct"]}/{baseline["total"]})',flush=True)
    config = LoraConfig(r=args.zero_line_backbone_rank,
                        lora_alpha=args.zero_line_backbone_rank*2,
                        lora_dropout=.05, bias='none', task_type='CAUSAL_LM',
                        target_modules=['q_proj','k_proj','v_proj','o_proj'])
    adapter = get_peft_model(model,config)
    adapter.train()
    adapter.config.use_cache=False
    optimizer = torch.optim.AdamW([p for p in adapter.parameters() if p.requires_grad],
                                   lr=args.zero_line_backbone_lr,weight_decay=.01)
    rng = np.random.default_rng(args.seed+417)
    best = None
    best_score = baseline['exact']
    history=[]
    saved = path/'best_adapter'
    for step in range(1,args.zero_line_backbone_steps+1):
        optimizer.zero_grad(set_to_none=True)
        for _ in range(args.zero_line_backbone_accum):
            idx=rng.integers(len(train),size=args.zero_line_backbone_batch)
            batch=collate(torch,tok,[train[int(i)][1] for i in idx],args.device)
            loss=adapter(**batch).loss/args.zero_line_backbone_accum
            loss.backward()
        torch.nn.utils.clip_grad_norm_(adapter.parameters(),1.)
        optimizer.step()
        if step%args.zero_line_backbone_eval_every==0 or step==args.zero_line_backbone_steps:
            score=evaluate_generation(torch,adapter,tok,dev,args)
            ordinary_score = evaluate_generation(torch,adapter,tok,ordinary_dev,args) if ordinary_dev else None
            entry={'step':step,'gate':score,'ordinary':ordinary_score,'training_loss':float(loss.detach().cpu())*args.zero_line_backbone_accum}
            history.append(entry)
            print(f'BACKBONE step={step} gate_exact={score["exact"]:.3f} best={best_score:.3f}',flush=True)
            ordinary_retained = ordinary_score is None or ordinary_score['exact'] >= ordinary_base['exact'] - .05
            if score['exact']>best_score and ordinary_retained:
                adapter.save_pretrained(saved,safe_serialization=True)
                best_score=score['exact'];best=step
            adapter.train()
    promoted=best is not None and (best_score-baseline['exact'] >= args.zero_line_backbone_min_improvement)
    report={'schema':'nanojev.c0.backbone_phase.v1','baseline':baseline,
            'ordinary_baseline':ordinary_base,'ordinary_gate_min_exact':max(0,ordinary_base['exact']-.05) if ordinary_base else None,
            'best_exact':best_score,'best_step':best,'promoted':promoted,
            'train_examples':len(train),'gate_examples':len(dev),
            'settings':{'rank':args.zero_line_backbone_rank,'steps':args.zero_line_backbone_steps,
                        'lr':args.zero_line_backbone_lr,'min_improvement':args.zero_line_backbone_min_improvement},
            'history':history,'adapter_path':str(saved) if promoted else None}
    marker.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'BACKBONE promoted={promoted} best_exact={best_score:.3f} baseline={baseline["exact"]:.3f}',flush=True)
    # Release LoRA wrapper and return to immutable baseline unless independently improved.
    from peft import PeftModel
    base = adapter.unload()
    del adapter, optimizer
    if promoted:
        # Fresh adapter load restores best step; do not retain final-step weights.
        model = PeftModel.from_pretrained(base,str(saved),is_trainable=False)
    else:
        model = base
    model.config.use_cache=True
    return model.eval(), report
