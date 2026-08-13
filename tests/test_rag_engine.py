"""rag/rag_engine.py — 檢索策略與建議生成。

LLM 呼叫一律 mock：測試要驗的是「送進 LLM 之前的檢索與組裝邏輯」
以及「LLM 失敗時的 fallback」，不是模型輸出品質。
"""

import pytest

import rag.rag_engine as engine
from rag.rag_engine import (
    _aqi_to_status,
    _build_query_text,
    _describe_user_profile,
    _fallback_advice,
    generate_advice,
)


# ── 測試替身 ─────────────────────────────────────────────────────────────────

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content="今天空氣不錯，出門走走剛剛好。", error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _FakeResponse(self.content)


@pytest.fixture
def fake_llm(monkeypatch):
    """攔截 rag_engine 的 OpenAI client，回傳可檢查的替身。"""
    completions = _FakeCompletions()

    class _Chat:
        pass

    chat = _Chat()
    chat.completions = completions

    class _Client:
        pass

    client = _Client()
    client.chat = chat
    monkeypatch.setattr(engine, "_client", client)
    return completions


@pytest.fixture
def fake_retrieval(monkeypatch):
    """攔截向量檢索，回傳固定結果並記錄查詢字串。"""
    calls = []

    def _query(query_text, n_results=3):
        calls.append((query_text, n_results))
        return [
            {"id": "aqi_moderate", "document": "普通等級說明"},
            {"id": "event_chemical_odor", "document": "化學異味說明"},
        ]

    monkeypatch.setattr(engine, "query_knowledge_base", _query)
    return calls


def _profile(**overrides):
    base = {
        "age_group": "adult",
        "is_pregnant": False,
        "has_asthma": False,
        "has_cardiovascular": False,
        "has_allergy": False,
        "other_notes": None,
    }
    base.update(overrides)
    return base


# ── _aqi_to_status ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "aqi, status",
    [
        (0, "良好"), (50, "良好"), (51, "普通"), (100, "普通"),
        (101, "對敏感族群不健康"), (150, "對敏感族群不健康"),
        (151, "對所有族群不健康"), (200, "對所有族群不健康"),
        (201, "非常不健康"), (300, "非常不健康"), (301, "危害"),
    ],
)
def test_aqi_to_status_boundaries(aqi, status):
    assert _aqi_to_status(aqi) == status


def test_aqi_to_status_matches_forecast_module():
    """兩個模組各有一份等級對照，數值必須一致（重構時應合併為單一實作）。"""
    from crawler.forecast_fetcher import _aqi_to_status as forecast_status

    for aqi in (0, 50, 51, 100, 101, 150, 151, 200, 201, 300, 301, 500):
        assert _aqi_to_status(aqi) == forecast_status(aqi)


# ── _describe_user_profile ───────────────────────────────────────────────────

def test_describe_profile_maps_age_group_to_chinese():
    assert "孩童" in _describe_user_profile(_profile(age_group="child"))["age_group"]
    assert "老年" in _describe_user_profile(_profile(age_group="elderly"))["age_group"]
    assert _describe_user_profile(_profile(age_group="adult"))["age_group"] == "成人"


def test_describe_profile_falls_back_for_unknown_age_group():
    assert _describe_user_profile(_profile(age_group="alien"))["age_group"] == "成人"


def test_describe_profile_renders_booleans_as_yes_no():
    desc = _describe_user_profile(_profile(has_asthma=True, is_pregnant=False))
    assert desc["has_asthma"] == "是"
    assert desc["is_pregnant"] == "否"


def test_describe_profile_defaults_empty_notes():
    assert _describe_user_profile(_profile(other_notes=None))["other_notes"] == "無"
    assert _describe_user_profile(_profile(other_notes=""))["other_notes"] == "無"


def test_describe_profile_supplies_every_prompt_placeholder():
    """缺任何一個 key 都會讓 prompt.format 直接拋 KeyError。"""
    desc = _describe_user_profile(_profile())
    required = {"age_group", "is_pregnant", "has_asthma",
                "has_cardiovascular", "has_allergy", "other_notes"}
    assert required <= set(desc)


# ── _build_query_text ────────────────────────────────────────────────────────

