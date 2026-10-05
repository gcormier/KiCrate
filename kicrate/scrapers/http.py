"""Polite cached HTTP for scrapers."""

from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "KiCrate/0.1 (+https://github.com/gcormier/KiCrate; enclosure PCB outline generator)"
DELAY = float(os.environ.get("KICRATE_HTTP_DELAY", "1.0"))
HOST_DELAY = {"www.budind.com": 10.0}  # robots.txt Crawl-delay
_last: dict[str, float] = {}


def cache_dir() -> Path:
    d = Path(os.environ.get("KICRATE_CACHE", Path.home() / ".cache" / "kicrate")) / "http"
    d.mkdir(parents=True, exist_ok=True)
    return d


RETRIES = int(os.environ.get("KICRATE_HTTP_RETRIES", "3"))
BACKOFF = float(os.environ.get("KICRATE_HTTP_BACKOFF", "15"))  # seconds, doubled each retry


def _transient(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code == 429
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError))


def get(url: str, *, max_age: float = 7 * 86400) -> bytes:
    """GET with an on-disk cache (default one week), a per-host delay between live requests, and
    retries with backoff on transient failures (5xx, 429, timeouts) so a flaky server doesn't
    silently drop a product from a run."""
    host = urllib.parse.urlsplit(url).hostname or ""
    path = cache_dir() / hashlib.sha256(url.encode()).hexdigest()
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(RETRIES + 1):
        wait = max(DELAY, HOST_DELAY.get(host, 0)) - (time.monotonic() - _last.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            break
        except Exception as e:
            if attempt == RETRIES or not _transient(e):
                raise
            time.sleep(BACKOFF * 2**attempt)
        finally:
            _last[host] = time.monotonic()
    path.write_bytes(body)
    return body
