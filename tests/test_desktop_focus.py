import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quicker_client import desktop


@pytest.fixture
def windows(monkeypatch):
    state = SimpleNamespace(foreground=20)
    gui = SimpleNamespace(
        GetForegroundWindow=lambda: state.foreground,
        SetForegroundWindow=Mock(side_effect=lambda window: setattr(state, "foreground", window)),
        IsWindow=Mock(return_value=True),
        IsWindowVisible=Mock(return_value=True),
        IsIconic=Mock(return_value=False),
        GetClassName=Mock(return_value="VideoPlayer"),
        GetWindowRect=Mock(return_value=(0, 0, 1920, 1080)),
    )
    api = SimpleNamespace(
        GetMonitorInfo=Mock(return_value={"Monitor": (0, 0, 1920, 1080)}),
        MonitorFromWindow=Mock(return_value=1),
        GetTickCount=lambda: 120000,
        GetLastInputInfo=lambda: 0,
        CloseHandle=Mock(),
    )
    event = SimpleNamespace(
        CreateMutex=Mock(return_value=99),
        WaitForSingleObject=Mock(return_value=0),
        WAIT_OBJECT_0=0,
        WAIT_ABANDONED=128,
        ReleaseMutex=Mock(),
    )
    process = SimpleNamespace(GetWindowThreadProcessId=Mock(side_effect=lambda window: (window, window)))
    for name, module in {
        "win32gui": gui,
        "win32api": api,
        "win32event": event,
        "win32process": process,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    adapter = object.__new__(desktop.WindowsQuicken)
    adapter.main = SimpleNamespace(
        process_id=lambda: 10,
        set_focus=lambda: setattr(state, "foreground", 10),
    )
    adapter.guard = None
    adapter.ready = Mock()
    guard = SimpleNamespace(interrupted=False)
    monkeypatch.setattr(desktop, "UserActivityGuard", lambda: nullcontext(guard))
    return SimpleNamespace(state=state, gui=gui, api=api, event=event, adapter=adapter, guard=guard)


@pytest.mark.parametrize(
    "bounds,monitor,expected",
    [
        ((0, 0, 1920, 1080), (0, 0, 1920, 1080), True),
        ((-1920, 0, 0, 1080), (-1920, 0, 0, 1080), True),
        ((-8, -8, 1928, 1048), (0, 0, 1920, 1080), False),
        ((100, 100, 900, 700), (0, 0, 1920, 1080), False),
        ((-1920, 0, 1920, 1080), (-1920, 0, 0, 1080), True),
    ],
)
def test_fullscreen_uses_window_monitor(windows, bounds, monitor, expected):
    windows.gui.GetWindowRect.return_value = bounds
    windows.api.GetMonitorInfo.return_value = {"Monitor": monitor}
    assert desktop.is_fullscreen(20) is expected
    windows.api.MonitorFromWindow.assert_called_once_with(20, 2)


@pytest.mark.parametrize("shell_class", ["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"])
def test_desktop_shell_does_not_block_exports(windows, shell_class):
    windows.gui.GetClassName.return_value = shell_class
    assert not desktop.is_fullscreen(20)


@pytest.mark.parametrize("failure", [False, True])
def test_background_session_restores_focus_and_releases_mutex(windows, failure):
    def run():
        with windows.adapter.session(background=True):
            assert windows.state.foreground == 10
            if failure:
                raise RuntimeError("export failed")

    if failure:
        with pytest.raises(RuntimeError, match="export failed"):
            run()
    else:
        run()
    assert windows.state.foreground == 20
    windows.event.ReleaseMutex.assert_called_once_with(99)
    windows.api.CloseHandle.assert_called_once_with(99)


@pytest.mark.parametrize("change", ["switch", "input", "closed", "reused", "minimized"])
def test_background_session_respects_user_and_window_changes(windows, change):
    with windows.adapter.session(background=True):
        if change == "switch":
            windows.state.foreground = 30
        elif change == "input":
            windows.guard.interrupted = True
        elif change == "closed":
            windows.gui.IsWindow.return_value = False
        elif change == "minimized":
            windows.gui.IsIconic.return_value = True
        else:
            sys.modules["win32process"].GetWindowThreadProcessId.side_effect = lambda window: (999, window)
    windows.gui.SetForegroundWindow.assert_not_called()


def test_focus_restoration_failure_preserves_export_error_and_releases_mutex(windows, caplog):
    windows.gui.SetForegroundWindow.side_effect = RuntimeError("focus denied")
    with pytest.raises(ValueError, match="original error"), windows.adapter.session(background=True):
        raise ValueError("original error")
    assert "Could not restore" in caplog.text
    windows.event.ReleaseMutex.assert_called_once()
    windows.api.CloseHandle.assert_called_once()


def test_manual_session_keeps_quicken_foreground(windows):
    with windows.adapter.session():
        pass
    assert windows.state.foreground == 10
    windows.gui.SetForegroundWindow.assert_not_called()


def test_focus_restoration_waits_for_async_activation(windows, monkeypatch, caplog):
    windows.state.foreground = 10
    windows.gui.SetForegroundWindow.side_effect = None
    monkeypatch.setattr(desktop.time, "sleep", lambda _: setattr(windows.state, "foreground", 20))
    windows.adapter._restore_foreground(20, (20, 20))
    assert windows.state.foreground == 20
    windows.gui.SetForegroundWindow.assert_called_once_with(20)
    assert not caplog.records


def test_focus_restoration_does_not_retry_after_async_app_switch(windows, monkeypatch, caplog):
    windows.state.foreground = 10
    windows.gui.SetForegroundWindow.side_effect = None
    monkeypatch.setattr(desktop.time, "sleep", lambda _: setattr(windows.state, "foreground", 30))
    windows.adapter._restore_foreground(20, (20, 20))
    assert windows.state.foreground == 30
    windows.gui.SetForegroundWindow.assert_called_once_with(20)
    assert not caplog.records


def test_deferred_session_never_takes_focus(windows):
    windows.adapter.ready.side_effect = desktop.DesktopUnavailable("fullscreen")
    with (
        pytest.raises(desktop.DesktopUnavailable, match="fullscreen"),
        windows.adapter.session(background=True),
    ):
        pytest.fail("Deferred exports must not start")
    assert windows.state.foreground == 20
    windows.gui.SetForegroundWindow.assert_not_called()
    windows.event.ReleaseMutex.assert_called_once()


def test_background_readiness_defers_fullscreen_but_manual_still_works(windows, monkeypatch):
    adapter = windows.adapter
    adapter.path = Path("example.qdf").resolve()
    adapter.identity = "test"
    adapter.main.window_text = lambda: "Quicken Classic"
    adapter.main.is_enabled = lambda: True
    adapter._dialogs = list
    monkeypatch.setitem(
        sys.modules,
        "pywinauto",
        SimpleNamespace(
            Desktop=lambda **kwargs: SimpleNamespace(windows=lambda **kwargs: [adapter.main]),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: SimpleNamespace(open_files=lambda: [SimpleNamespace(path=str(adapter.path))]),
            Error=RuntimeError,
        ),
    )
    monkeypatch.setattr(
        desktop.ctypes,
        "WinDLL",
        lambda *args, **kwargs: SimpleNamespace(
            OpenInputDesktop=Mock(return_value=1),
            CloseDesktop=Mock(),
        ),
        raising=False,
    )
    with pytest.raises(desktop.DesktopUnavailable, match="fullscreen"):
        desktop.WindowsQuicken.ready(adapter, background=True)
    assert desktop.WindowsQuicken.ready(adapter)["file_identity"] == "test"
    windows.gui.GetWindowRect.return_value = (100, 100, 900, 700)
    assert desktop.WindowsQuicken.ready(adapter, background=True)["file_identity"] == "test"
