"""Contracts for independent T03 computation tiers and safe T02 checkpoint cutover."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_TOOLS=Path(__file__).resolve().parents[1]/'tools'
if str(_TOOLS) not in sys.path:sys.path.insert(0,str(_TOOLS))

from tools import nanojev_game_transform_t03_tiered as tiered
from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_experiment as ex


class OracleContract(unittest.TestCase):
    def test_exact_training_distribution_and_reserved_t08(self):
        q=tiered.DEFAULT_QUOTAS
        self.assertEqual(sum(q.values()),41)
        self.assertEqual(sum(q[x] for x in tiered.TIERS),30)
        self.assertEqual(sum(q[x] for x in ('T04','T05','T06','T07')),8)
        self.assertEqual((q['T01'],q['T02f'],q['retention']),(1,1,1))
        self.assertNotIn('T08',q)
        self.assertEqual(tiered.parse_slots(tiered.DEFAULT_SLOTS),q)
        with self.assertRaises(ValueError):tiered.parse_slots('T03b=30,T08=1,retention=1')

    def test_independent_oracle_for_every_tier_and_orientation(self):
        for level in tuple(x for x in tiered.EVAL_TIERS if x != 'T03i'):
            for i in range(128):
                item=tiered.oracle_case(level,20261015,'train',i)
                with self.subTest(level=level,index=i):
                    self.assertEqual(item['y'],tiered.compute_oracle(item))
                    self.assertNotEqual(item['x'],item['y'])
                    self.assertIn('YYYYYY',tiered.tier_prompt(level,item,'forward'))
                    self.assertIn('Answer:',tiered.tier_prompt(level,item,'reordered'))
                    if level!='T03a':
                        self.assertIn('YYYYYY = [compute from transformation]',tiered.tier_prompt(level,item,'forward'))
                    self.assertNotEqual(tiered.tier_prompt(level,item,'forward'),
                                        tiered.tier_prompt(level,item,'reordered'))

    def test_split_namespaces_and_pool_uniqueness(self):
        for level in tiered.TIERS[:-1]:
            training=tiered.generated_pool(level,20261015,256)
            heldout=[tiered.oracle_case(level,20261015,'test',i) for i in range(128)]
            self.assertEqual(len(training),256)
            self.assertTrue({x['id'] for x in training}.isdisjoint(x['id'] for x in heldout))
            self.assertEqual(len({(x['x'],ex.canon(x['data'])) for x in training}),256)
            self.assertTrue({(x['x'],ex.canon(x['data'])) for x in training}.isdisjoint(
                (x['x'],ex.canon(x['data'])) for x in heldout))

    def test_generator_deterministic_and_rejects_unknown(self):
        a=tiered.oracle_case('T03d',20261015,'train',500)
        self.assertEqual(a,tiered.oracle_case('T03d',20261015,'train',500))
        self.assertNotEqual(a['id'],tiered.oracle_case('T03d',20261015,'test',500)['id'])
        with self.assertRaises(ValueError):tiered.oracle_case('T08',20261015,'train',0)
        with self.assertRaises(ValueError):tiered.oracle_case('T03b',20261015,'game_transfer',0)

    def test_existing_trainer_is_unmodified_by_adapter(self):
        levels=ladder.LEVELS;trainable=ladder.TRAINABLE
        original_prompt=ladder.contextual_prompt;original_pools=ladder.eligible_pools
        with tiered.install_tier_adapter(20261015,128):
            self.assertIn('T03h',ladder.TRAINABLE)
            self.assertNotIn('T08',tiered.DEFAULT_QUOTAS)
            self.assertEqual(ladder.read_map.__module__,ladder.__name__)
        self.assertEqual(ladder.LEVELS,levels)
        self.assertEqual(ladder.TRAINABLE,trainable)
        self.assertIs(ladder.contextual_prompt,original_prompt)
        self.assertIs(ladder.eligible_pools,original_pools)


class CutoverContract(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.parent=self.root/'parent';self.parent.mkdir()
        self.out=self.root/'tiered'
        self.db=self.root/'database.sqlite3';self.db.write_bytes(b'fixture sql sha')
        self.src=self.root/'v4.pt';self.src.write_bytes(b'fixture original model sha')
        self.diagnostic=self.root/'diag.json'
        self.diagnostic.write_text(json.dumps({
            'schema':ladder.SCHEMA,
            'generator_sha256':ex.filehash(Path(ladder.__file__)),
            'database_sha256':ex.filehash(self.db),
            'source_checkpoint_sha256':ex.filehash(self.src),
            'levels':list(ladder.DIAG_LEVELS),
            'models':{'source':{'step':104500}}}),encoding='utf8')
        self.parent_config={'schema':ladder.SCHEMA,'trainer_version':ladder.TRAINER_VERSION,
            'source_checkpoint_sha256':ex.filehash(self.src),'database_sha256':ex.filehash(self.db),
            'diagnostic_sha256':ex.filehash(self.diagnostic),'map_sha256':'oldmap',
            'seed':20261015,'lr':1e-5,'op_weight':0.,'cache_per_level':64,
            'retention_cache':64,'refresh_every':5000,
            'phase_definitions':{'t02_focus':{'T02c':2,'retention':1}}}
        (self.parent/'ladder-config.json').write_text(json.dumps(self.parent_config))
        import torch
        self.cp={'schema':ladder.SCHEMA,'trainer_version':ladder.TRAINER_VERSION,
                 'source_sha256':ex.filehash(self.src),'database_sha256':ex.filehash(self.db),
                 'step':220000,'decoder':{'weight':torch.tensor([123.])},
                 'game_operator_head':{'weight':torch.tensor([0.])},
                 'optimizer':{'state':{'hello':'world'}},'torch_rng_cpu':torch.get_rng_state(),
                 'phase_history':[{'phase':'t02_focus','first_step':1}]}
        torch.save(self.cp,self.parent/'latest.pt')
        self.parent_hash=ex.filehash(self.parent/'latest.pt')
        self.args=SimpleNamespace(from_output=self.parent,output=self.out,database=self.db,
                                  source_checkpoint=self.src,diagnostic=self.diagnostic,
                                  steps=240000,slots=tiered.DEFAULT_SLOTS,pool_per_tier=128,cache_per_level=64,
                                  refresh_every=1000,seed=20261015)

    def tearDown(self):self.temp.cleanup()

    def test_byte_identical_fork_and_sealed_cache_rotation(self):
        with tiered.install_tier_adapter(self.args.seed,self.args.pool_per_tier):
            config,created=tiered._create_or_resume(self.args)
        self.assertTrue(created)
        self.assertEqual(ex.filehash(self.parent/'latest.pt'),self.parent_hash)
        self.assertEqual(ex.filehash(self.out/'latest.pt'),self.parent_hash)
        self.assertEqual(config['refresh_every'],1000)
        self.assertEqual(config['cache_per_level'],64)
        spec=json.loads((self.out/'level-map.json').read_text())
        self.assertEqual(spec['t03_tier_pool_size'],128)
        self.assertEqual(spec['t03_tier_oracle_sha256'],ex.filehash(Path(tiered.__file__)))
        self.assertNotIn('T08',spec['phases'][tiered.PHASE])
        with tiered.install_tier_adapter(self.args.seed,self.args.pool_per_tier):
            same,created=tiered._create_or_resume(self.args)
        self.assertFalse(created)
        self.assertEqual(same,config)

    def test_rejects_identity_drift_and_resume_config_change(self):
        with tiered.install_tier_adapter(self.args.seed,self.args.pool_per_tier):
            tiered._create_or_resume(self.args)
            self.args.refresh_every=2000
            with self.assertRaisesRegex(RuntimeError,'T03_TIER_CACHE_CONFIG_CHANGED'):
                tiered._create_or_resume(self.args)
            self.args.refresh_every=1000
            self.args.pool_per_tier=256
            with self.assertRaisesRegex(RuntimeError,'T03_TIER_GENERATOR_OR_POOL_CHANGED'):
                tiered._create_or_resume(self.args)
        self.assertEqual(ex.filehash(self.parent/'latest.pt'),self.parent_hash)


class TieredCpuContinuation(unittest.TestCase):
    def test_real_one_step_training_after_t02_fork(self):
        """A synthetic Qwen capture, actual decoder/optimizer, and two cached tiers."""
        import hashlib
        import torch
        from contextlib import redirect_stdout
        from io import StringIO
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        from tools import nanojev_altreg_math_v3_decoder_experiment as v3
        from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base

        class Parent(torch.nn.Module):
            def extract(self,flat):return torch.zeros(len(flat),16)
            def xy_coord(self,h):return torch.zeros(len(h),2)
        class Frozen(torch.nn.Module):pass
        class Tokenizer:
            def __call__(self,prompt,add_special_tokens=False):
                return {'input_ids':list(range(max(6,len(prompt)//15)))}
        def get_parent(*_):return ({'config':{'input_dim':96,'width':16}},None,Parent(),'parent_sha')
        def capture(_backbone,_tok,_dev,prompts,_max):
            flats=[];sequences=[]
            for prompt in prompts:
                digest=hashlib.sha256(prompt.encode()).digest()
                flats.append(torch.tensor([(digest[i%32]-128)/128 for i in range(96)],dtype=torch.float32))
                sequences.append(torch.tensor([[(digest[(i*7+j)%32]-128)/128 for j in range(24)]
                                               for i in range(8)],dtype=torch.float32))
            return torch.stack(flats),torch.stack(sequences),torch.ones(len(flats),8,dtype=torch.bool)

        with TemporaryDirectory() as temp:
            root=Path(temp);db=root/'db.sqlite3';ex.generate(db,instances_per_game=3)
            torch.manual_seed(7)
            decoder=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
            op=mix.OperationHead(24,32)
            source=root/'source-v4.pt'
            torch.save({'schema':mix.SCHEMA,'step':104500,
                        'config':{'backbone':'qwen','parent_root':str(root),
                                  'parent_checkpoint':'best','parent_sha256':'parent_sha',
                                  'hidden':32,'max_prompt_tokens':512},
                        'head':decoder.state_dict(),'operation_head':op.state_dict()},source)
            diag=root/'diagnostic.json';oldmap=root/'old-map.json'
            oldmap.write_text(json.dumps({'schema':ladder.SCHEMA,'phases':{
                't02_focus':{'T02a':1,'retention':1}}}))
            original=root/'old-t02';original.mkdir()
            args=SimpleNamespace(database=db,source_checkpoint=source,
                experimental_checkpoint=None,levels=','.join(ladder.DIAG_LEVELS),
                limit_per_split=1,include_unchanged=False,report=diag,diagnostic=diag,
                level_map=oldmap,phase='t02_focus',output=original,steps=1,
                save_every=1,report_every=1,lr=1e-4,op_weight=0,seed=20261015,
                cache_per_level=2,retention_cache=2,refresh_every=2,
                max_wall_hours=0,device='cpu',dtype='float32',allow_model_download=False)
            with patch.object(v3,'get_parent',get_parent),patch.object(v3,'full_capture',capture),\
                 patch.object(base.cassette,'load_source',lambda *_:(None,Frozen(),Tokenizer(),'cpu',None)):
                with redirect_stdout(StringIO()):
                    ladder.diagnose(args)
                    ladder.train(args)
                cp=torch.load(original/'latest.pt',map_location='cpu',weights_only=True)
                cp['step']=220000
                torch.save(cp,original/'latest.pt')
                parent_hash=ex.filehash(original/'latest.pt')
                # Fast smoke executes both source-generated arithmetic and the
                # real MC3 full-game T03 prompt through the unchanged optimizer.
                slots={'retention':1,'T03b':1,'T03i':1}
                target=SimpleNamespace(from_output=original,output=root/'tiered',
                    database=db,source_checkpoint=source,diagnostic=diag,
                    steps=220001,save_every=1,report_every=1,max_wall_hours=0.,
                    cache_per_level=1,refresh_every=1000,pool_per_tier=64,
                    slots='retention=1,T03b=1,T03i=1',
                    seed=20261015,device='cpu',dtype='float32',allow_model_download=False)
                with patch.object(tiered,'DEFAULT_QUOTAS',slots),redirect_stdout(StringIO()):
                    out=tiered.train(target)
                self.assertEqual(out['step'],220001)
                self.assertEqual(out['phase'],tiered.PHASE)
                self.assertEqual(parent_hash,ex.filehash(original/'latest.pt'))
                self.assertTrue((target.output/'frozen-capture-cache-t03_tiered.pt').exists())
                trained=torch.load(target.output/'latest.pt',map_location='cpu',weights_only=True)
                self.assertEqual([p['phase'] for p in trained['phase_history']],
                                 ['t02_focus','t03_tiered'])
                self.assertEqual(trained['phase_history'][-1]['quotas'],slots)
                self.assertIn('optimizer',trained)


if __name__=='__main__':unittest.main()
