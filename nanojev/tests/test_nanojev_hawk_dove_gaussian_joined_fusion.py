"""Joined Gaussian fusion v2: independent, resumed, read-only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_joined_fusion as fusion
from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref


class JoinedFusionTests(unittest.TestCase):
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
        ref_ident=ref._identity(args,manifest,domain)
        reference=root/'reference'/'best.pt';reference.parent.mkdir(parents=True)
        torch.save({'schema':ref.SCHEMA,'identity':ref_ident,'step':2,
                    'model':ref.BlindDensityNetwork(16,32,1701,8).state_dict()},reference)
        duoroot=root/'duo'
        for backbone in ('pythia','tinystories'):
            path=duoroot/backbone;path.mkdir(parents=True)
            dim=16 if backbone=='pythia' else 8
            gen=torch.Generator().manual_seed(dim)
            cache={'schema':duo.SCHEMA+'.activations','backbone':backbone,
                   'model_id':duo.cassette.SOURCE[backbone][0],
                   'source_info':{'resolved_commit':'fixture'},
                   'capture':'middle and last hidden layer, last four tokens, FP32, no labels',
                   'input_dim':dim,'splits':{split:{
                       'activations':torch.randn(len(data[split]['rows']),dim,generator=gen),
                       'prompt_sha256':duo.prompt_hash(data[split]['rows'])}
                       for split in ('train','dev','test')}}
            ident=duo.identity(args,backbone,manifest,domain,cache)
            model=duo.IndependentGaussianHead(dim,16,32,1701,8)
            torch.save({'schema':duo.SCHEMA,'identity':ident,'step':2,
                        'model':model.state_dict()},path/'best.pt')
            torch.save(cache,path/'feature_cache.pt')
            (path/'config.json').write_text(json.dumps(ident))
            (path/'matrix_split_manifest.json').write_text(json.dumps(manifest))
        out=root/'joined'
        argv=['--reference-checkpoint',str(reference),'--duo-root',str(duoroot),
              '--output',str(out),'--device','cpu','--cpu-threads','1',
              '--eval-batch-size','32','--batch-size','16','--width','16',
              '--steps',str(steps),'--probe-every','2','--seed','444']
        return argv, out, duoroot

    def test_fresh_joined_head_no_reference_anchor(self):
        m=fusion.JoinedFusionHead(8,32,16)
        inputs=torch.randn(7,3*(8+6+64),requires_grad=True)
        values,zero=m(inputs)
        self.assertEqual(tuple(values.shape),(7,2))
        self.assertEqual(tuple(zero.shape),(7,2))
        ref_anchor=inputs[:,8+2:8+4]
        self.assertFalse(torch.allclose(values,ref_anchor))
        (values.square().mean()+zero.square().mean()).backward()
        dim=8+6+64
        for i in range(3):
            self.assertGreater(float(inputs.grad[:, i*dim:(i+1)*dim].abs().sum()),0)
            self.assertGreater(float(inputs.grad[:, i*dim+14:(i+1)*dim].abs().sum()),0)

    def test_signed_log_and_zero_are_resolved(self):
        scale=fusion.target_log_scale([[-2e6,2e6]]*2)
        truth=torch.tensor([[-1000000.,8.],[0.,0.],[100.,-10.]])
        encoded=fusion.to_log(truth,scale)
        zero_logits=torch.where(truth==0,torch.full_like(truth,10.),torch.full_like(truth,-10.))
        actual=fusion.decode(encoded,zero_logits,scale)
        torch.testing.assert_close(actual,truth,atol=1.e-2,rtol=1.e-6)
        self.assertTrue(torch.equal(actual[1],torch.zeros(2)))
        self.assertTrue(torch.isfinite(fusion.training_loss(encoded,zero_logits,truth,scale)))

    def test_new_head_learns_complementary_sources_without_reference_skip(self):
        torch.manual_seed(418)
        hidden,bins,width=4,4,24
        dim=hidden+6+2*bins
        batch=torch.randn(160,3*dim)
        # The synthetic target depends on the two OTHER heads, not the reference.
        target=torch.stack((torch.tanh(.3*batch[:,dim]+.4*batch[:,2*dim]),
                            torch.tanh(.4*batch[:,dim+1]-.3*batch[:,2*dim+1])),dim=-1)
        full=fusion.JoinedFusionHead(hidden,bins,width)
        control=fusion.JoinedFusionHead(hidden,bins,width)
        control.load_state_dict(full.state_dict())
        optim=torch.optim.Adam(full.parameters(),lr=.006)
        coptim=torch.optim.Adam(control.parameters(),lr=.006)
        inactive=fusion.reference_only(batch,dim)
        for _ in range(130):
            optim.zero_grad()
            loss=(full(batch)[0]-target).square().mean()
            loss.backward();optim.step()
            coptim.zero_grad()
            closs=(control(inactive)[0]-target).square().mean()
            closs.backward();coptim.step()
        self.assertLess(float(loss.detach()),float(closs.detach())*.5)
        self.assertLess(float(loss.detach()),.01)

    def test_training_and_read_only_sources(self):
        with tempfile.TemporaryDirectory() as td:
            argv,output,duoroot=self.fixture(Path(td),2)
            before={str(p):mr._sha256(p) for p in Path(td).rglob('*') if p.is_file()}
            with mock.patch('builtins.print'):
                report=fusion.main(argv)
            self.assertEqual(report['completed_updates'],2)
            self.assertEqual(report['best_trained_step'],2)
            self.assertIn('reference',report['source_ablation_dev_test']['dev'])
            self.assertTrue((output/'best.pt').is_file())
            self.assertTrue((output/'predictions_test.jsonl').is_file())
            self.assertEqual(before,{str(p):mr._sha256(p) for p in (Path(x) for x in before)})
            cache=torch.load(output/'feature_cache.pt',weights_only=True)
            self.assertEqual(cache['features']['train'].shape[-1],3*(8+6+64))
            self.assertTrue((cache['features']['train'][:,14:78] >= 0).all())
            with self.assertRaisesRegex(FileExistsError,'JOINED_REFUSES_OUTPUT_OVERWRITE'):
                fusion.main(argv)
            extended=argv.copy();extended[extended.index('--steps')+1]='4';extended.append('--resume')
            with mock.patch('builtins.print'):
                follow=fusion.main(extended)
            self.assertEqual(follow['completed_updates'],4)
            self.assertEqual(torch.load(output/'latest.pt',weights_only=True)['step'],4)
            (duoroot/'pythia'/'config.json').write_text('mutation')
            with self.assertRaisesRegex(RuntimeError,'JOINED_SOURCE_DRIFT'):
                fusion.main(extended)

    def test_restart_matches_uninterrupted(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            a1,o1,_=self.fixture(Path(t1),4)
            a2,o2,_=self.fixture(Path(t2),2)
            with mock.patch('builtins.print'):
                fusion.main(a1)
                fusion.main(a2)
                a2[a2.index('--steps')+1]='4';a2.append('--resume')
                fusion.main(a2)
            first=torch.load(o1/'latest.pt',weights_only=True)
            second=torch.load(o2/'latest.pt',weights_only=True)
            for name in first['model']:
                torch.testing.assert_close(first['model'][name],second['model'][name],rtol=0,atol=0)
            torch.testing.assert_close(first['sampler_rng'],second['sampler_rng'],rtol=0,atol=0)

if __name__=='__main__':
    unittest.main()
