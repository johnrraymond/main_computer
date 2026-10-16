#!/usr/bin/env python3
"""Read-only capability probe: current mix_v4 X/Y head against the Hawk–Dove ladder.

No database, optimizer, checkpoints, or curriculum plans are modified.
The synthetic rubric is a *teacher*, not empirical optimal captain behavior.

Example:
 python -m tools.nanojev_hawk_dove_baseline_probe --preview
 python -m tools.nanojev_hawk_dove_baseline_probe --device cuda --checkpoint latest
 python -m tools.nanojev_hawk_dove_baseline_probe --device cuda --scenarios all --states-per-scenario 3 --focus both --report runtime/hawk-probe.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import statistics
import sys
from types import SimpleNamespace

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from . import nanojev_hawk_dove_ladder_smoke as hawk

VERSION = 'hawk-dove-baseline-probe-v2-format-bridge'
CONTROL_LABELS = ('legacy_registered_logbar', 'legacy_plain_expression')

# Ordered single-factor contrasts rooted in the EXACT native mix_v4 prompt.
# Every parent is a previously defined contrast. The sample and target do not
# change, allowing a paired measurement of the effect of each intervention.
BRIDGE_STEPS = (
    ('B00_native', None, 'Exact registered_logbar prompt'),
    ('B01_short_x', 'B00_native', 'Shorten only the first register and its references'),
    ('B02_short_y', 'B00_native', 'Shorten only the second register and its references'),
    ('B03_short_xy', 'B01_short_x', 'Also shorten the second register'),
    ('B04_op_label', 'B00_native', 'Change only operation: to OP:'),
    ('B05_op_expression', 'B00_native', 'Change only operation sentence into expression'),
    ('B06_op_shorthand', 'B00_native', 'Use OP = SUB notation'),
    ('B07_short_xy_op', 'B03_short_xy', 'Use OP = SUB with short registers'),
    ('B08_case_tag', 'B00_native', 'Add one brief scenario tag to native prompt'),
    ('B09_categorical', 'B08_case_tag', 'Add explicit categorical state labels'),
    ('B10_short_jacket', 'B09_categorical', 'Add the captain short jacket'),
    ('B11_full_story', 'B00_native', 'Add entire natural-language problem to native prompt'),
    ('B12_hide_x', 'B11_full_story', 'Replace ONLY X numerical register by J semantic role'),
    ('B13_hide_y', 'B11_full_story', 'Replace ONLY Y numerical register by K semantic role'),
    ('B14_hide_both', 'B12_hide_x', 'Also replace Y register by K semantic role'),
)
BRIDGE_PARENT = {name:parent for name,parent,_ in BRIDGE_STEPS}
BRIDGE_NAMES = tuple(BRIDGE_PARENT)


def _append_before_answer(native: str, text: str) -> str:
    if not native.endswith('\nAnswer:'):
        raise RuntimeError('NATIVE_PROMPT_ANSWER_TERMINATOR_DRIFT')
    return native[:-len('\nAnswer:')] + '\n' + text + '\nAnswer:'


def build_bridge_prompts(case: ProbeCase, mix) -> dict[str, str]:
    """One-at-a-time interventions; all prompts share the EXACT same X/Y/OP.

    The native registered form is obtained by calling the trainer, never by
    reconstructing its six-character register grammar by hand.
    """
    x,y=case.targets['x'],case.targets['y']
    native=mix.curriculum_prompt(x,y,'written_sub','registered_logbar')
    lines=native.splitlines()
    if len(lines)!=4 or not lines[0].startswith('XXXXXX = ') or not lines[1].startswith('YYYYYY = ') or not lines[2].startswith('operation: ') or lines[3]!='Answer:':
        raise RuntimeError('NATIVE_REGISTER_PROMPT_DRIFT')
    operation=lines[2].removeprefix('operation: ')
    if operation != 'Subtract YYYYYY from XXXXXX.':
        raise RuntimeError('NATIVE_SUBTRACTION_TEMPLATE_DRIFT')
    scene=hawk.resolve_scenario(case.scenario_id)
    profile=hawk.resolve_captain(case.captain_id)
    state=case.state
    case_tag=f'CASE: {scene.asset} contest; choices: contest or yield.'
    state_text='OUR STATE: '+ '; '.join(f'{k}={getattr(state,k)}' for k in (
        'resource_priority','crew_danger','mission_dependency',
        'friendly_support','decision_horizon')) + '\nOPPONENT STATE: '+ '; '.join(
        f'{k}={getattr(state,k)}' for k in (
          'opponent_pressure','opponent_escalating','opponent_support'))
    full=hawk.full_prompt(state,profile,scene)
    variants={
      'B00_native':native,
      'B01_short_x':native.replace('XXXXXX','X'),
      'B02_short_y':native.replace('YYYYYY','Y'),
      'B03_short_xy':native.replace('XXXXXX','X').replace('YYYYYY','Y'),
      'B04_op_label':native.replace('operation: '+operation,'OP: '+operation),
      'B05_op_expression':native.replace('operation: '+operation,'operation: XXXXXX - YYYYYY'),
      'B06_op_shorthand':native.replace('operation: '+operation,'OP = SUB (XXXXXX - YYYYYY)'),
      'B07_short_xy_op':native.replace('XXXXXX','X').replace('YYYYYY','Y').replace(
          'operation: Subtract Y from X.','OP = SUB (X - Y)'),
      'B08_case_tag':_append_before_answer(native,case_tag),
      'B09_categorical':_append_before_answer(native,case_tag+'\n'+state_text),
      'B10_short_jacket':_append_before_answer(native,case_tag+'\n'+state_text+'\nJACKET: '+profile.jacket_short),
      'B11_full_story':_append_before_answer(native,full),
    }
    a=variants['B11_full_story']
    variants['B12_hide_x']=a.replace(lines[0],'XXXXXX = J(captain jacket, situation)',1)
    variants['B13_hide_y']=a.replace(lines[1],'YYYYYY = K(opponent evidence, situation)',1)
    variants['B14_hide_both']=variants['B12_hide_x'].replace(
        lines[1],'YYYYYY = K(opponent evidence, situation)',1)
    if set(variants)!=set(BRIDGE_NAMES) or any(not v.endswith('Answer:') for v in variants.values()):
        raise RuntimeError('BRIDGE_VARIANT_SCHEMA_DRIFT')
    return variants


def build_bridge_items(cases: list[ProbeCase], mix, answer_mode: str='paired') -> list[ProbeItem]:
    if answer_mode not in ('paired','present','absent'):
        raise ValueError('answer-mode must be paired, present or absent')
    rows=[]
    for case in cases:
        cid=f'{case.scenario_id}/{case.state_index:03d}/{case.captain_id}'
        for name,prompt in build_bridge_prompts(case,mix).items():
            for answer in (('present','absent') if answer_mode=='paired' else (answer_mode,)):
                displayed=prompt if answer=='present' else prompt.removesuffix('\nAnswer:')
                rows.append(ProbeItem(cid,f'{name}/{answer}',answer,displayed,
                    case.targets['x'],case.targets['y'],case.targets['op'],'oracle'))
    return rows


def summarize_bridge(records: list[dict], summary: dict, answer_mode: str) -> list[dict]:
    """Paired per-case changes relative to each intervention's declared parent.

    Do not conflate the two operation-ID conditioning modes. No baseline
    accuracy inference is made from a tiny six-case sample.
    """
    by_key={(r['case_id'],r['level'],r['condition']):r for r in records}
    out=[]
    for name,parent,meaning in BRIDGE_STEPS:
        for answer in ('present','absent'):
            key=f'{name}/{answer}'
            for mode in ('training','neutral'):
                met=summary['by_level'].get(f'{key}/{mode}')
                if not met:continue
                contrasts=[]
                if parent:
                    for r in records:
                        if r['level']!=key or r['condition']!=mode or r['status']!='scored': continue
                        before=by_key.get((r['case_id'],f'{parent}/{answer}',mode))
                        if before and before['status']=='scored':
                            contrasts.append({'case_id':r['case_id'],
                               'joint_accuracy_change':int(r['joint_5pct'])-int(before['joint_5pct']),
                               'x_abs_error_change':abs(r['pred_x']-r['truth_x'])-abs(before['pred_x']-before['truth_x']),
                               'y_abs_error_change':abs(r['pred_y']-r['truth_y'])-abs(before['pred_y']-before['truth_y']),
                               'operation_change':int(r['op_correct'])-int(before['op_correct'])})
                out.append({'name':name,'parent':parent,'meaning':meaning,'answer':answer,'condition':mode,
                    'n':met['scored'],'joint_5pct':met['joint_5pct'],'x_5pct':met['x_5pct'],
                    'y_5pct':met['y_5pct'],'op_accuracy':met['operation_accuracy'],
                    'max_tokens':met['max_token_count'],'paired_n':len(contrasts),
                    'paired_joint_delta':(round(sum(c['joint_accuracy_change'] for c in contrasts)/len(contrasts),4) if contrasts else None),
                    'paired_x_error_delta':(round(statistics.mean(c['x_abs_error_change'] for c in contrasts),1) if contrasts else None),
                    'paired_y_error_delta':(round(statistics.mean(c['y_abs_error_change'] for c in contrasts),1) if contrasts else None),
                    'paired_op_delta':(round(statistics.mean(c['operation_change'] for c in contrasts),4) if contrasts else None)})
    return out



@dataclass(frozen=True)
class ProbeCase:
    captain_id: str
    scenario_id: str
    state: hawk.State
    targets: dict
    state_index: int


@dataclass(frozen=True)
class ProbeItem:
    case_id: str
    level: str
    variant: str
    prompt: str
    x: int
    y: int
    op: str
    op_channel: str


def select_ids(text: str, catalog: dict, label: str) -> list[str]:
    values = list(catalog) if text == 'all' else [p.strip().lower() for p in text.split(',')]
    if not values or any(v not in catalog for v in values) or len(set(values)) != len(values):
        raise ValueError(f'Invalid {label} selection: {text}')
    return values


def state_samples(scenario: str, n: int, seed: int, overrides: dict[str,str] | None = None) -> list[hawk.State]:
    if n < 1 or n > math.prod(len(v) for v in hawk.STATE_CHOICES.values()):
        raise ValueError('states-per-scenario outside categorical grid')
    overrides = overrides or {}
    first = hawk.scenario_state(scenario, **overrides)
    if n == 1: return [first]
    keys = tuple(hawk.STATE_CHOICES)
    rng = random.Random(int(hashlib.sha256(f'{seed}:{scenario}'.encode()).hexdigest()[:16], 16))
    # The full grid is only 1,296 entries. Sample without replacement, exclude the default.
    grid = [hawk.State(**dict(zip(keys, values)))
            for values in itertools.product(*(
                (overrides[k],) if k in overrides else hawk.STATE_CHOICES[k]
                for k in keys))]
    grid.remove(first)
    if n - 1 > len(grid):
        raise ValueError('Requested more unique states than allowed by fixed state overrides')
    return [first, *rng.sample(grid, n - 1)]


def build_cases(captains: list[str], scenarios: list[str], n_states: int, seed: int, overrides: dict[str,str] | None = None) -> list[ProbeCase]:
    cases = []
    # Scenario -> common state -> all captains. Isolates the jacket variable.
    for scenario in scenarios:
        for idx, state in enumerate(state_samples(scenario, n_states, seed, overrides)):
            for captain in captains:
                labels = hawk.oracle(state, captain, scenario)
                cases.append(ProbeCase(captain, scenario, state, labels, idx))
    return cases


def channel_for_rung(level: int) -> str:
    # Same explicit-vs-masked policy used by existing mix_v4 Captain overlay.
    return 'oracle' if level <= 7 else 'masked'


def build_items(cases: list[ProbeCase], focus: str, include_controls: bool = True) -> list[ProbeItem]:
    if focus not in ('x', 'y', 'both'): raise ValueError('focus must be x, y, or both')
    rows: list[ProbeItem] = []
    seen: set[tuple[str, str]] = set()
    for case in cases:
        cid = f'{case.scenario_id}/{case.state_index:03d}/{case.captain_id}'
        for side in (('x','y') if focus == 'both' else (focus,)):
            for rung in hawk.build_ladder(case.state, case.targets, side, case.captain_id, case.scenario_id):
                # Identical rung text across focus variants should be probed once.
                key = (cid, f'{rung.level}:{rung.prompt}')
                if key in seen: continue
                seen.add(key)
                rows.append(ProbeItem(cid, f'L{rung.level:02d}', side, rung.prompt,
                                      case.targets['x'], case.targets['y'], case.targets['op'],
                                      channel_for_rung(rung.level)))
        if include_controls:
            # Exact same numeric X/Y as the captain task, but using *current* v4 prompts.
            # Filled later after lazy-importing training implementation.
            for level in CONTROL_LABELS:
                rows.append(ProbeItem(cid, level, 'control', '', case.targets['x'],
                                      case.targets['y'], case.targets['op'],
                                      'oracle' if level == CONTROL_LABELS[0] else 'masked'))
    return rows


def resolve_prompt(item: ProbeItem, mix) -> str:
    if item.level == CONTROL_LABELS[0]:
        return mix.curriculum_prompt(item.x, item.y, 'written_sub', 'registered_logbar')
    if item.level == CONTROL_LABELS[1]:
        return mix.curriculum_prompt(item.x, item.y, 'written_sub', 'plain_expression')
    return item.prompt


def summarize(records: list[dict], *, min_cases: int = 30,
              accuracy_floor: float = .8, op_floor: float = .8) -> dict:
    """Diagnostic, not a claim of proficiency from tiny samples."""
    groups = defaultdict(list)
    for record in records: groups[(record['level'],record['condition'])].append(record)
    summary = {}
    for (level, condition), rows in sorted(groups.items()):
        usable = [r for r in rows if r.get('status') == 'scored']
        n = len(usable)
        independent_cases = len({r.get('case_id', id(r)) for r in usable})
        def rate(field): return round(sum(bool(r[field]) for r in usable) / n, 4) if n else None
        summary[f'{level}/{condition}'] = {
            'cases':len(rows), 'scored':n, 'independent_cases':independent_cases, 'unscorable':len(rows)-n,
            'joint_5pct': rate('joint_5pct'),
            'joint_1pct': rate('joint_1pct'),
            'x_5pct':rate('x_5pct'), 'y_5pct':rate('y_5pct'),
            'operation_accuracy':rate('op_correct'),
            'decision_accuracy':rate('decision_correct'),
            'joint_5pct_and_operation':rate('joint_op'),
            'median_abs_x_error':round(statistics.median(abs(r['pred_x']-r['truth_x']) for r in usable),1) if n else None,
            'median_abs_y_error':round(statistics.median(abs(r['pred_y']-r['truth_y']) for r in usable),1) if n else None,
            'max_token_count':max((r['tokens'] for r in rows), default=0),
            'meets_floor':bool(independent_cases >= min_cases and len(rows) == n and
                 rate('joint_5pct') >= accuracy_floor and rate('op_correct') >= op_floor),
        }
    # The longest validated prefix is more defensible than selecting an isolated high rung.
    contiguous = []
    first_gap = None
    for level in range(11) if any(k.startswith('L00/') for k in summary) else ():
        key = f'L{level:02d}/training'
        metric = summary.get(key)
        if not metric or not metric['meets_floor']:
            first_gap = f'L{level:02d}'
            break
        contiguous.append(f'L{level:02d}')
    return {'by_level':summary, 'highest_contiguous_pass':contiguous[-1] if contiguous else None,
            'first_not_qualified':first_gap,
            'qualification_warning':(f'Rungs require at least {min_cases} distinct captain–scenario–state cases, no token-length exclusions, '
                                     f'joint X/Y 5% >= {accuracy_floor:.0%}, operation >= {op_floor:.0%}. '
                                     'A small smoke is diagnostic, not a promotion decision.'),
            'operation_warning':'All Hawk–Dove examples use SUB; operation accuracy here does not measure multi-operation generalization.',
            'teacher_warning':'Targets come from synthetic deterministic rubrics, not measured captain behavior.'}


def evaluate(args, cases: list[ProbeCase]) -> dict:
    import numpy as np
    import torch
    import nanojev_altreg_independent_math_qwen_precision_v2 as base
    import nanojev_altreg_math_v3_decoder_experiment as v3
    import nanojev_altreg_attention_curriculum_mix_v4 as mix

    if args.device == 'auto': args.device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    checkpoint_path = args.output_root.expanduser().resolve() / (args.checkpoint + '.pt')
    if not checkpoint_path.is_file(): raise FileNotFoundError(f'Checkpoint not found: {checkpoint_path}')
    saved = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    cfg = saved.get('config', {})
    if saved.get('schema') != mix.SCHEMA or cfg.get('schema') != mix.SCHEMA:
        raise RuntimeError('Probe requires a mix_v4 exact-mix checkpoint')
    if cfg.get('backbone') != 'qwen' or cfg.get('model_id') != base.MODEL_ID:
        raise RuntimeError('Unexpected backbone identity')
    # Confirm the actual parent checkpoint matches the one used by training.
    p_args = SimpleNamespace(parent_root=Path(cfg['parent_root']),
                             parent_checkpoint=cfg['parent_checkpoint'],
                             backbone='qwen', device=args.device)
    parent_cp, _, parent, parent_hash = v3.get_parent(base,p_args)
    if parent_hash != cfg['parent_sha256']:
        raise RuntimeError('Parent checkpoint hash changed; refusing to probe')
    token_dim = (parent_cp['config']['input_dim']//8)*2
    decoder = v3.V3Decoder(parent_cp['config']['width'],len(base.OP_IDS),'attention',
                           math.floor(math.log10(base.registers.MAX_VALUE)),
                           cfg['hidden'],token_dim).to(args.device)
    op_head = mix.OperationHead(token_dim,cfg['hidden']).to(args.device)
    decoder.load_state_dict(saved['head'], strict=True)
    op_head.load_state_dict(saved['operation_head'], strict=True)
    decoder.eval();op_head.eval()
    parent.eval()
    for model in (decoder,op_head,parent):
        for p in model.parameters():p.requires_grad_(False)
    c=SimpleNamespace(device=args.device,dtype=args.dtype,
                      allow_model_download=args.allow_model_download)
    _,backbone,tokenizer,model_device,_ = base.cassette.load_source('qwen',c)
    if str(model_device) != args.device:
        raise RuntimeError('Frozen backbone device mismatch')
    backbone.eval()
    for p in backbone.parameters():p.requires_grad_(False)
    limit = args.max_tokens or int(cfg['max_prompt_tokens'])
    print(f'PROBE CHECKPOINT step={saved["step"]} path={checkpoint_path} '
          f'token_limit={limit} trained_limit={cfg["max_prompt_tokens"]} cases={len(cases)}',flush=True)
    if limit > int(cfg['max_prompt_tokens']):
        print('WARNING: token limit extended beyond training checkpoint setting; report is out-of-distribution.',flush=True)

    items=[]
    if args.suite in ('ladder','both'):
        items.extend(build_items(cases,args.focus,not args.no_controls))
    if args.suite in ('bridge','both'):
        items.extend(build_bridge_items(cases,mix,args.answer_mode))
    records=[];eligible=[]
    for item in items:
        prompt=resolve_prompt(item,mix)
        if args.answer_suffix and item.variant not in ('present','absent') and not prompt.rstrip().endswith('Answer:'):
            prompt=prompt.rstrip()+'\nAnswer:'
        n_tok=len(tokenizer(prompt,add_special_tokens=False)['input_ids'])
        row={'case_id':item.case_id,'level':item.level,'variant':item.variant,
             'prompt_sha256':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
             'condition':'training','tokens':n_tok,'truth_x':item.x,'truth_y':item.y,
             'op':item.op,'op_channel':item.op_channel}
        if n_tok < 4 or n_tok > limit:
            row.update(status='token_limit', reason=f'{n_tok} tokens, allowed 4..{limit}')
            records.append(row)
        else:
            eligible.append((item,prompt,row))
    # Group by effective token length to minimize padding; preserve deterministic ordering.
    eligible.sort(key=lambda t:(t[2]['tokens'],t[2]['case_id'],t[2]['level'],t[2]['variant']))
    total_batches=(len(eligible)+args.batch_size-1)//args.batch_size
    with torch.no_grad():
        for j in range(0,len(eligible),args.batch_size):
            batch=eligible[j:j+args.batch_size]
            prompts=[b[1] for b in batch]
            flat,seq,mask=v3.full_capture(backbone,tokenizer,args.device,prompts,limit)
            h=parent.extract(flat.to(args.device))
            p_xy=parent.xy_coord(h)
            op_logits=op_head(seq.to(args.device),mask.to(args.device))
            classified=op_logits.argmax(axis=-1).cpu().tolist()
            operations=torch.tensor(
                [0 if item.op_channel == 'masked' else base.OP_IDS['written_sub']
                 for item,_,_ in batch],device=args.device,dtype=torch.long)
            input_seq=seq.to(args.device)
            input_mask=mask.to(args.device)
            # Two batched evaluations, no gradients and no checkpoint modifications.
            predictions={}
            for mode, op_ids in (('training',operations),('neutral',torch.zeros_like(operations))):
                decoder_out=decoder(h,p_xy,op_ids,input_seq,input_mask)
                predictions[mode]=v3.predict(decoder_out,base,'attention').cpu().numpy()
            for i,(item,prompt,row) in enumerate(batch):
                for mode in ('training','neutral'):
                    x,y=map(float,predictions[mode][i])
                    denom_x=max(1.,abs(item.x));denom_y=max(1.,abs(item.y))
                    x5=math.isfinite(x) and abs(x-item.x)<=.05*denom_x
                    y5=math.isfinite(y) and abs(y-item.y)<=.05*denom_y
                    x1=math.isfinite(x) and abs(x-item.x)<=.01*denom_x
                    y1=math.isfinite(y) and abs(y-item.y)<=.01*denom_y
                    z=x-y
                    def sign(v): return 1 if v>0 else -1 if v<0 else 0
                    out={**row,'condition':mode,'status':'scored',
                         'pred_x':x,'pred_y':y,'pred_z':z,
                         'pred_op':mix.OP_CLASSES[classified[i]],
                         'op_correct':mix.OP_CLASSES[classified[i]]=='subtract',
                         'x_5pct':x5,'y_5pct':y5,'joint_5pct':x5 and y5,
                         'joint_1pct':x1 and y1,
                         'joint_op':x5 and y5 and mix.OP_CLASSES[classified[i]]=='subtract',
                         'decision_correct':math.isfinite(z) and sign(z)==sign(item.x-item.y)}
                    records.append(out)
            print(f'  inference batches: {min(j//args.batch_size+1,total_batches)}/{total_batches}',flush=True)
    # Duplicate ineligible records for both effective conditioning analyses.
    prepared=[]
    for row in records:
        if row['status'] != 'scored':
            for mode in ('training','neutral'):
                prepared.append({**row,'condition':mode})
        else:prepared.append(row)
    summary=summarize(prepared,min_cases=args.min_cases,
                      accuracy_floor=args.accuracy_floor,op_floor=args.op_floor)
    bridge_summary=summarize_bridge(prepared,summary,args.answer_mode) if args.suite in ('bridge','both') else []
    failures=[r for r in prepared if r['status']=='scored' and r['condition']=='training' and not r['joint_op']]
    failures.sort(key=lambda r:(r['level'],r['case_id']))
    per_level=defaultdict(int)
    selected_failures=[]
    for r in failures:
        if per_level[r['level']] >= 2: continue
        selected_failures.append(r)
        per_level[r['level']] += 1
        if len(selected_failures) >= args.failure_examples: break
    return {'version':VERSION,'checkpoint':str(checkpoint_path),
            'checkpoint_sha256':v3.sha256(checkpoint_path),
            'ladder_sha256':v3.sha256(Path(hawk.__file__)),
            'checkpoint_step':int(saved['step']),'seed':args.seed,
            'captains':args.captains,'scenarios':args.scenarios,
            'states_per_scenario':args.states_per_scenario,
            'max_tokens':limit,'trained_max_tokens':int(cfg['max_prompt_tokens']),
            'cases':len(cases),
            'case_manifest':[{'id':f'{c.scenario_id}/{c.state_index:03d}/{c.captain_id}',
                 'state':asdict(c.state),'targets':{k:c.targets[k] for k in ('x','y','op','z')}} for c in cases],
            'suite':args.suite,'answer_mode':args.answer_mode,
            'bridge_steps':[{'name':n,'parent':par,'meaning':why} for n,par,why in BRIDGE_STEPS],
            'bridge_summary':bridge_summary,
            'summary':summary, 'failure_examples':selected_failures,
            'records':prepared}


def print_summary(report):
    print('\nHAWK–DOVE FORMAT BRIDGE — ACTUAL FROZEN BACKBONE + TRAINED HEAD (READ-ONLY)')
    print(f'Checkpoint step: {report["checkpoint_step"]}; canonical cases: {report["cases"]}')
    if report.get('bridge_summary'):
        print('Bridge  Answer  Condition  n  joint5%   X5%     Y5%     OP    delta joint (vs parent)')
        for r in report['bridge_summary']:
            if r['condition']!='training':continue
            def pc(v): return f'{v:6.1%}' if v is not None else '   n/a'
            print(f'{r["name"]:<20} {r["answer"]:<7} {r["condition"]:<8} {r["n"]:>3} '
                  f'{pc(r["joint_5pct"])} {pc(r["x_5pct"])} {pc(r["y_5pct"])} '
                  f'{pc(r["op_accuracy"])}  {pc(r["paired_joint_delta"])}')
        print('One feature changes relative to the named parent, except explicitly combined controls.')
        print('For each prompt, the JSON also contains neutral operation-ID results and paired absolute-error changes.')
        print('Do not infer a training entry rung from this six-case smoke.')
    if any(level+'/training' in report['summary']['by_level'] for level in CONTROL_LABELS):
        print('\nEXISTING LADDER / LEGACY CONTROLS')
        print('Rung                     valid   joint5%  X5%    Y5%    OP     decision | neutral joint5%')
        for level in [*CONTROL_LABELS,*[f'L{k:02d}' for k in range(11)]]:
            row=report['summary']['by_level'].get(level+'/training',{})
            if not row: continue
            neu=report['summary']['by_level'].get(level+'/neutral',{})
            def pc(v): return f'{v:.1%}' if v is not None else '  -- '
            print(f'{level:<24} {row.get("scored",0):>3}/{row.get("cases",0):<3} '
                  f'{pc(row.get("joint_5pct")):>6} {pc(row.get("x_5pct")):>6} '
                  f'{pc(row.get("y_5pct")):>6} {pc(row.get("operation_accuracy")):>6} '
                  f'{pc(row.get("decision_accuracy")):>7} | {pc(neu.get("joint_5pct"))}')
        print('\nHighest contiguous qualified rung:',report['summary']['highest_contiguous_pass'] or 'none')
        print('First unqualified rung:',report['summary']['first_not_qualified'] or 'not evaluated')
    print(report['summary']['qualification_warning'])
    print(report['summary']['operation_warning'])
    print(report['summary']['teacher_warning'])
    if report['failure_examples']:
        print('\nREPRESENTATIVE FAILURE EXAMPLES (full records in JSON):')
        for r in report['failure_examples']:
            print(f'  {r["level"]} {r["case_id"]}: truth=({r["truth_x"]},{r["truth_y"]}) '
                  f'pred=({r["pred_x"]:.1f},{r["pred_y"]:.1f}) '
                  f'op={r["pred_op"]} tokens={r["tokens"]}')


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_curriculum_v4_exact_mix')
    p.add_argument('--checkpoint',choices=('latest','best'),default='latest')
    p.add_argument('--device',choices=('auto','cuda','cpu'),default='auto')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--captains',default='all',help='all or comma-separated captain IDs')
    p.add_argument('--scenarios',default='s01',help='all or comma-separated scenario IDs')
    p.add_argument('--states-per-scenario',type=int,default=1,help='Default state plus deterministic variants shared by all captains')
    p.add_argument('--suite',choices=('bridge','ladder','both'),default='bridge',
                   help='bridge isolates prompt changes; ladder reproduces original probe; both combines them')
    p.add_argument('--answer-mode',choices=('paired','present','absent'),default='paired',
                   help='Bridge prompt terminator ablation; paired runs both for each identical case')
    p.add_argument('--focus',choices=('x','y','both'),default='y',help='Scaffolding removal orientation')
    for key,choices in hawk.STATE_CHOICES.items():
        p.add_argument('--'+key.replace('_','-'),choices=choices,default=None,
                       help='Fix this qualitative condition for every sampled scenario state')
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--min-cases',type=int,default=30,help='Minimum independently labeled cases before qualifying a rung')
    p.add_argument('--accuracy-floor',type=float,default=.8)
    p.add_argument('--op-floor',type=float,default=.8)
    p.add_argument('--max-tokens',type=int,default=None,help='Override checkpoint token limit (reported as OOD if increased)')
    p.add_argument('--answer-suffix',action='store_true',help='Append Answer: to prompts missing it (separate formatting experiment)')
    p.add_argument('--no-controls',action='store_true')
    p.add_argument('--failure-examples',type=int,default=22)
    p.add_argument('--report',type=Path,help='Optional JSON report path; never writes to training output directory by default')
    p.add_argument('--preview',action='store_true',help='Print raw problem and ladder with teacher targets; no model/checkpoint load')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.batch_size<1 or args.states_per_scenario<1 or args.min_cases<1 or not 0<args.accuracy_floor<=1 or not 0<args.op_floor<=1:
        raise SystemExit('Invalid sample counts or accuracy floors')
    args.captains=select_ids(args.captains,hawk.BY_ID,'captain')
    args.scenarios=select_ids(args.scenarios,hawk.SCENARIO_BY_ID,'scenario')
    overrides={k:getattr(args,k) for k in hawk.STATE_CHOICES if getattr(args,k) is not None}
    cases=build_cases(args.captains,args.scenarios,args.states_per_scenario,args.seed,overrides)
    if args.preview:
        first=cases[0]
        print(f'PREVIEW: {len(cases)} captain–scenario–state cases; model NOT loaded')
        for rung in reversed(hawk.build_ladder(first.state,first.targets,args.focus if args.focus!='both' else 'y',first.captain_id,first.scenario_id)):
            print(f'\n=== {rung.key} L{rung.level:02d} ===\n{rung.prompt}')
        if args.suite in ('bridge','both'):
            import nanojev_altreg_attention_curriculum_mix_v4 as mix
            print('\nFORMAT BRIDGE CONTRASTS (native grammar first):')
            prompts=build_bridge_prompts(first,mix)
            for name,parent,meaning in BRIDGE_STEPS:
                print(f'\n=== {name} (parent={parent or None}): {meaning} ===\n{prompts[name]}')
        print('\nSynthetic teacher targets:',first.targets['x'],first.targets['y'],first.targets['op'])
        return 0
    report=evaluate(args,cases)
    print_summary(report)
    if args.report:
        dest=args.report.expanduser().resolve()
        # Prevent an accidental overwrite of a checkpoint with report JSON.
        if dest.suffix.lower() != '.json': raise SystemExit('Report requires .json suffix')
        if dest.is_file(): raise SystemExit(f'Report exists; refusing overwrite: {dest}')
        if dest==args.output_root.expanduser().resolve(): raise SystemExit('Invalid report destination')
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print('Report:',dest)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
