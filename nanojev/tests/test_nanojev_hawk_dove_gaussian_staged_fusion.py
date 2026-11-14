"""Deterministic tests for recovered Gaussian fourth-head training."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_staged_fusion as sf
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref
from tools import nanojev_hawk_dove_gaussian_minority_report as mr


class StagedFusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def fixture(self, root, steps):
        torch.manual_seed(818)
        args=duo.parser().parse_args(['train','--backbone','pythia','--device','cpu',
            '--banks','16','--grid-bins','32','--hidden','8','--steps','2',
            '--seed','1701','--train-matrices','3','--dev-matrices','2',
            '--test-matrices','2','--counts-per-matrix','3','--masks','2',
            '--batch-size','16'])
        data,domain,manifest=ref._dataset(args,torch.device('cpu'))
        reference_id=ref._identity(args,manifest,domain)
        source=root/'reference'/'best.pt';source.parent.mkdir(parents=True)
        torch.save({'schema':ref.SCHEMA,'identity':reference_id,'step':2,
                    'model':ref.BlindDensityNetwork(16,32,1701,8).state_dict()},source)
        duo_root=root/'duo'
        for b in ('pythia','tinystories'):
            location=duo_root/b;location.mkdir(parents=True)
            dim=16 if b=='pythia' else 8
            gen=torch.Generator().manual_seed(dim)
            cache={'schema':duo.SCHEMA+'.activations','backbone':b,
                   'model_id':duo.cassette.SOURCE[b][0],
                   'source_info':{'resolved_commit':'fixture'},
                   'capture':'middle and last hidden layer, last four tokens, FP32, no labels',
                   'input_dim':dim,'splits':{s:{
                       'activations':torch.randn(len(data[s]['rows']),dim,generator=gen),
                       'prompt_sha256':duo.prompt_hash(data[s]['rows'])}
                       for s in ('train','dev','test')}}
            ident=duo.identity(args,b,manifest,domain,cache)
            model=duo.IndependentGaussianHead(dim,16,32,1701,8)
            torch.save({'schema':duo.SCHEMA,'identity':ident,'step':2,
                        'model':model.state_dict()},location/'best.pt')
            torch.save(cache,location/'feature_cache.pt')
            (location/'config.json').write_text(json.dumps(ident))
            (location/'matrix_split_manifest.json').write_text(json.dumps(manifest))
        output=root/'staged'
        argv=['--reference-checkpoint',str(source),'--duo-root',str(duo_root),
              '--output',str(output),'--device','cpu','--cpu-threads','1',
              '--eval-batch-size','32','--batch-size','16','--width','16',
              '--steps',str(steps),'--probe-every','2','--seed','444']
        return argv,output,source,duo_root

    def test_learned_readout_recovers_reference_means_without_hardcoded_weights(self):
        torch.manual_seed(4)
        head=sf.RecoveredFourthHead(8,32,16)
        dim=8+6+64
        x=torch.randn(100,3*dim)
        x[:,8+2:8+4]=torch.sigmoid(torch.randn(100,2))
        previous=head.readout.weight.detach().clone()
        fit=sf.recovered_readout(head,x)
        self.assertEqual(fit['training_rows'],100)
        self.assertFalse(torch.equal(previous,head.readout.weight))
        location,_=head(sf.prior.reference_only(x,dim))
        torch.testing.assert_close(location,x[:,10:12],atol=4e-6,rtol=0)
        (location.square().mean()).backward()
        self.assertIsNotNone(head.readout.weight.grad)

    def test_recovery_gate_refuses_lost_accuracy(self):
        src={'by_tier_joint_all_masks_rate':{'T01':.61,'T02':.62}}
        bad={'by_tier_joint_all_masks_rate':{'T01':.50,'T02':.62}}
        good={'by_tier_joint_all_masks_rate':{'T01':.61,'T02':.62}}
        self.assertEqual(sf.gate_recovery(bad,src,[[1,2]],[[1,2]],.05)['status'],'FAIL')
        self.assertEqual(sf.gate_recovery(good,src,[[1,2]],[[1,2]],.05)['status'],'PASS')

    def test_joint_branch_can_learn_complementary_input(self):
        torch.manual_seed(3)
        head=sf.RecoveredFourthHead(4,4,16)
        dim=head.head_dim
        x=torch.randn(32,3*dim)
        x[:,6:8]=torch.sigmoid(x[:,6:8])
        sf.recovered_readout(head,x)
        m=sf.prior.reference_only(x,dim)
        torch.testing.assert_close(head(x)[0],head(m)[0],atol=1e-6,rtol=0)
        optimizer=torch.optim.AdamW(head.parameters(),lr=.003)
        target=(x[:,6:8]+.1*torch.tanh(x[:,dim:dim+2])).clamp(0,1)
        for _ in range(20):
            optimizer.zero_grad();p,_=head(x);z=(p-target).square().mean();z.backward();optimizer.step()
        self.assertGreater(float((head(x)[0]-head(m)[0]).abs().max().detach()),0)
        self.assertGreater(float(head.projectors[1][1].weight.grad.abs().sum()),0)

    def test_fixture_train_resume_immutability_and_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            argv,out,source,duo_root=self.fixture(Path(td),2)
            original={str(p):mr._sha256(p) for p in Path(td).rglob('*') if p.is_file()}
            with mock.patch('builtins.print'):
                result=sf.main(argv)
            self.assertEqual(result['recovery_gate']['status'],'PASS')
            self.assertEqual(result['joint_updates_completed'],2)
            self.assertTrue((out/'recovered.pt').is_file())
            self.assertTrue((out/'latest.pt').is_file())
            self.assertTrue((out/'predictions_test.jsonl').is_file())
            self.assertEqual(original,{str(p):mr._sha256(Path(p)) for p in original})
            self.assertEqual(torch.load(out/'latest.pt',weights_only=True)['step'],2)
            self.assertEqual(torch.load(out/'recovered.pt',weights_only=True)['gate']['status'],'PASS')
            with self.assertRaisesRegex(FileExistsError,'RECOVERY_REFUSES_OUTPUT_OVERWRITE'):
                sf.main(argv)
            extended=argv.copy();extended[extended.index('--steps')+1]='4';extended.append('--resume')
            with mock.patch('builtins.print'):
                next_result=sf.main(extended)
            self.assertEqual(next_result['joint_updates_completed'],4)
            self.assertEqual(torch.load(out/'latest.pt',weights_only=True)['step'],4)
            (duo_root/'pythia'/'config.json').write_text(json.dumps({'changed':True}))
            with self.assertRaisesRegex(RuntimeError,'DRIFT|MISMATCH|CONFIG'):
                sf.main(extended)

    def test_exact_restart_weights_and_sampling(self):
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            a1,o1,_,_=self.fixture(Path(td1),4)
            a2,o2,_,_=self.fixture(Path(td2),2)
            with mock.patch('builtins.print'):
                sf.main(a1);sf.main(a2)
                a2[a2.index('--steps')+1]='4';a2.append('--resume');sf.main(a2)
            c1=torch.load(o1/'latest.pt',weights_only=True)
            c2=torch.load(o2/'latest.pt',weights_only=True)
            for k in c1['model']:
                torch.testing.assert_close(c1['model'][k],c2['model'][k],rtol=0,atol=0)
            for k in c1['control_model']:
                torch.testing.assert_close(c1['control_model'][k],c2['control_model'][k],rtol=0,atol=0)
            torch.testing.assert_close(c1['sampler_rng'],c2['sampler_rng'],rtol=0,atol=0)


if __name__=='__main__':
    unittest.main()
