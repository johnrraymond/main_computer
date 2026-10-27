"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-surface", title: "Promise Surface"},
  (dsl) => {
    const item = dsl.field.record({sku: dsl.field.text().minLength(1), name: dsl.field.text(), quantity: dsl.field.integer()});
    const items = dsl.state.canonical("items", dsl.field.list(item, {key: "sku"}), {initial: []});
    const actions = dsl.intent.keyedCollection("item", items);
    const collection = dsl.surface.collection("items", {state: items, key: "sku", fields: ["name", "quantity"], actions});
    const root = dsl.surface.define("collection", {root: dsl.surface.region("main", {children: [collection]})});
    return {states: [items], intents: [actions.add, actions.update, actions.remove], surfaces: [root]};
  }
);
