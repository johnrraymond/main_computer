"""Read-only G2 curriculum readiness pretest on existing G2-0048/0047 artifacts.

No model loading, no optimization, no DEV/TEST labels, no model promotion.
This is a *data sufficiency* pretest, not a training-effect experiment.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path

CURRENT = 'graph-g2-occurrence-evidence-repair-0048'
PREVIOUS = 'graph-g2-architectural-crossover-0047'
SCHEMA48 = 'nanojev.gamer.graph.g2.occurrence-evidence-repair.v1'
SCHEMA47 = 'nanojev.gamer.graph.g2.architectural-crossover-diagnosis.v1'
SECTIONS = ('learn', 'transfer', 'train_fit', 'natural_train_audit')
SHA_KEYS = {'ledger': 'g2_occurrence_evidence_repair_per_case.jsonl.gz',
            'weights': 'g2_occurrence_evidence_repair_development.safetensors',
            'policies': 'g2_occurrence_evidence_repair_frozen_policies.json'}
REPORT48 = 'g2_occurrence_evidence_repair_report.json'
REPORT47 = 'g2_architectural_crossover_report.json'
LEDGER47 = 'g2_architectural_crossover_per_case.jsonl.gz'
WEIGHTS47 = 'g2_architectural_crossover_development.safetensors'
POLICIES47 = 'g2_architectural_crossover_frozen_policies.json'


def refuse(tag):
    raise ValueError('GAMER_G2_CURRICULUM_PRETEST_' + tag)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def wilson(k, n):
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    center = (p + z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [round(max(0, center-half), 4), round(min(1, center+half), 4)]


def _group_key(value):
    """Parse repr((source_span, destination_span, relation)) without executing it."""
    if not isinstance(value, str) or len(value) > 512:
        return None
    try:
        obj = ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    if not (isinstance(obj, tuple) and len(obj) == 3
            and isinstance(obj[0], tuple) and len(obj[0]) == 3
            and isinstance(obj[1], tuple) and len(obj[1]) == 3
            and isinstance(obj[2], str)):
        return None
    if not (all(isinstance(x, int) for x in obj[0][:2])
            and isinstance(obj[0][2], str)):
        return None
    return obj[0], obj[2]


def opportunity(row):
    """Conservative observable lower bound on hard-negative competitive pairs.

    A missing gold edge and extra selected edge with the same (source,relation)
    demonstrate a within-group ranking error when the gold edge is reachable.
    Per-case edge lists are capped upstream: a zero here is NOT proof of zero
    candidates or of zero useful contrasts.
    """
    if not row['candidate_reachable']:
        return 0
    missing = Counter(g for v in row.get('missing_edges', []) if (g := _group_key(v)) is not None)
    extra = Counter(g for v in row.get('extra_edges', []) if (g := _group_key(v)) is not None)
    return sum(missing[group] * extra[group] for group in missing.keys() & extra.keys())


def _cohort_key(section):
    return ('train_learn' if section == 'learn' else section) + '_prompts'


def _assert_safety(report, schema, status):
    if report.get('schema') != schema or report.get('status') != status:
        refuse('SCHEMA_OR_STATUS')
    for flag, required in [('development_evaluation_skipped', True),
                           ('dev_labels_used_for_training', False),
                           ('dev_labels_used_for_calibration', False),
                           ('sealed_test_used', False),
                           ('production_promotion', False), ('g2_certified', False),
                           ('frozen_g1', True), ('frozen_g2_0033_parent', True)]:
        if report.get(flag) is not required:
            refuse('SAFETY_' + flag.upper())
    policy = report.get('train_policy', {})
    if policy.get('enabled') is not False:
        refuse('POLICY_ENABLED')


def _hash_artifacts(folder, report, filenames):
    for field, name in filenames.items():
        if digest(folder / name) != report.get(field + '_sha256'):
            refuse('INTEGRITY_' + field.upper())


def _load_rows(path, report, *, old=False):
    rows = {section: {} for section in SECTIONS}
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for raw in stream:
            row = json.loads(raw)
            if old and row.get('arm') != 'frozen:bce':
                continue
            sec = row.get('section')
            if old and sec == 'train_learn':
                sec = 'learn'
            if sec not in rows:
                refuse('LEDGER_SECTION')
            sha = row.get('prompt_sha256')
            if not isinstance(sha, str) or len(sha) != 64:
                refuse('PROMPT_ID')
            if sha in rows[sec]:
                refuse('LEDGER_DUPLICATE')
            rows[sec][sha] = row
    for sec, cases in rows.items():
        expected = report['cohorts'].get(_cohort_key(sec))
        if not isinstance(expected, list) or len(expected) != len(set(expected)):
            refuse('COHORT_' + sec.upper())
        if set(cases) != set(expected):
            refuse('LEDGER_COHORT_' + sec.upper())
    return rows


def _validate_row(row):
    fields = ('missing', 'extra', 'gold_count', 'selected_count')
    if any(not isinstance(row.get(f), int) or isinstance(row[f], bool) or row[f] < 0 for f in fields):
        refuse('ROW_COUNTS')
    if row['selected_count'] - row['gold_count'] != row['extra'] - row['missing']:
        refuse('ROW_SET_ARITHMETIC')
    if row.get('exact') is not (row['missing'] == row['extra'] == 0):
        refuse('ROW_EXACT')
    gap = row.get('missing_candidate_gold_edges')
    if not isinstance(gap, int) or gap < 0:
        refuse('CANDIDATE_GAP_MISSING')
    if row.get('candidate_reachable') is not (gap == 0):
        refuse('ROW_REACHABILITY')
    if row['exact'] and gap:
        refuse('UNREACHABLE_EXACT')
    if not isinstance(row.get('missing_edges'), list) or not isinstance(row.get('extra_edges'), list):
        refuse('EDGE_DIAGNOSTIC_MISSING')
    if len(row['missing_edges']) > 12 or len(row['extra_edges']) > 12:
        refuse('EDGE_DIAGNOSTIC_CAP')
    if row.get('family') is not None and not isinstance(row['family'], str):
        refuse('FAMILY')
    if not isinstance(row.get('inversions'), int) or row['inversions'] < 0:
        refuse('INVERSIONS')


def summarise(rows):
    seq = list(rows)
    cases=len(seq);exact=sum(x['exact'] for x in seq)
    reachable=[x for x in seq if x['candidate_reachable']]
    missing=sum(x['missing'] for x in seq)
    extra=sum(x['extra'] for x in seq)
    competing=[x for x in seq if opportunity(x)>0]
    return {
        'cases':cases,'exact':exact,'exact_rate':round(exact/cases, 5) if cases else None,
        'wilson95':wilson(exact,cases),
        'reachable':len(reachable),
        'exact_given_reachable':sum(x['exact'] for x in reachable),
        'reachable_exact_rate':round(sum(x['exact'] for x in reachable)/len(reachable),5) if reachable else None,
        'missing':missing,'extra':extra,
        'count_error_l1':sum(abs(x['gold_count']-x['selected_count']) for x in seq),
        'rank_inversions':sum(x['inversions'] for x in seq),
        'observed_competitive_failure_graphs':len(competing),
        'observed_competitive_pairs_lower_bound':sum(opportunity(x) for x in seq),
        'edge_lists_truncated_cases':sum(x['missing']>len(x['missing_edges']) or
                                         x['extra']>len(x['extra_edges']) for x in seq),
    }


def mcnemar_two_sided(fixed, regressed):
    """Exact paired sign/binomial test on discordant graph outcomes."""
    n=fixed+regressed
    if not n:return 1.0
    tail=sum(math.comb(n,k) for k in range(min(fixed,regressed)+1))/2**n
    return round(min(1.,2*tail),8)


def analyze(report48, rows48, report47=None, rows47=None):
    for section in SECTIONS:
        for row in rows48[section].values():
            _validate_row(row)
        result = summarise(rows48[section].values())
        reported = report48['results'][section]['all']
        for key in ('cases','exact','missing','extra'):
            if reported.get(key) != result[key]:
                refuse('SUMMARY_MISMATCH_' + section.upper() + '_' + key.upper())
        if reported.get('rank_inversions') != result['rank_inversions']:
            refuse('INVERSIONS_MISMATCH_' + section.upper())
    summaries={sec: summarise(rows48[sec].values()) for sec in SECTIONS}
    families={}
    sizes={}
    family_sizes={}
    bins=((0,10,'0-10'),(11,20,'11-20'),(21,40,'21-40'),(41,float('inf'),'41+'))
    for section in SECTIONS:
        fam=defaultdict(list);size=defaultdict(list);cross=defaultdict(list)
        for row in rows48[section].values():
            fam[str(row.get('family') or 'unclassified')].append(row)
            for low,high,label in bins:
                if low<=row['gold_count']<=high:
                    size[label].append(row)
                    cross[(str(row.get('family') or 'unclassified'),label)].append(row)
                    break
        families[section]={k:summarise(v) for k,v in sorted(fam.items())}
        sizes[section]={key:summarise(size.get(key,[])) for _,_,key in bins}
        family_sizes[section]={fam_name:{key:summarise(cross.get((fam_name,key),[]))
                                    for _,_,key in bins} for fam_name in families[section]}
    regression={}
    if report47 is not None:
        if report47['cohorts'] != report48['cohorts']:
            refuse('BASELINE_COHORT_DRIFT')
        for sec in SECTIONS:
            previously=rows47[sec]
            now=rows48[sec]
            regression[sec]={
                'previous_exact':sum(x['exact'] for x in previously.values()),
                'current_exact':sum(x['exact'] for x in now.values()),
                'fixed_graphs':sum(not previously[k]['exact'] and now[k]['exact'] for k in now),
                'regressed_graphs':sum(previously[k]['exact'] and not now[k]['exact'] for k in now),
            }
            ref=report47['results']['arms']['frozen:bce'][
                'train_learn' if sec=='learn' else sec]['all']
            regression[sec]['net_change']=regression[sec]['fixed_graphs']-regression[sec]['regressed_graphs']
            regression[sec]['mcnemar_exact_two_sided_p']=mcnemar_two_sided(
                regression[sec]['fixed_graphs'],regression[sec]['regressed_graphs'])
            if ref['exact'] != regression[sec]['previous_exact']:
                refuse('BASELINE_SUMMARY_' + sec.upper())
    return {
        'schema':'nanojev.gamer.g2.curriculum-readiness-pretest.v1',
        'status':'READ_ONLY_TRAIN_COHORT_PRETEST_COMPLETE',
        'source':'existing_0048_and_optional_0047_immutable_reports',
        'results':summaries,'by_family':families,'by_gold_graph_size':sizes,
        'by_family_and_gold_graph_size':family_sizes,
        'paired_previous_comparison':regression if report47 is not None else None,
        'learning_pipeline_readiness':{
            'candidate_recovery':{'observable':True,'required':summaries['transfer']['reachable']<summaries['transfer']['cases']},
            'contrastive_ranking':{
                'observable_competing_missed_extra_pairs':True,
                'candidate_level_gold_negative_pairs_available':False,
                'meaning':'observed pairs are LOWER BOUNDS, not sufficient to train a ranking curriculum'},
            'counterfactual_training':{
                'verified_paraphrase_and_role_reversal_pairs_available':False,
                'meaning':'prompt hashes and error lists do not establish semantic counterfactual labels'},
            'whole_graph_training':{
                'graph_exactness_and_cardinality_errors_available':True,
                'differentiable_decoder_or_joint_log_partition_available':False},
            'retention':{'paired_graph_regressions_available':report47 is not None},
        },
        'decision':'MEASURED_CURRICULUM_OPPORTUNITIES_NOT_TRAINING_EFFECTIVENESS',
        'development_evaluation_skipped':True,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
    }


def run(report_root, *, compare_previous=True):
    base=Path(report_root)
    folder48=base/CURRENT
    report48=load_json(folder48/REPORT48)
    _assert_safety(report48,SCHEMA48,'TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE')
    _hash_artifacts(folder48,report48,SHA_KEYS)
    if load_json(folder48/SHA_KEYS['policies'])!=report48['train_policy']:
        refuse('POLICY_CONTENT')
    rows48=_load_rows(folder48/SHA_KEYS['ledger'],report48)
    report47=None;rows47=None
    if compare_previous:
        folder47=base/PREVIOUS
        report47=load_json(folder47/REPORT47)
        _assert_safety(report47,SCHEMA47,'TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE')
        _hash_artifacts(folder47, report47, {'ledger':LEDGER47,
                                              'weights':WEIGHTS47,
                                              'policies':POLICIES47})
        if load_json(folder47/POLICIES47)!=report47['train_policy']:
            refuse('BASELINE_POLICY')
        h47 = report48.get('parent_hashes', {})
        for item, path in (('report',folder47/REPORT47), ('ledger',folder47/LEDGER47),
                           ('weights',folder47/WEIGHTS47), ('policies',folder47/POLICIES47)):
            if h47.get('g2_0047_'+item+'_sha256') != digest(path):
                refuse('PARENT47_LINEAGE_'+item.upper())
        rows47=_load_rows(folder47/LEDGER47,report47,old=True)
    result=analyze(report48,rows48,report47,rows47)
    result['artifact_hashes']={'g2_0048_report_sha256':digest(folder48/REPORT48),
        'g2_0048_ledger_sha256':digest(folder48/SHA_KEYS['ledger'])}
    if compare_previous:
        result['artifact_hashes']['g2_0047_report_sha256']=digest(base/PREVIOUS/REPORT47)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    default=Path(__file__).resolve().parents[2]/'runtime'/'reports'/'gamer-v1'
    parser.add_argument('--report-root',type=Path,default=default)
    parser.add_argument('--without-0047',action='store_true',help='Skip matched baseline regression comparison')
    parser.add_argument('--output',type=Path,help='Optional new JSON output path (must not already exist)')
    args=parser.parse_args(argv)
    report=run(args.report_root,compare_previous=not args.without_0047)
    if args.output:
        if args.output.exists():
            refuse('OUTPUT_EXISTS')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,sort_keys=True,indent=2,allow_nan=False)
            stream.write('\n')
    print('G2 LEARNING CURRICULUM — READ-ONLY PRETEST')
    for sec,stats in report['results'].items():
        print(f"  {sec}: exact {stats['exact']}/{stats['cases']}; reachable {stats['reachable']}/{stats['cases']}; "
              f"inversions {stats['rank_inversions']}; observed competitive pairs >= {stats['observed_competitive_pairs_lower_bound']}")
    print('TRANSFER FAMILY BREAKDOWN')
    for family,stats in report['by_family']['transfer'].items():
        print(f"  {family}: exact {stats['exact']}/{stats['cases']}; reachable {stats['reachable']}/{stats['cases']}; "
              f"graph count error {stats['count_error_l1']}")
    if report['paired_previous_comparison']:
        print('PAIRED 0047 TO 0048 RETENTION')
        for sec,change in report['paired_previous_comparison'].items():
            print(f"  {sec}: fixed {change['fixed_graphs']}; regressed {change['regressed_graphs']}")
    print('COUNTERFACTUAL PAIRS: not verifiable from saved ledgers; prompt-level labeled pairs required')
    print('DECISION:',report['decision'])
    print('NO TRAINING, NO MODEL OR POLICY CHANGES')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
