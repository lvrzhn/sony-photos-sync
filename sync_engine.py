"""
SyncEngine — FTP server + folder watcher + Google Photos uploader.

Thread-safe engine with start/stop/pause/get_status interface,
designed to be driven by the menu bar app.
"""

import copy
import logging
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Lazy imports for pyftpdlib — importing pyftpdlib.servers at module level
# triggers multiprocessing.Event() which fails on macOS when POSIX semaphores
# are exhausted. We only need the basic FTPServer, not MultiprocessFTPServer.
DummyAuthorizer = None
FTPHandler = None
FTPServer = None


def _ensure_ftp_imports():
    global DummyAuthorizer, FTPHandler, FTPServer
    if FTPServer is not None:
        return
    from pyftpdlib.authorizers import DummyAuthorizer as _DA
    from pyftpdlib.handlers import FTPHandler as _FH
    from pyftpdlib.servers import FTPServer as _FS
    DummyAuthorizer = _DA
    FTPHandler = _FH
    FTPServer = _FS

logger = logging.getLogger("sony-sync")

from dedup import DedupDB, check_and_record

MAX_RECENT = 10


class EngineState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class SyncStatus:
    state: EngineState = EngineState.STOPPED
    active_uploads: int = 0
    total_uploaded: int = 0
    last_uploaded_file: Optional[str] = None
    last_uploaded_time: Optional[float] = None
    last_error: Optional[str] = None
    recent_files: List[str] = field(default_factory=list)
    skipped_duplicates: int = 0
    ftp_host: str = ""
    ftp_port: int = 0


