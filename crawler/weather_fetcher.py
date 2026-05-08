# crawler/weather_fetcher.py
# 從中央氣象署 O-A0003-001 取得縣市代表測站的當前天氣狀況

import os
import requests

_CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"

_RAIN_KEYWORDS = ("雨", "陣雨", "雷雨", "毛毛雨", "豪雨", "大雨", "下雨")


def _normalize_county(county: str) -> str:
    return county.replace("臺", "台").rstrip("市縣")


def fetch_weather_for_county(county: str) -> dict:
    """
    取得指定縣市的當前天氣概況。
    回傳 dict：
        weather     : 天氣描述（e.g. "陰有雨"）
        temp        : 氣溫（°C，float）
        humidity    : 相對濕度（%，int）
        rain_mm     : 當前降水量（mm/hr，float）
        is_raining  : bool
        description : 給 Prompt 用的自然語言描述
    """
    api_key = os.getenv("CWA_API_KEY", "")
    if not api_key:
        return _empty("氣象署 API 金鑰未設定")

    try:
        resp = requests.get(
            _CWA_URL,
            params={"Authorization": api_key, "format": "JSON", "limit": 1000},
            timeout=10,
        )
        data = resp.json()
        stations = (
            data.get("records", {}).get("Station", [])
            or data.get("records", [])
        )

        norm = _normalize_county(county)
        matched = [
            s for s in stations
            if norm in _normalize_county(
                s.get("GeoInfo", {}).get("CountyName", "")
                or s.get("CountyName", "")
            )
        ]
        if not matched:
            return _empty("查無該縣市測站")

        # 選有降水資料的測站優先，其次取第一筆
        station = next(
            (s for s in matched if _get_rain(s) is not None),
            matched[0]
        )

        weather = _get_field(station, "Weather", "")
        temp    = _get_field(station, "AirTemperature", None)
        rh      = _get_field(station, "RelativeHumidity", None)
        rain    = _get_rain(station) or 0.0

        is_raining = (
            rain > 0.0
            or any(kw in weather for kw in _RAIN_KEYWORDS)
        )

        parts = []
        if weather:
            parts.append(weather)
        if temp is not None:
            try:
                parts.append(f"氣溫 {float(temp):.1f}°C")
            except (ValueError, TypeError):
                pass
        if rh is not None:
            try:
                parts.append(f"濕度 {int(float(rh))}%")
            except (ValueError, TypeError):
                pass
        if rain > 0:
            parts.append(f"降雨 {rain:.1f} mm/hr")

        description = "、".join(parts) if parts else "無資料"

        return {
            "weather":     weather,
            "temp":        float(temp) if temp is not None else None,
            "humidity":    int(float(rh)) if rh is not None else None,
            "rain_mm":     rain,
            "is_raining":  is_raining,
            "description": description,
        }

    except Exception as e:
        print(f"⚠️  氣象資料取得失敗：{e}")
        return _empty(str(e))


def _get_field(station: dict, name: str, default):
    """從 WeatherElement 或頂層找欄位值"""
    elements = station.get("WeatherElement", {})
    if isinstance(elements, dict):
        v = elements.get(name)
        if v not in (None, "", -99, "-99", -9999):
            return v
    elif isinstance(elements, list):
        for el in elements:
            if el.get("elementName") == name or el.get("ElementName") == name:
                v = el.get("elementValue") or el.get("ElementValue")
                if v not in (None, "", -99, "-99", -9999):
                    return v
    return station.get(name, default)


def _get_rain(station: dict) -> float | None:
    """嘗試多個可能的降水欄位名稱，回傳 mm/hr 或 None"""
    for field in ("Now", "Precipitation", "RAIN", "H_1R", "H_1RN"):
        v = _get_field(station, field, None)
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("Precipitation") or v.get("precipitation")
        try:
            f = float(v)
            if f >= 0:
                return f
        except (ValueError, TypeError):
            continue
    return None


_FORECAST_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"


def _pop_to_level(pop: int) -> str:
    """把降雨機率轉為程度提示，供 LLM 參考判斷，不直接說出數值。"""
    if pop <= 20:  return "低"
    if pop <= 50:  return "中"
    return "高"


def fetch_weather_forecast_for_county(county: str) -> str:
    """
    從 F-C0032-001 取得縣市今明天氣預報，
    回傳給 Prompt 用的自然語言描述（無數值）。
    """
    api_key = os.getenv("CWA_API_KEY", "")
    if not api_key:
        return ""
    try:
        resp = requests.get(
            _FORECAST_URL,
            params={"Authorization": api_key, "format": "JSON",
                    "locationName": county},
            timeout=10,
        )
        data = resp.json()
        locations = data.get("records", {}).get("location", [])
        if not locations:
            return ""

        loc = locations[0]
        elements = {e["elementName"]: e["time"] for e in loc.get("weatherElement", [])}

        # 取最近時段的天氣現象與降雨機率
        wx_periods  = elements.get("Wx", [])
        pop_periods = elements.get("PoP", [])

        wx  = wx_periods[0]["parameter"]["parameterName"]  if wx_periods  else ""
        try:
            pop = int(pop_periods[0]["parameter"]["parameterName"]) if pop_periods else 0
        except (ValueError, TypeError):
            pop = 0

        rain_level = _pop_to_level(pop)

        parts = []
        if wx:
            parts.append(wx)
        if rain_level in ("中", "高"):
            parts.append(f"降雨可能性{rain_level}")
        return "、".join(parts) if parts else ""

    except Exception as e:
        print(f"⚠️  天氣預報取得失敗：{e}")
        return ""


def _empty(reason: str) -> dict:
    return {
        "weather":     "",
        "temp":        None,
        "humidity":    None,
        "rain_mm":     0.0,
        "is_raining":  False,
        "description": f"無法取得天氣資料（{reason}）",
    }
