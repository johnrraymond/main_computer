    (function (global) {
      "use strict";

      const REGISTRY_VERSION = "0.2.0";
      const PHASE_ORDER = Object.freeze([
        "intake",
        "structure-detection",
        "purpose-inference",
        "risk-classification",
        "contract-assignment",
        "rectification",
        "rewrite-preview",
        "audit"
      ]);

      function phaseRank(phase) {
        const index = PHASE_ORDER.indexOf(phase);
        return index === -1 ? PHASE_ORDER.length : index;
      }

      function validateRule(rule, packId) {
        if (!rule || typeof rule !== "object") throw new Error(`MCEL Supercut registry rule in ${packId} must be an object`);
        if (!rule.id || typeof rule.id !== "string") throw new Error(`MCEL Supercut registry rule in ${packId} is missing an id`);
        if (!rule.phase || typeof rule.phase !== "string") throw new Error(`MCEL Supercut registry rule ${rule.id} is missing a phase`);
        if (typeof rule.apply !== "function") throw new Error(`MCEL Supercut registry rule ${rule.id} is missing apply(record, blackboard)`);
        return true;
      }

      function validatePack(pack) {
        if (!pack || typeof pack !== "object") throw new Error("MCEL Supercut knowledge pack must be an object");
        if (!pack.id || typeof pack.id !== "string") throw new Error("MCEL Supercut knowledge pack is missing id");
        if (!pack.version || typeof pack.version !== "string") throw new Error(`MCEL Supercut knowledge pack ${pack.id} is missing version`);
        if (!Array.isArray(pack.rules)) throw new Error(`MCEL Supercut knowledge pack ${pack.id} is missing rules`);
        const localRuleIds = new Set();
        pack.rules.forEach((rule) => {
          validateRule(rule, pack.id);
          if (localRuleIds.has(rule.id)) {
            throw new Error(`MCEL Supercut duplicate rule id rejected: ${rule.id}`);
          }
          localRuleIds.add(rule.id);
        });
        return true;
      }

      function createRegistry() {
        const packs = new Map();
        const ruleIds = new Map();

        function registerPack(pack) {
          validatePack(pack);
          const existing = packs.get(pack.id);
          if (existing) return existing;
          pack.rules.forEach((rule) => {
            if (ruleIds.has(rule.id)) {
              throw new Error(`MCEL Supercut duplicate rule id rejected: ${rule.id}`);
            }
            ruleIds.set(rule.id, pack.id);
          });
          const normalized = Object.freeze({
            id: pack.id,
            version: pack.version,
            description: pack.description || "",
            rules: Object.freeze(pack.rules.slice())
          });
          packs.set(pack.id, normalized);
          return normalized;
        }

        function registerPacks(nextPacks) {
          (nextPacks || []).forEach(registerPack);
          return listPacks();
        }

        function clear() {
          packs.clear();
          ruleIds.clear();
          return true;
        }

        function listPacks() {
          return Array.from(packs.values()).map((pack) => ({
            id: pack.id,
            version: pack.version,
            description: pack.description,
            ruleCount: pack.rules.length
          }));
        }

        function selectPacks(packIds) {
          const ids = Array.isArray(packIds) && packIds.length ? packIds : Array.from(packs.keys());
          return ids.map((id) => packs.get(id)).filter(Boolean);
        }

        function sortedRules(packIds) {
          return selectPacks(packIds)
            .flatMap((pack) => pack.rules.map((rule) => ({...rule, packId: pack.id})))
            .sort((left, right) => {
              const phaseDelta = phaseRank(left.phase) - phaseRank(right.phase);
              if (phaseDelta) return phaseDelta;
              const priorityDelta = Number(right.priority || 0) - Number(left.priority || 0);
              if (priorityDelta) return priorityDelta;
              return left.id.localeCompare(right.id);
            });
        }

        function run(blackboard, options = {}) {
          if (!blackboard?.records) {
            return {
              status: "skipped",
              packsLoaded: 0,
              rulesFired: 0,
              ruleTrace: []
            };
          }
          const activePacks = selectPacks(options.packs);
          const rules = sortedRules(options.packs);
          const ruleTrace = [];
          let rulesFired = 0;
          activePacks.forEach((pack) => {
            blackboard.addEvidence?.(null, "pack-loaded", `${pack.id}@${pack.version}`, "mcel-supercut-registry.load-pack");
          });
          rules.forEach((rule) => {
            let firedForRule = 0;
            blackboard.records.forEach((record) => {
              let shouldRun = true;
              if (typeof rule.when === "function") {
                try {
                  shouldRun = Boolean(rule.when(record, blackboard));
                } catch (error) {
                  blackboard.addViolation?.("rule-when-error", record, `${rule.id}: ${error.message || error}`, "warning");
                  shouldRun = false;
                }
              }
              if (!shouldRun) return;
              try {
                const result = rule.apply(record, blackboard);
                if (result !== false) {
                  firedForRule += 1;
                  rulesFired += 1;
                }
              } catch (error) {
                blackboard.addViolation?.("rule-apply-error", record, `${rule.id}: ${error.message || error}`, "warning");
              }
            });
            if (firedForRule) {
              ruleTrace.push({
                ruleId: rule.id,
                packId: rule.packId,
                phase: rule.phase,
                priority: Number(rule.priority || 0),
                fired: firedForRule
              });
            }
          });
          blackboard.metrics.packsLoaded = activePacks.length;
          blackboard.metrics.rulesFired += rulesFired;
          return {
            status: "ready",
            packsLoaded: activePacks.length,
            rulesFired,
            ruleTrace,
            packs: activePacks.map((pack) => ({id: pack.id, version: pack.version, ruleCount: pack.rules.length}))
          };
        }

        function loadDefaultPacks() {
          [
            global.McelSupercutPacksCore?.coreHtmlPack,
            global.McelSupercutPacksCore?.coreActionRiskPack,
            global.McelSupercutPacksGitTools?.gitToolsDomainPack,
            global.McelSupercutPacksTaskManager?.taskManagerDomainPack
          ].filter(Boolean).forEach(registerPack);
          return listPacks();
        }

        return {
          version: REGISTRY_VERSION,
          PHASE_ORDER,
          validatePack,
          registerPack,
          registerPacks,
          clear,
          listPacks,
          sortedRules,
          run,
          loadDefaultPacks
        };
      }

      const defaultRegistry = createRegistry();

      global.McelSupercutRegistry = {
        REGISTRY_VERSION,
        PHASE_ORDER,
        createRegistry,
        validatePack,
        registerPack: defaultRegistry.registerPack,
        registerPacks: defaultRegistry.registerPacks,
        clear: defaultRegistry.clear,
        listPacks: defaultRegistry.listPacks,
        sortedRules: defaultRegistry.sortedRules,
        run: defaultRegistry.run,
        loadDefaultPacks: defaultRegistry.loadDefaultPacks
      };
    })(window);
