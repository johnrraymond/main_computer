"""Transport regressions with an actual loopback HTTP server, not a real LLM."""
import contextlib
import json
import threading
import time
import unittest
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from tools import mc_encoding_execution as ex


@contextlib.contextmanager
def server(mode):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            b=json.dumps({'models':[{'name':'qwen-test:latest'}]}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Type','application/x-ndjson'); self.end_headers()
            def frame(obj):
                self.wfile.write(json.dumps(obj).encode()+b'\n'); self.wfile.flush()
            try:
                frame({'message':{'thinking':'internal','content':'{"ok":'},'done':False})
                if mode=='timeout': time.sleep(.5); return
                if mode=='eof': return
                if mode=='error': frame({'error':'model failed'}); return
                time.sleep(.01)
                frame({'message':{'content':'true}'},'done':False})
                frame({'message':{'content':''},'done':True,'done_reason':'length' if mode=='length' else 'stop','eval_count':5})
            except OSError: pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=http.serve_forever,kwargs={'poll_interval':.01},daemon=True); t.start()
    try: yield 'http://127.0.0.1:'+str(http.server_port),requests
    finally: http.shutdown(); http.server_close(); t.join()


class StreamTests(unittest.TestCase):
    def test_stream_and_diagnostics(self):
        with server('normal') as (url,req):
            p=ex.Ollama(url,None,2); p.context=8192; p.think=False; log=[]; p.on_progress=log.append
            self.assertEqual(json.loads(p([{'role':'user','content':'test'}])),{'ok':True})
            self.assertTrue(req[0]['stream']); self.assertFalse(req[0]['think']); self.assertEqual(req[0]['options']['num_ctx'],8192)
            self.assertEqual(p.last_diagnostics['phase'],'complete'); self.assertEqual(p.last_diagnostics['frames'],3)
            self.assertEqual(p.last_diagnostics['thinking_chars'],8); self.assertEqual(p.last_diagnostics['eval_count'],5)
            self.assertTrue(any('provider=stream' in line for line in log))
    def test_schema_is_sent_and_reset(self):
        with server('normal') as (url,req):
            p=ex.Ollama(url,None,2)
            ex.ask(p,[{'role':'user','content':'test'}],ex.TURN_SCHEMA)
            self.assertEqual(req[0]['format'],ex.TURN_SCHEMA)
            self.assertIsNone(p.response_schema)
            ex.ask(p,[{'role':'user','content':'test'}],ex.JUDGE_SCHEMA)
            self.assertEqual(req[1]['format'],ex.JUDGE_SCHEMA)
    def test_incomplete_transport(self):
        for mode in ('timeout','eof','error','length'):
            with self.subTest(mode=mode),server(mode) as (url,_):
                p=ex.Ollama(url,None,.12 if mode=='timeout' else 2)
                with self.assertRaises(ex.ProviderBlocked) as caught: p([{'role':'user','content':'test'}])
                d=caught.exception.diagnostics; self.assertGreaterEqual(d['frames'],1); self.assertTrue(d['partial_content'].startswith('{"ok":'))
                self.assertIn('error',d)
    def test_interrupted_investigation_preserves_reads_and_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,_=ex.controls(Path(tmp)); calls=0
            def provider(_):
                nonlocal calls
                calls+=1
                if calls==1: return json.dumps({'actions':[{'tool':'read','path':'src/mathops.py','start':1,'end':5}]})
                raise ex.ProviderBlocked('timeout',{'phase':'streaming','partial_content':'{'})
            result=ex.investigate(provider,s,'prompt','f','docs/contract.md','DOC_TO_CODE')
            self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(result['blocked_turn'],2)
            self.assertEqual(len(result['actions']),2); self.assertEqual(len(result['transcript']),1)
            self.assertEqual(ex.oracle(result,'docs/contract.md')['status'],'NOT_RUN')
    def test_control_blocked_stops_before_repo_scan(self):
        class Fake:
            model='qwen-test:latest'
            def __init__(self,*args): pass
            def __call__(self,messages): raise ex.ProviderBlocked('timeout',{'phase':'awaiting_first_frame'})
        with tempfile.TemporaryDirectory() as tmp,patch.object(ex,'Ollama',Fake):
            self.assertEqual(ex.main(['--root',tmp]),2)
            result=next(Path(tmp).glob('runtime/reports/mc_encoding/execution-*/summary.json'))
            summary=json.loads(result.read_text()); self.assertEqual(summary['status'],'CONTROL_BLOCKED')
            self.assertEqual(len(summary['cases']),1); self.assertEqual(summary['cases'][0]['oracle'],'NOT_RUN')
            self.assertNotIn('inventory',summary)

if __name__=='__main__': unittest.main()
