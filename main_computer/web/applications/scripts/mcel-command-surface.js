    var McelLabCommandSurface = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const {schema, defaults, blockTemplates} = contract;

      const actionWords = Object.freeze({
        compile: ["compile", "recompile"],
        serialize: ["serialize", "save-clean", "clean-save"],
        repair: ["repair", "heal"],
        damage: ["damage", "break"],
        test: ["test", "suite", "contract"],
        matrix: ["matrix", "coverage", "all-scenarios"],
        acid: ["acid", "acid-test", "acid-tests", "stress-proof", "torture", "fuzz", "hostile"],
        evidence: ["evidence", "packet"],
        explain: ["explain", "inspect", "debug"],
        graph: ["graph", "map"],
        audit: ["audit", "govern", "prove"],
        autopilot: ["autopilot", "full-proof", "prove-all", "quality-gate", "readiness"],
        kernel: ["kernel", "boot-audit", "module-audit"],
        traceability: ["traceability", "requirements", "developer-spec", "spec-map"],
        "prior-art": ["prior-art", "precedent", "references", "reference-map"]
      });

      function words(input) {
        return String(input || "")
          .trim()
          .toLowerCase()
          .split(/\s+/)
          .map((word) => word.replace(/^[^a-z0-9#-]+|[^a-z0-9#-]+$/gi, ""))
          .filter(Boolean);
      }

      function compactValue(input, prefix) {
        const match = String(input || "").match(new RegExp(`${prefix}\\s+([^;]+)`, "i"));
        return match ? match[1].trim() : "";
      }

      function record(plan, type, payload = {}) {
        plan.operations.push({type, ...payload});
      }

      function allowedValuesFor(trait) {
        const union = new Set();
        Object.values(schema).forEach((definition) => {
          const key = {
            kind: "allowedKinds",
            flow: "allowedFlows",
            rank: "allowedRanks",
            state: "allowedStates",
            density: "allowedDensities"
          }[trait];
          (definition[key] || []).forEach((value) => union.add(value));
        });
        return [...union];
      }

      function detectTrait(tokens, trait, input) {
        const allowed = allowedValuesFor(trait);
        const explicit = compactValue(input, `(?:set\\s+)?${trait}`);
        if (explicit && (trait === "connects" || trait === "words" || allowed.includes(explicit))) {
          return explicit;
        }
        return allowed.find((value) => tokens.includes(value)) || "";
      }

      function plan(input, context = {}) {
        const source = String(input || "").trim();
        const tokens = words(source);
        const result = {
          ok: true,
          command: source,
          selectedIndex: Number(context.selectedIndex || 0),
          theme: context.theme || "theme-machine",
          operations: [],
          warnings: [],
          summary: []
        };

        if (!source) {
          result.ok = false;
          result.warnings.push("No command was provided.");
          return result;
        }

        const insertIndex = tokens.indexOf("insert");
        if (insertIndex >= 0) {
          const block = tokens.slice(insertIndex + 1).join("-").replace(/-panel$/, "");
          const blockKey = Object.keys(blockTemplates).find((key) => key === block || key.replace(/-/g, "") === block.replace(/-/g, ""));
          if (blockKey) {
            record(result, "insert-block", {blockKey});
            result.summary.push(`insert ${blockKey}`);
          } else {
            result.warnings.push(`Unknown block requested: ${block || "(missing)"}.`);
          }
        }

        ["kind", "flow", "rank", "state", "density"].forEach((trait) => {
          const value = detectTrait(tokens, trait, source);
          if (value) {
            record(result, "set-trait", {trait, value});
            result.summary.push(`set ${trait}=${value}`);
          }
        });

        const wordsValue = compactValue(source, "words");
        if (wordsValue) {
          record(result, "set-trait", {trait: "words", value: wordsValue});
          result.summary.push("update words");
        }

        const connectsValue = compactValue(source, "connect(?:s)?(?:\\s+to)?");
        if (connectsValue) {
          record(result, "set-trait", {trait: "connects", value: connectsValue.replace(/^#/, "")});
          result.summary.push(`connect to ${connectsValue}`);
        }

        const theme = (styleLaw?.themes || []).find((name) => tokens.includes(name.replace("theme-", "")) || tokens.includes(name));
        if (theme) {
          record(result, "set-theme", {theme});
          result.theme = theme;
          result.summary.push(`theme ${theme}`);
        }

        Object.entries(actionWords).forEach(([action, aliases]) => {
          if (aliases.some((alias) => tokens.includes(alias))) {
            record(result, "action", {action});
            result.summary.push(action);
          }
        });

        const selectMatch = source.match(/select\s+(\d+)/i);
        if (selectMatch) {
          const index = Math.max(Number(selectMatch[1]) - 1, 0);
          record(result, "select", {index});
          result.selectedIndex = index;
          result.summary.push(`select ${index + 1}`);
        }

        if (!result.operations.length) {
          result.ok = false;
          result.warnings.push("Command did not map to a known semantic operation.");
        }
        return result;
      }

      function apply(commandPlan, context = {}) {
        let source = String(context.source || "");
        let selectedIndex = Number(context.selectedIndex || commandPlan.selectedIndex || 0);
        let theme = commandPlan.theme || context.theme || "theme-machine";
        const actions = [];
        const events = [];

        commandPlan.operations.forEach((operation) => {
          if (operation.type === "select") {
            selectedIndex = operation.index;
            events.push({level: "info", module: "command", code: "MCEL_COMMAND_SELECT", message: `Selected source widget ${selectedIndex + 1}.`});
          }
          if (operation.type === "insert-block") {
            const inserted = editor.insertBlock(source, operation.blockKey, {afterIndex: selectedIndex});
            source = inserted.source;
            selectedIndex = inserted.index;
            events.push(...inserted.events);
            events.push({level: "success", module: "command", code: "MCEL_COMMAND_INSERTED", message: `Inserted ${operation.blockKey} block.`});
          }
          if (operation.type === "set-trait") {
            const update = editor.applyTraits(source, {index: selectedIndex}, {[operation.trait]: operation.value});
            source = update.source;
            selectedIndex = update.index;
            events.push(...update.events);
            events.push({level: "success", module: "command", code: "MCEL_COMMAND_TRAIT_SET", message: `Set ${operation.trait}=${operation.value}.`});
          }
          if (operation.type === "set-theme") {
            theme = operation.theme;
            events.push({level: "success", module: "command", code: "MCEL_COMMAND_THEME_SET", message: `Theme changed to ${theme}.`});
          }
          if (operation.type === "action") {
            actions.push(operation.action);
          }
        });

        return {
          source: editor.canonicalSource(source),
          selectedIndex,
          theme,
          actions,
          events
        };
      }

      return Object.freeze({
        plan,
        apply,
        allowedValuesFor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabCommandSurface = McelLabCommandSurface;
    }
