"""
Config management for Sony Photos Sync.

Stores config and rclone credentials in ~/Library/Application Support/SonyPhotosSync/.
On first run, copies default config from the app bundle.
"""

import os
import shutil
from pathlib import Path

import yaml

APP_NAME = "SonyPhotosSync"
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_FILE = APP_SUPPORT_DIR / "config.yaml"
RCLONE_CONFIG = APP_SUPPORT_DIR / "rclone.conf"
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
LOG_FILE = LOG_DIR / "sync.log"


def _bundled_default_config() -> Path:
    """Path to config_default.yaml shipped with the app."""
    import sys
    if getattr(sys, "frozen", False):
        # PyInstaller: files are in _MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = Path(meipass) / "config_default.yaml"
            if p.exists():
                return p
        # py2app fallback
        return Path(sys.executable).parent.parent / "Resources" / "config_default.yaml"
    return Path(__file__).parent / "config_default.yaml"


def ensure_dirs():
    """Create all required directories."""
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    for key in ("incoming_dir", "uploaded_dir"):
        Path(config["paths"][key]).mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config, creating from defaults on first run."""
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        default = _bundled_default_config()
        if default.exists():
            shutil.copy(default, CONFIG_FILE)
        else:
            raise FileNotFoundError(
                f"No config found at {CONFIG_FILE} and no default at {default}"
            )
    with open(CONFIG_FILE) as f:
        raw = yaml.safe_load(f)
    # Expand ~ in paths
    for key in raw.get("paths", {}):
        raw["paths"][key] = str(Path(raw["paths"][key]).expanduser())
    # Override log file to standard macOS location
    raw["paths"]["log_file"] = str(LOG_FILE)
    return raw


def save_config(config: dict):
    """Write config back to disk."""
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False)


def is_configured() -> bool:
    """Check if the app has been through initial setup (rclone remote exists)."""
    return CONFIG_FILE.exists() and RCLONE_CONFIG.exists()
