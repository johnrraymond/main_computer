"""G2-0060: controlled, frozen-G1 actor-candidate policy bake-off.

Development: train pair indices [16,144), already-examined failed canary.
Holdout:     train pair indices [144,272), unseen by actor-policy selection.

No gold enters the policy builder; gold is used only for post-policy scoring.
All four policies receive identical frozen G1 outputs in one capture pass.
This is candidate *coverage* research, not model training or promotion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA = 'nanojev.gamer.graph.g2.actor-candidate-bakeoff-0060.v1'
ARMS = ('evidence', 'actor_quota', 'actor_cues', 'hybrid')
# Fixed before measuring the independent holdout.
DEVELOPMENT = (16, 144)
HOLDOUT = (144, 272)
QUOTA = 20
# These cue phrases describe observable English participant constructions. They
# do not use authored graph labels, object_spans, gold endpoints, or IDs.
_CUES = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in (
    r'\AA\s+(.{1,90}?)\s+chooses\s+from\b',
    r'\AConsider\s+the\s+choices\s+available\s+to\s+the\s+(.{1,90}?):',
    r'\AFor\s+the\s+(.{1,90}?),\s+available\s+moves\s+are\b',
    r'\AAvailable\s+choices\s+for\s+the\s+(.{1,90}?)\s+are\b',
    r'\AThe\s+(.{1,90}?)\s+compares\s+plans:',
    r'\AFor\s+a\s+(.{1,90}?),\s+the\s+available\s+projects\s+are\b',
    r'\AA\s+(.{1,90}?)\s+has\s+alternatives\b',
    r'\AProject\s+selection\s+for\s+the\s+(.{1,90}?):',
))


def _fail(why):
    raise ValueError('GAMER_G2_0060_' + str(why))


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def _json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _source_snapshot(g):
    # A change to ANY selection or frozen-interface source forces an explicitly
    # new bake-off instead of silently resuming a different experimental arm.
    paths = (Path(__file__), Path(g.__file__), Path(g.p52.__file__),
             Path(g.p40.__file__), Path(g.p50.__file__), Path(g.corpus.__file__))
    return {p.name: _sha(p) for p in paths}


def _valid_overlap(prepared, a, b):
    # Avoid creating an actor occurrence with no frozen contextual token.
    start = prepared['prompt_start']
    eligible = prepared.get('eligible')
    for i, (x, y) in enumerate(prepared['offsets']):
        if eligible is not None and not bool(eligible[i]):
            continue
        if y > x and x >= start and y <= start + len(prepared['prompt']):
            if x < start + b and y > start + a:
                return True
    return False


def cue_actors(prompt, prepared):
    """Extract participants from prompt text alone; no author annotations."""
    candidates = []
    for pattern in _CUES:
        match = pattern.search(prompt)
        if match is None:
            continue
        a, b = match.span(1)
        name = prompt[a:b]
        if (not name.strip() or not any(c.isalpha() for c in name)
                or any(c in name for c in ';:,.!?\n')
                or not _valid_overlap(prepared, a, b)):
            continue
        candidates.append((a, b, 'actor'))
    return list(dict.fromkeys(candidates))


def raw_actor_candidates(raw, parent, g):
    """Only the frozen neural actor lattice, with no gold or lexicon lookup."""
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

    logits, valid, prepared = g.p52._validate(raw, parent)
    prompt = prepared['prompt']
    actor_idx = SPAN_KINDS.index('actor') + 1
    scores = logits[:, :, actor_idx] - logits[:, :, 0]
    ranked = {}
    for t, w in valid.nonzero(as_tuple=False).tolist():
        bounds = token_span_char_bounds({'token_first': t, 'token_last': t+w}, prepared)
        if bounds is None:
            continue
        untrimmed = (bounds[0], bounds[1], 'actor')
        anchor = g.p52._punct_trim(untrimmed, prompt)
        if anchor is None:
            continue
        a, b, _ = anchor
        surface = prompt[a:b]
        if (b-a > 90 or b-a < 2 or not any(c.isalpha() for c in surface)
                or any(c in surface for c in ';:,.!?\n')):
            continue
        margin = float(scores[t, w])
        existing = ranked.get(anchor)
        if existing is None or margin > existing:
            ranked[anchor] = margin
    # Highest G1 margins, with span-length tie-breaking; anchor identity keeps
    # the test independent of dictionary ordering and GPU tie behavior.
    return sorted(ranked.items(), key=lambda kv: (-kv[1], -(kv[0][1]-kv[0][0]), kv[0]))


def _actor_reservation(raw, parent, g, *, arm, evidence=None, raw_ranked=None, cue_candidates=None):
    """Reallocate only supplemental 0052 anchors, never the 0051 parents."""
    if arm not in ARMS:
        _fail('UNKNOWN_ARM')
    if evidence is None:
        evidence = g._construct_evidence(raw, parent)
    if arm == 'evidence':
        return evidence
    if not any((rel == 'owns' and src == 'actor') for rel, src, dst in raw['allowed']):
        return evidence
    prepared = evidence.get('prepared')
    if prepared is None:
        _, _, prepared = g.p52._validate(raw, parent)
    fixed = list(parent['expanded_anchors'])
    if len(fixed) != len(set(fixed)):
        _fail('DUPLICATE_PARENT_ANCHORS')
    capacity = g.p52.MAX_ANCHORS - len(fixed)
    if capacity < 0:
        _fail('PARENT_OVER_CAPACITY')
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
    index = SPAN_KINDS.index('actor') + 1
    cues = (cue_candidates if cue_candidates is not None else cue_actors(prepared['prompt'], prepared))
    if arm not in ('actor_cues','hybrid'):
        cues = []
    if arm in ('actor_quota','hybrid') and raw_ranked is None:
        raw_ranked = raw_actor_candidates(raw, parent, g)
    if raw_ranked is None:
        raw_ranked = []
    fixed_set = set(fixed)
    existing = list(evidence['new_anchors'])
    existing_set = set(existing)
    score_by_anchor = {anchor: score for anchor, score in raw_ranked}
    proposed = []
    if arm in ('actor_cues', 'hybrid'):
        for anchor in cues:
            if anchor not in fixed_set and anchor not in proposed:
                proposed.append(anchor)
    if arm in ('actor_quota', 'hybrid'):
        # Reserve 20 distinct actor occurrences. Suppress overlapping spans so
        # the same named participant cannot consume all reserved positions.
        actors_now = [a for a in fixed + existing if a[2] == 'actor']
        selected = list(actors_now) + proposed
        for anchor, margin in raw_ranked:
            if len(proposed) >= QUOTA:
                break
            if anchor in fixed_set or anchor in existing_set or anchor in selected:
                continue
            if any(max(anchor[0], p[0]) < min(anchor[1], p[1]) for p in selected):
                continue
            proposed.append(anchor)
            selected.append(anchor)
    if not proposed:
        return evidence
    # Numeric and repeated-surface anchors are protected against regressions.
    # New actor proposals consume low-priority spatial leftovers first.
    protected = [a for a in existing if evidence['new_endpoint_provenance'][a]['source']
                 in ('inference_text_numeric', 'recurrent_frozen_g1_surface')]
    retained = []
    seen = set(fixed)
    def take(anchor):
        if anchor not in seen and len(retained) < capacity:
            retained.append(anchor);seen.add(anchor)
    for anchor in protected:
        take(anchor)
    for anchor in proposed:
        take(anchor)
    for anchor in existing:
        take(anchor)
    # Some arms may have zero spare capacity after protection. That is a
    # measured no-op, not license to exceed 192 anchors.
    chosen = fixed + retained
    if len(chosen) != len(set(chosen)) or len(chosen) > g.p52.MAX_ANCHORS:
        _fail('ACTOR_ANCHOR_BUDGET')
    typed = g.p52.inventory(chosen, set(raw['allowed']))
    if not set(parent['recovered_typed']).issubset(typed):
        _fail('ACTOR_PARENT_MONOTONICITY')
    provenance = {a: evidence['new_endpoint_provenance'][a]
                  for a in retained if a in evidence['new_endpoint_provenance']}
    for anchor in retained:
        if anchor in provenance:
            continue
        provenance[anchor] = {'kind': 'actor', 'char_span': list(anchor[:2]),
                              'neural_margin': round(score_by_anchor.get(anchor, -100.0), 6),
                              'source': ('inference_text_actor_cue' if anchor in cues
                                         else 'frozen_g1_actor_quota'),
                              'token_first': None, 'token_last': None}
    return {**evidence, 'anchors': chosen, 'new_anchors': retained,
            'typed_edges': typed, 'new_endpoint_provenance': provenance,
            'actor_bakeoff': {'arm': arm, 'cue_proposals': len(cues),
                              'raw_proposals': len(raw_ranked),
                              'actor_anchors': sum(a[2]=='actor' for a in chosen),
                              'protected_evidence_anchors': len(protected),
                              'supplemental_budget': capacity}}


def _key(edge):
    src, dst, rel = edge
    return [rel, list(src), list(dst)]


def _entry(g, row, pair, side, raw, parent, baseline=None):
    # Policy builders complete before the *first* access to gold below.
    # One expensive evidence reconstruction and one G1 actor scan per sample.
    # All arms share the exact same source-neural and text evidence.
    base_policy = g._construct_evidence(raw, parent)
    if any(rel=='owns' and src=='actor' for rel,src,dst in raw['allowed']):
        actor_bank = raw_actor_candidates(raw, parent, g)
        cues = cue_actors(base_policy['prepared']['prompt'], base_policy['prepared'])
    else:
        actor_bank = []
        cues = []
    policies = {arm:_actor_reservation(raw,parent,g,arm=arm,evidence=base_policy,
                   raw_ranked=actor_bank,cue_candidates=cues) for arm in ARMS}
    ceiling = g.p52.MAX_ANCHORS
    mandatory = set(parent['expanded_anchors'])
    for arm, policy in policies.items():
        if len(policy['anchors']) > ceiling or not mandatory.issubset(policy['anchors']):
            _fail('POLICY_INVARIANT_' + arm)
    gold = set(g.p50.g40.base.gold_binding_edges(row))
    baseline_missing = gold - policies['evidence']['typed_edges']
    if baseline is not None:
        if ((pair['id'], side, pair['sha256'][side]) !=
            (baseline['pair_id'], baseline['side'], baseline['prompt_sha256'])):
            _fail('CANARY_BASELINE_PAIR_DRIFT')
        if (len(baseline_missing) != baseline['missing_candidate_gold_edges'] or
                (not bool(baseline_missing)) != baseline['candidate_reachable']):
            _fail('CANARY_BASELINE_REPLAY_DRIFT')
        old = baseline.get('binding_diagnosis')
        if isinstance(old, dict) and old.get('cases') is not None:
            old_keys = {(c['type'], tuple(c['src_span']), tuple(c['dst_span']),
                         c['src_kind'], c['dst_kind']) for c in old['cases']}
            missing_keys = {(r, tuple(s[:2]), tuple(d[:2]), s[2], d[2])
                            for s, d, r in baseline_missing}
            if old_keys != missing_keys:
                _fail('CANARY_BASELINE_EDGE_IDENTITY_DRIFT')
    scores = {}
    for arm, policy in policies.items():
        missing = gold - policy['typed_edges']
        regressed = missing - baseline_missing
        recovered = baseline_missing - missing
        scores[arm] = {'reachable': not missing,
                       'missing_edges': len(missing),
                       'missing_by_relation': dict(Counter(r for s,d,r in missing)),
                       'misses': sorted(_key(x) for x in missing),
                       'regressed_edges': len(regressed),
                       'regressed': sorted(_key(x) for x in regressed),
                       'recovered_edges': len(recovered),
                       'anchor_count': len(policy['anchors']),
                       'actor_anchor_count': sum(a[2]=='actor' for a in policy['anchors']),
                       'typed_candidate_edges': len(policy['typed_edges'])}
    return {'index': pair['index'], 'pair_id': pair['id'],
            'prompt_sha256': pair['sha256'][side], 'side': side,
            'family': pair['family'], 'mode': pair['mode'],
            'distractor_count': pair['distractor_count'],
            'topology_hash': pair['topology_hash'], 'arms': scores}


def _verify_saved_shard(path, digest_path, *, phase, index, entries):
    if not path.exists() or not digest_path.exists():
        _fail('PARTIAL_SHARD_' + str(index))
    receipt = json.loads(digest_path.read_text(encoding='utf-8'))
    if receipt != {'sha256': _sha(path)}:
        _fail('SHARD_HASH_MISMATCH_' + str(index))
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['schema'] != SCHEMA or data['phase'] != phase or data['start'] != index:
        _fail('SHARD_METADATA_DRIFT_' + str(index))
    if len(data['entries']) != len(entries):
        _fail('SHARD_ENTRY_COUNT_' + str(index))
    for prior, expected in zip(data['entries'], entries):
        if (prior['index'], prior['pair_id'], prior['prompt_sha256'], prior['side']) != expected:
            _fail('SHARD_PAIR_ID_DRIFT_' + str(index))
    return data


def _cohort(g, results, phase):
    start, stop = DEVELOPMENT if phase == 'development' else HOLDOUT
    if sorted({e['index'] for e in results}) != list(range(start, stop)):
        _fail('INCOMPLETE_COHORT')
    pair_groups = defaultdict(list)
    for row in results:
        pair_groups[row['index']].append(row)
    if any(len(rows)!=2 or sorted(x['side'] for x in rows)!=[0,1]
           or rows[0]['pair_id']!=rows[1]['pair_id']
           for rows in pair_groups.values()):
        _fail('COUNTERFACTUAL_PAIR_DRIFT')
    topologies = {rows[0]['topology_hash'] for rows in pair_groups.values()}
    report = {}
    for arm in ARMS:
        cells = Counter(); hit_cells=Counter(); distractors=Counter(); hit_distractors=Counter()
        hit=missing=regressed=recovered=actors=edges=0
        by_relation=Counter()
        for rows in pair_groups.values():
            a,b=rows; cell=a['family']+':'+a['mode']
            cells[cell]+=1
            bucket='with_distractors' if a['distractor_count'] else 'without_distractors'
            distractors[bucket]+=1
            for row in rows:
                q=row['arms'][arm]
                missing+=q['missing_edges'];regressed+=q['regressed_edges']
                recovered+=q['recovered_edges'];actors+=q['actor_anchor_count']
                edges+=q['typed_candidate_edges']
                by_relation.update(q['missing_by_relation'])
            if a['arms'][arm]['reachable'] and b['arms'][arm]['reachable']:
                hit+=1;hit_cells[cell]+=1;hit_distractors[bucket]+=1
        total=stop-start
        fails=[]
        if hit/total < g.ROLLOUT_MIN_REACHABILITY:
            fails.append('OVERALL_REACHABILITY_BELOW_98_PERCENT')
        if regressed:
            fails.append('INTRODUCED_MISSING_GOLD_EDGES')
        if len(topologies)<g.ROLLOUT_MIN_TOPOLOGIES:
            fails.append('STRUCTURAL_DIVERSITY_INSUFFICIENT')
        for family,mode in g.CELLS:
            cell=family+':'+mode
            count=cells[cell]
            if count<g.ROLLOUT_MIN_CELL_PAIRS or (count and hit_cells[cell]/count<g.ROLLOUT_MIN_CELL_REACHABILITY):
                fails.append('FAMILY_MODE:'+cell)
        for bucket,count in sorted(distractors.items()):
            if count>=g.ROLLOUT_MIN_CELL_PAIRS and hit_distractors[bucket]/count<g.ROLLOUT_MIN_CELL_REACHABILITY:
                fails.append('DISTRACTOR:'+bucket)
        report[arm]={'pairs':total, 'fully_reachable_pairs':hit,
                     'pair_reachability':round(hit/total,6),
                     'missing_gold_edges':missing,
                     'missing_by_relation':dict(by_relation),
                     'recovered_gold_edges':recovered,
                     'regressed_gold_edges':regressed,
                     'mean_actor_anchors_per_graph':round(actors/(2*total),3),
                     'mean_typed_candidates_per_graph':round(edges/(2*total),1),
                     'cells':{c:{'pairs':count, 'reachable':hit_cells[c]}
                              for c,count in sorted(cells.items())},
                     'distractors':{c:{'pairs':count, 'reachable':hit_distractors[c]}
                                    for c,count in sorted(distractors.items())},
                     'distinct_topologies':len(topologies),
                     'acceptance_passed':not fails, 'failed_checks':fails}
    return report


def _select_development(g, reports):
    # Selection objective fixed before holdout: zero introduced misses first;
    # then 98% coverage, number of complete pairs, missing edges and footprint.
    def rank(arm):
        r=reports[arm]
        return (int(r['regressed_gold_edges']==0),int(r['acceptance_passed']),
                r['fully_reachable_pairs'],-r['missing_gold_edges'],
                -r['mean_typed_candidates_per_graph'], -ARMS.index(arm))
    return max(ARMS, key=rank)


def _commit(g, path, obj):
    g.atomic_json(path, obj)
    g.atomic_json(path.with_suffix('.sha256.json'), {'sha256':_sha(path)})


def run(g, plan_dir, baseline_dir, output, *, root, phase='development', device='cuda', max_shards=None):
    if phase not in ('development','holdout'):
        _fail('PHASE')
    if max_shards is not None and max_shards<1:
        _fail('SHARD_BUDGET')
    plan_dir=Path(plan_dir).resolve()
    baseline_dir=Path(baseline_dir).resolve()
    output=Path(output).resolve()
    root=Path(root).resolve()
    if len({plan_dir,baseline_dir,output})!=3:
        _fail('OVERWRITE_PROTECTED_INPUT')
    plan=g._require_plan(plan_dir)
    if plan['splits']['train']['pairs']<HOLDOUT[1]:
        _fail('INSUFFICIENT_TRAIN_PAIR_COUNT')
    baseline=g.FeatureCorpus(plan_dir,baseline_dir,'train',require_complete=False)
    if baseline.config.get('candidate_policy')!='evidence':
        _fail('BASELINE_NOT_EVIDENCE')
    baseline_map={(start+delta, entry['side']):entry for start,delta,a,b in baseline.pairs
                  for entry in (a,b)}
    if phase=='development':
        missing=[i for i in range(*DEVELOPMENT) if (i,0) not in baseline_map or (i,1) not in baseline_map]
        if missing:_fail('INCOMPLETE_PRIOR_CANARY')
    if phase=='holdout' and not (output/'development-selection.json').is_file():
        _fail('DEVELOPMENT_NOT_FROZEN')
    source=_source_snapshot(g)
    config={'schema':SCHEMA, 'plan_sha256':_sha(plan_dir/'plan.json'),
            'baseline_capture_sha256':_sha(baseline_dir/'capture.json'),
            'sources':source,'arms':list(ARMS),
            'development':list(DEVELOPMENT),'holdout':list(HOLDOUT),
            'quota':QUOTA, 'frozen_g1':True, 'production_promotion':False,
            'sealed_test_used':False}
    if output.exists():
        if not (output/'config.json').is_file():
            _fail('OUTPUT_ALREADY_EXISTS_UNINITIALIZED')
        if json.loads((output/'config.json').read_text(encoding='utf-8'))!=config:
            _fail('RESTART_SOURCE_OR_PLAN_DRIFT')
    else:
        output.mkdir(parents=True)
        g.atomic_json(output/'config.json',config)
    start,stop=DEVELOPMENT if phase=='development' else HOLDOUT
    if phase=='holdout':
        selection_path=output/'development-selection.json'
        if not selection_path.exists():
            _fail('DEVELOPMENT_NOT_FROZEN')
        selection=json.loads(selection_path.read_text(encoding='utf-8'))
        if selection['schema']!=SCHEMA or selection['phase']!='development':
            _fail('DEVELOPMENT_SELECTION_INVALID')
        # Recompute and confirm selection from immutable development shards.
        previous=_read_phase(g, output,'development',plan)
        expected=_select_development(g,_cohort(g,previous,'development'))
        if expected!=selection['selected_arm'] or _cohort(g,previous,'development')!=selection['arms']:
            _fail('DEVELOPMENT_SELECTION_DRIFT')
    shards=[];completed=[];needed=[]
    for shard in g._iter_shards(plan_dir,'train',plan['seed'],g.SHARD_PAIRS):
        if shard.index<start or shard.index>=stop:
            continue
        if not start<=shard.index or shard.index+len(shard.pairs)>stop:
            _fail('UNALIGNED_BAKEOFF_SHARD')
        shards.append(shard)
        out_file=output/f'{phase}-{shard.index:07d}.json'
        digest=out_file.with_suffix('.sha256.json')
        if out_file.exists() or digest.exists():
            expected=[(p['index'],p['id'],p['sha256'][side],side)
                      for p in shard.pairs for side in (0,1)]
            _verify_saved_shard(out_file,digest,phase=phase,index=shard.index,entries=expected)
            completed.append(shard.index)
        else:
            needed.append(shard)
    if max_shards is not None:
        needed=needed[:max_shards]
    if needed:
        paths=g._capture_parent_preflight(root)
        def consume(**hooks):
            cap=hooks.get('capture_candidate_repair_rows')
            frozen=hooks.get('assert_all_frozen')
            if cap is None or frozen is None:
                _fail('FROZEN_HOOK_UNAVAILABLE')
            for shard in needed:
                entries=[]
                for offset in range(0,len(shard.rows),8):
                    rows=shard.rows[offset:offset+8]
                    captures=cap(rows,'g2_0060_'+phase,include_raw_g1=True)
                    if len(captures)!=len(rows):
                        _fail('BATCH_CARDINALITY')
                    for j,(row,record) in enumerate(zip(rows,captures)):
                        if row['prompt']!=record['row']['prompt']:
                            _fail('PROMPT_REPLAY_DRIFT')
                        pair=shard.pairs[(offset+j)//2]
                        side=(offset+j)%2
                        old_side=baseline_map.get((pair['index'],side))
                        if phase=='development' and old_side is None:
                            # baseline_map is used only for the committed canary;
                            # obtain side-specific data in the corrected map below.
                            _fail('CANARY_BASELINE_SIDE_DRIFT')
                        entries.append(_entry(g,row,pair,side,
                            record['g1_raw_interface'],record['candidate_repair'],
                            baseline=old_side if phase=='development' else None))
                data={'schema':SCHEMA,'phase':phase,'start':shard.index,
                      'pair_count':len(shard.pairs),'entries':entries,
                      'frozen_parent_replay':True}
                frozen()
                _commit(g,output/f'{phase}-{shard.index:07d}.json',data)
                print('GAMER_G2_0060_SHARD '+_json({'phase':phase,'start':shard.index,
                    'pairs':len(shard.pairs),'saved':True}),flush=True)
        with tempfile.TemporaryDirectory(prefix='.g2-0060-frozen-',dir=output) as tmp:
            scratch=Path(tmp)/'parent-run'  # p40 refuses a pre-existing output
            original=Path.cwd()
            try:
                os.chdir(Path(g.__file__).resolve().parents[2])
                g.p40.run(**paths,output=str(scratch),device=device,root=str(root),
                    g1_validation_report=str(root/'graph-g1-reconstruction-validation-0031'/
                                              'reconstruction_validation_report.json'),
                    max_length=g.p40.MAX_LENGTH,diagnostic_callback=consume)
            finally:
                os.chdir(original)
    rows=[]
    for shard in shards:
        path=output/f'{phase}-{shard.index:07d}.json'
        if path.exists():
            rows.extend(json.loads(path.read_text(encoding='utf-8'))['entries'])
    if len(rows)<2*(stop-start):
        return {'status':'IN_PROGRESS','phase':phase,'shards_completed':len(rows)//(2*g.SHARD_PAIRS),
                'pairs_captured':len(rows)//2,'pairs_expected':stop-start,
                'production_promotion':False,'sealed_test_used':False}
    reports=_cohort(g,rows,phase)
    selected=_select_development(g,reports) if phase=='development' else selection['selected_arm']
    summary={'schema':SCHEMA,'phase':phase,'status':'DEVELOPMENT_LOCKED' if phase=='development'
             else 'HOLDOUT_EVALUATED','selected_arm':selected,
             'selection_basis':'predeclared development only',
             'arms':reports,'production_promotion':False,
             'sealed_test_used':False}
    filename=('development-selection.json' if phase=='development' else 'holdout-result.json')
    dest=output/filename
    if dest.exists():
        if json.loads(dest.read_text(encoding='utf-8'))!=summary:
            _fail('FROZEN_SELECTION_REPORT_DRIFT')
    else:g.atomic_json(dest,summary)
    if phase=='holdout':
        summary['status']=('HOLDOUT_GATE_PASSED' if reports[selected]['acceptance_passed']
                           else 'HOLDOUT_GATE_FAILED')
        summary['selected_arm_regressed_gold_edges']=reports[selected]['regressed_gold_edges']
    return summary


def _read_phase(g, output, phase, plan):
    begin,end=DEVELOPMENT if phase=='development' else HOLDOUT
    all_entries=[]
    for start in range(begin,end,g.SHARD_PAIRS):
        filename=output/f'{phase}-{start:07d}.json'
        digest=filename.with_suffix('.sha256.json')
        if not filename.exists() or not digest.exists():
            _fail('MISSING_FROZEN_PHASE_SHARD')
        if json.loads(digest.read_text(encoding='utf-8'))!={'sha256':_sha(filename)}:
            _fail('FROZEN_SHARD_CHANGED')
        all_entries.extend(json.loads(filename.read_text(encoding='utf-8'))['entries'])
    return all_entries
