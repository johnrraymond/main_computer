"""Regression checks: verified Gaussian baseline survives LM binding exactly."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import torch
from tools import nanojev_hawk_dove_gaussian_three_backbone as triad
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as baseline
from tools import nanojev_hawk_dove_gaussian_population_conditional as base


class FrozenFake(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.emb = torch.nn.Embedding(6, 4)
        for parameter in self.parameters(): parameter.requires_grad_(False)
    def get_input_embeddings(self): return self.emb


def fake_loader(name, args):
    return object(), FrozenFake(), object(), args.device, {'resolved_commit':'test-fixed', 'model_type':name}


def fake_capture(model, tokenizer, device, prompts, max_tokens):
    return torch.tensor([[int(x) / 255 for x in hashlib.sha256(p.encode()).digest()]
                         for p in prompts], dtype=torch.float32)


class BoundHeadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): torch.set_num_threads(1)

    @staticmethod
    def args(root, backbone='qwen'):
        return triad.parser().parse_args([
            'train','--backbone',backbone,'--device','cpu','--output',str(root/backbone),
            '--reference-checkpoint',str(root/'baseline.pt'),
            '--banks','24','--grid-bins','32','--hidden','8',
            '--train-matrices','5','--dev-matrices','2','--test-matrices','2',
            '--counts-per-matrix','3','--masks','1','--batch-size','16',
            '--steps','2','--probe-every','1','--capture-batch-size','32'])

    def anchor(self, seed=20261022):
        return baseline.BlindDensityNetwork(24,32,seed,8)

    def write_anchor(self, args):
        data, domain, manifest=triad._dataset(args,torch.device('cpu'))
        anchor=self.anchor(args.seed)
        base._atomic_save(args.reference_checkpoint,
                          {'schema':baseline.SCHEMA,
                           'identity':baseline._identity(args,manifest,domain),
                           'step':0,'model':anchor.state_dict()})
        return anchor

    def test_three_expected_backbones(self):
        self.assertEqual(triad.BACKBONES, ('qwen','tinystories','pythia'))

    def test_no_reference_checkpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'TRIAD_REFERENCE_CHECKPOINT_REQUIRED'):
            triad.FrozenBackboneGaussianHead(32,24,32,20261022,8)

    def test_bound_channel_recovers_exact_reference_density(self):
        anchor=self.anchor()
        model=triad.FrozenBackboneGaussianHead(32,24,32,20261022,8,anchor.state_dict())
        parsed=torch.randn(16,baseline.INPUT_DIM)
        features=torch.randn(16,32)
        with torch.no_grad():
            self.assertTrue(torch.equal(model(torch.cat([parsed,features],dim=1)),anchor(parsed)))
            self.assertTrue(torch.equal(model.locations(torch.cat([parsed,features],dim=1)),anchor.locations(parsed)))

    def test_reference_head_frozen_and_backbone_branch_has_gradients(self):
        anchor=self.anchor()
        model=triad.FrozenBackboneGaussianHead(32,24,32,20261022,8,anchor.state_dict())
        assert all(not p.requires_grad for p in model.encoder.parameters())
        self.assertFalse(model.bank_log_gains.requires_grad)
        self.assertFalse(model.log_proposal_sigma.requires_grad)
        features=torch.cat([torch.randn(10,baseline.INPUT_DIM), torch.randn(10,32)], dim=1)
        loss=model(features).square().sum()
        loss.backward()
        self.assertGreater(float(model.lm_residual[-1].weight.grad.abs().sum()), 0)

    def test_backbone_effect_is_real_and_ablatable(self):
        anchor=self.anchor()
        model=triad.FrozenBackboneGaussianHead(32,24,32,20261022,8,anchor.state_dict())
        x=torch.cat([torch.randn(4,baseline.INPUT_DIM),torch.randn(4,32)],1)
        with torch.no_grad():
            model.lm_residual[-1].weight[0,0]=2.
            with_lm=model.locations(x)
            model.backbone_enabled=False
            without_lm=model.locations(x)
            model.backbone_enabled=True
        self.assertFalse(torch.equal(with_lm,without_lm))
        with torch.no_grad():
            self.assertTrue(torch.equal(without_lm, anchor.locations(x[:,:baseline.INPUT_DIM])))

    def test_wrong_input_width_fails_closed(self):
        model=triad.FrozenBackboneGaussianHead(32,24,32,20261022,8,self.anchor().state_dict())
        with self.assertRaisesRegex(ValueError, 'TRIAD_BOUND_INPUT_DIMENSION_DRIFT'):
            model(torch.randn(4,32))

    def test_strict_reference_checkpoint_identity(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(Path(d));self.write_anchor(args)
            data, domain, manifest=triad._dataset(args,torch.device('cpu'))
            anchor,sha,rate=triad.verified_reference(args,manifest,domain,data)
            self.assertIsInstance(sha,str)
            self.assertEqual(len(sha),64)
            self.assertIn('encoder.0.weight',anchor)
            wrong=triad._dataset(args,torch.device('cpu'))[2]
            wrong['matrix_hashes']['dev'][0]='tampered'
            with self.assertRaisesRegex(RuntimeError,'TRIAD_REFERENCE_IDENTITY_MISMATCH'):
                triad.verified_reference(args,wrong,domain,data)

    def test_preserves_original_visible_numeric_features(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(Path(d)); self.write_anchor(args)
            data,domain,manifest=triad._dataset(args,torch.device('cpu'))
            cache={'schema':triad.SCHEMA+'.activations', 'backbone':'qwen',
                   'model_id':triad.cassette.SOURCE['qwen'][0], 'source_info':{},
                   'capture':'unit', 'input_dim':32,
                   'splits':{s:{'activations':torch.randn(len(v['rows']),32),
                                'prompt_sha256':triad.prompt_hash(v['rows'])}
                             for s,v in data.items()}}
            numeric_before=data['dev']['features'].clone()
            triad.validate_cache(cache,'qwen',data)
            triad._install_activations(data,cache,torch.device('cpu'))
            self.assertTrue(torch.equal(data['dev']['features'][:,:baseline.INPUT_DIM],numeric_before))
            self.assertEqual(data['dev']['features'].shape[1],baseline.INPUT_DIM+32)

    def test_old_fp32_caches_reused_with_strict_prompt_identity(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(Path(d));self.write_anchor(args)
            data,_,_=triad._dataset(args,torch.device('cpu'))
            legacy={'schema':'nanojev.hawk-dove.gaussian-frozen-three-backbone.v2-direct-input.activations',
                    'backbone':'qwen', 'model_id':triad.cassette.SOURCE['qwen'][0],
                    'source_info':{},'capture':'fp32','input_dim':32,
                    'splits':{s:{'activations':torch.ones(len(v['rows']),32),
                                 'prompt_sha256':triad.prompt_hash(v['rows'])}
                              for s,v in data.items()}}
            cache_root=Path(d)/'old'; (cache_root/'qwen').mkdir(parents=True)
            torch.save(legacy,cache_root/'qwen'/'feature_cache.pt')
            args.reuse_cache_root=cache_root
            with mock.patch.object(triad,'make_cache',side_effect=AssertionError('recapture')):
                cache=triad._load_cache_or_capture('qwen',args,data)
            self.assertEqual(cache['schema'],triad.SCHEMA+'.activations')
            cache['splits']['dev']['prompt_sha256']='invalid'
            with self.assertRaisesRegex(RuntimeError,'TRIAD_CACHE_PROMPT_OR_SPLIT_DRIFT'):
                triad.validate_cache(cache,'qwen',data)

    def test_train_all_dispatch_keeps_reference_and_independent_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            args=triad.parser().parse_args(['train-all','--output',str(root/'triad'),
                                            '--device','cpu','--steps','2',
                                            '--reference-checkpoint',str(root/'baseline.pt'),
                                            '--reuse-cache-root',str(root/'previous')])
            seen=[]
            def fake_run(argv, check):
                self.assertTrue(check)
                self.assertIn('--reference-checkpoint',argv)
                self.assertEqual(argv[argv.index('--reuse-cache-root')+1],str(root/'previous'))
                backbone=argv[argv.index('--backbone')+1]
                out=Path(argv[argv.index('--output')+1]);out.mkdir()
                seen.append(backbone)
                import json
                (out/'summary.json').write_text(json.dumps({
                    'identity':{'model_id':triad.cassette.SOURCE[backbone][0]},
                    'status':'FAIL','best_step':0,
                    'dev':{'gate':{'min_tier_joint_instance_rate':.60}},
                    'test':{'gate':{'min_tier_joint_instance_rate':.58}},
                    'manifest':{'matrix_hashes':{'train':['a'],'dev':['b'],'test':['c']}}
                }))
            with mock.patch.object(triad.subprocess,'run',side_effect=fake_run):
                report=triad.train_all(args)
            self.assertEqual(seen,list(triad.BACKBONES))
            self.assertEqual(set(report['backbones']),set(triad.BACKBONES))

    def test_three_separate_runs_checkpoint_and_audit_with_stub_captures(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for backbone in triad.BACKBONES:
                args=self.args(root,backbone)
                self.write_anchor(args)
                with mock.patch.object(triad.cassette,'load_source',side_effect=fake_loader), \
                     mock.patch.object(triad,'capture_hidden',side_effect=fake_capture):
                    result=triad.train(args)
                self.assertTrue((args.output/'best.pt').is_file())
                self.assertTrue((args.output/'summary.json').is_file())
                self.assertTrue(result['parsed_numeric_features_used_for_inference'])
                self.assertAlmostEqual(result['dev_backbone_increment'],0.,delta=1.)
                self.assertEqual(result['identity']['backbone'],backbone)
                audited=triad.audit(triad.parser().parse_args(['audit','--checkpoint',str(args.output/'best.pt')]))
                self.assertEqual(audited['splits']['dev']['gate'],result['dev']['gate'])

if __name__=='__main__': unittest.main()
