"""HTTP helper with a conditional-GET disk cache.

Every successful download is kept in the cache. When NAVCEN is unreachable or
returns garbage, the last good copy is used instead and the caller is told the
data is stale, so a bad run never blanks the map.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    content: bytes
    stale: bool = False  # True if served from cache after a failed fetch
    error: Optional[str] = None
    missing: bool = False  # True if the server answered 404 (e.g. empty NAVCEN category)


class CachedFetcher:
    def __init__(self, cache_dir: Path, user_agent: str, timeout: float = 60.0, delay: float = 0.5):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last = 0.0

    def _paths(self, url: str):
        key = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.meta"

    def _throttle(self):
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, validate=None) -> FetchResult:
        """Fetch ``url``. ``validate(bytes)`` may raise to reject a response."""
        body_path, meta_path = self._paths(url)
        meta = {}
        if meta_path.exists() and body_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except ValueError:
                meta = {}
        headers = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

        self._throttle()
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 304:
                log.debug("not modified: %s", url)
                return FetchResult(body_path.read_bytes())
            if resp.status_code == 404:
                body_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                return FetchResult(b"", missing=True)
            resp.raise_for_status()
            content = resp.content
            if validate:
                validate(content)
        except Exception as exc:  # network error, HTTP error or validation failure
            msg = f"{type(exc).__name__}: {exc}"
            if body_path.exists():
                log.warning("fetch failed, using cached copy: %s (%s)", url, msg)
                return FetchResult(body_path.read_bytes(), stale=True, error=msg)
            raise

        body_path.write_bytes(content)
        meta_path.write_text(json.dumps({
            "url": url,
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "fetched": time.time(),
        }))
        return FetchResult(content)
