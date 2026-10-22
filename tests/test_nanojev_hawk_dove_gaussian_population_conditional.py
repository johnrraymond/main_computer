"""CPU contract tests for prompt-conditioned 2000-bank Hawk–Dove density path."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import torch

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_population_conditional as model


class ConditionalPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    @staticmethod
    def fixture(tmp):
        path=Path(tmp)/'fixture.json'
        path.write_text(json.dumps({'schema':'nanojev.hawk-dove.million-counts.fixture.v1',
            'binding_version':'test-v1','demo_only':True,'encounter_total':1000000,
            'payoffs':{'HAWK_HAWK_PAYOFF':3,'HAWK_DOVE_PAYOFF':-1,
                       'DOVE_HAWK_PAYOFF':1,'DOVE_DOVE_PAYOFF':2}}))
        return path

    def test_numeric_visibility_for_every_tier_and_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,_=hd.read_fixture(self.fixture(tmp))
            for a in (0,200000,500000,600000,1000000):
                b=1000000-a
                x,y,_=hd.oracle(a,b,p)
                for tier in hd.TIERS:
                    for mask,full in hd.masks(tier,'path'):
                        prompt=hd.render(tier,a,b,p,full)
                        extracted=model.parse_visible(prompt,tier,hd.REP_LEGACY)
                        if tier=='T00':
                            self.assertIsNone(extracted['a'])
                            self.assertEqual((extracted['raw_x'],extracted['raw_y']),
                                             (float(x),float(y)))
                        else:
                            self.assertEqual((extracted['a'],extracted['b']), (a,b))
                        self.assertEqual(extracted['raw_x'] is not None,tier in ('T00','T01'))
                        self.assertEqual(extracted['raw_y'] is not None,tier in ('T00','T02'))
            for tier in hd.TIERS:
                prompt=hd.render(tier,200000,800000,p,(),hd.REP_SCIENCE)
                self.assertEqual(model.parse_visible(prompt,tier,hd.REP_SCIENCE)['tier'],tier)

    def test_prompt_inputs_ignore_unseen_teacher_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,_=hd.read_fixture(self.fixture(tmp))
            prompt=hd.render('T03',100000,900000,p)
            visible=model.parse_visible(prompt,'T03',hd.REP_LEGACY)
            f=model.make_features(visible,((-1500000.,3500000.),(0.,3000000.)))
            self.assertEqual(len(f),model.INPUT_DIM)
            self.assertEqual(f[:3],[.1,.9,1.])
            self.assertEqual(f[3:7],[0.,0.,0.,0.])
            bad=prompt.replace('100000','100001',1)
            with self.assertRaises(ValueError):model.parse_visible(bad,'T03',hd.REP_LEGACY)

    def test_tier_gate_is_joint_and_not_average(self):
        fake={'overall':{'within_tolerance':0.05},
              'by_tier':{tier:{'n':10,'independent_instances':5,
                'x_within_tolerance':0.9,'y_within_tolerance':0.9,
                'xy_within_tolerance':0.7} for tier in hd.TIERS}}
        verdict=model.tier_gate(fake,min_rate=0.8)
        self.assertFalse(verdict['all_tiers_pass'])
        self.assertEqual(verdict['min_tier_xy_rate'],0.7)
        fake['by_tier']['T00']['xy_within_tolerance']=0.8
        self.assertFalse(model.tier_gate(fake,min_rate=0.8)['all_tiers_pass'])
        for tier in hd.TIERS:fake['by_tier'][tier]['xy_within_tolerance']=0.8
        self.assertTrue(model.tier_gate(fake,min_rate=0.8)['all_tiers_pass'])

    def test_strict_relative_tolerance_near_zero(self):
        true=torch.tensor([[0.,100.],[100.,100.],[1.,100.]])
        pred=torch.tensor([[0.001,100.],[106.,100.],[1.04,100.]])
        hit=(pred-true).abs()<=(0.05*true.abs())
        self.assertEqual(hit.all(dim=-1).tolist(),[False,False,True])
        self.assertEqual(hit[:,1].tolist(),[True,True,True])

    def test_gate_selected_training_path_and_saved_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/'teacher.sqlite3'
            hd.build_database(db,self.fixture(tmp),instances=41,mode='path')
            opts=model.parser().parse_args(['train','--database',str(db),
                '--output',str(root/'selected'),'--device','cpu','--banks','80',
                '--grid-bins','64','--hidden','16','--batch-size','18',
                '--steps','20','--probe-every','10','--selection','tier-gate'])
            outcome=model.train(opts)
            self.assertEqual(outcome['checkpoint_selection'],'tier-gate')
            state=torch.load(root/'selected'/'best.pt',weights_only=True,map_location='cpu')
            self.assertEqual(len(state['best_gate_rank']),3)
            self.assertEqual(outcome['best_step'],int(state['step']))
            self.assertTrue(0 <= outcome['numeric_goal']['dev']['min_tier_xy_rate'] <= 1)

    def test_density_conservation_and_backprop(self):
        net=model.DensityNetwork(80,64,17,hidden=12)
        batch=torch.rand((3,model.INPUT_DIM))
        density=net(batch)
        self.assertEqual(tuple(density.shape),(3,2,64))
        self.assertTrue(bool((density>=0).all()))
        self.assertTrue(bool(torch.allclose(density.sum(-1),torch.ones((3,2)),atol=1e-5)))
        loss=density.square().sum();loss.backward()
        self.assertGreater(float(net.encoder[0].weight.grad.abs().sum()),0.)
        self.assertTrue(bool(torch.isfinite(net.encoder[-1].weight.grad).all()))

    def test_database_train_dev_select_then_test_and_predict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fixture=self.fixture(tmp)
            database=root/'teacher.sqlite3'
            hd.build_database(database,fixture,instances=41,mode='path')
            run=root/'run'
            args=model.parser().parse_args(['train','--database',str(database),
                '--output',str(run),'--device','cpu','--banks','80','--grid-bins','64',
                '--hidden','16','--batch-size','18','--steps','40',
                '--probe-every','20','--lr','0.01','--min-lr','0.0001'])
            report=model.train(args)
            self.assertEqual(report['status'],'COMPLETE')
            self.assertEqual(report['bank_count_per_output'],80)
            self.assertEqual(report['best_step'] in (0,20,40),True)
            self.assertTrue(report['independent_split_instances'])
            self.assertFalse(report['uses_test_for_training_or_promotion'])
            self.assertIn('numeric_goal',report)
            self.assertEqual(set(report['numeric_goal']['dev']['tiers']),set(hd.TIERS))
            self.assertLess(report['best_dev_tv'],1.0)
            self.assertTrue((run/'best.pt').exists())
            self.assertTrue((run/'latest.pt').exists())
            self.assertEqual(len(json.loads((run/'history.json').read_text())['records']),3)
            row=hd.read_rows(database,'test',tier='T08')[0]
            prompt_file=root/'prompt.txt';prompt_file.write_text(row['prompt'])
            pred_args=model.parser().parse_args(['predict','--checkpoint',str(run/'best.pt'),
                '--tier','T08','--prompt-file',str(prompt_file)])
            prediction=model.predict(pred_args)
            self.assertEqual(prediction['visible_inputs']['tier'],'T08')
            self.assertFalse(prediction['oracle_labels_read'])
            self.assertTrue(isinstance(prediction['predicted_X'],float))
            audit_args=model.parser().parse_args(['audit','--checkpoint',str(run/'best.pt'),
                '--database',str(database),'--device','cpu','--output',str(root/'gate.json'),
                '--splits','dev,test'])
            report_gate=model.audit(audit_args)
            self.assertIn(report_gate['status'],('PASS','FAIL'))
            self.assertEqual(report_gate['checkpoint_step'],report['best_step'])
            self.assertEqual(set(report_gate['splits']),{'dev','test'})
            self.assertEqual(set(report_gate['splits']['test']['gate']['tiers']),set(hd.TIERS))
            self.assertEqual(report_gate['splits']['test']['by_tier']['T08']['within_tolerance'],0.05)
            self.assertEqual(json.loads((root/'gate.json').read_text())['status'],report_gate['status'])
            with self.assertRaisesRegex(ValueError,'CONDITIONAL_AUDIT_DATABASE_SHA_MISMATCH'):
                changed=root/'copy.sqlite3';changed.write_bytes(database.read_bytes()+b'padding')
                audit_args.database=changed
                model.audit(audit_args)
            with self.assertRaisesRegex(ValueError,'CONDITIONAL_ALREADY_COMPLETE'):
                model.train(args)


if __name__=='__main__': unittest.main()
