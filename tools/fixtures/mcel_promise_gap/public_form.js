"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-form", title: "Promise Form"},
  (dsl) => {
    const sku = dsl.field.text().minLength(1);
    const quantity = dsl.field.integer().minimum(0);
    const item = dsl.field.record({sku, quantity});
    const items = dsl.state.canonical("items", dsl.field.list(item, {key: "sku"}), {initial: []});
    const operations = dsl.intent.keyedCollection("item", items);
    const form = dsl.surface.form("add-item", {fields: {sku, quantity}, intent: operations.add});
    const root = dsl.surface.define("form", {root: dsl.surface.region("main", {children: [form]})});
    return {states: [items], intents: [operations.add, operations.update, operations.remove], surfaces: [root]};
  }
);
