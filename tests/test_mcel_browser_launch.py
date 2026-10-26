"""Portable browser resolution: Windows Edge/Chrome need not be on PATH."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from tools import mcel_browser_launch as launch


class FakeChromium:
    def __init__(self, accepts):
        self.accepts=accepts
        self.calls=[]

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        channel=kwargs.get('channel')
        executable=kwargs.get('executable_path')
        name='explicit' if executable else channel or 'bundled'
        if name not in self.accepts:
            raise RuntimeError('Browser binary unavailable in this fixture')
        return SimpleNamespace(version='130.0', name=name)


class BrowserDiscoveryTests(unittest.TestCase):
    def test_bundled_playwright_preferred_without_path(self):
        engine=FakeChromium({'bundled'})
        with patch.object(launch.shutil,'which',return_value=None):
            browser,proof=launch.launch_chromium(SimpleNamespace(chromium=engine),environ={})
        self.assertEqual(browser.name,'bundled')
        self.assertEqual(proof['method'],'playwright-chromium')
        self.assertEqual(engine.calls[0]['headless'],True)

    def test_windows_edge_fallback_without_path(self):
        engine=FakeChromium({'msedge'})
        with patch.object(launch.shutil,'which',return_value=None):
            browser,proof=launch.launch_chromium(SimpleNamespace(chromium=engine),environ={})
        self.assertEqual(browser.name,'msedge')
        self.assertEqual(proof['method'],'installed-msedge')
        self.assertEqual([c.get('channel') for c in engine.calls], [None,'msedge'])

    def test_google_chrome_fallback_without_path(self):
        engine=FakeChromium({'chrome'})
        with patch.object(launch.shutil,'which',return_value=None):
            browser,proof=launch.launch_chromium(SimpleNamespace(chromium=engine),environ={})
        self.assertEqual(proof['method'],'installed-chrome')
        self.assertEqual(len(engine.calls),3)

    def test_explicit_path_is_authoritative_and_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'chrome.exe'
            exe.write_bytes(b'fixture-only')
            engine=FakeChromium({'bundled'})
            with self.assertRaisesRegex(launch.BrowserLaunchError,'Configured Chromium executable failed'):
                launch.launch_chromium(SimpleNamespace(chromium=engine),executable=str(exe),environ={})
            self.assertEqual(len(engine.calls),1)
            self.assertEqual(engine.calls[0]['executable_path'],str(exe))

    def test_env_override_works_without_path(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'chrome.exe';exe.write_bytes(b'fixture-only')
            engine=FakeChromium({'explicit'})
            browser,proof=launch.launch_chromium(SimpleNamespace(chromium=engine),
                         environ={'MCEL_CHROMIUM_EXECUTABLE':str(exe)})
            self.assertEqual(proof['method'],'environment-executable')
            self.assertEqual(browser.name,'explicit')

    def test_invalid_env_path_is_not_ignored(self):
        with self.assertRaisesRegex(launch.BrowserLaunchError,'does not exist'):
            launch.launch_chromium(SimpleNamespace(chromium=FakeChromium({'bundled'})),
                environ={'MCEL_CHROMIUM_EXECUTABLE':'C:/missing/chrome.exe'})

    def test_no_browser_explains_recovery(self):
        with patch.object(launch.shutil,'which',return_value=None):
            with self.assertRaises(launch.BrowserLaunchError) as case:
                launch.launch_chromium(SimpleNamespace(chromium=FakeChromium(set())),environ={})
        message=str(case.exception)
        self.assertIn('python -m playwright install chromium',message)
        self.assertIn('Edge/Chrome',message)

    def test_frob_design_review_forwards_browser_override(self):
        from tools import mcel_layout_frob, mcel_design_critic_frob
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(mcel_design_critic_frob, 'main', return_value=0) as invoke:
                result=mcel_layout_frob.main(['--mode','design-review',
                    '--chromium-executable','C:/example/browser.exe',
                    '--output',folder])
            self.assertEqual(result,0)
            arguments=invoke.call_args.args[0]
            self.assertEqual(arguments[arguments.index('--chromium-executable')+1],
                             'C:/example/browser.exe')

    def test_windows_program_files_locations_are_considered(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Microsoft Edge'/'Application'/'msedge.exe'
            exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture-only')
            loc=launch._windows_candidates({'PROGRAMFILES':folder})
            self.assertIn(str(exe),[path for _,path in loc])

if __name__=='__main__':unittest.main()
