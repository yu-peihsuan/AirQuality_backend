import os
import requests

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
    """將縣市名稱轉為空品區短名（如「台南市」→「雲嘉南」）。"""
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
    """處理 AQI 欄位可能為單值 '120' 或範圍 '101-150'，取上限值。"""
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
    """
    從 AQF_P_01 取得最新空品預報（每 30 分鐘更新）。
    county 為縣市名稱，內部自動轉換為對應的空品區過濾。
    """
    api_key = os.getenv("MOENV_API_KEY", "")
    if not api_key:
        print("⚠️  MOENV_API_KEY 未設定，跳過空品預報")
        return []
    try:
        resp = requests.get(
            FORECAST_URL,
            params={"api_key": api_key, "format": "JSON", "limit": 100, "offset": 0},
            timeout=10,
        )
        data = resp.json()
        records = data if isinstance(data, list) else data.get("records", [])

        if county:
            area_short = _county_to_area(county)
            if area_short:
                records = [r for r in records if area_short in r.get("area", "")]

        return records
    except Exception as e:
        print(f"⚠️  空品預報取得失敗：{e}")
        return []


def fetch_worsening_forecasts(county: str = None, current_aqi: int = 0) -> list[dict]:
    """
    取得預報 AQI ≥ 101 的空品區清單（格式相容 NewsRecord）。
    若傳入 current_aqi，只回傳比今天更差的紀錄。
    """
    records = fetch_latest_forecast(county)
    result = []
    for r in records:
        forecast_aqi   = _parse_aqi(r.get("aqi"))
        if forecast_aqi < 101:
            continue
        if current_aqi > 0 and _aqi_rank(forecast_aqi) <= _aqi_rank(current_aqi):
            continue

        status         = _aqi_to_status(forecast_aqi)
        area           = r.get("area", "")
        majorpollutant = r.get("majorpollutant", "")
        content        = r.get("content", "")
        forecastdate   = r.get("forecastdate", "")
        publishtime    = r.get("publishtime", "")

        result.append({
            "source":       "空品預報",
            "region":       county or area,
            "title":        f"預報 AQI {forecast_aqi}（{status}）",
            "summary":      content or (f"主要污染物：{majorpollutant}" if majorpollutant else ""),
            "url":          "",
            "published_at": publishtime or forecastdate,
            "timestamp":    "",
        })
    return result
