from pathlib import Path
from types import SimpleNamespace
from http import HTTPStatus
import ast
import json
import re


def test_backend_full_document_mode_preserves_structured_proposal_and_accepts_empty_content():
    source = Path(__file__).resolve().parents[1] / 'main_computer/viewport_routes_docs.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ViewportDocsRoutesMixin')
    method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == '_handle_docs_ai')
    ns = {'json':json,'re':re,'HTTPStatus':HTTPStatus,'ChatMessage':SimpleNamespace}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),ns)
    changes = {'html':'<h1>New</h1>', 'metadata':{'name':'New'}, 'layout':{'view':{'zoom':1.25}}}
    captured=[]
    def chat(messages):
        captured.extend(messages)
        return SimpleNamespace(content=json.dumps({'content':'Proposal','suggestion':{'operation':'transform_document','changes':changes}}),provider='test',model='test')
    context={'defaults':{'name':'Untitled Document'},'values':{'name':'Untitled Document'},'explicit':['name'],'prompt':'Banking'}
    req={'instruction':'Create document','preferred_operation':'transform_document','document':{'text':'','html':'<p></p>','metadata':{'name':'Untitled Document'},'creation':context}}
    response=[]
    h=SimpleNamespace(_read_json=lambda:req,_send_json=lambda payload,status=200:response.append((status,payload)),server=SimpleNamespace(signal=lambda *a,**k:None,computer=SimpleNamespace(provider=SimpleNamespace(chat=chat))))
    ns['_handle_docs_ai'](h)
    assert response[0][0]==200 and response[0][1]['suggestion']['changes']==changes
    assert 'explicit' in captured[1].content and 'Banking' in captured[1].content
    assert 'transform_document' in captured[0].content
