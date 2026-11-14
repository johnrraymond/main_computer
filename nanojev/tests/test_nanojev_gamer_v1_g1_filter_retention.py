"""G1-0027 frozen original retention and exact 0026 loss replay tests."""
import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_filter_retention as f
from tools import nanojev_gamer_v1_g1_layout_filter as old
from tools import nanojev_gamer_v1_graph_curriculum as cli
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def test_checks_include_object_loss_even_if_f1_improves():
    result={'metrics':{f.GUARD:{'f1':.8,'exact_prompts':1,'missing_objects':7,
             'false_positive_objects':20},
             old.CHALLENGER:{'f1':.9,'exact_prompts':2,'missing_objects':6,
             'false_positive_objects':2}},
            'paired_vs_frozen_guard':{'b_previously_correct_gold_lost':1}}
    checks=f._retention_assessment(result)
    assert all(v for k,v in checks.items() if k!='no_previously_correct_gold_lost')
    assert checks['no_previously_correct_gold_lost'] is False


def test_causal_loss_record_reports_competitor_and_retained_gold(monkeypatch):
    row={'prompt':'tiger 24 moves','family':'encounter','surface_shift':'ledger_grid'}
    gold={(0,5,'actor')};new={(0,8,'action')}
    guesses={f.GUARD:gold,old.CHALLENGER:new}
    klass=SPAN_KINDS.index('actor')+1
    k2=SPAN_KINDS.index('action')+1
    by_end=[[] for _ in range(14)]
    by_end[5].append((0,4,klass,2.));by_end[8].append((0,7,k2,4.))
    prep={'prompt':row['prompt'],'offsets':[(i,i+1) for i in range(14)],'prompt_start':0}
    prediction={'span_logits':torch.zeros((14,14,len(SPAN_KINDS)+1)),
                'span_valid':torch.zeros((14,14),dtype=torch.bool)}
    monkeypatch.setattr(f,'_candidate_scores',lambda *args,**kwargs:by_end)
    losses=f.diagnose_losses(row,'known_layout_replay',1,gold,guesses,[],prep,prediction)
    assert len(losses)==1
    assert losses[0]['cause_class']=='new_selected_span_competes_with_gold'
    assert losses[0]['gold_surface']=='tiger'
    assert losses[0]['surviving_gold_margin']==2.
    assert losses[0]['newly_selected_overlapping_spans'][0]['surface']=='tiger 24'


def test_loss_diagnosis_refuses_to_call_rejected_gold_safe(monkeypatch):
    row={'prompt':'tiger','family':'encounter'}
    gold={(0,5,'actor')};guesses={f.GUARD:gold,old.CHALLENGER:set()}
    monkeypatch.setattr(f,'_candidate_scores',lambda *args,**kwargs:[[] for _ in range(6)])
    prep={'prompt':'tiger','offsets':[(i,i+1) for i in range(5)],'prompt_start':0}
    prediction={'span_logits':torch.zeros((5,5,len(SPAN_KINDS)+1)),
                'span_valid':torch.zeros((5,5),dtype=torch.bool)}
    with pytest.raises(ValueError,match='PREVIOUSLY_SELECTED_GOLD_CANDIDATE_REMOVED'):
        f.diagnose_losses(row,'new_layout_replay',1,gold,guesses,[],prep,prediction)


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
    monkeypatch.chdir(nanojev)
    return tmp_path,nanojev,reports,layout


def test_cli_one_command_resolves_0027_paths(cli_setup,monkeypatch):
    repo,nanojev,reports,layout=cli_setup
    captured=[]
    monkeypatch.setattr(f,'run',lambda **kw:captured.append((kw,Path.cwd())) or {'ok':True})
    assert cli.main(['frob-g1-filter-retention','--device','cuda'])=={'ok':True}
    opts,cwd=captured[0]
    assert cwd==repo and Path.cwd()==nanojev
    assert opts['layout_run']==str(layout.resolve())
    assert opts['output']==str((reports/'graph-g1-filter-retention-0027').resolve())
    assert opts['device']=='cuda'