def test_query_text_always_includes_aqi():
    assert "AQI 85" in _build_query_text(85, _profile(), "無")


def test_query_text_includes_health_conditions():
    text = _build_query_text(85, _profile(has_asthma=True, is_pregnant=True), "無")
    assert "氣喘患者" in text
    assert "孕婦" in text


def test_query_text_includes_age_group_only_when_sensitive():
    assert "孩童" in _build_query_text(85, _profile(age_group="child"), "無")
    assert "老年人" in _build_query_text(85, _profile(age_group="elderly"), "無")
    assert "孩童" not in _build_query_text(85, _profile(age_group="adult"), "無")


def test_query_text_excludes_placeholder_event():
    """事件描述為「無」時不該污染檢索查詢。"""
    assert "無" not in _build_query_text(85, _profile(), "無").replace("AQI ", "")


def test_query_text_includes_real_event_description():
    assert "工廠火災" in _build_query_text(85, _profile(), "工廠火災濃煙")


# ── 檢索策略（消融實驗的四種模式）───────────────────────────────────────────

def test_retrieval_mode_none_skips_retrieval(fake_llm, fake_retrieval):
    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="none",
    )
    assert fake_retrieval == []
    assert result["retrieved_rules"] == []


def test_retrieval_mode_rule_injects_only_the_matching_level(fake_llm, fake_retrieval):
    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="rule",
    )
    assert fake_retrieval == []
    assert result["retrieved_rules"] == ["aqi_moderate"]


def test_retrieval_mode_semantic_uses_vector_search_only(fake_llm, fake_retrieval):
    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="semantic",
    )
    assert len(fake_retrieval) == 1
    assert result["retrieved_rules"] == ["aqi_moderate", "event_chemical_odor"]


def test_hybrid_puts_the_matching_aqi_level_first(fake_llm, fake_retrieval):
    """hybrid 的核心保證：對應 AQI 等級的規則必須排在第一位。"""
    result = generate_advice(
        county="測試", aqi=170, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="hybrid",
    )
    assert result["retrieved_rules"][0] == "aqi_unhealthy"


def test_hybrid_does_not_duplicate_the_level_rule(fake_llm, fake_retrieval):
    """語意檢索已命中等級規則時，置頂後不得出現兩次。"""
    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="hybrid",
    )
    ids = result["retrieved_rules"]
    assert len(ids) == len(set(ids))
    assert ids[0] == "aqi_moderate"


def test_hybrid_injects_fire_rule_when_event_mentions_fire(fake_llm, monkeypatch):
    seen = []

    def _query(query_text, n_results=3):
        seen.append(query_text)
        if "fire" in query_text:
            return [{"id": "event_fire_smoke", "document": "火災規則"}]
        return [{"id": "aqi_moderate", "document": "普通"}]

    monkeypatch.setattr(engine, "query_knowledge_base", _query)

    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), event_description="附近工廠火災濃煙",
        retrieval_mode="hybrid",
    )
    assert "event_fire_smoke" in result["retrieved_rules"]


def test_hybrid_does_not_inject_fire_rule_without_fire_event(fake_llm, fake_retrieval):
    result = generate_advice(
        county="測試", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), event_description="無", retrieval_mode="hybrid",
    )
    assert len(fake_retrieval) == 1  # 只查一次，沒有額外的火災檢索


def test_default_retrieval_mode_is_hybrid(fake_llm, fake_retrieval):
    default = generate_advice(
        county="測試", aqi=170, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(),
    )
    explicit = generate_advice(
        county="測試", aqi=170, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), retrieval_mode="hybrid",
    )
    assert default["retrieved_rules"] == explicit["retrieved_rules"]


# ── Prompt 組裝 ──────────────────────────────────────────────────────────────

def test_prompt_contains_the_key_context_fields(fake_llm, fake_retrieval):
    generate_advice(
        county="台南市", aqi=130, pm25=45.6, wind_speed=3.0, wind_direction=90.0,
        user_profile=_profile(has_asthma=True), event_description="工廠濃煙",
        is_downwind=True, temperature=34.0, weather_desc="晴", is_raining=False,
    )
    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert "台南市" in prompt
    assert "130" in prompt
    assert "工廠濃煙" in prompt
    assert "下風處" in prompt


