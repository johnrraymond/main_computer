(function (global) {
  "use strict";

  const GAMEPLAY_PACK_JS_RUNTIME_SCHEMA = "game.gameplayPackJsRuntime.v1";
  const GAMEPLAY_PACK_JS_RUNTIME_KIND = "gameplay-pack-js-runtime";
  const GAMEPLAY_PACK_JS_DEFINITION_KIND = "gameplay-pack-js-definition";
  const GAMEPLAY_PACK_JS_FORBIDDEN_GLOBALS = Object.freeze([
    "window",
    "document",
    "fetch",
    "XMLHttpRequest",
    "localStorage",
    "sessionStorage",
    "eval",
    "Function"
  ]);
  const GAMEPLAY_PACK_JS_ALLOWED_EXPORT_PATTERN = /export\s+default\s+defineGameplayPack\s*\(/m;

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function stringValue(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function cloneJson(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function gameplayPackProblem(message, detail = {}) {
    return {
      message: String(message || "gameplay pack validation problem"),
      ...objectValue(detail)
    };
  }

  function gameplayPackJsSourceWithoutStringsAndComments(source) {
    const text = String(source || "");
    let output = "";
    let index = 0;
    let mode = "code";
    let quote = "";
    while (index < text.length) {
      const current = text[index];
      const next = text[index + 1] || "";

      if (mode === "line-comment") {
        if (current === "\n") {
          output += "\n";
          mode = "code";
        } else {
          output += " ";
        }
        index += 1;
        continue;
      }

      if (mode === "block-comment") {
        if (current === "*" && next === "/") {
          output += "  ";
          index += 2;
          mode = "code";
        } else {
          output += current === "\n" ? "\n" : " ";
          index += 1;
        }
        continue;
      }

      if (mode === "string") {
        if (current === "\\") {
          output += " ";
          if (index + 1 < text.length) output += text[index + 1] === "\n" ? "\n" : " ";
          index += 2;
          continue;
        }
        if (current === quote) {
          output += " ";
          mode = "code";
          quote = "";
        } else {
          output += current === "\n" ? "\n" : " ";
        }
        index += 1;
        continue;
      }

      if (current === "/" && next === "/") {
        output += "  ";
        index += 2;
        mode = "line-comment";
        continue;
      }
      if (current === "/" && next === "*") {
        output += "  ";
        index += 2;
        mode = "block-comment";
        continue;
      }
      if (current === "\"" || current === "'" || current === "`") {
        output += " ";
        quote = current;
        mode = "string";
        index += 1;
        continue;
      }

      output += current;
      index += 1;
    }
    return output;
  }

  function gameplayPackJsForbiddenGlobalReferences(source, forbiddenGlobals = GAMEPLAY_PACK_JS_FORBIDDEN_GLOBALS) {
    const codeOnly = gameplayPackJsSourceWithoutStringsAndComments(source);
    return arrayValue(forbiddenGlobals)
      .map(stringValue)
      .filter(Boolean)
      .filter((name) => new RegExp(`(^|[^A-Za-z0-9_$])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Za-z0-9_$]|$)`).test(codeOnly));
  }

  function validateGameplayPackJsSource(source, options = {}) {
    const problems = [];
    const text = String(source || "");
    if (!text.trim()) {
      problems.push(gameplayPackProblem("pack.js source must be non-empty"));
    }
    if (!GAMEPLAY_PACK_JS_ALLOWED_EXPORT_PATTERN.test(text)) {
      problems.push(gameplayPackProblem("pack.js must export default defineGameplayPack(...)"));
    }
    const codeOnly = gameplayPackJsSourceWithoutStringsAndComments(text);
    if (/(^|[^A-Za-z0-9_$])import\s*\(/.test(codeOnly)) {
      problems.push(gameplayPackProblem("pack.js must not use dynamic import()"));
    }
    if (/(^|\n)\s*import\s+/.test(codeOnly)) {
      problems.push(gameplayPackProblem("pack.js must not use import declarations"));
    }
    const forbiddenGlobals = gameplayPackJsForbiddenGlobalReferences(
      text,
      objectValue(options).forbiddenGlobals || GAMEPLAY_PACK_JS_FORBIDDEN_GLOBALS
    );
    forbiddenGlobals.forEach((name) => {
      problems.push(gameplayPackProblem(`pack.js must not reference forbidden global ${name}`, {
        forbiddenGlobal: name
      }));
    });
    return {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: "gameplay-pack-js-source-validation",
      ok: problems.length === 0,
      valid: problems.length === 0,
      forbiddenGlobals,
      problems
    };
  }

  function normalizeGameplayPackTargets(value) {
    const raw = objectValue(value);
    return {
      projectId: stringValue(raw.projectId),
      scene: stringValue(raw.scene),
      encounter: stringValue(raw.encounter),
      template: stringValue(raw.template),
      systemId: stringValue(raw.systemId),
      destinationId: stringValue(raw.destinationId)
    };
  }

  function normalizeGameplayPackPermissions(value) {
    const raw = objectValue(value);
    const keys = [
      "spawnActors",
      "modifyCombatStats",
      "showHudMessages",
      "setObjectives",
      "grantReceipts",
      "completeEncounter",
      "writeSaveState",
      "accessNetwork",
      "accessFilesystem",
      "accessDom",
      "rawRendererAccess",
      "rawRuntimeStateMutation"
    ];
    return keys.reduce((record, key) => {
      record[key] = raw[key] === true;
      return record;
    }, {});
  }

  function defineGameplayPack(definition) {
    const raw = objectValue(definition);
    const id = stringValue(raw.id);
    const title = stringValue(raw.title);
    const version = stringValue(raw.version);
    const setup = raw.setup;
    const problems = [];
    if (!id) problems.push(gameplayPackProblem("gameplay pack id is required"));
    if (!title) problems.push(gameplayPackProblem("gameplay pack title is required"));
    if (!version) problems.push(gameplayPackProblem("gameplay pack version is required"));
    if (typeof setup !== "function") {
      problems.push(gameplayPackProblem("gameplay pack setup(pack) function is required"));
    }
    return {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: GAMEPLAY_PACK_JS_DEFINITION_KIND,
      id,
      title,
      version,
      description: stringValue(raw.description),
      targets: normalizeGameplayPackTargets(raw.targets),
      permissions: normalizeGameplayPackPermissions(raw.permissions),
      setup,
      setupAvailable: typeof setup === "function",
      loadOnly: true,
      handlersRegistered: false,
      gameplayExecuted: false,
      problems
    };
  }

  function transformGameplayPackJsModuleSource(source) {
    const text = String(source || "");
    if (!GAMEPLAY_PACK_JS_ALLOWED_EXPORT_PATTERN.test(text)) {
      throw new Error("pack.js must export default defineGameplayPack(...)");
    }
    return text.replace(GAMEPLAY_PACK_JS_ALLOWED_EXPORT_PATTERN, "return defineGameplayPack(");
  }

  function loadGameplayPackDefinitionFromSource(source, options = {}) {
    const validation = validateGameplayPackJsSource(source, options);
    if (!validation.ok) {
      const message = validation.problems.map((problem) => problem.message).join("; ");
      throw new Error(message || "invalid gameplay pack source");
    }
    const transformed = transformGameplayPackJsModuleSource(source);
    const factory = new Function("defineGameplayPack", `"use strict";\n${transformed}`);
    const definition = factory(defineGameplayPack);
    const normalized = objectValue(definition);
    if (stringValue(normalized.kind) !== GAMEPLAY_PACK_JS_DEFINITION_KIND) {
      throw new Error("pack.js did not return a gameplay pack definition");
    }
    if (arrayValue(normalized.problems).length) {
      const message = normalized.problems.map((problem) => stringValue(problem.message)).filter(Boolean).join("; ");
      throw new Error(message || "gameplay pack definition is invalid");
    }
    return normalized;
  }

  function gameplayPackDefinitionSummary(definition) {
    const raw = objectValue(definition);
    return {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: "gameplay-pack-js-definition-summary",
      id: stringValue(raw.id),
      title: stringValue(raw.title),
      version: stringValue(raw.version),
      targets: cloneJson(raw.targets || {}),
      permissions: cloneJson(raw.permissions || {}),
      setupAvailable: typeof raw.setup === "function" || raw.setupAvailable === true,
      loadOnly: raw.loadOnly !== false,
      handlersRegistered: raw.handlersRegistered === true,
      gameplayExecuted: raw.gameplayExecuted === true
    };
  }

  function integerValue(value, fallback = 0, min = Number.MIN_SAFE_INTEGER, max = Number.MAX_SAFE_INTEGER) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(min, Math.min(max, Math.trunc(number)));
  }

  function numberValue(value, fallback = 0, min = -Infinity, max = Infinity) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(min, Math.min(max, number));
  }

  function boolValue(value, fallback = false) {
    return typeof value === "boolean" ? value : fallback;
  }

  function normalizeGameplayPackActorCommand(value) {
    const raw = objectValue(value);
    return {
      archetype: stringValue(raw.archetype || raw.actorArchetypeId),
      count: integerValue(raw.count, 1, 1, 64),
      displayName: stringValue(raw.displayName),
      healthMultiplier: numberValue(raw.healthMultiplier, 1, 0.1, 10)
    };
  }

  function normalizeGameplayPackWaveCommand(value) {
    const raw = objectValue(value);
    const trigger = objectValue(raw.trigger);
    const triggerCommand = {
      type: stringValue(trigger.type || raw.triggerType)
    };
    if (Object.prototype.hasOwnProperty.call(trigger, "count") || Object.prototype.hasOwnProperty.call(raw, "triggerCount")) {
      triggerCommand.count = integerValue(trigger.count || raw.triggerCount, 0, 0, 999);
    }
    if (stringValue(trigger.cutsceneId || raw.cutsceneId)) {
      triggerCommand.cutsceneId = stringValue(trigger.cutsceneId || raw.cutsceneId);
    }

    const command = {
      id: stringValue(raw.id || raw.waveId),
      trigger: triggerCommand,
      actors: arrayValue(raw.actors).map(normalizeGameplayPackActorCommand).filter((actor) => actor.archetype),
      hudMessage: stringValue(raw.hudMessage || raw.message)
    };
    if (stringValue(raw.location || raw.locationId)) {
      command.location = stringValue(raw.location || raw.locationId);
    }
    return command;
  }

  function normalizeGameplayPackObjectiveCommand(value) {
    const raw = objectValue(value);
    const command = {
      id: stringValue(raw.id || raw.objectiveId),
      label: stringValue(raw.label || raw.title || raw.text),
      required: boolValue(raw.required, true)
    };
    if (stringValue(raw.status)) {
      command.status = stringValue(raw.status);
    }
    return command;
  }

  function gameplayPackCommand(harness, subjectKey, subjectId, type, payload = {}) {
    const rawHarness = objectValue(harness);
    const command = {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: "gameplay-pack-js-command",
      type: stringValue(type),
      sourcePackId: stringValue(rawHarness.packId),
      payload: cloneJson(objectValue(payload)),
      applied: false,
      gameplayExecuted: false
    };
    command[stringValue(subjectKey)] = stringValue(subjectId);
    if (Array.isArray(rawHarness.currentCommands)) rawHarness.currentCommands.push(command);
    return command;
  }

  function gameplayPackEncounterCommand(harness, encounterId, type, payload = {}) {
    return gameplayPackCommand(harness, "encounterId", encounterId, type, payload);
  }

  function gameplayPackSectionCommand(harness, sectionId, type, payload = {}) {
    return gameplayPackCommand(harness, "sectionId", sectionId, type, payload);
  }

  function createGameplayPackEncounterHarness(harness, encounterId) {
    const id = stringValue(encounterId);
    const registeredHandlers = [];
    function register(event, match, callback) {
      if (typeof callback !== "function") {
        throw new Error(`gameplay pack handler for ${event} must be a function`);
      }
      const record = {
        event: stringValue(event),
        match: cloneJson(objectValue(match)),
        callback,
        order: registeredHandlers.length + 1
      };
      registeredHandlers.push(record);
      return record;
    }

    const encounter = {
      id,
      handlers: registeredHandlers,

      onStart(callback) {
        return register("start", {}, callback);
      },

      onHostilesDefeated(count, callback) {
        return register("hostiles-defeated", {
          count: integerValue(count, 0, 0, 999)
        }, callback);
      },

      onAllHostilesDefeated(callback) {
        return register("all-hostiles-defeated", {}, callback);
      },

      onDestinationReached(destinationId, callback) {
        return register("destination-reached", {
          destinationId: stringValue(destinationId)
        }, callback);
      },

      onPlayerDefeated(callback) {
        return register("player-defeated", {}, callback);
      },

      setHostileHealthMultiplier(multiplier) {
        return gameplayPackEncounterCommand(harness, id, "set-hostile-health-multiplier", {
          multiplier: numberValue(multiplier, 1, 0.1, 10)
        });
      },

      showHudMessage(message) {
        return gameplayPackEncounterCommand(harness, id, "show-hud-message", {
          message: stringValue(message)
        });
      },

      spawnWave(wave) {
        return gameplayPackEncounterCommand(harness, id, "spawn-wave", normalizeGameplayPackWaveCommand(wave));
      },

      setObjective(objective) {
        return gameplayPackEncounterCommand(harness, id, "set-objective", normalizeGameplayPackObjectiveCommand(objective));
      },

      complete(result = {}) {
        const raw = objectValue(result);
        return gameplayPackEncounterCommand(harness, id, "complete-encounter", {
          receipt: stringValue(raw.receipt || raw.receiptId),
          message: stringValue(raw.message)
        });
      },

      fail(result = {}) {
        const raw = objectValue(result);
        return gameplayPackEncounterCommand(harness, id, "fail-encounter", {
          reason: stringValue(raw.reason),
          message: stringValue(raw.message)
        });
      }
    };

    return encounter;
  }

  function createGameplayPackSectionHarness(harness, sectionId) {
    const id = stringValue(sectionId);
    const registeredHandlers = [];
    function register(event, match, callback) {
      if (typeof callback !== "function") {
        throw new Error(`gameplay pack section handler for ${event} must be a function`);
      }
      const record = {
        event: stringValue(event),
        match: cloneJson(objectValue(match)),
        callback,
        order: registeredHandlers.length + 1
      };
      registeredHandlers.push(record);
      return record;
    }

    const section = {
      id,
      handlers: registeredHandlers,

      onCutsceneResolved(cutsceneId, callback) {
        return register("cutscene-resolved", {
          cutsceneId: stringValue(cutsceneId)
        }, callback);
      },

      onAllHostilesDefeated(callback) {
        return register("all-hostiles-defeated", {}, callback);
      },

      onPlayerDefeated(callback) {
        return register("player-defeated", {}, callback);
      },

      showHudMessage(message) {
        return gameplayPackSectionCommand(harness, id, "show-hud-message", {
          message: stringValue(message)
        });
      },

      spawnWave(wave) {
        return gameplayPackSectionCommand(harness, id, "spawn-wave", normalizeGameplayPackWaveCommand(wave));
      },

      setObjective(objective) {
        return gameplayPackSectionCommand(harness, id, "set-objective", normalizeGameplayPackObjectiveCommand(objective));
      },

      complete(result = {}) {
        const raw = objectValue(result);
        return gameplayPackSectionCommand(harness, id, "complete-section", {
          receipt: stringValue(raw.receipt || raw.receiptId),
          message: stringValue(raw.message)
        });
      },

      fail(result = {}) {
        const raw = objectValue(result);
        return gameplayPackSectionCommand(harness, id, "fail-section", {
          reason: stringValue(raw.reason),
          message: stringValue(raw.message)
        });
      }
    };

    return section;
  }

  function gameplayPackHandlerMatches(handler, event) {
    const rawHandler = objectValue(handler);
    const rawEvent = objectValue(event);
    const type = stringValue(rawEvent.type || rawEvent.event);
    const match = objectValue(rawHandler.match);
    if (!type || type !== stringValue(rawHandler.event)) return false;
    if (type === "hostiles-defeated") {
      return integerValue(rawEvent.count, -1, -1, 999) >= integerValue(match.count, 0, 0, 999);
    }
    if (type === "destination-reached") {
      const target = stringValue(match.destinationId);
      return !target || target === stringValue(rawEvent.destinationId || rawEvent.destination);
    }
    if (type === "cutscene-resolved") {
      const target = stringValue(match.cutsceneId);
      return !target || target === stringValue(rawEvent.cutsceneId || rawEvent.cutscene || rawEvent.id);
    }
    return true;
  }

  function createGameplayPackCommandHarness(definition, options = {}) {
    const rawDefinition = objectValue(definition);
    if (stringValue(rawDefinition.kind) !== GAMEPLAY_PACK_JS_DEFINITION_KIND) {
      throw new Error("createGameplayPackCommandHarness requires a loaded gameplay pack definition");
    }
    if (typeof rawDefinition.setup !== "function") {
      throw new Error("gameplay pack setup(pack) function is required");
    }

    const harness = {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: "gameplay-pack-js-command-harness",
      packId: stringValue(rawDefinition.id),
      title: stringValue(rawDefinition.title),
      version: stringValue(rawDefinition.version),
      targets: cloneJson(rawDefinition.targets || {}),
      permissions: cloneJson(rawDefinition.permissions || {}),
      encounters: [],
      sections: [],
      commands: [],
      currentCommands: null,
      setupRun: false,
      handlersRegistered: false,
      gameplayExecuted: false,
      appliedToScene: false,
      problems: []
    };

    const pack = {
      id: harness.packId,
      title: harness.title,
      version: harness.version,
      encounter(encounterId, callback) {
        if (typeof callback !== "function") {
          throw new Error("pack.encounter(id, callback) requires a callback");
        }
        const encounter = createGameplayPackEncounterHarness(harness, encounterId);
        harness.encounters.push(encounter);
        callback(encounter);
        return encounter;
      },

      section(sectionId, callback) {
        if (typeof callback !== "function") {
          throw new Error("pack.section(id, callback) requires a callback");
        }
        const section = createGameplayPackSectionHarness(harness, sectionId);
        harness.sections.push(section);
        callback(section);
        return section;
      }
    };

    rawDefinition.setup(pack, cloneJson(objectValue(options)));
    harness.setupRun = true;
    harness.handlersRegistered =
      harness.encounters.some((encounter) => encounter.handlers.length > 0) ||
      harness.sections.some((section) => section.handlers.length > 0);
    return harness;
  }

  function gameplayPackCommandHarnessSummary(harness) {
    const raw = objectValue(harness);
    return {
      schema: GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
      kind: "gameplay-pack-js-command-harness-summary",
      packId: stringValue(raw.packId),
      title: stringValue(raw.title),
      version: stringValue(raw.version),
      setupRun: raw.setupRun === true,
      handlersRegistered: raw.handlersRegistered === true,
      gameplayExecuted: raw.gameplayExecuted === true,
      appliedToScene: raw.appliedToScene === true,
      encounterIds: arrayValue(raw.encounters).map((encounter) => stringValue(encounter.id)).filter(Boolean),
      sectionIds: arrayValue(raw.sections).map((section) => stringValue(section.id)).filter(Boolean),
      handlerCount:
        arrayValue(raw.encounters).reduce((total, encounter) => total + arrayValue(encounter.handlers).length, 0) +
        arrayValue(raw.sections).reduce((total, section) => total + arrayValue(section.handlers).length, 0),
      handlers: arrayValue(raw.encounters).flatMap((encounter) => arrayValue(encounter.handlers).map((handler) => ({
        encounterId: stringValue(encounter.id),
        event: stringValue(handler.event),
        match: cloneJson(handler.match || {}),
        order: integerValue(handler.order, 0, 0)
      }))).concat(arrayValue(raw.sections).flatMap((section) => arrayValue(section.handlers).map((handler) => ({
        sectionId: stringValue(section.id),
        event: stringValue(handler.event),
        match: cloneJson(handler.match || {}),
        order: integerValue(handler.order, 0, 0)
      })))),
      commandCount: arrayValue(raw.commands).length,
      problems: cloneJson(arrayValue(raw.problems))
    };
  }

  function dispatchGameplayPackEncounterEvent(harness, encounterId, event = {}) {
    const rawHarness = objectValue(harness);
    const id = stringValue(encounterId);
    const rawEvent = objectValue(event);
    const encounters = arrayValue(rawHarness.encounters).filter((encounter) => stringValue(encounter.id) === id);
    const commands = [];
    rawHarness.currentCommands = commands;
    encounters.forEach((encounter) => {
      arrayValue(encounter.handlers)
        .filter((handler) => gameplayPackHandlerMatches(handler, rawEvent))
        .sort((left, right) => integerValue(left.order, 0, 0) - integerValue(right.order, 0, 0))
        .forEach((handler) => {
          handler.callback(cloneJson(rawEvent));
        });
    });
    rawHarness.currentCommands = null;
    rawHarness.commands = arrayValue(rawHarness.commands).concat(commands);
    return cloneJson(commands);
  }

  function dispatchGameplayPackSectionEvent(harness, sectionId, event = {}) {
    const rawHarness = objectValue(harness);
    const id = stringValue(sectionId);
    const rawEvent = objectValue(event);
    const sections = arrayValue(rawHarness.sections).filter((section) => stringValue(section.id) === id);
    const commands = [];
    rawHarness.currentCommands = commands;
    sections.forEach((section) => {
      arrayValue(section.handlers)
        .filter((handler) => gameplayPackHandlerMatches(handler, rawEvent))
        .sort((left, right) => integerValue(left.order, 0, 0) - integerValue(right.order, 0, 0))
        .forEach((handler) => {
          handler.callback(cloneJson(rawEvent));
        });
    });
    rawHarness.currentCommands = null;
    rawHarness.commands = arrayValue(rawHarness.commands).concat(commands);
    return cloneJson(commands);
  }

  function loadGameplayPackCommandHarnessFromSource(source, options = {}) {
    const definition = loadGameplayPackDefinitionFromSource(source, options);
    return createGameplayPackCommandHarness(definition, options);
  }


  const api = {
    GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
    GAMEPLAY_PACK_JS_RUNTIME_KIND,
    GAMEPLAY_PACK_JS_DEFINITION_KIND,
    GAMEPLAY_PACK_JS_FORBIDDEN_GLOBALS,
    defineGameplayPack,
    validateGameplayPackJsSource,
    gameplayPackJsForbiddenGlobalReferences,
    loadGameplayPackDefinitionFromSource,
    gameplayPackDefinitionSummary,
    createGameplayPackCommandHarness,
    gameplayPackCommandHarnessSummary,
    dispatchGameplayPackEncounterEvent,
    dispatchGameplayPackSectionEvent,
    loadGameplayPackCommandHarnessFromSource
  };

  global.MainComputerGameplayPackRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
