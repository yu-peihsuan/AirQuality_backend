# rag/rag_engine.py
# RAG 引擎：混合式檢索 + GPT-4o-mini 生成個人化空氣品質建議

import os
from openai import OpenAI

from core.timeutil import now_tw
from rag.embedder import query_knowledge_base
from rag.health_rules import get_rule_by_aqi, get_rule_by_id

_client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY", ""),
    base_url="https://openrouter.ai/api/v1",
)

# ── 個人化建議 Prompt ────────────────────────────────────────────────────────
#
# 資料區塊中英文共用（值本來就是中文：縣市名、事件描述、檢索到的健康指引），
# 只有「角色設定」與「輸出規則／範例」分語言——輸出語言不同，字數限制、
# 禁用句型與範例語氣都得跟著換，混在同一份模板裡兩邊都寫不好。

_ROLE_ZH = """# Role: 貼心且專業的生活顧問
你是一位像朋友一樣親切的空氣品質顧問。你的任務是根據使用者的「健康檔案」、「當前 AQI」與「空品預報」，給出一句最實用、貼近生活的行動建議。"""

_ROLE_EN = """# Role: a warm, practical air-quality advisor
You are an air-quality advisor who talks like a friend. Based on the user's health profile, the current AQI and the forecast, give one practical, down-to-earth piece of advice they can act on today.

Note: the data below is written in Traditional Chinese (place names, nearby incidents, health guidelines). Read it as reference material — your answer must be in English."""

_DATA_BLOCK = """# 當前數據
- 地點：{county}　AQI：{aqi}（{aqi_level}）　PM2.5：{pm25} µg/m³
- 現在時間：{current_time}
- 天氣狀況：{weather_info}
- 天氣預報：{weather_forecast}
- 附近污染事件：{event_description}
- 下風處警告：{downwind_warning}
- 空品預報：{forecast_info}

# 用戶健康檔案
- 年齡層：{age_group}　孕婦：{is_pregnant}　氣喘：{has_asthma}　心血管疾病：{has_cardiovascular}　過敏：{has_allergy}
- 其他說明：{other_notes}

# 相關健康指引（參考資料）
{retrieved_knowledge}"""

_RULES_ZH = """# Core Rules（嚴格遵守）
1. 語氣設定：繁體中文，語氣像朋友在傳 LINE 訊息，親切口語。
2. 字數限制：嚴格控制在「1 句話，20～35 字之間」，精簡有力，絕不廢話。
3. 絕對禁用：絕對不能出現「目前空氣品質為...」「AQI 數值...」「對敏感族群...」「根據健康指引...」「建議您...」「請注意...」等字眼。
4. 行動具體化：建議必須具體可執行。寫「戴 N95 出門」取代「注意防護」，寫「今天適合去公園跑步」取代「空氣良好可外出」。

# Execution Strategy
-1. 安全優先（最高優先級）：若「附近污染事件」不是「無」（火災、濃煙、化學異味、下風處警告等），整句建議只圍繞防護行動（關窗、戴口罩、避開該區域），**嚴禁**同時出現「適合外出運動／跑步／散步」等鼓勵外出字句 — 即使 AQI 良好也一樣，因為測站數據反映不了局部污染事件。
0. 時間與天氣感知：深夜／凌晨（22:00–06:00）不建議外出活動；正在下雨時即使空氣好也不建議戶外運動；氣溫 ≥ 33°C 提醒防曬補水；氣溫 ≤ 10°C 提醒保暖與心肺負擔。
1. 切入角度：直接從「健康狀況」或「生活場景」開門見山。
   - 氣喘患者：提醒備藥或避免誘發。
   - 孩童/孕婦：提醒家長留意或調整行程。
   - 老年人：提醒心肺負擔。
   - 一般成人（無特殊狀況）：直接點出當下適合或不適合做什麼事。
2. 動態預報：若空品預報與現在有明顯差異，在句尾自然補充。
3. 結合環境：若處於下風處，適時提醒風向帶來的污染影響。

# Examples（僅示意場景與風格，**嚴禁照抄**，每次都必須用不同的說法表達）
Input: [一般成人, AQI 35, 下雨]
Output: 雖然空氣不錯，但雨還在下，出門不方便，在家伸展一下或做點輕運動也很好。

Input: [一般成人, AQI 35, 預報轉差]
Output: 今天超適合去河堤跑步的，不過下午空氣會變糟，想運動的話趁早上趕快出門吧！

Input: [氣喘患者, AQI 130]
Output: 空氣對氣管不太友善，出門前記得帶擴張劑，口罩也要戴上喔。

Input: [老年人, AQI 160]
Output: 今天心肺壓力比較大，盡量待室內，非得出門就戴 N95 再走。

Input: [孕婦, AQI 80, 預報改善]
Output: 現在空氣還可以，散步沒問題，等等空氣會更好，到時候再出門也很棒！

Input: [一般成人, AQI 170, 下風處]
Output: 附近有污染源，風正往你這邊吹，今天出門口罩不能少。

Input: [一般成人, AQI 30, 附近有火災濃煙]
Output: 附近有火警濃煙飄散，先關窗待在室內，非得出門就戴口罩繞開那一帶。"""

