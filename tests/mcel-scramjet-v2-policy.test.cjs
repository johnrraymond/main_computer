'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const code=fs.readFileSync(path.resolve(__dirname,'../docker/mcel-browser-scramjet-v2/sw.js'),'utf8');

async function proxy(options={}) {
  const callbacks={};let routed=0;
  const response=options.response||new Response('<h1>test</h1>',{
    status:200,headers:{'Content-Type':'text/html','X-Testing-Header':'preserved'}
  });
  const environment={
    URL,Response,Headers,
    importScripts(src){assert.equal(src,'/controller/controller.sw.js');},
    self:{addEventListener(type,handler){callbacks[type]=handler;},skipWaiting(){},clients:{claim(){}}},
    $scramjetController:{shouldRoute(){return true;},route(){routed++;return Promise.resolve(response);}},
  };
  vm.runInNewContext(code,environment);
  const event={request:{mode:options.mode||'navigate',url:options.url||'http://127.0.0.2:39000/~/sj/ctrl/frame/http%3A%2F%2F127.0.0.9%3A48081%2F'},
    respondWith(p){this.out=Promise.resolve(p);}};
  callbacks.fetch(event);
  return {result:await event.out,routed,response};
}

test('rewritten v2 document retains content and gains own-origin COEP and CORP',async()=>{
  const {result,routed}=await proxy();
  assert.equal(routed,1);
  assert.equal(result.status,200);
  assert.equal(await result.text(),'<h1>test</h1>');
  assert.equal(result.headers.get('cross-origin-embedder-policy'),'require-corp');
  assert.equal(result.headers.get('cross-origin-resource-policy'),'same-origin');
  assert.equal(result.headers.get('cross-origin-opener-policy'),'same-origin');
  assert.equal(result.headers.get('x-testing-header'),'preserved');
});

test('subresource fetch passes through unmodified, protecting all other rewrites',async()=>{
  const {response,result}=await proxy({mode:'no-cors'});
  assert.strictEqual(result,response);
});

test('non-proxied URL passes through even when navigation',async()=>{
  const {response,result}=await proxy({url:'http://127.0.0.2:39000/mcel-surface.html'});
  assert.strictEqual(result,response);
});

test('HTTP redirect responses are never rewrapped or rewritten by MCEL',async()=>{
  const response=new Response(null,{status:302,headers:{Location:'/~/sj/target'}});
  const got=await proxy({response});
  assert.strictEqual(got.result,response);
  assert.equal(got.result.headers.get('location'),'/~/sj/target');
});

test('opaque responses are not modified',async()=>{
  const response={type:'opaque',status:0,headers:new Headers(),body:null};
  const got=await proxy({response});
  assert.strictEqual(got.result,response);
});
