"""
Rclone binary management — find bundled binary, run OAuth, check health.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

from config_manager import APP_SUPPORT_DIR, RCLONE_CONFIG


def get_rclone_path() -> str:
    """
    Resolve path to rclone binary.
    Priority: bundled in .app > system PATH (homebrew, etc.)
    """
    # 1. Check inside .app bundle (PyInstaller or py2app)
    if getattr(sys, "frozen", False):
        # PyInstaller: resources in _MEIPASS parent's Resources
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            resources = Path(meipass).parent / "Resources"
        else:
            resources = Path(sys.executable).parent.parent / "Resources"
    else:
        resources = Path(__file__).parent / "resources"

    arch = platform.machine()  # "arm64" or "x86_64"
    bundled = resources / f"rclone_{arch}" / "rclone"
    if bundled.exists() and os.access(str(bundled), os.X_OK):
        return str(bundled)

    # 2. Common homebrew paths
    for p in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone"):
        if Path(p).exists():
            return p

    # 3. Fallback to PATH
    return "rclone"


def rclone_env() -> dict:
    """Environment dict pointing RCLONE_CONFIG to our app-specific config."""
    env = os.environ.copy()
    env["RCLONE_CONFIG"] = str(RCLONE_CONFIG)
    return env


def is_rclone_available(rclone_path: str = None) -> bool:
    """Check if rclone binary is usable."""
    path = rclone_path or get_rclone_path()
    try:
        result = subprocess.run(
            [path, "version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def is_gphotos_configured(rclone_path: str = None) -> bool:
    """Check if the 'gphotos' remote exists in our rclone config."""
    path = rclone_path or get_rclone_path()
    try:
        result = subprocess.run(
            [path, "listremotes"],
            capture_output=True, text=True, timeout=10,
            env=rclone_env(),
        )
        return "gphotos:" in result.stdout
    except Exception:
        return False


def run_oauth_setup(rclone_path: str = None) -> bool:
    """
    Launch rclone config to create the 'gphotos' Google Photos remote.
    Opens a browser window for OAuth consent. Blocks until complete.
    Returns True on success.
    """
    path = rclone_path or get_rclone_path()
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [path, "config", "create", "gphotos", "google photos"],
            env=rclone_env(),
            timeout=300,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def run_oauth_setup_interactive(rclone_path: str = None) -> bool:
    """
    Fallback: open Terminal.app with rclone config for interactive setup.
    Used when the non-interactive create doesn't work.
    """
    path = rclone_path or get_rclone_path()
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    script = (
        f'export RCLONE_CONFIG="{RCLONE_CONFIG}"; '
        f'echo "=== Sony Photos Sync: Google Photos Setup ==="; '
        f'echo "Choose: n (new remote), name it gphotos, type: google photos"; '
        f'echo ""; '
        f'"{path}" config; '
        f'echo ""; echo "Done! You can close this window."; '
        f'read -p "Press Enter to close..."'
    )

    try:
        subprocess.Popen([
            "osascript", "-e",
            f'tell app "Terminal" to do script "{script}"',
        ])
        return True
    except Exception:
        return False
