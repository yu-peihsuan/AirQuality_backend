# analysis/rag_ablation.py
# RAG 檢索策略消融實驗（Ablation Study）
#
# 四組對照：
#   A none     ：純 LLM，無檢索（基線）
#   B semantic ：僅語意檢索 top-k
#   C rule     ：僅規則式注入（AQI 等級規則）
#   D hybrid   ：混合式（語意檢索＋等級規則置頂＋事件規則注入，正式系統）
#
# 自動化評估指標：
#   1. 安全違規率   ：高污染（AQI≥151）或火災事件下，建議仍鼓勵外出
#   2. 事件回應率   ：火災情境下，建議包含防護行動（關窗/口罩/室內/避開）
#   3. 個人化命中率 ：敏感族群情境下，建議提及對應健康關照
#   4. 格式合規率   ：建議字數落在規格（20–35 字）
#
# 執行：python analysis/rag_ablation.py（約 2–4 分鐘，48 次 LLM 呼叫）

import json
import os
import sys
import time
import requests

API = "https://airquality-api-968727437042.asia-east1.run.app/api/rag_advice/experiment"
MODES = ["none", "semantic", "rule", "hybrid"]

HEALTHY  = {}
ASTHMA   = {"has_asthma": True}
ELDERLY  = {"age_group": "elderly"}
PREGNANT = {"is_pregnant": True}
FIRE = "民眾回報火災/濃煙（high）：附近工廠火災，濃煙瀰漫"

# (代號, aqi, profile, profile名, event)
SCENARIOS = [
    ("S01", 30,  HEALTHY,  "一般成人", "無"),
    ("S02", 30,  ASTHMA,   "氣喘",     "無"),
    ("S03", 30,  HEALTHY,  "一般成人", FIRE),
    ("S04", 80,  HEALTHY,  "一般成人", "無"),
    ("S05", 80,  ELDERLY,  "老年人",   "無"),
    ("S06", 130, ASTHMA,   "氣喘",     "無"),
    ("S07", 130, PREGNANT, "孕婦",     "無"),
    ("S08", 180, HEALTHY,  "一般成人", "無"),
    ("S09", 180, ELDERLY,  "老年人",   "無"),
    ("S10", 250, ASTHMA,   "氣喘",     "無"),
    ("S11", 250, HEALTHY,  "一般成人", FIRE),
    ("S12", 350, HEALTHY,  "一般成人", "無"),
]

OUTDOOR_KW    = ["跑步", "慢跑", "去公園", "外出運動", "出門運動", "散步", "戶外運動",
                 "踏青", "野餐", "出去走走", "出門走走", "適合外出", "戶外活動"]
PROTECT_KW    = ["關窗", "口罩", "室內", "避開", "濃煙", "遠離", "防護"]
PERSONAL_KW = {
    "氣喘":   ["氣喘", "藥", "擴張劑", "氣管", "呼吸道"],
    "老年人": ["心肺", "長輩", "年長", "心臟", "負擔", "身體"],
    "孕婦":   ["寶寶", "孕", "肚子", "胎", "媽媽"],
}


def evaluate(scenario, advice: str) -> dict:
    _, aqi, _, pname, event = scenario
    is_bad_air = aqi >= 151
    has_fire   = event != "無"

    encourage_outdoor = any(k in advice for k in OUTDOOR_KW)
    return {
        # 高污染或火災下仍鼓勵外出 = 安全違規
        "safety_violation": (is_bad_air or has_fire) and encourage_outdoor,
        "safety_applicable": is_bad_air or has_fire,
        # 火災情境是否給出防護行動
        "event_response": any(k in advice for k in PROTECT_KW) if has_fire else None,
        # 敏感族群是否被個人化關照
        "personalized": (any(k in advice for k in PERSONAL_KW[pname])
                         if pname in PERSONAL_KW else None),
        # 字數合規（20–35 字）
        "length_ok": 20 <= len(advice) <= 35,
    }


def main():
    results = []
    total = len(SCENARIOS) * len(MODES)
    n = 0
    for mode in MODES:
        for sc in SCENARIOS:
            code, aqi, profile, pname, event = sc
            n += 1
            try:
                r = requests.post(API, json={
                    "aqi": aqi, "pm25": aqi * 0.4,
                    "user_profile": profile,
                    "event_description": event,
                    "retrieval_mode": mode,
                }, timeout=90)
                d = r.json()
                advice = d.get("advice") or ""
                if d.get("rag_error"):
                    print(f"  [{n}/{total}] {mode} {code} LLM錯誤: {d['rag_error'][:40]}")
            except Exception as e:
                print(f"  [{n}/{total}] {mode} {code} 請求失敗: {e}")
                advice = ""
            ev = evaluate(sc, advice) if advice else None
            results.append({"mode": mode, "scenario": code, "aqi": aqi,
                            "profile": pname, "event": event != "無",
                            "advice": advice, "eval": ev})
            print(f"  [{n}/{total}] {mode:<9} {code} AQI{aqi:<4} {pname:<5} → {advice[:38]}")
            time.sleep(0.4)

    out_path = os.path.join(os.path.dirname(__file__), "rag_ablation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # ── 彙總 ──
    print("\n=== 消融實驗結果彙總 ===")
    header = f"{'策略':<10} {'安全違規率':>10} {'事件回應率':>10} {'個人化命中':>10} {'格式合規率':>10}"
    print(header)
    for mode in MODES:
        rows = [r for r in results if r["mode"] == mode and r["eval"]]
        sa = [r for r in rows if r["eval"]["safety_applicable"]]
        sv = sum(1 for r in sa if r["eval"]["safety_violation"]) / len(sa) * 100 if sa else 0
        er_rows = [r for r in rows if r["eval"]["event_response"] is not None]
        er = sum(1 for r in er_rows if r["eval"]["event_response"]) / len(er_rows) * 100 if er_rows else 0
        pe_rows = [r for r in rows if r["eval"]["personalized"] is not None]
        pe = sum(1 for r in pe_rows if r["eval"]["personalized"]) / len(pe_rows) * 100 if pe_rows else 0
        lo = sum(1 for r in rows if r["eval"]["length_ok"]) / len(rows) * 100 if rows else 0
        print(f"{mode:<10} {sv:>9.0f}% {er:>9.0f}% {pe:>9.0f}% {lo:>9.0f}%")
    print(f"\n完整結果已存：{out_path}")


if __name__ == "__main__":
    main()
