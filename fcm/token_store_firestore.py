# fcm/token_store_firestore.py
# 裝置 FCM Token 的 Firestore 儲存實作，介面與 token_store.py 的 JSON 版完全相同。
#
# 為什麼不能用 JSON 檔：Cloud Run 的磁碟是暫時性的，實例被替換（即使設了
# min-instances=1 也會每隔幾天發生）或重新部署時整個檔案就消失。App 每次開啟
# 都會重新註冊 token 與縣市，所以警報推播看似正常；但每日通知的時間設定只在
# 使用者改設定時送一次，實例一換就永遠不見，使用者從此收不到每日通知。
#
# 每台裝置一份文件，文件 ID 為 token 的 SHA-256：FCM token 很長且含 ':' 等字元，
# 不適合直接當 ID；雜湊後也不會在 Firestore 主控台的 URL 上露出 token 本身。
# 讀寫語意（哪些空值要略過、哪些要寫入）與 JSON 版逐一對應，說明見 token_store.py。

import hashlib

from firebase_admin import firestore
from google.api_core.exceptions import NotFound
from google.cloud.firestore_v1 import FieldFilter

from core.timeutil import now_tw
from fcm.fcm_sender import _init_firebase
from fcm.token_store import _haversine, _normalize_county

_COLLECTION = "fcm_tokens"

# 單一 WriteBatch 上限 500 筆操作，留點餘裕
_BATCH_LIMIT = 400


def _collection():
    _init_firebase()
    return firestore.client().collection(_COLLECTION)


def _ref(token: str):
    return _collection().document(hashlib.sha256(token.encode()).hexdigest())


def _all() -> list[dict]:
    return [d.to_dict() for d in _collection().stream()]


def register_token(token: str, county: str = "", lat: float = None, lng: float = None,
                   conditions: str | None = None, device_id: str = ""):
    """新增或更新一筆裝置 token。只寫入有值的欄位（merge），未帶的欄位保留原值。"""
    county = _normalize_county(county)
    data = {"token": token}
    if county:                  data["county"]     = county
    if conditions is not None:  data["conditions"] = conditions
    if lat is not None:         data["lat"]        = lat
    if lng is not None:         data["lng"]        = lng
    if device_id:               data["device_id"]  = device_id
    _ref(token).set(data, merge=True)


@firestore.transactional
def _claim(transaction, ref, device_id: str) -> bool:
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return True
    owner = (snap.to_dict() or {}).get("device_id")
    if not owner:
        transaction.update(ref, {"device_id": device_id})
        return True
    return owner == device_id


def claim_token(token: str, device_id: str) -> bool:
    """確認 device_id 是否有權操作這個 FCM token，必要時建立歸屬（規則同 JSON 版）。"""
    return _claim(firestore.client().transaction(), _ref(token), device_id)


def get_tokens_by_county(county: str) -> list[str]:
    norm = _normalize_county(county)
    return [t["token"] for t in _all() if _normalize_county(t.get("county", "")) == norm]


def get_sensitive_tokens_by_county(county: str) -> list[str]:
    _SENSITIVE = ["氣喘", "心血管疾病", "懷孕中", "高血壓", "呼吸道疾病", "18歲以下", "65歲以上"]
    norm = _normalize_county(county)
    return [
        t["token"] for t in _all()
        if _normalize_county(t.get("county", "")) == norm
        and any(k in t.get("conditions", "") for k in _SENSITIVE)
    ]


def get_tokens_near(lat: float, lng: float, radius_km: float = 5.0) -> list[str]:
    result = []
    for t in _all():
        t_lat = t.get("lat")
        t_lng = t.get("lng")
        if t_lat is None or t_lng is None:
            continue
        if _haversine(lat, lng, float(t_lat), float(t_lng)) <= radius_km:
            result.append(t["token"])
    return result


def get_all_tokens() -> list[str]:
    return [t["token"] for t in _all()]


def remove_tokens(dead_tokens: list[str]) -> int:
    """刪除失效的 token，回傳實際刪除的筆數。"""
    refs = [_ref(t) for t in set(dead_tokens)]
    existing = [r for r in refs if r.get().exists]
    client = firestore.client()
    for start in range(0, len(existing), _BATCH_LIMIT):
        batch = client.batch()
        for r in existing[start:start + _BATCH_LIMIT]:
            batch.delete(r)
        batch.commit()
    return len(existing)


def get_token_county(token: str) -> str | None:
    rec = get_token_record(token)
    return (rec or {}).get("county") or None


def get_token_record(token: str) -> dict | None:
    snap = _ref(token).get()
    return snap.to_dict() if snap.exists else None


@firestore.transactional
def _set_daily(transaction, ref, token: str, enabled: bool, hour, minute,
               last_sent: str, device_id: str):
    snap = ref.get(transaction=transaction)
    if snap.exists:
        data = {"daily_enabled": enabled}
        if enabled:
            data.update(daily_hour=hour, daily_minute=minute, daily_last_sent=last_sent)
        transaction.update(ref, data)
    else:
        transaction.set(ref, {
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


def set_daily_preference(token: str, enabled: bool, hour: int | None = None,
                         minute: int | None = None, device_id: str = ""):
    """設定或取消一筆裝置的每日空氣品質摘要通知時間（今天已過的時間視為今天已發）。"""
    now = now_tw()
    last_sent = ""
    if enabled and hour is not None and minute is not None \
            and (hour, minute) <= (now.hour, now.minute):
        last_sent = now.strftime("%Y-%m-%d")
    _set_daily(firestore.client().transaction(), _ref(token), token, enabled,
               hour, minute, last_sent, device_id)


def get_due_daily_tokens(hour: int, minute: int, today: str) -> list[dict]:
    """取得每日通知時間已到、今天還沒發過且已知縣市的裝置（catch-up 語意同 JSON 版）。

    排程每分鐘呼叫一次，只查 daily_enabled 的文件，避免每分鐘把整個集合讀一遍。
    """
    docs = _collection().where(filter=FieldFilter("daily_enabled", "==", True)).stream()
    return [
        t for t in (d.to_dict() for d in docs)
        if t.get("daily_hour") is not None
        and t.get("daily_minute") is not None
        and (t["daily_hour"], t["daily_minute"]) <= (hour, minute)
        and t.get("daily_last_sent") != today
        and t.get("county")
    ]


def mark_daily_sent(token: str, today: str):
    try:
        _ref(token).update({"daily_last_sent": today})
    except NotFound:
        pass
