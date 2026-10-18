"""CPU-only acceptance tests for the V10 neural-lattice train/probe patch.

No transformers download, CUDA model, optimizer launch, or network needed.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
from unittest.mock import patch

from tools import nanojev_deterministic_brain_v10 as brain
from tools import nanojev_game_math_curriculum_v10_build as build
from tools import nanojev_game_math_curriculum_v10_runtime as rt
from tools import nanojev_game_math_curriculum_v10_probe as probe
from tools import nanojev_game_math_curriculum_v10_train as train
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_transformation_library as legacy


def fixture(path:Path):
    title=legacy.SCALAR_MODELS['robust_minimum'][0]
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE native_v6_cases(
            case_id TEXT PRIMARY KEY,instance_id TEXT,split TEXT,game_id TEXT,
            objective TEXT,t08_sha256 TEXT,status TEXT);
            CREATE TABLE native_v6_tiers(case_id TEXT,tier INTEGER,prompt TEXT);
            ''')
        for i,(split,x,y) in enumerate((('train',9,12),('dev',5,13),('test',7,17))):
            facts=native._facts('scalar','robust_minimum',title,
                  {'parameters':{'loss_case_1':x,'loss_case_2':y}},'scalar_value')
            text=native.render08(facts)
            db.execute('INSERT INTO native_v6_cases VALUES (?,?,?,?,?,?,?)',
                       (f'c{i}',f'instance-{i}',split,'robust_minimum','scalar_value',native.sha(text),'PASS'))
            db.execute('INSERT INTO native_v6_tiers VALUES (?,?,?)',(f'c{i}',8,text))


class V10NeuralContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory()
        root=Path(cls.temp.name)
        cls.source=root/'source.sqlite3'
        cls.teacher=root/'teacher.sqlite3'
        fixture(cls.source)
        build.make_teacher(cls.source,cls.teacher,seed=20261017)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_stages_are_topological_and_derived_from_teacher(self):
        info=rt.teacher_preflight(self.teacher)
        self.assertEqual(info['stages'],len(rt.STAGE_ORDER))
        self.assertEqual(info['splits'],{'train':1,'dev':1,'test':1})
        for tier in brain.TIERS:
            for parent, child, _ in brain.EDGES:
                self.assertLess(rt.STAGE_ORDER.index(tier+'/'+parent),
                                rt.STAGE_ORDER.index(tier+'/'+child))
            if tier in ('T00','T01'):
                for parent, child, _ in brain.bridge_edges(tier):
                    self.assertLess(rt.STAGE_ORDER.index(tier+'/'+parent),
                                    rt.STAGE_ORDER.index(tier+'/'+child))

    def test_read_cases_are_paired_and_nonleaking(self):
        rows=rt.read_cases(self.teacher,'dev',limit=3)
        self.assertEqual(len(rows),1)
        self.assertEqual(set(rows[0]['prompts']),set(rt.STAGE_ORDER))
        self.assertTrue(rows[0]['answer']['OP'])
        other=rt.read_cases(self.teacher,'train')
        self.assertNotEqual(rows[0]['instance'],other[0]['instance'])
        with self.assertRaises(ValueError):rt.read_cases(self.teacher,'unrecognized')

    def test_exact_oracle_probe_passes_all_stages(self):
        rows=rt.read_cases(self.teacher,'dev')
        lookup={prompt:(r['x'],r['y']) for r in rows for prompt in r['prompts'].values()}
        report=rt.evaluate(rows,list(rt.STAGE_ORDER),
                           lambda batch:[lookup[p] for p in batch],lambda _:True,
                           batch_size=7,min_count=1)
        self.assertEqual(report['stages_evaluated'],len(rt.STAGE_ORDER))
        self.assertTrue(report['all_stages_probed'])
        self.assertIsNone(report['first_failing'])
        self.assertEqual(len(report['paired_edges']),9*len(brain.EDGES)+sum(len(brain.bridge_edges(t)) for t in brain.TIERS))
        for score in report['scores'].values():
            self.assertEqual((score['x'],score['y'],score['joint_xy']),(1,1,1))

    def test_isolated_y_failure_is_detected_in_paired_edge(self):
        rows=rt.read_cases(self.teacher,'dev')
        row=rows[0]
        lookup={prompt:(row['x'],row['y']) for prompt in row['prompts'].values()}
        changed=row['prompts']['T02/logbar_y']
        def predictor(prompts):
            return [(x,y+100 if p==changed else y) for p in prompts
                    for x,y in [lookup[p]]]
        stages=rt.stage_list(('T02',))
        report=rt.evaluate(rows,stages,predictor,lambda _:True,min_count=1)
        self.assertEqual(report['first_failing_in_selection'],'T02/logbar_y')
        self.assertIsNone(report['first_failing'])  # partial report cannot assert global gate
        edge=report['paired_edges']['T02/full -> T02/logbar_y']
        self.assertEqual(edge['mutation'],'drop_y_logbar')
        self.assertEqual(edge['y_ok_regressed'],1)
        self.assertEqual(edge.get('x_ok_regressed',0),0)
        self.assertEqual(edge['joint_ok_regressed'],1)

    def test_token_overflow_is_counted_as_failure(self):
        rows=rt.read_cases(self.teacher,'dev')
        stage='T08/full'
        report=rt.evaluate(rows,[stage],lambda _: (_ for _ in ()).throw(AssertionError('should not infer')),
               lambda _:False,min_count=1)
        self.assertEqual(report['scores'][stage]['invalid'],{'unscorable_token_limit':1})
        self.assertEqual(report['scores'][stage]['joint_xy'],0)
        self.assertEqual(report['scores'][stage]['scorable'],0)
        self.assertFalse(rt.passed(report['scores'][stage],min_count=1))

    def test_two_probe_advancement_and_early_regression(self):
        focus='T02/logbar_x'
        a=train.decide_focus(focus,0,'T02/logbar_y')
        self.assertEqual(a,(focus,1,False))
        self.assertEqual(train.decide_focus(focus,a[1],'T02/logbar_y'),('T02/logbar_y',0,False))
        self.assertEqual(train.decide_focus('T03/full',1,'T00/raw'),('T00/raw',0,False))
        self.assertEqual(train.decide_focus(focus,1,focus),(focus,0,False))
        self.assertEqual(train.decide_focus('T08/raw',1,None),('T08/raw',0,True))

    def test_sampler_keeps_two_focus_tasks_and_side_branch(self):
        tasks=train.draw_tasks('T02/logbar_x',22,333,4)
        self.assertEqual(Counter(tasks)['T02/logbar_x'],2)
        self.assertEqual(Counter(tasks)['T02/logbar_y'],1)
        self.assertTrue(any(rt.STAGE_ORDER.index(t)<rt.STAGE_ORDER.index('T02/logbar_x') for t in tasks))
        self.assertEqual(tasks,train.draw_tasks('T02/logbar_x',22,333,4))

    def test_cli_metadata_check_does_not_load_gpu(self):
        with patch.object(rt,'load_model',side_effect=AssertionError('model loaded')):
            args=probe.make_parser().parse_args(['--teacher-db',str(self.teacher),'--teacher-info'])
            report=probe.run(args)
        self.assertFalse(report['model_loaded'])
        args=train.make_parser().parse_args(['--teacher-db',str(self.teacher),'--preflight-only'])
        self.assertFalse(train.run(args)['model_loaded'])

    def test_v9_checkpoint_can_be_imported_without_altering_parent(self):
        import torch
        with TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.pt';source.write_bytes(b'fake source')
            parent=root/'v9.pt'
            class FakeModel:
                def __init__(self):
                    self.decoder=torch.nn.Linear(1,2)
                    self.gamehead=torch.nn.Linear(1,1)
                def to_dict(self):
                    return {'decoder':self.decoder,'gamehead':self.gamehead}
            m=FakeModel()
            torch.save({'schema':rt.v9.SCHEMA,
                        'config':{'source_sha256':rt.source_hash(source)},
                        'step':245500,'decoder':m.decoder.state_dict(),
                        'game_operator_head':m.gamehead.state_dict()},parent)
            before=parent.read_bytes()
            with patch.object(rt.ex,'load_current',return_value=FakeModel().to_dict()):
                loaded,cp,sha=rt.load_model(source,parent,device='cpu',dtype='float32')
            self.assertEqual(cp['step'],245500)
            self.assertEqual(sha,rt.source_hash(parent))
            self.assertEqual(parent.read_bytes(),before)
            self.assertEqual(set(loaded),{'decoder','gamehead'})

    def test_cpu_training_resume_preserves_v9_parent_and_v10_optimizer(self):
        import torch
        with TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.pt';source.write_bytes(b'source')
            parent=root/'v9.pt';parent.write_bytes(b'parent')
            output=root/'v10'
            args=train.make_parser().parse_args([
                '--teacher-db',str(self.teacher),'--source-checkpoint',str(source),
                '--parent-checkpoint',str(parent),'--output',str(output),
                '--steps','2','--chunk-steps','1','--probe-limit','1',
                '--min-count','1','--device','cpu','--dtype','float32'])
            parent_hash=rt.source_hash(parent)
            def fake_load(*a,**k):
                torch.manual_seed(8)
                return ({'decoder':torch.nn.Linear(1,2),
                         'gamehead':torch.nn.Linear(1,1)},
                        {'step':245500},parent_hash)
            def fake_loss(model,prompt,x,y):
                prediction=model['decoder'](torch.ones(1,1))
                truth=torch.tensor([[x,y]],dtype=torch.float32)
                return ((prediction-truth)**2).mean()
            with patch.object(rt,'load_model',side_effect=fake_load), \
                 patch.object(rt,'batch_predict',side_effect=lambda m,p:[(0.,0.) for _ in p]), \
                 patch.object(rt,'token_eligible',return_value=True), \
                 patch.object(train,'objective',side_effect=fake_loss):
                first=train.run(args)
                self.assertEqual(first['v10_steps'],2)
                args.steps=3
                second=train.run(args)
                self.assertEqual(second['v10_steps'],3)
            cp=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
            self.assertEqual(cp['schema'],rt.SCHEMA)
            self.assertEqual(cp['step'],245503)
            self.assertEqual(cp['parent_step'],245500)
            self.assertEqual(cp['v10_steps'],3)
            self.assertIn('optimizer',cp)
            self.assertEqual(rt.source_hash(parent),parent_hash)
            self.assertTrue((output/'baseline-probe.json').exists())
            self.assertTrue((output/'probe-step-000245503.json').exists())

    def test_wrong_teacher_is_rejected_before_training(self):
        with TemporaryDirectory() as t:
            path=Path(t)/'broken.sqlite3'
            with sqlite3.connect(path) as db: pass
            with self.assertRaises(sqlite3.OperationalError):rt.teacher_preflight(path)


class V10AdjustableAdvancement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory()
        root=Path(cls.temp.name)
        source=root/'source.sqlite3'
        cls.teacher=root/'teacher.sqlite3'
        fixture(source)
        build.make_teacher(source,cls.teacher,seed=20261017)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cli_spellings_and_gate_validation(self):
        from tools import nanojev_game_math_curriculum_v10_policy as policy
        for spelling in ('--advancement-precent', '--advancement-percent'):
            args=train.make_parser().parse_args([spelling,'70'])
            self.assertAlmostEqual(policy.resolve_gate(args),.70)
            args=probe.make_parser().parse_args([spelling,'75'])
            self.assertAlmostEqual(policy.resolve_gate(args),.75)
        args=train.make_parser().parse_args([])
        self.assertAlmostEqual(policy.resolve_gate(args),.80)
        args=train.make_parser().parse_args(['--gate-fraction','.65'])
        self.assertAlmostEqual(policy.resolve_gate(args),.65)
        args=train.make_parser().parse_args(['--gate-fraction','.7','--advancement-percent','70'])
        self.assertAlmostEqual(policy.resolve_gate(args),.70)
        for raw in ('0','-10','110'):
            args=train.make_parser().parse_args(['--advancement-precent',raw])
            with self.assertRaisesRegex(ValueError,'OUT_OF_RANGE'):
                policy.resolve_gate(args)
        args=train.make_parser().parse_args(['--gate-fraction','.8','--advancement-percent','70'])
        with self.assertRaisesRegex(ValueError,'CONFLICTING'):
            policy.resolve_gate(args)

    def test_all_diagnostic_thresholds_are_independent_of_advancement(self):
        from tools import nanojev_game_math_curriculum_v10_policy as policy
        report={'gate_fraction':.65,'scores':{
            'T00/v9_anchor':{'x':.96,'y':.96,'joint_xy':.96,'n':48,'scorable':48},
            'T00/grammar_equals_x':{'x':.90,'y':.90,'joint_xy':.86,'n':48,'scorable':48},
            'T00/grammar_equals_y':{'x':.76,'y':.76,'joint_xy':.72,'n':48,'scorable':48},
        }}
        policy.attach_diagnostics(report,16)
        self.assertEqual(report['advancement_percent'],65)
        self.assertEqual({int(k):v['passing'] for k,v in report['diagnostic_gates'].items()},
                         {70:3,80:2,90:1,95:1})

    def test_legacy_checkpoint_config_allows_only_gate_and_known_code_migration(self):
        old={'schema':rt.SCHEMA,'teacher_sha256':'a','lr':1e-5,
             'gate_fraction':.8,'trainer_sha256':train.LEGACY_TRAINER_SHA256}
        new=dict(old,gate_fraction=.7,trainer_sha256='new-code-hash')
        self.assertTrue(train._check_resume_configuration(old,new))
        self.assertFalse(train._check_resume_configuration(dict(new),new))
        with self.assertRaisesRegex(RuntimeError,'CONFIG_DRIFT'):
            train._check_resume_configuration(old,dict(new,lr=2e-5))
        with self.assertRaisesRegex(RuntimeError,'UNRECOGNIZED_TRAINER'):
            train._check_resume_configuration(dict(old,trainer_sha256='unknown'),new)

    def test_resume_70_then_65_without_optimizer_reset(self):
        from tools import nanojev_game_math_curriculum_v10_policy as policy
        import torch
        with TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'source.pt';source.write_bytes(b'source')
            parent=root/'v9.pt';parent.write_bytes(b'parent')
            output=root/'v10';parent_hash=rt.source_hash(parent)
            args=train.make_parser().parse_args([
                '--teacher-db',str(self.teacher),
                '--source-checkpoint',str(source),
                '--parent-checkpoint',str(parent), '--output',str(output),
                '--steps','2','--chunk-steps','1','--probe-limit','1',
                '--min-count','1','--device','cpu','--dtype','float32'])
            def fake_load(*a,**k):
                torch.manual_seed(8)
                return ({'decoder':torch.nn.Linear(1,2),
                         'gamehead':torch.nn.Linear(1,1)},
                        {'step':245500},parent_hash)
            def fake_loss(model,prompt,x,y):
                prediction=model['decoder'](torch.ones(1,1))
                return ((prediction-torch.tensor([[x,y]],dtype=torch.float32))**2).mean()
            with patch.object(rt,'load_model',side_effect=fake_load), \
                 patch.object(rt,'batch_predict',side_effect=lambda m,p:[(0.,0.) for _ in p]), \
                 patch.object(rt,'token_eligible',return_value=True), \
                 patch.object(train,'objective',side_effect=fake_loss):
                train.run(args)
                cp_path=output/'latest.pt'
                before=torch.load(cp_path,map_location='cpu',weights_only=True)
                before_state=before['optimizer']['state']
                # Emulate a true 80% checkpoint made by the supplied old code.
                before['config']['trainer_sha256']=train.LEGACY_TRAINER_SHA256
                before['passes']=1
                torch.save(before,cp_path)
                oldcfg=json.loads((output/'v10-config.json').read_text())
                oldcfg['trainer_sha256']=train.LEGACY_TRAINER_SHA256
                rt.save_json(output/'v10-config.json',oldcfg)
                args.steps=3
                args.gate_fraction=None
                args.advancement_percent=70
                result=train.run(args)
                self.assertEqual(result['v10_steps'],3)
                changed=torch.load(cp_path,map_location='cpu',weights_only=True)
                self.assertAlmostEqual(changed['config']['gate_fraction'],.7)
                self.assertEqual(changed['step'],245503)
                self.assertEqual(changed['parent_step'],245500)
                self.assertEqual(changed['passes'],0)
                self.assertEqual(before_state.keys(),changed['optimizer']['state'].keys())
                self.assertEqual(rt.source_hash(parent),parent_hash)
                path=output/'gate-change-step-000245502-to-70-percent.json'
                self.assertTrue(path.exists())
                event=json.loads(path.read_text())
                self.assertEqual(event['old_advancement_percent'],80)
                self.assertEqual(event['advancement_percent'],70)
                self.assertEqual(event['consecutive_passes'],0)
                self.assertEqual(set(event['diagnostic_gates']),{'70','80','90','95'})
                args.steps=4
                args.gate_fraction=None
                args.advancement_percent=65
                result=train.run(args)
                self.assertEqual(result['v10_steps'],4)
                self.assertAlmostEqual(torch.load(cp_path,map_location='cpu',weights_only=True)
                                       ['config']['gate_fraction'],.65)
                self.assertTrue((output/'gate-change-step-000245503-to-65-percent.json').exists())
                self.assertEqual(json.loads((output/'v10-config.json').read_text())
                                 ['gate_fraction'],.65)
            self.assertEqual(parent.read_bytes(),b'parent')

    def test_probe_output_reports_dynamic_gate(self):
        summary={'cases':48,'stages_evaluated':1,'advancement_percent':70,
                 'scores':{'T00/v9_anchor':{'n':48,'x':.75,'y':.75,
                          'joint':.72,'coupled_Z':.9,'gate':'PASS'}},
                 'diagnostic_gates':{'70':{'passing':1,'total':1},
                                     '80':{'passing':0,'total':1},
                                     '90':{'passing':0,'total':1},
                                     '95':{'passing':0,'total':1}},
                 'first_failing_in_selection':None,'first_failing':None,
                 'all_stages_probed':False,'report':'test.json'}
        rendered=probe.format_console(summary)
        self.assertIn('>=70%',rendered)
        self.assertIn('80%=0/1',rendered)



if __name__=='__main__':unittest.main()
