#!/usr/bin/env python3
"""Train Qwen attention LoRA to USE certified HybridLink operand embeddings.

No external arithmetic resolver is used during inference. Signed residuals are
supervised as single-token class codes through Qwen's native LM head. Numeric
source-token embeddings are neutralized in every arm to prevent a raw-token
shortcut. No outward promotion occurs without exact per-cell DEV gates.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

import torch
from torch.nn import functional as F

from tools import nanojev_hybrid_emp_curriculum as curriculum
from tools import nanojev_hybrid_emp_join as join
from tools import nanojev_hybrid_emp_operand_stage as operand
from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as lora_tools

SCHEMA = 'nanojev.hybrid-emp-qwen-zero-line-consumption.v3'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_emp_zero_line_lora_v3'
OPERAND_DIR = operand.ROOT
STAGES = (1, 2, 4, 8, 16)
# Fixed, one-token *class labels*; these are not unrestricted textual integers.
CODE_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
PROMPT_SUFFIX = '\nResidual code: '
CELL_BANDS = ('1-4', '5-8', '9-16')
PROMPT_STYLES = 5


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for buf in iter(lambda: f.read(1024 * 1024), b''):
            h.update(buf)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf8')
    tmp.replace(path)


def atomic_torch(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    torch.save(value, tmp)
    tmp.replace(path)


def class_codes(tokenizer, max_radius=16):
    if not 1 <= max_radius <= 16:
        raise ValueError('BACKBONE_RADIUS_EXCEEDS_HEAD1_CERTIFICATION')
    values = list(range(-max_radius, max_radius + 1))
    result = {}
    for i, delta in enumerate(values):
        encoded = tokenizer(CODE_CHARS[i], add_special_tokens=False)['input_ids']
        if len(encoded) != 1:
            raise RuntimeError('BACKBONE_CLASS_TOKEN_NOT_ATOMIC:' + CODE_CHARS[i])
        result[delta] = int(encoded[0])
    if len(set(result.values())) != len(result):
        raise RuntimeError('BACKBONE_CLASS_TOKEN_COLLISION')
    return result


def band(magnitude):
    return '1-4' if magnitude <= 4 else '5-8' if magnitude <= 8 else '9-16'



def counterbalanced_style(step, radius):
    """Independent of the balanced residual-cycle index.

    Each block of (2*radius+1) updates sees every residual once with
    the SAME prompt style. Five successive blocks use all five renderings.
    Previously, `style=step % 3` was identical to the residual index at
    radius 1, allowing LoRA to memorize prompt wording instead of A/B.
    """
    if step < 0 or radius not in STAGES:
        raise ValueError('BACKBONE_BAD_STYLE_SCHEDULE')
    return (step // (2 * radius + 1)) % PROMPT_STYLES


def style_schedule_audit(radius):
    """Verify that the deterministic 5-by-(2r+1) core is fully crossed.

    Difficulty-driven overrides are independent and only replace 1 in 4
    balanced labels during training; they cannot create a fixed style-label
    mapping. This audit intentionally checks the guaranteed core schedule.
    """
    if radius not in STAGES:
        raise ValueError('BACKBONE_BAD_STYLE_SCHEDULE')
    labels = list(range(-radius, radius + 1))
    counts = {(style, delta): 0 for style in range(PROMPT_STYLES) for delta in labels}
    for step in range(len(labels) * PROMPT_STYLES):
        counts[counterbalanced_style(step, radius), labels[step % len(labels)]] += 1
    if any(value != 1 for value in counts.values()):
        raise RuntimeError('BACKBONE_STYLE_LABEL_LEAKAGE')
    return {'radius':radius, 'styles':PROMPT_STYLES, 'classes':len(labels),
            'all_style_label_pairs_covered':True,
            'min_samples_per_style_class':min(counts.values()),
            'max_samples_per_style_class':max(counts.values())}


def stage_cards(provider, split, radius):
    if radius not in STAGES:
        raise ValueError('BACKBONE_BAD_STAGE_RADIUS')
    cards = [c for c in provider.decks[split] if abs(c.delta) <= radius]
    found = {c.delta for c in cards}
    expected = set(range(-radius, radius + 1))
    if found != expected:
        raise RuntimeError(f'BACKBONE_RESIDUAL_COVERAGE_DRIFT:{split}:{radius}:{sorted(expected-found)}')
    return cards



def stratified_provider(seed):
    """Repartition existing zero-line cards so held-out cells are represented.

    The original hash split is pair-disjoint but leaves empty DEV/TEST cells
    near zero. Repartitioning uses exactly the same 1089 EMP cards, no new
    arithmetic generator, and never repeats any ordered pair across splits.
    """
    provider=curriculum.LocalCurriculum(16,seed)
    groups=defaultdict(list)
    for rows in provider.decks.values():
        for card in rows:groups[(card.delta,band(card.magnitude))].append(card)
    result={k:[] for k in ('train','dev','test')}
    for group,cards in sorted(groups.items()):
        cards=sorted(cards,key=lambda c:hashlib.sha256(
            f'backbone-cell-split:{seed}:{c.a}:{c.b}'.encode()).digest())
        n=len(cards)
        if n>=6: dev_n=test_n=2
        elif n>=3: dev_n=test_n=1
        elif n==2:dev_n=test_n=1
        else:dev_n=test_n=0
        for i,card in enumerate(cards):
            split='dev' if i<dev_n else 'test' if i<dev_n+test_n else 'train'
            result[split].append(replace(card,split=split))
    assert sum(map(len,result.values()))==1089, 'BACKBONE_SPLIT_CARD_COUNT_DRIFT'
    keys=[{(card.a,card.b) for card in result[k]} for k in result]
    if any(keys[i]&keys[j] for i in range(3) for j in range(i+1,3)):
        raise RuntimeError('BACKBONE_SPLIT_PAIR_LEAKAGE')
    provider.decks=result
    provider.by_delta={d:[card for card in result['train'] if card.delta==d]
                       for d in range(-32,33)}
    provider.eligible=[d for d,cards in provider.by_delta.items() if cards]
    return provider


def expected_cells(provider, split, radius):
    return {f'{card.delta}:{band(card.magnitude)}' for card in
            stage_cards(provider,split,radius)}

def code_indices(codebook, radius, device):
    return torch.tensor([codebook[n] for n in range(-radius, radius + 1)],
                        dtype=torch.long, device=device)


def sanitize_numerical_embeddings(example, sender, tokenizer, embedding, *, mode='intact', suffix_text=PROMPT_SUFFIX):
    """Qwen input = masked lexical prompt + sparse transmitted code + suffix.

    A/B original numeric-token coordinates are NOT available to Qwen. Every
    control has exactly the same mask, nonnumeric context, and output classes.
    """
    if mode not in ('intact', 'zero', 'wrong'):
        raise ValueError('BACKBONE_UNKNOWN_CONTROL')
    local = example['base'].clone()
    occupied = sorted(set(example['positions'][0] + example['positions'][1]))
    if not occupied or len(occupied) != sum(len(p) for p in example['positions']):
        raise RuntimeError('BACKBONE_OVERLAPPING_SPANS')
    # Critical: the ordinary numeral's Qwen embedding is NOT a fallback path.
    local[occupied] = 0.
    wrong = (operand.bits(operand.wrong_pair(example['values']), example['bits'].device)
             if mode == 'wrong' else None)
    with torch.no_grad():
        corrected = sender.edited_embeddings(example, mode=mode, wrong_bits=wrong)
    dims = example['dimensions']
    for tokens in example['positions']:
        for t in tokens:
            local[t, dims] = corrected[t, dims]
    # Source numerical tokens have ZERO outside the sparse channels.
    other = torch.ones(local.shape[-1], device=local.device, dtype=torch.bool)
    other[dims] = False
    if not bool(torch.all(local[occupied][:, other] == 0)):
        raise RuntimeError('BACKBONE_RAW_NUMBER_EMBEDDING_LEAK')
    suffix_ids = tokenizer(suffix_text, add_special_tokens=False)['input_ids']
    if not suffix_ids:
        raise RuntimeError('BACKBONE_EMPTY_SUFFIX')
    suffix = embedding(torch.tensor(suffix_ids, device=local.device, dtype=torch.long)).detach()
    return torch.cat((local, suffix), dim=0)


def select_logits(backbone, vectors, class_ids):
    if vectors.ndim != 2:
        raise ValueError('BACKBONE_INPUTS_EMBEDS_REQUIRED')
    outputs = backbone(inputs_embeds=vectors.unsqueeze(0),
                       attention_mask=torch.ones((1, vectors.shape[0]),
                                                   device=vectors.device, dtype=torch.long),
                       use_cache=False, return_dict=True)
    return outputs.logits[0, -1].index_select(0, class_ids).float()


def logits_for_card(backbone, tokenizer, embedding, head1, sender, card, style, codebook, radius,
                    device, *, mode='intact'):
    with torch.no_grad():
        example = join.extract(card, style, tokenizer, head1, embedding, device, 7)
        if example['values'] != [card.a, card.b]:
            raise RuntimeError('BACKBONE_HEAD1_PAIR_DRIFT')
        # Prior join extracted a seven-bit arithmetic target. The certified
        # operand writer instead consumes its canonical *six*-bit encoding.
        example['bits'] = operand.bits(example['values'], device)
    vectors = sanitize_numerical_embeddings(example, sender, tokenizer, embedding, mode=mode)
    return select_logits(backbone, vectors, code_indices(codebook, radius, vectors.device))


def cell_gate(evaluation, radius, *, expected=None, exact_threshold=.98, control_gap=.10):
    cells = evaluation['cells']
    expected_deltas = {str(n) for n in range(-radius, radius + 1)}
    if set(evaluation['by_residual']) != expected_deltas:
        return False
    if any(v['total'] < 2 for v in evaluation['by_residual'].values()):
        return False
    if expected is not None and set(cells)!=set(expected):
        return False
    if not cells or any(x['total'] < 2 or x['accuracy'] < exact_threshold for x in cells.values()):
        return False
    return (evaluation['exact'] >= exact_threshold and
            evaluation['exact'] - evaluation['zero_exact'] >= control_gap and
            evaluation['exact'] - evaluation['wrong_exact'] >= control_gap)


def evaluate(backbone, tokenizer, embedding, head1, sender, cards, codebook, radius,
             device, *, styles=(0,1), limit=None):
    if not cards:
        raise RuntimeError('BACKBONE_NO_EVALUATION_CARDS')
    was_training = backbone.training
    backbone.eval()
    totals = {'intact':0, 'zero':0, 'wrong':0}
    cells = {}; residuals = {}; cases = 0; signed_errors = []
    try:
        with torch.inference_mode():
            for card in (cards[:limit] if limit else cards):
                if abs(card.delta) > radius: continue
                for style in styles:
                    predictions = {}
                    for mode in totals:
                        out = logits_for_card(backbone,tokenizer,embedding,head1,sender,card,style,
                                              codebook,radius,device,mode=mode)
                        predictions[mode] = int(out.argmax().item())-radius
                        totals[mode] += int(predictions[mode] == card.delta)
                    cases += 1
                    signed_errors.append(predictions['intact']-card.delta)
                    cell_key = f'{card.delta}:{band(card.magnitude)}'
                    cell = cells.setdefault(cell_key, {'correct':0,'total':0})
                    cell['correct'] += int(predictions['intact'] == card.delta)
                    cell['total'] += 1
                    residual = residuals.setdefault(str(card.delta), {'correct':0,'total':0})
                    residual['correct'] += int(predictions['intact'] == card.delta)
                    residual['total'] += 1
    finally:
        backbone.train(was_training)
    for item in cells.values():
        item['accuracy'] = item['correct']/item['total']
    return {'cases':cases, 'exact':totals['intact']/cases,
            'signed_error_mean':statistics.mean(signed_errors),
            'signed_error_stddev':statistics.pstdev(signed_errors),
            'zero_exact':totals['zero']/cases, 'wrong_exact':totals['wrong']/cases,
            'cells':cells, 'by_residual':residuals,
            'min_cell_exact':min(x['accuracy'] for x in cells.values()),
            'all_zero_prediction_cannot_pass':True}


def load_certified_stack(*, device, head1_dir, operand_dir, allow_download=False,
                         model_loader=None, head1_override=None):
    operand_dir=Path(operand_dir)
    gate=json.loads((operand_dir/'gate_result.json').read_text('utf8'))
    config=json.loads((operand_dir/'config.json').read_text('utf8'))
    if (gate.get('status')!='OPERAND_TRANSMISSION_PASS' or not gate.get('gate_pass') or
        not gate.get('resolver_unlocked') or not gate.get('test_read') or
        gate.get('test_edited',{}).get('pair_exact',0)<.98):
        raise RuntimeError('BACKBONE_OPERAND_CERTIFICATION_REQUIRED')
    if config['schema']!=operand.SCHEMA or config['radius']!=16:
        raise RuntimeError('BACKBONE_OPERAND_LINEAGE_INCOMPATIBLE')
    if model_loader is None:
        wrapper, tokenizer, details = hybrid.load_qwen(
            device=device, precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'float32',
            allow_download=allow_download)
    else:
        wrapper, tokenizer, details = model_loader(device=device)
    backbone = wrapper.backbone if hasattr(wrapper,'backbone') else wrapper
    if not getattr(tokenizer, 'is_fast', False):
        raise RuntimeError('BACKBONE_FAST_TOKENIZER_REQUIRED')
    embedding=backbone.get_input_embeddings()
    if str(embedding.weight.device.type) != device:
        raise RuntimeError('BACKBONE_DEVICE_MISMATCH')
    identity={'revision':details.get('revision','toy-only'),
              'hidden':embedding.weight.shape[-1],'tokenizer_size':len(tokenizer)}
    if identity!=config['embedding']:
        raise RuntimeError('BACKBONE_EMBEDDING_PROVENANCE_MISMATCH')
    if head1_override is None:
        head1, head1meta=join.load_head1(head1_dir,device,identity['hidden'])
    else:
        head1,head1meta=head1_override
    if head1meta != config['sources']['head1']:
        raise RuntimeError('BACKBONE_HEAD1_PROVENANCE_DRIFT')
    best=torch.load(operand_dir/'best.pt',map_location='cpu',weights_only=True)
    if best['schema'] != operand.SCHEMA:
        raise RuntimeError('BACKBONE_OPERAND_CHECKPOINT_SCHEMA')
    sender=operand.OperandTransmission(sparse_dims=head1meta['sparse_dims']).to(device)
    sender.load_state_dict(best['head'],strict=True)
    sender.eval().requires_grad_(False)
    backbone.requires_grad_(False)
    frozen_hashes={name:digest(operand_dir/name) for name in
                   ('best.pt','config.json','gate_result.json')}
    return backbone,tokenizer,embedding,head1,sender,identity,frozen_hashes


def load_consumption_warmstart(backbone, consumption_dir, *, identity, operand_hashes,
                               rank, alpha):
    """Fail closed until Qwen itself has certified operand decoding.

    No arithmetic training can begin from random LoRA or from the failed
    v1/v2 zero-line checkpoints. A matching certified LoRA is mandatory.
    """
    from tools import nanojev_hybrid_emp_backbone_operand_consumption as consume
    root=Path(consumption_dir)
    config=json.loads((root/'config.json').read_text('utf8'))
    gate=json.loads((root/'gate_result.json').read_text('utf8'))
    if (config.get('schema')!=consume.SCHEMA or gate.get('schema')!=consume.SCHEMA or
        gate.get('status')!='BACKBONE_OPERAND_CONSUMPTION_PASS' or
        not gate.get('gate_pass') or not gate.get('test_read') or
        gate.get('arithmetic_trained') is not False):
        raise RuntimeError('BACKBONE_OPERAND_CONSUMPTION_CERTIFICATION_REQUIRED')
    if (config['identity']!=identity or config['operand_hashes']!=operand_hashes or
        config['rank']!=rank or config['alpha']!=alpha):
        raise RuntimeError('BACKBONE_CONSUMPTION_PROVENANCE_MISMATCH')
    for split in ('dev','test'):
        metrics=gate.get(split,{})
        if not consume.passes(metrics, expected_values=range(-16,17)):
            raise RuntimeError('BACKBONE_CONSUMPTION_GATE_METRICS_DRIFT')
    checkpoint=torch.load(root/'best.pt',map_location='cpu',weights_only=True)
    if checkpoint.get('schema')!=consume.SCHEMA:
        raise RuntimeError('BACKBONE_CONSUMPTION_CHECKPOINT_DRIFT')
    lora_tools.restore_lora(backbone,checkpoint['lora'])
    return {name:digest(root/name) for name in ('best.pt','gate_result.json','config.json')}


def _lora_snapshot(backbone):
    return {name: param.detach().cpu().clone() for name,param in lora_tools.lora_named_parameters(backbone)}


def _training_step(backbone, optimizer, vectors, class_ids, target_index, *, device):
    backbone.train()
    optimizer.zero_grad(set_to_none=True)
    cuda=device=='cuda'
    with torch.autocast(device_type='cuda' if cuda else 'cpu', enabled=cuda,
                        dtype=torch.bfloat16 if cuda else torch.float32):
        logits=select_logits(backbone,vectors,class_ids)
        target=torch.tensor([target_index],device=device,dtype=torch.long)
        loss=F.cross_entropy(logits.unsqueeze(0),target)
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('BACKBONE_LOSS_NONFINITE')
    loss.backward()
    report=lora_tools.gradient_report(backbone)
    if report['missing'] or report['nonfinite'] or not report['nonzero']:
        raise RuntimeError('BACKBONE_BAD_LORA_GRADIENT:' + str({
            k:len(report[k]) for k in ('missing','nonfinite','nonzero')}))
    if any(param.grad is not None for name,param in backbone.named_parameters()
           if not name.endswith(('.lora_a','.lora_b'))):
        raise RuntimeError('BACKBONE_FROZEN_PARAMETER_GRADIENT')
    torch.nn.utils.clip_grad_norm_([p for _,p in lora_tools.lora_named_parameters(backbone)],1.,error_if_nonfinite=True)
    optimizer.step()
    return float(loss.detach().item()),float(logits.detach().argmax().item()==target_index)


def _provenance(provider,identity,head1_dir,operand_dir,checkpoint_hashes):
    return {'embedding':identity,'operand_dir':str(Path(operand_dir).resolve()),
            'head1_dir':str(Path(head1_dir).resolve()),
            'operand_hashes':checkpoint_hashes,
            'data_fingerprints':{key:curriculum.signature(value) for key,value in provider.decks.items()},
            'trainer_source':digest(__file__),
            'curriculum_source':digest(curriculum.__file__)}


def run(*,device='cuda',output=ROOT,operand_dir=OPERAND_DIR,head1_dir=join.HEAD1,
        steps_per_stage=500,eval_every=100,max_residual=1,seed=20261026,
        lr=0.0002,rank=8,alpha=16.,allow_download=False,model_loader=None,
        head1_override=None,resume=False,stop_after=None,consumption_dir=None):
    if device not in ('cpu','cuda') or steps_per_stage<1 or eval_every<1 or lr<=0 or rank<1:
        raise ValueError('BACKBONE_BAD_CONFIG')
    if max_residual not in STAGES:
        raise ValueError('BACKBONE_RESIDUAL_RADIUS_MUST_BE_1_2_4_8_16')
    if device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('BACKBONE_CUDA_UNAVAILABLE')
    provider=stratified_provider(seed)
    style_schedule_audit(max_residual)
    backbone,tokenizer,embedding,head1,sender,identity,hashes=load_certified_stack(
        device=device,head1_dir=head1_dir,operand_dir=operand_dir,allow_download=allow_download,
        model_loader=model_loader,head1_override=head1_override)
    codebook=class_codes(tokenizer,16)
    lora_tools.install_lora(backbone,rank,alpha,required_layers=getattr(backbone.config,'num_hidden_layers',None))
    if consumption_dir is None:
        from tools import nanojev_hybrid_emp_backbone_operand_consumption as consume
        consumption_dir=consume.ROOT
    consumption_hashes=load_consumption_warmstart(backbone,consumption_dir,identity=identity,
        operand_hashes=hashes,rank=rank,alpha=alpha)
    params=[p for _,p in lora_tools.lora_named_parameters(backbone)]
    optimizer=torch.optim.AdamW(params,lr=lr,weight_decay=0.)
    stages=[r for r in STAGES if r<=max_residual]
    config={'schema':SCHEMA,'device':device,'seed':seed,'steps_per_stage':steps_per_stage,
            'eval_every':eval_every,'max_residual':max_residual,'lr':lr,'rank':rank,'alpha':alpha,
            'stages':stages,'class_tokens':{str(k):v for k,v in codebook.items()},
            'provenance':_provenance(provider,identity,head1_dir,operand_dir,hashes),
            'consumption_hashes':consumption_hashes}
    output=Path(output)
    step=0;stage_index=0;stage_step=0;streak=0;best=-1.;train_steps=0
    if resume:
        if (output/'gate_result.json').exists():raise RuntimeError('BACKBONE_ALREADY_GATED')
        if json.loads((output/'config.json').read_text('utf8'))!=config:
            raise RuntimeError('BACKBONE_RESUME_CONFIG_DRIFT')
        saved=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        if saved['schema']!=SCHEMA:raise RuntimeError('BACKBONE_RESUME_SCHEMA')
        lora_tools.restore_lora(backbone,saved['lora'])
        optimizer.load_state_dict(saved['optimizer'])
        torch.set_rng_state(saved['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(saved['cuda_rng'])
        provider.load(saved['curriculum'])
        step=saved['step'];stage_index=saved['stage_index'];stage_step=saved['stage_step']
        streak=saved['streak'];best=saved['best']
    else:
        if output.exists() and any(output.iterdir()):raise RuntimeError('BACKBONE_OUTPUT_EXISTS')
        torch.manual_seed(seed)
        if device=='cuda':torch.cuda.manual_seed_all(seed)
        output.mkdir(parents=True,exist_ok=True)
        atomic_json(output/'config.json',config)
    if device=='cuda':torch.cuda.reset_peak_memory_stats()
    result=None
    while stage_index<len(stages):
        radius=stages[stage_index]
        style_schedule_audit(radius)
        if stage_step>=steps_per_stage:
            result='STAGE_DEV_FAIL';break
        delta_options=list(range(-radius,radius+1))
        # Balanced residual cycling from the established EMP/zero-line cards.
        # Difficulty feedback only observes this TRAIN example, never DEV/TEST.
        delta=delta_options[step%len(delta_options)]
        if step%4==3:
            delta=provider.rng.choices(delta_options,
                           weights=[provider.difficulties[d] for d in delta_options])[0]
        card=provider.rng.choice(provider.by_delta[delta])
        # Decouple wording from the residual class. The old step%3 style
        # exactly encoded the radius-one delta on most training steps.
        style=counterbalanced_style(stage_step,radius)
        with torch.no_grad():
            example=join.extract(card,style,tokenizer,head1,embedding,device,7)
            example['bits'] = operand.bits(example['values'], device)
        vectors=sanitize_numerical_embeddings(example,sender,tokenizer,embedding,mode='intact')
        class_ids=code_indices(codebook,radius,device)
        loss,correct=_training_step(backbone,optimizer,vectors,class_ids,card.delta+radius,device=device)
        provider.emphasize(card.delta, not bool(correct))
        step+=1;stage_step+=1;train_steps+=1
        check=(stage_step==1 or stage_step%eval_every==0 or stage_step==steps_per_stage)
        if check:
            dev_cards=stage_cards(provider,'dev',radius)
            dev=evaluate(backbone,tokenizer,embedding,head1,sender,dev_cards,codebook,radius,device)
            passed=cell_gate(dev,radius,expected=expected_cells(provider,'dev',radius))
            streak=streak+1 if passed else 0
            objective=dev['min_cell_exact']+0.01*dev['exact']+0.001*max(0.,dev['exact']-dev['zero_exact'])
            if objective>best:
                best=objective
                atomic_torch(output/'best.pt',{'schema':SCHEMA,'step':step,'stage':radius,
                                              'lora':_lora_snapshot(backbone),'dev':dev})
            event={'step':step,'stage_radius':radius,'stage_step':stage_step,
                   'loss':round(loss,6),'dev_exact':dev['exact'],
                   'min_cell_exact':dev['min_cell_exact'],
                   'zero_exact':dev['zero_exact'],'wrong_exact':dev['wrong_exact'],
                   'signed_error_stddev':dev['signed_error_stddev'],
                   'dev_pass':passed,'consecutive_passes':streak,'best_objective':best}
            if device=='cuda':event['cuda_peak_mib']=round(torch.cuda.max_memory_allocated()/1048576,1)
            print('HYBRID_BACKBONE_STEP='+json.dumps(event,sort_keys=True),flush=True)
        if streak>=2:
            print('HYBRID_BACKBONE_STAGE_PASS='+json.dumps({'stage_radius':radius,'step':step},sort_keys=True),flush=True)
            stage_index+=1;stage_step=0;streak=0;best=-1.
        state={'schema':SCHEMA,'step':step,'stage_index':stage_index,'stage_step':stage_step,
               'streak':streak,'best':best,'optimizer':optimizer.state_dict(),
               'lora':_lora_snapshot(backbone),
               'curriculum':provider.state(),'torch_rng':torch.get_rng_state(),
               'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None}
        atomic_torch(output/'last.pt',state)
        if stop_after is not None and train_steps>=stop_after:
            result='PAUSED_RESUMABLE';break
    if result is None:
        result='TRAINED_UNCERTIFIED' if stage_index==len(stages) else 'STAGE_DEV_FAIL'
    report={'schema':SCHEMA,'status':result,'steps_completed':step,
            'stage_index':stage_index,'stage_radius':stages[min(stage_index,len(stages)-1)],
            'arithmetic_executor_used':False,'trainable_scope':'Qwen attention LoRA only',
            'test_read':False,'class_coded_residuals_not_freeform_integers':True}
    atomic_json(output/'training_result.json',report)
    print('HYBRID_BACKBONE_TRAIN_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def gate(*,device='cuda',output=ROOT,operand_dir=OPERAND_DIR,head1_dir=join.HEAD1,
         allow_download=False,model_loader=None,head1_override=None,consumption_dir=None):
    output=Path(output)
    if (output/'gate_result.json').exists():raise RuntimeError('BACKBONE_TEST_ALREADY_USED')
    config=json.loads((output/'config.json').read_text('utf8'))
    train_result=json.loads((output/'training_result.json').read_text('utf8'))
    if config['schema']!=SCHEMA or config['device']!=device or train_result['status']!='TRAINED_UNCERTIFIED':
        raise RuntimeError('BACKBONE_DEV_CERTIFICATION_REQUIRED')
    provider=stratified_provider(config['seed'])
    backbone,tokenizer,embedding,head1,sender,identity,hashes=load_certified_stack(
        device=device,head1_dir=head1_dir,operand_dir=operand_dir,allow_download=allow_download,
        model_loader=model_loader,head1_override=head1_override)
    provenance=_provenance(provider,identity,head1_dir,operand_dir,hashes)
    if provenance != config['provenance']:
        raise RuntimeError('BACKBONE_GATE_PROVENANCE_DRIFT')
    codebook=class_codes(tokenizer,16)
    if {str(k):v for k,v in codebook.items()} != config['class_tokens']:
        raise RuntimeError('BACKBONE_CLASS_TOKEN_DRIFT')
    lora_tools.install_lora(backbone,config['rank'],config['alpha'],
                            required_layers=getattr(backbone.config,'num_hidden_layers',None))
    if consumption_dir is None:
        from tools import nanojev_hybrid_emp_backbone_operand_consumption as consume
        consumption_dir=consume.ROOT
    proof=load_consumption_warmstart(backbone,consumption_dir,identity=identity,
        operand_hashes=hashes,rank=config['rank'],alpha=config['alpha'])
    if proof != config['consumption_hashes']:
        raise RuntimeError('BACKBONE_CONSUMPTION_GATE_PROVENANCE_DRIFT')
    saved=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
    if saved['schema']!=SCHEMA or saved['stage_index']!=len(config['stages']):
        raise RuntimeError('BACKBONE_NOT_FINISHED')
    lora_tools.restore_lora(backbone,saved['lora'])
    radius=config['max_residual']
    dev=evaluate(backbone,tokenizer,embedding,head1,sender,stage_cards(provider,'dev',radius),
                 codebook,radius,device)
    if not cell_gate(dev,radius,expected=expected_cells(provider,'dev',radius)):
        report={'schema':SCHEMA,'status':'BACKBONE_DEV_FAIL','gate_pass':False,
                'dev':dev,'test_read':False,'stage_radius':radius}
    else:
        test=evaluate(backbone,tokenizer,embedding,head1,sender,stage_cards(provider,'test',radius),
                      codebook,radius,device,styles=(0,1,2))
        passed=cell_gate(test,radius,expected=expected_cells(provider,'test',radius))
        report={'schema':SCHEMA,'status':'BACKBONE_ZERO_LINE_PASS' if passed else 'BACKBONE_ZERO_LINE_FAIL',
                'gate_pass':passed,'dev':dev,'test':test,'test_read':True,
                'stage_radius':radius,'qwen_consumed_edited_embeddings':True,
                'unmodified_number_embeddings_masked':True,
                'class_coded_residuals_not_freeform_integers':True}
    atomic_json(output/'gate_result.json',report)
    print('HYBRID_BACKBONE_GATE='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','train','gate','preview'),default='all')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--operand-output',type=Path,default=OPERAND_DIR)
    p.add_argument('--head1-output',type=Path,default=join.HEAD1)
    p.add_argument('--consumption-output',type=Path,default=None)
    p.add_argument('--max-residual',type=int,choices=STAGES,default=1)
    p.add_argument('--steps-per-stage',type=int,default=500)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--lr',type=float,default=0.0002)
    p.add_argument('--rank',type=int,default=8)
    p.add_argument('--alpha',type=float,default=16.)
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--stop-after',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage=='preview':
        pop=stratified_provider(args.seed)
        print('HYBRID_BACKBONE_PREVIEW='+json.dumps({
            'schema':SCHEMA,'train':len(pop.decks['train']),
            'dev':len(pop.decks['dev']),'test':len(pop.decks['test']),
            'radii':[r for r in STAGES if r<=args.max_residual],
            'delta_input_leak':False,'requires_head2_gate':True,
            'requires_qwen_operand_consumption_gate':True,
            'style_schedule':{str(r):style_schedule_audit(r)
                              for r in STAGES if r<=args.max_residual}},sort_keys=True))
        return 0
    common=dict(device=args.device,output=args.output,operand_dir=args.operand_output,
                head1_dir=args.head1_output,consumption_dir=args.consumption_output,
                allow_download=args.allow_model_download)
    if args.stage in ('train','all'):
        report=run(**common,max_residual=args.max_residual,steps_per_stage=args.steps_per_stage,
                   eval_every=args.eval_every,lr=args.lr,rank=args.rank,alpha=args.alpha,
                   seed=args.seed,stop_after=args.stop_after,resume=args.resume)
        if report['status']!='TRAINED_UNCERTIFIED':return 0
    if args.stage in ('gate','all'):gate(**common)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError,OSError,AssertionError,FileNotFoundError) as exc:
        print('HYBRID_BACKBONE_REFUSED='+str(exc),file=sys.stderr)
        sys.exit(2)
