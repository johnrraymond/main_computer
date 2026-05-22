    (function () {
      const apiName = "MainComputerChatThreadController";

      function controllerStore(options = {}) {
        return options.threadStore || window.MainComputerChatThreads || null;
      }

      function controllerClone(value) {
        try {
          return JSON.parse(JSON.stringify(value || null));
        } catch {
          return value || null;
        }
      }

      function controllerDateLabel(value) {
        const text = String(value || "");
        if (!text) return "unknown time";
        const date = new Date(text);
        if (Number.isNaN(date.getTime())) return text.slice(0, 16);
        return date.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit"
        });
      }

      function controllerThreadTitle(thread) {
        return String(thread?.title || "New Chat").trim() || "New Chat";
      }

      function controllerThreadPreview(thread) {
        const cells = Array.isArray(thread?.cells) ? thread.cells : [];
        for (const cell of cells) {
          const source = String(cell?.source || "").trim().replace(/\s+/g, " ");
          if (source) return source.slice(0, 110);
          const parts = Array.isArray(cell?.parts) ? cell.parts : [];
          for (const part of parts) {
            const content = String(part?.content || part?.title || "").trim().replace(/\s+/g, " ");
            if (content) return content.slice(0, 110);
          }
        }
        return "Empty chat";
      }

      function controllerThreadMeta(thread) {
        const cells = Array.isArray(thread?.cells) ? thread.cells : [];
        const inputCount = cells.filter((cell) => cell.type !== "output").length;
        const outputCount = cells.filter((cell) => cell.type === "output").length;
        const tags = [];
        if (thread?.pinned) tags.push("pinned");
        if (thread?.archived) tags.push("archived");
        if (thread?.metadata?.linked_workbooks?.length) tags.push("spreadsheet-linked");
        const counts = `${inputCount} input${inputCount === 1 ? "" : "s"} · ${outputCount} output${outputCount === 1 ? "" : "s"}`;
        return `${counts} · updated ${controllerDateLabel(thread?.updated_at)}${tags.length ? ` · ${tags.join(" · ")}` : ""}`;
      }

      function controllerGetElement(root, selector, fallback = document) {
        if (!selector) return null;
        return root?.querySelector?.(selector) || fallback?.querySelector?.(selector) || null;
      }

      function controllerDefaultThreadLink(threadId) {
        const url = new URL(window.location.href);
        url.searchParams.set("thread", threadId);
        return url.toString();
      }

      function controllerCopyThreadLink(thread, options = {}) {
        if (!thread?.id) return Promise.resolve(false);
        const href = options.buildThreadLink ? options.buildThreadLink(thread) : controllerDefaultThreadLink(thread.id);
        if (navigator.clipboard?.writeText) {
          return navigator.clipboard.writeText(href).then(() => true);
        }
        if (window.history?.replaceState) {
          window.history.replaceState(null, "", href);
        }
        return Promise.resolve(false);
      }

      function controllerMount(root, options = {}) {
        const host = root || document;
        const store = controllerStore(options);
        const elements = {
          newButton: options.elements?.newButton || controllerGetElement(host, "[data-chat-thread-new], #chat-thread-new"),
          searchInput: options.elements?.searchInput || controllerGetElement(host, "[data-chat-thread-search], #chat-thread-search"),
          list: options.elements?.list || controllerGetElement(host, "[data-chat-thread-list], #chat-thread-list"),
          activeTitle: options.elements?.activeTitle || controllerGetElement(host, "[data-chat-thread-active-title], #chat-thread-active-title"),
          activeMeta: options.elements?.activeMeta || controllerGetElement(host, "[data-chat-thread-active-meta], #chat-thread-active-meta"),
          renameButton: options.elements?.renameButton || controllerGetElement(host, "[data-chat-thread-rename], #chat-thread-rename"),
          cloneButton: options.elements?.cloneButton || controllerGetElement(host, "[data-chat-thread-clone], #chat-thread-clone"),
          archiveButton: options.elements?.archiveButton || controllerGetElement(host, "[data-chat-thread-archive], #chat-thread-archive"),
          copyLinkButton: options.elements?.copyLinkButton || controllerGetElement(host, "[data-chat-thread-copy-link], #chat-thread-copy-link")
        };
        const listeners = [];

        function addListener(element, eventName, handler) {
          if (!element) return;
          element.addEventListener(eventName, handler);
          listeners.push(() => element.removeEventListener(eventName, handler));
        }

        function status(message) {
          if (options.status) options.status(message);
        }

        function getActiveThreadId() {
          if (options.getActiveThreadId) return options.getActiveThreadId() || "";
          return store?.getActive?.()?.id || "";
        }

        function getActiveThread() {
          if (options.getActiveThread) return options.getActiveThread() || null;
          const activeId = getActiveThreadId();
          return (activeId && store?.get?.(activeId)) || store?.getActive?.() || null;
        }

        function listThreads(query = "") {
          if (!store) return [];
          const listOptions = options.listOptions || {};
          if (query && store.search) return store.search(query, listOptions);
          if (store.list) return store.list(listOptions);
          return [];
        }

        function render() {
          if (!store?.list || !elements.list) return;
          const query = elements.searchInput?.value || "";
          const activeThread = getActiveThread();
          const activeThreadId = activeThread?.id || getActiveThreadId();
          const threads = listThreads(query);
          elements.list.innerHTML = "";
          if (elements.activeTitle) elements.activeTitle.textContent = controllerThreadTitle(activeThread);
          if (elements.activeMeta) elements.activeMeta.textContent = activeThread ? controllerThreadMeta(activeThread) : "No active thread";
          [elements.renameButton, elements.cloneButton, elements.archiveButton, elements.copyLinkButton].forEach((button) => {
            if (button) button.disabled = !activeThread;
          });
          if (!threads.length) {
            const empty = document.createElement("div");
            empty.className = "chat-thread-empty";
            empty.textContent = query ? "No matching chats." : "No saved chats yet.";
            elements.list.append(empty);
            return;
          }
          threads.forEach((thread) => {
            const row = document.createElement("button");
            row.type = "button";
            row.className = `chat-thread-row${thread.id === activeThreadId ? " active" : ""}`;
            row.dataset.chatThreadId = thread.id;
            row.setAttribute("role", "listitem");
            const title = document.createElement("strong");
            title.textContent = controllerThreadTitle(thread);
            const meta = document.createElement("span");
            meta.className = "chat-thread-row-meta";
            meta.textContent = controllerThreadMeta(thread);
            const preview = document.createElement("span");
            preview.className = "chat-thread-row-preview";
            preview.textContent = controllerThreadPreview(thread);
            row.append(title, meta, preview);
            row.addEventListener("click", () => select(thread.id));
            elements.list.append(row);
          });
        }

        function setHostActiveThread(thread, context = {}) {
          if (!thread?.id) return null;
          if (options.setActiveThreadId) {
            const result = options.setActiveThreadId(thread.id, thread, context);
            return result || thread;
          }
          if (store?.setActive) return store.setActive(thread.id) || thread;
          return thread;
        }

        function select(threadId, context = {}) {
          if (!store?.get || !threadId) return null;
          const currentId = getActiveThreadId();
          if (currentId === threadId) {
            render();
            return getActiveThread();
          }
          if (options.beforeThreadChange) options.beforeThreadChange(threadId, context);
          const thread = store.get(threadId);
          if (!thread) {
            status("thread not found");
            return null;
          }
          const active = setHostActiveThread(thread, context) || thread;
          if (options.afterThreadChange) options.afterThreadChange(active, {reason: context.reason || "select", ...context});
          render();
          status(context.message || "thread loaded");
          return active;
        }

        function create(context = {}) {
          if (!store?.create) return null;
          if (options.beforeThreadChange) options.beforeThreadChange("", {reason: "create", ...context});
          const createOptions = {
            title: "New Chat",
            ...(options.createThreadOptions ? options.createThreadOptions(context) : {}),
            ...(context.options || {})
          };
          const thread = store.create(createOptions);
          const active = setHostActiveThread(thread, {reason: "create", ...context}) || thread;
          if (options.afterThreadChange) options.afterThreadChange(active, {reason: "create", ...context});
          render();
          status(context.message || "new chat ready");
          if (options.focusAfterNew) window.setTimeout(options.focusAfterNew, 0);
          return active;
        }

        function rename(context = {}) {
          const activeThread = getActiveThread();
          if (!store?.rename || !activeThread) return null;
          const promptFn = options.promptRename || ((thread) => prompt("Rename this chat thread:", controllerThreadTitle(thread)));
          const title = promptFn(activeThread);
          if (title === null || title === undefined) return null;
          const renamed = store.rename(activeThread.id, title);
          if (renamed && options.afterThreadChange) options.afterThreadChange(renamed, {reason: "rename", ...context});
          render();
          if (renamed) status(context.message || "thread renamed");
          return renamed;
        }

        function clone(context = {}) {
          const activeThread = getActiveThread();
          if (!store?.clone || !activeThread) return null;
          if (options.beforeThreadChange) options.beforeThreadChange(activeThread.id, {reason: "clone", ...context});
          const cloneOptions = {
            ...(options.cloneThreadOptions ? options.cloneThreadOptions(activeThread, context) : {}),
            ...(context.options || {})
          };
          const cloned = store.clone(activeThread.id, cloneOptions);
          if (!cloned) return null;
          const active = setHostActiveThread(cloned, {reason: "clone", ...context}) || cloned;
          if (options.afterThreadChange) options.afterThreadChange(active, {reason: "clone", ...context});
          render();
          status(context.message || "thread cloned");
          return active;
        }

        function archive(context = {}) {
          const activeThread = getActiveThread();
          if (!store?.archive || !activeThread) return null;
          const confirmFn = options.confirmArchive || ((thread) => confirm(`Archive "${controllerThreadTitle(thread)}"?`));
          if (!confirmFn(activeThread)) return null;
          store.archive(activeThread.id, true);
          let next = listThreads("")[0] || null;
          if (!next && store.create) next = store.create({title: "New Chat"});
          let active = next;
          if (next) active = setHostActiveThread(next, {reason: "archive", archivedThreadId: activeThread.id, ...context}) || next;
          if (options.afterThreadChange && active) options.afterThreadChange(active, {reason: "archive", archivedThreadId: activeThread.id, ...context});
          render();
          status(context.message || "thread archived");
          return active;
        }

        function copyLink(context = {}) {
          const activeThread = getActiveThread();
          if (!activeThread?.id) return Promise.resolve(false);
          const copy = options.copyThreadLink || controllerCopyThreadLink;
          return Promise.resolve(copy(activeThread, options)).then((ok) => {
            status(ok === false ? "thread link placed in URL" : "thread link copied");
            return ok;
          }).catch((error) => {
            status(error?.message || "thread link copy failed");
            throw error;
          });
        }

        addListener(elements.newButton, "click", () => create());
        addListener(elements.searchInput, "input", render);
        addListener(elements.renameButton, "click", () => rename());
        addListener(elements.cloneButton, "click", () => clone());
        addListener(elements.archiveButton, "click", () => archive());
        addListener(elements.copyLinkButton, "click", () => {
          copyLink().catch(() => {});
        });

        render();

        return {
          render,
          select,
          create,
          rename,
          clone,
          archive,
          copyLink,
          getActiveThread,
          destroy() {
            while (listeners.length) listeners.pop()();
          }
        };
      }

      window[apiName] = {
        mount: controllerMount,
        threadTitle: controllerThreadTitle,
        threadMeta: controllerThreadMeta,
        threadPreview: controllerThreadPreview,
        threadDateLabel: controllerDateLabel
      };
    })();
