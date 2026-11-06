"""Exact prompt extraction as an isolated, read-only GAMER experiment."""
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_staircase as stair
from tools import nanojev_gamer_v1_prompt_fidelity as fidelity
from tools import nanojev_gamer_v1_flat_registers as flat
from tools import nanojev_game_transformation_library as native
from tools import nanojev_gamer_v1_catalog_extensions as ext
from tools import nanojev_gamer_v1_transition_games as transition


def _oracle(game,i):
    row=pairs.generate_case(game,i,47)
    doc=flat.build_oracle(row)
    return row,doc,flat._plan(doc)


def test_all_146_games_18_cases_extract_public_values_without_oracle_inputs():
    games=sorted(set(native.MATRICES)|set(native.SCALAR_MODELS)|
                 set(ext.EXTRA_IDS)|set(transition.TRANSITION_IDS))
    assert len(games)==146
    totals={'cases':0,'registers':0}
    for game in games:
        for index in range(18):
            row,doc,plan=_oracle(game,index)
            vals=fidelity.extract_registers(row['prompt_08'],game,plan)
            truth={r['id']:Fraction(r['value']['exact']) for r in doc['registers']}
            assert vals==truth,(game,index)
            ans=fidelity.execute_exact(plan,vals)
            assert math.isclose(float(ans),float(row['answer']),rel_tol=1e-9,abs_tol=1e-8)
            totals['cases']+=1;totals['registers']+=len(vals)
    assert totals['cases']==2628
    assert totals['registers']==10674


def test_exact_extraction_preserves_duplicate_numeric_values_and_register_identity():
    row,doc,plan=_oracle('captain_fuel_reserve',4)
    altered=row['prompt_08'].replace('fuel units', 'fuel units')
    r=fidelity.extract_registers(altered,'captain_fuel_reserve',plan)
    assert r['parameter:initial']==Fraction(str(row['source']['parameters']['initial']))
    assert set(r)==set(plan['required_registers'])
    with pytest.raises(ValueError,match='TEMPLATE_DOES_NOT_MATCH'):
        fidelity.extract_registers(row['prompt_08'].replace('protect a reserve','protect a mystery'),
                                   'captain_fuel_reserve',plan)
    with pytest.raises(ValueError,match='TEMPLATE_DOES_NOT_MATCH'):
        fidelity.extract_registers(row['prompt_08']+' hidden oracle answer 999',
                                   'captain_fuel_reserve',plan)
    # Extracted values do not come from the oracle document: mutate an oracle
    # *label* without changing the public prompt, and output must stay fixed.
    fake=dict(row);fake['source']={'parameters':{'initial':99999}}
    assert fidelity.extract_registers(row['prompt_08'],'captain_fuel_reserve',plan)==r


def test_fail_closed_on_ambiguous_or_malformed_matrix_and_routing():
    row,doc,plan=_oracle('prisoners_dilemma',4)
    prompt=row['prompt_08']
    assert fidelity.extract_registers(prompt,'prisoners_dilemma',plan)
    with pytest.raises(ValueError,match='MATRIX_CELL_COVERAGE'):
        fidelity.extract_registers(prompt.replace('If you choose','Were you to choose',1),
                                   'prisoners_dilemma',plan)
    with pytest.raises(ValueError,match='AMBIGUOUS_OPPONENT_CHOICE'):
        fidelity.extract_registers(prompt.replace('Your opponent chooses Cooperate.',
                                                   'Your opponent chooses Unknown.'),
                                   'prisoners_dilemma',plan)
    with pytest.raises(ValueError,match='TITLE_MISMATCH'):
        fidelity.extract_registers(prompt,'hawk_dove',plan)


def test_boundary_math_is_exact_without_zero_thresholding():
    _,_,plan=_oracle('captain_fuel_reserve',4)
    vals={'parameter:initial':Fraction('0.3'),
          'parameter:reserve_required':Fraction('0.1'),
          'parameter:fuel_spent':Fraction('0.2')}
    assert fidelity.execute_exact(plan,vals)==0
    vals['parameter:initial']=Fraction('0.3000000000000001')
    assert fidelity.execute_exact(plan,vals)==Fraction('0.0000000000000001')


