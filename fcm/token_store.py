# fcm/token_store.py
# 管理裝置 FCM Token 的儲存與讀取

import json
import math
import os
import tempfile
import threading

from core.timeutil import now_tw

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "crawler", "fcm_tokens.json")

# token 檔是「整檔讀出→改一筆→整檔寫回」。FastAPI 以執行緒池處理同步端點，
# 兩個裝置同時註冊時兩條執行緒會各自讀到同一份舊清單，後寫的那份把先寫的
# 那筆蓋掉（lost update）。所有讀改寫的區段都必須在這把鎖裡完成。
_lock = threading.RLock()


def _normalize_county(county: str) -> str:
    """縣市名稱正規化：「臺」一律轉為「台」。

    寫入與查詢都要經過這裡。App 的定位結果可能給出「臺北市」，而推播端
    （main.normalize_name）查的是「台北市」；兩者若不統一，臺北／臺中／
    臺南／臺東的裝置就永遠查不到。
    """
    return county.replace("臺", "台") if county else county


def _load() -> list[dict]:
    if not os.path.exists(_TOKEN_FILE):
        return []
    with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(tokens: list[dict]):
    """先寫暫存檔再 os.replace，避免寫到一半中斷時留下半截的 JSON。"""
    directory = os.path.dirname(os.path.abspath(_TOKEN_FILE))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".fcm_tokens.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _TOKEN_FILE)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def register_token(token: str, county: str = "", lat: float = None, lng: float = None,
                   conditions: str | None = None, device_id: str = ""):
    """新增或更新一筆裝置 token（含座標、健康狀況與所屬裝置）。

    device_id 來自 access token，用於確認後續修改通知設定的人就是
    當初註冊這個 FCM token 的裝置（見 claim_token）。

    county 與 conditions 的空值語意不同，不能一起處理：

    - county=""：真的是「這次沒有縣市資訊」。App 的
      MyFirebaseMessagingService.onNewToken 以 uploadTokenWithCounty(context, "")
      重新註冊，只有縣市是空的；照單全收會讓使用者的縣市在每次 token 輪替時
      被清空，之後所有縣市推播都收不到。所以空字串一律略過。
    - conditions=""：真的是「這個使用者沒有任何健康狀況」。TokenManager 每次
      都從 prefs 讀當下的真實值送出（onNewToken 那條路也一樣），所以空字串是
      有意義的值，必須寫進去——否則使用者在設定裡取消「氣喘」之後，後端仍留著
      舊值，他會一直收到敏感族群的 AQI 警示。「不要更新」用 None 表達。
    """
    county = _normalize_county(county)
    with _lock:
        tokens = _load()
        for t in tokens:
            if t["token"] == token:
                if county:                  t["county"]     = county
                if conditions is not None:  t["conditions"] = conditions
                if lat is not None: t["lat"]        = lat
                if lng is not None: t["lng"]        = lng
                if device_id:       t["device_id"]  = device_id
                _save(tokens)
                return
        tokens.append({
            "token":      token,
            "county":     county,
            "lat":        lat,
            "lng":        lng,
            "conditions": conditions or "",
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
    with _lock:
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
    norm = _normalize_county(county)
    return [t["token"] for t in _load() if _normalize_county(t.get("county", "")) == norm]


# 視為敏感族群的健康狀況關鍵字。提到模組層是因為 AQI 警示門檻的預設值
# 也要靠它判斷（敏感族群預設 101，一般人 151）。
_SENSITIVE_CONDITIONS = [
    "氣喘", "心血管疾病", "懷孕中", "高血壓", "呼吸道疾病", "18歲以下", "65歲以上",
]

# AQI 即時警示的預設門檻，維持修改前的行為：
# AQI ≥ 151 推全體、101–150 只推敏感族群。
DEFAULT_THRESHOLD_GENERAL   = 151
DEFAULT_THRESHOLD_SENSITIVE = 101

# 使用者可設定的範圍。低於 50 幾乎天天觸發，高於 300 等於沒設定。
THRESHOLD_MIN = 50
THRESHOLD_MAX = 300


def is_sensitive(record: dict) -> bool:
    """這台裝置的健康檔案是否屬於敏感族群。"""
    conditions = record.get("conditions", "") or ""
    return any(k in conditions for k in _SENSITIVE_CONDITIONS)


def effective_alert_threshold(record: dict) -> int:
    """這台裝置實際生效的 AQI 警示門檻。

    個人設定「疊加」在預設分級之上，只能讓自己更早收到、不能更晚：
    取個人門檻與預設門檻的較小值。所以使用者把門檻設成 200 時，
    仍然會在 151 收到原本就該收到的那則警示。
    """
    default = DEFAULT_THRESHOLD_SENSITIVE if is_sensitive(record) else DEFAULT_THRESHOLD_GENERAL
    personal = record.get("aqi_threshold")
    if personal is None:
        return default
    try:
        return min(int(personal), default)
    except (TypeError, ValueError):
        return default


def get_records_by_county(county: str) -> list[dict]:
    """取得指定縣市的完整裝置紀錄（AQI 警示要逐台比對門檻，只有 token 不夠）。"""
    norm = _normalize_county(county)
    return [t for t in _load() if _normalize_county(t.get("county", "")) == norm]


def get_sensitive_tokens_by_county(county: str) -> list[str]:
    """取得指定縣市且有敏感健康狀況的 token。"""
    return [t["token"] for t in get_records_by_county(county) if is_sensitive(t)]


def set_alert_threshold(token: str, threshold: int | None, device_id: str = ""):
    """設定或清除一台裝置的 AQI 警示門檻。threshold=None 代表回到預設值。"""
    if threshold is not None:
        threshold = max(THRESHOLD_MIN, min(THRESHOLD_MAX, int(threshold)))
    with _lock:
        tokens = _load()
        for t in tokens:
            if t["token"] == token:
                t["aqi_threshold"] = threshold
                _save(tokens)
                return
        tokens.append({
            "token":         token,
            "county":        "",
            "lat":           None,
            "lng":           None,
            "conditions":    "",
            "device_id":     device_id,
            "aqi_threshold": threshold,
        })
        _save(tokens)


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


def remove_tokens(dead_tokens: list[str]) -> int:
    """刪除失效的 token（FCM 回報 UNREGISTERED／INVALID_ARGUMENT 時使用）。

    回傳實際刪除的筆數。留著失效 token 只會讓每次推播多一筆必然失敗的請求，
    並讓 failure_count 永遠不為零、掩蓋掉真正的問題。
    """
    dead = set(dead_tokens)
    if not dead:
        return 0
    with _lock:
        tokens = _load()
        kept = [t for t in tokens if t.get("token") not in dead]
        removed = len(tokens) - len(kept)
        if removed:
            _save(kept)
        return removed


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
    now = now_tw()
    last_sent = ""
    if enabled and hour is not None and minute is not None \
            and (hour, minute) <= (now.hour, now.minute):
        last_sent = now.strftime("%Y-%m-%d")

    with _lock:
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
    with _lock:
        tokens = _load()
        for t in tokens:
            if t["token"] == token:
                t["daily_last_sent"] = today
                _save(tokens)
                return
