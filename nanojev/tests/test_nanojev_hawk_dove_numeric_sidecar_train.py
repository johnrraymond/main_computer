"""No Qwen download; tests the actual scientific grammar and checkpoint upgrade."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch
from tools import nanojev_hawk_dove_numeric_sidecar_train as mod
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
from tools import nanojev_altreg_math_v3_decoder_experiment as v3
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_hawk_dove_cached_train as cached

PAYOFFS = dict(zip(hd.TAGS, map(Decimal, ('-1','3','0','1.5'))))
COEFF = np.asarray((1., -1., 3., 0., 1.5))


def demo_db(path):
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE presentations (presentation_id TEXT, instance_id TEXT, tier TEXT, mask INTEGER, full_count INTEGER, prompt TEXT, x REAL, y REAL, z TEXT, split TEXT)')
        for split, vals in [('train',(0,75000,250000,475000,750000,825000,950000,1000000)),
                            ('dev',(50000,125000,500000,900000))]:
            for a in vals:
                b=1_000_000-a
                x,y,z=hd.oracle(a,b,PAYOFFS)
                for tier in hd.TIERS:
                    for mask,slots in hd.masks(tier):
                        prompt=hd.render(tier,a,b,PAYOFFS,slots,hd.REP_SCIENCE)
                        con.execute('INSERT INTO presentations VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (f'{a}/{tier}/{mask}',str(a),tier,mask,len(slots),prompt,float(x),float(y),z,split))


class SidecarTests(unittest.TestCase):
    def test_all_tiers_and_every_mask(self):
        for tier in hd.TIERS:
            for mask,slots in hd.masks(tier):
                with self.subTest(tier=tier,mask=mask):
                    prompt=hd.render(tier,940000,60000,PAYOFFS,slots,hd.REP_SCIENCE)
                    pred=mod.features(prompt) @ COEFF * mod.SCALE
                    truth=np.asarray(hd.oracle(940000,60000,PAYOFFS)[:2],dtype=float)
                    np.testing.assert_allclose(pred,truth,rtol=0,atol=1e-6)

    def test_visible_literals_and_no_cross_branch_target_leak(self):
        p=hd.render('T02',940000,60000,PAYOFFS,(),hd.REP_SCIENCE)
        f=mod.features(p)
        modified=p.replace('90000)', '123456)')
        changed=mod.features(modified)
        np.testing.assert_array_equal(f[0],changed[0])
        self.assertNotEqual(f[1,0],changed[1,0])
        text=hd.render('T07',940000,60000,PAYOFFS,(),hd.REP_SCIENCE)
        with self.assertRaisesRegex(ValueError,'INCONSISTENT_PROSE|MISMATCH'):
            mod.features(text.replace('Hawk 940000 out of', 'Hawk 930000 out of'))
        for invalid in ('hello 123','(ARGMAX (SUB 400 500) 100)',
                        '(ARGMAX (MUL 7 OTHER_PAYOFF) 100)'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                mod.features(invalid)

    def test_train_only_fit_and_dev_no_test_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'teacher.sqlite3'
            demo_db(db)
            with mock.patch.object(hd,'read_rows', wraps=hd.read_rows) as reader:
                coef,proof=mod.preflight(db)
                self.assertEqual(proof['status'],'PASS')
                self.assertTrue(all(call.args[1]!='test' for call in reader.call_args_list))
            np.testing.assert_allclose(coef,COEFF,rtol=0,atol=1e-10)
            # Changing fitted weights destroys T02: predictions depend on learned coefficients.
            broken=coef.copy();broken[1]+=2.
            self.assertLess(mod.score(db,broken,'dev','T02')['joint_5pct'],.70)
            self.assertEqual(proof['trained_tiers'], ['T00','T01','T02'])
            for tier in hd.TIERS:
                self.assertEqual(proof['dev_by_tier'][tier]['joint_5pct'],1.)

    def test_head_exact_zero_and_gradients(self):
        h=mod.make_head()
        with torch.no_grad():
            h.coefficients.copy_(torch.from_numpy(COEFF))
        p=hd.render('T02',750000,250000,PAYOFFS,(),hd.REP_SCIENCE)
        # X = 0.  Check no float32 rounding degrades strict 5%-of-zero gate.
        xy=h(torch.from_numpy(np.stack([mod.features(p)])))
        self.assertLess(abs(xy[0,0].item()),1e-8)
        xy.sum().backward()
        self.assertGreater(h.coefficients.grad.abs().sum().item(),0)

    def test_checkpoint_upgrade_preserves_original_moments_and_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'teacher.sqlite3';demo_db(db)
            run=root/'run';run.mkdir()
            torch.manual_seed(7)
            dec=v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22)
            opt=torch.optim.AdamW(dec.parameters(),lr=1e-5)
            out=dec(torch.randn(2,11),torch.randn(2,2),torch.zeros(2,dtype=torch.long),
                    torch.randn(2,3,22),torch.ones(2,3,dtype=torch.bool))
            out['fraction'].sum().backward(); opt.step()
            ident={'trainer_sha256':adapter.digest(Path(science.__file__))}
            (run/'config.json').write_text(json.dumps(ident))
            ck={'schema':science.CHECKPOINT_SCHEMA,'config':ident,'step':1265400,
                'focus':'T02','streak':0,'decoder':dec.state_dict(),
                'game_operator_head':{},'optimizer':opt.state_dict(),
                'rng_cpu':torch.get_rng_state()}
            torch.save(ck,run/'latest.pt')
            adapter.migrate(run)
            existing=torch.load(run/'latest.pt',weights_only=True)
            prior_digest=mod.sha(run/'latest.pt')
            mod.migrate(run,db)
            new=torch.load(run/'latest.pt',weights_only=True)
            self.assertEqual(mod.sha(run/mod.BACKUP),prior_digest)
            self.assertEqual(new['step'],1265400)
            self.assertEqual(new['focus'],'T02')
            for k,v in existing['decoder'].items():
                self.assertTrue(torch.equal(v,new['decoder'][k]),k)
            for k,states in existing['optimizer']['state'].items():
                for name,value in states.items():
                    current=new['optimizer']['state'][k][name]
                    if isinstance(value,torch.Tensor):self.assertTrue(torch.equal(value,current))
                    else:self.assertEqual(value,current)
            self.assertEqual(len(new['optimizer']['param_groups'][0]['params']),
                             len(existing['optimizer']['param_groups'][0]['params'])+1)
            mod.migrate(run,db)
            model={'decoder':v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22),'device':'cpu'}
            adapter.attach(model)
            model['decoder'].add_module('numeric_sidecar',mod.make_head())
            model['decoder'].load_state_dict(new['decoder'],strict=True)
            renewed=torch.optim.AdamW(model['decoder'].parameters(),lr=1e-5)
            renewed.load_state_dict(new['optimizer'])
            self.assertEqual(len(renewed.param_groups[0]['params']),
                             len(new['optimizer']['param_groups'][0]['params']))

    def _create_adapter_checkpoint(self, root):
        db = root / 'teacher.sqlite3'
        demo_db(db)
        run = root / 'run'
        run.mkdir()
        torch.manual_seed(7)
        decoder = v3.V3Decoder(11, len(base.OP_IDS), 'attention', 7, hidden=16, token_dim=22)
        opt = torch.optim.AdamW(decoder.parameters(), lr=1e-5)
        output = decoder(torch.randn(2,11), torch.randn(2,2),
                         torch.zeros(2,dtype=torch.long), torch.randn(2,3,22),
                         torch.ones(2,3,dtype=torch.bool))
        output['fraction'].sum().backward()
        opt.step()
        config = {'trainer_sha256': adapter.digest(Path(science.__file__))}
        (run/'config.json').write_text(json.dumps(config),encoding='utf8')
        ck = {'schema':science.CHECKPOINT_SCHEMA,'config':config,
              'step':1263300,'focus':'T02','streak':0,
              'decoder':decoder.state_dict(),'game_operator_head':{},
              'optimizer':opt.state_dict(),'rng_cpu':torch.get_rng_state()}
        torch.save(ck,run/'latest.pt')
        adapter.migrate(run)
        return db,run

    def test_dropped_adapter_contract_can_only_be_restored_from_migration_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, run = self._create_adapter_checkpoint(Path(tmp))
            upgraded = torch.load(run/'latest.pt',weights_only=True)
            # Reproduce EXACT scientific trainer defect: it builds its save
            # dictionary from scratch, leaving old registered adapter tensors
            # and optimizer moments while omitting the metadata keys.
            dropped = {k:v for k,v in upgraded.items() if k not in
                       ('numeric_adapter_contract','step_at_migration')}
            dropped['step'] += 300
            torch.save(dropped,run/'latest.pt')
            before = torch.load(run/'latest.pt',weights_only=True)
            self.assertFalse(mod.status(run)['numeric_adapter_contract_present'])
            before_hash = mod.sha(run/'latest.pt')
            mod.restore_adapter_contract(run)
            repaired = torch.load(run/'latest.pt',weights_only=True)
            adapter.verify_saved(repaired)
            self.assertEqual(repaired['step'],1263600)
            self.assertEqual(repaired['step_at_migration'],1263300)
            self.assertEqual(mod.sha(run/'latest.pre-adapter-contract-repair.step-1263600.pt'),before_hash)
            for key,value in before['decoder'].items():
                self.assertTrue(torch.equal(value,repaired['decoder'][key]),key)
            for k,value in before['optimizer']['state'].items():
                for field, tensor in value.items():
                    updated = repaired['optimizer']['state'][k][field]
                    if isinstance(tensor, torch.Tensor):
                        self.assertTrue(torch.equal(tensor,updated))
                    else:self.assertEqual(tensor,updated)
            mod.migrate(run,db)
            self.assertTrue(mod.status(run)['numeric_sidecar_contract_present'])

    def test_scientific_chunk_save_preserves_both_contracts_and_optimizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db,run = self._create_adapter_checkpoint(Path(tmp))
            mod.migrate(run,db)
            sidecar_ck = torch.load(run/'latest.pt',weights_only=True)
            original_save = torch.save
            stripped = {k:v for k,v in sidecar_ck.items() if k not in
                        ('numeric_adapter_contract','step_at_migration',
                         'numeric_sidecar_contract','numeric_sidecar_step_at_migration')}
            stripped['step'] += 300
            with mod.hooks(sidecar_ck,run):
                self.assertIsNot(torch.save, original_save)
                torch.save(stripped,run/'latest.tmp.pt')
                # Other cache and auxiliary artifacts remain unchanged.
                torch.save({'cache':'data'},run/'cached.pt')
            self.assertIs(torch.save, original_save)
            checkpoint = torch.load(run/'latest.tmp.pt',weights_only=True)
            adapter.verify_saved(checkpoint)
            mod.verify_sidecar(checkpoint)
            self.assertEqual(checkpoint['step'],sidecar_ck['step']+300)
            self.assertEqual(checkpoint['numeric_sidecar_step_at_migration'],sidecar_ck['step'])
            self.assertEqual(len(checkpoint['optimizer']['param_groups'][0]['params']),
                             len(sidecar_ck['optimizer']['param_groups'][0]['params']))
            self.assertEqual(torch.load(run/'cached.pt',weights_only=True),{'cache':'data'})
            with self.assertRaisesRegex(ValueError,'NONADVANCING_CHECKPOINT'):
                with mod.hooks(sidecar_ck,run):
                    torch.save(sidecar_ck,run/'latest.tmp.pt')

    def test_hook_routes_both_training_loss_and_probe_predictions(self):
        p=hd.render('T02',940000,60000,PAYOFFS,(),hd.REP_SCIENCE)
        ck={'numeric_adapter_contract':{'width':11}}
        fakebase=SimpleNamespace(registers=SimpleNamespace(MAX_VALUE=10_000_000))
        def load(*a,**k):
            model={'decoder':v3.V3Decoder(11,len(base.OP_IDS),'attention',7,hidden=16,token_dim=22),
                   'v3':v3,'device':'cpu','base':fakebase}
            return model
        def stub(model,prompts,cache):
            n=len(prompts)
            out=model['decoder'](torch.randn(n,11),torch.zeros(n,2),
                    torch.zeros(n,dtype=torch.long),torch.randn(n,5,22),
                    torch.ones(n,5,dtype=torch.bool))
            return out,None
        with mock.patch.object(ex,'load_current',side_effect=load), \
             mock.patch.object(cached,'cached_forward',side_effect=stub), \
             mod.hooks(ck, Path(".")):
            model=ex.load_current()
            with torch.no_grad():model['decoder'].numeric_sidecar.coefficients.copy_(torch.from_numpy(COEFF))
            with mock.patch.object(ex,'forward',side_effect=lambda model,prompts:cached.cached_forward(model,prompts,None)):
                pred=science.predict(model,[p])
                np.testing.assert_allclose(pred, [[-760000.,90000.]],rtol=0,atol=1e-6)
                out,_=ex.forward(model,[p])
                loss,notes=v3.objective(out,torch.tensor([[-760000.,90000.]]),fakebase,'attention')
                self.assertIn('numeric',notes)
                self.assertTrue(torch.isfinite(loss))
                loss.backward()
                self.assertIsNotNone(model['decoder'].numeric_sidecar.coefficients.grad)

if __name__ == '__main__':unittest.main()
