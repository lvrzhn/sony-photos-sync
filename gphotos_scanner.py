"""
Google Photos library scanner.

Downloads small thumbnails from the user's Google Photos library
and computes perceptual hashes, storing them in the dedup database.
This allows the app to detect duplicates against the full library,
not just photos that went through the app.

Uses the Google Photos API with the OAuth token from rclone's config.
"""

import json
import logging
import time
from configparser import ConfigParser
from pathlib import Path
from typing import Optional

import requests

from config_manager import RCLONE_CONFIG
from dedup import DedupDB, compute_phash_from_bytes

logger = logging.getLogger("sony-sync")

GPHOTOS_API = "https://photoslibrary.googleapis.com/v1"
THUMBNAIL_SIZE = 64  # Small enough for fast download, enough for pHash
PAGE_SIZE = 100  # Max allowed by API


def _load_rclone_token() -> Optional[dict]:
    """
    Extract the Google Photos OAuth token from rclone's config file.
    Returns the parsed token dict, or None.
    """
    if not RCLONE_CONFIG.exists():
        logger.error(f"rclone config not found: {RCLONE_CONFIG}")
        return None

    config = ConfigParser()
    config.read(str(RCLONE_CONFIG))

    if "gphotos" not in config:
        logger.error("No 'gphotos' section in rclone config")
        return None

    token_str = config.get("gphotos", "token", fallback=None)
    if not token_str:
        logger.error("No token in rclone gphotos config")
        return None

    try:
        return json.loads(token_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse rclone token: {e}")
        return None


def _refresh_token(token_data: dict) -> Optional[str]:
    """
    Refresh the OAuth access token using the refresh token.
    Returns a fresh access_token, or None.
    """
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return token_data.get("access_token")

    # rclone stores client_id/secret in the config or uses defaults
    config = ConfigParser()
    config.read(str(RCLONE_CONFIG))
    client_id = config.get("gphotos", "client_id", fallback="")
    client_secret = config.get("gphotos", "client_secret", fallback="")

    # If no custom client ID, use rclone's built-in (public) credentials
    if not client_id:
        client_id = "202264815644.apps.googleusercontent.com"
        client_secret = "X4Z3ca8xfWDb1Voo-F9a7ZxJ"

    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        # Fall back to existing access token
        return token_data.get("access_token")


def _get_access_token() -> Optional[str]:
    """Get a valid access token for the Google Photos API."""
    token_data = _load_rclone_token()
    if not token_data:
        return None

    # Check if token is expired
    expiry = token_data.get("expiry", "")
    if expiry:
        try:
            from datetime import datetime
            exp_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_time.timestamp() < time.time():
                return _refresh_token(token_data)
        except Exception:
            pass

    # Try existing token first, refresh if it fails
    access_token = token_data.get("access_token")
    if access_token:
        # Quick test
        try:
            resp = requests.get(
                f"{GPHOTOS_API}/mediaItems",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"pageSize": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                return access_token
        except Exception:
            pass

    return _refresh_token(token_data)


class GPhotosScanner:
    """
    Scans the user's Google Photos library and populates the dedup database
    with perceptual hashes of all photos.
    """

    def __init__(self, db: DedupDB):
        self._db = db
        self._running = False
        self._progress = {"scanned": 0, "total": None, "status": "idle"}

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self):
        self._running = False

    def scan(self, on_progress=None):
        """
        Scan the full Google Photos library.

        on_progress: Optional callback(scanned: int, status: str)
        """
        self._running = True
        self._progress = {"scanned": 0, "total": None, "status": "starting"}

        access_token = _get_access_token()
        if not access_token:
            self._progress["status"] = "error: no access token"
            self._running = False
            return False

        headers = {"Authorization": f"Bearer {access_token}"}
        next_page_token = None
        scanned = 0
        errors = 0

        self._progress["status"] = "scanning"
        logger.info("Google Photos library scan started")

        while self._running:
            # List media items
            params = {"pageSize": PAGE_SIZE}
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                resp = requests.get(
                    f"{GPHOTOS_API}/mediaItems",
                    headers=headers,
                    params=params,
                    timeout=30,
                )

                if resp.status_code == 401:
                    # Token expired mid-scan, try refresh
                    access_token = _get_access_token()
                    if not access_token:
                        break
                    headers = {"Authorization": f"Bearer {access_token}"}
                    continue

                resp.raise_for_status()
                data = resp.json()

            except Exception as e:
                logger.error(f"API error during scan: {e}")
                errors += 1
                if errors > 5:
                    break
                time.sleep(2)
                continue

            items = data.get("mediaItems", [])
            if not items:
                break

            for item in items:
                if not self._running:
                    break

                mime = item.get("mimeType", "")
                if not mime.startswith("image/"):
                    continue

                base_url = item.get("baseUrl")
                filename = item.get("filename", "")
                if not base_url:
                    continue

                # Download tiny thumbnail
                thumb_url = f"{base_url}=w{THUMBNAIL_SIZE}-h{THUMBNAIL_SIZE}"
                try:
                    thumb_resp = requests.get(thumb_url, timeout=15)
                    if thumb_resp.status_code != 200:
                        continue

                    phash = compute_phash_from_bytes(thumb_resp.content)
                    if phash is not None:
                        # Check if already in DB to avoid duplicates in DB itself
                        is_dup, _ = self._db.is_duplicate(phash, threshold=0)
                        if not is_dup:
                            self._db.add_hash(
                                phash, filename=filename, source="gphotos"
                            )

                    scanned += 1
                    self._progress["scanned"] = scanned

                    if on_progress and scanned % 50 == 0:
                        on_progress(scanned, "scanning")

                except Exception as e:
                    logger.debug(f"Failed to hash {filename}: {e}")
                    continue

                # Rate limit: be gentle with the API
                if scanned % 20 == 0:
                    time.sleep(0.5)

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        self._running = False
        self._progress["status"] = f"done — {scanned} photos scanned"
        logger.info(f"Google Photos scan complete: {scanned} photos hashed")

        if on_progress:
            on_progress(scanned, "done")

        return True
