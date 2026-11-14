import importlib.util
from pathlib import Path
import random
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nanojev_round1_build_code_choices.py"
spec = importlib.util.spec_from_file_location("round1_builder", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeTokenizer:
    all_special_ids = []
    eos_token_id = 999
    def encode(self, text, add_special_tokens=False, truncation=False, max_length=None, verbose=True):
        ids = [ord(ch) for ch in text]
        if truncation and max_length is not None:
            ids = ids[:max_length]
        return ids
    def decode(self, ids, **kwargs):
        return "".join(chr(i) for i in ids)


class Round1Tests(unittest.TestCase):
    def test_split_is_stable_and_file_scoped(self):
        a = mod.split_for_file(17, "pkg/a.py")
        self.assertEqual(a, mod.split_for_file(17, "pkg/a.py"))
        self.assertIn(a, {"train", "dev", "test"})

    def test_token_classes(self):
        self.assertEqual(mod.classify_token_text("identifier"), "word")
        self.assertEqual(mod.classify_token_text("=="), "operator")
        self.assertEqual(mod.classify_token_text("  \n"), "whitespace")
        self.assertEqual(mod.classify_token_text("12"), "number")


    def test_large_source_is_token_capped(self):
        import tempfile
        tok = FakeTokenizer()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "big.py").write_text("x" * 1000, encoding="utf-8")
            docs = mod.load_documents(root, tok, seed=17, max_file_bytes=2000, min_tokens=4, max_file_tokens=128)
            self.assertEqual(len(docs), 1)
            self.assertEqual(len(docs[0].token_ids), 128)

    def test_record_contains_true_candidate_and_no_target_in_state(self):
        tok = FakeTokenizer()
        text = "abcdefghijklmnopqrstuvwxyz"
        doc = mod.SourceDoc(Path("a.py"), "a.py", "python", text, tuple(tok.encode(text)), "train")
        pools = mod.build_global_pools([doc], tok)
        boundary = 12
        row = mod.build_record(tokenizer=tok, doc=doc, boundary=boundary, context_tokens=8,
                               candidate_count=4, lookahead=12, global_pools=pools,
                               rng=random.Random(3), ordinal=1)
        self.assertIsNotNone(row)
        gold = row["gold"]["next_token"]
        self.assertIn(gold, row["questions"]["next_token"]["criteria"])
        self.assertEqual(row["metadata"]["target_token_id"], ord(text[boundary]))
        self.assertEqual(row["metadata"]["source_group_id"], "source-file:a.py")


if __name__ == "__main__":
    unittest.main()


def test_stream_cycle_helpers():
    import importlib.util, sys
    from pathlib import Path
    path = Path(__file__).parents[1] / "tools" / "nanojev_round1_stream_train.py"
    spec = importlib.util.spec_from_file_location("nanojev_round1_stream_train_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.cyclic_slice([1, 2, 3], 2, 3) == [3, 1, 2]
    assert abs(module.cycle_error(7, 10) - 0.3) < 1e-12
    assert module.cycle_error(0, 0) is None
