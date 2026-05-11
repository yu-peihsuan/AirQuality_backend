# fcm/token_store.py
# 管理裝置 FCM Token 的儲存與讀取

import json
import math
import os

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "crawler", "fcm_tokens.json")


def _load() -> list[dict]:
    if not os.path.exists(_TOKEN_FILE):
        return []
    with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(tokens: list[dict]):
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def register_token(token: str, county: str = "", lat: float = None, lng: float = None, conditions: str = ""):
    """新增或更新一筆裝置 token（含座標與健康狀況）。"""
    tokens = _load()
    for t in tokens:
        if t["token"] == token:
            t["county"]     = county
            t["conditions"] = conditions
            if lat is not None: t["lat"] = lat
            if lng is not None: t["lng"] = lng
            _save(tokens)
            return
    tokens.append({
        "token":      token,
        "county":     county,
        "lat":        lat,
        "lng":        lng,
        "conditions": conditions,
    })
    _save(tokens)


def get_tokens_by_county(county: str) -> list[str]:
    """取得指定縣市的所有裝置 token。"""
    return [t["token"] for t in _load() if t.get("county") == county]


def get_sensitive_tokens_by_county(county: str) -> list[str]:
    """取得指定縣市且有敏感健康狀況的 token。"""
    _SENSITIVE = ["氣喘", "心血管疾病", "懷孕中", "高血壓", "呼吸道疾病", "18歲以下", "65歲以上"]
    result = []
    for t in _load():
        if t.get("county") != county:
            continue
        if any(k in t.get("conditions", "") for k in _SENSITIVE):
            result.append(t["token"])
    return result


def get_tokens_near(lat: float, lng: float, radius_km: float = 5.0) -> list[str]:
    """取得指定座標 radius_km 範圍內的所有 token。"""
    result = []
    for t in _load():
        t_lat = t.get("lat")
        t_lng = t.get("lng")
        if t_lat is None or t_lng is None:
            continue
        if _haversine(lat, lng, float(t_lat), float(t_lng)) <= radius_km:
            result.append(t["token"])
    return result


def get_all_tokens() -> list[str]:
    """取得所有裝置 token。"""
    return [t["token"] for t in _load()]
