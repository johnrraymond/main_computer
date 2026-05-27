    const websiteBuilderStateModel = {
      loaded: false,
      sites: [],
      selectedSiteId: "",
      selectedSite: null,
      previewMode: "draft",
      previewDevice: "desktop",
      activeTab: "design",
      activeFile: "html",
      previewUpdateTimer: null,
      sourceUpdateTimer: null,
      grapesEditor: null,
      grapesLoadedSiteId: "",
      syncingGrapes: false,
      deploymentControllers: [],
      deploymentControllersLoaded: false,
      busy: false,
      dirty: false,
      acceptedPublishingSetupSignature: "",
      publishedRemoteProdUrls: {},
      localPrepareResult: null,
      pendingPublishingResource: {},
      chatController: null,
      chatOpen: false,
      ragApplyListenerBound: false,
      blogRuntimeWizard: {
        open: false,
        loading: false,
        contract: null,
        activity: [],
        localSites: {},
        pendingDirectusConnection: null
      },
      deployPreflight: {
        open: false,
        lane: "",
        result: null,
        requiresAcknowledgement: false,
        acknowledged: false
      },
      directusConnectionModal: {
        open: false,
        site: null,
        contract: null,
        context: "local_publish",
        requireDirectus: false,
        resolve: null
      }
    };

    const websiteBuilderLinkedChatThreads = new Map();

    const websiteBuilderDefaultRemoteRoot = "/srv/main-computer/sites";

    const websiteBuilderRemoteScpPublishPreset = {
      publishMode: "scp",
      siteSlug: "",
      sourcePath: "",
      remoteHost: "",
      remoteRoot: websiteBuilderDefaultRemoteRoot,
      publishedHost: "",
      lane: "remote_prod"
    };

    const websiteBuilderBackendRuntimeLabels = {
      none: "Static only",
      fastapi: "FastAPI",
      "node-express": "Node / Express",
      worker: "Worker"
    };

    const websiteBuilderBackendProductLabels = {
      api: "API Routes",
      forms: "Forms",
      database: "Database",
      blog: "Blog",
      auth: "Auth",
      email: "Email",
      jobs: "Scheduled Jobs",
      webhooks: "Webhooks",
      ai: "AI Calls",
      secrets: "Secrets",
      logs: "Logs"
    };

    const websiteBuilderBackendRuntimeOrder = ["none", "fastapi", "node-express", "worker"];

    const websiteBuilderBlogLayerInstallOrder = ["database", "cms", "blog"];

    const websiteBuilderBlogLayerLabels = {
      blog: "Blog",
      cms: "Directus CMS",
      database: "SQLite runtime dependency"
    };

    const websiteBuilderBlogLayerOptions = {
      blog: "Blog",
      cms: "Directus",
      database: "SQLite"
    };

    const websiteBuilderBlogLayerDescriptions = {
      blog: "Records blog runtime intent and waits for deploy-verified Directus and SQLite.",
      cms: "Directus is required for Blog and Configure Blog Runtime prepares or verifies it locally.",
      database: "SQLite is required by Blog and Configure Blog Runtime prepares or verifies it locally."
    };


    const websiteBuilderDefaultAssetSvgs = {
      aurora: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#38bdf8"/><stop offset=".45" stop-color="#6366f1"/><stop offset="1" stop-color="#f472b6"/></linearGradient><radialGradient id="r" cx=".72" cy=".22" r=".55"><stop offset="0" stop-color="#fef3c7" stop-opacity=".95"/><stop offset="1" stop-color="#fef3c7" stop-opacity="0"/></radialGradient></defs><rect width="1200" height="720" fill="#020617"/><path d="M0 520C190 410 310 660 520 510s310-430 680-300v510H0z" fill="url(#g)" opacity=".78"/><circle cx="850" cy="160" r="310" fill="url(#r)"/><g fill="#fff" opacity=".16"><circle cx="166" cy="138" r="7"/><circle cx="352" cy="92" r="4"/><circle cx="1040" cy="104" r="6"/><circle cx="970" cy="332" r="5"/></g></svg>`,
      workstation: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720"><rect width="1200" height="720" fill="#0f172a"/><rect x="164" y="110" width="872" height="500" rx="42" fill="#111827" stroke="#64748b" stroke-width="6"/><rect x="218" y="170" width="764" height="360" rx="24" fill="#020617"/><rect x="268" y="220" width="310" height="48" rx="14" fill="#38bdf8"/><rect x="268" y="300" width="560" height="24" rx="12" fill="#94a3b8"/><rect x="268" y="350" width="470" height="24" rx="12" fill="#64748b"/><rect x="268" y="420" width="180" height="58" rx="20" fill="#f59e0b"/><rect x="494" y="420" width="180" height="58" rx="20" fill="#1e293b" stroke="#475569" stroke-width="4"/><path d="M520 612h160l28 64H492z" fill="#475569"/><rect x="420" y="674" width="360" height="26" rx="13" fill="#334155"/></svg>`,
      pattern: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720"><rect width="1200" height="720" fill="#f8fafc"/><g fill="none" stroke="#0f172a" stroke-opacity=".12" stroke-width="2"><path d="M0 120h1200M0 240h1200M0 360h1200M0 480h1200M0 600h1200M120 0v720M240 0v720M360 0v720M480 0v720M600 0v720M720 0v720M840 0v720M960 0v720M1080 0v720"/></g><g fill="#2563eb" opacity=".9"><circle cx="240" cy="180" r="56"/><circle cx="960" cy="540" r="78"/><rect x="522" y="282" width="156" height="156" rx="36"/></g><g fill="#f59e0b" opacity=".85"><circle cx="780" cy="190" r="38"/><rect x="280" y="494" width="118" height="118" rx="30"/></g></svg>`
    };

    function websiteBuilderDataSvg(name) {
      const source = websiteBuilderDefaultAssetSvgs[name] || websiteBuilderDefaultAssetSvgs.aurora;
      return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
    }

    function websiteBuilderDefaultAssets() {
      return [
        {type: "image", name: "Aurora banner", src: websiteBuilderDataSvg("aurora")},
        {type: "image", name: "Workstation mockup", src: websiteBuilderDataSvg("workstation")},
        {type: "image", name: "Pattern card", src: websiteBuilderDataSvg("pattern")}
      ];
    }

    function setWebsiteBuilderLog(message, detail = null) {
      if (!websiteBuilderLog) return;
      const text = typeof message === "string" ? message : JSON.stringify(message, null, 2);
      const detailText = detail ? `\n${typeof detail === "string" ? detail : JSON.stringify(detail, null, 2)}` : "";
      websiteBuilderLog.textContent = `${text}${detailText}`;
    }

    function setWebsiteBuilderBusy(isBusy, label = "") {
      websiteBuilderStateModel.busy = Boolean(isBusy);
      [
        websiteBuilderSave,
        websiteBuilderPublishLocal,
        websiteBuilderPublishDev,
        websiteBuilderPublishRemote,
        websiteBuilderArchive,
        websiteBuilderPublishLocalCard,
        websiteBuilderPublishDevCard,
        websiteBuilderCoolifySave
      ].forEach((button) => {
        if (button) button.disabled = websiteBuilderStateModel.busy;
      });
      updateWebsiteBuilderPublishingSetupControls();
      updateWebsiteBuilderArchiveControl();
      if (label) setWebsiteBuilderLog(label);
    }

    function updateWebsiteBuilderArchiveControl() {
      if (!websiteBuilderArchive) return;
      const siteId = websiteBuilderStateModel.selectedSiteId || "";
      websiteBuilderArchive.disabled = websiteBuilderStateModel.busy || !siteId;
      websiteBuilderArchive.title = siteId === "hub-site"
        ? "Hub Site is protected and cannot be archived."
        : "Archive the selected website project.";
    }

    function markWebsiteBuilderDirty() {
      websiteBuilderStateModel.dirty = true;
      if (websiteBuilderSiteMeta && websiteBuilderStateModel.selectedSite) {
        const site = websiteBuilderStateModel.selectedSite;
        const local = websiteBuilderLaneUrl(site, "local") || "not configured";
        const dev = websiteBuilderLaneUrl(site, "dev") || "not configured";
        websiteBuilderSiteMeta.textContent = `${site.id} · ${site.kind} · unsaved changes · Deploy ${dev} · Local Server ${local}`;
      }
    }

    function currentWebsiteBuilderFilePayload(siteId) {
      syncWebsiteBuilderSourceFromGrapes({markDirty: false});
      return websiteBuilderEnsureBlogWidgetAssets({
        site_id: siteId,
        html: websiteBuilderHtml?.value || "",
        css: websiteBuilderCss?.value || "",
        js: websiteBuilderJs?.value || "",
        builder: websiteBuilderState?.value || ""
      });
    }

    function websiteBuilderNestedError(value) {
      if (!value || typeof value !== "object") return "";
      if (value.error) return String(value.error);
      if (value.message) return String(value.message);
      if (value.blog && typeof value.blog === "object") {
        if (value.blog.error) return String(value.blog.error);
        if (value.blog.message) return String(value.blog.message);
      }
      if (value.payload && typeof value.payload === "object") {
        return websiteBuilderNestedError(value.payload);
      }
      if (value.body && typeof value.body === "string") {
        try {
          const parsed = JSON.parse(value.body);
          const nested = websiteBuilderNestedError(parsed);
          if (nested) return nested;
        } catch {}
        return value.body;
      }
      return "";
    }

    function websiteBuilderApiErrorMessage(payload, response) {
      const result = payload?.result && typeof payload.result === "object" ? payload.result : {};
      const cmsVerify = Array.isArray(result.cms_verify) ? result.cms_verify : [];
      const failedCms = cmsVerify.find((entry) => entry && entry.ok === false);
      return payload?.error
        || payload?.message
        || result.error
        || result.blog_runtime_verify_error
        || websiteBuilderNestedError(result.blog_runtime_verify)
        || websiteBuilderNestedError(result.blog_hydration)
        || result.cms_verify_error
        || result.verify_error
        || (failedCms ? `${failedCms.service || "CMS dependency"}: ${failedCms.error || failedCms.body || failedCms.status || "verification failed"}` : "")
        || `Request failed: ${response.status}`;
    }

    async function websiteBuilderApi(path, options = {}) {
      const response = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(websiteBuilderApiErrorMessage(payload, response));
      }
      return payload;
    }

    function websiteBuilderLane(site, laneName) {
      const platform = site?.local_platform || {};
      const lanes = platform.lanes || {};
      return lanes[laneName] || {};
    }

    function websiteBuilderLaneUrl(site, laneName) {
      const lane = websiteBuilderLane(site, laneName);
      return lane.url || site?.local_platform?.[`${laneName}_url`] || "";
    }

    function websiteBuilderLaneLabel(laneName) {
      if (laneName === "remote_prod" || laneName === "publish") return "Publish";
      return laneName === "dev" ? "Deploy" : "Local Server";
    }

    function normalizeWebsiteBuilderVisitUrl(value) {
      const text = String(value || "").trim();
      if (!text) return "";
      const withProtocol = /^\/\//.test(text) ? `${window.location.protocol}${text}` : text;
      if (/^https?:\/\//i.test(withProtocol)) {
        try {
          const url = new URL(withProtocol);
          if (url.hostname === "0.0.0.0" || url.hostname === "::") {
            url.hostname = "localhost";
          }
          return url.toString();
        } catch (_error) {
          return withProtocol.replace(/^(https?:\/\/)0\.0\.0\.0(?=[:/]|$)/i, "$1localhost");
        }
      }
      return `https://${text}`;
    }

    function websiteBuilderPublishTargets(site) {
      const targets = site?.publish_targets && typeof site.publish_targets === "object" ? site.publish_targets : {};
      const siteId = site?.id || "";
      const remoteRaw = targets.remote_prod && typeof targets.remote_prod === "object" ? targets.remote_prod : {};
      const localRaw = targets.local_prod && typeof targets.local_prod === "object" ? targets.local_prod : {};
      const publishMode = String(remoteRaw.publish_mode || (remoteRaw.use_local_server ? "local_server" : "scp")).trim() || "scp";
      return {
        local_prod: {
          controller_id: localRaw.controller_id || "",
          project: localRaw.project || siteId,
          environment: localRaw.environment || "local-prod",
          domain: localRaw.domain || (siteId ? `${siteId}.localhost` : ""),
          accepted_at: localRaw.accepted_at || ""
        },
        remote_prod: {
          controller_id: remoteRaw.controller_id || "",
          project: remoteRaw.project || remoteRaw.site_slug || siteId,
          environment: remoteRaw.environment || "production",
          domain: remoteRaw.domain || "",
          publish_mode: publishMode,
          use_local_server: Boolean(remoteRaw.use_local_server || publishMode === "local_server"),
          site_slug: remoteRaw.site_slug || remoteRaw.project || siteId,
          source_path: remoteRaw.source_path || (siteId ? `runtime/websites/${siteId}` : ""),
          remote_host: remoteRaw.remote_host || "",
          remote_root: remoteRaw.remote_root || websiteBuilderDefaultRemoteRoot,
          ssh_password: remoteRaw.ssh_password || "",
          resource_uuid: remoteRaw.resource_uuid || "",
          service_uuid: remoteRaw.service_uuid || "",
          application_uuid: remoteRaw.application_uuid || "",
          uuid: remoteRaw.uuid || "",
          accepted_at: remoteRaw.accepted_at || ""
        }
      };
    }

    function websiteBuilderBlogFeature(site) {
      const blog = site?.features?.blog && typeof site.features.blog === "object" ? site.features.blog : null;
      return blog;
    }

    function websiteBuilderPublishingRequiresDirectus(site) {
      const blog = websiteBuilderBlogFeature(site);
      if (blog && (blog.selected || blog.enabled)) return true;
      const cms = site?.backend?.cms && typeof site.backend.cms === "object" ? site.backend.cms : null;
      const provider = String(cms?.provider || "").toLowerCase();
      return Boolean(provider === "directus" && (cms.required || cms.service || cms.local_connection));
    }

    function websiteBuilderPublishDirectusUrlFromSite(site) {
      const cms = site?.backend?.cms && typeof site.backend.cms === "object" ? site.backend.cms : null;
      if (!cms) return "";
      const publish = cms.publish && typeof cms.publish === "object" ? cms.publish : {};
      const targets = cms.targets && typeof cms.targets === "object" ? cms.targets : {};
      const publishTarget = targets.publish && typeof targets.publish === "object" ? targets.publish : {};
      const remoteProdTarget = targets.remote_prod && typeof targets.remote_prod === "object" ? targets.remote_prod : {};
      return String(
        publish.url
        || publish.internal_url
        || publish.public_url
        || publishTarget.url
        || publishTarget.internal_url
        || publishTarget.public_url
        || remoteProdTarget.url
        || remoteProdTarget.internal_url
        || remoteProdTarget.public_url
        || ""
      ).trim();
    }

    function websiteBuilderDirectusUrlLooksValid(value) {
      const text = String(value || "").trim();
      return /^https?:\/\/[^\s/$.?#].*$/i.test(text);
    }

    function websiteBuilderSiteWithPublishDirectusUrl(site, url) {
      if (!site || typeof site !== "object") return site;
      const cleanUrl = String(url || "").trim();
      const backend = site.backend && typeof site.backend === "object" ? {...site.backend} : {};
      const cms = backend.cms && typeof backend.cms === "object" ? {...backend.cms} : {};
      const publish = cms.publish && typeof cms.publish === "object" ? {...cms.publish} : {};
      if (cleanUrl) {
        publish.url = cleanUrl;
      } else {
        delete publish.url;
      }
      cms.provider = cms.provider || "directus";
      cms.publish = publish;
      backend.cms = cms;
      return {...site, backend};
    }

    function websiteBuilderControllerById(controllerId) {
      const id = String(controllerId || "");
      return websiteBuilderStateModel.deploymentControllers.find((controller) => controller.id === id) || null;
    }

    function websiteBuilderControllerLabel(controllerId) {
      const controller = websiteBuilderControllerById(controllerId);
      return controller ? `${controller.name || controller.id} (${controller.base_url || "no URL"})` : (controllerId || "not selected");
    }

    function websiteBuilderRawPublishTarget(site, laneName) {
      const targets = site?.publish_targets && typeof site.publish_targets === "object" ? site.publish_targets : {};
      const target = targets?.[laneName] && typeof targets[laneName] === "object" ? targets[laneName] : {};
      return target;
    }

    function websiteBuilderAcceptedPublishTarget(site) {
      const remote = websiteBuilderPublishTargets(site).remote_prod;
      const mode = String(remote.publish_mode || (remote.use_local_server ? "local_server" : "scp")).trim() || "scp";
      const hasBaseCommand = Boolean(site?.id && remote.accepted_at && remote.site_slug && remote.source_path && remote.remote_root);
      if (!hasBaseCommand) return null;
      if (mode !== "local_server" && !remote.remote_host) return null;
      return {...remote, publish_mode: mode, use_local_server: mode === "local_server"};
    }

    function websiteBuilderLocalPublishedUrl(site) {
      const localLane = websiteBuilderLane(site, "local");
      return normalizeWebsiteBuilderVisitUrl(localLane.last_published_url || localLane.url || websiteBuilderLaneUrl(site, "local"));
    }

    function websiteBuilderRemotePublishUrl(site) {
      const siteId = site?.id || "";
      const publishedUrl = normalizeWebsiteBuilderVisitUrl(siteId ? websiteBuilderStateModel.publishedRemoteProdUrls[siteId] : "");
      if (publishedUrl) return publishedUrl;
      const remote = websiteBuilderAcceptedPublishTarget(site);
      if (!remote) return "";
      const domainUrl = normalizeWebsiteBuilderVisitUrl(remote.domain);
      if (domainUrl) return domainUrl;
      if (remote.use_local_server || remote.publish_mode === "local_server") {
        return websiteBuilderLocalPublishedUrl(site);
      }
      return "";
    }

    function websiteBuilderCanPublishAcceptedSetup(site = websiteBuilderStateModel.selectedSite) {
      return Boolean(site?.id && websiteBuilderAcceptedPublishTarget(site));
    }

    function updateWebsiteBuilderPublishActionControls(site = websiteBuilderStateModel.selectedSite) {
      if (!websiteBuilderPublishRemote) return;
      const canPublish = websiteBuilderCanPublishAcceptedSetup(site);
      websiteBuilderPublishRemote.disabled = websiteBuilderStateModel.busy || !canPublish;
      websiteBuilderPublishRemote.title = websiteBuilderStateModel.busy
        ? "Publishing is in progress..."
        : canPublish
          ? "Publish using the accepted command setup."
          : "Accept a publishing setup before publishing.";
    }

    function websiteBuilderVisitUrl(site, laneName) {
      if (laneName === "remote_prod" || laneName === "publish") {
        return websiteBuilderRemotePublishUrl(site);
      }
      return normalizeWebsiteBuilderVisitUrl(websiteBuilderLaneUrl(site, laneName));
    }

    function setWebsiteBuilderVisitButton(button, url, label) {
      if (!button) return;
      const visitUrl = normalizeWebsiteBuilderVisitUrl(url);
      button.disabled = !visitUrl;
      button.dataset.websiteBuilderVisitUrl = visitUrl;
      button.title = visitUrl ? `Visit ${label}: ${visitUrl}` : `No ${label} URL configured yet.`;
    }

    function updateWebsiteBuilderVisitButtons(site = websiteBuilderStateModel.selectedSite) {
      const localUrl = websiteBuilderVisitUrl(site, "local");
      const devUrl = websiteBuilderVisitUrl(site, "dev");
      const remoteUrl = websiteBuilderVisitUrl(site, "remote_prod");
      setWebsiteBuilderVisitButton(websiteBuilderVisitLocal, localUrl, "Local Server");
      setWebsiteBuilderVisitButton(websiteBuilderVisitLocalCard, localUrl, "Local Server");
      setWebsiteBuilderVisitButton(websiteBuilderVisitDev, devUrl, "Deploy");
      setWebsiteBuilderVisitButton(websiteBuilderVisitDevCard, devUrl, "Deploy");
      setWebsiteBuilderVisitButton(websiteBuilderVisitRemoteProd, remoteUrl, "Publish");
      setWebsiteBuilderVisitButton(websiteBuilderVisitRemoteProdCard, remoteUrl, "Publish");
    }

    function visitWebsiteBuilderTarget(laneName) {
      const site = websiteBuilderStateModel.selectedSite;
      const url = websiteBuilderVisitUrl(site, laneName);
      const label = websiteBuilderLaneLabel(laneName);
      if (!url) {
        setWebsiteBuilderLog(`No ${label} URL is configured for ${site?.id || "this site"}.`);
        return;
      }
      window.open(url, "_blank", "noopener,noreferrer");
    }

    function websiteBuilderRemoteControllers() {
      return websiteBuilderStateModel.deploymentControllers.filter((controller) => {
        const roles = Array.isArray(controller.roles) ? controller.roles : [];
        return roles.includes("remote-prod");
      });
    }

    function renderWebsiteBuilderCoolifyTargets() {
      if (!websiteBuilderCoolifyTargets) return;
      websiteBuilderCoolifyTargets.replaceChildren();
      const controllers = websiteBuilderStateModel.deploymentControllers || [];
      if (!controllers.length) {
        const empty = document.createElement("p");
        empty.textContent = "No legacy Coolify API targets loaded. Publish now uses the saved command setup.";
        websiteBuilderCoolifyTargets.append(empty);
        return;
      }

      controllers.forEach((controller) => {
        const card = document.createElement("div");
        card.className = "website-builder-coolify-target";
        card.setAttribute("role", "listitem");

        const title = document.createElement("strong");
        title.textContent = controller.name || controller.id;

        const meta = document.createElement("span");
        const roles = Array.isArray(controller.roles) ? controller.roles.join(", ") : "";
        const defaults = Array.isArray(controller.default_for) && controller.default_for.length
          ? ` · default for ${controller.default_for.join(", ")}`
          : "";
        meta.textContent = `${controller.id} · ${controller.base_url || "no URL"} · ${roles}${defaults}`;

        const token = document.createElement("small");
        const tokenRef = String(controller.token_ref || "").trim();
        const tokenIsFile = tokenRef.toLowerCase().startsWith("file:");
        token.textContent = controller.has_token_ref || tokenRef
          ? tokenIsFile
            ? `Token file: ${tokenRef}`
            : `Token env/key: ${tokenRef}${controller.has_token_value ? " (set)" : " (not set)"}`
          : "No token configured";

        card.append(title, meta, token);
        websiteBuilderCoolifyTargets.append(card);
      });
    }

    function mergeWebsiteBuilderDeploymentController(controller) {
      if (!controller || typeof controller !== "object" || !controller.id) return;
      const controllers = Array.isArray(websiteBuilderStateModel.deploymentControllers)
        ? [...websiteBuilderStateModel.deploymentControllers]
        : [];
      const index = controllers.findIndex((existing) => existing.id === controller.id);
      if (index >= 0) {
        controllers[index] = {...controllers[index], ...controller};
      } else {
        controllers.push(controller);
      }
      websiteBuilderStateModel.deploymentControllers = controllers;
      websiteBuilderStateModel.deploymentControllersLoaded = true;
      renderWebsiteBuilderCoolifyTargets();
    }

    function websiteBuilderPublishingVisibleSetup() {
      const remote = websiteBuilderPublishTargets(websiteBuilderStateModel.selectedSite).remote_prod;
      return {
        use_local_server: Boolean(websiteBuilderPublishingUseLocalServer?.checked),
        publish_mode: websiteBuilderPublishingUseLocalServer?.checked ? "local_server" : "scp",
        site_slug: websiteBuilderPublishingSiteSlug?.value || remote.site_slug || "",
        source_path: websiteBuilderPublishingSourcePath?.value || remote.source_path || "",
        remote_host: websiteBuilderPublishingSshHost?.value || "",
        ssh_password: websiteBuilderPublishingSshPassword?.value || "",
        remote_root: websiteBuilderPublishingRemoteRoot?.value || remote.remote_root || websiteBuilderDefaultRemoteRoot,
        published_host_domain: websiteBuilderPublishingDomain?.value || "",
        publish_directus_url: websiteBuilderPublishDirectusUrl?.value || ""
      };
    }

    function websiteBuilderRemoteCoolifyCompose(visibleSetup = websiteBuilderPublishingVisibleSetup()) {
      const slug = String(visibleSetup.site_slug || "johnrraymond").trim() || "johnrraymond";
      const root = String(visibleSetup.remote_root || websiteBuilderDefaultRemoteRoot).trim().replace(/\/+$/, "") || websiteBuilderDefaultRemoteRoot;
      const directusUrl = String(visibleSetup.publish_directus_url || "").trim().replace(/\/+$/, "");
      const directusEnv = directusUrl || "${DIRECTUS_URL:-}";
      return [
        "services:",
        `  ${slug}-site:`,
        "    image: 'python:3.12-slim'",
        "    restart: unless-stopped",
        "    working_dir: /app",
        "    command:",
        "      - python",
        `      - /app/sites/${slug}/.main-computer/runtime/app.py`,
        "    environment:",
        `      SITE_ID: ${slug}`,
        `      SITE_NAME: ${slug}`,
        "      SITE_KIND: static-site",
        "      SITE_LANE: production",
        `      MC_SITE_ID: ${slug}`,
        "      MC_RUNTIME_LANE: production",
        "      CONTENT_ROOT: /app/sites",
        "      BLOG_ENABLED: 'true'",
        "      BLOG_PROVIDER: directus",
        "      BLOG_CONTENT_RUNTIME: directus",
        "      BLOG_COLLECTION: posts",
        `      DIRECTUS_URL: '${directusEnv}'`,
        `      DIRECTUS_PUBLIC_URL: '${directusEnv}'`,
        "    volumes:",
        `      - '${root}/${slug}:/app/sites/${slug}:ro'`,
        "    expose:",
        "      - '8080'",
        "    healthcheck:",
        "      test:",
        "        - CMD-SHELL",
        "        - 'python -c \"import sys, urllib.request; sys.exit(0 if urllib.request.urlopen(''http://127.0.0.1:8080/api/site/status'', timeout=5).status == 200 else 1)\"'",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
        "      start_period: 20s"
      ].join("\n");
    }

    function websiteBuilderRenderPublishCommandFields() {
      const visibleSetup = websiteBuilderPublishingVisibleSetup();
      const useLocal = Boolean(visibleSetup.use_local_server);
      if (websiteBuilderRemoteCommandFields) {
        websiteBuilderRemoteCommandFields.hidden = useLocal;
        websiteBuilderRemoteCommandFields.setAttribute("aria-hidden", useLocal ? "true" : "false");
      }
      [websiteBuilderPublishingSshHost, websiteBuilderPublishingSshPassword].forEach((input) => {
        if (input) input.disabled = useLocal;
      });
      if (websiteBuilderCoolifyComposeExample) {
        websiteBuilderCoolifyComposeExample.textContent = websiteBuilderRemoteCoolifyCompose(visibleSetup);
      }
    }

    function setWebsiteBuilderPublishingVisibleSetup(visibleSetup = {}, site = websiteBuilderStateModel.selectedSite) {
      const targets = websiteBuilderPublishTargets(site);
      const remote = targets.remote_prod;
      const useLocal = Boolean(visibleSetup.use_local_server || visibleSetup.publish_mode === "local_server");
      if (websiteBuilderPublishingUseLocalServer) {
        websiteBuilderPublishingUseLocalServer.checked = useLocal;
      }
      if (websiteBuilderPublishingSiteSlug) {
        websiteBuilderPublishingSiteSlug.value = visibleSetup.site_slug || visibleSetup.website_project || remote.site_slug || site?.id || "";
      }
      if (websiteBuilderPublishingSourcePath) {
        websiteBuilderPublishingSourcePath.value = visibleSetup.source_path || remote.source_path || (site?.id ? `runtime/websites/${site.id}` : "");
      }
      if (websiteBuilderPublishingSshHost) {
        websiteBuilderPublishingSshHost.value = visibleSetup.remote_host || "";
      }
      if (websiteBuilderPublishingSshPassword) {
        websiteBuilderPublishingSshPassword.value = visibleSetup.ssh_password || "";
      }
      if (websiteBuilderPublishingRemoteRoot) {
        websiteBuilderPublishingRemoteRoot.value = visibleSetup.remote_root || remote.remote_root || websiteBuilderDefaultRemoteRoot;
      }
      if (websiteBuilderPublishingDomain) {
        websiteBuilderPublishingDomain.value = visibleSetup.published_host_domain || visibleSetup.domain || remote.domain || "";
      }
      if (websiteBuilderPublishDirectusUrl) {
        const configuredUrl = Object.prototype.hasOwnProperty.call(visibleSetup, "publish_directus_url")
          ? visibleSetup.publish_directus_url
          : websiteBuilderPublishDirectusUrlFromSite(site);
        websiteBuilderPublishDirectusUrl.value = configuredUrl || "";
      }
      websiteBuilderRenderPublishCommandFields();
      renderWebsiteBuilderPublishDirectusAddon(site);
    }

    function websiteBuilderPublishingSetupRequiredFieldsReady(visibleSetup = websiteBuilderPublishingVisibleSetup(), site = websiteBuilderStateModel.selectedSite) {
      const useLocal = Boolean(visibleSetup.use_local_server || visibleSetup.publish_mode === "local_server");
      const baseReady = Boolean(
        String(visibleSetup.site_slug || "").trim()
        && String(visibleSetup.source_path || "").trim()
        && String(visibleSetup.remote_root || "").trim()
      );
      if (!baseReady) return false;
      if (!useLocal && !String(visibleSetup.remote_host || "").trim()) return false;
      if (!websiteBuilderPublishingRequiresDirectus(site)) return true;
      return websiteBuilderDirectusUrlLooksValid(visibleSetup.publish_directus_url);
    }

    function websiteBuilderPublishingSetupSignature(visibleSetup = websiteBuilderPublishingVisibleSetup()) {
      return JSON.stringify({
        publish_mode: visibleSetup.use_local_server || visibleSetup.publish_mode === "local_server" ? "local_server" : "scp",
        site_slug: String(visibleSetup.site_slug || "").trim(),
        source_path: String(visibleSetup.source_path || "").trim(),
        remote_host: String(visibleSetup.remote_host || "").trim(),
        remote_root: String(visibleSetup.remote_root || "").trim(),
        ssh_password: String(visibleSetup.ssh_password || ""),
        published_host_domain: String(visibleSetup.published_host_domain || "").trim(),
        publish_directus_url: String(visibleSetup.publish_directus_url || "").trim()
      });
    }

    function markWebsiteBuilderPublishingSetupAccepted(visibleSetup = websiteBuilderPublishingVisibleSetup()) {
      websiteBuilderStateModel.acceptedPublishingSetupSignature = websiteBuilderPublishingSetupSignature(visibleSetup);
    }

    function clearWebsiteBuilderPublishingSetupAccepted() {
      websiteBuilderStateModel.acceptedPublishingSetupSignature = "";
    }

    function websiteBuilderPublishingSetupAccepted(visibleSetup = websiteBuilderPublishingVisibleSetup()) {
      return Boolean(
        websiteBuilderStateModel.acceptedPublishingSetupSignature
        && websiteBuilderStateModel.acceptedPublishingSetupSignature === websiteBuilderPublishingSetupSignature(visibleSetup)
      );
    }

    function websiteBuilderSavedPublishTargetAccepted(remote) {
      return Boolean(remote?.accepted_at);
    }

    function websiteBuilderVisibleSetupFromSavedPublishTarget(remote, site = websiteBuilderStateModel.selectedSite) {
      if (!remote?.accepted_at) return null;
      return {
        publish_mode: remote.publish_mode || (remote.use_local_server ? "local_server" : "scp"),
        use_local_server: Boolean(remote.use_local_server || remote.publish_mode === "local_server"),
        site_slug: remote.site_slug || remote.project || site?.id || "",
        source_path: remote.source_path || (site?.id ? `runtime/websites/${site.id}` : ""),
        remote_host: remote.remote_host || "",
        ssh_password: remote.ssh_password || "",
        remote_root: remote.remote_root || websiteBuilderDefaultRemoteRoot,
        published_host_domain: remote.domain || "",
        publish_directus_url: remote.publish_directus_url || websiteBuilderPublishDirectusUrlFromSite(site)
      };
    }

    function syncWebsiteBuilderPublishingCompatibilityControls(site = websiteBuilderStateModel.selectedSite) {
      const visibleSetup = websiteBuilderPublishingVisibleSetup();
      const siteSlug = String(visibleSetup.site_slug || site?.id || "").trim();
      if (websiteBuilderRemoteProdTarget) {
        websiteBuilderRemoteProdTarget.replaceChildren();
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Command template";
        option.selected = true;
        websiteBuilderRemoteProdTarget.append(option);
      }
      if (websiteBuilderRemoteProdProject) {
        websiteBuilderRemoteProdProject.value = siteSlug;
      }
      if (websiteBuilderRemoteProdEnvironment && !websiteBuilderRemoteProdEnvironment.value) {
        websiteBuilderRemoteProdEnvironment.value = "production";
      }
      if (websiteBuilderRemoteProdDomain) {
        websiteBuilderRemoteProdDomain.value = String(visibleSetup.published_host_domain || "").trim();
      }
    }

    function websiteBuilderPublishingCommandPreview(visibleSetup = websiteBuilderPublishingVisibleSetup()) {
      const slug = String(visibleSetup.site_slug || "johnrraymond").trim() || "johnrraymond";
      const source = String(visibleSetup.source_path || "runtime/websites/hub-site").trim() || "runtime/websites/hub-site";
      const remoteRoot = String(visibleSetup.remote_root || websiteBuilderDefaultRemoteRoot).trim() || websiteBuilderDefaultRemoteRoot;
      if (visibleSetup.use_local_server || visibleSetup.publish_mode === "local_server") {
        return ["python", "deploy\\coolify\\push_site_local.py", slug, "--source", source, "--remote-root", remoteRoot];
      }
      const host = String(visibleSetup.remote_host || "root@publish.greatlibrary.io").trim() || "root@publish.greatlibrary.io";
      return ["python", "deploy\\coolify\\push_site_scp.py", slug, "--source", source, "--host", host, "--remote-root", remoteRoot];
    }

    function websiteBuilderPublishingSetupPayload(site = websiteBuilderStateModel.selectedSite) {
      const visibleSetup = websiteBuilderPublishingVisibleSetup();
      const hasVisibleSetup = Object.values(visibleSetup).some((value) => typeof value === "boolean" ? value : String(value || "").trim());
      const useLocal = Boolean(visibleSetup.use_local_server || visibleSetup.publish_mode === "local_server");
      const requiredFieldsReady = websiteBuilderPublishingSetupRequiredFieldsReady(visibleSetup, site);
      const siteSlug = String(visibleSetup.site_slug || site?.id || "").trim();
      const sourcePath = String(visibleSetup.source_path || (site?.id ? `runtime/websites/${site.id}` : "")).trim();
      const remoteRoot = String(visibleSetup.remote_root || websiteBuilderDefaultRemoteRoot).trim();
      const remoteHost = String(visibleSetup.remote_host || "").trim();
      return {
        prepared: hasVisibleSetup,
        ready_to_accept: requiredFieldsReady,
        already_accepted: requiredFieldsReady && websiteBuilderPublishingSetupAccepted(visibleSetup),
        visible_setup: visibleSetup,
        compatibility_payload: requiredFieldsReady
          ? {
              site_id: site?.id || "",
              lane: "remote_prod",
              publish_mode: useLocal ? "local_server" : "scp",
              use_local_server: useLocal,
              controller_id: "",
              project: siteSlug,
              site_slug: siteSlug,
              source_path: sourcePath,
              remote_host: useLocal ? "" : remoteHost,
              remote_root: remoteRoot,
              ssh_password: useLocal ? "" : String(visibleSetup.ssh_password || ""),
              environment: websiteBuilderRemoteProdEnvironment?.value || "production",
              domain: String(visibleSetup.published_host_domain || "").trim(),
              publish_directus_url: String(visibleSetup.publish_directus_url || "").trim()
            }
          : null,
        implementation_details: {
          route: "remote_prod",
          controller_id: useLocal ? "local_server" : "scp",
          server_target: useLocal ? "local server command" : remoteHost,
          deployment_destination: `${remoteRoot.replace(/\/+$/, "")}/${siteSlug}`,
          command: websiteBuilderPublishingCommandPreview(visibleSetup),
          compose: websiteBuilderRemoteCoolifyCompose(visibleSetup)
        }
      };
    }

    function renderWebsiteBuilderPublishingImplementation(site = websiteBuilderStateModel.selectedSite) {
      const payload = websiteBuilderPublishingSetupPayload(site);
      if (!payload.prepared) {
        if (websiteBuilderPublishingControllerId) websiteBuilderPublishingControllerId.textContent = "not selected";
        if (websiteBuilderPublishingServerTarget) websiteBuilderPublishingServerTarget.textContent = "not selected";
        if (websiteBuilderPublishingDestination) websiteBuilderPublishingDestination.textContent = "not selected";
        if (websiteBuilderPublishingImplementationPayload) {
          websiteBuilderPublishingImplementationPayload.textContent = "No publishing setup prepared.";
        }
        return;
      }
      if (websiteBuilderPublishingControllerId) {
        websiteBuilderPublishingControllerId.textContent = payload.implementation_details.controller_id || "not selected";
      }
      if (websiteBuilderPublishingServerTarget) {
        websiteBuilderPublishingServerTarget.textContent = payload.implementation_details.server_target || "not selected";
      }
      if (websiteBuilderPublishingDestination) {
        websiteBuilderPublishingDestination.textContent = payload.implementation_details.deployment_destination || "not selected";
      }
      if (websiteBuilderPublishingImplementationPayload) {
        const safePayload = JSON.parse(JSON.stringify(payload));
        if (safePayload.compatibility_payload?.ssh_password) {
          safePayload.compatibility_payload.ssh_password = "<set>";
        }
        if (safePayload.visible_setup?.ssh_password) {
          safePayload.visible_setup.ssh_password = "<set>";
        }
        websiteBuilderPublishingImplementationPayload.textContent = JSON.stringify(safePayload, null, 2);
      }
    }

    function renderWebsiteBuilderPublishDirectusAddon(site = websiteBuilderStateModel.selectedSite) {
      const required = websiteBuilderPublishingRequiresDirectus(site);
      if (websiteBuilderPublishBlogDirectusCard) {
        websiteBuilderPublishBlogDirectusCard.hidden = !required;
      }
      const localUrl = String(site?.backend?.cms?.service?.public_url || site?.backend?.cms?.local_connection?.public_url || "").trim();
      if (websiteBuilderPublishDirectusSummary) {
        websiteBuilderPublishDirectusSummary.textContent = required
          ? `Local Deploy and Local Server use the managed local Directus${localUrl ? ` at ${localUrl}` : ""}. Publish uses the URL below.`
          : "Blog is not selected for this site.";
      }
      if (!websiteBuilderPublishDirectusStatus) return;
      websiteBuilderPublishDirectusStatus.classList.remove("ready", "failed");
      if (!required) {
        websiteBuilderPublishDirectusStatus.textContent = "Configure Blog to enable this publishing addon.";
        return;
      }
      const url = String(websiteBuilderPublishDirectusUrl?.value || "").trim();
      if (websiteBuilderDirectusUrlLooksValid(url)) {
        websiteBuilderPublishDirectusStatus.textContent = "Published Blog content will be read from this Directus URL.";
        websiteBuilderPublishDirectusStatus.classList.add("ready");
      } else {
        websiteBuilderPublishDirectusStatus.textContent = "Required for Blog-enabled Publish. Enter the Directus URL that the published site should read.";
        websiteBuilderPublishDirectusStatus.classList.add("failed");
      }
    }

    function updateWebsiteBuilderPublishingSetupControls(statusText = "") {
      websiteBuilderRenderPublishCommandFields();
      const visibleSetup = websiteBuilderPublishingVisibleSetup();
      const hasVisibleSetup = Object.values(visibleSetup).some((value) => typeof value === "boolean" ? value : String(value || "").trim());
      if (hasVisibleSetup) {
        syncWebsiteBuilderPublishingCompatibilityControls();
      }
      const payload = websiteBuilderPublishingSetupPayload();
      const siteId = websiteBuilderStateModel.selectedSite?.id || websiteBuilderStateModel.selectedSiteId || "this site";
      if (websiteBuilderPublishingSetupStatus) {
        if (statusText) {
          websiteBuilderPublishingSetupStatus.textContent = statusText;
        } else if (payload.already_accepted) {
          websiteBuilderPublishingSetupStatus.textContent = `Publishing command setup accepted for ${siteId}. You can accept again to re-save the current command setup.`;
        } else if (!payload.prepared) {
          websiteBuilderPublishingSetupStatus.textContent = "Enter publish command values, then accept the setup before using Publish.";
        } else if (payload.ready_to_accept) {
          websiteBuilderPublishingSetupStatus.textContent = "Ready. Review the command, Coolify service example, and Directus URL if needed, then accept to save.";
        } else if (websiteBuilderPublishingRequiresDirectus(websiteBuilderStateModel.selectedSite) && !websiteBuilderDirectusUrlLooksValid(visibleSetup.publish_directus_url)) {
          websiteBuilderPublishingSetupStatus.textContent = "Enter the publish slug, source folder, remote command fields, and Published Site Directus URL before accepting.";
        } else {
          websiteBuilderPublishingSetupStatus.textContent = visibleSetup.use_local_server
            ? "Enter the publish slug, source folder, and remote root before accepting."
            : "Enter the publish slug, source folder, remote SSH host, and remote root before accepting.";
        }
      }
      renderWebsiteBuilderPublishDirectusAddon(websiteBuilderStateModel.selectedSite);
      renderWebsiteBuilderPublishingImplementation();
      if (websiteBuilderSaveRemoteProdTarget) {
        websiteBuilderSaveRemoteProdTarget.disabled = websiteBuilderStateModel.busy || !payload.ready_to_accept;
        websiteBuilderSaveRemoteProdTarget.title = websiteBuilderStateModel.busy
          ? "Saving publishing setup..."
          : payload.ready_to_accept
            ? (payload.already_accepted
              ? "Re-save this publishing command setup."
              : "Save this publishing command setup.")
            : websiteBuilderPublishingRequiresDirectus(websiteBuilderStateModel.selectedSite)
              ? "Enter the publish command fields and Published Site Directus URL before accepting."
              : "Enter the publish command fields before accepting.";
      }
      updateWebsiteBuilderPublishActionControls(websiteBuilderStateModel.selectedSite);
    }

    function syncWebsiteBuilderPublishingSetupForm(site = websiteBuilderStateModel.selectedSite) {
      const remote = websiteBuilderPublishTargets(site).remote_prod;
      const visibleSetup = websiteBuilderSavedPublishTargetAccepted(remote)
        ? websiteBuilderVisibleSetupFromSavedPublishTarget(remote, site)
        : {
            publish_mode: "scp",
            use_local_server: false,
            site_slug: site?.id || "",
            source_path: site?.repo_relative_path || (site?.id ? `runtime/websites/${site.id}` : ""),
            remote_host: "",
            ssh_password: "",
            remote_root: websiteBuilderDefaultRemoteRoot,
            published_host_domain: "",
            publish_directus_url: websiteBuilderPublishDirectusUrlFromSite(site)
          };
      setWebsiteBuilderPublishingVisibleSetup(visibleSetup, site);
      syncWebsiteBuilderPublishingCompatibilityControls(site);
      if (websiteBuilderSavedPublishTargetAccepted(remote)) {
        markWebsiteBuilderPublishingSetupAccepted(visibleSetup);
        updateWebsiteBuilderPublishingSetupControls(`Publishing command setup accepted for ${site?.id || "this site"}. You can accept again to re-save the current command setup.`);
      } else {
        clearWebsiteBuilderPublishingSetupAccepted();
        updateWebsiteBuilderPublishingSetupControls("Enter publish command values, then accept the setup before using Publish.");
      }
    }

    function syncWebsiteBuilderRemoteProdTargetForm(site) {
      const targets = websiteBuilderPublishTargets(site);
      const remote = targets.remote_prod;
      if (websiteBuilderRemoteProdProject) websiteBuilderRemoteProdProject.value = remote.site_slug || remote.project || "";
      if (websiteBuilderRemoteProdEnvironment) websiteBuilderRemoteProdEnvironment.value = remote.environment || "production";
      if (websiteBuilderRemoteProdDomain) websiteBuilderRemoteProdDomain.value = remote.domain || "";
      if (!websiteBuilderRemoteProdTarget) return;

      websiteBuilderRemoteProdTarget.replaceChildren();
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Command template";
      blank.selected = true;
      websiteBuilderRemoteProdTarget.append(blank);
    }

    function renderWebsiteBuilderPublishTargetControls(site = websiteBuilderStateModel.selectedSite) {
      const targets = websiteBuilderPublishTargets(site);
      if (websiteBuilderPublishLocalTarget) {
        const local = targets.local_prod;
        websiteBuilderPublishLocalTarget.textContent = `Target: ${websiteBuilderControllerLabel(local.controller_id)} · ${local.domain || "no local domain"}`;
      }
      if (websiteBuilderInspectorRemoteTarget) {
        const remote = targets.remote_prod;
        const mode = remote.use_local_server || remote.publish_mode === "local_server" ? "Local Server command" : "SCP command";
        const destination = remote.use_local_server || remote.publish_mode === "local_server"
          ? remote.remote_root
          : [remote.remote_host, remote.remote_root].filter(Boolean).join(":");
        const domain = remote.domain ? ` · ${remote.domain}` : "";
        websiteBuilderInspectorRemoteTarget.textContent = remote.accepted_at
          ? `${mode}${destination ? ` · ${destination}` : ""}${domain}`
          : "not accepted";
      }
      syncWebsiteBuilderRemoteProdTargetForm(site);
      syncWebsiteBuilderPublishingSetupForm(site);
      updateWebsiteBuilderVisitButtons(site);
    }

    async function loadWebsiteBuilderDeploymentControllers() {
      const payload = await websiteBuilderApi("/api/applications/deployment/controllers");
      websiteBuilderStateModel.deploymentControllers = payload.controllers || [];
      websiteBuilderStateModel.deploymentControllersLoaded = true;
      renderWebsiteBuilderCoolifyTargets();
      renderWebsiteBuilderPublishTargetControls();
      return payload;
    }

    async function saveWebsiteBuilderCoolifyRemote() {
      const controllerId = websiteBuilderCoolifyId?.value || "";
      const name = websiteBuilderCoolifyName?.value || controllerId;
      const baseUrl = websiteBuilderCoolifyUrl?.value || "";
      const tokenRef = websiteBuilderCoolifyTokenRef?.value || "";
      setWebsiteBuilderBusy(true, `Saving Coolify target ${controllerId || name}...`);
      try {
        const payload = await websiteBuilderApi("/api/applications/deployment/controller/save", {
          method: "POST",
          body: JSON.stringify({
            id: controllerId,
            kind: "coolify",
            name,
            base_url: baseUrl,
            token_ref: tokenRef,
            roles: ["remote-prod"],
            default_for: []
          })
        });
        websiteBuilderStateModel.deploymentControllers = payload.controllers || [];
        renderWebsiteBuilderCoolifyTargets();
        renderWebsiteBuilderPublishTargetControls();
        if (websiteBuilderCoolifyId) websiteBuilderCoolifyId.value = "";
        if (websiteBuilderCoolifyName) websiteBuilderCoolifyName.value = "";
        if (websiteBuilderCoolifyUrl) websiteBuilderCoolifyUrl.value = "";
        if (websiteBuilderCoolifyTokenRef) websiteBuilderCoolifyTokenRef.value = "";
        setWebsiteBuilderLog(`Saved Coolify target ${controllerId || name}.`);
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    async function saveWebsiteBuilderRemoteProdTarget() {
      const site = websiteBuilderStateModel.selectedSite;
      if (!site?.id) throw new Error("Select a website first.");
      updateWebsiteBuilderPublishingSetupControls();
      const setupPayload = websiteBuilderPublishingSetupPayload(site);
      if (!setupPayload.ready_to_accept || !setupPayload.compatibility_payload) {
        throw new Error(
          websiteBuilderPublishingRequiresDirectus(site)
            ? "Enter the publish command fields and Published Site Directus URL before accepting."
            : "Enter the publish command fields before accepting."
        );
      }
      setWebsiteBuilderBusy(true, `Saving publishing setup for ${site.id}...`);
      updateWebsiteBuilderPublishingSetupControls(`Saving publishing setup for ${site.id}...`);
      try {
        const payload = await websiteBuilderApi("/api/applications/websites/site/publish-target", {
          method: "POST",
          body: JSON.stringify(setupPayload.compatibility_payload)
        });
        websiteBuilderStateModel.selectedSite = payload.site;
        websiteBuilderStateModel.sites = websiteBuilderStateModel.sites.map((existing) => existing.id === payload.site.id ? payload.site : existing);
        renderWebsiteBuilderSites();
        renderWebsiteBuilderLinks(payload.site);
        updateWebsiteBuilderInspector();
        renderWebsiteBuilderPublishTargetControls(payload.site);
        updateWebsiteBuilderVisitButtons(payload.site);
        setWebsiteBuilderPublishingVisibleSetup(setupPayload.visible_setup);
        syncWebsiteBuilderPublishingCompatibilityControls(payload.site);
        markWebsiteBuilderPublishingSetupAccepted(setupPayload.visible_setup);
        updateWebsiteBuilderPublishingSetupControls(`Publishing command setup accepted for ${site.id}. You can accept again to re-save the current command setup.`);
        const logPayload = websiteBuilderPublishingSetupPayload(payload.site);
        if (logPayload.compatibility_payload?.ssh_password) logPayload.compatibility_payload.ssh_password = "<set>";
        if (logPayload.visible_setup?.ssh_password) logPayload.visible_setup.ssh_password = "<set>";
        setWebsiteBuilderLog(`Accepted publishing command setup for ${site.id}.`, logPayload);
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    function parseWebsiteBuilderBuilderState() {
      const text = websiteBuilderState?.value || "";
      if (!text.trim()) return {};
      try {
        const payload = JSON.parse(text);
        return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
      } catch (error) {
        return null;
      }
    }

    function writeWebsiteBuilderBuilderState(payload) {
      if (!websiteBuilderState) return;
      websiteBuilderState.value = `${JSON.stringify(payload, null, 2)}\n`;
      markWebsiteBuilderDirty();
    }

    function normalizeWebsiteBuilderBackendRuntime(value) {
      const runtime = String(value || "none").trim().toLowerCase();
      return websiteBuilderBackendRuntimeOrder.includes(runtime) ? runtime : "none";
    }

    function normalizeWebsiteBuilderBackendCapabilities(value) {
      const source = Array.isArray(value) ? value : [];
      const valid = Object.keys(websiteBuilderBackendProductLabels);
      return valid.filter((name) => source.includes(name));
    }

    function defaultWebsiteBuilderBackendConfig() {
      return {
        runtime: "none",
        base_path: "/api",
        entry: "backend/app.py",
        capabilities: [],
        routes: []
      };
    }

    function normalizeWebsiteBuilderBackendRoute(route) {
      if (!route || typeof route !== "object" || Array.isArray(route)) return null;
      const method = String(route.method || "GET").trim().toUpperCase() || "GET";
      const path = String(route.path || "").trim();
      if (!path.startsWith("/")) return null;
      return {
        method,
        path,
        name: String(route.name || `${method} ${path}`).trim(),
        source: String(route.source || "backend/app.py").trim() || "backend/app.py"
      };
    }

    function normalizeWebsiteBuilderBackendConfig(value) {
      const defaults = defaultWebsiteBuilderBackendConfig();
      const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const runtime = normalizeWebsiteBuilderBackendRuntime(source.runtime);
      const routes = runtime === "none"
        ? []
        : Array.isArray(source.routes)
          ? source.routes.map(normalizeWebsiteBuilderBackendRoute).filter(Boolean)
          : [];
      return {
        runtime,
        base_path: String(source.base_path || defaults.base_path).trim() || defaults.base_path,
        entry: String(source.entry || defaults.entry).trim() || defaults.entry,
        capabilities: runtime === "none" ? [] : normalizeWebsiteBuilderBackendCapabilities(source.capabilities),
        routes
      };
    }

    function currentWebsiteBuilderBackendConfig() {
      const builder = parseWebsiteBuilderBuilderState();
      if (builder === null) return null;
      return normalizeWebsiteBuilderBackendConfig(builder.backend);
    }

    function seedWebsiteBuilderBackendRoutes(config) {
      const routes = [...(config.routes || [])];
      const hasRoute = (method, path) => routes.some((route) => route.method === method && route.path === path);
      if (config.runtime === "fastapi" && !hasRoute("GET", "/api/health")) {
        routes.push({method: "GET", path: "/api/health", name: "Health check", source: config.entry});
      }
      if (config.capabilities.includes("forms") && !hasRoute("POST", "/api/contact")) {
        routes.push({method: "POST", path: "/api/contact", name: "Contact form", source: config.entry});
      }
      if (config.capabilities.includes("webhooks") && !hasRoute("POST", "/api/webhooks/inbound")) {
        routes.push({method: "POST", path: "/api/webhooks/inbound", name: "Inbound webhook", source: config.entry});
      }
      return routes;
    }

    function updateWebsiteBuilderBackendDraft(mutator) {
      const builder = parseWebsiteBuilderBuilderState();
      if (builder === null) {
        setWebsiteBuilderLog("Fix builder.json before editing the backend draft.");
        return null;
      }
      const nextBuilder = {...builder};
      const backend = normalizeWebsiteBuilderBackendConfig(nextBuilder.backend);
      mutator(backend);
      backend.runtime = normalizeWebsiteBuilderBackendRuntime(backend.runtime);
      backend.capabilities = normalizeWebsiteBuilderBackendCapabilities(backend.capabilities);
      backend.routes = seedWebsiteBuilderBackendRoutes(backend).map(normalizeWebsiteBuilderBackendRoute).filter(Boolean);
      nextBuilder.backend = backend;
      writeWebsiteBuilderBuilderState(nextBuilder);
      renderWebsiteBuilderBackendView();
      return backend;
    }

    function setWebsiteBuilderBackendRuntime(runtimeName) {
      const runtime = normalizeWebsiteBuilderBackendRuntime(runtimeName);
      updateWebsiteBuilderBackendDraft((backend) => {
        backend.runtime = runtime;
        if (runtime !== "none" && !backend.capabilities.includes("api")) {
          backend.capabilities = ["api", ...backend.capabilities];
        }
        if (runtime === "none") {
          backend.capabilities = [];
          backend.routes = [];
        }
      });
    }

    function toggleWebsiteBuilderBackendProduct(productName) {
      const product = String(productName || "").trim();
      if (!Object.prototype.hasOwnProperty.call(websiteBuilderBackendProductLabels, product)) return;
      updateWebsiteBuilderBackendDraft((backend) => {
        if (backend.runtime === "none") {
          backend.runtime = "fastapi";
        }
        const selected = new Set(backend.capabilities);
        if (selected.has(product)) {
          selected.delete(product);
        } else {
          selected.add(product);
        }
        backend.capabilities = Object.keys(websiteBuilderBackendProductLabels).filter((name) => selected.has(name));
      });
    }

    function buildWebsiteBuilderBackendSourcePreview(config) {
      if (!config || config.runtime === "none") {
        return "# Select FastAPI to preview a backend source shape.";
      }
      if (config.runtime !== "fastapi") {
        return `# ${websiteBuilderBackendRuntimeLabels[config.runtime]} is reserved for a later backend runtime.\n# FastAPI is the first runtime this builder will execute.`;
      }
      const routes = seedWebsiteBuilderBackendRoutes(config);
      const lines = [
        "from fastapi import FastAPI",
        "from pydantic import BaseModel",
        "",
        "",
        "app = FastAPI(title=\"Main Computer Website Backend\")",
        "",
        "",
        "class ContactPayload(BaseModel):",
        "    name: str = \"\"",
        "    email: str = \"\"",
        "    message: str = \"\"",
        ""
      ];
      routes.forEach((route) => {
        const decorator = route.method.toLowerCase();
        const functionName = route.path
          .replace(/^\/api\/?/, "")
          .replace(/[^a-zA-Z0-9]+/g, "_")
          .replace(/^_+|_+$/g, "") || "health";
        lines.push("");
        lines.push(`@app.${decorator}(\"${route.path}\")`);
        if (route.path === "/api/contact") {
          lines.push(`def ${functionName}(payload: ContactPayload):`);
          lines.push("    return {\"ok\": True, \"received\": payload.model_dump()}");
        } else {
          lines.push(`def ${functionName}():`);
          lines.push(`    return {\"ok\": True, \"route\": \"${route.path}\"}`);
        }
      });
      return lines.join("\n");
    }

    function renderWebsiteBuilderBackendView() {
      const config = currentWebsiteBuilderBackendConfig() || defaultWebsiteBuilderBackendConfig();
      const site = websiteBuilderStateModel.selectedSite;
      const runtimeLabel = websiteBuilderBackendRuntimeLabels[config.runtime] || "Static only";
      const devUrl = websiteBuilderLaneUrl(site, "dev");
      if (websiteBuilderBackendRuntimeLabel) {
        websiteBuilderBackendRuntimeLabel.textContent = runtimeLabel;
      }
      if (websiteBuilderBackendSummary) {
        const selectedProducts = config.capabilities.map((name) => websiteBuilderBackendProductLabels[name]).filter(Boolean);
        websiteBuilderBackendSummary.textContent = config.runtime === "none"
          ? "Static-only site. Choose FastAPI to add API routes, forms, database, auth, jobs, and more."
          : `${runtimeLabel} draft using ${config.entry}; products: ${selectedProducts.length ? selectedProducts.join(", ") : "API Routes"}.`;
      }
      if (websiteBuilderBackendDev) {
        websiteBuilderBackendDev.textContent = devUrl
          ? `Deploy lane: ${devUrl} · Save / Preview syncs this draft.`
          : "No Deploy lane configured yet.";
      }
      websiteBuilderBackendRuntimeButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.websiteBuilderBackendRuntime === config.runtime);
      });
      websiteBuilderBackendProductButtons.forEach((button) => {
        const product = button.dataset.websiteBuilderBackendProduct;
        const blogInstalled = product === "blog" && websiteBuilderBlogLocalSiteState(websiteBuilderStateModel.selectedSiteId).layers.blog?.status === "installed";
        button.classList.toggle("active", product === "blog" ? blogInstalled : config.capabilities.includes(product));
      });
      if (websiteBuilderBackendRoutes) {
        websiteBuilderBackendRoutes.replaceChildren();
        const routes = seedWebsiteBuilderBackendRoutes(config);
        if (!routes.length || config.runtime === "none") {
          const empty = document.createElement("p");
          empty.textContent = "Choose FastAPI and API Routes to seed a backend route plan.";
          websiteBuilderBackendRoutes.append(empty);
        } else {
          routes.forEach((route) => {
            const card = document.createElement("article");
            card.className = "website-builder-backend-route";
            const method = document.createElement("span");
            method.textContent = route.method;
            const title = document.createElement("strong");
            title.textContent = route.path;
            const source = document.createElement("small");
            source.textContent = `${route.name} · ${route.source}`;
            card.append(method, title, source);
            websiteBuilderBackendRoutes.append(card);
          });
        }
      }
      if (websiteBuilderBackendSourcePreview) {
        websiteBuilderBackendSourcePreview.textContent = buildWebsiteBuilderBackendSourcePreview(config);
      }
    }


    function websiteBuilderBlogAssumptionsEndpoint(siteId) {
      return `/api/sites/${encodeURIComponent(siteId)}/blog/install-assumptions`;
    }

    function websiteBuilderBlogIntentEndpoint(siteId) {
      return `/api/sites/${encodeURIComponent(siteId)}/blog/intent`;
    }

    function websiteBuilderBlogLayerInstallEndpoint(siteId, layerId) {
      return `/api/sites/${encodeURIComponent(siteId)}/blog/layers/${encodeURIComponent(layerId)}/install`;
    }

    function websiteBuilderBlogLocalSiteState(siteId = websiteBuilderStateModel.selectedSiteId) {
      const key = String(siteId || "draft-site");
      const sites = websiteBuilderStateModel.blogRuntimeWizard.localSites;
      if (!sites[key]) {
        sites[key] = {
          layers: {},
          sqliteInstalled: false,
          sqliteInstallCount: 0
        };
      }
      return sites[key];
    }

    function websiteBuilderBlogLayerStatus(siteState, layerId) {
      if (layerId === "database" && siteState.sqliteInstalled) return "already_installed";
      return siteState.layers[layerId]?.status || "planned";
    }

    function websiteBuilderBlogBuildFixtureContract(siteId = websiteBuilderStateModel.selectedSiteId) {
      const site = websiteBuilderStateModel.selectedSite || {};
      const siteState = websiteBuilderBlogLocalSiteState(siteId);
      const layers = [
        {
          id: "blog",
          label: "Blog",
          selected_option: "blog",
          option_label: "Blog",
          status: websiteBuilderBlogLayerStatus(siteState, "blog"),
          locked_reason: "Blog requires Directus and SQLite.",
          description: websiteBuilderBlogLayerDescriptions.blog,
          options: [
            {id: "blog", label: "Blog", available: true, recommended: true, default: true}
          ]
        },
        {
          id: "cms",
          label: "CMS Provider",
          selected_option: "directus",
          option_label: "Directus",
          status: websiteBuilderBlogLayerStatus(siteState, "cms"),
          locked_reason: "Directus is currently the supported CMS provider.",
          description: websiteBuilderBlogLayerDescriptions.cms,
          options: [
            {id: "directus", label: "Directus", available: true, recommended: true, default: true}
          ]
        },
        {
          id: "database",
          label: "Runtime Dependencies",
          selected_option: "sqlite",
          option_label: "SQLite",
          status: websiteBuilderBlogLayerStatus(siteState, "database"),
          locked_reason: "SQLite is required by the Blog runtime.",
          description: websiteBuilderBlogLayerDescriptions.database,
          existing_resource_detected: Boolean(siteState.sqliteInstalled),
          overwrite_default: false,
          overwrite_allowed: false,
          requires_user_confirmation: false,
          recommended_action: siteState.sqliteInstalled ? "reuse_when_safe" : "configure",
          options: [
            {id: "sqlite", label: "SQLite", available: true, recommended: true, default: true}
          ]
        }
      ];
      const configured = ["installed", "already_installed", "reused"].includes(websiteBuilderBlogLayerStatus(siteState, "database"))
        && ["configured", "ready"].includes(websiteBuilderBlogLayerStatus(siteState, "cms"))
        && ["pending_deploy", "ready"].includes(websiteBuilderBlogLayerStatus(siteState, "blog"));
      return {
        ok: true,
        source: "frontend_fixture_until_backend_exists",
        site_id: siteId || "",
        site_name: site.name || site.id || siteId || "selected site",
        feature: "blog",
        golden_path: true,
        provider_recommendation: "directus",
        database_recommendation: "sqlite",
        install_order: [...websiteBuilderBlogLayerInstallOrder],
        next_allowed_action: configured ? "pending_deploy_verification" : "configure_blog_runtime",
        mutation_allowed: false,
        commit_allowed: false,
        layers,
        directus: {
          provider: "directus",
          configured: ["configured", "ready"].includes(websiteBuilderBlogLayerStatus(siteState, "cms")),
          ready: websiteBuilderBlogLayerStatus(siteState, "cms") === "ready",
          runtime: "deployed",
          status: websiteBuilderBlogLayerStatus(siteState, "cms")
        },
        runtime: {
          content_runtime: "deployed",
          provider: "directus",
          collection: "posts",
          cms_url_ref: "backend.cms.service.internal_url",
          cms_public_url_ref: "backend.cms.service.public_url"
        },
        blog: {
          ready: websiteBuilderBlogLayerStatus(siteState, "blog") === "ready",
          install_status: websiteBuilderBlogLayerStatus(siteState, "blog"),
          routes: {index: "/blog", post: "/blog/:slug"},
          content: {provider: "directus", collection: "posts", draft_safe: true},
          source_files: [
            "src/content/runtime-config.js",
            "src/content/directus-client.js",
            "src/blog/list-posts.js",
            "src/blog/get-post-by-slug.js"
          ]
        },
        actions: {
          open_blog: {enabled: configured, path: "/blog", url: "/blog"},
          open_directus: {enabled: configured, url: "", credential_note: "Use the configured Directus admin credentials for this local runtime."},
          edit_blog_code: {enabled: configured, files: ["src/content/runtime-config.js", "src/content/directus-client.js"]},
          view_runtime_config: {enabled: configured, manifest_path: "site.json#runtime_config.content"}
        },
        assumptions: [
          {
            id: "blog_feature_selected",
            status: "pass",
            severity: "required",
            frontend_title: "Blog selected",
            frontend_message: "The Blog feature uses Directus and SQLite runtime dependencies."
          },
          {
            id: "directus_golden_path",
            status: ["configured", "ready"].includes(websiteBuilderBlogLayerStatus(siteState, "cms")) ? "configured" : "planned",
            severity: "required",
            frontend_title: "Directus CMS",
            frontend_message: "Directus is required for Blog. Configure Blog Runtime prepares the local service, schema, uploads, and public read permissions."
          },
          {
            id: "sqlite_nested_dependency",
            status: siteState.sqliteInstalled ? "pass" : "planned",
            severity: "blocker",
            frontend_title: "SQLite runtime dependency",
            frontend_message: siteState.sqliteInstalled
              ? "SQLite is available for the Blog runtime. Deploy will verify it before readiness."
              : "SQLite is required by the Blog runtime. Configure Blog Runtime prepares or verifies it locally."
          },
          {
            id: "runtime_dependency_reuse",
            status: "pass",
            severity: "required",
            frontend_title: "Runtime dependency reuse",
            frontend_message: "Configure Blog Runtime prepares local runtime dependencies. Existing DB management tools will come later."
          },
          {
            id: "commit_gate",
            status: configured ? "configured" : "planned",
            severity: "blocker",
            frontend_title: "Deploy verification gate",
            frontend_message: "Commit remains disabled until deploy verifies Directus and SQLite runtime readiness."
          }
        ]
      };
    }

    function websiteBuilderBlogNormalizeContract(value) {
      const fixture = websiteBuilderBlogBuildFixtureContract();
      const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const layers = Array.isArray(source.layers) && source.layers.length ? source.layers : fixture.layers;
      const assumptions = Array.isArray(source.assumptions) && source.assumptions.length ? source.assumptions : fixture.assumptions;
      return {
        ...fixture,
        ...source,
        layers,
        assumptions,
        install_order: Array.isArray(source.install_order) && source.install_order.length ? source.install_order : fixture.install_order,
        mutation_allowed: Boolean(source.mutation_allowed),
        commit_allowed: Boolean(source.commit_allowed)
      };
    }

    async function websiteBuilderBlogGetAssumptions(siteId) {
      try {
        return websiteBuilderBlogNormalizeContract(await websiteBuilderApi(websiteBuilderBlogAssumptionsEndpoint(siteId)));
      } catch (error) {
        websiteBuilderBlogAddActivity(`Backend assumption hook not available yet; using frontend fixture. (${error.message})`);
        return websiteBuilderBlogBuildFixtureContract(siteId);
      }
    }

    async function websiteBuilderBlogPersistIntentApi(siteId, payload = {}) {
      try {
        const response = await fetch(websiteBuilderBlogIntentEndpoint(siteId), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(result.error || `Request failed: ${response.status}`);
        }
        return result;
      } catch (error) {
        return websiteBuilderBlogIntentFixture(siteId, payload, error);
      }
    }

    async function websiteBuilderBlogInstallLayerApi(siteId, layerId, payload = {}) {
      try {
        const response = await fetch(websiteBuilderBlogLayerInstallEndpoint(siteId, layerId), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(result.error || `Request failed: ${response.status}`);
        }
        return result;
      } catch (error) {
        return websiteBuilderBlogInstallLayerFixture(siteId, layerId, payload, error);
      }
    }

    function websiteBuilderBlogIntentFixture(siteId, payload = {}, backendError = null) {
      const siteState = websiteBuilderBlogLocalSiteState(siteId);
      siteState.blogIntent = {
        selected: true,
        enabled: false,
        cms: "directus",
        database: "sqlite",
        runtime_lane: payload.runtime_lane || "local",
        install_status: "pending_deploy",
        install_order: [...websiteBuilderBlogLayerInstallOrder]
      };
      const contract = websiteBuilderBlogBuildFixtureContract(siteId);
      return {
        ok: true,
        source: "frontend_fixture_until_backend_exists",
        backend_error: backendError?.message || "",
        feature: "blog",
        intent: {...siteState.blogIntent},
        contract
      };
    }

    function websiteBuilderBlogInstallLayerFixture(siteId, layerId, payload = {}, backendError = null) {
      const siteState = websiteBuilderBlogLocalSiteState(siteId);
      if (layerId === "database") {
        if (siteState.sqliteInstalled && !payload.keep_existing && !payload.overwrite_sqlite) {
          return {
            ok: false,
            code: "sqlite_reinstall_guard",
            layer_id: "database",
            existing_resource_detected: true,
            overwrite_required: false,
            overwrite_default: false,
            overwrite_allowed: false,
            recommended_action: "keep_existing",
            message: "Runtime dependencies already exist. Configure Blog Runtime can verify them locally."
          };
        }
        siteState.sqliteInstalled = true;
        siteState.sqliteInstallCount += payload.overwrite_sqlite ? 1 : 0;
      }
      const statusByLayer = {
        database: payload.keep_existing ? "reused" : "configured",
        cms: "configured",
        blog: "pending_deploy"
      };
      siteState.layers[layerId] = {
        status: statusByLayer[layerId] || "configured",
        updated_at: new Date().toISOString()
      };
      return {
        ok: true,
        source: "frontend_fixture_until_backend_exists",
        backend_error: backendError?.message || "",
        layer_id: layerId,
        action: layerId === "database"
          ? (payload.keep_existing ? "verified" : payload.overwrite_sqlite ? "configured" : "prepared")
          : statusByLayer[layerId],
        existing_resource_detected: layerId === "database" && Boolean(payload.keep_existing || payload.overwrite_sqlite),
        overwrite_sqlite: Boolean(payload.overwrite_sqlite),
        keep_existing: Boolean(payload.keep_existing)
      };
    }

    function websiteBuilderBlogAddActivity(message) {
      const state = websiteBuilderStateModel.blogRuntimeWizard;
      state.activity = [
        {message: String(message || ""), time: new Date().toLocaleTimeString()},
        ...state.activity
      ].slice(0, 8);
      renderWebsiteBuilderBlogActivity();
    }

    function websiteBuilderBlogStatusLabel(status) {
      const labels = {
        pass: "Pass",
        fail: "Fail",
        blocked: "Blocked",
        unknown: "Unknown",
        planned: "Planned",
        not_applicable: "Not applicable",
        selected: "Selected",
        auto_selected: "Auto-selected",
        already_installed: "Already installed",
        installing: "Installing",
        installed: "Installed",
        reused: "Reused",
        configured: "Configured",
        pending_deploy: "Pending deploy",
        deploying: "Deploying",
        ready: "Ready",
        failed: "Failed"
      };
      return labels[status] || String(status || "planned");
    }

    function setWebsiteBuilderMcWidget(element, id, kind, widgetClass, label) {
      if (!element || !id) return element;
      element.dataset.mcWidgetId = id;
      element.dataset.mcWidgetKind = kind || "status";
      element.dataset.mcWidgetClass = widgetClass || kind || "status";
      if (label) {
        element.dataset.mcWidgetLabel = label;
      }
      return element;
    }

    function websiteBuilderMcToken(value, fallback = "item") {
      return String(value || fallback)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || fallback;
    }

    function renderWebsiteBuilderBlogActivity() {
      if (!websiteBuilderBlogActivity) return;
      websiteBuilderBlogActivity.replaceChildren();
      const activity = websiteBuilderStateModel.blogRuntimeWizard.activity || [];
      if (!activity.length) {
        const empty = document.createElement("p");
        setWebsiteBuilderMcWidget(
          empty,
          "website-builder.blog-runtime-activity-empty",
          "status",
          "status",
          "Blog Runtime Empty Activity"
        );
        empty.textContent = "No runtime configuration activity yet.";
        websiteBuilderBlogActivity.append(empty);
        return;
      }
      activity.forEach((entry, index) => {
        const row = document.createElement("div");
        row.className = "website-builder-blog-activity-row";
        setWebsiteBuilderMcWidget(
          row,
          `website-builder.blog-runtime-activity-row-${index + 1}`,
          "status",
          "output",
          "Blog Runtime Activity Row"
        );
        const time = document.createElement("span");
        setWebsiteBuilderMcWidget(
          time,
          `website-builder.blog-runtime-activity-row-${index + 1}-time`,
          "status",
          "status",
          "Blog Runtime Activity Time"
        );
        time.textContent = entry.time || "";
        const message = document.createElement("strong");
        setWebsiteBuilderMcWidget(
          message,
          `website-builder.blog-runtime-activity-row-${index + 1}-message`,
          "status",
          "status",
          "Blog Runtime Activity Message"
        );
        message.textContent = entry.message || "";
        row.append(time, message);
        websiteBuilderBlogActivity.append(row);
      });
    }

    function renderWebsiteBuilderBlogInstallWizard() {
      const state = websiteBuilderStateModel.blogRuntimeWizard;
      const contract = state.contract || websiteBuilderBlogBuildFixtureContract();
      if (websiteBuilderBlogWizard) {
        websiteBuilderBlogWizard.hidden = !state.open;
      }
      if (websiteBuilderBlogWizardSummary) {
        websiteBuilderBlogWizardSummary.textContent = "Blog requires Directus and SQLite. Configure Blog Runtime prepares the local Directus and SQLite dependencies.";
      }
      if (websiteBuilderBlogNextAction) {
        websiteBuilderBlogNextAction.textContent = state.loading
          ? "Configuring Blog Runtime..."
          : ["configure_blog_runtime", "install_recommended_stack"].includes(contract.next_allowed_action)
            ? "Configure Blog Runtime"
            : contract.next_allowed_action === "pending_deploy_verification"
              ? "Pending deploy verification"
              : "Configure Blog Runtime prepares local runtime dependencies";
      }
      renderWebsiteBuilderBlogLayers(contract);
      renderWebsiteBuilderBlogInstallOrder(contract);
      renderWebsiteBuilderBlogAssumptions(contract);
      renderWebsiteBuilderBlogRuntime(contract);
      renderWebsiteBuilderBlogActivity();
      const busy = Boolean(state.loading);
      if (websiteBuilderBlogInstallConfirm) {
        websiteBuilderBlogInstallConfirm.disabled = busy;
        websiteBuilderBlogInstallConfirm.textContent = busy ? "Configuring Blog Runtime..." : "Configure Blog Runtime";
      }
    }

    function renderWebsiteBuilderBlogLayers(contract) {
      if (!websiteBuilderBlogLayerStack) return;
      websiteBuilderBlogLayerStack.replaceChildren();
      (contract.layers || []).forEach((layer, index) => {
        const layerToken = websiteBuilderMcToken(layer.id, `layer-${index + 1}`);
        const card = document.createElement("article");
        card.className = `website-builder-blog-layer website-builder-blog-layer-${layer.status || "planned"}`;
        setWebsiteBuilderMcWidget(
          card,
          `website-builder.blog-runtime.layer.${layerToken}`,
          "panel",
          "panel",
          `${layer.label || websiteBuilderBlogLayerLabels[layer.id] || layer.id} Runtime Layer`
        );
        const depth = document.createElement("span");
        depth.className = "website-builder-blog-layer-depth";
        setWebsiteBuilderMcWidget(
          depth,
          `website-builder.blog-runtime.layer.${layerToken}.depth`,
          "status",
          "status",
          "Blog Runtime Layer Depth"
        );
        depth.textContent = index === 0 ? "Layer 1" : index === 1 ? "Layer 2" : "Layer 3";
        const title = document.createElement("strong");
        setWebsiteBuilderMcWidget(
          title,
          `website-builder.blog-runtime.layer.${layerToken}.title`,
          "status",
          "heading",
          "Blog Runtime Layer Title"
        );
        title.textContent = layer.label || websiteBuilderBlogLayerLabels[layer.id] || layer.id;
        const selected = document.createElement("span");
        setWebsiteBuilderMcWidget(
          selected,
          `website-builder.blog-runtime.layer.${layerToken}.selection`,
          "status",
          "status",
          "Blog Runtime Layer Selection"
        );
        selected.textContent = layer.option_label || websiteBuilderBlogLayerOptions[layer.id] || layer.selected_option || "";
        const meta = document.createElement("small");
        setWebsiteBuilderMcWidget(
          meta,
          `website-builder.blog-runtime.layer.${layerToken}.status`,
          "status",
          "status",
          "Blog Runtime Layer Status"
        );
        meta.textContent = `${websiteBuilderBlogStatusLabel(layer.status)} · ${layer.locked_reason || layer.description || "Runtime dependency"}`;
        card.append(depth, title, selected, meta);
        websiteBuilderBlogLayerStack.append(card);
      });
    }

    function renderWebsiteBuilderBlogInstallOrder(contract) {
      if (!websiteBuilderBlogInstallOrder) return;
      websiteBuilderBlogInstallOrder.replaceChildren();
      (contract.install_order || websiteBuilderBlogLayerInstallOrder).forEach((layerId) => {
        const layer = (contract.layers || []).find((entry) => entry.id === layerId) || {};
        const layerToken = websiteBuilderMcToken(layerId);
        const item = document.createElement("li");
        setWebsiteBuilderMcWidget(
          item,
          `website-builder.blog-runtime.dependency-order.${layerToken}`,
          "status",
          "status",
          `${websiteBuilderBlogLayerLabels[layerId] || layer.label || layerId} Dependency Order`
        );
        const title = document.createElement("strong");
        setWebsiteBuilderMcWidget(
          title,
          `website-builder.blog-runtime.dependency-order.${layerToken}.title`,
          "status",
          "heading",
          "Blog Runtime Dependency Title"
        );
        title.textContent = websiteBuilderBlogLayerLabels[layerId] || layer.label || layerId;
        const detail = document.createElement("span");
        setWebsiteBuilderMcWidget(
          detail,
          `website-builder.blog-runtime.dependency-order.${layerToken}.detail`,
          "status",
          "status",
          "Blog Runtime Dependency Detail"
        );
        detail.textContent = `${websiteBuilderBlogLayerOptions[layerId] || layer.selected_option || ""} · ${websiteBuilderBlogStatusLabel(layer.status)}`;
        item.append(title, detail);
        websiteBuilderBlogInstallOrder.append(item);
      });
    }

    function renderWebsiteBuilderBlogAssumptions(contract) {
      if (!websiteBuilderBlogAssumptions) return;
      websiteBuilderBlogAssumptions.replaceChildren();
      (contract.assumptions || []).forEach((assumption) => {
        const assumptionToken = websiteBuilderMcToken(assumption.id, "assumption");
        const card = document.createElement("article");
        card.className = `website-builder-blog-assumption website-builder-blog-assumption-${assumption.status || "unknown"}`;
        setWebsiteBuilderMcWidget(
          card,
          `website-builder.blog-runtime.assumption.${assumptionToken}`,
          "panel",
          "panel",
          `${assumption.frontend_title || assumption.id || "Blog Runtime Assumption"}`
        );
        const status = document.createElement("span");
        setWebsiteBuilderMcWidget(
          status,
          `website-builder.blog-runtime.assumption.${assumptionToken}.status`,
          "status",
          "status",
          "Blog Runtime Assumption Status"
        );
        status.textContent = websiteBuilderBlogStatusLabel(assumption.status);
        const body = document.createElement("div");
        setWebsiteBuilderMcWidget(
          body,
          `website-builder.blog-runtime.assumption.${assumptionToken}.body`,
          "panel",
          "panel",
          "Blog Runtime Assumption Body"
        );
        const title = document.createElement("strong");
        setWebsiteBuilderMcWidget(
          title,
          `website-builder.blog-runtime.assumption.${assumptionToken}.title`,
          "status",
          "heading",
          "Blog Runtime Assumption Title"
        );
        title.textContent = assumption.frontend_title || assumption.id || "Assumption";
        const message = document.createElement("small");
        setWebsiteBuilderMcWidget(
          message,
          `website-builder.blog-runtime.assumption.${assumptionToken}.message`,
          "status",
          "status",
          "Blog Runtime Assumption Message"
        );
        message.textContent = assumption.frontend_message || "";
        body.append(title, message);
        card.append(status, body);
        websiteBuilderBlogAssumptions.append(card);
      });
    }

    function renderWebsiteBuilderBlogRuntime(contract) {
      const runtime = contract.runtime || {};
      const blog = contract.blog || {};
      const actions = contract.actions || {};
      const status = blog.install_status || "planned";
      if (websiteBuilderBlogRuntimeSummary) {
        websiteBuilderBlogRuntimeSummary.textContent = status === "ready"
          ? "Blog runtime is ready. Directus and SQLite have been verified for published Blog content."
          : "Blog requires Directus and SQLite. Configure Blog Runtime prepares the local Directus and SQLite dependencies.";
      }
      if (websiteBuilderBlogRuntimeDetails) {
        websiteBuilderBlogRuntimeDetails.replaceChildren();
        const rows = [
          ["Status", websiteBuilderBlogStatusLabel(status)],
          ["Route", blog.routes?.index || "/blog"],
          ["CMS", runtime.provider || blog.content?.provider || "directus"],
          ["Collection", runtime.collection || blog.content?.collection || "posts"],
          ["Runtime", runtime.content_runtime || "deployed"]
        ];
        rows.forEach(([label, value]) => {
          const rowToken = websiteBuilderMcToken(label);
          const wrapper = document.createElement("div");
          setWebsiteBuilderMcWidget(
            wrapper,
            `website-builder.blog-runtime.fact.${rowToken}`,
            "status",
            "status",
            `Blog Runtime ${label} Fact`
          );
          const dt = document.createElement("dt");
          const dd = document.createElement("dd");
          setWebsiteBuilderMcWidget(
            dt,
            `website-builder.blog-runtime.fact.${rowToken}.label`,
            "status",
            "label",
            `Blog Runtime ${label} Label`
          );
          setWebsiteBuilderMcWidget(
            dd,
            `website-builder.blog-runtime.fact.${rowToken}.value`,
            "status",
            "value",
            `Blog Runtime ${label} Value`
          );
          dt.textContent = label;
          dd.textContent = value;
          wrapper.append(dt, dd);
          websiteBuilderBlogRuntimeDetails.append(wrapper);
        });
      }
      if (websiteBuilderBlogRuntimeActions) {
        websiteBuilderBlogRuntimeActions.replaceChildren();
      }
    }

    async function openWebsiteBuilderBlogInstallWizard(options = {}) {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) {
        setWebsiteBuilderLog("Select a website before configuring Blog Runtime.");
        return;
      }
      const state = websiteBuilderStateModel.blogRuntimeWizard;
      state.open = true;
      state.loading = true;
      state.activity = [];
      if (options.directusConnection) {
        state.pendingDirectusConnection = options.directusConnection;
      }
      renderWebsiteBuilderBlogInstallWizard();
      const contract = await websiteBuilderBlogGetAssumptions(siteId);
      state.contract = websiteBuilderBlogNormalizeContract(contract);
      state.loading = false;
      if (state.pendingDirectusConnection) {
        websiteBuilderBlogAddActivity("Directus storage choice captured for Blog configuration.");
      }
      websiteBuilderBlogAddActivity("Loaded Blog runtime requirements.");
      renderWebsiteBuilderBlogInstallWizard();
    }

    function closeWebsiteBuilderBlogInstallWizard() {
      websiteBuilderStateModel.blogRuntimeWizard.open = false;
      websiteBuilderStateModel.blogRuntimeWizard.loading = false;
      websiteBuilderStateModel.blogRuntimeWizard.pendingDirectusConnection = null;
      renderWebsiteBuilderBlogInstallWizard();
    }


    async function websiteBuilderDirectusConnectionForBlogConfigure(site, options = {}) {
      if (options.directusConnection || options.skipDirectusConnectionModal) {
        return options.directusConnection || null;
      }
      const connection = await openWebsiteBuilderDirectusConnectionModal(site, {
        context: "blog_configure",
        requireDirectus: true
      });
      if (!connection) {
        setWebsiteBuilderLog(`Blog runtime configuration canceled for ${site?.id || "this site"} before Directus was selected.`);
      }
      return connection;
    }

    async function openWebsiteBuilderBlogConfigureFlow(options = {}) {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) {
        setWebsiteBuilderLog("Select a website before configuring Blog.");
        return null;
      }
      await openWebsiteBuilderBlogInstallWizard(options);
      return true;
    }

    function websiteBuilderBlogSetupActivityLines(layerResult) {
      if (!layerResult || typeof layerResult !== "object") return [];
      const lines = [];
      const setup = layerResult.directus_setup;
      if (setup && typeof setup === "object") {
        const services = Array.isArray(setup.services) ? setup.services.filter(Boolean) : [];
        if (services.length) {
          lines.push(`Directus setup targeted service: ${services.join(", ")}.`);
        }
        const action = setup.directus_runtime_action;
        if (action && typeof action === "object") {
          const removedContainers = Array.isArray(action.removed_containers) ? action.removed_containers.length : 0;
          const removedVolumes = Array.isArray(action.removed_volumes) ? action.removed_volumes.length : 0;
          const actionState = action.ok === false ? "failed" : "completed";
          lines.push(`Directus runtime action ${actionState}; removed ${removedContainers} container(s) and ${removedVolumes} volume(s).`);
        }
        if (typeof setup.returncode !== "undefined") {
          lines.push(`Directus compose returned ${setup.returncode}.`);
        }
        if (Array.isArray(setup.cms_verify)) {
          setup.cms_verify.forEach((item) => {
            if (!item || typeof item !== "object") return;
            const service = item.service || "Directus";
            const status = item.ok ? "passed" : "failed";
            const probe = item.probe_url || item.public_url || "";
            const detail = item.error || item.status || item.attempts || "";
            lines.push(`${service} readiness ${status}${probe ? ` at ${probe}` : ""}${detail ? ` (${detail})` : ""}.`);
          });
        }
        if (Array.isArray(setup.directus_bootstrap)) {
          setup.directus_bootstrap.forEach((item) => {
            if (!item || typeof item !== "object") return;
            const service = item.service || "Directus";
            const status = item.ok ? "completed" : "failed";
            const steps = Array.isArray(item.steps) ? item.steps.length : 0;
            const detail = item.error || item.message || "";
            lines.push(`${service} blog bootstrap ${status}${steps ? ` with ${steps} step(s)` : ""}${detail ? `: ${detail}` : "."}`);
          });
        }
        if (setup.error) {
          lines.push(`Directus setup error: ${setup.error}`);
        }
      }
      if (layerResult.hydration && typeof layerResult.hydration === "object") {
        const status = layerResult.hydration.ok === false ? "failed" : "recorded";
        lines.push(`Blog runtime hydration ${status}.`);
      }
      return lines;
    }


    async function runWebsiteBuilderBlogInstallStack(options = {}) {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) {
        setWebsiteBuilderLog("Select a website before configuring Blog.");
        return;
      }
      const state = websiteBuilderStateModel.blogRuntimeWizard;
      const directusConnection = await websiteBuilderDirectusConnectionForBlogConfigure(websiteBuilderStateModel.selectedSite, {
        ...options,
        directusConnection: options.directusConnection || state.pendingDirectusConnection
      });
      if (!directusConnection) {
        return null;
      }
      state.pendingDirectusConnection = directusConnection;
      state.loading = true;
      renderWebsiteBuilderBlogInstallWizard();
      try {
        const payload = {
          feature: "blog",
          selected_stack: {
            blog: "blog",
            cms: "directus",
            database: "sqlite"
          },
          runtime_lane: options.runtimeLane || websiteBuilderStateModel.selectedSite?.lane || "local",
          install_status: "pending_deploy",
          install_order: [...websiteBuilderBlogLayerInstallOrder],
          directus_connection: directusConnection
        };
        const layerPayload = {
          ...payload,
          keep_existing: true,
          setup_local_directus: true
        };
        websiteBuilderBlogAddActivity("Saving Blog runtime intent to site.json...");
        const result = await websiteBuilderBlogPersistIntentApi(siteId, payload);
        if (!result.ok) {
          throw new Error(result.message || "Blog intent could not be saved.");
        }
        state.contract = result.contract
          ? websiteBuilderBlogNormalizeContract(result.contract)
          : await websiteBuilderBlogGetAssumptions(siteId);
        websiteBuilderBlogAddActivity("Blog intent saved. Installing local runtime layers...");
        renderWebsiteBuilderBlogInstallWizard();

        for (const layerId of websiteBuilderBlogLayerInstallOrder) {
          const layerLabel = websiteBuilderBlogLayerLabels[layerId] || layerId;
          websiteBuilderBlogAddActivity(`Installing ${layerLabel} layer...`);
          const layerResult = await websiteBuilderBlogInstallLayerApi(siteId, layerId, layerPayload);
          if (!layerResult.ok) {
            throw new Error(layerResult.message || `${layerLabel} layer could not be installed.`);
          }
          state.contract = layerResult.contract
            ? websiteBuilderBlogNormalizeContract(layerResult.contract)
            : await websiteBuilderBlogGetAssumptions(siteId);
          const layerStatus = websiteBuilderBlogStatusLabel(layerResult.action || layerResult.status || "configured").toLowerCase();
          websiteBuilderBlogAddActivity(`${layerLabel} layer ${layerStatus}.`);
          websiteBuilderBlogSetupActivityLines(layerResult).forEach((line) => websiteBuilderBlogAddActivity(line));
          renderWebsiteBuilderBlogInstallWizard();
        }

        websiteBuilderBlogAddActivity("Blog runtime layers configured. Local Directus setup ran during Configure Blog Runtime.");
        state.pendingDirectusConnection = null;
        setWebsiteBuilderLog("Blog runtime layers configured. Local Directus setup ran during Configure Blog Runtime.");
      } catch (error) {
        websiteBuilderBlogAddActivity(`Blog runtime configuration stopped: ${error.message}`);
        setWebsiteBuilderLog(`Blog runtime configuration stopped: ${error.message}`);
      } finally {
        state.loading = false;
        if (!state.contract) {
          state.contract = websiteBuilderBlogBuildFixtureContract(siteId);
        }
        renderWebsiteBuilderBlogInstallWizard();
      }
    }

    function escapePreviewStyle(css) {
      return String(css || "").replace(/<\/style/gi, "<\\/style");
    }

    function escapePreviewScript(script) {
      return String(script || "").replace(/<\/script/gi, "<\\/script");
    }

    function escapeWebsiteBuilderHtml(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function websiteBuilderDocumentTitle(site = websiteBuilderStateModel.selectedSite) {
      return escapeWebsiteBuilderHtml(site?.name || site?.id || websiteBuilderStateModel.selectedSiteId || "Website");
    }

    function buildWebsiteBuilderFullDocument(bodyHtml, site = websiteBuilderStateModel.selectedSite) {
      const body = String(bodyHtml || "").trim() || `<main class="site-shell"><h1>${websiteBuilderDocumentTitle(site)}</h1><p>Drag blocks from the Visual Builder to begin.</p></main>`;
      return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${websiteBuilderDocumentTitle(site)}</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/script.js" defer><\/script>
</head>
<body>
${body}
</body>
</html>
`;
    }

    function extractWebsiteBuilderBodyHtml(htmlText) {
      const source = String(htmlText || "").trim();
      if (!source) return "";
      try {
        const doc = new DOMParser().parseFromString(source, "text/html");
        const hasFullDocument = /<html[\s>]/i.test(source) || /<body[\s>]/i.test(source) || /<!doctype/i.test(source);
        if (hasFullDocument && doc.body) return doc.body.innerHTML.trim();
      } catch {
        // Fall back to the raw text below.
      }
      return source;
    }

    function buildWebsiteBuilderDraftDocument(htmlText, cssText, jsText = "") {
      const html = String(htmlText || "").trim() || "<main><h1>No page content yet</h1><p>Edit visually or in Source to begin.</p></main>";
      const previewHead = [
        '<base target="_blank">',
        `<style data-main-computer-preview>\n${escapePreviewStyle(cssText)}\n</style>`
      ].join("\n");
      const previewScript = jsText
        ? `\n<script data-main-computer-preview-script>\n${escapePreviewScript(jsText)}\n<\/script>`
        : "";
      let output = html;
      if (/<\/head\s*>/i.test(output)) {
        output = output.replace(/<\/head\s*>/i, `${previewHead}\n</head>`);
      } else if (/<html[\s>]/i.test(output)) {
        output = output.replace(/<html([^>]*)>/i, `<html$1><head>${previewHead}</head>`);
      } else {
        output = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
${previewHead}
</head>
<body>
${output}
</body>
</html>`;
      }
      if (previewScript && /<\/body\s*>/i.test(output)) {
        return output.replace(/<\/body\s*>/i, `${previewScript}\n</body>`);
      }
      return `${output}${previewScript}`;
    }

    function setWebsiteBuilderPreviewLabel(title, meta) {
      if (websiteBuilderPreviewTitle) websiteBuilderPreviewTitle.textContent = title;
      if (websiteBuilderPreviewMeta) websiteBuilderPreviewMeta.textContent = meta;
    }

    function setActiveWebsiteBuilderPreviewButton(mode) {
      [
        [websiteBuilderPreviewDraft, "draft"],
        [websiteBuilderPreviewLocal, "local"],
        [websiteBuilderPreviewDev, "dev"]
      ].forEach(([button, value]) => button?.classList.toggle("active", mode === value));
    }

    function setWebsiteBuilderGrapesFallback(message = "", visible = false) {
      if (websiteBuilderGrapesFallback) {
        websiteBuilderGrapesFallback.hidden = !visible;
        if (message) websiteBuilderGrapesFallback.textContent = message;
      }
      if (websiteBuilderGrapesHost) {
        websiteBuilderGrapesHost.classList.toggle("grapes-unavailable", Boolean(visible));
      }
    }

    function websiteBuilderGrapesAvailable() {
      return typeof window.grapesjs !== "undefined" && typeof window.grapesjs.init === "function";
    }


    function websiteBuilderBlogWidgetStyles() {
      return `/* Main Computer blog widget styles */
/* mc-blog-article-presentation-v1 */
/* mc-blog-index-grid-layout-v1 */
/* mc-blog-search-pagination-controls-v1 */
.mc-blog-widget,
.mc-blog-post-widget {
  background: #ffffff;
}

.mc-blog-widget[hidden],
.mc-blog-post-widget[hidden] {
  display: none !important;
}

body[data-mc-blog-route-mode="index"] .mc-blog-post-widget[data-mc-widget="blog-post-viewer"],
body[data-mc-blog-route-mode="detail"] .mc-blog-widget[data-mc-widget="blog-list"] {
  display: none;
}

body[data-mc-blog-route-mode="detail"] {
  background: #f8fafc;
}

body[data-mc-blog-route-mode="detail"] main {
  display: block;
  min-height: 100vh;
  width: 100%;
  padding: clamp(2rem, 6vw, 5rem) clamp(1rem, 4vw, 2rem);
}

.mc-section.mc-blog-widget[data-mc-widget="blog-list"] {
  width: min(1120px, calc(100vw - 48px));
  max-width: none;
  margin-left: auto;
  margin-right: auto;
  padding: clamp(3rem, 7vw, 6rem) 0;
  box-sizing: border-box;
}

.mc-blog-widget__header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: end;
  margin-bottom: 1.5rem;
}


.mc-blog-widget__controls {
  display: flex;
  flex-wrap: nowrap;
  gap: .5rem;
  align-items: end;
  margin: -.25rem 0 1rem;
  padding: .625rem .75rem;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: .85rem;
  background: #f8fafc;
}

.mc-blog-widget__control {
  display: grid;
  flex: 0 0 auto;
  gap: .2rem;
  min-width: 0;
  color: #0f172a;
  font-weight: 700;
}

.mc-blog-widget__control:first-child {
  flex: 1 1 16rem;
  min-width: 12rem;
}

.mc-blog-widget__control span {
  color: #64748b;
  font-size: .68rem;
  letter-spacing: .06em;
  line-height: 1.1;
  text-transform: uppercase;
}

.mc-blog-widget__control input {
  width: 100%;
  min-height: 2.15rem;
  padding: .4rem .55rem;
  border: 1px solid #cbd5e1;
  border-radius: .6rem;
  color: #0f172a;
  background: #ffffff;
  font: inherit;
}

.mc-blog-widget__control input[type="number"] {
  width: 6.25rem;
}

.mc-blog-widget__apply,
.mc-blog-widget__page-link {
  min-height: 2.15rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: .4rem .75rem;
  border: 1px solid #2563eb;
  border-radius: .6rem;
  color: #ffffff;
  background: #2563eb;
  font-weight: 800;
  text-decoration: none;
  cursor: pointer;
}

.mc-blog-widget__page-link.is-disabled {
  border-color: #cbd5e1;
  color: #94a3b8;
  background: #f8fafc;
  cursor: default;
}

.mc-blog-widget__summary {
  margin: 0 0 1rem;
  color: #64748b;
  font-size: .95rem;
  font-weight: 700;
}

.mc-blog-widget__pagination {
  display: flex;
  flex-wrap: wrap;
  gap: .75rem;
  align-items: center;
  justify-content: space-between;
  margin-top: 1.25rem;
}

.mc-blog-widget__page-status {
  color: #475569;
  font-weight: 800;
}

.mc-blog-widget__items {
  width: 100%;
  max-width: none;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.5rem;
  align-items: stretch;
  box-sizing: border-box;
}

.mc-blog-widget__placeholder,
.mc-blog-card,
.mc-blog-post-widget__empty,
.mc-blog-post-widget__article {
  min-height: 11rem;
  padding: 1.25rem;
  border-radius: 1.5rem;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 18px 45px rgba(15, 23, 42, .08);
}

.mc-blog-widget__placeholder,
.mc-blog-post-widget__empty {
  color: #64748b;
}

.mc-blog-card {
  display: grid;
  width: auto;
  min-width: 0;
  max-width: none;
  gap: .75rem;
  align-content: start;
  box-sizing: border-box;
  overflow-wrap: anywhere;
}

.mc-blog-card > * {
  min-width: 0;
  max-width: 100%;
}

.mc-blog-card__title {
  color: #0f172a;
  font-size: 1.1rem;
  font-weight: 900;
  line-height: 1.25;
  text-decoration: none;
}

.mc-blog-card__title:hover {
  text-decoration: underline;
}

.mc-blog-card__excerpt,
.mc-blog-post-widget__excerpt {
  margin: 0;
  color: #475569;
  line-height: 1.6;
}

.mc-blog-card__date,
.mc-blog-post-widget__date {
  color: #64748b;
  font-size: .85rem;
  font-weight: 700;
}

.mc-section.mc-blog-post-widget,
.mc-blog-post-widget {
  display: block;
  width: min(100%, 920px);
  max-width: 920px;
  margin: 0 auto;
  padding: 0;
}

.mc-blog-post-widget__back {
  display: inline-flex;
  align-items: center;
  gap: .35rem;
  margin: 0 0 1.25rem;
  color: #2563eb;
  font-weight: 800;
  text-decoration: none;
}

.mc-blog-post-widget__back:hover {
  text-decoration: underline;
}

.mc-blog-post-widget__article,
.mc-blog-post-widget__empty {
  width: 100%;
  max-width: none;
  min-height: 0;
  margin: 0 auto;
  padding: clamp(2rem, 5vw, 4rem);
  border-radius: clamp(1.25rem, 3vw, 2rem);
}

.mc-blog-post-widget__article h1 {
  max-width: 13ch;
  margin: .35rem 0 1rem;
  font-size: clamp(2.75rem, 7vw, 5.75rem);
  line-height: .95;
  letter-spacing: -.06em;
  overflow-wrap: anywhere;
}

.mc-blog-post-widget__excerpt {
  max-width: 62ch;
  margin-bottom: 1.5rem;
  font-size: clamp(1.05rem, 2vw, 1.3rem);
}

.mc-blog-post-widget__body {
  max-width: 72ch;
  color: #1e293b;
  font-size: clamp(1.02rem, 1.3vw, 1.13rem);
  line-height: 1.78;
  overflow-wrap: break-word;
}

.mc-blog-post-widget__body > *:first-child {
  margin-top: 0;
}

.mc-blog-post-widget__body > *:last-child {
  margin-bottom: 0;
}

.mc-blog-post-widget__body p,
.mc-blog-post-widget__body ul,
.mc-blog-post-widget__body ol,
.mc-blog-post-widget__body blockquote,
.mc-blog-post-widget__body pre {
  margin: 0 0 1.2rem;
}

.mc-blog-post-widget__body h2,
.mc-blog-post-widget__body h3,
.mc-blog-post-widget__body h4 {
  margin: 2rem 0 .75rem;
  color: #0f172a;
  line-height: 1.15;
  letter-spacing: -.03em;
}

.mc-blog-post-widget__body h2 {
  font-size: clamp(1.75rem, 3.5vw, 2.6rem);
}

.mc-blog-post-widget__body h3 {
  font-size: clamp(1.35rem, 2.5vw, 1.9rem);
}

.mc-blog-post-widget__body a {
  color: #2563eb;
  font-weight: 700;
}

.mc-blog-post-widget__body blockquote {
  padding: .25rem 0 .25rem 1.25rem;
  border-left: .25rem solid #bfdbfe;
  color: #475569;
}

.mc-blog-post-widget__body pre,
.mc-blog-post-widget__body code {
  border-radius: .75rem;
  background: #f1f5f9;
}

.mc-blog-post-widget__body pre {
  overflow-x: auto;
  padding: 1rem;
}

.mc-blog-post-widget__body code {
  padding: .15rem .3rem;
}

@media (max-width: 720px) {
  .mc-section.mc-blog-widget[data-mc-widget="blog-list"] {
    width: calc(100vw - 32px);
    padding-top: 3rem;
    padding-bottom: 3rem;
  }

  .mc-blog-widget__header {
    display: grid;
    align-items: start;
  }

  .mc-blog-widget__items {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  body[data-mc-blog-route-mode="detail"] main {
    padding: 1rem;
  }

  .mc-blog-post-widget__article,
  .mc-blog-post-widget__empty {
    padding: 1.35rem;
  }
}
`;
    }
    
    function websiteBuilderBlogWidgetHydratorScript() {
      return String.raw`(() => {
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
})();`;
    }
    
    function websiteBuilderHasBlogListWidget(html) {
      return /data-mc-widget\s*=\s*["'](?:blog-list|blog-post-viewer)["']/.test(String(html || ""));
    }

    function websiteBuilderEnsureBlogWidgetAssets(payload) {
      if (!websiteBuilderHasBlogListWidget(payload.html)) return payload;
      const nextPayload = {...payload};
      const cssText = String(nextPayload.css || "");
      if (
        !cssText.includes("Main Computer blog widget styles") ||
        !cssText.includes(".mc-blog-widget[hidden]") ||
        !cssText.includes("mc-blog-article-presentation-v1") ||
        !cssText.includes("mc-blog-index-grid-layout-v1") ||
        !cssText.includes("mc-blog-search-pagination-controls-v1")
      ) {
        nextPayload.css = `${cssText.trimEnd()}\n\n${websiteBuilderBlogWidgetStyles()}`.trimStart();
      }
      const jsText = String(nextPayload.js || "");
      if (
        !jsText.includes("mcBlogWidgetSelector") ||
        !jsText.includes("mcBlogWidgetApplyRouteModeVisibility") ||
        !jsText.includes("mcBlogWidgetSanitizeRichHtml") ||
        !jsText.includes("mcBlogWidgetRenderPagination")
      ) {
        nextPayload.js = `${jsText.trimEnd()}\n\n${websiteBuilderBlogWidgetHydratorScript()}`.trimStart();
      }
      return nextPayload;
    }

    function websiteBuilderDefaultCss() {
      return `:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #0f172a;
  background: #f8fafc;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: #f8fafc;
  color: #0f172a;
}

.mc-section {
  padding: clamp(3rem, 7vw, 7rem) max(1.5rem, calc((100vw - 1120px) / 2));
}

.mc-hero {
  min-height: 78vh;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
  gap: clamp(2rem, 6vw, 5rem);
  align-items: center;
  background:
    radial-gradient(circle at 20% 10%, rgba(56, 189, 248, .20), transparent 32rem),
    linear-gradient(135deg, #020617 0%, #111827 55%, #1e1b4b 100%);
  color: #f8fafc;
}

.mc-eyebrow {
  margin: 0 0 1rem;
  color: #93c5fd;
  font-size: .78rem;
  font-weight: 900;
  letter-spacing: .18em;
  text-transform: uppercase;
}

.mc-hero h1,
.mc-heading {
  margin: 0;
  font-size: clamp(2.8rem, 8vw, 6.8rem);
  line-height: .92;
  letter-spacing: -.07em;
}

.mc-lede {
  max-width: 62ch;
  margin: 1.35rem 0 0;
  color: #cbd5e1;
  font-size: clamp(1.05rem, 2vw, 1.35rem);
  line-height: 1.65;
}

.mc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: .8rem;
  margin-top: 2rem;
}

.mc-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 3rem;
  padding: .85rem 1.15rem;
  border-radius: 999px;
  background: #f59e0b;
  color: #111827;
  font-weight: 900;
  text-decoration: none;
}

.mc-button.secondary {
  background: rgba(255, 255, 255, .1);
  color: #f8fafc;
  border: 1px solid rgba(255, 255, 255, .22);
}

.mc-image-card {
  overflow: hidden;
  border-radius: 2rem;
  background: rgba(255, 255, 255, .08);
  border: 1px solid rgba(255, 255, 255, .18);
  box-shadow: 0 30px 90px rgba(0, 0, 0, .28);
}

.mc-image-card img {
  display: block;
  width: 100%;
  min-height: 320px;
  object-fit: cover;
}

.mc-feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1rem;
  background: #f8fafc;
}

.mc-feature {
  min-height: 14rem;
  padding: 1.5rem;
  border-radius: 1.5rem;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 18px 45px rgba(15, 23, 42, .08);
}

.mc-feature strong {
  display: block;
  font-size: 1.15rem;
  margin-bottom: .65rem;
}

.mc-split {
  display: grid;
  grid-template-columns: .95fr 1.05fr;
  gap: clamp(2rem, 6vw, 5rem);
  align-items: center;
  background: #ffffff;
}

.mc-split img {
  width: 100%;
  border-radius: 2rem;
  box-shadow: 0 26px 70px rgba(15, 23, 42, .16);
}

.mc-cta {
  text-align: center;
  background: #0f172a;
  color: #f8fafc;
}

.mc-cta .mc-lede {
  margin-left: auto;
  margin-right: auto;
}

.mc-footer {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  background: #020617;
  color: #cbd5e1;
}

@media (max-width: 820px) {
  .mc-hero,
  .mc-split,
  .mc-feature-grid {
    grid-template-columns: 1fr;
  }

  .mc-footer {
    flex-direction: column;
  }
}
`;
    }

    function websiteBuilderHeroBlock() {
      return `<section class="mc-section mc-hero">
  <div>
    <p class="mc-eyebrow">Main Computer Websites</p>
    <h1>Build a real local website visually.</h1>
    <p class="mc-lede">Drag sections, edit live HTML, save files, and publish through your local Docker lanes.</p>
    <div class="mc-actions">
      <a class="mc-button" href="#start">Start building</a>
      <a class="mc-button secondary" href="#features">See features</a>
    </div>
  </div>
  <figure class="mc-image-card">
    <img src="${websiteBuilderDataSvg("aurora")}" alt="Abstract website preview">
  </figure>
</section>`;
    }

    function websiteBuilderFeaturesBlock() {
      return `<section class="mc-section mc-feature-grid" id="features">
  <article class="mc-feature">
    <strong>Visual editing</strong>
    <p>Use the GrapesJS canvas instead of hand-editing every tag.</p>
  </article>
  <article class="mc-feature">
    <strong>Baked files</strong>
    <p>Save index.html, style.css, and script.js back into the selected project.</p>
  </article>
  <article class="mc-feature">
    <strong>Project URLs</strong>
    <p>Each site can live at its own Website Builder route.</p>
  </article>
</section>`;
    }

    function websiteBuilderSplitBlock() {
      return `<section class="mc-section mc-split">
  <img src="${websiteBuilderDataSvg("workstation")}" alt="Website workstation illustration">
  <div>
    <p class="mc-eyebrow">Real assets</p>
    <h2 class="mc-heading">Start with usable sections.</h2>
    <p class="mc-lede">Drop in an image/text split, replace copy, swap images from the Asset Manager, and save the result as normal website files.</p>
  </div>
</section>`;
    }

    function websiteBuilderCtaBlock() {
      return `<section class="mc-section mc-cta" id="start">
  <p class="mc-eyebrow">Ready to publish</p>
  <h2 class="mc-heading">Ship the page from here.</h2>
  <p class="mc-lede">Use Deploy for the fast lane or Local Server for the production-shaped local lane once the draft looks right.</p>
  <div class="mc-actions" style="justify-content:center">
    <a class="mc-button" href="#contact">Publish next</a>
  </div>
</section>`;
    }


    function websiteBuilderBlogListBlock() {
      return `<section class="mc-section mc-blog-widget" data-mc-widget="blog-list" data-source-ref="blog.posts" data-limit="3">
  <div class="mc-blog-widget__header">
    <div>
      <p class="mc-eyebrow">Latest posts</p>
      <h2 class="mc-heading">From the blog</h2>
    </div>
    <a class="mc-button secondary" href="/blog">View all</a>
  </div>
  <div class="mc-blog-widget__items" data-mc-blog-posts>
    <article class="mc-blog-widget__placeholder">Blog posts will appear here when Blog is configured.</article>
  </div>
</section>`;
    }

    function websiteBuilderBlogPostViewerBlock() {
      return `<section class="mc-section mc-blog-post-widget" data-mc-widget="blog-post-viewer" data-source-ref="blog.posts" data-route-prefix="/blog/">
  <a class="mc-blog-post-widget__back" href="/blog">← All posts</a>
  <div data-mc-blog-post-viewer>
    <article class="mc-blog-post-widget__empty">Open this page at /blog/&lt;slug&gt; to show a published post.</article>
  </div>
</section>`;
    }

    function websiteBuilderFooterBlock() {
      return `<footer class="mc-section mc-footer">
  <strong>Main Computer Website</strong>
  <span>Edit this footer in the visual builder.</span>
</footer>`;
    }

    function configureWebsiteBuilderGrapesBlocks(editor) {
      const blocks = editor.BlockManager;
      if (blocks.get("mc-hero")) return;
      blocks.add("mc-hero", {
        label: "Hero",
        category: "Main Computer",
        media: "🏁",
        content: websiteBuilderHeroBlock()
      });
      blocks.add("mc-feature-grid", {
        label: "Features",
        category: "Main Computer",
        media: "▦",
        content: websiteBuilderFeaturesBlock()
      });
      blocks.add("mc-split", {
        label: "Image split",
        category: "Main Computer",
        media: "▧",
        content: websiteBuilderSplitBlock()
      });
      blocks.add("mc-cta", {
        label: "CTA",
        category: "Main Computer",
        media: "◎",
        content: websiteBuilderCtaBlock()
      });
      blocks.add("mc-blog-list", {
        label: "Blog posts",
        category: "Main Computer",
        media: "✎",
        content: websiteBuilderBlogListBlock()
      });
      blocks.add("mc-blog-post-viewer", {
        label: "Blog post viewer",
        category: "Main Computer",
        media: "▤",
        content: websiteBuilderBlogPostViewerBlock()
      });
      blocks.add("mc-footer", {
        label: "Footer",
        category: "Main Computer",
        media: "▔",
        content: websiteBuilderFooterBlock()
      });
    }

    function ensureWebsiteBuilderGrapesEditor() {
      if (websiteBuilderStateModel.grapesEditor) return websiteBuilderStateModel.grapesEditor;
      if (!websiteBuilderGrapesCanvas) return null;
      if (!websiteBuilderGrapesAvailable()) {
        setWebsiteBuilderGrapesFallback("GrapesJS did not load from the CDN. Source editing and draft preview still work.", true);
        return null;
      }
      setWebsiteBuilderGrapesFallback("", false);
      const editor = window.grapesjs.init({
        container: websiteBuilderGrapesCanvas,
        height: "100%",
        width: "auto",
        fromElement: false,
        storageManager: false,
        noticeOnUnload: false,
        selectorManager: {componentFirst: true},
        assetManager: {assets: websiteBuilderDefaultAssets()},
        deviceManager: {
          devices: [
            {name: "Desktop", width: ""},
            {name: "Tablet", width: "768px", widthMedia: "992px"},
            {name: "Mobile", width: "375px", widthMedia: "640px"}
          ]
        }
      });
      configureWebsiteBuilderGrapesBlocks(editor);
      editor.on("update", () => {
        if (websiteBuilderStateModel.syncingGrapes) return;
        syncWebsiteBuilderSourceFromGrapes();
      });
      websiteBuilderStateModel.grapesEditor = editor;
      return editor;
    }

    function loadWebsiteBuilderGrapesFromSource({force = false} = {}) {
      const editor = ensureWebsiteBuilderGrapesEditor();
      if (!editor) return false;
      const siteId = websiteBuilderStateModel.selectedSiteId || "";
      if (!force && websiteBuilderStateModel.grapesLoadedSiteId === siteId && websiteBuilderStateModel.syncingGrapes) return true;
      websiteBuilderStateModel.syncingGrapes = true;
      try {
        const htmlBody = extractWebsiteBuilderBodyHtml(websiteBuilderHtml?.value || "");
        const cssText = websiteBuilderCss?.value || websiteBuilderDefaultCss();
        editor.setComponents(htmlBody || websiteBuilderHeroBlock() + websiteBuilderFeaturesBlock() + websiteBuilderCtaBlock() + websiteBuilderFooterBlock());
        editor.setStyle(cssText);
        websiteBuilderStateModel.grapesLoadedSiteId = siteId;
        editor.refresh();
      } finally {
        window.setTimeout(() => {
          websiteBuilderStateModel.syncingGrapes = false;
        }, 0);
      }
      return true;
    }

    function updateWebsiteBuilderBuilderMetadataFromGrapes() {
      if (!websiteBuilderState || !websiteBuilderStateModel.grapesEditor) return;
      let data = {};
      try {
        data = JSON.parse(websiteBuilderState.value || "{}");
        if (!data || typeof data !== "object" || Array.isArray(data)) data = {};
      } catch {
        data = {};
      }
      data.version = 2;
      data.engine = "grapesjs";
      data.entry_html = "index.html";
      data.stylesheet = "style.css";
      data.script = "script.js";
      data.updated_at = new Date().toISOString();
      websiteBuilderState.value = JSON.stringify(data, null, 2) + "\n";
    }

    function syncWebsiteBuilderSourceFromGrapes({markDirty = true} = {}) {
      const editor = websiteBuilderStateModel.grapesEditor;
      if (!editor || websiteBuilderStateModel.syncingGrapes) return false;
      const canvasHtml = editor.getHtml() || "";
      const canvasCss = editor.getCss() || websiteBuilderCss?.value || "";
      if (websiteBuilderHtml) websiteBuilderHtml.value = buildWebsiteBuilderFullDocument(canvasHtml, websiteBuilderStateModel.selectedSite);
      if (websiteBuilderCss) websiteBuilderCss.value = canvasCss;
      updateWebsiteBuilderBuilderMetadataFromGrapes();
      if (markDirty) markWebsiteBuilderDirty();
      scheduleWebsiteBuilderDraftPreview();
      return true;
    }

    function scheduleWebsiteBuilderSourceToGrapes() {
      window.clearTimeout(websiteBuilderStateModel.sourceUpdateTimer);
      websiteBuilderStateModel.sourceUpdateTimer = window.setTimeout(() => {
        if (websiteBuilderStateModel.activeTab === "design") {
          loadWebsiteBuilderGrapesFromSource({force: true});
        }
      }, 280);
    }

    function updateWebsiteBuilderInspector() {
      const site = websiteBuilderStateModel.selectedSite;
      const localUrl = websiteBuilderLaneUrl(site, "local") || "—";
      const devUrl = websiteBuilderLaneUrl(site, "dev") || "—";
      const siteText = site ? `${site.name || site.id}` : "—";
      const siteMeta = site ? `${site.id} · ${site.kind || "site"}` : "Select a site to begin.";

      if (websiteBuilderInspectorSite) websiteBuilderInspectorSite.textContent = siteText;
      if (websiteBuilderInspectorMode) websiteBuilderInspectorMode.textContent = `${websiteBuilderStateModel.activeTab} / ${websiteBuilderStateModel.previewMode}`;
      if (websiteBuilderInspectorLocal) websiteBuilderInspectorLocal.textContent = localUrl;
      if (websiteBuilderInspectorDev) websiteBuilderInspectorDev.textContent = devUrl;

      if (websiteBuilderOverviewSite) websiteBuilderOverviewSite.textContent = siteMeta;
      if (websiteBuilderOverviewLocal) websiteBuilderOverviewLocal.textContent = localUrl;
      if (websiteBuilderOverviewDev) websiteBuilderOverviewDev.textContent = devUrl;
      if (websiteBuilderPublishLocalUrl) websiteBuilderPublishLocalUrl.textContent = localUrl;
      if (websiteBuilderPublishDevUrl) websiteBuilderPublishDevUrl.textContent = devUrl;
      const remote = websiteBuilderPublishTargets(site).remote_prod;
      const remoteUrl = websiteBuilderVisitUrl(site, "remote_prod");
      if (websiteBuilderPublishRemoteUrl) {
        websiteBuilderPublishRemoteUrl.textContent = remoteUrl
          ? `Published URL: ${remoteUrl}`
          : websiteBuilderAcceptedPublishTarget(site)
            ? "No published site URL returned yet."
            : "No accepted publishing command setup.";
      }
      renderWebsiteBuilderPublishTargetControls(site);
      updateWebsiteBuilderVisitButtons(site);
      if (websiteBuilderSettingsManifest) {
        websiteBuilderSettingsManifest.textContent = site
          ? `Manifest: runtime/websites/${site.id}/site.json`
          : "Site settings are stored in runtime/websites/<site-id>/site.json.";
      }
    }

    function setWebsiteBuilderDraftPreview() {
      websiteBuilderStateModel.previewMode = "draft";
      setActiveWebsiteBuilderPreviewButton("draft");
      if (!websiteBuilderPreviewFrame) return;
      websiteBuilderPreviewFrame.removeAttribute("src");
      websiteBuilderPreviewFrame.srcdoc = buildWebsiteBuilderDraftDocument(
        websiteBuilderHtml?.value || "",
        websiteBuilderCss?.value || "",
        websiteBuilderJs?.value || ""
      );
      const siteName = websiteBuilderStateModel.selectedSite?.name || websiteBuilderStateModel.selectedSiteId || "selected site";
      setWebsiteBuilderPreviewLabel(
        websiteBuilderGrapesAvailable() ? "GrapesJS canvas" : "Draft preview",
        `Live unsaved view for ${siteName}. Save, then Deploy or Local Server when ready.`
      );
      updateWebsiteBuilderInspector();
    }

    function scheduleWebsiteBuilderDraftPreview() {
      if (websiteBuilderStateModel.previewMode !== "draft") return;
      window.clearTimeout(websiteBuilderStateModel.previewUpdateTimer);
      websiteBuilderStateModel.previewUpdateTimer = window.setTimeout(setWebsiteBuilderDraftPreview, 180);
    }

    function setWebsiteBuilderPublishedPreview(laneName) {
      const site = websiteBuilderStateModel.selectedSite;
      const url = websiteBuilderLaneUrl(site, laneName);
      if (!url) {
        setWebsiteBuilderLog(`No ${websiteBuilderLaneLabel(laneName)} URL is configured for ${site?.id || "this site"}.`);
        return;
      }
      websiteBuilderStateModel.previewMode = laneName;
      setActiveWebsiteBuilderPreviewButton(laneName);
      if (websiteBuilderPreviewFrame) {
        websiteBuilderPreviewFrame.removeAttribute("srcdoc");
        websiteBuilderPreviewFrame.src = `${url}${url.includes("?") ? "&" : "?"}mc_preview=${Date.now()}`;
      }
      setWebsiteBuilderPreviewLabel(`${websiteBuilderLaneLabel(laneName)} published view`, url);
      updateWebsiteBuilderInspector();
    }

    function refreshWebsiteBuilderPreview() {
      if (websiteBuilderStateModel.previewMode === "draft") {
        setWebsiteBuilderDraftPreview();
      } else {
        setWebsiteBuilderPublishedPreview(websiteBuilderStateModel.previewMode);
      }
    }

    function openWebsiteBuilderPreview() {
      const site = websiteBuilderStateModel.selectedSite;
      const url = websiteBuilderStateModel.previewMode === "draft"
        ? ""
        : websiteBuilderLaneUrl(site, websiteBuilderStateModel.previewMode);
      if (!url) {
        setWebsiteBuilderLog("Draft preview is embedded only. Switch to Deploy or Local Server to open a published URL.");
        return;
      }
      window.open(url, "_blank", "noopener,noreferrer");
    }

    function setWebsiteBuilderPreviewDevice(deviceName) {
      const normalized = ["desktop", "tablet", "mobile"].includes(deviceName) ? deviceName : "desktop";
      websiteBuilderStateModel.previewDevice = normalized;
      websiteBuilderDeviceButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.websiteBuilderDevice === normalized);
      });
      if (websiteBuilderPreviewStage) {
        websiteBuilderPreviewStage.classList.toggle("tablet", normalized === "tablet");
        websiteBuilderPreviewStage.classList.toggle("mobile", normalized === "mobile");
      }
      const editor = websiteBuilderStateModel.grapesEditor;
      if (editor) {
        const device = normalized === "desktop" ? "Desktop" : normalized === "tablet" ? "Tablet" : "Mobile";
        editor.setDevice(device);
      }
    }

    function renderWebsiteBuilderLinks(site) {
      if (!websiteBuilderLinks) return;
      websiteBuilderLinks.replaceChildren();
      if (!site) return;
      [["Deploy", websiteBuilderLaneUrl(site, "dev")], ["Local Server", websiteBuilderLaneUrl(site, "local")]]
        .filter(([, url]) => Boolean(url))
        .forEach(([label, url]) => {
          const link = document.createElement("a");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = `${label}: ${url}`;
          websiteBuilderLinks.append(link);
        });
    }

    function syncWebsiteBuilderSiteSelect() {
      if (!websiteBuilderSiteSelect) return;
      websiteBuilderSiteSelect.replaceChildren();
      websiteBuilderStateModel.sites.forEach((site) => {
        const option = document.createElement("option");
        option.value = site.id;
        option.textContent = site.name || site.id;
        option.selected = site.id === websiteBuilderStateModel.selectedSiteId;
        websiteBuilderSiteSelect.append(option);
      });
    }

    function renderWebsiteBuilderSites() {
      if (!websiteBuilderSiteList) return;
      websiteBuilderSiteList.replaceChildren();
      if (!websiteBuilderStateModel.sites.length) {
        const empty = document.createElement("p");
        empty.textContent = "No website projects found.";
        websiteBuilderSiteList.append(empty);
        syncWebsiteBuilderSiteSelect();
        updateWebsiteBuilderArchiveControl();
        return;
      }
      websiteBuilderStateModel.sites.forEach((site) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "website-site-card";
        card.classList.toggle("active", site.id === websiteBuilderStateModel.selectedSiteId);
        card.dataset.siteId = site.id;
        const title = document.createElement("strong");
        title.textContent = site.name || site.id;
        const meta = document.createElement("span");
        const localService = websiteBuilderLane(site, "local").service || "no Local Server service";
        meta.textContent = `${site.id} · ${site.kind || "site"} · ${localService}`;
        card.append(title, meta);
        card.addEventListener("click", () => selectWebsiteBuilderSite(site.id));
        websiteBuilderSiteList.append(card);
      });
      syncWebsiteBuilderSiteSelect();
      updateWebsiteBuilderArchiveControl();
    }

    function safeWebsiteBuilderChatSiteId(value = websiteBuilderStateModel.selectedSiteId) {
      const clean = String(value || "hub-site").replace(/\\/g, "/").split("/").filter(Boolean).join("-");
      return clean.replace(/[^a-zA-Z0-9_.-]/g, "-") || "hub-site";
    }

    function websiteBuilderSitePath(siteId = websiteBuilderStateModel.selectedSiteId) {
      return `runtime/websites/${safeWebsiteBuilderChatSiteId(siteId)}`;
    }

    function websiteBuilderChatThreadKey(siteId = websiteBuilderStateModel.selectedSiteId) {
      return `website-project:${safeWebsiteBuilderChatSiteId(siteId)}`;
    }

    function websiteBuilderChatLinkedTarget(context = null) {
      const siteId = safeWebsiteBuilderChatSiteId(context?.site_id || context?.target_id || websiteBuilderStateModel.selectedSiteId);
      return {
        app: "website-builder",
        kind: "website-project",
        id: siteId,
        path: websiteBuilderSitePath(siteId)
      };
    }

    function websiteBuilderChatContextSnapshot() {
      const siteId = safeWebsiteBuilderChatSiteId();
      const site = websiteBuilderStateModel.selectedSite || {};
      return {
        app: "website-builder",
        active_app: "website-builder",
        target_kind: "website-project",
        target_id: siteId,
        site_id: siteId,
        project_id: siteId,
        site_path: websiteBuilderSitePath(siteId),
        allowed_root: `${websiteBuilderSitePath(siteId)}/`,
        allowed_paths: [
          `${websiteBuilderSitePath(siteId)}/site.json`,
          `${websiteBuilderSitePath(siteId)}/index.html`,
          `${websiteBuilderSitePath(siteId)}/style.css`,
          `${websiteBuilderSitePath(siteId)}/script.js`,
          `${websiteBuilderSitePath(siteId)}/builder.json`,
          `${websiteBuilderSitePath(siteId)}/assets/**`,
          `${websiteBuilderSitePath(siteId)}/data/**`,
          `${websiteBuilderSitePath(siteId)}/blog/**`
        ],
        edit_mode: "proposal-only-context",
        dirty: Boolean(websiteBuilderStateModel.dirty),
        preview_mode: String(websiteBuilderStateModel.previewMode || "draft"),
        active_tab: String(websiteBuilderStateModel.activeTab || "design"),
        active_file: String(websiteBuilderStateModel.activeFile || "html"),
        site: {
          id: siteId,
          name: String(site.name || siteId),
          kind: String(site.kind || "site"),
          lane: String(site.lane || "")
        }
      };
    }

    function websiteBuilderBuildChatThreadMetadata(context = null) {
      const target = websiteBuilderChatLinkedTarget(context);
      return {
        origin_app: "website-builder",
        embedded_chat: true,
        target_kind: "website-project",
        target_id: target.id,
        linked_targets: [target],
        website_builder_phase: "mounted-rag-proposal"
      };
    }

    function findWebsiteBuilderLinkedChatThread(store, siteId = websiteBuilderStateModel.selectedSiteId) {
      const key = websiteBuilderChatThreadKey(siteId);
      const linkedId = websiteBuilderLinkedChatThreads.get(key);
      let thread = linkedId ? store?.get?.(linkedId) : null;
      if (thread) return thread;
      const target = websiteBuilderChatLinkedTarget({site_id: siteId});
      thread = (store?.list?.() || []).find((candidate) => {
        const metadata = candidate?.metadata || {};
        const linkedTargets = Array.isArray(metadata.linked_targets) ? metadata.linked_targets : [];
        return metadata.origin_app === "website-builder" && linkedTargets.some((linked) => (
          linked?.kind === "website-project"
          && String(linked?.id || "") === target.id
          && String(linked?.path || "") === target.path
        ));
      }) || null;
      if (thread?.id) websiteBuilderLinkedChatThreads.set(key, thread.id);
      return thread;
    }

    function ensureWebsiteBuilderLinkedChatThread() {
      const store = window.MainComputerChatThreads;
      if (!store?.load) return null;
      store.load();
      const siteId = safeWebsiteBuilderChatSiteId();
      let thread = findWebsiteBuilderLinkedChatThread(store, siteId);
      if (!thread && store?.create) {
        thread = store.create({
          title: "Website Builder Chat",
          metadata: websiteBuilderBuildChatThreadMetadata(websiteBuilderChatContextSnapshot()),
          makeActive: false
        });
      }
      if (thread?.id) websiteBuilderLinkedChatThreads.set(websiteBuilderChatThreadKey(siteId), thread.id);
      return thread || null;
    }

    function getWebsiteBuilderLinkedChatThreadId() {
      const key = websiteBuilderChatThreadKey();
      return websiteBuilderLinkedChatThreads.get(key) || "";
    }

    function setWebsiteBuilderLinkedChatThreadId(threadId, thread, context = {}) {
      const id = String(threadId || thread?.id || "");
      if (!id) return;
      websiteBuilderLinkedChatThreads.set(websiteBuilderChatThreadKey(), id);
      if (websiteBuilderChatPanel) {
        websiteBuilderChatPanel.dataset.linkedThreadId = id;
        websiteBuilderChatPanel.dataset.chatConsoleTargetId = safeWebsiteBuilderChatSiteId();
        if (context?.reason) websiteBuilderChatPanel.dataset.linkReason = String(context.reason);
      }
    }

    function buildWebsiteBuilderChatThreadLink(thread) {
      const url = new URL(window.location.href);
      url.pathname = `/applications/website-builder/${safeWebsiteBuilderChatSiteId()}`;
      url.searchParams.set("thread", thread?.id || getWebsiteBuilderLinkedChatThreadId());
      return url.toString();
    }

    function mountWebsiteBuilderChat({force = false} = {}) {
      if (!websiteBuilderChatPanel || !window.MainComputerChatConsole?.mountEmbedded) return null;
      const siteId = safeWebsiteBuilderChatSiteId();
      websiteBuilderChatPanel.dataset.chatConsoleTargetId = siteId;
      if (force && websiteBuilderStateModel.chatController?.destroy) {
        websiteBuilderStateModel.chatController.destroy();
        websiteBuilderStateModel.chatController = null;
      }
      const thread = ensureWebsiteBuilderLinkedChatThread();
      websiteBuilderStateModel.chatController = window.MainComputerChatConsole.mountEmbedded(websiteBuilderChatPanel, {
        embedId: "website-builder",
        activeApp: "website-builder",
        idPrefix: "website-builder-mounted-chat",
        classPrefix: "website-builder-chat",
        title: "Website Builder Chat",
        subtitle: "Editing this website",
        initialStatus: "proposal-only",
        targetKind: "website-project",
        targetId: siteId,
        layout: "full",
        showThreadRail: true,
        showCurrentThreadBar: true,
        threadId: thread?.id || getWebsiteBuilderLinkedChatThreadId(),
        getLinkedThreadId: getWebsiteBuilderLinkedChatThreadId,
        setLinkedThreadId: setWebsiteBuilderLinkedChatThreadId,
        buildThreadLink: buildWebsiteBuilderChatThreadLink,
        getEmbeddedContext: websiteBuilderChatContextSnapshot,
        buildThreadMetadata: websiteBuilderBuildChatThreadMetadata,
        plugins: [
          {
            id: "website-builder-edit",
            label: "Edit this website",
            checkedLabel: "Editing this website",
            hint: "Route this AI request through the Website Builder RAG proposal pathway, locked to the active site and builder allowlist.",
            appliesTo: "ai",
            defaultEnabled: true,
            endpoint: "/api/applications/website-builder/chat",
            pathway: "website-builder-rag-edit-proposal",
            targetKind: "website-project",
            targetId: siteId,
            lockedTarget: true,
            buildPayload({embedded_context: embeddedContext, config}) {
              const context = embeddedContext && typeof embeddedContext === "object" && !Array.isArray(embeddedContext) ? embeddedContext : {};
              const lockedSiteId = safeWebsiteBuilderChatSiteId(context.site_id || context.target_id || config?.targetId || siteId);
              return {
                edit_mode: "website-project",
                editor_edit_mode: "website-builder",
                requested_pathway: "website-builder-rag-edit-proposal",
                target_kind: "website-project",
                target_id: lockedSiteId,
                project_id: lockedSiteId,
                site_id: lockedSiteId,
                locked_to_mount: true,
                auto_apply: true,
                live_apply: true
              };
            }
          }
        ],
        status(message) {
          if (websiteBuilderChatStatus && message) websiteBuilderChatStatus.textContent = message;
          if (websiteBuilderChatPanel && message) websiteBuilderChatPanel.dataset.chatStatus = message;
        }
      });
      return websiteBuilderStateModel.chatController;
    }

    function refreshWebsiteBuilderChatMount(previousSiteId = websiteBuilderStateModel.selectedSiteId) {
      const previous = safeWebsiteBuilderChatSiteId(previousSiteId);
      const current = safeWebsiteBuilderChatSiteId();
      if (websiteBuilderChatPanel) websiteBuilderChatPanel.dataset.chatConsoleTargetId = current;
      if (!websiteBuilderStateModel.chatOpen) return websiteBuilderStateModel.chatController || null;
      if (previous !== current && websiteBuilderStateModel.chatController) return mountWebsiteBuilderChat({force: true});
      return mountWebsiteBuilderChat();
    }

    function setWebsiteBuilderChatOpen(open, {mountChat = true} = {}) {
      const shouldOpen = Boolean(open) && document.body.dataset.activeApp === "website-builder";
      websiteBuilderStateModel.chatOpen = shouldOpen;
      if (websiteBuilderChatPopout) websiteBuilderChatPopout.hidden = !shouldOpen;
      websiteBuilderChatToggle?.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      websiteBuilderApp?.classList.toggle("chat-open", shouldOpen);
      if (shouldOpen && mountChat) mountWebsiteBuilderChat();
    }

    async function loadWebsiteBuilderSites({selectFirst = false, replaceRoute = false} = {}) {
      const routeSiteId = websiteBuilderSiteIdFromPath(window.location.pathname);
      if (!websiteBuilderStateModel.selectedSiteId && routeSiteId) {
        websiteBuilderStateModel.selectedSiteId = routeSiteId;
      }
      const payload = await websiteBuilderApi("/api/applications/websites/sites");
      websiteBuilderStateModel.loaded = true;
      websiteBuilderStateModel.sites = payload.sites || [];
      const selectedStillExists = websiteBuilderStateModel.sites.some((site) => site.id === websiteBuilderStateModel.selectedSiteId);
      if (!selectedStillExists || selectFirst) {
        websiteBuilderStateModel.selectedSiteId = websiteBuilderStateModel.sites[0]?.id || "";
      }
      const selectedDiffersFromRoute = Boolean(routeSiteId && websiteBuilderStateModel.selectedSiteId && routeSiteId !== websiteBuilderStateModel.selectedSiteId);
      renderWebsiteBuilderSites();
      if (websiteBuilderStateModel.selectedSiteId) {
        await selectWebsiteBuilderSite(websiteBuilderStateModel.selectedSiteId, {
          skipRender: true,
          replaceRoute: replaceRoute || selectedDiffersFromRoute
        });
      } else {
        websiteBuilderStateModel.selectedSite = null;
        updateWebsiteBuilderInspector();
        updateWebsiteBuilderArchiveControl();
      }
      setWebsiteBuilderLog(`Loaded ${websiteBuilderStateModel.sites.length} website project(s).`);
    }

    async function selectWebsiteBuilderSite(siteId, {skipRender = false, syncRoute = true, replaceRoute = false} = {}) {
      if (!siteId) return;
      const previousSiteId = websiteBuilderStateModel.selectedSiteId;
      websiteBuilderStateModel.selectedSiteId = siteId;
      if (syncRoute && normalizeWebsiteBuilderRouteSiteId(siteId)) {
        syncWebsiteBuilderRoute(siteId, {replace: replaceRoute});
      }
      const payload = await websiteBuilderApi(`/api/applications/websites/site?site_id=${encodeURIComponent(siteId)}`);
      const site = payload.site;
      websiteBuilderStateModel.selectedSite = site;
      if (websiteBuilderSiteName) websiteBuilderSiteName.textContent = site.name || site.id;
      if (websiteBuilderSiteMeta) {
        const local = websiteBuilderLaneUrl(site, "local") || "not configured";
        const dev = websiteBuilderLaneUrl(site, "dev") || "not configured";
        websiteBuilderSiteMeta.textContent = `${site.id} · ${site.kind} · Deploy ${dev} · Local Server ${local}`;
      }
      if (websiteBuilderHtml) websiteBuilderHtml.value = payload.html || "";
      if (websiteBuilderCss) websiteBuilderCss.value = payload.css || websiteBuilderDefaultCss();
      if (websiteBuilderJs) websiteBuilderJs.value = payload.js || "";
      if (websiteBuilderState) websiteBuilderState.value = payload.builder || "";
      websiteBuilderStateModel.dirty = false;
      renderWebsiteBuilderLinks(site);
      loadWebsiteBuilderGrapesFromSource({force: true});
      setWebsiteBuilderDraftPreview();
      if (!skipRender) renderWebsiteBuilderSites();
      syncWebsiteBuilderSiteSelect();
      updateWebsiteBuilderArchiveControl();
      updateWebsiteBuilderInspector();
      renderWebsiteBuilderBackendView();
      renderWebsiteBuilderPublishTargetControls(site);
      refreshWebsiteBuilderChatMount(previousSiteId);
    }

    async function refreshWebsiteBuilderAfterRagApply(detail = {}) {
      const metadata = detail?.output_cell?.metadata || detail?.metadata || {};
      const applyResult = metadata.apply_result || metadata.proposal?.apply_result || null;
      if (!applyResult?.ok) return;
      if (String(metadata.editor_edit_mode || "") !== "website-builder") return;
      const appliedSiteId = safeWebsiteBuilderChatSiteId(metadata.site_id || applyResult.site_id || "");
      if (appliedSiteId && appliedSiteId !== safeWebsiteBuilderChatSiteId(websiteBuilderStateModel.selectedSiteId)) return;
      const files = Array.isArray(applyResult.files) ? applyResult.files : [];
      const touchedSiteFiles = files.some((item) => String(item?.path || "").startsWith(`${websiteBuilderSitePath(websiteBuilderStateModel.selectedSiteId)}/`));
      if (touchedSiteFiles) {
        await selectWebsiteBuilderSite(websiteBuilderStateModel.selectedSiteId, {syncRoute: false});
        setWebsiteBuilderLog("Website Builder RAG edit applied and preview reloaded", files.map((item) => item?.path).filter(Boolean).join("\n"));
      } else {
        refreshWebsiteBuilderPreview();
        setWebsiteBuilderLog("Website Builder implementation edit applied. Reload the browser tab to run changed builder code.", files.map((item) => item?.path).filter(Boolean).join("\n"));
      }
    }

    async function saveWebsiteBuilderSite() {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) throw new Error("Select a website first.");
      setWebsiteBuilderBusy(true, `Saving ${siteId}...`);
      try {
        const payload = await websiteBuilderApi("/api/applications/websites/site/save", {
          method: "POST",
          body: JSON.stringify(currentWebsiteBuilderFilePayload(siteId))
        });
        websiteBuilderStateModel.dirty = false;
        setWebsiteBuilderLog(`Saved ${payload.site?.id || siteId}.`);
        setWebsiteBuilderDraftPreview();
        await loadWebsiteBuilderSites();
        return payload;
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    function mergeWebsiteBuilderSite(site) {
      if (!site?.id) return;
      websiteBuilderStateModel.selectedSite = site;
      websiteBuilderStateModel.selectedSiteId = site.id;
      const existingIndex = websiteBuilderStateModel.sites.findIndex((entry) => entry.id === site.id);
      if (existingIndex >= 0) {
        websiteBuilderStateModel.sites[existingIndex] = site;
      } else {
        websiteBuilderStateModel.sites.push(site);
      }
      renderWebsiteBuilderSites();
      syncWebsiteBuilderSiteSelect();
      renderWebsiteBuilderLinks(site);
      updateWebsiteBuilderInspector();
      renderWebsiteBuilderBackendView();
      renderWebsiteBuilderPublishTargetControls(site);
    }

    async function refreshWebsiteBuilderSiteFromBackend(siteId) {
      const payload = await websiteBuilderApi(`/api/applications/websites/site?site_id=${encodeURIComponent(siteId)}`);
      if (payload.site) mergeWebsiteBuilderSite(payload.site);
      return payload.site || null;
    }

    function websiteBuilderPublishResultUrl(payload, result) {
      const plan = result?.plan && typeof result.plan === "object" ? result.plan : {};
      const resultSite = result?.site && typeof result.site === "object" ? result.site : {};
      const payloadSite = payload?.site && typeof payload.site === "object" ? payload.site : {};
      return normalizeWebsiteBuilderVisitUrl(
        plan.url
        || resultSite?.local_platform?.lanes?.local?.last_published_url
        || resultSite?.local_platform?.lanes?.local?.url
        || payloadSite?.local_platform?.lanes?.local?.last_published_url
        || payloadSite?.local_platform?.lanes?.local?.url
        || ""
      );
    }

    function rememberWebsiteBuilderRemotePublishedUrl(siteId, url) {
      const visitUrl = normalizeWebsiteBuilderVisitUrl(url);
      if (!siteId || !visitUrl) return;
      websiteBuilderStateModel.publishedRemoteProdUrls[siteId] = visitUrl;
      if (websiteBuilderPublishRemoteUrl) websiteBuilderPublishRemoteUrl.textContent = `Published URL: ${visitUrl}`;
      setWebsiteBuilderVisitButton(websiteBuilderVisitRemoteProd, visitUrl, "Publish");
      setWebsiteBuilderVisitButton(websiteBuilderVisitRemoteProdCard, visitUrl, "Publish");
    }

    function websiteBuilderPreviewLaneFromPublishResult(result, fallbackLane) {
      const plan = result?.plan && typeof result.plan === "object" ? result.plan : {};
      const planLane = String(plan.lane || "").trim();
      if (planLane) return planLane;
      return fallbackLane === "remote_prod" || fallbackLane === "publish" ? "remote_prod" : fallbackLane;
    }

    function websiteBuilderPublishPayload(siteId, lane, options = {}) {
      const payload = {...currentWebsiteBuilderFilePayload(siteId), lane};
      if (options.dryRun) {
        payload.dry_run = true;
      }
      if (options.directusConnection) {
        payload.directus_connection = options.directusConnection;
      }
      return payload;
    }

    async function websiteBuilderPublishApi(siteId, lane, options = {}) {
      return await websiteBuilderApi("/api/applications/websites/site/publish", {
        method: "POST",
        body: JSON.stringify(websiteBuilderPublishPayload(siteId, lane, options))
      });
    }

    function websiteBuilderCommandPreview(command) {
      if (Array.isArray(command)) {
        return command.map((part) => String(part || "")).join(" ");
      }
      return String(command || "");
    }

    function websiteBuilderDeployPreflightWarnings(result) {
      const plan = result?.plan && typeof result.plan === "object" ? result.plan : {};
      const command = Array.isArray(plan.command) ? plan.command.map((part) => String(part || "")) : [];
      const recreateReasons = Array.isArray(plan.recreate_reasons)
        ? plan.recreate_reasons.map((reason) => String(reason || "").trim()).filter(Boolean)
        : [];
      const warnings = [];
      if (command.includes("--force-recreate")) {
        const reasonText = recreateReasons.length ? ` Reason: ${recreateReasons.join(" ")}` : "";
        warnings.push(
          `Docker will force-recreate ${plan.service || "the selected site service"}. This restarts that service so stale container env cannot survive.${reasonText}`
        );
      }
      return warnings;
    }

    function websiteBuilderDeployPreflightRequiresAcknowledgement(result) {
      return websiteBuilderDeployPreflightWarnings(result).length > 0;
    }

    function websiteBuilderDeployPreflightBlogText(site) {
      const blog = site?.features?.blog && typeof site.features.blog === "object" ? site.features.blog : null;
      if (!blog || !blog.selected) return "not selected";
      if (blog.enabled) return `selected and enabled (${blog.install_status || "ready"})`;
      return `selected, disabled (${blog.install_status || "pending"})`;
    }

    function websiteBuilderDeployPreflightRows(result, lane) {
      const plan = result?.plan && typeof result.plan === "object" ? result.plan : {};
      const site = result?.site || plan.site || websiteBuilderStateModel.selectedSite || {};
      const cmsServices = Array.isArray(plan.cms_dependency_services) ? plan.cms_dependency_services : [];
      const command = Array.isArray(plan.command) ? plan.command : [];
      return [
        ["Site", `${site.name || site.id || websiteBuilderStateModel.selectedSiteId || "selected site"}${site.id ? ` (${site.id})` : ""}`],
        ["Lane", websiteBuilderLaneLabel(plan.lane || lane)],
        ["Service", plan.service || "not planned"],
        ["Compose", plan.compose_path ? "will be regenerated before deploy" : "not reported"],
        ["Blog", websiteBuilderDeployPreflightBlogText(site)],
        ["Directus dependency", cmsServices.length ? cmsServices.join(", ") : "none"],
        ["Container recreate", command.includes("--force-recreate") ? "yes, acknowledgement required" : "not forced"]
      ];
    }

    function renderWebsiteBuilderDeployPreflight() {
      if (!websiteBuilderDeployPreflight) return;
      const state = websiteBuilderStateModel.deployPreflight || {};
      websiteBuilderDeployPreflight.hidden = !state.open;
      if (!state.open) return;

      const result = state.result || {};
      const plan = result?.plan && typeof result.plan === "object" ? result.plan : {};
      const lane = plan.lane || state.lane || "dev";
      const service = plan.service || "selected service";
      const warnings = websiteBuilderDeployPreflightWarnings(result);
      const requiresAck = warnings.length > 0;

      if (websiteBuilderDeployPreflightTitle) {
        websiteBuilderDeployPreflightTitle.textContent = `Deploy ${service}?`;
      }
      if (websiteBuilderDeployPreflightSummary) {
        websiteBuilderDeployPreflightSummary.textContent = requiresAck
          ? "Dry-run completed. Review the plan and acknowledge the recreate before deploying."
          : "Dry-run completed. Review the plan before deploying.";
      }
      if (websiteBuilderDeployPreflightDetails) {
        websiteBuilderDeployPreflightDetails.innerHTML = websiteBuilderDeployPreflightRows(result, lane)
          .map(([label, value]) => `<dt>${escapeWebsiteBuilderHtml(label)}</dt><dd>${escapeWebsiteBuilderHtml(value)}</dd>`)
          .join("");
      }
      if (websiteBuilderDeployPreflightCommand) {
        websiteBuilderDeployPreflightCommand.textContent = websiteBuilderCommandPreview(plan.command) || "No command reported.";
      }
      if (websiteBuilderDeployPreflightWarning) {
        websiteBuilderDeployPreflightWarning.hidden = !requiresAck;
      }
      if (websiteBuilderDeployPreflightWarningList) {
        websiteBuilderDeployPreflightWarningList.innerHTML = warnings
          .map((message) => `<p>${escapeWebsiteBuilderHtml(message)}</p>`)
          .join("");
      }
      if (websiteBuilderDeployPreflightAck) {
        websiteBuilderDeployPreflightAck.checked = Boolean(state.acknowledged);
      }
      if (websiteBuilderDeployPreflightConfirm) {
        websiteBuilderDeployPreflightConfirm.textContent = requiresAck ? "Deploy anyway" : "Deploy";
        websiteBuilderDeployPreflightConfirm.disabled = Boolean(requiresAck && !state.acknowledged);
      }
    }

    function closeWebsiteBuilderDeployPreflight() {
      websiteBuilderStateModel.deployPreflight = {
        open: false,
        lane: "",
        result: null,
        requiresAcknowledgement: false,
        acknowledged: false
      };
      renderWebsiteBuilderDeployPreflight();
    }

    async function openWebsiteBuilderDeployPreflight(lane) {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) throw new Error("Select a website first.");
      const laneLabel = websiteBuilderLaneLabel(lane);
      setWebsiteBuilderBusy(true, `Saving and checking ${laneLabel} plan for ${siteId}...`);
      try {
        const payload = await websiteBuilderPublishApi(siteId, lane, {dryRun: true});
        const result = payload.result || payload;
        const preflightSite = payload.site || result.site || null;
        if (preflightSite) {
          mergeWebsiteBuilderSite(preflightSite);
        }
        websiteBuilderStateModel.dirty = false;
        websiteBuilderStateModel.deployPreflight = {
          open: true,
          lane,
          result,
          requiresAcknowledgement: websiteBuilderDeployPreflightRequiresAcknowledgement(result),
          acknowledged: false
        };
        renderWebsiteBuilderDeployPreflight();
        setWebsiteBuilderLog(`Deploy preflight ready for ${siteId}/${laneLabel}.`, result);
        return payload;
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    function websiteBuilderDirectusCms(site, options = {}) {
      const cms = site?.backend?.cms && typeof site.backend.cms === "object" ? site.backend.cms : null;
      if (!cms) return options.requireDirectus ? {} : null;
      const provider = String(cms.provider || "").toLowerCase();
      if (provider && provider !== "directus") return null;
      if (!options.requireDirectus && !cms.required && !cms.service && !cms.storage) return null;
      return cms;
    }

    function websiteBuilderDirectusServiceName(site, cms = websiteBuilderDirectusCms(site)) {
      const internalUrl = String(cms?.service?.internal_url || "");
      try {
        const parsed = new URL(internalUrl);
        if (parsed.hostname) return parsed.hostname;
      } catch {}
      return site?.id ? `${site.id}-directus` : "site-directus";
    }

    function websiteBuilderDirectusPublicPort(cms) {
      const publicUrl = String(cms?.service?.public_url || "");
      try {
        const parsed = new URL(publicUrl);
        if (parsed.port) return parsed.port;
      } catch {}
      return "28200";
    }

    function websiteBuilderDirectusContract(site, options = {}) {
      const cms = websiteBuilderDirectusCms(site, options);
      if (!cms || !site?.id) return null;
      const storage = cms.storage && typeof cms.storage === "object" ? cms.storage : {};
      const serviceName = websiteBuilderDirectusServiceName(site, cms);
      const publicPort = websiteBuilderDirectusPublicPort(cms);
      return {
        site_id: site.id,
        site_name: site.name || site.id,
        service_name: serviceName,
        database_volume: storage.database_volume || `${site.id}_directus_database`,
        uploads_volume: storage.uploads_volume || `${site.id}_directus_uploads`,
        public_port: publicPort,
        public_url: `http://127.0.0.1:${publicPort}`,
        internal_url: `http://${serviceName}:8055`,
        confirmed_at: cms.local_connection?.confirmed_at || "",
        mode: cms.local_connection?.mode || "use_existing"
      };
    }

    function websiteBuilderDirectusConnectionConfirmed(site) {
      const cms = websiteBuilderDirectusCms(site);
      const connection = cms?.local_connection && typeof cms.local_connection === "object" ? cms.local_connection : null;
      return Boolean(connection?.confirmed_at && connection?.database_volume && connection?.uploads_volume);
    }

    function websiteBuilderDirectusVolumeNameValid(value) {
      return /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/.test(String(value || "").trim());
    }

    function websiteBuilderDirectusConnectionFields(mode) {
      const contract = websiteBuilderStateModel.directusConnectionModal.contract || {};
      const databaseVolume = String(websiteBuilderDirectusDatabaseVolume?.value || "").trim();
      const uploadsVolume = String(websiteBuilderDirectusUploadsVolume?.value || "").trim();
      const publicPort = Number.parseInt(String(websiteBuilderDirectusPublicPortv2?.value || ""), 10);
      if (!websiteBuilderDirectusVolumeNameValid(databaseVolume) || !websiteBuilderDirectusVolumeNameValid(uploadsVolume)) {
        throw new Error("Directus volume names must use letters, numbers, dots, underscores, or hyphens.");
      }
      if (!Number.isInteger(publicPort) || publicPort < 1 || publicPort > 65535) {
        throw new Error("Directus public port must be between 1 and 65535.");
      }
      const destructive = mode === "overwrite_existing";
      return {
        mode,
        site_id: contract.site_id,
        service_name: contract.service_name,
        database_volume: databaseVolume,
        uploads_volume: uploadsVolume,
        public_port: publicPort,
        destructive_confirmation: destructive,
        reset_directus_data: destructive
      };
    }

    function renderWebsiteBuilderDirectusConnectionPreview(mode = "use_existing") {
      if (!websiteBuilderDirectusConnectionPreview) return;
      try {
        const payload = websiteBuilderDirectusConnectionFields(mode);
        websiteBuilderDirectusConnectionPreview.textContent = JSON.stringify(payload, null, 2);
      } catch (error) {
        websiteBuilderDirectusConnectionPreview.textContent = error.message;
      }
    }

    function updateWebsiteBuilderDirectusConnectionActions() {
      const acknowledged = Boolean(websiteBuilderDirectusConnectionAck?.checked);
      if (websiteBuilderDirectusConnectionExisting) websiteBuilderDirectusConnectionExisting.disabled = !acknowledged;
      if (websiteBuilderDirectusConnectionNew) websiteBuilderDirectusConnectionNew.disabled = !acknowledged;
      if (websiteBuilderDirectusConnectionOverwrite) websiteBuilderDirectusConnectionOverwrite.disabled = !acknowledged;
    }

    function setWebsiteBuilderDirectusConnectionFields(contract, options = {}) {
      if (websiteBuilderDirectusConnectionSite) websiteBuilderDirectusConnectionSite.textContent = contract.site_id || "not selected";
      if (websiteBuilderDirectusConnectionService) websiteBuilderDirectusConnectionService.textContent = contract.service_name || "not ready";
      if (websiteBuilderDirectusConnectionInternalUrl) websiteBuilderDirectusConnectionInternalUrl.textContent = contract.internal_url || "not ready";
      if (websiteBuilderDirectusConnectionSummary) {
        const context = options.context || "local_publish";
        websiteBuilderDirectusConnectionSummary.textContent = context === "blog_configure"
          ? `Configure Directus for ${contract.site_name || contract.site_id}. Reuse existing data, create separate empty volumes, or explicitly overwrite old Directus data.`
          : `Choose Directus data for ${contract.site_name || contract.site_id}. Reuse existing data, create separate empty volumes, or explicitly overwrite old Directus data.`;
      }
      if (websiteBuilderDirectusDatabaseVolume) websiteBuilderDirectusDatabaseVolume.value = contract.database_volume || "";
      if (websiteBuilderDirectusUploadsVolume) websiteBuilderDirectusUploadsVolume.value = contract.uploads_volume || "";
      if (websiteBuilderDirectusPublicPortv2) websiteBuilderDirectusPublicPortv2.value = contract.public_port || "";
      if (websiteBuilderDirectusConnectionAck) websiteBuilderDirectusConnectionAck.checked = false;
      renderWebsiteBuilderDirectusConnectionPreview("use_existing");
      updateWebsiteBuilderDirectusConnectionActions();
    }

    function closeWebsiteBuilderDirectusConnectionModal(result = null) {
      const resolver = websiteBuilderStateModel.directusConnectionModal.resolve;
      websiteBuilderStateModel.directusConnectionModal = {
        open: false,
        site: null,
        contract: null,
        context: "local_publish",
        requireDirectus: false,
        resolve: null
      };
      if (websiteBuilderDirectusConnection) websiteBuilderDirectusConnection.hidden = true;
      if (typeof resolver === "function") resolver(result);
    }

    function openWebsiteBuilderDirectusConnectionModal(site, options = {}) {
      const context = options.context || "local_publish";
      const requireDirectus = Boolean(options.requireDirectus);
      const contract = websiteBuilderDirectusContract(site, {requireDirectus});
      if (!contract || !websiteBuilderDirectusConnection) {
        return Promise.resolve(null);
      }
      return new Promise((resolve) => {
        websiteBuilderStateModel.directusConnectionModal = {
          open: true,
          site,
          contract,
          context,
          requireDirectus,
          resolve
        };
        setWebsiteBuilderDirectusConnectionFields(contract, {context});
        websiteBuilderDirectusConnection.hidden = false;
        websiteBuilderDirectusDatabaseVolume?.focus();
      });
    }

    function submitWebsiteBuilderDirectusConnection(mode) {
      if (!websiteBuilderDirectusConnectionAck?.checked) {
        setWebsiteBuilderLog("Review and confirm the Directus storage binding before publishing locally.");
        updateWebsiteBuilderDirectusConnectionActions();
        return;
      }
      try {
        const payload = websiteBuilderDirectusConnectionFields(mode);
        closeWebsiteBuilderDirectusConnectionModal(payload);
      } catch (error) {
        setWebsiteBuilderLog(`Directus connection is invalid: ${error.message}`);
        renderWebsiteBuilderDirectusConnectionPreview(mode);
      }
    }

    function submitWebsiteBuilderDirectusNewVolumes() {
      const contract = websiteBuilderStateModel.directusConnectionModal.contract || {};
      const suffix = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
      const siteId = String(contract.site_id || "site");
      if (websiteBuilderDirectusDatabaseVolume) websiteBuilderDirectusDatabaseVolume.value = `${siteId}_directus_database_${suffix}`;
      if (websiteBuilderDirectusUploadsVolume) websiteBuilderDirectusUploadsVolume.value = `${siteId}_directus_uploads_${suffix}`;
      submitWebsiteBuilderDirectusConnection("create_new");
    }

    function submitWebsiteBuilderDirectusOverwrite() {
      const contract = websiteBuilderStateModel.directusConnectionModal.contract || {};
      if (websiteBuilderDirectusDatabaseVolume) websiteBuilderDirectusDatabaseVolume.value = contract.database_volume || websiteBuilderDirectusDatabaseVolume.value || "";
      if (websiteBuilderDirectusUploadsVolume) websiteBuilderDirectusUploadsVolume.value = contract.uploads_volume || websiteBuilderDirectusUploadsVolume.value || "";
      submitWebsiteBuilderDirectusConnection("overwrite_existing");
    }

    async function websiteBuilderDirectusConnectionForLocalPublish(site, options = {}) {
      return options.directusConnection || null;
    }

    function websiteBuilderBlogDeploySummary(result) {
      const setup = result?.blog_deploy_setup;
      if (!setup || !setup.required) return "";
      const page = setup.page && typeof setup.page === "object" ? setup.page : {};
      if (!setup.ok || page.conflict) return "Blog page needs your choice before Deploy can continue.";
      if (page.created) return "Blog page created.";
      if (page.updated_page || page.overwritten) return "Blog page updated.";
      if (page.reused) return "Blog page ready.";
      return "Blog setup checked.";
    }

    async function publishWebsiteBuilderSite(lane, options = {}) {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) throw new Error("Select a website first.");
      const isPublishLane = lane === "remote_prod" || lane === "publish";
      if (lane === "dev" && !options.skipPreflight) {
        return await openWebsiteBuilderDeployPreflight(lane);
      }
      const directusConnection = lane === "local"
        ? await websiteBuilderDirectusConnectionForLocalPublish(websiteBuilderStateModel.selectedSite, options)
        : null;
      if (isPublishLane) {
        const backendSite = await refreshWebsiteBuilderSiteFromBackend(siteId);
        if (!websiteBuilderCanPublishAcceptedSetup(backendSite)) {
          throw new Error("Accept a publishing setup before publishing.");
        }
      }
      const laneLabel = websiteBuilderLaneLabel(lane);
      const actionLabel = lane === "dev" ? "Deploy" : isPublishLane ? "Publish" : "Local Server";
      setWebsiteBuilderBusy(true, `Saving and running ${actionLabel} for ${siteId}...`);
      try {
        const payload = await websiteBuilderPublishApi(siteId, lane, {directusConnection});
        const result = payload.result || payload;
        websiteBuilderStateModel.dirty = false;
        if (payload.ok) {
          const publishedSite = payload.site || result.site || null;
          if (publishedSite) {
            mergeWebsiteBuilderSite(publishedSite);
          } else {
            await selectWebsiteBuilderSite(siteId, {skipRender: true, syncRoute: false});
          }
          if (isPublishLane) {
            rememberWebsiteBuilderRemotePublishedUrl(siteId, websiteBuilderPublishResultUrl(payload, result));
          }
          const previewLane = websiteBuilderPreviewLaneFromPublishResult(result, lane);
          setWebsiteBuilderPublishedPreview(previewLane);
          updateWebsiteBuilderVisitButtons(websiteBuilderStateModel.selectedSite);
        }
        const blogSummary = lane === "dev" ? websiteBuilderBlogDeploySummary(result) : "";
        const blogSuffix = blogSummary ? ` ${blogSummary}` : "";
        setWebsiteBuilderLog(`${actionLabel} ${payload.ok ? "finished" : "failed"} for ${siteId}/${laneLabel}.${blogSuffix}`, result);
        return payload;
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    async function archiveWebsiteBuilderSite() {
      const siteId = websiteBuilderStateModel.selectedSiteId;
      if (!siteId) throw new Error("Select a website first.");
      if (siteId === "hub-site") {
        throw new Error("Hub Site is protected and cannot be archived.");
      }
      const site = websiteBuilderStateModel.selectedSite || websiteBuilderStateModel.sites.find((entry) => entry.id === siteId) || {id: siteId};
      const label = site.name || site.id;
      const confirmed = window.confirm(
        `Archive ${label} (${site.id})?\n\n` +
        `This will move runtime/websites/${site.id} out of the active website list into runtime/websites-archive, ` +
        "unregister its Local Server and Deploy services, and regenerate the local website compose file.\n\n" +
        "The archived files are kept, but this site will no longer appear in Website Builder until it is restored by a future restore workflow."
      );
      if (!confirmed) {
        setWebsiteBuilderLog(`Archive canceled for ${siteId}.`);
        return null;
      }
      setWebsiteBuilderBusy(true, `Archiving ${siteId}...`);
      try {
        const payload = await websiteBuilderApi("/api/applications/websites/site/archive", {
          method: "POST",
          body: JSON.stringify({site_id: siteId})
        });
        websiteBuilderStateModel.selectedSiteId = "";
        websiteBuilderStateModel.selectedSite = null;
        await loadWebsiteBuilderSites({selectFirst: true, replaceRoute: true});
        setWebsiteBuilderLog(`Archived ${siteId}. It is no longer shown in the active website list.`, payload.archive || payload);
        return payload;
      } finally {
        setWebsiteBuilderBusy(false);
      }
    }

    function slugFromWebsiteName(name) {
      return String(name || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 64);
    }

    async function createWebsiteBuilderSite() {
      const name = websiteBuilderNewName?.value || "";
      const siteId = websiteBuilderNewId?.value || slugFromWebsiteName(name);
      const payload = await websiteBuilderApi("/api/applications/websites/site/create", {
        method: "POST",
        body: JSON.stringify({site_id: siteId, name, kind: "static-site"})
      });
      const requestedSiteId = siteId;
      websiteBuilderStateModel.selectedSiteId = payload.site.id;
      const createMessage = requestedSiteId && payload.site.id !== requestedSiteId
        ? `Requested id ${requestedSiteId} was already reserved, so the new site was created as ${payload.site.id}.`
        : `Created ${payload.site.id}. Start with the Visual Builder, then save and publish when ready.`;
      if (websiteBuilderNewName) websiteBuilderNewName.value = "";
      if (websiteBuilderNewId) websiteBuilderNewId.value = "";
      if (websiteBuilderCreatePanel) websiteBuilderCreatePanel.hidden = true;
      await loadWebsiteBuilderSites();
      setWebsiteBuilderLog(createMessage);
    }

    function selectWebsiteBuilderWorkspaceTab(tabName) {
      const normalized = ["design", "overview", "source", "backend", "publish", "settings"].includes(tabName) ? tabName : "design";
      websiteBuilderStateModel.activeTab = normalized;
      websiteBuilderWorkspaceTabs.forEach((button) => {
        button.classList.toggle("active", button.dataset.websiteBuilderTab === normalized);
      });
      websiteBuilderWorkspacePanels.forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.websiteBuilderPanel === normalized);
      });
      updateWebsiteBuilderInspector();
      if (normalized === "design") {
        loadWebsiteBuilderGrapesFromSource({force: true});
        refreshWebsiteBuilderPreview();
      } else if (normalized === "source") {
        syncWebsiteBuilderSourceFromGrapes({markDirty: false});
      } else if (normalized === "backend") {
        renderWebsiteBuilderBackendView();
      }
    }

    function selectWebsiteBuilderSourceFile(fileName) {
      const normalized = ["html", "css", "js", "builder"].includes(fileName) ? fileName : "html";
      websiteBuilderStateModel.activeFile = normalized;
      websiteBuilderFileTabs.forEach((button) => {
        button.classList.toggle("active", button.dataset.websiteBuilderFile === normalized);
      });
      websiteBuilderEditorPanels.forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.websiteBuilderEditor === normalized);
      });
    }

    function initWebsiteBuilderApp() {
      if (!websiteBuilderApp) return;
      const routeSiteId = websiteBuilderSiteIdFromPath(window.location.pathname);
      const routeSelectionChanged = Boolean(routeSiteId && routeSiteId !== websiteBuilderStateModel.selectedSiteId);
      if (routeSiteId) {
        websiteBuilderStateModel.selectedSiteId = routeSiteId;
      }
      selectWebsiteBuilderWorkspaceTab(websiteBuilderStateModel.activeTab);
      selectWebsiteBuilderSourceFile(websiteBuilderStateModel.activeFile);
      setWebsiteBuilderPreviewDevice(websiteBuilderStateModel.previewDevice);
      renderWebsiteBuilderBackendView();
      if (!websiteBuilderStateModel.ragApplyListenerBound) {
        window.addEventListener("main-computer-chat-console-output-applied", (event) => {
          refreshWebsiteBuilderAfterRagApply(event?.detail || {}).catch((error) => setWebsiteBuilderLog(`RAG apply refresh failed: ${error.message}`));
        });
        websiteBuilderStateModel.ragApplyListenerBound = true;
      }
      if (!websiteBuilderStateModel.deploymentControllersLoaded) {
        loadWebsiteBuilderDeploymentControllers().catch((error) => setWebsiteBuilderLog(`Failed to load deployment targets: ${error.message}`));
      }
      if (!websiteBuilderStateModel.loaded) {
        loadWebsiteBuilderSites({selectFirst: !routeSiteId, replaceRoute: true}).catch((error) => setWebsiteBuilderLog(`Failed to load websites: ${error.message}`));
      } else if (routeSelectionChanged) {
        selectWebsiteBuilderSite(routeSiteId, {syncRoute: false}).catch((error) => setWebsiteBuilderLog(`Select failed: ${error.message}`));
      } else if (websiteBuilderStateModel.selectedSiteId) {
        loadWebsiteBuilderGrapesFromSource({force: false});
      }
    }

    websiteBuilderRefresh?.addEventListener("click", () => {
      loadWebsiteBuilderSites().catch((error) => setWebsiteBuilderLog(`Refresh failed: ${error.message}`));
    });
    websiteBuilderChatToggle?.addEventListener("click", () => setWebsiteBuilderChatOpen(!websiteBuilderStateModel.chatOpen));
    websiteBuilderChatClose?.addEventListener("click", () => {
      setWebsiteBuilderChatOpen(false, {mountChat: false});
      websiteBuilderChatToggle?.focus();
    });
    document.addEventListener("click", (event) => {
      if (!websiteBuilderChatPopout || websiteBuilderChatPopout.hidden) return;
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (websiteBuilderChatPopout.contains(target) || websiteBuilderChatToggle?.contains(target)) return;
      setWebsiteBuilderChatOpen(false, {mountChat: false});
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !websiteBuilderChatPopout || websiteBuilderChatPopout.hidden) return;
      setWebsiteBuilderChatOpen(false, {mountChat: false});
      websiteBuilderChatToggle?.focus();
    });
    websiteBuilderArchive?.addEventListener("click", () => {
      archiveWebsiteBuilderSite().catch((error) => setWebsiteBuilderLog(`Archive failed: ${error.message}`));
    });
    websiteBuilderSave?.addEventListener("click", () => {
      saveWebsiteBuilderSite().catch((error) => setWebsiteBuilderLog(`Save failed: ${error.message}`));
    });
    websiteBuilderPublishLocal?.addEventListener("click", () => {
      publishWebsiteBuilderSite("local").catch((error) => setWebsiteBuilderLog(`Run failed: ${error.message}`));
    });
    websiteBuilderPublishDev?.addEventListener("click", () => {
      publishWebsiteBuilderSite("dev").catch((error) => setWebsiteBuilderLog(`Run failed: ${error.message}`));
    });
    websiteBuilderPublishRemote?.addEventListener("click", () => {
      publishWebsiteBuilderSite("remote_prod").catch((error) => setWebsiteBuilderLog(`Publish failed: ${error.message}`));
    });
    websiteBuilderPublishLocalCard?.addEventListener("click", () => {
      publishWebsiteBuilderSite("local").catch((error) => setWebsiteBuilderLog(`Run failed: ${error.message}`));
    });
    websiteBuilderDirectusConnectionClose?.addEventListener("click", () => closeWebsiteBuilderDirectusConnectionModal(null));
    websiteBuilderDirectusConnectionCancel?.addEventListener("click", () => closeWebsiteBuilderDirectusConnectionModal(null));
    websiteBuilderDirectusConnectionExisting?.addEventListener("click", () => submitWebsiteBuilderDirectusConnection("use_existing"));
    websiteBuilderDirectusConnectionNew?.addEventListener("click", () => submitWebsiteBuilderDirectusNewVolumes());
    websiteBuilderDirectusConnectionOverwrite?.addEventListener("click", () => submitWebsiteBuilderDirectusOverwrite());
    websiteBuilderDirectusDatabaseVolume?.addEventListener("input", () => renderWebsiteBuilderDirectusConnectionPreview("custom"));
    websiteBuilderDirectusUploadsVolume?.addEventListener("input", () => renderWebsiteBuilderDirectusConnectionPreview("custom"));
    websiteBuilderDirectusPublicPortv2?.addEventListener("input", () => renderWebsiteBuilderDirectusConnectionPreview("custom"));
    websiteBuilderDirectusConnectionAck?.addEventListener("change", updateWebsiteBuilderDirectusConnectionActions);
    websiteBuilderPublishDevCard?.addEventListener("click", () => {
      publishWebsiteBuilderSite("dev").catch((error) => setWebsiteBuilderLog(`Run failed: ${error.message}`));
    });
    websiteBuilderDeployPreflightClose?.addEventListener("click", closeWebsiteBuilderDeployPreflight);
    websiteBuilderDeployPreflightCancel?.addEventListener("click", closeWebsiteBuilderDeployPreflight);
    websiteBuilderDeployPreflightAck?.addEventListener("change", () => {
      websiteBuilderStateModel.deployPreflight.acknowledged = Boolean(websiteBuilderDeployPreflightAck.checked);
      renderWebsiteBuilderDeployPreflight();
    });
    websiteBuilderDeployPreflightConfirm?.addEventListener("click", () => {
      const state = websiteBuilderStateModel.deployPreflight || {};
      if (!state.open) return;
      if (state.requiresAcknowledgement && !state.acknowledged) {
        setWebsiteBuilderLog("Deploy requires acknowledgement before it can continue.");
        renderWebsiteBuilderDeployPreflight();
        return;
      }
      const lane = state.lane || "dev";
      closeWebsiteBuilderDeployPreflight();
      publishWebsiteBuilderSite(lane, {skipPreflight: true}).catch((error) => setWebsiteBuilderLog(`Deploy failed: ${error.message}`));
    });
    websiteBuilderVisitLocal?.addEventListener("click", () => visitWebsiteBuilderTarget("local"));
    websiteBuilderVisitLocalCard?.addEventListener("click", () => visitWebsiteBuilderTarget("local"));
    websiteBuilderVisitDev?.addEventListener("click", () => visitWebsiteBuilderTarget("dev"));
    websiteBuilderVisitDevCard?.addEventListener("click", () => visitWebsiteBuilderTarget("dev"));
    websiteBuilderVisitRemoteProd?.addEventListener("click", () => visitWebsiteBuilderTarget("remote_prod"));
    websiteBuilderVisitRemoteProdCard?.addEventListener("click", () => visitWebsiteBuilderTarget("remote_prod"));
    websiteBuilderSaveRemoteProdTarget?.addEventListener("click", () => {
      saveWebsiteBuilderRemoteProdTarget().catch((error) => setWebsiteBuilderLog(`Accept publishing setup failed: ${error.message}`));
    });
    websiteBuilderCoolifySave?.addEventListener("click", () => {
      saveWebsiteBuilderCoolifyRemote().catch((error) => setWebsiteBuilderLog(`Save Coolify target failed: ${error.message}`));
    });
    websiteBuilderCreate?.addEventListener("click", () => {
      createWebsiteBuilderSite().catch((error) => setWebsiteBuilderLog(`Create failed: ${error.message}`));
    });
    websiteBuilderCreateToggle?.addEventListener("click", () => {
      if (!websiteBuilderCreatePanel) return;
      websiteBuilderCreatePanel.hidden = !websiteBuilderCreatePanel.hidden;
      websiteBuilderCreateToggle.textContent = websiteBuilderCreatePanel.hidden ? "+ New" : "Close";
    });
    websiteBuilderSiteSelect?.addEventListener("change", () => {
      selectWebsiteBuilderSite(websiteBuilderSiteSelect.value).catch((error) => setWebsiteBuilderLog(`Select failed: ${error.message}`));
    });
    websiteBuilderPreviewDraft?.addEventListener("click", () => {
      setWebsiteBuilderDraftPreview();
    });
    websiteBuilderPreviewLocal?.addEventListener("click", () => {
      setWebsiteBuilderPublishedPreview("local");
    });
    websiteBuilderPreviewDev?.addEventListener("click", () => {
      setWebsiteBuilderPublishedPreview("dev");
    });
    websiteBuilderPreviewRefresh?.addEventListener("click", refreshWebsiteBuilderPreview);
    websiteBuilderPreviewOpen?.addEventListener("click", openWebsiteBuilderPreview);
    websiteBuilderWorkspaceTabs.forEach((button) => {
      button.addEventListener("click", () => selectWebsiteBuilderWorkspaceTab(button.dataset.websiteBuilderTab));
    });
    websiteBuilderFileTabs.forEach((button) => {
      button.addEventListener("click", () => selectWebsiteBuilderSourceFile(button.dataset.websiteBuilderFile));
    });
    websiteBuilderBackendRuntimeButtons.forEach((button) => {
      button.addEventListener("click", () => setWebsiteBuilderBackendRuntime(button.dataset.websiteBuilderBackendRuntime));
    });
    websiteBuilderBackendProductButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const product = button.dataset.websiteBuilderBackendProduct;
        if (product === "blog") {
          openWebsiteBuilderBlogConfigureFlow().catch((error) => setWebsiteBuilderLog(`Blog setup failed: ${error.message}`));
          return;
        }
        toggleWebsiteBuilderBackendProduct(product);
      });
    });
    websiteBuilderBlogWizardClose?.addEventListener("click", closeWebsiteBuilderBlogInstallWizard);
    websiteBuilderBlogInstallConfirm?.addEventListener("click", () => {
      runWebsiteBuilderBlogInstallStack().catch((error) => setWebsiteBuilderLog(`Blog configuration failed: ${error.message}`));
    });
    websiteBuilderDeviceButtons.forEach((button) => {
      button.addEventListener("click", () => setWebsiteBuilderPreviewDevice(button.dataset.websiteBuilderDevice));
    });
    websiteBuilderHtml?.addEventListener("input", () => {
      markWebsiteBuilderDirty();
      scheduleWebsiteBuilderSourceToGrapes();
      scheduleWebsiteBuilderDraftPreview();
    });
    websiteBuilderCss?.addEventListener("input", () => {
      markWebsiteBuilderDirty();
      scheduleWebsiteBuilderSourceToGrapes();
      scheduleWebsiteBuilderDraftPreview();
    });
    websiteBuilderJs?.addEventListener("input", () => {
      markWebsiteBuilderDirty();
      scheduleWebsiteBuilderDraftPreview();
    });
    websiteBuilderState?.addEventListener("input", () => {
      markWebsiteBuilderDirty();
      renderWebsiteBuilderBackendView();
    });
    [
      websiteBuilderPublishingUseLocalServer,
      websiteBuilderPublishingSiteSlug,
      websiteBuilderPublishingSourcePath,
      websiteBuilderPublishingSshHost,
      websiteBuilderPublishingSshPassword,
      websiteBuilderPublishingRemoteRoot,
      websiteBuilderPublishingDomain,
      websiteBuilderPublishDirectusUrl
    ].forEach((input) => {
      input?.addEventListener("input", () => {
        clearWebsiteBuilderPublishingSetupAccepted();
        updateWebsiteBuilderPublishingSetupControls();
      });
    });
    websiteBuilderNewName?.addEventListener("input", () => {
      if (!websiteBuilderNewId || websiteBuilderNewId.value.trim()) return;
      websiteBuilderNewId.placeholder = slugFromWebsiteName(websiteBuilderNewName.value) || "portfolio-site";
    });
