"""No model downloads: test adapter, checkpoint migration and head preservation."""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
import torch

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_numeric_adapter_train as mod
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
from tools import nanojev_altreg_math_v3_decoder_experiment as v3


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payoffs={tag: Decimal(value) for tag,value in zip(hd.TAGS,('-4','3','0','1.5'))}

    def test_all_tiers_and_masks_parse_without_oracle_answer_leak(self):
        for tier in hd.TIERS:
            for mask, slots in hd.masks(tier):
                with self.subTest(tier=tier,mask=mask):
                    prompt=hd.render(tier,940000,60000,self.payoffs,slots,hd.REP_SCIENCE)
                    features=mod.encode(prompt)
                    self.assertEqual(len(features),mod.FEATURES)
                    if tier=='T00':
                        self.assertEqual(features, (0.,)*mod.FEATURES)
                    if tier in ('T02','T03','T04','T05','T06','T07'):
                        self.assertAlmostEqual(features[0],.94)
                        self.assertAlmostEqual(features[1],.06)
                    if tier=='T08':
                        self.assertAlmostEqual(features[8],.94)
                        self.assertAlmostEqual(features[9],.06)
        plain=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        modified=plain.replace('90000)', '123456)')
        self.assertNotEqual(plain,modified)
        self.assertEqual(mod.encode(plain),mod.encode(modified))

    def test_operand_roles_and_reversed_mul(self):
        prompt=hd.render('T06',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        features=mod.encode(prompt)
        self.assertEqual(features[:4],(.94,.06,.94,.06))
        self.assertEqual(features[4:8],(1.,1.,1.,1.))
        self.assertNotIn(-760000.0,features)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_NODE'):
            mod.encode(prompt.replace('ADD','SUB'))

    def test_zero_init_identity_and_trainability(self):
        torch.manual_seed(3)
        adapter=mod.make_adapter(9)
        raw=torch.randn(3,9)
        x=torch.tensor([mod.encode(hd.render(t,940000,60000,self.payoffs,(),hd.REP_SCIENCE))
                for t in ('T00','T02','T08')], dtype=torch.float32)
        self.assertTrue(torch.equal(raw+adapter(x),raw))
        loss=(raw + adapter(x)).square().sum()
        loss.backward()
        self.assertGreater(adapter.last.weight.grad.abs().sum().item(),0.)
        with torch.no_grad():
            adapter.last.weight.add_(.01)
        self.assertTrue(torch.equal(adapter(x)[0],torch.zeros(9)))
        self.assertGreater(adapter(x)[1:].abs().sum().item(),0)

    def test_real_v3_structure_preserved_and_gradient(self):
        torch.manual_seed(2)
        dec=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
        orig={k:v.clone() for k,v in dec.state_dict().items()}
        fake={'decoder':dec,'device':'cpu'}
        width=mod.adapter_width(dec,n_ops=len(base.OP_IDS))
        self.assertEqual(width,11)
        mod.attach(fake)
        self.assertTrue(all(torch.equal(v,dec.state_dict()[k]) for k,v in orig.items()))
        prompt=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        h=torch.randn(1,11)
        xy=torch.randn(1,2)
        ops=torch.zeros(1,dtype=torch.long)
        tokens=torch.randn(1,5,22)
        mask=torch.ones(1,5,dtype=torch.bool)
        before=dec(h,xy,ops,tokens,mask)
        with mod.inject(dec,[prompt],'cpu'):
            initial=dec(h,xy,ops,tokens,mask)
        for key in before:self.assertTrue(torch.equal(before[key],initial[key]))
        with mod.inject(dec,[prompt],'cpu'):
            out=dec(h,xy,ops,tokens,mask)
            (out['fraction'].sum()+out['sign'].sum()+out['exponent'].sum()).backward()
        self.assertGreater(dec.numeric_adapter.last.weight.grad.abs().sum().item(),0)

    def test_migration_is_in_place_preserves_existing_parameters_and_states(self):
        torch.manual_seed(4)
        decoder=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
        opt=torch.optim.AdamW(decoder.parameters(),lr=1e-5)
        # Produce nontrivial optimizer moments, so preservation is testable.
        output=decoder(torch.randn(2,11),torch.randn(2,2),torch.zeros(2,dtype=torch.long),
                       torch.randn(2,4,22),torch.ones(2,4,dtype=torch.bool))
        (output['fraction'].sum()+output['sign'].sum()).backward(); opt.step()
        identity={'trainer_sha256':mod.digest(Path(science.__file__))}
        original={'schema':science.CHECKPOINT_SCHEMA,'config':identity,'step':12300,
                  'focus':'T02','streak':0,'decoder':decoder.state_dict(),
                  'game_operator_head':{},'optimizer':opt.state_dict(),
                  'rng_cpu':torch.get_rng_state()}
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            (run/'config.json').write_text(json.dumps(identity),encoding='utf8')
            torch.save(original,run/'latest.pt')
            sha=mod.digest(run/'latest.pt')
            mod.migrate(run)
            self.assertEqual(mod.digest(run/mod.BACKUP),sha)
            migrated=torch.load(run/'latest.pt',weights_only=True)
            self.assertEqual(migrated['step'],original['step'])
            self.assertEqual(migrated['focus'],original['focus'])
            for k,v in original['decoder'].items():self.assertTrue(torch.equal(v,migrated['decoder'][k]))
            for k,v in original['optimizer']['state'].items():
                for name,value in v.items():
                    saved=migrated['optimizer']['state'][k][name]
                    if torch.is_tensor(value):self.assertTrue(torch.equal(value,saved))
                    else:self.assertEqual(value,saved)
            mod.migrate(run)
            self.assertEqual(mod.digest(run/mod.BACKUP),sha)
            model={'decoder':v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22),'device':'cpu'}
            mod.attach(model)
            model['decoder'].load_state_dict(migrated['decoder'], strict=True)
            resumed=torch.optim.AdamW(model['decoder'].parameters(),lr=1e-5)
            resumed.load_state_dict(migrated['optimizer'])
            self.assertEqual(len(resumed.state),len(opt.state))
            self.assertEqual(len(resumed.param_groups[0]['params']),len(opt.param_groups[0]['params'])+len(list(model['decoder'].numeric_adapter.parameters())))

    def test_wrapper_hooks_cached_training_forward_without_replacing_v3(self):
        from types import SimpleNamespace
        from tools import nanojev_game_transform_experiment as ex
        from tools import nanojev_hawk_dove_cached_train as cached
        prompt=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        holder={}
        def load_stub(*args, **kwargs):
            model={'decoder':v3.V3Decoder(11,len(base.OP_IDS),'attention',7,
                     hidden=16,token_dim=22),'device':'cpu'}
            holder['model']=model
            return model
        def cached_stub(model, prompts, cache):
            dec=model['decoder']
            self.assertIsNotNone(dec.numeric_adapter)
            return dec(torch.randn(len(prompts),11),torch.zeros(len(prompts),2),
                       torch.zeros(len(prompts),dtype=torch.long),
                       torch.randn(len(prompts),4,22),
                       torch.ones(len(prompts),4,dtype=torch.bool)), None
        def cached_main_stub(argv):
            self.assertIn('--cache-memory-mb',argv)
            loaded=ex.load_current('unused','cpu','float32',False,2)
            out,_=cached.cached_forward(loaded,[prompt],object())
            out['fraction'].sum().backward()
            self.assertGreater(loaded['decoder'].numeric_adapter.last.weight.grad.abs().sum().item(),0)
        fake={'numeric_adapter_contract':{'width':11}}
        with mock.patch.object(mod,'load_run_state',return_value=(fake,Path('/unused'))), \
                mock.patch.object(mod,'verify_saved',return_value=fake['numeric_adapter_contract']), \
                mock.patch.object(ex,'load_current',side_effect=load_stub), \
                mock.patch.object(cached,'cached_forward',side_effect=cached_stub), \
                mock.patch.object(cached,'main',side_effect=cached_main_stub):
            mod.execute(SimpleNamespace(output=Path('/unused'),cache_memory_mb=16,science_args=['train']))
        self.assertFalse(hasattr(holder['model']['decoder'],'_numeric_forward_override'))

    def test_unknown_unparsed_prompt_fails_closed(self):
        for text in ('hello 123', '(ARGMAX (MUL 5 OTHER_TAG) 0)',
                     'Your opponent plays Hawk 4 out of a million times.'):
            with self.subTest(text=text), self.assertRaises(ValueError):mod.encode(text)


if __name__ == '__main__':unittest.main()
