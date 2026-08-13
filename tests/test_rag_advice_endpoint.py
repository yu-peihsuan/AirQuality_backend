"""main.get_rag_advice — /api/rag_advice 的編排邏輯。

這是全系統扇出最廣的端點：AQI、天氣、天氣預報、空品預報、新聞事件、
民眾回報、熱點分析、下風處判斷、RAG 生成，九個來源在一次請求裡串起來。
之後要把它拆成 service 層並加快取，因此必須先把「編排契約」釘住：
誰被呼叫、用什麼參數、回應長什麼樣、上游掛掉時怎麼降級。

所有外部相依一律以 stub 注入，測試不碰網路、不呼叫 LLM。
"""

import pytest

import main


@pytest.fixture
def wiring(monkeypatch):
    """把 get_rag_advice 的所有外部相依換成可觀察的替身。"""
    calls = {"analyze_hotspots": [], "check_downwind": [], "generate_advice": [],
             "aqi_for_county": [], "weather": []}

    def _fetch_aqi(county):
        calls["aqi_for_county"].append(county)
        return {"aqi": 85, "pm25": 22.5, "sitename": "測試站",
                "wind_speed": 3.0, "wind_direction": 90.0, "temperature": 28.0}

    def _weather(county, lat=None, lng=None):
        calls["weather"].append((county, lat, lng))
        return {"description": "晴", "is_raining": False, "temp": 29.0,
                "weather": "晴", "humidity": 60, "rain_mm": 0.0}

    def _hotspots(**kwargs):
        calls["analyze_hotspots"].append(kwargs)
        return [{"lat": 23.1, "lng": 120.3, "count": 3, "intensity": 0.8,
                 "dominant_type": "fire", "radius_km": 1.5}]

    def _downwind(**kwargs):
        calls["check_downwind"].append(kwargs)
        return [{"lat": 23.1, "lng": 120.3, "distance_km": 4.2, "intensity": 0.8,
                 "dominant_type": "fire"}]

    def _advice(**kwargs):
        calls["generate_advice"].append(kwargs)
        return {"advice": "先關窗，出門記得戴口罩。", "aqi_level": "普通",
                "retrieved_rules": ["aqi_moderate"], "error": None}

    import crawler.forecast_fetcher as forecast_mod
    import crawler.weather_fetcher as weather_mod

    monkeypatch.setattr(main, "_fetch_aqi_for_county", _fetch_aqi)
    monkeypatch.setattr(main, "_fetch_recent_events_for_region", lambda region: "無")
    monkeypatch.setattr(main, "_fetch_user_report_events", lambda county: "無")
    monkeypatch.setattr(main, "analyze_hotspots", _hotspots)
    monkeypatch.setattr(main, "check_downwind", _downwind)
    monkeypatch.setattr(main, "generate_advice", _advice)
    monkeypatch.setattr(weather_mod, "fetch_weather_for_county", _weather)
    monkeypatch.setattr(weather_mod, "fetch_weather_forecast_for_county", lambda c: "晴")
    monkeypatch.setattr(forecast_mod, "fetch_latest_forecast",
                        lambda county=None: [{"aqi": "120", "status": "對敏感族群不健康"}])
    return calls


def _request(**overrides):
    payload = {"county": "台南市", "latitude": 23.0, "longitude": 120.2}
    payload.update(overrides)
    return main.RagAdviceRequest(**payload)


# ── 回應契約 ─────────────────────────────────────────────────────────────────

def test_response_contains_all_fields_the_app_reads(wiring):
    result = main.get_rag_advice(_request())
    expected = {
        "status", "county", "aqi", "pm25", "wind_speed", "wind_direction",
        "aqi_level", "advice", "event_context", "is_downwind",
        "downwind_sources", "retrieved_rules", "rag_error",
    }
    assert expected <= set(result)
    assert result["status"] == "success"


def test_client_supplied_aqi_is_used_verbatim(wiring):
    """App 首頁已經有 AQI 時直接沿用，不該被後端量測值覆寫。"""
    result = main.get_rag_advice(_request(aqi=42, pm25=9.9))
    assert result["aqi"] == 42
    assert result["pm25"] == pytest.approx(9.9)
    assert wiring["generate_advice"][0]["aqi"] == 42


def test_aqi_is_fetched_when_client_does_not_supply_it(wiring):
    result = main.get_rag_advice(_request())
    assert result["aqi"] == 85
    assert result["pm25"] == pytest.approx(22.5)


