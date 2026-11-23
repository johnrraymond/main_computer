'use strict';
// Scramjet 2.x controller. Runs exclusively at the untrusted Docker origin.
(async function () {
  const qs = new URLSearchParams(location.search);
  // The Windows public-site differential demonstrated that libcurl v2 loads
  // BOTH example.org and YouTube, while Epoxy v3 fails YouTube with an HTTP/2
  // PROTOCOL_ERROR. Keep the single proven transport as the production default.
  // Epoxy remains opt-in for diagnosis; do not silently fall back during failures.
  const transportName = qs.get('transport') === 'epoxy' ? 'epoxy' : 'libcurl';
  let parentOrigin = '';
  try {
    const candidate = new URL(qs.get('parentOrigin') || '');
    if (candidate.protocol === 'http:' && ['127.0.0.1','localhost'].includes(candidate.hostname)) parentOrigin = candidate.origin;
  } catch (_) { /* Standalone surface works without parent control. */ }
  const errorNode = document.getElementById('mcel-surface-error');
  let stage = 'initial';
  let frame = null;
  let ready = false;
  let failure = '';
  let current = 'about:blank';
  const notify = (type, extra = {}) => {
    if (parentOrigin && window.parent !== window)
      window.parent.postMessage({mcelScramjet:true,type,engine:'scramjet-v2',...extra},parentOrigin);
  };
  const progress = next => {stage=next;notify('progress',{stage});};
  const fail = e => {
    failure = String(e?.message || e || 'Unknown failure').slice(0,500);
    errorNode.textContent = `Scramjet v2 ${stage}: ${failure}`;
    errorNode.style.display = 'block';
    notify('error',{stage,error:failure});
    console.error('[MCEL Scramjet v2]',stage,e);
  };
  const timed = (promise,label,ms=25000) => {
    let timer;
    return Promise.race([Promise.resolve(promise), new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(Error(label+' timed out')),ms);
    })]).finally(()=>clearTimeout(timer));
  };
  const script = src => new Promise((resolve,reject)=>{
    const el=document.createElement('script');el.src=src;
    el.onload=resolve;el.onerror=()=>reject(Error('Failed to load '+src));document.head.append(el);
  });
  // Observe the SPECIFIC registration returned by register('/sw.js').
  // navigator.serviceWorker.ready may resolve for an older registration or
  // while an updating worker is still transitioning to activated. A stale
  // navigator.serviceWorker.controller must never be preferred to the new one.
  const stateOf = worker => worker ? worker.state + '@' + worker.scriptURL : 'none';
  const describeRegistration = registration =>
    'scope=' + registration.scope + ' active=' + stateOf(registration.active) +
    ' installing=' + stateOf(registration.installing) +
    ' waiting=' + stateOf(registration.waiting) +
    ' pageController=' + stateOf(navigator.serviceWorker.controller);
  const waitForRegisteredWorker = async (registration, timeoutMs = 30000) => {
    const expectedScope = new URL('/', location.href).href;
    const expectedScript = new URL('/sw.js', location.href).href;
    if (registration.scope !== expectedScope)
      throw Error('Unexpected Service Worker scope: ' + describeRegistration(registration));
    const deadline = Date.now() + timeoutMs;
    let last = '';
    while (Date.now() < deadline) {
      const active = registration.active;
      if (active?.state === 'activated' && active.scriptURL === expectedScript) return active;
      const observed = describeRegistration(registration);
      if (last !== observed) {
        last = observed;
        notify('progress', {stage: 'service-worker-activation', workerState: observed.slice(0, 400)});
        console.info('[MCEL Scramjet v2] Service Worker transition', observed);
      }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw Error('Registered Scramjet Service Worker failed to activate: ' +
      describeRegistration(registration));
  };
  const navigate = value => {
    if (!ready || typeof value !== 'string' || value.length>4096) return;
    if (value === 'about:blank') {
      frame.element.src = 'about:blank';current=value;notify('navigating',{url:value});return;
    }
    let url;
    try {
      url = new URL(value);
      if (!['https:','http:'].includes(url.protocol) || url.username || url.password) throw Error('invalid protocol');
    } catch (_) {return;}
    errorNode.style.display='none';
    current=url.href;
    progress('site-navigation');
    frame.go(url.href);
    notify('navigating',{url:url.href});
  };
  window.addEventListener('message',event=>{
    if (!parentOrigin || event.source !== window.parent || event.origin !== parentOrigin ||
        !event.data?.mcelBrowserControl) return;
    const m=event.data;
    if (m.action === 'probe') {
      notify('progress',{stage});
      if (failure) notify('error',{stage,error:failure});
      if (ready) notify('ready',{version:'2.0.67-alpha.2'});
      return;
    }
    if (!ready) return;
    if (m.action==='navigate') navigate(m.url);
    if (m.action==='back') frame.back();
    if (m.action==='forward') frame.forward();
    if (m.action==='reload') frame.reload();
  });
  window.addEventListener('unhandledrejection',event=>{
    if (!ready && !failure) fail(event.reason);
  });
  try {
    if (!navigator.serviceWorker) throw Error('Service Worker unavailable');
    progress('service-worker-registration');
    const registration=await timed(navigator.serviceWorker.register('/sw.js',
      {scope:'/',updateViaCache:'none'}),'Service Worker registration');
    progress('service-worker-activation');
    const worker = await waitForRegisteredWorker(registration);
    // The frame's fetches can now be routed through the exact v2 worker.
    console.info('[MCEL Scramjet v2] Using activated worker', worker.scriptURL);
    progress('v2-runtime-bundles');
    for (const src of ['/scram/scramjet.js','/controller/controller.api.js','/utils/scramjet-utils.js'])
      await timed(script(src),'Load '+src,15000);
    if (!window.$scramjetController?.Controller || !window.$scramjetUtils?.UrlWatcherPlugin)
      throw Error('Scramjet v2 globals missing or package versions incompatible');
    if (typeof window.$scramjetController.assertRuntimeScramjetVersion === 'function')
      window.$scramjetController.assertRuntimeScramjetVersion();
    progress('v2-network-transport');
    const wisp = `${location.protocol === 'https:'?'wss:':'ws:'}//${location.host}/wisp/`;
    let transport;
    if (transportName === 'epoxy') {
      const module = await timed(import('/epoxy/index.mjs'),'Epoxy v3 transport',15000);
      const EpoxyClient = module.EpoxyClient || module.default;
      if (typeof EpoxyClient !== 'function') throw Error('EpoxyClient export missing from pinned epoxy-transport v3');
      transport = new EpoxyClient({wisp});
    } else {
      const module = await timed(import('/libcurl/index.mjs'),'Libcurl v2 transport',15000);
      if (typeof module.default !== 'function') throw Error('LibcurlClient export missing');
      transport = new module.default({wisp});
    }
    document.documentElement.dataset.mcelTransport = transportName;
    console.info('[MCEL Scramjet v2] transport='+transportName);
    const controller = new window.$scramjetController.Controller({
      serviceworker:worker,
      transport,
      config:{scramjetPath:'/scram/scramjet.js',wasmPath:'/scram/scramjet.wasm',
              injectPath:'/controller/controller.inject.js'},
    });
    progress('v2-controller-handshake');
    await timed(controller.wait(),'Scramjet v2 controller',25000);
    progress('v2-frame-creation');
    const element=document.createElement('iframe');
    element.id='mcel-remote-frame';
    element.title='Rewritten website';
    frame=controller.createFrame(element,{
      plugins:[
        new window.$scramjetUtils.UrlWatcherPlugin(url=>{
          // URL watcher is scoped to this primary frame, not subframes.
          if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return;
          current=url;
          notify('navigating',{url});
        }),
        new window.$scramjetUtils.HttpCachePlugin(),
      ],
    });
    element.addEventListener('load',()=>notify('frame-load',{url:current}));
    document.body.append(element);
    // Keep the SW alive while this frontend is mounted, without restarting Docker.
    setInterval(()=>navigator.serviceWorker.controller?.postMessage('keepalive'),15000);
    ready=true;
    progress('ready');
    notify('ready',{version:'2.0.67-alpha.2'});
    const initial=qs.get('address');
    if(initial) navigate(initial);
  } catch(e) {fail(e);}
})();