_RULES_EN = """# Core Rules (follow strictly)
1. Voice: English, casual and warm — the way you would text a friend.
2. Length: exactly one sentence, 15-25 words. Tight and useful, no filler.
3. Never write: "The current air quality is...", "The AQI value...", "For sensitive groups...", "According to the health guidelines...", "It is recommended that you...", "Please note...".
4. Be concrete: say "wear an N95 if you head out" instead of "take precautions", say "great day for a run in the park" instead of "air quality is good, outdoor activity is fine".

# Execution Strategy
-1. Safety first (highest priority): if "附近污染事件" is anything other than "無" (fire, smoke, chemical odour, downwind warning, ...), the whole sentence must be about protective action (close windows, mask up, avoid the area). Never encourage going out to exercise, run or walk in the same sentence — not even when the AQI looks good, because station readings do not capture a local incident.
0. Time and weather awareness: do not suggest going out late at night or before dawn (22:00-06:00); do not suggest outdoor exercise while it is raining, even in clean air; at 33°C or above mention sun protection and hydration; at 10°C or below mention staying warm and the load on heart and lungs.
1. Angle: open straight from the user's health situation or from a concrete everyday scene.
   - Asthma: remind them to carry their inhaler or avoid triggers.
   - Children / pregnancy: prompt the parent or the user to adjust plans.
   - Older adults: mention the strain on heart and lungs.
   - Healthy adults: just say what today is or is not good for.
2. Forecast shift: if the forecast differs clearly from now, add it naturally at the end.
3. Environment: if they are downwind of a source, work the wind direction into the advice.

# Examples (scene and tone only — **never copy them**, phrase it differently every time)
Input: [healthy adult, AQI 35, raining]
Output: Air is clean but it is still raining, so stretch or do something light indoors today instead.

Input: [healthy adult, AQI 35, forecast worsening]
Output: Perfect morning for a run along the river, though the air turns bad this afternoon, so go early.

Input: [asthma, AQI 130]
Output: Rough air for your airways today, so keep your inhaler on you and wear a mask outside.

Input: [older adult, AQI 160]
Output: Today is hard on the heart and lungs, so stay in, and put on an N95 if you must go out.

Input: [pregnant, AQI 80, forecast improving]
Output: A short walk is fine right now, and the air clears up later if you would rather wait.

Input: [healthy adult, AQI 170, downwind]
Output: There is a source upwind of you and the wind is heading your way, so do not skip the mask.

Input: [healthy adult, AQI 30, fire and smoke nearby]
Output: Smoke from a fire nearby, so shut the windows, and mask up and skip that block if you go out."""

_ADVICE_PROMPTS = {
    "zh": "\n\n".join([_ROLE_ZH, _DATA_BLOCK, _RULES_ZH]),
    "en": "\n\n".join([_ROLE_EN, _DATA_BLOCK, _RULES_EN]),
}

_SYSTEM_MESSAGES = {
    "zh": "你是空氣品質健康顧問，只以繁體中文回覆，語氣友善親切。",
    "en": "You are an air quality health advisor. Reply in English only, in a friendly, down-to-earth voice.",
}

#: 支援的建議語言；其餘輸入一律退回繁體中文。
SUPPORTED_LANGS = ("zh", "en")


def normalize_lang(lang: str | None) -> str:
    """把客戶端送來的語言標籤收斂成 SUPPORTED_LANGS 之一。

    Android 送的是 BCP-47（zh-TW、en-US），所以只看主要子標籤；
    認不得的值一律當中文，不讓未知語系把使用者推到英文。
    """
    if not lang:
        return "zh"
    primary = lang.strip().lower().replace("_", "-").split("-")[0]
    return primary if primary in SUPPORTED_LANGS else "zh"


