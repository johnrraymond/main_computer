"""Tests for actual source-substitution and causal pixel migration acceptance."""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from tools.mcel_css_migration_frob import (
    STYLE, LAW_PATHS, MigrationRefused, candidate_html, removal_only_html,
    validate_candidate, migration_status,
)
from tools.mcel_css_retirement_frob import ROOT, declarations


def test_three_way_verdict_refuses_trivial_pixel_match():
    assert migration_status(0,0)=='REDUNDANT_NO_MIGRATION_NEEDED'
    assert migration_status(0,42)=='LAW_CHANGE_ALTERS_RENDER'
    assert migration_status(42,0)=='MIGRATION_PIXEL_MATCH'
    assert migration_status(42,2)=='MIGRATION_PIXEL_DIFFERENCE'


def test_in_memory_substitution_preserves_cascade_order():
    p={'baseline':{STYLE:'a{color:red}',next(iter(LAW_PATHS)):'var law = 1;'},
       'proposed':{STYLE:'a{}',next(iter(LAW_PATHS)):'var law = 2;'}}
    old='<head><style>a{color:red}</style><script>\nvar law = 1;\n</script></head>'
    assert removal_only_html(old,p)=='<head><style>a{}</style><script>\nvar law = 1;\n</script></head>'
    assert candidate_html(old,p)=='<head><style>a{}</style><script>\nvar law = 2;\n</script></head>'
    with pytest.raises(MigrationRefused,match='exactly once'):
        candidate_html(old+'<style>a{color:red}</style>',p)


def _zip_fixture(tmp_path: Path, *, css_only=False, extra_css='', no_removal=False,
                 extra_file=None) -> Path:
    original=(ROOT/STYLE).read_text(encoding='utf8')
    if not no_removal:
        first=declarations(original,STYLE)[0]
        new=original[:first['start']]+original[first['end']:]
    else:
        new=original
    new += extra_css
    outfile=tmp_path/'candidate.zip'
    with ZipFile(outfile,'w',ZIP_DEFLATED) as archive:
        archive.writestr(STYLE,new)
        if not css_only:
            law=next(iter(LAW_PATHS))
            archive.writestr(law,(ROOT/law).read_text(encoding='utf8')+'\n/* migration experiment */\n')
        if extra_file:
            archive.writestr(extra_file,'bad')
    return outfile


def test_candidate_requires_real_css_deletion_and_real_law_file(tmp_path):
    patch=_zip_fixture(tmp_path)
    plan=validate_candidate(ROOT,patch)
    assert len(plan['removed'])==1
    assert plan['owners']==['Theme']
    assert plan['source_hashes'][STYLE]
    with pytest.raises(MigrationRefused,match='must change Theme'):
        validate_candidate(ROOT,_zip_fixture(tmp_path,css_only=True))
    with pytest.raises(MigrationRefused,match='no removed declarations|unchanged'):
        validate_candidate(ROOT,_zip_fixture(tmp_path,no_removal=True))
    with pytest.raises(MigrationRefused,match='permits removal only'):
        validate_candidate(ROOT,_zip_fixture(tmp_path,extra_css='\n.foo{color:#ff00ee}'))
    with pytest.raises(MigrationRefused,match='Only Workbench CSS'):
        validate_candidate(ROOT,_zip_fixture(tmp_path,extra_file='main_computer/web/applications/styles/rogue.css'))


def test_unknown_html_missing_law_is_not_accepted(tmp_path):
    patch=_zip_fixture(tmp_path)
    plan=validate_candidate(ROOT,patch)
    html='<style>'+plan['baseline'][STYLE]+'</style>'
    with pytest.raises(MigrationRefused,match='did not embed'):
        candidate_html(html,plan)
