"""G1-0026 frozen layout-boundary filter and CLI contract tests."""
import hashlib
import json
import random
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools import nanojev_gamer_v1_graph_curriculum as cli
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_g1_structural_ood import structural_ood_row, LAYOUTS
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


@pytest.mark.parametrize('generator',[encounter,allocation])
@pytest.mark.parametrize('layout',[*LAYOUTS,*f.NOVEL_LAYOUTS])
def test_grammar_excludes_scaffolding_without_gold_leakage(generator,layout):
    for seed in range(12):
        source=generator(random.Random(seed+26100),'dev',seed)
        shifted=(structural_ood_row(source,layout=layout) if layout in LAYOUTS
                 else f.novel_layout_row(source,layout))
        assert shifted['expected_decisions']==source['expected_decisions']
        assert shifted['split']=='ood_dev'
        assert any(f.exclusion_reason(shifted['prompt'],(a,b))=='scaffold_heading'
                   for a,b in f._heading_ranges(shifted['prompt']))
        # Assessment-only: the fixed heuristic was authored before consulting gold.
        for node in shifted['graph']['nodes']:
            if 'span' in node:
                a,b=node['span']
                assert shifted['prompt'][a:b]==source['prompt'][
                    next(n['span'][0] for n in source['graph']['nodes'] if n['id']==node['id']):
                    next(n['span'][1] for n in source['graph']['nodes'] if n['id']==node['id'])]
                assert f.exclusion_reason(shifted['prompt'],(a,b)) is None, (
                    layout,shifted['prompt'][a:b],f.exclusion_reason(shifted['prompt'],(a,b)))


def test_rejects_cross_lines_syntax_and_separators_but_not_content():
    p='DATA LEDGER\nVALUES ::\nMary collects 7 / 9 units.\n\n## Next phase\n* Ada arrives.'
    examples={'scaffold_heading':['DATA LEDGER','VALUES ::','Next phase'],
              'inline_separator':['7 / 9'],
              'crosses_line':['units.\n\n##'],
              'syntax_only':['.'],
              'bullet_prefix':['* Ada']}
    for reason,surfaces in examples.items():
        for surface in surfaces:
            a=p.index(surface);b=a+len(surface)
            assert f.exclusion_reason(p,(a,b))==reason, (reason,surface)
    for surface in ['Mary','collects','units','Ada','arrives']:
        a=p.index(surface)
        assert f.exclusion_reason(p,(a,a+len(surface))) is None


def test_filter_preserves_scores_and_keeps_content_candidates():
    prompt='## Header\nactual value\n'
    prep={'prompt':prompt,'offsets':[(i,i+1) for i in range(len(prompt))],
          'prompt_start':0}
    cls=SPAN_KINDS.index('outcome')+1
    by_end=[[] for _ in range(len(prompt)+1)]
    options=[(0,1,cls,2.5),(prompt.index('actual'),len('actual')-1,cls,3.5),
             (prompt.index('Header'),len('Header')-1,cls,9.0)]
    for a,offset,k,margin in options:
        by_end[a+offset+1].append((a,offset,k,margin))
    clean,removed,kept=f.filter_by_end(by_end,prep)
    assert kept==1
    assert len(removed)==2
    assert all(e[3]==3.5 for entry in clean for e in entry)
    assert len(by_end[prompt.index('Header')+len('Header')])==1
    assert all(e['reason']=='scaffold_heading' for e in removed)


def test_malformed_proposal_fails_closed():
    prep={'prompt':'Abc','offsets':[(0,1),(1,2),(2,3)],'prompt_start':0}
    with pytest.raises(ValueError,match='BAD_PROPOSAL'):
        f.filter_by_end([[],[(-1,1,1,1.)]],prep)


