    const chatConsoleStorageKey = "main-computer-chat-console-v1";
    const chatConsoleCellTypes = [
      {type: "ai", label: "AI"},
      {type: "javascript", label: "JS"},
      {type: "python", label: "Python"},
      {type: "basic", label: "BASIC"},
      {type: "calculator", label: "Calc"},
      {type: "terminal", label: "Term"},
      {type: "mathics", label: "Math"},
      {type: "comment", label: "Note"},
      {type: "output", label: "Out"}
    ];
    const chatConsoleInputCellTypes = chatConsoleCellTypes.filter((item) => item.type !== "output");
    const chatConsoleOutputCellTypes = chatConsoleCellTypes.filter((item) => item.type === "output");
    const chatConsoleInputTypes = new Set(["ai", "javascript", "python", "basic", "calculator", "terminal", "mathics", "comment"]);
    const chatConsoleCodeCellTypes = new Set(["javascript", "python", "basic"]);
    let chatConsoleInitialized = false;
    let chatConsoleState = null;
    function chatConsoleNow() {
      return new Date().toISOString();
    }
    function chatConsoleId(prefix = "cell") {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }
    function chatConsoleSourceHash(source) {
      let hash = 0;
      const text = String(source || "");
      for (let index = 0; index < text.length; index += 1) hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
      return Math.abs(hash).toString(36);
    }
    function createChatConsoleCell(type = "ai", source = "", extra = {}) {
      const now = chatConsoleNow();
      return {
        id: chatConsoleId("cell"),
        type,
        source,
        attachments: [],
        status: type === "terminal" && extra.reviewRequired ? "review-required" : "idle",
        output_variant_ids: [],
        selected_output_variant_index: 0,
        thread_parent_output_cell_id: null,
        thread_parent_cell_id: null,
        promoted_from: null,
        rag_assisted_thinking: false,
        rag_assisted_thinking_options: {
          think: "low"
        },
        created_at: now,
        updated_at: now,
        ...extra
      };
    }
    function createChatConsoleNotebook() {
      const now = chatConsoleNow();
      const firstCell = createChatConsoleCell("ai");
      return {
        id: chatConsoleId("notebook"),
        title: "Chat Console Notebook",
        cells: [firstCell],
        selected_cell_id: firstCell.id,
        last_used_input_type: "ai",
        variables: {},
        created_at: now,
        updated_at: now
      };
    }
    function chatConsoleThreadStoreApi() {
      return window.MainComputerChatThreads || null;
    }
    function loadChatConsoleState() {
      const threadStore = chatConsoleThreadStoreApi();
      if (threadStore?.load && threadStore?.getActive) {
        threadStore.load();
        const activeThread = threadStore.getActive();
        if (activeThread && Array.isArray(activeThread.cells)) {
          return migrateChatConsoleState(JSON.parse(JSON.stringify(activeThread)));
        }
      }
      try {
        const stored = JSON.parse(localStorage.getItem(chatConsoleStorageKey) || "null");
        if (stored && Array.isArray(stored.cells)) return migrateChatConsoleState(stored);
      } catch {
        // fall through to fresh notebook
      }
      return createChatConsoleNotebook();
    }
    function migrateChatConsoleState(state) {
      if (!state.variables || typeof state.variables !== "object" || Array.isArray(state.variables)) {
        state.variables = {};
      }
      state.cells = (state.cells || []).map((cell) => {
        if (cell.thread_parent_output_cell_id === undefined) {
          cell.thread_parent_output_cell_id = cell.promoted_from?.promoted_from_output_cell_id || null;
        }
        if (cell.thread_parent_cell_id === undefined) {
          cell.thread_parent_cell_id = null;
        }
        if (cell.type !== "output" && !Array.isArray(cell.output_variant_ids)) {
          cell.output_variant_ids = [];
        }
        if (cell.type !== "output" && cell.selected_output_variant_index === undefined) {
          cell.selected_output_variant_index = 0;
        }
        if (cell.type !== "output" && cell.rag_assisted_thinking === undefined) {
          cell.rag_assisted_thinking = false;
        }
        if (cell.type !== "output" && (!cell.rag_assisted_thinking_options || typeof cell.rag_assisted_thinking_options !== "object" || Array.isArray(cell.rag_assisted_thinking_options))) {
          cell.rag_assisted_thinking_options = {think: "low"};
        }
        if (cell.type !== "output" && !cell.rag_assisted_thinking_options.think) {
          cell.rag_assisted_thinking_options.think = "medium";
        }
        if (cell.type !== "output" && Object.prototype.hasOwnProperty.call(cell.rag_assisted_thinking_options, "docker_enabled")) {
          delete cell.rag_assisted_thinking_options.docker_enabled;
        }
        return cell;
      });
      return state;
    }
    function saveChatConsoleState(message = "thread saved") {
      if (!chatConsoleState) return;
      chatConsoleState.updated_at = chatConsoleNow();
      const threadStore = chatConsoleThreadStoreApi();
      if (threadStore?.saveActiveThread) {
        const activeThread = threadStore.getActive?.();
        if (activeThread?.id && !threadStore.get?.(chatConsoleState.id)) {
          chatConsoleState.id = activeThread.id;
        }
        chatConsoleState = threadStore.saveActiveThread(chatConsoleState) || chatConsoleState;
      }
      localStorage.setItem(chatConsoleStorageKey, JSON.stringify(chatConsoleState));
      if (typeof chatConsoleSetStatus === "function") chatConsoleSetStatus(message);
      else if (chatConsoleSaveStatus) chatConsoleSaveStatus.textContent = message;
      if (typeof renderChatConsoleThreadSelector === "function") renderChatConsoleThreadSelector();
    }
