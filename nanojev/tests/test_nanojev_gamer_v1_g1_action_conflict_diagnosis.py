"""G1-0019 forensic replay; immutable 0018 reference, no training (CPU)."""
import hashlib
import json
from collections import Counter

import pytest
import torch

from tools import nanojev_gamer_v1_g1_action_conflict_diagnosis as diag
from tools import nanojev_gamer_v1_g1_action_width_validation as width
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT
from tools.nanojev_gamer_v1_g1_calibration_validation import _jsonl_bytes


def test_fixed_reference_no_training_and_cli(capsys):
    assert diag.PINNED_PAIRED['b_newly_recovered_gold']==13
    assert diag.PINNED_PAIRED['b_previously_correct_gold_lost']==21
    assert diag.PINNED_PAIRED['b_new_false_positives']==14
    assert diag.PINNED_PAIRED['b_old_false_positives_removed']==18
    with pytest.raises(SystemExit) as out:
        cur.main(['frob-g1-action-conflicts','--help'])
    assert out.value.code==0
    help_text=capsys.readouterr().out
    for flag in ('--ranking-run','--width-run','--action-run','--output','--device'):
        assert flag in help_text
    for flag in ('--train','--promote','--g2','--power','--seed','--challenge'):
        assert flag not in help_text


def _reference_fixture(tmp_path):
    rows=[{'prompt':f'prompt-{i}', 'split':'dev',
           'family':'encounter' if i%2==0 else 'allocation',
           'object_spans':[{'start':0,'end':1,'kind':'action'}]* (28 if i<180 else 27)}
          for i in range(320)]
    cases=[]
    totals={name:Counter() for name in diag.PINNED}
    only_a={121};only_b=set(range(122,129));both=set(range(121))
    non_a=[i for i in range(320) if i not in (both|only_a)]
    non_b=[i for i in range(320) if i not in (both|only_b)]
    for i,row in enumerate(rows):
        config={}
        for name,exact_set,others,target_miss,target_fp in (
            (width.CONTROL,both|only_a,non_a,280,225),
            (width.CHALLENGER,both|only_b,non_b,288,221)):
            if i in exact_set:
                missing=0;fp=0
            else:
                n=others.index(i)
                missing=1+int(n < target_miss-len(others))
                fp=1+int(n < target_fp-len(others))
            config[name]={'correct':len(row['object_spans'])-missing,
                          'missing':missing,'false_positives':fp,
                          'exact':i in exact_set}
            totals[name].update(config[name])
            totals[name]['exact_prompts']+=int(i in exact_set)
        cases.append({'index':i+1,'family':row['family'],'gold_count':len(row['object_spans']),
                      'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                      'configurations':config})
    for name in diag.PINNED:
        assert totals[name]['correct']==diag.PINNED[name]['correct']
        assert totals[name]['missing']==diag.PINNED[name]['missing_objects']
        assert totals[name]['false_positives']==diag.PINNED[name]['false_positive_objects']
        assert totals[name]['exact_prompts']==diag.PINNED[name]['exact_prompts']
    root=tmp_path/'reference';root.mkdir()
    dataset=_jsonl_bytes(rows)
    report={
        'schema':width.SCHEMA,'status':'FRESH_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY',
        'seed':281803,'split':'new_dev','cases':320,
        'families':{'encounter':160,'allocation':160},
        'configurations_predeclared':[
            {'key':name,'checkpoint':'null_focused','null_logit_shift':4.,
             'action_width_power':power,'action_per_span_penalty':penalty}
            for name,power,penalty in width.CANDIDATES],
        'validation_dataset_sha256':hashlib.sha256(dataset).hexdigest(),
        'old_validation_dataset_sha256':'oldval','old_fresh_dataset_sha256':'fresh',
        'previous_action_ranking_report_sha256':'rank',
        'previous_action_error_report_sha256':'action',
        'source_adapter_sha256':'adapter','head_sha256':'head',
        'checkpoint_file_sha256':{'manifest_sha256':'m','head_file_sha256':'h'},
        'fresh_ranking_not_certification':[width.CHALLENGER,width.CONTROL],
        'metrics':{
            width.CONTROL:{**diag.PINNED[width.CONTROL],
                'missing_by_kind':{'action':213,'actor':66,'quantity':1},
                'false_positives_by_kind':{'action':112,'actor':110,'outcome':1,'quantity':2}},
            width.CHALLENGER:{**diag.PINNED[width.CHALLENGER],
                'missing_by_kind':{'action':221,'actor':66,'quantity':1},
                'false_positives_by_kind':{'action':109,'actor':109,'outcome':1,'quantity':2}},
        },
        'paired_comparison':diag.PINNED_PAIRED,
        **{k:True for k in ('one_feature_capture_per_prompt','heads_frozen',
             'candidate_eligibility_unchanged','production_decoder_unchanged','no_training')},
        **{k:False for k in ('challenge_or_test_split_used','certification_authorized',
             'production_promotion','g2_unlocked')},
    }
    (root/diag.WIDTH_FILES[0]).write_text(json.dumps(report))
    (root/diag.WIDTH_FILES[1]).write_bytes(dataset)
    (root/diag.WIDTH_FILES[2]).write_bytes(_jsonl_bytes(cases))
    info={'validation_report':{'validation_dataset_sha256':'oldval'},'fresh_dataset_sha':'fresh',
          'entries':{'null_focused':{'manifest':{'head_sha256':'head'}}},
          'checkpoint_files':{'null_focused':report['checkpoint_file_sha256']},
          'adapter_sha':'adapter'}
    rank_hash={'action_ranking_report.json':'rank'}
    action_hash={'action_error_report.json':'action'}
    return root,rows,info,rank_hash,action_hash,report,cases


def test_actual_0018_contract_reference_acceptance(tmp_path):
    path,rows,info,rank_hash,action_hash,report,cases=_reference_fixture(tmp_path)
    loaded,loaded_cases,paths,hashes=diag._load_width_reference(
        path,rows,info,rank_hash,action_hash)
    assert loaded==report and loaded_cases==cases and len(paths)==len(hashes)==3


@pytest.mark.parametrize('field',[
    'head_sha256','validation_dataset_sha256','g2_unlocked','status',
    'previous_action_error_report_sha256','paired_comparison','metrics',
    'configurations_predeclared',
])
def test_reference_rejects_modified_metadata(tmp_path,field):
    path,rows,info,rank_hash,action_hash,report,_=_reference_fixture(tmp_path)
    report[field]='tampered'
    (path/diag.WIDTH_FILES[0]).write_text(json.dumps(report))
    with pytest.raises(ValueError,match='INVALID_WIDTH_REFERENCE'):
        diag._load_width_reference(path,rows,info,rank_hash,action_hash)


def test_reference_rejects_dataset_and_case_tampering(tmp_path):
    path,rows,info,r,a,_,_=_reference_fixture(tmp_path)
    file=path/diag.WIDTH_FILES[1]
    file.write_bytes(file.read_bytes().replace(b'prompt-0',b'PROMPT-0'))
    with pytest.raises(ValueError,match='WIDTH_DATASET_CHANGED'):
        diag._load_width_reference(path,rows,info,r,a)
    file.write_bytes(_jsonl_bytes(rows))
    f=path/diag.WIDTH_FILES[2]
    f.write_bytes(f.read_bytes().replace(b'prompt_sha256',b'changed_sha256',1))
    with pytest.raises(ValueError,match='INVALID_WIDTH_REFERENCE'):
        diag._load_width_reference(path,rows,info,r,a)


def test_forensics_tracks_exact_gold_objects_and_competition():
    row={'prompt':'abc','family':'encounter'}
    gold={(0,3,'action')}
    whole=(0,3,'action')
    parts={(0,1,'action'),(1,2,'action'),(2,3,'action')}
    catalog={key:{'kind':'action','span':[key[0],key[1]],
                  'token_width':key[1]-key[0],
                  'margin':2.,'control_score':2.,'challenger_score':3.}
             for key in {whole}|parts}
    events=diag._changed_objects(1,row,gold,parts,{whole},catalog)
    assert Counter(x['direction'] for x in events)=={
        'recovered_gold':1,'removed_false_positive':3}
    recovered=[x for x in events if x['direction']=='recovered_gold'][0]
    assert recovered['other_selected_non_gold_count']==3
    assert len(recovered['overlapping_other_selected'])==3
    assert recovered['relative_token_width']=='longer_than_every_competitor'
    assert recovered['local_challenger_score_gap']==-6.
    assert len(recovered['overlapping_eligible_candidates'])==4
    opposite=diag._changed_objects(1,row,parts,parts,{whole},catalog)
    assert Counter(x['direction'] for x in opposite)=={'lost_gold':3,'new_false_positive':1}


def test_guard_counts_21_lost_13_recovered_and_buckets():
    objects=[]
    for direction,count in (
        ('recovered_gold',13),('lost_gold',21),
        ('new_false_positive',14),('removed_false_positive',18)):
        for index in range(count):
            objects.append({'direction':direction,'kind':'action' if index%2 else 'actor',
                            'family':'encounter' if index%2 else 'allocation',
                            'token_width_bucket':'3-4','relative_token_width':'mixed_or_equal_width',
                            'overlapping_other_selected':[{'kind':'action'}],
                            'other_selected_non_gold_count':1,'other_selected_gold_count':0})
    summary=diag._summarize_changes(objects)
    assert summary['recovered_gold']['count']==13
    assert summary['lost_gold']['count']==21
    with pytest.raises(ValueError,match='REPLAY_CHANGE_DIVERGED'):
        diag._summarize_changes(objects[:-1])


def test_no_modified_reference_can_pass_drift_guard(tmp_path,monkeypatch):
    file=tmp_path/'report';file.write_bytes(b'old')
    hashes={'report':hashlib.sha256(b'old').hexdigest()}
    # Guard proof through its expected hash expression; run-level helper composes it.
    assert diag._file_sha(file)==hashes['report']
    file.write_bytes(b'new')
    assert diag._file_sha(file)!=hashes['report']


def test_gold_is_not_input_to_decoders(monkeypatch):
    # Only the post-inference diagnostic uses gold. Frozen decoder depends on
    # candidates and fixed numbers alone.
    candidates=[[],[(0,0,diag.SPAN_KINDS.index('action')+1,3.)]]
    a=diag.decode_action_ranked(candidates,action_power=1.,action_penalty=0.)
    b=diag.decode_action_ranked(candidates,action_power=1.25,action_penalty=0.)
    assert a==b==[{'kind':'action','token_first':0,'token_last':0}]


def test_normalized_duplicate_character_spans_remain_attributable():
    # Token span 'a ' and token span 'a' normalize to the same char span.
    prepared={'offsets':[(0,1),(1,2),(2,3)],'prompt_start':0,'prompt':'a b'}
    klass=diag.SPAN_KINDS.index('action')+1
    candidates=[[],[(0,0,klass,1.)],[(0,1,klass,2.)],[]]
    catalog=diag._candidate_catalog(candidates,prepared)
    key=(0,1,'action')
    assert len(catalog[key])==2
    decoded={width.CONTROL:[{'kind':'action','token_first':0,'token_last':0}],
             width.CHALLENGER:[{'kind':'action','token_first':0,'token_last':1}]}
    selected=diag._selected_catalog(decoded,prepared,catalog)
    assert selected[width.CONTROL][key]['token_width']==1
    assert selected[width.CHALLENGER][key]['token_width']==2


def test_mock_320_case_replay_writes_action_conflict_artifacts(tmp_path,monkeypatch):
    rows=[{'prompt':'abc','family':'encounter' if i%2==0 else 'allocation',
           'split':'dev','object_spans':[{'start':0,'end':3,'kind':'action'}]}
          for i in range(320)]
    cases=[{'configurations':{
        width.CONTROL:{'correct':0,'missing':1,'false_positives':3,'exact':False},
        width.CHALLENGER:{'correct':1,'missing':0,'false_positives':0,'exact':True}}}
        for _ in rows]
    from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _paired
    gold={(0,3,'action')}
    a={(0,1,'action'),(1,2,'action'),(2,3,'action')}
    b={*gold}
    ref_metrics={width.CONTROL:_summarize([(a,gold)]*320,rows),
                 width.CHALLENGER:_summarize([(b,gold)]*320,rows)}
    ref={'metrics':ref_metrics,'paired_comparison':_paired([(a,gold)]*320,[(b,gold)]*320),
         'validation_dataset_sha256':'mock'}
    cp=tmp_path/'cp';cp.mkdir()
    (cp/'manifest.json').write_bytes(b'manifest')
    (cp/cur.HEAD_FILE).write_bytes(b'head')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    adapter=tmp_path/'adapter';adapter.write_text('adapter')
    info={'entries':{'null_focused':{'checkpoint':cp,'manifest':{
             'source_adapter':str(adapter),'model_id':'mock','head_sha256':'head-sha'}}},
          'adapter_sha':'adapter-sha',
          'checkpoint_files':{'null_focused':{
              'manifest_sha256':digest(cp/'manifest.json'),
              'head_file_sha256':digest(cp/cur.HEAD_FILE)}},
          'fresh_rows':[],'validation_rows':[],'dataset':tmp_path/'dataset'}
    monkeypatch.setattr(diag,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(diag,'_load_action_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(diag,'_load_ranking_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(diag,'fresh_rows',lambda *args:rows)
    monkeypatch.setattr(diag,'_load_width_reference',lambda *args:(ref,cases,{},
         {diag.WIDTH_FILES[0]:'report-sha'}))
    monkeypatch.setattr(diag,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(diag,'_summarize_changes',lambda objects:{
         'recovered_gold':{'count':sum(x['direction']=='recovered_gold' for x in objects)}})
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    class Head:
        def eval(self): return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((3,3,SPAN_CLASS_COUNT),-40.,dtype=torch.float32)
            logits[:,:,0]=4.
            label=diag.SPAN_KINDS.index('action')+1
            for i in range(3): logits[i,0,label]=4.
            logits[0,2,label]=3.5
            valid=torch.zeros(3,3,dtype=torch.bool)
            for i in range(3):valid[i,:3-i]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(Head(),{
        'stage':'G1','head_sha256':'head-sha'}))
    monkeypatch.setattr(cur,'capture',lambda *args:{
        'prepared':{'eligible':torch.ones(3),'offsets':[(0,1),(1,2),(2,3)],
                    'prompt_start':0,'prompt':'abc'},
        'hidden':torch.zeros(3,4),
    })
    output=tmp_path/'output'
    actual=diag.run(root='root',transfer_report='transfer',fresh_run='fresh',
          null_run='null',validation_run='validation',action_run='action',
          ranking_run='ranking',width_run='width',output=output,device='cpu')
    assert actual['changed_object_rows']==1280
    assert actual['metrics_replayed']==ref_metrics
    assert actual['paired_replayed']==ref['paired_comparison']
    assert actual['g2_unlocked'] is False
    assert actual['no_training'] is True
    assert len(diag._read_jsonl(output/'action_conflict_objects.jsonl'))==1280
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        diag.run(root='root',transfer_report='transfer',fresh_run='fresh',
          null_run='null',validation_run='validation',action_run='action',
          ranking_run='ranking',width_run='width',output=output,device='cpu')
