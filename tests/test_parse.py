import pytest

from bnm.parse import find_positions, geometry_from_text, parse_position


@pytest.mark.parametrize("text, lon, lat", [
    ("39-47-30.772N 074-09-05.081W", -74.151411, 39.791881),
    ("41-30.123N, 070-45.678W", -70.7613, 41.50205),
    ("Location 45-34' 57.36\"N, 122-09'30.69\"W. ", -122.158525, 45.5826),
    ("41° 30.1' N 070° 45.6' W", -70.76, 41.501667),
    ("41 30 12 N 070 45 36 W", -70.76, 41.503333),
    ("41.5021N 070.7613W", -70.7613, 41.5021),
    ("41-30N 070-45W", -70.75, 41.5),
    ("12-30.0S 150-15.0E", 150.25, -12.5),
])
def test_parse_position_formats(text, lon, lat):
    got = parse_position(text)
    assert got == pytest.approx((lon, lat), abs=1e-5)


@pytest.mark.parametrize("text", [
    "Buoy 7 (LLNR 1288) depth 4 ft",
    "between 1 and 2 N",
    "41-75.0N 070-45.0W",  # minutes out of range
    "",
])
def test_no_false_positives(text):
    assert find_positions(text) == []


def test_multiple_positions_in_order():
    text = "in position 42-21.5N 070-58.2W and 42-22.0N 070-57.0W"
    assert find_positions(text) == [(-70.97, 42.358333), (-70.95, 42.366667)]


def test_single_point():
    assert geometry_from_text("Wreck reported in 41-30.0N 070-45.0W") == {
        "type": "Point", "coordinates": [-70.75, 41.5]}


def test_radius_becomes_circle():
    g = geometry_from_text("Safety zone within 500 yards of 41-30.0N 070-45.0W")
    assert g["type"] == "Polygon"
    ring = g["coordinates"][0]
    assert ring[0] == ring[-1]
    # 500 yd = 457 m ~= 0.0041 deg latitude
    lats = [p[1] for p in ring]
    assert max(lats) - 41.5 == pytest.approx(457.2 / 111320, rel=0.01)


def test_bounded_area_is_closed_polygon():
    g = geometry_from_text(
        "The area bounded by 41-30N 070-45W, 41-31N 070-45W, 41-31N 070-44W, 41-30N 070-44W.")
    assert g["type"] == "Polygon"
    ring = g["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]


def test_route_is_linestring():
    g = geometry_from_text("Cable laying along a route from 41-30N 070-45W to 41-35N 070-40W")
    assert g["type"] == "LineString"


def test_unrelated_points_are_multipoint():
    g = geometry_from_text("Buoys at 41-30N 070-45W and 41-35N 070-40W are off station.")
    assert g["type"] == "MultiPoint"


def test_no_positions():
    assert geometry_from_text("No positions here") is None
