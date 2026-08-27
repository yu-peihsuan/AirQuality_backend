# fcm/token_store.py
# 管理裝置 FCM Token 的儲存與讀取

import json
import math
import os
from datetime import datetime, timedelta, timezone

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


def register_token(token: str, county: str = "", lat: float = None, lng: float = None,
                   conditions: str = "", device_id: str = ""):
    """新增或更新一筆裝置 token（含座標、健康狀況與所屬裝置）。

    device_id 來自 access token，用於確認後續修改通知設定的人就是
    當初註冊這個 FCM token 的裝置（見 claim_token）。
    """
    tokens = _load()
    for t in tokens:
        if t["token"] == token:
            t["county"]     = county
            t["conditions"] = conditions
            if lat is not None: t["lat"] = lat
            if lng is not None: t["lng"] = lng
            if device_id:       t["device_id"] = device_id
            _save(tokens)
            return
    tokens.append({
        "token":      token,
        "county":     county,
        "lat":        lat,
        "lng":        lng,
        "conditions": conditions,
        "device_id":  device_id,
    })
    _save(tokens)


def claim_token(token: str, device_id: str) -> bool:
    """確認 device_id 是否有權操作這個 FCM token，必要時建立歸屬。

    - 尚無此筆紀錄：回 True（後續流程會建立，並在建立時寫入 device_id）
    - 已有紀錄但無 device_id（認證機制上線前的舊資料）：綁定給呼叫者並回 True
    - 已有紀錄且 device_id 相符：回 True
    - 已有紀錄但屬於其他裝置：回 False
    """
    tokens = _load()
    for t in tokens:
        if t["token"] != token:
            continue
        owner = t.get("device_id")
        if not owner:
            t["device_id"] = device_id
            _save(tokens)
            return True
        return owner == device_id
    return True


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


def get_token_county(token: str) -> str | None:
    """取得指定裝置目前註冊的縣市（尚未註冊過或無縣市則回傳 None）。"""
    for t in _load():
        if t["token"] == token:
            return t.get("county") or None
    return None


def get_token_record(token: str) -> dict | None:
    """取得指定裝置的完整註冊資料（含縣市與座標）；未註冊回傳 None。"""
    for t in _load():
        if t["token"] == token:
            return t
    return None


def set_daily_preference(token: str, enabled: bool, hour: int | None = None,
                         minute: int | None = None, device_id: str = ""):
    """設定或取消一筆裝置的每日空氣品質摘要通知時間。"""
    # 配合 get_due_daily_tokens 的 catch-up 語意（時間已過即發）：
    # 若設定的時間「今天已經過了」，標記今天已發，避免一設定就立刻收到通知；
    # 設定的是今天稍後的時間則清除標記，讓今天照常發送。
    now = datetime.now(timezone(timedelta(hours=8)))
    last_sent = ""
    if enabled and hour is not None and minute is not None \
            and (hour, minute) <= (now.hour, now.minute):
        last_sent = now.strftime("%Y-%m-%d")

    tokens = _load()
    for t in tokens:
        if t["token"] == token:
            t["daily_enabled"] = enabled
            if enabled:
                t["daily_hour"]      = hour
                t["daily_minute"]    = minute
                t["daily_last_sent"] = last_sent
            _save(tokens)
            return
    tokens.append({
        "token":           token,
        "county":          "",
        "lat":             None,
        "lng":             None,
        "conditions":      "",
        "device_id":       device_id,
        "daily_enabled":   enabled,
        "daily_hour":      hour,
        "daily_minute":    minute,
        "daily_last_sent": last_sent,
    })
    _save(tokens)


def get_due_daily_tokens(hour: int, minute: int, today: str) -> list[dict]:
    """取得每日通知「時間已到、今天還沒發過」且已知所在縣市的裝置。

    採 catch-up 語意（<= 而非 ==）：若某一分鐘的排程檢查被延遲或跳過
    （如 Cloud Run 背景 CPU 節流），下一次檢查仍會補發，不會整天漏發。
    """
    return [
        t for t in _load()
        if t.get("daily_enabled")
        and t.get("daily_hour") is not None
        and t.get("daily_minute") is not None
        and (t["daily_hour"], t["daily_minute"]) <= (hour, minute)
        and t.get("daily_last_sent") != today
        and t.get("county")
    ]


def mark_daily_sent(token: str, today: str):
    """標記一筆裝置今天已經收過每日摘要通知，避免重複發送。"""
    tokens = _load()
    for t in tokens:
        if t["token"] == token:
            t["daily_last_sent"] = today
            _save(tokens)
            return
