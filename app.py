#!/usr/bin/env python3
"""
Sony Photos Sync — macOS menu bar app.

Transfers photos from Sony Alpha cameras to Google Photos
via WiFi FTP, completely automatically.
"""

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import rumps

from config_manager import ensure_dirs, load_config, APP_SUPPORT_DIR, LOG_FILE
from rclone_manager import (
    get_rclone_path,
    is_gphotos_configured,
    is_rclone_available,
    rclone_env,
    run_oauth_setup,
    run_oauth_setup_interactive,
)
from dedup import DedupDB
from gphotos_scanner import GPhotosScanner
from sync_engine import EngineState, SyncEngine, get_local_ip

logger = logging.getLogger("sony-sync")
VERSION = "1.0.0"
PLIST_LABEL = "com.sonyphotossync.app"


def _time_ago(timestamp: float) -> str:
    delta = int(time.time() - timestamp)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _icon_path(name: str) -> str:
    """Resolve icon path in both dev and bundled mode."""
    if getattr(sys, "frozen", False):
        # PyInstaller
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = Path(meipass) / "resources" / name
            if p.exists():
                return str(p)
        # py2app fallback
        resources = Path(sys.executable).parent.parent / "Resources"
    else:
        resources = Path(__file__).parent / "resources"
    path = resources / name
    if path.exists():
        return str(path)
    return None


