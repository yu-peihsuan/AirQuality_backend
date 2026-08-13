"""RAG 管道整合測試 —— 需要真實 API 金鑰與 chromadb。

    pytest -m network tests/integration/

**預設不執行**（pytest.ini 的 addopts 帶 `-m "not network"`），因為會產生費用：
每次執行呼叫一次 embedding API 與三次 LLM。CI 也不跑。

這裡驗的是單元測試「刻意不驗」的那一段：真實 embedding、真實向量檢索、
真實 LLM 連得通，且回傳結構符合預期。前身是根目錄的 test_rag.py 手動腳本
（現為 scripts/rag_smoke.py），差別在於這裡有斷言。

刻意不斷言建議文字的內容：LLM 以 temperature=0.7 生成，
逐字比對必然 flaky。要看輸出品質請跑 scripts/rag_smoke.py 用眼睛看。
"""

import os

import pytest

pytestmark = pytest.mark.network


_REAL_KEY = os.getenv("OPENROUTER_API_KEY", "")
_needs_key = pytest.mark.skipif(
    not _REAL_KEY or _REAL_KEY.startswith("test-"),
    reason="需要真實的 OPENROUTER_API_KEY",
)

_BASE_PROFILE = {
    "age_group": "adult",
    "is_pregnant": False,
    "has_asthma": False,
    "has_cardiovascular": False,
    "has_allergy": False,
}


@_needs_key
def test_knowledge_base_builds_and_contains_every_rule():
    from rag.embedder import build_knowledge_base
    from rag.health_rules import AQI_HEALTH_RULES

    count = build_knowledge_base(force_rebuild=True)
    assert count == len(AQI_HEALTH_RULES)


@_needs_key
def test_semantic_retrieval_returns_relevant_rules():
    from rag.embedder import query_knowledge_base

    results = query_knowledge_base("氣喘患者 AQI 130 該注意什麼", n_results=3)
    assert len(results) == 3
    for r in results:
        assert {"id", "document", "metadata", "distance"} <= set(r)
    # cosine 距離應落在合理範圍，全 0 或全 1 代表 embedding 出問題
    assert all(0.0 <= r["distance"] <= 2.0 for r in results)


@_needs_key
def test_news_structuring_identifies_a_real_pollution_event():
    from rag.llm_structurer import structure_news_event

    result = structure_news_event({
        "title": "台南安南區化工廠爆炸 黑煙竄天臭味瀰漫",
        "summary": "台南市安南區一間化工廠今午突然爆炸起火，現場黑煙沖天，"
                   "異味飄散數公里遠，周邊居民紛紛反映眼睛不適。",
        "region": "台南市安南區",
    })

    se = result["structured_event"]
    assert se is not None, "LLM 回傳了無法解析的 JSON"
    assert se["is_confirmed_pollution_event"] is True
    assert se["event_type"] in ("fire", "chemical")


@_needs_key
def test_news_structuring_rejects_a_non_event():
    from rag.llm_structurer import structure_news_event

    result = structure_news_event({
        "title": "空污議題紀錄片本週上映 導演談十年拍攝歷程",
        "summary": "該片記錄台灣中部空污問題十年來的變化。",
        "region": "台中市",
    })
    se = result["structured_event"]
    assert se is not None
    assert se["is_confirmed_pollution_event"] is False


@_needs_key
@pytest.mark.parametrize(
    "label, kwargs",
    [
        ("良好-一般成人",
         dict(county="台北市", aqi=35, pm25=8.0, wind_speed=3.0, wind_direction=180,
              user_profile=_BASE_PROFILE, event_description="無")),
        ("敏感族群不健康-氣喘-有火災",
         dict(county="台中市", aqi=125, pm25=38.5, wind_speed=2.3, wind_direction=270,
              user_profile={**_BASE_PROFILE, "has_asthma": True},
              event_description="北屯區塑膠工廠大火（fire，high）")),
        ("不健康-孕婦",
         dict(county="高雄市", aqi=180, pm25=65.0, wind_speed=1.0, wind_direction=90,
              user_profile={**_BASE_PROFILE, "is_pregnant": True},
              event_description="無")),
    ],
)
def test_advice_generation_succeeds_for_representative_scenarios(label, kwargs):
    from rag.rag_engine import generate_advice

    result = generate_advice(**kwargs)

    assert result["error"] is None, f"{label}：LLM 呼叫失敗，落到 fallback"
    assert result["advice"].strip(), f"{label}：建議為空"
    assert result["retrieved_rules"], f"{label}：沒有檢索到任何規則"
    # prompt 要求 20–35 字；放寬上限只為擋住「整段跑掉」的情況
    assert len(result["advice"]) <= 120, f"{label}：建議過長（{len(result['advice'])} 字）"


@_needs_key
def test_advice_is_grounded_in_the_matching_aqi_level():
    """hybrid 檢索必須把對應 AQI 等級的規則排在第一位（真實檢索下也成立）。"""
    from rag.rag_engine import generate_advice

    result = generate_advice(
        county="高雄市", aqi=180, pm25=65.0, wind_speed=1.0, wind_direction=90,
        user_profile=_BASE_PROFILE, event_description="無",
    )
    assert result["retrieved_rules"][0] == "aqi_unhealthy"
