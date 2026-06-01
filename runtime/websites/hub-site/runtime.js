/* Main Computer Website Builder default page runtime.
 * This no-op runtime proves that exported sites can carry a selectable page
 * runtime without changing the user's HTML, CSS, or app script behavior.
 */
(function () {
  "use strict";

  const runtime = {
    id: "default",
    name: "Default Website Builder Runtime",
    version: "0.1.0",
    entry: "runtime.js",
    hydrate(root) {
      return {
        ok: true,
        runtime: "default",
        changed: false,
        root: root && root.nodeType === 9 ? "document" : "element"
      };
    },
    transform(html) {
      return String(html || "");
    },
    audit() {
      return {
        ok: true,
        runtime: "default",
        issues: []
      };
    }
  };

  Object.defineProperty(window, "WebsiteBuilderRuntime", {
    value: runtime,
    configurable: true,
    writable: false
  });

  document.addEventListener("DOMContentLoaded", function () {
    runtime.hydrate(document);
  });
})();
