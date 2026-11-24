"""BNM email parsing. The messages here are synthetic examples in the usual
USCG BNM style; replace or extend them with real GovDelivery messages once the
lab mailbox is subscribed."""

import mailbox
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from bnm.sources.email_bnm import (
    build_features, local_messages, message_text, parse_dtg, parse_message,
)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def mk(subject, body, date="Mon, 23 Nov 2026 10:00:00 +0000", html=False,
       sender="USCG Navigation Center <USDHSCG@public.govdelivery.com>"):
    m = EmailMessage()
    m["From"] = sender
    m["Subject"] = subject
    m["Date"] = date
    m.set_content(body, subtype="html" if html else "plain")
    return m


BUOY = mk(
    "D1 BNM 0123-26 Portsmouth Harbor Buoy 5 off station",
    """SECTOR NORTHERN NEW ENGLAND BNM 0123-26
NH-PORTSMOUTH HARBOR-PISCATAQUA RIVER: Portsmouth Harbor Lighted Buoy 5 (LLNR 1234)
is reported off station in approximate position 43-04.12N 070-41.24W.
Mariners are advised to use caution while transiting the area.
CANCEL AT//301200Z NOV 26//

You are subscribed to Broadcast Notice to Mariners for U.S. Coast Guard.
Update your subscriptions ...""")

ZONE = mk(
    "BNM 0124-26 Safety zone, fireworks",
    """BNM 0124-26
A safety zone is established within 500 yards of 42-21.30N 071-03.00W for a fireworks
display. Cancel this message at 240400Z NOV 26.""")

AREA = mk(
    "BNM 0125-26 Dredging operations",
    """BNM 0125-26
Dredging operations are under way in the area bounded by 41-30.0N 070-45.0W,
41-31.0N 070-45.0W, 41-31.0N 070-44.0W and 41-30.0N 070-44.0W.""")

CANCEL = mk(
    "BNM 0130-26 Buoy 5 restored",
    """BNM 0130-26
Portsmouth Harbor Lighted Buoy 5 (LLNR 1234) has been reset on station.
CANCEL BNM 0123-26.""", date="Mon, 23 Nov 2026 11:00:00 +0000")

NOPOS = mk("BNM 0126-26 VHF outage", "BNM 0126-26\nVHF-FM channel 16 coverage degraded in Sector Boston.")


def test_parse_dtg():
    assert parse_dtg("30", "12", "00", "NOV", "26") == datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
    assert parse_dtg("31", "12", "00", "NOV", "26") is None


def test_footer_is_removed():
    text = message_text(BUOY)
    assert "subscribed" not in text and text.endswith("CANCEL AT//301200Z NOV 26//")


def test_point_notice():
    p = parse_message(BUOY)
    f = p["feature"]
    assert p["number"] == "0123-26"
    assert f["geometry"] == {"type": "Point", "coordinates": [-70.687333, 43.068667]}
    props = f["properties"]
    assert props["source"] == "BNM" and props["district"] == 1
    assert props["end"] == "2026-11-30T12:00:00Z"
    assert props["begin"] == "2026-11-23T10:00:00Z"
    assert "LLNR 1234" in props["text"]


def test_radius_and_cancel_time():
    f = parse_message(ZONE)["feature"]
    assert f["geometry"]["type"] == "Polygon"
    assert f["properties"]["end"] == "2026-11-24T04:00:00Z"


def test_bounded_area():
    f = parse_message(AREA)["feature"]
    assert f["geometry"]["type"] == "Polygon"
    assert len(f["geometry"]["coordinates"][0]) == 5


def test_default_expiry():
    f = parse_message(AREA, max_age_days=10)["feature"]
    assert f["properties"]["end"] == "2026-12-03T10:00:00Z"


def test_html_body():
    m = mk("BNM 0127-26 Wreck", "<p>BNM 0127-26</p><p>Wreck reported in 41-20.5N&nbsp;071-10.2W.</p>", html=True)
    f = parse_message(m)["feature"]
    assert f["geometry"]["coordinates"] == [-71.17, 41.341667]


def test_build_features_cancels_and_unlocated():
    feats, stats = build_features([BUOY, ZONE, AREA, CANCEL, NOPOS], 30, NOW, "govdelivery")
    nums = sorted(f["properties"]["bnm"] for f in feats)
    # 0123 cancelled by 0130; 0126 has no position; 0130 itself has no position
    assert nums == ["0124-26", "0125-26"]
    assert stats["messages"] == 5
    assert "BNM 0126-26 VHF outage" in stats["unlocated"]


def test_sender_filter():
    other = mk("BNM 0999-26 spoof", "41-30N 070-45W", sender="someone@example.com")
    feats, stats = build_features([other], 30, NOW, "govdelivery")
    assert feats == [] and stats["messages"] == 0


def test_later_message_replaces_earlier():
    v2 = mk("BNM 0125-26 Dredging operations UPDATE",
            "BNM 0125-26\nDredge now at 41-32.0N 070-43.0W.", date="Mon, 23 Nov 2026 11:30:00 +0000")
    feats, _ = build_features([AREA, v2], 30, NOW)
    assert len(feats) == 1 and feats[0]["geometry"]["type"] == "Point"


@pytest.mark.parametrize("kind", ["mbox", "maildir"])
def test_local_mailboxes(tmp_path, kind):
    path = tmp_path / kind
    box = mailbox.mbox(str(path)) if kind == "mbox" else mailbox.Maildir(str(path))
    for m in (BUOY, ZONE):
        box.add(m)
    box.flush()
    msgs = list(local_messages(path))
    assert sorted(str(m["Subject"]) for m in msgs) == sorted(str(m["Subject"]) for m in (BUOY, ZONE))
