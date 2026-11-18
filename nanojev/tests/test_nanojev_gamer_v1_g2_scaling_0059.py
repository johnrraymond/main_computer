"""G2-0059 executable acceptance: data, capture, pause/resume, evaluation isolation."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil

import pytest
import torch

from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_counterfactual_learning as p50


def test_generator_variable_structures_and_executable_pair_alignment():
    rows=[g.make_pair(s,i) for s in g.SPLITS for i in range(120)]
    assert all(p['gold_edges']<=p55.SLOTS for p in rows)
    assert all(p['sha256'][0]!=p['sha256'][1] for p in rows)
    assert len({p['scenario_hash'] for p in rows})==len(rows)
    assert len({p['sha256'][0] for p in rows})==len(rows)
    for split in g.SPLITS:
        scoped=[p for p in rows if p['split']==split]
        assert len({p['topology_hash'] for p in scoped})>=50
        assert {p['family']+p['mode'] for p in scoped}=={
            'allocationrenaming','allocationrole_swap','encounterrenaming','encounterrole_swap'}
    assert set(p['dimensions'][0] for p in rows if p['split']=='challenge' and
               p['family']=='allocation')=={6}
    assert all(p['dimensions'][0]<6 for p in rows if p['split']!='challenge' and
               p['family']=='allocation')
    assert all(p['dimensions'] in ([2,4],[4,2]) for p in rows
               if p['split']=='challenge' and p['family']=='encounter')


def test_immutable_plan_hash_and_scenario_split_replay(tmp_path):
    folder=tmp_path/'plan'
    report=g.create_plan(folder,train_graphs=80,validation_graphs=80,
                         test_graphs=80,challenge_graphs=80)
    assert g._require_plan(folder)==report
    assert report['splits']['train']['distinct_topologies']>=24
    # A scenario cannot repeat within or between splits.
    seen=set()
    for split in g.SPLITS:
        for receipt in g._read_receipts(folder,split):
            pair=g.make_pair(split,receipt['index'],report['seed'])
            assert g.pair_receipt(pair)==receipt
            assert receipt['scenario_hash'] not in seen
            seen.add(receipt['scenario_hash'])
    with pytest.raises(ValueError,match='IMMUTABLE_PLAN'):
        g.create_plan(folder,train_graphs=80,validation_graphs=80,
                      test_graphs=80,challenge_graphs=80)
    with (folder/'test.jsonl.gz').open('ab') as f:f.write(b'corrupt')
    with pytest.raises(ValueError,match='PLAN_HASH_test'):
        g._require_plan(folder)


def _fake_capture(monkeypatch,tmp_path):
    root=tmp_path/'parents';root.mkdir()
    # The real 0040 runner requires historical parent paths. Previous tests
    # mocked the runner and never checked those path names.
    for parent in g._capture_parent_paths(root).values():
        Path(parent).mkdir(parents=True,exist_ok=True)
    precision=Path(g._capture_parent_paths(root)['precision30_run'])
    (precision/g.p40.p30.REPORT).write_text('{}',encoding='utf-8')
    g1=root/'graph-g1-reconstruction-validation-0031'
    g1.mkdir()
    (g1/'reconstruction_validation_report.json').write_text('{}',encoding='utf-8')
    source=root/'graph-g2-0057-counterfactual-bakeoff'
    source.mkdir();(source/g.p57.REPORT).write_text('{}',encoding='utf-8')
    torch.manual_seed(91)
    frozen=g.p56._copy_state(p55.model_for('set_prediction'))
    monkeypatch.setattr(g.p58,'verify_parent57',lambda _:(frozen,{},{}))
    monkeypatch.setattr(g.g53,'verify_parent52',lambda _:None)
    monkeypatch.setattr(g,'_load_parent_encoder',lambda path,device:(torch.nn.Identity(),'0'*64))
    def from_raw(raw,proposal):
        return proposal
    monkeypatch.setattr(g.p52,'construct',from_raw)
    def fake_features(raw,expanded,encoder,section):
        anchors=expanded['anchors'];edge_list=expanded['edges']
        ids={a:i for i,a in enumerate(anchors)}
        torch.manual_seed(13+len(anchors))
        return {'node':torch.randn(len(anchors),96,dtype=torch.float16),
            'src':torch.tensor([ids[e[0]] for e in edge_list],dtype=torch.long),
            'dst':torch.tensor([ids[e[1]] for e in edge_list],dtype=torch.long),
            'relation':torch.tensor([g53.REL_INDEX[e[2]] for e in edge_list],dtype=torch.long),
            'kinds':torch.tensor([g53.KIND_INDEX[a[2]] for a in anchors],dtype=torch.long),
            'margin':torch.zeros(len(anchors)),
            'geometry':torch.zeros(len(edge_list),5),
            'edges':edge_list,'section':section}
    monkeypatch.setattr(g.g53,'frozen_features',fake_features)
    def fake_runner(*,diagnostic_callback,output,**_):
        # The established graph-curriculum CLI enters the repository root
        # before invoking G2-0040. The scalable route must do the same.
        assert Path.cwd()==Path(g.__file__).resolve().parents[2]
        # Emulate the real G2-0040 immutable-output precondition. Capture
        # must supply a never-used parent path, including on resumed runs.
        working=Path(output)
        assert not working.exists(), 'parent capture output must be fresh'
        working.mkdir(parents=True)
        (working/'invocation.txt').write_text('parent output exists',encoding='utf-8')
        def cap(rows,tag,include_raw_g1):
            assert include_raw_g1
            out=[]
            for row in rows:
                gold=sorted(p50.g40.base.gold_binding_edges(row))
                anchors=sorted({node for edge in gold for node in edge[:2]})
                assert len(gold)<=p55.SLOTS
                out.append({'row':row,'candidate_repair':{'anchors':anchors,'edges':gold},
                            'g1_raw_interface':{'fake':True}})
            return out
        return diagnostic_callback(capture_candidate_repair_rows=cap,
                                   assert_all_frozen=lambda:None)
    monkeypatch.setattr(g.p40,'run',fake_runner)
    return root


def test_capture_resume_integrity_and_training_evaluation_gates(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture';train_dir=tmp_path/'trained'
    g.create_plan(plan,train_graphs=16,validation_graphs=8,
                  test_graphs=8,challenge_graphs=8)
    original_working_directory=Path.cwd()
    first=g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)
    assert Path.cwd()==original_working_directory
    assert first['captured_now']==1 and first['remaining']>0
    with pytest.raises(ValueError,match='CAPTURE_INCOMPLETE'):
        g.FeatureCorpus(plan,cache,'validation')
    second=g.capture(plan,cache,root=root,device='cpu',shard_pairs=4)
    assert Path.cwd()==original_working_directory
    assert second['completed_before']==1 and second['remaining']==0
    completed=g.capture(plan,cache,root=root,device='cpu',shard_pairs=4)
    assert completed['captured_now']==0
    assert {'pipeline','graph_author','legacy_frozen_runner','counterfactual_label_contract',
            'g2_0052','frozen_occurrence_encoder','g2_features',
            'training_pair_alignment'} <= set(json.loads(
                (cache/'capture.json').read_text())['capture_source_hashes'])
    audit=g.coverage_audit(plan,cache)
    assert all(v['complete'] and v['pair_reachability']==1.0
               for v in audit['results'].values())
    # A training feature loader never exports either gold or canonical IDs.
    features=g.FeatureCorpus(plan,cache,'test')
    (a,b),_=features.pair(0,with_labels=False)
    assert 'positive' not in a and 'truth_count' not in a
    assert 'training_node_ids' not in a
    left,right=features.pair(0,with_labels=True)[0]
    assert 'training_node_ids' not in left and 'positive' in left
    assert not any('positive' in key for key in features._tensors(0)[0])
    assert g.training_plan(plan,cache,epochs=2,batch_pairs=2)['total_optimizer_steps']==8
    # Force a real optimizer step, checkpoint, restart, final validation
    # selection and frozen baseline/candidate TEST comparison.
    report=g.train(plan,cache,train_dir,root=root,device='cpu',epochs=1,
                   batch_pairs=2,checkpoint_every=2,max_new_steps=1)
    assert report['status']=='PAUSED_RESTARTABLE' and report['global_step']==1
    report=g.train(plan,cache,train_dir,root=root,device='cpu',epochs=1,
                   batch_pairs=2,checkpoint_every=2)
    assert report['status']=='RESEARCH_TRAINING_COMPLETE_NOT_CERTIFIED'
    assert report['optimizer_steps']==4
    assert g._checkpoints(train_dir)[-1]['step']==4
    assert not (train_dir/'evaluation.json').exists()
    assert not (train_dir/'test.jsonl.gz').exists()
    evaluation=g.evaluate(plan,cache,train_dir,tmp_path/'final',root=root,device='cpu')
    assert set(evaluation['test_and_structural_challenge'])=={'test','challenge'}
    assert not evaluation['gates']['production_promotion']
    with pytest.raises(ValueError,match='FINAL_EVALUATION_ALREADY_EXISTS'):
        g.evaluate(plan,cache,train_dir,tmp_path/'final',root=root,device='cpu')
    # A corrupted feature shard cannot silently enter another training run.
    f,_,_=g._shard_paths(cache,'train',0)
    with f.open('ab') as stream:stream.write(b'broken')
    with pytest.raises(ValueError,match='SHARD_INTEGRITY'):
        g.coverage_audit(plan,cache)


def test_train_budget_does_not_undertrain_large_dataset(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture'
    g.create_plan(plan,train_graphs=40,validation_graphs=8,test_graphs=8,challenge_graphs=8)
    g.capture(plan,cache,root=root,device='cpu',shard_pairs=4)
    budget=g.training_plan(plan,cache,epochs=3,batch_pairs=2)
    assert budget['total_optimizer_steps']==30
    assert budget['graph_presentations']==120
    assert budget['steps_per_epoch']==10


def test_checkpoint_integrity_blocks_drift(tmp_path):
    torch.manual_seed(12)
    model=p55.model_for('set_prediction')
    optimizer=torch.optim.AdamW(model.parameters())
    d=tmp_path/'ckpt';d.mkdir()
    info=g._save_checkpoint(d,0,model,optimizer,epoch=0,next_batch=0,
        metrics={'exact':0,'missing':4,'extra':5,'exact_pairs':0})
    assert g._checkpoints(d)[0]==info
    _,_,metadata=g._ckpt_files(d,0)
    record=json.loads(metadata.read_text(encoding='utf-8'))
    record['weights_sha256']='a'*64
    metadata.write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises(ValueError,match='CHECKPOINT_HASH'):
        g._checkpoints(d)


def test_restart_matches_uninterrupted_weights(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture'
    g.create_plan(plan,train_graphs=16,validation_graphs=8,
        test_graphs=8,challenge_graphs=8)
    g.capture(plan,cache,root=root,device='cpu',shard_pairs=4)
    full=g.train(plan,cache,tmp_path/'full',root=root,device='cpu',
                 epochs=1,batch_pairs=2,checkpoint_every=2)
    paused=g.train(plan,cache,tmp_path/'resume',root=root,device='cpu',
                   epochs=1,batch_pairs=2,checkpoint_every=2,max_new_steps=1)
    assert paused['status']=='PAUSED_RESTARTABLE'
    resumed=g.train(plan,cache,tmp_path/'resume',root=root,device='cpu',
                    epochs=1,batch_pairs=2,checkpoint_every=2)
    assert full['selected_step']==resumed['selected_step']
    assert full['validation']==resumed['validation']
    a=g.load_file(str(g._ckpt_files(tmp_path/'full',4)[0]))
    b=g.load_file(str(g._ckpt_files(tmp_path/'resume',4)[0]))
    assert set(a)==set(b)
    assert all(torch.equal(a[k],b[k]) for k in a)


def test_ladder_is_validation_selected_and_does_not_reuse_test(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture'
    g.create_plan(plan,train_graphs=40,validation_graphs=8,
        test_graphs=8,challenge_graphs=8)
    g.capture(plan,cache,root=root,device='cpu',shard_pairs=4)
    small=g.train(plan,cache,tmp_path/'small',root=root,device='cpu',
                  epochs=1,batch_pairs=2,limit_training_graphs=16)
    big=g.train(plan,cache,tmp_path/'big',root=root,device='cpu',
                epochs=1,batch_pairs=2,limit_training_graphs=40)
    assert small['cohort_graphs']==16
    assert big['cohort_graphs']==40
    outcome=g.evaluate(plan,cache,[tmp_path/'small',tmp_path/'big'],
                       tmp_path/'final',root=root,device='cpu')
    assert [r['training_graphs'] for r in outcome['training_ladder']]==[16,40]
    assert outcome['selected_training_graphs'] in (16,40)
    assert outcome['gates']['validation_selected_before_test']
    with pytest.raises(ValueError,match='LADDER_TRAINING_PATHS'):
        g.evaluate(plan,cache,[tmp_path/'big',tmp_path/'big'],
                   tmp_path/'other-final',root=root,device='cpu')


def test_pilot_does_not_authorize_incomplete_training(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture'
    g.create_plan(plan,train_graphs=16,validation_graphs=8,
        test_graphs=8,challenge_graphs=8)
    g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)
    audited=g.coverage_audit(plan,cache,require_complete=False)
    assert audited['pilot_only']
    assert not audited['training_coverage_gate_passed']
    with pytest.raises(ValueError,match='CAPTURE_INCOMPLETE'):
        g.training_plan(plan,cache,epochs=1,batch_pairs=2)


def test_failed_frozen_capture_restores_cwd_and_restarts(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'cache'
    g.create_plan(plan,train_graphs=16,validation_graphs=8,
                  test_graphs=8,challenge_graphs=8)
    original_runner=g.p40.run
    original_cwd=Path.cwd()
    def interrupted_runner(*,output,**kwargs):
        assert Path.cwd()==Path(g.__file__).resolve().parents[2]
        Path(output).mkdir(parents=True)
        raise RuntimeError('INTERRUPTED_FROZEN_CAPTURE')
    monkeypatch.setattr(g.p40,'run',interrupted_runner)
    with pytest.raises(RuntimeError,match='INTERRUPTED_FROZEN_CAPTURE'):
        g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)
    assert Path.cwd()==original_cwd
    assert not list(cache.glob('.g2-0059-frozen-*'))
    monkeypatch.setattr(g.p40,'run',original_runner)
    recovered=g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)
    assert recovered['captured_now']==1
    assert Path.cwd()==original_cwd


def test_capture_parent_paths_match_real_historical_cli(tmp_path):
    """Capture must use the same 0001-0039 parent names as the real CLI."""
    script=Path(g.__file__).with_name('nanojev_gamer_v1_graph_curriculum.py')
    tree=ast.parse(script.read_text(encoding='utf-8'))
    actual={key:Path(value).name for key,value in g._capture_parent_paths(tmp_path).items()}
    candidates=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Dict):
            try:declared=ast.literal_eval(node)
            except (ValueError,TypeError,KeyError,RecursionError):continue
            if isinstance(declared,dict) and all(name in declared for name in actual):
                candidates.append(declared)
    assert candidates, 'cannot find canonical 0001-0039 CLI parent defaults'
    assert any(all(actual[name]==declared[name] for name in actual)
               for declared in candidates), 'one or more capture paths diverge from the real CLI'
    assert actual['precision30_run']=='graph-g2-graph-precision-selection-0030'


def test_parent_preflight_fails_before_loading_qwen(tmp_path):
    root=tmp_path/'parents';root.mkdir()
    with pytest.raises(ValueError,match='CAPTURE_PARENTS_MISSING') as failure:
        g._capture_parent_preflight(root)
    assert 'graph-g2-graph-precision-selection-0030' in str(failure.value)
    assert 'graph-g1-reconstruction-validation-0031' in str(failure.value)
    # The genuinely missing directory must be reported, not silently replaced
    # with a made-up alternative or allowed to fail after a GPU model load.


def test_failed_pre_capture_source_change_is_safely_recoverable(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    plan=tmp_path/'plan';cache=tmp_path/'capture'
    g.create_plan(plan,train_graphs=16,validation_graphs=8,
                  test_graphs=8,challenge_graphs=8)
    # In the reported incident an empty capture directory contains only
    # capture.json, written before 0040 hit the incorrect G2-0030 path.
    cache.mkdir()
    import tools.nanojev_gamer_v1_g2_scaling_0059 as module
    config={'schema':g.SCHEMA,'plan_sha256':g.file_sha(plan/'plan.json'),
       'frozen_encoder_sha256':'0'*64,
       'parent57_report_sha256':g.file_sha(root/'graph-g2-0057-counterfactual-bakeoff'/g.p57.REPORT),
       'parent57_artifact_hashes':{},
       'capture_source_hashes':{name:g.file_sha(Path(mod.__file__)) for name,mod in
            (('pipeline',module),('graph_author',g.corpus),('legacy_frozen_runner',g.p40),
             ('counterfactual_label_contract',g.p50),('g2_0052',g.p52),
             ('frozen_occurrence_encoder',g.d48),('g2_features',g.g53),
             ('training_pair_alignment',g.p57))},
       'shard_pairs':4,'production_promotion':False}
    config['capture_source_hashes']['pipeline']='a'*64
    (cache/'capture.json').write_text(json.dumps(config),encoding='utf-8')
    report=g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)
    assert report['captured_now']==1
    refreshed=json.loads((cache/'capture.json').read_text(encoding='utf-8'))
    assert refreshed['capture_source_hashes']['pipeline']==g.file_sha(Path(g.__file__))
    assert g.verify_shard(cache,'train',0)['pair_count']==4
    # Source changes after committing a shard MUST NOT trigger a rebase.
    refreshed['capture_source_hashes']['pipeline']='a'*64
    (cache/'capture.json').write_text(json.dumps(refreshed),encoding='utf-8')
    with pytest.raises(ValueError,match='CAPTURE_CONFIG_DRIFT'):
        g.capture(plan,cache,root=root,device='cpu',shard_pairs=4,max_shards=1)

