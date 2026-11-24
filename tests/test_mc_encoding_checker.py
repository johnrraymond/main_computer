import tempfile
import unittest
from pathlib import Path
from tools.mc_encoding_checker import scan, report, prompt, smoke

class EncodingCheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'src').mkdir(); (self.root/'docs').mkdir()
    def put(self,path,text):
        (self.root/path).write_text(text,encoding='utf-8')
    def scan(self):
        return scan(self.root,['src'],['docs'])
    def test_smoke(self):
        smoke()
    def test_python_string_and_markdown_fence_not_declarations(self):
        self.put('src/a.py', 'example = """\n#@-encoding\n# id: FAKE\n#@-end\n"""\n')
        self.put('docs/a.md','```markdown\n<!--@-encoding\nid: FAKE2\n@-->\n```\n')
        self.assertEqual(self.scan().nodes,{})
    def test_missing_symbol_and_mirror_context(self):
        self.put('src/a.py','#@-encoding\n# id: source\n# symbol: missing\n# implements: R1\n#@-end\ndef other():\n    return 1\n')
        self.put('docs/a.md','<!--@-encoding\nid: R1\nimplemented-by: source\n@-->\n## Contract\nMust return 9.\n')
        s=self.scan(); issue=next(x for x in s.issues if x['kind']=='MISSING_SOURCE_SYMBOL')
        self.assertIn('Must return 9',prompt(s,issue))
        self.assertEqual(s.pairs[0]['status'],'STRUCTURAL_DISAGREEMENT')
    def test_partial_document_coverage(self):
        self.put('docs/a.md','<!--@-encoding\nid: R1\n@-->\n## Encoded\nMust do A.\n## Not encoded\nMust do B.\n')
        s=self.scan(); issues=[x for x in s.issues if x['kind']=='UNCHECKED_DOCUMENT_SECTION']
        self.assertEqual([x['heading'] for x in issues],['Not encoded'])
        self.assertIn('Must do B',prompt(s,issues[0]))
    def test_report_replaces_only_old_managed_prompts(self):
        self.put('src/a.py','def a():\n    return 1\n')
        s=self.scan(); out=self.root/'out'; first=report(s,out)
        unrelated=out/'prompts'/'manual.txt'; unrelated.write_text('preserve')
        (self.root/'src'/'a.py').unlink(); report(self.scan(),out)
        self.assertTrue(unrelated.exists()); self.assertTrue(all(not (out/i['prompt']).exists() for i in first['issues']))
    def test_unclosed_encoding(self):
        self.put('src/a.js','/*@-encoding\nid: broken\n')
        self.assertIn('MALFORMED_ENCODING',{x['kind'] for x in self.scan().issues})
    def test_duplicate_ids(self):
        self.put('src/a.js','/*@-encoding\nid: same\n@*/\n')
        self.put('docs/a.md','<!--@-encoding\nid: same\n@-->\n')
        self.assertIn('DUPLICATE_ID',{x['kind'] for x in self.scan().issues})

if __name__=='__main__': unittest.main()
