(() => {
  (function createMcelRequirementsRegistry(global) {
    if (!global) return;

    const PAYLOAD = Object.freeze({
  "payload_version": "mcel-requirements-lab-payload-v1",
  "registry_version": "mcel-requirements-registry-v1",
  "strict_schema_ready": true,
  "valid": true,
  "source": "pretty_docs/*.md",
  "truth_gate": "requirements describe the contract; adapters and tests prove implementation",
  "summary": {
    "registry_version": "mcel-requirements-registry-v1",
    "repo_root": "/mnt/data/work_patch39/main_computer_test",
    "pretty_docs_root": "pretty_docs",
    "valid": true,
    "strict_schema_ready": true,
    "total_blocks": 304,
    "block_type_counts": {
      "mcel-acceptance": 18,
      "mcel-app": 6,
      "mcel-finding": 18,
      "mcel-form-primitive": 40,
      "mcel-grammar": 18,
      "mcel-intent": 58,
      "mcel-layout-pattern": 1,
      "mcel-region": 50,
      "mcel-requirement": 55,
      "mcel-runtime-check": 21,
      "mcel-source-binding": 2,
      "mcel-test-binding": 2,
      "mcel-use-case": 15
    },
    "app_counts": {
      "calculator": 48,
      "code-editor": 44,
      "file-explorer": 44,
      "git-tools": 51,
      "mcel-lab": 38,
      "website-builder": 54
    },
    "app_contracts": [
      "calculator",
      "code-editor",
      "file-explorer",
      "git-tools",
      "mcel-lab",
      "website-builder"
    ],
    "error_count": 0,
    "warning_count": 0
  },
  "apps": [
    {
      "app": "calculator",
      "id": "calculator",
      "title": "Calculator",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "CalculationSession",
      "primary_user_goal": "Enter arithmetic expressions, inspect results, draw graphs, run explicit symbolic evaluations, and ask contextual questions without hidden filesystem, remote-sync, or command-execution side effects.",
      "contract_complete": true,
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 11,
        "mcel-region": 11,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 1
      },
      "status_counts": {
        "draft": 1,
        "open": 2,
        "planned": 2,
        "specified": 42,
        "verified": 1
      },
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 10
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 11,
        "target_adapter_status:executable": 11
      },
      "use_cases": [
        {
          "id": "calculator.use-case.compare-monthly-costs",
          "status": "draft",
          "goal": "Compare two monthly pricing formulas, identify the break-even point, inspect sample values, plot the relationship, and explain the result without leaving Calculator."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "calculator.form.subject.calculation-session",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The active calculation scenario, including expressions, graph inputs, symbolic requests, result history, and explanation context.",
          "relationships": [
            "Arithmetic expressions, graph inputs, symbolic requests, and result explanations belong to the same calculation session subject.",
            "Deterministic numeric result evidence remains canonical for computed answers.",
            "Model explanations and symbolic evaluations are derived evidence, not silent replacements for computed results."
          ],
          "constraints": [
            "Calculation identity must remain traceable across evaluate, graph, ask, and symbolic helper actions.",
            "Helper evidence must not mutate the canonical expression or result without an explicit user action.",
            "No calculation subject may imply filesystem, repository, package, or shell mutation."
          ]
        },
        {
          "id": "calculator.form.action.evaluate-and-explain",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user asks Calculator to evaluate expressions, draw graphs, request symbolic results, or explain deterministic output.",
          "relationships": [
            "Evaluation derives result evidence from the active calculation session.",
            "Graphing derives visual evidence from expression and range state.",
            "Explanation actions must cite or preserve the deterministic result they explain."
          ],
          "constraints": [
            "Evaluation and graphing stay local and deterministic.",
            "Symbolic/model helpers run only through explicit helper actions.",
            "Failed parsing or evaluation must produce visible feedback instead of mutating unrelated state."
          ]
        },
        {
          "id": "calculator.form.work-surface.deterministic-compute",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface where expression input, numeric result evidence, graph output, and helper results remain tied to the active calculation session.",
          "relationships": [
            "Enables expression evaluation, graph inspection, sample comparison, symbolic helper use, and result explanation.",
            "Keeps computed result evidence authoritative over helper prose.",
            "Presents derived graph or helper evidence as part of the same calculation task."
          ],
          "constraints": [
            "The primary compute surface must remain visible and usable while Calculator is active.",
            "Derived helper output must not claim authority over deterministic result evidence.",
            "Transient helper activity must not obscure the calculation path beyond its explicit operation."
          ]
        },
        {
          "id": "calculator.form.context.result-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains formulas, ranges, history, parse state, graph evidence, and helper outputs for the active calculation session.",
          "relationships": [
            "Explains why a result, graph, symbolic response, or model explanation belongs to the current calculation.",
            "Connects validation failures to the input or helper action that produced them.",
            "Helps users compare values without changing the calculation subject."
          ],
          "constraints": [
            "Context must remain subordinate to deterministic result evidence.",
            "Parse and validation context must identify the affected input or operation.",
            "Explanation context must not hide whether the result came from local evaluation, symbolic evaluation, or model help."
          ]
        },
        {
          "id": "calculator.form.feedback.validation-and-compute-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Ambient and noticeable feedback about parse validity, compute success, graph readiness, helper status, and contract health.",
          "relationships": [
            "Observes evaluation state, validation failures, helper activity, and runtime integrity.",
            "Supports user, developer, and automation audiences without changing the calculation session.",
            "Can be summarized compactly or expanded into findings when investigation is needed."
          ],
          "constraints": [
            "Feedback must not interrupt ordinary calculation unless an operation fails or becomes unsafe.",
            "Feedback must not cover or replace the primary compute surface.",
            "Feedback must distinguish current active issues from historical or resolved issues."
          ]
        },
        {
          "id": "calculator.form.transient.explicit-helper-evaluation",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary helper activity for symbolic evaluation, model explanation, graph redraw, or validation recovery.",
          "relationships": [
            "Supports explicit helper actions without becoming the calculation session itself.",
            "May produce derived evidence, receipts, warnings, or recovery instructions.",
            "Ends when the helper action resolves, is dismissed, or is superseded by a new calculation action."
          ],
          "constraints": [
            "Helper transients require user initiation or a visible lifecycle trigger.",
            "Helper transients must preserve the active calculation subject and deterministic result evidence.",
            "Helper transients must not perform hidden filesystem, repository, network-publish, package, or shell operations."
          ]
        }
      ],
      "region_count": 11,
      "intent_count": 11,
      "mutation_intent_count": 1,
      "prohibited_intent_count": 0,
      "open_finding_count": 2,
      "planned_or_open_count": 47,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "calculator.runtime-check.default-primary-workspace",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "calculator.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "calculator.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "calculator.region.mode-toolbar",
          "status": "specified",
          "region": "mode-switcher-toolbar",
          "role": "mode-switcher",
          "responsibility": "Own mode selection between arithmetic and scientific/graphing surfaces without evaluating expressions or hiding the user's current calculation context."
        },
        {
          "id": "calculator.region.arithmetic-panel",
          "status": "specified",
          "region": "primary-calculation-surface",
          "role": "primary-work-surface",
          "responsibility": "Own the ordinary arithmetic workflow by keeping expression input, local actions, and deterministic result evidence visually connected."
        },
        {
          "id": "calculator.region.expression-display",
          "status": "specified",
          "region": "expression-input-display",
          "role": "input-display",
          "responsibility": "Show the current arithmetic expression as authoritative calculator input, separate from graph output, Mathics prompts, and model prose."
        },
        {
          "id": "calculator.region.keypad",
          "status": "specified",
          "region": "deterministic-input-grid",
          "role": "action-grid",
          "responsibility": "Provide local digit, operator, edit, and equals actions that mutate only the current arithmetic expression and deterministic result state."
        },
        {
          "id": "calculator.region.result-status",
          "status": "specified",
          "region": "result-evidence-status",
          "role": "evidence-status",
          "responsibility": "Show success, error, graph, and symbolic evaluation status near the calculator surface that produced the evidence."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-calculator-requirements.md",
        "start_line": 75,
        "end_line": 104
      }
    },
    {
      "app": "code-editor",
      "id": "code-editor",
      "title": "Code Editor / MCEL Code Studio",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "fullApplicationSemanticReady",
      "dominant_object": "SourceWorkspace",
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "contract_complete": true,
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
      "status_counts": {
        "open": 3,
        "planned": 6,
        "specified": 35
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 2,
        "local-state": 1,
        "read-only": 3
      },
      "adapter_status_counts": {},
      "use_cases": [
        {
          "id": "code-editor.use-case.review-apply-ai-source-change",
          "status": "planned",
          "goal": "Prepare an AI-assisted source change, inspect the proposed diff and affected files, apply only approved edits, and preserve author control over every source mutation."
        },
        {
          "id": "code-editor.use-case.edit-save-source-file",
          "status": "planned",
          "goal": "Select an author-owned project file, edit it safely, save it explicitly, and preserve visible evidence about the path, dirty state, and saved result."
        }
      ],
      "form_primitive_count": 7,
      "form_primitives": [
        {
          "id": "code-editor.form.subject.source-workspace",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The project/workspace source tree and selected source file that the app helps inspect, edit, and safely change.",
          "relationships": [
            "Selected file is part of the source workspace.",
            "Source text, diagnostics, SCM evidence, and Aider context derive from the selected workspace subject.",
            "Generated runtime or proof artifacts are derived evidence, not canonical source."
          ],
          "constraints": [
            "Author-owned source remains canonical.",
            "Runtime chrome and generated helper surfaces must not become saved source.",
            "Selection identity must remain visible enough to anchor editing and review."
          ]
        },
        {
          "id": "code-editor.form.action.edit-source",
          "status": "specified",
          "primitive": "action",
          "meaning": "Inspect and change selected source text while preserving explicit save, patch, execution, and remote-mutation boundaries.",
          "relationships": [
            "Acts on code-editor.form.subject.source-workspace.",
            "Uses code-editor.form.work-surface.selected-source-editor as the authoritative work surface.",
            "May consume supporting context, evidence, and feedback without allowing those projections to mutate source implicitly."
          ],
          "constraints": [
            "Preview, suggestion, diagnosis, and review are not writes.",
            "Save/apply/execute/remote mutation require explicit intents and receipts.",
            "Read-only Aider requests cannot mutate files."
          ]
        },
        {
          "id": "code-editor.form.work-surface.selected-source-editor",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The authoritative stable surface where the selected file's source text is edited.",
          "relationships": [
            "Enables code-editor.form.action.edit-source.",
            "Represents the selected file from code-editor.form.subject.source-workspace.",
            "May be implemented by Monaco or a mode-gated fallback, but exactly one editor surface may hold primary authority."
          ],
          "constraints": [
            "Must remain visible and usable in authoring mode.",
            "Must not be covered, replaced, or out-ranked by supporting context, feedback, proof, preview, or diagnostic projections.",
            "Must preserve selected-path and dirty-state evidence."
          ]
        },
        {
          "id": "code-editor.form.context.project-selection",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that lets the user choose, understand, and compare source workspace subjects.",
          "relationships": [
            "Selects or explains the active source workspace/file subject.",
            "Supports editing, review, SCM evidence, and Aider context gathering.",
            "May project through any selection affordance that preserves subject identity and editing flow."
          ],
          "constraints": [
            "Must not claim primary editor authority.",
            "Must not obscure the selected source editor below usable geometry.",
            "Must keep the current selected subject traceable when file-backed editing is active."
          ]
        },
        {
          "id": "code-editor.form.context.reasoning-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting explanation, evidence, diagnostics, ownership hints, documentation references, and Aider context that help reason about the selected source subject or proposed action.",
          "relationships": [
            "Observes or explains source text, diagnostics, requirements, SCM evidence, Aider plans, and test/source ownership.",
            "May be available on demand, adjacent, tabbed, collapsed, or deferred by layout inference.",
            "Shares viewport with the primary work surface only when it preserves primary authority and geometry."
          ],
          "constraints": [
            "Must not become the selected-file editor.",
            "Must not leak as an unowned overlay over the primary work surface.",
            "Must remain distinguishable from canonical source and from write/apply controls."
          ]
        },
        {
          "id": "code-editor.form.feedback.integrity-and-activity",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Signals about app integrity, contract health, dirty/save state, policy gates, activity, failures, receipts, and recovery posture.",
          "relationships": [
            "Observes the source workspace, editor usability, runtime contract, action lifecycle, and persistence state.",
            "May render as status text, badges, counters, inline findings, panels, or machine-readable reports.",
            "Supports users, developers, and automation without defining a physical slot."
          ],
          "constraints": [
            "Ambient feedback must not interrupt or cover the primary work surface.",
            "Noticeable or corrective feedback must identify the condition it observes.",
            "Feedback projections must be owned so they are not reported as random overlays."
          ]
        },
        {
          "id": "code-editor.form.transient.widget-structure-editing",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary structure-editing UI used only while an explicit widget or layout editing mode is active.",
          "relationships": [
            "Supports structural editing operations rather than ordinary source editing.",
            "May cover or annotate the app only while its explicit mode is active.",
            "Is shell/tool infrastructure when inert and a transient projection when active."
          ],
          "constraints": [
            "Active widget editor panes, selections, and dock previews are forbidden in normal authoring mode.",
            "The inert widget-editor root is not itself a visible work surface.",
            "Transient structure-editing UI must identify its mode and owner when visible."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "open_finding_count": 3,
      "planned_or_open_count": 44,
      "runtime_check_count": 5,
      "runtime_checks": [
        {
          "id": "code-editor.runtime-check.authoring-primary-monaco",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-required-regions",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "secondary-surface-policy",
          "severity": "warning"
        },
        {
          "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "forbidden-surfaces-hidden",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-lifecycle",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "lifecycle-contract-preserved",
          "severity": "critical"
        }
      ],
      "first_regions": [
        {
          "id": "code-editor.region.identity",
          "status": "specified",
          "region": "identity",
          "role": "identity-header",
          "responsibility": "Identify the active workspace, route, active file, dirty state, runtime version, gate status, and persistence state."
        },
        {
          "id": "code-editor.region.navigation",
          "status": "specified",
          "region": "navigation",
          "role": "project-navigation",
          "responsibility": "Let the user choose files, project context, open editors, and selected-file sets without applying patches or executing commands."
        },
        {
          "id": "code-editor.region.primary",
          "status": "specified",
          "region": "primary",
          "role": "primary-authoring-surface",
          "responsibility": "Own the selected-file editor, draft review, concrete diffs, and explicit preview modes while preventing supporting tools from becoming the source of truth."
        },
        {
          "id": "code-editor.region.inspector",
          "status": "specified",
          "region": "supporting-reasoning-evidence-projection",
          "role": "secondary-context-and-feedback-surface",
          "responsibility": "Project optional reasoning, evidence, diagnostics, Aider context, SCM manifests, source ownership, test ownership, documentation references, and action-specific preflight information without becoming the primary editor. A desktop renderer may currently place this projection beside the editor, but MCEL treats that placement as layout inference rather than the requirement."
        },
        {
          "id": "code-editor.region.evidence",
          "status": "specified",
          "region": "evidence",
          "role": "evidence-and-receipts-panel",
          "responsibility": "Show Aider output, SCM evidence, contract reports, regression results, receipts, and recovery guidance for reviewed actions."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-code-editor-requirements.md",
        "start_line": 18,
        "end_line": 45
      }
    },
    {
      "app": "file-explorer",
      "id": "file-explorer",
      "title": "File Explorer",
      "status": "specified",
      "current_runtime_status": "full-bounded-read-only-semantic-runtime",
      "target_runtime_status": "full-read-only-semantic-runtime",
      "dominant_object": "FileEntry",
      "primary_user_goal": "Browse trusted roots, inspect directory contents, search within a bounded scope, preview readable files, classify entries, and identify the appropriate owning app without hidden filesystem, Git, remote, command, or automatic cross-app side effects.",
      "contract_complete": true,
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
      "status_counts": {
        "draft": 2,
        "open": 3,
        "planned": 3,
        "prohibited": 3,
        "specified": 33
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-state": 1,
        "prohibited": 1,
        "read-only": 7
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 7,
        "current_adapter_status:preflight-only": 1,
        "current_adapter_status:prohibited": 3,
        "target_adapter_status:executable": 7,
        "target_adapter_status:preflight-only": 1,
        "target_adapter_status:prohibited": 3
      },
      "use_cases": [
        {
          "id": "file-explorer.use-case.inspect-project-file-safely",
          "status": "draft",
          "goal": "Browse the current workspace, search for a known file, inspect its metadata and preview content, and decide which Main Computer app should handle it without mutating the filesystem or repository."
        },
        {
          "id": "file-explorer.use-case.browse-mounted-windows-drive",
          "status": "draft",
          "goal": "Browse a configured mounted Windows drive through File Explorer while preserving root boundaries, display-path evidence, and read-only behavior."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "file-explorer.form.subject.browse-scope",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected trusted root, current path, directory entry set, selected entry, previewable content, and mounted-root evidence.",
          "relationships": [
            "The selected entry belongs to the selected root and current path scope.",
            "Preview content, metadata, category, and suggested app derive from the selected entry.",
            "Mounted-root evidence explains when a displayed path is backed by a host path mapping."
          ],
          "constraints": [
            "Root and path boundaries remain explicit for every selected entry.",
            "Relative traversal cannot escape the selected browse scope.",
            "Read-only browsing must not imply delete, move, rename, write, Git, upload, download, or shell authority."
          ]
        },
        {
          "id": "file-explorer.form.action.inspect-entry-safely",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user selects roots, searches within scope, chooses entries, previews readable content, and decides handoff without mutating files.",
          "relationships": [
            "Search and selection operate within the active browse scope.",
            "Preview derives evidence from the selected entry and documented preview limits.",
            "Handoff suggestions connect entry category to another Main Computer app."
          ],
          "constraints": [
            "Inspection actions are read-only.",
            "Preview failures must report the reason instead of attempting mutation.",
            "Handoff suggestions must not open, write, stage, publish, or execute without a separate explicit app action."
          ]
        },
        {
          "id": "file-explorer.form.work-surface.entry-inspection",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for browsing entries, selecting a file or folder subject, inspecting metadata, and viewing safe preview evidence.",
          "relationships": [
            "Enables root selection, scoped search, directory entry inspection, metadata preview, content preview, and app-handoff reasoning.",
            "Keeps selected entry identity connected to preview and classification evidence.",
            "Preserves read-only status as part of the inspection task."
          ],
          "constraints": [
            "The primary inspection surface must remain visible and usable while browsing.",
            "Preview evidence must stay tied to the selected entry.",
            "Read-only status must remain visible enough to prevent accidental mutation assumptions."
          ]
        },
        {
          "id": "file-explorer.form.context.selection-and-classification",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains current root, path, selected entry, metadata, category, suggested app, and preview availability.",
          "relationships": [
            "Explains why an entry is classified as code, text, spreadsheet, game, asset, binary, oversized, or other.",
            "Connects preview availability to size, type, readability, and safety limits.",
            "Connects selected entries to possible downstream app handoff."
          ],
          "constraints": [
            "Context must not claim file mutation authority.",
            "Category and suggested-app evidence must be distinguishable from the file contents themselves.",
            "Missing or unreadable preview must produce explicit evidence, not blank ambiguity."
          ]
        },
        {
          "id": "file-explorer.form.feedback.boundary-and-preview-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about selected scope, read-only status, search state, preview readiness, preview failure, mounted-root status, and contract health.",
          "relationships": [
            "Observes browse scope, selected entry, preview limits, search progress, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automated contract checking.",
            "Distinguishes active browse problems from historical or resolved findings."
          ],
          "constraints": [
            "Feedback must not interrupt ordinary browsing unless a boundary, preview, or safety rule is violated.",
            "Feedback must not cover or replace the primary inspection surface.",
            "Feedback must identify the affected root, path, entry, or operation when possible."
          ]
        },
        {
          "id": "file-explorer.form.transient.search-and-selection-evidence",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary evidence created by search, selection change, preview loading, classification refresh, or handoff consideration.",
          "relationships": [
            "Supports the active inspect-entry action without becoming persistent file state.",
            "May highlight a selection, search result, classification change, or preview-loading lifecycle.",
            "Ends when the selection, query, preview, or handoff consideration changes."
          ],
          "constraints": [
            "Transient evidence must remain bounded to the active browse scope.",
            "Transient evidence must not imply mutation or permission escalation.",
            "Transient evidence must not obscure root, path, selected-entry, or read-only identity."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 11,
      "mutation_intent_count": 3,
      "prohibited_intent_count": 3,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "file-explorer.runtime-check.default-primary-surface",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "file-explorer.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "file-explorer.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "file-explorer.layout.identity",
          "status": "specified",
          "region": "roots-panel-header",
          "role": "identity",
          "responsibility": "Identify the app as File Explorer, describe read-only system browsing, and expose the global status line."
        },
        {
          "id": "file-explorer.layout.roots",
          "status": "specified",
          "region": "roots-sidebar",
          "role": "navigation",
          "responsibility": "Show selectable trusted roots such as workspace, debug-root, cwd, home, workspace-parent, filesystem-root, drive roots, or configured mounted Windows roots."
        },
        {
          "id": "file-explorer.layout.path-toolbar",
          "status": "specified",
          "region": "path-and-search-toolbar",
          "role": "navigation",
          "responsibility": "Show the current root-relative browsing scope and provide bounded search/up navigation within that scope."
        },
        {
          "id": "file-explorer.layout.directory-list",
          "status": "specified",
          "region": "directory-listing",
          "role": "primary-work-surface",
          "responsibility": "Present the current directory or search result set as the primary selectable collection, with directories before files and enough metadata to choose a preview or handoff target."
        },
        {
          "id": "file-explorer.layout.preview",
          "status": "specified",
          "region": "preview-panel",
          "role": "inspector",
          "responsibility": "Show selected entry metadata, preview content when safe, preview-denied reasons when unsafe, category evidence, and suggested app evidence."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-file-explorer-requirements.md",
        "start_line": 121,
        "end_line": 148
      }
    },
    {
      "app": "git-tools",
      "id": "git-tools",
      "title": "Git Tools",
      "status": "specified",
      "current_runtime_status": "runtime-baseline-with-ignore-preview-semantic-adapter",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "RepositoryProject",
      "primary_user_goal": "Inspect repository state, triage files, create safe commits, and publish selected project work through governed Git/Gitea actions without exposing raw Git plumbing as the default user path.",
      "contract_complete": true,
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
      "status_counts": {
        "implemented": 6,
        "open": 4,
        "partially-implemented": 4,
        "planned": 8,
        "prohibited": 1,
        "specified": 28
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-repository-mutation": 1,
        "read-only": 6,
        "remote-mutation": 2
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 7,
        "current_adapter_status:preflight-only": 2,
        "current_adapter_status:prohibited": 1
      },
      "use_cases": [
        {
          "id": "git-tools.use-case.push-current-branch-local-gitea",
          "status": "partially-implemented",
          "goal": "Inspect repository, branch, and remote evidence, confirm the intended local Gitea target, push the current branch explicitly, and receive success or recovery evidence."
        },
        {
          "id": "git-tools.use-case.add-ignore-rule",
          "status": "planned",
          "goal": "Select an untracked file or directory, preview the proposed .gitignore rule, understand whether the target is already tracked, apply the ignore change, and refresh repository evidence."
        },
        {
          "id": "git-tools.use-case.switch-branch-safely",
          "status": "planned",
          "goal": "Inspect the current branch, available branch targets, and dirty working-tree state, then switch branches only when local work is safe or explicitly handled."
        },
        {
          "id": "git-tools.use-case.select-files-stage-commit",
          "status": "planned",
          "goal": "Inspect changed files, preview diffs, select the files that belong together, stage only those files, write a commit message, create the commit, and keep unselected changes untouched."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "git-tools.form.subject.repository-project",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected repository project, branch, remote, working-tree evidence, file basket, patch inventory, ignore rules, secrets filters, and publish target.",
          "relationships": [
            "Branch, remote, status, diff, staged intent, publish target, and receipts belong to the selected repository project.",
            "Patch inventory and file basket evidence derive from repository state but must remain distinguishable from executed Git actions.",
            "Publishing evidence connects repository state to an explicit governed target."
          ],
          "constraints": [
            "Repository identity, branch, and remote target must remain traceable before any mutation.",
            "Local evidence, remote evidence, and planned actions must not be conflated.",
            "Raw Git details may support evidence but must not become hidden default authority."
          ]
        },
        {
          "id": "git-tools.form.action.governed-repository-change",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user inspects repository state, selects files, stages intent, commits, edits ignore/filter rules, or publishes through governed preflight and receipt flow.",
          "relationships": [
            "Read actions gather status, branch, remote, diff, patch, and file evidence.",
            "Mutation actions require preflight, explicit confirmation, execution boundary, and receipt.",
            "Recovery actions derive from failed preflight, failed execution, or stale repository evidence."
          ],
          "constraints": [
            "Commit and push remain separate actions.",
            "Mutation actions require explicit target evidence and confirmation.",
            "Failed actions must produce recovery evidence without pretending repository or remote state changed."
          ]
        },
        {
          "id": "git-tools.form.work-surface.repository-workflow",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for repository triage, status review, file selection, commit preparation, governed publish actions, and recovery.",
          "relationships": [
            "Enables repository selection, status refresh, file-basket review, patch inventory review, commit preparation, ignore/filter editing, publish preflight, and recovery.",
            "Keeps evidence, intended mutation, confirmation, execution, and receipt connected.",
            "Presents advanced Git details as supporting evidence rather than default authority."
          ],
          "constraints": [
            "The primary repository workflow surface must remain visible and usable.",
            "Mutation controls must remain tied to current repository, branch, target, and preflight evidence.",
            "Evidence views must not silently execute Git commands."
          ]
        },
        {
          "id": "git-tools.form.context.evidence-and-preflight",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains branch, remote, status, diff, staged intent, ignore/filter effects, publish target, command preview, receipts, and recovery paths.",
          "relationships": [
            "Explains what evidence supports a commit, ignore change, filter change, push, or publish operation.",
            "Connects stale, missing, or conflicting evidence to preflight failures.",
            "Connects receipts and recovery suggestions to the operation that produced them."
          ],
          "constraints": [
            "Context must not hide the distinction between proposed and executed changes.",
            "Command preview must remain evidence until the user confirms execution.",
            "Receipts must name the affected repository, branch, remote, or target when available."
          ]
        },
        {
          "id": "git-tools.form.feedback.risk-and-operation-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about repository freshness, dirty state, staged intent, preflight readiness, confirmation requirement, execution result, recovery state, and contract health.",
          "relationships": [
            "Observes repository evidence, action risk, preflight state, execution state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing repository state.",
            "Distinguishes active blockers from resolved or historical findings."
          ],
          "constraints": [
            "Feedback must not make a mutation appear successful without a matching receipt.",
            "Feedback must not cover or replace the primary repository workflow surface.",
            "High-risk or failed operations may demand attention but must remain tied to recovery evidence."
          ]
        },
        {
          "id": "git-tools.form.transient.confirmation-and-recovery",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary confirmation, preflight, execution-progress, command-preview, receipt, and recovery evidence around governed Git and publishing actions.",
          "relationships": [
            "Supports explicit mutation or recovery actions without becoming repository state itself.",
            "May demand attention when action risk, missing evidence, conflict, or failure requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches repository subject."
          ],
          "constraints": [
            "Transient mutation UI requires a clear trigger and action target.",
            "Transient evidence must preserve repository, branch, remote, and target identity.",
            "Transient recovery must not perform a follow-up mutation without another explicit action."
          ]
        }
      ],
      "region_count": 8,
      "intent_count": 10,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 1,
      "open_finding_count": 4,
      "planned_or_open_count": 40,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "git-tools.runtime-check.default-primary-workflow",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "git-tools.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "git-tools.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "git-tools.region.identity",
          "status": "specified",
          "region": "identity",
          "role": "repository-identity-header",
          "responsibility": "Identify the selected project, repository root, branch, remote target, backend freshness, and semantic runtime scope."
        },
        {
          "id": "git-tools.region.navigation",
          "status": "specified",
          "region": "navigation",
          "role": "repository-navigation",
          "responsibility": "Let the user choose projects, workflow tabs, file baskets, patch inventory views, and support areas without mutating Git state."
        },
        {
          "id": "git-tools.region.primary",
          "status": "specified",
          "region": "primary",
          "role": "repository-workbench",
          "responsibility": "Own the selected repository workflow, changed-file triage, project publishing strip, status summary, and commit/publish content."
        },
        {
          "id": "git-tools.region.inspector",
          "status": "specified",
          "region": "inspector",
          "role": "preflight-inspector",
          "responsibility": "Show remote configuration, selected-file evidence, ignore-rule previews, policy gates, and action-specific confirmation details."
        },
        {
          "id": "git-tools.region.evidence",
          "status": "specified",
          "region": "evidence",
          "role": "evidence-and-recovery-panel",
          "responsibility": "Show status API output, semantic adapter evidence, intent coverage, receipts, backend errors, and recovery plans."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-git-tools-requirements.md",
        "start_line": 18,
        "end_line": 76
      }
    },
    {
      "app": "mcel-lab",
      "id": "mcel-lab",
      "title": "MCEL Lab Blueprint Studio",
      "status": "specified",
      "current_runtime_status": "scope-limited-semantic-runtime",
      "target_runtime_status": "scope-limited-semantic-runtime",
      "dominant_object": "AppBlueprint",
      "primary_user_goal": "Select an app blueprint, inspect its semantic form and implementation evidence, annotate rendered elements, validate findings, and export repair context without directly rewriting live implementation files.",
      "contract_complete": true,
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
      "status_counts": {
        "implemented": 4,
        "partially-implemented": 3,
        "specified": 31
      },
      "intent_risk_counts": {
        "local-state": 4,
        "read-only": 3
      },
      "adapter_status_counts": {},
      "use_cases": [
        {
          "id": "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
          "status": "specified",
          "goal": "Select an app, inspect its semantic form primitives, compare the declared contract with implementation evidence, and identify gaps before changing code."
        },
        {
          "id": "mcel-lab.use-case.self-host-refactor-context",
          "status": "specified",
          "goal": "Inspect MCEL Lab itself, annotate rendered elements, distinguish user intent from verified facts, and export reviewable repair context without directly rewriting the live Lab implementation."
        }
      ],
      "form_primitive_count": 9,
      "form_primitives": [
        {
          "id": "mcel-lab.form.subject.app-blueprint",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected app contract being inspected, validated, annotated, or prepared for repair.",
          "relationships": [
            "Owns app identity, object model, workflows, layout bindings, action policy, evidence, source/test bindings, annotations, findings, and repair plans.",
            "May represent MCEL Lab itself as a self-hosting target.",
            "Is loaded from documentation, blueprint core data, annotations, and runtime evidence."
          ],
          "constraints": [
            "AppBlueprint remains the dominant object even when a mounted app preview is visible.",
            "Prose, hardcoded JS blueprints, annotations, and runtime evidence must be distinguishable as separate evidence sources.",
            "Self-hosting inspection must not imply permission to rewrite the live Lab implementation."
          ]
        },
        {
          "id": "mcel-lab.form.action.inspect-blueprint",
          "status": "specified",
          "primitive": "action",
          "meaning": "Select an app and aspect, inspect the semantic contract and compare it with implementation evidence.",
          "relationships": [
            "Acts on mcel-lab.form.subject.app-blueprint.",
            "Uses the blueprint inspection work surface as the authoritative workspace.",
            "Consumes supporting implementation evidence, selected-element evidence, validation feedback, and annotations."
          ],
          "constraints": [
            "Inspection is read-oriented until the user explicitly creates or edits an annotation draft.",
            "Aspect navigation must not replace the selected AppBlueprint as the dominant object.",
            "Findings must distinguish documented intent from verified runtime facts."
          ]
        },
        {
          "id": "mcel-lab.form.work-surface.blueprint-inspection",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The stable surface where the selected AppBlueprint aspect, mounted preview, selected evidence, and repair context are inspected.",
          "relationships": [
            "Enables mcel-lab.form.action.inspect-blueprint.",
            "Represents the selected AppBlueprint and current aspect.",
            "Hosts mounted app preview evidence without granting that preview primary Lab authority."
          ],
          "constraints": [
            "Must remain visible and usable when MCEL Lab is active.",
            "Must keep selected app, selected aspect, and mounted route evidence traceable.",
            "Must not be covered or out-ranked by unowned feedback, transient overlays, or debug/proof internals."
          ]
        },
        {
          "id": "mcel-lab.form.context.app-and-aspect-selection",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that chooses which AppBlueprint and which aspect are being inspected.",
          "relationships": [
            "Selects the active subject for the blueprint inspection work surface.",
            "Filters the visible evidence, annotations, findings, and repair context.",
            "May render as controls, lists, command choices, tabs, or another inferred projection."
          ],
          "constraints": [
            "Must keep the selected app and aspect recoverable from visible UI or machine-readable state.",
            "Must not claim primary work-surface authority.",
            "Must not make physical placement part of the semantic contract."
          ]
        },
        {
          "id": "mcel-lab.form.context.implementation-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting evidence about DOM elements, source files, CSS ownership, tests, annotations, validation findings, and repair candidates.",
          "relationships": [
            "Explains the selected AppBlueprint, selected aspect, and selected rendered element.",
            "May be gathered from mounted previews, point inspection, annotation maps, source bindings, test bindings, and registry payloads.",
            "Supports repair planning without becoming a direct patch applicator."
          ],
          "constraints": [
            "Evidence must identify its source and freshness when it is used to justify a finding.",
            "Implementation evidence must not be confused with the target requirement itself.",
            "Derived repair context must remain reviewable before patch generation."
          ]
        },
        {
          "id": "mcel-lab.form.feedback.validation-and-mount-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Signals about selected app state, mount readiness, inspection mode, annotation save state, validation findings, export readiness, and repair-plan readiness.",
          "relationships": [
            "Observes app selection, aspect selection, mounted preview state, selected element state, annotation state, and validation results.",
            "May render as badges, receipts, inline findings, result summaries, or machine-readable packets.",
            "Serves users, developers, and automation without defining a physical slot."
          ],
          "constraints": [
            "Ambient feedback must not interrupt or obscure blueprint inspection.",
            "Corrective feedback must identify the condition it observes.",
            "Feedback projections must have an owner so they are not diagnosed as random overlays."
          ]
        },
        {
          "id": "mcel-lab.form.constraint.self-hosting-safety",
          "status": "specified",
          "primitive": "constraint",
          "meaning": "Safety law that lets MCEL Lab inspect and draft changes to its own blueprint without directly mutating its live implementation.",
          "relationships": [
            "Protects mcel-lab.form.subject.app-blueprint when selectedApp is mcel-lab.",
            "Applies to annotation edits, repair plans, export packets, and patch artifact generation.",
            "Separates draft intent from implementation mutation."
          ],
          "constraints": [
            "MCEL Lab may edit its own blueprint draft.",
            "MCEL Lab must not directly rewrite or apply its own live implementation.",
            "Self-hosting repair output must be reviewable as an artifact before any local patch workflow applies it."
          ]
        },
        {
          "id": "mcel-lab.form.transient.point-inspection",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary inspection UI used while the user is selecting a rendered element and capturing evidence.",
          "relationships": [
            "Supports element selection, bounding-box evidence, annotation drafting, and source/test ownership hints.",
            "Is active only while inspect mode is enabled or a selected element receipt is being reviewed.",
            "May annotate the mounted preview without mutating the mounted app."
          ],
          "constraints": [
            "Must be explicitly mode-bound and reversible.",
            "Must not fire the mounted app's ordinary actions while selecting an element.",
            "Must identify selected element evidence separately from user-authored annotation intent."
          ]
        },
        {
          "id": "mcel-lab.form.interruption.unsafe-repair-boundary",
          "status": "specified",
          "primitive": "interruption",
          "meaning": "Attention-demanding boundary used when a repair, removal, or self-hosting operation could be mistaken for a verified implementation fact or direct mutation.",
          "relationships": [
            "Protects patch planning, self-hosting edits, removal candidates, and destructive annotations.",
            "Can block export or require review when evidence is stale or unsafe.",
            "Explains recovery actions before any patch artifact is generated."
          ],
          "constraints": [
            "Must interrupt or block when the user attempts direct self-mutation.",
            "Must require evidence before deletion or rework candidates become patch guidance.",
            "Must separate possible fixes from verified facts."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "open_finding_count": 0,
      "planned_or_open_count": 31,
      "runtime_check_count": 4,
      "runtime_checks": [
        {
          "id": "mcel-lab.runtime.primary-blueprint-workspace",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "mcel-lab.runtime.required-semantic-projections",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "required-regions-visible",
          "severity": "error"
        },
        {
          "id": "mcel-lab.runtime.visual-integrity-baseline",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "visual-integrity-baseline",
          "severity": "critical"
        },
        {
          "id": "mcel-lab.runtime.self-hosting-safety-boundary",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "lifecycle-contract-preserved",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "mcel-lab.region.app-root",
          "status": "implemented",
          "region": "lab-app-root",
          "role": "app-boundary",
          "responsibility": "Owns the MCEL Lab application boundary and exposes the selected AppBlueprint as the dominant object."
        },
        {
          "id": "mcel-lab.region.selection-context",
          "status": "implemented",
          "region": "app-and-aspect-selection-context",
          "role": "supporting-context",
          "responsibility": "Projects app and aspect selection primitives without making their physical placement normative."
        },
        {
          "id": "mcel-lab.region.aspect-map",
          "status": "implemented",
          "region": "aspect-map-projection",
          "role": "navigation-context",
          "responsibility": "Exposes inspectable blueprint aspects and keeps the selected aspect traceable."
        },
        {
          "id": "mcel-lab.region.blueprint-workspace",
          "status": "implemented",
          "region": "blueprint-inspection-workspace",
          "role": "primary-work-surface",
          "responsibility": "Projects the selected AppBlueprint aspect and mounted preview evidence as the main inspection workspace."
        },
        {
          "id": "mcel-lab.region.mounted-preview",
          "status": "partially-implemented",
          "region": "mounted-app-preview-projection",
          "role": "implementation-evidence-context",
          "responsibility": "Shows a contained app preview as evidence while preserving AppBlueprint authority."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-lab-blueprint-studio.md",
        "start_line": 84,
        "end_line": 111
      }
    },
    {
      "app": "website-builder",
      "id": "website-builder",
      "title": "Website Builder and Websites",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "WebsiteProject",
      "primary_user_goal": "Edit saved websites, configure optional site runtime layers, preview and publish to explicit lanes, and hand repository changes to Git Tools without confusing author-owned source, generated runtime evidence, deployment targets, or remote sync.",
      "contract_complete": true,
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
      "status_counts": {
        "open": 4,
        "planned": 4,
        "specified": 46
      },
      "intent_risk_counts": {
        "local-file-mutation": 3,
        "local-state": 3,
        "read-only": 4,
        "remote-mutation": 2
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 12,
        "target_adapter_status:executable": 12
      },
      "use_cases": [
        {
          "id": "website-builder.use-case.edit-preview-saved-site",
          "status": "specified",
          "goal": "Select a saved website, edit its visible content or styling, preview the draft, save the site source, and verify that the saved site still has a coherent manifest, builder state, entry HTML, stylesheet, script, and page runtime."
        },
        {
          "id": "website-builder.use-case.configure-blog-runtime",
          "status": "specified",
          "goal": "Configure or inspect the blog-capable site runtime without confusing source pages, local database artifacts, Directus storage, generated API routes, or published website files."
        },
        {
          "id": "website-builder.use-case.publish-selected-lane",
          "status": "specified",
          "goal": "Publish a saved website to one explicit lane, verify the target URL, and keep local authoring, local server, dev deployment, and remote production separate."
        },
        {
          "id": "website-builder.use-case.git-tools-handoff",
          "status": "specified",
          "goal": "Turn saved website changes into reviewable repository evidence, then use Git Tools for file selection, commit, and governed push rather than hiding Git mutation inside Website Builder."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "website-builder.form.subject.website-project",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected saved website, page source, builder state, manifest, runtime configuration, generated evidence, publish target, and repository handoff state.",
          "relationships": [
            "Site manifest, builder state, source files, generated runtime evidence, and publish receipts belong to the selected website project.",
            "Author-owned source, local runtime data, generated files, deployment targets, and Git handoff evidence must remain distinguishable.",
            "Publish lane evidence derives from an explicit target and preflight state."
          ],
          "constraints": [
            "Selected website identity must remain traceable across edit, preview, save, configure, publish, and handoff actions.",
            "Generated runtime evidence must not be confused with author-owned source.",
            "Remote or deployment state must not be implied by local save or preview."
          ]
        },
        {
          "id": "website-builder.form.action.author-preview-publish",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user selects a website, edits content or style, previews draft output, saves source artifacts, configures runtime layers, publishes to an explicit lane, or hands work to Git Tools.",
          "relationships": [
            "Edit and save actions mutate only the selected website source artifacts.",
            "Preview actions derive evidence without publishing.",
            "Publish actions require target evidence, preflight, confirmation, execution, and receipt."
          ],
          "constraints": [
            "Save, preview, local publish, dev publish, remote publish, and Git handoff remain separate actions.",
            "Destructive runtime or storage choices require explicit acknowledgement.",
            "Failed preview, save, setup, publish, or handoff actions must preserve recovery evidence."
          ]
        },
        {
          "id": "website-builder.form.work-surface.site-authoring",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for selecting a website project, authoring source, inspecting preview evidence, configuring runtime state, and preparing publish or handoff actions.",
          "relationships": [
            "Enables site selection, content/style editing, source save, draft preview, runtime setup review, publish preflight, and Git Tools handoff.",
            "Keeps author-owned source, generated evidence, runtime setup, and publish state connected to the selected website project.",
            "Presents deployment evidence as a governed extension of the authoring workflow."
          ],
          "constraints": [
            "The primary authoring surface must remain visible and usable during editing and preview.",
            "Publish and runtime setup controls must remain tied to selected website and explicit target evidence.",
            "Generated evidence must not claim source authority."
          ]
        },
        {
          "id": "website-builder.form.context.runtime-and-publish-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains manifest state, builder state, source artifacts, generated runtime files, database/CMS layers, publish targets, receipts, and Git handoff evidence.",
          "relationships": [
            "Explains whether evidence came from source, generated runtime, local server, dev deployment, remote target, or repository handoff.",
            "Connects runtime setup dependencies to explicit choices and receipts.",
            "Connects publish results to the lane and target that produced them."
          ],
          "constraints": [
            "Context must keep author-owned source, generated files, runtime data, and deployed state distinguishable.",
            "Context must not hide destructive storage or remote deployment risk.",
            "Receipts must name the selected website and target lane when available."
          ]
        },
        {
          "id": "website-builder.form.feedback.save-preview-publish-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about dirty state, save result, preview readiness, runtime setup state, publish preflight, publish result, Git handoff readiness, and contract health.",
          "relationships": [
            "Observes selected website state, authoring activity, preview generation, setup progress, publish workflow, handoff state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing website source by itself.",
            "Distinguishes active issues from historical or resolved findings."
          ],
          "constraints": [
            "Feedback must not claim deployment success without a matching receipt.",
            "Feedback must not cover or replace the primary authoring surface.",
            "Feedback must identify the selected website, lane, runtime layer, or handoff target when possible."
          ]
        },
        {
          "id": "website-builder.form.transient.setup-publish-and-handoff",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary setup, generation, confirmation, execution-progress, receipt, and recovery evidence for runtime configuration, publish, and Git handoff operations.",
          "relationships": [
            "Supports explicit setup, publish, or handoff actions without becoming website source itself.",
            "May demand attention when storage, deployment, or repository risk requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches website subject."
          ],
          "constraints": [
            "Transient mutation UI requires a clear selected website and target.",
            "Transient evidence must preserve source/generated/runtime/deployment boundaries.",
            "Transient recovery must not perform follow-up mutation without another explicit action."
          ]
        }
      ],
      "region_count": 10,
      "intent_count": 12,
      "mutation_intent_count": 8,
      "prohibited_intent_count": 0,
      "open_finding_count": 4,
      "planned_or_open_count": 54,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "website-builder.runtime-check.default-primary-preview",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "website-builder.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "website-builder.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "website-builder.region.identity",
          "status": "specified",
          "region": "website-identity-header",
          "role": "identity-header",
          "responsibility": "Identify the selected website, current site metadata, dirty/save state, and source-vs-saved status across edit, preview, and publish workflows."
        },
        {
          "id": "website-builder.region.site-selector",
          "status": "specified",
          "region": "saved-site-navigation",
          "role": "navigation",
          "responsibility": "Let the user choose, create, search, and locate saved website projects without performing destructive site operations implicitly."
        },
        {
          "id": "website-builder.region.design-surface",
          "status": "specified",
          "region": "primary-design-surface",
          "role": "primary-work-surface",
          "responsibility": "Own the author-facing GrapesJS design canvas, page blocks, and draft page state during normal website editing."
        },
        {
          "id": "website-builder.region.preview-surface",
          "status": "specified",
          "region": "website-preview-surface",
          "role": "preview-surface",
          "responsibility": "Show draft, local, dev, or remote preview lanes and their availability without implying that preview equals publish success."
        },
        {
          "id": "website-builder.region.source-and-manifest",
          "status": "specified",
          "region": "source-manifest-evidence-panel",
          "role": "evidence-panel",
          "responsibility": "Expose site source, builder metadata, generated artifacts, runtime selection, and manifest evidence for the selected website."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-website-builder-requirements.md",
        "start_line": 162,
        "end_line": 196
      }
    }
  ],
  "app_contracts": {
    "calculator": {
      "app": "calculator",
      "id": "calculator",
      "title": "Calculator",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "CalculationSession",
      "primary_user_goal": "Enter arithmetic expressions, inspect results, draw graphs, run explicit symbolic evaluations, and ask contextual questions without hidden filesystem, remote-sync, or command-execution side effects.",
      "contract_complete": true,
      "block_type_counts": {
        "mcel-acceptance": 3,
        "mcel-app": 1,
        "mcel-finding": 3,
        "mcel-form-primitive": 6,
        "mcel-intent": 11,
        "mcel-region": 11,
        "mcel-requirement": 10,
        "mcel-runtime-check": 3,
        "mcel-use-case": 1
      },
      "status_counts": {
        "draft": 1,
        "open": 2,
        "planned": 2,
        "specified": 42,
        "verified": 1
      },
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 10
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 11,
        "target_adapter_status:executable": 11
      },
      "use_cases": [
        {
          "id": "calculator.use-case.compare-monthly-costs",
          "status": "draft",
          "goal": "Compare two monthly pricing formulas, identify the break-even point, inspect sample values, plot the relationship, and explain the result without leaving Calculator."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "calculator.form.subject.calculation-session",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The active calculation scenario, including expressions, graph inputs, symbolic requests, result history, and explanation context.",
          "relationships": [
            "Arithmetic expressions, graph inputs, symbolic requests, and result explanations belong to the same calculation session subject.",
            "Deterministic numeric result evidence remains canonical for computed answers.",
            "Model explanations and symbolic evaluations are derived evidence, not silent replacements for computed results."
          ],
          "constraints": [
            "Calculation identity must remain traceable across evaluate, graph, ask, and symbolic helper actions.",
            "Helper evidence must not mutate the canonical expression or result without an explicit user action.",
            "No calculation subject may imply filesystem, repository, package, or shell mutation."
          ]
        },
        {
          "id": "calculator.form.action.evaluate-and-explain",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user asks Calculator to evaluate expressions, draw graphs, request symbolic results, or explain deterministic output.",
          "relationships": [
            "Evaluation derives result evidence from the active calculation session.",
            "Graphing derives visual evidence from expression and range state.",
            "Explanation actions must cite or preserve the deterministic result they explain."
          ],
          "constraints": [
            "Evaluation and graphing stay local and deterministic.",
            "Symbolic/model helpers run only through explicit helper actions.",
            "Failed parsing or evaluation must produce visible feedback instead of mutating unrelated state."
          ]
        },
        {
          "id": "calculator.form.work-surface.deterministic-compute",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface where expression input, numeric result evidence, graph output, and helper results remain tied to the active calculation session.",
          "relationships": [
            "Enables expression evaluation, graph inspection, sample comparison, symbolic helper use, and result explanation.",
            "Keeps computed result evidence authoritative over helper prose.",
            "Presents derived graph or helper evidence as part of the same calculation task."
          ],
          "constraints": [
            "The primary compute surface must remain visible and usable while Calculator is active.",
            "Derived helper output must not claim authority over deterministic result evidence.",
            "Transient helper activity must not obscure the calculation path beyond its explicit operation."
          ]
        },
        {
          "id": "calculator.form.context.result-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains formulas, ranges, history, parse state, graph evidence, and helper outputs for the active calculation session.",
          "relationships": [
            "Explains why a result, graph, symbolic response, or model explanation belongs to the current calculation.",
            "Connects validation failures to the input or helper action that produced them.",
            "Helps users compare values without changing the calculation subject."
          ],
          "constraints": [
            "Context must remain subordinate to deterministic result evidence.",
            "Parse and validation context must identify the affected input or operation.",
            "Explanation context must not hide whether the result came from local evaluation, symbolic evaluation, or model help."
          ]
        },
        {
          "id": "calculator.form.feedback.validation-and-compute-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Ambient and noticeable feedback about parse validity, compute success, graph readiness, helper status, and contract health.",
          "relationships": [
            "Observes evaluation state, validation failures, helper activity, and runtime integrity.",
            "Supports user, developer, and automation audiences without changing the calculation session.",
            "Can be summarized compactly or expanded into findings when investigation is needed."
          ],
          "constraints": [
            "Feedback must not interrupt ordinary calculation unless an operation fails or becomes unsafe.",
            "Feedback must not cover or replace the primary compute surface.",
            "Feedback must distinguish current active issues from historical or resolved issues."
          ]
        },
        {
          "id": "calculator.form.transient.explicit-helper-evaluation",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary helper activity for symbolic evaluation, model explanation, graph redraw, or validation recovery.",
          "relationships": [
            "Supports explicit helper actions without becoming the calculation session itself.",
            "May produce derived evidence, receipts, warnings, or recovery instructions.",
            "Ends when the helper action resolves, is dismissed, or is superseded by a new calculation action."
          ],
          "constraints": [
            "Helper transients require user initiation or a visible lifecycle trigger.",
            "Helper transients must preserve the active calculation subject and deterministic result evidence.",
            "Helper transients must not perform hidden filesystem, repository, network-publish, package, or shell operations."
          ]
        }
      ],
      "region_count": 11,
      "intent_count": 11,
      "mutation_intent_count": 1,
      "prohibited_intent_count": 0,
      "open_finding_count": 2,
      "planned_or_open_count": 47,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "calculator.runtime-check.default-primary-workspace",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "calculator.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "calculator.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "calculator.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "calculator.region.mode-toolbar",
          "status": "specified",
          "region": "mode-switcher-toolbar",
          "role": "mode-switcher",
          "responsibility": "Own mode selection between arithmetic and scientific/graphing surfaces without evaluating expressions or hiding the user's current calculation context."
        },
        {
          "id": "calculator.region.arithmetic-panel",
          "status": "specified",
          "region": "primary-calculation-surface",
          "role": "primary-work-surface",
          "responsibility": "Own the ordinary arithmetic workflow by keeping expression input, local actions, and deterministic result evidence visually connected."
        },
        {
          "id": "calculator.region.expression-display",
          "status": "specified",
          "region": "expression-input-display",
          "role": "input-display",
          "responsibility": "Show the current arithmetic expression as authoritative calculator input, separate from graph output, Mathics prompts, and model prose."
        },
        {
          "id": "calculator.region.keypad",
          "status": "specified",
          "region": "deterministic-input-grid",
          "role": "action-grid",
          "responsibility": "Provide local digit, operator, edit, and equals actions that mutate only the current arithmetic expression and deterministic result state."
        },
        {
          "id": "calculator.region.result-status",
          "status": "specified",
          "region": "result-evidence-status",
          "role": "evidence-status",
          "responsibility": "Show success, error, graph, and symbolic evaluation status near the calculator surface that produced the evidence."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-calculator-requirements.md",
        "start_line": 75,
        "end_line": 104
      }
    },
    "code-editor": {
      "app": "code-editor",
      "id": "code-editor",
      "title": "Code Editor / MCEL Code Studio",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "fullApplicationSemanticReady",
      "dominant_object": "SourceWorkspace",
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "contract_complete": true,
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
      "status_counts": {
        "open": 3,
        "planned": 6,
        "specified": 35
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 2,
        "local-state": 1,
        "read-only": 3
      },
      "adapter_status_counts": {},
      "use_cases": [
        {
          "id": "code-editor.use-case.review-apply-ai-source-change",
          "status": "planned",
          "goal": "Prepare an AI-assisted source change, inspect the proposed diff and affected files, apply only approved edits, and preserve author control over every source mutation."
        },
        {
          "id": "code-editor.use-case.edit-save-source-file",
          "status": "planned",
          "goal": "Select an author-owned project file, edit it safely, save it explicitly, and preserve visible evidence about the path, dirty state, and saved result."
        }
      ],
      "form_primitive_count": 7,
      "form_primitives": [
        {
          "id": "code-editor.form.subject.source-workspace",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The project/workspace source tree and selected source file that the app helps inspect, edit, and safely change.",
          "relationships": [
            "Selected file is part of the source workspace.",
            "Source text, diagnostics, SCM evidence, and Aider context derive from the selected workspace subject.",
            "Generated runtime or proof artifacts are derived evidence, not canonical source."
          ],
          "constraints": [
            "Author-owned source remains canonical.",
            "Runtime chrome and generated helper surfaces must not become saved source.",
            "Selection identity must remain visible enough to anchor editing and review."
          ]
        },
        {
          "id": "code-editor.form.action.edit-source",
          "status": "specified",
          "primitive": "action",
          "meaning": "Inspect and change selected source text while preserving explicit save, patch, execution, and remote-mutation boundaries.",
          "relationships": [
            "Acts on code-editor.form.subject.source-workspace.",
            "Uses code-editor.form.work-surface.selected-source-editor as the authoritative work surface.",
            "May consume supporting context, evidence, and feedback without allowing those projections to mutate source implicitly."
          ],
          "constraints": [
            "Preview, suggestion, diagnosis, and review are not writes.",
            "Save/apply/execute/remote mutation require explicit intents and receipts.",
            "Read-only Aider requests cannot mutate files."
          ]
        },
        {
          "id": "code-editor.form.work-surface.selected-source-editor",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The authoritative stable surface where the selected file's source text is edited.",
          "relationships": [
            "Enables code-editor.form.action.edit-source.",
            "Represents the selected file from code-editor.form.subject.source-workspace.",
            "May be implemented by Monaco or a mode-gated fallback, but exactly one editor surface may hold primary authority."
          ],
          "constraints": [
            "Must remain visible and usable in authoring mode.",
            "Must not be covered, replaced, or out-ranked by supporting context, feedback, proof, preview, or diagnostic projections.",
            "Must preserve selected-path and dirty-state evidence."
          ]
        },
        {
          "id": "code-editor.form.context.project-selection",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that lets the user choose, understand, and compare source workspace subjects.",
          "relationships": [
            "Selects or explains the active source workspace/file subject.",
            "Supports editing, review, SCM evidence, and Aider context gathering.",
            "May project through any selection affordance that preserves subject identity and editing flow."
          ],
          "constraints": [
            "Must not claim primary editor authority.",
            "Must not obscure the selected source editor below usable geometry.",
            "Must keep the current selected subject traceable when file-backed editing is active."
          ]
        },
        {
          "id": "code-editor.form.context.reasoning-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting explanation, evidence, diagnostics, ownership hints, documentation references, and Aider context that help reason about the selected source subject or proposed action.",
          "relationships": [
            "Observes or explains source text, diagnostics, requirements, SCM evidence, Aider plans, and test/source ownership.",
            "May be available on demand, adjacent, tabbed, collapsed, or deferred by layout inference.",
            "Shares viewport with the primary work surface only when it preserves primary authority and geometry."
          ],
          "constraints": [
            "Must not become the selected-file editor.",
            "Must not leak as an unowned overlay over the primary work surface.",
            "Must remain distinguishable from canonical source and from write/apply controls."
          ]
        },
        {
          "id": "code-editor.form.feedback.integrity-and-activity",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Signals about app integrity, contract health, dirty/save state, policy gates, activity, failures, receipts, and recovery posture.",
          "relationships": [
            "Observes the source workspace, editor usability, runtime contract, action lifecycle, and persistence state.",
            "May render as status text, badges, counters, inline findings, panels, or machine-readable reports.",
            "Supports users, developers, and automation without defining a physical slot."
          ],
          "constraints": [
            "Ambient feedback must not interrupt or cover the primary work surface.",
            "Noticeable or corrective feedback must identify the condition it observes.",
            "Feedback projections must be owned so they are not reported as random overlays."
          ]
        },
        {
          "id": "code-editor.form.transient.widget-structure-editing",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary structure-editing UI used only while an explicit widget or layout editing mode is active.",
          "relationships": [
            "Supports structural editing operations rather than ordinary source editing.",
            "May cover or annotate the app only while its explicit mode is active.",
            "Is shell/tool infrastructure when inert and a transient projection when active."
          ],
          "constraints": [
            "Active widget editor panes, selections, and dock previews are forbidden in normal authoring mode.",
            "The inert widget-editor root is not itself a visible work surface.",
            "Transient structure-editing UI must identify its mode and owner when visible."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "open_finding_count": 3,
      "planned_or_open_count": 44,
      "runtime_check_count": 5,
      "runtime_checks": [
        {
          "id": "code-editor.runtime-check.authoring-primary-monaco",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-required-regions",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "secondary-surface-policy",
          "severity": "warning"
        },
        {
          "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "forbidden-surfaces-hidden",
          "severity": "critical"
        },
        {
          "id": "code-editor.runtime-check.authoring-lifecycle",
          "status": "specified",
          "mode": "authoring",
          "contract": "code-editor.contract.authoring.monaco-golden-path",
          "check": "lifecycle-contract-preserved",
          "severity": "critical"
        }
      ],
      "first_regions": [
        {
          "id": "code-editor.region.identity",
          "status": "specified",
          "region": "identity",
          "role": "identity-header",
          "responsibility": "Identify the active workspace, route, active file, dirty state, runtime version, gate status, and persistence state."
        },
        {
          "id": "code-editor.region.navigation",
          "status": "specified",
          "region": "navigation",
          "role": "project-navigation",
          "responsibility": "Let the user choose files, project context, open editors, and selected-file sets without applying patches or executing commands."
        },
        {
          "id": "code-editor.region.primary",
          "status": "specified",
          "region": "primary",
          "role": "primary-authoring-surface",
          "responsibility": "Own the selected-file editor, draft review, concrete diffs, and explicit preview modes while preventing supporting tools from becoming the source of truth."
        },
        {
          "id": "code-editor.region.inspector",
          "status": "specified",
          "region": "supporting-reasoning-evidence-projection",
          "role": "secondary-context-and-feedback-surface",
          "responsibility": "Project optional reasoning, evidence, diagnostics, Aider context, SCM manifests, source ownership, test ownership, documentation references, and action-specific preflight information without becoming the primary editor. A desktop renderer may currently place this projection beside the editor, but MCEL treats that placement as layout inference rather than the requirement."
        },
        {
          "id": "code-editor.region.evidence",
          "status": "specified",
          "region": "evidence",
          "role": "evidence-and-receipts-panel",
          "responsibility": "Show Aider output, SCM evidence, contract reports, regression results, receipts, and recovery guidance for reviewed actions."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-code-editor-requirements.md",
        "start_line": 18,
        "end_line": 45
      }
    },
    "file-explorer": {
      "app": "file-explorer",
      "id": "file-explorer",
      "title": "File Explorer",
      "status": "specified",
      "current_runtime_status": "full-bounded-read-only-semantic-runtime",
      "target_runtime_status": "full-read-only-semantic-runtime",
      "dominant_object": "FileEntry",
      "primary_user_goal": "Browse trusted roots, inspect directory contents, search within a bounded scope, preview readable files, classify entries, and identify the appropriate owning app without hidden filesystem, Git, remote, command, or automatic cross-app side effects.",
      "contract_complete": true,
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
      "status_counts": {
        "draft": 2,
        "open": 3,
        "planned": 3,
        "prohibited": 3,
        "specified": 33
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-file-mutation": 1,
        "local-state": 1,
        "prohibited": 1,
        "read-only": 7
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 7,
        "current_adapter_status:preflight-only": 1,
        "current_adapter_status:prohibited": 3,
        "target_adapter_status:executable": 7,
        "target_adapter_status:preflight-only": 1,
        "target_adapter_status:prohibited": 3
      },
      "use_cases": [
        {
          "id": "file-explorer.use-case.inspect-project-file-safely",
          "status": "draft",
          "goal": "Browse the current workspace, search for a known file, inspect its metadata and preview content, and decide which Main Computer app should handle it without mutating the filesystem or repository."
        },
        {
          "id": "file-explorer.use-case.browse-mounted-windows-drive",
          "status": "draft",
          "goal": "Browse a configured mounted Windows drive through File Explorer while preserving root boundaries, display-path evidence, and read-only behavior."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "file-explorer.form.subject.browse-scope",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected trusted root, current path, directory entry set, selected entry, previewable content, and mounted-root evidence.",
          "relationships": [
            "The selected entry belongs to the selected root and current path scope.",
            "Preview content, metadata, category, and suggested app derive from the selected entry.",
            "Mounted-root evidence explains when a displayed path is backed by a host path mapping."
          ],
          "constraints": [
            "Root and path boundaries remain explicit for every selected entry.",
            "Relative traversal cannot escape the selected browse scope.",
            "Read-only browsing must not imply delete, move, rename, write, Git, upload, download, or shell authority."
          ]
        },
        {
          "id": "file-explorer.form.action.inspect-entry-safely",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user selects roots, searches within scope, chooses entries, previews readable content, and decides handoff without mutating files.",
          "relationships": [
            "Search and selection operate within the active browse scope.",
            "Preview derives evidence from the selected entry and documented preview limits.",
            "Handoff suggestions connect entry category to another Main Computer app."
          ],
          "constraints": [
            "Inspection actions are read-only.",
            "Preview failures must report the reason instead of attempting mutation.",
            "Handoff suggestions must not open, write, stage, publish, or execute without a separate explicit app action."
          ]
        },
        {
          "id": "file-explorer.form.work-surface.entry-inspection",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for browsing entries, selecting a file or folder subject, inspecting metadata, and viewing safe preview evidence.",
          "relationships": [
            "Enables root selection, scoped search, directory entry inspection, metadata preview, content preview, and app-handoff reasoning.",
            "Keeps selected entry identity connected to preview and classification evidence.",
            "Preserves read-only status as part of the inspection task."
          ],
          "constraints": [
            "The primary inspection surface must remain visible and usable while browsing.",
            "Preview evidence must stay tied to the selected entry.",
            "Read-only status must remain visible enough to prevent accidental mutation assumptions."
          ]
        },
        {
          "id": "file-explorer.form.context.selection-and-classification",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains current root, path, selected entry, metadata, category, suggested app, and preview availability.",
          "relationships": [
            "Explains why an entry is classified as code, text, spreadsheet, game, asset, binary, oversized, or other.",
            "Connects preview availability to size, type, readability, and safety limits.",
            "Connects selected entries to possible downstream app handoff."
          ],
          "constraints": [
            "Context must not claim file mutation authority.",
            "Category and suggested-app evidence must be distinguishable from the file contents themselves.",
            "Missing or unreadable preview must produce explicit evidence, not blank ambiguity."
          ]
        },
        {
          "id": "file-explorer.form.feedback.boundary-and-preview-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about selected scope, read-only status, search state, preview readiness, preview failure, mounted-root status, and contract health.",
          "relationships": [
            "Observes browse scope, selected entry, preview limits, search progress, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automated contract checking.",
            "Distinguishes active browse problems from historical or resolved findings."
          ],
          "constraints": [
            "Feedback must not interrupt ordinary browsing unless a boundary, preview, or safety rule is violated.",
            "Feedback must not cover or replace the primary inspection surface.",
            "Feedback must identify the affected root, path, entry, or operation when possible."
          ]
        },
        {
          "id": "file-explorer.form.transient.search-and-selection-evidence",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary evidence created by search, selection change, preview loading, classification refresh, or handoff consideration.",
          "relationships": [
            "Supports the active inspect-entry action without becoming persistent file state.",
            "May highlight a selection, search result, classification change, or preview-loading lifecycle.",
            "Ends when the selection, query, preview, or handoff consideration changes."
          ],
          "constraints": [
            "Transient evidence must remain bounded to the active browse scope.",
            "Transient evidence must not imply mutation or permission escalation.",
            "Transient evidence must not obscure root, path, selected-entry, or read-only identity."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 11,
      "mutation_intent_count": 3,
      "prohibited_intent_count": 3,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "file-explorer.runtime-check.default-primary-surface",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "file-explorer.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "file-explorer.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "file-explorer.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "file-explorer.layout.identity",
          "status": "specified",
          "region": "roots-panel-header",
          "role": "identity",
          "responsibility": "Identify the app as File Explorer, describe read-only system browsing, and expose the global status line."
        },
        {
          "id": "file-explorer.layout.roots",
          "status": "specified",
          "region": "roots-sidebar",
          "role": "navigation",
          "responsibility": "Show selectable trusted roots such as workspace, debug-root, cwd, home, workspace-parent, filesystem-root, drive roots, or configured mounted Windows roots."
        },
        {
          "id": "file-explorer.layout.path-toolbar",
          "status": "specified",
          "region": "path-and-search-toolbar",
          "role": "navigation",
          "responsibility": "Show the current root-relative browsing scope and provide bounded search/up navigation within that scope."
        },
        {
          "id": "file-explorer.layout.directory-list",
          "status": "specified",
          "region": "directory-listing",
          "role": "primary-work-surface",
          "responsibility": "Present the current directory or search result set as the primary selectable collection, with directories before files and enough metadata to choose a preview or handoff target."
        },
        {
          "id": "file-explorer.layout.preview",
          "status": "specified",
          "region": "preview-panel",
          "role": "inspector",
          "responsibility": "Show selected entry metadata, preview content when safe, preview-denied reasons when unsafe, category evidence, and suggested app evidence."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-file-explorer-requirements.md",
        "start_line": 121,
        "end_line": 148
      }
    },
    "git-tools": {
      "app": "git-tools",
      "id": "git-tools",
      "title": "Git Tools",
      "status": "specified",
      "current_runtime_status": "runtime-baseline-with-ignore-preview-semantic-adapter",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "RepositoryProject",
      "primary_user_goal": "Inspect repository state, triage files, create safe commits, and publish selected project work through governed Git/Gitea actions without exposing raw Git plumbing as the default user path.",
      "contract_complete": true,
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
      "status_counts": {
        "implemented": 6,
        "open": 4,
        "partially-implemented": 4,
        "planned": 8,
        "prohibited": 1,
        "specified": 28
      },
      "intent_risk_counts": {
        "execution": 1,
        "local-repository-mutation": 1,
        "read-only": 6,
        "remote-mutation": 2
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 7,
        "current_adapter_status:preflight-only": 2,
        "current_adapter_status:prohibited": 1
      },
      "use_cases": [
        {
          "id": "git-tools.use-case.push-current-branch-local-gitea",
          "status": "partially-implemented",
          "goal": "Inspect repository, branch, and remote evidence, confirm the intended local Gitea target, push the current branch explicitly, and receive success or recovery evidence."
        },
        {
          "id": "git-tools.use-case.add-ignore-rule",
          "status": "planned",
          "goal": "Select an untracked file or directory, preview the proposed .gitignore rule, understand whether the target is already tracked, apply the ignore change, and refresh repository evidence."
        },
        {
          "id": "git-tools.use-case.switch-branch-safely",
          "status": "planned",
          "goal": "Inspect the current branch, available branch targets, and dirty working-tree state, then switch branches only when local work is safe or explicitly handled."
        },
        {
          "id": "git-tools.use-case.select-files-stage-commit",
          "status": "planned",
          "goal": "Inspect changed files, preview diffs, select the files that belong together, stage only those files, write a commit message, create the commit, and keep unselected changes untouched."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "git-tools.form.subject.repository-project",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected repository project, branch, remote, working-tree evidence, file basket, patch inventory, ignore rules, secrets filters, and publish target.",
          "relationships": [
            "Branch, remote, status, diff, staged intent, publish target, and receipts belong to the selected repository project.",
            "Patch inventory and file basket evidence derive from repository state but must remain distinguishable from executed Git actions.",
            "Publishing evidence connects repository state to an explicit governed target."
          ],
          "constraints": [
            "Repository identity, branch, and remote target must remain traceable before any mutation.",
            "Local evidence, remote evidence, and planned actions must not be conflated.",
            "Raw Git details may support evidence but must not become hidden default authority."
          ]
        },
        {
          "id": "git-tools.form.action.governed-repository-change",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user inspects repository state, selects files, stages intent, commits, edits ignore/filter rules, or publishes through governed preflight and receipt flow.",
          "relationships": [
            "Read actions gather status, branch, remote, diff, patch, and file evidence.",
            "Mutation actions require preflight, explicit confirmation, execution boundary, and receipt.",
            "Recovery actions derive from failed preflight, failed execution, or stale repository evidence."
          ],
          "constraints": [
            "Commit and push remain separate actions.",
            "Mutation actions require explicit target evidence and confirmation.",
            "Failed actions must produce recovery evidence without pretending repository or remote state changed."
          ]
        },
        {
          "id": "git-tools.form.work-surface.repository-workflow",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for repository triage, status review, file selection, commit preparation, governed publish actions, and recovery.",
          "relationships": [
            "Enables repository selection, status refresh, file-basket review, patch inventory review, commit preparation, ignore/filter editing, publish preflight, and recovery.",
            "Keeps evidence, intended mutation, confirmation, execution, and receipt connected.",
            "Presents advanced Git details as supporting evidence rather than default authority."
          ],
          "constraints": [
            "The primary repository workflow surface must remain visible and usable.",
            "Mutation controls must remain tied to current repository, branch, target, and preflight evidence.",
            "Evidence views must not silently execute Git commands."
          ]
        },
        {
          "id": "git-tools.form.context.evidence-and-preflight",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains branch, remote, status, diff, staged intent, ignore/filter effects, publish target, command preview, receipts, and recovery paths.",
          "relationships": [
            "Explains what evidence supports a commit, ignore change, filter change, push, or publish operation.",
            "Connects stale, missing, or conflicting evidence to preflight failures.",
            "Connects receipts and recovery suggestions to the operation that produced them."
          ],
          "constraints": [
            "Context must not hide the distinction between proposed and executed changes.",
            "Command preview must remain evidence until the user confirms execution.",
            "Receipts must name the affected repository, branch, remote, or target when available."
          ]
        },
        {
          "id": "git-tools.form.feedback.risk-and-operation-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about repository freshness, dirty state, staged intent, preflight readiness, confirmation requirement, execution result, recovery state, and contract health.",
          "relationships": [
            "Observes repository evidence, action risk, preflight state, execution state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing repository state.",
            "Distinguishes active blockers from resolved or historical findings."
          ],
          "constraints": [
            "Feedback must not make a mutation appear successful without a matching receipt.",
            "Feedback must not cover or replace the primary repository workflow surface.",
            "High-risk or failed operations may demand attention but must remain tied to recovery evidence."
          ]
        },
        {
          "id": "git-tools.form.transient.confirmation-and-recovery",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary confirmation, preflight, execution-progress, command-preview, receipt, and recovery evidence around governed Git and publishing actions.",
          "relationships": [
            "Supports explicit mutation or recovery actions without becoming repository state itself.",
            "May demand attention when action risk, missing evidence, conflict, or failure requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches repository subject."
          ],
          "constraints": [
            "Transient mutation UI requires a clear trigger and action target.",
            "Transient evidence must preserve repository, branch, remote, and target identity.",
            "Transient recovery must not perform a follow-up mutation without another explicit action."
          ]
        }
      ],
      "region_count": 8,
      "intent_count": 10,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 1,
      "open_finding_count": 4,
      "planned_or_open_count": 40,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "git-tools.runtime-check.default-primary-workflow",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "git-tools.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "git-tools.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "git-tools.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "git-tools.region.identity",
          "status": "specified",
          "region": "identity",
          "role": "repository-identity-header",
          "responsibility": "Identify the selected project, repository root, branch, remote target, backend freshness, and semantic runtime scope."
        },
        {
          "id": "git-tools.region.navigation",
          "status": "specified",
          "region": "navigation",
          "role": "repository-navigation",
          "responsibility": "Let the user choose projects, workflow tabs, file baskets, patch inventory views, and support areas without mutating Git state."
        },
        {
          "id": "git-tools.region.primary",
          "status": "specified",
          "region": "primary",
          "role": "repository-workbench",
          "responsibility": "Own the selected repository workflow, changed-file triage, project publishing strip, status summary, and commit/publish content."
        },
        {
          "id": "git-tools.region.inspector",
          "status": "specified",
          "region": "inspector",
          "role": "preflight-inspector",
          "responsibility": "Show remote configuration, selected-file evidence, ignore-rule previews, policy gates, and action-specific confirmation details."
        },
        {
          "id": "git-tools.region.evidence",
          "status": "specified",
          "region": "evidence",
          "role": "evidence-and-recovery-panel",
          "responsibility": "Show status API output, semantic adapter evidence, intent coverage, receipts, backend errors, and recovery plans."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-git-tools-requirements.md",
        "start_line": 18,
        "end_line": 76
      }
    },
    "mcel-lab": {
      "app": "mcel-lab",
      "id": "mcel-lab",
      "title": "MCEL Lab Blueprint Studio",
      "status": "specified",
      "current_runtime_status": "scope-limited-semantic-runtime",
      "target_runtime_status": "scope-limited-semantic-runtime",
      "dominant_object": "AppBlueprint",
      "primary_user_goal": "Select an app blueprint, inspect its semantic form and implementation evidence, annotate rendered elements, validate findings, and export repair context without directly rewriting live implementation files.",
      "contract_complete": true,
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
      "status_counts": {
        "implemented": 4,
        "partially-implemented": 3,
        "specified": 31
      },
      "intent_risk_counts": {
        "local-state": 4,
        "read-only": 3
      },
      "adapter_status_counts": {},
      "use_cases": [
        {
          "id": "mcel-lab.use-case.inspect-blueprint-from-doc-contract",
          "status": "specified",
          "goal": "Select an app, inspect its semantic form primitives, compare the declared contract with implementation evidence, and identify gaps before changing code."
        },
        {
          "id": "mcel-lab.use-case.self-host-refactor-context",
          "status": "specified",
          "goal": "Inspect MCEL Lab itself, annotate rendered elements, distinguish user intent from verified facts, and export reviewable repair context without directly rewriting the live Lab implementation."
        }
      ],
      "form_primitive_count": 9,
      "form_primitives": [
        {
          "id": "mcel-lab.form.subject.app-blueprint",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected app contract being inspected, validated, annotated, or prepared for repair.",
          "relationships": [
            "Owns app identity, object model, workflows, layout bindings, action policy, evidence, source/test bindings, annotations, findings, and repair plans.",
            "May represent MCEL Lab itself as a self-hosting target.",
            "Is loaded from documentation, blueprint core data, annotations, and runtime evidence."
          ],
          "constraints": [
            "AppBlueprint remains the dominant object even when a mounted app preview is visible.",
            "Prose, hardcoded JS blueprints, annotations, and runtime evidence must be distinguishable as separate evidence sources.",
            "Self-hosting inspection must not imply permission to rewrite the live Lab implementation."
          ]
        },
        {
          "id": "mcel-lab.form.action.inspect-blueprint",
          "status": "specified",
          "primitive": "action",
          "meaning": "Select an app and aspect, inspect the semantic contract and compare it with implementation evidence.",
          "relationships": [
            "Acts on mcel-lab.form.subject.app-blueprint.",
            "Uses the blueprint inspection work surface as the authoritative workspace.",
            "Consumes supporting implementation evidence, selected-element evidence, validation feedback, and annotations."
          ],
          "constraints": [
            "Inspection is read-oriented until the user explicitly creates or edits an annotation draft.",
            "Aspect navigation must not replace the selected AppBlueprint as the dominant object.",
            "Findings must distinguish documented intent from verified runtime facts."
          ]
        },
        {
          "id": "mcel-lab.form.work-surface.blueprint-inspection",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The stable surface where the selected AppBlueprint aspect, mounted preview, selected evidence, and repair context are inspected.",
          "relationships": [
            "Enables mcel-lab.form.action.inspect-blueprint.",
            "Represents the selected AppBlueprint and current aspect.",
            "Hosts mounted app preview evidence without granting that preview primary Lab authority."
          ],
          "constraints": [
            "Must remain visible and usable when MCEL Lab is active.",
            "Must keep selected app, selected aspect, and mounted route evidence traceable.",
            "Must not be covered or out-ranked by unowned feedback, transient overlays, or debug/proof internals."
          ]
        },
        {
          "id": "mcel-lab.form.context.app-and-aspect-selection",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that chooses which AppBlueprint and which aspect are being inspected.",
          "relationships": [
            "Selects the active subject for the blueprint inspection work surface.",
            "Filters the visible evidence, annotations, findings, and repair context.",
            "May render as controls, lists, command choices, tabs, or another inferred projection."
          ],
          "constraints": [
            "Must keep the selected app and aspect recoverable from visible UI or machine-readable state.",
            "Must not claim primary work-surface authority.",
            "Must not make physical placement part of the semantic contract."
          ]
        },
        {
          "id": "mcel-lab.form.context.implementation-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting evidence about DOM elements, source files, CSS ownership, tests, annotations, validation findings, and repair candidates.",
          "relationships": [
            "Explains the selected AppBlueprint, selected aspect, and selected rendered element.",
            "May be gathered from mounted previews, point inspection, annotation maps, source bindings, test bindings, and registry payloads.",
            "Supports repair planning without becoming a direct patch applicator."
          ],
          "constraints": [
            "Evidence must identify its source and freshness when it is used to justify a finding.",
            "Implementation evidence must not be confused with the target requirement itself.",
            "Derived repair context must remain reviewable before patch generation."
          ]
        },
        {
          "id": "mcel-lab.form.feedback.validation-and-mount-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Signals about selected app state, mount readiness, inspection mode, annotation save state, validation findings, export readiness, and repair-plan readiness.",
          "relationships": [
            "Observes app selection, aspect selection, mounted preview state, selected element state, annotation state, and validation results.",
            "May render as badges, receipts, inline findings, result summaries, or machine-readable packets.",
            "Serves users, developers, and automation without defining a physical slot."
          ],
          "constraints": [
            "Ambient feedback must not interrupt or obscure blueprint inspection.",
            "Corrective feedback must identify the condition it observes.",
            "Feedback projections must have an owner so they are not diagnosed as random overlays."
          ]
        },
        {
          "id": "mcel-lab.form.constraint.self-hosting-safety",
          "status": "specified",
          "primitive": "constraint",
          "meaning": "Safety law that lets MCEL Lab inspect and draft changes to its own blueprint without directly mutating its live implementation.",
          "relationships": [
            "Protects mcel-lab.form.subject.app-blueprint when selectedApp is mcel-lab.",
            "Applies to annotation edits, repair plans, export packets, and patch artifact generation.",
            "Separates draft intent from implementation mutation."
          ],
          "constraints": [
            "MCEL Lab may edit its own blueprint draft.",
            "MCEL Lab must not directly rewrite or apply its own live implementation.",
            "Self-hosting repair output must be reviewable as an artifact before any local patch workflow applies it."
          ]
        },
        {
          "id": "mcel-lab.form.transient.point-inspection",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary inspection UI used while the user is selecting a rendered element and capturing evidence.",
          "relationships": [
            "Supports element selection, bounding-box evidence, annotation drafting, and source/test ownership hints.",
            "Is active only while inspect mode is enabled or a selected element receipt is being reviewed.",
            "May annotate the mounted preview without mutating the mounted app."
          ],
          "constraints": [
            "Must be explicitly mode-bound and reversible.",
            "Must not fire the mounted app's ordinary actions while selecting an element.",
            "Must identify selected element evidence separately from user-authored annotation intent."
          ]
        },
        {
          "id": "mcel-lab.form.interruption.unsafe-repair-boundary",
          "status": "specified",
          "primitive": "interruption",
          "meaning": "Attention-demanding boundary used when a repair, removal, or self-hosting operation could be mistaken for a verified implementation fact or direct mutation.",
          "relationships": [
            "Protects patch planning, self-hosting edits, removal candidates, and destructive annotations.",
            "Can block export or require review when evidence is stale or unsafe.",
            "Explains recovery actions before any patch artifact is generated."
          ],
          "constraints": [
            "Must interrupt or block when the user attempts direct self-mutation.",
            "Must require evidence before deletion or rework candidates become patch guidance.",
            "Must separate possible fixes from verified facts."
          ]
        }
      ],
      "region_count": 7,
      "intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "open_finding_count": 0,
      "planned_or_open_count": 31,
      "runtime_check_count": 4,
      "runtime_checks": [
        {
          "id": "mcel-lab.runtime.primary-blueprint-workspace",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "mcel-lab.runtime.required-semantic-projections",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "required-regions-visible",
          "severity": "error"
        },
        {
          "id": "mcel-lab.runtime.visual-integrity-baseline",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "visual-integrity-baseline",
          "severity": "critical"
        },
        {
          "id": "mcel-lab.runtime.self-hosting-safety-boundary",
          "status": "specified",
          "mode": "default",
          "contract": "mcel-lab.contract.default.blueprint-studio-health",
          "check": "lifecycle-contract-preserved",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "mcel-lab.region.app-root",
          "status": "implemented",
          "region": "lab-app-root",
          "role": "app-boundary",
          "responsibility": "Owns the MCEL Lab application boundary and exposes the selected AppBlueprint as the dominant object."
        },
        {
          "id": "mcel-lab.region.selection-context",
          "status": "implemented",
          "region": "app-and-aspect-selection-context",
          "role": "supporting-context",
          "responsibility": "Projects app and aspect selection primitives without making their physical placement normative."
        },
        {
          "id": "mcel-lab.region.aspect-map",
          "status": "implemented",
          "region": "aspect-map-projection",
          "role": "navigation-context",
          "responsibility": "Exposes inspectable blueprint aspects and keeps the selected aspect traceable."
        },
        {
          "id": "mcel-lab.region.blueprint-workspace",
          "status": "implemented",
          "region": "blueprint-inspection-workspace",
          "role": "primary-work-surface",
          "responsibility": "Projects the selected AppBlueprint aspect and mounted preview evidence as the main inspection workspace."
        },
        {
          "id": "mcel-lab.region.mounted-preview",
          "status": "partially-implemented",
          "region": "mounted-app-preview-projection",
          "role": "implementation-evidence-context",
          "responsibility": "Shows a contained app preview as evidence while preserving AppBlueprint authority."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-lab-blueprint-studio.md",
        "start_line": 84,
        "end_line": 111
      }
    },
    "website-builder": {
      "app": "website-builder",
      "id": "website-builder",
      "title": "Website Builder and Websites",
      "status": "specified",
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "dominant_object": "WebsiteProject",
      "primary_user_goal": "Edit saved websites, configure optional site runtime layers, preview and publish to explicit lanes, and hand repository changes to Git Tools without confusing author-owned source, generated runtime evidence, deployment targets, or remote sync.",
      "contract_complete": true,
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
      "status_counts": {
        "open": 4,
        "planned": 4,
        "specified": 46
      },
      "intent_risk_counts": {
        "local-file-mutation": 3,
        "local-state": 3,
        "read-only": 4,
        "remote-mutation": 2
      },
      "adapter_status_counts": {
        "current_adapter_status:executable": 12,
        "target_adapter_status:executable": 12
      },
      "use_cases": [
        {
          "id": "website-builder.use-case.edit-preview-saved-site",
          "status": "specified",
          "goal": "Select a saved website, edit its visible content or styling, preview the draft, save the site source, and verify that the saved site still has a coherent manifest, builder state, entry HTML, stylesheet, script, and page runtime."
        },
        {
          "id": "website-builder.use-case.configure-blog-runtime",
          "status": "specified",
          "goal": "Configure or inspect the blog-capable site runtime without confusing source pages, local database artifacts, Directus storage, generated API routes, or published website files."
        },
        {
          "id": "website-builder.use-case.publish-selected-lane",
          "status": "specified",
          "goal": "Publish a saved website to one explicit lane, verify the target URL, and keep local authoring, local server, dev deployment, and remote production separate."
        },
        {
          "id": "website-builder.use-case.git-tools-handoff",
          "status": "specified",
          "goal": "Turn saved website changes into reviewable repository evidence, then use Git Tools for file selection, commit, and governed push rather than hiding Git mutation inside Website Builder."
        }
      ],
      "form_primitive_count": 6,
      "form_primitives": [
        {
          "id": "website-builder.form.subject.website-project",
          "status": "specified",
          "primitive": "subject",
          "meaning": "The selected saved website, page source, builder state, manifest, runtime configuration, generated evidence, publish target, and repository handoff state.",
          "relationships": [
            "Site manifest, builder state, source files, generated runtime evidence, and publish receipts belong to the selected website project.",
            "Author-owned source, local runtime data, generated files, deployment targets, and Git handoff evidence must remain distinguishable.",
            "Publish lane evidence derives from an explicit target and preflight state."
          ],
          "constraints": [
            "Selected website identity must remain traceable across edit, preview, save, configure, publish, and handoff actions.",
            "Generated runtime evidence must not be confused with author-owned source.",
            "Remote or deployment state must not be implied by local save or preview."
          ]
        },
        {
          "id": "website-builder.form.action.author-preview-publish",
          "status": "specified",
          "primitive": "action",
          "meaning": "The user selects a website, edits content or style, previews draft output, saves source artifacts, configures runtime layers, publishes to an explicit lane, or hands work to Git Tools.",
          "relationships": [
            "Edit and save actions mutate only the selected website source artifacts.",
            "Preview actions derive evidence without publishing.",
            "Publish actions require target evidence, preflight, confirmation, execution, and receipt."
          ],
          "constraints": [
            "Save, preview, local publish, dev publish, remote publish, and Git handoff remain separate actions.",
            "Destructive runtime or storage choices require explicit acknowledgement.",
            "Failed preview, save, setup, publish, or handoff actions must preserve recovery evidence."
          ]
        },
        {
          "id": "website-builder.form.work-surface.site-authoring",
          "status": "specified",
          "primitive": "work-surface",
          "meaning": "The primary stable work surface for selecting a website project, authoring source, inspecting preview evidence, configuring runtime state, and preparing publish or handoff actions.",
          "relationships": [
            "Enables site selection, content/style editing, source save, draft preview, runtime setup review, publish preflight, and Git Tools handoff.",
            "Keeps author-owned source, generated evidence, runtime setup, and publish state connected to the selected website project.",
            "Presents deployment evidence as a governed extension of the authoring workflow."
          ],
          "constraints": [
            "The primary authoring surface must remain visible and usable during editing and preview.",
            "Publish and runtime setup controls must remain tied to selected website and explicit target evidence.",
            "Generated evidence must not claim source authority."
          ]
        },
        {
          "id": "website-builder.form.context.runtime-and-publish-evidence",
          "status": "specified",
          "primitive": "context",
          "meaning": "Supporting context that explains manifest state, builder state, source artifacts, generated runtime files, database/CMS layers, publish targets, receipts, and Git handoff evidence.",
          "relationships": [
            "Explains whether evidence came from source, generated runtime, local server, dev deployment, remote target, or repository handoff.",
            "Connects runtime setup dependencies to explicit choices and receipts.",
            "Connects publish results to the lane and target that produced them."
          ],
          "constraints": [
            "Context must keep author-owned source, generated files, runtime data, and deployed state distinguishable.",
            "Context must not hide destructive storage or remote deployment risk.",
            "Receipts must name the selected website and target lane when available."
          ]
        },
        {
          "id": "website-builder.form.feedback.save-preview-publish-state",
          "status": "specified",
          "primitive": "feedback",
          "meaning": "Feedback about dirty state, save result, preview readiness, runtime setup state, publish preflight, publish result, Git handoff readiness, and contract health.",
          "relationships": [
            "Observes selected website state, authoring activity, preview generation, setup progress, publish workflow, handoff state, and runtime integrity.",
            "Supports user safety, developer diagnosis, and automation without changing website source by itself.",
            "Distinguishes active issues from historical or resolved findings."
          ],
          "constraints": [
            "Feedback must not claim deployment success without a matching receipt.",
            "Feedback must not cover or replace the primary authoring surface.",
            "Feedback must identify the selected website, lane, runtime layer, or handoff target when possible."
          ]
        },
        {
          "id": "website-builder.form.transient.setup-publish-and-handoff",
          "status": "specified",
          "primitive": "transient",
          "meaning": "Temporary setup, generation, confirmation, execution-progress, receipt, and recovery evidence for runtime configuration, publish, and Git handoff operations.",
          "relationships": [
            "Supports explicit setup, publish, or handoff actions without becoming website source itself.",
            "May demand attention when storage, deployment, or repository risk requires a user decision.",
            "Ends when the user confirms, cancels, receives a receipt, or switches website subject."
          ],
          "constraints": [
            "Transient mutation UI requires a clear selected website and target.",
            "Transient evidence must preserve source/generated/runtime/deployment boundaries.",
            "Transient recovery must not perform follow-up mutation without another explicit action."
          ]
        }
      ],
      "region_count": 10,
      "intent_count": 12,
      "mutation_intent_count": 8,
      "prohibited_intent_count": 0,
      "open_finding_count": 4,
      "planned_or_open_count": 54,
      "runtime_check_count": 3,
      "runtime_checks": [
        {
          "id": "website-builder.runtime-check.default-primary-preview",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "primary-surface",
          "severity": "critical"
        },
        {
          "id": "website-builder.runtime-check.default-required-regions",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "required-regions-visible",
          "severity": "critical"
        },
        {
          "id": "website-builder.runtime-check.default-overlay-policy",
          "status": "specified",
          "mode": "default",
          "contract": "website-builder.contract.default.app-health",
          "check": "overlay-policy",
          "severity": "warning"
        }
      ],
      "first_regions": [
        {
          "id": "website-builder.region.identity",
          "status": "specified",
          "region": "website-identity-header",
          "role": "identity-header",
          "responsibility": "Identify the selected website, current site metadata, dirty/save state, and source-vs-saved status across edit, preview, and publish workflows."
        },
        {
          "id": "website-builder.region.site-selector",
          "status": "specified",
          "region": "saved-site-navigation",
          "role": "navigation",
          "responsibility": "Let the user choose, create, search, and locate saved website projects without performing destructive site operations implicitly."
        },
        {
          "id": "website-builder.region.design-surface",
          "status": "specified",
          "region": "primary-design-surface",
          "role": "primary-work-surface",
          "responsibility": "Own the author-facing GrapesJS design canvas, page blocks, and draft page state during normal website editing."
        },
        {
          "id": "website-builder.region.preview-surface",
          "status": "specified",
          "region": "website-preview-surface",
          "role": "preview-surface",
          "responsibility": "Show draft, local, dev, or remote preview lanes and their availability without implying that preview equals publish success."
        },
        {
          "id": "website-builder.region.source-and-manifest",
          "status": "specified",
          "region": "source-manifest-evidence-panel",
          "role": "evidence-panel",
          "responsibility": "Expose site source, builder metadata, generated artifacts, runtime selection, and manifest evidence for the selected website."
        }
      ],
      "source": {
        "file": "pretty_docs/mcel-website-builder-requirements.md",
        "start_line": 162,
        "end_line": 196
      }
    }
  },
  "runtime_diagnostic_contracts": {
    "calculator": {
      "app": "calculator",
      "mode_contracts": {
        "default": {
          "contractId": "calculator.contract.default.app-health",
          "appId": "calculator",
          "mode": "default",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "calculator.surface.workspace",
            "label": "Calculator default mode must expose a usable workspace.",
            "hostSelector": ".calculator-workspace",
            "editorSelector": ".calculator-workspace",
            "minWidth": 420,
            "minHeight": 320
          },
          "requiredRegions": [
            {
              "id": "calculator.region.root",
              "selector": "#calculator-app",
              "label": "Calculator app root"
            },
            {
              "id": "calculator.region.shell",
              "selector": ".calculator-shell",
              "label": "Calculator shell"
            },
            {
              "id": "calculator.region.mode-switch",
              "selector": ".calculator-mode-switch",
              "label": "Calculator mode switch"
            },
            {
              "id": "calculator.region.workspace",
              "selector": ".calculator-workspace",
              "label": "Calculator workspace"
            },
            {
              "id": "calculator.region.display",
              "selector": "#calculator-display",
              "label": "Calculator display"
            }
          ],
          "optionalRegions": [],
          "allowedRegions": [],
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "geometryPolicies": [],
          "overlayPolicy": [],
          "checkCategories": [],
          "focusModes": [],
          "checks": [
            {
              "id": "calculator.runtime-check.default-overlay-policy",
              "app": "calculator",
              "status": "specified",
              "mode": "default",
              "contract": "calculator.contract.default.app-health",
              "check": "overlay-policy",
              "check_category": "",
              "focus": "",
              "severity": "warning",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "expects": [
                "MCEL/widget/proof overlays are not visible while the calculator is in default mode."
              ],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Calculator default mode should not be covered by diagnostic overlays.",
              "next_probe": "overlay.detector",
              "source_binding": "calculator.binding.route-and-ui",
              "test_binding": "calculator.test.route-checks",
              "source": {
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 1078,
                "end_line": 1101
              }
            },
            {
              "id": "calculator.runtime-check.default-primary-workspace",
              "app": "calculator",
              "status": "specified",
              "mode": "default",
              "contract": "calculator.contract.default.app-health",
              "check": "primary-surface",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                ".calculator-workspace"
              ],
              "expects": [
                "Calculator workspace is visible and large enough for the active mode.",
                "The primary calculator surface is not collapsed by surrounding app chrome."
              ],
              "forbids": [],
              "primary_surface_id": "calculator.surface.workspace",
              "host_selector": ".calculator-workspace",
              "editor_selector": ".calculator-workspace",
              "min_width": "420",
              "min_height": "320",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Calculator default mode must expose a usable workspace.",
              "next_probe": "layout.ownerProbe",
              "source_binding": "calculator.binding.route-and-ui",
              "test_binding": "calculator.test.route-checks",
              "source": {
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 1024,
                "end_line": 1046
              }
            },
            {
              "id": "calculator.runtime-check.default-required-regions",
              "app": "calculator",
              "status": "specified",
              "mode": "default",
              "contract": "calculator.contract.default.app-health",
              "check": "required-regions-visible",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "#calculator-app",
                ".calculator-shell",
                ".calculator-mode-switch",
                ".calculator-workspace",
                "#calculator-display"
              ],
              "expects": [
                "Calculator app root is visible.",
                "Mode switch remains visible.",
                "Calculator workspace and display remain visible."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "calculator.region.root",
                  "selector": "#calculator-app",
                  "label": "Calculator app root"
                },
                {
                  "id": "calculator.region.shell",
                  "selector": ".calculator-shell",
                  "label": "Calculator shell"
                },
                {
                  "id": "calculator.region.mode-switch",
                  "selector": ".calculator-mode-switch",
                  "label": "Calculator mode switch"
                },
                {
                  "id": "calculator.region.workspace",
                  "selector": ".calculator-workspace",
                  "label": "Calculator workspace"
                },
                {
                  "id": "calculator.region.display",
                  "selector": "#calculator-display",
                  "label": "Calculator display"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Calculator default mode must preserve root, controls, workspace, and display.",
              "next_probe": "layout.baseline",
              "source_binding": "calculator.binding.route-and-ui",
              "test_binding": "calculator.test.route-checks",
              "source": {
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 1048,
                "end_line": 1076
              }
            }
          ]
        }
      }
    },
    "code-editor": {
      "app": "code-editor",
      "mode_contracts": {
        "authoring": {
          "contractId": "code-editor.contract.authoring.monaco-golden-path",
          "appId": "code-editor",
          "mode": "authoring",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "code-editor.surface.monaco-selected-file-editor",
            "label": "Authoring mode must expose one usable Monaco selected-file editor.",
            "hostSelector": "#code-studio-runtime-monaco",
            "editorSelector": ".monaco-editor",
            "minWidth": 360,
            "minHeight": 320
          },
          "requiredRegions": [
            {
              "id": "code-editor.region.root",
              "selector": "#code-editor-app",
              "label": "Code Editor app root"
            },
            {
              "id": "code-editor.region.explorer",
              "selector": ".code-studio-sidebar",
              "label": "Explorer"
            },
            {
              "id": "code-editor.region.editor-group",
              "selector": ".code-studio-editor-group",
              "label": "Editor group"
            },
            {
              "id": "code-editor.region.statusbar",
              "selector": ".code-studio-statusbar",
              "label": "Status bar"
            }
          ],
          "optionalRegions": [
            {
              "id": "code-editor.region.inspector",
              "selector": ".code-studio-inspector",
              "label": "Supporting reasoning/evidence projection"
            }
          ],
          "allowedRegions": [
            {
              "id": "code-editor.allowed.mcel-tools-toggle",
              "selector": "#code-editor-mcel-tools-toggle",
              "label": "MCEL tools toggle projection"
            },
            {
              "id": "code-editor.allowed.diagnostics-counter",
              "selector": "#code-editor-diagnostics-counter",
              "label": "Ambient integrity feedback projection"
            }
          ],
          "forbiddenRegions": [
            {
              "id": "code-editor.forbidden.source-pane",
              "selector": "[data-code-studio-pane=\"source\"]",
              "label": "MCEL source model pane"
            },
            {
              "id": "code-editor.forbidden.serialized-pane",
              "selector": "[data-code-studio-pane=\"serialized\"]",
              "label": "Serialized output pane"
            },
            {
              "id": "code-editor.forbidden.contract-pane",
              "selector": "[data-code-studio-pane=\"contract\"]",
              "label": "Contract report pane"
            },
            {
              "id": "code-editor.forbidden.runtime-scaffold.window",
              "selector": ".code-studio-runtime-window",
              "label": "Generated runtime window scaffold"
            },
            {
              "id": "code-editor.forbidden.runtime-scaffold.layout",
              "selector": ".code-studio-runtime-layout",
              "label": "Generated runtime layout scaffold"
            },
            {
              "id": "code-editor.forbidden.runtime-file-rail",
              "selector": ".code-studio-runtime-files",
              "label": "Generated runtime file rail"
            },
            {
              "id": "code-editor.forbidden.fallback-textarea",
              "selector": "#code-studio-runtime-draft, .code-studio-runtime-fallback",
              "label": "Fallback textarea"
            },
            {
              "id": "code-editor.forbidden.proof-dock",
              "selector": ".code-studio-proof-dock, #code-studio-bottom-panel",
              "label": "MCEL proof/evidence dock"
            },
            {
              "id": "code-editor.forbidden.widget-overlay",
              "selector": "#mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden])",
              "label": "Active widget editor overlay"
            }
          ],
          "lifecycleAssertions": [
            "startup-authoring-mode-has-one-primary-editor",
            "file-click-keeps-one-primary-editor",
            "resize-keeps-primary-editor-usable",
            "mcel-diagnostics-hidden-in-authoring"
          ],
          "geometryPolicies": [
            "supporting-projection-visible-min-width-240",
            "supporting-projection-max-width-ratio-0.40",
            "supporting-projection-must-collapse-before-primary-breaks"
          ],
          "overlayPolicy": [
            "diagnostics-owned-by-supporting-or-feedback-projection-are-allowed",
            "diagnostics-covering-primary-editor-are-forbidden"
          ],
          "checkCategories": [
            "overlays",
            "lifecycle",
            "surface",
            "layout",
            "form"
          ],
          "focusModes": [
            "forbidden-surfaces",
            "startup-file-click-resize",
            "primary-editor",
            "required-regions",
            "supporting-context-feedback-projection"
          ],
          "checks": [
            {
              "id": "code-editor.runtime-check.authoring-forbidden-surfaces",
              "app": "code-editor",
              "status": "specified",
              "mode": "authoring",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "check": "forbidden-surfaces-hidden",
              "check_category": "overlays",
              "focus": "forbidden-surfaces",
              "severity": "critical",
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
              "expects": [
                "Source model pane is hidden.",
                "Serialized and contract panes are hidden.",
                "Generated runtime window/layout/file rail are absent from the default path.",
                "Fallback textarea is not visible in the Monaco golden path.",
                "Proof docks and active widget editor overlays are not visible in authoring mode; the inert widget-editor shell is not treated as a visible overlay."
              ],
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
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "MCEL diagnostic/runtime scaffolding must not leak into Code Editor authoring mode.",
              "next_probe": "overlay.detector",
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis",
              "source": {
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 897,
                "end_line": 941
              }
            },
            {
              "id": "code-editor.runtime-check.authoring-lifecycle",
              "app": "code-editor",
              "status": "specified",
              "mode": "authoring",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "check": "lifecycle-contract-preserved",
              "check_category": "lifecycle",
              "focus": "startup-file-click-resize",
              "severity": "critical",
              "observes": [
                "startup",
                "file-click",
                "resize"
              ],
              "expects": [
                "Startup authoring mode has exactly one primary Monaco editor.",
                "Clicking another file keeps exactly one primary Monaco editor.",
                "Resize keeps the Monaco host and editor useful."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [
                "startup-authoring-mode-has-one-primary-editor",
                "file-click-keeps-one-primary-editor",
                "resize-keeps-primary-editor-usable",
                "mcel-diagnostics-hidden-in-authoring"
              ],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "File selection and reload must preserve the Code Editor authoring contract.",
              "next_probe": "startup.timeline",
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis",
              "source": {
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 943,
                "end_line": 970
              }
            },
            {
              "id": "code-editor.runtime-check.authoring-primary-monaco",
              "app": "code-editor",
              "status": "specified",
              "mode": "authoring",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "check": "primary-surface",
              "check_category": "surface",
              "focus": "primary-editor",
              "severity": "critical",
              "observes": [
                "#code-studio-runtime-monaco",
                ".monaco-editor"
              ],
              "expects": [
                "Monaco host is visible and at least 360px wide by 320px tall.",
                "Monaco editor instance is visible and at least 360px wide by 320px tall.",
                "No fallback or source-model editor surface competes with Monaco in authoring mode."
              ],
              "forbids": [],
              "primary_surface_id": "code-editor.surface.monaco-selected-file-editor",
              "host_selector": "#code-studio-runtime-monaco",
              "editor_selector": ".monaco-editor",
              "min_width": "360",
              "min_height": "320",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Authoring mode must expose one usable Monaco selected-file editor.",
              "next_probe": "layout.ownerProbe",
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis",
              "source": {
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 793,
                "end_line": 819
              }
            },
            {
              "id": "code-editor.runtime-check.authoring-required-regions",
              "app": "code-editor",
              "status": "specified",
              "mode": "authoring",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "check": "required-regions-visible",
              "check_category": "layout",
              "focus": "required-regions",
              "severity": "critical",
              "observes": [
                "#code-editor-app",
                ".code-studio-sidebar",
                ".code-studio-editor-group",
                ".code-studio-statusbar"
              ],
              "expects": [
                "Code Editor root is present and visible.",
                "Explorer region is present and visible.",
                "Editor group is present and visible.",
                "Status bar is present and visible."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "code-editor.region.root",
                  "selector": "#code-editor-app",
                  "label": "Code Editor app root"
                },
                {
                  "id": "code-editor.region.explorer",
                  "selector": ".code-studio-sidebar",
                  "label": "Explorer"
                },
                {
                  "id": "code-editor.region.editor-group",
                  "selector": ".code-studio-editor-group",
                  "label": "Editor group"
                },
                {
                  "id": "code-editor.region.statusbar",
                  "selector": ".code-studio-statusbar",
                  "label": "Status bar"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Authoring mode must preserve the app root, explorer, editor group, and status bar.",
              "next_probe": "layout.baseline",
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis",
              "source": {
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 821,
                "end_line": 850
              }
            },
            {
              "id": "code-editor.runtime-check.authoring-supporting-projection-policy",
              "app": "code-editor",
              "status": "specified",
              "mode": "authoring",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "check": "secondary-surface-policy",
              "check_category": "form",
              "focus": "supporting-context-feedback-projection",
              "severity": "warning",
              "observes": [
                ".code-studio-inspector",
                "[data-code-studio-workbench-region=\\\"scm-ai-inspector\\\"]",
                "#code-editor-mcel-tools-toggle",
                "#code-editor-diagnostics-counter"
              ],
              "expects": [
                "Supporting reasoning, evidence, diagnostics, and assistant context are allowed in authoring mode as non-primary projections.",
                "Supporting projections may be visible, collapsed, tabbed, deferred, or trigger-only without becoming the primary editor.",
                "MCEL tools, diagnosis history, contract findings, source ownership, and test ownership must project from owned context or feedback primitives, or from an explicit mode.",
                "Supporting projections must not cover the Monaco editor or reduce it below its minimum geometry."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [
                {
                  "id": "code-editor.region.inspector",
                  "selector": ".code-studio-inspector",
                  "label": "Supporting reasoning/evidence projection"
                }
              ],
              "allowed_regions": [
                {
                  "id": "code-editor.allowed.mcel-tools-toggle",
                  "selector": "#code-editor-mcel-tools-toggle",
                  "label": "MCEL tools toggle projection"
                },
                {
                  "id": "code-editor.allowed.diagnostics-counter",
                  "selector": "#code-editor-diagnostics-counter",
                  "label": "Ambient integrity feedback projection"
                }
              ],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [
                "supporting-projection-visible-min-width-240",
                "supporting-projection-max-width-ratio-0.40",
                "supporting-projection-must-collapse-before-primary-breaks"
              ],
              "overlay_policy": [
                "diagnostics-owned-by-supporting-or-feedback-projection-are-allowed",
                "diagnostics-covering-primary-editor-are-forbidden"
              ],
              "ownership_hints": [],
              "failure_message": "Supporting context and feedback projections are allowed when they do not compete with the selected-source editor.",
              "next_probe": "semanticProjection.containment",
              "source_binding": "code-editor.binding.authoring-cockpit-layout",
              "test_binding": "code-editor.test.authoring-cockpit-diagnosis",
              "source": {
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 853,
                "end_line": 895
              }
            }
          ]
        }
      }
    },
    "file-explorer": {
      "app": "file-explorer",
      "mode_contracts": {
        "default": {
          "contractId": "file-explorer.contract.default.app-health",
          "appId": "file-explorer",
          "mode": "default",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "file-explorer.surface.main",
            "label": "File Explorer default mode must expose a usable browsing surface.",
            "hostSelector": ".file-explorer-main",
            "editorSelector": ".file-explorer-main",
            "minWidth": 420,
            "minHeight": 320
          },
          "requiredRegions": [
            {
              "id": "file-explorer.region.root",
              "selector": "#file-explorer-app",
              "label": "File Explorer app root"
            },
            {
              "id": "file-explorer.region.roots",
              "selector": ".file-explorer-roots-panel",
              "label": "Roots panel"
            },
            {
              "id": "file-explorer.region.main",
              "selector": ".file-explorer-main",
              "label": "Main browsing surface"
            },
            {
              "id": "file-explorer.region.toolbar",
              "selector": ".file-explorer-toolbar",
              "label": "Path/search toolbar"
            },
            {
              "id": "file-explorer.region.list",
              "selector": "#file-explorer-list",
              "label": "File list"
            }
          ],
          "optionalRegions": [],
          "allowedRegions": [],
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "geometryPolicies": [],
          "overlayPolicy": [],
          "checkCategories": [],
          "focusModes": [],
          "checks": [
            {
              "id": "file-explorer.runtime-check.default-overlay-policy",
              "app": "file-explorer",
              "status": "specified",
              "mode": "default",
              "contract": "file-explorer.contract.default.app-health",
              "check": "overlay-policy",
              "check_category": "",
              "focus": "",
              "severity": "warning",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "expects": [
                "MCEL/widget/proof overlays are not visible while browsing files."
              ],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "File Explorer should not be covered by diagnostic overlays in default mode.",
              "next_probe": "overlay.detector",
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "test_binding": "file-explorer.test.viewport-file-explorer",
              "source": {
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 1046,
                "end_line": 1069
              }
            },
            {
              "id": "file-explorer.runtime-check.default-primary-surface",
              "app": "file-explorer",
              "status": "specified",
              "mode": "default",
              "contract": "file-explorer.contract.default.app-health",
              "check": "primary-surface",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                ".file-explorer-main"
              ],
              "expects": [
                "File Explorer main browsing surface is visible and usable.",
                "The list/preview work area is not collapsed."
              ],
              "forbids": [],
              "primary_surface_id": "file-explorer.surface.main",
              "host_selector": ".file-explorer-main",
              "editor_selector": ".file-explorer-main",
              "min_width": "420",
              "min_height": "320",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "File Explorer default mode must expose a usable browsing surface.",
              "next_probe": "layout.ownerProbe",
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "test_binding": "file-explorer.test.viewport-file-explorer",
              "source": {
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 994,
                "end_line": 1016
              }
            },
            {
              "id": "file-explorer.runtime-check.default-required-regions",
              "app": "file-explorer",
              "status": "specified",
              "mode": "default",
              "contract": "file-explorer.contract.default.app-health",
              "check": "required-regions-visible",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "#file-explorer-app",
                ".file-explorer-roots-panel",
                ".file-explorer-main",
                ".file-explorer-toolbar",
                "#file-explorer-list"
              ],
              "expects": [
                "Root, roots panel, toolbar, main surface, and file list are visible."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "file-explorer.region.root",
                  "selector": "#file-explorer-app",
                  "label": "File Explorer app root"
                },
                {
                  "id": "file-explorer.region.roots",
                  "selector": ".file-explorer-roots-panel",
                  "label": "Roots panel"
                },
                {
                  "id": "file-explorer.region.main",
                  "selector": ".file-explorer-main",
                  "label": "Main browsing surface"
                },
                {
                  "id": "file-explorer.region.toolbar",
                  "selector": ".file-explorer-toolbar",
                  "label": "Path/search toolbar"
                },
                {
                  "id": "file-explorer.region.list",
                  "selector": "#file-explorer-list",
                  "label": "File list"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "File Explorer default mode must preserve roots, toolbar, and list.",
              "next_probe": "layout.baseline",
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "test_binding": "file-explorer.test.viewport-file-explorer",
              "source": {
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 1018,
                "end_line": 1044
              }
            }
          ]
        }
      }
    },
    "git-tools": {
      "app": "git-tools",
      "mode_contracts": {
        "default": {
          "contractId": "git-tools.contract.default.app-health",
          "appId": "git-tools",
          "mode": "default",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "git-tools.surface.workflow",
            "label": "Git Tools default mode must expose a usable workflow surface.",
            "hostSelector": "#git-project-workflow-surface",
            "editorSelector": "#git-project-workflow-surface",
            "minWidth": 420,
            "minHeight": 320
          },
          "requiredRegions": [
            {
              "id": "git-tools.region.root",
              "selector": "#git-tools-app",
              "label": "Git Tools app root"
            },
            {
              "id": "git-tools.region.shell",
              "selector": ".git-tools-shell",
              "label": "Git Tools shell"
            },
            {
              "id": "git-tools.region.project-selector",
              "selector": "#git-project-selector-panel",
              "label": "Project selector"
            },
            {
              "id": "git-tools.region.workflow",
              "selector": "#git-project-workflow-surface",
              "label": "Project workflow surface"
            }
          ],
          "optionalRegions": [],
          "allowedRegions": [],
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "geometryPolicies": [],
          "overlayPolicy": [],
          "checkCategories": [],
          "focusModes": [],
          "checks": [
            {
              "id": "git-tools.runtime-check.default-overlay-policy",
              "app": "git-tools",
              "status": "specified",
              "mode": "default",
              "contract": "git-tools.contract.default.app-health",
              "check": "overlay-policy",
              "check_category": "",
              "focus": "",
              "severity": "warning",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "expects": [
                "MCEL/widget/proof overlays are not visible while running the default Git Tools workflow."
              ],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Git Tools default mode should not be covered by diagnostic overlays.",
              "next_probe": "overlay.detector",
              "source_binding": "git-tools.binding.project-workflow",
              "test_binding": "git-tools.test.semantic-adapter",
              "source": {
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1143,
                "end_line": 1166
              }
            },
            {
              "id": "git-tools.runtime-check.default-primary-workflow",
              "app": "git-tools",
              "status": "specified",
              "mode": "default",
              "contract": "git-tools.contract.default.app-health",
              "check": "primary-surface",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "#git-project-workflow-surface"
              ],
              "expects": [
                "Git Tools project workflow surface is visible and usable.",
                "The workflow surface is not collapsed by rails or proof panels."
              ],
              "forbids": [],
              "primary_surface_id": "git-tools.surface.workflow",
              "host_selector": "#git-project-workflow-surface",
              "editor_selector": "#git-project-workflow-surface",
              "min_width": "420",
              "min_height": "320",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Git Tools default mode must expose a usable workflow surface.",
              "next_probe": "layout.ownerProbe",
              "source_binding": "git-tools.binding.project-workflow",
              "test_binding": "git-tools.test.semantic-adapter",
              "source": {
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1093,
                "end_line": 1115
              }
            },
            {
              "id": "git-tools.runtime-check.default-required-regions",
              "app": "git-tools",
              "status": "specified",
              "mode": "default",
              "contract": "git-tools.contract.default.app-health",
              "check": "required-regions-visible",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "#git-tools-app",
                ".git-tools-shell",
                "#git-project-selector-panel",
                "#git-project-workflow-surface"
              ],
              "expects": [
                "Root, shell, project selector, and workflow surface remain visible."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "git-tools.region.root",
                  "selector": "#git-tools-app",
                  "label": "Git Tools app root"
                },
                {
                  "id": "git-tools.region.shell",
                  "selector": ".git-tools-shell",
                  "label": "Git Tools shell"
                },
                {
                  "id": "git-tools.region.project-selector",
                  "selector": "#git-project-selector-panel",
                  "label": "Project selector"
                },
                {
                  "id": "git-tools.region.workflow",
                  "selector": "#git-project-workflow-surface",
                  "label": "Project workflow surface"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Git Tools default mode must preserve project selection and workflow.",
              "next_probe": "layout.baseline",
              "source_binding": "git-tools.binding.project-workflow",
              "test_binding": "git-tools.test.semantic-adapter",
              "source": {
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1117,
                "end_line": 1141
              }
            }
          ]
        }
      }
    },
    "mcel-lab": {
      "app": "mcel-lab",
      "mode_contracts": {
        "default": {
          "contractId": "mcel-lab.contract.default.blueprint-studio-health",
          "appId": "mcel-lab",
          "mode": "default",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "mcel-lab.form.work-surface.blueprint-inspection",
            "label": "Selected app/aspect work surface is missing or unusable.",
            "hostSelector": ".mcel-lab-blueprint-primary",
            "editorSelector": "#mcel-blueprint-work-surface",
            "minWidth": 640,
            "minHeight": 420
          },
          "requiredRegions": [
            {
              "id": "mcel-lab.region.app-root",
              "selector": "#mcel-lab-app",
              "label": "Lab app root"
            },
            {
              "id": "mcel-lab.region.selection-context",
              "selector": "#mcel-blueprint-app-select",
              "label": "App selection context"
            },
            {
              "id": "mcel-lab.region.selection-context",
              "selector": "#mcel-blueprint-aspect-select",
              "label": "Aspect selection context"
            },
            {
              "id": "mcel-lab.region.aspect-map",
              "selector": ".mcel-lab-blueprint-navigation",
              "label": "Aspect map projection"
            },
            {
              "id": "mcel-lab.region.blueprint-workspace",
              "selector": ".mcel-lab-blueprint-primary",
              "label": "Blueprint inspection workspace"
            },
            {
              "id": "mcel-lab.region.feedback-and-findings",
              "selector": "#mcel-blueprint-work-badge",
              "label": "Mount and validation feedback"
            }
          ],
          "optionalRegions": [],
          "allowedRegions": [],
          "forbiddenRegions": [],
          "lifecycleAssertions": [
            "self-hosting-draft-does-not-apply-itself",
            "repair-export-remains-reviewable-before-patch-workflow"
          ],
          "geometryPolicies": [
            "semantic-form-projections-must-not-obscure-blueprint-workspace",
            "owned-semantic-projections-must-not-overlap",
            "readable-text-must-remain-inside-owning-surface",
            "scroll-containers-must-contain-child-content",
            "primary-work-surface-must-not-be-occluded-by-context-or-feedback"
          ],
          "overlayPolicy": [
            "point-inspection-transient-is-mode-bound"
          ],
          "checkCategories": [
            "surface",
            "form",
            "contract",
            "layout"
          ],
          "focusModes": [
            "blueprint-workspace",
            "semantic-projections",
            "self-hosting-safety",
            "semantic-projection-readability"
          ],
          "checks": [
            {
              "id": "mcel-lab.runtime.primary-blueprint-workspace",
              "app": "mcel-lab",
              "status": "specified",
              "mode": "default",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "check": "primary-surface",
              "check_category": "surface",
              "focus": "blueprint-workspace",
              "severity": "critical",
              "observes": [
                "mcel-lab.form.work-surface.blueprint-inspection"
              ],
              "expects": [
                "Selected AppBlueprint workspace is visible and usable."
              ],
              "forbids": [],
              "primary_surface_id": "mcel-lab.form.work-surface.blueprint-inspection",
              "host_selector": ".mcel-lab-blueprint-primary",
              "editor_selector": "#mcel-blueprint-work-surface",
              "min_width": "640",
              "min_height": "420",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Selected app/aspect work surface is missing or unusable.",
              "next_probe": "lab.form.detector",
              "source_binding": "",
              "test_binding": "",
              "source": {
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 580,
                "end_line": 601
              }
            },
            {
              "id": "mcel-lab.runtime.required-semantic-projections",
              "app": "mcel-lab",
              "status": "specified",
              "mode": "default",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "check": "required-regions-visible",
              "check_category": "form",
              "focus": "semantic-projections",
              "severity": "error",
              "observes": [
                "mcel-lab.form.subject.app-blueprint",
                "mcel-lab.form.context.app-and-aspect-selection",
                "mcel-lab.form.feedback.validation-and-mount-state"
              ],
              "expects": [
                "App root, selection context, aspect map, primary blueprint workspace, and owned feedback are present."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "mcel-lab.region.app-root",
                  "selector": "#mcel-lab-app",
                  "label": "Lab app root"
                },
                {
                  "id": "mcel-lab.region.selection-context",
                  "selector": "#mcel-blueprint-app-select",
                  "label": "App selection context"
                },
                {
                  "id": "mcel-lab.region.selection-context",
                  "selector": "#mcel-blueprint-aspect-select",
                  "label": "Aspect selection context"
                },
                {
                  "id": "mcel-lab.region.aspect-map",
                  "selector": ".mcel-lab-blueprint-navigation",
                  "label": "Aspect map projection"
                },
                {
                  "id": "mcel-lab.region.blueprint-workspace",
                  "selector": ".mcel-lab-blueprint-primary",
                  "label": "Blueprint inspection workspace"
                },
                {
                  "id": "mcel-lab.region.feedback-and-findings",
                  "selector": "#mcel-blueprint-work-badge",
                  "label": "Mount and validation feedback"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "MCEL Lab semantic form projections are missing from the rendered workbench.",
              "next_probe": "lab.form.detector",
              "source_binding": "",
              "test_binding": "",
              "source": {
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 603,
                "end_line": 628
              }
            },
            {
              "id": "mcel-lab.runtime.self-hosting-safety-boundary",
              "app": "mcel-lab",
              "status": "specified",
              "mode": "default",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "check": "lifecycle-contract-preserved",
              "check_category": "contract",
              "focus": "self-hosting-safety",
              "severity": "warning",
              "observes": [
                "mcel-lab.form.constraint.self-hosting-safety",
                "mcel-lab.form.interruption.unsafe-repair-boundary"
              ],
              "expects": [
                "Self-hosting inspection can create draft annotations or export context but cannot directly rewrite live Lab implementation files."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [
                "self-hosting-draft-does-not-apply-itself",
                "repair-export-remains-reviewable-before-patch-workflow"
              ],
              "geometry_policies": [
                "semantic-form-projections-must-not-obscure-blueprint-workspace"
              ],
              "overlay_policy": [
                "point-inspection-transient-is-mode-bound"
              ],
              "ownership_hints": [],
              "failure_message": "MCEL Lab self-hosting safety boundary is not observable.",
              "next_probe": "lab.self-hosting.boundary",
              "source_binding": "",
              "test_binding": "",
              "source": {
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 659,
                "end_line": 683
              }
            },
            {
              "id": "mcel-lab.runtime.visual-integrity-baseline",
              "app": "mcel-lab",
              "status": "specified",
              "mode": "default",
              "contract": "mcel-lab.contract.default.blueprint-studio-health",
              "check": "visual-integrity-baseline",
              "check_category": "layout",
              "focus": "semantic-projection-readability",
              "severity": "critical",
              "observes": [
                "mcel-lab.form.work-surface.blueprint-inspection",
                "mcel-lab.form.context.app-and-aspect-selection",
                "mcel-lab.form.context.rendered-element-evidence",
                "mcel-lab.form.feedback.validation-and-mount-state"
              ],
              "expects": [
                "Every rendered semantic projection owns its visible text, controls, and child surfaces.",
                "Readable content must not paint across neighboring semantic surfaces.",
                "Stacked cards, buttons, summaries, feedback rows, and evidence panels must not overlap each other.",
                "Scroll containers must contain overflow instead of letting content visually overwrite nearby regions."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [
                "owned-semantic-projections-must-not-overlap",
                "readable-text-must-remain-inside-owning-surface",
                "scroll-containers-must-contain-child-content",
                "primary-work-surface-must-not-be-occluded-by-context-or-feedback"
              ],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "MCEL Lab has a visual-integrity failure: semantic projections collide, bleed, clip, or overwrite readable content.",
              "next_probe": "layout.visualIntegrityProbe",
              "source_binding": "",
              "test_binding": "",
              "source": {
                "file": "pretty_docs/mcel-lab-blueprint-studio.md",
                "start_line": 630,
                "end_line": 657
              }
            }
          ]
        }
      }
    },
    "website-builder": {
      "app": "website-builder",
      "mode_contracts": {
        "default": {
          "contractId": "website-builder.contract.default.app-health",
          "appId": "website-builder",
          "mode": "default",
          "source": "mcel-runtime-check",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
          ],
          "primarySurface": {
            "id": "website-builder.surface.preview",
            "label": "Website Builder default mode must expose a usable preview/design surface.",
            "hostSelector": "[data-mcel-surface-id='website-builder.surface.preview']",
            "editorSelector": "[data-mcel-surface-id='website-builder.surface.preview']",
            "minWidth": 420,
            "minHeight": 320
          },
          "requiredRegions": [
            {
              "id": "website-builder.region.root",
              "selector": "#website-builder-app",
              "label": "Website Builder app root"
            },
            {
              "id": "website-builder.region.main",
              "selector": ".website-builder-main",
              "label": "Website Builder shell"
            },
            {
              "id": "website-builder.region.summary",
              "selector": ".website-builder-summary",
              "label": "Website summary"
            },
            {
              "id": "website-builder.region.preview",
              "selector": "[data-mcel-surface-id='website-builder.surface.preview']",
              "label": "Preview/design surface"
            },
            {
              "id": "website-builder.region.inspector",
              "selector": ".website-builder-inspector",
              "label": "Inspector"
            }
          ],
          "optionalRegions": [],
          "allowedRegions": [],
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "geometryPolicies": [],
          "overlayPolicy": [],
          "checkCategories": [],
          "focusModes": [],
          "checks": [
            {
              "id": "website-builder.runtime-check.default-overlay-policy",
              "app": "website-builder",
              "status": "specified",
              "mode": "default",
              "contract": "website-builder.contract.default.app-health",
              "check": "overlay-policy",
              "check_category": "",
              "focus": "",
              "severity": "warning",
              "observes": [
                "#mc-widget-editor-root",
                "[data-mcel-proof-surface]",
                ".floating-tab",
                ".side-tab"
              ],
              "expects": [
                "MCEL/widget/proof overlays are not visible while using the default builder surface."
              ],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Website Builder default mode should not be covered by diagnostic overlays.",
              "next_probe": "overlay.detector",
              "source_binding": "website-builder.binding.builder-runtime",
              "test_binding": "website-builder.test.documentation-contract",
              "source": {
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1196,
                "end_line": 1219
              }
            },
            {
              "id": "website-builder.runtime-check.default-primary-preview",
              "app": "website-builder",
              "status": "specified",
              "mode": "default",
              "contract": "website-builder.contract.default.app-health",
              "check": "primary-surface",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "[data-mcel-surface-id='website-builder.surface.preview']"
              ],
              "expects": [
                "Website Builder preview/design surface is visible and usable.",
                "The selected site surface is not collapsed by inspector or publishing panels."
              ],
              "forbids": [],
              "primary_surface_id": "website-builder.surface.preview",
              "host_selector": "[data-mcel-surface-id='website-builder.surface.preview']",
              "editor_selector": "[data-mcel-surface-id='website-builder.surface.preview']",
              "min_width": "420",
              "min_height": "320",
              "required_regions": [],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Website Builder default mode must expose a usable preview/design surface.",
              "next_probe": "layout.ownerProbe",
              "source_binding": "website-builder.binding.builder-runtime",
              "test_binding": "website-builder.test.documentation-contract",
              "source": {
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1144,
                "end_line": 1166
              }
            },
            {
              "id": "website-builder.runtime-check.default-required-regions",
              "app": "website-builder",
              "status": "specified",
              "mode": "default",
              "contract": "website-builder.contract.default.app-health",
              "check": "required-regions-visible",
              "check_category": "",
              "focus": "",
              "severity": "critical",
              "observes": [
                "#website-builder-app",
                ".website-builder-main",
                ".website-builder-summary",
                "[data-mcel-surface-id='website-builder.surface.preview']",
                ".website-builder-inspector"
              ],
              "expects": [
                "Root, shell, summary, preview, and inspector remain visible."
              ],
              "forbids": [],
              "primary_surface_id": "",
              "host_selector": "",
              "editor_selector": "",
              "min_width": "",
              "min_height": "",
              "required_regions": [
                {
                  "id": "website-builder.region.root",
                  "selector": "#website-builder-app",
                  "label": "Website Builder app root"
                },
                {
                  "id": "website-builder.region.main",
                  "selector": ".website-builder-main",
                  "label": "Website Builder shell"
                },
                {
                  "id": "website-builder.region.summary",
                  "selector": ".website-builder-summary",
                  "label": "Website summary"
                },
                {
                  "id": "website-builder.region.preview",
                  "selector": "[data-mcel-surface-id='website-builder.surface.preview']",
                  "label": "Preview/design surface"
                },
                {
                  "id": "website-builder.region.inspector",
                  "selector": ".website-builder-inspector",
                  "label": "Inspector"
                }
              ],
              "optional_regions": [],
              "allowed_regions": [],
              "forbidden_regions": [],
              "lifecycle_assertions": [],
              "geometry_policies": [],
              "overlay_policy": [],
              "ownership_hints": [],
              "failure_message": "Website Builder default mode must preserve summary, preview, and inspector.",
              "next_probe": "layout.baseline",
              "source_binding": "website-builder.binding.builder-runtime",
              "test_binding": "website-builder.test.documentation-contract",
              "source": {
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1168,
                "end_line": 1194
              }
            }
          ]
        }
      }
    }
  },
  "app_comparison_seeds": {
    "calculator": {
      "app": "calculator",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "required_use_case_count": 1,
      "required_region_count": 11,
      "declared_form_primitive_count": 6,
      "required_intent_count": 11,
      "mutation_intent_count": 1,
      "prohibited_intent_count": 0,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    },
    "code-editor": {
      "app": "code-editor",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "fullApplicationSemanticReady",
      "required_use_case_count": 2,
      "required_region_count": 7,
      "declared_form_primitive_count": 7,
      "required_intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    },
    "file-explorer": {
      "app": "file-explorer",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "full-bounded-read-only-semantic-runtime",
      "target_runtime_status": "full-read-only-semantic-runtime",
      "required_use_case_count": 2,
      "required_region_count": 7,
      "declared_form_primitive_count": 6,
      "required_intent_count": 11,
      "mutation_intent_count": 3,
      "prohibited_intent_count": 3,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    },
    "git-tools": {
      "app": "git-tools",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "runtime-baseline-with-ignore-preview-semantic-adapter",
      "target_runtime_status": "full-application-semantic-runtime",
      "required_use_case_count": 4,
      "required_region_count": 8,
      "declared_form_primitive_count": 6,
      "required_intent_count": 10,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 1,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    },
    "mcel-lab": {
      "app": "mcel-lab",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "scope-limited-semantic-runtime",
      "target_runtime_status": "scope-limited-semantic-runtime",
      "required_use_case_count": 2,
      "required_region_count": 7,
      "declared_form_primitive_count": 9,
      "required_intent_count": 7,
      "mutation_intent_count": 4,
      "prohibited_intent_count": 0,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    },
    "website-builder": {
      "app": "website-builder",
      "requirements_contract_present": true,
      "requirements_contract_complete": true,
      "current_runtime_status": "fullApplicationSemanticReady",
      "target_runtime_status": "full-application-semantic-runtime",
      "required_use_case_count": 4,
      "required_region_count": 10,
      "declared_form_primitive_count": 6,
      "required_intent_count": 12,
      "mutation_intent_count": 8,
      "prohibited_intent_count": 0,
      "runtime_comparison_status": "pending-live-adapter-snapshot"
    }
  }
});

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
