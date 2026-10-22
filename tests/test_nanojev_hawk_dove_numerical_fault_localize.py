"""Numerical fault-localizer: mathematical, source-provenance, and live decoder tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_numerical_fault_localize as fault
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as internal
from tools import nanojev_hawk_dove_internal_curriculum_master as master
from tests.test_nanojev_hawk_dove_gaussian_register_fork import MiniDonor


class NumericalFaultTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(17)
        self.decoder=fork.GaussianRegisterDecoder(MiniDonor(),registers=64)
        self.decoder.eval().requires_grad_(False)
        self.head=internal.IntermediateBridge(35,kind='continuous')
        nn.init.normal_(self.head.feedback.weight,std=.01)
        self.head.eval().requires_grad_(False)

    def test_affine_oracle_is_recoverable_with_learned_linear_probe(self):
        y=torch.linspace(100000,1500000,200,dtype=torch.float64)
        x=y*(8/3)-1000000
        ys=torch.tensor([123456.,412500.,1020000.,1450000.],dtype=torch.float64)
        exact=ys*(8/3)-1000000
        probe=fault.ridge_fit(y[:,None],x,1e-10)
        predictions=fault.ridge_predict(probe,ys[:,None])
        self.assertLess(float((predictions-exact).abs().max()),.01)
        self.assertEqual(fault.metrics(predictions,exact)['correct'],4)
        # Not a nonlinear oracle, just train-only fitted coefficients.
        self.assertAlmostEqual(float(probe['coef'][0]/probe['std'][0]*1_000_000.),8/3,places=6)

    def test_bottleneck_localization_with_lost_features(self):
        y=torch.linspace(200000,800000,120,dtype=torch.float32)
        x=(8/3)*y-1000000
        train={'stages':{},'x':x[:100].double(),'y':y[:100].double()}
        dev={'stages':{},'x':x[100:].double(),'y':y[100:].double()}
        for name in fault.STAGES:
            features=y[:,None]/1000000 if name!='decoder_body_output' else torch.ones((120,1))
            train['stages'][name]=features[:100]
            dev['stages'][name]=features[100:]
        result=fault.ridge_diagnostics(train,dev,alpha=.00001)
        self.assertGreater(result['after_numeric_adapter']['x']['accuracy_5pct'],.9)
        self.assertLess(result['decoder_body_output']['x']['accuracy_5pct'],.5)
        self.assertEqual(result['exact_visible_y_affine_control']['x']['correct'],20)

    def test_valid_counterfactual_slopes_and_wrong_slope_detection(self):
        pairs=[({'tier':'T08','mask':0,'instance':1},
                {'tier':'T08','mask':0,'instance':2}) for _ in range(4)]
        orig={'x':torch.tensor([200000.,400000.,1000000.,-100000.],dtype=torch.float64),
              'y':torch.tensor([450000.,525000.,750000.,337500.],dtype=torch.float64),
              'pred_x':torch.tensor([200000.,400000.,1000000.,-100000.],dtype=torch.float64)}
        alt={'x':orig['x']+200000.,'y':orig['y']+75000.,'pred_x':orig['pred_x']+200000.}
        passed=fault.counterfactual_metrics(pairs,orig,alt)
        self.assertAlmostEqual(passed['oracle_slope_dX_dY'],8/3,places=6)
        self.assertEqual(passed['delta_x_5pct'],1.)
        wrong={**alt,'pred_x':orig['pred_x']+10000.}
        self.assertEqual(fault.counterfactual_metrics(pairs,orig,wrong)['delta_x_5pct'],0.)
        wrong['y']=orig['y']
        with self.assertRaisesRegex(ValueError,'ZERO_DELTA'):
            fault.counterfactual_metrics(pairs,orig,wrong)

    def test_capture_true_frozen_prebody_without_training(self):
        class NoCache:pass
        model={'decoder':self.decoder,'device':'cpu'}
        tokens=torch.randn(4,5,6)
        def synthetic_forward(model,prompts,channels,cache):
            n=len(prompts)
            h=torch.stack([torch.tensor([.1*j for j in range(7)]) for _ in prompts])
            atoms=torch.stack([torch.tensor(fork.adapter.encode(p),dtype=torch.float32) for p in prompts])
            # Input adapter dimension in MiniDonor is 4; use mock encoder below.
            h=h+self.decoder.numeric_adapter(atoms)+self.decoder.xyop_mask_adapter(torch.tensor(channels))
            return self.decoder(h,torch.ones(n,2),torch.zeros(n,dtype=torch.long),
                                tokens[:n],torch.ones(n,5,dtype=torch.bool))
        rows=[{'case_id':str(i),'prompt':'prompt '+str(i),'x':float(500000+i*1000),
               'y':750000.} for i in range(4)]
        with mock.patch.object(fork.rt,'token_eligible',return_value=True),\
             mock.patch.object(fork.adapter,'encode',side_effect=lambda p:(.1,.2,.3,.4)),\
             mock.patch.object(fork,'forward_masked',side_effect=synthetic_forward):
            actual=fault.capture(model,NoCache(),self.decoder,self.head,rows,'cpu',batch_size=4)
        self.assertEqual(actual['full_prebody'].shape,(4,2,35))
        self.assertEqual(set(actual['stages']),set(fault.STAGES))
        self.assertTrue(torch.isfinite(actual['pred_x']).all())
        self.assertTrue(all(p.grad is None for p in self.decoder.parameters()))
        self.assertTrue(all(p.grad is None for p in self.head.parameters()))
        grad=fault.gradient_audit(self.decoder,self.head,actual,4)
        self.assertEqual(grad['cases'],4)
        self.assertIn('x_magnitude_vs_gaussian_x',grad['pairwise'])
        self.assertLessEqual(grad['pairwise']['x_magnitude_vs_gaussian_x']['valid_cases'],4)
        self.assertTrue(grad['gaussian_x_is_hypothetical'])
        self.assertTrue(all(p.grad is None for p in self.decoder.parameters()))

    def test_checkpoint_provenance_for_both_experiment_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'fake frozen source');teacher.write_bytes(b'fake teacher')
            experiment=root/'internal';(experiment/'best_checkpoints').mkdir(parents=True)
            dsha='donor_sha'
            donor={'schema':fork.SCHEMA,'fork_step':68100}
            import io
            buffer=io.BytesIO();torch.save(donor,buffer);draw=buffer.getvalue()
            dsha=fault.sha(draw)
            (experiment/'donor_snapshot.pt').write_bytes(draw)
            identity={'donor_snapshot_sha256':dsha,'origin_fork_step':68100,
                      'source_sha256':fork.fingerprint(source),'teacher_sha256':fork.fingerprint(teacher),
                      'parent_registers':64,'parent_sigma_steps':2.,'registers':129,
                      'limit':.4,'sigma_bins':2.,'feedback_scale':.1}
            pt=experiment/'best_checkpoints/best_macro_continuous.pt'
            torch.save({'schema':internal.SCHEMA,'step':100,'identity':identity,
                        'heads':{'continuous':self.head.state_dict()}},pt)
            meta={'checkpoint_sha256':fork.fingerprint(pt),'step':100,'arm':'continuous'}
            pt.with_suffix('.json').write_text(json.dumps(meta))
            parent,donor,arm,ident=fault.parent_bundle(pt,source,teacher,'continuous')
            self.assertEqual(arm,'continuous')
            self.assertEqual(ident['checkpoint_step'],100)
            with self.assertRaisesRegex(ValueError,'ARM_REQUIRED'):
                fault.parent_bundle(pt,source,teacher,'continuous_control')
            meta['checkpoint_sha256']='bad';pt.with_suffix('.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'METADATA_MISMATCH'):
                fault.parent_bundle(pt,source,teacher,'continuous')
            pt.with_suffix('.json').unlink()
            experiment2=root/'master';(experiment2/'best_checkpoints').mkdir(parents=True)
            (experiment2/'donor_snapshot.pt').write_bytes(draw)
            path2=experiment2/'best_checkpoints/best_macro_continuous_control.pt'
            torch.save({'schema':master.SCHEMA,'step':200,
                       'identity':{'donor_identity':identity},
                       'heads':{'continuous_control':self.head.state_dict()}},path2)
            state,_,arm,_=fault.parent_bundle(path2,source,teacher,'continuous_control')
            self.assertEqual(arm,'continuous_control')
            self.assertEqual(state['step'],200)

    def test_complete_read_only_report_with_real_decoder_and_frozen_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'frozen-source');teacher.write_bytes(b'teacher')
            upstream=root/'experiment';(upstream/'best_checkpoints').mkdir(parents=True)
            import io
            data=io.BytesIO();torch.save({'schema':fork.SCHEMA,'fork_step':68100},data)
            (upstream/'donor_snapshot.pt').write_bytes(data.getvalue())
            ident={'donor_snapshot_sha256':fault.sha(data.getvalue()),'origin_fork_step':68100,
                   'source_sha256':fork.fingerprint(source),'teacher_sha256':fork.fingerprint(teacher),
                   'parent_registers':64,'parent_sigma_steps':2.,'registers':129,
                   'limit':.4,'sigma_bins':2.,'feedback_scale':.1}
            pt=upstream/'best_checkpoints/best_macro_continuous.pt'
            torch.save({'schema':internal.SCHEMA,'step':321,'identity':ident,
                        'heads':{'continuous':self.head.state_dict()}},pt)
            model={'device':'cpu','decoder':self.decoder,'config':{'max_prompt_tokens':100}}
            class NoCache:
                def __init__(self,*args,**kwargs):pass
            calls=[]
            def fake_rows(teacher,split,tier,n,seed):
                calls.append(split)
                start=100 if split=='dev' else 0
                return [{'instance':start+i,'case_id':f'{start+i}/{tier}/00',
                         'prompt':f'{tier}/{split}/{i}', 'tier':tier,'mask':0,
                         'x':float(-200000+i*250000),'y':float(300000+i*75000)}
                        for i in range(n)]
            def fake_forward(model,prompts,channels,cache):
                n=len(prompts)
                index=torch.tensor([int(p.rsplit('/',1)[1]) for p in prompts],dtype=torch.float32)
                h=torch.stack([torch.sin(index+j) for j in range(7)],dim=1)
                atoms=torch.tensor([fork.adapter.encode(p) for p in prompts],dtype=torch.float32)
                h=h+self.decoder.numeric_adapter(atoms)+self.decoder.xyop_mask_adapter(torch.tensor(channels))
                tokens=torch.sin(index[:,None,None]+torch.arange(30).reshape(1,5,6))
                return self.decoder(h,torch.zeros(n,2),torch.zeros(n,dtype=torch.long),
                                    tokens,torch.ones(n,5,dtype=torch.bool))
            def teacher_rows(teacher,split,tier):
                return fake_rows(teacher,split,tier,8,2)
            args=fault.parser().parse_args(['--checkpoint',str(pt),'--source',str(source),
                      '--teacher',str(teacher),'--output',str(root/'fault-result'),
                      '--device','cpu','--dtype','float32','--train-per-tier','8',
                      '--dev-per-tier','8','--counterfactual-limit','1',
                      '--grad-examples','2'])
            with mock.patch.object(fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fork,'build_for_resume',return_value=self.decoder),\
                 mock.patch.object(fork.cached,'FrozenCaptureCache',NoCache),\
                 mock.patch.object(fork.science,'rows_for_probe',side_effect=fake_rows),\
                 mock.patch.object(fork.hd,'read_rows',side_effect=teacher_rows),\
                 mock.patch.object(fork.rt,'token_eligible',return_value=True),\
                 mock.patch.object(fork.adapter,'encode',side_effect=lambda p:(.1,.2,.3,.4)),\
                 mock.patch.object(fork,'forward_masked',side_effect=fake_forward):
                report=fault.run(args)
            self.assertEqual(report['provenance']['checkpoint_step'],321)
            self.assertEqual(report['split_policy']['distinct_train_instances'],8)
            self.assertEqual(report['split_policy']['distinct_dev_instances'],8)
            self.assertNotIn('test',calls)
            self.assertEqual(len(report['readout_by_tier']),9)
            self.assertEqual(len(report['linear_probes']),len(fault.STAGES)+1)
            self.assertEqual(report['gradient_audit']['T00']['cases'],2)
            self.assertEqual(report['counterfactual_pairs']['T08']['pairs'],1)
            self.assertTrue((root/'fault-result/dev-cases.csv').exists())
            self.assertTrue((root/'fault-result/fault-localization.json').exists())
            self.assertEqual(fork.fingerprint(pt),report['provenance']['checkpoint_sha256'])
            with self.assertRaises(FileExistsError):fault.run(args)

    def test_output_cannot_overwrite_checkpoint_or_teacher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);teacher=root/'teacher.sqlite3';teacher.write_bytes(b'bad')
            cp=root/'internal/best_checkpoints/best.pt';cp.parent.mkdir(parents=True)
            cp.write_bytes(b'fake')
            for out in (teacher,root/'internal',cp.parent/'new',root):
                with self.assertRaisesRegex(ValueError,'OVERLAPS'):
                    fault.verify_no_overlap(out,[cp.parent,cp.parent.parent,teacher])
            self.assertEqual(fault.verify_no_overlap(root/'different'/'out',[cp.parent,teacher]),
                             (root/'different'/'out').resolve())

    def test_counterfactual_pairs_from_real_read_only_teacher(self):
        from tools import nanojev_hawk_dove_million as hd
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fixture=root/'fixture.json';database=root/'teacher.sqlite3'
            fixture.write_text(json.dumps({
                'schema':'nanojev.hawk-dove.million-counts.fixture.v1',
                'payoffs':{'HAWK_HAWK_PAYOFF':-1,'HAWK_DOVE_PAYOFF':3,
                           'DOVE_HAWK_PAYOFF':0,'DOVE_DOVE_PAYOFF':1.5},
                'encounter_total':1000000,'binding_version':'test-v1',
                'demo_only':True}))
            hd.build_database(database,fixture,instances=41)
            rows=fault.sample_rows(database,'dev',8,123,('T07','T08'))
            pairs=fault.eligible_pairs(database,rows,('T07','T08'))
            self.assertTrue(pairs['T07'] or pairs['T08'])
            for tier,group in pairs.items():
                for a,b in group:
                    self.assertEqual(a['tier'],b['tier'])
                    self.assertEqual(a['mask'],b['mask'])
                    self.assertNotEqual(a['instance'],b['instance'])
                    self.assertNotEqual(float(a['y']),float(b['y']))
                    # Known counterfactual relation is recovered from genuine
                    # independently certified teacher presentations.
                    dx=float(b['x'])-float(a['x'])
                    dy=float(b['y'])-float(a['y'])
                    self.assertAlmostEqual(dx/dy,8/3,places=6)

    def test_counterfactual_pair_selection_never_crosses_split_or_mask(self):
        rows=[{'instance':1,'case_id':'1/T08/00','mask':0,'tier':'T08','y':150000.,'x':0},
              {'instance':2,'case_id':'2/T08/00','mask':0,'tier':'T08','y':250000.,'x':100000.},
              {'instance':3,'case_id':'3/T08/01','mask':1,'tier':'T08','y':260000.,'x':100000.}]
        with mock.patch.object(fork.hd,'read_rows',return_value=rows):
            pairs=fault.eligible_pairs(Path('fake'),{'T08':rows[:1]},('T08',))
        self.assertEqual(len(pairs['T08']),1)
        a,b=pairs['T08'][0]
        self.assertEqual(b['case_id'],'2/T08/00')
        self.assertEqual(a['mask'],b['mask'])


if __name__=='__main__':unittest.main()
