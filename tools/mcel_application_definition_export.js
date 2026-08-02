#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const path = require("path");

function normalizedFunctionSource(value) {
  const source = Function.prototype.toString.call(value).trim();
  if (/^(?:async\s+)?function(?:\s*\*)?\b/.test(source)) return source;
  if (/^(?:async\s*)?(?:\([^]*?\)|[A-Za-z_$][\w$]*)\s*=>/.test(source)) return source;
  const asyncGenerator = source.match(/^async\s+\*([A-Za-z_$][\w$]*)\s*(\([^]*\))\s*\{/);
  if (asyncGenerator) return `async function* ${asyncGenerator[1]}${source.slice(source.indexOf("("))}`;
  const generator = source.match(/^\*([A-Za-z_$][\w$]*)\s*(\([^]*\))\s*\{/);
  if (generator) return `function* ${generator[1]}${source.slice(source.indexOf("("))}`;
  const method = source.match(/^([A-Za-z_$][\w$]*)\s*\(/);
  if (method) return `function ${source}`;
  return source;
}

function stableObject(value, seen = new Map()) {
  if (typeof value === "function") {
    const source = normalizedFunctionSource(value);
    return {
      $function: source,
      sha256: crypto.createHash("sha256").update(source, "utf8").digest("hex")
    };
  }
  if (value === undefined) return {$undefined: true};
  if (value === null || typeof value !== "object") return value;
  if (seen.has(value)) throw new Error("Application definition contains a cycle.");
  seen.set(value, true);
  if (Array.isArray(value)) {
    const result = value.map((entry) => stableObject(entry, seen));
    seen.delete(value);
    return result;
  }
  const result = {};
  Object.keys(value).sort().forEach((key) => {
    if (key === "validate") return;
    result[key] = stableObject(value[key], seen);
  });
  seen.delete(value);
  return result;
}

const definitionPath = process.argv[2];
if (!definitionPath) {
  process.stderr.write("Usage: mcel_application_definition_export.js <application.js>\n");
  process.exit(2);
}

try {
  const resolved = path.resolve(definitionPath);
  delete require.cache[resolved];
  const application = require(resolved);
  process.stdout.write(JSON.stringify(stableObject(application)));
} catch (error) {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(3);
}
