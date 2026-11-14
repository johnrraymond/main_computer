"""Conversation-mode notebook layout: canonical flow ownership and containment.

The semantic layout tree, not a Calculator-only CSS offset, owns the
conversation switch and notebook. Exercise the real promoted app and engine.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_chat_console_host import compile_chat_console
from tests.test_mcel_chat_console_p4_canonical_mount import page_with_storage
from tools.mcel_chat_console_layout_oracle import _launch


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = _launch(pw)
        try:
            yield b
        finally:
            b.close()


def observe(page):
    return page.evaluate('''() => {
      const host=document.querySelector('[data-chat-console-embed="calculator"]');
      const box=selector=>{
        const el=host.querySelector(selector), r=el.getBoundingClientRect(),css=getComputedStyle(el);
        return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,
          display:css.display,position:css.position,overflowY:css.overflowY,
          scrollHeight:el.scrollHeight,clientHeight:el.clientHeight};
      };
      return {status:host.dataset.mcelChatLayoutStatus,
        authority:host.querySelector('.chat-console-shell').dataset.mcelCanonicalLayoutAuthority,
        workspace:box('.chat-thread-workspace'),header:box('.chat-thread-current-bar'),
        switcher:box('.chat-conversation-switch'),notebook:box('.chat-console-notebook'),
        composer:box('[data-chat-conversation-role="composer"]'),
        switchRole:host.querySelector('.chat-conversation-switch').dataset.mcelChatRole,
        scrollWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth};
    }''')


def assert_allocated(snapshot):
    assert snapshot['status'] == 'pass', snapshot
    assert snapshot['authority'] == 'mcel-layout-tree.v1'
    assert snapshot['switchRole'] == 'view-switch'
    bar, toggle, notebook, work = (
        snapshot['header'], snapshot['switcher'], snapshot['notebook'], snapshot['workspace'])
    assert bar['bottom'] <= toggle['top'] + 1, snapshot
    assert toggle['bottom'] <= notebook['top'] + 1, snapshot
    for child in (bar, toggle, notebook):
        assert child['left'] >= work['left'] - 1, snapshot
        assert child['right'] <= work['right'] + 1, snapshot
    assert toggle['width'] >= 40 and toggle['height'] >= 24, snapshot
    assert notebook['overflowY'] == 'auto', snapshot
    assert snapshot['scrollWidth'] <= snapshot['viewportWidth'] + 1, snapshot


def test_authored_optional_view_switch_belongs_to_shared_workspace():
    tree=compile_chat_console()['layouts'][0]['layoutTree']['root']
    thread=next(n for n in tree['children'] if n['id']=='thread-layout')
    workspace=next(n for n in thread['children'] if n['id']=='thread-workspace')
    assert workspace['auxiliarySelectors']==['.chat-conversation-switch']
    assert workspace['auxiliaryPlacement']=='flow'
    assert [n['id'] for n in workspace['children']]==['active-thread','notebook']


@pytest.mark.parametrize('width', [320,390,500,950,1272])
def test_real_promoted_calculator_conversation_allocates_notebook_regions(browser, width):
    page=page_with_storage(browser,render_expanded_page(promoted=True),width)
    try:
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        before=observe(page)
        assert_allocated(before)
        switch=page.locator('[data-chat-conversation-switch]')
        switch.click()
        assert switch.inner_text() == 'Conversation view'
        assert page.locator('[data-chat-console-embed]').get_attribute('data-mcel-chat-layout-status')=='pass'
        switch.click()
        assert switch.inner_text() == 'Notebook tools'
        after=observe(page)
        assert_allocated(after)
    finally:
        page.close()


def test_scroll_is_owned_by_notebook_and_does_not_move_header_or_switch(browser):
    page=page_with_storage(browser,render_expanded_page(promoted=True),500)
    try:
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        before=observe(page)
        assert_allocated(before)
        response=page.evaluate('''() => {
          const host=document.querySelector('[data-chat-console-embed="calculator"]');
          const notebook=host.querySelector('.chat-console-notebook');
          const first=notebook.querySelector('.chat-cell');
          if(!first)throw Error('canonical chat cell missing');
          for(let i=0;i<14;i++){
            const clone=first.cloneNode(true);
            clone.dataset.cellId='layout-probe-'+i;
            clone.removeAttribute('id');
            notebook.appendChild(clone);
          }
          const result=MainComputerMCELChatConsole.refreshCanonical(host,true);
          notebook.scrollTop=notebook.scrollHeight;
          return {status:result.status,scrollTop:notebook.scrollTop,
            scrollHeight:notebook.scrollHeight,clientHeight:notebook.clientHeight};
        }''')
        assert response['status']=='pass',response
        assert response['scrollHeight']>response['clientHeight'],response
        assert response['scrollTop']>0,response
        after=observe(page)
        assert_allocated(after)
        assert abs(after['header']['top']-before['header']['top'])<=1
        assert abs(after['switcher']['top']-before['switcher']['top'])<=1
    finally:
        page.close()


def test_out_of_flow_switch_is_refused_by_canonical_layout(browser):
    page=page_with_storage(browser,render_expanded_page(promoted=True),500)
    try:
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        result=page.evaluate('''() => {
          const host=document.querySelector('[data-chat-console-embed="calculator"]');
          host.querySelector('.chat-conversation-switch').style.position='absolute';
          const attempt=MainComputerMCELChatConsole.refreshCanonical(host,true);
          return {status:attempt.status,reason:attempt.reason,
            flagged:host.dataset.mcelChatLayoutStatus,
            inert:host.querySelector('.chat-console-shell').inert};
        }''')
        assert result['status']=='refused',result
        assert result['flagged']=='refused',result
        assert result['inert'],result
    finally:
        page.close()
