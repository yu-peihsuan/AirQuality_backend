# fcm/fcm_sender.py
# Firebase Cloud Messaging 推播發送模組

import os
import firebase_admin
from firebase_admin import credentials, messaging

# firebase_admin 的 multicast 上限。實測 7.5.0：send_each_for_multicast 會把
# MulticastMessage 展開成一則則 Message 再送，展開後若超過 500 則，函式庫會在
# 送出前自己丟 ValueError('messages must not contain more than 500 elements.')——
# 是客戶端擋下來的，不是伺服器回 400，別去查不存在的 HTTP 回應。
# 舊程式碼沒分批，這個 ValueError 會被外層 except 接住變成整批失敗。
_MULTICAST_LIMIT = 500

# 代表「這個 token 已經不存在／不屬於本專案」的例外類別。這是主要判準：
# 類別是傳訊專用的，語意明確。實測 firebase-admin 7.5.0 的 .code 分別是
# NOT_FOUND 與 PERMISSION_DENIED——後者是通用碼，專案層級的權限問題也會
# 回同一個，所以不能只靠字串比對，否則一次設定錯誤就清空整個 token 檔。
_DEAD_TOKEN_TYPES = (messaging.UnregisteredError, messaging.SenderIdMismatchError)

# 後備判準：萬一日後版本換了類別名稱，仍可由錯誤碼認出來。
# 只收語意夠窄的兩個，PERMISSION_DENIED 刻意不列入（理由同上）。
_DEAD_TOKEN_CODES = {"NOT_FOUND", "UNREGISTERED", "INVALID_ARGUMENT"}

# 防呆：整批每一則都被判定失效時，比起「所有裝置剛好同時解除安裝」，
# 遠更可能是推播內容或憑證有問題（payload 不合法會讓整批回
# INVALID_ARGUMENT）。這種情況一筆都不刪，只留下大聲的 log。
_MASS_DELETE_GUARD = 10


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


def _is_dead_token(exc, code: str) -> bool:
    """這則失敗是否代表 token 本身已經沒救（而非暫時性錯誤）。"""
    if isinstance(exc, _DEAD_TOKEN_TYPES):
        return True
    return code in _DEAD_TOKEN_CODES


def _error_code(exc) -> str:
    """從 firebase_admin 的例外取出可判讀的錯誤碼。"""
    for attr in ("code", "cause"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(exc).__name__ if exc else ""


def _send_chunk(tokens: list[str], title: str, body: str, data: dict) -> dict:
    """送出一批（<= 500 筆）並逐則檢查結果。"""
    # tokens= 會觸發 DeprecationWarning（函式庫在推 fids），但**不要**改成 fids：
    # fids 是 Firebase Installation ID，跟 App 的 onNewToken 給的註冊 token
    # 是兩種不同的識別碼，換過去會全部送不到人。
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
        if _is_dead_token(resp.exception, code):
            dead.append(token)

    if dead and len(dead) == len(tokens) and len(tokens) >= _MASS_DELETE_GUARD:
        print(f"⚠️  整批 {len(tokens)} 則全部被判定為失效 token——推播內容或憑證"
              f"有問題的可能性遠高於此，本批不執行清除")
        dead = []

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
