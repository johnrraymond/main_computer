from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
EPISTEMIC = SCRIPTS / "mcel-epistemic-status.js"
OBSERVATION = SCRIPTS / "mcel-observation-bundle.js"
PRODUCER = SCRIPTS / "mcel-browser-observation-producer.js"


def run_node_json(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; browser observation producer tests cannot run")
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const path of [
          {json.dumps(str(EPISTEMIC))},
          {json.dumps(str(OBSERVATION))},
          {json.dumps(str(PRODUCER))}
        ]) {{
          vm.runInNewContext(fs.readFileSync(path, "utf8"), sandbox, {{filename: path}});
        }}
        const observations = sandbox.McelObservationBundle;
        const producer = sandbox.McelBrowserObservationProducer;

        function makeElement(tagName, attributes = {{}}, textContent = "") {{
          const attributeMap = Object.fromEntries(
            Object.entries(attributes).map(([name, value]) => [String(name).toLowerCase(), String(value)])
          );
          const element = {{
            tagName: String(tagName).toUpperCase(),
            textContent,
            children: [],
            parentElement: null,
            ownerDocument: null,
            attributes: Object.entries(attributeMap).map(([name, value]) => ({{name, value}})),
            operationCalls: [],
            getAttribute(name) {{
              const key = String(name).toLowerCase();
              return Object.prototype.hasOwnProperty.call(attributeMap, key) ? attributeMap[key] : null;
            }},
            hasAttribute(name) {{
              return Object.prototype.hasOwnProperty.call(attributeMap, String(name).toLowerCase());
            }},
            click() {{ this.operationCalls.push("click"); throw new Error("click forbidden"); }},
            focus() {{ this.operationCalls.push("focus"); throw new Error("focus forbidden"); }},
            submit() {{ this.operationCalls.push("submit"); throw new Error("submit forbidden"); }},
            requestSubmit() {{ this.operationCalls.push("requestSubmit"); throw new Error("requestSubmit forbidden"); }},
            dispatchEvent() {{ this.operationCalls.push("dispatchEvent"); throw new Error("dispatchEvent forbidden"); }}
          }};
          return element;
        }}

        function append(parent, child) {{
          parent.children.push(child);
          child.parentElement = parent;
          child.ownerDocument = parent.ownerDocument;
          child.isConnected = parent.isConnected;
          return child;
        }}

        function buildFixture(reverseEnumeration = false) {{
          let boundSurface = null;
          const ownerDocument = {{
            defaultView: {{
              innerWidth: 1280,
              innerHeight: 720,
              devicePixelRatio: 1
            }},
            documentElement: {{
              contains(element) {{
                return Boolean(
                  element &&
                  element.ownerDocument === ownerDocument &&
                  element.isConnected === true
                );
              }}
            }},
            querySelectorAll(selector) {{
              if (selector === "#parcel-surface" && boundSurface) return [boundSurface];
              return [];
            }}
          }};
          const root = makeElement("section", {{
            id: "parcel-surface",
            "data-state": "ready"
          }}, "Parcel desk Parcel name Create parcel");
          root.ownerDocument = ownerDocument;
          root.isConnected = true;
          boundSurface = root;
          const heading = append(root, makeElement("h1", {{}}, "Parcel desk"));
          const label = append(root, makeElement("label", {{for: "parcel-name"}}, "Parcel name"));
          const input = append(root, makeElement("input", {{
            id: "parcel-name",
            required: "",
            "aria-describedby": "parcel-name-hint"
          }}));
          input.required = true;
          const button = append(root, makeElement("button", {{
            type: "submit",
            "aria-expanded": "false"
          }}, "Create parcel"));
          const descendants = [heading, label, input, button];
          root.querySelectorAll = (selector) => {{
            if (selector !== "*") throw new Error(`unexpected selector: ${{selector}}`);
            return reverseEnumeration ? [...descendants].reverse() : [...descendants];
          }};
          return {{root, elements: [root, ...descendants]}};
        }}

        function captureInput(root, overrides = {{}}) {{
          return {{
            root,
            observationId: "observation.parcel-desk.dom-a11y.1",
            appId: "parcel-desk",
            route: "/fixtures/parcel-desk",
            surfaceId: "surface.parcel-desk.main",
            surfaceLocator: "#parcel-surface",
            surfaceDescriptor: {{
              appId: "parcel-desk",
              route: "/fixtures/parcel-desk",
              surfaceId: "surface.parcel-desk.main",
              locator: "#parcel-surface"
            }},
            repositoryFingerprint: "repo:abc123",
            capturedAt: "2026-07-30T21:00:00Z",
            codeFingerprint: "sha256:producer-fixture",
            viewport: {{width: 1280, height: 720, deviceScaleFactor: 1}},
            stateMarkers: ["clean-start"],
            browser: {{name: "StaticFixture", version: "1"}},
            ...overrides
          }};
        }}

        {body}
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_read_only_producer_builds_repository_and_surface_bound_bundle() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        const bundle = producer.captureReadOnlyObservation(captureInput(fixture.root));
        const report = observations.validateObservationBundle(bundle);
        const inputFact = bundle.lenses.accessibility.facts.find(
          (fact) => fact.value.nativeElement === "input"
        );
        process.stdout.write(JSON.stringify({
          valid: report.valid,
          contractVersion: bundle.contractVersion,
          mode: bundle.mode,
          route: bundle.route,
          repositoryFingerprint: bundle.repositoryFingerprint,
          stateMarkers: bundle.stateMarkers,
          source: {
            appId: bundle.provenance.sources[0].appId,
            id: bundle.provenance.sources[0].id,
            kind: bundle.provenance.sources[0].kind,
            locator: bundle.provenance.sources[0].locator,
            route: bundle.provenance.sources[0].route
          },
          surfaceDescriptor: bundle.provenance.sources[0].surfaceDescriptor,
          binding: bundle.provenance.sources[0].binding,
          capture: bundle.provenance.sources[0].capture,
          redaction: bundle.provenance.sources[0].redaction,
          lensStatuses: Object.fromEntries(
            Object.entries(bundle.lenses).map(([id, lens]) => [id, lens.status])
          ),
          domFactCount: bundle.lenses.dom.facts.length,
          accessibilityFactCount: bundle.lenses.accessibility.facts.length,
          accessibilityReason: bundle.lenses.accessibility.reason,
          inputAccessibility: inputFact && inputFact.value,
          claims: bundle.claims,
          resolvedClaims: bundle.resolvedClaims
        }));
        """
    )

    assert result["valid"] is True
    assert result["contractVersion"] == "mcel.observation-bundle.v1"
    assert result["mode"] == "read-only"
    assert result["route"] == "/fixtures/parcel-desk"
    assert result["repositoryFingerprint"] == "repo:abc123"
    assert "surface:surface.parcel-desk.main" in result["stateMarkers"]
    assert result["source"] == {
        "appId": "parcel-desk",
        "id": "surface.parcel-desk.main",
        "kind": "captured-browser-surface",
        "locator": "#parcel-surface",
        "route": "/fixtures/parcel-desk",
    }
    assert result["surfaceDescriptor"] == {
        "appId": "parcel-desk",
        "locator": "#parcel-surface",
        "route": "/fixtures/parcel-desk",
        "surfaceId": "surface.parcel-desk.main",
    }
    assert result["binding"] == {
        "matchCount": 1,
        "resolver": "document.querySelectorAll",
        "status": "validated",
    }
    assert result["capture"]["policyId"] == "mcel.browser-observation.capture-limits.v1"
    assert result["capture"]["status"] == "complete"
    assert result["capture"]["partialReasons"] == []
    assert result["redaction"] == {
        "policyId": "mcel.redaction-policy.stub.v1",
        "redactedFactCount": 0,
        "status": "not-implemented",
    }
    assert "capture:complete" in result["stateMarkers"]
    assert "redaction:not-implemented" in result["stateMarkers"]
    assert result["lensStatuses"] == {
        "dom": "captured",
        "accessibility": "captured",
        "layout": "missing",
        "visual": "missing",
        "source": "missing",
        "transition": "unavailable",
        "ridges": "missing",
    }
    assert result["domFactCount"] == 5
    assert result["accessibilityFactCount"] == 5
    assert result["accessibilityReason"] == "bounded-authored-dom-semantics-only"
    assert result["inputAccessibility"]["aria"] == {
        "aria-describedby": "parcel-name-hint"
    }
    assert result["inputAccessibility"]["labels"] == [
        {
            "locator": "#parcel-surface > label:nth-of-type(1)",
            "text": "Parcel name",
        }
    ]
    assert result["inputAccessibility"]["states"] == {"required": True}
    assert result["claims"] == []
    assert result["resolvedClaims"] == []


def test_fact_order_and_bundle_fingerprint_are_enumeration_independent() -> None:
    result = run_node_json(
        """
        const first = buildFixture(false);
        const second = buildFixture(true);
        const left = producer.captureReadOnlyObservation(captureInput(first.root));
        const right = producer.captureReadOnlyObservation(captureInput(second.root));
        process.stdout.write(JSON.stringify({
          leftFingerprint: left.bundleFingerprint,
          rightFingerprint: right.bundleFingerprint,
          leftDomIds: left.lenses.dom.facts.map((fact) => fact.id),
          rightDomIds: right.lenses.dom.facts.map((fact) => fact.id),
          leftAccessibilityIds: left.lenses.accessibility.facts.map((fact) => fact.id),
          rightAccessibilityIds: right.lenses.accessibility.facts.map((fact) => fact.id)
        }));
        """
    )

    assert result["leftFingerprint"] == result["rightFingerprint"]
    assert result["leftDomIds"] == result["rightDomIds"]
    assert result["leftAccessibilityIds"] == result["rightAccessibilityIds"]


@pytest.mark.parametrize(
    ("missing_key", "expected_code"),
    [
        ("observationId", "MCEL_BROWSER_OBSERVATION_ID_MISSING"),
        ("appId", "MCEL_BROWSER_OBSERVATION_APP_ID_MISSING"),
        ("route", "MCEL_BROWSER_OBSERVATION_ROUTE_MISSING"),
        ("surfaceId", "MCEL_BROWSER_OBSERVATION_SURFACE_ID_MISSING"),
        ("surfaceLocator", "MCEL_BROWSER_OBSERVATION_SURFACE_LOCATOR_MISSING"),
        (
            "repositoryFingerprint",
            "MCEL_BROWSER_OBSERVATION_REPOSITORY_FINGERPRINT_MISSING",
        ),
        ("capturedAt", "MCEL_BROWSER_OBSERVATION_CAPTURED_AT_MISSING"),
        ("codeFingerprint", "MCEL_BROWSER_OBSERVATION_CODE_FINGERPRINT_MISSING"),
    ],
)
def test_producer_refuses_missing_identity_and_provenance(
    missing_key: str,
    expected_code: str,
) -> None:
    result = run_node_json(
        f"""
        const fixture = buildFixture();
        const input = captureInput(fixture.root);
        delete input[{json.dumps(missing_key)}];
        try {{
          producer.captureReadOnlyObservation(input);
          process.stdout.write(JSON.stringify({{code: "NO_ERROR"}}));
        }} catch (error) {{
          process.stdout.write(JSON.stringify({{code: error.code, detail: error.detail}}));
        }}
        """
    )

    assert result["code"] == expected_code


def test_producer_refuses_active_mode_and_control_operation_requests() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        const codes = [];
        for (const overrides of [
          {mode: "active"},
          {operations: [{kind: "click"}]},
          {navigation: {route: "/next"}}
        ]) {
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root, overrides));
            codes.push("NO_ERROR");
          } catch (error) {
            codes.push(error.code);
          }
        }
        process.stdout.write(JSON.stringify({codes}));
        """
    )

    assert result["codes"] == [
        "MCEL_BROWSER_OBSERVATION_MODE_FORBIDDEN",
        "MCEL_BROWSER_OBSERVATION_OPERATION_FORBIDDEN",
        "MCEL_BROWSER_OBSERVATION_OPERATION_FORBIDDEN",
    ]


