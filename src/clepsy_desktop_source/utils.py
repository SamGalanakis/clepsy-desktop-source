from urllib.parse import urlparse
import configparser
from loguru import logger
from clepsy_desktop_source.config import CFG_DIR, CFG_FILE, config
from pathlib import Path


def is_valid_url(url: str) -> bool:
    try:
        parts = urlparse(url)
        return all([parts.scheme, parts.netloc])
    except ValueError:
        return False


def save_config(
    clepsy_backend_url: str,
    device_token: str,
    source_name: str,
    source_id: int | None,
    active: bool,
):
    logger.info(f"Saving config to {CFG_FILE}...")
    Path(CFG_DIR).mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser()
    # Read existing config to preserve other sections
    if Path(CFG_FILE).exists():
        cfg.read(CFG_FILE)

    if "user" not in cfg:
        cfg["user"] = {}

    cfg["user"]["clepsy_backend_url"] = clepsy_backend_url
    cfg["user"]["device_token"] = device_token
    cfg["user"]["source_name"] = source_name
    cfg["user"]["source_id"] = str(source_id) if source_id is not None else ""
    cfg["user"]["active"] = "true" if active else "false"

    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)
        # Reload config into memory
        config.load_user_config()
        logger.info("Config saved and reloaded.")
    except (OSError, configparser.Error) as e:
        logger.error(f"Failed to save config to {CFG_FILE}: {e}")


def validate_pairing_input(
    clepsy_backend_url: str, source_name: str, code: str
) -> str | None:
    if not clepsy_backend_url:
        return "Clepsy deployment Url cannot be empty."
    if not source_name:
        return "Device name cannot be empty."
    if not code:
        return "Pairing code cannot be empty."
    if not is_valid_url(clepsy_backend_url):
        return "Invalid Clepsy deployment Url format."
    return None


def validate_runtime_config(clepsy_backend_url: str, device_token: str) -> str | None:
    if not clepsy_backend_url:
        return "Clepsy  deployment url cannot be empty."
    if not is_valid_url(clepsy_backend_url):
        return "Invalid Clepsy deployment Url format."
    if not device_token:
        return "Device is not paired (missing device token)."
    return None


def reset_user_config() -> None:
    try:
        if Path(CFG_FILE).exists():
            Path(CFG_FILE).unlink()
    except OSError:
        # Fallback: clear section and rewrite file
        cfg = configparser.ConfigParser()
        cfg.read(CFG_FILE)
        if cfg.has_section("user"):
            cfg.remove_section("user")
        Path(CFG_DIR).mkdir(parents=True, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)
    finally:
        # Reload in-memory config to defaults
        config.load_user_config()
