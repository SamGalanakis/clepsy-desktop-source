from pydantic_settings import BaseSettings, SettingsConfigDict
import os
import logging
import sys
from datetime import timedelta
from loguru import logger
import configparser
import platformdirs
from enum import StrEnum
from pathlib import Path

APP_NAME = "clepsy"
CFG_DIR: Path = Path(platformdirs.user_config_dir(APP_NAME))
CFG_FILE: Path = CFG_DIR / "settings.ini"
LOG_DIR: Path = Path(platformdirs.user_log_dir(APP_NAME))
LOG_FILE: Path = LOG_DIR / "app.log"
LOCK_DIR: Path = Path(platformdirs.user_runtime_dir(APP_NAME))
LOCK_FILE: Path = LOCK_DIR / "clepsy.lock"


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", Path.cwd())
    base_path = Path(base_path)
    return str(base_path / relative_path)


ICON_PATH = resource_path("media/clepsy_black_white_logo.png")


class UserConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    clepsy_backend_url: str = ""
    device_token: str = ""
    source_name: str = ""
    source_id: int | None = None
    active: bool = True


class PlatformType(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


def detect_platform() -> PlatformType:
    p = sys.platform
    if p.startswith("win"):
        return PlatformType.WINDOWS
    if p == "darwin":
        return PlatformType.MACOS
    if p.startswith("linux"):
        return PlatformType.LINUX
    return PlatformType.UNKNOWN


class DisplayServerType(StrEnum):
    WIN32 = "win32"
    COCOA = "cocoa"
    X11 = "x11"
    WAYLAND = "wayland"
    UNKNOWN = "unknown"


def detect_display_server(
    platform_type: PlatformType | None = None,
) -> DisplayServerType:
    p = platform_type or detect_platform()
    if p == PlatformType.WINDOWS:
        return DisplayServerType.WIN32
    if p == PlatformType.MACOS:
        return DisplayServerType.COCOA
    if p == PlatformType.LINUX:
        xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if xdg == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            return DisplayServerType.WAYLAND
        if os.environ.get("DISPLAY"):
            return DisplayServerType.X11
    return DisplayServerType.UNKNOWN


PLATFORM: PlatformType = detect_platform()
DISPLAY_SERVER: DisplayServerType = detect_display_server(PLATFORM)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLEPSY_",
        env_nested_delimiter="__",
        extra="ignore",
    )
    screenshot_size: tuple[int, int] = (1024, 1024)
    screenshot_interval: timedelta = timedelta(seconds=30)
    afk_timeout: timedelta = timedelta(minutes=5)
    global_cd: timedelta = timedelta(seconds=5)
    same_window_cd: timedelta = timedelta(seconds=15)
    constant_window_cd: timedelta = timedelta(seconds=30)
    active_window_poll_interval: timedelta = timedelta(seconds=0.2)
    log_level: str = "INFO"

    user: UserConfig = UserConfig()
    platform: PlatformType = PLATFORM
    display_server: DisplayServerType = DISPLAY_SERVER

    def load_user_config(self):
        cfg = configparser.ConfigParser()
        cfg.read(CFG_FILE)
        if "user" in cfg:
            section = cfg["user"]
            device_token = section.get("device_token")
            source_id_val = section.get("source_id", None)
            try:
                parsed_source_id = int(source_id_val) if source_id_val else None
            except (TypeError, ValueError):
                parsed_source_id = None
            parsed = {
                "clepsy_backend_url": section.get("clepsy_backend_url", ""),
                "device_token": device_token,
                "source_name": section.get("source_name", ""),
                "source_id": parsed_source_id,
                "active": section.getboolean("active", True),
            }
            self.user = UserConfig(**parsed)


config = Config()
config.load_user_config()


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller to get correct stack depth
        frame, depth = logging.currentframe(), 2
        while frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

# Ensure required user directories exist early (config, log, runtime)
for _p in (CFG_DIR, LOG_DIR, LOCK_DIR):
    try:
        _p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Directory creation failure should not abort import; will surface later if needed
        pass


# Safely remove existing handlers (if any)
try:
    # When called without an id, this removes all configured handlers.
    logger.remove()
except ValueError:
    # No handlers were configured yet; ignore.
    pass

# Conditionally add stderr sink (skip in Windows noconsole / GUI builds where stderr may be None)
stderr = getattr(sys, "stderr", None)
if stderr is not None and hasattr(stderr, "write"):
    logger.add(
        stderr,
        level=config.log_level,
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )

logger.add(
    LOG_FILE,
    rotation="5 MB",
    retention=timedelta(days=7),
    backtrace=True,
    diagnose=True,
    level=config.log_level,
    enqueue=True,
)
logger.info("Log level set to {}", config.log_level)
logger.info(
    "Starting clepsy on platform: {}, display server: {}", PLATFORM, DISPLAY_SERVER
)