def test_cli_restores_cwd_on_failure(cli_setup,monkeypatch):
    _,nanojev,_,_=cli_setup
    def fail(**kw):raise RuntimeError('CUDA failure')
    monkeypatch.setattr(f,'run',fail)
    with pytest.raises(RuntimeError,match='CUDA failure'):
        cli.main(['frob-g1-filter-retention'])
    assert Path.cwd()==nanojev


def test_cli_missing_0026_blocks_before_cuda(cli_setup,monkeypatch):
    _,nanojev,_,layout=cli_setup
    (layout/'layout_filter_report.json').unlink()
    monkeypatch.setattr(f,'run',lambda **kw:pytest.fail('must not run'))
    with pytest.raises(FileNotFoundError,match='LAYOUT_FILTER_REPORT_MISSING'):
        cli.main(['frob-g1-filter-retention'])
    assert Path.cwd()==nanojev


def test_0026_references_require_thirteen_losses(tmp_path,monkeypatch):
    path=tmp_path/'0026';path.mkdir()
    new=[{'prompt':'x'} for _ in range(old.NOVEL_CASES)]
    (path/'layout_filter_new_layouts.jsonl').write_bytes(old._jsonl_bytes(new))
    cases=[{} for _ in range(f.EXPECTED_KNOWN+f.EXPECTED_NEW)]
    (path/'layout_filter_per_case.jsonl').write_bytes(old._jsonl_bytes(cases))
    report={'schema':old.SCHEMA,
        'status':'INFERENCE_ONLY_DEVELOPMENT_FILTER_NOT_CERTIFICATION',
        'known_layout_cases':320,'new_layout_cases':160,
        'new_layouts':list(old.NOVEL_LAYOUTS),'new_layout_seed':old.NOVEL_SEED,
        'ood_report_file_sha256':'ood-report','ood_dataset_file_sha256':'ood-dataset',
        'source_adapter_sha256':'adapter','checkpoint_file_sha256':{'sha':'head'},
        'new_layout_dataset_sha256':hashlib.sha256(old._jsonl_bytes(new)).hexdigest(),
        'configurations_predeclared':[f.CONTROL,f.GUARD,old.CHALLENGER],
        'rule_counts_all':{'gold_proposals_rejected':0,'gold_objects_lost_vs_guard':12}}
    (path/'layout_filter_report.json').write_text(json.dumps(report))
    info={'adapter_sha':'adapter','checkpoint_files':{f.WINNER_HEAD:{'sha':'head'}}}
    monkeypatch.setattr(old,'fresh_layout_rows',lambda *args:new)
    with pytest.raises(ValueError,match='REFERENCE_DRIFT:previously_correct_lost'):
        f.load_0026(path,info,{'structural_ood_report.json':'ood-report',
            'structural_ood_dataset.jsonl':'ood-dataset'},[])


