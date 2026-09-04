from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from PySide6.QtWidgets import QApplication, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PySide6 is required for rdklink-gui: py -3 -m pip install 'rdklink[gui]'") from exc

from .service_client import ServiceClient


def read_log_tail(path: Path, max_bytes: int = 20_000) -> str:
    if not path.exists():
        return "No activity log yet"
    with path.open("rb") as source:
        source.seek(0, 2)
        source.seek(max(0, source.tell() - max_bytes))
        return source.read(max_bytes).decode("utf-8", errors="replace")


class Page(QWidget):
    def __init__(self, title: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title))
        self.output = QTextEdit(); self.output.setReadOnly(True); layout.addWidget(self.output)

    def show_result(self, value: object) -> None:
        self.output.setPlainText(json.dumps(value, ensure_ascii=False, indent=2, default=str))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("RDKLink"); self.resize(980, 640); self.service = ServiceClient(); self.pages: dict[str, Page] = {}
        tabs = QTabWidget(); self.setCentralWidget(tabs)
        for name in ["Devices", "Projects", "Serial", "Processes", "Activity", "Settings"]:
            page = Page(name); tabs.addTab(page, name); self.pages[name] = page
        self._build_devices(); self._build_projects(); self._build_serial(); self._build_processes(); self._build_activity(); self._build_settings(); self.refresh_devices()

    def button(self, page: Page, label: str, callback) -> None:
        button = QPushButton(label); button.clicked.connect(callback); page.layout().insertWidget(1, button)

    def _build_devices(self) -> None:
        self.button(self.pages["Devices"], "Refresh", self.refresh_devices)

    def _build_projects(self) -> None:
        page = self.pages["Projects"]; row = QFormLayout(); name = QLineEdit(); local = QLineEdit(); remote = QLineEdit("project"); command = QLineEdit("python3 main.py")
        for label, field in [("Name", name), ("Local path", local), ("Remote path", remote), ("Run command", command)]: row.addRow(label, field)
        page.layout().insertLayout(1, row); actions = QHBoxLayout()
        for label, fn in [("List", lambda: page.show_result(self.service.call("project_list"))), ("Add", lambda: page.show_result(self.service.call("project_add", {"name": name.text(), "local_path": local.text(), "remote_path": remote.text(), "run_command": command.text()}))), ("Push", lambda: page.show_result(self.service.call("project_push", {"local_path": local.text(), "remote_path": remote.text()}))), ("Run", lambda: page.show_result(self.service.call("project_run", {"command": command.text(), "cwd": remote.text()})))]:
            b = QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
        page.layout().insertLayout(2, actions)

    def _build_serial(self) -> None:
        page = self.pages["Serial"]; port = QLineEdit("MOCK0"); baud = QSpinBox(); baud.setRange(1, 4_000_000); baud.setValue(115200); data = QLineEdit("STATUS")
        row = QFormLayout(); row.addRow("Port", port); row.addRow("Baudrate", baud); row.addRow("Data", data); page.layout().insertLayout(1, row); actions = QHBoxLayout()
        calls = [("List", lambda: page.show_result(self.service.call("serial_list"))), ("Open", lambda: page.show_result(self.service.call("serial_open", {"port": port.text(), "baudrate": baud.value()}))), ("Write", lambda: page.show_result(self.service.call("serial_write", {"port": port.text(), "data": data.text()}))), ("Tail", lambda: page.show_result(self.service.call("serial_tail", {"port": port.text(), "baudrate": baud.value()}))), ("Close", lambda: page.show_result(self.service.call("serial_close", {"port": port.text()})))]
        for label, fn in calls: b = QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
        page.layout().insertLayout(2, actions)

    def _build_processes(self) -> None:
        page = self.pages["Processes"]; pid = QSpinBox(); pid.setRange(1, 2_000_000_000); row = QFormLayout(); row.addRow("PID", pid); page.layout().insertLayout(1, row)
        actions = QHBoxLayout();
        for label, fn in [("Output", lambda: page.show_result(self.service.call("process_output", {"pid": pid.value()}))), ("Stop", lambda: page.show_result(self.service.call("project_stop", {"pid": pid.value()})))]: b = QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
        page.layout().insertLayout(2, actions)

    def _build_activity(self) -> None:
        self.button(self.pages["Activity"], "Refresh", self.refresh_activity)

    def _build_settings(self) -> None:
        self.pages["Settings"].show_result({"service_host": self.service.host, "service_port": self.service.port})

    def refresh_devices(self) -> None:
        try: self.pages["Devices"].show_result(self.service.call("device_info"))
        except Exception as exc: self.pages["Devices"].show_result({"ok": False, "error": str(exc)})

    def refresh_activity(self) -> None:
        path = Path.home() / ".rdklink" / "logs" / "rdklink-service.log"
        self.pages["Activity"].output.setPlainText(read_log_tail(path))


def main() -> None:
    app = QApplication(sys.argv); window = MainWindow(); window.show(); sys.exit(app.exec())


if __name__ == "__main__": main()
