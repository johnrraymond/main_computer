'use strict';
// Runs ONLY at the isolated Docker origin (127.0.0.3). Never execute remote
// websites or Scramjet libraries inside the trusted Main Computer origin.
(async () => {
  const params = new URLSearchParams(location.search);
  const requestedParent = params.get('parentOrigin') || '';
  let parentOrigin = null;
  try {
    const parsed = new URL(requestedParent);
    if (parsed.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(parsed.hostname))
      parentOrigin = parsed.origin;
  } catch (_) { /* Ignore invalid parent origins. */ }

  let phase = 'entry';
  let initialized = false;
  let failed = false;
  let frame = null;
  let currentUrl = '';
  const errorNode = document.getElementById('mcel-surface-error');
  const notify = (type, extra = {}) => {
    if (parentOrigin && window.parent !== window)
      window.parent.postMessage({mcelScramjet: true, type, ...extra}, parentOrigin);
  };
  const progress = next => { phase = next; notify('progress', {stage: phase}); };
  const message = e => String(e?.message || e || 'unknown error').slice(0, 500);
  const fail = err => {
    failed = true;
    const detail = message(err);
    if (errorNode) {
      errorNode.textContent = 'Scramjet ' + phase + ': ' + detail;
      errorNode.style.display = 'block';
    }
    notify('error', {stage: phase, error: detail});
    console.error('[MCEL Scramjet] stage=' + phase, err);
  };
  // If an async import or transport fails, never leave the MCEL Browser blank.
  window.addEventListener('unhandledrejection', e => { if (!failed) fail(e.reason || 'Unhandled rejection'); });
  window.addEventListener('error', e => {
    // Resource failure and JavaScript exception errors from this document;
    // remote content errors cannot be trusted as MCEL controller diagnostics.
    if (!failed && (e.target === window || e.target?.tagName === 'SCRIPT'))
      fail(e.error || e.message || ('Failed to load ' + (e.target?.src || 'script')));
  }, true);

  const withDeadline = (p, label, ms = 25000) => {
    let timer;
    return Promise.race([
      Promise.resolve(p),
      new Promise((_, reject) => {timer = setTimeout(() => reject(Error(label + ' timed out')), ms);}),
    ]).finally(() => clearTimeout(timer));
  };
  // Do not conflate "server returned sw.js" with "Chromium registered its
  // Service Worker".  The worker is needed for Scramjet's request rewriting.
  const workerDiagnostic = {origin: location.origin, secure: isSecureContext,
    embedded: window.parent !== window, script: 'not-checked', existing: 'not-checked',
    storage: 'unknown', probe: 'not-run'};
  const probeRegistration = async () => {
    try {
      const response = await withDeadline(fetch('/sw.js', {cache: 'no-store', credentials: 'same-origin'}),
        'Service Worker script fetch', 4500);
      const type = response.headers.get('Content-Type') || 'unknown';
      workerDiagnostic.script = String(response.status) + '/' + type.split(';')[0];
      if (!response.ok || !/javascript|ecmascript/i.test(type))
        throw Error('Invalid /sw.js response: ' + workerDiagnostic.script);
      // Check the actual registration entrypoint, not just generic HTTP health.
      if (!(await withDeadline(response.text(), 'Service Worker script body', 2500)).includes('$scramjetLoadWorker'))
        throw Error('/sw.js is not the expected Scramjet worker script');
    } catch (e) {
      if (workerDiagnostic.script === 'not-checked') workerDiagnostic.script = message(e);
      throw e;
    }
    try {
      const r = await withDeadline(navigator.serviceWorker.getRegistration('/'), 'Existing Service Worker query', 3000);
      workerDiagnostic.existing = r?.active?.scriptURL || (r ? 'registration-not-active' : 'none');
    } catch (e) { workerDiagnostic.existing = message(e); }
    if (typeof document.hasStorageAccess === 'function') {
      try {
        workerDiagnostic.storage = String(await withDeadline(document.hasStorageAccess(), 'Storage-access probe', 1500));
      } catch (e) {workerDiagnostic.storage = message(e);}
    }
  };
  const sameWorkerActive = async () => {
    try {
      const r = await withDeadline(navigator.serviceWorker.getRegistration('/'), 'Active worker query', 2500);
      return Boolean(r?.active?.scriptURL === new URL('/sw.js', location.href).href);
    } catch (_) {return false;}
  };
  const diagnoseHungRegistration = async () => {
    // Minimal independent SW diagnoses iframe/browser policy separately from
    // importScripts and the Scramjet bundle. Scope cannot intercept site URLs.
    try {
      const r = await withDeadline(navigator.serviceWorker.register('/mcel-sw-probe.js', {
        scope: '/mcel-worker-probe/', updateViaCache: 'none'
      }), 'Minimal isolated Service Worker probe', 4500);
      workerDiagnostic.probe = 'registered';
      // A successful probe has served its purpose; do not persist it.
      await withDeadline(r.unregister(), 'Minimal worker cleanup', 1200).catch(() => {});
    } catch (e) {workerDiagnostic.probe = message(e);}
  };
  const registerWorker = async () => {
    progress('service-worker-preflight');
    await probeRegistration();
    progress('service-worker-registration');
    try {
      await withDeadline(navigator.serviceWorker.register('/sw.js', {updateViaCache: 'none'}),
        'Service Worker registration', 15000);
    } catch (e) {
      if (/timed out/.test(message(e))) {
        // A registered, active worker is sufficient even if a concurrent
        // Chromium update remains pending. Never silently bypass absent SW.
        if (await sameWorkerActive()) {
          workerDiagnostic.existing = 'active-on-timeout';
          return;
        }
        await diagnoseHungRegistration();
      }
      const details = [workerDiagnostic.script, 'embedded=' + workerDiagnostic.embedded,
        'secure=' + workerDiagnostic.secure, 'storage=' + workerDiagnostic.storage,
        'probe=' + workerDiagnostic.probe].join(' ');
      const classification = workerDiagnostic.probe === 'registered'
        ? ' Scramjet worker startup issue (minimal worker registered).'
        : workerDiagnostic.probe !== 'not-run'
          ? ' Browser-level SW registration issue (minimal worker also failed).'
          : '';
      throw Error(message(e) + '; ' + details + classification);
    }
  };
  // The controller reports the requested URL before navigation. After a
  // native HTTP redirect, the iframe's actual *proxied* URL is authoritative.
  // Decode only our own Scramjet prefix, never read a site's HTML or cookies.
  const remoteUrlFromFrame = () => {
    if (!frame?.frame?.contentWindow) return null;
    try {
      const observed = new URL(frame.frame.contentWindow.location.href);
      // Depending on Scramjet's location shim, the parent may see either the
      // actual /scramjet/ URL or its virtual remote URL. The browser itself
      // enforces access to cross-origin native Location objects.
      const virtual = observed.origin === location.origin &&
        observed.pathname.startsWith('/scramjet/');
      const encoded = virtual ? observed.pathname.slice('/scramjet/'.length) : '';
      if (virtual && (!encoded || encoded.length > 8192)) return null;
      if (!virtual && observed.origin !== location.origin) {
        if (!['http:', 'https:'].includes(observed.protocol)) return null;
      } else if (!virtual) return null;
      const candidate = virtual ? new URL(decodeURIComponent(encoded) + observed.search + observed.hash) : observed;
      if (!['http:', 'https:'].includes(candidate.protocol) ||
          candidate.username || candidate.password) return null;
      return candidate.href;
    } catch (_) {
      return null; // A transient navigation or cross-origin document is never fatal.
    }
  };
  const publishFinalUrl = () => {
    const observed = remoteUrlFromFrame();
    if (observed && observed !== currentUrl) {
      currentUrl = observed;
      notify('navigating', {url: observed, redirected: true});
    }
    notify('frame-load', {stage: 'remote-frame-load'});
  };
  const navigate = address => {
    if (!initialized || typeof address !== 'string' || address.length > 2048) return;
    if (address === 'about:blank') {
      frame.frame.src = 'about:blank'; currentUrl = address;
      notify('navigating', {url: address}); return;
    }
    let parsed;
    try {
      parsed = new URL(address);
      if (!['http:', 'https:'].includes(parsed.protocol)) return;
    } catch (_) {return;}
    if (errorNode) errorNode.style.display = 'none';
    currentUrl = parsed.href;
    progress('site-navigation');
    frame.go(currentUrl);
    notify('navigating', {url: currentUrl});
  };

  // Register the authenticated parent control channel BEFORE awaiting any
  // Service Worker / BareMux operations. Probes make a lost ready event harmless.
  window.addEventListener('message', e => {
    if (!parentOrigin || e.source !== window.parent || e.origin !== parentOrigin ||
        !e.data?.mcelBrowserControl) return;
    const m = e.data;
    if (m.action === 'probe') {
      notify('progress', {stage: phase});
      if (initialized) notify('ready');
      if (failed) notify('error', {stage: phase, error: errorNode?.textContent || 'Initialization failed'});
      return;
    }
    if (!initialized) return;
    if (m.action === 'navigate') navigate(m.url);
    else if (m.action === 'reload') frame.reload();
    else if (m.action === 'back') frame.back();
    else if (m.action === 'forward') frame.forward();
  });

  try {
    progress('scramjet-controller');
    if (typeof $scramjetLoadController !== 'function' || typeof BareMux === 'undefined' || typeof BareMux.BareMuxConnection !== 'function')
      throw Error('Scramjet or BareMux bundle was not loaded');
    const {ScramjetController} = $scramjetLoadController();
    const controller = new ScramjetController({
      files: {wasm: '/scram/scramjet.wasm.wasm', all: '/scram/scramjet.all.js', sync: '/scram/scramjet.sync.js'},
    });
    // Controller owns IndexedDB schema creation. Do not start Scramjet's
    // Service Worker until both database schema and controller init are done.
    progress('indexeddb-initialization');
    if (typeof window.mcelPrepareScramjetDB !== 'function')
      throw Error('Scramjet IndexedDB initializer not loaded');
    await withDeadline(window.mcelPrepareScramjetDB(), 'Scramjet IndexedDB schema', 12000);
    progress('scramjet-controller-init');
    await withDeadline(controller.init(), 'Scramjet controller initialization', 12000);
    const transport = new BareMux.BareMuxConnection('/baremux/worker.js');
    if (!navigator.serviceWorker) throw Error('Service Worker unavailable in isolated origin');
    await registerWorker();
    progress('network-transport');
    const wisp = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/wisp/';
    if ((await withDeadline(transport.getTransport(), 'Transport query')) !== '/libcurl/index.mjs')
      await withDeadline(transport.setTransport('/libcurl/index.mjs', [{websocket: wisp}]), 'Transport setup');
    progress('frame-creation');
    frame = controller.createFrame();
    frame.frame.id = 'mcel-remote-frame';
    frame.frame.addEventListener('load', publishFinalUrl);
    document.body.appendChild(frame.frame);
    if (typeof frame.addEventListener === 'function') {
      frame.addEventListener('urlchange', e => {
        if (typeof e.url === 'string' && /^https?:\/\//i.test(e.url)) {
          try {
            const dest = new URL(e.url);
            if (['http:', 'https:'].includes(dest.protocol) && !dest.username && !dest.password) {
              currentUrl = dest.href;
              notify('navigating', {url: dest.href});
            }
          } catch (_) { /* Ignore malformed upstream URL changes. */ }
        }
      });
    }
    initialized = true;
    progress('ready');
    notify('ready');
    const initial = params.get('address');
    if (initial) navigate(initial);
  } catch (e) {fail(e);}
})();