def test_capture_never_invokes_fixture_control_methods() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        producer.captureReadOnlyObservation(captureInput(fixture.root));
        process.stdout.write(JSON.stringify({
          operationCalls: fixture.elements.flatMap((element) => element.operationCalls)
        }));
        """
    )

    assert result["operationCalls"] == []


def test_deferred_lenses_are_explicit_and_observation_does_not_verify_claims() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        const bundle = producer.captureReadOnlyObservation(captureInput(fixture.root));
        process.stdout.write(JSON.stringify({
          lenses: Object.fromEntries(
            Object.entries(bundle.lenses).map(([id, lens]) => [
              id,
              {status: lens.status, reason: lens.reason, factCount: lens.facts.length}
            ])
          ),
          claimStatuses: bundle.claims.map((claim) => claim.status)
        }));
        """
    )

    assert result["lenses"]["layout"] == {
        "status": "missing",
        "reason": "deferred-from-dom-accessibility-baseline",
        "factCount": 0,
    }
    assert result["lenses"]["visual"] == {
        "status": "missing",
        "reason": "deferred-from-dom-accessibility-baseline",
        "factCount": 0,
    }
    assert result["lenses"]["source"] == {
        "status": "missing",
        "reason": "deferred-from-dom-accessibility-baseline",
        "factCount": 0,
    }
    assert result["lenses"]["transition"] == {
        "status": "unavailable",
        "reason": "active-exploration-forbidden",
        "factCount": 0,
    }
    assert result["lenses"]["ridges"] == {
        "status": "missing",
        "reason": "deferred-from-dom-accessibility-baseline",
        "factCount": 0,
    }
    assert result["claimStatuses"] == []


