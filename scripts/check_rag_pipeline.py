"""
check_rag_pipeline.py — RAG 管道驗證腳本
執行方式：python scripts/check_rag_pipeline.py

刻意不叫 test_*.py：這支的邏輯寫在模組層級，pytest 光是 import 就會整支
執行——重建知識庫並打一輪 LLM，會實際產生 OpenRouter 費用。

自動化測試請看 test_auth.py。
"""
import json
import os
import sys

# 讓腳本無論從哪個目錄執行都能匯入專案模組
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

# ─── 測試 1：知識庫建立 ─────────────────────────────────────────────────────
print("=" * 50)
print("🧪 測試 1：知識庫建立")
print("=" * 50)
from rag.embedder import build_knowledge_base
count = build_knowledge_base(force_rebuild=True)
print(f"✅ 知識庫共 {count} 筆規則\n")

# ─── 測試 2：LLM 語意結構化 ──────────────────────────────────────────────────
print("=" * 50)
print("🧪 測試 2：LLM 語意結構化")
print("=" * 50)
from rag.llm_structurer import structure_news_event
result = structure_news_event({
    "title": "台南安南區化工廠爆炸 黑煙竄天臭味瀰漫",
    "summary": "台南市安南區一間化工廠今午突然爆炸起火，現場黑煙沖天，異味飄散數公里遠，周邊居民紛紛反映眼睛不適。",
    "region": "台南市安南區",
})
se = result.get("structured_event", {})
print(json.dumps(se, ensure_ascii=False, indent=2))
print()

# ─── 測試 3：RAG 建議生成 ────────────────────────────────────────────────────
print("=" * 50)
print("🧪 測試 3：RAG 個人化建議生成")
print("=" * 50)

from rag.rag_engine import generate_advice

# 情境 A：空氣良好，一般成人
print("【情境 A】AQI 35（良好），一般成人")
r = generate_advice(
    county="台北市",
    aqi=35,
    pm25=8.0,
    wind_speed=3.0,
    wind_direction=180,
    user_profile={
        "age_group": "adult",
        "is_pregnant": False,
        "has_asthma": False,
        "has_cardiovascular": False,
        "has_allergy": False,
    },
    event_description="無",
)
print(f"建議：{r['advice']}")
print(f"等級：{r['aqi_level']}  規則：{r['retrieved_rules']}\n")

# 情境 B：AQI 超標，氣喘患者，附近有火災
print("【情境 B】AQI 125（對敏感族群不健康），氣喘患者，附近有火災")
r = generate_advice(
    county="台中市",
    aqi=125,
    pm25=38.5,
    wind_speed=2.3,
    wind_direction=270,
    user_profile={
        "age_group": "adult",
        "is_pregnant": False,
        "has_asthma": True,
        "has_cardiovascular": False,
        "has_allergy": False,
    },
    event_description="北屯區塑膠工廠大火（fire，high）",
)
print(f"建議：{r['advice']}")
print(f"等級：{r['aqi_level']}  規則：{r['retrieved_rules']}\n")

# 情境 C：空氣惡化，孕婦
print("【情境 C】AQI 180（不健康），孕婦")
r = generate_advice(
    county="高雄市",
    aqi=180,
    pm25=65.0,
    wind_speed=1.0,
    wind_direction=90,
    user_profile={
        "age_group": "adult",
        "is_pregnant": True,
        "has_asthma": False,
        "has_cardiovascular": False,
        "has_allergy": False,
    },
    event_description="無",
)
print(f"建議：{r['advice']}")
print(f"等級：{r['aqi_level']}  規則：{r['retrieved_rules']}\n")

print("=" * 50)
print("✅ 所有測試完成！")
print("=" * 50)