class SonyPhotosSyncApp(rumps.App):
    def __init__(self):
        icon = _icon_path("icon.png")
        super().__init__(
            name="Sony Photos Sync",
            title="📷 SPS",
            icon=None,  # Use text-only title for now (more visible)
            template=True,
            quit_button=None,
        )
        self._setup_logging()
        self._config = load_config()
        ensure_dirs()
        self._rclone_path = None  # Resolved lazily
        self._dedup_db = None  # Created lazily
        self._scanner = None
        self._engine = None
        self._started = False
        self._build_menu()

    def _lazy_init(self):
        """Initialize heavy resources after the run loop is up."""
        if self._started:
            return
        self._started = True
        try:
            self._rclone_path = get_rclone_path()
            self._dedup_db = DedupDB()
            self._scanner = GPhotosScanner(self._dedup_db)
        except Exception as e:
            logger.error(f"Init error: {e}")
            self._status_item.title = f"Error: {e}"

    def _setup_logging(self):
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[logging.FileHandler(LOG_FILE)],
        )

    def _build_menu(self):
        port = self._config["ftp"]["port"]
        user = self._config["ftp"]["username"]
        passwd = self._config["ftp"]["password"]

        # Status — no heavy checks here, just placeholders
        self._status_item = rumps.MenuItem("Initializing...")

        # Toggle
        self._toggle_item = rumps.MenuItem("Start Sync", callback=self._toggle_sync)

        # Recent uploads
        self._recent_menu = rumps.MenuItem("Recent Uploads")
        self._recent_menu.add(rumps.MenuItem("(none yet)"))

        # FTP info — use placeholder IP, updated later by timer
        self._ftp_item = rumps.MenuItem(
            f"FTP: ...:{port}  ({user}/{passwd})",
            callback=self._copy_ftp_info,
        )

        # Dedup
        self._dedup_item = rumps.MenuItem("Dedup: initializing...")
        self._scan_item = rumps.MenuItem(
            "Scan Google Photos Library...", callback=self._scan_library
        )

        # Launch at login
        self._login_item = rumps.MenuItem("Launch at Login", callback=self._toggle_login)
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
        self._login_item.state = plist_path.exists()

        self.menu = [
            self._status_item,
            None,
            self._toggle_item,
            self._recent_menu,
            None,
            self._ftp_item,
            rumps.MenuItem("Camera Setup Guide", callback=self._show_camera_guide),
            rumps.MenuItem("Configure Google Photos...", callback=self._configure_gphotos),
            None,
            self._dedup_item,
            self._scan_item,
            None,
            rumps.MenuItem("Open Incoming Folder", callback=self._open_incoming),
            rumps.MenuItem("Open Uploaded Folder", callback=self._open_uploaded),
            rumps.MenuItem("View Log", callback=self._view_log),
            rumps.MenuItem("Edit Config", callback=self._edit_config),
            None,
            self._login_item,
            rumps.MenuItem(f"About Sony Photos Sync v{VERSION}", callback=self._show_about),
            rumps.MenuItem("Quit", callback=self._quit),
        ]

    # --- Status polling ---

    @rumps.timer(5)
    def _poll_status(self, _):
        """Poll engine status every 5 seconds. All heavy init happens here
        on the first tick, after the macOS run loop is established."""
        try:
            # Lazy init on first timer tick (run loop is now active)
            if not self._started:
                self._lazy_init()
                # Auto-start if Google Photos is configured
                # Update FTP info now that we're running
                local_ip = get_local_ip()
                port = self._config["ftp"]["port"]
                user = self._config["ftp"]["username"]
                passwd = self._config["ftp"]["password"]
                self._ftp_item.title = f"FTP: {local_ip}:{port}  ({user}/{passwd})"

                if self._rclone_path and is_gphotos_configured(self._rclone_path):
                    self._status_item.title = "Starting sync..."
                    self._start_engine()
                else:
                    self._status_item.title = "Setup required"
                    # Prompt user to configure Google Photos on first launch
                    rumps.notification(
                        "Sony Photos Sync",
                        "Welcome! Let's connect Google Photos.",
                        "Click the camera icon in your menu bar, then 'Configure Google Photos...'",
                    )
                # Update dedup count
                if self._dedup_db:
                    count = self._dedup_db.count()
                    self._dedup_item.title = f"Dedup: {count} photos indexed"
                return

            if self._engine is None:
                return

            status = self._engine.get_status()

            # Menu bar title
            if status.active_uploads > 0:
                self.title = f"↑{status.active_uploads}"
            else:
                self.title = "SPS"

            # Status line
            if status.state == EngineState.RUNNING:
                if status.active_uploads > 0:
                    self._status_item.title = f"Uploading {status.active_uploads} file(s)..."
                elif status.last_uploaded_time:
                    ago = _time_ago(status.last_uploaded_time)
                    total = status.total_uploaded
                    self._status_item.title = f"Idle — {total} synced, last {ago}"
                else:
                    self._status_item.title = "Running — waiting for photos"
            elif status.state == EngineState.PAUSED:
                self._status_item.title = "Paused"
            elif status.state == EngineState.ERROR:
                self._status_item.title = f"Error: {status.last_error or 'unknown'}"
            elif status.state == EngineState.STOPPED:
                self._status_item.title = "Stopped"

            # Toggle button text
            if status.state in (EngineState.RUNNING, EngineState.STARTING):
                self._toggle_item.title = "Pause Sync"
            else:
                self._toggle_item.title = "Start Sync"

            # Recent uploads submenu
            if status.recent_files:
                self._recent_menu.clear()
                for fname in reversed(status.recent_files):
                    self._recent_menu.add(rumps.MenuItem(fname))

            # Update FTP info (IP may change)
            if status.ftp_host:
                port = status.ftp_port
                user = self._config["ftp"]["username"]
                passwd = self._config["ftp"]["password"]
                self._ftp_item.title = f"FTP: {status.ftp_host}:{port}  ({user}/{passwd})"

            # Dedup stats
            if self._dedup_db and status.skipped_duplicates > 0:
                total_indexed = self._dedup_db.count()
                self._dedup_item.title = f"Dedup: {total_indexed} indexed, {status.skipped_duplicates} skipped"

            # Scanner progress
            if self._scanner and self._scanner.is_running:
                prog = self._scanner.progress
                self._scan_item.title = f"Scanning... {prog['scanned']} photos"

        except Exception as e:
            logger.error(f"Poll error: {e}")
            self._status_item.title = f"Error: {e}"

    # --- Engine control ---

    def _start_engine(self):
        if self._engine:
            return
        try:
            self._engine = SyncEngine(
                config=self._config,
                rclone_path=self._rclone_path,
                rclone_env=rclone_env(),
                dedup_db=self._dedup_db,
            )
            self._engine.start()
            self._toggle_item.set_callback(self._toggle_sync)
            logger.info("Engine started from menu bar app")
        except Exception as e:
            logger.error(f"Failed to start engine: {e}")
            self._status_item.title = f"Error: {e}"

    def _toggle_sync(self, sender):
        if not self._rclone_path:
            self._lazy_init()
        if self._engine is None:
            if self._rclone_path and is_gphotos_configured(self._rclone_path):
                self._start_engine()
            else:
                rumps.notification(
                    "Sony Photos Sync", "Setup Required",
                    "Configure Google Photos first."
                )
            return

        status = self._engine.get_status()
        if status.state == EngineState.RUNNING:
            self._engine.pause()
        elif status.state == EngineState.PAUSED:
            self._engine.resume()
        else:
            self._start_engine()

    # --- Google Photos setup ---

    def _configure_gphotos(self, _):
        if not self._rclone_path:
            self._lazy_init()
        if not is_rclone_available(self._rclone_path):
            rumps.alert(
                title="rclone Not Found",
                message=(
                    "rclone is required but was not found.\n\n"
                    "Install it with: brew install rclone\n\n"
                    "Then restart Sony Photos Sync."
                ),
            )
            return

        rumps.notification(
            "Sony Photos Sync",
            "Google Photos Setup",
            "A browser window will open. Sign in to Google and grant access.",
        )

        # Run OAuth in a background thread so the UI stays responsive
        def do_setup():
            success = run_oauth_setup(self._rclone_path)
            if success and is_gphotos_configured(self._rclone_path):
                rumps.notification(
                    "Sony Photos Sync", "Connected!",
                    "Google Photos is now linked. Sync starting.",
                )
                self._start_engine()
            else:
                # Fallback to interactive Terminal setup
                rumps.notification(
                    "Sony Photos Sync", "Manual Setup",
                    "Opening Terminal for interactive setup...",
                )
                run_oauth_setup_interactive(self._rclone_path)

        threading.Thread(target=do_setup, daemon=True).start()

    # --- Library scan ---

    def _scan_library(self, _):
        if not self._scanner:
            self._lazy_init()
        if self._scanner and self._scanner.is_running:
            rumps.notification(
                "Sony Photos Sync", "Scan in Progress",
                "A library scan is already running.",
            )
            return

        if not is_gphotos_configured(self._rclone_path):
            rumps.notification(
                "Sony Photos Sync", "Not Configured",
                "Configure Google Photos first.",
            )
            return

        existing = self._dedup_db.count(source="gphotos")
        msg = (
            "This scans your entire Google Photos library to detect\n"
            "duplicates before uploading. It downloads tiny thumbnails\n"
            "and indexes them locally.\n\n"
            "This may take a while for large libraries, but only needs\n"
            "to be done once."
        )
        if existing:
            msg += f"\n\nCurrently indexed: {existing} photos from Google Photos."

        response = rumps.alert(
            title="Scan Google Photos Library",
            message=msg,
            ok="Start Scan",
            cancel="Cancel",
        )
        if response != 1:
            return

        rumps.notification(
            "Sony Photos Sync", "Library Scan Started",
            "Scanning your Google Photos library in the background...",
        )

        def do_scan():
            def on_progress(count, status):
                if count % 500 == 0:
                    rumps.notification(
                        "Sony Photos Sync", "Scan Progress",
                        f"{count} photos indexed...",
                    )

            success = self._scanner.scan(on_progress=on_progress)
            prog = self._scanner.progress
            total = prog["scanned"]
            if success:
                rumps.notification(
                    "Sony Photos Sync", "Scan Complete",
                    f"Indexed {total} photos. Duplicates will now be detected.",
                )
                self._scan_item.title = "Scan Google Photos Library..."
                total_indexed = self._dedup_db.count()
                self._dedup_item.title = f"Dedup: {total_indexed} photos indexed"
            else:
                rumps.notification(
                    "Sony Photos Sync", "Scan Failed",
                    "Could not complete library scan. Check the log.",
                )
                self._scan_item.title = "Scan Google Photos Library..."

        threading.Thread(target=do_scan, daemon=True).start()

    # --- Camera guide ---

    def _show_camera_guide(self, _):
        local_ip = get_local_ip()
        port = self._config["ftp"]["port"]
        user = self._config["ftp"]["username"]
        passwd = self._config["ftp"]["password"]

        msg = (
            f"Configure your Sony Alpha camera:\n\n"
            f"1. Menu → Network → FTP Transfer → Server Setting\n"
            f"   • Display Name: My Mac\n"
            f"   • Destination: FTP\n"
            f"   • Server Name: {local_ip}\n"
            f"   • Port: {port}\n"
            f"   • User: {user}\n"
            f"   • Password: {passwd}\n\n"
            f"2. Menu → Network → FTP Transfer → Auto Transfer → On\n\n"
            f"3. Make sure your camera is on the same WiFi\n"
            f"   network as this Mac.\n\n"
            f"Compatible: A7R V, A7 IV, A7C II, A9 II, A1,\n"
            f"A6700, A6500, A6400, ZV-E1, and more."
        )

        response = rumps.alert(
            title="Camera Setup Guide",
            message=msg,
            ok="Copy FTP Details",
            cancel="Close",
        )
        if response == 1:  # OK button
            self._copy_ftp_to_clipboard()

    def _copy_ftp_info(self, _):
        self._copy_ftp_to_clipboard()
        rumps.notification("Sony Photos Sync", "", "FTP address copied to clipboard!")

    def _copy_ftp_to_clipboard(self):
        local_ip = get_local_ip()
        port = self._config["ftp"]["port"]
        user = self._config["ftp"]["username"]
        passwd = self._config["ftp"]["password"]
        text = f"Server: {local_ip}\nPort: {port}\nUser: {user}\nPassword: {passwd}"
        subprocess.run(["pbcopy"], input=text.encode(), check=True)

    # --- Folder / log actions ---

    def _open_incoming(self, _):
        subprocess.Popen(["open", self._config["paths"]["incoming_dir"]])

    def _open_uploaded(self, _):
        subprocess.Popen(["open", self._config["paths"]["uploaded_dir"]])

    def _view_log(self, _):
        subprocess.Popen(["open", "-a", "Console", str(LOG_FILE)])

    def _edit_config(self, _):
        from config_manager import CONFIG_FILE
        subprocess.Popen(["open", "-t", str(CONFIG_FILE)])

    # --- Launch at login ---

    def _toggle_login(self, sender):
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"

        if plist_path.exists():
            # Remove
            try:
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{_uid()}/{PLIST_LABEL}"],
                    capture_output=True,
                )
            except Exception:
                pass
            plist_path.unlink(missing_ok=True)
            sender.state = 0
            rumps.notification("Sony Photos Sync", "", "Removed from login items.")
        else:
            # Add
            app_path = self._get_app_path()
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>open</string>
        <string>-a</string>
        <string>{app_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist)
            try:
                subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist_path)],
                    capture_output=True,
                )
            except Exception:
                pass
            sender.state = 1
            rumps.notification("Sony Photos Sync", "", "Will launch at login.")

    def _get_app_path(self) -> str:
        if getattr(sys, "frozen", False):
            return str(Path(sys.executable).parent.parent.parent)
        return str(Path(__file__).resolve())

    # --- About / Quit ---

    def _show_about(self, _):
        rumps.alert(
            title="Sony Photos Sync",
            message=(
                f"Version {VERSION}\n\n"
                "Automatically transfers photos from your Sony Alpha\n"
                "camera to Google Photos over WiFi.\n\n"
                "Works with A7R V, A7 IV, A1, A9 II, A6700,\n"
                "and any Sony camera with FTP transfer.\n\n"
                "github.com/oz/sony-photos-sync"
            ),
        )

    def _quit(self, _):
        if self._engine:
            self._engine.stop()
        rumps.quit_application()


def _uid() -> int:
    import os
    return os.getuid()


def main():
    import sys

    # PyInstaller bundles need explicit NSApplication activation
    # for the menu bar icon to appear
    if getattr(sys, "frozen", False):
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            ns_app = NSApplication.sharedApplication()
            # Accessory = menu bar only, no dock icon
            ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except ImportError:
            pass  # AppKit not available — rumps will handle it

    try:
        app = SonyPhotosSyncApp()
        app.run()
    except Exception as e:
        print(f"FATAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
