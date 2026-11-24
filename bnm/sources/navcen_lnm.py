"""Local Notice to Mariners data from the NAVCEN MSI GeoJSON files.

NAVCEN publishes the LNM database as GeoJSON files, one set per category, at
``https://navcen.uscg.gov/sites/default/files/msi/<category>[Line|Poly|Point]_<page>.geojson``.
Large categories are split into pages of 1000 features. The current file list
is linked from https://navcen.uscg.gov/msi. An empty category answers 404.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable, List, Tuple

from ..http import CachedFetcher
from ..normalize import normalize_lnm
from . import SourceResult

log = logging.getLogger(__name__)

BASE = "https://navcen.uscg.gov"
MSI_PAGE = BASE + "/msi"
FILE_URL = BASE + "/sites/default/files/msi/{}.geojson"

# Files present when this was written (Nov 2026); used if /msi can't be read.
FALLBACK_FILES = [
    "autoVesselLine_1", "autoVesselPoint_1", "autoVesselPoly_1",
    "bridge_1", "bridgeLine_1", "bridgePoly_1",
    "change_1", "changeAdvNot_1", "changeProp_1",
    "discFedAid_1", "discFedAidCor_1", "discPriAid_1", "discPriAidCor_1",
    "hazNav_1", "hazNavLine_1", "hazNavPoly_1",
    "marCon_1", "marConLine_1", "marConPoly_1",
    "marEvent_1", "marEventLine_1", "marEventPoly_1",
    "misc_1", "miscLine_1", "miscPoly_1",
    "msib_1", "msibLine_1", "msibPoly_1",
    "navalAct_1", "navalActLine_1", "navalActPoly_1",
    "safeZone_1", "safeZoneLine_1", "safeZonePoly_1",
    "spaceOpsPoly_1",
    "tessAdv_1", "tessAdvLine_1", "tessAdvPoly_1",
    "tmpChange_1", "tmpChangeCor_1",
]

# lightList* and buoys* are the full Light List (permanent aids), not notices.
EXCLUDE = re.compile(r"^(lightList|buoys)")
FILE_RE = re.compile(r"/sites/default/files/msi/([A-Za-z]+_\d+)\.geojson")
NAME_RE = re.compile(r"^(?P<base>[A-Za-z]+?)(?:Line|Poly|Point)?_(?P<page>\d+)$")


def category_of(file_name: str) -> str:
    m = NAME_RE.match(file_name)
    if not m:
        raise ValueError(f"unexpected NAVCEN file name: {file_name}")
    return m.group("base")


def discover_files(fetcher: CachedFetcher) -> Tuple[List[str], List[str]]:
    """Return (file names, errors). Falls back to a built-in list."""
    try:
        res = fetcher.get(MSI_PAGE)
        names = sorted({n for n in FILE_RE.findall(res.content.decode("utf-8", "replace"))
                        if not EXCLUDE.match(n)})
        if names:
            return names, []
        err = "no GeoJSON links found on " + MSI_PAGE
    except Exception as exc:
        err = f"could not read {MSI_PAGE}: {exc}"
    log.warning("%s; using built-in file list", err)
    return list(FALLBACK_FILES), [err]


def _validate(content: bytes):
    data = json.loads(content)
    if data.get("type") != "FeatureCollection":
        raise ValueError("not a GeoJSON FeatureCollection")


def _next_page(name: str) -> str:
    m = NAME_RE.match(name)
    stem = name[: name.rindex("_")]
    return f"{stem}_{int(m.group('page')) + 1}"


def fetch(fetcher: CachedFetcher, districts: Iterable[int] = None) -> SourceResult:
    result = SourceResult("lnm")
    names, errors = discover_files(fetcher)
    result.errors.extend(errors)
    wanted = set(districts) if districts else None

    queue = list(names)
    seen = set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        url = FILE_URL.format(name)
        try:
            res = fetcher.get(url, validate=_validate)
        except Exception as exc:
            result.errors.append(f"{name}: {exc}")
            continue
        if res.missing:
            continue
        if res.stale:
            result.stale.append(name)
            result.errors.append(f"{name}: {res.error} (using cached copy)")
        feats = json.loads(res.content).get("features", [])
        # Full pages hold 1000 features; make sure the next page is fetched
        # even if /msi did not list it.
        if len(feats) >= 1000:
            queue.append(_next_page(name))
        cat = category_of(name)
        for raw in feats:
            f = normalize_lnm(raw, cat)
            if f is None:
                continue
            if wanted and f["properties"].get("district") not in wanted:
                continue
            result.features.append(f)
        log.info("%-20s %5d features", name, len(feats))

    if not result.features and len(result.errors) >= len(seen):
        result.ok = False
    return result
