"""Extract positions and geometries from free-text notices.

Handles the position styles that appear in USCG notices, e.g.::

    39-47-30.772N 074-09-05.081W      degrees-minutes-seconds with dashes
    41-30.123N, 070-45.678W           degrees-decimal minutes
    45-34' 57.36"N, 122-09'30.69"W    mixed dash / quote marks
    41° 30.1' N 070° 45.6' W          degree symbols
    41 30 12 N 070 45 36 W            spaces only
    41.5021N 070.7613W                decimal degrees
"""

from __future__ import annotations

import math
import re
from typing import List, Optional, Tuple

_DEG = r"[°º˚]"
_MIN = r"['′’]"
_SEC = r"(?:\"|″|''|”)"
_SEP = rf"(?:\s*{_DEG}\s*|\s*-\s*|\s+)"


def _coord(name: str, deg_digits: str, hemis: str) -> str:
    return (
        rf"(?P<{name}_d>\d{{{deg_digits}}}(?:\.\d+)?)"
        rf"(?:{_SEP}(?P<{name}_m>\d{{1,2}}(?:\.\d+)?)\s*{_MIN}?"
        rf"(?:(?:\s*-\s*|\s*)(?P<{name}_s>\d{{1,2}}(?:\.\d+)?)\s*{_SEC}?)?)?"
        rf"\s*{_DEG}?\s*(?P<{name}_h>[{hemis}])(?![A-Za-z])"
    )


PAIR_RE = re.compile(
    r"(?<![\d.])" + _coord("lat", "1,2", "NS") + r"\s*(?:,|/|and)?\s*" + _coord("lon", "1,3", "EW"),
)


def _to_decimal(d: str, m: Optional[str], s: Optional[str], h: str) -> float:
    deg, mins, secs = float(d), float(m or 0), float(s or 0)
    if ("." in d and (m or s)) or mins >= 60 or secs >= 60:
        raise ValueError("bad sexagesimal value")
    val = deg + mins / 60 + secs / 3600
    return -val if h in "SW" else val


def find_positions(text: str) -> List[Tuple[float, float]]:
    """Return [(lon, lat), ...] for every lat/lon pair found, in order."""
    out = []
    for m in PAIR_RE.finditer(text or ""):
        g = m.groupdict()
        try:
            lat = _to_decimal(g["lat_d"], g["lat_m"], g["lat_s"], g["lat_h"])
            lon = _to_decimal(g["lon_d"], g["lon_m"], g["lon_s"], g["lon_h"])
        except ValueError:
            continue
        if abs(lat) <= 90 and abs(lon) <= 180:
            out.append((round(lon, 6), round(lat, 6)))
    return out


def parse_position(text: str) -> Optional[Tuple[float, float]]:
    """Return the first (lon, lat) in ``text`` or None."""
    pos = find_positions(text)
    return pos[0] if pos else None


_AREA_RE = re.compile(
    r"\b(bounded|enclosed|encompass|polygon|area (?:within|defined|described)|following (?:points|positions|coordinates)|connecting)\b",
    re.I,
)
_TRACK_RE = re.compile(r"\b(track|route|transit(?:ing)?|line (?:between|from|connecting)|cable|pipeline)\b", re.I)
_RADIUS_RE = re.compile(
    r"(?:within|radius of|a distance of)\s+(?:a\s+)?(?:radius of\s+)?(?:approximately\s+)?"
    r"(?P<n>\d+(?:,\d{3})*(?:\.\d+)?)\s*-?\s*"
    r"(?P<u>yards?|yds?|feet|foot|ft|nautical miles?|nm|NM|meters?|metres?|m|miles?|statute miles?)\b",
    re.I,
)
_UNIT_M = {"y": 0.9144, "f": 0.3048, "n": 1852.0, "m": 1.0, "mi": 1609.344, "s": 1609.344}


def _unit_to_m(u: str) -> float:
    u = u.lower()
    if u.startswith("nautical") or u == "nm":
        return _UNIT_M["n"]
    if u.startswith("mile") or u.startswith("statute"):
        return _UNIT_M["mi"]
    if u.startswith("y"):
        return _UNIT_M["y"]
    if u.startswith("f"):
        return _UNIT_M["f"]
    return _UNIT_M["m"]


def circle(lon: float, lat: float, radius_m: float, n: int = 48) -> dict:
    """Approximate a circle as a GeoJSON Polygon (fine for small radii)."""
    dlat = radius_m / 111_320.0
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    ring = [
        [round(lon + dlon * math.cos(2 * math.pi * i / n), 6),
         round(lat + dlat * math.sin(2 * math.pi * i / n), 6)]
        for i in range(n)
    ]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def geometry_from_text(text: str) -> Optional[dict]:
    """Best-effort geometry from notice text, or None if no positions found."""
    pts = find_positions(text)
    # Drop consecutive duplicates (positions are often repeated in notices)
    dedup = []
    for p in pts:
        if not dedup or dedup[-1] != p:
            dedup.append(p)
    pts = dedup
    if not pts:
        return None
    if len(pts) == 1:
        lon, lat = pts[0]
        r = _RADIUS_RE.search(text)
        if r:
            meters = float(r.group("n").replace(",", "")) * _unit_to_m(r.group("u"))
            if 0 < meters <= 100_000:
                return circle(lon, lat, meters)
        return {"type": "Point", "coordinates": [lon, lat]}
    closed = len(pts) >= 4 and pts[0] == pts[-1]
    if len(pts) >= 3 and (closed or (_AREA_RE.search(text) and not _TRACK_RE.search(text))):
        ring = [list(p) for p in pts]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return {"type": "Polygon", "coordinates": [ring]}
    if _TRACK_RE.search(text) or _AREA_RE.search(text):
        return {"type": "LineString", "coordinates": [list(p) for p in pts]}
    return {"type": "MultiPoint", "coordinates": [list(p) for p in pts]}
