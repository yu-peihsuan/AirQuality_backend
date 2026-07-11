# fcm/fcm_sender.py
# Firebase Cloud Messaging 推播發送模組

import os
import json
import firebase_admin
from firebase_admin import credentials, messaging

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


def send_multicast(tokens: list[str], title: str, body: str, data: dict = None) -> dict:
    """
    批次推播給多個裝置。
    回傳 {"success": n, "failure": n}
    """
    if not tokens:
        return {"success": 0, "failure": 0}
    try:
        _init_firebase()
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=tokens,
        )
        response = messaging.send_each_for_multicast(message)
        return {
            "success": response.success_count,
            "failure": response.failure_count,
        }
    except Exception as e:
        print(f"⚠️  FCM 批次推播失敗：{e}")
        return {"success": 0, "failure": len(tokens)}
