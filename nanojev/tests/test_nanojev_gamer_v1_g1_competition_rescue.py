"""G1-0028: gold-blind rescue invariants, lineage, CLI, and novel-gold mapping."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import pytest

from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_layout_filter as old
from tools import nanojev_gamer_v1_graph_curriculum as cli
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def _proposals(*values):
    by_end=[[] for _ in range(8)]
    for start,end,kind,margin in values:
        by_end[end+1].append((start,end-start,SPAN_KINDS.index(kind)+1,margin))
    return by_end


def _span(start,end,kind):
    return {'token_first':start,'token_last':end,'kind':kind}


def test_surviving_older_object_can_displace_weaker_new_conflict():
    guard=[_span(1,2,'actor')]
    filtered=[_span(1,3,'action')]
    by_end=_proposals((1,2,'actor',2.4),(1,3,'action',2.0))
    recovered,counts=c.protect_surviving_guard(guard,filtered,by_end,margin_ratio=.95)
    assert recovered==guard
    assert counts['restored_guard_component']==1


def test_stronger_new_conflict_is_not_replaced():
    guard=[_span(1,2,'actor')]
    filtered=[_span(1,3,'action')]
    by_end=_proposals((1,2,'actor',1.1),(1,3,'action',2.0))
    retained,counts=c.protect_surviving_guard(guard,filtered,by_end,margin_ratio=.95)
    assert retained==filtered
    assert counts['retained_filter_component']==1


def test_removed_layout_candidate_is_never_resurrected_even_when_guard_confident():
    guard=[_span(1,2,'actor')]
    filtered=[_span(1,3,'action')]
    by_end=_proposals((1,3,'action',1.0))
    retained,counts=c.protect_surviving_guard(guard,filtered,by_end,margin_ratio=.95)
    assert retained==filtered
    assert counts['filtered_guard_cannot_be_restored']==1


def test_removed_and_unmatched_candidates_do_not_return():
    guard=[_span(0,1,'outcome')]
    filtered=[]
    selected,stats=c.protect_surviving_guard(guard,filtered,_proposals(),margin_ratio=.95)
    assert selected==[]
    assert stats['disjoint_or_removed_component']==1


def test_conflicting_multi_original_stays_filtered():
    guard=[_span(0,0,'actor'),_span(2,2,'actor')]
    filtered=[_span(0,3,'action')]
    proposals=_proposals((0,0,'actor',4.0),(2,2,'actor',4.0),(0,3,'action',1.0))
    selected,stats=c.protect_surviving_guard(guard,filtered,proposals,margin_ratio=.95)
    assert selected==filtered
    assert stats['ambiguous_component']==1


def test_reject_undeclared_ratios_and_invalid_filtered_selection():
    with pytest.raises(ValueError,match='UNDECLARED_RATIO'):
        c.protect_surviving_guard([],[],_proposals(),margin_ratio=1.0)
    with pytest.raises(ValueError,match='SELECTED_NOT_IN_FILTERED_CATALOG'):
        c.protect_surviving_guard([_span(0,0,'actor')],[_span(0,1,'action')],
           _proposals((0,0,'actor',2.0)),margin_ratio=.95)


@pytest.mark.parametrize('layout',c.INDEPENDENT_LAYOUTS)
def test_new_layout_reconstructs_all_author_gold_and_semantics(layout):
    from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
    for generator in (encounter,allocation):
        source=generator(random.Random(280028),'dev',4)
        shifted=c.independent_layout_row(source,layout)
        assert shifted['split']=='ood_dev'
        assert shifted['source_prompt_sha256']==c._digest(source['prompt'])
        assert len(shifted['object_spans'])==len(source['object_spans'])
        assert {x['kind'] for x in shifted['object_spans']}=={x['kind'] for x in source['object_spans']}
        for prior,post in zip(source['object_spans'],shifted['object_spans']):
            assert source['prompt'][prior['start']:prior['end']]==shifted['prompt'][post['start']:post['end']]
        assert shifted['expected_decisions']==source['expected_decisions']


def test_retention_checks_are_strict_about_any_new_gold_loss():
    baseline={'f1':.91,'exact_prompts':4,'missing_objects':10,'false_positive_objects':12}
    improved={'f1':.93,'exact_prompts':5,'missing_objects':9,'false_positive_objects':11}
    group={'metrics':{old.CHALLENGER:baseline,c.PRIMARY:improved},
           'paired_vs_filter':{c.PRIMARY:{'b_previously_correct_gold_lost':1}}}
    verdict=c._retention(group)
    assert verdict['no_previously_correct_gold_lost'] is False
    assert sum(verdict.values())==4


@pytest.fixture
def cli_setup(tmp_path,monkeypatch):
    nanojev=tmp_path/'nanojev';tools=nanojev/'tools';tools.mkdir(parents=True)
    monkeypatch.setattr(cli,'__file__',str(tools/'nanojev_gamer_v1_graph_curriculum.py'))
    reports=tmp_path/'runtime'/'reports'/'gamer-v1';reports.mkdir(parents=True)
    transfer=reports/'graph-g1-decoder-transfer-0008'/'transfer_report.json'
    transfer.parent.mkdir();transfer.write_text('{}')
    for name in ('graph-g1-fresh-dev-0011','graph-g1-null-boundary-0012',
                 'graph-g1-calibration-validation-0014','graph-g1-structural-ood-0022'):
        (reports/name).mkdir()
    layout=reports/'graph-g1-layout-filter-0026';layout.mkdir()
    (layout/'layout_filter_report.json').write_text('{}')
    retention=reports/'graph-g1-filter-retention-0027';retention.mkdir()
    (retention/'filter_retention_report.json').write_text('{}')
    monkeypatch.chdir(nanojev)
    return tmp_path,nanojev,reports,layout,retention


def test_cli_uses_only_py_and_no_manual_path_arguments(cli_setup,monkeypatch):
    repo,nanojev,reports,layout,retention=cli_setup
    captured=[]
    monkeypatch.setattr(c,'run',lambda **kw:captured.append((kw,Path.cwd())) or {'ok':True})
    assert cli.main(['frob-g1-competition-rescue','--device','cuda'])=={'ok':True}
    args,cwd=captured[0]
    assert cwd==repo and Path.cwd()==nanojev
    assert args['layout_run']==str(layout.resolve())
    assert args['retention_run']==str(retention.resolve())
    assert args['output']==str((reports/'graph-g1-competition-rescue-0028').resolve())
    assert args['device']=='cuda'


def test_cli_restores_dir_after_cuda_exception(cli_setup,monkeypatch):
    _,nanojev,_,_,_=cli_setup
    monkeypatch.setattr(c,'run',lambda **kwargs:(_ for _ in ()).throw(RuntimeError('cuda failed')))
    with pytest.raises(RuntimeError,match='cuda failed'):
        cli.main(['frob-g1-competition-rescue'])
    assert Path.cwd()==nanojev


def test_cli_fails_before_cuda_without_required_0027_report(cli_setup,monkeypatch):
    _,nanojev,_,_,retention=cli_setup
    (retention/'filter_retention_report.json').unlink()
    monkeypatch.setattr(c,'run',lambda **kw:pytest.fail('must not load CUDA'))
    with pytest.raises(FileNotFoundError,match='REFERENCE_MISSING'):
        cli.main(['frob-g1-competition-rescue'])
    assert Path.cwd()==nanojev


def test_0027_lineage_rejects_wrong_loss_count(tmp_path):
    folder=tmp_path/'old';folder.mkdir()
    report={'schema':c.r.SCHEMA,'status':'READ_ONLY_G1_0027_RETENTION_AND_LOSS_AUDIT',
            'filter_0026_sha256':'correct','source_adapter_sha256':'adapter',
            'checkpoint_file_sha256':{'head':'abc'},'frozen_0026_replay_exact':True,
            'original_retention_pass':True,'losses_replayed':12}
    (folder/c.r.REPORT_NAME).write_text(json.dumps(report))
    (folder/c.r.CASES_NAME).write_text('')
    (folder/c.r.LOSSES_NAME).write_text('')
    info={'adapter_sha':'adapter','checkpoint_files':{c.WINNER_HEAD:{'head':'abc'}}}
    with pytest.raises(ValueError,match='REFERENCE_DRIFT:losses'):
        c.load_retention(folder,info,'correct')


def test_mocked_complete_runner_lineage_promotion_gates_and_immutable_output(tmp_path,monkeypatch):
    """Full five-group orchestration is mocked at CUDA boundary, not training."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    def make_rows(group,n,shifted=False):
        return [{'prompt':f'{group}-{i:04d}',
            'split':'ood_dev' if shifted else 'dev',
            'family':'encounter' if i%2==0 else 'allocation',
            'surface_shift':'novel' if shifted else 'original',
            'object_spans':[{'start':0,'end':1,'kind':'action'}]}
            for i in range(n)]
    sources=make_rows('source',320)
    validation=make_rows('validation',320)
    known=make_rows('known',320,True)
    new=make_rows('previous-new',160,True)
    novel=make_rows('independent',320,True)
    cp=tmp_path/'checkpoint';cp.mkdir()
    (cp/'manifest.json').write_text('manifest')
    (cp/cli.HEAD_FILE).write_text('head')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    info={'entries':{c.WINNER_HEAD:{'checkpoint':cp,'manifest':{
        'source_adapter':'adapter','model_id':'model','head_sha256':'model-head'}}},
        'adapter_sha':'adapter-hash','validation_rows':validation,
        'checkpoint_files':{c.WINNER_HEAD:{
            'manifest_sha256':sha(cp/'manifest.json'),
            'head_file_sha256':sha(cp/cli.HEAD_FILE)}}}
    def mocked_group(name,rows,*args,**kwargs):
        missing=13 if name=='new_layout_replay' else 0
        base={'f1':.9,'exact_prompts':len(rows)-missing,
              'missing_objects':missing,'false_positive_objects':0}
        improved={'f1':.92,'exact_prompts':len(rows),
                  'missing_objects':0,'false_positive_objects':0}
        if not missing:improved=base.copy()
        g={'metrics':{c.CONTROL:base,c.GUARD:base,
                      old.CHALLENGER:base,c.PRIMARY:improved,c.STRICT:improved},
           'paired_vs_filter':{
               c.PRIMARY:{'b_previously_correct_gold_lost':0},
               c.STRICT:{'b_previously_correct_gold_lost':0}}}
        items=[{'group':name,'index':i,'configurations':{}}
               for i in range(1,len(rows)+1)]
        recovered=[{'group':name,'index':i,'layout':'novel','kind':'action',
                    'gold_span':[0,1], 'recovered':{c.PRIMARY:True,c.STRICT:True}}
                   for i in range(1,missing+1)]
        return g,items,recovered
    results={name:mocked_group(name,rows)[0] for name,rows in (
        ('original_paired_sources',sources),('original_independent_0014',validation),
        ('known_layout_replay',known),('new_layout_replay',new))}
    monkeypatch.setattr(c,'_load_prior_inputs',lambda *a:info)
    monkeypatch.setattr(c,'_load_0022',lambda *a:({},known,[],{},{}))
    monkeypatch.setattr(c.r,'load_0026',lambda *a:({},[],new,{},
                                                 {'layout_filter_report.json':'f26'}))
    fake_losses=[{'group':'new_layout_replay','index':i,'kind':'action','gold_span':[0,1]}
                 for i in range(1,14)]
    monkeypatch.setattr(c,'load_retention',lambda *a:({'groups':results}, {},fake_losses,{},{}))
    monkeypatch.setattr(c,'restore_source',lambda row:(sources[int(row['prompt'][-4:])],{}))
    monkeypatch.setattr(c,'verify_author_gold',lambda *a:None)
    monkeypatch.setattr(c,'independent_rows',lambda *a:novel)
    monkeypatch.setattr(c,'evaluate_group',mocked_group)
    monkeypatch.setattr(c,'_verify_no_drift',lambda *a:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *a:'adapter-hash')
    monkeypatch.setattr(backbone,'model_components',lambda *a:(object(),object()))
    monkeypatch.setattr(cli,'_load_checkpoint',lambda *a,**kw:(
        type('Head',(),{'eval':lambda self:self})(),
        {'stage':'G1','head_sha256':'model-head'}))
    out=tmp_path/'report'
    kw={'root':'reports','transfer_report':'transfer','fresh_run':'fresh',
        'null_run':'null','validation_run':'valid','ood_run':'ood',
        'layout_run':'layout','retention_run':'retention','output':out,'device':'cpu'}
    report=c.run(**kw)
    assert report['prior_losses_replayed']==13
    assert report['prior_losses_recovered'][c.PRIMARY]==13
    assert report['independent_validation_pass'] is True
    assert report['original_retention_pass'] is True
    assert report['primary_candidate_meets_all_development_gates'] is True
    assert report['no_training'] and not report['production_promotion'] and not report['g2_unlocked']
    assert len((out/c.CASES).read_text().splitlines())==1440
    assert len((out/c.LOSSES).read_text().splitlines())==13
    assert len((out/c.FRESH_ROWS).read_text().splitlines())==320
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        c.run(**kw)
