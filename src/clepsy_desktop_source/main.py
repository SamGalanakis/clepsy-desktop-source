import asyncio
from datetime import datetime
from loguru import logger
import os
from pathlib import Path
import queue
import signal
import sys
from asyncio import Queue as AsyncQueue

import fasteners
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QTimer, QThread
import qdarktheme
import faulthandler
from clepsy_desktop_source.data_generator import data_generator_worker
from clepsy_desktop_source.entities import DesktopCheck, AfkStart, AppState
from clepsy_desktop_source.sender import request_sender_worker
from clepsy_desktop_source.config import config, ICON_PATH, LOG_FILE
from clepsy_desktop_source.gui import SettingsManager
from clepsy_desktop_source.utils import validate_runtime_config
from urllib.parse import urljoin
import httpx
import random
import platformdirs


app_state = AppState()
gui_queue = queue.Queue()
tray_app_instance = None

# Define and create a directory for the lock file
LOCK_DIR = Path(platformdirs.user_runtime_dir("clepsy"))
LOCK_DIR.mkdir(parents=True, exist_ok=True)
LOCK_FILE = LOCK_DIR / "clepsy.lock"

# Enable low-level traceback on fatal signals to the user log file
FAULT_LOG_FH = None
try:
    # Open the configured LOG_FILE (directories are ensured in config.py)
    fault_log_path = Path(LOG_FILE).resolve()
    # Unbuffered binary writes to ensure data lands even during hard crashes
    FAULT_LOG_FH = open(fault_log_path, "ab", buffering=0)
    try:
        FAULT_LOG_FH.write(
            f"[faulthandler] armed pid={os.getpid()} path={fault_log_path}\n".encode()
        )
    except Exception:
        pass
    faulthandler.enable(file=FAULT_LOG_FH, all_threads=True)
    # Allow manual dump: kill -USR1 <pid>
    try:
        faulthandler.register(signal.SIGUSR1, file=FAULT_LOG_FH, all_threads=True)
    except Exception:
        pass
except Exception:
    # Safe fallback: only enable faulthandler if stderr exists (avoids RuntimeError on Windows GUI)
    _stderr = getattr(sys, "stderr", None)
    if _stderr is not None:
        try:
            faulthandler.enable(file=_stderr, all_threads=True)
        except Exception:
            pass


# Log uncaught exceptions to help diagnose abrupt terminations
def excepthook(exctype, value, tb):  # pragma: no cover - debug aid
    try:
        logger.opt(exception=(exctype, value, tb)).error("Uncaught exception")
    except Exception:
        # Fallback if logger fails
        import traceback as tb_module

        print("Uncaught exception:")
        tb_module.print_exception(exctype, value, tb)


sys.excepthook = excepthook


class AsyncWorker(QThread):
    def __init__(self, state: AppState):
        super().__init__()
        self.app_state = state

    def run(self):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.main_async())
        except Exception as exc:  # pragma: no cover - safety net
            logger.error("AsyncWorker crashed: {}", exc, exc_info=True)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def main_async(self):
        publish_queue: AsyncQueue[DesktopCheck | AfkStart] = AsyncQueue()

        worker_task = data_generator_worker(publish_queue=publish_queue)

        request_sender_task = request_sender_worker(
            queue=publish_queue,
            app_state=self.app_state,
        )

        heartbeat_sender_task = self.heartbeat_sender_worker()

        try:
            await asyncio.gather(
                worker_task, request_sender_task, heartbeat_sender_task
            )
        except Exception as exc:  # pragma: no cover - safety net
            logger.error("Background tasks failed: {}", exc, exc_info=True)
            raise

    async def heartbeat_sender_worker(self):
        async with httpx.AsyncClient() as client:
            while True:
                # Skip if not configured/paired or not active
                if not (
                    config.user.clepsy_backend_url
                    and config.user.device_token
                    and config.user.active
                ):
                    await asyncio.sleep(random.randint(30, 60))
                    continue
                heartbeat_url = urljoin(
                    config.user.clepsy_backend_url, "/sources/source-heartbeats"
                )
                headers = {}
                if config.user.device_token:
                    headers["Authorization"] = f"Bearer {config.user.device_token}"
                try:
                    response = await client.put(heartbeat_url, headers=headers, json={})
                    response.raise_for_status()
                    logger.info("Heartbeat sent successfully.")
                    self.app_state.last_heartbeat_timestamp = datetime.now()
                    self.app_state.last_heartbeat_status = "Success"
                except httpx.HTTPStatusError as e:
                    logger.error(
                        f"Error sending heartbeat: {e.response.status_code} - {e.response.text}"
                    )
                    self.app_state.last_heartbeat_timestamp = datetime.now()
                    self.app_state.last_heartbeat_status = "Fail"
                except httpx.RequestError as e:
                    logger.error(f"Error sending heartbeat: {e}")
                    self.app_state.last_heartbeat_timestamp = datetime.now()
                    self.app_state.last_heartbeat_status = "Fail"

                # Sleep 30-60 seconds with jitter
                await asyncio.sleep(random.randint(30, 60))


