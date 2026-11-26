"""Deterministic tests for the smoke oracle, not substitutes for live AI."""
from copy import deepcopy

from tools.mcel_document_ai_state_smoke import evaluate_full, LAYOUT, CUSTOM_LAYOUT


def fixture():
    target = {'metadata': {'name': 'Title', 'date': '1929-09-04', 'type': 'memorandum',
                           'author': 'Author', 'description': 'Description'},
              'headings': ['Heading'], 'paragraphs': ['Strong Italic'],
              'bold': ['Strong'], 'italic': ['Italic'], 'listItems': ['One', 'Two'],
              'original': 'original'}
    before = {'metadata': {}, 'headings': [], 'paragraphs': ['original'], 'bold': [],
              'italic': [], 'listItems': [], 'text': 'original', 'html': '<p>original</p>',
              'draft': '<p>original</p>', 'layout': {'layout': {'mode': 'preset', 'preset': 'letter',
              'custom': None, 'margins': dict.fromkeys(('top','right','bottom','left'), 96)},
              'view': {'mode': 'paged', 'zoom': 1, 'showPageBreaks': True}}}
    after = deepcopy(before)
    after.update({k: v for k, v in target.items() if k != 'original'})
    after.update(text='Heading Strong Italic One Two', html='<h1>Heading</h1>', draft='<h1>Heading</h1>')
    after['layout']={'layout': {'mode':'preset', 'preset':'a4', 'custom':None,
                     'margins': {'top':72,'right':80,'bottom':88,'left':64}},
                     'view': {'mode':'endless','zoom':1.25,'showPageBreaks':False}}
    return before, after, target


def test_full_oracle_requires_authoritative_applied_state():
    before, after, target = fixture()
    assert all(v['status']=='PASS' for v in evaluate_full(before,after,target).values())
    after['metadata']={}
    after['text']+=' Title 1929-09-04 memorandum Author Description'
    checks=evaluate_full(before,after,target)
    assert checks['metadata.name']['status']=='FAIL'
    assert checks['metadata.date']['reason']=='AUTHORITATIVE_STATE_NOT_EXPOSED'
    assert checks['metadata.type']['status']=='FAIL'


def test_unchanged_or_plain_text_only_cannot_pass():
    before, after, target = fixture()
    checks=evaluate_full(before,before,target)
    assert checks['content.headings']['status']=='FAIL'
    assert checks['layout.layout.preset']['status']=='FAIL'
    assert checks['content.original_removed']['status']=='FAIL'
    after['bold']=[]
    after['draft']='wrong'
    assert evaluate_full(before,after,target)['content.bold']['status']=='FAIL'
    assert evaluate_full(before,after,target)['content.state_binding']['status']=='FAIL'


def test_custom_dimensions_are_independent_checks():
    before, after, target = fixture()
    after['layout']['layout'].update(mode='custom',preset=None,custom={'widthPx':900,'heightPx':1200})
    checks=evaluate_full(before,after,target,CUSTOM_LAYOUT)
    assert all(v['status']=='PASS' for v in checks.values())
    after['layout']['layout']['custom']['heightPx']=1100
    assert evaluate_full(before,after,target,CUSTOM_LAYOUT)['layout.layout.custom.heightPx']['status']=='FAIL'
