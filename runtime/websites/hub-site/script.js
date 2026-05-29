(() => {
  const siteName = "Hub Site";
  const siteKind = "hub-site";
  document.documentElement.dataset.mainComputerWebsite = siteKind;
  console.info(`Main Computer website loaded: ${siteName}`);
})();

(() => {
  const mcBlogWidgetSelector = '[data-mc-widget="blog-list"]';
  const mcBlogPostsEndpoint = "/api/site/blog/posts";
  const mcBlogPostBasePath = "/blog/";

  function mcBlogWidgetEscapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function mcBlogWidgetFormatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
  }

  function mcBlogWidgetPostHref(post) {
    const slug = post && post.slug ? String(post.slug) : "";
    return slug ? mcBlogPostBasePath + encodeURIComponent(slug) : "#";
  }

  function mcBlogWidgetRenderPosts(widget, posts) {
    const target = widget.querySelector("[data-mc-blog-posts]") || widget;
    const limit = Math.max(1, Number(widget.getAttribute("data-limit") || widget.dataset.limit || 3) || 3);
    const visiblePosts = Array.isArray(posts) ? posts.slice(0, limit) : [];
    if (!visiblePosts.length) {
      widget.dataset.blogState = "empty";
      target.innerHTML = "";
      return;
    }
    widget.dataset.blogState = "ready";
    target.innerHTML = visiblePosts.map((post) => {
      const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
      const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
      const href = mcBlogWidgetEscapeHtml(mcBlogWidgetPostHref(post));
      const date = mcBlogWidgetFormatDate(post.published_at || post.date_created || post.updated_at);
      const dateHtml = date ? '<time class="mc-blog-card__date">' + mcBlogWidgetEscapeHtml(date) + "</time>" : "";
      const excerptHtml = excerpt ? '<p class="mc-blog-card__excerpt">' + excerpt + "</p>" : "";
      return '<article class="mc-blog-card"><a class="mc-blog-card__title" href="' + href + '">' + title + "</a>" + excerptHtml + dateHtml + "</article>";
    }).join("");
  }

  async function mcBlogWidgetHydrate(widget) {
    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostsEndpoint, {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Blog posts are not available.");
      }
      mcBlogWidgetRenderPosts(widget, payload.posts || []);
    } catch (error) {
      widget.dataset.blogState = "unavailable";
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.innerHTML = "";
      console.info("Main Computer blog widget unavailable:", error);
    }
  }

  function mcBlogWidgetHydrateAll() {
    document.querySelectorAll(mcBlogWidgetSelector).forEach((widget) => mcBlogWidgetHydrate(widget));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mcBlogWidgetHydrateAll);
  } else {
    mcBlogWidgetHydrateAll();
  }
})();

