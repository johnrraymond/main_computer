"""V32 bounded numerical authority and Captain contract acceptance (CPU/mocked Qwen)."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from main_computer import gamer_numerical_authority as auth
from main_computer.tactical_ai_service import TacticalAIService
from tools import nanojev_gamer_v1_v32_operational as op
from tools import nanojev_gamer_v1_v30_live_inference as live


def game(dc=160):
    return op.GAME_TEMPLATE.format(dc=dc)


def request(prompt=None):
    return {'schema': auth.REQUEST_SCHEMA,'requestId':'captain.test',
            'prompt': prompt or game()}


def active_config(root):
    root=Path(root)
    src=root/'original_wide.pt';src.write_bytes(b'original-wide-head-do-not-change')
    src2=root/'frozen_v30.pt';src2.write_bytes(b'v30-frozen')
    v31=root/'v31';v31.mkdir()
    for name in ('acceptance_report.json','live_inference_report.json',
                 'full_live_regression_cases.jsonl'):
        (v31/name).write_text(name)
    probe=root/'fresh-probe.json'
    probe.write_text(json.dumps({'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
         'total':3, 'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
         'production_modified':False, 'wide_head_untouched':True,
         'sources':{str(src.resolve()):auth.sha(src),str(src2.resolve()):auth.sha(src2)},
         'frozen_adapter_sha256':'a'*64,'selected_alpha':.25,
         'cases':[{'id':r['id'],'expected':r['expected'],
                   'difference':r['expected'],'exact':True,
                   'oracle_used_for_prediction':False} for r in op.PROBE]}))
    return {'schema':auth.SCHEMA,'authority':auth.V30,'scope':auth.SCOPE,
        'paths':{'run':str(root),'v27_run':str(root),'v29_run':str(root),
                 'v30_run':str(root),'v31_run':str(v31)},
        'source_files':{str(src.resolve()):auth.sha(src),str(src2.resolve()):auth.sha(src2)},
        'adapter_sha256':'a'*64,'selected_alpha':.25,
        'preactivation_probe':{'cases':3,'exact':3,'raw_report':str(probe),
                 'raw_sha256':auth.sha(probe)},
        'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
        'evidence':{'v31_report_sha256':auth.sha(v31/'acceptance_report.json'),
                    'v31_live_sha256':auth.sha(v31/'live_inference_report.json'),
                    'v31_deck_sha256':auth.sha(v31/'full_live_regression_cases.jsonl'),
                    'v31_case_count':624}}


def test_source_identity_normalization_retains_hash_and_rejects_aliases(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = Path('source.pt')
    src.write_bytes(b'frozen')
    relative = {'source.pt':auth.sha(src)}
    absolute = {str(src.resolve()):auth.sha(src)}
    assert auth.canonical_source_files(relative) == auth.canonical_source_files(absolute)
    tampered = {'source.pt':'0'*64}
    assert auth.canonical_source_files(tampered) != auth.canonical_source_files(absolute)
    with pytest.raises(ValueError, match='SOURCE_FILE_PATH_ALIAS_COLLISION'):
        auth.canonical_source_files({**relative, **absolute})


def test_certify_normalizes_relative_v31_sources_for_absolute_live_forward(monkeypatch, tmp_path):
    from tools import nanojev_gamer_v1_v30_live_inference as live
    monkeypatch.chdir(tmp_path)
    source = Path('model.pt')
    source.write_bytes(b'original-model-checkpoint')
    folder = Path('v31')
    folder.mkdir()
    (folder/'live_inference_report.json').write_text(json.dumps({
        'sources':{'model.pt':auth.sha(source)},
        'frozen_adapter_sha256':'a'*64,'selected_alpha':.25}))
    monkeypatch.setattr(auth,'_exact_deck_verified',lambda *a:{'v31_case_count':624})
    monkeypatch.setattr(live,'load',lambda _:{
        'source_files':{'model.pt':auth.sha(source)},
        'lineage':{'adapter_sha256':'a'*64}, 'alpha':.25})
    args=SimpleNamespace(run='.',input='.',v27_run='.',v29_run='.',v30_run='.',v31_run='v31')
    config=auth.certify(args)
    assert config['source_files']=={str(source.resolve()):auth.sha(source)}
    assert Path(config['paths']['v29_run']).is_absolute()


def test_fresh_triplet_accepts_same_fingerprints_after_path_canonicalization(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    from tools import nanojev_gamer_v1_v30_live_inference as live
    relpath=Path(config['source_files'].__iter__().__next__())
    # Relative identifiers in a live report must match by resolved file identity.
    monkeypatch.chdir(tmp_path)
    assert auth.canonical_source_files({relpath.name:config['source_files'][str(relpath)]}) == {
        str(relpath):config['source_files'][str(relpath)]}
    def fake_run(opts):
        rows=[json.loads(line) for line in Path(opts.v30_cases_jsonl).read_text().splitlines()]
        sources={str(Path(k).relative_to(tmp_path)):v for k,v in config['source_files'].items()}
        result={'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'total':3,'scored_cases':3,'exact_cases':3,
            'production_modified':False,'wide_head_untouched':True,
            'activation_source':config['activation_source'],
            'sources':sources, 'frozen_adapter_sha256':config['adapter_sha256'],
            'selected_alpha':.25,'all_scored_exact':True,
            'cases':[{'id':r['id'],'difference':r['expected'],
                'expected':r['expected'],'exact':True,'oracle_used_for_prediction':False,
                'calculation_authority':'exact_fraction_postprocess'} for r in rows]}
        Path(opts.v30_report).write_text(json.dumps(result))
        return result
    monkeypatch.setattr(live,'run',fake_run)
    outcome=op.sign_triplet(config,tmp_path/'probe-absolute',device='cpu')
    assert outcome['exact']==3


def test_default_authority_is_wide_and_no_state_created(tmp_path):
    assert auth.status(tmp_path)['authority']==auth.LEGACY
    with pytest.raises(ValueError, match='LEGACY_AUTHORITY_RETAINED'):
        auth.infer(tmp_path,request(),device='cpu')
    assert not auth.state_path(tmp_path).exists()


def test_atomic_activation_and_explicit_rollback_preserve_original_wide(tmp_path):
    config=active_config(tmp_path)
    src=tmp_path/'original_wide.pt'
    original=src.read_bytes()
    state=auth.activate(tmp_path,config)
    assert state['authority']==auth.V30
    assert src.read_bytes()==original
    with pytest.raises(ValueError,match='ALREADY_ACTIVE'):
        auth.activate(tmp_path,config)
    undone=auth.rollback(tmp_path)
    assert undone['authority']==auth.LEGACY
    assert src.read_bytes()==original
    assert auth.rollback(tmp_path)==undone


@pytest.mark.parametrize('bad',[
    {'schema':'game.captainDecisionRequest.v6','requestId':'x','prompt':game()},
    {'schema':auth.REQUEST_SCHEMA,'requestId':'x','prompt':game(),'expected':'0'},
    {'schema':auth.REQUEST_SCHEMA,'requestId':'x','prompt':game(),'answers':[]},
    {'schema':auth.REQUEST_SCHEMA,'requestId':'x','prompt':'Answer a helm vote: A or B.'},
    {'schema':auth.REQUEST_SCHEMA,'requestId':'','prompt':game()},
])
def test_action_requests_and_oracle_values_never_reach_decoder(monkeypatch,tmp_path,bad):
    monkeypatch.setattr(live,'run',lambda _:pytest.fail('must not call Qwen'))
    with pytest.raises(ValueError):
        TacticalAIService(tmp_path).evaluate_gamer_signed_difference(bad,device='cpu')


def test_active_source_mutation_refused(tmp_path,monkeypatch):
    config=active_config(tmp_path)
    auth.activate(tmp_path,config)
    (tmp_path/'frozen_v30.pt').write_bytes(b'changed')
    monkeypatch.setattr(live,'run',lambda _:pytest.fail('mutated source must not run'))
    with pytest.raises(ValueError,match='ACTIVE_SOURCE_CHANGED'):
        auth.infer(tmp_path,request(),device='cpu')


def test_live_acceptance_evidence_mutation_refused(tmp_path):
    config=active_config(tmp_path)
    auth.activate(tmp_path,config)
    (tmp_path/'v31'/'live_inference_report.json').write_text('changed')
    with pytest.raises(ValueError,match='ACTIVE_CERTIFICATION_MUTATED'):
        auth.infer(tmp_path,request(),device='cpu')


def test_captain_path_forwards_to_model_with_no_oracle_and_writes_receipt(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    auth.activate(tmp_path,config)
    before=(tmp_path/'original_wide.pt').read_bytes()
    def fake_run(args):
        assert args.stage=='v30-live'
        assert args.v30_expected is None
        assert args.v30_prompt==game()
        assert args.device=='cpu'
        Path(args.v30_report).parent.mkdir(parents=True,exist_ok=True)
        result={'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'total':1,'scored_cases':0,'production_modified':False,
            'wide_head_untouched':True,
            'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
            'sources':config['source_files'],
            'frozen_adapter_sha256':config['adapter_sha256'],
            'selected_alpha':.25,
            'cases':[{'id':'single','difference':'0','first_operand':'160',
               'second_operand':'160','sign':0,'zero_exact':True,
               'oracle_used_for_prediction':False,
               'calculation_authority':'exact_fraction_postprocess',
               'operand_authority':'frozen_v29_selector_and_v30_layer18_decoder'}]}
        Path(args.v30_report).write_text(json.dumps(result))
        return result
    monkeypatch.setattr(live,'run',fake_run)
    response=TacticalAIService(tmp_path).evaluate_gamer_signed_difference(request(),device='cpu')
    assert response['schema']==auth.RESPONSE_SCHEMA
    assert response['difference']=='0' and response['sign']==0
    assert response['captainOrder'] is None
    assert response['modelReceipt']['oracleUsedForPrediction'] is False
    assert Path(response['modelReceipt']['rawReport']).exists()
    assert response['modelReceipt']['rawSha256']==auth.sha(response['modelReceipt']['rawReport'])
    assert before==(tmp_path/'original_wide.pt').read_bytes()
    with pytest.raises(ValueError,match='CAPTAIN_RECEIPT_EXISTS'):
        TacticalAIService(tmp_path).evaluate_gamer_signed_difference(request(),device='cpu')


def test_wrong_live_result_never_returned_as_authoritative(monkeypatch,tmp_path):
    config=active_config(tmp_path);auth.activate(tmp_path,config)
    monkeypatch.setattr(live,'run',lambda _:{'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
                  'cases':[],'total':0,'scored_cases':0})
    with pytest.raises(ValueError,match='CAPTAIN_LIVE_SOURCE_OR_REPORT_INVALID'):
        auth.infer(tmp_path,request(),device='cpu')


def test_live_response_has_no_tactical_maneuver(monkeypatch,tmp_path):
    config=active_config(tmp_path);auth.activate(tmp_path,config)
    class FakeResponse:
        def __init__(self):self.data=None
    monkeypatch.setattr(live,'run',lambda _:pytest.fail('not asked to infer'))
    # A game.captainDecisionRequest.v6 must never be turned into a numerical vote.
    with pytest.raises(ValueError,match='CAPTAIN_REQUEST_SCHEMA_NOT_SUPPORTED'):
        auth.infer(tmp_path,{'schema':'game.captainDecisionRequest.v6','questions':[]},device='cpu')


def test_fresh_triplet_is_exact_reordered_and_never_trains(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    def fake_run(opts):
        assert opts.v30_cases_jsonl
        rows=[json.loads(l) for l in Path(opts.v30_cases_jsonl).read_text().splitlines()]
        assert rows==list(op.PROBE)
        result={'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'total':3,'scored_cases':3,'exact_cases':3,
            'production_modified':False,'wide_head_untouched':True,
            'activation_source':config['activation_source'],
            'sources':config['source_files'],
            'frozen_adapter_sha256':config['adapter_sha256'],
            'selected_alpha':.25,
            'all_scored_exact':True,
            'cases':[{'id':r['id'],'difference':r['expected'],
                'expected':r['expected'],'exact':True,'oracle_used_for_prediction':False,
                'calculation_authority':'exact_fraction_postprocess'} for r in rows]}
        Path(opts.v30_report).write_text(json.dumps(result))
        return result
    monkeypatch.setattr(live,'run',fake_run)
    r=op.sign_triplet(config,tmp_path/'triplet',device='cpu')
    assert r['exact']==3 and r['cases']==3
    assert r['raw_sha256']==auth.sha(r['raw_report'])


def test_failed_fresh_triplet_does_not_activate(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    monkeypatch.setattr(auth,'certify',lambda _:config)
    monkeypatch.setattr(op,'sign_triplet',lambda *a,**kw: (_ for _ in ()).throw(ValueError('ZERO_FAILED')))
    opts=SimpleNamespace(root=tmp_path,output=str(tmp_path/'out'),run='run',input='input',
        v27_run='v27',v29_run='v29',v30_run='v30',v31_run='v31',device='cpu')
    with pytest.raises(ValueError,match='ZERO_FAILED'):
        op.promote(opts)
    assert auth.status(tmp_path)['authority']==auth.LEGACY


def test_failed_captain_handoff_automatically_rolls_back(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    monkeypatch.setattr(auth,'certify',lambda _:config)
    monkeypatch.setattr(op,'sign_triplet',lambda *a,**kw:config['preactivation_probe'])
    monkeypatch.setattr(TacticalAIService,'evaluate_gamer_signed_difference',
        lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('CAPTAIN_FAILURE')))
    opts=SimpleNamespace(root=tmp_path,output=str(tmp_path/'out'),run='run',input='input',
        v27_run='v27',v29_run='v29',v30_run='v30',v31_run='v31',device='cpu')
    with pytest.raises(RuntimeError,match='CAPTAIN_FAILURE'):
        op.promote(opts)
    assert auth.status(tmp_path)['authority']==auth.LEGACY


def test_successful_bounded_cutover_does_not_claim_tactical_certification(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    monkeypatch.setattr(auth,'certify',lambda _:config)
    monkeypatch.setattr(op,'sign_triplet',lambda *a,**kw:config['preactivation_probe'])
    monkeypatch.setattr(TacticalAIService,'evaluate_gamer_signed_difference',
        lambda *a,**kw:{'difference':'0','sign':0,'captainOrder':None,
          'authority':auth.V30,'modelReceipt':{'oracleUsedForPrediction':False}})
    opts=SimpleNamespace(root=tmp_path,output=str(tmp_path/'out'),run='run',input='input',
        v27_run='v27',v29_run='v29',v30_run='v30',v31_run='v31',device='cpu')
    (tmp_path/'out').mkdir()
    result=op.promote(opts)
    assert result['status']=='PASS_BOUNDED_GAMER_NUMERICAL_CUTOVER'
    assert result['captain_tactical_v6_handoff'] is False
    assert auth.status(tmp_path)['authority']==auth.V30
    assert (tmp_path/'out'/'cutover_acceptance.json').is_file()


def test_cli_only_accepts_scoped_controls():
    args=op.parser().parse_args(['promote','--run','run','--input','input',
        '--v31-run','r31','--v27-run','r27','--v29-run','r29','--v30-run','r30',
        '--output','new','--device','cuda'])
    assert args.stage=='promote'
    assert op.parser().parse_args(['rollback']).stage=='rollback'
    with pytest.raises(SystemExit):
        op.parser().parse_args(['promote','--enable-all-captain-decisions'])


def test_v31_evidence_is_independently_recomputed_and_tampering_fails(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_v31_live_regression as battery
    deck=[]
    for group,n in battery.EXPECTED.items():
        for i in range(n):
            sign=(-1,0,1)[i % 3]
            deck.append({'id':f'{group}.{i}','prompt':game(),
                         'expected':str(sign),'scope_kind':group,
                         'surface': 'source_native' if i%2==0 else 'spine_difference',
                         'sign':sign,'heldout_train':False,'prompt_sha256':'0'*64})
    provenance={'fixture':'provenance'}
    monkeypatch.setattr(battery,'regenerate',lambda *a:(deck,provenance))
    live_result={'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
        'production_modified':False,'promotion_authorized':False,
        'wide_head_untouched':True,
        'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
        'selected_alpha':.25,'frozen_adapter_sha256':'a'*64,
        'sources':{'source':'digest'},
        'cases':[{'id':c['id'],'expected':c['expected'],
                  'difference':c['expected'],'exact':True,
                  'oracle_used_for_prediction':False,
                  'calculation_authority':'exact_fraction_postprocess'} for c in deck]}
    summary=battery.summarize(deck,live_result,provenance)
    folder=tmp_path/'v31';folder.mkdir()
    raw=folder/'live_inference_report.json'
    raw.write_text(json.dumps(live_result))
    summary['live_report_sha256']=auth.sha(raw)
    (folder/'acceptance_report.json').write_text(json.dumps(summary))
    (folder/'full_live_regression_cases.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in deck))
    verified=auth._exact_deck_verified(folder,'input','v27','v29','v30')
    assert verified['v31_case_count']==624
    live_result['cases'][0]['difference']='999'
    live_result['cases'][0]['exact']=False
    raw.write_text(json.dumps(live_result))
    # Even when the attacker updates the top-level evidence hash, independent
    # scoring detects the wrong answer instead of trusting a PASS label.
    summary['live_report_sha256']=auth.sha(raw)
    (folder/'acceptance_report.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError,match='V31_REPLAY_NOT_EXACT'):
        auth._exact_deck_verified(folder,'input','v27','v29','v30')


def test_preactivation_probe_tamper_refused_after_activation(tmp_path):
    config=active_config(tmp_path)
    auth.activate(tmp_path,config)
    Path(config['preactivation_probe']['raw_report']).write_text('changed')
    with pytest.raises(ValueError,match='ACTIVE_PREACTIVATION_PROBE_MISSING_OR_CHANGED'):
        auth.infer(tmp_path,request(),device='cpu')


def test_live_sign_must_match_exact_operands(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    auth.activate(tmp_path,config)
    def wrong_delta(opts):
        p=Path(opts.v30_report)
        p.parent.mkdir(parents=True,exist_ok=True)
        report={'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'total':1,'scored_cases':0,'production_modified':False,
            'wide_head_untouched':True,'activation_source':config['activation_source'],
            'sources':config['source_files'],'selected_alpha':.25,
            'frozen_adapter_sha256':config['adapter_sha256'],
            'cases':[{'first_operand':'160','second_operand':'160',
                      'difference':'1','sign':1,'zero_exact':False,
                      'oracle_used_for_prediction':False,
                      'calculation_authority':'exact_fraction_postprocess',
                      'operand_authority':'frozen_v29_selector_and_v30_layer18_decoder'}]}
        p.write_text(json.dumps(report))
        return report
    monkeypatch.setattr(live,'run',wrong_delta)
    with pytest.raises(ValueError,match='CAPTAIN_PREDICTION_INVALID'):
        auth.infer(tmp_path,request(),device='cpu')


def test_failure_to_commit_acceptance_report_rolls_back(monkeypatch,tmp_path):
    config=active_config(tmp_path)
    monkeypatch.setattr(auth,'certify',lambda _:config)
    monkeypatch.setattr(op,'sign_triplet',lambda *a,**kw:config['preactivation_probe'])
    monkeypatch.setattr(TacticalAIService,'evaluate_gamer_signed_difference',
        lambda *a,**kw:{'difference':'0','sign':0,'captainOrder':None,
            'authority':auth.V30,'modelReceipt':{'oracleUsedForPrediction':False}})
    original=auth.atomic_write
    def fail_report(path,payload):
        if Path(path).name=='cutover_acceptance.json':
            raise OSError('SIMULATED_REPORT_DISK_FAILURE')
        return original(path,payload)
    monkeypatch.setattr(auth,'atomic_write',fail_report)
    opts=SimpleNamespace(root=tmp_path,output=str(tmp_path/'out'),run='run',input='input',
        v27_run='v27',v29_run='v29',v30_run='v30',v31_run='v31',device='cpu')
    with pytest.raises(OSError,match='SIMULATED_REPORT_DISK_FAILURE'):
        op.promote(opts)
    assert auth.status(tmp_path)['authority']==auth.LEGACY