def test_surface_descriptor_and_binding_are_validated_before_capture() -> None:
    result = run_node_json(
        """
        const cases = [];

        {
          const fixture = buildFixture();
          const input = captureInput(fixture.root);
          delete input.surfaceDescriptor;
          try {
            producer.captureReadOnlyObservation(input);
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          const input = captureInput(fixture.root);
          input.surfaceDescriptor = {...input.surfaceDescriptor, route: "/wrong-route"};
          try {
            producer.captureReadOnlyObservation(input);
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          fixture.root.ownerDocument.querySelectorAll = () => [];
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          fixture.root.ownerDocument.querySelectorAll = () => [
            fixture.root,
            fixture.elements[1]
          ];
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          fixture.root.ownerDocument.querySelectorAll = () => [fixture.elements[1]];
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          fixture.root.isConnected = false;
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            cases.push("NO_ERROR");
          } catch (error) {
            cases.push(error.code);
          }
        }

        process.stdout.write(JSON.stringify({cases}));
        """
    )

    assert result["cases"] == [
        "MCEL_BROWSER_OBSERVATION_SURFACE_DESCRIPTOR_MISSING",
        "MCEL_BROWSER_OBSERVATION_SURFACE_DESCRIPTOR_MISMATCH",
        "MCEL_BROWSER_OBSERVATION_SURFACE_UNRESOLVED",
        "MCEL_BROWSER_OBSERVATION_SURFACE_AMBIGUOUS",
        "MCEL_BROWSER_OBSERVATION_SURFACE_ROOT_MISMATCH",
        "MCEL_BROWSER_OBSERVATION_SURFACE_DETACHED",
    ]


