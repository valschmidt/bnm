import json
import xml.dom.minidom
from pathlib import Path

from bnm.build import build_site
from bnm.normalize import is_expired, normalize_lnm
from bnm.sources import SourceResult
from bnm.sources.navcen_lnm import category_of

FIX = Path(__file__).parent / "fixtures"


def load(name):
    cat = category_of(name)
    raw = json.loads((FIX / f"{name}.geojson").read_text())["features"]
    return [normalize_lnm(r, cat) for r in raw]


def test_category_of():
    assert category_of("hazNavPoly_1") == "hazNav"
    assert category_of("autoVesselPoint_1") == "autoVessel"
    assert category_of("discFedAidCor_1") == "discFedAidCor"
    assert category_of("discFedAid_12") == "discFedAid"


def test_narrative_notice_uses_coordinates_json():
    f = load("hazNavPoly_1")[0]
    p = f["properties"]
    assert f["geometry"]["type"] == "Polygon"
    assert p["source"] == "LNM" and p["category"] == "hazNav"
    assert p["label"] == "Hazards to Navigation"
    assert p["text"] and p["title"]
    assert p["begin"].endswith("Z") and p["district"]


def test_aid_record_text():
    for f in load("discFedAid_1") + load("tmpChange_1"):
        p = f["properties"]
        assert f["geometry"]["type"] == "Point"
        assert "Aid:" in p["text"]
        assert p["llnr"] is not None


def test_advance_notices_are_kept():
    feats = load("changeAdvNot_1")
    assert feats and all(f is not None for f in feats)
    assert feats[0]["properties"]["status"]


def test_withdrawn_dropped():
    raw = json.loads((FIX / "hazNavPoly_1.geojson").read_text())["features"][0]
    raw["properties"]["STATUS"] = "Cancelled"
    assert normalize_lnm(raw, "hazNav") is None


def test_ids_unique():
    feats = [f for n in ("hazNavPoly_1", "tmpChange_1", "discFedAid_1", "change_1", "marEvent_1")
             for f in load(n)]
    ids = [f["properties"]["id"] for f in feats]
    assert len(ids) == len(set(ids))


def test_is_expired():
    f = {"properties": {"end": "2020-01-01T00:00:00Z"}}
    assert is_expired(f, "2026-01-01T00:00:00Z")
    assert not is_expired({"properties": {"end": None}}, "2026-01-01T00:00:00Z")


def _future(feats):
    for f in feats:
        f["properties"]["end"] = None
    return feats


def test_build_site_and_failure_keeps_previous(tmp_path):
    feats = _future(load("hazNavPoly_1") + load("tmpChange_1"))
    m = build_site(tmp_path, [SourceResult("lnm", features=feats)])
    assert m["count"] == len(feats)
    assert (tmp_path / "index.html").exists() and (tmp_path / "app.js").exists()
    fc = json.loads((tmp_path / "data" / "notices.geojson").read_text())
    assert len(fc["features"]) == len(feats)
    xml.dom.minidom.parse(str(tmp_path / "data" / "notices.kml"))

    # A failed run must not wipe out the previous data
    m2 = build_site(tmp_path, [SourceResult("lnm", errors=["boom"], ok=False)])
    assert m2["count"] == len(feats)
    assert m2["sources"]["lnm"]["ok"] is False
    assert m2["sources"]["lnm"]["updated"] == m["sources"]["lnm"]["updated"]
    assert m2["sources"]["lnm"]["errors"] == ["boom"]


def test_build_site_drops_expired(tmp_path):
    feats = _future(load("marEvent_1"))
    feats[0]["properties"]["end"] = "2000-01-01T00:00:00Z"
    m = build_site(tmp_path, [SourceResult("lnm", features=feats)])
    assert m["count"] == len(feats) - 1