def test_downwind_sources_are_capped_at_three(wiring, monkeypatch):
    monkeypatch.setattr(main, "check_downwind", lambda **kw: [
        {"lat": 23.0, "lng": 120.0, "distance_km": float(i),
         "intensity": 0.5, "dominant_type": "fire"} for i in range(10)
    ])
    result = main.get_rag_advice(_request())
    assert len(result["downwind_sources"]) == 3


def test_downwind_context_is_appended_to_the_event_description(wiring):
    result = main.get_rag_advice(_request())
    assert result["is_downwind"] is True
    assert "下風處" in result["event_context"]


# ── 位置與風況的條件分支 ────────────────────────────────────────────────────

def test_hotspot_analysis_is_skipped_without_coordinates(wiring):
    """沒有 GPS 座標就無從判斷下風處，不該白跑一次 KDE。"""
    result = main.get_rag_advice(_request(latitude=None, longitude=None))
    assert wiring["analyze_hotspots"] == []
    assert result["is_downwind"] is False
    assert result["downwind_sources"] == []


def test_hotspot_analysis_is_skipped_when_wind_is_too_weak(wiring, monkeypatch):
    monkeypatch.setattr(main, "_fetch_aqi_for_county", lambda c: {
        "aqi": 85, "pm25": 22.5, "wind_speed": 0.2, "wind_direction": 0.0,
        "temperature": 28.0,
    })
    result = main.get_rag_advice(_request())
    assert wiring["analyze_hotspots"] == []
    assert result["is_downwind"] is False


def test_weather_lookup_receives_the_user_coordinates(wiring):
    """天氣要找「離使用者最近」的測站，座標必須傳下去。"""
    main.get_rag_advice(_request(latitude=22.9, longitude=120.1))
    assert wiring["weather"][0][1] == pytest.approx(22.9)
    assert wiring["weather"][0][2] == pytest.approx(120.1)


def test_cwa_temperature_overrides_the_aqi_station_temperature(wiring):
    """氣象署測站的溫度比 AQI 測站可靠，有值時優先採用。"""
    main.get_rag_advice(_request())
    assert wiring["generate_advice"][0]["temperature"] == pytest.approx(29.0)


def test_forecast_is_passed_through_to_the_generator(wiring):
    main.get_rag_advice(_request())
    assert wiring["generate_advice"][0]["forecast_aqi"] == 120


# ── 降級行為 ─────────────────────────────────────────────────────────────────

def test_generator_error_is_surfaced_without_failing_the_request(wiring, monkeypatch):
    monkeypatch.setattr(main, "generate_advice", lambda **kw: {
        "advice": "降級建議", "aqi_level": "普通",
        "retrieved_rules": [], "error": "LLM timeout",
    })
    result = main.get_rag_advice(_request())
    assert result["status"] == "success"
    assert result["advice"] == "降級建議"
    assert result["rag_error"] == "LLM timeout"


def test_unexpected_exception_is_reported_rather_than_raised(wiring, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("上游炸了")

    monkeypatch.setattr(main, "generate_advice", _boom)
    result = main.get_rag_advice(_request())
    assert result["status"] == "error"
    assert result["advice"] is None


# ── 效率：不重複打同一個上游 ────────────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="get_rag_advice 對同一個縣市呼叫 _fetch_aqi_for_county 兩次"
           "（一次取 AQI/PM2.5、一次取風速溫度），等於每個請求多打一次 MOENV。"
           "應合併為單次呼叫，或加上 TTL 快取。",
)
def test_aqi_upstream_is_queried_at_most_once_per_request(wiring):
    main.get_rag_advice(_request())
    assert len(wiring["aqi_for_county"]) <= 1


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="熱點分析未帶入本次請求已取得的風速／風向，"
           "導致熱點一律以「無風」計算（半徑加倍、is_calm_wind=True），"
           "卻又用真實風速做下風處判斷，兩者前後矛盾。",
)
def test_hotspot_analysis_uses_the_same_wind_as_downwind_check(wiring):
    main.get_rag_advice(_request())
    hotspot_kwargs = wiring["analyze_hotspots"][0]
    downwind_kwargs = wiring["check_downwind"][0]
    assert hotspot_kwargs.get("wind_speed") == downwind_kwargs.get("wind_speed")
