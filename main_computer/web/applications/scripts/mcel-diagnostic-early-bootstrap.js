/* First executable Applications script: retain browser errors occurring during bootstrap.
 * No network access and no SDK dependency. */
(() => {
  "use strict";
  if (window.__MCELDiagnosticEarly) return;
  const events = [];
  const maximum = 200;
  const pageStartedAt = new Date().toISOString();
  function push(code, severity, detail, extra) {
    const event = {pluginId:"browser-startup",code,severity,detail:String(detail || "").slice(0,1200),
      timestamp:new Date().toISOString(),phase:document.readyState, ...extra};
    events.push(event);
    if (events.length > maximum) events.shift();
  }
  window.addEventListener("error", event => {
    if (event instanceof ErrorEvent) {
      push("uncaught-error","error",event.message,{source:event.filename,line:event.lineno,column:event.colno});
    } else {
      const target = event.target;
      push("resource-error","warning",target?.tagName || "Resource load failed",
        {source:target?.currentSrc || target?.src || target?.href || ""});
    }
  }, true);
  window.addEventListener("unhandledrejection", event => {
    const reason = event.reason;
    push("unhandled-rejection","error",String(reason?.stack || reason?.message || reason));
  });
  window.__MCELDiagnosticEarly = Object.freeze({pageStartedAt,events:() => events.slice(),drain:() => events.splice(0)});
})();
