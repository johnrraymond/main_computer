import tempfile
import json
import subprocess
import shutil
import time
from unittest.mock import patch
import unittest
from pathlib import Path
from tools.mc_encoding_checker import scan, report, prompt, smoke, CandidateIndex, sample_published_prompts, select_prompt_examples, main, Scan, prompt_candidates, refresh_prompt_examples

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

    def test_pruned_environment_and_warning(self):
        (self.root/'src'/'.venv-diagram').mkdir()
        self.put('src/.venv-diagram/skip.py','def skipped(): pass')
        self.put('src/a.py',r'x = "\P"'+'\n')
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always'); s=self.scan()
        self.assertNotIn('src/.venv-diagram/skip.py',s.texts)
        self.assertFalse(any(issubclass(w.category,(SyntaxWarning,DeprecationWarning)) for w in caught))
    @unittest.skipUnless(shutil.which('git'),'git required')
    def test_git_inventory(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.put('.gitignore','ignored/\n')
        (self.root/'src'/'ignored').mkdir()
        self.put('src/ignored/skip.py','def skipped(): pass')
        self.put('src/a.py','def a(): pass')
        self.put('docs/a.md','# A')
        s=self.scan(); self.assertEqual(s.inventory,'git-tracked-and-unignored')
        self.assertNotIn('src/ignored/skip.py',s.texts)
        self.assertIn('src/a.py',s.texts); self.assertIn('docs/a.md',s.texts)
        self.assertIn('src/ignored/skip.py',scan(self.root,['src'],['docs'],include_ignored=True).texts)
    def test_interrupted_legacy_prompt_cleanup_and_batched_findings(self):
        self.put('src/a.py','def a(): pass\ndef b(): pass\n')
        out=self.root/'out'; prompts=out/'prompts'; prompts.mkdir(parents=True)
        orphan=prompts/('a'*20+'.txt'); orphan.write_text('# Bidirectional encoding review\nold interrupted result')
        manual=prompts/('b'*20+'.txt'); manual.write_text('manual evidence')
        r=report(self.scan(),out)
        self.assertEqual(r['prompt_batches'],1); self.assertEqual(len(r['issues']),3)
        self.assertFalse(orphan.exists()); self.assertTrue(manual.exists())
        for issue in r['issues']:
            self.assertIn(issue['finding_id'],(out/issue['prompt']).read_text())
    def test_index_is_reused_and_bounded(self):
        for i in range(300): self.put(f'src/a{i}.py','def sharedtoken(): pass\n')
        self.put('docs/a.md','# sharedtoken\nMust do rareaction.')
        s=self.scan(); idx=CandidateIndex(s); self.assertIn('sharedtoken',idx.common)
        self.assertNotIn('sharedtoken',idx.postings)
        s.index=idx
        with patch('tools.mc_encoding_checker.CandidateIndex',side_effect=AssertionError('rebuilt index')):
            r=report(s,self.root/'out')
        self.assertEqual(r['counts']['UNCHECKED_CODE_SYMBOL'],300)
    def test_prompt_smoke_samples_actual_published_prompts(self):
        for i in range(8):
            self.put(f'src/module{i}.py', f'def action{i}():\n    return {i}\n')
            self.put(f'docs/contract{i}.md', f'# Contract {i}\nMust perform action{i}.\n')
        out=self.root/'out'; r=report(self.scan(),out)
        before={p: p.read_bytes() for p in out.rglob('*') if p.is_file()}
        with patch('tools.mc_encoding_checker.scan',side_effect=AssertionError('must not rescan')), patch('tools.mc_encoding_checker.prompt',side_effect=AssertionError('must not regenerate')):
            self.assertEqual(main(['smoke-prompts','--out',str(out)]),0)
        text=(out/'prompt_smoke.txt').read_text(encoding='utf-8')
        chosen=select_prompt_examples(r['issues'])
        self.assertEqual(len(chosen),10)
        self.assertEqual(len({i['path'] for _,i in chosen}),10)
        self.assertEqual(sum(d=='CODE_TO_DOC' for d,_ in chosen),5)
        self.assertEqual(sum(d=='DOC_TO_CODE' for d,_ in chosen),5)
        for _,i in chosen:
            self.assertIn(i['finding_id'],text)
            self.assertIn('Location: '+i['path'],text)
        self.assertIn('Extract normative requirements',text)
        self.assertIn('Find the governing documentation',text)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(sample_published_prompts(out)['semantic_verification'],'NOT_PERFORMED')
        self.assertEqual(text,(out/'prompt_smoke.txt').read_text(encoding='utf-8'))
    def test_prompt_smoke_fails_on_corrupt_published_prompt(self):
        self.put('src/a.py','def a(): return 1\n')
        self.put('docs/a.md','# A\nMust return 1.\n')
        out=self.root/'out'; r=report(self.scan(),out)
        path=out/r['issues'][0]['prompt']; path.write_text('corrupted')
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            sample_published_prompts(out)
        self.assertFalse((out/'prompt_smoke.txt').exists())
    def test_prompt_smoke_partial_coverage_and_no_fake_mirror(self):
        self.put('src/a.py','def a(): return 1\n')
        out=self.root/'out'; report(self.scan(),out)
        r=sample_published_prompts(out)
        self.assertEqual(r['status'],'PARTIAL_COVERAGE')
        self.assertEqual(r['directions'],{'CODE_TO_DOC':1})
        self.assertEqual(r['examples'],1)
    def test_prompt_smoke_real_missing_solution_priority(self):
        self.put('docs/missing.md','<!--@-encoding\nid: MISSING\ntype: requirement\n@-->\n# Missing\nMust deliver a result.\n')
        self.put('src/a.py','def a(): return 1\n')
        out=self.root/'out'; report(self.scan(),out)
        result=sample_published_prompts(out,2)
        self.assertEqual(result['status'],'READY_FOR_REVIEW')
        self.assertIn('REQUIREMENT_WITHOUT_IMPLEMENTATION',result['kinds'])
        self.assertIn('Search for an actual implementation',(out/'prompt_smoke.txt').read_text())

    def test_exact_references_bidirectional_and_no_arbitrary_fallback(self):
        self.put('src/a.py','def rare_action(): return 1\n')
        self.put('src/irrelevant.py','def another(): return 2\n')
        self.put('docs/mapping.md','# Contract\n`src/a.py` implements this; `rare_action` is its API.\n')
        self.put('docs/unrelated.md','# Another subject\n')
        s=self.scan()
        for path,expected in [('src/a.py','docs/mapping.md'),('docs/mapping.md','src/a.py')]:
            i=next(x for x in s.issues if x['path']==path and x['kind']!='UNCHECKED_CODE_SYMBOL')
            body=prompt(s,i); found=prompt_candidates(body)['primary']
            self.assertEqual(found[0]['path'],expected)
            self.assertGreaterEqual(found[0]['tier'],3)
            self.assertIn('items: an array',body)
            self.assertIn('Each item needs its own verdict',body)
            self.assertIn('Missing evidence or incomplete search means UNVERIFIED',body)
        i=next(x for x in s.issues if x['path']=='src/irrelevant.py')
        self.assertEqual(prompt_candidates(prompt(s,i))['primary'],[])
        self.assertIn('absence of candidates is NOT evidence',prompt(s,i))

    def test_filename_windows_relative_and_dotted_module(self):
        (self.root/'src'/'nested').mkdir()
        self.put('src/nested/runner.py','def exact_action(): return 1\n')
        self.put('docs/a.md',r'Run `.\nested\runner.py`; import src.nested.runner; call exact_action.'+'\n')
        s=self.scan(); i=next(x for x in s.issues if x['path']=='docs/a.md')
        hit=prompt_candidates(prompt(s,i))['primary'][0]
        self.assertEqual(hit['path'],'src/nested/runner.py'); self.assertGreaterEqual(hit['tier'],3)
        self.put('docs/a.md','The exact_action function provides the contract.\n')
        s=self.scan(); i=next(x for x in s.issues if x.get('symbol')=='exact_action')
        self.assertEqual(prompt_candidates(prompt(s,i))['primary'][0]['reason'],'exact-symbol')

    @unittest.skipUnless(shutil.which('git'),'git required')
    def test_ignored_docs_and_machine_spec_inventoried_once(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.put('.gitignore','docs/\n')
        self.put('src/a.py','def a(): return 1\n')
        self.put('docs/a.md','# Contract\n`src/a.py` implements it.\n')
        self.put('docs/a.json','{"requirement":"must return one"}')
        s=scan(self.root,['.'],['docs'])
        self.assertEqual(s.kinds['docs/a.json'],'doc')
        self.assertEqual(s.kinds['docs/a.md'],'doc')
        self.assertEqual([i['kind'] for i in s.issues if i['path']=='docs/a.json'],['UNCHECKED_DOCUMENT'])

    def test_original_ten_example_retrieval_replay(self):
        corpus=json.loads((Path(__file__).parent/'fixtures'/'mc_encoding_prompt_replay_v1.json').read_text(encoding='utf-8'))
        s=Scan(self.root); s.texts={p:'' for p in corpus['scope_files']}
        # Only the ten actual excerpts are available; other entries are name-only.
        for example in corpus['examples']:
            i=example['finding']; s.texts[i['path']]='\n'*(i['line']-1)+example['excerpt']
        for n,example in enumerate(corpus['examples'],1):
            i=example['finding']; s.issues.append({k:v for k,v in i.items() if k!='finding_id'})
            with self.subTest(example=n,path=i['path']):
                body=prompt(s,i); c=prompt_candidates(body)
                found={x['path']:x for group in c.values() for x in group}
                self.assertFalse(set(example['expected_candidates'])-set(found))
                self.assertIn('Each item needs its own verdict',body)
                if n<=5: self.assertTrue(all(x['role']=='documentation' for x in c['primary']))
                else: self.assertTrue(all(x['role']=='implementation' for x in c['primary']))
                if n in {7,8,10}:
                    for p in example['expected_candidates']: self.assertGreaterEqual(found[p]['tier'],3)
                self.assertFalse(any(x['path'].startswith(('game_projects/','health_app_')) for x in found.values()))
        anchors=json.loads((Path(__file__).parents[1]/'tools'/'fixtures'/'mc_encoding_prompt_smoke_v1.json').read_text(encoding='utf-8'))
        result=refresh_prompt_examples(s,anchors,self.root/'out')
        self.assertEqual(result['retrieval_passed'],10)
        self.assertEqual(result['semantic_verification'],'NOT_PERFORMED')
        self.assertEqual(result['directions'],{'CODE_TO_DOC':5,'DOC_TO_CODE':5})
        self.assertFalse((self.root/'out'/'report.json').exists())
        with patch('tools.mc_encoding_checker.scan',return_value=s), patch('tools.mc_encoding_checker.report',side_effect=AssertionError('must not publish full scan')), patch('tools.mc_encoding_checker.sample_published_prompts',side_effect=AssertionError('must regenerate')):
            self.assertEqual(main(['smoke-prompts','--refresh','--root',str(self.root),'--out',str(self.root/'out')]),0)
        print('MC_ENCODING_TEN_REPLAY PASS 10/10 (excerpt + path inventory only)')

    def test_refresh_no_substitution_or_report_mutation(self):
        self.put('src/a.py','def a(): return 1\n')
        self.put('docs/a.md','# Contract\n`src/a.py` implements it.\n')
        s=self.scan(); out=self.root/'out'; report(s,out)
        before={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
        anchors=[{'finding_id':'original','kind':'UNCHECKED_CODE','path':'src/missing.py','expected_candidates':[]}]
        result=refresh_prompt_examples(s,anchors,out)
        self.assertEqual(result['status'],'RETRIEVAL_INCOMPLETE')
        self.assertEqual(result['results'][0]['status'],'BLOCKED')
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_repository_scale(self):
        for i in range(2000):
            self.put(f'src/module_{i}.py',''.join(f'def action_{i}_{j}():\n    return {j}\n\n' for j in range(20)))
        for i in range(500): self.put(f'docs/module_{i}.md',f'# Module {i}\nThe system shall perform action_{i}_0.\n')
        start=time.monotonic(); s=self.scan(); scanned=time.monotonic()-start
        out=self.root/'out'; r=report(s,out); elapsed=time.monotonic()-start
        self.assertEqual(len(r['scope_files']),2500)
        self.assertEqual(len(r['issues']),42500)
        self.assertEqual(r['counts'],{'UNCHECKED_CODE':2000,'UNCHECKED_CODE_SYMBOL':40000,'UNCHECKED_DOCUMENT':500})
        self.assertEqual(r['prompt_batches'],2500)
        self.assertEqual(len(list((out/'prompts').glob('*.txt'))),2500)
        self.assertLess(elapsed,120,'repository-scale regression')
        print(f'MC_ENCODING_SCALE PASS files=2500 findings=42500 batches=2500 scan={scanned:.2f}s total={elapsed:.2f}s')

if __name__=='__main__': unittest.main()
