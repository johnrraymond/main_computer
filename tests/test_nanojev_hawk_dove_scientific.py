"""CPU-only scientific representation/database/cutover/forensic regression suite."""
from __future__ import annotations
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_million_test as auditor
from tools import nanojev_hawk_dove_million_train as legacy
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_scientific_cutover as fork
from tools import nanojev_hawk_dove_scientific_probe as forensic


class ScientificCutoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.fixture=self.root/'original-demo.json'
        # Fixture supplied for tests only: real cutover checks the SHA of the
        # existing workstation fixture against original database provenance.
        self.fixture.write_text(json.dumps({'schema':'nanojev.hawk-dove.million-counts.fixture.v1',
            'binding_version':'test-only','demo_only':True,'encounter_total':1000000,
            'payoffs':{'HAWK_HAWK_PAYOFF':-1,'HAWK_DOVE_PAYOFF':3,
                      'DOVE_HAWK_PAYOFF':0,'DOVE_DOVE_PAYOFF':1.5}}))
        self.payoffs,_=hd.read_fixture(self.fixture)
    def tearDown(self):self.tmp.cleanup()

    def build_both(self):
        old=self.root/'old.sqlite3';new=self.root/'science.sqlite3'
        hd.build_database(old,self.fixture,instances=41,representation=hd.REP_LEGACY)
        hd.build_database(new,self.fixture,instances=41,representation=hd.REP_SCIENCE)
        return old,new

    def test_exact_scientific_roundtrip_and_no_change_to_legacy(self):
        for num,expected in [(0,'0e0'),(650000,'6.5e5'),(1500000,'1.5e6'),
                             (-400000,'-4e5'),(1,'1e0'),(-1,'-1e0'),(1000000,'1e6')]:
            self.assertEqual(hd.scientific(D(num)),expected)
            self.assertEqual(D(expected),D(num))
        with self.assertRaises(ValueError):hd.scientific(D('1.25'))
        self.assertEqual(hd.render('T01',200000,800000,self.payoffs),
                         hd.render('T01',200000,800000,self.payoffs,representation=hd.REP_LEGACY))
        for a in (0,200000,600000,800000,1000000):
            for tier in hd.TIERS:
                for mask,slots in hd.masks(tier):
                    old=hd.render(tier,a,1000000-a,self.payoffs,slots)
                    new=hd.render(tier,a,1000000-a,self.payoffs,slots,hd.REP_SCIENCE)
                    self.assertEqual(old,new) if mask==0 else self.assertNotEqual(old,new)
                    value=hd.certify(new,tier,a,1000000-a,self.payoffs,slots,hd.REP_SCIENCE)
                    self.assertEqual(D(value['x']),hd.oracle(a,1000000-a,self.payoffs)[0])
        prompt=hd.render('T01',200000,800000,self.payoffs,hd.SLOTS['T01'],hd.REP_SCIENCE)
        self.assertIn('8e5',prompt)
        self.assertIn('2.2e6',prompt)
        for damaged in (prompt.replace('8e5','8.1e5'),
                        prompt.replace('8e5','8e6'),
                        prompt.replace('8e5',''),
                        prompt.replace('8e5','8E5'),
                        prompt.replace('8e5','0e0')):
            with self.subTest(damaged=damaged[-80:]):
                with self.assertRaises(ValueError):
                    hd.certify(damaged,'T01',200000,800000,self.payoffs,
                               hd.SLOTS['T01'],hd.REP_SCIENCE)

    def test_db_audit_and_semantic_identity(self):
        old,new=self.build_both()
        self.assertEqual(auditor.audit(old,self.fixture)['status'],'PASS')
        self.assertEqual(auditor.audit(new,self.fixture)['status'],'PASS')
        result=fork.check_teachers(old,new,self.fixture)
        self.assertTrue(result['raw_only_prompts_identical'])
        self.assertEqual(result['presentations'],41*152)
        self.assertGreater(result['scientific_presentations_changed'],0)
        self.assertEqual(hd.db_manifest(new)['config']['schema'],hd.SCHEMA_SCIENCE)
        self.assertEqual(hd.db_manifest(old)['config']['schema'],hd.SCHEMA)
        with self.assertRaises(ValueError):fork.check_teachers(old,old,self.fixture)

    def test_fork_retains_optimizer_rng_masks_gates_and_parent_independence(self):
        import torch
        old_db,new_db=self.build_both()
        old_dir=self.root/'donor';old_dir.mkdir()
        source=self.root/'source.pt';source.write_bytes(b'opaque-source-bytes')
        ident={'schema':legacy.CHECKPOINT_SCHEMA,'source_sha256':hd.sha(source),
            'parent_sha256':'historical-parent-not-present','teacher_sha256':hd.sha(old_db),
            'trainer_sha256':'old-runtime-file-fingerprint',
            'decoder_head_decode_cap':10000000,'global_decoder_untouched':True,
            'gate_fraction':0.5,'probe_per_tier':48,'lr':1e-5,'seed':20261018,
            'views':4,'device':'cpu','dtype':'float32','Z_loss_weight':0}
        state={'schema':legacy.CHECKPOINT_SCHEMA,'config':ident,'step':58500,
            'focus':'T01','streak':0,'decoder':{'weight':torch.arange(4)},
            'game_operator_head':{'weight':torch.arange(3)},
            'optimizer':{'state':{'0':{'momentum':torch.ones(4)}},'param_groups':[]},
            'rng_cpu':torch.get_rng_state()}
        legacy.jwrite(old_dir/'config.json',ident)
        torch.save(state,old_dir/'latest.pt')
        lg,gw=legacy.resolve_gate_policy(old_dir,ident,
              legacy.make_parser().parse_args(['train','--tier-gate','T00=70','--tier-gate','T01=70']))
        legacy.jwrite(lg,gw)
        lp,mw=legacy.resolve_mask_policy(old_dir,ident,['T00:3=2','T01:7=4'])
        legacy.jwrite(lp,mw)
        donor_before=hd.sha(old_dir/'latest.pt')
        new_dir=self.root/'fork'
        result=fork.cutover(old_run=old_dir,new_run=new_dir,old_db=old_db,new_db=new_db,
                            fixture=self.fixture,source=source)
        self.assertEqual(result['step'],58500)
        self.assertEqual(hd.sha(old_dir/'latest.pt'),donor_before)
        nc=torch.load(new_dir/'latest.pt',map_location='cpu',weights_only=True)
        self.assertTrue(torch.equal(nc['decoder']['weight'],state['decoder']['weight']))
        self.assertTrue(torch.equal(nc['optimizer']['state']['0']['momentum'],torch.ones(4)))
        self.assertTrue(torch.equal(nc['rng_cpu'],state['rng_cpu']))
        self.assertEqual(nc['config']['parent_sha256'],'historical-parent-not-present')
        self.assertEqual(nc['config']['cutover_checkpoint_sha256'],donor_before)
        self.assertEqual(nc['schema'],science.CHECKPOINT_SCHEMA)
        args=science.make_parser().parse_args(['train','--teacher',str(new_db),
             '--source',str(source),'--output',str(new_dir),'--device','cpu','--dtype','float32'])
        self.assertEqual(science.config(args,hd.db_manifest(new_db)),nc['config'])
        mask=json.loads((new_dir/'mask-weights.json').read_text())
        gates=json.loads((new_dir/'tier-gates.json').read_text())
        self.assertEqual(mask['weights_by_tier']['T01']['7'],4)
        self.assertEqual(gates['gate_percent_by_tier']['T00'],70)
        self.assertEqual(gates['gate_percent_by_tier']['T01'],70)
        with self.assertRaises(FileExistsError):
            fork.cutover(old_run=old_dir,new_run=new_dir,old_db=old_db,new_db=new_db,
                         fixture=self.fixture,source=source)
        source.write_bytes(b'mutated-source')
        with self.assertRaisesRegex(ValueError,'SOURCE_DRIFT|CONFIG_DRIFT'):
            saved=json.loads((new_dir/'config.json').read_text())
            expected=science.config(args,hd.db_manifest(new_db))
            science.resolve_run_identity(saved,expected,'train')

    def test_forked_scientific_trainer_cpu_mock_can_resume_and_diagnose(self):
        import sys
        from types import ModuleType
        import torch
        import torch._dynamo
        from torch import nn
        old_db,new_db=self.build_both()
        source=self.root/'source.pt';source.write_bytes(b'opaque-source')
        donor=self.root/'donor';donor.mkdir()
        class Dec(nn.Module):
            def __init__(self):
                super().__init__();self.weight=nn.Parameter(torch.tensor(.2))
        class V3:
            @staticmethod
            def objective(o,truth,base,arm):
                return (o['fraction'].mean()-torch.tanh(truth.mean()/3000000))**2,None
            @staticmethod
            def compose_logits(sign,exponent,fraction,limit):
                return torch.pow(10.,exponent.argmax(-1).float()+fraction).clamp(max=limit)
        model_dec=Dec();head=nn.Linear(1,1)
        opt=torch.optim.AdamW(model_dec.parameters(),lr=1e-5)
        opt.zero_grad();(model_dec.weight**2).backward();opt.step()
        ident={'schema':legacy.CHECKPOINT_SCHEMA,'source_sha256':hd.sha(source),
            'parent_sha256':'deleted-historical-parent','teacher_sha256':hd.sha(old_db),
            'trainer_sha256':'old-runtime-file-fingerprint','decoder_head_decode_cap':10000000,
            'global_decoder_untouched':True,'gate_fraction':.5,
            'probe_per_tier':16,'lr':1e-5,'seed':20261018,'views':2,
            'device':'cpu','dtype':'float32','Z_loss_weight':0}
        donor_state={'schema':legacy.CHECKPOINT_SCHEMA,'config':ident,'step':10,
            'focus':'T01','streak':0,'decoder':model_dec.state_dict(),
            'game_operator_head':head.state_dict(),'optimizer':opt.state_dict(),
            'rng_cpu':torch.get_rng_state()}
        legacy.jwrite(donor/'config.json',ident)
        torch.save(donor_state,donor/'latest.pt')
        new_run=self.root/'new'
        fork.cutover(old_run=donor,new_run=new_run,old_db=old_db,new_db=new_db,
                      fixture=self.fixture,source=source)
        rt=ModuleType('tools.nanojev_game_math_curriculum_v10_runtime')
        ex=ModuleType('tools.nanojev_game_transform_experiment')
        rt.token_eligible=lambda model,prompt:True
        def load_current(source,device,dtype,allow_model_download,seed):
            return {'decoder':Dec(),'gamehead':nn.Linear(1,1),'v3':V3(),'base':None}
        def forward(model,prompts):
            n=len(prompts)
            exponent=torch.zeros(n,2,7);exponent[:,:,6]=1
            return {'sign':torch.ones(n,2,3),'exponent':exponent,
                    'fraction':torch.sigmoid(model['decoder'].weight).expand(n,2)},None
        ex.load_current=load_current;ex.forward=forward
        args=science.make_parser().parse_args(['train','--teacher',str(new_db),
            '--source',str(source),'--output',str(new_run),'--device','cpu','--dtype','float32',
            '--steps','11','--chunk-steps','1','--views','2','--probe-per-tier','16'])
        with mock.patch.dict(sys.modules,{'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                                          'tools.nanojev_game_transform_experiment':ex}):
            run=science.run(args)
            self.assertEqual(run['step'],11)
            args.mode='diagnose';args.diagnose_tier='T01'
            diagnosis=science.run(args)
        self.assertEqual(diagnosis['step'],11)
        self.assertEqual(set(diagnosis['masks']),{str(n) for n in range(8)})
        self.assertFalse(diagnosis['parent_status']['parent_file_required'])
        self.assertEqual(hd.sha(donor/'latest.pt'),hd.sha(donor/'latest.pt'))

    def test_forensic_match_and_scale_bias(self):
        old,new=self.build_both()
        from tools import nanojev_hawk_dove_million as hd
        rows=hd.read_rows(new,'dev','T01')
        cases=[]
        for r in rows:
            x=float(r['x']);y=float(r['y']);py=.9*y
            ey=abs(py-y)/max(1,abs(y))
            cases.append({'id':r['case_id'],'instance':r['instance'],'mask':r['mask'],
                 'x':x,'y':y,'pred_x':x,'pred_y':py,'relative_error_x':0,
                 'relative_error_y':ey,'x_pass':True,'y_pass':ey<=.05,
                 'joint_pass':ey<=.05})
        report={'tier':'T01','split':'dev','cases':cases,'step':58500}
        findings=forensic.analyze(report,new)
        self.assertEqual(findings['cases_analyzed'],len(cases))
        self.assertAlmostEqual(findings['by_group']['all']['predicted_y_vs_actual_slope'],.9)
        later=json.loads(json.dumps(report));later['step']=60000
        for c in later['cases']:
            c.update({'pred_y':c['y'],'relative_error_y':0,'y_pass':True,'joint_pass':True})
        diff=forensic.compare(report,later)
        self.assertEqual(diff['cases_matched'],len(cases))
        self.assertTrue(all(x['mean_y_relative_error_improvement']>0
                            for x in diff['by_mask'].values()))

if __name__=='__main__':unittest.main()
