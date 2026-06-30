from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; SCM kernel functional smoke test cannot run")

    script_path = tmp_path / "mcel-scm-kernel-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _scm_bootstrap() -> str:
    return f"""
const window = {{}};
{_script("mcel-scm.js")}
McelLabScm.clearDefinitions();
"""


def _core_dependency_stubs() -> str:
    return """
const window = {};
var McelLabContract = {
  contractVersion: "mcel.facade.test",
  defaultSource: "<main data-mc='component'></main>",
  attributes: {sourceIndex: "data-mc-source-index"}
};
var McelLabEngine = {};
var McelLabEditor = {};
var McelLabStyleLaw = {};
var McelLabLayoutLaw = {};
var McelLabChromeLaw = {};
var McelLabBrowserObserver = {};
var McelLabPlatformSpine = {};
var McelLabWorkbench = {};
var McelLabBrowserRunner = {};
var McelLabCommandSurface = {};
var McelLabGraph = {};
var McelLabOpsRunner = {};
var McelLabAcidTests = {};
var McelLabSupervisor = {};
var McelLabLawRegistry = {};
"""


def test_scm_defines_and_lists_component(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const definition = McelLabScm.defineComponent("TestPanel", {{
  version: "1.0.0",
  contract: "test.panel.v1",
  owns: {{
    state: ["activeFileId"]
  }},
  state: {{
    activeFileId: null
  }},
  transitions: {{}}
}});

process.stdout.write(JSON.stringify({{
  definitionName: definition.name,
  listed: McelLabScm.listComponentDefinitions(),
  lookupName: McelLabScm.componentDefinition("TestPanel").name
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["definitionName"] == "TestPanel"
    assert data["lookupName"] == "TestPanel"
    assert data["listed"] == [
        {
            "kind": "mcel-scm-component-definition-summary",
            "name": "TestPanel",
            "version": "1.0.0",
            "contract": "test.panel.v1",
            "owns": {"state": ["activeFileId"]},
            "transitions": [],
            "effects": [],
        }
    ]


def test_scm_rejects_component_without_ownership(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const missing = McelLabScm.validateComponentManifest("NoOwner", {{
  transitions: {{}}
}});
const empty = McelLabScm.validateComponentManifest("EmptyOwner", {{
  owns: {{}},
  transitions: {{}}
}});

let thrown = null;
try {{
  McelLabScm.defineComponent("NoOwner", {{transitions: {{}}}});
}} catch (error) {{
  thrown = error.violation;
}}

process.stdout.write(JSON.stringify({{
  missing,
  empty,
  thrown
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["missing"]["ok"] is False
    assert data["missing"]["issues"][0]["code"] == "SCM_MISSING_OWNERSHIP"
    assert data["empty"]["ok"] is False
    assert data["empty"]["issues"][0]["code"] == "SCM_EMPTY_OWNERSHIP"
    assert data["thrown"]["kind"] == "mcel-scm-violation"
    assert data["thrown"]["code"] == "SCM_MISSING_OWNERSHIP"


def test_scm_rejects_transition_without_reads_and_writes(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const validation = McelLabScm.validateComponentManifest("BadTransition", {{
  owns: {{
    state: ["activeFileId"]
  }},
  transitions: {{
    selectFile: {{
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }}
}});

process.stdout.write(JSON.stringify(validation));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert [issue["code"] for issue in data["issues"]] == [
        "SCM_TRANSITION_MISSING_PATH_LIST",
        "SCM_TRANSITION_MISSING_PATH_LIST",
    ]
    assert {issue["property"] for issue in data["issues"]} == {"reads", "writes"}


def test_scm_allows_declared_transition_write(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("CodeStudio", {{
  version: "1.0.0",
  contract: "mcel.scm.code-studio.v1",
  owns: {{
    source: ["workspace.files", "workspace.manifest"],
    runtime: ["workbench.shell", "editor.chrome"],
    state: ["activeFileId", "openTabs", "drafts"]
  }},
  state: {{
    activeFileId: null,
    openTabs: [],
    drafts: {{}}
  }},
  transitions: {{
    openFile: {{
      reads: ["source.workspace.files", "state.openTabs"],
      writes: ["state.openTabs", "state.activeFileId"],
      apply(ctx, event) {{
        ctx.addUnique("state.openTabs", event.fileId);
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("CodeStudio", {{
  source: {{
    workspace: {{
      files: [{{id: "src-app", path: "src/app.js"}}]
    }}
  }}
}});
const result = McelLabScm.transition(instance, "openFile", {{fileId: "src-app"}});
const packet = McelLabScm.exportEvidence(instance);

process.stdout.write(JSON.stringify({{
  result,
  state: instance.state,
  packet
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["result"]["ok"] is True
    assert data["state"]["activeFileId"] == "src-app"
    assert data["state"]["openTabs"] == ["src-app"]
    assert data["packet"]["evidence"][0]["phase"] == "create-instance"
    assert data["packet"]["evidence"][-1]["phase"] == "transition"
    assert data["packet"]["evidence"][-1]["ok"] is True


def test_scm_rejects_undeclared_transition_write(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("CodeStudio", {{
  owns: {{
    state: ["drafts", "activeFileId"]
  }},
  state: {{
    activeFileId: "src-app",
    drafts: {{}}
  }},
  transitions: {{
    editDraft: {{
      reads: ["state.activeFileId"],
      writes: ["state.drafts"],
      apply(ctx, event) {{
        ctx.set("state.secret", event.text);
      }}
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("CodeStudio");
let violation = null;
try {{
  McelLabScm.transition(instance, "editDraft", {{text: "bad"}});
}} catch (error) {{
  violation = error.violation;
}}

process.stdout.write(JSON.stringify({{
  violation,
  state: instance.state,
  evidence: McelLabScm.exportEvidence(instance)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_UNDECLARED_WRITE"
    assert data["violation"]["path"] == "state.secret"
    assert data["violation"]["declaredWrites"] == ["state.drafts"]
    assert data["state"]["secret"] is None if "secret" in data["state"] else "secret" not in data["state"]
    assert data["evidence"]["evidence"][-1]["code"] == "SCM_UNDECLARED_WRITE"


def test_scm_rejects_undeclared_transition_read(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("CodeStudio", {{
  owns: {{
    state: ["drafts", "activeFileId"]
  }},
  state: {{
    activeFileId: "src-app",
    secret: "hidden",
    drafts: {{}}
  }},
  transitions: {{
    editDraft: {{
      reads: ["state.activeFileId"],
      writes: ["state.drafts"],
      apply(ctx, event) {{
        const secret = ctx.get("state.secret");
        ctx.set("state.drafts.src-app", secret + event.text);
      }}
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("CodeStudio");
let violation = null;
try {{
  McelLabScm.transition(instance, "editDraft", {{text: "bad"}});
}} catch (error) {{
  violation = error.violation;
}}

process.stdout.write(JSON.stringify({{
  violation,
  evidence: McelLabScm.exportEvidence(instance)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_UNDECLARED_READ"
    assert data["violation"]["path"] == "state.secret"
    assert data["violation"]["declaredReads"] == ["state.activeFileId"]
    assert data["evidence"]["evidence"][-1]["code"] == "SCM_UNDECLARED_READ"


def test_scm_allows_descendant_path_of_declared_parent(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("DraftOwner", {{
  owns: {{
    state: ["drafts"]
  }},
  state: {{
    drafts: {{}}
  }},
  transitions: {{
    editDraft: {{
      reads: [],
      writes: ["state.drafts"],
      apply(ctx, event) {{
        ctx.set("state.drafts.src-app", event.text);
      }}
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("DraftOwner");
const result = McelLabScm.transition(instance, "editDraft", {{text: "new text"}});

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  state: instance.state
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is True
    assert data["state"]["drafts"]["src-app"] == "new text"


def test_scm_rejects_duplicate_component_unless_replace_true(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const manifest = {{
  owns: {{
    state: ["activeFileId"]
  }},
  state: {{
    activeFileId: null
  }},
  transitions: {{}}
}};

McelLabScm.defineComponent("CodeStudio", manifest);
let duplicate = null;
try {{
  McelLabScm.defineComponent("CodeStudio", manifest);
}} catch (error) {{
  duplicate = error.violation;
}}
McelLabScm.defineComponent("CodeStudio", {{
  ...manifest,
  version: "replacement"
}}, {{replace: true}});

process.stdout.write(JSON.stringify({{
  duplicate,
  definition: McelLabScm.componentDefinition("CodeStudio")
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["duplicate"]["code"] == "SCM_DUPLICATE_COMPONENT"
    assert data["definition"]["version"] == "replacement"


def test_scm_rejects_undeclared_outputs_and_missing_child_transition_targets(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const validation = McelLabScm.validateComponentManifest("OutputOwner", {{
  owns: {{
    state: ["activeFileId"]
  }},
  outputs: ["selected"],
  transitions: {{
    selectFile: {{
      reads: [],
      writes: ["state.activeFileId"],
      emits: ["missingOutput"],
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }},
  children: {{
    explorer: {{
      outputs: {{
        openFile: "transition.openFile"
      }}
    }}
  }}
}});

process.stdout.write(JSON.stringify(validation));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_UNDECLARED_OUTPUT" in [issue["code"] for issue in data["issues"]]
    assert "SCM_CHILD_OUTPUT_TARGET_MISSING" in [issue["code"] for issue in data["issues"]]


def test_mcel_core_facade_exposes_scm_kernel(tmp_path: Path) -> None:
    script = f"""
{_core_dependency_stubs()}
{_script("mcel-scm.js")}
{_script("mcel-core.js")}

MCEL.scm.clearDefinitions();
MCEL.defineComponent("FacadeStudio", {{
  owns: {{
    state: ["activeFileId"]
  }},
  state: {{
    activeFileId: null
  }},
  transitions: {{
    openFile: {{
      reads: [],
      writes: ["state.activeFileId"],
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }}
}});

const instance = MCEL.createComponentInstance("FacadeStudio");
MCEL.transition(instance, "openFile", {{fileId: "src-app"}});

process.stdout.write(JSON.stringify({{
  hasScm: Boolean(MCEL.scm),
  listed: MCEL.listComponentDefinitions().map((item) => item.name),
  activeFileId: instance.state.activeFileId,
  evidenceKind: MCEL.exportScmEvidence(instance).kind,
  facadeMethods: [
    typeof MCEL.defineComponent,
    typeof MCEL.validateComponentManifest,
    typeof MCEL.createComponentInstance,
    typeof MCEL.createChildContext,
    typeof MCEL.createEffectContext,
    typeof MCEL.runEffect,
    typeof MCEL.cancelEffect,
    typeof MCEL.transition,
    typeof MCEL.exportScmEvidence
  ]
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["hasScm"] is True
    assert data["listed"] == ["FacadeStudio"]
    assert data["activeFileId"] == "src-app"
    assert data["evidenceKind"] == "mcel-scm-evidence-packet"
    assert data["facadeMethods"] == ["function", "function", "function", "function", "function", "function", "function", "function", "function"]


def test_scm_validates_child_slots_inputs_outputs_and_mutations(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const invalid = McelLabScm.validateComponentManifest("BrokenChildren", {{
  owns: {{
    source: ["workspace.files"],
    state: ["activeFileId", "drafts"],
    runtime: ["loadedFile"],
    layout: ["sidebar", "editorGroup"]
  }},
  transitions: {{
    openFile: {{
      reads: [],
      writes: ["state.activeFileId"],
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }},
  children: {{
    explorer: {{
      component: "FileExplorer",
      slot: "missingSlot",
      inputs: {{
        files: "source.workspace.files",
        secret: "state.secret"
      }},
      outputs: {{
        openFile: "transition.missingTransition",
        badTarget: "handler.openFile"
      }},
      mayMutate: ["state.secret"],
      maySerialize: "no"
    }}
  }}
}});

const valid = McelLabScm.validateComponentManifest("ValidChildren", {{
  owns: {{
    source: ["workspace.files"],
    state: ["activeFileId", "drafts"],
    runtime: ["loadedFile"],
    layout: ["sidebar", "editorGroup"]
  }},
  transitions: {{
    openFile: {{
      reads: ["source.workspace.files"],
      writes: ["state.activeFileId"],
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }},
  children: {{
    explorer: {{
      component: "FileExplorer",
      slot: "sidebar",
      inputs: {{
        files: "source.workspace.files",
        activeFileId: "state.activeFileId"
      }},
      outputs: {{
        openFile: "transition.openFile"
      }},
      mayMutate: [],
      maySerialize: false
    }},
    editor: {{
      component: "SourceEditor",
      slot: "editorGroup",
      inputs: {{
        drafts: "state.drafts"
      }},
      outputs: {{}},
      mayMutate: ["state.drafts"],
      maySerialize: false
    }}
  }}
}});

process.stdout.write(JSON.stringify({{
  invalidOk: invalid.ok,
  invalidCodes: invalid.issues.map((issue) => issue.code),
  validOk: valid.ok,
  validIssueCount: valid.issues.length
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["invalidOk"] is False
    assert "SCM_CHILD_SLOT_OUTSIDE_LAYOUT" in data["invalidCodes"]
    assert "SCM_CHILD_INPUT_TARGET_UNOWNED" in data["invalidCodes"]
    assert "SCM_CHILD_OUTPUT_TARGET_MISSING" in data["invalidCodes"]
    assert "SCM_CHILD_OUTPUT_TARGET_INVALID" in data["invalidCodes"]
    assert "SCM_CHILD_MUTATION_OUTSIDE_OWNERSHIP" in data["invalidCodes"]
    assert "SCM_CHILD_MAY_SERIALIZE_NOT_BOOLEAN" in data["invalidCodes"]
    assert data["validOk"] is True
    assert data["validIssueCount"] == 0


def test_scm_child_context_reads_inputs_emits_outputs_and_enforces_may_mutate(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("ChildStudio", {{
  owns: {{
    source: ["workspace.files"],
    state: ["activeFileId", "drafts"],
    layout: ["sidebar", "editorGroup"]
  }},
  source: {{
    workspace: {{
      files: [
        {{id: "src-app", text: "hello"}},
        {{id: "test-app", text: "test"}}
      ]
    }}
  }},
  state: {{
    activeFileId: "src-app",
    drafts: {{}}
  }},
  transitions: {{
    openFile: {{
      reads: ["source.workspace.files"],
      writes: ["state.activeFileId"],
      apply(ctx, event) {{
        ctx.set("state.activeFileId", event.fileId);
      }}
    }}
  }},
  children: {{
    explorer: {{
      component: "FileExplorer",
      slot: "sidebar",
      inputs: {{
        files: "source.workspace.files",
        activeFileId: "state.activeFileId"
      }},
      outputs: {{
        openFile: "transition.openFile"
      }},
      mayMutate: [],
      maySerialize: false
    }},
    editor: {{
      component: "SourceEditor",
      slot: "editorGroup",
      inputs: {{
        drafts: "state.drafts"
      }},
      outputs: {{}},
      mayMutate: ["state.drafts"],
      maySerialize: false
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("ChildStudio");
const explorer = McelLabScm.createChildContext(instance, "explorer");
const editor = McelLabScm.createChildContext(instance, "editor");
const fileIds = explorer.get("files").map((file) => file.id);

explorer.emit("openFile", {{fileId: "test-app"}});
editor.set("state.drafts.test-app", "updated draft");

let blocked = null;
try {{
  explorer.set("state.activeFileId", "src-app");
}} catch (error) {{
  blocked = error.violation;
}}

const packet = McelLabScm.exportEvidence(instance);

process.stdout.write(JSON.stringify({{
  contextKind: explorer.kind,
  childComponent: explorer.childComponent,
  fileIds,
  activeFileId: instance.state.activeFileId,
  draft: instance.state.drafts["test-app"],
  blocked,
  evidencePhases: packet.evidence.map((entry) => entry.phase)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["contextKind"] == "mcel-scm-child-context"
    assert data["childComponent"] == "FileExplorer"
    assert data["fileIds"] == ["src-app", "test-app"]
    assert data["activeFileId"] == "test-app"
    assert data["draft"] == "updated draft"
    assert data["blocked"]["code"] == "SCM_CHILD_UNDECLARED_MUTATION"
    assert data["blocked"]["childName"] == "explorer"
    assert "child-output" in data["evidencePhases"]
    assert "child-mutation" in data["evidencePhases"]
