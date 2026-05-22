#!/usr/bin/env python3
"""
Main Computer experimental operating shell.

Install:
    pip install PySide6

Run against an already-running Main Computer server:
    python -m main_computer.main_computer_shell

Or start the server too:
    python -m main_computer.main_computer_shell --start-server

Behavior:
- The shell dock is its own controller and stays open even when all app windows close.
- The dock collapses to a 10-pixel left-edge strip when the mouse is not over it.
- The dock expands while the mouse is inside the strip/dock.
- Each app launches in its own subprocess so Windows can give each app window its
  own taskbar identity/icon instead of treating everything as one browser window.
- No Chrome tabs, omnibox, bookmarks, or browser UI.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QPushButton, QVBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


@dataclass(frozen=True)
class ShellApp:
    key: str
    title: str
    path: str
    icon_text: str
    icon_color: str


APPS = [
    ShellApp("spreadsheet", "Spreadsheet", "/applications/spreadsheet", "S", "#60a5fa"),
    ShellApp("chat", "Chat Console", "/applications/chat-console", "C", "#34d399"),
    ShellApp("code", "Code Editor", "/applications/code-editor", "E", "#f59e0b"),
    ShellApp("terminal", "Terminal", "/applications/terminal", "T", "#a78bfa"),
    ShellApp("tasks", "Tasks", "/applications/task-manager", "K", "#fb7185"),
    ShellApp("files", "Files", "/applications/file-explorer", "F", "#facc15"),
]

APP_BY_KEY = {app.key: app for app in APPS}


def windows_app_id_for_app(app: ShellApp) -> str:
    """Return a stable, unique Windows shell identity for one Main Computer app."""
    return f"com.maincomputer.shell.{app.key}"


def set_windows_app_id(app_id: str) -> None:
    """Give Windows a stable per-process taskbar identity when possible."""
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        # AppUserModelID is a best-effort shell hint; the app still runs without it.
        pass


class WindowsGUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _windows_guid_from_string(value: str) -> WindowsGUID:
    guid = WindowsGUID()
    ctypes.oledll.ole32.CLSIDFromString(str(value), ctypes.byref(guid))
    return guid


def set_windows_window_app_id(
    window: QMainWindow,
    app_id: str,
    *,
    relaunch_command: str = "",
    display_name: str = "",
) -> bool:
    """Set a per-window AppUserModelID so Windows taskbar grouping splits apps.

    SetCurrentProcessExplicitAppUserModelID is often not enough for Python/Qt
    shells because every child app is still hosted by python.exe. Stamping the
    top-level HWND with PKEY_AppUserModel_ID gives Windows a window-level app
    identity, which is the important piece for separate taskbar groups.
    """

    if not sys.platform.startswith("win"):
        return False

    try:
        ctypes.oledll.ole32.CoInitialize(None)
    except Exception:
        # COM may already be initialized by Qt/WebEngine. That is fine.
        pass

    try:
        hwnd = int(window.winId())
        if hwnd <= 0:
            return False

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [
                ("fmtid", WindowsGUID),
                ("pid", ctypes.c_ulong),
            ]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [
                ("vt", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort),
                ("wReserved3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p),
            ]

        VT_LPWSTR = 31
        IID_IPropertyStore = _windows_guid_from_string("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
        property_fmtid = _windows_guid_from_string("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")

        PKEY_AppUserModel_RelaunchCommand = PROPERTYKEY(property_fmtid, 2)
        PKEY_AppUserModel_RelaunchDisplayNameResource = PROPERTYKEY(property_fmtid, 4)
        PKEY_AppUserModel_ID = PROPERTYKEY(property_fmtid, 5)

        shell32 = ctypes.windll.shell32
        shell32.SHGetPropertyStoreForWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WindowsGUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long

        store = ctypes.c_void_p()
        hr = shell32.SHGetPropertyStoreForWindow(
            ctypes.c_void_p(hwnd),
            ctypes.byref(IID_IPropertyStore),
            ctypes.byref(store),
        )
        if hr < 0 or not store.value:
            return False

        vtbl = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_value = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(PROPERTYKEY),
            ctypes.POINTER(PROPVARIANT),
        )(vtbl[6])
        commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])

        def write_string(key: PROPERTYKEY, value: str) -> None:
            if not value:
                return
            prop = PROPVARIANT(VT_LPWSTR, 0, 0, 0, str(value))
            set_value(store, ctypes.byref(key), ctypes.byref(prop))

        try:
            write_string(PKEY_AppUserModel_ID, app_id)
            write_string(PKEY_AppUserModel_RelaunchCommand, relaunch_command)
            write_string(PKEY_AppUserModel_RelaunchDisplayNameResource, display_name)
            commit(store)
        finally:
            release(store)

        return True
    except Exception:
        return False


def make_icon(text: str, color: str) -> QIcon:
    """Create a small generated icon so each app window has its own visual identity."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    painter.setPen(QColor("#101411"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(30)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, text[:2].upper())
    painter.end()

    return QIcon(pixmap)


def app_url(base_url: str, app: ShellApp) -> str:
    return f"{base_url.rstrip('/')}{app.path}"


