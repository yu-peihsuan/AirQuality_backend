"""gis/interpolation.py — IDW 反距離加權空間插值。

這支模組是 README 宣稱「較最近測站法降低 19.2% 誤差」的依據，
屬於對外宣稱過的數值方法，行為必須被釘住。
"""

import math

import pytest

from gis.interpolation import _haversine_km, _to_float, _valid_stations, idw_estimate


def _station(sitename, lat, lng, aqi=None, pm25=None, county="測試縣"):
    rec = {"sitename": sitename, "county": county, "latitude": lat, "longitude": lng}
    if aqi is not None:
        rec["aqi"] = aqi
    if pm25 is not None:
        rec["pm2.5"] = pm25
    return rec


# ── _haversine_km ────────────────────────────────────────────────────────────

def test_haversine_same_point_is_zero():
    assert _haversine_km(25.0, 121.5, 25.0, 121.5) == pytest.approx(0.0, abs=1e-9)


def test_haversine_is_symmetric():
    a = _haversine_km(25.0478, 121.5319, 22.6273, 120.3014)
    b = _haversine_km(22.6273, 120.3014, 25.0478, 121.5319)
    assert a == pytest.approx(b, rel=1e-12)


def test_haversine_taipei_to_kaohsiung_is_about_296km():
    """台北市中心到高雄市中心的大圓距離約 296 km（±5 km）。"""
    d = _haversine_km(25.0478, 121.5319, 22.6273, 120.3014)
    assert d == pytest.approx(296, abs=5)


def test_haversine_one_degree_latitude_is_about_111km():
    d = _haversine_km(24.0, 121.0, 25.0, 121.0)
    assert d == pytest.approx(111.19, abs=0.5)


# ── _to_float / _valid_stations ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("35", 35.0),
        ("35.5", 35.5),
        ("  42  ", 42.0),   # MOENV 回傳值常有前後空白
        (7, 7.0),
        (7.5, 7.5),
        ("", None),
        ("-", None),        # MOENV 用 "-" 表示無資料
        ("ND", None),       # 未檢出
        (None, None),
    ],
)
def test_to_float_handles_moenv_quirks(raw, expected):
    assert _to_float(raw) == expected


def test_valid_stations_drops_records_missing_coords_or_value():
    records = [
        _station("完整", 25.0, 121.5, aqi="30"),
        _station("無座標", None, None, aqi="30"),
        _station("無數值", 25.0, 121.5),
        {"sitename": "缺欄位"},
    ]
    valid = _valid_stations(records, "aqi")
    assert [s["sitename"] for s in valid] == ["完整"]


def test_valid_stations_selects_the_requested_field():
    records = [_station("A", 25.0, 121.5, aqi="30")]  # 有 aqi 但沒有 pm2.5
    assert _valid_stations(records, "aqi") != []
    assert _valid_stations(records, "pm2.5") == []


# ── idw_estimate：邊界情況 ───────────────────────────────────────────────────

def test_idw_returns_none_when_no_valid_station():
    assert idw_estimate(25.0, 121.5, [], field="aqi") is None
    assert idw_estimate(25.0, 121.5, [_station("壞資料", None, None)], field="aqi") is None


def test_idw_on_site_returns_measured_value_verbatim():
    """幾乎站在測站上時，必須回傳實測值而非插值，method 標記為 on_site。"""
    records = [
        _station("就在腳下", 25.0, 121.5, aqi="42"),
        _station("遠方", 22.6, 120.3, aqi="100"),
    ]
    result = idw_estimate(25.0, 121.5, records, field="aqi")
    assert result["method"] == "on_site"
    assert result["value"] == 42.0
    assert len(result["stations"]) == 1
    assert result["stations"][0]["weight"] == 1.0


def test_idw_single_station_returns_that_stations_value():
    records = [_station("唯一", 24.0, 121.0, aqi="55")]
    result = idw_estimate(25.0, 121.5, records, field="aqi", k=4)
    assert result["method"] == "idw"
    assert result["value"] == pytest.approx(55.0)