def test_new_scalar_generation_fixes_integer_counts_and_exact_zero_without_legacy_drift():
    for game in pairs.ZERO_GAMES|set(fidelity.DISCRETE):
        for index in range(18):
            old=pairs.generate_case(game,index,47)
            assert pairs.certify(old)
            new=pairs.generate_case(game,index,47,prompt_fidelity_v2=True)
            assert new['generator_schema']==pairs.PROMPT_FIDELITY_SCALAR_SCHEMA
            assert pairs.certify(new)
            assert new['source_registry_sha256']!=old['source_registry_sha256']
            for name in fidelity.DISCRETE.get(game,()):
                assert Fraction(str(new['source']['parameters'][name])).denominator==1
            doc=flat.build_oracle(new)
            r=fidelity.extract_registers(new['prompt_08'],game,flat._plan(doc))
            assert all(Fraction(v['value']['exact'])==r[v['id']] for v in doc['registers'])
            if game in pairs.ZERO_GAMES and (index//6*2+index%6)%3==1:
                exact=fidelity.execute_exact(flat._plan(doc),r)
                assert exact==0,(game,index,exact)
    with pytest.raises(ValueError,match='ONLY_SOURCE_SCALARS'):
        pairs.generate_case('prisoners_dilemma',0,47,prompt_fidelity_v2=True)
    with pytest.raises(ValueError,match='ONLY_SOURCE_SCALARS'):
        pairs.generate_case('centipede',0,47,prompt_fidelity_v2=True)


def _fake_saved_run(tmp_path):
    names=('captain_fuel_reserve','prisoners_dilemma','centipede',
           'public_goods','rock_paper_scissors')
    rows=[pairs.generate_case(g,4,47) for g in names]
    base=tmp_path/'original';base.mkdir()
    corpus=base/'certified_pairs.jsonl'
    corpus.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    dataset_hash=hashlib.sha256(corpus.read_bytes()).hexdigest()
    flat_run=tmp_path/'flat';flat_run.mkdir()
    flat_docs=[flat.build_oracle(r) for r in rows]
    plans=flat._plans(flat_docs,flat_docs)
    predictions=[];regs=[];abs_errors=[]
    for r,d in zip(rows,flat_docs):
        actual={reg['id']:reg['value']['float'] for reg in d['registers']}
        guessed={reg_id:float(v)+.11 for reg_id,v in actual.items()}
        result,_=flat._eval_plan(plans[r['game_id']]['plan'],guessed)
        abs_errors.append(abs(result-float(r['answer'])))
        predictions.append({'case_sha256':r['case_sha256'],'game_id':r['game_id'],
            'predicted_game_id':r['game_id'],'target_zone':d['target_zone'],
            'target':float(r['answer']),'predicted_answer':result,
            'learned_route_error':None})
        for name in actual:
            regs.append({'case_sha256':r['case_sha256'],'game_id':r['game_id'],
                'register_id':name,'split':'dev','target':actual[name],
                'prediction':guessed[name]})
    (flat_run/'dev_predictions.jsonl').write_text(''.join(json.dumps(v)+'\n' for v in predictions))
    with (flat_run/'dev_register_predictions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['case_sha256','game_id','register_id','split',
                                        'target','prediction']);w.writeheader();w.writerows(regs)
    (flat_run/'train_formula_templates.json').write_text(json.dumps(plans))
    report={'schema':flat.SCHEMA,'status':'TRAINED_DIAGNOSTIC_NOT_PROMOTED',
        'output':str(flat_run.resolve()),'source_run':str(base),
        'source':{'dataset_sha256':dataset_hash,'frozen_adapter_sha256':'test-adapter'},
        'training':{'dev_cases':len(rows),'dev_register_labels':len(regs)},
        'dev':{'learned_route':{'metrics_on_valid_predictions':{
              'raw_mae':sum(abs_errors)/len(abs_errors)}}}}
    (flat_run/'final_report.json').write_text(json.dumps(report))
    return flat_run,base


def test_end_to_end_saved_ridge_comparison_and_readonly(tmp_path):
    run,original=_fake_saved_run(tmp_path)
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            list(run.iterdir())+list(original.iterdir())}
    result=stair.main(['flat-prompt','--run',str(run)])
    assert result['extracted_cases']==5
    assert result['extracted_registers']==result['dev_registers']
    assert result['outcomes']['exact_answer']['mae_on_valid']<1e-9
    assert result['outcomes']['ridge_answer']['mae_on_valid']>0
    assert result['outcomes']['hybrid_answer']['mae_on_valid']<1e-9
    assert (Path(result['source_flat_run']).parent/'flat-prompt-fidelity-0001'/'corpus_prompt_anomalies.csv').exists()
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==v for p,v in hashes.items())
    assert (Path(result['source_flat_run']).parent/'flat-prompt-fidelity-0001'/'final_report.json').is_file()
    with pytest.raises(ValueError,match='FLAGS_ONLY_FLAT_PROMPT'):
        stair.main(['flat-attribution','--run',str(run),'--prompt-output',str(tmp_path/'blocked')])
    with pytest.raises(ValueError,match='DOES_NOT_TRAIN'):
        stair.main(['flat-prompt','--run',str(run),'--steps','1'])


def test_source_tamper_and_dev_reference_are_detected(tmp_path):
    run,original=_fake_saved_run(tmp_path)
    path=original/'certified_pairs.jsonl'
    path.write_text(path.read_text().replace('prisoners_dilemma','altered_name',1))
    with pytest.raises(ValueError,match='DATASET_SHA256'):
        fidelity.run_prompt_fidelity(run)
