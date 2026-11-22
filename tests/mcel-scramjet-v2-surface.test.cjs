'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../docker/mcel-browser-scramjet-v2/mcel-surface.js'),'utf8');

async function boot(options={}){
  const messages=[], actions={}, listeners=new Map();
  const parent={postMessage(m,origin){messages.push({m,origin});}};
  const err={textContent:'',style:{display:'none'}};
  const wnd={parent,addEventListener:(type,fn)=>listeners.set(type,fn)};
  class UrlWatcherPlugin{constructor(fn){this.callback=fn;}}
  class HttpCachePlugin{}
  class Controller{
    constructor(settings){actions.ctor=settings;}
    wait(){return Promise.resolve();}
    createFrame(element,opts){
      actions.plugins=opts.plugins;
      return actions.frame={element,go(url){actions.navigated=url;opts.plugins[0].callback(url);},
        back(){actions.back=true;},forward(){actions.forward=true;},reload(){actions.reload=true;}};
    }
  }
  wnd.$scramjetController={Controller,assertRuntimeScramjetVersion(){actions.versionChecked=true;}};
  wnd.$scramjetUtils={UrlWatcherPlugin,HttpCachePlugin};
  const doc={documentElement:{dataset:{}},
    getElementById:()=>err,
    createElement(tag){if(tag==='script')return {onload:null,onerror:null};
      return {id:'',addEventListener(){},src:''};},
    head:{append(el){queueMicrotask(()=>el.onload());}},body:{append(el){actions.element=el;}}
  };
  const sw={state:'activated',scriptURL:'http://127.0.0.3:12345/sw.js',postMessage(x){actions.keepalive=x;}};
  const older={state:'activated',scriptURL:'http://127.0.0.3:12345/old-sw.js'};
  const reg={scope:options.wrongScope?'http://127.0.0.3:12345/elsewhere/':'http://127.0.0.3:12345/',
    active:options.delayed||options.timeout?null:sw,installing:null,waiting:null};
  if (options.delayed) {
    sw.state='installing';reg.installing=sw;
    setTimeout(()=>{sw.state='activating';reg.installing=null;reg.active=sw;},20);
    setTimeout(()=>{sw.state='activated';},250);
  }
  const nav={serviceWorker:{controller:options.staleController?older:sw,
    ready:Promise.resolve({active:older}),register(){actions.registered=true;return Promise.resolve(reg);}}};
  const stub=source
    .replace("import('/epoxy/index.mjs')", "Promise.resolve({EpoxyClient:class EpoxyClient {constructor(args){this.wisp=args.wisp;this.kind='epoxy';}}})")
    .replace("import('/libcurl/index.mjs')", "Promise.resolve({default:class LibcurlClient {constructor(args){this.wisp=args.wisp;this.kind='libcurl';}}})");
  let clock=0;
  const FakeDate=options.timeout?class extends Date {static now(){return (clock+=31000)}}:Date;
  vm.runInNewContext(stub, {window:wnd,document:doc,navigator:nav,
    location:{href:'http://127.0.0.3:12345/mcel-surface.html',
      search:'?parentOrigin='+encodeURIComponent('http://127.0.0.1:8765')+'&address='+encodeURIComponent('https://example.org/')+(options.transport?'&transport='+options.transport:''),
      protocol:'http:',host:'127.0.0.3:12345'},
    URL,URLSearchParams,Date:FakeDate,console:{error(){},info(){}},setInterval(fn){actions.keepaliveCallback=fn;},
    setTimeout,clearTimeout},{filename:'mcel-surface.js'});
  for(let attempt=0;attempt<130;attempt++){
    if (messages.some(x=>x.m.type==='ready'||x.m.type==='error'))break;
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  function dispatch(m,origin='http://127.0.0.1:8765'){
    listeners.get('message')({origin,source:parent,data:{mcelBrowserControl:true,...m}});
  }
  return {messages,actions,dispatch,err,sw,older,reg,doc};
}

test('v2 surface selects its own registered worker, waits for controller, and navigates',async()=>{
 const b=await boot();
 assert.ok(b.actions.registered);
 assert.equal(b.actions.ctor.serviceworker,b.sw);
 assert.equal(b.actions.versionChecked,true);
 assert.ok(b.actions.ctor.transport.wisp.endsWith('/wisp/'));
 assert.equal(b.actions.ctor.transport.kind, 'libcurl');
 assert.equal(b.doc.documentElement.dataset.mcelTransport,'libcurl');
 assert.equal(b.actions.navigated,'https://example.org/');
 assert.ok(b.messages.some(x=>x.m.type==='ready'&&x.m.version==='2.0.67-alpha.2'));
 assert.ok(b.messages.every(x=>x.origin==='http://127.0.0.1:8765'));
});

test('registration activation can complete after register resolves and ready is stale',async()=>{
 const b=await boot({delayed:true,staleController:true});
 assert.equal(b.sw.state,'activated');
 assert.equal(b.actions.ctor.serviceworker,b.sw);
 assert.ok(b.messages.some(x=>x.m.workerState?.includes('activating@')));
 assert.ok(b.messages.some(x=>x.m.type==='ready'));
 assert.equal(b.err.style.display,'none');
});

test('never pick stale page controller in preference to the registered v2 worker',async()=>{
 const b=await boot({staleController:true});
 assert.equal(b.actions.ctor.serviceworker,b.sw);
 assert.notEqual(b.actions.ctor.serviceworker,b.older);
 assert.ok(b.messages.some(x=>x.m.type==='ready'));
});

test('reject mismatched worker scope even if an old page controller is activated',async()=>{
 const b=await boot({wrongScope:true,staleController:true});
 assert.ok(b.messages.some(x=>x.m.type==='error'&&x.m.error.includes('Unexpected Service Worker scope')));
 assert.equal(b.actions.ctor,undefined);
});

test('timeout reports actual registration state and does not create remote frame',async()=>{
 const b=await boot({timeout:true,staleController:true});
 assert.ok(b.messages.some(x=>x.m.type==='error'&&x.m.error.includes('failed to activate')));
 assert.ok(b.err.textContent.includes('scope='));
 assert.equal(b.actions.ctor,undefined);
});

test('only authenticated MCEL parent controls frame and final URL follows watcher',async()=>{
 const b=await boot();const n=b.messages.length;
 b.dispatch({action:'navigate',url:'https://evil.example/'},'http://attacker.local');
 assert.equal(b.actions.navigated,'https://example.org/');
 assert.equal(b.messages.length,n);
 b.dispatch({action:'navigate',url:'https://youtube.com/'});
 assert.equal(b.actions.navigated,'https://youtube.com/');
 b.actions.plugins[0].callback('https://www.youtube.com/');
 assert.equal(b.messages.at(-1).m.url,'https://www.youtube.com/');
 b.dispatch({action:'back'});b.dispatch({action:'forward'});b.dispatch({action:'reload'});
 assert.ok(b.actions.back&&b.actions.forward&&b.actions.reload);
 b.dispatch({action:'probe'});
 assert.equal(b.messages.at(-1).m.type,'ready');
});


test('explicit Epoxy comparison mode is opt-in and cannot silently become default',async()=>{
 const b=await boot({transport:'epoxy'});
 assert.equal(b.actions.ctor.transport.kind,'epoxy');
 assert.equal(b.doc.documentElement.dataset.mcelTransport,'epoxy');
 assert.ok(b.messages.some(x=>x.m.type==='ready'));
});

test('unknown transport parameters do not override the accepted libcurl default',async()=>{
 const b=await boot({transport:'unknown'});
 assert.equal(b.actions.ctor.transport.kind,'libcurl');
 assert.equal(b.doc.documentElement.dataset.mcelTransport,'libcurl');
});
