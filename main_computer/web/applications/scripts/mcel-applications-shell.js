/* Canonical MCEL applications shell: navigation belongs to the host;
 * application capabilities, storage and layout remain in the child page.
 */
(function (root) {
  "use strict";
  const irTag = document.getElementById("mcel-applications-shell-ir");
  const catalogTag = document.getElementById("mcel-applications-shell-registry");
  const shell = document.getElementById("mcel-applications-shell");
  const status = document.getElementById("mcel-shell-status");
  const entries = document.getElementById("mcel-shell-launcher-items");
  const panel = document.getElementById("mcel-shell-launcher-panel");
  const mount = document.getElementById("mcel-shell-application-mount");
  const frame = document.getElementById("mcel-shell-frame");
  const title = document.getElementById("mcel-shell-active-title");
  const openNative = document.getElementById("mcel-shell-open-native");
  function refuse(reason) {
    document.body.dataset.mcelShellReady = "false";
    if (status) { status.dataset.error = "true"; status.textContent = "MCEL shell refused: " + reason; }
    if (shell) shell.inert = true;
    return null;
  }
  if (!irTag || !catalogTag || !shell || !status || !entries || !panel || !mount || !frame || !title || !openNative)
    return refuse("required-shell-element-missing");

  let ir, catalog;
  try {
    ir = JSON.parse(irTag.textContent);
    catalog = JSON.parse(catalogTag.textContent);
  } catch (e) { return refuse("compiled-source-or-registry-invalid"); }
  const expected = ["application", "masthead", "shell", "launcher", "workspace", "workspace-header", "application-mount", "status"];
  const regionIds = new Set(ir?.surfaces?.[0]?.interfaceIntent?.regions?.map(r => r.id) || []);
  if (ir?.application?.appId !== "semantic-applications-shell" ||
      ir?.layouts?.[0]?.layoutTree?.schema !== "mcel.layout-tree.v1" ||
      ir?.capabilities?.length || ir?.intents?.length || ir?.effects?.length ||
      !expected.every(name => regionIds.has("interface-region:semantic-applications-shell." + name) &&
        document.querySelectorAll('[data-mcel-region-id="interface-region:semantic-applications-shell.' + name + '"]').length === 1))
    return refuse("semantic-shell-contract-mismatch");

  const layout = root.McelSemanticLayoutInference;
  if (typeof layout?.mountCanonicalLayoutTree !== "function") return refuse("canonical-layout-engine-missing");
  let layoutMount;
  try { layoutMount = layout.mountCanonicalLayoutTree(shell, ir, {observeResize: false, observeMutations: false}); }
  catch(e) { return refuse("canonical-layout-exception: " + String(e?.message || e)); }
  if (layoutMount?.status !== "pass") return refuse("canonical-layout-refused: " + String(layoutMount?.reason));

  if (catalog?.schema !== "mcel.applications-shell.registry.v1" || !Array.isArray(catalog.apps))
    return refuse("registry-contract-invalid");
  const allowed = new Map();
  function childHref(app) {
    const target = new URL(app.route, location.origin === "null" ? "http://127.0.0.1" : location.origin);
    // Deep links (task-manager tabs, website-builder sites) stay in the native
    // child when the parent is initially opened at that exact canonical path.
    if (routeFaithful && !routePreview &&
        (location.pathname === app.route || location.pathname.startsWith(app.route + "/"))) {
      target.pathname = location.pathname;
      for (const [key, value] of new URLSearchParams(location.search)) {
        if (key !== "mcel-shell-preview" && key !== childOnlyKey && key !== "app")
          target.searchParams.append(key, value);
      }
    }
    target.searchParams.set(childOnlyKey, "1");
    return target.pathname + target.search;
  }
  for (const app of catalog.apps) {
    if (!app || !/^[a-z][a-z0-9-]*$/.test(app.id) ||
        !["overview", "semantic", "legacy"].includes(app.mount) ||
        app.route !== (app.id === "desktop" ? "/applications" : "/applications/" + app.id) ||
        allowed.has(app.id)) return refuse("registry-entry-invalid");
    allowed.set(app.id, app);
  }
  if (allowed.get("desktop")?.mount !== "overview") return refuse("desktop-identity-missing");
  // A route-preview keeps canonical-looking URLs while retaining this parent
  // shell.  Child iframe URLs never carry this preview flag, avoiding recursion.
  const routePreview = document.body.dataset.mcelShellRoutePreview === "true";
  const canonicalPromoted = document.body.dataset.mcelShellCanonicalPromoted === "true";
  const routeFaithful = routePreview || canonicalPromoted;
  const childOnlyKey = "mcel-shell-child";
  const buttons = new Map();
  let active = null;
  let firstHoverPending = false;
  let firstHoverTimer = null;
  function traceLauncher(kind, extra = {}) {
    const rect = panel.getBoundingClientRect();
    root.dispatchEvent(new CustomEvent("mcel-shell-launcher-trace", {detail: {
      scope: "mcel-applications-shell.launcher", kind, appId: active,
      dismissed: panel.dataset.dismissed === "true",
      explicitlyOpen: panel.dataset.open === "true",
      hovered: panel.matches(":hover"),
      focused: panel.contains(document.activeElement),
      left: Math.round(rect.left * 100) / 100,
      right: Math.round(rect.right * 100) / 100,
      viewportWidth: root.innerWidth,
      ...extra,
    }}));
  }
  function candidateHref(id) {
    if (routeFaithful) {
      const target = new URL(allowed.get(id).route, location.origin === "null" ? "http://127.0.0.1" : location.origin);
      if (routePreview) target.searchParams.set("mcel-shell-preview", "1");
      return target.pathname + target.search;
    }
    const target = new URL(location.href);
    target.search = "";
    if (id !== "desktop") target.searchParams.set("app", id);
    return target.pathname + target.search;
  }
  for (const app of catalog.apps) {
    const a = document.createElement("a");
    a.className = "mcel-shell-app-card";
    a.href = candidateHref(app.id);
    a.dataset.app = app.id;
    const strong = document.createElement("strong"); strong.textContent = app.title;
    const detail = document.createElement("span"); detail.textContent = app.description;
    a.append(strong, detail);
    a.addEventListener("click", event => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.shiftKey || event.metaKey || event.altKey) return;
      event.preventDefault();
      select(app.id, true);
    });
    entries.append(a); buttons.set(app.id, a);
  }
  function select(id, push) {
    const app = allowed.get(id);
    if (!app) return false;
    if (active !== id) {
      active = id;
      for (const [slug, link] of buttons) {
        if (slug === id) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
      title.textContent = app.title;
      document.title = "MCEL Shell · " + app.title;
      mount.dataset.mode = app.mount;
      openNative.href = childHref(app);
      openNative.hidden = app.mount === "overview";
      if (app.mount !== "overview") {
        frame.title = app.title + " (canonical application)";
        frame.src = childHref(app); // Explicit native child, never shell recursion
      } else {
        frame.removeAttribute("src");
      }
      status.textContent = (canonicalPromoted ? "Canonical" : "Candidate") +
        " · semantic shell authority · " + app.title +
        (app.mount !== "overview" ? " · child application authority unchanged" : " · choose an app");
      document.body.dataset.mcelShellActiveApp = app.id;
      firstHoverPending = true;
      traceLauncher("application-selected", {mount:app.mount});
    }
    // Browser Back/Forward can change a deep link within the same app.
    if (!push && active === id && app.mount !== "overview") {
      const desired = childHref(app);
      if (frame.getAttribute("src") !== desired) {
        frame.src = desired;
        openNative.href = desired;
      }
    }
    if (push && location.pathname + location.search !== candidateHref(id))
      history.pushState({mcelShellApp: id}, "", candidateHref(id));
    if (push) closeLauncher(false);
    else panel.dataset.open = "false";
    return true;
  }
  function currentId() {
    const routeApp = routeFaithful
      ? [...allowed.values()].find(app => app.route === location.pathname ||
          (app.id !== "desktop" && location.pathname.startsWith(app.route + "/")))?.id
      : null;
    const app = routeApp || new URLSearchParams(location.search).get("app") || "desktop";
    return allowed.has(app) ? app : "desktop";
  }
  root.addEventListener("popstate", () => select(currentId(), false));
  document.querySelectorAll("[data-shell-launch]").forEach(button =>
    button.addEventListener("click", () => {
      const app = allowed.get(button.dataset.shellLaunch);
      if (!app) return;
      select(app.id, true);
    }));
  const trigger = document.getElementById("mcel-shell-open-apps");
  function closeLauncher(returnFocus) {
    // The latch is only needed when dismissing a launcher that is already
    // exposed by hover, keyboard focus, or explicit opening. Desktop-icon
    // navigation never opened this panel: latching it there swallowed the
    // first subsequent hover until a pointerleave event arrived.
    const suppressHover = panel.dataset.open === "true" ||
      panel.matches(":hover") || panel.contains(document.activeElement);
    panel.dataset.open = "false";
    panel.dataset.dismissed = suppressHover ? "true" : "false";
    if (returnFocus && trigger) trigger.focus({preventScroll: true});
    else if (panel.contains(document.activeElement)) document.activeElement.blur();
    traceLauncher("launcher-closed", {reason:returnFocus ? "keyboard" : "navigation", suppressHover});
  }
  panel.addEventListener("pointerenter", event => {
    if (!firstHoverPending) return;
    firstHoverPending = false;
    clearTimeout(firstHoverTimer);
    traceLauncher("first-pointer-enter", {pointerType:event.pointerType});
    // Allow the 180-ms CSS transition to finish before measuring the result.
    firstHoverTimer = setTimeout(() => {
      firstHoverTimer = null;
      if (!panel.matches(":hover")) {
        traceLauncher("first-hover-abandoned");
        return;
      }
      const opened = Math.abs(panel.getBoundingClientRect().left) <= 3;
      traceLauncher(opened ? "first-hover-expanded" : "first-hover-did-not-expand");
    }, 260);
  });
  panel.addEventListener("pointerleave", () => {
    const wasDismissed = panel.dataset.dismissed === "true";
    panel.dataset.dismissed = "false";
    if (wasDismissed) traceLauncher("hover-rearmed");
  });
  root.addEventListener("keydown", event => {
    if (event.key === "Escape" &&
        (panel.dataset.open === "true" || panel.matches(":hover") || panel.contains(document.activeElement))) {
      closeLauncher(true);
      event.preventDefault();
    }
  });
  if (trigger) trigger.addEventListener("click", () => {
    const shouldOpen = panel.dataset.open !== "true";
    if (shouldOpen) panel.dataset.dismissed = "false";
    if (shouldOpen) {
      panel.dataset.open = "true";
      traceLauncher("launcher-opened-by-button");
    } else closeLauncher(false);
  });
  // An application may navigate its own iframe to another application URL.
  // If it lands on a shell document, recover the intended selection in this
  // existing parent instead of allowing a recursively nested shell.
  frame.addEventListener("load", () => {
    try {
      const child = frame.contentWindow;
      if (!child) return;
      const path = child.location.pathname;
      const destination = [...allowed.values()].find(app => app.route === path ||
        (app.id !== "desktop" && path.startsWith(app.route + "/")));
      if (!destination) return;
      // Native children may navigate between apps without involving the host.
      // Reconcile their destination with this one persistent launcher.
      if (destination.id !== active) {
        select(destination.id, true);
      } else if (child.document?.getElementById("mcel-applications-shell")) {
        // Defensive recovery for a nested shell (e.g., lost child-only query).
        const native = new URL(child.location.href);
        native.searchParams.set(childOnlyKey, "1");
        frame.src = native.pathname + native.search;
      }
    } catch (e) { /* Cross-origin child: do not inspect or modify it. */ }
  });
  if (!select(currentId(), false)) return refuse("initial-navigation-refused");
  document.body.dataset.mcelShellReady = "true";
  root.__mcelApplicationsShell = Object.freeze({
    appId: ir.application.appId, contract: "mcel.applications-shell.candidate.v1",
    layoutAuthority: "mcel.layout-tree.v1", select, getActive: () => active,
    routePreview, canonicalPromoted, availableApplications: () => [...allowed.keys()],
  });
})(window);
