import uuid
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QTabWidget,
    QWidget,
    QCheckBox,
    QStackedWidget,
)
from PySide6.QtCore import QTimer, Signal, QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QDesktopServices
from clepsy_desktop_source.config import config, ICON_PATH
from clepsy_desktop_source.utils import (
    save_config,
    validate_pairing_input,
    is_valid_url,
    reset_user_config,
)
from urllib.parse import urljoin
import httpx


class PairingPage(QWidget):
    paired = Signal(dict)

    def __init__(self, initial_values: dict | None = None):
        super().__init__()
        self.initial_values = initial_values or {}
        self.setup_ui()
        self.prefill(self.initial_values)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        form_layout = QGridLayout()

        form_layout.addWidget(QLabel("Clepsy Deployment Url:"), 0, 0)
        self.url_entry = QLineEdit()
        form_layout.addWidget(self.url_entry, 0, 1)

        form_layout.addWidget(QLabel("Device Name:"), 1, 0)
        self.source_name_entry = QLineEdit()
        form_layout.addWidget(self.source_name_entry, 1, 1)

        form_layout.addWidget(QLabel("Pairing Code:"), 2, 0)
        self.code_entry = QLineEdit()
        self.code_entry.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addWidget(self.code_entry, 2, 1)

        layout.addLayout(form_layout)

        button_layout = QHBoxLayout()
        self.pair_button = QPushButton("Pair")
        self.pair_button.clicked.connect(self.attempt_pair)
        self.feedback_label = QLabel("")
        button_layout.addWidget(self.pair_button)
        button_layout.addWidget(self.feedback_label)
        button_layout.addStretch()

        layout.addLayout(button_layout)
        layout.addStretch()

    def prefill(self, values: dict | None = None):
        values = values or {}
        cleaned = {
            "clepsy_backend_url": values.get("clepsy_backend_url", ""),
            "source_name": values.get("source_name", ""),
        }
        self.initial_values = cleaned
        self.url_entry.setText(cleaned["clepsy_backend_url"])
        self.source_name_entry.setText(cleaned["source_name"])
        self.code_entry.clear()
        self.clear_feedback()

    def clear_feedback(self):
        self.feedback_label.setText("")
        self.feedback_label.setStyleSheet("")

    def show_feedback(self, message: str, success: bool = False):
        self.feedback_label.setText(message)
        color = "green" if success else "red"
        self.feedback_label.setStyleSheet(f"color: {color}")
        if message:
            QTimer.singleShot(5000, self.clear_feedback)

    def attempt_pair(self):
        clepsy_backend_url = self.url_entry.text().strip()
        source_name = self.source_name_entry.text().strip()
        code = self.code_entry.text().strip()

        error = validate_pairing_input(clepsy_backend_url, source_name, code)
        if error:
            self.show_feedback(f"Error: {error}", success=False)
            return

        pair_url = urljoin(clepsy_backend_url, "/sources/pair")
        self.pair_button.setEnabled(False)
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    pair_url,
                    json={
                        "code": code,
                        "device_name": source_name,
                        "source_type": "desktop",
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                device_token = data.get("device_token", "")
                source_id = data.get("source_id")
                if not device_token:
                    raise ValueError("Server did not return a device_token.")
                save_config(
                    clepsy_backend_url,
                    device_token,
                    source_name,
                    source_id,
                    True,
                )
                self.code_entry.clear()
                self.show_feedback("Paired successfully.", success=True)
                payload = {
                    "clepsy_backend_url": clepsy_backend_url,
                    "source_name": source_name,
                    "message": "Paired successfully.",
                }
                self.paired.emit(payload)
            elif resp.status_code in (400, 401, 404):
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"message": resp.text}
                self.show_feedback(
                    f"Pairing failed: {detail.get('message', resp.reason_phrase)}",
                    success=False,
                )
            else:
                self.show_feedback(
                    f"Pairing failed: {resp.status_code} {resp.text}", success=False
                )
        except Exception as exc:  # noqa: BLE001 - user feedback
            self.show_feedback(f"Pairing error: {exc}", success=False)
        finally:
            self.pair_button.setEnabled(True)


