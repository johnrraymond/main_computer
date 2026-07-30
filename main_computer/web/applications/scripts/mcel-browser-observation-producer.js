var McelBrowserObservationProducer = (() => {
  "use strict";

  const PRODUCER_ID = "mcel-browser-observation-producer";
  const COLLECTOR_VERSION = "mcel.browser-observation-producer.dom-a11y-bounded.v2";
  const CAPTURE_POLICY_ID = "mcel.browser-observation.capture-limits.v1";
  const REDACTION_POLICY_ID = "mcel.redaction-policy.stub.v1";
  const REDACTION_STATUS = "not-implemented";
  const READ_ONLY_MODE = "read-only";
  const DEFERRED_REASON = "deferred-from-dom-accessibility-baseline";
  const ACTIVE_EXPLORATION_REASON = "active-exploration-forbidden";
  const SEMANTIC_TAGS = new Set([
    "a",
    "article",
    "aside",
    "button",
    "details",
    "dialog",
    "fieldset",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "input",
    "label",
    "main",
    "nav",
    "option",
    "output",
    "progress",
    "section",
    "select",
    "summary",
    "table",
    "tbody",
    "td",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "tr"
  ]);
  const BOOLEAN_STATE_NAMES = Object.freeze([
    "disabled",
    "checked",
    "selected",
    "expanded",
    "required"
  ]);
  const FORBIDDEN_REQUEST_KEYS = Object.freeze([
    "actions",
    "interaction",
    "mutation",
    "navigation",
    "operations"
  ]);
  const DEFAULT_LIMITS = Object.freeze({
    maxElements: 500,
    maxDepth: 24,
    maxFactsPerLens: 750,
    maxAttributesPerElement: 32,
    maxAttributeLength: 512,
    maxTextLength: 2048,
    maxTotalTextBytes: 65536,
    maxStateMarkers: 32
  });
  const MINIMUM_LIMITS = Object.freeze({
    maxElements: 1,
    maxDepth: 0,
    maxFactsPerLens: 1,
    maxAttributesPerElement: 1,
    maxAttributeLength: 1,
    maxTextLength: 1,
    maxTotalTextBytes: 1,
    maxStateMarkers: 5
  });
  const HARD_LIMITS = Object.freeze({
    maxElements: 5000,
    maxDepth: 100,
    maxFactsPerLens: 5000,
    maxAttributesPerElement: 128,
    maxAttributeLength: 4096,
    maxTextLength: 16384,
    maxTotalTextBytes: 1048576,
    maxStateMarkers: 128
  });
  const LIMIT_KEYS = Object.freeze(Object.keys(DEFAULT_LIMITS));

  function globalApi(name) {
    if (typeof window !== "undefined" && window[name]) return window[name];
    if (typeof globalThis !== "undefined" && globalThis[name]) return globalThis[name];
    return null;
  }

  function observationApi(options) {
    return options?.observationApi || globalApi("McelObservationBundle");
  }

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function clonePlain(value) {
    if (value == null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(clonePlain);
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => typeof entry !== "function" && entry !== undefined)
        .map(([key, entry]) => [key, clonePlain(entry)])
    );
  }

  function canonicalValue(value) {
    if (Array.isArray(value)) return value.map(canonicalValue);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .filter((key) => typeof value[key] !== "function" && value[key] !== undefined)
        .map((key) => [key, canonicalValue(value[key])])
    );
  }

  function canonicalJson(value) {
    return JSON.stringify(canonicalValue(value));
  }

  function stableHash(value) {
    const text = typeof value === "string" ? value : canonicalJson(value);
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }

  function refusal(code, message, detail = {}) {
    const error = new Error(message);
    error.code = code;
    error.detail = canonicalValue(clonePlain(detail));
    throw error;
  }

  function requireString(source, key, code) {
    const value = safeString(source?.[key]);
    if (!value) {
      refusal(code, `Read-only browser observation requires ${key}.`, {key});
    }
    return value;
  }

  function tagNameFor(element) {
    return safeString(element?.tagName || element?.nodeName).toLowerCase();
  }

  function normalizedText(value) {
    return safeString(value).replace(/\s+/g, " ");
  }

  function rawAttributeEntries(element) {
    return Array.from(element?.attributes || [])
      .map((attribute) => [
        safeString(attribute?.name).toLowerCase(),
        safeString(attribute?.value)
      ])
      .filter(([name]) => Boolean(name))
      .sort(([left], [right]) => left.localeCompare(right));
  }

  function childCount(parent) {
    const children = parent?.children;
    const length = Number(children?.length ?? 0);
    return Number.isFinite(length) && length > 0 ? Math.floor(length) : 0;
  }

  function childAt(parent, index) {
    const children = parent?.children;
    if (!children) return null;
    if (typeof children.item === "function") return children.item(index);
    return children[index] || null;
  }

  function elementSegment(element) {
    const tagName = tagNameFor(element);
    const parent = element?.parentElement || null;
    if (!tagName || !parent) return "";
    let sameTagIndex = 0;
    const count = childCount(parent);
    for (let index = 0; index < count; index += 1) {
      const candidate = childAt(parent, index);
      if (tagNameFor(candidate) !== tagName) continue;
      sameTagIndex += 1;
      if (candidate === element) {
        return `${tagName}:nth-of-type(${sameTagIndex})`;
      }
    }
    return "";
  }

  function exactLocatorFor(element, root, surfaceLocator) {
    if (element === root) return surfaceLocator;
    const segments = [];
    let current = element;
    while (current && current !== root) {
      const segment = elementSegment(current);
      if (!segment) return "";
      segments.unshift(segment);
      current = current.parentElement || null;
    }
    if (current !== root) return "";
    return `${surfaceLocator} > ${segments.join(" > ")}`;
  }

  function normalizeLimits(input) {
    if (input !== undefined && (input === null || typeof input !== "object" || Array.isArray(input))) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_LIMITS_INVALID",
        "captureLimits must be an object when supplied."
      );
    }
    const source = input || {};
    const unknownKeys = Object.keys(source).filter((key) => !LIMIT_KEYS.includes(key)).sort();
    if (unknownKeys.length) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_LIMIT_UNKNOWN",
        "captureLimits contains unsupported keys.",
        {unknownKeys}
      );
    }
    const limits = {};
    LIMIT_KEYS.forEach((key) => {
      const supplied = source[key];
      const value = supplied === undefined ? DEFAULT_LIMITS[key] : Number(supplied);
      if (
        !Number.isInteger(value) ||
        value < MINIMUM_LIMITS[key]
      ) {
        refusal(
          "MCEL_BROWSER_OBSERVATION_LIMIT_INVALID",
          "Capture limits must be integers within the supported range.",
          {key, minimum: MINIMUM_LIMITS[key]}
        );
      }
      if (value > HARD_LIMITS[key]) {
        refusal(
          "MCEL_BROWSER_OBSERVATION_LIMIT_EXCEEDS_HARD_CEILING",
          "Requested capture limit exceeds the producer hard ceiling.",
          {key, hardCeiling: HARD_LIMITS[key]}
        );
      }
      limits[key] = value;
    });
    return deepFreeze(limits);
  }

  function createCaptureState(limits) {
    return {
      limits,
      remainingTextBytes: limits.maxTotalTextBytes,
      partialReasons: new Set(),
      counters: {
        attributeEntriesOmitted: 0,
        attributeValuesTruncated: 0,
        textValuesTruncated: 0,
        totalTextByteTruncations: 0,
        stateMarkersOmitted: 0
      }
    };
  }

  function markPartial(state, reason) {
    state.partialReasons.add(reason);
  }

  function utf8ByteLength(value) {
    let length = 0;
    for (const character of String(value)) {
      const codePoint = character.codePointAt(0);
      if (codePoint <= 0x7f) length += 1;
      else if (codePoint <= 0x7ff) length += 2;
      else if (codePoint <= 0xffff) length += 3;
      else length += 4;
    }
    return length;
  }

  function truncateCodePoints(value, maximum) {
    let result = "";
    let count = 0;
    let truncated = false;
    for (const character of String(value)) {
      if (count >= maximum) {
        truncated = true;
        break;
      }
      result += character;
      count += 1;
    }
    return {value: result, truncated};
  }

  function truncateUtf8Bytes(value, maximumBytes) {
    let result = "";
    let bytes = 0;
    let truncated = false;
    for (const character of String(value)) {
      const characterBytes = utf8ByteLength(character);
      if (bytes + characterBytes > maximumBytes) {
        truncated = true;
        break;
      }
      result += character;
      bytes += characterBytes;
    }
    return {value: result, bytes, truncated};
  }

  function boundedString(value, maximumLength, state, kind) {
    const byLength = truncateCodePoints(value, maximumLength);
    if (byLength.truncated) {
      markPartial(state, kind === "attribute" ? "max-attribute-length" : "max-text-length");
      if (kind === "attribute") state.counters.attributeValuesTruncated += 1;
      else state.counters.textValuesTruncated += 1;
    }
    const byBytes = truncateUtf8Bytes(byLength.value, state.remainingTextBytes);
    state.remainingTextBytes -= byBytes.bytes;
    if (byBytes.truncated) {
      markPartial(state, "max-total-text-bytes");
      state.counters.totalTextByteTruncations += 1;
    }
    return {
      value: byBytes.value,
      truncated: byLength.truncated || byBytes.truncated
    };
  }

  function boundedAttributes(element, state) {
    const entries = rawAttributeEntries(element);
    const selected = entries.slice(0, state.limits.maxAttributesPerElement);
    const omitted = Math.max(0, entries.length - selected.length);
    if (omitted) {
      markPartial(state, "max-attributes-per-element");
      state.counters.attributeEntriesOmitted += omitted;
    }
    let truncatedValueCount = 0;
    const bounded = selected.map(([name, value]) => {
      const result = boundedString(value, state.limits.maxAttributeLength, state, "attribute");
      if (result.truncated) truncatedValueCount += 1;
      return [name, result.value];
    });
    return {
      value: Object.fromEntries(bounded),
      detail: {
        omittedAttributeCount: omitted,
        truncatedAttributeValueCount: truncatedValueCount
      }
    };
  }

  function boundedElementSnapshot(element, state) {
    const attributes = boundedAttributes(element, state);
    const text = boundedString(
      normalizedText(element?.textContent),
      state.limits.maxTextLength,
      state,
      "text"
    );
    return {
      tagName: tagNameFor(element),
      attributes: attributes.value,
      text: text.value,
      detail: {
        omittedAttributeCount: attributes.detail.omittedAttributeCount,
        truncatedAttributeValueCount: attributes.detail.truncatedAttributeValueCount,
        textTruncated: text.truncated
      }
    };
  }

  function capturedAttribute(snapshot, name) {
    return safeString(snapshot?.attributes?.[safeString(name).toLowerCase()]);
  }

  function hasCapturedAttribute(snapshot, name) {
    return Object.prototype.hasOwnProperty.call(
      snapshot?.attributes || {},
      safeString(name).toLowerCase()
    );
  }

  function collectBoundedElements(root, limits, state) {
    const entries = [];

    function visit(element, depth) {
      if (!element) return;
      if (entries.length >= limits.maxElements) {
        markPartial(state, "max-elements");
        return;
      }
      entries.push({element, depth});
      const count = childCount(element);
      if (depth >= limits.maxDepth) {
        if (count > 0) markPartial(state, "max-depth");
        return;
      }
      for (let index = 0; index < count; index += 1) {
        if (entries.length >= limits.maxElements) {
          markPartial(state, "max-elements");
          break;
        }
        visit(childAt(element, index), depth + 1);
      }
    }

    visit(root, 0);
    return entries;
  }

  function factFingerprint(kind, locator, value) {
    return `mcel-browser-fact.${stableHash({kind, locator, value})}`;
  }

  function makeFact(lens, kind, locator, value, observedAt, detail = {}) {
    const normalizedValue = canonicalValue(clonePlain(value));
    const fingerprint = factFingerprint(kind, locator, normalizedValue);
    return deepFreeze({
      id: `fact.${lens}.${stableHash({kind, locator})}`,
      kind,
      locator,
      value: normalizedValue,
      observedAt,
      fingerprint,
      detail: canonicalValue(clonePlain(detail))
    });
  }

  function domFactFor(snapshot, locator, observedAt) {
    return makeFact(
      "dom",
      "element",
      locator,
      {
        tagName: snapshot.tagName,
        attributes: snapshot.attributes,
        text: snapshot.text
      },
      observedAt,
      {capture: snapshot.detail}
    );
  }

  function authoredAriaFor(snapshot, state) {
    const aria = {};
    Object.entries(snapshot.attributes || {})
      .filter(([name]) => name.startsWith("aria-"))
      .sort(([left], [right]) => left.localeCompare(right))
      .forEach(([name, value]) => {
        aria[name] = boundedString(
          value,
          state.limits.maxAttributeLength,
          state,
          "attribute"
        ).value;
      });
    return aria;
  }

  function labelsByControlId(entries, locatorByElement, snapshotByElement, state) {
    const labels = new Map();
    entries.forEach(({element}) => {
      const snapshot = snapshotByElement.get(element);
      if (snapshot?.tagName !== "label") return;
      const targetId = capturedAttribute(snapshot, "for");
      if (!targetId) return;
      if (!labels.has(targetId)) labels.set(targetId, []);
      labels.get(targetId).push({
        locator: locatorByElement.get(element) || "",
        text: boundedString(
          snapshot.text,
          state.limits.maxTextLength,
          state,
          "text"
        ).value
      });
    });
    labels.forEach((values) => {
      values.sort((left, right) => left.locator.localeCompare(right.locator));
    });
    return labels;
  }

  function wrappingLabelFor(element, locatorByElement, snapshotByElement, state) {
    let current = element?.parentElement || null;
    while (current) {
      const snapshot = snapshotByElement.get(current);
      if (snapshot?.tagName === "label") {
        return {
          locator: locatorByElement.get(current) || "",
          text: boundedString(
            snapshot.text,
            state.limits.maxTextLength,
            state,
            "text"
          ).value
        };
      }
      current = current.parentElement || null;
    }
    return null;
  }

  function booleanStateFor(element, snapshot, name) {
    const ariaName = `aria-${name}`;
    if (hasCapturedAttribute(snapshot, ariaName)) {
      return capturedAttribute(snapshot, ariaName).toLowerCase();
    }
    if (hasCapturedAttribute(snapshot, name)) return true;
    return element?.[name] === true ? true : null;
  }

  function authoredAccessibilityValue(
    element,
    snapshot,
    labels,
    locatorByElement,
    snapshotByElement,
    state
  ) {
    const id = capturedAttribute(snapshot, "id");
    const associatedLabels = id && labels.has(id) ? labels.get(id) : [];
    const wrappingLabel = wrappingLabelFor(element, locatorByElement, snapshotByElement, state);
    const states = {};
    BOOLEAN_STATE_NAMES.forEach((name) => {
      const value = booleanStateFor(element, snapshot, name);
      if (value !== null && value !== "") states[name] = value;
    });
    return canonicalValue({
      nativeElement: snapshot.tagName,
      explicitRole: boundedString(
        capturedAttribute(snapshot, "role"),
        state.limits.maxAttributeLength,
        state,
        "attribute"
      ).value,
      aria: authoredAriaFor(snapshot, state),
      labels: [
        ...associatedLabels,
        ...(wrappingLabel ? [wrappingLabel] : [])
      ].filter((label, index, values) => (
        label.locator &&
        values.findIndex((candidate) => candidate.locator === label.locator) === index
      )),
      states
    });
  }

  function hasAuthoredAccessibilitySemantics(snapshot, value) {
    if (SEMANTIC_TAGS.has(snapshot.tagName)) return true;
    if (value.explicitRole) return true;
    if (Object.keys(value.aria || {}).length) return true;
    if ((value.labels || []).length) return true;
    return Object.keys(value.states || {}).length > 0;
  }

  function normalizedViewport(source, root) {
    const view = root?.ownerDocument?.defaultView || null;
    const input = source?.viewport && typeof source.viewport === "object" ? source.viewport : {};
    const width = Number(input.width ?? view?.innerWidth ?? 0);
    const height = Number(input.height ?? view?.innerHeight ?? 0);
    const deviceScaleFactor = Number(input.deviceScaleFactor ?? view?.devicePixelRatio ?? 1);
    return {
      width: Number.isFinite(width) ? width : 0,
      height: Number.isFinite(height) ? height : 0,
      deviceScaleFactor: Number.isFinite(deviceScaleFactor) && deviceScaleFactor > 0
        ? deviceScaleFactor
        : 1
    };
  }

  function assertReadOnlyRequest(source) {
    const mode = safeString(source?.mode || READ_ONLY_MODE);
    if (mode !== READ_ONLY_MODE) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_MODE_FORBIDDEN",
        "MCEL browser observation producer permits read-only capture only.",
        {mode}
      );
    }
    const forbiddenKeys = FORBIDDEN_REQUEST_KEYS.filter((key) => source?.[key] !== undefined);
    if (forbiddenKeys.length) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_OPERATION_FORBIDDEN",
        "MCEL browser observation producer does not accept control operations.",
        {forbiddenKeys}
      );
    }
  }

  function validateSurfaceDescriptor(source, appId, route, surfaceId, surfaceLocator) {
    const descriptor = source?.surfaceDescriptor;
    if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor)) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_DESCRIPTOR_MISSING",
        "Read-only browser observation requires a surfaceDescriptor."
      );
    }
    const expected = {
      appId,
      locator: surfaceLocator,
      route,
      surfaceId
    };
    const actual = {
      appId: safeString(descriptor.appId),
      locator: safeString(descriptor.locator),
      route: safeString(descriptor.route),
      surfaceId: safeString(descriptor.surfaceId)
    };
    const missingFields = Object.keys(actual).filter((key) => !actual[key]).sort();
    if (missingFields.length) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_DESCRIPTOR_INCOMPLETE",
        "surfaceDescriptor requires appId, route, surfaceId, and locator.",
        {missingFields}
      );
    }
    const mismatchedFields = Object.keys(expected)
      .filter((key) => actual[key] !== expected[key])
      .sort();
    if (mismatchedFields.length) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_DESCRIPTOR_MISMATCH",
        "surfaceDescriptor does not match the capture identity.",
        {mismatchedFields}
      );
    }
    return deepFreeze(actual);
  }

  function normalizeResolverMatches(result) {
    if (result == null) return [];
    if (Array.isArray(result)) return result.filter(Boolean);
    if (
      typeof result !== "string" &&
      typeof result[Symbol.iterator] === "function"
    ) {
      return Array.from(result).filter(Boolean);
    }
    return [result].filter(Boolean);
  }

  function validateSurfaceBinding(root, surfaceLocator, descriptor, source, options) {
    const document = root?.ownerDocument || null;
    if (!document) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_DOCUMENT_MISSING",
        "Captured surface must belong to a document."
      );
    }
    if (root.isConnected !== true) {
      const contains = document?.documentElement?.contains;
      const contained = typeof contains === "function"
        ? contains.call(document.documentElement, root)
        : false;
      if (!contained) {
        refusal(
          "MCEL_BROWSER_OBSERVATION_SURFACE_DETACHED",
          "Captured surface must be attached to its owner document.",
          {surfaceId: descriptor.surfaceId}
        );
      }
    }

    const suppliedResolver = options?.surfaceResolver || source?.surfaceResolver;
    const documentResolver = typeof document.querySelectorAll === "function"
      ? (locator) => document.querySelectorAll(locator)
      : null;
    const resolver = typeof suppliedResolver === "function"
      ? suppliedResolver
      : documentResolver;
    if (!resolver) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_RESOLVER_UNAVAILABLE",
        "Surface binding requires a deterministic locator resolver."
      );
    }

    let matches;
    try {
      matches = normalizeResolverMatches(
        resolver(surfaceLocator, {
          document,
          root,
          surfaceDescriptor: descriptor
        })
      );
    } catch (error) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_LOCATOR_INVALID",
        "Surface locator could not be resolved.",
        {errorName: safeString(error?.name)}
      );
    }

    if (matches.length === 0) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_UNRESOLVED",
        "Surface locator did not resolve to an attached element.",
        {surfaceId: descriptor.surfaceId, surfaceLocator}
      );
    }
    if (matches.length !== 1) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_AMBIGUOUS",
        "Surface locator must resolve to exactly one element.",
        {matchCount: matches.length, surfaceId: descriptor.surfaceId, surfaceLocator}
      );
    }
    const resolvedRoot = matches[0];
    if (resolvedRoot !== root) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_ROOT_MISMATCH",
        "Surface locator resolved to a different element than the supplied root.",
        {surfaceId: descriptor.surfaceId, surfaceLocator}
      );
    }
    if (resolvedRoot?.ownerDocument !== document) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_SURFACE_DOCUMENT_MISMATCH",
        "Resolved surface does not belong to the supplied root document.",
        {surfaceId: descriptor.surfaceId}
      );
    }

    return deepFreeze({
      matchCount: 1,
      resolver: typeof suppliedResolver === "function"
        ? "provided-surface-resolver"
        : "document.querySelectorAll",
      status: "validated"
    });
  }

  function uniqueSortedStrings(values) {
    return [...new Set((Array.isArray(values) ? values : [])
      .map((value) => safeString(value))
      .filter(Boolean))].sort();
  }

  function boundedStateMarkers(source, requiredMarkers, state) {
    const capacity = state.limits.maxStateMarkers - requiredMarkers.length - 1;
    const requested = uniqueSortedStrings(source?.stateMarkers);
    const selected = requested.slice(0, Math.max(0, capacity));
    const omitted = Math.max(0, requested.length - selected.length);
    if (omitted) {
      markPartial(state, "max-state-markers");
      state.counters.stateMarkersOmitted += omitted;
    }
    const statusMarker = state.partialReasons.size ? "capture:partial" : "capture:complete";
    return [...requiredMarkers, statusMarker, ...selected];
  }

  function captureSummary(state, elementCount, domFactCount, accessibilityFactCount) {
    const reasons = [...state.partialReasons].sort();
    return deepFreeze({
      policyId: CAPTURE_POLICY_ID,
      status: reasons.length ? "partial" : "complete",
      limits: clonePlain(state.limits),
      partialReasons: reasons,
      capturedElementCount: elementCount,
      domFactCount,
      accessibilityFactCount,
      counters: clonePlain(state.counters)
    });
  }

  function redactionStubSummary() {
    return deepFreeze({
      policyId: REDACTION_POLICY_ID,
      status: REDACTION_STATUS,
      redactedFactCount: 0
    });
  }

  function captureReadOnlyObservation(input, options = {}) {
    const source = input && typeof input === "object" ? input : {};
    assertReadOnlyRequest(source);

    const api = observationApi(options);
    if (!api || typeof api.createObservationBundle !== "function") {
      refusal(
        "MCEL_OBSERVATION_BUNDLE_API_UNAVAILABLE",
        "MCEL browser observation producer requires McelObservationBundle."
      );
    }

    const root = source.root;
    if (!root || typeof root !== "object" || !root.children) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_ROOT_INVALID",
        "Read-only browser observation requires a static DOM element root."
      );
    }

    const observationId = requireString(source, "observationId", "MCEL_BROWSER_OBSERVATION_ID_MISSING");
    const appId = requireString(source, "appId", "MCEL_BROWSER_OBSERVATION_APP_ID_MISSING");
    const route = requireString(source, "route", "MCEL_BROWSER_OBSERVATION_ROUTE_MISSING");
    const surfaceId = requireString(source, "surfaceId", "MCEL_BROWSER_OBSERVATION_SURFACE_ID_MISSING");
    const surfaceLocator = requireString(
      source,
      "surfaceLocator",
      "MCEL_BROWSER_OBSERVATION_SURFACE_LOCATOR_MISSING"
    );
    const repositoryFingerprint = requireString(
      source,
      "repositoryFingerprint",
      "MCEL_BROWSER_OBSERVATION_REPOSITORY_FINGERPRINT_MISSING"
    );
    const capturedAt = requireString(source, "capturedAt", "MCEL_BROWSER_OBSERVATION_CAPTURED_AT_MISSING");
    const codeFingerprint = requireString(
      source,
      "codeFingerprint",
      "MCEL_BROWSER_OBSERVATION_CODE_FINGERPRINT_MISSING"
    );

    const descriptor = validateSurfaceDescriptor(
      source,
      appId,
      route,
      surfaceId,
      surfaceLocator
    );
    const binding = validateSurfaceBinding(
      root,
      surfaceLocator,
      descriptor,
      source,
      options
    );
    const limits = normalizeLimits(source.captureLimits);
    const captureState = createCaptureState(limits);
    const entries = collectBoundedElements(root, limits, captureState);
    const locatorByElement = new Map(
      entries.map(({element}) => [
        element,
        exactLocatorFor(element, root, surfaceLocator)
      ])
    );
    const locatedEntries = entries.filter(
      ({element}) => Boolean(locatorByElement.get(element))
    );
    if (locatedEntries.length !== entries.length) {
      refusal(
        "MCEL_BROWSER_OBSERVATION_DESCENDANT_UNBOUND",
        "Every captured descendant must have an exact surface-relative locator.",
        {
          capturedElementCount: entries.length,
          locatedElementCount: locatedEntries.length,
          surfaceId
        }
      );
    }

    const snapshotByElement = new Map();
    locatedEntries.forEach(({element}) => {
      snapshotByElement.set(element, boundedElementSnapshot(element, captureState));
    });

    const domCandidates = locatedEntries.map(({element}) => {
      return domFactFor(
        snapshotByElement.get(element),
        locatorByElement.get(element),
        capturedAt
      );
    });
    if (domCandidates.length > limits.maxFactsPerLens) {
      markPartial(captureState, "max-facts-per-lens:dom");
    }
    const domFacts = domCandidates
      .slice(0, limits.maxFactsPerLens)
      .sort((left, right) => left.id.localeCompare(right.id));

    const labels = labelsByControlId(
      locatedEntries,
      locatorByElement,
      snapshotByElement,
      captureState
    );
    const accessibilityCandidates = locatedEntries
      .map(({element}) => {
        const snapshot = snapshotByElement.get(element);
        const value = authoredAccessibilityValue(
          element,
          snapshot,
          labels,
          locatorByElement,
          snapshotByElement,
          captureState
        );
        return hasAuthoredAccessibilitySemantics(snapshot, value)
          ? makeFact(
            "accessibility",
            "authored-dom-accessibility",
            locatorByElement.get(element),
            value,
            capturedAt
          )
          : null;
      })
      .filter(Boolean);
    if (accessibilityCandidates.length > limits.maxFactsPerLens) {
      markPartial(captureState, "max-facts-per-lens:accessibility");
    }
    const accessibilityFacts = accessibilityCandidates
      .slice(0, limits.maxFactsPerLens)
      .sort((left, right) => left.id.localeCompare(right.id));

    const requiredMarkers = [
      `route:${route}`,
      `surface:${surfaceId}`,
      "capture:dom-accessibility-bounded",
      `redaction:${REDACTION_STATUS}`
    ];
    const stateMarkers = boundedStateMarkers(source, requiredMarkers, captureState);
    const capture = captureSummary(
      captureState,
      locatedEntries.length,
      domFacts.length,
      accessibilityFacts.length
    );
    const redaction = redactionStubSummary();

    return api.createObservationBundle({
      observationId,
      appId,
      route,
      mode: READ_ONLY_MODE,
      capturedAt,
      repositoryFingerprint,
      viewport: normalizedViewport(source, root),
      stateMarkers,
      provenance: {
        producer: PRODUCER_ID,
        collectorVersion: COLLECTOR_VERSION,
        codeFingerprint,
        browser: source.browser || null,
        sources: [
          {
            id: surfaceId,
            kind: "captured-browser-surface",
            appId,
            route,
            locator: surfaceLocator,
            surfaceDescriptor: descriptor,
            binding,
            capture,
            redaction
          }
        ]
      },
      lenses: {
        dom: {
          status: "captured",
          reason: capture.status === "partial"
            ? "bounded-static-dom-snapshot-partial"
            : "bounded-static-dom-snapshot",
          facts: domFacts
        },
        accessibility: {
          status: "captured",
          reason: capture.status === "partial"
            ? "bounded-authored-dom-semantics-partial"
            : "bounded-authored-dom-semantics-only",
          facts: accessibilityFacts
        },
        layout: {
          status: "missing",
          reason: DEFERRED_REASON,
          facts: []
        },
        visual: {
          status: "missing",
          reason: DEFERRED_REASON,
          facts: []
        },
        source: {
          status: "missing",
          reason: DEFERRED_REASON,
          facts: []
        },
        transition: {
          status: "unavailable",
          reason: ACTIVE_EXPLORATION_REASON,
          facts: []
        },
        ridges: {
          status: "missing",
          reason: DEFERRED_REASON,
          facts: []
        }
      },
      claims: []
    });
  }

  return deepFreeze({
    PRODUCER_ID,
    COLLECTOR_VERSION,
    CAPTURE_POLICY_ID,
    REDACTION_POLICY_ID,
    REDACTION_STATUS,
    READ_ONLY_MODE,
    DEFERRED_REASON,
    ACTIVE_EXPLORATION_REASON,
    DEFAULT_LIMITS,
    HARD_LIMITS,
    captureReadOnlyObservation
  });
})();

if (typeof window !== "undefined") {
  window.McelBrowserObservationProducer = McelBrowserObservationProducer;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelBrowserObservationProducer;
}
