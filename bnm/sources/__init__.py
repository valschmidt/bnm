"""Data sources. Each returns a ``SourceResult`` of normalized features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SourceResult:
    name: str
    features: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)  # inputs served from cache
    ok: bool = True  # False if the source as a whole failed
    stats: Optional[dict] = None  # extra source-specific facts for the manifest
