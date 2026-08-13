"""crawler/forecast_fetcher.py — 空品預報解析與縣市／空品區對應。

`fetch_worsening_forecasts` 的輸出直接餵給 `_forecast_push_job`，
而該 job 拿 `region` 欄位去查裝置 token。這裡的欄位語意錯了，
整條預報推播就會靜默失效（見檔案末段的 known_bug）。
"""

import pytest

from crawler.forecast_fetcher import (
    _COUNTY_TO_AREA,
    _aqi_rank,
    _aqi_to_status,
    _county_to_area,
    _parse_aqi,
    _parse_trend,
    _parse_trend_detail,
)


# ── _parse_aqi ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        (85, 85),
        ("85", 85),
        ("  85  ", 85),
        ("51-100", 100),      # 環境部常以區間表示，取上界（保守）
        ("101-150", 150),
        (None, 0),
        ("", 0),
        ("無資料", 0),
        ("N/A", 0),
    ],
)
def test_parse_aqi(raw, expected):
    assert _parse_aqi(raw) == expected


def test_parse_aqi_takes_upper_bound_of_range():
    """區間值取上界，確保警示不會低估風險。"""
    assert _parse_aqi("51-100") == 100


# ── AQI 等級對應 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "aqi, status",
    [
        (0, "良好"), (50, "良好"),
        (51, "普通"), (100, "普通"),
        (101, "對敏感族群不健康"), (150, "對敏感族群不健康"),
        (151, "對所有族群不健康"), (200, "對所有族群不健康"),
        (201, "非常不健康"), (300, "非常不健康"),
        (301, "危害"), (500, "危害"),
    ],
)
def test_aqi_to_status_boundaries(aqi, status):
    assert _aqi_to_status(aqi) == status


@pytest.mark.parametrize(
    "aqi, rank",
    [(50, 1), (51, 2), (100, 2), (101, 3), (150, 3), (151, 4), (201, 5), (301, 6)],
)
def test_aqi_rank_boundaries(aqi, rank):
    assert _aqi_rank(aqi) == rank


def test_aqi_rank_is_monotonic():
    ranks = [_aqi_rank(v) for v in range(0, 400, 7)]
    assert ranks == sorted(ranks)


def test_status_and_rank_agree_on_the_101_threshold():
    """101 是「開始需要對敏感族群示警」的分界，兩個函式必須一致。"""
    assert _aqi_rank(100) == 2 and _aqi_to_status(100) == "普通"
    assert _aqi_rank(101) == 3 and _aqi_to_status(101) == "對敏感族群不健康"


# ── 縣市 → 空品區 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "county, area",
    [
        ("台北市", "北部"), ("臺北市", "北部"), ("台北", "北部"),
        ("新竹縣", "竹苗"), ("新竹市", "竹苗"),
        ("台中市", "中部"), ("彰化縣", "中部"),
        ("台南市", "雲嘉南"), ("高雄市", "高屏"),
        ("宜蘭縣", "宜蘭"), ("花蓮縣", "花東"),
        ("澎湖縣", "離島"), ("金門縣", "離島"),
    ],
)
def test_county_to_area(county, area):
    assert _county_to_area(county) == area


def test_county_to_area_returns_none_for_unknown():
    assert _county_to_area("不存在縣") is None
    assert _county_to_area("") is None


def test_every_centroid_county_maps_to_an_area():
    """gis 的縣市清單與預報的空品區對應表必須涵蓋一致，否則該縣市永遠收不到預報。"""
    from gis.hotspot_analyzer import COUNTY_CENTROIDS

    missing = [c for c in COUNTY_CENTROIDS if _county_to_area(c) is None]
    assert missing == []


# ── 趨勢文字解析 ─────────────────────────────────────────────────────────────

def test_parse_trend_only_reads_sentences_about_its_own_area():
    """整篇預報文幾乎必含「污染」，不得因此把每個空品區都判成轉差。"""
    content = "北部空品區污染物易累積，空氣品質不良；花東空品區擴散條件良好，空氣品質改善。"
    assert "轉差" in _parse_trend(content, area="北部空品區", forecast_aqi=120)
    assert _parse_trend(content, area="花東空品區", forecast_aqi=40) == "空氣品質預計改善"


