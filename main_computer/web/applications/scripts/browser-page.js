(function () {
  'use strict';
  // The MCEL origin ONLY owns navigation and Docker leases. Untrusted HTML/JS
  // executes at a separate 127.0.0.3 origin inside this iframe.
  const ids = ['toolbar','address','back','forward','reload','go','surface','frame','empty','status','page-title'];
  const ui = Object.fromEntries(ids.map(id => [id, document.getElementById('browser-' + id)]));
  const api = '/api/applications/browser/control';
  const proxyPrefix = '/applications/browser';
  let session = null;
  let sidecarOrigin = '';
  let currentUrl = 'about:blank';
  let ready = false;
  let closed = false;
  let startup = null;
  let epoch = 0;
  let phase = 'waiting-for-docker';
  let bootWatchdog = null;
  let bootProbe = null;
  let bootFailed = false;
  const BOOT_DEADLINE_MS = 45000;
  function stopBootWatch() {
    if (bootProbe !== null) window.clearInterval(bootProbe);
    if (bootWatchdog !== null) window.clearTimeout(bootWatchdog);
    bootProbe = bootWatchdog = null;
  }
  function bootFailure(reason) {
    stopBootWatch();
    ready = false;
    bootFailed = true;
    ui.surface.dataset.ready = 'false';
    const text = 'Isolated Browser: ' + reason;
    ui.empty.textContent = text;
    status(text, true);
    console.error('[MCEL Browser]', text, {phase, iframe: ui.frame.src});
  }
  function beginBootWatch() {
    stopBootWatch();
    bootFailed = false;
    // Never assume a one-shot ready event can be delivered during iframe load.
    // Probe the isolated origin until acknowledged or timeout; no DOM access.
    const probe = () => {
      if (closed || ready || bootFailed || !sidecarOrigin || !ui.frame.contentWindow) return;
      ui.frame.contentWindow.postMessage({mcelBrowserControl:true, action:'probe'}, sidecarOrigin);
    };
    bootProbe = window.setInterval(probe, 1500);
    bootWatchdog = window.setTimeout(() => {
      if (!ready && !closed) bootFailure('initialization timed out at ' + phase + '. Reload the Browser to retry.');
    }, BOOT_DEADLINE_MS);
    probe();
  }
  ui.frame.addEventListener('load', () => {
    if (!ready && !bootFailed && sidecarOrigin) {
      phase = 'iframe-loaded-awaiting-controller';
      status('Isolated frame loaded; initializing controller…');
      ui.frame.contentWindow?.postMessage({mcelBrowserControl:true, action:'probe'}, sidecarOrigin);
    }
  });
  ui.frame.addEventListener('error', () => {
    if (!ready && !bootFailed) bootFailure('isolated frame failed to load');
  });

  function requestedAddress() {
    if (!location.pathname.startsWith(proxyPrefix + '/')) return '';
    const suffix = location.pathname.slice(proxyPrefix.length + 1);
    if (!/^https?:\/\/[^/]+/i.test(suffix)) return '';
    const parts = location.search.slice(1).split('&').filter(Boolean);
    const i = parts.findLastIndex(part => part === 'mcel-shell-child=1');
    if (i >= 0) parts.splice(i, 1);
    return suffix + (parts.length ? '?' + parts.join('&') : '') + location.hash;
  }
  function syncAddressRoute(remoteUrl) {
    if (!/^https?:\/\//i.test(remoteUrl)) return;
    let canonical;
    try {
      const parsed = new URL(remoteUrl);
      if (!['http:', 'https:'].includes(parsed.protocol)) return;
      canonical = proxyPrefix + '/' + parsed.href;
    } catch (_) { return; }
    try {
      const host = window.parent !== window ? window.parent : window;
      if (host !== window && host.document.body.dataset.mcelShellActiveApp !== 'browser') return;
      if (host.location.origin !== location.origin) return;
      if (host.location.pathname + host.location.search + host.location.hash !== canonical)
        host.history.replaceState(host.history.state, '', canonical);
      if (host !== window && location.pathname + location.search + location.hash !== canonical) {
        const childUrl = new URL(canonical, location.origin);
        childUrl.searchParams.set('mcel-shell-child', '1');
        history.replaceState(history.state, '', childUrl.pathname + childUrl.search + childUrl.hash);
      }
    } catch (_) { /* Navigation must not fail because the shell was re-mounted. */ }
  }
  function status(value, error = false) {
    ui.status.textContent = value;
    ui.status.dataset.error = String(error);
  }
  function normalize(address) {
    const value = address.trim();
    if (!value || value === 'about:blank') return 'about:blank';
    if (value.length > 2048 || /[\\\x00-\x1f\x7f]/.test(value) || value.startsWith('//')) throw Error('Invalid browser address');
    let uri;
    if (/^https?:\/\//i.test(value)) uri = new URL(value);
    else if (/^(?:localhost|127\.0\.0\.1)(?::\d{1,5})?(?:[/?#]|$)/i.test(value)) uri = new URL('http://' + value);
    else if (/^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d{1,5})?(?:[/?#]|$)/i.test(value)) uri = new URL('https://' + value);
    else if (value.startsWith('./') || value.startsWith('../') || value.startsWith('?') || value.startsWith('#'))
      uri = new URL(value, currentUrl === 'about:blank' ? location.origin + '/' : currentUrl);
    else uri = new URL(value.replace(/^\/+/, ''), location.origin + '/');
    if (!['http:', 'https:'].includes(uri.protocol) || uri.username || uri.password) throw Error('Only HTTP(S) websites are supported');
    // Do not silently send private MCEL paths through the public web rewriter.
    // Wisp independently enforces private/loopback IP filtering after DNS resolution.
    if (['localhost', '127.0.0.1', '127.0.0.2', '127.0.0.3'].includes(uri.hostname))
      throw Error('MCEL localhost is intentionally unavailable to untrusted web pages');
    return uri.href;
  }
  async function command(operation, fields = {}, keepalive = false) {
    const response = await fetch(api, {
      method:'POST', credentials:'same-origin', cache:'no-store', keepalive,
      headers:{'Content-Type':'application/json', 'X-MCEL-Browser-Operation':'browser-control'},
      body:JSON.stringify({operation, ...fields}),
    });
    const value = await response.json();
    if (!response.ok || !value.ok) throw Error(value.error || 'Scramjet Browser request failed');
    return value;
  }
  function send(action, extra={}) {
    if (!ready || !sidecarOrigin || !ui.frame.contentWindow) return;
    ui.frame.contentWindow.postMessage({mcelBrowserControl:true, action, ...extra}, sidecarOrigin);
  }
  async function start() {
    if (startup) return startup;
    startup = (async () => {
      status('Starting isolated Scramjet Docker browser…');
      const value = await command('surface-start');
      if (closed) {await command('surface-close',{session:value.session});return;}
      // Only the fixed, explicitly isolated host is permitted as iframe source.
      const base = new URL(value.sidecar_url);
      if (value.engine !== 'scramjet-docker' || base.protocol !== 'http:' || base.hostname !== '127.0.0.3' ||
          !base.port || base.pathname !== '/' || base.username || base.password) {
        await command('surface-close',{session:value.session});
        throw Error('Backend returned an invalid isolated browser origin');
      }
      session = value.session;
      sidecarOrigin = base.origin;
      ready = false;
      phase = 'frame-request';
      const frameUrl = new URL('/mcel-surface.html', base);
      frameUrl.searchParams.set('parentOrigin', location.origin);
      if (currentUrl !== 'about:blank') frameUrl.searchParams.set('address', currentUrl);
      ui.frame.src = frameUrl.href;
      ui.surface.dataset.ready = 'true';
      beginBootWatch();
      ui['page-title'].textContent = 'Scramjet';
      status('Starting frontend browser engine…');
    })().catch(e => {startup = null;status(e.message || String(e), true);ui.empty.textContent = 'Browser unavailable: ' + (e.message || e);throw e;});
    return startup;
  }
  async function restartOnLeaseExpiry() {
    session = null;
    ready = false;
    startup = null;
    await start();
  }
  async function refreshLease() {
    if (!session || closed) return;
    try {await command('surface-touch',{session});}
    catch (e) {
      status('Browser Docker lease expired; reconnecting…', true);
      try {await restartOnLeaseExpiry();}
      catch (_) { /* start() displays its own diagnostic. */ }
    }
  }
  function navigate(address) {
    let url;
    try {url = normalize(address);}
    catch (e) {status(e.message || String(e), true);return;}
    currentUrl = url;
    ui.address.value = url === 'about:blank' ? '' : url;
    if (url !== 'about:blank') syncAddressRoute(url);
    if (!session) {
      start().catch(() => {});
    } else if (ready) {
      send('navigate',{url});
      status('Navigating to ' + url);
    } else {
      status('Waiting for isolated browser to initialize…');
    }
  }
  window.addEventListener('message', event => {
    if (closed || !sidecarOrigin || event.origin !== sidecarOrigin ||
        event.source !== ui.frame.contentWindow || !event.data || event.data.mcelScramjet !== true) return;
    const msg = event.data;
    if (msg.type === 'ready') {
      if (bootFailed) return;
      phase = 'ready';
      stopBootWatch();
      ready = true;
      ui.empty.textContent = '';
      status('Scramjet ready');
      // The sidecar's initial address param already navigates; only send if
      // the iframe was opened blank and a user typed while it initialized.
      const seededAddress = new URL(ui.frame.src).searchParams.get('address') || 'about:blank';
      if (currentUrl !== seededAddress) send('navigate',{url:currentUrl});
    } else if (msg.type === 'progress') {
      if (!ready && !bootFailed && typeof msg.stage === 'string' && /^[a-z-]{1,50}$/.test(msg.stage)) {
        phase = msg.stage;
        status('Initializing isolated Browser: ' + phase);
      }
    } else if (msg.type === 'navigating') {
      if (typeof msg.url === 'string' && /^https?:\/\//.test(msg.url)) {
        currentUrl = msg.url;
        if (document.activeElement !== ui.address) ui.address.value = currentUrl;
        syncAddressRoute(currentUrl);
      }
      status('Navigating to ' + currentUrl);
    } else if (msg.type === 'frame-load') {
      status(currentUrl === 'about:blank' ? 'Scramjet ready' : currentUrl);
    } else if (msg.type === 'error') {
      const failedStage = typeof msg.stage === 'string' ? msg.stage.slice(0, 50) : phase;
      if (!ready) bootFailure(failedStage + ': ' + String(msg.error || 'initialization failed').slice(0, 480));
      else status('Scramjet: ' + String(msg.error || 'site error').slice(0, 300), true);
    }
  });
  ui.toolbar.addEventListener('submit', event => {event.preventDefault();navigate(ui.address.value);});
  ui.back.addEventListener('click', () => send('back'));
  ui.forward.addEventListener('click', () => send('forward'));
  ui.reload.addEventListener('click', () => {
    if (!ready && session && sidecarOrigin) {
      const frameUrl = new URL('/mcel-surface.html', sidecarOrigin);
      frameUrl.searchParams.set('parentOrigin', location.origin);
      if (currentUrl !== 'about:blank') frameUrl.searchParams.set('address', currentUrl);
      ui.surface.dataset.ready = 'true';
      ui.empty.textContent = '';
      phase = 'retry-frame-request';
      ready = false;
      ui.frame.src = frameUrl.href;
      beginBootWatch();
    } else if (!session) { startup = null; start().catch(() => {}); }
    else send('reload');
  });
  window.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'l') {
      event.preventDefault();ui.address.focus();ui.address.select();
    }
  });
  window.addEventListener('visibilitychange', () => {if (!document.hidden) refreshLease();});
  const timer = window.setInterval(refreshLease, 25000);
  window.addEventListener('pagehide', () => {
    closed = true;
    window.clearInterval(timer);
    stopBootWatch();
    if (session) command('surface-close',{session},true).catch(() => {});
    ui.frame.removeAttribute('src');
  });
  const initial = requestedAddress();
  if (initial) {
    currentUrl = initial;
    ui.address.value = initial;
  }
  start().catch(() => {});
})();
