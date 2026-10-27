"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id: "promise-inventory", title: "Inventory"}, dsl => {
  const sku = dsl.field.text().minLength(1);
  const name = dsl.field.text().minLength(1);
  const quantity = dsl.field.integer().minimum(0);
  const category = dsl.field.text().minLength(1);
  const item = dsl.field.record({sku, name, quantity, category});
  const items = dsl.state.canonical("items", dsl.field.list(item, {key: "sku"}), {initial: []});
  const operations = dsl.intent.keyedCollection("item", items);
  const form = dsl.surface.form("new-item", {fields: {sku, name, quantity, category}, intent: operations.add});
  const table = dsl.surface.collection("items", {state: items, key: "sku", fields: ["sku", "name", "quantity", "category"],
    actions: operations});
  const root = dsl.surface.define("inventory", {root: dsl.surface.region("workspace", {children: [form, table]})});
  const layout = dsl.layout.define("workspace", {surface: root, orderedChildren: [root.node("new-item"), root.node("items")]});
  return {states: [items], intents: [operations.add, operations.update, operations.remove], surfaces: [root], layouts: [layout]};
});
