"""Quicken Classic Windows adapter. QDF content is never read or modified.

Control IDs and Alt+F,E,Q were verified on Quicken 27.1.69.29. Fail closed on
unknown dialogs; never dismiss a dialog or repeat an import after uncertainty.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


def is_fullscreen(window):
    """Conservatively detect windows covering their monitor, including borderless apps."""
    import win32api
    import win32gui

    if not window or win32gui.IsIconic(window) or not win32gui.IsWindowVisible(window):
        return False
    if win32gui.GetClassName(window) in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
        return False
    monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(window, 2))["Monitor"]
    left, top, right, bottom = win32gui.GetWindowRect(window)
    return left <= monitor[0] and top <= monitor[1] and right >= monitor[2] and bottom >= monitor[3]


class DesktopUnavailable(RuntimeError):
    pass


def file_identity(path):
    # Identity is the configured canonical path, not mutable QDF file bytes.
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()


class WindowsQuicken:
    def __init__(self, data_file):
        if os.name != "nt":
            raise DesktopUnavailable("Quicken automation requires Windows")
        if not data_file or Path(data_file).suffix.lower() != ".qdf":
            raise DesktopUnavailable("Choose the intended Quicken .QDF data file")
        self.path = Path(data_file).resolve()
        self.identity = file_identity(self.path)
        self.guard = None
        self.main = None

    def ready(self, background=False):
        import psutil
        import win32api
        import win32gui
        import win32process
        from pywinauto import Desktop

        u = ctypes.WinDLL("user32", use_last_error=True)
        u.OpenInputDesktop.restype = ctypes.c_void_p
        u.CloseDesktop.argtypes = [ctypes.c_void_p]
        desk = u.OpenInputDesktop(0, False, 0x100)
        if not desk:
            raise DesktopUnavailable("Desktop is locked or unavailable")
        u.CloseDesktop(desk)
        windows = [
            w
            for w in Desktop(backend="win32").windows(class_name="QFRAME")
            if w.window_text().startswith("Quicken Classic")
        ]
        if len(windows) != 1:
            raise DesktopUnavailable("Open exactly one Quicken Classic window")
        self.main = windows[0]
        try:
            paths = {
                os.path.normcase(str(Path(f.path).resolve()))
                for f in psutil.Process(self.main.process_id()).open_files()
                if f.path.lower().endswith(".qdf")
            }
        except psutil.Error as exc:
            raise DesktopUnavailable(
                "Cannot inspect Quicken; run both apps at the same privilege level"
            ) from exc
        if paths != {os.path.normcase(str(self.path))}:
            raise DesktopUnavailable("Quicken has a different data file open; select the configured file")
        if not self.main.is_enabled() or self._dialogs():
            raise DesktopUnavailable("Close the open Quicken dialog before starting")
        if background:
            idle = (win32api.GetTickCount() - win32api.GetLastInputInfo()) & 0xFFFFFFFF
            foreground = win32gui.GetForegroundWindow()
            if (
                idle < 60000
                or not foreground
                or win32process.GetWindowThreadProcessId(foreground)[1] == self.main.process_id()
            ):
                raise DesktopUnavailable("Refresh deferred while the desktop or Quicken is in use")
            if is_fullscreen(foreground):
                raise DesktopUnavailable("Refresh deferred while the foreground application is fullscreen")
        return {
            "file_identity": self.identity,
            "file_name": self.path.name,
            "window": self.main.window_text(),
        }

    def _dialogs(self):
        from pywinauto import Desktop

        return [
            w
            for w in Desktop(backend="win32").windows(process=self.main.process_id())
            if w.handle != self.main.handle and w.class_name() == "#32770" and w.is_visible()
        ]

    def _check(self):
        import psutil
        import win32gui
        import win32process

        if self.guard and self.guard.interrupted:
            raise DesktopUnavailable("User activity detected; stopped. Review Quicken before resuming.")
        foreground = win32gui.GetForegroundWindow()
        if foreground and win32process.GetWindowThreadProcessId(foreground)[1] != self.main.process_id():
            raise DesktopUnavailable("Quicken lost focus; stopped before the next action")
        if (" - " + self.path.stem + " - ").casefold() not in self.main.window_text().casefold():
            raise DesktopUnavailable("The Quicken data file changed during automation")
        paths = {
            os.path.normcase(str(Path(f.path).resolve()))
            for f in psutil.Process(self.main.process_id()).open_files()
            if f.path.lower().endswith(".qdf")
        }
        if paths != {os.path.normcase(str(self.path))}:
            raise DesktopUnavailable("The open Quicken data file changed during automation")

    def _dialog(self, title, timeout=12):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self._check()
            dialogs = self._dialogs()
            matching = [d for d in dialogs if d.window_text() == title]
            if len(matching) == 1 and len(dialogs) == 1:
                return matching[0]
            if dialogs and not matching:
                raise DesktopUnavailable("Unexpected Quicken dialog: " + dialogs[0].window_text())
            time.sleep(0.1)
        raise DesktopUnavailable(f"Quicken did not open {title}")

    def _control(self, dialog, control_id, class_name=None):
        items = [
            c
            for c in dialog.descendants()
            if c.control_id() == control_id and (not class_name or c.class_name() == class_name)
        ]
        if len(items) != 1:
            raise DesktopUnavailable(f"Unsupported Quicken dialog layout (control {control_id})")
        return items[0]

    def _text(self, dialog, control_id, value):
        self._check()
        c = self._control(dialog, control_id, "Edit")
        c.set_edit_text(str(value))
        if c.window_text() != str(value):
            raise DesktopUnavailable("Quicken did not accept a dialog value")

    def _checked(self, dialog, control_id, desired):
        self._check()
        c = self._control(dialog, control_id, "Button")
        if bool(c.get_check_state()) != desired:
            c.click()
        if bool(c.get_check_state()) != desired:
            raise DesktopUnavailable("Quicken did not accept an export/import option")

    def _all_accounts(self, dialog):
        account = self._control(dialog, 2302, "QWComboBox")
        if account.window_text() != "<All accounts>":
            self._check()
            # QWComboBox ignores Home unless its list is open. Enter on the
            # closed control can submit the entire dialog with the old account.
            rect = account.rectangle()
            account.click_input(coords=(rect.width() - 10, rect.height() // 2))
            self._check()
            account.type_keys("{HOME}{ESC}", pause=0.15, set_foreground=False)
        if account.window_text() != "<All accounts>" or not dialog.is_visible():
            raise DesktopUnavailable("Cannot select all accounts for QIF routing")

    @contextmanager
    def session(self, background=False):
        import win32api
        import win32event
        import win32gui
        import win32process

        handle = win32event.CreateMutex(None, False, "Local\\Quicker-Quicken-Desktop")
        result = win32event.WaitForSingleObject(handle, 0)
        if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            win32api.CloseHandle(handle)
            raise DesktopUnavailable("Another Quicker operation is using Quicken")
        previous = None
        try:
            self.ready(background)
            if background:
                window = win32gui.GetForegroundWindow()
                if window:
                    previous = (window, win32process.GetWindowThreadProcessId(window))
            self.main.set_focus()
            with UserActivityGuard() as self.guard:
                yield self
        finally:
            try:
                if previous and not (self.guard and self.guard.interrupted):
                    self._restore_foreground(*previous)
            finally:
                self.guard = None
                win32event.ReleaseMutex(handle)
                win32api.CloseHandle(handle)

    def _restore_foreground(self, window, identity):
        import win32gui
        import win32process

        try:
            # Do not replace a window the user selected or reuse a destroyed HWND.
            current = win32gui.GetForegroundWindow()
            if (
                not current
                or win32process.GetWindowThreadProcessId(current)[1] != self.main.process_id()
                or not win32gui.IsWindow(window)
                or not win32gui.IsWindowVisible(window)
                or win32gui.IsIconic(window)
                or win32process.GetWindowThreadProcessId(window) != identity
            ):
                return
            win32gui.SetForegroundWindow(window)
            if win32gui.GetForegroundWindow() != window:
                logger.warning("Windows did not restore the application active before the automatic export")
        except Exception:
            logger.warning(
                "Could not restore the application active before the automatic export", exc_info=True
            )

    def export(self, destination):
        """Must run inside session(); returns only a new, completed export."""
        target = Path(destination).resolve()
        if target.exists() or target.suffix.lower() != ".qif":
            raise DesktopUnavailable("Export requires a new, unique .qif output path")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._check()
        self.main.type_keys("%feq", pause=0.15)
        dialog = self._dialog("QIF Export")
        self._text(dialog, 100, target)
        self._all_accounts(dialog)
        self._text(dialog, 110, "1/1/1901")
        self._text(dialog, 111, "12/31/2099")
        for cid in (102, 103, 104, 105):
            self._checked(dialog, cid, True)
        self._checked(dialog, 112, False)
        self._check()
        self._control(dialog, 32767).click_input()
        previous = None
        stable_since = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self._check()
            dialogs = self._dialogs()
            if dialogs and any(d.window_text() != "QIF Export" for d in dialogs):
                raise DesktopUnavailable("Export interrupted by an unexpected dialog")
            if target.exists() and not dialogs and self.main.is_enabled():
                stat = target.stat()
                stamp = (stat.st_size, stat.st_mtime_ns)
                if stat.st_size > 0 and stamp == previous:
                    if stable_since is not None and time.monotonic() - stable_since >= 1:
                        return target.read_bytes()
                else:
                    stable_since = time.monotonic()
                previous = stamp
            time.sleep(0.2)
        raise DesktopUnavailable("Export completion could not be verified")

    def enter(self, item, directory, before_submit):
        """Submit once. before_submit durably records uncertainty before the click.

        Returning only means the dialog closed; the server must verify an export.
        """
        from .qif import render_entry

        content = render_entry(item)
        self._import(content, item["id"], directory, before_submit, account_only=False)

    def create_account(self, request, directory, before_submit):
        from .qif import render_account

        self._import(render_account(request), request["id"], directory, before_submit, account_only=True)

    def _import(self, content, request_id, directory, before_submit, account_only):
        from uuid import uuid4

        # Preparation can be retried before an attempt; every preparation gets a new file.
        target = Path(directory).resolve() / (request_id + "-" + str(uuid4()) + ".qif")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        self._check()
        self.main.type_keys("%fiq", pause=0.15)
        dialog = self._dialog("QIF Import")
        self._text(dialog, 100, target)
        self._all_accounts(dialog)
        self._checked(dialog, 102, not account_only)
        self._checked(dialog, 104, account_only)
        for cid in (103, 105, 113, 114, 2304):
            self._checked(dialog, cid, False)
        self._check()
        before_submit()
        self._check()
        self._control(dialog, 32764).click_input()
        # Quicken briefly shows an untitled progress window while loading QIF.
        time.sleep(0.6)
        until = time.monotonic() + 30
        while time.monotonic() < until:
            self._check()
            dialogs = self._dialogs()
            if not dialogs and self.main.is_enabled():
                return
            if len(dialogs) == 1 and dialogs[0].window_text() == "QIF Import":
                success = [
                    c
                    for c in dialogs[0].descendants()
                    if c.control_id() == 1001 and c.window_text() == "QIF import successful."
                ]
                if success:
                    self._check()
                    self._control(dialogs[0], 32767).click_input()
                    time.sleep(0.2)
                    continue
            if any(d.window_text() != "QIF Import" for d in dialogs):
                raise DesktopUnavailable("Import outcome needs reconciliation; inspect the Quicken dialog")
            time.sleep(0.1)
        raise DesktopUnavailable("Import outcome is uncertain; refresh and reconcile before retrying")


class UserActivityGuard:
    """Observe physical input only while automating; never record keys or positions."""

    def __init__(self):
        self.interrupted = False

    def __enter__(self):
        import threading

        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        if not self.started.wait(5) or self.error:
            self.cancelled.set()
            raise DesktopUnavailable("Cannot monitor desktop takeover")
        return self

    def _listen(self):
        from ctypes import wintypes as w

        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, w.HINSTANCE, w.DWORD]
        u.SetWindowsHookExW.restype = w.HANDLE
        u.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.UnhookWindowsHookEx.argtypes = [w.HANDLE]
        self.tid = k.GetCurrentThreadId()

        def keyboard(code, wp, lp):
            if code >= 0:
                flags = ctypes.cast(lp, ctypes.POINTER(w.DWORD))[2]
                if not flags & 0x10:
                    self.interrupted = True
            return u.CallNextHookEx(None, code, wp, lp)

        def mouse(code, wp, lp):
            if code >= 0:
                flags = ctypes.cast(lp, ctypes.POINTER(w.DWORD))[3]
                if not flags & 1:
                    self.interrupted = True
            return u.CallNextHookEx(None, code, wp, lp)

        callbacks = [callback_type(keyboard), callback_type(mouse)]
        hooks = [u.SetWindowsHookExW(13 + i, cb, None, 0) for i, cb in enumerate(callbacks)]
        if not all(hooks):
            self.error = True
        self.started.set()
        msg = w.MSG()
        try:
            if not self.error and not self.cancelled.is_set():
                while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hook in hooks:
                if hook:
                    u.UnhookWindowsHookEx(hook)

    def __exit__(self, *_):
        ctypes.windll.user32.PostThreadMessageW(self.tid, 0x12, 0, 0)
        self.thread.join(3)
