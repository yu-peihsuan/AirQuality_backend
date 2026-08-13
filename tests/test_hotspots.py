"""gis/hotspot_analyzer.py — analyze_hotspots()（KDE 熱點分析）。

熱點結果同時餵給 `/api/hotspots`（地圖圖層）與 `/api/rag_advice`（下風處判斷），
所以「資料不足時要安靜地回空清單」跟「有共識時要成群」一樣重要。

資料來源以 monkeypatch 注入，測試不碰真實 DB。
"""

import pytest

import gis.hotspot_analyzer as hotspots
from gis.hotspot_analyzer import CALM_WIND_THRESHOLD, analyze_hotspots


def _report(lat, lng, event_type="fire", category="fire"):
    return {
        "latitude": lat,
        "longitude": lng,
        "category": category,
        "structured_event": {"event_type": event_type} if event_type else None,
    }


@pytest.fixture
def reports(monkeypatch):
    """注入已確認回報清單。"""
    holder = {"rows": []}
    monkeypatch.setattr(hotspots, "get_confirmed_reports", lambda *a, **k: holder["rows"])
    return holder


# ── 資料不足 ─────────────────────────────────────────────────────────────────

def test_no_reports_yields_no_hotspots(reports):
    assert analyze_hotspots() == []


def test_below_min_reports_yields_no_hotspots(reports):
    reports["rows"] = [_report(25.0, 121.5)]
    assert analyze_hotspots(min_reports=2) == []


def test_reports_without_coordinates_are_ignored(reports):
    reports["rows"] = [
        {"latitude": None, "longitude": None, "category": "fire"},
        {"latitude": None, "longitude": None, "category": "fire"},
        {"latitude": None, "longitude": None, "category": "fire"},
    ]
    assert analyze_hotspots(min_reports=2) == []


def test_mixed_reports_only_use_those_with_coordinates(reports):
    reports["rows"] = [
        _report(25.000, 121.500),
        {"latitude": None, "longitude": None, "category": "fire"},
    ]
    assert analyze_hotspots(min_reports=2) == []


# ── 完全重合的座標 ───────────────────────────────────────────────────────────

def test_identical_coordinates_collapse_into_one_hotspot(reports):
    """同一地點的多筆回報：KDE 會退化，必須走特例路徑而不是拋例外。"""
    reports["rows"] = [_report(25.0478, 121.5319) for _ in range(4)]
    result = analyze_hotspots(min_reports=2)
    assert len(result) == 1
    assert result[0]["count"] == 4
    assert result[0]["intensity"] == 1.0
    assert result[0]["lat"] == pytest.approx(25.0478)
    assert result[0]["lng"] == pytest.approx(121.5319)


# ── 一般聚類 ─────────────────────────────────────────────────────────────────

def test_clustered_reports_produce_a_hotspot_near_the_cluster(reports):
    reports["rows"] = [
        _report(25.0470, 121.5310),
        _report(25.0480, 121.5320),
        _report(25.0475, 121.5318),
        _report(25.0479, 121.5312),
    ]
    result = analyze_hotspots(min_reports=2, cluster_radius_km=1.5)
    assert result
    assert result[0]["lat"] == pytest.approx(25.047, abs=0.05)
    assert result[0]["lng"] == pytest.approx(121.531, abs=0.05)


def test_hotspot_result_shape(reports):
    reports["rows"] = [_report(25.0478, 121.5319) for _ in range(3)]
    hs = analyze_hotspots(min_reports=2)[0]
    expected = {"lat", "lng", "count", "intensity", "radius_km",
                "dominant_type", "is_calm_wind", "wind_speed", "wind_direction"}
    assert expected <= set(hs)


def test_intensity_is_normalised_between_zero_and_one(reports):
    reports["rows"] = [
        _report(25.0470 + i * 0.0005, 121.5310 + i * 0.0005) for i in range(6)
    ]
    for hs in analyze_hotspots(min_reports=2):
        assert 0.0 <= hs["intensity"] <= 1.0


