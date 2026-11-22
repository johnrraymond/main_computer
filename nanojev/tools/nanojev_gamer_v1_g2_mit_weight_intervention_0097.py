"""G2-0097: frozen 0096 head, direct MIT-style weight-space intervention.

From main_computer/nanojev:
    python -m tools.nanojev_gamer_v1_g2_mit_weight_intervention_0097 run --device cuda

A priori edit: raise the NONE (no relation) output bias by +0.50. The 0096
warm-supervised confirmation had FP 16849 vs FN 4607, suggesting excess
positive admission. This is one explicit coefficient edit, not gradient descent,
not full circuit-aware MIT weight-space programming, and not an accuracy claim.

All checks and scorecards are emitted under runtime/reports. Parent weights
remain byte-for-byte unchanged. Fresh sealed challenge data are never opened
by the edit or inference stages. No automatic adoption or G3 unlock.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import torch
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_frozen_generalization_0091 as frozen
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools import nanojev_gamer_v1_graph_contract as contract
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.mit-weight-intervention-0097.v1'
MODULE = 'tools.nanojev_gamer_v1_g2_mit_weight_intervention_0097'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
OUTPUT_NAME = 'oracle-fabric-g2-mit-weight-intervention-0097'
PREFERRED_ARM = 'warm_supervised'
SEED = 970097
EDIT_DELTA = 0.5
EDIT_TENSOR = 'binding.bias'
EDIT_LABEL = 'none'


def require(ok, code):
    if not ok:
        raise ValueError('G2_0097_' + code)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(prompt):
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2,
                                    ensure_ascii=False) + '\n', encoding='utf-8')


def write_rows(path, records):
    with Path(path).open('x', encoding='utf-8') as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n')


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def location(root, output):
    return (Path(output) if output else Path(root) / OUTPUT_NAME).resolve()


def _candidate_info(path):
    """Check a 0096 weight file with its own source manifest.

    Extended Learning 0096 writes its own manifest format and milestone
    directories. It is NOT necessarily a curriculum-v2 checkpoint. All
    accepted inputs must be under the named 0096 campaign and have a
    manifest-bound byte-for-byte head hash. Never load an unverified file.
    """
    path = Path(path).resolve()
    file = path / cur.HEAD_FILE
    manifest_file = path / cur.MANIFEST
    require(file.is_file() and manifest_file.is_file(), 'SOURCE_CHECKPOINT_INCOMPLETE')
    require('oracle-fabric-g2-extended-learning-0096' in path.parts,
            'SOURCE_NOT_0096_CAMPAIGN')
    manifest = load(manifest_file)
    require(manifest.get('head_sha256') == sha(file), 'SOURCE_CHECKPOINT_HASH_DRIFT')
    # 0096 _save_head emits a checkpoint manifest, not an experiment-status
    # manifest: some selected milestones contain no `status` field. Do not
    # require optional run-level metadata on a checkpoint. Its integrity is
    # established instead by (a) the exact campaign path, (b) the manifest's
    # mandatory head hash above, and (c) the manifest's own sealed byte hash.
    # A present unlock/promotion marker must never be true.
    require(isinstance(manifest, dict) and
            manifest.get('production_promotion') is not True and
            manifest.get('g3_unlocked') is not True and
            manifest.get('g3_training_unlocked') is not True,
            'SOURCE_MANIFEST_INVALID')
    source_schema = manifest.get('schema') or '0096-checkpoint-manifest-unversioned'
    source_status = manifest.get('status') or '0096-SELECTED_CHECKPOINT'
    require(isinstance(source_schema, str) and
            isinstance(source_status, str), 'SOURCE_MANIFEST_INVALID')
    return {'checkpoint': str(path), 'head_sha256': sha(file),
            'manifest_sha256': sha(manifest_file),
            'source_schema': source_schema, 'status': source_status,
            'g3_unlocked': False}


def _campaign(root):
    return Path(root).resolve() / 'oracle-fabric-g2-extended-learning-0096'


def _selected_directory(campaign, arm):
    """Select the arm's final *best development milestone*, not final weights.

    0096 saves a milestone whenever the dev winner changes; 0096's printed
    best_step progressed through 1600 for warm_supervised. For editor arms,
    the selected output is `checkpoint` rather than `milestone-*`.
    """
    home = campaign / arm
    if not home.is_dir():
        return None
    if (home / 'checkpoint' / cur.HEAD_FILE).is_file():
        return home / 'checkpoint'
    milestones = sorted((p for p in home.glob('milestone-[0-9][0-9][0-9][0-9][0-9]')
                         if (p / cur.HEAD_FILE).is_file() and
                         (p / cur.MANIFEST).is_file()),
                        key=lambda p: int(p.name.removeprefix('milestone-')))
    if milestones:
        return milestones[-1]
    return None


def discover(root, arm=PREFERRED_ARM):
    """Only examine the 0096 selected arm, not the entire reports tree."""
    root = Path(root).resolve()
    require(root.is_dir(), 'REPORT_ROOT_MISSING')
    campaign = _campaign(root)
    chosen = _selected_directory(campaign, arm)
    if chosen is None:
        return []
    return [_candidate_info(chosen)]


def select_checkpoint(root, checkpoint, arm):
    if checkpoint:
        candidate = _candidate_info(Path(checkpoint).resolve())
        require(arm in Path(candidate['checkpoint']).parts, 'SOURCE_ARM_MISMATCH')
        return candidate
    candidates = discover(root, arm)
    if len(candidates) != 1:
        print('G2_0097_SOURCE_CANDIDATES ' + json.dumps(candidates, sort_keys=True), flush=True)
        raise ValueError('G2_0097_EXPECT_ONE_0096_CHECKPOINT__USE_--checkpoint')
    print('G2_0097_SOURCE_SELECTED ' + json.dumps(candidates[0], sort_keys=True), flush=True)
    return candidates[0]


def _load_scientific_head(checkpoint, reference, *, device):
    """Use the verified architecture from G2-0087 and raw 0096 state.

    G2-0096 uses an independent checkpoint manifest, so cur._load_checkpoint
    cannot be assumed to accept the 0096 milestone. The tensor state is a
    full, strictly shape-compatible head (verified with load_state_dict).
    """
    import copy
    head = copy.deepcopy(reference).to('cpu')
    state = load_file(str(Path(checkpoint) / cur.HEAD_FILE), device='cpu')
    head.load_state_dict(state, strict=True)
    head.to(device).eval().requires_grad_(False)
    return head


def edit_state(state, delta=EDIT_DELTA):
    """One direct coefficient intervention; return copied state and receipt."""
    require(EDIT_TENSOR in state and isinstance(state[EDIT_TENSOR], torch.Tensor),
            'BINDING_BIAS_MISSING')
    bias = state[EDIT_TENSOR]
    label_idx = BIND_CLASSES.index(EDIT_LABEL)
    require(bias.ndim == 1 and bias.numel() == len(BIND_CLASSES), 'BIND_CLASS_SHAPE_DRIFT')
    require(float(delta) == EDIT_DELTA, 'PRECOMMITTED_EDIT_DELTA_DRIFT')
    updated = {name: tensor.detach().cpu().clone().contiguous()
               for name, tensor in state.items()}
    before = updated[EDIT_TENSOR][label_idx].item()
    updated[EDIT_TENSOR][label_idx] += delta
    after = updated[EDIT_TENSOR][label_idx].item()
    for key in state:
        source = state[key].detach().cpu()
        if key != EDIT_TENSOR:
            require(torch.equal(source, updated[key]), 'NON_TARGET_MUTATION')
        else:
            before_copy = updated[key].clone()
            before_copy[label_idx] = source[label_idx]
            require(torch.equal(source, before_copy), 'OTHER_BIAS_MUTATION')
    require(abs((after - before) - EDIT_DELTA) < 1e-5, 'EDIT_MAGNITUDE_DRIFT')
    return updated, {'tensor': EDIT_TENSOR, 'label': EDIT_LABEL,
                     'index': label_idx, 'before': before, 'after': after,
                     'requested_delta': EDIT_DELTA, 'changed_coefficients': 1,
                     'gradient_steps': 0, 'training_examples_used_for_edit': 0}


def known_prompts(root, source_dataset):
    # Only prepare scans known prior prompts; no training data is used to
    # choose the edit, and this function cannot be called in predict/score.
    known = set()
    # Walk only metadata/seal branches; 0093's blind/shards subtree can
    # contain thousands of case files and must not be traversed.
    prompt_files = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('blind','shards','scored','models',
                                                 '__pycache__','training')]
        if 'prompts.jsonl' in files:
            prompt_files.append(Path(directory) / 'prompts.jsonl')
    for f in prompt_files:
        try:
            for item in rows(f):
                if isinstance(item, dict):
                    key = item.get('prompt_sha256') or (digest(item['prompt'])
                             if isinstance(item.get('prompt'), str) else None)
                    if key: known.add(key)
        except (OSError, json.JSONDecodeError):
            raise ValueError('G2_0097_PROMPT_INDEX_READ_FAILED_' + str(f))
    if source_dataset.is_file():
        for item in cur.load_rows(source_dataset):
            known.add(digest(item['prompt']))
    return known


def new_cohort(root, dataset, size):
    require(size >= 40 and size <= 2000 and size % 2 == 0, 'CASE_COUNT_INVALID')
    used = known_prompts(root, dataset)
    rng = random.Random(SEED)
    public, secret = [], []
    serial = 97_000_000
    for k in range(size):
        family = 'allocation' if k % 2 == 0 else 'encounter'
        generator = corpus.allocation if family == 'allocation' else corpus.encounter
        for attempt in range(4096):
            record = generator(rng, 'test', serial)
            serial += 1
            prompt_hash = digest(record['prompt'])
            if prompt_hash not in used: break
        else:
            raise ValueError('G2_0097_FRESH_PROMPT_EXHAUSTION')
        used.add(prompt_hash)
        require(record.get('annotation_provenance') ==
                'AUTHOR_MARKED_IN_SOURCE_GENERATOR', 'SOURCE_GOLD_MISSING')
        contract.validate_graph(record['graph'], complete=True)
        key = f'mit0097-{k:04d}'
        public.append({'case_id': key, 'prompt': record['prompt'],
                       'prompt_sha256': prompt_hash, 'family': family})
        secret.append({'case_id': key, 'prompt_sha256': prompt_hash,
                       'family': family, 'graph': record['graph']})
    return public, secret


def prepare(*, root=ROOT_DEFAULT, output=None, checkpoint=None,
            arm=PREFERRED_ARM, cases=240):
    dest = location(root, output)
    require(not dest.exists() and dest.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    source = select_checkpoint(root, checkpoint, arm)
    paths = structural.paths_for(root)
    # Verify that source is the same frozen G1 and trained head architecture.
    _manifest, spec = structural.preflight(paths)
    require(spec.get('method') in ('joint', 'native', 'threshold', 'source_topk',
                                   'type_aware'), 'DECODER_SPEC_UNSUPPORTED')
    prompts, answers = new_cohort(root, Path(paths['dataset']), cases)
    dest.mkdir()
    (dest / 'seal').mkdir()
    write_rows(dest / 'seal' / 'prompts.jsonl', prompts)
    write_rows(dest / 'seal' / 'answers.SECRET.jsonl', answers)
    commitment = {'schema': SCHEMA, 'state': 'SEALED_BEFORE_EDIT_OR_INFERENCE',
                  'runner_sha256': sha(__file__), 'source': source,
                  'decoder_spec': spec, 'cases': cases,
                  'prompts_sha256': sha(dest/'seal'/'prompts.jsonl'),
                  'answers_sha256': sha(dest/'seal'/'answers.SECRET.jsonl'),
                  'delta': EDIT_DELTA, 'tensor': EDIT_TENSOR, 'label': EDIT_LABEL,
                  'model_training': False, 'g3_unlocked': False,
                  'production_promotion': False}
    write(dest / 'seal' / 'commit.json', commitment)
    print('G2_0097_PREPARED ' + json.dumps({'cases': cases, 'checkpoint':
          source['checkpoint'], 'parent_sha256': source['head_sha256'],
          'edit': f'{EDIT_TENSOR}[{EDIT_LABEL}]+={EDIT_DELTA}',
          'status': commitment['state']}, sort_keys=True), flush=True)
    return commitment


def check(dest, *, with_gold=False):
    commit = load(dest/'seal'/'commit.json')
    require(commit.get('schema') == SCHEMA and
            commit.get('state') == 'SEALED_BEFORE_EDIT_OR_INFERENCE' and
            commit.get('runner_sha256') == sha(__file__) and
            commit.get('delta') == EDIT_DELTA and
            commit.get('tensor') == EDIT_TENSOR and
            commit.get('label') == EDIT_LABEL and
            commit.get('g3_unlocked') is False and
            commit.get('production_promotion') is False and
            commit['prompts_sha256'] == sha(dest/'seal'/'prompts.jsonl'),
            'SEAL_MISMATCH')
    source = _candidate_info(commit['source']['checkpoint'])
    require(source == commit['source'], 'SOURCE_HEAD_CHANGED')
    if with_gold:
        require(commit['answers_sha256'] == sha(dest/'seal'/'answers.SECRET.jsonl'),
                'GOLD_CHANGED')
    return commit


def edit(*, root=ROOT_DEFAULT, output=None):
    dest = location(root, output)
    commit = check(dest)
    target = dest / 'edit'
    require(not target.exists(), 'EDIT_ALREADY_EXISTS')
    source = Path(commit['source']['checkpoint'])
    raw = load_file(str(source / cur.HEAD_FILE), device='cpu')
    edited, receipt = edit_state(raw)
    target.mkdir()
    checkpoint_dir = target / 'checkpoint'
    checkpoint_dir.mkdir()
    output_head = checkpoint_dir / cur.HEAD_FILE
    save_file(edited, str(output_head))
    manifest = load(source / cur.MANIFEST)
    manifest.update({'head_sha256': sha(output_head), 'status': 'G2_0097_DIRECT_EDIT_NOT_CERTIFIED',
                     'g3_training_unlocked': False, 'production_promotion': False,
                     'g2_0097_mit': {'schema': SCHEMA, 'parent_head_sha256':
                                    commit['source']['head_sha256'], 'receipt': receipt,
                                    'frozen_g1': True, 'experimental': True}})
    write(checkpoint_dir / cur.MANIFEST, manifest)
    # Verify saved tensors again, not just the in-memory copies.
    check_state = load_file(str(output_head), device='cpu')
    require(set(check_state) == set(edited) and all(torch.equal(check_state[k], edited[k])
                                                   for k in edited), 'SAVED_TENSOR_DRIFT')
    write(target / 'receipt.json', {**receipt,
           'parent_head_sha256': commit['source']['head_sha256'],
           'edited_head_sha256': sha(output_head),
           'manifest_sha256': sha(checkpoint_dir / cur.MANIFEST),
           'source_unchanged': _candidate_info(source) == commit['source'],
           'g3_unlocked': False, 'production_promotion': False})
    print('G2_0097_EDITED ' + json.dumps(load(target / 'receipt.json'),sort_keys=True),
          flush=True)


def verify_edit(dest):
    receipt = load(dest/'edit'/'receipt.json')
    d = dest/'edit'/'checkpoint'
    require(receipt['edited_head_sha256'] == sha(d/cur.HEAD_FILE) and
            receipt['manifest_sha256'] == sha(d/cur.MANIFEST) and
            receipt['source_unchanged'] is True and
            receipt['changed_coefficients'] == 1 and
            receipt['gradient_steps'] == 0, 'EDIT_DRIFT')
    return receipt


def json_edge(edge):
    return [edge[0], list(edge[1]), edge[2], list(edge[3]), edge[4]]


def _validated_shard(file, row):
    record = load(file)
    require(record.get('case_id') == row['case_id'] and
            record.get('prompt_sha256') == row['prompt_sha256'] and
            record.get('family') == row['family'] and
            set(('baseline','edited','g1_objects')) <= set(record),
            'BLIND_SHARD_IDENTITY_DRIFT')
    return record


def predict(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = location(root, output)
    commit = check(dest, with_gold=False)
    receipt = verify_edit(dest)
    blind = dest / 'blind'
    require(not (blind/'lock.json').exists(), 'BLIND_ALREADY_LOCKED')
    blind.mkdir(exist_ok=True)
    shards = blind / 'shards'
    shards.mkdir(exist_ok=True)
    prompts = list(rows(dest / 'seal' / 'prompts.jsonl'))
    require(len(prompts) == commit['cases'] and
            len({r['case_id'] for r in prompts}) == commit['cases'], 'CASES_DRIFT')
    pending = [r for r in prompts if not (shards/(r['case_id']+'.json')).is_file()]
    if pending:
        paths = structural.paths_for(root)
        network, tok, composite, reference, _manifest = structural._load_runtime(paths, device)
        head0 = _load_scientific_head(commit['source']['checkpoint'], reference, device=device)
        head1 = _load_scientific_head(dest/'edit'/'checkpoint', reference, device=device)
        require(receipt['edited_head_sha256'] == sha(dest/'edit'/'checkpoint'/cur.HEAD_FILE),
                'EDIT_SHA_DRIFT')
        spec = commit['decoder_spec']
        with torch.inference_mode():
            for n, row in enumerate(pending, 1):
                require(row['prompt_sha256'] == digest(row['prompt']), 'PROMPT_CHANGED')
                prepared, hidden = bakeoff.previous._capture_inference(
                    network, tok, row['prompt'], device, 2048)
                g1 = composite.predict(hidden, prepared,
                                       family=row['family'], native_stage='G1')
                spans = g1['token_spans']
                eligible = prepared['eligible'].to(device)
                surf0 = frozen._infer(head0, hidden, eligible, spans, prepared)
                surf1 = frozen._infer(head1, hidden, eligible, spans, prepared)
                require(surf0['g1_objects'] == surf1['g1_objects'], 'G1_CHANGED')
                base = bakeoff.decode(surf0, spec)
                altered = bakeoff.decode(surf1, spec)
                record = {'case_id': row['case_id'], 'family': row['family'],
                          'prompt_sha256': row['prompt_sha256'],
                          'g1_objects': surf0['g1_objects'],
                          'baseline': [json_edge(e) for e in sorted(base)],
                          'edited': [json_edge(e) for e in sorted(altered)]}
                target = shards / (row['case_id'] + '.json')
                tmp = target.with_suffix('.tmp')
                write(tmp, record)
                os.replace(tmp, target)
                if n == 1 or n % 25 == 0 or n == len(pending):
                    print(f'G2_0097_BLIND {len(prompts)-len(pending)+n}/{len(prompts)}',
                          flush=True)
    # Re-read every shard and reconstruct the aggregate deterministically.
    # No gold material is opened or used in this phase.
    output = blind / 'predictions.jsonl'
    tmp = blind / 'predictions.jsonl.tmp'
    with tmp.open('w', encoding='utf-8') as fp:
        for row in prompts:
            item = _validated_shard(shards / (row['case_id']+'.json'), row)
            fp.write(json.dumps(item, sort_keys=True) + '\n')
    os.replace(tmp, output)
    lock = {'schema': SCHEMA, 'status': 'BLIND_PREDICTIONS_LOCKED',
            'predictions_sha256': sha(output),
            'prompts_sha256': commit['prompts_sha256'],
            'parent_head_sha256': commit['source']['head_sha256'],
            'edited_head_sha256': receipt['edited_head_sha256'],
            'gold_accessed': False, 'g3_unlocked': False,
            'production_promotion': False}
    write(blind/'lock.json', lock)
    print('G2_0097_BLIND_LOCKED '+json.dumps({'cases': len(prompts),
          'predictions_sha256': lock['predictions_sha256']}, sort_keys=True), flush=True)


def as_set(edges):
    return {(a,tuple(sa),b,tuple(sb),label) for a,sa,b,sb,label in edges}


def summarize(counter):
    p=counter['tp'] / max(1,counter['tp']+counter['fp'])
    r=counter['tp'] / max(1,counter['tp']+counter['fn'])
    f=2*counter['tp'] / max(1,2*counter['tp']+counter['fp']+counter['fn'])
    return {**dict(counter),'precision':p,'recall':r,'edge_f1':f,
            'exact_rate':counter['exact']/max(1,counter['cases'])}


def score(*, root=ROOT_DEFAULT, output=None):
    dest = location(root,output)
    commit = check(dest,with_gold=True)
    receipt = verify_edit(dest)
    lock = load(dest/'blind'/'lock.json')
    require(lock['schema']==SCHEMA and
            lock['status']=='BLIND_PREDICTIONS_LOCKED' and
            lock['prompts_sha256']==commit['prompts_sha256'] and
            lock['predictions_sha256']==sha(dest/'blind'/'predictions.jsonl') and
            lock['parent_head_sha256']==commit['source']['head_sha256'] and
            lock['edited_head_sha256']==receipt['edited_head_sha256'] and
            lock['gold_accessed'] is False,'BLIND_LOCK_DRIFT')
    require(not (dest/'scored').exists(), 'ALREADY_SCORED')
    answers = {r['case_id']:r for r in rows(dest/'seal'/'answers.SECRET.jsonl')}
    public = {r['case_id']:r for r in rows(dest/'seal'/'prompts.jsonl')}
    predicted = list(rows(dest/'blind'/'predictions.jsonl'))
    require(len(predicted)==len(answers)==len(public)==commit['cases'] and
            len({r['case_id'] for r in predicted})==len(predicted),'SCORE_COUNT_DRIFT')
    tally = {family:{arm:Counter() for arm in ('baseline','edited')}
             for family in ('allocation','encounter','overall')}
    pairwise = Counter()
    for row in predicted:
        key = row['case_id']
        require(key in answers and key in public and
                row['prompt_sha256']==answers[key]['prompt_sha256']==public[key]['prompt_sha256']
                and row['family']==answers[key]['family']==public[key]['family'],
                'CASE_IDENTITY_DRIFT')
        gold = bakeoff.truth(answers[key]['graph'])
        before, after = as_set(row['baseline']),as_set(row['edited'])
        for arm, values in (('baseline',before),('edited',after)):
            totals={'cases':1,'exact':int(values==gold),
                    'tp':len(values&gold),'fp':len(values-gold),'fn':len(gold-values)}
            tally[row['family']][arm].update(totals)
            tally['overall'][arm].update(totals)
        pairwise.update({'rescued_tp':len((after&gold)-(before&gold)),
                         'lost_tp':len((before&gold)-(after&gold)),
                         'removed_fp':len((before-gold)-(after-gold)),
                         'introduced_fp':len((after-gold)-(before-gold)),
                         'exact_gained':int(after==gold and before!=gold),
                         'exact_lost':int(before==gold and after!=gold)})
    metrics = {f:{a:summarize(c) for a,c in arms.items()} for f,arms in tally.items()}
    deltas = {f:{'f1':v['edited']['edge_f1']-v['baseline']['edge_f1'],
                 'exact':v['edited']['exact']-v['baseline']['exact'],
                 'fp':v['edited']['fp']-v['baseline']['fp'],
                 'fn':v['edited']['fn']-v['baseline']['fn']}
              for f,v in metrics.items()}
    passed = (deltas['overall']['f1']>0.005 and deltas['overall']['exact']>=0
              and all(deltas[f]['f1']>=-0.01 for f in ('allocation','encounter')))
    report = {'schema':SCHEMA,'status':('MIT_EDIT_FRESH_CHALLENGE_IMPROVED_NOT_CERTIFIED'
                 if passed else 'MIT_EDIT_NO_SAFE_GAIN_ON_FRESH_CHALLENGE'),
              'edit':receipt,'metrics':metrics,'deltas':deltas,
              'edge_impact':dict(pairwise),'precommitted_gate_passed':passed,
              'training':False,'parent_unchanged':_candidate_info(
                  commit['source']['checkpoint'])==commit['source'],
              'g3_unlocked':False,'production_promotion':False,
              'limitations':('Single scalar logit-bias intervention, not MIT '
                  'circuit-level reasoning; synthetic balanced test-set cohort. '
                  'No model-selection use of prior 0096 confirmation gold.')}
    (dest/'scored').mkdir()
    write(dest/'scored'/'report.json',report)
    print('G2_0097_SCORED '+json.dumps({'status':report['status'],
          'deltas':deltas,'paired':dict(pairwise),
          'parent_unchanged':report['parent_unchanged']},sort_keys=True),flush=True)
    return report


def run(*,root=ROOT_DEFAULT,output=None,device='cuda',checkpoint=None,
        arm=PREFERRED_ARM,cases=240):
    dest=location(root,output)
    phases=[('prepare', ['--cases',str(cases),'--arm',arm] +
             (['--checkpoint',checkpoint] if checkpoint else [])),
            ('edit',[]),('predict',['--device',device]),('score',[])]
    for phase,extra in phases:
        target = {'prepare':dest/'seal'/'commit.json',
                  'edit':dest/'edit'/'receipt.json',
                  'predict':dest/'blind'/'lock.json',
                  'score':dest/'scored'/'report.json'}[phase]
        if target.is_file():
            if phase=='prepare':check(dest)
            if phase in ('edit','predict','score'):verify_edit(dest)
            continue
        argv=[sys.executable,'-u','-m',MODULE,phase,'--root',str(root)]
        if output:argv.extend(['--output',str(output)])
        argv.extend(extra)
        subprocess.run(argv,check=True)
    report=load(dest/'scored'/'report.json')
    print('G2_0097_RESULT '+json.dumps({'status':report['status'],
          'gate':report['precommitted_gate_passed'],
          'deltas':report['deltas']},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    for command in ('run','find','prepare','edit','predict','score'):
        p=subs.add_parser(command)
        p.add_argument('--root',default=ROOT_DEFAULT)
        if command not in ('find',):p.add_argument('--output')
        if command in ('run','prepare','find'):p.add_argument('--arm',default=PREFERRED_ARM)
        if command in ('run','prepare'):p.add_argument('--checkpoint')
        if command in ('run','prepare'):p.add_argument('--cases',type=int,default=240)
        if command in ('run','predict'):p.add_argument('--device',default='cuda')
    args=parser.parse_args(argv)
    if args.command=='find':
        print('G2_0097_SOURCE_CANDIDATES '+json.dumps(discover(args.root,args.arm),
                                                      sort_keys=True),flush=True)
        return
    params={'root':args.root,'output':args.output}
    if args.command in ('run','prepare'):
        params.update(checkpoint=args.checkpoint,arm=args.arm,cases=args.cases)
    if args.command in ('run','predict'):params.update(device=args.device)
    globals()[args.command](**params)


if __name__=='__main__':
    main()
