/* MCEL Icon v1. Independent browser adapter over the canonical JSON registry.
 * No fetch, launcher dependency, globals beyond MCELIcon, or control behavior.
 * Load by passing the JSON object to MCELIcon.create(registry). The Python
 * adapter reads the same registry and generates equivalent SVG geometry.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MCELIcon = api;
})(typeof globalThis !== "undefined" ? globalThis : null, function () {
  "use strict";
  const SCHEMA = "mcel.icon-registry.v1";
  const FAMILIES = new Set(["application", "frober", "action", "resource", "status", "navigation", "interface"]);
  const SHAPES = {
    path: new Set(["d"]), circle: new Set(["cx", "cy", "r"]),
    rect: new Set(["x", "y", "width", "height", "rx", "ry"]),
    line: new Set(["x1", "y1", "x2", "y2"]),
    ellipse: new Set(["cx", "cy", "rx", "ry"]),
    polyline: new Set(["points"]), polygon: new Set(["points"]),
  };
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ID = /^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$/;
  const CLASS = /^[A-Za-z_-][A-Za-z0-9_-]*(?:\s+[A-Za-z_-][A-Za-z0-9_-]*)*$/;
  const NUMBER = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
  const PATH = /^[MmLlHhVvCcSsQqTtAaZz0-9+.,\-\s]+$/;
  const POINTS = /^[0-9+.,\-\s]+$/;

  function refuse(message) { throw new Error("MCEL Icon: " + message); }
  function own(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
  function exactKeys(obj, keys) {
    return !!obj && typeof obj === "object" && !Array.isArray(obj) &&
      Object.keys(obj).length === keys.length && keys.every(key => own(obj, key));
  }
  function check(registry) {
    if (!exactKeys(registry, ["schema", "viewBox", "icons"]) ||
        registry.schema !== SCHEMA || registry.viewBox !== "0 0 24 24" ||
        !Array.isArray(registry.icons) || !registry.icons.length)
      refuse("invalid registry contract");
    const ids = new Set();
    for (const icon of registry.icons) {
      if (!exactKeys(icon, ["id", "family", "label", "shapes"]) ||
          typeof icon.id !== "string" || !ID.test(icon.id) || ids.has(icon.id) ||
          typeof icon.family !== "string" || !FAMILIES.has(icon.family) ||
          !icon.id.startsWith(icon.family + ".") ||
          typeof icon.label !== "string" || !icon.label.trim() || icon.label.length > 96 ||
          !Array.isArray(icon.shapes) || !icon.shapes.length || icon.shapes.length > 48)
        refuse("invalid icon identity: " + String(icon?.id));
      for (const shape of icon.shapes) {
        if (!exactKeys(shape, ["tag", "attrs"]) || !own(SHAPES, shape.tag) ||
            !shape.attrs || typeof shape.attrs !== "object" ||
            Array.isArray(shape.attrs) || !Object.keys(shape.attrs).length)
          refuse("invalid SVG shape: " + icon.id);
        for (const [name, value] of Object.entries(shape.attrs)) {
          if (!(SHAPES[shape.tag].has(name) || name === "fill" || name === "stroke") ||
              typeof value !== "string" || !value || value.length > 1024 ||
              !(name === "d" ? PATH.test(value) : name === "points" ? POINTS.test(value) :
                name === "fill" || name === "stroke" ? ["none", "currentColor"].includes(value) :
                NUMBER.test(value)))
            refuse("unsafe SVG data: " + icon.id + "." + name);
        }
      }
      ids.add(icon.id);
    }
    return registry;
  }
  function esc(value) {
    return String(value).replace(/[&<>"']/g, char =>
      ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
  }
  function optionsFor(options) {
    const supplied = options || {};
    const size = supplied.size === undefined ? 24 : supplied.size;
    const className = supplied.className === undefined ? "" : supplied.className;
    const ariaLabel = supplied.ariaLabel === undefined ? null : supplied.ariaLabel;
    if (!Number.isInteger(size) || size < 8 || size > 256 ||
        typeof className !== "string" || (className && !CLASS.test(className)) ||
        (ariaLabel !== null && (typeof ariaLabel !== "string" || !ariaLabel.trim())))
      refuse("invalid rendering options");
    return {size, className, ariaLabel};
  }
  function create(registry) {
    const icons = new Map();
    for (const icon of check(registry).icons) {
      // Snapshot geometry so application code cannot mutate the authority.
      const drawing = {
        id: icon.id, family: icon.family, label: icon.label,
        shapes: icon.shapes.map(shape => ({tag: shape.tag, attrs: {...shape.attrs}})),
      };
      for (const shape of drawing.shapes) { Object.freeze(shape.attrs); Object.freeze(shape); }
      Object.freeze(drawing.shapes);
      icons.set(icon.id, Object.freeze(drawing));
    }
    function get(id) {
      const icon = icons.get(id);
      if (!icon) refuse("unknown icon identity: " + String(id));
      return icon;
    }
    function list(family) {
      if (family !== undefined && !FAMILIES.has(family)) refuse("unknown family: " + family);
      return Array.from(icons.values()).filter(icon => family === undefined || icon.family === family)
        .map(({id, family, label}) => ({id, family, label}));
    }
    function svg(id, options) {
      const icon = get(id);
      const {size, className, ariaLabel} = optionsFor(options);
      const attrs = [
        `xmlns="${SVG_NS}"`, `class="mcel-icon-svg${className ? " " + className : ""}"`,
        'viewBox="0 0 24 24"', `width="${size}"`, `height="${size}"`,
        'fill="none"', 'stroke="currentColor"', 'stroke-width="2.2"',
        'stroke-linecap="round"', 'stroke-linejoin="round"',
        `data-mcel-icon-id="${icon.id}"`, `data-mcel-icon-family="${icon.family}"`,
        'focusable="false"',
      ];
      if (ariaLabel === null) attrs.push('aria-hidden="true"');
      else attrs.push('role="img"', `aria-label="${esc(ariaLabel)}"`);
      const inner = icon.shapes.map(shape =>
        `<${shape.tag} ${Object.entries(shape.attrs).map(([k,v]) => `${k}="${esc(v)}"`).join(" ")}/>`).join("");
      return `<svg ${attrs.join(" ")}> ${inner}</svg>`;
    }
    function element(id, options, doc) {
      const icon = get(id);
      const {size, className, ariaLabel} = optionsFor(options);
      const documentRef = doc || (typeof document !== "undefined" ? document : null);
      if (!documentRef?.createElementNS) refuse("document required for element rendering");
      const el = documentRef.createElementNS(SVG_NS, "svg");
      const attrs = {
        class: "mcel-icon-svg" + (className ? " " + className : ""),
        viewBox: "0 0 24 24", width: String(size), height: String(size),
        fill: "none", stroke: "currentColor", "stroke-width": "2.2",
        "stroke-linecap": "round", "stroke-linejoin": "round",
        "data-mcel-icon-id": icon.id, "data-mcel-icon-family": icon.family,
        focusable: "false",
      };
      if (ariaLabel === null) attrs["aria-hidden"] = "true";
      else { attrs.role = "img"; attrs["aria-label"] = ariaLabel; }
      for (const [k,v] of Object.entries(attrs)) el.setAttribute(k, v);
      for (const shape of icon.shapes) {
        const node = documentRef.createElementNS(SVG_NS, shape.tag);
        for (const [k,v] of Object.entries(shape.attrs)) node.setAttribute(k, v);
        el.appendChild(node);
      }
      return el;
    }
    function mount(target, id, options) {
      if (!target?.replaceChildren) refuse("mount target must be an element");
      const rendered = element(id, options, target.ownerDocument);
      target.replaceChildren(rendered);
      return rendered;
    }
    return Object.freeze({schema: SCHEMA, has: id => icons.has(id), get, list, svg, element, mount});
  }
  function fromDocument(scriptId = "mcel-icon-registry", doc) {
    const documentRef = doc || (typeof document !== "undefined" ? document : null);
    const node = documentRef?.getElementById(scriptId);
    if (!node || node.getAttribute("type") !== "application/json") refuse("registry script missing: " + scriptId);
    return create(JSON.parse(node.textContent));
  }
  return Object.freeze({schema: SCHEMA, create, fromDocument});
});
