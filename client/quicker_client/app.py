import hashlib
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PySide6.QtCore import QLockFile, QObject, QSignalBlocker, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import ConfigurationStore, migrate_legacy_state
from .lifecycle import configure_logging, set_startup
from .sync import Companion


def state_directory():
    if os.environ.get("QUICKER_STATE_DIR"):
        return Path(os.environ["QUICKER_STATE_DIR"]).resolve()
    # AppData can be virtualized for child processes of packaged desktop hosts.
    # A profile-level directory is shared with ordinary Explorer/sign-in launches.
    return (Path.home() / ".quicker").resolve()


def legacy_state_directories():
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config"))
    candidates = [local / "Quicker"]
    # Recover installations first configured by the Codex desktop host, whose
    # child-process AppData writes can land in its private MSIX cache.
    candidates.extend((local / "Packages").glob("OpenAI.Codex_*/LocalCache/Local/Quicker"))
    return candidates


class Events(QObject):
    message = Signal(str)
    paired = Signal(dict)
    status = Signal(dict)


class Window(QMainWindow):
    def __init__(self, directory=None, auto_connect=True, companion_factory=Companion):
        super().__init__()
        self.setWindowTitle("Quicker companion")
        self.resize(680, 700)
        self.engine = None
        self.thread = None
        self.closing = False
        self.desired = False
        self.retry_at = None
        self.restarts = 0
        self.started_at = 0
        self.shutdown_deadline = None
        self.pair_thread = None
        self.companion_factory = companion_factory
        self.last_message = None
        self.notified = None
        self.current_status = {}
        self.events = Events()
        self.events.message.connect(self.log_message)
        self.events.paired.connect(self.finish_pair)
        self.events.status.connect(self.connection_status)
        self.state_dir = Path(directory) if directory is not None else state_directory()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "config.json"
        self.settings = ConfigurationStore(self.state_dir)
        self.config_error = False
        try:
            self.config = self.settings.load()
        except (OSError, ValueError):
            self.config = {}
            self.config_error = True
        self.logger = configure_logging(self.state_dir, lambda: [self.config.get("token"), self.code.text()])
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("<h1>Quicker companion</h1>")
        layout.addWidget(title)
        layout.addWidget(QLabel("Upload your input folder and archive every document from the server."))
        form = QFormLayout()
        self.server = QLineEdit(self.config.get("server", "http://localhost:8999"))
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.Password)
        self.code.setPlaceholderText("Only needed for first-time pairing or re-pairing")
        self.pairing_status = QLabel()
        self.pairing_status.setWordWrap(True)
        self.update_pairing_status()
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or str(
            Path.home() / "Desktop"
        )
        self.input = QLineEdit(self.config.get("input", str(Path(desktop) / "Quicker Input")))
        self.archive = QLineEdit(
            self.config.get("archive", str(Path.home() / "Documents" / "Quicker Archive"))
        )
        form.addRow("Server address", self.server)
        form.addRow("Pairing", self.pairing_status)
        form.addRow("New pairing code", self.code)
        for label, field in (("Input folder", self.input), ("Archive folder", self.archive)):
            row = QHBoxLayout()
            row.addWidget(field)
            button = QPushButton("Browse…")
            button.clicked.connect(lambda _, field=field: self.browse(field))
            row.addWidget(button)
            form.addRow(label, row)
        self.qif = QLineEdit(self.config.get("qif_path", ""))
        qif_row = QHBoxLayout()
        qif_row.addWidget(self.qif)
        qif_browse = QPushButton("Browse…")
        qif_browse.clicked.connect(self.browse_qif)
        qif_row.addWidget(qif_browse)
        form.addRow("Quicken QIF export", qif_row)
        self.data_file = QLineEdit(self.config.get("data_file", ""))
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_file)
        data_browse = QPushButton("Browse…")
        data_browse.clicked.connect(self.browse_data)
        data_row.addWidget(data_browse)
        form.addRow("Quicken data file", data_row)
        layout.addLayout(form)
        self.startup = QCheckBox("Start with Windows")
        self.startup.setChecked(self.config.get("start_with_windows", os.name == "nt"))
        self.minimized = QCheckBox("Start minimized to tray")
        self.minimized.setChecked(self.config.get("start_minimized", True))
        layout.addWidget(self.startup)
        layout.addWidget(self.minimized)
        self.startup.toggled.connect(self.save_preferences)
        self.minimized.toggled.connect(self.save_preferences)
        layout.addWidget(
            QLabel(
                "Optional: export all Quicken accounts and dates to this file. Changes sync automatically."
            )
        )
        buttons = QHBoxLayout()
        self.pair_button = QPushButton("Pair using code")
        self.pair_button.clicked.connect(self.pair)
        buttons.addWidget(self.pair_button)
        self.connect_button = QPushButton("Save & connect")
        self.connect_button.clicked.connect(self.connect)
        buttons.addWidget(self.connect_button)
        self.stop_button = QPushButton("Disconnect")
        self.stop_button.clicked.connect(self.disconnect)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.entry = QPushButton("Enter approved transactions")
        self.entry.setEnabled(False)
        self.entry.clicked.connect(lambda: self.command("entry"))
        layout.addWidget(self.entry)
        self.refresh = QPushButton("Refresh from Quicken / reconcile")
        self.refresh.setEnabled(False)
        self.refresh.clicked.connect(lambda: self.command("refresh"))
        layout.addWidget(self.refresh)
        layout.addWidget(
            QLabel(
                "Open the selected data file in Quicken. Entry verifies fresh exports before and after.\n"
                "Keep the desktop idle during automation. Physical input stops the operation."
            )
        )
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(500)
        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log)
        self.setCentralWidget(root)
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_DirIcon), self)
        self.menu = QMenu(self)
        self.menu.setTitle("Quicker")
        self.menuBar().addMenu(self.menu)
        self.tray_status = self.menu.addAction("Not connected")
        self.tray_status.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Open Quicker settings and activity", self.show_settings)
        self.menu.addAction("Reload saved settings", self.reload_settings)
        self.menu.addAction("Open web interface", self.open_web)
        self.refresh_action = self.menu.addAction(
            "Refresh from Quicken / reconcile", lambda: self.command("refresh")
        )
        self.refresh_action.setEnabled(False)
        self.pause_action = self.menu.addAction("Pause automatic exports")
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(self.config.get("auto_exports_paused", False))
        self.pause_action.toggled.connect(self.pause_exports)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.messageClicked.connect(self.open_attention)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self.poll_workers)
        self.monitor.start(250)
        if self.config_error:
            self.log_message(
                "Saved settings could not be read. Files are preserved. Use Quicker > Reload saved settings; do not re-pair."
            )
        else:
            self.logger.info(
                "Loaded saved settings from %s (paired=%s, recovered=%s)",
                self.settings.path,
                bool(self.config.get("token")),
                self.settings.recovered,
            )
            if self.settings.recovered:
                self.log_message("Recovered saved settings and pairing from the local backup.")
        if auto_connect and self.config.get("token"):
            QTimer.singleShot(0, self.connect)

    def show_initial(self):
        if not self.config.get("token") or not self.minimized.isChecked() or not self.tray.isVisible():
            self.show_settings()

    def update_pairing_status(self):
        if self.config_error:
            text = "Saved settings unavailable. Reload them; no new pairing code is needed."
        elif self.config.get("token"):
            text = "Paired — saved device credential will be reused automatically."
        else:
            text = "Not paired. Enter a new code from the server to set up this device."
        self.pairing_status.setText(text)

    def reload_settings(self):
        if self.closing or (self.thread and self.thread.is_alive()):
            self.log_message("Disconnect and wait for sync to stop before reloading settings.")
            return
        try:
            config = self.settings.load()
        except (OSError, ValueError):
            self.log_message(
                "Saved settings and recovery copy are unavailable. Existing files were preserved."
            )
            return
        self.config, self.config_error = config, False
        for field, key, fallback in (
            (self.server, "server", "http://localhost:8999"),
            (self.input, "input", self.input.text()),
            (self.archive, "archive", self.archive.text()),
            (self.qif, "qif_path", ""),
            (self.data_file, "data_file", ""),
        ):
            field.setText(config.get(key, fallback))
        for field, key, fallback in (
            (self.startup, "start_with_windows", os.name == "nt"),
            (self.minimized, "start_minimized", True),
            (self.pause_action, "auto_exports_paused", False),
        ):
            with QSignalBlocker(field):
                field.setChecked(config.get(key, fallback))
        self.update_pairing_status()
        self.log_message("Saved settings reloaded.")
        if config.get("token"):
            self.connect()

    def show_settings(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_settings()

    def open_web(self):
        url = QUrl(self.config.get("server", self.server.text()).strip())
        if url.scheme() in ("http", "https") and url.host():
            QDesktopServices.openUrl(url)

    def open_attention(self):
        if (self.current_status.get("attention") or {}).get("key") in ("pairing", "worker"):
            self.show_settings()
        else:
            self.open_web()

    def write_config(self):
        if self.config_error:
            raise ValueError(
                "Repair the unreadable config.json before saving; existing pairing has been retained"
            )
        self.settings.save(self.config)

    def save_preferences(self):
        try:
            if self.config_error:
                raise ValueError("Reload saved settings first")
            set_startup(self.startup.isChecked())
            self.config.update(
                start_with_windows=self.startup.isChecked(), start_minimized=self.minimized.isChecked()
            )
            self.write_config()
        except (OSError, ValueError):
            self.log_message("Could not save startup preferences. Check settings and folder permissions.")
            self.show_settings()

    def pause_exports(self, paused):
        self.config["auto_exports_paused"] = paused
        try:
            self.write_config()
        except (OSError, ValueError):
            self.log_message("Could not save the pause preference; it may reset on restart.")
        if self.engine:
            self.engine.pause_exports(paused)
        self.connection_status({**self.current_status, "paused": paused})

    def browse(self, field):
        selected = QFileDialog.getExistingDirectory(self, "Choose folder", field.text())
        if selected:
            field.setText(selected)

    def browse_qif(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose Quicken export", self.qif.text(), "Quicken export (*.qif *.QIF)"
        )
        if selected:
            self.qif.setText(selected)

    def browse_data(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose Quicken data file", self.data_file.text(), "Quicken data (*.QDF *.qdf)"
        )
        if selected:
            self.data_file.setText(selected)

    def connection_status(self, status):
        if self.closing:
            return
        self.current_status = status
        ready = status.get("connected", False) and status.get("desktop", False) and self.desired
        ready = ready and not (self.engine and self.engine.stop.is_set())
        self.entry.setEnabled(ready)
        self.refresh.setEnabled(ready)
        self.refresh_action.setEnabled(ready)
        connection = "Connected" if status.get("connected") else "Not connected"
        waiting = (
            (status.get("readiness") if status.get("connected") else "")
            or status.get("waiting")
            or ("Quicken automation available" if ready else "Waiting for connection")
        )
        last = status.get("last_export")
        exported = (
            datetime.fromtimestamp(last, UTC).astimezone().strftime("%b %d %H:%M") if last else "none yet"
        )
        text = f"{connection} · {waiting}\nLast native export: {exported}"
        if status.get("paused"):
            text += " · Automatic exports paused"
        if text != self.status_label.text():
            self.status_label.setText(text)
            self.tray_status.setText(text.replace("\n", " — "))
            self.tray.setToolTip(("Quicker · " + text)[:127])
            self.log_message(text.replace("\n", " · "))
        attention = status.get("attention")
        key = attention.get("key") if attention else None
        if key and key != self.notified:
            self.log_message(attention["message"])
            if self.tray.isVisible():
                self.tray.showMessage(
                    "Quicker needs attention", attention["message"], QSystemTrayIcon.Warning
                )
        self.notified = key

    def command(self, kind):
        if not self.closing and self.engine and not self.engine.stop.is_set():
            self.engine.command(kind)
            self.log_message(
                "Requested "
                + ("approved entry" if kind == "entry" else "fresh export and reconciliation")
                + ". Keep Quicken open and the desktop idle."
            )

    def log_message(self, message):
        for secret in (self.config.get("token"), self.code.text()):
            if secret:
                message = message.replace(secret, "[redacted]")
        if message == self.last_message:
            return
        self.last_message = message
        self.logger.info(message)
        # Plain text prevents filenames or server messages from becoming rich text.
        self.log.append(message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def save(self):
        self.config.update(
            server=self.server.text().strip().rstrip("/"),
            input=self.input.text(),
            archive=self.archive.text(),
            qif_path=self.qif.text().strip(),
            data_file=self.data_file.text().strip(),
        )
        self.config.update(
            start_with_windows=self.startup.isChecked(), start_minimized=self.minimized.isChecked()
        )
        self.write_config()
        try:
            set_startup(self.startup.isChecked())
        except OSError:
            self.log_message(
                "Settings saved. Windows startup registration could not be updated; connection is still available."
            )

    def pair(self):
        if self.config_error:
            self.log_message(
                "Reload the saved settings before pairing. Existing credentials have been preserved."
            )
            return
        if self.closing or (self.thread and self.thread.is_alive()):
            self.log_message("Disconnect and wait for sync to stop before pairing.")
            return
        self.pair_button.setEnabled(False)
        server, code = self.server.text().strip().rstrip("/"), self.code.text().strip()

        def work():
            try:
                response = httpx.post(
                    server + "/api/pair",
                    json={"code": code, "name": os.environ.get("COMPUTERNAME", "Windows companion")},
                    timeout=20,
                )
                response.raise_for_status()
                self.events.paired.emit(response.json())
            except (httpx.HTTPError, ValueError):
                self.events.paired.emit(
                    {
                        "error": "Pairing failed. Check the address and code; revoke an old device in the browser before replacing it."
                    }
                )

        self.pair_thread = threading.Thread(target=work, daemon=True)
        self.pair_thread.start()

    def finish_pair(self, result):
        if self.closing:
            return
        self.pair_button.setEnabled(True)
        if result.get("error"):
            QMessageBox.warning(self, "Pairing failed", result["error"])
        else:
            self.config.update(result)
            self.code.clear()
            self.update_pairing_status()
            try:
                self.save()
            except (OSError, ValueError):
                self.log_message(
                    "Pairing received but settings could not be saved. Keep this window open and check folder permissions."
                )
                return
            self.log_message("Paired successfully. Click Save & connect to start archive sync.")

    def connect(self, checked=False, recovery=False):
        if self.closing or self.config_error:
            return
        if self.thread and self.thread.is_alive():
            self.log_message("Disconnect and wait for sync to stop before reconnecting.")
            return
        if not self.config.get("token"):
            QMessageBox.information(
                self, "Pair first", "Create a pairing code in the browser's Windows companion tab."
            )
            return
        try:
            self.save()
            self.engine = self.companion_factory(
                self.config["server"],
                self.config["token"],
                self.config["input"],
                self.config["archive"],
                self.state_dir,
                self.events.message.emit,
                qif_path=self.config.get("qif_path"),
                data_file=self.config.get("data_file"),
                status_report=self.events.status.emit,
                auto_exports_paused=self.pause_action.isChecked(),
            )
            self.desired = True
            self.retry_at = None
            if not recovery:
                self.restarts = 0
            engine = self.engine

            def run():
                try:
                    engine.run()
                except Exception as exc:  # noqa: BLE001 - supervise unexpected worker exits
                    self.events.message.emit("Sync worker stopped unexpectedly (" + type(exc).__name__ + ").")

            self.thread = threading.Thread(target=run, daemon=True)
            self.started_at = time.monotonic()
            self.thread.start()
            self.log_message("Connecting and synchronizing archives…")
        except (OSError, ValueError) as exc:
            self.desired = False
            self.log_message("Could not connect using saved settings: " + str(exc))
            self.show_settings()

    def disconnect(self):
        self.desired = False
        self.retry_at = None
        if self.engine:
            self.engine.stop.set()
            self.entry.setEnabled(False)
            self.refresh.setEnabled(False)
            self.refresh_action.setEnabled(False)
            self.log_message("Stopping sync. Incomplete transfers will resume on reconnect.")

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.disconnect()
        self.shutdown_deadline = time.monotonic() + 15
        self.centralWidget().setEnabled(False)
        self.menu.setEnabled(False)
        self.status_label.setText("Stopping safely; unfinished operations will reconcile on restart…")
        self.poll_workers()

    def poll_workers(self):
        alive = self.thread and self.thread.is_alive()
        pairing = self.pair_thread and self.pair_thread.is_alive()
        if self.closing:
            if not (alive or pairing) or time.monotonic() >= self.shutdown_deadline:
                if alive or pairing:
                    self.log_message(
                        "Shutdown deadline reached. Durable receipts and uncertain attempts retained for recovery."
                    )
                self.monitor.stop()
                self.tray.hide()
                QApplication.quit()
            return
        self.connect_button.setEnabled(not alive and not pairing and not self.config_error)
        self.pair_button.setEnabled(not alive and not pairing and not self.config_error)
        if alive:
            if time.monotonic() - self.started_at > 600:
                self.restarts = 0
            return
        if not self.desired:
            if self.engine:
                self.connection_status(
                    {**self.current_status, "connected": False, "desktop": False, "waiting": "Disconnected"}
                )
            return
        if self.engine and self.engine.fatal_reason == "pairing":
            self.desired = False
            self.connection_status(
                {
                    **self.current_status,
                    "connected": False,
                    "desktop": False,
                    "waiting": "Pairing required",
                    "attention": {"key": "pairing", "message": "Open settings to pair this companion again."},
                }
            )
            return
        if self.retry_at is None:
            self.connection_status(
                {
                    **self.current_status,
                    "connected": False,
                    "desktop": False,
                    "waiting": "Sync worker stopped; recovering"
                    if self.restarts < 3
                    else "Sync stopped; open settings",
                    "attention": {
                        "key": "worker",
                        "message": "Sync worker stopped. Open settings and activity for details.",
                    },
                }
            )
            if self.restarts >= 3:
                self.desired = False
                return
            self.retry_at = time.monotonic() + (5, 15, 60)[self.restarts]
            self.restarts += 1
        elif time.monotonic() >= self.retry_at:
            self.connect(recovery=True)

    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return
        if self.tray.isVisible() and not self.closing:
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit()


def instance_name(directory):
    return "Quicker-" + hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()[:24]


def activate_existing(name):
    socket = QLocalSocket()
    deadline = time.monotonic() + 2
    while True:
        socket.connectToServer(name)
        if socket.waitForConnected(100):
            break
        if time.monotonic() >= deadline:
            return False
        socket.abort()
        time.sleep(0.05)
    socket.write(b"show\n")
    socket.flush()
    sent = socket.waitForBytesWritten(1000) or socket.bytesToWrite() == 0
    socket.disconnectFromServer()
    return sent


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Quicker")
    app.setQuitOnLastWindowClosed(False)
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "companion.lock"))
    name = instance_name(directory)
    if not lock.tryLock(0):
        if not activate_existing(name):
            QMessageBox.information(
                None, "Quicker is running", "Open the existing companion from the system tray."
            )
        return
    if not os.environ.get("QUICKER_STATE_DIR"):
        try:
            migrate_legacy_state(directory, legacy_state_directories(), QLockFile)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(None, "Saved settings need attention", str(exc))
            lock.unlock()
            return
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    QLocalServer.removeServer(name)  # Only the lock owner can remove a stale endpoint.
    if not server.listen(name):
        QMessageBox.warning(
            None, "Could not start", "Could not open local companion communication. Try again."
        )
        return
    window = Window()

    def incoming():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            buffer = bytearray()

            def receive(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                if len(buffer) < 5:
                    return
                if buffer == b"show\n" and not window.closing:
                    window.show_settings()
                socket.disconnectFromServer()

            socket.readyRead.connect(receive)
            socket.disconnected.connect(socket.deleteLater)
            QTimer.singleShot(2000, socket, socket.disconnectFromServer)
            if socket.bytesAvailable():
                receive()

    server.newConnection.connect(incoming)
    app.commitDataRequest.connect(lambda _: window.quit())
    app.aboutToQuit.connect(window.disconnect)
    window.show_initial()
    try:
        return app.exec()
    finally:
        server.close()
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
