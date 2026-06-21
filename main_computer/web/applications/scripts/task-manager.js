function taskManagerMcelFlagValue(search = window.location.search) {
  try {
    return (new URLSearchParams(String(search || "")).get("mcel") || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

function taskManagerMcelStorageFlag(key) {
  try {
    return String(localStorage.getItem(key) || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

const taskManagerMcelEnableValues = new Set(["1", "true", "on", "yes", "enabled"]);
const taskManagerMcelDisableValues = new Set(["0", "false", "off", "no", "disabled"]);
let taskManagerMcelUrlSessionEnabled = false;
let taskManagerMcelLastReport = null;
let taskManagerMcelApplyScheduled = false;

function taskManagerMcelAppEnabled() {
  const queryValue = taskManagerMcelFlagValue();
  if (taskManagerMcelDisableValues.has(queryValue)) {
    taskManagerMcelUrlSessionEnabled = false;
    return false;
  }
  if (taskManagerMcelEnableValues.has(queryValue)) {
    taskManagerMcelUrlSessionEnabled = true;
  }

  const disabledValue = taskManagerMcelStorageFlag("taskManagerMcelDisabled");
  if (taskManagerMcelEnableValues.has(disabledValue)) {
    return false;
  }

  return true;
}

function applyTaskManagerMcelAppSemantics(reason = "app-refresh") {
  if (!taskManagerMcelAppEnabled()) {
    return null;
  }
  const adapter = window.TaskManagerMcel;
  if (typeof adapter?.applyTaskManagerMcelSemantics !== "function") {
    return null;
  }
  try {
    taskManagerMcelLastReport = adapter.applyTaskManagerMcelSemantics({
      document,
      rootSelector: "#task-manager-app",
      route: `${window.location.pathname}${window.location.search}${window.location.hash}`,
      mode: "app",
      reason,
      report: false
    });
    window.taskManagerMcelLastReport = taskManagerMcelLastReport;
    taskManagerApp?.setAttribute?.("data-task-manager-mcel-mode", "passive");
    return taskManagerMcelLastReport;
  } catch (error) {
    console.warn("Task Manager MCEL enrichment failed:", error);
    return null;
  }
}

function scheduleTaskManagerMcelAppSemantics(reason = "app-refresh") {
  if (!taskManagerMcelAppEnabled() || taskManagerMcelApplyScheduled) {
    return;
  }
  taskManagerMcelApplyScheduled = true;
  const raf = typeof window.requestAnimationFrame === "function"
    ? window.requestAnimationFrame.bind(window)
    : (callback) => window.setTimeout(callback, 16);
  raf(() => {
    taskManagerMcelApplyScheduled = false;
    applyTaskManagerMcelAppSemantics(reason);
  });
}

window.taskManagerMcelStatus = function taskManagerMcelStatus() {
  return {
    enabled: taskManagerMcelAppEnabled(),
    sessionEnabled: taskManagerMcelUrlSessionEnabled,
    adapterAvailable: typeof window.TaskManagerMcel?.applyTaskManagerMcelSemantics === "function",
    lastReport: taskManagerMcelLastReport
  };
};


function initTaskManagerApp() {
    if (!taskManagerInitialized) {
      taskManagerInitialized = true;
      taskNotebookTabButtons.forEach((button) => {
        button.addEventListener("click", () => {
          setTaskNotebookTab(button.dataset.taskTab || "server-processes");
        });
      });
    setTaskNotebookTab(taskNotebookTabFromPath(window.location.pathname), {replaceRoute: true});
    const nextStepNode = typeof gitProjectNextStep === "undefined" ? null : gitProjectNextStep;
    if (nextStepNode) {
      nextStepNode.addEventListener("click", (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const button = target?.closest("button[data-git-repo-boundary-action='open']");
        if (!button) return;
        event.preventDefault();
        openGitProjectRepoBoundaryModal(gitProjectLastInspection);
      });
    }
    taskRefresh.addEventListener("click", () => refreshTaskManager().catch(() => null));
    if (taskServerStatus) taskServerStatus.addEventListener("click", () => runTaskAction("server_status", {}, false));
    if (taskServerShutdown) taskServerShutdown.addEventListener("click", () => runTaskAction("server_shutdown", {}, true));
    if (taskServerStart) taskServerStart.addEventListener("click", () => runTaskAction("server_start", {}, true));
    if (taskServerRestart) taskServerRestart.addEventListener("click", () => runTaskAction("server_restart", {}, true));
    taskScheduleCreate.addEventListener("click", createTaskSchedule);
    taskSchedulesRefresh.addEventListener("click", () => refreshTaskManager().catch(() => null));
    taskAiAnalyze.addEventListener("click", askTaskManagerAi);
    taskAutoRefresh.addEventListener("change", scheduleTaskManagerAutoRefresh);
    taskQuery.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        refreshTaskManager();
      }
    });
    [taskProcessTable, taskAllProcessTable].filter(Boolean).forEach((table) => {
      table.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-task-action]");
        if (!button) return;
        const pid = Number(button.dataset.taskPid || 0);
        if (!pid) return;
        const action = button.dataset.taskAction === "kill" ? "kill_pid" : "terminate_pid";
        runTaskAction(action, {pid}, true);
      });
    });
    taskScheduleList.addEventListener("click", (event) => {
      const deleteButton = event.target.closest("button[data-task-delete]");
      if (deleteButton) {
        deleteTaskSchedule(deleteButton.dataset.taskDelete || "");
        return;
      }
      const runButton = event.target.closest("button[data-task-run]");
      if (runButton) {
        const action = runButton.dataset.taskActionName || "server_status";
        runTaskAction(action, {}, true);
      }
    });
    if (!taskScheduleWhen.value) {
      const now = new Date(Date.now() + 10 * 60 * 1000);
      taskScheduleWhen.value = now.toISOString().slice(0, 16);
    }
  }
  scheduleTaskManagerAutoRefresh();
  updateTaskManagerWidgetTickers(taskManagerSnapshotCache, "Task manager awaiting first snapshot.", "Task manager ready");
  updateTaskAiTicker(taskAiOutput.textContent);
  scheduleTaskManagerMcelAppSemantics("init");
  refreshTaskManager().catch(() => null);
}
