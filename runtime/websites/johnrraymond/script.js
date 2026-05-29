(() => {
  const siteName = "johnrraymond";
  const siteKind = "static-site";
  document.documentElement.dataset.mainComputerWebsite = siteKind;
  console.info(`Main Computer website loaded: ${siteName}`);
})();

(() => {
  const mcBlogWidgetSelector = '[data-mc-widget="blog-list"]';
  const mcBlogPostViewerSelector = '[data-mc-widget="blog-post-viewer"]';
  const mcBlogPostsEndpoint = "/api/site/blog/posts";
  const mcBlogPostEndpointBase = "/api/site/blog/posts/";
  const mcBlogDefaultPostBasePath = "/blog/";
  const mcBlogDefaultPageSize = 50;
  const mcBlogMaxAllowedFuzz = 5;

  function mcBlogWidgetEscapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function mcBlogWidgetPublishedDateValue(post) {
    if (!post) return "";
    return post.published_on || post.published_at || post.date_created || post.updated_at || "";
  }

  function mcBlogWidgetFormatDate(value) {
    if (!value) return "";
    const text = String(value).trim();
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(text);
    const date = dateOnly
      ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
      : new Date(text);
    if (Number.isNaN(date.getTime())) return text;
    return date.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
  }

  function mcBlogWidgetFormatReadTime(value) {
    const minutes = Number(value);
    if (!Number.isFinite(minutes) || minutes <= 0) return "";
    const rounded = Math.round(minutes);
    return rounded + " min read";
  }

  function mcBlogWidgetMetaHtml(blockClass, post) {
    const date = mcBlogWidgetFormatDate(mcBlogWidgetPublishedDateValue(post));
    const readTime = mcBlogWidgetFormatReadTime(post && post.read_time_minutes);
    const parts = [];
    if (date) parts.push('<time class="' + blockClass + '__date">' + mcBlogWidgetEscapeHtml(date) + "</time>");
    if (readTime) parts.push('<span class="' + blockClass + '__read-time">' + mcBlogWidgetEscapeHtml(readTime) + "</span>");
    if (!parts.length) return "";
    return parts.join('<span aria-hidden="true"> · </span>');
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

  function mcBlogWidgetClampInt(value, fallback, minimum, maximum) {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    let next = Number.isFinite(parsed) ? parsed : fallback;
    next = Math.max(minimum, next);
    if (Number.isFinite(maximum)) next = Math.min(maximum, next);
    return next;
  }

  function mcBlogWidgetDefaultPageSize(widget) {
    return mcBlogWidgetClampInt(
      widget.getAttribute("data-page-size") || widget.dataset.pageSize || widget.getAttribute("data-limit") || widget.dataset.limit,
      mcBlogDefaultPageSize,
      1
    );
  }

  function mcBlogWidgetIsPagedList(widget) {
    const explicit = String(widget.getAttribute("data-search-enabled") || widget.dataset.searchEnabled || widget.getAttribute("data-pagination-enabled") || widget.dataset.paginationEnabled || "").toLowerCase();
    if (["1", "true", "yes", "on"].includes(explicit)) return true;
    if (["0", "false", "no", "off"].includes(explicit)) return false;
    const body = document.body;
    return Boolean(body && body.hasAttribute("data-mc-generated-blog-page") && mcBlogWidgetIsOnRoute(widget));
  }

  function mcBlogWidgetListState(widget, paged) {
    const params = new URLSearchParams(window.location.search || "");
    const defaultPerPage = mcBlogWidgetDefaultPageSize(widget);
    const routeInfo = mcBlogWidgetRouteInfo(widget);
    const listPath = routeInfo.root || "/blog";
    if (!paged) {
      return {paged: false, query: "", fuzz: 0, page: 1, perPage: defaultPerPage, defaultPerPage, listPath};
    }
    const query = String(params.get("q") || params.get("search") || params.get("query") || "").trim();
    const fuzz = mcBlogWidgetClampInt(params.get("fuzz") || params.get("allowed_fuzz") || params.get("allowedFuzz"), 0, 0, mcBlogMaxAllowedFuzz);
    const page = mcBlogWidgetClampInt(params.get("page"), 1, 1);
    const perPage = mcBlogWidgetClampInt(params.get("per_page") || params.get("perPage") || params.get("results_per_page"), defaultPerPage, 1);
    return {paged: true, query, fuzz, page, perPage, defaultPerPage, listPath};
  }

  function mcBlogWidgetApiUrlForState(state) {
    if (!state || !state.paged) return mcBlogPostsEndpoint;
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.fuzz > 0) params.set("fuzz", String(state.fuzz));
    params.set("page", String(state.page || 1));
    params.set("per_page", String(state.perPage || mcBlogDefaultPageSize));
    const query = params.toString();
    return mcBlogPostsEndpoint + (query ? "?" + query : "");
  }

  function mcBlogWidgetPageUrl(state, page) {
    const params = new URLSearchParams(window.location.search || "");
    ["q", "search", "query", "fuzz", "allowed_fuzz", "allowedFuzz", "page", "per_page", "perPage", "results_per_page", "limit"].forEach((name) => params.delete(name));
    if (state.query) params.set("q", state.query);
    if (state.fuzz > 0) params.set("fuzz", String(state.fuzz));
    if (state.perPage !== state.defaultPerPage) params.set("per_page", String(state.perPage));
    if (page > 1) params.set("page", String(page));
    const query = params.toString();
    const listPath = String((state && state.listPath) || window.location.pathname || "/blog").replace(/\/index\.html$/i, "").replace(/\/+$/g, "") || "/";
    return listPath + (query ? "?" + query : "");
  }

  function mcBlogWidgetControlsState(widget, currentState) {
    const form = widget.querySelector("[data-mc-blog-controls]");
    if (!form) return {...currentState, page: 1};
    const searchInput = form.querySelector("[data-mc-blog-search]");
    const fuzzInput = form.querySelector("[data-mc-blog-fuzz]");
    const perPageInput = form.querySelector("[data-mc-blog-per-page]");
    return {
      ...currentState,
      query: String(searchInput ? searchInput.value : "").trim(),
      fuzz: mcBlogWidgetClampInt(fuzzInput ? fuzzInput.value : 0, 0, 0, mcBlogMaxAllowedFuzz),
      perPage: mcBlogWidgetClampInt(perPageInput ? perPageInput.value : currentState.defaultPerPage, currentState.defaultPerPage, 1),
      page: 1
    };
  }

  function mcBlogWidgetEnsureControls(widget, state) {
    if (!state.paged) return;
    let form = widget.querySelector("[data-mc-blog-controls]");
    if (!form) {
      form = document.createElement("form");
      form.className = "mc-blog-widget__controls";
      form.setAttribute("data-mc-blog-controls", "");
      form.innerHTML = '<label class="mc-blog-widget__control"><span>Search</span><input type="search" name="q" autocomplete="off" data-mc-blog-search></label><label class="mc-blog-widget__control"><span>Allowed Fuzz</span><input type="number" name="fuzz" min="0" max="' + mcBlogMaxAllowedFuzz + '" step="1" value="0" data-mc-blog-fuzz></label><label class="mc-blog-widget__control"><span>Results per Page</span><input type="number" name="per_page" min="1" step="1" value="' + mcBlogDefaultPageSize + '" data-mc-blog-per-page></label><button class="mc-blog-widget__apply" type="submit">Apply</button>';
      const header = widget.querySelector(".mc-blog-widget__header");
      if (header && header.parentNode) {
        header.insertAdjacentElement("afterend", form);
      } else {
        widget.insertAdjacentElement("afterbegin", form);
      }
    }
    if (!form.dataset.mcBlogControlsBound) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const nextState = mcBlogWidgetControlsState(widget, state);
        window.location.assign(mcBlogWidgetPageUrl(nextState, 1));
      });
      form.dataset.mcBlogControlsBound = "true";
    }
  }

  function mcBlogWidgetUpdateControls(widget, state, pagination) {
    if (!state.paged) return;
    mcBlogWidgetEnsureControls(widget, state);
    const form = widget.querySelector("[data-mc-blog-controls]");
    if (form) {
      const searchInput = form.querySelector("[data-mc-blog-search]");
      const fuzzInput = form.querySelector("[data-mc-blog-fuzz]");
      const perPageInput = form.querySelector("[data-mc-blog-per-page]");
      if (searchInput) searchInput.value = state.query || "";
      if (fuzzInput) {
        fuzzInput.value = String(state.fuzz || 0);
        fuzzInput.max = String((pagination && pagination.max_allowed_fuzz) || mcBlogMaxAllowedFuzz);
      }
      if (perPageInput) {
        perPageInput.value = String((pagination && pagination.per_page) || state.perPage || state.defaultPerPage);
        if (pagination && Number(pagination.total) > 0) {
          perPageInput.max = String(pagination.total);
        } else {
          perPageInput.removeAttribute("max");
        }
      }
    }
    let summary = widget.querySelector("[data-mc-blog-summary]");
    if (!summary) {
      summary = document.createElement("p");
      summary.className = "mc-blog-widget__summary";
      summary.setAttribute("data-mc-blog-summary", "");
      const form = widget.querySelector("[data-mc-blog-controls]");
      if (form && form.parentNode) {
        form.insertAdjacentElement("afterend", summary);
      } else {
        widget.insertAdjacentElement("afterbegin", summary);
      }
    }
    const total = Number(pagination && pagination.total) || 0;
    const page = Number(pagination && pagination.page) || 1;
    const totalPages = Number(pagination && pagination.total_pages) || 1;
    const suffix = state.query ? ' matching "' + state.query + '"' + (state.fuzz > 0 ? " with fuzz " + state.fuzz : "") : "";
    summary.textContent = total + " post" + (total === 1 ? "" : "s") + suffix + " · page " + page + " of " + totalPages;
  }

  function mcBlogWidgetRenderPagination(widget, state, pagination) {
    if (!state.paged) return;
    let nav = widget.querySelector("[data-mc-blog-pagination]");
    if (!nav) {
      nav = document.createElement("nav");
      nav.className = "mc-blog-widget__pagination";
      nav.setAttribute("data-mc-blog-pagination", "");
      nav.setAttribute("aria-label", "Blog pagination");
      const target = widget.querySelector("[data-mc-blog-posts]") || widget;
      target.insertAdjacentElement("afterend", nav);
    }
    const page = Number(pagination && pagination.page) || 1;
    const totalPages = Number(pagination && pagination.total_pages) || 1;
    const previousHtml = pagination && pagination.has_previous
      ? '<a class="mc-blog-widget__page-link" href="' + mcBlogWidgetEscapeHtml(mcBlogWidgetPageUrl(state, page - 1)) + '">← Newer posts</a>'
      : '<span class="mc-blog-widget__page-link is-disabled">← Newer posts</span>';
    const nextHtml = pagination && pagination.has_next
      ? '<a class="mc-blog-widget__page-link" href="' + mcBlogWidgetEscapeHtml(mcBlogWidgetPageUrl(state, page + 1)) + '">Older posts →</a>'
      : '<span class="mc-blog-widget__page-link is-disabled">Older posts →</span>';
    nav.innerHTML = previousHtml + '<span class="mc-blog-widget__page-status">Page ' + page + " of " + totalPages + "</span>" + nextHtml;
  }

  function mcBlogWidgetRenderPosts(widget, posts, pagination, state) {
    const target = widget.querySelector("[data-mc-blog-posts]") || widget;
    const list = Array.isArray(posts) ? posts : [];
    const limit = state && state.paged ? list.length : Math.max(1, Number(widget.getAttribute("data-limit") || widget.dataset.limit || 3) || 3);
    const visiblePosts = state && state.paged ? list : list.slice(0, limit);
    if (!visiblePosts.length) {
      widget.dataset.blogState = "empty";
      const message = state && state.query ? "No posts matched your search." : "No published posts yet.";
      target.innerHTML = '<article class="mc-blog-widget__placeholder" data-mc-blog-empty="true">' + mcBlogWidgetEscapeHtml(message) + "</article>";
      mcBlogWidgetUpdateControls(widget, state || {paged: false}, pagination || {});
      mcBlogWidgetRenderPagination(widget, state || {paged: false}, pagination || {});
      return;
    }
    widget.dataset.blogState = "ready";
    target.innerHTML = visiblePosts.map((post) => {
      const title = mcBlogWidgetEscapeHtml(post.title || post.slug || "Untitled post");
      const excerpt = mcBlogWidgetEscapeHtml(post.excerpt || "");
      const href = mcBlogWidgetEscapeHtml(mcBlogWidgetPostHref(widget, post));
      const metaHtml = mcBlogWidgetMetaHtml("mc-blog-card", post);
      const metaBlockHtml = metaHtml ? '<p class="mc-blog-card__meta">' + metaHtml + "</p>" : "";
      const excerptHtml = excerpt ? '<p class="mc-blog-card__excerpt">' + excerpt + "</p>" : "";
      return '<article class="mc-blog-card"><h2><a class="mc-blog-card__title" href="' + href + '">' + title + "</a></h2>" + metaBlockHtml + excerptHtml + "</article>";
    }).join("");
    mcBlogWidgetUpdateControls(widget, state || {paged: false}, pagination || {});
    mcBlogWidgetRenderPagination(widget, state || {paged: false}, pagination || {});
  }

  async function mcBlogWidgetHydrateList(widget) {
    const paged = mcBlogWidgetIsPagedList(widget);
    const state = mcBlogWidgetListState(widget, paged);
    mcBlogWidgetEnsureControls(widget, state);
    try {
      widget.dataset.blogState = "loading";
      const response = await fetch(mcBlogWidgetApiUrlForState(state), {headers: {"Accept": "application/json"}});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Blog posts are not available.");
      }
      const pagination = payload.pagination || {
        page: state.page,
        per_page: state.perPage,
        total: Array.isArray(payload.posts) ? payload.posts.length : 0,
        total_pages: 1,
        has_previous: false,
        has_next: false,
        default_per_page: state.defaultPerPage,
        max_allowed_fuzz: mcBlogMaxAllowedFuzz
      };
      mcBlogWidgetRenderPosts(widget, payload.posts || [], pagination, state);
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
      const slug = path.slice(prefix.length).replace(/^\/+|\/+$/g, "");
      return slug === "index.html" ? "" : slug;
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
    const configured = widget && (widget.getAttribute("data-route-prefix") || widget.dataset.routePrefix || widget.getAttribute("data-post-base-path") || widget.dataset.postBasePath || "");
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
    const metaHtml = mcBlogWidgetMetaHtml("mc-blog-post-widget", post);
    const bodyHtml = mcBlogWidgetRenderBodyHtml(post.body || post.content || post.excerpt || "");
    const metaBlockHtml = metaHtml ? '<p class="mc-blog-post-widget__meta">' + metaHtml + "</p>" : "";
    const excerptHtml = excerpt ? '<p class="mc-blog-post-widget__excerpt">' + excerpt + "</p>" : "";
    const body = bodyHtml || "<p>This post does not have body content yet.</p>";
    widget.dataset.blogState = "ready";
    if (post.title || post.slug) {
      document.title = (post.title || post.slug || "Blog post") + " - Blog";
    }
    target.innerHTML = '<article class="mc-blog-post-widget__article">' + metaBlockHtml + '<h1>' + title + "</h1>" + excerptHtml + '<div class="mc-blog-post-widget__body">' + body + "</div></article>";
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
