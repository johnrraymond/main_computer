    const chatThreadStoreStorageKey = "main-computer-chat-thread-store-v2";
    const chatThreadStoreLegacyStorageKey = "main-computer-chat-console-v1";
    const chatThreadStoreSchemaVersion = 2;
    let chatThreadStoreState = null;

    function chatThreadStoreNow() {
      return new Date().toISOString();
    }

    function chatThreadStoreId(prefix = "thread") {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function chatThreadStoreCloneJson(value) {
      return JSON.parse(JSON.stringify(value || null));
    }

    function chatThreadStoreIsObject(value) {
      return Boolean(value && typeof value === "object" && !Array.isArray(value));
    }

    function chatThreadStoreSafeString(value) {
      if (value === null || value === undefined) return "";
      if (typeof value === "string") return value;
      try {
        return JSON.stringify(value);
      } catch {
        return String(value);
      }
    }

    function chatThreadStoreUniqueId(prefix, taken) {
      let id = chatThreadStoreId(prefix);
      while (taken.has(id)) id = chatThreadStoreId(prefix);
      taken.add(id);
      return id;
    }

    function chatThreadStoreNormalizeCell(cell) {
      const now = chatThreadStoreNow();
      const normalized = chatThreadStoreIsObject(cell) ? {...cell} : {};
      normalized.id = String(normalized.id || chatThreadStoreId("cell"));
      normalized.type = String(normalized.type || "ai");
      normalized.source = String(normalized.source || "");
      normalized.attachments = Array.isArray(normalized.attachments) ? normalized.attachments : [];
      normalized.status = String(normalized.status || "idle");
      if (normalized.thread_parent_output_cell_id === undefined) {
        normalized.thread_parent_output_cell_id = normalized.promoted_from?.promoted_from_output_cell_id || null;
      }
      if (normalized.thread_parent_cell_id === undefined) normalized.thread_parent_cell_id = null;
      if (normalized.type !== "output" && !Array.isArray(normalized.output_variant_ids)) normalized.output_variant_ids = [];
      if (normalized.type !== "output" && normalized.selected_output_variant_index === undefined) normalized.selected_output_variant_index = 0;
      normalized.created_at = String(normalized.created_at || now);
      normalized.updated_at = String(normalized.updated_at || normalized.created_at || now);
      return normalized;
    }

    function chatThreadStoreInferTitle(thread) {
      const title = String(thread?.title || "").trim();
      if (title && title !== "Chat Console Notebook" && title !== "New Chat") return title.slice(0, 96);
      const cells = Array.isArray(thread?.cells) ? thread.cells : [];
      for (const cell of cells) {
        const source = String(cell?.source || "").trim().replace(/\s+/g, " ");
        if (source) return source.slice(0, 60);
        const parts = Array.isArray(cell?.parts) ? cell.parts : [];
        for (const part of parts) {
          const content = String(part?.content || part?.title || "").trim().replace(/\s+/g, " ");
          if (content) return content.slice(0, 60);
        }
      }
      return title || "New Chat";
    }

    function chatThreadStoreDefaultMetadata(metadata = {}) {
      const safeMetadata = chatThreadStoreIsObject(metadata) ? {...metadata} : {};
      return {
        origin_app: safeMetadata.origin_app || "chat-console",
        linked_workbooks: Array.isArray(safeMetadata.linked_workbooks) ? safeMetadata.linked_workbooks : [],
        linked_cells: Array.isArray(safeMetadata.linked_cells) ? safeMetadata.linked_cells : [],
        tags: Array.isArray(safeMetadata.tags) ? safeMetadata.tags : [],
        ...safeMetadata
      };
    }

    function chatThreadStoreNormalizeThread(candidate = {}, fallbackTitle = "New Chat") {
      const now = chatThreadStoreNow();
      const thread = chatThreadStoreIsObject(candidate) ? {...candidate} : {};
      let cells = Array.isArray(thread.cells) ? thread.cells.map(chatThreadStoreNormalizeCell) : [];
      if (!cells.length) cells = [chatThreadStoreNormalizeCell({type: "ai", source: ""})];
      const selectedCellId = cells.some((cell) => cell.id === thread.selected_cell_id) ? thread.selected_cell_id : cells[0].id;
      thread.id = String(thread.id || chatThreadStoreId("thread"));
      thread.title = String(thread.title || fallbackTitle || "New Chat");
      thread.summary = String(thread.summary || "");
      thread.cells = cells;
      thread.selected_cell_id = selectedCellId;
      thread.last_used_input_type = String(thread.last_used_input_type || "ai");
      thread.variables = chatThreadStoreIsObject(thread.variables) ? thread.variables : {};
      thread.pinned = Boolean(thread.pinned);
      thread.archived = Boolean(thread.archived);
      thread.created_at = String(thread.created_at || now);
      thread.updated_at = String(thread.updated_at || thread.created_at || now);
      thread.metadata = chatThreadStoreDefaultMetadata(thread.metadata);
      thread.title = chatThreadStoreInferTitle(thread);
      thread.search_text = chatThreadBuildSearchText(thread);
      return thread;
    }

    function chatThreadStoreCreateBlankThread(options = {}) {
      const now = chatThreadStoreNow();
      const firstCell = chatThreadStoreNormalizeCell({type: "ai", source: ""});
      return chatThreadStoreNormalizeThread({
        id: options.id || chatThreadStoreId("thread"),
        title: options.title || "New Chat",
        summary: options.summary || "",
        cells: options.cells || [firstCell],
        selected_cell_id: options.selected_cell_id || firstCell.id,
        last_used_input_type: options.last_used_input_type || "ai",
        variables: options.variables || {},
        pinned: Boolean(options.pinned),
        archived: Boolean(options.archived),
        created_at: now,
        updated_at: now,
        metadata: chatThreadStoreDefaultMetadata(options.metadata)
      }, options.title || "New Chat");
    }

    function chatThreadStoreReadJson(storageKey) {
      try {
        return JSON.parse(localStorage.getItem(storageKey) || "null");
      } catch {
        return null;
      }
    }

    function chatThreadStoreReadLegacyNotebook() {
      const legacy = chatThreadStoreReadJson(chatThreadStoreLegacyStorageKey);
      if (!legacy || !Array.isArray(legacy.cells)) return null;
      return chatThreadStoreNormalizeThread({
        ...legacy,
        id: legacy.id && String(legacy.id).startsWith("thread-") ? legacy.id : chatThreadStoreId("thread"),
        title: legacy.title || "Imported Chat Console Notebook",
        metadata: {
          ...(chatThreadStoreIsObject(legacy.metadata) ? legacy.metadata : {}),
          origin_app: "chat-console",
          migrated_from_storage_key: chatThreadStoreLegacyStorageKey,
          migrated_from_notebook_id: legacy.id || ""
        }
      }, legacy.title || "Imported Chat Console Notebook");
    }

    function chatThreadStoreCreateDefaultState() {
      const legacyThread = chatThreadStoreReadLegacyNotebook();
      const thread = legacyThread || chatThreadStoreCreateBlankThread({title: "New Chat"});
      return {
        version: chatThreadStoreSchemaVersion,
        active_thread_id: thread.id,
        thread_order: [thread.id],
        threads: {[thread.id]: thread}
      };
    }

    function chatThreadStoreNormalizeState(state) {
      if (!chatThreadStoreIsObject(state) || !chatThreadStoreIsObject(state.threads)) {
        return chatThreadStoreCreateDefaultState();
      }
      const threads = {};
      const order = [];
      const requestedOrder = Array.isArray(state.thread_order) ? state.thread_order.map(String) : Object.keys(state.threads);
      for (const id of requestedOrder) {
        if (!state.threads[id]) continue;
        const thread = chatThreadStoreNormalizeThread({...state.threads[id], id});
        threads[thread.id] = thread;
        order.push(thread.id);
      }
      for (const [id, value] of Object.entries(state.threads)) {
        if (threads[id]) continue;
        const thread = chatThreadStoreNormalizeThread({...value, id});
        threads[thread.id] = thread;
        order.push(thread.id);
      }
      if (!order.length) return chatThreadStoreCreateDefaultState();
      const activeThreadId = threads[state.active_thread_id] ? String(state.active_thread_id) : order[0];
      return {
        version: chatThreadStoreSchemaVersion,
        active_thread_id: activeThreadId,
        thread_order: order,
        threads
      };
    }

    function chatThreadStoreLoad() {
      const stored = chatThreadStoreReadJson(chatThreadStoreStorageKey);
      chatThreadStoreState = chatThreadStoreNormalizeState(stored);
      chatThreadStoreSave(chatThreadStoreState);
      return chatThreadStoreState;
    }

    function chatThreadStoreEnsureLoaded() {
      if (!chatThreadStoreState) return chatThreadStoreLoad();
      return chatThreadStoreState;
    }

    function chatThreadStoreSave(state = chatThreadStoreState) {
      chatThreadStoreState = chatThreadStoreNormalizeState(state);
      localStorage.setItem(chatThreadStoreStorageKey, JSON.stringify(chatThreadStoreState));
      return chatThreadStoreState;
    }

    function chatThreadBuildSearchText(thread) {
      const chunks = [];
      const add = (value) => {
        const text = chatThreadStoreSafeString(value).trim();
        if (text) chunks.push(text);
      };
      add(thread?.title);
      add(thread?.summary);
      const metadata = chatThreadStoreIsObject(thread?.metadata) ? thread.metadata : {};
      add(metadata.tags);
      add(metadata.linked_workbooks);
      add(metadata.linked_cells);
      const variables = chatThreadStoreIsObject(thread?.variables) ? thread.variables : {};
      Object.entries(variables).forEach(([key, value]) => {
        add(key);
        add(value);
      });
      (Array.isArray(thread?.cells) ? thread.cells : []).forEach((cell) => {
        add(cell.id);
        add(cell.type);
        add(cell.source);
        add(cell.status);
        (Array.isArray(cell.attachments) ? cell.attachments : []).forEach((attachment) => {
          add(attachment.filename || attachment.name || "");
          add(attachment.mime_type || attachment.kind || "");
        });
        (Array.isArray(cell.parts) ? cell.parts : []).forEach((part) => {
          add(part.kind);
          add(part.title);
          add(part.language);
          add(part.content);
          (Array.isArray(part.snippets) ? part.snippets : []).forEach((snippet) => {
            add(snippet.kind);
            add(snippet.title);
            add(snippet.language);
            add(snippet.content);
          });
        });
      });
      return chunks.join("\n").toLowerCase();
    }

    function chatThreadListFunc(options = {}) {
      const state = chatThreadStoreEnsureLoaded();
      const includeArchived = Boolean(options.includeArchived);
      return state.thread_order
        .map((id) => state.threads[id])
        .filter(Boolean)
        .filter((thread) => includeArchived || !thread.archived)
        .sort((a, b) => {
          if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
          return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });
    }

    function chatThreadGet(threadId) {
      const state = chatThreadStoreEnsureLoaded();
      return state.threads[String(threadId || "")] || null;
    }

    function chatThreadGetActive() {
      const state = chatThreadStoreEnsureLoaded();
      return state.threads[state.active_thread_id] || null;
    }

    function chatThreadSetActive(threadId) {
      const state = chatThreadStoreEnsureLoaded();
      const id = String(threadId || "");
      if (!state.threads[id]) return null;
      state.active_thread_id = id;
      chatThreadStoreSave(state);
      return state.threads[id];
    }

    function chatThreadCreate(options = {}) {
      const state = chatThreadStoreEnsureLoaded();
      const thread = chatThreadStoreCreateBlankThread(options);
      state.threads[thread.id] = thread;
      state.thread_order = [thread.id, ...state.thread_order.filter((id) => id !== thread.id)];
      if (options.makeActive !== false) state.active_thread_id = thread.id;
      chatThreadStoreSave(state);
      return thread;
    }

    function chatThreadSaveThread(threadId, threadPatchOrThread = {}, options = {}) {
      const state = chatThreadStoreEnsureLoaded();
      const id = String(threadId || threadPatchOrThread?.id || "");
      if (!id) return null;
      const existing = state.threads[id] || {};
      const existingMetadata = chatThreadStoreIsObject(existing.metadata) ? existing.metadata : {};
      const patchMetadata = chatThreadStoreIsObject(threadPatchOrThread?.metadata) ? threadPatchOrThread.metadata : {};
      const merged = options.replace
        ? {...threadPatchOrThread, id}
        : {
          ...existing,
          ...threadPatchOrThread,
          id,
          metadata: {
            ...existingMetadata,
            ...patchMetadata
          }
        };
      const normalized = chatThreadStoreNormalizeThread(merged);
      normalized.updated_at = chatThreadStoreNow();
      normalized.search_text = chatThreadBuildSearchText(normalized);
      state.threads[id] = normalized;
      if (!state.thread_order.includes(id)) state.thread_order.unshift(id);
      if (options.makeActive) state.active_thread_id = id;
      chatThreadStoreSave(state);
      return normalized;
    }

    function chatThreadSaveActiveThread(thread) {
      const state = chatThreadStoreEnsureLoaded();
      const active = chatThreadGetActive();
      const threadId = String(thread?.id || active?.id || state.active_thread_id || "");
      const normalized = chatThreadSaveThread(threadId, thread, {replace: true, makeActive: true});
      return normalized;
    }

    function chatThreadRenameFunc(threadId, title) {
      const state = chatThreadStoreEnsureLoaded();
      const thread = state.threads[String(threadId || "")];
      if (!thread) return null;
      thread.title = String(title || "New Chat").trim() || "New Chat";
      thread.updated_at = chatThreadStoreNow();
      thread.search_text = chatThreadBuildSearchText(thread);
      chatThreadStoreSave(state);
      return thread;
    }

    function chatThreadStoreCloneCells(cells = []) {
      const taken = new Set();
      const idMap = new Map();
      const clonedCells = cells.map((cell) => {
        const oldId = String(cell?.id || "");
        const newId = chatThreadStoreUniqueId("cell", taken);
        if (oldId) idMap.set(oldId, newId);
        return {...chatThreadStoreCloneJson(cell), id: newId};
      });
      const remap = (value) => idMap.get(String(value || "")) || value || null;
      clonedCells.forEach((cell) => {
        cell.source_cell_id = cell.source_cell_id ? remap(cell.source_cell_id) : cell.source_cell_id;
        cell.thread_parent_output_cell_id = cell.thread_parent_output_cell_id ? remap(cell.thread_parent_output_cell_id) : cell.thread_parent_output_cell_id;
        cell.thread_parent_cell_id = cell.thread_parent_cell_id ? remap(cell.thread_parent_cell_id) : cell.thread_parent_cell_id;
        if (Array.isArray(cell.output_variant_ids)) cell.output_variant_ids = cell.output_variant_ids.map(remap).filter(Boolean);
        if (cell.promoted_from && typeof cell.promoted_from === "object") {
          cell.promoted_from = {...cell.promoted_from};
          if (cell.promoted_from.promoted_from_output_cell_id) {
            cell.promoted_from.promoted_from_output_cell_id = remap(cell.promoted_from.promoted_from_output_cell_id);
          }
        }
      });
      return {cells: clonedCells, idMap};
    }

    function chatThreadCloneFunc(threadId, options = {}) {
      const state = chatThreadStoreEnsureLoaded();
      const source = state.threads[String(threadId || "")];
      if (!source) return null;
      const {cells, idMap} = chatThreadStoreCloneCells(source.cells || []);
      const now = chatThreadStoreNow();
      const cloned = chatThreadStoreNormalizeThread({
        ...chatThreadStoreCloneJson(source),
        id: chatThreadStoreId("thread"),
        title: options.title || `${source.title || "Chat"} copy`,
        cells,
        selected_cell_id: idMap.get(String(source.selected_cell_id || "")) || cells[0]?.id || "",
        pinned: Boolean(options.pinned),
        archived: false,
        created_at: now,
        updated_at: now,
        metadata: {
          ...(chatThreadStoreIsObject(source.metadata) ? source.metadata : {}),
          cloned_from_thread_id: source.id,
          cloned_at: now
        }
      });
      state.threads[cloned.id] = cloned;
      state.thread_order = [cloned.id, ...state.thread_order];
      if (options.makeActive !== false) state.active_thread_id = cloned.id;
      chatThreadStoreSave(state);
      return cloned;
    }

    function chatThreadArchiveFunc(threadId, archived = true) {
      const state = chatThreadStoreEnsureLoaded();
      const id = String(threadId || "");
      const thread = state.threads[id];
      if (!thread) return null;
      thread.archived = Boolean(archived);
      thread.updated_at = chatThreadStoreNow();
      if (state.active_thread_id === id && thread.archived) {
        const nextId = state.thread_order.find((candidate) => candidate !== id && !state.threads[candidate]?.archived);
        if (nextId) state.active_thread_id = nextId;
      }
      chatThreadStoreSave(state);
      return thread;
    }

    function chatThreadDelete(threadId) {
      const state = chatThreadStoreEnsureLoaded();
      const id = String(threadId || "");
      if (!state.threads[id]) return false;
      delete state.threads[id];
      state.thread_order = state.thread_order.filter((candidate) => candidate !== id);
      if (!state.thread_order.length) {
        const thread = chatThreadStoreCreateBlankThread({title: "New Chat"});
        state.threads[thread.id] = thread;
        state.thread_order = [thread.id];
      }
      if (!state.threads[state.active_thread_id]) state.active_thread_id = state.thread_order[0];
      chatThreadStoreSave(state);
      return true;
    }

    function chatThreadSearchFunc(query, options = {}) {
      const text = String(query || "").trim().toLowerCase();
      if (!text) return chatThreadListFunc(options);
      return chatThreadListFunc(options).filter((thread) => {
        const haystack = thread.search_text || chatThreadBuildSearchText(thread);
        return haystack.includes(text);
      });
    }

    function chatThreadExportState() {
      return chatThreadStoreCloneJson(chatThreadStoreEnsureLoaded());
    }

    window.MainComputerChatThreads = {
      storageKey: chatThreadStoreStorageKey,
      legacyStorageKey: chatThreadStoreLegacyStorageKey,
      version: chatThreadStoreSchemaVersion,
      load: chatThreadStoreLoad,
      save: chatThreadStoreSave,
      list: chatThreadListFunc,
      search: chatThreadSearchFunc,
      create: chatThreadCreate,
      get: chatThreadGet,
      getActive: chatThreadGetActive,
      setActive: chatThreadSetActive,
      saveActiveThread: chatThreadSaveActiveThread,
      saveThread: chatThreadSaveThread,
      rename: chatThreadRenameFunc,
      clone: chatThreadCloneFunc,
      archive: chatThreadArchiveFunc,
      delete: chatThreadDelete,
      buildSearchText: chatThreadBuildSearchText,
      exportState: chatThreadExportState
    };