class SettingsTab(QWidget):
    unpaired = Signal(dict)
    settings_updated = Signal(dict)

    def __init__(self, initial_values: dict | None = None):
        super().__init__()
        self.initial_values = initial_values or {}
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("Clepsy Deployment Url:"), 0, 0)
        self.url_entry = QLineEdit()
        self.url_entry.setText(self.initial_values.get("clepsy_backend_url", ""))
        form_layout.addWidget(self.url_entry, 0, 1)

        form_layout.addWidget(QLabel("Device Name:"), 1, 0)
        self.source_name_entry = QLineEdit()
        self.source_name_entry.setText(self.initial_values.get("source_name", ""))
        form_layout.addWidget(self.source_name_entry, 1, 1)

        layout.addLayout(form_layout)

        self.active_checkbox = QCheckBox("Active (monitoring is on)")
        self.active_checkbox.setChecked(self.initial_values.get("active", True))
        layout.addWidget(self.active_checkbox)

        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_settings)
        self.feedback_label = QLabel("")
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.feedback_label)
        button_layout.addStretch()

        self.reset_button = QPushButton("Reset Pairing…")
        self.reset_button.clicked.connect(self.reset_pairing)
        button_layout.addWidget(self.reset_button)

        layout.addLayout(button_layout)
        layout.addStretch()

    def update_values(self, values: dict | None = None):
        values = values or {}
        self.initial_values = {**self.initial_values, **values}
        self.url_entry.setText(self.initial_values.get("clepsy_backend_url", ""))
        self.source_name_entry.setText(self.initial_values.get("source_name", ""))
        self.active_checkbox.setChecked(self.initial_values.get("active", True))

    def clear_feedback(self):
        self.feedback_label.setText("")
        self.feedback_label.setStyleSheet("")

    def show_feedback(self, message: str, success: bool):
        self.feedback_label.setText(message)
        color = "green" if success else "red"
        self.feedback_label.setStyleSheet(f"color: {color}")
        if message:
            QTimer.singleShot(4000, self.clear_feedback)

    def save_settings(self):
        clepsy_backend_url = self.url_entry.text().strip()
        source_name = self.source_name_entry.text().strip()
        is_active = self.active_checkbox.isChecked()

        if not clepsy_backend_url or not is_valid_url(clepsy_backend_url):
            self.show_feedback("Error: Invalid Clepsy deployment Url.", success=False)
            return

        device_token = config.user.device_token
        if not device_token:
            self.show_feedback("Error: Device is not paired.", success=False)
            return

        save_config(
            clepsy_backend_url,
            device_token,
            source_name or config.user.source_name,
            config.user.source_id,
            is_active,
        )
        self.initial_values.update(
            {
                "clepsy_backend_url": clepsy_backend_url,
                "source_name": source_name or config.user.source_name,
                "active": is_active,
            }
        )
        self.settings_updated.emit(self.initial_values.copy())
        self.show_feedback("Settings saved.", success=True)

    def reset_pairing(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Reset Pairing")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(
            "This will remove the stored device token and settings and return the app to first-run state.\n\n"
            "You will need to enter the server URL and a new one-time pairing code to connect again.\n\n"
            "Do you want to proceed?"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
        )
        msg.button(QMessageBox.StandardButton.Ok).setText("Reset")
        result = msg.exec()
        if result == QMessageBox.StandardButton.Ok:
            previous_values = {
                "clepsy_backend_url": self.url_entry.text().strip(),
                "source_name": self.source_name_entry.text().strip(),
            }
            reset_user_config()
            self.show_feedback("Pairing has been reset.", success=True)
            payload = {**previous_values, "message": "Pairing has been reset."}
            self.unpaired.emit(payload)


class MonitoringTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state
        self.setup_ui()

        # Set up timer to update monitoring data
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_monitoring_data)
        self.update_timer.start(1000)  # Update every second

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Create monitoring layout
        monitor_layout = QGridLayout()

        # Last Heartbeat
        monitor_layout.addWidget(QLabel("Last Heartbeat:"), 0, 0)
        self.heartbeat_label = QLabel("N/A")
        monitor_layout.addWidget(self.heartbeat_label, 0, 1)

        # Last Data Sent
        monitor_layout.addWidget(QLabel("Last Data Sent:"), 1, 0)
        self.send_label = QLabel("N/A")
        monitor_layout.addWidget(self.send_label, 1, 1)

        layout.addLayout(monitor_layout)
        layout.addStretch()  # Push everything to the top

    def update_monitoring_data(self):
        now = datetime.now()

        # Heartbeat
        if self.app_state.last_heartbeat_timestamp:
            time_diff = now - self.app_state.last_heartbeat_timestamp
            ago = self.format_time_diff(time_diff)
            status = self.app_state.last_heartbeat_status or "N/A"
            self.heartbeat_label.setText(f"{ago} ({status})")
        else:
            self.heartbeat_label.setText("N/A")

        # Data Sent
        if self.app_state.last_data_sent_timestamp:
            time_diff = now - self.app_state.last_data_sent_timestamp
            ago = self.format_time_diff(time_diff)
            status = self.app_state.last_data_sent_status or "N/A"
            self.send_label.setText(f"{ago} ({status})")
        else:
            self.send_label.setText("N/A")

    def format_time_diff(self, time_diff):
        total_seconds = int(time_diff.total_seconds())

        if total_seconds < 60:
            return f"{total_seconds}s ago"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}m {seconds}s ago"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m ago"