def _aqi_to_status(aqi: int) -> str:
    if aqi <= 50:  return "良好"
    if aqi <= 100: return "普通"
    if aqi <= 150: return "對敏感族群不健康"
    if aqi <= 200: return "對所有族群不健康"
    if aqi <= 300: return "非常不健康"
    return "危害"


def _describe_user_profile(profile: dict) -> dict:
    """將健康檔案 dict 轉為可讀描述"""
    age_map = {
        "child": "孩童（12歲以下）",
        "adult": "成人",
        "elderly": "老年人（65歲以上）",
    }
    other = profile.get("other_notes") or ""
    return {
        "age_group": age_map.get(profile.get("age_group", "adult"), "成人"),
        "is_pregnant": "是" if profile.get("is_pregnant") else "否",
        "has_asthma": "是" if profile.get("has_asthma") else "否",
        "has_cardiovascular": "是" if profile.get("has_cardiovascular") else "否",
        "has_allergy": "是" if profile.get("has_allergy") else "否",
        "other_notes": other if other else "無",
    }


def _build_retrieved_context(retrieved: list[dict], profile: dict) -> str:
    """將檢索結果展開為完整的健康規則內容，依使用者健康狀況選擇對應建議"""
    is_sensitive = any([
        profile.get("has_asthma"),
        profile.get("has_cardiovascular"),
        profile.get("is_pregnant"),
        profile.get("age_group") in ("child", "elderly"),
    ])

    # 依健康狀況對應 special_groups 的 key
    group_keys = []
    if profile.get("has_asthma"):
        group_keys.append("氣喘患者")
    if profile.get("has_cardiovascular"):
        group_keys.append("心肺疾病患者")
    if profile.get("is_pregnant"):
        group_keys.append("孕婦")
    if profile.get("age_group") == "child":
        group_keys.append("孩童")
    if profile.get("age_group") == "elderly":
        group_keys.append("老年人")

    parts = []
    for r in retrieved:
        full_rule = get_rule_by_id(r["id"])
        if not full_rule:
            parts.append(f"- {r['document']}")
            continue

        section = [f"【{full_rule['level']}】"]
        if full_rule.get("health_effects"):
            section.append(f"健康影響：{full_rule['health_effects']}")
        if is_sensitive and full_rule.get("advice_sensitive"):
            section.append(f"敏感族群建議：{full_rule['advice_sensitive']}")
        else:
            if full_rule.get("advice_general"):
                section.append(f"一般建議：{full_rule['advice_general']}")

        special = full_rule.get("special_groups", {})
        for key in group_keys:
            for sk, sv in special.items():
                if key in sk or sk in key:
                    section.append(f"{sk}專屬建議：{sv}")
                    break

        parts.append("\n".join(section))

    return "\n\n".join(parts)


def _build_query_text(aqi: int, profile: dict, event_description: str) -> str:
    """組合 RAG 查詢語句，融合 AQI、用戶特徵與事件描述"""
    parts = [f"AQI {aqi}"]

    if profile.get("has_asthma"):
        parts.append("氣喘患者")
    if profile.get("has_cardiovascular"):
        parts.append("心血管疾病")
    if profile.get("is_pregnant"):
        parts.append("孕婦")
    if profile.get("age_group") == "child":
        parts.append("孩童")
    if profile.get("age_group") == "elderly":
        parts.append("老年人")
    if event_description and event_description != "無":
        parts.append(event_description)

    return " ".join(parts)


