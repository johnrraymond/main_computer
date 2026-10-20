"""CPU contracts and synthetic end-to-end smoke for the T02 numerical pathway probe."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import torch
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_continuous_benchmark as old
from tools import nanojev_hawk_dove_numeric_pathway_probe as probe
from tools import nanojev_altreg_math_v3_decoder_experiment as v3


class PathwayTests(unittest.TestCase):
    def test_literal_spans_identical_across_eight_annotations(self):
        payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}
        for mask,slots in hd.masks('T02'):
            with self.subTest(mask=mask):
                prompt=hd.render('T02',940000,60000,payoffs,slots,hd.REP_SCIENCE)
                spans=probe.literal_spans(prompt)
                self.assertEqual([prompt[a:b] for a,b in spans],['940000','60000'])
                self.assertNotIn('90000',[prompt[a:b] for a,b in spans])

    def test_token_offsets_take_input_digits_not_given_y(self):
        payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}
        prompt=hd.render('T02',940000,60000,payoffs,hd.SLOTS['T02'],hd.REP_SCIENCE)
        class FakeTok:
            def __call__(self,inp,add_special_tokens,return_offsets_mapping):
                assert inp==prompt and not add_special_tokens and return_offsets_mapping
                return {'offset_mapping':[(i,i+1) for i in range(len(inp))]}
        spans=probe.token_span_indices(FakeTok(),prompt,len(prompt))
        self.assertEqual([''.join(prompt[i] for i in group) for group in spans],['940000','60000'])
        with self.assertRaises(RuntimeError):probe.token_span_indices(FakeTok(),prompt,len(prompt)-1)

    def test_baseline_cap_changes_predictions_only_above_legacy_cap(self):
        import math
        # A target 3.2M is clipped by the old cap and survives the 10M cap.
        sign=torch.tensor([[[0.,0.,10.],[0.,0.,10.]]])
        exponent=torch.zeros(1,2,8);exponent[:,:,6]=10.
        frac=torch.tensor([[math.log10(3.2),math.log10(2.1)]])
        before=v3.compose_logits(sign,exponent,frac,2_000_000)
        after=v3.compose_logits(sign,exponent,frac,10_000_000)
        self.assertLessEqual(float(before.max()),2_000_002.)
        self.assertAlmostEqual(float(after[0,0]),3_200_000,delta=1)
        self.assertAlmostEqual(float(after[0,1]),2_100_000,delta=1)

    def test_arithmetic_learned_only_from_train_generalizes(self):
        a=np.arange(0,1.01,.05)
        b=1-a
        inputs=np.stack([a,b],axis=-1).astype(np.float32)
        targets=np.stack([(3*b-a)*1e6,1.5*b*1e6],axis=-1).astype(np.float32)
        coeff=probe.learned_arithmetic_map({'operands':inputs,'targets':targets})
        hidden=np.array([[.139,.861],[.953,.047]],dtype=np.float64)
        xy=np.column_stack([hidden,np.ones(2)])@coeff*1e6
        truth=np.stack([(3*hidden[:,1]-hidden[:,0])*1e6,1.5*hidden[:,1]*1e6],axis=-1)
        self.assertLess(np.max(np.abs(xy-truth)),.2)

    def test_mean_and_operand_pools_ignore_padding(self):
        seq=torch.arange(2*8*3).reshape(2,8,3).float()
        mask=torch.tensor([[1,1,1,1,1,1,1,1],[1,1,1,1,1,0,0,0]],dtype=torch.bool)
        class MockTok:
            pass
        with patch.object(probe,'token_span_indices',return_value=[[1,2],[3,4]]):
            means,vals=probe.pools_from_seq(seq,mask,MockTok(),['a','b'])
        np.testing.assert_allclose(means[1],seq[1,:5].mean(0).numpy())
        np.testing.assert_allclose(vals[0][:3],seq[0,[1,2]].mean(0).numpy())
        np.testing.assert_allclose(vals[1][-3:],seq[1,[3,4]].mean(0).numpy())

    def test_train_readout_observes_train_only(self):
        with tempfile.TemporaryDirectory() as root:
            args=SimpleNamespace(root=Path(root),train_device='cpu',epochs=2,
                seed=3,learning_rate=.005,train_batch_size=8)
            n=16
            features=np.repeat(np.arange(n,dtype=np.float32)[:,None]/100,4,axis=1)
            truth=np.stack([np.arange(n)/15,1-np.arange(n)/15],axis=1).astype(np.float32)
            rows={'operands':truth}
            def capture(x,split):
                self.assertEqual(split,'train')
                return {'qwen_mean':features}
            with patch.object(probe,'load_capture',side_effect=capture), \
                 patch.object(old,'load_dataset',return_value=rows), \
                 patch.object(probe,'arg_namespace',return_value=None), \
                 patch.object(probe,'digest',return_value='fake-hash'):
                probe.learn(args,'qwen_mean','linear')
            self.assertTrue((args.root/'probe-qwen_mean-linear.pt').exists())

    def test_finite_operand_metrics(self):
        truth=np.array([[.1,.9],[.5,.5]],dtype=np.float32)
        guess=np.array([[.1,.9],[.501,.499]],dtype=np.float32)
        m=probe.operand_metrics(guess,truth)
        self.assertEqual(m['both_within_5000_counts'],1.)
        self.assertLess(m['mae_a_count'],1000)

    def test_previous_artifacts_unchanged_by_synthetic_fit_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);previous=root/'old';previous.mkdir();out=root/'new';out.mkdir()
            donor=root/'donor';donor.mkdir();(donor/'latest.pt').write_bytes(b'donor')
            teacher=root/'teacher';teacher.write_bytes(b'teacher')
            (previous/'manifest.json').write_text('{}')
            (out/'manifest.json').write_text('{}')
            args=SimpleNamespace(previous=previous,root=out,donor=donor,teacher=teacher,
                train_device='cpu',epochs=3,seed=12,learning_rate=.001,train_batch_size=16, source=root/'source')
            for split,ncases in (('train',8),('dev',3)):
                av=np.repeat(np.linspace(.05,.95,ncases),8)
                bv=1-av
                inputs=np.column_stack([av,bv]).astype(np.float32)
                targets=np.column_stack([(3*bv-av)*1e6,1.5*bv*1e6]).astype(np.float32)
                f=np.stack([np.tile(inputs[:,0,None],(1,4)),np.tile(inputs[:,1,None],(1,4))],axis=1)
                old.save_dataset(SimpleNamespace(root=previous,donor=donor,teacher=teacher),
                    split,f,targets,inputs,np.zeros_like(targets),
                    np.tile(np.arange(8),ncases),np.repeat(np.arange(ncases),8),
                    [f'{i}/T02/{m}' for i in range(ncases) for m in range(8)])
                olddata=old.load_dataset(SimpleNamespace(root=previous,donor=donor,teacher=teacher),split)
                blob={'metadata':np.asarray(json.dumps({'schema':probe.SCHEMA,'split':split,
                    'identity_sha256':probe.digest(out/'manifest.json'),
                    'prior_features_sha256':probe.digest(previous/f'features-{split}.npz')},sort_keys=True)),
                      'corrected_v3':np.zeros_like(targets)}
                for plane in probe.PLANES:
                    blob[plane]=np.column_stack([inputs,inputs**2,np.ones(len(inputs))]).astype(np.float32)
                p=out/f'pathway-{split}.npz'
                np.savez_compressed(p,**blob)
                probe.jsonwrite(out/f'pathway-{split}.json',{'sha256':probe.digest(p)})
            oldtrain_hash=probe.digest(previous/'features-train.npz')
            with patch.object(probe,'verify',return_value={}):
                probe.fit_all(args)
                report=probe.report(args)
            self.assertEqual(probe.digest(previous/'features-train.npz'),oldtrain_hash)
            self.assertEqual(report['report']['dev']['numerical_channel_affine']['joint_5pct'],1.)
            self.assertEqual(set(report['report']['dev']['planes']),set(probe.PLANES))
            self.assertTrue((out/'pathway-results.json').is_file())
            self.assertFalse((out/'features-test.npz').exists())


if __name__=='__main__':unittest.main()
