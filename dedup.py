"""
Perceptual hashing & deduplication for Sony Photos Sync.

Uses pHash (perceptual hash) to fingerprint images based on visual content,
not filename or metadata. Two photos of the same scene will produce the same
hash even if filenames, JPEG quality, or EXIF data differ.

Hashes are stored in a SQLite database alongside the config.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from config_manager import APP_SUPPORT_DIR

logger = logging.getLogger("sony-sync")

DB_PATH = APP_SUPPORT_DIR / "dedup.db"
HASH_SIZE = 8  # 8x8 = 64-bit hash
HAMMING_THRESHOLD = 5  # Max bit difference to consider "same" image


def _compute_phash(image_path: str) -> Optional[int]:
    """
    Compute a perceptual hash (pHash) for an image.

    Algorithm:
    1. Resize to 32x32 (for DCT-like frequency analysis via downscale)
    2. Convert to grayscale
    3. Downscale to 8x8
    4. Compute average pixel value
    5. Each bit = 1 if pixel > average, else 0
    Result: 64-bit integer fingerprint

    Returns None if the image can't be processed.
    """
    try:
        with Image.open(image_path) as img:
            # Convert to grayscale and resize to small square
            img = img.convert("L").resize(
                (HASH_SIZE, HASH_SIZE), Image.LANCZOS
            )
            pixels = list(img.getdata())
            avg = sum(pixels) / len(pixels)
            # Build hash: 1 bit per pixel
            bits = 0
            for px in pixels:
                bits = (bits << 1) | (1 if px > avg else 0)
            return bits
    except Exception as e:
        logger.warning(f"Could not hash {image_path}: {e}")
        return None


def compute_phash_from_bytes(data: bytes) -> Optional[int]:
    """Compute pHash from raw image bytes (for thumbnails)."""
    try:
        import io
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("L").resize(
                (HASH_SIZE, HASH_SIZE), Image.LANCZOS
            )
            pixels = list(img.getdata())
            avg = sum(pixels) / len(pixels)
            bits = 0
            for px in pixels:
                bits = (bits << 1) | (1 if px > avg else 0)
            return bits
    except Exception as e:
        logger.warning(f"Could not hash image bytes: {e}")
        return None


def _hamming_distance(h1: int, h2: int) -> int:
    """Count differing bits between two hashes."""
    return bin(h1 ^ h2).count("1")


class DedupDB:
    """
    SQLite-backed dedup database.

    Stores pHash values with source info (local upload vs Google Photos scan).
    Thread-safe via a per-instance lock.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phash INTEGER NOT NULL,
                    filename TEXT,
                    source TEXT NOT NULL,
                    added_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_phash ON hashes(phash)
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), timeout=10)

    def add_hash(self, phash: int, filename: str = "", source: str = "local"):
        """Store a hash in the database."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO hashes (phash, filename, source, added_at) VALUES (?, ?, ?, ?)",
                    (phash, filename, source, time.time()),
                )

    def is_duplicate(self, phash: int, threshold: int = HAMMING_THRESHOLD) -> Tuple[bool, Optional[str]]:
        """
        Check if a hash matches any existing entry.

        Returns (is_dup, matching_filename).
        Uses exact match first (fast), then hamming distance scan if needed.
        """
        with self._lock:
            with self._connect() as conn:
                # Fast path: exact match
                row = conn.execute(
                    "SELECT filename FROM hashes WHERE phash = ? LIMIT 1",
                    (phash,),
                ).fetchone()
                if row:
                    return True, row[0]

                if threshold == 0:
                    return False, None

                # Slow path: hamming distance check
                rows = conn.execute("SELECT phash, filename FROM hashes").fetchall()
                for stored_hash, filename in rows:
                    if _hamming_distance(phash, stored_hash) <= threshold:
                        return True, filename

                return False, None

    def count(self, source: Optional[str] = None) -> int:
        """Count entries, optionally filtered by source."""
        with self._lock:
            with self._connect() as conn:
                if source:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM hashes WHERE source = ?", (source,)
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM hashes").fetchone()
                return row[0]

    def clear(self, source: Optional[str] = None):
        """Clear entries, optionally filtered by source."""
        with self._lock:
            with self._connect() as conn:
                if source:
                    conn.execute("DELETE FROM hashes WHERE source = ?", (source,))
                else:
                    conn.execute("DELETE FROM hashes")


def check_and_record(image_path: str, db: DedupDB) -> Tuple[bool, Optional[str]]:
    """
    Convenience: compute pHash, check for duplicate, record if new.

    Returns (is_duplicate, matching_filename).
    """
    phash = _compute_phash(image_path)
    if phash is None:
        return False, None  # Can't hash → allow upload

    is_dup, match = db.is_duplicate(phash)
    if is_dup:
        return True, match

    # New image — record it
    filename = Path(image_path).name
    db.add_hash(phash, filename=filename, source="local")
    return False, None
