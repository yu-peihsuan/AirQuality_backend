# gis/interpolation.py
# 個人定位點空品估計：IDW 反距離加權空間插值
#
# 以使用者座標周圍 k 個測站，依距離倒數的 power 次方加權平均，
# 估計該點的 AQI / PM2.5，取代單一最近測站法。

import math

# 判定「幾乎就在測站上」的距離門檻（公里），小於此距離直接回傳該站實測值
_ON_SITE_THRESHOLD_KM = 0.05


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _to_float(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f
    except (ValueError, TypeError):
        return None


def _valid_stations(records: list[dict], field: str) -> list[dict]:
    """整理出座標與目標欄位皆有效的測站清單。"""
    out = []
    for r in records:
        lat = _to_float(r.get("latitude"))
        lng = _to_float(r.get("longitude"))
        val = _to_float(r.get(field))
        if lat is None or lng is None or val is None:
            continue
        out.append({
            "sitename": r.get("sitename", ""),
            "county": r.get("county", ""),
            "lat": lat, "lng": lng, "value": val,
        })
    return out


def idw_estimate(
    user_lat: float,
    user_lng: float,
    records: list[dict],
    field: str = "aqi",
    k: int = 4,
    power: float = 2.0,
) -> dict | None:
    """
    IDW 插值估計使用者位置的空品數值。

    回傳：
        {
          "value": 估計值,
          "method": "idw" | "on_site",
          "stations": [{sitename, county, distance_km, weight, value}, ...]
        }
        資料不足時回傳 None。
    """
    stations = _valid_stations(records, field)
    if not stations:
        return None

    for s in stations:
        s["distance_km"] = _haversine_km(user_lat, user_lng, s["lat"], s["lng"])
    stations.sort(key=lambda s: s["distance_km"])
    nearest = stations[:k]

    # 使用者幾乎就站在測站上 → 直接回傳實測值
    if nearest[0]["distance_km"] < _ON_SITE_THRESHOLD_KM:
        s = nearest[0]
        return {
            "value": round(s["value"], 1),
            "method": "on_site",
            "stations": [{
                "sitename": s["sitename"], "county": s["county"],
                "distance_km": round(s["distance_km"], 2),
                "weight": 1.0, "value": s["value"],
            }],
        }

    weight_sum = 0.0
    weighted_val = 0.0
    for s in nearest:
        w = 1.0 / (s["distance_km"] ** power)
        s["weight"] = w
        weight_sum += w
        weighted_val += w * s["value"]

    estimate = weighted_val / weight_sum
    return {
        "value": round(estimate, 1),
        "method": "idw",
        "stations": [{
            "sitename": s["sitename"], "county": s["county"],
            "distance_km": round(s["distance_km"], 2),
            "weight": round(s["weight"] / weight_sum, 3),
            "value": s["value"],
        } for s in nearest],
    }
