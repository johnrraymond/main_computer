;(function () {
  // Patch S: browser-safe renderer module registry for extracted shuttle-3D renderer passes.
  const root = globalThis.MainComputerShuttle3DRendererModules =
    globalThis.MainComputerShuttle3DRendererModules || {};
  const modules = root.modules = root.modules || {};

  root.register = function registerShuttle3DRendererModule(name, methods) {
    const key = String(name || "").trim();
    if (!key) return null;
    const current = modules[key] || {};
    modules[key] = Object.assign(current, methods || {});
    return modules[key];
  };

  root.method = function shuttle3DRendererModuleMethod(name, methodName) {
    const module = modules[String(name || "")];
    if (!module) return null;
    const method = module[String(methodName || "")];
    return typeof method === "function" ? method : null;
  };

  root.call = function callShuttle3DRendererModule(name, methodName, context, ...args) {
    const method = root.method(name, methodName);
    if (!method) return undefined;
    return method.apply(context, args);
  };
})();
