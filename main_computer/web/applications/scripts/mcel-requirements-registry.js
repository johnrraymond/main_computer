(() => {
  (function createMcelRequirementsRegistry(global) {
    if (!global) return;

    const PAYLOAD = Object.freeze({
  "app_comparison_seeds": {
    "calculator": {
      "app": "calculator",
      "current_runtime_status": "domain-ready-planner-plus-domain-pack",
      "declared_form_primitive_count": 6,
      "mutation_intent_count": 1,
      "prohibited_intent_count": 0,
      "required_intent_count": 10,
      "required_region_count": 11,
      "required_use_case_count": 1,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "full-application-semantic-runtime"
    },
    "code-editor": {
      "app": "code-editor",
      "current_runtime_status": "structural-workbench-with-domain-enrichment",
      "declared_form_primitive_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "required_intent_count": 7,
      "required_region_count": 7,
      "required_use_case_count": 2,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "full-application-semantic-runtime"
    },
    "file-explorer": {
      "app": "file-explorer",
      "current_runtime_status": "domain-ready-read-only-planner-plus-domain-pack",
      "declared_form_primitive_count": 6,
      "mutation_intent_count": 3,
      "prohibited_intent_count": 3,
      "required_intent_count": 11,
      "required_region_count": 7,
      "required_use_case_count": 2,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "full-read-only-semantic-runtime"
    },
    "git-tools": {
      "app": "git-tools",
      "current_runtime_status": "scope-limited-semantic-runtime",
      "declared_form_primitive_count": 6,
      "mutation_intent_count": 5,
      "prohibited_intent_count": 1,
      "required_intent_count": 10,
      "required_region_count": 8,
      "required_use_case_count": 4,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "full-application-semantic-runtime"
    },
    "mcel-lab": {
      "app": "mcel-lab",
      "current_runtime_status": "structural-only",
      "declared_form_primitive_count": 9,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "required_intent_count": 7,
      "required_region_count": 7,
      "required_use_case_count": 2,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "scope-limited-semantic-runtime"
    },
    "website-builder": {
      "app": "website-builder",
      "current_runtime_status": "working-app-plus-site-project-model",
      "declared_form_primitive_count": 6,
      "mutation_intent_count": 8,
      "prohibited_intent_count": 0,
      "required_intent_count": 12,
      "required_region_count": 10,
      "required_use_case_count": 4,
      "requirements_contract_complete": true,
      "requirements_contract_present": true,
      "runtime_comparison_status": "pending-live-adapter-snapshot",
      "target_runtime_status": "full-application-semantic-runtime"
    }
  },
  "app_contracts": {
    "calculator": {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 10,
        "target_adapter_status:executable": 10
      },
      "app": "calculator",
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 10,
        "mcel-region": 11,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 1
      },
      "contract_complete": true,
      "current_runtime_status": "domain-ready-planner-plus-domain-pack",
      "dominant_object": "CalculationSession",
      "first_regions": [
        {
          "id": "calculator.region.mode-toolbar",
          "region": "mode-switcher-toolbar",
          "responsibility": "Own mode selection between arithmetic and scientific/graphing surfaces without evaluating expressions or hiding the user's current calculation context.",
          "role": "mode-switcher",
          "status": "specified"
        },
        {
          "id": "calculator.region.arithmetic-panel",
          "region": "primary-calculation-surface",
          "responsibility": "Own the ordinary arithmetic workflow by keeping expression input, local actions, and deterministic result evidence visually connected.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "calculator.region.expression-display",
          "region": "expression-input-display",
          "responsibility": "Show the current arithmetic expression as authoritative calculator input, separate from graph output, Mathics prompts, and model prose.",
          "role": "input-display",
          "status": "specified"
        },
        {
          "id": "calculator.region.keypad",
          "region": "deterministic-input-grid",
          "responsibility": "Provide local digit, operator, edit, and equals actions that mutate only the current arithmetic expression and deterministic result state.",
          "role": "action-grid",
          "status": "specified"
        },
        {
          "id": "calculator.region.result-status",
          "region": "result-evidence-status",
          "responsibility": "Show success, error, graph, and symbolic evaluation status near the calculator surface that produced the evidence.",
          "role": "evidence-status",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Calculation identity must remain traceable across evaluate, graph, ask, and symbolic helper actions.",
            "Helper evidence must not mutate the canonical expression or result without an explicit user action.",
            "No calculation subject may imply filesystem, repository, package, or shell mutation."
          ],
          "id": "calculator.form.subject.calculation-session",
          "meaning": "The active calculation scenario, including expressions, graph inputs, symbolic requests, result history, and explanation context.",
          "primitive": "subject",
          "relationships": [
            "Arithmetic expressions, graph inputs, symbolic requests, and result explanations belong to the same calculation session subject.",
            "Deterministic numeric result evidence remains canonical for computed answers.",
            "Model explanations and symbolic evaluations are derived evidence, not silent replacements for computed results."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Evaluation and graphing stay local and deterministic.",
            "Symbolic/model helpers run only through explicit helper actions.",
            "Failed parsing or evaluation must produce visible feedback instead of mutating unrelated state."
          ],
          "id": "calculator.form.action.evaluate-and-explain",
          "meaning": "The user asks Calculator to evaluate expressions, draw graphs, request symbolic results, or explain deterministic output.",
          "primitive": "action",
          "relationships": [
            "Evaluation derives result evidence from the active calculation session.",
            "Graphing derives visual evidence from expression and range state.",
            "Explanation actions must cite or preserve the deterministic result they explain."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary compute surface must remain visible and usable while Calculator is active.",
            "Derived helper output must not claim authority over deterministic result evidence.",
            "Transient helper activity must not obscure the calculation path beyond its explicit operation."
          ],
          "id": "calculator.form.work-surface.deterministic-compute",
          "meaning": "The primary stable work surface where expression input, numeric result evidence, graph output, and helper results remain tied to the active calculation session.",
          "primitive": "work-surface",
          "relationships": [
            "Enables expression evaluation, graph inspection, sample comparison, symbolic helper use, and result explanation.",
            "Keeps computed result evidence authoritative over helper prose.",
            "Presents derived graph or helper evidence as part of the same calculation task."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must remain subordinate to deterministic result evidence.",
            "Parse and validation context must identify the affected input or operation.",
            "Explanation context must not hide whether the result came from local evaluation, symbolic evaluation, or model help."
          ],
          "id": "calculator.form.context.result-evidence",
          "meaning": "Supporting context that explains formulas, ranges, history, parse state, graph evidence, and helper outputs for the active calculation session.",
          "primitive": "context",
          "relationships": [
            "Explains why a result, graph, symbolic response, or model explanation belongs to the current calculation.",
            "Connects validation failures to the input or helper action that produced them.",
            "Helps users compare values without changing the calculation subject."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not interrupt ordinary calculation unless an operation fails or becomes unsafe.",
            "Feedback must not cover or replace the primary compute surface.",
            "Feedback must distinguish current active issues from historical or resolved issues."
          ],
          "id": "calculator.form.feedback.validation-and-compute-state",
          "meaning": "Ambient and noticeable feedback about parse validity, compute success, graph readiness, helper status, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes evaluation state, validation failures, helper activity, and runtime integrity.",
            "Supports user, developer, and automation audiences without changing the calculation session.",
            "Can be summarized compactly or expanded into findings when investigation is needed."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Helper transients require user initiation or a visible lifecycle trigger.",
            "Helper transients must preserve the active calculation subject and deterministic result evidence.",
            "Helper transients must not perform hidden filesystem, repository, network-publish, package, or shell operations."
          ],
          "id": "calculator.form.transient.explicit-helper-evaluation",
          "meaning": "Temporary helper activity for symbolic evaluation, model explanation, graph redraw, or validation recovery.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit helper actions without becoming the calculation session itself.",
            "May produce derived evidence, receipts, warnings, or recovery instructions.",
            "Ends when the helper action resolves, is dismissed, or is superseded by a new calculation action."
          ],
          "status": "specified"
        }
      ],
      "id": "calculator",
      "intent_count": 10,
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 9
      },
      "mutation_intent_count": 1,
      "open_finding_count": 3,
      "planned_or_open_count": 47,
      "primary_user_goal": "Enter arithmetic expressions, inspect results, draw graphs, run explicit symbolic evaluations, and ask contextual questions without hidden filesystem, remote-sync, or command-execution side effects.",
      "prohibited_intent_count": 0,
      "region_count": 11,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-primary-workspace",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 96,
        "file": "pretty_docs/mcel-calculator-requirements.md",
        "start_line": 71
      },
      "status": "specified",
      "status_counts": {
        "draft": 1,
        "open": 3,
        "planned": 2,
        "specified": 41
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Calculator",
      "use_cases": [
        {
          "goal": "Compare two monthly pricing formulas, identify the break-even point, inspect sample values, plot the relationship, and explain the result without leaving Calculator.",
          "id": "calculator.use-case.compare-monthly-costs",
          "status": "draft"
        }
      ]
    },
    "code-editor": {
      "adapter_status_counts": {},
      "app": "code-editor",
      "block_type_counts": {
        "mcel-acceptance": 1,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 7,
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 8,
        "mcel-runtime-check": 5,
        "mcel-source-binding": 2,
        "mcel-test-binding": 2,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "structural-workbench-with-domain-enrichment",
      "dominant_object": "SourceWorkspace",
      "first_regions": [
        {
          "id": "code-editor.region.identity",
          "region": "identity",
          "responsibility": "Identify the active workspace, route, active file, dirty state, runtime version, gate status, and persistence state.",
          "role": "identity-header",
          "status": "specified"
        },
        {
          "id": "code-editor.region.navigation",
          "region": "navigation",
          "responsibility": "Let the user choose files, project context, open editors, and selected-file sets without applying patches or executing commands.",
          "role": "project-navigation",
          "status": "specified"
        },
        {
          "id": "code-editor.region.primary",
          "region": "primary",
          "responsibility": "Own the selected-file editor, draft review, concrete diffs, and explicit preview modes while preventing supporting tools from becoming the source of truth.",
          "role": "primary-authoring-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.inspector",
          "region": "supporting-reasoning-evidence-projection",
          "responsibility": "Project optional reasoning, evidence, diagnostics, Aider context, SCM manifests, source ownership, test ownership, documentation references, and action-specific preflight information without becoming the primary editor. A desktop renderer may currently place this projection beside the editor, but MCEL treats that placement as layout inference rather than the requirement.",
          "role": "secondary-context-and-feedback-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.evidence",
          "region": "evidence",
          "responsibility": "Show Aider output, SCM evidence, contract reports, regression results, receipts, and recovery guidance for reviewed actions.",
          "role": "evidence-and-receipts-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 7,
      "form_primitives": [
        {
          "constraints": [
            "Author-owned source remains canonical.",
            "Runtime chrome and generated helper surfaces must not become saved source.",
            "Selection identity must remain visible enough to anchor editing and review."
          ],
          "id": "code-editor.form.subject.source-workspace",
          "meaning": "The project/workspace source tree and selected source file that the app helps inspect, edit, and safely change.",
          "primitive": "subject",
          "relationships": [
            "Selected file is part of the source workspace.",
            "Source text, diagnostics, SCM evidence, and Aider context derive from the selected workspace subject.",
            "Generated runtime or proof artifacts are derived evidence, not canonical source."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Preview, suggestion, diagnosis, and review are not writes.",
            "Save/apply/execute/remote mutation require explicit intents and receipts.",
            "Read-only Aider requests cannot mutate files."
          ],
          "id": "code-editor.form.action.edit-source",
          "meaning": "Inspect and change selected source text while preserving explicit save, patch, execution, and remote-mutation boundaries.",
          "primitive": "action",
          "relationships": [
            "Acts on code-editor.form.subject.source-workspace.",
            "Uses code-editor.form.work-surface.selected-source-editor as the authoritative work surface.",
            "May consume supporting context, evidence, and feedback without allowing those projections to mutate source implicitly."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must remain visible and usable in authoring mode.",
            "Must not be covered, replaced, or out-ranked by supporting context, feedback, proof, preview, or diagnostic projections.",
            "Must preserve selected-path and dirty-state evidence."
          ],
          "id": "code-editor.form.work-surface.selected-source-editor",
          "meaning": "The authoritative stable surface where the selected file's source text is edited.",
          "primitive": "work-surface",
          "relationships": [
            "Enables code-editor.form.action.edit-source.",
            "Represents the selected file from code-editor.form.subject.source-workspace.",
            "May be implemented by Monaco or a mode-gated fallback, but exactly one editor surface may hold primary authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must not claim primary editor authority.",
            "Must not obscure the selected source editor below usable geometry.",
            "Must keep the current selected subject traceable when file-backed editing is active."
          ],
          "id": "code-editor.form.context.project-selection",
          "meaning": "Supporting context that lets the user choose, understand, and compare source workspace subjects.",
          "primitive": "context",
          "relationships": [
            "Selects or explains the active source workspace/file subject.",
            "Supports editing, review, SCM evidence, and Aider context gathering.",
            "May project through any selection affordance that preserves subject identity and editing flow."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must not become the selected-file editor.",
            "Must not leak as an unowned overlay over the primary work surface.",
            "Must remain distinguishable from canonical source and from write/apply controls."
          ],
          "id": "code-editor.form.context.reasoning-evidence",
          "meaning": "Supporting explanation, evidence, diagnostics, ownership hints, documentation references, and Aider context that help reason about the selected source subject or proposed action.",
          "primitive": "context",
          "relationships": [
            "Observes or explains source text, diagnostics, requirements, SCM evidence, Aider plans, and test/source ownership.",
            "May be available on demand, adjacent, tabbed, collapsed, or deferred by layout inference.",
            "Shares viewport with the primary work surface only when it preserves primary authority and geometry."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Ambient feedback must not interrupt or cover the primary work surface.",
            "Noticeable or corrective feedback must identify the condition it observes.",
            "Feedback projections must be owned so they are not reported as random overlays."
          ],
          "id": "code-editor.form.feedback.integrity-and-activity",
          "meaning": "Signals about app integrity, contract health, dirty/save state, policy gates, activity, failures, receipts, and recovery posture.",
          "primitive": "feedback",
          "relationships": [
            "Observes the source workspace, editor usability, runtime contract, action lifecycle, and persistence state.",
            "May render as status text, badges, counters, inline findings, panels, or machine-readable reports.",
            "Supports users, developers, and automation without defining a physical slot."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Active widget editor panes, selections, and dock previews are forbidden in normal authoring mode.",
            "The inert widget-editor root is not itself a visible work surface.",
            "Transient structure-editing UI must identify its mode and owner when visible."
          ],
          "id": "code-editor.form.transient.widget-structure-editing",
          "meaning": "Temporary structure-editing UI used only while an explicit widget or layout editing mode is active.",
          "primitive": "transient",
          "relationships": [
            "Supports structural editing operations rather than ordinary source editing.",
            "May cover or annotate the app only while its explicit mode is active.",
            "Is shell/tool infrastructure when inert and a transient projection when active."
          ],
          "status": "specified"
        }
      ],
      "id": "code-editor",
      "intent_count": 7,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 2,
        "local-state": 1,
        "read-only": 3
      },
      "mutation_intent_count": 4,
      "open_finding_count": 3,
      "planned_or_open_count": 44,
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 5,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-primary-monaco",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-required-regions",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "secondary-surface-policy",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
          "mode": "authoring",
          "severity": "warning",
          "status": "specified"
        },
        {
          "check": "forbidden-surfaces-hidden",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "lifecycle-contract-preserved",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-lifecycle",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 45,
        "file": "pretty_docs/mcel-code-editor-requirements.md",
        "start_line": 18
      },
      "status": "specified",
      "status_counts": {
        "open": 3,
        "planned": 7,
        "specified": 34
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Code Editor / MCEL Code Studio",
      "use_cases": [
        {
          "goal": "Prepare an AI-assisted source change, inspect the proposed diff and affected files, apply only approved edits, and preserve author control over every source mutation.",
          "id": "code-editor.use-case.review-apply-ai-source-change",
          "status": "planned"
        },
        {
          "goal": "Select an author-owned project file, edit it safely, save it explicitly, and preserve visible evidence about the path, dirty state, and saved result.",
          "id": "code-editor.use-case.edit-save-source-file",
          "status": "planned"
        }
      ]
    },
    "file-explorer": {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 8,
        "current_adapter_status:prohibited": 3,
        "target_adapter_status:executable": 7,
        "target_adapter_status:preflight-only": 1,
        "target_adapter_status:prohibited": 3
      },
      "app": "file-explorer",
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 11,
        "mcel-region": 7,
        "mcel-requirement": 9,
        "mcel-runtime-check": 3,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "domain-ready-read-only-planner-plus-domain-pack",
      "dominant_object": "FileEntry",
      "first_regions": [
        {
          "id": "file-explorer.layout.identity",
          "region": "roots-panel-header",
          "responsibility": "Identify the app as File Explorer, describe read-only system browsing, and expose the global status line.",
          "role": "identity",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.roots",
          "region": "roots-sidebar",
          "responsibility": "Show selectable trusted roots such as workspace, debug-root, cwd, home, workspace-parent, filesystem-root, drive roots, or configured mounted Windows roots.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.path-toolbar",
          "region": "path-and-search-toolbar",
          "responsibility": "Show the current root-relative browsing scope and provide bounded search/up navigation within that scope.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.directory-list",
          "region": "directory-listing",
          "responsibility": "Present the current directory or search result set as the primary selectable collection, with directories before files and enough metadata to choose a preview or handoff target.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.preview",
          "region": "preview-panel",
          "responsibility": "Show selected entry metadata, preview content when safe, preview-denied reasons when unsafe, category evidence, and suggested app evidence.",
          "role": "inspector",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Root and path boundaries remain explicit for every selected entry.",
            "Relative traversal cannot escape the selected browse scope.",
            "Read-only browsing must not imply delete, move, rename, write, Git, upload, download, or shell authority."
          ],
          "id": "file-explorer.form.subject.browse-scope",
          "meaning": "The selected trusted root, current path, directory entry set, selected entry, previewable content, and mounted-root evidence.",
          "primitive": "subject",
          "relationships": [
            "The selected entry belongs to the selected root and current path scope.",
            "Preview content, metadata, category, and suggested app derive from the selected entry.",
            "Mounted-root evidence explains when a displayed path is backed by a host path mapping."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Inspection actions are read-only.",
            "Preview failures must report the reason instead of attempting mutation.",
            "Handoff suggestions must not open, write, stage, publish, or execute without a separate explicit app action."
          ],
          "id": "file-explorer.form.action.inspect-entry-safely",
          "meaning": "The user selects roots, searches within scope, chooses entries, previews readable content, and decides handoff without mutating files.",
          "primitive": "action",
          "relationships": [
            "Search and selection operate within the active browse scope.",
            "Preview derives evidence from the selected entry and documented preview limits.",
            "Handoff suggestions connect entry category to another Main Computer app."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary inspection surface must remain visible and usable while browsing.",
            "Preview evidence must stay tied to the selected entry.",
            "Read-only status must remain visible enough to prevent accidental mutation assumptions."
          ],
          "id": "file-explorer.form.work-surface.entry-inspection",
          "meaning": "The primary stable work surface for browsing entries, selecting a file or folder subject, inspecting metadata, and viewing safe preview evidence.",
          "primitive": "work-surface",
          "relationships": [
            "Enables root selection, scoped search, directory entry inspection, metadata preview, content preview, and app-handoff reasoning.",
            "Keeps selected entry identity connected to preview and classification evidence.",
            "Preserves read-only status as part of the inspection task."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must not claim file mutation authority.",
            "Category and suggested-app evidence must be distinguishable from the file contents themselves.",
            "Missing or unreadable preview must produce explicit evidence, not blank ambiguity."
          ],
          "id": "file-explorer.form.context.selection-and-classification",
          "meaning": "Supporting context that explains current root, path, selected entry, metadata, category, suggested app, and preview availability.",
          "primitive": "context",
          "relationships": [
            "Explains why an entry is classified as code, text, spreadsheet, game, asset, binary, oversized, or other.",
            "Connects preview availability to size, type, readability, and safety limits.",
            "Connects selected entries to possible downstream app handoff."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not interrupt ordinary browsing unless a boundary, preview, or safety rule is violated.",
            "Feedback must not cover or replace the primary inspection surface.",
            "Feedback must identify the affected root, path, entry, or operation when possible."
          ],
          "id": "file-explorer.form.feedback.boundary-and-preview-state",
          "meaning": "Feedback about selected scope, read-only status, search state, preview readiness, preview failure, mounted-root status, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes browse scope, selected entry, preview limits, search progress, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automated contract checking.",
            "Distinguishes active browse problems from historical or resolved findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient evidence must remain bounded to the active browse scope.",
            "Transient evidence must not imply mutation or permission escalation.",
            "Transient evidence must not obscure root, path, selected-entry, or read-only identity."
          ],
          "id": "file-explorer.form.transient.search-and-selection-evidence",
          "meaning": "Temporary evidence created by search, selection change, preview loading, classification refresh, or handoff consideration.",
          "primitive": "transient",
          "relationships": [
            "Supports the active inspect-entry action without becoming persistent file state.",
            "May highlight a selection, search result, classification change, or preview-loading lifecycle.",
            "Ends when the selection, query, preview, or handoff consideration changes."
          ],
          "status": "specified"
        }
      ],
      "id": "file-explorer",
      "intent_count": 11,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-state": 1,
        "prohibited": 1,
        "read-only": 7
      },
      "mutation_intent_count": 3,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
      "primary_user_goal": "Browse trusted roots, inspect directory contents, search within a bounded scope, preview readable files, classify entries, and hand off chosen files to the right Main Computer app without hidden filesystem, Git, remote, or command side effects.",
      "prohibited_intent_count": 3,
      "region_count": 7,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-primary-surface",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 138,
        "file": "pretty_docs/mcel-file-explorer-requirements.md",
        "start_line": 112
      },
      "status": "specified",
      "status_counts": {
        "draft": 2,
        "open": 3,
        "planned": 3,
        "prohibited": 3,
        "specified": 33
      },
      "target_runtime_status": "full-read-only-semantic-runtime",
      "title": "File Explorer",
      "use_cases": [
        {
          "goal": "Browse the current workspace, search for a known file, inspect its metadata and preview content, and decide which Main Computer app should handle it without mutating the filesystem or repository.",
          "id": "file-explorer.use-case.inspect-project-file-safely",
          "status": "draft"
        },
        {
          "goal": "Browse a configured mounted Windows drive through File Explorer while preserving root boundaries, display-path evidence, and read-only behavior.",
          "id": "file-explorer.use-case.browse-mounted-windows-drive",
          "status": "draft"
        }
      ]
    },
    "git-tools": {
      "adapter_status_counts": {
        "current_adapter_status:declared-only": 3,
        "current_adapter_status:executable": 2,
        "current_adapter_status:not-registered": 3,
        "current_adapter_status:preflight-only": 1,
        "current_adapter_status:prohibited": 1
      },
      "app": "git-tools",
      "block_type_counts": {
        "mcel-acceptance": 5,
        "mcel-app": 1,
        "mcel-finding": 4,
        "mcel-form-primitive": 6,
        "mcel-intent": 10,
        "mcel-region": 8,
        "mcel-requirement": 11,
        "mcel-runtime-check": 3,
        "mcel-use-case": 4
      },
      "contract_complete": true,
      "current_runtime_status": "scope-limited-semantic-runtime",
      "dominant_object": "RepositoryProject",
      "first_regions": [
        {
          "id": "git-tools.region.identity",
          "region": "identity",
          "responsibility": "Identify the selected project, repository root, branch, remote target, backend freshness, and semantic runtime scope.",
          "role": "repository-identity-header",
          "status": "specified"
        },
        {
          "id": "git-tools.region.navigation",
          "region": "navigation",
          "responsibility": "Let the user choose projects, workflow tabs, file baskets, patch inventory views, and support areas without mutating Git state.",
          "role": "repository-navigation",
          "status": "specified"
        },
        {
          "id": "git-tools.region.primary",
          "region": "primary",
          "responsibility": "Own the selected repository workflow, changed-file triage, project publishing strip, status summary, and commit/publish content.",
          "role": "repository-workbench",
          "status": "specified"
        },
        {
          "id": "git-tools.region.inspector",
          "region": "inspector",
          "responsibility": "Show remote configuration, selected-file evidence, ignore-rule previews, policy gates, and action-specific confirmation details.",
          "role": "preflight-inspector",
          "status": "specified"
        },
        {
          "id": "git-tools.region.evidence",
          "region": "evidence",
          "responsibility": "Show status API output, semantic adapter evidence, intent coverage, receipts, backend errors, and recovery plans.",
          "role": "evidence-and-recovery-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Repository identity, branch, and remote target must remain traceable before any mutation.",
            "Local evidence, remote evidence, and planned actions must not be conflated.",
            "Raw Git details may support evidence but must not become hidden default authority."
          ],
          "id": "git-tools.form.subject.repository-project",
          "meaning": "The selected repository project, branch, remote, working-tree evidence, file basket, patch inventory, ignore rules, secrets filters, and publish target.",
          "primitive": "subject",
          "relationships": [
            "Branch, remote, status, diff, staged intent, publish target, and receipts belong to the selected repository project.",
            "Patch inventory and file basket evidence derive from repository state but must remain distinguishable from executed Git actions.",
            "Publishing evidence connects repository state to an explicit governed target."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Commit and push remain separate actions.",
            "Mutation actions require explicit target evidence and confirmation.",
            "Failed actions must produce recovery evidence without pretending repository or remote state changed."
          ],
          "id": "git-tools.form.action.governed-repository-change",
          "meaning": "The user inspects repository state, selects files, stages intent, commits, edits ignore/filter rules, or publishes through governed preflight and receipt flow.",
          "primitive": "action",
          "relationships": [
            "Read actions gather status, branch, remote, diff, patch, and file evidence.",
            "Mutation actions require preflight, explicit confirmation, execution boundary, and receipt.",
            "Recovery actions derive from failed preflight, failed execution, or stale repository evidence."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary repository workflow surface must remain visible and usable.",
            "Mutation controls must remain tied to current repository, branch, target, and preflight evidence.",
            "Evidence views must not silently execute Git commands."
          ],
          "id": "git-tools.form.work-surface.repository-workflow",
          "meaning": "The primary stable work surface for repository triage, status review, file selection, commit preparation, governed publish actions, and recovery.",
          "primitive": "work-surface",
          "relationships": [
            "Enables repository selection, status refresh, file-basket review, patch inventory review, commit preparation, ignore/filter editing, publish preflight, and recovery.",
            "Keeps evidence, intended mutation, confirmation, execution, and receipt connected.",
            "Presents advanced Git details as supporting evidence rather than default authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must not hide the distinction between proposed and executed changes.",
            "Command preview must remain evidence until the user confirms execution.",
            "Receipts must name the affected repository, branch, remote, or target when available."
          ],
          "id": "git-tools.form.context.evidence-and-preflight",
          "meaning": "Supporting context that explains branch, remote, status, diff, staged intent, ignore/filter effects, publish target, command preview, receipts, and recovery paths.",
          "primitive": "context",
          "relationships": [
            "Explains what evidence supports a commit, ignore change, filter change, push, or publish operation.",
            "Connects stale, missing, or conflicting evidence to preflight failures.",
            "Connects receipts and recovery suggestions to the operation that produced them."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not make a mutation appear successful without a matching receipt.",
            "Feedback must not cover or replace the primary repository workflow surface.",
            "High-risk or failed operations may demand attention but must remain tied to recovery evidence."
          ],
          "id": "git-tools.form.feedback.risk-and-operation-state",
          "meaning": "Feedback about repository freshness, dirty state, staged intent, preflight readiness, confirmation requirement, execution result, recovery state, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes repository evidence, action risk, preflight state, execution state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing repository state.",
            "Distinguishes active blockers from resolved or historical findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient mutation UI requires a clear trigger and action target.",
            "Transient evidence must preserve repository, branch, remote, and target identity.",
            "Transient recovery must not perform a follow-up mutation without another explicit action."
          ],
          "id": "git-tools.form.transient.confirmation-and-recovery",
          "meaning": "Temporary confirmation, preflight, execution-progress, command-preview, receipt, and recovery evidence around governed Git and publishing actions.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit mutation or recovery actions without becoming repository state itself.",
            "May demand attention when action risk, missing evidence, conflict, or failure requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches repository subject."
          ],
          "status": "specified"
        }
      ],
      "id": "git-tools",
      "intent_count": 10,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-repository-mutation": 1,
        "read-only": 5,
        "remote-mutation": 2
      },
      "mutation_intent_count": 5,
      "open_finding_count": 4,
      "planned_or_open_count": 44,
      "primary_user_goal": "Inspect repository state, triage files, create safe commits, and publish selected project work through governed Git/Gitea actions without exposing raw Git plumbing as the default user path.",
      "prohibited_intent_count": 1,
      "region_count": 8,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-primary-workflow",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 57,
        "file": "pretty_docs/mcel-git-tools-requirements.md",
        "start_line": 18
      },
      "status": "specified",
      "status_counts": {
        "implemented": 1,
        "open": 4,
        "partially-implemented": 5,
        "planned": 12,
        "prohibited": 1,
        "specified": 28
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Git Tools",
      "use_cases": [
        {
          "goal": "Inspect repository, branch, and remote evidence, confirm the intended local Gitea target, push the current branch explicitly, and receive success or recovery evidence.",
          "id": "git-tools.use-case.push-current-branch-local-gitea",
          "status": "partially-implemented"
        },
        {
          "goal": "Select an untracked file or directory, preview the proposed .gitignore rule, understand whether the target is already tracked, apply the ignore change, and refresh repository evidence.",
          "id": "git-tools.use-case.add-ignore-rule",
          "status": "planned"
        },
        {
          "goal": "Inspect the current branch, available branch targets, and dirty working-tree state, then switch branches only when local work is safe or explicitly handled.",
          "id": "git-tools.use-case.switch-branch-safely",
          "status": "planned"
        },
        {
          "goal": "Inspect changed files, preview diffs, select the files that belong together, stage only those files, write a commit message, create the commit, and keep unselected changes untouched.",
          "id": "git-tools.use-case.select-files-stage-commit",
          "status": "planned"
        }
      ]
    },
    "mcel-lab": {
      "adapter_status_counts": {},
      "app": "mcel-lab",
      "block_type_counts": {
        "mcel-acceptance": 1,
        "mcel-app": 1,
        "mcel-finding": 1,
        "mcel-form-primitive": 9,
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 7,
        "mcel-runtime-check": 4,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "structural-only",
      "dominant_object": "AppBlueprint",
      "first_regions": [
        {
          "id": "mcel-lab.region.app-root",
          "region": "lab-app-root",
          "responsibility": "Owns the MCEL Lab application boundary and exposes the selected AppBlueprint as the dominant object.",
          "role": "app-boundary",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.selection-context",
          "region": "app-and-aspect-selection-context",
          "responsibility": "Projects app and aspect selection primitives without making their physical placement normative.",
          "role": "supporting-context",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.aspect-map",
          "region": "aspect-map-projection",
          "responsibility": "Exposes inspectable blueprint aspects and keeps the selected aspect traceable.",
          "role": "navigation-context",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.blueprint-workspace",
          "region": "blueprint-inspection-workspace",
          "responsibility": "Projects the selected AppBlueprint aspect and mounted preview evidence as the main inspection workspace.",
          "role": "primary-work-surface",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.mounted-preview",
          "region": "mounted-app-preview-projection",
          "responsibility": "Shows a contained app preview as evidence while preserving AppBlueprint authority.",
          "role": "implementation-evidence-context",
          "status": "partially-implemented"
        }
      ],
      "form_primitive_count": 9,
      "form_primitives": [
        {
          "constraints": [
            "AppBlueprint remains the dominant object even when a mounted app preview is visible.",
            "Prose, hardcoded JS blueprints, annotations, and runtime evidence must be distinguishable as separate evidence sources.",
            "Self-hosting inspection must not imply permission to rewrite the live Lab implementation."
          ],
          "id": "mcel-lab.form.subject.app-blueprint",
          "meaning": "The selected app contract being inspected, validated, annotated, or prepared for repair.",
          "primitive": "subject",
          "relationships": [
            "Owns app identity, object model, workflows, layout bindings, action policy, evidence, source/test bindings, annotations, findings, and repair plans.",
            "May represent MCEL Lab itself as a self-hosting target.",
            "Is loaded from documentation, blueprint core data, annotations, and runtime evidence."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Inspection is read-oriented until the user explicitly creates or edits an annotation draft.",
            "Aspect navigation must not replace the selected AppBlueprint as the dominant object.",
            "Findings must distinguish documented intent from verified runtime facts."
          ],
          "id": "mcel-lab.form.action.inspect-blueprint",
          "meaning": "Select an app and aspect, inspect the semantic contract and compare it with implementation evidence.",
          "primitive": "action",
          "relationships": [
            "Acts on mcel-lab.form.subject.app-blueprint.",
            "Uses the blueprint inspection work surface as the authoritative workspace.",
            "Consumes supporting implementation evidence, selected-element evidence, validation feedback, and annotations."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must remain visible and usable when MCEL Lab is active.",
            "Must keep selected app, selected aspect, and mounted route evidence traceable.",
            "Must not be covered or out-ranked by unowned feedback, transient overlays, or debug/proof internals."
          ],
          "id": "mcel-lab.form.work-surface.blueprint-inspection",
          "meaning": "The stable surface where the selected AppBlueprint aspect, mounted preview, selected evidence, and repair context are inspected.",
          "primitive": "work-surface",
          "relationships": [
            "Enables mcel-lab.form.action.inspect-blueprint.",
            "Represents the selected AppBlueprint and current aspect.",
            "Hosts mounted app preview evidence without granting that preview primary Lab authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must keep the selected app and aspect recoverable from visible UI or machine-readable state.",
            "Must not claim primary work-surface authority.",
            "Must not make physical placement part of the semantic contract."
          ],
          "id": "mcel-lab.form.context.app-and-aspect-selection",
          "meaning": "Supporting context that chooses which AppBlueprint and which aspect are being inspected.",
          "primitive": "context",
          "relationships": [
            "Selects the active subject for the blueprint inspection work surface.",
            "Filters the visible evidence, annotations, findings, and repair context.",
            "May render as controls, lists, command choices, tabs, or another inferred projection."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Evidence must identify its source and freshness when it is used to justify a finding.",
            "Implementation evidence must not be confused with the target requirement itself.",
            "Derived repair context must remain reviewable before patch generation."
          ],
          "id": "mcel-lab.form.context.implementation-evidence",
          "meaning": "Supporting evidence about DOM elements, source files, CSS ownership, tests, annotations, validation findings, and repair candidates.",
          "primitive": "context",
          "relationships": [
            "Explains the selected AppBlueprint, selected aspect, and selected rendered element.",
            "May be gathered from mounted previews, point inspection, annotation maps, source bindings, test bindings, and registry payloads.",
            "Supports repair planning without becoming a direct patch applicator."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Ambient feedback must not interrupt or obscure blueprint inspection.",
            "Corrective feedback must identify the condition it observes.",
            "Feedback projections must have an owner so they are not diagnosed as random overlays."
          ],
          "id": "mcel-lab.form.feedback.validation-and-mount-state",
          "meaning": "Signals about selected app state, mount readiness, inspection mode, annotation save state, validation findings, export readiness, and repair-plan readiness.",
          "primitive": "feedback",
          "relationships": [
            "Observes app selection, aspect selection, mounted preview state, selected element state, annotation state, and validation results.",
            "May render as badges, receipts, inline findings, result summaries, or machine-readable packets.",
            "Serves users, developers, and automation without defining a physical slot."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "MCEL Lab may edit its own blueprint draft.",
            "MCEL Lab must not directly rewrite or apply its own live implementation.",
            "Self-hosting repair output must be reviewable as an artifact before any local patch workflow applies it."
          ],
          "id": "mcel-lab.form.constraint.self-hosting-safety",
          "meaning": "Safety law that lets MCEL Lab inspect and draft changes to its own blueprint without directly mutating its live implementation.",
          "primitive": "constraint",
          "relationships": [
            "Protects mcel-lab.form.subject.app-blueprint when selectedApp is mcel-lab.",
            "Applies to annotation edits, repair plans, export packets, and patch artifact generation.",
            "Separates draft intent from implementation mutation."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must be explicitly mode-bound and reversible.",
            "Must not fire the mounted app's ordinary actions while selecting an element.",
            "Must identify selected element evidence separately from user-authored annotation intent."
          ],
          "id": "mcel-lab.form.transient.point-inspection",
          "meaning": "Temporary inspection UI used while the user is selecting a rendered element and capturing evidence.",
          "primitive": "transient",
          "relationships": [
            "Supports element selection, bounding-box evidence, annotation drafting, and source/test ownership hints.",
            "Is active only while inspect mode is enabled or a selected element receipt is being reviewed.",
            "May annotate the mounted preview without mutating the mounted app."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must interrupt or block when the user attempts direct self-mutation.",
            "Must require evidence before deletion or rework candidates become patch guidance.",
            "Must separate possible fixes from verified facts."
          ],
          "id": "mcel-lab.form.interruption.unsafe-repair-boundary",
          "meaning": "Attention-demanding boundary used when a repair, removal, or self-hosting operation could be mistaken for a verified implementation fact or direct mutation.",
          "primitive": "interruption",
          "relationships": [
            "Protects patch planning, self-hosting edits, removal candidates, and destructive annotations.",
            "Can block export or require review when evidence is stale or unsafe.",
            "Explains recovery actions before any patch artifact is generated."
          ],
          "status": "specified"
        }
      ],
      "id": "mcel-lab",
      "intent_count": 7,
      "intent_risk_counts": {
        "local-state": 4,
        "read-only": 3
      },
      "mutation_intent_count": 4,
      "open_finding_count": 1,
      "planned_or_open_count": 31,
      "primary_user_goal": "Select an app blueprint, inspect its semantic form and implementation evidence, annotate rendered elements, validate findings, and export repair context without directly rewriting live implementation files.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 4,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.primary-blueprint-workspace",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.required-semantic-projections",
          "mode": "default",
          "severity": "error",
          "status": "specified"
        },
        {
          "check": "visual-integrity-baseline",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.visual-integrity-baseline",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "lifecycle-contract-preserved",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.self-hosting-safety-boundary",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 109,
        "file": "pretty_docs/mcel-lab-blueprint-studio.md",
        "start_line": 84
      },
      "status": "specified",
      "status_counts": {
        "implemented": 4,
        "open": 1,
        "partially-implemented": 3,
        "planned": 8,
        "specified": 22
      },
      "target_runtime_status": "scope-limited-semantic-runtime",
      "title": "MCEL Lab Blueprint Studio",
      "use_cases": [
        {
          "goal": "Select an app, inspect its semantic form primitives, compare the declared contract with implementation evidence, and identify gaps before changing code.",
          "id": "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
          "status": "planned"
        },
        {
          "goal": "Inspect MCEL Lab itself, annotate rendered elements, distinguish user intent from verified facts, and export reviewable repair context without directly rewriting the live Lab implementation.",
          "id": "mcel-lab.use-case.self-host-refactor-context",
          "status": "planned"
        }
      ]
    },
    "website-builder": {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 12,
        "target_adapter_status:executable": 12
      },
      "app": "website-builder",
      "block_type_counts": {
        "mcel-acceptance": 5,
        "mcel-app": 1,
        "mcel-finding": 4,
        "mcel-form-primitive": 6,
        "mcel-intent": 12,
        "mcel-region": 10,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 4
      },
      "contract_complete": true,
      "current_runtime_status": "working-app-plus-site-project-model",
      "dominant_object": "WebsiteProject",
      "first_regions": [
        {
          "id": "website-builder.region.identity",
          "region": "website-identity-header",
          "responsibility": "Identify the selected website, current site metadata, dirty/save state, and source-vs-saved status across edit, preview, and publish workflows.",
          "role": "identity-header",
          "status": "specified"
        },
        {
          "id": "website-builder.region.site-selector",
          "region": "saved-site-navigation",
          "responsibility": "Let the user choose, create, search, and locate saved website projects without performing destructive site operations implicitly.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "website-builder.region.design-surface",
          "region": "primary-design-surface",
          "responsibility": "Own the author-facing GrapesJS design canvas, page blocks, and draft page state during normal website editing.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "website-builder.region.preview-surface",
          "region": "website-preview-surface",
          "responsibility": "Show draft, local, dev, or remote preview lanes and their availability without implying that preview equals publish success.",
          "role": "preview-surface",
          "status": "specified"
        },
        {
          "id": "website-builder.region.source-and-manifest",
          "region": "source-manifest-evidence-panel",
          "responsibility": "Expose site source, builder metadata, generated artifacts, runtime selection, and manifest evidence for the selected website.",
          "role": "evidence-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Selected website identity must remain traceable across edit, preview, save, configure, publish, and handoff actions.",
            "Generated runtime evidence must not be confused with author-owned source.",
            "Remote or deployment state must not be implied by local save or preview."
          ],
          "id": "website-builder.form.subject.website-project",
          "meaning": "The selected saved website, page source, builder state, manifest, runtime configuration, generated evidence, publish target, and repository handoff state.",
          "primitive": "subject",
          "relationships": [
            "Site manifest, builder state, source files, generated runtime evidence, and publish receipts belong to the selected website project.",
            "Author-owned source, local runtime data, generated files, deployment targets, and Git handoff evidence must remain distinguishable.",
            "Publish lane evidence derives from an explicit target and preflight state."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Save, preview, local publish, dev publish, remote publish, and Git handoff remain separate actions.",
            "Destructive runtime or storage choices require explicit acknowledgement.",
            "Failed preview, save, setup, publish, or handoff actions must preserve recovery evidence."
          ],
          "id": "website-builder.form.action.author-preview-publish",
          "meaning": "The user selects a website, edits content or style, previews draft output, saves source artifacts, configures runtime layers, publishes to an explicit lane, or hands work to Git Tools.",
          "primitive": "action",
          "relationships": [
            "Edit and save actions mutate only the selected website source artifacts.",
            "Preview actions derive evidence without publishing.",
            "Publish actions require target evidence, preflight, confirmation, execution, and receipt."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary authoring surface must remain visible and usable during editing and preview.",
            "Publish and runtime setup controls must remain tied to selected website and explicit target evidence.",
            "Generated evidence must not claim source authority."
          ],
          "id": "website-builder.form.work-surface.site-authoring",
          "meaning": "The primary stable work surface for selecting a website project, authoring source, inspecting preview evidence, configuring runtime state, and preparing publish or handoff actions.",
          "primitive": "work-surface",
          "relationships": [
            "Enables site selection, content/style editing, source save, draft preview, runtime setup review, publish preflight, and Git Tools handoff.",
            "Keeps author-owned source, generated evidence, runtime setup, and publish state connected to the selected website project.",
            "Presents deployment evidence as a governed extension of the authoring workflow."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must keep author-owned source, generated files, runtime data, and deployed state distinguishable.",
            "Context must not hide destructive storage or remote deployment risk.",
            "Receipts must name the selected website and target lane when available."
          ],
          "id": "website-builder.form.context.runtime-and-publish-evidence",
          "meaning": "Supporting context that explains manifest state, builder state, source artifacts, generated runtime files, database/CMS layers, publish targets, receipts, and Git handoff evidence.",
          "primitive": "context",
          "relationships": [
            "Explains whether evidence came from source, generated runtime, local server, dev deployment, remote target, or repository handoff.",
            "Connects runtime setup dependencies to explicit choices and receipts.",
            "Connects publish results to the lane and target that produced them."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not claim deployment success without a matching receipt.",
            "Feedback must not cover or replace the primary authoring surface.",
            "Feedback must identify the selected website, lane, runtime layer, or handoff target when possible."
          ],
          "id": "website-builder.form.feedback.save-preview-publish-state",
          "meaning": "Feedback about dirty state, save result, preview readiness, runtime setup state, publish preflight, publish result, Git handoff readiness, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes selected website state, authoring activity, preview generation, setup progress, publish workflow, handoff state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing website source by itself.",
            "Distinguishes active issues from historical or resolved findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient mutation UI requires a clear selected website and target.",
            "Transient evidence must preserve source/generated/runtime/deployment boundaries.",
            "Transient recovery must not perform follow-up mutation without another explicit action."
          ],
          "id": "website-builder.form.transient.setup-publish-and-handoff",
          "meaning": "Temporary setup, generation, confirmation, execution-progress, receipt, and recovery evidence for runtime configuration, publish, and Git handoff operations.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit setup, publish, or handoff actions without becoming website source itself.",
            "May demand attention when storage, deployment, or repository risk requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches website subject."
          ],
          "status": "specified"
        }
      ],
      "id": "website-builder",
      "intent_count": 12,
      "intent_risk_counts": {
        "local-file-mutation": 3,
        "local-state": 3,
        "read-only": 4,
        "remote-mutation": 2
      },
      "mutation_intent_count": 8,
      "open_finding_count": 4,
      "planned_or_open_count": 51,
      "primary_user_goal": "Edit saved websites, configure optional site runtime layers, preview and publish to explicit lanes, and hand repository changes to Git Tools without confusing author-owned source, generated runtime evidence, deployment targets, or remote sync.",
      "prohibited_intent_count": 0,
      "region_count": 10,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-primary-preview",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 196,
        "file": "pretty_docs/mcel-website-builder-requirements.md",
        "start_line": 162
      },
      "status": "specified",
      "status_counts": {
        "open": 4,
        "partially-implemented": 3,
        "planned": 6,
        "specified": 41
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Website Builder and Websites",
      "use_cases": [
        {
          "goal": "Select a saved website, edit its visible content or styling, preview the draft, save the site source, and verify that the saved site still has a coherent manifest, builder state, entry HTML, stylesheet, script, and page runtime.",
          "id": "website-builder.use-case.edit-preview-saved-site",
          "status": "partially-implemented"
        },
        {
          "goal": "Configure or inspect the blog-capable site runtime without confusing source pages, local database artifacts, Directus storage, generated API routes, or published website files.",
          "id": "website-builder.use-case.configure-blog-runtime",
          "status": "partially-implemented"
        },
        {
          "goal": "Publish a saved website to one explicit lane, verify the target URL, and keep local authoring, local server, dev deployment, and remote production separate.",
          "id": "website-builder.use-case.publish-selected-lane",
          "status": "partially-implemented"
        },
        {
          "goal": "Turn saved website changes into reviewable repository evidence, then use Git Tools for file selection, commit, and governed push rather than hiding Git mutation inside Website Builder.",
          "id": "website-builder.use-case.git-tools-handoff",
          "status": "planned"
        }
      ]
    }
  },
  "apps": [
    {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 10,
        "target_adapter_status:executable": 10
      },
      "app": "calculator",
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 10,
        "mcel-region": 11,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 1
      },
      "contract_complete": true,
      "current_runtime_status": "domain-ready-planner-plus-domain-pack",
      "dominant_object": "CalculationSession",
      "first_regions": [
        {
          "id": "calculator.region.mode-toolbar",
          "region": "mode-switcher-toolbar",
          "responsibility": "Own mode selection between arithmetic and scientific/graphing surfaces without evaluating expressions or hiding the user's current calculation context.",
          "role": "mode-switcher",
          "status": "specified"
        },
        {
          "id": "calculator.region.arithmetic-panel",
          "region": "primary-calculation-surface",
          "responsibility": "Own the ordinary arithmetic workflow by keeping expression input, local actions, and deterministic result evidence visually connected.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "calculator.region.expression-display",
          "region": "expression-input-display",
          "responsibility": "Show the current arithmetic expression as authoritative calculator input, separate from graph output, Mathics prompts, and model prose.",
          "role": "input-display",
          "status": "specified"
        },
        {
          "id": "calculator.region.keypad",
          "region": "deterministic-input-grid",
          "responsibility": "Provide local digit, operator, edit, and equals actions that mutate only the current arithmetic expression and deterministic result state.",
          "role": "action-grid",
          "status": "specified"
        },
        {
          "id": "calculator.region.result-status",
          "region": "result-evidence-status",
          "responsibility": "Show success, error, graph, and symbolic evaluation status near the calculator surface that produced the evidence.",
          "role": "evidence-status",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Calculation identity must remain traceable across evaluate, graph, ask, and symbolic helper actions.",
            "Helper evidence must not mutate the canonical expression or result without an explicit user action.",
            "No calculation subject may imply filesystem, repository, package, or shell mutation."
          ],
          "id": "calculator.form.subject.calculation-session",
          "meaning": "The active calculation scenario, including expressions, graph inputs, symbolic requests, result history, and explanation context.",
          "primitive": "subject",
          "relationships": [
            "Arithmetic expressions, graph inputs, symbolic requests, and result explanations belong to the same calculation session subject.",
            "Deterministic numeric result evidence remains canonical for computed answers.",
            "Model explanations and symbolic evaluations are derived evidence, not silent replacements for computed results."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Evaluation and graphing stay local and deterministic.",
            "Symbolic/model helpers run only through explicit helper actions.",
            "Failed parsing or evaluation must produce visible feedback instead of mutating unrelated state."
          ],
          "id": "calculator.form.action.evaluate-and-explain",
          "meaning": "The user asks Calculator to evaluate expressions, draw graphs, request symbolic results, or explain deterministic output.",
          "primitive": "action",
          "relationships": [
            "Evaluation derives result evidence from the active calculation session.",
            "Graphing derives visual evidence from expression and range state.",
            "Explanation actions must cite or preserve the deterministic result they explain."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary compute surface must remain visible and usable while Calculator is active.",
            "Derived helper output must not claim authority over deterministic result evidence.",
            "Transient helper activity must not obscure the calculation path beyond its explicit operation."
          ],
          "id": "calculator.form.work-surface.deterministic-compute",
          "meaning": "The primary stable work surface where expression input, numeric result evidence, graph output, and helper results remain tied to the active calculation session.",
          "primitive": "work-surface",
          "relationships": [
            "Enables expression evaluation, graph inspection, sample comparison, symbolic helper use, and result explanation.",
            "Keeps computed result evidence authoritative over helper prose.",
            "Presents derived graph or helper evidence as part of the same calculation task."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must remain subordinate to deterministic result evidence.",
            "Parse and validation context must identify the affected input or operation.",
            "Explanation context must not hide whether the result came from local evaluation, symbolic evaluation, or model help."
          ],
          "id": "calculator.form.context.result-evidence",
          "meaning": "Supporting context that explains formulas, ranges, history, parse state, graph evidence, and helper outputs for the active calculation session.",
          "primitive": "context",
          "relationships": [
            "Explains why a result, graph, symbolic response, or model explanation belongs to the current calculation.",
            "Connects validation failures to the input or helper action that produced them.",
            "Helps users compare values without changing the calculation subject."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not interrupt ordinary calculation unless an operation fails or becomes unsafe.",
            "Feedback must not cover or replace the primary compute surface.",
            "Feedback must distinguish current active issues from historical or resolved issues."
          ],
          "id": "calculator.form.feedback.validation-and-compute-state",
          "meaning": "Ambient and noticeable feedback about parse validity, compute success, graph readiness, helper status, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes evaluation state, validation failures, helper activity, and runtime integrity.",
            "Supports user, developer, and automation audiences without changing the calculation session.",
            "Can be summarized compactly or expanded into findings when investigation is needed."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Helper transients require user initiation or a visible lifecycle trigger.",
            "Helper transients must preserve the active calculation subject and deterministic result evidence.",
            "Helper transients must not perform hidden filesystem, repository, network-publish, package, or shell operations."
          ],
          "id": "calculator.form.transient.explicit-helper-evaluation",
          "meaning": "Temporary helper activity for symbolic evaluation, model explanation, graph redraw, or validation recovery.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit helper actions without becoming the calculation session itself.",
            "May produce derived evidence, receipts, warnings, or recovery instructions.",
            "Ends when the helper action resolves, is dismissed, or is superseded by a new calculation action."
          ],
          "status": "specified"
        }
      ],
      "id": "calculator",
      "intent_count": 10,
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 9
      },
      "mutation_intent_count": 1,
      "open_finding_count": 3,
      "planned_or_open_count": 47,
      "primary_user_goal": "Enter arithmetic expressions, inspect results, draw graphs, run explicit symbolic evaluations, and ask contextual questions without hidden filesystem, remote-sync, or command-execution side effects.",
      "prohibited_intent_count": 0,
      "region_count": 11,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-primary-workspace",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "calculator.contract.default.app-health",
          "id": "calculator.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 96,
        "file": "pretty_docs/mcel-calculator-requirements.md",
        "start_line": 71
      },
      "status": "specified",
      "status_counts": {
        "draft": 1,
        "open": 3,
        "planned": 2,
        "specified": 41
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Calculator",
      "use_cases": [
        {
          "goal": "Compare two monthly pricing formulas, identify the break-even point, inspect sample values, plot the relationship, and explain the result without leaving Calculator.",
          "id": "calculator.use-case.compare-monthly-costs",
          "status": "draft"
        }
      ]
    },
    {
      "adapter_status_counts": {},
      "app": "code-editor",
      "block_type_counts": {
        "mcel-acceptance": 1,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 7,
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 8,
        "mcel-runtime-check": 5,
        "mcel-source-binding": 2,
        "mcel-test-binding": 2,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "structural-workbench-with-domain-enrichment",
      "dominant_object": "SourceWorkspace",
      "first_regions": [
        {
          "id": "code-editor.region.identity",
          "region": "identity",
          "responsibility": "Identify the active workspace, route, active file, dirty state, runtime version, gate status, and persistence state.",
          "role": "identity-header",
          "status": "specified"
        },
        {
          "id": "code-editor.region.navigation",
          "region": "navigation",
          "responsibility": "Let the user choose files, project context, open editors, and selected-file sets without applying patches or executing commands.",
          "role": "project-navigation",
          "status": "specified"
        },
        {
          "id": "code-editor.region.primary",
          "region": "primary",
          "responsibility": "Own the selected-file editor, draft review, concrete diffs, and explicit preview modes while preventing supporting tools from becoming the source of truth.",
          "role": "primary-authoring-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.inspector",
          "region": "supporting-reasoning-evidence-projection",
          "responsibility": "Project optional reasoning, evidence, diagnostics, Aider context, SCM manifests, source ownership, test ownership, documentation references, and action-specific preflight information without becoming the primary editor. A desktop renderer may currently place this projection beside the editor, but MCEL treats that placement as layout inference rather than the requirement.",
          "role": "secondary-context-and-feedback-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.evidence",
          "region": "evidence",
          "responsibility": "Show Aider output, SCM evidence, contract reports, regression results, receipts, and recovery guidance for reviewed actions.",
          "role": "evidence-and-receipts-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 7,
      "form_primitives": [
        {
          "constraints": [
            "Author-owned source remains canonical.",
            "Runtime chrome and generated helper surfaces must not become saved source.",
            "Selection identity must remain visible enough to anchor editing and review."
          ],
          "id": "code-editor.form.subject.source-workspace",
          "meaning": "The project/workspace source tree and selected source file that the app helps inspect, edit, and safely change.",
          "primitive": "subject",
          "relationships": [
            "Selected file is part of the source workspace.",
            "Source text, diagnostics, SCM evidence, and Aider context derive from the selected workspace subject.",
            "Generated runtime or proof artifacts are derived evidence, not canonical source."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Preview, suggestion, diagnosis, and review are not writes.",
            "Save/apply/execute/remote mutation require explicit intents and receipts.",
            "Read-only Aider requests cannot mutate files."
          ],
          "id": "code-editor.form.action.edit-source",
          "meaning": "Inspect and change selected source text while preserving explicit save, patch, execution, and remote-mutation boundaries.",
          "primitive": "action",
          "relationships": [
            "Acts on code-editor.form.subject.source-workspace.",
            "Uses code-editor.form.work-surface.selected-source-editor as the authoritative work surface.",
            "May consume supporting context, evidence, and feedback without allowing those projections to mutate source implicitly."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must remain visible and usable in authoring mode.",
            "Must not be covered, replaced, or out-ranked by supporting context, feedback, proof, preview, or diagnostic projections.",
            "Must preserve selected-path and dirty-state evidence."
          ],
          "id": "code-editor.form.work-surface.selected-source-editor",
          "meaning": "The authoritative stable surface where the selected file's source text is edited.",
          "primitive": "work-surface",
          "relationships": [
            "Enables code-editor.form.action.edit-source.",
            "Represents the selected file from code-editor.form.subject.source-workspace.",
            "May be implemented by Monaco or a mode-gated fallback, but exactly one editor surface may hold primary authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must not claim primary editor authority.",
            "Must not obscure the selected source editor below usable geometry.",
            "Must keep the current selected subject traceable when file-backed editing is active."
          ],
          "id": "code-editor.form.context.project-selection",
          "meaning": "Supporting context that lets the user choose, understand, and compare source workspace subjects.",
          "primitive": "context",
          "relationships": [
            "Selects or explains the active source workspace/file subject.",
            "Supports editing, review, SCM evidence, and Aider context gathering.",
            "May project through any selection affordance that preserves subject identity and editing flow."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must not become the selected-file editor.",
            "Must not leak as an unowned overlay over the primary work surface.",
            "Must remain distinguishable from canonical source and from write/apply controls."
          ],
          "id": "code-editor.form.context.reasoning-evidence",
          "meaning": "Supporting explanation, evidence, diagnostics, ownership hints, documentation references, and Aider context that help reason about the selected source subject or proposed action.",
          "primitive": "context",
          "relationships": [
            "Observes or explains source text, diagnostics, requirements, SCM evidence, Aider plans, and test/source ownership.",
            "May be available on demand, adjacent, tabbed, collapsed, or deferred by layout inference.",
            "Shares viewport with the primary work surface only when it preserves primary authority and geometry."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Ambient feedback must not interrupt or cover the primary work surface.",
            "Noticeable or corrective feedback must identify the condition it observes.",
            "Feedback projections must be owned so they are not reported as random overlays."
          ],
          "id": "code-editor.form.feedback.integrity-and-activity",
          "meaning": "Signals about app integrity, contract health, dirty/save state, policy gates, activity, failures, receipts, and recovery posture.",
          "primitive": "feedback",
          "relationships": [
            "Observes the source workspace, editor usability, runtime contract, action lifecycle, and persistence state.",
            "May render as status text, badges, counters, inline findings, panels, or machine-readable reports.",
            "Supports users, developers, and automation without defining a physical slot."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Active widget editor panes, selections, and dock previews are forbidden in normal authoring mode.",
            "The inert widget-editor root is not itself a visible work surface.",
            "Transient structure-editing UI must identify its mode and owner when visible."
          ],
          "id": "code-editor.form.transient.widget-structure-editing",
          "meaning": "Temporary structure-editing UI used only while an explicit widget or layout editing mode is active.",
          "primitive": "transient",
          "relationships": [
            "Supports structural editing operations rather than ordinary source editing.",
            "May cover or annotate the app only while its explicit mode is active.",
            "Is shell/tool infrastructure when inert and a transient projection when active."
          ],
          "status": "specified"
        }
      ],
      "id": "code-editor",
      "intent_count": 7,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 2,
        "local-state": 1,
        "read-only": 3
      },
      "mutation_intent_count": 4,
      "open_finding_count": 3,
      "planned_or_open_count": 44,
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 5,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-primary-monaco",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-required-regions",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "secondary-surface-policy",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
          "mode": "authoring",
          "severity": "warning",
          "status": "specified"
        },
        {
          "check": "forbidden-surfaces-hidden",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "lifecycle-contract-preserved",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "id": "code-editor.runtime-check.authoring-lifecycle",
          "mode": "authoring",
          "severity": "critical",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 45,
        "file": "pretty_docs/mcel-code-editor-requirements.md",
        "start_line": 18
      },
      "status": "specified",
      "status_counts": {
        "open": 3,
        "planned": 7,
        "specified": 34
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Code Editor / MCEL Code Studio",
      "use_cases": [
        {
          "goal": "Prepare an AI-assisted source change, inspect the proposed diff and affected files, apply only approved edits, and preserve author control over every source mutation.",
          "id": "code-editor.use-case.review-apply-ai-source-change",
          "status": "planned"
        },
        {
          "goal": "Select an author-owned project file, edit it safely, save it explicitly, and preserve visible evidence about the path, dirty state, and saved result.",
          "id": "code-editor.use-case.edit-save-source-file",
          "status": "planned"
        }
      ]
    },
    {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 8,
        "current_adapter_status:prohibited": 3,
        "target_adapter_status:executable": 7,
        "target_adapter_status:preflight-only": 1,
        "target_adapter_status:prohibited": 3
      },
      "app": "file-explorer",
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 11,
        "mcel-region": 7,
        "mcel-requirement": 9,
        "mcel-runtime-check": 3,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "domain-ready-read-only-planner-plus-domain-pack",
      "dominant_object": "FileEntry",
      "first_regions": [
        {
          "id": "file-explorer.layout.identity",
          "region": "roots-panel-header",
          "responsibility": "Identify the app as File Explorer, describe read-only system browsing, and expose the global status line.",
          "role": "identity",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.roots",
          "region": "roots-sidebar",
          "responsibility": "Show selectable trusted roots such as workspace, debug-root, cwd, home, workspace-parent, filesystem-root, drive roots, or configured mounted Windows roots.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.path-toolbar",
          "region": "path-and-search-toolbar",
          "responsibility": "Show the current root-relative browsing scope and provide bounded search/up navigation within that scope.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.directory-list",
          "region": "directory-listing",
          "responsibility": "Present the current directory or search result set as the primary selectable collection, with directories before files and enough metadata to choose a preview or handoff target.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "file-explorer.layout.preview",
          "region": "preview-panel",
          "responsibility": "Show selected entry metadata, preview content when safe, preview-denied reasons when unsafe, category evidence, and suggested app evidence.",
          "role": "inspector",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Root and path boundaries remain explicit for every selected entry.",
            "Relative traversal cannot escape the selected browse scope.",
            "Read-only browsing must not imply delete, move, rename, write, Git, upload, download, or shell authority."
          ],
          "id": "file-explorer.form.subject.browse-scope",
          "meaning": "The selected trusted root, current path, directory entry set, selected entry, previewable content, and mounted-root evidence.",
          "primitive": "subject",
          "relationships": [
            "The selected entry belongs to the selected root and current path scope.",
            "Preview content, metadata, category, and suggested app derive from the selected entry.",
            "Mounted-root evidence explains when a displayed path is backed by a host path mapping."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Inspection actions are read-only.",
            "Preview failures must report the reason instead of attempting mutation.",
            "Handoff suggestions must not open, write, stage, publish, or execute without a separate explicit app action."
          ],
          "id": "file-explorer.form.action.inspect-entry-safely",
          "meaning": "The user selects roots, searches within scope, chooses entries, previews readable content, and decides handoff without mutating files.",
          "primitive": "action",
          "relationships": [
            "Search and selection operate within the active browse scope.",
            "Preview derives evidence from the selected entry and documented preview limits.",
            "Handoff suggestions connect entry category to another Main Computer app."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary inspection surface must remain visible and usable while browsing.",
            "Preview evidence must stay tied to the selected entry.",
            "Read-only status must remain visible enough to prevent accidental mutation assumptions."
          ],
          "id": "file-explorer.form.work-surface.entry-inspection",
          "meaning": "The primary stable work surface for browsing entries, selecting a file or folder subject, inspecting metadata, and viewing safe preview evidence.",
          "primitive": "work-surface",
          "relationships": [
            "Enables root selection, scoped search, directory entry inspection, metadata preview, content preview, and app-handoff reasoning.",
            "Keeps selected entry identity connected to preview and classification evidence.",
            "Preserves read-only status as part of the inspection task."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must not claim file mutation authority.",
            "Category and suggested-app evidence must be distinguishable from the file contents themselves.",
            "Missing or unreadable preview must produce explicit evidence, not blank ambiguity."
          ],
          "id": "file-explorer.form.context.selection-and-classification",
          "meaning": "Supporting context that explains current root, path, selected entry, metadata, category, suggested app, and preview availability.",
          "primitive": "context",
          "relationships": [
            "Explains why an entry is classified as code, text, spreadsheet, game, asset, binary, oversized, or other.",
            "Connects preview availability to size, type, readability, and safety limits.",
            "Connects selected entries to possible downstream app handoff."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not interrupt ordinary browsing unless a boundary, preview, or safety rule is violated.",
            "Feedback must not cover or replace the primary inspection surface.",
            "Feedback must identify the affected root, path, entry, or operation when possible."
          ],
          "id": "file-explorer.form.feedback.boundary-and-preview-state",
          "meaning": "Feedback about selected scope, read-only status, search state, preview readiness, preview failure, mounted-root status, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes browse scope, selected entry, preview limits, search progress, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automated contract checking.",
            "Distinguishes active browse problems from historical or resolved findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient evidence must remain bounded to the active browse scope.",
            "Transient evidence must not imply mutation or permission escalation.",
            "Transient evidence must not obscure root, path, selected-entry, or read-only identity."
          ],
          "id": "file-explorer.form.transient.search-and-selection-evidence",
          "meaning": "Temporary evidence created by search, selection change, preview loading, classification refresh, or handoff consideration.",
          "primitive": "transient",
          "relationships": [
            "Supports the active inspect-entry action without becoming persistent file state.",
            "May highlight a selection, search result, classification change, or preview-loading lifecycle.",
            "Ends when the selection, query, preview, or handoff consideration changes."
          ],
          "status": "specified"
        }
      ],
      "id": "file-explorer",
      "intent_count": 11,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-state": 1,
        "prohibited": 1,
        "read-only": 7
      },
      "mutation_intent_count": 3,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
      "primary_user_goal": "Browse trusted roots, inspect directory contents, search within a bounded scope, preview readable files, classify entries, and hand off chosen files to the right Main Computer app without hidden filesystem, Git, remote, or command side effects.",
      "prohibited_intent_count": 3,
      "region_count": 7,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-primary-surface",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "file-explorer.contract.default.app-health",
          "id": "file-explorer.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 138,
        "file": "pretty_docs/mcel-file-explorer-requirements.md",
        "start_line": 112
      },
      "status": "specified",
      "status_counts": {
        "draft": 2,
        "open": 3,
        "planned": 3,
        "prohibited": 3,
        "specified": 33
      },
      "target_runtime_status": "full-read-only-semantic-runtime",
      "title": "File Explorer",
      "use_cases": [
        {
          "goal": "Browse the current workspace, search for a known file, inspect its metadata and preview content, and decide which Main Computer app should handle it without mutating the filesystem or repository.",
          "id": "file-explorer.use-case.inspect-project-file-safely",
          "status": "draft"
        },
        {
          "goal": "Browse a configured mounted Windows drive through File Explorer while preserving root boundaries, display-path evidence, and read-only behavior.",
          "id": "file-explorer.use-case.browse-mounted-windows-drive",
          "status": "draft"
        }
      ]
    },
    {
      "adapter_status_counts": {
        "current_adapter_status:declared-only": 3,
        "current_adapter_status:executable": 2,
        "current_adapter_status:not-registered": 3,
        "current_adapter_status:preflight-only": 1,
        "current_adapter_status:prohibited": 1
      },
      "app": "git-tools",
      "block_type_counts": {
        "mcel-acceptance": 5,
        "mcel-app": 1,
        "mcel-finding": 4,
        "mcel-form-primitive": 6,
        "mcel-intent": 10,
        "mcel-region": 8,
        "mcel-requirement": 11,
        "mcel-runtime-check": 3,
        "mcel-use-case": 4
      },
      "contract_complete": true,
      "current_runtime_status": "scope-limited-semantic-runtime",
      "dominant_object": "RepositoryProject",
      "first_regions": [
        {
          "id": "git-tools.region.identity",
          "region": "identity",
          "responsibility": "Identify the selected project, repository root, branch, remote target, backend freshness, and semantic runtime scope.",
          "role": "repository-identity-header",
          "status": "specified"
        },
        {
          "id": "git-tools.region.navigation",
          "region": "navigation",
          "responsibility": "Let the user choose projects, workflow tabs, file baskets, patch inventory views, and support areas without mutating Git state.",
          "role": "repository-navigation",
          "status": "specified"
        },
        {
          "id": "git-tools.region.primary",
          "region": "primary",
          "responsibility": "Own the selected repository workflow, changed-file triage, project publishing strip, status summary, and commit/publish content.",
          "role": "repository-workbench",
          "status": "specified"
        },
        {
          "id": "git-tools.region.inspector",
          "region": "inspector",
          "responsibility": "Show remote configuration, selected-file evidence, ignore-rule previews, policy gates, and action-specific confirmation details.",
          "role": "preflight-inspector",
          "status": "specified"
        },
        {
          "id": "git-tools.region.evidence",
          "region": "evidence",
          "responsibility": "Show status API output, semantic adapter evidence, intent coverage, receipts, backend errors, and recovery plans.",
          "role": "evidence-and-recovery-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Repository identity, branch, and remote target must remain traceable before any mutation.",
            "Local evidence, remote evidence, and planned actions must not be conflated.",
            "Raw Git details may support evidence but must not become hidden default authority."
          ],
          "id": "git-tools.form.subject.repository-project",
          "meaning": "The selected repository project, branch, remote, working-tree evidence, file basket, patch inventory, ignore rules, secrets filters, and publish target.",
          "primitive": "subject",
          "relationships": [
            "Branch, remote, status, diff, staged intent, publish target, and receipts belong to the selected repository project.",
            "Patch inventory and file basket evidence derive from repository state but must remain distinguishable from executed Git actions.",
            "Publishing evidence connects repository state to an explicit governed target."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Commit and push remain separate actions.",
            "Mutation actions require explicit target evidence and confirmation.",
            "Failed actions must produce recovery evidence without pretending repository or remote state changed."
          ],
          "id": "git-tools.form.action.governed-repository-change",
          "meaning": "The user inspects repository state, selects files, stages intent, commits, edits ignore/filter rules, or publishes through governed preflight and receipt flow.",
          "primitive": "action",
          "relationships": [
            "Read actions gather status, branch, remote, diff, patch, and file evidence.",
            "Mutation actions require preflight, explicit confirmation, execution boundary, and receipt.",
            "Recovery actions derive from failed preflight, failed execution, or stale repository evidence."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary repository workflow surface must remain visible and usable.",
            "Mutation controls must remain tied to current repository, branch, target, and preflight evidence.",
            "Evidence views must not silently execute Git commands."
          ],
          "id": "git-tools.form.work-surface.repository-workflow",
          "meaning": "The primary stable work surface for repository triage, status review, file selection, commit preparation, governed publish actions, and recovery.",
          "primitive": "work-surface",
          "relationships": [
            "Enables repository selection, status refresh, file-basket review, patch inventory review, commit preparation, ignore/filter editing, publish preflight, and recovery.",
            "Keeps evidence, intended mutation, confirmation, execution, and receipt connected.",
            "Presents advanced Git details as supporting evidence rather than default authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must not hide the distinction between proposed and executed changes.",
            "Command preview must remain evidence until the user confirms execution.",
            "Receipts must name the affected repository, branch, remote, or target when available."
          ],
          "id": "git-tools.form.context.evidence-and-preflight",
          "meaning": "Supporting context that explains branch, remote, status, diff, staged intent, ignore/filter effects, publish target, command preview, receipts, and recovery paths.",
          "primitive": "context",
          "relationships": [
            "Explains what evidence supports a commit, ignore change, filter change, push, or publish operation.",
            "Connects stale, missing, or conflicting evidence to preflight failures.",
            "Connects receipts and recovery suggestions to the operation that produced them."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not make a mutation appear successful without a matching receipt.",
            "Feedback must not cover or replace the primary repository workflow surface.",
            "High-risk or failed operations may demand attention but must remain tied to recovery evidence."
          ],
          "id": "git-tools.form.feedback.risk-and-operation-state",
          "meaning": "Feedback about repository freshness, dirty state, staged intent, preflight readiness, confirmation requirement, execution result, recovery state, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes repository evidence, action risk, preflight state, execution state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing repository state.",
            "Distinguishes active blockers from resolved or historical findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient mutation UI requires a clear trigger and action target.",
            "Transient evidence must preserve repository, branch, remote, and target identity.",
            "Transient recovery must not perform a follow-up mutation without another explicit action."
          ],
          "id": "git-tools.form.transient.confirmation-and-recovery",
          "meaning": "Temporary confirmation, preflight, execution-progress, command-preview, receipt, and recovery evidence around governed Git and publishing actions.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit mutation or recovery actions without becoming repository state itself.",
            "May demand attention when action risk, missing evidence, conflict, or failure requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches repository subject."
          ],
          "status": "specified"
        }
      ],
      "id": "git-tools",
      "intent_count": 10,
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-repository-mutation": 1,
        "read-only": 5,
        "remote-mutation": 2
      },
      "mutation_intent_count": 5,
      "open_finding_count": 4,
      "planned_or_open_count": 44,
      "primary_user_goal": "Inspect repository state, triage files, create safe commits, and publish selected project work through governed Git/Gitea actions without exposing raw Git plumbing as the default user path.",
      "prohibited_intent_count": 1,
      "region_count": 8,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-primary-workflow",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "git-tools.contract.default.app-health",
          "id": "git-tools.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 57,
        "file": "pretty_docs/mcel-git-tools-requirements.md",
        "start_line": 18
      },
      "status": "specified",
      "status_counts": {
        "implemented": 1,
        "open": 4,
        "partially-implemented": 5,
        "planned": 12,
        "prohibited": 1,
        "specified": 28
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Git Tools",
      "use_cases": [
        {
          "goal": "Inspect repository, branch, and remote evidence, confirm the intended local Gitea target, push the current branch explicitly, and receive success or recovery evidence.",
          "id": "git-tools.use-case.push-current-branch-local-gitea",
          "status": "partially-implemented"
        },
        {
          "goal": "Select an untracked file or directory, preview the proposed .gitignore rule, understand whether the target is already tracked, apply the ignore change, and refresh repository evidence.",
          "id": "git-tools.use-case.add-ignore-rule",
          "status": "planned"
        },
        {
          "goal": "Inspect the current branch, available branch targets, and dirty working-tree state, then switch branches only when local work is safe or explicitly handled.",
          "id": "git-tools.use-case.switch-branch-safely",
          "status": "planned"
        },
        {
          "goal": "Inspect changed files, preview diffs, select the files that belong together, stage only those files, write a commit message, create the commit, and keep unselected changes untouched.",
          "id": "git-tools.use-case.select-files-stage-commit",
          "status": "planned"
        }
      ]
    },
    {
      "adapter_status_counts": {},
      "app": "mcel-lab",
      "block_type_counts": {
        "mcel-acceptance": 1,
        "mcel-app": 1,
        "mcel-finding": 1,
        "mcel-form-primitive": 9,
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 7,
        "mcel-runtime-check": 4,
        "mcel-use-case": 2
      },
      "contract_complete": true,
      "current_runtime_status": "structural-only",
      "dominant_object": "AppBlueprint",
      "first_regions": [
        {
          "id": "mcel-lab.region.app-root",
          "region": "lab-app-root",
          "responsibility": "Owns the MCEL Lab application boundary and exposes the selected AppBlueprint as the dominant object.",
          "role": "app-boundary",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.selection-context",
          "region": "app-and-aspect-selection-context",
          "responsibility": "Projects app and aspect selection primitives without making their physical placement normative.",
          "role": "supporting-context",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.aspect-map",
          "region": "aspect-map-projection",
          "responsibility": "Exposes inspectable blueprint aspects and keeps the selected aspect traceable.",
          "role": "navigation-context",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.blueprint-workspace",
          "region": "blueprint-inspection-workspace",
          "responsibility": "Projects the selected AppBlueprint aspect and mounted preview evidence as the main inspection workspace.",
          "role": "primary-work-surface",
          "status": "implemented"
        },
        {
          "id": "mcel-lab.region.mounted-preview",
          "region": "mounted-app-preview-projection",
          "responsibility": "Shows a contained app preview as evidence while preserving AppBlueprint authority.",
          "role": "implementation-evidence-context",
          "status": "partially-implemented"
        }
      ],
      "form_primitive_count": 9,
      "form_primitives": [
        {
          "constraints": [
            "AppBlueprint remains the dominant object even when a mounted app preview is visible.",
            "Prose, hardcoded JS blueprints, annotations, and runtime evidence must be distinguishable as separate evidence sources.",
            "Self-hosting inspection must not imply permission to rewrite the live Lab implementation."
          ],
          "id": "mcel-lab.form.subject.app-blueprint",
          "meaning": "The selected app contract being inspected, validated, annotated, or prepared for repair.",
          "primitive": "subject",
          "relationships": [
            "Owns app identity, object model, workflows, layout bindings, action policy, evidence, source/test bindings, annotations, findings, and repair plans.",
            "May represent MCEL Lab itself as a self-hosting target.",
            "Is loaded from documentation, blueprint core data, annotations, and runtime evidence."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Inspection is read-oriented until the user explicitly creates or edits an annotation draft.",
            "Aspect navigation must not replace the selected AppBlueprint as the dominant object.",
            "Findings must distinguish documented intent from verified runtime facts."
          ],
          "id": "mcel-lab.form.action.inspect-blueprint",
          "meaning": "Select an app and aspect, inspect the semantic contract and compare it with implementation evidence.",
          "primitive": "action",
          "relationships": [
            "Acts on mcel-lab.form.subject.app-blueprint.",
            "Uses the blueprint inspection work surface as the authoritative workspace.",
            "Consumes supporting implementation evidence, selected-element evidence, validation feedback, and annotations."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must remain visible and usable when MCEL Lab is active.",
            "Must keep selected app, selected aspect, and mounted route evidence traceable.",
            "Must not be covered or out-ranked by unowned feedback, transient overlays, or debug/proof internals."
          ],
          "id": "mcel-lab.form.work-surface.blueprint-inspection",
          "meaning": "The stable surface where the selected AppBlueprint aspect, mounted preview, selected evidence, and repair context are inspected.",
          "primitive": "work-surface",
          "relationships": [
            "Enables mcel-lab.form.action.inspect-blueprint.",
            "Represents the selected AppBlueprint and current aspect.",
            "Hosts mounted app preview evidence without granting that preview primary Lab authority."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must keep the selected app and aspect recoverable from visible UI or machine-readable state.",
            "Must not claim primary work-surface authority.",
            "Must not make physical placement part of the semantic contract."
          ],
          "id": "mcel-lab.form.context.app-and-aspect-selection",
          "meaning": "Supporting context that chooses which AppBlueprint and which aspect are being inspected.",
          "primitive": "context",
          "relationships": [
            "Selects the active subject for the blueprint inspection work surface.",
            "Filters the visible evidence, annotations, findings, and repair context.",
            "May render as controls, lists, command choices, tabs, or another inferred projection."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Evidence must identify its source and freshness when it is used to justify a finding.",
            "Implementation evidence must not be confused with the target requirement itself.",
            "Derived repair context must remain reviewable before patch generation."
          ],
          "id": "mcel-lab.form.context.implementation-evidence",
          "meaning": "Supporting evidence about DOM elements, source files, CSS ownership, tests, annotations, validation findings, and repair candidates.",
          "primitive": "context",
          "relationships": [
            "Explains the selected AppBlueprint, selected aspect, and selected rendered element.",
            "May be gathered from mounted previews, point inspection, annotation maps, source bindings, test bindings, and registry payloads.",
            "Supports repair planning without becoming a direct patch applicator."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Ambient feedback must not interrupt or obscure blueprint inspection.",
            "Corrective feedback must identify the condition it observes.",
            "Feedback projections must have an owner so they are not diagnosed as random overlays."
          ],
          "id": "mcel-lab.form.feedback.validation-and-mount-state",
          "meaning": "Signals about selected app state, mount readiness, inspection mode, annotation save state, validation findings, export readiness, and repair-plan readiness.",
          "primitive": "feedback",
          "relationships": [
            "Observes app selection, aspect selection, mounted preview state, selected element state, annotation state, and validation results.",
            "May render as badges, receipts, inline findings, result summaries, or machine-readable packets.",
            "Serves users, developers, and automation without defining a physical slot."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "MCEL Lab may edit its own blueprint draft.",
            "MCEL Lab must not directly rewrite or apply its own live implementation.",
            "Self-hosting repair output must be reviewable as an artifact before any local patch workflow applies it."
          ],
          "id": "mcel-lab.form.constraint.self-hosting-safety",
          "meaning": "Safety law that lets MCEL Lab inspect and draft changes to its own blueprint without directly mutating its live implementation.",
          "primitive": "constraint",
          "relationships": [
            "Protects mcel-lab.form.subject.app-blueprint when selectedApp is mcel-lab.",
            "Applies to annotation edits, repair plans, export packets, and patch artifact generation.",
            "Separates draft intent from implementation mutation."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must be explicitly mode-bound and reversible.",
            "Must not fire the mounted app's ordinary actions while selecting an element.",
            "Must identify selected element evidence separately from user-authored annotation intent."
          ],
          "id": "mcel-lab.form.transient.point-inspection",
          "meaning": "Temporary inspection UI used while the user is selecting a rendered element and capturing evidence.",
          "primitive": "transient",
          "relationships": [
            "Supports element selection, bounding-box evidence, annotation drafting, and source/test ownership hints.",
            "Is active only while inspect mode is enabled or a selected element receipt is being reviewed.",
            "May annotate the mounted preview without mutating the mounted app."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Must interrupt or block when the user attempts direct self-mutation.",
            "Must require evidence before deletion or rework candidates become patch guidance.",
            "Must separate possible fixes from verified facts."
          ],
          "id": "mcel-lab.form.interruption.unsafe-repair-boundary",
          "meaning": "Attention-demanding boundary used when a repair, removal, or self-hosting operation could be mistaken for a verified implementation fact or direct mutation.",
          "primitive": "interruption",
          "relationships": [
            "Protects patch planning, self-hosting edits, removal candidates, and destructive annotations.",
            "Can block export or require review when evidence is stale or unsafe.",
            "Explains recovery actions before any patch artifact is generated."
          ],
          "status": "specified"
        }
      ],
      "id": "mcel-lab",
      "intent_count": 7,
      "intent_risk_counts": {
        "local-state": 4,
        "read-only": 3
      },
      "mutation_intent_count": 4,
      "open_finding_count": 1,
      "planned_or_open_count": 31,
      "primary_user_goal": "Select an app blueprint, inspect its semantic form and implementation evidence, annotate rendered elements, validate findings, and export repair context without directly rewriting live implementation files.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 4,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.primary-blueprint-workspace",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.required-semantic-projections",
          "mode": "default",
          "severity": "error",
          "status": "specified"
        },
        {
          "check": "visual-integrity-baseline",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.visual-integrity-baseline",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "lifecycle-contract-preserved",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "id": "mcel-lab.runtime.self-hosting-safety-boundary",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 109,
        "file": "pretty_docs/mcel-lab-blueprint-studio.md",
        "start_line": 84
      },
      "status": "specified",
      "status_counts": {
        "implemented": 4,
        "open": 1,
        "partially-implemented": 3,
        "planned": 8,
        "specified": 22
      },
      "target_runtime_status": "scope-limited-semantic-runtime",
      "title": "MCEL Lab Blueprint Studio",
      "use_cases": [
        {
          "goal": "Select an app, inspect its semantic form primitives, compare the declared contract with implementation evidence, and identify gaps before changing code.",
          "id": "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
          "status": "planned"
        },
        {
          "goal": "Inspect MCEL Lab itself, annotate rendered elements, distinguish user intent from verified facts, and export reviewable repair context without directly rewriting the live Lab implementation.",
          "id": "mcel-lab.use-case.self-host-refactor-context",
          "status": "planned"
        }
      ]
    },
    {
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 12,
        "target_adapter_status:executable": 12
      },
      "app": "website-builder",
      "block_type_counts": {
        "mcel-acceptance": 5,
        "mcel-app": 1,
        "mcel-finding": 4,
        "mcel-form-primitive": 6,
        "mcel-intent": 12,
        "mcel-region": 10,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 4
      },
      "contract_complete": true,
      "current_runtime_status": "working-app-plus-site-project-model",
      "dominant_object": "WebsiteProject",
      "first_regions": [
        {
          "id": "website-builder.region.identity",
          "region": "website-identity-header",
          "responsibility": "Identify the selected website, current site metadata, dirty/save state, and source-vs-saved status across edit, preview, and publish workflows.",
          "role": "identity-header",
          "status": "specified"
        },
        {
          "id": "website-builder.region.site-selector",
          "region": "saved-site-navigation",
          "responsibility": "Let the user choose, create, search, and locate saved website projects without performing destructive site operations implicitly.",
          "role": "navigation",
          "status": "specified"
        },
        {
          "id": "website-builder.region.design-surface",
          "region": "primary-design-surface",
          "responsibility": "Own the author-facing GrapesJS design canvas, page blocks, and draft page state during normal website editing.",
          "role": "primary-work-surface",
          "status": "specified"
        },
        {
          "id": "website-builder.region.preview-surface",
          "region": "website-preview-surface",
          "responsibility": "Show draft, local, dev, or remote preview lanes and their availability without implying that preview equals publish success.",
          "role": "preview-surface",
          "status": "specified"
        },
        {
          "id": "website-builder.region.source-and-manifest",
          "region": "source-manifest-evidence-panel",
          "responsibility": "Expose site source, builder metadata, generated artifacts, runtime selection, and manifest evidence for the selected website.",
          "role": "evidence-panel",
          "status": "specified"
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "constraints": [
            "Selected website identity must remain traceable across edit, preview, save, configure, publish, and handoff actions.",
            "Generated runtime evidence must not be confused with author-owned source.",
            "Remote or deployment state must not be implied by local save or preview."
          ],
          "id": "website-builder.form.subject.website-project",
          "meaning": "The selected saved website, page source, builder state, manifest, runtime configuration, generated evidence, publish target, and repository handoff state.",
          "primitive": "subject",
          "relationships": [
            "Site manifest, builder state, source files, generated runtime evidence, and publish receipts belong to the selected website project.",
            "Author-owned source, local runtime data, generated files, deployment targets, and Git handoff evidence must remain distinguishable.",
            "Publish lane evidence derives from an explicit target and preflight state."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Save, preview, local publish, dev publish, remote publish, and Git handoff remain separate actions.",
            "Destructive runtime or storage choices require explicit acknowledgement.",
            "Failed preview, save, setup, publish, or handoff actions must preserve recovery evidence."
          ],
          "id": "website-builder.form.action.author-preview-publish",
          "meaning": "The user selects a website, edits content or style, previews draft output, saves source artifacts, configures runtime layers, publishes to an explicit lane, or hands work to Git Tools.",
          "primitive": "action",
          "relationships": [
            "Edit and save actions mutate only the selected website source artifacts.",
            "Preview actions derive evidence without publishing.",
            "Publish actions require target evidence, preflight, confirmation, execution, and receipt."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "The primary authoring surface must remain visible and usable during editing and preview.",
            "Publish and runtime setup controls must remain tied to selected website and explicit target evidence.",
            "Generated evidence must not claim source authority."
          ],
          "id": "website-builder.form.work-surface.site-authoring",
          "meaning": "The primary stable work surface for selecting a website project, authoring source, inspecting preview evidence, configuring runtime state, and preparing publish or handoff actions.",
          "primitive": "work-surface",
          "relationships": [
            "Enables site selection, content/style editing, source save, draft preview, runtime setup review, publish preflight, and Git Tools handoff.",
            "Keeps author-owned source, generated evidence, runtime setup, and publish state connected to the selected website project.",
            "Presents deployment evidence as a governed extension of the authoring workflow."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Context must keep author-owned source, generated files, runtime data, and deployed state distinguishable.",
            "Context must not hide destructive storage or remote deployment risk.",
            "Receipts must name the selected website and target lane when available."
          ],
          "id": "website-builder.form.context.runtime-and-publish-evidence",
          "meaning": "Supporting context that explains manifest state, builder state, source artifacts, generated runtime files, database/CMS layers, publish targets, receipts, and Git handoff evidence.",
          "primitive": "context",
          "relationships": [
            "Explains whether evidence came from source, generated runtime, local server, dev deployment, remote target, or repository handoff.",
            "Connects runtime setup dependencies to explicit choices and receipts.",
            "Connects publish results to the lane and target that produced them."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Feedback must not claim deployment success without a matching receipt.",
            "Feedback must not cover or replace the primary authoring surface.",
            "Feedback must identify the selected website, lane, runtime layer, or handoff target when possible."
          ],
          "id": "website-builder.form.feedback.save-preview-publish-state",
          "meaning": "Feedback about dirty state, save result, preview readiness, runtime setup state, publish preflight, publish result, Git handoff readiness, and contract health.",
          "primitive": "feedback",
          "relationships": [
            "Observes selected website state, authoring activity, preview generation, setup progress, publish workflow, handoff state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing website source by itself.",
            "Distinguishes active issues from historical or resolved findings."
          ],
          "status": "specified"
        },
        {
          "constraints": [
            "Transient mutation UI requires a clear selected website and target.",
            "Transient evidence must preserve source/generated/runtime/deployment boundaries.",
            "Transient recovery must not perform follow-up mutation without another explicit action."
          ],
          "id": "website-builder.form.transient.setup-publish-and-handoff",
          "meaning": "Temporary setup, generation, confirmation, execution-progress, receipt, and recovery evidence for runtime configuration, publish, and Git handoff operations.",
          "primitive": "transient",
          "relationships": [
            "Supports explicit setup, publish, or handoff actions without becoming website source itself.",
            "May demand attention when storage, deployment, or repository risk requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches website subject."
          ],
          "status": "specified"
        }
      ],
      "id": "website-builder",
      "intent_count": 12,
      "intent_risk_counts": {
        "local-file-mutation": 3,
        "local-state": 3,
        "read-only": 4,
        "remote-mutation": 2
      },
      "mutation_intent_count": 8,
      "open_finding_count": 4,
      "planned_or_open_count": 51,
      "primary_user_goal": "Edit saved websites, configure optional site runtime layers, preview and publish to explicit lanes, and hand repository changes to Git Tools without confusing author-owned source, generated runtime evidence, deployment targets, or remote sync.",
      "prohibited_intent_count": 0,
      "region_count": 10,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "check": "primary-surface",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-primary-preview",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "required-regions-visible",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-required-regions",
          "mode": "default",
          "severity": "critical",
          "status": "specified"
        },
        {
          "check": "overlay-policy",
          "contract": "website-builder.contract.default.app-health",
          "id": "website-builder.runtime-check.default-overlay-policy",
          "mode": "default",
          "severity": "warning",
          "status": "specified"
        }
      ],
      "source": {
        "end_line": 196,
        "file": "pretty_docs/mcel-website-builder-requirements.md",
        "start_line": 162
      },
      "status": "specified",
      "status_counts": {
        "open": 4,
        "partially-implemented": 3,
        "planned": 6,
        "specified": 41
      },
      "target_runtime_status": "full-application-semantic-runtime",
      "title": "Website Builder and Websites",
      "use_cases": [
        {
          "goal": "Select a saved website, edit its visible content or styling, preview the draft, save the site source, and verify that the saved site still has a coherent manifest, builder state, entry HTML, stylesheet, script, and page runtime.",
          "id": "website-builder.use-case.edit-preview-saved-site",
          "status": "partially-implemented"
        },
        {
          "goal": "Configure or inspect the blog-capable site runtime without confusing source pages, local database artifacts, Directus storage, generated API routes, or published website files.",
          "id": "website-builder.use-case.configure-blog-runtime",
          "status": "partially-implemented"
        },
        {
          "goal": "Publish a saved website to one explicit lane, verify the target URL, and keep local authoring, local server, dev deployment, and remote production separate.",
          "id": "website-builder.use-case.publish-selected-lane",
          "status": "partially-implemented"
        },
        {
          "goal": "Turn saved website changes into reviewable repository evidence, then use Git Tools for file selection, commit, and governed push rather than hiding Git mutation inside Website Builder.",
          "id": "website-builder.use-case.git-tools-handoff",
          "status": "planned"
        }
      ]
    }
  ],
  "payload_version": "mcel-requirements-lab-payload-v1",
  "registry_version": "mcel-requirements-registry-v1",
  "runtime_diagnostic_contracts": {
    "calculator": {
      "app": "calculator",
      "mode_contracts": {
        "default": {
          "allowedRegions": [],
          "appId": "calculator",
          "checkCategories": [],
          "checks": [
            {
              "allowed_regions": [],
              "app": "calculator",
              "check": "overlay-policy",
              "check_category": "",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while the calculator is in default mode."
              ],
              "failure_message": "Calculator default mode should not be covered by diagnostic overlays.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "geometry_policies": [],
              "host_selector": "",
              "id": "calculator.runtime-check.default-overlay-policy",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "overlay.detector",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1071,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 1048
              },
              "source_binding": "calculator.binding.route-and-ui",
              "status": "specified",
              "test_binding": "calculator.test.route-checks"
            },
            {
              "allowed_regions": [],
              "app": "calculator",
              "check": "primary-surface",
              "check_category": "",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": ".calculator-workspace",
              "expects": [
                "Calculator workspace is visible and large enough for the active mode.",
                "The primary calculator surface is not collapsed by surrounding app chrome."
              ],
              "failure_message": "Calculator default mode must expose a usable workspace.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": ".calculator-workspace",
              "id": "calculator.runtime-check.default-primary-workspace",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "420",
              "mode": "default",
              "next_probe": "layout.ownerProbe",
              "observes": [
                ".calculator-workspace"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "calculator.surface.workspace",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 1016,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 994
              },
              "source_binding": "calculator.binding.route-and-ui",
              "status": "specified",
              "test_binding": "calculator.test.route-checks"
            },
            {
              "allowed_regions": [],
              "app": "calculator",
              "check": "required-regions-visible",
              "check_category": "",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Calculator app root is visible.",
                "Mode switch remains visible.",
                "Calculator workspace and display remain visible."
              ],
              "failure_message": "Calculator default mode must preserve root, controls, workspace, and display.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "calculator.runtime-check.default-required-regions",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "layout.baseline",
              "observes": [
                "#calculator-app",
                ".calculator-shell",
                ".calculator-mode-switch",
                ".calculator-workspace",
                "#calculator-display"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "calculator.region.root",
                  "label": "Calculator app root",
                  "selector": "#calculator-app"
                },
                {
                  "id": "calculator.region.shell",
                  "label": "Calculator shell",
                  "selector": ".calculator-shell"
                },
                {
                  "id": "calculator.region.mode-switch",
                  "label": "Calculator mode switch",
                  "selector": ".calculator-mode-switch"
                },
                {
                  "id": "calculator.region.workspace",
                  "label": "Calculator workspace",
                  "selector": ".calculator-workspace"
                },
                {
                  "id": "calculator.region.display",
                  "label": "Calculator display",
                  "selector": "#calculator-display"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 1046,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 1018
              },
              "source_binding": "calculator.binding.route-and-ui",
              "status": "specified",
              "test_binding": "calculator.test.route-checks"
            }
          ],
          "contractId": "calculator.contract.default.app-health",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [],
          "forbiddenRegions": [],
          "geometryPolicies": [],
          "lifecycleAssertions": [],
          "mode": "default",
          "optionalRegions": [],
          "overlayPolicy": [],
          "primarySurface": {
            "editorSelector": ".calculator-workspace",
            "hostSelector": ".calculator-workspace",
            "id": "calculator.surface.workspace",
            "label": "Calculator default mode must expose a usable workspace.",
            "minHeight": 320,
            "minWidth": 420
          },
          "requiredRegions": [
            {
              "id": "calculator.region.root",
              "label": "Calculator app root",
              "selector": "#calculator-app"
            },
            {
              "id": "calculator.region.shell",
              "label": "Calculator shell",
              "selector": ".calculator-shell"
            },
            {
              "id": "calculator.region.mode-switch",
              "label": "Calculator mode switch",
              "selector": ".calculator-mode-switch"
            },
            {
              "id": "calculator.region.workspace",
              "label": "Calculator workspace",
              "selector": ".calculator-workspace"
            },
            {
              "id": "calculator.region.display",
              "label": "Calculator display",
              "selector": "#calculator-display"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    },
    "code-editor": {
      "app": "code-editor",
      "mode_contracts": {
        "authoring": {
          "allowedRegions": [
            {
              "id": "code-editor.allowed.mcel-tools-toggle",
              "label": "MCEL tools toggle projection",
              "selector": "#code-editor-mcel-tools-toggle"
            },
            {
              "id": "code-editor.allowed.diagnostics-counter",
              "label": "Ambient integrity feedback projection",
              "selector": "#code-editor-diagnostics-counter"
            }
          ],
          "appId": "code-editor",
          "checkCategories": [
            "overlays",
            "lifecycle",
            "surface",
            "layout",
            "form"
          ],
          "checks": [
            {
              "allowed_regions": [],
              "app": "code-editor",
              "check": "forbidden-surfaces-hidden",
              "check_category": "overlays",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Source model pane is hidden.",
                "Serialized and contract panes are hidden.",
                "Generated runtime window/layout/file rail are absent from the default path.",
                "Fallback textarea is not visible in the Monaco golden path.",
                "Proof docks and active widget editor overlays are not visible in authoring mode; the inert widget-editor shell is not treated as a visible overlay."
              ],
              "failure_message": "MCEL diagnostic/runtime scaffolding must not leak into Code Editor authoring mode.",
              "focus": "forbidden-surfaces",
              "forbidden_regions": [],
              "forbids": [
                "code-editor.forbidden.source-pane | [data-code-studio-pane=\"source\"] | MCEL source model pane",
                "code-editor.forbidden.serialized-pane | [data-code-studio-pane=\"serialized\"] | Serialized output pane",
                "code-editor.forbidden.contract-pane | [data-code-studio-pane=\"contract\"] | Contract report pane",
                "code-editor.forbidden.runtime-scaffold.window | .code-studio-runtime-window | Generated runtime window scaffold",
                "code-editor.forbidden.runtime-scaffold.layout | .code-studio-runtime-layout | Generated runtime layout scaffold",
                "code-editor.forbidden.runtime-file-rail | .code-studio-runtime-files | Generated runtime file rail",
                "code-editor.forbidden.fallback-textarea | #code-studio-runtime-draft, .code-studio-runtime-fallback | Fallback textarea",
                "code-editor.forbidden.proof-dock | .code-studio-proof-dock, #code-studio-bottom-panel | MCEL proof/evidence dock",
                "code-editor.forbidden.widget-overlay | #mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden]) | Active widget editor overlay"
              ],
              "geometry_policies": [],
              "host_selector": "",
              "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "authoring",
              "next_probe": "overlay.detector",
              "observes": [
                "[data-code-studio-pane=\\\"source\\\"]",
                "[data-code-studio-pane=\\\"serialized\\\"]",
                "[data-code-studio-pane=\\\"contract\\\"]",
                ".code-studio-runtime-window",
                ".code-studio-runtime-layout",
                ".code-studio-runtime-files",
                "#code-studio-runtime-draft",
                ".code-studio-runtime-fallback",
                ".code-studio-proof-dock",
                "#code-studio-bottom-panel",
                "#mc-widget-editor-pane.open",
                ".mc-widget-selection:not([hidden])",
                ".mc-widget-dock-preview:not([hidden])"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 938,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 894
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "allowed_regions": [],
              "app": "code-editor",
              "check": "lifecycle-contract-preserved",
              "check_category": "lifecycle",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Startup authoring mode has exactly one primary Monaco editor.",
                "Clicking another file keeps exactly one primary Monaco editor.",
                "Resize keeps the Monaco host and editor useful."
              ],
              "failure_message": "File selection and reload must preserve the Code Editor authoring contract.",
              "focus": "startup-file-click-resize",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "code-editor.runtime-check.authoring-lifecycle",
              "lifecycle_assertions": [
                "startup-authoring-mode-has-one-primary-editor",
                "file-click-keeps-one-primary-editor",
                "resize-keeps-primary-editor-usable",
                "mcel-diagnostics-hidden-in-authoring"
              ],
              "min_height": "",
              "min_width": "",
              "mode": "authoring",
              "next_probe": "startup.timeline",
              "observes": [
                "startup",
                "file-click",
                "resize"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 967,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 940
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "allowed_regions": [],
              "app": "code-editor",
              "check": "primary-surface",
              "check_category": "surface",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": ".monaco-editor",
              "expects": [
                "Monaco host is visible and at least 520px wide by 320px tall.",
                "Monaco editor instance is visible and at least 520px wide by 320px tall.",
                "No fallback or source-model editor surface competes with Monaco in authoring mode."
              ],
              "failure_message": "Authoring mode must expose one usable Monaco selected-file editor.",
              "focus": "primary-editor",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "#code-studio-runtime-monaco",
              "id": "code-editor.runtime-check.authoring-primary-monaco",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "520",
              "mode": "authoring",
              "next_probe": "layout.ownerProbe",
              "observes": [
                "#code-studio-runtime-monaco",
                ".monaco-editor"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "code-editor.surface.monaco-selected-file-editor",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 816,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 790
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "allowed_regions": [],
              "app": "code-editor",
              "check": "required-regions-visible",
              "check_category": "layout",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Code Editor root is present and visible.",
                "Explorer region is present and visible.",
                "Editor group is present and visible.",
                "Status bar is present and visible."
              ],
              "failure_message": "Authoring mode must preserve the app root, explorer, editor group, and status bar.",
              "focus": "required-regions",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "code-editor.runtime-check.authoring-required-regions",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "authoring",
              "next_probe": "layout.baseline",
              "observes": [
                "#code-editor-app",
                ".code-studio-sidebar",
                ".code-studio-editor-group",
                ".code-studio-statusbar"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "code-editor.region.root",
                  "label": "Code Editor app root",
                  "selector": "#code-editor-app"
                },
                {
                  "id": "code-editor.region.explorer",
                  "label": "Explorer",
                  "selector": ".code-studio-sidebar"
                },
                {
                  "id": "code-editor.region.editor-group",
                  "label": "Editor group",
                  "selector": ".code-studio-editor-group"
                },
                {
                  "id": "code-editor.region.statusbar",
                  "label": "Status bar",
                  "selector": ".code-studio-statusbar"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 847,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 818
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "allowed_regions": [
                {
                  "id": "code-editor.allowed.mcel-tools-toggle",
                  "label": "MCEL tools toggle projection",
                  "selector": "#code-editor-mcel-tools-toggle"
                },
                {
                  "id": "code-editor.allowed.diagnostics-counter",
                  "label": "Ambient integrity feedback projection",
                  "selector": "#code-editor-diagnostics-counter"
                }
              ],
              "app": "code-editor",
              "check": "secondary-surface-policy",
              "check_category": "form",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Supporting reasoning, evidence, diagnostics, and assistant context are allowed in authoring mode as non-primary projections.",
                "Supporting projections may be visible, collapsed, tabbed, deferred, or trigger-only without becoming the primary editor.",
                "MCEL tools, diagnosis history, contract findings, source ownership, and test ownership must project from owned context or feedback primitives, or from an explicit mode.",
                "Supporting projections must not cover the Monaco editor or reduce it below its minimum geometry."
              ],
              "failure_message": "Supporting context and feedback projections are allowed when they do not compete with the selected-source editor.",
              "focus": "supporting-context-feedback-projection",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [
                "supporting-projection-visible-min-width-240",
                "supporting-projection-max-width-ratio-0.40",
                "supporting-projection-must-collapse-before-primary-breaks"
              ],
              "host_selector": "",
              "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "authoring",
              "next_probe": "semanticProjection.containment",
              "observes": [
                ".code-studio-inspector",
                "[data-code-studio-workbench-region=\\\"scm-ai-inspector\\\"]",
                "#code-editor-mcel-tools-toggle",
                "#code-editor-diagnostics-counter"
              ],
              "optional_regions": [
                {
                  "id": "code-editor.region.inspector",
                  "label": "Supporting reasoning/evidence projection",
                  "selector": ".code-studio-inspector"
                }
              ],
              "overlay_policy": [
                "diagnostics-owned-by-supporting-or-feedback-projection-are-allowed",
                "diagnostics-covering-primary-editor-are-forbidden"
              ],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 892,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 850
              },
              "source_binding": "code-editor.binding.authoring-cockpit-layout",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-cockpit-diagnosis"
            }
          ],
          "contractId": "code-editor.contract.authoring.monaco-golden-path",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [
            "forbidden-surfaces",
            "startup-file-click-resize",
            "primary-editor",
            "required-regions",
            "supporting-context-feedback-projection"
          ],
          "forbiddenRegions": [
            {
              "id": "code-editor.forbidden.source-pane",
              "label": "MCEL source model pane",
              "selector": "[data-code-studio-pane=\"source\"]"
            },
            {
              "id": "code-editor.forbidden.serialized-pane",
              "label": "Serialized output pane",
              "selector": "[data-code-studio-pane=\"serialized\"]"
            },
            {
              "id": "code-editor.forbidden.contract-pane",
              "label": "Contract report pane",
              "selector": "[data-code-studio-pane=\"contract\"]"
            },
            {
              "id": "code-editor.forbidden.runtime-scaffold.window",
              "label": "Generated runtime window scaffold",
              "selector": ".code-studio-runtime-window"
            },
            {
              "id": "code-editor.forbidden.runtime-scaffold.layout",
              "label": "Generated runtime layout scaffold",
              "selector": ".code-studio-runtime-layout"
            },
            {
              "id": "code-editor.forbidden.runtime-file-rail",
              "label": "Generated runtime file rail",
              "selector": ".code-studio-runtime-files"
            },
            {
              "id": "code-editor.forbidden.fallback-textarea",
              "label": "Fallback textarea",
              "selector": "#code-studio-runtime-draft, .code-studio-runtime-fallback"
            },
            {
              "id": "code-editor.forbidden.proof-dock",
              "label": "MCEL proof/evidence dock",
              "selector": ".code-studio-proof-dock, #code-studio-bottom-panel"
            },
            {
              "id": "code-editor.forbidden.widget-overlay",
              "label": "Active widget editor overlay",
              "selector": "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])"
            }
          ],
          "geometryPolicies": [
            "supporting-projection-visible-min-width-240",
            "supporting-projection-max-width-ratio-0.40",
            "supporting-projection-must-collapse-before-primary-breaks"
          ],
          "lifecycleAssertions": [
            "startup-authoring-mode-has-one-primary-editor",
            "file-click-keeps-one-primary-editor",
            "resize-keeps-primary-editor-usable",
            "mcel-diagnostics-hidden-in-authoring"
          ],
          "mode": "authoring",
          "optionalRegions": [
            {
              "id": "code-editor.region.inspector",
              "label": "Supporting reasoning/evidence projection",
              "selector": ".code-studio-inspector"
            }
          ],
          "overlayPolicy": [
            "diagnostics-owned-by-supporting-or-feedback-projection-are-allowed",
            "diagnostics-covering-primary-editor-are-forbidden"
          ],
          "primarySurface": {
            "editorSelector": ".monaco-editor",
            "hostSelector": "#code-studio-runtime-monaco",
            "id": "code-editor.surface.monaco-selected-file-editor",
            "label": "Authoring mode must expose one usable Monaco selected-file editor.",
            "minHeight": 320,
            "minWidth": 520
          },
          "requiredRegions": [
            {
              "id": "code-editor.region.root",
              "label": "Code Editor app root",
              "selector": "#code-editor-app"
            },
            {
              "id": "code-editor.region.explorer",
              "label": "Explorer",
              "selector": ".code-studio-sidebar"
            },
            {
              "id": "code-editor.region.editor-group",
              "label": "Editor group",
              "selector": ".code-studio-editor-group"
            },
            {
              "id": "code-editor.region.statusbar",
              "label": "Status bar",
              "selector": ".code-studio-statusbar"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    },
    "file-explorer": {
      "app": "file-explorer",
      "mode_contracts": {
        "default": {
          "allowedRegions": [],
          "appId": "file-explorer",
          "checkCategories": [],
          "checks": [
            {
              "allowed_regions": [],
              "app": "file-explorer",
              "check": "overlay-policy",
              "check_category": "",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while browsing files."
              ],
              "failure_message": "File Explorer should not be covered by diagnostic overlays in default mode.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "geometry_policies": [],
              "host_selector": "",
              "id": "file-explorer.runtime-check.default-overlay-policy",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "overlay.detector",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1059,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 1036
              },
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "status": "specified",
              "test_binding": "file-explorer.test.viewport-file-explorer"
            },
            {
              "allowed_regions": [],
              "app": "file-explorer",
              "check": "primary-surface",
              "check_category": "",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": ".file-explorer-main",
              "expects": [
                "File Explorer main browsing surface is visible and usable.",
                "The list/preview work area is not collapsed."
              ],
              "failure_message": "File Explorer default mode must expose a usable browsing surface.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": ".file-explorer-main",
              "id": "file-explorer.runtime-check.default-primary-surface",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "420",
              "mode": "default",
              "next_probe": "layout.ownerProbe",
              "observes": [
                ".file-explorer-main"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "file-explorer.surface.main",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 1006,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 984
              },
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "status": "specified",
              "test_binding": "file-explorer.test.viewport-file-explorer"
            },
            {
              "allowed_regions": [],
              "app": "file-explorer",
              "check": "required-regions-visible",
              "check_category": "",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, roots panel, toolbar, main surface, and file list are visible."
              ],
              "failure_message": "File Explorer default mode must preserve roots, toolbar, and list.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "file-explorer.runtime-check.default-required-regions",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "layout.baseline",
              "observes": [
                "#file-explorer-app",
                ".file-explorer-roots-panel",
                ".file-explorer-main",
                ".file-explorer-toolbar",
                "#file-explorer-list"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "file-explorer.region.root",
                  "label": "File Explorer app root",
                  "selector": "#file-explorer-app"
                },
                {
                  "id": "file-explorer.region.roots",
                  "label": "Roots panel",
                  "selector": ".file-explorer-roots-panel"
                },
                {
                  "id": "file-explorer.region.main",
                  "label": "Main browsing surface",
                  "selector": ".file-explorer-main"
                },
                {
                  "id": "file-explorer.region.toolbar",
                  "label": "Path/search toolbar",
                  "selector": ".file-explorer-toolbar"
                },
                {
                  "id": "file-explorer.region.list",
                  "label": "File list",
                  "selector": "#file-explorer-list"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 1034,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 1008
              },
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "status": "specified",
              "test_binding": "file-explorer.test.viewport-file-explorer"
            }
          ],
          "contractId": "file-explorer.contract.default.app-health",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [],
          "forbiddenRegions": [],
          "geometryPolicies": [],
          "lifecycleAssertions": [],
          "mode": "default",
          "optionalRegions": [],
          "overlayPolicy": [],
          "primarySurface": {
            "editorSelector": ".file-explorer-main",
            "hostSelector": ".file-explorer-main",
            "id": "file-explorer.surface.main",
            "label": "File Explorer default mode must expose a usable browsing surface.",
            "minHeight": 320,
            "minWidth": 420
          },
          "requiredRegions": [
            {
              "id": "file-explorer.region.root",
              "label": "File Explorer app root",
              "selector": "#file-explorer-app"
            },
            {
              "id": "file-explorer.region.roots",
              "label": "Roots panel",
              "selector": ".file-explorer-roots-panel"
            },
            {
              "id": "file-explorer.region.main",
              "label": "Main browsing surface",
              "selector": ".file-explorer-main"
            },
            {
              "id": "file-explorer.region.toolbar",
              "label": "Path/search toolbar",
              "selector": ".file-explorer-toolbar"
            },
            {
              "id": "file-explorer.region.list",
              "label": "File list",
              "selector": "#file-explorer-list"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    },
    "git-tools": {
      "app": "git-tools",
      "mode_contracts": {
        "default": {
          "allowedRegions": [],
          "appId": "git-tools",
          "checkCategories": [],
          "checks": [
            {
              "allowed_regions": [],
              "app": "git-tools",
              "check": "overlay-policy",
              "check_category": "",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while running the default Git Tools workflow."
              ],
              "failure_message": "Git Tools default mode should not be covered by diagnostic overlays.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "geometry_policies": [],
              "host_selector": "",
              "id": "git-tools.runtime-check.default-overlay-policy",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "overlay.detector",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1141,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1118
              },
              "source_binding": "git-tools.binding.project-workflow",
              "status": "specified",
              "test_binding": "git-tools.test.semantic-adapter"
            },
            {
              "allowed_regions": [],
              "app": "git-tools",
              "check": "primary-surface",
              "check_category": "",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "#git-project-workflow-surface",
              "expects": [
                "Git Tools project workflow surface is visible and usable.",
                "The workflow surface is not collapsed by rails or proof panels."
              ],
              "failure_message": "Git Tools default mode must expose a usable workflow surface.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "#git-project-workflow-surface",
              "id": "git-tools.runtime-check.default-primary-workflow",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "420",
              "mode": "default",
              "next_probe": "layout.ownerProbe",
              "observes": [
                "#git-project-workflow-surface"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "git-tools.surface.workflow",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 1090,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1068
              },
              "source_binding": "git-tools.binding.project-workflow",
              "status": "specified",
              "test_binding": "git-tools.test.semantic-adapter"
            },
            {
              "allowed_regions": [],
              "app": "git-tools",
              "check": "required-regions-visible",
              "check_category": "",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, shell, project selector, and workflow surface remain visible."
              ],
              "failure_message": "Git Tools default mode must preserve project selection and workflow.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "git-tools.runtime-check.default-required-regions",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "layout.baseline",
              "observes": [
                "#git-tools-app",
                ".git-tools-shell",
                "#git-project-selector-panel",
                "#git-project-workflow-surface"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "git-tools.region.root",
                  "label": "Git Tools app root",
                  "selector": "#git-tools-app"
                },
                {
                  "id": "git-tools.region.shell",
                  "label": "Git Tools shell",
                  "selector": ".git-tools-shell"
                },
                {
                  "id": "git-tools.region.project-selector",
                  "label": "Project selector",
                  "selector": "#git-project-selector-panel"
                },
                {
                  "id": "git-tools.region.workflow",
                  "label": "Project workflow surface",
                  "selector": "#git-project-workflow-surface"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 1116,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1092
              },
              "source_binding": "git-tools.binding.project-workflow",
              "status": "specified",
              "test_binding": "git-tools.test.semantic-adapter"
            }
          ],
          "contractId": "git-tools.contract.default.app-health",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [],
          "forbiddenRegions": [],
          "geometryPolicies": [],
          "lifecycleAssertions": [],
          "mode": "default",
          "optionalRegions": [],
          "overlayPolicy": [],
          "primarySurface": {
            "editorSelector": "#git-project-workflow-surface",
            "hostSelector": "#git-project-workflow-surface",
            "id": "git-tools.surface.workflow",
            "label": "Git Tools default mode must expose a usable workflow surface.",
            "minHeight": 320,
            "minWidth": 420
          },
          "requiredRegions": [
            {
              "id": "git-tools.region.root",
              "label": "Git Tools app root",
              "selector": "#git-tools-app"
            },
            {
              "id": "git-tools.region.shell",
              "label": "Git Tools shell",
              "selector": ".git-tools-shell"
            },
            {
              "id": "git-tools.region.project-selector",
              "label": "Project selector",
              "selector": "#git-project-selector-panel"
            },
            {
              "id": "git-tools.region.workflow",
              "label": "Project workflow surface",
              "selector": "#git-project-workflow-surface"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    },
    "mcel-lab": {
      "app": "mcel-lab",
      "mode_contracts": {
        "default": {
          "allowedRegions": [],
          "appId": "mcel-lab",
          "checkCategories": [
            "surface",
            "form",
            "contract",
            "layout"
          ],
          "checks": [
            {
              "allowed_regions": [],
              "app": "mcel-lab",
              "check": "primary-surface",
              "check_category": "surface",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "editor_selector": "#mcel-blueprint-work-surface",
              "expects": [
                "Selected AppBlueprint workspace is visible and usable."
              ],
              "failure_message": "Selected app/aspect work surface is missing or unusable.",
              "focus": "blueprint-workspace",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": ".mcel-lab-blueprint-primary",
              "id": "mcel-lab.runtime.primary-blueprint-workspace",
              "lifecycle_assertions": [],
              "min_height": "420",
              "min_width": "640",
              "mode": "default",
              "next_probe": "lab.form.detector",
              "observes": [
                "mcel-lab.form.work-surface.blueprint-inspection"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "mcel-lab.form.work-surface.blueprint-inspection",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 597,
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 576
              },
              "source_binding": "",
              "status": "specified",
              "test_binding": ""
            },
            {
              "allowed_regions": [],
              "app": "mcel-lab",
              "check": "required-regions-visible",
              "check_category": "form",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "editor_selector": "",
              "expects": [
                "App root, selection context, aspect map, primary blueprint workspace, and owned feedback are present."
              ],
              "failure_message": "MCEL Lab semantic form projections are missing from the rendered workbench.",
              "focus": "semantic-projections",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "mcel-lab.runtime.required-semantic-projections",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "lab.form.detector",
              "observes": [
                "mcel-lab.form.subject.app-blueprint",
                "mcel-lab.form.context.app-and-aspect-selection",
                "mcel-lab.form.feedback.validation-and-mount-state"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "mcel-lab.region.app-root",
                  "label": "Lab app root",
                  "selector": "#mcel-lab-app"
                },
                {
                  "id": "mcel-lab.region.selection-context",
                  "label": "App selection context",
                  "selector": "#mcel-blueprint-app-select"
                },
                {
                  "id": "mcel-lab.region.selection-context",
                  "label": "Aspect selection context",
                  "selector": "#mcel-blueprint-aspect-select"
                },
                {
                  "id": "mcel-lab.region.aspect-map",
                  "label": "Aspect map projection",
                  "selector": ".mcel-lab-blueprint-navigation"
                },
                {
                  "id": "mcel-lab.region.blueprint-workspace",
                  "label": "Blueprint inspection workspace",
                  "selector": ".mcel-lab-blueprint-primary"
                },
                {
                  "id": "mcel-lab.region.feedback-and-findings",
                  "label": "Mount and validation feedback",
                  "selector": "#mcel-blueprint-work-badge"
                }
              ],
              "severity": "error",
              "source": {
                "end_line": 624,
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 599
              },
              "source_binding": "",
              "status": "specified",
              "test_binding": ""
            },
            {
              "allowed_regions": [],
              "app": "mcel-lab",
              "check": "lifecycle-contract-preserved",
              "check_category": "contract",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "editor_selector": "",
              "expects": [
                "Self-hosting inspection can create draft annotations or export context but cannot directly rewrite live Lab implementation files."
              ],
              "failure_message": "MCEL Lab self-hosting safety boundary is not observable.",
              "focus": "self-hosting-safety",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [
                "semantic-form-projections-must-not-obscure-blueprint-workspace"
              ],
              "host_selector": "",
              "id": "mcel-lab.runtime.self-hosting-safety-boundary",
              "lifecycle_assertions": [
                "self-hosting-draft-does-not-apply-itself",
                "repair-export-remains-reviewable-before-patch-workflow"
              ],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "lab.self-hosting.boundary",
              "observes": [
                "mcel-lab.form.constraint.self-hosting-safety",
                "mcel-lab.form.interruption.unsafe-repair-boundary"
              ],
              "optional_regions": [],
              "overlay_policy": [
                "point-inspection-transient-is-mode-bound"
              ],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 679,
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 655
              },
              "source_binding": "",
              "status": "specified",
              "test_binding": ""
            },
            {
              "allowed_regions": [],
              "app": "mcel-lab",
              "check": "visual-integrity-baseline",
              "check_category": "layout",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "editor_selector": "",
              "expects": [
                "Every rendered semantic projection owns its visible text, controls, and child surfaces.",
                "Readable content must not paint across neighboring semantic surfaces.",
                "Stacked cards, buttons, summaries, feedback rows, and evidence panels must not overlap each other.",
                "Scroll containers must contain overflow instead of letting content visually overwrite nearby regions."
              ],
              "failure_message": "MCEL Lab has a visual-integrity failure: semantic projections collide, bleed, clip, or overwrite readable content.",
              "focus": "semantic-projection-readability",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [
                "owned-semantic-projections-must-not-overlap",
                "readable-text-must-remain-inside-owning-surface",
                "scroll-containers-must-contain-child-content",
                "primary-work-surface-must-not-be-occluded-by-context-or-feedback"
              ],
              "host_selector": "",
              "id": "mcel-lab.runtime.visual-integrity-baseline",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "layout.visualIntegrityProbe",
              "observes": [
                "mcel-lab.form.work-surface.blueprint-inspection",
                "mcel-lab.form.context.app-and-aspect-selection",
                "mcel-lab.form.context.rendered-element-evidence",
                "mcel-lab.form.feedback.validation-and-mount-state"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 653,
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 626
              },
              "source_binding": "",
              "status": "specified",
              "test_binding": ""
            }
          ],
          "contractId": "mcel-lab.contract.default.blueprint-studio-health",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [
            "blueprint-workspace",
            "semantic-projections",
            "self-hosting-safety",
            "semantic-projection-readability"
          ],
          "forbiddenRegions": [],
          "geometryPolicies": [
            "semantic-form-projections-must-not-obscure-blueprint-workspace",
            "owned-semantic-projections-must-not-overlap",
            "readable-text-must-remain-inside-owning-surface",
            "scroll-containers-must-contain-child-content",
            "primary-work-surface-must-not-be-occluded-by-context-or-feedback"
          ],
          "lifecycleAssertions": [
            "self-hosting-draft-does-not-apply-itself",
            "repair-export-remains-reviewable-before-patch-workflow"
          ],
          "mode": "default",
          "optionalRegions": [],
          "overlayPolicy": [
            "point-inspection-transient-is-mode-bound"
          ],
          "primarySurface": {
            "editorSelector": "#mcel-blueprint-work-surface",
            "hostSelector": ".mcel-lab-blueprint-primary",
            "id": "mcel-lab.form.work-surface.blueprint-inspection",
            "label": "Selected app/aspect work surface is missing or unusable.",
            "minHeight": 420,
            "minWidth": 640
          },
          "requiredRegions": [
            {
              "id": "mcel-lab.region.app-root",
              "label": "Lab app root",
              "selector": "#mcel-lab-app"
            },
            {
              "id": "mcel-lab.region.selection-context",
              "label": "App selection context",
              "selector": "#mcel-blueprint-app-select"
            },
            {
              "id": "mcel-lab.region.selection-context",
              "label": "Aspect selection context",
              "selector": "#mcel-blueprint-aspect-select"
            },
            {
              "id": "mcel-lab.region.aspect-map",
              "label": "Aspect map projection",
              "selector": ".mcel-lab-blueprint-navigation"
            },
            {
              "id": "mcel-lab.region.blueprint-workspace",
              "label": "Blueprint inspection workspace",
              "selector": ".mcel-lab-blueprint-primary"
            },
            {
              "id": "mcel-lab.region.feedback-and-findings",
              "label": "Mount and validation feedback",
              "selector": "#mcel-blueprint-work-badge"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    },
    "website-builder": {
      "app": "website-builder",
      "mode_contracts": {
        "default": {
          "allowedRegions": [],
          "appId": "website-builder",
          "checkCategories": [],
          "checks": [
            {
              "allowed_regions": [],
              "app": "website-builder",
              "check": "overlay-policy",
              "check_category": "",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while using the default builder surface."
              ],
              "failure_message": "Website Builder default mode should not be covered by diagnostic overlays.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "geometry_policies": [],
              "host_selector": "",
              "id": "website-builder.runtime-check.default-overlay-policy",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "overlay.detector",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1219,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1196
              },
              "source_binding": "website-builder.binding.builder-runtime",
              "status": "specified",
              "test_binding": "website-builder.test.documentation-contract"
            },
            {
              "allowed_regions": [],
              "app": "website-builder",
              "check": "primary-surface",
              "check_category": "",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": "[data-mcel-surface-id='website-builder.surface.preview']",
              "expects": [
                "Website Builder preview/design surface is visible and usable.",
                "The selected site surface is not collapsed by inspector or publishing panels."
              ],
              "failure_message": "Website Builder default mode must expose a usable preview/design surface.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "[data-mcel-surface-id='website-builder.surface.preview']",
              "id": "website-builder.runtime-check.default-primary-preview",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "420",
              "mode": "default",
              "next_probe": "layout.ownerProbe",
              "observes": [
                "[data-mcel-surface-id='website-builder.surface.preview']"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "website-builder.surface.preview",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 1166,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1144
              },
              "source_binding": "website-builder.binding.builder-runtime",
              "status": "specified",
              "test_binding": "website-builder.test.documentation-contract"
            },
            {
              "allowed_regions": [],
              "app": "website-builder",
              "check": "required-regions-visible",
              "check_category": "",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, shell, summary, preview, and inspector remain visible."
              ],
              "failure_message": "Website Builder default mode must preserve summary, preview, and inspector.",
              "focus": "",
              "forbidden_regions": [],
              "forbids": [],
              "geometry_policies": [],
              "host_selector": "",
              "id": "website-builder.runtime-check.default-required-regions",
              "lifecycle_assertions": [],
              "min_height": "",
              "min_width": "",
              "mode": "default",
              "next_probe": "layout.baseline",
              "observes": [
                "#website-builder-app",
                ".website-builder-main",
                ".website-builder-summary",
                "[data-mcel-surface-id='website-builder.surface.preview']",
                ".website-builder-inspector"
              ],
              "optional_regions": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "primary_surface_id": "",
              "required_regions": [
                {
                  "id": "website-builder.region.root",
                  "label": "Website Builder app root",
                  "selector": "#website-builder-app"
                },
                {
                  "id": "website-builder.region.main",
                  "label": "Website Builder shell",
                  "selector": ".website-builder-main"
                },
                {
                  "id": "website-builder.region.summary",
                  "label": "Website summary",
                  "selector": ".website-builder-summary"
                },
                {
                  "id": "website-builder.region.preview",
                  "label": "Preview/design surface",
                  "selector": "[data-mcel-surface-id='website-builder.surface.preview']"
                },
                {
                  "id": "website-builder.region.inspector",
                  "label": "Inspector",
                  "selector": ".website-builder-inspector"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 1194,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1168
              },
              "source_binding": "website-builder.binding.builder-runtime",
              "status": "specified",
              "test_binding": "website-builder.test.documentation-contract"
            }
          ],
          "contractId": "website-builder.contract.default.app-health",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "focusModes": [],
          "forbiddenRegions": [],
          "geometryPolicies": [],
          "lifecycleAssertions": [],
          "mode": "default",
          "optionalRegions": [],
          "overlayPolicy": [],
          "primarySurface": {
            "editorSelector": "[data-mcel-surface-id='website-builder.surface.preview']",
            "hostSelector": "[data-mcel-surface-id='website-builder.surface.preview']",
            "id": "website-builder.surface.preview",
            "label": "Website Builder default mode must expose a usable preview/design surface.",
            "minHeight": 320,
            "minWidth": 420
          },
          "requiredRegions": [
            {
              "id": "website-builder.region.root",
              "label": "Website Builder app root",
              "selector": "#website-builder-app"
            },
            {
              "id": "website-builder.region.main",
              "label": "Website Builder shell",
              "selector": ".website-builder-main"
            },
            {
              "id": "website-builder.region.summary",
              "label": "Website summary",
              "selector": ".website-builder-summary"
            },
            {
              "id": "website-builder.region.preview",
              "label": "Preview/design surface",
              "selector": "[data-mcel-surface-id='website-builder.surface.preview']"
            },
            {
              "id": "website-builder.region.inspector",
              "label": "Inspector",
              "selector": ".website-builder-inspector"
            }
          ],
          "source": "mcel-runtime-check"
        }
      }
    }
  },
  "source": "pretty_docs/*.md",
  "strict_schema_ready": true,
  "summary": {
    "app_contracts": [
      "calculator",
      "code-editor",
      "file-explorer",
      "git-tools",
      "mcel-lab",
      "website-builder"
    ],
    "app_counts": {
      "calculator": 47,
      "code-editor": 44,
      "file-explorer": 44,
      "git-tools": 51,
      "mcel-lab": 38,
      "website-builder": 54
    },
    "block_type_counts": {
      "mcel-acceptance": 18,
      "mcel-app": 6,
      "mcel-finding": 18,
      "mcel-form-primitive": 40,
      "mcel-grammar": 18,
      "mcel-intent": 57,
      "mcel-layout-pattern": 1,
      "mcel-region": 50,
      "mcel-requirement": 55,
      "mcel-runtime-check": 21,
      "mcel-source-binding": 2,
      "mcel-test-binding": 2,
      "mcel-use-case": 15
    },
    "error_count": 0,
    "pretty_docs_root": "pretty_docs",
    "registry_version": "mcel-requirements-registry-v1",
    "repo_root": "<repo-root>",
    "strict_schema_ready": true,
    "total_blocks": 303,
    "valid": true,
    "warning_count": 0
  },
  "truth_gate": "requirements describe the contract; adapters and tests prove implementation",
  "valid": true
}
);

    function clonePlain(value) {
      if (value == null || typeof value !== "object") return value;
      if (Array.isArray(value)) return value.map(clonePlain);
      return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, clonePlain(entry)]));
    }

    function normalizeAppId(value) {
      return String(value || "").trim();
    }

    function getSummary() {
      return clonePlain(PAYLOAD.summary);
    }

    function listAppContracts() {
      return PAYLOAD.apps.map(clonePlain);
    }

    function getAppContract(appId) {
      const id = normalizeAppId(appId);
      const contract = PAYLOAD.app_contracts[id] || null;
      return clonePlain(contract);
    }

    function getRuntimeDiagnosisContracts(appId) {
      const id = normalizeAppId(appId);
      const contracts = PAYLOAD.runtime_diagnostic_contracts?.[id] || null;
      return clonePlain(contracts);
    }

    function getRuntimeDiagnosisContract(appId, mode = "authoring") {
      const contracts = getRuntimeDiagnosisContracts(appId);
      const modeContracts = contracts?.mode_contracts || {};
      return clonePlain(modeContracts[String(mode || "authoring")] || null);
    }

    function listRuntimeDiagnosisContracts() {
      return clonePlain(PAYLOAD.runtime_diagnostic_contracts || {});
    }

    function compareAppToRuntime(appId, runtimeReadiness = {}) {
      const id = normalizeAppId(appId);
      const contract = PAYLOAD.app_contracts[id] || null;
      const runtime = runtimeReadiness || {};
      if (!contract) {
        return {
          app: id,
          requirementsContractPresent: false,
          comparisonStatus: "missing-requirements-contract",
          gaps: ["No MCEL requirements contract was found for this app."]
        };
      }

      const runtimePresent = runtime.registryAdapterPresent === true || Boolean(runtime.adapter || runtime.adapterId);
      const runtimeCoreReady = runtime.runtimeCoreReady === true;
      const fullApplicationSemanticReady = runtime.fullApplicationSemanticReady === true;
      const gaps = [];

      if (!runtimePresent) gaps.push("No live domain adapter snapshot is available.");
      if (contract.target_runtime_status === "fullApplicationSemanticReady" && !fullApplicationSemanticReady) {
        gaps.push("Requirements target full application semantic readiness, but runtime readiness does not prove it.");
      }
      if (contract.target_runtime_status === "scope-limited-semantic-runtime" && !runtimeCoreReady) {
        gaps.push("Requirements target a scope-limited semantic runtime, but runtime core readiness is not proven.");
      }
      if (contract.mutation_intent_count > 0 && runtimePresent && runtime.executableIntentCount === 0) {
        gaps.push("Requirements include mutation intents, but runtime exposes no executable intents.");
      }

      return {
        app: id,
        requirementsContractPresent: true,
        requirementsContractComplete: contract.contract_complete === true,
        comparisonStatus: gaps.length ? "requirements-runtime-gap" : "requirements-runtime-aligned-or-unverified",
        requirements: {
          currentRuntimeStatus: contract.current_runtime_status,
          targetRuntimeStatus: contract.target_runtime_status,
          useCaseCount: contract.use_cases.length,
          regionCount: contract.region_count,
          intentCount: contract.intent_count,
          mutationIntentCount: contract.mutation_intent_count,
          prohibitedIntentCount: contract.prohibited_intent_count,
          openFindingCount: contract.open_finding_count
        },
        runtime: clonePlain(runtime),
        gaps
      };
    }

    function compareAllApps(runtimeRegistry = global.McelDomainAdapterRegistry) {
      return listAppContracts().map((contract) => {
        let readiness = null;
        if (runtimeRegistry && typeof runtimeRegistry.evaluateAdapterReadiness === "function") {
          try {
            readiness = runtimeRegistry.evaluateAdapterReadiness(contract.app);
          } catch (error) {
            readiness = {
              error: {
                name: error?.name || "Error",
                message: error?.message || String(error)
              }
            };
          }
        }
        return compareAppToRuntime(contract.app, readiness || {});
      });
    }

    function buildLabComparisonSnapshot(runtimeRegistry = global.McelDomainAdapterRegistry) {
      const comparisons = compareAllApps(runtimeRegistry);
      const statusCounts = comparisons.reduce((counts, comparison) => {
        counts[comparison.comparisonStatus] = (counts[comparison.comparisonStatus] || 0) + 1;
        return counts;
      }, {});
      return {
        payloadVersion: PAYLOAD.payload_version,
        registryVersion: PAYLOAD.registry_version,
        strictSchemaReady: PAYLOAD.strict_schema_ready === true,
        appCount: PAYLOAD.apps.length,
        comparisonStatusCounts: statusCounts,
        comparisons
      };
    }

    const api = Object.freeze({
      PAYLOAD_VERSION: PAYLOAD.payload_version,
      REGISTRY_VERSION: PAYLOAD.registry_version,
      strictSchemaReady: PAYLOAD.strict_schema_ready === true,
      getSummary,
      listAppContracts,
      getAppContract,
      getRuntimeDiagnosisContracts,
      getRuntimeDiagnosisContract,
      listRuntimeDiagnosisContracts,
      compareAppToRuntime,
      compareAllApps,
      buildLabComparisonSnapshot
    });

    global.McelRequirementsRegistry = api;

    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