def get_local_ip() -> str:
    """Get the Mac's local network IP address."""
    try:
        result = subprocess.run(
            ["ipconfig", "getifaddr", "en0"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Fallback: connect to a public address to determine local interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _is_jpeg(path: Path) -> bool:
    return path.suffix.lower() in (".jpg", ".jpeg")


def _wait_for_stable(path: Path, settle_time: float = 3, timeout: float = 120) -> bool:
    """Wait until a file's size stops changing (fully written)."""
    deadline = time.time() + timeout
    prev_size = -1
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == prev_size and size > 0:
            return True
        prev_size = size
        time.sleep(settle_time)
    return False


class _IncomingHandler(FileSystemEventHandler):
    """Watches the incoming directory for new JPEGs."""

    def __init__(self, engine: "SyncEngine"):
        self._engine = engine

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if _is_jpeg(path):
            self._engine._submit_upload(path)

    def on_moved(self, event):
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if _is_jpeg(path):
            self._engine._submit_upload(path)


class SyncEngine:
    """
    Core sync engine. Call start() to begin, get_status() to poll state.

    Parameters:
        config: Parsed config dict
        rclone_path: Path to rclone binary
        rclone_env: Optional environment dict for rclone (e.g. RCLONE_CONFIG)
    """

    def __init__(self, config: dict, rclone_path: str = "rclone",
                 rclone_env: Optional[dict] = None,
                 dedup_db: Optional[DedupDB] = None,
                 on_upload=None):
        self._config = config
        self._rclone_path = rclone_path
        self._rclone_env = rclone_env
        self._dedup = dedup_db or DedupDB()
        self._on_upload = on_upload  # callback(filename, uploaded_path)

        self._status = SyncStatus()
        self._lock = threading.Lock()

        self._ftp_server: Optional[FTPServer] = None
        self._ftp_thread: Optional[threading.Thread] = None
        self._observer: Optional[Observer] = None
        self._pool: Optional[ThreadPoolExecutor] = None

        self._seen: set = set()
        self._seen_lock = threading.Lock()
        self._paused = False

        # Paths
        self._incoming_dir = Path(config["paths"]["incoming_dir"])
        self._uploaded_dir = Path(config["paths"]["uploaded_dir"])
        self._incoming_dir.mkdir(parents=True, exist_ok=True)
        self._uploaded_dir.mkdir(parents=True, exist_ok=True)

        # Upload settings
        self._remote = config["rclone"]["remote_name"]
        self._album = config["rclone"].get("album", "")
        self._settle_time = config["upload"].get("settle_time", 3)
        self._extensions = set(config["upload"].get("extensions", [".jpg", ".jpeg"]))
        max_workers = config["upload"].get("max_workers", 2)
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def get_status(self) -> SyncStatus:
        """Thread-safe snapshot of current status."""
        with self._lock:
            return copy.copy(self._status)

    def start(self):
        """Start FTP server and folder watcher."""
        with self._lock:
            if self._status.state == EngineState.RUNNING:
                return
            self._status.state = EngineState.STARTING

        try:
            # FTP server is optional — if it fails (e.g. port in use,
            # semaphore exhaustion), the watcher still works for manual drops
            try:
                self._start_ftp()
            except Exception as e:
                logger.warning(f"FTP server failed to start: {e}")
                logger.warning("Photos can still be synced by dropping files into the incoming folder.")
                with self._lock:
                    self._status.last_error = f"FTP unavailable: {e}"

            self._start_watcher()
            self._process_existing()

            with self._lock:
                self._status.state = EngineState.RUNNING
                self._status.ftp_host = get_local_ip()
                self._status.ftp_port = self._config["ftp"]["port"]

            logger.info("SyncEngine started")
        except Exception as e:
            logger.error(f"Failed to start: {e}")
            with self._lock:
                self._status.state = EngineState.ERROR
                self._status.last_error = str(e)

    def stop(self):
        """Graceful shutdown."""
        logger.info("SyncEngine stopping...")

        if self._ftp_server:
            try:
                self._ftp_server.close_all()
            except Exception:
                pass

        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass

        if self._pool:
            self._pool.shutdown(wait=False)

        with self._lock:
            self._status.state = EngineState.STOPPED

        logger.info("SyncEngine stopped")

    def pause(self):
        """Pause accepting new uploads. FTP server stays up."""
        self._paused = True
        with self._lock:
            self._status.state = EngineState.PAUSED
        logger.info("SyncEngine paused")

    def resume(self):
        """Resume from paused state."""
        self._paused = False
        with self._lock:
            self._status.state = EngineState.RUNNING
        # Pick up any files that arrived while paused
        self._process_existing()
        logger.info("SyncEngine resumed")

    # --- Internal methods ---

    def _start_ftp(self):
        _ensure_ftp_imports()
        ftp_cfg = self._config["ftp"]

        authorizer = DummyAuthorizer()
        authorizer.add_user(
            ftp_cfg["username"],
            ftp_cfg["password"],
            str(self._incoming_dir),
            perm="elradfmw",
        )

        handler = FTPHandler
        handler.authorizer = authorizer
        handler.passive_ports = range(60000, 60100)
        handler.banner = "Sony Photos Sync"
        logging.getLogger("pyftpdlib").setLevel(logging.WARNING)

        self._ftp_server = FTPServer(
            (ftp_cfg["host"], ftp_cfg["port"]), handler
        )
        self._ftp_server.max_cons = 5
        self._ftp_server.max_cons_per_ip = 5

        self._ftp_thread = threading.Thread(
            target=self._ftp_server.serve_forever,
            daemon=True,
            name="ftp-server",
        )
        self._ftp_thread.start()

        logger.info(f"FTP server on {ftp_cfg['host']}:{ftp_cfg['port']}")

    def _start_watcher(self):
        self._observer = Observer()
        self._observer.schedule(
            _IncomingHandler(self), str(self._incoming_dir), recursive=True
        )
        self._observer.start()
        logger.info(f"Watching: {self._incoming_dir}")

    def _process_existing(self):
        """Queue any JPEGs already in the incoming directory."""
        count = 0
        try:
            for f in sorted(self._incoming_dir.iterdir()):
                if f.is_file() and _is_jpeg(f):
                    self._submit_upload(f)
                    count += 1
        except FileNotFoundError:
            pass
        if count:
            logger.info(f"Queued {count} existing file(s)")

    def _submit_upload(self, path: Path):
        """Submit a file for upload (deduplicated, respects pause)."""
        if self._paused:
            return

        with self._seen_lock:
            key = path.name  # Use filename only to catch all path variants
            if key in self._seen:
                logger.debug(f"Already seen, skipping: {key} (path={path})")
                return
            self._seen.add(key)
            logger.info(f"Queued: {key}")

        self._pool.submit(self._upload, path)

    def _rclone_dest(self) -> str:
        if self._album:
            return f"{self._remote}:album/{self._album}"
        return f"{self._remote}:upload"

    def _upload(self, path: Path):
        """Upload a single file to Google Photos via rclone."""
        with self._lock:
            self._status.active_uploads += 1

        try:
            if not path.exists():
                return

            if not _wait_for_stable(path, self._settle_time):
                logger.warning(f"File not stable, skipping: {path.name}")
                return

            # Dedup check: only check, don't record yet
            from dedup import _compute_phash
            phash = _compute_phash(str(path))
            if phash is not None:
                is_dup, match = self._dedup.is_duplicate(phash)
                if is_dup:
                    logger.info(f"Duplicate skipped: {path.name} (matches {match})")
                    try:
                        self._move_to_uploaded(path)
                    except FileNotFoundError:
                        pass
                    with self._lock:
                        self._status.skipped_duplicates += 1
                    return

            size_mb = path.stat().st_size / (1024 * 1024)
            logger.info(f"Uploading: {path.name} ({size_mb:.1f} MB)")

            cmd = [
                self._rclone_path, "copy",
                str(path), self._rclone_dest(),
                "--log-level", "NOTICE",
                "--tpslimit", "1",
                "--transfers", "1",
            ]

            env = self._rclone_env if self._rclone_env else None

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, env=env,
            )

            if result.returncode == 0:
                # Record hash only after successful upload
                if phash is not None:
                    self._dedup.add_hash(phash, filename=path.name, source="local")
                dest = self._move_to_uploaded(path)
                logger.info(f"Done: {path.name} -> {dest.name}")
                with self._lock:
                    self._status.total_uploaded += 1
                    self._status.last_uploaded_file = path.name
                    self._status.last_uploaded_time = time.time()
                    self._status.recent_files.append(path.name)
                    if len(self._status.recent_files) > MAX_RECENT:
                        self._status.recent_files.pop(0)
                    self._status.last_error = None
                if self._on_upload:
                    try:
                        self._on_upload(path.name, dest)
                    except Exception:
                        pass
            else:
                err = result.stderr.strip()[:200]
                logger.error(f"Upload failed [{path.name}]: {err}")
                with self._lock:
                    self._status.last_error = err
                # Quota exceeded — schedule retry later
                if "Quota exceeded" in result.stderr:
                    logger.info(f"Quota hit, will retry {path.name} in 30 minutes")
                    with self._lock:
                        self._status.last_error = "Upload quota reached — retrying later"
                    self._schedule_retry(path, delay=1800)

        except subprocess.TimeoutExpired:
            logger.error(f"Upload timed out: {path.name}")
            with self._lock:
                self._status.last_error = f"Timeout: {path.name}"
        except Exception as e:
            logger.error(f"Error uploading {path.name}: {e}")
            with self._lock:
                self._status.last_error = str(e)
        finally:
            with self._lock:
                self._status.active_uploads -= 1
            # Don't remove from _seen — prevents re-processing from
            # duplicate watchdog events (on_created + on_moved + process_existing)

    def _schedule_retry(self, path: Path, delay: float):
        """Retry an upload after a delay (e.g. quota reset)."""
        def _retry():
            time.sleep(delay)
            if path.exists():
                with self._seen_lock:
                    self._seen.discard(path.name)
                logger.info(f"Retrying upload: {path.name}")
                self._submit_upload(path)
        threading.Thread(target=_retry, daemon=True, name=f"retry-{path.name}").start()

    def _move_to_uploaded(self, path: Path) -> Path:
        if not path.exists():
            # Already moved by another thread
            return self._uploaded_dir / path.name
        dest = self._uploaded_dir / path.name
        if dest.exists():
            stem, suffix = path.stem, path.suffix
            counter = 1
            while dest.exists():
                dest = self._uploaded_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        try:
            path.rename(dest)
        except FileNotFoundError:
            pass  # Already moved
        return dest
