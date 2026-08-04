"use strict";

const fs = require("node:fs");
const path = require("node:path");

const RESULT_SCHEMA = "mcel.counter-legacy-runtime-result.v1";

function serialize(value) {
  if (typeof value === "function") {
    return {$functionSource: Function.prototype.toString.call(value)};
  }
  if (Array.isArray(value)) {
    return value.map(serialize);
  }
  if (value && typeof value === "object") {
    const result = {};
    for (const key of Object.keys(value).sort()) {
      result[key] = serialize(value[key]);
    }
    return result;
  }
  return value;
}

async function readRequest() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  return JSON.parse(input || "{}");
}

async function importNamed(packageRoot, relativePath, exportName) {
  const absolutePath = path.resolve(packageRoot, relativePath);
  const sourceText = fs.readFileSync(absolutePath, "utf8");
  const dataUrl = `data:text/javascript;base64,${Buffer.from(sourceText, "utf8").toString("base64")}`;
  const moduleValue = await import(dataUrl);
  if (!Object.prototype.hasOwnProperty.call(moduleValue, exportName)) {
    throw new Error(`${relativePath} does not export ${exportName}.`);
  }
  return serialize(moduleValue[exportName]);
}

async function main() {
  const request = await readRequest();
  const packageRoot = path.resolve(String(request.packageRoot || ""));
  const exports = {
    domain: await importNamed(packageRoot, "contracts/domain.js", "ContractCounterDomain"),
    intents: await importNamed(packageRoot, "contracts/intents.js", "ContractCounterIntents"),
    surface: await importNamed(packageRoot, "contracts/surface.js", "ContractCounterSurface"),
    layout: await importNamed(packageRoot, "contracts/layout.js", "ContractCounterLayout"),
    acceptance: await importNamed(packageRoot, "contracts/acceptance.js", "ContractCounterAcceptance"),
    observation: await importNamed(packageRoot, "contracts/observation.js", "ContractCounterObservation"),
  };
  process.stdout.write(JSON.stringify({schema: RESULT_SCHEMA, valid: true, exports}));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    schema: RESULT_SCHEMA,
    valid: false,
    diagnostics: [{
      code: "MCEL_COUNTER_LEGACY_MODULE_IMPORT_FAILED",
      blocking: true,
      severity: "error",
      summary: error && error.message ? error.message : String(error),
    }],
  }));
});
