"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-list", title: "Promise List"},
  (dsl) => {
    const item = dsl.field.record({
      sku: dsl.field.text().minLength(1),
      label: dsl.field.text(),
    });
    const items = dsl.state.canonical("items", dsl.field.list(item, {key: "sku"}), {initial: []});
    return {states: [items]};
  }
);
