'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const pageHtml = fs.readFileSync(path.join(root, 'main_computer/web/applications/apps/browser-page.html'), 'utf8');
const browserJs = fs.readFileSync(path.join(root, 'main_computer/web/applications/scripts/browser-page.js'), 'utf8');
const htmlIds = [...pageHtml.matchAll(/\bid="(browser-[^"]+)"/g)].map(match => match[1]);

async function bootBrowser({route = '/applications/browser', url = 'about:blank', title = 'MCEL Browser'} = {}) {
  const origin = 'http://127.0.0.1:8765';
  let hostUrl = new URL(origin + route);
  let childUrl = new URL(origin + route);
  childUrl.searchParams.set('mcel-shell-child', '1');
  const host = {
    document: {body: {dataset: {mcelShellActiveApp: 'browser'}}},
    history: {state: {}, replaceState(_state, _unused, next) { hostUrl = new URL(next, hostUrl); }},
  };
  Object.defineProperty(host, 'location', {get() {return hostUrl;}});
  const child = {
    parent: host,
    history: {state: {}, replaceState(_state, _unused, next) { childUrl = new URL(next, childUrl); }},
    clearTimeout() {}, setTimeout() {return 1;}, addEventListener() {},
  };
  Object.defineProperty(child, 'location', {get() {return childUrl;}});
  const nodes = new Map(htmlIds.map(id => [id, {
    dataset: {}, textContent: '', value: '', disabled: false, src: '', naturalWidth: 0,
    addEventListener() {}, getBoundingClientRect() {return {width: 900, height: 600};},
  }]));
  const requests = [];
  const document = {getElementById(id) {return nodes.get(id);}, activeElement: null, hidden: true};
  const fetch = async (_url, options) => {
    const command = JSON.parse(options.body);
    requests.push(command);
    return {ok: true, json: async () => ({
      ok: true, session: 'a'.repeat(32), base_url: origin + '/', url, title,
      image: 'ZmFrZQ==', can_go_back: false,
    })};
  };
  const sandbox = {window: child, location: childUrl, document, fetch,
    ResizeObserver: class {observe() {}}, URL, URLSearchParams, Set, Promise, console};
  vm.runInNewContext(browserJs, sandbox, {filename: 'browser-page.js'});
  // Allow the startup request, successful response, and update to settle.
  for (let i = 0; i < 8; i++) await Promise.resolve();
  return {nodes, requests, hostUrl, childUrl, host, child};
}

test('successful backend startup renders page title and frame without a false Chromium error', async () => {
  const state = await bootBrowser();
  assert.equal(state.requests[0].operation, 'start');
  assert.equal(state.requests[0].address, '');
  assert.equal(state.nodes.get('browser-page-title').textContent, 'MCEL Browser');
  assert.equal(state.nodes.get('browser-status').textContent, 'Backend Chromium ready');
  assert.equal(state.nodes.get('browser-status').dataset.error, 'false');
  assert.equal(state.nodes.get('browser-image').src, 'data:image/jpeg;base64,ZmFrZQ==');
  assert.equal(state.nodes.get('browser-surface').dataset.ready, 'true');
  assert.equal(state.nodes.get('browser-empty').textContent, '');
});

test('remote navigation renders title and mirrors destination without leaking shell-child marker', async () => {
  const remote = 'https://www.youtube.com/watch?v=abc&t=5';
  const state = await bootBrowser({
    route: '/applications/browser/' + remote,
    url: remote,
    title: 'Video title',
  });
  assert.equal(state.requests[0].address, remote);
  assert.equal(state.nodes.get('browser-page-title').textContent, 'Video title');
  assert.equal(state.nodes.get('browser-status').textContent, remote);
  assert.equal(state.nodes.get('browser-status').dataset.error, 'false');
  assert.equal(state.host.location.href,
    'http://127.0.0.1:8765/applications/browser/' + remote);
  assert.equal(state.child.location.search, '?v=abc&t=5&mcel-shell-child=1');
});