(() => {
  const mcBlogWidgetSelector = '[data-mc-widget="blog-list"]';
  const mcBlogPostViewerSelector = '[data-mc-widget="blog-post-viewer"]';
  const mcBlogPostsEndpoint = "/api/site/blog/posts";
  const mcBlogPostEndpointBase = "/api/site/blog/posts/";
  const mcBlogDefaultPostBasePath = "/blog/";

  function mcBlogWidgetEscapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function mcBlogWidgetFormatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
  }

  function mcBlogWidgetTextToParagraphs(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return text
      .split(/\n{2,}/)
      .map((part) => "<p>" + mcBlogWidgetEscapeHtml(part).replace(/\n/g, "<br>") + "</p>")
      .join("");
  }

  function mcBlogWidgetPostBasePath(widget) {
    const configured = widget.getAttribute("data-post-base-path") || widget.dataset.postBasePath || "";
    return configured || mcBlogDefaultPostBasePath;
  }

  function mcBlogWidgetPostHref(widget, post) {
    const slug = post && post.slug ? String(post.slug) : "";
    if (!slug) return "#";
    const encodedSlug = encodeURIComponent(slug);
    const base = mcBlogWidgetPostBasePath(widget);
    if (base.includes("{slug}")) return base.replace("{slug}", encodedSlug);
    if (base.includes(":slug")) return base.replace(":slug", encodedSlug);
    if (base.includes("?")) return base + encodedSlug;
    return base.replace(/\/?$/, "/") + encodedSlug;
  }

  function mcBlogWidgetRenderPosts(widget, posts) {
    const target = widget.querySelector("[data-mc-blog-posts]") || widget;
    const limit = Math.max(1, Number(widget.getAttribute("data-limit") || widget.dataset.limit || 3) || 3);
    const visiblePosts = Array.isArray(posts) ? posts.slice(0, limit) : [];
    if (!visiblePosts.length) {
      widget.dataset.blogState = "empty";
      target.innerHTML = "";
      return;
    }
    widget.dataset.blogState = "ready";
    target.innerHTML = visiblePosts.map((post) => {
      const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
      const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
      const href = mcBlogWidgetEscapeHtml(mcBlogWidgetPostHref(widget, post));
      const date = mcBlogWidgetFormatDate(post.published_at || post.date_created || post.updated_at);
      const dateHtml = date ? '<time class="mc-blog-card__date">' + mcBlogWidgetEscapeHtml(date) + "</time>" : "";
      const excerptHtml = excerpt ? '<p class="mc-blog-card__excerpt">' + excerpt + "</p>" : "";
      return '<article class="mc-blog-card"><a class="mc-blog-card__title" href="' + href + '">' + title + "</a>" + excerptHtml + dateHtml + "</article>";
    }).join("");
  }

  async function mcBlogWidgetHydrateList(widget) {
    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostsEndpoint, {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Blog posts are not available.");
      }
      mcBlogWidgetRenderPosts(widget, payload.posts || []);
    } catch (error) {
      widget.dataset.blogState = "unavailable";
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.innerHTML = "";
      console.info("Main Computer blog widget unavailable:", error);
    }
  }

  function mcBlogWidgetSlugFromLocation(widget) {
    const explicitSlug = widget.getAttribute("data-slug") || widget.dataset.slug || "";
    if (explicitSlug.trim()) return explicitSlug.trim();

    const params = new URLSearchParams(window.location.search || "");
    const querySlug = params.get("slug") || params.get("post") || params.get("blog_post") || "";
    if (querySlug.trim()) return querySlug.trim();

    const routePrefix = widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || mcBlogDefaultPostBasePath;
    const prefix = String(routePrefix || "").replace(/\/?$/, "/");
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}

    if (path.startsWith(prefix)) {
      return path.slice(prefix.length).replace(/^\/+|\/+$/g, "");
    }
    return "";
  }

  function mcBlogWidgetPathname() {
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}
    return path.replace(/\/+$/g, "") || "/";
  }

  function mcBlogWidgetRouteInfo(widget) {
    const configured = widget && (widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || "");
    let prefix = String(configured || mcBlogDefaultPostBasePath || "/blog/").trim() || "/blog/";
    if (!prefix.startsWith("/")) prefix = "/" + prefix;
    prefix = prefix.replace(/\/?$/, "/");
    const root = prefix.replace(/\/+$/g, "") || "/";
    return {prefix, root};
  }

  function mcBlogWidgetIsOnRoute(widget) {
    const {prefix, root} = mcBlogWidgetRouteInfo(widget);
    const path = mcBlogWidgetPathname();
    return path === root || path.startsWith(prefix);
  }

  function mcBlogWidgetApplyGeneratedPageMode(listWidgets, viewers) {
    const body = document.body;
    const viewer = Array.isArray(viewers) && viewers.length ? viewers[0] : document.querySelector(mcBlogPostViewerSelector);
    const slug = viewer ? mcBlogWidgetSlugFromLocation(viewer) : "";
    const hasGeneratedMarker = Boolean(body && body.hasAttribute("data-mc-generated-blog-page"));
    const hasCombinedBlogShell = Boolean(viewer && Array.isArray(listWidgets) && listWidgets.length);
    const shouldManageRoute = hasGeneratedMarker || (hasCombinedBlogShell && mcBlogWidgetIsOnRoute(viewer));
    if (!body || !shouldManageRoute) {
      return {managed: false, mode: "custom", slug: ""};
    }
    const mode = slug ? "detail" : "index";
    body.setAttribute("data-mc-blog-route-mode", mode);
    body.dataset.mcBlogRouteMode = mode;
    return {managed: true, mode, slug};
  }

  function mcBlogWidgetSetRouteHidden(widget, hidden) {
    if (!widget) return;
    widget.hidden = Boolean(hidden);
    if (hidden) {
      widget.setAttribute("aria-hidden", "true");
    } else {
      widget.removeAttribute("aria-hidden");
    }
  }

  function mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers) {
    if (!routeMode || !routeMode.managed) return;
    const isDetail = routeMode.mode === "detail";
    listWidgets.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, isDetail));
    postViewers.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, !isDetail));
  }

  function mcBlogWidgetRenderPost(widget, post) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
    const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
    const date = mcBlogWidgetFormatDate(post.published_at || post.date_created || post.updated_at);
    const bodyHtml = mcBlogWidgetTextToParagraphs(post.body || post.content || post.excerpt || "");
    const dateHtml = date ? '<time class="mc-blog-post-widget__date">' + mcBlogWidgetEscapeHtml(date) + "</time>" : "";
    const excerptHtml = excerpt ? '<p class="mc-blog-post-widget__excerpt">' + excerpt + "</p>" : "";
    const body = bodyHtml || "<p>This post does not have body content yet.</p>";
    widget.dataset.blogState = "ready";
    if (post.title || post.slug) {
      document.title = (post.title || post.slug || "Blog post") + " - Blog";
    }
    target.innerHTML = '<article class="mc-blog-post-widget__article">' + dateHtml + '<h1>' + title + "</h1>" + excerptHtml + '<div class="mc-blog-post-widget__body">' + body + "</div></article>";
  }

  function mcBlogWidgetRenderPostMessage(widget, message, state) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    widget.dataset.blogState = state;
    target.innerHTML = '<article class="mc-blog-post-widget__empty">' + mcBlogWidgetEscapeHtml(message) + "</article>";
  }

  async function mcBlogWidgetHydratePostViewer(widget) {
    const slug = mcBlogWidgetSlugFromLocation(widget);
    if (!slug) {
      mcBlogWidgetRenderPostMessage(widget, "Choose a blog post to view it here.", "waiting");
      return;
    }

    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostEndpointBase + encodeURIComponent(slug), {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (response.status === 404) {
        mcBlogWidgetRenderPostMessage(widget, "Post not found.", "not-found");
        return;
      }
      if (!response.ok || payload.ok === false || !payload.post) {
        throw new Error(payload.error || "Blog post is not available.");
      }
      mcBlogWidgetRenderPost(widget, payload.post);
    } catch (error) {
      mcBlogWidgetRenderPostMessage(widget, "Blog post is not available right now.", "unavailable");
      console.info("Main Computer blog post widget unavailable:", error);
    }
  }

  function mcBlogWidgetHydrateAll() {
    const listWidgets = Array.from(document.querySelectorAll(mcBlogWidgetSelector));
    const postViewers = Array.from(document.querySelectorAll(mcBlogPostViewerSelector));
    const routeMode = mcBlogWidgetApplyGeneratedPageMode(listWidgets, postViewers);
    mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers);

    listWidgets.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "detail") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydrateList(widget);
    });

    postViewers.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "index") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydratePostViewer(widget);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mcBlogWidgetHydrateAll);
  } else {
    mcBlogWidgetHydrateAll();
  }
})();

