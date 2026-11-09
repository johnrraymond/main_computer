"""V42 replay must be DEV-only, read-only, and honest about gold controls."""
import inspect

from tools import nanojev_gamer_v1_v41_surface_generalization as v41
from tools import nanojev_gamer_v1_v42_ood_diagnostic as v42


def _perfect():
    rows=v41.make_deck('dev')
    roles=[r['roles'] for r in rows]
    sides=[r['q_side'] for r in rows]
    actions=[r['label'] for r in rows]
    return rows,roles,sides,actions


def test_summary_groups_every_dev_style():
    rows,r,s,a=_perfect()
    out=v42.summarize(rows,r,s,a)
    assert out['overall']['structural_exact']==120
    assert out['overall']['decision_exact']==120
    assert {int(k) for k in out['by']['table_style']}=={8,9,10}
    assert {int(k) for k in out['by']['belief_style']}=={8,9,10}
    assert all(x['total']==40 for x in out['by']['table_style'].values())
    assert out['number_before_action_description']['total']==40
    assert out['number_after_action_description']['total']==80
    assert out['errors']==[]


def test_summary_reports_disjoint_semantic_and_numerical_failures():
    rows,r,s,a=_perfect()
    r[0]=[1,0,2,3]
    a[1]=1-a[1]
    s[2]=1-s[2]
    out=v42.summarize(rows,r,s,a,[x['label'] for x in rows])
    assert out['overall']['structural_exact']==118
    assert out['overall']['decision_exact']==119
    assert len(out['errors'])==3
    assert out['oracle_assisted_dev_numeric_diagnostic_only']['correct_with_gold_semantic_slots']==120


def test_no_heldout_generation_in_replay():
    source=inspect.getsource(v42.run)
    assert "make_deck('test'" not in source
    assert "make_deck('dev'" in source
    assert "manifest['dev_scores']" in source
    assert 'DEV_CHECKPOINT_REPLAY_MISMATCH' in source
    assert source.index('v40.predict_slots')<source.index('gold=v40.run_decisions')


def test_summary_uses_not_only_decision_accuracy():
    rows,r,s,a=_perfect()
    r[0]=[1,0,2,3]
    out=v42.summarize(rows,r,s,a)
    assert out['overall']['decision_exact']==120
    assert out['overall']['structural_exact']==119
    assert out['by']['table_style'][str(rows[0]['table_style'])]['structural_exact']==39


def test_run_replays_dev_only_with_mocked_inference(tmp_path,monkeypatch):
    import numpy as np
    from tools import nanojev_gamer_v1_v33_game_learning as v33
    from tools import nanojev_gamer_v1_v40_actor_grounding as v40
    rows,r,s,a=_perfect()
    base=tmp_path/'v41';base.mkdir()
    (base/'semantic_head.pt').write_bytes(b'mock-checkpoint')
    (base/'train_manifest.json').write_text('{}')
    manifest={'dev_pass':False,'seed':41,'dev_deck_sha256':v41.digest(rows),
              'dev_scores':v40.scores(rows,r,s,a),'lineage':{'model_id':'mock'},
              'v34_root':'unused'}
    monkeypatch.setattr(v41,'load',lambda *args:(manifest,object()))
    orig=v41.make_deck
    def safe_deck(split,seed=41):
        assert split=='dev', 'V42 must never open TEST'
        return orig(split,seed)
    monkeypatch.setattr(v41,'make_deck',safe_deck)
    monkeypatch.setattr(v33,'model_context',lambda *args:(__import__('torch'),object(),object()))
    monkeypatch.setattr(v40,'capture',lambda model,tok,t,prompts,**kwargs:(
        np.zeros((120,4,8),dtype=np.float32),np.zeros((120,8),dtype=np.float32)))
    monkeypatch.setattr(v40,'predict_slots',lambda model,x,q:(r,s))
    monkeypatch.setattr(v40,'_load_v34',lambda *args:({},object()))
    monkeypatch.setattr(v40,'run_decisions',lambda numeric,rows,roles,sides:
                        [row['label'] for row in rows])
    output=tmp_path/'v42'
    report=v42.run(base,output,device='cpu')
    assert report['status']=='DEV_ONLY_OOD_DIAGNOSTIC_NO_PROMOTION'
    assert report['holdout_accessed'] is False
    assert report['new_training_performed'] is False
    assert report['production_modified'] is False
    assert (output/'ood_dev_report.json').exists()
    assert not (base/'holdout_deck.json').exists()


def test_run_rejects_dev_score_mismatch(tmp_path,monkeypatch):
    import numpy as np
    from tools import nanojev_gamer_v1_v33_game_learning as v33
    from tools import nanojev_gamer_v1_v40_actor_grounding as v40
    rows,r,s,a=_perfect()
    base=tmp_path/'v41';base.mkdir()
    (base/'semantic_head.pt').write_bytes(b'mock-checkpoint')
    (base/'train_manifest.json').write_text('{}')
    manifest={'dev_pass':False,'seed':41,'dev_deck_sha256':v41.digest(rows),
              'dev_scores':{'structural_exact':1},'lineage':{'model_id':'mock'},'v34_root':'unused'}
    monkeypatch.setattr(v41,'load',lambda *args:(manifest,object()))
    monkeypatch.setattr(v33,'model_context',lambda *args:(__import__('torch'),object(),object()))
    monkeypatch.setattr(v40,'capture',lambda model,tok,t,prompts,**kwargs:(
        np.zeros((120,4,8),dtype=np.float32),np.zeros((120,8),dtype=np.float32)))
    monkeypatch.setattr(v40,'predict_slots',lambda model,x,q:(r,s))
    monkeypatch.setattr(v40,'_load_v34',lambda *args:({},object()))
    monkeypatch.setattr(v40,'run_decisions',lambda numeric,rows,roles,sides:
                        [row['label'] for row in rows])
    import pytest
    with pytest.raises(ValueError,match='GAMER_V42_DEV_CHECKPOINT_REPLAY_MISMATCH'):
        v42.run(base,tmp_path/'v42',device='cpu')
    assert not (tmp_path/'v42').exists()