def test_parse_trend_falls_back_to_aqi_level_when_no_match():
    result = _parse_trend("", area="北部空品區", forecast_aqi=120)
    assert result == "空氣品質預計為對敏感族群不健康等級"


def test_parse_trend_without_any_information_is_still_a_sentence():
    assert _parse_trend("", area="", forecast_aqi=0)


def test_parse_trend_worsening_wording_scales_with_severity():
    content = "北部空品區污染物濃度升高。"
    severe = _parse_trend(content, area="北部空品區", forecast_aqi=160)
    mild = _parse_trend(content, area="北部空品區", forecast_aqi=80)
    assert severe != mild
    assert "防護" in severe


def test_parse_trend_detail_is_length_bounded():
    long_content = "北部空品區今日空氣品質為普通等級。" * 30
    assert len(_parse_trend_detail(long_content, area="北部空品區")) <= 80


def test_parse_trend_detail_handles_empty_content():
    assert _parse_trend_detail("", area="北部空品區") == ""


# ── 已知缺陷：預報推播的欄位語意 ─────────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="fetch_worsening_forecasts(county=None) 會把 region 填成空品區名稱"
           "（如「北部空品區」），而 main._forecast_push_job 直接拿它呼叫 "
           "get_tokens_by_county()。裝置註冊的是縣市（「臺北市」），永遠對不上，"
           "整條空品預報推播從未送出任何一則。",
)
def test_worsening_forecast_region_is_a_county_not_an_air_quality_zone(monkeypatch):
    import crawler.forecast_fetcher as mod
    from gis.hotspot_analyzer import COUNTY_CENTROIDS

    monkeypatch.setattr(
        mod, "fetch_latest_forecast",
        lambda county=None: [{"area": "北部空品區", "aqi": "120",
                              "content": "北部空品區污染物易累積。",
                              "publishtime": "2026-08-13 06:00"}],
    )

    records = mod.fetch_worsening_forecasts()
    assert records, "AQI 120 應該產生一筆惡化預報"
    for rec in records:
        assert rec["region"] in COUNTY_CENTROIDS, (
            f"region={rec['region']!r} 不是縣市名稱，無法用來查裝置 token"
        )


def test_worsening_forecast_filters_below_threshold(monkeypatch):
    import crawler.forecast_fetcher as mod

    monkeypatch.setattr(
        mod, "fetch_latest_forecast",
        lambda county=None: [
            {"area": "北部空品區", "aqi": "80", "content": "", "publishtime": "2026-08-13 06:00"},
            {"area": "高屏空品區", "aqi": "160", "content": "", "publishtime": "2026-08-13 06:00"},
        ],
    )

    records = mod.fetch_worsening_forecasts()
    assert len(records) == 1
    assert "160" not in records[0]["title"]      # title 用文字等級而非數值
    assert "對所有族群不健康" in records[0]["title"]


def test_worsening_forecast_skips_when_not_worse_than_current(monkeypatch):
    """預報等級沒有比現況更差時不推播，避免重複打擾。"""
    import crawler.forecast_fetcher as mod

    monkeypatch.setattr(
        mod, "fetch_latest_forecast",
        lambda county=None: [{"area": "北部空品區", "aqi": "120",
                              "content": "", "publishtime": "2026-08-13 06:00"}],
    )
    assert mod.fetch_worsening_forecasts(current_aqi=130) == []
    assert mod.fetch_worsening_forecasts(current_aqi=50) != []


def test_area_table_covers_all_taiwan_counties():
    """對應表完整性：22 個行政區劃都要有歸屬空品區。

    表的 key 是去掉「市／縣」後綴的短名，因此「新竹」「嘉義」各自涵蓋市與縣，
    「連江」與「馬祖」是同一個縣的兩種寫法。
    """
    expected_short_names = {
        "基隆", "台北", "新北", "桃園", "新竹", "苗栗", "台中", "彰化", "南投",
        "雲林", "嘉義", "台南", "高雄", "屏東", "宜蘭", "花蓮", "台東",
        "澎湖", "金門", "連江",
    }
    assert expected_short_names <= set(_COUNTY_TO_AREA)
    assert all(v for v in _COUNTY_TO_AREA.values())


def test_both_city_and_county_variants_resolve_for_shared_short_names():
    for full in ("新竹市", "新竹縣", "嘉義市", "嘉義縣"):
        assert _county_to_area(full) is not None
