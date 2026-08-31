# crawler/weather_fetcher.py
# 從中央氣象署 O-A0003-001 取得縣市代表測站的當前天氣狀況

import os
import requests
from datetime import datetime

from core.timeutil import now_tw, to_tw

_CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"

_RAIN_KEYWORDS = ("雨", "陣雨", "雷雨", "毛毛雨", "豪雨", "大雨", "下雨")


def _normalize_county(county: str) -> str:
    return county.replace("臺", "台").rstrip("市縣")


def _station_coords(station: dict) -> tuple[float, float] | None:
    """從測站 GeoInfo.Coordinates 取得 (lat, lng)。"""
    try:
        for c in station.get("GeoInfo", {}).get("Coordinates", []):
            lat = c.get("StationLatitude")
            lng = c.get("StationLongitude")
            if lat and lng:
                return float(lat), float(lng)
    except Exception:
        pass
    return None


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_weather_for_county(county: str, lat: float = None, lng: float = None) -> dict:
    """
    取得當前天氣概況。
    有提供 lat/lng → 全台測站找最近的（最準確）。
    只有 county → 縣市過濾後取有降水資料的測站。
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

        if lat is None or lng is None:
            return _empty("需要提供座標才能找到最近測站")

        # 全台找最近測站（純座標判斷，不靠縣市名稱）
        best, best_dist = None, float("inf")
        for s in stations:
            coords = _station_coords(s)
            if coords is None:
                continue
            d = _haversine(lat, lng, coords[0], coords[1])
            if d < best_dist:
                best_dist = d
                best = s
        if best is None:
            return _empty("找不到鄰近氣象測站")
        station = best
        print(f"  🌤 [{county}] 最近氣象測站：{station.get('StationName', '')}（{best_dist:.1f} km）")

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


def _current_period(periods: list) -> dict | None:
    """從時段清單中找到目前時間在有效範圍內的那筆，找不到則取最近未來時段。"""
    now = now_tw()
    future = None
    for p in periods:
        try:
            # 中央氣象署的時間是台灣時間但不帶時區標記，to_tw 會據此補上。
            start = to_tw(datetime.fromisoformat(p["startTime"].replace(" ", "T")))
            end   = to_tw(datetime.fromisoformat(p["endTime"].replace(" ", "T")))
            if start <= now <= end:
                return p
            if start > now and future is None:
                future = p
        except Exception:
            continue
    return future  # 找不到當前時段，回傳最近的未來時段


def fetch_weather_forecast_for_county(county: str) -> str:
    """
    從 F-C0032-001 取得縣市今明天氣預報，
    回傳給 Prompt 用的自然語言描述（無數值）。
    """
    api_key = os.getenv("CWA_API_KEY", "")
    if not api_key:
        return ""
    # CWA API 使用「臺」，統一轉換
    cwa_county = county.replace("台", "臺")
    try:
        resp = requests.get(
            _FORECAST_URL,
            params={"Authorization": api_key, "format": "JSON",
                    "locationName": cwa_county},
            timeout=10,
        )
        data = resp.json()
        locations = data.get("records", {}).get("location", [])
        if not locations:
            return ""

        loc = locations[0]
        elements = {e["elementName"]: e["time"] for e in loc.get("weatherElement", [])}

        # 取當前有效時段
        wx_period  = _current_period(elements.get("Wx",  []))
        pop_period = _current_period(elements.get("PoP", []))

        wx = wx_period["parameter"]["parameterName"] if wx_period else ""
        try:
            pop = int(pop_period["parameter"]["parameterName"]) if pop_period else 0
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
