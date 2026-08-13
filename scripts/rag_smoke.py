"""RAG 管道手動煙霧測試：用真實金鑰跑一遍，肉眼檢查建議品質。

    python scripts/rag_smoke.py

⚠️  這會呼叫**真實 API**並產生費用：
    - 以 force_rebuild=True 重建整個向量知識庫（真實 embedding API）
    - 呼叫 LLM 4 次（1 次結構化 + 3 個建議情境）

不是測試，是給人看輸出品質用的 —— 因此刻意不放在 tests/，
檔名也不以 test_ 開頭：原本叫 test_rag.py 時，模組層程式碼會在 pytest
**收集階段**就執行，連 --collect-only 都會觸發真實 API 呼叫與知識庫重建。

自動化版本：
    tests/test_rag_engine.py                    檢索策略與 prompt 組裝（mock LLM）
    tests/integration/test_rag_pipeline.py      真實 API 連通性（需 -m network）
"""

import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
load_dotenv()

# ─── 1：知識庫建立 ───────────────────────────────────────────────────────────
print("=" * 50)
print("🧪 1：知識庫建立")
print("=" * 50)
from rag.embedder import build_knowledge_base

count = build_knowledge_base(force_rebuild=True)
print(f"✅ 知識庫共 {count} 筆規則\n")

# ─── 2：LLM 語意結構化 ──────────────────────────────────────────────────────
print("=" * 50)
print("🧪 2：LLM 語意結構化")
print("=" * 50)
from rag.llm_structurer import structure_news_event

result = structure_news_event({
    "title": "台南安南區化工廠爆炸 黑煙竄天臭味瀰漫",
    "summary": "台南市安南區一間化工廠今午突然爆炸起火，現場黑煙沖天，異味飄散數公里遠，周邊居民紛紛反映眼睛不適。",
    "region": "台南市安南區",
})
print(json.dumps(result.get("structured_event", {}), ensure_ascii=False, indent=2))
print()

# ─── 3：RAG 建議生成 ────────────────────────────────────────────────────────
print("=" * 50)
print("🧪 3：RAG 個人化建議生成")
print("=" * 50)

from rag.rag_engine import generate_advice

_BASE_PROFILE = {
    "age_group": "adult",
    "is_pregnant": False,
    "has_asthma": False,
    "has_cardiovascular": False,
    "has_allergy": False,
}

_SCENARIOS = [
    ("A", "AQI 35（良好），一般成人",
     dict(county="台北市", aqi=35, pm25=8.0, wind_speed=3.0, wind_direction=180,
          user_profile=_BASE_PROFILE, event_description="無")),
    ("B", "AQI 125（對敏感族群不健康），氣喘患者，附近有火災",
     dict(county="台中市", aqi=125, pm25=38.5, wind_speed=2.3, wind_direction=270,
          user_profile={**_BASE_PROFILE, "has_asthma": True},
          event_description="北屯區塑膠工廠大火（fire，high）")),
    ("C", "AQI 180（不健康），孕婦",
     dict(county="高雄市", aqi=180, pm25=65.0, wind_speed=1.0, wind_direction=90,
          user_profile={**_BASE_PROFILE, "is_pregnant": True},
          event_description="無")),
]

for label, description, kwargs in _SCENARIOS:
    print(f"【情境 {label}】{description}")
    r = generate_advice(**kwargs)
    print(f"建議：{r['advice']}")
    print(f"等級：{r['aqi_level']}  規則：{r['retrieved_rules']}\n")

print("=" * 50)
print("✅ 全部執行完成")
print("=" * 50)
