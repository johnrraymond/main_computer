/* Opt-in, local-only shell launcher diagnostic. Records pointer/hover state
 * transitions without copying application content or sending network traffic.
 * Register with the existing Applications diagnostic plugin framework.
 */
(function (root) {
  "use strict";
  const plugins = root.MCELDiagnosticPlugins;
  if (!plugins) return;
  const events = [];
  let dropped = 0;
  let active = false;
  let startedAt = null;
  let limit = 1200;
  plugins.register({
    id: "mcel-shell-launcher",
    kind: "recorder",
    description: "Local Apps rail: desktop navigation to first hover, dismissal and geometry",
    availability: () => root.document.getElementById("mcel-shell-launcher-panel") ? "ready" : "shell-not-mounted",
    start(ctx) {
      const panel = root.document.getElementById("mcel-shell-launcher-panel");
      if (!panel) throw new Error("MCEL shell launcher panel not mounted");
      events.length = 0;
      dropped = 0;
      limit = Math.max(100, Math.min(10000, Number(ctx.config.maxEvents) || 1200));
      startedAt = new Date().toISOString();
      active = true;
      const onTrace = event => {
        const detail = event.detail || {};
        if (detail.scope !== "mcel-applications-shell.launcher") return;
        // Deliberately restrict captured fields to navigation/pointer geometry.
        const row = {
          ms: Math.round(performance.now() * 10) / 10,
          kind: String(detail.kind || "unknown").slice(0, 80),
          appId: String(detail.appId || "").slice(0, 60),
          dismissed: !!detail.dismissed,
          explicitlyOpen: !!detail.explicitlyOpen,
          hovered: !!detail.hovered,
          focused: !!detail.focused,
          left: Number(detail.left),
          right: Number(detail.right),
          viewportWidth: Number(detail.viewportWidth),
          suppressHover: typeof detail.suppressHover === "boolean" ? detail.suppressHover : null,
        };
        events.push(row);
        if (events.length > limit) {
          const n = events.length - limit;
          events.splice(0, n);
          dropped += n;
        }
        if (row.kind === "first-hover-did-not-expand")
          ctx.emit("first-hover-did-not-expand", "error", "Apps launcher stayed collapsed on first hover after app selection", row);
      };
      root.addEventListener("mcel-shell-launcher-trace", onTrace);
      return {stop() {
        active = false;
        root.removeEventListener("mcel-shell-launcher-trace", onTrace);
      }};
    },
    run() {
      return {
        schema: "mcel.applications-shell.launcher-trace.v1",
        localOnly: true,
        active,
        startedAt,
        dropped,
        events: events.slice(),
      };
    },
  });
})(window);