def test_idw_result_lies_between_min_and_max_of_neighbours():
    """IDW 是加權平均，估計值必須落在鄰近測站數值的範圍內（不得外推）。"""
    records = [
        _station("A", 25.00, 121.50, aqi="20"),
        _station("B", 25.10, 121.50, aqi="60"),
        _station("C", 25.00, 121.60, aqi="40"),
        _station("D", 25.10, 121.60, aqi="80"),
    ]
    result = idw_estimate(25.05, 121.55, records, field="aqi", k=4)
    assert 20.0 <= result["value"] <= 80.0


def test_idw_is_closer_to_the_nearest_station():
    """距離最近的測站權重最大，估計值應偏向它。"""
    records = [
        _station("很近", 25.001, 121.500, aqi="20"),
        _station("很遠", 25.400, 121.500, aqi="100"),
    ]
    result = idw_estimate(25.0, 121.5, records, field="aqi", k=2)
    assert result["value"] < 30.0


def test_idw_weights_sum_to_one():
    records = [
        _station("A", 25.00, 121.50, aqi="20"),
        _station("B", 25.10, 121.50, aqi="60"),
        _station("C", 25.00, 121.60, aqi="40"),
    ]
    result = idw_estimate(25.05, 121.55, records, field="aqi", k=3)
    assert sum(s["weight"] for s in result["stations"]) == pytest.approx(1.0, abs=0.005)


def test_idw_respects_k_and_returns_nearest_k_stations():
    records = [
        _station(f"S{i}", 25.01 + i * 0.05, 121.5, aqi=str(10 * i + 10))
        for i in range(6)
    ]
    result = idw_estimate(25.0, 121.5, records, field="aqi", k=3)
    assert result["method"] == "idw"
    assert len(result["stations"]) == 3
    names = [s["sitename"] for s in result["stations"]]
    assert names == ["S0", "S1", "S2"]


def test_idw_stations_are_sorted_by_distance_ascending():
    records = [
        _station("遠", 25.30, 121.5, aqi="90"),
        _station("近", 25.02, 121.5, aqi="10"),
        _station("中", 25.15, 121.5, aqi="50"),
    ]
    result = idw_estimate(25.0, 121.5, records, field="aqi", k=3)
    distances = [s["distance_km"] for s in result["stations"]]
    assert distances == sorted(distances)


def test_idw_higher_power_concentrates_weight_on_nearest():
    """power 越大，最近站的權重越高（IDW 的基本性質）。"""
    records = [
        _station("近", 25.01, 121.5, aqi="10"),
        _station("遠", 25.20, 121.5, aqi="90"),
    ]
    low = idw_estimate(25.0, 121.5, records, field="aqi", k=2, power=1.0)
    high = idw_estimate(25.0, 121.5, records, field="aqi", k=2, power=4.0)
    assert high["stations"][0]["weight"] > low["stations"][0]["weight"]
    assert high["value"] < low["value"]


def test_idw_ignores_stations_missing_the_target_field():
    """某些測站只有 aqi 沒有 pm2.5，估 pm2.5 時不能把它們算進來。"""
    records = [
        _station("有pm25", 25.01, 121.50, aqi="30", pm25="12"),
        _station("只有aqi", 25.02, 121.50, aqi="30"),
    ]
    result = idw_estimate(25.0, 121.5, records, field="pm2.5", k=4)
    assert len(result["stations"]) == 1
    assert result["value"] == pytest.approx(12.0)


def test_idw_does_not_mutate_caller_records():
    """插值計算不得污染呼叫端傳入的原始資料。"""
    records = [_station("A", 25.01, 121.5, aqi="30")]
    snapshot = {k: v for k, v in records[0].items()}
    idw_estimate(25.0, 121.5, records, field="aqi")
    assert records[0] == snapshot


def test_idw_value_is_finite_for_realistic_inputs():
    records = [
        _station("A", 24.9, 121.4, aqi="35"),
        _station("B", 25.1, 121.6, aqi="45"),
    ]
    result = idw_estimate(25.0, 121.5, records, field="aqi")
    assert math.isfinite(result["value"])
