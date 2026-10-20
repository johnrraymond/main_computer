"use strict";
const mcel = require("@mcel/app");
const HOST_MARKUP = {
  "tag": "div",
  "attrs": {
    "class": "onlyoffice-app",
    "id": "onlyoffice-app",
    "aria-label": "ONLYOFFICE",
    "data-mc-widget-id": "onlyoffice.root",
    "data-mc-widget-kind": "app",
    "data-mc-widget-class": "app",
    "data-mc-widget-label": "ONLYOFFICE",
    "data-mc-component-id": "onlyoffice.root",
    "data-mc-component-kind": "app",
    "data-mc-component-label": "ONLYOFFICE",
    "data-mc-component-owner": "applications.workspace",
    "data-mc-feature-id": "onlyoffice.feature.workbook"
  },
  "children": [
    {
      "text": "\n"
    },
    {
      "tag": "div",
      "attrs": {
        "class": "onlyoffice-shell app-widget",
        "data-widget-label": "ONLYOFFICE"
      },
      "children": [
        {
          "text": "\n"
        },
        {
          "tag": "aside",
          "attrs": {
            "class": "onlyoffice-library",
            "aria-label": "ONLYOFFICE workbook library"
          },
          "children": [
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-library-head"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "strong",
                  "attrs": {},
                  "children": [
                    {
                      "text": "ONLYOFFICE"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "id": "onlyoffice-refresh-files"
                  },
                  "children": [
                    {
                      "text": "Refresh"
                    }
                  ]
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-library-actions"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "id": "onlyoffice-new-workbook"
                  },
                  "children": [
                    {
                      "text": "New XLSX"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "id": "onlyoffice-upload-trigger"
                  },
                  "children": [
                    {
                      "text": "Upload XLSX"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "input",
                  "attrs": {
                    "type": "file",
                    "id": "onlyoffice-upload-file",
                    "accept": ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "hidden": ""
                  },
                  "children": []
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-library-status",
                "id": "onlyoffice-library-status"
              },
              "children": [
                {
                  "text": "Loading ONLYOFFICE workbooks..."
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-file-list",
                "id": "onlyoffice-file-list"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "div",
                  "attrs": {
                    "class": "onlyoffice-file-empty"
                  },
                  "children": [
                    {
                      "text": "No XLSX workbooks found."
                    }
                  ]
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            }
          ]
        },
        {
          "text": "\n"
        },
        {
          "tag": "main",
          "attrs": {
            "class": "onlyoffice-workspace"
          },
          "children": [
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-head"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "div",
                  "attrs": {},
                  "children": [
                    {
                      "text": "\n"
                    },
                    {
                      "tag": "strong",
                      "attrs": {},
                      "children": [
                        {
                          "text": "ONLYOFFICE Workbook Editor"
                        }
                      ]
                    },
                    {
                      "text": "\n"
                    },
                    {
                      "tag": "span",
                      "attrs": {},
                      "children": [
                        {
                          "text": "Native XLSX editing through a self-hosted ONLYOFFICE Document Server."
                        }
                      ]
                    },
                    {
                      "text": "\n"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "class": "mcel-diagnostics-counter",
                    "id": "onlyoffice-diagnostics-counter",
                    "data-mcel-diagnostics-counter": "onlyoffice",
                    "aria-label": "MCEL diagnostics counter"
                  },
                  "children": [
                    {
                      "text": "[00/00/00]"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "div",
                  "attrs": {
                    "class": "onlyoffice-status",
                    "id": "onlyoffice-status"
                  },
                  "children": [
                    {
                      "text": "ONLYOFFICE not connected"
                    }
                  ]
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-current-path",
                "id": "onlyoffice-current-path"
              },
              "children": [
                {
                  "text": "No workbook selected."
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-server-status",
                "id": "onlyoffice-server-status"
              },
              "children": [
                {
                  "text": "Document Server status unknown."
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-server-url",
                "id": "onlyoffice-server-url"
              },
              "children": [
                {
                  "text": "Default local Docker URL: http://127.0.0.1:18085"
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-toolbar",
                "aria-label": "ONLYOFFICE workbook controls"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "id": "onlyoffice-save-now"
                  },
                  "children": [
                    {
                      "text": "Save now"
                    }
                  ]
                },
                {
                  "text": "\n"
                },
                {
                  "tag": "button",
                  "attrs": {
                    "type": "button",
                    "id": "onlyoffice-reload-editor"
                  },
                  "children": [
                    {
                      "text": "Reload editor"
                    }
                  ]
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-note",
                "id": "onlyoffice-note"
              },
              "children": [
                {
                  "text": "\n                  Default Windows local mode uses the Main Computer ONLYOFFICE Docker container at http://127.0.0.1:18085. Run tools\\onlyoffice\\onlyoffice-control.ps1 start if the server is offline.\n                "
                }
              ]
            },
            {
              "text": "\n"
            },
            {
              "tag": "div",
              "attrs": {
                "class": "onlyoffice-editor-host",
                "id": "onlyoffice-editor-host"
              },
              "children": [
                {
                  "text": "\n"
                },
                {
                  "tag": "div",
                  "attrs": {
                    "class": "onlyoffice-editor-placeholder"
                  },
                  "children": [
                    {
                      "text": "\n                    Select or upload an .xlsx workbook to open it in ONLYOFFICE.\n                  "
                    }
                  ]
                },
                {
                  "text": "\n"
                }
              ]
            },
            {
              "text": "\n"
            }
          ]
        },
        {
          "text": "\n"
        }
      ]
    },
    {
      "text": "\n"
    }
  ]
};
module.exports = mcel.defineApp({id:"onlyoffice",title:"ONLYOFFICE",semanticVersion:"1"}, ({application}) => application.hostBound(app => {
  app.presentation.hostBound("workspace", {route:"/applications/onlyoffice",root:"#onlyoffice-app",presentationAuthority:"mcel-compiled-host"});
  app.presentation.semanticSurface({id:"onlyoffice.semantic-surface.host",surfaceId:"onlyoffice.surface.editor",hostMarkup:HOST_MARKUP});
  app.layout.tree({schema:"mcel.layout-tree.v1",root:{id:"host",selector:"#onlyoffice-app",role:"group",maxColumns:1,children:[{id:"editor",selector:"#onlyoffice-editor-host",role:"region",verifyControls:false}]}});
  app.proof.semanticRuntimeProven({targetTruthStatus:"runtime-baseline"});
}));
