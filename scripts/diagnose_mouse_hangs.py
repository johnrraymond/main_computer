#!/usr/bin/env python3
"""
diagnose_mouse_hangs.py - Windows mouse hang diagnostic, no extra packages.

Run:
  python diagnose_mouse_hangs.py --seconds 120

While it runs, reproduce the hang and wiggle the mouse during the bad period.
It writes mouse_hang_report_*.json and prints likely leads.
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import threading
import time
from typing import Any

if platform.system().lower() != "windows":
    print("This script is Windows-only; it uses Win32 mouse/input APIs.", file=sys.stderr)
    raise SystemExit(2)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MOUSEWHEEL, WM_QUIT = 0x020A, 0x0012
GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x00000008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def filetime_int(ft: FILETIME) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def cpu_sample():
    idle, kern, user = FILETIME(), FILETIME(), FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    return filetime_int(idle), filetime_int(kern), filetime_int(user)


def cpu_percent(prev, cur):
    if not prev or not cur:
        return None
    idle_delta = cur[0] - prev[0]
    total_delta = (cur[1] - prev[1]) + (cur[2] - prev[2])
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 1)


def memory_snapshot() -> dict[str, Any]:
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return {}
    return {
        "memory_load_percent": int(m.dwMemoryLoad),
        "avail_phys_mb": round(m.ullAvailPhys / 1024 / 1024, 1),
        "total_phys_mb": round(m.ullTotalPhys / 1024 / 1024, 1),
    }


def cursor_pos() -> tuple[int, int]:
    p = POINT()
    return (int(p.x), int(p.y)) if user32.GetCursorPos(ctypes.byref(p)) else (-1, -1)


def last_input_age_ms():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    return max(0, int(kernel32.GetTickCount64()) - int(li.dwTime))


def win_text(hwnd: int) -> str:
    try:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def win_class(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""


def win_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def proc_image(pid: int) -> str:
    if pid <= 0:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        size = wt.DWORD(4096)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(h)
    return ""


def win_rect(hwnd: int):
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return [int(r.left), int(r.top), int(r.right), int(r.bottom)]


def exstyle(hwnd: int) -> int:
    try:
        f = user32.GetWindowLongPtrW if hasattr(user32, "GetWindowLongPtrW") else user32.GetWindowLongW
        return int(f(hwnd, GWL_EXSTYLE))
    except Exception:
        return 0


def describe_window(hwnd: int) -> dict[str, Any]:
    pid = win_pid(hwnd)
    img = proc_image(pid)
    return {
        "hwnd": int(hwnd),
        "pid": pid,
        "process_name": os.path.basename(img) if img else "",
        "process_image": img,
        "title": win_text(hwnd),
        "class": win_class(hwnd),
        "rect": win_rect(hwnd),
        "topmost": bool(exstyle(hwnd) & WS_EX_TOPMOST),
    }


def foreground_window() -> dict[str, Any]:
    hwnd = int(user32.GetForegroundWindow())
    return describe_window(hwnd) if hwnd else {}


def windows_at_cursor(limit: int = 12) -> list[dict[str, Any]]:
    x, y = cursor_pos()
    out: list[dict[str, Any]] = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if len(out) >= limit:
            return False
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            r = win_rect(int(hwnd))
            if not r:
                return True
            if r[0] <= x < r[2] and r[1] <= y < r[3]:
                d = describe_window(int(hwnd))
                if d["title"] or d["class"] or d["topmost"]:
                    out.append(d)
        except Exception:
            pass
        return True

    user32.EnumWindows(CB(cb), 0)
    return out


class MouseHook:
    def __init__(self):
        self.lock = threading.Lock()
        self.installed = False
        self.error = ""
        self.thread_id = 0
        self.handle = None
        self.cb_ref = None
        self.total = self.moves = self.buttons = self.wheels = 0
        self.last_point = None
        self.last_event_perf = 0.0
        self.last_msg = 0

    def start(self):
        t = threading.Thread(target=self._thread, daemon=True)
        t.start()
        deadline = time.perf_counter() + 2
        while time.perf_counter() < deadline:
            with self.lock:
                if self.installed or self.error:
                    return
            time.sleep(0.02)

    def stop(self):
        with self.lock:
            tid = self.thread_id
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass

    def snap(self):
        with self.lock:
            return {
                "installed": self.installed,
                "error": self.error,
                "total_events": self.total,
                "move_events": self.moves,
                "button_events": self.buttons,
                "wheel_events": self.wheels,
                "last_point": self.last_point,
                "last_msg": self.last_msg,
                "last_event_age_ms": None
                if not self.last_event_perf
                else round((time.perf_counter() - self.last_event_perf) * 1000, 1),
            }

    def _thread(self):
        self.thread_id = int(kernel32.GetCurrentThreadId())
        HOOKPROC = ctypes.WINFUNCTYPE(wt.LPARAM, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        def proc(nCode, wParam, lParam):
            if nCode >= 0:
                try:
                    data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    msg = int(wParam)
                    with self.lock:
                        self.total += 1
                        self.moves += 1 if msg == WM_MOUSEMOVE else 0
                        self.buttons += 1 if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP) else 0
                        self.wheels += 1 if msg == WM_MOUSEWHEEL else 0
                        self.last_point = [int(data.pt.x), int(data.pt.y)]
                        self.last_msg = msg
                        self.last_event_perf = time.perf_counter()
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self.cb_ref = HOOKPROC(proc)
        try:
            hmod = kernel32.GetModuleHandleW(None)
            self.handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self.cb_ref, hmod, 0)
            with self.lock:
                if not self.handle:
                    self.error = f"SetWindowsHookExW failed; GetLastError={kernel32.GetLastError()}"
                    return
                self.installed = True

            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            with self.lock:
                self.error = repr(e)
        finally:
            if self.handle:
                try:
                    user32.UnhookWindowsHookEx(self.handle)
                except Exception:
                    pass


def run(cmd: list[str], timeout=8) -> dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def ps_json(script: str, timeout=10):
    r = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)
    if not r.get("ok") or not r.get("stdout"):
        return {"_error": r}
    try:
        return json.loads(r["stdout"])
    except Exception:
        return {"_parse_error": r}


def listify(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict) and not any(str(k).startswith("_") for k in x):
        return [x]
    return []


def top_processes():
    return ps_json(
        r"""
