"""New fourth-head fusion learns only from frozen Gaussian head representations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_fusion as fusion
from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo


class FusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_initial_prediction_is_reference_density_mean_and_uncapped(self):
        x = torch.randn(7, 3*8+18)
        x[:, 3*8+2:3*8+4] = .5
        net = fusion.FusionHead(x.shape[1], 8, 16)
        self.assertTrue(torch.equal(net(x), x[:, 3*8+2:3*8+4]))
        with torch.no_grad():
            net.mlp[-1].bias[:] = torch.tensor([.20,-.20])
        pred = net(x)
        self.assertTrue(torch.allclose(pred, x[:, 3*8+2:3*8+4]+torch.tensor([.2,-.2]),atol=1e-6))

    def test_all_hidden_blocks_trainable_via_fusion(self):
        net = fusion.FusionHead(3*8+18, 8, 32)
        x = torch.randn(13,42,requires_grad=True)
        x.data[:,24+2:24+4] = .5
        y = torch.tensor([[.45,.55]]).expand(13,-1)
        # The final correction layer is zero at initialization. Move it first,
        # then prove gradients can access every independent learned head block.
        opt = torch.optim.Adam(net.parameters(),lr=.001)
        for _ in range(2):
            opt.zero_grad()
            loss = (net(x)-y).square().mean()
            loss.backward()
            opt.step()
        opt.zero_grad()
        net(x).sum().backward()
        for index in range(3):
            self.assertGreater(float(x.grad[:,index*8:(index+1)*8].abs().sum()),0)

    def _setup(self, root, steps, resume=False):
        torch.manual_seed(818)  # identical frozen source fixtures in both branches
        parser = duo.parser()
        args = parser.parse_args(['train','--backbone','pythia','--device','cpu',
             '--banks','16','--grid-bins','32','--hidden','8','--steps','2',
             '--seed','1701','--train-matrices','3','--dev-matrices','2',
             '--test-matrices','2','--counts-per-matrix','3','--masks','2',
             '--batch-size','16'])
        dataset, domain, manifest = ref._dataset(args,torch.device('cpu'))
        reference_ident = ref._identity(args,manifest,domain)
        reference=root/'reference'/'best.pt'
        reference.parent.mkdir(parents=True)
        model=ref.BlindDensityNetwork(16,32,1701,8)
        torch.save({'schema':ref.SCHEMA,'identity':reference_ident,
                    'step':2,'model':model.state_dict()},reference)
        duo_root=root/'duo'
        for backbone in ('pythia','tinystories'):
            path=duo_root/backbone
            path.mkdir(parents=True)
            dim=16 if backbone=='pythia' else 8
            generator=torch.Generator().manual_seed(dim)
            cache={'schema':duo.SCHEMA+'.activations','backbone':backbone,
                   'model_id':duo.cassette.SOURCE[backbone][0],
                   'source_info':{'resolved_commit':'fixture'},
                   'capture':'middle and last hidden layer, last four tokens, FP32, no labels',
                   'input_dim':dim,
                   'splits':{split:{'activations':torch.randn(len(dataset[split]['rows']),
                              dim,generator=generator),
                              'prompt_sha256':duo.prompt_hash(dataset[split]['rows'])}
                             for split in ('train','dev','test')}}
            identity=duo.identity(args,backbone,manifest,domain,cache)
            head=duo.IndependentGaussianHead(dim,16,32,1701,8)
            torch.save({'schema':duo.SCHEMA,'identity':identity,
                        'step':2,'model':head.state_dict()},path/'best.pt')
            torch.save(cache,path/'feature_cache.pt')
            (path/'config.json').write_text(json.dumps(identity))
            (path/'matrix_split_manifest.json').write_text(json.dumps(manifest))
        output = root/'fusion-run'
        argv = ['--reference-checkpoint',str(reference), '--duo-root',str(duo_root),
                '--output',str(output),'--device','cpu','--cpu-threads','1',
                '--eval-batch-size','32','--batch-size','16','--width','16',
                '--steps',str(steps),'--probe-every','2','--seed','444']
        if resume: argv.append('--resume')
        return argv,reference,duo_root,output

    def test_end_to_end_frozen_provenance_score_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            argv,ref,duo,output=self._setup(root,2)
            before={str(p):mr._sha256(p) for p in root.rglob('*') if p.is_file()}
            with mock.patch('builtins.print'):
                report=fusion.main(argv)
            self.assertGreater(report['best_step'], 0)
            self.assertGreater(report['reference_only_best_step'], 0)
            self.assertTrue(report['best_model_has_completed_optimizer_updates'])
            self.assertEqual(report['training_updates_completed'], 2)
            self.assertEqual(report['initial_untrained_baseline']['stage'], 'untrained_baseline')
            self.assertFalse(report['initial_untrained_baseline']['promoted'])
            self.assertEqual(report['best_trained_probe']['stage'], 'trained')
            checkpoint = torch.load(output/'best.pt', weights_only=True)
            self.assertGreater(checkpoint['step'], 0)
            self.assertGreater(float(checkpoint['model']['mlp.4.weight'].abs().sum()), 0)
            self.assertFalse(report['test_used_to_select_fusion'])
            self.assertTrue((output/'latest.pt').is_file())
            self.assertTrue((output/'best.pt').is_file())
            self.assertTrue((output/'predictions_dev.jsonl').is_file())
            self.assertTrue((output/'predictions_test.jsonl').is_file())
            self.assertEqual(before,{k:mr._sha256(Path(k)) for k in before})
            with self.assertRaisesRegex(FileExistsError,'FUSION_REFUSES_OVERWRITE'):
                fusion.main(argv)
            argv2=[str(x) for x in argv]
            argv2[argv2.index('--steps')+1]='4'
            argv2.append('--resume')
            with mock.patch('builtins.print'):
                report2=fusion.main(argv2)
            saved=torch.load(output/'latest.pt',weights_only=True)
            self.assertEqual(saved['step'],4)
            self.assertEqual(report2['optimizer_step'],4)
            self.assertEqual(before,{k:mr._sha256(Path(k)) for k in before})
            # A mutated original checkpoint or activations must be refused.
            (duo/'pythia'/'config.json').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'FUSION_RESUME_SOURCE_FINGERPRINT_DRIFT'):
                fusion.main(argv2)

    def test_zero_is_only_baseline_even_when_gate_never_improves(self):
        with tempfile.TemporaryDirectory() as temp:
            argv, _, _, output = self._setup(Path(temp), 2)
            # Identical zero accuracies at every probe are common in fresh heads.
            # The very first trained candidate must still become best.pt.
            with mock.patch('builtins.print'), mock.patch.object(fusion, '_rank', return_value=(0.0, 0.0)):
                fusion.main(argv)
            hist = json.loads((output/'history.json').read_text())['probes']
            self.assertEqual([e['step'] for e in hist], [0, 2])
            self.assertFalse(hist[0]['promoted'])
            self.assertTrue(hist[1]['promoted'])
            self.assertEqual(hist[1]['stage'], 'trained')
            self.assertEqual(torch.load(output/'best.pt',weights_only=True)['step'],2)
            self.assertEqual(torch.load(output/'best_reference_only.pt',weights_only=True)['step'],2)

    def test_legacy_step_zero_best_is_replaced_without_retraining(self):
        with tempfile.TemporaryDirectory() as temp:
            argv, _, _, output = self._setup(Path(temp), 2)
            with mock.patch('builtins.print'):
                fusion.main(argv)
            saved = torch.load(output/'latest.pt', weights_only=True)
            saved['best_step'] = 0
            saved['control_best_step'] = 0
            baseline_rank = (saved['history'][0]['dev']['min_joint5'],
                             saved['history'][0]['dev']['mean_joint5'])
            control_rank = (saved['history'][0]['control_dev']['min_joint5'],
                            saved['history'][0]['control_dev']['mean_joint5'])
            saved['best_rank'] = baseline_rank
            saved['control_best_rank'] = control_rank
            torch.save(saved, output/'latest.pt')
            # These imitate an older checkpoint that retained the initial head.
            for filename in ('best.pt','best_reference_only.pt'):
                checkpoint = torch.load(output/filename, weights_only=True)
                checkpoint['step'] = 0
                torch.save(checkpoint,output/filename)
            with mock.patch('builtins.print'):
                report=fusion.main(argv+['--resume'])
            self.assertEqual(report['optimizer_step'],2)
            self.assertEqual(report['best_step'],2)
            self.assertEqual(report['reference_only_best_step'],2)
            self.assertEqual(torch.load(output/'best.pt',weights_only=True)['step'],2)
            self.assertEqual(torch.load(output/'best_reference_only.pt',weights_only=True)['step'],2)

    def test_resume_matches_uninterrupted_learning(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            r1=Path(first);r2=Path(second)
            a1,_,_,o1=self._setup(r1,4)
            a2,_,_,o2=self._setup(r2,2)
            with mock.patch('builtins.print'):
                fusion.main(a1)
                fusion.main(a2)
                a2[a2.index('--steps')+1]='4'
                a2.append('--resume')
                fusion.main(a2)
            s1=torch.load(o1/'latest.pt',weights_only=True)
            s2=torch.load(o2/'latest.pt',weights_only=True)
            for k in s1['model']:
                torch.testing.assert_close(s1['model'][k],s2['model'][k],atol=0,rtol=0)
            torch.testing.assert_close(s1['sampler_rng'],s2['sampler_rng'],atol=0,rtol=0)

if __name__=='__main__': unittest.main()