def current_script_invocation() -> list[str]:
    """Return a subprocess command that re-enters this shell module/script."""
    script = Path(__file__).resolve()
    if script.exists():
        return [sys.executable, str(script)]
    return [sys.executable, "-m", "main_computer.main_computer_shell"]


def app_window_command(app: ShellApp, base_url: str) -> list[str]:
    return [
        *current_script_invocation(),
        "--app-window",
        app.key,
        "--base-url",
        base_url.rstrip("/"),
    ]


def raise_process_window(pid: int) -> bool:
    """Best-effort focus for an existing app process on Windows."""
    if not sys.platform.startswith("win") or pid <= 0:
        return False

    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        found_hwnd = ctypes.c_void_p(0)

        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if int(window_pid.value) != int(pid):
                return True
            found_hwnd.value = int(hwnd)
            return False

        user32.EnumWindows(enum_proc_type(enum_proc), 0)

        if not found_hwnd.value:
            return False

        user32.ShowWindow(found_hwnd, SW_RESTORE)
        return bool(user32.SetForegroundWindow(found_hwnd))
    except Exception:
        return False


class AppWindow(QMainWindow):
    def __init__(self, app: ShellApp, url: str) -> None:
        super().__init__()
        self.shell_app = app
        self.setWindowTitle(f"Main Computer - {app.title}")
        self.setWindowIcon(make_icon(app.icon_text, app.icon_color))
        self.resize(1200, 800)

        self.view = QWebEngineView()
        self.view.setUrl(QUrl(url))
        self.setCentralWidget(self.view)

        reload_action = QAction("Reload", self)
        reload_action.setShortcut("Ctrl+R")
        reload_action.triggered.connect(self.view.reload)
        self.addAction(reload_action)

    def focus_shell_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


class EdgeDock(QFrame):
    COLLAPSED_WIDTH = 10
    EXPANDED_WIDTH = 230

    def __init__(self, shell: "ShellController") -> None:
        super().__init__()
        self.shell = shell
        self.expanded = False
        self.buttons: list[QPushButton] = []

        self.setWindowTitle("Main Computer Dock")
        self.setWindowIcon(make_icon("MC", "#7fd190"))

        # This is the controller strip, not an application window. Keep it above
        # normal windows and out of the taskbar.
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )

        self.setMouseTracking(True)
        self.setObjectName("edgeDock")
        self.setMinimumSize(0, 0)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setLayout(self.layout)

        for app in APPS:
            button = QPushButton(app.title)
            button.setIcon(make_icon(app.icon_text, app.icon_color))
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(0)
            button.clicked.connect(lambda checked=False, item=app: self.shell.open_app(item))
            self.layout.addWidget(button)
            self.buttons.append(button)

        self.layout.addStretch(1)

        close_button = QPushButton("Quit Shell")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setMinimumWidth(0)
        close_button.clicked.connect(QApplication.instance().quit)
        self.layout.addWidget(close_button)
        self.buttons.append(close_button)

        self.setStyleSheet(
            """
            QFrame#edgeDock {
                background: #101411;
                border-right: 1px solid #303830;
            }

            QPushButton {
                background: #1d241f;
                color: #eef7ed;
                border: 1px solid #3a463d;
                border-radius: 8px;
                padding: 10px;
                text-align: left;
                font-size: 14px;
            }

            QPushButton:hover {
                background: #2a342d;
                border-color: #7fd190;
            }
            """
        )

        # Rely on global cursor polling instead of enter/leave events. A frameless
        # always-on-top tool window can miss leave events when other top-level
        # windows are raised or when the mouse moves quickly across window edges.
        # Polling makes the collapsed 10px strip deterministic.
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(50)
        self.hover_timer.timeout.connect(self._sync_hover_state)
        self.hover_timer.start()

        self._force_collapsed()

    def screen_rect(self) -> QRect:
        screen = QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def expanded_geometry(self) -> QRect:
        rect = self.screen_rect()
        return QRect(rect.x(), rect.y(), self.EXPANDED_WIDTH, rect.height())

    def collapsed_geometry(self) -> QRect:
        rect = self.screen_rect()
        return QRect(rect.x(), rect.y(), self.COLLAPSED_WIDTH, rect.height())

    def edge_probe_geometry(self) -> QRect:
        rect = self.screen_rect()
        return QRect(rect.x(), rect.y(), self.COLLAPSED_WIDTH, rect.height())

    def _pin_width(self, width: int) -> None:
        # Qt layouts may otherwise keep a larger size hint from the expanded
        # button column. Pinning min/max width makes the hidden dock become a
        # true 10px strip instead of retaining the old expanded size hint.
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)

    def _apply_expanded_visuals(self) -> None:
        self.layout.setContentsMargins(10, 16, 10, 16)
        self.layout.setSpacing(8)
        for button in self.buttons:
            button.setVisible(True)

    def _apply_collapsed_visuals(self) -> None:
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        for button in self.buttons:
            button.setVisible(False)

    def _force_expanded(self) -> None:
        if self.expanded and self.geometry().width() == self.EXPANDED_WIDTH:
            return
        self.expanded = True
        self._apply_expanded_visuals()
        self._pin_width(self.EXPANDED_WIDTH)
        self.setGeometry(self.expanded_geometry())
        self.raise_()

    def _force_collapsed(self) -> None:
        if not self.expanded and self.geometry().width() == self.COLLAPSED_WIDTH:
            return
        self.expanded = False
        self._apply_collapsed_visuals()
        self._pin_width(self.COLLAPSED_WIDTH)
        self.setGeometry(self.collapsed_geometry())
        self.raise_()

    def position_collapsed(self) -> None:
        self._force_collapsed()

    def expand(self) -> None:
        self._force_expanded()

    def collapse(self) -> None:
        self._force_collapsed()

    def enterEvent(self, event) -> None:
        self.expand()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # Do not collapse immediately. The polling loop checks global cursor
        # position so the user can cross from the 10px strip into the expanded
        # dock without a flicker race.
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        self._force_collapsed()
        super().showEvent(event)

    def _sync_hover_state(self) -> None:
        cursor_pos = QCursor.pos()
        screen = self.screen_rect()
        if not screen.contains(cursor_pos):
            self._force_collapsed()
            return

        if self.expanded:
            # Keep open only while the cursor stays inside the actual expanded
            # dock. As soon as it moves into the app workspace, snap back to the
            # 10px controller strip.
            if self.expanded_geometry().contains(cursor_pos):
                if self.geometry() != self.expanded_geometry():
                    self.setGeometry(self.expanded_geometry())
                return
            self._force_collapsed()
            return

        if self.edge_probe_geometry().contains(cursor_pos):
            self._force_expanded()
        else:
            if self.geometry() != self.collapsed_geometry():
                self._force_collapsed()


