"""gis/hotspot_analyzer.py — 方位角、下風處判斷、受影響縣市。

推播「要發給誰」完全取決於這裡的幾何計算，算錯就是漏警報或誤擾民，
因此邊界條件（正北、跨 0/360 度、無風）都要釘住。
"""

import pytest

from gis.hotspot_analyzer import (
    CALM_WIND_THRESHOLD,
    COUNTY_CENTROIDS,
    _bearing,
    _haversine,
    check_downwind,
    get_affected_counties,
)


# ── _bearing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "lat2, lng2, expected",
    [
        (26.0, 121.5, 0.0),     # 正北
        (25.0, 122.5, 90.0),    # 正東
        (24.0, 121.5, 180.0),   # 正南
        (25.0, 120.5, 270.0),   # 正西
    ],
)
def test_bearing_cardinal_directions(lat2, lng2, expected):
    brg = _bearing(25.0, 121.5, lat2, lng2)
    assert brg == pytest.approx(expected, abs=1.0)


def test_bearing_is_always_in_0_360_range():
    for lat in (22.0, 24.0, 25.5):
        for lng in (119.5, 121.0, 122.0):
            brg = _bearing(24.0, 121.0, lat, lng)
            assert 0.0 <= brg < 360.0


def test_bearing_northeast_is_between_north_and_east():
    brg = _bearing(25.0, 121.5, 25.5, 122.0)
    assert 0.0 < brg < 90.0


# ── get_affected_counties ────────────────────────────────────────────────────

def _taipei():
    return COUNTY_CENTROIDS["台北市"]


def test_affected_counties_returns_names_present_in_centroid_table():
    lat, lng = _taipei()
    affected = get_affected_counties(lat, lng, wind_speed=5.0, wind_direction=0.0)
    assert set(affected) <= set(COUNTY_CENTROIDS)


def test_calm_wind_uses_radius_not_direction():
    """無風時改用半徑判定，且結果與風向無關。"""
    lat, lng = _taipei()
    calm = CALM_WIND_THRESHOLD - 0.1
    a = get_affected_counties(lat, lng, wind_speed=calm, wind_direction=0.0)
    b = get_affected_counties(lat, lng, wind_speed=calm, wind_direction=270.0)
    assert a == b
    assert "台北市" in a


def test_calm_wind_radius_is_honoured():
    lat, lng = _taipei()
    tight = get_affected_counties(lat, lng, 0.0, 0.0, calm_radius_km=1.0)
    wide = get_affected_counties(lat, lng, 0.0, 0.0, calm_radius_km=200.0)
    assert tight == ["台北市"]
    assert len(wide) > len(tight)


def test_wind_from_north_affects_counties_to_the_south():
    """風向 0 度代表「風從北方吹來」，污染往南擴散。

    以台北為事件點、北風的情況下，基隆（東北方）不該被列入，
    新北／桃園一帶（南方）才是下風處。
    """
    lat, lng = _taipei()
    affected = get_affected_counties(lat, lng, wind_speed=5.0, wind_direction=0.0)
    assert "基隆市" not in affected


def test_wind_direction_reversal_changes_the_affected_set():
    lat, lng = _taipei()
    north = set(get_affected_counties(lat, lng, 5.0, 0.0))
    south = set(get_affected_counties(lat, lng, 5.0, 180.0))
    assert north != south


def test_affected_counties_is_empty_for_far_offshore_event_with_wind():
    """遠離台灣本島的事件點不應誤判任何縣市受影響。"""
    affected = get_affected_counties(10.0, 100.0, wind_speed=5.0, wind_direction=0.0)
    assert affected == []


# ── check_downwind ───────────────────────────────────────────────────────────

def _hotspot(lat, lng, **extra):
    hs = {"lat": lat, "lng": lng, "count": 3, "intensity": 0.9, "dominant_type": "fire"}
    hs.update(extra)
    return hs


def test_check_downwind_returns_empty_when_direction_is_none():
    assert check_downwind(25.0, 121.5, None, [_hotspot(25.1, 121.5)], wind_speed=5.0) == []


def test_check_downwind_returns_empty_without_hotspots():
    assert check_downwind(25.0, 121.5, 0.0, [], wind_speed=5.0) == []


def test_check_downwind_detects_user_south_of_hotspot_under_north_wind():
    """北風（0 度）把污染往南送；使用者在熱點正南方，應判定為下風處。"""
    hotspot = _hotspot(25.30, 121.50)
    result = check_downwind(25.00, 121.50, wind_direction_deg=0.0,
                            hotspots=[hotspot], wind_speed=5.0)
    assert len(result) == 1
    assert result[0]["distance_km"] == pytest.approx(33.4, abs=2.0)


def test_check_downwind_excludes_user_upwind_of_hotspot():
    """使用者在熱點的上風處（北風下位於熱點北方）不該被判定為下風處。"""
    hotspot = _hotspot(25.00, 121.50)
    result = check_downwind(25.30, 121.50, wind_direction_deg=0.0,
                            hotspots=[hotspot], wind_speed=5.0)
    assert result == []


def test_check_downwind_preserves_original_hotspot_fields():
    hotspot = _hotspot(25.30, 121.50, dominant_type="chemical", count=7)
    result = check_downwind(25.0, 121.5, 0.0, [hotspot], wind_speed=5.0)
    assert result[0]["dominant_type"] == "chemical"
    assert result[0]["count"] == 7


def test_check_downwind_does_not_mutate_input_hotspots():
    hotspot = _hotspot(25.30, 121.50)
    snapshot = dict(hotspot)
    check_downwind(25.0, 121.5, 0.0, [hotspot], wind_speed=5.0)
    assert hotspot == snapshot


def test_check_downwind_results_sorted_by_distance():
    hotspots = [
        _hotspot(25.40, 121.50),
        _hotspot(25.10, 121.50),
        _hotspot(25.25, 121.50),
    ]
    result = check_downwind(25.0, 121.5, 0.0, hotspots, wind_speed=5.0)
    distances = [r["distance_km"] for r in result]
    assert distances == sorted(distances)


def test_check_downwind_calm_wind_ignores_direction():
    """無風時（< CALM_WIND_THRESHOLD）改以 50km 半徑判定，不看方向。"""
    upwind = _hotspot(25.00, 121.50)
    calm = CALM_WIND_THRESHOLD - 0.1
    result = check_downwind(25.20, 121.50, wind_direction_deg=0.0,
                            hotspots=[upwind], wind_speed=calm)
    assert len(result) == 1
    assert result[0]["bearing_to_user"] == 0.0


def test_check_downwind_calm_wind_still_bounded_by_50km():
    far = _hotspot(22.60, 120.30)  # 高雄，距台北約 296 km
    result = check_downwind(25.0478, 121.5319, 0.0, [far], wind_speed=0.0)
    assert result == []


def test_haversine_matches_interpolation_module():
    """兩個模組各自實作了 haversine，數值必須一致。

    （重構為單一共用實作後，這個測試應繼續通過。）
    """
    from gis.interpolation import _haversine_km

    a = _haversine(25.0478, 121.5319, 22.6273, 120.3014)
    b = _haversine_km(25.0478, 121.5319, 22.6273, 120.3014)
    assert a == pytest.approx(b, rel=1e-9)
