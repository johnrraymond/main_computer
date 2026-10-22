"""Regression for Code Editor preview-status polling retriggering canonical layout.

The DOM's reflected ``hidden`` property queues attribute mutations even when
reassigned the same value. The host-bound layout observer responds to those
mutations, so repeated preview polling must not write an unchanged value.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PREVIEW_HOST = ROOT / "main_computer/web/applications/scripts/mcel-code-editor-preview-host.js"


@pytest.fixture(scope="module")
def observed_hidden_writes() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to exercise the actual preview host")

    # The fake element models browser behavior: a reflected Boolean property
    # assignment records an attribute mutation even when its value is unchanged.
    code = textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');
        const sandbox = {console};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox,
          {filename: 'mcel-code-editor-preview-host.js'});
        const host = sandbox.McelCodeEditorPreviewHost;

        function element(initiallyHidden) {
          let hidden = initiallyHidden;
          const assignments = [];
          const attrs = {};
          return {
            assignments,
            dataset: {},
            innerHTML: '',
            get hidden() { return hidden; },
            set hidden(next) {
              const before = hidden;
              hidden = Boolean(next);
              assignments.push({before, after: hidden});
            },
            setAttribute(name, value) { attrs[name] = String(value); },
            getAttribute(name) { return attrs[name] ?? null; },
            querySelector() { return null; },
            get title() { return attrs.title; }
          };
        }

        const noPreview = {
          status: 'not-applicable', valid: true, previewable: false,
          summary: 'MCEL Preview: not applicable'
        };
        const readyPreview = {
          status: 'pass', valid: true, previewable: true,
          counts: {errors: 0, warnings: 0}, summary: 'MCEL Preview: PASS'
        };
        const doc = {querySelector() { return null; }};

        // Actual periodic refresh route, not only the lower-level renderStatus.
        // The authoring surface is not MCEL, so preview remains hidden.
        sandbox.McelAuthoredSurfaceDocument = {
          analyzeText() {
            return {applicable: false, status: 'not-applicable',
              containsSurfaceRidges: false, diagnostics: []};
          }
        };
        sandbox.McelSurfacePreviewContract = {renderPreview() {
          throw new Error('not-applicable source should not reach rendering');
        }};
        const refreshStatus = element(true);
        for (let i = 0; i < 4; i++) {
          host.refresh({document: doc, element: refreshStatus,
            sourceText: 'ordinary editor source'});
        }

        // Real visibility transitions must still update the reflected attribute;
        // repeated states must not schedule new mutations.
        const transitionStatus = element(true);
        for (const report of [noPreview, noPreview, readyPreview,
                              readyPreview, noPreview, noPreview]) {
          host.renderStatus(transitionStatus, report, {document: doc});
        }
        const initiallyVisible = element(false);
        host.renderStatus(initiallyVisible, readyPreview, {document: doc});
        host.renderStatus(initiallyVisible, readyPreview, {document: doc});

        process.stdout.write(JSON.stringify({
          refresh: {assignments: refreshStatus.assignments,
            hidden: refreshStatus.hidden,
            title: refreshStatus.title,
            status: refreshStatus.dataset.mcelPreviewStatusState},
          transitions: {assignments: transitionStatus.assignments,
            hidden: transitionStatus.hidden,
            status: transitionStatus.dataset.mcelPreviewStatusState},
          alreadyVisible: {assignments: initiallyVisible.assignments,
            hidden: initiallyVisible.hidden}
        }));
        """
    )
    completed = subprocess.run(
        [node, "-e", code, str(PREVIEW_HOST)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def test_idle_refresh_does_not_rewrite_unchanged_hidden_attribute(observed_hidden_writes: dict) -> None:
    refresh = observed_hidden_writes["refresh"]
    assert refresh["assignments"] == []
    assert refresh["hidden"] is True
    assert refresh["status"] == "not-applicable"
    assert refresh["title"] == "The current document does not contain an MCEL surface preview."


def test_actual_visibility_transitions_write_exactly_once(observed_hidden_writes: dict) -> None:
    transitions = observed_hidden_writes["transitions"]
    assert transitions["assignments"] == [
        {"before": True, "after": False},
        {"before": False, "after": True},
    ]
    assert transitions["hidden"] is True
    assert transitions["status"] == "not-applicable"


def test_visible_status_refresh_does_not_rewrite_hidden_attribute(observed_hidden_writes: dict) -> None:
    visible = observed_hidden_writes["alreadyVisible"]
    assert visible["assignments"] == []
    assert visible["hidden"] is False