class ShellController:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.processes: Dict[str, subprocess.Popen] = {}
        self.dock = EdgeDock(self)

    def start(self, open_default_app: bool = True) -> None:
        self.dock.show()
        self.dock.raise_()
        if open_default_app:
            self.open_app(APPS[0])

    def open_app(self, app: ShellApp) -> None:
        existing = self.processes.get(app.key)
        if existing is not None and existing.poll() is None:
            if not raise_process_window(existing.pid):
                print(f"{app.title} is already running as pid {existing.pid}.")
            return

        command = app_window_command(app, self.base_url)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
        process = subprocess.Popen(
            command,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
            creationflags=creationflags,
        )
        self.processes[app.key] = process

    def terminate_children(self) -> None:
        for process in list(self.processes.values()):
            if process.poll() is None:
                process.terminate()


def start_main_computer_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "main_computer.cli", "viewport", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_app_window(app_key: str, base_url: str) -> int:
    app = APP_BY_KEY[app_key]
    app_id = windows_app_id_for_app(app)
    set_windows_app_id(app_id)

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(f"Main Computer - {app.title}")
    qt_app.setOrganizationName("Main Computer")
    qt_app.setApplicationDisplayName(f"Main Computer - {app.title}")
    qt_app.setWindowIcon(make_icon(app.icon_text, app.icon_color))

    window = AppWindow(app, app_url(base_url, app))
    window.show()

    # Apply both immediately and shortly after show; Qt can recreate native HWNDs
    # around initial show/WebEngine startup on some Windows setups.
    relaunch_command = subprocess.list2cmdline(app_window_command(app, base_url))
    display_name = f"Main Computer - {app.title}"
    set_windows_window_app_id(
        window,
        app_id,
        relaunch_command=relaunch_command,
        display_name=display_name,
    )
    QTimer.singleShot(
        100,
        lambda: set_windows_window_app_id(
            window,
            app_id,
            relaunch_command=relaunch_command,
            display_name=display_name,
        ),
    )
    QTimer.singleShot(
        750,
        lambda: set_windows_window_app_id(
            window,
            app_id,
            relaunch_command=relaunch_command,
            display_name=display_name,
        ),
    )

    return qt_app.exec()


def run_shell(args: argparse.Namespace) -> int:
    set_windows_app_id("com.maincomputer.shell.dock")

    server_process: Optional[subprocess.Popen] = None
    base_url = args.base_url

    if args.start_server:
        server_process = start_main_computer_server(args.port)
        base_url = f"http://127.0.0.1:{args.port}"

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Main Computer Shell")
    qt_app.setWindowIcon(make_icon("MC", "#7fd190"))

    # Critical: the dock/shell must remain alive even after every app window is closed.
    qt_app.setQuitOnLastWindowClosed(False)

    shell = ShellController(base_url)
    shell.start(open_default_app=not args.no_default_app)

    try:
        return qt_app.exec()
    finally:
        shell.terminate_children()
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Main Computer operating shell prototype.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--start-server", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-default-app",
        action="store_true",
        help="Start only the left-edge dock without opening Spreadsheet automatically.",
    )
    parser.add_argument(
        "--app-window",
        choices=sorted(APP_BY_KEY),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.app_window:
        return run_app_window(args.app_window, args.base_url)

    return run_shell(args)


if __name__ == "__main__":
    raise SystemExit(main())
