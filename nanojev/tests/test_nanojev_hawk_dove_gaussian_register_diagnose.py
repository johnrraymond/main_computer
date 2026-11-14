"""CPU-only regression contracts for the read-only Gaussian register diagnosis."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_register_diagnose as d
from tools import nanojev_hawk_dove_gaussian_register_fork as f


class RegisterDiagnosisTests(unittest.TestCase):
    def test_hidden_only_scoring_and_oracle_analysis(self):
        # X sign correct, log magnitude wrong; visible Y MUST NOT be graded.
        centers=torch.linspace(0,7,512)
        logits=torch.zeros((2,2,512))
        output={'bank_logits':logits,
                'sign':torch.tensor([[[0.,0.,3.],[0.,0.,3.]],[[0.,0.,3.],[0.,0.,3.]]]),
                'log_magnitude':torch.tensor([[5.,1.],[6.,1.]]),
                'xy':torch.tensor([[100000.,-900000.],[1000000.,-900000.]])}
        rows=d.sample_metrics(output,[('100000','300000'),('2000000','300000')],
            ['011','011'],[.5,.5],['case-one','case-two'],[3,7],centers,.03)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(x['slot']=='x' for x in rows))
        self.assertAlmostEqual(d.summarize_cases(rows)['within_5pct'],.5)
        self.assertAlmostEqual(d.summarize_cases(rows)['sign_accuracy'],1.)
        self.assertEqual(rows[1]['true_sign'],1)
        self.assertAlmostEqual(rows[0]['true_log_magnitude'],math.log10(100001),places=5)

    def test_oracle_sign_identifies_sign_only_failure(self):
        centers=torch.linspace(0,7,512)
        g=math.log10(1+12345.)
        output={'bank_logits':torch.zeros((1,2,512)),
                'sign':torch.tensor([[[5.,0.,0.],[0.,0.,5.]]]),
                'log_magnitude':torch.tensor([[g,1.]]),
                'xy':torch.tensor([[-12345.,10.]])}
        rows=d.sample_metrics(output,[(12345.,2222.)],['011'],[1.],['one'],[1],centers,.03)
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['sign_correct'])
        self.assertTrue(rows[0]['oracle_sign_5pct'])
        self.assertFalse(rows[0]['oracle_magnitude_5pct'])

    def test_magnitude_only_failure_identifies_oracle_magnitude(self):
        centers=torch.linspace(0,7,512)
        output={'bank_logits':torch.zeros((1,2,512)),
                'sign':torch.tensor([[[0.,0.,5.],[0.,0.,5.]]]),
                'log_magnitude':torch.tensor([[3.,1.]]),
                'xy':torch.tensor([[999.,10.]])}
        rows=d.sample_metrics(output,[(12345.,2222.)],['011'],[1.],['one'],[1],centers,.03)
        self.assertTrue(rows[0]['oracle_magnitude_5pct'])
        self.assertFalse(rows[0]['within_5pct'])

    def test_saved_history_transfer_and_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # Model improves on T02 when T02 is focus, T03 regresses.
            first={t:.5 for t in d.TIERS}; first['T02']=.35
            second={**first,'T02':.45,'T03':.4};second['T03']=.30
            third={**second,'T02':.35,'T03':.45}
            for step,scores,focus in ((300,first,'T02'),(600,second,'T02'),(900,third,'T03')):
                (root/f'probe-fork-step-{step:07d}.json').write_text(json.dumps({
                    'fork_step':step,'gate_percent':40,'first_failing':focus,
                    'scores':{t:{'joint_5pct':scores[t]} for t in d.TIERS},
                    'active_tiers':list(d.TIERS)}))
            report=d.read_history(root,40)
            self.assertEqual(report['n_saved_probes'],3)
            self.assertEqual(report['last_step'],900)
            self.assertEqual(report['recent_first_failing_counts']['T02'],2)
            transfer=report['by_inferred_training_focus']['T02']
            self.assertEqual(transfer['chunks'],2)
            self.assertEqual(transfer['focus_up_chunks'],1)
            self.assertEqual(transfer['focus_up_other_down_chunks'],1)
            self.assertAlmostEqual(transfer['transitions'][0]['deltas']['T02'],.1)
            self.assertAlmostEqual(transfer['transitions'][0]['deltas']['T03'],-.2)

    def test_history_only_does_not_load_checkpoint_or_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config.json').write_text(json.dumps({'schema':f.SCHEMA}))
            (root/'probe-fork-step-0000300.json').write_text(json.dumps({
                'fork_step':300,'gate_percent':40,'first_failing':'T02',
                'scores':{tier:{'joint_5pct':.2} for tier in d.TIERS}}))
            a=d.parser().parse_args(['--output',str(root),'--history-only'])
            with mock.patch.object(torch,'load',side_effect=AssertionError('READ_ONLY_MODE_LOADED_CK')):
                report=d.run(a)
            self.assertTrue(report['checkpoint_unchanged'])
            self.assertTrue((root/'diagnostics/gaussian-register-diagnosis-step-0000300.json').is_file())
            self.assertFalse((root/'latest.pt').exists())

    def test_gradient_norm_does_not_mutate_params(self):
        p=torch.nn.Parameter(torch.tensor([2.]))
        before=p.detach().clone()
        norm=d.grad_norm((p**2).sum(),(p,))
        self.assertAlmostEqual(norm,4.)
        self.assertTrue(torch.equal(p.detach(),before))
        self.assertIsNone(p.grad)

    def test_validate_tiers(self):
        self.assertEqual(d.parse_tiers('T02,T00'),('T00','T02'))
        with self.assertRaisesRegex(ValueError,'REGISTER_DIAG_INVALID_TIERS'):
            d.parse_tiers('T02,T02')
        with self.assertRaisesRegex(ValueError,'REGISTER_DIAG_INVALID_TIERS'):
            d.parse_tiers('T99')

    def test_live_scoring_matches_native_gate_without_mutating_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'base.pt';teacher=root/'teacher.sqlite3'
            src.write_bytes(b'model');teacher.write_bytes(b'teacher')
            identity={'schema':f.SCHEMA,'source':str(src),'teacher':str(teacher),
                      'source_sha256':f.fingerprint(src),'teacher_sha256':f.fingerprint(teacher),
                      'mix':['*:*=011:1'],'seed':1,'registers':16,
                      'sigma_steps':2.,'probe_per_tier':8,'gaussian_weight':.1}
            (root/'config.json').write_text(json.dumps(identity))
            checkpoint={'schema':f.SCHEMA,'identity':identity,'fork_step':300}
            (root/'latest.pt').write_bytes(b'not touched by live()')
            saved_before=(root/'latest.pt').read_bytes()
            class Head(torch.nn.Module):
                def __init__(self):
                    super().__init__()
                    self.register_buffer('centers',torch.linspace(0,7,16))
                    self.sigma=.05
            model={'config':{'max_prompt_tokens':40},'device':'cpu','decoder':Head()}
            rows=[{'prompt':'test prompt','mask':1,'x':'100000','y':'200000',
                   'case_id':f'case-{i}'} for i in range(8)]
            def forward(m,prompts,channels,cache):
                b=len(prompts)
                return {'bank_logits':torch.zeros(b,2,16),
                        'sign':torch.tensor([[[0.,0.,10.],[0.,0.,10.]]]*b,dtype=torch.float32),
                        'log_magnitude':torch.full((b,2),math.log10(100001)),
                        'xy':torch.tensor([[100000.,200000.]]*b)}
            history={'last_step':300,'tier_trends':{'T02':{'latest_5pct':1.}}}
            args=d.parser().parse_args(['--output',str(root),'--device','cpu',
                                       '--tiers','T02','--gradient-samples','0',
                                       '--hint-samples','0'])
            with mock.patch.object(f.ex,'load_current',return_value=model), \
                 mock.patch.object(f,'build_for_resume',return_value=model['decoder']), \
                 mock.patch.object(f.cached,'FrozenCaptureCache',return_value=object()), \
                 mock.patch.object(f.science,'rows_for_probe',return_value=rows), \
                 mock.patch.object(f.rt,'token_eligible',return_value=True), \
                 mock.patch.object(f,'forward_masked',side_effect=forward):
                details,cases=d.live(args,root,identity,checkpoint,history)
            self.assertEqual(len(cases),8)
            self.assertEqual(details['T02']['joint_5pct'],1.)
            self.assertEqual(details['T02']['hidden']['y']['n'],0)
            self.assertEqual((root/'latest.pt').read_bytes(),saved_before)

    def test_guard_unsafe_report_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config.json').write_text(json.dumps({'schema':f.SCHEMA}))
            (root/'probe-fork-step-0000300.json').write_text(json.dumps({
                'fork_step':300,'gate_percent':40,'first_failing':'T02',
                'scores':{tier:{'joint_5pct':.2} for tier in d.TIERS}}))
            args=d.parser().parse_args(['--output',str(root),'--history-only','--report',str(root/'config.json')])
            with self.assertRaisesRegex(ValueError,'REGISTER_DIAG_UNSAFE_REPORT_PATH'):
                d.run(args)


if __name__=='__main__':unittest.main()