def test_invalid_surface_locator_and_unavailable_resolver_are_refused() -> None:
    result = run_node_json(
        """
        const codes = [];

        {
          const fixture = buildFixture();
          fixture.root.ownerDocument.querySelectorAll = () => {
            const error = new Error("bad selector");
            error.name = "SyntaxError";
            throw error;
          };
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            codes.push("NO_ERROR");
          } catch (error) {
            codes.push(error.code);
          }
        }

        {
          const fixture = buildFixture();
          delete fixture.root.ownerDocument.querySelectorAll;
          try {
            producer.captureReadOnlyObservation(captureInput(fixture.root));
            codes.push("NO_ERROR");
          } catch (error) {
            codes.push(error.code);
          }
        }

        process.stdout.write(JSON.stringify({codes}));
        """
    )

    assert result["codes"] == [
        "MCEL_BROWSER_OBSERVATION_SURFACE_LOCATOR_INVALID",
        "MCEL_BROWSER_OBSERVATION_SURFACE_RESOLVER_UNAVAILABLE",
    ]


def test_bounded_capture_is_valid_explicit_and_reproducible() -> None:
    result = run_node_json(
        """
        const limits = {
          maxElements: 3,
          maxDepth: 1,
          maxFactsPerLens: 2,
          maxAttributesPerElement: 1,
          maxAttributeLength: 4,
          maxTextLength: 6,
          maxTotalTextBytes: 48,
          maxStateMarkers: 5
        };
        const firstFixture = buildFixture();
        const secondFixture = buildFixture(true);
        const first = producer.captureReadOnlyObservation(
          captureInput(firstFixture.root, {captureLimits: limits})
        );
        const second = producer.captureReadOnlyObservation(
          captureInput(secondFixture.root, {captureLimits: limits})
        );
        const source = first.provenance.sources[0];
        process.stdout.write(JSON.stringify({
          valid: observations.validateObservationBundle(first).valid,
          firstFingerprint: first.bundleFingerprint,
          secondFingerprint: second.bundleFingerprint,
          capture: source.capture,
          stateMarkers: first.stateMarkers,
          domReason: first.lenses.dom.reason,
          accessibilityReason: first.lenses.accessibility.reason,
          domFactCount: first.lenses.dom.facts.length,
          accessibilityFactCount: first.lenses.accessibility.facts.length,
          rootFact: first.lenses.dom.facts.find(
            (fact) => fact.locator === "#parcel-surface"
          )
        }));
        """
    )

    assert result["valid"] is True
    assert result["firstFingerprint"] == result["secondFingerprint"]
    assert result["capture"]["status"] == "partial"
    assert result["capture"]["capturedElementCount"] == 3
    assert result["capture"]["domFactCount"] == 2
    assert result["capture"]["accessibilityFactCount"] == 2
    assert {
        "max-elements",
        "max-facts-per-lens:dom",
        "max-facts-per-lens:accessibility",
        "max-attributes-per-element",
        "max-attribute-length",
        "max-text-length",
        "max-state-markers",
    }.issubset(set(result["capture"]["partialReasons"]))
    assert result["capture"]["counters"]["attributeEntriesOmitted"] >= 1
    assert result["capture"]["counters"]["attributeValuesTruncated"] >= 1
    assert result["capture"]["counters"]["textValuesTruncated"] >= 1
    assert result["capture"]["counters"]["stateMarkersOmitted"] == 1
    assert len(result["stateMarkers"]) == 5
    assert "capture:partial" in result["stateMarkers"]
    assert result["domReason"] == "bounded-static-dom-snapshot-partial"
    assert result["accessibilityReason"] == "bounded-authored-dom-semantics-partial"
    assert result["domFactCount"] == 2
    assert result["accessibilityFactCount"] == 2
    assert result["rootFact"]["detail"]["capture"]["omittedAttributeCount"] == 1
    assert result["rootFact"]["detail"]["capture"]["textTruncated"] is True


