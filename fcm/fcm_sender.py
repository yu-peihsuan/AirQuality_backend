# fcm/fcm_sender.py
# Firebase Cloud Messaging 推播發送模組

import os
import firebase_admin
from firebase_admin import credentials, messaging

# FCM 單次 multicast 的硬性上限。超過這個數量整批會被拒絕
# （回 400 too many registration tokens），不是只丟掉多的那些。
_MULTICAST_LIMIT = 500

# 代表「這個 token 已經不存在」的錯誤碼。收到就該從 token 檔刪掉，
# 否則每次推播都會多一筆必然失敗的請求。
_DEAD_TOKEN_CODES = {
    "UNREGISTERED",
    "NOT_FOUND",
    "INVALID_ARGUMENT",
    "registration-token-not-registered",
    "invalid-registration-token",
    "invalid-argument",
}


def _init_firebase():
    """確保 Firebase 已初始化（多模組共用，只初始化一次）。"""
    try:
        firebase_admin.get_app()
    except ValueError:
        key_path = os.path.join(os.path.dirname(__file__), "..", "serviceAccountKey.json")
        if os.path.exists(key_path):
            # 本機開發：使用金鑰檔案
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        else:
            # Cloud Run 環境：使用專案預設服務帳戶，不需要金鑰檔案
            firebase_admin.initialize_app()


def send_notification(token: str, title: str, body: str, data: dict = None) -> bool:
    """
    發送推播給單一裝置。
    回傳 True 表示成功，False 表示失敗。
    """
    try:
        _init_firebase()
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=token,
        )
        messaging.send(message)
        return True
    except Exception as e:
        print(f"⚠️  FCM 推播失敗：{e}")
        return False


def _error_code(exc) -> str:
    """從 firebase_admin 的例外取出可判讀的錯誤碼。"""
    for attr in ("code", "cause"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(exc).__name__ if exc else ""


def _send_chunk(tokens: list[str], title: str, body: str, data: dict) -> dict:
    """送出一批（<= 500 筆）並逐則檢查結果。"""
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in (data or {}).items()},
        tokens=tokens,
    )
    response = messaging.send_each_for_multicast(message)

    dead: list[str] = []
    errors: list[str] = []
    for token, resp in zip(tokens, response.responses):
        if resp.success:
            continue
        code = _error_code(resp.exception)
        errors.append(f"{token[:12]}…: {code or resp.exception}")
        if code in _DEAD_TOKEN_CODES:
            dead.append(token)

    return {
        "success": response.success_count,
        "failure": response.failure_count,
        "dead": dead,
        "errors": errors,
    }


def send_multicast(tokens: list[str], title: str, body: str, data: dict = None) -> dict:
    """
    批次推播給多個裝置。

    每 500 筆送一批（FCM 的 multicast 上限），並逐則檢視結果：
    已註銷／無效的 token 會直接從 token 檔移除，其餘失敗原因寫進 log。

    回傳 {"success": n, "failure": n, "removed": n, "errors": [...]}
    """
    if not tokens:
        return {"success": 0, "failure": 0, "removed": 0, "errors": []}

    total_success = 0
    total_failure = 0
    dead: list[str] = []
    errors: list[str] = []

    try:
        _init_firebase()
    except Exception as e:
        print(f"⚠️  FCM 初始化失敗：{e}")
        return {"success": 0, "failure": len(tokens), "removed": 0, "errors": [str(e)]}

    for start in range(0, len(tokens), _MULTICAST_LIMIT):
        chunk = tokens[start:start + _MULTICAST_LIMIT]
        try:
            result = _send_chunk(chunk, title, body, data)
        except Exception as e:
            # 整批失敗（網路、憑證等）：這批的 token 無從判斷死活，不做清除
            print(f"⚠️  FCM 批次推播失敗（第 {start // _MULTICAST_LIMIT + 1} 批）：{e}")
            total_failure += len(chunk)
            errors.append(str(e))
            continue
        total_success += result["success"]
        total_failure += result["failure"]
        dead.extend(result["dead"])
        errors.extend(result["errors"])

    removed = 0
    if dead:
        try:
            from fcm.token_store import remove_tokens
            removed = remove_tokens(dead)
            print(f"🧹 已清除 {removed} 筆失效 token")
        except Exception as e:
            print(f"⚠️  清除失效 token 失敗：{e}")

    if errors:
        # 只印前幾筆，避免大量裝置失敗時洗版
        print(f"⚠️  FCM 推播有 {total_failure} 則失敗，前 5 則原因：")
        for line in errors[:5]:
            print(f"    - {line}")

    return {
        "success": total_success,
        "failure": total_failure,
        "removed": removed,
        "errors": errors[:20],
    }
