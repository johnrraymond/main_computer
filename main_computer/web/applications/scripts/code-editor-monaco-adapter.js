(() => {
  const GLOBAL_NAME = "MainComputerMonacoAdapter";
  const LOCAL_VS_BASE = "applications/vendor/monaco-editor/min/vs";
  const CDN_VS_BASE = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs";
  const EDITOR_URI_SCHEME = "inmemory://code-studio-runtime/";
  const BLOCKED_MOBILE_RE = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i;

  let loadPromise = null;
  let activeSession = null;

  function now() {
    return new Date().toISOString();
  }

  function emitReceipt(onReceipt, receipt) {
    const normalized = {
      kind: "mcel-code-studio-monaco-runtime-receipt",
      generatedAt: now(),
      actionOutcome: receipt.actionOutcome || "pass",
      externalOutcome: receipt.externalOutcome || "unknown",
      governanceOutcome: receipt.governanceOutcome || "pass",
      safetyOutcome: receipt.safetyOutcome || "pass",
      nextAction: receipt.nextAction || "inspect Monaco receipt",
      ...receipt
    };
    try {
      if (typeof onReceipt === "function") onReceipt(normalized);
    } catch (error) {
      // Receipts must never break editor recovery.
    }
    return normalized;
  }

  function unsupportedReason() {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return "dom-unavailable";
    }
    if (BLOCKED_MOBILE_RE.test(String(window.navigator?.userAgent || ""))) {
      return "mobile-browser-unsupported";
    }
    if (window.location?.protocol === "file:") {
      return "file-protocol-workers-unsupported";
    }
    return "";
  }

  function scriptExists(src) {
    return Array.from(document.scripts || []).some((script) => script.src === src || script.getAttribute("src") === src);
  }

  function appendScript(src) {
    return new Promise((resolve, reject) => {
      if (scriptExists(src)) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.dataset.codeStudioMonacoLoader = "true";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Unable to load Monaco loader: ${src}`));
      document.head.append(script);
    });
  }

  function workerPath(label) {
    if (label === "json") return "language/json/json.worker.js";
    if (label === "css" || label === "scss" || label === "less") return "language/css/css.worker.js";
    if (label === "html" || label === "handlebars" || label === "razor") return "language/html/html.worker.js";
    if (label === "typescript" || label === "javascript") return "language/typescript/ts.worker.js";
    return "editor/editor.worker.js";
  }

  function configureWorkers(vsBase) {
    if (!window.MonacoEnvironment) window.MonacoEnvironment = {};
    if (typeof window.MonacoEnvironment.getWorkerUrl !== "function") {
      window.MonacoEnvironment.getWorkerUrl = (_moduleId, label) => `${vsBase}/${workerPath(label)}`;
    }
  }

  function loadViaAmd(vsBase) {
    configureWorkers(vsBase);
    const loaderSrc = `${vsBase}/loader.js`;
    return appendScript(loaderSrc).then(() => new Promise((resolve, reject) => {
      const amdRequire = window.require;
      if (typeof amdRequire !== "function") {
        reject(new Error("Monaco AMD loader did not expose window.require"));
        return;
      }
      if (typeof amdRequire.config === "function") {
        amdRequire.config({paths: {vs: vsBase}});
      }
      amdRequire(["vs/editor/editor.main"], () => {
        if (window.monaco?.editor) {
          resolve({
            monaco: window.monaco,
            source: vsBase === LOCAL_VS_BASE ? "local-vendor" : "cdn"
          });
          return;
        }
        reject(new Error("Monaco editor.main loaded without window.monaco.editor"));
      }, (error) => {
        reject(error instanceof Error ? error : new Error(String(error || "Monaco editor.main failed")));
      });
    }));
  }

  function load(options = {}) {
    if (window.monaco?.editor) {
      return Promise.resolve({
        ok: true,
        actionOutcome: "pass",
        externalOutcome: "already-loaded",
        source: "window.monaco"
      });
    }

    const reason = unsupportedReason();
    if (reason) {
      return Promise.resolve({
        ok: false,
        actionOutcome: "blocked",
        externalOutcome: reason,
        source: "none"
      });
    }

    if (loadPromise) return loadPromise;

    const allowCdn = options.allowCdn !== false;
    loadPromise = loadViaAmd(LOCAL_VS_BASE)
      .catch((localError) => {
        if (!allowCdn) throw localError;
        return loadViaAmd(CDN_VS_BASE).catch((cdnError) => {
          const error = new Error(`${localError.message}; ${cdnError.message}`);
          error.localError = localError;
          error.cdnError = cdnError;
          throw error;
        });
      })
      .then((loaded) => ({
        ok: true,
        actionOutcome: "pass",
        externalOutcome: "monaco-loaded",
        source: loaded.source
      }))
      .catch((error) => ({
        ok: false,
        actionOutcome: "exception",
        externalOutcome: "loader-exception",
        source: "none",
        message: error?.message || String(error)
      }));

    return loadPromise;
  }

  function normalizeLanguage(language, path) {
    const explicit = String(language || "").trim().toLowerCase();
    if (explicit) {
      const aliases = {
        js: "javascript",
        jsx: "javascript",
        ts: "typescript",
        tsx: "typescript",
        py: "python",
        md: "markdown",
        yml: "yaml",
        htm: "html"
      };
      return aliases[explicit] || explicit;
    }
    const suffix = String(path || "").split(".").pop()?.toLowerCase() || "";
    const byExtension = {
      css: "css",
      html: "html",
      js: "javascript",
      json: "json",
      md: "markdown",
      py: "python",
      ts: "typescript",
      txt: "plaintext",
      yml: "yaml",
      yaml: "yaml"
    };
    return byExtension[suffix] || "plaintext";
  }

  function uriForPath(path) {
    const safePath = String(path || "untitled.txt")
      .replace(/\\/g, "/")
      .replace(/^\/+/, "")
      .replace(/\.\./g, "__");
    return window.monaco.Uri.parse(`${EDITOR_URI_SCHEME}${encodeURI(safePath)}`);
  }

  function disposeActive(reason = "replace") {
    if (!activeSession) {
      return {
        ok: true,
        actionOutcome: "blocked",
        externalOutcome: "not-mounted",
        reason
      };
    }

    const session = activeSession;
    activeSession = null;
    session.subscriptions.forEach((subscription) => {
      try {
        subscription.dispose();
      } catch (error) {
        // Disposal is best-effort; source state is not affected.
      }
    });
    try {
      session.editor.dispose();
    } catch (error) {
      return {
        ok: false,
        actionOutcome: "exception",
        externalOutcome: "dispose-exception",
        reason,
        message: error?.message || String(error)
      };
    }
    return {
      ok: true,
      actionOutcome: "pass",
      externalOutcome: "monaco-disposed",
      reason,
      path: session.path
    };
  }

  function mount(options = {}) {
    const host = options.host;
    const onReceipt = options.onReceipt;

    if (!host) {
      return Promise.resolve(emitReceipt(onReceipt, {
        effect: "editor.monaco.mount",
        ok: false,
        actionOutcome: "blocked",
        externalOutcome: "missing-host",
        nextAction: "render fallback textarea"
      }));
    }

    host.dataset.monacoOutcome = "loading";
    host.textContent = "Loading Monaco editor runtime…";

    return load(options).then((loaded) => {
      emitReceipt(onReceipt, {
        effect: "editor.monaco.load",
        ok: loaded.ok,
        actionOutcome: loaded.actionOutcome,
        externalOutcome: loaded.externalOutcome,
        source: loaded.source,
        message: loaded.message || "",
        nextAction: loaded.ok ? "mount editor model" : "use fallback textarea"
      });

      if (!loaded.ok) {
        host.dataset.monacoOutcome = loaded.actionOutcome || "blocked";
        host.textContent = `Monaco unavailable (${loaded.externalOutcome || "blocked"}). Fallback textarea remains active.`;
        return emitReceipt(onReceipt, {
          effect: "editor.monaco.mount",
          ok: false,
          actionOutcome: loaded.actionOutcome || "blocked",
          externalOutcome: loaded.externalOutcome || "loader-blocked",
          source: loaded.source || "none",
          message: loaded.message || "",
          nextAction: "use fallback textarea"
        });
      }

      try {
        disposeActive("remount");
        host.textContent = "";
        const monaco = window.monaco;
        const path = String(options.path || "untitled.txt");
        const language = normalizeLanguage(options.language, path);
        const value = String(options.value ?? "");
        const uri = uriForPath(path);
        const existingModel = monaco.editor.getModel(uri);
        const model = existingModel || monaco.editor.createModel(value, language, uri);
        if (existingModel && existingModel.getValue() !== value) existingModel.setValue(value);

        const editor = monaco.editor.create(host, {
          model,
          automaticLayout: true,
          minimap: {enabled: false},
          scrollBeyondLastLine: false,
          theme: "vs-dark",
          wordWrap: "on"
        });
        const subscriptions = [
          model.onDidChangeContent(() => {
            const text = model.getValue();
            if (typeof options.onChange === "function") options.onChange(text);
          })
        ];

        activeSession = {
          editor,
          model,
          path,
          language,
          subscriptions
        };
        host.dataset.monacoOutcome = "pass";
        host.dataset.monacoLanguage = language;

        const layoutReceipt = emitReceipt(onReceipt, {
          effect: "editor.monaco.layoutObserved",
          ok: true,
          actionOutcome: "pass",
          externalOutcome: "layout-observed",
          path,
          language,
          nextAction: "edit draft"
        });
        editor.layout();

        return emitReceipt(onReceipt, {
          effect: "editor.monaco.mount",
          ok: true,
          actionOutcome: "pass",
          externalOutcome: "model-mounted",
          source: loaded.source,
          path,
          language,
          layoutReceipt,
          nextAction: "edit draft"
        });
      } catch (error) {
        host.dataset.monacoOutcome = "exception";
        host.textContent = "Monaco raised an exception. Fallback textarea remains active.";
        return emitReceipt(onReceipt, {
          effect: "editor.monaco.mount",
          ok: false,
          actionOutcome: "exception",
          externalOutcome: "mount-exception",
          message: error?.message || String(error),
          nextAction: "inspect exception"
        });
      }
    });
  }

  function setModel(options = {}) {
    if (!activeSession || !window.monaco?.editor) {
      return {
        ok: false,
        actionOutcome: "blocked",
        externalOutcome: "not-mounted"
      };
    }
    const path = String(options.path || activeSession.path || "untitled.txt");
    const language = normalizeLanguage(options.language, path);
    const value = String(options.value ?? "");
    const uri = uriForPath(path);
    let model = window.monaco.editor.getModel(uri);
    if (!model) model = window.monaco.editor.createModel(value, language, uri);
    if (model.getValue() !== value) model.setValue(value);
    activeSession.editor.setModel(model);
    activeSession.model = model;
    activeSession.path = path;
    activeSession.language = language;
    return {
      ok: true,
      actionOutcome: "pass",
      externalOutcome: "model-updated",
      path,
      language
    };
  }

  function getValue() {
    if (!activeSession?.model) return null;
    return activeSession.model.getValue();
  }

  function layout() {
    if (!activeSession?.editor) {
      return {
        ok: false,
        actionOutcome: "blocked",
        externalOutcome: "not-mounted"
      };
    }
    try {
      activeSession.editor.layout();
      return {
        ok: true,
        actionOutcome: "pass",
        externalOutcome: "layout-observed",
        path: activeSession.path
      };
    } catch (error) {
      return {
        ok: false,
        actionOutcome: "exception",
        externalOutcome: "layout-exception",
        message: error?.message || String(error),
        path: activeSession.path
      };
    }
  }

  function dispose(reason = "manual") {
    return disposeActive(reason);
  }

  window[GLOBAL_NAME] = Object.freeze({
    version: "1.0.0",
    localVsBase: LOCAL_VS_BASE,
    cdnVsBase: CDN_VS_BASE,
    load,
    mount,
    setModel,
    getValue,
    layout,
    dispose,
    normalizeLanguage
  });
})();
