# rag/llm_structurer.py
# 使用 GPT-4o-mini (via OpenRouter) 將爬蟲非結構化新聞轉為標準化 JSON 事件物件

import os
import json
from openai import OpenAI

_client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY", ""),
    base_url="https://openrouter.ai/api/v1",
)

_STRUCTURING_PROMPT = """你是一個專業的空氣污染事件分析系統。
請從以下新聞文本中萃取污染事件資訊，以 JSON 格式回傳。

新聞標題：{title}
新聞摘要：{summary}
地區：{region}

請回傳以下 JSON 格式（所有欄位都必須填寫，不確定的填 null）：
{{
  "event_type": "<fire|chemical|dust|odor|general_air_quality|unknown>",
  "pollution_category": "<PM2.5|PM10|VOC|CO|SO2|NOx|mixed|unknown>",
  "severity": "<low|medium|high|critical|unknown>",
  "location_description": "<從文章擷取的具體地點描述，例如：台中市北屯區某工廠>",
  "affected_radius_estimate": "<預估影響範圍，例如：500公尺、1公里，若無法判斷填 null>",
  "is_confirmed_pollution_event": <true 或 false，是否為確認的污染事件（非測試或無效回報）>,
  "confidence": "<high|medium|low，對上述判斷的信心程度>"
}}

嚴格回傳 JSON，不要有任何其他文字。"""


def structure_news_event(news_item: dict) -> dict:
    """
    接收一筆爬蟲新聞 dict，呼叫 LLM 萃取為標準化事件結構。
    回傳原始 news_item 加上 'structured_event' 欄位。
    若 LLM 呼叫失敗，'structured_event' 為 None。
    """
    title = news_item.get("title", "")
    summary = news_item.get("summary", "")
    region = news_item.get("region", "")

    prompt = _STRUCTURING_PROMPT.format(
        title=title,
        summary=summary,
        region=region,
    )

    try:
        response = _client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是空氣污染事件分析專家，只輸出 JSON，不輸出任何其他文字。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,  # 結構化任務，設為 0 確保輸出穩定
            max_tokens=300,
        )

        raw_json = response.choices[0].message.content.strip()
        # 移除可能的 markdown code block
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
            raw_json = raw_json.strip()

        structured = json.loads(raw_json)
        result = dict(news_item)
        result["structured_event"] = structured
        return result

    except json.JSONDecodeError as e:
        print(f"⚠️  LLM 回傳無效 JSON，跳過結構化：{title} | 錯誤：{e}")
        result = dict(news_item)
        result["structured_event"] = None
        return result
    except Exception as e:
        print(f"⚠️  結構化 API 呼叫失敗：{title} | 錯誤：{e}")
        result = dict(news_item)
        result["structured_event"] = None
        return result


def structure_news_batch(news_list: list[dict]) -> list[dict]:
    """
    批次處理新聞清單，回傳每筆加上 structured_event 欄位的清單。
    """
    if not news_list:
        return []

    print(f"🤖 開始批次語意結構化，共 {len(news_list)} 筆新聞...")
    results = []
    for i, news in enumerate(news_list, 1):
        structured = structure_news_event(news)
        results.append(structured)

        event = structured.get("structured_event")
        if event:
            etype = event.get("event_type", "unknown")
            severity = event.get("severity", "unknown")
            confirmed = event.get("is_confirmed_pollution_event", False)
            print(
                f"  [{i}/{len(news_list)}] {news.get('title', '')[:30]}... "
                f"→ {etype} / {severity} / 確認:{confirmed}"
            )
        else:
            print(f"  [{i}/{len(news_list)}] ⚠️ 結構化失敗")

    confirmed_count = sum(
        1 for r in results
        if r.get("structured_event") and r["structured_event"].get("is_confirmed_pollution_event")
    )
    print(f"✅ 結構化完成：{len(results)} 筆中有 {confirmed_count} 筆確認為污染事件。")
    return results
