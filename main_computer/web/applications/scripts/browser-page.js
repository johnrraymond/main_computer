(function () {
  "use strict";
  const ui = Object.fromEntries(["toolbar","address","back","forward","reload","go","surface","image","empty","status","page-title"].map(id => [id, document.getElementById("browser-" + id)]));
  const api = "/api/applications/browser/control";
  let session = null;
  let base = location.origin + "/";
  let currentUrl = "";
  let busy = false;
  let queuedControls = 0;
  let queued = Promise.resolve();
  let lastDimensions = {width:0,height:0};
  let textBuffer = "";
  let textFlushTimer = null;
  const proxyPrefix = "/applications/browser";
  // Remote content stays in backend Chromium. This path identifies that
  // session's destination; it is not a same-origin proxy for remote HTML.
  function requestedAddress() {
    if (!location.pathname.startsWith(proxyPrefix + "/")) return "";
    const suffix = location.pathname.slice(proxyPrefix.length + 1);
    if (!/^https?:\/\/[^/]+/i.test(suffix)) return "";
    // The shell appends its own child marker to a deep link's remote query.
    // Remove that transport marker while keeping the remote query and hash.
    const parts = location.search.slice(1).split("&").filter(Boolean);
    const i = parts.findLastIndex(part => part === "mcel-shell-child=1");
    if (i >= 0) parts.splice(i, 1);
    return suffix + (parts.length ? "?" + parts.join("&") : "") + location.hash;
  }
  function syncAddressRoute(remoteUrl) {
    if (!/^https?:\/\//i.test(remoteUrl)) return;
    let canonical;
    try {
      const parsed = new URL(remoteUrl);
      if (!(["http:", "https:"].includes(parsed.protocol))) return;
      canonical = proxyPrefix + "/" + parsed.href;
    } catch (_) { return; }
    try {
      // When mounted within MCEL, the user's visible address bar belongs to
      // the parent shell, not the child iframe. Never leave the local origin.
      const host = window.parent !== window ? window.parent : window;
      if (host !== window && host.document.body.dataset.mcelShellActiveApp !== "browser") return;
      if (host.location.origin !== location.origin) return;
      if (host.location.pathname + host.location.search + host.location.hash !== canonical)
        host.history.replaceState(host.history.state, "", canonical);
      if (location.pathname + location.search + location.hash !== canonical && host !== window)
        {
          const childUrl = new URL(canonical, location.origin);
          childUrl.searchParams.set("mcel-shell-child", "1");
          history.replaceState(history.state, "", childUrl.pathname + childUrl.search + childUrl.hash);
        }
    } catch (_) { /* Route sync cannot interfere with browser operation. */ }
  }
  function flushText() {
    window.clearTimeout(textFlushTimer);
    const text = textBuffer;
    textBuffer = "";
    if (text && session) perform("type", {text});
  }
  function setStatus(value, error = false) {
    ui.status.textContent = value;
    ui.status.dataset.error = String(error);
  }
  function dimensions() {
    const rect = ui.surface.getBoundingClientRect();
    return {width: Math.max(320, Math.min(1920, Math.round(rect.width) || 1080)),
      height: Math.max(240, Math.min(1080, Math.round(rect.height) || 690))};
  }
  function update(result) {
    if (result.session) session = result.session;
    if (result.base_url) base = result.base_url;
    if (result.url) {
      currentUrl = result.url;
      // The backend page URL is authoritative; mirror it into the browser's
      // outer localhost route without exposing remote HTML at that origin.
      if (document.activeElement !== ui.address) ui.address.value = currentUrl === "about:blank" ? "" : currentUrl;
      syncAddressRoute(currentUrl);
    }
    ui["page-title"].textContent = result.title || "MCEL Browser";
    ui.image.src = "data:image/jpeg;base64," + result.image;
    ui.surface.dataset.ready = "true";
    ui.back.disabled = !result.can_go_back;
    setStatus(result.navigation_error || (result.url === "about:blank" ? "Backend Chromium ready" : (result.url || base)),
      Boolean(result.navigation_error));
  }
  async function request(operation, fields = {}) {
    const response = await fetch(api, {
      method:"POST", credentials:"same-origin", cache:"no-store",
      headers:{"Content-Type":"application/json", "X-MCEL-Browser-Operation":"browser-control"},
      body:JSON.stringify({operation,session,...fields}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Browser request failed");
    return result;
  }
  function perform(operation, fields = {}) {
    // UI actions are serialized so key presses and click positions are not
    // displaced by a racing frame poll.
    queuedControls += 1;
    queued = queued.catch(() => {}).then(async () => {
      busy = true;
      try { update(await request(operation, fields)); }
      catch (error) { setStatus(error.message || String(error), true); }
      finally { busy = false; queuedControls -= 1; }
    });
    return queued;
  }
  async function poll() {
    if (session && !busy && queuedControls === 0 && !document.hidden) {
      busy = true;
      try {
        const response = await fetch("/api/applications/browser/frame?session=" + encodeURIComponent(session), {cache:"no-store",credentials:"same-origin"});
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || "Frame unavailable");
        update(result);
      } catch (error) {setStatus(error.message || String(error),true);}
      finally {busy = false;}
    }
    window.setTimeout(poll, 900);
  }
  ui.toolbar.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!session) return;
    const address = ui.address.value.trim();
    ui.surface.focus({preventScroll:true});
    perform("navigate", {address,current_url:currentUrl});
  });
  ui.back.addEventListener("click", () => session && perform("back"));
  ui.forward.addEventListener("click", () => session && perform("forward"));
  ui.reload.addEventListener("click", () => session && perform("reload"));
  function pointerCoordinates(event) {
    const rect = ui.image.getBoundingClientRect();
    const iw = ui.image.naturalWidth || lastDimensions.width;
    const ih = ui.image.naturalHeight || lastDimensions.height;
    // object-fit: contain can leave letterboxing. Ignore clicks there.
    const scale = Math.min(rect.width / iw, rect.height / ih);
    const displayedWidth = iw * scale;
    const displayedHeight = ih * scale;
    const x = event.clientX - rect.left - (rect.width - displayedWidth) / 2;
    const y = event.clientY - rect.top - (rect.height - displayedHeight) / 2;
    if (x < 0 || y < 0 || x >= displayedWidth || y >= displayedHeight) return null;
    return {x:Math.round(x/scale),y:Math.round(y/scale)};
  }
  ui.surface.addEventListener("click", (event) => {
    if (!session || !ui.image.naturalWidth) return;
    flushText();
    const point = pointerCoordinates(event);
    ui.surface.focus({preventScroll:true});
    if (point) perform("click",point);
  });
  ui.surface.addEventListener("keydown", (event) => {
    if (!session) return;
    if (event.ctrlKey && event.key.toLowerCase() === "l") {
      event.preventDefault(); ui.address.focus();ui.address.select(); return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const specials = new Set(["Enter","Escape","Backspace","Tab","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Home","End","PageUp","PageDown","Delete"]);
    if (!(event.key.length === 1 || specials.has(event.key))) return;
    event.preventDefault();
    if (event.key.length === 1) {
      textBuffer += event.key;
      if (textBuffer.length >= 256) flushText();
      else { window.clearTimeout(textFlushTimer); textFlushTimer = window.setTimeout(flushText, 90); }
    } else {
      flushText();
      perform("key", {key:event.key});
    }
  });
  ui.surface.addEventListener("paste", (event) => {
    if (!session || !event.clipboardData) return;
    const text = event.clipboardData.getData("text/plain");
    event.preventDefault();
    flushText();
    for (let i=0; i<text.length && i<16384; i+=512) perform("type", {text:text.slice(i,i+512)});
  });
  ui.surface.addEventListener("wheel", (event) => {
    if (!session) return;
    event.preventDefault();
    if (busy) return;
    const point = pointerCoordinates(event) || {};
    perform("scroll", {dx:Math.max(-1200,Math.min(1200,Math.round(event.deltaX))),
      dy:Math.max(-1200,Math.min(1200,Math.round(event.deltaY))),...point});
  }, {passive:false});
  let resizeTimer = null;
  const observer = new ResizeObserver(() => {
    if (!session) return;
    const next = dimensions();
    if (Math.abs(next.width-lastDimensions.width) < 10 && Math.abs(next.height-lastDimensions.height) < 10) return;
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      lastDimensions = next;
      perform("resize",next);
    }, 150);
  });
  observer.observe(ui.surface);
  (async () => {
    const size = dimensions();
    lastDimensions = size;
    try {
      // A blank initial tab avoids recursively loading the heavyweight MCEL
      // shell into itself; direct destination deep links still auto-navigate.
      const address = requestedAddress();
      if (address) ui.address.value = address;
      update(await request("start", {address,...size}));
    } catch (error) {
      // Only a missing executable calls for an installation. A timeout is
      // instead a backend worker or navigation failure and needs its own fix.
      setStatus(error.message || String(error), true);
      ui.empty.textContent = "Backend Chromium could not start. Check Browser status above.";
    }
    poll();
  })();
  window.addEventListener("pagehide", () => {
    if (!session) return;
    fetch(api, {method:"POST", credentials:"same-origin", keepalive:true,
      headers:{"Content-Type":"application/json", "X-MCEL-Browser-Operation":"browser-control"},
      body:JSON.stringify({operation:"close",session}),
    }).catch(() => {});
  });
})();
