"""Patch 14.4: fair, honest, read-only visual-review packet foundations."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import zipfile

from tools.mcel_patch14_visual_quality_frob import (
    ARMS, CASES, WIDTHS, STATES, blind_assignments, build_case,
    stress_snapshot, make_contact_sheet, write_review_gallery,
    create_review_packet, png_size, ROOT,
)

FIXTURES = ROOT/'tools/fixtures/mcel_computer_knowledge'


def test_blinding_is_stable_and_each_arm_once():
    a=blind_assignments('task-manager.processes','normal')
    assert a==blind_assignments('task-manager.processes','normal')
    assert set(a)=={'A','B','C'} and set(a.values())==set(ARMS)
    assert len({tuple(blind_assignments(o,s).values()) for o,_,_ in CASES for s in STATES}) > 1


def test_stress_fixtures_are_deterministic_and_do_not_mutate_sources():
    for observation,fixture,_ in CASES:
        src=json.loads((FIXTURES/fixture).read_text('utf-8'))
        before=deepcopy(src)
        a=stress_snapshot(src,observation)
        b=stress_snapshot(src,observation)
        assert src==before
        assert a==b and a!=before


def test_all_three_real_domains_compile_in_both_states():
    for observation,fixture,purpose in CASES:
        for state in STATES:
            data=build_case(observation,fixture,purpose,state)
            assert data['plan']['readOnly'] is True
            assert data['plan']['productionPromotion'] is False
            assert data['plan']['profile']=='mcel.layout.universal.v1'
            assert set(data['variants'])==set(ARMS)
            assert data['sourceRecordCount']>=1
            assert all(len(data['variants'][arm]['rows'])==data['sourceRecordCount'] for arm in ARMS)
            assert all(all('mcelCanonicalRecord' in r for r in data['variants'][arm]['rows']) for arm in ARMS)


def test_stress_changes_source_not_semantic_layout_signature():
    for observation,fixture,purpose in CASES:
        normal=build_case(observation,fixture,purpose,'normal')
        stress=build_case(observation,fixture,purpose,'stressed')
        assert normal['sourceHash']!=stress['sourceHash']
        assert normal['semanticSignature']==stress['semanticSignature']


def test_packet_does_not_leak_arm_key_and_provides_hashes(tmp_path):
    import base64
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch13_live_adoption_frob import _chrome
    images=[]
    for i,label in enumerate('ABC'):
        n=f'task-manager-processes-normal-390-{label}.png'
        # Standard-library 1x1 PNG fixture; real triptych uses Chromium.
        (tmp_path/n).write_bytes(base64.b64decode(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XHQUAAAAASUVORK5CYII='))
        import hashlib
        images.append({'path':n,'blindLabel':label,'observation':'task-manager.processes',
          'state':'normal','viewport':390,'pixelWidth':390,'pixelHeight':120,
          'pngSha256':hashlib.sha256((tmp_path/n).read_bytes()).hexdigest()})
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            sheet=make_contact_sheet(browser,tmp_path,images,'task-manager.processes normal 390px')
        finally:
            browser.close()
    assert png_size(tmp_path/sheet['path'])==(1440,810)
    named={**sheet,'title':'task-manager.processes normal 390px'}
    write_review_gallery(tmp_path,images,[named])
    packet,review=create_review_packet(tmp_path,images,[named],
        [{'observation':'task-manager.processes','state':'normal'}],
        {'task-manager.processes|normal':{'A':'semantic','B':'blind','C':'baseline'}})
    with zipfile.ZipFile(packet) as z:
        names=set(z.namelist())
        assert 'answer-key.json' not in names
        assert {'review-request.md','review-packet.json','gallery.html','visual-manifest.json'}.issubset(names)
        packet_data=json.loads(z.read('review-packet.json'))
        assert not any('arm' in entry for entry in packet_data['reviewedImages'])
    assert (tmp_path/'answer-key.json').exists()
    assert review['status']=='AWAITING_INDEPENDENT_REVIEW'
    manifest=json.loads((tmp_path/'visual-manifest.json').read_text('utf-8'))
    assert manifest['captures'][0]['capture_scope']=='complete-rendered-component-not-full-browser-page'
    assert 'full_page' not in manifest['captures'][0]
