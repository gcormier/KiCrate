"""Polite cached HTTP for scrapers."""

from __future__ import annotations

import hashlib
import os
import time
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


def get(url: str, *, max_age: float = 7 * 86400) -> bytes:
    """GET with an on-disk cache (default one week) and a delay between live requests."""
    host = urllib.parse.urlsplit(url).hostname or ""
    path = cache_dir() / hashlib.sha256(url.encode()).hexdigest()
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return path.read_bytes()
    wait = max(DELAY, HOST_DELAY.get(host, 0)) - (time.monotonic() - _last.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    finally:
        _last[host] = time.monotonic()
    path.write_bytes(body)
    return body
