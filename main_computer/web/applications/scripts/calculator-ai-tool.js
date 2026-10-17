"use strict";

// Headless entrypoint only. The calculation algorithm lives in calculator-core.js.
const core = require("./calculator-core.js");
const MAX_STDIN_BYTES = 8192;
let requestText = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  requestText += chunk;
  if (Buffer.byteLength(requestText, "utf8") > MAX_STDIN_BYTES) {
    process.stderr.write("Calculator request exceeds input limit.\n");
    process.exitCode = 2;
    process.stdin.destroy();
  }
});
process.stdin.on("end", () => {
  if (process.exitCode) return;
  try {
    const request = JSON.parse(requestText);
    if (!request || typeof request !== "object" || Array.isArray(request)
        || request.operation !== "evaluate-expression"
        || typeof request.expression !== "string"
        || !request.expression.trim()
        || request.expression.length > core.limits.maxExpressionLength) {
      throw new Error("Invalid bounded calculator.evaluate request.");
    }
    const evaluation = core.evaluateCalculatorExpression(request.expression);
    process.stdout.write(JSON.stringify({
      schema: "mcel.calculator.headless-core-result.v1",
      engine: core.schema,
      evaluation
    }) + "\n");
  } catch (error) {
    process.stderr.write("Calculator tool refused request: " + String(error.message || error) + "\n");
    process.exitCode = 2;
  }
});
