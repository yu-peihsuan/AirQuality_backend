"""rag/health_rules.py — AQI 等級知識庫。

這是 RAG 的唯一知識來源（共 8 條規則），也是 `/api/rag_advice` 回傳
`aqi_level` 欄位的依據，App 端直接顯示給使用者。
規則的區間覆蓋、邊界、與等級名稱一致性都必須釘住。
"""

import pytest

from rag.health_rules import (
    AQI_HEALTH_RULES,
    WHO_AQG_2021,
    get_all_rules,
    get_rag_indexing_text,
    get_rule_by_aqi,
    get_rule_by_id,
    get_who_key_message,
    get_who_standard_context,
)

_AQI_RULES = [r for r in AQI_HEALTH_RULES if r.get("aqi_range")]
_EVENT_RULES = [r for r in AQI_HEALTH_RULES if not r.get("aqi_range")]


# ── 結構完整性 ───────────────────────────────────────────────────────────────

def test_rule_ids_are_unique():
    ids = [r["id"] for r in AQI_HEALTH_RULES]
    assert len(ids) == len(set(ids))


def test_every_rule_has_the_required_fields():
    required = {"id", "level", "level_en", "color", "text"}
    for rule in AQI_HEALTH_RULES:
        assert required <= set(rule), f"{rule.get('id')} 缺少欄位"


def test_every_rule_has_general_and_sensitive_advice():
    for rule in _AQI_RULES:
        assert rule.get("advice_general"), f"{rule['id']} 缺少一般建議"
        assert rule.get("advice_sensitive"), f"{rule['id']} 缺少敏感族群建議"


def test_event_rules_exist_for_fire_and_chemical():
    ids = {r["id"] for r in _EVENT_RULES}
    assert {"event_fire_smoke", "event_chemical_odor"} <= ids


def test_get_all_rules_returns_every_rule():
    assert len(get_all_rules()) == len(AQI_HEALTH_RULES)


# ── AQI 區間覆蓋 ─────────────────────────────────────────────────────────────

def test_aqi_ranges_are_contiguous_and_non_overlapping():
    ranges = sorted(r["aqi_range"] for r in _AQI_RULES)
    assert ranges[0][0] == 0
    for (_, prev_max), (next_min, _) in zip(ranges, ranges[1:]):
        assert next_min == prev_max + 1, f"區間不連續：{prev_max} → {next_min}"


@pytest.mark.parametrize("aqi", [0, 1, 50, 51, 100, 101, 150, 151, 200, 201, 300, 301, 500])
def test_every_aqi_in_range_resolves_to_a_rule(aqi):
    assert get_rule_by_aqi(aqi) is not None


@pytest.mark.parametrize(
    "aqi, expected_id",
    [
        (0, "aqi_good"), (50, "aqi_good"),
        (51, "aqi_moderate"), (100, "aqi_moderate"),
        (101, "aqi_usg"), (150, "aqi_usg"),
        (151, "aqi_unhealthy"), (200, "aqi_unhealthy"),
        (201, "aqi_very_unhealthy"), (300, "aqi_very_unhealthy"),
        (301, "aqi_hazardous"), (500, "aqi_hazardous"),
    ],
)
def test_rule_lookup_boundaries(aqi, expected_id):
    assert get_rule_by_aqi(aqi)["id"] == expected_id


def test_lookup_never_returns_an_event_rule():
    """事件規則沒有 AQI 區間，不得被數值查詢意外命中。"""
    event_ids = {r["id"] for r in _EVENT_RULES}
    for aqi in range(0, 501, 13):
        rule = get_rule_by_aqi(aqi)
        assert rule is None or rule["id"] not in event_ids


# ── get_rule_by_id ───────────────────────────────────────────────────────────

def test_get_rule_by_id_roundtrips_every_rule():
    for rule in AQI_HEALTH_RULES:
        assert get_rule_by_id(rule["id"])["level"] == rule["level"]


def test_get_rule_by_id_returns_none_for_unknown():
    assert get_rule_by_id("does_not_exist") is None


def test_rag_indexing_text_available_for_every_rule():
    """embedder 用這段文字建索引，缺任何一條都會讓該規則檢索不到。"""
    for rule in AQI_HEALTH_RULES:
        text = get_rag_indexing_text(rule["id"])
        assert text and text.strip()


# ── WHO 參考資料 ─────────────────────────────────────────────────────────────

def test_who_context_available_for_main_pollutants():
    for pollutant in ("PM2.5", "PM10", "NO2", "SO2", "CO"):
        assert get_who_standard_context(pollutant) is not None


def test_who_context_returns_none_for_unknown_pollutant():
    assert get_who_standard_context("不存在污染物") is None


def test_who_key_message_is_present():
    assert "安全" in get_who_key_message()


def test_who_pm25_values_match_the_2021_guideline():
    """WHO 2021 AQG：PM2.5 年均 5、日均 15 µg/m³。數值寫錯會誤導使用者。"""
    assert WHO_AQG_2021["PM2.5"]["annual_mean_ug_m3"] == 5
    assert WHO_AQG_2021["PM2.5"]["24h_mean_ug_m3"] == 15


# ── 已知缺陷：等級名稱不一致 ────────────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="AQI 151–200 在 health_rules 是「不健康」，但 rag_engine._aqi_to_status 與 "
           "forecast_fetcher._aqi_to_status 都是「對所有族群不健康」（環境部官方用語）。"
           "同一個 AQI 在 App 的建議卡片與預報通知會顯示兩種等級名稱。",
)
def test_rule_level_names_match_the_official_status_names():
    def official(aqi):
        if aqi <= 50:  return "良好"
        if aqi <= 100: return "普通"
        if aqi <= 150: return "對敏感族群不健康"
        if aqi <= 200: return "對所有族群不健康"
        if aqi <= 300: return "非常不健康"
        return "危害"

    mismatches = [
        (rule["id"], rule["level"], official(rule["aqi_range"][0]))
        for rule in _AQI_RULES
        if rule["level"] != official(rule["aqi_range"][0])
    ]
    assert mismatches == []


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="規則區間上限是 500，但 AQI 理論上可超過 500（沙塵暴、重大火災）。"
           "此時 get_rule_by_aqi 回傳 None，/api/rag_advice 的 aqi_level 變成「未知」，"
           "反而在最危險的情境下失去等級資訊。",
)
def test_extreme_aqi_still_resolves_to_the_hazardous_rule():
    assert get_rule_by_aqi(501) is not None
    assert get_rule_by_aqi(999)["id"] == "aqi_hazardous"


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="負值／None 等異常輸入沒有防禦，get_rule_by_aqi 直接回傳 None，"
           "呼叫端未檢查就會在 prompt 組裝時出現「未知」等級。",
)
def test_negative_aqi_is_clamped_to_the_good_rule():
    assert get_rule_by_aqi(-1)["id"] == "aqi_good"
