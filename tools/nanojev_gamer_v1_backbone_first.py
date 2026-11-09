#!/usr/bin/env python3
"""GAMER direct neural inference: raw prompt -> frozen Qwen/LoRA -> decision head.

Inference uses ONE contextual Qwen forward pass, NO next-token generation,
NO game-name selection or task-specific parser, and NO supervised labels.
The head selects spans naming actions in the user prompt and ranks them.
It does not reconstruct an exact payoff matrix or certify arbitrary game skill.
The preserved exact executor below is for independently verified training
oracles and optional standalone state validation; query never invokes it.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path

SCHEMA = 'nanojev.gamer.v1.direct-neural-decision.v4'
MAX_FACTS = 128
MAX_ACTIONS = 64
MAX_NODES = 512
MAX_DEPTH = 24
MAX_NUMERATOR_BITS = 8192

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda: handle.read(1 << 20), b''):
            h.update(data)
    return h.hexdigest()


def adapter_fingerprint(directory):
    directory = Path(directory)
    files = sorted(f for f in directory.rglob('*') if f.is_file())
    if not files or not (directory / 'adapter_config.json').is_file():
        raise FileNotFoundError('GAMER_ADAPTER_FILES_MISSING')
    h = hashlib.sha256()
    for file in files:
        relative = file.relative_to(directory).as_posix().encode()
        h.update(len(relative).to_bytes(4, 'big') + relative)
        h.update(bytes.fromhex(sha256(file)))
    return h.hexdigest()


def _fraction(value):
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError('GAMER_IR_NUMBER_MUST_BE_EXACT_STRING')
    if not value or len(value) > 160:
        raise ValueError('GAMER_IR_NUMBER_SIZE')
    try:
        n = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('GAMER_IR_INVALID_NUMBER') from exc
    if n.numerator.bit_length() > MAX_NUMERATOR_BITS or n.denominator.bit_length() > MAX_NUMERATOR_BITS:
        raise ValueError('GAMER_IR_NUMBER_OVERFLOW')
    return n


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError('GAMER_IR_INVALID_' + name)
    return value


def validate_state(state):
    """Validate model-produced IR only; never inspect raw prompt for meaning."""
    if not isinstance(state, dict) or state.get('status') not in ('ok','insufficient_information'):
        raise ValueError('GAMER_IR_INVALID_STATUS')
    if state['status'] == 'insufficient_information':
        if set(state) != {'status','missing'} or not isinstance(state['missing'], list) or not state['missing']:
            raise ValueError('GAMER_IR_INVALID_ABSTENTION')
        for entry in state['missing']:
            _text(entry, 'MISSING_DESCRIPTION')
        return state
    if set(state) != {'status','objective','facts','actions'}:
        raise ValueError('GAMER_IR_INVALID_FIELDS')
    if state['objective'] not in ('maximize', 'minimize'):
        raise ValueError('GAMER_IR_INVALID_OBJECTIVE')
    facts = state['facts']
    actions = state['actions']
    if not isinstance(facts, dict) or not 1 <= len(facts) <= MAX_FACTS:
        raise ValueError('GAMER_IR_INVALID_FACTS')
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise ValueError('GAMER_IR_INVALID_ACTIONS')
    for key, value in facts.items():
        _text(key,'FACT_ID')
        _fraction(value)
    names = set()
    nodes = [0]

    def check(expr, depth):
        nodes[0] += 1
        if depth > MAX_DEPTH or nodes[0] > MAX_NODES:
            raise ValueError('GAMER_IR_EXPRESSION_LIMIT')
        if not isinstance(expr, dict):
            raise ValueError('GAMER_IR_INVALID_EXPRESSION')
        if set(expr) == {'ref'}:
            if not isinstance(expr['ref'], str) or expr['ref'] not in facts:
                raise ValueError('GAMER_IR_UNKNOWN_REFERENCE')
            return
        if set(expr) != {'op','args'}:
            raise ValueError('GAMER_IR_INVALID_EXPRESSION')
        op = expr['op']
        args = expr['args']
        if op not in ('add','sub','mul','div','neg') or not isinstance(args,list):
            raise ValueError('GAMER_IR_UNSUPPORTED_OPERATOR')
        if not ((op == 'neg' and len(args)==1) or
                (op in ('sub','div') and len(args)==2) or
                (op in ('add','mul') and 2 <= len(args) <= 32)):
            raise ValueError('GAMER_IR_INVALID_ARITY')
        for arg in args:
            check(arg, depth+1)

    for action in actions:
        if not isinstance(action, dict) or set(action) != {'name','score'}:
            raise ValueError('GAMER_IR_INVALID_ACTION')
        name = _text(action['name'],'ACTION_NAME')
        if name in names:
            raise ValueError('GAMER_IR_DUPLICATE_ACTION')
        names.add(name)
        check(action['score'],0)
    return state


def execute_state(state):
    """Perform generic arithmetic only after model-produced state is validated."""
    validate_state(state)
    if state['status'] == 'insufficient_information':
        return {'status':'insufficient_information', 'missing':state['missing']}
    facts = {name:_fraction(number) for name,number in state['facts'].items()}

    def run(expr):
        if 'ref' in expr:
            return facts[expr['ref']]
        op = expr['op']
        values = [run(x) for x in expr['args']]
        if op == 'add':
            answer = sum(values, Fraction(0))
        elif op == 'mul':
            answer = Fraction(1)
            for value in values:answer *= value
        elif op == 'sub':
            answer = values[0] - values[1]
        elif op == 'div':
            if values[1] == 0:raise ValueError('GAMER_IR_DIVISION_BY_ZERO')
            answer = values[0] / values[1]
        else:
            answer = -values[0]
        if answer.numerator.bit_length() > MAX_NUMERATOR_BITS or answer.denominator.bit_length() > MAX_NUMERATOR_BITS:
            raise ValueError('GAMER_IR_RESULT_OVERFLOW')
        return answer

    scores = {a['name']:run(a['score']) for a in state['actions']}
    extreme = (max if state['objective']=='maximize' else min)(scores.values())
    winners = [name for name,value in scores.items() if value == extreme]
    return {'status':'evaluated','objective':state['objective'],
            'scores':{name:str(value) for name,value in scores.items()},
            'decisions':winners,'tie':len(winners)>1}




def prompt_messages(prompt):
    """No generation instruction or interpretation; raw prompt is the user turn."""
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('GAMER_EMPTY_PROMPT')
    return [{'role':'user','content':prompt}]


def format_prompt(tokenizer,prompt):
    messages=prompt_messages(prompt)
    try:
        return tokenizer.apply_chat_template(messages, tokenize=False,
                         add_generation_prompt=False, enable_thinking=False)
    except TypeError:
        return tokenizer.apply_chat_template(messages, tokenize=False,
                         add_generation_prompt=False)


def prepare_prompt(tokenizer,prompt,*,max_input_tokens=2048):
    """Tokenization and offsets only. No action/payoff/number inspection."""
    import torch
    text=format_prompt(tokenizer,prompt)
    start=text.rfind(prompt)
    if start<0:raise ValueError('GAMER_CHAT_TEMPLATE_LOST_ORIGINAL_PROMPT')
    result=tokenizer(text,return_offsets_mapping=True,add_special_tokens=False,
                     return_tensors='pt',truncation=False)
    ids=result['input_ids']
    if ids.shape[0]!=1 or ids.shape[1]>max_input_tokens:
        raise ValueError('GAMER_PROMPT_TOO_LONG_NO_TRUNCATION')
    offsets=result['offset_mapping'][0].tolist()
    mask=torch.tensor([(a>=start and b<=start+len(prompt) and b>a)
                       for a,b in offsets],dtype=torch.bool)
    if not bool(mask.any()):raise ValueError('GAMER_RAW_PROMPT_HAS_NO_TOKEN_SPANS')
    return {'ids':ids,'attention':result['attention_mask'],
            'offsets':offsets,'eligible':mask,'prompt_start':start,
            'prompt':prompt,'formatted':text}


def frozen_context(model,prepared,device='cuda'):
    """Exactly one frozen Qwen/LoRA forward pass; zero generated tokens."""
    import torch
    if any(p.requires_grad for p in model.parameters()):
        raise RuntimeError('GAMER_BACKBONE_MUST_BE_FROZEN')
    model.eval()
    with torch.inference_mode():
        out=model(input_ids=prepared['ids'].to(device),
                  attention_mask=prepared['attention'].to(device),
                  output_hidden_states=True,use_cache=False)
    if not out.hidden_states:
        raise RuntimeError('GAMER_QWEN_HIDDEN_STATES_MISSING')
    return out.hidden_states[-1][0].detach().clone()  # normal tensor for trainable head


def model_components(model_id, adapter, device):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel
    if str(device).startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_CUDA_UNAVAILABLE')
    tok=AutoTokenizer.from_pretrained(model_id,trust_remote_code=False,use_fast=True)
    if not tok.is_fast:raise RuntimeError('GAMER_FAST_TOKENIZER_REQUIRED_FOR_POINTER_OUTPUT')
    dtype=torch.float16 if str(device).startswith('cuda') else torch.float32
    model=AutoModelForCausalLM.from_pretrained(model_id,trust_remote_code=False,dtype=dtype).to(device)
    model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False)
    for p in model.parameters():p.requires_grad_(False)
    model.eval()
    return model,tok


def load_head_from_checkpoint(checkpoint,manifest,*,device):
    from safetensors.torch import load_file
    from tools.nanojev_gamer_v1_backbone_first_head import GamerDirectHead
    head_path=Path(checkpoint)/'gamer_head.safetensors'
    if not head_path.is_file() or sha256(head_path)!=manifest.get('head_sha256'):
        raise ValueError('GAMER_DIRECT_HEAD_LINEAGE_MISMATCH')
    settings=manifest['head']
    if settings.get('type')!='contextual-player-opponent-role-and-coupled-preference':
        raise ValueError('GAMER_LEGACY_TOKEN_GENERATOR_FORBIDDEN')
    head=GamerDirectHead(hidden_size=settings['hidden_size'],
                         width=settings['width'],layers=settings['layers'])
    head.load_state_dict(load_file(str(head_path),device='cpu'),strict=True)
    return head.to(device).eval()


def direct_decide(head,hidden,prepared):
    from tools.nanojev_gamer_v1_backbone_first_head import (decode_action_spans, coupled_start_logits)
    import torch
    head.eval()
    with torch.inference_mode():
        tags,preferences=head(hidden.to(next(head.parameters()).device))
    choices=decode_action_spans(tags.argmax(-1).tolist(),
                coupled_start_logits(tags,preferences).tolist(),prepared['eligible'].tolist(),
                prepared['offsets'],prepared['prompt_start'],prepared['prompt'])
    if not choices:
        return {'status':'NO_NEURAL_ACTION_SPANS','decisions':[],
                'neural_actions':[],'certified':False}
    return {'status':'NEURAL_DECISION_UNCERTIFIED',
            'decisions':[choices[0]['name']],
            'neural_actions':choices,'certified':False}


def query(*,checkpoint,prompt,device='cuda',max_input_tokens=2048):
    """Production-facing diagnostic: no game identity, no semantic parser, no generation."""
    checkpoint=Path(checkpoint)
    report=checkpoint/'manifest.json'
    if not report.is_file():raise FileNotFoundError('GAMER_NEURAL_CHECKPOINT_MISSING')
    manifest=json.loads(report.read_text(encoding='utf-8'))
    if (manifest.get('schema')!=SCHEMA or
        manifest.get('status')!='HEAD_TRAINED_NOT_CERTIFIED' or
        manifest.get('backbone_frozen') is not True or
        manifest.get('lora_frozen') is not True):
        raise ValueError('GAMER_DIRECT_CHECKPOINT_INVALID_OR_LEGACY_TOKEN_GENERATOR')
    source=Path(manifest['source_adapter'])
    if adapter_fingerprint(source)!=manifest.get('source_adapter_sha256'):
        raise ValueError('GAMER_SOURCE_ADAPTER_LINEAGE_MISMATCH')
    model,tok=model_components(manifest['model_id'],source,device)
    head=load_head_from_checkpoint(checkpoint,manifest,device=device)
    prepared=prepare_prompt(tok,prompt,max_input_tokens=max_input_tokens)
    hidden=frozen_context(model,prepared,device=device)
    result=direct_decide(head,hidden,prepared)
    return {**result,'source_adapter_sha256':manifest['source_adapter_sha256'],
            'gamer_head_sha256':manifest['head_sha256'],
            'backbone_frozen':True,'lora_frozen':True,
            'game_selected_externally':False,'semantic_parser_used':False,
            'generated_tokens':0,'production_promotion':False}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('query','validate-state'))
    ap.add_argument('--checkpoint',type=Path)
    ap.add_argument('--prompt')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--max-input-tokens',type=int,default=2048)
    ap.add_argument('--state-json',type=Path)
    args=ap.parse_args(argv)
    if args.command=='validate-state':
        if args.state_json is None:ap.error('validate-state requires --state-json')
        state=validate_state(json.loads(args.state_json.read_text(encoding='utf-8')))
        result={'state':state,'evaluation':execute_state(state)}
    else:
        if args.checkpoint is None or args.prompt is None:
            ap.error('query requires --checkpoint and --prompt')
        result=query(checkpoint=args.checkpoint,prompt=args.prompt,
                     device=args.device,max_input_tokens=args.max_input_tokens)
    print(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True))
    return result

if __name__=='__main__':main()
