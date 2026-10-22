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
      push("uncaught-error","error",event.message,{
        source:event.filename || "",line:event.lineno,column:event.colno,
        stack:String(event.error?.stack || "").slice(0,4000),
        errorName:String(event.error?.name || "").slice(0,120)
      });
    } else {
      const target = event.target;
      const source = String(target?.currentSrc || target?.src || target?.href || "").slice(0,1200);
      const timing = source && typeof performance !== "undefined"
        ? performance.getEntriesByName(source,"resource").slice(-1)[0] : null;
      push("resource-error","warning",target?.tagName || "Resource load failed",{
        source,
        resourceTag:String(target?.tagName || "").slice(0,32),
        resourceType:String(target?.type || "").slice(0,120),
        crossorigin:String(target?.crossOrigin || "").slice(0,80),
        integrity:String(target?.integrity || "").slice(0,200),
        timing:timing ? {
          duration:timing.duration,
          responseStatus:typeof timing.responseStatus === "number" ? timing.responseStatus : null,
          transferSize:timing.transferSize,
          decodedBodySize:timing.decodedBodySize,
          initiatorType:timing.initiatorType
        } : null
      });
    }
  }, true);
  window.addEventListener("unhandledrejection", event => {
    const reason = event.reason;
    push("unhandled-rejection","error",String(reason?.stack || reason?.message || reason));
  });
  window.__MCELDiagnosticEarly = Object.freeze({pageStartedAt,events:() => events.slice(),drain:() => events.splice(0)});
})();
