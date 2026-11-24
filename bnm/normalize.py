"""Map source-specific records onto one feature schema.

Every output feature is a GeoJSON Feature whose properties are:

    id        stable unique id (source:category:uid:geomtype)
    source    "LNM" | "BNM" | "NGA"
    category  short key, e.g. "hazNav"
    label     human-readable category
    district  USCG district number (int) or None
    title     one-line summary
    text      full notice text
    begin, end, updated   ISO-8601 UTC strings or None
    waterway  waterway name or None
    status    discrepancy / change status, if any
    bnm       referenced BNM number(s), if any
    llnr      Light List number, if any
    url       link to the originating page
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

MSI_URL = "https://www.navcen.uscg.gov/msi"

CATEGORY_LABELS = {
    "autoVessel": "Autonomous Vessel Operations",
    "bridge": "Bridges",
    "change": "ATON Changes",
    "changeAdvNot": "ATON Changes - Advance Notice",
    "changeProp": "ATON Changes - Proposed",
    "discFedAid": "Discrepancies - Federal Aids",
    "discFedAidCor": "Discrepancies Corrected - Federal Aids",
    "discPriAid": "Discrepancies - Private Aids",
    "discPriAidCor": "Discrepancies Corrected - Private Aids",
    "hazNav": "Hazards to Navigation",
    "marCon": "Marine Construction",
    "marEvent": "Marine Events",
    "misc": "Miscellaneous",
    "msib": "Marine Safety Information Bulletins",
    "navalAct": "Naval Activity",
    "safeZone": "Safety & Security Zones",
    "spaceOps": "Space Operations",
    "tessAdv": "TESS Advisories",
    "tmpChange": "Temporary Changes",
    "tmpChangeCor": "Temporary Changes Corrected",
}

AID_TYPES = {"FD": "Federal aid", "PV": "Private aid"}

WITHDRAWN = {"draft", "pending", "rejected", "deleted", "cancelled", "canceled", "withdrawn"}


def iso_ms(ms) -> Optional[str]:
    """Epoch milliseconds -> ISO-8601 UTC string."""
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def _geometry(raw: dict) -> Optional[dict]:
    geom = raw.get("geometry")
    if geom and geom.get("coordinates"):
        return geom
    p = raw.get("properties") or {}
    cj = p.get("COORDINATES_JSON")
    if cj:
        try:
            geom = json.loads(cj)
            if geom.get("coordinates"):
                return geom
        except (ValueError, AttributeError):
            pass
    lat, lon = p.get("DECIMAL_LATITUDE"), p.get("DECIMAL_LONGITUDE")
    if lat is not None and lon is not None:
        return {"type": "Point", "coordinates": [lon, lat]}
    return None


def _clean(s) -> Optional[str]:
    if s is None:
        return None
    s = str(s).replace("\r\n", "\n").strip()
    return s or None


def make_feature(geometry: dict, **props) -> dict:
    base = dict.fromkeys(
        ["id", "source", "category", "label", "district", "title", "text", "begin", "end",
         "updated", "waterway", "status", "bnm", "llnr", "url"])
    base.update(props)
    return {"type": "Feature", "geometry": geometry, "properties": base}


def normalize_lnm(raw: dict, category: str) -> Optional[dict]:
    p = raw.get("properties") or {}
    geom = _geometry(raw)
    if geom is None:
        log.debug("no geometry for %s record %s", category, p.get("ESRI_OID"))
        return None
    # Published values seen: Approved, Advanced Notice, Proposed (and null).
    status = (p.get("STATUS") or p.get("MSI_STATUS") or "").lower()
    if status in WITHDRAWN:
        return None

    uid = p.get("MSI_UID") or p.get("LNM_UID") or p.get("ESRI_OID") or raw.get("id")
    common = dict(
        id=f"lnm:{category}:{uid}:{geom['type']}",
        source="LNM",
        category=category,
        label=CATEGORY_LABELS.get(category, category),
        district=p.get("ATU"),
        waterway=_clean(p.get("WATERWAY_NAME")),
        url=MSI_URL,
    )

    if "DESCRIPTION" in p or "TITLE" in p:
        # Narrative notice (hazards, events, construction, ATON changes ...)
        title = _clean(p.get("TITLE")) or common["label"]
        llnr = p.get("PRIMARY_LIGHT_LIST_NUMBER")
        if p.get("AID_NAME"):
            title = f"{p['AID_NAME']} - {title}"
        return make_feature(
            geom,
            title=title,
            text=_clean(p.get("DESCRIPTION")) or "",
            begin=iso_ms(p.get("BEGIN_DATE")),
            end=iso_ms(p.get("END_DATE")),
            updated=iso_ms(p.get("MODIFIED_DATE") or p.get("CREATE_DATE")),
            status=_clean(p.get("TYPE")) or (None if status == "approved" else _clean(p.get("STATUS"))),
            llnr=llnr,
            **common,
        )

    # Aid-to-navigation record (discrepancies, temporary changes)
    name = _clean(p.get("NAME")) or "Unnamed aid"
    aid_status = _clean(p.get("DISCREP_STATUS") or p.get("TC_STATUS"))
    corr = _clean(p.get("DISCREP_CORR_STATUS") or p.get("TC_CORR_STATUS"))
    llnr = p.get("LLNR")
    lines = [f"{p.get('ATON_GROUP') or common['label']}"]
    lines.append(f"Aid: {name}" + (f" (LLNR {llnr})" if llnr else ""))
    if aid_status:
        lines.append(f"Status: {aid_status}")
    if corr:
        lines.append(f"Correction: {corr}")
    if p.get("CORRECTION_DATE"):
        lines.append(f"Corrected: {iso_ms(p['CORRECTION_DATE'])}")
    if p.get("ASSIGNED_LATITUDE"):
        lines.append(f"Assigned position: {p['ASSIGNED_LATITUDE']} {p.get('ASSIGNED_LONGITUDE', '')}")
    if p.get("AID_TYPE") in AID_TYPES:
        lines.append(f"Type: {AID_TYPES[p['AID_TYPE']]}")
    if p.get("BNM_NUM"):
        lines.append(f"BNM: {p['BNM_NUM']}")
    return make_feature(
        geom,
        title=f"{name}: {aid_status}" if aid_status else name,
        text="\n".join(lines),
        begin=iso_ms(p.get("CREATE_DATE")),
        updated=iso_ms(p.get("CORRECTION_DATE") or p.get("CREATE_DATE")),
        status=aid_status,
        bnm=_clean(p.get("BNM_NUM")),
        llnr=llnr,
        **common,
    )


def is_expired(feature: dict, now_iso: str) -> bool:
    end = feature["properties"].get("end")
    return bool(end) and end < now_iso