class ClepsyTrayApp:
    def __init__(self, app):
        self.app = app

        self.settings_manager = SettingsManager(app_state)
        self.async_worker = None

        # Create system tray icon
        self.tray_icon = QSystemTrayIcon()
        self.tray_icon.setIcon(QIcon(ICON_PATH))

        # Connect double-click to show settings
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Create context menu
        self.create_tray_menu()

        # Set up timer for processing GUI queue
        self.timer = QTimer()
        self.timer.timeout.connect(self.process_gui_queue)
        self.timer.start(100)  # Check every 100ms

        # Show the tray icon
        self.tray_icon.show()

        # Check initial config after a short delay
        QTimer.singleShot(1000, self.check_initial_config)

    def create_tray_menu(self):
        menu = QMenu()

        # Settings action
        settings_action = QAction("Settings…")
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)

        # Quit action
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)

    def on_tray_icon_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,  # single left-click on many DEs
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.show_settings()

    def show_settings(self):
        self.settings_manager.ask_config()

    def quit_app(self):
        if self.async_worker and self.async_worker.isRunning():
            self.async_worker.terminate()
            self.async_worker.wait()
        self.app.quit()

    def process_gui_queue(self):
        try:
            message = gui_queue.get_nowait()
            if message == "show_settings":
                self.show_settings()
            elif message == "quit":
                self.quit_app()
        except queue.Empty:
            pass

    def check_initial_config(self):
        config_ok = False
        if config.user.clepsy_backend_url and config.user.device_token:
            error = validate_runtime_config(
                config.user.clepsy_backend_url, config.user.device_token
            )
            if not error:
                config_ok = True
            else:
                self.settings_manager.show_error_dialog(
                    "Invalid Configuration",
                    f"There was an error in your configuration file:\n\n{error}\n\nPlease correct it.",
                )

        if not config_ok:
            self.settings_manager.ask_config()

        # Always start background workers; they no-op until paired
        self.start_async_tasks()

    def start_async_tasks(self):
        if not self.async_worker:
            self.async_worker = AsyncWorker(app_state)
            self.async_worker.start()

    def run(self):
        return self.app.exec()


def signal_handler(signum, _frame):
    global tray_app_instance
    logger.info(f"Received signal {signum}, shutting down...")

    if tray_app_instance:
        tray_app_instance.quit_app()
    else:
        # Try to gracefully quit the Qt app if present
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            # Fallback to a normal exit to allow cleanup
            sys.exit(0)


@fasteners.interprocess_locked(str(Path.home() / "clepsy_single_instance.lock"))
def main():
    global tray_app_instance

    try:
        app = QApplication(sys.argv)
        qdarktheme.setup_theme()

        app.setQuitOnLastWindowClosed(False)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error("System tray is not available on this system.")
            return 1

        tray_app_instance = ClepsyTrayApp(app)

        # Set up signal handlers after app/tray are ready
        signal.signal(signal.SIGINT, signal_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal_handler)
        return tray_app_instance.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        if tray_app_instance:
            tray_app_instance.quit_app()
        return 0
    except Exception as e:
        logger.error(f"Error in main: {e}")
        return 1
    finally:
        # Ensure cleanup
        if tray_app_instance and tray_app_instance.async_worker:
            if tray_app_instance.async_worker.isRunning():
                tray_app_instance.async_worker.terminate()
                tray_app_instance.async_worker.wait()
        # Close faulthandler file if open
        try:
            if FAULT_LOG_FH and not FAULT_LOG_FH.closed:
                FAULT_LOG_FH.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt during startup, exiting...")
        sys.exit(0)
