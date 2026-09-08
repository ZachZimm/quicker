import json
import os
import sys
import threading
from pathlib import Path

import httpx


def state_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config")) / "Quicker"


def main():
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import (
        QApplication,
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

    from .sync import Companion

    class Events(QObject):
        message = Signal(str)
        paired = Signal(dict)

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Quicker companion")
            self.resize(680, 550)
            self.engine = None
            self.thread = None
            self.closing = False
            self.events = Events()
            self.events.message.connect(self.log_message)
            self.events.paired.connect(self.finish_pair)
            self.state_dir = state_directory()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.config_path = self.state_dir / "config.json"
            self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
            root = QWidget()
            layout = QVBoxLayout(root)
            title = QLabel("<h1>Quicker companion</h1>")
            layout.addWidget(title)
            layout.addWidget(QLabel("Upload your input folder and archive every document from the server."))
            form = QFormLayout()
            self.server = QLineEdit(self.config.get("server", "http://localhost:8765"))
            self.code = QLineEdit()
            self.code.setEchoMode(QLineEdit.Password)
            self.input = QLineEdit(self.config.get("input", str(Path.home() / "Documents" / "Quicker Input")))
            self.archive = QLineEdit(
                self.config.get("archive", str(Path.home() / "Documents" / "Quicker Archive"))
            )
            form.addRow("Server address", self.server)
            form.addRow("Pairing code", self.code)
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
            layout.addLayout(form)
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
            self.entry.setToolTip("Quicken interaction is not implemented in this version")
            layout.addWidget(self.entry)
            layout.addWidget(
                QLabel("Quicken entry is not available yet. Approval and archives remain saved.")
            )
            self.log = QTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log)
            self.setCentralWidget(root)
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_DirIcon), self)
            menu = QMenu()
            show = menu.addAction("Open Quicker")
            show.triggered.connect(self.showNormal)
            quit_action = menu.addAction("Quit")
            quit_action.triggered.connect(self.quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda _: self.showNormal())
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.show()
            if self.config.get("token"):
                QTimer.singleShot(0, self.connect)

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

        def log_message(self, message):
            # Plain text prevents filenames or server messages from becoming rich text.
            self.log.append(message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        def save(self):
            self.config.update(
                server=self.server.text().strip().rstrip("/"),
                input=self.input.text(),
                archive=self.archive.text(),
                qif_path=self.qif.text().strip(),
            )
            temp = self.config_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.config, indent=2))
            if os.name != "nt":
                temp.chmod(0o600)
            os.replace(temp, self.config_path)

        def pair(self):
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

            threading.Thread(target=work, daemon=True).start()

        def finish_pair(self, result):
            self.pair_button.setEnabled(True)
            if result.get("error"):
                QMessageBox.warning(self, "Pairing failed", result["error"])
            else:
                self.config.update(result)
                self.code.clear()
                self.save()
                self.log_message("Paired successfully. Click Save & connect to start archive sync.")

        def connect(self):
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
                self.engine = Companion(
                    self.config["server"],
                    self.config["token"],
                    self.config["input"],
                    self.config["archive"],
                    self.state_dir,
                    self.events.message.emit,
                    qif_path=self.config.get("qif_path"),
                )
                self.thread = threading.Thread(target=self.engine.run, daemon=True)
                self.thread.start()
                self.log_message("Connecting and synchronizing archives…")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not connect", str(exc))

        def disconnect(self):
            if self.engine:
                self.engine.stop.set()
                self.log_message("Stopping sync. Incomplete transfers will resume on reconnect.")

        def quit(self):
            self.closing = True
            self.disconnect()
            QApplication.quit()

        def closeEvent(self, event):
            if self.tray.isVisible() and not self.closing:
                self.hide()
                event.ignore()
            else:
                self.disconnect()
                event.accept()

    app = QApplication(sys.argv)
    app.setApplicationName("Quicker")
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