(() => {
  const mcBlogWidgetSelector = '[data-mc-widget="blog-list"]';
  const mcBlogPostViewerSelector = '[data-mc-widget="blog-post-viewer"]';
  const mcBlogPostsEndpoint = "/api/site/blog/posts";
  const mcBlogPostEndpointBase = "/api/site/blog/posts/";
  const mcBlogDefaultPostBasePath = "/blog/";

  function mcBlogWidgetEscapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function mcBlogWidgetFormatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
  }

  function mcBlogWidgetTextToParagraphs(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    return text
      .split(/\n{2,}/)
      .map((part) => "<p>" + mcBlogWidgetEscapeHtml(part).replace(/\n/g, "<br>") + "</p>")
      .join("");
  }

  function mcBlogWidgetLooksLikeHtml(value) {
    return /<\/?[a-z][\s\S]*>/i.test(String(value || ""));
  }

  function mcBlogWidgetSafeHref(value) {
    const href = String(value || "").trim();
    if (!href) return "";
    if (href.startsWith("#") || href.startsWith("/") || href.startsWith("./") || href.startsWith("../")) {
      return href;
    }
    try {
      const url = new URL(href, window.location.origin);
      if (["http:", "https:", "mailto:", "tel:"].includes(url.protocol.toLowerCase())) {
        return href;
      }
    } catch {}
    return "";
  }

  function mcBlogWidgetSanitizeRichHtml(value) {
    const html = String(value || "").trim();
    if (!html) return "";
    const allowedTags = new Set(["p", "br", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li", "blockquote", "pre", "code", "h2", "h3", "h4", "hr", "span"]);
    const dropTags = new Set(["script", "style", "iframe", "object", "embed", "link", "meta"]);
    const template = document.createElement("template");
    template.innerHTML = html;

    function cleanNode(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        return document.createTextNode(node.textContent || "");
      }
      if (node.nodeType !== Node.ELEMENT_NODE) {
        return document.createDocumentFragment();
      }
      const tag = String(node.tagName || "").toLowerCase();
      if (dropTags.has(tag)) {
        return document.createDocumentFragment();
      }
      const children = document.createDocumentFragment();
      Array.from(node.childNodes || []).forEach((child) => {
        children.appendChild(cleanNode(child));
      });
      if (!allowedTags.has(tag)) {
        return children;
      }
      const element = document.createElement(tag);
      if (tag === "a") {
        const href = mcBlogWidgetSafeHref(node.getAttribute("href") || "");
        if (href) element.setAttribute("href", href);
        const title = String(node.getAttribute("title") || "").trim();
        if (title) element.setAttribute("title", title);
        const target = String(node.getAttribute("target") || "").trim();
        if (target === "_blank") element.setAttribute("target", "_blank");
        element.setAttribute("rel", "noopener noreferrer");
      }
      element.appendChild(children);
      return element;
    }

    const output = document.createElement("div");
    Array.from(template.content.childNodes || []).forEach((node) => {
      output.appendChild(cleanNode(node));
    });
    return output.innerHTML.trim();
  }

  function mcBlogWidgetRenderBodyHtml(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (mcBlogWidgetLooksLikeHtml(text)) {
      const sanitized = mcBlogWidgetSanitizeRichHtml(text);
      if (sanitized) return sanitized;
    }
    return mcBlogWidgetTextToParagraphs(text);
  }

  function mcBlogWidgetPostBasePath(widget) {
    const configured = widget.getAttribute("data-post-base-path") || widget.dataset.postBasePath || "";
    return configured || mcBlogDefaultPostBasePath;
  }

  function mcBlogWidgetPostHref(widget, post) {
    const slug = post && post.slug ? String(post.slug) : "";
    if (!slug) return "#";
    const encodedSlug = encodeURIComponent(slug);
    const base = mcBlogWidgetPostBasePath(widget);
    if (base.includes("{slug}")) return base.replace("{slug}", encodedSlug);
    if (base.includes(":slug")) return base.replace(":slug", encodedSlug);
    if (base.includes("?")) return base + encodedSlug;
    return base.replace(/\/?$/, "/") + encodedSlug;
  }

  function mcBlogWidgetRenderPosts(widget, posts) {
    const target = widget.querySelector("[data-mc-blog-posts]") || widget;
    const limit = Math.max(1, Number(widget.getAttribute("data-limit") || widget.dataset.limit || 3) || 3);
    const visiblePosts = Array.isArray(posts) ? posts.slice(0, limit) : [];
    if (!visiblePosts.length) {
      widget.dataset.blogState = "empty";
      target.innerHTML = "";
      return;
    }
    widget.dataset.blogState = "ready";
    target.innerHTML = visiblePosts.map((post) => {
      const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
      const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
      const href = mcBlogWidgetEscapeHtml(mcBlogWidgetPostHref(widget, post));
      const date = mcBlogWidgetFormatDate(post.published_at || post.date_created || post.updated_at);
      const dateHtml = date ? '<time class="mc-blog-card__date">' + mcBlogWidgetEscapeHtml(date) + "</time>" : "";
      const excerptHtml = excerpt ? '<p class="mc-blog-card__excerpt">' + excerpt + "</p>" : "";
      return '<article class="mc-blog-card"><a class="mc-blog-card__title" href="' + href + '">' + title + "</a>" + excerptHtml + dateHtml + "</article>";
    }).join("");
  }

  async function mcBlogWidgetHydrateList(widget) {
    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostsEndpoint, {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Blog posts are not available.");
      }
      mcBlogWidgetRenderPosts(widget, payload.posts || []);
    } catch (error) {
      widget.dataset.blogState = "unavailable";
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.innerHTML = "";
      console.info("Main Computer blog widget unavailable:", error);
    }
  }

  function mcBlogWidgetSlugFromLocation(widget) {
    const explicitSlug = widget.getAttribute("data-slug") || widget.dataset.slug || "";
    if (explicitSlug.trim()) return explicitSlug.trim();

    const params = new URLSearchParams(window.location.search || "");
    const querySlug = params.get("slug") || params.get("post") || params.get("blog_post") || "";
    if (querySlug.trim()) return querySlug.trim();

    const routePrefix = widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || mcBlogDefaultPostBasePath;
    const prefix = String(routePrefix || "").replace(/\/?$/, "/");
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}

    if (path.startsWith(prefix)) {
      return path.slice(prefix.length).replace(/^\/+|\/+$/g, "");
    }
    return "";
  }

  function mcBlogWidgetPathname() {
    let path = window.location.pathname || "/";
    try {
      path = decodeURIComponent(path);
    } catch {}
    return path.replace(/\/+$/g, "") || "/";
  }

  function mcBlogWidgetRouteInfo(widget) {
    const configured = widget && (widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || "");
    let prefix = String(configured || mcBlogDefaultPostBasePath || "/blog/").trim() || "/blog/";
    if (!prefix.startsWith("/")) prefix = "/" + prefix;
    prefix = prefix.replace(/\/?$/, "/");
    const root = prefix.replace(/\/+$/g, "") || "/";
    return {prefix, root};
  }

  function mcBlogWidgetIsOnRoute(widget) {
    const {prefix, root} = mcBlogWidgetRouteInfo(widget);
    const path = mcBlogWidgetPathname();
    return path === root || path.startsWith(prefix);
  }

  function mcBlogWidgetApplyGeneratedPageMode(listWidgets, viewers) {
    const body = document.body;
    const viewer = Array.isArray(viewers) && viewers.length ? viewers[0] : document.querySelector(mcBlogPostViewerSelector);
    const slug = viewer ? mcBlogWidgetSlugFromLocation(viewer) : "";
    const hasGeneratedMarker = Boolean(body && body.hasAttribute("data-mc-generated-blog-page"));
    const hasCombinedBlogShell = Boolean(viewer && Array.isArray(listWidgets) && listWidgets.length);
    const shouldManageRoute = hasGeneratedMarker || (hasCombinedBlogShell && mcBlogWidgetIsOnRoute(viewer));
    if (!body || !shouldManageRoute) {
      return {managed: false, mode: "custom", slug: ""};
    }
    const mode = slug ? "detail" : "index";
    body.setAttribute("data-mc-blog-route-mode", mode);
    body.dataset.mcBlogRouteMode = mode;
    return {managed: true, mode, slug};
  }

  function mcBlogWidgetSetRouteHidden(widget, hidden) {
    if (!widget) return;
    widget.hidden = Boolean(hidden);
    if (hidden) {
      widget.setAttribute("aria-hidden", "true");
    } else {
      widget.removeAttribute("aria-hidden");
    }
  }

  function mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers) {
    if (!routeMode || !routeMode.managed) return;
    const isDetail = routeMode.mode === "detail";
    listWidgets.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, isDetail));
    postViewers.forEach((widget) => mcBlogWidgetSetRouteHidden(widget, !isDetail));
  }

  function mcBlogWidgetRenderPost(widget, post) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
    const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
    const date = mcBlogWidgetFormatDate(post.published_at || post.date_created || post.updated_at);
    const bodyHtml = mcBlogWidgetRenderBodyHtml(post.body || post.content || post.excerpt || "");
    const dateHtml = date ? '<time class="mc-blog-post-widget__date">' + mcBlogWidgetEscapeHtml(date) + "</time>" : "";
    const excerptHtml = excerpt ? '<p class="mc-blog-post-widget__excerpt">' + excerpt + "</p>" : "";
    const body = bodyHtml || "<p>This post does not have body content yet.</p>";
    widget.dataset.blogState = "ready";
    if (post.title || post.slug) {
      document.title = (post.title || post.slug || "Blog post") + " - Blog";
    }
    target.innerHTML = '<article class="mc-blog-post-widget__article">' + dateHtml + '<h1>' + title + "</h1>" + excerptHtml + '<div class="mc-blog-post-widget__body">' + body + "</div></article>";
  }

  function mcBlogWidgetRenderPostMessage(widget, message, state) {
    const target = widget.querySelector("[data-mc-blog-post-viewer]") || widget;
    widget.dataset.blogState = state;
    target.innerHTML = '<article class="mc-blog-post-widget__empty">' + mcBlogWidgetEscapeHtml(message) + "</article>";
  }

  async function mcBlogWidgetHydratePostViewer(widget) {
    const slug = mcBlogWidgetSlugFromLocation(widget);
    if (!slug) {
      mcBlogWidgetRenderPostMessage(widget, "Choose a blog post to view it here.", "waiting");
      return;
    }

    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogPostEndpointBase + encodeURIComponent(slug), {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (response.status === 404) {
        mcBlogWidgetRenderPostMessage(widget, "Post not found.", "not-found");
        return;
      }
      if (!response.ok || payload.ok === false || !payload.post) {
        throw new Error(payload.error || "Blog post is not available.");
      }
      mcBlogWidgetRenderPost(widget, payload.post);
    } catch (error) {
      mcBlogWidgetRenderPostMessage(widget, "Blog post is not available right now.", "unavailable");
      console.info("Main Computer blog post widget unavailable:", error);
    }
  }

  function mcBlogWidgetHydrateAll() {
    const listWidgets = Array.from(document.querySelectorAll(mcBlogWidgetSelector));
    const postViewers = Array.from(document.querySelectorAll(mcBlogPostViewerSelector));
    const routeMode = mcBlogWidgetApplyGeneratedPageMode(listWidgets, postViewers);
    mcBlogWidgetApplyRouteModeVisibility(routeMode, listWidgets, postViewers);

    listWidgets.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "detail") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydrateList(widget);
    });

    postViewers.forEach((widget) => {
      if (routeMode.managed && routeMode.mode === "index") {
        widget.dataset.blogState = "route-hidden";
        return;
      }
      mcBlogWidgetHydratePostViewer(widget);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mcBlogWidgetHydrateAll);
  } else {
    mcBlogWidgetHydrateAll();
  }
})();