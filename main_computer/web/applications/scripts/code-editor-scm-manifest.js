var McelCodeStudioScm = (() => {
      const COMPONENT_NAME = "CodeStudio";
      const COMPONENT_VERSION = "2.6.0";
      const COMPONENT_CONTRACT = "mcel.scm.code-studio.v1";
      const ROUTE_NAME = "workspace.file";
      const ROUTE_VERSION = "1.1.0";
      const ROUTE_CONTRACT = "mcel.scm.route.workspace-file.v1";

      function cloneValue(value) {
        if (!value || typeof value !== "object") return value;
        if (Array.isArray(value)) return value.map((item) => cloneValue(item));
        const copy = {};
        Object.keys(value).forEach((key) => {
          copy[key] = cloneValue(value[key]);
        });
        return copy;
      }

      function defaultSource() {
        return {
          workspace: {
            manifest: {
              id: "workspace-main",
              title: "MCEL Code Studio",
              summary: "Source-safe code editor example composed as an SCM component."
            },
            files: [
              {
                id: "src-app",
                path: "src/app.js",
                language: "javascript",
                text: "console.log('hello from MCEL Code Studio');"
              },
              {
                id: "test-app",
                path: "tests/app.test.js",
                language: "javascript",
                text: "test('studio loads', () => expect(true).toBe(true));"
              },
              {
                id: "mcel-contract",
                path: "mcel.contract.json",
                language: "json",
                text: "{\n  \"contract\": \"mcel.scm.code-studio.v1\"\n}"
              }
            ]
          }
        };
      }

      function defaultRuntime() {
        return {
          workbench: {
            shell: {
              mounted: false,
              damaged: false
            }
          },
          editor: {
            chrome: {
              generated: true,
              serialize: "omit"
            }
          },
          loadedFile: null,
          serializedOutput: "",
          validationReport: null,
          assistantSession: null
        };
      }

      function defaultState() {
        return {
          activeFileId: "src-app",
          openTabs: ["src-app"],
          drafts: {},
          selectedPanel: "source",
          sidebarMode: "explorer",
          bottomDockExpanded: false,
          layoutLock: true,
          dirty: false
        };
      }

      function findFileById(files, fileId) {
        return Array.isArray(files) ? files.find((file) => file && file.id === fileId) || null : null;
      }

      const componentManifest = {
        version: COMPONENT_VERSION,
        contract: COMPONENT_CONTRACT,

        owns: {
          source: [
            "workspace.manifest",
            "workspace.files"
          ],

          runtime: [
            "workbench.shell",
            "editor.chrome",
            "loadedFile",
            "serializedOutput",
            "validationReport",
            "assistantSession"
          ],

          state: [
            "activeFileId",
            "openTabs",
            "drafts",
            "selectedPanel",
            "sidebarMode",
            "bottomDockExpanded",
            "layoutLock",
            "dirty"
          ],

          layout: [
            "activitybar",
            "sidebar",
            "editorGroup",
            "inspector",
            "bottomDock",
            "statusbar"
          ],

          style: [
            "codeStudioTheme"
          ],

          effects: [
            "loadWorkspace",
            "loadFile",
            "saveFile",
            "runValidation"
          ]
        },

        source: defaultSource(),
        runtime: defaultRuntime(),
        state: defaultState(),

        outputs: [
          "fileOpened",
          "draftEdited",
          "draftCommitted",
          "panelSelected",
          "dockToggled",
          "workspaceSerialized"
        ],

        children: {
          activitybar: {
            component: "ActivityBar",
            slot: "activitybar",
            inputs: {
              selectedPanel: "state.selectedPanel",
              sidebarMode: "state.sidebarMode"
            },
            outputs: {
              selectPanel: "transition.selectPanel"
            },
            mayMutate: [],
            maySerialize: false
          },

          explorer: {
            component: "FileExplorer",
            slot: "sidebar",
            inputs: {
              files: "source.workspace.files",
              activeFileId: "state.activeFileId",
              openTabs: "state.openTabs"
            },
            outputs: {
              openFile: "transition.openFile"
            },
            mayMutate: [],
            maySerialize: false
          },

          editor: {
            component: "SourceEditor",
            slot: "editorGroup",
            inputs: {
              activeFileId: "state.activeFileId",
              drafts: "state.drafts",
              files: "source.workspace.files"
            },
            outputs: {
              editDraft: "transition.editDraft",
              commitDraft: "transition.commitDraft"
            },
            mayMutate: [
              "state.drafts"
            ],
            mayMutateSource: "only-through-commit",
            maySerialize: false
          },

          inspector: {
            component: "ContractInspector",
            slot: "inspector",
            inputs: {
              validationReport: "runtime.validationReport",
              loadedFile: "runtime.loadedFile"
            },
            outputs: {},
            mayMutate: [],
            maySerialize: false
          },

          bottomDock: {
            component: "AssistantDock",
            slot: "bottomDock",
            inputs: {
              expanded: "state.bottomDockExpanded",
              activeFileId: "state.activeFileId"
            },
            outputs: {
              toggleDock: "transition.toggleBottomDock"
            },
            mayMutate: [
              "runtime.assistantSession"
            ],
            maySerialize: false
          }
        },

        layoutContract: {
          root: "#code-editor-app",
          failClosed: true,
          maxDocumentHeightRatio: 1.6,

          requiredComputed: {
            ".code-studio-shell": {
              display: "grid",
              overflow: "hidden"
            },

            ".code-studio-body": {
              display: "grid"
            }
          },

          regions: {
            activitybar: {
              selector: ".code-studio-activitybar",
              slot: "activitybar",
              required: true
            },

            sidebar: {
              selector: ".code-studio-sidebar",
              slot: "sidebar",
              required: true
            },

            editorGroup: {
              selector: ".code-studio-editor-group",
              slot: "editorGroup",
              required: true
            },

            inspector: {
              selector: ".code-studio-inspector",
              slot: "inspector",
              required: true
            },

            bottomDock: {
              selector: "#code-studio-bottom-panel",
              slot: "bottomDock",
              required: true
            },

            statusbar: {
              selector: ".code-studio-statusbar",
              slot: "statusbar",
              required: true
            }
          },

          states: {
            bottomDockCollapsed: {
              when: "state.bottomDockExpanded === false",
              selector: "#code-studio-bottom-panel",
              maxHeight: 80
            }
          }
        },

        styleContract: {
          scope: "sealed",
          owns: [
            "codeStudioTheme"
          ],
          forbidsGlobalLeakage: true,

          expectedComputed: {
            "#code-editor-app": {
              backgroundColor: "rgb(30, 30, 30)"
            },

            ".code-studio-body": {
              display: "grid"
            },

            ".code-studio-titlebar button": {
              backgroundColor: "rgb(45, 45, 48)",
              color: "rgb(220, 220, 220)"
            }
          },

          forbiddenComputed: {
            "button": {
              backgroundColor: "rgb(246, 199, 91)"
            }
          }
        },

        serializationContract: {
          sourceOwns: [
            "source.workspace.manifest",
            "source.workspace.files"
          ],

          runtimeOnly: [
            "runtime.workbench.shell",
            "runtime.editor.chrome",
            "runtime.loadedFile",
            "runtime.serializedOutput",
            "runtime.validationReport",
            "runtime.assistantSession"
          ],

          commitRequiredFor: [
            "source.workspace.manifest",
            "source.workspace.files"
          ],

          dirtyState: {
            blockedBy: [
              "state.dirty",
              "state.drafts"
            ]
          },

          failIfRuntimeLeaks: true,

          runtimeLeakMarkers: [
            "data-mc-runtime",
            "data-mc-generated",
            "code-studio-shell",
            "code-studio-titlebar",
            "code-studio-bottom-panel"
          ],

          output: {
            format: "clean-source-json",
            includeDebug: false,
            includeRuntime: false,
            includeEditorChrome: false,
            writeTo: "runtime.serializedOutput"
          }
        },

        repairContract: {
          allowed: [
            "runtime.workbench.shell",
            "runtime.editor.chrome",
            "runtime.validationReport"
          ],

          forbidden: [
            "source.workspace.manifest",
            "source.workspace.files",
            "state.activeFileId",
            "state.openTabs",
            "state.drafts",
            "state.dirty"
          ],

          strategies: {
            rebuildWorkbenchShell: {
              reads: [
                "source.workspace.manifest",
                "source.workspace.files",
                "state.openTabs",
                "state.activeFileId",
                "runtime.workbench.shell",
                "runtime.editor.chrome"
              ],

              writes: [
                "runtime.workbench.shell",
                "runtime.editor.chrome",
                "runtime.validationReport"
              ],

              apply(ctx) {
                const manifest = ctx.get("source.workspace.manifest");
                const files = ctx.get("source.workspace.files");
                const openTabs = ctx.get("state.openTabs");
                const activeFileId = ctx.get("state.activeFileId");
                ctx.set("runtime.workbench.shell", {
                  mounted: true,
                  damaged: false,
                  rebuilt: true,
                  title: manifest?.title || "MCEL Code Studio",
                  fileCount: Array.isArray(files) ? files.length : 0,
                  openTabs: Array.isArray(openTabs) ? openTabs.slice() : [],
                  activeFileId: activeFileId || null
                });
                ctx.set("runtime.editor.chrome", {
                  generated: true,
                  serialize: "omit",
                  repaired: true
                });
                ctx.set("runtime.validationReport", {
                  kind: "repair-report",
                  strategy: "rebuildWorkbenchShell",
                  ok: true,
                  sourceUnchanged: true
                });
                return "rebuildWorkbenchShell";
              },

              post(ctx) {
                const shell = ctx.get("runtime.workbench.shell");
                const chrome = ctx.get("runtime.editor.chrome");
                return Boolean(shell?.mounted) && shell.damaged === false && chrome?.serialize === "omit";
              }
            }
          }
        },

        transitions: {
          openFile: {
            reads: [
              "source.workspace.files",
              "state.openTabs",
              "state.activeFileId"
            ],
            writes: [
              "state.openTabs",
              "state.activeFileId",
              "runtime.loadedFile"
            ],
            emits: [
              "fileOpened"
            ],
            pre(ctx, event) {
              return ctx.exists("source.workspace.files", event?.fileId);
            },
            apply(ctx, event) {
              const files = ctx.get("source.workspace.files");
              const file = findFileById(files, event.fileId);
              ctx.addUnique("state.openTabs", event.fileId);
              ctx.set("state.activeFileId", event.fileId);
              ctx.set("runtime.loadedFile", file);
            },
            post(ctx) {
              const activeFileId = ctx.get("state.activeFileId");
              const openTabs = ctx.get("state.openTabs");
              return Array.isArray(openTabs) && openTabs.includes(activeFileId);
            }
          },

          selectPanel: {
            reads: [],
            writes: [
              "state.selectedPanel"
            ],
            emits: [
              "panelSelected"
            ],
            apply(ctx, event) {
              ctx.set("state.selectedPanel", event?.panel || "source");
            }
          },

          editDraft: {
            reads: [
              "state.activeFileId"
            ],
            writes: [
              "state.drafts",
              "state.dirty"
            ],
            emits: [
              "draftEdited"
            ],
            pre(ctx) {
              return Boolean(ctx.get("state.activeFileId"));
            },
            apply(ctx, event) {
              const fileId = ctx.get("state.activeFileId");
              ctx.set(`state.drafts.${fileId}`, String(event?.text ?? ""));
              ctx.set("state.dirty", true);
            }
          },

          commitDraft: {
            reads: [
              "source.workspace.files",
              "state.activeFileId",
              "state.drafts"
            ],
            writes: [
              "source.workspace.files",
              "state.drafts",
              "state.dirty"
            ],
            emits: [
              "draftCommitted"
            ],
            pre(ctx) {
              const fileId = ctx.get("state.activeFileId");
              return Boolean(fileId) && ctx.get(`state.drafts.${fileId}`) !== undefined;
            },
            apply(ctx) {
              const fileId = ctx.get("state.activeFileId");
              const draft = ctx.get(`state.drafts.${fileId}`);
              const files = ctx.get("source.workspace.files");
              const nextFiles = Array.isArray(files)
                ? files.map((file) => file && file.id === fileId ? {...file, text: draft} : file)
                : [];
              ctx.set("source.workspace.files", nextFiles);
              ctx.delete(`state.drafts.${fileId}`);
              ctx.set("state.dirty", false);
            },
            post(ctx) {
              const fileId = ctx.get("state.activeFileId");
              return ctx.get(`state.drafts.${fileId}`) === undefined;
            }
          },

          toggleBottomDock: {
            reads: [
              "state.bottomDockExpanded"
            ],
            writes: [
              "state.bottomDockExpanded"
            ],
            emits: [
              "dockToggled"
            ],
            apply(ctx) {
              ctx.set("state.bottomDockExpanded", !ctx.get("state.bottomDockExpanded"));
            }
          },

          serializeWorkspace: {
            reads: [
              "source.workspace",
              "runtime.serializedOutput"
            ],
            writes: [
              "runtime.serializedOutput"
            ],
            emits: [
              "workspaceSerialized"
            ],
            apply(ctx) {
              ctx.set("runtime.serializedOutput", JSON.stringify(ctx.get("source.workspace"), null, 2));
            },
            post(ctx) {
              const serialized = ctx.get("runtime.serializedOutput");
              return typeof serialized === "string" && serialized.includes("files");
            }
          }
        },
        effects: {
          loadWorkspace: {
            kind: "async-data",
            triggers: [
              "source.workspace.manifest"
            ],
            reads: [
              "source.workspace.manifest"
            ],
            writes: [
              "runtime.validationReport"
            ],
            external: {
              resource: "workspace-registry",
              operation: "loadWorkspace"
            },
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win",
            errorPolicy: {
              onFailure: "set-runtime-error",
              retry: "manual"
            },
            run(ctx, event) {
              const manifest = ctx.get("source.workspace.manifest");
              return {
                id: event?.workspaceId || manifest?.id || "workspace-main",
                manifest
              };
            },
            commit(ctx, result) {
              ctx.set("runtime.validationReport", {
                kind: "workspace-load-report",
                ok: true,
                workspaceId: result?.id || "workspace-main"
              });
              return result;
            }
          },

          loadFile: {
            kind: "async-data",
            triggers: [
              "state.activeFileId"
            ],
            reads: [
              "source.workspace.files",
              "state.activeFileId"
            ],
            writes: [
              "runtime.loadedFile"
            ],
            external: {
              resource: "workspace-files",
              operation: "loadFile"
            },
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win",
            errorPolicy: {
              onFailure: "set-runtime-error",
              retry: "manual"
            },
            run(ctx, event) {
              const files = ctx.get("source.workspace.files");
              const fileId = event?.fileId || ctx.get("state.activeFileId");
              return findFileById(files, fileId);
            },
            commit(ctx, result) {
              ctx.set("runtime.loadedFile", result || null);
              return result || null;
            }
          },

          saveFile: {
            kind: "async-command",
            triggers: [
              "state.dirty"
            ],
            reads: [
              "source.workspace.files",
              "state.activeFileId",
              "state.dirty"
            ],
            writes: [
              "runtime.validationReport"
            ],
            external: {
              resource: "workspace-files",
              operation: "saveFile"
            },
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win",
            errorPolicy: {
              onFailure: "set-runtime-error",
              retry: "manual"
            },
            run(ctx) {
              return {
                fileId: ctx.get("state.activeFileId"),
                dirty: ctx.get("state.dirty"),
                fileCount: ctx.get("source.workspace.files").length
              };
            },
            commit(ctx, result) {
              ctx.set("runtime.validationReport", {
                kind: "file-save-report",
                ok: true,
                fileId: result?.fileId || null,
                dirty: Boolean(result?.dirty),
                fileCount: Number(result?.fileCount || 0)
              });
              return result;
            }
          },

          runValidation: {
            kind: "async-data",
            triggers: [
              "source.workspace.files",
              "state.dirty"
            ],
            reads: [
              "source.workspace.files",
              "state.dirty"
            ],
            writes: [
              "runtime.validationReport"
            ],
            external: {
              resource: "mcel-validator",
              operation: "validateWorkspace"
            },
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win",
            errorPolicy: {
              onFailure: "set-runtime-error",
              retry: "manual"
            },
            run(ctx) {
              const files = ctx.get("source.workspace.files");
              return {
                fileCount: Array.isArray(files) ? files.length : 0,
                dirty: ctx.get("state.dirty")
              };
            },
            commit(ctx, result) {
              ctx.set("runtime.validationReport", {
                kind: "workspace-validation-report",
                ok: true,
                fileCount: Number(result?.fileCount || 0),
                dirty: Boolean(result?.dirty)
              });
              return result;
            }
          }
        }

      };

      const workspaceFileRouteManifest = {
        version: ROUTE_VERSION,
        contract: ROUTE_CONTRACT,

        segments: [
          {literal: "workspace"},
          {
            param: "workspaceId",
            type: "id",
            required: true
          },
          {literal: "file"},
          {
            param: "fileId",
            type: "id",
            required: true
          }
        ],

        query: {
          panel: {
            type: "enum",
            values: [
              "source",
              "runtime",
              "serialized",
              "contract",
              "debug"
            ],
            default: "source"
          },

          line: {
            type: "integer",
            optional: true
          }
        },

        mounts: {
          component: COMPONENT_NAME,

          inputs: {
            workspaceId: "route.params.workspaceId",
            activeFileId: "route.params.fileId",
            selectedPanel: "route.query.panel"
          }
        },

        data: {
          loadWorkspace: {
            kind: "async-data",

            triggers: [
              "route.params.workspaceId"
            ],

            reads: [
              "route.params.workspaceId"
            ],

            writes: [
              "route.data.workspace"
            ],

            external: {
              resource: "workspace-registry",
              operation: "loadWorkspace"
            },

            cancellation: "cancel-previous",
            racePolicy: "latest-route-wins",

            errorPolicy: {
              onFailure: "set-route-error",
              retry: "manual"
            },

            run(ctx) {
              const workspaceId = ctx.get("route.params.workspaceId");
              return {
                id: workspaceId,
                loaded: true
              };
            },

            commit(ctx, result) {
              ctx.set("route.data.workspace", result);
              return result;
            }
          },

          loadFile: {
            kind: "async-data",

            triggers: [
              "route.params.workspaceId",
              "route.params.fileId"
            ],

            reads: [
              "route.params.workspaceId",
              "route.params.fileId"
            ],

            writes: [
              "route.data.activeFile"
            ],

            external: {
              resource: "workspace-files",
              operation: "loadFile"
            },

            cancellation: "cancel-previous",
            racePolicy: "latest-route-wins",

            errorPolicy: {
              onFailure: "set-route-error",
              retry: "manual"
            },

            run(ctx) {
              return {
                workspaceId: ctx.get("route.params.workspaceId"),
                fileId: ctx.get("route.params.fileId"),
                loaded: true
              };
            },

            commit(ctx, result) {
              ctx.set("route.data.activeFile", result);
              return result;
            }
          }
        },

        lifecycle: {
          onEnter: [
            "validateParams",
            "loadWorkspace",
            "loadFile"
          ],

          onLeave: {
            blockedBy: [
              "component.state.dirty"
            ],

            resolutions: [
              "commitDraft",
              "discardDraft",
              "cancelNavigation"
            ]
          }
        },

        evidence: {
          captureOnEnter: true,
          captureOnLeave: true,
          captureOnBlockedLeave: true,
          captureOnParamFailure: true
        }
      };

      function resolveMcel(explicitMcel) {
        if (explicitMcel && typeof explicitMcel === "object") return explicitMcel;
        if (typeof MCEL !== "undefined") return MCEL;
        if (typeof window !== "undefined" && window.MCEL) return window.MCEL;
        return null;
      }

      function register(options = {}) {
        const mcel = resolveMcel(options.mcel);
        if (!mcel || typeof mcel.defineComponent !== "function") return null;

        if (options.replace !== true && typeof mcel.componentDefinition === "function") {
          const existing = mcel.componentDefinition(COMPONENT_NAME);
          if (existing) return existing;
        }

        return mcel.defineComponent(COMPONENT_NAME, componentManifest, {
          replace: options.replace === true
        });
      }

      function registerRoute(options = {}) {
        const mcel = resolveMcel(options.mcel);
        if (!mcel || typeof mcel.defineRoute !== "function") return null;

        register({mcel, replace: options.replaceComponent === true});

        if (options.replace !== true && typeof mcel.routeDefinition === "function") {
          const existing = mcel.routeDefinition(ROUTE_NAME);
          if (existing) return existing;
        }

        return mcel.defineRoute(ROUTE_NAME, workspaceFileRouteManifest, {
          replace: options.replace === true
        });
      }

      function createDefaultInstance(options = {}) {
        const mcel = resolveMcel(options.mcel);
        if (!mcel || typeof mcel.createComponentInstance !== "function") return null;
        register({mcel, replace: options.replace === true});
        return mcel.createComponentInstance(COMPONENT_NAME, {
          id: options.id || "code-studio-scm-instance",
          source: options.source || defaultSource(),
          runtime: options.runtime || defaultRuntime(),
          state: options.state || defaultState()
        });
      }

      function createDefaultRouteInstance(options = {}) {
        const mcel = resolveMcel(options.mcel);
        if (!mcel || typeof mcel.createRouteInstance !== "function") return null;
        const componentInstance = options.componentInstance || createDefaultInstance({
          mcel,
          id: options.componentInstanceId || "code-studio-route-component-instance",
          source: options.source,
          runtime: options.runtime,
          state: options.state,
          replace: options.replaceComponent === true
        });
        registerRoute({
          mcel,
          replace: options.replace === true,
          replaceComponent: options.replaceComponent === true
        });
        return mcel.createRouteInstance(ROUTE_NAME, {
          id: options.id || "workspace-file-route-instance",
          componentInstance,
          data: options.data || {}
        });
      }

      const api = Object.freeze({
        componentName: COMPONENT_NAME,
        version: COMPONENT_VERSION,
        contract: COMPONENT_CONTRACT,
        routeName: ROUTE_NAME,
        routeVersion: ROUTE_VERSION,
        routeContract: ROUTE_CONTRACT,
        manifest: componentManifest,
        routeManifest: workspaceFileRouteManifest,
        defaultSource,
        defaultRuntime,
        defaultState,
        register,
        registerRoute,
        createDefaultInstance,
        createDefaultRouteInstance
      });

      const registeredDefinition = register();
      const registeredRouteDefinition = registerRoute();
      if (typeof window !== "undefined") {
        window.McelCodeStudioScm = api;
        window.MainComputerCodeStudioScm = {
          definition: registeredDefinition,
          routeDefinition: registeredRouteDefinition,
          manifest: componentManifest,
          routeManifest: workspaceFileRouteManifest
        };
        if (window.MainComputerCodeStudio && typeof window.MainComputerCodeStudio === "object") {
          window.MainComputerCodeStudio.scmDefinition = registeredDefinition;
          window.MainComputerCodeStudio.scmRouteDefinition = registeredRouteDefinition;
          window.MainComputerCodeStudio.scmManifest = componentManifest;
          window.MainComputerCodeStudio.scmRouteManifest = workspaceFileRouteManifest;
        }
      }

      return api;
    })();
