import os
import requests
from datetime import datetime

FORECAST_URL = "https://data.moenv.gov.tw/api/v2/AQF_P_01"

# 縣市 → 空品區對應表
_COUNTY_TO_AREA = {
    "基隆": "北部", "台北": "北部", "新北": "北部", "桃園": "北部",
    "新竹": "竹苗", "苗栗": "竹苗",
    "台中": "中部", "彰化": "中部", "南投": "中部",
    "雲林": "雲嘉南", "嘉義": "雲嘉南", "台南": "雲嘉南",
    "高雄": "高屏", "屏東": "高屏",
    "宜蘭": "宜蘭",
    "花蓮": "花東", "台東": "花東",
    "澎湖": "離島", "金門": "離島", "連江": "離島", "馬祖": "離島",
}


def _county_to_area(county: str) -> str | None:
    norm = county.replace("臺", "台").rstrip("市縣")
    return _COUNTY_TO_AREA.get(norm)


def _aqi_rank(aqi: int) -> int:
    if aqi <= 50:  return 1
    if aqi <= 100: return 2
    if aqi <= 150: return 3
    if aqi <= 200: return 4
    if aqi <= 300: return 5
    return 6


def _aqi_to_status(aqi: int) -> str:
    if aqi <= 50:  return "良好"
    if aqi <= 100: return "普通"
    if aqi <= 150: return "對敏感族群不健康"
    if aqi <= 200: return "對所有族群不健康"
    if aqi <= 300: return "非常不健康"
    return "危害"


def _parse_aqi(raw) -> int:
    if raw is None:
        return 0
    s = str(raw).strip()
    if "-" in s:
        try:
            return int(s.split("-")[-1])
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


def fetch_latest_forecast(county: str = None) -> list[dict]:
    """從 AQF_P_01 取得最新空品預報，依縣市過濾，每個空品區只保留最新一筆。"""
    api_key = os.getenv("MOENV_API_KEY", "")
    if not api_key:
        print("⚠️  MOENV_API_KEY 未設定，跳過空品預報")
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            FORECAST_URL,
            params={"api_key": api_key, "format": "JSON", "limit": 1000,
                    "sort": "publishtime desc", "offset": 0},
            timeout=10,
        )
        data = resp.json()
        records = data if isinstance(data, list) else data.get("records", [])

        # 只保留今天發布的資料（publishtime 開頭是今天日期）
        records = [r for r in records if str(r.get("publishtime", "")).startswith(today)]

        # 每個空品區只取第一筆（已按 publishtime desc 排序，即最新）
        seen_areas: set[str] = set()
        deduped = []
        for r in records:
            area = r.get("area", "")
            if area not in seen_areas:
                seen_areas.add(area)
                deduped.append(r)
        records = deduped

        if county:
            area_short = _county_to_area(county)
            if area_short:
                records = [r for r in records if area_short in r.get("area", "")]

        return records
    except Exception as e:
        print(f"⚠️  空品預報取得失敗：{e}")
        return []


def _parse_trend(content: str, area: str = "") -> str:
    """從預報文字中判斷趨勢，回傳簡短描述。"""
    if not content:
        return ""
    worsening = ["轉差", "惡化", "升高", "增加", "不良", "偏差", "加重", "累積", "偏高", "污染", "汙染"]
    improving = ["改善", "好轉", "降低", "減少", "轉好", "趨緩", "消散", "減輕", "偏低", "趨好"]
    if any(k in content for k in worsening):
        return "空氣品質預計將變差"
    if any(k in content for k in improving):
        return "空氣品質預計將改善"
    return "空氣品質預計維持穩定"


def _parse_trend_detail(content: str, area: str = "") -> str:
    """
    從預報文字中提取該空品區的相關句子（供 RAG 使用）。
    """
    if not content:
        return ""
    import re
    sentences = re.split(r'[；。\n]', content)
    sentences = [s.strip() for s in sentences if s.strip()]
    area_short = area.replace("空品區", "").replace("空氣品質區", "")

    # 優先：找含空品區名稱的句子（今日狀況段落）
    if area_short:
        for s in sentences:
            if area_short in s and ("等級" in s or "普通" in s or "良好" in s or "橘色" in s or "紅色" in s):
                return s[:60]

    # fallback：回傳前兩句（通常是今日整體概況）
    summary = "；".join(sentences[:2])
    return summary[:80]


def fetch_today_forecasts(county: str = None) -> list[dict]:
    """
    取得今日空品預報（格式相容 NewsRecord）。
    好壞都回傳，通知中心用。
    """
    records = fetch_latest_forecast(county)
    result = []
    for r in records:
        forecast_aqi = _parse_aqi(r.get("aqi"))
        status       = _aqi_to_status(forecast_aqi)
        area         = r.get("area", "")
        location     = county or area
        publishtime  = r.get("publishtime", "")
        result.append({
            "source":       "空品預報",
            "region":       location,
            "title":        f"{location} 空品預報：{status}",
            "summary":      _parse_trend(r.get("content", "")),
            "url":          "",
            "published_at": publishtime,
            "timestamp":    "",
        })
    return result


def fetch_worsening_forecasts(county: str = None, current_aqi: int = 0) -> list[dict]:
    """取得今日預報 AQI ≥ 101 的清單，推播用。"""
    records = fetch_latest_forecast(county)
    result = []
    for r in records:
        forecast_aqi = _parse_aqi(r.get("aqi"))
        if forecast_aqi < 101:
            continue
        if current_aqi > 0 and _aqi_rank(forecast_aqi) <= _aqi_rank(current_aqi):
            continue

        status      = _aqi_to_status(forecast_aqi)
        area        = r.get("area", "")
        publishtime = r.get("publishtime", "")

        location = county or area
        result.append({
            "source":       "空品預報",
            "region":       location,
            "title":        f"{location} 空品預報：{status}",
            "summary":      r.get("content", ""),
            "url":          "",
            "published_at": publishtime,
            "timestamp":    "",
        })
    return result
