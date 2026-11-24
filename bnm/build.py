"""Write the static site: map page, GeoJSON, KML and manifest."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape

from .normalize import is_expired
from .sources import SourceResult

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, data: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _fc(features: List[dict]) -> str:
    return json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- KML

def _kml_coords(coords) -> str:
    return " ".join(f"{c[0]},{c[1]}" for c in coords)


def _kml_geom(g: dict) -> str:
    t, c = g["type"], g["coordinates"]
    if t == "Point":
        return f"<Point><coordinates>{c[0]},{c[1]}</coordinates></Point>"
    if t == "LineString":
        return f"<LineString><coordinates>{_kml_coords(c)}</coordinates></LineString>"
    if t == "Polygon":
        rings = [f"<outerBoundaryIs><LinearRing><coordinates>{_kml_coords(c[0])}</coordinates></LinearRing></outerBoundaryIs>"]
        rings += [f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coords(r)}</coordinates></LinearRing></innerBoundaryIs>" for r in c[1:]]
        return f"<Polygon>{''.join(rings)}</Polygon>"
    if t.startswith("Multi"):
        single = t[5:]
        return "<MultiGeometry>" + "".join(_kml_geom({"type": single, "coordinates": x}) for x in c) + "</MultiGeometry>"
    if t == "GeometryCollection":
        return "<MultiGeometry>" + "".join(_kml_geom(x) for x in g["geometries"]) + "</MultiGeometry>"
    raise ValueError(t)


def to_kml(features: List[dict], generated: str) -> str:
    by_label: Dict[str, List[dict]] = {}
    for f in features:
        p = f["properties"]
        by_label.setdefault(f"{p['source']} - {p['label']}", []).append(f)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"<name>USCG Notices to Mariners ({generated})</name>",
        '<Style id="n"><LineStyle><color>ff0000cc</color><width>2</width></LineStyle>'
        '<PolyStyle><color>440000cc</color></PolyStyle></Style>',
    ]
    for label in sorted(by_label):
        out.append(f"<Folder><name>{escape(label)}</name>")
        for f in by_label[label]:
            p = f["properties"]
            dates = " to ".join(x for x in (p.get("begin"), p.get("end")) if x)
            desc = p.get("text") or ""
            if dates:
                desc += f"\n\nEffective: {dates}"
            desc += f"\nDistrict: {p.get('district')}  Source: {p.get('url')}"
            out.append(
                f"<Placemark><name>{escape(p.get('title') or '')}</name>"
                f"<description><![CDATA[{desc.replace(']]>', ']]&gt;')}]]></description>"
                f"<styleUrl>#n</styleUrl>{_kml_geom(f['geometry'])}</Placemark>"
            )
        out.append("</Folder>")
    out.append("</Document></kml>")
    return "\n".join(out)


# ---------------------------------------------------------------- archive

def archive(db_path: Path, features: List[dict], now: str):
    """Record every notice seen, with first/last-seen times, in SQLite."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    with con:
        con.execute("""CREATE TABLE IF NOT EXISTS notices (
            id TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, feature TEXT)""")
        for f in features:
            con.execute(
                """INSERT INTO notices VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, feature=excluded.feature""",
                (f["properties"]["id"], now, now, json.dumps(f, ensure_ascii=False)),
            )
    con.close()


# ---------------------------------------------------------------- site

def build_site(out: Path, results: List[SourceResult], archive_db: Path = None) -> dict:
    """Write the site to ``out``. Returns the manifest."""
    out = Path(out)
    data_dir = out / "data"
    src_dir = data_dir / "sources"
    now = utcnow_iso()

    # Previous manifest, so a failed source keeps its last good timestamp
    prev = {}
    try:
        prev = json.loads((data_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        pass

    manifest = {"generated": now, "sources": {}}
    all_features: List[dict] = []
    for r in results:
        path = src_dir / f"{r.name}.geojson"
        entry = {"errors": r.errors, "stale_inputs": r.stale}
        if r.stats:
            entry["stats"] = r.stats
        if r.ok:
            feats = [f for f in r.features if not is_expired(f, now)]
            # De-duplicate on id, keeping the first occurrence
            seen, uniq = set(), []
            for f in feats:
                fid = f["properties"]["id"]
                if fid not in seen:
                    seen.add(fid)
                    uniq.append(f)
            atomic_write(path, _fc(uniq))
            entry.update(ok=True, updated=now, count=len(uniq))
        else:
            log.error("source %s failed; keeping previous data", r.name)
            try:
                uniq = json.loads(path.read_text())["features"]
                uniq = [f for f in uniq if not is_expired(f, now)]
            except (OSError, ValueError, KeyError):
                uniq = []
            prev_entry = prev.get("sources", {}).get(r.name, {})
            entry.update(ok=False, updated=prev_entry.get("updated"), count=len(uniq))
        manifest["sources"][r.name] = entry
        all_features.extend(uniq)

    # Sources not run this time (e.g. --only lnm) are carried over unchanged
    for name, entry in prev.get("sources", {}).items():
        if name in manifest["sources"]:
            continue
        try:
            feats = json.loads((src_dir / f"{name}.geojson").read_text())["features"]
        except (OSError, ValueError, KeyError):
            continue
        feats = [f for f in feats if not is_expired(f, now)]
        manifest["sources"][name] = dict(entry, count=len(feats))
        all_features.extend(feats)

    manifest["count"] = len(all_features)
    atomic_write(data_dir / "notices.geojson", _fc(all_features))
    atomic_write(data_dir / "notices.kml", to_kml(all_features, now))
    atomic_write(data_dir / "manifest.json", json.dumps(manifest, indent=1))

    for src in WEB_DIR.iterdir():
        if src.is_file():
            shutil.copyfile(src, out / src.name)

    if archive_db:
        archive(Path(archive_db), all_features, now)
    return manifest
