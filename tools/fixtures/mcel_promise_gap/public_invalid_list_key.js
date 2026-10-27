"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-invalid-list-key", title: "Invalid List Key"},
  (dsl) => {
    // Deliberate invalid declaration: a string has no named sku field.
    const item = dsl.field.text();
    const items = dsl.state.canonical("items", dsl.field.list(item, {key: "sku"}), {initial: []});
    return {items};
  }
);
