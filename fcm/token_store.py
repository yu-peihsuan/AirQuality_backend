# fcm/token_store.py
# 管理裝置 FCM Token 的儲存與讀取

import json
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


def register_token(token: str, county: str = ""):
    """新增或更新一筆裝置 token。"""
    tokens = _load()
    for t in tokens:
        if t["token"] == token:
            t["county"] = county
            _save(tokens)
            return
    tokens.append({"token": token, "county": county})
    _save(tokens)


def get_tokens_by_county(county: str) -> list[str]:
    """取得指定縣市的所有裝置 token。"""
    tokens = _load()
    return [t["token"] for t in tokens if t.get("county") == county]


def get_all_tokens() -> list[str]:
    """取得所有裝置 token。"""
    return [t["token"] for t in _load()]
