var McelLabScm = (() => {
      const CONTRACT_VERSION = "mcel.scm.v1";
      const definitions = new Map();
      const routeDefinitions = new Map();
      let nextInstanceId = 1;
      let nextRouteInstanceId = 1;

      const ROOTS = Object.freeze(["source", "state", "runtime"]);
      const BLOCKED_SEGMENTS = Object.freeze(["__proto__", "prototype", "constructor"]);

      function now() {
        try {
          return new Date().toISOString();
        } catch (_error) {
          return "unknown-time";
        }
      }

      function isPlainObject(value) {
        return Boolean(value) && typeof value === "object" && !Array.isArray(value);
      }

      function cloneValue(value, seen = new Map()) {
        if (!value || typeof value !== "object") return value;
        if (seen.has(value)) return seen.get(value);
        if (Array.isArray(value)) {
          const copy = [];
          seen.set(value, copy);
          value.forEach((item, index) => {
            copy[index] = cloneValue(item, seen);
          });
          return copy;
        }
        const copy = {};
        seen.set(value, copy);
        Object.keys(value).forEach((key) => {
          copy[key] = cloneValue(value[key], seen);
        });
        return copy;
      }

      function jsonSafe(value, seen = new Set()) {
        if (value === undefined || typeof value === "function") return null;
        if (!value || typeof value !== "object") return value;
        if (seen.has(value)) return "[Circular]";
        seen.add(value);
        if (Array.isArray(value)) {
          const copy = value.map((item) => jsonSafe(item, seen));
          seen.delete(value);
          return copy;
        }
        const copy = {};
        Object.keys(value).forEach((key) => {
          const safe = jsonSafe(value[key], seen);
          if (safe !== undefined) copy[key] = safe;
        });
        seen.delete(value);
        return copy;
      }

      function deepFreeze(value, seen = new Set()) {
        if (!value || (typeof value !== "object" && typeof value !== "function")) return value;
        if (seen.has(value)) return value;
        seen.add(value);
        Object.keys(value).forEach((key) => deepFreeze(value[key], seen));
        return Object.freeze(value);
      }

      function safeString(value) {
        return String(value || "").trim();
      }

      function validName(name) {
        return /^[A-Za-z][A-Za-z0-9_.:-]*$/.test(String(name || ""));
      }

      function splitPath(path) {
        const text = safeString(path);
        if (!text || text.startsWith(".") || text.endsWith(".") || text.includes("..")) return null;
        const parts = text.split(".");
        if (parts.some((part) => !part || BLOCKED_SEGMENTS.includes(part))) return null;
        if (parts.some((part) => !/^[A-Za-z0-9_$-]+$/.test(part))) return null;
        return parts;
      }

      function rootForPath(path) {
        const parts = splitPath(path);
        if (!parts || !ROOTS.includes(parts[0]) || parts.length < 2) return null;
        return parts[0];
      }

      function normalizePath(path) {
        const parts = splitPath(path);
        return parts ? parts.join(".") : "";
      }

      function normalizePathList(value) {
        if (!Array.isArray(value)) return [];
        return value.map(normalizePath).filter(Boolean);
      }

      function normalizeOwnedPath(surface, path) {
        const normalized = normalizePath(path);
        if (!normalized) return "";
        const prefix = `${surface}.`;
        return normalized.startsWith(prefix) ? normalized.slice(prefix.length) : normalized;
      }

      function pathMatches(path, declaredPath) {
        return path === declaredPath || path.startsWith(`${declaredPath}.`);
      }

      function isAllowedPath(path, declaredPaths) {
        return declaredPaths.some((declaredPath) => pathMatches(path, declaredPath));
      }

      function ownedPathsFor(manifest, surface) {
        const owns = manifest?.owns || {};
        return Array.isArray(owns[surface])
          ? owns[surface].map((path) => normalizeOwnedPath(surface, path)).filter(Boolean)
          : [];
      }

      function isOwnedWrite(manifest, path) {
        const root = rootForPath(path);
        if (!root) return false;
        const localPath = path.slice(root.length + 1);
        return isAllowedPath(localPath, ownedPathsFor(manifest, root));
      }

      function isOwnedPath(manifest, path) {
        const root = rootForPath(path);
        if (!root) return false;
        const localPath = path.slice(root.length + 1);
        return isAllowedPath(localPath, ownedPathsFor(manifest, root));
      }

      function ownedLayoutSlots(manifest) {
        const owns = manifest?.owns || {};
        return Array.isArray(owns.layout)
          ? owns.layout.map((slot) => safeString(slot)).filter(Boolean)
          : [];
      }

      function transitionTargetName(target) {
        const match = /^transition\.([A-Za-z][A-Za-z0-9_.:-]*)$/.exec(safeString(target));
        return match ? match[1] : "";
      }

      function isValidOutputTarget(target) {
        return Boolean(transitionTargetName(target));
      }

      function declaredOutputs(manifest) {
        if (Array.isArray(manifest?.outputs)) return manifest.outputs.map(safeString).filter(Boolean);
        if (isPlainObject(manifest?.outputs)) return Object.keys(manifest.outputs);
        return [];
      }

      function violation(code, details = {}) {
        const transitionName = details.transitionName || "";
        const path = details.path || "";
        const componentName = details.componentName || "";
        const message = details.message || defaultViolationMessage(code, componentName, transitionName, path, details);
        return {
          kind: "mcel-scm-violation",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          severity: details.severity || "blocking",
          phase: details.phase || "unknown",
          code,
          componentName,
          transitionName,
          path,
          message,
          ...details,
          ok: false
        };
      }

      function defaultViolationMessage(code, componentName, transitionName, path, details) {
        if (code === "SCM_UNDECLARED_READ") {
          return `Transition ${transitionName} attempted to read ${path} without declaring it.`;
        }
        if (code === "SCM_UNDECLARED_WRITE") {
          return `Transition ${transitionName} attempted to write ${path} without declaring it.`;
        }
        if (code === "SCM_WRITE_OUTSIDE_OWNERSHIP") {
          return `Transition ${transitionName || details.transition || ""} declares a write outside component ownership.`;
        }
        if (code === "SCM_MISSING_OWNERSHIP") {
          return `Component ${componentName} is missing owns.`;
        }
        if (code === "SCM_EMPTY_OWNERSHIP") {
          return `Component ${componentName} must own at least one source, runtime, or state path.`;
        }
        if (code === "SCM_DUPLICATE_COMPONENT") {
          return `Component ${componentName} is already defined. Pass replace:true to replace it.`;
        }
        if (code === "SCM_CHILD_OUTPUT_TARGET_MISSING") {
          return `Child ${details.childName || ""} output ${details.outputName || ""} targets a missing transition.`;
        }
        if (code === "SCM_CHILD_INPUT_TARGET_UNOWNED") {
          return `Child ${details.childName || ""} input ${details.inputName || ""} reads outside component ownership.`;
        }
        if (code === "SCM_CHILD_UNDECLARED_MUTATION") {
          return `Child ${details.childName || ""} attempted to mutate ${path} without declaring it.`;
        }
        if (code === "SCM_ROUTE_PATH_NOT_STRUCTURED") {
          return `Route ${details.routeName || ""} must use structured segments, not a string path.`;
        }
        if (code === "SCM_ROUTE_MISSING_SEGMENTS") {
          return `Route ${details.routeName || ""} must declare structured segments.`;
        }
        if (code === "SCM_UNKNOWN_ROUTE") {
          return `Unknown SCM route ${details.routeName || ""}.`;
        }
        if (code === "SCM_ROUTE_PARAM_MISSING") {
          return `Route ${details.routeName || ""} is missing required param ${details.paramName || ""}.`;
        }
        if (code === "SCM_ROUTE_PARAM_INVALID") {
          return `Route ${details.routeName || ""} received invalid param ${details.paramName || ""}.`;
        }
        if (code === "SCM_ROUTE_QUERY_INVALID") {
          return `Route ${details.routeName || ""} received invalid query value ${details.queryName || ""}.`;
        }
        if (code === "SCM_ROUTE_LEAVE_BLOCKED") {
          return `Route ${details.routeName || ""} blocked navigation because dirty state is present.`;
        }
        return details.message || `SCM violation ${code}.`;
      }

      function throwViolation(entry, instance = null) {
        if (instance) recordEvidence(instance, entry);
        const error = new Error(entry.message);
        error.name = "McelScmViolationError";
        error.violation = jsonSafe(entry);
        throw error;
      }

      function recordEvidence(instance, entry) {
        if (!instance || !Array.isArray(instance.evidence)) return entry;
        instance.evidence.push(jsonSafe(entry));
        return entry;
      }

      function validatePathList(componentName, transitionName, label, paths, issues) {
        if (!Array.isArray(paths)) {
          issues.push(violation("SCM_TRANSITION_MISSING_PATH_LIST", {
            phase: "validate-manifest",
            componentName,
            transitionName,
            property: label,
            message: `Transition ${transitionName} must declare ${label} as an array.`
          }));
          return [];
        }
        return paths.map((path) => {
          const normalized = normalizePath(path);
          if (!normalized || !rootForPath(normalized)) {
            issues.push(violation("SCM_INVALID_PATH", {
              phase: "validate-manifest",
              componentName,
              transitionName,
              path: safeString(path),
              property: label,
              message: `Transition ${transitionName} declares invalid ${label} path ${safeString(path)}.`
            }));
          }
          return normalized;
        }).filter(Boolean);
      }

      function ownedEffectNames(manifest) {
        const owns = manifest?.owns || {};
        return Array.isArray(owns.effects)
          ? owns.effects.map((name) => safeString(name)).filter(Boolean)
          : [];
      }

      function effectKind(spec) {
        return safeString(spec?.kind || "effect");
      }

      function isAsyncEffect(spec) {
        const kind = effectKind(spec).toLowerCase();
        return spec?.async === true || kind.startsWith("async");
      }

      function validateComponentEffectPathList(componentName, effectName, label, paths, issues) {
        if (!Array.isArray(paths)) {
          issues.push(violation("SCM_EFFECT_MISSING_PATH_LIST", {
            phase: "validate-manifest",
            componentName,
            effectName,
            property: label,
            message: `Effect ${effectName} must declare ${label} as an array.`
          }));
          return [];
        }
        return paths.map((path) => {
          const normalized = normalizePath(path);
          if (!normalized || !rootForPath(normalized)) {
            issues.push(violation("SCM_EFFECT_INVALID_PATH", {
              phase: "validate-manifest",
              componentName,
              effectName,
              path: safeString(path),
              property: label,
              message: `Effect ${effectName} declares invalid ${label} path ${safeString(path)}.`
            }));
          }
          return normalized;
        }).filter(Boolean);
      }

      function validateExternalDeclaration(owner, effectName, spec, issues, details = {}) {
        if (!isPlainObject(spec.external) || !safeString(spec.external.resource) || !safeString(spec.external.operation)) {
          issues.push(violation("SCM_EFFECT_EXTERNAL_MISSING", {
            phase: details.phase || "validate-manifest",
            componentName: details.componentName || "",
            routeName: details.routeName || "",
            loaderName: details.loaderName || "",
            effectName,
            owner,
            message: `${owner} effect ${effectName} must declare external.resource and external.operation.`
          }));
        }
      }

      function validateErrorPolicy(owner, effectName, spec, issues, details = {}) {
        if (!isPlainObject(spec.errorPolicy) || !safeString(spec.errorPolicy.onFailure)) {
          issues.push(violation("SCM_EFFECT_ERROR_POLICY_MISSING", {
            phase: details.phase || "validate-manifest",
            componentName: details.componentName || "",
            routeName: details.routeName || "",
            loaderName: details.loaderName || "",
            effectName,
            owner,
            message: `${owner} effect ${effectName} must declare errorPolicy.onFailure.`
          }));
        }
      }

      function validateAsyncEffectPolicy(owner, effectName, spec, issues, details = {}) {
        if (!safeString(spec.kind)) {
          issues.push(violation("SCM_EFFECT_MISSING_KIND", {
            phase: details.phase || "validate-manifest",
            componentName: details.componentName || "",
            routeName: details.routeName || "",
            loaderName: details.loaderName || "",
            effectName,
            owner,
            message: `${owner} effect ${effectName} must declare kind.`
          }));
        }

        validateExternalDeclaration(owner, effectName, spec, issues, details);
        validateErrorPolicy(owner, effectName, spec, issues, details);

        if (isAsyncEffect(spec)) {
          if (!safeString(spec.cancellation)) {
            issues.push(violation("SCM_EFFECT_MISSING_CANCELLATION", {
              phase: details.phase || "validate-manifest",
              componentName: details.componentName || "",
              routeName: details.routeName || "",
              loaderName: details.loaderName || "",
              effectName,
              owner,
              message: `${owner} async effect ${effectName} must declare cancellation.`
            }));
          }
          if (!safeString(spec.racePolicy)) {
            issues.push(violation("SCM_EFFECT_MISSING_RACE_POLICY", {
              phase: details.phase || "validate-manifest",
              componentName: details.componentName || "",
              routeName: details.routeName || "",
              loaderName: details.loaderName || "",
              effectName,
              owner,
              message: `${owner} async effect ${effectName} must declare racePolicy.`
            }));
          }
        }
      }

      function validateComponentEffects(componentName, manifest, issues) {
        if (manifest.effects === undefined) {
          const declared = ownedEffectNames(manifest);
          if (declared.length > 0) {
            declared.forEach((effectName) => {
              issues.push(violation("SCM_EFFECT_DECLARED_BUT_MISSING", {
                phase: "validate-manifest",
                componentName,
                effectName,
                message: `Component ${componentName} owns effect ${effectName} but does not define an effect contract.`
              }));
            });
          }
          return;
        }

        if (!isPlainObject(manifest.effects)) {
          issues.push(violation("SCM_INVALID_EFFECTS", {
            phase: "validate-manifest",
            componentName,
            message: `Component ${componentName} effects must be an object when provided.`
          }));
          return;
        }

        const declared = ownedEffectNames(manifest);
        const effectNames = Object.keys(manifest.effects);
        declared.forEach((effectName) => {
          if (!Object.prototype.hasOwnProperty.call(manifest.effects, effectName)) {
            issues.push(violation("SCM_EFFECT_DECLARED_BUT_MISSING", {
              phase: "validate-manifest",
              componentName,
              effectName,
              message: `Component ${componentName} owns effect ${effectName} but does not define it.`
            }));
          }
        });

        effectNames.forEach((effectName) => {
          const spec = manifest.effects[effectName];
          if (!validName(effectName) || !isPlainObject(spec)) {
            issues.push(violation("SCM_INVALID_EFFECT", {
              phase: "validate-manifest",
              componentName,
              effectName,
              message: `Effect ${effectName} must be a named object.`
            }));
            return;
          }

          if (!declared.includes(effectName)) {
            issues.push(violation("SCM_EFFECT_OUTSIDE_OWNERSHIP", {
              phase: "validate-manifest",
              componentName,
              effectName,
              declaredEffects: declared,
              message: `Component ${componentName} defines effect ${effectName} without owns.effects permission.`
            }));
          }

          const triggers = validateComponentEffectPathList(componentName, effectName, "triggers", spec.triggers, issues);
          const reads = validateComponentEffectPathList(componentName, effectName, "reads", spec.reads, issues);
          const writes = validateComponentEffectPathList(componentName, effectName, "writes", spec.writes, issues);
          void triggers;
          void reads;

          writes.forEach((path) => {
            if (!isOwnedWrite(manifest, path)) {
              issues.push(violation("SCM_EFFECT_WRITE_OUTSIDE_OWNERSHIP", {
                phase: "validate-manifest",
                componentName,
                effectName,
                path,
                declaredWrites: writes,
                ownedSource: ownedPathsFor(manifest, "source"),
                ownedState: ownedPathsFor(manifest, "state"),
                ownedRuntime: ownedPathsFor(manifest, "runtime")
              }));
            }
          });

          validateAsyncEffectPolicy("component", effectName, spec, issues, {
            phase: "validate-manifest",
            componentName
          });
        });
      }

      function validateChildComposition(componentName, manifest, transitionNames, issues) {
        if (manifest.children === undefined) return;
        if (!isPlainObject(manifest.children)) {
          issues.push(violation("SCM_INVALID_CHILDREN", {
            phase: "validate-manifest",
            componentName,
            message: `Component ${componentName} children must be an object when provided.`
          }));
          return;
        }

        const layoutSlots = ownedLayoutSlots(manifest);
        Object.keys(manifest.children).forEach((childName) => {
          const child = manifest.children[childName];

          if (!validName(childName) || !isPlainObject(child)) {
            issues.push(violation("SCM_INVALID_CHILD", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} must be a named object.`
            }));
            return;
          }

          if (child.component !== undefined && !validName(child.component)) {
            issues.push(violation("SCM_INVALID_CHILD_COMPONENT", {
              phase: "validate-manifest",
              componentName,
              childName,
              childComponent: safeString(child.component),
              message: `Child ${childName} has an invalid component name ${safeString(child.component)}.`
            }));
          }

          const slot = safeString(child.slot);
          if (!slot) {
            issues.push(violation("SCM_CHILD_MISSING_SLOT", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} must declare a layout slot.`
            }));
          } else if (layoutSlots.length && !layoutSlots.includes(slot)) {
            issues.push(violation("SCM_CHILD_SLOT_OUTSIDE_LAYOUT", {
              phase: "validate-manifest",
              componentName,
              childName,
              slot,
              declaredLayoutSlots: layoutSlots,
              message: `Child ${childName} uses slot ${slot} outside declared layout ownership.`
            }));
          }

          if (!isPlainObject(child.inputs)) {
            issues.push(violation("SCM_CHILD_INPUTS_NOT_OBJECT", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} inputs must be an object.`
            }));
          } else {
            Object.keys(child.inputs).forEach((inputName) => {
              const target = normalizePath(child.inputs[inputName]);
              if (!validName(inputName)) {
                issues.push(violation("SCM_INVALID_CHILD_INPUT", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  inputName,
                  message: `Child ${childName} input ${inputName} is not a valid input name.`
                }));
              }
              if (!target || !rootForPath(target)) {
                issues.push(violation("SCM_CHILD_INPUT_TARGET_INVALID", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  inputName,
                  target: safeString(child.inputs[inputName]),
                  message: `Child ${childName} input ${inputName} targets invalid path ${safeString(child.inputs[inputName])}.`
                }));
              } else if (!isOwnedPath(manifest, target)) {
                issues.push(violation("SCM_CHILD_INPUT_TARGET_UNOWNED", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  inputName,
                  target,
                  ownedSource: ownedPathsFor(manifest, "source"),
                  ownedState: ownedPathsFor(manifest, "state"),
                  ownedRuntime: ownedPathsFor(manifest, "runtime")
                }));
              }
            });
          }

          if (!isPlainObject(child.outputs)) {
            issues.push(violation("SCM_CHILD_OUTPUTS_NOT_OBJECT", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} outputs must be an object.`
            }));
          } else {
            Object.keys(child.outputs).forEach((outputName) => {
              const target = safeString(child.outputs[outputName]);
              const transitionName = transitionTargetName(target);
              if (!validName(outputName)) {
                issues.push(violation("SCM_INVALID_CHILD_OUTPUT", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  outputName,
                  message: `Child ${childName} output ${outputName} is not a valid output name.`
                }));
              }
              if (!isValidOutputTarget(target)) {
                issues.push(violation("SCM_CHILD_OUTPUT_TARGET_INVALID", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  outputName,
                  target,
                  message: `Child ${childName} output ${outputName} must target transition.<name>.`
                }));
              } else if (!transitionNames.includes(transitionName)) {
                issues.push(violation("SCM_CHILD_OUTPUT_TARGET_MISSING", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  outputName,
                  target,
                  transitionName,
                  message: `Child ${childName} output ${outputName} targets missing transition ${transitionName}.`
                }));
              }
            });
          }

          if (!Array.isArray(child.mayMutate)) {
            issues.push(violation("SCM_CHILD_MAY_MUTATE_NOT_ARRAY", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} mayMutate must be an array.`
            }));
          } else {
            child.mayMutate.forEach((path) => {
              const normalized = normalizePath(path);
              if (!normalized || !rootForPath(normalized)) {
                issues.push(violation("SCM_CHILD_MUTATION_PATH_INVALID", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  path: safeString(path),
                  message: `Child ${childName} declares invalid mutation path ${safeString(path)}.`
                }));
              } else if (!isOwnedWrite(manifest, normalized)) {
                issues.push(violation("SCM_CHILD_MUTATION_OUTSIDE_OWNERSHIP", {
                  phase: "validate-manifest",
                  componentName,
                  childName,
                  path: normalized,
                  ownedSource: ownedPathsFor(manifest, "source"),
                  ownedState: ownedPathsFor(manifest, "state"),
                  ownedRuntime: ownedPathsFor(manifest, "runtime")
                }));
              }
            });
          }

          if (typeof child.maySerialize !== "boolean") {
            issues.push(violation("SCM_CHILD_MAY_SERIALIZE_NOT_BOOLEAN", {
              phase: "validate-manifest",
              componentName,
              childName,
              message: `Child ${childName} maySerialize must be a boolean.`
            }));
          }
        });
      }

      const ROUTE_SCHEMA_TYPES = Object.freeze(["id", "string", "integer", "boolean", "enum"]);
      const ROUTE_BUILTIN_ENTER_STEPS = Object.freeze(["validateParams", "checkPermissions", "mountComponent"]);

      function routeParamNames(manifest) {
        const names = [];
        if (!Array.isArray(manifest?.segments)) return names;
        manifest.segments.forEach((segment) => {
          const name = safeString(segment?.param);
          if (name && !names.includes(name)) names.push(name);
        });
        return names;
      }

      function routeQueryNames(manifest) {
        return isPlainObject(manifest?.query) ? Object.keys(manifest.query) : [];
      }

      function routeDataOutputPaths(manifest) {
        const paths = [];
        if (!isPlainObject(manifest?.data)) return paths;
        Object.keys(manifest.data).forEach((loaderName) => {
          const loader = manifest.data[loaderName];
          if (Array.isArray(loader?.writes)) {
            loader.writes.forEach((path) => {
              const normalized = normalizeRoutePath(path);
              if (normalized && normalized.startsWith("route.data.") && !paths.includes(normalized)) {
                paths.push(normalized);
              }
            });
          }
        });
        return paths;
      }

      function normalizeRoutePath(path) {
        const parts = splitPath(path);
        if (!parts || parts.length < 3) return "";
        if (parts[0] === "route" && ["params", "query", "data"].includes(parts[1])) return parts.join(".");
        if (parts[0] === "component" && ["source", "state", "runtime"].includes(parts[1])) return parts.join(".");
        return "";
      }

      function componentPathFromRoutePath(path) {
        const normalized = normalizeRoutePath(path);
        if (!normalized || !normalized.startsWith("component.")) return "";
        return normalized.slice("component.".length);
      }

      function routePathMatches(path, declaredPath) {
        return path === declaredPath || path.startsWith(`${declaredPath}.`);
      }

      function isAllowedRoutePath(path, declaredPaths) {
        return declaredPaths.some((declaredPath) => routePathMatches(path, declaredPath));
      }

      function schemaType(schema) {
        return safeString(schema?.type || "string");
      }

      function validIdValue(value) {
        return /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(String(value || ""));
      }

      function schemaAcceptsValue(schema, value) {
        const type = schemaType(schema);
        if (value === undefined || value === null || value === "") {
          return schema?.required === true || schema?.optional !== true ? value !== undefined && value !== null && value !== "" : true;
        }
        if (type === "id") return validIdValue(value);
        if (type === "string") return typeof value === "string";
        if (type === "integer") return Number.isInteger(typeof value === "number" ? value : Number(value));
        if (type === "boolean") return typeof value === "boolean" || value === "true" || value === "false";
        if (type === "enum") return Array.isArray(schema?.values) && schema.values.includes(value);
        return false;
      }

      function coerceSchemaValue(schema, value) {
        const type = schemaType(schema);
        if (value === undefined || value === null || value === "") return value;
        if (type === "integer") return Number(value);
        if (type === "boolean") {
          if (value === true || value === "true") return true;
          if (value === false || value === "false") return false;
        }
        return value;
      }

      function validateSchemaObject(routeName, schemaName, schema, issues) {
        if (!isPlainObject(schema)) {
          issues.push(violation("SCM_ROUTE_SCHEMA_INVALID", {
            phase: "validate-route",
            routeName,
            schemaName,
            message: `Route ${routeName} schema ${schemaName} must be an object.`
          }));
          return;
        }

        const type = schemaType(schema);
        if (!ROUTE_SCHEMA_TYPES.includes(type)) {
          issues.push(violation("SCM_ROUTE_SCHEMA_TYPE_INVALID", {
            phase: "validate-route",
            routeName,
            schemaName,
            type,
            message: `Route ${routeName} schema ${schemaName} has invalid type ${type}.`
          }));
        }

        if (type === "enum") {
          if (!Array.isArray(schema.values) || schema.values.length === 0 || schema.values.some((value) => typeof value !== "string")) {
            issues.push(violation("SCM_ROUTE_ENUM_VALUES_INVALID", {
              phase: "validate-route",
              routeName,
              schemaName,
              message: `Route ${routeName} enum schema ${schemaName} must declare string values.`
            }));
          }
        }

        if (schema.default !== undefined && !schemaAcceptsValue({...schema, optional: true, required: false}, schema.default)) {
          issues.push(violation("SCM_ROUTE_SCHEMA_DEFAULT_INVALID", {
            phase: "validate-route",
            routeName,
            schemaName,
            value: schema.default,
            message: `Route ${routeName} schema ${schemaName} has an invalid default.`
          }));
        }
      }

      function validateRouteReferencePath(routeName, manifest, path, issues, details = {}) {
        const normalized = normalizeRoutePath(path);
        if (!normalized) {
          issues.push(violation("SCM_ROUTE_PATH_INVALID", {
            phase: "validate-route",
            routeName,
            path: safeString(path),
            property: details.property || "",
            message: `Route ${routeName} references invalid path ${safeString(path)}.`
          }));
          return "";
        }

        const parts = normalized.split(".");
        const paramNames = routeParamNames(manifest);
        const queryNames = routeQueryNames(manifest);
        const mountedComponentName = safeString(manifest?.mounts?.component);
        const mountedComponent = mountedComponentName ? componentDefinition(mountedComponentName) : null;

        if (parts[0] === "route" && parts[1] === "params" && !paramNames.includes(parts[2])) {
          issues.push(violation("SCM_ROUTE_PARAM_REFERENCE_MISSING", {
            phase: "validate-route",
            routeName,
            path: normalized,
            paramName: parts[2],
            declaredParams: paramNames,
            message: `Route ${routeName} references missing param ${parts[2]}.`
          }));
        }

        if (parts[0] === "route" && parts[1] === "query" && !queryNames.includes(parts[2])) {
          issues.push(violation("SCM_ROUTE_QUERY_REFERENCE_MISSING", {
            phase: "validate-route",
            routeName,
            path: normalized,
            queryName: parts[2],
            declaredQuery: queryNames,
            message: `Route ${routeName} references missing query key ${parts[2]}.`
          }));
        }

        if (parts[0] === "route" && parts[1] === "data" && details.requireDeclaredData === true) {
          const dataPaths = routeDataOutputPaths(manifest);
          if (!dataPaths.some((declaredPath) => routePathMatches(normalized, declaredPath) || routePathMatches(declaredPath, normalized))) {
            issues.push(violation("SCM_ROUTE_DATA_REFERENCE_MISSING", {
              phase: "validate-route",
              routeName,
              path: normalized,
              declaredData: dataPaths,
              message: `Route ${routeName} references undeclared data path ${normalized}.`
            }));
          }
        }

        if (parts[0] === "component") {
          const componentPath = componentPathFromRoutePath(normalized);
          if (!mountedComponent) {
            issues.push(violation("SCM_ROUTE_MOUNT_COMPONENT_MISSING", {
              phase: "validate-route",
              routeName,
              componentName: mountedComponentName,
              path: normalized,
              message: `Route ${routeName} references component path ${normalized} without a defined mounted component.`
            }));
          } else if (!isOwnedPath(mountedComponent, componentPath)) {
            issues.push(violation("SCM_ROUTE_COMPONENT_PATH_UNOWNED", {
              phase: "validate-route",
              routeName,
              componentName: mountedComponentName,
              path: normalized,
              componentPath,
              message: `Route ${routeName} references component path ${normalized} outside ${mountedComponentName} ownership.`
            }));
          }
        }

        return normalized;
      }

      function validateRoutePathList(routeName, manifest, label, paths, issues, details = {}) {
        if (!Array.isArray(paths)) {
          issues.push(violation("SCM_ROUTE_MISSING_PATH_LIST", {
            phase: "validate-route",
            routeName,
            property: label,
            loaderName: details.loaderName || "",
            message: `Route ${routeName} must declare ${label} as an array.`
          }));
          return [];
        }
        return paths.map((path) => validateRouteReferencePath(routeName, manifest, path, issues, {...details, property: label})).filter(Boolean);
      }

      function validateRouteLoaderEffectContract(routeName, manifest, loaderName, loader, issues) {
        validateRoutePathList(routeName, manifest, "triggers", loader.triggers, issues, {loaderName});
        const writes = validateRoutePathList(routeName, manifest, "writes", loader.writes, issues, {loaderName});
        writes.forEach((path) => {
          if (!path.startsWith("route.data.")) {
            issues.push(violation("SCM_ROUTE_LOADER_WRITE_TARGET_INVALID", {
              phase: "validate-route",
              routeName,
              loaderName,
              path,
              message: `Route ${routeName} loader ${loaderName} may only write route.data.* paths in Patch 5.`
            }));
          }
        });

        validateAsyncEffectPolicy("route-loader", loaderName, loader, issues, {
          phase: "validate-route",
          routeName,
          loaderName
        });
      }

      function validateRouteManifest(name, manifest) {
        const routeName = safeString(name);
        const issues = [];

        if (!validName(routeName)) {
          issues.push(violation("SCM_INVALID_ROUTE_NAME", {
            phase: "validate-route",
            routeName,
            message: `Invalid SCM route name ${String(name)}.`
          }));
        }

        if (!isPlainObject(manifest)) {
          issues.push(violation("SCM_INVALID_ROUTE_MANIFEST", {
            phase: "validate-route",
            routeName,
            message: `Route ${routeName} manifest must be an object.`
          }));
          return {
            kind: "mcel-scm-route-validation",
            contractVersion: CONTRACT_VERSION,
            generatedAt: now(),
            routeName,
            ok: false,
            issues: issues.map((issue) => jsonSafe(issue))
          };
        }

        if (manifest.path !== undefined) {
          issues.push(violation("SCM_ROUTE_PATH_NOT_STRUCTURED", {
            phase: "validate-route",
            routeName,
            path: safeString(manifest.path),
            message: `Route ${routeName} must declare canonical structured segments instead of path strings.`
          }));
        }

        if (!Array.isArray(manifest.segments) || manifest.segments.length === 0) {
          issues.push(violation("SCM_ROUTE_MISSING_SEGMENTS", {
            phase: "validate-route",
            routeName
          }));
        } else {
          const seenParams = new Set();
          manifest.segments.forEach((segment, index) => {
            if (!isPlainObject(segment)) {
              issues.push(violation("SCM_ROUTE_SEGMENT_INVALID", {
                phase: "validate-route",
                routeName,
                segmentIndex: index,
                message: `Route ${routeName} segment ${index} must be an object.`
              }));
              return;
            }

            const hasLiteral = Object.prototype.hasOwnProperty.call(segment, "literal");
            const hasParam = Object.prototype.hasOwnProperty.call(segment, "param");
            if (hasLiteral === hasParam) {
              issues.push(violation("SCM_ROUTE_SEGMENT_SHAPE_INVALID", {
                phase: "validate-route",
                routeName,
                segmentIndex: index,
                message: `Route ${routeName} segment ${index} must declare exactly one of literal or param.`
              }));
              return;
            }

            if (hasLiteral) {
              const literal = safeString(segment.literal);
              if (!literal || literal.includes("/") || literal.startsWith(":") || literal.includes("{") || literal.includes("}")) {
                issues.push(violation("SCM_ROUTE_LITERAL_INVALID", {
                  phase: "validate-route",
                  routeName,
                  segmentIndex: index,
                  literal,
                  message: `Route ${routeName} literal segment ${index} must be a plain literal.`
                }));
              }
              return;
            }

            const paramName = safeString(segment.param);
            if (!validName(paramName)) {
              issues.push(violation("SCM_ROUTE_PARAM_INVALID_NAME", {
                phase: "validate-route",
                routeName,
                segmentIndex: index,
                paramName,
                message: `Route ${routeName} param segment ${index} has invalid name ${paramName}.`
              }));
            }
            if (seenParams.has(paramName)) {
              issues.push(violation("SCM_ROUTE_PARAM_DUPLICATE", {
                phase: "validate-route",
                routeName,
                segmentIndex: index,
                paramName,
                message: `Route ${routeName} declares duplicate param ${paramName}.`
              }));
            }
            seenParams.add(paramName);
            validateSchemaObject(routeName, `params.${paramName}`, {
              type: segment.type || "id",
              required: segment.required !== false
            }, issues);
          });
        }

        if (!isPlainObject(manifest.query)) {
          issues.push(violation("SCM_ROUTE_QUERY_SCHEMA_MISSING", {
            phase: "validate-route",
            routeName,
            message: `Route ${routeName} must declare query as an object.`
          }));
        } else {
          Object.keys(manifest.query).forEach((queryName) => {
            if (!validName(queryName)) {
              issues.push(violation("SCM_ROUTE_QUERY_NAME_INVALID", {
                phase: "validate-route",
                routeName,
                queryName,
                message: `Route ${routeName} has invalid query key ${queryName}.`
              }));
            }
            validateSchemaObject(routeName, `query.${queryName}`, manifest.query[queryName], issues);
          });
        }

        if (!isPlainObject(manifest.mounts)) {
          issues.push(violation("SCM_ROUTE_MOUNTS_MISSING", {
            phase: "validate-route",
            routeName,
            message: `Route ${routeName} must declare mounts.`
          }));
        } else {
          const componentName = safeString(manifest.mounts.component);
          if (!validName(componentName)) {
            issues.push(violation("SCM_ROUTE_MOUNT_COMPONENT_INVALID", {
              phase: "validate-route",
              routeName,
              componentName,
              message: `Route ${routeName} has invalid mount component ${componentName}.`
            }));
          } else if (!componentDefinition(componentName)) {
            issues.push(violation("SCM_ROUTE_MOUNT_COMPONENT_UNKNOWN", {
              phase: "validate-route",
              routeName,
              componentName,
              message: `Route ${routeName} mounts unknown component ${componentName}.`
            }));
          }

          if (!isPlainObject(manifest.mounts.inputs)) {
            issues.push(violation("SCM_ROUTE_MOUNT_INPUTS_INVALID", {
              phase: "validate-route",
              routeName,
              message: `Route ${routeName} mount inputs must be an object.`
            }));
          } else {
            Object.keys(manifest.mounts.inputs).forEach((inputName) => {
              if (!validName(inputName)) {
                issues.push(violation("SCM_ROUTE_MOUNT_INPUT_INVALID", {
                  phase: "validate-route",
                  routeName,
                  inputName,
                  message: `Route ${routeName} mount input ${inputName} is invalid.`
                }));
              }
              validateRouteReferencePath(routeName, manifest, manifest.mounts.inputs[inputName], issues, {
                property: `mounts.inputs.${inputName}`,
                requireDeclaredData: true
              });
            });
          }
        }

        const data = manifest.data;
        if (data !== undefined && !isPlainObject(data)) {
          issues.push(violation("SCM_ROUTE_DATA_INVALID", {
            phase: "validate-route",
            routeName,
            message: `Route ${routeName} data must be an object when provided.`
          }));
        }

        const loaderNames = isPlainObject(data) ? Object.keys(data) : [];
        loaderNames.forEach((loaderName) => {
          const loader = data[loaderName];
          if (!validName(loaderName) || !isPlainObject(loader)) {
            issues.push(violation("SCM_ROUTE_LOADER_INVALID", {
              phase: "validate-route",
              routeName,
              loaderName,
              message: `Route ${routeName} loader ${loaderName} must be a named object.`
            }));
            return;
          }

          validateRoutePathList(routeName, manifest, "reads", loader.reads, issues, {loaderName});
          validateRouteLoaderEffectContract(routeName, manifest, loaderName, loader, issues);
        });

        if (manifest.lifecycle !== undefined && !isPlainObject(manifest.lifecycle)) {
          issues.push(violation("SCM_ROUTE_LIFECYCLE_INVALID", {
            phase: "validate-route",
            routeName,
            message: `Route ${routeName} lifecycle must be an object when provided.`
          }));
        } else if (isPlainObject(manifest.lifecycle)) {
          const onEnter = manifest.lifecycle.onEnter;
          if (onEnter !== undefined) {
            if (!Array.isArray(onEnter)) {
              issues.push(violation("SCM_ROUTE_ON_ENTER_INVALID", {
                phase: "validate-route",
                routeName,
                message: `Route ${routeName} lifecycle.onEnter must be an array.`
              }));
            } else {
              onEnter.forEach((step) => {
                const name = safeString(step);
                if (!ROUTE_BUILTIN_ENTER_STEPS.includes(name) && !loaderNames.includes(name)) {
                  issues.push(violation("SCM_ROUTE_ON_ENTER_STEP_UNKNOWN", {
                    phase: "validate-route",
                    routeName,
                    step: name,
                    knownLoaders: loaderNames,
                    message: `Route ${routeName} lifecycle.onEnter references unknown step ${name}.`
                  }));
                }
              });
            }
          }

          if (manifest.lifecycle.onLeave !== undefined) {
            const onLeave = manifest.lifecycle.onLeave;
            if (!isPlainObject(onLeave)) {
              issues.push(violation("SCM_ROUTE_ON_LEAVE_INVALID", {
                phase: "validate-route",
                routeName,
                message: `Route ${routeName} lifecycle.onLeave must be an object.`
              }));
            } else {
              if (!Array.isArray(onLeave.blockedBy)) {
                issues.push(violation("SCM_ROUTE_ON_LEAVE_BLOCKED_BY_INVALID", {
                  phase: "validate-route",
                  routeName,
                  message: `Route ${routeName} lifecycle.onLeave.blockedBy must be an array.`
                }));
              } else {
                onLeave.blockedBy.forEach((path) => {
                  validateRouteReferencePath(routeName, manifest, path, issues, {
                    property: "lifecycle.onLeave.blockedBy",
                    requireDeclaredData: true
                  });
                });
              }

              if (!Array.isArray(onLeave.resolutions) || onLeave.resolutions.length === 0) {
                issues.push(violation("SCM_ROUTE_ON_LEAVE_RESOLUTIONS_INVALID", {
                  phase: "validate-route",
                  routeName,
                  message: `Route ${routeName} lifecycle.onLeave.resolutions must be a non-empty array.`
                }));
              }
            }
          }
        }

        return {
          kind: "mcel-scm-route-validation",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          routeName,
          ok: issues.length === 0,
          issues: issues.map((issue) => jsonSafe(issue))
        };
      }

      function normalizeRouteDefinition(name, manifest) {
        const copy = cloneValue(manifest);
        copy.name = safeString(name);
        copy.segments = Array.isArray(copy.segments) ? copy.segments.map((segment) => {
          if (Object.prototype.hasOwnProperty.call(segment, "literal")) return {literal: safeString(segment.literal)};
          return {
            param: safeString(segment.param),
            type: safeString(segment.type || "id"),
            required: segment.required !== false
          };
        }) : [];
        copy.query = isPlainObject(copy.query) ? copy.query : {};
        copy.mounts = isPlainObject(copy.mounts) ? copy.mounts : {component: "", inputs: {}};
        copy.mounts.component = safeString(copy.mounts.component);
        copy.mounts.inputs = isPlainObject(copy.mounts.inputs) ? copy.mounts.inputs : {};
        Object.keys(copy.mounts.inputs).forEach((inputName) => {
          copy.mounts.inputs[inputName] = normalizeRoutePath(copy.mounts.inputs[inputName]);
        });
        copy.data = isPlainObject(copy.data) ? copy.data : {};
        Object.keys(copy.data).forEach((loaderName) => {
          const loader = copy.data[loaderName];
          loader.kind = safeString(loader.kind || "async-data");
          loader.triggers = Array.isArray(loader.triggers) ? loader.triggers.map(normalizeRoutePath).filter(Boolean) : [];
          loader.reads = Array.isArray(loader.reads) ? loader.reads.map(normalizeRoutePath).filter(Boolean) : [];
          loader.writes = Array.isArray(loader.writes) ? loader.writes.map(normalizeRoutePath).filter(Boolean) : [];
          loader.cancellation = safeString(loader.cancellation);
          loader.racePolicy = safeString(loader.racePolicy);
          loader.external = isPlainObject(loader.external) ? loader.external : {};
          loader.errorPolicy = isPlainObject(loader.errorPolicy) ? loader.errorPolicy : {};
        });
        copy.lifecycle = isPlainObject(copy.lifecycle) ? copy.lifecycle : {};
        if (isPlainObject(copy.lifecycle.onLeave)) {
          copy.lifecycle.onLeave.blockedBy = Array.isArray(copy.lifecycle.onLeave.blockedBy)
            ? copy.lifecycle.onLeave.blockedBy.map(normalizeRoutePath).filter(Boolean)
            : [];
          copy.lifecycle.onLeave.resolutions = Array.isArray(copy.lifecycle.onLeave.resolutions)
            ? copy.lifecycle.onLeave.resolutions.map(safeString).filter(Boolean)
            : [];
        }
        copy.displayPath = copy.segments.map((segment) => {
          if (Object.prototype.hasOwnProperty.call(segment, "literal")) return segment.literal;
          return `{${segment.param}}`;
        }).join("/");
        return deepFreeze(copy);
      }

      function defineRoute(name, manifest, options = {}) {
        const routeName = safeString(name);
        if (routeDefinitions.has(routeName) && options?.replace !== true) {
          throwViolation(violation("SCM_DUPLICATE_ROUTE", {
            phase: "define-route",
            routeName,
            message: `Route ${routeName} is already defined. Pass replace:true to replace it.`
          }));
        }

        const validation = validateRouteManifest(routeName, manifest);
        if (!validation.ok) {
          throwViolation(validation.issues[0]);
        }

        const definition = normalizeRouteDefinition(routeName, manifest);
        routeDefinitions.set(routeName, definition);
        return definition;
      }

      function clearRouteDefinitions() {
        const count = routeDefinitions.size;
        routeDefinitions.clear();
        return {
          kind: "mcel-scm-clear-route-definitions",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          cleared: count
        };
      }

      function listRouteDefinitions() {
        return [...routeDefinitions.values()].map((definition) => ({
          kind: "mcel-scm-route-definition-summary",
          name: definition.name,
          version: definition.version || "",
          contract: definition.contract || "",
          segments: cloneValue(definition.segments || []),
          displayPath: definition.displayPath || "",
          mountComponent: definition.mounts?.component || "",
          dataLoaders: Object.keys(definition.data || {})
        }));
      }

      function routeDefinition(name) {
        return routeDefinitions.get(safeString(name)) || null;
      }

      function createRouteInstance(name, options = {}) {
        const routeName = safeString(name);
        const definition = routeDefinition(routeName);
        if (!definition) {
          throwViolation(violation("SCM_UNKNOWN_ROUTE", {
            phase: "create-route-instance",
            routeName
          }));
        }

        const instance = {
          kind: "mcel-scm-route-instance",
          contractVersion: CONTRACT_VERSION,
          id: options.id || `scm-route-${nextRouteInstanceId++}`,
          routeName,
          definition,
          params: {},
          query: {},
          data: cloneValue(options.data || {}),
          status: "created",
          componentInstance: options.componentInstance || null,
          evidence: []
        };

        recordEvidence(instance, {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "create-route-instance",
          ok: true,
          routeName,
          instanceId: instance.id
        });

        return instance;
      }

      function routeDataRoot(instance, surface) {
        if (surface === "params") return instance.params;
        if (surface === "query") return instance.query;
        return instance.data;
      }

      function readRoutePath(instance, path) {
        const normalized = normalizeRoutePath(path);
        if (!normalized) return undefined;
        const parts = normalized.split(".");
        if (parts[0] === "component") {
          if (!instance.componentInstance) return undefined;
          return readRaw(instance.componentInstance, parts.slice(1).join("."));
        }
        let cursor = routeDataRoot(instance, parts[1]);
        for (let index = 2; index < parts.length; index += 1) {
          if (cursor == null) return undefined;
          cursor = cursor[parts[index]];
        }
        return cursor;
      }

      function blockedValue(value) {
        if (value === true) return true;
        if (value === false || value === null || value === undefined) return false;
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === "object") return Object.keys(value).length > 0;
        if (typeof value === "string") return value.length > 0;
        if (typeof value === "number") return value !== 0;
        return Boolean(value);
      }

      function assertRouteInstance(instance, phase) {
        if (!instance || instance.kind !== "mcel-scm-route-instance") {
          throwViolation(violation("SCM_INVALID_ROUTE_INSTANCE", {
            phase,
            message: "MCEL SCM route operation requires a route instance."
          }));
        }
      }

      function buildRouteParams(definition, rawParams) {
        const params = {};
        definition.segments.forEach((segment) => {
          if (!Object.prototype.hasOwnProperty.call(segment, "param")) return;
          const value = rawParams?.[segment.param];
          if ((value === undefined || value === null || value === "") && segment.required !== false) {
            throwViolation(violation("SCM_ROUTE_PARAM_MISSING", {
              phase: "route-enter",
              routeName: definition.name,
              paramName: segment.param,
              expectedType: segment.type || "id"
            }));
          }
          const schema = {type: segment.type || "id", required: segment.required !== false};
          if (value !== undefined && value !== null && value !== "" && !schemaAcceptsValue(schema, value)) {
            throwViolation(violation("SCM_ROUTE_PARAM_INVALID", {
              phase: "route-enter",
              routeName: definition.name,
              paramName: segment.param,
              expectedType: schema.type,
              value
            }));
          }
          if (value !== undefined && value !== null && value !== "") {
            params[segment.param] = coerceSchemaValue(schema, value);
          }
        });
        return params;
      }

      function buildRouteQuery(definition, rawQuery) {
        const query = {};
        Object.keys(definition.query || {}).forEach((queryName) => {
          const schema = definition.query[queryName];
          let value = rawQuery?.[queryName];
          if ((value === undefined || value === null || value === "") && schema.default !== undefined) {
            value = schema.default;
          }
          if (value === undefined || value === null || value === "") {
            if (schema.required === true && schema.optional !== true) {
              throwViolation(violation("SCM_ROUTE_QUERY_MISSING", {
                phase: "route-enter",
                routeName: definition.name,
                queryName,
                expectedType: schemaType(schema),
                message: `Route ${definition.name} is missing required query value ${queryName}.`
              }));
            }
            return;
          }
          if (!schemaAcceptsValue(schema, value)) {
            throwViolation(violation("SCM_ROUTE_QUERY_INVALID", {
              phase: "route-enter",
              routeName: definition.name,
              queryName,
              expectedType: schemaType(schema),
              value
            }));
          }
          query[queryName] = coerceSchemaValue(schema, value);
        });
        return query;
      }

      function resolveMountInputs(instance) {
        const inputs = {};
        const mapping = instance.definition?.mounts?.inputs || {};
        Object.keys(mapping).forEach((inputName) => {
          inputs[inputName] = cloneValue(readRoutePath(instance, mapping[inputName]));
        });
        return inputs;
      }

      function enterRoute(instance, paramsOrOptions = {}, queryArg = {}) {
        assertRouteInstance(instance, "route-enter");
        const options = isPlainObject(paramsOrOptions) && (paramsOrOptions.params || paramsOrOptions.query)
          ? paramsOrOptions
          : {params: paramsOrOptions, query: queryArg};

        try {
          instance.params = buildRouteParams(instance.definition, options.params || {});
          instance.query = buildRouteQuery(instance.definition, options.query || {});
        } catch (error) {
          if (error?.violation?.kind === "mcel-scm-violation") {
            recordEvidence(instance, error.violation);
            throw error;
          }
          throw error;
        }

        instance.status = "entered";
        const mountInputs = resolveMountInputs(instance);
        const entry = {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-enter",
          ok: true,
          routeName: instance.routeName,
          instanceId: instance.id,
          params: cloneValue(instance.params),
          query: cloneValue(instance.query),
          mountComponent: instance.definition.mounts?.component || "",
          mountInputs: jsonSafe(mountInputs)
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-route-enter-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          routeName: instance.routeName,
          params: cloneValue(instance.params),
          query: cloneValue(instance.query),
          mountInputs
        };
      }

      function leaveRoute(instance, options = {}) {
        assertRouteInstance(instance, "route-leave");
        const onLeave = instance.definition?.lifecycle?.onLeave || {};
        const blockedBy = Array.isArray(onLeave.blockedBy) ? onLeave.blockedBy : [];
        const blockers = blockedBy.filter((path) => blockedValue(readRoutePath(instance, path)));
        const resolution = safeString(options.resolution);
        const resolutions = Array.isArray(onLeave.resolutions) ? onLeave.resolutions : [];

        if (blockers.length > 0) {
          if (!resolution || resolution === "cancelNavigation") {
            const entry = violation("SCM_ROUTE_LEAVE_BLOCKED", {
              phase: "route-leave",
              routeName: instance.routeName,
              instanceId: instance.id,
              blockers,
              resolutions,
              severity: "user-action-required"
            });
            recordEvidence(instance, entry);
            return {
              kind: "mcel-scm-route-leave-result",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              ok: false,
              blocked: true,
              routeName: instance.routeName,
              blockers,
              resolutions,
              evidence: jsonSafe(entry)
            };
          }

          if (!resolutions.includes(resolution)) {
            throwViolation(violation("SCM_ROUTE_INVALID_LEAVE_RESOLUTION", {
              phase: "route-leave",
              routeName: instance.routeName,
              instanceId: instance.id,
              resolution,
              resolutions,
              message: `Route ${instance.routeName} received invalid leave resolution ${resolution}.`
            }), instance);
          }
        }

        instance.status = "left";
        const entry = {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-leave",
          ok: true,
          routeName: instance.routeName,
          instanceId: instance.id,
          resolution: resolution || ""
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-route-leave-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          blocked: false,
          routeName: instance.routeName,
          evidence: jsonSafe(entry)
        };
      }

      function writeRoutePath(instance, path, value) {
        const normalized = normalizeRoutePath(path);
        if (!normalized) return undefined;
        const parts = normalized.split(".");
        if (parts[0] === "component") {
          if (!instance.componentInstance) return undefined;
          return writeRaw(instance.componentInstance, parts.slice(1).join("."), value);
        }

        if (parts[0] !== "route") return undefined;
        const root = routeDataRoot(instance, parts[1]);
        if (!root || typeof root !== "object") return undefined;
        let cursor = root;
        for (let index = 2; index < parts.length - 1; index += 1) {
          const key = parts[index];
          if (!cursor[key] || typeof cursor[key] !== "object") cursor[key] = {};
          cursor = cursor[key];
        }
        cursor[parts[parts.length - 1]] = cloneValue(value);
        return value;
      }

      function deleteRoutePath(instance, path) {
        const normalized = normalizeRoutePath(path);
        if (!normalized) return false;
        const parts = normalized.split(".");
        if (parts[0] === "component") {
          if (!instance.componentInstance) return false;
          return deleteRaw(instance.componentInstance, parts.slice(1).join("."));
        }

        if (parts[0] !== "route") return false;
        const root = routeDataRoot(instance, parts[1]);
        if (!root || typeof root !== "object") return false;
        let cursor = root;
        for (let index = 2; index < parts.length - 1; index += 1) {
          if (!cursor || typeof cursor !== "object") return false;
          cursor = cursor[parts[index]];
        }
        if (!cursor || typeof cursor !== "object") return false;
        delete cursor[parts[parts.length - 1]];
        return true;
      }

      function routeLoaderDefinition(instance, loaderName) {
        return instance?.definition?.data?.[safeString(loaderName)] || null;
      }

      function createRouteLoaderContext(instance, loaderName) {
        assertRouteInstance(instance, "route-loader");
        const name = safeString(loaderName);
        const loader = routeLoaderDefinition(instance, name);
        if (!loader) {
          throwViolation(violation("SCM_UNKNOWN_ROUTE_LOADER", {
            phase: "route-loader",
            routeName: instance.routeName,
            loaderName: name,
            message: `Unknown SCM route loader ${name} on route ${instance.routeName}.`
          }), instance);
        }

        const declaredReads = Array.isArray(loader.reads) ? loader.reads.map(normalizeRoutePath).filter(Boolean) : [];
        const declaredWrites = Array.isArray(loader.writes) ? loader.writes.map(normalizeRoutePath).filter(Boolean) : [];

        function checkPath(path, access) {
          const normalized = normalizeRoutePath(path);
          if (!normalized) {
            throwViolation(violation("SCM_ROUTE_LOADER_INVALID_PATH", {
              phase: "route-loader",
              routeName: instance.routeName,
              loaderName: name,
              path: safeString(path),
              declaredReads,
              declaredWrites,
              message: `Route loader ${name} attempted ${access} with invalid path ${safeString(path)}.`
            }), instance);
          }
          return normalized;
        }

        function assertRead(path) {
          const normalized = checkPath(path, "read");
          if (!isAllowedRoutePath(normalized, declaredReads)) {
            throwViolation(violation("SCM_ROUTE_LOADER_UNDECLARED_READ", {
              phase: "route-loader",
              routeName: instance.routeName,
              loaderName: name,
              path: normalized,
              declaredReads,
              declaredWrites,
              message: `Route loader ${name} attempted to read ${normalized} without declaring it.`
            }), instance);
          }
          return normalized;
        }

        function assertWrite(path) {
          const normalized = checkPath(path, "write");
          if (!isAllowedRoutePath(normalized, declaredWrites)) {
            throwViolation(violation("SCM_ROUTE_LOADER_UNDECLARED_WRITE", {
              phase: "route-loader",
              routeName: instance.routeName,
              loaderName: name,
              path: normalized,
              declaredReads,
              declaredWrites,
              message: `Route loader ${name} attempted to write ${normalized} without declaring it.`
            }), instance);
          }
          return normalized;
        }

        return Object.freeze({
          kind: "mcel-scm-route-loader-context",
          contractVersion: CONTRACT_VERSION,
          routeName: instance.routeName,
          loaderName: name,

          get(path) {
            return cloneValue(readRoutePath(instance, assertRead(path)));
          },

          set(path, value) {
            return writeRoutePath(instance, assertWrite(path), value);
          },

          delete(path) {
            return deleteRoutePath(instance, assertWrite(path));
          },

          evidence(entry = {}) {
            return recordEvidence(instance, {
              kind: "mcel-scm-route-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "route-loader",
              routeName: instance.routeName,
              loaderName: name,
              ...jsonSafe(entry)
            });
          }
        });
      }

      function recordRouteLoaderFailure(instance, loaderName, error) {
        const entry = {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-loader-failure",
          ok: false,
          routeName: instance.routeName,
          loaderName,
          code: error?.violation?.code || "SCM_ROUTE_LOADER_EXCEPTION",
          message: error?.violation?.message || error?.message || String(error),
          errorName: error?.name || "Error"
        };
        recordEvidence(instance, entry);
        return entry;
      }

      function finishRouteLoaderCommit(instance, loaderName, loader, ctx, result, payload) {
        let committed = null;
        if (typeof loader.commit === "function") {
          committed = loader.commit(ctx, result, payload);
        }
        const entry = {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-loader-commit",
          ok: true,
          routeName: instance.routeName,
          loaderName,
          declaredReads: loader.reads || [],
          declaredWrites: loader.writes || [],
          committed: jsonSafe(committed)
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-route-loader-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          routeName: instance.routeName,
          loaderName,
          result: jsonSafe(result),
          data: cloneValue(instance.data),
          evidence: jsonSafe(entry)
        };
      }

      function runRouteLoader(instance, loaderName, payload = {}) {
        assertRouteInstance(instance, "route-loader");
        const name = safeString(loaderName);
        const loader = routeLoaderDefinition(instance, name);
        if (!loader) {
          throwViolation(violation("SCM_UNKNOWN_ROUTE_LOADER", {
            phase: "route-loader",
            routeName: instance.routeName,
            loaderName: name,
            message: `Unknown SCM route loader ${name} on route ${instance.routeName}.`
          }), instance);
        }

        const ctx = createRouteLoaderContext(instance, name);
        recordEvidence(instance, {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-loader-start",
          ok: true,
          routeName: instance.routeName,
          loaderName: name,
          kind: loader.kind || "async-data",
          triggers: loader.triggers || [],
          declaredReads: loader.reads || [],
          declaredWrites: loader.writes || [],
          cancellation: loader.cancellation || "",
          racePolicy: loader.racePolicy || "",
          external: jsonSafe(loader.external || {})
        });

        try {
          const result = typeof loader.run === "function" ? loader.run(ctx, payload) : null;
          if (result && typeof result.then === "function") {
            return result
              .then((resolved) => finishRouteLoaderCommit(instance, name, loader, ctx, resolved, payload))
              .catch((error) => {
                recordRouteLoaderFailure(instance, name, error);
                if (error?.violation?.kind === "mcel-scm-violation") throw error;
                throwViolation(violation("SCM_ROUTE_LOADER_EXCEPTION", {
                  phase: "route-loader",
                  routeName: instance.routeName,
                  loaderName: name,
                  message: error?.message || String(error),
                  errorName: error?.name || "Error"
                }), instance);
              });
          }
          return finishRouteLoaderCommit(instance, name, loader, ctx, result, payload);
        } catch (error) {
          recordRouteLoaderFailure(instance, name, error);
          if (error?.violation?.kind === "mcel-scm-violation") throw error;
          throwViolation(violation("SCM_ROUTE_LOADER_EXCEPTION", {
            phase: "route-loader",
            routeName: instance.routeName,
            loaderName: name,
            message: error?.message || String(error),
            errorName: error?.name || "Error"
          }), instance);
        }
      }

      function cancelRouteLoader(instance, loaderName, reason = "manual") {
        assertRouteInstance(instance, "route-loader-cancel");
        const name = safeString(loaderName);
        const loader = routeLoaderDefinition(instance, name);
        if (!loader) {
          throwViolation(violation("SCM_UNKNOWN_ROUTE_LOADER", {
            phase: "route-loader-cancel",
            routeName: instance.routeName,
            loaderName: name,
            message: `Unknown SCM route loader ${name} on route ${instance.routeName}.`
          }), instance);
        }

        const entry = {
          kind: "mcel-scm-route-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "route-loader-cancel",
          ok: true,
          routeName: instance.routeName,
          loaderName: name,
          reason: safeString(reason),
          cancellation: loader.cancellation || "",
          racePolicy: loader.racePolicy || ""
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-route-loader-cancel-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          routeName: instance.routeName,
          loaderName: name,
          evidence: jsonSafe(entry)
        };
      }

      function exportRouteEvidence(instance) {
        return {
          kind: "mcel-scm-route-evidence-packet",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          routeName: instance?.routeName || "",
          instanceId: instance?.id || "",
          evidence: Array.isArray(instance?.evidence) ? instance.evidence.map((entry) => jsonSafe(entry)) : []
        };
      }

      function validateComponentManifest(name, manifest) {
        const componentName = safeString(name);
        const issues = [];

        if (!validName(componentName)) {
          issues.push(violation("SCM_INVALID_COMPONENT_NAME", {
            phase: "validate-manifest",
            componentName,
            message: `Invalid SCM component name ${String(name)}.`
          }));
        }

        if (!isPlainObject(manifest)) {
          issues.push(violation("SCM_INVALID_MANIFEST", {
            phase: "validate-manifest",
            componentName,
            message: `Component ${componentName} manifest must be an object.`
          }));
          return {
            kind: "mcel-scm-manifest-validation",
            contractVersion: CONTRACT_VERSION,
            generatedAt: now(),
            componentName,
            ok: false,
            issues: issues.map((issue) => jsonSafe(issue))
          };
        }

        if (!isPlainObject(manifest.owns)) {
          issues.push(violation("SCM_MISSING_OWNERSHIP", {
            phase: "validate-manifest",
            componentName
          }));
        } else {
          const ownedCount = Object.keys(manifest.owns).reduce((count, surface) => {
            if (!Array.isArray(manifest.owns[surface])) return count;
            return count + manifest.owns[surface].filter((path) => Boolean(normalizeOwnedPath(surface, path))).length;
          }, 0);
          if (ownedCount === 0) {
            issues.push(violation("SCM_EMPTY_OWNERSHIP", {
              phase: "validate-manifest",
              componentName
            }));
          }
        }

        const transitions = manifest.transitions;
        if (transitions !== undefined && !isPlainObject(transitions)) {
          issues.push(violation("SCM_INVALID_TRANSITIONS", {
            phase: "validate-manifest",
            componentName,
            message: `Component ${componentName} transitions must be an object when provided.`
          }));
        }

        const outputNames = declaredOutputs(manifest);
        const transitionNames = isPlainObject(transitions) ? Object.keys(transitions) : [];

        transitionNames.forEach((transitionName) => {
          const transitionSpec = transitions[transitionName];
          if (!validName(transitionName) || !isPlainObject(transitionSpec)) {
            issues.push(violation("SCM_INVALID_TRANSITION", {
              phase: "validate-manifest",
              componentName,
              transitionName,
              message: `Transition ${transitionName} must be a named object.`
            }));
            return;
          }

          const reads = validatePathList(componentName, transitionName, "reads", transitionSpec.reads, issues);
          const writes = validatePathList(componentName, transitionName, "writes", transitionSpec.writes, issues);

          writes.forEach((path) => {
            if (!isOwnedWrite(manifest, path)) {
              issues.push(violation("SCM_WRITE_OUTSIDE_OWNERSHIP", {
                phase: "validate-manifest",
                componentName,
                transitionName,
                path,
                declaredWrites: writes,
                ownedSource: ownedPathsFor(manifest, "source"),
                ownedState: ownedPathsFor(manifest, "state"),
                ownedRuntime: ownedPathsFor(manifest, "runtime")
              }));
            }
          });

          if (transitionSpec.emits !== undefined) {
            if (!Array.isArray(transitionSpec.emits)) {
              issues.push(violation("SCM_TRANSITION_EMITS_NOT_ARRAY", {
                phase: "validate-manifest",
                componentName,
                transitionName,
                message: `Transition ${transitionName} emits must be an array when provided.`
              }));
            } else {
              transitionSpec.emits.forEach((outputName) => {
                const output = safeString(outputName);
                if (output && !outputNames.includes(output)) {
                  issues.push(violation("SCM_UNDECLARED_OUTPUT", {
                    phase: "validate-manifest",
                    componentName,
                    transitionName,
                    output,
                    message: `Transition ${transitionName} emits undeclared output ${output}.`
                  }));
                }
              });
            }
          }
        });

        validateComponentEffects(componentName, manifest, issues);
        validateChildComposition(componentName, manifest, transitionNames, issues);

        return {
          kind: "mcel-scm-manifest-validation",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          componentName,
          ok: issues.length === 0,
          issues: issues.map((issue) => jsonSafe(issue))
        };
      }

      function normalizeDefinition(name, manifest) {
        const copy = cloneValue(manifest);
        copy.name = safeString(name);
        copy.transitions = isPlainObject(copy.transitions) ? copy.transitions : {};
        Object.keys(copy.transitions).forEach((transitionName) => {
          const spec = copy.transitions[transitionName];
          spec.reads = normalizePathList(spec.reads);
          spec.writes = normalizePathList(spec.writes);
          spec.emits = Array.isArray(spec.emits) ? spec.emits.map(safeString).filter(Boolean) : [];
        });
        copy.effects = isPlainObject(copy.effects) ? copy.effects : {};
        Object.keys(copy.effects).forEach((effectName) => {
          const spec = copy.effects[effectName];
          spec.kind = safeString(spec.kind || "effect");
          spec.triggers = normalizePathList(spec.triggers);
          spec.reads = normalizePathList(spec.reads);
          spec.writes = normalizePathList(spec.writes);
          spec.cancellation = safeString(spec.cancellation);
          spec.racePolicy = safeString(spec.racePolicy);
          spec.external = isPlainObject(spec.external) ? spec.external : {};
          spec.errorPolicy = isPlainObject(spec.errorPolicy) ? spec.errorPolicy : {};
        });
        copy.children = isPlainObject(copy.children) ? copy.children : {};
        Object.keys(copy.children).forEach((childName) => {
          const child = copy.children[childName];
          child.component = safeString(child.component || childName);
          child.slot = safeString(child.slot);
          child.inputs = isPlainObject(child.inputs) ? child.inputs : {};
          Object.keys(child.inputs).forEach((inputName) => {
            child.inputs[inputName] = normalizePath(child.inputs[inputName]);
          });
          child.outputs = isPlainObject(child.outputs) ? child.outputs : {};
          Object.keys(child.outputs).forEach((outputName) => {
            child.outputs[outputName] = safeString(child.outputs[outputName]);
          });
          child.mayMutate = normalizePathList(child.mayMutate);
          child.maySerialize = child.maySerialize === true;
        });
        return deepFreeze(copy);
      }

      function defineComponent(name, manifest, options = {}) {
        const componentName = safeString(name);
        if (definitions.has(componentName) && options?.replace !== true) {
          throwViolation(violation("SCM_DUPLICATE_COMPONENT", {
            phase: "define-component",
            componentName
          }));
        }

        const validation = validateComponentManifest(componentName, manifest);
        if (!validation.ok) {
          throwViolation(validation.issues[0]);
        }

        const definition = normalizeDefinition(componentName, manifest);
        definitions.set(componentName, definition);
        return definition;
      }

      function clearDefinitions() {
        const componentCount = definitions.size;
        const routeCount = routeDefinitions.size;
        definitions.clear();
        routeDefinitions.clear();
        return {
          kind: "mcel-scm-clear-definitions",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          cleared: componentCount,
          clearedComponents: componentCount,
          clearedRoutes: routeCount
        };
      }

      function listComponentDefinitions() {
        return [...definitions.values()].map((definition) => ({
          kind: "mcel-scm-component-definition-summary",
          name: definition.name,
          version: definition.version || "",
          contract: definition.contract || "",
          owns: cloneValue(definition.owns || {}),
          transitions: Object.keys(definition.transitions || {}),
          effects: Object.keys(definition.effects || {})
        }));
      }

      function componentDefinition(name) {
        return definitions.get(safeString(name)) || null;
      }

      function mergeState(base, overrides) {
        const result = cloneValue(base || {});
        if (!isPlainObject(overrides)) return result;
        Object.keys(overrides).forEach((key) => {
          result[key] = cloneValue(overrides[key]);
        });
        return result;
      }

      function createComponentInstance(name, options = {}) {
        const componentName = safeString(name);
        const definition = componentDefinition(componentName);
        if (!definition) {
          throwViolation(violation("SCM_UNKNOWN_COMPONENT", {
            phase: "create-instance",
            componentName,
            message: `Unknown SCM component ${componentName}.`
          }));
        }

        const instance = {
          kind: "mcel-scm-component-instance",
          contractVersion: CONTRACT_VERSION,
          id: options.id || `scm-instance-${nextInstanceId++}`,
          componentName,
          definition,
          source: cloneValue(options.source || definition.source || {}),
          runtime: cloneValue(options.runtime || definition.runtime || {}),
          state: mergeState(definition.state || {}, options.state),
          evidence: []
        };

        recordEvidence(instance, {
          kind: "mcel-scm-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "create-instance",
          ok: true,
          componentName,
          instanceId: instance.id
        });

        return instance;
      }

      function dataRoot(instance, root) {
        if (root === "source") return instance.source;
        if (root === "runtime") return instance.runtime;
        return instance.state;
      }

      function readRaw(instance, path) {
        const parts = splitPath(path);
        if (!parts) return undefined;
        let cursor = dataRoot(instance, parts[0]);
        for (let index = 1; index < parts.length; index += 1) {
          if (cursor == null) return undefined;
          cursor = cursor[parts[index]];
        }
        return cursor;
      }

      function writeRaw(instance, path, value) {
        const parts = splitPath(path);
        if (!parts || !rootForPath(path)) return undefined;
        let cursor = dataRoot(instance, parts[0]);
        for (let index = 1; index < parts.length - 1; index += 1) {
          const part = parts[index];
          if (!isPlainObject(cursor[part]) && !Array.isArray(cursor[part])) cursor[part] = {};
          cursor = cursor[part];
        }
        cursor[parts[parts.length - 1]] = cloneValue(value);
        return cursor[parts[parts.length - 1]];
      }

      function deleteRaw(instance, path) {
        const parts = splitPath(path);
        if (!parts || !rootForPath(path)) return false;
        let cursor = dataRoot(instance, parts[0]);
        for (let index = 1; index < parts.length - 1; index += 1) {
          if (cursor == null) return false;
          cursor = cursor[parts[index]];
        }
        if (cursor && Object.prototype.hasOwnProperty.call(cursor, parts[parts.length - 1])) {
          delete cursor[parts[parts.length - 1]];
          return true;
        }
        return false;
      }

      function createTransitionContext(instance, transitionName, spec) {
        const declaredReads = normalizePathList(spec.reads);
        const declaredWrites = normalizePathList(spec.writes);

        function checkPath(path, access) {
          const normalized = normalizePath(path);
          if (!normalized || !rootForPath(normalized)) {
            throwViolation(violation("SCM_INVALID_PATH", {
              phase: "transition",
              componentName: instance.componentName,
              transitionName,
              path: safeString(path),
              declaredReads,
              declaredWrites,
              message: `Transition ${transitionName} attempted ${access} with invalid path ${safeString(path)}.`
            }), instance);
          }
          return normalized;
        }

        function assertRead(path) {
          const normalized = checkPath(path, "read");
          if (!isAllowedPath(normalized, declaredReads)) {
            throwViolation(violation("SCM_UNDECLARED_READ", {
              phase: "transition",
              componentName: instance.componentName,
              transitionName,
              path: normalized,
              declaredReads,
              declaredWrites
            }), instance);
          }
          return normalized;
        }

        function assertWrite(path) {
          const normalized = checkPath(path, "write");
          if (!isAllowedPath(normalized, declaredWrites)) {
            throwViolation(violation("SCM_UNDECLARED_WRITE", {
              phase: "transition",
              componentName: instance.componentName,
              transitionName,
              path: normalized,
              declaredReads,
              declaredWrites
            }), instance);
          }
          return normalized;
        }

        return Object.freeze({
          get(path) {
            return cloneValue(readRaw(instance, assertRead(path)));
          },

          set(path, value) {
            return writeRaw(instance, assertWrite(path), value);
          },

          delete(path) {
            return deleteRaw(instance, assertWrite(path));
          },

          addUnique(path, value) {
            const normalizedRead = assertRead(path);
            const normalizedWrite = assertWrite(path);
            const current = readRaw(instance, normalizedRead);
            const next = Array.isArray(current) ? current.slice() : [];
            if (!next.includes(value)) next.push(value);
            return writeRaw(instance, normalizedWrite, next);
          },

          exists(path, value) {
            const current = readRaw(instance, assertRead(path));
            if (arguments.length < 2) return current !== undefined && current !== null;
            if (Array.isArray(current)) {
              return current.some((item) => {
                if (item && typeof item === "object" && "id" in item) return item.id === value;
                return item === value;
              });
            }
            if (current && typeof current === "object") return Object.prototype.hasOwnProperty.call(current, value);
            return current === value;
          },

          state(path) {
            return this.get(path.startsWith("state.") ? path : `state.${path}`);
          },

          commit({from, to} = {}) {
            const value = this.get(from);
            this.set(to, value);
            return value;
          },

          evidence(entry) {
            return recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "transition",
              componentName: instance.componentName,
              transitionName,
              ...jsonSafe(entry || {})
            });
          }
        });
      }

      function createEffectContext(instance, effectName) {
        assertComponentInstance(instance, "effect", {effectName});
        const name = safeString(effectName);
        const spec = instance.definition?.effects?.[name];
        if (!spec) {
          throwViolation(violation("SCM_UNKNOWN_EFFECT", {
            phase: "effect",
            componentName: instance.componentName,
            effectName: name,
            message: `Unknown SCM effect ${name} on component ${instance.componentName}.`
          }), instance);
        }

        const declaredReads = normalizePathList(spec.reads);
        const declaredWrites = normalizePathList(spec.writes);

        function checkPath(path, access) {
          const normalized = normalizePath(path);
          if (!normalized || !rootForPath(normalized)) {
            throwViolation(violation("SCM_EFFECT_INVALID_PATH", {
              phase: "effect",
              componentName: instance.componentName,
              effectName: name,
              path: safeString(path),
              declaredReads,
              declaredWrites,
              message: `Effect ${name} attempted ${access} with invalid path ${safeString(path)}.`
            }), instance);
          }
          return normalized;
        }

        function assertRead(path) {
          const normalized = checkPath(path, "read");
          if (!isAllowedPath(normalized, declaredReads)) {
            throwViolation(violation("SCM_EFFECT_UNDECLARED_READ", {
              phase: "effect",
              componentName: instance.componentName,
              effectName: name,
              path: normalized,
              declaredReads,
              declaredWrites,
              message: `Effect ${name} attempted to read ${normalized} without declaring it.`
            }), instance);
          }
          return normalized;
        }

        function assertWrite(path) {
          const normalized = checkPath(path, "write");
          if (!isAllowedPath(normalized, declaredWrites)) {
            throwViolation(violation("SCM_EFFECT_UNDECLARED_WRITE", {
              phase: "effect",
              componentName: instance.componentName,
              effectName: name,
              path: normalized,
              declaredReads,
              declaredWrites,
              message: `Effect ${name} attempted to write ${normalized} without declaring it.`
            }), instance);
          }
          return normalized;
        }

        return Object.freeze({
          kind: "mcel-scm-effect-context",
          contractVersion: CONTRACT_VERSION,
          componentName: instance.componentName,
          effectName: name,

          get(path) {
            return cloneValue(readRaw(instance, assertRead(path)));
          },

          set(path, value) {
            return writeRaw(instance, assertWrite(path), value);
          },

          delete(path) {
            return deleteRaw(instance, assertWrite(path));
          },

          addUnique(path, value) {
            const normalizedRead = assertRead(path);
            const normalizedWrite = assertWrite(path);
            const current = readRaw(instance, normalizedRead);
            const next = Array.isArray(current) ? current.slice() : [];
            if (!next.includes(value)) next.push(value);
            return writeRaw(instance, normalizedWrite, next);
          },

          exists(path, value) {
            const current = readRaw(instance, assertRead(path));
            if (arguments.length < 2) return current !== undefined && current !== null;
            if (Array.isArray(current)) {
              return current.some((item) => {
                if (item && typeof item === "object" && "id" in item) return item.id === value;
                return item === value;
              });
            }
            if (current && typeof current === "object") return Object.prototype.hasOwnProperty.call(current, value);
            return current === value;
          },

          evidence(entry) {
            return recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "effect",
              componentName: instance.componentName,
              effectName: name,
              ...jsonSafe(entry || {})
            });
          }
        });
      }

      function recordEffectFailure(instance, effectName, error) {
        const entry = {
          kind: "mcel-scm-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "effect-failure",
          ok: false,
          componentName: instance.componentName,
          effectName,
          code: error?.violation?.code || "SCM_EFFECT_EXCEPTION",
          message: error?.violation?.message || error?.message || String(error),
          errorName: error?.name || "Error"
        };
        recordEvidence(instance, entry);
        return entry;
      }

      function finishEffectCommit(instance, effectName, spec, ctx, result, payload) {
        let committed = null;
        if (typeof spec.commit === "function") {
          committed = spec.commit(ctx, result, payload);
        }
        const entry = {
          kind: "mcel-scm-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "effect-commit",
          ok: true,
          componentName: instance.componentName,
          effectName,
          declaredReads: spec.reads,
          declaredWrites: spec.writes,
          committed: jsonSafe(committed)
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-effect-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          componentName: instance.componentName,
          effectName,
          result: jsonSafe(result),
          state: cloneValue(instance.state),
          source: cloneValue(instance.source),
          runtime: cloneValue(instance.runtime),
          evidence: jsonSafe(entry)
        };
      }

      function runEffect(instance, effectName, payload = {}) {
        assertComponentInstance(instance, "effect", {effectName});
        const name = safeString(effectName);
        const spec = instance.definition?.effects?.[name];
        if (!spec) {
          throwViolation(violation("SCM_UNKNOWN_EFFECT", {
            phase: "effect",
            componentName: instance.componentName,
            effectName: name,
            message: `Unknown SCM effect ${name} on component ${instance.componentName}.`
          }), instance);
        }

        const ctx = createEffectContext(instance, name);
        recordEvidence(instance, {
          kind: "mcel-scm-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "effect-start",
          ok: true,
          componentName: instance.componentName,
          effectName: name,
          kind: spec.kind || "effect",
          triggers: spec.triggers || [],
          declaredReads: spec.reads || [],
          declaredWrites: spec.writes || [],
          cancellation: spec.cancellation || "",
          racePolicy: spec.racePolicy || "",
          external: jsonSafe(spec.external || {})
        });

        try {
          const result = typeof spec.run === "function" ? spec.run(ctx, payload) : null;
          if (result && typeof result.then === "function") {
            return result
              .then((resolved) => finishEffectCommit(instance, name, spec, ctx, resolved, payload))
              .catch((error) => {
                recordEffectFailure(instance, name, error);
                if (error?.violation?.kind === "mcel-scm-violation") throw error;
                throwViolation(violation("SCM_EFFECT_EXCEPTION", {
                  phase: "effect",
                  componentName: instance.componentName,
                  effectName: name,
                  message: error?.message || String(error),
                  errorName: error?.name || "Error"
                }), instance);
              });
          }
          return finishEffectCommit(instance, name, spec, ctx, result, payload);
        } catch (error) {
          recordEffectFailure(instance, name, error);
          if (error?.violation?.kind === "mcel-scm-violation") throw error;
          throwViolation(violation("SCM_EFFECT_EXCEPTION", {
            phase: "effect",
            componentName: instance.componentName,
            effectName: name,
            message: error?.message || String(error),
            errorName: error?.name || "Error"
          }), instance);
        }
      }

      function cancelEffect(instance, effectName, reason = "manual") {
        assertComponentInstance(instance, "effect-cancel", {effectName});
        const name = safeString(effectName);
        const spec = instance.definition?.effects?.[name];
        if (!spec) {
          throwViolation(violation("SCM_UNKNOWN_EFFECT", {
            phase: "effect-cancel",
            componentName: instance.componentName,
            effectName: name,
            message: `Unknown SCM effect ${name} on component ${instance.componentName}.`
          }), instance);
        }

        const entry = {
          kind: "mcel-scm-evidence",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          phase: "effect-cancel",
          ok: true,
          componentName: instance.componentName,
          effectName: name,
          reason: safeString(reason),
          cancellation: spec.cancellation || "",
          racePolicy: spec.racePolicy || ""
        };
        recordEvidence(instance, entry);
        return {
          kind: "mcel-scm-effect-cancel-result",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          ok: true,
          componentName: instance.componentName,
          effectName: name,
          evidence: jsonSafe(entry)
        };
      }

      function assertComponentInstance(instance, phase, details = {}) {
        if (!instance || instance.kind !== "mcel-scm-component-instance") {
          throwViolation(violation("SCM_INVALID_INSTANCE", {
            phase,
            transitionName: safeString(details.transitionName),
            childName: safeString(details.childName),
            effectName: safeString(details.effectName),
            message: "MCEL SCM operation requires a component instance."
          }));
        }
      }

      function childDefinition(instance, childName) {
        return instance?.definition?.children?.[safeString(childName)] || null;
      }

      function createChildContext(instance, childName) {
        assertComponentInstance(instance, "child-context", {childName});
        const name = safeString(childName);
        const child = childDefinition(instance, name);
        if (!child) {
          throwViolation(violation("SCM_UNKNOWN_CHILD", {
            phase: "child-context",
            componentName: instance.componentName,
            childName: name,
            message: `Unknown SCM child ${name} on component ${instance.componentName}.`
          }), instance);
        }

        const declaredMutations = normalizePathList(child.mayMutate);

        function inputTarget(inputName) {
          const name = safeString(inputName);
          if (!Object.prototype.hasOwnProperty.call(child.inputs || {}, name)) {
            throwViolation(violation("SCM_UNKNOWN_CHILD_INPUT", {
              phase: "child-context",
              componentName: instance.componentName,
              childName,
              inputName: name,
              message: `Child ${childName} attempted to read undeclared input ${name}.`
            }), instance);
          }
          return child.inputs[name];
        }

        function outputTarget(outputName) {
          const name = safeString(outputName);
          if (!Object.prototype.hasOwnProperty.call(child.outputs || {}, name)) {
            throwViolation(violation("SCM_UNKNOWN_CHILD_OUTPUT", {
              phase: "child-output",
              componentName: instance.componentName,
              childName,
              outputName: name,
              message: `Child ${childName} attempted to emit undeclared output ${name}.`
            }), instance);
          }
          return child.outputs[name];
        }

        function assertChildMutation(path) {
          const normalized = normalizePath(path);
          if (!normalized || !rootForPath(normalized)) {
            throwViolation(violation("SCM_INVALID_PATH", {
              phase: "child-mutation",
              componentName: instance.componentName,
              childName,
              path: safeString(path),
              message: `Child ${childName} attempted mutation with invalid path ${safeString(path)}.`
            }), instance);
          }
          if (!isAllowedPath(normalized, declaredMutations)) {
            throwViolation(violation("SCM_CHILD_UNDECLARED_MUTATION", {
              phase: "child-mutation",
              componentName: instance.componentName,
              childName,
              path: normalized,
              declaredMutations
            }), instance);
          }
          if (!isOwnedWrite(instance.definition, normalized)) {
            throwViolation(violation("SCM_CHILD_MUTATION_OUTSIDE_OWNERSHIP", {
              phase: "child-mutation",
              componentName: instance.componentName,
              childName,
              path: normalized,
              declaredMutations
            }), instance);
          }
          return normalized;
        }

        return Object.freeze({
          kind: "mcel-scm-child-context",
          contractVersion: CONTRACT_VERSION,
          componentName: instance.componentName,
          childName: name,
          childComponent: child.component || name,
          slot: child.slot,
          maySerialize: child.maySerialize === true,

          get(inputName) {
            return cloneValue(readRaw(instance, inputTarget(inputName)));
          },

          emit(outputName, payload = {}) {
            const target = outputTarget(outputName);
            const targetTransition = transitionTargetName(target);
            if (!targetTransition) {
              throwViolation(violation("SCM_CHILD_OUTPUT_TARGET_INVALID", {
                phase: "child-output",
                componentName: instance.componentName,
                childName: name,
                outputName: safeString(outputName),
                target,
                message: `Child ${name} output ${safeString(outputName)} has invalid target ${target}.`
              }), instance);
            }

            recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "child-output",
              ok: true,
              componentName: instance.componentName,
              childName: name,
              outputName: safeString(outputName),
              target,
              transitionName: targetTransition
            });
            return transition(instance, targetTransition, payload);
          },

          set(path, value) {
            const normalized = assertChildMutation(path);
            const result = writeRaw(instance, normalized, value);
            recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "child-mutation",
              ok: true,
              componentName: instance.componentName,
              childName: name,
              path: normalized
            });
            return result;
          },

          delete(path) {
            const normalized = assertChildMutation(path);
            const result = deleteRaw(instance, normalized);
            recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "child-mutation",
              ok: true,
              componentName: instance.componentName,
              childName: name,
              path: normalized,
              operation: "delete"
            });
            return result;
          },

          addUnique(path, value) {
            const normalized = assertChildMutation(path);
            const current = readRaw(instance, normalized);
            const next = Array.isArray(current) ? current.slice() : [];
            if (!next.includes(value)) next.push(value);
            const result = writeRaw(instance, normalized, next);
            recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "child-mutation",
              ok: true,
              componentName: instance.componentName,
              childName: name,
              path: normalized,
              operation: "addUnique"
            });
            return result;
          },

          evidence(entry = {}) {
            return recordEvidence(instance, {
              kind: "mcel-scm-evidence",
              contractVersion: CONTRACT_VERSION,
              generatedAt: now(),
              phase: "child-context",
              componentName: instance.componentName,
              childName: name,
              ...jsonSafe(entry)
            });
          }
        });
      }

      function transition(instance, transitionName, payload = {}) {
        assertComponentInstance(instance, "transition", {transitionName});

        const name = safeString(transitionName);
        const spec = instance.definition?.transitions?.[name];
        if (!spec) {
          throwViolation(violation("SCM_UNKNOWN_TRANSITION", {
            phase: "transition",
            componentName: instance.componentName,
            transitionName: name,
            message: `Unknown SCM transition ${name} on component ${instance.componentName}.`
          }), instance);
        }

        const ctx = createTransitionContext(instance, name, spec);
        try {
          if (typeof spec.pre === "function") {
            const preResult = spec.pre(ctx, payload);
            if (preResult === false) {
              throwViolation(violation("SCM_TRANSITION_PRECONDITION_FAILED", {
                phase: "transition",
                componentName: instance.componentName,
                transitionName: name,
                message: `Transition ${name} precondition failed.`
              }), instance);
            }
          }

          let result = null;
          if (typeof spec.apply === "function") result = spec.apply(ctx, payload);
          if (typeof spec.post === "function") {
            const postResult = spec.post(ctx, payload, result);
            if (postResult === false) {
              throwViolation(violation("SCM_TRANSITION_POSTCONDITION_FAILED", {
                phase: "transition",
                componentName: instance.componentName,
                transitionName: name,
                message: `Transition ${name} postcondition failed.`
              }), instance);
            }
          }

          const entry = {
            kind: "mcel-scm-evidence",
            contractVersion: CONTRACT_VERSION,
            generatedAt: now(),
            phase: "transition",
            ok: true,
            componentName: instance.componentName,
            transitionName: name,
            declaredReads: spec.reads,
            declaredWrites: spec.writes
          };
          recordEvidence(instance, entry);
          return {
            kind: "mcel-scm-transition-result",
            contractVersion: CONTRACT_VERSION,
            generatedAt: now(),
            ok: true,
            componentName: instance.componentName,
            transitionName: name,
            state: cloneValue(instance.state),
            source: cloneValue(instance.source),
            runtime: cloneValue(instance.runtime),
            evidence: jsonSafe(entry)
          };
        } catch (error) {
          if (error?.violation?.kind === "mcel-scm-violation") throw error;
          throwViolation(violation("SCM_TRANSITION_EXCEPTION", {
            phase: "transition",
            componentName: instance.componentName,
            transitionName: name,
            message: error?.message || String(error),
            errorName: error?.name || "Error"
          }), instance);
        }
      }

      function exportEvidence(instance) {
        return {
          kind: "mcel-scm-evidence-packet",
          contractVersion: CONTRACT_VERSION,
          generatedAt: now(),
          componentName: instance?.componentName || "",
          instanceId: instance?.id || "",
          evidence: Array.isArray(instance?.evidence) ? instance.evidence.map((entry) => jsonSafe(entry)) : []
        };
      }

      return Object.freeze({
        contractVersion: CONTRACT_VERSION,
        defineComponent,
        validateComponentManifest,
        listComponentDefinitions,
        componentDefinition,
        createComponentInstance,
        createChildContext,
        createEffectContext,
        runEffect,
        cancelEffect,
        transition,
        exportEvidence,
        defineRoute,
        validateRouteManifest,
        listRouteDefinitions,
        routeDefinition,
        createRouteInstance,
        enterRoute,
        leaveRoute,
        createRouteLoaderContext,
        runRouteLoader,
        cancelRouteLoader,
        exportRouteEvidence,
        clearRouteDefinitions,
        clearDefinitions
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabScm = McelLabScm;
    }