def test_capture_limits_refuse_unknown_invalid_and_over_ceiling_values() -> None:
    result = run_node_json(
        """
        const codes = [];
        for (const captureLimits of [
          {unknownLimit: 1},
          {maxElements: 0},
          {maxElements: producer.HARD_LIMITS.maxElements + 1},
          {maxStateMarkers: 4}
        ]) {
          const fixture = buildFixture();
          try {
            producer.captureReadOnlyObservation(
              captureInput(fixture.root, {captureLimits})
            );
            codes.push("NO_ERROR");
          } catch (error) {
            codes.push(error.code);
          }
        }
        process.stdout.write(JSON.stringify({codes}));
        """
    )

    assert result["codes"] == [
        "MCEL_BROWSER_OBSERVATION_LIMIT_UNKNOWN",
        "MCEL_BROWSER_OBSERVATION_LIMIT_INVALID",
        "MCEL_BROWSER_OBSERVATION_LIMIT_EXCEEDS_HARD_CEILING",
        "MCEL_BROWSER_OBSERVATION_LIMIT_INVALID",
    ]


def test_redaction_is_an_explicit_non_functional_stub() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        const bundle = producer.captureReadOnlyObservation(captureInput(fixture.root));
        process.stdout.write(JSON.stringify({
          policyId: producer.REDACTION_POLICY_ID,
          status: producer.REDACTION_STATUS,
          sourceRedaction: bundle.provenance.sources[0].redaction,
          redactionMarkerPresent: bundle.stateMarkers.includes("redaction:not-implemented")
        }));
        """
    )

    assert result == {
        "policyId": "mcel.redaction-policy.stub.v1",
        "status": "not-implemented",
        "sourceRedaction": {
            "policyId": "mcel.redaction-policy.stub.v1",
            "redactedFactCount": 0,
            "status": "not-implemented",
        },
        "redactionMarkerPresent": True,
    }


def test_custom_static_surface_resolver_can_prove_the_same_exact_root() -> None:
    result = run_node_json(
        """
        const fixture = buildFixture();
        delete fixture.root.ownerDocument.querySelectorAll;
        const bundle = producer.captureReadOnlyObservation(
          captureInput(fixture.root, {
            surfaceResolver(locator, context) {
              return (
                locator === "#parcel-surface" &&
                context.root === fixture.root &&
                context.document === fixture.root.ownerDocument
              ) ? [fixture.root] : [];
            }
          })
        );
        process.stdout.write(JSON.stringify({
          binding: bundle.provenance.sources[0].binding,
          valid: observations.validateObservationBundle(bundle).valid
        }));
        """
    )

    assert result == {
        "binding": {
            "matchCount": 1,
            "resolver": "provided-surface-resolver",
            "status": "validated",
        },
        "valid": True,
    }
