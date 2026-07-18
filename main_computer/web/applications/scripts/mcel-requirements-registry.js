(() => {
  (function createMcelRequirementsRegistry(global) {
    if (!global) return;

    const PAYLOAD = Object.freeze({
  "app_comparison_seeds": {
    "calculator": {
      "app": "calculator",
      "current_runtime_status": "domain-ready-planner-plus-domain-pack",
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
    "website-builder": {
      "app": "website-builder",
      "current_runtime_status": "working-app-plus-site-project-model",
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
      "id": "calculator",
      "intent_count": 10,
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 9
      },
      "mutation_intent_count": 1,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
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
        "specified": 35
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
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 7,
        "mcel-runtime-check": 4,
        "mcel-source-binding": 1,
        "mcel-test-binding": 1,
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
          "responsibility": "Own active source editing, draft review, concrete diffs, and explicit runtime preview while preventing unreviewed writes.",
          "role": "primary-authoring-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.inspector",
          "region": "inspector",
          "responsibility": "Show Aider context, selected-file evidence, SCM manifests, documentation references, and action-specific preflight information.",
          "role": "context-inspector",
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
      "planned_or_open_count": 33,
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 4,
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
        "specified": 23
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
      "planned_or_open_count": 35,
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
        "specified": 27
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
      "planned_or_open_count": 38,
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
        "specified": 22
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
      "planned_or_open_count": 45,
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
        "specified": 35
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
      "id": "calculator",
      "intent_count": 10,
      "intent_risk_counts": {
        "local-state": 1,
        "read-only": 9
      },
      "mutation_intent_count": 1,
      "open_finding_count": 3,
      "planned_or_open_count": 41,
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
        "specified": 35
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
        "mcel-intent": 7,
        "mcel-region": 7,
        "mcel-requirement": 7,
        "mcel-runtime-check": 4,
        "mcel-source-binding": 1,
        "mcel-test-binding": 1,
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
          "responsibility": "Own active source editing, draft review, concrete diffs, and explicit runtime preview while preventing unreviewed writes.",
          "role": "primary-authoring-surface",
          "status": "specified"
        },
        {
          "id": "code-editor.region.inspector",
          "region": "inspector",
          "responsibility": "Show Aider context, selected-file evidence, SCM manifests, documentation references, and action-specific preflight information.",
          "role": "context-inspector",
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
      "planned_or_open_count": 33,
      "primary_user_goal": "Inspect, edit, preview, and safely change project source with AI assistance while preserving explicit write, patch, execution, and remote-mutation boundaries.",
      "prohibited_intent_count": 0,
      "region_count": 7,
      "runtime_check_count": 4,
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
        "specified": 23
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
      "planned_or_open_count": 35,
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
        "specified": 27
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
      "planned_or_open_count": 38,
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
        "specified": 22
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
      "adapter_status_counts": {
        "current_adapter_status:not-registered": 12,
        "target_adapter_status:executable": 12
      },
      "app": "website-builder",
      "block_type_counts": {
        "mcel-acceptance": 5,
        "mcel-app": 1,
        "mcel-finding": 4,
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
      "planned_or_open_count": 45,
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
        "specified": 35
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
          "appId": "calculator",
          "checks": [
            {
              "app": "calculator",
              "check": "overlay-policy",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while the calculator is in default mode."
              ],
              "failure_message": "Calculator default mode should not be covered by diagnostic overlays.",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
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
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 967,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 944
              },
              "source_binding": "calculator.binding.route-and-ui",
              "status": "specified",
              "test_binding": "calculator.test.route-checks"
            },
            {
              "app": "calculator",
              "check": "primary-surface",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": ".calculator-workspace",
              "expects": [
                "Calculator workspace is visible and large enough for the active mode.",
                "The primary calculator surface is not collapsed by surrounding app chrome."
              ],
              "failure_message": "Calculator default mode must expose a usable workspace.",
              "forbidden_regions": [],
              "forbids": [],
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
              "primary_surface_id": "calculator.surface.workspace",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 912,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 890
              },
              "source_binding": "calculator.binding.route-and-ui",
              "status": "specified",
              "test_binding": "calculator.test.route-checks"
            },
            {
              "app": "calculator",
              "check": "required-regions-visible",
              "contract": "calculator.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Calculator app root is visible.",
                "Mode switch remains visible.",
                "Calculator workspace and display remain visible."
              ],
              "failure_message": "Calculator default mode must preserve root, controls, workspace, and display.",
              "forbidden_regions": [],
              "forbids": [],
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
                "end_line": 942,
                "file": "pretty_docs/mcel-calculator-requirements.md",
                "start_line": 914
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
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "mode": "default",
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
          "appId": "code-editor",
          "checks": [
            {
              "app": "code-editor",
              "check": "forbidden-surfaces-hidden",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Source model pane is hidden.",
                "Serialized and contract panes are hidden.",
                "Generated runtime window/layout/file rail are absent from the default path.",
                "Fallback textarea is not visible in the Monaco golden path.",
                "Proof and widget overlays are not visible in authoring mode."
              ],
              "failure_message": "MCEL diagnostic/runtime scaffolding must not leak into Code Editor authoring mode.",
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
                "code-editor.forbidden.widget-overlay | #mc-widget-editor-root | Widget editor overlay"
              ],
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
                "#mc-widget-editor-root"
              ],
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 704,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 664
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "app": "code-editor",
              "check": "lifecycle-contract-preserved",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Startup authoring mode has exactly one primary Monaco editor.",
                "Clicking another file keeps exactly one primary Monaco editor.",
                "Resize keeps the Monaco host and editor useful."
              ],
              "failure_message": "File selection and reload must preserve the Code Editor authoring contract.",
              "forbidden_regions": [],
              "forbids": [],
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
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 731,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 706
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "app": "code-editor",
              "check": "primary-surface",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": ".monaco-editor",
              "expects": [
                "Monaco host is visible and at least 800px wide by 600px tall.",
                "Monaco editor instance is visible and at least 800px wide by 600px tall.",
                "No fallback or source-model editor surface competes with Monaco in authoring mode."
              ],
              "failure_message": "Authoring mode must expose one usable Monaco selected-file editor.",
              "forbidden_regions": [],
              "forbids": [],
              "host_selector": "#code-studio-runtime-monaco",
              "id": "code-editor.runtime-check.authoring-primary-monaco",
              "lifecycle_assertions": [],
              "min_height": "600",
              "min_width": "800",
              "mode": "authoring",
              "next_probe": "layout.ownerProbe",
              "observes": [
                "#code-studio-runtime-monaco",
                ".monaco-editor"
              ],
              "primary_surface_id": "code-editor.surface.monaco-selected-file-editor",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 636,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 612
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            },
            {
              "app": "code-editor",
              "check": "required-regions-visible",
              "contract": "code-editor.contract.authoring.monaco-golden-path",
              "editor_selector": "",
              "expects": [
                "Code Editor root is present and visible.",
                "Explorer region is present and visible.",
                "Editor group is present and visible."
              ],
              "failure_message": "Authoring mode must preserve the app root, explorer, and editor group.",
              "forbidden_regions": [],
              "forbids": [],
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
                ".code-studio-editor-group"
              ],
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
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 662,
                "file": "pretty_docs/mcel-code-editor-requirements.md",
                "start_line": 638
              },
              "source_binding": "code-editor.binding.authoring-monaco-surface",
              "status": "specified",
              "test_binding": "code-editor.test.authoring-monaco-diagnosis"
            }
          ],
          "contractId": "code-editor.contract.authoring.monaco-golden-path",
          "derivedFromBlockTypes": [
            "mcel-runtime-check"
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
              "label": "Widget editor overlay",
              "selector": "#mc-widget-editor-root"
            }
          ],
          "lifecycleAssertions": [
            "startup-authoring-mode-has-one-primary-editor",
            "file-click-keeps-one-primary-editor",
            "resize-keeps-primary-editor-usable",
            "mcel-diagnostics-hidden-in-authoring"
          ],
          "mode": "authoring",
          "primarySurface": {
            "editorSelector": ".monaco-editor",
            "hostSelector": "#code-studio-runtime-monaco",
            "id": "code-editor.surface.monaco-selected-file-editor",
            "label": "Authoring mode must expose one usable Monaco selected-file editor.",
            "minHeight": 600,
            "minWidth": 800
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
          "appId": "file-explorer",
          "checks": [
            {
              "app": "file-explorer",
              "check": "overlay-policy",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while browsing files."
              ],
              "failure_message": "File Explorer should not be covered by diagnostic overlays in default mode.",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
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
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 955,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 932
              },
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "status": "specified",
              "test_binding": "file-explorer.test.viewport-file-explorer"
            },
            {
              "app": "file-explorer",
              "check": "primary-surface",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": ".file-explorer-main",
              "expects": [
                "File Explorer main browsing surface is visible and usable.",
                "The list/preview work area is not collapsed."
              ],
              "failure_message": "File Explorer default mode must expose a usable browsing surface.",
              "forbidden_regions": [],
              "forbids": [],
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
              "primary_surface_id": "file-explorer.surface.main",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 902,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 880
              },
              "source_binding": "file-explorer.binding.viewport-file-explorer",
              "status": "specified",
              "test_binding": "file-explorer.test.viewport-file-explorer"
            },
            {
              "app": "file-explorer",
              "check": "required-regions-visible",
              "contract": "file-explorer.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, roots panel, toolbar, main surface, and file list are visible."
              ],
              "failure_message": "File Explorer default mode must preserve roots, toolbar, and list.",
              "forbidden_regions": [],
              "forbids": [],
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
                "end_line": 930,
                "file": "pretty_docs/mcel-file-explorer-requirements.md",
                "start_line": 904
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
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "mode": "default",
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
          "appId": "git-tools",
          "checks": [
            {
              "app": "git-tools",
              "check": "overlay-policy",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while running the default Git Tools workflow."
              ],
              "failure_message": "Git Tools default mode should not be covered by diagnostic overlays.",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
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
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1037,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 1014
              },
              "source_binding": "git-tools.binding.project-workflow",
              "status": "specified",
              "test_binding": "git-tools.test.semantic-adapter"
            },
            {
              "app": "git-tools",
              "check": "primary-surface",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "#git-project-workflow-surface",
              "expects": [
                "Git Tools project workflow surface is visible and usable.",
                "The workflow surface is not collapsed by rails or proof panels."
              ],
              "failure_message": "Git Tools default mode must expose a usable workflow surface.",
              "forbidden_regions": [],
              "forbids": [],
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
              "primary_surface_id": "git-tools.surface.workflow",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 986,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 964
              },
              "source_binding": "git-tools.binding.project-workflow",
              "status": "specified",
              "test_binding": "git-tools.test.semantic-adapter"
            },
            {
              "app": "git-tools",
              "check": "required-regions-visible",
              "contract": "git-tools.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, shell, project selector, and workflow surface remain visible."
              ],
              "failure_message": "Git Tools default mode must preserve project selection and workflow.",
              "forbidden_regions": [],
              "forbids": [],
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
                "end_line": 1012,
                "file": "pretty_docs/mcel-git-tools-requirements.md",
                "start_line": 988
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
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "mode": "default",
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
    "website-builder": {
      "app": "website-builder",
      "mode_contracts": {
        "default": {
          "appId": "website-builder",
          "checks": [
            {
              "app": "website-builder",
              "check": "overlay-policy",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "MCEL/widget/proof overlays are not visible while using the default builder surface."
              ],
              "failure_message": "Website Builder default mode should not be covered by diagnostic overlays.",
              "forbidden_regions": [],
              "forbids": [
                "shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay",
                "shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface",
                "shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab"
              ],
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
              "primary_surface_id": "",
              "required_regions": [],
              "severity": "warning",
              "source": {
                "end_line": 1114,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1091
              },
              "source_binding": "website-builder.binding.builder-runtime",
              "status": "specified",
              "test_binding": "website-builder.test.documentation-contract"
            },
            {
              "app": "website-builder",
              "check": "primary-surface",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": ".website-builder-preview",
              "expects": [
                "Website Builder preview/design surface is visible and usable.",
                "The selected site surface is not collapsed by inspector or publishing panels."
              ],
              "failure_message": "Website Builder default mode must expose a usable preview/design surface.",
              "forbidden_regions": [],
              "forbids": [],
              "host_selector": ".website-builder-preview",
              "id": "website-builder.runtime-check.default-primary-preview",
              "lifecycle_assertions": [],
              "min_height": "320",
              "min_width": "420",
              "mode": "default",
              "next_probe": "layout.ownerProbe",
              "observes": [
                ".website-builder-preview"
              ],
              "primary_surface_id": "website-builder.surface.preview",
              "required_regions": [],
              "severity": "critical",
              "source": {
                "end_line": 1061,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1039
              },
              "source_binding": "website-builder.binding.builder-runtime",
              "status": "specified",
              "test_binding": "website-builder.test.documentation-contract"
            },
            {
              "app": "website-builder",
              "check": "required-regions-visible",
              "contract": "website-builder.contract.default.app-health",
              "editor_selector": "",
              "expects": [
                "Root, shell, summary, preview, and inspector remain visible."
              ],
              "failure_message": "Website Builder default mode must preserve summary, preview, and inspector.",
              "forbidden_regions": [],
              "forbids": [],
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
                ".website-builder-preview",
                ".website-builder-inspector"
              ],
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
                  "selector": ".website-builder-preview"
                },
                {
                  "id": "website-builder.region.inspector",
                  "label": "Inspector",
                  "selector": ".website-builder-inspector"
                }
              ],
              "severity": "critical",
              "source": {
                "end_line": 1089,
                "file": "pretty_docs/mcel-website-builder-requirements.md",
                "start_line": 1063
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
          "forbiddenRegions": [],
          "lifecycleAssertions": [],
          "mode": "default",
          "primarySurface": {
            "editorSelector": ".website-builder-preview",
            "hostSelector": ".website-builder-preview",
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
              "selector": ".website-builder-preview"
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
      "website-builder"
    ],
    "app_counts": {
      "calculator": 41,
      "code-editor": 33,
      "file-explorer": 38,
      "git-tools": 45,
      "website-builder": 48
    },
    "block_type_counts": {
      "mcel-acceptance": 17,
      "mcel-app": 5,
      "mcel-finding": 17,
      "mcel-grammar": 17,
      "mcel-intent": 50,
      "mcel-region": 43,
      "mcel-requirement": 47,
      "mcel-runtime-check": 16,
      "mcel-source-binding": 1,
      "mcel-test-binding": 1,
      "mcel-use-case": 13
    },
    "error_count": 0,
    "pretty_docs_root": "pretty_docs",
    "registry_version": "mcel-requirements-registry-v1",
    "repo_root": "/mnt/data/work_phase2_diag_norm/main_computer_test",
    "strict_schema_ready": true,
    "total_blocks": 227,
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
