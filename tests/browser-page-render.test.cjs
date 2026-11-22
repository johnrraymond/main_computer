'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'main_computer/web/applications/scripts/browser-page.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'main_computer/web/applications/apps/browser-page.html'), 'utf8');
const ids = [...html.matchAll(/\bid="(browser-[^"]+)"/g)].map(match => match[1]);

async function boot({route='/applications/browser'}={}) {
  const origin = 'http://127.0.0.1:8765';
  let hostURL = new URL(origin + route);
  let childURL = new URL(origin + route);
  childURL.searchParams.set('mcel-shell-child', '1');
  const listeners = {};
  const messages = [];
  const requests = [];
  const frameWindow = {postMessage(data, dest) {messages.push({data,dest});}};
  const nodes = new Map(ids.map(id => [id, {
    value:'',src:'',textContent:'',dataset:{},disabled:false,contentWindow:frameWindow,
    addEventListener(type, callback) {this['on'+type]=callback;},
    focus(){},select(){},removeAttribute(name){delete this[name];},
  }]));
  const host = {document:{body:{dataset:{mcelShellActiveApp:'browser'}}}, history:{state:{},replaceState(_s,_unused,path){hostURL=new URL(path,hostURL);}}};
  Object.defineProperty(host,'location',{get:()=>hostURL});
  const child = {
    parent:host,
    history:{state:{},replaceState(_s,_unused,path){childURL=new URL(path,childURL);}},
    addEventListener(type,callback){listeners[type]=callback;},
    setInterval(){return 1;},clearInterval(){},setTimeout(){return 2;},clearTimeout(){},
  };
  Object.defineProperty(child,'location',{get:()=>childURL});
  const document = {getElementById:id=>nodes.get(id),activeElement:null,hidden:false};
  const fetch = async (_url, options) => {
    const command = JSON.parse(options.body);
    requests.push(command);
    return {ok:true,json:async()=>({
      ok:true,engine:'scramjet-docker',session:'a'.repeat(32),sidecar_url:'http://127.0.0.3:49152/',
    })};
  };
  const sandbox = {window:child,history:child.history,location:childURL,document,fetch,URL,URLSearchParams,console};
  vm.runInNewContext(source,sandbox,{filename:'browser-page.js'});
  for(let i=0;i<80;i++)await Promise.resolve();
  return {nodes,requests,messages, listeners,frameWindow,document,getHost:()=>hostURL,getChild:()=>childURL};
}

test('Browser opens one on-demand Docker session and mounts isolated iframe, never a JPEG', async () => {
  const s=await boot();
  assert.equal(s.requests[0].operation,'surface-start');
  assert.equal(s.nodes.get('browser-page-title').textContent,'Scramjet');
  assert.equal(s.nodes.get('browser-surface').dataset.ready,'true');
  assert.equal(s.nodes.get('browser-frame').src.startsWith('http://127.0.0.3:49152/mcel-surface.html?'),true);
  assert.equal(s.nodes.has('browser-image'),false);
  assert.equal(s.nodes.get('browser-status').dataset.error,'false');
});

test('Deep links preserve destination and shell route, while controls target only isolated origin', async () => {
  const remote='https://www.youtube.com/watch?v=abc&t=5';
  const s=await boot({route:'/applications/browser/'+remote});
  const frameURL=new URL(s.nodes.get('browser-frame').src);
  assert.equal(frameURL.searchParams.get('address'),remote);
  assert.equal(frameURL.searchParams.get('parentOrigin'),'http://127.0.0.1:8765');
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'ready'}});
  s.nodes.get('browser-address').value='https://example.org/new';
  s.nodes.get('browser-toolbar').onsubmit({preventDefault(){}});
  assert.equal(s.messages.at(-1).dest,'http://127.0.0.3:49152');
  assert.equal(s.messages.at(-1).data.url,'https://example.org/new');
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'navigating',url:remote}});
  assert.equal(s.getHost().href,'http://127.0.0.1:8765/applications/browser/'+remote);
  assert.equal(s.getChild().search,'?v=abc&t=5&mcel-shell-child=1');
});

test('Cross-origin messages cannot control Browser and closing the last view releases lease', async () => {
  const s=await boot();
  s.listeners.message({origin:'http://127.0.0.4:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'ready'}});
  const before = s.messages.length;
  s.nodes.get('browser-reload').onclick();
  assert.ok(s.messages.slice(before).every(m=>m.dest==='http://127.0.0.3:49152')); 
  s.listeners.pagehide();
  for(let i=0;i<8;i++)await Promise.resolve();
  assert.equal(s.requests.at(-1).operation,'surface-close');
  assert.equal(s.requests.at(-1).session,'a'.repeat(32));
});


test('redirected URL synchronizes MCEL toolbar and canonical shell path',async()=>{
  const old='https://youtube.com/';
  const final='https://www.youtube.com/';
  const s=await boot({route:'/applications/browser/'+old});
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'ready'}});
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'navigating',redirected:true,url:final}});
  assert.equal(s.nodes.get('browser-address').value,final);
  assert.equal(s.getHost().href,'http://127.0.0.1:8765/applications/browser/'+final);
  assert.equal(s.getChild().pathname,'/applications/browser/'+final);
});


test('Enter-submitted navigation updates a focused address bar on HTTP redirect',async()=>{
  const before='https://youtube.com/';
  const after='https://www.youtube.com/';
  const s=await boot({route:'/applications/browser/'+before});
  s.document.activeElement = s.nodes.get('browser-address');
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'navigating',url:after,redirected:true}});
  assert.equal(s.nodes.get('browser-address').value,after);
  assert.equal(s.getHost().href,'http://127.0.0.1:8765/applications/browser/'+after);
});

test('a user editing the next URL is not overwritten by an in-flight redirect',async()=>{
  const before='https://youtube.com/';
  const after='https://www.youtube.com/';
  const s=await boot({route:'/applications/browser/'+before});
  s.document.activeElement = s.nodes.get('browser-address');
  s.nodes.get('browser-address').value='https://next-site.example/';
  s.listeners.message({origin:'http://127.0.0.3:49152',source:s.frameWindow,
    data:{mcelScramjet:true,type:'navigating',url:after,redirected:true}});
  assert.equal(s.nodes.get('browser-address').value,'https://next-site.example/');
  assert.equal(s.getHost().href,'http://127.0.0.1:8765/applications/browser/'+after);
});
