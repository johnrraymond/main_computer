// Run with: node tests/test_mcel_activity_dock_minimal_moves.js
// Exercises the production renderActivityDock() ordering logic without external packages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../main_computer/web/applications/scripts/activity-dock.js');
const source = fs.readFileSync(filename, 'utf8');
const start = source.indexOf('function renderActivityDock() {');
const end = source.indexOf('function setFilter(', start);
assert.ok(start >= 0 && end > start, 'Production renderer not found');

class Card {
  constructor(id) { this.__activityEventId = String(id); this.parentNode = null; this.renderedTitle = ''; }
  remove() { if (this.parentNode) this.parentNode.detach(this); }
}
class List {
  constructor() { this.children = []; this.resetCounts(); }
  resetCounts() { this.moves = 0; this.additions = 0; this.removals = 0; }
  detach(card) {
    const index = this.children.indexOf(card);
    assert.ok(index >= 0, 'detached card must belong to the list');
    this.children.splice(index, 1);
    card.parentNode = null;
    this.removals++;
  }
  insertBefore(card, anchor) {
    if (anchor) assert.ok(this.children.includes(anchor), 'anchor must already be mounted');
    if (card.parentNode) {
      assert.equal(card.parentNode, this);
      card.remove();
      this.moves++;
    }
    const index = anchor ? this.children.indexOf(anchor) : this.children.length;
    this.children.splice(index, 0, card);
    card.parentNode = this;
    this.additions++;
  }
}

const list = new List();
const state = {filter: 'live', filters: {live: {label: 'Live'}}, events: []};
const context = vm.createContext({
  activityState: state,
  dom: {events: () => list, filterState: () => null, summary: () => null},
  document: {querySelectorAll: () => [], createElement: () => new Card('empty')},
  allEvents: () => state.events,
  eventMatchesFilter: () => true,
  renderEvent: (event) => { const card = new Card(event.id); card.renderedTitle = event.title; return card; },
  updateEventCard: (card, event) => { card.renderedTitle = event.title; return card; },
  renderHeartbeat: () => {}, renderAiSessionBox: () => {}, renderMetaModel: () => {},
});
vm.runInContext(source.slice(start, end), context, {filename});

function render(ids, titles = {}) {
  state.events = ids.map((id) => ({id: String(id), title: titles[id] || String(id)}));
  vm.runInContext('renderActivityDock()', context);
}
function assertOrder(ids) {
  assert.deepEqual(list.children.map((card) => card.__activityEventId), ids.map(String));
}
function reset(ids) {
  list.children.forEach((card) => { card.parentNode = null; });
  list.children = [];
  render(ids);
  list.resetCounts();
}
function lisLength(items) {
  const dp = items.map(() => 1);
  for (let i = 0; i < items.length; i++)
    for (let j = 0; j < i; j++)
      if (items[j] < items[i]) dp[i] = Math.max(dp[i], dp[j] + 1);
  return Math.max(0, ...dp);
}

const eighty = Array.from({length: 80}, (_, i) => i);
reset(eighty);
const before = [...list.children];
render(eighty);
assertOrder(eighty);
assert.equal(list.additions, 0);
assert.equal(list.removals, 0);
assert.deepEqual(list.children, before, 'unchanged cards retain identity');
console.log('PASS unchanged 80 cards: zero DOM mutations');

reset(eighty);
const toFront = [79, ...eighty.slice(0, 79)];
render(toFront);
assertOrder(toFront);
assert.equal(list.moves, 1);
assert.equal(list.additions, 1);
assert.equal(list.removals, 1);
console.log('PASS moving final card to front: one move (not 79)');

reset(eighty);
const toTail = [...eighty.slice(1), 0];
render(toTail);
assertOrder(toTail);
assert.equal(list.moves, 1);
console.log('PASS moving first card to tail: one move');

reset(eighty);
const position75 = [...eighty.slice(1, 76), 0, ...eighty.slice(76)];
render(position75);
assertOrder(position75);
assert.equal(list.moves, 1);
console.log('PASS previously observed 75-move rotation: one move');

reset(eighty);
render([80, ...eighty.slice(0, 79)]);
assertOrder([80, ...eighty.slice(0, 79)]);
assert.equal(list.moves, 0);
assert.equal(list.additions, 1);
assert.equal(list.removals, 1);
console.log('PASS 80-card rollover: one new, one removed, no moves');

reset([1, 2, 3]);
const preserved = list.children[1];
render([1, 2, 3], {2: 'updated'});
assert.equal(list.additions, 0);
assert.equal(list.removals, 0);
assert.equal(list.children[1], preserved);
assert.equal(preserved.renderedTitle, 'updated');
console.log('PASS content updates: retain node without movement');

reset([1, 2, 3]);
render([3, 2, 1]);
assertOrder([3, 2, 1]);
assert.equal(list.moves, 2);
console.log('PASS reversed ordering: minimum two moves');

reset(eighty);
const mixed = [...eighty];
for (let i = mixed.length - 1; i > 0; i--) {
  const j = (i * 17 + 13) % (i + 1);
  [mixed[i], mixed[j]] = [mixed[j], mixed[i]];
}
render(mixed);
assertOrder(mixed);
assert.equal(list.moves, eighty.length - lisLength(mixed));
console.log('PASS arbitrary permutation: minimal moves via LIS');

reset([1, 2, 3]);
render([1, 3]);
assertOrder([1, 3]);
assert.equal(list.moves, 0);
assert.equal(list.additions, 0);
assert.equal(list.removals, 1);
console.log('PASS removed event: one removal');
