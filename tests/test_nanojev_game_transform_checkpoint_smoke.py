"""Complete CPU lineage smoke with mocked *frozen* feature capture.

This checks real decoder loss/backprop/checkpoint code, not Qwen accuracy.
No model downloads, no changes to a real NanoJev training run.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import sys
import unittest

# The actual V3/V4 trainer imports sibling tool modules by their script names.
TOOLS=Path(__file__).resolve().parents[1]/'tools'
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))


class CheckpointPipelineSmoke(unittest.TestCase):
    def test_real_decoder_updates_and_checkpoint_resume(self):
        try:
            import torch
            from tools import nanojev_game_transform_experiment as ex
            from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
            from tools import nanojev_altreg_math_v3_decoder_experiment as v3
            from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
        except (ImportError,ModuleNotFoundError) as exc:
            self.skipTest('V3/V4 dependencies unavailable:'+str(exc))
        class Parent(torch.nn.Module):
            def extract(self,flat):return torch.zeros(len(flat),16)
            def xy_coord(self,hidden):return torch.zeros(len(hidden),2)
        class Frozen(torch.nn.Module):pass
        def get_parent(*unused):
            return ({'config':{'input_dim':96,'width':16}},None,Parent(),'fake_parent_sha')
        def full_capture(_model,_tokenizer,_device,prompts,_max_tokens):
            flat=[];seq=[];masks=[]
            for prompt in prompts:
                b=hashlib.sha256(prompt.encode()).digest()
                flat.append(torch.tensor([(b[k%32]-128)/128 for k in range(96)],dtype=torch.float32))
                seq.append(torch.tensor([[(b[(i*11+j)%32]-128)/128 for j in range(24)]
                                          for i in range(8)],dtype=torch.float32))
                masks.append(torch.ones(8,dtype=torch.bool))
            return torch.stack(flat),torch.stack(seq),torch.stack(masks)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/'lib.sqlite3';ex.generate(db,instances_per_game=3)
            torch.manual_seed(7)
            model=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
            operation_head=mix.OperationHead(24,32)
            original_weights={key:value.detach().clone() for key,value in model.state_dict().items()}
            source=root/'source.pt'
            torch.save({'schema':mix.SCHEMA,'step':123,
                'config':{'backbone':'qwen','parent_root':str(root),
                          'parent_checkpoint':'best','parent_sha256':'fake_parent_sha',
                          'hidden':32,'max_prompt_tokens':512},
                'head':model.state_dict(),'operation_head':operation_head.state_dict()},source)
            args=SimpleNamespace(database=db,output=root/'isolated',checkpoint=source,
                seed=20261015,device='cpu',dtype='float32',allow_model_download=False,
                steps=1,training_mix='positive_affine=1,retention=1',eval_limit=2,
                lr=1e-4,op_weight=.1,save_every=1,action='baseline')
            with patch.object(v3,'get_parent',get_parent),\
                 patch.object(v3,'full_capture',full_capture),\
                 patch.object(base.cassette,'load_source',
                    lambda *_: (None,Frozen(),object(),'cpu',None)):
                first=ex.experiment(args)
                self.assertIn('baseline',first)
                args.action='train';second=ex.experiment(args)
                self.assertEqual(second['step'],1)
                cp=torch.load(args.output/'latest.pt',map_location='cpu',weights_only=True)
                self.assertTrue(any(not torch.equal(val,cp['decoder'][name])
                                    for name,val in original_weights.items()))
                args.action='evaluate';third=ex.experiment(args)
                self.assertEqual(third['step'],1)
                self.assertEqual(third['baseline']['evaluation'],first['baseline']['evaluation'])
                # The input source remains byte-identical to its original.
                source_after=torch.load(source,map_location='cpu',weights_only=True)
                self.assertTrue(all(torch.equal(value,source_after['head'][key])
                                    for key,value in original_weights.items()))
                self.assertTrue((args.output/'comparison.json').is_file())

if __name__=='__main__':unittest.main()
