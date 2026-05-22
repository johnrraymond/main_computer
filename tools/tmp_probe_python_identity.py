import json, os, sys
info = {
  "executable": sys.executable,
  "_base_executable": getattr(sys, "_base_executable", ""),
  "base_executable": getattr(sys, "base_executable", ""),
  "prefix": sys.prefix,
  "base_prefix": sys.base_prefix,
  "in_venv": sys.prefix != sys.base_prefix,
  "version": sys.version,
}
for k in ("_base_executable", "base_executable"):
    v = info.get(k) or ""
    info[k + "_exists"] = bool(v and os.path.exists(v))
bp = os.path.join(sys.base_prefix, "python.exe")
info["base_prefix_python"] = bp
info["base_prefix_python_exists"] = os.path.exists(bp)
print(json.dumps(info, indent=2))