class ControlPanelWindow(QDialog):
    def __init__(self, parent=None, initial_values: dict | None = None, app_state=None):
        super().__init__(parent)
        self.setWindowTitle("Clepsy Control Panel")
        self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(500, 400)
        self.setModal(False)

        self.initial_values = initial_values or {}
        self.app_state = app_state

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.setLayout(layout)

        self.dashboard_url: str | None = None

        self.header_button = QPushButton("Clepsy")
        self.header_button.setIcon(QIcon(ICON_PATH))
        self.header_button.setIconSize(QSize(32, 32))
        self.header_button.setFlat(True)
        self.header_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_button.setStyleSheet(
            "font-size: 20px; font-weight: 600; text-align: left; padding: 6px 12px;"
        )
        self.header_button.setToolTip("Open Clepsy Dashboard")
        self.header_button.clicked.connect(self.open_dashboard)
        self.header_button.setVisible(False)
        layout.addWidget(self.header_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.pairing_page = PairingPage(self.initial_values)
        self.pairing_page.paired.connect(self.on_paired)
        self.stack.addWidget(self.pairing_page)

        self.tab_widget = QTabWidget()
        self.settings_tab = SettingsTab(self.initial_values)
        self.settings_tab.unpaired.connect(self.on_unpaired)
        self.settings_tab.settings_updated.connect(self.on_settings_updated)
        self.monitoring_tab = MonitoringTab(self.app_state)
        self.tab_widget.addTab(self.settings_tab, "Settings")
        self.tab_widget.addTab(self.monitoring_tab, "Monitoring")
        self.stack.addWidget(self.tab_widget)

        self.update_header_state(self.initial_values.get("clepsy_backend_url"))

        if config.user.device_token:
            self.header_button.setVisible(True)
            self.stack.setCurrentWidget(self.tab_widget)
        else:
            self.header_button.setVisible(False)
            self.stack.setCurrentWidget(self.pairing_page)

    def on_paired(self, payload: dict | None = None):
        payload = payload or {}
        self.initial_values = config.user.model_dump()
        self.settings_tab.update_values(self.initial_values)
        self.update_header_state(self.initial_values.get("clepsy_backend_url"))
        self.header_button.setVisible(True)
        self.stack.setCurrentWidget(self.tab_widget)
        message = payload.get("message")
        if message:
            self.settings_tab.show_feedback(message, success=True)

    def on_unpaired(self, payload: dict | None = None):
        payload = payload or {}
        self.initial_values = config.user.model_dump()
        self.settings_tab.update_values(self.initial_values)
        self.pairing_page.prefill(payload)
        self.update_header_state(None)
        self.header_button.setVisible(False)
        self.stack.setCurrentWidget(self.pairing_page)
        message = payload.get("message")
        if message:
            self.pairing_page.show_feedback(message, success=True)

    def on_settings_updated(self, payload: dict | None = None):
        payload = payload or {}
        backend_url = payload.get("clepsy_backend_url")
        self.update_header_state(backend_url)

    def update_header_state(self, base_url: str | None):
        base_url = (base_url or "").strip()
        if base_url:
            normalized = base_url if base_url.endswith("/") else f"{base_url}/"
            self.dashboard_url = urljoin(normalized, "s/")
            self.header_button.setEnabled(True)
            self.header_button.setToolTip(
                f"Open Clepsy Dashboard ({self.dashboard_url})"
            )
        else:
            self.dashboard_url = None
            self.header_button.setEnabled(False)
            self.header_button.setToolTip(
                "Set the Clepsy deployment URL in Settings to open the dashboard."
            )

    def open_dashboard(self):
        if not self.dashboard_url:
            QMessageBox.warning(
                self,
                "Clepsy Dashboard",
                "Set the Clepsy deployment URL in Settings before opening the dashboard.",
            )
            return
        QDesktopServices.openUrl(QUrl(self.dashboard_url))


class SettingsManager:
    def __init__(self, app_state=None):
        self.app_state = app_state
        self.window = None

    def show_error_dialog(self, title: str, message: str):
        msg_box = QMessageBox()
        msg_box.setWindowTitle(title)
        msg_box.setWindowIcon(QIcon(ICON_PATH))
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def show_info_dialog(self, title: str, message: str):
        msg_box = QMessageBox()
        msg_box.setWindowTitle(title)
        msg_box.setWindowIcon(QIcon(ICON_PATH))
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def ask_config(self):
        if self.window:
            self.window.show()
            self.window.activateWindow()
            self.window.raise_()
            return

        initial_values = config.user.model_dump()
        if not initial_values.get("source_name"):
            initial_values["source_name"] = f"desktop_source_{uuid.uuid4()}"

        self.window = ControlPanelWindow(
            initial_values=initial_values, app_state=self.app_state
        )
        self.window.finished.connect(self.on_window_closed)
        self.window.show()

    def on_window_closed(self, result):
        self.window = None


# Legacy functions for backward compatibility
def ask_config():
    manager = SettingsManager()
    manager.ask_config()


def show_error_dialog(title: str, message: str):
    manager = SettingsManager()
    manager.show_error_dialog(title, message)


def show_info_dialog(title: str, message: str):
    manager = SettingsManager()
    manager.show_info_dialog(title, message)