$items = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
  ? { $_.IDProcess -ne 0 -and $_.Name -notin @('_Total','Idle') } |
  Sort PercentProcessorTime -Descending |
  Select -First 10 IDProcess,Name,PercentProcessorTime,WorkingSet
$items | ConvertTo-Json -Compress -Depth 3
""",
        6,
    )


def pnp_devices():
    return ps_json(
        r"""
$items = Get-PnpDevice |
  ? { $_.Class -in @('Mouse','HIDClass','Bluetooth','USB') -or $_.FriendlyName -match 'mouse|hid|usb|bluetooth|receiver|dongle|logitech|razer|corsair|steelseries|touchpad' } |
  Select Status,Class,FriendlyName,InstanceId,Problem
$items | ConvertTo-Json -Compress -Depth 4
""",
        12,
    )


def recent_events(minutes: int):
    return ps_json(
        rf"""
$since=(Get-Date).AddMinutes(-{int(minutes)})
$events = foreach ($log in @('System','Application')) {{
  Get-WinEvent -FilterHashtable @{{LogName=$log; StartTime=$since}} -ErrorAction SilentlyContinue
}}
$events |
  ? {{
    $_.ProviderName -match 'Kernel-PnP|DriverFrameworks|WHEA|Display|USB|Bluetooth|HID|HidBth|BTHUSB|mou|i8042|nvlddmkm|amdkmdag|igfx' -or
    $_.Message -match 'mouse|hid|usb|bluetooth|driver|reset|device|display|timeout|stopped responding|dpc|isr|power'
  }} |
  Sort TimeCreated -Descending |
  Select -First 80 TimeCreated,LogName,ProviderName,Id,LevelDisplayName,
    @{{Name='Message';Expression={{($PSItem.Message -replace '\s+',' ').Substring(0,[Math]::Min(500,($PSItem.Message -replace '\s+',' ').Length))}}}} |
  ConvertTo-Json -Compress -Depth 5