def generate_advice(
    county: str,
    aqi: int,
    pm25: float,
    wind_speed: float,
    wind_direction: float,
    user_profile: dict,
    event_description: str = "無",
    is_downwind: bool = False,
    forecast_aqi: int = 0,
    forecast_status: str = "",
    temperature: float = 0.0,
    weather_desc: str = "",
    is_raining: bool = False,
    weather_forecast: str = "",
    retrieval_mode: str = "hybrid",
    lang: str = "zh",
) -> dict:
    """
    核心 RAG 生成函式。
    
    Parameters:
        county: 縣市名稱（用於顯示）
        aqi: 當前 AQI 數值
        pm25: PM2.5 濃度（µg/m³）
        wind_speed: 風速（m/s）
        wind_direction: 風向（度，0=北，90=東）
        user_profile: 用戶健康檔案 dict
        event_description: 附近污染事件描述（若有）
        lang: 輸出語言（zh / en），只影響生成的 advice 文字；
              aqi_level 一律回傳中文等級名稱，由客戶端自行本地化。

    Returns:
        dict with keys: advice, aqi_level, retrieved_rules, error
    """
    # 1. 取得 AQI 等級名稱
    rule = get_rule_by_aqi(aqi)
    aqi_level = rule["level"] if rule else "未知"

    # 2. 知識檢索（retrieval_mode 供消融實驗切換策略；正式服務固定 hybrid）
    #    none     ：不檢索（純 LLM 基線）
    #    semantic ：僅語意檢索 top-k
    #    rule     ：僅規則式注入（AQI 等級對應規則）
    #    hybrid   ：語意檢索 + 等級規則置頂 + 事件規則注入（正式模式）
    query_text = _build_query_text(aqi, user_profile, event_description)

    if retrieval_mode == "none":
        retrieved = []
    elif retrieval_mode == "rule":
        retrieved = [{"id": rule["id"], "document": rule.get("text", "")}] if rule else []
    elif retrieval_mode == "semantic":
        retrieved = query_knowledge_base(query_text, n_results=3)
    else:  # hybrid
        retrieved = query_knowledge_base(query_text, n_results=3)

        # 強制將對應 AQI 等級規則移到第一位（alignment：確保正確等級排序優先）
        if rule:
            retrieved = [r for r in retrieved if r["id"] != rule["id"]]
            retrieved.insert(0, {"id": rule["id"], "document": rule.get("text", "")})

        # 若有事件且是火災，強制加入火災規則
        if "火災" in event_description or "濃煙" in event_description or "fire" in event_description.lower():
            event_retrieved = query_knowledge_base("火災濃煙 fire smoke PM2.5", n_results=1)
            for er in event_retrieved:
                if er["id"] not in [r["id"] for r in retrieved]:
                    retrieved.append(er)

    # 3. 整理檢索結果供 Prompt 使用（含完整 health_effects / advice / special_groups）
    retrieved_texts = _build_retrieved_context(retrieved, user_profile)

    # 4. 組合個人化 Prompt
    profile_desc = _describe_user_profile(user_profile)
    downwind_warning = "是，您目前位於污染熱點的下風處，污染物可能隨風飄向您所在位置，請特別注意防護。" if is_downwind else "否"
    if forecast_aqi > 0:
        forecast_info = f"預報 AQI {forecast_aqi}（{forecast_status or _aqi_to_status(forecast_aqi)}）"
    else:
        forecast_info = "無預報資料"
    now = now_tw()
    hour = now.hour
    if 0 <= hour < 6:
        time_label = "深夜／凌晨"
    elif hour < 12:
        time_label = "上午"
    elif hour < 14:
        time_label = "中午"
    elif hour < 18:
        time_label = "下午"
    elif hour < 22:
        time_label = "傍晚／晚上"
    else:
        time_label = "深夜"
    current_time = f"{now.strftime('%H:%M')}（{time_label}）"
    rain_note = "（⚠️ 目前正在下雨）" if is_raining else ""
    if weather_desc:
        weather_info = f"{weather_desc}{rain_note}"
    elif temperature > 0:
        if temperature >= 33:   temp_label = "高溫炎熱"
        elif temperature >= 28: temp_label = "偏熱"
        elif temperature >= 15: temp_label = "舒適"
        elif temperature >= 10: temp_label = "偏涼"
        else:                   temp_label = "低溫寒冷"
        weather_info = f"{temperature}°C（{temp_label}）{rain_note}"
    else:
        weather_info = f"無資料{rain_note}"
    lang = normalize_lang(lang)
    prompt = _ADVICE_PROMPTS[lang].format(
        county=county,
        aqi=aqi,
        aqi_level=aqi_level,
        pm25=round(pm25, 1) if pm25 else "未知",
        wind_speed=round(wind_speed, 1) if wind_speed else "未知",
        wind_direction=round(wind_direction) if wind_direction else "未知",
        event_description=event_description if event_description else "無",
        current_time=current_time,
        weather_info=weather_info,
        weather_forecast=weather_forecast if weather_forecast else "無資料",
        downwind_warning=downwind_warning,
        forecast_info=forecast_info,
        retrieved_knowledge=retrieved_texts or "（無相關健康指引）",
        **profile_desc,
    )

    # 5. 呼叫 LLM 生成建議
    try:
        response = _client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": _SYSTEM_MESSAGES[lang],
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=60,
        )

        advice = response.choices[0].message.content.strip()

        return {
            "advice": advice,
            "aqi_level": aqi_level,
            "retrieved_rules": [r["id"] for r in retrieved],
            "error": None,
        }

    except Exception as e:
        print(f"⚠️  RAG 生成失敗：{e}")
        # Fallback：回傳基本建議
        fallback = _fallback_advice(aqi, aqi_level, user_profile, event_description, lang)
        return {
            "advice": fallback,
            "aqi_level": aqi_level,
            "retrieved_rules": [],
            "error": str(e),
        }


