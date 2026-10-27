"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-record", title: "Promise Record"},
  (dsl) => {
    const item = dsl.field.record({sku: dsl.field.text(), quantity: dsl.field.integer()});
    const current = dsl.state.canonical("current", item, {initial: {sku: "initial", quantity: 0}});
    return {states: [current]};
  }
);