def test_no_0026_training_or_production_modifications(tmp_path,monkeypatch):
    checkpoint=tmp_path/'checkpoint';checkpoint.mkdir()
    (checkpoint/'manifest.json').write_bytes(b'manifest')
    (checkpoint/cli.HEAD_FILE).write_bytes(b'head')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    info={'entries':{'null_focused':{'checkpoint':checkpoint,'manifest':{
        'source_adapter':'mock_adapter','model_id':'mock_model','head_sha256':'head-sha'}}},
        'fresh_rows':[],'validation_rows':[], 'adapter_sha':'adapter-sha',
        'checkpoint_files':{'null_focused':{
           'manifest_sha256':sha(checkpoint/'manifest.json'),
           'head_file_sha256':sha(checkpoint/cli.HEAD_FILE)}}}
    rows=[{'prompt':'ab'+str(i),'split':'ood_dev',
           'family':('encounter' if i%2==0 else 'allocation'),
           'surface_shift':f.NOVEL_LAYOUTS[(i//2)%2],
           'object_spans':[{'start':0,'end':2,'kind':'action'}],
           'source_prompt_sha256':hashlib.sha256(('ab'+str(i)).encode()).hexdigest()}
          for i in range(320)]
    novel=[{'prompt':'ab fresh '+str(i),'split':'ood_dev',
            'family':('encounter' if i%2==0 else 'allocation'),
            'surface_shift':f.NOVEL_LAYOUTS[(i//2)%2],
            'object_spans':[{'start':0,'end':2,'kind':'action'}]}
           for i in range(f.NOVEL_CASES)]
    metric=f._summarize([({(0,2,'action')},{(0,2,'action')}) for _ in rows],rows)
    saved={'metrics':{f.CONTROL:metric,f.GUARD:metric}}
    cases=[{'configurations':{name:{'correct':1,'missing':0,'false_positives':0,'exact':True}
            for name in (f.CONTROL,f.GUARD)}} for _ in rows]
    monkeypatch.setattr(f,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(f,'_load_0022',lambda *args:(saved,rows,cases,{},
        {name:'mock-sha' for name in f.OOD_FILES}))
    monkeypatch.setattr(f,'load_diagnosis',lambda *args:({'source_control_metrics':metric},
                      checkpoint/'manifest.json',sha(checkpoint/'manifest.json')))
    monkeypatch.setattr(f,'restore_source',lambda row:({**row,'split':'dev'},{}))
    monkeypatch.setattr(f,'verify_author_gold',lambda *args:320)
    monkeypatch.setattr(f,'fresh_layout_rows',lambda *args:novel)
    monkeypatch.setattr(f,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    monkeypatch.setattr(backbone,'prepare_prompt',lambda tok,prompt,max_input_tokens:{
        'eligible':torch.ones(2,dtype=torch.bool),
        'offsets':[(0,1),(1,2)],'prompt_start':0,'prompt':prompt})
    monkeypatch.setattr(backbone,'frozen_context',lambda model,prepared,device:torch.zeros(2,4))
    action=SPAN_KINDS.index('action')+1
    class MockHead:
        def eval(self):return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((2,2,len(SPAN_KINDS)+1),-35.,dtype=torch.float32)
            logits[:,:,0]=6.;logits[0,1,action]=12.
            valid=torch.zeros(2,2,dtype=torch.bool);valid[0,1]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cli,'_load_checkpoint',lambda *args,**kwargs:(MockHead(),{
        'stage':'G1','head_sha256':'head-sha'}))
    out=tmp_path/'output'
    report=f.run(root='r',transfer_report='t',fresh_run='f',null_run='n',validation_run='v',
                 ood_run='ood',diagnosis_run='diag',output=out,device='cpu')
    assert len(json.loads((out/'layout_filter_report.json').read_text())['groups'])==2
    assert report['groups']['known_layout']['metrics'][f.CHALLENGER]['exact_prompts']==320
    assert report['groups']['new_layout']['metrics'][f.CHALLENGER]['exact_prompts']==160
    assert report['rule_counts_all']['gold_proposals_rejected']==0
    assert not report['promotion_authorized'] and not report['g2_unlocked']
    assert len((out/'layout_filter_per_case.jsonl').read_text().splitlines())==480
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        f.run(root='r',transfer_report='t',fresh_run='f',null_run='n',validation_run='v',
              ood_run='ood',diagnosis_run='diag',output=out,device='cpu')


@pytest.fixture
def cli_layout(tmp_path,monkeypatch):
    nanojev=tmp_path/'nanojev';tools=nanojev/'tools';tools.mkdir(parents=True)
    monkeypatch.setattr(cli,'__file__',str(tools/'nanojev_gamer_v1_graph_curriculum.py'))
    reports=tmp_path/'runtime'/'reports'/'gamer-v1';reports.mkdir(parents=True)
    transfer=reports/'graph-g1-decoder-transfer-0008'/'transfer_report.json'
    transfer.parent.mkdir();transfer.write_text('{}')
    for name in ('graph-g1-fresh-dev-0011','graph-g1-null-boundary-0012',
                 'graph-g1-calibration-validation-0014','graph-g1-structural-ood-0022'):
        (reports/name).mkdir()
    diag=reports/'graph-g1-structural-diagnosis-0023-retry';diag.mkdir()
    (diag/'structural_failure_report.json').write_text('{}')
    monkeypatch.chdir(nanojev)
    return tmp_path,nanojev,reports,diag


def test_normal_module_cli_with_only_py_variable(cli_layout,monkeypatch):
    repo,nanojev,reports,diag=cli_layout
    seen=[]
    monkeypatch.setattr(f,'run',lambda **kw:seen.append((kw,Path.cwd())) or {'ok':True})
    assert cli.main(['frob-g1-layout-filter','--device','cuda'])=={'ok':True}
    assert Path.cwd()==nanojev
    assert seen[0][1]==repo
    opts=seen[0][0]
    assert opts['diagnosis_run']==str(diag.resolve())
    assert opts['output']==str((reports/'graph-g1-layout-filter-0026').resolve())
    assert opts['device']=='cuda'


def test_cli_restores_directory_on_cuda_failure(cli_layout,monkeypatch):
    _,nanojev,_,_=cli_layout
    def fail(**kwargs):raise RuntimeError('CUDA failure')
    monkeypatch.setattr(f,'run',fail)
    with pytest.raises(RuntimeError,match='CUDA failure'):
        cli.main(['frob-g1-layout-filter'])
    assert Path.cwd()==nanojev


def test_cli_missing_diagnosis_fails_before_gpu(cli_layout,monkeypatch):
    _,nanojev,_,diag=cli_layout
    (diag/'structural_failure_report.json').unlink()
    monkeypatch.setattr(f,'run',lambda **kw:pytest.fail('should not run'))
    with pytest.raises(FileNotFoundError,match='DIAGNOSIS_REPORT_MISSING'):
        cli.main(['frob-g1-layout-filter'])
    assert Path.cwd()==nanojev
