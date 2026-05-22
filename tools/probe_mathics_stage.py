import faulthandler
import importlib.util
import sys
import time
import traceback

faulthandler.enable()
faulthandler.dump_traceback_later(20, repeat=True)

t0 = time.monotonic()

def mark(label):
    print(f"{time.monotonic() - t0:8.3f}s | {label}", flush=True)

try:
    mark(f"python = {sys.executable}")

    mark("find_spec mathics.core.load_builtin")
    print(importlib.util.find_spec("mathics.core.load_builtin"), flush=True)

    mark("find_spec mathics.session")
    print(importlib.util.find_spec("mathics.session"), flush=True)

    mark("import import_and_load_builtins")
    from mathics.core.load_builtin import import_and_load_builtins

    mark("import MathicsSession")
    from mathics.session import MathicsSession

    mark("call import_and_load_builtins()")
    import_and_load_builtins()

    mark("construct MathicsSession(add_builtin=True, catch_interrupt=True)")
    session = MathicsSession(add_builtin=True, catch_interrupt=True)

    mark("evaluate 2+2")
    result = session.evaluate("2+2")

    mark("format_output")
    text = session.evaluation.format_output(result)

    mark(f"RESULT = {text!r}")
except Exception:
    mark("EXCEPTION")
    traceback.print_exc()
    raise
