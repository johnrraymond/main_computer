'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '../docker/mcel-browser-scramjet/mcel-surface.js'), 'utf8');
const parentOrigin = 'http://127.0.0.1:8765';

async function simulate({failServiceWorker = false, hangWithExistingWorker = false, badMime = false, failController = false} = {}) {
  const messages = [];
  const handlers = new Map();
  const scripts = [];
  const order = [];
  const parent = {postMessage: (payload, target) => messages.push({payload, target})};
  const errorNode = {textContent: '', style: {display: 'none'}};
  const iframeListeners = {};
  const iframe = {id: '', src: '', contentWindow: {location:{href:'about:blank'}},
    addEventListener(type,fn){iframeListeners[type]=fn;}};
  const frame = {frame: iframe, go(url) {scripts.push(url);}, addEventListener(){}, reload(){}, back(){}, forward(){}};
  const doc = {getElementById: id => id === 'mcel-surface-error' ? errorNode : null,
    body: {appendChild(child) {assert.equal(child, iframe);}}};
  const window = {parent, addEventListener: (type, handler) => handlers.set(type, handler),
    async mcelPrepareScramjetDB() {order.push('db-ready');}};
  const env = {
    window, document: doc, location: {search: '?parentOrigin=' + encodeURIComponent(parentOrigin) +
      '&address=' + encodeURIComponent('https://example.org/'),
      protocol:'http:', host:'127.0.0.3:45678', origin:'http://127.0.0.3:45678', href:'http://127.0.0.3:45678/mcel-surface.html'},
    $scramjetLoadController: () => ({ScramjetController: class {
      async init(){order.push('controller-init'); if (failController) throw Error('controller failed');}
      createFrame(){return frame;}
    }}),
    BareMux: {BareMuxConnection: class {
      getTransport(){return Promise.resolve('/libcurl/index.mjs');}
      setTransport(){return Promise.resolve();}
    }},
    fetch: async url => {assert.equal(url, '/sw.js'); return {
      ok:true, status:200, headers:{get: key => key === 'Content-Type' ? (badMime ? 'text/html' : 'text/javascript; charset=utf-8') : null},
      text: async () => 'const {ScramjetServiceWorker}=$scramjetLoadWorker();',
    };},
    navigator: {serviceWorker: {
      getRegistration(){return Promise.resolve(hangWithExistingWorker ? {active:{scriptURL:'http://127.0.0.3:45678/sw.js'}} : null);},
      register(){order.push('sw-registration');return failServiceWorker ? Promise.reject(Error('SW broke')) :
        hangWithExistingWorker ? new Promise(()=>{}) : Promise.resolve({});},
    }},
    isSecureContext:true,
    URL, URLSearchParams, console: {error(){}},
    setTimeout: (fn,ms) => setTimeout(fn, hangWithExistingWorker && ms === 15000 ? 0 : ms), clearTimeout,
  };
  vm.runInNewContext(source, env, {filename:'mcel-surface.js'});
  for(let i = 0;i < 60;i++) await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
  function dispatch(type, data) {
    const fn = handlers.get(type);
    assert.ok(fn, 'Missing listener for ' + type);
    fn({origin:parentOrigin,source:parent,data:{mcelBrowserControl:true,...data}});
  }
  return {messages,handlers,errorNode,iframe,iframeListeners,scripts,dispatch,order};
}

test('isolated surface sends deterministic staged ready handshake and navigates fixture',async()=>{
  const e = await simulate();
  assert.ok(e.messages.some(m=>m.payload.stage==='service-worker-preflight'));
  assert.ok(e.messages.some(m=>m.payload.stage==='service-worker-registration'));
  assert.ok(e.messages.some(m=>m.payload.type==='ready'));
  assert.ok(e.scripts.includes('https://example.org/'));
  assert.ok(e.messages.every(m=>m.target===parentOrigin));
  const old = e.messages.length;
  e.dispatch('message', {action:'probe'});
  assert.ok(e.messages.slice(old).some(m=>m.payload.type==='ready'));
});

test('service worker error is reported to MCEL, no premature readiness',async()=>{
  const e = await simulate({failServiceWorker:true});
  assert.ok(e.messages.some(m=>m.payload.type==='error' && /SW broke/.test(m.payload.error) && /200\/text\/javascript/.test(m.payload.error)));
  assert.ok(!e.messages.some(m=>m.payload.type==='ready'));
  assert.match(e.errorNode.textContent,/SW broke/);
});


test('preflight refuses invalid worker MIME before declaring ready',async()=>{
  const e = await simulate({badMime:true});
  assert.ok(e.messages.some(m=>m.payload.type==='error' && /Invalid \/sw\.js response: 200\/text\/html/.test(m.payload.error)));
  assert.ok(!e.messages.some(m=>m.payload.type==='ready'));
});

test('registration update timeout may reuse verified active Scramjet worker',async()=>{
  const e = await simulate({hangWithExistingWorker:true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.ok(e.messages.some(m=>m.payload.type==='ready'));
  assert.ok(e.scripts.includes('https://example.org/'));
});


test('database schema and controller finish BEFORE worker registration',async()=>{
  const e = await simulate();
  assert.deepEqual(e.order.slice(0,3),['db-ready','controller-init','sw-registration']);
});

test('controller failure prevents any Service Worker attempt',async()=>{
  const e = await simulate({failController:true});
  assert.ok(e.messages.some(m=>m.payload.type==='error' && /controller failed/.test(m.payload.error)));
  assert.ok(!e.order.includes('sw-registration'));
  assert.ok(!e.messages.some(m=>m.payload.type==='ready'));
});


test('HTTP 301 final Scramjet frame URL is reported to trusted MCEL parent',async()=>{
  const e = await simulate();
  const final='http://127.0.0.9:48083/redirected?source=301';
  const encoded=encodeURIComponent(final);
  e.iframe.contentWindow.location.href = 'http://127.0.0.3:45678/scramjet/' + encoded;
  const prior = e.messages.length;
  e.iframeListeners.load();
  const updates = e.messages.slice(prior).filter(m => m.payload.type==='navigating');
  assert.equal(updates.length,1);
  assert.equal(updates[0].payload.url,final);
  assert.equal(updates[0].payload.redirected,true);
  assert.equal(updates[0].target,parentOrigin);
  e.iframeListeners.load();
  assert.equal(e.messages.filter(m=>m.payload.type==='navigating'&&m.payload.url===final).length,1);
});

test('non-web or unrecognized proxied frame URLs are not reported as destinations',async()=>{
  const e = await simulate();
  for (const uri of [
    'http://127.0.0.3:45678/other-route',
    'http://127.0.0.3:45678/scramjet/javascript%3Aalert(1)',
    'http://127.0.0.3:45678/scramjet/https%3A%2F%2Fusername%3Apassword%40evil.example%2F',
  ]) {
    e.iframe.contentWindow.location.href = uri;
    const before = e.messages.filter(m=>m.payload.type==='navigating').length;
    e.iframeListeners.load();
    assert.equal(e.messages.filter(m=>m.payload.type==='navigating').length,before);
  }
});

test('Scramjet virtual location shim reports final URL after a redirect',async()=>{
  const e = await simulate();
  e.iframe.contentWindow.location.href = 'https://www.youtube.com/watch?v=abc';
  e.iframeListeners.load();
  assert.ok(e.messages.some(m=>m.payload.type==='navigating' && m.payload.url === 'https://www.youtube.com/watch?v=abc'));
});