def test_top_n_caps_the_number_of_hotspots(reports):
    reports["rows"] = [
        _report(25.0 + i * 0.3, 121.0 + i * 0.2) for i in range(6)
    ] * 3
    result = analyze_hotspots(min_reports=2, top_n=2)
    assert len(result) <= 2


def test_min_reports_threshold_is_enforced_per_hotspot(reports):
    """孤立的單筆回報不得自成熱點（需要 min_reports 筆共識）。"""
    reports["rows"] = [
        _report(25.0470, 121.5310),
        _report(25.0475, 121.5315),
        _report(25.0478, 121.5318),
        _report(24.1477, 120.6736),   # 台中，孤立一筆
    ]
    result = analyze_hotspots(min_reports=3, cluster_radius_km=1.5)
    for hs in result:
        assert hs["count"] >= 3


# ── dominant_type ────────────────────────────────────────────────────────────

def test_dominant_type_is_the_most_common_event_type(reports):
    reports["rows"] = [
        _report(25.0478, 121.5319, event_type="chemical"),
        _report(25.0478, 121.5319, event_type="chemical"),
        _report(25.0478, 121.5319, event_type="fire"),
    ]
    assert analyze_hotspots(min_reports=2)[0]["dominant_type"] == "chemical"


def test_dominant_type_falls_back_to_category(reports):
    """LLM 結構化失敗（structured_event 為 None）時改用使用者選的類別。"""
    reports["rows"] = [
        _report(25.0478, 121.5319, event_type=None, category="dust"),
        _report(25.0478, 121.5319, event_type=None, category="dust"),
    ]
    assert analyze_hotspots(min_reports=2)[0]["dominant_type"] == "dust"


# ── 風況 ─────────────────────────────────────────────────────────────────────

def test_calm_wind_doubles_the_effective_radius(reports):
    reports["rows"] = [_report(25.0478, 121.5319) for _ in range(3)]
    calm = analyze_hotspots(min_reports=2, cluster_radius_km=1.5,
                            wind_speed=CALM_WIND_THRESHOLD - 0.1)[0]
    windy = analyze_hotspots(min_reports=2, cluster_radius_km=1.5,
                             wind_speed=5.0)[0]
    assert calm["radius_km"] == pytest.approx(3.0)
    assert windy["radius_km"] == pytest.approx(1.5)
    assert calm["is_calm_wind"] is True
    assert windy["is_calm_wind"] is False


def test_wind_metadata_is_echoed_back(reports):
    reports["rows"] = [_report(25.0478, 121.5319) for _ in range(3)]
    hs = analyze_hotspots(min_reports=2, wind_speed=3.24, wind_direction=91.7)[0]
    assert hs["wind_speed"] == pytest.approx(3.2)
    assert hs["wind_direction"] == pytest.approx(91.7)


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="回報點共線時（沿著道路、河道或煙流軸線分布，是很常見的真實情境），"
           "gaussian_kde 的共變異數矩陣退化並拋 LinAlgError，"
           "被 except 吞掉後回傳空清單 —— 明明有共識回報卻分析不出熱點。"
           "現有的 np.std < 1e-6 防護只擋得住「完全重合」，擋不住「共線」。",
)
def test_collinear_reports_still_produce_a_hotspot(reports):
    """沿一直線分布的回報（例如同一條路上的多筆通報）仍應成為熱點。"""
    reports["rows"] = [
        _report(25.0470, 121.5310),
        _report(25.0475, 121.5315),
        _report(25.0480, 121.5320),
        _report(25.0485, 121.5325),
    ]
    result = analyze_hotspots(min_reports=2, cluster_radius_km=1.5)
    assert result, "共線的回報群仍應被辨識為熱點"


# 註：「/api/rag_advice 未把風況帶進熱點分析」的缺陷，
# 以行為測試記錄於 test_rag_advice_endpoint.py::
# test_hotspot_analysis_uses_the_same_wind_as_downwind_check
