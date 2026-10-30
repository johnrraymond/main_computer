#!/usr/bin/env python3
"""Answer-prefix A/B diagnostic for the read-only Qwen worded-number transfer smoke.

The original smoke allowed the completion to start with a new PROBLEM: packet.
This probe provides only the invariant ANSWER: format prefix (not any gold
number, word, label, or per-case hint), then measures genuine answer generation.
The decoder's trailing-newline behavior requires a separate prefix-safe decoder.

All Qwen, LoRA, and glass weights are frozen. Results are saved outside both
trained artifacts and the original 112-case smoke report directory.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path

from tools import nanojev_qwen_scale_transfer_smoke as transfer

SCHEMA = 'nanojev.qwen.worded-order-transfer-answer-prefix-probe.v1'


def _input_prefix(source: str, *, space: bool = False) -> str:
    """No target data. Never append a newline after the ANSWER: prefix."""
    return source + '\nANSWER:' + (' ' if space else '')


def _generate_prefilled(bb, tok, sources, device, max_tokens, max_new_tokens, *, space=False):
    """Clone ladder greedy decoding but DO NOT append a newline to the prefix.

    The existing ladder._greedy implicitly adds '\\n' to every input. Using
    that function with a prefilled answer would instead provide 'ANSWER:\\n',
    which is *not* the desired next-token context.
    """
    import torch
    head = bb.get_output_embeddings()
    decoder = bb.get_decoder()
    if head is None:
        raise RuntimeError('TRANSFER_PREFIX_LM_HEAD_UNAVAILABLE')
    pieces = [tok(_input_prefix(s,space=space),add_special_tokens=False)['input_ids']
              for s in sources]
    if any(not p or len(p)+max_new_tokens>max_tokens for p in pieces):
        raise ValueError('TRANSFER_PREFIX_CONTEXT_LIMIT')
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    if pad is None:
        raise ValueError('TRANSFER_PREFIX_NO_PAD_ID')
    width = max(map(len,pieces))
    size = len(pieces)
    ids = torch.full((size,width),int(pad),device=device,dtype=torch.long)
    mask = torch.zeros_like(ids)
    for i, p in enumerate(pieces):
        ids[i,width-len(p):] = torch.tensor(p,device=device)
        mask[i,width-len(p):] = 1
    emitted = [[] for _ in pieces]
    done = [False] * size
    eos = tok.eos_token_id
    eos_set = {int(eos)} if isinstance(eos,int) else set(eos or ())
    with torch.inference_mode():
        for _ in range(max_new_tokens):
            out = decoder(input_ids=ids,attention_mask=mask,
                          use_cache=False,return_dict=True)
            h = out.last_hidden_state[:,-1,:]
            weight = getattr(head,'weight',None)
            logits = head(h.to(dtype=weight.dtype if weight is not None else h.dtype))
            tokens = logits.argmax(dim=-1).tolist()
            for i, token in enumerate(tokens):
                if done[i]:
                    continue
                if token in eos_set:
                    done[i] = True
                    continue
                emitted[i].append(token)
                completion = tok.decode(emitted[i],skip_special_tokens=True)
                if '\n' in completion or '\r' in completion:
                    done[i] = True
            if all(done):
                break
            extra = torch.tensor([[int(pad) if done[i] else int(tokens[i])]
                                  for i in range(size)],device=device)
            ids = torch.cat((ids,extra),dim=1)
            active = torch.tensor([[0 if done[i] else 1] for i in range(size)],device=device)
            mask = torch.cat((mask,active),dim=1)
    return [tok.decode(tokens,skip_special_tokens=True) for tokens in emitted]


def _score_prefill(row, completion, *, prefix_space=False):
    # Preserve the exact produced text for strict grading, including whitespace.
    actual = 'ANSWER:' + (' ' if prefix_space else '') + completion
    grade = transfer.grade(row,actual)
    # A generated '1000' without the whitespace immediately after ':' is
    # semantically correct even if its literal format is not. Exact stays false.
    if not grade['normalized'] and completion and not completion[0].isspace():
        canonical = transfer.grade(row,'ANSWER: '+completion)
        if canonical['normalized']:
            grade['normalized'] = True
            grade['format_only_missing_space'] = True
    return grade


def _run_variant(args, cases, bb, tok, glass, label):
    head = bb.get_output_embeddings()
    context = glass.hook(head) if glass is not None else contextlib.nullcontext()
    results = []
    with context:
        for pos in range(0,len(cases),args.eval_batch):
            batch = cases[pos:pos+args.eval_batch]
            completions = _generate_prefilled(bb,tok,[r['source'] for r in batch],
                        args.device,args.max_tokens,args.max_new_tokens,
                        space=args.prefix_space)
            for row, raw in zip(batch,completions):
                results.append({**row,'generated_suffix':raw,
                                'grade':_score_prefill(row,raw,prefix_space=args.prefix_space)})
            if args.progress_every and (pos//args.eval_batch+1)%args.progress_every==0:
                print('TRANSFER_PREFIX_PROGRESS='+json.dumps({
                    'variant':label,'tested':len(results),'total':len(cases)
                },sort_keys=True),flush=True)
    return results


def _scores(rows):
    return {'tested':len(rows),
            'strict':sum(int(r['grade']['strict']) for r in rows),
            'normalized':sum(int(r['grade']['normalized']) for r in rows),
            'by_group':transfer._aggregate(rows),
            'missing_space_only':sum(int(r['grade'].get('format_only_missing_space',False)) for r in rows)}


def _hash_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    cases = transfer.make_deck()
    output = Path(args.probe_output).resolve()
    protected = Path(args.output).resolve()
    original = Path(args.raw_probe_output).resolve()
    if output == protected or output in protected.parents or protected in output.parents:
        raise RuntimeError('TRANSFER_PREFIX_OUTPUT_OVERLAPS_BACKBONE')
    if output == original or output in original.parents or original in output.parents:
        raise RuntimeError('TRANSFER_PREFIX_OUTPUT_OVERLAPS_RAW_RESULTS')
    output.mkdir(parents=True,exist_ok=True)
    deck_path = output/'transfer_cases.jsonl'
    deck_path.write_text(''.join(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n'
                        for r in cases),encoding='utf8')
    deck_sha = _hash_file(deck_path)
    if args.stage == 'deck':
        print('TRANSFER_PREFIX_DECK='+json.dumps({'cases':len(cases),
              'path':str(deck_path),'sha256':deck_sha},sort_keys=True),flush=True)
        return
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('TRANSFER_PREFIX_REQUIRES_STAGE1_AND_TEXT_CHECKPOINT')
    from tools import nanojev_qwen_lm_deformation_smoke as smoke
    bb,tok,zero,_ = transfer._load_model(args)
    glass = transfer._glass(args,bb,zero,smoke)
    lora_before = smoke._lora_digest(bb,zero)
    baseline = _run_variant(args,cases,bb,tok,None,'baseline')
    fixed = _run_variant(args,cases,bb,tok,glass,args.glass_mode) if glass is not None else None
    if smoke._lora_digest(bb,zero)!=lora_before:
        raise RuntimeError('TRANSFER_PREFIX_MUTATED_QWEN')
    report = {'schema':SCHEMA,'protocol':'source + newline + ANSWER: + optional_space',
              'prefix_space':args.prefix_space,'tested':len(cases),
              'deck_sha256':deck_sha,
              'baseline_sha256':_hash_file(Path(args.output)/'baseline.pt'),
              'baseline':_scores(baseline),'with_glass':_scores(fixed) if fixed is not None else None,
              'variant':args.glass_mode,
              'note':'Read-only answer-prefix protocol diagnostic; gold answers never enter the inference context.'}
    if fixed is not None:
        a={r['id'] for r in baseline if r['grade']['normalized']}
        b={r['id'] for r in fixed if r['grade']['normalized']}
        report['glass_effect']={'fixed':sorted(b-a),'regressed':sorted(a-b),'net':len(b)-len(a)}
    previous_path = original/'report.json'
    if previous_path.is_file():
        previous = json.loads(previous_path.read_text(encoding='utf8'))
        if (previous.get('deck_sha256')==deck_sha and
                previous.get('baseline_sha256')==report['baseline_sha256']):
            prior = {'baseline':previous['baseline']['normalized'],
                     'glass':previous.get('with_glass',{}).get('normalized') if previous.get('with_glass') else None}
            report['raw_protocol_reference']=prior
            report['prefix_vs_raw']={
                'baseline_gain':report['baseline']['normalized']-prior['baseline'],
                'glass_gain':(report['with_glass']['normalized']-prior['glass'])
                    if fixed is not None and prior['glass'] is not None else None,
            }
        else:
            report['raw_reference_ignored']='deck or baseline hash mismatch'
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf8')
    with (output/'predictions.jsonl').open('w',encoding='utf8') as handle:
        for label,entries in [('baseline',baseline),(args.glass_mode,fixed or [])]:
            for row in entries:
                handle.write(json.dumps({'variant':label,**row},ensure_ascii=False,sort_keys=True)+'\n')
    print('TRANSFER_PREFIX_RESULT='+json.dumps({
        'baseline':{k:report['baseline'][k] for k in ('strict','normalized','tested')},
        'glass':{k:report['with_glass'][k] for k in ('strict','normalized','tested')}
                  if fixed is not None else None,
        'glass_effect':report.get('glass_effect'),
        'prefix_vs_raw':report.get('prefix_vs_raw'),
        'report':str(output/'report.json'),
    },sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('deck','run'),default='deck')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--stage1-checkpoint')
    p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working')
    p.add_argument('--radii',default='0,1,2,3,4')
    p.add_argument('--max-rung',type=int,default=8)
    p.add_argument('--operand-limit',type=int,default=1024)
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_deformation_glass_smoke_v1'))
    p.add_argument('--probe-output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_worded_order_transfer_answer_prefix_probe_v1'))
    p.add_argument('--raw-probe-output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_worded_order_transfer_smoke_v1'))
    p.add_argument('--glass-mode',choices=('off','original','refined'),default='refined')
    p.add_argument('--prefix-space',action='store_true',help='Use ANSWER: [space] rather than ANSWER:')
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--eval-batch',type=int,default=4)
    p.add_argument('--max-tokens',type=int,default=512)
    p.add_argument('--max-new-tokens',type=int,default=128)
    p.add_argument('--progress-every',type=int,default=5)
    args=p.parse_args(argv)
    if args.eval_batch<1 or args.max_new_tokens<8 or args.max_tokens<=args.max_new_tokens:
        p.error('Invalid token or batch limits')
    return run(args)


if __name__=='__main__':main()
