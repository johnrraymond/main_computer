"""Forking a T02-trained checkpoint directly into a T03-priority run is safe."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys
TOOLS=Path(__file__).resolve().parents[1]/'tools'
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))

from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_phase_cutover as cutover
from tools import nanojev_game_transform_experiment as ex


class CutoverContract(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.parent = self.root/'parent'
        self.parent.mkdir()
        self.out = self.root/'new-t03'
        self.db = self.root/'db.sqlite3'
        self.db.write_bytes(b'stable SQL training corpus identity')
        self.source = self.root/'source-v4.pt'
        self.source.write_bytes(b'stable source V4 weights identity')
        self.diag = self.root/'t02-v2.json'
        self.diag.write_text(json.dumps({
            'schema':ladder.SCHEMA,
            'generator_sha256':ex.filehash(Path(ladder.__file__)),
            'database_sha256':ex.filehash(self.db),
            'source_checkpoint_sha256':ex.filehash(self.source),
            'levels':list(ladder.DIAG_LEVELS),
            'models':{'source':{'step':104500}}
        }),encoding='utf-8')
        self.oldmap = {'t02_focus':{'T00':1,'T02c':2,'retention':1}}
        self.config = {
            'schema':ladder.SCHEMA,'trainer_version':ladder.TRAINER_VERSION,
            'source_checkpoint_sha256':ex.filehash(self.source),
            'database_sha256':ex.filehash(self.db),
            'diagnostic_sha256':ex.filehash(self.diag),
            'map_sha256':'sealed-ancestor-map-hash',
            'seed':20261015,'lr':1e-5,'op_weight':0,
            'cache_per_level':64,'retention_cache':64,'refresh_every':5000,
            'phase_definitions':self.oldmap
        }
        (self.parent/'ladder-config.json').write_text(json.dumps(self.config),encoding='utf-8')
        import torch
        self.cp = {
            'schema':ladder.SCHEMA,'trainer_version':ladder.TRAINER_VERSION,
            'source_sha256':ex.filehash(self.source),'database_sha256':ex.filehash(self.db),
            'step':220000,'phase_history':[{'phase':'t02_focus','first_step':1}],
            'decoder':{'weight':torch.tensor([4.25])},
            'game_operator_head':{'weight':torch.tensor([0.])},
            'optimizer':{'state':{'7':{'step':123}}},
            'torch_rng_cpu':torch.get_rng_state(),
            'last_loss':1e-6
        }
        torch.save(self.cp,self.parent/'latest.pt')
        self.original_hash = ex.filehash(self.parent/'latest.pt')
        self.args = SimpleNamespace(from_output=self.parent,output=self.out,
            database=self.db,source_checkpoint=self.source,diagnostic=self.diag,
            slots=cutover.DEFAULT_SLOTS,steps=420000,save_every=1000,
            report_every=500,max_wall_hours=8,device='cpu',dtype='float32',
            allow_model_download=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_quota_contract_and_t08_is_forbidden(self):
        q=cutover.parse_slots(cutover.DEFAULT_SLOTS)
        self.assertEqual(sum(q.values()),41)
        self.assertEqual(q['T03'],30)
        self.assertEqual({x:q[x] for x in ('T04','T05','T06','T07')},
                         {x:2 for x in ('T04','T05','T06','T07')})
        self.assertNotIn('T08',q)
        for slots in ('T03=30,T08=2,retention=1','T03=30,T03=1,retention=1',
                      'T03=130,retention=1','T03=30,retention=0','T03=30'):
            with self.subTest(slots=slots),self.assertRaises(ValueError):
                cutover.parse_slots(slots)

    def test_fork_copies_checkpoint_bitwise_and_seals_only_new_config(self):
        inherited,created=cutover.prepare(self.args)
        self.assertTrue(created)
        self.assertEqual(ex.filehash(self.parent/'latest.pt'),self.original_hash)
        self.assertEqual(ex.filehash(self.out/'latest.pt'),self.original_hash)
        self.assertEqual(json.loads((self.parent/'ladder-config.json').read_text()),self.config)
        spec,quota=ladder.read_map(self.out/'level-map.json',cutover.PHASE)
        self.assertEqual(quota,cutover.parse_slots(cutover.DEFAULT_SLOTS))
        self.assertEqual(spec['phases']['t02_focus'],self.oldmap['t02_focus'])
        self.assertEqual(inherited['op_weight'],0)
        self.assertEqual(inherited['seed'],20261015)
        self.assertEqual(inherited['map_sha256'],ex.filehash(self.out/'level-map.json'))
        self.assertEqual(inherited['phase_definitions'],spec['phases'])
        self.assertEqual(json.loads((self.out/cutover.PROVENANCE).read_text())['fork_step'],220000)
        import torch
        child_cp=torch.load(self.out/'latest.pt',map_location='cpu',weights_only=True)
        self.assertEqual(child_cp['step'],220000)
        self.assertEqual(child_cp['optimizer'],self.cp['optimizer'])
        self.assertEqual(child_cp['phase_history'],self.cp['phase_history'])
        # Calling the same bootstrap again must not overwrite trained weights.
        (self.out/'latest.pt').write_bytes(b'resumed checkpoint; do not overwrite')
        new_config,created=cutover.prepare(self.args)
        self.assertFalse(created)
        self.assertEqual((self.out/'latest.pt').read_bytes(),b'resumed checkpoint; do not overwrite')
        self.assertEqual(new_config,inherited)

    def test_invocation_is_one_step_and_retains_parent_hyperparameters(self):
        with patch.object(ladder,'train',return_value={'step':420000,'phase':cutover.PHASE}) as trainer:
            result=cutover.train_from_cutover(self.args)
            self.assertEqual(result['step'],420000)
            trainer.assert_called_once()
            train_args=trainer.call_args.args[0]
        self.assertEqual(train_args.phase,cutover.PHASE)
        self.assertEqual(train_args.steps,420000)
        self.assertEqual(train_args.lr,1e-5)
        self.assertEqual(train_args.op_weight,0)
        self.assertEqual(train_args.seed,20261015)
        self.assertEqual(train_args.level_map,self.out/'level-map.json')
        self.assertEqual(train_args.output,self.out)
        self.assertEqual(ex.filehash(self.parent/'latest.pt'),self.original_hash)

    def test_rejects_identity_drift_before_any_output(self):
        self.source.write_bytes(b'changed source checkpoint')
        with self.assertRaisesRegex(RuntimeError,'DIAGNOSTIC_IDENTITY_MISMATCH'):
            cutover.prepare(self.args)
        self.assertFalse(self.out.exists())

    def test_rejects_parent_or_target_collision(self):
        self.args.output=self.parent
        with self.assertRaisesRegex(ValueError,'CUTOVER_MUST_HAVE_SEPARATE_OUTPUT'):
            cutover.prepare(self.args)
        self.args.output=self.out
        self.out.mkdir()
        with self.assertRaises(FileNotFoundError):
            cutover.prepare(self.args)
        self.assertEqual(ex.filehash(self.parent/'latest.pt'),self.original_hash)

    def test_rejects_tampering_with_sealed_new_map(self):
        cutover.prepare(self.args)
        map_file=self.out/'level-map.json'
        data=json.loads(map_file.read_text())
        data['phases'][cutover.PHASE]['T03']=10
        map_file.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError,'CUTOVER_OUTPUT_MAP_DRIFT'):
            cutover.prepare(self.args)


class CutoverCpuContinuation(unittest.TestCase):
    def test_real_optimizer_resume_from_fork(self):
        """Minimal real CPU backward pass after an automatic optimizer-state fork."""
        try:
            import torch
            from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
            from tools import nanojev_altreg_math_v3_decoder_experiment as v3
            from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
        except ImportError as exc:
            self.skipTest('V4 source dependencies missing: '+str(exc))

        class Parent(torch.nn.Module):
            def extract(self,flat):
                return torch.zeros(len(flat),16)
            def xy_coord(self,h):
                return torch.zeros(len(h),2)
        class Frozen(torch.nn.Module):
            pass
        class Tokenizer:
            def __call__(self,prompt,add_special_tokens=False):
                return {'input_ids':list(range(max(6,len(prompt)//15)))}
        def get_parent(*_):
            return ({'config':{'input_dim':96,'width':16}},None,Parent(),'parent_sha')
        def capture(_backbone,_tokenizer,_device,prompts,_maximum):
            flats=[];sequences=[]
            for prompt in prompts:
                digest=hashlib.sha256(prompt.encode()).digest()
                flats.append(torch.tensor([(digest[i%32]-128)/128 for i in range(96)],dtype=torch.float32))
                sequences.append(torch.tensor([[(digest[(i*7+j)%32]-128)/128 for j in range(24)]
                                               for i in range(8)],dtype=torch.float32))
            return (torch.stack(flats),torch.stack(sequences),
                    torch.ones(len(flats),8,dtype=torch.bool))

        with TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'db.sqlite3';ex.generate(db,instances_per_game=3)
            torch.manual_seed(7)
            decoder=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
            op=mix.OperationHead(24,32)
            source=root/'source-v4.pt'
            torch.save({'schema':mix.SCHEMA,'step':104500,
                        'config':{'backbone':'qwen','parent_root':str(root),
                                  'parent_checkpoint':'best','parent_sha256':'parent_sha',
                                  'hidden':32,'max_prompt_tokens':512},
                        'head':decoder.state_dict(),'operation_head':op.state_dict()},source)
            diag=root/'diagnostic.json'
            old_map=root/'old-map.json'
            old_map.write_text(json.dumps({'schema':ladder.SCHEMA,'phases':{
                't02_focus':{'T02a':1,'retention':1}}}),encoding='utf-8')
            original=root/'completed-t02';original.mkdir()
            fakeargs=SimpleNamespace(database=db,source_checkpoint=source,experimental_checkpoint=None,
                levels=','.join(ladder.DIAG_LEVELS),limit_per_split=1,include_unchanged=False,
                report=diag,diagnostic=diag,level_map=old_map,phase='t02_focus',output=original,
                steps=1,save_every=1,lr=1e-4,op_weight=0,seed=20261015,
                cache_per_level=2,retention_cache=2,report_every=1,refresh_every=2,max_wall_hours=0.,
                device='cpu',dtype='float32',allow_model_download=False)
            from contextlib import redirect_stdout
            from io import StringIO
            with patch.object(v3,'get_parent',get_parent),patch.object(v3,'full_capture',capture),\
                 patch.object(base.cassette,'load_source',lambda *_:(None,Frozen(),Tokenizer(),'cpu',None)):
                with redirect_stdout(StringIO()):
                    ladder.diagnose(fakeargs)
                    initial=ladder.train(fakeargs)
                self.assertEqual(initial['step'],1)
                initial_cp=torch.load(original/'latest.pt',map_location='cpu',weights_only=True)
                # Mimic a completed long T02 lineage without running 220k CPU iterations.
                initial_cp['step']=220000
                torch.save(initial_cp,original/'latest.pt')
                old_hash=ex.filehash(original/'latest.pt')
                cutargs=SimpleNamespace(from_output=original,output=root/'cutover-t03',
                    database=db,source_checkpoint=source,diagnostic=diag,
                    slots='T03=1,T04=1,retention=1',steps=220001,
                    save_every=1,report_every=1,max_wall_hours=0,
                    device='cpu',dtype='float32',allow_model_download=False)
                with redirect_stdout(StringIO()):
                    result=cutover.train_from_cutover(cutargs)
                self.assertEqual(result['step'],220001)
                self.assertEqual(result['phase'],'t03_priority')
                self.assertEqual(ex.filehash(original/'latest.pt'),old_hash)
                cp=torch.load(cutargs.output/'latest.pt',map_location='cpu',weights_only=True)
                self.assertEqual([e['phase'] for e in cp['phase_history']],['t02_focus','t03_priority'])
                self.assertEqual(cp['phase_history'][-1]['first_step'],220001)
                self.assertIn('optimizer',cp)
                self.assertNotEqual(ex.filehash(cutargs.output/'latest.pt'),old_hash)
                self.assertTrue((cutargs.output/'frozen-capture-cache-t03_priority.pt').is_file())


if __name__=='__main__':
    unittest.main()