def test_mocked_end_to_end_replays_all_and_diagnoses_13(tmp_path,monkeypatch):
    """Full 1120-row runner, immutable reports, lineage comparison, no training."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    def rows(prefix,count,shifted=False):
        return [{'prompt':f'{prefix}-{i:04d}','split':'ood_dev' if shifted else 'dev',
            'family':'encounter' if i%2==0 else 'allocation',
            **({'surface_shift':'evidence_first' if i%2==0 else 'ledger_grid'}
               if shifted and prefix=='known' else
               {'surface_shift':'markdown_note' if i%2==0 else 'key_value_index'}
               if shifted else {}),
            'object_spans':[{'start':0,'end':4,'kind':'action'}]}
            for i in range(count)]
    src=rows('source',320);val=rows('validation',320)
    known=rows('known',320,True);novel=rows('new',160,True)
    checkpoint=tmp_path/'cp';checkpoint.mkdir()
    (checkpoint/'manifest.json').write_text('manifest')
    (checkpoint/cli.HEAD_FILE).write_text('weights')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    info={'entries':{f.WINNER_HEAD:{'checkpoint':checkpoint,'manifest':{
           'source_adapter':'adapter','model_id':'model','head_sha256':'head'}}},
          'adapter_sha':'adapter-digest','validation_rows':val,
          'validation_report':{'metrics':{}},
          'validation_file_sha256':{'validation_report.json':'validation-sha'},
          'checkpoint_files':{f.WINNER_HEAD:{
              'manifest_sha256':sha(checkpoint/'manifest.json'),
              'head_file_sha256':sha(checkpoint/cli.HEAD_FILE)}}}
    gold={(0,4,'action')}
    def guessed(row):
        g={f.CONTROL:set(gold),f.GUARD:set(gold),old.CHALLENGER:set(gold)}
        if row['prompt'].startswith('new-') and int(row['prompt'].split('-')[1])<13:
            g[old.CHALLENGER]=set()
        return g
    def summary(group_rows,group_name):
        rec=[];stats={};per=[];by_layout={}
        for i,row in enumerate(group_rows,1):
            g=guessed(row);c=f._counters(g,gold,[],1)
            rec.append((g,gold))
            if not stats:stats=Counter()
            stats.update(c)
            layout=row['surface_shift']
            if layout not in by_layout:by_layout[layout]=([],[],Counter())
            by_layout[layout][0].append((g,gold))
            by_layout[layout][1].append(row)
            by_layout[layout][2].update(c)
            per.append({'group':group_name,'index':i,'layout':layout,
                        'family':row['family'],
                        'prompt_sha256':f._sha_string(row['prompt']),
                        'gold_count':1,'rejections':0,
                        'rejected_gold_proposals':0,'configurations':f._case_summary(g,gold)})
        res=old._assess(rec,group_rows,stats)
        res['by_layout']={k:old._assess(*v) for k,v in sorted(by_layout.items())}
        return res,per
    from collections import Counter
    known_res,known_cases=summary(known,'known_layout')
    new_res,new_cases=summary(novel,'new_layout')
    source_metrics=old._summarize([(gold,gold) for _ in src],src)
    val_metrics=old._summarize([(gold,gold) for _ in val],val)
    info['validation_report']['metrics']['null_focused_4']=val_metrics
    report26={'groups':{'known_layout':known_res,'new_layout':new_res},
              'source_control_reference':source_metrics}
    monkeypatch.setattr(f,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(f,'_load_0022',lambda *args:({},known,[],{},{}))
    monkeypatch.setattr(f,'load_0026',lambda *args:(report26,
        known_cases+new_cases,novel,{}, {'layout_filter_report.json':'report-sha'}))
    monkeypatch.setattr(f,'restore_source',lambda row:(src[int(row['prompt'][-4:])],{}))
    monkeypatch.setattr(f,'verify_author_gold',lambda *args:320)
    monkeypatch.setattr(f,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-digest')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    monkeypatch.setattr(cli,'_load_checkpoint',lambda *args,**kwargs:(
        type('Head',(),{'eval':lambda self:self})(),{'stage':'G1','head_sha256':'head'}))
    monkeypatch.setattr(f,'_capture',lambda head,model,tok,backbone,row,device,max_length:
        (guessed(row),[],1,{},{}))
    monkeypatch.setattr(f,'diagnose_losses',lambda row,group,index,g,guesses,rejected,prep,pred:
        [{'group':group,'index':index,'layout':row.get('surface_shift','original'),
          'cause_class':'new_selected_span_competes_with_gold'}]
        if len((guesses[f.GUARD]&g)-guesses[old.CHALLENGER]) else [])
    out=tmp_path/'results'
    report=f.run(root='root',transfer_report='transfer',fresh_run='fresh',null_run='null',
                 validation_run='valid',ood_run='ood',layout_run='layout',
                 output=out,device='cpu')
    assert report['frozen_0026_replay_exact']
    assert report['original_retention_pass'] is True
    assert report['losses_replayed']==13
    assert report['losses_by_layout']=={'key_value_index':6,'markdown_note':7}
    assert len((out/f.CASES_NAME).read_text().splitlines())==1120
    assert len((out/f.LOSSES_NAME).read_text().splitlines())==13
    assert report['production_promotion'] is False and report['g2_unlocked'] is False
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        f.run(root='root',transfer_report='transfer',fresh_run='fresh',null_run='null',
              validation_run='valid',ood_run='ood',layout_run='layout',output=out,device='cpu')