""",
        18,
    )


def powercfg():
    return {
        "active_scheme": run(["powercfg", "/getactivescheme"], 4),
        "usb": run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_USB"], 8),
    }


def win_fingerprint(w: dict[str, Any]) -> str:
    return " | ".join(
        str(x)
        for x in [
            w.get("process_name"),
            w.get("class"),
            (w.get("title") or "")[:80],
            "TOPMOST" if w.get("topmost") else "",
        ]
        if x
    )


def overlayish(w: dict[str, Any]) -> bool:
    h = " ".join(str(w.get(k, "")) for k in ("process_name", "process_image", "class", "title")).lower()
    return bool(w.get("topmost")) or any(
        s in h for s in ["main_computer", "main computer", "pyside", "qt", "qwindow", "chrome_widgetwin", "webengine"]
    )


def build_findings(report: dict[str, Any]) -> list[str]:
    out = []
    stalls = report["stall_events"]

    if stalls:
        mx = max(s["loop_gap_ms"] for s in stalls)
        out.append(
            f"{len(stalls)} scheduler/sample stalls detected; worst gap {mx:.1f} ms. "
            "This points to system/driver/load/DPC/GPU/storage/power stalls more than a normal UI bug."
        )
    else:
        out.append(
            "No scheduler/sample stall crossed the threshold. If the mouse hung during this run, focus on "
            "HID/USB/Bluetooth/wireless receiver/overlay/input capture rather than whole-system scheduling."
        )

    hook = report["mouse_hook"]
    out.append(
        f"Mouse hook installed={hook.get('installed')} moves={hook.get('move_events')} "
        f"total_events={hook.get('total_events')} error={hook.get('error')!r}."
    )

    if report["hook_moves_without_cursor_delta"]:
        out.append(
            f"{len(report['hook_moves_without_cursor_delta'])} samples had hook mouse-move events but no GetCursorPos movement. "
            "Many of these during a reproduced hang suggest overlay capture, cursor clamping, remote desktop capture, or driver filtering."
        )

    hits = []
    for s in stalls:
        for w in s.get("windows_at_cursor", [])[:4]:
            if overlayish(w):
                hits.append(win_fingerprint(w))
    if hits:
        out.append(
            "Overlay/topmost/Main-Computer-like windows under cursor during stalls: "
            + "; ".join(f"{k} x{v}" for k, v in collections.Counter(hits).most_common(5))
        )

    bad = []
    for d in listify(report.get("pnp_devices")):
        if str(d.get("Status", "")).upper() not in ("", "OK"):
            bad.append(f"{d.get('Class')} {d.get('FriendlyName')} Status={d.get('Status')} Problem={d.get('Problem')}")
    if bad:
        out.append("PnP devices not OK: " + "; ".join(bad[:8]))

    evs = listify(report.get("recent_events"))
    if evs:
        c = collections.Counter(f"{e.get('ProviderName')}#{e.get('Id')}" for e in evs if isinstance(e, dict)).most_common(8)
        out.append("Recent relevant event-log entries: " + "; ".join(f"{k} x{v}" for k, v in c) + ". Match their timestamps to your hang.")

    cpus = [s["cpu_percent"] for s in report["samples"] if isinstance(s.get("cpu_percent"), (int, float))]
    if cpus and max(cpus) >= 90:
        out.append(f"CPU reached {max(cpus):.1f}%; inspect top_process_snapshots in the JSON.")

    mems = [s.get("memory", {}).get("memory_load_percent") for s in report["samples"]]
    mems = [m for m in mems if isinstance(m, int)]
    if mems and max(mems) >= 90:
        out.append(f"Memory load reached {max(mems)}%; paging can feel like input hangs.")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--interval-ms", type=float, default=25)
    ap.add_argument("--stall-ms", type=float, default=250)
    ap.add_argument("--event-minutes", type=int, default=45)
    ap.add_argument("--out", default="")
    ap.add_argument("--skip-eventlog", action="store_true")
    args = ap.parse_args()

    started = dt.datetime.now()
    outpath = args.out or f"mouse_hang_report_{started.strftime('%Y%m%d_%H%M%S')}.json"

    print(f"Writing report to: {os.path.abspath(outpath)}")
    print("Reproduce the hang now. Wiggle the mouse during the bad period. Ctrl+C stops early.\n")

    hook = MouseHook()
    hook.start()

    report: dict[str, Any] = {
        "meta": {
            "started": started.isoformat(timespec="seconds"),
            "platform": platform.platform(),
            "python": sys.version,
            "pid": os.getpid(),
            "interval_ms": args.interval_ms,
            "stall_ms": args.stall_ms,
        },
        "initial_cursor": cursor_pos(),
        "initial_foreground_window": foreground_window(),
        "pnp_devices": pnp_devices(),
        "powercfg": powercfg(),
        "samples": [],
        "stall_events": [],
        "hook_moves_without_cursor_delta": [],
        "top_process_snapshots": [],
    }

    interval = max(0.005, args.interval_ms / 1000)
    end = time.perf_counter() + max(1, args.seconds)
    last_tick = time.perf_counter()
    last_cursor = cursor_pos()
    last_hook_moves = hook.snap()["move_events"]
    prev_cpu = cpu_sample()
    next_status = time.perf_counter() + 5
    next_proc = time.perf_counter()
    latest_top = None

    try:
        while time.perf_counter() < end:
            time.sleep(interval)
            t = time.perf_counter()
            gap = (t - last_tick) * 1000
            last_tick = t

            cur_cpu = cpu_sample()
            cpu = cpu_percent(prev_cpu, cur_cpu)
            prev_cpu = cur_cpu

            cur = cursor_pos()
            hs = hook.snap()
            move_delta = int(hs["move_events"]) - int(last_hook_moves)
            last_hook_moves = hs["move_events"]

            sample = {
                "t": now_iso(),
                "loop_gap_ms": round(gap, 2),
                "cursor": cur,
                "cursor_changed": cur != last_cursor,
                "last_input_age_ms": last_input_age_ms(),
                "hook_move_delta": move_delta,
                "hook_last_point": hs.get("last_point"),
                "cpu_percent": cpu,
                "memory": memory_snapshot(),
            }
            report["samples"].append(sample)

            if move_delta >= 3 and cur == last_cursor:
                report["hook_moves_without_cursor_delta"].append(sample)

            stall = gap >= args.stall_ms
            if t >= next_proc:
                latest_top = top_processes()
                report["top_process_snapshots"].append(
                    {"t": now_iso(), "reason": "periodic", "top_processes": latest_top}
                )
                next_proc = t + 10

            if stall:
                event = dict(sample)
                event["foreground_window"] = foreground_window()
                event["windows_at_cursor"] = windows_at_cursor()
                event["top_processes"] = latest_top
                report["stall_events"].append(event)
                fg = event["foreground_window"]
                print(
                    f"[STALL] {event['t']} gap={gap:.1f}ms cursor={cur} "
                    f"fg={fg.get('process_name') or fg.get('title') or 'unknown'} cpu={cpu}"
                )

            if t >= next_status:
                elapsed = args.seconds - max(0, end - t)
                print(
                    f"[{elapsed:5.1f}s] cursor={cur} input_idle={sample['last_input_age_ms']}ms "
                    f"hook_moves={hs['move_events']} stalls={len(report['stall_events'])} cpu={cpu}"
                )
                next_status = t + 5

            last_cursor = cur

    except KeyboardInterrupt:
        print("\nStopped; writing report.")
    finally:
        hook.stop()

    report["ended"] = dt.datetime.now().isoformat(timespec="seconds")
    report["final_cursor"] = cursor_pos()
    report["final_foreground_window"] = foreground_window()
    report["mouse_hook"] = hook.snap()

    if not args.skip_eventlog:
        print("Collecting recent USB/HID/Bluetooth/display/driver Event Log entries...")
        report["recent_events"] = recent_events(args.event_minutes)

    if len(report["samples"]) > 20000:
        n = max(2, len(report["samples"]) // 10000)
        report["samples_decimated_note"] = f"Kept every {n}th ordinary sample; stall_events are preserved separately."
        report["samples"] = report["samples"][::n]

    report["findings"] = build_findings(report)

    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\nFindings:")
    for fnd in report["findings"]:
        print("-", fnd)

    print(f"\nReport written: {os.path.abspath(outpath)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())