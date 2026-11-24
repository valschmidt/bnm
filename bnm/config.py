"""Runtime settings, read from an optional TOML file and overridden by CLI flags."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

ALL_DISTRICTS = [1, 5, 7, 8, 9, 11, 13, 14, 17]


@dataclass
class Config:
    out: Path = Path("site")
    cache_dir: Path = Path.home() / ".cache" / "bnm"
    # "lnm" = NAVCEN GeoJSON; "bnm" = Broadcast Notices from a subscribed mailbox
    sources: List[str] = field(default_factory=lambda: ["lnm"])
    districts: List[int] = field(default_factory=lambda: list(ALL_DISTRICTS))
    request_delay: float = 0.5  # seconds between requests, to be polite to NAVCEN
    timeout: float = 60.0
    user_agent: str = "bnm-map/0.1 (+notice-to-mariners map)"
    rsync: Optional[str] = None
    archive: Optional[Path] = None

    # --- BNM mailbox (GovDelivery BNM email subscriptions) ---
    # Either IMAP ...
    imap_host: Optional[str] = None
    imap_port: int = 993
    imap_user: Optional[str] = None
    imap_password_file: Optional[Path] = None  # or env BNM_IMAP_PASSWORD
    imap_folder: str = "INBOX"
    # ... or a local Maildir / mbox the mail is delivered to
    mail_path: Optional[Path] = None
    mail_from: str = "govdelivery"  # only messages whose From contains this
    bnm_max_age_days: int = 30  # BNMs with no cancel time expire after this

    @classmethod
    def load(cls, path: Optional[Path]) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                raise ValueError(f"Unknown config key in {path}: {key}")
            if key in ("out", "cache_dir", "archive", "imap_password_file", "mail_path") and value is not None:
                value = Path(value).expanduser()
            setattr(cfg, key, value)
        return cfg