def _fallback_advice(
    aqi: int,
    aqi_level: str,
    profile: dict,
    event_description: str,
    lang: str = "zh",
) -> str:
    """LLM 失敗時的基本規則建議（不依賴 API）"""
    is_sensitive = any([
        profile.get("has_asthma"),
        profile.get("has_cardiovascular"),
        profile.get("is_pregnant"),
        profile.get("age_group") in ("child", "elderly"),
    ])
    has_event = bool(event_description) and event_description != "無"

    if normalize_lang(lang) == "en":
        return _fallback_advice_en(aqi, is_sensitive, has_event)

    if has_event:
        if is_sensitive:
            return f"目前 {event_description}，空氣品質為{aqi_level}（AQI {aqi}）。考量您的健康狀況，建議立即關閉窗戶，暫停戶外活動，並備妥相關藥物。"
        return f"目前 {event_description}，建議暫時避免戶外活動，必要時配戴 N95 口罩。"

    if aqi <= 50:
        return f"目前空氣品質良好（AQI {aqi}），非常適合户外活動，請盡情享受！"
    elif aqi <= 100:
        return f"目前空氣品質普通（AQI {aqi}），一般民眾可正常活動，敏感族群建議避免長時間激烈運動。"
    elif aqi <= 150:
        msg = f"目前空氣品質對敏感族群不健康（AQI {aqi}）。"
        if is_sensitive:
            msg += "考量您的健康狀況，建議減少戶外活動，外出時配戴口罩。"
        else:
            msg += "建議減少長時間在戶外激烈運動。"
        return msg
    else:
        return f"目前空氣品質不佳（AQI {aqi}，{aqi_level}），建議所有人減少戶外活動，外出配戴 N95 口罩。"


def _aqi_to_status_en(aqi: int) -> str:
    """英文版 AQI 等級名稱，沿用美國 EPA 的標準說法。"""
    if aqi <= 50:  return "Good"
    if aqi <= 100: return "Moderate"
    if aqi <= 150: return "Unhealthy for Sensitive Groups"
    if aqi <= 200: return "Unhealthy"
    if aqi <= 300: return "Very Unhealthy"
    return "Hazardous"


def _fallback_advice_en(aqi: int, is_sensitive: bool, has_event: bool) -> str:
    """英文版的規則式退路建議。

    事件描述本身是中文（來自新聞與民眾回報），直接嵌進英文句子會很突兀，
    所以這裡只說「附近有污染事件」，不轉述原文。
    """
    if has_event:
        if is_sensitive:
            return (
                f"A pollution incident has been reported nearby (AQI {aqi}). "
                "Given your health profile, close your windows, hold off on outdoor activity, "
                "and keep your medication to hand."
            )
        return (
            "A pollution incident has been reported nearby. Avoid outdoor activity for now, "
            "and wear an N95 mask if you do need to go out."
        )

    if aqi <= 50:
        return f"Air quality is good right now (AQI {aqi}) — a great time to be outdoors."
    elif aqi <= 100:
        return (
            f"Air quality is moderate (AQI {aqi}). Most people can carry on as usual; "
            "if you are in a sensitive group, skip long or intense outdoor workouts."
        )
    elif aqi <= 150:
        msg = f"Air quality is unhealthy for sensitive groups (AQI {aqi})."
        if is_sensitive:
            msg += " Given your health profile, cut back on outdoor activity and wear a mask when you go out."
        else:
            msg += " Cut back on long, intense outdoor exercise."
        return msg
    else:
        return (
            f"Air quality is poor (AQI {aqi}, {_aqi_to_status_en(aqi)}). "
            "Everyone should cut back on outdoor activity and wear an N95 mask outside."
        )