def test_downwind_flag_changes_the_prompt(fake_llm, fake_retrieval):
    kwargs = dict(county="台南市", aqi=90, pm25=20.0, wind_speed=3.0,
                  wind_direction=90.0, user_profile=_profile())
    generate_advice(**kwargs, is_downwind=False)
    generate_advice(**kwargs, is_downwind=True)
    assert fake_llm.calls[0]["messages"][1]["content"] != \
           fake_llm.calls[1]["messages"][1]["content"]


def test_rain_is_surfaced_in_the_prompt(fake_llm, fake_retrieval):
    generate_advice(
        county="台南市", aqi=40, pm25=10.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), is_raining=True, weather_desc="陰",
    )
    assert "下雨" in fake_llm.calls[0]["messages"][1]["content"]


def test_forecast_absent_is_labelled_explicitly(fake_llm, fake_retrieval):
    generate_advice(
        county="台南市", aqi=40, pm25=10.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(), forecast_aqi=0,
    )
    assert "無預報資料" in fake_llm.calls[0]["messages"][1]["content"]


def test_generation_is_length_constrained(fake_llm, fake_retrieval):
    """建議要顯示在 App 卡片上，max_tokens 必須維持小值以控制長度與成本。"""
    generate_advice(
        county="台南市", aqi=40, pm25=10.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(),
    )
    assert fake_llm.calls[0]["max_tokens"] <= 120


# ── LLM 失敗時的 fallback ───────────────────────────────────────────────────

def test_generate_advice_falls_back_when_llm_raises(monkeypatch, fake_retrieval):
    class _Boom:
        def create(self, **kwargs):
            raise RuntimeError("upstream 503")

    class _Chat:
        pass

    chat = _Chat()
    chat.completions = _Boom()

    class _Client:
        pass

    client = _Client()
    client.chat = chat
    monkeypatch.setattr(engine, "_client", client)

    result = generate_advice(
        county="台南市", aqi=170, pm25=60.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(),
    )
    assert result["advice"]
    assert result["error"] == "upstream 503"
    # aqi_level 取自 health_rules（目前是「不健康」），
    # 與 _aqi_to_status 的「對所有族群不健康」不一致 —— 見 test_health_rules.py
    assert result["aqi_level"] == "不健康"


def test_fallback_never_returns_empty_advice():
    for aqi in (0, 50, 51, 100, 101, 150, 151, 300, 500):
        advice = _fallback_advice(aqi, _aqi_to_status(aqi), _profile(), "無")
        assert advice and advice.strip()


def test_fallback_mentions_the_event_when_present():
    advice = _fallback_advice(40, "良好", _profile(), "附近工廠火災")
    assert "附近工廠火災" in advice


def test_fallback_never_encourages_outdoor_activity_during_an_event():
    """安全優先：有污染事件時，即使 AQI 良好也不得鼓勵外出。"""
    advice = _fallback_advice(20, "良好", _profile(), "附近工廠火災濃煙")
    assert "適合戶外活動" not in advice
    assert "盡情享受" not in advice


def test_fallback_is_stricter_for_sensitive_users():
    general = _fallback_advice(130, "對敏感族群不健康", _profile(), "無")
    asthma = _fallback_advice(130, "對敏感族群不健康", _profile(has_asthma=True), "無")
    assert general != asthma
    assert "口罩" in asthma


@pytest.mark.parametrize(
    "profile_kwargs",
    [
        {"has_asthma": True},
        {"has_cardiovascular": True},
        {"is_pregnant": True},
        {"age_group": "child"},
        {"age_group": "elderly"},
    ],
)
def test_all_sensitive_groups_get_the_sensitive_fallback(profile_kwargs):
    advice = _fallback_advice(130, "對敏感族群不健康", _profile(**profile_kwargs), "無")
    assert "您的健康狀況" in advice


def test_generate_advice_result_shape_is_stable(fake_llm, fake_retrieval):
    """API 回應直接展開這個 dict，欄位不得缺漏。"""
    result = generate_advice(
        county="台南市", aqi=85, pm25=20.0, wind_speed=1.0, wind_direction=0.0,
        user_profile=_profile(),
    )
    assert set(result) == {"advice", "aqi_level", "retrieved_rules", "error"}
    assert result["error"] is None
