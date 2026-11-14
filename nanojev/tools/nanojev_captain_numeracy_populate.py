#!/usr/bin/env python3
"""Populate a sealed SQLite Pass-One captain-domain NUMERACY curriculum bank.

No game decision labels, no model imports, no training, no GPU, no side-channel
changes. The bank contains canonical X/Y/operation/result targets and graded
presentations of each problem, using the existing TrainingTaskStore v1.

Run from the repository root:
  python -m tools.nanojev_captain_numeracy_populate --self-test
  python -m tools.nanojev_captain_numeracy_populate --preview
  python -m tools.nanojev_captain_numeracy_populate --output-dir runtime/captain-numeracy-pass1
  python -m tools.nanojev_captain_numeracy_populate --verify-bank runtime/captain-numeracy-pass1

Output directories must not already exist. Rebuilding with the same seed and
counts yields the same canonical problems and presentations. All variants of an
unordered operand pair stay together in one split (even when the operation is
commutative). The original four operation holdouts never enter the train split.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
from typing import Any

# Running as `python -m tools...` or `python tools/...` must both work.
if __package__:
    from . import nanojev_register_stability_v3_generate as registers
    from .nanojev_training_task_store import TrainingTaskStore, SCHEMA as STORE_SCHEMA, canonical, sha
else:
    import nanojev_register_stability_v3_generate as registers
    from nanojev_training_task_store import TrainingTaskStore, SCHEMA as STORE_SCHEMA, canonical, sha

SCHEMA = 'nanojev.captain-numeracy-pass1.generator.v1'
FAMILY = 'captain_numeracy_pass1'
DEFAULT_OUTPUT = Path('runtime/captain-numeracy-pass1')
SPLITS = ('train', 'dev', 'predev', 'test')
FIRST, SECOND = registers.FIRST, registers.SECOND

# Use precisely the operation names and held-out boundary from the v3 numeric
# generator. Different surface operations can have the same numerical truth.
SEMANTIC = {
    'sym_add': 'add', 'written_add': 'add', 'algebra_difference': 'add',
    'sym_sub': 'subtract', 'written_sub': 'subtract', 'algebra_left': 'subtract',
    'algebra_balance': 'subtract',
    'sym_reverse_sub': 'reverse_subtract', 'written_difference': 'reverse_subtract',
    'algebra_right': 'reverse_subtract',
    'sym_multiply': 'multiply', 'written_product': 'multiply',
    'sym_absdiff': 'absolute_difference', 'sym_max': 'maximum',
    'written_greater': 'greater_than', 'conditional_gt': 'greater_than',
    'written_average': 'average', 'conditional_min': 'minimum',
    'conditional_positive': 'positive_select',
    'conditional_abs': 'absolute_greater_than',
    'multistep_double': 'double_subtract',
    'multistep_difference': 'double_subtract',
    'multistep_combine': 'double_sum', 'multistep_square': 'square_sum',
}
OPERATIONS = {name: (name, family, task) for name, family, task in registers.OPERATIONS}
TRAIN_OPERATIONS = tuple(name for name in OPERATIONS if name not in registers.HELD_OUT_OPERATIONS)
HELD_OUT = frozenset(registers.HELD_OUT_OPERATIONS)

# Variants are *not* assumed to be monotonically difficult. The trainer should
# evaluate each and use measured competence to pick a curriculum mixture.
# The suffix _forward/_reordered changes only presentation order, never X/Y.
VARIANTS = (
    'registered_logbar',          # v3 baseline without added English
    'plain_expression',           # old hard-tier baseline without added English
    'story_full_logbar',         # familiar registers + LogBars + full English
    'story_one_logbar_x',        # both values registered; only X LogBar
    'story_one_logbar_y',        # both values registered; only Y LogBar
    'story_numeric_registers',  # both values, no LogBars
    'story_numeric_no_op',       # both values, operation only in English
    'story_numeric_x_semantic_y',# X value, Y role; op explicitly supplied
    'story_semantic_x_numeric_y',# X role, Y value; op explicitly supplied
    'story_semantic_with_op',    # X/Y named by their roles; no numeric aids
    'story_semantic_no_op',      # roles given; operation must be inferred
    'story_one_semantic_x',      # X role only, no explicit op
    'story_one_semantic_y',      # Y role only, no explicit op
    'story_op_only',             # only operation hint, values/roles in story
    'story_raw',                 # story and question, no help
)
ORDERS = ('forward', 'reordered')


@dataclass(frozen=True)
class Frame:
    frame_id: str
    x_role: str
    y_role: str
    forward: str
    reordered: str


def frame(key: str, x: str, y: str, forward: str, reordered: str) -> Frame:
    return Frame(key, x, y, forward, reordered)


# Two independent captain-facing situations for each *semantic* operation.
# Use signed navigation offsets, instrumentation and accounting wherever the
# original ±2,000,000 stream requires negative values or zero. Signed gains
# are dimensionless, so multiplying two arbitrary signed values is coherent.
FRAMES: dict[str, tuple[Frame, ...]] = {
 'add': (
  frame('logistics_ledger','opening fuel allocation balance','fuel allocation adjustment',
        'The shuttle fuel allocation ledger starts at {a} units. Logistics posts an adjustment of {b} units. What balance does the ledger show now?',
        'Logistics posts a fuel allocation adjustment of {b} units. Before that posting, the shuttle ledger showed {a} units. What is its new balance?'),
  frame('navigation_corrections','first signed course correction','second signed course correction',
        'Navigation applies a signed correction of {a} meters and then another of {b} meters. What is the combined correction?',
        'After a second signed correction of {b} meters, navigation combines it with an earlier correction of {a} meters. What is their total?'),
 ),
 'subtract': (
  frame('relative_offset','ship signed position offset','reference signed position offset',
        'Navigation places our ship at signed offset {a} meters and the reference beacon at {b} meters. What is our offset relative to the beacon?',
        'The reference beacon is at signed offset {b} meters. Our ship is at {a} meters on the same axis. What is our relative offset?'),
  frame('reserve_difference','reported reserve balance','required reserve balance',
        'Engineering reports a signed reserve balance of {a} units against a required balance of {b} units. What is the signed reserve margin?',
        'The reserve requirement is {b} units. Engineering reports a signed balance of {a} units. By how much is the reported balance above the requirement?'),
 ),
 'reverse_subtract': (
  frame('navigation_change','initial signed position','final signed position',
        'The shuttle begins at signed position {a} meters and ends at {b} meters. What was the signed change in position?',
        'The shuttle ends at signed position {b} meters, having begun at {a} meters. What was its signed displacement?'),
  frame('signal_change','earlier signed signal reading','later signed signal reading',
        'A long-range scan begins with a signed reading of {a} units and later reads {b} units. How much did the reading change?',
        'The scan now reads {b} units. Earlier it read {a} units. What was the signed change?'),
 ),
 'multiply': (
  frame('signed_guidance_gain','signed correction signal','signed dimensionless gain',
        'The guidance computer has a signed correction signal of {a} units and applies a signed gain of {b}. What is the resulting signal?',
        'A signed gain of {b} is applied to the guidance computer\'s correction signal of {a} units. What signal results?'),
  frame('stabilizer_scale','signed stabilizer offset','signed scale factor',
        'A stabilizer reports a signed offset of {a} units. Control scales that offset by {b}. What is the scaled offset?',
        'Control applies a signed scale factor of {b} to a stabilizer offset of {a} units. What is the resulting offset?'),
 ),
 'absolute_difference': (
  frame('relative_position_sensor_spread','forward relative-position sensor reading','aft relative-position sensor reading',
        'The forward relative-position sensor reports signed offset {a} meters; the aft sensor reports {b} meters on the same axis. What is the absolute difference?',
        'The aft relative-position sensor reports signed offset {b} meters; the forward one reports {a} meters. How far apart are those readings?'),
  frame('telemetry_disagreement','primary telemetry measurement','backup telemetry measurement',
        'The primary telemetry channel shows {a} units and the backup channel shows {b} units. What is their unsigned disagreement?',
        'Backup telemetry shows {b} units; primary telemetry shows {a} units. What is the magnitude of their difference?'),
 ),
 'maximum': (
  frame('greater_contact_signal','port contact signal','starboard contact signal',
        'The port sensor reports a signed contact signal of {a} units; starboard reports {b} units. What is the larger signed reading?',
        'Starboard reports a signed signal of {b} units, and port reports {a} units. Which numerical reading is larger?'),
  frame('largest_bus_value','navigation bus reading','tactical bus reading',
        'The navigation data bus reads {a} and the tactical bus reads {b}. Return the larger numeric value.',
        'The tactical bus has value {b} while navigation has {a}. What is the maximum of those two readings?'),
 ),
 'greater_than': (
  frame('contact_strength_comparison','port contact strength','starboard contact strength',
        'Port contact strength is {a} and starboard contact strength is {b}. Is the port reading strictly greater than starboard? Answer true or false.',
        'Starboard contact strength reads {b}, while port reads {a}. Is port strictly greater? Answer true or false.'),
  frame('sensor_threshold_comparison','measured signed field','comparison signed field',
        'The primary sensor reads {a} units and the comparison sensor reads {b} units. Is the primary reading greater? Answer true or false.',
        'The comparison sensor reads {b} units; the primary sensor reads {a}. Does primary exceed comparison? Answer true or false.'),
 ),
 'average': (
  frame('average_corrections','first signed course adjustment','second signed course adjustment',
        'The ship makes a signed course adjustment of {a} units followed by {b} units. What is their arithmetic mean?',
        'The second signed course adjustment is {b} units and the first is {a} units. What is their average?'),
  frame('average_telemetry','first telemetry sample','second telemetry sample',
        'Tactical records telemetry samples of {a} and {b} units. What is the arithmetic mean of the readings?',
        'The later telemetry sample is {b} units; the earlier is {a}. What is their mean?'),
 ),
 'minimum': (
  frame('lowest_scan_reading','forward sensor value','aft sensor value',
        'The forward sensor reads {a} units and the aft sensor reads {b}. What is the smaller signed reading?',
        'The aft sensor reads {b} units and the forward sensor reads {a}. Return the minimum reading.'),
  frame('minimum_bus_value','navigation bus value','engineering bus value',
        'The navigation bus reports {a} while the engineering bus reports {b}. What is the numerical minimum?',
        'Engineering reports {b}, and navigation reports {a}. Which number is smaller?'),
 ),
 'positive_select': (
  frame('priority_sensor','primary sensor reading','backup sensor reading',
        'The primary sensor reads {a} units and the backup reads {b}. If primary is positive, report backup; otherwise report primary. What is reported?',
        'Backup sensor reads {b} units and primary reads {a}. Report backup only when primary is positive; otherwise report primary. What value is reported?'),
  frame('fallback_telemetry','primary health index','reserve telemetry value',
        'A primary signed health index is {a}; reserve telemetry is {b}. Select reserve telemetry if the index is above zero, or select the index otherwise. What value is selected?',
        'Reserve telemetry has value {b}; the primary signed health index is {a}. Return reserve when that index exceeds zero and primary otherwise. What is returned?'),
 ),
 'absolute_greater_than': (
  frame('compare_absolute_offsets','port signed offset','starboard signed offset',
        'Port reports signed offset {a} and starboard reports {b}. Is the magnitude of port strictly greater than that of starboard? Answer true or false.',
        'Starboard reports signed offset {b} while port reports {a}. Is port\'s absolute magnitude greater? Answer true or false.'),
  frame('compare_absolute_signals','primary signed signal','secondary signed signal',
        'Primary reports signed signal {a} and secondary reports {b}. Does primary have larger absolute magnitude? Answer true or false.',
        'Secondary signed signal is {b}, compared with primary at {a}. Is primary larger in absolute value? Answer true or false.'),
 ),
 'double_subtract': (
  frame('double_correction_less_trim','signed guidance correction','signed trim offset',
        'The guidance correction is {a} units. Control doubles it and then subtracts a signed trim offset of {b} units. What is the final correction?',
        'Control subtracts a signed trim offset of {b} units from twice the guidance correction of {a} units. What is the final correction?'),
  frame('double_balance_less_debit','signed mission credit balance','signed debit adjustment',
        'The mission credit ledger shows {a} units. Double that balance and then subtract an adjustment of {b} units. What remains?',
        'After subtracting an adjustment of {b} units from twice the signed mission credit balance of {a}, what balance remains?'),
 ),
 'double_sum': (
  frame('double_combined_corrections','first signed correction','second signed correction',
        'Navigation combines signed corrections of {a} units and {b} units, then doubles the sum. What is the result?',
        'The second signed correction is {b} units and the first is {a} units. Navigation doubles their combined correction. What results?'),
  frame('amplified_signal_sum','primary signed signal','secondary signed signal',
        'The primary control signal is {a} units and the secondary is {b} units. Double their sum. What value results?',
        'The secondary signal reads {b} units and the primary reads {a} units. Their sum is doubled. What is the amplified signal?'),
 ),
 'square_sum': (
  frame('squared_error_total','first signed error component','second signed error component',
        'The navigation computer measures signed error components of {a} and {b} units on one axis. Add them, then square the total. What is the squared error?',
        'The second error component is {b} units and the first is {a} units. Square their sum. What is the squared error?'),
  frame('squared_signal','primary signed signal','secondary signed signal',
        'Primary contributes signal {a} units and secondary contributes {b} units. What is the square of their combined signal?',
        'Secondary contributes {b} units to primary\'s {a} units. Square the combined value. What results?'),
 ),
}


def truth(a: int, b: int, semantic: str) -> int | float:
    if semantic == 'add': return a + b
    if semantic == 'subtract': return a - b
    if semantic == 'reverse_subtract': return b - a
    if semantic == 'multiply': return a * b
    if semantic == 'absolute_difference': return abs(a - b)
    if semantic == 'maximum': return max(a, b)
    if semantic == 'greater_than': return int(a > b)
    if semantic == 'average': return (a+b)/2
    if semantic == 'minimum': return min(a, b)
    if semantic == 'positive_select': return b if a > 0 else a
    if semantic == 'absolute_greater_than': return int(abs(a) > abs(b))
    if semantic == 'double_subtract': return 2*a-b
    if semantic == 'double_sum': return 2*(a+b)
    if semantic == 'square_sum': return (a+b)**2
    raise ValueError('CAPTAIN_UNKNOWN_SEMANTIC_OPERATION:'+semantic)


def catalogue_check() -> None:
    if set(SEMANTIC) != set(OPERATIONS):
        raise RuntimeError('CAPTAIN_OPERATION_CATALOG_DRIFT')
    if set(SEMANTIC.values()) != set(FRAMES):
        raise RuntimeError('CAPTAIN_FRAME_CATALOG_DRIFT')
    if set(TRAIN_OPERATIONS) & HELD_OUT or len(TRAIN_OPERATIONS) != 20:
        raise RuntimeError('CAPTAIN_HOLDOUT_BOUNDARY_CHANGED')
    for semantic, frames in FRAMES.items():
        if len(frames) < 2:
            raise RuntimeError('CAPTAIN_TOO_FEW_FRAMES:'+semantic)
        for row in frames:
            if not row.x_role or not row.y_role or row.x_role == row.y_role:
                raise RuntimeError('CAPTAIN_AMBIGUOUS_SEMANTIC_ROLES:'+row.frame_id)
            for order, template in (('forward',row.forward),('reordered',row.reordered)):
                if template.count('{a}')!=1 or template.count('{b}')!=1:
                    raise RuntimeError('CAPTAIN_FRAME_OPERAND_COUNT:'+row.frame_id)
                if (template.index('{a}') < template.index('{b}')) != (order=='forward'):
                    raise RuntimeError('CAPTAIN_FRAME_ORDER:'+row.frame_id+':'+order)
            if row.forward==row.reordered:
                raise RuntimeError('CAPTAIN_FRAME_DUPLICATE_PARAPHRASE:'+row.frame_id)


def instruction(a: str, b: str, operation: str) -> str:
    return (OPERATIONS[operation][2]
            .replace(FIRST, a).replace(SECOND, b))


def baseline(a: int, b: int, operation: str, reverse: bool) -> str:
    source = registers.render(a,b,OPERATIONS[operation])
    if not reverse:
        return source
    lines = source.splitlines()
    return '\n'.join((lines[1],lines[0],*lines[2:]))


def scaffold(a: int, b: int, op: str, semantic_frame: Frame,
             variant: str, order: str) -> str:
    if variant not in VARIANTS or order not in ORDERS:
        raise ValueError('CAPTAIN_UNKNOWN_PRESENTATION')
    reverse = (order=='reordered')
    if variant=='registered_logbar':
        return baseline(a,b,op,reverse)
    if variant=='plain_expression':
        return ('Please solve: ' if reverse else 'operation: ')+instruction(str(a),str(b),op)+'\nAnswer:'
    sentence = (semantic_frame.reordered if reverse else semantic_frame.forward).format(a=a,b=b)
    op_line = 'operation: '+instruction(FIRST,SECOND,op)
    num_x = f'{a}, {registers.logbar(a)}'
    num_y = f'{b}, {registers.logbar(b)}'
    rows: dict[str,tuple[str | None,str | None,bool]] = {
        'story_full_logbar': (num_x,num_y,True),
        'story_one_logbar_x': (num_x,str(b),True),
        'story_one_logbar_y': (str(a),num_y,True),
        'story_numeric_registers': (str(a),str(b),True),
        'story_numeric_no_op': (str(a),str(b),False),
        'story_numeric_x_semantic_y': (str(a),semantic_frame.y_role,True),
        'story_semantic_x_numeric_y': (semantic_frame.x_role,str(b),True),
        'story_semantic_with_op': (semantic_frame.x_role,semantic_frame.y_role,True),
        'story_semantic_no_op': (semantic_frame.x_role,semantic_frame.y_role,False),
        'story_one_semantic_x': (semantic_frame.x_role,None,False),
        'story_one_semantic_y': (None,semantic_frame.y_role,False),
        'story_op_only': (None,None,True),
        'story_raw': (None,None,False),
    }
    x,y,visible_op = rows[variant]
    lines = [(FIRST+' = '+x) if x is not None else None,
             (SECOND+' = '+y) if y is not None else None]
    lines = [line for line in (reversed(lines) if reverse else lines) if line is not None]
    if visible_op:
        lines.append(op_line)
    lines.extend((sentence,'Answer:'))
    return '\n'.join(lines)


def validate_presentations(a: int,b: int,operation: str,row: Frame, variants:dict[str,Any]) -> None:
    if len(variants) != len(VARIANTS)*len(ORDERS):
        raise RuntimeError('CAPTAIN_PRESENTATION_COUNT_DRIFT')
    for label, sample in variants.items():
        content = sample['prompt']
        if not content.endswith('Answer:') or not content.strip():
            raise RuntimeError('CAPTAIN_MALFORMED_PROMPT:'+label)
        if 'RESULT =' in content or 'FINAL =' in content:
            raise RuntimeError('CAPTAIN_ANSWER_LEAK:'+label)
        rung,orientation=label.rsplit('__',1)
        if sample['variant']!=rung or sample['order']!=orientation:
            raise RuntimeError('CAPTAIN_PRESENTATION_METADATA_DRIFT')
        if rung=='story_raw':
            for forbidden in (FIRST,SECOND,'LogBar','operation:','|'):
                if forbidden in content:
                    raise RuntimeError('CAPTAIN_RAW_SCAFFOLD_LEAK:'+label)
        if rung.startswith('story_'):
            story=(row.forward if orientation=='forward' else row.reordered).format(a=a,b=b)
            if content.count(story)!=1:
                raise RuntimeError('CAPTAIN_STORY_MISSING:'+label)
            # Ordering is checked from placeholder positions in catalogue_check().
            # Searching rendered numeric substrings is invalid (200 in 2000).
    if variants['registered_logbar__forward']['prompt']!=baseline(a,b,operation,False):
        raise RuntimeError('CAPTAIN_BASELINE_DRIFT')


def presentations(a:int,b:int,operation:str, row:Frame)->dict[str,Any]:
    entries={}
    for v in VARIANTS:
        for orientation in ORDERS:
            entries[f'{v}__{orientation}'] = {
                'prompt':scaffold(a,b,operation,row,v,orientation),
                'variant':v, 'order':orientation,
                'semanticFrameId':row.frame_id,
                # The following describes the representation, NOT a side-channel
                # sent to the decoder (the population script never runs a model).
                'operationSideChannel':'not_applied_by_populator',
            }
    validate_presentations(a,b,operation,row,entries)
    return entries


def pair_key(a:int,b:int)->str:
    return f'{min(a,b)}:{max(a,b)}'


def draw_number(rng: random.Random, grid: list[int]) -> int:
    p=rng.random()
    if p<.60:
        return rng.choice(grid)
    if p<.85:
        return rng.randint(-100,100)
    return rng.randint(-registers.MAX_VALUE,registers.MAX_VALUE)


def generate_pairs(seed:int,n:int) -> list[tuple[int,int]]:
    rng=random.Random(seed)
    grid=registers.numeric_grid()
    seen=set()
    pairs=[]
    # Fixed boundary probes in the source grid, followed by scalable generated
    # pairs; no pair (a,b) can appear in another split in reversed order.
    probes=[(0,0),(1,0),(-1,0),(1,-1),(17,-3),(2000,200),
            (registers.MAX_VALUE,-3),(-registers.MAX_VALUE,1),
            (registers.MAX_VALUE,-registers.MAX_VALUE)]
    for a,b in probes:
        if len(pairs)>=n:break
        k=pair_key(a,b)
        if k not in seen:
            pairs.append((a,b));seen.add(k)
    attempts=0
    while len(pairs)<n:
        attempts+=1
        if attempts>max(n*100,1000):
            raise RuntimeError('CAPTAIN_NUMERIC_PAIR_POOL_EXHAUSTED')
        mode=attempts%20
        if mode in (0,1):
            # Equality, including signed zero and both extreme magnitudes, is
            # essential for strict comparisons, max/min and boolean outputs.
            a=draw_number(rng,grid);b=a
        elif mode in (2,3):
            a=0;b=draw_number(rng,grid)
        elif mode in (4,5):
            a=draw_number(rng,grid);b=-a
        elif mode in (6,7):
            # Near-equal pairs challenge 1% accuracy and directionality.
            a=draw_number(rng,grid)
            b=max(-registers.MAX_VALUE,min(registers.MAX_VALUE,
                a+rng.choice((-5,-2,-1,1,2,5))))
        else:
            a,b=draw_number(rng,grid),draw_number(rng,grid)
        key=pair_key(a,b)
        if key in seen:continue
        if rng.randrange(2):a,b=b,a
        seen.add(key);pairs.append((a,b))
    rng.shuffle(pairs)
    return pairs


def select_operations(seed:int, counts:dict[str,int], include_heldout_eval:bool)->dict[str,list[str]]:
    rng=random.Random(seed+917)
    selections={}
    for split,n in counts.items():
        allowed=list(TRAIN_OPERATIONS)
        if include_heldout_eval and split in ('predev','test'):
            allowed += sorted(HELD_OUT)
        # Deterministic cycling supplies approximately equal representation of
        # the full operation catalog, independent of requested sample volume.
        rng.shuffle(allowed)
        selections[split]=[allowed[i%len(allowed)] for i in range(n)]
    return selections


def hash_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def verify_split_pairs(store:TrainingTaskStore)->dict[str,int]:
    query="SELECT split,provenance_json FROM task_groups WHERE family=?"
    seen={}
    count={key:0 for key in SPLITS}
    for dbrow in store.conn.execute(query,(FAMILY,)):
        provenance=json.loads(dbrow['provenance_json'])
        pk=provenance['unorderedOperandPair']
        split=dbrow['split']
        if pk in seen:
            raise RuntimeError('CAPTAIN_CROSS_GROUP_PAIR_DUPLICATION:'+pk)
        seen[pk]=split
        count[split]+=1
    return count


def populate(output_dir:Path, *,seed:int, counts:dict[str,int],include_heldout_eval:bool=False)->dict:
    catalogue_check()
    if any(counts[s]<1 for s in SPLITS):
        raise ValueError('CAPTAIN_SPLIT_COUNTS_MUST_BE_POSITIVE')
    output_dir=Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError('CAPTAIN_OUTPUT_EXISTS:'+str(output_dir))
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.'+output_dir.name+'-generating-',dir=output_dir.parent))
    try:
        n=sum(counts.values())
        pairs=generate_pairs(seed,n)
        splits=[name for name in SPLITS for _ in range(counts[name])]
        random.Random(seed+31).shuffle(splits)
        ops=select_operations(seed,counts,include_heldout_eval)
        cursors={key:0 for key in SPLITS}
        frame_counts={}
        with TrainingTaskStore(temp/'training-tasks.sqlite') as store:
            with store.conn:
                for split in SPLITS:
                    store.add_cohort(f'captain-numeracy-pass1-{split}-seed-{seed}',
                                     family=FAMILY,split=split,
                                     source={'generator':SCHEMA,'seed':seed,'split':split})
            for index,((a,b),split) in enumerate(zip(pairs,splits)):
                op=ops[split][cursors[split]]
                cursors[split]+=1
                semantic=SEMANTIC[op]
                frames=FRAMES[semantic]
                # Frame rotates independently of operation selection; an alternate
                # seed changes the assignment but remains reproducible.
                frame_row=frames[(index+seed)%len(frames)]
                frame_counts[frame_row.frame_id]=frame_counts.get(frame_row.frame_id,0)+1
                result=truth(a,b,semantic)
                target={'a':a,'b':b,'x':a,'y':b,'operation':op,
                        'semanticOperation':semantic,'result':result}
                provenance={'generator':SCHEMA,'seed':seed,'scenarioFamily':'captain_numeracy',
                            'semanticFrameId':frame_row.frame_id,
                            'xRole':frame_row.x_role,'yRole':frame_row.y_role,
                            'unorderedOperandPair':pair_key(a,b),
                            'source':'synthetic_captain_domain_numeracy',
                            'gameStateVerified':False,
                            'operationReservedFromTraining':op in HELD_OUT}
                items=presentations(a,b,op,frame_row)
                with store.conn:
                    store.put(cohort_id=f'captain-numeracy-pass1-{split}-seed-{seed}',
                              family=FAMILY,task=op,split=split,
                              fingerprint=sha([SCHEMA,'numerical-pair',pair_key(a,b)]),
                              oracle='exact_arithmetic',label_status='verified',
                              target=target,provenance=provenance,variants=items)
            stats=store.stats()
            checks=store.verify()
            pairs_per_split=verify_split_pairs(store)
            if pairs_per_split!=counts or cursors!=counts:
                raise RuntimeError('CAPTAIN_SPLIT_COUNTS_DRIFT')
            if store.conn.execute("SELECT COUNT(*) FROM task_groups WHERE split='train' AND task IN (?,?,?,?)",
                                  tuple(sorted(HELD_OUT))).fetchone()[0]:
                raise RuntimeError('CAPTAIN_TRAIN_OPERATION_HOLDOUT_VIOLATION')
            # Put preview examples in the manifest; don't export hundreds of
            # thousands of redundant JSONL rows by default.
            preview={}
            for v in ('registered_logbar','plain_expression','story_full_logbar',
                      'story_semantic_with_op','story_semantic_no_op','story_raw'):
                row=next(store.iter_examples('train',v+'__forward',family=FAMILY))
                preview[v]={'prompt':row['example']['prompt'],
                            'target':row['target'],
                            'semanticFrameId':row['provenance']['semanticFrameId']}
        manifest={'schema':SCHEMA,'storeSchema':STORE_SCHEMA,'seed':seed,
                  'counts':counts,'stats':stats,'checks':checks,
                  'pairsPerSplit':pairs_per_split,'frameCounts':frame_counts,
                  'heldOutOperations':sorted(HELD_OUT),'heldOutEvaluationEnabled':include_heldout_eval,
                  'trainOperationCatalog':list(TRAIN_OPERATIONS),
                  'variants':list(VARIANTS),'orientations':list(ORDERS),
                  'databaseFile':'training-tasks.sqlite',
                  'trainerIntegrated':False,'noTrainingPerformed':True,
                  'note':'Synthetic captain-domain arithmetic only. No game states, physics, action legality, jackets or preference labels. RESULT/FINAL not trained.',
                  'splitPolicy':'No ordered or reversed numerical pair spans groups or splits; variants share one group.',
                  'preview':preview}
        manifest['databaseSha256']=hash_file(temp/'training-tasks.sqlite')
        (temp/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        os.replace(temp,output_dir)
        return {'ok':True,'outputDir':str(output_dir),'groups':stats['groups'],
                'presentations':stats['presentations'],'databaseSha256':manifest['databaseSha256'],
                'counts':counts,'checks':checks}
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def verify_bank(output_dir:Path)->dict:
    output_dir=Path(output_dir).expanduser().resolve(strict=True)
    manifest=json.loads((output_dir/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=SCHEMA or manifest.get('storeSchema')!=STORE_SCHEMA:
        raise RuntimeError('CAPTAIN_MANIFEST_SCHEMA_MISMATCH')
    path=output_dir/'training-tasks.sqlite'
    if hash_file(path)!=manifest['databaseSha256']:
        raise RuntimeError('CAPTAIN_DATABASE_SHA256_MISMATCH')
    with TrainingTaskStore(path) as store:
        checks=store.verify()
        if checks!=manifest['checks'] or store.stats()!=manifest['stats']:
            raise RuntimeError('CAPTAIN_BANK_COUNTS_OR_CHECKS_DRIFT')
        if verify_split_pairs(store)!=manifest['counts']:
            raise RuntimeError('CAPTAIN_PAIR_SPLIT_MISMATCH')
        expected=len(VARIANTS)*len(ORDERS)
        if store.conn.execute('''SELECT COUNT(*) FROM task_groups g WHERE
            (SELECT COUNT(*) FROM presentations p WHERE p.group_id=g.group_id)<>?''',
            (expected,)).fetchone()[0]:
            raise RuntimeError('CAPTAIN_PRESENTATION_CARDINALITY_DRIFT')
        if store.conn.execute("SELECT COUNT(*) FROM task_groups WHERE split='train' AND task IN (?,?,?,?)",
                              tuple(sorted(HELD_OUT))).fetchone()[0]:
            raise RuntimeError('CAPTAIN_TRAIN_OPERATION_HOLDOUT_VIOLATION')
    return {'ok':True,'database':str(path),'databaseSha256':manifest['databaseSha256'],
            'groups':manifest['stats']['groups'],'presentations':manifest['stats']['presentations'],
            'checks':checks}


def preview(seed:int)->dict:
    catalogue_check()
    examples=[(18,7,'written_add'),(2700,2500,'written_sub'),(17,-3,'written_difference')]
    rows=[]
    for a,b,op in examples:
        frames=FRAMES[SEMANTIC[op]]
        row=frames[seed%len(frames)]
        entries=presentations(a,b,op,row)
        rows.append({'target':{'x':a,'y':b,'operation':op,'result':truth(a,b,SEMANTIC[op])},
                     'frame':row.frame_id,
                     'presentations':{key:entries[key]['prompt'] for key in (
                         'registered_logbar__forward','story_full_logbar__forward',
                         'story_numeric_x_semantic_y__forward','story_semantic_with_op__forward',
                         'story_semantic_no_op__forward','story_one_semantic_x__forward',
                         'story_raw__forward','story_raw__reordered')}})
    return {'schema':SCHEMA,'examples':rows}


def self_test()->dict:
    import tempfile
    catalogue_check()
    for op in SEMANTIC:
        for a,b in ((17,-3),(0,0),(-registers.MAX_VALUE,registers.MAX_VALUE)):
            for row in FRAMES[SEMANTIC[op]]:
                entries=presentations(a,b,op,row)
                assert all(p['prompt'].endswith('Answer:') for p in entries.values())
                assert truth(a,b,SEMANTIC[op]) is not None
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'bank'
        populate(path,seed=23,counts={'train':60,'dev':12,'predev':12,'test':12},
                 include_heldout_eval=True)
        result=verify_bank(path)
        if result['groups']!=96 or result['presentations']!=96*len(VARIANTS)*2:
            raise RuntimeError('CAPTAIN_SELF_TEST_COUNTS')
        with TrainingTaskStore(path/'training-tasks.sqlite') as store:
            for row in store.iter_examples('train','story_raw__forward',family=FAMILY):
                if row['task'] in HELD_OUT or row['target']['result']!=truth(
                    row['target']['a'],row['target']['b'],SEMANTIC[row['task']]):
                    raise RuntimeError('CAPTAIN_SELF_TEST_LABEL_INVALID')
    return {'ok':True,'schema':SCHEMA,'operations':len(SEMANTIC),
            'trainOperations':len(TRAIN_OPERATIONS),'heldOutOperations':len(HELD_OUT),
            'frames':sum(map(len,FRAMES.values())),'variants':len(VARIANTS)*len(ORDERS),
            'databaseRoundTrip':'PASS'}


def main(argv:list[str] | None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--train-groups',type=int,default=4096)
    p.add_argument('--dev-groups',type=int,default=512)
    p.add_argument('--predev-groups',type=int,default=512)
    p.add_argument('--test-groups',type=int,default=512)
    p.add_argument('--include-heldout-eval',action='store_true',
                   help='Put reserved operations in predev/test, never in train')
    p.add_argument('--preview',action='store_true',help='Show graded example prompts without writing a database')
    p.add_argument('--self-test',action='store_true',help='Quick deterministic generation, validation, and round-trip test')
    p.add_argument('--verify-bank',type=Path,help='Verify a previously generated sealed database')
    args=p.parse_args(argv)
    try:
        if args.self_test:
            data=self_test()
        elif args.preview:
            data=preview(args.seed)
        elif args.verify_bank is not None:
            data=verify_bank(args.verify_bank)
        else:
            data=populate(args.output_dir,seed=args.seed,
                          counts={'train':args.train_groups,'dev':args.dev_groups,
                                  'predev':args.predev_groups,'test':args.test_groups},
                          include_heldout_eval=args.include_heldout_eval)
        print(json.dumps(data,sort_keys=True,indent=2,allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__+': '+str(exc)},sort_keys=True))
        return 1


if __name__=='__main__':
    sys.exit(main())
