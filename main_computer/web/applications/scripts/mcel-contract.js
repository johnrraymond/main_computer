    var McelLabContract = (() => {
      const attributes = Object.freeze({
        type: "data-mc",
        kind: "data-mc-kind",
        flow: "data-mc-flow",
        rank: "data-mc-rank",
        state: "data-mc-state",
        density: "data-mc-density",
        words: "data-mc-words",
        connects: "data-mc-connects",
        sizePolicy: "data-mc-size-policy",
        overflowPolicy: "data-mc-overflow-policy",
        scrollPolicy: "data-mc-scroll-policy",
        componentName: "data-mc-component",
        componentKind: "data-mc-component-kind",
        slot: "data-mc-slot",
        propContract: "data-mc-prop-contract",
        stateOwner: "data-mc-state-owner",
        stateScope: "data-mc-state-scope",
        statePolicy: "data-mc-state-policy",
        query: "data-mc-query",
        cachePolicy: "data-mc-cache-policy",
        mutation: "data-mc-mutation",
        syncPolicy: "data-mc-sync-policy",
        submit: "data-mc-submit",
        validation: "data-mc-validation",
        dirtyPolicy: "data-mc-dirty-policy",
        errorPolicy: "data-mc-error-policy",
        action: "data-mc-action",
        target: "data-mc-target",
        swapPolicy: "data-mc-swap-policy",
        eventPolicy: "data-mc-event-policy",
        route: "data-mc-route",
        renderMode: "data-mc-render",
        hydration: "data-mc-hydration",
        islandPolicy: "data-mc-island",
        focusPolicy: "data-mc-focus-policy",
        a11yPolicy: "data-mc-a11y-policy",
        performanceBudget: "data-mc-performance-budget",
        securityPolicy: "data-mc-security-policy",
        enhanced: "data-mc-enhanced",
        generated: "data-mc-generated",
        part: "data-mc-part",
        neighborhood: "data-mc-neighborhood",
        computedDensity: "data-mc-density-computed",
        relation: "data-mc-relation",
        relationCount: "data-mc-relation-count",
        clusterSize: "data-mc-cluster-size",
        sourceIndex: "data-mc-source-index",
        editorSelected: "data-mc-editor-selected",
        theme: "data-mc-theme",
        styleLaw: "data-mc-style-law",
        flowAxis: "data-mc-flow-axis",
        fieldPressure: "data-mc-field-pressure",
        attention: "data-mc-attention",
        relationMode: "data-mc-relation-mode",
        layoutLaw: "data-mc-layout-law",
        overflowComputed: "data-mc-overflow-computed",
        scrollNeeded: "data-mc-scroll-needed",
        scrollOwner: "data-mc-scroll-owner",
        layoutPressure: "data-mc-layout-pressure",
        geometryProof: "data-mc-geometry-proof",
        keyboardScroll: "data-mc-keyboard-scroll",
        componentLaw: "data-mc-component-law",
        slotLaw: "data-mc-slot-law",
        stateLaw: "data-mc-state-law",
        dataLaw: "data-mc-data-law",
        formLaw: "data-mc-form-law",
        actionLaw: "data-mc-action-law",
        renderLaw: "data-mc-render-law",
        a11yLaw: "data-mc-a11y-law",
        focusLaw: "data-mc-focus-law",
        performanceLaw: "data-mc-performance-law",
        securityLaw: "data-mc-security-law",
        proofTier: "data-mc-proof-tier",
        semanticRisk: "data-mc-semantic-risk",
        dependencyMode: "data-mc-dependency-mode",
        hydrationProof: "data-mc-hydration-proof",
        dataFreshness: "data-mc-data-freshness",
        formValidity: "data-mc-form-validity",
        actionSafety: "data-mc-action-safety",
        artifactOwner: "data-mc-owner",
        artifactOrigin: "data-mc-origin",
        artifactReason: "data-mc-reason",
        contractVersion: "data-mc-contract-version"
      });

      const contractVersion = "mcel-lab.v0.11-ui-site-skeleton";

      const contractGuarantees = Object.freeze([
        Object.freeze({
          id: "mcel.contract.source-intent-is-input.v1",
          label: "Source intent is the durable input",
          status: "executable",
          scope: "MCEL source elements selected by data-mc before compile, repair, or serialize.",
          guarantee: "The durable MCEL input is source-owned markup and source-owned data-mc-* policy attributes; runtime facts are outputs, not durable source intent.",
          absoluteWhen: Object.freeze([
            "The element is inside a DOM root passed to McelLabEngine.compileDocument or serializeRuntimeRoot.",
            "The element carries the canonical data-mc attribute.",
            "Callers treat compile-time schema normalization as an explicit source mutation and inspect emitted events before saving."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL does not guarantee that arbitrary non-MCEL DOM is semantically owned.",
            "MCEL does not guarantee that invalid source values are preserved byte-for-byte after compile.",
            "MCEL does not make external framework state durable source intent."
          ]),
          failureMode: "If source ownership is ambiguous, MCEL must report or normalize explicitly instead of silently inventing durable intent.",
          evidenceTests: Object.freeze([
            "compile inserts generated parts without touching source semantics",
            "schema normalizes invalid trait values"
          ])
        }),
        Object.freeze({
          id: "mcel.contract.generated-runtime-is-discardable.v1",
          label: "Generated runtime parts are discardable",
          status: "executable",
          scope: "Nodes and attributes marked by MCEL as generated/runtime-owned.",
          guarantee: "MCEL-generated DOM parts and runtime-owned attributes are reconstructable from source and must not be required as saved source.",
          absoluteWhen: Object.freeze([
            "Generated nodes carry data-mc-generated=\"true\" and data-mc-part.",
            "Runtime-owned attributes are listed in McelLabContract.runtimeOwnedAttributes.",
            "Repair is allowed to rebuild generated parts from the current source schema."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL does not guarantee recovery of user-authored content placed inside generated nodes.",
            "MCEL does not guarantee recovery after callers remove source-owned data-mc attributes.",
            "MCEL does not treat unknown third-party generated markup as MCEL-owned."
          ]),
          failureMode: "If generated ownership cannot be proven, serialization and repair must leave the node source-owned or warn rather than deleting it.",
          evidenceTests: Object.freeze([
            "compile inserts generated parts without touching source semantics",
            "repair restores canonical generated parts after damage"
          ])
        }),
        Object.freeze({
          id: "mcel.contract.serializer-cleans-runtime-state.v1",
          label: "Serialization strips runtime state",
          status: "executable",
          scope: "serializeRuntimeRoot output produced from an MCEL runtime root.",
          guarantee: "Serialization removes MCEL-generated parts and MCEL runtime-owned attributes before returning clean source markup.",
          absoluteWhen: Object.freeze([
            "The caller serializes through McelLabEngine.serializeRuntimeRoot.",
            "Runtime-owned generated nodes and attributes use the names declared by McelLabContract.",
            "The serializer report returns serializerClean=true."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL does not clean arbitrary inline styles or classes that were source-authored by non-MCEL code.",
            "MCEL does not guarantee cleanliness if callers ignore serializerClean=false.",
            "MCEL does not infer deletes from omitted files or external storage state."
          ]),
          failureMode: "If generated markers survive serialization, serializerClean must become false and warnings must explain the leak.",
          evidenceTests: Object.freeze([
            "serializer removes all generated runtime parts",
            "platform source policies survive while runtime proof facts are stripped",
            "layout source policies survive while observed geometry is stripped"
          ])
        }),
        Object.freeze({
          id: "mcel.contract.repair-is-schema-bounded.v1",
          label: "Repair is bounded by schema",
          status: "executable",
          scope: "repairRuntimeRoot rebuilding direct MCEL generated children.",
          guarantee: "Repair may restore missing canonical generated parts, but it must derive those parts from the declared schema for the source element.",
          absoluteWhen: Object.freeze([
            "The source element still has a supported data-mc type.",
            "The schema declares generatedParts for that type.",
            "Repair only rebuilds MCEL-owned generated children and leaves source-owned children in place."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL repair does not recover deleted source-authored children.",
            "MCEL repair does not adjudicate business meaning outside the schema.",
            "MCEL repair does not make stale browser measurements true."
          ]),
          failureMode: "If schema ownership is unclear, repair must report no-op or warnings rather than inventing new source meaning.",
          evidenceTests: Object.freeze([
            "repair restores canonical generated parts after damage"
          ])
        }),
        Object.freeze({
          id: "mcel.contract.validation-is-reporting-not-trust.v1",
          label: "Validation reports proof state",
          status: "executable",
          scope: "MCEL validation, a11y, browser, proof, and adoption reports.",
          guarantee: "MCEL reports validation/proof state and must not convert an unchecked claim into a trusted platform decision.",
          absoluteWhen: Object.freeze([
            "The report is produced by an MCEL proof, validation, contract test, or adoption-case entry point.",
            "The caller checks failed/clean/valid verdict fields before adopting the behavior.",
            "Uncovered guarantees are treated as not proven."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL does not guarantee a screen is accessible merely because it rendered.",
            "MCEL does not guarantee runtime correctness when proof reports are ignored.",
            "MCEL does not guarantee a platform replacement claim without evidence gates."
          ]),
          failureMode: "If evidence is missing, MCEL must expose a warning, failed guarantee, uncovered guarantee, or hold verdict instead of a pass.",
          evidenceTests: Object.freeze([
            "a11y report fails unlabeled source widgets",
            "MCEL adoption case is gate based, not assertion based"
          ])
        }),
        Object.freeze({
          id: "mcel.contract.browser-facts-are-runtime-only.v1",
          label: "Browser facts are runtime-only",
          status: "executable",
          scope: "Observed layout, overflow, scroll, geometry, hydration, and performance facts.",
          guarantee: "Observed browser facts may guide runtime repair or diagnostics, but they must not be serialized as source-owned policy.",
          absoluteWhen: Object.freeze([
            "Observed facts use attributes listed in runtimeOwnedAttributes.",
            "Serialization runs through serializeRuntimeRoot.",
            "The caller treats browser observations as evidence attached to a runtime report, not source intent."
          ]),
          nonGuarantees: Object.freeze([
            "MCEL does not make browser measurements stable across devices.",
            "MCEL does not guarantee that an observation remains true after CSS, viewport, or content changes.",
            "MCEL does not persist browser facts as authored source."
          ]),
          failureMode: "If a browser fact would be saved as source, serialization must strip it or report an unclean serializer result.",
          evidenceTests: Object.freeze([
            "layout source policies survive while observed geometry is stripped"
          ])
        })
      ]);

      const userSpaceContract = Object.freeze([
        Object.freeze({
          id: "mcel.user.source-traits-are-planning-surface.v1",
          label: "Source traits are the planning surface",
          stableSurface: "Author-owned HTML elements with data-mc plus supported data-mc-* policy attributes.",
          userCanRelyOn: Object.freeze([
            "MCEL reads source-owned data-mc traits as the durable description of intent.",
            "Compile-time normalization is observable through events instead of being hidden.",
            "Unsupported MCEL values fall back to documented defaults instead of inventing a new contract."
          ]),
          userMustProvide: Object.freeze([
            "Use data-mc on elements MCEL is expected to own.",
            "Use supported values from McelLabContract.schema and platform policy lists.",
            "Inspect compile events before saving normalized source."
          ]),
          userMustNotAssume: Object.freeze([
            "Non-MCEL DOM is automatically owned by MCEL.",
            "Invalid trait values are preserved byte-for-byte after compile.",
            "External framework state becomes MCEL source intent."
          ]),
          failClosedSignal: "warning event such as MCEL_UNKNOWN_TYPE or MCEL_SCHEMA_NORMALIZED",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.source-intent-is-input.v1",
            "mcel.contract.validation-is-reporting-not-trust.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.runtime-generation-is-discardable.v1",
          label: "Runtime generation is discardable",
          stableSurface: "Generated nodes marked data-mc-generated=\"true\" and runtime-owned data-mc-* attributes.",
          userCanRelyOn: Object.freeze([
            "MCEL-generated wrappers, rails, metadata, proof markers, and browser facts are runtime outputs.",
            "Generated parts may be removed and rebuilt from current source traits.",
            "Runtime-owned attributes are listed in McelLabContract.runtimeOwnedAttributes."
          ]),
          userMustProvide: Object.freeze([
            "Keep authored content in source-owned elements, not inside generated MCEL parts.",
            "Treat generated nodes as cacheable runtime artifacts.",
            "Use MCEL repair or compile rather than hand-editing generated runtime markup."
          ]),
          userMustNotAssume: Object.freeze([
            "User-authored content placed inside generated nodes can be recovered.",
            "Generated runtime markup is a stable authoring format.",
            "Runtime-only attributes are safe to persist as source."
          ]),
          failClosedSignal: "serializerClean=false, missing generated parts, or a repair warning",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.generated-runtime-is-discardable.v1",
            "mcel.contract.repair-is-schema-bounded.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.serialization-is-source-firewall.v1",
          label: "Serialization is the source firewall",
          stableSurface: "McelLabEngine.serializeRuntimeRoot and MCEL.serialize.",
          userCanRelyOn: Object.freeze([
            "Serialization removes MCEL-generated nodes.",
            "Serialization strips runtime-owned attributes and MCEL-owned runtime classes.",
            "Source-owned MCEL policy attributes remain available for saving or export."
          ]),
          userMustProvide: Object.freeze([
            "Serialize through MCEL before persisting MCEL-owned runtime DOM.",
            "Block save/export when serializerClean is false.",
            "Treat serialized output as the durable source artifact, not runtime innerHTML."
          ]),
          userMustNotAssume: Object.freeze([
            "Raw runtime innerHTML is safe to save.",
            "Non-MCEL mutations are validated by the serializer.",
            "Serializer success proves product behavior, accessibility, or business correctness."
          ]),
          failClosedSignal: "serializerClean=false or mcel.contract.serializer-cleans-runtime-state.v1 failing",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.serializer-cleans-runtime-state.v1",
            "mcel.contract.browser-facts-are-runtime-only.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.repair-is-bounded-regeneration.v1",
          label: "Repair is bounded regeneration",
          stableSurface: "McelLabEngine.repairRuntimeRoot and MCEL.repair reports.",
          userCanRelyOn: Object.freeze([
            "Repair may rebuild missing MCEL-generated parts from the source schema.",
            "Repair is scoped to MCEL-owned generated structure and runtime-owned state.",
            "Repair reports what it changed instead of silently asserting correctness."
          ]),
          userMustProvide: Object.freeze([
            "Keep source-owned traits present.",
            "Treat repair as regeneration of MCEL-owned runtime structure.",
            "Re-run serialization and proof checks after repair before save/export."
          ]),
          userMustNotAssume: Object.freeze([
            "Repair recovers deleted source-owned semantics.",
            "Repair makes arbitrary DOM valid.",
            "Repair can infer product intent that is absent from source traits."
          ]),
          failClosedSignal: "repair report warnings, failed proof, or failed serializer result",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.generated-runtime-is-discardable.v1",
            "mcel.contract.repair-is-schema-bounded.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.validation-is-evidence-not-trust.v1",
          label: "Validation is evidence, not trust",
          stableSurface: "MCEL audit, proof, adoption-case, a11y, and contract-test reports.",
          userCanRelyOn: Object.freeze([
            "MCEL reports expose failed, uncovered, or warning states.",
            "Adoption is blocked when required evidence is missing.",
            "Contract tests map user-facing claims back to executable guarantee IDs."
          ]),
          userMustProvide: Object.freeze([
            "Treat reports as gates, not badges.",
            "Fail closed when relevant guarantees are failed or uncovered.",
            "Add executable evidence before widening MCEL responsibilities."
          ]),
          userMustNotAssume: Object.freeze([
            "A rendered component is proven safe.",
            "A law module is a user-facing contract by itself.",
            "MCEL is a better replacement without an evidence-gated adoption case."
          ]),
          failClosedSignal: "failed guarantee, uncovered guarantee, adoption hold verdict, or warning event",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.validation-is-reporting-not-trust.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.browser-facts-are-snapshots.v1",
          label: "Browser facts are snapshots",
          stableSurface: "MCEL browser observer, layout proof attributes, and runtime proof reports.",
          userCanRelyOn: Object.freeze([
            "Browser measurements are runtime evidence collected for a specific DOM, viewport, CSS, and content state.",
            "Observed facts may guide diagnostics and repair.",
            "Observed facts are stripped from serialized source."
          ]),
          userMustProvide: Object.freeze([
            "Re-observe after viewport, CSS, content, or DOM changes.",
            "Do not save browser observations as source policies.",
            "Treat browser evidence as time- and environment-scoped."
          ]),
          userMustNotAssume: Object.freeze([
            "A browser fact remains true across devices or later edits.",
            "Observed layout facts are durable source intent.",
            "MCEL can guarantee visual correctness without a current browser observation."
          ]),
          failClosedSignal: "stale or absent browser proof, layout warning, or stripped runtime observation",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.browser-facts-are-runtime-only.v1"
          ])
        }),
        Object.freeze({
          id: "mcel.user.adoption-is-narrow-and-reversible.v1",
          label: "Adoption is narrow and reversible",
          stableSurface: "MCEL adoption case, subsumption lattice, and explicitly selected workflow gates.",
          userCanRelyOn: Object.freeze([
            "MCEL adoption is justified only for a named workflow and named guarantees.",
            "Missing evidence produces a hold verdict instead of a platform-wide claim.",
            "A workflow can opt into MCEL without making MCEL the default platform for unrelated UI."
          ]),
          userMustProvide: Object.freeze([
            "Name the workflow MCEL is allowed to own.",
            "Name the user-space contract clauses required by that workflow.",
            "Keep fallback or rollback behavior for workflows that have not passed the gate."
          ]),
          userMustNotAssume: Object.freeze([
            "MCEL should replace React-like or framework-like concerns globally.",
            "Passing one workflow proves all MCEL domains.",
            "The subsumption lattice is an adoption decision by itself."
          ]),
          failClosedSignal: "hold-until-proof-gates-pass or missing workflow-specific evidence",
          evidenceGuarantees: Object.freeze([
            "mcel.contract.source-intent-is-input.v1",
            "mcel.contract.validation-is-reporting-not-trust.v1"
          ])
        })
      ]);

      function cloneContractGuarantee(guarantee) {
        return Object.freeze({
          id: guarantee.id,
          label: guarantee.label,
          status: guarantee.status,
          scope: guarantee.scope,
          guarantee: guarantee.guarantee,
          absoluteWhen: Object.freeze([...(guarantee.absoluteWhen || [])]),
          nonGuarantees: Object.freeze([...(guarantee.nonGuarantees || [])]),
          failureMode: guarantee.failureMode,
          evidenceTests: Object.freeze([...(guarantee.evidenceTests || [])])
        });
      }

      function cloneUserContractClause(clause) {
        return Object.freeze({
          id: clause.id,
          label: clause.label,
          stableSurface: clause.stableSurface,
          userCanRelyOn: Object.freeze([...(clause.userCanRelyOn || [])]),
          userMustProvide: Object.freeze([...(clause.userMustProvide || [])]),
          userMustNotAssume: Object.freeze([...(clause.userMustNotAssume || [])]),
          failClosedSignal: clause.failClosedSignal,
          evidenceGuarantees: Object.freeze([...(clause.evidenceGuarantees || [])])
        });
      }

      function listContractGuarantees() {
        return Object.freeze(contractGuarantees.map(cloneContractGuarantee));
      }

      function guaranteeById(id) {
        const normalized = String(id || "");
        const guarantee = contractGuarantees.find((item) => item.id === normalized);
        return guarantee ? cloneContractGuarantee(guarantee) : null;
      }

      function listUserContractClauses() {
        return Object.freeze(userSpaceContract.map(cloneUserContractClause));
      }

      function userContractClauseById(id) {
        const normalized = String(id || "");
        const clause = userSpaceContract.find((item) => item.id === normalized);
        return clause ? cloneUserContractClause(clause) : null;
      }

      function buildUserSpaceContract() {
        return Object.freeze({
          kind: "mcel-user-space-contract",
          contractVersion,
          purpose: "The user-facing MCEL contract: what builders can rely on, what they must provide, what they must not assume, and how MCEL fails closed.",
          stableEntrypoints: Object.freeze([
            "McelLabContract.buildUserSpaceContract()",
            "McelLabContract.listUserContractClauses()",
            "McelLabEngine.compileSource(sourceHtml, options)",
            "McelLabEngine.compileDocument(documentOrRoot, options)",
            "McelLabEngine.serializeRuntimeRoot(root, options)",
            "McelLabEngine.repairRuntimeRoot(root, options)",
            "McelLabEngine.runContractTests()",
            "MCEL.compile(sourceHtml, options)",
            "MCEL.serialize(runtimeRootOrHtml, options)",
            "MCEL.repair(runtimeRootOrHtml, options)",
            "MCEL.audit(sourceHtml, runtimeRoot, options)",
            "MCEL.buildAdoptionCase(options)"
          ]),
          clauseCount: userSpaceContract.length,
          evidenceGuaranteeIds: Object.freeze([...new Set(userSpaceContract.flatMap((clause) => clause.evidenceGuarantees || []))]),
          clauses: listUserContractClauses()
        });
      }

      function buildContractEnvelope() {
        return Object.freeze({
          kind: "mcel-contract-envelope",
          contractVersion,
          guaranteeCount: contractGuarantees.length,
          executableGuaranteeIds: Object.freeze(contractGuarantees
            .filter((guarantee) => guarantee.status === "executable")
            .map((guarantee) => guarantee.id)),
          guarantees: listContractGuarantees()
        });
      }

      const modes = Object.freeze(["source", "editor", "runtime", "diff", "stress", "a11y"]);

      const defaults = Object.freeze({
        type: "panel",
        kind: "signal",
        flow: "forward",
        rank: "secondary",
        state: "idle",
        density: "auto",
        sizePolicy: "adaptive",
        overflowPolicy: "contain",
        scrollPolicy: "external"
      });

      const runtimeOwnedAttributes = Object.freeze([
        attributes.enhanced,
        attributes.neighborhood,
        attributes.computedDensity,
        attributes.relation,
        attributes.relationCount,
        attributes.clusterSize,
        attributes.sourceIndex,
        attributes.editorSelected,
        attributes.theme,
        attributes.styleLaw,
        attributes.flowAxis,
        attributes.fieldPressure,
        attributes.attention,
        attributes.relationMode,
        attributes.layoutLaw,
        attributes.overflowComputed,
        attributes.scrollNeeded,
        attributes.scrollOwner,
        attributes.layoutPressure,
        attributes.geometryProof,
        attributes.keyboardScroll,
        attributes.componentLaw,
        attributes.slotLaw,
        attributes.stateLaw,
        attributes.dataLaw,
        attributes.formLaw,
        attributes.actionLaw,
        attributes.renderLaw,
        attributes.a11yLaw,
        attributes.focusLaw,
        attributes.performanceLaw,
        attributes.securityLaw,
        attributes.proofTier,
        attributes.semanticRisk,
        attributes.dependencyMode,
        attributes.hydrationProof,
        attributes.dataFreshness,
        attributes.formValidity,
        attributes.actionSafety,
        attributes.artifactOwner,
        attributes.artifactOrigin,
        attributes.artifactReason,
        attributes.contractVersion
      ]);

      const runtimeOwnedClasses = Object.freeze(["mc", "mcel-selected"]);

      const themes = Object.freeze([
        "theme-machine",
        "theme-local",
        "theme-saas",
        "theme-editorial",
        "theme-luxury",
        "theme-civic",
        "theme-accessible",
        "theme-debug"
      ]);

      const themeAliases = Object.freeze({
        "theme-basic": "theme-local",
        basic: "theme-local",
        local: "theme-local",
        "local-service": "theme-local",
        "small-business": "theme-local",
        machine: "theme-machine",
        original: "theme-machine",
        "original-mcel": "theme-machine",
        launch: "theme-saas",
        startup: "theme-saas",
        saas: "theme-saas",
        product: "theme-saas",
        "theme-article": "theme-editorial",
        article: "theme-editorial",
        editorial: "theme-editorial",
        magazine: "theme-editorial",
        "theme-premium": "theme-luxury",
        premium: "theme-luxury",
        luxury: "theme-luxury",
        portfolio: "theme-luxury",
        civic: "theme-civic",
        nonprofit: "theme-civic",
        public: "theme-civic",
        "theme-accessibility": "theme-accessible",
        accessibility: "theme-accessible",
        accessible: "theme-accessible",
        "high-contrast": "theme-accessible",
        debug: "theme-debug",
        wireframe: "theme-debug"
      });

      const layoutPolicies = Object.freeze({
        size: Object.freeze(["adaptive", "fixed", "fluid", "intrinsic"]),
        overflow: Object.freeze(["visible", "contain", "clip", "delegate", "paginate", "virtualize", "expand", "collapse"]),
        scroll: Object.freeze(["never", "auto", "required", "external", "child-only", "viewport-only"])
      });

      const platformPolicies = Object.freeze({
        componentKinds: Object.freeze(["component", "page", "layout", "island", "primitive", "adapter"]),
        slots: Object.freeze(["title", "body", "actions", "media", "meta", "fallback"]),
        stateOwners: Object.freeze(["none", "element", "view", "session", "server", "url", "worker"]),
        statePolicies: Object.freeze(["immutable", "local", "shared", "derived", "transactional", "replayable"]),
        cachePolicies: Object.freeze(["none", "network-only", "cache-first", "stale-while-revalidate", "content-hash", "session"]),
        syncPolicies: Object.freeze(["none", "manual", "on-visible", "background", "optimistic", "offline-first"]),
        validationPolicies: Object.freeze(["none", "schema", "native", "server", "hybrid"]),
        dirtyPolicies: Object.freeze(["none", "track", "warn", "block", "autosave"]),
        errorPolicies: Object.freeze(["silent", "inline", "summary", "inline-and-summary", "recoverable", "fatal"]),
        swapPolicies: Object.freeze(["none", "replace", "append", "prepend", "lawful-region", "morph", "stream"]),
        eventPolicies: Object.freeze(["none", "local", "delegated", "command", "transaction", "audited"]),
        renderModes: Object.freeze(["static", "server", "client", "island", "stream", "incremental", "edge", "offline"]),
        hydrationPolicies: Object.freeze(["none", "eager", "visible", "idle", "interaction", "islands", "progressive"]),
        islandPolicies: Object.freeze(["none", "static-shell", "interactive", "lazy", "critical", "portable"]),
        focusPolicies: Object.freeze(["auto", "preserve", "trap", "delegate", "restore", "none"]),
        a11yPolicies: Object.freeze(["auto", "strict", "decorative", "interactive", "landmark", "live-region"]),
        performanceBudgets: Object.freeze(["none", "tiny", "small", "medium", "large", "critical"]),
        securityPolicies: Object.freeze(["default", "sandboxed", "trusted", "user-content", "networked", "dangerous"])
      });

      const schema = Object.freeze({
        panel: Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "hero", "article", "proof"]),
          allowedFlows: Object.freeze(["forward", "reverse", "stack", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        feed: Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["signal", "work", "article"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "command-row": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "field"]),
          allowedKinds: Object.freeze(["work", "signal"]),
          allowedFlows: Object.freeze(["forward", "reverse"]),
          allowedRanks: Object.freeze(["secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "proof-surface": Object.freeze({
          generatedParts: Object.freeze(["rail", "copy", "meta", "field"]),
          allowedKinds: Object.freeze(["proof", "signal", "work"]),
          allowedFlows: Object.freeze(["forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        }),
        "smart-region": Object.freeze({
          generatedParts: Object.freeze(["rail", "meta", "field"]),
          allowedKinds: Object.freeze(["article", "signal", "work", "proof"]),
          allowedFlows: Object.freeze(["stack", "forward", "reverse", "split"]),
          allowedRanks: Object.freeze(["primary", "secondary", "minor"]),
          allowedStates: Object.freeze(["idle", "live", "draft", "warning"]),
          allowedDensities: Object.freeze(["auto", "calm", "dense", "compressed"]),
          allowedSizePolicies: layoutPolicies.size,
          allowedOverflowPolicies: layoutPolicies.overflow,
          allowedScrollPolicies: layoutPolicies.scroll,
          allowedComponentKinds: platformPolicies.componentKinds,
          allowedStateOwners: platformPolicies.stateOwners,
          allowedStatePolicies: platformPolicies.statePolicies,
          allowedCachePolicies: platformPolicies.cachePolicies,
          allowedSyncPolicies: platformPolicies.syncPolicies,
          allowedValidationPolicies: platformPolicies.validationPolicies,
          allowedDirtyPolicies: platformPolicies.dirtyPolicies,
          allowedErrorPolicies: platformPolicies.errorPolicies,
          allowedSwapPolicies: platformPolicies.swapPolicies,
          allowedEventPolicies: platformPolicies.eventPolicies,
          allowedRenderModes: platformPolicies.renderModes,
          allowedHydrationPolicies: platformPolicies.hydrationPolicies,
          allowedIslandPolicies: platformPolicies.islandPolicies,
          allowedFocusPolicies: platformPolicies.focusPolicies,
          allowedA11yPolicies: platformPolicies.a11yPolicies,
          allowedPerformanceBudgets: platformPolicies.performanceBudgets,
          allowedSecurityPolicies: platformPolicies.securityPolicies
        })
      });

      const defaultSource = `<main
  data-mc="smart-region"
  data-mc-kind="article"
  data-mc-flow="stack"
  data-mc-rank="primary"
  data-mc-state="live"
  data-mc-density="calm"
  data-mc-size-policy="fluid"
  data-mc-overflow-policy="delegate"
  data-mc-scroll-policy="external"
  data-mc-component="NeighborhoodMarketSite"
  data-mc-component-kind="page"
  data-mc-state-owner="url"
  data-mc-state-policy="replayable"
  data-mc-route="/"
  data-mc-render="static"
  data-mc-hydration="islands"
  data-mc-a11y-policy="strict"
  data-mc-performance-budget="small"
  data-mc-security-policy="trusted"
  data-mc-words="minimal site skeleton emerges from simple semantic html"
>
  <section
    data-mc="panel"
    data-mc-kind="hero"
    data-mc-flow="split"
    data-mc-rank="primary"
    data-mc-state="live"
    data-mc-density="calm"
    data-mc-size-policy="fluid"
    data-mc-overflow-policy="expand"
    data-mc-scroll-policy="never"
    data-mc-component="HeroSection"
    data-mc-component-kind="layout"
    data-mc-render="static"
    data-mc-a11y-policy="strict"
    data-mc-performance-budget="tiny"
    data-mc-words="hero promise call to action"
  >
    <p data-mc-slot="meta">Neighborhood Cluster · Open today</p>
    <h1 data-mc-slot="title">A useful local site from almost plain HTML.</h1>
    <p data-mc-slot="body">MCEL turns simple semantic sections into a resilient product surface with layout, proof, state, actions, forms, and accessibility rules layered on top.</p>
    <p data-mc-slot="actions"><a href="#join" data-mc-action="join-neighborhood" data-mc-event-policy="audited">Join the list</a></p>
  </section>

  <section
    data-mc="feed"
    data-mc-kind="signal"
    data-mc-flow="stack"
    data-mc-rank="secondary"
    data-mc-state="live"
    data-mc-density="auto"
    data-mc-size-policy="fluid"
    data-mc-overflow-policy="expand"
    data-mc-scroll-policy="never"
    data-mc-component="TrustCluster"
    data-mc-component-kind="layout"
    data-mc-a11y-policy="strict"
    data-mc-performance-budget="tiny"
    data-mc-words="cards hours delivery pickup trust"
  >
    <h2>Neighborhood Cluster</h2>
    <article data-mc="panel" data-mc-kind="signal" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="TrustCard" data-mc-component-kind="component" data-mc-words="fresh daily">
      <h3>Fresh daily</h3>
      <p>Simple source becomes a polished card without hand-authored wrapper soup.</p>
    </article>
    <article data-mc="panel" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="idle" data-mc-density="dense" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="TrustCard" data-mc-component-kind="component" data-mc-words="pickup delivery">
      <h3>Pickup + delivery</h3>
      <p>The layout law expands content instead of making each card a scroll trap.</p>
    </article>
    <article data-mc="panel" data-mc-kind="proof" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="dense" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="TrustCard" data-mc-component-kind="component" data-mc-words="proof accessible">
      <h3>Proof visible</h3>
      <p>Runtime facts are generated, inspected, and stripped back out on serialize.</p>
    </article>
  </section>

  <form
    id="join"
    data-mc="smart-region"
    data-mc-kind="work"
    data-mc-flow="split"
    data-mc-rank="secondary"
    data-mc-state="draft"
    data-mc-density="auto"
    data-mc-size-policy="fluid"
    data-mc-overflow-policy="delegate"
    data-mc-scroll-policy="external"
    data-mc-component="SignupForm"
    data-mc-component-kind="island"
    data-mc-state-owner="view"
    data-mc-state-policy="transactional"
    data-mc-submit="lead.create"
    data-mc-validation="native"
    data-mc-dirty-policy="warn"
    data-mc-error-policy="inline-and-summary"
    data-mc-action="signup"
    data-mc-event-policy="audited"
    data-mc-render="island"
    data-mc-hydration="interaction"
    data-mc-focus-policy="preserve"
    data-mc-a11y-policy="strict"
    data-mc-performance-budget="small"
    data-mc-words="signup form validated accessible"
  >
    <h2>Get the weekly market note.</h2>
    <label>Email <input name="email" type="email" required placeholder="you@example.com"></label>
    <button type="submit" data-mc-action="signup" data-mc-event-policy="audited">Notify me</button>
  </form>

  <section
    data-mc="command-row"
    data-mc-kind="work"
    data-mc-flow="forward"
    data-mc-rank="minor"
    data-mc-state="live"
    data-mc-density="compressed"
    data-mc-size-policy="intrinsic"
    data-mc-overflow-policy="clip"
    data-mc-scroll-policy="never"
    data-mc-component="FooterCta"
    data-mc-component-kind="primitive"
    data-mc-action="open-hours"
    data-mc-event-policy="audited"
    data-mc-a11y-policy="strict"
    data-mc-performance-budget="tiny"
    data-mc-words="footer command row hours contact"
  >
    <h2>Open 7am–7pm · 12th and Pine</h2>
    <p>One semantic command row, zero nested scrollbars.</p>
  </section>
</main>`;

      const blockTemplates = Object.freeze({
        panel: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="semantic source html">
  <h2>New Panel</h2>
  <p>Clean MCEL source that can compile into runtime structure.</p>
</section>`,
        hero: `<section data-mc="panel" data-mc-kind="hero" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="calm" data-mc-size-policy="fluid" data-mc-overflow-policy="expand" data-mc-scroll-policy="never" data-mc-words="hero proof surface">
  <h2>Hero Panel</h2>
  <p>A semantic hero panel without generated wrapper pollution.</p>
</section>`,
        signal: `<section data-mc="panel" data-mc-kind="signal" data-mc-flow="reverse" data-mc-rank="primary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="signal argument stream public">
  <h2>Signal Panel</h2>
  <p>A live signal block for public argument streams.</p>
</section>`,
        work: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="secondary" data-mc-state="idle" data-mc-density="calm" data-mc-size-policy="adaptive" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-words="work compiler module">
  <h2>Work Panel</h2>
  <p>A work block for implementation progress.</p>
</section>`,
        feed: `<section data-mc="feed" data-mc-kind="signal" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="live" data-mc-density="dense" data-mc-size-policy="fixed" data-mc-overflow-policy="virtualize" data-mc-scroll-policy="required" data-mc-words="feed stream items">
  <h2>Feed</h2>
  <p>Container source for future feed item intelligence.</p>
</section>`,
        "command-row": `<section data-mc="command-row" data-mc-kind="work" data-mc-flow="forward" data-mc-rank="minor" data-mc-state="idle" data-mc-density="compressed" data-mc-size-policy="intrinsic" data-mc-overflow-policy="clip" data-mc-scroll-policy="never" data-mc-words="command action controls">
  <h2>Command Row</h2>
  <p>Semantic command strip source.</p>
</section>`,
        proof: `<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="paginate" data-mc-scroll-policy="child-only" data-mc-words="proof verification serializer">
  <h2>Proof Surface</h2>
  <p>Runtime and serializer claims should be proven here.</p>
</section>`,
        "smart-region": `<section data-mc="smart-region" data-mc-kind="article" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="fluid" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-words="region neighborhood a11y">
  <h2>Smart Region</h2>
  <p>A general semantic region for neighborhood and accessibility tests.</p>
</section>`,
        component: `<section data-mc="panel" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-component="SemanticComponent" data-mc-component-kind="component" data-mc-state-owner="element" data-mc-state-policy="local" data-mc-render="island" data-mc-hydration="interaction" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-words="component slots props">
  <h2 data-mc-slot="title">Semantic Component</h2>
  <p data-mc-slot="body">Component structure, slots, props, state, events, a11y, and performance are source-level policies.</p>
</section>`,
        form: `<form data-mc="smart-region" data-mc-kind="work" data-mc-flow="stack" data-mc-rank="secondary" data-mc-state="draft" data-mc-density="auto" data-mc-size-policy="adaptive" data-mc-overflow-policy="contain" data-mc-scroll-policy="auto" data-mc-submit="profile.update" data-mc-validation="schema" data-mc-dirty-policy="warn" data-mc-error-policy="inline-and-summary" data-mc-a11y-policy="strict" data-mc-performance-budget="small" data-mc-words="form validation errors">
  <h2>Lawful Form</h2>
  <label>Name <input name="name" required></label>
  <button type="submit" data-mc-action="submit-form" data-mc-event-policy="audited">Save</button>
</form>`,
        route: `<section data-mc="proof-surface" data-mc-kind="proof" data-mc-flow="split" data-mc-rank="primary" data-mc-state="live" data-mc-density="auto" data-mc-size-policy="fluid" data-mc-overflow-policy="delegate" data-mc-scroll-policy="external" data-mc-route="/mcel/proof" data-mc-render="stream" data-mc-hydration="islands" data-mc-cache-policy="content-hash" data-mc-security-policy="trusted" data-mc-performance-budget="medium" data-mc-words="route render hydration">
  <h2>Semantic Route</h2>
  <p>Routing, rendering, hydration, caching, and security are source policies with runtime proof.</p>
</section>`
      });

      return Object.freeze({
        attributes,
        modes,
        defaults,
        runtimeOwnedAttributes,
        runtimeOwnedClasses,
        themes,
        themeAliases,
        layoutPolicies,
        platformPolicies,
        contractVersion,
        contractGuarantees,
        userSpaceContract,
        listContractGuarantees,
        guaranteeById,
        listUserContractClauses,
        userContractClauseById,
        buildUserSpaceContract,
        buildContractEnvelope,
        schema,
        defaultSource,
        blockTemplates
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabContract = McelLabContract;
    }
