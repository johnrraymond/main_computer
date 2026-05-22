    let fileMapMarked = new Set(JSON.parse(localStorage.getItem("main-computer-aider-map-files-v1") || "[]"));
    let aiderTimer = null;
    let aiderTimerSourceKey = "";
    let aiderTimerLabel = "";

    let aiderActivityPollTimer = null;
    let aiderAttachedActivityId = "";
    let aiderContextState = {active: {entries: []}, archives: [], activities: []};
    let aiderActionInFlight = false;